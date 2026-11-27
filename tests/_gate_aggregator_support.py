"""Shared test support for gate aggregator test domains."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"

def _module():
    spec = importlib.util.spec_from_file_location("gate_aggregate", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    # dataclasses (used by aggregate.py) resolves annotation types via
    # sys.modules[cls.__module__] under `from __future__ import annotations` —
    # it must be registered before exec_module, or the dataclass decorator
    # crashes with "'NoneType' object has no attribute '__dict__'".
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

class _FakeResponse:
    def __init__(self, payload=b""):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.payload

def _assert_terminal_classification(outcome, expected):
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == expected
    if expected[0] == "integration_error":
        assert (outcome.audit_available, outcome.audit_source_attempt, outcome.audit_artifact_name) == (False, None, None)
