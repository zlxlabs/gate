"""Contract tests for the pure incremental blocking-shadow classifier.

Unit tests lock the invariant axes of dispatch G1 (no-anchor precedence,
duplicate precedence over diff checks, canonical-round folding incl. same-
head rerun and run-attempt dedup, rename-only diffs never on_diff, §4.1
counterfactual) plus the fail-fast ``incremental_shadow_invalid_input``
contract.  The replay tests feed REAL (redacted) gate-hub silo ledger rows
(premise cache ``~/.cache/gatehub-261015-premise/ledger/gate-hub/entries.jsonl``)
and REAL inter-round ``git diff -U0 -M`` texts, and assert the classes equal
values computed by the offline study's exact functions (ast-extracted verbatim
from ``collect_and_classify.py``, sha256 e892cccf83eb...) on these same rows:

    pr1446 r2 failure-state-overwritten              -> duplicate (match r1 l92;
        l98 is also on changed lines +-3, so duplicate beats on_diff for real)
    pr1441 r2 removes-attached-worktree              -> neither (untouched file;
        empty diff fixture = no consumed path has added lines)
    pr1447 r2 success_protocol_fields_unvalidated    -> no_anchor
    pr1447 r3 failure_protocol_fields_unvalidated    -> no_anchor

The offline gate-hub rows contain no on_diff finding, so the on_diff class is
locked by a SYNTHETIC unit test (clearly marked, repo name example/synthetic)
plus the real duplicate-on-changed-line case above.

Diff fixture texts are structure-preserving redactions: every added line is
kept as a line (placeholder content) inside a minimal hunk header carrying
the recomputed start, so ``parse_diff_added_lines`` returns identical line
sets versus the original diff (verified at fixture generation time and
end-to-end by these replay assertions).  Large transitions keep only the file
sections the asserted classifications consume (exact-path matches); dropped
sections cannot affect those classes (no basename-fallback hit in the full
diff, verified at generation time) and the replay assertions below still
equal the values computed on the full diffs.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "incremental_shadow.py"
FIXTURES = ROOT / "tests" / "fixtures" / "incremental_shadow"

_MODULE_NAME = "gate_incremental_shadow"


def _module():
    existing = sys.modules.get(_MODULE_NAME)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _f(file="a.py", line=10, severity="major", fid="F1", category="correctness", issue="bug"):
    return {"id": fid, "severity": severity, "file": file, "line": line, "category": category, "issue": issue}


def _row(run_id, recorded_at, head, status="fail", attempt=1, findings=(), pr=1):
    return {
        "repository": "zlxlabs/gate-hub",
        "pr_number": pr,
        "run_id": run_id,
        "run_attempt": attempt,
        "recorded_at": recorded_at,
        "head_sha": head,
        "review": {"status": status, "result": {"findings": list(findings)}},
    }


def _classify(prior_rows, findings, diff, *, head_sha="b" * 40, gate_result="fail", reason_code="primary_findings"):
    mod = _module()
    prior = mod.fold_canonical_rounds(prior_rows)
    return mod.classify_p1_findings(
        prior, findings, diff, head_sha=head_sha, gate_result=gate_result, reason_code=reason_code
    )


def test_parse_basic_multi_file():
    diff = (
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,2 +1,3 @@\n"
        " context\n"
        "-old\n"
        "+new\n"
        "+more\n"
        "diff --git a/y.py b/y.py\n"
        "+++ b/y.py\n"
        "@@ -0,0 +1,1 @@\n"
        "+added\n"
    )
    assert _module().parse_diff_added_lines(diff) == {"x.py": {2, 3}, "y.py": {1}}


def test_parse_rename_only_yields_no_lines():
    diff = (
        "diff --git a/old.py b/new.py\n"
        "similarity index 100%\n"
        "rename from old.py\n"
        "rename to new.py\n"
    )
    assert _module().parse_diff_added_lines(diff) == {}


def test_parse_rename_with_added_lines_uses_new_path():
    diff = (
        "diff --git a/old.py b/new.py\n"
        "similarity index 75%\n"
        "rename from old.py\n"
        "rename to new.py\n"
        "--- a/old.py\n"
        "+++ b/new.py\n"
        "@@ -2 +2,2 @@\n"
        "-old line\n"
        "+changed line\n"
        "+added line\n"
    )
    assert _module().parse_diff_added_lines(diff) == {"new.py": {2, 3}}


def test_parse_invalid_input():
    with pytest.raises(ValueError, match=r"^incremental_shadow_invalid_input: diff_text"):
        _module().parse_diff_added_lines(None)  # type: ignore[arg-type]


def test_fold_skips_non_eligible_and_headless_rows():
    rows = [
        _row(1, "2026-01-01T00:00:00+00:00", "a" * 40, status="not_applicable"),
        _row(2, "2026-01-02T00:00:00+00:00", "", status="fail"),
        _row(3, "2026-01-03T00:00:00+00:00", "b" * 40, status="pass"),
        _row(4, "2026-01-04T00:00:00+00:00", "c" * 40, status="fail"),
    ]
    rounds = _module().fold_canonical_rounds(rows)
    assert [r["head_sha"] for r in rounds] == ["b" * 40, "c" * 40]
    assert [r["round"] for r in rounds] == [1, 2]


def test_fold_collapses_same_head_to_latest_row():
    early = _row(1, "2026-01-01T00:00:00+00:00", "a" * 40, findings=[_f(fid="early")])
    late = _row(2, "2026-01-02T00:00:00+00:00", "a" * 40, findings=[_f(fid="late")])
    rounds = _module().fold_canonical_rounds([early, late, _row(3, "2026-01-03T00:00:00+00:00", "b" * 40)])
    assert len(rounds) == 2
    assert rounds[0]["findings"] == [_f(fid="late")]
    assert rounds[0]["run_id"] == 2


def test_fold_dedups_run_id_to_max_attempt():
    attempt1 = _row(7, "2026-01-01T00:00:00+00:00", "a" * 40, attempt=1, findings=[_f(fid="att1")])
    attempt2 = _row(7, "2026-01-02T00:00:00+00:00", "a" * 40, attempt=2, findings=[_f(fid="att2")])
    rounds = _module().fold_canonical_rounds([attempt1, attempt2])
    assert len(rounds) == 1
    assert rounds[0]["findings"] == [_f(fid="att2")]
    assert rounds[0]["run_attempt"] == 2


def test_fold_sorts_by_recorded_at_then_run_id():
    rows = [
        _row(20, "2026-01-01T00:00:00+00:00", "b" * 40),
        _row(10, "2026-01-01T00:00:00+00:00", "a" * 40),
    ]
    assert [r["head_sha"][0] for r in _module().fold_canonical_rounds(rows)] == ["a", "b"]


@pytest.mark.parametrize(
    "rows",
    [
        "not-a-list",
        [42],
        [_row(None, "t", "a" * 40)],  # eligible row without run_id
        [_row(1, "t", "a" * 40, attempt="2")],  # non-int run_attempt
        [_row(1, 5, "a" * 40)],  # non-string recorded_at
        [_row(1, "t", "a" * 40, findings="nope")],  # findings not a list
    ],
)
def test_fold_invalid_inputs(rows):
    with pytest.raises(ValueError, match=r"^incremental_shadow_invalid_input:"):
        _module().fold_canonical_rounds(rows)


def test_classify_no_anchor_wins_even_with_same_file_anchor():
    prior = [_row(1, "t1", "a" * 40, findings=[_f(line=10)])]
    payload = _classify(prior, [_f(line=None, fid="anchorless")], diff={}, head_sha="b" * 40)
    entry = payload["findings"][0]
    assert entry["line"] is None and entry["class"] == "no_anchor"
    assert payload["class_counts"]["no_anchor"] == 1


def test_classify_duplicate_beats_on_diff_and_survives_unavailable_diff():
    prior = [_row(1, "t1", "a" * 40, findings=[_f(file="x.py", line=10)])]
    current = [_f(file="x.py", line=20, fid="dup")]
    diff = {"x.py": {20, 21}}
    on_diff_too = _classify(prior, current, diff)
    assert on_diff_too["findings"][0]["class"] == "duplicate"
    assert on_diff_too["findings"][0]["match_round"] == 1
    assert on_diff_too["findings"][0]["match_line"] == 10
    unavailable = _classify(prior, current, None)
    assert unavailable["findings"][0]["class"] == "duplicate"
    assert unavailable["diff_status"] == "unavailable"


def test_classify_diff_unavailable_without_duplicate():
    prior = [_row(1, "t1", "a" * 40, findings=[_f(file="x.py", line=10)])]
    payload = _classify(prior, [_f(file="y.py", line=500, fid="solo")], None)
    assert payload["findings"][0]["class"] == "diff_unavailable"


def test_classify_on_diff_slop_boundaries():
    prior = [_row(1, "t1", "a" * 40, findings=[_f(file="far.py", line=1)])]
    diff = {"x.py": {100}}
    inside = _classify(prior, [_f(file="x.py", line=97, fid="in")], diff)
    outside = _classify(prior, [_f(file="x.py", line=104, fid="out")], diff)
    assert inside["findings"][0]["class"] == "on_diff"
    assert outside["findings"][0]["class"] == "neither"


def test_classify_basename_fallback_unique_and_ambiguous():
    prior = [_row(1, "t1", "a" * 40, findings=[_f(file="far.py", line=1)])]
    unique = _classify(prior, [_f(file="x.py", line=100, fid="fb")], {"src/x.py": {100}})
    assert unique["findings"][0]["class"] == "on_diff"
    ambiguous = _classify(prior, [_f(file="x.py", line=100, fid="fb")], {"a/x.py": {100}, "b/x.py": {100}})
    assert ambiguous["findings"][0]["class"] == "neither"


def test_classify_rename_only_diff_is_neither_not_on_diff():
    prior = [_row(1, "t1", "a" * 40, findings=[_f(file="far.py", line=1)])]
    rename_diff = "diff --git a/x.py b/renamed.py\nsimilarity index 100%\nrename from x.py\nrename to renamed.py\n"
    payload = _classify(prior, [_f(file="renamed.py", line=3, fid="moved")], _module().parse_diff_added_lines(rename_diff))
    assert payload["findings"][0]["class"] == "neither"


def test_classify_ignores_non_p1_and_non_string_severity():
    prior = [_row(1, "t1", "a" * 40, findings=[_f(file="far.py", line=1)])]
    findings = [_f(severity="minor", fid="m"), _f(severity=None, fid="n"), _f(fid="p1")]
    payload = _classify(prior, findings, {})
    assert [e["id"] for e in payload["findings"]] == ["p1"]


def test_classify_anchor_set_includes_non_p1_findings():
    prior = [_row(1, "t1", "a" * 40, findings=[_f(file="x.py", line=10, severity="nit", fid="nit")])]
    payload = _classify(prior, [_f(file="x.py", line=15, fid="p1")], None)
    assert payload["findings"][0]["class"] == "duplicate"


def test_classify_same_head_rerun_keeps_round_number_and_excludes_current_round():
    prior_rows = [_row(1, "t1", "a" * 40, findings=[_f(file="x.py", line=10)])]
    head = "b" * 40
    rerun_findings = [_f(file="y.py", line=99, fid="rerun")]
    payload = _classify(prior_rows, rerun_findings, {}, head_sha=head)
    assert payload["canonical_round"] == 2  # distinct head -> next round
    rerun_again = _classify(prior_rows + [_row(2, "t2", head, findings=rerun_findings)], rerun_findings, {}, head_sha=head)
    assert rerun_again["canonical_round"] == 2  # same head -> rerun of round 2
    entry = rerun_again["findings"][0]
    assert entry["class"] == "neither"  # own round's earlier attempt is not an anchor
    assert payload["prev_head_sha"] == "a" * 40
    assert rerun_again["prior_rounds_used"] == 2


def test_classify_payload_shape_class_counts_and_cap():
    prior = [_row(1, "t1", "a" * 40, findings=[_f(file="far.py", line=1)])]
    findings = [
        _f(file="n.py", line=i, fid=f"F{i}", category="c", issue="x") for i in range(51)
    ]
    findings[51 - 1] = _f(file=None, line=None, fid="F50")
    payload = _classify(prior, findings, {})
    assert payload["truncated"] is True
    assert len(payload["findings"]) == 50
    assert sum(payload["class_counts"].values()) == 51
    assert set(payload["class_counts"]) == {"no_anchor", "duplicate", "diff_unavailable", "on_diff", "neither"}
    assert set(payload) == {
        "canonical_round",
        "prev_head_sha",
        "head_sha",
        "prior_rounds_used",
        "diff_status",
        "findings",
        "truncated",
        "class_counts",
        "counterfactual",
    }
    assert set(payload["findings"][0]) == {
        "id", "severity", "file", "line", "class", "match_round", "match_line", "text_digest",
    }


@pytest.mark.parametrize(
    ("gate_result", "reason_code", "classes", "diff_status", "expected"),
    [
        ("pass", "primary_pass", ["neither"], "ok", "not_applicable"),
        ("fail", "disposition_resolved", ["neither"], "ok", "not_applicable"),
        ("fail", "primary_findings", ["neither", "no_anchor"], "ok", "pass"),
        ("fail", "primary_findings", ["on_diff"], "ok", "block"),
        ("fail", "primary_findings", ["duplicate"], "unavailable", "block"),
        ("fail", "primary_findings", ["neither"], "unavailable", "unclassified"),
        ("fail", "primary_findings", [], "unavailable", "unclassified"),
    ],
)
def test_compute_counterfactual_matrix(gate_result, reason_code, classes, diff_status, expected):
    assert _module().compute_counterfactual(gate_result, reason_code, classes, diff_status) == expected


def test_compute_counterfactual_rejects_unknown_class_and_status():
    mod = _module()
    with pytest.raises(ValueError, match=r"^incremental_shadow_invalid_input: classes"):
        mod.compute_counterfactual("fail", "primary_findings", ["bogus"], "ok")
    with pytest.raises(ValueError, match=r"^incremental_shadow_invalid_input: diff_status"):
        mod.compute_counterfactual("fail", "primary_findings", [], "maybe")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"prior_rounds": []},
        {"prior_rounds": "nope"},
        {"prior_rounds": [{"round": 1}]},  # missing head_sha/findings shape
        {"findings": {}},
        {"diff_lines": {"x.py": {"1"}}},  # non-int line numbers
        {"diff_lines": {7: {1}}},
    ],
)
def test_classify_invalid_inputs(kwargs):
    mod = _module()
    params = {
        "prior_rounds": mod.fold_canonical_rounds([_row(1, "t1", "a" * 40)]),
        "findings": [_f()],
        "diff_lines": None,
        "head_sha": "b" * 40,
        "gate_result": "fail",
        "reason_code": "primary_findings",
    }
    params.update(kwargs)
    with pytest.raises(ValueError, match=r"^incremental_shadow_invalid_input:"):
        mod.classify_p1_findings(**params)


def test_text_digest_is_stable_sixteen_hex_and_whitespace_normalized():
    mod = _module()
    digest = mod.text_digest("correctness", "line  has\ttabs\nand  spaces")
    assert digest == mod.text_digest("correctness", "line has tabs and spaces")
    assert len(digest) == 16 and int(digest, 16) >= 0
    assert digest != mod.text_digest("reliability", "line has tabs and spaces")
    assert mod.text_digest(None, None) == mod.text_digest("", "")


def test_text_digest_rejects_non_string():
    with pytest.raises(ValueError, match=r"^incremental_shadow_invalid_input: issue"):
        _module().text_digest("correctness", 42)


# --- replay against the real #1533 study (redacted real rows + real diffs) ---


def _replay_data():
    payload = json.loads((FIXTURES / "rows.json").read_text(encoding="utf-8"))
    diffs = {
        key: _module().parse_diff_added_lines((FIXTURES / name).read_text(encoding="utf-8"))
        for key, name in payload["diffs"].items()
    }
    return payload["pulls"], diffs


# (pr, round, finding id, expected class, match_round, match_line) — values
# computed by the offline study's exact functions on these same gate-hub rows
# (see report for the computation command and output).
_EXPECTED_CLASSES = [
    (1446, 2, "correctness-failure-state-overwritten", "duplicate", 1, 92),
    (1441, 2, "correctness-removes-attached-worktree", "neither", None, None),
    (1447, 2, "correctness_success_protocol_fields_unvalidated", "no_anchor", None, None),
    (1447, 3, "correctness_failure_protocol_fields_unvalidated", "no_anchor", None, None),
]


def test_replay_folded_rounds_match_study():
    pulls, _ = _replay_data()
    rounds1446 = _module().fold_canonical_rounds(pulls["1446"])
    rounds1441 = _module().fold_canonical_rounds(pulls["1441"])
    rounds1447 = _module().fold_canonical_rounds(pulls["1447"])
    assert [r["head_sha"][:8] for r in rounds1446] == ["c58425e7", "3cd42c1e", "507811bf"]
    assert [r["head_sha"][:8] for r in rounds1441] == ["58867216", "339807e2", "d759159a"]
    assert rounds1441[2]["findings"] == []  # passing round without findings
    assert [r["head_sha"][:8] for r in rounds1447] == ["a702784f", "4034cae7", "5ca15031", "2d93fab0"]
    # 三个 PR 均单 attempt：离线口径（无 attempt 去重）与模块口径轮序列一致。
    assert all(len({row["run_id"] for row in pulls[str(pr)]}) == len(pulls[str(pr)]) for pr in (1446, 1441, 1447))


def test_replay_classes_match_offline_loose_class():
    pulls, diffs = _replay_data()
    for pr, round_no, fid, expected_class, match_round, match_line in _EXPECTED_CLASSES:
        rounds = _module().fold_canonical_rounds(pulls[str(pr)])
        prior, current = rounds[: round_no - 1], rounds[round_no - 1]
        key = f"{prior[-1]['head_sha']}..{current['head_sha']}"
        payload = _module().classify_p1_findings(
            prior,
            current["findings"],
            diffs[key],
            head_sha=current["head_sha"],
            gate_result="fail",
            reason_code="primary_findings",
        )
        entry = next(e for e in payload["findings"] if e["id"] == fid)
        assert entry["class"] == expected_class, (pr, round_no, fid)
        assert entry["match_round"] == match_round
        assert entry["match_line"] == match_line
        assert len(entry["text_digest"]) == 16


def test_replay_counterfactual_on_real_rounds():
    pulls, diffs = _replay_data()
    rounds1446 = _module().fold_canonical_rounds(pulls["1446"])
    prior, current = rounds1446[:1], rounds1446[1]
    key = f"{prior[-1]['head_sha']}..{current['head_sha']}"
    payload = _module().classify_p1_findings(
        prior, current["findings"], diffs[key], head_sha=current["head_sha"],
        gate_result="fail", reason_code="primary_findings",
    )
    assert payload["counterfactual"] == "block"  # r2 was failed by a duplicate P1
    rounds1441 = _module().fold_canonical_rounds(pulls["1441"])
    prior, current = rounds1441[:1], rounds1441[1]
    key = f"{prior[-1]['head_sha']}..{current['head_sha']}"
    payload = _module().classify_p1_findings(
        prior, current["findings"], diffs[key], head_sha=current["head_sha"],
        gate_result="fail", reason_code="primary_findings",
    )
    assert payload["counterfactual"] == "pass"  # neither-only fail round: rule would flip it


def test_on_diff_synthetic_example_only():
    """SYNTHETIC on_diff lock (repo name example/synthetic, not real data).

    The real gate-hub ledger rows contain no on_diff finding (26 r>=2 P1
    classifications: 7 duplicate / 4 neither / 14 no_anchor / 1
    diff_unavailable / 0 on_diff), so this class is locked synthetically.
    """
    prior_rows = [_row(1, "t1", "a" * 40, findings=[_f(file="src/x.py", line=10)])]
    payload = _classify(
        prior_rows,
        [_f(file="src/x.py", line=100, fid="syn-on-diff")],
        {"src/x.py": {100}},
        head_sha="b" * 40,
    )
    assert payload["findings"][0]["class"] == "on_diff"
    assert payload["counterfactual"] == "block"
