"""Permanent source ownership and import-boundary regression tests."""

from tools.offline_inputs import private_path
import ast
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
from tests.fixture_cache import SOURCE_CACHE

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
LAYOUT = json.loads((ROOT / "tools/source_layout.json").read_text(encoding="utf-8"))
OLD = set(LAYOUT["moved_modules"]) - {"api", "main"}


def _imports(path):
    tree = SOURCE_CACHE.tree(path)
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
    assert {p.name for p in SRC.glob("*.py")} == {"api.py"}
    assert LAYOUT["allowed_root_modules"] == ["api.py"]
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
    for name in ("model_runtime", "knowledge", "plc", "agent_runtime", "inspection", "storage", "shared", "rendering"):
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


def test_config_location_survives_source_move(monkeypatch, tmp_path):
    from storage.config import get_config_path
    monkeypatch.delenv("PLC_AI_CONFIG_PATH", raising=False)
    monkeypatch.setenv("PLC_AI_DATA_DIR", str(tmp_path / "user-settings"))
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert Path(get_config_path()) == tmp_path / "user-settings" / "config.json"
    monkeypatch.chdir(tmp_path)
    assert Path(get_config_path()) == tmp_path / "user-settings" / "config.json"
    assert not (tmp_path / "user-settings").exists()


def test_config_location_survives_frozen_move(monkeypatch, tmp_path):
    from storage.config import get_config_path
    monkeypatch.delenv("PLC_AI_CONFIG_PATH", raising=False)
    monkeypatch.setenv("PLC_AI_DATA_DIR", str(tmp_path / "user-settings"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "GXWorks-Agent.exe"))
    assert Path(get_config_path()) == tmp_path / "user-settings" / "config.json"
    monkeypatch.setattr(sys, "executable", str(tmp_path / "moved" / "GXWorks-Agent.exe"))
    assert Path(get_config_path()) == tmp_path / "user-settings" / "config.json"


def test_read_only_resources_keep_the_same_source_root(monkeypatch):
    from shared.paths import resource_path
    from model_runtime.catalog import catalog_directory
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert resource_path("config.default.json") == ROOT / "resources/config.default.json"
    assert resource_path("instructions/mitsubishi/pwm_contract.generated.json") == ROOT / "resources/instructions/mitsubishi/pwm_contract.generated.json"
    assert catalog_directory() == ROOT / "resources/model_catalog"


def test_frozen_catalog_root_is_not_the_python_package_directory(monkeypatch, tmp_path):
    from model_runtime.catalog import catalog_directory
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert catalog_directory() == tmp_path / "resources/model_catalog"


