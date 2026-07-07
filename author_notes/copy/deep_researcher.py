"""Main LangGraph implementation for the Deep Research agent."""

import os

# Python 标准库：异步编程支持。
# Deep Research agent 通常会并行/异步调用模型、搜索工具、研究子任务等，
# 因此主图或节点中可能会使用 async / await / asyncio.gather 等机制。
import asyncio

# Python 类型注解：Literal 用于限制某个变量只能取指定字符串字面量。
# 在 LangGraph 中常用于声明路由返回值、节点状态标志、Command goto 目标等。
from typing import Literal

# LangChain 模型初始化入口。
# init_chat_model 可以根据配置动态初始化不同 provider/model，
# 例如 OpenAI、Anthropic、Google、OpenRouter 等兼容模型。
from langchain.chat_models import init_chat_model

# LangChain Core 的消息类型与消息处理工具。
from langchain_core.messages import (
    AIMessage,          # AI/LLM 返回的消息类型，通常包含模型回复或 tool_calls。
    HumanMessage,       # 用户消息类型，用于表示用户输入。
    SystemMessage,      # 系统消息类型，用于注入系统提示词 / 角色约束 / 行为规范。
    ToolMessage,        # 工具调用结果消息，用于把工具执行结果返回给模型。
    filter_messages,    # 消息过滤工具，可按类型、名称、ID 等筛选消息。
    get_buffer_string,  # 将消息列表转为字符串形式，常用于压缩、摘要或拼接上下文。
)

# LangChain Runnable 的运行配置类型。
# RunnableConfig 常用于传递 configurable、callbacks、metadata、recursion_limit 等运行参数。
from langchain_core.runnables import RunnableConfig

# LangGraph 图构建核心对象。
from langgraph.graph import END, START, StateGraph
# START：图的起始伪节点。
# END：图的结束伪节点。
# StateGraph：基于状态对象构建有向状态图，是 LangGraph agent workflow 的核心。

# LangGraph 的 Command 类型。
# Command 通常用于节点返回“状态更新 + 下一跳路由”的组合结果，
# 例如 Command(update={...}, goto="some_node")。
from langgraph.types import Command

# 项目内配置对象。
# Configuration 通常负责从 RunnableConfig / 环境变量 / 默认值中读取模型、工具、搜索等配置。
from open_deep_research.configuration import (
    Configuration,
)

# 项目内 prompt 模板。
# 这些 prompt 分别服务于：用户澄清、研究压缩、最终报告生成、supervisor 调度、研究员执行等阶段。
from open_deep_research.prompts import (
    clarify_with_user_instructions,              # 判断是否需要向用户澄清研究问题的提示词。
    compress_research_simple_human_message,      # 简化版研究内容压缩的人类消息模板。
    compress_research_system_prompt,             # 研究内容压缩阶段的系统提示词。
    final_report_generation_prompt,              # 最终报告生成提示词。
    lead_researcher_prompt,                      # 主研究员 / supervisor 使用的提示词。
    research_system_prompt,                      # 子研究员执行研究时使用的系统提示词。
    transform_messages_into_research_topic_prompt, # 将用户多轮消息转为明确研究主题的提示词。
)

# 项目内状态结构与结构化输出类型。
# 这些类型定义了 LangGraph 在不同阶段传递、更新和约束的数据。
from open_deep_research.state import (
    AgentInputState,      # Agent 的输入状态，通常只包含用户初始消息或外部输入。
    AgentState,           # 主 Agent 图的完整状态。
    ClarifyWithUser,      # 结构化输出：表示是否需要向用户继续澄清。
    ConductResearch,      # 结构化输出 / 工具 schema：表示需要发起研究任务。
    ResearchComplete,     # 结构化输出 / 工具 schema：表示研究任务完成。
    ResearcherOutputState,# 子研究员输出状态。
    ResearcherState,      # 单个 researcher 子图/节点的内部状态。
    ResearchQuestion,     # 结构化输出：规范化后的研究问题。
    SupervisorState,      # supervisor 多研究员协调状态。
)

# 项目内工具函数。
# 这些函数负责模型 API key、工具加载、token 限制检查、搜索调用识别、消息裁剪等工程逻辑。
from open_deep_research.utils import (
    anthropic_websearch_called,   # 判断 Anthropic web search 是否被调用过。
    get_all_tools,                # 根据配置加载所有可用工具，例如搜索工具、think_tool 等。
    get_api_key_for_model,        # 根据模型名称获取对应 provider 的 API key。
    get_model_token_limit,        # 获取指定模型的上下文 token 上限。
    get_notes_from_tool_calls,    # 从工具调用结果中提取研究笔记。
    get_today_str,                # 获取当前日期字符串，用于 prompt 注入时间上下文。
    is_token_limit_exceeded,      # 判断当前消息是否超过模型 token 限制。
    openai_websearch_called,      # 判断 OpenAI web search 是否被调用过。
    remove_up_to_last_ai_message, # 当上下文过长时，裁剪到最近一次 AIMessage 之后/附近。
    think_tool,                   # “思考工具”，通常用于让模型显式记录推理/计划/研究思路。
)



# =========================
# 整体注释解析：初始化可配置模型
# =========================
# 这个代码块创建了一个“运行时可配置”的聊天模型对象 configurable_model。
#
# 它的核心作用不是立刻绑定某一个固定模型，而是先构造一个通用模型入口，
# 后续在不同节点、不同任务、不同运行配置中，再通过 with_config(...) 动态指定：
# - 使用哪个模型；
# - 最大输出 token 数；
# - 使用哪个 API key。
#
# 这种设计非常适合 Deep Research / Agent 系统：
# 1. 不同阶段可以使用不同模型，例如澄清阶段用便宜模型，最终报告阶段用强模型；
# 2. 不同用户或不同运行环境可以注入不同 API key；
# 3. 不需要在每个函数里硬编码模型名称，增强可维护性和可迁移性。
#
# configurable_fields 表示允许在运行时被覆盖的模型字段。
# 也就是说，后面调用 .with_config(...) 时，可以动态改变这些字段。
configurable_model = init_chat_model(
    configurable_fields=("model", "max_tokens", "api_key"),
)




async def clarify_with_user(
    state: AgentState,
    config: RunnableConfig
) -> Command[Literal["write_research_brief", "__end__"]]:
    """
    整体注释解析：用户澄清节点

    这个函数是 Deep Research Agent 工作流中的“澄清判断节点”。

    它的核心任务是：
    1. 读取当前用户消息和运行配置；
    2. 判断当前研究请求是否足够清楚；
    3. 如果用户问题不清楚，则向用户提出澄清问题，并结束本轮 graph 执行；
    4. 如果用户问题已经足够清楚，则生成一个确认性回复，并进入 write_research_brief 节点。

    输入：
    - state: 当前 AgentState，主要包含 messages 等对话状态；
    - config: LangGraph / LangChain 的运行配置，里面可能包含模型、API key、是否允许澄清等信息。

    输出：
    - Command(...): LangGraph 的路由指令，用来决定下一步跳转到哪个节点；
        - goto="write_research_brief"：继续生成研究简报；
        - goto=END / "__end__"：结束当前 graph，并把澄清问题返回给用户。

    关键设计点：
    - 使用 allow_clarification 控制是否允许追问用户；
    - 使用结构化输出 ClarifyWithUser，避免模型自由发挥；
    - 使用 with_retry 提高结构化输出稳定性；
    - 使用 Command 同时完成“状态更新”和“节点跳转”。
    """

    # =========================
    # Step 1：解析运行配置，并判断是否允许澄清
    # =========================

    # 从 LangGraph / LangChain 的通用运行配置中，
    # 解析出 open_deep_research 项目自定义的 Configuration 对象。
    #
    # RunnableConfig 是框架层的通用配置；
    # Configuration 是项目层封装后的配置，里面包含：
    # - 是否允许澄清；
    # - 使用哪个 research_model；
    # - 最大 token 数；
    # - 最大结构化输出重试次数等。
    configurable = Configuration.from_runnable_config(config)

    # 如果配置中不允许澄清，则直接跳过 clarify 阶段。
    #
    # 典型场景：
    # - 命令行指定 --no-allow-clarification；
    # - 用户希望 agent 不要追问，直接执行；
    # - 自动评测时不希望出现交互式澄清；
    # - 批处理任务中需要固定流程，不能中途停下来问用户。
    if not configurable.allow_clarification:
        return Command(goto="write_research_brief")

    # =========================
    # Step 2：准备结构化澄清判断模型
    # =========================

    # 从当前 AgentState 中取出历史消息。
    #
    # messages 一般包含：
    # - HumanMessage：用户输入；
    # - AIMessage：模型回复；
    # - ToolMessage：工具调用结果；
    # - SystemMessage：系统提示词，视具体实现而定。
    messages = state["messages"]

    # 构造本次模型调用需要的运行配置。
    #
    # 注意：
    # 这里不是写死模型，而是从 configurable.research_model 中读取。
    # 这样可以让不同运行环境、不同命令行参数、不同任务配置使用不同模型。
    model_config = {
        # 用于研究流程相关判断的模型。
        # 这里虽然是 clarify 阶段，但仍然使用 research_model，
        # 因为它需要理解用户研究意图，而不只是普通闲聊。
        "model": configurable.research_model,

        # 当前研究模型的最大输出 token 数。
        # 对澄清判断来说，一般不需要很大；
        # 但统一使用配置项可以减少硬编码。
        "max_tokens": configurable.research_model_max_tokens,

        # 根据模型名称和当前运行配置获取对应 API key。
        #
        # 这样可以支持多模型提供商，例如：
        # - OpenAI；
        # - Anthropic；
        # - Google；
        # - OpenRouter；
        # - 本地模型服务等。
        "api_key": get_api_key_for_model(configurable.research_model, config),

        # LangSmith 追踪标签。
        #
        # langsmith:nostream 通常用于标记该调用不做流式输出追踪展示。
        # 对结构化输出调用来说，这样更容易得到稳定、完整的记录。
        "tags": ["langsmith:nostream"],
    }

    # 创建专门用于“澄清判断”的模型链。
    clarification_model = (
        configurable_model

        # 要求模型输出必须符合 ClarifyWithUser 这个结构化 schema。
        #
        # 这一步的意义是：
        # 模型不能随意输出自然语言，而是必须输出固定字段，例如：
        # - need_clarification: bool
        # - question: str
        # - verification: str
        #
        # 这样后续代码可以安全访问 response.need_clarification，
        # 不需要手动解析一段不可控的自然语言。
        .with_structured_output(ClarifyWithUser)

        # 如果模型第一次没有返回合法结构化输出，就自动重试。
        #
        # 常见失败情况：
        # - JSON 格式不合法；
        # - 字段缺失；
        # - 字段类型错误；
        # - 模型输出了额外解释文本；
        # - schema 校验失败。
        .with_retry(stop_after_attempt=configurable.max_structured_output_retries)

        # 注入本次模型调用的运行时配置。
        #
        # 这里会把 model、max_tokens、api_key、tags 等传给 configurable_model。
        .with_config(model_config)
    )

    # =========================
    # Step 3：构造澄清判断 Prompt，并异步调用模型
    # =========================

    # 将多轮消息转换成适合放入 prompt 的纯文本上下文。
    #
    # get_buffer_string(messages) 会把 HumanMessage / AIMessage 等对象
    # 转成类似下面的字符串：
    # Human: ...
    # AI: ...
    #
    # 这样模型可以看到完整对话背景，
    # 而不是只看到当前最后一条用户消息。
    #
    # date=get_today_str() 的作用是提供当前日期。
    # 这对处理“今天、最近、当前、最新、今年”等时间敏感表达很重要。
    prompt_content = clarify_with_user_instructions.format(
        messages=get_buffer_string(messages),
        date=get_today_str()
    )

    # 异步调用澄清判断模型。
    #
    # 这里传入 HumanMessage，是因为整个 clarify prompt 本质上是一条
    # 发送给模型的任务指令：
    # “请根据这些历史消息判断用户是否需要进一步澄清。”
    #
    # ainvoke(...) 表示异步调用。
    # 在 Agent / Deep Research 系统中，异步设计通常有利于：
    # - 并发工具调用；
    # - 并发研究子任务；
    # - 避免阻塞事件循环；
    # - 提升整体吞吐。
    response = await clarification_model.ainvoke(
        [HumanMessage(content=prompt_content)]
    )

    # =========================
    # Step 4：根据结构化输出结果决定下一步路由
    # =========================

    # 如果模型判断当前用户请求还不够清楚，
    # 则向用户返回一个澄清问题，并结束当前 graph 执行。
    if response.need_clarification:
        return Command(
            # END 表示本轮 graph 到此结束。
            # 用户回答澄清问题后，下一轮再继续运行。
            goto=END,

            # 向 AgentState 中追加一条 AIMessage。
            # 这条消息就是模型生成的澄清问题。
            update={"messages": [AIMessage(content=response.question)]}
        )

    # 如果模型判断不需要澄清，
    # 则进入 write_research_brief 节点。
    else:
        return Command(
            # 跳转到下一个节点：生成研究简报。
            goto="write_research_brief",

            # 将 verification 写入消息状态。
            #
            # verification 通常是一句确认性说明，例如：
            # “我理解你的请求是……接下来我将……”
            #
            # 它的作用：
            # 1. 给用户一个可见的理解确认；
            # 2. 给后续节点保留一条明确的任务理解记录；
            # 3. 降低后续研究简报偏题的概率。
            update={"messages": [AIMessage(content=response.verification)]}
        )




