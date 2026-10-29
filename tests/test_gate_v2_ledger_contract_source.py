"""gate-hub#1917 source provision: required quality carries the real
review-ledger consumer.

The consumer (gate-v2 workflow + review-ledger composite action + builder +
scrub) is published with the reusable workflow itself. The quality job must
materialize exactly those files from `job.workflow_sha` — never the caller
tree, never a moving ref — before `scripts/gate-quality` runs, and hand the
absolute runner.temp path over via GATE_LEDGER_CONSUMER_DIR without adding a
nested checkout to the caller workspace.

Failure marker: LEDGER-CONSUMER-WORKSPACE-CONTAMINATION
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.test_gate_source import CLIENT, CURL_WRAPPER, GIT_ENV, GIT_WRAPPER

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github/workflows/gate-v2.yml"
CHECKOUT_STEP_NAME = "Checkout review-ledger consumer source at this workflow's own commit"
QUALITY_STEP_NAME = "Run scripts/gate-quality"
CONSUMER_SUBDIR = "_gate-ledger-consumer-src"
CONSUMER_FILES = (
    ".github/workflows/gate-v2.yml",
    ".github/actions/review-ledger/action.yml",
    ".github/actions/review-ledger/build_ledger.py",
    "scripts/scrub_outbound.py",
)
BOOTSTRAP_CALL = 'bash -euo pipefail -c "$GATE_SOURCE_BOOTSTRAP_SCRIPT"'
CHECKOUT_HELPER_RUN = 'python3 "${RUNNER_TEMP}/gate_bounded_retry.py" checkout'


def _quality_steps() -> list[dict]:
    raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return raw["jobs"]["quality"]["steps"]


def _named(steps: list[dict], name: str) -> dict:
    return next(step for step in steps if step.get("name") == name)


def _sparse_paths(step: dict) -> list[str]:
    return [line.strip() for line in step["env"]["GATE_CHECKOUT_SPARSE"].splitlines() if line.strip()]


def _assert_contract(steps: list[dict]) -> None:
    """The whole source-provision contract, judged from the parsed workflow.

    Order comes from the step-list order (GitHub's real execution order), not
    from any string occurrence; every env pin is exact, not a substring.
    """
    names = [step.get("name") for step in steps]
    checkout = _named(steps, CHECKOUT_STEP_NAME)
    entry = _named(steps, QUALITY_STEP_NAME)
    assert names.index(CHECKOUT_STEP_NAME) < names.index(QUALITY_STEP_NAME)
    assert checkout["if"] == entry["if"] == "${{ steps.quality-entry.outputs.mode == 'entry' }}"
    env = checkout["env"]
    assert env["GATE_CHECKOUT_REPOSITORY"] == "${{ job.workflow_repository }}"
    assert env["GATE_CHECKOUT_REF"] == "${{ job.workflow_sha }}"
    assert env["GATE_CHECKOUT_PATH"] == CONSUMER_SUBDIR
    assert _sparse_paths(checkout) == list(CONSUMER_FILES)
    assert env["GATE_GITHUB_TOKEN"] == "${{ github.token }}"
    assert BOOTSTRAP_CALL in checkout["run"]
    assert CHECKOUT_HELPER_RUN in checkout["run"]
    root_export = 'export GITHUB_WORKSPACE="$RUNNER_TEMP"'
    assert root_export in checkout["run"]
    assert checkout["run"].index(root_export) < checkout["run"].index(BOOTSTRAP_CALL)
    assert "GITHUB_ENV" not in checkout["run"]
    assert "GITHUB_WORKSPACE" not in checkout["env"]
    assert "${{" not in checkout["run"]
    assert (entry.get("env") or {}).get("GATE_LEDGER_CONSUMER_DIR") == (
        f"${{{{ runner.temp }}}}/{CONSUMER_SUBDIR}"
    )
    assert "gate_run_and_record ./scripts/gate-quality" in entry["run"]
    assert entry["env"]["GATE_ARTIFACT_DIR"] == "${{ runner.temp }}/gate-quality"


def test_quality_provisions_the_consumer_before_the_quality_entry():
    _assert_contract(_quality_steps())


def test_consumer_files_are_published_in_this_repo():
    """The sparse list stays truthful: a rename here must update the workflow."""
    for relative in CONSUMER_FILES:
        assert (REPO_ROOT / relative).is_file(), relative


def test_pre_caller_cleanup_never_removes_the_consumer_dir():
    """The caller's same-named ordinary directory is not ours to remove."""
    steps = _quality_steps()
    stale = _named(steps, "Remove stale gate source directories")
    pre = _named(steps, "Remove gate action source before caller checks")
    assert CONSUMER_SUBDIR not in stale["run"]
    assert CONSUMER_SUBDIR not in pre["run"]


def _mutated(mutate) -> list[dict]:
    raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = raw["jobs"]["quality"]["steps"]
    mutate(steps)
    return steps


def _set_ref(steps: list[dict], expression: str) -> None:
    _named(steps, CHECKOUT_STEP_NAME)["env"]["GATE_CHECKOUT_REF"] = expression


def _drop_builder_from_sparse(steps: list[dict]) -> None:
    env = _named(steps, CHECKOUT_STEP_NAME)["env"]
    env["GATE_CHECKOUT_SPARSE"] = "\n".join(
        line for line in env["GATE_CHECKOUT_SPARSE"].splitlines() if "build_ledger" not in line
    ) + "\n"


def _move_after_quality(steps: list[dict]) -> None:
    index = next(i for i, step in enumerate(steps) if step.get("name") == CHECKOUT_STEP_NAME)
    steps.append(steps.pop(index))


MUTATIONS = {
    "ref-main": lambda steps: _set_ref(steps, "main"),
    "ref-caller-sha": lambda steps: _set_ref(steps, "${{ github.sha }}"),
    "ref-registry-commit": lambda steps: _set_ref(steps, "${{ inputs.registry_commit }}"),
    "repository-caller": lambda steps: _named(steps, CHECKOUT_STEP_NAME)["env"].__setitem__(
        "GATE_CHECKOUT_REPOSITORY", "${{ github.repository }}"
    ),
    "path-caller-tree": lambda steps: _named(steps, CHECKOUT_STEP_NAME)["env"].__setitem__(
        "GATE_CHECKOUT_PATH", "."
    ),
    "workspace-export-dropped": lambda steps: _named(steps, CHECKOUT_STEP_NAME).__setitem__(
        "run", _named(steps, CHECKOUT_STEP_NAME)["run"].replace(
            'export GITHUB_WORKSPACE="$RUNNER_TEMP"', ""
        )
    ),
    "env-caller-workspace": lambda steps: _named(steps, QUALITY_STEP_NAME)["env"].__setitem__(
        "GATE_LEDGER_CONSUMER_DIR", f"${{{{ github.workspace }}}}/{CONSUMER_SUBDIR}"
    ),
    "sparse-missing-file": _drop_builder_from_sparse,
    "env-dropped": lambda steps: _named(steps, QUALITY_STEP_NAME)["env"].pop("GATE_LEDGER_CONSUMER_DIR"),
    "env-relative-path": lambda steps: _named(steps, QUALITY_STEP_NAME)["env"].__setitem__(
        "GATE_LEDGER_CONSUMER_DIR", CONSUMER_SUBDIR
    ),
    "checkout-after-quality-entry": _move_after_quality,
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_every_deviation_from_the_consumer_contract_is_red(name: str):
    """Reverse proof: main / caller SHA / registry_commit / dropped sparse file /
    caller-controlled path / lost env / wrong order each trip the contract."""
    with pytest.raises(AssertionError):
        _assert_contract(_mutated(MUTATIONS[name]))


def _git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, env={**os.environ, **GIT_ENV},
        check=True, capture_output=True, text=True,
    )
    return completed.stdout.strip()


def _git_blob(*args: str) -> bytes:
    """Raw `git show` bytes — the payload comparison must be byte-exact."""
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, env={**os.environ, **GIT_ENV},
        check=True, capture_output=True,
    ).stdout


