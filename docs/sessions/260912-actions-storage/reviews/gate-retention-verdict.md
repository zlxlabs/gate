# gate PR #153：诊断产物 retention 独立审查

## 审查对象

- 仓库：`zlxlabs/gate`，risk-tier：`personal`
- 冻结范围：`bf5f022feb093c9510d19eb16c0c0ec11887fb69..1f689b6c00788a7440284853b1c5d49346347259`
- 只审上述范围；范围内只改 `.github/workflows/gate-v2.yml` 和 `tests/test_gate_v2_contract.py`
- 本档 P1 红线是数据丢失、静默出错、崩溃；低于红线的缺陷按 P2 处理

## 审查方法与证据

- 在 `zlxlabs/gate` 与 `/home/zlx/projects/personal/gate-hub` 中按产物前缀检索了 producer、`actions/download-artifact`、`gh run download`、`gh api .../artifacts` 和 `startswith` 过滤。
- OCR 前置扫描返回 `status=reviewed`、`coverage=complete`、`findings=[]`；它没有替代本次逐文件复核。
- 基线临时树只加入本次新增测试后，确认基线 workflow 的 terminal upload 缺少 `retention-days`，运行 `test_gate_terminal_upload_declares_explicit_retention` 得到 `KeyError: 'retention-days'`；因此删掉 workflow 的 `retention-days: 30` 会转红。
- 将冻结 diff 应用于临时 head 树后，`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_contract.py` 为 `131 passed`。
- `git diff --check` 对冻结范围通过。

## 产物与消费者核对

| 产物 | 新 retention | 消费者证据 | 结论 |
|---|---:|---|---|
| `review-ledger-input-v2-*` | 1 天 | 同一 run 的 resolver 在 `gate-v2.yml:1332-1341` 按当前 run 列举；ledger 在 `:1550-1560` 下载。 | 同 run 窗口，满足不变式 1 |
| `primary-audit-v2-*` | 7 天 | 同 run 的 gate/ledger 读取在 `gate-v2.yml:1113,1187-1194,1339,1562-1568`；但 disposition workflow 还按用户输入的历史 run 下载：gate `gate-v2-disposition.yml:59-79`，gate-hub `gate-v2-disposition.yml:47-66`。 | 见 F-1 |
| `primary-review-diagnostics-v2-*` | 3 天 | gate `gate-v2.yml:688-698` 只上传；gate-hub 仅有契约夹具 `tests/test_review_primary.py:851-862`，无程序化下载方。 | 满足不变式 2 |
| `advisory-event-*` | 3 天 | gate `gate-v2.yml:1057-1064` 上传；两仓程序代码中无该前缀的下载/API/过滤读取。gate-hub 文档中的同名命令针对的是 `zlxlabs/agent-config`，不是本产物。 | 满足不变式 2 |
| `gate-convergence-receipt-v1-*` | 3 天 | gate `gate-v2.yml:1251-1258` 上传；两仓无该前缀的程序化读取。`gate-disposition-receipt-v2-*` 是不同产物，聚合器以 `aggregate.py:171,1572-1617` 读取。 | 满足不变式 2 |
| `gate-terminal-v1-*` | 30 天 | `aggregate.py:1476-1556` 按前缀重建历史；targeted 上限为 50 个 run，repo-wide 上限为 5 页×100 条（`:168-169,1483-1489,1521-1546`）。 | 不变式 3 通过 |
| `gate-status-panel-delivery-v1-*` | 3 天 | gate `gate-v2.yml:1306-1313` 上传；两仓无程序化读取方。 | 满足不变式 2 |

## 五条不变式逐条结论

1. **不变式 1：不通过。** 除 F-1 外，ledger input 的 1 天是同 run 消费；terminal 的 30 天符合本次明确接受的历史降级，未发现比 retention 更长的时间筛选消费者。
2. **不变式 2：通过。** diagnostics、advisory、convergence、panel 四类产物在两仓均未发现程序化读取；未把 `advisory-event-*` 误算为 `shadow-event-*`，也未把 convergence receipt 误算为 `gate-disposition-receipt-v2-*`。`shadow-event-*` 的真实消费者仍是 gate-hub monitor/patrol，且 producer 在未改的 `gate-shadow-v2.yml:611-614` 保持 30 天。
3. **不变式 3：通过。** `gate-terminal-v1-*` 在 head 的 `gate-v2.yml:1260-1268` 显式为 30；`aggregate.py` 只按 run/artifact 数量有界扫描，没有按 `created_at`、`completed_at` 或“最近 N 天”裁剪路径。因而没有本条所指的时间维度 P1 候选。
4. **不变式 4：不通过。** 已有 retention 断言与 workflow 同步：convergence 为 3（`tests/test_gate_v2_contract.py:730-739`）、primary audit 为 7（`:825-841`）、diagnostics 为 3（`:844-861`）、terminal 为 30（`:702-709,892-893`）、panel 为 3（`:1518-1534`），ledger 为 30（`:972-985`）。但 input normal/retry 在 workflow `:420,:434` 的 1 天、advisory 在 `:1064` 的 3 天，都没有对应的 retention 断言；现有测试只检查 input 的 name/path/retry（`:934-955`）和 advisory 的 path（`:461-500`）。这两处 retention 只改了一边，见 F-2。
5. **不变式 5：通过。** `codex-review-ledger-v2` 在 `gate-v2.yml:1593-1599` 仍为 30；冻结 diff 的文件清单只有 `gate-v2.yml` 和 `test_gate_v2_contract.py`，没有 `gate.yml` 或 `gate-shadow-v2.yml`。

## 发现清单

### F-1 — P2：primary audit 的 7 天 retention 短于 disposition 消费窗口

- **违反 spec：** 不变式 1。
- **证据：** `gate/.github/workflows/gate-v2-disposition.yml:59-79` 与 `gate-hub/.github/workflows/gate-v2-disposition.yml:47-66` 接受任意 `primary_run_id`/`primary_run_attempt`，构造完整 `primary-audit-v2-*` 名称后执行 `gh run download`；没有 7 天内的约束。producer 在冻结 head `gate-v2.yml:683-686` 已改为 7 天。
- **后果：** PR head 在第 8 天仍未变化时，合法的 P1 disposition 流程会因历史 primary audit 过期而失败，无法签发 receipt。失败是显式的，不是静默放行或崩溃，因此按 personal 档为 P2，而非 P1。

### F-2 — P2：两组 retention 修改没有第二处契约锁定

- **违反 spec：** 不变式 4。
- **证据：** `gate-v2.yml:420,434` 将 `review-ledger-input-v2-*` normal/retry 从 30 改为 1，`:1064` 将 `advisory-event-*` 从 30 改为 3；`tests/test_gate_v2_contract.py:934-955` 和 `:461-500` 均未断言对应 `retention-days`。因此把任一数值改错，现有契约测试仍可能全绿。
- **后果：** retention 的第二处声明缺失，后续存储策略回退或误缩不会被契约测试捕获；这是测试约束缺口，不改变本次实际上传值，按 P2 处理。

## 熵增核对

本 diff 没有新增抽象、状态、包装层或独立配置项；只新增一个直接的 workflow retention 字段和一个直接读取该字段的契约测试，并同步已有字面量。未发现熵增意见。

## 裁决

本轮存在两个可溯源的 P2 发现；P1 红线与 terminal 时间维度检查均未命中。

本轮审查结论：fail
