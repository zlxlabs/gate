# gate-sha-pins R1 verdict（v2/shadow workflow 官方 action SHA 固化）

- **审查对象**：`a6dbd54898d2d914f42153e0cb75592e86782baf..ddbd74ceb180de33203a264705a410228042db08`（base..H0，冻结）
- **关联 PR**：[#70](https://github.com/zlxlabs/gate/pull/70)（`agent/action-sha-pins`，draft）
- **审查者**：cursor 执行器（独立 review 卡 R1）
- **OCR 前置**：主脑预取 `status=reviewed`、`coverage=complete`、`findings=[]`；本卡未复跑 OCR（卡上已声明扫过且干净）

## 本轮新证据

1. **全量 diff 审读**：4 文件、+67/-28 行；workflow 侧除 `uses:` SHA 替换外仅 1 处注释措辞（`actions/upload-artifact@v4` → `actions/upload-artifact v4`，非 runner 消费字段）。
2. **H0 合同测试实测**：`python3 -m pytest -q tests/test_gate_v2_contract.py tests/test_gate_shadow_v2_contract.py` → **103 passed in 3.21s**（detached HEAD @ `ddbd74c`）。
3. **红验**：base workflow + H0 新增 `test_*_official_actions_are_exactly_sha_pinned` → **2 failed**（期望：base 仍 `@v4`）。
4. **负向探针**（H0 逻辑内联变异）：tag `@v4`、短 SHA、错误 SHA、新增未登记 `actions/labeler@v5` 均使断言失败。
5. **CI 结论**：`gh pr view 70 --json statusCheckRollup` → `test` **SUCCESS**、`actionlint` **SUCCESS**（非 SKIPPED）。
6. **空白/冲突**：`git diff --check a6dbd548..ddbd74c` → 无输出。

## 行为验收（job 语义未变）

对 `gate-v2.yml` / `gate-shadow-v2.yml` 逐项核对：所有 `actions/checkout|cache|upload-artifact|download-artifact` 的 `uses:` 由 `@v4` 改为 40 位 SHA + 行尾 `# v4` 注释；各 step 的 `if:`、`with:`、`continue-on-error:`、`if-no-files-found:`、artifact 名称表达式、matrix/needs 依赖**均未改动**。

| workflow | action | 步数 | 锁定 SHA |
|---|---|---:|---|
| gate-v2.yml | checkout | 6 | `11d5960a326750d5838078e36cf38b85af677262` |
| gate-v2.yml | cache | 1 | `0057852bfaa89a56745cba8c7296529d2fc39830` |
| gate-v2.yml | upload-artifact | 6 | `ea165f8d65b6e75b540449e92b4886f43607fa02` |
| gate-v2.yml | download-artifact | 3 | `d3f86a106a0bac45b974a628896c90dbdf5c8093` |
| gate-shadow-v2.yml | checkout | 1 | `11d5960a326750d5838078e36cf38b85af677262` |
| gate-shadow-v2.yml | upload-artifact | 1 | `ea165f8d65b6e75b540449e92b4886f43607fa02` |
| gate-shadow-v2.yml | download-artifact | 1 | `d3f86a106a0bac45b974a628896c90dbdf5c8093` |

与 issue #389 / gate-hub 锁定清单**完全一致**。`gate-shadow-v2.yml` 无 cache 步（与 base 一致，非遗漏）。

## 合同测试覆盖

| 不变式 | 代码位置 | 测试锁 |
|---|---|---|
| gate-v2 四类官方 action 唯一 SHA 集合 | `EXPECTED_ACTION_REFS` + `test_production_v2_official_actions_are_exactly_sha_pinned` | `tests/test_gate_v2_contract.py` |
| gate-shadow-v2 三类官方 action 唯一 SHA 集合 | 同上（无 cache） | `tests/test_gate_shadow_v2_contract.py` |
| 既有 step 级断言随 pin 更新 | `CHECKOUT_ACTION` / `UPLOAD_ARTIFACT_ACTION` 常量 | 原有用例仍绿（103 passed） |
| tag/短 SHA/错 SHA/新增 actions/* | 聚合断言 `actual == EXPECTED` | 红验 + 内联变异探针 |

测试读取 **实际 workflow YAML 字节**（`_load_workflow()` → `yaml.safe_load(WORKFLOW.read_text())`），非自造映射表单独维护。

## 降层三问

1. **pin 是否在 runner 真正消费的 `uses:` 字段？** 是；GitHub Actions 解析 `uses:` 中的 `@ref`，注释 `# v4` 不影响解析（H0 pytest 已证）。
2. **SHA 是否唯一标识版本？** 是；均为 40 位 hex，与 gate-hub archive resolved commit 一致。
3. **测试保护的是 workflow 字节还是自造映射？** workflow 实文件；期望 SHA 与 workflow 交叉断言，改 workflow 未同步常量会红。

## Findings

### P1

（无）

### P2

（无）

### P3 / backlog

（无）—— 本卡锁定决策明确不要求 SHA 格式校验器、自动同步或 fallback；现有聚合测试已满足 scope。

## 结论

**PASS** — 冻结范围内以最小 diff 将生产 `gate-v2` / `gate-shadow-v2` 四类（shadow 三类）官方 action 固化到约定 archive SHA，未改变 job 行为；合同测试与 CI 均支持该结论。
