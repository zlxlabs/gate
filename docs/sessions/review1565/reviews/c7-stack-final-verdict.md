# C7 stacked PR final review verdict

failure-visibility: clean
reviewer: /root/c7_stack_review (新会话只读)
reviewed-range: `358a0caf7f5055c942e62f06222cd3d0970773bb..ec58e8efc09042d555ce39973ac4a14c9f71afcb`
OCR: skipped by task constraint; no model was called.
Validation: no new tests or red checks were run.

## Verdict

No finding could be substantiated in the frozen C7 stack. Under the existing explicit contract, the reviewed changes are non-blocking. This verdict records code and existing test evidence only; it does not claim a new test run.

The range covers the carry/readback step and the three later closure paths. Line references below refer to the frozen H0 source in this range. The cross-repository consumer was read from `zlxlabs/gate-hub` main at `1a51c18d4dfbed5e85d8c41e7ce343303258e24d`.

## Carry and closure evidence

### #342 — carry open findings

The gate workflow passes previous review context through `gate-v2.yml:1008-1058`; gate-hub's `scripts/review/review-primary:1380-1398` constructs `prior_review_context`. In `aggregate.py:960-1025`, the audit's `open_findings` is written from the relation; `aggregate.py:4111-4127` builds `outcome.finding_relation`, which reaches the terminal at `gate-v2.yml:1257-1258`, then the ledger and next-round loader.

Existing regression evidence is `test_silent_round_carries_open_finding_through_ledger_jsonl` (`tests/test_gate_aggregator.py:5522`): audit relation at `5648-5650`, terminal at `5678-5686`, and ledger/loader at `5723-5753`. Its silent-carry case at `5495-5496` expects `pass`. This locks preservation and visibility of carried facts; it does not require every open finding to block the gate. The explicit contract says carry/readback only (`docs/design/cross-round-finding-relation.md:3,9`; PR #342 body). Actual same-location opposing evidence is handled as conflict at `aggregate.py:4128-4132`, covered by `test_opposite_same_location_requirement_is_conflict_and_not_pass` (`tests/test_gate_aggregator.py:5172-5200`).

### #344 — consumed refutation closes one carry

`_close_consumed_false_positive_carry` removes a carry entry only after a valid refutation receipt was consumed and the recorded finding ID maps to the unique current P1 with the matching canonical key. Existing coverage is `test_real_disposition_producer_closes_only_consumed_current_carry`; unrelated IDs, invalid/deferred receipts, and unmatched carries remain open.

### #345 — explicit withdrawal

`aggregate.py:708-757,979-982` requires a reasoned withdrawal, an existing carried finding, and no current report of that finding before closure. The producer fixture/hash and end-to-end chain exercise the consumed producer result through aggregate, terminal, ledger, and next-round loading; a later report reopens the carry.

### #346 — removed quoted anchor

A carry closes only when the current primary review provides the exact file, line, and quote matching the old blob, and that old line was deleted or replaced. Unchanged lines are projected to their current line. Missing or unavailable Git evidence preserves carry. Existing coverage includes deleted-anchor, adjacent-insertion, and multi-round projection cases.

## Considered, not substantiated

- Withdrawal is consumed before audit identity validation, but the production caller derives identity from the workflow event through `PrimaryIdentity` and selects the artifact at the same coordinates; the model supplies only `result`. No reachable wrong-identity input was established.
- `history_status=unknown` reports that source history was unavailable, with the documented degradation log (`docs/design/cross-round-finding-relation.md:11,22`); new findings are still classified as new. The contract does not promise retention of history that could not be read.

No implementation or tests were changed for this review. OCR was intentionally skipped and no new test, model call, or red validation was performed.
