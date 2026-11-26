"""Contract for the red-on-base reusable workflow (G1, gate-hub#1815)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import jsonschema
import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "red-on-base.yml"
CALLER = REPO / "templates" / "caller-red-on-base.yml"
GATE_V2 = REPO / ".github" / "workflows" / "gate-v2.yml"


def _doc(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _trigger(doc):
    return doc.get("on") or doc.get(True) or {}


def test_workflow_has_no_make_test():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "make test" not in json.dumps(_doc(WORKFLOW))
    assert "make test" not in text


def test_result_job_always_runs():
    assert _doc(WORKFLOW)["jobs"]["result"].get("if") in ("always()", "${{ always() }}")


def test_tree_uploads_always():
    steps = _doc(WORKFLOW)["jobs"]["tree"]["steps"]
    uploads = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert uploads
    for step in uploads:
        assert step.get("if") in ("always()", "${{ always() }}")
    run = next(s for s in steps if s.get("name") == "Run subset and write tree files")
    assert run.get("if") in ("always()", "${{ always() }}")
    body = run["run"]
    assert "GATE_RED_ON_BASE_PYTEST_FILES" in body
    assert "GATE_RED_ON_BASE_JUNIT" in body
    assert "scripts/gate-quality" in body
    assert "entry_missing" in body
    assert "make test" not in body


def test_uses_are_pinned():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in text
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in text
    assert "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093" in text
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("uses: actions/"):
            assert "@" in stripped and not stripped.endswith("@v4")


def test_gate_v2_yml_does_not_wire_red_on_base():
    text = GATE_V2.read_text(encoding="utf-8")
    assert "red-on-base" not in text and "GATE_RED_ON_BASE" not in text


def test_real_shell_manifest_workflow_executes_last_failure_and_synthesizes_result(tmp_path):
    work = tmp_path / "caller"
    work.mkdir()
    (work / "tests").mkdir()
    (work / "scripts").mkdir()
    (work / "tests" / "dummy.py").write_text("", encoding="utf-8")
    (work / "test-first.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (work / "test-last.sh").write_text("#!/bin/sh\nexit 17\n", encoding="utf-8")
    gate_quality = work / "scripts" / "gate-quality"
    gate_quality.write_text(
        '''#!/bin/sh\nprintf '%s' "$GATE_RED_ON_BASE_PYTEST_FILES" > pytest-files-consumed.txt\n''',
        encoding="utf-8",
    )
    gate_quality.chmod(0o755)
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=workflow-test", "-c", "user.email=workflow-test@example.invalid", "commit", "-qm", "fixture"],
        cwd=work, check=True,
    )
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, check=True, capture_output=True, text=True).stdout.strip()
    tree_hash = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=work, check=True, capture_output=True, text=True).stdout.strip()
    run_body = next(
        step["run"] for step in _doc(WORKFLOW)["jobs"]["tree"]["steps"]
        if step.get("name") == "Run subset and write tree files"
    )
    shell_files = ["test-first.sh", "test-last.sh"]
    shell_json = json.dumps(shell_files)
    pytest_json = json.dumps(["tests/dummy.py"])
    artifacts = tmp_path / "artifacts"

    for role in ("contrast", "tip"):
        tree_out = artifacts / role
        env = os.environ | {
            "EXPECTED_SHA": sha, "EXPECTED_TREE": tree_hash, "ROLE": role,
            "PYTEST_FILES_JSON": pytest_json, "SHELL_FILES_JSON": shell_json,
            "TIMEOUT_MINUTES": "2", "TREE_OUT": str(tree_out),
        }
        result = subprocess.run(["bash", "-e", "-c", run_body], cwd=work, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert (tree_out / "pytest_files.txt").read_bytes() == b"tests/dummy.py\n"
        assert (tree_out / "shell_files.txt").read_bytes() == b"test-first.sh\ntest-last.sh\n"
        assert (work / "pytest-files-consumed.txt").read_text(encoding="utf-8") == "tests/dummy.py"
        producer_cases = [json.loads(line) for line in (tree_out / "shell.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [case["id"] for case in producer_cases] == shell_files
        assert [case["status"] for case in producer_cases] == ["pass", "fail"]

    out = artifacts / "result.json"
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "red_on_base_result.py"),
         "--dispatch-id", "dlg-workflow-test", "--run-id", "1", "--run-attempt", "1",
         "--contrast-sha", sha, "--tip-sha", sha, "--contrast-tree", tree_hash, "--tip-tree", tree_hash,
         "--pytest-files", pytest_json, "--shell-files", json.dumps(shell_files),
         "--contrast-dir", str(artifacts / "contrast"), "--tip-dir", str(artifacts / "tip"), "--out", str(out)],
        cwd=work, check=True, capture_output=True, text=True,
    )
    doc = json.loads(out.read_text(encoding="utf-8"))
    schema = json.loads((REPO / "docs" / "red-on-base-result-v1.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(doc, schema)
    assert doc["evidence_status"] == "complete"
    for role in ("contrast", "tip"):
        assert [(case["id"], case["status"]) for case in doc["trees"][role]["cases"]] == [
            ("test-first.sh", "pass"), ("test-last.sh", "fail")
        ]


@pytest.mark.parametrize("manifest", [["test-last.sh"], ["test-first.sh", "test-last.sh"]], ids=["one-script", "two-scripts"])
@pytest.mark.parametrize("trailing_newline", [False, True], ids=["without-final-newline", "with-final-newline"])
def test_workflow_loop_executes_unterminated_last_manifest_entry(tmp_path, manifest, trailing_newline):
    work = tmp_path / "caller"
    work.mkdir()
    (work / "test-first.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (work / "test-last.sh").write_text("#!/bin/sh\nexit 17\n", encoding="utf-8")
    tree_out = tmp_path / "tree"
    tree_out.mkdir()
    manifest_bytes = "\n".join(manifest).encode("utf-8")
    (tree_out / "shell_files.txt").write_bytes(manifest_bytes + (b"\n" if trailing_newline else b""))
    (tree_out / "shell.jsonl").write_text("", encoding="utf-8")
    body = next(
        step["run"] for step in _doc(WORKFLOW)["jobs"]["tree"]["steps"]
        if step.get("name") == "Run subset and write tree files"
    )
    start = body.index("    while IFS= read -r f")
    marker = '    done <"$TREE_OUT/shell_files.txt"'
    end = body.index(marker, start) + len(marker)
    loop = textwrap.dedent(body[start:end])
    env = os.environ | {"TREE_OUT": str(tree_out)}
    result = subprocess.run(["bash", "-e", "-c", "set -euo pipefail\n" + loop], cwd=work, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    cases = [json.loads(line) for line in (tree_out / "shell.jsonl").read_text(encoding="utf-8").splitlines()]
    expected = [(name, "fail" if name == "test-last.sh" else "pass") for name in manifest]
    assert [(case["id"], case["status"]) for case in cases] == expected


def test_schema_id_is_absolute_https_uri():
    schema = json.loads((REPO / "docs/red-on-base-result-v1.schema.json").read_text())
    assert schema["$id"].startswith("https://")
    assert schema["properties"]["schema"]["const"] == "red-on-base-result/v1"


def test_caller_template_dispatches_v2():
    doc = _doc(CALLER)
    assert "workflow_dispatch" in _trigger(doc)
    assert doc["jobs"]["red-on-base"]["uses"] == "zlxlabs/gate/.github/workflows/red-on-base.yml@v2"
    assert _trigger(_doc(WORKFLOW))["workflow_call"]["inputs"]["timeout_minutes"]["default"] == 15