def _service_host(tmp_path: Path, head: str) -> dict:
    """Service-mode host carrying the published consumer snapshot.

    CI checkouts are shallow (actions/checkout fetch-depth 1), and git refuses
    to push/fetch a shallow closure into a full repository, so the fixture
    builds a parentless snapshot commit from the bytes of `git show HEAD:<file>`
    for exactly the files this chain consumes (the four consumer files plus the
    two bootstrap tools). Every snapshot file is byte-checked against the
    published HEAD blob, so the payload IS the released consumer; the snapshot
    SHA differs from `head` only by lineage, which the consumer chain never
    reads.
    """
    snapshot_files = sorted(set(CONSUMER_FILES) | {
        "scripts/gate_source.py", "scripts/gate_bounded_retry.py",
    })
    work = tmp_path / "consumer-snapshot"
    work.mkdir()
    _git("init", "--quiet", cwd=work)
    for relative in snapshot_files:
        target = work / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_git_blob("show", f"{head}:{relative}"))
    _git("add", "-A", cwd=work)
    _git("commit", "--quiet", "-m", "published consumer snapshot", cwd=work)
    snapshot_sha = _git("rev-parse", "HEAD", cwd=work)
    for relative in snapshot_files:
        assert (work / relative).read_bytes() == _git_blob("show", f"{head}:{relative}"), relative

    upstream = tmp_path / "upstream" / "zlxlabs" / "gate.git"
    upstream.parent.mkdir(parents=True)
    _git("init", "--bare", "--quiet", str(upstream))
    _git("-C", str(work), "push", "--quiet", str(upstream), f"{snapshot_sha}:refs/heads/main")
    mirror_root = tmp_path / "cache/git"
    mirror_root.mkdir(parents=True)
    (mirror_root / "SOURCE-MODE").write_text("service\n")
    client = mirror_root / "git-source-prepare"
    client.write_text(CLIENT)
    client.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").write_text(GIT_WRAPPER.format(real_git=shutil.which("git")))
    (bin_dir / "curl").write_text(CURL_WRAPPER)
    for name in ("git", "curl"):
        (bin_dir / name).chmod(0o755)
    argv_log = tmp_path / "argv.log"
    argv_log.touch()
    return {
        "upstream": upstream, "mirror_root": mirror_root, "bin": bin_dir,
        "argv_log": argv_log, "runner_temp": tmp_path / "runner-temp",
        "snapshot_sha": snapshot_sha,
    }