@pytest.mark.parametrize("filename", ["instructions/control_runtime.json", "instructions/mitsubishi/device_definitions.json"])
def test_frozen_resources_are_found_inside_the_packaged_resources_directory(monkeypatch, tmp_path, filename):
    from shared.paths import resource_path

    bundled = tmp_path / "resources" / filename
    bundled.parent.mkdir(parents=True)
    bundled.write_text('{"fixture": true}', encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resource_path(filename) == bundled
    # An explicitly flattened bundle remains authoritative when both exist.
    flattened = tmp_path / filename
    flattened.parent.mkdir(parents=True, exist_ok=True)
    flattened.write_text('{"fixture": "flat"}', encoding="utf-8")
    assert resource_path(filename) == flattened




def test_public_api_facade_preserves_callable_identity():
    import api
    from application import model_api
    for name in api.__all__:
        assert getattr(api, name) is getattr(model_api, name), name
    assert len((SRC / "api.py").read_text().splitlines()) < 80


@pytest.mark.parametrize("module", [
    "model_runtime.contract", "plc.ir", "knowledge.retriever",
    "rendering.ladder_svg", "application.model_api", "integrations.web.__main__",
    "gxworks2.ui_observations", "gxworks2.workspace_observations",
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


def test_session_persistence_does_not_import_engineering_semantics():
    for name in ("session.py", "project_records.py"):
        assert not {"plc", "simulator", "gxw", "application"}.intersection(
            module.split(".")[0] for module in _imports(SRC / "storage" / name))


def test_web_uses_projected_repair_and_sfc_flags():
    failure = (ROOT / "web/src/features/JobFailure.tsx").read_text(encoding="utf-8")
    sfc = (ROOT / "web/src/features/SFCPanel.tsx").read_text(encoding="utf-8")
    spec = (ROOT / "web/src/features/SpecEditor.tsx").read_text(encoding="utf-8")
    assert "branch_id" not in failure and "y_offset_level" not in failure
    assert "cell.type ===" not in sfc
    assert "function describeExpression" not in spec and "function describeEffect" not in spec
    assert "plc.specification" not in (SRC / "integrations/web/native_validation_routes.py").read_text(encoding="utf-8")


def test_vendor_observation_constants_stay_out_of_gxw_semantics():
    text = "\n".join((SRC / "gxw" / name).read_text(encoding="utf-8") for name in
        ("project_metadata.py", "live_context.py", "native_diagnostics.py", "native_write.py"))
    for value in ("14014", "10002", "~0x6", "compiler_member_offset", "interface_byte_offset", ".dll", "0x39f30", "0x73e5"):
        assert value not in text


@pytest.fixture
def check_workspace(tmp_path):
    """A tiny independent ownership graph, with no product or native calls."""
    root, archive = tmp_path / 'source', tmp_path / 'private'
    (root / 'tests').mkdir(parents=True)
    (root / 'src').mkdir()
    (archive / 'research').mkdir(parents=True)
    alpha = 'def shared():\n    return 1\n\ndef test_one():\n    assert shared() == 1\n\ndef test_two():\n    assert True\n'
    beta = 'from tests.test_alpha import shared\n\ndef test_route():\n    assert shared() == 1\n'
    (root / 'tests/test_alpha.py').write_text(alpha, encoding='utf-8')
    (root / 'tests/test_beta.py').write_text(beta, encoding='utf-8')
    (root / 'tests/README.md').write_text(
        '### Compiler\n| `tests/test_alpha.py` | compiler |\n'
        '### API\n| `tests/test_beta.py` | api |\n<!-- TEST-BOUNDARY-REGISTRY-END -->\n', encoding='utf-8')
    config = {
        'version': 1, 'ignored_inputs': ['pytest.ini'], 'live_tests': [],
        'global_paths': ['pytest.ini', 'tests/conftest.py'],
        'dependency_paths': ['requirements.txt', 'requirements/**'],
        'domains': {'compiler': {'heading': 'Compiler', 'quick': ['tests/test_alpha.py::test_one']},
                    'api': {'heading': 'API', 'quick': ['tests/test_beta.py']}},
        'rules': [
            {'paths': ['src/gxw/diagnostic_*.py'], 'domains': ['compiler', 'api'],
             'quick': ['tests/test_alpha.py::test_one', 'tests/test_beta.py::test_route']},
            {'paths': ['src/gxw/**', 'private/research/**'], 'domains': ['compiler'], 'domain_only': True},
            {'paths': ['tests/support.py'], 'domains': ['compiler'], 'domain_only': True},
            {'paths': ['tests/README.md'], 'domains': ['compiler']},
        ],
    }
    (root / 'tests/check_suites.json').write_text(json.dumps(config), encoding='utf-8')
    return root, archive, config


# Runner contract these local-check tests rely on: ``make_plan`` keeps the requested
# level; only an explicit ``--level full`` selects the full plan. Unmapped
# source inputs, unowned test files, a missing Git base and unknown saved owners are
# reported through ``Plan.unresolved_inputs`` ({path: reason}) instead. Source inputs
# awaiting verification live in ``Plan.source_verification`` ({selector: reason}),
# the impacts added by this run in ``Plan.source_changes``, and the persisted state in
# ``inputs.json`` as ``observed_inputs`` ({path: base64|null}), ``source_pending``
# ({identity: [selector]}) and ``unresolved_inputs`` ({path: reason}).
@pytest.mark.parametrize('path,level,expected,frontend', [
    ('web/src/App.tsx', 'quick', set(), True),
    ('src/gxw/diagnostic_events.py', 'quick', {'tests/test_alpha.py::test_one', 'tests/test_beta.py::test_route'}, False),
    ('src/gxw/diagnostic_events.py', 'domain', {'tests/test_alpha.py', 'tests/test_beta.py'}, False),
    ('src/gxw/reader.py', 'quick', {'tests/test_alpha.py'}, False),
    ('private/research/' + 'evidence/sample.zip', 'quick', {'tests/test_alpha.py'}, False),
    ('requirements.txt', 'quick', set(), False),
    ('requirements/development.constraints.txt', 'quick', set(), False),
    ('web/src/App.tsx', 'full', {'tests/test_alpha.py', 'tests/test_beta.py'}, True),
])
def test_check_selection_respects_paths_levels_and_conservative_fallback(check_workspace, path, level, expected, frontend):
    from tests.check_inputs import Change
    from tests.check_plan import make_plan
    root, archive, config = check_workspace
    result = make_plan(root, archive, config, [Change(path, b'old', b'new')], level)
    assert set(result.targets) == expected
    assert result.frontend is frontend
    assert all(result.targets.values())
    # Only an explicit --level full changes the requested level.
    assert result.level == level and result.requested_level == level


@pytest.mark.parametrize('path', ['src/unmapped.py', '<missing-source-base>'])
def test_check_unknown_source_inputs_never_escalate_and_stay_pending(check_workspace, path):
    from tests.check_inputs import Change
    from tests.check_plan import make_plan
    root, archive, config = check_workspace
    plan = make_plan(root, archive, config, [Change(path, None, b'value = 1\n', unknown=True)], 'quick')
    # An input this configuration cannot place, or a checkout without a base, is not
    # a reason to run everything: the level stays as requested and the input waits.
    assert plan.level == plan.requested_level == 'quick'
    assert plan.full_reasons == []
    assert path in plan.unresolved_inputs
    assert not plan.targets and not plan.frontend


def test_check_test_file_without_owner_selects_only_itself(check_workspace):
    from tests.check_inputs import Change
    from tests.check_plan import make_plan
    root, archive, config = check_workspace
    orphan = root / 'tests/test_orphan.py'
    orphan.write_text('def test_only():\n    assert True\n', encoding='utf-8')
    plan = make_plan(root, archive, config, [Change('tests/test_orphan.py', None, orphan.read_bytes())])
    # An unowned test file is checked as the file it is; it cannot pull in a suite,
    # and the missing owner stays visible as an unresolved input.
    assert plan.level == 'quick' and plan.full_reasons == []
    assert set(plan.targets) == {'tests/test_orphan.py'}
    assert 'tests/test_orphan.py' in plan.unresolved_inputs


@pytest.mark.parametrize('level', ['quick', 'domain'])
@pytest.mark.parametrize('path', [
    'skills/gxworks-mcp-router/SKILL.md',
    'skills/gxworks-mcp-router/references/workflows.md',
    'private/skills/gxworks-private-research/README.md',
    'private/skills/gxworks-private-research/references/search-playbook.md',
])
def test_check_skill_documentation_and_environment_keep_selected_scope(check_workspace, level, path):
    from tests.check_inputs import Change
    from tests.check_plan import load_config, make_plan
    root, archive, config = check_workspace
    config['rules'].extend(rule for rule in load_config(ROOT)['rules'] if not rule['domains'])
    changes = [Change(path, b'old documentation', b'new documentation'),
               Change('<environment>', None, None, True,
                      detail={'kind': 'environment', 'summary': 'pytest 9.1 -> 9.2', 'broad': True}),
               Change('src/gxw/reader.py', b'old', b'new')]
    plan = make_plan(root, archive, config, changes, level,
                     environment_pending={'tests': ['tests/test_alpha.py', 'tests/test_beta.py']})
    assert plan.level == plan.requested_level == level
    assert plan.full_reasons == []
    assert set(plan.targets) == {'tests/test_alpha.py'}
    assert not plan.frontend
    assert 'tests/test_beta.py' in plan.deferred
    assert 're-verify when its scope is selected' in plan.verification['tests/test_beta.py']


@pytest.mark.parametrize('path', ['skills/router/scripts/helper.py', 'private/skills/research/scripts/helper.py'])
def test_check_skill_documentation_rule_does_not_hide_executable_inputs(check_workspace, path):
    from tests.check_inputs import Change
    from tests.check_plan import load_config, make_plan
    root, archive, config = check_workspace
    config['rules'].extend(rule for rule in load_config(ROOT)['rules'] if not rule['domains'])
    plan = make_plan(root, archive, config, [Change(path, None, b'print(1)')])
    # The documentation rule never hides the executable input, but it is not a cause
    # for a complete run either: it waits for confirmation as an unresolved input.
    assert plan.level == plan.requested_level == 'quick'
    assert plan.full_reasons == []
    assert path in plan.unresolved_inputs


@pytest.mark.parametrize('environment_first', [True, False])
def test_check_source_causes_never_escalate_while_environment_stays_separate(check_workspace, environment_first):
    from tests.check_inputs import Change
    from tests.check_plan import make_plan
    root, archive, config = check_workspace
    detail = {'kind': 'interpreter', 'summary': 'Python changed', 'broad': True}
    environment = Change('<environment>', None, None, True, detail=detail)
    changes = [Change('src/unmapped.py', b'old', b'new'), Change('pytest.ini', b'old', b'new')]
    changes.insert(0 if environment_first else len(changes), environment)
    plan = make_plan(root, archive, config, changes, 'domain',
                     environment_pending={'tests': ['tests/test_alpha.py', 'tests/test_beta.py']})
    # Unmapped and shared inputs no longer raise the level; they stay confirmations.
    assert plan.requested_level == plan.level == 'domain'
    assert plan.full_reasons == []
    # Only the input with no known association is unresolved; the shared pytest.ini
    # keeps its real consumer files, which for a domain run are both owners' files.
    assert set(plan.unresolved_inputs) == {'src/unmapped.py'}
    assert set(plan.targets) == {'tests/test_alpha.py', 'tests/test_beta.py'}
    assert plan.environment == [detail]
    assert any('does not request full validation' in note for note in plan.notes)
    # Environment verification stays independent of those source inputs.
    assert set(plan.verification) == {'tests/test_alpha.py', 'tests/test_beta.py'}
    assert any('dependency/runtime change' in reason
               for reasons in plan.targets.values() for reason in reasons)


@pytest.mark.parametrize('kind,expected', [
    ('function', {'tests/test_alpha.py::test_two'}),
    ('new_test', {'tests/test_alpha.py::test_three'}),
    ('decorator', {'tests/test_alpha.py::test_two'}),
    ('helper', {'tests/test_alpha.py', 'tests/test_beta.py'}),
    ('removed_test', {'tests/test_alpha.py', 'tests/test_beta.py'}),
    ('missing_snapshot', set()),
])
def test_check_selection_keeps_parameter_variants_and_expands_fixture_users(check_workspace, kind, expected):
    from tests.check_inputs import Change
    from tests.check_plan import make_plan
    root, archive, config = check_workspace
    path = root / 'tests/test_alpha.py'
    before = path.read_bytes()
    after = {
        'function': before.replace(b'assert True', b'assert 2 == 2'),
        'new_test': before + b'\ndef test_three():\n    assert True\n',
        'decorator': before.replace(b'def test_two()', b'@pytest.mark.parametrize("value", [1, 2])\ndef test_two(value)'),
        'helper': before.replace(b'return 1', b'return 2'),
        'removed_test': before[:before.index(b'\ndef test_two')],
        'missing_snapshot': before,
    }[kind]
    path.write_bytes(after)
    change = Change('tests/test_alpha.py', None if kind == 'missing_snapshot' else before, after,
                    unknown=kind == 'missing_snapshot')
    plan = make_plan(root, archive, config, [change])
    assert set(plan.targets) == expected
    if kind == 'missing_snapshot':
        # An unknown baseline keeps the owners owed as verification instead of
        # silently expanding a scope the user did not select.
        assert {'tests/test_alpha.py', 'tests/test_beta.py'} <= set(plan.source_verification)
    assert not any('[' in target for target in plan.targets)  # pytest expands the full parameter matrix


@pytest.mark.parametrize('deleted', [False, True])
def test_check_shared_helper_impact_follows_only_its_importers(check_workspace, deleted):
    from tests.check_inputs import Change
    from tests.check_plan import make_plan
    root, archive, config = check_workspace
    helper = root / 'tests/support.py'
    helper.write_text('def build():\n    return 1\n', encoding='utf-8')
    # Only alpha imports the shared helper; beta is an independent owner.
    (root / 'tests/test_alpha.py').write_text(
        'from .support import build\n\ndef test_one():\n    assert build() == 1\n\ndef test_two():\n    assert True\n',
        encoding='utf-8')
    (root / 'tests/test_beta.py').write_text('def test_route():\n    assert True\n', encoding='utf-8')
    before = helper.read_bytes()
    if deleted:
        helper.unlink()
    plan = make_plan(root, archive, config, [Change('tests/support.py', before, None if deleted else before + b'\n')])
    touched = set(plan.targets) | set(plan.source_changes) | set(plan.source_verification)
    assert any(selector.startswith('tests/test_alpha.py') for selector in touched)
    # A helper alpha alone reaches never pulls the unrelated owner into this run.
    assert not any(selector.startswith('tests/test_beta.py') for selector in touched)
    assert 'tests/test_beta.py' in plan.deferred
    assert plan.level == 'quick' and plan.full_reasons == []


def test_check_shared_input_impact_intersects_an_existing_scope(check_workspace):
    from tests.check_inputs import Change
    from tests.check_plan import make_plan
    root, archive, config = check_workspace
    config['rules'].append({'paths': ['src/api/**'], 'domains': ['api'], 'domain_only': True})
    # pytest.ini is a shared input: with an explicit API scope selected, only the
    # intersection of its impacts may run, so the compiler owner stays out.
    plan = make_plan(root, archive, config, [Change('src/api/client.py', None, b'x = 1\n'),
                                             Change('pytest.ini', b'old', b'new')], 'quick')
    assert plan.level == 'quick' and plan.full_reasons == []
    assert set(plan.targets) == {'tests/test_beta.py'}
    assert 'tests/test_alpha.py' in plan.deferred


def test_check_resource_input_maps_through_its_declared_reader_owner(check_workspace):
    from tests.check_inputs import Change
    from tests.check_plan import make_plan
    root, archive, config = check_workspace
    (root / 'src/gxw').mkdir()
    # A resource has no import edge: the configured reader names the module that reads
    # it, and its static consumers are the tests that reach that module.
    (root / 'src/gxw/instructions.py').write_text(
        "PATH = 'resources/instructions/ops.json'\n\ndef load():\n    return PATH\n", encoding='utf-8')
    (root / 'tests/test_alpha.py').write_text(
        'from gxw.instructions import load\n\ndef test_one():\n    assert load()\n\ndef test_two():\n    assert True\n',
        encoding='utf-8')
    (root / 'tests/test_beta.py').write_text('def test_route():\n    assert True\n', encoding='utf-8')
    config['shared_impacts'] = [{'paths': ['resources/instructions/**'], 'readers': ['gxw.instructions']}]
    plan = make_plan(root, archive, config, [Change('resources/instructions/ops.json', None, b'{}')])
    assert plan.level == 'quick' and plan.full_reasons == []
    # The declared reader's consumer is selected through its quick contract only.
    assert set(plan.targets) == {'tests/test_alpha.py::test_one'}
    assert 'tests/test_beta.py' not in plan.targets


def test_check_diagnostic_rules_select_real_export_index_and_public_contracts():
    from tests.check_inputs import Change
    from tests.check_plan import load_config, make_plan, registry
    config = load_config(ROOT)
    owners = registry(ROOT, config)
    assert {p for files in owners.values() for p in files if p.startswith('tests/')} == {
        p.relative_to(ROOT).as_posix() for p in (ROOT / 'tests').glob('test_*.py')}
    plan = make_plan(ROOT, private_path('').resolve(), config,
                     [Change('src/gxw/diagnostic_events.py', b'old', b'new')])
    assert 'tests/test_gxw_compiler.py::test_native_export_evidence_is_independent_of_save_and_publication' in plan.targets
    assert 'tests/test_gxw_compiler.py::test_native_event_index_preserves_order_positions_duplicates_and_source_names' in plan.targets
    assert any(p.startswith('tests/test_mcp.py::') for p in plan.targets)
    assert any(p.startswith('tests/test_web_fbd.py::') for p in plan.targets)
    assert any(p.startswith('tests/test_web_contract.py::') for p in plan.targets)
    assert 'tests/test_gxw_compiler.py' not in plan.targets


def _check_git(directory, *args):
    result = subprocess.run(['git', '-C', str(directory), *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def check_environment(monkeypatch):
    """A mutable observation of interpreter, versions, modules and venv identity."""
    observed = {'python': 'Fixture Python 3.13.0', 'platform': 'fixture platform',
                'identity': 'fixture-root::fixture-venv::fixture-python',
                'interpreter': {'executable': 'fixture-python', 'venv': 'fixture-venv'},
                'packages': [['anyio', '4.15.1'], ['pytest', '9.1.1']],
                'plugins': ['_hypothesis_pytestplugin', 'anyio', 'tests'],
                'modules': {'pytest': ['pytest'], 'anyio': ['anyio']}}

    def observe(*_args, **_kwargs):
        return {'python': observed['python'], 'platform': observed['platform'],
                'identity': observed['identity'], 'interpreter': dict(observed['interpreter']),
                'plugins': list(observed['plugins']),
                'packages': [list(row) for row in observed['packages']],
                'modules': {name: list(values) for name, values in observed['modules'].items()}}

    monkeypatch.setattr('tests.check_inputs.runtime_environment', observe)
    monkeypatch.setattr('tests.run_checks.environment_identity', lambda *_: observed['identity'])
    return observed


@pytest.fixture
def check_git_workspace(check_workspace, monkeypatch, check_environment):
    from tests.check_inputs import checkout_state, save_snapshot, snapshot
    root, archive, config = check_workspace
    (root / '.gitignore').write_text('/tests/\n')
    (root / 'src/a.py').write_text('a = 1\n')
    (root / 'src/b.py').write_text('b = 1\n')
    (archive / '.gitignore').write_text('/local/\n')
    (archive / 'research/replay.py').write_text('value = 1\n')
    for directory in (root, archive):
        _check_git(directory, 'init', '--initial-branch=main')
        _check_git(directory, 'add', '.')
        _check_git(directory, '-c', 'user.name=Offline test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'fixture')
        _check_git(directory, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    state = checkout_state(root, archive)
    save_snapshot(state, snapshot(root, archive, config))
    return root, archive, config, state


def test_check_git_discovery_includes_commits_index_worktree_ignored_and_private(check_git_workspace):
    from tests.check_inputs import discover
    root, archive, config, state = check_git_workspace
    baseline = _check_git(root, 'rev-parse', 'HEAD')
    (root / 'src/committed.py').write_text('value = 1\n')
    _check_git(root, 'add', '.')
    _check_git(root, '-c', 'user.name=Offline test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'unpushed')
    (root / 'src/a.py').rename(root / 'src/moved.py')
    _check_git(root, 'add', '-A')
    (root / 'src/b.py').write_text('b = 2\n')
    (root / 'src/new.py').write_text('value = 2\n')
    (root / 'tests/test_alpha.py').write_text('def test_changed():\n    assert True\n')
    (archive / 'research/replay.py').write_text('value = 2\n')
    changes, _, _, base = discover(root, archive, config, state)
    mapped = {change.path: change for change in changes}
    assert base == baseline
    assert set(mapped) == {'src/a.py', 'src/moved.py', 'src/b.py', 'src/new.py', 'src/committed.py',
                           'tests/test_alpha.py', 'private/research/replay.py'}
    assert mapped['src/a.py'].after is None and mapped['src/moved.py'].before is None
    assert mapped['tests/test_alpha.py'].before.startswith(b'def shared')
    assert mapped['private/research/replay.py'].before == b'value = 1\n'


@pytest.mark.parametrize('corruption', ['missing', 'json', 'shape', 'content', 'environment', 'pending',
                                      'observation', 'source_pending', 'unresolved_input'])
def test_check_missing_and_corrupt_snapshots_expand_ignored_inputs(check_git_workspace, corruption):
    from tests.check_inputs import discover
    root, archive, config, state = check_git_workspace
    file = state / 'inputs.json'
    old = json.loads(file.read_text())
    if corruption == 'missing':
        file.unlink()
    elif corruption == 'json':
        file.write_text('{broken')
    else:
        old.update({'shape': {'ignored': []}, 'content': {'ignored': {'tests/test_alpha.py': 'bad base64'}},
                    'environment': {'environment': []}, 'pending': {'pending': ['outside.py']},
                    'observation': {'observed_inputs': {'src/a.py': 'bad base64'}},
                    'source_pending': {'source_pending': {'venv': ['../outside.py']}},
                    'unresolved_input': {'unresolved_inputs': {'../outside.py': 'unknown'}}}[corruption])
        file.write_text(json.dumps(old))
    changes, _, notes, _ = discover(root, archive, config, state)
    ignored = [change for change in changes if change.path.startswith('tests/')]
    assert ignored and all(change.unknown for change in ignored)
    assert any('no snapshot' in note for note in notes)
    with pytest.raises(ValueError, match='unknown source base'):
        discover(root, archive, config, state, base='missing-ref')
    with pytest.raises(ValueError, match='stay inside'):
        discover(root, archive, config, state, paths=['../outside.py'])


def test_check_discovery_detects_ignored_resources_and_environment_changes(check_git_workspace, check_environment):
    from tests.check_inputs import discover
    root, archive, config, state = check_git_workspace
    sample = root / 'tests/fixtures/sample.dat'
    sample.parent.mkdir()
    sample.write_bytes(b'changed test input')
    changes, _, _, _ = discover(root, archive, config, state)
    assert 'tests/fixtures/sample.dat' in {change.path for change in changes}
    old = json.loads((state / 'inputs.json').read_text())
    old['environment']['python'] = 'previous Python version'
    (state / 'inputs.json').write_text(json.dumps(old))
    changes, _, notes, _ = discover(root, archive, config, state)
    found = {change.path: change for change in changes}
    assert '<environment>' in found
    detail = found['<environment>'].detail
    assert detail['kind'] == 'environment' and detail['broad'] and detail['python'] == [
        'previous Python version', check_environment['python']]
    assert detail['packages'] == {'changed': [], 'added': [], 'removed': []}
    assert any('Dependency/runtime environment changed' in note for note in notes)
    assert any('every earlier Python result of this environment is unverified' in note for note in notes)


def test_environment_fingerprint_covers_only_distributions_the_repository_imports():
    from importlib import metadata
    from tests.check_inputs import distribution_name, relevant_distributions, runtime_environment
    tracked = relevant_distributions(ROOT)
    reported = {name for name, _ in runtime_environment(ROOT)['packages']}
    installed = {distribution_name(item.metadata['Name']) for item in metadata.distributions()
                 if item.metadata['Name']}
    assert 'pytest' in reported
    assert reported <= installed
    assert reported == installed & tracked


def test_environment_fingerprint_skips_unused_and_constraint_only_packages(tmp_path):
    from importlib import metadata
    from tests.check_inputs import (distribution_name, relevant_distributions, requirement_paths,
                                    runtime_environment)
    tracked = relevant_distributions(ROOT)
    reported = {name for name, _ in runtime_environment(ROOT)['packages']}
    unused = {distribution_name(item.metadata['Name']) for item in metadata.distributions()
              if item.metadata['Name'] and distribution_name(item.metadata['Name']) not in tracked}
    assert not reported & unused
    # Retiring a package the repository no longer imports must not force full validation.
    assert not reported & {'pyqt5', 'pyqt6', 'pyqt5-qt5', 'pyqt6-qt6'}
    (tmp_path / 'requirements').mkdir()
    (tmp_path / 'requirements/web.txt').write_text('demo-client==1.0\n', encoding='utf-8')
    (tmp_path / 'requirements/development.constraints.txt').write_text('only-pinned==1.0\n', encoding='utf-8')
    assert {path.name for path in requirement_paths(tmp_path)} == {'web.txt'}

def _passing_execute(root, status='passed'):
    """Stand in for pytest: report a per-selector result for what was selected."""
    from tests.check_plan import test_functions

    def execute(plan, *_args, **_kwargs):
        results = {target.split('::')[0] + '::' + name: status for target in plan.targets
                   for name in test_functions((root / target.split('::')[0]).read_bytes())}
        return [{'name': 'python', 'status': status, 'exit_code': 0 if status == 'passed' else 7,
                 'results': results,
                 'pending': sorted(selector for selector, value in results.items() if value != 'passed')}]
    return execute


def test_environment_dependency_graph_follows_enabled_extras_and_reverse_edges(tmp_path, monkeypatch):
    from tests import check_inputs
    statements = {'demo-client': ['demo-crypto[crypto]'],
                  'demo-crypto': ['cffi; extra == "crypto"', 'plain-helper'],
                  'plain-helper': [], 'cffi': [], 'unrelated': []}
    provided = {'demo_client': ['demo-client'], 'demo_crypto': ['demo-crypto'], 'cffi': ['cffi'],
                'plain_helper': ['plain-helper'], 'unrelated': ['unrelated']}

    def requires(name):
        if name not in statements:
            raise check_inputs.metadata.PackageNotFoundError(name)
        return list(statements[name])

    monkeypatch.setattr(check_inputs.metadata, 'requires', requires)
    monkeypatch.setattr(check_inputs.metadata, 'packages_distributions', lambda: provided)
    monkeypatch.setattr(check_inputs, 'repository_imports', lambda _root: frozenset({'demo_client'}))
    monkeypatch.setattr(check_inputs, 'pytest_plugin_modules', lambda _root: frozenset())
    monkeypatch.setattr(check_inputs, 'requirement_paths', lambda _root: ())
    graph = check_inputs.distribution_graph(str(tmp_path))
    # The extra enabled by the declaration, not the whole installed set, decides this.
    assert set(graph['closure']) == {'demo-client', 'demo-crypto', 'cffi', 'plain-helper'}
    assert 'cffi' not in graph['seeds'] and 'unrelated' not in graph['closure']
    assert set(graph['reverse']['cffi']) == {'demo-crypto'}
    assert 'demo_client' in check_inputs.dependency_modules(str(tmp_path), ['cffi'])['cffi']
    assert check_inputs.dependency_modules(str(tmp_path), ['unrelated']) == {'unrelated': []}


def test_environment_pytest_plugin_detection_honours_autoload_and_disable_switch(tmp_path, monkeypatch):
    from tests import check_inputs

    class Entry:
        def __init__(self, name, module):
            self.group, self.name, self.value = 'pytest11', name, module + ':hook'

    class Distribution:
        def __init__(self, entries):
            self.entry_points = entries

    monkeypatch.setattr(check_inputs.metadata, 'distributions', lambda: [
        Distribution([Entry('anyio', 'anyio')]),
        Distribution([Entry('pytest_cov', 'pytest_cov')]),
        Distribution([Entry('hypothesispytest', '_hypothesis_pytestplugin')])])
    check_inputs.pytest_plugin_modules.cache_clear()
    monkeypatch.setenv('PYTEST_DISABLE_PLUGIN_AUTOLOAD', '1')
    assert check_inputs.pytest_plugin_modules(str(tmp_path)) == frozenset()
    monkeypatch.delenv('PYTEST_DISABLE_PLUGIN_AUTOLOAD')
    monkeypatch.setenv('PYTEST_ADDOPTS', '-p no:pytest_cov -p demo_project.plugin')
    check_inputs.pytest_plugin_modules.cache_clear()
    modules = check_inputs.pytest_plugin_modules(str(tmp_path))
    assert 'pytest_cov' not in modules
    assert {'anyio', '_hypothesis_pytestplugin', 'demo_project'} <= modules
    (tmp_path / 'pytest.ini').write_text('[pytest]\naddopts = -p no:anyio\n', encoding='utf-8')
    check_inputs.pytest_plugin_modules.cache_clear()
    assert 'anyio' not in check_inputs.pytest_plugin_modules(str(tmp_path))


def test_check_affected_tests_match_imported_submodules(tmp_path):
    from tests.check_plan import affected_tests
    root, archive = tmp_path / 'source', tmp_path / 'private'
    (root / 'tests').mkdir(parents=True)
    archive.mkdir()
    (root / 'tests/test_client.py').write_text('from demo_client import submodule\n', encoding='utf-8')
    (root / 'tests/test_other.py').write_text('def test_other():\n    assert True\n', encoding='utf-8')
    graph = ({'tests/test_client.py': ('tests/test_client.py', None),
              'tests/test_other.py': ('tests/test_other.py', None)},
             {'demo_client.submodule': {'tests/test_client.py'}})
    assert affected_tests(root, archive, ['demo_client'], graph) == {'tests/test_client.py'}


def test_check_environment_change_reverifies_only_the_selected_owner(check_git_workspace, check_environment, monkeypatch, capsys):
    from tests import run_checks
    from tests.check_inputs import Change, discover
    from tests.check_plan import make_plan
    root, archive, config, state = check_git_workspace
    # A pytest change is broad: no earlier Python result of this venv still holds.
    check_environment['packages'] = [['anyio', '4.15.1'], ['pytest', '9.2.0']]
    changes, _, notes, _ = discover(root, archive, config, state)
    assert any('Dependency/runtime environment changed' in note for note in notes)
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', lambda *_args, **_kwargs: pytest.fail('dry run executed tests'))
    # A snapshot without grouped results cannot name them, so every Python file is
    # owed - but still only inside the selected owner and without raising the level.
    assert run_checks.main(['--dry-run', '--paths', 'src/gxw/reader.py']) == 0
    out = capsys.readouterr().out
    assert 'Level: quick;' in out
    assert 'verify later: tests/test_beta.py' in out
    changes.append(Change('src/gxw/reader.py', b'a = 1\n', b'a = 2\n'))
    plan = make_plan(root, archive, config, changes, 'quick',
                     environment_pending={'tests': ['tests/test_alpha.py', 'tests/test_beta.py']})
    assert plan.level == 'quick'
    assert set(plan.targets) == {'tests/test_alpha.py'}
    # Both files keep the obligation: the selected one only loses it once the whole
    # file ran and passed, so a failed or partial run cannot drop its own debt.
    assert set(plan.verification) == {'tests/test_alpha.py', 'tests/test_beta.py'}
    assert 'whole file selected' in plan.verification['tests/test_alpha.py']
    assert 'tests/test_beta.py' in plan.deferred
    assert make_plan(root, archive, config, changes, 'domain',
                     environment_pending={'tests': ['tests/test_beta.py']}).level == 'domain'


def test_check_unmapped_dependency_impact_is_pending_confirmation_not_full(check_git_workspace, check_environment):
    from tests.check_inputs import discover
    from tests.check_plan import make_plan
    root, archive, config, state = check_git_workspace
    check_environment['packages'] = [['anyio', '4.15.1'], ['olefile', '0.47'], ['pytest', '9.1.1']]
    changes, _, notes, _ = discover(root, archive, config, state)
    plan = make_plan(root, archive, config, changes, 'quick')
    assert plan.level == 'quick' and not plan.frontend
    # olefile provides no module at all: it stays unresolved pending confirmation
    # instead of selecting a complete run, and an unchanged package is not involved.
    assert 'olefile' in plan.unknown_dependencies and 'anyio' not in plan.unknown_dependencies
    assert any('maps to no test' in note for note in plan.notes)
    assert any('olefile' in note for note in notes)


def test_check_environment_owed_scope_waits_until_its_owner_is_selected(check_git_workspace, monkeypatch, check_environment):
    from tests import run_checks
    from tests.check_inputs import discover, save_snapshot, snapshot
    from tests.check_plan import make_plan
    root, archive, config, state = check_git_workspace
    # The other owner's test starts using a third-party package, then becomes baseline.
    (root / 'tests/test_beta.py').write_text('import filelock\n\n\ndef test_route():\n    assert filelock is not None\n',
                                             encoding='utf-8')
    check_environment['packages'].append(['filelock', '3.24.0'])
    check_environment['modules']['filelock'] = ['filelock']
    save_snapshot(state, snapshot(root, archive, config))
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', _passing_execute(root))
    check_environment['packages'] = [['anyio', '4.15.1'], ['pytest', '9.1.1'], ['filelock', '3.25.0']]
    changes, _, _, _ = discover(root, archive, config, state)
    plan = make_plan(root, archive, config, changes, 'quick')
    assert plan.level == 'quick' and set(plan.verification) == {'tests/test_beta.py'}
    assert run_checks.main(['--paths', 'src/gxw/reader.py']) == 0      # compiler owner only
    saved = json.loads((state / 'inputs.json').read_text())
    identity = saved['environment_identity']
    assert saved['environment_pending'][identity]['tests'] == ['tests/test_beta.py']
    # Selecting one function does not discharge an entire file, even if that
    # function happens to be its only current test. Explicit file scope can.
    assert run_checks.main(['--paths', 'src/gxw/diagnostic_events.py']) == 0
    saved = json.loads((state / 'inputs.json').read_text())
    assert saved['environment_pending'][identity]['tests'] == ['tests/test_beta.py']
    assert run_checks.main(['--paths', 'tests/test_beta.py']) == 0
    saved = json.loads((state / 'inputs.json').read_text())
    assert 'environment_pending' not in saved


def test_check_environment_results_are_isolated_per_interpreter_and_venv(check_git_workspace, monkeypatch, check_environment):
    from tests import run_checks
    root, archive, config, state = check_git_workspace
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', _passing_execute(root))
    first = 'fixture-root::fixture-venv::fixture-python'
    assert run_checks.main(['--paths', 'src/gxw/reader.py']) == 0
    saved = json.loads((state / 'inputs.json').read_text())
    observation = saved['verified'][first]['observations'][0]
    assert observation['environment']['python'] == 'Fixture Python 3.13.0'
    assert 'tests/test_alpha.py::test_one' in observation['selectors']
    # The same selectors in another venv are another environment, not a replacement.
    check_environment.update({'identity': 'fixture-root::other-venv::fixture-python2',
                              'interpreter': {'executable': 'fixture-python2', 'venv': 'other-venv'}})
    assert run_checks.main(['--paths', 'src/gxw/reader.py']) == 0
    saved = json.loads((state / 'inputs.json').read_text())
    other = 'fixture-root::other-venv::fixture-python2'
    assert set(saved['verified']) == {first, other}
    assert saved['verified'][first]['observations'][0]['selectors'] == observation['selectors']
    assert saved['verified'][other]['interpreter']['executable'] == 'fixture-python2'
    assert set(saved['environments']) == {first, other}


def test_check_environment_observation_is_recorded_once_per_distinct_environment(check_git_workspace, monkeypatch, check_environment):
    from tests import run_checks
    from tests.check_inputs import discover
    root, archive, config, state = check_git_workspace
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', _passing_execute(root))
    check_environment['packages'] = [['anyio', '4.16.0'], ['pytest', '9.1.1']]
    for _ in range(2):
        assert run_checks.main(['--paths', 'src/gxw/reader.py']) == 0
    saved = json.loads((state / 'inputs.json').read_text())
    entry = saved['verified'][saved['environment_identity']]
    assert len(entry['observations']) == 1
    assert entry['observations'][0]['environment']['packages'] == [['anyio', '4.16.0'], ['pytest', '9.1.1']]
    changes, _, notes, _ = discover(root, archive, config, state)
    assert '<environment>' not in {change.path for change in changes}
    assert not any('Dependency/runtime environment changed' in note for note in notes)


def test_check_discovery_ignores_unexpanded_variable_tool_artifacts(check_git_workspace):
    from tests.check_inputs import discover, is_tool_artifact
    root, archive, config, state = check_git_workspace
    artifact = root / '%SystemDrive%' / 'ProgramData/Microsoft/Windows/Caches/cversions.2.db'
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b'clr cache')
    changes, _, notes, _ = discover(root, archive, config, state)
    assert is_tool_artifact('%SystemDrive%/ProgramData/cversions.2.db')
    assert not is_tool_artifact('src/a.py')
    assert all('%SystemDrive%' not in change.path for change in changes)
    assert any('tool artifact' in note for note in notes)

def test_align_baseline_records_the_environment_and_preserves_known_failures(check_git_workspace, monkeypatch):
    from tests.check_inputs import discover, load_snapshot, save_snapshot
    from tests.run_checks import align_baseline
    root, archive, config, state = check_git_workspace
    monkeypatch.setattr('tests.check_inputs.relevant_distributions', lambda _root: frozenset({'pytest'}))
    monkeypatch.setattr('tests.check_inputs.runtime_environment',
                        lambda *_: {'python': 'fixture Python', 'platform': 'here',
                                    'packages': [['pytest', '9.1.1']]})
    previous = json.loads((state / 'inputs.json').read_text())
    previous['environment'] = {'python': 'fixture Python', 'platform': 'here',
                                'packages': [['pytest', '8.0.0']]}
    previous['pending'] = ['tests/test_alpha.py::test_one']
    previous['domain_debt'] = ['gxw']
    previous['source_pending'] = {'fixture-venv': ['tests/test_beta.py']}
    previous['unresolved_inputs'] = {'src/unmapped.py': 'unknown'}
    previous['observed_inputs'] = {'src/unmapped.py': None}
    save_snapshot(state, previous)
    record = align_baseline(root, archive, config, state, 'user asked to align the baseline')
    assert record['status'] == 'aligned_not_validated'
    assert 'pytest 8.0.0 -> 9.1.1' in record['environment_change']
    assert record['preserved_pending'] == ['tests/test_alpha.py::test_one']
    assert record['preserved_domain_debt'] == ['gxw']
    assert 'No test ran' in record['note']
    saved = load_snapshot(state, root, archive)
    assert saved['environment']['packages'] == [['pytest', '9.1.1']]
    assert saved['pending'] == ['tests/test_alpha.py::test_one']
    assert saved['domain_debt'] == ['gxw']
    for field in ('source_pending', 'unresolved_inputs', 'observed_inputs'):
        assert saved[field] == previous[field]
    assert (state / 'alignments' / (record['recorded_at'] + '.json')).is_file()
    changes, _, notes, _ = discover(root, archive, config, state)
    assert '<environment>' not in {change.path for change in changes}
    assert not any('versions changed' in note for note in notes)


def test_align_baseline_requires_the_user_instruction_and_leaves_the_snapshot_alone(check_git_workspace):
    from tests.run_checks import align_baseline
    root, archive, config, state = check_git_workspace
    before = (state / 'inputs.json').read_text()
    with pytest.raises(ValueError, match='user instruction'):
        align_baseline(root, archive, config, state, '   ')
    assert (state / 'inputs.json').read_text() == before

def test_check_node_environment_change_only_owes_a_frontend_check(check_git_workspace, monkeypatch):
    from tests import run_checks
    from tests.check_inputs import discover
    from tests.check_plan import make_plan
    root, archive, config, state = check_git_workspace
    old = json.loads((state / 'inputs.json').read_text())
    old['environment']['node'] = {'node': 'old Node', 'npm': 'old npm'}
    (state / 'inputs.json').write_text(json.dumps(old))
    changes, _, _, _ = discover(root, archive, config, state)
    plan = make_plan(root, archive, config, changes)
    # A Node change alone must not run Web tests for an unrelated PLC scope.
    assert plan.frontend_pending and not plan.frontend and not plan.targets
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', _passing_execute(root))
    assert run_checks.main(['--paths', 'src/gxw/reader.py']) == 0
    assert json.loads((state / 'inputs.json').read_text())['frontend_pending'] is True
    monkeypatch.setattr(run_checks, 'execute', lambda *_args, **_kwargs: [
        {'name': 'python', 'status': 'passed', 'exit_code': 0, 'results': {}},
        {'name': 'web-tests', 'status': 'passed', 'exit_code': 0},
        {'name': 'web-build', 'status': 'passed', 'exit_code': 0}])
    assert run_checks.main(['--paths', 'web/src/App.tsx']) == 0
    assert 'frontend_pending' not in json.loads((state / 'inputs.json').read_text())


def test_check_node_change_without_python_scope_records_its_debt_once(check_git_workspace, monkeypatch, capsys):
    from tests import run_checks
    root, archive, config, state = check_git_workspace
    old = json.loads((state / 'inputs.json').read_text())
    old['environment']['node'] = {'node': 'old Node', 'npm': 'old npm'}
    (state / 'inputs.json').write_text(json.dumps(old))
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', lambda *_args, **_kwargs: pytest.fail('nothing was selected'))
    # Nothing Python is selectable, yet the observation and the owed Web check are saved.
    assert run_checks.main([]) == 0
    first = capsys.readouterr().out
    assert 'Node/npm versions changed' in first
    assert 'Web tests/build still owed' in first
    saved = json.loads((state / 'inputs.json').read_text())
    assert saved['frontend_pending'] is True
    assert run_checks.main([]) == 0
    assert 'Node/npm versions changed' not in capsys.readouterr().out
    assert json.loads((state / 'inputs.json').read_text())['frontend_pending'] is True


def test_check_frontend_is_cleared_only_by_both_web_steps_passing(check_git_workspace, monkeypatch):
    from tests import run_checks
    root, archive, config, state = check_git_workspace
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', lambda *_args, **_kwargs: [
        {'name': 'web-tests', 'status': 'passed', 'exit_code': 0},
        {'name': 'web-build', 'status': 'failed', 'exit_code': 1}])
    # A first frontend selection that fails keeps its own obligation; the failing
    # Web step decides the exit code.
    assert run_checks.main(['--paths', 'web/src/App.tsx']) == 1
    assert json.loads((state / 'inputs.json').read_text())['frontend_pending'] is True
    monkeypatch.setattr(run_checks, 'execute', lambda *_args, **_kwargs: [
        {'name': 'web-tests', 'status': 'passed', 'exit_code': 0},
        {'name': 'web-build', 'status': 'passed', 'exit_code': 0}])
    assert run_checks.main(['--paths', 'web/src/App.tsx']) == 0
    assert 'frontend_pending' not in json.loads((state / 'inputs.json').read_text())


def test_check_pending_frontend_runs_only_in_a_scope_that_owns_web_presentation(check_workspace):
    from tests.check_inputs import Change
    from tests.check_plan import make_plan
    root, archive, config = check_workspace
    (root / 'web/tests').mkdir(parents=True)
    (root / 'web/tests/workbench.test.mjs').write_text('test("x", () => {});\n', encoding='utf-8')
    (root / 'tests/README.md').write_text(
        (root / 'tests/README.md').read_text(encoding='utf-8').replace(
            '| `tests/test_alpha.py` | compiler |',
            '| `tests/test_alpha.py` | compiler |\n| `web/tests/workbench.test.mjs` | web workbench |'),
        encoding='utf-8')
    config['rules'].append({'paths': ['src/api/**'], 'domains': ['api'], 'domain_only': True})
    # The quick contract of this owner selects Python cases only.
    web_owner = [Change('src/gxw/diagnostic_events.py', b'a = 1\n', b'a = 2\n')]
    plc_only = [Change('src/api/client.py', b'a = 1\n', b'a = 2\n')]
    assert not make_plan(root, archive, config, web_owner, 'quick').frontend
    # The owed Web check runs inside the selected owner that owns Web presentation.
    plan = make_plan(root, archive, config, web_owner, 'quick', frontend_pending=True)
    assert plan.frontend and plan.frontend_pending
    assert {'tests/test_alpha.py::test_one', 'tests/test_beta.py::test_route'} <= set(plan.targets)
    # An unrelated PLC owner neither runs the Web check nor loses the obligation.
    plan = make_plan(root, archive, config, plc_only, 'quick', frontend_pending=True)
    assert not plan.frontend and plan.frontend_pending and set(plan.targets) == {'tests/test_beta.py'}


def test_environment_pytest_dependency_change_is_broad(check_git_workspace, check_environment):
    from tests.check_inputs import discover, save_snapshot, snapshot
    root, archive, config, state = check_git_workspace
    (root / 'tests/test_alpha.py').write_text('import pytest\n\n\ndef test_one():\n    assert pytest is not None\n',
                                             encoding='utf-8')
    save_snapshot(state, snapshot(root, archive, config))
    # pluggy is required by pytest itself: every test executes through it, including
    # the tests that never import pytest, so it is a broad change.
    check_environment['packages'] = [['anyio', '4.15.1'], ['pluggy', '1.6.0'], ['pytest', '9.1.1']]
    changes, _, notes, _ = discover(root, archive, config, state)
    detail = {change.path: change for change in changes}['<environment>'].detail
    assert detail['packages']['added'] == [['pluggy', '1.6.0']]
    assert detail['broad']
    assert any('every earlier Python result' in note for note in notes)


def test_environment_plugin_dependency_change_is_broad(check_git_workspace, check_environment):
    from tests.check_inputs import discover, save_snapshot, snapshot
    root, archive, config, state = check_git_workspace
    (root / 'tests/test_alpha.py').write_text('import pytest\n\n\ndef test_one():\n    assert pytest is not None\n',
                                             encoding='utf-8')
    save_snapshot(state, snapshot(root, archive, config))
    # idna is only required by the active anyio plugin and imported by no test:
    # the direct imports of the tests cannot scope this change, so it is broad too.
    check_environment['packages'] = [['anyio', '4.15.1'], ['idna', '3.18'], ['pytest', '9.1.1']]
    changes, _, notes, _ = discover(root, archive, config, state)
    detail = {change.path: change for change in changes}['<environment>'].detail
    assert detail['packages']['added'] == [['idna', '3.18']]
    assert 'idna' in detail['modules']
    assert detail['broad']
    assert any('every earlier Python result' in note for note in notes)


def test_check_dependency_with_modules_but_no_mapped_test_is_pending(check_git_workspace, check_environment):
    from tests.check_inputs import discover, save_snapshot, snapshot
    from tests.check_plan import make_plan
    root, archive, config, state = check_git_workspace
    # A shared test helper imports a distribution that no test reaches.
    (root / 'tests/support.py').write_text('import requests\n\n\ndef build():\n    return requests\n',
                                          encoding='utf-8')
    save_snapshot(state, snapshot(root, archive, config))
    check_environment['packages'] = [['anyio', '4.15.1'], ['pytest', '9.1.1'], ['requests', '2.34.2']]
    changes, _, _, _ = discover(root, archive, config, state)
    plan = make_plan(root, archive, config, changes, 'quick')
    # A known module that reaches no test is unresolved impact, not a complete run.
    assert plan.level == 'quick' and not plan.frontend and not plan.targets
    assert 'requests' in plan.unknown_dependencies
    assert any('requests' in note and 'maps to no test' in note for note in plan.notes)


def test_check_results_keep_the_observation_time_when_stamps_are_missing(check_git_workspace):
    from tests.run_checks import record_results, recorded_results
    root, archive, config, state = check_git_workspace
    identity = 'fixture-root::fixture-venv::fixture-python'
    old = {'python': 'old', 'platform': 'p', 'plugins': [], 'packages': []}
    new = {'python': 'new', 'platform': 'p', 'plugins': [], 'packages': []}
    # An observation written before per-selector stamps existed.
    verified = {identity: {'source_root': str(root.resolve()), 'interpreter': {},
                           'observations': [
                               {'environment': old, 'recorded_at': '20260101T000000.000000Z',
                                'selectors': {'tests/test_alpha.py::test_one': 'passed',
                                              'tests/test_beta.py::test_route': 'passed'}},
                               {'environment': new, 'recorded_at': '20260103T000000.000000Z',
                                'selectors': {'tests/test_alpha.py::test_one': 'failed'}}]}}
    # Back in the first environment only the second case runs; the case that did not
    # run keeps the time of its own observation, so the newer failure still wins.
    verified = record_results(verified, identity, root, dict(old, interpreter={}),
                              {'tests/test_beta.py::test_route': 'passed'}, '20260104T000000.000000Z')
    assert recorded_results(verified, identity) == {'tests/test_alpha.py::test_one': 'failed',
                                                    'tests/test_beta.py::test_route': 'passed'}


def test_check_state_is_isolated_between_checkouts_sharing_a_private_root(check_git_workspace, monkeypatch):
    import shutil as _shutil
    from tests import run_checks
    from tests.check_inputs import checkout_state, load_snapshot, save_snapshot, snapshot
    root, archive, config, state = check_git_workspace
    other = root.parent / 'second-checkout'
    _shutil.copytree(root, other)
    save_snapshot(checkout_state(other, archive), snapshot(other, archive, config))
    assert state == checkout_state(root, archive) and checkout_state(root, archive) != checkout_state(other, archive)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'execute', _passing_execute(root, status='failed'))
    assert run_checks.main(['--paths', 'src/gxw/reader.py']) == 7
    failed = json.loads((checkout_state(root, archive) / 'inputs.json').read_text())
    monkeypatch.setattr(run_checks, 'ROOT', other)
    monkeypatch.setattr(run_checks, 'execute', _passing_execute(other))
    assert run_checks.main(['--paths', 'src/gxw/reader.py']) == 0
    passed = json.loads((checkout_state(other, archive) / 'inputs.json').read_text())
    # Each checkout keeps its own observation, failure and owed verification.
    assert failed['root'] == str(root.resolve()) and passed['root'] == str(other.resolve())
    assert failed['pending'] and not passed['pending']
    assert failed['verified'] and passed['verified']
    assert load_snapshot(state, root, archive)['pending'] == failed['pending']
    assert load_snapshot(checkout_state(other, archive), other, archive)['pending'] == []
    assert set(failed['verified']) == set(passed['verified'])


def test_environment_observation_belongs_to_its_own_interpreter_and_venv(check_git_workspace, check_environment):
    from tests.check_inputs import environment_change, observed_environment
    root, archive, config, state = check_git_workspace
    current = {'python': check_environment['python'], 'platform': check_environment['platform'],
               'identity': check_environment['identity'], 'interpreter': check_environment['interpreter'],
               'plugins': list(check_environment['plugins']),
               'packages': [list(row) for row in check_environment['packages']],
               'modules': dict(check_environment['modules'])}
    mine = dict(current)
    other = dict(current, identity='fixture-root::other-venv::other-python',
                 python='Other Python 3.12.0', packages=[['anyio', '4.0.0']])
    previous = {'environment': other, 'environment_identity': other['identity'],
                'environments': {mine['identity']: mine, other['identity']: other}}
    # A -> B -> A is not a new difference: the record of this venv is read again.
    assert observed_environment(previous, mine['identity']) is mine
    assert environment_change(observed_environment(previous, mine['identity']), current, root) is None
    # A venv without its own record is compared as a different environment instead.
    foreign = observed_environment(previous, 'fixture-root::third-venv::third-python')
    assert foreign['identity'] == other['identity']
    assert environment_change(foreign, dict(current, identity='fixture-root::third-venv::third-python'),
                              root)['broad']


def test_environment_plugin_set_change_is_observed_without_a_version_change(check_git_workspace, check_environment):
    from tests.check_inputs import discover
    root, archive, config, state = check_git_workspace
    check_environment['plugins'] = ['_hypothesis_pytestplugin', 'anyio']
    changes, _, notes, _ = discover(root, archive, config, state)
    detail = {change.path: change for change in changes}['<environment>'].detail
    assert detail['plugins'] == {'removed': ['tests'], 'added': []}
    assert detail['broad'] and not detail['packages']['changed']
    assert any('pytest plugin tests disabled' in note for note in notes)


def test_check_partial_owner_run_keeps_the_new_dependency_debt(check_git_workspace, monkeypatch, check_environment):
    from tests import run_checks
    from tests.check_inputs import discover, save_snapshot, snapshot
    root, archive, config, state = check_git_workspace
    (root / 'tests/test_beta.py').write_text('import anyio\n\n\ndef test_route():\n    assert anyio is not None\n',
                                             encoding='utf-8')
    save_snapshot(state, snapshot(root, archive, config))
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    check_environment['packages'] = [['anyio', '4.16.0'], ['pytest', '9.1.1']]
    monkeypatch.setattr(run_checks, 'execute', _passing_execute(root, status='failed'))
    assert run_checks.main(['--paths', 'src/gxw/reader.py']) == 7
    saved = json.loads((state / 'inputs.json').read_text())
    identity = saved['environment_identity']
    # The affected owner was selected but did not pass, so its new debt survives.
    assert 'tests/test_alpha.py' in saved['environment_pending'][identity]['tests']
    assert 'tests/test_beta.py' in saved['environment_pending'][identity]['tests']
    before = json.loads((state / 'inputs.json').read_text())
    before['domain_debt'] = ['compiler']
    (state / 'inputs.json').write_text(json.dumps(before))
    assert run_checks.main(['--level', 'full', '--full-approval', 'user asked for the complete run']) == 7
    saved = json.loads((state / 'inputs.json').read_text())
    # An unsuccessful complete run cannot clear an existing module obligation.
    assert saved['domain_debt'] == ['compiler']


def test_check_state_falls_back_to_the_shared_snapshot_only_for_its_own_checkout(tmp_path):
    from tests.check_inputs import checkout_state, load_snapshot, save_snapshot
    archive = tmp_path / 'private'
    first, second = tmp_path / 'checkout-a', tmp_path / 'checkout-b'
    for directory in (first, second):
        directory.mkdir()
    value = {'version': 1, 'root': str(first.resolve()), 'private_root': str(archive.resolve()),
             'ignored': {}, 'environment': {'python': 'first'}, 'pending': ['tests/test_a.py::test_one'],
             'domain_debt': [], 'verified': {}, 'environment_pending': {'x': {'tests': ['tests/test_a.py']}}}
    save_snapshot(archive / 'local/test-checks', value)
    assert load_snapshot(checkout_state(first, archive), first, archive)['pending'] == \
        ['tests/test_a.py::test_one']
    # Another checkout never inherits it, and writes into its own directory.
    assert load_snapshot(checkout_state(second, archive), second, archive) is None
    save_snapshot(checkout_state(second, archive), dict(value, root=str(second.resolve()),
                                                        environment={'python': 'second'}, pending=[]))
    assert load_snapshot(checkout_state(second, archive), second, archive)['environment']['python'] == 'second'
    assert load_snapshot(checkout_state(first, archive), first, archive)['environment']['python'] == 'first'


def test_check_dry_run_does_not_run_tests_or_advance_inputs(check_git_workspace, monkeypatch):
    from tests import run_checks
    root, archive, config, state = check_git_workspace
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', lambda *_args, **_kwargs: pytest.fail('dry run executed tests'))
    before = {path.relative_to(state): path.read_bytes() for path in state.rglob('*') if path.is_file()}
    assert run_checks.main(['--dry-run', '--paths', 'web/src/App.tsx']) == 0
    assert before == {path.relative_to(state): path.read_bytes() for path in state.rglob('*') if path.is_file()}


@pytest.mark.parametrize('status,code,expected', [('passed', 0, 0), ('failed', 7, 7),
                                               ('missing_environment', 2, 2), ('incomplete', 0, 3)])
def test_check_receipts_distinguish_failures_missing_environment_and_skips(check_git_workspace, monkeypatch, status, code, expected):
    from tests import run_checks
    root, archive, config, state = check_git_workspace
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    before = json.loads((state / 'inputs.json').read_text())
    pending = ['tests/test_alpha.py::test_one'] if status == 'incomplete' else []
    monkeypatch.setattr(run_checks, 'execute', lambda *_args, **_kwargs: [
        {'name': 'python', 'status': status, 'exit_code': code, 'pending': pending}])
    assert run_checks.main(['--paths', 'src/gxw/diagnostic_events.py']) == expected
    record = json.loads(next((state / 'runs').glob('*/result.json')).read_text(encoding='utf-8'))
    assert record['status'] == status and record['selection']['deferred']
    saved = json.loads((state / 'inputs.json').read_text())
    # Nothing here reported a case result, so no verification is claimed and the
    # observed environment never advances the source inputs it did not check.
    assert saved.get('verified', {}) == before.get('verified', {})
    assert saved.get('ignored') == before.get('ignored')
    if status != 'passed':
        # No case result proves which selected test ran: all stay incomplete.
        assert set(saved['pending']) == set(record['selection']['targets'])
    else:
        assert saved.get('pending', []) == before.get('pending', [])


def test_check_failed_run_records_the_environment_without_advancing_ignored_inputs(check_git_workspace, monkeypatch):
    from tests import run_checks
    from tests.check_inputs import discover
    root, archive, config, state = check_git_workspace
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute',
                        lambda *_args, **_kwargs: [
                            {'name': 'python', 'status': 'failed', 'exit_code': 7,
                             'results': {'tests/test_alpha.py::test_one': 'failed'},
                             'pending': ['tests/test_alpha.py::test_one']}])
    # An ignored test outside the selected owner changes while that run fails.
    (root / 'tests/test_beta.py').write_text('def test_route():\n    assert 1 == 2\n', encoding='utf-8')
    assert run_checks.main(['--paths', 'src/gxw/diagnostic_events.py']) == 7
    saved = json.loads((state / 'inputs.json').read_text())
    assert saved['environment']['python'] == 'Fixture Python 3.13.0'
    assert saved['environment_identity'] == 'fixture-root::fixture-venv::fixture-python'
    assert saved['pending'] == ['tests/test_alpha.py::test_one']
    # Observation consumes the event, but never the unexecuted verification.
    changes, _, _, _ = discover(root, archive, config, state)
    assert 'tests/test_beta.py' not in {change.path for change in changes}
    assert 'tests/test_beta.py' in saved['source_pending'][saved['environment_identity']]


def test_check_inputs_changed_during_run_do_not_advance_baseline(check_git_workspace, monkeypatch):
    from tests import run_checks
    from tests.check_inputs import discover
    root, archive, config, state = check_git_workspace
    before = json.loads((state / 'inputs.json').read_text())
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    def execute(*_args, **_kwargs):
        (root / 'src/b.py').write_text('b = 99\n')
        return [{'name': 'python', 'status': 'passed', 'exit_code': 0}]
    monkeypatch.setattr(run_checks, 'execute', execute)
    assert run_checks.main(['--paths', 'src/gxw/diagnostic_events.py']) == 3
    saved = json.loads((state / 'inputs.json').read_text())
    # The run keeps its own bookkeeping, but no observation advances and nothing it
    # reported becomes a verification for the source that changed mid-run.
    for key in ('ignored', 'observed_inputs', 'environments'):
        assert saved.get(key) == before.get(key), key
    assert 'src/b.py' in {change.path for change in discover(root, archive, config, state)[0]}


def test_source_fixture_cache_reuses_reads_and_parse_and_invalidates_changed_files(tmp_path, monkeypatch):
    from tests.fixture_cache import SourceCache
    path = tmp_path / 'sample.py'
    path.write_text('value = 1\n')
    reads, parses = [], []
    original_read, original_parse = Path.read_bytes, ast.parse
    def read(file):
        reads.append(file)
        return original_read(file)
    def parse(*args, **kwargs):
        parses.append(args[0])
        return original_parse(*args, **kwargs)
    monkeypatch.setattr(Path, 'read_bytes', read)
    monkeypatch.setattr(ast, 'parse', parse)
    cache = SourceCache()
    first = cache.tree(path)
    assert cache.tree(path) is first
    assert len(reads) == len(parses) == 1
    stamp = path.stat().st_mtime_ns
    path.write_text('value = 2\n')
    os.utime(path, ns=(stamp + 1_000_000, stamp + 1_000_000))
    assert cache.tree(path).body[0].value.value == 2
    assert len(reads) == len(parses) == 2
    path.unlink()
    with pytest.raises(FileNotFoundError):
        cache.tree(path)


def test_check_offline_plugin_deselects_live_cases_without_running_them(check_workspace):
    from types import SimpleNamespace
    from tests.run_checks import pytest_collection_modifyitems
    root, _, config = check_workspace
    config['live_tests'] = ['tests/test_alpha.py::test_one']
    (root / 'tests/check_suites.json').write_text(json.dumps(config))
    removed = []
    def item(nodeid, live=False):
        return SimpleNamespace(nodeid=nodeid, get_closest_marker=lambda _: live)
    normal = item('tests/test_beta.py::test_route')
    items = [item('tests/test_alpha.py::test_one[CPU]'), normal, item('tests/test_beta.py::test_dynamic_live', True)]
    pytest_collection_modifyitems(SimpleNamespace(rootpath=root, hook=SimpleNamespace(
        pytest_deselected=lambda **kwargs: removed.extend(kwargs['items']))), items)
    assert items == [normal]
    assert len(removed) == 2


def test_check_xml_retains_skipped_variants_and_class_methods_as_pending(tmp_path):
    from tests.run_checks import python_results
    xml = tmp_path / 'result.xml'
    xml.write_text('<testsuites><testsuite>'
                   '<testcase classname="tests.test_alpha" name="test_one[A]" />'
                   '<testcase classname="tests.test_alpha" name="test_one[B]"><skipped /></testcase>'
                   '<testcase classname="tests.test_beta.Checks" name="test_route[C]"><failure /></testcase>'
                   '</testsuite></testsuites>')
    counts, pending = python_results(xml)
    assert counts == {'passed': 1, 'failed': 1, 'skipped': 1}
    assert pending == ['tests/test_alpha.py::test_one', 'tests/test_beta.py::Checks::test_route']


def test_check_offline_plugin_real_collection_never_executes_live_cases(check_workspace):
    root, _, config = check_workspace
    config['live_tests'] = ['tests/test_alpha.py::test_one']
    (root / 'tests/check_suites.json').write_text(json.dumps(config))
    (root / 'pytest.ini').write_text('[pytest]\n')
    (root / 'tests/test_alpha.py').write_text(
        'def test_one():\n    raise AssertionError("real GX case was executed")\n'
        '\ndef test_two():\n    assert True\n')
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'tests.run_checks',
                             '-c', str(root / 'pytest.ini'), str(root / 'tests/test_alpha.py')],
                            cwd=root, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '1 passed, 1 deselected' in result.stdout


def test_check_pending_environment_cases_are_reselected_without_new_changes(check_git_workspace, monkeypatch, capsys):
    from tests import run_checks
    root, archive, _, state = check_git_workspace
    old = json.loads((state / 'inputs.json').read_text())
    old['pending'] = ['tests/test_alpha.py::test_one']
    (state / 'inputs.json').write_text(json.dumps(old))
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    assert run_checks.main(['--dry-run']) == 0
    assert 'tests/test_alpha.py::test_one: previously failed or missing environment' in capsys.readouterr().out


def test_check_successful_process_without_junit_is_a_failed_validation(tmp_path):
    from tests.run_checks import run_step
    result = run_step('missing-junit', [sys.executable, '-c', 'print("no receipt")'], tmp_path,
                      tmp_path, xml=tmp_path / 'missing.xml')
    assert result['status'] == 'failed' and result['exit_code'] != 0


def test_check_snapshot_has_one_writer_and_lock_is_reusable_after_exit(tmp_path):
    from tests.check_inputs import acquire_runner_lock
    lock = acquire_runner_lock(tmp_path)
    try:
        with pytest.raises(ValueError, match='Another local check'):
            acquire_runner_lock(tmp_path)
    finally:
        lock.close()
    acquire_runner_lock(tmp_path).close()


@pytest.mark.parametrize('domain_status', ['passed', 'failed', 'incomplete'])
def test_check_quick_snapshot_retains_required_module_acceptance(check_git_workspace, monkeypatch, domain_status):
    from tests import run_checks
    from tests.check_plan import test_functions
    root, archive, _, state = check_git_workspace
    path = root / 'tests/test_alpha.py'
    path.write_bytes(path.read_bytes().replace(b'assert True', b'assert 2 == 2'))
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    selections = []
    status = ['passed']

    def execute(plan, *_args, **_kwargs):
        selections.append(set(plan.targets))
        results = {target.split('::')[0] + '::' + name: 'passed' for target in plan.targets
                   for name in test_functions((root / target.split('::')[0]).read_bytes())}
        pending = []
        if status[0] == 'failed':
            results = {selector: 'failed' for selector in results}
        elif status[0] == 'incomplete':
            results['tests/test_alpha.py::test_one'] = 'skipped'
            pending = ['tests/test_alpha.py::test_one']
        return [{'name': 'python', 'status': status[0], 'exit_code': 7 if status[0] == 'failed' else 0,
                 'pending': pending, 'results': results}]

    monkeypatch.setattr(run_checks, 'execute', execute)
    assert run_checks.main([]) == 0
    assert selections[-1] == {'tests/test_alpha.py::test_two'}
    assert json.loads((state / 'inputs.json').read_text())['domain_debt'] == ['compiler']
    status[0] = domain_status
    assert run_checks.main(['--level', 'domain']) == {'passed': 0, 'failed': 7, 'incomplete': 3}[domain_status]
    assert selections[-1] == {'tests/test_alpha.py'}
    saved = json.loads((state / 'inputs.json').read_text())
    if domain_status == 'failed':
        assert saved['domain_debt'] == ['compiler']
        status[0] = 'passed'
        assert run_checks.main(['--level', 'domain']) == 0
        assert selections[-1] == {'tests/test_alpha.py'}
    elif domain_status == 'incomplete':
        assert saved['pending'] == ['tests/test_alpha.py::test_one']
        status[0] = 'passed'
        assert run_checks.main([]) == 0
        assert selections[-1] == {'tests/test_alpha.py::test_one'}
        latest = max((state / 'runs').glob('*/result.json'))
        receipt = json.loads(latest.read_text(encoding='utf-8'))
        assert 'tests/test_alpha.py::test_two' in receipt['selection']['deferred']
        # A skipped case validated nothing: module acceptance is still owed.
        assert json.loads((state / 'inputs.json').read_text())['domain_debt'] == ['compiler']
        assert run_checks.main(['--level', 'domain']) == 0
        assert selections[-1] == {'tests/test_alpha.py'}
    assert json.loads((state / 'inputs.json').read_text())['domain_debt'] == []


def test_check_unknown_saved_module_owner_stays_pending_confirmation(check_workspace):
    from tests.check_plan import make_plan
    root, archive, config = check_workspace
    plan = make_plan(root, archive, config, [], pending_domains=['retired-owner'])
    # A saved owner this configuration no longer knows is a confirmation item: it is
    # not permission to run everything and the saved obligation is not dropped.
    assert plan.level == plan.requested_level == 'quick'
    assert plan.full_reasons == [] and not plan.frontend
    assert not plan.targets
    messages = ' '.join([*plan.notes, *map(str, plan.unresolved_inputs.items())])
    assert 'retired-owner' in messages


@pytest.mark.parametrize('flag', ['--skip-worktree', '--assume-unchanged'])
def test_check_hidden_tracked_resources_are_not_silently_omitted(check_git_workspace, flag):
    from tests.check_inputs import discover, save_snapshot, snapshot
    root, archive, config, state = check_git_workspace
    _check_git(root, 'update-index', flag, 'src/a.py')
    before = (root / 'src/a.py').read_bytes()
    save_snapshot(state, snapshot(root, archive, config))
    (root / 'src/a.py').write_bytes(b'a = 2\n')
    assert _check_git(root, 'diff', '--name-only', 'HEAD') == ''
    changes, _, _, _ = discover(root, archive, config, state)
    found = {item.path: item for item in changes}
    assert set(found) == {'src/a.py'}
    assert found['src/a.py'].before == before and found['src/a.py'].after == b'a = 2\n'


@pytest.mark.parametrize('cause', ['explicit', 'pending_covers_suite', 'previous_approval', 'blank_approval'])
def test_check_unapproved_full_selection_never_executes_or_advances_inputs(check_git_workspace, monkeypatch, capsys, cause):
    from tests import run_checks
    root, archive, _, state = check_git_workspace
    arguments = ['--level', 'full']
    if cause in ('pending_covers_suite', 'previous_approval'):
        snapshot = json.loads((state / 'inputs.json').read_text())
        if cause == 'pending_covers_suite':
            snapshot['pending'] = ['tests/test_alpha.py', 'tests/test_beta.py']
            arguments = []
        else:
            snapshot['full_regression_approval'] = {'statement': 'permission for a previous fixture run'}
        (state / 'inputs.json').write_text(json.dumps(snapshot))
    elif cause == 'blank_approval':
        arguments += ['--full-approval', '  ']
    original = (state / 'inputs.json').read_bytes()
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', lambda *_: pytest.fail('unapproved full plan executed'))
    assert run_checks.main(arguments) == 2
    assert 'Full regression requires explicit user permission' in capsys.readouterr().err
    assert (state / 'inputs.json').read_bytes() == original
    assert not (state / 'runs').exists()


@pytest.mark.parametrize('cause', ['shared_input', 'unmapped_input', 'missing_base', 'unknown_debt'])
def test_check_unknown_inputs_never_request_full_approval(check_git_workspace, monkeypatch, capsys, cause):
    from tests import run_checks
    root, archive, _, state = check_git_workspace
    arguments = []
    if cause == 'shared_input':
        arguments = ['--paths', 'pytest.ini']
    elif cause == 'unmapped_input':
        arguments = ['--paths', 'src/unmapped.py']
    elif cause == 'missing_base':
        _check_git(root, 'update-ref', '-d', 'refs/remotes/origin/main')
    else:
        value = json.loads((state / 'inputs.json').read_text())
        value['domain_debt'] = ['retired-owner']
        (state / 'inputs.json').write_text(json.dumps(value))
    before = json.loads((state / 'inputs.json').read_text())
    plans = []

    def execute(plan, *_args, **_kwargs):
        plans.append(plan)
        return [{'name': 'python', 'status': 'passed', 'exit_code': 0, 'results': {}}]

    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', execute)
    assert run_checks.main(arguments) == 0
    assert 'Full regression requires explicit user permission' not in capsys.readouterr().err
    assert all(plan.level != 'full' and not plan.full_reasons for plan in plans)
    saved = json.loads((state / 'inputs.json').read_text())
    # Nothing reported a case result, so no verification was claimed for the input.
    assert saved.get('verified', {}) == before.get('verified', {})
    if cause == 'unmapped_input':
        assert 'src/unmapped.py' in saved['unresolved_inputs']
    if cause == 'unknown_debt':
        assert 'retired-owner' in saved['domain_debt']


def test_check_full_dry_run_needs_no_permission_and_writes_nothing(check_git_workspace, monkeypatch, capsys):
    from tests import run_checks
    root, archive, _, state = check_git_workspace
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', lambda *_: pytest.fail('dry run executed a full plan'))
    original = {path.relative_to(state): path.read_bytes() for path in state.rglob('*') if path.is_file()}
    assert run_checks.main(['--level', 'full', '--dry-run']) == 0
    assert 'Level: full;' in capsys.readouterr().out
    assert original == {path.relative_to(state): path.read_bytes() for path in state.rglob('*') if path.is_file()}


def test_check_shared_input_dry_run_stays_in_the_requested_level(check_git_workspace, monkeypatch, capsys):
    from tests import run_checks
    root, archive, _, state = check_git_workspace
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', lambda *_: pytest.fail('dry run executed a plan'))
    original = {path.relative_to(state): path.read_bytes() for path in state.rglob('*') if path.is_file()}
    assert run_checks.main(['--paths', 'pytest.ini', '--dry-run']) == 0
    output = capsys.readouterr().out
    assert 'Level: quick;' in output and '  FULL:' not in output
    assert original == {path.relative_to(state): path.read_bytes() for path in state.rglob('*') if path.is_file()}


def test_check_dry_run_reports_unresolved_inputs_next_to_environment_notes(check_git_workspace, check_environment, monkeypatch, capsys):
    from tests import run_checks
    root, archive, _, state = check_git_workspace
    check_environment['packages'] = [['anyio', '4.15.1'], ['pytest', '9.2.0']]
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', lambda *_args, **_kwargs: pytest.fail('dry run executed tests'))
    original = (state / 'inputs.json').read_bytes()
    assert run_checks.main(['--dry-run', '--level', 'domain', '--paths', 'src/unmapped.py', 'pytest.ini']) == 0
    output = capsys.readouterr().out
    assert 'Requested level: domain\nLevel: domain;' in output
    # No complete-run cause is reported any more, and the unresolved input is named.
    assert '  FULL:' not in output
    pending = [line for line in output.splitlines() if 'src/unmapped.py' in line]
    assert pending and 'unresolved' in pending[0]
    # The dependency note stays a note: it never claims the level escalated.
    assert 'Dependency/runtime environment changed' in output
    assert 'does not request full validation' in output
    assert (state / 'inputs.json').read_bytes() == original


def test_check_dry_run_labels_complete_scope_without_claiming_level_escalation(check_git_workspace, monkeypatch, capsys):
    from tests import run_checks
    root, archive, _, state = check_git_workspace
    value = json.loads((state / 'inputs.json').read_text())
    value['pending'] = ['tests/test_alpha.py', 'tests/test_beta.py']
    (state / 'inputs.json').write_text(json.dumps(value))
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', lambda *_args, **_kwargs: pytest.fail('dry run executed tests'))
    assert run_checks.main(['--dry-run']) == 0
    output = capsys.readouterr().out
    assert 'Level: quick;' in output
    assert 'Full execution scope:' in output
    assert '  FULL:' not in output


def test_check_full_permission_is_forwarded_and_recorded_only_for_that_invocation(check_git_workspace, monkeypatch):
    from tests import run_checks
    root, archive, _, state = check_git_workspace
    permission = 'Fixture user explicitly permits this isolated complete suite.'
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    def execute(plan, source, receipt, *, full_approval):
        assert source == root and plan.level == 'full'
        assert set(plan.targets) == {'tests/test_alpha.py', 'tests/test_beta.py'}
        assert full_approval == permission
        return [{'name': 'python', 'status': 'passed', 'exit_code': 0}]
    monkeypatch.setattr(run_checks, 'execute', execute)
    assert run_checks.main(['--level', 'full', '--full-approval', permission]) == 0
    receipt = json.loads(next((state / 'runs').glob('*/result.json')).read_text(encoding='utf-8'))
    assert receipt['full_regression_approval'] == {'source': 'explicit_user_permission', 'statement': permission}
    assert receipt['selection']['requested_level'] == 'full'
    assert receipt['selection']['full_reasons'] == ['explicit full offline validation']
    assert 'full_regression_approval' not in json.loads((state / 'inputs.json').read_text())
    assert run_checks.main(['--level', 'full']) == 2  # No saved permission carries into another call.


@pytest.mark.parametrize('level', ['full', 'quick', 'domain'])
def test_check_execution_boundary_rejects_full_scope_without_permission(check_workspace, monkeypatch, tmp_path, level):
    from tests import run_checks
    from tests.check_plan import Plan
    root, _, _ = check_workspace
    plan = Plan(level, targets={'tests/test_alpha.py': [], 'tests/test_beta.py': []})
    monkeypatch.setattr(run_checks, 'run_step', lambda *_args, **_kwargs: pytest.fail('unapproved subprocess launched'))
    with pytest.raises(ValueError, match='explicit user permission'):
        run_checks.execute(plan, root, tmp_path)


def test_check_execution_passes_full_permission_explicitly_to_pytest(check_workspace, monkeypatch, tmp_path):
    from tests import run_checks
    from tests.check_plan import Plan
    root, _, _ = check_workspace
    permission = 'Fixture user permits only this execution.'
    commands = []
    def run_step(name, command, *_args, **_kwargs):
        commands.append(command)
        return {'name': name, 'status': 'passed', 'exit_code': 0}
    monkeypatch.setattr(run_checks, 'run_step', run_step)
    result = run_checks.execute(Plan('full', targets={'tests/test_alpha.py': []}), root, tmp_path,
                                full_approval=permission)
    assert result == [{'name': 'python', 'status': 'passed', 'exit_code': 0}]
    assert commands[0][-2:] == ['--full-approval', permission]


@pytest.mark.parametrize('scope,permitted', [
    ('default', False), ('directory', False), ('repository', False), ('files', False),
    ('functions', False), ('alternate_root', False), ('late_plugin', False),
    ('environment_permission', False), ('configuration_permission', False),
    ('ci_environment', False), ('blank_permission', False),
    ('file', True), ('function', True), ('collect_only', True),
    ('fixture_permission', True), ('joined_permission', True),
])
def test_check_real_pytest_requires_per_invocation_permission_before_full_collection(check_workspace, scope, permitted):
    root, _, _ = check_workspace
    for name, functions in [('alpha', ('one', 'two')), ('beta', ('route',))]:
        (root / f'tests/test_{name}.py').write_text(
            'from pathlib import Path\nPath("collected.txt").touch()\n\n' + ''.join(
                f'def test_{function}():\n    Path("executed.txt").touch()\n\n' for function in functions),
            encoding='utf-8')
    options = '-p tests.check_approval'
    arguments = []
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    env.pop('PYTEST_ADDOPTS', None)
    if scope == 'directory':
        arguments = ['tests']
    elif scope == 'repository':
        arguments = ['.']
    elif scope == 'files':
        arguments = [str(root / 'tests/test_alpha.py'), str(root / 'tests/test_beta.py')]
    elif scope == 'functions':
        arguments = ['tests/test_alpha.py::test_one', 'tests/test_alpha.py::test_two',
                     'tests/test_beta.py::test_route']
    elif scope == 'alternate_root':
        (root / 'external-root').mkdir()
        arguments = ['--rootdir', str(root / 'external-root'), 'tests']
    elif scope == 'late_plugin':
        # A root directory traversal can discover conftest after sessionstart.
        (root / 'tests/conftest.py').write_text("pytest_plugins = ['tests.check_approval']\n", encoding='utf-8')
        arguments = ['-o', 'addopts=', '.']
    elif scope == 'environment_permission':
        env['PYTEST_ADDOPTS'] = '--full-approval=stale-environment-permission'
    elif scope == 'configuration_permission':
        options += ' --full-approval=stale-configuration-permission'
    elif scope == 'ci_environment':
        env.update(CI='true', GITHUB_ACTIONS='true')
    elif scope == 'blank_permission':
        arguments = ['--full-approval', '  ']
    elif scope == 'file':
        arguments = ['tests/test_alpha.py']
    elif scope == 'function':
        arguments = ['tests/test_alpha.py::test_one']
    elif scope == 'collect_only':
        arguments = ['--collect-only']
    elif scope == 'fixture_permission':
        # This permission covers only the three canaries in this temporary directory.
        arguments = ['--full-approval', 'Fixture user authorizes these three canaries only.']
    elif scope == 'joined_permission':
        arguments = ['--full-approval=Fixture user authorizes these three canaries only.']
    (root / 'pytest.ini').write_text('[pytest]\naddopts = ' + options + '\ntestpaths = tests\n', encoding='utf-8')
    result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'pytest', '-q', *arguments],
                            cwd=root, env=env, capture_output=True, text=True, encoding='utf-8', timeout=30)
    output = result.stdout + result.stderr
    assert result.returncode == (0 if permitted else 4), output
    if not permitted:
        assert 'Full regression requires explicit user permission' in output
        if scope != 'late_plugin':
            assert not (root / 'collected.txt').exists(), output
        assert not (root / 'executed.txt').exists()
    elif scope == 'collect_only':
        assert '3 tests collected' in output and not (root / 'executed.txt').exists()
    else:
        assert (root / 'executed.txt').is_file()
        assert ('3 passed' if scope in ('fixture_permission', 'joined_permission') else 'passed') in output
        if scope in ('fixture_permission', 'joined_permission'):
            assert 'Fixture user authorizes these three canaries only.' in output


