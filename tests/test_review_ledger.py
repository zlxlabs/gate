import base64
import hashlib
import importlib.util
import inspect
import io
import json
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import pytest

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "actions" / "review-ledger" / "build_ledger.py"
PRIMARY_REVIEW_V2_TIER_SOURCE_URL = (
    "https://github.com/zlxlabs/fd-satisfaction-survey/actions/runs/32446501755/"
    "artifacts/9434236632"
)


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


def test_convergence_projection_is_observational_only():
    module = _module()
    disposition = {
        "disposition": "false-positive",
        "reason": "locked upstream behavior",
        "status": "active_false_positive",
    }
    projected = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight={}, audit=_audit("head", ["a"]),
        prior_entries=[], dispositions={"a": disposition},
    )
    without_projection = dict(projected)
    without_projection.pop("convergence_projection")
    assert projected["convergence_projection"] == {
        "source": "disposition-observation",
        "required_gate_effect": "none",
        "statuses": {
            "a": {
                "status": "active_false_positive",
                "reason": "locked upstream behavior",
            },
        },
    }
    assert without_projection["review"] == projected["review"]
    assert without_projection["comparison"] == projected["comparison"]
    assert projected["false_positive_count"] == 1


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


def test_review_ledger_consumes_real_primary_v2_tier_artifact_bytes():
    module = _module()
    fixture_bytes = (ROOT / "tests/data/primary_review_v2_with_tier.json").read_bytes()
    assert hashlib.sha256(fixture_bytes).hexdigest() == (
        "5f8beb2607fd3cf7a79b0adb539d85a68ee189aed463bd08932835a436f3abb8"
    )
    audit = json.loads(fixture_bytes)

    entry = module.build_entry(
        repository=audit["repository"], pr_number=audit["pr"], run_id=audit["run_id"],
        run_attempt=audit["run_attempt"], head_sha=audit["head_sha"], preflight=_preflight(),
        audit=audit, prior_entries=[], dispositions={},
    )

    assert audit["run_id"] == 32446501755, PRIMARY_REVIEW_V2_TIER_SOURCE_URL
    assert entry["primary_identity"]["tier"] == "internal"


@pytest.mark.parametrize("tier", ["personal", "internal", "saas"])
@pytest.mark.parametrize("verdict", ["pass", "not_expected"])
def test_primary_v2_tier_accepts_domain_values_for_review_and_no_review(verdict, tier):
    module = _module()
    audit = _v2_audit(verdict)
    audit.update(schema_version=2, tier=tier)

    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight=_preflight(), audit=audit,
        prior_entries=[], dispositions={},
    )

    assert entry["primary_identity"]["tier"] == tier


@pytest.mark.parametrize("tier", [None, "", 1, True, "enterprise"])
def test_primary_v2_tier_rejects_invalid_domain_values(tier):
    module = _module()
    audit = _v2_audit("pass")
    audit.update(schema_version=2, tier=tier)

    with pytest.raises(ValueError, match="canonical primary tier"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit,
            prior_entries=[], dispositions={},
        )


def test_primary_v2_tier_does_not_allow_other_unknown_fields():
    module = _module()
    audit = _v2_audit("pass")
    audit.update(schema_version=2, tier="personal", unexpected_primary_field="value")

    with pytest.raises(ValueError, match="extra=.*unexpected_primary_field"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit,
            prior_entries=[], dispositions={},
        )


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


def test_v2_detached_shadows_are_recorded_as_unavailable_without_losing_expectations():
    module = _module()
    audit = _v2_audit("pass", expected_shadows=["claude-glm", "gemini-pro"])

    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight=_preflight(), audit=audit,
        prior_entries=[], dispositions={},
    )

    assert entry["review"]["shadows"] == {
        "shadow_mode": "detached",
        "status": "detached_unavailable",
        "expected_shadows": ["claude-glm", "gemini-pro"],
        "outcomes": None,
    }
    assert entry["review"]["shadows"] != {}


@pytest.mark.parametrize("value", [None, {}, "claude-glm", [""], ["claude-glm", 3]])
def test_v2_detached_shadows_reject_malformed_expected_names(value):
    module = _module()
    audit = _v2_audit("pass")
    audit["expected_shadows"] = value

    with pytest.raises(ValueError, match="expected_shadows"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit,
            prior_entries=[], dispositions={},
        )


def test_v2_detached_shadows_reject_non_detached_mode():
    module = _module()
    audit = _v2_audit("pass", expected_shadows=["claude-glm"])
    audit["shadow_mode"] = "inline"

    with pytest.raises(ValueError, match="shadow_mode"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit,
            prior_entries=[], dispositions={},
        )


