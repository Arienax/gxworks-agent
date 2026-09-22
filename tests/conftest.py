"""Keep presentation tests independent of the user's saved application locale."""

import pytest

from shared.i18n import set_language


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
