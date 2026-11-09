import json
import os
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "gate-v2.yml"
PRODUCER = ROOT / "scripts" / "install_result_telemetry.py"
ENTRY = "${{ steps.quality-entry.outputs.mode == 'entry' }}"


def _quality_steps():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["quality"]["steps"]


def test_entry_mode_telemetry_brackets_quality_and_uses_ledger_upload_path():
    steps = _quality_steps()
    names = [step.get("name") for step in steps]
    start_index = names.index("Start entry install-result telemetry")
    quality_index = names.index("Run scripts/gate-quality")
    finish_index = names.index("Finish entry install-result telemetry")
    publish_index = names.index("Publish v2 review ledger inputs")
    start = steps[start_index]
    quality = steps[quality_index]
    source_checkout = next(step for step in steps if step.get("name") == "Checkout gate actions at this workflow's own commit")
    finish = steps[finish_index]
    legacy = next(step for step in steps if step.get("id") == "install")
    publish = steps[publish_index]

    assert start["if"] == ENTRY
    assert quality["if"] == ENTRY
    assert finish["if"] == "${{ always() && steps.quality-entry.outputs.mode == 'entry' }}"
    assert start_index < quality_index < finish_index < publish_index
    assert start["continue-on-error"] is True and finish["continue-on-error"] is True
    assert start["env"]["INSTALL_RESULT_PATH"] == "${{ runner.temp }}/install-result.json"
    assert finish["env"]["INSTALL_RESULT_PATH"] == start["env"]["INSTALL_RESULT_PATH"]
    assert start["env"]["GATE_INSTALL_TELEMETRY_STATE"] == finish["env"]["GATE_INSTALL_TELEMETRY_STATE"]
    assert 'python3 "${RUNNER_TEMP}/install_result_telemetry.py" start' in start["run"]
    assert 'python3 "${RUNNER_TEMP}/install_result_telemetry.py" finish "$status"' in finish["run"]
    assert "GATE-INSTALL-RESULT-DEGRADED" in start["run"]
    assert "GATE-INSTALL-RESULT-DEGRADED" in finish["run"]
    assert legacy["if"] == "${{ steps.quality-entry.outputs.mode == 'legacy' }}"
    assert "install_result_telemetry.py" in legacy["run"]
    assert legacy["env"]["INSTALL_RESULT_PATH"] == start["env"]["INSTALL_RESULT_PATH"]
    assert publish["env"]["INSTALL"] == "${{ runner.temp }}/install-result.json"
    assert "scripts/install_result_telemetry.py" in source_checkout["env"]["GATE_CHECKOUT_SPARSE"]
    assert 'cp "$GITHUB_WORKSPACE/_gate-action-src/scripts/install_result_telemetry.py" "$RUNNER_TEMP/install_result_telemetry.py"' in source_checkout["run"]


def test_telemetry_producer_writes_legacy_schema_bytes_for_entry_fixture(tmp_path):
    cache = tmp_path / "uv-cache"
    cache.mkdir()
    (cache / "existing.bin").write_bytes(b"old")
    (tmp_path / "uv.lock").touch()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    counter = tmp_path / "du-count"
    du = bin_dir / "du"
    du.write_text(
        "#!/bin/sh\n"
        f"n=$(cat '{counter}' 2>/dev/null || echo 0)\n"
        f"echo $((n + 1)) > '{counter}'\n"
        "if [ \"$n\" -eq 0 ]; then printf '100\\t%s\\n' \"$2\"; "
        "else printf '108\\t%s\\n' \"$2\"; fi\n",
        encoding="utf-8",
    )
    du.chmod(0o755)
    quality = tmp_path / "gate-quality-fixture"
    quality.write_text(
        "#!/bin/sh\nprintf 'newdata' > \"$UV_CACHE_DIR/new.bin\"\n",
        encoding="utf-8",
    )
    quality.chmod(0o755)
    result_path = tmp_path / "install-result.json"
    state_path = tmp_path / "telemetry-start.json"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GATE_INSTALL_TELEMETRY_STATE": str(state_path),
        "INSTALL_RESULT_PATH": str(result_path),
        "GATE_RUNNER_LANE": "self",
        "UV_CACHE_DIR": str(cache),
        "UV_CACHE_HIT": "",
        "RUNNER_NAME": "gatehub-a1b2c3d4e5f6-slot-3",
    }

    subprocess.run([sys.executable, str(PRODUCER), "start"], cwd=tmp_path, env=env, check=True)
    subprocess.run([str(quality)], cwd=tmp_path, env=env, check=True)
    subprocess.run([sys.executable, str(PRODUCER), "finish", "ok"], cwd=tmp_path, env=env, check=True)

    payload_bytes = result_path.read_bytes()
    payload = json.loads(payload_bytes)
    assert set(payload) == {
        "ecosystem", "status", "duration_s", "cache_hit", "cache_scope",
        "runner_name", "cache_new_bytes",
    }
    expected = (
        '{"ecosystem":"uv","status":"ok","duration_s":'
        f'{payload["duration_s"]},"cache_hit":false,"cache_scope":"uv-per-slot",'
        '"runner_name":"gatehub-a1b2c3d4e5f6-slot-3","cache_new_bytes":8}\n'
    ).encode()
    assert payload_bytes == expected
    assert payload["cache_new_bytes"] == 8
    assert payload["cache_scope"] == "uv-per-slot"


def test_telemetry_initialization_failure_is_visible_and_fail_open(tmp_path):
    result_path = tmp_path / "install-result.json"
    proc = subprocess.run(
        [sys.executable, str(PRODUCER), "finish", "ok"],
        env={
            **os.environ,
            "GATE_INSTALL_TELEMETRY_STATE": str(tmp_path / "missing-state.json"),
            "INSTALL_RESULT_PATH": str(result_path),
        },
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "::warning::GATE-INSTALL-RESULT-DEGRADED reason=telemetry-initialization-or-write-failed" in proc.stdout
