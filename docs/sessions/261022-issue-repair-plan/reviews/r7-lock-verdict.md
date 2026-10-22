# R7: checkout lock wait test removal

failure-visibility: clean
risk-tier: personal
reviewed-range: f144ece04a1c26e9b63dde8409ab403e0f026dcc..b0769c25f1f5c9e45c7256f9a0af3259a43d43c3

Root review of the complete deletion diff found no P1 or blocking issue. The implementation withdrew the proposed synchronization mechanism and deletes only the 31-line wall-clock test. The removed fixed 1.5-second holder and `consume_lock_wait_ms >= 900` threshold measured scheduler load. Remaining tests preserve the real JSON nonnegative timing-field assertion, the client-before-consumer lock-order probe, and the workflow shared-flock call contract. They do not claim to verify actual blocking duration under contention.

Validation: `tests/test_gate_checkout_mirror.py` passed (17 tests). A local negative control that removed the workflow shared-flock call made the existing wiring-contract test fail; the injection was restored. OCR status was `skipped` (`no_reviewable_items`, coverage `none`); no clean OCR result is claimed.