# NOTE on the persistence contract these cases pin: ``unresolved_inputs`` and
# ``source_pending`` belong to every saving path, not only to the branch where
# nothing was executable. They are written with the same snapshot that records
# the observation; a failed/skipped/unexecuted case
# never discharges debt, and a new source impact is owed by every known environment
# while a pass clears only the selectors that environment actually ran.
def test_check_unresolved_input_is_observed_once_and_stays_pending(check_git_workspace, monkeypatch):
    """An unknown input next to a local source change: pending twice, one event."""
    from base64 import b64decode
    from tests import run_checks
    from tests.check_inputs import discover
    root, archive, config, state = check_git_workspace
    (root / 'src/gxw').mkdir()
    (root / 'src/gxw/ladder_program.py').write_text('value = 1\n', encoding='utf-8')
    (root / 'src/unmapped.py').write_text('value = 0\n', encoding='utf-8')
    assert 'src/unmapped.py' in {change.path for change in discover(root, archive, config, state)[0]}
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', _passing_execute(root))
    assert run_checks.main(['--paths', 'src/gxw/ladder_program.py']) == 0
    saved = json.loads((state / 'inputs.json').read_text())
    # The unmapped input is observed and kept as a confirmation, never as a result.
    assert 'src/unmapped.py' in saved['unresolved_inputs']
    assert b64decode(saved['observed_inputs']['src/unmapped.py']) == (root / 'src/unmapped.py').read_bytes()
    changes, _, _, _ = discover(root, archive, config, state)
    # The same content is not a new event on the next round, for either input.
    assert 'src/unmapped.py' not in {change.path for change in changes}
    assert 'src/gxw/ladder_program.py' not in {change.path for change in changes}
    assert run_checks.main(['--paths', 'src/gxw/ladder_program.py']) == 0
    again = json.loads((state / 'inputs.json').read_text())
    assert 'src/unmapped.py' in again['unresolved_inputs']
    assert again['observed_inputs']['src/unmapped.py'] == saved['observed_inputs']['src/unmapped.py']
    # An explicit path can still select an input that is no longer reported as new.
    forced = discover(root, archive, config, state, paths=['src/unmapped.py'])[0]
    assert 'src/unmapped.py' in {change.path for change in forced}


