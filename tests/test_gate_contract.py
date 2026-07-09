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
        if "codex" in str(s.get("name", "")).lower()
    ]
    assert len(codex_steps) == 1, "gate job 应恰好有一个 Codex review 步骤"
    cond = str(codex_steps[0].get("if", ""))
    assert "inputs.runner == 'self'" in cond
    assert FORK_GUARD in cond, "codex 步骤丢了 fork 防护"


def test_notify_webhook_secret_first_var_fallback():
    # 公开仓走 secret(fork run 拿不到),私有仓回落 repo 变量;标题前缀约定同 build-deploy。
    text = WORKFLOW.read_text()
    assert "secrets.FEISHU_CI_WEBHOOK || vars.FEISHU_CI_WEBHOOK" in text
    assert "vars.FEISHU_CI_TITLE_PREFIX" in text
    assert "[zlxlabs·CI]" in text
