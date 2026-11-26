"""red-on-base-result/v1 mapping and insufficient_code (K2 §1.2 / §1.3)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
jsonschema = pytest.importorskip("jsonschema")

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


def _synth(contrast: Path, tip: Path, *, shell_files: list[str] | None = None):
    return synthesize(
        dispatch_id="dlg-test", run_id=1, run_attempt=1,
        contrast_sha=C_SHA, tip_sha=T_SHA, contrast_tree=C_TREE, tip_tree=T_TREE,
        pytest_files=["mod.py"], shell_files=shell_files or [],
        contrast=load_tree_dir(contrast), tip=load_tree_dir(tip),
    )


def _shell_case(case_id: str) -> dict[str, str | None]:
    return {"id": case_id, "status": "pass", "fail_kind": None, "channel": "shell"}


def _write_shell_cases(tree: Path, case_ids: list[str]) -> None:
    (tree / "shell.jsonl").write_text(
        "".join(json.dumps(_shell_case(case_id)) + "\n" for case_id in case_ids),
        encoding="utf-8",
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


@pytest.mark.parametrize("role", ["contrast", "tip"])
@pytest.mark.parametrize(
    "actual_ids",
    [[], ["tests/test_shell.sh", "tests/test_shell.sh"], ["tests/test_shell.sh", "tests/extra.sh"]],
    ids=["missing", "duplicate", "undeclared-extra"],
)
def test_shell_manifest_mismatch_is_insufficient(tmp_path, role, actual_ids):
    c, t = tmp_path / "c", tmp_path / "t"
    _tree(c, role="contrast", sha=C_SHA, tree=C_TREE)
    _tree(t, role="tip", sha=T_SHA, tree=T_TREE)
    _write_shell_cases(c if role == "contrast" else t, actual_ids)
    _write_shell_cases(t if role == "contrast" else c, ["tests/test_shell.sh"])

    doc = _synth(c, t, shell_files=["tests/test_shell.sh"])

    jsonschema.validate(doc, SCHEMA)
    assert doc["evidence_status"] == "insufficient"
    assert doc["insufficient_code"] == "artifact_missing"


def test_duplicate_shell_manifest_is_insufficient(tmp_path):
    c, t = tmp_path / "c", tmp_path / "t"
    _tree(c, role="contrast", sha=C_SHA, tree=C_TREE)
    _tree(t, role="tip", sha=T_SHA, tree=T_TREE)
    for tree in (c, t):
        _write_shell_cases(tree, ["tests/test_shell.sh"])

    doc = _synth(c, t, shell_files=["tests/test_shell.sh", "tests/test_shell.sh"])

    jsonschema.validate(doc, SCHEMA)
    assert doc["evidence_status"] == "insufficient"
    assert doc["insufficient_code"] == "artifact_missing"


def test_timeout_takes_priority_over_unexecuted_shell_manifest(tmp_path):
    c, t = tmp_path / "c", tmp_path / "t"
    _tree(c, role="contrast", sha=C_SHA, tree=C_TREE, step="timeout")
    _tree(t, role="tip", sha=T_SHA, tree=T_TREE)

    doc = _synth(c, t, shell_files=["tests/test_shell.sh"])

    jsonschema.validate(doc, SCHEMA)
    assert doc["evidence_status"] == "insufficient"
    assert doc["insufficient_code"] == "timeout"


def test_matching_shell_manifest_is_complete(tmp_path):
    c, t = tmp_path / "c", tmp_path / "t"
    _tree(c, role="contrast", sha=C_SHA, tree=C_TREE)
    _tree(t, role="tip", sha=T_SHA, tree=T_TREE)
    for tree in (c, t):
        _write_shell_cases(tree, ["tests/test_shell.sh"])

    doc = _synth(c, t, shell_files=["tests/test_shell.sh"])

    jsonschema.validate(doc, SCHEMA)
    assert doc["evidence_status"] == "complete"
    assert "insufficient_code" not in doc


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


@pytest.mark.parametrize("code", ["artifact_missing", "cancelled", "install_failed", "entry_missing"])
def test_insufficient_input_codes(tmp_path, code):
    _tree(tmp_path / "t", role="tip", sha=T_SHA, tree=T_TREE, junit=_junit("pass_mod", tmp_path / "p"))
    c = tmp_path / "c"
    if code != "artifact_missing":
        _tree(c, role="contrast", sha=C_SHA, tree=C_TREE, pytest_exit="1" if code == "install_failed" else "0")
        if code == "cancelled":
            (c / "cancelled").write_text("", encoding="utf-8")
        elif code == "entry_missing":
            (c / "pytest_requested").write_text("true", encoding="utf-8")
            (c / "entry_present").write_text("false", encoding="utf-8")
    doc = _synth(tmp_path / "x" if code == "artifact_missing" else c, tmp_path / "t")
    jsonschema.validate(doc, SCHEMA)
    assert doc["insufficient_code"] == code


ERROR_JUNIT = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<testsuites><testsuite name="gate">'
    '<testcase classname="newpkg" name="test_new"><error message="ModuleNotFoundError: No module named \'foo_new\'">Traceback …</error></testcase>'
    '<testcase classname="newpkg" name="test_pass"/>'
    '<testcase classname="newpkg" name="test_fail"><failure message="assert 1 == 2">assert 1 == 2</failure></testcase>'
    '<testcase classname="newpkg" name="test_skip"><skipped message="todo"/></testcase>'
    '</testsuite></testsuites>'
)


def test_error_case_message_survives_result_json_bytes(tmp_path):
    c, t, out = tmp_path / "c", tmp_path / "t", tmp_path / "result.json"
    for d, role, sha, tree in ((c, "contrast", C_SHA, C_TREE), (t, "tip", T_SHA, T_TREE)):
        _tree(d, role=role, sha=sha, tree=tree)
    (c / "junit.xml").write_text(ERROR_JUNIT, encoding="utf-8")
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "red_on_base_result.py"),
         "--dispatch-id", "dlg-msg", "--run-id", "1", "--run-attempt", "1",
         "--contrast-sha", C_SHA, "--tip-sha", T_SHA, "--contrast-tree", C_TREE, "--tip-tree", T_TREE,
         "--pytest-files", "[]", "--shell-files", "[]",
         "--contrast-dir", str(c), "--tip-dir", str(t), "--out", str(out)],
        check=True, capture_output=True, text=True,
    )
    raw = out.read_bytes()
    doc = json.loads(raw)
    jsonschema.validate(doc, SCHEMA)
    cases = doc["trees"]["contrast"]["cases"]
    err = next(x for x in cases if x["status"] == "error")
    assert err["fail_kind"] == "import"
    assert "No module named 'foo_new'" in err["message"]
    assert b"No module named 'foo_new'" in raw
    assert all("message" not in x for x in cases if x["status"] != "error")


def test_error_message_blank_attr_takes_first_body_line(tmp_path):
    xml = tmp_path / "body.xml"
    xml.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<testsuites><testsuite name="gate">'
        '<testcase classname="m" name="t1"><error message="">\n   \n  ImportError: cannot import name \'boom\'\n  during collection\n</error></testcase>'
        '</testsuite></testsuites>',
        encoding="utf-8",
    )
    case = parse_junit(xml)[0]
    assert case["fail_kind"] == "import"
    assert case["message"] == "ImportError: cannot import name 'boom'"


def test_error_message_truncated_to_500_utf8_bytes(tmp_path):
    xml = tmp_path / "long.xml"
    long_msg = "中" * 200
    xml.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<testsuites><testsuite name="gate">'
        '<testcase classname="m" name="t1"><error message="' + long_msg + '">body</error></testcase>'
        '</testsuite></testsuites>',
        encoding="utf-8",
    )
    msg = parse_junit(xml)[0]["message"]
    assert msg == "中" * 166  # 166×3=498 bytes fit; 167 would exceed 500
    assert len(msg.encode("utf-8")) <= 500
    assert msg.encode("utf-8").decode("utf-8") == msg


def _collection_error_junit(tmp: Path) -> Path:
    """Run real pytest on a module whose import fails at collection; junit is genuine."""
    work = tmp / "coll"
    work.mkdir()
    (work / "broken_import.py").write_text(
        "from foo_new import x\n\n\ndef test_one():\n    assert True\n", encoding="utf-8"
    )
    xml = tmp / "coll.xml"
    subprocess.run(
        [sys.executable, "-m", "pytest", "broken_import.py", f"--junitxml={xml}", "-q", "-p", "no:cacheprovider"],
        cwd=work, check=False, capture_output=True, text=True,
    )
    assert xml.is_file(), "pytest did not write junit for collection error"
    return xml


def test_collection_error_message_takes_import_body_line(tmp_path):
    xml = _collection_error_junit(tmp_path)
    case = parse_junit(xml)[0]
    assert case["status"] == "error" and case["fail_kind"] == "import"
    assert case["message"] == "ModuleNotFoundError: No module named 'foo_new'"
