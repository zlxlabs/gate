<!-- delegate-outcome: succeeded -->

# Issue #81 tier 契约 — 独立终审 verdict

| 字段 | 值 |
| --- | --- |
| 审查范围（固定 SHA） | `73ac64be2e0b62377695704ac5d9eef9b6827673..caa3604d7622155a090cbcdc73005e947f81db78` |
| Base | `73ac64be2e0b62377695704ac5d9eef9b6827673` |
| Head (H1) | `caa3604d7622155a090cbcdc73005e947f81db78` |
| PR | #82 |
| 风险档 | personal |
| 审查轮次 | 独立终审（全新 review 卡，未读实现方报告） |
| OCR 前置 | `status=reviewed`，1 条 low/test finding（见 backlog） |
| 最终 verdict | **PASS** |

## Scope check

| 检查项 | 结果 |
| --- | --- |
| 变更文件数 | 3（与任务卡一致） |
| 允许文件 | `.github/actions/review-ledger/build_ledger.py` (+5/-1)、`tests/data/primary_review_v2_with_tier.json` (+56)、`tests/test_review_ledger.py` (+66) |
| 禁止文件 | 未触碰 |
| `--numstat` 合计 | 127 insertions / 1 deletion |
| scope drift | **无** |
| 熵增（新增抽象/状态/配置/fallback/catch） | **无** — 生产侧仅扩展 `PRIMARY_IDENTITY_FIELDS` 与 4 行 tier 校验 |

## 验证命令

均在 H1 临时 worktree（`caa3604`）执行：

| 命令 | 结果 |
| --- | --- |
| `uv run --with pytest,PyYAML python -m pytest -q tests/test_review_ledger.py` | **156 passed** |
| `uv run --with pytest,PyYAML python -m pytest -q` | **598 passed** |
| `python3 scripts/check_pinned_uses.py` | **OK** |

### 红验抽查

在 base `73ac64b` 临时 worktree 仅拷入 H1 的 `tests/test_review_ledger.py` 与 `tests/data/primary_review_v2_with_tier.json`（旧 `build_ledger.py` 不动）：

| 测试 | 期望 | 实测 |
| --- | --- | --- |
| `test_review_ledger_consumes_real_primary_v2_tier_artifact_bytes` | 因旧 consumer 拒绝 `tier` 字段而失败 | **FAILED** — `ValueError: invalid canonical primary envelope: extra=['tier']` |

红验通过：真实 producer fixture 用例在 base 上确实因缺少 tier 消费逻辑而变红。

## 不变式核对

### I1 — schema v2 可选 `tier` 被 consumer 接受并保留进 `primary_identity`

| 锚点 | 证据 |
| --- | --- |
| 代码 | `build_ledger.py:61` — `tier` 加入 `PRIMARY_IDENTITY_FIELDS`；`build_ledger.py:70-73` — `tier` 进入 `PRIMARY_ALLOWED_FIELDS`；`build_ledger.py:434` — `{field: audit[field] for field in PRIMARY_IDENTITY_FIELDS if field in audit}` 投影 |
| 测试 | `test_review_ledger_consumes_real_primary_v2_tier_artifact_bytes` — fixture SHA-256 `5f8beb26…` 锁定后断言 `entry["primary_identity"]["tier"] == "internal"` |

**结论：满足**

### I2 — `tier` 只能是 `personal` / `internal` / `saas`；非法值 `ValueError`

| 锚点 | 证据 |
| --- | --- |
| 代码 | `build_ledger.py:292-295` — `"tier" in audit` 时校验 `isinstance(tier, str) and tier in {"personal", "internal", "saas"}`，否则 `ValueError("canonical primary tier must be personal, internal, or saas")` |
| 测试 | `test_primary_v2_tier_rejects_invalid_domain_values` — 参数化 `None`, `""`, `1`, `True`, `"enterprise"` 均匹配 `canonical primary tier` |

**结论：满足**

