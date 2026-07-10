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

你的项目现在已经有三类硬限制：supervisor 并发 researcher 数、researcher 单轮工具并发数、Tavily 单次 query/result 数。`max_concurrent_research_units` 默认 3，`max_concurrent_researcher_tool_calls` 默认 3，`max_queries_per_search_call` 默认 3，`max_results_per_tavily` 默认 3。 同时，`compress_research` 已经把 researcher 的工具消息和 AI 消息压缩成 `compressed_research`，再返回给 supervisor。 因此，现在不应优先改 researcher 输出形态，而应新增一条 **process metrics track（过程指标轨）**：第一阶段只观测和记录信息增益，不改变 LangGraph 路由；后续再逐步升级为软停止建议和硬停止闸门。

实施前还需要修正一个当前代码与文档不一致的前提：`print_process_info` 的代码默认值目前与“默认关闭”的设计意图不一致。接入 metrics trace 前应先统一为默认关闭，否则会破坏“低污染、默认不增加运行输出”的前提。

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

推荐优先新增一个轻量后处理函数或可选节点：`extract_research_metrics`。它位于 `compress_research` 之后、返回 supervisor 之前，但阶段 A/B 不改变原有路由，只把 metrics 写入 hidden state 和 trace。

```text
researcher
  ↓
researcher_tools
  ↓
compress_research
  ↓
extract_research_metrics   ← 新增，失败时降级为原流程
  ↓
supervisor_tools 汇总 compressed_research + hidden metrics
  ↓
supervisor 原有决策流程
  ↓
继续研究 / ResearchComplete / final_report

后续可选：
hidden metrics → soft_stop_decision → hard information_gain_gate
```

你的 `compress_research_system_prompt` 已经要求保留所有相关信息、来源、URL、行内引用和 Sources 列表，并强调不要丢失来源。 因此 `extract_research_metrics` 不需要重新研究，只需要从 `compressed_research` 中抽取结构化 claim、source 和 coverage 信息。

---

## 4. 核心数据结构

建议在 `state.py` 中新增三类结构：`ClaimRecord`、`SourceRecord`、`RoundMetrics`。你当前 `AgentState / SupervisorState / ResearcherState` 主要保存 messages、notes、raw_notes、final_report 等字段，还没有过程指标状态。

注意：如果 metrics 由 researcher 子图产生，除了扩展 `ResearcherState`，还必须同步扩展 `ResearcherOutputState`。否则 `compress_research` 返回的 `claims`、`sources`、`metrics` 可能不会被 researcher 子图输出给 `supervisor_tools`，导致 supervisor 侧无法汇总 hidden metrics。

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

更稳的方式是用 embedding 相似度，但这不应放进第一阶段。embedding 去重会带来模型选择、新依赖、本地/外部调用和成本问题；阶段 A/B 应先使用轻量文本规范化、citation 去除、URL 去重和保守字符串相似度。

```text
如果 cosine_similarity(new_claim, existing_claim) >= 0.86
则认为是重复 claim
```

embedding 可作为后续校准阶段的增强项，只有当轻量去重无法满足评估集需求时再引入。

### 7.2 新 claim 判定

```python
def is_new_claim(new_claim, existing_claims):
    canonical_new = canonicalize_claim(new_claim)
    for old in existing_claims:
        if canonical_new == canonicalize_claim(old):
            return False
    return True
```

如果后续启用 embedding 相似度，应通过独立配置开关控制，并明确 embedding provider、缓存策略、成本和失败降级路径。

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
try:
    metrics_result = await extract_research_metrics(
        compressed_research=compressed,
        research_topic=state.get("research_topic", ""),
        existing_claims=state.get("claim_registry", []),
        research_dimensions=state.get("research_dimensions", []),
        config=config,
    )
except Exception:
    metrics_result = None

