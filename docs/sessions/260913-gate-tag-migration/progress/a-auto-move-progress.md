# gate v2 自动前移：执行进度

- 任务：#163；分支：`card/gate-20260913-22`
- 已完成：新增 `v2-tag-sync.yml`，仅响应 `main` 分支 push 或人工 dispatch；仓库变量 `V2_TAG_SYNC_ENABLED` 未设为 `true` 时保持停用。
- 熔断：提交范围内任一 commit message 含 `[v2-tag-sync:hold]`，或仓内 `.github/v2-tag-sync.hold` 存在，即不前移；熔断文件查询异常也按 hold 处理。启用且无熔断信号时才继续。
- 前移前置：全量取回 `v2`，运行 caller 模板实际 `uses` 的三份 reusable workflow（`gate-v2.yml`、`gate-shadow-v2.yml`、`gate-v2-disposition.yml`）兼容性检查；检查通过后才强推 `HEAD:refs/tags/v2`。守护路径从 `templates/caller-*.yml` 的 job-level reusable-workflow 引用自动推导，不再靠数量或手工清单。
- 熔断实现：`main()` 与 `should_advance()` 统一调用 `evaluate_advance()` 判定；行为测试走 `main()` 生产入口，消除判定与文案分叉。
- 熔断文案（F1）：区分 hold 文件存在（`held by breaker file:`）、确认不存在（放行 `enabled and no breaker signal found`）、查询异常（`held: breaker file query failed:` + 异常类名与详情），依然严格 fail-closed。
- 契约检查：对比 `workflow_call.inputs` 的名字/required/type/default、`secrets` 的名字/required、`workflow_call.outputs` 名称，以及 workflow 与 job 级 `permissions`；拦截删除、required 收紧、输入类型/默认值变化、输出删除/改名、权限扩大/收窄和有效权限声明删除。浅克隆无标签时测试 skip；同步工作流用 `fetch-depth: 0` 和显式 `git fetch` 确保消费环境拿到标签。
- 写后回读与附注标签（F2）：强推后用 `git ls-remote origin refs/tags/v2` 读取远端事实，与 `HEAD` 意图 SHA 比较；push 非零但远端匹配视为成功，远端不匹配或查询空/失败均响亮失败；附注标签测试采用真实临时本地 git 仓库 + `git tag -a` producer 产出两行 `ls-remote` 输出，锁定因直接 ref 指向 tag 对象导致比较不符而 fail-closed，且错误信息展示 tag 对象 SHA。
- 验证：变更目标测试 13 passed；全量测试 966 passed（Python 3.14 与 3.12 均通过）；`python3 scripts/check_pinned_uses.py` 通过。
- 变异注入实测：
  - F2 变异：将 `_remote_tag_sha()` 改为匹配 `refs/tags/v2^{}`，附注标签测试立即变红（`AssertionError: assert not True`，1 failed）。
  - P1-1 回读变异：在 `verify_remote_tag` 注入依赖 `push_exit_code != 0`，`test_push_failure_is_success_when_remote_matches_intent` 变红（1 failed）。
  - P1-2 契约权限变异：抑制 `permission restricted` 告警，`test_contract_guard_rejects_permission_restriction` 变红（AssertionError）。
  - P1-3 熔断入口变异：绕过 hold 文件 `lstat()` 判定，`test_squash_safe_breaker_file_blocks_advancement` 变红（`assert 0 == 1`）。
- 远端标签保持：`e74743bb621c5a373956545c730dbe81b5304eaa refs/tags/v2`；本卡未执行任何标签、变量或 workflow 写入。