def test_check_source_verification_keeps_the_unrun_owner_and_function_scope(check_workspace):
    from tests.check_inputs import Change
    from tests.check_plan import make_plan
    root, archive, config = check_workspace
    plan = make_plan(root, archive, config, [Change('src/gxw/reader.py', b'old', b'new')], 'quick',
                     source_pending=['tests/test_beta.py::test_route'])
    assert plan.level == 'quick' and plan.full_reasons == []
    # A pending source debt never widens a quick function scope into whole files.
    assert set(plan.targets) == {'tests/test_alpha.py'}
    assert 'tests/test_beta.py' not in plan.targets
    assert 'tests/test_beta.py::test_route' in plan.source_verification
    assert 'tests/test_beta.py' in plan.deferred


def test_check_local_pass_clears_only_its_own_source_debt(check_git_workspace, monkeypatch):
    from tests import run_checks
    root, archive, _, state = check_git_workspace
    identity = json.loads((state / 'inputs.json').read_text())['environment_identity']
    value = json.loads((state / 'inputs.json').read_text())
    value['source_pending'] = {identity: ['tests/test_beta.py::test_route']}
    (state / 'inputs.json').write_text(json.dumps(value))
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', _passing_execute(root))
    # The local owner runs and passes; the owner that did not run keeps its debt.
    assert run_checks.main(['--paths', 'src/gxw/reader.py']) == 0
    saved = json.loads((state / 'inputs.json').read_text())
    assert saved['source_pending'][identity] == ['tests/test_beta.py::test_route']
    assert run_checks.main(['--paths', 'src/gxw/diagnostic_events.py']) == 0
    assert not json.loads((state / 'inputs.json').read_text()).get('source_pending', {}).get(identity)


