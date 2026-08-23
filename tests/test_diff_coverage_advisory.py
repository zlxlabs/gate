import importlib.util
import json
import subprocess
import sys
from pathlib import Path

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
            "uv",
            "run",
            "--with",
            "coverage,pytest",
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
        ["uv", "run", "--with", "coverage", "coverage", "lcov", "-o", str(lcov_path)],
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
    pytest.importorskip("diff_cover")
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
    pytest.importorskip("diff_cover")
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


def test_post_sticky_comment_uses_marker_and_prefix(module, monkeypatch):
    posted: dict[str, str] = {}

    def fake_request(_token, method, url, payload=None):
        if method == "GET" and url.endswith("/pulls/42"):
            return {"head": {"sha": "abc123"}}
        if method == "GET" and "/issues/42/comments" in url:
            return []
        if method == "POST" and payload is not None:
            posted["body"] = payload["body"]
            return {"id": 1}
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(module, "_request", fake_request)
    module.post_sticky_comment(
        {
            "state": "covered",
            "percent_covered": 100,
            "covered_lines": 2,
            "total_lines": 2,
            "head_sha": "abc123",
        },
        token="token",
        repository="owner/repo",
        pr_number=42,
    )

    assert module.MARKER in posted["body"]
    assert "diff-coverage: 100% (2/2 changed lines)" in posted["body"]
