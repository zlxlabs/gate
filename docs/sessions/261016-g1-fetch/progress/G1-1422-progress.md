# G1-1422 进度存档

## 2026-10-16 段 1（dispatch dlg-20261016-034649-780179）

- **当前阶段**：测量完成，修复已实现并通过行为测试与红验，产物待推送。
- **本段结论**：同构场景（agent-config 规模 bare 镜像 + shallow 单 commit 工作区）实测本地 fetch 慢在 upload-pack 对整条 main 闭包的现场 delta 压缩（12.7s 中协商仅 2.6ms）；用真实 `ensure_commits` A/B：旧 fetch 14.45s/610MB → alternates 借对象 0.12s/14MB（复跑 4ms）。修复落在 `scripts/gate_source.py` 的 `ensure_commits`/新私有 `_borrow_mirror_objects`，签名与错误码不变，checkout 路径未动。
- **关键决策与已否决方案**：采纳 alternates 借对象（与 gate-v2.yml prime 步同机理）；否决合并单次 fetch（实测 12.69s 无改善）、本地化 repack（等于把被移除的整闭包 delta 搬到客户端）、对共享工作区浅取（卡面禁止）。alternates 的长期依赖由镜像 demand ref 7 天留存 + pin keep-alive + consume 锁纪律兜底（ci-03 实查：镜像 101MB/1 pack/932 demand refs）。
- **下一步唯一动作**：推送 `card/gh261016-g1`、开 draft PR、写 report.md。
