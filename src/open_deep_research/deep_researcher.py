"""Main LangGraph implementation for the Deep Research agent."""

import asyncio
from typing import Literal

from langchain.chat_models import init_chat_model
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    filter_messages,
    get_buffer_string,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from open_deep_research.configuration import (
    Configuration,
)
from open_deep_research.knowledge.ids import stable_id
from open_deep_research.knowledge.models import KnowledgeScope
from open_deep_research.prompts import (
    agentic_rag_researcher_prompt,
    agentic_rag_supervisor_prompt,
    clarify_with_user_instructions,
    compress_research_simple_human_message,
    compress_research_system_prompt,
    final_report_generation_prompt,
    knowledge_augmented_legacy_prompt,
    lead_researcher_prompt,
    research_system_prompt,
    transform_messages_into_research_topic_prompt,
)
from open_deep_research.research import (
    CompletionDecision,
    RequirementMaterializer,
    RequirementSet,
)
from open_deep_research.reporting.pipeline import citation_validation_node
from open_deep_research.state import (
    AgentInputState,
    AgentState,
    ClarifyWithUser,
    ConductResearch,
    ResearchComplete,
    ResearcherOutputState,
    ResearcherState,
    ResearchQuestion,
    SupervisorState,
)
from open_deep_research.utils import (
    anthropic_websearch_called,
    get_all_tools,
    get_api_key_for_model,
    get_governed_results_from_tool_calls,
    get_model_token_limit,
    get_notes_from_tool_calls,
    get_today_str,
    is_token_limit_exceeded,
    next_process_id,
    openai_websearch_called,
    process_print,
    remove_up_to_last_ai_message,
    think_tool,
    with_process_context,
)

# Initialize a configurable model that we will use throughout the agent
configurable_model = init_chat_model(
    configurable_fields=("model", "max_tokens", "api_key"),
)


def _agentic_scope_and_run_id(
    config: RunnableConfig,
    configurable: Configuration,
    research_brief: str,
) -> tuple[str, str]:
    """Resolve trusted scope/run identities before materializing requirements."""
    raw_config = (config or {}).get("configurable", {})
    injected_runtime = raw_config.get("_governed_runtime")
    if injected_runtime is not None:
        scope = getattr(injected_runtime, "scope", None)
        run_id = getattr(injected_runtime, "run_id", None)
        if scope is not None and isinstance(run_id, str) and run_id.strip():
            return scope.scope_id, run_id.strip()

    if not configurable.knowledge_tenant_id or not configurable.knowledge_project_id:
        raise ValueError(
            "Agentic RAG requires knowledge_tenant_id and knowledge_project_id"
        )
    scope = KnowledgeScope(
        tenant_id=configurable.knowledge_tenant_id,
        project_id=configurable.knowledge_project_id,
    )
    explicit_run_id = raw_config.get("research_run_id")
    if isinstance(explicit_run_id, str) and explicit_run_id.strip():
        return scope.scope_id, explicit_run_id.strip()
    thread_id = raw_config.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError(
            "Agentic RAG requires a trusted research_run_id or thread_id"
        )
    return (
        scope.scope_id,
        stable_id("research_run", scope.scope_id, thread_id.strip(), research_brief),
    )


def _requirement_set_from_state(state: dict) -> RequirementSet:
    raw = state.get("requirement_set")
    if isinstance(raw, RequirementSet):
        return raw
    if isinstance(raw, dict):
        return RequirementSet.model_validate(raw)
    raise RuntimeError("Agentic RAG state is missing its trusted RequirementSet")


def _governed_artifact_updates(messages) -> dict[str, list[str]]:
    """Recover only schema-validated governed IDs from tool outputs."""
    updates: dict[str, list[str]] = {
        "source_ids": [],
        "evidence_ids": [],
        "run_evidence_ids": [],
        "coverage_assessment_ids": [],
        "retrieval_decision_ids": [],
    }
    for result in get_governed_results_from_tool_calls(list(messages)):
        updates["source_ids"].extend(item.source_id for item in result.evidence)
        updates["evidence_ids"].extend(item.evidence_id for item in result.evidence)
        updates["run_evidence_ids"].extend(result.run_evidence_ids)
        updates["coverage_assessment_ids"].extend(
            result.coverage_assessment_ids
        )
        updates["retrieval_decision_ids"].append(result.decision_id)
    return {key: value for key, value in updates.items() if value}