async def write_research_brief(
    state: AgentState,
    config: RunnableConfig
) -> Command[Literal["research_supervisor"]]:
    """
    整体注释解析：生成研究简报并初始化 Supervisor 上下文

    这个函数是 Deep Research 工作流中位于用户澄清之后、正式研究之前的关键节点。

    它的核心职责是：
    1. 从当前对话消息中提取用户的真实研究意图；
    2. 调用模型把零散、多轮、自然语言形式的用户输入，转换成结构化 research brief；
    3. 根据配置生成 Research Supervisor 的系统提示词；
    4. 初始化 supervisor_messages，使后续 research_supervisor 节点可以基于明确研究简报开始调度子研究任务；
    5. 通过 Command 跳转到 research_supervisor 节点。

    输入：
    - state: 当前 AgentState，主要包含用户消息、AI 回复等对话状态；
    - config: LangGraph / LangChain 的运行配置，包含模型、token、API key、并发数、迭代次数等参数。

    输出：
    - Command(goto="research_supervisor", update={...})
      表示：
      1. 将 research_brief 写入 AgentState；
      2. 覆盖 supervisor_messages；
      3. 跳转到 research_supervisor 节点继续执行。

    关键设计点：
    - 使用结构化输出 ResearchQuestion，保证模型输出稳定包含 research_brief；
    - 使用 with_retry 提高结构化输出可靠性；
    - 使用 supervisor_messages 的 override 机制初始化 Supervisor 对话上下文；
    - 将用户原始消息压缩成聚焦的研究简报，作为后续多智能体研究的“任务北极星”。
    """

    # =========================
    # Step 1：读取配置，并配置研究模型
    # =========================

    # 从 LangGraph / LangChain 的 RunnableConfig 中解析项目自己的 Configuration。
    #
    # Configuration 中包含：
    # - research_model：用于生成 research brief 的模型；
    # - research_model_max_tokens：模型最大输出 token 数；
    # - max_structured_output_retries：结构化输出失败时的最大重试次数；
    # - max_concurrent_research_units：后续 Supervisor 可并发调度的研究单元数；
    # - max_researcher_iterations：后续 Supervisor 最大研究迭代次数。
    configurable = Configuration.from_runnable_config(config)

    # 构造 research_model 本次调用需要的运行配置。
    #
    # 注意：
    # 这里使用的是 configurable.research_model，
    # 说明“生成研究简报”这个阶段和“执行研究”阶段默认共用 research_model。
    #
    # tags=["langsmith:nostream"] 用于 LangSmith 追踪标记，
    # 通常表示这次结构化输出调用不进行流式展示。
    research_model_config = {
        "model": configurable.research_model,
        "max_tokens": configurable.research_model_max_tokens,
        "api_key": get_api_key_for_model(configurable.research_model, config),
        "tags": ["langsmith:nostream"],
    }

    # 配置一个专门用于生成结构化研究问题 / 研究简报的模型链。
    research_model = (
        configurable_model

        # 要求模型输出符合 ResearchQuestion 结构。
        #
        # 通常 ResearchQuestion 里会包含类似字段：
        # - research_brief: str
        #
        # 这样后续可以稳定访问：
        # response.research_brief
        #
        # 而不是从一段自由文本中手动解析研究任务。
        .with_structured_output(ResearchQuestion)

        # 如果模型输出不符合 ResearchQuestion schema，
        # 则自动重试，最多重试 configurable.max_structured_output_retries 次。
        .with_retry(stop_after_attempt=configurable.max_structured_output_retries)

        # 注入模型名、max_tokens、API key、LangSmith tags 等运行配置。
        .with_config(research_model_config)
    )

    # =========================
    # Step 2：将用户消息转换为结构化 research brief
    # =========================

    # 构造“研究简报生成”提示词。
    #
    # transform_messages_into_research_topic_prompt 的作用是：
    # 引导模型把当前对话中的用户需求整理成一个清晰、聚焦、可执行的研究简报。
    #
    # get_buffer_string(...) 会把 state["messages"] 中的多轮消息转换成字符串，
    # 让模型能够看到完整上下文，而不是只看到最后一句用户输入。
    #
    # state.get("messages", []) 的写法比较稳健：
    # 即使 state 中暂时没有 messages 字段，也会返回空列表，避免 KeyError。
    #
    # date=get_today_str() 用于注入当前日期，
    # 方便模型正确理解“当前、最新、最近、今年”等时间敏感表达。
    prompt_content = transform_messages_into_research_topic_prompt.format(
        messages=get_buffer_string(state.get("messages", [])),
        date=get_today_str(),
    )

    # 异步调用 research_model，生成结构化 ResearchQuestion。
    #
    # 这里传入 HumanMessage，是因为 prompt_content 已经是一条完整的任务指令：
    # “请根据这些用户消息生成一个聚焦的 research brief。”
    #
    # 返回的 response 不是普通字符串，
    # 而是符合 ResearchQuestion schema 的结构化对象。
    response = await research_model.ainvoke(
        [HumanMessage(content=prompt_content)]
    )

    # =========================
    # Step 3：生成 Supervisor 系统提示词
    # =========================

    # 构造 Research Supervisor 的 system prompt。
    #
    # lead_researcher_prompt 通常会告诉 Supervisor：
    # - 你是研究主管；
    # - 你要如何拆分研究任务；
    # - 你最多可以并发多少个研究单元；
    # - 你最多可以迭代多少轮；
    # - 当前日期是什么；
    # - 如何判断研究是否充分。
    #
    # max_concurrent_research_units 控制并发研究规模；
    # max_researcher_iterations 控制 Supervisor 最大反思 / 追加研究轮数。
    supervisor_system_prompt = lead_researcher_prompt.format(
        date=get_today_str(),
        max_concurrent_research_units=configurable.max_concurrent_research_units,
        max_researcher_iterations=configurable.max_researcher_iterations,
    )

    # =========================
    # Step 4：更新状态，并跳转到 research_supervisor 节点
    # =========================

    # 返回 LangGraph Command。
    #
    # 它同时完成两件事：
    # 1. goto="research_supervisor"：
    #    跳转到 research_supervisor 节点；
    #
    # 2. update={...}：
    #    更新 AgentState，为 Supervisor 初始化上下文。
    return Command(
        goto="research_supervisor",
        update={
            # 将模型生成的 research brief 写入 AgentState。
            #
            # 后续 supervisor、researcher、final report 节点都可以基于它工作。
            "research_brief": response.research_brief,

            # 初始化 / 覆盖 supervisor_messages。
            #
            # 这里使用 override，表示不要在旧 supervisor_messages 后面追加，
            # 而是直接替换成新的 Supervisor 初始上下文。
            #
            # 这通常依赖 state schema 中对 supervisor_messages 的 reducer 设计。
            "supervisor_messages": {
                "type": "override",
                "value": [
                    # 第一条是 SystemMessage：
                    # 定义 Supervisor 的角色、规则、并发限制和研究迭代限制。
                    SystemMessage(content=supervisor_system_prompt),

                    # 第二条是 HumanMessage：
                    # 把 research brief 作为用户任务交给 Supervisor。
                    HumanMessage(content=response.research_brief),
                ],
            },
        },
    )


