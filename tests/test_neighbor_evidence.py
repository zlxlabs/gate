"""Neighbor-evidence collection + gate-job wiring tests (gate#297).

End-to-end contract: a drifted (head_sha / audit_digest no longer equal)
false-positive receipt is consumed as ``matched_by == "neighbor"`` only when
the collector proves — against the REAL git history of the caller repo — that
the anchor window survived the drift and the allowlisted counterevidence
still refutes on the current head.  External services (Silo, GitHub artifacts)
are faked; git is never faked: the collector spawns real ``git fetch --depth
1`` / ``git diff --unified=0`` / ``git grep`` subprocesses against a local
file:// origin (url.insteadOf rewrite + allowAnySHA1InWant).

Locked invariants: silo key bytes match the issuance key rule; fetch argv
never carries --filter (gate#302); the rerun argv differs from the stored one
only at index 6 (head); zero candidates → zero boundary calls; any collection
failure prints one GATE-NEIGHBOR-EVIDENCE-UNAVAILABLE line and leaves the
consumer on its existing fail-closed reason codes.
"""

import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NEIGHBOR_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "neighbor_evidence.py"
AGGREGATE_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Load order mirrors production: aggregate.py registers its convergence copy
# first; neighbor_evidence reuses the host module so Scope dataclass identity
# matches end to end.
AGG = _load("gate_aggregate_test", AGGREGATE_PATH)
NEI = _load("gate_neighbor_evidence_test", NEIGHBOR_PATH)
CONV = NEI._CONVERGENCE
assert CONV is AGG._CONVERGENCE

TOLERANCE = CONV.NEIGHBOR_LINE_TOLERANCE  # 4, imported — never a local 4
REPOSITORY = "zlxlabs/caller"
REPOSITORY_ID = 123
PR_NUMBER = 42
ANCHOR_FILE = "src/widget.py"
LITERAL = "locked-behavior"
RUN_ID = 77
ATTEMPT = 1


def _finding_def(finding_id: str, line: int):
    return {
        "id": finding_id, "severity": "major", "trigger_kind": "inferred",
        "file": ANCHOR_FILE, "line": line, "category": "correctness",
    }


def _widget_source() -> str:
    lines = [f"# filler line {i}" for i in range(1, 251)]
    lines[186] = f"ANCHOR_{187} = '{LITERAL}'"  # 1-indexed line 187
    return "\n".join(lines) + "\n"


def _drifted(source: str, after_line: int, tag: str) -> str:
    """Insert 3 lines after ``after_line``; line 187 drifts when it is before."""
    marker = f"# filler line {after_line}\n"
    assert source.count(marker) == 1
    return source.replace(marker, marker + f"# {tag} a\n# {tag} b\n# {tag} c\n", 1)


def _git(repo: Path, *argv: str) -> None:
    subprocess.run(["git", "-C", str(repo), *argv], check=True, capture_output=True)


def _commit(repo: Path, content: str, message: str) -> str:
    (repo / ANCHOR_FILE).parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        (repo / ANCHOR_FILE).unlink()
    else:
        (repo / ANCHOR_FILE).write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", message)
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture()
def caller_repo(tmp_path, monkeypatch):
    repo = tmp_path / "caller-origin"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo, "config", "user.email", "fixture@example.test")
    _git(repo, "config", "user.name", "fixture")
    source = _widget_source()
    sha_a = _commit(repo, source, "issuing head")
    sha_b = _commit(repo, _drifted(source, 30, "drift"), "current head")  # 187→190
    _git(repo, "checkout", "--quiet", sha_a)
    sha_bp = _commit(repo, _drifted(source, 189, "in-window"), "in-window drift")
    _git(repo, "checkout", "--quiet", "--detach", sha_a)
    sha_bd = _commit(repo, None, "file deleted")
    _git(repo, "checkout", "--quiet", "main")
    # The collector fetches the GitHub https URL; rewrite it to this local
    # origin for the test process only.  Real git subprocess, no network.
    _git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        f'[url "file://{repo}"]\n\tinsteadOf = https://github.com/{REPOSITORY}.git\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    return {"repo": repo, "sha_a": sha_a, "sha_b": sha_b, "sha_bp": sha_bp, "sha_bd": sha_bd}


