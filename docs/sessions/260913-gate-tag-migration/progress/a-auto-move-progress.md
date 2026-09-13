# gate v2 自动前移：执行进度

- 任务：#163；分支：`card/gate-20260913-15`
- 已完成：新增 `v2-tag-sync.yml`，仅响应 `main` 分支 push 或人工 dispatch；仓库变量 `V2_TAG_SYNC_ENABLED` 未设为 `true` 时保持停用。
- 熔断：提交范围内任一 commit message 含 `[v2-tag-sync:hold]` 即不前移；启用且无标记时才继续。
- 前移前置：全量取回 `v2`，运行 caller 模板实际 `uses` 的三份 reusable workflow（`gate-v2.yml`、`gate-shadow-v2.yml`、`gate-v2-disposition.yml`）兼容性检查；检查通过后才强推 `HEAD:refs/tags/v2`。守护路径从 `templates/caller-*.yml` 的 job-level reusable-workflow 引用自动推导，不再靠数量或手工清单。
- 熔断实现：`main()` 调用唯一的 `should_advance()` 判定；行为测试走 `main()` 生产入口，不再测试旁路副本。
- 契约检查：对比 `workflow_call.inputs` 的名字/required、`secrets` 的名字/required，以及 workflow 与 job 级 `permissions`；拦截删除、required 收紧、权限扩大和有效权限声明删除。浅克隆无标签时测试 skip；同步工作流用 `fetch-depth: 0` 和显式 `git fetch` 确保消费环境拿到标签。
- 验证：全量测试 954 passed（默认 Python 3.14 环境 45.25s，Python 3.12 52.14s）；本卡目标测试 9 passed；`actionlint .github/workflows/v2-tag-sync.yml` 与 `python3 scripts/check_pinned_uses.py` 均通过。
- 故障注入：临时删除 `gate-v2.yml` 的 `tier` input，契约测试 exit 1，报告 `input removed: tier`；既有文件已恢复。
- 本卡故障注入：临时反转熔断开关条件后，生产入口测试 2 failed、1 passed；临时删除 `gate-v2-disposition.yml` 的 `finding_id` input，守护输出 `input removed: finding_id`；既有文件已恢复。
- 远端标签保持：`e74743bb621c5a373956545c730dbe81b5304eaa refs/tags/v2`。
