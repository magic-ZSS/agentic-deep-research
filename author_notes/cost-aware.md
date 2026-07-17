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






# 基于混合判断器的 DeepResearch 研究收益治理方案

## 1. 改造名称

**Hybrid Research Gain Governance（基于混合判断器的研究收益治理）**

本方案在现有 DeepResearch 多智能体架构上增加一套轻量但完整的研究过程控制机制，通过“确定性指标计算 + 一次轻量 LLM 语义判断”综合评估每轮研究的实际收益，用于抑制：

* token bloat（Token 膨胀）；
* research fan-out（研究过度扇出）；
* 重复搜索与重复来源；
* 局部过度探索；
* 任务维度覆盖失衡；
* 低收益研究继续消耗工具和模型预算。

本次改造不替换现有 Supervisor、Researcher、Tavily、Compression 和 Final Report 主链路，不要求 Researcher 直接输出 Evidence Card，也不重新设计原项目的研究范式。

---

## 2. 核心目标

现有系统主要依赖最大并发数、最大工具轮次和最大研究轮次控制成本。这些硬限制能够防止无限运行，但不能判断：

```text
当前研究是否已经获得足够信息？
最近一轮是否真正新增了有效证据？
继续搜索的预期收益是否仍高于其成本？
当前缺失的是新证据，还是特定任务维度？
```

本方案的目标是让系统形成以下闭环：

```text
研究
→ 压缩研究结果
→ 抽取新增证据与覆盖状态
→ 计算研究收益和成本
→ 判断继续、定向补缺或停止
→ 返回 Supervisor
```

系统最终不再只是“达到上限后停止”，而是：

> **硬限制负责保证系统不会失控，研究收益判断负责让系统在低收益时提前收敛。**

---

## 3. 设计原则

### 3.1 保留现有主流程

现有流程继续作为主内容轨：

```text
researcher
  ↓
researcher_tools
  ↓
compress_research
  ↓
supervisor_tools
  ↓
supervisor
  ↓
final_report_generation
```

改造后只在 `compress_research` 之后增加一个轻量节点：

```text
researcher
  ↓
researcher_tools
  ↓
compress_research
  ↓
evaluate_research_gain       ← 新增混合判断节点
  ↓
supervisor_tools
  ↓
supervisor
```

`compressed_research` 仍然是 Supervisor 可见的主要研究结果，不修改其基本文本格式。

### 3.2 每个 Researcher 最多增加一次轻量模型调用

`evaluate_research_gain` 每个完成研究的 Researcher 只调用一次判断模型，不在每次工具调用、每个网页或每条 Claim 上单独调用模型。

该调用必须满足：

```text
低成本模型
结构化输出
低输出长度
不执行搜索
不读取原始网页
不读取完整 researcher_messages
不生成长篇分析
失败时退回数学指标和原流程
```

建议限制：

```text
temperature = 0
max_tokens = 800–1200
max_retries = 1
timeout = 60 秒
```

判断模型只接收：

* `research_brief`；
* 当前 `compressed_research`；
* 已有研究维度及其覆盖状态；
* 已有证据摘要或 Claim 指纹；
* 本轮确定性指标；
* 当前剩余预算。

不传入原始网页正文和完整工具历史，避免判断器自身形成新的上下文膨胀。

### 3.3 数学计算与 LLM 各司其职

确定性代码负责计算可精确统计的事实：

* Token；
* 工具调用数；
* 搜索调用数；
* URL 数量和重复率；
* 新来源比例；
* 剩余预算；
* 连续低收益次数。

LLM 判断器负责处理代码难以稳定判断的语义问题：

* 本轮新增了哪些有效 Claim；
* Claim 是否有明确来源支持；
* 是否补充了新的任务维度；
* 新信息是否只是已有结论的改写；
* 当前仍缺哪些关键维度；
* 下一步应该继续、定向补缺还是停止。

最终决策由两者综合完成，不能由单一指标或单次 LLM 意见直接决定。

### 3.4 默认只观测，不立即改变路由

新增治理功能必须支持以下模式：

```text
off      完全关闭
observe  只计算和记录
soft     给出建议，但不强制停止
hard     满足条件时改变路由
```

默认使用 `observe`，待真实任务完成校准后再开启 `soft` 或 `hard`。

### 3.5 失败时保持原流程

以下任何情况均不得导致研究流程失败：

* Token usage 不可获得；
* URL 解析失败；
* LLM 判断超时；
* 结构化输出解析失败；
* Claim 抽取失败；
* 状态合并失败。

