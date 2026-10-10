#!/usr/bin/env bash
# Failure-path probe for the v2 promotion gate (gate-hub#1426).  Runs the
# candidate commit's GATE_SOURCE_PREPARE_SCRIPT fragment against the
# host-deployed client, asking for a commit that cannot exist.  The fragment
# must surface the producer's real failure — a SOURCE-CLIENT-FAILED annotation
# carrying exit= and a SOURCE-* error code — instead of the pre-fix blanket
# SOURCE-CLIENT-CONTRACT.  Every probe-infrastructure problem is itself fatal
# with a SOURCE-PROBE-* literal: a green probe is the only condition under
# which v2-tag-sync moves the tag.
#
# The fragment is extracted from the candidate commit's gate-v2.yml by the
# GitHub-hosted sync job (which installs PyYAML) and passed in via the
# GATE_SOURCE_PREPARE_SCRIPT_FRAGMENT environment variable.  This script runs
# on the self-hosted runner, whose system python3 has no PyYAML, so every
# python here must import the standard library only.
set -euo pipefail

die() {
  echo "::error::$1" >&2
  exit 1
}

# --- probe infrastructure must exist before anything else runs ---

[ -n "${GATE_HUB_GIT_MIRROR_DIR:-}" ] ||
  die "SOURCE-PROBE-MIRROR-UNSET: GATE_HUB_GIT_MIRROR_DIR is not set in this runner environment"
[ -d "$GATE_HUB_GIT_MIRROR_DIR" ] ||
  die "SOURCE-PROBE-MIRROR-UNREADABLE: $GATE_HUB_GIT_MIRROR_DIR is not a directory"
host_client="$GATE_HUB_GIT_MIRROR_DIR/git-source-prepare"
[ -x "$host_client" ] ||
  die "SOURCE-PROBE-CLIENT-MISSING: $host_client is not an executable on this host"

fragment="${GATE_SOURCE_PREPARE_SCRIPT_FRAGMENT:-}"
[ -n "$fragment" ] ||
  die "SOURCE-PROBE-SCRIPT-EXTRACT-FAILED: probe fragment was not supplied (the sync job did not pass GATE_SOURCE_PREPARE_SCRIPT_FRAGMENT)"

# --- ask the real pairing for a commit that cannot exist ---

probe_output=$(
  {
    source_root=$GATE_HUB_GIT_MIRROR_DIR
    export GATE_CHECKOUT_REPOSITORY="zlxlabs/gate"
    export GATE_CHECKOUT_REF="0000000000000000000000000000000000000001"
    export GATE_SOURCE_BUDGET_SECS="${GATE_SOURCE_BUDGET_SECS:-30}"
    eval "$fragment"
    gate_source_prepare
  } 2>&1
) && probe_status=0 || probe_status=$?

# The captured output never reached the job log (command substitution);
# forward it so the fragment's annotation stays greppable either way.
printf '%s\n' "$probe_output" >&2

# --- the fragment must report the real failure, not the pre-fix blanket ---

verdict=$(PROBE_OUTPUT="$probe_output" PROBE_STATUS="$probe_status" python3 - <<'PY'
import json
import os

output = os.environ["PROBE_OUTPUT"]
problems = []
if "::error::SOURCE-CLIENT-FAILED: exit=" not in output:
    problems.append("no SOURCE-CLIENT-FAILED annotation carrying exit=")
if "SOURCE-CLIENT-CONTRACT" in output:
    problems.append("fragment emitted the pre-fix SOURCE-CLIENT-CONTRACT verdict")
codes = []
marker = "line=GIT-SOURCE-PREPARE-V1 "
for line in output.splitlines():
    if marker not in line:
        continue
    payload_text = line.split(marker, 1)[1]
    try:
        code = json.loads(payload_text).get("code")
    except json.JSONDecodeError:
        problems.append(f"protocol line payload is not JSON: {payload_text[:120]}")
        continue
    if isinstance(code, str) and code.startswith("SOURCE-"):
        codes.append(code)
    else:
        problems.append(f"protocol line code is not a SOURCE-* error code: {code!r}")
if not codes:
    problems.append("no protocol line carried a SOURCE-* producer error code")
if problems:
    print("SOURCE-PROBE-ASSERT-FAILED: " + "; ".join(problems))
    raise SystemExit(1)
print(
    "SOURCE-PROBE-OK: client exit "
    + os.environ["PROBE_STATUS"]
    + " surfaced producer codes "
    + ", ".join(sorted(set(codes)))
)
PY
) || die "$verdict"
echo "$verdict"
