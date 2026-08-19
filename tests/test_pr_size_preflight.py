import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "actions" / "pr-size-preflight" / "preflight.py"
FIXTURE_PATH = ROOT / "tests" / "data" / "size_filter_contract_v1.patch"


def _module():
    spec = importlib.util.spec_from_file_location("preflight", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _repo(tmp_path: Path, added_lines: int) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "app.py").write_text("base = True\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "app.py").write_text("".join(f"value_{i} = {i}\n" for i in range(added_lines)))
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "head"], cwd=repo, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return repo, base, head


def _fixture_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "fixture-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("fixture base\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(["git", "apply", "--binary", str(FIXTURE_PATH)], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "head"], cwd=repo, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return repo, base, head


def test_classifies_single_sharded_warning_and_blocked():
    module = _module()
    assert module.classify(3000, 4000, 8000, 8) == ("single", True)
    assert module.classify(5000, 4000, 8000, 8) == ("sharded", True)
    assert module.classify(12000, 4000, 8000, 8) == ("warning", True)
    assert module.classify(32001, 4000, 8000, 8) == ("blocked", False)


def test_measurement_matches_codex_diff_and_records_capacity(tmp_path):
    module = _module()
    repo, base, head = _repo(tmp_path, 20)

    result = module.measure(repo, base, head, max_diff_lines=12, warn_lines=20, max_review_shards=3)

    expected_lines = len(
        subprocess.check_output(
            ["git", "diff", "--no-ext-diff", "--binary", base, head], cwd=repo
        ).splitlines()
    )
    assert result["reviewable_lines"] == expected_lines
    assert result["diff_lines"] == expected_lines
    assert result["raw_patch_lines"] == expected_lines
    assert result["excluded_files"] == []
    assert result["changed_files"] == 1
    assert result["changed_lines"] == 21
    assert result["additions"] == 20
    assert result["deletions"] == 1
    assert result["thresholds"]["hard_lines"] == 36
    assert result["classification"] in {"warning", "blocked"}


def test_size_filter_fixture_uses_real_git_diff_and_applies_r1_r2_r3(tmp_path):
    module = _module()
    repo, base, head = _fixture_repo(tmp_path)

    fixture_text = FIXTURE_PATH.read_text()
    assert fixture_text.startswith("diff --git ")
    result = module.measure(repo, base, head, max_diff_lines=20, warn_lines=40, max_review_shards=3)

    assert result["size_filter_contract"] == "v1"
    assert result["raw_patch_lines"] == 5041
    assert result["reviewable_lines"] == 10
    assert result["diff_lines"] == result["reviewable_lines"]
    assert result["changed_lines"] == 5013
    assert result["excluded_files"] == [
        {"path": "assets/payload.bin", "rule": "R1", "raw_lines": 10},
        {"path": "docs.pdf", "rule": "R2", "raw_lines": 9},
        {"path": "exports/survey.doc.html", "rule": "R3", "raw_lines": 5012},
    ]
    assert result["classification"] == "single"


def test_size_filter_rules_are_ordered_and_case_insensitive():
    module = _module()

    assert module._exclusion_rule("asset.PDF", False, 1) == "R2"
    assert module._exclusion_rule("bundle.MIN.JS", False, 1) == "R2"
    assert module._exclusion_rule("export.html", False, 5000) is None
    assert module._exclusion_rule("export.html", False, 5001) == "R3"
    assert module._exclusion_rule("asset.png", True, 6000) == "R1"


def test_binary_pdf_uses_r1_before_extension_rule():
    module = _module()

    assert module._exclusion_rule("asset.pdf", True, 6000) == "R1"


def test_large_pdf_uses_r2_before_generated_rule():
    module = _module()

    assert module._exclusion_rule("export.pdf", False, 6001) == "R2"


