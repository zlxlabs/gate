"""Neighbor-evidence collection + gate-job wiring tests (gate#297).

End-to-end contract: a drifted (head_sha / audit_digest no longer equal)
false-positive receipt is consumed as ``matched_by == "neighbor"`` only when
the collector proves — against the REAL git history of the caller repo — that
the anchor window survived the drift and the allowlisted counterevidence
still refutes on the current head.  External services (Silo, the GitHub
artifact layer) are faked; git itself is never faked: the collector spawns
real `git fetch --depth 1` / `git diff --unified=0` / `git grep` subprocesses
against a local file:// origin (url.insteadOf rewrite, allowAnySHA1InWant).

Locked invariants here (design §7): the silo key bytes match the issuance
workflow's key rule; git fetch argv never carries --filter (gate#302); the
rerun argv differs from the receipt's stored argv only at index 6 (head);
zero candidate receipts means zero network/git calls; any collection failure
prints one GATE-NEIGHBOR-EVIDENCE-UNAVAILABLE line and leaves the consumer on
its existing fail-closed reason codes.
"""

import hashlib
import importlib.util
import json
import subprocess
import sys
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
# first, and neighbor_evidence reuses the host process's module so Scope
# dataclass identity matches end to end.
AGG = _load("gate_aggregate_test", AGGREGATE_PATH)
NEI = _load("gate_neighbor_evidence_test", NEIGHBOR_PATH)
CONV = NEI._CONVERGENCE
assert CONV is AGG._CONVERGENCE

TOLERANCE = CONV.NEIGHBOR_LINE_TOLERANCE  # 4, imported — never a local 4
REPOSITORY = "zlxlabs/caller"
REPOSITORY_ID = 123
PR_NUMBER = 42
ANCHOR_FILE = "src/widget.py"
COUNTEREVIDENCE_LITERAL = "locked-behavior"
ISSUING_RUN_ID = 77
ISSUING_RUN_ATTEMPT = 1


def _widget_source() -> str:
    lines = [f"# filler line {i}" for i in range(1, 251)]
    lines[186] = f"ANCHOR_{187} = '{COUNTEREVIDENCE_LITERAL}'"  # line 187, 1-indexed
    return "\n".join(lines) + "\n"


def _git(repo: Path, *argv: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *argv],
        check=True,
        capture_output=True,
    )


def _commit_file(repo: Path, content: str, message: str) -> str:
    (repo / ANCHOR_FILE).parent.mkdir(parents=True, exist_ok=True)
    (repo / ANCHOR_FILE).write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "--quiet", "-m", message)
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _drift_outside_window(source: str) -> str:
    """Insert 3 lines after old line 30: line 187 drifts to 190, window untouched."""
    marker = "# filler line 30\n"
    assert source.count(marker) == 1
    return source.replace(marker, marker + "# drift a\n# drift b\n# drift c\n", 1)


def _drift_inside_window(source: str) -> str:
    """Insert 3 lines after old line 189: inside the [183, 191] anchor window."""
    marker = "# filler line 189\n"
    assert source.count(marker) == 1
    return source.replace(marker, marker + "# in-window a\n# in-window b\n# in-window c\n", 1)


@pytest.fixture()
def caller_repo(tmp_path, monkeypatch):
    repo = tmp_path / "caller-origin"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo, "config", "user.email", "fixture@example.test")
    _git(repo, "config", "user.name", "fixture")
    sha_a = _commit_file(repo, _widget_source(), "issuing head")
    sha_b = _commit_file(repo, _drift_outside_window(_widget_source()), "current head")
    _git(repo, "checkout", "--quiet", sha_a)
    sha_bp = _commit_file(repo, _drift_inside_window(_widget_source()), "in-window drift")
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
    return {"repo": repo, "sha_a": sha_a, "sha_b": sha_b, "sha_bp": sha_bp}


def _finding(finding_id: str, line: int, *, file=ANCHOR_FILE, category="correctness"):
    return {
        "id": finding_id, "severity": "major", "trigger_kind": "inferred",
        "file": file, "line": line, "category": category,
    }


