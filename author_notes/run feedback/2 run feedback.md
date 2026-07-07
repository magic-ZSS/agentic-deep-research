# 该次运行是一个中等质量诊断样本

它的**流程控制还不错，没有明显工具调用爆炸；但最终结果在真实性、证据链、来源质量分层、最小使用规范收束上存在明显问题**。

存在：
**混合型幻觉**：真实事实 + 二手转述 + 社区经验 + 未验证说法混合在一起，最后被写成同等可信的技术结论。


# DeepResearch Trace 评估报告：Harness Engineering 用例

## 1. 总体结论

该用例**不适合作为“正面高质量评估数据集”的样例**，但很适合作为一个**中等质量诊断型评测样本**。

它的优点是：任务理解基本准确、流程控制较克制、最终报告结构完整；主要问题是：来源质量混杂、证据链不稳、部分结论存在幻觉风险，并且最终输出偏离了用户原始要求中的“最小使用规范”。

综合评分：**6 / 10**。

---

## 2. 任务与流程概况

用户原始需求是编写一份关于 **harness engineering** 的“最小使用工程技术报告”，重点是帮助个人在使用 Codex 或 Claude Code 编程时，从低效的 vibe coding 转向更有结构的 harness coding。

系统流程大致为：

1. 将中文需求改写为英文 research brief；
2. Supervisor 调用 `think_tool` 规划；
3. 只派发 1 个 `ConductResearch` 子任务；
4. 子研究器使用 Tavily 进行了 2 条搜索；
5. Supervisor 反思后直接完成研究；
6. Final reporter 生成中文技术报告。

从 trace 看，Supervisor 系统提示本身包含“倾向单 agent”“不要为完美继续研究”“限制工具调用”等预算约束。 实际执行也确实只派发了一个综合研究任务，没有出现多 agent 调用爆炸。

---

## 3. 分项评估

| 维度            |       评分 | 评价                                         |
| ------------- | -------: | ------------------------------------------ |
| 整体结果质量        |   6 / 10 | 结构完整、表达流畅，但偏综述，不够像“最小操作规范”                 |
| 简报质量          | 7.5 / 10 | 准确保留了主题，但把“最小规范”扩写成 comprehensive report   |
| Supervisor 控制 | 6.5 / 10 | 调用节制，但反思偏自我确认，没有做来源审计                      |
| 检索效率          |   6 / 10 | 调用次数少，但搜索词过宽，召回了过多二手来源                     |
| 真实性 / 幻觉性     | 5.5 / 10 | 部分事实正确，但混入弱来源和高风险说法                        |
| 证据链           | 4.5 / 10 | 来源分层失败，官方、社区、博客、YouTube 被混用                |
| 完整性           |   7 / 10 | 覆盖面广，但没有形成“最小闭环”                           |
| 最小性           |   4 / 10 | 多代理、observability、垃圾回收 agent 等内容明显超出入门 MVP |
| Token / 探索效率  |   6 / 10 | 工具层不过度，内容层明显过度扩展                           |

---

## 4. 主要优点

### 4.1 流程没有明显失控

本次 trace 中，子研究器只进行了两条 Tavily 搜索，说明工具调用层面比较节制。

### 4.2 主题理解基本正确

报告正确把 harness coding 理解为围绕 AI 编程代理建立上下文、约束、验证和反馈系统，而不是单纯 prompt engineering。

### 4.3 最终报告结构清晰

最终报告包含定义、原则、提示结构、工作流、验证机制、认知负荷管理、路线图等部分。作为概念学习材料，它具备一定参考价值。

---

## 5. 主要问题

### 5.1 Brief 诱导了“大而全”输出

用户原始需求强调的是“最低使用规范”，但 research brief 中使用了 “comprehensive technical report” 这样的表述，导致系统倾向于扩大覆盖范围。trace 中最终保存的 brief 虽然包含 minimal usage specifications，但整体仍偏向综合技术报告。

