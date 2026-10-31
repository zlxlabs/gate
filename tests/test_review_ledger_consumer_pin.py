"""zlxlabs/gate-hub#2071: the review-ledger consumer bytes must be pre-registered.

zlxlabs/gate-hub pins the exact sha256 of two files from this repo (its
`scripts/ci/review_ledger_contract_joint.py:verify_chain()`) and fails every
PR's quality job when the digest it computes is not in its approved set. That
set lives in a private repo, so this repo cannot read it at CI time; instead
`tests/fixtures/review-ledger-consumer-pin.txt` records the values gate-hub has
approved. Touching either file without registering the new digest in gate-hub
first makes this test red here — before the merged change rides `@v2` into
every gate-hub PR.

Failure marker: REVIEW-LEDGER-CONSUMER-PIN-MISMATCH
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECORD = Path(__file__).resolve().parent / "fixtures" / "review-ledger-consumer-pin.txt"
ACTION = ROOT / ".github" / "actions" / "review-ledger" / "action.yml"
WORKFLOW = ROOT / ".github" / "workflows" / "gate-v2.yml"
CHECKOUT_MARKER = "GATE_CHECKOUT_PATH: _gate-aggregator-src"
GUIDANCE = (
    "gate-hub pins these bytes; register the new digest BEFORE it rides @v2.\n"
    "  1. append the new digest to the approved set in zlxlabs/gate-hub\n"
    "     scripts/ci/review_ledger_contract_joint.py (verify_chain) and merge it there;\n"
    "  2. then update tests/fixtures/review-ledger-consumer-pin.txt here.\n"
    "Skipping step 1 turns EVERY gate-hub PR's quality job red as soon as @v2 moves\n"
    "(zlxlabs/gate-hub#2071)."
)


def _uncomment(text: str) -> str:
    # Byte-identical copy of verify_chain()'s inner uncomment() in zlxlabs/gate-hub
    # scripts/ci/review_ledger_contract_joint.py:123-125. Keep it literal: the digest
    # only matches gate-hub's if the blank/comment dropping and rstrip agree exactly.
    return "\n".join(line.rstrip() for line in text.splitlines()
                     if line.strip() and not line.lstrip().startswith("#"))


def _digests() -> dict[str, str]:
    workflow = _uncomment(WORKFLOW.read_text()) + "\n"
    # Same ledger-job split as review_ledger_contract_joint.py:129 (not a lookalike).
    ledger = re.split(r"\n  [a-zA-Z_][\w-]*:", workflow.split("\n  ledger:", 1)[1])[0]
    # Same step split and selection as :137 and :142-143.
    steps = re.split(r"(?m)^      - ", ledger)[1:]
    selected = [step for step in steps if CHECKOUT_MARKER in step]
    assert len(selected) == 1, (
        f"REVIEW-LEDGER-CONSUMER-PIN-MISMATCH: gate-hub expects exactly one ledger "
        f"step containing {CHECKOUT_MARKER!r}, found {len(selected)}"
    )
    return {
        "action": hashlib.sha256(_uncomment(ACTION.read_text()).encode()).hexdigest(),
        "checkout": hashlib.sha256(_uncomment(selected[0]).encode()).hexdigest(),
    }


def _recorded() -> dict[str, str]:
    recorded = {}
    for line in RECORD.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _, value = stripped.partition(":")
        recorded[key.strip()] = value.strip()
    return recorded


def test_consumer_pins_match_what_gate_hub_approved():
    actual = _digests()
    recorded = _recorded()
    drifted = [
        f"{key}: gate-hub approved {recorded.get(key) or '<missing from fixture>'}\n"
        f"        computed here {actual[key]}"
        for key in actual
        if recorded.get(key) != actual[key]
    ]
    assert not drifted, (
        "REVIEW-LEDGER-CONSUMER-PIN-MISMATCH — these review-ledger consumer bytes changed\n"
        "without a matching registration in zlxlabs/gate-hub:\n"
        + "\n".join(drifted)
        + "\n" + GUIDANCE
    )