def _audit(head_sha: str, findings: list[dict], *, run_id=ISSUING_RUN_ID) -> dict:
    return {
        "kind": "primary_review", "schema_version": 1,
        "repository_id": REPOSITORY_ID, "head_sha": head_sha,
        "run_id": run_id, "run_attempt": ISSUING_RUN_ATTEMPT, "pr": PR_NUMBER,
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
    """A signed refuted receipt bound to the issuing round (head/digest)."""
    issued_finding = _finding(finding_id, 187)
    return CONV.DispositionReceipt(
        schema_version=CONV.DISPOSITION_RECEIPT_SCHEMA_VERSION,
        disposition="false-positive",
        repository_id=str(REPOSITORY_ID),
        pr_number=PR_NUMBER,
        epoch=CONV.derive_epoch(_scope_for(_audit(head_sha, [_finding(finding_id, 187)]))),
        head_sha=head_sha,
        audit_digest=audit_digest,
        finding_id=finding_id,
        finding_key=CONV.canonical_finding_key(issued_finding),
        reason="locked upstream behavior",
        counterevidence={
            "command": "pytest -q tests/test_regression.py",
            "output": "1 passed",
            "result": "refuted",
            "pointer": "tests/test_regression.py::test_regression",
            "gate_rerun": {
                "argv": ["git", "grep", "-n", "-F", "-e", COUNTEREVIDENCE_LITERAL,
                         head_sha, "--", ANCHOR_FILE],
                "exit_code": 0,
                "match_count": 1,
                "stdout_sha256": hashlib.sha256(
                    f"{ANCHOR_FILE}:187:{COUNTEREVIDENCE_LITERAL}".encode()
                ).hexdigest(),
                "excerpt": f"{ANCHOR_FILE}:187:{COUNTEREVIDENCE_LITERAL}",
            },
        },
        approver="octocat",
        approver_id=1,
        approved_at="2026-08-30T12:00:00Z",
    )


def _terminal_rows(head_sha: str) -> list[dict]:
    return [{
        "schema_version": 1, "repository": REPOSITORY,
        "run_id": ISSUING_RUN_ID, "run_attempt": ISSUING_RUN_ATTEMPT,
        "head_sha": head_sha, "gate_result": "fail",
        "classification": "code_fail", "reason_code": "primary_findings",
        "_eligible_primary_round": True,
    }]


class _Spies:
    """Records the bytes handed to each faked external boundary."""

    def __init__(self, monkeypatch, *, issued_audit: dict):
        self.issued_audit = issued_audit
        self.silo_keys: list[str] = []
        self.gh_calls: list[tuple[str, int, str]] = []
        self.git_argvs: list[list[str]] = []
        self.silo_mode = "ok"    # ok | absent | wrong | explode
        self.gh_mode = "absent"  # absent | ok

        self._real_run_git = NEI._run_git
        monkeypatch.setattr(NEI, "_run_git", self._spy_run_git)
        monkeypatch.setattr(NEI, "_silo_audit_get", self._fake_silo_audit_get)
        monkeypatch.setattr(NEI, "_gh_run_download", self._fake_gh_run_download)

    def _spy_run_git(self, argv, *, cwd=None, timeout_s):
        self.git_argvs.append(list(argv))
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
                [*self.issued_audit["result"]["findings"], _finding("extra-id", 200)],
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

    def fetch_argvs(self) -> list[list[str]]:
        return [argv for argv in self.git_argvs if "fetch" in argv]

    def boundary_calls(self) -> int:
        return len(self.silo_keys) + len(self.gh_calls) + len(self.git_argvs)


CURRENT_FINDINGS = [_finding("p2-id", 185), _finding("t-id", 190)]
CURRENT_AUDIT_HEAD = "current"


def _collect(spy: _Spies, *, receipts, sha_a, sha_b, current_audit=None):
    """Drive the real collector; silo/gh are faked, git runs for real."""
    if current_audit is None:
        current_audit = _audit(sha_b, CURRENT_FINDINGS, run_id=999)
    return NEI.collect_neighbor_evidence(
        receipts=receipts,
        repository=REPOSITORY,
        repository_id=REPOSITORY_ID,
        pr_number=PR_NUMBER,
        current_head_sha=sha_b,
        audit_digest=CONV.canonical_audit_digest(current_audit),
        legacy_raw_audit_digest=None,
        terminal_history_rows=_terminal_rows(sha_a),
        token="test-token",
    ), current_audit


def _evaluate(current_audit, sha_b, receipt, evidence_map):
    current_scope = _scope_for(current_audit)
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
            repository_id=REPOSITORY_ID, head_sha=sha_b,
            run_id=999, run_attempt=1, pr=PR_NUMBER,
        ),
        pr_author="octocat",
        audit_source_attempt=1,
        audit_artifact_name="primary-audit-v2",
        scope=current_scope,
        audit_digest=CONV.canonical_audit_digest(current_audit),
        legacy_raw_audit_digest=None,
        waiver_receipts=(receipt,),
        repository=REPOSITORY,
        neighbor_evidence=evidence_map,
    )