def test_size_filter_decodes_quoted_unicode_path_from_numstat_z(tmp_path):
    module = _module()
    repo = tmp_path / "quoted-path-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    path = "目录/报告 final`v1`.PDF"
    (repo / "README.md").write_text("fixture base\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "目录").mkdir()
    (repo / path).write_text("pdf-shaped text\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "head"], cwd=repo, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    result = module.measure(repo, base, head, max_diff_lines=20, warn_lines=40, max_review_shards=3)

    assert result["excluded_files"] == [{"path": path, "rule": "R2", "raw_lines": 7}]
    assert "\\347" not in result["excluded_files"][0]["path"]


def test_size_filter_decodes_rename_paths_from_numstat_z(tmp_path):
    module = _module()
    repo = tmp_path / "rename-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "docs").mkdir()
    (repo / "docs" / "old.txt").write_text("rename me\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(["git", "mv", "docs/old.txt", "docs/new.PDF"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "head"], cwd=repo, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    result = module.measure(repo, base, head, max_diff_lines=20, warn_lines=40, max_review_shards=3)

    assert result["excluded_files"] == [{"path": "docs/new.PDF", "rule": "R2", "raw_lines": 4}]


def test_u2028_does_not_change_byte_patch_line_count(tmp_path):
    module = _module()
    repo, base, head = _repo(tmp_path, 1)
    (repo / "app.py").write_text('value = "left\u2028right"\n')
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "u2028"], cwd=repo, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    patch = subprocess.check_output(
        ["git", "diff", "--no-ext-diff", "--binary", base, head], cwd=repo
    )
    old_byte_lines = len(patch.splitlines())

    result = module.measure(repo, base, head, max_diff_lines=old_byte_lines, warn_lines=20, max_review_shards=3)

    assert old_byte_lines == 7
    assert result["raw_patch_lines"] == old_byte_lines
    assert result["reviewable_lines"] == old_byte_lines
    assert result["classification"] == "single"


def test_measurement_fetches_only_missing_pr_endpoints_from_a_shallow_clone(tmp_path):
    module = _module()
    source, base, head = _repo(tmp_path, 20)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "--bare", str(source), str(remote)], check=True)

    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "--depth", "1", remote.as_uri(), str(shallow)], check=True)
    assert subprocess.run(
        ["git", "cat-file", "-e", f"{base}^{{commit}}"], cwd=shallow, check=False
    ).returncode != 0

    result = module.measure(shallow, base, head, max_diff_lines=12, warn_lines=20, max_review_shards=3)

    assert result["base_sha"] == base
    assert result["head_sha"] == head
    assert subprocess.run(
        ["git", "cat-file", "-e", f"{base}^{{commit}}"], cwd=shallow, check=False
    ).returncode == 0


def test_warning_comment_tells_agent_to_split_without_claiming_review_failed():
    module = _module()
    result = {
        "classification": "warning",
        "reviewable": True,
        "diff_lines": 12000,
        "reviewable_lines": 12000,
        "raw_patch_lines": 12000,
        "changed_lines": 12000,
        "changed_files": 40,
        "additions": 9000,
        "deletions": 3000,
        "head_sha": "abcdef1234567890",
        "excluded_files": [],
        "thresholds": {"single_turn_lines": 4000, "warn_lines": 8000, "hard_lines": 32000},
    }
    body = module.render_comment(result)
    assert "<!-- pr-size-preflight -->" in body
    assert "强警告" in body
    assert "small PR" in body
    assert "仍会完整分片 review" in body
    assert "当前审查 Patch 为 **12000 行**" in body
    assert "实际增删：12000 行（+9000 / -3000）" in body
    assert "审查 Patch：12000 行（可审文本口径）" in body
    assert "当前 diff" not in body
    assert "abcdef1234567890" in body


def test_summary_and_action_outputs_show_excluded_file_details(tmp_path):
    module = _module()
    result = {
        "classification": "single",
        "reviewable_lines": 10,
        "diff_lines": 10,
        "raw_patch_lines": 5041,
        "changed_lines": 5013,
        "additions": 5013,
        "deletions": 0,
        "changed_files": 4,
        "review_plan": "single",
        "excluded_files": [
            {"path": "docs.pdf", "rule": "R2", "raw_lines": 9},
        ],
    }
    summary = tmp_path / "summary.md"
    output = tmp_path / "github-output"
    module._append_summary(result, str(summary))
    module._append_action_outputs(result, str(output))

    summary_text = summary.read_text()
    assert "Reviewable text: 10 lines" in summary_text
    assert "Raw patch: 5041 lines" in summary_text
    assert '"docs.pdf" — rule `R2`, raw patch 9 lines' in summary_text
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert outputs["reviewable-lines"] == "10"
    assert json.loads(outputs["excluded-files"]) == result["excluded_files"]


def test_summary_quotes_paths_with_backticks_without_code_span(tmp_path):
    module = _module()
    result = {
        "classification": "single",
        "reviewable_lines": 0,
        "diff_lines": 0,
        "raw_patch_lines": 8,
        "changed_lines": 0,
        "additions": 0,
        "deletions": 0,
        "changed_files": 1,
        "review_plan": "single",
        "excluded_files": [
            {"path": "reports/final`v1`.pdf", "rule": "R2", "raw_lines": 8},
        ],
    }
    summary = tmp_path / "summary.md"

    module._append_summary(result, str(summary))

    summary_text = summary.read_text()
    assert '"reports/final`v1`.pdf" — rule `R2`, raw patch 8 lines' in summary_text


