import argparse
import ast
import hashlib
import importlib.util
import inspect
import json
import socket
import sys
from pathlib import Path

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

def test_install_metrics_flow_through_when_present_and_default_to_none():
    module = _module()
    with_install = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=_audit("sha", []),
        install={"ecosystem": "uv", "status": "ok", "duration_s": 42, "cache_hit": True},
    )
    without_install = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=11, run_attempt=1,
        head_sha="sha", preflight={}, audit=_audit("sha", []),
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
        head_sha="sha", preflight={}, audit=_audit("sha", []),
        install=module._load_json(missing),
    )
    assert entry["install"] is None
    assert json.loads(json.dumps(entry))["install"] is None


def test_ledger_deduplicates_run_attempts_and_writes_jsonl(tmp_path):
    module = _module()
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=_audit("sha", []),
    )
    output = tmp_path / "ledger.jsonl"
    module.write_ledger(output, entry)

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
        audit=audit,
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
        )


def test_primary_v2_tier_does_not_allow_other_unknown_fields():
    module = _module()
    audit = _v2_audit("pass")
    audit.update(schema_version=2, tier="personal", unexpected_primary_field="value")

    with pytest.raises(ValueError, match="extra=.*unexpected_primary_field"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit,
        )


@pytest.mark.parametrize("diff_lines,plan,mode,complete,shards", [(100, "single", "single", True, 1), (5000, "sharded", "sharded+cross-module integration", False, None)])
def test_v2_review_preserves_result_and_recomputes_legacy_coverage(
    diff_lines, plan, mode, complete, shards,
):
    module = _module()
    audit = _v2_audit("fail", cost=1.25, tokens=[{"input": 3}], runtime={"duration_s": 12.5})
    audit["attempts"] = [{"reviewer": "codex-sub", "exit_code": 0, "reason": "", "duration_s": 12.5, "cost_usd": 1.25}]
    entry = module.build_entry(repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1, head_sha="head", preflight=_preflight(diff_lines, plan=plan), audit=audit)

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


# ── gate#121: quality short-circuit must still write the ledger row ──────────
# Production path: primary failure skips quality, Download is skipped, Build
# still runs with a missing preflight file. main() maps that to {}. The new
# cell is selected only by an explicit input_short_circuited boolean — empty
# preflight without that flag must keep raising.


def _short_circuit_fail_audit(**kwargs):
    audit = _v2_audit("fail", runtime={"duration_s": 9.5}, **kwargs)
    audit["attempts"] = [
        {"reviewer": "codex-sub", "exit_code": 0, "reason": "", "duration_s": 9.5, "cost_usd": 0},
    ]
    audit["result"]["findings"] = [
        {
            "id": "correctness.bad-state",
            "severity": "major",
            "category": "correctness",
            "trigger_kind": "measured",
        },
        {
            "id": "security.leak",
            "severity": "blocker",
            "category": "security",
            "trigger_kind": "inferred",
        },
    ]
    return audit


def _assert_short_circuit_audit_projection(review, *, audit):
    assert review["coverage"] is None
    assert review["severity_counts"] == {"blocker": 1, "major": 1}
    assert review["category_counts"] == {"correctness": 1, "security": 1}
    assert review["trigger_kind_counts"] == {"inferred": 1, "measured": 1}
    assert review["finding_ids"] == ["correctness.bad-state", "security.leak"]
    assert review["finding_count"] == 2
    assert review["inferred_p1_count"] == 1
    assert review["runtime"] == {"duration_s": 9.5}
    assert review["reviewer"] == "codex-sub"
    assert review["verdict"] == "fail"
    assert review["status"] == "fail"
    assert review["shadows"] == {}
    assert review["result"] == audit["result"]


