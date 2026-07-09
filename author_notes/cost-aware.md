# **任务失控、token膨胀和研究过度扇出**

## 分析
严重 token bloat 往往来自这几类行为：

| 失控行为                | 后果       | 解决方式                        |
| ------------------- | -------- | --------------------------- |
| researcher 自己决定继续搜  | 搜索轮次无限扩散 | 每轮必须报告新增信息量                 |
| 每个 query 返回大量网页正文   | 工具反馈膨胀   | 先 title/snippet 筛选，再读正文     |
| 多 researcher 搜同一类资料 | 重复上下文堆积  | URL 去重、source cluster 去重    |
| 论坛/博客/视频被大量读取       | 证据质量下降   | 来源分级，官方/论文优先                |
| 最终报告前才校验引用          | 发现问题太晚   | 每轮研究后即做 evidence validation |

**关键问题：压缩后的信息有没有带来新增价值，没有被量化并用于指导系统收敛**

再者，我们需要做以下评估：

| 指标                             | 作用                 |
| ------------------------------ | ------------------ |
| total_tokens                   | 总 token 成本         |
| **tokens_per_claim**               | **每个有效结论消耗多少 token**   |
| **tool_calls_per_claim**           | **每个有效结论用了多少工具调用**     |
| unique_sources / total_sources | 来源去重率              |
| official_sources_ratio         | 官方/论文/权威来源占比       |
| duplicate_query_rate           | 重复搜索率              |
| unsupported_claim_rate         | 无证据主张比例            |
| citation_mismatch_rate         | 引用不匹配比例            |
| fanout_width                   | 同时启动的 researcher 数 |
| fanout_depth                   | 每个 researcher 搜索轮次 |
| **marginal_gain_per_round**        | **每轮新增信息增益**           |


**关键思考：DeepResearch 系统不能只优化最终答案质量，而要优化“单位 token 产生的有效证据量”，即增信息增益**






## 方案：

# DeepResearch 信息增益控制方案：概述与实施

## 1. 核心目标

这里的“信息增益”不是严格的信息论熵增，而是一个**工程化过程指标**：衡量每一轮 researcher / search / compression 之后，系统是否真的获得了新的、可引用的、能补足任务维度的有效信息。

你的项目现在已经有三类硬限制：supervisor 并发 researcher 数、researcher 单轮工具并发数、Tavily 单次 query/result 数。`max_concurrent_research_units` 默认 3，`max_concurrent_researcher_tool_calls` 默认 3，`max_queries_per_search_call` 默认 3，`max_results_per_tavily` 默认 3。 同时，`compress_research` 已经把 researcher 的工具消息和 AI 消息压缩成 `compressed_research`，再返回给 supervisor。 因此，现在不应优先改 researcher 输出形态，而应新增一层 **Information Gain Gate（信息增益闸门）**。

---

## 2. 为什么要用信息增益控制 DeepResearch

Anthropic 在多智能体 Research 系统复盘中指出，多智能体研究系统适合开放式、宽搜索空间任务，但也会快速消耗 token；他们的数据中，普通 agent 约为 chat 的 4 倍 token，多智能体系统约为 chat 的 15 倍 token。Anthropic 还提到，token usage、tool calls 和 model choice 是解释浏览型 agent 性能差异的重要因素。

LangChain 多智能体文档也把性能成本明确拆成两个关键指标：**model calls** 和 **tokens processed**；更多模型调用会增加延迟和 API 成本，更多 token 会增加处理成本并触及上下文限制。 LangChain 的 Context Engineering 文章则把上下文治理总结为 **write、select、compress、isolate** 四类策略，本质上也是控制“每一步到底该给模型看什么”。

所以，对你的系统来说，信息增益闸门要解决的是：

| 问题          | 传统硬限制              | 信息增益控制                                              |
| ----------- | ------------------ | --------------------------------------------------- |
| 搜索过度        | 最多 N 轮             | 如果连续低增益，提前停止                                        |
| fan-out 过度  | 最多 N 个 researcher  | 如果已有维度覆盖充分，不再开新 researcher                          |
| token bloat | 最大 token 上限        | 用 `tokens_per_claim` 衡量 token 是否转化为有效结论             |
| 工具滥用        | 每轮最多 N 个工具         | 用 `tool_calls_per_claim` 衡量工具调用是否有效                 |
| 重复检索        | URL 去重             | 用 `duplicate_url_ratio` 和 `new_source_ratio` 判断是否继续 |
| 压缩失真        | compression prompt | 用 claim/source 抽取验证压缩后是否仍有可用证据                      |

