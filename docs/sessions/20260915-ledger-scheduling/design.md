# Review ledger scheduling and time budget

## Context

The current released ledger job has a 3-minute total timeout. A real run using the published workflow spent about 130 seconds downloading inputs and was then cancelled at the job limit even though Build and Upload completed; the artifact existed, but the whole job was reported cancelled. Separately, the repository-wide concurrency group has one pending slot. A real sequence showed a no-step ledger job cancelled while another same-repository ledger was running, followed by a later pending ledger starting. The ledger producer writes a fresh file under `$RUNNER_TEMP` and uploads a run-specific artifact; it does not mutate a shared ledger file.

## Goal

Allow the ledger job enough total time for its measured input latency, and let each run produce its own artifact without replacing another run's pending ledger.

## Invariants

- Each run emits only its own ledger row and artifact.
- A ledger timeout must not cancel an otherwise healthy required gate.
- No repository-wide pending slot may discard another run's independent ledger artifact.
- Unknown or missing upstream result values remain fail-loud; this change does not reinterpret cancellation inputs.
- The released `v2` tag and downstream pins do not move as part of this code change.

## Decision

Set the ledger job timeout to 10 minutes, retaining the existing 1-minute Build step cap: 10 minutes gives roughly three times the full observed end-to-end job duration and more than four times the observed input download latency. Remove the job-level repository-wide concurrency group because the producer and artifact are run-scoped and no shared write needs serialization.

## Exclusions

Do not change primary/quality cancellation input handling (#160/#803), add retries, or promote/move the `v2` tag. A later release and caller rollout are separate work.

## Verification

Update the workflow contract tests to assert the actual producer YAML has the 10-minute job limit and no shared ledger concurrency group; keep the existing run-scoped artifact contract. Run the gate repository's focused workflow and ledger tests, then its required lint checks.
