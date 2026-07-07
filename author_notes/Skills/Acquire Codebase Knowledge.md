# overview

## 1.描述
当用户明确要求对一个已有代码库进行映射、文档化，或完成代码库入门引导时，使用此 Skill。

当出现类似以下提示时触发：

* “map this codebase” 指的是“梳理/绘制这个代码库的整体结构”，不是画图本身，而是建立项目地图，例如目录、模块、入口、依赖关系。
* “document this architecture”指的是“把这个项目的架构写成文档”，例如生成架构说明、模块关系、运行流程等。
* “onboard me to this repo”指的是“带我入门这个仓库”，让用户快速理解这个项目怎么组织、怎么运行、主要文件在哪里。
* “create codebase docs”指的是“创建代码库文档”，通常会生成类似 docs/codebase/ 这样的项目说明文档。

不要因为常规功能实现、Bug 修复或局部代码编辑而触发此 Skill，除非用户要求进行仓库级别的发现/探索。

## 2.使用

$acquire-codebase-knowledge

请对当前项目进行 codebase onboarding。
要求：
1. 不要修改业务代码；
2. 生成 docs/codebase/ 下的七份项目理解文档；
3. 所有文档内容使用简体中文；
4. 只写能从文件、配置或终端输出验证的内容；
5. 不确定的地方标记为 [TODO]；
6. 需要我确认的地方标记为 [ASK USER]。
7. 
---

请先读取 AGENTS.md 和 docs/codebase/，恢复项目上下文。
先用中文简要说明你对项目的理解，然后等待我的具体任务。

---

请先读取 AGENTS.md 和 docs/codebase/，恢复项目上下文。
然后再根据当前任务阅读相关源码，不要直接大范围扫描整个项目。

我的任务是：……

---

在AGENT.md里增加：

#Codex Instructions

每次新对话开始时，先读取 `docs/codebase/` 下的项目文档，恢复项目上下文。除非用户明确要求重新生成项目文档，否则不要自动运行 `$acquire-codebase-knowledge`。

默认使用简体中文回答。修改代码前，必须先阅读与任务直接相关的源码、测试和配置文件。

---
Skill 负责生成/大更新，docs/codebase/ 负责长期沉淀，AGENTS.md 负责提醒每次开发后维护它们。

| 场景              | 推荐做法                                   |
| --------------- | -------------------------------------- |
| 项目结构、技术栈、运行方式大改 | 重新运行 `$acquire-codebase-knowledge`     |
| 只改了某个模块、测试、配置   | 让 Codex **局部更新对应文档**                   |
| 每次开发结束后想同步记录    | 在任务最后要求 Codex 更新 `docs/codebase/` 相关条目 |

开发后局部更新：
请根据本次修改，局部更新 docs/codebase/ 中相关文档。

要求：
1. 不要重新全量扫描整个项目；
2. 只更新与本次改动相关的文档；
3. 保持简体中文；
4. 每个新增或修改的结论都要有 evidence 文件路径；
5. 不确定的地方标记为 [TODO]；
6. 涉及团队意图的问题标记为 [ASK USER]；
7. 最后说明你更新了哪些文档、为什么更新。

知道具体该更新哪份文档：
请根据刚才的代码修改，只更新：

- docs/codebase/ARCHITECTURE.md
- docs/codebase/TESTING.md
- docs/codebase/CONCERNS.md

不要改其他文档。保持中文，并补充 evidence 路径。

大改时重新运行 Skill：
$acquire-codebase-knowledge

项目最近发生了较大变化，请重新生成或全面更新 docs/codebase/ 下的七份中文项目文档。

要求：
1. 保留仍然正确的内容；
2. 删除已经过时的内容；
3. 所有结论必须能从文件、配置或终端输出验证；
4. 每份文档保留 evidence；
5. 最后列出主要变化、过时内容和 [ASK USER] 问题。

加到AGENTS.md：
## Codebase docs 维护规则

本项目使用 `docs/codebase/` 作为长期项目知识库。

当代码、配置、测试、运行入口、架构或外部集成发生变化时，Codex 在完成任务后应检查是否需要同步更新以下文档：

