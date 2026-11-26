from __future__ import annotations

import ast
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGGREGATOR = ROOT / ".github" / "actions" / "gate-aggregator"
CARD3 = {"gate_io.py", "terminal_history.py", "previous_context.py"}
PURE = {"aggregate_models.py", "disposition_projection.py", "finding_relations.py", "decision.py"}
CANONICAL_FILES = {
    "gate_aggregate_models": "aggregate_models.py",
    "gate_disposition_projection": "disposition_projection.py",
    "gate_finding_relations": "finding_relations.py",
    "gate_aggregate_decision": "decision.py",
    "gate_aggregate_io": "gate_io.py",
    "gate_terminal_history": "terminal_history.py",
    "gate_previous_context": "previous_context.py",
    "gate_convergence_from_aggregate": "convergence.py",
    "gate_action_from_aggregate": "gate_action.py",
    "gate_carry_findings_from_aggregate": "carry_findings.py",
    "gate_neighbor_evidence_from_aggregate": "neighbor_evidence.py",
}


def _load_aggregate(path: Path = AGGREGATOR / "aggregate.py"):
    spec = importlib.util.spec_from_file_location("gate_io_split_contract_aggregate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _all_module_dependencies(filename: str) -> set[str]:
    tree = ast.parse((AGGREGATOR / filename).read_text(encoding="utf-8"))
    aliases = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    edges: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in CANONICAL_FILES:
            dependency = CANONICAL_FILES[node.module]
            edges.add(dependency)
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "spec_from_file_location" or len(node.args) < 2:
            continue
        module_name = node.args[0]
        module_name = aliases.get(module_name.id) if isinstance(module_name, ast.Name) else None
        path = node.args[1]
        if not isinstance(path, ast.Call) or not isinstance(path.func, ast.Attribute):
            continue
        if path.func.attr != "with_name" or not path.args or not isinstance(path.args[0], ast.Constant):
            continue
        dependency = path.args[0].value
        assert CANONICAL_FILES.get(module_name, dependency) == dependency
        edges.add(dependency)
    return edges


def _module_edges(filename: str) -> set[str]:
    return _all_module_dependencies(filename) & (CARD3 | PURE)


def test_call_time_ports_reach_io_history_previous_context_and_main(monkeypatch, tmp_path):
    aggregate = _load_aggregate()
    assert aggregate._GATE_IO._github_json.__module__ == "gate_aggregate_io"
    assert aggregate._TERMINAL_HISTORY._load_pr_convergence_history.__module__ == "gate_terminal_history"
    assert aggregate._PREVIOUS_CONTEXT.load_previous_round_findings.__module__ == "gate_previous_context"
    github_calls = []
    monkeypatch.setattr(
        aggregate,
        "_github_request",
        lambda **kwargs: (github_calls.append(kwargs) or b'{"login":"fixture"}'),
    )
    assert aggregate._github_json(token="fixture", url="https://api.github.com/user") == {"login": "fixture"}
    assert github_calls == [{"token": "fixture", "url": "https://api.github.com/user"}]

    silo_calls = []
    def silo_cli(argv, timeout_s):
        silo_calls.append((argv, timeout_s))
        target = Path(argv[argv.index("--dest") + 1])
        target.write_text(
            '{"pr_number":7,"run_id":1,"run_attempt":1,"head_sha":"abc",'
            '"review":{"result":{"findings":[]}}}\n',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(aggregate, "_silo_configured", lambda: True)
    monkeypatch.setattr(aggregate, "_silo_cli", silo_cli)
    loaded = aggregate.load_previous_round_findings(
        path=None, repository_id=12, pr_number=7, run_id=8, run_attempt=1,
    )
    assert loaded["available"] is True and loaded["head_sha"] == "abc"
    assert silo_calls
    argv, timeout_s = silo_calls[0]
    assert argv[:3] == [
        "get", "--key", "d30/12/codex-review-ledger-latest-v1-12-pr7/ledger.jsonl",
    ]
    assert argv[3] == "--dest" and Path(argv[4]).name == "ledger.jsonl"
    assert timeout_s == aggregate.SILO_CLI_CALL_TIMEOUT_SECONDS

    object_calls = []
    monkeypatch.setattr(
        aggregate,
        "_silo_objects_under",
        lambda prefix, timeout_s: (object_calls.append((prefix, timeout_s)) or []),
    )
    assert aggregate._fetch_silo_disposition_receipts(repository_id=12, pr_number=7) == ()
    assert object_calls and object_calls[0][0].startswith("d30/12/")

    # Exercise both split terminal-history and receipt ports through the real CLI.
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    audit = {
        "kind": "primary_review", "schema_version": 1,
        "repository_id": 12, "head_sha": "a" * 40, "run_id": 8,
        "run_attempt": 1, "pr": 7, "verdict": "pass", "reviewer": "fixture",
        "base_sha": "b" * 40, "diff_digest": "d" * 64,
        "policy_version": "policy-v1", "policy_digest": "p" * 64,
        "tier": "personal", "caller_sha": "c" * 40,
        "reusable_workflow_sha": "w" * 40, "result": {"findings": []},
    }
    (audit_dir / "audit.json").write_text(__import__("json").dumps(audit), encoding="utf-8")
    history_calls = []
    receipt_calls = []
    monkeypatch.setattr(
        aggregate,
        "_load_pr_convergence_history",
        lambda **kwargs: (history_calls.append(kwargs) or ("available", (), aggregate.HistoryLoad())),
    )
    monkeypatch.setattr(
        aggregate,
        "_fetch_disposition_receipts",
        lambda **kwargs: (receipt_calls.append(kwargs) or ()),
    )
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token")
    result = aggregate.main([
        "--quality-result", "success", "--primary-result", "success", "--runner", "self",
        "--is-draft", "false", "--is-fork", "false", "--classify-review-expected", "true",
        "--review-expected", "true", "--repository-id", "12", "--repository", "owner/repo",
        "--head-sha", "a" * 40, "--run-id", "8", "--run-attempt", "1", "--pr-number", "7",
        "--pr-author-json", '"fixture"', "--audit-dir", str(audit_dir),
    ])
    assert result in (0, 1)
    assert history_calls == [{"repository": "owner/repo", "repository_id": 12, "pr_number": 7}]
    assert receipt_calls == [{"token": "fixture-token", "repository": "owner/repo", "repository_id": 12, "pr_number": 7}]


def test_card3_modules_form_downward_acyclic_dag_and_pure_modules_do_not_import_them():
    names = PURE | CARD3
    all_dependencies = {name: _all_module_dependencies(name) for name in names}
    assert all_dependencies["gate_io.py"] == {"aggregate_models.py", "convergence.py"}
    assert all_dependencies["terminal_history.py"] == {
        "aggregate_models.py", "decision.py", "convergence.py", "gate_action.py", "gate_io.py",
    }
    assert all_dependencies["previous_context.py"] == {
        "aggregate_models.py", "finding_relations.py", "convergence.py", "gate_io.py",
        "terminal_history.py", "carry_findings.py", "neighbor_evidence.py",
    }
    edges = {name: _module_edges(name) for name in names}
    assert edges["aggregate_models.py"] == set()
    assert edges["disposition_projection.py"] == set()
    assert edges["finding_relations.py"] == {"aggregate_models.py"}
    assert edges["decision.py"] == {"aggregate_models.py", "disposition_projection.py"}
    assert edges["gate_io.py"] == {"aggregate_models.py"}
    assert edges["terminal_history.py"] == {
        "aggregate_models.py", "decision.py", "gate_io.py",
    }
    assert edges["previous_context.py"] == {
        "aggregate_models.py", "finding_relations.py", "gate_io.py", "terminal_history.py",
    }
    for pure in PURE:
        assert not edges[pure] & CARD3, f"card-2 pure module imports a card-3 module: {pure}"

    visited: set[str] = set()
    visiting: set[str] = set()
    def visit(filename: str) -> None:
        assert filename not in visiting, f"split-module cycle includes {filename}"
        if filename in visited:
            return
        visiting.add(filename)
        for dependency in edges[filename]:
            visit(dependency)
        visiting.remove(filename)
        visited.add(filename)
    for filename in names:
        visit(filename)


def test_literal_loaders_and_isolated_checkout_preserve_injected_ports(tmp_path):
    aggregate_source = (AGGREGATOR / "aggregate.py").read_text(encoding="utf-8")
    required = PURE | CARD3
    for filename in required:
        assert f'Path(__file__).with_name("{filename}")' in aggregate_source

    isolated = tmp_path
    isolated_aggregator = isolated / ".github" / "actions" / "gate-aggregator"
    isolated_aggregator.mkdir(parents=True)
    (isolated / "scripts").mkdir()
    for source in AGGREGATOR.glob("*.py"):
        shutil.copy2(source, isolated_aggregator / source.name)
    shutil.copy2(ROOT / "scripts" / "scrub_outbound.py", isolated / "scripts" / "scrub_outbound.py")
    check = r'''
import importlib.util, json, os, subprocess, sys
from pathlib import Path
base = Path.cwd() / ".github/actions/gate-aggregator"
spec = importlib.util.spec_from_file_location("isolated_gate_aggregate", base / "aggregate.py")
aggregate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = aggregate
spec.loader.exec_module(aggregate)

github_calls = []
aggregate._github_request = lambda **kwargs: (github_calls.append(kwargs) or b'{"login":"isolated"}')
assert aggregate._github_json(token="token", url="https://api.github.com/user") == {"login":"isolated"}
assert github_calls and github_calls[0]["url"].endswith("/user")

silo_calls = []
object_calls = []
def silo_cli(argv, timeout_s):
    silo_calls.append(argv)
    target = Path(argv[argv.index("--dest") + 1])
    target.write_text('{"pr_number":5,"run_id":1,"run_attempt":1,"head_sha":"abc","review":{"result":{"findings":[]}}}\n', encoding="utf-8")
    return subprocess.CompletedProcess(argv, 0, "", "")
aggregate._silo_configured = lambda: True
aggregate._silo_cli = silo_cli
aggregate._silo_objects_under = lambda prefix, timeout_s: (object_calls.append(prefix) or [])
assert aggregate.main(["--render-previous-context", "--repository-id", "12", "--pr-number", "5", "--run-id", "8", "--run-attempt", "1", "--previous-json", str(Path.cwd() / "previous.json")]) == 0
assert silo_calls and silo_calls[0][0] == "get"
assert object_calls and object_calls[0].startswith("d30/12/")

# The ordinary aggregate CLI invokes the patched history and receipt ports, without
# reaching either GitHub or Silo after these stubs are installed.
audit_dir = Path.cwd() / "audit"
audit_dir.mkdir()
audit = {"kind":"primary_review","schema_version":1,"repository_id":12,"head_sha":"a"*40,"run_id":8,"run_attempt":1,"pr":7,"verdict":"pass","reviewer":"fixture","base_sha":"b"*40,"diff_digest":"d"*64,"policy_version":"policy-v1","policy_digest":"p"*64,"tier":"personal","caller_sha":"c"*40,"reusable_workflow_sha":"w"*40,"result":{"findings":[]}}
(audit_dir / "audit.json").write_text(json.dumps(audit), encoding="utf-8")
os.environ["GITHUB_TOKEN"] = "fixture-token"
history_calls, receipt_calls = [], []
aggregate._load_pr_convergence_history = lambda **kwargs: (history_calls.append(kwargs) or ("available", (), aggregate.HistoryLoad()))
aggregate._fetch_disposition_receipts = lambda **kwargs: (receipt_calls.append(kwargs) or ())
aggregate.main(["--quality-result","success","--primary-result","success","--runner","self","--is-draft","false","--is-fork","false","--classify-review-expected","true","--review-expected","true","--repository-id","12","--repository","owner/repo","--head-sha","a"*40,"--run-id","8","--run-attempt","1","--pr-number","7","--pr-author-json",'"fixture"',"--audit-dir",str(audit_dir)])
assert history_calls == [{"repository":"owner/repo","repository_id":12,"pr_number":7}]
assert receipt_calls == [{"token":"fixture-token","repository":"owner/repo","repository_id":12,"pr_number":7}]
'''
    completed = subprocess.run(
        [sys.executable, "-c", check], cwd=isolated, text=True, capture_output=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