async def _programmatic_completion(
    state: dict,
    config: RunnableConfig,
    *,
    blocked: bool = False,
    blocked_reasons: tuple[str, ...] = (),
):
    """Evaluate the hard completion gate through the run-shared orchestrator."""
    from open_deep_research.knowledge.retrieval.runtime import get_governed_runtime

    requirement_set = _requirement_set_from_state(state)
    runtime = get_governed_runtime(config, run_id=requirement_set.run_id)
    return await runtime.orchestrator.completion_decision(
        requirement_set,
        blocked=blocked,
        blocked_reasons=blocked_reasons,
    )


def _completion_updates(decision) -> dict[str, object]:
    return {
        "completion_decision_ids": [decision.audit_id],
        "research_gaps": {
            "type": "override",
            "value": list(decision.explicit_gaps),
        },
    }


def _agentic_research_notes(messages) -> list[str]:
    """Exclude diagnostic ToolMessages from the Writer evidence view."""
    notes: list[str] = []
    for message in filter_messages(messages, include_types="tool"):
        if getattr(message, "name", None) != "ConductResearch":
            continue
        content = str(message.content)
        if content.startswith("Error "):
            continue
        notes.append(content)
    return notes

async def clarify_with_user(state: AgentState, config: RunnableConfig) -> Command[Literal["write_research_brief", "__end__"]]:
    """Analyze user messages and ask clarifying questions if the research scope is unclear.
    
    This function determines whether the user's request needs clarification before proceeding
    with research. If clarification is disabled or not needed, it proceeds directly to research.
    
    Args:
        state: Current agent state containing user messages
        config: Runtime configuration with model settings and preferences
        
    Returns:
        Command to either end with a clarifying question or proceed to research brief
    """
    # Step 1: Check if clarification is enabled in configuration
    configurable = Configuration.from_runnable_config(config)
    if not configurable.allow_clarification:
        # Skip clarification step and proceed directly to research
        return Command(goto="write_research_brief")
    
    # Step 2: Prepare the model for structured clarification analysis
    messages = state["messages"]
    model_config = {
        "model": configurable.research_model,
        "max_tokens": configurable.research_model_max_tokens,
        "api_key": get_api_key_for_model(configurable.research_model, config),
        "tags": ["langsmith:nostream"]
    }
    
    # Configure model with structured output and retry logic
    clarification_model = (
        configurable_model
        .with_structured_output(ClarifyWithUser)
        .with_retry(stop_after_attempt=configurable.max_structured_output_retries)
        .with_config(model_config)
    )
    
    # Step 3: Analyze whether clarification is needed
    prompt_content = clarify_with_user_instructions.format(
        messages=get_buffer_string(messages), 
        date=get_today_str()
    )
    response = await clarification_model.ainvoke([HumanMessage(content=prompt_content)])
    
    # Step 4: Route based on clarification analysis
    if response.need_clarification:
        # End with clarifying question for user
        return Command(
            goto=END, 
            update={"messages": [AIMessage(content=response.question)]}
        )
    else:
        # Proceed to research with verification message
        return Command(
            goto="write_research_brief", 
            update={"messages": [AIMessage(content=response.verification)]}
        )


async def write_research_brief(state: AgentState, config: RunnableConfig) -> Command[Literal["research_supervisor"]]:
    """Transform user messages into a structured research brief and initialize supervisor.
    
    This function analyzes the user's messages and generates a focused research brief
    that will guide the research supervisor. It also sets up the initial supervisor
    context with appropriate prompts and instructions.
    
    Args:
        state: Current agent state containing user messages
        config: Runtime configuration with model settings
        
    Returns:
        Command to proceed to research supervisor with initialized context
    """
    # Step 1: Set up the research model for structured output
    configurable = Configuration.from_runnable_config(config)
    research_model_config = {
        "model": configurable.research_model,
        "max_tokens": configurable.research_model_max_tokens,
        "api_key": get_api_key_for_model(configurable.research_model, config),
        "tags": ["langsmith:nostream"]
    }
    
    # Configure model for structured research question generation
    research_model = (
        configurable_model
        .with_structured_output(ResearchQuestion)
        .with_retry(stop_after_attempt=configurable.max_structured_output_retries)
        .with_config(research_model_config)
    )
    
    # Step 2: Generate structured research brief from user messages
    prompt_content = transform_messages_into_research_topic_prompt.format(
        messages=get_buffer_string(state.get("messages", [])),
        date=get_today_str()
    )
    response = await research_model.ainvoke([HumanMessage(content=prompt_content)])
    process_print(
        config,
        event="research_brief",
        name="research_brief",
        title=response.research_brief,
        item_id=next_process_id(config, "B"),
    )
    
    # Step 3: Initialize supervisor with research brief and instructions
    supervisor_system_prompt = lead_researcher_prompt.format(
        date=get_today_str(),
        max_concurrent_research_units=configurable.max_concurrent_research_units,
        max_researcher_iterations=configurable.max_researcher_iterations
    )
    update_payload = {
        "research_brief": response.research_brief,
    }
    if configurable.enable_agentic_rag:
        scope_id, run_id = _agentic_scope_and_run_id(
            config, configurable, response.research_brief
        )
        requirement_set = await RequirementMaterializer(
            extractor_version="deterministic-fallback-v1",
            policy_version=configurable.requirement_completion_policy_version,
        ).materialize(
            research_brief=response.research_brief,
            scope_id=scope_id,
            run_id=run_id,
        )
        supervisor_system_prompt += agentic_rag_supervisor_prompt
        update_payload.update(
            {
                "research_run_id": run_id,
                "requirement_set": requirement_set.model_dump(mode="json"),
                "requirement_ids": list(requirement_set.requirement_ids),
            }
        )

    update_payload["supervisor_messages"] = {
        "type": "override",
        "value": [
            SystemMessage(content=supervisor_system_prompt),
            HumanMessage(content=response.research_brief),
        ],
    }
    
    return Command(
        goto="research_supervisor",
        update=update_payload,
    )