判断器失败时应返回：

```text
decision = "unknown"
decision_source = "fallback"
```

随后继续执行原有 Supervisor 流程。

---

## 4. 总体结构：主内容轨与治理轨

```text
主内容轨：

tool outputs
  → compress_research
  → compressed_research
  → supervisor
  → final_report


治理轨：

tool call / query / URL / token
  + compressed_research
  + research_brief
  + existing evidence digest
  ↓
evaluate_research_gain
  ↓
EvidenceItem
CoverageStatus
RoundAssessment
  ↓
continue / targeted_continue / should_stop
```

治理状态保存在 hidden state 中，不把完整指标、Claim 注册表或长篇判断过程塞入 `supervisor_messages`。

Supervisor 最多接收一段紧凑状态：

```text
Research status:
- 本轮新增有效证据：3
- 新覆盖维度：默认参数建议
- 尚缺维度：评估方法
- 来源重复率：67%
- 综合收益：中等
- 建议：仅针对“评估方法”继续研究
```

---

## 5. 核心模块

为减少修改量，原方案中的多个模块合并为四个核心模块。

| 核心模块                      | 主要职责                    |           是否调用模型 |
| ------------------------- | ----------------------- | ---------------: |
| `ResearchTelemetry`       | 记录工具、URL、Token 和预算      |                否 |
| `ResearchGainEvaluator`   | 抽取 Claim、来源、覆盖状态并给出语义判断 | 每个 Researcher 一次 |
| `ResearchDecisionGate`    | 综合数学指标与 LLM 判断          |                否 |
| `ResearchGovernanceTrace` | 输出过程指标并支持离线评估           |                否 |

---

## 6. 模块一：ResearchTelemetry

### 6.1 作用

该模块复用现有 `researcher_tools` 和模型响应，记录每个 Researcher 的基础过程数据。

建议记录：

```python
class ResearchTelemetry(BaseModel):
    tool_calls: int = 0
    search_calls: int = 0
    queries: int = 0

    total_urls: int = 0
    unique_urls: int = 0
    new_urls: int = 0
    duplicate_urls: int = 0
    duplicate_url_ratio: float = 0.0

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    token_source: str = "unavailable"

    elapsed_seconds: float | None = None
```

### 6.2 URL 治理

URL 需要进行基础规范化：

```text
统一 host 大小写；
移除 fragment；
移除末尾无意义斜杠；
删除常见 tracking 参数；
保留可能改变正文内容的 query 参数。
```

计算：

```text
new_source_ratio =
new_urls / max(unique_urls, 1)

duplicate_url_ratio =
duplicate_urls / max(total_urls, 1)
```

URL 新颖性只是研究收益的一个客观指标，不能直接等同于信息增益。

### 6.3 Token 数据

Token 统计优先读取模型响应的 `usage_metadata`：

```text
exact       模型提供精确 usage
estimated   由 tokenizer 或字符数估算
unavailable 无法获取
```

无法获取时保留空值，不得为了获得 Token 数再次调用模型。

---

## 7. 模块二：ResearchGainEvaluator

### 7.1 节点职责

该节点位于 `compress_research` 之后，使用一次轻量结构化模型调用，对当前压缩结果进行综合判断。

它同时完成原方案中以下工作：

* Research Dimension Extraction；
* Claim & Source Extraction；
* Claim 语义去重判断；
* Coverage Gain 判断；
* Source Quality 粗分类；
* LLM Research Gain 判断。

这样无需分别增加多个节点和多次模型调用。

### 7.2 结构化输出

建议只保留两个主要数据结构。

```python
class EvidenceItem(BaseModel):
    claim: str
    source_urls: list[str]
    dimension: str | None = None

    support_status: str = "unknown"
    # supported / partial / unsupported / conflicting / unknown

    novelty: str = "unknown"
    # new / reinforcing / duplicate / unknown

    source_quality: str = "unknown"
    # primary / authoritative_secondary / other / unknown
```

```python
class ResearchAssessment(BaseModel):
    evidence_items: list[EvidenceItem] = []

    covered_dimensions: list[str] = []
    newly_covered_dimensions: list[str] = []
    missing_dimensions: list[str] = []

    llm_gain_score: float = 0.0
    # 0–1，只作为辅助判断

    recommendation: str = "continue"
    # continue / targeted_continue / should_stop / unknown

    recommended_focus: str | None = None
    reason: str = ""
```