def _run_ledger_main(
    module,
    tmp_path,
    monkeypatch,
    *,
    extra_argv=(),
    preflight=None,
    install=None,
    audit=None,
):
    audit = audit or _short_circuit_fail_audit()
    work = tmp_path
    work.mkdir(parents=True, exist_ok=True)
    audit_path = work / "audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    if preflight is None:
        preflight_path = work / "missing" / "pr-size-preflight.json"
    else:
        preflight_path = work / "pr-size-preflight.json"
        preflight_path.write_text(json.dumps(preflight), encoding="utf-8")
    if install is None:
        install_path = work / "missing" / "install-result.json"
    else:
        install_path = work / "install-result.json"
        install_path.write_text(json.dumps(install), encoding="utf-8")
    output = work / "ledger.jsonl"
    monkeypatch.setattr(sys, "argv", [
        "build_ledger.py",
        "--audit-path", str(audit_path),
        "--preflight-path", str(preflight_path),
        "--install-path", str(install_path),
        "--output", str(output),
        "--repository", "zlxlabs/app",
        "--pr-number", "7",
        "--run-id", "10",
        "--run-attempt", "1",
        "--head-sha", "head",
        "--expected-repository-id", "123",
        "--expected-base-sha", "base",
        "--expected-caller-sha", "b" * 40,
        "--expected-reusable-workflow-sha", "c" * 40,
        "--codex-expected", "true",
        *extra_argv,
    ])
    rc = module.main()
    return rc, output, audit



def test_main_no_network_writes_one_v2_row_with_current_fields(tmp_path, monkeypatch):
    module = _module()

    def fail_socket(*args, **kwargs):
        raise RuntimeError("network access is forbidden in ledger producer")

    monkeypatch.setattr(socket, "socket", fail_socket)
    rc, output, audit = _run_ledger_main(
        module,
        tmp_path,
        monkeypatch,
        preflight=_preflight(),
        install={"ecosystem": "uv", "status": "ok", "duration_s": 2, "cache_hit": True},
    )

    assert rc == 0
    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert set(module.LEDGER_ENTRY_REQUIRED_FIELDS) <= set(row)
    assert row["schema_version"] == 2
    assert isinstance(row["recorded_at"], str) and row["recorded_at"]
    assert row["repository"] == "zlxlabs/app"
    assert row["pr_number"] == 7
    assert row["run_id"] == 10
    assert row["run_attempt"] == 1
    assert row["head_sha"] == "head"
    assert row["preflight"] == _preflight()
    assert row["install"] == {"ecosystem": "uv", "status": "ok", "duration_s": 2, "cache_hit": True}
    assert row["primary_identity"] is not None
    assert row["review"]["finding_ids"] == [
        "correctness.bad-state", "security.leak",
    ]
    assert row["review"]["result"] == audit["result"]
    assert row["finding_dispositions"] == {}
    assert row["false_positive_count"] == 0
    assert row["disposition_status"] == "success"
    assert not {"review_round", "comparison", "history_status", "ledger_conflict", "convergence_projection"} & row.keys()
    assert not set(module.LEDGER_ENTRY_OPTIONAL_FIELDS) & row.keys()