- `docs/codebase/STACK.md`
- `docs/codebase/STRUCTURE.md`
- `docs/codebase/ARCHITECTURE.md`
- `docs/codebase/CONVENTIONS.md`
- `docs/codebase/INTEGRATIONS.md`
- `docs/codebase/TESTING.md`
- `docs/codebase/CONCERNS.md`

默认只做局部更新，不要每次全量重写。

更新规则：

1. 所有文档内容使用简体中文；
2. 代码、命令、路径、类名、函数名、配置键名保持英文；
3. 新增或修改的结论必须有 evidence 文件路径；
4. 无法确认的内容标记为 `[TODO]`；
5. 需要用户确认团队意图的内容标记为 `[ASK USER]`；
6. 删除或修正已经过时的描述；
7. 最后说明更新了哪些文档，以及为什么更新。


---
---
---


name: acquire-codebase-knowledge

description: '当用户明确要求对一个已有代码库进行映射、文档化，或完成代码库入门引导时，使用此 skill。对于类似 "map this codebase"、"document this architecture"、"onboard me to this repo" 或 "create codebase docs" 的提示应触发。对于常规功能实现、Bug 修复或局部代码编辑，不要触发，除非用户要求进行仓库级别的发现/探索。'

license: MIT

compatibility: '跨平台。需要 Python 3.8+ 和 git。从目标项目根目录运行 scripts/scan.py。'

metadata:

version: "1.3"

enhancements:
- 多语言 manifest 检测（支持 25+ 种语言）
- CI/CD pipeline 检测（10+ 个平台）
- 容器与编排检测
- 按语言统计代码指标
- 安全与合规配置检测
- 性能测试标记

argument-hint: '可选：指定要关注的区域，例如 "architecture only"、"testing and concerns"'

--------------------------------------------------------------------------

# 获取代码库知识

在 `docs/codebase/` 中生成七份已填充的文档，覆盖高效参与该项目所需的一切内容。只记录可以从文件或终端输出中验证的内容——绝不要推断或假设。

## 输出契约（必需）

在结束之前，以下所有条件都必须满足：

1. `docs/codebase/` 中必须准确存在这些文件：`STACK.md`、`STRUCTURE.md`、`ARCHITECTURE.md`、`CONVENTIONS.md`、`INTEGRATIONS.md`、`TESTING.md`、`CONCERNS.md`。
2. 每一项主张都必须能够追溯到源文件、配置或终端输出。
3. 未知项标记为 `[TODO]`；依赖意图的决策标记为 `[ASK USER]`。
4. 每份文档都包含一个简短的 "evidence" 列表，并列出具体文件路径。
5. 最终回复包含编号的 `[ASK USER]` 问题，以及“意图 vs 现实”的偏差。

## 工作流

复制并跟踪此清单：

```
- [ ] Phase 1: Run scan, read intent documents
- [ ] Phase 2: Investigate each documentation area
- [ ] Phase 3: Populate all seven docs in docs/codebase/
- [ ] Phase 4: Validate docs, present findings, resolve all [ASK USER] items
```

## 关注区域模式

如果用户提供了一个关注区域，例如："architecture only" 或 "testing and concerns"：

1. 始终完整运行 Phase 1。
2. 优先完整完成关注区域对应的文档。
3. 对于尚未分析的非关注文档，仍然保留必需章节，并将未知项标记为 `[TODO]`。
4. 在最终输出之前，仍然要对全部七份文档运行 Phase 4 验证循环。

### Phase 1：扫描并阅读意图文档

1. 从目标项目根目录运行扫描脚本：

   ```bash
   python3 "$SKILL_ROOT/scripts/scan.py" --output docs/codebase/.codebase-scan.txt
   ```

   其中 `$SKILL_ROOT` 是 skill 文件夹的绝对路径。适用于 Windows、macOS 和 Linux。

   **快速开始：** 如果你已经有内联路径：

   ```bash
   python3 /absolute/path/to/skills/acquire-codebase-knowledge/scripts/scan.py --output docs/codebase/.codebase-scan.txt
   ```