async def supervisor(
    state: SupervisorState,
    config: RunnableConfig
) -> Command[Literal["supervisor_tools"]]:
    """
    整体注释解析：Research Supervisor 研究主管节点

    这个函数是 Deep Research 多智能体系统中的“主管决策节点”。

    它的核心职责是：
    1. 读取当前 supervisor state 和运行配置；
    2. 配置一个具备工具调用能力的 research_model；
    3. 让模型基于当前 supervisor_messages 判断下一步行动；
    4. 模型可以选择调用以下工具：
       - think_tool：进行战略性思考；
       - ConductResearch：派发子研究任务给 researcher；
       - ResearchComplete：宣布研究已经完成；
    5. 将模型回复写回 supervisor_messages；
    6. 将 research_iterations 加 1；
    7. 跳转到 supervisor_tools 节点执行模型选择的工具调用。

    输入：
    - state: SupervisorState，包含 supervisor_messages、research_iterations 等研究主管状态；
    - config: RunnableConfig，包含模型、max_tokens、API key、重试次数等运行配置。

    输出：
    - Command(goto="supervisor_tools", update={...})
      表示更新 supervisor 状态，并进入 supervisor_tools 节点处理工具调用。

    关键设计点：
    - supervisor 本身不直接执行搜索或研究；
    - 它通过工具调用机制，把任务委派给 researcher 或结束研究；
    - 它是整个多智能体研究流程的“规划器”和“调度器”；
    - 每次进入 supervisor，都会让 research_iterations 增加一次，用于限制最大研究轮数。
    """

    # =========================
    # Step 1：读取配置，并配置 Supervisor 模型
    # =========================

    # 从 LangGraph / LangChain 的运行配置中解析项目自己的 Configuration。
    #
    # 这里主要会用到：
    # - research_model：Supervisor 使用的模型；
    # - research_model_max_tokens：Supervisor 单次最大输出 token 数；
    # - max_structured_output_retries：模型调用失败或工具调用格式异常时的最大重试次数。
    configurable = Configuration.from_runnable_config(config)

    # 构造 Supervisor 模型调用配置。
    #
    # 注意：
    # Supervisor 使用的是 research_model，
    # 因为它需要做任务拆分、策略规划、工具选择和研究完成判断。
    research_model_config = {
        # 研究主管使用的模型名称。
        "model": configurable.research_model,

        # Supervisor 单次输出最大 token 数。
        "max_tokens": configurable.research_model_max_tokens,

        # 根据模型名称和当前 config 获取对应 API key。
        "api_key": get_api_key_for_model(configurable.research_model, config),

        # LangSmith 追踪标签。
        # nostream 通常表示这次调用不做流式输出展示。
        "tags": ["langsmith:nostream"],
    }

    # =========================
    # Step 2：定义 Supervisor 可用工具
    # =========================

    # Supervisor 可调用的工具列表。
    #
    # ConductResearch：
    # - 用于把一个具体研究子任务委派给 sub-researcher；
    # - 例如让某个 researcher 搜索某个角度、某类证据或某个子问题。
    #
    # ResearchComplete：
    # - 用于表示 Supervisor 认为当前研究已经足够充分；
    # - 调用后通常会进入压缩或最终报告阶段。
    #
    # think_tool：
    # - 用于让 Supervisor 进行显式策略思考；
    # - 适合在决定是否继续研究、如何拆分任务、是否覆盖充分时使用。
    lead_researcher_tools = [ConductResearch, ResearchComplete, think_tool]

    # =========================
    # Step 3：绑定工具，构造具备调度能力的 Supervisor 模型
    # =========================

    # 给 configurable_model 绑定 Supervisor 可用工具。
    #
    # bind_tools(...) 的作用是：
    # 让模型不只是输出普通文本，
    # 还可以生成工具调用请求，例如：
    # - 调用 ConductResearch 派发任务；
    # - 调用 ResearchComplete 结束研究；
    # - 调用 think_tool 做规划。
    research_model = (
        configurable_model

        # 绑定工具后，模型可以根据上下文选择调用哪个工具。
        .bind_tools(lead_researcher_tools)

        # 如果模型调用失败、工具调用格式不合法、输出不符合预期，
        # 则自动重试。
        .with_retry(stop_after_attempt=configurable.max_structured_output_retries)

        # 注入模型名、max_tokens、API key、tags 等运行配置。
        .with_config(research_model_config)
    )

    # =========================
    # Step 4：基于当前 Supervisor 上下文生成下一步决策
    # =========================

    # 从 SupervisorState 中取出 supervisor_messages。
    #
    # supervisor_messages 通常由 write_research_brief 节点初始化，
    # 初始内容一般包括：
    # - SystemMessage：Supervisor 的角色、规则、并发限制、迭代限制；
    # - HumanMessage：research_brief，即用户研究任务书。
    #
    # 后续每一轮 supervisor 的回复、工具调用结果，也会不断进入这个消息列表。
    supervisor_messages = state.get("supervisor_messages", [])

    # 异步调用 Supervisor 模型。
    #
    # 模型会根据当前 supervisor_messages 决定下一步：
    # - 是否需要先 think_tool；
    # - 是否调用 ConductResearch 派发一个或多个研究任务；
    # - 是否调用 ResearchComplete 结束研究。
    #
    # 返回的 response 通常是一个 AIMessage，
    # 其中可能包含 tool_calls。
    response = await research_model.ainvoke(supervisor_messages)

    # =========================
    # Step 5：更新状态，并跳转到 supervisor_tools 节点
    # =========================

    # 返回 LangGraph Command。
    #
    # goto="supervisor_tools" 表示：
    # 当前 supervisor 只负责“决策”，
    # 具体工具调用交给 supervisor_tools 节点执行。
    return Command(
        goto="supervisor_tools",
        update={
            # 将 Supervisor 本轮模型输出追加到 supervisor_messages。
            #
            # 注意：
            # 这里写的是 [response]，不是完整消息列表。
            # 具体是追加还是覆盖，取决于 SupervisorState 中
            # supervisor_messages 字段对应的 reducer 设计。
            "supervisor_messages": [response],

            # 研究迭代次数 +1。
            #
            # 这个字段通常用于控制 Supervisor 最多运行多少轮，
            # 防止无限循环研究。
            #
            # 如果 state 中还没有 research_iterations，则默认从 0 开始。
            "research_iterations": state.get("research_iterations", 0) + 1,
        },
    )