def _audit(head_sha: str, findings: list[dict], *, run_id=RUN_ID) -> dict:
    return {
        "kind": "primary_review", "schema_version": 1,
        "repository_id": REPOSITORY_ID, "head_sha": head_sha,
        "run_id": run_id, "run_attempt": ATTEMPT, "pr": PR_NUMBER,
        "verdict": "fail", "reviewer": "primary-bot",
        "base_sha": "b" * 40, "diff_digest": "d" * 64,
        "policy_version": "policy-v1", "policy_digest": "p" * 64,
        "tier": "personal", "caller_sha": "c" * 40,
        "reusable_workflow_sha": "w" * 40,
        "result": {"findings": findings},
    }


def _scope_for(audit: dict):
    scope, missing = AGG._convergence_scope_from_audit(
        audit, AGG.Identity(
            repository_id=REPOSITORY_ID, head_sha=audit["head_sha"],
            run_id=audit["run_id"], run_attempt=audit["run_attempt"], pr=PR_NUMBER,
        ),
    )
    assert not missing and scope is not None
    return scope


def _receipt(head_sha: str, audit_digest: str, *, finding_id="t-id") -> object:
    """A signed refuted receipt bound to the issuing round (head + digest)."""
    grep_argv = ["git", "grep", "-n", "-F", "-e", LITERAL, head_sha, "--", ANCHOR_FILE]
    excerpt = f"{ANCHOR_FILE}:187:{LITERAL}"
    return CONV.DispositionReceipt(
        schema_version=CONV.DISPOSITION_RECEIPT_SCHEMA_VERSION,
        disposition="false-positive",
        repository_id=str(REPOSITORY_ID), pr_number=PR_NUMBER,
        epoch=CONV.derive_epoch(_scope_for(_audit(head_sha, [_finding_def(finding_id, 187)]))),
        head_sha=head_sha, audit_digest=audit_digest, finding_id=finding_id,
        finding_key=CONV.canonical_finding_key(_finding_def(finding_id, 187)),
        reason="locked upstream behavior",
        counterevidence={
            "command": "pytest -q tests/test_regression.py",
            "output": "1 passed", "result": "refuted",
            "pointer": "tests/test_regression.py::test_regression",
            "gate_rerun": {
                "argv": grep_argv, "exit_code": 0, "match_count": 1,
                "stdout_sha256": hashlib.sha256(excerpt.encode()).hexdigest(),
                "excerpt": excerpt,
            },
        },
        approver="octocat", approver_id=1, approved_at="2026-08-30T12:00:00Z",
    )


def _terminal_rows(head_sha: str) -> list[dict]:
    return [{
        "schema_version": 1, "repository": REPOSITORY,
        "run_id": RUN_ID, "run_attempt": ATTEMPT, "head_sha": head_sha,
        "gate_result": "fail", "classification": "code_fail",
        "reason_code": "primary_findings", "_eligible_primary_round": True,
    }]


CURRENT_FINDINGS = [_finding_def("p2-id", 185), _finding_def("t-id", 190)]