### I3 — `tier` 仅扩充 allowlist，不进 required；历史无 tier 继续接受

| 锚点 | 证据 |
| --- | --- |
| 代码 | `build_ledger.py:63-67` — `PRIMARY_REQUIRED_IDENTITY_FIELDS` **不含** `tier`；缺 `tier` 时不触发 I2 校验块 |
| 测试 | `test_v2_primary_audit_projects_verdict_and_identity` — 五 verdict（含 `not_expected`/`waived`）在无 `tier` 时仍通过；`test_ledger_consumes_historical_v1_fixture_without_runtime` — v1 历史 fixture 仍消费 |

**结论：满足**

### I4 — no-review verdict 带合法 `tier` 不被错绑到 reviewer-only 语义

| 锚点 | 证据 |
| --- | --- |
| 代码 | tier 校验位于 verdict 分支之前（`build_ledger.py:292-295`），与 reviewer 约束（`build_ledger.py:344-348`）独立 |
| 测试 | `test_primary_v2_tier_accepts_domain_values_for_review_and_no_review` — `verdict=not_expected` × 三 tier 值均保留 `primary_identity["tier"]`；`reviewer` 仍为 `None`（`_v2_audit` 构造） |

**注：** `waived` + tier 无专用参数化，但代码路径与 `not_expected` 相同（tier 校验不依赖 verdict）。现有 `test_v2_primary_audit_projects_verdict_and_identity(waived)` 已覆盖无 tier 的 waived；加 tier 不会触发额外 reviewer 约束。

**结论：满足**

### I5 — 其他未知额外字段仍拒绝

| 锚点 | 证据 |
| --- | --- |
| 代码 | `build_ledger.py:290-291` — `extra = set(audit) - PRIMARY_ALLOWED_FIELDS` |
| 测试 | `test_primary_v2_tier_does_not_allow_other_unknown_fields` — `unexpected_primary_field` 触发 `extra=` 错误 |

**结论：满足**

### I6 — 跨边界 fixture 来自真实 producer bytes，锁来源/摘要/投影

| 锚点 | 证据 |
| --- | --- |
| Fixture | `tests/data/primary_review_v2_with_tier.json` — 来源 run `32446501755` / artifact `9434236632`（常量 `PRIMARY_REVIEW_V2_TIER_SOURCE_URL`） |
| 摘要 | `test_review_ledger_consumes_real_primary_v2_tier_artifact_bytes` — `sha256(fixture_bytes) == 5f8beb2607fd3cf7a79b0adb539d85a68ee189aed463bd08932835a436f3abb8` |
| 投影 | 同测试断言 `entry["primary_identity"]["tier"] == "internal"`（白名单字段，未复述 payload 正文） |

**结论：满足**

### I7 — 不新增 helper/抽象/状态/配置/fallback/防御式 catch

diff 生产侧变更：元组增 1 字段 + 4 行校验。测试侧仅 `import hashlib` 与新增测试函数，无包装层。

**结论：满足**

## Findings

### 阻塞项（P1）

无。

### 非阻塞

| ID | 级别 | 置信度 | 违反不变式 | 摘要 |
| --- | --- | --- | --- | --- |
| B1 | P3 | 6/10 | — (backlog) | OCR 指出 `test_primary_v2_tier_does_not_allow_other_unknown_fields` 经 `extra` 路径即可失败，单独移除 tier 校验块不会使该测试变红；该测试职责仍是 I5（未知字段），tier 校验由 `test_primary_v2_tier_rejects_invalid_domain_values` 覆盖。可选改进：为 `waived`+tier 补一条参数化，增强 I4 显式覆盖。 |

## Backlog（范围外 / 非阻塞）

- B1：测试命名与断言路径解耦（OCR reviewed，未verified）

## 最终判定

七条 producer/consumer 不变式均有明确代码锚点与约束力测试；红验确认真实 producer tier 用例在 base consumer 上失败；scope 无漂移；无 P1/P2 finding。

**PASS**