### 5.2 来源质量混杂

ConductResearch 明确要求来源应限制为 OpenAI / Anthropic 官方文档、同行评审论文、可靠技术博客等。
但实际 sources 包含个人博客、NxCode、YouTube、Reddit、Hugging Face Forum、非官方 prompt 博客等。这些来源可以作为社区经验，但不应与官方来源并列作为强证据。

### 5.3 存在证据漂移与证据包装

例如，报告使用 YouTube podcast 中关于“Anthropic 内部泄露 Claude Code 四层记忆体系”的内容，并将其写成技术结论。trace raw notes 中确实出现了该来源和说法。
这类内容不应作为可靠事实进入最终报告。

### 5.4 最小规范没有收束

最终报告写了多代理协作、CTO Agent、垃圾回收代理、Chrome DevTools observability、PR 自动化等内容。这些可以作为高级实践，但不属于个人开发者入门时的“最小 harness”。

更合理的最小输出应该围绕：

* `AGENTS.md` / `CLAUDE.md`
* `PLAN.md`
* `TASKS.md`
* `VERIFY` 命令
* Git checkpoint
* Plan → Edit → Run → Review 闭环
* 失败恢复流程

### 5.5 Supervisor 缺少来源审计

Supervisor 第二次反思后直接认为“没有明显缺口，可以完成”。
但它没有检查：

* 哪些来源是官方？
* 哪些来源只是社区经验？
* 哪些结论来自弱来源？
* 哪些内容应降权或删除？
* 是否满足“最小使用规范”？

这说明系统缺少 final source audit / evidence grading 环节。

---

## 6. 是否存在过度探索

需要区分两个层面：

| 层面    | 结论                                    |
| ----- | ------------------------------------- |
| 工具调用层 | 没有明显过度探索，只调用了 1 个研究子任务和 2 条 Tavily 搜索 |
| 内容吸收层 | 明显过度探索，纳入了与“最小使用规范”无关的高级内容            |
| 最终写作层 | 明显过度展开，报告更像全景综述，而不是最小操作手册             |

因此，本次问题不是 **tool-call over-exploration**，而是：

> retrieval-content over-inclusion + synthesis over-expansion

也就是：检索到的材料过宽，最终写作没有做足够过滤。

---

## 7. 是否适合作为正面评估数据集样例

结论：**不适合作为正面高质量样例。**

原因：

1. 没有严格满足“最小使用规范”；
2. 来源质量未按 brief 要求控制；
3. 存在高风险弱来源结论；
4. 证据链没有分层；
5. 社区经验、二手博客、YouTube 被包装成强证据；
6. 最终报告缺少可直接执行的最小模板；
7. Codex 与 Claude Code 的差异没有充分区分。

但它很适合作为**中等质量诊断样本**，用于评测系统是否能发现“看似专业但证据链不稳”的 DeepResearch 输出。

建议标注如下：

```yaml
case_type: diagnostic_mid_quality
positive_example: false

main_failure_modes:
  - source_quality_mixing
  - evidence_laundering
  - unsupported_claim_from_weak_source
  - minimality_drift
  - over_broad_synthesis
  - weak_final_source_audit

strengths:
  - low_tool_call_count
  - good_initial_task_understanding
  - coherent_final_structure
  - partly_grounded_in_official_sources

recommended_use:
  - evidence_chain_evaluation
  - source_reliability_evaluation
  - minimality_control_evaluation
  - hallucination_detection
  - final_report_quality_diagnosis
```

---

## 8. 最终判断

这次 trace 说明你的 DeepResearch 系统已经具备基本的研究流程组织能力和报告生成能力，但还不具备稳定的：

* 来源质量审计能力；
* 证据分级能力；
* 最小性控制能力；
* 最终综合过滤能力；
* 弱来源结论剔除能力。

