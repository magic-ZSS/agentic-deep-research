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


## 策略一 配置 提示词 和 模型调度


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

summarization_model_max_tokens 8192  # 总结模型的最大结果长度

新增max_results_per_tavily 5->3 # 每次Tavily调用的内部返回结果个数

3 个 researcher
× 每个 researcher 3 个工具调用
× 每个 tavily_search 3 个 query
× 每个 query 3 个结果
= 81 个网页摘要任务 / 一轮

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


五.为防止summery阶段产生过多上下文，调整其提示词保留比例默认值 25-30 -> 10-15


六.简报生成阶段提示词重设计 无脑引导最大化具体性与细节 -> 



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

- SUPERVISOR_MODEL=glm-5.2 max / qwen3.7-plus # 高价值**强**模型
- RESEARCHER_MODEL=qwen3.7-plus # 均衡模型
- SUMMARIZATION_MODEL=qwen3.5-flash # 低成本**快**响应模型
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

1. 较难任务 中高质量但明显过度扇出 negative

| 维度 | 评分 | 判断 |
|---|---:|---|
| **任务难度** | 7/10 | 概念较新，且要结合 DeepResearch 与 Coding Agent 两类场景，属于中等偏高；但原始目标是“快速理解”，不需要重型 deep research。 |
| 用户意图匹配 | 6/10 | 回答了问题，但明显超出“快速理解”的需求，变成了长篇行业综述。 |
| 简报质量 | 7/10 | 结构清晰、维度完整，但把“快速理解”改成“深入研究”，引入过多产品和资料要求，导致范围膨胀。 |
| 最终报告结构 | 8/10 | 章节清楚，定义、场景、对比、产品实践都有覆盖。 |
| 核心观点准确性 | 7.5/10 | Context Engineering 与 Prompt Engineering 的主线区分基本正确。 |
| **真实性/幻觉性** | 6/10 | 主体合理，但存在可疑链接、二手资料权威化、部分数字缺少强证据的问题。 |
| **证据链质量** | 5.5/10 | 引用数量多，但没有逐条支撑关键 claim；一手、二手、营销博客混用。 |
| DeepResearch 场景分析 | 7.5/10 | 抓住了长文档、检索结果、压缩、隔离等核心问题，但可以更贴近实际 agent 流程。 |
| Coding Agent 场景分析 | 7.5/10 | AST、代码图谱、repo-map、规则文件等方向正确，但部分产品细节需要更强官方依据。 |
| Prompt Engineering 对比 | 8/10 | “怎么说”与“看什么”的对比清楚，但“Prompt Engineering 是 Context Engineering 子集”略绝对。 |
| 完整性 | 8/10 | 覆盖很完整，甚至过完整。 |
| **Token 用量** | 2/10 | 927.5K token 对该任务严重过量，属于明显 fan-out / token bloat。 |
| **耗时** | 3/10 | 1893 秒约 31.5 分钟，与“快速理解”不匹配。 |
| 总体评分 | **6.8/10** | 内容有价值，但执行策略、证据洁净度和成本控制明显扣分。 |



2. 简单任务 中等质量但为扇出且遵循约束 positive


| 维度 | 评分 | 判断 |
|---|---:|---|
| 任务难度 | 4/10 | 低到中等难度。MCP 是新概念，但用户限制很明确。 |
| 简报质量 | 9/10 | 很好地保留了用户的来源数、字数、表格、范围限制，没有明显扩写。 |
| 最终回答结构 | 9/10 | 表格为主，紧凑清楚，符合“简要说明”。 |
| 用户意图匹配 | 9/10 | 准确覆盖“是什么、解决什么问题、核心组件、适合场景”。 |
| 真实性/幻觉性 | 8/10 | 主体准确；“M×N → M+N”属于合理抽象，但最好标为“可理解为”。 |
| **证据链质量** | 5/10 | 来源数量合规，但 [2] 中文翻译站、[3] 个人博客不如官方来源；应优先用 MCP 官方文档 + Anthropic 发布页。 |
| 来源约束遵守 | 8/10 | 严格控制在 3 个来源内，这是优点。 |
| 字数约束 | 8.5/10 | 正文基本符合 600 字以内；若把 Sources URL 也算入，略有风险。 |
| 范围控制 | 9.5/10 | 没有扩展到无关主题，表现很好。 |
| Token 用量 | 6.5/10 | 103K 对普通问答偏高，但作为 DeepResearch 受限检索测试可以接受。 |
| **耗时** | 3/10 | 653.89s 约 10.9 分钟，仍偏长，是主要扣分项。 |
| 总体评分 | 7.0/10 | 最终输出质量好，**约束遵守强**；主要问题是来源可进一步官方化、耗时偏长。 |