def test_build_ledger_imports_have_no_network_modules():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = {
        node.names[0].name.split(".", 1)[0]
        for node in tree.body
        if isinstance(node, ast.Import)
    }
    imported.update(
        node.module.split(".", 1)[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert not imported & {"urllib", "http", "socket", "ssl"}


def test_short_circuited_empty_preflight_writes_ledger_row():
    """W1: short-circuit + empty/missing preflight → row written, coverage None."""
    module = _module()
    assert "input_short_circuited" in inspect.signature(module.build_entry).parameters
    assert "input_short_circuited" in inspect.signature(module._review_summary).parameters
    audit = _short_circuit_fail_audit()
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight={}, audit=audit,
        install=None, expected_identity=EXPECTED_IDENTITY, input_short_circuited=True,
    )
    assert entry["preflight"] is None
    assert entry["install"] is None
    _assert_short_circuit_audit_projection(entry["review"], audit=audit)


def test_input_short_circuited_option_is_registered_on_parser(monkeypatch):
    """CLI option must be registered on ArgumentParser, not merely appear in source text."""
    module = _module()
    captured: list[str] = []

    def grab(self, args=None, namespace=None):
        captured.extend(
            option
            for action in self._actions
            for option in action.option_strings
        )
        raise SystemExit("captured-parser")

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", grab)
    monkeypatch.setattr(sys, "argv", ["build_ledger.py"])
    with pytest.raises(SystemExit, match="captured-parser"):
        module.main()
    assert "--input-short-circuited" in captured


def test_main_short_circuited_missing_preflight_writes_jsonl(tmp_path, monkeypatch):
    """W1 main(): missing input files + --input-short-circuited true → jsonl row."""
    module = _module()
    rc, output, audit = _run_ledger_main(
        module, tmp_path, monkeypatch,
        extra_argv=["--input-short-circuited", "true"],
    )
    assert rc == 0
    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["preflight"] is None
    _assert_short_circuit_audit_projection(row["review"], audit=audit)


def test_primary_review_empty_preflight_without_short_circuit_still_raises():
    """W2: non-short-circuit + empty preflight must keep raising. Do not relax."""
    module = _module()
    with pytest.raises(ValueError, match="canonical primary preflight has invalid coverage shape"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight={}, audit=_v2_audit("fail"),
        )
    params = inspect.signature(module.build_entry).parameters
    if "input_short_circuited" in params:
        with pytest.raises(ValueError, match="canonical primary preflight has invalid coverage shape"):
            module.build_entry(
                repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
                head_sha="head", preflight={}, audit=_v2_audit("fail"),
                input_short_circuited=False,
            )


def test_short_circuited_valid_preflight_still_computes_coverage():
    """W3: short-circuit flag is not a switch to ignore a present preflight."""
    module = _module()
    assert "input_short_circuited" in inspect.signature(module.build_entry).parameters
    audit = _v2_audit("fail")
    preflight = _preflight(100)
    kwargs = dict(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight=preflight, audit=audit, expected_identity=EXPECTED_IDENTITY,
    )
    normal = module.build_entry(**kwargs)
    shorted = module.build_entry(**kwargs, input_short_circuited=True)
    assert shorted["review"]["coverage"] == normal["review"]["coverage"]
    assert shorted["review"]["coverage"]["complete"] is True
    assert shorted["review"]["coverage"]["diff_lines"] == 100
    assert shorted["preflight"] == preflight


@pytest.mark.parametrize("raw", ["yes", "1", "TRUE", "True", "", "maybe", "false "])
def test_input_short_circuited_illegal_value_is_fail_loud(tmp_path, monkeypatch, raw):
    """W4: domain is exactly {true, false}; anything else SystemExit."""
    module = _module()
    with pytest.raises(SystemExit) as exc:
        _run_ledger_main(
            module, tmp_path, monkeypatch,
            extra_argv=["--input-short-circuited", raw],
        )
    assert exc.value.code == "--input-short-circuited must be one of true, false"


def test_omitted_input_short_circuited_matches_explicit_false(tmp_path, monkeypatch):
    """W4: omitting the CLI flag must behave identically to passing false."""
    module = _module()

    def outcome(label, extra):
        try:
            _run_ledger_main(module, tmp_path / label, monkeypatch, extra_argv=extra)
        except SystemExit as exc:
            return ("systemexit", str(exc))
        except ValueError as exc:
            return ("valueerror", str(exc))
        return ("ok", "")

    omitted = outcome("omitted", ())
    explicit = outcome("explicit-false", ("--input-short-circuited", "false"))
    assert omitted == explicit
    assert omitted[0] == "valueerror"
    assert "canonical primary preflight has invalid coverage shape" in omitted[1]


def test_v2_detached_shadows_are_recorded_as_unavailable_without_losing_expectations():
    module = _module()
    audit = _v2_audit("pass", expected_shadows=["claude-glm", "gemini-pro"])

    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight=_preflight(), audit=audit,
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
        )


def test_v2_detached_shadows_reject_non_detached_mode():
    module = _module()
    audit = _v2_audit("pass", expected_shadows=["claude-glm"])
    audit["shadow_mode"] = "inline"

    with pytest.raises(ValueError, match="shadow_mode"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit,
        )


def test_v2_runtime_rejects_attempt_duration_sum_mismatch():
    module = _module()
    audit = _v2_audit("pass", runtime={"duration_s": 1})
    audit["attempts"] = [{"reviewer": "codex-sub", "exit_code": 0, "reason": "", "duration_s": 2, "cost_usd": None}]
    with pytest.raises(ValueError, match="runtime"):
        module.build_entry(repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1, head_sha="head", preflight=_preflight(), audit=audit)


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
        head_sha="head", preflight=_preflight(), audit=audit,
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
        audit=fixture,
    )
    assert entry["review"]["runtime"] is None


@pytest.mark.parametrize("field,value", [("cost", -1), ("cost", float("inf")), ("tokens", {}), ("runtime", {"duration_s": -1}), ("runtime", {"duration_s": float("nan")})])
def test_v2_review_rejects_invalid_telemetry(field, value):
    module = _module()
    audit = _v2_audit("pass", **{field: value})
    with pytest.raises(ValueError, match="canonical primary"):
        module.build_entry(repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1, head_sha="head", preflight=_preflight(), audit=audit)


