# Agentic Deep Research 2.0：Codex主控实施方案

## 一、你的角色

你正在改造仓库：

```text
magic-ZSS/agentic-deep-research
```

目标是在保留现有LangGraph Supervisor—Researcher多智能体研究流程的基础上，增加：

1. 基于PaperQA2的本地知识库和证据检索；
2. PDF、Markdown、HTML/Wikipedia快照和历史高质量查询的统一接入；
3. Agentic RAG：本地检索不足时联网搜索，验证后增量写回知识库；
4. 文档版本、过时、错误、冲突、隔离和软删除机制；
5. Filesystem MCP和自定义Knowledge MCP；
6. Working、Episodic、Semantic、Procedural、User Preference五类记忆；
7. Claim—Evidence—Source引用验证；
8. 基于DeepEval的Agent、RAG、记忆和成本回归评测。

本项目追求：

```text
可靠效果
+ 可解释工程设计
+ 可展示评测结果
+ 求职含金量
+ 尽快落地
```

不得为了架构华丽度加入当前阶段不需要的技术。

---

# 二、强制执行原则

## 1. 分阶段执行

一次只执行一个阶段。

首次收到本方案时，只执行：

```text
阶段0：参考仓库准备与Baseline
```

阶段完成后停止，等待用户明确要求继续下一阶段。

不得提前实现下一阶段内容。

## 2. 先理解当前项目

每个阶段开始前必须读取：

```text
AGENTS.md
README.md
pyproject.toml
src/open_deep_research/state.py
src/open_deep_research/configuration.py
src/open_deep_research/deep_researcher.py
src/open_deep_research/prompts.py
src/open_deep_research/utils.py
tests/
feature_list.json
progress.md
session-handoff.md
```

如文件不存在，记录实际情况，不要虚构。

## 3. 保留旧流程

所有新增能力必须通过配置开关启用。

至少保留：

```python
enable_knowledge_base
enable_paperqa_retrieval
enable_agentic_rag
enable_memory
enable_citation_validation
```

关闭这些开关后，原有DeepResearch流程仍应可运行。

## 4. 不进行不必要重构

不得在功能尚未稳定时：

* 重写整个DeepResearch主图；
* 替换现有Supervisor；
* 替换现有Tavily搜索；
* 删除现有Researcher压缩节点；
* 引入Graphiti、RAGFlow、R2R或完整GraphRAG；
* 同时部署多个向量数据库；
* 创建复杂微服务架构；
* 开发与核心能力无关的前端。

## 5. 测试优先

每阶段都必须包含：

* 单元测试；
* 最小集成测试；
* 回归测试；
* 明确的验收命令；
* 实际测试结果。

测试失败时不得宣称阶段完成。

## 6. 参考仓库使用规范

优先借鉴数据结构、接口、测试方式和关键算法，不要整段盲目复制。

复用源码时必须：

* 确认许可证；
* 保留必要版权声明；
* 在`THIRD_PARTY_NOTICES.md`记录来源；
* 记录参考仓库Commit SHA；
* 标明本项目进行了哪些适配。

---

# 三、参考仓库准备

## 3.1 目录

统一使用：

```text
docs/reference/
```

参考仓库只供Codex阅读和借鉴，不作为本项目Python包的一部分。

在`.gitignore`中忽略克隆内容，但保留：

```text
docs/reference/README.md
docs/reference/refs.lock.json
```

建议规则：

```gitignore
docs/reference/*/
!docs/reference/README.md
!docs/reference/refs.lock.json
```

## 3.2 第一批必须准备的仓库

```powershell
New-Item -ItemType Directory -Force docs/reference

git clone --depth 1 https://github.com/Future-House/paper-qa.git docs/reference/paper-qa
git clone --depth 1 https://github.com/confident-ai/deepeval.git docs/reference/deepeval
git clone --depth 1 https://github.com/langchain-ai/langmem.git docs/reference/langmem
git clone --depth 1 https://github.com/langchain-ai/langgraph.git docs/reference/langgraph
git clone --depth 1 https://github.com/modelcontextprotocol/servers.git docs/reference/mcp-servers
```

