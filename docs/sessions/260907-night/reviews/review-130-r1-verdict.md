# PR #130 独立审查 verdict（第 1 轮）

- 审查对象：`1830d622a46e04b9d78760d9b507557a0c8681dc..94e6c44081bbbee226e1b6de3b890559ed858640`
- 审查范围：冻结 SHA 范围内的 1 个提交、2 个文件
- 风险基准：按 internal 档审查；P1 需经过真实环境第一问与后果第二问
- 总体 verdict：pass

## 1. 失败路径穷举（primary × quality 16 格）

结论：没有发现任何一格从阻塞变成放行。以下结果是对冻结头中
`.github/actions/gate-aggregator/aggregate.py:evaluate()` 的实际调用（`measured`）。
矩阵约定：`primary=success` 使用身份匹配的 `verdict=pass` 审计，
`primary=failure` 使用身份匹配的 `verdict=fail` 审计，`primary=cancelled`
无审计，`primary=skipped` 使用 `review_expected=false` 的预期跳过分支；
这使 16 格均有确定输入。`skipped` 是聚合器的明确结果，不等同于审查通过。

| primary \\ quality | success | failure | cancelled | skipped |
|---|---|---|---|---|
| success | `pass / code_pass / primary_pass` | `fail / ci_failure / quality_failure` | `fail / ci_failure / quality_cancelled` | `fail / ci_failure / quality_skipped` |
| failure | `fail / code_fail / primary_findings` | `fail / ci_failure / quality_failure` | `fail / ci_failure / quality_cancelled` | `fail / code_fail / primary_findings` |
| cancelled | `unavailable / review_unavailable / primary_cancelled` | `fail / ci_failure / quality_failure` | `fail / ci_failure / quality_cancelled` | `fail / ci_failure / quality_skipped` |
| skipped（预期跳过） | `skipped / expected_skip / review_not_expected` | `fail / ci_failure / quality_failure` | `fail / ci_failure / quality_cancelled` | `fail / ci_failure / quality_skipped` |

单元命令与原始输出：

```text
success   x success   -> gate_result='pass', classification='code_pass', reason='primary_pass', ok=True
success   x failure   -> gate_result='fail', classification='ci_failure', reason='quality_failure', ok=False
success   x cancelled -> gate_result='fail', classification='ci_failure', reason='quality_cancelled', ok=False
success   x skipped   -> gate_result='fail', classification='ci_failure', reason='quality_skipped', ok=False
failure   x success   -> gate_result='fail', classification='code_fail', reason='primary_findings', ok=False
failure   x failure   -> gate_result='fail', classification='ci_failure', reason='quality_failure', ok=False
failure   x cancelled -> gate_result='fail', classification='ci_failure', reason='quality_cancelled', ok=False
failure   x skipped   -> gate_result='fail', classification='code_fail', reason='primary_findings', ok=False
cancelled x success   -> gate_result='unavailable', classification='review_unavailable', reason='primary_cancelled', ok=False
cancelled x failure   -> gate_result='fail', classification='ci_failure', reason='quality_failure', ok=False
cancelled x cancelled -> gate_result='fail', classification='ci_failure', reason='quality_cancelled', ok=False
cancelled x skipped   -> gate_result='fail', classification='ci_failure', reason='quality_skipped', ok=False
skipped   x success   -> gate_result='skipped', classification='expected_skip', reason='review_not_expected', ok=True
skipped   x failure   -> gate_result='fail', classification='ci_failure', reason='quality_failure', ok=False
skipped   x cancelled -> gate_result='fail', classification='ci_failure', reason='quality_cancelled', ok=False
skipped   x skipped   -> gate_result='fail', classification='ci_failure', reason='quality_skipped', ok=False
```

注意：`primary=failure, quality=skipped` 仍命中聚合器中遗留的
`quality_short_circuited` 特判，因此 reason 是 `primary_findings` 而不是
`quality_skipped`；它仍然是阻塞结果。该特判属于本次 diff 影响到的存量语义，
在维度 2 单独记录，不把它误报成放行缺陷。

## 2. fail-closed 验证

结论：查了，未发现 fail-open。PR 正文声明是“the aggregator remains fail-closed”，
对应实现与该声明一致。

