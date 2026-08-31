import importlib.util
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

import diff_cover  # noqa: F401 — CI must install diff-cover; missing dep must fail, not skip
import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "actions" / "diff-coverage-advisory" / "advisory.py"


def _module():
    spec = importlib.util.spec_from_file_location("diff_coverage_advisory", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def _repo_with_code_change(tmp_path: Path, *, covered: bool) -> tuple[Path, str, str, Path]:
    repo = tmp_path / "code-repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "app.py").write_text("def value():\n    return 1\n")
    (repo / "test_app.py").write_text(
        "from app import value\n\n"
        "def test_value():\n"
        "    assert value() == 1\n"
    )
    base = _commit_all(repo, "base")

    (repo / "app.py").write_text(
        "def value():\n"
        "    return 1\n\n"
        "def extra():\n"
        "    return 2\n"
    )
    if covered:
        (repo / "test_app.py").write_text(
            "from app import extra, value\n\n"
            "def test_value():\n"
            "    assert value() == 1\n\n"
            "def test_extra():\n"
            "    assert extra() == 2\n"
        )
    head = _commit_all(repo, "head")

    lcov_path = repo / "coverage" / "lcov.info"
    lcov_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "-m",
            "pytest",
            "test_app.py",
            "-q",
        ],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "coverage", "lcov", "-o", str(lcov_path)],
        cwd=repo,
        check=True,
    )
    assert lcov_path.is_file(), "fixture lcov must be produced by coverage.py"
    return repo, base, head, lcov_path


def _docs_only_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "docs-repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "README.md").write_text("# base\n")
    base = _commit_all(repo, "base")
    (repo / "README.md").write_text("# updated\n")
    (repo / "docs" / "guide.md").parent.mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "guide.md").write_text("more docs\n")
    head = _commit_all(repo, "head")
    return repo, base, head


@pytest.fixture(scope="module")
def module():
    return _module()


def test_render_note_line_for_covered_partial_and_missing_data(module):
    assert (
        module.render_note_line(
            {"state": "covered", "percent_covered": 50, "covered_lines": 1, "total_lines": 2}
        )
        == "diff-coverage: 50% (1/2 changed lines)"
    )
    assert module.render_note_line({"state": "no_data"}) == "diff-coverage: no coverage data"
    assert module.render_note_line({"state": "skip"}) is None


def test_docs_only_pr_skips_note(module, tmp_path):
    repo, base, head = _docs_only_repo(tmp_path)

    result = module.measure(repo, base, head)

    assert result["state"] == "skip"
    assert module.render_note_line(result) is None


def test_missing_lcov_reports_no_coverage_data_not_zero(module, tmp_path):
    repo, base, head, _ = _repo_with_code_change(tmp_path, covered=True)
    lcov_path = repo / "coverage" / "lcov.info"
    lcov_path.unlink()

    result = module.measure(repo, base, head)

    assert result["state"] == "no_data"
    assert module.render_note_line(result) == "diff-coverage: no coverage data"
    assert "0%" not in module.render_note_line(result)


def test_real_lcov_fixture_reports_percent_and_counts(module, tmp_path):
    repo, base, head, _ = _repo_with_code_change(tmp_path, covered=True)

    result = module.measure(repo, base, head)

    assert result["state"] == "covered"
    note = module.render_note_line(result)
    assert note is not None
    assert note.startswith("diff-coverage: ")
    assert "%" in note
    assert f"({result['covered_lines']}/{result['total_lines']} changed lines)" in note
    assert result["total_lines"] > 0


def test_partially_covered_real_lcov_reports_non_hundred_percent(module, tmp_path):
    repo, base, head, _ = _repo_with_code_change(tmp_path, covered=False)

    result = module.measure(repo, base, head)

    assert result["state"] == "covered"
    note = module.render_note_line(result)
    assert note is not None
    assert result["covered_lines"] < result["total_lines"]
    assert "100%" not in note


def test_ensure_review_commits_fetches_missing_objects(module, monkeypatch):
    calls: list[list[str]] = []

    def fake_git(repo, *args):
        calls.append(list(args))
        if args[:2] == ("cat-file", "-e"):
            raise subprocess.CalledProcessError(1, "git")
        return b""

    monkeypatch.setattr(module, "_git", fake_git)
    module.ensure_review_commits(Path("/tmp/repo"), "base", "head")

    assert any(call[:2] == ["fetch", "--no-tags"] for call in calls)


def test_main_never_returns_nonzero_on_measure_failure(tmp_path, monkeypatch):
    module = _module()
    repo = tmp_path / "broken"
    repo.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setenv("GH_TOKEN", "")
    monkeypatch.setenv("PR_NUMBER", "0")
    monkeypatch.setattr(
        sys,
        "argv",
        ["advisory.py", "--base-sha", "deadbeef", "--head-sha", "cafebabe"],
    )

    assert module.main() == 0


@pytest.mark.parametrize(
    "result, fragments",
    [
        ({"state": "skip"}, ["Status: `skipped`"]),
        (
            {"state": "no_data"},
            ["Status: `no_data`", "diff-coverage: no coverage data"],
        ),
        (
            {
                "state": "covered",
                "percent_covered": 50,
                "covered_lines": 1,
                "total_lines": 2,
                "lcov_path": "coverage/lcov.info",
            },
            ["Status: `covered`", "50% (1/2 changed lines)", "coverage/lcov.info"],
        ),
    ],
)
def test_append_summary_covers_skip_no_data_and_covered(module, tmp_path, result, fragments):
    path = tmp_path / "summary.md"
    module._append_summary(result, str(path))
    text = path.read_text(encoding="utf-8")
    for fragment in fragments:
        assert fragment in text


def test_advisory_source_has_no_issue_comment_http_writes():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert re.search(r"issues/[^\"']*comments", source) is None
    for name in ("post_sticky_comment", "render_comment", "MARKER", "_request"):
        assert f"def {name}" not in source
        assert f"{name} =" not in source


def test_main_writes_job_summary_and_makes_no_github_write_requests(module, tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    requests: list[tuple[str, str]] = []

    class RecordingRequest(urllib.request.Request):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            requests.append((self.get_method(), self.full_url))

    def fake_urlopen(request, timeout=None):
        requests.append((request.get_method(), getattr(request, "full_url", str(request))))
        raise AssertionError(f"unexpected GitHub HTTP: {request.get_method()} {request.full_url}")

    monkeypatch.setattr(urllib.request, "Request", RecordingRequest)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    if hasattr(module, "_request"):
        monkeypatch.setattr(
            module,
            "_request",
            lambda *args, **kwargs: requests.append(("CALL", str(args))) or (_ for _ in ()).throw(
                AssertionError("advisory _request must not be called")
            ),
        )
    monkeypatch.setenv("GH_TOKEN", "token")
    monkeypatch.setenv("PR_NUMBER", "42")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(
        module,
        "measure",
        lambda *args, **kwargs: {
            "state": "covered",
            "percent_covered": 50,
            "covered_lines": 1,
            "total_lines": 2,
            "head_sha": "abc123",
            "lcov_path": "coverage/lcov.info",
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["advisory.py", "--base-sha", "base", "--head-sha", "abc123"],
    )

    assert module.main() == 0

    writes = [item for item in requests if item[0] in {"POST", "PATCH", "PUT", "DELETE", "CALL"}]
    assert writes == []
    assert requests == []
    text = summary.read_text(encoding="utf-8")
    assert "Diff coverage advisory" in text
    assert "Status: `covered`" in text
