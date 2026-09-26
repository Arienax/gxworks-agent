"""Architecture boundaries for retired runtime control surfaces."""
import ast
from pathlib import Path
import subprocess
import sys


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


def test_offline_profile_helper_imports_without_site_packages():
    # -I -S excludes optional Web/SDK packages even when CI has them installed.
    script = '''
import sys
sys.path.insert(0, sys.argv[1])
from model_profile_fixtures import offline_runtime_profile
first = offline_runtime_profile("first")
second = offline_runtime_profile()
first["model"] = "changed"
assert second == {
    "adapter": "openai_compatible",
    "baseUrl": "https://offline.invalid/v1",
    "model": "offline",
}
assert first is not second
for name in ("pytest", "fastapi", "httpx", "openai", "test_web_api"):
    assert name not in sys.modules, name
'''
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(ROOT / "tests")],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_core_profile_fixtures_do_not_import_web_at_collection():
    # Function-local HTTP tests may still import their Web harness when run.
    for name in ("test_generation_agent_boundary.py", "test_confirmed_input_protocol.py"):
        path = ROOT / "tests" / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]
        assert any(node.module == "model_profile_fixtures" for node in imports), name
        assert all(node.module != "test_web_api" for node in imports), name