return {
    "compressed_research": compressed,
    "raw_notes": [raw_notes_content],
    "claims": metrics_result.claims if metrics_result else [],
    "sources": metrics_result.sources if metrics_result else [],
    "metrics": metrics_result.metrics if metrics_result else None,
}
```

这部分必须满足两个约束：

1. `extract_research_metrics` 失败不能导致 `compress_research` 失败；
2. `ResearcherOutputState` 需要包含新增输出字段，否则 `supervisor_tools` 可能拿不到子图返回的 metrics。

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

第一阶段不要直接改变路由，只计算并打印 soft decision。下面的硬停止路由只能在 soft decision 经评估稳定后启用：

```python
gain_decision = compute_global_gain_decision(
    round_metrics=update_payload.get("round_metrics", []),
    claim_registry=update_payload.get("claim_registry", []),
    source_registry=update_payload.get("source_registry", []),
    research_dimensions=state.get("research_dimensions", []),
    config=config,
)

if configurable.enable_hard_gain_stop and gain_decision.should_stop:
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
1. 给 state.py 加 ClaimRecord / SourceRecord / RoundMetrics，并同步扩展 ResearcherOutputState；
2. 给 compress_research 后加 extract_research_metrics，失败时降级为原 compressed_research 返回；
3. 给 researcher_tools / supervisor / compress_research 加 tool call 统计；
4. token 统计优先读取 usage_metadata；取不到时允许为空或粗估，并标注来源；
5. 每轮用 process_metrics_print 打印 metrics；
6. 暂不早停，不改变 LangGraph 路由。
```

阶段 A 不引入 embedding 去重，不新增外部模型调用依赖，不把 metrics 写入 `supervisor_messages`。

### 阶段 B：软早停

目标：先提示，不强制停止。

```text
当 marginal_gain < 0.15：
- 在 trace 中打印 decision=should_stop
- 但仍让 supervisor 自己决定是否继续
```

软早停只影响 trace、调试信息和后续评估，不应让单个指标直接决定停止。

### 阶段 C：硬早停

目标：正式控制失控。

```text
当连续 low_gain_patience 轮低增益：
- researcher 直接进入 compress_research
- supervisor 不再继续 ConductResearch
- final_report 中标注“已确认 / 未确认 / 证据不足”
```

硬早停是第一个改变流程的阶段，必须等阶段 A/B 的指标在真实任务和评估样本上校准后再启用。启用前还应先处理 `supervisor_tools` 中会把所有异常都当作结束研究处理的过宽异常分支，否则真实失败与低增益停止会混淆。

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



# DeepResearch 信息增益控制改造：阶段与模块说明

## 0. 改造目标与边界

本方案目标是在现有 DeepResearch 多智能体系统中增加一层 **Information Gain Control（信息增益控制）**，用于抑制 token bloat、研究过度扇出、重复搜索、低效工具调用和无效研究轮次。

核心原则：

1. **不替换现有 compression 链路**：保留 researcher → `compress_research` → supervisor 的主流程。
2. **不强制 researcher 直接输出 Evidence Card**：结构化指标从 `compressed_research` 后处理提取。
3. **不污染 supervisor 文本上下文**：metrics 进入 state / trace / decision layer，而不是直接塞进 `supervisor_messages`。
4. **先观测，再干预**：先实现指标采集与打印，再逐步接入软早停和硬早停。
5. **让编码智能体保留实现自由度**：本文只规定模块职责、原理和接口边界，不写死具体代码细节。
6. **第一阶段不新增外部依赖**：不引入 embedding 模型、向量库或额外外部模型调用，先用轻量规则和现有输出建立指标基线。
7. **失败降级为原流程**：metrics 抽取、去重、评分或打印失败时，不应阻断 `compressed_research` 回传和最终报告生成。

---

# 一、总体分阶段

| 阶段   | 名称    | 目标                                                     | 是否影响原流程 |
| ---- | ----- | ------------------------------------------------------ | ------- |
| 阶段 A | 观测阶段  | 统计 token、tool call、source、claim 等过程数据                  | 否       |
| 阶段 B | 结构化阶段 | 从压缩结果中抽取 claim、source、coverage 信息                      | 否       |
| 阶段 C | 评分阶段  | 计算 marginal gain、tokens_per_claim、tool_calls_per_claim | 否       |
| 阶段 D | 软控制阶段 | 给出 continue / should_stop 建议，但不强制中断                    | 弱影响     |
| 阶段 E | 硬控制阶段 | 根据低增益、重复搜索、预算超限自动早停                                    | 是       |
| 阶段 F | 校准阶段  | 基于评估集调参，降低误停和漏停                                        | 是       |

推荐落地顺序应更保守：阶段 A/B/C/D 可以连续小步实现；阶段 E 必须在指标稳定、阈值经过评估后再默认启用；阶段 F 不是收尾项，而应从阶段 A 开始同步积累样本。

---

# 二、模块 1：Research Metrics State 模块

## 概述

该模块负责为系统新增过程指标状态，包括 claim、source、round_metrics、global_metrics 等。它是所有后续信息增益控制的基础。

当前系统的 `AgentState / SupervisorState / ResearcherState` 主要保存 messages、notes、raw_notes、final_report、research_iterations、tool_call_iterations 等字段，还没有研究过程指标状态。

## 原理

DeepResearch 的失控往往不是最终报告阶段才发生，而是在研究过程中逐轮累积。因此需要把“每轮研究是否值得继续”变成可观测状态。

该模块不直接决定是否停止，只负责记录事实：

* 本轮用了多少 token；
* 本轮调用了多少工具；
* 本轮新增了多少 claim；
* 本轮新增了多少 URL；
* 本轮是否覆盖了新的任务维度；
* 本轮是否重复已有信息。

## 实现方案

建议新增以下状态对象：

* `ClaimRecord`
* `SourceRecord`
* `RoundMetrics`
* `GlobalResearchMetrics`

建议新增 state 字段：

* `claim_registry`
* `source_registry`
* `round_metrics`
* `research_dimensions`
* `global_metrics`

这些字段应作为 hidden state 使用，不直接进入 supervisor 的自然语言上下文。

如果 researcher 子图负责生成这些字段，`ResearcherOutputState` 必须同步声明 `claims`、`sources`、`metrics` 或等价输出字段，保证 `supervisor_tools` 能从 `tool_results` 中读取。

## 模块边界

该模块只定义数据结构和 reducer 策略，不负责：

* claim 抽取；
* 信息增益计算；
* 是否停止研究；
* 最终报告校验。

---

# 三、模块 2：Research Dimension Extraction 模块

## 概述

该模块负责将用户研究简报拆成若干可覆盖、可追踪的研究维度，用于计算 `coverage_gain`。

例如用户要求：

```text
1. 总体架构建议
2. 阶段-模型选择-升级条件-降级条件-风险控制表
3. 上下文治理策略表
4. supervisor_model 和 researcher_model 是否分开
5. 默认参数建议
6. 评估指标表
```

系统应生成：

```text
- 总体架构
- 模型调度策略
- 上下文治理策略
- supervisor/researcher 模型分离
- 默认参数建议
- 评估指标体系
```

## 原理

单纯统计来源数量或 token 数不能判断研究是否完整。一个 researcher 可能读取很多网页，但只反复覆盖同一个维度。

`research_dimensions` 的作用是让系统知道：

* 哪些用户需求已经被覆盖；
* 哪些维度仍缺证据；
* 新一轮搜索是否真的补足了缺口；
* supervisor 是否还需要继续分发 researcher。

## 实现方案

建议在 `write_research_brief` 后增加轻量结构化步骤，生成 `research_dimensions`。

实现方式可选：

1. 使用同一个 research brief 生成模型；
2. 使用更便宜的结构化抽取模型；
3. 直接基于用户输出要求做规则抽取；
4. 对简单任务可跳过该模块，默认只有一个维度。

输出建议：

```json
{
  "research_dimensions": [
    {
      "id": "D1",
      "name": "总体架构建议",
      "description": "系统整体调用关系、数据流和控制策略"
    }
  ]
}
```

## 模块边界

该模块只负责定义“应该覆盖什么”，不判断“是否已经覆盖”。覆盖判断交给 Claim & Coverage 模块处理。

---

# 四、模块 3：Claim & Source Extraction 模块

## 概述

该模块负责在 `compress_research` 之后，从 `compressed_research` 中抽取结构化 claim 和 source。

当前 compression prompt 已经要求保留所有相关信息、来源、URL、行内引用和 Sources 列表，因此 `compressed_research` 是一个适合做结构化抽取的输入。

## 原理

信息增益的基本单位不应该是：

* token 数；
* 网页数；
* 搜索次数；
* 摘要长度。

而应该是：

> 有来源支撑、与任务相关、非重复的新 claim。

因此该模块要从压缩结果中抽取：

* claim；
* 对应 URL；
* 来源标题；
* 来源类型；
* 所属研究维度；
* 是否有明确证据支撑。

## 实现方案

建议新增一个轻量后处理函数：

```text
extract_research_metrics(compressed_research, research_dimensions, existing_claims, existing_sources)
```

它应完成：

1. 从 `compressed_research` 中抽取 claim；
2. 从 Sources 区域抽取 URL；
3. 建立 claim → URL 的对应关系；
4. 将 claim 映射到 research dimension；
5. 过滤无来源 claim；
6. 初步识别来源类型。

输出：

```json
{
  "claims": [],
  "sources": [],
  "coverage_dimensions": [],
  "raw_extraction_notes": ""
}
```

## 模块边界

该模块不负责判断是否继续研究，只负责把文本变成结构化事实。

是否新增、是否重复、是否高价值，由后续模块判断。

---

# 五、模块 4：Claim Dedup & Registry 模块

## 概述

该模块负责维护全局 claim registry，并判断本轮 claim 是新增信息还是重复信息。

## 原理

DeepResearch 失控时常见的问题是：

* 不同 researcher 找到相同来源；
* 不同搜索 query 返回相同事实；
* compression 后重复表达相同结论；
* supervisor 误以为“信息很多”，但其实只是重复。

因此需要对 claim 做 canonicalization 和相似度去重。但在最小实现中，去重应先保持低成本和无外部依赖：优先做规范化文本匹配、citation 去除和 URL 级去重；语义相似度只作为后续增强项。

## 实现方案

建议分阶段去重：

### 1. 轻量文本规范化

用于处理完全相同或近似相同表达：

```text
大小写统一、空格归一、去掉无意义标点、去掉 citation 标号。
```

### 2. 保守字符串相似度

用于处理轻微改写但仍高度相似的表达：

```text
去除 citation、统一大小写和空白后，如果文本完全相同或高度重叠，则视为重复。
```

### 3. 语义相似度去重（后续增强）

用于处理同义表达，但不进入第一阶段：

```text
如果 new_claim 与 existing_claim 的 embedding 相似度高于阈值，则视为重复。
```

只有当评估证明轻量去重不足时，才引入 embedding。引入前必须明确 provider、缓存、成本、失败降级和配置开关，避免为观测阶段增加新依赖或外部调用。

## 模块边界

该模块不计算信息增益总分，只输出：

* `new_claims`
* `duplicate_claims`
* `supported_new_claims`
* `claim_registry_updated`

---

# 六、模块 5：Source Registry & Authority 模块

## 概述

该模块负责维护全局来源注册表，判断本轮来源是否新增，以及来源质量如何。

当前 `tavily_search` 已经按 URL 去重单次搜索结果。 但这个去重只发生在一次 Tavily search 内，不等价于全局研究过程去重。

## 原理

信息增益不只取决于是否有新 claim，也取决于来源是否更权威。

例如：

* 新增一个官方文档，价值高；
* 新增三个 SEO 博客，价值低；
* 重复读取同一个 GitHub README，价值接近 0；
* 论坛来源只能作为辅助证据，不能支撑高风险结论。

## 实现方案

建议维护 `source_registry`，记录：

* URL；
* title；
* source_type；
* authority_score；
* first_seen_round；
* used_by_claim_ids。

来源类型可分为：

| 来源类型                          | 建议权重 |
| ----------------------------- | ---: |
| official_doc / official_blog  | 1.00 |
| paper / arxiv / journal       | 0.85 |
| github_repo / open_source_doc | 0.80 |
| authoritative_blog            | 0.75 |
| mainstream_media              | 0.60 |
| normal_blog                   | 0.35 |
| forum / reddit / youtube      | 0.20 |
| unknown                       | 0.10 |

## 模块边界

该模块只评估来源质量和新增来源比例，不直接决定研究停止。

---

# 七、模块 6：Coverage Gain 模块

## 概述

该模块负责计算本轮研究对任务维度覆盖率的提升，即 `coverage_gain`。

## 原理

如果本轮 claim 全都映射到已经覆盖的维度，则即使新增了来源，也可能不值得继续扩展。

覆盖率计算：

```text
coverage = covered_dimensions / total_dimensions
coverage_gain = coverage_after - coverage_before
```

## 实现方案

输入：

* `research_dimensions`
* `claim_registry`
* 本轮新增 claims

处理：

1. 读取每个 claim 的 `dimension_id`；
2. 统计本轮前已覆盖维度；
3. 统计本轮后已覆盖维度；
4. 计算 coverage gain；
5. 标记仍未覆盖的维度。

输出：

```json
{
  "coverage_before": 0.50,
  "coverage_after": 0.83,
  "coverage_gain": 0.33,
  "missing_dimensions": ["默认参数建议"]
}
```

## 模块边界

该模块只判断“覆盖了多少”，不判断“证据是否充分”。证据充分性由 claim/source 模块和 critic/evaluator 模块处理。

---

# 八、模块 7：Information Gain Scoring 模块

## 概述

该模块负责把 claim 新增、source 新增、coverage gain、authority gain 和 duplicate penalty 汇总成 `marginal_gain_per_round`。

## 原理

推荐使用工程化混合评分，而不是严格信息论公式。

建议公式：

```text
marginal_gain_per_round =
  0.40 × new_supported_claim_ratio