async def supervisor_tools(
    state: SupervisorState,
    config: RunnableConfig
) -> Command[Literal["supervisor", "__end__"]]:
    """
    整体注释解析：Supervisor 工具执行节点

    这个函数负责执行 supervisor 节点中模型发起的工具调用。

    在上一阶段 supervisor(...) 中，模型会根据 research brief 和已有研究结果决定下一步动作。
    这些动作通常以 tool_calls 的形式出现。supervisor_tools(...) 的职责就是读取这些 tool_calls，
    并根据工具类型执行对应逻辑。

    它主要处理三类工具调用：
    1. think_tool：
       - 记录 Supervisor 的战略反思；
       - 不启动子研究；
       - 执行后继续回到 supervisor 节点。

    2. ConductResearch：
       - 将具体研究主题委派给 researcher_subgraph；
       - 可以并发运行多个子研究任务；
       - 每个子研究任务完成后返回 compressed_research；
       - 同时收集 raw_notes 供最终报告或后续压缩使用。

    3. ResearchComplete：
       - 表示 Supervisor 判断研究已经完成；
       - 结束研究阶段，进入 graph 后续流程。

    输入：
    - state: SupervisorState，包含 supervisor_messages、research_iterations、research_brief 等；
    - config: RunnableConfig，包含模型、并发数、最大迭代次数等运行配置。

    输出：
    - Command(goto="supervisor", update=...)
      表示工具执行完成后，带着工具结果回到 supervisor 继续判断；

    - Command(goto=END, update=...)
      表示研究阶段结束，将 notes 和 research_brief 写入状态。

    关键设计点：
    - supervisor 只负责“决定调用什么工具”；
    - supervisor_tools 负责“真正执行这些工具”；
    - ConductResearch 会通过 asyncio.gather 并发执行多个 researcher 子图；
    - max_concurrent_research_units 用于限制并发研究数量；
    - research_iterations 和 ResearchComplete 共同控制研究循环何时结束。
    """

    # =========================
    # Step 1：读取当前状态，并检查是否应该结束研究阶段
    # =========================

    # 从 RunnableConfig 中解析项目自己的 Configuration 配置对象。
    #
    # 这里主要会用到：
    # - max_researcher_iterations：Supervisor 最大研究迭代次数；
    # - max_concurrent_research_units：最大并发子研究任务数量；
    # - research_model：用于判断 token limit 等异常归属。
    configurable = Configuration.from_runnable_config(config)

    # 取出当前 Supervisor 对话上下文。
    #
    # supervisor_messages 通常包含：
    # - SystemMessage：Supervisor 的系统提示词；
    # - HumanMessage：research brief；
    # - AIMessage：Supervisor 的工具调用决策；
    # - ToolMessage：工具执行结果。
    supervisor_messages = state.get("supervisor_messages", [])

    # 取出当前已经执行过的 Supervisor 迭代次数。
    #
    # 这个字段一般在 supervisor(...) 节点中自增。
    research_iterations = state.get("research_iterations", 0)

    # 获取最近一条 Supervisor 消息。
    #
    # 通常这条消息是 supervisor(...) 刚刚生成的 AIMessage，
    # 里面可能包含 tool_calls。
    #
    # 注意：
    # 这里默认 supervisor_messages 非空。
    # 如果 supervisor_messages 为空，会触发 IndexError。
    most_recent_message = supervisor_messages[-1]

    # 判断是否超过最大研究迭代次数。
    #
    # 注意这里是 >，不是 >=。
    # 如果 max_researcher_iterations = 6，
    # 那么 research_iterations 为 7 时才会触发退出。
    exceeded_allowed_iterations = (
        research_iterations > configurable.max_researcher_iterations
    )

    # 判断最近一条消息是否没有任何工具调用。
    #
    # 如果 Supervisor 没有调用工具，说明它没有继续研究动作。
    # 这种情况下直接结束研究阶段。
    no_tool_calls = not most_recent_message.tool_calls

    # 判断 Supervisor 是否调用了 ResearchComplete 工具。
    #
    # ResearchComplete 表示 Supervisor 主动声明：
    # 当前研究已经足够，可以结束研究阶段。
    research_complete_tool_call = any(
        tool_call["name"] == "ResearchComplete"
        for tool_call in most_recent_message.tool_calls
    )

    # 如果满足任意退出条件，则结束研究阶段。
    #
    # 退出条件包括：
    # 1. 超过最大研究迭代次数；
    # 2. 最近一条 Supervisor 消息没有工具调用；
    # 3. Supervisor 调用了 ResearchComplete。
    if exceeded_allowed_iterations or no_tool_calls or research_complete_tool_call:
        return Command(
            goto=END,
            update={
                # 从 supervisor_messages 中提取工具调用产生的研究笔记。
                #
                # get_notes_from_tool_calls(...) 通常会从 ConductResearch 的 ToolMessage
                # 中提取压缩后的研究结果，供最终报告阶段使用。
                "notes": get_notes_from_tool_calls(supervisor_messages),

                # 保留 research_brief，供后续最终报告阶段使用。
                "research_brief": state.get("research_brief", ""),
            },
        )

    # =========================
    # Step 2：准备收集所有工具执行结果
    # =========================

    # all_tool_messages 用来收集本轮所有工具调用的返回消息。
    #
    # 后面会把这些 ToolMessage 写回 supervisor_messages，
    # 让下一轮 supervisor 能看到工具执行结果。
    all_tool_messages = []

    # update_payload 是本轮要写回 SupervisorState 的状态更新。
    #
    # 先初始化 supervisor_messages，
    # 后续可能额外加入 raw_notes。
    update_payload = {"supervisor_messages": []}

    # =========================
    # Step 3：处理 think_tool 工具调用
    # =========================

    # 从最近一条 AIMessage 的 tool_calls 中筛选出 think_tool 调用。
    #
    # think_tool 用于记录 Supervisor 的战略反思，
    # 例如：
    # - 当前研究是否充分；
    # - 下一轮应该补充哪些证据；
    # - 是否存在研究盲区；
    # - 是否可以结束研究。
    think_tool_calls = [
        tool_call
        for tool_call in most_recent_message.tool_calls
        if tool_call["name"] == "think_tool"
    ]

    # 遍历所有 think_tool 调用，并将其转换成 ToolMessage。
    for tool_call in think_tool_calls:
        # 从工具参数中取出 reflection 字段。
        #
        # 这个字段一般是 Supervisor 生成的一段策略性思考文本。
        reflection_content = tool_call["args"]["reflection"]

        # 将 reflection 包装成 ToolMessage。
        #
        # ToolMessage 必须携带 tool_call_id，
        # 这样 LangChain / LangGraph 能把工具结果和对应工具调用关联起来。
        all_tool_messages.append(
            ToolMessage(
                content=f"Reflection recorded: {reflection_content}",
                name="think_tool",
                tool_call_id=tool_call["id"],
            )
        )

    # =========================
    # Step 4：处理 ConductResearch 工具调用
    # =========================

    # 从最近一条 AIMessage 的 tool_calls 中筛选出 ConductResearch 调用。
    #
    # 每个 ConductResearch 调用都代表一个子研究任务。
    # 例如：
    # - 调研某个模型的能力；
    # - 查找某个系统的公开 benchmark；
    # - 验证某个技术说法；
    # - 收集某一类证据。
    conduct_research_calls = [
        tool_call
        for tool_call in most_recent_message.tool_calls
        if tool_call["name"] == "ConductResearch"
    ]

    # 如果本轮存在 ConductResearch 调用，则启动 researcher 子图执行研究。
    if conduct_research_calls:
        try:
            # 根据 max_concurrent_research_units 限制本轮允许并发执行的研究任务数量。
            #
            # allowed_conduct_research_calls：
            # - 真正会执行的研究任务；
            #
            # overflow_conduct_research_calls：
            # - 超过并发上限的研究任务；
            # - 这些任务不会执行，而是返回错误 ToolMessage。
            allowed_conduct_research_calls = conduct_research_calls[
                :configurable.max_concurrent_research_units
            ]
            overflow_conduct_research_calls = conduct_research_calls[
                configurable.max_concurrent_research_units:
            ]

            # 为每个允许执行的 ConductResearch 调用创建一个 researcher_subgraph 异步任务。
            #
            # researcher_subgraph.ainvoke(...) 会启动一个子研究流程。
            #
            # 传入状态包括：
            # - researcher_messages：
            #   给子 researcher 的初始消息，内容是 research_topic；
            #
            # - research_topic：
            #   当前子研究任务主题，供 researcher 子图内部使用。
            research_tasks = [
                researcher_subgraph.ainvoke(
                    {
                        "researcher_messages": [
                            HumanMessage(
                                content=tool_call["args"]["research_topic"]
                            )
                        ],
                        "research_topic": tool_call["args"]["research_topic"],
                    },
                    config,
                )
                for tool_call in allowed_conduct_research_calls
            ]

            # 并发执行所有子研究任务。
            #
            # asyncio.gather 会等待所有 research_tasks 完成，
            # 并按照任务顺序返回结果列表。
            #
            # 这里是真正实现“多个 researcher 并行研究”的关键。
            tool_results = await asyncio.gather(*research_tasks)

            # 将每个 researcher 子图返回的研究结果包装成 ToolMessage。
            for observation, tool_call in zip(
                tool_results,
                allowed_conduct_research_calls,
            ):
                all_tool_messages.append(
                    ToolMessage(
                        # compressed_research 是子 researcher 压缩后的研究结论。
                        #
                        # 如果没有该字段，则返回一个兜底错误信息。
                        content=observation.get(
                            "compressed_research",
                            "Error synthesizing research report: Maximum retries exceeded",
                        ),
                        name=tool_call["name"],
                        tool_call_id=tool_call["id"],
                    )
                )

            # 对超过并发上限的 ConductResearch 调用返回错误 ToolMessage。
            #
            # 这样做的好处是：
            # - 不会静默丢弃工具调用；
            # - Supervisor 下一轮能看到哪些研究任务没有执行；
            # - 可以提醒 Supervisor 下次减少并发任务数量。
            for overflow_call in overflow_conduct_research_calls:
                all_tool_messages.append(
                    ToolMessage(
                        content=(
                            "Error: Did not run this research as you have already "
                            "exceeded the maximum number of concurrent research units. "
                            f"Please try again with {configurable.max_concurrent_research_units} "
                            "or fewer research units."
                        ),
                        name="ConductResearch",
                        tool_call_id=overflow_call["id"],
                    )
                )

            # 聚合所有 researcher 子图返回的 raw_notes。
            #
            # raw_notes 通常比 compressed_research 更原始、更详细，
            # 可能包含搜索片段、引用、观察记录、中间结论等。
            raw_notes_concat = "\n".join(
                [
                    "\n".join(observation.get("raw_notes", []))
                    for observation in tool_results
                ]
            )

            # 如果存在 raw_notes，则写入 update_payload。
            #
            # 注意这里写成列表 [raw_notes_concat]，
            # 通常意味着 raw_notes 字段在 state schema 中可能使用追加型 reducer。
            if raw_notes_concat:
                update_payload["raw_notes"] = [raw_notes_concat]

        except Exception as e:
            # =========================
            # Step 5：处理子研究执行异常
            # =========================

            # 如果子研究执行过程中出现异常，则进入这里。
            #
            # 原代码写法：
            # if is_token_limit_exceeded(e, configurable.research_model) or True:
            #
            # 由于存在 or True，这个条件永远成立。
            # 也就是说：无论是不是 token limit 问题，任何异常都会直接结束研究阶段。
            if is_token_limit_exceeded(e, configurable.research_model) or True:
                return Command(
                    goto=END,
                    update={
                        # 异常时不继续研究，而是提取已有工具调用结果作为 notes。
                        "notes": get_notes_from_tool_calls(supervisor_messages),

                        # 保留 research_brief，避免最终报告阶段丢失任务目标。
                        "research_brief": state.get("research_brief", ""),
                    },
                )

    # =========================
    # Step 6：返回工具结果，并继续 Supervisor 循环
    # =========================

    # 将本轮所有工具执行结果写入 update_payload。
    #
    # 包括：
    # - think_tool 的 reflection 记录；
    # - ConductResearch 的 compressed_research；
    # - 超出并发上限的错误消息。
    update_payload["supervisor_messages"] = all_tool_messages

    # 跳回 supervisor 节点。
    #
    # 下一轮 supervisor 会读取这些 ToolMessage，
    # 然后判断：
    # - 是否继续派发研究任务；
    # - 是否补充其他方向；
    # - 是否调用 ResearchComplete 结束研究。
    return Command(
        goto="supervisor",
        update=update_payload,
    )



# =========================
# 整体注释解析：构建 Supervisor 子图
# =========================
# 这段代码创建 Deep Research 系统中的 Supervisor 子图。
#
# Supervisor 子图负责研究任务的规划、委派、工具执行和循环控制：
# 1. 从 START 进入 supervisor 节点；
# 2. supervisor 调用模型，决定下一步工具调用；
# 3. supervisor_tools 执行工具调用；
# 4. 如果还需要继续研究，则回到 supervisor；
# 5. 如果研究完成、超过最大迭代次数或没有工具调用，则结束子图。
#
# 这个子图一般会被主图调用，用于完成“研究主管调度阶段”。
supervisor_builder = StateGraph(SupervisorState, config_schema=Configuration)


# =========================
# 整体注释解析：注册 Supervisor 子图节点
# =========================
# supervisor：
# - 负责研究规划和工具调用决策；
# - 会绑定 ConductResearch、ResearchComplete、think_tool；
# - 返回 Command(goto="supervisor_tools")。
#
# supervisor_tools：
# - 负责真正执行 supervisor 发起的工具调用；
# - 可以并发启动 researcher_subgraph；
# - 根据结果返回 supervisor 或 END。
supervisor_builder.add_node("supervisor", supervisor)
supervisor_builder.add_node("supervisor_tools", supervisor_tools)


# =========================
# 整体注释解析：定义子图入口
# =========================
# START 是子图入口。
# 这条边表示 supervisor_subgraph 一启动，就先执行 supervisor 节点。
#
# 后续 supervisor 和 supervisor_tools 之间的流转，
# 主要由节点内部返回的 Command(goto=...) 控制。
supervisor_builder.add_edge(START, "supervisor")


# =========================
# 整体注释解析：编译子图
# =========================
# compile() 会把 StateGraph 构建器转换成可执行 graph。
# 编译后的 supervisor_subgraph 可以被主图作为子图调用。
supervisor_subgraph = supervisor_builder.compile()