class _Spies:
    """Fakes the external service boundaries, records the bytes handed over."""

    def __init__(self, monkeypatch, *, issued_audit: dict):
        self.issued_audit = issued_audit
        self.silo_keys: list[str] = []
        self.gh_calls: list[tuple[str, int, str]] = []
        self.git_argvs: list[list[str]] = []
        self.fetch_timeouts: list[float] = []
        self.silo_mode = "ok"    # ok | absent | wrong | explode
        self.gh_mode = "absent"  # absent | ok
        self.git_fail_mode = None  # None | fetch_timeout | diff_timeout | init_fail
        self._real_run_git = NEI._run_git
        monkeypatch.setattr(NEI, "_run_git", self._spy_run_git)
        monkeypatch.setattr(NEI, "_silo_audit_get", self._fake_silo_audit_get)
        monkeypatch.setattr(NEI, "_gh_run_download", self._fake_gh_run_download)

    def _spy_run_git(self, argv, *, cwd=None, timeout_s):
        self.git_argvs.append(list(argv))
        if "fetch" in argv:
            self.fetch_timeouts.append(timeout_s)
        # Timeout/exit-code injection at the _run_git seam: the collector must
        # convert these into fail enums, never let them escape to the caller.
        if self.git_fail_mode == "fetch_timeout" and "fetch" in argv:
            raise subprocess.TimeoutExpired(argv, timeout_s)
        if self.git_fail_mode == "diff_timeout" and "diff" in argv:
            raise subprocess.TimeoutExpired(argv, timeout_s)
        if self.git_fail_mode == "init_fail" and "init" in argv:
            return subprocess.CompletedProcess(argv, returncode=1)
        return self._real_run_git(argv, cwd=cwd, timeout_s=timeout_s)

    def _fake_silo_audit_get(self, key, timeout_s):
        self.silo_keys.append(key)
        if self.silo_mode == "absent":
            return None
        if self.silo_mode == "explode":
            raise RuntimeError("silo exploded")
        audit = self.issued_audit
        if self.silo_mode == "wrong":
            audit = _audit(
                self.issued_audit["head_sha"],
                [*self.issued_audit["result"]["findings"], _finding_def("extra-id", 200)],
                run_id=self.issued_audit["run_id"],
            )
        return json.dumps(audit).encode("utf-8")

    def _fake_gh_run_download(self, *, repository, run_id, artifact_name, dest_dir, token):
        self.gh_calls.append((repository, run_id, artifact_name))
        if self.gh_mode != "ok":
            return None
        target = dest_dir / "primary-review-audit.json"
        target.write_text(json.dumps(self.issued_audit), encoding="utf-8")
        return target.read_bytes()

    def fetch_argvs(self):
        return [argv for argv in self.git_argvs if "fetch" in argv]

    def boundary_calls(self) -> int:
        return len(self.silo_keys) + len(self.gh_calls) + len(self.git_argvs)


def _collect(spy: _Spies, *, receipts, sha_a, sha_b, current_audit=None):
    """Drive the real collector; silo/gh are faked, git runs for real."""
    if current_audit is None:
        current_audit = _audit(sha_b, CURRENT_FINDINGS, run_id=999)
    evidence_map = NEI.collect_neighbor_evidence(
        receipts=receipts,
        repository=REPOSITORY,
        repository_id=REPOSITORY_ID,
        pr_number=PR_NUMBER,
        current_head_sha=sha_b,
        audit_digest=CONV.canonical_audit_digest(current_audit),
        legacy_raw_audit_digest=None,
        terminal_history_rows=_terminal_rows(sha_a),
        token="test-token",
    )
    return evidence_map, current_audit


def _evaluate(current_audit, head_sha, receipt, evidence_map):
    return AGG.evaluate(
        quality_result="success",
        primary_result="failure",
        preflight_result="success",
        runner="self",
        is_draft=False,
        is_fork=False,
        review_expected=True,
        audit=current_audit,
        audit_error=None,
        identity=AGG.Identity(
            repository_id=REPOSITORY_ID, head_sha=head_sha,
            run_id=999, run_attempt=1, pr=PR_NUMBER,
        ),
        pr_author="octocat",
        audit_source_attempt=1,
        audit_artifact_name="primary-audit-v2",
        scope=_scope_for(current_audit),
        audit_digest=CONV.canonical_audit_digest(current_audit),
        legacy_raw_audit_digest=None,
        waiver_receipts=(receipt,),
        repository=REPOSITORY,
        neighbor_evidence=evidence_map,
    )


def _unavailable_line(receipt, reason: str) -> str:
    key_sha = hashlib.sha256(receipt.finding_key.encode()).hexdigest()[:12]
    return f"GATE-NEIGHBOR-EVIDENCE-UNAVAILABLE finding_key_sha={key_sha} reason={reason}"


# ── hunk parsing / window / projection ────────────────────────────────────


def test_pure_insertion_lands_between_old_lines_and_touches_by_closed_interval():
    # `-a,0` is a pure insertion landing between old line a and a+1; as the
    # closed interval [a, a+1] it touches the window [lo, hi] iff lo-1<=a<=hi.
    assert NEI.parse_unified_zero_hunks("@@ -183,0 +183,3 @@") == [(183, 0, 183, 3)]
    assert NEI.anchor_window_touched([(183, 0, 183, 3)], 187, TOLERANCE) is True   # a == lo
    assert NEI.anchor_window_touched([(182, 0, 182, 3)], 187, TOLERANCE) is True   # [182,183]∩[183,191]
    assert NEI.anchor_window_touched([(181, 0, 181, 3)], 187, TOLERANCE) is False  # fully before