def test_sticky_comment_scrub_failure_prevents_github_write(monkeypatch):
    module = _module()
    result = {
        "classification": "warning",
        "reviewable_lines": 12000,
        "diff_lines": 12000,
        "raw_patch_lines": 12000,
        "changed_lines": 12000,
        "changed_files": 1,
        "additions": 12000,
        "deletions": 0,
        "head_sha": "head",
        "review_plan": "sharded",
        "excluded_files": [],
        "thresholds": {"single_turn_lines": 4000, "warn_lines": 8000, "hard_lines": 32000},
    }
    writes = []

    def fail_scrub(*args, **kwargs):
        raise RuntimeError("scrub failed")

    monkeypatch.setattr(module, "scrub_for_publish", fail_scrub)
    monkeypatch.setattr(module, "_request", lambda *args, **kwargs: writes.append((args, kwargs)))

    with pytest.raises(RuntimeError, match="scrub failed"):
        module.post_sticky_comment(result, token="token", repository="org/repo", pr_number=7)

    assert writes == []


def test_sticky_comment_sends_scrubbed_body(monkeypatch):
    module = _module()
    result = {
        "classification": "warning",
        "reviewable_lines": 12000,
        "diff_lines": 12000,
        "raw_patch_lines": 12000,
        "changed_lines": 12000,
        "changed_files": 1,
        "additions": 12000,
        "deletions": 0,
        "head_sha": "10.0.0.1",
        "review_plan": "sharded",
        "excluded_files": [],
        "thresholds": {"single_turn_lines": 4000, "warn_lines": 8000, "hard_lines": 32000},
    }
    writes = []

    class Response:
        def __init__(self, payload=b""):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.payload

    def fake_urlopen(request, timeout):
        if request.get_method() == "GET" and "/pulls/" in request.full_url:
            return Response(json.dumps({"head": {"sha": result["head_sha"]}}).encode())
        if request.get_method() == "GET":
            return Response(b"[]")
        writes.append(json.loads(request.data.decode()))
        return Response(b"{}")

    monkeypatch.setenv("RUNNER_NAME", "runner-secret")
    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    module.post_sticky_comment(result, token="token", repository="org/repo", pr_number=7)

    assert len(writes) == 1
    body = writes[0]["body"]
    assert "10.0.0.1" not in body
    assert "[REDACTED:PRIVATE_IP]" in body


def test_step_summary_scrub_failure_leaves_existing_bytes_untouched(monkeypatch, tmp_path):
    module = _module()
    result = {
        "classification": "single",
        "reviewable_lines": 1,
        "diff_lines": 1,
        "raw_patch_lines": 1,
        "changed_lines": 1,
        "additions": 1,
        "deletions": 0,
        "changed_files": 1,
        "review_plan": "single",
        "excluded_files": [],
    }
    summary = tmp_path / "summary.md"
    summary.write_text("existing\n")

    monkeypatch.setattr(module, "scrub_for_publish", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("scrub failed")))

    with pytest.raises(RuntimeError, match="scrub failed"):
        module._append_summary(result, str(summary))

    assert summary.read_text() == "existing\n"


def test_main_writes_action_outputs_and_summary_from_real_producer(tmp_path, monkeypatch):
    module = _module()
    repo, base, head = _fixture_repo(tmp_path)
    result_path = tmp_path / "main-result.json"
    output_path = tmp_path / "github-output"
    summary_path = tmp_path / "github-summary.md"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "fixture/repo")
    monkeypatch.setenv("PR_NUMBER", "0")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "preflight.py",
            "--base-sha", base,
            "--head-sha", head,
            "--max-diff-lines", "20",
            "--warn-lines", "40",
            "--max-review-shards", "3",
            "--output", str(result_path),
        ],
    )

    assert module.main() == 0

    output_bytes = output_path.read_bytes()
    summary_bytes = summary_path.read_bytes()
    expected_excluded = json.dumps(
        [
            {"path": "assets/payload.bin", "rule": "R1", "raw_lines": 10},
            {"path": "docs.pdf", "rule": "R2", "raw_lines": 9},
            {"path": "exports/survey.doc.html", "rule": "R3", "raw_lines": 5012},
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    assert b"reviewable-lines=10\n" in output_bytes
    assert b"excluded-files=" + expected_excluded + b"\n" in output_bytes
    assert b"Reviewable text: 10 lines" in summary_bytes
    assert b"`exports/survey.doc.html`" not in summary_bytes
    assert b'"exports/survey.doc.html"' in summary_bytes
