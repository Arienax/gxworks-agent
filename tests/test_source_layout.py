"""Permanent source ownership and import-boundary regression tests."""
import ast
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
LAYOUT = json.loads((ROOT / "tools/source_layout.json").read_text(encoding="utf-8"))
OLD = set(LAYOUT["moved_modules"]) - {"api", "main"}


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module
        elif isinstance(node, ast.Call) and node.args:
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name in {"import_module", "__import__"} and isinstance(node.args[0], ast.Constant):
                if isinstance(node.args[0].value, str):
                    yield node.args[0].value


def test_only_stable_entrypoints_live_at_source_root():
    assert {p.name for p in SRC.glob("*.py")} == {"api.py", "main.py"}
    assert LAYOUT["allowed_root_modules"] == ["api.py", "main.py"]
    assert not LAYOUT["temporary_aliases"]


def test_no_module_package_shadowing_at_any_depth():
    assert not [p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py")
                if p.name != "__init__.py" and (p.parent / p.stem / "__init__.py").is_file()]


def test_no_transition_or_date_module_names_in_production():
    pattern = re.compile(r"(?:_\d{8}|_phase\w*|_legacy|_old|_new|_tmp)\.py$")
    assert not [p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py") if pattern.search(p.name)]


def test_production_and_tests_use_canonical_imports():
    violations = []
    for base in (SRC, ROOT / "tests", ROOT / "scripts", ROOT / "tools"):
        for path in base.rglob("*.py"):
            for name in _imports(path):
                if name in OLD or any(name.startswith(old + ".") for old in OLD):
                    violations.append(f"{path.relative_to(ROOT)} -> {name}")
    assert not violations, "\n".join(violations)


def test_foundation_package_initializers_are_inert():
    for name in ("model_runtime", "knowledge", "plc", "agent_runtime", "inspection", "storage", "shared", "rendering", "ui"):
        tree = ast.parse((SRC / name / "__init__.py").read_text(encoding="utf-8"))
        assert all(isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                   and isinstance(n.value.value, str) for n in tree.body), name


def test_model_package_is_independent_of_plc_and_desktop():
    forbidden = {"plc", "gxw", "gxworks2", "simulator", "pywinauto", "application", "integrations", "ui", "main", "api", "rendering"}
    for path in (SRC / "model_runtime").rglob("*.py"):
        assert not forbidden.intersection(n.split(".")[0] for n in _imports(path)), path


def test_deterministic_core_cannot_load_models_or_application():
    for path in (SRC / "plc").rglob("*.py"):
        assert not {"model_runtime", "application", "ui", "integrations", "openai", "mcp"}.intersection(
            n.split(".")[0] for n in _imports(path)), path


def test_shared_renderer_never_imports_desktop_or_csv_export():
    for path in (SRC / "rendering").rglob("*.py"):
        assert not {"ui", "PyQt5", "PyQt6", "gxworks2", "openai"}.intersection(
            n.split(".")[0] for n in _imports(path)), path


def test_production_never_imports_research_baselines():
    for path in SRC.rglob("*.py"):
        assert not any(n == "research" or n.startswith("research.") for n in _imports(path)), path


def test_config_location_survives_source_move(monkeypatch):
    from storage.config import get_config_path
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert Path(get_config_path()) == SRC / "config.json"


def test_config_location_survives_frozen_move(monkeypatch, tmp_path):
    from storage.config import get_config_path
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "GXWorks-Agent.exe"))
    assert Path(get_config_path()) == tmp_path / "config.json"


def test_read_only_resources_keep_the_same_source_root(monkeypatch):
    from shared.paths import resource_path
    from model_runtime.catalog import catalog_directory
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert resource_path("config.default.json") == ROOT / "resources/config.default.json"
    assert resource_path("knowledge/fx3u_knowledge.sqlite") == ROOT / "resources/knowledge/fx3u_knowledge.sqlite"
    assert catalog_directory() == ROOT / "resources/model_catalog"


def test_frozen_catalog_root_is_not_the_python_package_directory(monkeypatch, tmp_path):
    from model_runtime.catalog import catalog_directory
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert catalog_directory() == tmp_path / "resources/model_catalog"


def test_workbench_has_no_dynamic_source_loader():
    path = SRC / "ui/desktop/workbench"
    assert (path / "editor.py").is_file()
    assert (path / "review.py").is_file()
    assert (path / "messages.py").is_file()
    for file in path.glob("*.py"):
        text = file.read_text(encoding="utf-8")
        assert "spec_from_file_location" not in text
        assert "exec_module" not in text
    assert len((path / "__init__.py").read_text().splitlines()) < 20


def test_public_api_facade_preserves_callable_identity():
    import api
    from application import model_api
    for name in api.__all__:
        assert getattr(api, name) is getattr(model_api, name), name
    assert len((SRC / "api.py").read_text().splitlines()) < 80
    assert len((SRC / "main.py").read_text().splitlines()) < 20


@pytest.mark.parametrize("module", [
    "model_runtime.contract", "plc.ir", "knowledge.retriever",
    "rendering.ladder_svg", "application.model_api", "ui.desktop.application",
])
def test_core_and_entrypoint_imports_do_not_load_qt_or_create_state(module, tmp_path):
    code = """
import importlib, importlib.abc, sys
class NoDesktop(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'PyQt5', 'PyQt6', 'pywinauto'}:
            raise AssertionError('Eager desktop dependency: ' + fullname)
sys.meta_path.insert(0, NoDesktop())
importlib.import_module(sys.argv[1])
"""
    env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-c", code, module], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list(tmp_path.iterdir())


def test_test_boundary_registry_is_complete_and_current():
    """Every executable test file must declare exactly one stable owner boundary."""
    document = (ROOT / "tests" / "README.md").read_text(encoding="utf-8")
    start_marker = "<!-- TEST-BOUNDARY-REGISTRY-START -->"
    end_marker = "<!-- TEST-BOUNDARY-REGISTRY-END -->"
    assert document.count(start_marker) == document.count(end_marker) == 1
    registry = document.split(start_marker, 1)[1].split(end_marker, 1)[0]

    listed = re.findall(
        r"\|\s*`((?:tests/test_[^`]+\.py|web/tests/[^`]+\.test\.mjs))`\s*\|",
        registry,
    )
    assert len(listed) == len(set(listed)), "duplicate test-file entries in tests/README.md"

    actual = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").glob("test_*.py")
    }
    actual.update(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "web" / "tests").glob("*.test.mjs")
    )
    assert set(listed) == actual, (
        "test ownership registry mismatch; "
        f"undocumented={sorted(actual - set(listed))}, "
        f"stale={sorted(set(listed) - actual)}"
    )
    assert not [
        path for path in actual
        if re.search(r"(?:_20\d{6}|_(?:pr|issue)_?\d+)\.py$", path, re.I)
    ], "test files must be named after stable behavior, not dates/issues"
