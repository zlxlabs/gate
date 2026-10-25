"""red-on-base-result/v1 mapping and insufficient_code (K2 §1.2 / §1.3)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from scripts.red_on_base_result import load_tree_dir, parse_junit, synthesize

REPO = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((REPO / "docs/red-on-base-result-v1.schema.json").read_text())
FIXTURES = REPO / "tests" / "fixtures" / "red_on_base"
C_SHA, T_SHA, C_TREE, T_TREE = "c" * 40, "d" * 40, "e" * 40, "f" * 40


def _junit(mod: str, tmp: Path) -> Path:
    work = tmp / "work"
    work.mkdir(parents=True)
    shutil.copy(FIXTURES / f"{mod}.py", work / "mod.py")
    xml = tmp / f"{mod}.xml"
    subprocess.run(
        [sys.executable, "-m", "pytest", "mod.py", f"--junitxml={xml}", "-q", "-p", "no:cacheprovider"],
        cwd=work, check=False, capture_output=True, text=True,
    )
    assert xml.is_file(), f"pytest did not write junit for {mod}"
    return xml


def _tree(dir_: Path, *, role: str, sha: str, tree: str, junit: Path | None = None, step: str = "", pytest_exit: str = "0") -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "role").write_text(role, encoding="utf-8")
    (dir_ / "actual_sha").write_text(sha, encoding="utf-8")
    (dir_ / "actual_tree").write_text(tree, encoding="utf-8")
    (dir_ / "step_insufficient").write_text(step, encoding="utf-8")
    (dir_ / "pytest_exit").write_text(pytest_exit, encoding="utf-8")
    (dir_ / "shell.jsonl").write_text("", encoding="utf-8")
    if junit is not None:
        shutil.copy(junit, dir_ / "junit.xml")


def _synth(contrast: Path, tip: Path):
    return synthesize(
        dispatch_id="dlg-test", run_id=1, run_attempt=1,
        contrast_sha=C_SHA, tip_sha=T_SHA, contrast_tree=C_TREE, tip_tree=T_TREE,
        pytest_files=["mod.py"], shell_files=[],
        contrast=load_tree_dir(contrast), tip=load_tree_dir(tip),
    )


def test_zero_cases_even_when_step_exits_zero(tmp_path):
    xml = _junit("empty_mod", tmp_path / "empty")
    assert parse_junit(xml) == []
    _tree(tmp_path / "c", role="contrast", sha=C_SHA, tree=C_TREE, junit=xml, pytest_exit="0")
    _tree(tmp_path / "t", role="tip", sha=T_SHA, tree=T_TREE, junit=xml, pytest_exit="0")
    doc = _synth(tmp_path / "c", tmp_path / "t")
    jsonschema.validate(doc, SCHEMA)
    assert doc["evidence_status"] == "insufficient"
    assert doc["insufficient_code"] == "zero_cases"


def test_all_skipped(tmp_path):
    xml = _junit("skip_mod", tmp_path / "skip")
    cases = parse_junit(xml)
    assert cases and all(c["status"] == "skip" for c in cases)
    _tree(tmp_path / "c", role="contrast", sha=C_SHA, tree=C_TREE, junit=xml)
    _tree(tmp_path / "t", role="tip", sha=T_SHA, tree=T_TREE, junit=xml)
    doc = _synth(tmp_path / "c", tmp_path / "t")
    jsonschema.validate(doc, SCHEMA)
    assert doc["evidence_status"] == "insufficient"
    assert doc["insufficient_code"] == "all_skipped"


def test_contrast_failure_tip_pass_is_complete_assertion(tmp_path):
    fail_xml, pass_xml = _junit("fail_mod", tmp_path / "fail"), _junit("pass_mod", tmp_path / "pass")
    fail_cases, pass_cases = parse_junit(fail_xml), parse_junit(pass_xml)
    assert fail_cases == [{"id": fail_cases[0]["id"], "status": "fail", "fail_kind": "assertion", "channel": "pytest"}]
    assert pass_cases[0]["status"] == "pass" and fail_cases[0]["id"] == pass_cases[0]["id"]
    _tree(tmp_path / "c", role="contrast", sha=C_SHA, tree=C_TREE, junit=fail_xml)
    _tree(tmp_path / "t", role="tip", sha=T_SHA, tree=T_TREE, junit=pass_xml)
    doc = _synth(tmp_path / "c", tmp_path / "t")
    jsonschema.validate(doc, SCHEMA)
    assert doc["evidence_status"] == "complete" and "insufficient_code" not in doc
    hit = doc["trees"]["contrast"]["cases"][0]
    assert hit["status"] == "fail" and hit["fail_kind"] == "assertion"
    assert doc["trees"]["tip"]["cases"][0]["status"] == "pass"


def test_import_error_is_not_a_hit(tmp_path):
    xml = _junit("import_error_mod", tmp_path / "err")
    cases = parse_junit(xml)
    assert cases and all(c["status"] == "error" and c["fail_kind"] == "import" for c in cases)
    assert not any(c["status"] == "fail" and c["fail_kind"] == "assertion" for c in cases)
    _tree(tmp_path / "c", role="contrast", sha=C_SHA, tree=C_TREE, junit=xml)
    _tree(tmp_path / "t", role="tip", sha=T_SHA, tree=T_TREE, junit=_junit("pass_mod", tmp_path / "pass"))
    doc = _synth(tmp_path / "c", tmp_path / "t")
    jsonschema.validate(doc, SCHEMA)
    err = doc["trees"]["contrast"]["cases"][0]
    assert doc["evidence_status"] == "complete"
    assert err["status"] == "error" and err["fail_kind"] == "import"


def test_tree_mismatch(tmp_path):
    _tree(tmp_path / "c", role="contrast", sha=C_SHA, tree="0" * 40, junit=_junit("fail_mod", tmp_path / "fail"), step="tree_mismatch")
    _tree(tmp_path / "t", role="tip", sha=T_SHA, tree=T_TREE, junit=_junit("pass_mod", tmp_path / "pass"))
    doc = _synth(tmp_path / "c", tmp_path / "t")
    jsonschema.validate(doc, SCHEMA)
    assert doc["evidence_status"] == "insufficient"
    assert doc["insufficient_code"] == "tree_mismatch"


def test_insufficient_without_code_fails_schema(tmp_path):
    _tree(tmp_path / "c", role="contrast", sha=C_SHA, tree=C_TREE, junit=_junit("fail_mod", tmp_path / "fail"))
    _tree(tmp_path / "t", role="tip", sha=T_SHA, tree=T_TREE, junit=_junit("pass_mod", tmp_path / "pass"))
    doc = _synth(tmp_path / "c", tmp_path / "t")
    doc["evidence_status"] = "insufficient"
    doc.pop("insufficient_code", None)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, SCHEMA)
