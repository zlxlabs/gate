# gate-C：已消费 disposition receipt 进 ledger 投影（G3 gate 侧）

## 2026-08-31 里程碑 ① terminal 结构化消费块

- 当前阶段：implementing / milestone ① terminal structured block
- 本段结论：`build_terminal_envelope` 恒写顶层 `disposition_receipt_consumption`。数据来自判定者第一次 `consume_dispositions` 的结构化对象（finding_id / receipt 名 / 授权三字段 / reason + 计数 + rejected_reasons + fail_closed），不解析 G4 显示字符串。无消费时写空列表与零计数，缺省键恒在。
- 关键决策与已否决方案：用第一次消费结果而非 `round_decision.disposition`（后者只看到已转发的 consumed receipts，丢 rejected）。`reason` 写入 resolved 项，避免 hub-A 再去解析 G4 行。否决把该块塞进 `_review_summary`。
- 下一步唯一动作：ledger job 下载本 run 的 `gate-terminal-v1-*` artifact（fail-loud）。

## 2026-08-31 里程碑 ② ledger job 下载 terminal artifact

- 当前阶段：implementing / milestone ② workflow download
- 本段结论：ledger resolver 增加 `TERMINAL_PREFIX`（与 input/audit 同一 `repository_id-head_sha-run_id-` + 数字 attempt 选择器），缺制品 fail-loud。下载 step 无 `continue-on-error`，落到 `$RUNNER_TEMP/gate-terminal`。
- 关键决策与已否决方案：复用既有 `select_artifact` + `actions/download-artifact`（artifact-ids），不另开 `gh run download`。terminal 恒 required（gate job `if: always()` 且 `if-no-files-found: error`），不像 audit 按 review-expected 可选。
- 下一步唯一动作：ledger `build_entry` 投影顶层 `disposition_receipt_consumption`，契约测试用 `build_terminal_envelope` 真实产物喂入。

## 2026-08-31 里程碑 ③ ledger 投影 + 真实 producer 契约

- 当前阶段：implementing / milestone ③ ledger projection
- 本段结论：`build_entry` 顶层写入 `disposition_receipt_consumption`，缺省为恒在的空块。CLI/`action.yml` 增加 required `terminal-path`；契约测试用 `evaluate` + `build_terminal_envelope` 真实产物喂 `build_entry`，resolved 列表字节一致。`_review_summary` / `_compact_attempts` 源码与返回值都不含该字段。
- 关键决策与已否决方案：ledger 侧复制空块形状（跨 job 发布边界，禁止共享模块）。无 terminal 实参的单测走缺省空块，与「文件缺失/损坏」的 fail-loud 路径分开。评论通道 `finding_dispositions` 不动。
- 下一步唯一动作：注入损坏 terminal payload，断言报错方向且不可投影成无消费。

## 2026-08-31 里程碑 ④ 失败路径 fail-loud

- 当前阶段：implementing / milestone ④ fail-loud paths
- 本段结论：缺文件、空文件、非法 JSON、非对象、缺消费块、计数类型错、身份不匹配一律 `ValueError`，不会落到空消费缺省。缺省空块只在 `terminal_envelope is None`（单测无消费）或 producer 写出的零计数块。
- 关键决策与已否决方案：无 fallback/兼容分支。损坏 payload 不投影成无消费。
- 下一步唯一动作：红验约束 2（改坏 terminal 结构化块字段名 → consumer 契约测试红），再跑全量 pytest 与 pin 检查。

## 2026-08-31 里程碑 ⑤ 红验与全量收口

- 当前阶段：done / verification
- 本段结论：红验把 producer 块字段 `resolved` 改成 `resolved_items` 后，producer 字典断言以 AssertionError 转红，consumer 契约以 `ValueError: disposition_receipt_consumption is missing resolved` 转红（未投影成无消费）。已只还原该行。全量 pytest 701 passed 退出码 0，`check_pinned_uses.py` 退出码 0。
- 关键决策与已否决方案：无新增。
- 下一步唯一动作：主脑本地 review；合并须用 merge commit（执行器不推不合并）。

## 2026-08-31 r1 修复（P1-1 / P1-2 / P2-1）

- 当前阶段：done / r1 repair
- 本段结论：P1-1 选 Option A——ledger terminal resolver 只认当前 attempt，旧 artifact 残存且当前缺失时直接报 `No matching required gate terminal artifact found`，不再先选旧再 identity mismatch。P1-2 把 `terminal-path` 改为可选：未提供时条目缺 `disposition_receipt_consumption` 键（与 G3 前逐字一致）；提供时走 G3 投影（含 producer 空块）。P2-1 用真实 producer 块 mutate 锁 validator 六类负例。
- 关键决策与已否决方案：禁止继续 `<= current` 却丢掉 `terminal_source_attempt`。这是输入可选性（legacy `gate.yml` 是真实第二消费者），不是损坏路径 fallback。旧 terminal 缺消费块在路径已提供时仍 fail-loud。
- 下一步唯一动作：主脑本地 review；执行器不推不合并。