3.中等难度 用量较大且不完整缺失了实质事实 negative

| 维度 | 评分 | 判断 |
|---|---:|---|
| 任务难度 | 6/10 | 中等偏上。关键点不是信息难找，而是要区分三款产品的官方公开能力、引用机制、数据源边界和局限，且不能把未核验内容写成事实。 |
| 总体评分 | 4.5/10 | 结构完整，但核心事实失衡，尤其将 Perplexity Research 全部写成“未公开”，属于明显错误；同时存在过度探索和低权威来源混入。 |
| 任务理解 | 6/10 | 理解了要比较 OpenAI、Gemini、Perplexity 的五个维度，但没有贯彻“简要比较”和“只使用可核验公开资料”。 |
| 简报质量 | 5/10 | 简报保留了五个维度和“未公开”要求，但把原始输入中的“简要比较”扩写成“详细比较”，导致后续研究范围放大。 |
| 输出契合度 | 5/10 | 输出按五个维度展开，但篇幅过大，细节过多，不符合“简要”；Perplexity 部分缺失导致三方比较不成立。 |
| **真实性与引用忠实度** | 4/10 | OpenAI、Gemini 部分有部分真实公开信息，但夹杂疑似过新、难核验或二手来源内容；Perplexity“未公开”判断明显不真实。 |
| 证据来源质量 | 4/10 | 来源数量很多，但官方文档占比不够，混入 YouTube、Reddit、论坛、二手博客等，不符合“优先官方/权威资料”的要求。 |
| **“未公开”执行情况** | 3/10 | 对无法确认内容标注“未公开”的意识是对的，但把 Perplexity 整体误判为未公开，属于过度保守导致的信息缺失。 |
| 产品边界清晰度 | 5/10 | OpenAI/Gemini 的用户端、API、第三方连接能力混在一起写，Gemini 部分尤其容易把模型能力、产品能力和未来/预览能力混淆。 |
| **Token 用量** | 1/10 | 385.3K token 对这种简要对比任务严重过高，属于明显 token bloat 和研究过度扇出。 |
| **耗时** | 2/10 | 911.59s 约 15 分钟，远超该任务合理耗时，说明检索和阅读策略没有收敛。 |
| **探索效率** | 2/10 | 27 个来源仍没有覆盖 Perplexity 官方公开信息，说明不是资料不足，而是搜索路径和来源筛选失败。 |

4.高级难度 用量很大探索效率很低且真实性与引用忠实度差有明显的乱造幻觉 negative

