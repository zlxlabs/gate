"""Card 4 contracts for rendering/panel-publishing module boundaries."""

from __future__ import annotations

import ast
import base64
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGGREGATOR = ROOT / ".github/actions/gate-aggregator"
SPLIT_MODULES = {
    "aggregate_models.py",
    "disposition_projection.py",
    "finding_relations.py",
    "decision.py",
    "gate_io.py",
    "terminal_history.py",
    "previous_context.py",
    "rendering.py",
    "panel_publish.py",
}
FORBIDDEN_RENDER_IMPORTS = {
    "gate_io.py",
    "terminal_history.py",
    "previous_context.py",
    "panel_publish.py",
}


def _aggregate_module():
    name = "gate_aggregate_render_publish_contract"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, AGGREGATOR / "aggregate.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


AGG = _aggregate_module()


def _local_dependencies(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    dependencies = set()
    canonical_to_file = {
        "gate_aggregate_models": "aggregate_models.py",
        "gate_disposition_projection": "disposition_projection.py",
        "gate_finding_relations": "finding_relations.py",
        "gate_aggregate_decision": "decision.py",
        "gate_aggregate_io": "gate_io.py",
        "gate_terminal_history": "terminal_history.py",
        "gate_previous_context": "previous_context.py",
        "gate_aggregate_rendering": "rendering.py",
        "gate_panel_publish": "panel_publish.py",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in canonical_to_file:
            dependencies.add(canonical_to_file[node.module])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "with_name" and node.args and isinstance(node.args[0], ast.Constant):
                target = node.args[0].value
                if target in canonical_to_file.values():
                    dependencies.add(target)
    return dependencies


def test_exceptions_are_test_only_and_nine_split_modules_form_a_dag():
    exceptions = json.loads((ROOT / ".file-size-exceptions.json").read_text(encoding="utf-8"))
    exception_paths = {item["path"] for item in exceptions}
    assert ".github/actions/gate-aggregator/aggregate.py" not in exception_paths
    assert ".github/actions/gate-aggregator/convergence.py" not in exception_paths
    assert exception_paths == {
        "tests/test_gate_aggregator.py",
    }

    paths = {name: AGGREGATOR / name for name in SPLIT_MODULES}
    graph = {name: _local_dependencies(path) & SPLIT_MODULES for name, path in paths.items()}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        assert name not in visiting, f"module dependency cycle at {name}"
        if name in visited:
            return
        visiting.add(name)
        for dependency in graph[name]:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for name in SPLIT_MODULES:
        visit(name)

    rendering_dependencies = _local_dependencies(paths["rendering.py"])
    assert not (rendering_dependencies & FORBIDDEN_RENDER_IMPORTS)


def _publish_only_argv(tmp_path: Path) -> list[str]:
    terminal = tmp_path / "gate-terminal.json"
    terminal.write_text("{}", encoding="utf-8")
    return [
        "--quality-result", "success",
        "--primary-result", "success",
        "--runner", "self",
        "--is-draft", "false",
        "--review-expected", "true",
        "--repository-id", "123",
        "--repository", "owner/repo",
        "--head-sha", "a" * 40,
        "--run-id", "701",
        "--run-attempt", "1",
        "--pr-number", "42",
        "--terminal-path", str(terminal),
        "--publish-only",
    ]


def test_main_keeps_aggregate_monkeypatch_ports_live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    assert AGG._PANEL_PUBLISH.__name__ == "gate_panel_publish"
    calls: list[str] = []
    panel_path = tmp_path / "panel.json"
    argv = _publish_only_argv(tmp_path) + ["--panel-delivery-path", str(panel_path)]

    monkeypatch.setattr(AGG, "_terminal_row", lambda *args, **kwargs: {
        "run_id": 701,
        "run_attempt": 1,
        "gate_result": "pass",
        "classification": "accepted",
        "reason_code": "primary_pass",
        "head_sha": "a" * 40,
        "repository": "owner/repo",
    })
    monkeypatch.setattr(AGG, "_read_audit_file", lambda *args, **kwargs: (None, None, None))
    monkeypatch.setattr(AGG, "render_status_panel", lambda *args, **kwargs: calls.append("render") or "panel")
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: calls.append("fetch") or [])
    monkeypatch.setattr(AGG, "_post_issue_comment", lambda **kwargs: calls.append("post"))
    monkeypatch.setattr(AGG, "_github_identity", lambda token: {"id": 1, "login": "bot"})
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad())
    monkeypatch.setattr(AGG, "scrub_for_publish", lambda text, **kwargs: calls.append("scrub") or text)
    monkeypatch.setattr(AGG, "_write_panel_delivery", lambda path, receipt: calls.append("write"))
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    assert AGG.main(argv) == 0
    assert {"render", "fetch", "post", "scrub", "write"} <= set(calls)


