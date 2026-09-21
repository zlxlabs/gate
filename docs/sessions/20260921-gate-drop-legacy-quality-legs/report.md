outcome: success
## 结果

本卡已完成。两个 workflow 中的两条 legacy quality step 已删除，相关 step ID/output 引用、契约测试、功能注释和 README 已同步；没有改动 gate-shadow-v2.yml 或 tests/test_gate_tool_isolation.py，也没有新增替代检查、抽象或配置项。
## 实现范围

- .github/workflows/gate-v2.yml：删除 jscpd 与 dependency-cruiser 两个 step；删除 Record caller checks outcome 对两个已删除 step outcome 的读取；更新 quality 功能描述。
- .github/workflows/gate.yml：删除两个同构 step；更新文件头功能描述。
- README.md：功能列表改为 lint / tests / Codex review。
- 三份契约测试：删去两个 step 名称和 ID 的断言；删去 shadow/aggregator caller-quality marker。marker 列表的语义是当前会执行的 caller quality 特征，不是历史特征；两腿删除后保留它们只会成为无消费者的死 marker，也违反本卡非历史目录零命中约束。
- 假设核对：两份 legacy step 结构同构，均由 mode == 'legacy' 门控；v2 另外有 caller-checks 证据记录，因此同步移除了对应的两个 outcome 变量和循环项。没有发现其他 step 依赖这两个 ID/output。
## 关键不变式与反向验证

删除名称后，first_caller_check 仍取现存的四个 caller quality step（Run scripts/gate-quality、Lint / format、Install dependencies、Tests）的最小位置，因此 cleanup_index < first_caller_check 仍约束清理早于所有 caller check；后面的逐项 cleanup_index < names.index(name) 仍提供逐名守卫。相邻性断言仍约束 PR size preflight -> Remove gate action source before caller checks -> Run scripts/gate-quality，没有塌成恒真。
反向验证 1：把清理 step 移到 Lint / format 后，运行原测试，得到断言失败（exit 1）：
~~~text
F                                                                        [100%]
=================================== FAILURES ===================================
__________ test_quality_removes_gate_sources_before_any_caller_check ___________

    def test_quality_removes_gate_sources_before_any_caller_check():
        raw, _ = _load_workflow()
        steps = raw["jobs"]["quality"]["steps"]
        names = [step.get("name") for step in steps]
        stale_cleanup = next(s for s in steps if s.get("name") == "Remove stale gate source directories")
        source_checkout = next(
            s for s in steps if s.get("name") == "Checkout gate actions at this workflow's own commit"
        )
        preflight_index = names.index("PR size preflight")
        cleanup = next(s for s in steps if s.get("name") == "Remove gate action source before caller checks")
        cleanup_index = names.index(cleanup["name"])
        first_caller_check = min(
            names.index(name)
            for name in (
                "Run scripts/gate-quality",
                "Lint / format",
                "Install dependencies",
                "Tests",
            )
        )

        assert names.index(stale_cleanup["name"]) < names.index(source_checkout["name"])
>       assert names.index(source_checkout["name"]) < preflight_index < cleanup_index < first_caller_check
E       assert 7 < 5

tests/test_gate_v2_contract.py:2232: AssertionError
=========================== short test summary info ============================
FAILED tests/test_gate_v2_contract.py::test_quality_removes_gate_sources_before_any_caller_check
1 failed in 0.30s
~~~
反向验证 2：把 Restore uv download cache 插入预检与清理之间，保持清理仍早于 caller checks，专门验证相邻性守卫，得到断言失败（exit 1）：

~~~text
F                                                                        [100%]
=================================== FAILURES ===================================
__________ test_quality_removes_gate_sources_before_any_caller_check ___________

    def test_quality_removes_gate_sources_before_any_caller_check():
        raw, _ = _load_workflow()
        steps = raw["jobs"]["quality"]["steps"]
        names = [step.get("name") for step in steps]
        stale_cleanup = next(s for s in steps if s.get("name") == "Remove stale gate source directories")
        source_checkout = next(
            s for s in steps if s.get("name") == "Checkout gate actions at this workflow's own commit"
        )
        preflight_index = names.index("PR size preflight")
        cleanup = next(s for s in steps if s.get("name") == "Remove gate action source before caller checks")
        cleanup_index = names.index(cleanup["name"])
        first_caller_check = min(
            names.index(name)
            for name in (
                "Run scripts/gate-quality",
                "Lint / format",
                "Install dependencies",
                "Tests",
            )
        )

        assert names.index(stale_cleanup["name"]) < names.index(source_checkout["name"])
        assert names.index(source_checkout["name"]) < preflight_index < cleanup_index < first_caller_check
>       assert cleanup_index == preflight_index + 1 and names[cleanup_index + 1] == "Run scripts/gate-quality"
E       assert (6 == (4 + 1))

tests/test_gate_v2_contract.py:2233: AssertionError
=========================== short test summary info ============================
FAILED tests/test_gate_v2_contract.py::test_quality_removes_gate_sources_before_any_caller_check
1 failed in 0.34s
~~~
两次临时变更均已还原；还原后该顺序测试为 1 passed，工作树相对实现提交无 diff。

## 验证

- focused contract tests：266 passed in 29.10s。
- full suite：1083 passed in 74.68s (0:01:14)。
- actionlint -shellcheck= .github/workflows/gate-v2.yml .github/workflows/gate.yml：exit 0，无输出。
- 默认 actionlint 首次还发现 v2 的两个悬空引用（steps.duplicate-check.outcome、steps.dependency-direction.outcome），已删除；默认 shellcheck 仍报告两个改动前已有的 SC2129 风格告警（gate-v2.yml:351、gate.yml:199），本卡未扩大范围修复。
- python3 scripts/check_pinned_uses.py：OK: checked 9 live workflow/action metadata file(s); all internal uses are workspace-relative。
- git diff --check：exit 0。
- git grep -in -E 'jscpd|dependency-cruiser|depcruise|JSCPD_THRESHOLD' -- . ':!docs/sessions'：exit 1、无输出。历史评审记录目录按任务约束保留。
## 提交审计
实现提交：
~~~text
b8419ff refactor: remove legacy quality checks
~~~
git show --stat --format= HEAD 实际输出：
~~~text
 .github/workflows/gate-v2.yml         | 20 ++------------------
 .github/workflows/gate.yml            | 16 +---------------
 README.md                             |  4 ++--
 tests/test_gate_contract.py           |  3 +--
 tests/test_gate_shadow_v2_contract.py |  2 --
 tests/test_gate_v2_contract.py        | 11 +----------
 6 files changed, 7 insertions(+), 49 deletions(-)
~~~
git status --short --untracked-files=all 实际输出：无输出（exit 0）。

## 现场备注

- pickup 现场无交接单，工作树初始干净，当前 checkout 停在 card/gate-20260921-02。
- 协作状态扫描识别本派卡为自己的 dispatch；push 命令 exit 0 且远端返回新建分支，但随后两次 ls-remote 复核均因 SSH 连接关闭失败，未以本地 tracking ref 代替远端结论。
- 收件箱扫描完成；开放 issue 中未发现与本卡直接冲突的事项。
- archive_orphan_debts 巡检探针因内部 fetch 远端阻塞，按长命令边界终止，exit 130；不能据此判断有主/无主欠账。
- memory 巡检原样失败：memory 巡检报告不可用：memory_dir_mismatch（/home/zlx/.local/state/memory-doctor/latest.json）。
- 临时反向验证没有留下临时目录；本卡报告目录为正式落盘产物。
