"""Contracts for the primary failure/timeout diagnostics handoff."""

import base64
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"
DIAGNOSTICS_DIR = "${{ runner.temp }}/primary-review-diagnostics"


def _primary_steps():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["primary"]["steps"]


def test_primary_writes_restricted_manifest_before_diagnostics_upload():
    steps = _primary_steps()
    indexes = {step.get("name"): i for i, step in enumerate(steps)}
    manifest_index = indexes["Write primary review diagnostics manifest"]
    audit_index = indexes["Upload canonical primary audit"]
    upload_index = indexes["Upload primary review diagnostics"]
    manifest, upload = steps[manifest_index], steps[upload_index]
    assert manifest_index < audit_index < upload_index
    assert manifest["if"] == "always()"
    assert manifest["continue-on-error"] is True
    assert manifest["shell"] == "bash"
    assert manifest["env"]["DIAGNOSTICS_DIR"] == "${{ runner.temp }}/primary-review-diagnostics"
    assert manifest["env"]["RUN_ATTEMPT"] == "${{ github.run_attempt }}"
    run = manifest["run"]
    for field in ("directory_exists", "file_count", "other_file_count", '"name"', '"bytes"', '"mtime"'):
        assert field in run
    assert "attempt-[0-9]+" in run and "raw-output" in run
    assert "::warning::" in run
    assert not any(token in run for token in ("cat ", "read_bytes", "read_text(", "response_body"))
    assert upload["if"] == "always()"
    assert "uses" not in upload
    assert "continue-on-error" not in upload
    assert upload["env"]["DIAGNOSTICS_DIR"] == DIAGNOSTICS_DIR
    assert "--tier d3" in upload["run"]
    assert "--empty skip" in upload["run"]
    assert "$SILO_EXEC" in upload["run"]


def _manifest_program():
    step = next(
        step for step in _primary_steps()
        if step.get("name") == "Write primary review diagnostics manifest"
    )
    run_script = step["run"]
    assert "<<'PY'\n" in run_script and "\nPY\n" in run_script
    return run_script.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


