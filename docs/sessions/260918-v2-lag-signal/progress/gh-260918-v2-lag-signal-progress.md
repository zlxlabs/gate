# gate#189 / gate#192 v2 滞后信号进度

## 进度 1

- 当前阶段：repairing / AGENTS.md 关单规则
- 本段结论：在「合并方式是发布动作的一部分」后写入「发布完成与关单」，机读判据为远端 `git ls-remote --tags origin v2` + `git merge-base --is-ancestor`；未满足改标 `status:waiting`。过时的 `@<40hex>` pin 描述改为「调用方钉 `@v2`；本仓 workflow 历史仍不可 rebase」。
- 关键决策与已否决方案：按设计 §6 原文落 gate 仓；不改 gate-hub 指针句。关单主语是本仓 maintainer，不新造 runbook。
- 下一步唯一动作：给 `v2-tag-sync` 加四元组观测与超阈/连续未抬上报。
