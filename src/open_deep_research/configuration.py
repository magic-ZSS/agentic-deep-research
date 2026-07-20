"""Configuration management for the Open Deep Research system."""

import os
from enum import Enum
from typing import Any, Literal, Optional

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field, model_validator

from open_deep_research.mcp.config import MCPConfig, MCPServerConfig


class SearchAPI(Enum):
    """Enumeration of available search API providers."""
    
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    TAVILY = "tavily"
    NONE = "none"

class Configuration(BaseModel):
    """Main configuration class for the Deep Research agent."""
    
    # General Configuration

    # 配置结构化输出失败后的最大重试次数。
    max_structured_output_retries: int = Field(
        default=1,
        metadata={
            "x_oap_ui_config": {
                "type": "number",
                "default": 1,
                "min": 1,
                "max": 10,
                "description": "Maximum number of retries for structured output calls from models"
            }
        }
    )

    # 配置是否允许先向用户澄清问题。
    allow_clarification: bool = Field(
        default=False,
        metadata={
            "x_oap_ui_config": {
                "type": "boolean",
                "default": True,
                "description": "Whether to allow the researcher to ask the user clarifying questions before starting research"
            }
        }
    )

    # 配置是否打印简要运行流程信息。
    print_process_info: bool = Field(
        default=True,
        metadata={
            "x_oap_ui_config": {
                "type": "boolean",
                "default": False,
                "description": "Whether to print concise process trace information during a research run."
            }
        }
    )

    # Structured evidence storage remains disconnected from the research graph
    # until a later phase explicitly enables that integration.
    enable_structured_evidence: bool = False
    knowledge_repository_backend: Literal["memory", "sqlite"] = "sqlite"
    knowledge_tenant_id: Optional[str] = None
    knowledge_project_id: Optional[str] = None
    knowledge_db_path: str = "data/knowledge/knowledge.db"
    knowledge_blob_dir: str = "data/knowledge/blobs"
    sqlite_busy_timeout_ms: int = Field(default=5000, ge=1, le=120000)

    # Phase 2 knowledge ingestion/retrieval remains opt-in and is deliberately
    # disconnected from the production Researcher tool set. Candidate visibility
    # is granted by a trusted inspection context, never by model configuration.
    enable_knowledge_base: bool = False
    enable_paperqa_retrieval: bool = False
    knowledge_import_roots: tuple[str, ...] = ()
    knowledge_import_staging: str = "data/knowledge/import"
    paperqa_index_dir: str = "data/knowledge/paperqa-index"
    knowledge_search_visibility: Literal["active_only"] = "active_only"
    knowledge_search_limit: int = Field(default=8, ge=1, le=50)
    knowledge_chunk_size_chars: int = Field(default=4000, ge=256, le=20000)
    knowledge_chunk_overlap_chars: int = Field(default=200, ge=0, le=4000)
    paperqa_contextual_summarization: bool = False
    paperqa_evidence_k: int = Field(default=8, ge=1, le=50)
    paperqa_contextual_max_concurrency: int = Field(default=2, ge=1, le=8)
    paperqa_contextual_timeout_seconds: float = Field(default=30.0, ge=1, le=300)
    paperqa_contextual_token_limit: int = Field(default=4000, ge=256, le=20000)

    # Phase 3 governance modes are mutually exclusive at the tool-routing layer.
    # Defaults deliberately preserve the Phase 0/2 production path.
    enable_knowledge_tools: bool = False
    enable_agentic_rag: bool = False
    enable_knowledge_writeback: bool = False
    agentic_web_provider: Literal["tavily"] = "tavily"
    run_evidence_store_backend: Literal["memory", "sqlite"] = "memory"
    run_evidence_db_path: str = "data/run-evidence/run-evidence.db"
    run_evidence_ttl_seconds: int = Field(default=86400, ge=60, le=2592000)
    requirement_extraction_model: Optional[str] = None
    requirement_completion_policy_version: str = "phase3-completion-v1"
    knowledge_lifecycle_policy_version: str = "phase3-lifecycle-v1"
    knowledge_coverage_threshold: float = Field(default=1.0, ge=0, le=1)
    min_direct_evidence: int = Field(default=1, ge=1, le=10)
    min_source_authority: Literal[
        "unknown", "self_reported", "secondary", "primary", "official"
    ] = "secondary"
    max_evidence_age_days: Optional[int] = Field(default=None, ge=1, le=36500)
    candidate_min_content_chars: int = Field(default=40, ge=1, le=10000)
    candidate_min_confidence: float = Field(default=0.7, ge=0, le=1)
    max_web_queries_per_run: int = Field(default=5, ge=0, le=50)
    max_web_results_per_query: int = Field(default=3, ge=1, le=20)
    max_web_results_per_run: int = Field(default=15, ge=1, le=200)
    max_concurrent_web_requests: int = Field(default=2, ge=1, le=20)

    # 配置同时运行的研究子任务数量。
    max_concurrent_research_units: int = Field(
        default=3,
        metadata={
            "x_oap_ui_config": {
                "type": "slider",
                "default": 3,
                "min": 1,
                "max": 20,
                "step": 1,
                "description": "Maximum number of research units to run concurrently. This will allow the researcher to use multiple sub-agents to conduct research. Note: with more concurrency, you may run into rate limits."
            }
        }
    )
    # Research Configuration

    # 配置单个 researcher 每轮并发工具调用数。
    max_concurrent_researcher_tool_calls: int = Field(
        default=3,
        ge=1,
        le=10,
        metadata={
            "x_oap_ui_config": {
                "type": "slider",
                "default": 3,
                "min": 1,
                "max": 10,
                "step": 1,
                "description": "Maximum number of tool calls a single researcher may execute concurrently in one tool-calling round."
            }
        }
    )

    # 配置研究流程使用的搜索后端。
    search_api: SearchAPI = Field(
        default=SearchAPI.TAVILY,
        metadata={
            "x_oap_ui_config": {
                "type": "select",
                "default": "tavily",
                "description": "Search API to use for research. NOTE: Make sure your Researcher Model supports the selected search API.",
                "options": [
                    {"label": "Tavily", "value": SearchAPI.TAVILY.value},
                    {"label": "OpenAI Native Web Search", "value": SearchAPI.OPENAI.value},
                    {"label": "Anthropic Native Web Search", "value": SearchAPI.ANTHROPIC.value},
                    {"label": "None", "value": SearchAPI.NONE.value}
                ]
            }
        }
    )

    # 配置单次 Tavily 搜索调用允许的 query 数。
    max_queries_per_search_call: int = Field(
        default=3,
        ge=1,
        le=10,
        metadata={
            "x_oap_ui_config": {
                "type": "slider",
                "default": 3,
                "min": 1,
                "max": 10,
                "step": 1,
                "description": "Maximum number of Tavily queries in one search tool call. Also limits concurrent webpage summarization tasks for that search call."
            }
        }
    )

    # 配置 Tavily 每个 query 返回的结果数。
    max_results_per_tavily: int = Field(
        default=3,
        ge=1,
        le=10,
        metadata={
            "x_oap_ui_config": {
                "type": "slider",
                "default": 3,
                "min": 1,
                "max": 10,
                "step": 1,
                "description": "Maximum number of results returned by Tavily for each individual search query."
            }
        }
    )

    # 配置 supervisor 最大研究迭代次数。
    max_researcher_iterations: int = Field(
        default=5,
        metadata={
            "x_oap_ui_config": {
                "type": "slider",
                "default": 5,
                "min": 1,
                "max": 10,
                "step": 1,
                "description": "Maximum number of research iterations for the Research Supervisor. This is the number of times the Research Supervisor will reflect on the research and ask follow-up questions."
            }
        }
    )

    # 配置 researcher 最大工具调用轮数。
    max_react_tool_calls: int = Field(
        default=5,
        metadata={
            "x_oap_ui_config": {
                "type": "slider",
                "default": 5,
                "min": 1,
                "max": 30,
                "step": 1,
                "description": "Maximum number of tool calling iterations to make in a single researcher step."
            }
        }
    )
    # Model Configuration

    # 配置网页摘要使用的模型。
    summarization_model: str = Field(
        default=os.getenv("SUMMARIZATION_MODEL"),
        metadata={
            "x_oap_ui_config": {
                "type": "text",
                "default": "openai:gpt-4.1-mini",
                "description": "Model for summarizing research results from Tavily search results"
            }
        }
    )

    # 配置网页摘要模型的最大输出 token。
    summarization_model_max_tokens: int = Field(
        default=4096,
        metadata={
            "x_oap_ui_config": {
                "type": "number",
                "default": 4096,
                "description": "Maximum output tokens for summarization model"
            }
        }
    )

    # 配置网页摘要前保留的最大正文长度。
    max_content_length: int = Field(
        default=20000,
        metadata={
            "x_oap_ui_config": {
                "type": "number",
                "default": 20000,
                "min": 1000,
                "max": 200000,
                "description": "Maximum character length for webpage content before summarization"
            }
        }
    )

    # 配置 researcher 使用的模型。
    research_model: str = Field(
        default=os.getenv("RESEARCH_MODEL"),
        metadata={
            "x_oap_ui_config": {
                "type": "text",
                "default": "openai:gpt-4.1",
                "description": "Model for conducting research. NOTE: Make sure your Researcher Model supports the selected search API."
            }
        }
    )

    # 配置 researcher 模型的最大输出 token。
    research_model_max_tokens: int = Field(
        default=10000,
        metadata={
            "x_oap_ui_config": {
                "type": "number",
                "default": 10000,
                "description": "Maximum output tokens for research model"
            }
        }
    )

    # 配置研究结果压缩使用的模型。
    compression_model: str = Field(
        default=os.getenv("COMPRESSION_MODEL"),
        metadata={
            "x_oap_ui_config": {
                "type": "text",
                "default": "openai:gpt-4.1",
                "description": "Model for compressing research findings from sub-agents. NOTE: Make sure your Compression Model supports the selected search API."
            }
        }
    )

    # 配置压缩模型的最大输出 token。
    compression_model_max_tokens: int = Field(
        default=8192,
        metadata={
            "x_oap_ui_config": {
                "type": "number",
                "default": 8192,
                "description": "Maximum output tokens for compression model"
            }
        }
    )
    compression_max_retries: int = Field(default=2, ge=1, le=10)

    # 配置最终报告生成使用的模型。
    final_report_model: str = Field(
        default=os.getenv("FINAL_REPORT_MODEL"),
        metadata={
            "x_oap_ui_config": {
                "type": "text",
                "default": "openai:gpt-4.1",
                "description": "Model for writing the final report from all research findings"
            }
        }
    )

    # 配置最终报告模型的最大输出 token。
    final_report_model_max_tokens: int = Field(
        default=10000,
        metadata={
            "x_oap_ui_config": {
                "type": "number",
                "default": 10000,
                "description": "Maximum output tokens for final report model"
            }
        }
    )
    # MCP server configuration

    # 配置 MCP 服务和工具列表。
    mcp_config: Optional[MCPConfig] = Field(
        default=None,
        optional=True,
        metadata={
            "x_oap_ui_config": {
                "type": "mcp",
                "description": "MCP server configuration"
            }
        }
    )
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    enable_filesystem_mcp: bool = False
    enable_knowledge_mcp: bool = False

    # 配置传给 MCP 工具的额外提示词。
    mcp_prompt: Optional[str] = Field(
        default=None,
        optional=True,
        metadata={
            "x_oap_ui_config": {
                "type": "text",
                "description": "Any additional instructions to pass along to the Agent regarding the MCP tools that are available to it."
            }
        }
    )

    @model_validator(mode="after")
    def validate_knowledge_chunking(self) -> "Configuration":
        """Reject overlap settings that cannot advance the chunk window."""
        if self.knowledge_chunk_overlap_chars >= self.knowledge_chunk_size_chars:
            raise ValueError(
                "knowledge_chunk_overlap_chars must be smaller than "
                "knowledge_chunk_size_chars"
            )
        if self.enable_knowledge_writeback and not self.enable_agentic_rag:
            raise ValueError(
                "enable_knowledge_writeback requires enable_agentic_rag"
            )
        if self.enable_agentic_rag and self.search_api in {
            SearchAPI.OPENAI,
            SearchAPI.ANTHROPIC,
        }:
            raise ValueError(
                "Agentic RAG cannot govern provider-native Web search; "
                "use Tavily or disable Web search"
            )
        if (
            self.run_evidence_store_backend == "sqlite"
            and os.path.normcase(os.path.abspath(self.run_evidence_db_path))
            == os.path.normcase(os.path.abspath(self.knowledge_db_path))
        ):
            raise ValueError(
                "run evidence SQLite storage must be isolated from the canonical "
                "knowledge database"
            )
        return self


    @classmethod
    def from_runnable_config(
        cls, config: Optional[RunnableConfig] = None
    ) -> "Configuration":
        """Create a Configuration instance from a RunnableConfig."""
        configurable = config.get("configurable", {}) if config else {}
        field_names = list(cls.model_fields.keys())
        values: dict[str, Any] = {
            field_name: os.environ.get(field_name.upper(), configurable.get(field_name))
            for field_name in field_names
        }
        return cls(**{k: v for k, v in values.items() if v is not None})

    class Config:
        """Pydantic configuration."""
        
        arbitrary_types_allowed = True
