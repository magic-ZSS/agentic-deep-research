"""Deep Research Agent 的图状态定义与数据结构。

本文件集中定义 Deep Research 系统中使用的：

1. 结构化输出模型；
2. 主 Agent 状态；
3. Supervisor 状态；
4. Researcher 状态；
5. 自定义 reducer。
"""

# operator.add 用于 LangGraph reducer，old_list + new_list
import operator

# Annotated 用于给字段类型附加 reducer 等额外元信息
# Optional 表示字段值可以是 None 或 str
from typing import Annotated, Optional

# MessageLikeRepresentation 是 LangChain 中的“消息类表示形式”，
# 它比 BaseMessage 更宽松，可以表示：
# - HumanMessage / AIMessage / ToolMessage 等消息对象；
# - 字符串；
# - 元组形式消息；
# - 字典形式消息。
from langchain_core.messages import MessageLikeRepresentation

# MessagesState 是 LangGraph 内置的消息状态基类。
# 它默认包含 messages 字段，并使用 add_messages 作为 reducer。
from langgraph.graph import MessagesState

# BaseModel 和 Field 用于定义 Pydantic 结构化输出模型
from pydantic import BaseModel, Field

# TypedDict 用于定义带类型注释的字典式状态结构
from typing_extensions import TypedDict


###################
# Structured Outputs
###################

class ConductResearch(BaseModel):
    """supervisor调用该工具, 启动一个子智能体对某个具体主题执行研究的结构化输出。"""

    # 子研究任务主题
    #
    # 要求：
    # - 只能是一个单一主题；
    # - 需要描述得足够详细；
    # - 至少是一段完整说明。
    #
    # 这样子研究智能体才能在独立上下文中理解：
    # 要研究什么、关注什么、输出什么。
    
    research_topic: str = Field(
        description=(
            "The topic to research. Should be a single topic, and should be described in high detail (at least a paragraph)."
        ),
    )


class ResearchComplete(BaseModel):
    """调用该工具，表示当前研究流程已经完成。"""

    # 该类没有字段。
    #
    # 在 Python 中，类体中只有 docstring 也是合法的，
    # 因此这里不需要显式写 pass。
    #
    # 它通常作为 Supervisor 的控制信号：
    # 当 Supervisor 判断研究资料已经足够时，调用该工具结束研究。
    

class Summary(BaseModel):
    """研究摘要结构，包含核心总结与关键摘录。"""

    # 压缩后的网页、文档或研究内容总结
    summary: str

    # 关键证据摘录
    #
    # 通常用于保留重要原文、关键事实或可引用片段。
    key_excerpts: str


class ClarifyWithUser(BaseModel):
    """用户澄清请求模型。"""

    # 是否需要向用户追问澄清问题
    need_clarification: bool = Field(
        description=(
            "Whether the user needs to be asked a clarifying question."
        ),
    )

    # 如果需要澄清，模型应该向用户提出的问题
    question: str = Field(
        description=(
            "A question to ask the user to clarify the report scope"
        ),
    )

    # 如果不需要澄清，向用户确认将开始研究的说明
    verification: str = Field(
        description=(
            "Verify message that we will start research after the user has provided the necessary information."
        ),
    )


class ResearchQuestion(BaseModel):
    """用于指导研究的问题和简报。"""

    # 研究简报
    #
    # 这是 Scope 阶段输出给 Supervisor 的核心任务说明。
    research_brief: str = Field(
        description=(
            "A research question that will be used to guide the research."
        ),
    )


class ResearcherOutputState(BaseModel):
    """单个研究子智能体的输出状态。"""

    # 子研究智能体最终压缩后的研究发现
    compressed_research: str

    # 子研究智能体的原始研究笔记
    raw_notes: Annotated[
        list[str],
        override_reducer,
    ] = []

    

###################
# State Definitions
###################

