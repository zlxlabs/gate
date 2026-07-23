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
    # review 步骤(2026-07-20 改名 Agent review gate)除 runs-on 路由外还要自带同条件,
    # 防两处条件漂移时在 hosted 上误跑。
    raw, _ = _load()
    codex_steps = [
        s for s in raw["jobs"]["gate"]["steps"]
        if s.get("name") == "Agent review gate"
    ]
    assert len(codex_steps) == 1, "gate job 应恰好有一个 agent review 步骤"
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
    # timeout 已参数化：契约锁定"引用 timeout_minutes 输入且默认值为 45"
    assert job["timeout-minutes"] == "${{ inputs.timeout_minutes }}"
    assert inputs["timeout_minutes"]["default"] == 45
    codex = next(step for step in job["steps"] if step.get("name") == "Agent review gate")
    assert "CODEX_REVIEW_RESULT_PATH" in codex["env"]
    assert "MAX_DIFF_LINES" in codex["env"]
    assert "MAX_REVIEW_SHARDS" in codex["env"]
    assert codex["env"]["GATE_TIER"] == "${{ inputs.tier }}"

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
    # canary ring 第 1 批(2026-07-17 起,临时):门禁自身变更按 tier 分批生效,
    # 本步骤先只对 personal 生效。promotion 时(→ internal → 全量)由后续 PR
    # 修改/删除 gate.yml 的 if 条件,并同步更新这条断言 —— 故意钉死,防止条件
    # 被顺手删掉而绕过灰度纪律。
    assert "inputs.tier == 'personal'" in str(install.get("if", ""))


def test_uv_actions_cache_is_hosted_only():
    # ci-cache-strategy.md §0 D2: `runner: self` 仓改走 run-ephemeral-runner.sh
    # 挂载的 VM201 本机卷,不再靠 actions/cache 云端回传(重依赖仓库缓存体积可达
    # GB 级,回传本身比冷装还慢)。这两步只应该在 `runner == 'hosted'` 时跑;误删
    # 这个条件会让 self 仓悄悄退回云端回传路径,故意钉死。
    raw, _ = _load()
    steps = raw["jobs"]["gate"]["steps"]
    restore = next(s for s in steps if s.get("name") == "Restore uv download cache")
    prune = next(s for s in steps if s.get("name") == "Prune uv cache before persistence")
    assert "inputs.runner == 'hosted'" in str(restore.get("if", ""))
    assert "inputs.runner == 'hosted'" in str(prune.get("if", ""))


def test_self_runner_points_package_managers_at_the_local_cache_volume():
    # ci-cache-strategy.md §0 D2: self 仓的包管理器缓存指向
    # run-ephemeral-runner.sh(私有 gate-hub 仓)挂载的本机读写卷,按生态分卷。
    # canary ring 第 1 批,和 Install dependencies 同一条件、同一批次。
    raw, _ = _load()
    steps = raw["jobs"]["gate"]["steps"]
    names = [step.get("name") for step in steps]
    step_name = "Point package manager caches at the VM201 local cache volume (self, personal canary)"
    cache_index = names.index(step_name)
    assert cache_index > names.index("Restore uv download cache")
    assert cache_index < names.index("Install dependencies")
    assert cache_index < names.index("Tests")

    cache_step = steps[cache_index]
    cond = str(cache_step.get("if", ""))
    assert "inputs.runner == 'self'" in cond
    # canary ring: 先只对 personal 生效,promotion 时(→ internal → 全量)由后续
    # PR 修改并同步更新这条断言 —— 故意钉死,防止条件被顺手删掉而绕过灰度纪律。
    assert "inputs.tier == 'personal'" in cond

    run = cache_step["run"]
    assert "cache_root=/opt/gate-hub-cache" in run
    assert 'UV_CACHE_DIR=$cache_root/uv' in run
    assert 'npm_config_cache=$cache_root/npm' in run
    assert 'pnpm_config_store_dir=$cache_root/pnpm' in run
    assert 'GOMODCACHE=$cache_root/go' in run
    assert run.count("GITHUB_ENV") == 4


def test_self_runner_cache_env_switch_does_not_apply_to_internal_or_saas_yet():
    # canary ring: internal/saas 仓暂时没有这个 env 切换(D8 上线顺序:
    # personal → internal → saas)。这条断言不解析表达式真值,只保证条件字符串
    # 里出现的是 tier == 'personal' 而不是更宽松的写法,防止有人不小心把灰度
    # 范围放宽了却没有走 promotion 的 PR 流程。
    raw, _ = _load()
    steps = raw["jobs"]["gate"]["steps"]
    step_name = "Point package manager caches at the VM201 local cache volume (self, personal canary)"
    cache_step = next(s for s in steps if s.get("name") == step_name)
    cond = str(cache_step.get("if", ""))
    assert "inputs.tier != 'saas'" not in cond
    assert cond.count("inputs.tier ==") == 1


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