将每个仓库的以下信息记录到`docs/reference/refs.lock.json`：

```json
{
  "repository": "",
  "url": "",
  "commit": "",
  "default_branch": "",
  "license": "",
  "used_for": []
}
```

## 3.3 每个仓库的借鉴重点

### PaperQA2

重点研究：

```text
文档对象和元数据
文档Hash与去重
PDF页码保存
Chunk/Text数据结构
Evidence Context
证据召回与重排序
Contextual Summarization
引用生成
索引缓存
固定检索流程与Agentic流程的边界
```

优先查看：

```text
src/paperqa/docs.py
src/paperqa/types.py
src/paperqa/agents/tools.py
src/paperqa/settings.py
tests/
```

不得直接将PaperQA2完整Agent嵌套在现有Researcher中。

### DeepEval

重点研究：

```text
LangGraph Callback集成
Task Completion
Tool Correctness
Faithfulness
Contextual Precision
Contextual Recall
自定义G-Eval或Metric
Dataset和Golden管理
pytest式评测
```

### LangMem

重点研究：

```text
记忆搜索工具
记忆写入工具
Semantic Memory
Episodic Memory
Procedural Memory
LangGraph Store集成
Memory Namespace
Memory更新与合并
```

不得直接开启无约束的后台自动记忆写入。

### LangGraph

重点研究：

```text
Checkpoint
Store
Postgres或SQLite持久化
State Reducer
子图
中断与恢复
多用户Namespace
```

### MCP Servers

重点研究：

```text
Filesystem Server
Allowed Roots
路径规范化
路径穿越防护
readOnlyHint
idempotentHint
destructiveHint
工具参数校验
```

官方实现仅作为参考，不能默认视为生产级安全实现。

## 3.4 暂不克隆的可选仓库

以下仓库只有进入引用验证阶段后才允许克隆：

```text
stanford-oval/storm
Libr-AI/OpenFactVerification
mbzuai-nlp/fire
```

使用目的：

```text
STORM：
全局来源注册、Outline-first和分章节写作

OpenFactVerification：
原子Claim拆分和事实验证流程

FIRE：
基于置信度的迭代检索和停止机制
```

如果现有设计已经能够完成对应功能，不得为了“参考更多项目”而引入它们。

---

# 四、确定的技术范围

## 4.1 Python版本

项目统一为：

```text
Python >= 3.11
```

不得使用`uv`作为用户默认安装方式。

保持Windows原生和conda可用。

## 4.2 MVP存储方案

第一版使用：

```text
SQLite
+ 本地文件快照
+ PaperQA2索引
```

原因是快速落地和降低环境复杂度。

必须通过Repository接口隔离存储实现，未来可以迁移到PostgreSQL，但本轮路线不以PostgreSQL迁移为完成条件。

## 4.3 数据存储职责

```text
SQLite：
文档元数据、版本、来源、Evidence、Memory、Run记录

文件系统：
原始PDF、Markdown、HTML和不可变快照

PaperQA2索引：
可重建的检索投影

LangGraph Checkpointer：
当前Thread运行状态
```

PaperQA2索引不是唯一事实来源。

## 4.4 不进入当前路线的技术

```text
Neo4j
Graphiti
完整GraphRAG
RAGFlow
R2R
Kafka
微服务拆分
复杂前端
自动模型训练
```

只有核心路线完成并有评测证明后，才允许作为后续扩展讨论。

---

# 五、统一阶段完成标准

每阶段完成后必须输出：

```text
1. 本阶段目标
2. 实际完成内容
3. 修改文件列表
4. 参考了哪些仓库和具体模块
5. 关键设计决策
6. 测试命令
7. 测试结果
8. 未完成项
9. 风险与限制
10. 下一阶段的前置条件
```

同时更新：

```text
feature_list.json
progress.md
session-handoff.md
docs/implementation/phase-<N>.md
```

