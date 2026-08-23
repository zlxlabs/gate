VERDICT: pass

审查范围：`3a7cd36283a35d18f9c3eaca8e1e83052fb45273..5de99523a253c0198e172e73ea29dab04bbf0146`（H0 冻结，8 文件 +656/-5）。
风险档：`personal`（P1 红线：数据丢失、静默出错、崩溃）；infra 类收敛按 internal 档（连续 2 轮无新增 P1）。
本轮视角：gate-v2 diff-coverage advisory 第 1 轮全量审 + 降层三问 + 熵增。

本轮新证据：

- 全量 diff 阅读（workflow 接线、composite action、advisory.py、两份测试、文档）。
- head SHA `5de9952` 运行 `uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q`：`635 passed in 95.35s`（0 skipped）。
- 红验：base SHA `3a7cd36` 临时克隆仅拷入 `tests/test_diff_coverage_advisory.py`，`test_missing_lcov_reports_no_coverage_data_not_zero` 以 `FileNotFoundError`（缺 `advisory.py`）失败，exit=1。
- OCR 前置扫描：`status=reviewed`（MiniMax-M3），6 条 finding 均未 verified；逐条经 P1 两问重判后落地如下，不照搬 OCR severity。

## 降层三问

### 1. 终态（PR 评论发出 / quality job 结论）写入成功之前，已发生哪些不可逆动作？

按时间顺序（quality job 内）：

1. **Tests 及之前步骤**已完成——测试产物（含可能的 `coverage/lcov.info`）已写入 caller workspace；Tests 失败时 job 结论已注定非 success，与 advisory 无关。
2. **Sparse checkout**（`gate-v2.yml:287-297`）从 `job.workflow_repository` @ `job.workflow_sha` 拉取 gate 源码到 `_gate-action-src`——在 ephemeral runner 上为可丢弃对象，不构成跨 run 持久副作用。
3. **advisory.py `measure()`** 内 `ensure_review_commits` 可能对 `origin` 做按需 `git fetch`（`advisory.py:59-65`）——向本地 repo 增加对象，runner 销毁后消失。
4. **diff-cover 计算**为只读，不写 caller 仓库。
5. **不可逆对外动作**：`post_sticky_comment` 成功执行 `POST`/`PATCH` GitHub Issues API（`advisory.py:218-223`）后，PR 评论已发布/更新；`_append_summary` 追加 Step Summary（`advisory.py:240-243`）为同 run 内可见状态。

**quality job 结论**：advisory 两步均 `if: always()` + `continue-on-error: true`（`gate-v2.yml:288-301`）；`advisory.py:main()` 所有路径 `return 0`（`:254-255`、`:263-264`、`:288`）。终态写入（评论）失败时 job 结论仍由 Tests/lint 等上游步骤决定，advisory 不改变之。

必要二次 checkout 原因：`gate-v2.yml:169-177` 在 caller checks 前删除 `_gate-action-src`，故 Tests 后必须重新 checkout（非无谓重复）。

### 2. 守卫值（sticky 标识、`job.workflow_sha`、lcov 路径）在实际部署形态下是否唯一？

| 守卫 | 部署形态 | 唯一性 |
|---|---|---|
| `MARKER` (`<!-- diff-coverage-advisory -->`) | 同 PR 多次 run / rerun | 通过 marker 查找已有评论并 PATCH（`advisory.py:218-221`），同 PR 仅一条 sticky。 |
| `head_sha` 陈旧守卫 | push 后 rerun | `post_sticky_comment` 比对 live PR head（`:214-216`），head 已前进则跳过写入，避免发陈旧注记。 |
| `job.workflow_sha` | 多 caller pin | 用于 checkout gate 动作源码版本，非评论主键；各 caller 各自 checkout，不冲突。 |
| `coverage/lcov.info` | 固定默认路径 | 单 workspace 单路径；caller 自定义可通过 `lcov-path` input（当前 workflow 未覆盖）。 |
| `github.token` + fork PR | fork → hosted fallback | fork PR 的 `pull-requests: write` 受限，API 403 → `URLError` 被捕获（`:284-285`），降级为无评论（spec 三态之外的可接受缺失，非静默改结论）。 |
| 并发 quality job | 同 PR 并行 workflow | 两 job 同 head_sha 可能竞态 PATCH；末次写入胜出，最多评论闪烁，不改 job 结论。 |
| Issues API 分页 | PR 评论 >100 条 | `per_page=100` 仅扫首页（`:218`）；marker 评论若在后续页会漏检并 POST 重复评论——见 F-2。 |

### 3. `continue-on-error` 保护的是「写入」还是「行为」？有无失败路径绕过它仍改变 job 结论或发出错误评论？

- **保护层级**：workflow 两步 `continue-on-error: true` 吸收 step 级失败；`advisory.py` 顶层 `try/except` + 恒 `return 0` 保证 composite 内 `set -euo pipefail` 不因 Python 非零退出（`action.yml:23-32`、`advisory.py:256-264`）。
- **不改变 job 结论**：无 `exit 1`；`test_diff_coverage_advisory_never_gates_quality_job` 与 `test_diff_coverage_advisory_runs_after_caller_tests_with_continue_on_error` 锁定接线。
- **错误评论风险**：缺 lcov 输出 `no coverage data` 而非 `0%`（spec 条款 2，`test_missing_lcov_reports_no_coverage_data_not_zero`）；head 陈旧跳过；measure 异常降级为无评论。未发现绕过 `continue-on-error` 仍 fail quality 的路径。
- **stderr 丢失**（F-3）使运维难以区分「无评论」原因，但不构成静默改结论。