async def supervisor(state: SupervisorState, config: RunnableConfig) -> Command[Literal["supervisor_tools"]]:
    """Lead research supervisor that plans research strategy and delegates to researchers.
    
    The supervisor analyzes the research brief and decides how to break down the research
    into manageable tasks. It can use think_tool for strategic planning, ConductResearch
    to delegate tasks to sub-researchers, or ResearchComplete when satisfied with findings.
    
    Args:
        state: Current supervisor state with messages and research context
        config: Runtime configuration with model settings
        
    Returns:
        Command to proceed to supervisor_tools for tool execution
    """
    # Step 1: Configure the supervisor model with available tools
    configurable = Configuration.from_runnable_config(config)
    research_model_config = {
        "model": configurable.research_model,
        "max_tokens": configurable.research_model_max_tokens,
        "api_key": get_api_key_for_model(configurable.research_model, config),
        "tags": ["langsmith:nostream"]
    }
    
    # Available tools: research delegation, completion signaling, and strategic thinking
    lead_researcher_tools = [ConductResearch, ResearchComplete, think_tool]
    
    # Configure model with tools, retry logic, and model settings
    research_model = (
        configurable_model
        .bind_tools(lead_researcher_tools)
        .with_retry(stop_after_attempt=configurable.max_structured_output_retries)
        .with_config(research_model_config)
    )
    
    # Step 2: Generate supervisor response based on current context
    supervisor_messages = state.get("supervisor_messages", [])
    response = await research_model.ainvoke(supervisor_messages)
    supervisor_round = state.get("research_iterations", 0) + 1
    tool_names = [tool_call["name"] for tool_call in getattr(response, "tool_calls", [])]
    process_print(
        config,
        event="supervisor",
        name="tool_calls",
        round_id=f"supervisor:{supervisor_round}",
        item_id=next_process_id(config, "SV"),
        tools=tool_names,
    )
    
    # Step 3: Update state and proceed to tool execution
    return Command(
        goto="supervisor_tools",
        update={
            "supervisor_messages": [response],
            "research_iterations": state.get("research_iterations", 0) + 1
        }
    )

