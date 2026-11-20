"""Test support for PR size preflight's GitHub compare boundary."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def moving_base_repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    repo = tmp_path / "moving-base-repo"
    repo.mkdir()
    hooks = tmp_path / "empty-hooks"
    hooks.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "core.hooksPath", str(hooks)], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "base_deleted.txt").write_text("deleted after fork\n")
    (repo / "pr_target.txt").write_text("base value\n")
    (repo / "base_modified.txt").write_text("before base advance\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "branch point"], cwd=repo, check=True)
    fork_point = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    base_branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=repo, text=True
    ).strip()
    subprocess.run(["git", "switch", "-qc", "pr"], cwd=repo, check=True)
    (repo / "pr_target.txt").write_text("PR value\n")
    (repo / "pr_only.py").write_text("first = 1\nsecond = 2\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "PR changes"], cwd=repo, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(["git", "switch", "-q", base_branch], cwd=repo, check=True)
    (repo / "base_deleted.txt").unlink()
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base deletes file"], cwd=repo, check=True)
    (repo / "base_modified.txt").write_text("changed after branch point\n")
    (repo / "base_only.txt").write_text("not part of PR\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base advances again"], cwd=repo, check=True)
    base_tip = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return repo, base_tip, head, fork_point


def run_preflight_with_compare_stub(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    merge_base_sha: str | None = None,
    http_status: int | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    script = Path(__file__).resolve().parents[1] / ".github" / "actions" / "pr-size-preflight" / "preflight.py"
    launcher = (
        "import json, os, runpy, sys, urllib.error, urllib.request\n"
        "class Response:\n"
        "    def __enter__(self): return self\n"
        "    def __exit__(self, *exc): return False\n"
        "    def read(self): return json.dumps({'merge_base_commit': {'sha': os.environ.get('PREFLIGHT_COMPARE_SHA')}}).encode()\n"
        "def fake_urlopen(request, timeout):\n"
        "    status = int(os.environ.get('PREFLIGHT_COMPARE_HTTP_STATUS', '0'))\n"
        "    if status: raise urllib.error.HTTPError(request.full_url, status, 'stub failure', {}, None)\n"
        "    return Response()\n"
        "urllib.request.urlopen = fake_urlopen\n"
        "script = sys.argv[1]\n"
        "sys.argv = sys.argv[1:]\n"
        "runpy.run_path(script, run_name='__main__')\n"
    )
    producer_env = {
        **env,
        "GITHUB_REPOSITORY": env.get("GITHUB_REPOSITORY", "fixture/repo"),
        "GH_TOKEN": "compare-test-token",
        "PREFLIGHT_COMPARE_SHA": merge_base_sha or "",
        "PREFLIGHT_COMPARE_HTTP_STATUS": str(http_status or 0),
    }
    return subprocess.run(
        [sys.executable, "-c", launcher, str(script), *argv],
        cwd=cwd, env=producer_env, capture_output=True, text=True, check=check,
    )


def assert_shallow_compare_preflight(tmp_path: Path, monkeypatch, module, module_path: Path) -> None:
    source, base_tip, head, fork_point = moving_base_repo(tmp_path)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "--bare", str(source), str(remote)], check=True)
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", "pr", remote.as_uri(), str(shallow)],
        check=True,
    )
    assert subprocess.run(
        ["git", "cat-file", "-e", f"{head}^{{commit}}"], cwd=shallow, check=False
    ).returncode == 0
    assert subprocess.run(
        ["git", "cat-file", "-e", f"{fork_point}^{{commit}}"], cwd=shallow, check=False
    ).returncode != 0
    assert subprocess.check_output(
        ["git", "rev-parse", "--is-shallow-repository"], cwd=shallow, text=True
    ).strip() == "true"

    requests = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.payload

    def fake_urlopen(request, timeout):
        headers = {key.lower(): value for key, value in request.header_items()}
        requests.append((request.full_url, headers.get("authorization")))
        return Response(json.dumps({"merge_base_commit": {"sha": fork_point}}).encode())

    real_git = shutil.which("git")
    assert real_git
    git_bin = tmp_path / "git-bin"
    git_bin.mkdir()
    fetch_log = tmp_path / "git-fetch-argv.jsonl"
    fake_git = git_bin / "git"
    fake_git.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "if args[:1] == ['fetch']:\n"
        "    with open(os.environ['GIT_FETCH_LOG'], 'a', encoding='utf-8') as output:\n"
        "        output.write(json.dumps(args) + '\\n')\n"
        f"os.execv({real_git!r}, [{real_git!r}, *args])\n"
    )
    fake_git.chmod(0o755)
    result_path = tmp_path / "preflight.json"
    monkeypatch.chdir(shallow)
    monkeypatch.setenv("PATH", str(git_bin) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GIT_FETCH_LOG", str(fetch_log))
    monkeypatch.setenv("GH_TOKEN", "compare-test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/project")
    monkeypatch.setenv("PR_NUMBER", "0")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "preflight.py",
            "--base-sha", base_tip,
            "--head-sha", head,
            "--max-diff-lines", "20",
            "--warn-lines", "40",
            "--max-review-shards", "3",
            "--output", str(result_path),
        ],
    )

    assert module.main() == 0

    result = json.loads(result_path.read_text())
    expected_patch = subprocess.check_output(
        ["git", "diff", "--no-ext-diff", "--binary", fork_point, head], cwd=shallow
    )
    assert len(requests) == 1
    assert requests[0][0].partition("https://api.github.com")[2] == (
        f"/repos/owner/project/compare/{base_tip}...{head}"
    )
    assert requests[0][1] == "Bearer compare-test-token"
    assert result["base_sha"] == base_tip
    assert result["head_sha"] == head
    assert result["merge_base_sha"] == fork_point
    assert result["changed_files"] == 2
    assert result["additions"] == 3
    assert result["deletions"] == 1
    assert result["changed_lines"] == 4
    assert result["raw_patch_lines"] == len(expected_patch.splitlines())
    assert result["reviewable_lines"] == len(expected_patch.splitlines())
    assert json.loads(fetch_log.read_text().splitlines()[0]) == [
        "fetch", "--no-tags", "--depth=1", "origin", fork_point,
    ]
    assert subprocess.check_output(
        ["git", "rev-parse", "--is-shallow-repository"], cwd=shallow, text=True
    ).strip() == "true"


def assert_compare_response_without_merge_base(tmp_path: Path, monkeypatch, module) -> None:
    repo = tmp_path / "unrelated-repo"
    repo.mkdir()
    hooks = tmp_path / "empty-hooks"
    hooks.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "core.hooksPath", str(hooks)], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "base.txt").write_text("base root\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base root"], cwd=repo, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(["git", "switch", "--orphan", "unrelated"], cwd=repo, check=True)
    (repo / "base.txt").unlink(missing_ok=True)
    (repo / "other.txt").write_text("unrelated root\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "unrelated root"], cwd=repo, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    result_path = tmp_path / "result.json"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PR_NUMBER", "0")
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "fixture/repo")
    monkeypatch.setattr(module, "_request", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "preflight.py", "--base-sha", base, "--head-sha", head,
            "--max-diff-lines", "20", "--warn-lines", "40",
            "--max-review-shards", "3", "--output", str(result_path),
        ],
    )

    assert module.main() == 1
    result = json.loads(result_path.read_text())
    assert result["base_sha"] == base
    assert result["head_sha"] == head
    assert result["merge_base_sha"] is None
    assert result["classification"] == "unavailable"
    assert "merge-base-unavailable" in result["measurement_error"]
    assert "missing merge_base_commit.sha" in result["measurement_error"]


def assert_compare_http_error_unavailable(tmp_path: Path, monkeypatch, module) -> None:
    repo = tmp_path / "compare-error-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "file.txt").write_text("base\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    result_path = tmp_path / "compare-error.json"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("GITHUB_REPOSITORY", "fixture/repo")
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("PR_NUMBER", "0")

    def fail_compare(token, method, url, payload=None):
        raise module.urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(module, "_request", fail_compare)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "preflight.py", "--base-sha", base, "--head-sha", base,
            "--max-diff-lines", "20", "--warn-lines", "40",
            "--max-review-shards", "3", "--output", str(result_path),
        ],
    )

    assert module.main() == 1
    result = json.loads(result_path.read_text())
    assert result["classification"] == "unavailable"
    assert result["merge_base_sha"] is None
    assert "HTTP 503" in result["measurement_error"]
