# gate#189 / gate#192 v2 滞后信号进度

## 进度 1

- 当前阶段：repairing / AGENTS.md 关单规则
- 本段结论：在「合并方式是发布动作的一部分」后写入「发布完成与关单」，机读判据为远端 `git ls-remote --tags origin v2` + `git merge-base --is-ancestor`；未满足改标 `status:waiting`。过时的 `@<40hex>` pin 描述改为「调用方钉 `@v2`；本仓 workflow 历史仍不可 rebase」。
- 关键决策与已否决方案：按设计 §6 原文落 gate 仓；不改 gate-hub 指针句。关单主语是本仓 maintainer，不新造 runbook。
- 下一步唯一动作：给 `v2-tag-sync` 加四元组观测与超阈/连续未抬上报。

## 进度 2

- 当前阶段：repairing / 四元组 + 超阈/连续未抬
- 本段结论：`v2-tag-sync` 每轮用 `git ls-remote` 取远端 v2/main，job summary 写四元组；超阈非零退出。连续未抬用 `actions/cache` restore/save 持久化 `v2-stuck-verified.json`，成功抬到当时 canary-verified target 后计数清零。阈值走 workflow env。
- 关键决策与已否决方案：观测脚本放 `tests/v2_lag_observe.py`（本仓已有 workflow 跑 `tests/` 的先例，且 scripts/ 不在本卡允许路径）。不把状态提交进 git 历史。cache key 带 run_id + restore-keys 前缀，因为 GitHub cache 不可覆写。未知 schema 直接失败，不加兼容分支。
- 下一步唯一动作：跑全量验证、红验，再推分支开 PR。

## 进度 3

- 当前阶段：repairing / 红验与收尾
- 本段结论：全量 pytest 1044 passed；`check_pinned_uses.py` 通过。红验三条均红后已还原：去掉 summary 的 `age_h` 行、去掉 `age_h` 上报、卡住计数不再自增。
- 关键决策与已否决方案：红验只改 `tests/v2_lag_observe.py` 后还原，不留兼容分支。
- 下一步唯一动作：push card 分支并开 PR；合并须 merge commit。

## 进度 4

- 当前阶段：repairing / age_h 误报
- 本段结论：`age_h` 与 `behind_main` 仅在存在更新 canary-verified 候选（`target_sha` 非空且不等于当前 v2）时上报；无候选时四元组仍写 summary，reasons 为空。`newer_target_without_move` / `stuck_verified_cycles` 不变。
- 关键决策与已否决方案：不把「v2 单纯变老」当故障——夜间/周末 main 无合并是常态。不改设计文档、不改 workflow summary 格式。
- 下一步唯一动作：红验两条新测试后推同一分支。

## 进度 5

- 当前阶段：repairing / age_h 红验收口
- 本段结论：无候选超阈仍 reasons 为空；有候选超阈含 age_h。两条红验均红后已还原。
- 关键决策与已否决方案：红验只改 `has_newer_candidate` 守卫后还原。
- 下一步唯一动作：全量验证后推 `card/gate-20260918-01`。


