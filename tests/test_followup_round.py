"""Follow-up treatment wiring (round >= 4) and clean-streak coverage gating.

Covers the followup_round.py decision protocol (canonical round counting,
previous canonical head, carry-form validation), the gate-v2 inject-step
shell block (REVIEW_FOLLOWUP_MODE / REVIEW_PREV_HEAD_SHA exports and the
GATE-FOLLOWUP-SKIPPED degradation line), producer-bytes pass-through to
REVIEW_PREVIOUS_ROUND_PATH, and the convergence rule that a
coverage-incomplete round never increases the clean streak.
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
AGGREGATOR_DIR = ROOT / ".github" / "actions" / "gate-aggregator"
WORKFLOW = ROOT / ".github" / "workflows" / "gate-v2.yml"
PRODUCER_FIXTURE = ROOT / "tests" / "fixtures" / "followup_round" / "previous-findings.json"
INJECT_STEP_NAME = "Inject previous-round findings into primary review context"
PREVIOUS_JSON_EXPR = "${RUNNER_TEMP}/previous-findings.json"

H1, H2, H3, H4 = (f"{n:02x}" * 20 for n in range(1, 5))


def _module(name, path):
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


if str(AGGREGATOR_DIR) not in sys.path:
    sys.path.insert(0, str(AGGREGATOR_DIR))
CONV = _module("gate_convergence_from_aggregate", AGGREGATOR_DIR / "convergence.py")
FUP = _module("gate_followup_round", AGGREGATOR_DIR / "followup_round.py")


def _row(run_id, head, *, attempt=1):
    return {"run_id": run_id, "run_attempt": attempt, "head_sha": head, "_eligible_primary_round": True}


def _patch_history(monkeypatch, *, status="available", rows=()):
    aggregate = FUP._load_aggregate()

    def fake_load(*, repository, repository_id, pr_number):
        prior = tuple(row["run_id"] for row in rows if row.get("_eligible_primary_round") is True)
        return status, prior, aggregate.HistoryLoad(rows=list(rows))

    monkeypatch.setattr(aggregate, "_load_pr_convergence_history", fake_load)


CARRY_FORM = {
    "available": True, "detail": "", "head_sha": H3,
    "open_findings": [{"id": "c10f-f1", "severity": "blocker", "file": "a.py", "line": 1, "issue": "open"}],
    "dispositions": [],
}


def _carry(tmp_path, payload):
    path = tmp_path / "previous-findings.json"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return str(path)


def _resolve(tmp_path, monkeypatch, *, rows, carry=None, head_sha=H4):
    _patch_history(monkeypatch, rows=rows)
    payload = CARRY_FORM if carry is None else carry
    return FUP.resolve_followup_round(
        repository="zlxlabs/gate", repository_id=123, pr_number=42, run_id=400,
        tier="personal", head_sha=head_sha, previous_json=_carry(tmp_path, payload),
    )


def test_round_four_with_distinct_head_enables_treatment(tmp_path, monkeypatch):
    rows = [_row(101, H1), _row(102, H2), _row(103, H3)]
    assert _resolve(tmp_path, monkeypatch, rows=rows) == f"action=enable prev_head_sha={H3}"


def test_rounds_below_four_stay_silent_full_review(tmp_path, monkeypatch):
    rows = [_row(101, H1), _row(102, H2)]
    assert _resolve(tmp_path, monkeypatch, rows=rows) == "action=none"


def test_round_four_on_same_head_does_not_enable(tmp_path, monkeypatch):
    rows = [_row(101, H1), _row(102, H2), _row(103, H4)]
    assert _resolve(tmp_path, monkeypatch, rows=rows, head_sha=H4) == "action=skip reason=same_head"


def test_unavailable_history_skips_conservatively(tmp_path, monkeypatch):
    _patch_history(monkeypatch, status="history_unavailable", rows=[_row(101, H1)])
    line = FUP.resolve_followup_round(
        repository="zlxlabs/gate", repository_id=123, pr_number=42, run_id=400,
        tier="personal", head_sha=H4, previous_json=str(tmp_path / "absent.json"),
    )
    assert line == "action=skip reason=history_unavailable"


def test_producer_fixture_bytes_carry_open_findings_and_enable_treatment(tmp_path, monkeypatch):
    # Producer contract: the committed fixture is the actual
    # --render-previous-context output bytes (generated through AGG.main on a
    # synthetic saas blocking-matrix ledger); its top-level open_findings is
    # the merge-blocking subset of findings no approved receipt released
    # (fp releases any covered finding; deferred releases only must_respond;
    # comment never blocks), and it enables follow-up treatment (gate-hub
    # _load_carry reads the same key).
    payload = json.loads(PRODUCER_FIXTURE.read_text(encoding="utf-8"))
    assert set(payload) == {
        "available", "detail", "head_sha", "findings", "dispositions", "open_findings",
    }
    assert payload["available"] is True and isinstance(payload["findings"], list)
    assert all(isinstance(item, dict) for item in payload["open_findings"])
    open_ids = {finding["id"] for finding in payload["open_findings"]}
    expected_open = {
        finding["id"] for finding in payload["findings"]
        if finding.get("gate_action") in {"block", "must_respond"}
    } - {"carry-fp-block", "carry-deferred-mr"}
    assert open_ids == expected_open
    assert "carry-comment-nit" not in open_ids
    assert "carry-mr-minor-open" in open_ids
    assert "carry-deferred-block" in open_ids
    prev_head = payload["head_sha"]
    rows = [_row(101, H1), _row(102, H2), _row(103, prev_head)]
    assert _resolve(tmp_path, monkeypatch, rows=rows, carry=payload) == (
        f"action=enable prev_head_sha={prev_head}"
    )


def test_carry_head_mismatch_skips(tmp_path, monkeypatch):
    rows = [_row(101, H1), _row(102, H2), _row(103, H3)]
    carry = {**CARRY_FORM, "head_sha": H2}
    assert _resolve(tmp_path, monkeypatch, rows=rows, carry=carry) == "action=skip reason=head_mismatch"


STUB_AGGREGATE = '''
import sys
ROWS = %(rows)r
STATUS = %(status)r
class _H:
    rows = ROWS
def _load_pr_convergence_history(*, repository, repository_id, pr_number):
    print("GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason=silo_not_configured")
    return STATUS, (), _H()
class _C:
    @staticmethod
    def pr_round_budget(*, prior_run_ids, current_run_id, current_eligible, tier):
        return {"eligible_rounds": len(set(prior_run_ids) | {current_run_id})}
_CONVERGENCE = _C()
'''


def _run_cli(tmp_path, *, rows, status="available", carry_payload=None):
    workdir = tmp_path / "pkg"
    workdir.mkdir(exist_ok=True)
    (workdir / "aggregate.py").write_text(STUB_AGGREGATE % {"rows": rows, "status": status}, encoding="utf-8")
    (workdir / "followup_round.py").write_bytes((AGGREGATOR_DIR / "followup_round.py").read_bytes())
    carry = workdir / "carry.json"
    carry.write_text(json.dumps(carry_payload if carry_payload is not None else CARRY_FORM) + "\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(workdir / "followup_round.py"), "--repository", "zlxlabs/gate",
         "--repository-id", "123", "--pr-number", "42", "--run-id", "400", "--tier", "personal",
         "--head-sha", H4, "--previous-json", str(carry)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc


def test_cli_stdout_is_exactly_one_action_line(tmp_path):
    rows = [_row(101, H1), _row(102, H2), _row(103, H3)]
    proc = _run_cli(tmp_path, rows=rows)
    assert proc.stdout == f"action=enable prev_head_sha={H3}\n"
    assert "GATE-CONVERGENCE-HISTORY-UNAVAILABLE" in proc.stderr
    proc = _run_cli(tmp_path, rows=[_row(101, H1)])
    assert proc.stdout == "action=none\n"
    proc = _run_cli(tmp_path, rows=rows, status="history_unavailable")
    assert proc.stdout == "action=skip reason=history_unavailable\n"


def _inject_step():
    raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return next(
        step for step in raw["jobs"]["primary"]["steps"]
        if step.get("name") == INJECT_STEP_NAME
    )


def _followup_shell_lines():
    lines = _inject_step()["run"].splitlines()
    start = next(i for i, line in enumerate(lines) if line.lstrip().startswith("followup_decision="))
    end = next(i for i, line in enumerate(lines) if line.strip() == "esac")
    return lines[start:end + 1]


def _carry_gate_rerun(head_sha):
    # Compact copy of test_gate_aggregator._gate_rerun: a counterevidence
    # gate_rerun block that passes _counterevidence_reason's allowlist.
    literal, pathspec = "locked-behavior", "src/lock.py"
    argv = ["git", "grep", "-n", "-F", "-e", literal, head_sha, "--", pathspec]
    excerpt = f"{pathspec}:1:{literal}"
    return {
        "argv": argv, "exit_code": 0, "match_count": 1,
        "stdout_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
        "excerpt": excerpt,
    }


def _carry_receipt(repository_id, pr_number, head_sha, disposition, finding_id, **extra):
    payload = {
        "schema_version": 1, "kind": CONV.DISPOSITION_RECEIPT_KIND,
        "disposition": disposition, "repository_id": repository_id, "pr_number": pr_number,
        "epoch": "2026-11-01", "head_sha": head_sha, "audit_digest": "0" * 64,
        "finding_id": finding_id, "finding_key": f"key-{finding_id}",
        "reason": "carry fixture", "approver": "owner", "approver_id": 1,
        "approved_at": "2026-11-01T00:00:00Z", "triggering_actor": "owner",
        "triggering_actor_source": "session",
    }
    payload.update(extra)
    return payload


def _carry_producer_output(ledger, receipts, tmp_path, monkeypatch):
    """Run the real --render-previous-context CLI over a fake Silo holding
    ``ledger`` (the ledger-latest object) and ``receipts``; second consumer
    of the fake-silo producer drive (test_gate_aggregator keeps the PR1063
    variant). Returns (returncode, parsed payload)."""
    aggregate = FUP._load_aggregate()
    repository_id = ledger["primary_identity"]["repository_id"]
    objects = [
        (aggregate._ledger_latest_key(repository_id, ledger["pr_number"]),
         json.dumps(ledger, ensure_ascii=False).encode("utf-8")),
    ]
    for receipt in receipts:
        name = CONV.disposition_receipt_artifact_name(CONV.parse_disposition_receipt(receipt))
        objects.append((
            f"{aggregate.SILO_DISPOSITION_TIER}/{repository_id}/{name}/{name}.json",
            json.dumps(receipt, ensure_ascii=False).encode("utf-8"),
        ))
    by_key = dict(objects)

    def fake_silo_cli(argv, timeout_s):
        if argv[0] == "list":
            raise AssertionError("previous-round findings must not list Silo objects")
        key = argv[argv.index("--key") + 1]
        dest = argv[argv.index("--dest") + 1]
        body = by_key.get(key)
        if body is None:
            return subprocess.CompletedProcess(argv, 2, stdout="", stderr="Silo object not found")
        Path(dest).write_bytes(body)
        return subprocess.CompletedProcess(argv, 0, stdout=dest, stderr="")

    monkeypatch.setattr(aggregate, "_silo_configured", lambda: True)
    monkeypatch.setattr(aggregate, "_silo_cli", fake_silo_cli)
    monkeypatch.setattr(
        aggregate, "_silo_objects_under",
        lambda prefix, timeout_s: [(key, raw) for key, raw in objects if key.startswith(prefix)],
    )
    output = tmp_path / "previous-findings.json"
    rc = aggregate.main([
        "--render-previous-context",
        "--repository-id", str(repository_id),
        "--pr-number", str(ledger["pr_number"]),
        "--run-id", str(ledger["run_id"] + 100),
        "--run-attempt", str(ledger["run_attempt"]),
        "--previous-json", str(output),
    ])
    return rc, json.loads(output.read_text(encoding="utf-8"))


def test_render_previous_context_blocking_matrix_carry_semantics(tmp_path, monkeypatch):
    """Blocking-matrix-v1 carry (gate-hub review-followup-incremental 2.1):
    merge-blocking is gate_action-driven; deferred releases only
    must_respond, false-positive releases any covered finding, and
    comment-level findings never carry."""
    head = "c10f2" * 8
    findings = [
        {"id": "carry-mr-minor-open", "severity": "minor", "file": "src/export.py", "line": 10,
         "category": "correctness", "issue": "saas minor must_respond blocks merge",
         "acceptance": "fix or respond", "gate_action": "must_respond"},
        {"id": "carry-comment-nit", "severity": "nit", "file": "src/style.py", "line": 20,
         "category": "style", "issue": "comment level never blocks", "acceptance": "n/a",
         "gate_action": "comment"},
        {"id": "carry-fp-block", "severity": "major", "file": "src/lock.py", "line": 30,
         "category": "correctness", "issue": "released by false-positive receipt",
         "acceptance": "n/a", "gate_action": "block"},
        {"id": "carry-deferred-mr", "severity": "minor", "file": "src/queue.py", "line": 40,
         "category": "reliability", "issue": "released by deferred receipt",
         "acceptance": "n/a", "gate_action": "must_respond"},
        {"id": "carry-deferred-block", "severity": "major", "file": "src/lease.py", "line": 50,
         "category": "correctness", "issue": "deferred cannot release a block finding",
         "acceptance": "n/a", "gate_action": "block"},
    ]
    ledger = {
        "schema_version": 2, "pr_number": 77, "run_id": 300, "run_attempt": 1,
        "head_sha": head,
        "primary_identity": {
            "repository_id": 301, "pr_number": 77, "run_id": 300, "run_attempt": 1,
            "head_sha": head,
        },
        "review": {
            "status": "fail", "verdict": "fail",
            "blocking_matrix": {"version": 1, "tier": "saas"},
            "result": {"verdict": "fail", "findings": findings},
        },
    }
    fp = _carry_receipt(
        301, 77, head, "false-positive", "carry-fp-block",
        counterevidence={
            "command": "git grep -n -F -e locked-behavior",
            "output": "src/lock.py:1:locked-behavior", "result": "refuted",
            "pointer": "src/lock.py:1", "gate_rerun": _carry_gate_rerun(head),
        },
    )
    deferred_mr = _carry_receipt(301, 77, head, "deferred", "carry-deferred-mr", tracking_issue="#77")
    deferred_block = _carry_receipt(301, 77, head, "deferred", "carry-deferred-block", tracking_issue="#78")
    rc, payload = _carry_producer_output(ledger, [fp, deferred_mr, deferred_block], tmp_path, monkeypatch)

    assert rc == 0
    assert payload["available"] is True and payload["head_sha"] == head
    assert payload["findings"] == findings  # findings stays verbatim
    open_ids = [finding["id"] for finding in payload["open_findings"]]
    assert "carry-mr-minor-open" in open_ids  # saas minor must_respond blocks merge
    assert "carry-deferred-block" in open_ids  # deferred releases only must_respond
    assert "carry-comment-nit" not in open_ids  # comment level never carries
    assert "carry-fp-block" not in open_ids  # false-positive receipt releases
    assert "carry-deferred-mr" not in open_ids  # deferred receipt releases must_respond
    mr_open = next(f for f in payload["open_findings"] if f["id"] == "carry-mr-minor-open")
    assert mr_open["gate_action"] == "must_respond" and mr_open["severity"] == "minor"
    assert {"id", "severity", "file", "line", "issue", "gate_action"} <= set(mr_open)


def test_render_previous_context_legacy_carry_deferred_does_not_release(tmp_path, monkeypatch, capsys):
    """Legacy audit (no blocking_matrix): carry is P1-severity-only and a
    deferred receipt releases nothing."""
    head = "1e6a" * 10
    findings = [
        {"id": "carry-legacy-blocker-open", "severity": "blocker", "file": "src/core.py", "line": 1,
         "category": "correctness", "issue": "unreleased blocker stays in carry", "acceptance": "fix"},
        {"id": "carry-legacy-minor", "severity": "minor", "file": "src/minor.py", "line": 2,
         "category": "style", "issue": "legacy minor never carries", "acceptance": "n/a"},
        {"id": "carry-legacy-blocker-deferred", "severity": "blocker", "file": "src/old.py", "line": 3,
         "category": "correctness", "issue": "deferred cannot release a legacy P1", "acceptance": "n/a"},
    ]
    ledger = {
        "schema_version": 2, "pr_number": 78, "run_id": 301, "run_attempt": 1,
        "head_sha": head,
        "primary_identity": {
            "repository_id": 301, "pr_number": 78, "run_id": 301, "run_attempt": 1,
            "head_sha": head,
        },
        "review": {"status": "fail", "verdict": "fail",
                   "result": {"verdict": "fail", "findings": findings}},
    }
    deferred = _carry_receipt(301, 78, head, "deferred", "carry-legacy-blocker-deferred", tracking_issue="#79")
    rc, payload = _carry_producer_output(ledger, [deferred], tmp_path, monkeypatch)
    out = capsys.readouterr().out

    assert rc == 0
    assert {finding["id"] for finding in payload["open_findings"]} == {
        "carry-legacy-blocker-open", "carry-legacy-blocker-deferred",
    }
    assert all("gate_action" not in finding for finding in payload["open_findings"])
    assert "GATE-PREVIOUS-DISPOSITIONS: kept=1 dropped=0" in out


def test_inject_step_wires_followup_env_and_skipped_line():
    step = _inject_step()
    run = step["run"]
    assert WORKFLOW.read_text(encoding="utf-8").count("GATE-FOLLOWUP-SKIPPED") >= 1
    assert 'python3 _gate-silo-src/.github/actions/gate-aggregator/followup_round.py' in run
    assert f'--previous-json "{PREVIOUS_JSON_EXPR}"' in run
    # Cross-boundary: every consumer reads the same single temp path — the
    # [ -s ] check, the REVIEW_PREVIOUS_ROUND_PATH export, the producer call
    # and the decision call — so review-primary reads exactly the bytes the
    # decision validated (gate-hub contract section 9).
    assert run.count(PREVIOUS_JSON_EXPR) == 4
    export = f'echo "REVIEW_PREVIOUS_ROUND_PATH={PREVIOUS_JSON_EXPR}" >> "$GITHUB_ENV"'
    assert export in run
    assert 'echo "REVIEW_FOLLOWUP_MODE=treatment" >> "$GITHUB_ENV"' in run
    assert 'echo "REVIEW_PREV_HEAD_SHA=${followup_line#*prev_head_sha=}" >> "$GITHUB_ENV"' in run
    assert 'echo "GATE-FOLLOWUP-SKIPPED reason=${followup_line#action=skip reason=}"' in run
    assert "REVIEW_FOLLOWUP_MODE" not in (step.get("env") or {})
    sparse = step["env"]["GATE_CHECKOUT_SPARSE"].splitlines()
    assert any(
        path == ".github/actions/gate-aggregator/followup_round.py"
        or path.rstrip("/") == ".github/actions/gate-aggregator"
        for path in (line.strip() for line in sparse)
    ), sparse
    for key, expr in (
        ("GH_TOKEN", "${{ github.token }}"),
        ("HEAD_SHA", "${{ github.event.pull_request.head.sha }}"),
        ("REPOSITORY", "${{ github.repository }}"),
        ("TIER", "${{ inputs.tier }}"),
    ):
        assert step["env"][key] == expr
    review = next(
        s for s in yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["primary"]["steps"]
        if s.get("name") == "Run review-primary"
    )
    assert "REVIEW_FOLLOWUP_MODE" not in (review.get("env") or {})
    assert "REVIEW_PREV_HEAD_SHA" not in (review.get("env") or {})


def test_producer_bytes_flow_through_one_path_expression(tmp_path, monkeypatch):
    # The committed fixture is the actual --render-previous-context output
    # bytes; feed them to the decision exactly as the inject step would.
    # With the carry produced (open_findings + head match) the same bytes
    # that pre-c10f2 reported carry_missing now enable treatment.
    payload = json.loads(PRODUCER_FIXTURE.read_text(encoding="utf-8"))
    assert _resolve(
        tmp_path, monkeypatch,
        rows=[_row(101, H1), _row(102, H2), _row(103, payload["head_sha"])],
        carry=payload,
    ) == f"action=enable prev_head_sha={payload['head_sha']}"


def _run_shell_block(tmp_path, *, fake_line, stub_exit=0, name="case"):
    workspace = tmp_path / name
    stub_dir = workspace / "_gate-silo-src" / ".github" / "actions" / "gate-aggregator"
    stub_dir.mkdir(parents=True, exist_ok=True)
    (stub_dir / "followup_round.py").write_text(
        "import os, sys\n"
        "print(os.environ['FOLLOWUP_FAKE_LINE'])\n"
        f"sys.exit({stub_exit})\n",
        encoding="utf-8",
    )
    github_env = workspace / "github-env.txt"
    runner_temp = workspace / "runner-temp"
    runner_temp.mkdir(exist_ok=True)
    script = workspace / "block.sh"
    script.write_text(
        "set -euo pipefail\n" + "\n".join(_followup_shell_lines()) + "\n", encoding="utf-8",
    )
    env = {
        **os.environ,
        "GITHUB_ENV": str(github_env), "RUNNER_TEMP": str(runner_temp),
        "REPOSITORY": "zlxlabs/gate", "REPOSITORY_ID": "123", "PR_NUMBER": "42",
        "RUN_ID": "400", "TIER": "personal", "HEAD_SHA": H4,
        "FOLLOWUP_FAKE_LINE": fake_line,
    }
    proc = subprocess.run(["bash", str(script)], cwd=workspace, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    exported = github_env.read_text(encoding="utf-8") if github_env.exists() else ""
    return proc.stdout, exported


def test_shell_block_exports_pair_on_enable_and_skips_loudly_otherwise(tmp_path):
    stdout, exported = _run_shell_block(tmp_path, fake_line=f"action=enable prev_head_sha={H3}", name="enable")
    assert stdout == ""
    assert exported == f"REVIEW_FOLLOWUP_MODE=treatment\nREVIEW_PREV_HEAD_SHA={H3}\n"

    stdout, exported = _run_shell_block(tmp_path, fake_line="action=skip reason=carry_missing", name="skip")
    assert stdout == "GATE-FOLLOWUP-SKIPPED reason=carry_missing\n"
    assert exported == ""

    stdout, exported = _run_shell_block(tmp_path, fake_line="action=none", name="none")
    assert stdout == "" and exported == ""

    stdout, exported = _run_shell_block(tmp_path, fake_line="unreachable", stub_exit=1, name="crash")
    assert stdout == "GATE-FOLLOWUP-SKIPPED reason=decision_cli_failed\n"
    assert exported == ""


# --- clean streak: coverage-incomplete rounds are not clean rounds ---

def _scope():
    # internal tier: clean_rounds=2, so a two-round sequence stays collecting
    return CONV.Scope(
        repository_id=123, pr_number=42, base_sha="b" * 40, head_sha="h" * 40,
        diff_digest="d" * 64, policy_version="policy-v1", policy_digest="p" * 64,
        tier="internal", caller_sha="c" * 40, reusable_workflow_sha="w" * 40,
    )


def _clean_round(state, scope, *, run_id, coverage_result=None):
    primary = CONV.CanonicalPrimary(
        schema_version=1, repository_id=scope.repository_id, pr_number=scope.pr_number,
        head_sha=scope.head_sha, run_id=run_id, run_attempt=1, verdict="pass",
        p1_ids=(), p1_findings=(),
    )
    return CONV.evaluate_round(
        state=state, scope=scope, primary=primary,
        audit_digest=hashlib.sha256(f"round-{run_id}".encode()).hexdigest(),
        waiver_receipts=(), processing_key=CONV.ProcessingKey(scope.repository_id, scope.pr_number, run_id, 1),
        coverage_result=coverage_result,
    )


INCOMPLETE = {"coverage": {"status": "incomplete", "reasons": ["followup_diff_unavailable"]}}
COMPLETE = {"coverage": {"status": "complete", "reasons": []}}


def test_coverage_incomplete_round_never_increases_clean_streak():
    scope = _scope()
    first = _clean_round(CONV.initial_state(scope), scope, run_id=1)
    assert first.state.clean_streak == 1
    second = _clean_round(first.state, scope, run_id=2, coverage_result=INCOMPLETE)
    assert second.state.clean_streak == 0
    assert second.state.eligible_rounds == 2


def test_coverage_complete_round_still_counts_clean():
    scope = _scope()
    first = _clean_round(CONV.initial_state(scope), scope, run_id=1, coverage_result=COMPLETE)
    second = _clean_round(first.state, scope, run_id=2, coverage_result=COMPLETE)
    assert second.state.clean_streak == 2


def test_missing_coverage_keeps_base_clean_behavior():
    scope = _scope()
    first = _clean_round(CONV.initial_state(scope), scope, run_id=1)
    second = _clean_round(first.state, scope, run_id=2)
    assert second.state.clean_streak == 2
