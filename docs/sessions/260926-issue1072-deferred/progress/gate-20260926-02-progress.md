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

## 段 2：实现落地（2026-09-26）

- 当前阶段：implementing，commit ①（`4b01e6c`，负例 7 红）之后实现已完成，四文件 655 全绿。
- 本段结论：消费侧 deferred 分支在 tracking_issue 校验之后无条件返回
  `DEFERRED_RECEIPT_REJECT_REASON`；签发侧同分支无条件
  `raise ValueError(_CONVERGENCE.DEFERRED_RECEIPT_REJECT_REASON)`。生产代码理由码构造点
  收敛为 convergence.py:35 一处（git grep 实证）。禁改符号（DISPOSITION_KINDS /
  SCHEMA_VERSION）在 diff 中无改动行。
- 关键决策与已否决方案：共享判定最终形态是模块级常量而非函数——deferred 分支结构本身
  即「一律拒绝」的判定，函数包装是单值间接层。改载体时多发现一处卡面未列的
  「deferred 放行」用例：`test_deferred_producer_accepts_same_repository_issue_reference`
  （issue_receipt 测试 ：127-136），改写为「合法引用得到 tier 拒绝理由」负例并加
  `tracking_issue_ 不出现` 断言保住格式校验顺序覆盖；另补 false-positive×measured 负例，
  承接原用例里 deferred+measured 隐含的 trigger_kind 规则。
- 下一步唯一动作：commit ③（README deferred 条目与 clean-streak-convergence.md 措辞同步）。
