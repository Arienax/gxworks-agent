"""Keep presentation tests independent of the user's saved application locale."""

from tools.offline_inputs import private_path, private_root

import sys

# Research modules are optional offline test inputs, never product imports.
sys.path.extend([str(private_root()), str(private_path('research'))])

import pytest

pytest_plugins = ['tests.check_approval']

from shared.i18n import set_language


@pytest.fixture(scope='session')
def private_manual_registry_state(tmp_path_factory):
    """Frozen pre-migration definitions, only for explicit private-source tests."""
    import zipfile
    from pathlib import Path
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    archive = private_path('research/' + 'evidence/rag-source-definitions-20261016.zip')
    if not archive.is_file():
        pytest.skip('private pre-migration definition archive is unavailable')
    path = tmp_path_factory.mktemp('private-definition-source') / 'instruction_definitions.json.gz'
    with zipfile.ZipFile(archive) as z:
        path.write_bytes(z.read('instruction_definitions.json.gz'))
    names = ('_cpu_contracts', 'definition_inventory', '_definition_owners', 'definition_documents')
    before = {name: getattr(registry, name) for name in (*names, 'language_facts', '_language_features',
                                                       'fact_bundle_version', '_fact_resource')}
    registry._cpu_contracts = dict(registry._cpu_contracts)
    try:
        registry.load_instruction_definitions(path)
        return {name: getattr(registry, name) for name in names}
    finally:
        for name, value in before.items():
            setattr(registry, name, value)


@pytest.fixture
def private_manual_evidence(monkeypatch, private_manual_registry_state):
    """Exercise the retained evidence parser, never the product's default corpus.

    Tests opting in assert original wording/pages and historical extraction.
    Authored runtime tests use the unmodified production path instead.
    """
    from knowledge import core, dense, index_access
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from shared.paths import resource_path
    from pathlib import Path
    path = Path(private_path('resources/knowledge/fx3u_knowledge.sqlite'))
    if not path.is_file():
        pytest.skip('private original evidence corpus is unavailable')
    core._close_thread_connection()
    monkeypatch.setattr(core, '_index_path', lambda: path)
    monkeypatch.setattr(index_access, 'index_path', lambda: path)
    monkeypatch.setattr(dense, '_MODEL_RESOURCE', str(private_path('resources/knowledge/fx3u_dense_lsa.npz')))
    for name, value in private_manual_registry_state.items():
        monkeypatch.setattr(registry, name, dict(value) if isinstance(value, dict) else value)
    yield
    core._close_thread_connection()


@pytest.fixture(autouse=True)
def isolate_application_language():
    # A workbench test may load the user's real saved locale. Do not let that
    # leak into subsequent tests whose default-language expectations are Chinese.
    # Individual i18n tests remain free to select English or Japanese.
    set_language("zh-CN")
    yield
    set_language("zh-CN")


@pytest.fixture(autouse=True)
def reset_model_metadata_cache():
    import sys
    module = sys.modules.get("application.model_detection")
    if module is not None:
        module._cache.clear()
    yield
    module = sys.modules.get("application.model_detection")
    if module is not None:
        module._cache.clear()


@pytest.fixture
def gx_ready(monkeypatch):
    """Provide a deterministic ready GX desktop only to tests that are not testing preflight."""
    import application.execution as execution

    monkeypatch.setattr(
        execution,
        "read_gx_environment",
        lambda: {
            "status": "ready",
            "passed": True,
            "desktop_execution_required": True,
            "gx_works2_running": True,
            "project_open": True,
            "message": "GX Works2 已运行。",
        },
    )
