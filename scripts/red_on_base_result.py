#!/usr/bin/env python3
"""Synthesize red-on-base-result/v1 from two tree artifacts.

Junit §1.2: <failure>→fail/assertion (only hit shape); <error>+ImportError/No
module→error/import; other <error>→error/collection; <skipped>→skip; no child→
pass. <error> is never a hit.

§1.3 codes: this script does not produce run_not_found / not_completed (consumer
owns those when no completed run exists). Missing in-run tree dir is
artifact_missing.
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_ID = "red-on-base-result/v1"
PRIORITY = (
    "artifact_missing", "sha_mismatch", "tree_mismatch", "entry_missing",
    "install_failed", "timeout", "cancelled", "junit_unreadable",
    "zero_cases", "all_skipped",
)


@dataclass
class TreeSnapshot:
    role: str = ""
    actual_sha: str = ""
    actual_tree: str = ""
    cases: list[dict[str, Any]] = field(default_factory=list)
    step_insufficient: str | None = None
    artifact_present: bool = True
    junit_unreadable: bool = False


def _import_error(text: str) -> bool:
    return "ImportError" in text or "ModuleNotFoundError" in text or "No module" in text


def map_junit_case(el: ET.Element) -> dict[str, Any]:
    name = el.get("name") or ""
    loc = el.get("file") or el.get("classname") or ""
    case_id = f"{loc}::{name}" if loc else name
    if el.find("failure") is not None:
        return {"id": case_id, "status": "fail", "fail_kind": "assertion", "channel": "pytest"}
    error = el.find("error")
    if error is not None:
        text = f"{error.get('message') or ''}{error.text or ''}"
        kind = "import" if _import_error(text) else "collection"
        return {"id": case_id, "status": "error", "fail_kind": kind, "channel": "pytest"}
    if el.find("skipped") is not None:
        return {"id": case_id, "status": "skip", "fail_kind": None, "channel": "pytest"}
    return {"id": case_id, "status": "pass", "fail_kind": None, "channel": "pytest"}


def parse_junit(path: Path) -> list[dict[str, Any]]:
    return [map_junit_case(el) for el in ET.parse(path).getroot().iter("testcase")]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def load_tree_dir(path: Path | None) -> TreeSnapshot:
    if path is None or not path.is_dir() or not (path / "role").is_file():
        return TreeSnapshot(artifact_present=False)
    snap = TreeSnapshot(
        role=_read(path / "role"),
        actual_sha=_read(path / "actual_sha"),
        actual_tree=_read(path / "actual_tree"),
        step_insufficient=_read(path / "step_insufficient") or None,
    )
    if snap.step_insufficient is None:
        if (path / "cancelled").is_file():
            snap.step_insufficient = "cancelled"
        elif _read(path / "pytest_requested") == "true" and _read(path / "entry_present") != "true":
            snap.step_insufficient = "entry_missing"
        elif _read(path / "pytest_exit") not in ("", "0") and not (path / "junit.xml").is_file():
            snap.step_insufficient = "install_failed"
    if (path / "junit.xml").is_file():
        try:
            snap.cases.extend(parse_junit(path / "junit.xml"))
        except ET.ParseError:
            snap.junit_unreadable = True
    shell = path / "shell.jsonl"
    if shell.is_file():
        snap.cases.extend(json.loads(line) for line in shell.read_text(encoding="utf-8").splitlines() if line.strip())
    return snap


def _codes_for(snap: TreeSnapshot, expected_sha: str, expected_tree: str) -> list[str]:
    if not snap.artifact_present:
        return ["artifact_missing"]
    codes: list[str] = []
    if snap.step_insufficient:
        codes.append(snap.step_insufficient)
    if snap.junit_unreadable:
        codes.append("junit_unreadable")
    if snap.actual_sha != expected_sha:
        codes.append("sha_mismatch")
    elif snap.actual_tree != expected_tree:
        codes.append("tree_mismatch")
    return codes


def decide_evidence(contrast: TreeSnapshot, tip: TreeSnapshot, c_sha: str, t_sha: str, c_tree: str, t_tree: str) -> tuple[str, str | None]:
    found = set(_codes_for(contrast, c_sha, c_tree) + _codes_for(tip, t_sha, t_tree))
    for code in PRIORITY:
        if code in found and code not in {"zero_cases", "all_skipped"}:
            return "insufficient", code
    cases = contrast.cases + tip.cases
    if not cases:
        return "insufficient", "zero_cases"
    if all(c.get("status") == "skip" for c in cases):
        return "insufficient", "all_skipped"
    return "complete", None


def synthesize(*, dispatch_id: str, run_id: int, run_attempt: int, contrast_sha: str, tip_sha: str, contrast_tree: str, tip_tree: str, pytest_files: list[str], shell_files: list[str], contrast: TreeSnapshot, tip: TreeSnapshot) -> dict[str, Any]:
    status, code = decide_evidence(contrast, tip, contrast_sha, tip_sha, contrast_tree, tip_tree)
    doc: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "dispatch_id": dispatch_id,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "contrast_sha": contrast_sha,
        "tip_sha": tip_sha,
        "contrast_tree": contrast_tree,
        "tip_tree": tip_tree,
        "test_files": {"pytest": list(pytest_files), "shell": list(shell_files)},
        "evidence_status": status,
        "trees": {
            "contrast": {"sha": contrast.actual_sha or contrast_sha, "tree": contrast.actual_tree or contrast_tree, "cases": contrast.cases},
            "tip": {"sha": tip.actual_sha or tip_sha, "tree": tip.actual_tree or tip_tree, "cases": tip.cases},
        },
    }
    if code is not None:
        doc["insufficient_code"] = code
    return doc


def _parse_files(raw: str) -> list[str]:
    value = json.loads(raw)
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise SystemExit("pytest/shell files must be a JSON array of strings")
    return value


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dispatch-id", required=True)
    p.add_argument("--run-id", required=True, type=int)
    p.add_argument("--run-attempt", required=True, type=int)
    p.add_argument("--contrast-sha", required=True)
    p.add_argument("--tip-sha", required=True)
    p.add_argument("--contrast-tree", required=True)
    p.add_argument("--tip-tree", required=True)
    p.add_argument("--pytest-files", required=True)
    p.add_argument("--shell-files", required=True)
    p.add_argument("--contrast-dir", type=Path, required=True)
    p.add_argument("--tip-dir", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args(argv)
    doc = synthesize(
        dispatch_id=a.dispatch_id, run_id=a.run_id, run_attempt=a.run_attempt,
        contrast_sha=a.contrast_sha, tip_sha=a.tip_sha, contrast_tree=a.contrast_tree, tip_tree=a.tip_tree,
        pytest_files=_parse_files(a.pytest_files), shell_files=_parse_files(a.shell_files),
        contrast=load_tree_dir(a.contrast_dir), tip=load_tree_dir(a.tip_dir),
    )
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