def test_check_source_debt_is_isolated_per_identity(check_git_workspace, monkeypatch, check_environment):
    from tests import run_checks
    root, archive, _, state = check_git_workspace
    first = 'fixture-root::fixture-venv::fixture-python'
    other = 'fixture-root::other-venv::fixture-python2'
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', _passing_execute(root))
    (root / 'src/gxw').mkdir()
    reader = root / 'src/gxw/reader.py'
    reader.write_text('value = 1\n', encoding='utf-8')
    assert run_checks.main(['--paths', 'src/gxw/reader.py']) == 0
    check_environment.update({'identity': other,
                              'interpreter': {'executable': 'fixture-python2', 'venv': 'other-venv'}})
    assert run_checks.main(['--paths', 'src/gxw/reader.py']) == 0
    reader.write_text('value = 2\n', encoding='utf-8')
    monkeypatch.setattr(run_checks, 'execute', _passing_execute(root, status='failed'))
    assert run_checks.main(['--paths', 'src/gxw/reader.py']) == 7
    saved = json.loads((state / 'inputs.json').read_text())
    # A new source impact is owed by every environment that has not verified it.
    assert saved['source_pending'][other] and saved['source_pending'][first]
    check_environment.update({'identity': first,
                              'interpreter': {'executable': 'fixture-python', 'venv': 'fixture-venv'}})
    monkeypatch.setattr(run_checks, 'execute', _passing_execute(root))
    assert run_checks.main(['--paths', 'src/gxw/reader.py']) == 0
    saved = json.loads((state / 'inputs.json').read_text())
    # The other venv keeps its own debt; what this one ran and passed is cleared.
    assert saved['source_pending'][other]
    assert not set(saved['source_pending'].get(first) or ()) & {
        'tests/test_alpha.py', 'tests/test_alpha.py::test_one', 'tests/test_alpha.py::test_two'}