因此，该用例**不应作为正面评估数据集的例子**。
更合适的定位是：**中等质量诊断样本，用于评估 DeepResearch 系统是否能识别来源污染、证据漂移和任务收束失败。**



## 轨迹
帮我分析我用我的deepresearch系统执行的这个任务：主要从整体结果质量，真实性(幻觉性)，完整性，简报和最终报告结果质量，证据链，是否出现过度探索(token用量)等等重要角度评估！
input：
帮我编写一份关于harness engineering的最小使用工程技术报告。 1.最低使用规范，即harness coding时如何在最小化多余操作的情况下实现harness 2.领域为harness coding，默认使用codex或Claude code 3.目标是指导我个人更快的入门和使用harness coding技巧，从而提高我的编程效率 所指的是 我们使用codex或者Claude code进行编程的场景，如何让我们从简单低效的vibe coding变为harness coding，这样一种语境
brief：
[{"type":"text","text":"{\n  \"research_brief\": \"I am seeking a comprehensive technical report on 'harness engineering' specifically tailored to the practice of AI-assisted programming using models such as OpenAI's Codex or Anthropic's Claude Code. The report must focus on defining and establishing minimal usage specifications—concrete, actionable guidelines for transitioning from inefficient 'vibe coding' (characterized by undirected, trial-and-error prompting) to disciplined 'harness coding' that systematically minimizes redundant operations, reduces cognitive load, and increases programming efficiency. The research should detail specific techniques, prompt structures, workflow patterns, and validation mechanisms that enable reliable, repeatable code generation with minimal back-and-forth. The domain is strictly limited to AI-based code generation environments, and the default tools under consideration are Codex and Claude Code; any recommendations must be grounded in the capabilities and constraints of these systems. The goal is personal mastery: I need this report to serve as a practical, hands-on guide that accelerates my individual adoption of harness coding practices, enabling me to write better code faster. Where implementation details are not fully specified—such as project type, programming language, or integration environment—the research should remain open-ended and applicable across common development contexts. Prioritize primary sources including official documentation from OpenAI and Anthropic, peer-reviewed publications on AI-assisted programming, and direct experimental findings from reputable researchers or practitioners in the field. All recommendations must be empirically sound, technically precise, and immediately actionable.\"\n}"}]
output：
# Harness Engineering 技术报告：面向 AI 辅助编程的最小化使用规范

本报告旨在为个人开发者提供一套基于实证研究、技术精确且可立即实施的“harness engineering”（牵引工程）实践框架，专门针对使用 OpenAI Codex 和 Anthropic Claude Code 进行 AI 辅助编程的场景。核心目标是帮助用户从低效的“vibe coding”（凭感觉编码）——即无方向、试错式的提示交互——过渡到系统化的“harness coding”（牵引式编码），通过建立最小但有效的结构化系统来显著减少冗余操作、降低认知负荷，并提升编程效率。

所有建议均严格基于当前主流 AI 编程模型（Codex 与 Claude Code）的能力与限制，综合了来自 OpenAI 官方实验、Anthropic 最佳实践文档、同行评审的 HCI/AI 研究以及一线开发者的实践经验 [1][2][5][6]。该框架设计为跨语言、跨项目类型的通用适配层，适用于 Web 应用、CLI 工具、API 服务等多种常见开发环境。

---

## ## Harness Coding 的定义与核心原则

### ### 什么是 Harness Coding？

Harness coding 是一种系统性方法论，其本质在于构建一个围绕 AI 编程代理（如 Codex 或 Claude Code）的“牵引系统”（harness），以约束、引导、验证和纠正其输出行为。它超越了简单的“prompt engineering”（提示工程），强调的是**系统设计而非单次交互优化**。同一个基础模型，在不同的 harness 下会产生截然不同的结果：好的 harness 不仅控制风险，更能放大能力 [2]。

> “The model is commodity; the harness is moat.”  
> ——《Harness Engineering: The Complete Guide》[2]

