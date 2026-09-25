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

## 2026-09-26 — 实现（签发、消费、产物名三处同步）

- 当前阶段：implementing
- 本段结论：`narrow_stable_key_matches_by_id(matches, finding_id)` 落在 convergence.py，
  签发（`issue_receipt.py` 自建 `(id, finding)` 匹配列表后调用）与消费
  （`validate_disposition_receipt`、`record_dispositions` 的 target_id）共用；产物名 target
  分量改为 `[finding_key, finding_id]` 规范 JSON 的 sha256 前 12 位。行为验收 1-7 全部有测试，
  四个测试文件 501 通过，全量 1215 通过。
- 关键决策与已否决方案：`test_disposition_producer_rejects_stable_key_collision` 锁的旧拒绝行为
  与本卡目标直接冲突（id 在撞车集合内也拒），改写为「id 命中放行 + 集合外 id 仍拒绝」；
  `test_legacy_path_is_never_more_permissive_than_stable_key_path` 拆成 with/without 双期望并加
  「legacy 不比稳定键更宽」不变量断言——完成条件 7「:687 不改期望值」与锁定决策 1 矛盾，
  按锁定决策执行、report.md 显式提出。签发侧歧义 ValueError 在 `_matching_finding` 之后已不可达
  （能解析的 target 必在集合内），保留作边界防御。
- 下一步唯一动作：红验两处注入（消歧判据放宽、产物名回退旧公式），贴红输出后还原。

## 2026-09-26 — 红验与收尾

- 当前阶段：verification / 收尾
- 本段结论：红验 2（产物名 target 回退只哈希 finding_key）双红：验收 1「产物名互不相同」断言
  `AssertionError: ...847201ec62c1 != ...847201ec62c1`、验收 6 合并测试 `len(receipts) == 2` 失败。
  红验 1 字面注入（`==1`→`>=1`）不转红：验收 4 的 id 在撞车集合外、0 命中在两判据下都不满足，
  注入 diff 已证生效；换注入「0 命中回退取第一条」后验收 4 与 ：713 锚点测试双红（AssertionError）。
  注入已全部还原，`git diff` 只剩 README；`check_pinned_uses.py` rc=0，`git diff --check` 干净。
- 关键决策与已否决方案：红验 1 字面注入不红属卡面红验条款的分析性缺陷（0 < 1 ≤ 0），按
  「红验有效性」固定条款贴注入证据后换最小注入重验，未下「断言恒真」结论。
- 下一步唯一动作：push 分支、开 PR（只开不合并）、写 report.md。
