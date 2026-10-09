# counterevidence rerun 进度

## 里程碑 1：白名单校验 + convergence

- 当前阶段：implementing
- 本段结论：convergence 只认带 `gate_rerun` 且 argv 为 `git grep -n -F -e <literal> <head_sha> -- <pathspec>` 的反证；旧回执无 `gate_rerun` 一律 `counterevidence_not_rerun`。审计行优先展示 `gate_rerun.excerpt`。
- 关键决策与已否决方案：白名单函数放在 convergence.py，issue_receipt 复用；不升 SCHEMA_VERSION。零命中不是「不存在」证据。
- 下一步唯一动作：在 issue_receipt.py 按同一白名单于 head_sha 上重跑 git grep，并补真实 git 仓库子进程测试。

## 里程碑 2：issue_receipt 重跑 + 真实 git 测试

- 当前阶段：implementing
- 本段结论：false-positive 回执由门禁在调用方仓库 head_sha 上执行白名单 `git grep`；零命中/超时/非 0/1 退出都不写回执。提交方 `output` 保留但不参与判定。跨进程用真实 git 仓库断言 `gate_rerun` 字节。
- 关键决策与已否决方案：白名单先于取仓校验，不合规命令无需 repo_dir；不把零命中当成「不存在」证据。
- 下一步唯一动作：workflow 取调用方 head 对象，并把 `--repo-dir` 传入 issue 步骤，补契约测试。

## 里程碑 3：workflow 取仓 + 契约测试

- 当前阶段：implementing
- 本段结论：control 作业用 github.token 把调用方 head_sha fetch 到 `_gate-caller-head`，issue 步骤把该目录传给 producer。未新增第三方 action，permissions 仍是 contents: read。契约测试以 workflow 步骤原文跑 producer。
- 关键决策与已否决方案：不复用 gate_bounded_retry 包 fetch，取仓失败直接失败、不得降级采信。http.extraheader 写入 caller clone，供 blob:none 懒取 blob。
- 下一步唯一动作：写设计文档并做红验。

## 里程碑 4：设计文档 + 红验

- 当前阶段：implementing
- 本段结论：契约写入 `docs/design/counterevidence-rerun.md`。红验把 `_counterevidence_reason` 的缺失 `gate_rerun` 判据改为 `return None` 后，`test_old_receipt_without_gate_rerun_is_counterevidence_not_rerun` 以 AssertionError 转红（`active_false_positive` != `counterevidence_not_rerun`），随后只还原该行。
- 关键决策与已否决方案：无。
- 下一步唯一动作：提交本段、跑 Accept-Check、push 本卡分支。