---

## 3. 总体架构

推荐新增一个轻量节点：`extract_research_metrics`。它位于 `compress_research` 之后、返回 supervisor 之前。

```text
researcher
  ↓
researcher_tools
  ↓
compress_research
  ↓
extract_research_metrics   ← 新增
  ↓
supervisor_tools 汇总 compressed_research + metrics
  ↓
information_gain_gate
  ↓
继续研究 / 终止研究 / 进入 final_report
```

你的 `compress_research_system_prompt` 已经要求保留所有相关信息、来源、URL、行内引用和 Sources 列表，并强调不要丢失来源。 因此 `extract_research_metrics` 不需要重新研究，只需要从 `compressed_research` 中抽取结构化 claim、source 和 coverage 信息。

---

## 4. 核心数据结构

建议在 `state.py` 中新增三类结构：`ClaimRecord`、`SourceRecord`、`RoundMetrics`。你当前 `AgentState / SupervisorState / ResearcherState` 主要保存 messages、notes、raw_notes、final_report 等字段，还没有过程指标状态。

```python
from pydantic import BaseModel, Field
from typing import Optional


class ClaimRecord(BaseModel):
    claim_id: str
    claim: str
    canonical_claim: str
    source_urls: list[str]
    source_titles: list[str] = []
    dimension: Optional[str] = None
    researcher_id: Optional[str] = None
    supervisor_round: int = 0
    support_score: float = 1.0
    source_quality: str = "unknown"  # official / paper / docs / media / forum / unknown


class SourceRecord(BaseModel):
    url: str
    title: str = ""
    source_type: str = "unknown"  # official_doc / paper / blog / media / forum / unknown
    authority_score: float = 0.5
    first_seen_round: int = 0
    used_by_claim_ids: list[str] = []


class RoundMetrics(BaseModel):
    supervisor_round: int
    researcher_id: str

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    tool_calls: int = 0
    search_calls: int = 0
    queries: int = 0

    total_urls: int = 0
    unique_urls: int = 0
    duplicate_urls: int = 0

    extracted_claims: int = 0
    new_claims: int = 0
    supported_new_claims: int = 0

    coverage_before: float = 0.0
    coverage_after: float = 0.0
    coverage_gain: float = 0.0

    new_source_ratio: float = 0.0
    duplicate_url_ratio: float = 0.0
    authority_gain: float = 0.0
    marginal_gain: float = 0.0

    tokens_per_claim: Optional[float] = None
    tool_calls_per_claim: Optional[float] = None

    decision: str = "continue"  # continue / stop / escalate / compress_more
    stop_reason: Optional[str] = None
```

---

## 5. 信息增益公式

### 5.1 推荐公式

```text
marginal_gain_per_round =
  0.40 × new_supported_claim_ratio
+ 0.25 × new_source_ratio
+ 0.20 × coverage_gain
+ 0.15 × authority_gain
- 0.20 × duplicate_penalty
```

### 5.2 各项定义

| 指标                          | 公式                                                   | 含义                               |
| --------------------------- | ---------------------------------------------------- | -------------------------------- |
| `new_supported_claim_ratio` | `supported_new_claims / max(extracted_claims, 1)`    | 本轮抽取出的 claim 中，有多少是新增且有 URL 支撑的。 |
| `new_source_ratio`          | `new_unique_urls / max(unique_urls_this_round, 1)`   | 本轮来源是否真的新增，而不是重复已有 URL。          |
| `coverage_gain`             | `coverage_after - coverage_before`                   | 本轮是否补足了研究简报中的任务维度。               |
| `authority_gain`            | `new_high_quality_sources / max(new_unique_urls, 1)` | 本轮是否带来官方文档、论文、权威博客等高质量来源。        |
| `duplicate_penalty`         | `duplicate_urls / max(total_urls_this_round, 1)`     | 重复来源惩罚。                          |

### 5.3 为什么 claim 是核心单位

RAGAS 的 Faithfulness 指标就是先识别回答中的 claims，再判断每个 claim 是否能被 retrieved context 支持；如果所有 claim 都能被上下文支持，回答才被认为 faithful。 RAGAS 的 Context Recall 也会把参考答案拆成 claims，并判断这些 claims 是否能归因到 retrieved context。

因此，对 DeepResearch 来说，**claim 是比“摘要长度”“来源数量”“搜索次数”更可靠的过程计量单位**。

---

## 6. 配套效率指标