- `QUALITY_RESULT_DOMAIN` 与 `PRIMARY_RESULT_DOMAIN` 都只接受
  `success/failure/cancelled/skipped`；未知值直接返回 `Outcome(ok=False)`。
- quality 非 `success` 会形成 `quality_failure`、`quality_cancelled` 或
  `quality_skipped`，并最终映射为 `gate_result=fail`；唯一例外是旧的
  `quality_short_circuited = (quality_result == 'skipped' and primary_result == 'failure')`
  特判。该特判在本 diff 后不再是正常调度路径，但即使命中也只让 primary 的
  `fail`/`unavailable` 结论继续主导，不能变成 `pass`。
- `primary=cancelled` 生成 `job_timed_out` synthetic audit 并为
  `unavailable`；`primary=skipped` 只有 draft 或 `review_expected=false` 才进入
  `expected_skip/skipped`，非预期 skip 为 `unavailable`。
- 审计 verdict 为 `pass` 时还要求 `primary_result == success`；primary failure
  搭配 pass 审计命中 `job_audit_mismatch`，不会信任审计放行。

复核命令与原始关键输出：

```text
$ git show 94e6c44081bbbee226e1b6de3b890559ed858640:.github/actions/gate-aggregator/aggregate.py | nl -ba | sed -n '655,790p'
658 if quality_result not in QUALITY_RESULT_DOMAIN:
660 if primary_result not in PRIMARY_RESULT_DOMAIN:
664 return Outcome(ok=False, problems=invalid_inputs)
673 quality_short_circuited = quality_result == "skipped" and primary_result == "failure"
674 quality_reason = None if quality_result == "success" or quality_short_circuited else {...}[quality_result]
685 problems.append(f"quality job result is {quality_result!r} (required: success)")
720 elif primary_result == "cancelled":
722 problems.append("primary job was cancelled before completion — fail-closed, synthetic audit generated")
751 if primary_result != "success":
756 ... inconsistent, fail-closed
789 gate_result = {"code_pass": "pass", "code_fail": "fail", "expected_skip": "skipped", "ci_failure": "fail", "review_unavailable": "unavailable", "integration_error": "unavailable"}[classification]
791 ok=gate_result in ("pass", "skipped")
```

实际 16 格矩阵见维度 1；其中 `primary=failure × quality=skipped` 的实际结果为
`gate_result='fail'`，不是放行。冻结头全量测试中，相关聚合器回归测试也实际执行：
`837 passed in 36.69s`。无 P1 finding：真实运行数据和代码路径均没有观测到
「primary 已 failure 但 gate 变 pass」；因此 P1 两问均不成立，不升级为 P1。

存量 backlog（不计本 diff finding）：聚合器中的短路特判及其注释仍描述旧的
`quality: needs [primary]`，并会在一个非正常的 `failure/skipped` 组合中继续给出
primary reason。它没有造成放行，且聚合器不在本次 diff 内；后续若要清理，应单独
补一轮聚合器与 ledger reason 的契约审查。

## 3. 删除 `always()` 的后果

结论：查了，删除的是 quality 的依赖条件，不是 gate 聚合器的
`if: always()`；这确实让 quality 在 primary 的四种终态下都保持可调度，
没有引入新的默认依赖行为。

冻结 diff 的前后对比：

```text
-    needs: [primary]
-    if: always() && needs.primary.result != 'failure'
+    # gate#129: keep quality independent from primary so both expensive jobs
+    # start without waiting for the other. `gate` still needs both terminal
+    # results and aggregates them fail-closed.
```

冻结头 YAML 实际解析结果：

```text
quality needs= None if= None
primary needs= None if= ${{ github.event.pull_request.draft != true && github.event.pull_request.head.repo.full_name == github.repository && inputs.runner == 'self' }}
gate needs= ['quality', 'primary'] if= always()
ledger needs= ['quality', 'primary', 'gate'] if= always()
```

语义核对依据是 GitHub Actions 官方 workflow 语法：`needs` 才定义 job 间等待与
失败/跳过传播，`always()` 是在存在依赖且仍需运行时覆盖默认成功门槛；官方并发说明
也明确没有依赖时 job 默认可并发运行：
<https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax>、
<https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency>。

