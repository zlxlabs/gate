# E2a 终态主审结果传输独立审查

failure-visibility: p2-only

## 结论

**未通过，要求修正后复审。** 发现 2 项 P2，均阻断本卡交付：终态可能持久化格式无效审计的 `pass`，且历史消费者会接受不属于主审投影域的枚举值。未发现 P1。

审查范围固定为 `fed4ae1079f002b2cf5c73f454dfb026e2c633d5..13f08dd4b3aff5d25583e51af1b1cb01f2bacb63`。本次仅新增 verdict，没有修改实现。

## Findings

### P2 · 阻断：格式校验失败后仍将主审记录为 pass

- **违反规格：** 1、2、3。`primary_verdict` 必须来自通过格式与身份校验、并与 primary job 一致的 canonical audit；无效 audit 不得生成 `pass`。
- **触发条件：** audit 的 identity、verdict、source attempt 与 primary job 均匹配，verdict 为 `pass`，但 audit 带有 convergence scope 且 `result.findings` 中 severity 缺失或非法。
- **证据：** `.github/actions/gate-aggregator/aggregate.py:1411` 在 convergence finding 格式检查前设置 `primary_verdict`。随后 `.github/actions/gate-aggregator/aggregate.py:1502` 的 `_canonical_p1_findings` 拒绝该 audit，并将 gate 设为 `unavailable`、reason 设为 `audit_invalid` 后返回；此前的 `primary_verdict` 没有清除。终态生产者仍在 `.github/actions/gate-aggregator/aggregate.py:3618` 写入文件，workflow 随后上传该文件（`gate-v2.yml:2005,2032,2057-2071`）。固定目标 SHA 上用 `AGG.main` 实际生成的 `gate-terminal.json` 得到 `gate=unavailable`、`classification=integration_error`、`reason_code=audit_invalid`，但原始 JSON 字节仍包含 `"primary_verdict": "pass"`。
- **影响与阻断：** 审计格式已经被拒绝，历史记录却声称主审通过；这会污染跨轮主审结果。阻断交付。

### P2 · 阻断：历史消费者接受非法的主审投影枚举

- **违反规格：** 1、2。`primary_verdict` 的值域仅为 `pass | fail | unavailable`；其他枚举必须进入既有 incomplete 路径。
- **触发条件：** 历史终态 JSON 的 `primary_verdict` 为 `not_expected` 或 `waived`。
- **证据：** 全局 `PRIMARY_VERDICT_DOMAIN` 是主审 audit 的域，包含 `not_expected` 和 `waived`；终态生产验证 `.github/actions/gate-aggregator/aggregate.py:1026` 与历史读取验证 `.github/actions/gate-aggregator/aggregate.py:2171` 直接复用该较宽域。固定目标 SHA 上将这两种值放入历史终态并经过 `_consume_terminal_artifact`，两者均产生 `rows=1`，`incomplete=[]`；它们没有被隔离为不完整记录。
- **影响与阻断：** 非法值会被当成可信主审结果读回，和“非法枚举走 incomplete 路径”的契约相反。阻断交付。

## 已核对的路径与未发现项

- 质量失败且 primary audit 为 pass 时，终态保留 `primary_verdict=pass`，gate 仍为既有质量失败；不会把质量失败误记为主审失败。
- 缺少 audit、primary job 与 audit verdict 不匹配时，真实终态文件不含 `primary_verdict`；draft skip 不制造 pass/fail。
- 旧 terminal 字段缺失或为 null 时，历史投影为 `None`；任意未知字符串会触发既有 incomplete 路径。
- diff 未改 required gate 判定、重试、历史查询次数或收敛状态机；未删除原始 audit 证据。跨轮计数未实现，符合锁定决策。
- 新增测试文件：无。diff 仅为 `.github/actions/gate-aggregator/aggregate.py` 与 `tests/test_gate_aggregator.py`，共 89 行新增、1 行删除。
- 无额外范围建议。