async def researcher(
    state: ResearcherState,
    config: RunnableConfig
) -> Command[Literal["researcher_tools"]]:
    """
    整体注释解析：单个 Researcher 子研究节点

    这个函数是 Deep Research 系统中的“子研究员节点”。

    它通常不是直接接收用户原始问题，而是接收 Supervisor 通过 ConductResearch
    派发下来的某个具体 research_topic，然后围绕这个局部主题进行 focused research。

    核心职责：
    1. 读取当前 researcher 状态和运行配置；
    2. 加载当前可用工具，包括搜索工具、MCP 工具和 think_tool；
    3. 检查是否至少存在一个可用研究工具；
    4. 构造 researcher 专用 system prompt；
    5. 给 research_model 绑定工具调用能力；
    6. 让模型基于当前子研究任务生成下一步动作；
    7. 将模型回复写回 researcher_messages；
    8. 将 tool_call_iterations 加 1；
    9. 跳转到 researcher_tools 节点执行工具调用。

    输入：
    - state: ResearcherState，包含 researcher_messages、research_topic、
      tool_call_iterations 等子研究状态；
    - config: RunnableConfig，包含模型、搜索 API、MCP、max_tokens 等运行配置。

    输出：
    - Command(goto="researcher_tools", update={...})
      表示当前 researcher 已经生成工具调用决策，
      下一步进入 researcher_tools 节点真正执行工具。

    关键设计点：
    - researcher 负责具体信息搜集，不负责全局研究规划；
    - researcher 的工具集合由 get_all_tools(config) 动态决定；
    - 如果没有搜索工具或 MCP 工具，研究无法进行，直接报错；
    - 每次 researcher 调用后都会增加 tool_call_iterations，
      用于限制单个子研究任务的最大工具调用轮数。
    """

    # =========================
    # Step 1：读取配置，并检查工具可用性
    # =========================

    # 从 RunnableConfig 中解析项目自定义 Configuration。
    #
    # 这里主要会用到：
    # - research_model：子研究员使用的模型；
    # - research_model_max_tokens：模型最大输出 token 数；
    # - max_structured_output_retries：工具调用格式异常时的重试次数；
    # - search_api：决定是否加载搜索工具；
    # - mcp_config / mcp_prompt：决定是否加载 MCP 工具和 MCP 使用说明。
    configurable = Configuration.from_runnable_config(config)

    # 取出当前 researcher 的消息上下文。
    #
    # researcher_messages 通常由 supervisor_tools 初始化，
    # 初始内容一般是：
    # HumanMessage(content=tool_call["args"]["research_topic"])
    #
    # 后续每一轮 researcher 的 AIMessage 和工具返回的 ToolMessage
    # 也会追加到这个列表中。
    researcher_messages = state.get("researcher_messages", [])

    # 加载当前 researcher 可用的所有工具。
    #
    # get_all_tools(config) 通常会根据配置动态组合：
    # - 搜索工具，例如 Tavily / OpenAI Search / Anthropic Search；
    # - MCP tools；
    # - think_tool；
    # - 其他项目自定义研究工具。
    tools = await get_all_tools(config)

    # 如果没有任何工具，则无法进行研究。
    #
    # Deep Research 的 researcher 依赖工具获取外部信息。
    # 如果既没有搜索 API，也没有 MCP tools，
    # 那 researcher 只能凭模型内部知识回答，这会破坏系统的真实性和证据链。
    if len(tools) == 0:
        raise ValueError(
            "No tools found to conduct research: Please configure either your "
            "search API or add MCP tools to your configuration."
        )

    # =========================
    # Step 2：配置具备工具调用能力的 Researcher 模型
    # =========================

    # 构造本次 researcher 模型调用配置。
    #
    # researcher 使用 research_model，
    # 因为它需要执行具体研究、调用工具、综合搜索结果。
    research_model_config = {
        # 子研究员使用的模型名称。
        "model": configurable.research_model,

        # 单次模型输出最大 token 数。
        "max_tokens": configurable.research_model_max_tokens,

        # 根据模型名称和运行配置获取对应 API key。
        "api_key": get_api_key_for_model(configurable.research_model, config),

        # LangSmith 追踪标签。
        # nostream 通常表示这次调用不做流式输出展示。
        "tags": ["langsmith:nostream"],
    }

    # 构造 researcher 专用 system prompt。
    #
    # research_system_prompt 通常会告诉 researcher：
    # - 你是一个子研究员；
    # - 你要围绕给定 research_topic 做聚焦研究；
    # - 你可以使用搜索工具和 MCP 工具；
    # - 你应该记录证据、来源和关键发现；
    # - 不要偏离 Supervisor 派发的具体主题。
    #
    # mcp_prompt：
    # - 如果配置了 MCP 工具说明，就注入 prompt；
    # - 如果没有配置，则传入空字符串。
    #
    # date：
    # - 提供当前日期；
    # - 帮助模型正确理解“最新、最近、当前、今年”等时间表达。
    researcher_prompt = research_system_prompt.format(
        mcp_prompt=configurable.mcp_prompt or "",
        date=get_today_str(),
    )

    # 给模型绑定 researcher 可用工具，并注入重试逻辑和运行配置。
    research_model = (
        configurable_model

        # 绑定工具后，模型可以生成 tool_calls。
        #
        # 例如：
        # - 调用搜索工具查资料；
        # - 调用 think_tool 做中间规划；
        # - 调用 MCP 工具读取文件、查数据库或访问外部系统。
        .bind_tools(tools)

        # 如果模型输出工具调用格式异常、请求失败或解析失败，
        # 则自动重试。
        .with_retry(stop_after_attempt=configurable.max_structured_output_retries)

        # 注入模型名、max_tokens、API key、tags 等配置。
        .with_config(research_model_config)
    )

    # =========================
    # Step 3：调用 Researcher 模型，生成下一步工具调用决策
    # =========================

    # 组合完整上下文消息。
    #
    # 第一条是 SystemMessage：
    # - 定义 researcher 的角色、约束和工具使用规则。
    #
    # 后面接 researcher_messages：
    # - 包含 Supervisor 派发的 research_topic；
    # - 包含前几轮 researcher 回复；
    # - 包含工具返回结果。
    messages = [SystemMessage(content=researcher_prompt)] + researcher_messages

    # 异步调用 researcher 模型。
    #
    # 模型会根据当前子研究上下文决定下一步：
    # - 是否调用搜索工具；
    # - 是否调用 MCP 工具；
    # - 是否调用 think_tool；
    # - 是否直接给出中间研究结论。
    #
    # 返回的 response 通常是 AIMessage，
    # 其中可能包含 tool_calls。
    response = await research_model.ainvoke(messages)

    # =========================
    # Step 4：更新 researcher 状态，并跳转到 researcher_tools
    # =========================

    # 返回 LangGraph Command。
    #
    # 当前 researcher 节点只负责“生成决策”；
    # 真正执行工具调用的是 researcher_tools 节点。
    return Command(
        goto="researcher_tools",
        update={
            # 将本轮 researcher 的模型回复写入 researcher_messages。
            #
            # 注意这里是 [response]，不是完整列表。
            # 实际追加还是覆盖，取决于 ResearcherState 中
            # researcher_messages 字段的 reducer 设计。
            "researcher_messages": [response],

            # 工具调用轮数 +1。
            #
            # 这个字段通常用于限制单个 researcher 的最大 ReAct 循环次数，
            # 防止某个子研究任务无限搜索或过度探索。
            "tool_call_iterations": state.get("tool_call_iterations", 0) + 1,
        },
    )





# =========================
# 整体注释解析：安全执行工具的辅助函数
# =========================
# 这个函数用于统一执行工具调用，并捕获工具执行过程中的异常。
#
# 在 Agent / LangGraph 系统中，工具调用可能因为很多原因失败，例如：
# - 搜索 API 报错；
# - MCP server 连接失败；
# - 参数格式不正确；
# - API key 缺失；
# - 网络超时；
# - 工具内部逻辑异常。
#
# 如果不捕获异常，单个工具失败可能会直接中断整个 graph。
# 这个函数的作用就是把异常转成字符串结果返回，
# 让上层 Agent 可以继续运行，并把错误信息作为 observation 交给模型处理。
#
# 输入：
# - tool：要执行的工具对象，通常需要支持 ainvoke(...)；
# - args：传给工具的参数；
# - config：LangChain / LangGraph 的运行配置。
#
# 输出：
# - 成功时：返回工具本身的执行结果；
# - 失败时：返回一段错误字符串，例如 "Error executing tool: ..."。
async def execute_tool_safely(tool, args, config):
    """
    整体注释解析：安全执行单个工具调用

    这个异步函数封装了 tool.ainvoke(args, config) 的执行过程。

    它的核心逻辑非常简单：
    1. 尝试异步调用工具；
    2. 如果工具正常执行，就返回工具结果；
    3. 如果工具执行失败，就捕获异常；
    4. 将异常信息转换成字符串返回，而不是让异常继续向外抛出。

    这个函数适合用于 researcher_tools 这类批量工具执行场景，
    可以避免某一个工具失败导致整个工具执行节点崩溃。
    """

    try:
        # 异步调用工具。
        #
        # ainvoke(...) 是 LangChain Runnable / Tool 常见的异步调用接口。
        #
        # args：
        # - 工具调用参数；
        # - 通常来自模型生成的 tool_call["args"]。
        #
        # config：
        # - 运行配置；
        # - 可包含 tracing、callbacks、metadata、configurable 等信息。
        return await tool.ainvoke(args, config)

    except Exception as e:
        # 捕获工具执行过程中的所有异常。
        #
        # 这里没有重新 raise，
        # 而是把错误转换成字符串返回。
        #
        # 好处：
        # - 避免单个工具失败中断整个 graph；
        # - 让模型可以看到错误信息；
        # - 上层逻辑可以继续处理其他工具结果。
        #
        # 风险：
        # - 异常被吞掉后，调试时可能不容易发现真实错误堆栈；
        # - 返回值类型变成“正常结果或错误字符串”的混合类型。
        return f"Error executing tool: {str(e)}"








