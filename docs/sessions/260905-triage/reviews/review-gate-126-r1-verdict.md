# gate PR #126 独立审查 verdict

## 结论

**pass（非阻塞）**。固定 H0 的三处 resolver 在 `set -euo pipefail` 下实测能重试、能正确捕获 `gh` / `timeout` 退出码、成功后短路，并对耗尽与成功无匹配分别 fail-closed；未发现 blocker/major 或 personal 档 P1。F-1、F-2 是新增契约测试的 minor 质量缺口，建议记 backlog，不改变本轮实现通过结论。

## 审查范围与判级

- 固定对象：`9015cdf384fb3176f8b1c99e00afa3d51a61d79d..351669627c986665139556dcd9d8cb0aa4d8509e`。
- 不看分支名和 H0 之后的提交；本仓风险等级为 `personal`。
- 本轮只审三处 `Resolve numeric job id for REVIEW_JOB_ID`：`gate-v2.yml` 的 `primary`、`ocr`，以及 `gate-shadow-v2.yml` 的 shadow matrix。
- 结论中的实测指真实本地 shell / 测试环境；另对当前 PR 的 GitHub 运行状态做了只读测量。

PR 与上游修复单的证据命令已执行：PR #126 输出为 `fix(gate-v2/shadow-v2): REVIEW_JOB_ID 解析加有界重试，查询失败与查到为空拆成两态 (#125)`、`state=DRAFT`、`additions=175`、`deletions=13`；issue #125 输出为 `OPEN`，正文给出的实际故障日志是 `Get "https://api.github.com/.../jobs?per_page=100": EOF` 和 `Process completed with exit code 1`。

## 本轮实测了什么

### 固定对象、spec 与仓库测试

```text
$ git diff --stat 9015cdf384fb3176f8b1c99e00afa3d51a61d79d..351669627c986665139556dcd9d8cb0aa4d8509e
 .github/workflows/gate-shadow-v2.yml  | 21 +++++++++---
 .github/workflows/gate-v2.yml         | 47 +++++++++++++++++++++-----
 tests/test_gate_shadow_v2_contract.py | 58 ++++++++++++++++++++++++++++++++
 tests/test_gate_v2_contract.py        | 62 ++++++++++++++++++++++++++++++++++-
 4 files changed, 175 insertions(+), 13 deletions(-)
```

```text
$ uv run --with pytest,PyYAML python -m pytest -q tests/test_gate_v2_contract.py -k 'job_id_resolution or resolve_jobs_api_failure_probe or no_literal_gha_expression'
.......                                                                  [100%]
7 passed, 112 deselected in 3.56s

$ uv run --with pytest,PyYAML python -m pytest -q tests/test_gate_shadow_v2_contract.py -k 'job_id_resolution or resolve_jobs_api_failure_probe or no_literal_gha_expression'
.....                                                                    [100%]
5 passed, 78 deselected in 3.39s

$ uv run --with pytest,PyYAML python -m pytest -q tests/test_no_literal_gha_expression_in_run_blocks.py
...                                                                      [100%]
3 passed in 0.22s

$ git diff --check 9015cdf384fb3176f8b1c99e00afa3d51a61d79d..351669627c986665139556dcd9d8cb0aa4d8509e
# 无输出，退出码 0
```

```text
$ uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q
........................................................................ [ 94%]
..........................................                               [100%]
834 passed in 20.18s

$ python3 scripts/check_pinned_uses.py
OK: checked 8 live workflow/action metadata file(s); all internal uses are workspace-relative
```

### `set -euo pipefail`、重试和两条错误路径

从 H0 的三个实际 `run:` block 中替换掉 GHA 上下文后，用临时 `gh` / `sleep` stub 执行；临时目录在命令退出时清理。输出如下：

```text
--- primary transient success ---
exit=0 calls=3 sleeps=1,2, output=job_id=123

--- primary exhausted ---
exit=1 calls=3 sleeps=1,2,
::error::could not resolve a numeric job id: Jobs API call failed after 3 attempts (exit=42)

--- primary successful empty ---
exit=1 calls=1 sleeps=
::error::could not resolve a numeric job id: Jobs API succeeded but returned no matching job for 'primary'

--- shadow successful no-match ---
exit=1 calls=1 sleeps=
::error::resolve-job-id: Jobs API succeeded but no matching job for JOB_NAME_SUFFIX='shadow (codex)' (candidate job names (0): )

--- shadow exhausted ---
exit=1 calls=3 sleeps=1,2,
::error::resolve-job-id: Jobs API call failed after 3 attempts for repos/owner/repo/actions/runs/1/attempts/1/jobs (exit=42)
```

