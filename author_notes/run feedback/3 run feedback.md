# my thought
1.TOKEN灾难级浪费！！
2.任务理解出现严重歧义，并未找我澄清
3.任务拆分过度，工具连续失败，但系统没有及时停止


# MCP MVP 测试用例运行轨迹评估报告

## 1. 测试目标

本次用例原本是一个 **MVP 正向测试**，目标是用较低 token 成本验证系统是否具备基础 Deep Research 能力：理解问题、检索权威来源、压缩信息、表格化输出，并遵守边界限制。

原始输入要求基于 2–3 个官方或权威来源，简要说明 MCP 是什么、解决什么问题、核心组件有哪些，以及适合哪些 Agent 场景；同时限制最多 3 个来源、最终不超过 600 字、表格为主、不扩展无关主题。

## 2. 总体结论

本次测试 **未通过**。

| 维度       | 结论                                                          |
| -------- | ----------------------------------------------------------- |
| 任务理解     | 失败：系统将 MCP 错误解释为“多云平台”                                      |
| 检索质量     | 部分失败：raw notes 中出现了正确的 Model Context Protocol 来源，但没有被最终答案采用 |
| 工具调用策略   | 失败：一个 MVP 问题被拆成多次并行研究，并在失败后继续重试                             |
| 最终回答质量   | 失败：输出内容偏离用户真实意图，且出现 `example.com` 示例链接                      |
| token 成本 | 严重失败：用户观察到总消耗约 80w token，远超 MVP 任务合理范围                      |
| 抗幻觉能力    | 失败：工具失败后仍然基于模型常识生成答案                                        |

核心判断：**这不是一个成功的正向用例，而是一个很有价值的系统缺陷暴露用例。**

## 3. 关键问题复盘

### 3.1 缩写消歧失败：MCP 被错误解释为“多云平台”

用户问题处于 Agent / LLM 场景，MCP 更合理的默认含义应是 **Model Context Protocol**。但系统在 `research_brief` 中将其改写为：

> MCP（多云平台）

这直接导致后续研究方向偏离。trace 中的 `research_brief` 明确显示，系统把任务设定为研究“多云平台”。

更严重的是，supervisor 一开始就在 `think_tool` 中确认了这一错误方向，称 “MCP（多云平台）是一个技术主题”。

### 3.2 任务拆分过度：MVP 问题被拆成 4 个 ConductResearch

用户要求“最多 3 个来源、600 字以内”，这类任务理论上只需要 **1 次轻量检索 + 1 次总结**。

但 supervisor 一次性发起了 4 个 ConductResearch 子任务，分别研究定义、解决的问题、核心组件和适用场景。

这违反了 MVP 目标，也直接放大了 token、检索和失败重试成本。

### 3.3 工具连续失败，但系统没有及时停止

第一轮 4 个 ConductResearch 全部返回：

> Error synthesizing research report: Maximum retries exceeded

这说明研究工具没有成功产出可用结果。

但系统没有停止，而是继续规划“重新评估并细化研究请求”，随后又发起第二轮多个 ConductResearch。

第二轮仍然失败，trace 中继续出现多次 `Maximum retries exceeded`。

这说明当前系统缺少一个关键机制：

> 工具失败后应立即降级，而不是自动加大检索力度。

### 3.4 raw notes 中其实出现了正确方向，但最终没有利用

raw notes 里检索到了 Google Cloud 关于 **Model Context Protocol (MCP)** 的资料，说明 MCP 是 Anthropic 于 2024 年提出的开放标准，用于让 LLM 与外部数据、应用和服务交互；它包含 MCP Host、MCP Client、MCP Server、Transport Layer 等组件，并通过 JSON-RPC 2.0 通信。

这说明搜索结果并非完全不可用。真正的问题是：

1. 查询方向被“多云平台”污染；
2. 正确来源没有被优先选择；
3. final writer 没有基于 raw notes 做主题校验；
4. 没有 source-grounded verifier 检查最终答案是否匹配用户意图。

### 3.5 最终答案出现伪来源和无源生成

最终答案给出了多云平台解释，并使用了 `example.com` 作为来源。trace 中可以看到，最终输出包含：

* `https://example.com/mcp-definition`
* `https://example.com/mcp-benefits-and-use-cases`
* `https://example.com/key-components-of-effective-mcp`

这些显然不是官方或权威来源。

更后面的 fallback 也承认无法通过 ConductResearch 获取信息，于是“利用已有知识库”生成多云平台概述。

这违反了用户的核心要求：

> 只基于 2–3 个官方或权威来源；无法确认写“未公开”。

## 4. token 成本分析

用户反馈本次总消耗约 **80w token**。这对该 MVP 问题来说明显异常。

从 trace 可见的顶层调用看，部分顶层 LLM 调用 token 并不大，例如最终错误回答约 1,914 tokens。 另一段 fallback 调用约 2,592 tokens。

