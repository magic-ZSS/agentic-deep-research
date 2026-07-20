# 基于信息增益的 DeepResearch 过程治理改造

本次改动可命名为 **Information Gain Based Research Governance（基于信息增益的研究过程治理）**。它的核心目标不是替换原有 researcher、compression 或 supervisor 架构，而是在现有 DeepResearch 多智能体系统之上增加一套“过程可观测、成本可度量、研究可早停”的控制机制。当前系统已经具备 researcher 并发、工具调用上限、Tavily 查询限制和 `compress_research` 压缩回传机制；其中 compression 阶段会将 researcher 的工具消息和 AI 消息整理为 `compressed_research`，再交给 supervisor 使用。 因此，本方案的重点不是再强迫 researcher 直接输出 Evidence Card，而是在压缩结果之后抽取 claim、source、coverage 和 cost 等过程指标，判断本轮研究是否真的带来了新的有效信息。

这项改造的直接原因，是 DeepResearch 系统天然容易出现 **token bloat（token 膨胀）**、**fan-out 过度扇出**、重复搜索、工具反馈膨胀和最终报告证据链失真等问题。Anthropic 在其多智能体 Research 系统复盘中指出，多智能体研究适合开放式、宽搜索空间任务，但也会快速消耗 token；其数据表明，普通 agent 约为普通 chat 的 4 倍 token，多智能体系统约为 chat 的 15 倍 token，并且早期系统曾出现“简单问题生成大量 subagents”“无休止搜索不存在来源”“重复工作”等失控现象。 这说明，仅靠固定的最大轮次、最大工具调用数或最大 token 限制，只能防止系统无限运行，却不能判断“继续研究是否值得”。

本方案的启发来自三类成熟实践。第一，Anthropic 强调多智能体系统需要 effort budget、source quality、tool efficiency 和 observability，即必须让系统知道研究投入是否与任务复杂度匹配。 第二，LangChain 将多智能体设计的关键问题归结为 context engineering，即决定每个 agent 应该看到什么上下文，并关注 model calls 与 tokens processed 等成本指标。 LangChain 的 Context Engineering 进一步提出 write、select、compress、isolate 等策略，强调长运行 agent 必须管理上下文写入、选择、压缩和隔离。 第三，RAGAS 的 Faithfulness 指标以 claim 为核心，判断回答中的声明是否能被检索上下文支持，这为“以有效 claim 衡量研究收益”提供了评估依据。

本改造的核心思想是：**将研究过程从“自由探索”转变为“边际收益驱动的受控探索”**。系统不再只记录“用了多少轮、搜了多少次、返回多少网页”，而是进一步计算：本轮新增了多少有来源支撑的 claim，新增来源是否重复，是否覆盖了新的研究维度，来源是否权威，每个有效 claim 消耗了多少 token 和工具调用。由此形成 `marginal_gain_per_round`、`tokens_per_claim`、`tool_calls_per_claim`、`duplicate_url_ratio`、`coverage_gain` 等指标。当连续多轮新增信息很少、重复来源很多、工具调用成本过高或任务维度已基本覆盖时，系统即可触发软停止或硬停止，避免继续无效搜索。

这套机制的价值在于，它把原本模糊的“研究是否充分”拆解为可观察、可计算、可解释的过程判断。对 supervisor 而言，它减少了被冗长 researcher 输出误导的风险；对 researcher 而言，它约束了无边界搜索；对 compression 而言，它不再只是压缩文本，而成为后续 claim/source 抽取的基础；对 final report 而言，它提供了更清晰的证据池和覆盖状态。更重要的是，它不会破坏原系统主流程，而是采用“双轨制”：主内容轨继续传递 `compressed_research`，过程指标轨在 hidden state 中记录 claim、source、coverage 和 cost，从而既保留生成质量，又控制研究成本。

该方案的效用主要体现在四个方面：第一，降低 token 成本，避免为了少量新增信息消耗大量上下文；第二，抑制 fan-out 失控，使 researcher 数量和搜索轮次与任务复杂度匹配；第三，提升证据链质量，让最终报告更多依赖有来源支撑的 claim；第四，增强系统可调试性，使开发者能够从 trace 中看出哪一轮开始低效、哪个 researcher 重复搜索、哪个维度仍未覆盖。它尤其适用于多维度技术调研、产品对比、资料综述和工程方案设计等 research-heavy 任务。

