# gate-20260926-01 进度存档（disposition 同键撞车按 finding_id 消歧，gate#240）

## 2026-09-26 — 测试先行（红测入库）

- 当前阶段：implementing
- 本段结论：复刻 gate#240 形态（同文件、`line: null`、同类同级的两条 P1）落了签发子进程测试：
  行为验收 1（两个 id 各签一张 deferred 回执，rc=0、finding_id 各自正确、产物名互不相同）在
  origin/main 上红（撞键 ValueError，两次签发均 rc≠0）；行为验收 2（不存在的 id 仍拒绝）绿。
- 关键决策与已否决方案：消歧判定收敛为 convergence.py 单一函数（窄化 `(id, record)` 匹配列表），
  签发/消费两侧共用；签发侧以 audit dict 列表自建匹配列表后调该函数（卡面假设允许的适配，
  细节记 report.md）。
- 下一步唯一动作：在 convergence.py 加共享消歧函数，签发侧接入让验收 1 转绿，随后消费侧与产物名。
