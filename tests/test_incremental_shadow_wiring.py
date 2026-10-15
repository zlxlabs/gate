"""Wiring tests for the incremental blocking shadow (card G2).

Three layers, one file:

1. **io assembly** (``incremental_shadow_io.py``) driven through ``main()``
   with a REAL local git remote (fetch-by-SHA + ``git diff -U0 -M`` run for
   real), a stub ``silo_store.py`` implementing the same CLI contract from a
   temp directory, and a stubbed GitHub API.  All repo names are
   ``example/synthetic`` — no real data in this file (fixture rule, card G2).
2. **producer→consumer boundary**: the payload bytes the io script actually
   wrote are fed to ``build_ledger.py`` unchanged; §5.1 shape/domain asserts
   run against those bytes, and a harmlessness test proves the ledger row is
   byte-identical to a no-shadow row whenever the shadow fails (missing,
   unparseable, invalid payload) and identical-modulo-the-field when the
   shadow itself degraded (silo down, budget exhausted, diff unavailable).
3. **workflow/action contract** (``gate-v2.yml`` ledger job + review-ledger
   action): step placement, fail-open guards, and the ``shadow-path`` input
   chain.  Design: gate-hub
   ``docs/designs/convergence-incremental-blocking-shadow.md`` §2/§5/§8.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IO_MODULE_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "incremental_shadow_io.py"
LEDGER_MODULE_PATH = ROOT / ".github" / "actions" / "review-ledger" / "build_ledger.py"
ACTION_PATH = ROOT / ".github" / "actions" / "review-ledger" / "action.yml"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "gate-v2.yml"

REPO = "example/synthetic"
REPO_ID = 424242
PR = 7
RUN_ID = 200
ATTEMPT = 1
BRANCH = "feat/x"
MISSING_SHA = "b" * 40  # valid-format SHA that never exists on the remote

CLASSES = ("no_anchor", "duplicate", "diff_unavailable", "on_diff", "neither")
COUNTERFACTUALS = {"block", "pass", "unclassified", "not_applicable"}


def _load_module(path: Path) -> object:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def io_mod():
    return _load_module(IO_MODULE_PATH)


@pytest.fixture
def ledger_mod():
    return _load_module(LEDGER_MODULE_PATH)


# ─────────────────────────── fixture builders ───────────────────────────


def _finding(fid, file, line, severity="major", category="correctness", issue="bug"):
    return {"id": fid, "severity": severity, "file": file, "line": line, "category": category, "issue": issue}


def _row(run_id, head, recorded_at, findings, *, attempt=1, pr=PR, status="fail"):
    return {
        "schema_version": 2,
        "recorded_at": recorded_at,
        "repository": REPO,
        "pr_number": pr,
        "run_id": run_id,
        "run_attempt": attempt,
        "head_sha": head,
        "review": {"status": status, "result": {"findings": findings}},
    }


def _seed_archive(root: Path, run_id: int, attempt: int, head: str, row: dict) -> None:
    name = f"codex-review-ledger-v2-{REPO_ID}-{head}-{run_id}-{attempt}"
    target = root / name
    target.mkdir(parents=True)
    (target / "ledger.jsonl").write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")


SILO_STUB = """\
import os, shutil, sys
from pathlib import Path

root = Path(os.environ["SHADOW_TEST_SILO_ROOT"])
args = sys.argv[1:]
if args[0] == "list":
    prefix = args[args.index("--name-prefix") + 1]
    repo_id = args[args.index("--repo-id") + 1]
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name.startswith(prefix):
            print(f"d30/{repo_id}/{child.name}/ledger.jsonl")
elif args[0] == "get":
    key = args[args.index("--key") + 1]
    dest = Path(args[args.index("--dest") + 1])
    name, obj = key.split("/")[2], key.split("/")[3]
    src = root / name / obj
    if not src.is_file():
        print(f"silo stub: object not found: {key}", file=sys.stderr)
        raise SystemExit(1)
    shutil.copyfile(src, dest / obj)
else:
    print(f"silo stub: unsupported subcommand {args[0]}", file=sys.stderr)
    raise SystemExit(3)