一句话总结：**基于信息增益的研究过程治理，本质上是为 DeepResearch 系统增加一个“研究仪表盘 + 成本刹车系统”，让系统不再追求查得更多，而是追求每一轮查得更有价值、更可引用、更值得继续。**

# detail

你这份改动的本质，不是“再加几个统计字段”，而是给 DeepResearch 系统加一个**研究过程控制闭环**：

```text
原系统：研究 → 压缩 → 汇总 → 写报告

改造后：研究 → 压缩 → 抽取结构化证据 → 计算信息增益 → 判断是否继续 → 汇总/写报告
```

更直观地说，原系统像一个研究小组：每个 researcher 拼命查资料，最后把资料交给 supervisor。现在要加的是一个“研究审计员”：它不替 researcher 查资料，也不替 supervisor 写报告，而是持续问三个问题：

1. 这一轮真的新增了有价值的信息吗？
2. 这一轮新增信息的成本是否合理？
3. 继续查下去，边际收益是否还值得？

这正对应 Anthropic 在多智能体 Research 系统复盘里提到的问题：多智能体研究系统确实适合开放式、多方向搜索任务，但会快速消耗 token；他们的系统中普通 agent 约为 chat 的 4 倍 token，多智能体约为 chat 的 15 倍，并且早期出现过“简单问题生成 50 个 subagents”“无休止搜索不存在来源”“重复工作”等失控行为。Anthropic 后续用 effort scaling、source quality、tool efficiency、observability 等方式约束这些问题。([Anthropic][1])

---

## 一、整体协作模型：主内容轨 + 过程指标轨

你现在的项目已经有比较清晰的主内容轨。`researcher` 调工具，`compress_research` 把 researcher 的工具消息和 AI 消息压缩成 `compressed_research`，再返回给 supervisor；压缩 prompt 也明确要求保留相关信息、来源、URL、行内引用和 Sources 列表。

这次改造不要破坏它，而是在旁边新增一条“过程指标轨”：

```text
主内容轨：
researcher_messages
  → tool outputs
  → compressed_research
  → supervisor_messages
  → final_report

过程指标轨：
tool calls / token usage / compressed_research
  → claims / sources / coverage
  → marginal_gain
  → stop decision
  → trace / eval / budget control
```

为什么要双轨？因为 supervisor 需要的是**可读研究结果**，而系统控制器需要的是**可计算过程指标**。如果把 metrics 塞进 supervisor_messages，会让上下文更臃肿，反而制造新的 token bloat。LangChain 文档也强调，多智能体设计的核心是 context engineering，即决定每个 agent 应该看到什么信息；不同模式的成本要看 model calls 和 tokens processed。([Docs by LangChain][2])

---

## 二、新增部分逐个讲解

### 1. Research Metrics State：研究过程状态层

**概述**：这是新增模块的地基。它给系统加上 `claim_registry`、`source_registry`、`round_metrics`、`research_dimensions` 等字段，用来保存每轮研究的过程数据。

**原理**：Agent 失控不是突然发生在最终报告阶段，而是在每一轮 search、tool call、compression 中逐渐累积。没有状态记录，系统只知道“查了很多”，不知道“查得值不值”。

你当前 `state.py` 里主要有 messages、notes、raw_notes、final_report、research_iterations、tool_call_iterations 等字段，还没有 claim/source/metrics 这类过程状态。 所以系统只能靠 `max_researcher_iterations`、`max_react_tool_calls` 这类硬上限防爆，不能根据“信息收益”动态停止。

**意义作用**：
它让系统第一次能回答这些问题：本轮新增了几个有效结论？用了多少 token？用了多少工具？来源重复了吗？覆盖了新维度吗？

**形象例子**：
没有 metrics state 的系统，像一个老板只问员工：“你忙了吗？”员工说：“很忙。”老板就默认有效。
加了 metrics state 后，老板会问：“你今天新增了几个可交付成果？用了多少小时？是不是和昨天重复？”这才是管理。

---

### 2. Research Dimension Extraction：研究维度拆解层