async def supervisor_tools(state: SupervisorState, config: RunnableConfig) -> Command[Literal["supervisor", "__end__"]]:
    """Execute tools called by the supervisor, including research delegation and strategic thinking.
    
    This function handles three types of supervisor tool calls:
    1. think_tool - Strategic reflection that continues the conversation
    2. ConductResearch - Delegates research tasks to sub-researchers
    3. ResearchComplete - Signals completion of research phase
    
    Args:
        state: Current supervisor state with messages and iteration count
        config: Runtime configuration with research limits and model settings
        
    Returns:
        Command to either continue supervision loop or end research phase
    """
    configurable = Configuration.from_runnable_config(config)
    supervisor_messages = state.get("supervisor_messages", [])
    research_iterations = state.get("research_iterations", 0)
    most_recent_message = supervisor_messages[-1]
    tool_calls = list(getattr(most_recent_message, "tool_calls", []) or [])
    no_tool_calls = not tool_calls
    reached_iteration_limit = (
        research_iterations >= configurable.max_researcher_iterations
    )
    completion_calls = [
        call for call in tool_calls if call["name"] == "ResearchComplete"
    ]
    all_tool_messages: list[ToolMessage | HumanMessage] = []
    update_payload: dict[str, object] = {}

    # Always execute work requested in the same model turn before considering
    # ResearchComplete. This preserves LangChain's one-result-per-tool-call contract.
    for tool_call in tool_calls:
        if tool_call["name"] != "think_tool":
            continue
        all_tool_messages.append(
            ToolMessage(
                content=(
                    "Reflection recorded: "
                    f"{tool_call.get('args', {}).get('reflection', '')}"
                ),
                name="think_tool",
                tool_call_id=tool_call["id"],
            )
        )

    conduct_calls = [
        call for call in tool_calls if call["name"] == "ConductResearch"
    ]
    allowed_calls = conduct_calls[: configurable.max_concurrent_research_units]
    overflow_calls = conduct_calls[configurable.max_concurrent_research_units :]
    successful_results: list[dict] = []
    if allowed_calls:
        parent_round_id = f"supervisor:{research_iterations}"
        research_tasks = []
        for research_index, tool_call in enumerate(allowed_calls):
            research_topic = tool_call["args"]["research_topic"]
            researcher_id = f"{parent_round_id}/researcher:{research_index}"
            child_context: dict[str, object] = {
                "parent": parent_round_id,
                "concurrency_id": researcher_id,
                "researcher_id": researcher_id,
                "researcher_topic": research_topic,
            }
            researcher_input: dict[str, object] = {
                "researcher_messages": [HumanMessage(content=research_topic)],
                "research_topic": research_topic,
            }
            if configurable.enable_agentic_rag:
                requirement_set = _requirement_set_from_state(state)
                requirement_payload = requirement_set.model_dump(mode="json")
                child_context.update(
                    {
                        "run_id": requirement_set.run_id,
                        "requirement_set": requirement_payload,
                    }
                )
                researcher_input.update(
                    {
                        "research_run_id": requirement_set.run_id,
                        "requirement_set": requirement_payload,
                        "requirement_ids": list(requirement_set.requirement_ids),
                    }
                )
            researcher_config = with_process_context(config, **child_context)
            research_tasks.append(
                researcher_subgraph.ainvoke(researcher_input, researcher_config)
            )

        observations = await asyncio.gather(
            *research_tasks, return_exceptions=True
        )
        for observation, tool_call in zip(observations, allowed_calls):
            if isinstance(observation, BaseException):
                content = (
                    "Error executing ConductResearch "
                    f"[{type(observation).__name__}]: {observation}"
                )
            else:
                successful_results.append(observation)
                content = observation.get(
                    "compressed_research",
                    "Error synthesizing research report: result missing",
                )
            all_tool_messages.append(
                ToolMessage(
                    content=content,
                    name="ConductResearch",
                    tool_call_id=tool_call["id"],
                )
            )

    for overflow_call in overflow_calls:
        all_tool_messages.append(
            ToolMessage(
                content=(
                    "Error executing ConductResearch [ConcurrencyLimit]: "
                    "the supervisor exceeded max_concurrent_research_units="
                    f"{configurable.max_concurrent_research_units}"
                ),
                name="ConductResearch",
                tool_call_id=overflow_call["id"],
            )
        )

    raw_notes = [
        note
        for observation in successful_results
        for note in observation.get("raw_notes", [])
        if note
    ]
    if raw_notes:
        update_payload["raw_notes"] = raw_notes
    for key in (
        "source_ids",
        "evidence_ids",
        "requirement_ids",
        "run_evidence_ids",
        "coverage_assessment_ids",
        "retrieval_decision_ids",
        "completion_decision_ids",
    ):
        values = [
            stable_identifier
            for observation in successful_results
            for stable_identifier in observation.get(key, [])
        ]
        if values:
            update_payload[key] = values

    should_attempt_completion = bool(completion_calls) or no_tool_calls
    if configurable.enable_agentic_rag and (
        should_attempt_completion or reached_iteration_limit
    ):
        decision = await _programmatic_completion(
            state,
            config,
            blocked=reached_iteration_limit,
            blocked_reasons=("supervisor_iteration_limit_reached",)
            if reached_iteration_limit
            else (),
        )
        update_payload.update(_completion_updates(decision))
        decision_text = (
            "Programmatic completion gate: "
            f"{decision.decision.value}; gaps={list(decision.explicit_gaps)}"
        )
        for call in completion_calls:
            all_tool_messages.append(
                ToolMessage(
                    content=decision_text,
                    name="ResearchComplete",
                    tool_call_id=call["id"],
                )
            )
        if decision.decision is CompletionDecision.CONTINUE:
            if not completion_calls:
                all_tool_messages.append(HumanMessage(content=decision_text))
            update_payload["supervisor_messages"] = all_tool_messages
            return Command(goto="supervisor", update=update_payload)

        combined_messages = [*supervisor_messages, *all_tool_messages]
        update_payload.update(
            {
                "supervisor_messages": all_tool_messages,
                "notes": _agentic_research_notes(combined_messages),
                "research_brief": state.get("research_brief", ""),
            }
        )
        return Command(goto=END, update=update_payload)

    if not configurable.enable_agentic_rag and (
        reached_iteration_limit or no_tool_calls or completion_calls
    ):
        for call in completion_calls:
            all_tool_messages.append(
                ToolMessage(
                    content="Research completion recorded.",
                    name="ResearchComplete",
                    tool_call_id=call["id"],
                )
            )
        combined_messages = [*supervisor_messages, *all_tool_messages]
        update_payload.update(
            {
                "supervisor_messages": all_tool_messages,
                "notes": get_notes_from_tool_calls(combined_messages),
                "research_brief": state.get("research_brief", ""),
            }
        )
        return Command(goto=END, update=update_payload)

    update_payload["supervisor_messages"] = all_tool_messages
    return Command(goto="supervisor", update=update_payload)

