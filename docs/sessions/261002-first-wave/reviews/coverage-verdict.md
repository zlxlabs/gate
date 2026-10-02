# Gate 269 覆盖率修复独立审查

failure-visibility: clean

## 结论

- 固定范围 `85916ed5c4f9d181fec866f15d1bb5f58111c9c8..3b4cdca3ffaf5d02d17af871295b2895208a88f1`：未发现 P1/P2 finding；两行实现改动符合 personal 档不变式。
- `.github/actions/diff-coverage-advisory/advisory.py:87-103` 保留 `--compare-branch <base_sha>`，并显式传 `--diff-range-notation ..`；没有改 base/head 选取、权限或异常降级。
- `tests/test_diff_coverage_advisory.py:140-159` 锁住 producer 参数构造。把该测试移植到固定 base 后，单项红验失败于缺少 range 参数；固定 head 全文件 13 项通过。
- 在固定 base/head 上建立的本地 depth-1 Git 消费环境中，base 初始缺失；执行既有 endpoint fetch 后，checkout 仍为固定 head，实际 diff-cover argv 保留固定 base 并带 `--diff-range-notation ..`，真实运行结果为 `covered`。
- 没有把 advisory 步骤成功或检查状态当作覆盖率证据。PR 的自身 CI 没有 diff-coverage check-run；覆盖行为由上述真实 producer 探针验证。

## 合并与发布状态

- PR #269 当前 open、非 draft、目标分支正确；冻结 head 未变，`mergeable_state=clean`。`pr_merge_ready.py` 重试结果为 `READY`，退出码 0。
- 该 head 唯一 workflow run 为 `ci`，run `36523549634`、attempt 1、completed；required checks `test` 与 `actionlint` 均为 SUCCESS。
- 远端 main 为 `39c889050e6a9b25f868738cdae074475ab1559c`，比 PR base 晚 10 个提交；所审生产文件和测试文件在 main 的这段增量中没有改动。固定 PR head 尚不在 main。
- 远端 `v2` 仍指向 `39c889050e6a9b25f868738cdae074475ab1559c`，固定 PR head 不是其祖先；修复尚未发布。本 verdict 不授权或执行合并、canary、tag 移动。

## 限制

- diff-cover 的右端点仍由消费工作树 `HEAD` 提供，这是本次未改动的既有选取方式；本次真实浅克隆探针令 `HEAD` 与固定 head 一致。PR #269 自身的 CI 只执行 `test` 和 `actionlint`，没有真实 caller 的 advisory run，因此不据其 SUCCESS 推断覆盖率。