**概述**：把 research brief 拆成可追踪的任务维度。例如用户要求“总体架构、模型调度、上下文治理、默认参数、评估指标”，系统就生成这些维度列表。

**原理**：完整性不能靠“资料多”判断，而要靠“用户要求是否被覆盖”判断。RAGAS 的 Context Recall 指标也是类似思路：它衡量相关信息是否被成功检索，LLM 版本会把 reference 拆成 claims，再看这些 claims 是否能归因到 retrieved context。([Ragas][3])

**意义作用**：
它是 `coverage_gain` 的基础。如果没有研究维度，系统不知道自己到底还缺哪一块，只能盲目继续搜索。

**形象例子**：
用户让你做一份“装修方案”，包括预算、材料、工期、风格、风险。
一个 researcher 查了 20 篇“北欧风格装修”，资料很多，但预算、工期、风险都没覆盖。
维度拆解层会告诉系统：这不是“研究充分”，而是“一个维度过度展开，其他维度缺失”。

---

### 3. Claim & Source Extraction：结论与来源抽取层

**概述**：从 `compressed_research` 中抽取结构化 claim 和 source。这里不让 researcher 直接输出 Evidence Card，而是在压缩后做后处理。

**原理**：信息增益的基本单位不是“网页”“token”“搜索次数”，而是**有来源支撑的新 claim**。RAGAS Faithfulness 也采用类似思想：先识别回答中的 claims，再判断每个 claim 是否能被 retrieved context 支持。([Ragas][4])

**意义作用**：
它把“自然语言研究结果”变成“可计算研究资产”。只有抽出 claim，后面才能计算 `new_claims`、`supported_new_claims`、`tokens_per_claim`、`tool_calls_per_claim`。

**形象例子**：
`compressed_research` 像一篇会议纪要。
Claim extraction 像助理从会议纪要里抽出行动项：

```text
会议纪要：我们查到 Anthropic 多智能体系统适合宽搜索任务，但 token 成本高。
抽取后：
claim: 多智能体研究适合宽搜索任务，但 token 成本显著增加。
source: Anthropic Engineering Blog
dimension: fan-out 控制依据
```

这样系统才知道这是一条可用证据，而不是一段普通文字。

---

### 4. Claim Dedup & Registry：结论去重与注册层

**概述**：维护全局 claim registry，判断本轮 claim 是新增还是重复。

**原理**：DeepResearch 的一个典型失控模式是“重复性繁荣”：看起来查了很多网页，其实都在说同一件事。Anthropic 也提到，如果 subagent 的任务边界不清，会出现重复工作，例如多个 subagent 查到相同方向而留下其他缺口。([Anthropic][1])

**意义作用**：
它防止系统把“重复表达”误判为“新增信息”。这对控制 fan-out 很关键。

**形象例子**：
三个 researcher 分别查到：

```text
A: Anthropic says multi-agent systems burn through tokens fast.
B: Anthropic says multi-agent systems use about 15× chat tokens.
C: Anthropic reports high token usage in multi-agent research.
```

这三条不是三个独立结论，而是同一个事实的不同说法。Claim dedup 会把它们合并，避免 supervisor 误以为“证据很多，还可以继续扩展”。

---

### 5. Source Registry & Authority：来源注册与权威性层

**概述**：维护全局来源表，判断 URL 是否重复、来源是否权威。

你现在的 `tavily_search` 已经会在单次搜索内部按 URL 去重。 但这只是局部去重，不是跨 researcher、跨轮次的全局来源治理。

**原理**：来源数量不等于证据质量。官方文档、论文、工程博客、论坛、SEO 博客的证据价值不同。Anthropic 的 eval rubric 里也明确包含 source quality，并关注是否优先使用 primary sources，而不是低质量二手来源。([Anthropic][1])

**意义作用**：
它让系统知道：本轮是不是找到了更好的来源，而不是只找到了更多来源。

**形象例子**：
你要证明“LangChain 多智能体设计关注 model calls 和 tokens processed”。
一个官方 LangChain 文档价值很高；五篇 SEO 博客重复转述价值反而低。
Source authority 模块会告诉系统：新增 5 个普通博客，不如新增 1 个官方文档。

---

