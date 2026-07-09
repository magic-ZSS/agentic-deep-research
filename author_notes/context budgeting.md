# Context management problem （上下文管理问题）导致的 token bloat（token 膨胀）与 context clash（上下文冲突）以及 智能体轮次和工具并发控制问题导致的研究过度扇出（fan-out）

## 分析


当前项目的 token 消耗问题，本质上不是简单的“最终报告生成过长”，而是 DeepResearch 架构在执行开放式研究任务时产生的 **token bloat（token 膨胀）** 、**context accumulation（上下文累积）**、**tool-feedback expansion（工具过度反馈）** 和 **multi-agent coordination overhead（多智能体协调开销）** 的叠加结果。


其一，deep research天然执行开放式研究任务天然高成本
  

    Anthropic 在其多智能体研究系统复盘中给出了经验：multi-agent research 的优势在于通过多个独立上下文窗口并行探索复杂问题，但代价是 token 消耗显著上升；其内部数据表明，普通 agent 通常约为聊天交互的 4 倍 token，而 multi-agent 系统约为聊天交互的15 倍 token。

    Anthropic 还特别强调，multi-agent 架构主要适用于高价值、可并行、信息量超出单一上下文窗口的任务；而许多 coding 类任务并没有足够多真正可并行的子问题，因此并不总是适合默认启用多智能体扩展。 

**并发控制**，观察到最消耗token的地方往往是异步并发处，需要对齐进行控制，主要是三个并发位置：

| 位置                               | 主流程 | 并发对象                                    | 触发条件                                       |
| -------------------------------------- | ------: | --------------------------------------- | ------------------------------------------ |
| **Supervisor 同时启动多个 researcher**       |       是 | 多个子研究任务并发                               | Supervisor 一次调用多个 `ConductResearch`        |
| **Researcher 同一轮并发执行多个 tool call**     |       是 | Tavily / MCP / search / think 之外的工具调用并发 | Researcher 模型一次返回多个工具调用                    |
| **Tavily 多 query 并发搜索 + 多网页并发摘要**      |       是 | 多个搜索 query、多个网页 summarization 并发        | Tavily 工具收到 `queries: List[str]`           |


第二，supervisor-subagent 架构存在 fan-out effect（扇出效应）。

    Open Deep Research 的 supervisor 会判断 research brief 是否可以拆成多个独立子主题，并委派给不同 sub-agents；这种设计可以隔离上下文并提升并行搜索能力，但一旦任务本身不需要并行，子任务数量、工具调用轮数和网页内容长度会共同放大成本。

第三，工具反馈是主要 token 来源

    Open Deep Research 官方特别强调，sub-agent 工具调用会产生大量原始网页、失败工具调用和无关网站结果；如果不做清洗，token usage 会显著膨胀。 之前的 harness engineering trace 中出现的 YouTube、Reddit、二手博客、论坛混入，本质上就是 tool-feedback expansion 和 source pruning 不足的表现。

第四，缺少 effort scaling（努力程度缩放）机制

    Anthropic 明确建议根据问题复杂度分配研究资源：简单事实查询只需要 1 个 agent 和少量工具调用，直接比较任务才需要 2–4 个 subagents，复杂研究才适合更多 subagents；这种规则用于防止简单问题被过度投入。 你当前项目的问题正是：具备深研究能力，但还没有稳定地区分“需要深研究”和“只需要 MVP 回答”的任务。


## 策略


### 配置层

---**本质上是做了基于规则的并发控制**

一. 结构化输出最大重试次数max_structured_output_retries默认值3->1。
   
    这样做的原因是我认为我将调用的模型的结构化输出能力一定是稳定可靠的，绝大部分情况下结构化响应不会出错，极端情况下可以直接结束流程，无需为了极端情况而放宽重试次数加大token bloat风险。

二. 最大并发研究单元数量max_concurrent_research_units默认值5->3。

    这是指每轮supervisor能够同时调用的ConductResearch的个数，即研究者的个数，直观上是决定了研究的**广度**的唯二之一因素。我认为暂时的收紧这个值有利于控制成本，后续可以再放宽。实际上，我认为我进行评估时，大部分情况下是不需要大于三个子主题同时开展研究的。

