# Phase 2 PaperQA 依赖兼容矩阵

## 目的与边界

本文件固定阶段 2 已验证的 Windows/Python 3.11 PaperQA 组合，并记录失败尝试，防止后续依赖解析悄然漂移。兼容检查入口为：

```powershell
python scripts/check_phase2_dependencies.py --json
```

该脚本只读取本机 distribution metadata、导入本地模块、构造显式离线 `Settings` 并静态检查 Adapter；不会安装包、解析远程索引、联网、创建模型客户端或调用 PaperQA Agent。

## 已验证矩阵

| 组件 | 固定版本 / 提交 | Windows Python 3.11 结果 | 用途 |
|---|---|---|---|
| `paper-qa` | `2026.3.18` | import 通过 | `Docs`、`Doc`、`Text`、`Context` 与 raw retrieval API |
| `paper-qa-pypdf` | `2026.3.18` | import 通过 | PaperQA 默认 PDF reader 依赖；阶段 2 Adapter 仍优先接收本项目 canonical chunks |
| `tantivy` | `0.26.0` | import 及 `Index` export 通过 | PaperQA 派生全文索引 |
| `fhaviary` | `0.34.0` | PaperQA import 链通过 | PaperQA 类型依赖 |
| `fhlmi` | `0.45.0` | PaperQA import 链通过 | PaperQA 模型抽象依赖；阶段 2 smoke 不构造远程模型 |
| `litellm` | `1.82.4` | PaperQA import 链通过 | 与上游官方 lock 对齐；阶段 2 不调用模型 |
| PaperQA 参考源码 | `d7675d7b7eddeb3535e8c260399c5bbeeb818c50` | 定点 API 审阅完成 | Adapter contract 的源码证据，不作为 PyPI 版本推断 |

PaperQA 参考仓库为浅克隆且使用 `setuptools_scm` 动态版本，HEAD 没有 tag；因此不能声称上述 Git SHA 等于 `2026.3.18`。发布包版本与参考源码提交是两条独立、同时记录的证据：发布包用于可复现安装，固定 SHA 用于解释 Adapter 的设计来源。

## 解析过程与失败证据

依赖解析先在临时隔离 venv 中完成，确认矩阵后才在用户授权的项目 conda 环境安装同一组精确版本：

1. 首次未固定 `litellm` 的解析进入了需要 Rust toolchain 的构建路径，Windows 安装失败。该组合未被接受。
2. 将 `litellm` 固定为 `1.81.14` 后，安装可以继续，但 PaperQA import contract 失败，说明“能解析依赖”不等于 API 兼容。该组合未被接受。
3. 对齐 PaperQA 官方 lock 中的 `fhaviary==0.34.0`、`fhlmi==0.45.0`、`litellm==1.82.4`，并固定 `paper-qa==2026.3.18`、`paper-qa-pypdf==2026.3.18`、`tantivy==0.26.0` 后，Windows Python 3.11 的 distribution/version、关键 import、类型 export 和离线 Settings smoke 全部通过。

临时 venv 只用于兼容探索，完成后可删除并重建；它不是 canonical 项目环境，也不允许把绝对路径写入配置。探索期间主 conda 得到结构化 `missing_dependencies` 和退出码 `2`。随后执行 `python -m pip install -e ".[knowledge]"`（退出码 0），主 conda 的兼容脚本与 `pip check` 均取得退出码 `0`。测试仍使用依赖注入覆盖 missing 路径，不假设某个开发环境永远安装 PaperQA；任何情况下都不能把缺依赖标成 skip 或 PASS。

## Pyproject 固定规则

`[project.optional-dependencies].knowledge` 必须对以下六个 distribution 使用精确 `==` pin：

```toml
knowledge = [
    "paper-qa==2026.3.18",
    "paper-qa-pypdf==2026.3.18",
    "tantivy==0.26.0",
    "fhaviary==0.34.0",
    "fhlmi==0.45.0",
    "litellm==1.82.4",
]
```

该 extra 必须保持可选；关闭 `enable_paperqa_retrieval` 时不得导入 PaperQA。项目现有 `uv.lock` 不是本轮 conda/pip 兼容矩阵的权威证据，后续若维护该 lock，仍不得替代 Windows 安装/import smoke。

## 离线 Settings contract

兼容脚本只构造下列显式安全语义：

```text
parsing.use_doc_details = false
parsing.multimodal = false
parsing.defer_embedding = true
answer.evidence_skip_summary = true
agent.index.sync_with_paper_directory = false
agent.rebuild_index = false
```

并验证 `should_parse_and_enrich_media == (False, False)`。脚本还把 `agent.index.index_directory` 显式指向工作区内一个不创建的 smoke 占位路径，避免上游默认构造过程写入用户 `~/.pqa`；只构造 Settings 不会创建该索引目录。脚本不调用 `get_llm()`、`get_summary_llm()`、`get_embedding_model()`、metadata client 或 reader enrichment。生产 Adapter 仍必须注入受控 embedding；不能因 `Settings` 可构造就启用默认 OpenAI 路径。

## Adapter 禁止 API 门禁

兼容脚本使用 Python AST 检查 `src/open_deep_research/knowledge/paperqa_adapter.py`，拒绝：

- `paperqa.ask` / `ask`；
- `Docs.aquery` / `aquery`；
- `paperqa.agents` / `agent_query`；
- `PaperSearch`、`GatherEvidence`、`GenerateAnswer`、`Complete`。

允许的上游边界仅限 `Docs`、`Doc`、`Text`、`Context`、`aadd_texts`、`retrieve_texts`，以及未来另行注入并设成本上限的 contextual evidence seam。

## 退出码和验收解释

| 退出码 | 状态 | 含义 |
|---:|---|---|
| `0` | `compatible` | 平台、Python、精确 pin、已安装版本、关键 import、离线 Settings、参考 SHA 和 Adapter 静态门禁全部通过 |
| `1` | `incompatible` | 包已存在但版本/API/配置/静态安全门禁不兼容，或平台/配置证据错误 |
| `2` | `missing_dependencies` | 至少一个必需的 optional distribution 未安装；报告仍包含其他 pin/import 问题 |

JSON 报告显式包含 `network_used=false` 与 `installation_attempted=false`。在隔离 venv 或明确安装 extra 的目标环境中必须取得退出码 `0` 才能作为 T2-12 的真实兼容 evidence；未安装 extra 的环境返回退出码 `2` 只是正确降级证据，不能单独证明 PaperQA 可用。

## Windows 与许可证注意事项

- Tantivy 是本矩阵最关键的原生 wheel；Python 小版本或架构改变后必须重新验证，不能仅沿用版本字符串。
- Windows 文件占用可能影响索引目录清理；测试使用唯一临时目录并在清理前释放 PaperQA/Tantivy 对象。
- `paper-qa` 与 `paper-qa-pypdf` 使用 Apache-2.0。未引入 `paper-qa-pymupdf` extra；该 wrapper 为 AGPL-3.0。
- 项目已有 PyMuPDF 依赖采用 AGPL-3.0/Artifex 商业双许可证，属于既有独立许可证风险，不应误写成 PaperQA core 的 Apache-2.0 覆盖范围。
