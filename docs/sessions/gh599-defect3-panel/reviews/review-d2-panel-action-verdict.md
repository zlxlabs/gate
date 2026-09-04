# gh599-defect3-panel review-d2 verdict

## 审查范围与结论

- 固定审查对象：`6c65424..6505511b6ca74763d89a0670dea96100dcac5397`。
- 本轮只审该提交范围；未把范围外存量问题计入 findings。
- 被审仓风险等级：`personal`；P1 红线为数据丢失、静默错误结果、崩溃。
- 本轮唯一仓内产物为本文件；生产代码、测试、配置和其他文档均已还原未改。

结论：`PASS`（没有 P1；记录 2 条 P2 与 1 条 P3，均为不改变门禁判定的可执行缺口，可由主脑分诊是否补修）。

## Findings

### P2-1：真实主审审计没有写入 `reviewable_chars`，带数字文案在生产路径不可达

- 文件:行号：`.github/actions/gate-aggregator/aggregate.py:1081-1094`。
- 触发路径：真实 gate-hub `build_primary_audit()` 生成 `verdict=unavailable`、所有 attempt 为预算耗尽时，canonical record 没有 `coverage`；legacy `review-gate.sh` 的 coverage 也只包含 `mode`、`complete`、`diff_lines`、`shards`，没有 `reviewable_chars`。当前 helper 要求三个字段全部存在，因此实际输入只能走无数字回落。
- 取证：直接调用 gate-hub 当前 `/home/zlx/projects/personal/gate-hub/scripts/review/primary_orchestrator.py` 的 `build_primary_audit()`，输出 `coverage=None`、`coverage_key_present=False`；用 producer-shaped coverage 调用当前聚合器，输出无数字文案。
- 违反项：对应不变式 3 的跨发布 coverage 消费契约/本卡目标“带上规模数字”。安全回落本身没有改变判定，但本 diff 把成功路径绑定到 producer 未提供的字段，导致目标文案在真实环境不会出现。
- 修复方向：明确并锁定跨仓契约：若必须显示字符数，让 gate-hub producer 把已计算的 `reviewable_diff_chars` 写入 canonical `coverage.reviewable_chars`，并以 producer 实际 JSON 字节 fixture 锁死；否则删掉消费者对该不存在字段的硬依赖，按真实 producer 字段渲染可用数字。不要让两边继续靠复制常量/隐含字段协作。

### P2-2：`shards >= 1` 没有回归断言，删除该条件仍全绿

- 文件:行号：`tests/test_gate_aggregator.py:2275-2315`；对应实现为 `.github/actions/gate-aggregator/aggregate.py:1083-1087`。
- 触发输入：预算耗尽且 `coverage={"diff_lines":401,"reviewable_chars":12003,"shards":0}`。当前实现应回落无数字文案，不能渲染“需 0 个审查分片”。
- 红验：临时将 `coverage[field] >= (1 if field == "shards" else 0)` 改为 `coverage[field] >= 0`，现有预算测试仍为 `5 passed, 221 deselected`；因此测试没有锁住该条件。
- 违反项：不变式 3 的 coverage 无效/不完整时安全回落要求，以及卡面锁定的 `shards >= 1` 条件。
- 修复方向：在现有预算参数化中加入 `shards=0`（并至少加入 producer-shaped coverage 缺 `reviewable_chars`）的实际渲染断言，断言完整动作句等于无数字拆分文案；保留 `shards=0` 的红验。

### P3-1：`row.get("audit")` 是没有生产者的新增 fallback 支路

- 文件:行号：`.github/actions/gate-aggregator/aggregate.py:1100-1104`。
- 触发路径：`_panel_action()` 的新增代码在 `primary_audit` 为空时尝试读取 `row["audit"]`；但当前 `_terminal_row()`（:1394 起）、`_panel_current_row()`（:1959 起）和 `_parse_panel_history()` 都不产生该字段，真实面板 row 只有持久化终态字段，故该支路在当前生产路径不可达。
- 违反项：不变式 6（不得新增 fallback、不得新增没有第二消费者的抽象）及熵增审查要求。
- 修复方向：只消费已验证并由 `_publish_only()` 写入的 `row.get("primary_audit")`；删除 `or row.get("audit")`。若将来真的要支持另一种 row 契约，应先把 producer、schema 和回归测试一并落地，而不是保留无契约 fallback。