def test_main_uses_aggregate_post_status_and_delivery_write_patches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    assert AGG._PANEL_PUBLISH.__name__ == "gate_panel_publish"
    calls: list[str] = []
    argv = _publish_only_argv(tmp_path) + ["--panel-delivery-path", str(tmp_path / "receipt.json")]

    monkeypatch.setattr(AGG, "_terminal_row", lambda *args, **kwargs: {
        "run_id": 701,
        "run_attempt": 1,
        "gate_result": "pass",
        "classification": "accepted",
        "reason_code": "primary_pass",
        "head_sha": "a" * 40,
        "repository": "owner/repo",
    })
    monkeypatch.setattr(AGG, "_read_audit_file", lambda *args, **kwargs: (None, None, None))
    monkeypatch.setattr(
        AGG, "_post_status_panel_fail_open",
        lambda **kwargs: calls.append("publish") or ("panel", {"delivery": "not_created", "reason_code": "stub"}),
    )
    monkeypatch.setattr(AGG, "scrub_for_publish", lambda text, **kwargs: calls.append("scrub") or text)
    monkeypatch.setattr(AGG, "render_status_panel", lambda *args, **kwargs: "panel")
    monkeypatch.setattr(AGG, "_write_panel_delivery", lambda path, receipt: calls.append("write"))

    assert AGG.main(argv) == 0
    assert {"publish", "scrub", "write"} <= set(calls)


def test_main_panel_update_uses_history_retry_and_duplicate_cleanup_ports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    calls: list[str] = []
    argv = _publish_only_argv(tmp_path) + ["--panel-delivery-path", str(tmp_path / "receipt.json")]
    cache_path = tmp_path / "history.json"
    cache_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(AGG, "_terminal_row", lambda *args, **kwargs: {
        "run_id": 701, "run_attempt": 1, "gate_result": "pass",
        "classification": "accepted", "reason_code": "primary_pass",
        "head_sha": "a" * 40, "repository": "owner/repo",
    })
    monkeypatch.setattr(AGG, "_terminal_history_cache_path", lambda path: calls.append("cache_path") or cache_path)
    monkeypatch.setattr(AGG, "_read_terminal_history_cache", lambda path: calls.append("cache_read") or AGG.HistoryLoad())
    monkeypatch.setattr(AGG, "_read_audit_file", lambda *args, **kwargs: (None, None, None))
    monkeypatch.setattr(AGG, "_github_identity", lambda token: {"id": 1, "login": "bot"})
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: calls.append("history") or AGG.HistoryLoad())
    panel_comments = [
        {"id": 10, "created_at": "2026-01-01T00:00:00Z", "body": AGG.PANEL_MARKER, "user": {"id": 1, "login": "bot"}},
        {"id": 11, "created_at": "2026-01-02T00:00:00Z", "body": AGG.PANEL_MARKER, "user": {"id": 1, "login": "bot"}},
    ]
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: calls.append("fetch") or panel_comments)
    patch_attempts = iter(("retry", "ok"))
    def patch_comment(**kwargs):
        result = next(patch_attempts)
        calls.append("patch")
        if result == "retry":
            raise AGG.urllib.error.URLError("temporary")
    monkeypatch.setattr(AGG, "_patch_issue_comment", patch_comment)
    monkeypatch.setattr(AGG, "_github_json", lambda **kwargs: {"body": "old panel"})
    monkeypatch.setattr(AGG, "_delete_issue_comment", lambda **kwargs: calls.append("delete"))
    monkeypatch.setattr(AGG, "render_status_panel", lambda *args, **kwargs: "panel")
    monkeypatch.setattr(AGG, "scrub_for_publish", lambda text, **kwargs: text)
    monkeypatch.setattr(AGG, "_write_panel_delivery", lambda path, receipt: calls.append("write"))
    monkeypatch.setattr(AGG.time, "sleep", lambda seconds: calls.append("sleep"))
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    assert AGG.main(argv) == 0
    assert {"cache_path", "cache_read", "history", "fetch", "patch", "sleep", "delete", "write"} <= set(calls)