def test_check_historical_domain_debt_stays_inside_its_own_scope(check_git_workspace, monkeypatch):
    from tests import run_checks
    from tests.check_inputs import Change
    from tests.check_plan import make_plan
    root, archive, config, state = check_git_workspace
    config['rules'].append({'paths': ['src/api/**'], 'domains': ['api'], 'domain_only': True})
    (root / 'tests/check_suites.json').write_text(json.dumps(config), encoding='utf-8')
    (root / 'src/api').mkdir()
    (root / 'src/api/client.py').write_text('value = 1\n', encoding='utf-8')
    change = Change('src/api/client.py', None, b'value = 1\n')
    quick = make_plan(root, archive, config, [change], 'quick', pending_domains=('compiler',))
    # Saved module debt never adds an owner outside the explicit scope.
    assert set(quick.targets) == {'tests/test_beta.py'}
    assert 'tests/test_alpha.py' in quick.deferred
    wider = make_plan(root, archive, config, [change], 'domain', pending_domains=('compiler',))
    assert set(wider.targets) == {'tests/test_beta.py'}
    # Only an unrelated-free, unforced domain run may close the old known owner.
    own = make_plan(root, archive, config, [], 'domain', pending_domains=('compiler',))
    assert set(own.targets) == {'tests/test_alpha.py'}
    value = json.loads((state / 'inputs.json').read_text())
    value['domain_debt'] = ['compiler']
    (state / 'inputs.json').write_text(json.dumps(value))
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', _passing_execute(root))
    assert run_checks.main(['--paths', 'src/api/client.py']) == 0
    assert 'compiler' in json.loads((state / 'inputs.json').read_text())['domain_debt']