def test_v2_runtime_rejects_attempt_duration_sum_mismatch():
    module = _module()
    audit = _v2_audit("pass", runtime={"duration_s": 1})
    audit["attempts"] = [{"reviewer": "codex-sub", "exit_code": 0, "reason": "", "duration_s": 2, "cost_usd": None}]
    with pytest.raises(ValueError, match="runtime"):
        module.build_entry(repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1, head_sha="head", preflight=_preflight(), audit=audit, prior_entries=[], dispositions={})


def _ledger_runtime_case(schema_version, verdict, runtime_case):
    reviewer_verdict = verdict not in {"not_expected", "waived"}
    audit = _v2_audit(verdict, runtime=None)
    audit["schema_version"] = schema_version
    if reviewer_verdict and runtime_case in {"valid", "mismatch"}:
        audit["attempts"] = [{"reviewer": "codex-sub", "exit_code": 0, "reason": "", "duration_s": 1.0, "cost_usd": None}]
    if runtime_case == "missing":
        audit.pop("runtime", None)
    else:
        audit["runtime"] = {"null": None, "valid": {"duration_s": 1.0},
                              "mismatch": {"duration_s": 2.0}, "invalid": {"duration_s": "bad"}}[runtime_case]
    return audit


@pytest.mark.parametrize("schema_version", [1, 2, 99])
@pytest.mark.parametrize("runtime_case", ["missing", "null", "valid", "mismatch", "invalid"])
@pytest.mark.parametrize("verdict", ["pass", "fail", "unavailable", "not_expected", "waived"])
def test_ledger_runtime_schema_upgrade_matrix(schema_version, runtime_case, verdict):
    module = _module()
    reviewer_verdict = verdict not in {"not_expected", "waived"}
    expected = (
        schema_version in {1, 2}
        and (runtime_case == "null" or (runtime_case == "valid" and reviewer_verdict)
             or (runtime_case == "missing" and schema_version == 1))
    )
    audit = _ledger_runtime_case(schema_version, verdict, runtime_case)
    call = lambda: module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight=_preflight(), audit=audit, prior_entries=[], dispositions={},
    )
    if expected:
        entry = call()
        assert entry["review"]["runtime"] is None if runtime_case == "missing" else True
    else:
        with pytest.raises(ValueError, match="canonical primary|runtime|schema"):
            call()


def test_ledger_consumes_historical_v1_fixture_without_runtime():
    module = _module()
    fixture = json.loads((ROOT / "tests/data/primary_review_v1_missing_runtime.json").read_text())
    entry = module.build_entry(
        repository=fixture["repository"], pr_number=fixture["pr"], run_id=fixture["run_id"],
        run_attempt=fixture["run_attempt"], head_sha=fixture["head_sha"], preflight=_preflight(),
        audit=fixture, prior_entries=[], dispositions={},
    )
    assert entry["review"]["runtime"] is None


@pytest.mark.parametrize("field,value", [("cost", -1), ("cost", float("inf")), ("tokens", {}), ("runtime", {"duration_s": -1}), ("runtime", {"duration_s": float("nan")})])
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
    audit = _v2_audit("pass", expected_shadows=["claude-glm"])
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


def test_fetch_prior_entries_queries_the_v2_ledger_epoch(monkeypatch):
    module = _module()
    requested = []
    monkeypatch.setattr(
        module,
        "_api_json",
        lambda token, url: requested.append(url) or {"artifacts": []},
    )

    assert module.fetch_prior_entries("token", "zlxlabs/app") == []
    assert len(requested) == 2
    assert "name=codex-review-ledger-v2" in requested[0]
    assert "name=codex-review-ledger&" not in requested[0]
    assert "name=codex-review-ledger&" in requested[1]
    assert "name=codex-review-ledger-v2" not in requested[1]