# Supervisor Subgraph Construction
# Creates the supervisor workflow that manages research delegation and coordination
supervisor_builder = StateGraph(SupervisorState, config_schema=Configuration)

# Add supervisor nodes for research management
supervisor_builder.add_node("supervisor", supervisor)           # Main supervisor logic
supervisor_builder.add_node("supervisor_tools", supervisor_tools)  # Tool execution handler

# Define supervisor workflow edges
supervisor_builder.add_edge(START, "supervisor")  # Entry point to supervisor

# Compile supervisor subgraph for use in main workflow
supervisor_subgraph = supervisor_builder.compile()






async def researcher(state: ResearcherState, config: RunnableConfig) -> Command[Literal["researcher_tools"]]:
    """Individual researcher that conducts focused research on specific topics.
    
    This researcher is given a specific research topic by the supervisor and uses
    available tools (search, think_tool, MCP tools) to gather comprehensive information.
    It can use think_tool for strategic planning between searches.
    
    Args:
        state: Current researcher state with messages and topic context
        config: Runtime configuration with model settings and tool availability
        
    Returns:
        Command to proceed to researcher_tools for tool execution
    """
    # Step 1: Load configuration and validate tool availability
    configurable = Configuration.from_runnable_config(config)
    researcher_messages = state.get("researcher_messages", [])
    
    # Get all available research tools (search, MCP, think_tool)
    tools = await get_all_tools(config)
    if len(tools) == 0:
        raise ValueError(
            "No tools found to conduct research: Please configure either your "
            "search API or add MCP tools to your configuration."
        )
    
    # Step 2: Configure the researcher model with tools
    research_model_config = {
        "model": configurable.research_model,
        "max_tokens": configurable.research_model_max_tokens,
        "api_key": get_api_key_for_model(configurable.research_model, config),
        "tags": ["langsmith:nostream"]
    }
    
    # Prepare system prompt with MCP context if available
    researcher_prompt = research_system_prompt.format(
        mcp_prompt=configurable.mcp_prompt or "", 
        date=get_today_str(),
        max_concurrent_researcher_tool_calls=configurable.max_concurrent_researcher_tool_calls,
        max_queries_per_search_call=configurable.max_queries_per_search_call
    )
    if configurable.enable_agentic_rag:
        researcher_prompt += agentic_rag_researcher_prompt
    elif configurable.enable_knowledge_tools:
        researcher_prompt += knowledge_augmented_legacy_prompt
    
    # Configure model with tools, retry logic, and settings
    research_model = (
        configurable_model
        .bind_tools(tools)
        .with_retry(stop_after_attempt=configurable.max_structured_output_retries)
        .with_config(research_model_config)
    )
    
    # Step 3: Generate researcher response with system context
    messages = [SystemMessage(content=researcher_prompt)] + researcher_messages
    response = await research_model.ainvoke(messages)
    researcher_round = state.get("tool_call_iterations", 0) + 1
    tool_names = [tool_call["name"] for tool_call in getattr(response, "tool_calls", [])]
    process_print(
        config,
        event="researcher",
        name="tool_calls",
        title=state.get("research_topic", ""),
        round_id=f"researcher:{researcher_round}",
        item_id=next_process_id(config, "R"),
        tools=tool_names,
    )
    
    # Step 4: Update state and proceed to tool execution
    return Command(
        goto="researcher_tools",
        update={
            "researcher_messages": [response],
            "tool_call_iterations": state.get("tool_call_iterations", 0) + 1
        }
    )