每次最多返回 5–8 条最重要的 EvidenceItem，不要求穷举所有句子，避免输出过长。

### 7.3 Research Dimension 的简化生成

不再单独增加 `research_dimension_extractor` 节点。

第一次调用 `ResearchGainEvaluator` 时：

```text
existing_dimensions = []
```

判断器根据 `research_brief` 同时生成核心任务维度。

后续调用只传入：

```text
dimension_id
dimension_name
coverage_status
```

避免每轮重复生成维度。

对于简单任务，可以只生成一个总体维度；对于具有明确输出要求的任务，最多生成 3–8 个核心维度。

### 7.4 Evidence Digest

为了判断本轮 Claim 是否新增，需要向判断器提供已有证据摘要，但不能传入全部历史研究文本。

建议维护紧凑的 `evidence_digest`：

```text
E1: 多智能体研究 Token 成本显著高于普通聊天。
E2: 当前项目已有 Supervisor、Researcher 和 Tavily 三层并发限制。
E3: 固定轮次只能限制最坏成本，不能动态判断收益。
```

限制：

```text
最多保存 30–50 条核心 Claim；
每条 Claim 保持一句话；
相同或高度相似 Claim 合并；
不重复保存完整来源正文。
```

---

## 8. 模块三：混合研究收益计算

### 8.1 数学部分

建议保留四个核心量：

```text
evidence_yield =
本轮新增且有来源支撑的 Claim 数
÷ max(本轮抽取的有效 Claim 数, 1)

source_novelty =
new_urls
÷ max(unique_urls, 1)

coverage_gain =
本轮新增覆盖的必要维度权重
÷ 全部必要维度权重

cost_pressure =
min(本轮成本 ÷ 当前轮次预算, 1)
```

来源质量可简化为：

```text
primary_source_ratio =
新增一手或权威来源数
÷ max(new_urls, 1)
```

确定性收益分：

```text
deterministic_gain =
0.40 × evidence_yield
+ 0.25 × coverage_gain
+ 0.20 × source_novelty
+ 0.15 × primary_source_ratio
```

成本不直接与收益混为同一个概念，而作为独立决策条件：

```text
cost_efficiency =
deterministic_gain
÷ max(cost_pressure, minimum_cost_floor)
```

这些权重只是初始工程参数，必须通过评估集校准，不能表述为普适理论常数。

### 8.2 LLM 部分

判断器输出：

```text
llm_gain_score ∈ [0, 1]
recommendation
missing_dimensions
reason
```

LLM 主要判断：

* 新 Claim 是否具有真实语义增量；
* 是否只是在重复或改写已有结论；
* 新来源是否真正支持 Claim；
* 关键维度是否仍然缺失；
* 是否值得继续寻找更高质量证据。

### 8.3 综合得分

建议初始采用：

```text
hybrid_gain =
0.60 × deterministic_gain
+ 0.40 × llm_gain_score
```

数学指标权重更高，避免一次模型判断直接控制研究流程。

但最终停止不能只比较一个 `hybrid_gain` 数字，还必须同时检查：

```text
必要维度是否已经充分覆盖；
是否仍存在关键证据冲突；
是否还有明确且可执行的定向搜索目标；
是否触发硬预算；
是否连续多轮低收益。
```

---

## 9. 模块四：ResearchDecisionGate

### 9.1 决策类型

```text
continue
继续正常研究

targeted_continue
只针对缺失维度或证据冲突继续研究

should_stop
当前信息已经基本充分，建议停止

force_stop
达到硬预算，必须停止

unknown
判断器失败，退回原有 Supervisor 决策
```

### 9.2 决策规则

#### 继续研究

满足以下任一情况：

```text
存在尚未覆盖的必要维度；
存在关键 Claim 证据不足或相互冲突；
本轮新增了重要一手来源；
判断器给出了明确、具体的下一步搜索方向。
```

#### 定向继续

```text
总体覆盖已经较高，
但仍有少量明确缺口，
且 suggested_focus 可以转化为具体子任务。
```

此时 Supervisor 不应再次进行宽泛研究，而应只针对缺口发起 `ConductResearch`。

#### 建议停止

建议初始规则：

```text
必要维度覆盖充分
AND 不存在关键证据冲突
AND hybrid_gain < min_hybrid_gain
AND 连续 low_gain_patience 轮低收益
```

#### 强制停止

```text
达到总 Token 预算
OR 达到总工具调用预算
OR 达到现有最大研究轮次
```

