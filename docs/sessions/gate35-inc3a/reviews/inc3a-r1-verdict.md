VERDICT: fail

审查范围：`1ea66b7..a501b7a1d43092efd77c10488efefceaf3630c04`，只审该两点 SHA diff。
风险档：`personal`；本轮视角：结构性降层 + 熵增。实际 diff 为 8 个文件、500 行新增、40 行删除，超过任务卡的 400 行 hard budget；该预算事实不单独计入 finding。

本轮新证据：

- H0 临时解包副本运行 `uv run --with pytest,PyYAML python -m pytest -q`：`584 passed in 4.64s`。
- H0 运行 `python3 scripts/check_pinned_uses.py`：`OK: checked 7 live workflow/action metadata file(s)`；冻结 diff 的 `git diff --check` 通过。
- H0 直接调用 `aggregate.evaluate()` 的临时探针：身份合法、`verdict=pass`、`result.findings=[]`，但删除任一 convergence scope 字段时得到 `ok=True, gate_result=pass, receipt=False, envelope=False`。
- OCR 前置扫描最终为 `status=reviewed`，MiniMax 复核 10/10、confirmed 0；其复核证据混用了基线与目标代码，未采纳其 refuted 结论。

## 降层三问

### 1. 终态写入前的不可逆动作与发布顺序

新增本地 receipt 写盘发生在 `.github/actions/gate-aggregator/aggregate.py:741-751`，且 `_finish()` 在 `:1802-1814` 先写 receipt、再追加 Step Summary；`gate-terminal.json` 的原子写入在 `:1834-1839`。工作流顺序是 aggregate step `:903-956` → receipt artifact upload `:958-965` → `gate-terminal.json` upload `:967-974` → status panel `:976-1010`。primary audit artifact 的上传和下载发生在本 diff 之外、aggregate 之前。

receipt 写盘失败会在 terminal 写盘前直接抛错，aggregate 返回非零，receipt 输出为空，后续 terminal 上传因文件缺失而红，panel 不发布。receipt artifact upload 失败则不同：terminal upload 仍由 `if: always()` 执行；只要 terminal upload 成功，panel 仍满足 `:976-978` 的条件并发布 terminal 中的 pass。因此存在“receipt 账本上传失败，但 terminal/pass 状态已对外发布”的路径，详见 F-2。

可复核命令：

```bash
git show a501b7a1d43092efd77c10488efefceaf3630c04:.github/actions/gate-aggregator/aggregate.py | nl -ba | sed -n '741,751p;1792,1840p'
git show a501b7a1d43092efd77c10488efefceaf3630c04:.github/workflows/gate-v2.yml | nl -ba | sed -n '903,978p'
```

### 2. `event_id` 在实际部署形态下是否唯一

通过。`.github/actions/gate-aggregator/convergence.py:877-878` 将 `event_id` 绑定到 `epoch + run_id + run_attempt + audit_digest + receipt_kind`；`round_key` 则是 `epoch + run_id + audit_digest`（`:881-883`）。

- 同 PR 的并行 workflow/attempt：不同 run 使用不同 `run_id`；同一 run 的 attempt 使用不同 `run_attempt`，因此 event id 不撞。
- `rerun --failed`：同一 `run_id`、递增 `run_attempt`，event id 会变化；若复用同一 audit，`round_key` 不变并由 replay 去重，不会重复计 round。
- force-push 换 head：scope 的 `head_sha`/diff 变化使 `derive_epoch()`（`:603-607`）变化；旧 receipt 的 epoch 校验（`:1311-1319`、`:1340-1351`）拒绝旧 generation。GitHub 同时会产生新的 run id，但即使只看 epoch 也足够隔代。

可复核命令：

```bash
git show a501b7a1d43092efd77c10488efefceaf3630c04:.github/actions/gate-aggregator/convergence.py | nl -ba | sed -n '877,883p;1070,1117p;1298,1351p'
```

### 3. 守卫保护的是写入还是行为，是否存在绿 gate 无成功 receipt

两者都有保护，但保护不完整。receipt 生产只在 `RoundDecision` 被接受、非 no-op 且有 `event_id` 时发生（`aggregate.py:712-720`）；写出前做 `validate_receipt` 并 canonical/atomic 落盘（`aggregate.py:741-751`）；shell 捕获 aggregate 原码并写 `GITHUB_OUTPUT`（`gate-v2.yml:930-956`），upload action 没有 `continue-on-error`（`:958-965`）。这些路径对正常 eligible round 有效。

但 `aggregate.py:400-401` 对缺失 scope 字段只返回 `None`，`evaluate()` 的 convergence handoff 又以 `scope is not None` 为门槛（`:674-682`）。身份和 verdict 都合法、但 audit 缺少 `infra_diff` 等字段时，旧的 single-round `code_pass` 结果不变，aggregate 以 0 退出，输出 `convergence-receipt=absent`，上传被跳过；因此确实存在 `gate/gate` 变绿而 receipt 未成功上传的路径。这是 F-1。

可复核命令：

```bash
uv run --with pytest,PyYAML python -m pytest -q
python3 scripts/check_pinned_uses.py
```

前一命令在 H0 临时解包副本的结果为 `584 passed`；缺 scope 的结果由临时 H0 探针直接打印为 `{'ok': True, 'gate_result': 'pass', ..., 'receipt': False}`。

