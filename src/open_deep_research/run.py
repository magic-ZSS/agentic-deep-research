"""Simple IDE-friendly runner for the Deep Research graph.

这是一个面向 IDE / 本地命令行运行的 Deep Research graph 简易入口。

核心作用：
1. 从命令行参数或 QUESTION 常量中读取研究问题；
2. 加载项目根目录下的 .env 配置；
3. 构造 LangChain HumanMessage；
4. 调用 open_deep_research 中定义好的 deep_researcher graph；
5. 打印最终研究报告或兜底输出。
"""

# 运行方式：
# python src/open_deep_research/run.py "你的研究问题"
#
# 如果命令行没有传入问题，则使用下方 QUESTION 中写死的默认问题。


# Python 标准库：异步事件循环支持。
# deep_researcher 使用的是 ainvoke(...) 异步调用接口，
# 因此需要用 asyncio.run(main()) 启动异步主函数。
import asyncio

# Python 标准库：系统相关功能。
# 这里主要用到两个能力：
# 1. sys.argv：读取命令行传入的问题；
# 2. sys.path：临时修改 Python 模块搜索路径，确保可以导入 src 下的项目源码。
import sys

# Python 标准库：面向对象的路径处理工具。
# 相比字符串拼接路径，Path 更适合跨平台处理 Windows / Linux / macOS 路径。
# 这里用于：
# 1. 获取当前文件路径；
# 2. 推导项目根目录；
# 3. 定位 .env 文件；
# 4. 定位 src 目录。
from pathlib import Path

# Python 标准库 typing：通用类型标注。
# Any 表示“任意类型”。
# 这里用于兼容 LangChain Message 对象、dict 消息、以及 graph 返回的复杂状态结构。
from typing import Any

# python-dotenv 第三方库：加载 .env 文件中的环境变量。
# Deep Research graph 往往依赖 API Key、模型配置、搜索服务配置等环境变量。
# load_dotenv(PROJECT_ROOT / ".env") 会把项目根目录下的 .env 注入到当前进程环境中。
from dotenv import load_dotenv

# LangChain 核心消息类型。
# HumanMessage 用来表示“用户输入的一条消息”。
# deep_researcher graph 的入口状态是 messages，因此需要把普通字符串问题包装成 HumanMessage。
from langchain_core.messages import HumanMessage


# 默认研究问题。
# 适合在 IDE 中直接修改此变量，然后点击运行当前文件。
# 如果命令行传入了参数，则命令行参数优先级更高。
QUESTION = '''请基于 2–3 个官方或权威来源，简要说明 MCP 是什么、解决什么问题、核心组件有哪些，以及它适合哪些 Agent 场景。

限制：

1. 最多使用 3 个来源；
2. 最终回答不超过 600 字；
3. 用表格为主；
4. 不要扩展到无关主题；
5. 无法确认的内容写“未公开”。
'''


# 定位项目根目录。
# 当前文件路径假设为：
#   <project_root>/src/open_deep_research/run.py
#
# Path(__file__).resolve() 获取当前文件的绝对路径；
# parents[0] 是当前文件所在目录 open_deep_research；
# parents[1] 是 src；
# parents[2] 是项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[2]


# 将项目的 src 目录加入 Python 模块搜索路径。
# 这样即使不安装当前项目包，也可以直接从源码目录导入 open_deep_research。
#
# 这对 IDE 直接运行很友好，因为 IDE 运行单个脚本时，
# Python 不一定自动把项目 src 目录加入 sys.path。
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))


# 由于上面需要先动态修改 sys.path，再导入项目内部模块，
# 所以这里的 import 必须放在 sys.path 修改之后。
# noqa: E402 用于告诉 Ruff / Flake8：
# “这里的 import 不在文件顶部是有意为之，不要报错。”
from open_deep_research.deep_researcher import deep_researcher  # noqa: E402


