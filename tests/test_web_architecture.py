"""Migration boundaries stay enforceable without a GUI or optional web runtime."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
APPLICATION = SOURCE / "application"
WEB = SOURCE / "integrations" / "web"
GUI_MODULES = {"main", "ui", "qtpy", "PyQt5", "PyQt6", "PySide2", "PySide6"}
VENDOR_SDKS = {"openai", "anthropic", "zhipuai"}


def _module_references(path):
    """Include local imports and literal dynamic imports, even inside functions."""
    package = list(path.relative_to(SOURCE).parts[:-1])
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            prefix = package[:len(package) - node.level + 1] if node.level else []
            module = ".".join(prefix + ([node.module] if node.module else []))
            if module:
                yield node.lineno, module
            for alias in node.names:
                yield node.lineno, ".".join(filter(None, (module, alias.name)))
        elif isinstance(node, ast.Call) and node.args:
            function = node.func
            name = function.id if isinstance(function, ast.Name) else getattr(function, "attr", "")
            if name in {"__import__", "import_module"} and isinstance(node.args[0], ast.Constant):
                module = node.args[0].value
                if isinstance(module, str):
                    yield node.lineno, module.lstrip(".")


def _assert_no_dependencies(paths, forbidden):
    violations = []
    for path in paths:
        for line, module in _module_references(path):
            reference = module.removeprefix("src.")
            if any(reference == prefix or reference.startswith(prefix + ".") for prefix in forbidden):
                violations.append(f"{path.relative_to(ROOT)}:{line} -> {module}")
    assert not violations, "\n".join(violations)


def test_application_and_web_never_import_qt_desktop_or_vendor_sdks():
    _assert_no_dependencies(
        [*APPLICATION.rglob("*.py"), *WEB.rglob("*.py")], GUI_MODULES | VENDOR_SDKS
    )


def test_http_adapters_use_application_services_not_engineering_or_model_implementations():
    _assert_no_dependencies(WEB.rglob("*.py"), {
        "api", "model_runtime.provider", "plc.core", "plc.ir", "agent_runtime.agent", "agent_runtime.plc_tools",
        "plc.validation", "plc.static_analysis", "rendering", "gxworks2", "simulator",
        "pywinauto", "pythoncom", "storage.credentials", "storage.config",
    })


def test_deterministic_core_does_not_depend_on_application_or_model_protocol():
    paths = {SOURCE / name for name in (
        'plc/core.py', 'plc/ir.py', 'plc/generation_contract.py', 'plc/semantics.py',
        'plc/static_analysis.py', 'plc/timing.py', 'plc/st_renderer.py', "rendering/ladder_svg.py",
        'plc/specification/repair.py', 'plc/ladder_repair.py',
    )} | set((SOURCE / "plc").rglob("*.py"))
    _assert_no_dependencies(paths, GUI_MODULES | VENDOR_SDKS | {
        "api", "model_runtime.provider", "agent_runtime.agent", "application", "integrations", "mcp",
    })


def test_model_protocol_does_not_import_application_or_engineering_implementation():
    _assert_no_dependencies([SOURCE / 'model_runtime/provider.py', SOURCE / 'agent_runtime/messages.py'],
        GUI_MODULES | {"application", "integrations", "api", "agent_runtime.agent", "plc.core",
                       "plc.ir", "gxworks2", "simulator", "rendering", "pywinauto", "pythoncom"})


def test_all_application_and_web_modules_import_with_gui_and_desktop_execution_blocked(tmp_path):
    if importlib.util.find_spec("fastapi") is None:
        pytest.skip("Optional requirements/web.txt is not installed")
    modules = []
    for path in [*APPLICATION.rglob("*.py"), *WEB.rglob("*.py")]:
        parts = list(path.relative_to(SOURCE).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        modules.append(".".join(parts))
    script = '''
import importlib, importlib.abc, json, sys
blocked = {"main", "ui", "qtpy", "PyQt5", "PyQt6", "PySide2", "PySide6",
           "pywinauto", "pythoncom", "simulator.runtime"}
attempts = []
class BlockDesktop(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            attempts.append(fullname)
            raise ImportError("Desktop dependency attempted: " + fullname)
sys.meta_path.insert(0, BlockDesktop())
for module in json.loads(sys.argv[1]):
    importlib.import_module(module)
assert not attempts, attempts
assert not any(name in sys.modules for name in blocked)
'''
    env = dict(os.environ, PYTHONPATH=str(SOURCE), PYTHONDONTWRITEBYTECODE="1")
    completed = subprocess.run([sys.executable, "-c", script, json.dumps(sorted(set(modules)))],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=45)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not list(tmp_path.iterdir()), "Importing modules must not create local service state"


def _requirements(path, seen=None):
    seen = set() if seen is None else seen
    path = path.resolve()
    if path in seen:
        return set()
    seen.add(path)
    names = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        include = re.match(r"^(?:-r\s*|--requirement(?:\s+|=))(.+)$", line)
        if include:
            names.update(_requirements(path.parent / include.group(1).strip(), seen))
            continue
        name = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", line)
        if name:
            names.add(re.sub(r"[-_.]+", "-", name.group()).lower())
    return names




def test_web_runtime_has_its_own_manifest_without_qt():
    names = _requirements(ROOT / "requirements/web.txt")
    assert {"fastapi", "uvicorn"} <= names
    assert not names.intersection({"pyqt5", "pyqt6", "pyside2", "pyside6", "qtpy"})


def test_root_build_and_packaging_layout_is_explicit():
    assert (ROOT / "build-web.bat").is_file()
    assert (ROOT / "start-web.cmd").is_file()
    assert not (ROOT / "start.bat").exists()
    assert not (SOURCE / "ui").exists()
    assert not (SOURCE / "main.py").exists()
    assert not (ROOT / "requirements/win7.txt").exists()
    assert not (ROOT / "packaging/pyinstaller/desktop.spec").exists()
    assert not (ROOT / "packaging/pyinstaller/desktop-win7.spec").exists()
    assert not _requirements(ROOT / "requirements.txt").intersection({"pyqt5", "pyqt6", "pyside2", "pyside6", "qtpy"})
    for old_path in (
        "main.spec", "main_win7.spec", "web.spec", "requirements-web.txt",
        "requirements-mcp.txt", "requirements-win7.txt", "requirements-gxw-test.txt",
    ):
        assert not (ROOT / old_path).exists(), old_path
    for new_path in (
        "packaging/pyinstaller/web.spec", "requirements/web.txt", "requirements/mcp.txt",
        "requirements/gxw-test.txt",
    ):
        assert (ROOT / new_path).is_file(), new_path
    build_script = (ROOT / "build-web.bat").read_text(encoding="utf-8")
    assert 'scripts\\build_web_source.ps1' in build_script
    assert '-FrontendOnly' in build_script
    assert 'exit /b %BUILD_EXIT%' in build_script
    implementation = (ROOT / "scripts/build_web_source.ps1").read_text(encoding="utf-8")
    assert "Get-Command npm.cmd" in implementation
    assert "& $npm.Source ci" in implementation
    assert "& $npm.Source run types" in implementation
    assert "& $npm.Source run build" in implementation
    assert "requirements\\web.txt" in implementation
    assert "dist\\index.html" in implementation
    assert "exit $exitCode" in implementation