def _ledger_zip_bytes(entries: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as bundle:
        bundle.writestr("ledger.jsonl", "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))
    return buf.getvalue()


def _named_entry(run_id: int, sha: str) -> dict:
    return {"repository": "zlxlabs/app", "run_id": run_id, "run_attempt": 1, "head_sha": sha}


def _entry_keys(entries: list[dict]) -> set[tuple]:
    return {(e.get("repository"), e.get("run_id"), e.get("run_attempt"), e.get("head_sha")) for e in entries}


def _patch_named_artifact_api(module, monkeypatch, artifacts_by_name: dict[str, list[list[dict]]]):
    archives, listed, artifact_id = {}, {}, 0
    for name, artifacts in artifacts_by_name.items():
        rows = []
        for entries in artifacts:
            artifact_id += 1
            url = f"https://example.test/archive/{artifact_id}"
            archives[url] = _ledger_zip_bytes(entries)
            rows.append({"id": artifact_id, "expired": False, "archive_download_url": url})
        listed[name] = rows

    def fake_json(token, url):
        name = (urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("name") or [""])[0]
        return {"artifacts": listed.get(name, [])}

    monkeypatch.setattr(module, "_api_json", fake_json)
    monkeypatch.setattr(module, "_api_request", lambda token, url, **kwargs: archives[url])


_V2, _V1, _SHARED, _V2U, _V1U = (
    _named_entry(21, "v2-only"), _named_entry(11, "v1-only"),
    _named_entry(10, "shared"), _named_entry(22, "v2-unique"), _named_entry(12, "v1-unique"),
)


@pytest.mark.parametrize(
    "artifacts_by_name, expected",
    [
        ({"codex-review-ledger-v2": [[_V2]], "codex-review-ledger": []}, [_V2]),
        ({"codex-review-ledger-v2": [], "codex-review-ledger": [[_V1]]}, [_V1]),
        (
            {"codex-review-ledger-v2": [[_SHARED, _V2U]], "codex-review-ledger": [[_SHARED, _V1U]]},
            [_SHARED, _V2U, _V1U],
        ),
        ({"codex-review-ledger-v2": [], "codex-review-ledger": []}, []),
    ],
    ids=["v2_only", "v1_only", "both_union_deduped", "neither"],
)
def test_fetch_prior_entries_reads_v1_and_v2_artifact_names(artifacts_by_name, expected, monkeypatch):
    module = _module()
    _patch_named_artifact_api(module, monkeypatch, artifacts_by_name)
    assert _entry_keys(module.fetch_prior_entries("token", "zlxlabs/app")) == _entry_keys(expected)


def test_v1_artifact_history_posts_state_comment_when_cursor_missing(monkeypatch, capsys):
    module = _module()
    historical, current = _pr_ledger_entries(module, 2)
    zip_bytes = _ledger_zip_bytes([historical])
    recorded: list[tuple[str, str, dict | None]] = []

    def fake_api_request(token, url, *, method="GET", payload=None):
        recorded.append((method, url, payload))
        parsed = urllib.parse.urlparse(url)
        name = (urllib.parse.parse_qs(parsed.query).get("name") or [""])[0]
        if parsed.path.endswith("/actions/artifacts"):
            artifacts = ([{"id": 1, "expired": False, "archive_download_url": "https://example.test/v1"}]
                         if name == "codex-review-ledger" else [])
            return json.dumps({"artifacts": artifacts}).encode()
        if url == "https://example.test/v1":
            return zip_bytes
        if "/pulls/" in parsed.path:
            return json.dumps({"head": {"sha": current["head_sha"]}}).encode()
        if method == "POST":
            return b"{}"
        raise AssertionError(f"unexpected request {method} {url}")

    monkeypatch.setattr(module, "_api_request", fake_api_request)
    prior = module.fetch_prior_entries("token", "zlxlabs/app")
    assert _entry_keys(prior) == _entry_keys([historical])
    module.post_state_comment(
        "token", "zlxlabs/app", 7, current["head_sha"],
        module.dedupe_entries([*prior, current]), current, [],
    )
    writes = [item for item in recorded if item[0] in {"POST", "PATCH", "PUT", "DELETE"}]
    assert "skip first-round review ledger state comment" not in capsys.readouterr().out
    assert len(writes) == 1 and writes[0][0] == "POST"
    assert writes[0][1] == "https://api.github.com/repos/zlxlabs/app/issues/7/comments"
    assert writes[0][2] is not None and "codex-review-ledger-state:v2:" in writes[0][2]["body"]


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


def _legacy_state_comment_body(module, entries, current):
    """Frozen copy of the pre-humanize render_state_comment layout.

    The sticky comment doubles as machine-readable cursor storage, and live PRs
    already carry comments in this exact layout. This fixture pins that layout so
    a renderer change can never silently break cursor recovery from old comments.
    """
    relevant = [
        entry for entry in entries
        if entry.get("repository") == current.get("repository")
        and entry.get("pr_number") == current.get("pr_number")
    ][-20:]
    encoded = base64.urlsafe_b64encode(
        json.dumps(relevant, ensure_ascii=False, separators=(",", ":")).encode()
    ).decode()
    review = current["review"]
    comparison = current["comparison"]
    comparison_line = comparison["kind"]
    if comparison["kind"] == "new_head":
        comparison_line += (
            f"; persistent/resolved/new = {len(comparison['persistent_finding_ids'])}/"
            f"{len(comparison['resolved_finding_ids'])}/{len(comparison['new_finding_ids'])}"
        )
    elif comparison["kind"] == "same_head_rerun":
        comparison_line += (
            f"; stable/missing/appeared = {len(comparison['persistent_finding_ids'])}/"
            f"{len(comparison['missing_finding_ids'])}/{len(comparison['appeared_finding_ids'])}"
        )
    reviewer = review.get("reviewer") or "none"
    failover = bool(review.get("failover"))
    reviewer_line = f"{reviewer}" + (" (failover)" if failover else "")
    return (
        f"{module.STATE_MARKER}\n\n### 📒 Review ledger state\n\n"
        f"- Commit: `{current['head_sha']}`\n"
        f"- Round: **{current['review_round']}**\n"
        f"- Status / findings: **{review['status']} / {review['finding_count']}**\n"
        f"- Reviewer: **{reviewer_line}**\n"
        f"- Comparison: `{comparison_line}`\n\n"
        "完整数据保存在 `codex-review-ledger-v2` artifact；此 sticky comment 仅保存 v2 epoch 的跨 rerun 连续游标。\n\n"
        f"<!-- codex-review-ledger-state:v2:{encoded} -->\n"
    )


def test_parse_state_entries_recovers_cursor_from_pre_humanize_comment():
    """Backward compat: comments already posted in the old layout must still parse."""
    module = _module()
    previous = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="old", preflight={}, audit=_audit("old", ["a", "b"]), prior_entries=[], dispositions={},
    )
    current = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=11, run_attempt=1,
        head_sha="new", preflight={}, audit=_audit("new", ["b", "c"]), prior_entries=[previous], dispositions={},
    )
    body = _legacy_state_comment_body(module, [previous, current], current)
    comments = [{"body": body, "user": {"login": "github-actions[bot]", "type": "Bot"}}]

    restored = module.parse_state_entries(comments)

    assert restored == [previous, current]