因此“总是运行”在本维度的准确含义是“不会因 primary 终态而被 job graph 短路”，
不是绕过整个 workflow 的取消、事件过滤或 runner 不可用。`gate` 自身保留
`needs: [quality, primary]` 与 `if: always()`，所以聚合仍会等待两者终态并自行判定。
该维度无 finding（`measured`：冻结头 YAML 解析、契约测试 5 条实跑通过）。

## 4. `REVIEW_EXPECTED` 双副本与测试执行性

结论：查了，双副本逐字相同，测试确实被收集且实际运行，没有被 skip 或同名测试
遮蔽；该维度无 finding。

冻结头实际解析输出：

```text
primary_if= "${{ github.event.pull_request.draft != true && github.event.pull_request.head.repo.full_name == github.repository && inputs.runner == 'self' }}"
review_expected= "${{ github.event.pull_request.draft != true && github.event.pull_request.head.repo.full_name == github.repository && inputs.runner == 'self' }}"
byte_identical= True
```

契约测试收集与执行证据：

```text
$ uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest tests/test_gate_v2_contract.py --collect-only -q
tests/test_gate_v2_contract.py::test_gate_job_review_expected_matches_primary_jobs_own_condition
122 tests collected in 0.02s

$ uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_contract.py::test_gate_job_review_expected_matches_primary_jobs_own_condition tests/test_gate_v2_contract.py::test_quality_runs_for_each_primary_result_without_dependency
.....                                                                    [100%]
5 passed in 0.33s
```

全量冻结头测试还报告 `837 passed in 36.69s`。测试的新增参数化用例覆盖四种
primary 字符串并断言 quality 没有 `needs`/`if`；REVIEW_EXPECTED 专测则对两个
表达式做完整字符串相等断言。

## 5. 正文 failure 率数据复核与样本构成

结论：数据已从 Jobs API 重新取得，但它是滚动窗口，当前快照不再与正文逐字相同；
这不能单独证明正文当时错误。正文的 gate-hub 数字可在正文提交时间附近复现，
agent-config 数字在当前 API 与正文时间截点均无法完全复现，因此把它作为
“历史测量快照”而不是当前事实。该数据偏差不构成代码 finding。

复核方法：对每个仓库的 `gate.yml` workflow runs 取最新已完成的 20 个同时含
`gate / primary` 与 `gate / quality` job 的 run，读取 Jobs API 的 `conclusion`；
`failure / executed` 的分母是 primary 非 skipped。2026-09-07 实测输出：

```text
repo=zlxlabs/gate-hub
paired_completed_sample=20
primary={"cancelled": 0, "failure": 1, "skipped": 13, "success": 6}
quality={"cancelled": 1, "failure": 1, "skipped": 1, "success": 17}
failure_all=1/20 (5.0%)
failure_executed=1/7 (14.3%)

repo=zlxlabs/agent-config
paired_completed_sample=20
primary={"cancelled": 0, "failure": 1, "skipped": 16, "success": 3}
quality={"cancelled": 3, "failure": 0, "skipped": 1, "success": 16}
failure_all=1/20 (5.0%)
failure_executed=1/4 (25.0%)
```

以 PR 提交时间 `2026-09-06T17:56:18Z` 为上界重新取样的原始摘要为：

```text
repo=zlxlabs/gate-hub cutoff=2026-09-06T17:56:18Z paired=20 primary={"cancelled": 0, "failure": 1, "skipped": 12, "success": 7}
repo=zlxlabs/agent-config cutoff=2026-09-06T17:56:18Z paired=20 primary={"cancelled": 0, "failure": 1, "skipped": 16, "success": 3}
```

因此 gate-hub 的正文 `7/1/12` 与 `1/8` 对得上；agent-config 的正文 `3/0/17`、
`0/3` 在这个截点已经对不上，最可能是作者更早取样后新 run 进入窗口，但正文没有
样本时间戳，无法用当前 API 还原作者当时的 20 个 run。正文没有把该样本外推成总体
故障率，也同时给了 `failure / executed`，所以不把这个时间窗问题升级为 major。