def test_pure_deletion_window_edges_and_accumulated_projection():
    # A pure deletion (+c,0) keeps its old range; edges land exactly at ±N.
    assert NEI.anchor_window_touched([(188, 3, 185, 0)], 187, TOLERANCE) is True    # [188,190]
    assert NEI.anchor_window_touched([(192, 3, 189, 0)], 187, TOLERANCE) is False   # starts hi+1
    assert NEI.anchor_window_touched([(191, 1, 191, 1)], 187, TOLERANCE) is True    # starts at hi
    assert NEI.anchor_window_touched([(182, 1, 182, 1)], 187, TOLERANCE) is False   # ends at lo-1
    # Hunks entirely before the window accumulate new−old onto the anchor.
    before = [(30, 2, 30, 4), (100, 0, 103, 2), (150, 4, 154, 2), (5, 1, 7, 1)]
    assert NEI.anchor_window_touched(before, 187, TOLERANCE) is False
    # +2 +2 -2 +0 = +2 net lines before the window → 187 maps to 189.
    assert NEI.projected_anchor_line(before, 187, TOLERANCE) == 189
    assert NEI.projected_anchor_line([(5, 1, 7, 1)], 187, TOLERANCE) == 187  # net zero


def test_anchor_projection_shares_total_git_deadline(caller_repo, monkeypatch):
    timeouts = []
    run_git = NEI._run_git

    def record_git_timeout(argv, *, cwd=None, timeout_s):
        timeouts.append(timeout_s)
        time.sleep(0.01)
        return run_git(argv, cwd=cwd, timeout_s=timeout_s)

    monkeypatch.setattr(NEI, "_run_git", record_git_timeout)
    updates = NEI.collect_anchor_updates(
        findings=[{"id": "anchor-id", "file": ANCHOR_FILE, "line": 187,
                   "quote": f"ANCHOR_187 = '{LITERAL}'"}],
        repository=REPOSITORY, previous_head_sha=caller_repo["sha_a"],
        current_head_sha=caller_repo["sha_b"], token="fixture-token",
    )

    assert updates == {"anchor-id": 190}
    assert len(timeouts) >= 8
    assert all(0 < timeout_s <= NEI._GIT_SETUP_TIMEOUT_S for timeout_s in timeouts)
    assert all(later < earlier for earlier, later in zip(timeouts, timeouts[1:]))


# ── end-to-end: collector + evaluate (production record_dispositions entry) ──


def test_end_to_end_drifted_receipt_is_consumed_as_neighbor(caller_repo, monkeypatch, capsys):
    sha_a, sha_b = caller_repo["sha_a"], caller_repo["sha_b"]
    issued_audit = _audit(sha_a, [_finding_def("t-id", 187)])
    spy = _Spies(monkeypatch, issued_audit=issued_audit)
    receipt = _receipt(sha_a, CONV.canonical_audit_digest(issued_audit))

    evidence_map, current_audit = _collect(spy, receipts=(receipt,), sha_a=sha_a, sha_b=sha_b)
    assert len(evidence_map) == 1
    evidence = evidence_map[receipt.finding_key]
    # Real diff of the real repo: 3 inserted lines before the window → 187→190.
    assert evidence.anchor_window_touched is False
    assert evidence.anchor_line_current == 190
    assert evidence.current_head_sha == sha_b
    assert evidence.rerun_exit_code == 0          # the rerun really ran
    assert evidence.rerun_match_count >= 1        # and really matched

    # Cross-boundary: silo key bytes follow the issuance workflow's key rule.
    expected_key = (
        f"d14/{REPOSITORY_ID}/"
        f"primary-audit-v2-{REPOSITORY_ID}-{sha_a}-{RUN_ID}-{ATTEMPT}"
        f"/primary-review-audit.json"
    )
    assert spy.silo_keys == [expected_key]
    # Cross-boundary: fetch argv is shallow and never blob-filtered (gate#302).
    fetches = spy.fetch_argvs()
    assert {argv[-1] for argv in fetches} == {sha_a, sha_b}
    assert spy.fetch_timeouts == [NEI.GIT_FETCH_TIMEOUT_S] * len(fetches)
    for argv in fetches:
        assert "--filter" not in " ".join(argv)
        assert argv[-4:-2] == ["--depth", "1"]
        assert argv[-2] == "origin"
    # Cross-boundary: rerun argv differs from the receipt argv only at index 6.
    receipt_argv = receipt.counterevidence["gate_rerun"]["argv"]
    assert evidence.rerun_argv[:6] == receipt_argv[:6]
    assert evidence.rerun_argv[6] == sha_b
    assert evidence.rerun_argv[7:] == receipt_argv[7:]

    # Success line is greppable; no file contents or command literals echoed.
    key_sha = hashlib.sha256(receipt.finding_key.encode()).hexdigest()[:12]
    assert (
        f"GATE-NEIGHBOR-EVIDENCE finding_key_sha={key_sha} "
        f"window_touched=False rerun_exit=0 match_count=1 anchor_line_current=190"
    ) in capsys.readouterr().out
    assert LITERAL not in capsys.readouterr().out

    # Production entry: evaluate() → record_dispositions consumes the receipt.
    outcome = _evaluate(current_audit, sha_b, receipt, evidence_map)
    statuses = {s.receipt.finding_id: s for s in outcome.disposition_audit.statuses}
    t_status = statuses["t-id"]
    assert t_status.valid is True and t_status.active is True
    assert t_status.matched_by == "neighbor"
    assert t_status.audit_digest_drift is True
    assert outcome.disposition_audit.remaining_p1_ids == ("p2-id",)
    assert outcome.disposition_audit.recorded_finding_ids == ("t-id",)