## 熵增审查

| 新增项 | 熵 +1？ | 判断 |
|---|---:|---|
| `.github/actions/diff-coverage-advisory/` 复合 action + `advisory.py` | 否 | spec 条款 1 要求独立 advisory step；与 `pr-size-preflight` 同构，有明确消费者（gate-v2 quality job）。 |
| `CODE_EXTENSIONS` 元组（`advisory.py:26-52`） | 偏是 | 手写扩展名表属「自建分类」（REFACTOR 词表 #7），但用于 docs-only 预判、避免无 lcov 时误跑 diff-cover；可用 `git diff --diff-filter` 等替代的讨论留 backlog，当前不抬 P1。 |
| Tests 后第二次 sparse checkout | 否 | 由 `:169-177` 清理步骤强制；非转发-only 层。 |
| `test_gate_v2_contract` 放宽 `len(checkouts)==1` | 偏是 | 为容纳两 checkout 而弱化断言却未迭代验证每个 checkout——见 F-1。 |
| `docs/diff-coverage-advisory.md` | 否 | spec 文档化契约的必要补充。 |
| CI/AGENTS 增 `diff-cover,coverage` | 否 | spec 条款 6 对齐。 |

## Spec 对照（摘要）

| 条款 | 结论 |
|---|---|
| 1. Tests 后 advisory step + `diff-coverage: ` 前缀 | 满足（`gate-v2.yml:275-306`，`advisory.py:24-25`） |
| 2. 三态语义 | 满足；lcov 实测、缺 lcov、docs-only 均有测试 |
| 3. I-1a 隔离：`continue-on-error` + 不参与门禁 | 满足 |
| 4. 只度量改动行 | diff-cover `--compare-branch base_sha` |
| 5. base 按需 fetch | `ensure_review_commits` 与 preflight 同构 |
| 6. 测试合同：真实 lcov fixture、依赖一致 | 满足（`_repo_with_code_change` 用 coverage.py；CI/AGENTS 已对齐） |

## Findings

| ID | 判级 | 文件:行 | 违反 spec / 不变式 | P1 两问 | 说明 |
|---|---|---|---|---|---|
| F-1 | P2 | `tests/test_gate_v2_contract.py:975-977` | spec 条款 6（测试合同完整性） | ① 仅当未来新增 `_gate-action-src` checkout 且 sparse 漏 `scripts/scrub_outbound.py` 时触发；② 后果是 import 失败 → step 红但被 `continue-on-error` 吸收，缺注记而非改结论 | 从 `len(checkouts)==1` 放宽为 `assert checkouts` 后仍只验 `checkouts[0]`；quality job 现有两个同 path checkout（`:149` 与 `:287`），应 `for checkout in checkouts` 逐个验 sparse 覆盖。 |
| F-2 | P3 | `advisory.py:218` | 无法溯源 spec 条款（降一级） | ① 仅超活跃 PR（>100 issue 评论且 marker 不在首页）；② 重复评论，不改 job 结论 | `per_page=100` 无分页遍历，可能 POST 第二条带 marker 的评论。 |
| F-3 | P3 | `advisory.py:103-104,263-264` | I-1a 可观测性（非功能违反） | ① malformed lcov 时；② 降级为无注记，日志无 diff-cover stderr | `capture_output=True` 且宽泛 `except Exception` 吞掉 stderr，拉长排障时间。 |
| F-4 | P3 | `advisory.py:26-52,83-87` | spec 条款 2 边缘 | ① 仅改无扩展名/未列扩展名「代码」文件（如 `Dockerfile`）；② 误判 docs-only → 不产注记（既非 `no_data` 也非百分比） | `CODE_EXTENSIONS` 未覆盖 extensionless 路径；advisory 误导性低于门禁风险。 |
| F-5 | P3 | `gate-v2.yml:287-306` | 无（与 preflight 同构） | ① 非 `pull_request` 事件；② 空 SHA fetch 后降级，浪费网络 | 可加 `github.event_name == 'pull_request'` 守卫；preflight 亦同形，记 backlog。 |

**本轮 P1 = 0** → **pass**。

## OCR 对照（工具标注 → 本仓判定）

| OCR 摘要 | 工具 severity | 本仓判定 | 理由 |
|---|---|---|---|
| checkout 断言弱化 | high | P2（F-1） | 两问不过 P1 |
| 重复 checkout | low | 不立案 | 清理步骤使二次 checkout 必要 |
| 非 PR 事件未守卫 | medium | P3（F-5） | 与既有 preflight 一致，fail-soft |
| diff-cover stderr | low | P3（F-3） | |
| 测试硬编码格式串 | low | P3 backlog | 行为回归仍会被测到 |
| 注释措辞 | low | 不立案 | 纯可读性 |

## 红验抽查

base `3a7cd36` 临时克隆，仅拷入 `tests/test_diff_coverage_advisory.py`：

```
ERROR tests/test_diff_coverage_advisory.py::test_missing_lcov_reports_no_coverage_data_not_zero
FileNotFoundError: .../diff-coverage-advisory/advisory.py
1 error in 0.38s
EXIT=1
```

非恒真测试。

## 相关测试

head `5de9952`：`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q` → **635 passed in 95.35s**（0 skipped）。

## Backlog（存量 / 越界）

- `pr-size-preflight` 与 advisory 均未对非 PR 事件加 `if:` 守卫（F-5 同族）。
- blocking 阈值、E1/onboard 模板：任务卡声明非本轮对象。
- fork PR 无评论属 GitHub token 权限模型，spec 允许降级为缺失注记。
