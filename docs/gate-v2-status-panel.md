# Gate v2 status panel contract

Gate v2 publishes one aggregate status panel per pull request:

```text
<!-- gate-v2-status-panel:v1 -->
```

The publisher lists PR comments, finds this marker, and PATCHes the whole
body. If no marker exists it POSTs once. The body is a pure projection and is
never used as the history database.

History authority is the repository's `gate-terminal-v1-*` Actions artifacts.
The aggregator lists artifacts through the Actions API, filters each terminal
record by repository, repository id, and PR number, and adds the current
record before rendering. This includes prior head SHAs, so a new push adds a
row. If the panel comment is deleted, the next run rebuilds it from those
artifacts.

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

The OCR publisher follows the same find → PATCH / absent → POST rule. Delivery
failures are fail-open and recorded in the Step Summary plus the uploaded
delivery diagnostic/event artifact with HTTP status and permission category.