def test_change_inside_the_window_expires_the_receipt(caller_repo, monkeypatch):
    sha_a, sha_bp = caller_repo["sha_a"], caller_repo["sha_bp"]
    issued_audit = _audit(sha_a, [_finding_def("t-id", 187)])
    spy = _Spies(monkeypatch, issued_audit=issued_audit)
    receipt = _receipt(sha_a, CONV.canonical_audit_digest(issued_audit))

    # The drift commit B' edits inside [183, 191]: window touched → voided.
    current_audit = _audit(sha_bp, [_finding_def("t-id", 190), _finding_def("p2-id", 185)], run_id=999)
    evidence_map, current_audit = _collect(
        spy, receipts=(receipt,), sha_a=sha_a, sha_b=sha_bp, current_audit=current_audit,
    )
    assert evidence_map[receipt.finding_key].anchor_window_touched is True
    outcome = _evaluate(current_audit, sha_bp, receipt, evidence_map)
    status = outcome.disposition_audit.statuses[0]
    assert status.reason_code == "counterevidence_expired"
    assert status.active is False
    assert "t-id" in outcome.disposition_audit.remaining_p1_ids


def test_file_deleted_on_current_head_counts_as_touched(caller_repo, monkeypatch):
    sha_a, sha_bd = caller_repo["sha_a"], caller_repo["sha_bd"]
    issued_audit = _audit(sha_a, [_finding_def("t-id", 187)])
    spy = _Spies(monkeypatch, issued_audit=issued_audit)
    receipt = _receipt(sha_a, CONV.canonical_audit_digest(issued_audit))
    evidence_map, _ = _collect(spy, receipts=(receipt,), sha_a=sha_a, sha_b=sha_bd)
    assert evidence_map[receipt.finding_key].anchor_window_touched is True


def test_audit_digest_mismatch_keeps_head_sha_mismatch_and_is_greppable(caller_repo, monkeypatch, capsys):
    sha_a, sha_b = caller_repo["sha_a"], caller_repo["sha_b"]
    issued_audit = _audit(sha_a, [_finding_def("t-id", 187)])
    spy = _Spies(monkeypatch, issued_audit=issued_audit)
    spy.silo_mode = "wrong"
    receipt = _receipt(sha_a, CONV.canonical_audit_digest(issued_audit))

    evidence_map, current_audit = _collect(spy, receipts=(receipt,), sha_a=sha_a, sha_b=sha_b)
    assert evidence_map == {}
    assert _unavailable_line(receipt, "audit_digest_no_match") in capsys.readouterr().out
    outcome = _evaluate(current_audit, sha_b, receipt, evidence_map)
    status = outcome.disposition_audit.statuses[0]
    # Fail-closed: no evidence means the existing drift reason code stands.
    assert status.reason_code == "head_sha_mismatch"
    assert status.active is False
    assert outcome.disposition_audit.remaining_p1_ids == ("p2-id", "t-id")