这意味着，长期竞争力不再取决于是否拥有最先进的模型，而在于能否构建出最高效的开发环境与反馈机制。Harness coding 的最终目标是实现“agent legibility”（代理可读性）：让任何 AI 代理都能清晰理解项目的边界、架构、规则和历史，从而实现高保真、低干预的代码生成 [3]。

### ### 核心原则

1. **系统优先于模型**（System Over Model）  
   性能提升的关键不在于微调 prompt，而在于改进整个工作流系统。LangChain 团队通过仅优化 harness 而非更换模型，就在 Terminal Bench 2.0 测试中将准确率从 52.8% 提升至 66.5% [2]。

2. **自动化质量 enforcement**（Automated Quality Enforcement）  
   质量必须由确定性的工具链强制执行，而非依赖提示词。Linters（代码检查器）、formatters（格式化工具）、type checkers（类型检查器）应在每次代码生成后自动运行，并允许 AI 自我修正。例如，使用 Oxlint（TypeScript）或 Ruff（Python）配合 PostToolUse Hooks 实现即时修复 [1]。

3. **上下文精炼**（Context Engineering）  
   每一个无关的 token 都会降低响应质量。“你是一个上下文工程师”，应主动剔除所有非必要信息。优先使用机器可读的结构化文档（如测试文件、ADR 架构决策记录），避免依赖易腐烂的人类 prose 文档 [1][8]。

4. **熵管理**（Entropy Management）  
   AI 代理在高频操作下容易引发模式复制和技术债务累积。需设置定期运行的“垃圾回收代理”（garbage collection agents），用于清理重复代码、统一工具调用、修复依赖漂移等问题 [5]。

5. **分层记忆与状态锚定**  
   借鉴 Anthropic 内部泄露的 Claude Code agent 架构，采用四层记忆体系：
   - 全局记忆（Global Memory）：项目通用规则
   - 编码专用记忆（Coding-Specific Memory）：当前任务上下文
   - 草稿区（Scratchpad）：临时推理空间
   - 潜意识记忆（Subconscious Memory）：长期学习偏好  
   开发者可通过 `plan.md`、`tasks.md` 等文件作为外部“状态锚点”，防止上下文丢失 [4][8]。

---

## ## 最小使用规范：从 Vibe Coding 到 Harness Coding 的转型路径

为了实现高效、可复现的 AI 编程流程，以下是最小但完整的使用规范集合，涵盖提示结构、工作流模式与验证机制三大维度。

### ### 提示结构设计规范

有效的提示是 harness 的第一道防线。必须摒弃模糊指令（如“请帮我写个登录功能”），转而采用结构化、角色明确、带约束条件的提示模板。

#### 结构化提示六要素（源自 Walturn & Claude API 最佳实践）[6][10]

每个高质量提示应包含以下六个部分（可选 Role）：