因此，80w token 大概率主要消耗在以下隐藏或子级环节：

| 消耗来源                       | 说明                                     |
| -------------------------- | -------------------------------------- |
| 多个 ConductResearch 子任务     | 一次拆成 4 个研究任务                           |
| 工具内部重试                     | 每个 ConductResearch 可能内部多轮搜索、总结、结构化输出重试 |
| `Maximum retries exceeded` | 失败前已经消耗大量 token                        |
| 第二轮重新尝试                    | 第一轮失败后又发起新一轮多个研究任务                     |
| raw notes 过长               | 搜索结果塞入大量多云平台、MCP、AWS、IBM、华为云等摘要        |
| final fallback             | 工具失败后仍继续生成答案                           |

合理预算应当是：

| 任务类型             | 合理 token 范围 |
| ---------------- | ----------- |
| 当前 MCP MVP 问题    | 5k–20k      |
| 轻量 Deep Research | 20k–50k     |
| 中等多源报告           | 50k–100k    |
| 本次实际             | 约 80w，严重失控  |

## 5. 根因总结

| 根因              | 具体表现                                    |
| --------------- | --------------------------------------- |
| 缩写消歧缺失          | Agent 场景下 MCP 被误判为 Multi-Cloud Platform |
| brief 改写污染      | research brief 直接写成“多云平台”               |
| 任务拆分过度          | 600 字问题拆成 4 个并行子任务                      |
| 工具失败策略错误        | ConductResearch 失败后继续重试                 |
| source gate 缺失  | final answer 没有检查来源是否真实、是否匹配主题          |
| raw notes 选择失败  | 正确的 Model Context Protocol 来源出现，但未被采用   |
| fallback 不安全    | 工具失败后使用模型常识生成，导致无源答案                    |
| token budget 缺失 | 没有按 MVP 限制调用次数、来源数和 raw notes 长度        |

## 6. 修复建议

### 6.1 增加缩写消歧规则

```text
If the user asks about "MCP" in an Agent, LLM, tool-calling, or AI application context,
interpret it as Model Context Protocol unless the user explicitly says multi-cloud platform.
If ambiguous, preserve the acronym and search both meanings briefly before choosing.
```

### 6.2 增加 MVP 模式

```text
If the user requests "简要", "MVP", "最多 3 个来源", or "不超过 600 字":
- Use at most 1 ConductResearch call.
- Use at most 3 search results.
- Do not split the task into multiple sub-agents.
- Do not retry more than once.
- Final answer must be short and table-first.
```

### 6.3 工具失败后立即降级

```text
If ConductResearch returns "Maximum retries exceeded", empty output, or tool error:
- Do not call ConductResearch again unless the user explicitly asks.
- Do not generate unsourced factual claims.
- Return a short failure message:
  "检索失败，无法基于来源回答。"
```

### 6.4 禁止伪来源

```text
Final answers must cite only retrieved real sources.
Placeholder URLs such as example.com are forbidden.
If no valid source is available, write "未公开" or "未能核验".
```

### 6.5 final writer 增加 source-grounded 校验

最终写作前必须检查：

| 检查项    | 要求                                       |
| ------ | ---------------------------------------- |
| 主题匹配   | MCP 是否为 Model Context Protocol，而不是其他缩写含义 |
| 来源真实   | URL 必须来自检索结果                             |
| 来源数量   | 不超过用户限制                                  |
| 字数限制   | 不超过 600 字                                |
| 无关扩展   | 不引入多云平台等无关主题                             |
| 工具失败处理 | 若研究失败，不得伪装成成功答案                          |

## 7. 推荐的新测试问题

当前测试题建议稍微改写，明确 MCP 含义：

```text
请基于 2–3 个官方或权威来源，简要说明 Model Context Protocol（MCP）是什么、解决什么问题、核心组件有哪些，以及它适合哪些 Agent 场景。

限制：
1. 最多使用 3 个来源；
2. 最终回答不超过 600 字；
3. 用表格为主；
4. 不要扩展到多云平台等无关含义；
5. 无法确认的内容写“未公开”。
```

## 8. 最终判定

本用例应从“正向展示用例”调整为：

> **缩写消歧 + 工具失败降级 + token budget 控制 + 禁止伪来源 的回归测试用例。**

它暴露的问题非常关键，尤其适合放入系统评测集，用于验证后续版本是否解决以下能力：

1. 能否正确识别 MCP = Model Context Protocol；
2. 能否避免把 MVP 问题拆成多 agent 长任务；
3. 能否在 ConductResearch 失败时停止；
4. 能否拒绝生成无来源答案；
5. 能否控制 token 在合理范围内；
6. 能否只使用真实检索来源生成最终报告。

本次运行的失败不是个别回答质量问题，而是 **任务理解、调度策略、错误处理和事实约束机制共同失效**。