def _details_block(body: str) -> str:
    return body[body.index("<details>"):body.index("</details>")]


@pytest.mark.parametrize(
    "kind,fragment",
    [
        ("new_head", "persistent/resolved/new = 1/1/1"),
        ("same_head_rerun", "stable/missing/appeared = 1/1/1"),
    ],
)
def test_state_comment_folds_machine_details_behind_human_navigation(kind, fragment):
    module = _module()
    same_head = kind == "same_head_rerun"
    # Non-default fixture values so rendered content is proven to come from render inputs.
    repository = "acme/widget"
    pr_number = 99
    previous = module.build_entry(
        repository=repository, pr_number=pr_number, run_id=10, run_attempt=1,
        head_sha="head", preflight={}, audit=_audit("head", ["a", "b"]), prior_entries=[], dispositions={},
    )
    current = module.build_entry(
        repository=repository, pr_number=pr_number, run_id=10 if same_head else 11,
        run_attempt=2 if same_head else 1, head_sha="head" if same_head else "new",
        preflight={}, audit=_audit("head" if same_head else "new", ["b", "c"]),
        prior_entries=[previous], dispositions={},
    )
    body = module.render_state_comment([previous, current], current)

    # First line stays the machine anchor — human navigation must not displace it.
    assert body.splitlines()[0] == module.STATE_MARKER
    # Human first screen: heading keeps the referenced name but cannot read as a verdict.
    assert "### ⚙️ Review ledger state（机器状态记录，非评审结论）" in body
    navigation = body[: body.index("<details>")]
    assert "机器状态记录" in navigation
    assert "不代表评审结论" in navigation
    # Must not name gate-hub-only advisory comment titles (fleet-wide dangling pointer).
    assert "Gate 当前状态" not in navigation
    # Navigation must not point anywhere: no single surface can reliably answer
    # "can this merge" across fleet deployment shapes — lock it with no-URL.
    assert "http" not in navigation
    # Machine details are folded but still complete (six items incl. artifact note).
    assert "<details><summary>机器状态明细</summary>" in body
    details = _details_block(body)
    for item in ("- Commit:", "- Round:", "- Status / findings:", "- Reviewer:", "- Comparison:"):
        assert item in details
    assert fragment in details
    assert "完整数据保存在 `codex-review-ledger-v2` artifact" in details
    # Cursor comment stays last, byte-stable, and decodes back to the entry list.
    match = module.STATE_RE.search(body)
    assert match is not None
    assert body.endswith(match.group(0) + "\n")
    payload = base64.urlsafe_b64decode(match.group(1).encode())
    assert json.loads(payload) == [previous, current]


def test_state_comment_renders_failover_reviewer_inside_details():
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

    details = _details_block(body)
    assert "- Reviewer: **codex-sub (failover)**" in details


def test_v2_state_marker_does_not_restore_the_legacy_epoch():
    module = _module()
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="same", preflight={}, audit=_audit("same", ["a"]), prior_entries=[], dispositions={},
    )
    body = module.render_state_comment([entry], entry)

    assert module.parse_state_entries([
        {
            "body": "<!-- codex-review-ledger-state:v1:W3sicmVwb3NpdG9yeSI6InpseGxhYnMvYXBwIiwicHJfbnVtYmVyIjo3LCJydW5faWQiOjEwfV0= -->",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        }
    ]) == []
    assert "codex-review-ledger-state:v2:" in body
    assert "codex-review-ledger-state:v1:" not in body
    assert "codex-review-ledger-v2" in body


