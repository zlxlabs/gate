# counterevidence rerun 进度

## 里程碑 1：白名单校验 + convergence

- 当前阶段：implementing
- 本段结论：convergence 只认带 `gate_rerun` 且 argv 为 `git grep -n -F -e <literal> <head_sha> -- <pathspec>` 的反证；旧回执无 `gate_rerun` 一律 `counterevidence_not_rerun`。审计行优先展示 `gate_rerun.excerpt`。
- 关键决策与已否决方案：白名单函数放在 convergence.py，issue_receipt 复用；不升 SCHEMA_VERSION。零命中不是「不存在」证据。
- 下一步唯一动作：在 issue_receipt.py 按同一白名单于 head_sha 上重跑 git grep，并补真实 git 仓库子进程测试。