## 熵增审查

| 新增项 | 熵 +1？ | 判断 |
|---|---:|---|
| `Outcome.convergence_receipt` | 否 | 它是 receipt 写盘与 workflow 输出之间的必要事实传递，消费者至少有 `_finish()` 和 workflow upload gate，不是旧 envelope 的镜像。 |
| `aggregate.py:389-415` 的 `_CONVERGENCE_SCOPE_FIELDS` + scope 投影 | 是 | 它在 aggregator 重新维护一份 scope 字段清单，和 `convergence.py:559-590` 的验证事实源分叉；该重复还放大 F-1 的缺字段静默路径。应由 evaluator 暴露唯一的 scope contract，或在一个边界集中校验。 |
| `convergence.py:1238-1274` 的 `receipt_for_round()` | 工厂本身否；参数形状是 | 集中复制 `RoundDecision` 三个 key 是 spec 2 所需；但新 API 的 `source_attempt=None → primary.run_attempt` 是 fallback，`artifact_id` 在 3a 没有生产消费者。当前 aggregate 调用确实传入 `audit_source`，所以它不是当前 P1，但增加了可被误用的第二条来源路径，见 F-3。 |
| `_read_audit_file()` 返回 raw bytes | 否 | spec 7 明确要求跨进程原始字节 digest，这是必要的边界事实，不是包装层。 |
| `_write_convergence_receipt()`、`--convergence-receipt-path`、workflow env/output、receipt upload step | 否 | 分别是写盘、CLI 传递和 artifact barrier 的最小接线，且对应 artifact 契约；没有额外 retry/fallback/catch。 |
| 新增跨进程测试与设计文档 | 否 | 测试固定 producer bytes/argv/replay，文档同步 3a/3b 边界，不引入运行时状态。 |

本轮没有新增重试；没有新增防御性吞错分支。唯一明确的新增 fallback 是 F-3 的 `source_attempt` 默认值；F-1 的 `scope=None` 则是更严重的静默降级。

## Findings

| ID | 严重级 | 文件:行 | 违反 spec | 具体触发路径 | 建议修法 |
|---|---|---|---|---|---|
| F-1 | P1 | `.github/actions/gate-aggregator/aggregate.py:400-415,674-682` | spec 1、4、5 | `primary_result=success`、`quality_result=success`、身份合法的 canonical `verdict=pass` audit 缺少任一 scope 字段（如 `infra_diff`）→ `_convergence_scope_from_audit()` 返回 `None` → convergence 不执行、`Outcome` 保持 `code_pass/ok=True` → aggregate 退出 0、无 receipt，workflow 跳过 upload，Required Check 可绿。审计缺失/损坏本应 fail-closed；“无 receipt”只允许 draft/fork/预期 skip 等非 eligible 轮。 | 将 scope 缺失明确转换为 `audit_invalid`/非零 fail-closed，而不是把 `None` 当作没有 handoff；把 scope 字段 contract 与类型校验集中到唯一边界，并为缺字段的 CLI/subprocess 路径加回归锁定。 |
| F-2 | P2 | `.github/workflows/gate-v2.yml:967-978` | spec 3 | eligible round 已写出 receipt，aggregate 返回 0，但 `Upload convergence receipt` 因网络/权限失败 → terminal upload 仍 `always()` 执行并成功 → panel 条件只检查 terminal upload 成功，于是对外发布 terminal/pass 状态，而 receipt artifact 没有成功上传。job 最终会红，所以本条低于 P1；但留下了账本与人类状态不一致。 | 给 receipt upload 加 `id`；当 aggregate output 为 `present` 时，terminal upload 和 panel 都必须以 receipt upload 成功为 barrier；当 output 为 `absent` 的预期 skip/非 eligible 路径，再单独允许 terminal/panel 发布。 |
| F-3 | P2 | `.github/actions/gate-aggregator/convergence.py:1243-1246,1267` | spec 2 的事实来源约束；反熵硬禁（新增 fallback） | 新增 helper 被调用时省略 `source_attempt`（或只拿到跨 attempt audit 却未传 resolver 结果）→ `None` 被静默替换为 `primary.run_attempt` → receipt 通过校验但错误声明 artifact 来源 attempt；`artifact_id` 也是当前 3a 未使用的可选分支。当前 aggregate 调用传入了 `audit_source`，故这是 P2 的接口/熵问题，不是当前运行 P1。 | 要求 `source_attempt` 为必填、缺失即 fail fast；删除当前没有第二消费者的 `artifact_id` 可选参数，待 3b 真有 verified artifact id 时再按最小契约接入。 |

本轮 P1=1；因此总判为 `fail`。除 F-1 外，F-2/F-3 均为 P2，不另行抬升。

## Backlog / 越界项

- 3b 刻意未通电的跨 run receipt listing/download/disambiguation、`convergence_state` 历史读回、disposition receipt 消费、convergence envelope 消费和真实 canary，不计入本轮 findings。
- 既有 status panel 的 fail-open 发布语义、既有 audit schema 边界、非本次 diff 的 workflow 行为未计入 findings。
- OCR 的 10 条 refuted 结果未作为 finding；H0 全量测试与 pinned-use 检查均通过，不代表 F-1 的缺字段组合已被覆盖。