def get_question() -> str:
    """从命令行参数或 QUESTION 常量中获取研究问题。

    优先级：
    1. 命令行传入的问题；
    2. 文件中写死的 QUESTION；
    3. 两者都没有时抛出错误。

    Returns:
        str: 最终要发送给 Deep Research graph 的研究问题。

    Raises:
        ValueError: 当命令行和 QUESTION 都没有提供有效问题时抛出。
    """

    # sys.argv[0] 是当前脚本路径；
    # sys.argv[1:] 才是用户在命令行中额外输入的参数。
    #
    # 例如：
    # python run.py "什么是 MCP"
    #
    # sys.argv[1:] 大致为：
    # ["什么是 MCP"]
    command_line_question = " ".join(sys.argv[1:]).strip()

    # 如果用户通过命令行传入了问题，则优先使用命令行版本。
    # 这样不需要每次修改源码中的 QUESTION。
    if command_line_question:
        return command_line_question

    # 如果命令行没有传入问题，则使用文件内的默认 QUESTION。
    if QUESTION.strip():
        return QUESTION.strip()

    # 如果两种来源都没有有效问题，则明确报错，提示用户如何使用。
    raise ValueError(
        'Set QUESTION in run.py or pass a question, for example: python src/open_deep_research/run.py "your question"'
    )


def get_message_content(message: Any) -> str:
    """从 LangChain message 或 message-like dict 中提取可读文本内容。

    Deep Research graph 返回的 messages 可能是：
    1. LangChain Message 对象，例如 AIMessage / HumanMessage；
    2. 类似 {"content": "..."} 的字典。

    这个函数用于兼容两种结构，统一取出 content 字段。

    Args:
        message: 一个 LangChain 消息对象，或包含 content 字段的字典。

    Returns:
        str: 消息中的文本内容。如果没有 content，则返回空字符串。
    """

    # 兼容 dict 格式消息。
    if isinstance(message, dict):
        return str(message.get("content", ""))

    # 兼容 LangChain Message 对象。
    # getattr(message, "content", "") 表示：
    # 如果 message 有 content 属性，就取出来；
    # 如果没有，就返回空字符串。
    return str(getattr(message, "content", ""))


def print_result(final_state: dict[str, Any]) -> None:
    """打印 Deep Research graph 的最终输出。

    优先打印 final_report。
    如果没有 final_report，则回退打印 messages 中的最后一条消息。
    如果连 messages 也没有，则打印完整 final_state，方便调试。

    Args:
        final_state: deep_researcher.ainvoke(...) 返回的最终状态字典。
    """

    # 最理想情况：graph 直接返回了 final_report 字段。
    # 这通常代表最终研究报告已经被整理好。
    final_report = final_state.get("final_report")
    if final_report:
        sys.stdout.write(str(final_report).rstrip() + "\n")
        return

    # 兜底情况一：
    # 如果没有 final_report，但有 messages，
    # 就打印最后一条消息，通常它也可能包含最终回答。
    messages = final_state.get("messages") or []
    if messages:
        sys.stdout.write(get_message_content(messages[-1]).rstrip() + "\n")
        return

    # 兜底情况二：
    # 如果既没有 final_report，也没有 messages，
    # 直接打印 final_state，帮助开发者观察 graph 实际返回结构。
    sys.stdout.write(str(final_state).rstrip() + "\n")


async def main() -> None:
    """运行 Deep Research graph。

    执行步骤：
    1. 从项目根目录加载 .env 环境变量；
    2. 获取研究问题；
    3. 将问题包装成 HumanMessage；
    4. 异步调用 deep_researcher graph；
    5. 打印最终结果。
    """

    # 加载项目根目录下的 .env 文件。
    # 里面通常存放 API Key、搜索服务配置、模型配置等环境变量。
    load_dotenv(PROJECT_ROOT / ".env")

    # 调用 Deep Research graph。
    #
    # 输入状态格式为：
    # {
    #     "messages": [HumanMessage(content=...)]
    # }
    #
    # 这说明该 graph 以对话消息作为入口，
    # HumanMessage 表示用户提出的原始研究问题。
    final_state = await deep_researcher.ainvoke(
        {"messages": [HumanMessage(content=get_question())]}
    )

    # 从 graph 的最终状态中提取并打印结果。
    print_result(final_state)


# Python 脚本入口。
# 只有当该文件被直接运行时，才会执行 main()。
# 如果该文件被其他模块 import，则不会自动运行。
if __name__ == "__main__":

    # asyncio.run(...) 用于启动异步事件循环，
    # 并运行 async main()。
    #
    # 这适合普通命令行或 IDE 直接运行。
    # 但如果在 Jupyter Notebook 中运行，可能会遇到已有事件循环的问题。
    asyncio.run(main())