async def researcher_tools(
    state: ResearcherState,
    config: RunnableConfig
) -> Command[Literal["researcher", "compress_research"]]:
    """
    整体注释解析：Researcher 工具执行节点，带单轮并发工具调用上限控制

    这个函数负责执行 researcher 节点中模型生成的工具调用。

    在上一阶段 researcher(...) 中，模型会根据当前 research_topic、已有工具结果和系统提示词，
    决定是否调用搜索工具、MCP 工具、think_tool 或 ResearchComplete。
    researcher_tools(...) 的职责是：
    1. 读取 researcher 最新消息；
    2. 判断是否存在工具调用或原生 web search；
    3. 如果没有任何工具行为，则进入 compress_research；
    4. 动态加载当前可用工具；
    5. 根据 max_concurrent_researcher_tool_calls 限制本轮实际执行的工具调用数量；
    6. 并发执行允许范围内的工具调用；
    7. 对超出并发上限的工具调用返回显式 ToolMessage；
    8. 检查是否达到最大 ReAct 轮数，或是否调用 ResearchComplete；
    9. 决定回到 researcher 继续研究，还是进入 compress_research 压缩结果。

    关键设计点：
    - researcher 负责“生成工具调用决策”；
    - researcher_tools 负责“执行工具调用并返回 observation”；
    - allowed_tool_calls 控制本轮真正执行的工具数量；
    - overflow_tool_calls 不会被执行，但会返回 ToolMessage，保证 tool-call 协议完整；
    - max_react_tool_calls 控制单个 researcher 最多循环多少轮；
    - max_concurrent_researcher_tool_calls 控制单轮最多并发执行多少个工具调用。
    """

    # =========================
    # Step 1：读取当前状态，并检查提前退出条件
    # =========================

    # 从 RunnableConfig 中解析项目自定义 Configuration。
    #
    # 这里会用到：
    # - max_react_tool_calls：单个 researcher 最大工具调用轮数；
    # - max_concurrent_researcher_tool_calls：单轮最大并发工具调用数量；
    # - search_api / mcp_config：决定 get_all_tools 加载哪些工具。
    configurable = Configuration.from_runnable_config(config)

    # 取出当前 researcher 的消息上下文。
    #
    # researcher_messages 通常包含：
    # - HumanMessage：Supervisor 派发的 research_topic；
    # - AIMessage：researcher 的工具调用决策；
    # - ToolMessage：搜索、MCP、think_tool 等工具返回结果。
    researcher_messages = state.get("researcher_messages", [])

    # 取出最近一条 researcher 消息。
    #
    # 正常情况下，这条消息应该是 researcher(...) 节点刚生成的 AIMessage，
    # 里面可能包含 tool_calls。
    #
    # 注意：
    # 如果 researcher_messages 为空，这里会触发 IndexError。
    most_recent_message = researcher_messages[-1]

    # 判断是否存在普通工具调用。
    #
    # 标准 LangChain tool calling 通常会把工具调用放在：
    # most_recent_message.tool_calls
    has_tool_calls = bool(most_recent_message.tool_calls)

    # 判断是否调用了模型厂商原生 web search。
    #
    # OpenAI / Anthropic 的 native search 有时不一定以普通 tool_calls 形式出现，
    # 因此需要额外检查。
    has_native_search = (
        openai_websearch_called(most_recent_message) or
        anthropic_websearch_called(most_recent_message)
    )

    # 如果既没有普通工具调用，也没有原生搜索，
    # 说明 researcher 没有继续采取外部研究动作。
    #
    # 此时认为当前子研究过程可以结束，
    # 进入 compress_research，把已有 researcher_messages 压缩成研究总结。
    if not has_tool_calls and not has_native_search:
        return Command(goto="compress_research")

    # =========================
    # Step 2：加载工具，并构造工具名称映射
    # =========================

    # 动态加载当前 researcher 可用的全部工具。
    #
    # get_all_tools(config) 通常会返回：
    # - ResearchComplete；
    # - think_tool；
    # - 搜索工具，例如 Tavily / OpenAI / Anthropic search；
    # - MCP tools；
    # - 其他自定义工具。
    tools = await get_all_tools(config)

    # 构造工具名称到工具对象的映射。
    #
    # 普通 Tool 一般有 .name 属性；
    # 如果工具是 dict，则尝试从 dict["name"] 读取；
    # 如果没有 name，则兜底为 "web_search"。
    tools_by_name = {
        tool.name if hasattr(tool, "name") else tool.get("name", "web_search"): tool
        for tool in tools
    }

    # =========================
    # Step 3：限制本轮工具调用数量，防止过度 fan-out
    # =========================

    # 取出最近一条 AIMessage 中的所有普通工具调用。
    #
    # 每个 tool_call 通常包含：
    # - name：工具名称；
    # - args：工具参数；
    # - id：工具调用 ID。
    tool_calls = most_recent_message.tool_calls

    # 只允许执行前 max_concurrent_researcher_tool_calls 个工具调用。
    #
    # 这一步是本版代码的关键新增点：
    # 它限制“单个 researcher 在一轮中最多并发调用多少个工具”。
    #
    # 作用：
    # - 防止模型一次性发起过多搜索；
    # - 降低 token 消耗；
    # - 降低 API rate limit 风险；
    # - 防止研究过程过度扇出。
    allowed_tool_calls = tool_calls[
        :configurable.max_concurrent_researcher_tool_calls
    ]

    # 超出并发上限的工具调用不会真正执行。
    #
    # 但后面会为它们生成 ToolMessage，
    # 否则可能破坏模型工具调用协议。
    overflow_tool_calls = tool_calls[
        configurable.max_concurrent_researcher_tool_calls:
    ]

    # =========================
    # Step 4：并发执行允许范围内的工具调用
    # =========================

    # 为每个允许执行的 tool_call 创建异步工具执行任务。
    #
    # execute_tool_safely(...) 会调用：
    # tool.ainvoke(args, config)
    #
    # 并捕获异常，把异常转成字符串 observation 返回。
    #
    # 注意：
    # 这里默认 tool_call["name"] 一定存在于 tools_by_name。
    # 如果模型调用了未知工具，会触发 KeyError。
    tool_execution_tasks = [
        execute_tool_safely(
            tools_by_name[tool_call["name"]],
            tool_call["args"],
            config,
        )
        for tool_call in allowed_tool_calls
    ]

    # 并发执行允许范围内的所有工具调用。
    #
    # 这是 researcher 工具执行阶段的并发点。
    observations = await asyncio.gather(*tool_execution_tasks)

    # 将每个工具执行结果包装成 ToolMessage。
    #
    # ToolMessage 会被写回 researcher_messages，
    # 供下一轮 researcher 模型读取。
    #
    # tool_call_id 必须对应原始 tool_call["id"]，
    # 用于维持工具调用和工具结果之间的协议匹配关系。
    tool_outputs = [
        ToolMessage(
            content=observation,
            name=tool_call["name"],
            tool_call_id=tool_call["id"],
        )
        for observation, tool_call in zip(observations, allowed_tool_calls)
    ]

    # =========================
    # Step 5：为超出并发上限的工具调用返回显式 ToolMessage
    # =========================

    # 对于 overflow_tool_calls，虽然不真正执行工具，
    # 但仍然必须返回一个对应的 ToolMessage。
    #
    # 这是非常重要的工程设计：
    # 当模型发起 tool_call 后，框架通常期望每个 tool_call_id
    # 都能得到一个对应 ToolMessage。
    #
    # 如果直接丢弃 overflow tool call，
    # 可能导致：
    # - 工具调用协议不完整；
    # - 后续消息校验失败；
    # - 模型不知道哪些工具没有被执行；
    # - researcher 下一轮无法自我纠正。
    for overflow_call in overflow_tool_calls:
        tool_outputs.append(
            ToolMessage(
                content=(
                    "Error: Did not run this tool call because the researcher "
                    "exceeded the maximum number of concurrent tool calls. "
                    f"Please try again with "
                    f"{configurable.max_concurrent_researcher_tool_calls} "
                    "or fewer tool calls in one round."
                ),
                name=overflow_call["name"],
                tool_call_id=overflow_call["id"],
            )
        )

    # =========================
    # Step 6：检查工具执行后的退出条件
    # =========================

    # 判断当前 researcher 是否已经达到最大工具调用轮数。
    #
    # tool_call_iterations 通常在 researcher(...) 节点中自增。
    #
    # 注意：
    # 这里限制的是 researcher loop 的轮数，
    # 不是本轮 tool_calls 的数量。
    exceeded_iterations = (
        state.get("tool_call_iterations", 0)
        >= configurable.max_react_tool_calls
    )

    # 判断 researcher 是否调用了 ResearchComplete。
    #
    # ResearchComplete 表示当前子研究员主动声明：
    # 当前 research_topic 已经研究完成，可以进入压缩阶段。
    #
    # 注意：
    # 这里检查的是 most_recent_message.tool_calls 中的全部 tool calls，
    # 包括 overflow_tool_calls。
    research_complete_called = any(
        tool_call["name"] == "ResearchComplete"
        for tool_call in most_recent_message.tool_calls
    )

    # 如果达到最大 ReAct 轮数，或者调用了 ResearchComplete，
    # 则进入 compress_research。
    if exceeded_iterations or research_complete_called:
        return Command(
            goto="compress_research",
            update={
                # 写回本轮工具结果。
                #
                # 这样 compress_research 能看到最后一轮 observation，
                # 包括正常工具结果和 overflow 错误提示。
                "researcher_messages": tool_outputs
            },
        )

    # =========================
    # Step 7：继续 researcher 循环
    # =========================

    # 如果还没有达到退出条件，
    # 则把本轮工具结果写回 researcher_messages，
    # 然后回到 researcher 节点，让模型继续基于 observation 做下一步研究决策。
    return Command(
        goto="researcher",
        update={
            "researcher_messages": tool_outputs
        },
    )