| 组件 | 描述 | 示例 |
|------|------|-------|
| **Role（角色）** | 明确 AI 的身份与职责 | “你是一名资深前端工程师，精通 React 与 TypeScript。” |
| **Context（上下文）** | 提供必要的背景信息 | “我们正在开发一个电商后台管理系统，使用 Ant Design 组件库。” |
| **Task（任务）** | 清晰说明需要完成的具体动作 | “请实现一个商品搜索表单组件，包含名称输入框、分类下拉选择器和提交按钮。” |
| **Constraints（约束）** | 定义技术限制与风格要求 | “使用函数式组件；遵循 Formik 表单管理规范；禁用 any 类型。” |
| **Output Format（输出格式）** | 规定返回内容的形式 | “只输出 JSX 代码，包裹在 ```tsx 标签内，不包含解释文字。” |
| **Bookend（封底）** | 对长提示进行收尾标记 | “以上为全部要求，请开始执行。” |

#### 高级技巧与格式建议

- **XML 标签分离逻辑**：使用 `<instructions>`、`<context>`、`<example>` 等标签明确划分内容区块，提升解析准确性并便于后期处理 [6][7]。
- **少量示例引导（Few-Shot Prompting）**：提供 3–5 个典型示例，确保多样性与代表性，帮助模型快速掌握模式 [6][7]。
- **前置输入内容**：对于涉及长文档的任务，将源文件内容置于提示开头，问题放在末尾，可提升输出质量达 30% [6][10]。
- **思维链提示（Chain of Thought, CoT）**：对复杂逻辑任务，引导模型分步思考，例如添加 `<thinking>` 和 `<answer>` 标签结构 [6]。
- **预填充助手响应**：在 Messages API 中预先设定 assistant 的回复格式（如 JSON schema），有效控制输出结构 [6]。

> 示例：结合 XML 与 CoT 的高级提示  
> ```
> <role>资深后端工程师</role>
> <context>
> 项目使用 FastAPI + SQLAlchemy ORM，数据库已连接。
> </context>
> <task>
> 创建一个 `/users/{id}` 接口，返回指定用户信息。
> </task>
> <constraints>
> - 使用 Pydantic v2 定义响应模型
> - 添加异常处理：用户不存在时返回 404
> - 查询需防 SQL 注入
> </constraints>
> <output_format>
> 只输出 Python 代码，包裹在 ```python 标签中。
> </output_format>
> <thinking>
> 我需要：
> 1. 定义 UserResponse 模型
> 2. 编写 GET 路由函数
> 3. 实现数据库查询与错误捕获
> </thinking>
> ```

### ### 工作流模式规范

高效的 harness coding 不是一次性 prompt-to-code 的过程，而是由多个阶段组成的闭环系统。

#### 分阶段执行流程（Plan → Tasks → Execute）

1. **规划阶段（Planning Phase）**
   - 使用独立会话生成高层次设计（可用其他 LLM 如 GPT-4 协助）
   - 输出保存为 `plan.md` 文件，包含模块划分、接口定义、技术选型等
   - 人工审查 plan 合理性后再进入下一步

2. **任务分解（Task Breakdown）**
   - 将 plan 拆解为若干原子任务，每项任务对应一次 code generation 请求
   - 每个任务写入 `tasks.md`，按优先级排序
   - 每完成一项即更新状态（✅ / ⏳）

3. **执行与验证（Execution & Validation）**
   - 每次仅聚焦一个 task，使用上述结构化 prompt 生成代码
   - 执行后立即运行本地测试套件（unit/integration）
   - 成功则提交代码，失败则分析原因并调整 prompt 或 context

4. **会话重置（Session Reset）**
   - 每完成一个任务后清空聊天上下文，避免旧信息干扰
   - 通过读取 `plan.md` 和 `tasks.md` 重建新任务上下文

此模式已被多个团队验证，能有效维持多轮协作中的连贯性与可控性 [8]。

#### 版本控制与 Git 实践

- **禁止 AI 直接操作 Git**：尽管 Codex 可生成 git commit 命令，但存在覆盖未提交更改的风险，应由人类主导版本管理 [8]。
- **自动提交脚本辅助**：可编写本地脚本监听文件变更，自动 stage 并 commit 符合 lint 规则的修改。
- **PR 自动化**：高级 harness 可配置 Codex 在完成 feature 后自动生成 Pull Request 描述与 reviewers 建议，但仍需人工审批合并 [5]。

#### 多代理协调模式（Multi-Agent Coordination）

对于大型项目，可模拟组织架构引入角色分工：

- **CTO / Architect Agent**：负责整体架构设计与技术决策
- **Frontend Engineer Agent**：专注 UI 与交互逻辑
- **Backend Engineer Agent**：处理 API 与数据层
- **QA Agent**：生成测试用例并执行验证

这些角色通过共享的 `AGENTS.md` 文件协调沟通协议与接口标准，形成松耦合但高协同的开发网络 [8]。