### 6.1 `tokens_per_claim`

```text
tokens_per_claim = round_total_tokens / max(supported_new_claims, 1)
```

含义：每获得一个“有证据支撑的新结论”，消耗了多少 token。

建议解释方式：

|                         数值 | 判断               |
| -------------------------: | ---------------- |
|                     `< 5K` | 很高效              |
|                   `5K–15K` | 可接受              |
|                  `15K–30K` | 低效，需要观察          |
|                    `> 30K` | 明显 token bloat   |
| `supported_new_claims = 0` | 本轮无效，应停止或改 query |

LangSmith 成本追踪支持记录 token 和 cost，并支持通过 `usage_metadata` 上传 input/output/total token 或工具成本信息。 所以你的系统可以优先从 LangChain/模型响应的 `usage_metadata` 取 token；取不到时再用 tokenizer 估算。

### 6.2 `tool_calls_per_claim`

```text
tool_calls_per_claim = executed_tool_calls / max(supported_new_claims, 1)
```

含义：每获得一个有效新结论，消耗了多少工具调用。

你的 `researcher_tools` 已经在执行前拿到了 `tool_calls`，并通过 `max_concurrent_researcher_tool_calls` 截断允许执行的工具调用。 因此这里很容易统计：

```python
executed_tool_calls = len(allowed_tool_calls)
overflow_tool_calls = len(overflow_tool_calls)
search_calls = sum(1 for c in allowed_tool_calls if c["name"] == "tavily_search")
```

建议判断：

|                         数值 | 判断           |
| -------------------------: | ------------ |
|                      `< 1` | 很高效          |
|                      `1–3` | 正常           |
|                      `3–5` | 低效           |
|                      `> 5` | 工具调用膨胀       |
| `supported_new_claims = 0` | 工具调用没有产生有效结论 |

---

## 7. Claim 去重与新旧判断

### 7.1 Canonical Claim

每个 claim 需要先规范化：

```python
def canonicalize_claim(text: str) -> str:
    text = text.lower().strip()
    text = " ".join(text.split())
    return text
```

更稳的方式是用 embedding 相似度：

```text
如果 cosine_similarity(new_claim, existing_claim) >= 0.86
则认为是重复 claim
```

初始可以用轻量 embedding 模型，后续再换更强的中文/英文混合 embedding。

### 7.2 新 claim 判定

```python
def is_new_claim(new_claim, existing_claims, threshold=0.86):
    for old in existing_claims:
        if cosine_similarity(new_claim, old) >= threshold:
            return False
    return True
```

### 7.3 有效 claim 判定

一个 claim 必须同时满足：

```text
1. 有明确 source_url；
2. 不是纯背景描述；
3. 与 research_brief 相关；
4. 能映射到至少一个 research_dimension；
5. 与已有 claim 不重复。
```

---

## 8. Coverage Gain：任务维度覆盖增益

建议在 `write_research_brief` 后新增一个轻量结构化输出：`research_dimensions`。

例如用户要求：

```text
1. 总体架构建议
2. 阶段-模型选择-升级条件-降级条件-风险控制表
3. 上下文治理策略表
4. supervisor_model 和 researcher_model 是否分开
5. 默认参数建议
6. 评估指标表
```

则生成：

```json
{
  "research_dimensions": [
    "总体架构建议",
    "阶段模型调度策略",
    "上下文治理策略",
    "supervisor/researcher模型分离",
    "默认参数建议",
    "评估指标体系"
  ]
}
```

每个 `ClaimRecord` 需要标注 `dimension`。覆盖率计算：

```text
coverage = covered_dimensions / total_dimensions
coverage_gain = coverage_after - coverage_before
```

示例：

| round | 覆盖维度数 | coverage | coverage_gain |
| ----: | ----: | -------: | ------------: |
|     1 | 3 / 6 |     0.50 |         +0.50 |
|     2 | 5 / 6 |     0.83 |         +0.33 |
|     3 | 5 / 6 |     0.83 |          0.00 |
|     4 | 6 / 6 |     1.00 |         +0.17 |

如果某轮 `coverage_gain = 0`，且 `new_supported_claims` 很少，说明继续搜索大概率是在重复。

---

## 9. Source Quality：来源质量评分

Anthropic 在自己的多智能体评估 rubric 中包含 source quality，并强调偏好 primary sources over lower-quality secondary sources。 你的系统可以把来源分级：