async def compress_research(state: ResearcherState, config: RunnableConfig):
    """
    整体注释解析：压缩并综合单个 researcher 的研究结果

    这个函数是 researcher 子图的收尾节点。

    在前面的 researcher / researcher_tools 循环中，子研究员可能已经进行了多轮：
    - 搜索工具调用；
    - MCP 工具调用；
    - think_tool 反思；
    - AI 中间分析；
    - 工具 observation 收集。

    compress_research 的任务是把这些较长、较散的研究过程记录，
    压缩成一个更清晰、更紧凑、更适合 Supervisor 阅读的研究总结。

    核心职责：
    1. 读取 compression_model 配置；
    2. 构造专门用于“研究压缩”的模型；
    3. 获取 researcher_messages 中的完整子研究上下文；
    4. 追加一条提示，让模型从“继续研究模式”切换到“总结压缩模式”；
    5. 调用 compression_model 生成 compressed_research；
    6. 同时保留 tool / ai 消息中的 raw_notes；
    7. 如果遇到 token limit，则裁剪旧消息后重试；
    8. 如果多次失败，则返回兜底错误信息和原始笔记。

    输入：
    - state: ResearcherState，包含 researcher_messages 等子研究过程状态；
    - config: RunnableConfig，包含 compression_model、max_tokens、API key 等运行配置。

    输出：
    - dict:
      {
          "compressed_research": "...",
          "raw_notes": [...]
      }

    关键设计点：
    - compressed_research 用于返回给 Supervisor，作为高密度研究发现；
    - raw_notes 用于保留更原始的工具结果和 AI 分析，防止证据完全丢失；
    - token limit 触发时，会通过 remove_up_to_last_ai_message(...) 删除部分旧上下文后重试；
    - 最多尝试 3 次，避免压缩阶段无限重试。
    """

    # =========================
    # Step 1：读取配置，并配置压缩模型
    # =========================

    # 从 RunnableConfig 中解析项目自定义 Configuration。
    #
    # 这里主要使用：
    # - compression_model：用于压缩 researcher 结果的模型；
    # - compression_model_max_tokens：压缩模型最大输出 token 数；
    # - research_model：当前代码中用于 token limit 判断；
    # - API key 相关配置。
    configurable = Configuration.from_runnable_config(config)

    # 配置压缩模型。
    #
    # 注意：
    # 这里没有使用 research_model，而是使用 compression_model。
    #
    # 这样做的好处是：
    # - researcher 执行搜索时可以用强模型；
    # - 压缩阶段可以用另一个成本更低或更适合总结的模型；
    # - 不同阶段可以独立调参。
    synthesizer_model = configurable_model.with_config({
        "model": configurable.compression_model,
        "max_tokens": configurable.compression_model_max_tokens,
        "api_key": get_api_key_for_model(configurable.compression_model, config),

        # LangSmith 追踪标签。
        # nostream 通常表示该模型调用不做流式输出展示。
        "tags": ["langsmith:nostream"],
    })

    # =========================
    # Step 2：准备待压缩的 researcher 消息
    # =========================

    # 取出 researcher 子图中累积的所有消息。
    #
    # researcher_messages 通常包含：
    # - HumanMessage：Supervisor 派发的 research_topic；
    # - AIMessage：researcher 的中间分析和工具调用请求；
    # - ToolMessage：搜索工具、MCP 工具、think_tool 的返回结果。
    researcher_messages = state.get("researcher_messages", [])

    # 追加一条 HumanMessage，显式告诉模型：
    # 现在不要继续搜索或调用工具了，而是切换到“压缩总结模式”。
    #
    # compress_research_simple_human_message 通常类似于：
    # “请根据以上研究过程，总结出清晰、完整、保留证据的研究发现。”
    #
    # 注意：
    # 这里使用 append 会原地修改 researcher_messages。
    # 如果 researcher_messages 是从 state 中直接取出的列表，
    # 这可能会改变原始 state 中的列表对象。
    researcher_messages.append(
        HumanMessage(content=compress_research_simple_human_message)
    )

    # =========================
    # Step 3：带重试地执行研究压缩
    # =========================

    # 当前压缩尝试次数。
    synthesis_attempts = 0

    # 最大压缩尝试次数。
    #
    # 设置上限是为了防止压缩阶段因为 token limit 或模型异常无限循环。
    max_attempts = 3

    # 只要还没超过最大尝试次数，就继续尝试压缩。
    while synthesis_attempts < max_attempts:
        try:
            # 构造压缩阶段的 system prompt。
            #
            # compress_research_system_prompt 通常会告诉模型：
            # - 你现在不是 researcher；
            # - 你的任务是压缩已有研究结果；
            # - 保留关键发现、证据、来源和限制；
            # - 不要引入未被研究过程支持的新信息。
            #
            # date 用于提供当前日期，帮助处理时间敏感信息。
            compression_prompt = compress_research_system_prompt.format(
                date=get_today_str()
            )

            # 组合完整压缩上下文。
            #
            # 第一条 SystemMessage：
            # - 定义压缩任务的角色和规则；
            #
            # 后续 researcher_messages：
            # - 包含完整研究过程；
            # - 最后一条是刚追加的“请开始压缩”的 HumanMessage。
            messages = [SystemMessage(content=compression_prompt)] + researcher_messages

            # 调用压缩模型生成综合摘要。
            #
            # response.content 就是压缩后的研究总结文本。
            response = await synthesizer_model.ainvoke(messages)

            # 从 researcher_messages 中提取原始笔记。
            #
            # 这里只保留 tool 和 ai 类型消息：
            # - tool：搜索结果、MCP 返回、think_tool 记录等；
            # - ai：researcher 的中间推理、总结和工具调用上下文。
            #
            # 不包含 human / system，是为了减少无关提示词内容。
            raw_notes_content = "\n".join([
                str(message.content)
                for message in filter_messages(
                    researcher_messages,
                    include_types=["tool", "ai"],
                )
            ])

            # 压缩成功后，返回两个结果：
            #
            # compressed_research：
            # - 给 Supervisor 阅读的高密度研究摘要；
            #
            # raw_notes：
            # - 更原始的研究记录；
            # - 后续可用于最终报告、调试、证据回溯。
            return {
                "compressed_research": str(response.content),
                "raw_notes": [raw_notes_content],
            }

        except Exception as e:
            # 如果压缩失败，先增加尝试次数。
            synthesis_attempts += 1

            # 如果失败原因是 token limit exceeded，
            # 则裁剪 researcher_messages 后继续重试。
            #
            # remove_up_to_last_ai_message(...) 的作用通常是：
            # - 删除一部分较早的消息；
            # - 尽量保留最近的 AI 消息之后的内容；
            # - 降低上下文长度；
            # - 让下一次压缩调用不再超出 token 限制。
            #
            # 注意：
            # 这里判断时传入的是 configurable.research_model，
            # 但当前实际调用的是 compression_model。
            # 从语义上看，更合理的参数可能是 configurable.compression_model。
            if is_token_limit_exceeded(e, configurable.research_model):
                researcher_messages = remove_up_to_last_ai_message(
                    researcher_messages
                )
                continue

            # 对于非 token limit 错误，当前逻辑也是继续重试。
            #
            # 这意味着：
            # - API 短暂异常可能被下一轮恢复；
            # - 但真实代码错误也会被吞掉，不容易暴露堆栈。
            continue

    # =========================
    # Step 4：多次压缩失败后，返回兜底结果
    # =========================

    # 如果 3 次尝试全部失败，则仍然提取 raw_notes。
    #
    # 这样即使 compressed_research 生成失败，
    # 上层节点至少还能拿到原始研究记录，
    # 不至于完全丢失 researcher 已经完成的工作。
    raw_notes_content = "\n".join([
        str(message.content)
        for message in filter_messages(
            researcher_messages,
            include_types=["tool", "ai"],
        )
    ])

    # 返回压缩失败的兜底结果。
    #
    # compressed_research 是错误说明；
    # raw_notes 仍然保留已有研究材料。
    return {
        "compressed_research": "Error synthesizing research report: Maximum retries exceeded",
        "raw_notes": [raw_notes_content],
    }





# =========================
# 整体注释解析：构建 Researcher 子图
# =========================
# 这段代码创建单个 researcher 的子图工作流。
#
# Researcher 子图负责执行一个具体的子研究任务：
# 1. 从 START 进入 researcher；
# 2. researcher 调用模型，决定下一步工具调用；
# 3. researcher_tools 执行搜索、MCP、think_tool 等工具；
# 4. 如果还需要继续研究，则回到 researcher；
# 5. 如果研究完成或达到工具调用上限，则进入 compress_research；
# 6. compress_research 压缩研究过程，生成 compressed_research 和 raw_notes；
# 7. 子图进入 END，并把结果返回给上层 Supervisor。
#
# 这个子图通常由 supervisor_tools 并发调用，
# 每个 researcher_subgraph 负责一个独立 research_topic。
researcher_builder = StateGraph(
    ResearcherState,
    output=ResearcherOutputState,
    config_schema=Configuration,
)


# =========================
# 整体注释解析：注册 Researcher 子图节点
# =========================
# researcher：
# - 子研究员决策节点；
# - 负责调用 research_model；
# - 根据当前上下文决定是否调用搜索、MCP、think_tool 或 ResearchComplete。
#
# researcher_tools：
# - 工具执行节点；
# - 负责真正执行 researcher 发起的工具调用；
# - 根据执行结果决定继续循环或进入压缩。
#
# compress_research：
# - 结果压缩节点；
# - 负责把完整研究过程压缩成 Supervisor 可使用的研究总结。
researcher_builder.add_node("researcher", researcher)
researcher_builder.add_node("researcher_tools", researcher_tools)
researcher_builder.add_node("compress_research", compress_research)


# =========================
# 整体注释解析：定义 Researcher 子图入口和出口
# =========================
# START → researcher：
# - 子图启动后，先进入 researcher 节点。
#
# compress_research → END：
# - 研究压缩完成后，子图结束。
#
# researcher 和 researcher_tools 之间的循环，
# 主要由节点内部返回的 Command(goto=...) 动态控制。
researcher_builder.add_edge(START, "researcher")
researcher_builder.add_edge("compress_research", END)


# =========================
# 整体注释解析：编译 Researcher 子图
# =========================
# compile() 会把图定义编译成可执行对象。
#
# researcher_subgraph 可以被 supervisor_tools 调用，
# 并且可以通过 asyncio.gather 并发运行多个子研究任务。
researcher_subgraph = researcher_builder.compile()






