<!-- delegate-outcome: succeeded -->
## 机理核对

主脑第 3 步**成立**。agent-config 失败 run `35183978473` 对本 token 已 404，改用 issue #185 评论里同形状的第二出处：`zlxlabs/key-proxy` run `35188009171`（`classify_pr_paths` 打出 `review_expected=false`，`primary` skipped，`gate / gate` success，`gate / ledger` failure）。

API 步骤结论（job `105094823913`）：

- `#3 skipped  Resolve Silo hostname via MagicDNS`
- `#8 success  Upload gate terminal envelope`（`continue-on-error: true`）
- `#9 success  Retry upload gate terminal envelope`（同上）
- job conclusion：`success`

gate 作业日志原文：

```
2026-09-17T06:04:37.2067358Z ##[warning]Gate terminal envelope upload failed after retry; ledger may lack terminal input
```

ledger 作业日志原文（job `105095044426`）：

```
2026-09-17T06:05:14.9471689Z   REVIEW_EXPECTED: false
2026-09-17T06:05:14.9472874Z   PRIMARY_RESULT_RAW: skipped
2026-09-17T06:05:14.9473260Z   PRIMARY_RESULT: skipped
2026-09-17T06:05:21.5963349Z No matching Silo artifact for prefix 'gate-terminal-v1-1190228685-b44f323113219fe7e0e8efea9be9abe073692efe-35188009171-' (attempt <= 1)
2026-09-17T06:05:21.7806256Z No matching required gate terminal artifact found
2026-09-17T06:05:21.7887352Z ##[error]Process completed with exit code 1.
```

链路：MagicDNS 因 `needs.primary.result != 'skipped'` 被 skip → `silo_store.py put` 没有 `/etc/hosts` 条目 → 上传失败被 `continue-on-error` 吞掉、只留 warning → `gate` 仍 success → ledger `parse_resolve(..., True)` 找不到终态 → exit 1。

## 选定修法

**锁定决策 1 为主，并补上豁免路径缺终态时的显式记账。**

理由：

1. `Upload gate terminal envelope` 本来就是 `if: always()`，聚合器在豁免路径也会写出 `expected_skip` / `review_not_expected` 终态信封。把 MagicDNS 绑在 `primary != skipped` 上，是让「终态必须产出」变成非法状态，不是有意的「豁免路径不连 Silo」。
2. 裸 `if: always()` 会在 hosted 上让 **required** 的 `gate` 作业因没有 `100.100.100.100` 而红。所以 MagicDNS 改成自建同仓才跑：`${{ always() && inputs.runner == 'self' && github.event.pull_request.head.repo.full_name == github.repository }}`。fork/hosted 仍不连 Silo。
3. hosted / 上传 `continue-on-error` 仍失败时，若 `required` 继续无条件 True，ledger 还是红、账本还是丢行。因此 `review_expected=false` 且无终态时 resolver 退出 0，并打印 `::notice::本轮未评审/豁免，无终态产物`；`review_expected=true` 缺终态、以及「聚合器本 attempt 跑过却没产终态」仍 fail-closed。不是静默跳过。

未改 `primary` / `quality` / `ocr`，未动 `v2` 标签 / runner group，未新增枚举或配置项。

## 豁免路径实际会记哪一行

resolver stdout 原文示例（红验 1 的真实执行也打出了同一行）：

```
::notice::本轮未评审/豁免，无终态产物
```

GITHUB_OUTPUT：`terminal_artifact_id=` 与 `terminal_source_attempt=` 为空。随后 download 因 `terminal_artifact_id != ''` 跳过；Build 把 `terminal-path` 置空，走已有的 `codex-expected=false → fallback=not_applicable` 落账。自建同仓豁免且上传成功时，走真实终态信封，不走这条 notice。

## git 产物

```
2d92910 ci(gate-v2): keep ledger green on review-exempt PRs without dropping the row
```

`git show --stat --format= HEAD`：

```
 .github/workflows/gate-v2.yml  | 23 +++++++++---
 tests/test_gate_v2_contract.py | 85 +++++++++++++++++++++++++++++++++++++-----
 2 files changed, 93 insertions(+), 15 deletions(-)
```

相对 `origin/main` 另含本报告文件。diff 行数 108（93+/15-），超过 target 80、低于 hard 260。多出来的主要是契约测试（执行 + AST），不是新抽象。

`git show 2d92910` 的实现要点：

- gate MagicDNS `if`：自建同仓 `always()`，不再看 `primary != skipped`
- `terminal_required = expected_text == "true"`；缺终态且未期望评审时 `print("::notice::本轮未评审/豁免，无终态产物")`
- download 终态加 `if: terminal_artifact_id != ''`；Build 的 `terminal-path` 在空 id 时为 `''`

## 测试输出末行

```
1030 passed in 37.41s
```

`python3 scripts/check_pinned_uses.py`：`OK: checked 9 live workflow/action metadata file(s); all internal uses are workspace-relative`

## 红验

红验前 commit：`2d92910464510d96ef55bba1f0881103a850e44c`。每次只改坏一处，验完 `git checkout --` 还原。两次都是断言失败。

### ① 把新增豁免断言改回「要求终态产物」

变异：`test_ledger_resolver_records_not_applicable_when_review_not_expected_without_terminal` 改为 `assert result.returncode != 0` 且要求 `No matching required gate terminal artifact found`。

```
>       assert result.returncode != 0
E       AssertionError: assert 0 != 0
E        +  where 0 = CompletedProcess(..., returncode=0, stdout='::notice::本轮未评审/豁免，无终态产物\n', stderr='').returncode
FAILED tests/test_gate_v2_contract.py::test_ledger_resolver_records_not_applicable_when_review_not_expected_without_terminal
```