@pytest.mark.parametrize("mutation", [lambda audit: audit["result"].pop("findings"), lambda audit: audit["result"]["findings"].append({"id": "broken"}), lambda audit: audit["attempts"].append({"reviewer": 3})])
def test_v2_review_rejects_malformed_consumed_payload(mutation):
    module = _module()
    audit = _v2_audit("pass")
    mutation(audit)
    with pytest.raises(ValueError, match="canonical primary"):
        module.build_entry(repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1, head_sha="head", preflight=_preflight(), audit=audit)


def test_v2_primary_audit_rejects_mismatched_parent_identity():
    module = _module()
    audit = _v2_audit("pass", expected_shadows=["claude-glm"])
    audit["head_sha"] = "stale"

    with pytest.raises(ValueError, match="primary audit identity mismatch"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit,
        )


@pytest.mark.parametrize("field", list(EXPECTED_IDENTITY))
def test_v2_primary_audit_binds_every_workflow_identity_field(field):
    module = _module()
    audit = _v2_audit("pass")
    audit[field] = 999 if isinstance(EXPECTED_IDENTITY[field], int) else "stale"

    with pytest.raises(ValueError, match="primary audit identity mismatch"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit,
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
            head_sha="head", preflight=_preflight(), audit=audit,
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
            head_sha="head", preflight=_preflight(), audit=audit,
        )



def test_step_summary_scrubs_runtime_values(monkeypatch, tmp_path):
    module = _module()
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight={}, audit=_audit("head", []),
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
        head_sha="sha", preflight={}, audit=audit,
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
        head_sha="sha", preflight={}, audit=audit,
    )
    assert entry["review"]["reviewer"] == "claude-glm"
    assert entry["review"]["failover"] is False
    assert len(entry["review"]["attempts"]) == 1


def test_review_summary_defaults_when_audit_missing():
    module = _module()
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=None,
        fallback_status="not_run",
    )
    assert entry["review"]["reviewer"] is None
    assert entry["review"]["failover"] is False
    assert entry["review"]["attempts"] == []
    assert entry["review"]["trigger_kind_counts"] == {}
    assert entry["review"]["inferred_p1_count"] == 0


def test_review_summary_counts_measured_inferred_and_missing_trigger_kind():
    module = _module()
    audit = _audit("sha", ["a", "b", "c"])
    findings = audit["result"]["findings"]
    findings[0]["trigger_kind"] = "measured"
    findings[1]["trigger_kind"] = "inferred"
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=audit,
    )
    assert entry["review"]["trigger_kind_counts"] == {
        "inferred": 1, "measured": 1, "unspecified": 1,
    }


def test_review_summary_inferred_p1_count_covers_blocker_and_major():
    module = _module()
    audit = _audit("sha", ["a", "b", "c", "d"])
    findings = audit["result"]["findings"]
    findings[0].update(severity="blocker", trigger_kind="inferred")
    findings[1].update(severity="major", trigger_kind="inferred")
    findings[2].update(severity="minor", trigger_kind="inferred")
    findings[3].update(severity="blocker", trigger_kind="measured")
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=audit,
    )
    assert entry["review"]["inferred_p1_count"] == 2


def test_review_summary_invalid_trigger_kind_counts_as_unspecified():
    module = _module()
    audit = _audit("sha", ["a", "b"])
    findings = audit["result"]["findings"]
    findings[0]["trigger_kind"] = "guess"
    findings[1]["trigger_kind"] = 1
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=audit,
    )
    assert entry["review"]["trigger_kind_counts"] == {"unspecified": 2}