async def final_report_generation(state: AgentState, config: RunnableConfig):
    """
    整体注释解析：生成最终研究报告

    这个函数是 Deep Research 主流程中的最终报告生成节点。

    它接收前面 Supervisor / Researcher 阶段积累下来的研究结果 notes，
    再结合原始用户消息、research_brief 和当前日期，调用 final_report_model
    生成最终的综合研究报告。

    核心职责：
    1. 从 AgentState 中读取 notes，也就是前面研究阶段汇总出的研究发现；
    2. 将 notes 拼接成 findings，作为最终报告模型的主要证据材料；
    3. 配置 final_report_model；
    4. 构造 final_report_generation_prompt；
    5. 调用模型生成最终报告；
    6. 如果遇到 token limit，则逐步截断 findings 并重试；
    7. 如果生成成功，返回 final_report，并清空 notes；
    8. 如果多次失败，返回错误信息。

    输入：
    - state: AgentState，包含用户原始 messages、research_brief、notes 等；
    - config: RunnableConfig，包含最终报告模型、max_tokens、API key 等运行配置。

    输出：
    - dict:
      {
          "final_report": "...",
          "messages": [final_report],
          "notes": {"type": "override", "value": []}
      }

    关键设计点：
    - notes 是研究阶段的中间材料，最终报告生成后会被清空；
    - final_report_model 可以和 research_model / compression_model 分开配置；
    - token 超限时不会立刻失败，而是通过截断 findings 进行最多 3 次重试；
    - findings 的截断用字符数近似 token 数，属于工程上的粗略兜底策略。
    """

    # =========================
    # Step 1：提取研究发现，并准备清理中间状态
    # =========================

    # 从 AgentState 中取出 notes。
    #
    # notes 通常来自 supervisor_subgraph 的最终输出，
    # 里面包含多个 researcher 子任务返回的 compressed_research。
    #
    # 如果没有 notes，则使用空列表，避免 KeyError。
    notes = state.get("notes", [])

    # 准备清空 notes 的状态更新。
    #
    # {"type": "override", "value": []} 表示：
    # 不要在旧 notes 后面追加，而是直接把 notes 覆盖为空列表。
    #
    # 这样做的目的：
    # - 最终报告已经生成；
    # - 中间研究笔记不再需要继续保留在主状态中；
    # - 避免后续流程重复使用旧 notes；
    # - 控制状态体积。
    cleared_state = {
        "notes": {
            "type": "override",
            "value": [],
        }
    }

    # 将所有研究笔记拼接成一个长字符串。
    #
    # findings 是最终报告模型最核心的输入材料，
    # 相当于前面所有 researcher 研究结果的汇总证据池。
    findings = "\n".join(notes)

    # =========================
    # Step 2：配置最终报告生成模型
    # =========================

    # 从 RunnableConfig 中解析项目自定义配置。
    #
    # 这里主要使用：
    # - final_report_model：最终报告模型；
    # - final_report_model_max_tokens：最终报告最大输出 token 数；
    # - API key 获取逻辑；
    # - token limit 判断逻辑。
    configurable = Configuration.from_runnable_config(config)

    # 构造最终报告模型调用配置。
    #
    # final_report_model 专门负责长文综合、结构组织和最终表达。
    # 它可以不同于 research_model 和 compression_model。
    writer_model_config = {
        # 最终报告生成模型。
        "model": configurable.final_report_model,

        # 最终报告最大输出 token 数。
        # 这个值会影响报告长度上限。
        "max_tokens": configurable.final_report_model_max_tokens,

        # 根据模型名称和当前运行配置获取对应 API key。
        "api_key": get_api_key_for_model(configurable.final_report_model, config),

        # LangSmith 追踪标签。
        # nostream 通常表示该调用不进行流式展示。
        "tags": ["langsmith:nostream"],
    }

    # =========================
    # Step 3：带 token limit 重试逻辑地生成最终报告
    # =========================

    # 最大重试次数。
    #
    # 注意：
    # 下面 while 条件是 current_retry <= max_retries。
    # 如果 max_retries = 3，理论上最多会尝试 4 次：
    # - 初始尝试；
    # - retry 1；
    # - retry 2；
    # - retry 3。
    max_retries = 3

    # 当前已经发生的 token limit 重试次数。
    current_retry = 0

    # findings 截断长度。
    #
    # 初始为 None，只有在第一次 token limit 失败后才会计算。
    findings_token_limit = None

    # 只要没有超过最大重试次数，就继续尝试生成报告。
    while current_retry <= max_retries:
        try:
            # 构造最终报告生成 prompt。
            #
            # final_report_generation_prompt 通常会包含：
            # - 用户原始研究请求；
            # - research_brief；
            # - 所有研究发现 findings；
            # - 当前日期；
            # - 最终报告的写作规范。
            #
            # research_brief：
            # - 来自 write_research_brief 阶段；
            # - 是对用户需求的结构化压缩。
            #
            # messages：
            # - 原始对话上下文；
            # - 帮助最终报告模型理解用户真实意图和额外约束。
            #
            # findings：
            # - 前面研究阶段产生的核心材料。
            #
            # date：
            # - 帮助模型处理“当前、最新、最近”等时间敏感问题。
            final_report_prompt = final_report_generation_prompt.format(
                research_brief=state.get("research_brief", ""),
                messages=get_buffer_string(state.get("messages", [])),
                findings=findings,
                date=get_today_str(),
            )

            # 调用最终报告模型生成报告。
            #
            # 这里每次循环都会重新基于当前 findings 构造 prompt。
            # 如果前一次因为 token limit 失败，
            # findings 会被截断，然后再次尝试。
            final_report = await configurable_model.with_config(
                writer_model_config
            ).ainvoke([
                HumanMessage(content=final_report_prompt)
            ])

            # 如果生成成功，则返回最终报告。
            #
            # final_report：
            # - 存入专门字段，供外部读取；
            #
            # messages：
            # - 把最终报告作为 AIMessage 写入对话消息；
            #
            # **cleared_state：
            # - 清空 notes，避免中间研究材料继续堆积。
            return {
                "final_report": final_report.content,
                "messages": [final_report],
                **cleared_state,
            }

        except Exception as e:
            # =========================
            # Step 4：处理 token limit 错误
            # =========================

            # 如果错误是 token limit exceeded，
            # 说明 final_report_prompt 太长，通常是 findings 太大导致。
            if is_token_limit_exceeded(e, configurable.final_report_model):
                # 记录一次 token limit 重试。
                current_retry += 1

                # 第一次重试时，需要先估算模型上下文上限。
                if current_retry == 1:
                    # 获取当前 final_report_model 的上下文 token 上限。
                    #
                    # get_model_token_limit(...) 通常依赖项目内部维护的模型上下文窗口映射表。
                    model_token_limit = get_model_token_limit(
                        configurable.final_report_model
                    )

                    # 如果无法确定模型上下文长度，则无法安全截断。
                    #
                    # 这里直接返回错误信息，并提示用户更新 deep_researcher/utils.py 中的模型映射。
                    if not model_token_limit:
                        return {
                            "final_report": (
                                "Error generating final report: Token limit exceeded, "
                                "however, we could not determine the model's maximum "
                                "context length. Please update the model map in "
                                "deep_researcher/utils.py with this information. "
                                f"{e}"
                            ),
                            "messages": [
                                AIMessage(
                                    content="Report generation failed due to token limits"
                                )
                            ],
                            **cleared_state,
                        }

                    # 用 token 上限的 4 倍作为字符截断近似。
                    #
                    # 这是一个粗略估算：
                    # - 英文中 1 token 大约 3~4 个字符；
                    # - 中文、代码、URL、表格则不一定准确。
                    #
                    # 这里不是精确 token 计算，而是工程兜底。
                    findings_token_limit = model_token_limit * 4

                else:
                    # 第二次及之后重试，每次在上一次基础上再减少 10%。
                    #
                    # 这样可以逐步降低 prompt 长度，
                    # 避免一次性截断过多导致信息损失太大。
                    findings_token_limit = int(findings_token_limit * 0.9)

                # 截断 findings，并继续下一轮生成尝试。
                #
                # 注意：
                # 这里只截断 findings，不截断 messages 或 research_brief。
                # 因为 findings 通常是 prompt 中最大的一部分。
                findings = findings[:findings_token_limit]
                continue

            else:
                # =========================
                # Step 5：处理非 token limit 错误
                # =========================

                # 如果不是 token limit 错误，则不继续重试，直接返回错误。
                #
                # 例如：
                # - API key 错误；
                # - 模型名称错误；
                # - 网络异常；
                # - 服务端错误；
                # - prompt 格式错误。
                return {
                    "final_report": f"Error generating final report: {e}",
                    "messages": [
                        AIMessage(
                            content="Report generation failed due to an error"
                        )
                    ],
                    **cleared_state,
                }

    # =========================
    # Step 6：超过最大重试次数后的兜底返回
    # =========================

    # 如果多次 token limit 截断后仍然无法成功生成报告，
    # 则返回最大重试次数耗尽的错误结果。
    return {
        "final_report": "Error generating final report: Maximum retries exceeded",
        "messages": [
            AIMessage(
                content="Report generation failed after maximum retries"
            )
        ],
        **cleared_state,
    }





# =========================
# 整体注释解析：构建完整 Deep Researcher 主图
# =========================
# 这段代码创建从“用户输入”到“最终报告”的完整 Deep Research 工作流。
#
# 主图包含四个阶段：
# 1. clarify_with_user：
#    判断用户请求是否需要澄清。
#
# 2. write_research_brief：
#    把用户原始消息转换成结构化 research_brief。
#
# 3. research_supervisor：
#    调用 supervisor_subgraph 执行研究规划、任务委派、并发子研究和结果汇总。
#
# 4. final_report_generation：
#    根据 research_brief、notes 和原始 messages 生成最终报告。
#
# 这个主图是系统最高层 orchestration，
# 负责串联澄清、规划、研究和报告生成四个核心环节。
deep_researcher_builder = StateGraph(
    AgentState,
    input=AgentInputState,
    config_schema=Configuration,
)


# =========================
# 整体注释解析：注册主图节点
# =========================
# clarify_with_user：
# - 用户澄清节点；
# - 如果问题不清楚，可能直接返回澄清问题并结束；
# - 如果问题清楚，则进入 write_research_brief。
#
# write_research_brief：
# - 研究简报生成节点；
# - 将用户消息规约成 research_brief；
# - 初始化 Supervisor 上下文。
#
# research_supervisor：
# - 研究执行节点；
# - 实际上是 supervisor_subgraph；
# - 内部会调度 researcher_subgraph 执行并发研究。
#
# final_report_generation：
# - 最终报告生成节点；
# - 将研究发现综合成最终报告。
deep_researcher_builder.add_node("clarify_with_user", clarify_with_user)
deep_researcher_builder.add_node("write_research_brief", write_research_brief)
deep_researcher_builder.add_node("research_supervisor", supervisor_subgraph)
deep_researcher_builder.add_node("final_report_generation", final_report_generation)


# =========================
# 整体注释解析：定义主图静态边
# =========================
# START → clarify_with_user：
# - 主图入口，从澄清阶段开始。
#
# research_supervisor → final_report_generation：
# - 研究完成后进入最终报告生成。
#
# final_report_generation → END：
# - 最终报告生成后，整个流程结束。
#
# 注意：
# clarify_with_user 到 write_research_brief，
# write_research_brief 到 research_supervisor，
# 是由节点内部 Command(goto=...) 动态路由控制的。
deep_researcher_builder.add_edge(START, "clarify_with_user")
deep_researcher_builder.add_edge("research_supervisor", "final_report_generation")
deep_researcher_builder.add_edge("final_report_generation", END)


# =========================
# 整体注释解析：编译完整 Deep Researcher 工作流
# =========================
# compile() 会把主图编译成可执行对象。
#
# deep_researcher 就是完整 Agent 的入口，
# 外部可以通过 invoke / ainvoke / stream / astream 调用它。
deep_researcher = deep_researcher_builder.compile()