def test_sticky_comment_scrub_failure_prevents_github_write(monkeypatch):
    module = _module()
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight={}, audit=_audit("head", []), prior_entries=[], dispositions={},
    )
    calls = []

    monkeypatch.setattr(module, "scrub_for_publish", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("scrub failed")))
    monkeypatch.setattr(module, "_api_json", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(module, "_api_request", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(RuntimeError, match="scrub failed"):
        module.post_state_comment(
            "token", "org/repo", 7, "head", [entry], entry, [],
        )

    assert calls == []


def test_sticky_comment_sends_scrubbed_body(monkeypatch):
    module = _module()
    previous = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=9, run_attempt=1,
        head_sha="old", preflight={}, audit=_audit("old", []), prior_entries=[], dispositions={},
    )
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight={}, audit=_audit("head", []), prior_entries=[previous], dispositions={},
    )
    entry["review"]["reviewer"] = "runner-secret"
    writes = []

    class Response:
        def __init__(self, payload=b""):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.payload

    def fake_urlopen(request, timeout):
        if request.get_method() == "GET":
            return Response(b'{"head":{"sha":"head"}}')
        writes.append(json.loads(request.data.decode()))
        return Response(b"{}")

    monkeypatch.setenv("RUNNER_NAME", "runner-secret")
    monkeypatch.setattr(module.URL_OPENER, "open", fake_urlopen)

    module.post_state_comment("token", "org/repo", 7, "head", [previous, entry], entry, [])

    assert len(writes) == 1
    body = writes[0]["body"]
    assert "runner-secret" not in body
    assert "[REDACTED:RUNNER_NAME]" in body


def _pr_ledger_entries(module, count: int) -> list[dict]:
    entries = []
    for index in range(count):
        sha = "head" if index == count - 1 else f"old{index}"
        entries.append(
            module.build_entry(
                repository="zlxlabs/app",
                pr_number=7,
                run_id=10 + index,
                run_attempt=1,
                head_sha=sha,
                preflight={},
                audit=_audit(sha, []),
                prior_entries=entries,
                dispositions={},
            )
        )
    return entries


def _same_repo_other_pr_entry(module) -> dict:
    """Noise that a repository-only filter would keep."""
    return module.build_entry(
        repository="zlxlabs/app",
        pr_number=99,
        run_id=1,
        run_attempt=1,
        head_sha="other-pr",
        preflight={},
        audit=_audit("other-pr", []),
        prior_entries=[],
        dispositions={},
    )


def _other_repo_same_pr_entry(module) -> dict:
    """Noise that a pr_number-only filter would keep."""
    return module.build_entry(
        repository="other/repo",
        pr_number=7,
        run_id=2,
        run_attempt=1,
        head_sha="other-repo",
        preflight={},
        audit=_audit("other-repo", []),
        prior_entries=[],
        dispositions={},
    )


def _cross_key_noise_entries(module) -> list[dict]:
    return [_same_repo_other_pr_entry(module), _other_repo_same_pr_entry(module)]


@pytest.mark.parametrize(
    "has_existing, entry_count, expected_write",
    [
        (False, 1, None),
        (False, 2, "POST"),
        (True, 1, "PATCH"),
        (True, 2, "PATCH"),
    ],
    ids=[
        "no_comment_one_entry",
        "no_comment_two_entries",
        "has_comment_one_entry",
        "has_comment_two_entries",
    ],
)
def test_post_state_comment_create_or_skip_matrix(
    has_existing, entry_count, expected_write, monkeypatch, capsys,
):
    module = _module()
    same_pr = _pr_ledger_entries(module, entry_count)
    current = same_pr[-1]
    entries = [*_cross_key_noise_entries(module), *same_pr]
    comments = [{"id": 99, "body": f"{module.STATE_MARKER}\n\nold\n"}] if has_existing else []
    recorded: list[tuple[str, str, dict | None]] = []

    def fake_api_request(token, url, *, method="GET", payload=None):
        recorded.append((method, url, payload))
        if method == "GET":
            return json.dumps({"head": {"sha": current["head_sha"]}}).encode()
        return b"{}"

    monkeypatch.setattr(module, "_api_request", fake_api_request)
    module.post_state_comment(
        "token", "zlxlabs/app", 7, current["head_sha"], entries, current, comments,
    )

    writes = [item for item in recorded if item[0] in {"POST", "PATCH", "PUT", "DELETE"}]
    output = capsys.readouterr().out
    if expected_write is None:
        assert writes == []
        assert (
            "::notice::skip first-round review ledger state comment; no prior history to persist"
            in output
        )
        return

    assert "skip first-round review ledger state comment" not in output
    assert len(writes) == 1
    method, url, payload = writes[0]
    assert method == expected_write
    assert payload is not None
    if expected_write == "POST":
        assert url == "https://api.github.com/repos/zlxlabs/app/issues/7/comments"
        restored = module.parse_state_entries(
            [{
                "body": payload["body"],
                "user": {"login": "github-actions[bot]", "type": "Bot"},
            }]
        )
        assert restored == same_pr
        assert "codex-review-ledger-state:v2:" in payload["body"]
    else:
        assert url == "https://api.github.com/repos/zlxlabs/app/issues/comments/99"
        assert not any(item[0] == "POST" for item in recorded)


