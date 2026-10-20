# Producer fixture

`primary-audit-unavailable-malformed-v2.json` is the byte-for-byte output of gate-hub `build_primary_audit()` + `write_primary_audit_atomically()` (source `zlxlabs/gate-hub` commit `44e969faac26ea767d24ae71f58ae94c5a03a6e6`). Its producer identity includes all six aggregator convergence-scope fields; both review attempts failed, the first records one malformed finding dropped, and the canonical contract correctly emits `verdict=unavailable`, `result=null`.

The exact generation command and identities are recorded in the executor report. The consumer test derives the scope from these fields; scope is intentionally not injected into the producer's audit payload.
