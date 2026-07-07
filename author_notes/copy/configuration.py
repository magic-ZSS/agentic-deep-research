"""
整体注释解析：Open Deep Research 系统配置管理模块

这个模块负责集中管理 Deep Research Agent 的运行配置。

主要包含四类配置：
1. 搜索配置：选择使用 Tavily、OpenAI、Anthropic，或不启用搜索；
2. MCP 配置：配置 MCP server 地址、可用工具和鉴权要求；
3. Agent 运行控制配置：澄清、并发、研究迭代次数、工具调用次数；
4. 模型配置：摘要模型、研究模型、压缩模型、最终报告模型及其 token 上限。

核心设计思想：
- 不在业务逻辑中硬编码模型和参数；
- 通过 Configuration 统一管理所有运行参数；
- 支持从环境变量和 RunnableConfig 动态读取配置；
- 便于本地运行、LangGraph 部署、Open Agent Platform UI 配置和实验调参。
"""

import os
from enum import Enum
from typing import Any, List, Optional

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field


class SearchAPI(Enum):
    """
    整体注释解析：搜索 API 类型枚举

    这个枚举类定义 Deep Research 系统支持的搜索方式。

    使用枚举的好处：
    1. 限制 search_api 只能取合法值；
    2. 避免代码中到处写裸字符串；
    3. 方便后续根据不同搜索类型选择不同工具链。
    """

    # 使用 Anthropic 模型原生搜索能力。
    ANTHROPIC = "anthropic"

    # 使用 OpenAI 模型原生 Web Search 能力。
    OPENAI = "openai"

    # 使用 Tavily 第三方搜索 API。
    TAVILY = "tavily"

    # 不启用搜索能力。
    NONE = "none"


class MCPConfig(BaseModel):
    """
    整体注释解析：MCP Server 配置类

    这个类用于描述 Model Context Protocol，也就是 MCP server 的连接配置。

    MCP 的作用是给 Agent 提供外部工具或上下文能力，例如：
    - 文件读取；
    - 数据库查询；
    - 企业内部工具；
    - 自定义搜索工具；
    - 浏览器或代码执行工具。

    字段说明：
    - url：MCP server 地址；
    - tools：允许暴露给 LLM 的工具列表；
    - auth_required：该 MCP server 是否需要认证。
    """

    # MCP server 的 URL。
    # 如果为 None，通常表示不连接 MCP server。
    url: Optional[str] = Field(
        default=None,
        optional=True,
    )
    """The URL of the MCP server"""

    # 允许暴露给 LLM 使用的 MCP 工具列表。
    #
    # 例如：
    # tools=["read_file", "query_database", "search_docs"]
    #
    # 如果为 None，具体含义取决于后续 MCP 加载逻辑：
    # 可能表示不限制，也可能表示不启用。
    tools: Optional[List[str]] = Field(
        default=None,
        optional=True,
    )
    """The tools to make available to the LLM"""

    # MCP server 是否需要认证。
    # 默认 False，表示不需要额外认证。
    auth_required: Optional[bool] = Field(
        default=False,
        optional=True,
    )
    """Whether the MCP server requires authentication"""