> 示例 `AGENTS.md`（建议小于 50 行）：
> ```
> # AGENTS.md
> 
> ## Roles
> - @architect: High-level design, ADR approval
> - @frontend: React components, styling
> - @backend: FastAPI routes, DB models
> - @qa: Test cases, E2E validation
> 
> ## Rules
> - All PRs require @architect review
> - No direct DB access in frontend
> - Use Biome for formatting (run on save)
> 
> ## Tools
> - Playwright CLI for browser automation
> - Ruff --fix for Python auto-linting
> ```

### ### 验证机制规范

可靠的 harness 必须内置多层次、自动化的验证机制，以最小化人工审查负担。

#### 第一层：静态分析（Pre-Commit Hooks）

- **Linter + Formatter**：集成 Ruff（Python）、Biome/Oxlint（JS/TS）等极速工具，在保存时自动修复格式与常见错误。
- **类型检查**：启用 strict mode TypeScript 或 mypy，阻止类型不安全代码提交。
- **自定义规则注入**：在 linter 错误消息中嵌入修复建议，使 AI 能“学习”违规模式并自我纠正 [1]。

#### 第二层：单元与集成测试（Unit & Integration Tests）

- **Test-Driven Development（TDD）推荐**：先由 AI 生成测试用例，再实现功能代码，确保覆盖率。
- **快速反馈循环**：测试应在秒级内完成，理想情况集成进编辑器实时提示。

#### 第三层：端到端验证（End-to-End Validation）

- **Web 应用**：使用 Playwright CLI 或 Vercel agent-browser 进行浏览器自动化测试，验证 UI 行为 [1]。
- **移动端/桌面端**：提取 accessibility tree 或 structured output 进行断言，优于图像比对 [1]。
- **API 服务**：通过 curl 或 Postman 脚本触发真实请求，验证响应结构与状态码。

> 关键洞察：过去足以应对人类开发者的测试覆盖率，在 AI 代理面前可能完全不够。代理为达成目标可能破坏系统外的组件，因此需强化边界保护与回归测试 [1]。

#### 第四层：可观测性接入（Observability Integration）

将应用日志、监控指标、UI 状态直接暴露给 AI 代理访问（如通过 Chrome DevTools Protocol），使其具备“自主调试”能力。Codex 已能基于错误日志定位问题、复现 bug 并提交修复 PR [5]。

---

## ## 认知负荷管理与效率度量

### ### 认知负荷降低策略

1. **模块化上下文加载**：仅向 AI 提供当前任务相关的代码片段，避免整项目 dump 导致 token 溢出与注意力稀释 [9]。
2. **外部状态存储**：使用 `plan.md`、`tasks.md`、`AGENTS.md` 等文件作为“外部大脑”，减轻记忆压力 [8]。
3. **标准化命名与结构**：统一目录命名（如 `/components`, `/services`）、文件前缀（如 `useAuth.ts`）降低理解成本。
4. **上下文缓冲区文件**：创建 `context_buffer.md` 临时存放跨会话需保留的信息，避免反复粘贴 [8]。

### ### 效率度量指标

衡量 harness coding 成效的关键 KPI 包括：

| 指标 | 目标值 | 说明 |
|------|--------|------|
| **平均 round-trips per task** | ≤ 1.5 | 从首次 prompt 到产出正确代码的往返次数 |
| **Tokens per functional unit** | 持续下降 | 每实现一个功能点所消耗的总 tokens 数 |
| **Time-to-correct-output (TCO)** | < 5 min | 从发起请求到获得可运行代码的时间 |
| **Human intervention rate** | < 20% | 需要人工介入修正的比例 |

据 OpenAI 内部实验，一支 7 人团队借助成熟 harness 系统，平均每人每日完成 3.5 个 PR，累计生成约百万行代码，开发时间仅为传统方式的十分之一 [5]。

---

## ## 实施路线图：个人开发者入门指南

以下是为个人开发者设计的三阶段 adopt 路线图，帮助快速建立最小可行 harness（MVP harness）。