+ 0.25 × new_source_ratio
+ 0.20 × coverage_gain
+ 0.15 × authority_gain
- 0.20 × duplicate_penalty
```

## 实现方案

输入：

* 本轮 `RoundMetrics`
* claim 去重结果
* source 去重结果
* coverage 结果
* authority 结果

输出：

```json
{
  "marginal_gain": 0.31,
  "gain_level": "medium",
  "main_positive_factors": ["new_supported_claims", "coverage_gain"],
  "main_negative_factors": ["duplicate_url_ratio"]
}
```

建议分级：

|          分数 | 等级       | 含义            |
| ----------: | -------- | ------------- |
|   `>= 0.45` | high     | 本轮价值高，建议继续或保留 |
| `0.20–0.45` | medium   | 本轮有一定价值，可继续观察 |
| `0.10–0.20` | low      | 低增益，应谨慎继续     |
|    `< 0.10` | very_low | 基本无效，建议停止     |

## 模块边界

该模块只计算分数，不直接终止流程。终止决策交给 Stop Decision 模块。

---

# 九、模块 8：Efficiency Metrics 模块

## 概述

该模块负责计算与信息增益配套的效率指标，重点包括：

* `tokens_per_claim`
* `tool_calls_per_claim`
* `duplicate_url_ratio`
* `search_calls_per_claim`

## 原理

只看信息增益还不够，因为某轮可能确实新增了信息，但成本极高。

例如：

```text
新增 1 个 claim，但消耗 80K token 和 12 次工具调用。
```

这种情况不应被视为健康研究。

## 实现方案

建议计算：

```text
tokens_per_claim = round_total_tokens / max(supported_new_claims, 1)