# ── pure hunk parsing / window / projection ───────────────────────────────


def test_pure_insertion_hunks_touch_by_landing_point_not_range():
    # `-a,0` is a pure insertion landing between old line a and a+1; treat it
    # as the closed interval [a, a+1] when intersecting the window.
    hunks = NEI.parse_unified_zero_hunks("@@ -183,0 +183,3 @@")
    assert NEI.anchor_window_touched(hunks, 187, TOLERANCE) is True  # a=183 == lo
    hunks = NEI.parse_unified_zero_hunks("@@ -182,0 +182,3 @@")
    assert NEI.anchor_window_touched(hunks, 187, TOLERANCE) is True  # [182,183] ∩ [183,191]
    hunks = NEI.parse_unified_zero_hunks("@@ -181,0 +181,3 @@")
    assert NEI.anchor_window_touched(hunks, 187, TOLERANCE) is False  # fully before


def test_pure_deletion_hunks_use_their_old_range():
    hunks = NEI.parse_unified_zero_hunks("@@ -188,3 +185,0 @@")
    assert NEI.anchor_window_touched(hunks, 187, TOLERANCE) is True   # [188,190] ∩ window
    hunks = NEI.parse_unified_zero_hunks("@@ -192,3 +189,0 @@")
    assert NEI.anchor_window_touched(hunks, 187, TOLERANCE) is False  # starts at hi+1
    hunks = NEI.parse_unified_zero_hunks("@@ -100,3 +97,0 @@")
    assert NEI.anchor_window_touched(hunks, 187, TOLERANCE) is False
    assert NEI.projected_anchor_line(hunks, 187, TOLERANCE) == 184    # -3 deleted before


def test_window_edges_exactly_at_tolerance():
    inside = NEI.parse_unified_zero_hunks("@@ -191,1 +191,1 @@")  # starts at hi
    assert NEI.anchor_window_touched(inside, 187, TOLERANCE) is True
    outside_before = NEI.parse_unified_zero_hunks("@@ -182,1 +182,1 @@")  # ends at lo-1
    assert NEI.anchor_window_touched(outside_before, 187, TOLERANCE) is False
    outside_after = NEI.parse_unified_zero_hunks("@@ -192,1 +192,1 @@")  # starts at hi+1
    assert NEI.anchor_window_touched(outside_after, 187, TOLERANCE) is False


def test_multiple_out_of_window_hunks_accumulate_offset():
    hunks = NEI.parse_unified_zero_hunks(
        "@@ -30,2 +30,4 @@\n@@ -100,0 +103,2 @@\n@@ -150,4 +154,2 @@\n"
    )
    assert NEI.anchor_window_touched(hunks, 187, TOLERANCE) is False
    # +2 (insertion) +2 (insertion) -2 (shrink) = +2 net lines before the window
    assert NEI.projected_anchor_line(hunks, 187, TOLERANCE) == 189


def test_missing_hunk_counts_default_to_one():
    hunks = NEI.parse_unified_zero_hunks("@@ -5 +7 @@")
    assert hunks == [(5, 1, 7, 1)]
    # one line replaced by one line: net zero offset for lines after it
    assert NEI.projected_anchor_line(hunks, 187, TOLERANCE) == 187


# ── end-to-end: collector + evaluate (production record_dispositions entry) ──


