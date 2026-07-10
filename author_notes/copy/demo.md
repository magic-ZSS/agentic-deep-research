## 总体评估

这次结果**严重失控**。原始输入只是“简单介绍当前最前沿的 deepresearch 是哪个、性能如何”，合理答案应是**短对比 + 明确结论 + 少量指标**。但简报被扩写成“2026 年中期全球 Deep Research 全景报告”，输出进一步变成大规模行业综述，导致 **787.6K token、1226 秒**，明显属于过度研究和过度扇出。

更严重的是：输出不只是长，而是**可信度有问题**。它把“Deep Research 产品”“底座模型 benchmark”“通用 agent/coding agent”“开源框架”混在一起比较，导致结论口径不一致。

## 关键问题

**1. 简报明显过度扩张。**
输入问的是“哪个最前沿、性能如何”，但简报要求覆盖 OpenAI、Google、Anthropic、Meta、开源、创业公司、架构、性能、API、开源权重、横向对比。这已经把一个简答任务膨胀成行业研究报告。合理简报应该限制为：最多 3–5 个系统，重点回答“目前没有唯一公认 SOTA，不同 benchmark 下 OpenAI/Gemini/Perplexity/Claude 各有优势”。

**2. 输出没有真正回答“哪个”。**
它列了很多系统，但没有建立清晰判定标准：是看 HLE？BrowseComp？GAIA？真实网页研究？引用质量？企业文档研究？如果没有标准，就不能直接说“最前沿是某一个”。目前更严谨的结论应是：**专用 Deep Research 产品上，OpenAI、Gemini、Perplexity 是主要前沿；按公开 benchmark，Gemini Deep Research / Gemini 3.1 Pro 在部分指标上领先，但 OpenAI Deep Research 仍是代表性产品；Claude 更偏长上下文与企业/代码 agent，不应直接等同于 deep research 产品。**

**3. 多处事实口径混乱。**
OpenAI 部分相对可靠：官方说明 deep research 使用 browsing + Python，并在 HLE 上 26.6%、GAIA pass@1 平均 67.36%。([OpenAI][1])
但 Gemini 部分问题很大：输出写 “HLE 74.2%、BrowseComp 85.9%”，其中 85.9% 是 Gemini 3.1 Pro model card 里的 agentic search 指标，不等同于 Gemini Deep Research 产品；Google 的 Gemini Deep Research agent 官方页给的是 HLE 46.4%、DeepSearchQA 66.1%、BrowseComp 59.2%。([blog.google][2])
Claude 部分也混淆：Anthropic 官方确实说 Opus 4.8 面向复杂 agentic coding/enterprise work，并推出 dynamic workflows、Cowork effort control 等，但输出中的 “MRCR 1M 78.3%、14.5 小时任务窗口、Opus 4.8 SWE-bench 80.9%”没有在官方 Opus 4.8 发布页中得到对应支持。([Anthropic][3])

**4. 引用体系失效。**
正文大量引用 `[9] [11] [12] [13] [14] [15] [18] [19]`，但 Sources 只列到 `[8]`。这属于严重证据链错误。即使内容部分正确，读者也无法追溯来源。

**5. 部分系统不该放在同一张表里直接比较。**
Meta Llama 4 是开源/开放权重模型生态，Meta 官方确实发布 Llama 4 Scout/Maverick，强调 MoE、多模态和开放权重，但这不是一个已验证的 Deep Research 产品。([Meta AI][4])
Sakana AI Scientist 是自动化科研系统，Nature 论文确实描述其能生成研究方向、执行实验、写论文，但它更偏“自动化机器学习科研”，不是普通网页 deep research agent。([Nature][5])
DeerFlow 是开源 SuperAgent harness，官方 GitHub 也说它编排 sub-agents、memory、sandboxes、skills，但它是框架，不是可直接与 OpenAI/Gemini 产品 benchmark 横比的闭环系统。([GitHub][6])

**6. 性能评价缺少独立评测约束。**
目前真正有价值的评价应区分“厂商自报 benchmark”和“第三方任务评测”。例如 2026 年一篇 deep research agent 评测显示，Claude Opus 4.6 with web search、OpenAI o3-deep-research、Google Gemini 3.1 Pro deep-research 在高门槛咨询任务上的 acceptance 都不高，Gemini 21.4%，o3 和 Claude 各 9.5%，说明“看起来很强”不等于“决策级可靠”。([arXiv][7])

## 这次任务理想输出应是什么样

更合理的回答应该控制在 500–800 字左右，类似：

> 当前没有唯一公认的 deep research SOTA。如果按专用研究产品成熟度看，OpenAI Deep Research 仍是代表性系统；如果按公开研究/搜索 benchmark 看，Google Gemini Deep Research / Gemini 3.1 Pro 在 HLE、BrowseComp、DeepSearchQA 等指标上非常强；如果看实时搜索、引用和产品化效率，Perplexity Deep Research 也值得列入第一梯队；Claude 更适合作为长上下文、代码和企业 agent 底座，而不是单独的 Deep Research 产品。总体上，前沿 deep research 的核心能力已经从 RAG 演进到“规划—搜索—阅读—验证—综合”的长程 agent，但仍有遗漏、引用错误、过度自信和成本高的问题。

## 评分表

| 维度 | 评分 | 判断 |
|---|---:|---|
| 任务难度 | 8/10 | “当前最前沿 deepresearch 是哪个”属于开放集合 SOTA 判断任务，需要候选发现、权威来源查证、横向比较和不确定性表达，难度较高。 |
| 输入匹配度 | 5/10 | 输出覆盖了“最前沿系统”和“性能”主题，但没有很好满足“请简单介绍”的表达要求，明显过长。 |
| 简报质量 | 6/10 | 简报意识到这是横向比较任务，方向基本合理；但缺少候选数量、来源数量、输出篇幅、token 和耗时边界。 |
| 最终报告质量 | 5/10 | 结构完整、覆盖面广，但重点不收敛，没有把“没有唯一公认最强”作为核心结论。 |
| 真实性 / 幻觉性 | 4/10 | 存在较多口径混用：把 Deep Research 产品、底座模型、开源框架、通用 agent benchmark 放在一起比较，部分指标可疑。 |
| 完整性 | 7/10 | 覆盖范围很广，但有效完整性一般；缺少“哪些系统不能直接横比”的分层说明。 |
| 证据链 | 3/10 | 引用编号断裂，正文引用超过 Sources 范围，关键性能数据难以追溯。 |
| Token 用量 | 2/10 | 787.6K token 对这个任务明显过量。任务值得深搜，但不应接近百万 token。 |
| 耗时 | 2/10 | 1226.59 秒明显过长，除非用户明确要求完整行业研究报告。 |
| 总体评分 | 5/10 | 任务方向理解有价值，但研究边界、证据治理、事实口径和最终压缩都存在明显问题，属于“高难任务下的失控式完成”。 |