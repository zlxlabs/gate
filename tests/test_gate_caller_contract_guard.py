"""Compatibility guard between the moving v2 tag and the current workflows."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATHS = (
    ".github/workflows/gate-v2.yml",
    ".github/workflows/gate-shadow-v2.yml",
    ".github/workflows/gate.yml",
)
PERMISSION_SCOPES = (
    "actions",
    "attestations",
    "checks",
    "contents",
    "deployments",
    "discussions",
    "id-token",
    "issues",
    "models",
    "packages",
    "pages",
    "pull-requests",
    "security-events",
    "statuses",
)


def _workflow_call(document):
    trigger = document.get("on", document.get(True, {}))
    return trigger.get("workflow_call", {})


def _required(value) -> bool:
    return bool(value.get("required", False)) if isinstance(value, dict) else False


def _signature(document):
    call = _workflow_call(document)
    inputs = call.get("inputs", {}) or {}
    secrets = call.get("secrets", {}) or {}
    return (
        {name: _required(spec) for name, spec in inputs.items()},
        {name: _required(spec) for name, spec in secrets.items()},
    )


def _permission_level(value) -> int:
    return {"none": 0, "read": 1, "write": 2}.get(value, 0)


def _permission_map(declaration) -> dict[str, int]:
    if isinstance(declaration, str):
        level = {"none": 0, "read-all": 1, "write-all": 2}.get(declaration)
        if level is not None:
            return {scope: level for scope in PERMISSION_SCOPES}
    if isinstance(declaration, dict):
        return {scope: _permission_level(declaration.get(scope, "none")) for scope in declaration}
    return {}


def _permission_declarations(document):
    declarations = {}
    if "permissions" in document:
        declarations["workflow"] = document["permissions"]
    for job_name, job in (document.get("jobs", {}) or {}).items():
        if "permissions" in job:
            declarations[f"job:{job_name}"] = job["permissions"]
    return declarations


def contract_violations(baseline, current) -> list[str]:
    violations = []
    old_inputs, old_secrets = _signature(baseline)
    new_inputs, new_secrets = _signature(current)
    for name, required in old_inputs.items():
        if name not in new_inputs:
            violations.append(f"input removed: {name}")
        elif not required and new_inputs[name]:
            violations.append(f"input became required: {name}")
    for name, required in old_secrets.items():
        if name not in new_secrets:
            violations.append(f"secret removed: {name}")
        elif not required and new_secrets[name]:
            violations.append(f"secret became required: {name}")
    violations.extend(
        f"required input added: {name}"
        for name, required in new_inputs.items()
        if name not in old_inputs and required
    )
    violations.extend(
        f"required secret added: {name}"
        for name, required in new_secrets.items()
        if name not in old_secrets and required
    )
    old_permissions = _permission_declarations(baseline)
    new_permissions = _permission_declarations(current)
    for location, old_decl in old_permissions.items():
        if location not in new_permissions and any(_permission_map(old_decl).values()):
            violations.append(f"permissions declaration removed at {location}")
    for location, current_decl in new_permissions.items():
        old_decl = old_permissions.get(location, {})
        old_map = _permission_map(old_decl)
        new_map = _permission_map(current_decl)
        for scope, level in new_map.items():
            if level > old_map.get(scope, 0):
                violations.append(
                    f"permission expanded at {location}: {scope} "
                    f"{old_map.get(scope, 0)}->{level}"
                )
    return violations


def _tag_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "refs/tags/v2^{commit}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("refs/tags/v2 is unavailable; CI must fetch tags for this guard")
    return result.stdout.strip()


def _load_at_ref(ref: str, path: str):
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"cannot read {path} at {ref}: {result.stderr.strip()}")
    return yaml.safe_load(result.stdout)


def test_v2_contract_does_not_break_existing_callers():
    tag_commit = _tag_commit()
    failures = []
    for path in WORKFLOW_PATHS:
        baseline = _load_at_ref(tag_commit, path)
        current = yaml.safe_load((REPO_ROOT / path).read_text())
        failures.extend(f"{path}: {item}" for item in contract_violations(baseline, current))
    assert not failures, "v2 caller contract is not backward compatible:\n" + "\n".join(failures)


def test_contract_guard_rejects_removed_input():
    tag_commit = _tag_commit()
    baseline = _load_at_ref(tag_commit, WORKFLOW_PATHS[0])
    current = yaml.safe_load((REPO_ROOT / WORKFLOW_PATHS[0]).read_text())
    current_call = _workflow_call(current)
    del current_call["inputs"]["tier"]
    assert "input removed: tier" in contract_violations(baseline, current)


def test_contract_guard_rejects_permission_expansion():
    tag_commit = _tag_commit()
    baseline = _load_at_ref(tag_commit, WORKFLOW_PATHS[0])
    current = yaml.safe_load((REPO_ROOT / WORKFLOW_PATHS[0]).read_text())
    current["permissions"]["contents"] = "write"
    assert "permission expanded at workflow: contents 1->2" in contract_violations(
        baseline, current
    )