def test_post_state_comment_skips_when_live_head_advanced(monkeypatch, capsys):
    module = _module()
    same_pr = _pr_ledger_entries(module, 2)
    current = same_pr[-1]
    entries = [*_cross_key_noise_entries(module), *same_pr]
    recorded: list[tuple[str, str, dict | None]] = []

    def fake_api_request(token, url, *, method="GET", payload=None):
        recorded.append((method, url, payload))
        if method == "GET":
            return json.dumps({"head": {"sha": "live-new-head"}}).encode()
        return b"{}"

    monkeypatch.setattr(module, "_api_request", fake_api_request)
    module.post_state_comment(
        "token", "zlxlabs/app", 7, current["head_sha"], entries, current, [],
    )

    writes = [item for item in recorded if item[0] in {"POST", "PATCH", "PUT", "DELETE"}]
    assert writes == []
    assert "skip stale review ledger state; PR head advanced" in capsys.readouterr().out


def test_render_and_post_share_relevant_pr_entries_filter(monkeypatch, capsys):
    module = _module()
    noise = _cross_key_noise_entries(module)

    def run_post(entries, current):
        recorded: list[tuple[str, str, dict | None]] = []

        def fake_api_request(token, url, *, method="GET", payload=None):
            recorded.append((method, url, payload))
            if method == "GET":
                return json.dumps({"head": {"sha": current["head_sha"]}}).encode()
            return b"{}"

        monkeypatch.setattr(module, "_api_request", fake_api_request)
        module.post_state_comment(
            "token", "zlxlabs/app", 7, current["head_sha"], entries, current, [],
        )
        return recorded, capsys.readouterr().out

    same_pr_one = _pr_ledger_entries(module, 1)
    current_one = same_pr_one[-1]
    recorded, output = run_post([*noise, *same_pr_one], current_one)
    writes = [item for item in recorded if item[0] in {"POST", "PATCH", "PUT", "DELETE"}]
    assert writes == []
    assert (
        "::notice::skip first-round review ledger state comment; no prior history to persist"
        in output
    )

    same_pr = _pr_ledger_entries(module, 2)
    current = same_pr[-1]
    entries = [*noise, *same_pr]
    expected = [
        entry for entry in entries
        if entry.get("repository") == current.get("repository")
        and entry.get("pr_number") == current.get("pr_number")
    ]
    assert len(expected) >= 2
    assert len(entries) > len(expected)
    recorded, output = run_post(entries, current)
    posts = [item for item in recorded if item[0] == "POST"]
    assert len(posts) == 1
    assert "skip first-round review ledger state comment" not in output
    restored = module.parse_state_entries(
        [{
            "body": posts[0][2]["body"],
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        }]
    )
    assert restored == expected
    assert restored == same_pr


def test_step_summary_scrubs_runtime_values(monkeypatch, tmp_path):
    module = _module()
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight={}, audit=_audit("head", []), prior_entries=[], dispositions={},
    )
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("RUNNER_NAME", "runner-secret")
    entry["review"]["reviewer"] = "runner-secret"

    module._append_summary(entry, str(summary))

    text = summary.read_text()
    assert "runner-secret" not in text
    assert "[REDACTED:RUNNER_NAME]" in text


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


