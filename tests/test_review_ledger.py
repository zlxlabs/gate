import importlib.util
import json
import urllib.request
import zipfile
from pathlib import Path

import pytest


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


def _preflight(diff_lines: int = 100, *, plan: str = "single") -> dict:
    return {"diff_lines": diff_lines, "classification": plan, "review_plan": plan, "thresholds": {"single_turn_lines": 4000}}


def _v2_audit(verdict: str, *, cost=None, tokens=None, runtime=None, expected_shadows=None) -> dict:
    result = None if verdict not in {"pass", "fail"} else {
        "verdict": verdict,
        "summary": "result",
        "findings": [] if verdict == "pass" else [
            {"id": "correctness.bad-state", "severity": "major", "category": "correctness"}
        ],
    }
    audit = {
        "kind": "primary_review", "schema_version": 1,
        "repository_id": 123, "repository": "zlxlabs/app", "pr": 7,
        "base_sha": "base", "head_sha": "head", "diff_digest": "d" * 64,
        "policy_version": "v1", "policy_digest": "e" * 64,
        "registry_commit": "a" * 40, "caller_sha": "b" * 40,
        "reusable_workflow_sha": "c" * 40, "run_id": 10, "run_attempt": 1,
        "job_id": 99, "reviewer": None if verdict in {"not_expected", "waived"} else "codex-sub",
        "verdict": verdict, "attempts": [], "shadow_mode": "detached",
        "expected_shadows": [] if expected_shadows is None else expected_shadows,
        "result": result, "cost": cost, "tokens": tokens, "runtime": runtime,
        "merge_base_sha": "merge-base", "candidate_commit_sha": "candidate-commit",
        "candidate_tree_sha": "candidate-tree", "run_mode": "PAYLOAD_ONLY",
    }
    if verdict == "not_expected":
        audit["not_expected_reason"] = "hosted_runner"
    elif verdict == "waived":
        audit["waiver"] = {"approver": "owner", "approved_at": "2026-08-09T00:00:00Z", "reason": "test"}
    return audit


EXPECTED_IDENTITY = {
    "repository_id": 123,
    "base_sha": "base",
    "caller_sha": "b" * 40,
    "reusable_workflow_sha": "c" * 40,
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


@pytest.mark.parametrize("verdict", ["pass", "fail", "unavailable", "not_expected", "waived"])
def test_v2_primary_audit_projects_verdict_and_identity(verdict):
    module = _module()
    audit = _v2_audit(verdict)
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight=_preflight(), audit=audit,
        prior_entries=[], dispositions={},
    )

    assert entry["review"]["status"] == verdict
    assert entry["review"]["verdict"] == verdict
    assert entry["primary_identity"] == {key: audit[key] for key in module.PRIMARY_IDENTITY_FIELDS if key in audit}
    if verdict in {"not_expected", "waived"}: assert entry["review"]["result"] is None and entry["review"]["finding_count"] == 0


@pytest.mark.parametrize("diff_lines,plan,mode,complete,shards", [(100, "single", "single", True, 1), (5000, "sharded", "sharded+cross-module integration", False, None)])
def test_v2_review_preserves_result_and_recomputes_legacy_coverage(
    diff_lines, plan, mode, complete, shards,
):
    module = _module()
    audit = _v2_audit("fail", cost=1.25, tokens=[{"input": 3}], runtime={"duration_s": 12.5})
    audit["attempts"] = [{"reviewer": "codex-sub", "exit_code": 0, "reason": "", "duration_s": 12.5, "cost_usd": 1.25}]
    entry = module.build_entry(repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1, head_sha="head", preflight=_preflight(diff_lines, plan=plan), audit=audit, prior_entries=[], dispositions={})

    review = entry["review"]
    assert review["result"] == audit["result"]
    assert review["cost_usd"] == 1.25
    assert review["tokens"] == [{"input": 3}]
    assert review["runtime"] == {"duration_s": 12.5}
    assert review["coverage"] == {
        "mode": mode, "complete": complete, "diff_lines": diff_lines, "shards": shards,
    }
    assert review["shadows"] == {}
    assert review["finding_count"] == 1


def test_v2_runtime_rejects_attempt_duration_sum_mismatch():
    module = _module()
    audit = _v2_audit("pass", runtime={"duration_s": 1})
    audit["attempts"] = [{"reviewer": "codex-sub", "exit_code": 0, "reason": "", "duration_s": 2, "cost_usd": None}]
    with pytest.raises(ValueError, match="runtime"):
        module.build_entry(repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1, head_sha="head", preflight=_preflight(), audit=audit, prior_entries=[], dispositions={})