### 6. Coverage Gain：覆盖增益层

**概述**：衡量本轮研究是否覆盖了新的任务维度。

**原理**：信息增益不只是“新增事实”，还包括“新增覆盖”。如果本轮新增了 10 条 claim，但都属于已经覆盖的维度，那么对最终报告的边际贡献可能很低。

**意义作用**：
它解决“局部过度深入，整体仍不完整”的问题。

**形象例子**：
用户问“设计成本感知模型调度与上下文治理方案”，你需要覆盖：

```text
模型调度
上下文治理
停止条件
fan-out 限制
评估指标
默认参数
```

如果系统连续三轮都在查“模型调度”，但完全没查“评估指标”，coverage gain 会很低，或者指出缺口在“评估指标”。这会引导 supervisor 不要继续泛搜，而是定向补缺。

---

### 7. Information Gain Scoring：信息增益评分层

**概述**：把新增 claim、新增来源、覆盖增益、来源质量、重复惩罚合成一个 `marginal_gain_per_round`。

**原理**：单一指标不可靠。例如只看新 URL，会鼓励系统找大量低质量网页；只看新 claim，会忽略来源权威性；只看 coverage，会忽略证据质量。所以要用混合评分。

推荐公式仍然是工程化的：

```text
marginal_gain =
  0.40 × new_supported_claim_ratio
+ 0.25 × new_source_ratio
+ 0.20 × coverage_gain
+ 0.15 × authority_gain
- 0.20 × duplicate_penalty
```

**意义作用**：
它把“是否值得继续研究”从模型主观判断变成半确定性流程判断。

**形象例子**：
一轮研究新增了 8 个 claim、5 个新官方来源、补上了两个缺失维度，增益高。
另一轮研究新增了 20 个网页，但 18 个重复、claim 都相似、没有覆盖新维度，增益低。
这就是信息增益评分要区分的情况。

---

### 8. Efficiency Metrics：效率指标层

**概述**：计算 `tokens_per_claim`、`tool_calls_per_claim`、`duplicate_url_ratio` 等指标。

**原理**：信息增益高不代表成本合理。某轮也许确实找到 1 个新结论，但如果花了 80K token 和 12 次工具调用，这一轮仍然不健康。

LangSmith 的成本追踪支持按 trace tree 查看 token 和 cost，能把 input、output、tool/retrieval 等成本拆开，也支持通过 `usage_metadata` 上传 token 或工具成本。([Docs by LangChain][5]) 这正好支撑你实现 `tokens_per_claim` 和工具成本统计。

**意义作用**：
它回答的是“这条新信息贵不贵”。

**形象例子**：
两个 researcher 都新增了 3 个有效 claim：

```text
Researcher A: 12K token + 3 次工具调用 → 每 claim 4K token
Researcher B: 90K token + 18 次工具调用 → 每 claim 30K token
```

最终报告质量可能差不多，但 B 是明显低效路径。这个指标能让你发现哪个阶段、哪个 researcher、哪类任务最容易烧钱。

---

### 9. Process Metrics Trace：过程指标日志层

**概述**：把每轮指标打印出来，形成可观察轨迹。

你当前项目已有 `process_print`，可以打印 event、round、name、id、parent、concurrency、tools 等字段。 这个模块就是在现有 trace 体系上新增 metrics trace。

**原理**：Agent 系统具有非确定性，同一个输入可能走出不同路径。Anthropic 也强调，多智能体系统很难用固定步骤评估，应关注结果是否正确以及过程是否合理，并且生产调试需要 tracing 和高层 observability。([Anthropic][1])

**意义作用**：
它让你能复盘“为什么 1.2M token 被烧掉”。没有 trace，你只能看到最终报告；有 trace，你能看到哪一轮开始低效、哪个 researcher 重复搜索、哪个工具调用没有产出。

**形象例子**：
没有 metrics trace，账单来了你只知道“花了很多”。
有 metrics trace，你能看到：

```text
第 1 轮：新增 12 个 claim，正常
第 2 轮：新增 5 个 claim，正常
第 3 轮：新增 1 个 claim，重复 URL 70%
第 4 轮：新增 0 个 claim，仍继续搜索
```

问题立刻定位到第 3–4 轮。