def override_reducer(
    current_value,
    new_value,
):
    """允许状态字段被覆盖的 reducer。

    默认情况下，该 reducer 会执行 operator.add，
    也就是将旧值和新值相加。

    但如果新值是如下形式：

    {
        "type": "override",
        "value": ...
    }

    那么它会直接用 value 覆盖旧值。

    这使得同一个字段既支持“追加累积”，也支持“强制覆盖”。

    Args:
        current_value: 当前状态中的旧值。
        new_value: 节点返回的新值。

    Returns:
        合并后的状态值。
    """

    # 如果新值显式声明为 override，则直接覆盖旧值
    if (
        isinstance(new_value, dict)
        and new_value.get("type") == "override"
    ):
        return new_value.get(
            "value",
            new_value,
        )

    # 否则使用 operator.add 进行追加式合并
    else:
        return operator.add(
            current_value,
            new_value,
        )


class AgentInputState(MessagesState):
    """完整 Agent 的输入状态。

    该输入状态只包含 MessagesState 默认提供的 messages 字段。

    典型输入形式：

    {
        "messages": [
            HumanMessage(content="请研究……")
        ]
    }
    """


class AgentState(MessagesState):
    """完整 Deep Research Agent 的主状态。

    该状态贯穿整个主工作流：

    clarify_with_user
    → write_research_brief
    → supervisor_subgraph
    → final_report_generation

    除了 MessagesState 自带的 messages 字段外，
    这里还增加了研究简报、Supervisor 消息、研究笔记和最终报告等字段。
    """

    # Supervisor 的消息历史
    #
    # 使用 override_reducer 的原因：
    # 既允许正常追加消息，也允许在需要时整体覆盖消息历史。
    supervisor_messages: Annotated[
        list[MessageLikeRepresentation],
        override_reducer,
    ]

    # 研究简报
    #
    # Optional[str] 表示字段值可以是字符串或 None。
    # 注意：Optional 不等于该 key 一定可以缺失；
    # 如果希望 key 本身可省略，需要使用 NotRequired。
    research_brief: Optional[str]

    # 子研究智能体返回的原始研究笔记
    #
    # 通常包含工具输出、搜索结果、原始材料等。
    raw_notes: Annotated[
        list[str],
        override_reducer,
    ] = []

    # 经过整理、压缩后适合最终报告生成的研究笔记
    notes: Annotated[
        list[str],
        override_reducer,
    ] = []

    # 最终研究报告
    final_report: str


class SupervisorState(TypedDict):
    """Supervisor 的状态。

    Supervisor 负责管理研究任务拆分、子智能体调度、
    研究结果聚合和研究完成判断。
    """

    # Supervisor 与模型之间的消息历史
    supervisor_messages: Annotated[
        list[MessageLikeRepresentation],
        override_reducer,
    ]

    # 总体研究简报
    #
    # Supervisor 根据该字段决定如何拆分研究任务。
    research_brief: str

    # Supervisor 聚合后的结构化研究笔记
    notes: Annotated[
        list[str],
        override_reducer,
    ] = []

    # Supervisor 已经进行的研究决策轮数
    #
    # 用于限制循环，防止不断委派新研究任务。
    research_iterations: int = 0

    # 子研究智能体返回的原始研究资料
    raw_notes: Annotated[
        list[str],
        override_reducer,
    ] = []


class ResearcherState(TypedDict):
    """单个研究子智能体的状态。

    每个 Researcher Agent 独立负责一个 research_topic，
    并在自己的上下文窗口中执行搜索、阅读、反思和压缩。
    """

    # 研究子智能体的消息历史
    #
    # 这里使用 operator.add，表示每次返回的新消息都会追加到旧消息后面。
    researcher_messages: Annotated[
        list[MessageLikeRepresentation],
        operator.add,
    ]

    # 工具调用轮数
    #
    # 用于限制单个研究子智能体的最大工具循环次数。
    tool_call_iterations: int = 0

    # 当前研究子智能体负责的具体研究主题
    research_topic: str

    # 压缩后的研究结果
    compressed_research: str

    # 原始研究笔记
    #
    # 使用 override_reducer，说明它既可以追加，也可以在需要时覆盖。
    raw_notes: Annotated[
        list[str],
        override_reducer,
    ] = []