三. Research Supervisor 最大研究迭代次数max_researcher_iterations默认值6->3

    这是指supervisor能够进行的最大迭代次数，直观上看其是决定研究**深度**的唯二之一因素。我认为我暂时希望评估的内容不会涉及太深太复杂的询问，所以暂时收紧以后可逐渐放宽。

四. 单个 researcher step 中最大工具调用次数max_react_tool_calls默认值10->5

    这是指单个researcher能够执行的工具调用节点的最大轮数，其是决定研究**深度**的唯二之一因素。我认为我暂时希望评估的内容不会涉及太深太复杂的询问，所以暂时收紧以后可逐渐放宽。

五. **配置**researcher每次轮工具调用最大并行工具调用个数max_concurrent_researcher_tool_calls 默认值None->3 同步追加到researcher的系统提示词

    这是指researcher每次轮工具调用最大并行工具调用个数，直观上其决定研究**广度**的唯二之一因素。我认为我进行评估时，每个子主题大部分情况下是不需要大于三个工具并发开展研究的。

六. **配置**限制一次 tavily_search 内部查询最大执行个数max_queries_per_search_call 默认值None->3 同步追加到researcher的系统提示词

    这是指tavily_search 内部查询最大执行个数，同时会复用该值限制网页摘要任务的并发度。直观上其是决定研究**深度和广度**的工具因素。

    tavily_search 工具内部也会并行执行多个 query，并且对每个 unique URL 并行调用摘要模型。所以即使 researcher 只发出 1 个 tavily_search tool call，内部也可能放大成多次 Tavily 请求和多次模型摘要请求。这会影响是否需要新增“每轮 researcher tool call 并发数”限制的判断。


七. 压缩并综合单个 researcher 的研究结果的compress_research节点的压缩尝试次数max_attempts默认值3->1

八. summery节点配置调整

summarization_timeout_seconds 60->180 # 等待时长

max_content_length 500000->300000 # 每个网页结果进入 summarization_model 前的最大字符数

summarization_model_max_tokens 8192->4096 # 总结模型的最大结果长度

新增max_results_per_tavily 5->3 # 每次Tavily调用的内部返回结果个数

九. 运行流程 Print Trace 用于运行时追踪和调试

新增一个总开关 print_process_info: bool = False，默认不打印，开启后用 print() 输出精简运行流程。打印逻辑集中封装到 utils.py，主流程只插入少量 helper 调用。输出只展示流程、编号、轮次、工具名、主题短标题和并发编号，不打印大段搜索内容或模型正文。

十. 小结：

    经过上述收紧后，系统从“默认深研究 + 高并发扩展”调整为“受控研究 + 成本优先”的执行模式。

    核心控制结果(暂时忽略thinktool的调用)如下：

| 指标                 |                原理论上限 |                   调整后理论上限 | 控制目标                                                                       |
| ------------------ | -------------------: | ------------------------: | -------------------------------------------------------------------------- |
| 最大 Tavily 内部 search query 请求数         | `6 × 5 × 10 × m × n` | `3 × 3 × 5 × 3 × 3 = 405` | 控制搜索、工具调用和网页摘要的 fan-out                                                    |
| 最大语言模型调用上限         |                未显式收敛 |                  `2088` 次 | 控制 supervisor、researcher、summarization、compression、final report 等节点的总体调用规模 |
| 启用澄清模式后的最大语言模型调用上限 |                未显式收敛 |                  `2089` 次 | 额外增加 1 次 clarification 判断                                                  |

    调整后的关键配置如下：

| 控制项                                    | 原默认值 | 调整后 | 控制目标                       |
| -------------------------------------- | ---: | --: | -------------------------- |
| `max_structured_output_retries`        |    3 |   1 | 降低异常重试成本                   |
| `max_concurrent_research_units`        |    5 |   3 | 限制 Supervisor 研究广度         |
| `max_researcher_iterations`            |    6 |   3 | 限制 Supervisor 研究深度         |
| `max_react_tool_calls`                 |   10 |   5 | 限制单个 Researcher 工具轮次       |
| `max_concurrent_researcher_tool_calls` | None |   3 | 限制 Researcher 单轮工具并发       |
| `max_queries_per_search_call`          | None |   3 | 限制 Tavily 内部 query 与网页摘要并发 |
| `compress_research.max_attempts`       |    3 |   1 | 降低压缩重试成本                   |

    当前配置下，理论最大语言模型调用数为：

    `1 + 4 + 45 + 2025 + 9 + 4 = 2088`

    其中：

