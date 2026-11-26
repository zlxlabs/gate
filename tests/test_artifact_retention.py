"""Contract: reusable-workflow GitHub artifact retention follows the consumer.

Parses the real producer YAML (not a fixture). Each actions/upload-artifact
step in the four workflows must declare a positive integer retention-days
equal to the consumer-driven target, and sit next to a `# retention:` comment.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
UPLOAD_PREFIX = "actions/upload-artifact@"
RETENTION_COMMENT_RE = re.compile(r"# retention:\s+(\d+)\s+—")

# file → baseline retention-days; gate-v2 quality reuse has a 7-day consumer window
EXPECTED_DAYS = {
    "gate-v2.yml": 14,
    "gate-v2-disposition.yml": 1,
    "gate-shadow-v2.yml": 1,
    "red-on-base.yml": 3,
}


def _upload_steps(raw: dict):
    for job in raw["jobs"].values():
        for step in job.get("steps") or []:
            if str(step.get("uses") or "").startswith(UPLOAD_PREFIX):
                yield step


def test_listed_workflow_upload_retention_matches_consumer():
    for name, expected in EXPECTED_DAYS.items():
        path = WORKFLOWS / name
        text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text)
        steps = list(_upload_steps(raw))
        assert steps, f"{name} has no upload-artifact steps"
        for step in steps:
            artifact_name = str((step.get("with") or {}).get("name", ""))
            step_expected = 7 if artifact_name.startswith("gate-quality-reuse-v1-") else expected
            days = (step.get("with") or {}).get("retention-days")
            assert type(days) is int and days > 0, (
                f"{name} step {step.get('name')!r} retention-days must be a "
                f"positive int; got {days!r}"
            )
            assert days == step_expected, (
                f"{name} step {step.get('name')!r} retention-days={days} "
                f"!= consumer target {step_expected}"
            )

        lines = text.splitlines()
        uses_at = [
            i for i, line in enumerate(lines)
            if "uses: actions/upload-artifact@" in line
        ]
        assert len(uses_at) == len(steps), (
            f"{name}: yaml upload count {len(steps)} != raw uses count {len(uses_at)}"
        )
        for idx, step in zip(uses_at, steps):
            artifact_name = str((step.get("with") or {}).get("name", ""))
            step_expected = 7 if artifact_name.startswith("gate-quality-reuse-v1-") else expected
            window = "\n".join(lines[max(0, idx - 8): idx + 12])
            match = RETENTION_COMMENT_RE.search(window)
            assert match, (
                f"{name} step {step.get('name')!r} missing `# retention:` comment"
            )
            assert int(match.group(1)) == step_expected
            if "gate-quality-junit" in str((step.get("with") or {}).get("name", "")):
                assert "消费方" in window
                assert "d14" in window
                assert "retention:" in window
