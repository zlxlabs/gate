# 独立评审结论

评审范围：`e74743bb621c5a373956545c730dbe81b5304eaa..92421ac2114d229721bdaee6e2a1e3583d6b03b8`，共 8 个提交。

结论：changes requested。发现 1 条 P1、2 条 P2。P1 违反本轮锁定的不变式：`finding_key` 为空的兼容路径不得比稳定键路径更宽松；它能让重复稳定键的当前 P1 被消费而不 fail-closed。

## 1. 稳定键碰撞与歧义

规范化实现位于 `.github/actions/gate-aggregator/convergence.py:423-446`：四个字段逐个使用 `missing/null/value` 标签，再用紧凑 JSON 编码。针对 `canonical_finding_key()` 的构造实测如下（`False` 表示两种输入产出的 key 不相同）：

| 构造 | 结果 |
| --- | --- |
| 分隔符注入：`file="a|b"` 对 `file="a", category="b"` | `False` |
| 路径：`./src/a.py` 对 `src/a.py` | `False` |
| 大小写：`SRC/a.py` 对 `src/a.py` | `False` |
| Unicode NFC `é.py` 对 NFD `é.py` | `False` |
| `line=12` 对 `line="12"` | `False` |
| `line=12` 对 `line=true` | `False` |
| `line=null` 对缺少 `line` | `False` |
| `category=""` 对 `category=null` | `False` |
| 四字段完全相同 | `True`（预期的重复 key） |

因此没有找到“两个不同四字段值因编码碰撞而得到同一个 key”的路径；路径拼接、大小写、Unicode 等价形态也不会碰撞。等价路径的文字差异会得到不同 key，但它只导致旧登记不能应用、不会静默把另一条 finding 当成已处置。

重复四字段是有意暴露的歧义：`.github/actions/gate-disposition/issue_receipt.py:186-195` 的签发守卫会拒绝同 key 的多条 P1；`.github/actions/gate-aggregator/convergence.py:701-724` 的消费守卫会返回 `finding_key_ambiguous`。

## 2. 消费入口与守卫覆盖

生产入口链为：`.github/actions/gate-aggregator/aggregate.py:1586-1631` 解析下载回执，`:839-863` 把它们交给 `consume_dispositions()`；随后 `.github/actions/gate-aggregator/convergence.py:1530-1550` 只把消费结果交给 `evaluate_round()`。`disposition_status()`（`:763-779`）只是同一校验器的观察投影，账本的 `.github/actions/review-ledger/build_ledger.py:504-592` 只校验已经产生的终端投影，不会另行决定 gate。

稳定键入口确实经过 `:701-724` 的重复匹配守卫；但兼容分支在 `:725-747` 另行按 `finding_id` 取 finding，没有调用稳定键重复守卫。这是下一节的 P1，不是入口遗漏的推测。

## 3. P1：缺少 `finding_key` 的兼容回执绕过重复 key 守卫

证据：`.github/actions/gate-aggregator/convergence.py:701-747`，以及 `.github/actions/gate-aggregator/convergence.py:782-792`。

触发输入：当前 primary 含两条 P1，除 `id` 外四字段完全相同，且 `line` 都是 `null`：

```json
[
  {"id":"p1","severity":"major","trigger_kind":"inferred","file":"src/lock.py","line":null,"category":"correctness"},
  {"id":"p2","severity":"major","trigger_kind":"inferred","file":"src/lock.py","line":null,"category":"correctness"}
]
```

构造一份其余绑定字段均正确、但省略 `finding_key` 的 v2 回执：`finding_id="p1"`、`audit_digest` 为当前 audit digest。实测结果：

```text
带 finding_key：valid=False, consumable=False, reason_code=finding_key_ambiguous
缺 finding_key：valid=True, consumable=True, reason_code=active_false_positive
缺 key 的 consume：remaining_p1_ids=('p2',), fail_closed=False
缺 key 的两份回执（p1/p2）：aggregate gate_result=pass, remaining_p1_ids=(), fail_closed=False
```

`parse_disposition_receipt()` 在 `.github/actions/gate-aggregator/convergence.py:899-924` 对缺失字段填空串，因而这不是不可到达的类型分支；`.github/actions/gate-aggregator/convergence.py:1194-1256` 也没有在 primary 投影处拒绝重复稳定键。结果是稳定键路径明确拒绝的歧义 P1，可以经旧 ID 路径被消费，并在两份回执时把 gate 置绿。

按 `personal` 档 P1 两问：

- 真实使用方式下会被触发吗？会。兼容分支明确保留在途 v2 回执，回执解析允许省略 `finding_key`；评审模型的自由 finding ID 也允许当前 audit 出现相同四字段、不同 ID 的重复 P1。
- 触发后的后果能否接受？不能。歧义 key 未 fail-loud，消费结果可使 gate 通过，属于静默错误处置并可能越过唯一需要防护的 P1 门禁。

## 4. audit digest 的区分力

`.github/actions/gate-aggregator/convergence.py:419-479` 现在只摘要 `file/line/category/severity`，并在 `.github/actions/gate-disposition/issue_receipt.py:200-209` 生成回执。实测两份 audit 的四字段完全相同、但 `id`、`trigger_kind`、finding 正文和 evidence 不同，`canonical_audit_digest()` 相同；顶层 `verdict` 改变时 digest 不同。