| 维度 | 评分 | 判断 |
|---|---:|---|
| 任务难度 | 8/10 | 难度较高。关键点在于既要设计 DeepResearch 架构，又要结合模型调度、上下文治理、评估指标和成本控制，还要求依据官方文档、论文、权威博客或开源项目资料。 |
| 总体评分 | 5.5/10 | 方案框架完整、工程方向基本正确，但事实核验和引用忠实度明显不足，存在伪来源、错引、过度确定和参数臆断问题。 |
| 任务理解 | 8/10 | 基本理解了用户要的是“成本感知模型调度 + 上下文治理方案”，覆盖了 supervisor、researcher、summarization、compression、final_report、critic/evaluator 等阶段。 |
| 简报质量 | 8/10 | 简报完整保留了输入要求，并补充了“灵活建议、多种可选方案、优先权威资料”等合理约束，没有明显偏离任务。 |
| 输出契合度 | 8/10 | 输出包含总体架构、阶段模型调度表、上下文治理表、supervisor/researcher 是否分开、默认参数和评估指标表，形式上高度契合要求。 |
| 结构完整性 | 9/10 | 结构非常完整，层次清楚，表格覆盖面充分，读起来像一套完整设计方案。 |
| **真实性与引用忠实度** | 3/10 | 最大硬伤。正文引用编号错乱，部分来源不存在或无法支持结论，部分具体数字和业界实践疑似编造或过度概括。 |
| **证据来源质量** | 4/10 | 使用了 Anthropic、LangChain、RAGAS、TruLens、STORM 等方向正确的来源，但混入伪 arXiv、泛化博客、错配来源，且国产模型能力与价格缺少逐项官方支撑。 |
| 模型调度合理性 | 6/10 | “低成本模型做路由/压缩，高能力模型做研究/最终综合/评估”的原则合理；但具体到 DeepSeek-V4-Flash、Qwen-Max、GLM-5.2、Kimi-128k 等阶段分配，缺乏充分官方数据支撑。 |
| 上下文治理方案质量 | 7/10 | 输入裁剪、搜索压缩、证据保留、引用校验、停止条件、fan-out 限制这些方向都对；但 LLMLingua、Map-Reduce、CitationAgent、CaRT 等依据混杂，部分细节写得过满。 |
| 默认参数建议 | 5/10 | 3-5 个 researcher、3-5 轮搜索、每轮 2-3 个工具调用、摘要 1000-2000 token 等建议有工程直觉，但依据不足，不能说是“基于 LangGraph/AutoGen/Anthropic 经验数据设定”。 |
| 评估指标质量 | 7/10 | 指标表覆盖真实性、完整性、证据链、Token、耗时、fan-out，方向正确；但目标阈值如 ≥0.90、降低 60%+、fan-out efficiency ≥0.60 缺少依据。 |
| “未公开/不确定”处理 | 3/10 | 对未核验信息没有保守处理，很多应该写“建议值/经验假设/需实验校准”的内容被写成确定事实。 |
| **Token 用量** | 0.5/10 | 1.2M token 对这类方案设计任务严重失控，属于典型 token bloat 和研究过度扇出。 |
| 耗时 | 1/10 | 1375.56s 约 22.9 分钟，明显过长；如果最终仍存在伪来源和错引，说明耗时没有转化为可靠性。 |
| **探索效率** | 1/10 | 消耗极高但证据链质量很差，说明不是“查得不够”，而是检索、筛源、引用校验和压缩策略失败。 |


5. 高低难度 严重过度扇出和扩张且事实混乱证据链错误 negative

| 维度 | 评分 | 判断 |
|---|---:|---|
| 任务难度 | 8/10 | “当前最前沿 deepresearch 是哪个”属于**开放集合 SOTA 判断任务**，需要候选发现、权威来源查证、横向比较和不确定性表达，难度较高。 |
| 输入匹配度 | 5/10 | 输出覆盖了“最前沿系统”和“性能”主题，但没有很好满足“请简单介绍”的表达要求，明显过长。 |
| 简报质量 | 6/10 | 简报意识到这是横向比较任务，方向基本合理；但缺少候选数量、来源数量、输出篇幅、token 和耗时边界。 |
| 最终报告质量 | 5/10 | 结构完整、覆盖面广，但重点不收敛，没有把“没有唯一公认最强”作为核心结论。 |
| **真实性 / 幻觉性** | 4/10 | 存在较多口径混用：把 Deep Research 产品、底座模型、开源框架、通用 agent benchmark 放在一起比较，部分指标可疑。 |
| 完整性 | 7/10 | 覆盖范围很广，但有效完整性一般；缺少“哪些系统不能直接横比”的分层说明。 |
| **证据链** | 3/10 | 引用编号断裂，正文引用超过 Sources 范围，关键性能数据难以追溯。 |
| **Token 用量** | 2/10 | 787.6K token 对这个任务明显过量。任务值得深搜，但不应接近百万 token。 |
| 耗时 | 2/10 | 1226.59 秒明显过长，除非用户明确要求完整行业研究报告。 |
| 总体评分 | 5/10 | 任务方向理解有价值，但研究边界、证据治理、事实口径和最终压缩都存在明显问题，属于“高难任务下的失控式完成”。 |