# Tool Execution Helper Function
async def execute_tool_safely(tool, args, config):
    """Safely execute a tool with error handling."""
    if tool is None:
        return "Error executing tool [UnknownTool]: tool is not bound"
    try:
        return await tool.ainvoke(args, config)
    except Exception as e:
        return f"Error executing tool [{type(e).__name__}]: {e}"


async def researcher_tools(state: ResearcherState, config: RunnableConfig) -> Command[Literal["researcher", "compress_research"]]:
    """Execute tools called by the researcher, including search tools and strategic thinking.
    
    This function handles various types of researcher tool calls:
    1. think_tool - Strategic reflection that continues the research conversation
    2. Search tools (tavily_search, web_search) - Information gathering
    3. MCP tools - External tool integrations
    4. ResearchComplete - Signals completion of individual research task
    
    Args:
        state: Current researcher state with messages and iteration count
        config: Runtime configuration with research limits and tool settings
        
    Returns:
        Command to either continue research loop or proceed to compression
    """
    configurable = Configuration.from_runnable_config(config)
    researcher_messages = state.get("researcher_messages", [])
    most_recent_message = researcher_messages[-1]
    
    # Early exit if no tool calls were made (including native web search)
    has_tool_calls = bool(most_recent_message.tool_calls)
    has_native_search = (
        openai_websearch_called(most_recent_message) or 
        anthropic_websearch_called(most_recent_message)
    )
    
    if not has_tool_calls and not has_native_search:
        return Command(goto="compress_research")
    
    tools = await get_all_tools(config)
    tools_by_name = {
        tool.name if hasattr(tool, "name") else tool.get("name", "web_search"): tool 
        for tool in tools
    }
    
    # ResearchComplete is a signal, not an executable research call. Other calls
    # in the same turn must finish before the completion gate is evaluated.
    tool_calls = list(most_recent_message.tool_calls)
    completion_calls = [
        call for call in tool_calls if call["name"] == "ResearchComplete"
    ]
    executable_calls = [
        call for call in tool_calls if call["name"] != "ResearchComplete"
    ]
    allowed_tool_calls = executable_calls[
        : configurable.max_concurrent_researcher_tool_calls
    ]
    overflow_tool_calls = executable_calls[
        configurable.max_concurrent_researcher_tool_calls :
    ]

    process_context = (config or {}).get("configurable", {}).get("_process_context", {})
    researcher_concurrency_id = process_context.get("concurrency_id")
    tool_round = state.get("tool_call_iterations", 0)
    tool_execution_tasks = []
    for tool_index, tool_call in enumerate(allowed_tool_calls):
        tool_concurrency_id = f"researcher:{tool_round}/tool:{tool_index}"
        tool_config = with_process_context(
            config,
            parent=researcher_concurrency_id,
            concurrency_id=tool_concurrency_id,
            tool_name=tool_call["name"],
        )
        tool_execution_tasks.append(
            execute_tool_safely(
                tools_by_name.get(tool_call["name"]),
                tool_call["args"],
                tool_config,
            )
        )
    
    observations = await asyncio.gather(*tool_execution_tasks)
    
    # Create tool messages from execution results
    tool_outputs = [
        ToolMessage(
            content=observation if isinstance(observation, str) else str(observation),
            name=tool_call["name"],
            tool_call_id=tool_call["id"]
        ) 
        for observation, tool_call in zip(observations, allowed_tool_calls)
    ]

    # Return an explicit result for every overflow call to preserve tool-call protocol.
    for overflow_call in overflow_tool_calls:
        tool_outputs.append(ToolMessage(
            content=(
                "Error executing tool [ConcurrencyLimit]: "
                "the researcher exceeded the maximum number of concurrent "
                "tool calls; "
                f"max_concurrent_researcher_tool_calls="
                f"{configurable.max_concurrent_researcher_tool_calls}"
            ),
            name=overflow_call["name"],
            tool_call_id=overflow_call["id"]
        ))

    update_payload: dict[str, object] = {"researcher_messages": tool_outputs}
    if configurable.enable_agentic_rag:
        update_payload.update(_governed_artifact_updates(tool_outputs))

    exceeded_iterations = state.get("tool_call_iterations", 0) >= configurable.max_react_tool_calls
    if configurable.enable_agentic_rag and (
        completion_calls or exceeded_iterations
    ):
        decision = await _programmatic_completion(
            state,
            config,
            blocked=exceeded_iterations,
            blocked_reasons=("researcher_iteration_limit_reached",)
            if exceeded_iterations
            else (),
        )
        update_payload.update(_completion_updates(decision))
        decision_text = (
            "Programmatic completion gate: "
            f"{decision.decision.value}; gaps={list(decision.explicit_gaps)}"
        )
        completion_outputs = [
            ToolMessage(
                content=decision_text,
                name="ResearchComplete",
                tool_call_id=call["id"],
            )
            for call in completion_calls
        ]
        if completion_outputs:
            tool_outputs.extend(completion_outputs)
            update_payload["researcher_messages"] = tool_outputs
        if decision.decision is CompletionDecision.CONTINUE:
            return Command(goto="researcher", update=update_payload)
        return Command(goto="compress_research", update=update_payload)

    if not configurable.enable_agentic_rag and completion_calls:
        tool_outputs.extend(
            ToolMessage(
                content="Research completion recorded.",
                name="ResearchComplete",
                tool_call_id=call["id"],
            )
            for call in completion_calls
        )
        update_payload["researcher_messages"] = tool_outputs

    if exceeded_iterations or completion_calls:
        return Command(goto="compress_research", update=update_payload)
    return Command(goto="researcher", update=update_payload)

