"""Shared non-test helpers for Required Gate v2 contract tests."""
import json
import os
import subprocess


def _load_workflow():
    from test_gate_v2_contract import WORKFLOW, yaml
    raw = yaml.safe_load(WORKFLOW.read_text())
    return raw, raw.get("on", raw.get(True))


def _evaluate_primary_result_expr(expression: str, raw_result: str) -> str:
    from test_gate_v2_contract import _evaluate_primary_result_expr as evaluate
    return evaluate(expression, raw_result)


def _sparse_env_paths(step: dict) -> list[str]:
    raw = (step.get("env") or {}).get("GATE_CHECKOUT_SPARSE", "")
    if not isinstance(raw, str):
        return []
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _run_quality_result_render(tmp_path, result_bytes=None):
    from test_gate_v2_contract import REPO_ROOT, _load_workflow, _quality_steps
    raw, _ = _load_workflow()
    step = next(step for step in _quality_steps(raw) if step.get("id") == "render-quality-result")
    artifact_dir = tmp_path / "gate-quality"
    artifact_dir.mkdir(parents=True)
    if result_bytes is not None:
        (artifact_dir / "gate-quality-result.json").write_bytes(result_bytes)
    summary_path = tmp_path / "summary.md"
    summary_path.write_bytes(b"")
    env = os.environ.copy()
    env.update({"GATE_ARTIFACT_DIR": str(artifact_dir), "GITHUB_STEP_SUMMARY": str(summary_path)})
    completed = subprocess.run(["bash", "-c", step["run"]], cwd=REPO_ROOT, env=env, text=True, capture_output=True, check=False)
    return step, completed, summary_path.read_bytes()
