"""T5: static contract checks on the reusable gate.yml.

fork-PR 防护是公开仓安全的承重墙:公开仓任何人都能开 fork PR,而 pull_request
事件执行的是 PR 作者那份 caller 文件 —— 「fork 不许上 self-hosted」的判断只能
活在本仓这份 reusable workflow 里(永远取 @main,PR 作者改不了)。这里把三处
防护(gate.runs-on / codex step if / notify.runs-on)钉成契约,误删任何一处
都在 PR 时 fail。
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate.yml"

FORK_GUARD = "github.event.pull_request.head.repo.full_name == github.repository"


def _load():
    # PyYAML parses the `on:` key as boolean True — load and normalise.
    raw = yaml.safe_load(WORKFLOW.read_text())
    trigger = raw.get("on", raw.get(True))
    return raw, trigger


def test_workflow_is_workflow_call():
    _, trigger = _load()
    assert "workflow_call" in trigger, "must be a reusable workflow"


def test_secrets_explicit_and_feishu_optional():
    # 同 build-deploy 的纪律:secrets 显式声明、绝不 inherit。
    # FEISHU_CI_WEBHOOK 必须 optional:私有仓不传 secret,走 vars 兜底。
    code = "\n".join(
        ln for ln in WORKFLOW.read_text().splitlines()
        if not ln.lstrip().startswith("#")
    )
    assert "inherit" not in code, "must NOT use `secrets: inherit`"
    _, trigger = _load()
    secrets = trigger["workflow_call"].get("secrets", {})
    assert set(secrets.keys()) == {"FEISHU_CI_WEBHOOK"}
    assert secrets["FEISHU_CI_WEBHOOK"].get("required") is False, (
        "FEISHU_CI_WEBHOOK 必须 required: false(私有仓走 vars 兜底)"
    )


def test_fork_guard_on_every_self_hosted_runs_on():
    # 任何一个 job 的 runs-on 里出现 self-hosted,同一表达式里必须带 fork 防护
    # 且必须有 hosted 降级出口 —— 保证 fork PR 永远落到一次性沙箱。
    raw, _ = _load()
    saw_self_hosted = 0
    for name, job in raw["jobs"].items():
        runs_on = str(job.get("runs-on", ""))
        if "self-hosted" in runs_on:
            saw_self_hosted += 1
            assert FORK_GUARD in runs_on, f"jobs.{name}.runs-on 丢了 fork 防护"
            assert "inputs.runner == 'self'" in runs_on, f"jobs.{name}.runs-on 丢了 runner 开关"
            assert "ubuntu-latest" in runs_on, f"jobs.{name}.runs-on 缺 hosted 降级出口"
    assert saw_self_hosted >= 2, "gate 和 notify 两个 job 都应可路由 self-hosted"


def test_codex_step_gated_on_same_repo_head():
    # codex 步骤除 runs-on 路由外还要自带同条件,防两处条件漂移时在 hosted 上误跑。
    raw, _ = _load()
    codex_steps = [
        s for s in raw["jobs"]["gate"]["steps"]
        if s.get("name") == "Codex review gate"
    ]
    assert len(codex_steps) == 1, "gate job 应恰好有一个 Codex review 步骤"
    cond = str(codex_steps[0].get("if", ""))
    assert "inputs.runner == 'self'" in cond
    assert FORK_GUARD in cond, "codex 步骤丢了 fork 防护"


def test_codex_waiver_requires_audited_reason_comment():
    raw, _ = _load()
    waiver = next(
        s for s in raw["jobs"]["gate"]["steps"]
        if s.get("name") == "Validate audited Codex review waiver"
    )
    assert "codex-review-waived" in str(waiver.get("if", ""))
    assert "Codex review waiver:" in waiver["run"]


def test_same_pr_runs_cancel_superseded_reviews():
    raw, _ = _load()
    concurrency = raw.get("concurrency", {})

    assert concurrency.get("cancel-in-progress") is True
    group = str(concurrency.get("group", ""))
    assert "github.repository" in group
    assert "github.event.pull_request.number" in group


def test_codex_review_exports_machine_readable_audit_artifact():
    raw, trigger = _load()
    inputs = trigger["workflow_call"]["inputs"]
    assert inputs["max_diff_lines"]["default"] == 4000
    assert inputs["max_review_shards"]["default"] == 8

    job = raw["jobs"]["gate"]
    assert job["timeout-minutes"] == 45
    codex = next(step for step in job["steps"] if step.get("name") == "Codex review gate")
    assert "CODEX_REVIEW_RESULT_PATH" in codex["env"]
    assert "MAX_DIFF_LINES" in codex["env"]
    assert "MAX_REVIEW_SHARDS" in codex["env"]

    upload = next(step for step in job["steps"] if step.get("name") == "Upload Codex review audit")
    assert upload["if"] == "always()"
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert "codex-review-result.json" in upload["with"]["path"]
    assert upload["with"]["if-no-files-found"] == "ignore"


def test_pr_size_preflight_runs_before_expensive_checks_and_uses_review_capacity():
    raw, trigger = _load()
    inputs = trigger["workflow_call"]["inputs"]
    assert inputs["pr_size_warn_lines"]["default"] == 8000

    steps = raw["jobs"]["gate"]["steps"]
    names = [step.get("name") for step in steps]
    preflight_index = names.index("PR size preflight")
    assert preflight_index < names.index("Lint / format")
    preflight = steps[preflight_index]
    assert preflight["uses"].startswith("zlxlabs/gate/.github/actions/pr-size-preflight@")
    assert preflight["with"]["max-diff-lines"] == "${{ inputs.max_diff_lines }}"
    assert preflight["with"]["max-review-shards"] == "${{ inputs.max_review_shards }}"
    assert preflight["with"]["warn-lines"] == "${{ inputs.pr_size_warn_lines }}"


def test_checkout_is_shallow_and_preflight_restores_exact_diff_endpoints():
    raw, _ = _load()
    checkout = raw["jobs"]["gate"]["steps"][0]
    assert checkout["uses"] == "actions/checkout@v4"
    assert checkout["with"]["fetch-depth"] == 1

    preflight_code = (REPO_ROOT / ".github" / "actions" / "pr-size-preflight" / "preflight.py").read_text()
    assert '"fetch", "--no-tags", "origin", base_sha, head_sha' in preflight_code


def test_install_dependencies_runs_before_tests_and_never_blocks_the_job():
    # D5(ci-cache-strategy.md 阶段 A):拆分计时的 step 必须在 Tests 之前(否则测不到
    # 真实的"预装让 Tests 变快"效果),且必须 continue-on-error —— 预装失败不能
    # 变成一种新的、覆盖不到所有仓库真实安装位置的失败模式(见 gate.yml 步骤注释)。
    raw, _ = _load()
    steps = raw["jobs"]["gate"]["steps"]
    names = [step.get("name") for step in steps]
    install_index = names.index("Install dependencies")
    assert install_index < names.index("Tests")
    assert install_index > names.index("Restore uv download cache")

    install = steps[install_index]
    assert install["continue-on-error"] is True
    assert "uv.lock" in install["run"]
    assert "package-lock.json" in install["run"]
    assert "pnpm-lock.yaml" in install["run"]
    # Go 调研结论:先不拆(见步骤内注释),但仍需识别 go.sum 并显式记录跳过原因,
    # 不能悄悄什么都不做。
    assert "go.sum" in install["run"]


def test_review_effectiveness_ledger_is_built_and_uploaded_even_on_failure():
    raw, _ = _load()
    assert raw["permissions"]["actions"] == "read"
    steps = raw["jobs"]["gate"]["steps"]
    build = next(step for step in steps if step.get("name") == "Build review effectiveness ledger")
    assert build["if"] == "always()"
    assert build["uses"].startswith("zlxlabs/gate/.github/actions/review-ledger@")
    assert "codex-review-result.json" in build["with"]["audit-path"]
    assert "pr-size-preflight.json" in build["with"]["preflight-path"]
    assert "install-result.json" in build["with"]["install-path"]

    upload = next(step for step in steps if step.get("name") == "Upload review effectiveness ledger")
    assert upload["if"] == "always()"
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["with"]["name"] == "codex-review-ledger"
    assert "ledger.jsonl" in upload["with"]["path"]
    assert upload["with"]["retention-days"] == 90


def test_notify_webhook_secret_first_var_fallback():
    # 公开仓走 secret(fork run 拿不到),私有仓回落 repo 变量;标题前缀约定同 build-deploy。
    text = WORKFLOW.read_text()
    assert "secrets.FEISHU_CI_WEBHOOK || vars.FEISHU_CI_WEBHOOK" in text
    assert "vars.FEISHU_CI_TITLE_PREFIX" in text
    assert "[zlxlabs·CI]" in text