这组实测证明失败码由 `gh` / `timeout` 通过 `|| rc=$?` 捕获，成功后会 `break`，耗尽后不会 fail-open；当前 H0 的 OCR wrapper 也保持单层 `timeout --foreground "${OCR_GITHUB_TIMEOUT_SECONDS}s"`，三次失败的理论上界为 `15+1+15+2+15=48s`，远小于 15 分钟 job 上限。

首行 shell 参数展开的四种形态实测输出：

```text
empty first='' raw=''
single first=123 raw=123
multi first=123 raw=$'123\n456'
trailing first=123 raw=$'123\n'
```

### 变异验证与真实 GitHub 状态

先在临时的 H0 archive 中确认 primary 的 `break` 位于 `if [ "$rc" -eq 0 ]; then`，再删除该行；没有改当前工作树。变异后的输出为：

```text
.......                                                                  [100%]
7 passed, 112 deselected in 3.48s
mutated-test-exit=0
```

也就是说，当前新增契约测试没有锁住“成功即停止重试”；H0 实现本身仍保留该 `break`。

当前 PR 的只读线上测量：

```text
$ gh pr view 126 -R zlxlabs/gate --json headRefOid,statusCheckRollup --jq '{head:.headRefOid, checks:[.statusCheckRollup[] | {name:.name,status:.status,conclusion:.conclusion}]}'
{"checks":[{"conclusion":"SUCCESS","name":"test","status":"COMPLETED"},{"conclusion":"SUCCESS","name":"actionlint","status":"COMPLETED"}],"head":"351669627c986665139556dcd9d8cb0aa4d8509e"}

$ gh run list -R zlxlabs/gate --workflow gate-v2.yml --limit 5 --json databaseId,headSha,status,conclusion,event --jq '[.[] | {databaseId,headSha,status,conclusion,event}]'
[]
```

当前 PR 没有 `gate-v2.yml` 实际运行记录，因此没有把测试契约缺口升级成“真实生产已触发”的 P1；线上可见的当前检查是 `test` 与 `actionlint`。

### OCR 前置扫描

使用 `ocr-review` 包装器、摘要背景文件和同一固定 SHA 范围运行；返回 envelope 为 `status=reviewed`、`profile=minimax`、`model=MiniMax-M3`、`cli_status=complete`、`coverage=complete`，共 11 条候选，复核结果 `confirmed=5/refuted=5/unverifiable=1`。其中确认的重复测试意见与本 verdict 的 F-1 一致；其余确认项要么是未来修改建议，要么指向不属于本 PR 三处 REVIEW_JOB_ID resolver 的 shadow summary API，均未直接纳入本轮结论。

## 初步 findings

### F-1 — minor：新增 fail-closed 契约用例是严格重复，熵 +1

- 违反/关联 spec：spec 1、3（有界重试与 fail-closed）的测试契约；反熵要求。
- 证据：`tests/test_gate_v2_contract.py:419-437` 已断言 `for attempt`、`rc` 非零分支、`exit 1`、禁止无限循环；`tests/test_gate_v2_contract.py:464-477` 的五条断言全部是前者的严格子集。shadow 对应为 `tests/test_gate_shadow_v2_contract.py:622-639` 与 `:665-677`。
- 结论：这不改变 H0 的运行行为，但新用例没有增加任何不变式，只增加重复采集和 CI 熵；应合并或删除，记非阻塞 backlog 即可。

### F-2 — minor：新增契约测试未锁住成功分支的短路行为

- 违反/关联 spec：spec 1 的“有界重试”不变式；重试应只在失败后继续，成功应停止。
- 证据：三个 resolver 当前分别在 `.github/workflows/gate-v2.yml:491`、`:694`、`.github/workflows/gate-shadow-v2.yml:441` 的 `rc=0` 分支执行 `break`；但新增测试只检查 `max_attempts`、循环、超时、`rc` 捕获、退避和失败分支，未断言 `break`。
- 实测结论：临时 archive 删除 primary 的 `break` 后，相关新增/既有 resolver 测试仍为 `7 passed, 112 deselected`；注入前先打印并确认了目标行，注入后只改临时 archive，当前 H0 未改。
- 影响：未来若成功后仍继续打两次 API 请求，后续一次瞬时失败可把已经成功的解析覆盖成最终失败，且无测试阻止；这是测试约束缺口，不是当前 H0 已发生的运行缺陷，判 minor，不阻塞合并。