| 部分                |      调用数 |
| ----------------- | -------: |
| Research Brief 生成 |        1 |
| Supervisor 调用     |        4 |
| Researcher 推理调用   |       45 |
| Tavily 网页摘要调用     |     2025 |
| Research 压缩调用     |        9 |
| Final Report 生成调用 |        4 |
| **合计**            | **2088** |

    如果启用澄清模式，则额外增加 1 次 clarification 判断，总上限为：

    `2088 + 1 = 2089`

    因此，当前策略的核心结论是：系统仍保留 DeepResearch 的多轮检索、多智能体和网页摘要能力，但通过限制 Supervisor 广度、Researcher 深度、工具并发、Tavily 内部 query 数和压缩重试次数，将最坏情况下的工具调用和模型调用规模显式收敛，避免简单评估任务被过度研究。




### 提示词层


一. 监督智能体系统提示词中**明确**子智能体和反思的调用**停止规则**和**并行管理**

    a.明确停止规则

    b.防止过度委派 让监督者倾向于节省预算

    c.并行管理

    <Task>
    ...
    当你对工具调用返回的研究发现完全满意时，你应调用 "ResearchComplete" 工具，表示你的研究已经完成。
    </Task>

    <Hard Limits>
    **任务委派预算**（防止过度委派）：
    - **偏向使用单个 agent**——除非用户请求中有明确的并行化机会，否则为了简单性使用单个 agent
    - **能够自信回答时就停止**——不要为了追求完美而持续委派研究
    - **限制工具调用**——如果找不到合适来源，在调用 ConductResearch 和 think_tool 达到 {max_researcher_iterations} 次后必须停止

    **每轮最多 {max_concurrent_research_units} 个并行 agent**
    </Hard Limits>



二. 监督智能体系统提示词中明确


三. 研究智能体系统提示词中**明确**搜索工具调用的**停止规则**和**并行管理**
   
    a.明确停止规则

    b.根据任务复杂度而定的token使用启发式规则

    c.明确规定每次搜索工具调用的最大并行个数以及每个调用的最大查询数量

    <Hard Limits>
    **工具调用预算**（防止过度搜索）：
    - **简单查询**：最多使用 2-3 次搜索工具调用
    - **复杂查询**：最多使用 5 次搜索工具调用
    - **必须停止**：如果找不到合适来源，在 5 次搜索工具调用后必须停止
    - **并行工具调用**：在一次响应中最多调用 `{max_concurrent_researcher_tool_calls}` 个工具
    - **每次搜索调用的查询数量**：在一次 `tavily_search` 调用中最多包含 `{max_queries_per_search_call}` 个查询

    **在以下情况立即停止**：
    - 你已经能够全面回答用户的问题
    - 你已经拥有 3 个以上与问题相关的示例或来源
    - 你最近 2 次搜索返回了相似信息
    </Hard Limits>

    

四. 为防止过度探索（Over-exploration）导致的局部低价值信息token浪费，研究智能体系统提示词中明确指出**研究策略要遵循由广到窄由浅入深的规则**指令：

   <Instructions>
   **从更宽泛的搜索开始**——优先使用宽泛、全面的查询
   **随着信息积累，执行更窄的搜索**——补齐缺口
   </Instructions>

   open-deep-research:"先建立全局问题地图，再选择高价值方向深挖，最后用证据链收敛成结论。"




### 模型调度层

一. 基于规则的成本感知模型调度（Cost-aware Model Orchestration）：
   
    a.不同的调用根据所处任务的复杂度和重要性使用不同强度的模型 

    b.不同的任务需求使用具有不同的能力或画像的模型。

    c.Supervisor/final_report_model 用“强但受控”的模型，Researcher 用“主力但成本可控”的模型，Summary/Compression 用低成本模型，Critic 用高价值模型。

