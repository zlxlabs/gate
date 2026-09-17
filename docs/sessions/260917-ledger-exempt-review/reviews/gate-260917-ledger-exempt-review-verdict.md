# gate PR #187 独立对抗审查 verdict

**pass-with-findings**

- 审查对象（H0 冻结）：`639e7c205932e61a0cf1e3cfab78f546afd879cf..69ea43b01c518d7d72c531109e319e238581bb21`
- 仓库风险档：personal（P1 红线仅数据丢失 / 静默出错 / 崩溃）
- 改动文件：`.github/workflows/gate-v2.yml`、`tests/test_gate_v2_contract.py`、`docs/sessions/260917-ledger-terminal-exempt/report.md`（实现方会话记录，本轮不采信为推理）
- 总评：豁免 / 未期望评审路径上，ledger 不再因「终态产物 required 写死 True」稳定红；期望评审时缺终态仍 fail-closed；「有陈旧终态 + 本 attempt 聚合器跑过」的既有 fail-closed 仍在。一条 P2：MagicDNS `if` 没对齐 `runs-on` 的 `control_runner != 'github-hosted'`，逃生舱格子会把原本绿的豁免/draft 聚合器打红。P2 不阻塞本轮合并。

## 七条判据

| # | 结论 | 证据 |
|---|------|------|
| 1 | **成立** | H0 `gate-v2.yml:1652-1659`：`terminal_required = expected_text == "true"`；未要求时 `parse_resolve(..., terminal_required)` 返回 `None` 并 `print("::notice::本轮未评审/豁免，无终态产物")`。独立复现场景 B：`rc=0`，stdout 含该 notice。测试锁：`test_gate_v2_contract.py:1478-1492`。 |
| 2 | **成立** | 接线：`terminal-path` 在空 id 时为 `''`（`gate-v2.yml:1821`）；Build 步无 `if`，下载步 `if: terminal_artifact_id != ''`（`:1801-1802`）被跳过不失败，Build 仍跑。`build_ledger.py:646-653,567-570`：空 `terminal-path` 不调用 `load_gate_terminal_envelope`；`not _truthy(codex_expected)` → `fallback="not_applicable"`；`write_ledger` 必写一行。独立探针：`--terminal-path '' --codex-expected false` → `rc=0`，恰好 1 行，`review.status=not_applicable`。 |
| 3 | **成立** | `terminal_required` 在 `expected_text=="true"` 时为真；缺终态 `SystemExit("No matching required gate terminal artifact found")`。场景 A：`review_expected=true`、无终态、`rc=1`，原文即该句。测试：`test_ledger_resolver_refuses_stale_terminal_when_current_attempt_is_missing`（H0 `:1388-1399`，显式 `review_expected="true"`）。 |
| 4 | **成立** | 既有判据针对「本 attempt 聚合器跑过、终态却是更早 attempt 的产物」。H0 `:1739` 仍走 `if terminal_artifact is not None and terminal_artifact[0] != current`。场景 C（豁免 + 陈旧终态 + 聚合器本 attempt 跑过）与 D（期望评审 + 同上）均为 `rc=1`，原文 `Aggregator ran on this attempt but did not produce a terminal artifact`。`test_ledger_resolver_hard_fails_when_aggregator_ran_without_terminal` 未改语义（仍带 attempt-1 终态）。无终态（`None`）+ 豁免变绿见下方对抗节，不构成对本条既有含义的放宽。 |
| 5 | **成立** | 红验（只拷入 H0 测试文件到 base `639e7c2` 临时 worktree）：`test_ledger_resolver_records_not_applicable_when_review_not_expected_without_terminal` 转红。注入确认：拷贝后文件 `:1478` 有该测试；同树 YAML `:1648` 仍是 `True`。失败原文见「红验」。AST 测试 `test_ledger_resolver_python_keeps_terminal_required_tied_to_review_expected`（`:1495-`）锁死 `parse_resolve` 第三参不得再是 Constant True。 |
| 6 | **成立** | 无新 workflow input / job / 枚举 / 配置项。`terminal_required` 是 resolver 内局部变量；`fallback=not_applicable` 是 `build_ledger.py:652-653` 既有分支。未新增抽象层。 |
| 7 | **不成立（有边界）** | 默认路径（`control_runner` 留空）下 fork/hosted 的 MagicDNS 仍关，同仓 self 的非 draft 行为不变。但 `self × control_runner=github-hosted × 同仓 × (draft 或 primary skipped)` 会把原本绿的 `gate / gate` 变红，见 finding P2-1 与矩阵。 |

