import importlib.util
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "actions" / "pr-size-preflight" / "preflight.py"


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
    assert result["diff_lines"] == expected_lines
    assert result["changed_files"] == 1
    assert result["changed_lines"] == 21
    assert result["additions"] == 20
    assert result["deletions"] == 1
    assert result["thresholds"]["hard_lines"] == 36
    assert result["classification"] in {"warning", "blocked"}


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
        "changed_files": 40,
        "additions": 9000,
        "deletions": 3000,
        "head_sha": "abcdef1234567890",
        "thresholds": {"single_turn_lines": 4000, "warn_lines": 8000, "hard_lines": 32000},
    }
    body = module.render_comment(result)
    assert "<!-- pr-size-preflight -->" in body
    assert "强警告" in body
    assert "small PR" in body
    assert "仍会完整分片 review" in body
    assert "当前审查 Patch 为 **12000 行**" in body
    assert "实际增删：12000 行（+9000 / -3000）" in body
    assert "审查 Patch：12000 行（包含上下文和 diff 元数据）" in body
    assert "当前 diff" not in body
    assert "abcdef1234567890" in body