## 角度 1：不变式 1 的机械核对

实际命令：

```text
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
# 内联脚本分别从工作树加载 HEAD、从
# git show 6c65424:.github/actions/gate-aggregator/aggregate.py 加载 BASE，
# 对 with coverage / without coverage / mixed failure 三个 audit 调用
# evaluate() 与 render_status_panel()，打印两侧原文和三元组。
PY
```

实际输出（`BASE` 为基线，`HEAD` 为固定 H0）：

```text
=== with coverage ===
BASE tuple: ('unavailable', 'review_unavailable', 'primary_unavailable')
HEAD tuple: ('unavailable', 'review_unavailable', 'primary_unavailable')
BASE panel:
当前状态：**unavailable** · **修基础设施**
当前裁决：`review_unavailable` / `primary_unavailable`
HEAD panel:
当前状态：**unavailable** · **本 PR 规模超出单次评审预算（401 行 / 12003 字符，需 3 个审查分片），本次未能评审完。请拆成更小的增量 PR 后重试。**
当前裁决：`review_unavailable` / `primary_unavailable`

=== without coverage ===
BASE tuple: ('unavailable', 'review_unavailable', 'primary_unavailable')
HEAD tuple: ('unavailable', 'review_unavailable', 'primary_unavailable')
BASE panel:
当前状态：**unavailable** · **修基础设施**
当前裁决：`review_unavailable` / `primary_unavailable`
HEAD panel:
当前状态：**unavailable** · **本 PR 规模超出单次评审预算，本次未能评审完。请拆成更小的增量 PR 后重试。**
当前裁决：`review_unavailable` / `primary_unavailable`

=== mixed failure ===
BASE tuple: ('unavailable', 'review_unavailable', 'primary_unavailable')
HEAD tuple: ('unavailable', 'review_unavailable', 'primary_unavailable')
BASE panel:
当前状态：**unavailable** · **修基础设施**
当前裁决：`review_unavailable` / `primary_unavailable`
HEAD panel:
当前状态：**unavailable** · **修基础设施**
当前裁决：`review_unavailable` / `primary_unavailable`
```

结论：`evaluate()` 的 `gate_result` / `classification` / `reason_code` 三元组和调用时序未因文案改变；`build_terminal_envelope()`、`synthetic_primary` 和判定函数签名均未改。

## 角度 2：反向穷举

实际命令：

```text
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
# 内联脚本对当前 aggregate.py 构造 8 种 audit，调用
# render_status_panel([row])，打印以“当前状态：”开头的实际行。
PY
```

实际输出：

| 形态 | 实际渲染文字 | 是否回落 | 结论 |
|---|---|---:|---|
| `attempts` 为空 | `当前状态：**unavailable** · **修基础设施**` | 是 | 正确拒绝 |
| 单腿非预算失败 | `当前状态：**unavailable** · **修基础设施**` | 是 | 正确拒绝 |
| `exit_code=True`（bool 冒充 22） | `当前状态：**unavailable** · **修基础设施**` | 是 | `_is_strict_int` 正确拒绝 |
| `exit_code="22"`（字符串） | `当前状态：**unavailable** · **修基础设施**` | 是 | 严格类型正确拒绝 |
| `reason` 多一个空格 | `当前状态：**unavailable** · **修基础设施**` | 是 | 精确匹配正确拒绝 |
| `reason` 少一个字 | `当前状态：**unavailable** · **修基础设施**` | 是 | 精确匹配正确拒绝 |
| `attempts` 混入非 dict 元素 | `当前状态：**unavailable** · **修基础设施**` | 是 | 全称判断正确拒绝 |
| `verdict` 不是 `unavailable` | `当前状态：**unavailable** · **修基础设施**` | 是 | 顶层 verdict 守卫正确拒绝 |