def test_end_to_end_drifted_receipt_is_consumed_as_neighbor(
    caller_repo, monkeypatch, capsys,
):
    sha_a, sha_b = caller_repo["sha_a"], caller_repo["sha_b"]
    issued_audit = _audit(sha_a, [_finding("t-id", 187)])
    spy = _Spies(monkeypatch, issued_audit=issued_audit)
    receipt = _receipt(sha_a, CONV.canonical_audit_digest(issued_audit))

    evidence_map, current_audit = _collect(
        spy, receipts=(receipt,), sha_a=sha_a, sha_b=sha_b,
    )
    assert len(evidence_map) == 1
    evidence = evidence_map[receipt.finding_key]
    # Real diff of the real repo: 3 inserted lines before the window → 187→190.
    assert evidence.anchor_window_touched is False
    assert evidence.anchor_line_current == 190
    assert evidence.current_head_sha == sha_b
    # The rerun really ran on the current head and really matched.
    assert evidence.rerun_exit_code == 0
    assert evidence.rerun_match_count >= 1

    # Cross-boundary: silo key bytes follow the issuance workflow's key rule.
    expected_key = (
        f"d14/{REPOSITORY_ID}/"
        f"primary-audit-v2-{REPOSITORY_ID}-{sha_a}-{ISSUING_RUN_ID}-{ISSUING_RUN_ATTEMPT}"
        f"/primary-review-audit.json"
    )
    assert spy.silo_keys == [expected_key]
    # Cross-boundary: fetch argv is shallow and never blob-filtered (gate#302).
    fetches = spy.fetch_argvs()
    assert len(fetches) == 2
    fetched = {argv[-1] for argv in fetches}
    assert fetched == {sha_a, sha_b}
    for argv in fetches:
        assert "--filter" not in " ".join(argv)
        assert argv[-4:-2] == ["--depth", "1"]
        assert argv[-2] == "origin"
    # Cross-boundary: rerun argv differs from the receipt argv only at index 6.
    receipt_argv = receipt.counterevidence["gate_rerun"]["argv"]
    assert evidence.rerun_argv[:6] == receipt_argv[:6]
    assert evidence.rerun_argv[6] == sha_b
    assert evidence.rerun_argv[7:] == receipt_argv[7:]

    # Success line is greppable, no file contents or command literals echoed.
    out = capsys.readouterr().out
    key_sha = hashlib.sha256(receipt.finding_key.encode()).hexdigest()[:12]
    assert (
        f"GATE-NEIGHBOR-EVIDENCE finding_key_sha={key_sha} "
        f"window_touched=False rerun_exit=0 match_count=1 anchor_line_current=190"
    ) in out
    assert COUNTEREVIDENCE_LITERAL not in out

    # Production entry point: evaluate() → record_dispositions consumes it.
    outcome = _evaluate(current_audit, sha_b, receipt, evidence_map)
    statuses = {
        status.receipt.finding_id: status
        for status in outcome.disposition_audit.statuses
    }
    t_status = statuses["t-id"]
    assert t_status.valid is True and t_status.active is True
    assert t_status.matched_by == "neighbor"
    assert t_status.audit_digest_drift is True
    assert outcome.disposition_audit.remaining_p1_ids == ("p2-id",)
    # The receipt covered the drifted finding even though its id differs.
    assert outcome.disposition_audit.recorded_finding_ids == ("t-id",)


def test_change_inside_the_window_expires_the_receipt(caller_repo, monkeypatch):
    sha_a, sha_bp = caller_repo["sha_a"], caller_repo["sha_bp"]
    issued_audit = _audit(sha_a, [_finding("t-id", 187)])
    spy = _Spies(monkeypatch, issued_audit=issued_audit)
    receipt = _receipt(sha_a, CONV.canonical_audit_digest(issued_audit))

    # The drift commit B' edits inside [183, 191]: window touched → voided.
    current_audit = _audit(sha_bp, [_finding("t-id", 190), _finding("p2-id", 185)], run_id=999)
    evidence_map, current_audit = _collect(
        spy, receipts=(receipt,), sha_a=sha_a, sha_b=sha_bp, current_audit=current_audit,
    )
    evidence = evidence_map[receipt.finding_key]
    assert evidence.anchor_window_touched is True

    outcome = _evaluate(current_audit, sha_bp, receipt, evidence_map)
    status = outcome.disposition_audit.statuses[0]
    assert status.reason_code == "counterevidence_expired"
    assert status.active is False
    assert "t-id" in outcome.disposition_audit.remaining_p1_ids