## 对抗场景

### 期望评审 + 上传失败 → 仍 fail-closed

上传步仍是 `if: always()` + `continue-on-error: true`（H0 `gate-v2.yml:1402-1405`），聚合器 job 自身不因上传失败变红。Ledger 侧 `REVIEW_EXPECTED` 与 primary `if` 同表达式（`:1550` / `:541`）。`review_expected=true` 时 `parse_resolve(..., True)` 在 `TERMINAL_RESOLVE` 为空时直接 `SystemExit`。

独立复现 A：`review_expected=true`，有 ledger-input 与 primary-audit、无 terminal → `rc=1`，stderr 原文：

```
No matching required gate terminal artifact found
```

### 豁免 + 聚合器本 attempt 跑过但没产终态 → 变绿，可接受（非 P1）

H0 把 `if terminal_artifact[0] != current` 改成 `if terminal_artifact is not None and ...`（`:1739`）。豁免且 `TERMINAL_RESOLVE` 为空时 `terminal_artifact is None`，**不再**进入 aggregator_ran 检查。

独立复现 B：`review_expected=false`，`PRIMARY_RESULT=skipped`，jobs 列表含 `gate / gate` 且 `started_at` = `run_started_at`，无终态 → `rc=0`，stdout 原文：

```
::notice::本轮未评审/豁免，无终态产物
```

GITHUB_OUTPUT 仍写出一行字段（`terminal_artifact_id=` 空）。Build 仍会落 `not_applicable` 行（判据 2 探针）。

P1 两问：真实豁免 PR 上聚合器 `if: always()` 确实跑（`gate-v2.yml:1219`）；上传失败时 `gate / gate` 因 `continue-on-error` 本就绿，ledger 曾是唯一红 check。本轮让这条红 check 在「未期望评审」时变绿，并留下 notice + 记账行。未期望评审不是「真故障被吞成假绿」——模型腿本就不该跑；静默出错 / 数据丢失 / 崩溃三条 P1 都不中。**接受为设计内行为，不单开 finding。**

对照：同一豁免条件下若存在 **陈旧** 终态（场景 C），aggregator_ran 仍红。判据 4 的原语义仍在。

## 矩阵：MagicDNS（gate job 步）与行为

轴从 workflow 机械取出：`inputs.runner`（`gate-v2.yml:80`）、`inputs.control_runner`（`:99`）、`head.repo.full_name == github.repository`（primary/MagicDNS/FORK_GUARD）、`pull_request.draft`（primary `if` `:541`）。`classify.review_expected=false` 与 draft 一样让 primary skip，单独在表注里写。

`runs-on` 三元式（gate/ledger，`:1226`）：`runner==self && control_runner!='github-hosted' && 同仓` → self-hosted，否则 `ubuntu-latest`。

MagicDNS：

- 改前：`if: ${{ needs.primary.result != 'skipped' }}`（base `:1253`）
- 改后：`if: ${{ always() && inputs.runner == 'self' && github.event.pull_request.head.repo.full_name == github.repository }}`（H0 `:1257`）