## 角度 3：新增读审计路径健壮性

实际命令：

```text
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
# 内联脚本用 tempfile.TemporaryDirectory() 构造目录不存在、空目录、
# 两个 .json、非法 JSON、合法 JSON 非 object 五种输入，调用
# _read_audit_file() + evaluate()；随后用同一终态制品和 monkeypatch 的
# _post_status_panel_fail_open() 调用 _publish_only()，验证真实发布读取路径。
PY
```

直接读取与判定输出：

```text
目录不存在: exception=None; audit_type=NoneType; error='audit directory not present (download step likely found no artifact)'; triple=('unavailable', 'integration_error', 'audit_missing')
目录为空: exception=None; audit_type=NoneType; error='no *.json file found under .../empty'; triple=('unavailable', 'integration_error', 'audit_missing')
两个 .json: exception=None; audit_type=NoneType; error='expected exactly one audit file under .../two, found 2: a.json, b.json'; triple=('unavailable', 'integration_error', 'audit_missing')
非法 JSON: exception=None; audit_type=NoneType; error='could not parse audit.json: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'; triple=('unavailable', 'integration_error', 'audit_missing')
合法 JSON 但非 object: exception=None; audit_type=list; error=None; triple=('unavailable', 'integration_error', 'audit_invalid')
```

真实发布路径输出：

```text
args.audit_dir: return=0; primary_audit_attached=True
当前状态：**unavailable** · **本 PR 规模超出单次评审预算（401 行 / 12003 字符，需 3 个审查分片），本次未能评审完。请拆成更小的增量 PR 后重试。**
RUNNER_TEMP/primary-audit: return=0; primary_audit_attached=True
当前状态：**unavailable** · **本 PR 规模超出单次评审预算（401 行 / 12003 字符，需 3 个审查分片），本次未能评审完。请拆成更小的增量 PR 后重试。**
```

五种异常的实际 `_publish_only()` 输出也全部为 `exception=None; return=0; 当前状态：**unavailable** · **修基础设施**`。结论：目录/文件异常不抛异常，也没有改变既有终态判定。

## 角度 4：`not validate_audit_identity(...)` 守卫方向

实际命令：

```text
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
# 内联脚本分别调用 validate_audit_identity(valid, identity) 和
# validate_audit_identity(invalid, identity)，打印 errors 与 not errors。
PY
```

实际输出：

```text
合法记录: errors=[]; not_errors=True
非法记录: errors=["identity mismatch on 'head_sha': audit='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' expected='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]"; not_errors=False
```

结论：方向正确。空错误列表代表合法，`not []` 为真并采纳；非空错误列表代表非法，`not errors` 为假并拒绝。OCR 的 critical 判断为误报。

## 角度 5：常量复制漂移风险

实际命令与输出：

```text
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
# 用 ast.literal_eval 读取 gate-hub/scripts/review/job_budget.py 的
# BUDGET_EXHAUSTED_REASON 与当前 aggregate.py 的对应常量并比较。
PY
gate-hub source line: BUDGET_EXHAUSTED_REASON = "评审总预算已耗尽，保留收尾空间"
gate source line: PRIMARY_BUDGET_EXHAUSTED_REASON = "评审总预算已耗尽，保留收尾空间"
gate-hub BUDGET_EXHAUSTED_REASON='评审总预算已耗尽，保留收尾空间'
gate PRIMARY_BUDGET_EXHAUSTED_REASON='评审总预算已耗尽，保留收尾空间'
exact_match=True
```

若 gate-hub 将来只修改那句 reason，而 gate 仍保留旧副本，严格精确匹配会使真实预算腿全部不再命中，方向是安全回落到「修基础设施」，不会误触发“PR 太大”。代价是目标文案丢失，并且漂移只有在跨仓联调或生产样本出现时才暴露。更小的发现办法是在 gate-hub producer 侧增加一个固定 reason 的契约测试，并在 gate 侧保留 producer-shaped fixture；如果需要自动发现跨仓漂移，应让发布检查读取同一份版本化契约，而不是继续手工复制字符串。不需要引入第二个 checkout 才能先把 producer 侧改动锁成显式失败。

