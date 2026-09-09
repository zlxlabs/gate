VERDICT: pass

审查范围：固定提交 `c13c92a4d639b1277dca1382d1b1bccecae91011..4510bc567982a0d0c9108364260a771d7a6bcda6`，只审该 SHA 范围。
风险档：`personal`。目标是把 canonical primary finding 的 `trigger_kind` 绑定到 Gate 终态、disposition 消费、摘要和 replay；不修改实现边界之外的 3b 历史检索或真实 canary。

结论：未发现 personal 档 P1。当前冻结实现的真实门禁路径已做到：`inferred` P1 配合合法 receipt 才能清除阻塞；`measured`、`unmeasurable`、缺失、未知或非字符串触发类型均保持阻塞。P2/P3 仅记录为不阻塞 backlog，接受不修。

## 关键不变式核对

| 不变式 | 结论与证据 |
|---|---|
| canonical primary 的 finding id、P1 severity、`trigger_kind` 绑定 | 通过。`aggregate.py:445-466` 从 `audit.result.findings` 投影 `(id, severity, trigger_kind)`；`convergence.py:126-155` 将其纳入 `CanonicalPrimary`，并由 `_primary_errors` 做结构校验。 |
| 只有 `inferred` P1 能被 disposition receipt 消费 | 通过。`convergence.py:550-568` 精确要求 `finding[2] == "inferred"`；拒绝原因 `finding_trigger_not_inferred` 进入 fail-closed 集合。`issue_receipt.py:164-170` 的真实 producer 对非 inferred 直接非零退出。 |
| 触发类型改变会使摘要失效，旧 receipt 不能跨类型放行 | 通过。`convergence.py:398` 将 `trigger_kind` 纳入 canonical audit digest；receipt 的 round/event fingerprint 也包含 `p1_findings`（`:1510-1548` 附近）。实测 inferred 与 measured 的 canonical digest 不同，且 measured 等五类均未消费 receipt。 |
| 缺字段不默认成 inferred，旧 receipt 仍安全 | 通过。缺字段在聚合器中投影为 `None`，消费侧只接受精确字符串 `inferred`；旧 receipt 缺 `p1_findings` 时最多保留既有阻塞证据，不会被推断为可豁免。 |
| 跨发布边界由真实 producer/consumer 证明 | 当前行为通过，新增字段的证据有缺口，见 F-1。生产写入函数本身在 `aggregate.py:902-912` 使用 `receipt.as_dict()` 并先 `validate_receipt`；但新增 P1 字段的测试没有直接读取该函数写出的字节。 |

## 验证事实

- 冻结 diff 为 8 个文件、`186 insertions(+), 23 deletions(-)`，超过任务卡 `Diff-Lines-Hard: 150`；这是预算事实，不单独计 finding。
- 目标提交临时副本运行：`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_aggregator.py tests/test_gate_convergence.py tests/test_gate_convergence_artifact.py tests/test_review_ledger.py`，结果 `569 passed in 5.81s`。此前同一目标副本全量验证为 `859 passed in 47.04s`，退出码 0。
- 目标提交运行 `python3 scripts/check_pinned_uses.py`，退出码 0，输出 `OK: checked 8 live workflow/action metadata file(s)`。
- 红验在 base 临时副本进行：仅拷入新增目标测试后，base 的 measured 聚合测试仍会把 required gate 错误置为 pass；producer 对 measured、unmeasurable、缺失、unknown 的新增负例均从 base 的 0 变为目标提交的非零，5 个失败均为预期 `AssertionError`/负例失败。
- 真实行为探针矩阵：`inferred → gate_result=pass, consumed=1`；`measured/unmeasurable/missing/unknown → gate_result=fail, consumed=0, rejected_reasons={finding_trigger_not_inferred: 1}`。真实 `issue_receipt.py` subprocess 只对 inferred 写出 canonical JSON，其他四类返回 1。`aggregate._write_convergence_receipt` 写出的真实字节包含 `p1_findings=[["p1","major","inferred"]]`。
- OCR 前置扫描 envelope 为 `status=reviewed`、`profile=minimax`、`cli_status=complete`，共 6 条候选；复核计数 `confirmed=0, refuted=6`。这些候选的 `existing_code` 指向 base checkout，与冻结目标不符，故未采纳为 finding。

## Findings

### F-1 — P2：新增 convergence receipt 字段缺少真实写盘边界的回归锁

