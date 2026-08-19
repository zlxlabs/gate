from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts.scrub_outbound import scrub_outbound_text


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "scrub_outbound.py"


def test_scrubs_internal_shapes_and_reports_categories():
    text = (
        "runner=gatehub-ubuntu-2204-slot-3 host=build.internal user=alice "
        "ip=10.23.4.5 auth=/opt/review-auth/prod/token "
        "workspace=/home/alice/project token=ghp_1234567890abcdefghijklmnop"
    )

    scrubbed, categories = scrub_outbound_text(
        text,
        runtime_values={
            "RUNNER_NAME": "gatehub-ubuntu-2204-slot-3",
            "HOSTNAME": "build.internal",
            "USER": "alice",
        },
    )

    assert scrubbed == (
        "runner=[REDACTED:RUNNER_NAME] host=[REDACTED:HOSTNAME] "
        "user=[REDACTED:USERNAME] ip=[REDACTED:PRIVATE_IP] "
        "auth=[REDACTED:AUTH_PATH] workspace=[REDACTED:ABSOLUTE_PATH] "
        "[REDACTED:TOKEN]"
    )
    assert categories == [
        "PRIVATE_IP", "AUTH_PATH", "ABSOLUTE_PATH", "RUNNER_NAME", "TOKEN",
        "HOSTNAME", "USERNAME",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "commit=0123456789abcdef0123456789abcdef01234567",
        "file=scripts/scrub_outbound.py:42",
        "function=render_extremely_descriptive_review_summary_payload",
        "diff=+ return [finding.category for finding in findings]",
        "url=https://tmp/path /dev/null /usr/lib/python3.12/x.py",
    ],
)
def test_negative_samples_are_byte_identical(text: str):
    assert scrub_outbound_text(text) == (text, [])


@pytest.mark.parametrize(
    "token",
    [
        "ghp_1234567890abcdefghijklmnop",
        "github_pat_1234567890abcdefghijklmnop",
        "xoxb-1234567890abcdefghijklmnop",
        "token=1234567890abcdefghijklmnop",
        "Bearer 1234567890abcdefghijklmnop",
        "AKIA1234567890ABCDEF",
    ],
)
def test_token_shapes_are_redacted_without_generic_long_token_heuristic(token: str):
    scrubbed, categories = scrub_outbound_text(token)
    assert scrubbed == "[REDACTED:TOKEN]"
    assert categories == ["TOKEN"]


def test_private_ip_requires_valid_octets_and_boundaries():
    text = "v10.0.0.1 10.999.1.1 10.0.0.1 10.0.0.1:8080 10.0.0.1/24"
    scrubbed, categories = scrub_outbound_text(text)
    assert scrubbed == (
        "v10.0.0.1 10.999.1.1 [REDACTED:PRIVATE_IP] "
        "[REDACTED:PRIVATE_IP]:8080 [REDACTED:PRIVATE_IP]/24"
    )
    assert categories == ["PRIVATE_IP"]


def test_short_runtime_values_are_reported_but_not_replaced():
    completed = subprocess.run(
        ["python3", str(MODULE)],
        input="USER=ab abcde",
        text=True,
        capture_output=True,
        check=False,
        env={"PATH": os.environ["PATH"], "USER": "ab"},
    )

    assert completed.returncode == 0
    assert completed.stdout == "USER=ab abcde"
    assert completed.stderr == "scrub_outbound: runtime value USER too short to scrub safely, skipped\n"


def test_cli_scrubs_runner_and_private_ip_using_environment():
    completed = subprocess.run(
        ["python3", str(MODULE)],
        input="runner-secret 10.0.0.1 0123456789abcdef0123456789abcdef01234567",
        text=True,
        capture_output=True,
        check=False,
        env=os.environ | {"RUNNER_NAME": "runner-secret"},
    )

    assert completed.returncode == 0
    assert completed.stdout == (
        "[REDACTED:RUNNER_NAME] [REDACTED:PRIVATE_IP] "
        "0123456789abcdef0123456789abcdef01234567"
    )
    assert completed.stderr == "scrub_outbound: redacted PRIVATE_IP, RUNNER_NAME\n"