class Configuration(BaseModel):
    """
    整体注释解析：Deep Research Agent 主配置类

    这是整个系统最核心的配置对象。

    它集中控制：
    1. Agent 是否可以追问用户；
    2. Research Supervisor 最多迭代多少轮；
    3. 每个 researcher 最多调用多少次工具；
    4. 并发运行多少个研究单元；
    5. 每个阶段分别使用什么模型；
    6. 每个模型最大输出 token 数；
    7. 是否接入 MCP server。

    在 graph 节点中，通常会这样使用：

        configurable = Configuration.from_runnable_config(config)

    然后通过：

        configurable.research_model
        configurable.search_api
        configurable.max_react_tool_calls

    来决定当前节点的运行行为。
    """

    # =========================
    # General Configuration
    # 整体注释解析：通用运行配置
    # =========================
    # 这一组参数控制 Agent 的基础运行行为，
    # 包括结构化输出稳定性、是否允许用户澄清、并发研究数量。

    # 结构化输出最大重试次数。
    #
    # 用在 with_structured_output(...) 场景中。
    # 如果模型没有返回合法 schema，会自动重试。
    max_structured_output_retries: int = Field(
        default=3,
        metadata={
            "x_oap_ui_config": {
                "type": "number",
                "default": 3,
                "min": 1,
                "max": 10,
                "description": "Maximum number of retries for structured output calls from models"
            }
        }
    )

    # 是否允许 Agent 在正式研究前向用户提澄清问题。
    #
    # True：问题不清楚时可以追问；
    # False：直接跳过澄清，进入研究流程。
    allow_clarification: bool = Field(
        default=True,
        metadata={
            "x_oap_ui_config": {
                "type": "boolean",
                "default": True,
                "description": "Whether to allow the researcher to ask the user clarifying questions before starting research"
            }
        }
    )

    # 最大并发研究单元数量。
    #
    # 多智能体研究中，Supervisor 可能会拆出多个 sub-agent 并行研究。
    # 这个值越大，速度可能越快，但也更容易触发 API rate limit。
    max_concurrent_research_units: int = Field(
        default=5,
        metadata={
            "x_oap_ui_config": {
                "type": "slider",
                "default": 5,
                "min": 1,
                "max": 20,
                "step": 1,
                "description": "Maximum number of research units to run concurrently. This will allow the researcher to use multiple sub-agents to conduct research. Note: with more concurrency, you may run into rate limits."
            }
        }
    )

    # =========================
    # Research Configuration
    # 整体注释解析：研究过程配置
    # =========================
    # 这一组参数控制研究阶段的搜索方式、Supervisor 反思次数、
    # 以及单个 researcher 的 ReAct 工具调用上限。

    # 研究阶段使用的搜索 API。
    #
    # 默认 Tavily。
    # 注意：如果选择 OpenAI / Anthropic 原生搜索，
    # 需要确保对应 research_model 支持该搜索能力。
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

    # Research Supervisor 最大研究迭代次数。
    #
    # 一次 iteration 通常表示：
    # Supervisor 检查当前研究结果，发现不足，再提出后续研究任务。
    #
    # 值越大，研究更充分，但 token 成本也更高。
    max_researcher_iterations: int = Field(
        default=6,
        metadata={
            "x_oap_ui_config": {
                "type": "slider",
                "default": 6,
                "min": 1,
                "max": 10,
                "step": 1,
                "description": "Maximum number of research iterations for the Research Supervisor. This is the number of times the Research Supervisor will reflect on the research and ask follow-up questions."
            }
        }
    )

    # 单个 researcher step 中最多允许多少次工具调用。
    #
    # ReAct 模式大致是：
    # 思考 → 调用工具 → 观察结果 → 再思考 → 再调用工具。
    #
    # 这个值限制单个研究任务的探索深度。
    max_react_tool_calls: int = Field(
        default=10,
        metadata={
            "x_oap_ui_config": {
                "type": "slider",
                "default": 10,
                "min": 1,
                "max": 30,
                "step": 1,
                "description": "Maximum number of tool calling iterations to make in a single researcher step."
            }
        }
    )

    # =========================
    # Model Configuration
    # 整体注释解析：多阶段模型配置
    # =========================
    # Deep Research 不是所有阶段都必须用同一个模型。
    #
    # 这里把模型拆成四类：
    # 1. summarization_model：搜索结果摘要；
    # 2. research_model：执行研究与工具调用；
    # 3. compression_model：压缩多个子研究结果；
    # 4. final_report_model：生成最终报告。
    #
    # 这种拆分可以让系统在质量、成本和速度之间做更细粒度的权衡。

    # 搜索结果摘要模型。
    #
    # 主要用于把 Tavily 返回的长网页内容压缩成可供 researcher 使用的摘要。
    summarization_model: str = Field(
        default="openai:gpt-4.1-mini",
        metadata={
            "x_oap_ui_config": {
                "type": "text",
                "default": "openai:gpt-4.1-mini",
                "description": "Model for summarizing research results from Tavily search results"
            }
        }
    )

    # 摘要模型最大输出 token 数。
    summarization_model_max_tokens: int = Field(
        default=8192,
        metadata={
            "x_oap_ui_config": {
                "type": "number",
                "default": 8192,
                "description": "Maximum output tokens for summarization model"
            }
        }
    )

    # 单个网页内容在摘要前允许保留的最大字符数。
    #
    # 注意这里是字符数，不是 token 数。
    # 作用是避免网页内容过长导致上下文爆炸。
    max_content_length: int = Field(
        default=50000,
        metadata={
            "x_oap_ui_config": {
                "type": "number",
                "default": 50000,
                "min": 1000,
                "max": 200000,
                "description": "Maximum character length for webpage content before summarization"
            }
        }
    )

    # 研究模型。
    #
    # 这是 researcher / supervisor 阶段最核心的模型。
    # 它通常负责理解任务、制定搜索策略、调用工具、综合中间结论。
    research_model: str = Field(
        default="openai:gpt-4.1",
        metadata={
            "x_oap_ui_config": {
                "type": "text",
                "default": "openai:gpt-4.1",
                "description": "Model for conducting research. NOTE: Make sure your Researcher Model supports the selected search API."
            }
        }
    )

    # 研究模型最大输出 token 数。
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

    # 压缩模型。
    #
    # 多个 sub-agent 完成研究后，可能产生大量 findings。
    # compression_model 用于压缩、去重、合并和保留关键证据。
    compression_model: str = Field(
        default="openai:gpt-4.1",
        metadata={
            "x_oap_ui_config": {
                "type": "text",
                "default": "openai:gpt-4.1",
                "description": "Model for compressing research findings from sub-agents. NOTE: Make sure your Compression Model supports the selected search API."
            }
        }
    )

    # 压缩模型最大输出 token 数。
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

    # 最终报告模型。
    #
    # 用于把所有研究发现整合成最终报告。
    # 这个模型通常决定最终输出的结构、表达、综合质量和可读性。
    final_report_model: str = Field(
        default="openai:gpt-4.1",
        metadata={
            "x_oap_ui_config": {
                "type": "text",
                "default": "openai:gpt-4.1",
                "description": "Model for writing the final report from all research findings"
            }
        }
    )

    # 最终报告最大输出 token 数。
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

    # =========================
    # MCP server configuration
    # 整体注释解析：MCP 服务配置
    # =========================
    # 这一组配置控制 Agent 是否接入 MCP server，
    # 以及接入后如何向 Agent 说明可用工具。

    # MCP server 配置对象。
    #
    # None 表示不启用 MCP。
    # MCPConfig 表示启用指定 MCP server。
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

    # 传给 Agent 的 MCP 工具使用说明。
    #
    # 例如可以告诉 Agent：
    # - 哪些工具优先使用；
    # - 哪些工具只在特定场景下使用；
    # - 工具调用前要不要先规划；
    # - 工具返回结果应该如何解释。
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

    @classmethod
    def from_runnable_config(
        cls, config: Optional[RunnableConfig] = None
    ) -> "Configuration":
        """
        整体注释解析：从 RunnableConfig 构造 Configuration 实例

        这个类方法负责把 LangGraph / LangChain 运行时传入的 RunnableConfig，
        转换成当前项目使用的 Configuration 配置对象。

        核心流程：
        1. 从 config 中取出 "configurable" 字典；
        2. 获取 Configuration 类中定义的所有字段名；
        3. 对每个字段，优先读取同名大写环境变量；
        4. 如果环境变量不存在，再读取 configurable 中的同名字段；
        5. 过滤掉值为 None 的字段；
        6. 用剩余字段创建 Configuration 实例。

        配置优先级：
        环境变量 > RunnableConfig["configurable"] > Field 默认值

        这个方法的价值：
        - 统一配置入口；
        - 支持环境变量覆盖；
        - 支持 graph 运行时动态注入配置；
        - 避免各个节点重复解析配置。
        """

        # 如果传入了 config，则取出其中的 "configurable" 字段。
        # 如果 config 为 None，则使用空字典，避免后续报错。
        configurable = config.get("configurable", {}) if config else {}

        # 获取 Configuration 类中所有字段名。
        #
        # cls.model_fields 来自 Pydantic 模型字段信息。
        # 这里会拿到所有配置项名称，例如：
        # research_model、search_api、allow_clarification 等。
        field_names = list(cls.model_fields.keys())

        # 构造字段值字典。
        #
        # 对每一个字段 field_name：
        # 1. 先读取环境变量 field_name.upper()；
        #    例如 research_model -> RESEARCH_MODEL；
        # 2. 如果环境变量不存在，再读取 configurable[field_name]；
        # 3. 如果两者都没有，则得到 None。
        #
        # 注意：
        # 环境变量读取出来一定是字符串，
        # 后续类型转换依赖 Pydantic。
        values: dict[str, Any] = {
            field_name: os.environ.get(field_name.upper(), configurable.get(field_name))
            for field_name in field_names
        }

        # 过滤掉值为 None 的字段，然后创建 Configuration 实例。
        #
        # 过滤 None 的原因：
        # 如果某个字段没有被显式配置，就不要把 None 传进去覆盖默认值，
        # 而是让 Field(default=...) 自动生效。
        return cls(**{k: v for k, v in values.items() if v is not None})

    class Config:
        """
        整体注释解析：Pydantic 模型行为配置

        这个内部类用于调整 Pydantic BaseModel 的行为。

        arbitrary_types_allowed = True 表示：
        允许字段中出现 Pydantic 默认无法严格识别的任意 Python 类型。

        在 LangChain / LangGraph 生态中，有时会涉及一些复杂对象，
        这个设置可以提高模型兼容性。
        """

        arbitrary_types_allowed = True