必须执行项目实际适用的：

```powershell
ruff check .
mypy src
pytest -q
```

如果现有项目暂时无法全量通过，必须：

* 区分原有失败和本阶段新增失败；
* 本阶段新增测试必须全部通过；
* 不得隐藏失败。

建议新增统一阶段验收入口：

```powershell
python scripts/validate_phase.py --phase 0
```

后续阶段依次支持`--phase 1`至`--phase 7`。

---

# 六、阶段0：参考仓库准备与Baseline

## 目标

在不改变核心运行逻辑的情况下：

1. 准备参考仓库；
2. 固定当前系统Baseline；
3. 建立最小DeepEval评测骨架；
4. 建立后续阶段文档和验收机制。

## 实现范围

### A. 参考仓库

完成：

```text
docs/reference/README.md
docs/reference/refs.lock.json
THIRD_PARTY_NOTICES.md
```

`README.md`说明每个仓库：

* 用途；
* 允许参考的模块；
* 禁止直接照搬的部分；
* Commit SHA；
  -许可证。

### B. Baseline数据集

将现有测试用例整理成至少三类：

```text
simple
medium
complex
```

第一版至少选择：

```text
1个简单事实任务
1个中等多对象比较任务
1个复杂技术方案任务
```

保存：

```text
输入
Research Brief
最终输出
Token
时长
工具调用数
人工评估结果
```

不要在阶段0重新设计全部评测标准。

### C. 最小DeepEval

只接入：

```text
TaskCompletionMetric
FaithfulnessMetric
```

并增加确定性指标：

```text
token_count
duration_seconds
tool_call_count
source_count
output_length
finish_reason
```

LLM Judge不可进入默认快速单元测试。

划分：

```text
smoke eval：低成本，开发时运行
full eval：显式命令运行
```

## 验收测试

必须通过：

```text
T0-1：5个参考仓库均存在且记录Commit SHA
T0-2：refs.lock.json可被解析
T0-3：Baseline三个测试用例可被加载
T0-4：至少一个Baseline用例能完整运行
T0-5：评测结果能保存为JSON
T0-6：Token、耗时和工具调用数均不为空
T0-7：关闭DeepEval时原系统行为不变
T0-8：原有单元测试无新增失败
```

建议命令：

```powershell
python scripts/validate_phase.py --phase 0
pytest tests/evals/test_baseline_dataset.py -q
```

## 完成边界

阶段0不得：

* 增加知识库；
* 修改Researcher输出结构；
* 接入PaperQA检索；
* 增加记忆；
* 修改主图节点。

阶段0完成后必须停止。

---

# 七、阶段1：知识、来源和证据基础模型

## 目标

建立后续所有RAG和记忆能力依赖的稳定数据结构。

## 实现范围

新增核心对象：

```text
KnowledgeDocument
DocumentVersion
KnowledgeChunk
SourceRecord
EvidenceCard
Requirement
QueryArchive
```

新增：

```text
Repository Protocol
SQLite Repository
InMemory Repository
ID与Hash工具
URL规范化
版本关系
状态Reducer
```

文档状态至少包括：

```text
candidate
active
stale
superseded
quarantined
archived
```

Evidence必须能回溯到：

```text
Evidence
→ Chunk
→ DocumentVersion
→ Source
```

保留现有：

```text
compressed_research
raw_notes
```

此阶段不得立即删除旧字段。

## 验收测试

```text
T1-1：相同URL和相同正文生成相同Source ID
T1-2：相同URL但正文变化生成新Version
T1-3：旧Version不会被覆盖
T1-4：重复Evidence可以按ID去重
T1-5：并行Researcher结果合并后来源不重复
T1-6：Evidence可回溯到原文和来源
T1-7：SQLite关闭重开后数据仍存在
T1-8：禁用新数据层时原流程仍可运行
```

完成后停止。

---

# 八、阶段2：文档导入与PaperQA2本地检索

## 目标

完成真正可用的本地知识库MVP。

## 支持格式

第一版必须支持：

