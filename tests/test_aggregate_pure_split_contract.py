from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGGREGATOR = ROOT / ".github" / "actions" / "gate-aggregator"
NEW_MODULES = (
    "aggregate_models.py",
    "disposition_projection.py",
    "finding_relations.py",
    "decision.py",
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_pure_split_uses_one_model_and_contextvar_identity():
    aggregate = _load("aggregate_pure_split_contract_aggregate", AGGREGATOR / "aggregate.py")
    models = sys.modules["gate_aggregate_models"]
    decision = sys.modules["gate_aggregate_decision"]
    finding_relations = sys.modules["gate_finding_relations"]

    assert aggregate.Identity is models.Identity is decision.Identity
    assert aggregate.Outcome is models.Outcome is decision.Outcome
    assert finding_relations.Outcome is models.Outcome
    assert aggregate._ACTIVE_PUBLISH_BUDGET is models._ACTIVE_PUBLISH_BUDGET
    assert aggregate._ACTIVE_PUBLISH_BUDGET.get() is None


def test_pure_modules_have_downward_dag_and_no_network_or_process_io_imports():
    forbidden = {"http", "socket", "subprocess", "urllib", "requests"}
    imports: dict[str, set[str]] = {}
    for filename in NEW_MODULES:
        tree = ast.parse((AGGREGATOR / filename).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        imports[filename] = imported
        assert not imported & forbidden, f"{filename} imported I/O modules: {imported & forbidden}"

    canonical_files = {
        "gate_aggregate_models": "aggregate_models.py",
        "gate_disposition_projection": "disposition_projection.py",
        "gate_finding_relations": "finding_relations.py",
        "gate_aggregate_decision": "decision.py",
        "gate_convergence_from_aggregate": "convergence.py",
        "gate_action_from_aggregate": "gate_action.py",
    }
    edges = {filename: set() for filename in NEW_MODULES}
    for filename in NEW_MODULES:
        tree = ast.parse((AGGREGATOR / filename).read_text(encoding="utf-8"))
        string_names = {
            target.id: node.value
            for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            for target in node.targets if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in canonical_files:
                dependency = canonical_files[node.module]
                if dependency in edges:
                    edges[filename].add(dependency)
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "spec_from_file_location" or len(node.args) < 2:
                continue
            module_name = node.args[0]
            module_name = string_names.get(module_name.id) if isinstance(module_name, ast.Name) else None
            path = node.args[1]
            if not isinstance(path, ast.Call) or not isinstance(path.func, ast.Attribute):
                continue
            if path.func.attr != "with_name" or not path.args or not isinstance(path.args[0], ast.Constant):
                continue
            dependency = path.args[0].value
            assert canonical_files.get(module_name, dependency) == dependency
            if dependency in edges:
                edges[filename].add(dependency)

    assert edges == {
        "aggregate_models.py": set(),
        "disposition_projection.py": set(),
        "finding_relations.py": {"aggregate_models.py"},
        "decision.py": {"aggregate_models.py", "disposition_projection.py"},
    }
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(filename: str) -> None:
        assert filename not in visiting, f"pure-module import cycle includes {filename}"
        if filename in visited:
            return
        visiting.add(filename)
        for dependency in edges[filename]:
            visit(dependency)
        visiting.remove(filename)
        visited.add(filename)

    for filename in NEW_MODULES:
        visit(filename)


def test_aggregate_has_literal_loaders_for_all_split_modules():
    source = (AGGREGATOR / "aggregate.py").read_text(encoding="utf-8")
    canonical_names = {
        "aggregate_models.py": "gate_aggregate_models",
        "disposition_projection.py": "gate_disposition_projection",
        "finding_relations.py": "gate_finding_relations",
        "decision.py": "gate_aggregate_decision",
    }
    for filename in NEW_MODULES:
        literal = f'Path(__file__).with_name("{filename}")'
        assert literal in source
        path_at = source.index(literal)
        loader_start = source.rfind("sys.modules.get(", 0, path_at)
        loader_end = source.find("_SPEC.loader.exec_module(", path_at)
        loader = source[loader_start:loader_end]
        canonical = canonical_names[filename]
        assert f'sys.modules.get(_' in loader
        assert f'_NAME = "{canonical}"' in source[:path_at]
        assert f'sys.modules[_' in loader


def test_isolated_aggregate_and_direct_decision_loads_are_equivalent(tmp_path: Path):
    isolated = tmp_path
    (isolated / ".github" / "actions" / "gate-aggregator").mkdir(parents=True)
    (isolated / "scripts").mkdir()
    for source in AGGREGATOR.glob("*.py"):
        shutil.copy2(source, isolated / ".github" / "actions" / "gate-aggregator" / source.name)
    shutil.copy2(ROOT / "scripts" / "scrub_outbound.py", isolated / "scripts" / "scrub_outbound.py")

    check = r'''
import dataclasses
import importlib.util
import json
import sys
from pathlib import Path
root = Path.cwd()
base = root / ".github/actions/gate-aggregator"
spec = importlib.util.spec_from_file_location("direct_decision_entry", base / "decision.py")
direct = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = direct
spec.loader.exec_module(direct)
spec = importlib.util.spec_from_file_location("aggregate_entry", base / "aggregate.py")
aggregate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = aggregate
spec.loader.exec_module(aggregate)
models = sys.modules["gate_aggregate_models"]
assert direct.Identity is aggregate.Identity is models.Identity
assert direct.Outcome is aggregate.Outcome is models.Outcome
assert aggregate._ACTIVE_PUBLISH_BUDGET is models._ACTIVE_PUBLISH_BUDGET
identity = aggregate.Identity(repository_id=17, head_sha="a" * 40, run_id=23, run_attempt=2, pr=5)
kwargs = dict(
    quality_result="success", primary_result="cancelled", runner="self",
    is_draft=False, review_expected=True, audit=None, audit_error="fixture missing",
    identity=identity, pr_author="reviewer",
)
from_aggregate = aggregate.evaluate(**kwargs)
from_decision = direct.evaluate(**kwargs)
assert type(from_aggregate) is type(from_decision) is aggregate.Outcome
assert json.dumps(dataclasses.asdict(from_aggregate), sort_keys=True, separators=(",", ":")).encode() == json.dumps(dataclasses.asdict(from_decision), sort_keys=True, separators=(",", ":")).encode()
terminal_kwargs = dict(
    repository="owner/repo", identity=identity, quality_result="success",
    primary_result="cancelled", review_expected=True, is_draft=False,
    runner="self",
)
aggregate_terminal = aggregate.build_terminal_envelope(outcome=from_aggregate, **terminal_kwargs)
decision_terminal = direct.build_terminal_envelope(outcome=from_decision, **terminal_kwargs)
assert json.dumps(aggregate_terminal, sort_keys=True, separators=(",", ":")).encode() == json.dumps(decision_terminal, sort_keys=True, separators=(",", ":")).encode()
'''
    completed = subprocess.run(
        [sys.executable, "-c", check], cwd=isolated, text=True, capture_output=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