@pytest.mark.parametrize("field,value", [("cost", -1), ("cost", float("inf")), ("tokens", {}), ("runtime", {"duration_s": -1}), ("runtime", {"duration_s": float("nan")}), ("expected_shadows", ["claude-glm"])])
def test_v2_review_rejects_invalid_telemetry(field, value):
    module = _module()
    audit = _v2_audit("pass", **{field: value})
    with pytest.raises(ValueError, match="canonical primary"):
        module.build_entry(repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1, head_sha="head", preflight=_preflight(), audit=audit, prior_entries=[], dispositions={})


@pytest.mark.parametrize("mutation", [lambda audit: audit["result"].pop("findings"), lambda audit: audit["result"]["findings"].append({"id": "broken"}), lambda audit: audit["attempts"].append({"reviewer": 3})])
def test_v2_review_rejects_malformed_consumed_payload(mutation):
    module = _module()
    audit = _v2_audit("pass")
    mutation(audit)
    with pytest.raises(ValueError, match="canonical primary"):
        module.build_entry(repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1, head_sha="head", preflight=_preflight(), audit=audit, prior_entries=[], dispositions={})


def test_v2_primary_audit_rejects_mismatched_parent_identity():
    module = _module()
    audit = _v2_audit("pass")
    audit["head_sha"] = "stale"

    with pytest.raises(ValueError, match="primary audit identity mismatch"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit, prior_entries=[], dispositions={},
        )


@pytest.mark.parametrize("field", list(EXPECTED_IDENTITY))
def test_v2_primary_audit_binds_every_workflow_identity_field(field):
    module = _module()
    audit = _v2_audit("pass")
    audit[field] = 999 if isinstance(EXPECTED_IDENTITY[field], int) else "stale"

    with pytest.raises(ValueError, match="primary audit identity mismatch"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit, prior_entries=[], dispositions={},
            expected_identity=EXPECTED_IDENTITY,
        )


@pytest.mark.parametrize("field,value", [("diff_digest", "D" * 64), ("policy_digest", "D" * 64), ("candidate_tree_sha", None)])
def test_v2_primary_audit_rejects_malformed_canonical_shape(field, value):
    module = _module()
    audit = _v2_audit("pass")
    if value is None:
        del audit[field]
    else:
        audit[field] = value

    with pytest.raises(ValueError, match="canonical primary"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit, prior_entries=[], dispositions={},
        )


@pytest.mark.parametrize(
    "verdict,field",
    [(verdict, field) for verdict in ["pass", "fail", "unavailable"] for field in ["waiver", "not_expected_reason"]]
    + [("not_expected", "waiver"), ("waived", "not_expected_reason")],
)
def test_v2_primary_audit_rejects_companion_fields_for_wrong_verdict(verdict, field):
    module = _module()
    audit = _v2_audit(verdict)
    audit[field] = {} if field == "waiver" else "hosted_runner"

    with pytest.raises(ValueError, match="canonical primary"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit, prior_entries=[], dispositions={},
        )


def test_fetch_prior_entries_fails_on_corrupt_artifact(monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "_api_json", lambda token, url: {
        "artifacts": [{"id": 1, "expired": False, "archive_download_url": "https://example.test/1"}]
    })
    monkeypatch.setattr(module, "_api_request", lambda token, url: b"not a zip")

    with pytest.raises(zipfile.BadZipFile):
        module.fetch_prior_entries("token", "zlxlabs/app")


@pytest.mark.parametrize("max_entries", [0, -1])
def test_write_ledger_rejects_nonpositive_capacity(tmp_path, max_entries):
    module = _module()
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=_audit("sha", []), prior_entries=[], dispositions={},
    )

    with pytest.raises(ValueError, match="max_entries"):
        module.write_ledger(tmp_path / "ledger.jsonl", [entry], max_entries=max_entries)