---

### 10. Soft Stop Decision：软停止层

**概述**：系统先不强制停止，只给出 `should_stop` 建议。

**原理**：刚开始阈值不一定准。如果一上来就硬停，可能误伤复杂任务。所以第一阶段只打印建议，观察 10–20 个用例后再启用强控制。

**意义作用**：
它是安全过渡层，避免工程改动过猛。

**形象例子**：
软停止就像汽车的疲劳驾驶提醒：它先提示“建议休息”，但不直接帮你刹车。你可以先观察它提醒是否准确。

---

### 11. Hard Stop & Budget Gate：硬停止与预算闸门

**概述**：当连续低增益、重复率高、成本超限时，强制结束当前 researcher 或整个研究阶段。

**原理**：硬上限只能防止无限循环，不能防止“在上限内浪费大量 token”。信息增益闸门是动态上限：如果第 2 轮已经低增益，就不必等到 `max_react_tool_calls=5` 才停。

你的项目目前已有几类硬限制：`max_concurrent_research_units` 默认 3，`max_concurrent_researcher_tool_calls` 默认 3，`max_queries_per_search_call` 默认 3，`max_researcher_iterations` 默认 3，`max_react_tool_calls` 默认 5。 这些限制有效，但仍然是“数量上限”，不是“收益判断”。

**意义作用**：
它真正阻止 token bloat 和 fan-out 失控。

**形象例子**：
原系统像规定员工“最多加班 5 小时”。
信息增益硬停像规定：“如果连续 2 小时没有新增有效成果，就停止加班。”
后者明显更合理。

---

### 12. Evaluation & Calibration：评估校准层

**概述**：用你的测试用例集来校准阈值，例如 `min_marginal_gain=0.15`、`max_tokens_per_claim=30000` 是否合适。

**原理**：不同任务的合理成本不同。简要比较任务和 exhaustive literature review 不能用同一阈值。Anthropic 也提到，他们用真实查询样本、小规模 eval、LLM judge、人工测试共同迭代多智能体系统。([Anthropic][1])

**意义作用**：
防止系统“该停不停”或“不该停乱停”。

**形象例子**：
如果你用“简要对比任务”的阈值去控制“系统性文献综述”，系统可能太早停止。
如果用“文献综述”的阈值去控制“简单 MCP 介绍”，系统又会过度研究。
Evaluation & Calibration 就是给不同任务类型调节“刹车灵敏度”。

---

## 三、数据如何流转

完整数据流可以这样理解：

```text
1. 用户输入
   ↓
2. write_research_brief 生成 research_brief
   ↓
3. research_dimension_extractor 拆出研究维度
   ↓
4. supervisor 判断是否分发 researcher
   ↓
5. researcher 调 tavily_search / think_tool / MCP
   ↓
6. researcher_tools 记录工具调用数量
   ↓
7. tavily_search 返回结果，并做单次 URL 去重
   ↓
8. summarize_webpage 对网页做摘要
   ↓
9. compress_research 清洗 researcher 全部发现
   ↓
10. claim_source_extractor 从 compressed_research 抽 claim/source
   ↓
11. claim_registry/source_registry 做去重与来源质量判断
   ↓
12. coverage_gain 判断是否补足新维度
   ↓
13. information_gain_scoring 计算 marginal_gain
   ↓
14. efficiency_metrics 计算 tokens_per_claim/tool_calls_per_claim
   ↓
15. soft/hard stop 判断是否继续研究
   ↓
16. 若继续：回到 supervisor
   ↓
17. 若停止：final_report_generation
```

这个流程的关键不是“多了一个 evaluator”，而是把原来模糊的研究过程拆成了四个可控对象：

| 对象       | 问题                      |
| -------- | ----------------------- |
| claim    | 新增了什么结论？                |
| source   | 来源是否新增且权威？              |
| coverage | 用户要求是否覆盖？               |
| cost     | 这些新增信息花了多少 token 和工具调用？ |

---

## 四、系统什么时候会生效

它最容易在以下场景生效：

### 1. 多维度技术研究

比如你之前的“成本感知模型调度与上下文治理方案”。这种任务维度明确，系统可以判断哪些维度已覆盖、哪些没覆盖。