def test_no_candidate_receipts_means_zero_git_and_network_calls(caller_repo, monkeypatch):
    sha_a, sha_b = caller_repo["sha_a"], caller_repo["sha_b"]
    current_audit = _audit(sha_b, CURRENT_FINDINGS, run_id=999)
    spy = _Spies(monkeypatch, issued_audit=_audit(sha_a, [_finding_def("t-id", 187)]))
    # Exact receipt: same head and digest as the current round — fast path.
    exact = replace(
        _receipt(sha_a, "0" * 64),
        head_sha=sha_b,
        audit_digest=CONV.canonical_audit_digest(current_audit),
        epoch=CONV.derive_epoch(_scope_for(current_audit)),
    )
    deferred = replace(_receipt(sha_a, "0" * 64), disposition="deferred")
    evidence_map, _ = _collect(spy, receipts=(exact, deferred), sha_a=sha_a, sha_b=sha_b)
    assert evidence_map == {}
    assert spy.boundary_calls() == 0


def test_silo_absent_falls_back_to_gh_run_download_with_issuance_key(caller_repo, monkeypatch):
    sha_a, sha_b = caller_repo["sha_a"], caller_repo["sha_b"]
    issued_audit = _audit(sha_a, [_finding_def("t-id", 187)])
    spy = _Spies(monkeypatch, issued_audit=issued_audit)
    spy.silo_mode = "absent"
    spy.gh_mode = "ok"
    receipt = _receipt(sha_a, CONV.canonical_audit_digest(issued_audit))
    evidence_map, current_audit = _collect(spy, receipts=(receipt,), sha_a=sha_a, sha_b=sha_b)
    # Second-level fetch reuses the issuance artifact name; consumption still works.
    assert spy.gh_calls == [(
        REPOSITORY, RUN_ID,
        f"primary-audit-v2-{REPOSITORY_ID}-{sha_a}-{RUN_ID}-{ATTEMPT}",
    )]
    assert evidence_map[receipt.finding_key].anchor_line_current == 190
    outcome = _evaluate(current_audit, sha_b, receipt, evidence_map)
    assert outcome.disposition_audit.statuses[0].matched_by == "neighbor"


def test_unavailable_reasons_are_greppable_and_produce_no_evidence(caller_repo, monkeypatch, capsys):
    sha_a, sha_b = caller_repo["sha_a"], caller_repo["sha_b"]
    issued_audit = _audit(sha_a, [_finding_def("t-id", 187)])
    receipt = _receipt(sha_a, CONV.canonical_audit_digest(issued_audit))

    # silo hard failure (rc=1): the issuance contract fails the step, so the
    # receipt yields no evidence and never retried via gh.
    spy = _Spies(monkeypatch, issued_audit=issued_audit)
    spy.silo_mode = "explode"
    assert _collect(spy, receipts=(receipt,), sha_a=sha_a, sha_b=sha_b)[0] == {}
    assert _unavailable_line(receipt, "audit_fetch_failed") in capsys.readouterr().out
    assert spy.gh_calls == []

    # issuing head absent from the loaded terminal history: no I/O at all.
    spy = _Spies(monkeypatch, issued_audit=issued_audit)
    evidence_map = NEI.collect_neighbor_evidence(
        receipts=(receipt,), repository=REPOSITORY, repository_id=REPOSITORY_ID,
        pr_number=PR_NUMBER, current_head_sha=sha_b, audit_digest="9" * 64,
        legacy_raw_audit_digest=None, terminal_history_rows=[], token="test-token",
    )
    assert evidence_map == {}
    assert _unavailable_line(receipt, "receipt_head_not_in_history") in capsys.readouterr().out
    assert spy.silo_keys == [] and spy.gh_calls == [] and spy.fetch_argvs() == []


# ── git subprocess timeouts / setup failures stay fail-closed (resume-1 P1) ──