- research_model:高 使用计划推理和思考能力突出的模型 例如claude fable5、glm-5.2、**Qwen3.7-plus(较高质量且限时降价)**
- summarization_model:中低 使用长上下文和总结提炼能力突出的模型 例如 gemini3.1、
- compression_model:中 使用长上下文和总结提炼能力突出的模型 例如 gemini3.1 pro
- final_report_model:高 使用思考推理总结写作等综合能力全面的模型 例如 chatgpt5.5

配置如下：

- SUPERVISOR_MODEL=glm-5.2 max / qwen3.7-plus # 高价值强模型
- RESEARCHER_MODEL=qwen3.7-plus # 均衡模型
- SUMMARIZATION_MODEL=qwen3.5-flash # 低成本快响应模型
- COMPRESSION_MODEL=qwen3.5-flash # 低成本快响应模型
- FINAL_REPORT_MODEL=glm-5.2 max / qwen3.7-plus # 高价值强模型
- CRITIC_MODEL=glm-5.2 max/ qwen3.7-plus # 高价值强模型

二.


附录1. 模型画像表：

| 品牌       | 模型                  | 综合定位                    | 官方一手能力/突出能力                                                                                                                                  | 上下文与模态                                                           | 百炼价格参考                                                                                                      | 百炼功能支持                                                             | 响应速度/吞吐信息                                                                           | DeepResearch 适用任务                                              |
| -------- | ------------------- | ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ | ----------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| 千问       | `qwen3.7-max`       | 千问旗舰高能力模型               | Qwen 官方将 Qwen3.7-Max 定位为 Agent 时代模型，强调 coding agent、office workflow、长程自主执行等任务；百炼也标注其为“最强推理能力”选择。([Qwen Studio][1])                           | 1M；以文本推理/Agent 为核心，部分版本增强视觉模态。([阿里云帮助中心][2])                     | 百炼全球区示例：`$1.65` 输入 / `$4.951` 输出，每 1M tokens。([AlibabaCloud][3])                                            | 思考、Function Calling、内置工具、Batch；结构化输出需谨慎，不建议作为强 JSON/Pydantic 默认模型。 | 未见官方固定 TPS；百炼公开的是 RPM/TPM 限流而非真实响应时间。                                               | 高难 `research_model`、复杂规划、最终报告升级模型；不建议用于高频摘要压缩。                 |
| 千问       | `qwen3.7-plus`      | **默认高级主力模型**            | Qwen/百炼均强调其能力与成本均衡，适合 Agent、AI 编程、聊天、内容生成、摘要总结、文档处理；百炼推荐它作为 Agent/编程开发首选。([阿里云帮助中心][4])                                                      | 1M；文本、图像、视频输入到文本输出；具备多模态交互混合智能体能力。([阿里云帮助中心][2])                 | 全球区示例：≤256K 为 `$0.276` 输入 / `$1.101` 输出；256K–1M 为 `$0.826` 输入 / `$3.301` 输出，每 1M tokens。([AlibabaCloud][3]) | 思考、Function Calling、内置工具、结构化输出、Batch 支持较完整。                        | 未见官方固定 TPS；百炼公开限流可用于估算并发上限。                                                         | 默认 `research_model`、`final_report_model`；也可承担高质量摘要/压缩。         |
| 千问       | `qwen3.6-plus`      | 上一代主力备用                 | Qwen 官方称 Qwen3.6-Plus 是 hosted model，默认 1M 上下文，并提升 agentic coding、多模态能力。([Qwen Studio][5])                                                   | 1M；支持多模态/Agent 场景。                                               | 全球区示例：≤256K 为 `$0.276` 输入 / `$1.651` 输出；256K–1M 为 `$1.101` 输入 / `$6.602` 输出，每 1M tokens。([AlibabaCloud][3]) | 思考、Function Calling、内置工具、结构化输出、Batch。                              | 未见官方固定 TPS。                                                                         | `qwen3.7-plus` 不可用时备用；不建议作为长期首选。                               |
| 千问       | `qwen3.6-flash`     | **低成本长上下文工作马**          | 百炼文档建议：确认 `qwen3.7-plus` 效果满足需求后，可尝试 `qwen3.6-flash` 降低成本，且拥有相同上下文长度和功能支持。([阿里云帮助中心][4])                                                     | 1M；轻量长上下文。                                                       | 全球区示例：≤256K 为 `$0.165` 输入 / `$0.99` 输出；256K–1M 为 `$0.66` 输入 / `$3.961` 输出，每 1M tokens。([AlibabaCloud][3])   | 思考、Function Calling、内置工具、结构化输出、Batch。                              | 未见官方固定 TPS。                                                                         | 默认 `summarization_model`、`compression_model`；网页摘要、文档压缩、低成本批处理。 |
| DeepSeek | `deepseek-v4-pro`   | 强推理/强代码备选               | DeepSeek 官方文档标注 V4-Pro 支持思考/非思考模式、1M 上下文、最大 384K 输出、JSON Output、Tool Calls。([DeepSeek API Docs][6])                                          | 1M；最大输出 384K。([DeepSeek API Docs][6])                            | 百炼中国内地：`$1.65` 输入 / `$3.301` 输出，每 1M tokens。([AlibabaCloud][3])                                             | 思考、JSON Output、Tool Calls；百炼集成层面不建议依赖内置工具/Batch。                   | 未见官方固定 TPS；DeepSeek 官方公开并发限制。([DeepSeek API Docs][6])                               | 复杂逻辑推理、代码推理、researcher 中间判断；可作为 `qwen3.7-max` 的性价比替代。          |
| DeepSeek | `deepseek-v4-flash` | 低成本推理模型                 | DeepSeek 官方同样标注 V4-Flash 支持思考/非思考模式、1M 上下文、最大 384K 输出、JSON Output、Tool Calls。([DeepSeek API Docs][6])                                        | 1M；最大输出 384K。([DeepSeek API Docs][6])                            | 百炼中国内地：`$0.138` 输入 / `$0.275` 输出，每 1M tokens。([AlibabaCloud][3])                                            | 思考、JSON Output、Tool Calls。                                         | DeepSeek 官方标注 V4-Flash 并发限制高于 Pro；固定 TPS 未公开。([DeepSeek API Docs][6])               | 低成本 reasoning fallback、轻量 researcher、快速判断。                     |
| DeepSeek | `deepseek-v3.2`     | 低成本推理备用                 | 百炼价格页列出 `deepseek-v3.2`；DeepSeek V4 官方已将主力更新到 V4-Pro/Flash，因此 V3.2 更适合作为备用。([AlibabaCloud][3])                                               | 百炼侧需以控制台为准。                                                      | 百炼中国内地：`$0.287` 输入 / `$0.431` 输出，每 1M tokens。([AlibabaCloud][3])                                            | 需以百炼当前模型详情为准。                                                      | 未见官方固定 TPS。                                                                         | 低成本分析备用；优先级低于 V4-Flash。                                        |
| 智谱AI     | `glm-5.2`           | **高价高价值旗舰模型**           | Z.AI 官方称 GLM-5.2 面向 long-horizon tasks，具备稳定 1M 上下文、长程工作能力、复杂代码/Agent 工程能力。([Z.ai][7])                                                        | 1M；文本推理、长程 Agent、代码工程。([Z.ai][7])                                | 百炼全球区：`$1.1` 输入 / `$3.851` 输出，每 1M tokens；不区分阶梯。([AlibabaCloud][3])                                         | 思考、Function Calling、结构化输出；不建议依赖内置工具/Batch。                         | 未见官方固定 TPS；通常应按高价值节点少量调用。                                                           | `critic_model`、`evaluator_model`、最终审稿、复杂中文报告、结构化评估。            |
| 智谱AI     | `glm-5.1`           | GLM 高阶备用                | Z.AI 官方将 GLM-5.1 定位于 Agentic Coding、长程规划、分步执行、动态调整和交付能力。([Overview - Z.AI DEVELOPER DOCUMENT][8])                                            | 百炼侧约 200K 阶梯；具体以控制台为准。                                           | 百炼全球区示例：≤32K 为 `$0.825` 输入 / `$3.301` 输出；32K–200K 为 `$1.1` 输入 / `$3.851` 输出。([AlibabaCloud][3])             | 思考、Function Calling、结构化输出。                                         | 未见官方固定 TPS。                                                                         | GLM-5.2 的降级备用；中高质量评估、写作、审查。                                    |
| 智谱AI     | `glm-5`             | 工程 Agent 基座备用           | GLM-5 论文将其定位为从 vibe coding 到 agentic engineering 的模型，强调复杂系统工程和真实软件工程任务。([arXiv][9])                                                          | 百炼侧上下文/阶梯以控制台为准。                                                 | 百炼价格页中 GLM-5 系列按阶梯计费，具体以当前控制台为准。([AlibabaCloud][3])                                                         | 思考、Function Calling、结构化输出。                                         | 未见官方固定 TPS。                                                                         | GLM 低一档备用；不建议优先于 GLM-5.2。                                      |
| 月之暗面     | `kimi-k2.7-code`    | **代码 Agent 专用模型**       | Kimi 官方称 K2.7 Code 是其最强 coding model，长上下文指令遵循更可靠、编程任务成功率更高；K2.7 Code HighSpeed 约 180 tokens/s，短上下文最高约 260 tokens/s。([Kimi API Platform][10]) | 256K；文本、图像、视频；K2.7 Code 不支持关闭 thinking。([Kimi API Platform][10]) | 百炼中国内地：`$0.894` 输入 / `$3.713` 输出，每 1M tokens。([AlibabaCloud][3])                                            | 思考、Function Calling、视觉/视频输入、Agent 任务；不适合强结构化输出节点。                  | 标准版未给固定 TPS；HighSpeed 约 180 tokens/s，短上下文最高约 260 tokens/s。([Kimi API Platform][10]) | `coding_model` 首选；代码库理解、多文件重构、长会话调试。                           |
| 月之暗面     | `kimi-k2.6`         | 通用多模态 Agent 模型          | 百炼/Kimi 说明其具备更强更稳的长程代码编写、指令遵循和自我纠错；支持文本、图片、视频输入，支持思考与非思考模式、对话与 Agent 任务。([阿里云帮助中心][11])                                                      | 256K；文本、图像、视频。([Kimi API Platform][10])                          | 百炼中国内地：`$0.8939` 输入 / `$3.7131` 输出，每 1M tokens。([AlibabaCloud][3])                                          | 思考/非思考、Function Calling、多模态输入、Agent 任务。                            | 未见官方固定 TPS。                                                                         | 多模态理解、通用 Agent 备用；代码任务不如 K2.7 Code 专精。                         |
| 月之暗面     | `kimi-k2.5`         | 多模态 Agentic 模型          | Kimi K2.5 论文称其面向 visual agentic intelligence，强调文本与视觉联合优化、Agent Swarm、多模态 Agent 能力。([arXiv][12])                                              | 256K；文本、图像、视频。([Kimi API Platform][10])                          | 百炼中国内地：`$0.574` 输入 / `$3.011` 输出，每 1M tokens。([AlibabaCloud][3])                                            | 思考/非思考、多模态、Agent 任务。                                               | 未见官方固定 TPS。                                                                         | 视觉理解、图文 Agent 备用；已被 K2.6/K2.7 覆盖较多。                            |
| MiniMax  | `MiniMax-M2.5`      | 低成本高速 Agent / 办公 / 代码模型 | MiniMax 官方文档将 M2.5 定位为“顶尖性能与极致性价比，轻松驾驭复杂任务”；OpenRouter 公开模型页显示其面向真实生产力、办公与代码任务，Context 约 205K。([MiniMax 开放平台文档中心][13])                       | 约 204.8K / 205K。([OpenRouter][14])                               | 百炼中国内地：`$0.304` 输入 / `$1.213` 输出，每 1M tokens。([AlibabaCloud][3])                                            | 思考模式、Agent/代码能力；结构化输出与内置工具需以百炼实测为准。                                | `highspeed` 版本官方描述为速度大幅提升；固定 TPS 需实测。([MiniMax 开放平台文档中心][13])                       | 低成本摘要、轻代码、改写、办公类任务；可做 `summarization_model` 备用。                |



