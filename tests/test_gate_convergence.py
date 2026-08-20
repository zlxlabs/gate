"""Pure reducer contract tests for canonical clean-streak convergence.

Input credibility matrix (the dispatch report has the full table): state
None/non-state/tampered/legal -> fail-closed/fail-closed/fail-closed/replay;
processing keys with bad shape/old epoch/legal -> fail-closed/fail-closed/
new epoch/legal; audit digests conflicting/new/replayed -> fail-closed/new/
no-op; findings with missing, non-string, unknown, known non-P1, P1 severity
-> fail-closed/fail-closed/fail-closed/ignore/reset.
"""

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "convergence.py"


def _module():
    spec = importlib.util.spec_from_file_location("gate_convergence", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CONV = _module()


def _scope(*, tier="personal", infra_diff=False, **changes):
    effective = CONV._TIER_UPGRADE[tier] if infra_diff else tier
    values = dict(
        repository_id=123,
        pr_number=42,
        base_sha="b" * 40,
        head_sha="h" * 40,
        diff_digest="d" * 64,
        policy_version="policy-v1",
        policy_digest="p" * 64,
        tier=tier,
        effective_tier=effective,
        infra_classifier_version="infra-v1",
        infra_diff=infra_diff,
        caller_sha="c" * 40,
        reusable_workflow_sha="w" * 40,
    )
    values.update(changes)
    return CONV.Scope(**values)


SCOPE = _scope()


def _primary(scope=SCOPE, *, run_id=1, run_attempt=1, verdict="pass", p1_ids=()):
    return CONV.CanonicalPrimary(
        schema_version=1,
        repository_id=scope.repository_id,
        pr_number=scope.pr_number,
        head_sha=scope.head_sha,
        run_id=run_id,
        run_attempt=run_attempt,
        verdict=verdict,
        p1_ids=tuple(p1_ids),
    )


def _key(scope=SCOPE, *, run_id=1, run_attempt=1):
    return CONV.ProcessingKey(scope.repository_id, scope.pr_number, run_id, run_attempt)


def _round(state, scope=SCOPE, *, run_id=1, run_attempt=1, digest="1", verdict="pass", p1_ids=(), waiver=()):
    return CONV.evaluate_round(
        state=state,
        scope=scope,
        primary=_primary(scope, run_id=run_id, run_attempt=run_attempt, verdict=verdict, p1_ids=p1_ids),
        audit_digest=digest * 64 if len(digest) == 1 else digest,
        waiver_receipts=waiver,
        processing_key=_key(scope, run_id=run_id, run_attempt=run_attempt),
    )


def _receipt(scope=SCOPE, *, run_id=1, run_attempt=1, digest="1", verdict="pass", p1_ids=(), artifact=None, source_attempt=None, reported=None):
    audit_digest = digest * 64 if len(digest) == 1 else digest
    epoch = CONV.derive_epoch(scope)
    processing = _key(scope, run_id=run_id, run_attempt=run_attempt)
    round_key = CONV.RoundKey(epoch, run_id, audit_digest)
    artifact = artifact or f"primary-audit-{run_id}-{run_attempt}"
    receipt = CONV.Receipt(
        schema_version=1,
        scope=scope,
        epoch=epoch,
        processing_key=processing,
        round_key=round_key,
        event_id=CONV._event_id(epoch=epoch, run_id=run_id, run_attempt=run_attempt, audit_digest=audit_digest),
        run_id=run_id,
        run_attempt=run_attempt,
        audit_digest=audit_digest,
        verdict=verdict,
        p1_ids=tuple(p1_ids),
        source_attempt=run_attempt if source_attempt is None else source_attempt,
        artifact_id=artifact,
        reported_decision=reported,
    )
    return receipt


def test_receipt_for_round_copies_decision_identity_and_validates():
    primary = _primary(run_id=11, run_attempt=2, p1_ids=())
    decision = CONV.evaluate_round(
        state=CONV.initial_state(SCOPE),
        scope=SCOPE,
        primary=primary,
        audit_digest="a" * 64,
        waiver_receipts=(),
        processing_key=_key(run_id=11, run_attempt=2),
    )
    receipt = CONV.receipt_for_round(
        scope=SCOPE,
        primary=primary,
        audit_digest="a" * 64,
        decision=decision,
        source_attempt=1,
        artifact_id="artifact-123",
    )

    CONV.validate_receipt(receipt, SCOPE)
    assert receipt.event_id == decision.event_id
    assert receipt.processing_key == decision.processing_key
    assert receipt.round_key == decision.round_key
    assert receipt.schema_version == CONV.RECEIPT_SCHEMA_VERSION
    assert receipt.receipt_kind == CONV.RECEIPT_KIND
    assert receipt.source_attempt == 1
    assert receipt.artifact_id == "artifact-123"


def _disposition(scope=SCOPE, *, primary=None, audit_digest=None, **changes):
    primary = primary or _primary(scope, run_id=7, run_attempt=2, p1_ids=("p1",))
    audit_digest = audit_digest or "a" * 64
    receipt = CONV.DispositionReceipt(
        schema_version=1,
        disposition="false-positive",
        repository_id=str(scope.repository_id),
        pr_number=scope.pr_number,
        epoch=CONV.derive_epoch(scope),
        head_sha=scope.head_sha,
        audit_digest=audit_digest,
        finding_id=primary.p1_ids[0],
        reason="locked upstream behavior",
    )
    return replace(receipt, **changes)


def _state_for(name):
    state = CONV.initial_state(SCOPE)
    if name == "C":
        return state
    if name == "U":
        return _round(state, run_id=900, verdict="unavailable", digest="9").state
    if name == "T":
        return _round(state, run_id=900, digest="9").state
    if name == "M":
        for run_id in (900, 901, 902):
            state = _round(state, run_id=run_id, digest=str(run_id % 10), p1_ids=(f"f-{run_id}",)).state
        return state
    if name == "F":
        return _round(state, digest="not-a-digest").state
    raise AssertionError(name)


def test_derive_epoch_is_canonical_and_scope_changes_are_guards():
    assert CONV.derive_epoch(SCOPE) == CONV.derive_epoch(replace(SCOPE))
    assert CONV.derive_epoch(SCOPE) != CONV.derive_epoch(replace(SCOPE, head_sha="x" * 40))
    assert CONV.derive_epoch(SCOPE) != CONV.derive_epoch(replace(SCOPE, infra_diff=True, effective_tier="internal"))


def test_disposition_receipt_is_bound_to_the_current_round():
    primary = _primary(run_id=7, run_attempt=2, p1_ids=("p1",))
    receipt = _disposition(primary=primary)
    status = CONV.validate_disposition_receipt(
        receipt, scope=SCOPE, primary=primary, audit_digest="a" * 64,
    )
    assert (status.valid, status.active, status.reason) == (True, True, "active_false_positive")


def test_disposition_binding_rejects_head_epoch_digest_and_finding_mismatch():
    primary = _primary(run_id=7, run_attempt=2, p1_ids=("p1",))
    receipt = _disposition(primary=primary)
    changed_scope = replace(SCOPE, head_sha="x" * 40)
    assert CONV.validate_disposition_receipt(
        receipt, scope=changed_scope, primary=replace(primary, head_sha=changed_scope.head_sha),
        audit_digest="a" * 64,
    ).reason == "epoch_mismatch_stale"
    assert CONV.validate_disposition_receipt(
        receipt, scope=SCOPE, primary=primary, audit_digest="b" * 64,
    ).reason == "audit_digest_mismatch"
    assert CONV.validate_disposition_receipt(
        replace(receipt, finding_id="other"), scope=SCOPE, primary=primary, audit_digest="a" * 64,
    ).reason == "finding_not_current_p1"


def test_only_false_positive_resolves_matching_current_finding():
    primary = _primary(run_id=7, run_attempt=2, p1_ids=("a", "b"))
    receipt = _disposition(primary=primary)
    receipt = replace(receipt, finding_id="b")
    result = CONV.evaluate_round(
        state=CONV.initial_state(SCOPE), scope=SCOPE, primary=primary,
        audit_digest="a" * 64, waiver_receipts=(receipt,),
        processing_key=_key(run_id=7, run_attempt=2),
    )
    assert result.decision == "collecting" and result.clean_streak == 0
    assert result.state.event_records[-1][2][2] == ("a",)
    # A second receipt cannot clear the already-consumed round; a new primary
    # round with its own exact receipt can clear its only current P1.
    next_primary = _primary(run_id=8, run_attempt=1, p1_ids=("b",))
    next_receipt = _disposition(primary=next_primary, audit_digest="b" * 64, finding_id="b")
    next_result = CONV.evaluate_round(
        state=result.state, scope=SCOPE, primary=next_primary,
        audit_digest="b" * 64, waiver_receipts=(next_receipt,),
        processing_key=_key(run_id=8),
    )
    assert next_result.clean_streak == 1 and next_result.decision == "converged"


def test_rejected_disposition_cannot_advance_streak():
    primary = _primary(run_id=7, run_attempt=2, p1_ids=("p1",))
    for disposition in ("accepted", "wont-fix", "garbage", ""):
        receipt = _disposition(primary=primary, disposition=disposition)
        result = CONV.evaluate_round(
            state=CONV.initial_state(SCOPE), scope=SCOPE, primary=primary,
            audit_digest="a" * 64, waiver_receipts=(receipt,),
            processing_key=_key(run_id=7, run_attempt=2),
        )
        assert (
            result.clean_streak,
            result.eligible_rounds,
            result.decision,
            result.reason,
        ) == (0, 0, "fail_closed", "invalid disposition: unknown_disposition")

    empty_result = _round(
        CONV.initial_state(SCOPE),
        run_id=31,
        digest="3",
        p1_ids=("a",),
        waiver=(CONV.DispositionReceipt(),),
    )
    assert empty_result.clean_streak == 0


def test_duplicate_disposition_is_idempotent():
    primary = _primary(run_id=7, run_attempt=2, p1_ids=("p1",))
    receipt = _disposition(primary=primary)
    first = CONV.consume_dispositions(
        primary.p1_ids, (receipt,), scope=SCOPE, primary=primary,
        audit_digest="a" * 64,
    )
    replay = CONV.consume_dispositions(
        primary.p1_ids, (receipt, receipt), scope=SCOPE, primary=primary,
        audit_digest="a" * 64,
    )
    assert len(first.consumed_receipts) == 1 and not first.fail_closed
    assert len(replay.consumed_receipts) == 1 and not replay.fail_closed


def test_malformed_disposition_input_preserves_typed_rejection():
    primary = _primary(run_id=7, run_attempt=2, p1_ids=("p1",))
    malformed = {"finding_id": "p1"}
    result = CONV.consume_dispositions(
        primary.p1_ids, (malformed,), scope=SCOPE, primary=primary,
        audit_digest="a" * 64,
    )
    assert result.fail_closed
    assert result.rejected_receipts == ((CONV.DispositionReceipt(), "malformed_receipt"),)


def test_comment_alone_cannot_change_required_decision():
    primary = _primary(run_id=7, run_attempt=2, p1_ids=("p1",))
    result = CONV.evaluate_round(
        state=CONV.initial_state(SCOPE), scope=SCOPE, primary=primary,
        audit_digest="a" * 64, waiver_receipts=(),
        processing_key=_key(run_id=7, run_attempt=2),
    )
    assert result.decision == "collecting" and result.clean_streak == 0


@pytest.mark.parametrize(
    "tier,infra,effective,n,max_rounds",
    [
        ("personal", False, "personal", 1, 3),
        ("personal", True, "internal", 2, 5),
        ("internal", False, "internal", 2, 5),
        ("internal", True, "saas", 2, 8),
        ("saas", False, "saas", 2, 8),
        ("saas", True, "saas", 2, 8),
    ],
)
def test_policy_matrix_is_frozen(tier, infra, effective, n, max_rounds):
    scope = _scope(tier=tier, infra_diff=infra, effective_tier=effective)
    policy = CONV.policy_for(scope)
    assert (policy.effective_tier, policy.clean_rounds, policy.max_rounds, policy.unavailable_budget) == (effective, n, max_rounds, max_rounds)


@pytest.mark.parametrize("field", ["tier", "effective_tier"])
def test_unknown_tier_fails_closed(field):
    values = {field: "unknown"}
    if field == "effective_tier":
        values["tier"] = "personal"
    with pytest.raises(CONV.ScopeValidationError):
        CONV.validate_scope(replace(SCOPE, **values))


def test_invalid_policy_cap_fails_closed():
    original = CONV._POLICY_BY_TIER["personal"]
    CONV._POLICY_BY_TIER["personal"] = (0, 0)
    try:
        with pytest.raises(CONV.ConvergenceError):
            CONV.policy_for(SCOPE)
    finally:
        CONV._POLICY_BY_TIER["personal"] = original


def test_nonempty_p1_resets_streak_even_when_finding_ids_repeat():
    scope = _scope(tier="internal")
    state = _round(CONV.initial_state(scope), scope, run_id=1, digest="1").state
    result = _round(state, scope, run_id=2, digest="2", p1_ids=("same-finding",))
    assert (result.clean_streak, result.eligible_rounds, result.decision) == (0, 2, "collecting")


def test_clean_threshold_wins_over_max_rounds_on_same_event():
    scope = _scope(tier="internal")
    state = CONV.initial_state(scope)
    state = _round(state, scope, run_id=1, digest="1", p1_ids=("f",)).state
    state = _round(state, scope, run_id=2, digest="2", p1_ids=("f",)).state
    state = _round(state, scope, run_id=3, digest="3", p1_ids=("f",)).state
    state = _round(state, scope, run_id=4, digest="4").state
    result = _round(state, scope, run_id=5, digest="5")
    assert (result.clean_streak, result.eligible_rounds, result.decision) == (2, 5, "converged")


def test_unavailable_budget_is_independent_and_bounded():
    scope = _scope(tier="personal")
    state = CONV.initial_state(scope)
    state = _round(state, scope, run_id=1, verdict="unavailable", digest="1").state
    state = _round(state, scope, run_id=2, verdict="unavailable", digest="2").state
    assert (state.unavailable_streak, state.eligible_rounds, state.clean_streak, state.decision) == (2, 0, 0, "collecting")
    result = _round(state, scope, run_id=3, verdict="unavailable", digest="3")
    assert (result.unavailable_streak, result.eligible_rounds, result.decision) == (3, 0, "manual_required")


def test_scope_digest_change_starts_zero_generation():
    state = _round(CONV.initial_state(SCOPE), run_id=1, digest="1").state
    changed = replace(SCOPE, diff_digest="e" * 64)
    result = _round(state, changed, run_id=1, run_attempt=1, digest="2")
    assert result.state.epoch == CONV.derive_epoch(changed)
    assert (result.clean_streak, result.eligible_rounds) == (1, 1)


def test_duplicate_round_is_idempotent_and_conflicting_payload_fails_closed():
    state = CONV.initial_state(SCOPE)
    first = _round(state, run_id=10, run_attempt=1, digest="a")
    duplicate = _round(first.state, run_id=10, run_attempt=2, digest="a")
    assert duplicate.no_op and duplicate.state.as_dict() == first.state.as_dict()
    one = _receipt(run_id=11, run_attempt=1, digest="b")
    conflicting = _receipt(run_id=11, run_attempt=1, digest="b", p1_ids=("new",))
    replayed = CONV.replay_receipts(scope=SCOPE, receipts=[one, conflicting])
    assert replayed.decision == "fail_closed"


def test_same_processing_key_with_new_audit_fails_closed_without_counting():
    first = _round(CONV.initial_state(SCOPE), run_id=10, run_attempt=1, digest="a")
    conflict = _round(first.state, run_id=10, run_attempt=1, digest="b")
    assert (conflict.decision, conflict.accepted, conflict.no_op) == ("fail_closed", False, False)
    assert (conflict.state.clean_streak, conflict.state.eligible_rounds, conflict.state.unavailable_streak) == (first.clean_streak, first.eligible_rounds, first.unavailable_streak)


def test_tampered_derived_state_is_fail_closed():
    forged = replace(CONV.initial_state(SCOPE), clean_streak=1)
    result = _round(forged, run_id=11, digest="b")
    assert (result.decision, result.accepted, result.no_op) == ("fail_closed", False, False)
    assert result.state.clean_streak == 1


@pytest.mark.parametrize("bad_p1", [[["nested"]], {"nested": []}, [{"deep": {"value": []}}]])
def test_unhashable_p1_ids_are_controlled_fail_closed(bad_p1):
    state = CONV.initial_state(SCOPE)
    result = CONV.evaluate_round(
        state=state,
        scope=SCOPE,
        primary=_primary(run_id=12, p1_ids=(bad_p1,)),
        audit_digest="c" * 64,
        waiver_receipts=(),
        processing_key=_key(run_id=12),
    )
    assert (result.decision, result.accepted, result.no_op) == ("fail_closed", False, False)
    assert (result.state.clean_streak, result.state.eligible_rounds, result.state.unavailable_streak) == (0, 0, 0)


@pytest.mark.parametrize(
    "field,value",
    [
        ("processing_keys", ((1, 2, 3, 4), ("heterogeneous",))),
        ("round_keys", (("epoch", 1, "a" * 64), (1, "heterogeneous"))),
    ],
)
def test_heterogeneous_state_indexes_are_controlled_fail_closed(field, value):
    state = replace(CONV.initial_state(SCOPE), **{field: value})
    result = _round(state, run_id=13, digest="d")
    assert (result.decision, result.accepted, result.no_op) == ("fail_closed", False, False)
    assert (result.state.clean_streak, result.state.eligible_rounds, result.state.unavailable_streak) == (
        state.clean_streak,
        state.eligible_rounds,
        state.unavailable_streak,
    )


@pytest.mark.parametrize("bad_state,bad_key", [(None, _key()), (object(), _key()), (CONV.initial_state(SCOPE), object())])
def test_untrusted_round_inputs_are_controlled_fail_closed(bad_state, bad_key):
    result = CONV.evaluate_round(state=bad_state, scope=SCOPE, primary=_primary(), audit_digest="c" * 64, waiver_receipts=(), processing_key=bad_key)
    assert result.decision == "fail_closed" and not result.accepted and not result.no_op


def test_duplicate_status_rejects_untrusted_state_and_key_shapes():
    state = replace(CONV.initial_state(SCOPE), event_records=(("broken", "event", "fingerprint"),))
    assert CONV._duplicate_status(state, processing_key=object(), round_key=object(), event_id="", fingerprint="") == (False, True)


def test_manual_reinitialize_is_explicit_and_zero_based():
    manual = _state_for("M")
    changed = replace(SCOPE, head_sha="z" * 40)
    assert _round(manual, changed, run_id=99, digest="9").decision == "fail_closed"
    fresh = CONV.initial_state(changed)
    result = _round(fresh, changed, run_id=99, digest="9")
    assert (result.clean_streak, result.eligible_rounds, result.decision) == (1, 1, "converged")


def test_rerun_same_audit_is_not_a_second_round():
    first = _round(CONV.initial_state(SCOPE), run_id=20, run_attempt=1, digest="c")
    second = _round(first.state, run_id=20, run_attempt=2, digest="c")
    assert second.no_op
    assert second.state.eligible_rounds == first.state.eligible_rounds


def test_eligible_round_resets_unavailable_budget():
    state = _state_for("U")
    result = _round(state, run_id=901, digest="1", p1_ids=("blocked",))
    assert (result.unavailable_streak, result.eligible_rounds, result.clean_streak) == (0, 1, 0)


def test_clean_round_resets_unavailable_budget():
    result = _round(_state_for("U"), run_id=901, digest="1")
    assert (result.unavailable_streak, result.eligible_rounds, result.decision) == (0, 1, "converged")


def test_waiver_and_unavailable_counters_are_independent():
    result = _round(_state_for("U"), run_id=901, digest="1", p1_ids=("blocked",), waiver=(CONV.DispositionReceipt(),))
    assert (result.unavailable_streak, result.clean_streak, result.decision) == (0, 0, "collecting")


def test_partial_disposition_stays_blocked():
    result = _round(CONV.initial_state(SCOPE), run_id=30, digest="3", p1_ids=("a", "b"), waiver=(CONV.DispositionReceipt(),))
    assert (result.clean_streak, result.decision) == (0, "collecting")


def test_duplicate_unavailable_receipt_is_idempotent():
    receipt = _receipt(run_id=32, digest="3", verdict="unavailable")
    state = CONV.replay_receipts(scope=SCOPE, receipts=[receipt, receipt])
    assert (state.unavailable_streak, state.eligible_rounds) == (1, 0)


def test_new_epoch_drops_unavailable_history():
    changed = replace(SCOPE, head_sha="n" * 40)
    result = _round(_state_for("U"), changed, run_id=33, digest="3")
    assert (result.unavailable_streak, result.eligible_rounds, result.clean_streak) == (0, 1, 1)


def test_terminal_replay_with_new_finding_requires_manual():
    result = _round(_state_for("T"), run_id=34, digest="4", p1_ids=("new",))
    assert result.decision == "manual_required"


def test_terminal_replay_does_not_consume_round():
    state = _state_for("T")
    result = _round(state, run_id=900, digest="9")
    assert result.no_op and result.state.as_dict() == state.as_dict()


def test_terminal_replay_consumes_only_matching_disposition():
    result = _round(_state_for("T"), run_id=35, digest="5", p1_ids=("not-current",), waiver=(CONV.DispositionReceipt(),))
    assert result.decision == "manual_required"


def test_terminal_replay_rejects_invalid_disposition():
    result = _round(_state_for("T"), run_id=36, digest="6", p1_ids=("f",), waiver=(_disposition(primary=_primary(run_id=36, p1_ids=("f",)), disposition="accepted"),))
    assert result.decision == "fail_closed"


def test_converged_state_cannot_be_extended_by_rerun():
    state = _state_for("T")
    result = _round(state, run_id=900, run_attempt=2, digest="9")
    assert result.no_op and result.decision == "converged"


def test_converged_state_resets_on_head_change():
    changed = replace(SCOPE, head_sha="q" * 40)
    result = _round(_state_for("T"), changed, run_id=37, digest="7")
    assert result.state.epoch == CONV.derive_epoch(changed)
    assert (result.clean_streak, result.eligible_rounds) == (1, 1)


def test_manual_required_is_terminal_for_epoch():
    result = _round(_state_for("M"), run_id=38, digest="8")
    assert result.no_op and result.decision == "manual_required"


def test_manual_required_rejects_late_clean_round():
    result = _round(_state_for("M"), run_id=39, digest="9")
    assert result.state.clean_streak == _state_for("M").clean_streak


def test_manual_required_rejects_waiver_shortcut():
    result = _round(_state_for("M"), run_id=40, digest="a", waiver=(CONV.DispositionReceipt(),))
    assert result.decision == "manual_required"


def test_manual_required_is_idempotent():
    state = _state_for("M")
    one = _round(state, run_id=41, digest="a")
    two = _round(one.state, run_id=41, run_attempt=2, digest="a")
    assert two.no_op and two.state.as_dict() == one.state.as_dict()


def test_fail_closed_never_consumes_primary():
    state = _state_for("F")
    result = _round(state, run_id=42, digest="a")
    assert result.decision == "fail_closed" and result.state.eligible_rounds == state.eligible_rounds


def test_fail_closed_never_treats_missing_as_clean():
    result = _round(CONV.initial_state(SCOPE), run_id=43, digest="")
    assert result.decision == "fail_closed" and result.state.clean_streak == 0


def test_fail_closed_rejects_waiver():
    result = _round(_state_for("F"), run_id=44, digest="a", waiver=(_disposition(primary=_primary(run_id=44, p1_ids=("f",)), disposition="accepted"),))
    assert result.decision == "fail_closed"


def test_fail_closed_is_sticky_until_reinitialize():
    state = _state_for("F")
    result = _round(state, run_id=45, digest="a")
    assert result.decision == "fail_closed"
    assert _round(CONV.initial_state(SCOPE), run_id=45, digest="a").decision == "converged"


def test_fail_closed_replay_is_deterministic():
    bad = _receipt(run_id=46, digest="b", verdict="not_expected")
    first = CONV.replay_receipts(scope=SCOPE, receipts=[bad])
    second = CONV.replay_receipts(scope=SCOPE, receipts=[bad])
    assert first.as_dict() == second.as_dict()


def test_untrusted_state_cannot_auto_reset_on_new_head():
    changed = replace(SCOPE, head_sha="r" * 40)
    result = _round(_state_for("F"), changed, run_id=47, digest="b")
    assert result.decision == "fail_closed" and result.state.clean_streak == 0


def test_not_expected_and_reviewer_waived_never_count_clean():
    for verdict in ("not_expected", "waived"):
        result = _round(CONV.initial_state(SCOPE), run_id=48, digest="c", verdict=verdict)
        assert result.decision == "fail_closed"


def test_line_null_and_repeated_findings_remain_current_round_evidence():
    result = _round(CONV.initial_state(SCOPE), run_id=49, digest="d", p1_ids=("finding-line-null",))
    assert result.clean_streak == 0


def test_replay_ignores_reported_derived_counters():
    receipts = [_receipt(run_id=50, digest="e", reported="converged")]
    receipts[0] = replace(receipts[0], reported_clean_streak=999, reported_eligible_rounds=999)
    state = CONV.replay_receipts(scope=SCOPE, receipts=receipts)
    assert (state.clean_streak, state.eligible_rounds) == (1, 1)


def test_all_state_event_cells_are_callable():
    """Every cell asserts exact counters and flags; named tests add scenario detail."""
    cells = [
        (state, event)
        for state in "CUTMF"
        for event in ("major", "clean", "waiver_pass", "waiver_reject", "rerun", "new_digest")
    ]
    assert len(cells) == 30
    assert {(state, event) for state, event in cells} == set(cells)
    for state_name, event in cells:
        state = _state_for(state_name)
        waiver = (CONV.DispositionReceipt(),) if "waiver" in event else ()
        if event == "rerun":
            run_id, digest = (900, "9") if state_name in "UT" else ((902, "2") if state_name == "M" else (1, "1"))
            verdict = "unavailable" if state_name == "U" else "pass"
            rerun_p1 = ("f-902",) if state_name == "M" else ()
            first = _round(state, run_id=run_id, digest=digest, verdict=verdict, p1_ids=rerun_p1, waiver=waiver)
            result = _round(first.state, run_id=run_id, run_attempt=2, digest=digest, waiver=waiver)
        elif event == "new_digest":
            changed = replace(SCOPE, head_sha=f"{state_name.lower()}" * 40)
            result = _round(state, changed, run_id=800, digest="8", waiver=waiver)
        else:
            p1 = (f"{state_name}-finding",) if event in {"major", "waiver_pass", "waiver_reject"} else ()
            result = _round(state, run_id=800, digest="8", p1_ids=p1, waiver=waiver)
        expected = {
            "C": {"major": ("collecting", 0, 1, 0, True, False), "clean": ("converged", 1, 1, 0, True, False), "waiver_pass": ("collecting", 0, 1, 0, True, False), "waiver_reject": ("collecting", 0, 1, 0, True, False), "rerun": ("converged", 1, 1, 0, True, True), "new_digest": ("converged", 1, 1, 0, True, False)},
            "U": {"major": ("collecting", 0, 1, 0, True, False), "clean": ("converged", 1, 1, 0, True, False), "waiver_pass": ("collecting", 0, 1, 0, True, False), "waiver_reject": ("collecting", 0, 1, 0, True, False), "rerun": ("collecting", 0, 0, 1, True, True), "new_digest": ("converged", 1, 1, 0, True, False)},
            "T": {event: ("manual_required", 1, 1, 0, True, False) for event in ("major", "clean", "waiver_pass", "waiver_reject")},
            "M": {event: ("manual_required", 0, 3, 0, False, True) for event in ("major", "clean", "waiver_pass", "waiver_reject", "rerun")},
            "F": {event: ("fail_closed", 0, 0, 0, False, True) for event in ("major", "clean", "waiver_pass", "waiver_reject", "rerun")},
        }
        expected["T"].update({"rerun": ("converged", 1, 1, 0, True, True), "new_digest": ("converged", 1, 1, 0, True, False)})
        expected["M"].update({"rerun": ("manual_required", 0, 3, 0, True, True), "new_digest": ("fail_closed", 0, 3, 0, False, False)})
        expected["F"]["new_digest"] = ("fail_closed", 0, 0, 0, False, False)
        assert (result.decision, result.clean_streak, result.eligible_rounds, result.unavailable_streak, result.accepted, result.no_op) == expected[state_name][event]