def test_consumer_step_materializes_the_published_files_at_workflow_sha(tmp_path):
    """E2E: the workflow's REAL step body (its own env and run are the producer)
    materializes the four consumer files from this repo's actual HEAD through
    the service mirror. The payload is the released consumer — bytes equal
    `git show HEAD:<file>`, nothing else is tracked, runner.temp residue is
    cleared, the caller tree is untouched, no credential is persisted, and no
    network is used.
    """
    head = _git("-C", str(REPO_ROOT), "rev-parse", "HEAD")
    host = _service_host(tmp_path, head)
    snapshot_sha = host["snapshot_sha"]
    workspace = tmp_path / "workspace"
    planted = workspace / CONSUMER_SUBDIR
    planted.mkdir(parents=True)
    (planted / "caller-planted.txt").write_text("caller tree\n", encoding="utf-8")
    caller_before = {
        str(p.relative_to(workspace)): p.read_bytes()
        for p in workspace.rglob("*") if p.is_file()
    }
    dest = host["runner_temp"] / CONSUMER_SUBDIR
    dest.mkdir(parents=True)
    (dest / "runner-residue.txt").write_text("stale consumer\n", encoding="utf-8")
    github_env = tmp_path / "github-env"
    github_env.write_bytes(b"")

    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    step = _named(_quality_steps(), CHECKOUT_STEP_NAME)
    env = os.environ.copy()
    env.update(GIT_ENV)
    env.update({
        "PATH": f"{host['bin']}{os.pathsep}{env['PATH']}",
        "GATE_ARGV_LOG": str(host["argv_log"]),
        "GATE_HUB_GIT_MIRROR_DIR": str(host["mirror_root"]),
        "GATE_FAKE_UPSTREAM": str(tmp_path / "upstream"),
        "RUNNER_TEMP": str(host["runner_temp"]),
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_ENV": str(github_env),
        # Resolved the way the runner resolves them: the two job-context
        # expressions to fixture values, everything else from the step itself.
        # The ref is the snapshot commit's real 40-hex SHA (byte-equal to the
        # published HEAD tree), so the step's pin and PIN-mismatch machinery
        # run against a true SHA exactly as in production.
        "GATE_CHECKOUT_REPOSITORY": "zlxlabs/gate",
        "GATE_CHECKOUT_REF": snapshot_sha,
        "GATE_CHECKOUT_PATH": step["env"]["GATE_CHECKOUT_PATH"],
        "GATE_CHECKOUT_SPARSE": step["env"]["GATE_CHECKOUT_SPARSE"],
        "GATE_GITHUB_TOKEN": "fixture-token",
        "GATE_SOURCE_BUDGET_SECS": workflow["env"]["GATE_SOURCE_BUDGET_SECS"],
        "GATE_SOURCE_DECIDE_SCRIPT": workflow["env"]["GATE_SOURCE_DECIDE_SCRIPT"],
        "GATE_SOURCE_PREPARE_SCRIPT": workflow["env"]["GATE_SOURCE_PREPARE_SCRIPT"],
        "GATE_SOURCE_BOOTSTRAP_SCRIPT": workflow["env"]["GATE_SOURCE_BOOTSTRAP_SCRIPT"],
    })
    host["runner_temp"].mkdir(parents=True, exist_ok=True)

    cleanup = _named(_quality_steps(), "Remove stale gate source directories")
    subprocess.run(
        ["bash", "-euo", "pipefail", "-c", cleanup["run"]],
        env=env, check=True, capture_output=True, text=True,
    )
    run = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step["run"]],
        cwd=workspace, env=env, capture_output=True, text=True, timeout=300,
    )
    assert run.returncode == 0, f"{run.stderr}\n{run.stdout}"

    assert not (dest / "runner-residue.txt").exists()
    assert github_env.read_bytes() == b""
    assert env["GITHUB_WORKSPACE"] == str(workspace)
    assert not (planted / ".git").exists()
    assert {
        str(p.relative_to(workspace)): p.read_bytes()
        for p in workspace.rglob("*") if p.is_file()
    } == caller_before
    entry = _named(_quality_steps(), QUALITY_STEP_NAME)
    consumer_dir = entry["env"]["GATE_LEDGER_CONSUMER_DIR"].replace(
        "${{ runner.temp }}", str(host["runner_temp"])
    )
    # Resolve the real quality-step env and hand it to a fresh caller subprocess.
    received = subprocess.run(
        ["bash", "-euo", "pipefail", "-c",
         'printf "%s\\n%s\\n" "$GITHUB_WORKSPACE" "$GATE_LEDGER_CONSUMER_DIR"'],
        cwd=workspace, env={**env, "GATE_LEDGER_CONSUMER_DIR": consumer_dir},
        check=True, capture_output=True, text=True,
    )
    assert received.stdout.splitlines() == [str(workspace), str(dest)]
    # The checkout pins the ref it was handed (PIN-mismatch guard ran) and the
    # materialized bytes are the PUBLISHED consumer, not the fixture's lineage.
    assert _git("rev-parse", "HEAD", cwd=dest) == snapshot_sha
    # Sparse checkout keeps the full index (`ls-files` over-reports); the DISK
    # is what the consumer reads, so the materialized set must be exactly the
    # four published files.
    materialized = sorted(
        str(path.relative_to(dest))
        for path in dest.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(dest).parts
    )
    assert materialized == sorted(CONSUMER_FILES)
    for relative in CONSUMER_FILES:
        assert (dest / relative).read_bytes() == _git_blob("show", f"{head}:{relative}"), relative
    extraheader = subprocess.run(
        ["git", "config", "--local", "--get", "http.https://github.com/.extraheader"],
        cwd=dest, env={**os.environ, **GIT_ENV}, capture_output=True, text=True,
    )
    assert extraheader.returncode != 0 and not extraheader.stdout.strip()
    assert "fixture-token" not in run.stdout + run.stderr
    argv = host["argv_log"].read_text(encoding="utf-8").splitlines()
    mirror = str(host["mirror_root"])
    assert not [
        line for line in argv
        if line.startswith("curl") or ("fetch" in line and mirror not in line)
    ]
