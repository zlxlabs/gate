# Gate v2 status panel contract

Gate v2 publishes one aggregate status panel per pull request:

```text
<!-- gate-v2-status-panel:v1 -->
```

The publisher obtains its own GitHub API identity from `/user`, lists all PR
comment pages, and considers a comment owned only when both the marker and the
API author identity match. When `/user` returns HTTP 403 or 404 for an Actions
installation token, identity resolution falls back to the documented
`github-actions[bot]` / user id `41898282` pair. No other identity error enters
that fallback: 5xx, network, and invalid-response failures remain fail-open
diagnostics with `operation=IDENTITY`. The receipt records
`identity_source=user_api` or `identity_source=actions_bot_fallback`.

For ownership matching, a comment author id takes precedence; the login is
used only when the comment has no id. It PATCHes the earliest own marker
comment; a non-own comment is never modified. If no own marker exists it POSTs once, then
re-lists own markers, PATCHes the earliest one, and deletes later own
duplicates. This closes the local POST race while the gate job's per-PR
concurrency group serializes normal runs. It uses only GitHub's supported
`group` and `cancel-in-progress: false` keys. GitHub's default concurrency
queue keeps one run running and at most one pending; when a newer run arrives,
an older pending run may be canceled. A canceled intermediate run reaches no
panel publish step, and the surviving run's idempotent find/PATCH/POST and
POST self-healing still enforce the one-comment invariant.

The body is a pure projection and is never used as the history database. The
marker is `gate-v2-status-panel:v1`; it is not an ownership credential.

History authority is the repository's `gate-terminal-v1-*` Actions artifacts.
The aggregator lists artifacts through the Actions API, filters each terminal
record by repository, repository id, and PR number, and adds the current
record before rendering. Each malformed, mismatched, or expired record is
skipped independently and counted in the receipt/Step Summary. This includes
prior head SHAs, so a new push adds a row. If an existing own panel is
available, its parseable history rows are unioned with artifact rows by
`run_id + run_attempt`; this is a cache/retention fallback, not a replacement
for the artifact authority. If either source is incomplete, the public panel
states `历史可能不完整（原因）` and the receipt carries the per-record
diagnostic. If the panel is deleted, the next run rebuilds from artifacts and
therefore does not depend on the deleted body.

The aggregate step writes `gate-terminal.json` first. The terminal artifact is
uploaded before the independent `--publish-only` step can PATCH/POST the
panel; an upload failure therefore suppresses the panel update for that run.

Each rendered history row has schema version `1` and these fields:

`schema_version`, `repository`, `run_id`, `run_attempt`, `head_sha`,
`gate_result`, `classification`, `reason_code`.

`gate_result` is the aggregate's existing finite domain: `pass`, `fail`,
`skipped`, and `unavailable`. The explicit recipient buckets are respectively
`可合并`, `要修代码`, `无需动作`, and `修基础设施`. `skipped` also renders
`主审未跑，绿≠过审`.

OCR advisory comments use one marker per reviewer:

```text
<!-- gate-v2-ocr-advisory:<reviewer>:v1 -->
```

The OCR publisher follows the same own-author, all-pages, earliest-marker,
find → PATCH / absent → POST rule, including POST verification and duplicate
self-healing. It uses the same `/user` 403/404 Actions-bot fallback and
`identity_source` field. Delivery failures are fail-open and recorded in the
Step Summary plus the uploaded delivery diagnostic/event artifact with HTTP
status and permission category.
