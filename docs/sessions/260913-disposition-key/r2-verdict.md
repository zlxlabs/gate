# 第二轮独立评审结论

评审范围：`92421ac2114d229721bdaee6e2a1e3583d6b03b8..b440d3f333341ea128baa1a1fc1526c9a7c74169`。
本轮只评估这四个提交引入的新风险，不重复第一轮已结清的稳定键碰撞构造、消费入口链和 digest 去 `id` 影响分析。

## 1. 消费与签发入口及守卫覆盖

| 入口 | 经过的守卫 | 失败归属 |
| --- | --- | --- |
| `.github/workflows/gate-v2-disposition.yml:169-176` 的签发命令 | `issue_receipt.py:122-151` 的 `_matching_finding()` 同时收集 id/key 候选；随后 `issue_receipt.py:196-211` 对匹配 finding 的稳定键、P1 和 `inferred` 做签发侧校验 | 任一歧义或非法目标抛出 `ValueError`，命令失败，不写 artifact；稳定键重复在 `:198-207` 拒绝 |
| `.github/actions/gate-aggregator/aggregate.py:839-846` 的首轮消费 | `convergence.py:846-884` 对每条回执调用 `validate_disposition_receipt()`；稳定键路径 `:724-740` 调 `_ambiguous_finding_key_status()`，无 key 的兼容路径在六元 finding 时于 `:766-774` 调同一守卫 | `finding_key_ambiguous` 进入 rejected，`consume_dispositions()` 在 `:881-884` 置 `fail_closed=True`；歧义 P1 留在 `remaining_p1_ids`，`aggregate.py:880-896` 不会改成 gate pass |
| `.github/actions/gate-aggregator/convergence.py:1557-1577` 的 `evaluate_round()` 消费 | 它再次调用 `consume_dispositions()`，因此同样经过 `:724-740` 或 `:766-774`；`finding_key_ambiguous` 在 `_DISPOSITION_FAIL_CLOSED_REASONS` `:809-818` 内 | 直接收敛路径返回 `fail_closed` state，reason 为 `invalid disposition: finding_key_ambiguous` |
| `.github/actions/gate-aggregator/convergence.py:790-806` 的 `disposition_status()` | 只是 `validate_disposition_receipt()` 的观察投影，仍经过上述两个分支 | 返回 `valid=False, consumable=False, reason_code=finding_key_ambiguous`，不写收敛状态 |
| `.github/actions/gate-aggregator/convergence.py:1935-1942` 的历史回放 | 调用 `evaluate_round()`，但固定传 `waiver_receipts=()`，没有可消费的 disposition；守卫因此只作空输入校验 | 无 disposition 可被消费，不构成第三个消费出口 |
| `.github/actions/review-ledger/build_ledger.py:690-697` | 只从已经生成的 terminal envelope 读取并在 `:504-564` 做结构校验，不重新决定或消费原始 disposition | 是终端投影消费者，不是绕过消费守卫的入口 |

因此，稳定键与兼容路径各有一个共享守卫调用点（`:726-729`、`:768-772`）；实际 gate 入口和 evaluator 二次入口都通过 `consume_dispositions()`，没有发现第三个能消费原始 disposition 的生产入口。

## 2. 三元在途 primary 的兼容豁免

构造了真正的旧形状：

```text
p1_ids = ("p1", "p2")
p1_findings = (
  ("p1", "major", "inferred"),
  ("p2", "major", "inferred"),
)
```

另在构造语义中令两条 finding 具有相同的 `file/line/category/severity`，并传入两份没有 `finding_key`、分别指向 `p1`/`p2` 的合法旧回执。实测两条 status 都是 `valid=True, consumable=True, reason_code=active_false_positive`，消费结果为 `remaining_p1_ids=()`、`fail_closed=False`。兼容分支确实整体跳过稳定键守卫，因为 `:765-774` 只有找到六元 tuple 才计算稳定键。

该跳过在本契约下是安全的：

1. 当前生产投影 `.github/actions/gate-aggregator/aggregate.py:450-480` 对每个 P1 始终写入六元 tuple，并对 `file/line/category` 做结构校验；当前审计不能新生成三元 primary。
2. 三元只来自 `convergence.py:514-533` 兼容读取的历史 canonical primary，或 `:1935-1942` 回放的历史 receipt。旧格式没有稳定键分量，旧 disposition 的绑定依据就是精确 `finding_id`。
3. 给三元 primary 传带 key 的回执不会走兼容分支，而在稳定路径得到零个稳定匹配并返回 `finding_not_current_p1`；不会把稳定键误当作旧 ID 消费。
4. 因而两条“语义上相撞”的 finding 在三元载荷中只是不可观察的外部事实，消费者不能据此安全地声称发生了稳定键冲突；按旧 ID 各自消费是 expand-then-contract 的既有兼容语义，强行按稳定键拒绝会误伤在途旧登记。

## 3. 签发侧三种合法登记场景

对实际 `issue()` 路径 `issue_receipt.py:262-277` 用临时 audit fixture 实测，三种场景均成功写出 v2 receipt（返回码 `0`）：

| 输入 target | 结果 |
| --- | --- |
| 只传人读 id `human-id` | 成功，输出 `finding_id=human-id`，`finding_key` 正确 |
| 只传稳定键（仍通过 `--finding-id` 参数传入） | 成功，解析到同一个 finding，输出 human `finding_id` 与稳定 `finding_key` |
| 传一个恰好等于自身 stable key 的 id | 成功；`by_id` 与 `by_key` 指向同一 dict，`:140-142` 按同对象合法返回 |

