"""`aggregate.py --prior-rounds`: the shadow arbiter's read-only round accounting.

261006-review-precision C2b.  This projection decides whether the arbiter job may
spend a cross-family model call, so the two facts it carries — this PR's eligible
primary rounds EXCLUDING the current run, and this round's blocking-finding count —
must come from the same code the `gate` job uses, and must never become a guess:
an uncountable history reports `null`, never `0` (gate#287 -> #290: a truncated
list that reads as zero silently disables the cap), and an unusable canonical
audit reports `null`, never "no blockers".

Every case runs the real CLI in a SUBPROCESS — the asserted JSON bytes are written
by a separate process — with the two network edges (GitHub run listing, Silo
terminal read) stubbed inside that child.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AGGREGATE = REPO_ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"
GOLDEN_AUDIT = REPO_ROOT / "tests" / "fixtures" / "primary-audit-redline-golden.json"
AUDIT_FILE_NAME = "primary-review-audit.json"

# Runs the real CLI out of a stubbed process: the driver loads aggregate.py by path
# (its directory has a dash, so it is not importable as a package), replaces the
# GitHub/Silo edges, and calls the entry point the workflow calls.
DRIVER = '''\
import importlib.util
import json
import pathlib
import sys

spec = importlib.util.spec_from_file_location("gate_aggregate_under_test", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules["gate_aggregate_under_test"] = module
spec.loader.exec_module(module)

stub = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
module._silo_configured = lambda: True
module._pr_target_run_ids = lambda **_: stub["run_ids"]
module._fetch_silo_terminal_history = lambda **_: module.HistoryLoad(
    rows=stub.get("rows", []), incomplete_reasons=stub.get("incomplete_reasons", []),
)
module._fetch_github_terminal_history = lambda **_: module.HistoryLoad(
    incomplete_reasons=stub.get("github_incomplete_reasons", []),
)
sys.exit(module.main(sys.argv[3:]))
'''


def _run_prior_rounds(
    tmp_path: Path, *, run_ids: list[int], rows: list[dict] | None = None,
    incomplete_reasons: list[str] | None = None, run_id: int = 900, tier: str = "personal",
    audit: Path | None = None,
) -> tuple[subprocess.CompletedProcess, Path]:
    (tmp_path / "driver.py").write_text(DRIVER, encoding="utf-8")
    stub = tmp_path / "stub.json"
    stub.write_text(
        json.dumps({
            "run_ids": run_ids,
            "rows": rows if rows is not None else [
                {"run_id": item, "run_attempt": 1, "_eligible_primary_round": True} for item in run_ids
            ],
            "incomplete_reasons": incomplete_reasons or [],
            "github_incomplete_reasons": (
                [f"no terminal artifact matched run {run_id}"]
                if not rows and not incomplete_reasons and run_ids else []
            ),
        }),
        encoding="utf-8",
    )
    out = tmp_path / "prior-rounds.json"
    argv = [
        sys.executable, str(tmp_path / "driver.py"), str(AGGREGATE), str(stub), "--prior-rounds",
        "--repository-id", "918273645", "--repository", "zlxlabs/example", "--pr-number", "42",
        "--run-id", str(run_id), "--run-attempt", "2", "--tier", tier,
    ]
    if audit is not None:
        argv += ["--audit-dir", str(audit)]
    argv += ["--out", str(out)]
    completed = subprocess.run(
        argv, capture_output=True, text=True, check=False,
        env={
            "PATH": os.environ["PATH"], "PYTHONPATH": str(REPO_ROOT),
            "SILO_ENDPOINT": "https://silo.example.test:9000", "GITHUB_TOKEN": "stub-token",
        },
    )
    return completed, out


def test_prior_rounds_excludes_the_current_run_and_writes_exact_bytes(tmp_path):
    completed, out = _run_prior_rounds(tmp_path, run_ids=[111, 222, 333], run_id=333)
    assert completed.returncode == 0, completed.stderr
    assert out.read_text(encoding="utf-8") == json.dumps(
        {
            "prior_rounds": 2, "limit": 5, "state": "collecting", "reason": "",
            "blocking_findings": None, "audit_state": "not_checked",
        },
        ensure_ascii=False, indent=2,
    ) + "\n"
    assert (
        "GATE-PRIOR-ROUNDS: repository_id=918273645 pr_number=42 run_id=333 run_attempt=2 "
        "prior_rounds=2 limit=5 state=collecting blocking_findings=None audit_state=not_checked"
        in completed.stdout
    )


@pytest.mark.parametrize(
    ("tier", "limit", "prior", "expected_state"),
    [
        ("personal", 5, 0, "collecting"),
        ("personal", 5, 4, "collecting"),
        ("personal", 5, 5, "arbitration_required"),
        ("internal", 8, 5, "collecting"),
        ("saas", 12, 12, "arbitration_required"),
    ],
)
def test_prior_rounds_uses_the_tier_limit_from_pr_round_budget(tmp_path, tier, limit, prior, expected_state):
    completed, out = _run_prior_rounds(
        tmp_path, run_ids=[1000 + index for index in range(prior)], run_id=999, tier=tier,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert (payload["prior_rounds"], payload["limit"], payload["state"]) == (prior, limit, expected_state)


def test_ineligible_history_rows_are_not_counted(tmp_path):
    rows = [
        {"run_id": 111, "run_attempt": 1, "_eligible_primary_round": True},
        {"run_id": 222, "run_attempt": 1, "_eligible_primary_round": False},
    ]
    completed, out = _run_prior_rounds(tmp_path, run_ids=[111, 222], rows=rows)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["prior_rounds"] == 1


def test_uncountable_history_is_null_never_zero(tmp_path):
    completed, out = _run_prior_rounds(
        tmp_path, run_ids=[111], rows=[],
        incomplete_reasons=["history budget exhausted", "Silo terminal history unavailable: RuntimeError"],
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["prior_rounds"] is None, "an unavailable history must never read as zero rounds"
    assert payload["state"] == "history_unavailable"
    assert payload["limit"] == 5
    assert payload["reason"] == "history budget exhausted; Silo terminal history unavailable: RuntimeError"


def test_blocking_findings_come_from_the_canonical_audit(tmp_path):
    audit_dir = tmp_path / "primary-audit"
    audit_dir.mkdir()
    (audit_dir / AUDIT_FILE_NAME).write_bytes(GOLDEN_AUDIT.read_bytes())
    completed, out = _run_prior_rounds(tmp_path, run_ids=[111, 222], audit=audit_dir)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    # The golden audit carries two major findings and one minor: only blocker/major
    # count as still blocking, the same P1 projection the aggregator uses.
    assert (payload["blocking_findings"], payload["audit_state"]) == (2, "available")
    assert "blocking_findings=2 audit_state=available" in completed.stdout


@pytest.mark.parametrize(
    ("payload_bytes", "expected_state"),
    [
        # Nothing on disk, and bytes that are not readable JSON: one "no usable audit" state.
        (None, "missing"),
        (b"{", "missing"),
        # Readable JSON whose findings cannot be projected: invalid, still null.
        (b'{"result": {"findings": [1]}}', "invalid"),
        (b'{"result": {"findings": [{"id": "x", "severity": "urgent"}]}}', "invalid"),
    ],
)
def test_unusable_audit_is_not_zero_blockers(tmp_path, payload_bytes, expected_state):
    audit_dir = tmp_path / "primary-audit"
    if payload_bytes is not None:
        audit_dir.mkdir()
        (audit_dir / AUDIT_FILE_NAME).write_bytes(payload_bytes)
    completed, out = _run_prior_rounds(tmp_path, run_ids=[111], audit=audit_dir)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["blocking_findings"] is None
    assert payload["audit_state"] == expected_state


def test_unsupported_tier_fails_loud_instead_of_guessing_a_limit(tmp_path):
    completed, out = _run_prior_rounds(tmp_path, run_ids=[111], tier="gold")
    assert completed.returncode != 0
    assert not out.exists()
    assert "unsupported tier for PR round budget 'gold'" in completed.stderr
