"""Contract tests for the quality_prepare_mise opt-in bootstrap (gate#314).

Evidence: zlxlabs/herdweb actions run 38427594289 — `gate / quality` died with
`./scripts/gate-quality: 9: mise: not found` (exit 127): the shared quality job
had no way to prepare the tool manager the business entry assumes, and a
workflow_call caller cannot inject its own setup step into a reusable job.
These tests read the REAL producer workflow callers consume via @v2 — never a
hand-written fixture.

改坏 .github/workflows/gate-v2.yml（input 缺失/类型/默认值漂移、entry guard 移除、
install 漂移、步骤换位、吞错、legacy 路径误触）会红；对应卡面不变式：仅 entry
模式 + caller 显式 opt-in 才准备 mise；gate 只引导管理器本身（install: false）；
准备失败直接阻断业务入口。
"""
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"

MISE_INPUT = "quality_prepare_mise"
MISE_STEP_NAME = "Prepare mise tool manager (quality entry opt-in)"
DETECT_STEP_NAME = "Detect repository quality entry"
BUSINESS_ENTRY_RUN = "./scripts/gate-quality"


def _load_gate_v2():
    raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return raw


def _quality_steps(raw):
    return raw["jobs"]["quality"]["steps"]


def _mise_step(raw):
    """Return the single mise bootstrap step, with an explicit contract failure."""
    steps = _quality_steps(raw)
    matches = [s for s in steps if s.get("name") == MISE_STEP_NAME]
    assert len(matches) == 1, (
        f"jobs.quality.steps must declare exactly one {MISE_STEP_NAME!r} step; "
        f"found {len(matches)}"
    )
    return matches[0]


def _evaluate_mise_guard(expression: str, mode: str, opt_in: bool | None) -> bool:
    """Evaluate the guard over the caller matrix with strict shape checks.

    `opt_in=None` models a caller that does not pass the input at all:
    workflow_call then substitutes the declared default (false).
    """
    assert expression.startswith("${{") and expression.endswith("}}"), (
        f"guard must be a single GHA expression, got: {expression!r}"
    )
    body = expression[3:-2].strip()
    operands = [o.strip() for o in body.split("&&")]
    assert len(operands) == 2, (
        f"guard must be exactly two ANDed conditions (entry mode AND opt-in), got: {body!r}"
    )
    mode_match = re.fullmatch(
        r"steps\.quality-entry\.outputs\.mode == '([^']+)'", operands[0]
    )
    assert mode_match, (
        f"first operand must gate on the real entry detection output, got: {operands[0]!r}"
    )
    if mode_match.group(1) != mode:
        return False
    assert operands[1] == "inputs.quality_prepare_mise", (
        f"second operand must be the raw opt-in input, got: {operands[1]!r}"
    )
    return bool(opt_in)


def test_quality_prepare_mise_input_is_boolean_and_defaults_false():
    raw = _load_gate_v2()
    trigger = raw.get("on", raw.get(True))
    inputs = trigger["workflow_call"]["inputs"]
    assert MISE_INPUT in inputs, (
        f"workflow_call inputs must declare {MISE_INPUT} (default false); "
        "the opt-in surface is the whole fix for gate#314"
    )
    declared = inputs[MISE_INPUT]
    assert declared["type"] == "boolean", (
        f"{MISE_INPUT} must be a boolean input, got type: {declared.get('type')!r}"
    )
    assert declared["default"] is False, (
        f"{MISE_INPUT} must default to false: existing callers must keep "
        "today's behavior until they opt in"
    )


@pytest.mark.parametrize(
    ("mode", "opt_in", "expected"),
    [
        ("entry", True, True),
        ("entry", False, False),
        ("entry", None, False),
        ("legacy", True, False),
        ("legacy", False, False),
        ("legacy", None, False),
    ],
)
def test_quality_mise_bootstrap_runs_only_for_entry_mode_with_explicit_opt_in(
    mode, opt_in, expected
):
    raw = _load_gate_v2()
    step = _mise_step(raw)
    guard = step.get("if")
    assert guard is not None, f"{MISE_STEP_NAME} must carry an if guard"
    assert _evaluate_mise_guard(guard, mode, opt_in) is expected, (
        f"guard {guard!r} must select the bootstrap only for "
        f"entry mode + explicit opt-in; got {mode=} {opt_in=}"
    )


def test_quality_mise_bootstrap_uses_official_action_and_never_installs_project_tools():
    raw = _load_gate_v2()
    step = _mise_step(raw)
    assert step.get("uses") == "jdx/mise-action@v3", (
        f"{MISE_STEP_NAME} must use the official jdx/mise-action@v3, "
        f"got: {step.get('uses')!r}"
    )
    assert step.get("with") == {"install": False}, (
        f"{MISE_STEP_NAME} with must be exactly install: false — gate prepares "
        f"the tool manager only; got: {step.get('with')!r}"
    )
    assert not str(step.get("run", "")).strip(), (
        f"{MISE_STEP_NAME} must be a pure action step: no inline installer script"
    )


def test_quality_mise_bootstrap_sits_between_entry_detection_and_single_business_entry_call():
    raw = _load_gate_v2()
    steps = _quality_steps(raw)
    names = [s.get("name") for s in steps]
    assert DETECT_STEP_NAME in names, f"{DETECT_STEP_NAME} step missing"
    assert MISE_STEP_NAME in names, f"{MISE_STEP_NAME} step missing"
    detect_index = names.index(DETECT_STEP_NAME)
    mise_index = names.index(MISE_STEP_NAME)
    entry_calls = [
        i for i, s in enumerate(steps)
        if str(s.get("run", "")).strip() == BUSINESS_ENTRY_RUN
    ]
    assert len(entry_calls) == 1, (
        f"business entry ({BUSINESS_ENTRY_RUN}) must still be called exactly once, "
        f"found {len(entry_calls)} call sites"
    )
    assert detect_index < mise_index < entry_calls[0], (
        "mise bootstrap must run after the real entry detection and before "
        "the business entry"
    )
    run_quality = steps[entry_calls[0]]
    assert run_quality.get("if") == (
        "${{ steps.quality-entry.outputs.mode == 'entry' }}"
    ), (
        "business entry guard must stay independent of the mise opt-in: "
        "entry mode without the flag must still run the caller's entry"
    )


def test_quality_mise_bootstrap_cannot_swallow_failure():
    raw = _load_gate_v2()
    step = _mise_step(raw)
    assert step.get("continue-on-error") is not True, (
        f"{MISE_STEP_NAME} must not carry continue-on-error: a failed preparation "
        "must stop quality before the business entry runs"
    )
    assert step.get("if") != "always()", (
        f"{MISE_STEP_NAME} must not run unconditionally"
    )


def test_legacy_quality_steps_stay_independent_of_mise_opt_in():
    raw = _load_gate_v2()
    steps = _quality_steps(raw)
    legacy_names = ("Lint / format", "Install dependencies", "Tests",
                    "Restore uv download cache")
    legacy_steps = [s for s in steps if s.get("name") in legacy_names]
    assert len(legacy_steps) == 4, (
        "legacy quality steps must all stay present; "
        f"found: {[s.get('name') for s in legacy_steps]}"
    )
    for step in legacy_steps:
        guard = str(step.get("if", ""))
        assert MISE_INPUT not in guard, (
            f"legacy step {step.get('name')!r} must not depend on {MISE_INPUT}"
        )
        assert "mode == 'legacy'" in guard, (
            f"legacy step {step.get('name')!r} keeps its legacy-only guard"
        )