附录2. 模型调度策略表

| DeepResearch 阶段              | 默认模型                | 升级模型                                          | 降级/备用模型                              | 调度规则                                                          | 原因                                                                                             |
| ---------------------------- | ------------------- | --------------------------------------------- | ------------------------------------ | ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `research_model`             | `qwen3.7-plus`      | `qwen3.7-max` / `glm-5.2` / `deepseek-v4-pro` | `deepseek-v4-flash`                  | 默认用 `qwen3.7-plus`；当任务涉及复杂规划、强推理、代码库级分析、证据冲突判断时升级。            | research 阶段决定后续搜索方向和证据链质量，不能太弱；`qwen3.7-plus` 是能力/成本/工具支持最均衡的默认选择。([阿里云帮助中心][1])               |
| `summarization_model`        | `qwen3.6-flash`     | `qwen3.7-plus`                                | `MiniMax-M2.5` / `deepseek-v4-flash` | 高频网页摘要、文档摘要默认 flash；遇到论文、复杂技术文档、关键证据再升 plus。                  | 摘要调用频率高，不应默认用 `glm-5.2` 或 `qwen3.7-max`；百炼明确建议 flash 用于降低成本且保持相近上下文长度和功能支持。([阿里云帮助中心][1])      |
| `compression_model`          | `qwen3.6-flash`     | `qwen3.7-plus` / `glm-5.2`                    | `deepseek-v4-flash`                  | 普通上下文压缩用 flash；涉及事实保真、证据冲突、复杂概念压缩时升 plus；极高价值压缩才用 GLM-5.2。    | compression 核心是保真压缩与降低 token，不是追求最强生成质量；成本控制优先。                                                |
| `final_report_model`         | `qwen3.7-plus`      | `glm-5.2` / `qwen3.7-max`                     | 不建议低于 `qwen3.7-plus`                 | 普通报告默认 plus；中文严肃写作、结构化评估报告、最终审稿用 GLM-5.2；复杂推理型报告用 max。        | 最终报告直接决定用户感知质量；这里可以适当花钱，但仍不建议每次都上最高价模型。                                                        |
| `critic/evaluator_model`     | `glm-5.2`           | `qwen3.7-max`                                 | `deepseek-v4-pro`                    | 只在少量关键节点调用：真实性检查、证据链审查、评分、结构化评估。                              | GLM-5.2 是高成本高价值模型，适合用在“少次数、高影响”的评估/审稿节点。([Z.ai][2])                                            |
| `coding_model`               | `kimi-k2.7-code`    | `glm-5.2` / `qwen3.7-max`                     | `MiniMax-M2.5` / `qwen3.7-plus`      | 代码库理解、多文件重构、长会话调试默认 K2.7 Code；普通脚本/轻代码可用 MiniMax 或 Qwen Plus。 | Kimi 官方和百炼都把 K2.7 Code 定位为长程软件工程/编程任务模型，且官方给出更强 coding 与 agentic 表现说明。([Kimi API Platform][3]) |
| `fast_reasoning_model`       | `deepseek-v4-flash` | `deepseek-v4-pro`                             | `qwen3.6-flash`                      | 用于低成本快速判断、路线选择、轻量反思；失败或不确定时再升 pro。                            | DeepSeek V4-Flash 价格显著低，且官方支持 1M 上下文、JSON Output、Tool Calls。([DeepSeek API Docs][4])           |
| `structured_output_model`    | `qwen3.7-plus`      | `glm-5.2`                                     | `qwen3.6-flash`                      | 涉及 JSON Schema、Pydantic、评分表、brief schema 的节点优先用支持结构化输出更稳的模型。  | 不建议把 `qwen3.7-max`、Kimi、DeepSeek 作为强 schema 默认模型；结构化稳定性比“裸推理能力”更重要。                            |
| `vision_understanding_model` | `qwen3.7-plus`      | `kimi-k2.6` / `kimi-k2.7-code`                | `kimi-k2.5`                          | 图像/视频理解、屏幕理解、GUI/视觉参考代码生成用多模态模型；文本研究任务不要默认调用视觉模型。             | `qwen3.7-plus` 和 Kimi K2 系列均支持多模态输入，但视觉 token 成本和延迟更高，应按需调用。([阿里云帮助中心][5])                     |








