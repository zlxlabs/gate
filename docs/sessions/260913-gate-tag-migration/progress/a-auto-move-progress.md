# gate v2 自动前移：执行进度

- 任务：#163；分支：`card/gate-20260913-15`
- 已完成：新增 `v2-tag-sync.yml`，仅响应 `main` 分支 push 或人工 dispatch；仓库变量 `V2_TAG_SYNC_ENABLED` 未设为 `true` 时保持停用。
- 熔断：提交范围内任一 commit message 含 `[v2-tag-sync:hold]` 即不前移；启用且无标记时才继续。
- 前移前置：全量取回 `v2`，运行三份 reusable workflow 的兼容性检查；检查通过后才强推 `HEAD:refs/tags/v2`。
- 契约检查：对比 `workflow_call.inputs` 的名字/required、`secrets` 的名字/required，以及 workflow 与 job 级 `permissions`；拦截删除、required 收紧和权限扩大。浅克隆无标签时测试 skip；同步工作流用 `fetch-depth: 0` 和显式 `git fetch` 确保消费环境拿到标签。
- 验证：全量测试 953 passed（默认 Python 3.14 环境 25.82s，Python 3.12 31.08s）；新增测试 8 passed；`actionlint .github/workflows/v2-tag-sync.yml` 与 `python3 scripts/check_pinned_uses.py` 均通过。
- 故障注入：临时删除 `gate-v2.yml` 的 `tier` input，契约测试 exit 1，报告 `input removed: tier`；既有文件已恢复。
- 远端标签保持：`e74743bb621c5a373956545c730dbe81b5304eaa refs/tags/v2`。