tool_calls_per_claim = executed_tool_calls / max(supported_new_claims, 1)

duplicate_url_ratio = duplicate_urls / max(total_urls, 1)
```

token 获取优先级：

1. 模型响应中的 `usage_metadata`；
2. provider 返回的 token usage；
3. LangSmith trace；
4. tokenizer 估算；
5. 字符数粗略估算。

不是所有模型/provider 都稳定返回 token usage。因此 `tokens_per_claim` 应允许为 `None` 或标记为 estimated，trace 中应同时输出 token 数据来源，例如 `usage_source=usage_metadata / estimated / unavailable`。不要因为取不到 token 而让 metrics 模块失败。

当前系统中 `researcher_tools` 已经能拿到 allowed tool calls 和 overflow tool calls，适合统计工具调用效率。

## 模块边界

该模块不直接修改搜索策略，只输出效率指标供 Stop Decision 模块使用。

---

# 十、模块 9：Process Metrics Trace 模块

## 概述

该模块负责把每轮指标打印出来，用于人工观察、调参和后续评估。

当前项目已有 `process_print`，可打印 event、round、name、id、parent、concurrency、tools 等字段。 可以在此基础上新增 `process_metrics_print`。

接入前应先统一 `print_process_info` 默认关闭的行为，避免 metrics trace 在默认运行中污染输出。`process_metrics_print` 应复用现有 trace 开关；未开启时保持完全静默。

## 原理

在真正启用硬早停之前，必须先看系统真实运行轨迹。

否则容易出现两个问题：

1. 阈值过严，导致研究提前停止；
2. 阈值过松，继续发生 token bloat。

## 实现方案

建议打印格式：

```text
[METRICS] round=supervisor:2 researcher=researcher:1
tokens=48320 tool_calls=6 search_calls=3
claims=9 new_claims=4 supported_new_claims=3
unique_urls=8 duplicate_url_ratio=0.33
coverage_gain=0.17 marginal_gain=0.31
tokens_per_claim=16106 tool_calls_per_claim=2.0
decision=continue
```

## 模块边界

该模块只做观测，不参与流程控制。

它也不应打印搜索正文、summary 正文、compression 正文或 final report 正文，只输出计数、比例、decision 和 reason。

---

# 十一、模块 10：Soft Stop Decision 模块

## 概述

该模块负责根据指标给出停止建议，但不强制中断研究流程。

## 原理

软早停是从“可观测”走向“可控制”的过渡阶段。

系统仍然允许 supervisor 自己决定是否继续，但会在 trace 中标记：

```text
decision=should_stop
reason=low_marginal_gain
```

## 实现方案

建议软停止条件：

```text
marginal_gain < 0.15
或 supported_new_claims == 0
或 duplicate_url_ratio > 0.70
或 tool_calls_per_claim > 5
或 tokens_per_claim > 30000
```

输出：

```json
{
  "decision": "should_stop",
  "reason": "low_marginal_gain_and_high_duplicate_ratio",
  "severity": "warning"
}
```

## 模块边界

该模块不改变 LangGraph 路由，只影响日志、调试信息和后续策略提示。

软停止条件应视为提示，不应让 `marginal_gain`、`tokens_per_claim` 或 `duplicate_url_ratio` 中任一单项直接中断流程。

---

# 十二、模块 11：Hard Stop & Budget Gate 模块

## 概述

该模块负责在信息增益持续过低、工具效率过差或预算超限时，强制停止继续研究。

## 原理

硬停止不能只看单轮结果，因为某一轮低增益可能只是短期波动。

更稳妥的方式是：

```text
连续 N 轮低增益 + 当前覆盖率已达到最低要求 → 停止
```

## 实现方案

建议条件：

```text
if low_gain_streak >= 2:
    stop current researcher

