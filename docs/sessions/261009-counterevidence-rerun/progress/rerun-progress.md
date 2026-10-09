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
