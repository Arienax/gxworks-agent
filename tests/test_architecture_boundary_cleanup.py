"""Architecture boundaries for retired runtime control surfaces."""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.append(node.module)
    return result


def test_runtime_does_not_import_retired_context_policy():
    violations = []
    for package in ("application", "agent_runtime", "integrations", "knowledge", "model_runtime"):
        root = SRC / package
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            for imported in _imports(path):
                if imported == "shared.context_policy" or imported.startswith("shared.context_policy."):
                    violations.append(f"{path.relative_to(ROOT)} -> {imported}")
    assert not violations, "\n".join(violations)


def test_workflows_do_not_set_reasoning_effort_defaults():
    violations = []
    for package in ("application", "agent_runtime"):
        root = SRC / package
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and node.arg == "effort":
                    if not isinstance(node.value, ast.Constant) or node.value.value is not None:
                        violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
                if isinstance(node, ast.Dict):
                    for key in node.keys:
                        if isinstance(key, ast.Constant) and key.value == "reasoning_effort":
                            violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not violations, "\n".join(violations)


def test_self_hold_has_no_dedicated_checker_module():
    assert not (SRC / "plc" / "specification" / "checks.py").exists()


def test_generation_does_not_turn_semantic_findings_into_job_failures():
    generation = (SRC / "application" / "generation.py").read_text(encoding="utf-8")
    semantic = (SRC / "plc" / "specification" / "semantic_validation.py").read_text(
        encoding="utf-8"
    )
    assert "ConfirmedSemanticValidationError" not in generation
    assert "raise ConfirmedSemanticValidationError" not in semantic