def _compression_trace(messages, *, agentic: bool):
    """Build an evidence-only compression trace without tool protocol debris."""
    if not agentic:
        return list(messages)
    cleaned = []
    for message in messages:
        if isinstance(message, ToolMessage):
            if getattr(message, "name", None) != "governed_retrieval":
                continue
            if not get_governed_results_from_tool_calls([message]):
                continue
            cleaned.append(
                HumanMessage(content=f"Governed evidence result:\n{message.content}")
            )
        elif isinstance(message, AIMessage):
            # Compression does not execute tools, so remove tool-call metadata while
            # preserving any textual analysis that preceded a valid evidence result.
            cleaned.append(AIMessage(content=str(message.content)))
        else:
            cleaned.append(message)
    return cleaned


async def compress_research(state: ResearcherState, config: RunnableConfig):
    """Compress and synthesize research findings into a concise, structured summary.
    
    This function takes all the research findings, tool outputs, and AI messages from
    a researcher's work and distills them into a clean, comprehensive summary while
    preserving all important information and findings.
    
    Args:
        state: Current researcher state with accumulated research messages
        config: Runtime configuration with compression model settings
        
    Returns:
        Dictionary containing compressed research summary and raw notes
    """
    # Step 1: Configure the compression model
    configurable = Configuration.from_runnable_config(config)
    process_print(
        config,
        event="compression",
        name="compress_research",
        title=state.get("research_topic", ""),
        item_id=next_process_id(config, "C"),
    )
    synthesizer_model = configurable_model.with_config({
        "model": configurable.compression_model,
        "max_tokens": configurable.compression_model_max_tokens,
        "api_key": get_api_key_for_model(configurable.compression_model, config),
        "tags": ["langsmith:nostream"]
    })
    
    original_messages = list(state.get("researcher_messages", []))
    researcher_messages = _compression_trace(
        original_messages,
        agentic=configurable.enable_agentic_rag,
    )
    raw_notes_content = "\n".join(
        str(message.content)
        for message in filter_messages(
            original_messages, include_types=["tool", "ai"]
        )
    )

    # Each configured attempt performs a real model call. On a token error the
    # next attempt uses the compression model's limit classification and a newly
    # rebuilt, trimmed message list.
    max_attempts = configurable.compression_max_retries
    for synthesis_attempt in range(max_attempts):
        try:
            compression_prompt = compress_research_system_prompt.format(date=get_today_str())
            messages = [
                SystemMessage(content=compression_prompt),
                *researcher_messages,
                HumanMessage(content=compress_research_simple_human_message),
            ]
            response = await synthesizer_model.ainvoke(messages)
            return {
                "compressed_research": str(response.content),
                "raw_notes": [raw_notes_content]
            }
        except Exception as e:
            if (
                is_token_limit_exceeded(e, configurable.compression_model)
                and synthesis_attempt + 1 < max_attempts
            ):
                researcher_messages = remove_up_to_last_ai_message(researcher_messages)
            continue

    return {
        "compressed_research": "Error synthesizing research report: Maximum retries exceeded",
        "raw_notes": [raw_notes_content]
    }

# Researcher Subgraph Construction
# Creates individual researcher workflow for conducting focused research on specific topics
researcher_builder = StateGraph(
    ResearcherState, 
    output=ResearcherOutputState, 
    config_schema=Configuration
)

# Add researcher nodes for research execution and compression
researcher_builder.add_node("researcher", researcher)                 # Main researcher logic
researcher_builder.add_node("researcher_tools", researcher_tools)     # Tool execution handler
researcher_builder.add_node("compress_research", compress_research)   # Research compression

# Define researcher workflow edges
researcher_builder.add_edge(START, "researcher")           # Entry point to researcher
researcher_builder.add_edge("compress_research", END)      # Exit point after compression

