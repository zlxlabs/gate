# Primary skip reason verdict contract

## Producer

`.github/actions/gate-aggregator/aggregate.py` emits one line to stdout for each normal `gate / gate` aggregation:

```text
AGENT-GATE-VERDICT-V1 {"classification":"expected_skip","draft":false,"gate_result":"skipped","primary":"skipped","reason_code":"review_not_expected","skip_reason":"review_exempt","v":1}
```

The prefix includes one trailing space. The JSON is compact, single-line, and sorted by key. The same line is appended to the Step Summary. The terminal envelope carries the same `skip_reason` value alongside its existing primary result, draft, classification, reason code, and gate result fields.

The aggregator prints exactly one marker from its shared finalization path. `primary` is `skipped` only when `needs.primary.result` is `skipped`; all other primary conclusions map to `executed`. `draft` is always a JSON Boolean copied from the pull request event.

## Fields and value domains

| Field | Values | Meaning |
| --- | --- | --- |
| `v` | `1` | Marker schema version. |
| `gate_result` | `pass`, `fail`, `skipped`, `unavailable` | Existing `GATE_RESULT_DOMAIN`. |
| `classification` | `code_pass`, `code_fail`, `expected_skip`, `review_unavailable`, `ci_failure`, `integration_error` | Existing `TERMINAL_CLASSIFICATION_DOMAIN`. |
| `reason_code` | Existing `TERMINAL_REASON_DOMAIN` | Existing terminal reason; this change adds no reason code. |
| `primary` | `executed`, `skipped` | Whether the job conclusion is the `skipped` state. |
| `skip_reason` | `draft`, `review_exempt`, `fork`, `hosted_runner`, `null` | Why a skipped primary is accepted, or null when there is no identified reason. |
| `draft` | JSON Boolean | Draft state from the pull request event, independent of `skip_reason`. |

`PRIMARY_SKIP_REASON_DOMAIN` contains exactly `draft`, `review_exempt`, `fork`, and `hosted_runner`. The independent decision precedence selects the first matching fact:

1. `draft`: event draft is true. The existing current-draft recheck still decides whether that skip can be accepted.
2. `fork`: head repository full name differs from the current repository.
3. `hosted_runner`: `inputs.runner` is not `self` (currently the valid other value is `hosted`).
4. `review_exempt`: `needs.classify_pr_paths.outputs.review_expected` is exactly the string `false`.

`gate-v2.yml` passes these facts into `Aggregate required verdict`. It does not alter the primary job's `if:` or the `REVIEW_EXPECTED` expression, and the aggregator does not call GitHub to reconstruct them.

The CLI defaults a missing fork input to false and a missing classify output to empty so callers that only aggregate an executed primary can continue to run. These defaults never establish a skip reason: fork and review-exempt require their explicit facts, and any skipped primary with no identified reason fails closed.

If primary is skipped and none of the four facts supplies a reason, the aggregator fails closed with `integration_error / unexpected_primary_skip`; its existing mapping yields `gate_result=unavailable`, never `skipped`. It emits `primary=skipped, skip_reason=null` to preserve the actual job conclusion. This fail-closed case is the necessary exception to a broad `primary=executed` iff `skip_reason=null` assertion: reporting `primary=executed` would falsify the observed skipped job.

## Consumer rule

The first consumer, agent-config `ci-watch`, may treat the primary skip as the review-exempt case only when `skip_reason` is `review_exempt` **and** `draft` is false. Other skip reasons do not relax that consumer's primary-skip handling. Missing or duplicate markers remain a consumer error; the producer test runs this script in a subprocess and checks the bytes written to stdout.