LLM 无权绕过硬预算。

### 9.3 Researcher 局部停止

为了控制当前 Researcher 内部的重复搜索，同时又避免增加额外 LLM 调用，可以保留一条简单的确定性规则：

```text
连续两轮搜索没有新增 URL
且重复 URL 比例超过阈值
→ 提前进入 compress_research
```

该规则只处理非常明显的重复搜索，不进行语义 Claim 判断。

复杂的语义收益判断统一在 `compress_research` 后执行一次。

---

## 10. 状态设计

为了降低代码修改量，不再引入大量独立 Registry 类型。

建议新增以下状态字段：

```text
research_dimensions
evidence_digest
seen_urls
research_assessments
low_gain_streak
research_decision
research_stop_reason
```

其中：

```text
ResearcherState：
- seen_urls
- local_low_yield_streak
- telemetry

ResearcherOutputState：
- compressed_research
- raw_notes
- assessment
- telemetry

SupervisorState：
- research_dimensions
- evidence_digest
- research_assessments
- seen_urls
- low_gain_streak
- research_decision
```

并行 Researcher 完成后，由 `supervisor_tools` 在 fan-in 阶段统一合并：

* URL 集合取并集；
* EvidenceItem 按 Claim 和来源合并；
* Dimension coverage 取最高状态；
* Assessment 按 Researcher 分开保留；
* 不让并行子图直接修改同一个全局 Registry。

---

## 11. 模型配置

建议增加独立判断模型配置：

```python
research_gain_model: str
research_gain_model_max_tokens: int = 1200
research_gain_timeout_seconds: int = 60
research_gain_max_retries: int = 1
```

模型要求：

```text
成本较低；
支持结构化输出；
中文和英文 Claim 抽取稳定；
能够进行基本语义去重；
不需要极强长程推理；
不应使用 Final Report 级高价模型。
```

判断器属于中低复杂度、高频但短输出节点，适合使用低成本、结构化能力稳定的模型。

---

## 12. 配置项

```python
enable_research_governance: bool = True

research_governance_mode: str = "observe"
# off / observe / soft / hard

enable_local_duplicate_stop: bool = False

local_duplicate_patience: int = 2
max_duplicate_url_ratio: float = 0.75

min_hybrid_gain: float = 0.20
global_low_gain_patience: int = 2
min_required_coverage_to_stop: float = 0.80

max_evidence_digest_items: int = 50
max_evidence_items_per_round: int = 8

print_research_metrics: bool = False
```

阈值均为待评估的初始值，不作为权威固定标准。

---

## 13. 分阶段实施

### 阶段 0：文档一致性修补

当前暂不修改其他既有流程问题，只完成：

```text
统一 print_process_info 的代码默认值与文档描述；
同步 configuration、README、progress 和 session-handoff 中的说明。
```

---

### 阶段 1：Telemetry 与状态基础

不改变 LangGraph 路由。

实现：

```text
统计工具调用、搜索、query、URL 和 Token；
维护 seen_urls；
增加 ResearchTelemetry；
增加 metrics trace；
扩展 ResearcherOutputState。
```

验收：

```text
治理功能关闭或 observe 时，最终输出与原流程保持兼容。
```

---

### 阶段 2：混合判断节点

增加：

```text
compress_research
→ evaluate_research_gain
→ END
```

实现一次轻量结构化调用，输出：

```text
EvidenceItem
ResearchAssessment
research_dimensions
```

此阶段仅记录，不影响路由。

同时记录判断器自身的：

```text
input tokens
output tokens
latency
cost
failure rate
```

用于判断新增节点是否物有所值。

---

### 阶段 3：软控制

将判断结果压缩为短状态交给 Supervisor：

```text
recommendation
missing_dimensions
recommended_focus
reason
```

Supervisor 仍保留最终决策权。

重点验证：

```text
是否减少无意义的重复委派；
是否能够从宽泛搜索转向定向补缺；
是否产生新的上下文膨胀。
```

---

### 阶段 4：Researcher 局部硬停止

显式开启后，连续明显重复的搜索轮次提前进入 `compress_research`。

仅依赖：

```text
new_urls
duplicate_url_ratio
local_low_yield_streak
```

不使用 LLM 进行工具轮级判断。

---

### 阶段 5：Supervisor 硬停止

只有在评估集完成校准后开启。

必须同时满足：