### ② 把 gate MagicDNS gating 恢复成 `needs.primary.result != 'skipped'`

变异：仅 gate 作业那一步的 `if` 改回旧值。

```
>       assert gate_dns["if"] == (
            "${{ always() && inputs.runner == 'self' && "
            "github.event.pull_request.head.repo.full_name == github.repository }}"
        )
E       assert "${{ needs.pr... 'skipped' }}" == '${{ always()...repository }}'
E         - ${{ always() && inputs.runner == 'self' && github.event.pull_request.head.repo.full_name == github.repository }}
E         + ${{ needs.primary.result != 'skipped' }}
FAILED tests/test_gate_v2_contract.py::test_silo_touching_jobs_resolve_magicdns_before_s3
```

## E2E-Assertion

豁免路径下 `Resolve v2 ledger artifacts` 退出 0 且写出一行「本轮未评审/豁免，无终态产物」；正常路径缺终态、以及聚合器本 attempt 跑过却没产终态，仍 fail-closed。

## P2-1：MagicDNS `if` 对齐 `runs-on` 的 `control_runner`

审查 P2：`gate` 作业 MagicDNS 的 `if` 漏了 `inputs.control_runner != 'github-hosted'`，与同 job `runs-on` 三元式的 self-hosted 条件不对齐。`runner=self` + `control_runner=github-hosted` 时 job 跑在 `ubuntu-latest`，改动前 MagicDNS 因 `primary` skipped 被跳过（required 绿），改动后会硬失败（required 红）。

修法：只给 gate MagicDNS 补这一项，ledger MagicDNS 仍是存量 `if: always()`。新 `if`：

```
${{ always() && inputs.runner == 'self' && inputs.control_runner != 'github-hosted' && github.event.pull_request.head.repo.full_name == github.repository }}
```

新对齐断言原文（从 `runs-on` 抽出 `&& fromJSON` 前的 self-hosted 条件，再对 `RUNNER_GUARD` / `CONTROL_RUNNER_GUARD` 做子串包含）：

```
    gate_runs_on = str(raw["jobs"]["gate"]["runs-on"])
    self_hosted_cond = gate_runs_on.split("&& fromJSON", 1)[0]
    for token in (RUNNER_GUARD, CONTROL_RUNNER_GUARD):
        assert token in self_hosted_cond, token
        assert token in gate_dns["if"], token
```

红验：把 gate MagicDNS `if` 改回不带 `control_runner` 的版本。对齐断言转红（断言失败，不是导入错误）：

```
        for token in (RUNNER_GUARD, CONTROL_RUNNER_GUARD):
            assert token in self_hosted_cond, token
>           assert token in gate_dns["if"], token
E           AssertionError: inputs.control_runner != 'github-hosted'
E           assert "inputs.control_runner != 'github-hosted'" in "${{ always() && inputs.runner == 'self' && github.event.pull_request.head.repo.full_name == github.repository }}"
FAILED tests/test_gate_v2_contract.py::test_silo_touching_jobs_resolve_magicdns_before_s3
```

修复 commit：`425c8e89adc30facebe2b669dad897b66971a9c0`。红验后已还原，未改历史。

## 回执

- 主脑第 3 步已用 key-proxy run 35188009171 的真实 warning 原文证实 [output: 2026-09-17T06:04:37.2067358Z ##[warning]Gate terminal envelope upload failed after retry; ledger may lack terminal input]
- 选定锁定决策 1：自建同仓豁免路径仍解析 Silo 并上传终态；required 在评审期望为 true 时保持无条件 [file: .github/workflows/gate-v2.yml:1257]
- 豁免且无终态时写出 `::notice::本轮未评审/豁免，无终态产物`，resolver 退出 0 [file: .github/workflows/gate-v2.yml:1659]
- `_run_ledger_resolver(review_expected="false", 无 terminal)` 断言 returncode == 0 且含该记账行 [file: tests/test_gate_v2_contract.py:1478]
- `test_ledger_resolver_hard_fails_when_aggregator_ran_without_terminal` 保持绿 [file: tests/test_gate_v2_contract.py:1460]
- 终态 `parse_resolve` 第三参不得再写死 Constant True（抽出 resolver 后 AST 断言） [file: tests/test_gate_v2_contract.py:1495]
- 仓级 Verify-Command 末行 [output: 1030 passed in 37.41s]
- `check_pinned_uses.py` 退出 0 [output: OK: checked 9 live workflow/action metadata file(s); all internal uses are workspace-relative]
- 红验 ① 断言失败原文见上节 [file: tests/test_gate_v2_contract.py:1488]
- 红验 ② 断言失败原文见上节 [file: tests/test_gate_v2_contract.py:406]
- 实现 commit [commit: 2d92910464510d96ef55bba1f0881103a850e44c]
- P2-1：gate MagicDNS `if` 补上 `inputs.control_runner != 'github-hosted'`，与同 job `runs-on` 对齐 [file: .github/workflows/gate-v2.yml:1257]
- P2-1：从 `runs-on` 抽出 self-hosted 条件，对 `CONTROL_RUNNER_GUARD` 做子串包含锁死 [file: tests/test_gate_v2_contract.py:409]
- P2-1 红验：去掉 `control_runner` 后对齐断言失败原文见上节 [file: tests/test_gate_v2_contract.py:411]
- P2-1 修复 commit [commit: 425c8e89adc30facebe2b669dad897b66971a9c0]
- P2 复跑仓级 Verify-Command 末行 [output: 1030 passed in 78.37s (0:01:18)]
- P2 复跑 check_pinned_uses.py [output: OK: checked 9 live workflow/action metadata file(s); all internal uses are workspace-relative]
- 合并须 merge commit，禁 squash / rebase（workflow SHA 被下游 pin）
