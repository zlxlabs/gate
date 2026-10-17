# Gate PR335 A1c diagnostic archive review verdict

**Review target:** `82c2a68231569e2774cddc052ddfccf5baaf9f78..4c72009744af56b7e0cf8b5ba3ecc16dce161108` (fixed H0)  
**Risk tier:** personal  
**Verdict:** pass with two nonblocking P2 findings.  
**Delivery blocked:** no.

failure-visibility: p2-only

## Findings

### P2 — advisory archive accepts names the producer cannot emit

**Violates invariant 2.** `scripts/silo_store.py:69` accepts reviewer segments with uppercase letters, dots, or a non-letter first character. The actual producer rejects those via `.github/workflows/gate-v2.yml:1345` (`^[a-z][a-z0-9_-]*$`). A fixed-H0 in-memory Silo probe passed `advisory-event-UPPER-123-0` to `put-dir --tier archive` and wrote both the source file and generated manifest. This lets the archive tier persist an artifact outside the specified producer namespace. It does not affect the required gate verdict; nonblocking P2.

### P2 — status-panel archive warns when its producer was skipped

**Violates invariant 3.** `Publish gate status panel` runs only if a terminal upload succeeds (`.github/workflows/gate-v2.yml:2145`), but the new archive step uses `if: always()` (`:2203`). If both terminal uploads fail, the producer is skipped and `PANEL_PATH` is absent; the archive step then logs “Permanent archive ... failed.” The following existing warning already reports the diagnostic as unavailable. This adds a misleading duplicate archive-failure warning for an ineligible output; it does not change the gate result. Nonblocking P2.

## Invariant check

- **1 — source preservation:** all four real producer paths are wired: primary diagnostics, advisory event directory, convergence receipt directory, and the exact status-panel JSON. Existing d3 uploads remain. The H0 workflow-shell test compared source bytes, PUT/GET keys, and generated directory-manifest entries.
- **2 — names, collision, readback:** directory writes retain all enumerated source files and add a separate SHA-256/byte-count manifest; a root source collision with that manifest and an occupied prefix fail before PUT. New archive objects are read back and byte-checked. The advisory-name allowlist gap is finding 1.
- **3 — qualification and failure visibility:** primary empty output skips; advisory and convergence keep their producer eligibility; convergence archives only when its receipt is present. Archive failures emit warnings and are `continue-on-error`; the advisory comment step precedes its archive step. The status-panel absence case is finding 2.
- **4 — prohibited changes:** the increment adds no deletion, production write, IAM change, retry, or background mechanism. It does not depend on PR334’s GNU-timeout fix.

## Verification

- `git diff --check 82c2a68231569e2774cddc052ddfccf5baaf9f78..4c72009744af56b7e0cf8b5ba3ecc16dce161108` — exit 0.
- Fixed-H0 scratch worktree: `uv run --python 3.12 --with pytest,PyYAML python -m pytest -q tests/test_silo_store.py tests/test_gate_v2_diagnostics_upload.py` — **48 passed in 15.35s**. Tests used fake Silo state; no production S3 writes or model calls.
- Fixed-H0 in-memory probe: `advisory-event-UPPER-123-0` was accepted and archived; output: `nonproducer_name_archived=True real_s3_writes=0 source_bytes_preserved=True`.
- OCR preflight: final envelope `status=reviewed_fallback`, `cli_status=complete`, DeepSeek backup after primary `leg_timeout` at 900s; 6 candidates, 5 confirmed and 1 refuted by its verifier. Only candidates independently mapped to invariants are findings above.
- CI baseline was unavailable at dispatch (`gh api request failed`); inherited-red status is **undetermined**. These local narrow tests were green; no CI run-level conclusion was collected.
- Added verdict budget: target 80 lines, hard limit 120; count checked after staging.
