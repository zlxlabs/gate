# gate-20260926-02 进度存档

任务卡：deferred 处置回执对 P1 全档拒绝（签发侧与消费侧同步，gate-hub#1072 裁决 4(a)）。
基线 origin/main `9173646e`，分支 `card/gate-20260926-02`。

## 段 1：开工（2026-09-26）

- 当前阶段：implementing，刚完成现场核对，未改任何代码。
- 本段结论：基线核对通过（HEAD == origin/main `9173646e`，工作区干净）。生产代码里
  `deferred_not_allowed_for_tier` 目前恰好两处构造（convergence.py:719 消费侧、
  issue_receipt.py:263 签发侧），改后收敛为 convergence 一处。`deferred` 在测试里只出现在
  卡面 Scope-Globs 列的四个测试文件，改动边界闭合。
- 关键决策与已否决方案：共享判定采用 convergence 模块级常量
  `DEFERRED_RECEIPT_REJECT_REASON`（签发侧经 `_CONVERGENCE` 复用、消费侧直接引用），
  不新增带 tier 参数的判定函数——按卡面假设节，deferred 一律拒绝后 tier 判定消失，
  保留 tier 参数即违反反熵条款。
- 下一步唯一动作：写 commit ① 的负例测试（收敛参数表 internal 行翻转 + 新增 personal 行、
  签发侧三档负例、聚合器三档消费负例），跑红并提交。
