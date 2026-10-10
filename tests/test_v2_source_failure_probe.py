"""Judgement tests for scripts/v2_source_failure_probe.sh (gate-hub#1426).

The real pairing — candidate fragment x host-deployed client — is exercised
only on the self-hosted runner inside v2-tag-sync.  Here a fake client pins
the probe's own assertions: what counts as a pass, and that the pre-fix
fragment (gate d88d62cd) fails the probe instead of sailing through.
"""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBE = REPO_ROOT / "scripts" / "v2_source_failure_probe.sh"
PROBE_COMMIT = "0" * 39 + "1"

# Verbatim env.GATE_SOURCE_PREPARE_SCRIPT from gate d88d62cd: every client
# failure is validated against the reply contract first, and a real producer
# failure line (status "error") never matches it, so the fragment reports the
# blanket SOURCE-CLIENT-CONTRACT and swallows the real error code.
PRE_FIX_FRAGMENT = '''gate_source_prepare() {
    repository=${GATE_CHECKOUT_REPOSITORY:?}
    commit=${GATE_CHECKOUT_REF:?}
    if [ "${#commit}" -ne 40 ] || [[ "$commit" == *[!0-9a-f]* ]]; then echo "::error::SOURCE-COMMIT-INVALID: $commit" >&2; exit 1; fi
    case "$repository" in zlxlabs/*) ;; *) echo "::error::SOURCE-REPOSITORY-REJECTED: $repository" >&2; exit 1 ;; esac
    source_root=$(realpath -e "$source_root") || { echo "::error::SOURCE-MIRROR-UNREADABLE: $source_root" >&2; exit 1; }
    budget=${GATE_SOURCE_BUDGET_SECS:-180}
    deadline=$(( $(date +%s) + budget ))
    client="$source_root/git-source-prepare"
    [ -x "$client" ] || { echo "::error::SOURCE-CLIENT-MISSING: $client" >&2; exit 1; }
    client_raw=$("$client" --repository "$repository" --commit "$commit" --deadline-epoch "$deadline" 2>&1 >/dev/null) && client_status=0 || client_status=$?
    if ! printf '%s\\n' "$client_raw" | GATE_SOURCE_EXPECTED_REPOSITORY="$repository" GATE_SOURCE_EXPECTED_COMMIT="$commit" GATE_SOURCE_CLIENT_EXIT="$client_status" python3 -c '
import json, os, sys
marker = "GIT-SOURCE-PREPARE-V1 "
lines = [line[len(marker):] for line in sys.stdin.read().splitlines() if line.startswith(marker)]
try:
    if len(lines) != 1:
        raise ValueError("expected exactly one protocol line")
    payload = json.loads(lines[0])
    status = "ready" if os.environ["GATE_SOURCE_CLIENT_EXIT"] == "0" else "failed"
    if not isinstance(payload, dict) or payload.get("status") != status or payload.get("repository") != os.environ["GATE_SOURCE_EXPECTED_REPOSITORY"] or payload.get("commit_sha") != os.environ["GATE_SOURCE_EXPECTED_COMMIT"] or payload.get("source") not in ("hit", "cold"):
        raise ValueError("reply fields do not match the request")
except (json.JSONDecodeError, ValueError):
    raise SystemExit(1)
' ; then
      echo "::error::SOURCE-CLIENT-CONTRACT: invalid GIT-SOURCE-PREPARE-V1 reply" >&2
      exit 1
    fi
    client_line=$(printf '%s\\n' "$client_raw" | grep -m1 '^GIT-SOURCE-PREPARE-V1 ')
    [ "$client_status" -eq 0 ] || { echo "::error::SOURCE-CLIENT-FAILED: $client_line" >&2; exit 1; }
    prepare_source=$(printf '%s\\n' "$client_line" | python3 -c 'import json, sys; print(json.loads(sys.stdin.read().removeprefix("GIT-SOURCE-PREPARE-V1 "))["source"])')
}'''


def _current_fragment() -> str:
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "gate-v2.yml").read_text(encoding="utf-8")
    )
    return workflow["env"]["GATE_SOURCE_PREPARE_SCRIPT"]


