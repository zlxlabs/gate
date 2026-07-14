# TODO

## Evaluate further Gate checkout acceleration

The shared Gate now uses a shallow checkout and fetches only the precise PR
base/head commits needed for diff measurement and Codex review. If checkout
remains a material part of CI latency, evaluate these next-level options:

- Audit Git-tracked binary assets and large test fixtures; keep only the small,
  required PR-test set in the normal checkout, and move optional or regenerable
  assets to a suitable artifact/object-storage flow.
- Evaluate a runner-local bare Git mirror/cache so ephemeral job workspaces can
  clone or fetch Git objects locally after the mirror receives incremental
  updates from GitHub.

Before implementing either option, capture checkout P50/P95 by repository and
verify that tests and Codex still receive every file and exact base/head commits
they require. Do not use sparse checkout for the general Gate unless each
consumer's required paths are explicitly proven.