### 评测1 （未启用澄清模式）

截止到当前，测试情况如下：

1. **测试用例一**：**/ None->20w TU / positive 偏高 / llm judge score：6.5**

| 维度 | 评分 | 判断 |
|---|---:|---|
| 任务难度 | 5.5/10 | 难点不在事实检索，而在概念边界解释：需要准确说明 Context Engineering 解决的问题，并结合 DeepResearch / Coding Agent 两类场景区分它与 Prompt Engineering 的关系 |
| 总体评分 | 6.5/10 | 主论点基本正确，结构也完整，但证据链偏松，存在引用扩写、数字错配和低质量来源依赖 |
| 任务理解 | 8/10 | 抓住了“Context Engineering 解决什么问题”和“与 Prompt Engineering 的区别” |
| 简报质量 | 8/10 | 简报比原始输入更完整，但把“快速理解”扩成了“详细研究”，略有任务放大 |
| 结构完整性 | 7.5/10 | 覆盖 DeepResearch / Coding Agent / 差异 / 权衡 / 局限，框架比较完整 |
| 真实性与引用忠实度 | 5.5/10 | 主论点可靠，但多个案例、百分比、工具说法证据不足或引用错位 |
| **证据来源质量** | 5/10 | 官方来源不足，Medium / TowardsAI / 厂商营销内容占比偏高 |
| 场景贴合度 | 6.5/10 | DeepResearch 和 Coding Agent 都有覆盖，但部分案例偏泛化，未充分围绕真实 DeepResearch / Coding Agent 工作流展开 |
| 实用性 | 7/10 | 对入门理解有帮助，能解释大方向，但工程落地层还可以更具体 |
| **Token 用量** | 4/10 | 约 20w token 对该概念解释型任务明显偏高，合理范围更接近 4–8w |
| 耗时 | 未提供 | 没有给出该用例耗时，暂无法评分 |
| 探索效率 | 6/10 | 来源数量不少，但筛选不够严格，存在“引用铺陈”而非“证据筛选” |