def _aggregator():
    path = ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"
    spec = importlib.util.spec_from_file_location("gate_aggregate_for_ledger", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _producer_terminal(*, receipts=(), run_attempt=1):
    agg = _aggregator()
    conv = agg._CONVERGENCE
    identity = agg.Identity(
        repository_id=123, head_sha="a" * 40, run_id=999, run_attempt=run_attempt, pr=42,
    )
    audit = {
        "kind": "primary_review",
        "schema_version": 1,
        "repository_id": identity.repository_id,
        "repository": "zlxlabs/gate",
        "head_sha": identity.head_sha,
        "run_id": identity.run_id,
        "run_attempt": identity.run_attempt,
        "pr": identity.pr,
        "verdict": "fail",
        "reviewer": "claude-glm",
        "base_sha": "b" * 40,
        "diff_digest": "d" * 64,
        "policy_version": "policy-v1",
        "policy_digest": "e" * 64,
        "tier": "personal",
        "caller_sha": "c" * 40,
        "reusable_workflow_sha": "w" * 40,
        "registry_commit": "r" * 40,
        "job_id": 99,
        "shadow_mode": "detached",
        "expected_shadows": [],
        "attempts": [],
        "cost": 0,
        "tokens": [],
        "runtime": None,
        "result": {
            "verdict": "fail",
            "summary": "primary reviewer found a blocking issue",
            "findings": [{
                "id": "p1", "severity": "major", "trigger_kind": "inferred",
                "file": "src/lock.py", "line": 12, "category": "correctness",
            }],
        },
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


def test_ledger_projects_real_producer_terminal_claim_record():
    module = _module()
    agg, conv, identity, receipts, outcome, terminal = _producer_terminal(receipts=[{}])
    assert outcome.disposition_audit is not None
    assert terminal["disposition_receipt_consumption"]["consumed_count"] == 0
    entry = module.build_entry(
        repository=terminal["repository"],
        pr_number=terminal["pr_number"],
        run_id=terminal["run_id"],
        run_attempt=terminal["run_attempt"],
        head_sha=terminal["head_sha"],
        preflight={},
        audit=None,
        terminal_envelope=terminal,
    )
    assert entry["disposition_receipt_consumption"] == terminal["disposition_receipt_consumption"]
    assert entry["disposition_receipt_consumption"]["recorded"] == [
        {
            "finding_id": "p1",
            "receipt": conv.disposition_receipt_artifact_name(receipts[0]),
            "disposition_claim": "false-positive",
            "triggering_actor": "octocat",
            "triggering_actor_id": 1,
            "recorded_at": "2026-08-30T12:00:00Z",
            "reason": "locked upstream behavior",
        }
    ]
    assert entry["disposition_receipt_consumption"]["resolved"] == []
    assert "resolved by receipt" not in json.dumps(entry["disposition_receipt_consumption"])


def test_ledger_preserves_recorded_human_id_and_stable_key():
    module = _module()
    block = {
        "resolved": [{
            "finding_id": "p1",
            "finding_key": "stable-key",
            "receipt": "receipt-artifact",
            "approver": "octocat",
            "approver_id": 1,
            "approved_at": "2026-08-30T12:00:00Z",
            "reason": "locked upstream behavior",
        }],
        "consumed_count": 1,
        "rejected_count": 0,
        "rejected_reasons": {},
        "fail_closed": False,
    }
    assert module.validate_disposition_receipt_audit(block) == {
        **block,
        "recorded": [],
    }


def test_ledger_empty_audit_when_producer_had_no_receipts():
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
        terminal_envelope=terminal,
    )
    expected = module.empty_disposition_receipt_audit()
    assert terminal["disposition_receipt_consumption"] == expected
    assert entry["disposition_receipt_consumption"] == expected


def test_ledger_omits_consumption_when_terminal_is_absent():
    module = _module()
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=_audit("sha", []),
    )
    assert "disposition_receipt_consumption" not in entry


def test_disposition_audit_stays_out_of_review_summary_and_compact_attempts():
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
        terminal_envelope=terminal,
    )
    assert "disposition_receipt_consumption" in entry
    assert "disposition_receipt_consumption" not in entry["review"]
    for attempt in entry["review"]["attempts"]:
        assert "disposition_receipt_consumption" not in attempt
    assert "disposition_receipt_consumption" not in inspect.getsource(module._review_summary)
    assert "disposition_receipt_consumption" not in inspect.getsource(module._compact_attempts)


