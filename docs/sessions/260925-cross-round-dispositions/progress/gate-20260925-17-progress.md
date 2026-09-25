## 真实生产夹具

- 当前阶段：implementing
- 本段结论：从 gate-hub run 35987744407 的 ledger 日志行保存了完整真实 JSON，并用生产端 `issue_receipt.py issue` 为目标 finding 生成 v3 false-positive 回执。完整 ledger 有多条 P1 共享同一 canonical finding key，签发输入因此只含从该 ledger 原样抽出的目标 finding。
- 关键决策与已否决方案：保留原始 ledger 行；回执来源使用生产 CLI，反证记录 `--rm` 一次性容器与固定 SHA 下 Git 对象不可变的理由。不读取 GitHub artifact。
- 下一步唯一动作：实现前轮 JSON 的有效回执投影并先补行为单测。