def _run_manifest(program, diagnostics_dir, attempt):
    subprocess.run([sys.executable, "-c", program, str(diagnostics_dir), str(attempt)], check=True)
    return json.loads((diagnostics_dir / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_producer_emits_absent_empty_and_populated_payloads(tmp_path):
    program = _manifest_program()
    for name, attempt in (("absent", 4), ("empty", 5), ("populated", 6)):
        diagnostics_dir = tmp_path / name
        expected_files = []
        if name != "absent":
            diagnostics_dir.mkdir()
        if name == "populated":
            raw_output = diagnostics_dir / "attempt-01-codex-sub-raw-output.txt"
            raw_output.write_bytes(b"{}")
            (diagnostics_dir / "unexpected-provider.stderr").write_text("provider secret", encoding="utf-8")
            expected_files = [{"name": raw_output.name, "bytes": 2, "mtime": raw_output.stat().st_mtime}]
        manifest = _run_manifest(program, diagnostics_dir, attempt)
        assert manifest == {
            "schema_version": 1,
            "attempt": attempt,
            "directory_exists": name != "absent",
            "file_count": len(expected_files),
            "other_file_count": int(name == "populated"),
            "files": expected_files,
        }


def test_archive_workflow_shell_preserves_d3_outputs_at_silo_boundary(tmp_path):
    repo_id, head, run_id, attempt = "1326603956", "a" * 40, "32446501755", "1"
    state, wrapper = tmp_path / "silo.json", tmp_path / "silo_store_fake.py"
    wrapper.write_text('''import base64, io, json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ["REPO_ROOT"])
from scripts import silo_store as store
path = Path(os.environ["FAKE_SILO_STATE"])
state = json.loads(path.read_text()) if path.exists() else {"objects": {}, "calls": []}
state.setdefault("argv", []).append(sys.argv[1:])
class Client:
 def list_objects_v2(self, Bucket, Prefix="", **kwargs):
  return {"Contents": [{"Key": k} for k in state["objects"] if k.startswith(Prefix)], "IsTruncated": False}
 def put_object(self, Bucket, Key, Body):
  state["calls"].append(["PUT", Key]); state["objects"][Key] = base64.b64encode(Body).decode()
 def get_object(self, Bucket, Key):
  state["calls"].append(["GET", Key]); return {"Body": io.BytesIO(base64.b64decode(state["objects"][Key]))}
store.connect = Client
try: rc = store.main()
finally: path.write_text(json.dumps(state))
sys.exit(rc)
''', encoding="utf-8")
    base = tmp_path / "artifacts"
    primary, convergence = (base / name for name in ("primary", "convergence"))
    primary.mkdir(parents=True)
    raw = primary / "attempt-1-codex-sub-raw-output.txt"
    raw.write_bytes(b"provider raw\n\xff")
    (primary / "unexpected-provider.stderr").write_bytes(b"opaque\x00diagnostic")
    _run_manifest(_manifest_program(), primary, 1)
    convergence.mkdir()
    (convergence / "convergence-receipt.json").write_bytes(b'{"receipt":"producer bytes"}\n')
    panel = base / "gate-status-panel-delivery.json"
    panel.write_bytes(b'{"delivery":"producer bytes"}\n')

    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    steps = jobs["primary"]["steps"] + jobs["ocr"]["steps"] + jobs["gate"]["steps"]
    reviewer = "claude-glm-5-3"
    post = next(step for step in jobs["ocr"]["steps"] if step.get("name") == "Post advisory PR comment")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    gh = fake_bin / "gh"
    gh.write_text('''#!/bin/sh
case "$*" in
  *"api user"*) echo '{"id":41898282,"login":"github-actions[bot]"}' ;;
  *--paginate*) echo '[]' ;;
  *"--method POST"*)
    if [ "$FAKE_GH_MODE" = failure ]; then echo "HTTP 503" >&2; exit 1; fi
    echo '{"id":77}' ;;
  *) exit 2 ;;
esac
''', encoding="utf-8")
    gh.chmod(0o755)
    timeout = fake_bin / "timeout"
    timeout.write_text('#!/bin/sh\nshift 2\nexec "$@"\n', encoding="utf-8"); timeout.chmod(0o755)
    scrub = tmp_path / "_gate-scrub-src/scripts/scrub_outbound.py"
    scrub.parent.mkdir(parents=True); scrub.write_text("import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n", encoding="utf-8")
    advisories = []
    for index, (mode, comment) in enumerate((("normal", b"review comment\n"), ("empty", b""), ("failure", b"review comment\n"))):
        advisory = base / f"shadow-{mode}" / "advisory"
        advisory.mkdir(parents=True)
        current_run = str(int(run_id) + index)
        event = advisory / f"shadow-review-v1-{repo_id}-{head}-{reviewer}-{current_run}-{attempt}.json"; event.write_bytes((REPO_ROOT / "tests/fixtures/advisory-shadow-event-v1.json").read_bytes())
        comment_path = advisory / f"advisory-comment-{reviewer}.md"; comment_path.write_bytes(comment)
        env = {**os.environ, **{key: str(value) for key, value in post["env"].items()},
               "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "GH_TOKEN": "test",
               "GITHUB_REPOSITORY": "zlxlabs/gate", "PR_NUMBER": "1", "REVIEWER": reviewer,
               "REVIEW_SHADOW_EVENT_DIR": str(advisory.parent),
               "GITHUB_STEP_SUMMARY": str(tmp_path / f"summary-{mode}.md"), "FAKE_GH_MODE": mode}
        subprocess.run(["bash", "-euo", "pipefail", "-c", post["run"]], cwd=tmp_path, env=env,
                       check=True, capture_output=True)
        names = {path.name for path in advisory.iterdir()}
        expected_names = {event.name, comment_path.name} if mode == "empty" else {
            event.name, f"advisory-sticky-{reviewer}.md", f"advisory-sticky-{reviewer}.json",
            f"advisory-delivery-{reviewer}.json"}
        assert names == expected_names
        assert mode == "empty" or json.loads((advisory / f"advisory-delivery-{reviewer}.json").read_bytes())["delivery"] == ("created" if mode == "normal" else "not_created")
        advisories.append((advisory, f"advisory-event-{reviewer}-{current_run}-{attempt}"))

    sources = [
        ("Archive primary review diagnostics permanently", primary, "DIAGNOSTICS_DIR", f"primary-review-diagnostics-v2-{repo_id}-{head}-{run_id}-{attempt}"),
        ("Archive convergence receipt permanently", convergence, "RECEIPT_DIR", f"gate-convergence-receipt-v1-{repo_id}-{head}-{run_id}-{attempt}"),
        ("Archive gate status panel delivery diagnostic permanently", panel, "PANEL_PATH", f"gate-status-panel-delivery-v1-{repo_id}-{head}-{run_id}-{attempt}"),
    ]
    sources.extend(("Archive advisory review event permanently", source, "ADVISORY_DIR", name) for source, name in advisories)
    expected = {}
    for label, source, variable, name in sources:
        step = next(step for step in steps if step.get("name") == label)
        env = {**os.environ, **step.get("env", {}), "ARTIFACT_NAME": name, variable: str(source),
               "RUNNER_TEMP": str(tmp_path), "SILO_STORE": str(wrapper), "SILO_EXEC": str(REPO_ROOT / "scripts/silo_exec.sh"),
               "REPO_ROOT": str(REPO_ROOT), "FAKE_SILO_STATE": str(state), "AWS_ACCESS_KEY_ID": "test"}
        script = step["run"].replace("${{ github.repository_id }}", repo_id).replace("${{ github.event.pull_request.head.sha }}", head).replace("${{ github.run_id }}", run_id).replace("${{ github.run_attempt }}", attempt)
        subprocess.run(["bash", "-euo", "pipefail", "-c", script], env=env, check=True, capture_output=True)
        prefix = f"archive/{repo_id}/{name}/"
        files = [source] if source.is_file() else [path for path in source.rglob("*") if path.is_file()]
        expected.update({prefix + (path.name if source.is_file() else path.relative_to(source).as_posix()): path.read_bytes() for path in files})
        if source.is_dir():
            expected[prefix + "archive-manifest-v1.json"] = None

    saved = json.loads(state.read_text())
    objects = {key: base64.b64decode(body) for key, body in saved["objects"].items()}
    assert set(objects) == set(expected)
    for key, payload in expected.items():
        if payload is not None:
            assert objects[key] == payload
    puts = {key for method, key in saved["calls"] if method == "PUT"}
    gets = {key for method, key in saved["calls"] if method == "GET"}
    assert puts == gets == set(expected)
    assert [args[0] for args in saved["argv"]] == ["put-dir", "put-dir", "put"] + ["put-dir"] * 3
    assert saved["argv"][0][-2:] == ["--empty", "skip"]
    for key, payload in expected.items():
        if payload is None:
            directory = key.rsplit("/", 1)[0] + "/"
            manifest = json.loads(objects[key])
            assert {row["path"]: (row["bytes"], row["sha256"]) for row in manifest["files"]} == {
                path[len(directory):]: (len(value), hashlib.sha256(value).hexdigest())
                for path, value in expected.items() if path.startswith(directory) and value is not None
            }