def _aggregator():
    path = ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"
    spec = importlib.util.spec_from_file_location("gate_aggregate_for_ledger", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _producer_terminal(*, receipts=()):
    agg = _aggregator()
    conv = agg._CONVERGENCE
    identity = agg.Identity(
        repository_id=123, head_sha="a" * 40, run_id=999, run_attempt=1, pr=42,
    )
    audit = {
        "kind": "primary_review",
        "schema_version": 1,
        "repository_id": identity.repository_id,
        "head_sha": identity.head_sha,
        "run_id": identity.run_id,
        "run_attempt": identity.run_attempt,
        "pr": identity.pr,
        "verdict": "fail",
        "reviewer": "claude-glm",
        "base_sha": "b" * 40,
        "diff_digest": "d" * 64,
        "policy_version": "policy-v1",
        "policy_digest": "p" * 64,
        "tier": "personal",
        "caller_sha": "c" * 40,
        "reusable_workflow_sha": "w" * 40,
        "result": {"findings": [{"id": "p1", "severity": "major"}]},
    }
    scope, missing = agg._convergence_scope_from_audit(audit, identity)
    assert not missing and scope is not None
    digest = "a" * 64
    typed_receipts = []
    for changes in receipts:
        fields = dict(
            schema_version=conv.DISPOSITION_RECEIPT_SCHEMA_VERSION,
            disposition="false-positive",
            repository_id=str(identity.repository_id),
            pr_number=identity.pr,
            epoch=conv.derive_epoch(scope),
            head_sha=identity.head_sha,
            audit_digest=digest,
            finding_id="p1",
            reason="locked upstream behavior",
            approver="octocat",
            approver_id=1,
            approved_at="2026-08-30T12:00:00Z",
        )
        fields.update(changes)
        typed_receipts.append(conv.DispositionReceipt(**fields))
    outcome = agg.evaluate(
        quality_result="success",
        primary_result="failure",
        runner="self",
        is_draft=False,
        review_expected=True,
        audit=audit,
        audit_error=None,
        identity=identity,
        audit_source_attempt=identity.run_attempt,
        audit_artifact_name="primary-audit-v2-1",
        scope=scope,
        audit_digest=digest,
        waiver_receipts=tuple(typed_receipts),
    )
    terminal = agg.build_terminal_envelope(
        repository="zlxlabs/gate", identity=identity, quality_result="success",
        primary_result="failure", review_expected=True, is_draft=False, runner="self",
        outcome=outcome,
    )
    return agg, conv, identity, typed_receipts, outcome, terminal


def test_ledger_projects_real_producer_terminal_consumption():
    module = _module()
    agg, conv, identity, receipts, outcome, terminal = _producer_terminal(receipts=[{}])
    assert outcome.disposition_consumption is not None
    assert terminal["disposition_receipt_consumption"]["consumed_count"] == 1
    entry = module.build_entry(
        repository=terminal["repository"],
        pr_number=terminal["pr_number"],
        run_id=terminal["run_id"],
        run_attempt=terminal["run_attempt"],
        head_sha=terminal["head_sha"],
        preflight={},
        audit=None,
        prior_entries=[],
        dispositions={},
        terminal_envelope=terminal,
    )
    assert entry["disposition_receipt_consumption"] == terminal["disposition_receipt_consumption"]
    assert entry["disposition_receipt_consumption"]["resolved"] == [
        {
            "finding_id": "p1",
            "receipt": conv.disposition_receipt_artifact_name(receipts[0]),
            "approver": "octocat",
            "approver_id": 1,
            "approved_at": "2026-08-30T12:00:00Z",
            "reason": "locked upstream behavior",
        }
    ]
    assert "resolved by receipt" not in json.dumps(entry["disposition_receipt_consumption"])


def test_ledger_empty_consumption_when_producer_had_no_receipts():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal()
    entry = module.build_entry(
        repository=terminal["repository"],
        pr_number=terminal["pr_number"],
        run_id=terminal["run_id"],
        run_attempt=terminal["run_attempt"],
        head_sha=terminal["head_sha"],
        preflight={},
        audit=None,
        prior_entries=[],
        dispositions={},
        terminal_envelope=terminal,
    )
    expected = module.empty_disposition_receipt_consumption()
    assert terminal["disposition_receipt_consumption"] == expected
    assert entry["disposition_receipt_consumption"] == expected


def test_ledger_omits_consumption_when_terminal_is_absent():
    module = _module()
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=_audit("sha", []), prior_entries=[], dispositions={},
    )
    assert "disposition_receipt_consumption" not in entry


def test_disposition_consumption_stays_out_of_review_summary_and_compact_attempts():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal(receipts=[{}])
    entry = module.build_entry(
        repository=terminal["repository"],
        pr_number=terminal["pr_number"],
        run_id=terminal["run_id"],
        run_attempt=terminal["run_attempt"],
        head_sha=terminal["head_sha"],
        preflight={},
        audit=None,
        prior_entries=[],
        dispositions={},
        terminal_envelope=terminal,
    )
    assert "disposition_receipt_consumption" in entry
    assert "disposition_receipt_consumption" not in entry["review"]
    for attempt in entry["review"]["attempts"]:
        assert "disposition_receipt_consumption" not in attempt
    assert "disposition_receipt_consumption" not in inspect.getsource(module._review_summary)
    assert "disposition_receipt_consumption" not in inspect.getsource(module._compact_attempts)