2. 搜索 `PRD`、`TRD`、`README`、`ROADMAP`、`SPEC`、`DESIGN` 文件并阅读它们。

3. 在阅读任何源代码之前，总结项目所声明的意图。

### Phase 2：调查

使用扫描输出，回答七个模板中每个模板的问题。加载 [`references/inquiry-checkpoints.md`](references/inquiry-checkpoints.md)，获取每个模板的完整问题清单。

如果技术栈不明确，例如存在多个 manifest 文件、不熟悉的文件类型、没有 `package.json`，则加载 [`references/stack-detection.md`](references/stack-detection.md)。

### Phase 3：填充模板

将 `assets/templates/` 中的每个模板复制到 `docs/codebase/`。按照以下顺序填写：

1. [STACK.md](assets/templates/STACK.md) —— 语言、运行时、框架、所有依赖
2. [STRUCTURE.md](assets/templates/STRUCTURE.md) —— 目录布局、入口点、关键文件
3. [ARCHITECTURE.md](assets/templates/ARCHITECTURE.md) —— 分层、模式、数据流
4. [CONVENTIONS.md](assets/templates/CONVENTIONS.md) —— 命名、格式化、错误处理、导入
5. [INTEGRATIONS.md](assets/templates/INTEGRATIONS.md) —— 外部 API、数据库、认证、监控
6. [TESTING.md](assets/templates/TESTING.md) —— 框架、文件组织、mock 策略
7. [CONCERNS.md](assets/templates/CONCERNS.md) —— 技术债、Bug、安全风险、性能瓶颈

对于无法从代码中确定的任何内容，使用 `[TODO]`。如果正确答案依赖团队意图，使用 `[ASK USER]`。

### Phase 4：验证、修复、确认

在最终完成前，运行此强制验证循环：

1. 根据 `references/inquiry-checkpoints.md` 验证每份文档。
2. 对于每个非平凡主张，确认至少存在一个证据引用。
3. 如果任何必需章节缺失或缺乏支撑：

* 修复该文档。
* 重新运行验证。

4. 重复上述流程，直到全部七份文档通过验证。

然后展示七份文档的摘要，将每个 `[ASK USER]` 项作为编号问题列出，并高亮 Phase 1 中发现的“意图 vs 现实”偏差。

验证通过标准：

* 没有缺乏支撑的主张。
* 没有空的必需章节。
* 未知项使用 `[TODO]`，而不是假设。
* 团队意图缺口被明确标记为 `[ASK USER]`。

---

## 注意事项

**Monorepos：** 根目录 `package.json` 可能没有源代码——检查 `workspaces`、`packages/` 或 `apps/` 目录。每个 workspace 可能有独立的依赖和约定。分别映射每个子包。

**过时的 README：** README 经常描述的是预期架构，而不是当前架构。在把任何 README 主张视为事实之前，要与实际文件结构交叉验证。

**TypeScript 路径别名：** `tsconfig.json` 的 `paths` 配置意味着像 `@/foo` 这样的导入不会直接映射到文件系统。记录结构前，要先将别名映射到真实路径。

**生成/编译产物：** 不要记录来自 `dist/`、`build/`、`generated/`、`.next/`、`out/` 或 `__pycache__/` 的模式。这些是产物——只记录源码约定。

**`.env.example` 会暴露必需配置：** 密钥永远不会被提交。阅读 `.env.example`、`.env.template` 或 `.env.sample`，以发现必需的环境变量。

**`devDependencies` ≠ 生产技术栈：** 只有 `dependencies`，或等价配置，例如 `[tool.poetry.dependencies]`，会在生产中运行。将 linter、formatter 和测试框架作为开发工具单独记录。

**测试 TODO ≠ 生产债务：** `test/`、`tests/`、`__tests__/` 或 `spec/` 中的 TODO 是覆盖率缺口，不是生产技术债。要在 `CONCERNS.md` 中将它们分开。

**高变更文件 = 脆弱区域：** 最近 git 历史中出现最多的文件，修改频率最高，可能隐藏复杂性。始终在 `CONCERNS.md` 中注明这些文件。

---