## P1 两问记录

本轮没有 P1 finding。两条意见都逐条按 personal 档执行了两问：

- F-1：问题对象是重复测试，不是生产路径。真实 GitHub 测量显示当前 PR 只有 `test` / `actionlint`，`gh run list --workflow gate-v2.yml` 为空；因此该缺陷在真实运行中不会被触发。后果只是测试熵和维护成本，可接受，判 minor。
- F-2：真实 H0 代码保留 `break`，当前线上同样没有 `gate-v2.yml` 运行记录；删除 `break` 只在临时 archive 变异中发生，未进入被审提交，所以当前缺陷不会被真实使用触发。若未来实现真的丢失 `break`，实测代码会继续打 API，后续失败可把成功解析变成失败；该后果本身不可接受，但第一问在当前真实环境不成立，故不能判 P1，保留为 minor 测试缺口。

## spec 条目逐条对照

| 条目 | 结论 | 证据 |
|---|---|---|
| 1. 三处固定 3 次尝试、显式超时、指数退避 | 已验证 | `gate-v2.yml:483-498`（primary）、`:674-701`（OCR，`gh_api` 包装在 `:674-675`）、`gate-shadow-v2.yml:434-448`（shadow）；实测调用次数为 3，退避为 1/2 秒。 |
| 2. 耗尽与成功无匹配为不同 `::error::`，均 `exit 1` | 已验证 | primary `gate-v2.yml:499-507`、OCR `:702-725`、shadow `gate-shadow-v2.yml:449-471`；stub 实测两类路径均退出 1 且文案不同。 |
| 3. fail-closed，无无限循环、无 fail-open | 已验证 | 三处均有 `if [ "$rc" -ne 0 ]; then ... exit 1`，并明确 `for attempt in 1 2 3`；全失败 stub 实测 `exit=1`。 |
| 4. job name 匹配语义逐字不变 | 已验证 | base 与 H0 的三个选择器比较结果均为 `selector_equal=True; base_hits=1 head_hits=1`；primary 保留 exact/`endswith`，OCR/shadow 保留 `--arg suffix` 与 reviewer 后缀。 |
| 5. matrix 值继续走 `env:` | 已验证 | OCR/shadow 的 `REVIEWER` 仍在 `env:`，脚本使用 `$REVIEWER`；现有 resolver contract tests 与全量测试通过。 |
| 6. `run:` 内无字面 GHA 双花括号表达式语法（含注释） | 已验证 | `tests/test_no_literal_gha_expression_in_run_blocks.py`：`3 passed in 0.22s`；全量测试也通过。 |
| 7. 无新增 action/脚本/依赖，未改依赖、超时预算、caller、pin、runner group | 已验证 | 固定 diff 只有已有的 4 个 workflow/test 文件；未出现新增依赖或 action，超时/依赖/pin/runner group 不在改动行；`check_pinned_uses.py` 输出 `OK`。 |

## 说明：PR 正文与 base 事实的出入

PR 正文把三处都概括成同一类“失败与空结果压成一态”，但 base 实际只有 primary 使用 `gh api ... | head -n1` 且失败分支不可达；OCR 与 shadow 在 base 已有 `rc=0 / || rc=$?` 及独立错误分支。本轮以固定 diff 为准：PR 仍对三处统一加入重试/超时，并保留了 OCR/shadow 的既有两态处理；该描述出入不改变本轮结论。

## backlog（不计本轮结论）

- `gate-shadow-v2.yml:639-645` 的 summary job 另有一次 Jobs API 调用，base 与 H0 均未变；它不是三处 `REVIEW_JOB_ID` resolver，也不在 PR spec 的改动范围内。本轮不把它判为 PR #126 finding；若以后要统一 Jobs API 抖动策略，另开范围明确的卡处理。
- F-1/F-2 均为非阻塞契约测试改进项：可合并重复断言，并为 `rc=0` 分支补一个运行时短路/调用次数断言。

## 现场收口

- 被审文件没有改动；当前唯一新增产物是本 verdict 文件。
- 临时 stub、变异 archive 和摘要文件均已清理；没有改全局配置。
- 本文件在 delegate 分配的 `card/gate-20260905-02` 分支上分两次 commit；最终提交信息见执行器报告。