def test_comment_dispositions_remain_a_separate_channel_from_receipt_consumption():
    module = _module()
    dispositions = {
        "p1": {
            "disposition": "false-positive",
            "reason": "comment observation",
            "status": "active_false_positive",
        }
    }
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=_audit("sha", ["p1"]),
        prior_entries=[], dispositions=dispositions,
    )
    assert entry["finding_dispositions"]["p1"]["reason"] == "comment observation"
    assert entry["convergence_projection"]["source"] == "disposition-observation"
    assert entry["convergence_projection"]["required_gate_effect"] == "none"
    assert "disposition_receipt_consumption" not in entry


def _write_terminal(tmp_path, payload):
    path = tmp_path / "gate-terminal.json"
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    else:
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def test_missing_or_empty_terminal_file_is_fail_loud_not_empty_consumption(tmp_path):
    module = _module()
    missing = tmp_path / "missing" / "gate-terminal.json"
    empty = tmp_path / "gate-terminal.json"
    empty.write_text("")
    with pytest.raises(ValueError, match="missing or empty"):
        module.load_gate_terminal_envelope(missing)
    with pytest.raises(ValueError, match="missing or empty"):
        module.load_gate_terminal_envelope(empty)


def test_corrupt_terminal_json_is_fail_loud_not_empty_consumption(tmp_path):
    module = _module()
    path = _write_terminal(tmp_path, "{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        module.load_gate_terminal_envelope(path)


def test_terminal_array_payload_is_fail_loud(tmp_path):
    module = _module()
    path = _write_terminal(tmp_path, "[]")
    with pytest.raises(ValueError, match="not a JSON object"):
        module.load_gate_terminal_envelope(path)


def test_missing_consumption_block_is_fail_loud_not_empty_default():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal(receipts=[{}])
    del terminal["disposition_receipt_consumption"]
    with pytest.raises(ValueError, match="missing disposition_receipt_consumption"):
        module.build_entry(
            repository=terminal["repository"],
            pr_number=terminal["pr_number"],
            run_id=terminal["run_id"],
            run_attempt=terminal["run_attempt"],
            head_sha=terminal["head_sha"],
            preflight={},
            audit=None,
            prior_entries=[],
            dispositions={},
            terminal_envelope=terminal,
        )


@pytest.mark.parametrize("kind, match", [
    ("resolved_not_array", "resolved must be an array"),
    ("missing_receipt", "missing receipt"),
    ("approver_id", "approver_id must be a positive integer"),
    ("consumed_count", "consumed_count does not match resolved"),
    ("rejected_count", "rejected_count does not match rejected_reasons"),
    ("fail_closed", "fail_closed must be a boolean"),
])
def test_validator_rejects_malformed_consumption_shapes(kind, match):
    module = _module()
    *_rest, terminal = _producer_terminal(receipts=[{}])
    block = json.loads(json.dumps(terminal["disposition_receipt_consumption"]))
    if kind == "resolved_not_array":
        block["resolved"] = {}
    elif kind == "missing_receipt":
        del block["resolved"][0]["receipt"]
    elif kind == "approver_id":
        block["resolved"][0]["approver_id"] = 0
    elif kind == "consumed_count":
        block["consumed_count"] = 0
    elif kind == "rejected_count":
        block["rejected_count"] = 3
    else:
        block["fail_closed"] = "false"
    with pytest.raises(ValueError, match=match):
        module.validate_disposition_receipt_consumption(block)


def test_malformed_consumption_block_is_fail_loud():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal(receipts=[{}])
    terminal["disposition_receipt_consumption"]["consumed_count"] = "1"
    with pytest.raises(ValueError, match="consumed_count"):
        module.build_entry(
            repository=terminal["repository"],
            pr_number=terminal["pr_number"],
            run_id=terminal["run_id"],
            run_attempt=terminal["run_attempt"],
            head_sha=terminal["head_sha"],
            preflight={},
            audit=None,
            prior_entries=[],
            dispositions={},
            terminal_envelope=terminal,
        )


def test_terminal_identity_mismatch_is_fail_loud():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal(receipts=[{}])
    with pytest.raises(ValueError, match="identity mismatch"):
        module.build_entry(
            repository=terminal["repository"],
            pr_number=terminal["pr_number"],
            run_id=terminal["run_id"] + 1,
            run_attempt=terminal["run_attempt"],
            head_sha=terminal["head_sha"],
            preflight={},
            audit=None,
            prior_entries=[],
            dispositions={},
            terminal_envelope=terminal,
        )


def test_unsupported_terminal_schema_is_fail_loud(tmp_path):
    module = _module()
    path = _write_terminal(tmp_path, {"schema_version": 2, "kind": "gate_terminal"})
    with pytest.raises(ValueError, match="unsupported schema"):
        module.load_gate_terminal_envelope(path)