样本构成结论：会低估。正文的 `12/17` 个 primary skipped 不执行审查，把大量
“没有进入 primary”放进 `failure / all` 分母；更有信息量的是 `1/8 (12.5%)` 与
`0/3 (0%)`，但执行样本极小，不能支撑稳定的总体失败率。它只足以支持“存在可观测的
green-path 串行成本”这一局部决策背景。该维度无阻塞 finding；建议后续正文给样本
时间戳并固定取样规则，作为 P3 文档 backlog。

## 6. 并发、concurrency group、runner 池与 writer lock

结论：查了，quality 与 primary 不争抢同一 concurrency group，也不共用 self-hosted
runner 标签；gate/ledger 的 writer lock 与 runs-on 均未改变。该维度无 finding。

冻结头实测：

```text
quality needs= None
  concurrency={'group': 'gate-required-v2-quality-${{ github.repository_id }}-${{ github.event.pull_request.number || github.run_id }}', 'cancel-in-progress': True}
  runs-on=...fromJSON('["self-hosted","linux","ci"]')...
primary needs= None
  concurrency={'group': 'gate-required-v2-primary-${{ github.repository_id }}-${{ github.event.pull_request.number || github.run_id }}', 'cancel-in-progress': True}
  runs-on=...fromJSON('["self-hosted","linux","codex"]')...
gate needs= ['quality', 'primary']
  concurrency={'group': 'gate-required-v2-panel-${{ github.repository_id }}-${{ github.event.pull_request.number }}', 'cancel-in-progress': False}
  runs-on=...fromJSON('["self-hosted","linux","codex"]')...
ledger needs= ['quality', 'primary', 'gate']
  concurrency={'group': 'gate-required-v2-ledger-${{ github.repository_id }}', 'cancel-in-progress': False}
  runs-on=...fromJSON('["self-hosted","linux","codex"]')...
```

对 base 与冻结头逐项解析的原始结果显示：除 quality 的 `needs` 从
`['primary']` 变为 `None` 外，上述四个 job 的 `concurrency`、`runs-on` 均逐字
不变。quality 走 `ci` 池，primary 走 `codex` 池；gate 依赖两者终态，ledger
依赖 gate，因此 gate/ledger 不会与同一 run 的 primary 同时写入。不同 PR 的
run 仍按既有每 PR group 取消旧的 quality/primary，同一 run 的两把 group 不相同。

本轮没有可用于观察 PR #130 新 `gate-v2` 调度时间的实际 caller run：PR 未改
caller，仓库现有历史 `gate.yml` 也不是这个冻结 workflow 的新调用。因此“两个
`started_at` 独立”属于 `inferred` 的调度结论，不能冒充 measured；但不影响本维度
对 group、runner、writer lock 的 measured 静态核验。

## 7. 熵增与删除注释承载的约束

结论：查了，删除的注释主要解释已经被删除的 `needs: [primary]` 机制；没有发现
只存在于旧注释、但仍适用于新 job graph 的约束被一并删除。该 diff 没有新增状态、
抽象、配置项或包装层，因此没有熵增 finding。

逐项核对：

- 旧注释中“`always()` 必须保住 primary cancelled/skipped 时 quality 仍运行”
  的前提是 quality 有 `needs: [primary]`；新结构没有这个依赖，约束本身不再适用。
- 旧注释中“primary failure 短路 quality”及其 4.2–31.1 分钟代价描述的是被删除
  的行为，继续保留反而会误导后续维护者。
- 新注释保留了真正仍成立的两个不变式：quality/primary 独立启动，gate 仍等待
  两个终态并 fail-closed；顶层并发说明与契约测试仍保留 writer lock 和独立 group
  约束。
- 测试文件的旧短路说明也随测试契约改成“quality independent”，与实现一致。

证据：`git diff --unified=20 1830d622a46e04b9d78760d9b507557a0c8681dc..94e6c44081bbbee226e1b6de3b890559ed858640 -- .github/workflows/gate-v2.yml tests/test_gate_v2_contract.py`
显示该 commit 只有 18 insertions / 31 deletions；新增内容只是并行调度注释与
参数化结构断言，没有引入额外机制。结论为 `measured`（diff、冻结头解析及全量
测试均已执行）。

## 8. PR 正文逐句核验

结论：正文描述与冻结 diff 基本一致；没有发现“正文声称做了、实现却没做”的
major。逐句核验如下：

