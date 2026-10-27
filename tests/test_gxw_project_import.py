"""Native import guards and outcome detection, using no desktop access."""
from types import SimpleNamespace

import pytest

from gxworks2.project_import import GXWProjectAutomation, import_gxw_project


class Window:
    def __init__(self):
        self.title = "GX Works2 - old project"
        self.keys = []

    def set_focus(self):
        pass

    def type_keys(self, keys):
        self.keys.append(keys)

    def window_text(self):
        return self.title

    def is_enabled(self):
        return True


def automation(*, existing_dialog=False, after_dialog=None):
    window = Window()
    events = []
    def submit(dialog, path):
        events.append(path)
        window.title = "GX Works2 ..." + path.name
    ui = SimpleNamespace(
        _native_dialog_handles=lambda _: [7] if existing_dialog or events and after_dialog else [],
        _main_window=lambda _: window,
        _wait_legacy_dialog=lambda *_: object(),
        _set_legacy_file_name=submit,
        _native_dialog_text=lambda _: after_dialog,
    )
    driver = GXWProjectAutomation.__new__(GXWProjectAutomation)
    driver.ui, driver.timeout = ui, 0.2
    return driver, window, events


def test_import_never_types_into_an_existing_dialog(tmp_path):
    driver, window, events = automation(existing_dialog=True)
    result = driver.open_project(object(), tmp_path / "gxw_run.gxw")
    assert result["error_code"] == "gx_modal_open" and not result["success"]
    assert window.keys == events == []


@pytest.mark.parametrize("message", ["是否保存变更？", "文件名的长度超出了可使用的字符数"])
def test_import_does_not_confirm_save_or_error_dialog(tmp_path, message):
    driver, window, events = automation(after_dialog=message)
    result = driver.open_project(object(), tmp_path / "gxw_run.gxw")
    assert result["error_code"] == "gx_requires_attention"
    assert window.keys == ["^o"] and len(events) == 1


def test_open_success_still_leaves_compile_unverified_and_checks_input_hash(tmp_path):
    driver, window, events = automation()
    path = tmp_path / "gxw_run.gxw"
    path.write_bytes(b"input")
    finder = SimpleNamespace(find_running=lambda: object())
    with pytest.raises(ValueError, match="changed before import"):
        import_gxw_project(path, finder=finder, automation=driver, expected_sha256="wrong")
    assert events == []
    result = import_gxw_project(path, finder=finder, automation=driver)
    assert result["success"] and result["gx_compile_status"] == "unverified"
    assert window.keys == ["^o"] and events == [path]