if global_coverage >= 0.80
and recent_marginal_gain < 0.15
and recent_supported_new_claims <= 2:
    stop supervisor research loop

if total_tokens > token_budget:
    stop research and enter final_report
```

停止后不要直接失败，而是进入 final report，并要求报告区分：

```text
- 已确认结论
- 证据不足结论
- 未能确认的信息
```

## 模块边界

该模块是第一个会改变原 LangGraph 流程的模块。建议只在前面观测数据稳定后启用。

启用前置条件：

1. 阶段 A/B/C/D 已在若干真实任务或小评估集上跑过；
2. 阈值经过校准，能解释误停和漏停；
3. `supervisor_tools` 的过宽异常处理已修正，避免把异常失败误判成低增益停止；
4. 配置默认仍应关闭 hard stop，由用户或评估配置显式开启。

---

# 十三、模块 12：Evaluation & Calibration 模块

## 概述

该模块负责用评估集校准阈值，避免信息增益控制误伤正常深度研究。

## 原理

不同任务的合理 token 和工具调用差异很大：

| 任务类型                     | 合理成本 |
| ------------------------ | ---- |
| 简要事实查询                   | 很低   |
| 三方产品比较                   | 中低   |
| 技术方案设计                   | 中等   |
| 学术综述                     | 较高   |
| exhaustive deep research | 很高   |

因此阈值不应一次写死，而应按任务复杂度和评估结果调整。

## 实现方案

建议评估以下指标：

| 指标                   | 含义              |
| -------------------- | --------------- |
| final_quality_score  | 最终答案质量          |
| faithfulness         | claim 是否有证据支撑   |
| coverage             | 用户要求维度覆盖率       |
| total_tokens         | 总 token         |
| total_tool_calls     | 总工具调用数          |
| tokens_per_claim     | 单个有效 claim 成本   |
| tool_calls_per_claim | 单个有效 claim 工具成本 |
| duplicate_url_ratio  | 重复搜索比例          |
| early_stop_success   | 早停后质量是否未明显下降    |
| over_stop_rate       | 是否过早停止          |
| under_stop_rate      | 是否没有及时停止        |

## 模块边界

该模块不参与单次运行的决策，只用于离线调参和回归测试。

---

# 十四、多阶段与模块协作模块：Integration Orchestrator

## 概述

该模块用于指导编码智能体将以上模块有机接入原系统，而不是各自孤立实现。

核心目标：

```text
让 metrics 贯穿 researcher、compression、supervisor、final_report，
但不破坏原有消息流和压缩链路。
```

## 协作原理

整体数据流应保持“双轨制”：

```text
主内容轨：
researcher_messages
  → tool outputs
  → compressed_research
  → supervisor_messages
  → final_report