| 正文声明 | 结论 | 证据与性质 |
|---|---|---|
| Remove `quality.needs: [primary]` 和 primary-dependent condition | 成立 | 冻结 diff 删除两行；YAML 实测 `quality.needs=None, if=None`（`measured`） |
| Keep `gate` dependent on both terminal results and `if: always()` | 成立 | `gate.needs=['quality','primary']`, `gate.if='always()'`（`measured`） |
| primary 自身 `if:` unchanged；`REVIEW_EXPECTED` byte-identical | 成立 | base/head primary 条件相同，冻结头双副本 `byte_identical=True`，专测 1 条通过（`measured`） |
| No caller / pin / tag / runner-pool / merge-queue / aggregator changes | 成立 | `git diff --name-status` 只有 `.github/workflows/gate-v2.yml` 与 `tests/test_gate_v2_contract.py`；runner expression、writer group、uses/pin 与 aggregator 文件均未进入 diff（`measured`） |
| primary failure 可能重新等待 quality | 成立 | 依赖关系移除后 quality 与 primary 独立，gate 仍等待两者（`measured` + 官方调度语义 `inferred`） |
| gate-hub 三个成功 run 的串行税为 4.1–7.2 分钟 | 大体支持但无法锁定正文所指三 run | Jobs API 对正文窗口内可识别成功 run 测得 serial tax `4.5, 6.3, 5.7, 5.9, 7.2` 分钟；正文未给 run id，因此不能证明精确的“三个”和 4.1 下界（`measured`，非代码 finding） |
| agent-config 历史样本 primary 1m28 / quality 15m04 | 本轮未独立定位 | 正文未给 run id；本轮没有把这句当作 verdict 依据（`unverified`，非 finding） |
| Contract matrix 的四种 primary 结果都让 quality runnable | 成立 | 无 `needs`/job-level `if`，四个参数化契约用例实跑；GitHub 官方文档确认 `needs` 才产生 job 依赖（`measured` + `inferred`） |
| aggregator remains fail-closed | 成立 | 16 格实测无 `primary` failure/cancelled 或非成功 quality 进入 `pass`（`measured`） |
| `837 passed in 42.85s` | 结果成立，耗时因环境不同 | 冻结头实跑 `837 passed in 36.69s`（`measured`） |
| `check_pinned_uses.py` 全部内部 uses 为 workspace-relative | 成立 | 冻结头输出 `OK: checked 8 live workflow/action metadata file(s); all internal uses are workspace-relative`（`measured`） |
| red verification 注入旧 needs/if 后 success/failure 两例变红 | 成立 | 独立临时工作树注入并确认两例 `2 failed in 0.41s`；随后 `git restore`，`restored_workflow_diff_rc=0`（`measured`） |

PR 检查的原始输出也与正文的验证状态一致：

```text
actionlint  pass  4s
test        pass 33s
```

本机 actionlint 另有 3 个 SC2129 style 警告（冻结头未改动的旧行 256、988），
本地退出码为 1；这与 GitHub PR 检查的 `pass` 不一致但不在本 diff，记作存量
backlog，不作为本轮 finding。

## Findings

本轮有效 finding：0。

没有 P1/P2/P3 阻塞意见：16 格矩阵没有从阻塞变成放行，`REVIEW_EXPECTED` 未漂移，
writer lock 与 runner pool 未被改动，冻结头测试与 PR 检查均通过。按 internal 风险
基准执行了 P1 两问：第一问以 GitHub Jobs API 的真实 paired runs 量过，未观察到
primary failure/cancelled 或非成功 quality 进入 `gate_result=pass`；第二问因此没有
“已触发且后果不可接受”的实例。

非 finding backlog（不阻塞本 PR）：

1. P3 文档质量：PR 正文的 20-run 统计没有样本时间戳和 run id，滚动窗口现已与
   agent-config 数字不一致；后续应固定取样时间和规则。
2. 存量语义：聚合器仍保留旧的 `quality_short_circuited` 特判及注释，未造成放行，
   但可在后续独立聚合器/ledger 契约清理中处理。
3. 存量工具提示：本机 actionlint 对未改动行报 SC2129，而 GitHub PR check 为 pass；
   不归因于本次 diff。