2. **测试用例二**：**/ 100w->45w TU / negative高 / llm judge score：5.0**

| 维度 | 评分 | 判断 |
|---|---:|---|
| 任务难度 | 6.5/10 | 难点不在写表格，而在实时产品边界核验：需要区分 OpenAI/Gemini/Perplexity 的不同 Research 产品形态，并严格执行“官方优先、未公开不推测、简要输出” |
| 总体评分 | 5/10 | 信息量很大，但没有服从“简要、可核验、未公开不推测”的核心约束；研究控制能力明显不足 |
| 任务理解 | 7/10 | 知道要比较 OpenAI / Gemini / Perplexity 三类 Research 产品，也覆盖了五个维度 |
| 简报质量 | 7.5/10 | 基本保留了“可核验、未公开不推测”的要求，但弱化了“简要”这个关键约束 |
| 输出契合度 | 4/10 | 用户要“表格简要比较”，结果变成超长百科式表格，明显过度展开 |
| **真实性与引用忠实度** | 5/10 | 部分核心事实可靠，但夹杂大量第三方说法、营销信息、争议新闻和未核实细节 |
| **证据来源质量** | 5/10 | 有官方来源，但也混入 Wikipedia、个人博客、第三方营销博客等非首选来源 |
| **“未公开”执行情况** | 2/10 | 几乎没有使用“未公开”，反而用不稳来源填满空白 |
| 产品边界清晰度 | 4/10 | 混淆 ChatGPT Deep Research、OpenAI API、Gemini App、Gemini Enterprise、Perplexity Research、Computer、Pages、Pro Search |
| **Token 用量** | 2/10 | 44w token 对该任务明显过高，属于严重过度探索和上下文膨胀 |
| 耗时 | 4/10 | 251s 对需要联网核验的产品对比可以解释，但与“简要比较”目标不匹配 |
| **探索效率** | 2/10 | 来源数量过多但筛选不足，未能做到官方优先、早停和低质量来源剔除 |










    