**生效方式**：
如果前两轮已经覆盖“总体架构、上下文治理、模型分离”，但还缺“评估指标”，系统会引导下一轮定向补“评估指标”，而不是继续泛搜模型调度。

### 2. 三方/多方产品比较

比如 OpenAI Deep Research、Gemini Deep Research、Perplexity Research 对比。

**生效方式**：
系统能发现 Perplexity 维度缺失，要求补查，而不是只在 OpenAI/Gemini 上越查越多。

### 3. 来源重复严重的任务

比如多个 query 都返回同一批官方文档或同一篇博客。

**生效方式**：
`duplicate_url_ratio` 升高，`new_source_ratio` 降低，触发低增益，阻止继续重复搜索。

### 4. 工具调用膨胀任务

比如 researcher 连续调用 Tavily，但每轮只新增 0–1 个有效 claim。

**生效方式**：
`tool_calls_per_claim` 和 `tokens_per_claim` 升高，系统判断继续搜索不划算。

---

## 五、系统什么时候不生效或效果有限

这部分很重要，因为信息增益控制不是万能的。

### 1. 用户任务本身没有明确维度

例如：“帮我深入研究一下 AI 的未来。”

这种任务太开放，`research_dimensions` 很难稳定，coverage gain 会不可靠。

**解决方式**：
需要 clarification 或 brief generation 把任务收窄。

### 2. 创意写作/主观建议类任务

例如：“帮我写一篇有感染力的演讲稿。”

这类任务不以 evidence claim 为核心，`tokens_per_claim` 没意义。

**解决方式**：
信息增益控制只应用于 research-heavy 模式，不应用于纯生成模式。

### 3. Claim 抽取模型不稳定

如果 extractor 抽不准 claim，就会导致信息增益误判。

**表现**：
明明有新信息，但系统认为没有；或者把重复信息当新信息。

**解决方式**：
先软停，不硬停；积累样本后校准 extractor prompt 和相似度阈值。

### 4. 所有高质量来源都难以访问

例如付费墙、登录墙、私有数据库。

**表现**：
authority_gain 很低，但不是搜索无效，而是环境限制。

**解决方式**：
报告应输出“证据不足/需人工提供来源”，而不是无限搜索公开网页。

### 5. exhaustive research 场景

如果用户明确要求“尽可能全面、不要省 token”，系统早停可能反而违背需求。

**解决方式**：
增加模式开关：`fast / balanced / exhaustive`。只有 fast/balanced 默认启用强早停。

---

## 六、为什么这份方案有效

### 1. 它把“努力程度”变成了可度量对象

Anthropic 明确指出，多智能体系统的表现与 token usage、tool calls、model choice 强相关，并且 token 使用本身解释了 BrowseComp 中大量性能差异，但代价是多智能体系统 token 消耗很快。([Anthropic][1])

你的方案不是简单降低 token，而是看：

```text
token 是否转化成有效 claim？
tool call 是否转化成有效 claim？
新增来源是否真的新增？
新增信息是否补足用户维度？
```

这比单纯设置 `max_tokens` 更有效。

### 2. 它符合 Context Engineering 的主流方向

LangChain 把 context engineering 定义为在 agent 每一步轨迹中填入“刚好合适的信息”，并总结了 write、select、compress、isolate 四类策略。([LangChain][6])

你的方案对应关系是：

| Context Engineering 策略 | 本方案对应                                           |
| ---------------------- | ----------------------------------------------- |
| write                  | 把 claim/source/metrics 写入外部 state               |
| select                 | supervisor 只看 compressed_research，不看全部 raw logs |
| compress               | 保留现有 compress_research                          |
| isolate                | researcher 的过程指标不污染 supervisor 上下文              |

所以它不是“拍脑袋加指标”，而是符合现代 agent context management 的工程范式。

### 3. 它符合 RAG/RAGAS 的事实评估思路

RAGAS Faithfulness 的核心是：回答中的 claims 是否被 retrieved context 支持。([Ragas][4]) Context Recall 也是把 reference 拆成 claims，再看是否可归因到 retrieved context。([Ragas][3])

你的方案把这种“最终评估思想”前移到“研究过程控制”：