| 来源类型                          | `authority_score` |
| ----------------------------- | ----------------: |
| 官方文档 / 官方博客 / 标准文档            |              1.00 |
| 论文 / arXiv / 期刊 / 开源项目 README |              0.85 |
| 权威技术博客 / 大厂工程博客               |              0.75 |
| 主流媒体报道                        |              0.60 |
| 普通博客 / SEO 内容                 |              0.35 |
| Reddit / 论坛 / YouTube 评论      |              0.20 |
| 未知来源                          |              0.10 |

`authority_gain` 可以这样算：

```text
authority_gain = average(authority_score of new sources)
```

或者更简单：

```text
authority_gain = new_high_quality_sources / max(new_unique_urls, 1)
```

其中 `high_quality_sources = authority_score >= 0.75`。

---

## 10. 早停规则

### 10.1 单 researcher 早停

当 researcher 连续低增益时，提前进入 `compress_research`：

```text
if low_gain_streak >= 2:
    goto compress_research
```

低增益定义：

```text
marginal_gain < 0.15
或 supported_new_claims == 0
或 duplicate_url_ratio > 0.70
或 tool_calls_per_claim > 5
或 tokens_per_claim > 30000
```

### 10.2 Supervisor 全局早停

当全局覆盖已经足够，但新增信息很少时，结束研究：

```text
if global_coverage >= 0.80
and recent_marginal_gain < 0.15
and recent_supported_new_claims <= 2:
    stop research
```

### 10.3 成本熔断

```text
if total_tokens > token_budget:
    stop search
    generate final report with:
    - 已确认结论
    - 证据不足结论
    - 未能确认的信息
```

### 10.4 重复搜索熔断

```text
if duplicate_url_ratio > 0.70
and new_supported_claims <= 1:
    stop current researcher
```

---

## 11. 推荐接入位置

### 11.1 `compress_research` 后新增抽取

当前 `compress_research` 返回：

```python
return {
    "compressed_research": str(response.content),
    "raw_notes": [raw_notes_content]
}
```

建议改为：

```python
compressed = str(response.content)
metrics_result = await extract_research_metrics(
    compressed_research=compressed,
    research_topic=state.get("research_topic", ""),
    existing_claims=state.get("claim_registry", []),
    research_dimensions=state.get("research_dimensions", []),
    config=config,
)

return {
    "compressed_research": compressed,
    "raw_notes": [raw_notes_content],
    "claims": metrics_result.claims,
    "sources": metrics_result.sources,
    "metrics": metrics_result.metrics,
}
```

### 11.2 `supervisor_tools` 汇总指标

当前 `supervisor_tools` 会把每个 researcher 的 `compressed_research` 包成 `ToolMessage` 回传给 supervisor。 可以保持这部分不变，只额外汇总 hidden metrics：

```python
update_payload["claim_registry"] = [
    claim
    for observation in tool_results
    for claim in observation.get("claims", [])
]

update_payload["source_registry"] = [
    source
    for observation in tool_results
    for source in observation.get("sources", [])
]

update_payload["round_metrics"] = [
    observation["metrics"]
    for observation in tool_results
    if observation.get("metrics")
]
```

### 11.3 在回到 supervisor 前判断是否早停

```python
gain_decision = compute_global_gain_decision(
    round_metrics=update_payload.get("round_metrics", []),
    claim_registry=update_payload.get("claim_registry", []),
    source_registry=update_payload.get("source_registry", []),
    research_dimensions=state.get("research_dimensions", []),
    config=config,
)

if gain_decision.should_stop:
    return Command(
        goto=END,
        update={
            "notes": get_notes_from_tool_calls(supervisor_messages + all_tool_messages),
            "research_brief": state.get("research_brief", ""),
            "round_metrics": update_payload.get("round_metrics", []),
            "stop_reason": gain_decision.reason,
        }
    )
```

---

## 12. 指标打印格式

建议复用你已有的 `process_print` 风格，新增 `process_metrics_print`。你当前已有 trace 输出函数，会打印 event、round、name、id、parent、concurrency、tools 等字段。

新增输出：

```text
──────────────────────────────
[METRICS #012] round=supervisor:2 researcher=supervisor:2/researcher:1
tokens=48320 tool_calls=6 search_calls=3
claims=9 new_claims=4 supported_new_claims=3
urls=12 unique_urls=8 duplicate_url_ratio=0.33
coverage_gain=0.17 marginal_gain=0.31
tokens_per_claim=16106 tool_calls_per_claim=2.0
decision=continue
──────────────────────────────
```

这样你之后评估 case 时，不只看最终报告，还能看到系统为什么继续/停止。