def test_real_disposition_producer_receipt_is_record_only_through_ledger(tmp_path):
    import os
    import subprocess

    agg = _aggregator()
    conv = agg._CONVERGENCE
    identity = agg.Identity(
        repository_id=123, head_sha="a" * 40, run_id=999, run_attempt=1, pr=42,
    )
    audit = {
        "kind": "primary_review",
        "schema_version": 1,
        "repository_id": identity.repository_id,
        "repository": "zlxlabs/gate",
        "head_sha": identity.head_sha,
        "run_id": identity.run_id,
        "run_attempt": identity.run_attempt,
        "pr": identity.pr,
        "verdict": "fail",
        "reviewer": "claude-glm",
        "base_sha": "b" * 40,
        "diff_digest": "d" * 64,
        "policy_version": "policy-v1",
        "policy_digest": "e" * 64,
        "tier": "personal",
        "caller_sha": "c" * 40,
        "reusable_workflow_sha": "w" * 40,
        "registry_commit": "r" * 40,
        "job_id": 99,
        "shadow_mode": "detached",
        "expected_shadows": [],
        "attempts": [],
        "cost": 0,
        "tokens": [],
        "runtime": None,
        "result": {
            "verdict": "fail",
            "summary": "primary reviewer found a blocking issue",
            "findings": [{
                "id": "p1", "severity": "major", "trigger_kind": "inferred",
                "file": "src/lock.py", "line": 12, "category": "correctness",
            }],
        },
    }
    scope, missing = agg._convergence_scope_from_audit(audit, identity)
    assert not missing and scope is not None
    audit_path = tmp_path / "canonical-audit.json"
    audit_path.write_bytes(json.dumps(audit, indent=2).encode("utf-8") + b"\n")
    output_dir = tmp_path / "receipts"
    command = [
        sys.executable, str(ROOT / ".github/actions/gate-disposition/issue_receipt.py"), "issue",
        "--output-dir", str(output_dir), "--audit-path", str(audit_path),
        "--repository-id", str(identity.repository_id), "--pr-number", str(identity.pr),
        "--head-sha", identity.head_sha, "--finding-id", "p1",
        "--reason", "locked upstream behavior",
        "--approver", "octocat", "--approver-id", "1",
        "--approved-at", "2026-08-30T12:00:00Z",
        "--scope-json", json.dumps(scope.as_dict(), sort_keys=True),
    ]
    produced = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "GITHUB_RUN_ID": "control-999", "GITHUB_TRIGGERING_ACTOR": "octocat"},
    )
    producer_result = json.loads(produced.stdout)
    receipt_bytes = Path(producer_result["path"]).read_bytes()
    receipt_payload = json.loads(receipt_bytes)
    assert receipt_bytes == json.dumps(
        receipt_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    receipt = conv.parse_disposition_receipt(receipt_payload)
    audit_digest = conv.canonical_audit_digest(audit)

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
        audit_digest=audit_digest,
        waiver_receipts=(receipt,),
    )
    assert outcome.gate_result == "fail", (
        "expected a record-only receipt to leave the primary P1 blocking; "
        f"actual gate_result={outcome.gate_result!r}"
    )
    assert outcome.ok is False
    assert outcome.convergence_envelope["state"]["clean_streak"] == 0
    assert outcome.convergence_envelope["state"]["eligible_rounds"] == 1

    terminal = agg.build_terminal_envelope(
        repository="zlxlabs/gate",
        identity=identity,
        quality_result="success",
        primary_result="failure",
        review_expected=True,
        is_draft=False,
        runner="self",
        outcome=outcome,
    )
    ledger = _module().build_entry(
        repository="zlxlabs/gate",
        pr_number=identity.pr,
        run_id=identity.run_id,
        run_attempt=identity.run_attempt,
        head_sha=identity.head_sha,
        preflight=_preflight(),
        audit=audit,
        terminal_envelope=terminal,
    )
    assert terminal["gate_result"] == "fail"
    assert ledger["disposition_receipt_consumption"]["recorded"] == [
        {
            "finding_id": "p1",
            "receipt": producer_result["artifact"],
            "triggering_actor": "octocat",
            "triggering_actor_id": 1,
            "recorded_at": "2026-08-30T12:00:00Z",
            "disposition_claim": "false-positive",
            "reason": "locked upstream behavior",
            "finding_key": conv.canonical_finding_key(audit["result"]["findings"][0]),
        }
    ]
    projected = json.dumps(ledger["disposition_receipt_consumption"])
    assert '"resolved": []' in projected
    assert "approved" not in projected


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
    elif kind in {"missing_receipt", "approver_id", "consumed_count"}:
        block["resolved"] = [{
            "finding_id": "p1", "receipt": "artifact", "approver": "owner",
            "approver_id": 1, "approved_at": "2026-01-01T00:00:00Z", "reason": "test",
        }]
        block["consumed_count"] = 1
        if kind == "missing_receipt":
            del block["resolved"][0]["receipt"]
        elif kind == "approver_id":
            block["resolved"][0]["approver_id"] = 0
        else:
            block["consumed_count"] = 0
    elif kind == "rejected_count":
        block["rejected_count"] = 3
    else:
        block["fail_closed"] = "false"
    with pytest.raises(ValueError, match=match):
        module.validate_disposition_receipt_audit(block)


