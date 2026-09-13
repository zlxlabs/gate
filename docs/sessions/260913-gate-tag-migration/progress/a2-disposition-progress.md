# A2 disposition 迁移进度

- 状态：实现完成，验证通过。
- `gate-v2-disposition.yml` 保留 `gate_ref` 作为可选的兼容输入但完全忽略，checkout 改用 `job.workflow_repository` 与 `job.workflow_sha`；旧的 40-hex 校验 step 已删除。
- `templates/caller-gate-disposition.yml` 不再传 `gate_ref`。契约测试覆盖两触发器的可选声明、传入与不传入旧参数的兼容性，以及 checkout 必须使用 reusable workflow 自身身份。
- 修复（PR #164 P2-1）：消除 `test_disposition_workflow_call_accepts_legacy_gate_ref_both_ways` 构造恒真缺陷。caller 侧输入从 `templates/caller-gate-disposition.yml` 独立读取，callee 侧声明从 `gate-v2-disposition.yml` 读取，以真实文件集合比较跨越 caller/callee 边界，并由双向变异实测锁死。
- 自检：生产路径、脚本和模板中 `inputs.gate_ref` 读取数为 0；`python3 scripts/check_pinned_uses.py` 通过；Python 3.14 与 3.12 下全量测试均为 945 passed。
- 后续：舰队迁移完成后，以运行时事实确认所有 disposition 调用在删除该输入后仍能启动且没有“caller 传入未声明 input”的 workflow-call 错误，再彻底删除 `gate_ref` 声明。