```text
PDF
Markdown
HTML或网页快照
历史高质量查询归档
```

Wikipedia按普通HTML快照处理，但必须保存：

```text
canonical_url
retrieved_at
revision_id（能够获取时）
```

## 实现范围

新增：

```text
DocumentIngestor接口
PDFIngestor
MarkdownIngestor
HTMLIngestor
QueryArchiveIngestor
PaperQAAdapter
knowledge_search
knowledge_read
```

PaperQA2只负责：

```text
解析或接收文档
建立索引
候选证据召回
证据重排序
Contextual Summary
页码或位置返回
```

Researcher调用本项目的：

```python
KnowledgeRetriever
```

不得直接依赖PaperQA2内部类型。

## 验收样本

在`tests/fixtures/knowledge/`准备：

```text
一篇小型PDF
一篇Markdown博客
一个HTML快照
一个历史查询JSON
```

## 验收测试

```text
T2-1：四类文档均能成功导入
T2-2：重复导入同一文件不会创建重复文档
T2-3：修改Markdown后生成新Version
T2-4：PDF检索结果包含页码
T2-5：Markdown检索结果包含标题层级
T2-6：knowledge_search返回Source ID和Excerpt
T2-7：knowledge_read可根据Chunk ID读取上下文
T2-8：不存在结果时返回空结果而不是幻觉
T2-9：关闭PaperQA后系统回退旧搜索流程
T2-10：PaperQA不得启动独立DeepResearch Agent
```

完成后停止。

---

# 九、阶段3：Agentic RAG和知识生命周期

## 目标

实现：

```text
先查本地
→ 证据不足时联网
→ 验证候选来源
→ 写回知识库
→ 后续任务复用
```

## 新增流程

```text
retrieve_local_knowledge
grade_local_coverage
search_external_sources
ingest_candidate_sources
validate_candidate_sources
promote_or_quarantine
```

Coverage Grader至少判断：

```text
相关性
Requirement覆盖
证据直接性
来源时效
来源权威性
冲突情况
```

## 写回规则

网络结果默认进入：

```text
candidate
```

只有满足规则后进入：

```text
active
```

低质量或冲突未解决内容进入：

```text
quarantined
```

Agent不允许硬删除，只允许：

```text
propose_stale
propose_supersede
propose_quarantine
```

## 验收测试

```text
T3-1：本地证据充分时不触发Web Search
T3-2：本地证据不足时触发Web Search
T3-3：新来源首先进入candidate
T3-4：验证通过后才进入active
T3-5：低质量来源进入quarantined
T3-6：stale和superseded版本默认不参与当前检索
T3-7：相同查询第二次运行可以命中历史知识
T3-8：相同查询第二次Web调用数低于第一次
T3-9：错误知识被隔离后不会继续召回
T3-10：所有状态变化存在Audit记录
```

对于重复查询测试，第二次Web调用数目标至少降低50%；若第一次只产生一次调用，则要求第二次不新增Web调用。

完成后停止。

---

# 十、阶段4：Filesystem MCP与Knowledge MCP

## 目标

让原项目和外部Agent能够安全访问本地文件和知识库。

## 实现范围

### Filesystem MCP

使用官方Filesystem MCP参考实现，Windows通过：

```text
cmd /c npx
```

只允许访问：

```text
data/knowledge/import
data/knowledge/active
```

默认：

```text
active只读
import可写
```

### Knowledge MCP

实现自定义Python MCP Server，第一版提供：

```text
kb_search
kb_read
kb_get_source
kb_search_past_queries
memory_search
```

受控写操作只提供：

```text
kb_propose_ingest
kb_propose_quarantine
kb_propose_stale
```

不得提供：

```text
hard_delete
force_promote
force_memory_write
```

## 验收测试

```text
T4-1：MCP可以读取允许目录文件
T4-2：MCP不能访问允许目录外文件
T4-3：../路径穿越请求被拒绝
T4-4：Knowledge MCP搜索结果与内部Retriever一致
T4-5：只读工具不会修改文件
T4-6：写入提议只进入candidate或proposal状态
T4-7：破坏性操作需要显式治理流程
T4-8：Windows配置样例可以启动
```

