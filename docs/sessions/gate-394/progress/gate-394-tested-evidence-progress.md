# gate-394 tested evidence 进度存档

## 里程碑 1：红用例骨架

- **当前阶段**：implementing
- **本段结论**：按卡面「先写 tests/test_gate_v2_tested_evidence.py 红用例，再改 workflow」落下从 YAML 抽出 Emit 脚本真跑、喂真实 pytest junit 的测试；基线上无 `Emit tested evidence` 步骤，这些用例必红。
- **关键决策与已否决方案**：Emit 判定逻辑用内联 python heredoc，不放 `scripts/`——quality 在 caller checks 前会删掉 `_gate-action-src`，独立脚本在 caller workspace 不可引用。已否决：mtime 判新鲜度、PYTEST_ADDOPTS 注入 make、gate 侧保留前缀硬失败。
- **下一步唯一动作**：改 `.github/workflows/gate-v2.yml` quality job（清目录、legacy pytest `--junitxml`、新增 Emit 步骤）并跟随既有测试。

## 里程碑 2：quality job 落地 Emit 步骤

- **当前阶段**：implementing
- **本段结论**：`run-quality` / `run-tests` 开始前清空本次专用 junit 目录（entry 同时删旧 result JSON）；legacy 仅 pytest 直跑分支加 `--junitxml`；新增 `Emit tested evidence`（`always()` + `continue-on-error`，内联 python）。证据行 marker 拆成 `GATE-TESTED-` + `EVIDENCE-V1`，避免步骤源码回显出现完整证据行。Narrow 19 条与 caller_checks / junit_upload / contract / source / net_diag 全绿。
- **关键决策与已否决方案**：不把判定逻辑抽到 `scripts/`（`Remove gate action source before caller checks` 之后 caller workspace 没有 gate 源；diff-coverage 的再 checkout 是 continue-on-error，不能当 Emit 的依赖）。不新增 job output / 配置项。已否决：mtime、PYTEST_ADDOPTS、保留前缀硬失败。
- **下一步唯一动作**：写 docs/gate-tested-evidence.md 契约、scope check、开 PR（Refs #394，不合并）。

## 里程碑 3：契约文档与准备开 PR

- **当前阶段**：implementing
- **本段结论**：`docs/gate-tested-evidence.md` 写清语义、mode、不打清单、consumer 只认 Emit 步骤内唯一整行。实现与测试已齐，下一步提交并开 PR。
- **关键决策与已否决方案**：无
- **下一步唯一动作**：commit + push + 开 PR（正文 Refs zlxlabs/gate#394，不自行合并）。