def test_isolated_aggregate_and_rendering_emit_identical_summary_and_panel_bytes(tmp_path: Path):
    isolated = tmp_path / "repo"
    action_dir = isolated / ".github/actions/gate-aggregator"
    action_dir.mkdir(parents=True)
    scripts = isolated / "scripts"
    scripts.mkdir()
    for source in AGGREGATOR.glob("*.py"):
        shutil.copy2(source, action_dir / source.name)
    shutil.copy2(ROOT / "scripts/scrub_outbound.py", scripts / "scrub_outbound.py")
    script = r'''
import base64, importlib.util, json, sys, tempfile
from pathlib import Path
path = Path(sys.argv[1]) / ".github/actions/gate-aggregator/aggregate.py"
spec = importlib.util.spec_from_file_location("isolated_aggregate", path)
agg = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = agg
spec.loader.exec_module(agg)
rendering = sys.modules["gate_aggregate_rendering"]
outcome = agg.Outcome(ok=True)
summary_path = Path(tempfile.mkdtemp()) / "summary.md"
agg._finish(outcome, str(summary_path))
summary = summary_path.read_bytes()
expected = agg.scrub_for_publish(
    rendering.render_summary(outcome),
    runtime_values=agg.runtime_values_from_environment(),
).encode("utf-8")
verdict = {"v": 1, "gate_result": "pass", "classification": "integration_error",
           "reason_code": "audit_invalid", "primary": "executed", "skip_reason": None, "draft": False}
expected += ("AGENT-GATE-VERDICT-V1 " + json.dumps(verdict, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
row = {"run_id": 701, "run_attempt": 1, "gate_result": "fail", "classification": "code_fail",
       "reason_code": "primary_findings", "head_sha": "a" * 40, "repository": "owner/repo"}
panel_aggregate = agg.render_status_panel([row]).encode("utf-8")
panel_rendering = rendering.render_status_panel([row]).encode("utf-8")
identity = agg.Identity(repository_id=123, head_sha="a" * 40, run_id=701, run_attempt=1, pr=42)
receipt = agg._build_panel_delivery(body="panel", repository="owner/repo", pr_number=42,
                                    identity=identity, delivery="created", reason_code="posted")
aggregate_receipt_path = Path(tempfile.mkdtemp()) / "aggregate.json"
panel_receipt_path = Path(tempfile.mkdtemp()) / "panel.json"
agg._write_panel_delivery(str(aggregate_receipt_path), receipt)
agg._PANEL_PUBLISH._write_panel_delivery(str(panel_receipt_path), receipt)
print(json.dumps({"summary": base64.b64encode(summary).decode(),
                  "expected": base64.b64encode(expected).decode(),
                  "panel_aggregate": base64.b64encode(panel_aggregate).decode(),
                  "panel_rendering": base64.b64encode(panel_rendering).decode(),
                  "receipt_aggregate": base64.b64encode(aggregate_receipt_path.read_bytes()).decode(),
                  "receipt_panel": base64.b64encode(panel_receipt_path.read_bytes()).decode()}))
'''
    completed = subprocess.run(
        [sys.executable, "-c", script, str(isolated)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout.splitlines()[-1])
    summary_bytes = base64.b64decode(payload["summary"])
    expected_summary_bytes = base64.b64decode(payload["expected"])
    panel_bytes = base64.b64decode(payload["panel_aggregate"])
    assert summary_bytes == expected_summary_bytes
    assert panel_bytes == base64.b64decode(payload["panel_rendering"])
    assert base64.b64decode(payload["receipt_aggregate"]) == base64.b64decode(payload["receipt_panel"])


def test_aggregate_canonical_loader_names_every_split_module():
    text = (AGGREGATOR / "aggregate.py").read_text(encoding="utf-8")
    loaded = set(re.findall(r'Path\(__file__\)\.with_name\("([^"]+\.py)"\)', text))
    assert SPLIT_MODULES <= loaded