@pytest.mark.parametrize("count,capacity", [(2000, 2000), (2001, 2000)])
def test_write_ledger_capacity_boundary(tmp_path, count, capacity):
    module = _module()
    entries = [{"repository": "zlxlabs/app", "run_id": index, "run_attempt": 1,
                "recorded_at": str(index)} for index in range(count)]
    output = tmp_path / "ledger.jsonl"
    if count == capacity:
        module.write_ledger(output, entries, max_entries=capacity)
        assert len(output.read_text().splitlines()) == capacity
    else:
        with pytest.raises(ValueError, match="max_entries"):
            module.write_ledger(output, entries, max_entries=capacity)
        assert not output.exists()


def test_ledger_preserves_conflicting_run_attempt_variants():
    module = _module()
    first = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight=_preflight(), audit=_v2_audit("pass"),
        prior_entries=[], dispositions={},
    )
    second = json.loads(json.dumps(first))
    second["review"]["status"] = "fail"

    variants = module.dedupe_entries([first, second])

    assert len(variants) == 2
    assert all(item["ledger_conflict"]["variant_count"] == 2 for item in variants)
    survivor = module.dedupe_entries([variants[0]])
    assert survivor[0]["ledger_conflict"]["present_variant_count"] == 1

    current = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=11, run_attempt=1,
        head_sha="next", preflight={}, audit=_audit("next", []),
        prior_entries=survivor, dispositions={},
    )
    assert current["comparison"]["kind"] == "prior_conflict"
    assert current["review_round"] == 2
    recovered = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=12, run_attempt=1, head_sha="later", preflight={}, audit=_audit("later", []), prior_entries=[*variants, current], dispositions={},
    )
    assert recovered["comparison"]["kind"] == "new_head"


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


def test_review_summary_includes_reviewer_attempts_and_failover_from_audit():
    """P0: ledger review block must carry who ran, hop path, and whether failover happened."""
    module = _module()
    audit = _audit("sha", [])
    audit["reviewer"] = "codex-sub"
    audit["attempts"] = [
        {
            "reviewer": "claude-glm",
            "exit_code": 20,
            "reason": "Claude 模型服务过载（529）",
            "cost_usd": 0,
            "tokens": None,
            "duration_s": 175,
            "diag_snippet": "api_error_status=529 该模型当前访问量过大，请您稍后再试",
        },
        {
            "reviewer": "codex-sub",
            "exit_code": 0,
            "reason": "",
            "cost_usd": None,
            "tokens": None,
            "duration_s": 42,
            "diag_snippet": None,
        },
    ]
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=audit, prior_entries=[], dispositions={},
    )
    review = entry["review"]
    assert review["reviewer"] == "codex-sub"
    assert review["failover"] is True
    assert review["attempts"] == [
        {
            "reviewer": "claude-glm",
            "exit_code": 20,
            "reason": "Claude 模型服务过载（529）",
            "duration_s": 175,
            "cost_usd": 0,
            "diag_snippet": "api_error_status=529 该模型当前访问量过大，请您稍后再试",
        },
        {
            "reviewer": "codex-sub",
            "exit_code": 0,
            "reason": "",
            "duration_s": 42,
            "cost_usd": None,
            "diag_snippet": None,
        },
    ]


def test_review_summary_no_failover_when_single_successful_hop():
    module = _module()
    audit = _audit("sha", [])
    audit["reviewer"] = "claude-glm"
    audit["attempts"] = [
        {"reviewer": "claude-glm", "exit_code": 0, "reason": "", "duration_s": 12, "cost_usd": 0.9},
    ]
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=audit, prior_entries=[], dispositions={},
    )
    assert entry["review"]["reviewer"] == "claude-glm"
    assert entry["review"]["failover"] is False
    assert len(entry["review"]["attempts"]) == 1


def test_review_summary_defaults_when_audit_missing():
    module = _module()
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=None, prior_entries=[], dispositions={},
        fallback_status="not_run",
    )
    assert entry["review"]["reviewer"] is None
    assert entry["review"]["failover"] is False
    assert entry["review"]["attempts"] == []


def test_state_comment_and_summary_mention_reviewer_on_failover():
    module = _module()
    audit = _audit("sha", [])
    audit["reviewer"] = "codex-sub"
    audit["attempts"] = [
        {"reviewer": "claude-glm", "exit_code": 20, "reason": "限流", "duration_s": 1},
        {"reviewer": "codex-sub", "exit_code": 0, "reason": "", "duration_s": 2},
    ]
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=audit, prior_entries=[], dispositions={},
    )
    body = module.render_state_comment([entry], entry)
    assert "codex-sub" in body
    assert "failover" in body.lower() or "切换" in body