# Compile researcher subgraph for parallel execution by supervisor
researcher_subgraph = researcher_builder.compile()






async def final_report_generation(state: AgentState, config: RunnableConfig):
    """Generate the final comprehensive research report with retry logic for token limits.
    
    This function takes all collected research findings and synthesizes them into a 
    well-structured, comprehensive final report using the configured report generation model.
    
    Args:
        state: Agent state containing research findings and context
        config: Runtime configuration with model settings and API keys
        
    Returns:
        Dictionary containing the final report and cleared state
    """
    # Step 1: Extract research findings and prepare state cleanup
    notes = state.get("notes", [])
    cleared_state = {"notes": {"type": "override", "value": []}}
    findings = "\n".join(notes)
    
    # Step 2: Configure the final report generation model
    configurable = Configuration.from_runnable_config(config)
    writer_model_config = {
        "model": configurable.final_report_model,
        "max_tokens": configurable.final_report_model_max_tokens,
        "api_key": get_api_key_for_model(configurable.final_report_model, config),
        "tags": ["langsmith:nostream"]
    }
    process_print(
        config,
        event="final_report",
        name="final_report_generation",
        title=state.get("research_brief", ""),
        item_id=next_process_id(config, "F"),
    )
    
    # Step 3: Attempt report generation with token limit retry logic
    max_retries = 3
    current_retry = 0
    findings_token_limit = None
    
    while current_retry <= max_retries:
        try:
            # Create comprehensive prompt with all research context
            final_report_prompt = final_report_generation_prompt.format(
                research_brief=state.get("research_brief", ""),
                messages=get_buffer_string(state.get("messages", [])),
                findings=findings,
                date=get_today_str()
            )
            
            # Generate the final report
            final_report = await configurable_model.with_config(writer_model_config).ainvoke([
                HumanMessage(content=final_report_prompt)
            ])
            
            # Return successful report generation
            return {
                "final_report": final_report.content, 
                "messages": [final_report],
                **cleared_state
            }
            
        except Exception as e:
            # Handle token limit exceeded errors with progressive truncation
            if is_token_limit_exceeded(e, configurable.final_report_model):
                current_retry += 1
                
                if current_retry == 1:
                    # First retry: determine initial truncation limit
                    model_token_limit = get_model_token_limit(configurable.final_report_model)
                    if not model_token_limit:
                        return {
                            "final_report": f"Error generating final report: Token limit exceeded, however, we could not determine the model's maximum context length. Please update the model map in deep_researcher/utils.py with this information. {e}",
                            "messages": [AIMessage(content="Report generation failed due to token limits")],
                            **cleared_state
                        }
                    # Use 4x token limit as character approximation for truncation
                    findings_token_limit = model_token_limit * 4
                else:
                    # Subsequent retries: reduce by 10% each time
                    findings_token_limit = int(findings_token_limit * 0.9)
                
                # Truncate findings and retry
                findings = findings[:findings_token_limit]
                continue
            else:
                # Non-token-limit error: return error immediately
                return {
                    "final_report": f"Error generating final report: {e}",
                    "messages": [AIMessage(content="Report generation failed due to an error")],
                    **cleared_state
                }
    
    # Step 4: Return failure result if all retries exhausted
    return {
        "final_report": "Error generating final report: Maximum retries exceeded",
        "messages": [AIMessage(content="Report generation failed after maximum retries")],
        **cleared_state
    }

# Main Deep Researcher Graph Construction
# Creates the complete deep research workflow from user input to final report
deep_researcher_builder = StateGraph(
    AgentState, 
    input=AgentInputState, 
    config_schema=Configuration
)

# Add main workflow nodes for the complete research process
deep_researcher_builder.add_node("clarify_with_user", clarify_with_user)           # User clarification phase
deep_researcher_builder.add_node("write_research_brief", write_research_brief)     # Research planning phase
deep_researcher_builder.add_node("research_supervisor", supervisor_subgraph)       # Research execution phase
deep_researcher_builder.add_node("final_report_generation", final_report_generation)  # Report generation phase
deep_researcher_builder.add_node("citation_validation", citation_validation_node)  # Optional report governance

# Define main workflow edges for sequential execution
deep_researcher_builder.add_edge(START, "clarify_with_user")                       # Entry point
deep_researcher_builder.add_edge("research_supervisor", "final_report_generation") # Research to report
deep_researcher_builder.add_edge("final_report_generation", "citation_validation") # Draft to optional validation
deep_researcher_builder.add_edge("citation_validation", END)                       # Final exit point

# Compile the complete deep researcher workflow
deep_researcher = deep_researcher_builder.compile()