"""


@pytest.fixture
def silo_stub(monkeypatch, tmp_path):
    store = tmp_path / "silo_store_stub.py"
    store.write_text(SILO_STUB, encoding="utf-8")
    root = tmp_path / "silo-root"
    root.mkdir()
    monkeypatch.setenv("SILO_STORE", str(store))
    monkeypatch.setenv("SHADOW_TEST_SILO_ROOT", str(root))
    return root


GIT_ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
    GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
)


def _make_git_remote(work: Path) -> tuple[Path, dict[str, str]]:
    """Bare origin with three commits on main; heads are REAL SHAs so the
    io module's fetch-by-SHA + ``git diff -U0 -M`` run for real.  The last
    commit adds ``new_mod.py`` line 1 — the on_diff anchor for round 3."""
    origin = work.parent / "origin.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(origin)], check=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", str(work)], check=True, env=GIT_ENV)
    for message, files in (
        ("c1", {"old_mod.py": "x = 1\n" * 60, "other_mod.py": "y = 1\n" * 30}),
        ("c2", {"new_mod.py": ""}),
        ("c3", {"new_mod.py": "value = 1\n"}),
    ):
        for name, content in files.items():
            (work / name).write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=work, check=True, env=GIT_ENV)
        subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=work, check=True, env=GIT_ENV)
    subprocess.run(["git", "push", "--quiet", str(origin), "main"], cwd=work, check=True, env=GIT_ENV)
    heads = subprocess.run(
        ["git", "log", "--format=%H", "main"], cwd=work, capture_output=True, text=True, check=True,
    ).stdout.split()
    # log is newest-first: c3, c2, c1
    return origin, {"1": heads[2], "2": heads[1], "3": heads[0]}


class _GhFake:
    """Stubbed api.github.com; records request paths, serves canned payloads."""

    def __init__(self, monkeypatch, io_mod, *, head_ref=BRANCH, run_ids=(100, 150), total_count=None):
        self.paths: list[str] = []
        self.run_ids = list(run_ids)
        self.total_count = total_count
        self.head_ref = head_ref
        monkeypatch.setattr(io_mod, "_gh_api_json", self)

    def __call__(self, path: str, token: str):
        self.paths.append(path)
        assert token == "test-token"
        if "/pulls/" in path:
            return {"head": {"ref": self.head_ref}}
        payload = {"workflow_runs": [{"id": run_id} for run_id in self.run_ids]}
        if self.total_count is not None:
            payload["total_count"] = self.total_count
        return payload


TERMINAL_BLOCKING = {
    "schema_version": 1, "kind": "gate_terminal", "gate_result": "fail", "reason_code": "primary_findings",
}
AUDIT_ON_NEW_FILE = {
    "status": "fail", "runtime": {"duration_s": 30},
    "result": {"verdict": "fail", "summary": "s", "findings": [_finding("F1", "new_mod.py", 1)]},
}


class _Env:
    """Everything one io main() invocation needs."""

    def __init__(self, monkeypatch, io_mod, silo_stub, tmp_path):
        self.monkeypatch = monkeypatch
        self.io_mod = io_mod
        self.silo = silo_stub
        self.tmp = tmp_path
        self.audit_path = tmp_path / "primary-review-audit.json"
        self.terminal_path = tmp_path / "gate-terminal.json"
        self.out_path = tmp_path / "shadow" / "shadow.json"
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

    def gh(self, **kwargs):
        return _GhFake(self.monkeypatch, self.io_mod, **kwargs)

    def audit(self, audit):
        self.audit_path.write_text(json.dumps(audit), encoding="utf-8")

    def terminal(self, terminal=TERMINAL_BLOCKING):
        self.terminal_path.write_text(json.dumps(terminal), encoding="utf-8")

    def argv(self, *, head="c" * 40, review_expected="true", pr=PR, budget=None):
        if budget is not None:
            self.monkeypatch.setenv("GATE_INCREMENTAL_SHADOW_BUDGET_S", str(budget))
        self.monkeypatch.setenv("GH_TOKEN", "test-token")
        self.monkeypatch.setattr(sys, "argv", [
            "incremental_shadow_io.py",
            "--audit-path", str(self.audit_path),
            "--terminal-path", str(self.terminal_path),
            "--shadow-out", str(self.out_path),
            "--repository", REPO,
            "--repository-id", str(REPO_ID),
            "--pr-number", str(pr),
            "--run-id", str(RUN_ID),
            "--head-sha", head,
            "--review-expected", review_expected,
        ])

    def run(self, capsys) -> dict:
        assert self.io_mod.main() == 0
        return self.payload()

    def payload(self) -> dict:
        lines = self.out_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        return json.loads(lines[0])

    def seed_rounds(self, rounds):
        for run_id, head, findings in rounds:
            _seed_archive(
                self.silo, run_id, ATTEMPT, head,
                _row(run_id, head, f"2026-10-0{run_id % 10}T00:00:00+00:00", findings),
            )


ROUND3_ROUNDS = "rounds with real heads set up per test"


# ─────────────────────────── io: skips & degrades ───────────────────────────


def test_review_not_expected_skips_without_any_io(monkeypatch, io_mod, tmp_path, silo_stub, capsys):
    env = _Env(monkeypatch, io_mod, silo_stub, tmp_path)
    env.audit({"status": "fail"})
    gh = env.gh()
    env.argv(head="", review_expected="false")
    payload = env.run(capsys)
    assert payload["status"] == "skipped" and payload["skip_reason"] == "review_not_expected"
    assert payload["counterfactual"] == "not_applicable" and payload["findings"] == []
    assert gh.paths == []  # zero network / silo traffic on this path
    assert "incremental-blocking-shadow: skipped" in capsys.readouterr().out


def test_audit_absent_skips(monkeypatch, io_mod, tmp_path, silo_stub, capsys):
    env = _Env(monkeypatch, io_mod, silo_stub, tmp_path)
    env.argv()  # audit file never written
    payload = env.run(capsys)
    assert payload["status"] == "skipped" and payload["skip_reason"] == "audit_absent"


def test_history_over_50_runs_skips_not_half_counts(monkeypatch, io_mod, tmp_path, silo_stub, capsys):
    env = _Env(monkeypatch, io_mod, silo_stub, tmp_path)
    env.audit(AUDIT_ON_NEW_FILE)
    gh = env.gh(run_ids=range(1, 52))
    env.argv()
    payload = env.run(capsys)
    assert payload["status"] == "skipped" and payload["skip_reason"] == "history_truncated"
    assert payload["counterfactual"] == "unclassified"
    assert len([p for p in gh.paths if "actions/runs" in p]) == 1


@pytest.mark.parametrize("total_count", [None, 101])
def test_runs_page_truncation_skips(monkeypatch, io_mod, tmp_path, silo_stub, capsys, total_count):
    env = _Env(monkeypatch, io_mod, silo_stub, tmp_path)
    env.audit(AUDIT_ON_NEW_FILE)
    env.gh(run_ids=range(1, 101), total_count=total_count)
    env.argv()
    assert env.run(capsys)["skip_reason"] == "history_truncated"


def test_runs_api_failure_degrades(monkeypatch, io_mod, tmp_path, silo_stub, capsys):
    env = _Env(monkeypatch, io_mod, silo_stub, tmp_path)
    env.audit(AUDIT_ON_NEW_FILE)

    def broken(path, token):
        raise RuntimeError("api down")

    monkeypatch.setattr(io_mod, "_gh_api_json", broken)
    env.argv()
    payload = env.run(capsys)
    assert payload["status"] == "degraded" and payload["degrade_reason"] == "runs_list_failed"
    assert payload["counterfactual"] == "unclassified"
    out = capsys.readouterr().out
    assert out.count("GATE-INCREMENTAL-SHADOW-UNAVAILABLE: reason=runs_list_failed") == 1


def test_silo_list_failure_degrades(monkeypatch, io_mod, tmp_path, silo_stub, capsys):
    env = _Env(monkeypatch, io_mod, silo_stub, tmp_path)
    env.audit(AUDIT_ON_NEW_FILE)
    env.gh()
    monkeypatch.setenv("SHADOW_TEST_SILO_ROOT", str(tmp_path / "absent-silo"))
    env.argv()
    payload = env.run(capsys)
    assert payload["status"] == "degraded" and payload["degrade_reason"] == "silo_list_failed"
    assert capsys.readouterr().out.count("GATE-INCREMENTAL-SHADOW-UNAVAILABLE") == 1


def test_budget_exhaustion_degrades_before_any_io(monkeypatch, io_mod, tmp_path, silo_stub, capsys):
    env = _Env(monkeypatch, io_mod, silo_stub, tmp_path)
    env.audit(AUDIT_ON_NEW_FILE)
    gh = env.gh()
    env.argv(budget=0)
    payload = env.run(capsys)
    assert payload["status"] == "degraded" and payload["degrade_reason"] == "budget_exhausted"
    assert payload["counterfactual"] == "unclassified"
    assert gh.paths == []  # budget checked before the first network call
    assert capsys.readouterr().out.count("GATE-INCREMENTAL-SHADOW-UNAVAILABLE") == 1


def test_internal_error_fails_open_with_one_line(monkeypatch, io_mod, tmp_path, silo_stub, capsys):
    env = _Env(monkeypatch, io_mod, silo_stub, tmp_path)
    env.audit(AUDIT_ON_NEW_FILE)
    env.gh()

    def boom(**kwargs):
        raise AssertionError("injected bug")

    monkeypatch.setattr(io_mod, "build_shadow", boom)
    env.argv()
    assert io_mod.main() == 0
    payload = env.payload()
    assert payload["status"] == "degraded" and payload["degrade_reason"] == "internal_error"
    assert capsys.readouterr().out.count("GATE-INCREMENTAL-SHADOW-UNAVAILABLE: reason=internal_error") == 1


def test_invalid_budget_string_fails_fast(monkeypatch, io_mod, tmp_path, silo_stub, capsys):
    env = _Env(monkeypatch, io_mod, silo_stub, tmp_path)
    env.audit(AUDIT_ON_NEW_FILE)
    env.gh()
    env.argv(budget="soon")
    assert io_mod.main() == 0
    assert env.payload()["degrade_reason"] == "internal_error"


# ─────────────────────── io: computed paths (real git remote) ───────────────────────


def _round3_env(monkeypatch, io_mod, silo_stub, tmp_path, *, audit=AUDIT_ON_NEW_FILE, terminal=TERMINAL_BLOCKING):
    """Two prior rounds (real heads 1/2) + current head 3; 3 adds new_mod.py:1."""
    heads = _make_git_remote(tmp_path / "work")[1]
    env = _Env(monkeypatch, io_mod, silo_stub, tmp_path)
    env.heads = heads
    monkeypatch.setattr(
        io_mod, "_remote_url", lambda repository: str(tmp_path / "origin.git")
    )  # offline replay seam: fetch the local fixture remote instead of github.com
    env.audit(audit)
    env.terminal(terminal)
    env.gh()
    return env


def test_round3_on_diff_finding_blocks(monkeypatch, io_mod, tmp_path, silo_stub, capsys):
    env = _round3_env(monkeypatch, io_mod, silo_stub, tmp_path)
    env.seed_rounds([
        (100, env.heads["1"], [_finding("F0", "old_mod.py", 50)]),
        (150, env.heads["2"], [_finding("F0", "old_mod.py", 51)]),
    ])
    env.argv(head=env.heads["3"])
    payload = env.run(capsys)
    assert payload["status"] == "computed" and payload["skip_reason"] == "" and payload["degrade_reason"] == ""
    assert payload["canonical_round"] == 3 and payload["prior_rounds_used"] == 2
    assert payload["prev_head_sha"] == env.heads["2"] and payload["head_sha"] == env.heads["3"]
    assert payload["diff_status"] == "ok"
    assert payload["class_counts"] == {"no_anchor": 0, "duplicate": 0, "diff_unavailable": 0, "on_diff": 1, "neither": 0}
    assert payload["findings"][0]["class"] == "on_diff" and payload["findings"][0]["file"] == "new_mod.py"
    assert payload["counterfactual"] == "block"
    assert "incremental-blocking-shadow: computed canonical_round=3" in capsys.readouterr().out


def test_runs_api_request_paths_are_pulled_then_branch_runs(monkeypatch, io_mod, tmp_path, silo_stub, capsys):
    env = _round3_env(monkeypatch, io_mod, silo_stub, tmp_path)
    captured = []

    real_gh = io_mod._gh_api_json

    def recording(path, token):
        captured.append(path)
        return real_gh(path, token)

    monkeypatch.setattr(io_mod, "_gh_api_json", recording)
    env.seed_rounds([
        (100, env.heads["1"], []),
        (150, env.heads["2"], []),
    ])
    env.argv(head=env.heads["3"])
    env.run(capsys)
    assert captured[0] == f"/repos/{REPO}/pulls/{PR}"
    assert f"event=pull_request&branch={urllib.parse.quote(BRANCH, safe='')}&per_page=100" in captured[1]


def test_duplicate_rounds_beat_diff_classes(monkeypatch, io_mod, tmp_path, silo_stub, capsys):
    env = _round3_env(
        monkeypatch, io_mod, silo_stub, tmp_path,
        audit={"status": "fail", "runtime": {"duration_s": 1},
               "result": {"verdict": "fail", "summary": "s", "findings": [_finding("F1", "old_mod.py", 53)]}},
    )
    env.seed_rounds([
        (100, env.heads["1"], [_finding("F0", "old_mod.py", 50)]),
        (150, env.heads["2"], [_finding("F0", "other_mod.py", 20)]),
    ])
    env.argv(head=env.heads["3"])
    payload = env.run(capsys)
    assert payload["findings"][0]["class"] == "duplicate"
    assert payload["findings"][0]["match_round"] == 1 and payload["findings"][0]["match_line"] == 50
    assert payload["counterfactual"] == "block"


def test_terminal_not_blocking_makes_counterfactual_not_applicable(monkeypatch, io_mod, tmp_path, silo_stub, capsys):
    env = _round3_env(
        monkeypatch, io_mod, silo_stub, tmp_path,
        terminal={"schema_version": 1, "kind": "gate_terminal", "gate_result": "pass", "reason_code": "quality_ok"},
    )
    env.seed_rounds([(100, env.heads["1"], [])])
    env.argv(head=env.heads["2"])
    assert env.run(capsys)["counterfactual"] == "not_applicable"


def test_force_pushed_prev_head_degrades_diff(monkeypatch, io_mod, tmp_path, silo_stub, capsys):
    env = _round3_env(monkeypatch, io_mod, silo_stub, tmp_path)
    env.seed_rounds([
        (100, env.heads["1"], [_finding("F0", "old_mod.py", 50)]),
        (150, MISSING_SHA, [_finding("F0", "old_mod.py", 51)]),  # force-push residue: SHA not on remote
    ])
    env.argv(head=env.heads["3"])
    payload = env.run(capsys)
    assert payload["status"] == "degraded" and payload["degrade_reason"] == "diff_failed"
    assert payload["diff_status"] == "unavailable"
    assert payload["findings"][0]["class"] == "diff_unavailable"
    assert payload["counterfactual"] == "unclassified"  # §4.2: findings may hide on the unavailable diff


def test_archive_get_failure_is_partial_but_still_classifies(monkeypatch, io_mod, tmp_path, silo_stub, capsys):
    env = _round3_env(monkeypatch, io_mod, silo_stub, tmp_path)
    env.seed_rounds([
        (100, env.heads["1"], [_finding("F0", "old_mod.py", 50)]),
        (150, env.heads["2"], [_finding("F0", "old_mod.py", 51)]),
    ])
    for child in list(env.silo.iterdir()):
        if "-150-1" in child.name:
            (child / "ledger.jsonl").unlink()  # list still returns it, get now fails (rc=1)
    env.argv(head=env.heads["3"])
    payload = env.run(capsys)
    assert payload["status"] == "degraded" and payload["degrade_reason"] == "silo_archives_partial"
    assert payload["prior_rounds_used"] == 1 and payload["canonical_round"] == 2
    assert payload["prev_head_sha"] == env.heads["1"]
    assert payload["findings"][0]["class"] == "on_diff"


def test_same_head_rerun_skips_no_prior_distinct_head(monkeypatch, io_mod, tmp_path, silo_stub, capsys):
    env = _round3_env(
        monkeypatch, io_mod, silo_stub, tmp_path,
        audit={"status": "fail", "runtime": {"duration_s": 1},
               "result": {"verdict": "fail", "summary": "s", "findings": [_finding("F1", "old_mod.py", 52)]}},
    )
    env.seed_rounds([(100, env.heads["2"], [_finding("F0", "old_mod.py", 50)])])
    env.argv(head=env.heads["2"])  # rerun of round 2's exact head
    payload = env.run(capsys)
    assert payload["status"] == "skipped" and payload["skip_reason"] == "no_prior_distinct_head"
    assert payload["canonical_round"] == 1
    assert payload["prev_head_sha"] == env.heads["2"]
    assert payload["prior_rounds_used"] == 1
    assert payload["counterfactual"] == "not_applicable"


def test_first_run_of_pr_skips_no_prior_distinct_head(monkeypatch, io_mod, tmp_path, silo_stub, capsys):
    env = _round3_env(monkeypatch, io_mod, silo_stub, tmp_path)
    env.gh(run_ids=[RUN_ID])  # only the current run exists
    env.argv(head=env.heads["3"])
    payload = env.run(capsys)
    assert payload["status"] == "skipped" and payload["skip_reason"] == "no_prior_distinct_head"
    assert payload["canonical_round"] == 0 and payload["prior_rounds_used"] == 0


def test_current_run_and_foreign_pr_archives_are_ignored(monkeypatch, io_mod, tmp_path, silo_stub, capsys):
    env = _round3_env(monkeypatch, io_mod, silo_stub, tmp_path)
    env.seed_rounds([
        (100, env.heads["1"], []),
        (150, env.heads["2"], []),
    ])
    _seed_archive(env.silo, RUN_ID, ATTEMPT, env.heads["3"],
                  _row(RUN_ID, env.heads["3"], "2026-10-03T00:00:00+00:00", []))  # current run archive
    _seed_archive(env.silo, 120, ATTEMPT, "d" * 40,
                  _row(120, "d" * 40, "2026-10-01T12:00:00+00:00", [], pr=999))  # same branch, other PR
    env.gh(run_ids=[100, 120, 150, RUN_ID])
    env.argv(head=env.heads["3"])
    payload = env.run(capsys)
    assert payload["prior_rounds_used"] == 2  # run 120 (other PR) and run 200 (current) never enter the fold


# ─────────────────────── producer bytes → build_ledger ───────────────────────

FIXED_NOW = datetime.datetime(2026, 10, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture
def frozen_now(monkeypatch, ledger_mod):
    class _Frozen(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return FIXED_NOW

    monkeypatch.setattr(ledger_mod, "datetime", _Frozen)
    return FIXED_NOW


AUDIT_FOR_LEDGER = {
    "status": "fail", "runtime": {"duration_s": 30},
    "result": {"verdict": "fail", "summary": "s", "findings": [_finding("F1", "new_mod.py", 1)]},
}


def _run_build_ledger(ledger_mod, tmp_path, monkeypatch, *, shadow_path=None):
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(AUDIT_FOR_LEDGER), encoding="utf-8")
    (tmp_path / "preflight.json").write_text(json.dumps({"reviewable": True}), encoding="utf-8")
    (tmp_path / "install.json").write_text("", encoding="utf-8")
    (tmp_path / "terminal.json").write_text(json.dumps({
        "schema_version": 1, "kind": "gate_terminal",
        "repository": REPO, "pr_number": 7, "run_id": 10, "run_attempt": 1,
        "head_sha": "a" * 40, "gate_result": "fail", "reason_code": "primary_findings",
        "disposition_receipt_consumption": {
            "recorded": [], "resolved": [], "consumed_count": 0,
            "rejected_count": 0, "rejected_reasons": {}, "fail_closed": False,
        },
    }), encoding="utf-8")
    output = tmp_path / "ledger.jsonl"
    argv = [
        "build_ledger.py",
        "--audit-path", str(audit_path),
        "--preflight-path", str(tmp_path / "preflight.json"),
        "--install-path", str(tmp_path / "install.json"),
        "--terminal-path", str(tmp_path / "terminal.json"),
        "--output", str(output),
        "--repository", REPO, "--pr-number", "7",
        "--run-id", "10", "--run-attempt", "1",
        "--head-sha", "a" * 40,
        "--expected-repository-id", str(REPO_ID),
        "--expected-base-sha", "0" * 40,
        "--expected-caller-sha", "0" * 40,
        "--expected-reusable-workflow-sha", "0" * 40,
        "--codex-expected", "true", "--codex-waived", "false",
    ]
    if shadow_path is not None:
        argv += ["--shadow-path", str(shadow_path)]
    monkeypatch.setattr(sys, "argv", argv)
    rc = ledger_mod.main()
    return rc, output


def assert_s51_shape(payload: dict) -> None:
    assert set(payload) == {
        "schema_version", "status", "skip_reason", "degrade_reason", "canonical_round",
        "prev_head_sha", "head_sha", "prior_rounds_used", "diff_status", "findings",
        "truncated", "class_counts", "counterfactual",
    }
    assert payload["schema_version"] == 1
    assert payload["status"] in {"computed", "skipped", "degraded"}
    assert payload["diff_status"] in {"ok", "unavailable"}
    assert sorted(payload["class_counts"]) == sorted(CLASSES)
    assert payload["counterfactual"] in COUNTERFACTUALS
    if payload["status"] == "skipped":
        assert payload["skip_reason"] and payload["degrade_reason"] == ""
    if payload["status"] == "degraded":
        assert payload["degrade_reason"] and payload["skip_reason"] == ""
    if payload["status"] == "computed":
        assert payload["skip_reason"] == "" and payload["degrade_reason"] == ""


def test_producer_bytes_flow_through_consumer(monkeypatch, io_mod, ledger_mod, tmp_path, silo_stub, capsys, frozen_now):
    env = _round3_env(monkeypatch, io_mod, silo_stub, tmp_path)
    env.seed_rounds([
        (100, env.heads["1"], [_finding("F0", "old_mod.py", 50)]),
        (150, env.heads["2"], [_finding("F0", "old_mod.py", 51)]),
    ])
    env.argv(head=env.heads["3"])
    assert io_mod.main() == 0
    capsys.readouterr()
    payload = env.payload()  # the exact bytes the io script wrote
    assert_s51_shape(payload)
    assert payload["status"] == "computed"

    rc, output = _run_build_ledger(ledger_mod, tmp_path, monkeypatch, shadow_path=env.out_path)
    assert rc == 0
    row = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert row["incremental_blocking_shadow"] == payload  # consumer keeps producer bytes verbatim
    assert ledger_mod.LEDGER_ENTRY_OPTIONAL_FIELDS[-1] == "incremental_blocking_shadow"


@pytest.mark.parametrize("corrupt", ["missing", "garbage", "wrong_json_type"])
def test_failed_shadow_payload_keeps_row_byte_identical(
    monkeypatch, ledger_mod, tmp_path, capsys, frozen_now, corrupt,
):
    _rc, baseline_output = _run_build_ledger(ledger_mod, tmp_path, monkeypatch)
    baseline = baseline_output.read_text(encoding="utf-8")
    assert baseline.count("\n") == 1
    capsys.readouterr()

    shadow_path = tmp_path / "shadow.json"
    if corrupt == "garbage":
        shadow_path.write_text("{not json", encoding="utf-8")
    elif corrupt == "wrong_json_type":
        shadow_path.write_text("[1, 2, 3]", encoding="utf-8")
    rc, output = _run_build_ledger(ledger_mod, tmp_path, monkeypatch, shadow_path=shadow_path)
    assert rc == 0
    assert output.read_text(encoding="utf-8") == baseline  # byte-identical incl. frozen recorded_at
    combined = capsys.readouterr()
    assert combined.out.count("GATE-INCREMENTAL-SHADOW-UNAVAILABLE: reason=") == 1
    assert "incremental_blocking_shadow" not in json.loads(baseline)


def test_degraded_payload_keeps_rest_of_row_byte_identical(
    monkeypatch, io_mod, ledger_mod, tmp_path, silo_stub, capsys, frozen_now,
):
    _rc, baseline_output = _run_build_ledger(ledger_mod, tmp_path, monkeypatch)
    row_no_shadow = json.loads(baseline_output.read_text(encoding="utf-8"))
    capsys.readouterr()

    # producer: silo unreachable → degraded payload bytes on disk
    env = _Env(monkeypatch, io_mod, silo_stub, tmp_path / "io")
    monkeypatch.setenv("SHADOW_TEST_SILO_ROOT", str(tmp_path / "absent-silo"))
    env.gh()  # history enumeration must succeed so the failure lands on the silo list
    env.audit(AUDIT_ON_NEW_FILE)
    env.terminal()
    env.argv(head="c" * 40)
    assert io_mod.main() == 0
    payload = env.payload()
    assert payload["status"] == "degraded" and payload["degrade_reason"] == "silo_list_failed"

    capsys.readouterr()
    rc, output = _run_build_ledger(ledger_mod, tmp_path, monkeypatch, shadow_path=env.out_path)
    assert rc == 0
    row = json.loads(output.read_text(encoding="utf-8"))
    assert row["incremental_blocking_shadow"] == payload
    row.pop("incremental_blocking_shadow")
    assert row == row_no_shadow  # every other field identical


def _invalid_payload_variants():
    good = {
        "schema_version": 1, "status": "computed", "skip_reason": "", "degrade_reason": "",
        "canonical_round": 3, "prev_head_sha": "b" * 40, "head_sha": "c" * 40,
        "prior_rounds_used": 2, "diff_status": "ok",
        "findings": [{"id": "F1", "severity": "major", "file": "x.py", "line": 1,
                      "class": "on_diff", "match_round": None, "match_line": None,
                      "text_digest": "0123456789abcdef"}],
        "truncated": False,
        "class_counts": {"no_anchor": 0, "duplicate": 0, "diff_unavailable": 0, "on_diff": 1, "neither": 0},
        "counterfactual": "block",
    }

    def mutate(name, **changes):
        payload = json.loads(json.dumps(good))
        for key, value in changes.items():
            if value is ...:
                payload.pop(key)
            else:
                payload[key] = value
        return (name, payload)

    finding = good["findings"][0]
    return [
        mutate("schema_version", schema_version=2),
        mutate("unknown_status", status="pending"),
        mutate("skipped_without_reason", status="skipped"),
        mutate("degraded_without_reason", status="degraded"),
        mutate("computed_with_degrade", degrade_reason="diff_failed"),
        mutate("computed_round_too_low", canonical_round=1),
        mutate("computed_prev_head_empty", prev_head_sha=""),
        mutate("bad_head_sha", head_sha="nothex"),
        mutate("bad_diff_status", diff_status="maybe"),
        mutate("computed_diff_unavailable", diff_status="unavailable"),
        mutate("finding_extra_field", findings=[dict(finding, extra=1)]),
        mutate("finding_bad_class", findings=[dict(finding, **{"class": "dunno"})]),
        mutate("duplicate_without_match", findings=[dict(finding, **{"class": "duplicate"})]),
        mutate("finding_bad_digest", findings=[dict(finding, text_digest="zz")]),
        mutate("finding_bad_severity", findings=[dict(finding, severity="minor")]),
        mutate("counts_mismatch", class_counts={**good["class_counts"], "on_diff": 2}),
        mutate("counts_extra_key", class_counts={**good["class_counts"], "other": 0}),
        mutate("counts_negative", class_counts={**good["class_counts"], "on_diff": -1}),
        mutate("truncated_not_bool", truncated="yes"),
        mutate("bad_counterfactual", counterfactual="maybe"),
        ("not_an_object", [1, 2, 3]),
    ]


@pytest.mark.parametrize(
    "payload", [payload for _, payload in _invalid_payload_variants()],
    ids=[name for name, _ in _invalid_payload_variants()],
)
def test_validator_rejects_invalid_payloads(
    monkeypatch, ledger_mod, tmp_path, capsys, frozen_now, payload,
):
    _rc, _ = _run_build_ledger(ledger_mod, tmp_path, monkeypatch)
    capsys.readouterr()
    shadow_path = tmp_path / "shadow.json"
    shadow_path.write_text(json.dumps(payload), encoding="utf-8")
    rc, output = _run_build_ledger(ledger_mod, tmp_path, monkeypatch, shadow_path=shadow_path)
    assert rc == 0
    combined = capsys.readouterr()
    assert combined.out.count("GATE-INCREMENTAL-SHADOW-UNAVAILABLE: reason=") == 1
    assert "incremental_blocking_shadow" not in json.loads(output.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "skip_reason", ["review_not_expected", "no_prior_distinct_head", "history_truncated", "audit_absent"],
)
def test_skipped_payloads_validate_and_merge(
    monkeypatch, ledger_mod, tmp_path, capsys, frozen_now, skip_reason,
):
    _rc, baseline_output = _run_build_ledger(ledger_mod, tmp_path, monkeypatch)
    row_no_shadow = json.loads(baseline_output.read_text(encoding="utf-8"))
    capsys.readouterr()
    payload = {
        "schema_version": 1, "status": "skipped", "skip_reason": skip_reason, "degrade_reason": "",
        "canonical_round": 0, "prev_head_sha": "", "head_sha": "c" * 40,
        "prior_rounds_used": 0, "diff_status": "unavailable", "findings": [],
        "truncated": False, "class_counts": {name: 0 for name in CLASSES},
        "counterfactual": "not_applicable",
    }
    shadow_path = tmp_path / "shadow.json"
    shadow_path.write_text(json.dumps(payload), encoding="utf-8")
    rc, output = _run_build_ledger(ledger_mod, tmp_path, monkeypatch, shadow_path=shadow_path)
    assert rc == 0
    row = json.loads(output.read_text(encoding="utf-8"))
    assert row["incremental_blocking_shadow"] == payload
    row.pop("incremental_blocking_shadow")
    assert row == row_no_shadow


# ─────────────────────── workflow / action contract ───────────────────────


def _yaml(path=WORKFLOW_PATH):
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_ledger_job_computes_shadow_before_build_and_fails_open():
    raw = _yaml()
    steps = raw["jobs"]["ledger"]["steps"]
    names = [step.get("name") for step in steps]
    assert "Compute incremental blocking shadow" in names
    step = steps[names.index("Compute incremental blocking shadow")]
    terminal_index = names.index("Download gate terminal envelope for ledger")
    build_index = names.index("Build v2 review effectiveness ledger")
    assert terminal_index < names.index("Compute incremental blocking shadow") < build_index
    # fail-open: the shadow step must never be the reason the ledger row (or
    # any other ledger step) disappears.
    assert step["continue-on-error"] is True
    assert step["timeout-minutes"] == 5
    env = step["env"]
    assert env["GATE_SHADOW_IO"] == (
        "${{ github.workspace }}/_gate-aggregator-src/.github/actions/gate-aggregator/incremental_shadow_io.py"
    )
    assert env["GATE_SHADOW_REVIEW_EXPECTED"] == "${{ steps.resolve-ledger-artifacts.outputs.review_expected }}"
    assert env["GH_TOKEN"] == "${{ github.token }}"
    run = step["run"]
    assert "${{" not in run  # workflow expressions reach the script only via env
    assert '|| echo "GATE-INCREMENTAL-SHADOW-UNAVAILABLE: reason=invocation_failed"' in run
    assert '--shadow-out "$GATE_SHADOW_OUT"' in run
    assert "--review-expected \"$GATE_SHADOW_REVIEW_EXPECTED\"" in run


def test_build_step_passes_shadow_path_and_resolver_outputs_review_expected():
    raw = _yaml()
    steps = raw["jobs"]["ledger"]["steps"]
    names = [step.get("name") for step in steps]
    build = steps[names.index("Build v2 review effectiveness ledger")]
    assert build["with"]["shadow-path"] == "${{ runner.temp }}/incremental-shadow/shadow.json"
    resolver_run = steps[names.index("Resolve v2 ledger artifacts")]["run"]
    assert 'output.write(f"review_expected={expected_text}' + '\\n")' in resolver_run


def test_action_declares_optional_shadow_path():
    action = _yaml(ACTION_PATH)
    shadow = action["inputs"]["shadow-path"]
    assert shadow["required"] is False and shadow["default"] == ""
    run_block = action["runs"]["steps"][0]["run"]
    assert '--shadow-path "${{ inputs.shadow-path }}"' in run_block