def test_audit_digest_mismatch_keeps_head_sha_mismatch_and_is_greppable(
    caller_repo, monkeypatch, capsys,
):
    sha_a, sha_b = caller_repo["sha_a"], caller_repo["sha_b"]
    issued_audit = _audit(sha_a, [_finding("t-id", 187)])
    spy = _Spies(monkeypatch, issued_audit=issued_audit)
    spy.silo_mode = "wrong"
    receipt = _receipt(sha_a, CONV.canonical_audit_digest(issued_audit))

    evidence_map, current_audit = _collect(
        spy, receipts=(receipt,), sha_a=sha_a, sha_b=sha_b,
    )
    assert evidence_map == {}
    key_sha = hashlib.sha256(receipt.finding_key.encode()).hexdigest()[:12]
    assert (
        f"GATE-NEIGHBOR-EVIDENCE-UNAVAILABLE finding_key_sha={key_sha} "
        f"reason=audit_digest_no_match"
    ) in capsys.readouterr().out

    outcome = _evaluate(current_audit, sha_b, receipt, evidence_map)
    status = outcome.disposition_audit.statuses[0]
    # Fail-closed: no evidence means the existing drift reason code stands.
    assert status.reason_code == "head_sha_mismatch"
    assert status.active is False
    assert outcome.disposition_audit.remaining_p1_ids == ("p2-id", "t-id")


def test_no_candidate_receipts_means_zero_git_and_network_calls(
    caller_repo, monkeypatch,
):
    sha_a, sha_b = caller_repo["sha_a"], caller_repo["sha_b"]
    current_audit = _audit(sha_b, CURRENT_FINDINGS, run_id=999)
    spy = _Spies(monkeypatch, issued_audit=_audit(sha_a, [_finding("t-id", 187)]))
    # Exact receipt: same head and same digest as the current round — the
    # consumer's fast path, not a neighbor candidate.
    exact = replace(
        _receipt(sha_a, "0" * 64),
        head_sha=sha_b,
        audit_digest=CONV.canonical_audit_digest(current_audit),
        epoch=CONV.derive_epoch(_scope_for(current_audit)),
    )
    deferred = replace(_receipt(sha_a, "0" * 64), disposition="deferred")

    evidence_map, current_audit = _collect(
        spy, receipts=(exact, deferred), sha_a=sha_a, sha_b=sha_b,
    )
    assert evidence_map == {}
    assert spy.boundary_calls() == 0


def test_silo_absent_falls_back_to_gh_run_download_with_issuance_key(
    caller_repo, monkeypatch,
):
    sha_a, sha_b = caller_repo["sha_a"], caller_repo["sha_b"]
    issued_audit = _audit(sha_a, [_finding("t-id", 187)])
    spy = _Spies(monkeypatch, issued_audit=issued_audit)
    spy.silo_mode = "absent"
    spy.gh_mode = "ok"
    receipt = _receipt(sha_a, CONV.canonical_audit_digest(issued_audit))

    evidence_map, current_audit = _collect(
        spy, receipts=(receipt,), sha_a=sha_a, sha_b=sha_b,
    )
    # Second-level fetch happened under the same artifact name the issuance
    # workflow derives, and the receipt was still consumed end to end.
    assert spy.gh_calls == [(
        REPOSITORY, ISSUING_RUN_ID,
        f"primary-audit-v2-{REPOSITORY_ID}-{sha_a}-{ISSUING_RUN_ID}-{ISSUING_RUN_ATTEMPT}",
    )]
    evidence = evidence_map[receipt.finding_key]
    assert evidence.anchor_line_current == 190
    outcome = _evaluate(current_audit, sha_b, receipt, evidence_map)
    assert outcome.disposition_audit.statuses[0].matched_by == "neighbor"