过程指标轨：
tool usage
  → extracted claims/sources
  → round_metrics
  → information_gain
  → stop_decision
  → trace/eval
```

主内容轨负责生成最终答案；过程指标轨负责控制成本和搜索深度。

二者应相互影响，但不能混在一起。

---

## 1. 推荐集成顺序

### Step 1：扩展 state

在 `state.py` 中加入：

```text
ClaimRecord
SourceRecord
RoundMetrics
GlobalResearchMetrics
claim_registry
source_registry
round_metrics
research_dimensions
ResearcherOutputState 中对应的输出字段
```

目标：先让系统有地方存过程指标。

---

### Step 2：扩展 configuration

在 `configuration.py` 中加入可配置阈值：

```text
enable_research_metrics
enable_soft_gain_stop
enable_hard_gain_stop
min_marginal_gain
low_gain_patience
max_tokens_per_claim
max_tool_calls_per_claim
max_duplicate_url_ratio
min_global_coverage_to_stop
token_budget
```

目标：允许渐进启用，而不是一次强制改动。`enable_research_metrics` 可以先默认关闭或只随 `print_process_info` 开启；`enable_soft_gain_stop`、`enable_hard_gain_stop` 必须默认关闭，尤其 hard stop 不应作为初始默认行为。

---

### Step 3：新增 metrics utilities

在独立 `metrics.py` 中加入：

```text
extract_usage
canonicalize_claim
deduplicate_claims
classify_source_quality
compute_coverage_gain
compute_marginal_gain
compute_efficiency_metrics
process_metrics_print
```

目标：避免把指标逻辑塞进 `deep_researcher.py` 主流程。第一阶段的 utilities 不应引入 embedding、向量库或额外外部模型调用；claim/source 抽取可先基于 `compressed_research` 的 Sources 区域和简单规则实现，必要时再加可配置的模型抽取。

---

### Step 4：接入 compress_research 后处理

在 `compress_research` 成功生成 `compressed_research` 后，调用：

```text
extract_research_metrics
```

返回：

```text
compressed_research
raw_notes
claims
sources
metrics
```

目标：保留原有压缩结果，同时增加结构化指标。

必须使用失败降级：抽取失败时返回空 `claims/sources/metrics`，但仍保留 `compressed_research` 和 `raw_notes`。

---

### Step 5：接入 supervisor_tools 汇总

在 `supervisor_tools` 中接收 researcher 返回结果时：

```text
compressed_research → 继续作为 ToolMessage 给 supervisor
claims/sources/metrics → 写入 hidden state
```

目标：不改变 supervisor 的可见研究材料，但让系统拥有全局过程视角。

---

### Step 6：接入 soft stop

先只打印：

```text
decision=continue / should_stop
reason=...
```

目标：观察指标是否合理，不立即改变流程。

---

### Step 7：接入 hard stop

当 soft stop 经过评估后稳定，再启用硬停止：

```text
researcher 级低增益 → 进入 compress_research
supervisor 级低增益 → 结束研究
全局预算超限 → 进入 final_report
```

目标：真正抑制 token bloat 和 fan-out 失控。

启用前必须先修正过宽异常处理，并确保 hard stop 由配置显式开启。

---

## 2. 模块依赖关系

```text
Research Dimension Extraction
        ↓