这份相同 digest 的直接后果是：只要稳定四元组相同，回执可以跨 finding ID 变化复用；`validate_disposition_receipt()` 仍在 `.github/actions/gate-aggregator/convergence.py:749-760` 检查当前 severity 和 `trigger_kind == "inferred"`，所以 trigger 变化不会放行。正文/evidence 不属于本轮锁定的稳定键分量，当前结果没有额外造成错误 gate 判定。另测到“缺失字段”和显式 `null` 在 digest 投影中会因 `.get()` 相同，但缺字段 P1 会先被 `aggregate.py:469-475` 拒绝，不能进入消费。

## 5. `line: null` 路径

`.github/actions/gate-aggregator/aggregate.py:450-480` 接受显式 `line: null`，`.github/actions/gate-aggregator/convergence.py:1239-1244` 与 `:1763-1768` 也接受它；key 编码为 `line:["null"]`。单条 `line:null` P1 的稳定回执实测为 `active_false_positive` 并可消费。

两条相同 `file/category/severity` 且均为 `line:null` 的 P1 实测为 `finding_key_ambiguous` / `fail_closed=True`，说明稳定键守卫本身有效；同一输入配合上一节的缺 key 回执则得到 `fail_closed=False`，故该形态正是 P1 的可复现触发器。

## 6. 账本、终端和人读字段拆分

生产终端投影 `.github/actions/gate-aggregator/aggregate.py:384-418` 使用消费时解析出的当前人读 ID 写入 `resolved[].finding_id`，并仅在新回执存在时把稳定键写入 `resolved[].finding_key`；账本 `.github/actions/review-ledger/build_ledger.py:504-564` 保留这两个字段，且没有把 G4 展示字符串混入结构化块。该主路径的字段拆分实测为 `finding_id="p1"`、`finding_key=<稳定 JSON key>`，自洽。

但有一处 API 投影反了：`.github/actions/gate-aggregator/convergence.py:214-221` 的 `DispositionStatus.finding_id` 返回 `receipt.finding_key or receipt.finding_id`。对新回执实测为：回执字段 `finding_id="human-id"`、`finding_key=<key>`，而 `status.finding_id=<key>`。这不会影响当前 `aggregate.evaluate()` 的 gate 决定，因为当前终端投影不读取该属性，但会误导任何把 status 投影到人读/账本字段的调用方。

## Findings

### P1 — 兼容分支允许歧义稳定键被按旧 ID 消费

- 证据：`.github/actions/gate-aggregator/convergence.py:725-747`；稳定路径守卫在 `:701-724`。
- 触发条件：当前有两条 P1 共享四元稳定键；回执 JSON 缺少 `finding_key`，保留有效当前 `finding_id`、scope、epoch、head 和 digest。
- 结果：旧路径返回 `active_false_positive` 且不置 `fail_closed`；两条对应回执可使 aggregate gate `pass`。
- 违反不变式：同 key 命中多条 P1 必须 fail-loud，兼容路径不得比稳定键路径宽松。
- Personal P1 两问：真实保留/解析 v2 回执与模型重复 finding 可触发；后果是静默消费歧义 P1 并可能放行 gate，不可接受。

### P2 — 稳定 key 输入与自由 finding ID 共享未消歧的命名空间

- 证据：`.github/actions/gate-disposition/issue_receipt.py:122-140,184-209`。
- 触发条件：把 B 的稳定 key `K_B` 作为 target，同时让另一条 finding A 的人读 `id` 恰好等于 `K_B`。`_matching_finding()` 先命中唯一 `by_id`，会选择 A；实测 target 是 B 的 key，但选中位置为 `src/a.py:10`，目标 B 实际为 `src/b.py:20`。
- 后果：调用方按函数声明支持的“stable key 或 old finding id”传入 `K_B` 时，可能签发给错误 finding。当前 workflow 输入描述仍是 exact finding id，故不是自动 gate 绕过；但这是静默的登记目标错误，应拒绝歧义而不是按优先级猜测。

### P2 — `DispositionStatus.finding_id` 在新回执上返回稳定键

- 证据：`.github/actions/gate-aggregator/convergence.py:214-221`。
- 触发条件：调用 `validate_disposition_receipt()` 得到新回执的 status，再读取 `status.finding_id`；当回执同时有 human `finding_id` 与非空 `finding_key` 时，返回 key 而不是 human ID。
- 后果：任何将 status 当作结构化投影的消费者会把稳定键写入/展示为 `finding_id`，与终端/账本的拆分契约不一致；当前仓库没有 gate 决策路径读取该属性，所以不升级为 P1。

## 7. 处置判定错误输入的最终结论

已给出具体错误输入：两个 `line:null`、相同 `file/category/severity`、不同 ID 的当前 P1，加上省略 `finding_key` 的 `p1`/`p2` 回执。带 key 的版本被拒绝为 ambiguous，缺 key 的版本被消费，双回执版本直接得到 `gate_result=pass`。这不是“未发现”，而是已复现的处置判定错误，对应上面的 P1。

辅助验证：定向生产/接缝测试 `587 passed`；`ocr-review` 已启动但约 12 分钟无 envelope，手动中止并清理了本次启动的子进程，不能称为已扫过。

outcome: changes-requested