def test_fetch_timeout_yields_unavailable_line_and_head_sha_mismatch(caller_repo, monkeypatch, capsys):
    sha_a, sha_b = caller_repo["sha_a"], caller_repo["sha_b"]
    issued_audit = _audit(sha_a, [_finding_def("t-id", 187)])
    spy = _Spies(monkeypatch, issued_audit=issued_audit)
    spy.git_fail_mode = "fetch_timeout"
    receipt = _receipt(sha_a, CONV.canonical_audit_digest(issued_audit))

    # A timed-out fetch must not crash the gate job: {} plus one greppable line.
    evidence_map, current_audit = _collect(spy, receipts=(receipt,), sha_a=sha_a, sha_b=sha_b)
    assert evidence_map == {}
    assert _unavailable_line(receipt, "git_fetch_timeout") in capsys.readouterr().out
    # Through the production evaluate() path the conclusion keeps the existing
    # fail-closed drift reason code.
    outcome = _evaluate(current_audit, sha_b, receipt, evidence_map)
    assert outcome.disposition_audit.statuses[0].reason_code == "head_sha_mismatch"
    assert "t-id" in outcome.disposition_audit.remaining_p1_ids


def test_diff_timeout_yields_unavailable_line_and_no_evidence(caller_repo, monkeypatch, capsys):
    sha_a, sha_b = caller_repo["sha_a"], caller_repo["sha_b"]
    issued_audit = _audit(sha_a, [_finding_def("t-id", 187)])
    spy = _Spies(monkeypatch, issued_audit=issued_audit)
    spy.git_fail_mode = "diff_timeout"
    receipt = _receipt(sha_a, CONV.canonical_audit_digest(issued_audit))

    evidence_map, _ = _collect(spy, receipts=(receipt,), sha_a=sha_a, sha_b=sha_b)
    assert evidence_map == {}
    assert _unavailable_line(receipt, "git_diff_timeout") in capsys.readouterr().out


def test_git_init_nonzero_fails_before_any_fetch(caller_repo, monkeypatch, capsys):
    sha_a, sha_b = caller_repo["sha_a"], caller_repo["sha_b"]
    issued_audit = _audit(sha_a, [_finding_def("t-id", 187)])
    spy = _Spies(monkeypatch, issued_audit=issued_audit)
    spy.git_fail_mode = "init_fail"
    receipt = _receipt(sha_a, CONV.canonical_audit_digest(issued_audit))

    evidence_map, _ = _collect(spy, receipts=(receipt,), sha_a=sha_a, sha_b=sha_b)
    assert evidence_map == {}
    assert _unavailable_line(receipt, "git_fetch_failed") in capsys.readouterr().out
    # Setup failed: the collector never reaches the network/fetch boundary.
    assert spy.fetch_argvs() == []


# ── production silo subprocess path (fake store script, real subprocess) ──


def test_production_silo_get_maps_rc2_to_absent(tmp_path, monkeypatch):
    store = tmp_path / "fake_silo.py"
    store.write_text(
        "import sys, pathlib\n"
        "args = sys.argv[1:]\n"
        "key = args[args.index('--key') + 1]\n"
        "if 'missing' in key:\n    sys.exit(2)\n"
        "if 'broken' in key:\n    sys.exit(1)\n"
        "dest = args[args.index('--dest') + 1]\n"
        "pathlib.Path(dest, 'primary-review-audit.json').write_bytes(b'{}')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SILO_STORE", str(store))
    assert NEI._silo_audit_get("d14/1/ok/primary-review-audit.json", 30) == b"{}"
    assert NEI._silo_audit_get("d14/1/missing/primary-review-audit.json", 30) is None
    with pytest.raises(RuntimeError):
        NEI._silo_audit_get("d14/1/broken/primary-review-audit.json", 30)


def test_evaluate_default_neighbor_evidence_keeps_current_reason(caller_repo):
    sha_a, sha_b = caller_repo["sha_a"], caller_repo["sha_b"]
    issued_audit = _audit(sha_a, [_finding_def("t-id", 187)])
    current_audit = _audit(sha_b, CURRENT_FINDINGS, run_id=999)
    receipt = _receipt(sha_a, CONV.canonical_audit_digest(issued_audit))
    outcome = _evaluate(current_audit, sha_b, receipt, None)
    status = outcome.disposition_audit.statuses[0]
    assert status.reason_code == "head_sha_mismatch"
    assert outcome.disposition_audit.remaining_p1_ids == ("p2-id", "t-id")