- **位置**：`tests/test_gate_convergence_artifact.py:466-480`；对照生产写入 `.github/actions/gate-aggregator/aggregate.py:902-912`。
- **违反**：关键不变式 5；设计的 3a 验收要求读取 aggregate CLI 实际写出的 JSON 字节，再喂给 `validate_receipt()` 与 `replay_receipts()`。
- **触发方式**：新增测试直接调用 `outcome.convergence_receipt.as_dict()`，再用测试私有 `_receipt_from_payload()` 解码并 replay；没有调用 aggregate CLI 的 `--convergence-receipt-path`，也没有读取 `_write_convergence_receipt()` 的文件。现有真实 CLI 字节测试 `:117-171` 的 audit 没有 P1 finding，因此即使生产写入遗漏 `p1_findings`，该测试仍可通过。这样会留下“单元对象含字段、实际 artifact 不含字段”的未观测路径。
- **当前后果**：不是已观察到的门禁 bypass。固定实现的 writer 确实调用 `receipt.as_dict()`，当前真实 probe 也观察到字段；问题是测试不能证明该行为不会回归。个人档下判 P2，接受不修，不阻塞本轮。
- **最小修法方向**：把现有真实 CLI 写盘测试改为带一个 `inferred` P1 finding，并断言文件字节的 `p1_findings`，再将该文件 payload 解码后 replay；不要用测试私有字典构造替代 producer 证据。

### F-2 — P3：非法/空白 `trigger_kind` 的测试矩阵未显式覆盖

- **位置**：`tests/test_gate_convergence_artifact.py:458-463`、`tests/test_gate_aggregator.py:2656-2674`。
- **违反**：关键不变式 2、4 的测试完整性；当前测试覆盖 inferred、measured、unmeasurable、缺失和 unknown，但未覆盖 `""`、空白字符串或非字符串值。
- **触发方式与证据**：`issue_receipt.py:169` 的精确比较以及 `aggregate.py:462-464` 的非字符串归 `None` 当前都会拒绝这些输入；因此没有当前运行时放行路径。缺口只意味着未来若把精确比较软化为 truthy/归一化判断，现有矩阵未必转红。
- **当前后果**：真实实现仍 fail-closed，个人档不构成 P1/P2；接受不修，记 backlog。最小补充是将 `""`、`"  "` 和 `1` 加入 producer/aggregate 负例参数化。

### F-3 — P3：producer 模块 docstring 未同步 digest 新字段

- **位置**：`.github/actions/gate-disposition/issue_receipt.py:4-7`。
- **违反**：canonical digest 的文档契约。文档仍写 stable subset 为 `finding id/severity/file/line + verdict`，而冻结实现已在 `convergence.py:398` 将 `trigger_kind` 纳入 `CANONICAL_AUDIT_DIGEST_FINDING_FIELDS`。
- **触发方式与证据**：维护者按 docstring 手工重算 digest，会漏掉 `trigger_kind`，造成与真实 producer/consumer 不一致；代码路径本身调用统一的 `canonical_audit_digest`，所以当前不会因该 docstring 产生错误放行。
- **当前后果**：仅文档误导，判 P3，接受不修，记 backlog。应在后续文档收口时把 `trigger_kind` 加入字段说明。

## P1 复核与熵增结论

- P1 两问均未命中：真实 Gate subprocess/aggregate 路径会区分 inferred 与其他类型；其他类型的后果是 required gate 保持 fail，而不是静默 pass，因此没有 personal 档数据丢失、静默放行或崩溃红线。
- `CanonicalPrimary.p1_findings` 与 convergence `Receipt.p1_findings` 是当前 producer、receipt writer、disposition consumer、replay 四个既有消费者共同需要的同一证据，不是无第二消费者的抽象。canonical digest 字段更新也是既有 issuer/consumer 合约同步所需。
- 未发现新增 fallback、retry、审批机制、额外状态 writer 或终态双写路径。预算超限和上述 P2/P3 不改变 `pass` verdict。

## Backlog / 越界项

- 3b 的 convergence artifact 分页、跨 run 下载、生产历史 JSON parser、真实 `gate/gate` canary 不在本冻结 diff；本轮仅核对 3a 的 writer 与纯 replay 边界。F-1 是新增字段的 producer-byte 测试缺口，不把未通电的 3b 读回实现误报为当前代码缺陷。
- `aggregate.py` 对缺少 convergence scope 时保留 single-round pass、`_canonical_finding_sort_key` 未将 trigger_kind 纳入排序键、以及 gate 侧旧设计文档仍列旧 digest 字段，均为存量/未来收口观察，不作为本轮 finding。
