# Counterevidence argv contract v1

`counterevidence_argv.py` exports `counterevidence_argv_reason(argv, head_sha)` and
`COUNTEREVIDENCE_ARGV_CONTRACT_VERSION == 1`. It checks allowlisted
`git grep -n -F -e <literal> <head_sha> -- <pathspec>` syntax and head SHA
equality only; it never executes Git or performs I/O. Adjacent
`counterevidence_argv_v1_vectors.json` contains v1 accepted and rejection cases.

Pin the module and JSON to the same immutable `zlxlabs/gate` commit SHA, not
`@v2`. Load the module with `importlib.util.spec_from_file_location`, read the
adjacent JSON, and compare `contract_version` to the exported constant before
use. This takes no network request or CLI execution.

A vector with `reason_detail: null` expects Python `None`; otherwise expect
`reason_code + ": " + reason_detail + "; " + COUNTEREVIDENCE_ARGV_TEMPLATE`
exactly.

The aggregator and `.github/actions/gate-disposition/issue_receipt.py` load via
`convergence.py`; both sparse checkouts include the module. `agent-config#3761`
is a follow-up; agent-config is not yet integrated and must pin both files to
the same SHA.