```text
最终报告 claim 是否有证据
↓ 前移
每轮 researcher 新增 claim 是否有证据
```

这就是为什么它能提前阻止幻觉和过度搜索。

### 4. 它利用了你项目已有结构，而不是推倒重来

你的项目已经有：

| 已有结构                                   | 可复用点                |
| -------------------------------------- | ------------------- |
| `max_concurrent_research_units`        | 控制 researcher 并发    |
| `max_concurrent_researcher_tool_calls` | 控制单 researcher 工具并发 |
| `max_queries_per_search_call`          | 控制单次 Tavily query 数 |
| `max_results_per_tavily`               | 控制每 query 结果数       |
| `compress_research`                    | 生成 researcher 压缩结果  |
| `process_print`                        | 打印过程 trace          |
| Tavily URL 去重                          | 局部来源去重              |

这些都已经存在于代码里。

所以新增方案是“补一层过程审计和动态停止”，不是“改掉整个架构”。

---

## 七、一个完整生动例子

假设用户问：

```text
请比较 OpenAI Deep Research、Gemini Deep Research、Perplexity Research 的能力、来源、引用、场景、局限。
```

### 原系统可能发生什么

```text
supervisor 分 3 个 researcher：
A 查 OpenAI
B 查 Gemini
C 查 Perplexity
```

A 和 B 找到大量资料，C 没找到合适资料，于是被误判为“Perplexity 未公开”。
后续 supervisor 可能继续让 A/B 深挖，导致 OpenAI/Gemini 信息越来越多，Perplexity 仍然缺失。最终报告结构完整，但三方比较失衡。

### 加入信息增益后

第一轮后，系统统计：

| 产品         | claim 数 | source 数 | coverage |
| ---------- | ------: | -------: | -------: |
| OpenAI     |       8 |        5 |        高 |
| Gemini     |       9 |        6 |        高 |
| Perplexity |       0 |        0 |        低 |

Coverage 模块发现 Perplexity 维度缺失。
Information Gain Gate 不会让系统继续泛搜 OpenAI/Gemini，而是提示：

```text
missing_dimension = Perplexity Research
next_action = targeted_search
```

第二轮只针对 Perplexity 官方 help/API 文档查找。
这时系统新增 Perplexity claim 和 source，coverage_gain 上升。最终报告更平衡，token 更少。

这就是它的实际价值：**不是让系统少查，而是让系统查缺口，不查重复。**

---

## 八、最终你应该怎么理解这份改动

这份方案可以用一句话概括：

> 它不是给 DeepResearch 加一个“更聪明的 researcher”，而是给 DeepResearch 加一个“研究过程仪表盘 + 刹车系统”。

各模块关系如下：

```text
Research Metrics State
    负责“记账”

Research Dimension Extraction
    负责“知道要完成哪些任务”

Claim & Source Extraction
    负责“从研究结果中提取有效资产”

Claim Dedup & Source Registry
    负责“识别重复与来源质量”

Coverage Gain
    负责“判断是否补足用户需求”

Information Gain Scoring
    负责“判断本轮新增价值”

Efficiency Metrics
    负责“判断新增价值是否太贵”

Process Metrics Trace
    负责“让人能看见系统为什么这么做”

Soft Stop
    负责“先提醒，不强制”

Hard Stop
    负责“真正阻止失控”

Evaluation & Calibration
    负责“调阈值，避免误杀”
```

如果它生效，你会看到系统从：

```text
多搜一点总没错
```

变成：

```text
只有当新增信息能提高 coverage、claim、source quality，且成本合理时，才继续搜
```

这就是从“自由探索型 DeepResearch”到“成本感知型 DeepResearch”的关键转变。

[1]: https://www.anthropic.com/engineering/built-multi-agent-research-system "How we built our multi-agent research system \ Anthropic"
[2]: https://docs.langchain.com/oss/python/langchain/multi-agent "Multi-agent - Docs by LangChain"
[3]: https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/ "Context Recall - Ragas"
[4]: https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/ "Faithfulness - Ragas"
[5]: https://docs.langchain.com/langsmith/cost-tracking "Cost tracking - Docs by LangChain"
[6]: https://www.langchain.com/blog/context-engineering-for-agents "Context Engineering"