def _write_fake_client(mirror: Path, *, exit_code: int, payload: dict | None) -> None:
    client = mirror / "git-source-prepare"
    body = "#!/usr/bin/env bash\n"
    if payload is not None:
        body += (
            "echo 'GIT-SOURCE-PREPARE-V1 "
            + json.dumps(payload, separators=(",", ":"))
            + "' >&2\n"
        )
    body += f"exit {exit_code}\n"
    client.write_text(body)
    client.chmod(client.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_probe(
    tmp_path: Path,
    fragment: str | None,
    *,
    client_exit: int = 4,
    client_payload: dict | None = "failure",
    mirror_set: bool = True,
    client_present: bool = True,
    cwd: Path | None = None,
    path_prepend: Path | None = None,
) -> subprocess.CompletedProcess:
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    if client_present:
        payload = client_payload
        if payload == "failure":
            payload = {
                "status": "error",
                "code": "SOURCE-PREPARE-FAILED",
                "repository": "zlxlabs/gate",
                "commit_sha": PROBE_COMMIT,
                "source": None,
                "elapsed_ms": 0,
            }
        _write_fake_client(mirror, exit_code=client_exit, payload=payload)
    if cwd is None:
        cwd = tmp_path / "tree"
        cwd.mkdir()
    env = dict(os.environ)
    if mirror_set:
        env["GATE_HUB_GIT_MIRROR_DIR"] = str(mirror)
    else:
        env.pop("GATE_HUB_GIT_MIRROR_DIR", None)
    if fragment is None:
        env.pop("GATE_SOURCE_PREPARE_SCRIPT_FRAGMENT", None)
    else:
        env["GATE_SOURCE_PREPARE_SCRIPT_FRAGMENT"] = fragment
    if path_prepend is not None:
        env["PATH"] = f"{path_prepend}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        ["bash", str(PROBE)], cwd=cwd, env=env, capture_output=True, text=True
    )


def test_probe_passes_when_fragment_surfaces_the_real_failure(tmp_path):
    result = _run_probe(tmp_path, _current_fragment())
    assert result.returncode == 0, result.stderr
    assert "SOURCE-PROBE-OK" in result.stdout
    assert "SOURCE-PREPARE-FAILED" in result.stdout


def test_probe_fails_on_the_pre_fix_fragment(tmp_path):
    result = _run_probe(tmp_path, PRE_FIX_FRAGMENT)
    assert result.returncode != 0
    assert "SOURCE-CLIENT-CONTRACT" in result.stderr
    assert "SOURCE-PROBE-ASSERT-FAILED" in result.stderr


def test_probe_fails_when_fragment_swallows_error_into_contract(tmp_path):
    bad_payload = {
        "status": "ready",
        "repository": "zlxlabs/gate",
        "commit_sha": PROBE_COMMIT,
        "source": "maybe",
        "elapsed_ms": 0,
    }
    result = _run_probe(tmp_path, _current_fragment(), client_exit=0, client_payload=bad_payload)
    assert result.returncode != 0
    assert "SOURCE-CLIENT-CONTRACT" in result.stderr


def test_probe_fails_when_client_unexpectedly_succeeds(tmp_path):
    ok_payload = {
        "status": "ready",
        "repository": "zlxlabs/gate",
        "commit_sha": PROBE_COMMIT,
        "source": "hit",
        "elapsed_ms": 0,
    }
    result = _run_probe(tmp_path, _current_fragment(), client_exit=0, client_payload=ok_payload)
    assert result.returncode != 0
    assert "SOURCE-PROBE-ASSERT-FAILED" in result.stderr


def test_probe_fails_when_failure_carries_no_protocol_line(tmp_path):
    result = _run_probe(tmp_path, _current_fragment(), client_exit=4, client_payload=None)
    assert result.returncode != 0
    assert "SOURCE-CLIENT-FAILED: exit=4" in result.stderr
    assert "SOURCE-PROBE-ASSERT-FAILED" in result.stderr