Claim & Source Extraction
        ↓
Claim Dedup & Source Registry
        ↓
Coverage Gain + Authority Gain
        ↓
Information Gain Scoring
        ↓
Efficiency Metrics
        ↓
Soft Stop / Hard Stop
        ↓
Final Report / Evaluation
```

其中：

* `Claim & Source Extraction` 依赖 `compressed_research`；
* `Coverage Gain` 依赖 `research_dimensions`；
* `Information Gain` 依赖 claim/source/coverage 结果；
* `Stop Decision` 依赖 information gain 和 efficiency metrics；
* `Evaluation & Calibration` 依赖完整运行日志。

---

## 3. 编码智能体实现约束

编码智能体实现时应遵守以下约束：

1. **不要删除现有 compression prompt**；
2. **不要强制 researcher 直接输出结构化 JSON**；
3. **不要把 metrics 大段写入 supervisor_messages**；
4. **不要在第一阶段启用硬早停**；
5. **不要让单个指标单独决定停止**；
6. **不要让 claim 抽取失败导致主流程失败**；
7. **metrics 模块失败时应降级为只运行原流程**；
8. **所有 stop decision 必须可解释，输出 reason**。
9. **不要在第一阶段引入 embedding 或新外部模型依赖**；
10. **不要假设 token usage 一定可用**，不可用时应标记为 `unavailable` 或 `estimated`。

---

## 4. 推荐最终运行逻辑

```text
1. 用户输入
2. 生成 research_brief
3. 生成 research_dimensions
4. supervisor 决定是否 ConductResearch
5. researcher 搜索与工具调用
6. researcher_tools 记录 tool_calls
7. tavily_search / summarization 记录 source 与 token
8. compress_research 生成 compressed_research
9. extract_research_metrics 抽取 claims/sources/coverage
10. compute_marginal_gain 计算本轮信息增益
11. compute_efficiency_metrics 计算 tokens/tool_calls per claim
12. process_metrics_print 打印过程指标
13. soft stop 只输出建议，不改变路由
14. hard stop 在显式开启且校准后才判断是否改变路由
15. 若继续，回到 supervisor
16. 若停止，进入 final_report
17. final_report 基于 findings 生成最终答案
18. 输出 run-level metrics 供评估
```

---

## 5. 最终验收标准

完成后，系统应能回答以下问题：

```text
这一轮为什么继续？
这一轮为什么停止？
本轮新增了哪些 claim？
这些 claim 来自哪些 URL？
本轮是否覆盖了新的用户需求维度？
本轮用了多少 token？
本轮调用了多少工具？
每个有效 claim 消耗多少 token？
每个有效 claim 消耗多少工具调用？
是否出现重复搜索？
是否出现低增益继续搜索？
是否触发预算熔断？
```

如果这些问题都能从 trace 和 state 中回答，说明信息增益控制模块已经成功融入原系统。

---

# 十五、最终实施建议

参考资料层面，Anthropic 多智能体 Research 复盘、LangChain multi-agent/context engineering、RAGAS faithfulness/context recall 和 LangSmith cost tracking 足以支撑本方案的概念方向。但目前没有可直接照搬到本仓库的完整 Information Gain Gate 参考实现。实际落地应以本仓库已有的 `compress_research`、`process_print` 和 `tests/test_research_limits.py` 为本地参考，小步扩展并用 fake-based 单测验证。

建议编码智能体按以下优先级实施：

```text
优先级 1：Research Metrics State
优先级 2：Claim & Source Extraction
优先级 3：Round Metrics 统计
优先级 4：Metrics Trace 打印
优先级 5：Information Gain Scoring
优先级 6：Soft Stop
优先级 7：Hard Stop
优先级 8：Evaluation & Calibration
```

最重要的设计取向是：

> 先让系统知道“每轮研究值不值”，再让系统决定“是否继续研究”。