另外构造 A 的人读 id 等于 B 的稳定键，`_matching_finding()` 在 `:143-146` 拒绝跨对象歧义；这是不能从 target 字符串判定调用者意图的正确 fail-fast，不影响上表三种合法登记。稳定键重复的 P1 producer 负例也在 `:198-207` 拒绝。

## 4. `DispositionStatus.finding_id` 的下游影响

当前属性实现位于 `convergence.py:214-221`，返回 `receipt.finding_id`，不再把稳定键冒充人读 id。仓内检索后的读取关系如下：

- 测试直接读取该属性：`tests/test_gate_convergence.py:488-495`，断言新 receipt 的人读 id 与 stable key 不混淆。
- 终端投影 `aggregate.py:384-418` 读取的是 `receipt.finding_id` 和可选的 `receipt.finding_key`，不是 `DispositionStatus.finding_id`；稳定 receipt 的当前人读 id 由消费阶段的 `consumed_finding_ids` 写入 `:393-407`。
- convergence envelope `aggregate.py:534-543`、人类诊断 `convergence.py:910-923` 读取 receipt 或 target 字段，不读取该 status 属性。
- ledger `review-ledger/build_ledger.py:518-537,690-697` 读取已经持久化的 `resolved[].finding_id`，并独立保留可选 `finding_key`；没有依赖 status 属性返回稳定键。

没有找到生产代码把 `DispositionStatus.finding_id` 当作稳定键使用，因此该改回不会造成下游稳定键丢失或误写。

## 5. 三态不变式测试的约束力

`tests/test_gate_convergence.py:542-575` 当前三态实测结果：

| 状态 | 带 key | 去掉 key | 结论 |
| --- | --- | --- | --- |
| 命中多条（两条同键、`line=None`） | `(False, False, finding_key_ambiguous)` | `(False, False, finding_key_ambiguous)` | 有约束；将共享 helper monkeypatch 为总返回 `None` 后，这一态变成两条 `(True, True)`，会红 |
| 命中零条（旧 receipt 的 id 也为 `missing`） | `(False, False, finding_not_current_p1)` | `(False, False, finding_not_current_p1)` | 稳定路径受 stale-key 断言约束，但无 key 一侧先在 `:749-757` 因 id 不在 `p1_ids` 返回；移除 helper 后仍为 false，不能单独锁住兼容侧的零命中语义 |
| 恰好一条 | `(True, True, active_false_positive)` | `(True, True, active_false_positive)` | 有约束；若把兼容路径误改为拒绝合法回执会红 |

因此零命中行的兼容侧断言对“共享守卫是否被调用”不是有效变异防线；它不是恒真到所有实现都必然通过，但被错误 id 的早退路径支配。另有低严重度诊断缺口：用例只比较 `(valid, consumable)`，没有断言两路径 `reason_code` 一致；当前值一致，但未来 reason 漂移不会使该用例变红。两点都不改变当前 gate 的消费结果，按 personal 档记为 P3 接受不修。

## 6. 处置判定错误输入的对抗尝试

没有找到本轮修复后、在真实 producer 形状下仍能让歧义 P1 被消费并把 gate 推绿的输入。具体尝试及挡点：

- 两条六元 P1 共享 `("src/lock.py", null, "correctness", "major")`，分别传无 key 的 `p1`/`p2` 回执：两条均为 `finding_key_ambiguous`，消费保留 `('p1','p2')` 且 `fail_closed=True`；直接 `evaluate_round()` 也落 `fail_closed`。
- 同一输入传带 stable key 的回执：稳定路径 `:724-740` 命中多条后拒绝，不能消费。
- 三元历史 primary 配两条语义相同的旧回执：确实按 ID 消费，但这不是错误输入——三元格式没有稳定键字段，且当前 producer 不会产生该形状；带 key 的回执在三元 primary 上被零命中拒绝。
- target 为 `*`、`all` 或包含 `?[]`：兼容路径 `:747-748` 返回 `finding_target_not_exact`，进入 fail-closed 原因集合。
- A 的 id 等于 B 的 stable key：签发侧 `:143-146` fail-fast，不能产生错误绑定的 receipt。

本轮未新增 P1/P2。OCR 前置扫描返回 `status=reviewed`（MiniMax-M3），其两条低严重度意见分别是 reason_code 未锁定与相邻 producer 测试重复构造；前者已作为上述 P3 记录，后者是 maintainability 建议，不影响 personal 档 gate 正确性，也不为此新增抽象。

## Findings

### P3 — 三态用例的零命中兼容断言与诊断码断言不足

- 证据：`tests/test_gate_convergence.py:550-557,565-575`。
- 触发条件：将 `convergence.py:768-774` 的兼容稳定键守卫调用改为不执行时，零命中参数仍因 `finding_id="missing"` 在 `:749-757` 早退，且测试不比较 `reason_code`；因此该态不会对兼容 guard 变异或诊断码漂移报警。
- 影响：降低回归测试对兼容分支零命中/诊断一致性的约束，但现有稳定路径与歧义态测试仍覆盖 gate 不可放行，当前实现没有错误消费。
- 处置：接受不修，不阻塞合并；若后续继续改兼容诊断，再补一个 id 当前存在但稳定记录不可解析/不可匹配的专门 fixture，并断言 reason_code。

总体结论：当前四个提交已堵住第一轮 P1 的第二个兼容出口，签发合法场景未被误杀，status 字段拆分没有发现下游依赖问题。

outcome: pass