def test_malformed_audit_block_is_fail_loud():
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
            terminal_envelope=terminal,
        )


def _build_from_terminal(module, terminal, **overrides):
    kwargs = dict(
        repository=terminal["repository"],
        pr_number=terminal["pr_number"],
        run_id=terminal["run_id"],
        run_attempt=terminal["run_attempt"],
        head_sha=terminal["head_sha"],
        preflight={},
        audit=None,
        terminal_envelope=terminal,
    )
    kwargs.update(overrides)
    return module.build_entry(**kwargs)


_SAME_ATTEMPT_TERMINAL_ENTRY_KEYS = {
    "schema_version", "recorded_at", "repository", "pr_number", "run_id",
    "run_attempt", "head_sha", "disposition_status",
    "preflight", "install",
    "primary_identity", "review", "finding_dispositions", "false_positive_count",
    "disposition_receipt_consumption",
}


def test_prior_attempt_terminal_from_producer_is_accepted():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal(receipts=[{}], run_attempt=1)
    assert terminal["run_attempt"] == 1
    entry = _build_from_terminal(module, terminal, run_attempt=2)
    assert entry["disposition_receipt_consumption"] == terminal["disposition_receipt_consumption"]


def test_same_attempt_terminal_entry_keys_do_not_add_source_attempt():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal(receipts=[{}])
    entry = _build_from_terminal(module, terminal)
    assert "terminal_source_attempt" not in entry
    assert set(entry) == _SAME_ATTEMPT_TERMINAL_ENTRY_KEYS


def test_prior_attempt_terminal_entry_records_source_attempt():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal(receipts=[{}], run_attempt=1)
    entry = _build_from_terminal(module, terminal, run_attempt=2)
    assert entry["terminal_source_attempt"] == 1
    assert set(entry) == _SAME_ATTEMPT_TERMINAL_ENTRY_KEYS | {"terminal_source_attempt"}


def test_future_attempt_terminal_from_producer_is_rejected():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal(run_attempt=3)
    assert terminal["run_attempt"] == 3
    with pytest.raises(ValueError, match="gate terminal identity mismatch"):
        _build_from_terminal(module, terminal, run_attempt=2)


@pytest.mark.parametrize("bad_attempt", [0, -1, 1.5, "1", True, False, None])
def test_invalid_terminal_run_attempt_is_rejected(bad_attempt):
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal()
    terminal["run_attempt"] = bad_attempt
    with pytest.raises(ValueError, match="gate terminal identity mismatch"):
        _build_from_terminal(module, terminal, run_attempt=1)


@pytest.mark.parametrize("field, value", [
    ("repository", "other/repo"),
    ("pr_number", 99),
    ("run_id", 1),
    ("head_sha", "b" * 40),
])
def test_terminal_identity_still_requires_exact_non_attempt_fields(field, value):
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal()
    with pytest.raises(ValueError, match="gate terminal identity mismatch"):
        _build_from_terminal(module, terminal, **{field: value})


def test_unsupported_terminal_schema_is_fail_loud(tmp_path):
    module = _module()
    path = _write_terminal(tmp_path, {"schema_version": 2, "kind": "gate_terminal"})
    with pytest.raises(ValueError, match="unsupported schema"):
        module.load_gate_terminal_envelope(path)


def _relation_block(relation, previous_id=None):
    item = {"id": "f1", "relation_to_previous": relation, "file": "a.py", "line": 1}
    if previous_id:
        item["previous_finding_id"] = previous_id
    counts = {"new": 0, "repeat": 0, "conflict": 0}
    counts[relation] = 1
    return {
        "schema_version": 1,
        "counts": counts,
        "review_terminal": "manual_required" if relation == "conflict" else "unchanged",
        "items": [item],
    }


def test_ledger_copies_finding_relation_counts():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal()
    terminal["finding_relation"] = _relation_block("repeat", "f1")
    entry = _build_from_terminal(module, terminal)
    assert entry["finding_relation"]["counts"] == {"new": 0, "repeat": 1, "conflict": 0}


def test_ledger_rejects_unknown_finding_relation():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal()
    block = _relation_block("new")
    block["items"][0]["relation_to_previous"] = "maybe"
    terminal["finding_relation"] = block
    with pytest.raises(ValueError, match="GATE-FINDING-RELATION-UNKNOWN"):
        _build_from_terminal(module, terminal)