def test_check_historical_failures_are_retried_only_inside_the_selected_scope(check_git_workspace, monkeypatch):
    from tests import run_checks
    root, archive, config, state = check_git_workspace
    config['rules'].append({'paths': ['src/api/**'], 'domains': ['api'], 'domain_only': True})
    (root / 'tests/check_suites.json').write_text(json.dumps(config), encoding='utf-8')
    (root / 'src/api').mkdir()
    (root / 'src/api/client.py').write_text('value = 1\n', encoding='utf-8')
    value = json.loads((state / 'inputs.json').read_text())
    value['pending'] = ['tests/test_alpha.py::test_one']
    (state / 'inputs.json').write_text(json.dumps(value))
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', _passing_execute(root))

    def last_targets():
        receipt = json.loads(max((state / 'runs').glob('*/result.json')).read_text(encoding='utf-8'))
        return set(receipt['selection']['targets'])

    assert run_checks.main(['--paths', 'src/api/client.py']) == 0
    # An old failure is not added to a scope that already has its own selection.
    assert not [target for target in last_targets() if target.startswith('tests/test_alpha.py')]
    assert json.loads((state / 'inputs.json').read_text())['pending'] == ['tests/test_alpha.py::test_one']
    # Without a new scope or an explicit path the old failure is retried.
    assert run_checks.main([]) == 0
    assert [target for target in last_targets() if target.startswith('tests/test_alpha.py')]