| runner | control_runner | 来源 | draft | 改前 DNS | 改后 DNS | 改前/改后期望（gate job / ledger 终态要求） | 非期望变化 |
|---|---|---|---|---|---|---|---|
| self | default | 同仓 | 否 | 开（primary 跑） | 开 | 终态 required；缺则 ledger 红。不变 | 无 |
| self | default | 同仓 | 是 | 关 | **开** | 改前：上传 fail-open，ledger 因 required=True 红。改后：DNS 开以便产出终态；无终态则 ledger 绿 + notice | 有意（修 #185）。self-hosted 有 tailnet，DNS 应变绿 |
| self | default | fork | 否/是 | 关 | 关 | primary skip；ledger 改后不再因缺终态红 | 有意，ledger 红→绿 |
| self | github-hosted | 同仓 | 否 | 开（job 在 ubuntu-latest） | 开 | 逃生舱上 DNS 本就会因无 `100.100.100.100` 失败（步骤自带该文案） | 非豁免格：存量，非本 diff 引入 |
| self | github-hosted | 同仓 | 是 | **关**（primary skip） | **开**（ubuntu-latest） | 改前：DNS 跳过，上传 fail-open，**gate/gate 绿**。改后：DNS 硬失败（无 continue-on-error），**gate/gate 红** | **非期望** → P2-1 |
| self | github-hosted | fork | 任意 | 关 | 关 | job 本就 ubuntu-latest；fork 守卫关 DNS | 无 |
| hosted | default / github-hosted | 同仓 | 任意 | 关（primary skip） | 关（`runner!='self'`） | ledger 缺终态：改前红、改后绿 | 有意 |
| hosted | * | fork | 任意 | 关 | 关 | 同上 | 有意 |

`classify.review_expected=false` 且非 draft、self、同仓、control 默认：与 draft 行同类（改前 DNS 关 / 改后 DNS 开）——这是本修的主路径，有意。同条件 + `control_runner=github-hosted`：与 draft+hatch 行同类，非期望变红。

## Finding

### P2-1 — hatch 格子上 MagicDNS `if` 与 `runs-on` 不对齐，豁免/draft 的 `gate / gate` 会从绿变红

- **违反**：判据 7（不得把其它组合上原本绿的行为变红）
- **本仓判定**：P2（工具未标此条；审查自检）
- **P1 两问**：
  1. 真实使用会被触发吗？**当前默认路径不会。** 量过 `/home/zlx/projects/personal/*` 各仓主 checkout 的 `.github/workflows/*.yml`：没有任何 caller 传入 `control_runner: github-hosted`；只有 `gate-v2.yml` 输入定义/`runs-on` 三元式，以及 `gate-hub/.github/workflows/gate.yml:83` 注释「逃生舱」。`templates/caller-gate-v2.yml` 显式省略该输入。
  2. 触发了后果能否接受？会：`gate / gate` 是 required check，文档-only / 豁免 PR 在启用逃生舱时会被挡住。不是静默出错、不是丢数据、不是崩溃 → 不是本仓 P1。
- **证据**：H0 `:1257` MagicDNS `if` 不含 `inputs.control_runner != 'github-hosted'`，而同 job `:1226` `runs-on` 含该项。步骤无 `continue-on-error`，失败文案写明 hosted 无 `100.100.100.100`。
- **可执行边界**：MagicDNS `if` 补上与 `runs-on` 相同的 `control_runner != 'github-hosted'`（或等价：只在 self-hosted 标签上跑 DNS）。不改 ledger 的 `terminal_required` 逻辑。本轮接受不修（逃生舱未接入；修 #185 主路径是 default control_runner）。

### P3-1 — 新测试未锁 audit/input 槽（OCR medium → 本仓 P3）

- **工具标注**：OCR MiniMax-M3 `severity=medium` / `unverified`
- **违反**：判据 5 的「约束力」弱相关（断言已能在 base 转红，只是漏了邻接字段）
- **P1 两问**：不会导致假绿门禁；测试缺口。不修。
- **证据**：`test_gate_v2_contract.py:1478-1492` 只断言空 `terminal_*` 与 notice。场景 B 实际还写出 `input_artifact_id=...`、`input_short_circuited=false`、空 `audit_*`。