## 角度 6：`shards >= 1`

gate-hub 当前 `review-gate.sh` 默认 `shard_count=1`；分片构建失败路径会显式写 `shard_count=0`。实际命令：

```text
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
# 构造 attempts 全预算耗尽、coverage.diff_lines=401、
# reviewable_chars=12003、shards=0，调用 _primary_budget_exhausted_action。
PY
coverage={'diff_lines': 401, 'reviewable_chars': 12003, 'shards': 0}
rendered='本 PR 规模超出单次评审预算，本次未能评审完。请拆成更小的增量 PR 后重试。'
```

判定：这是期望的安全行为，不单列行为 finding。`shards=0` 表示分片覆盖计数不可用；渲染“需 0 个审查分片”会产生明显错误的数字，因此宁可回落无数字动作句。缺陷在于 P2-2 的测试没有锁住这个期望，而不是当前实现条件本身错误。

## 角度 7：红验抽查

红验前工作树干净。先确认注入位置后，实际执行：

```text
sed -n '1068,1080p' .github/actions/gate-aggregator/aggregate.py
# 输出包含：if not any(
uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_aggregator.py -k budget_exhaustion
# 1 failed, 4 passed, 221 deselected in 0.22s
# 唯一失败：mixed-failure
```

还原 `all` 后，临时把 `shards >= 1` 改成 `shards >= 0`，先确认位置：

```text
sed -n '1080,1094p' .github/actions/gate-aggregator/aggregate.py
# 输出包含：and coverage[field] >= 0
uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_aggregator.py -k budget_exhaustion
# 5 passed, 221 deselected in 0.06s
```

这次未变红就是 P2-2 的直接证据：没有对应的 `shards=0` 断言。随后恢复为 `all` 和 `coverage[field] >= (1 if field == "shards" else 0)`，清理所有工作树内 `__pycache__`，再跑正常版本，结果 `5 passed, 221 deselected in 0.25s`。

## 角度 8：熵增与调用点

- `_primary_budget_exhausted_action` 有 2 个真实生产调用点：`_action_sentence:989`（Step Summary）和 `_panel_action:1102`（状态面板）。这是两个不同的渲染出口，不能合并成一个调用而不重新引入跨层状态或改变接口。
- 两处外层守卫语义相同，均先限制 `unavailable + review_unavailable + primary_unavailable`，但输入类型不同：一处是 `Outcome`，一处是面板 row。helper 内部再统一检查 primary audit 的 `verdict`、attempts 与 coverage，重复是边界适配，不是第三份判定逻辑。
- 两个新增常量被 helper 的生产比较和测试 fixture 使用；`primary_audit` 同时流入 Summary 与 panel 发布路径；没有新增 verdict、状态或判定函数。
- `row.get("audit")` 没有真实生产者，是 P3-1 所列的多余 fallback；`primary_audit` 才是当前发布路径唯一实际附着的审计投影。

## OCR 前置与测试总览

OCR 前置实际返回 `status=reviewed`、`profile=minimax`、`coverage=complete`，4 条候选中 1 条已被复核器 refuted，3 条因复核器超时未验证。负数 coverage 候选与当前 `>= 0` 校验矛盾，已独立排除；其余候选未直接作为结论，P3-1 是本次独立代码路径核对后的分诊结果。

实际测试：

```text
PYTHONDONTWRITEBYTECODE=1 uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_aggregator.py -k budget_exhaustion
5 passed, 221 deselected in 0.25s

PYTHONDONTWRITEBYTECODE=1 uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q
822 passed in 33.78s
```

无 P1 finding，因此不存在需要执行 P1 两问的候选；P2-1 的真实 producer 路径已通过 gate-hub 当前代码和实际 `build_primary_audit()` 调用量过，后果是文案缺数字，不涉及 gate_result/classification/reason_code、数据丢失、崩溃或合并放行。
