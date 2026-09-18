"""Keep the source tree navigable without changing execution or data contracts."""
import ast
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src'
MODULES = json.loads((ROOT / 'docs/architecture/source-layout-map.json').read_text(encoding='utf-8'))
NEW_PACKAGES = ('model_runtime', 'knowledge', 'plc', 'agent_runtime', 'inspection',
                'rendering', 'storage', 'shared', 'ui')


def test_source_root_contains_only_compatible_entrypoints():
    assert {p.name for p in SOURCE.glob('*.py')} == {'api.py', 'main.py'}
    for name in ('api.py', 'main.py'):
        assert len((SOURCE / name).read_text(encoding='utf-8').splitlines()) <= 15


def test_no_python_module_and_package_shadow_each_other():
    collisions = [str(p.relative_to(SOURCE)) for p in SOURCE.rglob('*.py')
                  if p.name != '__init__.py' and (p.with_suffix('') / '__init__.py').is_file()]
    assert collisions == []


def test_no_new_version_or_stage_named_production_modules():
    forbidden = re.compile(r'(?:_20\d{6}|_phase\d\w*|_(?:old|new|tmp|legacy))$', re.I)
    violations = [str(p.relative_to(SOURCE)) for name in NEW_PACKAGES
                  for p in (SOURCE / name).rglob('*.py') if forbidden.search(p.stem)]
    assert not violations, violations
    # Existing experimental native-format implementations are evidence, not trash.
    assert (SOURCE / 'gxw/container_growth_experimental.py').is_file()


def test_product_imports_use_only_canonical_module_paths():
    old = set(MODULES)
    violations = []
    for p in SOURCE.rglob('*.py'):
        tree = ast.parse(p.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                names = [node.module]
            for name in names:
                if name in old:
                    violations.append(f'{p.relative_to(SOURCE)}:{node.lineno}: {name}')
    assert not violations, violations


def test_new_package_initializers_are_deliberately_small():
    for name in NEW_PACKAGES:
        for p in (SOURCE / name).rglob('__init__.py'):
            assert len(p.read_text(encoding='utf-8').splitlines()) <= 20, p


def test_workbench_uses_static_package_imports_not_source_file_loading():
    root = SOURCE / 'ui/desktop/workbench'
    text = '\n'.join(p.read_text(encoding='utf-8') for p in root.glob('*.py'))
    assert 'spec_from_file_location' not in text
    assert 'exec_module' not in text
    assert 'from . import editor as _legacy' in text
    for filename in ('desktop.spec', 'desktop-win7.spec'):
        spec = (ROOT / 'packaging/pyinstaller' / filename).read_text(encoding='utf-8')
        assert 'src/workbench_widgets.py' not in spec
        assert 'ui.desktop.workbench.editor' in spec


def test_api_compatibility_is_the_same_module_not_a_copy(monkeypatch):
    import api
    from application import model_workflows
    assert api is model_workflows
    assert api._provider_session is model_workflows._provider_session
    marker = object()
    monkeypatch.setattr(api, '_layout_identity_probe', marker, raising=False)
    assert model_workflows._layout_identity_probe is marker


def test_desktop_compatibility_is_the_same_module_when_qt_is_installed():
    if not any(importlib.util.find_spec(name) is not None for name in ('PyQt6', 'PyQt5')):
        pytest.skip('Desktop module identity requires the optional Qt dependency')
    import main
    from ui.desktop import main_window
    assert main is main_window
    assert callable(main.main)


def test_original_source_resource_and_state_locations_are_preserved(tmp_path, monkeypatch):
    from shared.paths import source_root, resource_path
    from shared import diagnostics
    from storage.config import get_config_path
    from model_runtime.catalog import catalog_directory
    monkeypatch.chdir(tmp_path)
    assert source_root() == SOURCE
    assert Path(get_config_path()) == SOURCE / 'config.json'
    assert diagnostics._ROOT == SOURCE
    assert resource_path('config.default.json') == ROOT / 'resources/config.default.json'
    assert catalog_directory() == ROOT / 'resources/model_catalog'
    assert not list(tmp_path.iterdir())


def test_frozen_resources_and_external_config_keep_their_separate_locations(tmp_path, monkeypatch):
    from shared.paths import resource_path
    from storage.config import get_config_path
    from model_runtime.catalog import catalog_directory
    bundle = tmp_path / 'bundle'; executable = tmp_path / 'install/app.exe'
    bundle.mkdir(); executable.parent.mkdir()
    bundled = bundle / 'config.default.json'; bundled.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(bundle), raising=False)
    monkeypatch.setattr(sys, 'executable', str(executable))
    assert resource_path('config.default.json') == bundled
    assert Path(get_config_path()) == executable.parent / 'config.json'
    assert catalog_directory() == bundle / 'resources/model_catalog'


def test_data_contract_packages_import_without_loading_optional_runtimes(tmp_path):
    script = '''
import importlib, importlib.abc, sys
blocked = {'openai', 'anthropic', 'mcp', 'PyQt5', 'PyQt6', 'ui', 'pywinauto', 'pythoncom'}
class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise AssertionError('Unexpected optional dependency: ' + fullname)
sys.meta_path.insert(0, BlockOptional())
for name in ('model_runtime.contract', 'agent_runtime.messages', 'plc.ir',
             'plc.generation', 'plc.artifacts', 'knowledge.retriever', 'inspection.presenter'):
    importlib.import_module(name)
assert not blocked.intersection(sys.modules)
'''
    completed = subprocess.run([sys.executable, '-c', script], cwd=tmp_path,
        env={**os.environ, 'PYTHONPATH': str(SOURCE), 'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not list(tmp_path.iterdir())


def test_artifact_writer_is_below_application_and_reuses_the_same_error_type():
    from plc import artifacts
    from application import debug_loop
    assert artifacts.render_candidate_artifacts is debug_loop.render_candidate_artifacts
    assert artifacts.DebugLoopError is debug_loop.DebugLoopError
    text = (SOURCE / 'plc/core.py').read_text(encoding='utf-8')
    assert 'from plc.artifacts import render_candidate_artifacts' in text
    assert 'from application.debug_loop' not in text


def test_simulator_runtime_exports_retain_their_original_identity():
    from simulator import SimulatorGatewayRuntime, SimulatorRuntimeError
    from simulator import runtime
    assert SimulatorGatewayRuntime is runtime.SimulatorGatewayRuntime
    assert SimulatorRuntimeError is runtime.SimulatorRuntimeError