def test_check_unowned_test_is_not_invalidated_again_by_reconsideration(check_workspace):
    from tests.check_inputs import Change
    from tests.check_plan import make_plan
    root, archive, config = check_workspace
    path = 'tests/test_extra.py'
    (root / path).write_text('def test_extra():\n    assert True\n', encoding='utf-8')
    first = make_plan(root, archive, config, [Change(path, None, (root / path).read_bytes())])
    assert path in first.source_changes and path in first.unresolved_inputs
    again = make_plan(root, archive, config, [], unresolved_inputs=first.unresolved_inputs)
    assert not again.targets and not again.source_changes
    assert path in again.unresolved_inputs
    # Supplying the missing owner converts the unknown association to verification
    # debt without scheduling it outside the current scope.
    registry_path = root / 'tests/README.md'
    registry_path.write_text(registry_path.read_text(encoding='utf-8').replace(
        '<!-- TEST-BOUNDARY-REGISTRY-END -->',
        '### Extra\n| `tests/test_extra.py` | extra |\n<!-- TEST-BOUNDARY-REGISTRY-END -->'), encoding='utf-8')
    config['domains']['extra'] = {'heading': 'Extra', 'quick': [path]}
    resolved = make_plan(root, archive, config, [], unresolved_inputs=again.unresolved_inputs)
    assert not resolved.targets and path not in resolved.unresolved_inputs
    assert path in resolved.source_changes


def test_check_deleted_registered_test_does_not_block_its_remaining_consumers(check_workspace):
    from tests.check_inputs import Change
    from tests.check_plan import make_plan
    root, archive, config = check_workspace
    path = root / 'tests/test_alpha.py'
    before = path.read_bytes()
    path.unlink()
    plan = make_plan(root, archive, config, [Change('tests/test_alpha.py', before, None)])
    assert plan.level == 'quick' and not plan.full_reasons
    assert set(plan.targets) == {'tests/test_beta.py'}
    assert 'tests/test_alpha.py' in plan.unresolved_inputs
    again = make_plan(root, archive, config, [], unresolved_inputs=plan.unresolved_inputs)
    assert 'tests/test_alpha.py' in again.unresolved_inputs and not again.source_changes


@pytest.mark.parametrize('changed', ['src/demo/reader.py', 'src/demo/__init__.py'])
def test_check_import_associations_cover_literal_dynamic_calls_and_package_initialization(check_workspace, changed):
    from tests.check_plan import consumers
    root, archive, _ = check_workspace
    (root / 'src/demo').mkdir()
    (root / 'src/demo/reader.py').write_text('value = 1\n', encoding='utf-8')
    (root / 'src/demo/__init__.py').write_text('', encoding='utf-8')
    (root / 'tests/test_alpha.py').write_text(
        "from importlib import import_module\nreader = import_module('.reader', 'demo')\n"
        'def test_one():\n    assert reader.value == 1\n', encoding='utf-8')
    (root / 'tests/test_beta.py').write_text('def test_route():\n    assert True\n', encoding='utf-8')
    assert consumers(root, archive, changed) == {'tests/test_alpha.py'}


def test_check_temporary_resource_basename_does_not_invent_a_test_association(check_workspace):
    from tests.check_inputs import Change
    from tests.check_plan import make_plan
    root, archive, config = check_workspace
    (root / 'tests/test_alpha.py').write_text(
        "def test_one():\n    assert 'project.gxw'\n", encoding='utf-8')
    path = 'tmp_export/project.gxw'
    plan = make_plan(root, archive, config, [Change(path, None, b'output')])
    assert not plan.targets and not plan.source_changes and path in plan.unresolved_inputs


def test_check_resolved_dependency_debt_is_saved_without_adding_an_unselected_owner(check_git_workspace, monkeypatch):
    from tests import run_checks
    from tests.check_inputs import save_snapshot, snapshot
    root, archive, config, state = check_git_workspace
    (root / 'tests/test_beta.py').write_text(
        'import document_reader\n\ndef test_route():\n    assert document_reader\n', encoding='utf-8')
    value = snapshot(root, archive, config)
    identity = value['environment_identity']
    value['environment_pending'] = {identity: {'tests': [], 'unknown': ['document-tools', 'unlocated']}}
    save_snapshot(state, value)
    monkeypatch.setattr('tests.check_plan.dependency_modules',
                        lambda _root, names: {name: ['document_reader'] if name == 'document-tools' else []
                                              for name in names})
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', lambda *_a, **_k: pytest.fail('unselected owner executed'))
    assert run_checks.main([]) == 0
    saved = json.loads((state / 'inputs.json').read_text())
    assert saved['environment_pending'][identity]['unknown'] == ['unlocated']
    assert saved['environment_pending'][identity]['tests'] == ['tests/test_beta.py']
    monkeypatch.setattr(run_checks, 'execute', _passing_execute(root))
    assert run_checks.main(['--paths', 'src/gxw/reader.py']) == 0
    saved = json.loads((state / 'inputs.json').read_text())
    assert saved['environment_pending'][identity]['tests'] == ['tests/test_beta.py']
    assert run_checks.main(['--paths', 'tests/test_beta.py']) == 0
    saved = json.loads((state / 'inputs.json').read_text())
    assert saved['environment_pending'][identity]['tests'] == []
    assert saved['environment_pending'][identity]['unknown'] == ['unlocated']


def test_check_observation_detects_revert_and_deletion_without_repeating_either(check_git_workspace, monkeypatch):
    from tests import run_checks
    from tests.check_inputs import discover
    root, archive, config, state = check_git_workspace
    path = root / 'src/a.py'
    original = path.read_bytes()
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', lambda *_a, **_k: pytest.fail('unmapped input executed'))
    # The initial Git blob uses LF; subsequent observations preserve disk bytes.
    before = b'a = 1\n'
    for content in (b'a = 2\n', original, None):
        if content is None:
            path.unlink()
        else:
            path.write_bytes(content)
        change = next(c for c in discover(root, archive, config, state)[0] if c.path == 'src/a.py')
        assert change.before == before and change.after == content
        assert run_checks.main([]) == 0
        assert 'src/a.py' not in {c.path for c in discover(root, archive, config, state)[0]}
        assert 'src/a.py' in json.loads((state / 'inputs.json').read_text())['unresolved_inputs']
        before = content


def test_check_partial_environment_scope_does_not_claim_the_whole_file(check_workspace):
    from tests.check_inputs import Change
    from tests.check_plan import make_plan
    root, archive, config = check_workspace
    plan = make_plan(root, archive, config, [Change('src/gxw/diagnostic_events.py', b'old', b'new')],
                     environment_pending={'tests': ['tests/test_alpha.py']})
    assert 'tests/test_alpha.py::test_one' in plan.targets and 'tests/test_alpha.py' not in plan.targets
    assert 'the rest of this file stays owed' in plan.verification['tests/test_alpha.py']


def test_check_other_environment_pass_does_not_erase_this_environments_failure(check_git_workspace, monkeypatch, check_environment):
    from tests import run_checks
    root, archive, _, state = check_git_workspace
    first = check_environment['identity']
    monkeypatch.setattr(run_checks, 'ROOT', root)
    monkeypatch.setattr(run_checks, 'private_root', lambda: archive)
    monkeypatch.setattr(run_checks, 'execute', _passing_execute(root, status='failed'))
    assert run_checks.main(['--paths', 'src/gxw/reader.py']) == 7
    check_environment['identity'] = 'another-venv'
    monkeypatch.setattr(run_checks, 'execute', _passing_execute(root))
    assert run_checks.main(['--paths', 'src/gxw/reader.py']) == 0
    check_environment['identity'] = first
    monkeypatch.setattr(run_checks, 'execute', _passing_execute(root, status='failed'))
    # No new source input is needed to recover this environment's earlier failure.
    assert run_checks.main([]) == 7
    saved = json.loads((state / 'inputs.json').read_text())
    assert 'tests/test_alpha.py::test_one' in saved['pending']
    assert run_checks.recorded_results(saved['verified'], first)['tests/test_alpha.py::test_one'] == 'failed'
    assert run_checks.recorded_results(saved['verified'], 'another-venv')['tests/test_alpha.py::test_one'] == 'passed'


def test_check_restored_owner_mapping_does_not_schedule_its_old_acceptance(check_workspace):
    from tests.check_inputs import Change
    from tests.check_plan import make_plan
    root, archive, config = check_workspace
    plan = make_plan(root, archive, config, [Change('tests/test_beta.py', None, b'new')],
                     pending_domains=['compiler'],
                     unresolved_inputs={'<owner:compiler>': 'earlier owner was missing'})
    assert not plan.unresolved_inputs and '<owner:compiler>' in plan.resolved_inputs
    assert set(plan.targets) == {'tests/test_beta.py'}
    assert 'tests/test_alpha.py' in plan.deferred