```text
必要维度基本覆盖；
无关键证据冲突；
连续多轮低收益；
数学指标与 LLM 判断基本一致；
不存在明确的高价值定向搜索目标。
```

停止后直接进入 Final Report，而不是让任务失败。

可向 Final Report 提供一段短状态：

```text
已覆盖维度；
尚未确认的内容；
证据冲突；
停止原因。
```

---

### 阶段 6：离线校准

使用现有测试用例和 LangSmith trace 比较：

| 版本 | 内容                        |
| -- | ------------------------- |
| V0 | 当前系统                      |
| V1 | Telemetry                 |
| V2 | Telemetry + 混合判断器 Observe |
| V3 | V2 + Soft Control         |
| V4 | V3 + Researcher 局部停止      |
| V5 | V4 + Supervisor 硬停止       |

重点指标：

```text
final_quality_score
faithfulness
citation_accuracy
required_dimension_coverage
total_tokens
total_tool_calls
total_latency
evaluator_overhead
duplicate_source_ratio
early_stop_success
over_stop_rate
under_stop_rate
```

核心有效条件：

```text
最终质量没有明显下降
AND Token、工具调用或耗时下降
AND 判断器新增成本明显小于其节省成本
```

---

## 14. 代码修改范围

### `configuration.py`

新增治理模式、判断模型和阈值配置，并修正 `print_process_info` 默认值与文档不一致。

### `state.py`

新增：

```text
ResearchTelemetry
EvidenceItem
ResearchAssessment
```

以及少量治理状态字段。

### `utils.py`

新增：

```text
extract_urls
normalize_url
compute_research_telemetry
merge_evidence_digest
compute_hybrid_gain
process_research_metrics_print
```

### `prompts.py`

新增一个短的：

```text
research_gain_evaluator_prompt
```

不修改现有 compression prompt 的核心职责。

### `deep_researcher.py`

修改范围集中在三个位置：

```text
researcher_tools
→ 记录局部 Telemetry
→ 可选明显重复早停

compress_research 之后
→ 调用 evaluate_research_gain

supervisor_tools
→ 合并 assessment
→ 计算软/硬决策
```

### `tests/`

至少覆盖：

```text
URL 规范化和全局去重；
Telemetry 统计；
判断器结构化输出；
判断器失败时原流程继续；
并行 Researcher assessment 合并；
observe 模式不改变路由；
soft 模式只提供建议；
hard 模式正确停止；
硬预算优先于 LLM 判断；
evidence_digest 长度受控。
```

---

## 15. 对原项目的影响

| 实施阶段            | 修改强度 | 行为风险 |
| --------------- | ---: | ---: |
| 文档一致性修补         |   很低 |   很低 |
| Telemetry       |    低 |   很低 |
| 混合判断器 Observe   |    中 |    低 |
| Soft Control    |    中 |   中低 |
| Researcher 局部停止 |    中 |    中 |
| Supervisor 硬停止  |   中高 |   中高 |

兼容性原则：

```text
不删除 compression；
不改变 compressed_research 主输出；
不强迫 researcher 输出 JSON；
不向 Supervisor 注入完整 Claim Registry；
不读取原始网页进行二次判断；
不为每个工具或 Claim 单独调用模型；
判断器失败时继续原流程；
硬停止默认关闭；
所有停止决定必须具有 reason。
```

---

## 16. 预期效果

本方案保留了信息增益治理的核心能力：

* Claim 与来源级语义判断；
* 研究维度覆盖追踪；
* 新旧证据判断；
* URL 与工具成本统计；
* 数学收益计算；
* LLM 综合判断；
* Researcher 局部停止；
* Supervisor 全局停止；
* 过程可观测与离线校准。

同时通过以下方式减少修改量：

* 将 Dimension、Claim、Source、Coverage 和语义增益合并到一次判断器调用；
* 不新增 embedding、向量库和外部检索依赖；
* 不在每个工具轮调用 LLM；
* 不重新设计 Compression；
* 不建立过度复杂的多级 Registry；
* 不立即启用硬停止；
* 不修改现有流程问题，仅修补 `print_process_info` 一致性。

---

## 17. 一句话总结

> **基于混合判断器的研究收益治理，通过复用 `compressed_research`，在每个 Researcher 完成后执行一次轻量结构化判断，并将 Claim、来源、覆盖增益等语义信息与 Token、工具调用、URL 新颖性等数学指标结合，在不重构原有 DeepResearch 主链路的前提下，实现可观测、可解释、可校准的研究收敛与成本控制。**