def test_silo_hard_failure_yields_unavailable_not_evidence(
    caller_repo, monkeypatch, capsys,
):
    sha_a, sha_b = caller_repo["sha_a"], caller_repo["sha_b"]
    issued_audit = _audit(sha_a, [_finding("t-id", 187)])
    spy = _Spies(monkeypatch, issued_audit=issued_audit)
    spy.silo_mode = "explode"
    receipt = _receipt(sha_a, CONV.canonical_audit_digest(issued_audit))

    evidence_map, _current_audit = _collect(
        spy, receipts=(receipt,), sha_a=sha_a, sha_b=sha_b,
    )
    assert evidence_map == {}
    key_sha = hashlib.sha256(receipt.finding_key.encode()).hexdigest()[:12]
    assert (
        f"GATE-NEIGHBOR-EVIDENCE-UNAVAILABLE finding_key_sha={key_sha} "
        f"reason=audit_fetch_failed"
    ) in capsys.readouterr().out
    assert "GATE-NEIGHBOR-EVIDENCE finding_key_sha=" not in capsys.readouterr().out


def test_head_not_in_terminal_history_yields_unavailable(
    caller_repo, monkeypatch, capsys,
):
    sha_a, sha_b = caller_repo["sha_a"], caller_repo["sha_b"]
    issued_audit = _audit(sha_a, [_finding("t-id", 187)])
    spy = _Spies(monkeypatch, issued_audit=issued_audit)
    receipt = _receipt(sha_a, CONV.canonical_audit_digest(issued_audit))

    evidence_map = NEI.collect_neighbor_evidence(
        receipts=(receipt,),
        repository=REPOSITORY,
        repository_id=REPOSITORY_ID,
        pr_number=PR_NUMBER,
        current_head_sha=sha_b,
        audit_digest="9" * 64,
        legacy_raw_audit_digest=None,
        terminal_history_rows=[],  # issuing round unknown → cannot address silo
        token="test-token",
    )
    assert evidence_map == {}
    key_sha = hashlib.sha256(receipt.finding_key.encode()).hexdigest()[:12]
    assert (
        f"GATE-NEIGHBOR-EVIDENCE-UNAVAILABLE finding_key_sha={key_sha} "
        f"reason=receipt_head_not_in_history"
    ) in capsys.readouterr().out
    # No audit/git boundary was touched: the run lookup precedes all I/O.
    assert spy.silo_keys == [] and spy.gh_calls == [] and spy.fetch_argvs() == []


# ── production silo subprocess path (fake store, real subprocess argv) ────


def test_production_silo_get_maps_rc2_to_absent(tmp_path, monkeypatch):
    store = tmp_path / "fake_silo.py"
    store.write_text(
        "import sys\n"
        "args = sys.argv[1:]\n"
        "key = args[args.index('--key') + 1]\n"
        "if 'missing' in key:\n"
        "    sys.exit(2)\n"
        "if 'broken' in key:\n"
        "    sys.exit(1)\n"
        "dest = args[args.index('--dest') + 1]\n"
        "import pathlib\n"
        "pathlib.Path(dest, 'primary-review-audit.json').write_bytes(b'{}')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SILO_STORE", str(store))
    assert NEI._silo_audit_get("d14/1/ok/primary-review-audit.json", 30) == b"{}"
    assert NEI._silo_audit_get("d14/1/missing/primary-review-audit.json", 30) is None
    with pytest.raises(RuntimeError):
        NEI._silo_audit_get("d14/1/broken/primary-review-audit.json", 30)


# ── evaluate() wiring: evidence absent/None keeps current behaviour ────────


def test_evaluate_default_neighbor_evidence_is_none_not_error(caller_repo):
    sha_a, sha_b = caller_repo["sha_a"], caller_repo["sha_b"]
    issued_audit = _audit(sha_a, [_finding("t-id", 187)])
    current_audit = _audit(sha_b, CURRENT_FINDINGS, run_id=999)
    receipt = _receipt(sha_a, CONV.canonical_audit_digest(issued_audit))
    outcome = _evaluate(current_audit, sha_b, receipt, None)
    status = outcome.disposition_audit.statuses[0]
    assert status.reason_code == "head_sha_mismatch"
    assert outcome.disposition_audit.remaining_p1_ids == ("p2-id", "t-id")