---

## 13. 阈值建议

这些阈值不是权威固定值，而是建议初始值，需要用你的评估集校准。

| 参数                              |     默认值 | 含义               |
| ------------------------------- | ------: | ---------------- |
| `min_marginal_gain`             |  `0.15` | 低于该值认为本轮信息增益低    |
| `low_gain_patience`             |     `2` | 连续几轮低增益后停止       |
| `max_tokens_per_claim`          | `30000` | 超过说明 token 转化效率差 |
| `max_tool_calls_per_claim`      |   `5.0` | 超过说明工具调用膨胀       |
| `max_duplicate_url_ratio`       |  `0.70` | 超过说明重复检索严重       |
| `min_global_coverage_to_stop`   |  `0.80` | 覆盖率超过该值后可考虑早停    |
| `min_supported_new_claims`      |     `2` | 本轮少于该数量时认为收益低    |
| `high_quality_source_threshold` |  `0.75` | 来源质量高于该分数算权威来源   |

---

## 14. 最小实现路线

### 阶段 A：只观测，不干预

目标：先收集真实运行数据，不改变系统行为。

```text
1. 给 state.py 加 ClaimRecord / SourceRecord / RoundMetrics；
2. 给 compress_research 后加 extract_research_metrics；
3. 给 researcher_tools / supervisor / compress_research 加 token 和 tool call 统计；
4. 每轮打印 metrics；
5. 暂不早停。
```

### 阶段 B：软早停

目标：先提示，不强制停止。

```text
当 marginal_gain < 0.15：
- 在 trace 中打印 decision=should_stop
- 但仍让 supervisor 自己决定是否继续
```

### 阶段 C：硬早停

目标：正式控制失控。

```text
当连续 low_gain_patience 轮低增益：
- researcher 直接进入 compress_research
- supervisor 不再继续 ConductResearch
- final_report 中标注“已确认 / 未确认 / 证据不足”
```

### 阶段 D：预算自适应

目标：根据任务难度动态调整 fan-out。

```text
简单任务：
- max_concurrent_research_units = 1
- max_react_tool_calls = 2

中等任务：
- max_concurrent_research_units = 2–3
- max_react_tool_calls = 3

复杂任务：
- max_concurrent_research_units = 3–5
- max_react_tool_calls = 5
```

Anthropic 的经验也支持按任务复杂度分配 effort：简单 fact-finding 用较少 agent 和工具调用，直接比较任务可用 2–4 个 subagents，复杂研究才使用更多 subagents。

---

## 15. 实施后的评估表

| 评估项        | 指标                                              | 目标                         |
| ---------- | ----------------------------------------------- | -------------------------- |
| 真实性        | `faithfulness`                                  | claim 能被证据支持               |
| 完整性        | `coverage` / `context_recall`                   | 用户要求维度覆盖充分                 |
| 证据链        | `supported_claim_ratio`                         | 大多数 claim 有 URL 和 evidence |
| Token 效率   | `tokens_per_claim`                              | 越低越好                       |
| 工具效率       | `tool_calls_per_claim`                          | 越低越好                       |
| 搜索去重       | `duplicate_url_ratio`                           | 越低越好                       |
| 来源质量       | `authority_gain`                                | 官方/论文/权威来源占比更高             |
| fan-out 控制 | `researcher_count × search_rounds × tool_calls` | 避免宽度和深度同时膨胀                |
| 停止质量       | `low_gain_stop_precision`                       | 停止后 final report 质量不下降     |
| 成本收益       | `quality_score / total_tokens`                  | 单位 token 质量更高              |

---

## 16. 推荐最终落地结论

你的项目不需要优先改成 Evidence Card，因为 `compress_research` 已经承担了 researcher → supervisor 的压缩回传职责，而且 prompt 已明确要求去重、来源、URL 和引用。更优改法是：

```text
保留 compressed_research 作为 supervisor 可见内容；
新增 claims / sources / metrics 作为 hidden state；
用 marginal_gain_per_round 判断是否继续研究；
用 tokens_per_claim 判断 token 是否转化为有效结论；
用 tool_calls_per_claim 判断工具调用是否过度；
用 coverage_gain 判断是否补足用户要求维度；
用 duplicate_url_ratio 判断搜索是否重复；
用 authority_gain 判断是否真的找到更高质量来源。
```

一句话概括：

> **信息增益控制不是替代 compression，而是在 compression 之后判断“这轮压缩结果值不值得继续投入更多 token”。**