完成后停止。

---

# 十一、阶段5：分层记忆系统

## 目标

实现足够完整、但不过度复杂的记忆系统。

## 记忆类型

### Working Memory

使用：

```text
LangGraph State
+ SQLite Checkpointer
```

支持当前Thread中断恢复。

### Episodic Memory

保存高质量研究经历：

```text
任务类型
研究计划
有效工具
失败动作
最终质量
经验总结
```

### Semantic Memory

保存稳定事实，但必须绑定：

```text
Source ID
Evidence ID
有效时间
置信度
```

### Procedural Memory

保存可复用研究策略。

第一版不自动修改系统提示词。

程序记忆只允许：

```text
人工批准
或同类任务至少3次成功后转为active
```

### User Preference Memory

只保存用户明确表达且稳定的偏好。

## Memory Write Gate

所有长期记忆经过：

```text
类型判断
重要性判断
来源检查
重复检查
时效检查
敏感性检查
Promotion
```

## Namespace

至少隔离：

```text
user_id
memory_type
project_id
```

## 验收测试

```text
T5-1：运行中断后可以从Checkpoint恢复
T5-2：不同用户的Memory完全隔离
T5-3：明确用户偏好可跨Thread召回
T5-4：Semantic Memory没有Evidence时拒绝写入
T5-5：低质量研究运行不进入Episodic Memory
T5-6：过期Semantic Memory不会作为当前事实返回
T5-7：重复Memory被合并而不是无限追加
T5-8：Procedural Memory不会因单次任务自动激活
T5-9：Memory返回时包含类型、来源和时间
T5-10：禁用Memory后研究流程仍可运行
```

完成后停止。

---

# 十二、阶段6：引用验证与报告修复

## 目标

直接解决项目中长期存在的来源质量和引用忠实度问题。

## 流程

```text
Draft Report
→ Atomic Claim Extraction
→ Check-worthiness
→ Claim–Evidence Retrieval
→ Entailment Validation
→ Source Authority Validation
→ Temporal Validation
→ Local Report Repair
→ Final Citation Rendering
```

## 验证结果

```text
fully_supported
partially_supported
unsupported
contradicted
not_checkable
```

## 修复动作

```text
keep
weaken
add_citation
rewrite
remove
targeted_research
```

只修改有问题的Claim，不得默认重新生成整份报告。

最终来源编号和来源列表必须由Source Registry程序化生成。

## 阶段开始前可选参考仓库

只有本阶段允许新增：

```text
stanford-oval/storm
Libr-AI/OpenFactVerification
mbzuai-nlp/fire
```

先判断是否确实需要；没有必要时不克隆。

## 验收测试

准备固定故障样例：

```text
MetaGPT来源被用于支持AutoGen机制
旧版本法律被用于支持新版本规定
企业宣称被写成行业统一结论
无来源的精确性能数字
一个引用编号对应多个URL
```

必须通过：

```text
T6-1：MetaGPT与AutoGen错配被识别
T6-2：旧版本来源被标记为时效问题
T6-3：企业宣称被降级或明确标注
T6-4：无依据数字被删除或标记证据不足
T6-5：unsupported Claim不能进入最终报告
T6-6：正文引用与来源表100%一致
T6-7：所有引用Source ID均存在
T6-8：局部修复不破坏其他章节
T6-9：Citation Validator可单独关闭
T6-10：复杂测试报告能够完整生成
```

完成后停止。

---

# 十三、阶段7：完整DeepEval回归与求职展示

## 目标

证明新系统相对于Baseline确实更好。

## 评测层

### Agent

```text
Task Completion
Tool Correctness
Step Efficiency
Plan Adherence
```

### RAG

```text
Faithfulness
Contextual Precision
Contextual Recall
Knowledge Hit Rate
Verified Evidence Recall
```

### Citation

