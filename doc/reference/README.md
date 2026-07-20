# 参考仓库固定与获取

本目录包含开发期只读参考仓库。五个目录由外层仓库以 git submodule 固定到精确 commit；`refs.lock.json` 是机器可读的版本、许可证和来源审计镜像。commit 是唯一权威版本，branch、tag和包版本仅作说明。

## 获取

新 clone 推荐：

```powershell
git clone --recurse-submodules --shallow-submodules https://github.com/magic-ZSS/agentic-deep-research
```

已有 clone：

```powershell
git submodule sync --recursive
git submodule update --init --depth 1 --checkout
```

不要使用 `git submodule update --remote`，也不要把 submodule branch推进到未记录的最新提交。阶段验收只读取 gitlink、本地 HEAD和lock，不自动 fetch或联网。

当前 Windows 环境中 `git submodule status` 可能因 Git for Windows 的 `sh.exe` signal pipe 权限失败；`scripts/validate_phase.py` 因此使用 `git ls-files --stage`，并在本地目录已初始化时使用 `git -C <path> rev-parse HEAD`。

## 使用边界

- 参考仓库不参与本项目的 package discovery、pytest、Ruff或mypy。
- 不得对本目录运行全库格式化或lint。
- 不直接复制论文、网页fixture或第三方源码；若未来必须复制代码，先逐文件核验许可证，并记录来源commit、修改与attribution。
- PaperQA2、LangMem和MCP Servers在阶段0只锁定，不安装、不导入。
- DeepEval仅为可选`eval` extra；默认smoke不导入DeepEval、不注册其callback、不运行Judge或上传。

更新任何参考commit时，必须同步修改外层gitlink、`.gitmodules`（仅当URL变化）、`refs.lock.json`、`THIRD_PARTY_NOTICES.md`和`progress.md`，然后重新执行阶段验证。
