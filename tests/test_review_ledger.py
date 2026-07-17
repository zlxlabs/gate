import importlib.util
import json
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "actions" / "review-ledger" / "build_ledger.py"


def _module():
    spec = importlib.util.spec_from_file_location("review_ledger", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _audit(sha: str, ids: list[str], duration: int = 30) -> dict:
    return {
        "status": "fail" if ids else "pass",
        "reviewed_sha": sha,
        "coverage": {"mode": "single", "complete": True, "diff_lines": 100, "shards": 1},
        "runtime": {"duration_s": duration, "codex_version": "codex-cli test", "model": "default"},
        "result": {
            "verdict": "fail" if ids else "pass",
            "summary": "result",
            "findings": [
                {"id": finding_id, "severity": "major", "category": "correctness"}
                for finding_id in ids
            ],
        },
    }


def test_new_head_comparison_tracks_persistent_resolved_and_new_findings():
    module = _module()
    previous = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="old", preflight={}, audit=_audit("old", ["a", "b"]), prior_entries=[], dispositions={},
    )
    current = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=11, run_attempt=1,
        head_sha="new", preflight={}, audit=_audit("new", ["b", "c"]), prior_entries=[previous], dispositions={},
    )

    assert current["comparison"]["kind"] == "new_head"
    assert current["comparison"]["persistent_finding_ids"] == ["b"]
    assert current["comparison"]["resolved_finding_ids"] == ["a"]
    assert current["comparison"]["new_finding_ids"] == ["c"]
    assert current["review_round"] == 2


def test_same_head_rerun_is_recorded_as_stability_not_as_a_fix():
    module = _module()
    previous = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="same", preflight={}, audit=_audit("same", ["a", "b"]), prior_entries=[], dispositions={},
    )
    current = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=2,
        head_sha="same", preflight={}, audit=_audit("same", ["b", "c"]), prior_entries=[previous], dispositions={},
    )

    assert current["comparison"]["kind"] == "same_head_rerun"
    assert current["comparison"]["missing_finding_ids"] == ["a"]
    assert current["comparison"]["appeared_finding_ids"] == ["c"]
    assert "resolved_finding_ids" not in current["comparison"]


def test_install_metrics_flow_through_when_present_and_default_to_none():
    module = _module()
    with_install = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=_audit("sha", []), prior_entries=[], dispositions={},
        install={"ecosystem": "uv", "status": "ok", "duration_s": 42, "cache_hit": True},
    )
    without_install = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=11, run_attempt=1,
        head_sha="sha", preflight={}, audit=_audit("sha", []), prior_entries=[], dispositions={},
    )

    assert with_install["install"] == {
        "ecosystem": "uv", "status": "ok", "duration_s": 42, "cache_hit": True,
    }
    # Old callers (and old ledger entries with no "install" key) must not break —
    # the field is purely additive.
    assert without_install["install"] is None


def test_missing_install_result_file_yields_null_install_field(tmp_path):
    # canary ring:tier != personal 时 gate.yml 的 Install 步骤整体不跑,
    # install-result.json *不存在*(不是一份 skipped JSON)。_load_json 必须
    # 对缺文件(以及空文件)容错为 None,进而 ledger 条目 install 字段为 null,
    # 不能报错 —— 否则非 personal tier 的每次 run 都会在 ledger 步骤炸掉。
    module = _module()

    missing = tmp_path / "does-not-exist" / "install-result.json"
    assert module._load_json(missing) is None

    empty = tmp_path / "install-result.json"
    empty.write_text("")
    assert module._load_json(empty) is None

    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=_audit("sha", []), prior_entries=[], dispositions={},
        install=module._load_json(missing),
    )
    assert entry["install"] is None
    assert json.loads(json.dumps(entry))["install"] is None


def test_disposition_comments_capture_false_positive_reason_and_author():
    module = _module()
    comments = [{
        "body": "Codex finding disposition: correctness.bad-state = false-positive — 真实接口不会进入此路径",
        "user": {"login": "owner"},
        "created_at": "2026-07-12T00:00:00Z",
        "html_url": "https://example.test/comment/1",
    }]

    dispositions = module.parse_dispositions(comments)

    assert dispositions["correctness.bad-state"]["disposition"] == "false-positive"
    assert dispositions["correctness.bad-state"]["reason"] == "真实接口不会进入此路径"
    assert dispositions["correctness.bad-state"]["author"] == "owner"


def test_ledger_deduplicates_run_attempts_and_writes_jsonl(tmp_path):
    module = _module()
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=_audit("sha", []), prior_entries=[], dispositions={},
    )
    output = tmp_path / "ledger.jsonl"
    module.write_ledger(output, [entry, entry], max_entries=2000)

    lines = output.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["review"]["status"] == "pass"


def test_cross_host_artifact_redirect_strips_github_authorization():
    module = _module()
    handler = module.CrossHostAuthStripRedirectHandler()
    original = urllib.request.Request(
        "https://api.github.com/repos/zlxlabs/app/actions/artifacts/1/zip",
        headers={"Authorization": "Bearer secret", "Accept": "application/json"},
    )

    redirected = handler.redirect_request(
        original,
        None,
        302,
        "Found",
        {"Location": "https://artifactcache.example.test/signed"},
        "https://artifactcache.example.test/signed",
    )

    assert redirected is not None
    assert redirected.get_header("Authorization") is None
    assert redirected.get_header("Accept") == "application/json"


def test_bot_sticky_state_survives_reruns_but_user_spoof_is_ignored():
    module = _module()
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="same", preflight={}, audit=_audit("same", ["a"]), prior_entries=[], dispositions={},
    )
    body = module.render_state_comment([entry], entry)
    comments = [
        {"body": body, "user": {"login": "owner", "type": "User"}},
        {"body": body, "user": {"login": "github-actions[bot]", "type": "Bot"}},
    ]

    restored = module.parse_state_entries(comments)

    assert restored == [entry]
    assert "Review ledger state" in body
    assert "same" in body