```text
Citation Coverage
Citation Entailment
Unsupported Claim Rate
Source Authority
Stale Evidence Rate
```

### Memory

```text
Memory Precision
Memory Reuse Rate
Stale Memory Usage
Incorrect Memory Write Rate
```

### Cost

```text
tokens_per_verified_claim
tool_calls_per_verified_claim
cost_per_completed_requirement
duration_seconds
```

## 评测模式

```text
smoke：
小数据、低成本、可进入普通CI

full：
完整LLM Judge，手动或定时运行
```

不得把昂贵DeepEval全量测试放进每次普通单元测试。

## 消融实验

至少比较：

```text
Baseline
Baseline + PaperQA2
+ Agentic RAG
+ Memory
+ Citation Validator
```

## 最终验收

```text
T7-1：简单、中等、复杂数据集均可运行
T7-2：最终任务完成率不低于Baseline
T7-3：复杂任务引用忠实度高于Baseline
T7-4：引用编号错误率为0
T7-5：历史重复查询Web调用明显下降
T7-6：历史重复查询Token成本低于首次运行
T7-7：错误知识失效后不再召回
T7-8：Memory跨任务复用可被观察和量化
T7-9：生成机器可读JSON评测报告
T7-10：生成可放入README的对比结果
```

对于LLM Judge波动，连续运行三次，报告平均值和标准差，不得只选择最好的一次。

---

# 十四、建议的最终目录

```text
src/open_deep_research/
├── knowledge/
│   ├── models.py
│   ├── repository.py
│   ├── sqlite_repository.py
│   ├── lifecycle.py
│   ├── ingestion/
│   ├── retrieval/
│   └── paperqa_adapter.py
├── evidence/
│   ├── models.py
│   ├── consolidator.py
│   ├── claim_extractor.py
│   └── citation_validator.py
├── memory/
│   ├── models.py
│   ├── repository.py
│   ├── recall.py
│   ├── write_gate.py
│   └── policies.py
├── mcp_servers/
│   └── knowledge_server.py
├── evaluation/
│   ├── datasets.py
│   ├── metrics.py
│   ├── deepeval_adapter.py
│   └── reports.py
└── ...

data/
├── knowledge/
│   ├── import/
│   ├── active/
│   ├── quarantined/
│   ├── archived/
│   └── snapshots/
└── indexes/

tests/
├── fixtures/
├── unit/
├── integration/
└── evals/

docs/
├── reference/
└── implementation/
```

目录可以根据现有项目结构小幅调整，但不得把所有代码塞进`deep_researcher.py`。

---

# 十五、最终项目必须能够演示的场景

最终Demo至少覆盖：

## Demo 1：本地论文研究

```text
导入PDF论文
→ Agent检索论文
→ 输出带页码证据
```

## Demo 2：增量知识更新

```text
本地知识不足
→ Web搜索
→ 验证新资料
→ 写回知识库
→ 第二次查询复用
```

## Demo 3：错误知识失效

```text
错误来源进入隔离
→ 相关Evidence失效
→ 后续查询不再召回
```

## Demo 4：MCP文件访问

```text
Agent通过Filesystem MCP读取允许目录
→ 无法访问目录外文件
```

## Demo 5：长期记忆

```text
保存用户稳定偏好
→ 新Thread召回
→ 不同用户相互隔离
```

## Demo 6：引用修复

```text
构造错误引用报告
→ Validator发现
→ 局部修复
→ 输出一致来源表
```

## Demo 7：评测对比

```text
Baseline
vs
最终版本
```

展示：

```text
完成率
引用忠实度
Token
工具调用
耗时
知识复用率
```

---

# 十六、现在开始执行

当前只执行：

```text
阶段0：参考仓库准备与Baseline
```

执行前先检查当前工作区状态和已有文件，不覆盖用户未提交的修改。

阶段0完成并通过全部验收测试后：

1. 输出阶段总结；
2. 更新进度文件；
3. 停止执行；
4. 不进入阶段1；
5. 等待用户下一条明确指令。