def test_probe_rejects_contract_even_beside_a_real_failure_annotation(tmp_path):
    # Isolates the SOURCE-CLIENT-CONTRACT ban: the other two assertions
    # (FAILED exit=, SOURCE-* code) are satisfied, so only the ban can fail.
    fragment = """gate_source_prepare() {
    printf '%s\\n' '::error::SOURCE-CLIENT-FAILED: exit=4 line=GIT-SOURCE-PREPARE-V1 {"status":"error","code":"SOURCE-PREPARE-FAILED"}' >&2
    printf '%s\\n' '::error::SOURCE-CLIENT-CONTRACT: stale blanket verdict' >&2
    exit 1
}"""
    result = _run_probe(tmp_path, fragment)
    assert result.returncode != 0
    assert "SOURCE-PROBE-ASSERT-FAILED" in result.stderr
    assert "SOURCE-CLIENT-CONTRACT" in result.stderr


def test_probe_requires_the_failed_exit_annotation_not_just_a_code_line(tmp_path):
    # Isolates the SOURCE-CLIENT-FAILED exit= requirement: a code-bearing
    # line alone (the client "succeeding" on a commit that cannot exist)
    # must not satisfy the probe.
    fragment = """gate_source_prepare() {
    printf '%s\\n' 'line=GIT-SOURCE-PREPARE-V1 {"status":"error","code":"SOURCE-SOMETHING"}' >&2
    exit 0
}"""
    result = _run_probe(tmp_path, fragment)
    assert result.returncode != 0
    assert "SOURCE-PROBE-ASSERT-FAILED" in result.stderr


def test_probe_fails_loud_when_mirror_env_is_unset(tmp_path):
    result = _run_probe(tmp_path, _current_fragment(), mirror_set=False, cwd=REPO_ROOT)
    assert result.returncode != 0
    assert "SOURCE-PROBE-MIRROR-UNSET" in result.stderr


def test_probe_fails_loud_when_host_client_is_missing(tmp_path):
    result = _run_probe(tmp_path, _current_fragment(), client_present=False, cwd=REPO_ROOT)
    assert result.returncode != 0
    assert "SOURCE-PROBE-CLIENT-MISSING" in result.stderr


def test_probe_fails_loud_when_fragment_was_not_supplied(tmp_path):
    # The fragment now travels from the sync job via env; the probe must
    # fail loud when it never arrives (extraction failed upstream).
    result = _run_probe(tmp_path, None)
    assert result.returncode != 0
    assert "SOURCE-PROBE-SCRIPT-EXTRACT-FAILED" in result.stderr


def _stdlib_only_python_shim(directory: Path) -> Path:
    """A python3 that forwards to the real interpreter with -I -S, so
    site-packages (PyYAML included) is never importable — the same
    environment as the self-hosted runner's system python3."""
    real = shutil.which("python3")
    assert real, "python3 must be on PATH to build the stdlib-only shim"
    directory.mkdir(parents=True, exist_ok=True)
    shim = directory / "python3"
    shim.write_text(f'#!/bin/sh\nexec "{real}" -I -S "$@"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return shim


def test_stdlib_only_shim_actually_hides_third_party_packages(tmp_path):
    # Anchor for the guard below: prove the shim really hides PyYAML before
    # trusting it to detect a third-party import inside the probe.
    shim = _stdlib_only_python_shim(tmp_path / "shim")
    hidden = subprocess.run(
        [str(shim), "-c", "import yaml"], capture_output=True, text=True
    )
    assert hidden.returncode != 0
    assert "ModuleNotFoundError" in hidden.stderr
    stdlib = subprocess.run(
        [str(shim), "-c", "import json, os, sys"], capture_output=True, text=True
    )
    assert stdlib.returncode == 0, stdlib.stderr


def test_probe_passes_with_stdlib_only_python(tmp_path):
    # The self-hosted runner's python3 has no PyYAML; the probe must not
    # import any third-party module.  Running it behind the -I -S shim turns
    # any such import into a ModuleNotFoundError and fails the probe.
    shim = _stdlib_only_python_shim(tmp_path / "shim")
    result = _run_probe(tmp_path, _current_fragment(), path_prepend=(tmp_path / "shim"))
    assert result.returncode == 0, result.stderr
    assert "SOURCE-PROBE-OK" in result.stdout