### P3-2 — AST 扫描只走 `tree.body`（OCR low → 本仓 P3）

- **工具标注**：OCR `severity=low` / `unverified`
- **违反**：无法溯源到七条硬判据（测试稳健性）→ 降为 P3
- **证据**：`test_gate_v2_contract.py:1499` `for node in tree.body`。当前 YAML 抽出的脚本里赋值就在顶层，现约束有效。接受不修。

## 判据 6 / 熵

新增局部变量 `terminal_required`、一条 `::notice::`、下载步 `if`、`terminal-path` 表达式。无新状态机、无新配置、无包装层。会话 `report.md` 为文档，不进入运行时。

## 记账落行（`build_ledger.py`）

空 `terminal-path` + `codex-expected=false`：

1. `:646-647` `terminal_raw` 空 → 不调用 `load_gate_terminal_envelope`（该函数对缺文件 fail-loud，`:380-383`）。
2. `:652-653` `not _truthy(args.codex_expected)` → `fallback = "not_applicable"`（既有枚举，非本 diff 新增）。
3. `:657-676` `build_entry(...)` + `:568-570` `write_ledger` 写恰好一行 JSONL。

独立探针（本审查执行，非测试套件）：`rc=0`，1 行，`review.status=not_applicable`，无 `terminal_source_attempt` 键。

workflow `codex-expected` 与 primary `if` / `REVIEW_EXPECTED` 为同一表达式（`:1822`），豁免时为 false，与 resolver `expected_text` 对齐。

## 红验

临时 worktree：`git worktree add --detach <tmp> 639e7c205932e61a0cf1e3cfab78f546afd879cf`；只覆盖 `tests/test_gate_v2_contract.py`（H0 blob）。注入确认：`sed`/`grep` 见 `:1478` 新测试；同树 YAML `:1648` 仍 `True`。

命令：

```
uv run --python 3.12 --with pytest,PyYAML python -m pytest -q \
  tests/test_gate_v2_contract.py::test_ledger_resolver_records_not_applicable_when_review_not_expected_without_terminal --tb=short
```

原文：

```
F                                                                        [100%]
=================================== FAILURES ===================================
_ test_ledger_resolver_records_not_applicable_when_review_not_expected_without_terminal _
tests/test_gate_v2_contract.py:1488: in test_ledger_resolver_records_not_applicable_when_review_not_expected_without_terminal
    assert result.returncode == 0, combined
E   AssertionError: No matching required gate terminal artifact found
E     
E   assert 1 == 0
E    +  where 1 = CompletedProcess(... returncode=1, stdout='', stderr='No matching required gate terminal artifact found\n').returncode
=========================== short test summary info ============================
FAILED tests/test_gate_v2_contract.py::test_ledger_resolver_records_not_applicable_when_review_not_expected_without_terminal
1 failed in 0.98s
```

worktree 已 `git worktree remove --force` 销毁。

## OCR

`ocr-review --from 639e7c20 --to 69ea43b0 --audience agent` → envelope `status=reviewed`（primary MiniMax-M3，非 skipped）。2 条 finding 均 `verification.unverified`（codex-sub 非零）。落地见 P3-1 / P3-2，不升级。

## 验证命令

`python3 scripts/check_pinned_uses.py` → `OK: checked 9 live workflow/action metadata file(s); all internal uses are workspace-relative`，退出 0。

未跑被审仓全量 pytest（卡面允许最小范围）。本轮只跑红验单测 + 独立 resolver/build_ledger 探针。

## Backlog（不占本轮）

- ledger job 自己的 MagicDNS 仍是 `if: always()`（base `:1521`，本 diff 未改）。`runner=hosted` 时 ledger 跑在 ubuntu-latest，DNS 步可能自己红——存量。
- `tests/` 里没有直接锁 `fallback=not_applicable` 的 `build_ledger` 用例；本轮用独立探针补了一次，未要求本 PR 加测试。