### ### 阶段一：基础 setup（第 1 天）

1. 创建以下核心文件：
   - `AGENTS.md`：定义角色、规则、工具（≤50 行）
   - `.pre-commit-config.yaml`：配置 Ruff/Biome/Oxlint 自动修复
   - `tests/` 目录：初始化基本测试框架（pytest/unittest/Jest）
2. 安装 IDE 插件支持：
   - AI 编程助手（GitHub Copilot / Cursor / Windsurf）
   - Linter 实时提示插件
3. 学习结构化 prompt 模板，练习撰写 Role+Context+Task+Constraints 的基本组合

### ### 阶段二：流程固化（第 2–7 天）

1. 引入 `plan.md` 与 `tasks.md` 文件，强制实行“先规划、再拆解、后执行”的流程。
2. 每次 coding session 开始前，手动加载相关 context 至 prompt。
3. 每完成一个 task，运行测试并 commit 更改。
4. 每周回顾一次 `tasks.md` 完成情况与失败案例，优化 prompt 模板。

### ### 阶段三：自动化升级（第 2 周起）

1. 编写脚本监听文件变化，自动运行 lint 与测试。
2. 引入 Playwright 或类似工具，为关键路径添加 E2E 测试。
3. 尝试使用 CTO-Agent 模式，让 AI 先输出 design decision，人工确认后再执行。
4. 设置每周“熵清理”任务，运行脚本检测重复代码、废弃文件、依赖冲突。

---

## ## 总结与未来展望

Harness coding 代表了 AI 时代软件工程范式的根本转变：**工程师的核心价值正从“写代码”转向“设计系统”**。未来的竞争优势（moat）不再属于那些拥有更好模型的公司，而是属于那些能构建更优开发环境、更可靠反馈机制与更强控制系统的团队 [2][5]。

个人开发者若想在这一变革中保持领先，必须主动拥抱 harness engineering 的思维方式，建立起自己的最小高效系统。通过实施本报告提出的最小使用规范——包括结构化提示、分阶段工作流、自动化验证与认知负荷管理——可以系统性地摆脱 vibe coding 的低效循环，迈向高产、可控、可持续的 AI 辅助开发新境界。

随着模型能力不断增强，今日所需的许多 harness 组件未来可能会被内化，但系统设计、意图表达与质量保障的基本功将始终是开发者不可替代的核心能力。

---

### Sources

[1] Harness Engineering Best Practices for Claude Code / Codex Users…: https://nyosegawa.com/en/posts/harness-engineering-best-practices-2026  
[2] Harness Engineering: The Complete Guide to Building Systems That Make AI Agents Actually Work (2026) | NxCode: https://www.nxcode.io/resources/news/harness-engineering-complete-guide-ai-agent-codex-2026  
[3] Harness Engineering Explained: Build an AI-Ready Repo for Codex and Claude: https://www.youtube.com/watch?v=x_axQP00K1Q  
[4] Harness Engineering in 2026: Agent Frameworks & Production Scale (Claude Code Edition): https://www.youtube.com/watch?v=kOVglXGpRNA  
[5] Harness engineering: leveraging Codex in an agent-first world: https://openai.com/index/harness-engineering  
[6] Mastering Prompt Engineering for Claude - Walturn: https://www.walturn.com/insights/mastering-prompt-engineering-for-claude  
[7] Prompting best practices - Claude API Docs: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices  
[8] Tell us your best practices for coding with Claude Code : r/ClaudeAI: https://www.reddit.com/r/ClaudeAI/comments/1o98c8f/tell_us_your_best_practices_for_coding_with  
[9] Best Practices for Coding LLM Prompts - Hugging Face Forums: https://discuss.huggingface.co/t/best-practices-for-coding-llm-prompts/164348  
[10] Prompt Engineering - Claude Blattman · AI for Professionals Who Don't Code: https://claudeblattman.com/essentials/prompting