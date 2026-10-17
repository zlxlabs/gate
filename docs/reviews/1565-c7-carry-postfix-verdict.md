# PR #342：C7 历史未关闭事项持久化独立审查

failure-visibility: p1-found

## Verdict

- 判定：Fail；发现 1 条 P1，阻断交付。
- 范围：`358a0caf7f5055c942e62f06222cd3d0970773bb..12519bd5f53cb200b8e1bda84d49cd1db2d66b32`，全量审查本次 4 个变更文件。
- 不变式：上一轮未关闭 finding 必须经真实 aggregator 输出、review-ledger JSONL 写入及下一轮读取后仍保留；当前轮没有再报告不表示关闭，历史 open 只作为 previous context。

## P1 — 实际 aggregate argv 丢失已注解 audit 的历史 open 集

- 证据：`.github/workflows/gate-v2.yml:1121-1123` 在 primary job 用 `previous-findings.json` 调 `--annotate-primary-audit`；`.github/workflows/gate-v2.yml:1237-1254` 随后上传同一 canonical audit。gate job 下载该 audit（`2002-2013`），实际 aggregate 调用（`2050-2072`）没有传 `--previous-findings-path`。但 `.github/actions/gate-aggregator/aggregate.py:3942-3949` 遇到已带 `relation_to_previous` 的 finding 且没有该参数时，把 `previous_round.findings` 置空；`610-650` 因而只把本轮 findings 放进 `open_findings`。ledger 将 terminal 中的 relation block 写入 JSONL（`.github/actions/review-ledger/build_ledger.py:757-758`）；下一轮 loader 又优先读取 `open_findings`（`aggregate.py:746-754`）。
- 回归测试缺口：新增测试实际调用 annotate CLI（`tests/test_gate_aggregator.py:5459-5464`），但随后在 `5467-5469` 把同一 previous JSON 再传给 aggregate；workflow 的 gate job 没有这个参数，因此该测试覆盖了不同调用路径。
- 实测：H0 定向测试 `test_silent_round_carries_open_finding_through_ledger_jsonl` 通过（1 passed）。随后从该测试派生私有三轮重放，保留真实 annotate CLI、aggregator、ledger JSONL producer 和 loader，将 aggregate argv 设为 workflow 实际调用并核对其输出；结果为 `round 2: previous=['open-with-evidence'] open=['new-current']`、`round 3: previous=['new-current'] open=['new-current']`。第二轮非空 current finding 后，第一轮 open finding 已从第三轮 previous context 消失。该重放只把 Silo `get` 指向生成的 JSONL 文件，没有生产写入或在线启用结论。
- 后果与风险：新 finding 出现的轮次会从活动 open carry 与后续 primary context 静默移除旧 finding；下一轮即使没有再次报告它，也无法从最新 ledger 恢复。workflow 另有永久归档路径（ledger `gate-v2.yml:2750-2777`、canonical audit `2779-2805`），本 finding 指向活动 carry 丢失，不声称原始归档被删除；仍违反本卡核心不变式，属于 personal 档静默状态丢失。
- 阻断：是，P1。需让实际 workflow 的 annotate→aggregate→JSONL→loader 路径保留前一轮 open finding，并以该真实 argv 的非空 current finding 三轮回归覆盖。

## 其余检查

- 旧版 relation block 缺少 `open_findings`：fixture `previous-round-pr1063-ledger.json` 的定向 loader 测试通过，回退读取 `review.result.findings`。
- 明确空 `open_findings`：私有 JSONL 探针返回 `[]`，没有错误回退到旧 finding 列表；符合“字段存在即采用新格式”的读取语义。
- 同 ID 重复观察且新事实/位置齐全：定向探针得到 `repeat`，carry 保留新 file/line/quote，未修改上一轮输入；未发现可达形态下的额外缺陷。任意手造缺必需字段的 hybrid 不作为生产 finding。
- 原始记录归档：workflow 声明将 raw review ledger 与 canonical audit 发到 permanent archive，并执行读回字节比较；本审查未执行生产写入或线上读回。
- OCR envelope：`reviewed`。确认第一条候选；其余候选没有独立可达后果，或把“历史 open 作为 previous context”误判成当前 counts/items 扩大，未列为 finding。

## 验证范围

- 运行 H0 的新增三轮定向测试：`1 passed`。
- 运行旧 ledger 格式定向测试：`1 passed`；运行空 open 列表私有 JSONL 探针：返回空列表。
- 未运行全量测试、付费模型、生产写入；主干基线按卡面记录为不可用。
