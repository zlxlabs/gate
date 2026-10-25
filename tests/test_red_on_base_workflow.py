"""Contract for the red-on-base reusable workflow (G1, gate-hub#1815)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

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


def test_gate_v2_yml_untouched():
    text = GATE_V2.read_text(encoding="utf-8")
    assert "red-on-base" not in text and "GATE_RED_ON_BASE" not in text
    probe = subprocess.run(["git", "rev-parse", "--verify", "origin/main"], cwd=REPO, capture_output=True, check=False)
    if probe.returncode != 0:
        return
    diff = subprocess.check_output(["git", "diff", "origin/main", "--", ".github/workflows/gate-v2.yml"], cwd=REPO)
    assert diff == b""


def test_schema_id_is_absolute_https_uri():
    schema = json.loads((REPO / "docs/red-on-base-result-v1.schema.json").read_text())
    assert schema["$id"].startswith("https://")
    assert schema["properties"]["schema"]["const"] == "red-on-base-result/v1"


def test_caller_template_dispatches_v2():
    doc = _doc(CALLER)
    assert "workflow_dispatch" in _trigger(doc)
    assert doc["jobs"]["red-on-base"]["uses"] == "zlxlabs/gate/.github/workflows/red-on-base.yml@v2"
    assert _trigger(_doc(WORKFLOW))["workflow_call"]["inputs"]["timeout_minutes"]["default"] == 15
