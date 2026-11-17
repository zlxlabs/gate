"""Docs-only entry-script early exit: caller -> gate result convention.

The caller's `scripts/gate-quality` decides docs-only by itself (gate-v2 does
no path-based short-circuit) and exits 0 with a `mode=docs-only` result JSON.
These tests run the real gate-v2 workflow scripts (via the helper functions
from the existing contract modules) to lock the gate-side guarantees: quality
stays green, the summary renders, and no evidence line is printed.
"""
from __future__ import annotations

import json
from pathlib import Path

from test_gate_v2_contract import _run_quality_result_render
from test_gate_v2_tested_evidence import _evidence_lines, _passing_xml, _run_emit

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs" / "gate-tested-evidence.md"

# The convention itself: this literal is the caller -> gate file boundary and
# must stay byte-for-byte identical to the one quoted in the docs section
# "入口脚本提前退出（docs-only 等）".
DOCS_ONLY_RESULT = (
    b'{"schema":"gate-quality-result/v1","mode":"docs-only","stage":"setup",'
    b'"outcome":"not_started","failed_nodeids":null,"evidence":"not_applicable",'
    b'"flake_signal":"undetermined"}\n'
)


def test_docs_only_result_literal_is_documented_verbatim():
    assert json.loads(DOCS_ONLY_RESULT)["mode"] == "docs-only"
    assert DOCS_ONLY_RESULT.decode().strip() in DOCS.read_text(encoding="utf-8")


def test_render_docs_only_result_renders_summary_without_warning(tmp_path):
    _step, completed, summary = _run_quality_result_render(tmp_path, DOCS_ONLY_RESULT)
    assert completed.returncode == 0, completed.stderr
    assert "::warning::" not in completed.stdout + completed.stderr
    text = summary.decode()
    assert "- Stage: setup" in text
    assert "- Outcome: not_started" in text
    assert "- Evidence: not_applicable" in text
    assert "- Mode: docs-only" in text


def test_emit_docs_only_result_does_not_print_evidence(tmp_path):
    completed, summary = _run_emit(
        tmp_path, junit_xml=_passing_xml(tmp_path), result=DOCS_ONLY_RESULT
    )
    assert completed.returncode == 0, completed.stderr
    assert _evidence_lines(completed.stdout) == []
    assert "tested-evidence: not_emitted reason=result_unqualified" in summary
