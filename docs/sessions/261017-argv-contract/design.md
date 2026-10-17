# Counterevidence argv contract v1

`counterevidence_argv.py` exports `counterevidence_argv_reason(argv, head_sha)`
and `COUNTEREVIDENCE_ARGV_CONTRACT_VERSION == 1`. It validates only the
allowlisted `git grep -n -F -e <literal> <head_sha> -- <pathspec>` syntax and
head SHA equality; it never executes Git or performs I/O. The adjacent
`counterevidence_argv_v1_vectors.json` carries accepted argv and rejection
code/detail cases for this version.

Consumers pin the module and JSON vectors to the same immutable `zlxlabs/gate`
commit SHA, not the moving `@v2` tag. Load the module with
`importlib.util.spec_from_file_location` and read the adjacent JSON in that
same checkout; check its `contract_version` against the module constant before
using cases. This requires no network request or CLI execution.

The gate aggregator and `.github/actions/gate-disposition/issue_receipt.py`
both load the contract through `convergence.py`; their sparse checkouts include
the module. `zlxlabs/agent-config` is not yet integrated; its follow-up is
agent-config#3761 and must pin the same gate SHA for both files.
