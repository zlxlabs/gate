"""Table-driven tests for scripts/classify_pr_reviewable_paths.py.

The classifier is the single source of truth for "should this PR run a model
review?". Fixtures use the GitHub Pull Request Files API JSON shape
(objects with `filename`); see Evidence-Commands on gate-hub#734
(`gh api .../zlxlabs/AiUsageMonitor/pulls/201/files`).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "classify_pr_reviewable_paths.py"
LEDGER_PATH = "retro/acceptance-log.jsonl"

# Sanitized copy of the live AiUsageMonitor PR 201 files API payload: patch
# bodies (ledger rows) stripped; GitHub's object shape and `filename` kept.
PR201_FILES_API = [
    {
        "sha": "c2ad54902d89b85362c204c002ce962e97de9616",
        "filename": LEDGER_PATH,
        "status": "modified",
        "additions": 8,
        "deletions": 0,
        "changes": 8,
        "blob_url": (
            "https://github.com/zlxlabs/AiUsageMonitor/blob/"
            "52fa8aa435543c01267c251b9249dae787f4405a/retro%2Facceptance-log.jsonl"
        ),
        "raw_url": (
            "https://github.com/zlxlabs/AiUsageMonitor/raw/"
            "52fa8aa435543c01267c251b9249dae787f4405a/retro%2Facceptance-log.jsonl"
        ),
        "contents_url": (
            "https://api.github.com/repos/zlxlabs/AiUsageMonitor/contents/"
            "retro%2Facceptance-log.jsonl"
            "?ref=52fa8aa435543c01267c251b9249dae787f4405a"
        ),
    }
]


def _file_obj(filename: str, **extra) -> dict:
    payload = {
        "sha": "0" * 40,
        "filename": filename,
        "status": extra.pop("status", "modified"),
        "additions": extra.pop("additions", 1),
        "deletions": extra.pop("deletions", 0),
        "changes": extra.pop("changes", 1),
    }
    payload.update(extra)
    return payload


def _run_cli(
    tmp_path: Path,
    payload,
    *,
    extra_args: tuple[str, ...] = (),
    stdin: bool = False,
    github_output: bool = True,
    extra_env: dict | None = None,
    input_text: str | None = None,
):
    output_path = tmp_path / "github_output"
    argv = [sys.executable, str(SCRIPT), *extra_args]
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    if github_output:
        argv.extend(["--github-output", str(output_path)])
    stdin_bytes = None
    if input_text is not None:
        stdin_bytes = input_text
        stdin = True
    elif stdin:
        stdin_bytes = json.dumps(payload)
    else:
        listing = tmp_path / "files.json"
        listing.write_text(json.dumps(payload), encoding="utf-8")
        argv.append(str(listing))
    proc = subprocess.run(
        argv,
        input=stdin_bytes,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=str(REPO_ROOT),
    )
    written = output_path.read_text(encoding="utf-8") if output_path.is_file() else ""
    return proc, written


def _review_expected_from_output(written: str, stdout: str) -> str:
    for source in (written, stdout):
        for line in source.splitlines():
            if line.startswith("review_expected="):
                return line.split("=", 1)[1]
    raise AssertionError(
        f"classifier did not write review_expected= (output={written!r} stdout={stdout!r})"
    )


@pytest.mark.parametrize(
    "name, payload, extra_args, want",
    [
        ("pr201_ledger_only", PR201_FILES_API, (), "false"),
        ("empty_list", [], (), "true"),
        (
            "ledger_plus_other",
            [_file_obj(LEDGER_PATH), _file_obj("scripts/gate-quality")],
            (),
            "true",
        ),
        ("other_only", [_file_obj("README.md")], (), "true"),
        (
            "truncated_page_looks_like_ledger_only",
            PR201_FILES_API,
            ("--has-next-page",),
            "true",
        ),
        (
            "slurp_pages_ledger_only",
            [PR201_FILES_API],
            (),
            "false",
        ),
        (
            "slurp_pages_mixed",
            [PR201_FILES_API, [_file_obj("docs/GOALS.md")]],
            (),
            "true",
        ),
    ],
)
def test_table_driven_files_api_payloads(tmp_path, name, payload, extra_args, want):
    assert SCRIPT.is_file(), f"{SCRIPT} must exist as the single classifier source"
    proc, written = _run_cli(tmp_path, payload, extra_args=extra_args)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert _review_expected_from_output(written, proc.stdout) == want, name
    assert f"review_expected={want}" in written


@pytest.mark.parametrize(
    "name, payload_text, extra_args",
    [
        ("not_json", "{", ()),
        ("json_object_not_array", '{"filename": "retro/acceptance-log.jsonl"}', ()),
        ("null_payload", "null", ()),
        ("missing_filename_field", json.dumps([{"sha": "abc", "status": "modified"}]), ()),
        ("filename_not_string", json.dumps([{"filename": 1}]), ()),
        ("mixed_page_and_object", json.dumps([{"filename": LEDGER_PATH}, "nope"]), ()),
    ],
)
def test_unusable_payloads_are_not_false(tmp_path, name, payload_text, extra_args):
    assert SCRIPT.is_file()
    proc, written = _run_cli(
        tmp_path,
        None,
        extra_args=extra_args,
        input_text=payload_text,
    )
    # Failure must not skip review: never write false. Exit may be non-zero.
    combined = written + proc.stdout
    assert "review_expected=false" not in combined, name
    if "review_expected=" in combined:
        assert _review_expected_from_output(written, proc.stdout) == "true"


def test_stdin_json_matches_file_argv(tmp_path):
    assert SCRIPT.is_file()
    via_file, file_out = _run_cli(tmp_path, PR201_FILES_API)
    via_stdin, stdin_out = _run_cli(tmp_path, PR201_FILES_API, stdin=True)
    assert via_file.returncode == 0
    assert via_stdin.returncode == 0
    assert _review_expected_from_output(file_out, via_file.stdout) == "false"
    assert _review_expected_from_output(stdin_out, via_stdin.stdout) == "false"


def test_github_output_env_is_honored_without_flag(tmp_path):
    assert SCRIPT.is_file()
    output_path = tmp_path / "from-env"
    listing = tmp_path / "files.json"
    listing.write_text(json.dumps(PR201_FILES_API), encoding="utf-8")
    env = os.environ.copy()
    env["GITHUB_OUTPUT"] = str(output_path)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(listing)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert output_path.read_text(encoding="utf-8").strip() == "review_expected=false"


def test_missing_input_file_is_not_false(tmp_path):
    assert SCRIPT.is_file()
    output_path = tmp_path / "github_output"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--github-output",
            str(output_path),
            str(tmp_path / "no-such-files.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    written = output_path.read_text(encoding="utf-8") if output_path.is_file() else ""
    combined = written + proc.stdout
    assert "review_expected=false" not in combined
    if "review_expected=" in combined:
        assert _review_expected_from_output(written, proc.stdout) == "true"