## 反模式

| ❌ 不要这样做                                                       | ✅ 应该这样做                                               |
| ------------------------------------------------------------- | ----------------------------------------------------- |
| "Uses Clean Architecture with Domain/Data layers."（当不存在这些目录时） | 只说明目录结构实际展示出的内容。                                      |
| "This is a Next.js project."（没有检查 `package.json`）             | 先检查 `dependencies`。说明实际存在的内容。                         |
| 根据类似 `dbUrl` 的变量名猜测数据库                                        | 检查 manifest 中是否有 `pg`、`mysql2`、`mongoose`、`prisma` 等。 |
| 将 `dist/` 或 `build/` 中的命名模式记录为约定                              | 只记录源文件。                                               |

---

## 增强扫描输出章节

`scan.py` 脚本现在除了原始输出之外，还会生成以下章节：

* **CODE METRICS** —— 总文件数、按语言统计的代码行数、最大文件（复杂度信号）
* **CI/CD PIPELINES** —— 检测到的 GitHub Actions、GitLab CI、Jenkins、CircleCI 等
* **CONTAINERS & ORCHESTRATION** —— Docker、Docker Compose、Kubernetes、Vagrant 配置
* **SECURITY & COMPLIANCE** —— Snyk、Dependabot、SECURITY.md、SBOM、安全策略
* **PERFORMANCE & TESTING** —— benchmark 配置、profiling 标记、负载测试工具

在 Phase 2 中使用这些章节来指导调查问题，并识别工具特定模式。

---

## 随附资源

| Asset                                | 何时加载                                        |
| ------------------------------------ | ------------------------------------------- |
| [`scripts/scan.py`](scripts/scan.py) | Phase 1 —— 最先运行，在阅读任何代码之前运行（需要 Python 3.8+） |

| [`references/inquiry-checkpoints.md`](references/inquiry-checkpoints.md) | Phase 2 —— 加载以获取每个模板的调查问题 |
| [`references/stack-detection.md`](references/stack-detection.md) | Phase 2 —— 仅当技术栈不明确时加载 |
| [`assets/templates/STACK.md`](assets/templates/STACK.md) | Phase 3 第 1 步 |
| [`assets/templates/STRUCTURE.md`](assets/templates/STRUCTURE.md) | Phase 3 第 2 步 |
| [`assets/templates/ARCHITECTURE.md`](assets/templates/ARCHITECTURE.md) | Phase 3 第 3 步 |
| [`assets/templates/CONVENTIONS.md`](assets/templates/CONVENTIONS.md) | Phase 3 第 4 步 |
| [`assets/templates/INTEGRATIONS.md`](assets/templates/INTEGRATIONS.md) | Phase 3 第 5 步 |
| [`assets/templates/TESTING.md`](assets/templates/TESTING.md) | Phase 3 第 6 步 |
| [`assets/templates/CONCERNS.md`](assets/templates/CONCERNS.md) | Phase 3 第 7 步 |

模板使用模式：

* 默认模式：只完成每个模板中的 "Core Sections (Required)"。
* 扩展模式：只有当仓库复杂度值得这样做时，才添加可选章节。

<!-- LOCAL_CHINESE_OUTPUT_OVERRIDE -->

## 本地输出语言覆盖规则

在此仓库中使用该 skill 时，所有生成的文档都必须使用简体中文。

要求：

* `docs/codebase/` 下的所有文件都必须使用简体中文。
* 必要时保留技术术语英文，并在有帮助时添加简短中文解释。
* 文件名可以保持英文，例如：

  * STACK.md
  * STRUCTURE.md
  * ARCHITECTURE.md
  * CONVENTIONS.md
  * INTEGRATIONS.md
  * TESTING.md
  * CONCERNS.md
* 不要翻译代码、命令、包名、API 名称、类名、函数名、文件路径、环境变量名或配置键名。
* 优先使用紧凑的中文技术写作。
* 避免冗长的通用解释。
* 聚焦于帮助用户快速理解项目结构、架构、运行流程、测试/验证命令和风险。
* 如果原始 skill 要求英文文档，本地覆盖规则优先。
