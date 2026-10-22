"""Projection regressions using the shipped projects, without a native desktop."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import struct
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from gxw.fb_authoring import definition_snapshot  # noqa: E402
from gxw.models import GXWFormatError  # noqa: E402
from gxw.offline_project import read_offline_project  # noqa: E402
from gxw.project_templates import blank_fbd_baseline, blank_ladder_baseline  # noqa: E402
from gxw.project_writer import write_project_replacements  # noqa: E402
from gxw.source_header import source_payload_offset  # noqa: E402
from gxw.token_pou import frame_token_pou  # noqa: E402
from gxw.visible_engineering import _preserved, source_projection  # noqa: E402


_UNALLOCATED = (0xFFFFFFFF, 0xFFFFFFFF)
_STRUCTURED_CONFIGURATION = {
    "name": "CPU_MATRIX.prj",
    "ranges": {"D": (512, 999), "R": (512, 7999), "W": _UNALLOCATED,
               "T": (100, 199), "ST": _UNALLOCATED, "C": (100, 199),
               "M": (3840, 7679), "B": _UNALLOCATED, "P": (2048, 4095),
               "S": (2048, 4095), "T10": (220, 245)},
    "program_capacity": 16000,
}
_FBD_BODY = bytes.fromhex(
    "500000000100000001000000440000000100000000000100000000000500000001"
    "0000002c0000000200000000000000010000000000010000000000010000000000"
    "0000010000000500000000000000"
)


def _structured_projection(body, *, kind=208, rows=()):
    return {
        "cpu": "FX3U/FX3UC",
        "sources": {"FBD_MATRIX": {"kind": kind, "body": body,
                    **({"trailer": bytes(24)} if kind == 208 else {})}},
        "declarations": {
            "Global1.gh": {"name": "Global1", "kind": "global_table", "declarations": []},
            "local:FBD_MATRIX": {"owner": "FBD_MATRIX", "kind": 0x1000000, "rows": list(rows)},
        },
        "structures": {},
        "configuration": deepcopy(_STRUCTURED_CONFIGURATION),
    }


def _replace_source(raw, logical, replacement):
    project = read_offline_project(raw)
    return write_project_replacements(raw, {
        logical: (project.mapping[logical], project.streams[logical], replacement)
    }).data


def _st_project(text):
    """Exercise a C1 source in the captured structured project's real container."""
    raw = blank_fbd_baseline("FX3U")
    source = read_offline_project(raw).streams["FBD_MATRIX.Program.pou"]
    payload = text.encode("utf-16le") + b"\0\0"
    size = 16 + len(payload)
    replacement = (source[:source_payload_offset(source)] + b"\xc1"
                   + struct.pack("<4I", size, size, 1, len(payload) // 2)
                   + payload + bytes(28))
    return _replace_source(raw, "FBD_MATRIX.Program.pou", replacement)


def _generated_ladder(contact):
    from gxw.ladder_program_writer import build_ladder_project
    from gxworks2.csv_export import lower_ladder_program

    logic = {"rungs": [{"branches": [{"inputs": [{"type": "NO", "address": contact}],
                                      "outputs": [{"type": "COIL", "address": "Y0"}]}]}]}
    return build_ladder_project(lower_ladder_program(logic, plc_model="FX3U"),
                                plc_model="FX3U").data


def test_fbd_projection_preserves_all_fields():
    assert source_projection(blank_fbd_baseline("FX3U")) == _structured_projection(_FBD_BODY)


def test_st_projection_preserves_text_labels_and_configuration():
    from gxw.declarations import edit_declarations, parse_declarations, serialize_declarations

    raw = _st_project("enabled := X0;\r\nY0 := enabled;\r")
    logical = "FBD_MATRIX.Labels.lh"
    source = read_offline_project(raw).streams[logical]
    document = edit_declarations(parse_declarations(source, logical_name=logical),
        upserts=[{"name": "enabled", "data_type": "BOOL", "comment": "保持输出"}],
        native_fbd_locals=True)
    raw = _replace_source(raw, logical, serialize_declarations(document))
    expected = _structured_projection("enabled := X0;\nY0 := enabled;\n", kind=193, rows=[{
        "name": "enabled", "data_type": "BOOL", "class_name": "VAR",
        "initial_value": "FALSE", "comment": "保持输出", "kind": "variable",
    }])
    assert source_projection(raw) == expected


def test_blank_ladder_projection_has_body_and_configuration():
    raw = blank_ladder_baseline("FX3U")
    forbidden = AssertionError("ordinary Ladder must not enter structured authoring")
    with patch("gxw.visible_engineering._context", side_effect=forbidden), \
            patch("gxw.visible_engineering.definition_snapshot", side_effect=forbidden):
        result = source_projection(raw)
    assert result == {
        "cpu": "FX3U/FX3UC",
        "sources": {"MAIN": {"kind": 1, "body": b"\x03\x34\x03"}},
        "declarations": {}, "structures": {},
        "configuration": {"name": "FX3U.prj", "program_capacity": 16000,
            "ranges": {name: _UNALLOCATED for name in _STRUCTURED_CONFIGURATION["ranges"]}},
    }
    # Opening a Ladder project does not extend the shared definition editor.
    try:
        definition_snapshot(raw, require_fbd=False)
    except GXWFormatError as error:
        assert "structured ST/FBD source" in str(error)
    else:
        raise AssertionError("definition authoring unexpectedly accepted ordinary Ladder")


def test_ladder_projection_tracks_source_changes():
    first, second = _generated_ladder("X0"), _generated_ladder("X1")
    before, after = source_projection(first), source_projection(second)
    for raw, result in ((first, before), (second, after)):
        source = read_offline_project(raw).streams["MAIN.Program.pou"]
        assert set(result) == {"cpu", "sources", "declarations", "structures", "configuration"}
        assert result["sources"] == {"MAIN": {"kind": 1, "body": frame_token_pou(source).body}}
        assert result["declarations"] == result["structures"] == {}
    assert before["sources"] != after["sources"]
    _preserved(before, after, sources=("MAIN",))
    try:
        _preserved(before, after)
    except GXWFormatError as error:
        assert "sources" in str(error)
    else:
        raise AssertionError("a changed Ladder program was invisible to source preservation")


def test_ladder_projection_preserves_undecoded_records():
    from gxw.token_listing import decode_token_listing

    raw = blank_ladder_baseline("FX3U")
    logical = "MAIN.Program.pou"
    source = read_offline_project(raw).streams[logical]
    framed = frame_token_pou(source)
    body = b"\x03\xfe\x03" + framed.body
    changed = bytearray(source[:framed.body_offset] + body + source[framed.body_end:])
    offset = source_payload_offset(changed)
    size = struct.unpack_from("<I", changed, offset + 1)[0] + 3
    struct.pack_into("<II", changed, offset + 1, size, size)
    assert decode_token_listing(bytes(changed), profile="fx3u", text_encoding="gb18030").gaps
    result = source_projection(_replace_source(raw, logical, bytes(changed)))
    assert result["sources"] == {"MAIN": {"kind": 1, "body": body}}


def test_opaque_st_source_is_not_treated_as_ladder():
    try:
        source_projection(_st_project("Y0 := X0;\0Y1 := X1;"))
    except GXWFormatError as error:
        assert "ST source is opaque" in str(error)
    else:
        raise AssertionError("an unreadable structured source must keep its existing rejection")


def test_native_inspect_reads_ladder_codepage_and_keeps_identity_checks():
    from tempfile import TemporaryDirectory
    from types import SimpleNamespace
    from unittest.mock import Mock

    from gxworks2.engineering_driver import NativeEngineeringDriver
    from gxworks2.engineering_errors import NativeEngineeringDriverError
    from gxworks2.models import GXWorks2Session
    from gxworks2.project_profiles import native_project_profile

    with TemporaryDirectory() as directory:
        path = Path(directory).resolve() / "ladder.gxw"
        path.write_bytes(blank_ladder_baseline("FX3U"))
        session = GXWorks2Session(process_id=123, window_handle=456, title="GX Works2",
            executable="GD2.exe", project_open=True, project_name=str(path),
            process_started="test-session", software_version=NativeEngineeringDriver.VERSION)
        driver = NativeEngineeringDriver.__new__(NativeEngineeringDriver)
        driver.path, driver.cpu, driver.native_ui_language = path, "FX3U/FX3UC", "zh-CN"
        driver._codepage_observation = None
        driver._frame_identity = Mock(return_value=session)
        driver.ui = SimpleNamespace(
            _current_native_project_profile=Mock(return_value=native_project_profile("FX3U")),
            _current_native_project_codepage_observation=Mock(
                return_value={"codepage_candidates": [936, 54936]}))
        with patch("gxworks2.engineering_driver.inspect_native_language", return_value="zh-CN"), \
                patch("gxw.fb_authoring._context",
                      side_effect=AssertionError("reading a codepage must not enter structured authoring")):
            result = driver.inspect()
            assert result["project_codepage"] == 936
            assert result["cpu_menu"] == "FX3U/FX3UC"
            assert result["project_path"] == str(path)

            driver.ui._current_native_project_profile.return_value = native_project_profile("Q03UDV")
            try:
                driver.inspect()
            except NativeEngineeringDriverError as error:
                assert "CPU" in str(error)
            else:
                raise AssertionError("the native CPU must still match the bound Ladder project")

            driver.ui._current_native_project_profile.return_value = native_project_profile("FX3U")
            driver.ui._current_native_project_codepage_observation.return_value = {"codepage_candidates": [932]}
            try:
                driver.inspect()
            except NativeEngineeringDriverError as error:
                assert error.observation["saved_project_codepage"] == 936
            else:
                raise AssertionError("the native codepage must still match the saved Ladder project")


def _library_update_controls(*, selected=(0, 0), enabled=(True, True), close=True):
    """Small control doubles; their IDs deliberately differ from standard dialog IDs."""
    from types import SimpleNamespace

    from gxworks2.engineering_driver import NativeEngineeringDriver
    from gxworks2.engineering_errors import NativeEngineeringDriverError

    def button(text, identity, style, *, checked=0, available=True):
        control = SimpleNamespace(checked=checked)
        control.window_text = lambda: text
        control.control_id = lambda: identity
        control.style = lambda: style
        control.is_visible = lambda: True
        control.is_enabled = lambda: available
        control.get_check_state = lambda: control.checked
        return control

    buttons = [button("PID instructions", 34121, 3, checked=selected[0], available=enabled[0]),
               button("Application functions", 34122, 3, checked=selected[1], available=enabled[1]),
               button("OK", 34123, 1), button("Cancel", 34124, 0)]
    dialog = SimpleNamespace(handle=9100, window_text=lambda: "Library Update Confirmation",
        is_enabled=lambda: True, descendants=lambda **kwargs: buttons)
    state = SimpleNamespace(active=[dialog], clicks=[], close_requested=False, waits=[])
    driver = NativeEngineeringDriver.__new__(NativeEngineeringDriver)
    driver.native_ui_language = "en"
    driver.session = SimpleNamespace(process_id=7100, executable="GD2.exe")
    driver.open_updates = []
    driver._dialog_text = lambda observed: "GX Works2: Library Update Confirmation"
    driver._dialogs = lambda: list(state.active)

    def click(observed, identity):
        assert observed is dialog
        chosen, = [control for control in buttons if control.control_id() == identity]
        assert chosen.is_enabled()
        state.clicks.append(identity)
        if chosen.style() & 0xF in (2, 3):
            chosen.checked = 1 - chosen.checked
        elif chosen is buttons[2]:
            state.close_requested = True
        else:
            raise AssertionError("unexpected dialog action")

    def wait(condition, message):
        completed = bool(condition())
        state.waits.append(completed)
        if not completed and state.close_requested and close:
            state.active.clear()
            completed = bool(condition())
        if not completed:
            raise NativeEngineeringDriverError(message)
        return completed

    driver._click, driver._wait = click, wait
    return driver, dialog, buttons, state


@patch("gxworks2.engineering_driver.inspect_native_language", return_value="en")
def test_library_update_selects_only_enabled_unchecked_controls(_language):
    for selected, enabled, wanted in (
            ((0, 0), (True, True), [34121, 34122, 34123]),
            ((1, 0), (True, False), [34123]),
            ((0, 1), (True, True), [34121, 34123]),
            ((0, 0), (False, True), [34122, 34123])):
        driver, dialog, buttons, state = _library_update_controls(selected=selected, enabled=enabled)
        result = driver._accept_library_update(dialog)
        assert state.clicks == wanted
        assert [button.checked for button in buttons[:2]] == [
            1 if available else value for value, available in zip(selected, enabled)]
        assert result["confirmed"] is True
        assert [option["control_id"] for option in result["options"]] == [34121, 34122]
        assert [option["enabled"] for option in result["options"]] == list(enabled)
        # The final wait first sees the still-open dialog, then sees it disappear.
        assert state.waits[-1] is False
        assert not state.active

    driver, dialog, buttons, state = _library_update_controls(enabled=(True, False))
    buttons[2].is_enabled = lambda: bool(buttons[0].checked)
    assert not buttons[2].is_enabled()
    result = driver._accept_library_update(dialog)
    assert result["confirmed"] is True
    assert state.clicks == [34121, 34123]
    assert buttons[1].checked == 0
    assert not state.active


@patch("gxworks2.engineering_driver.inspect_native_language", return_value="en")
def test_library_update_does_not_click_unrelated_or_invalid_dialogs(language):
    from gxworks2.engineering_errors import NativeEngineeringDriverError

    for changed in ("title", "application", "extra_button", "duplicate_id", "invalid_state"):
        driver, dialog, buttons, state = _library_update_controls()
        if changed == "title":
            dialog.window_text = lambda: "Delete Project Confirmation"
        elif changed == "application":
            driver._dialog_text = lambda observed: "Unrelated application"
        elif changed == "extra_button":
            buttons.append(buttons[-1])
        elif changed == "duplicate_id":
            buttons[1].control_id = buttons[0].control_id
        else:
            buttons[1].checked = 2
        try:
            result = driver._accept_library_update(dialog)
        except NativeEngineeringDriverError:
            assert changed == "invalid_state"
        else:
            assert result is None
            assert changed != "invalid_state"
        assert state.clicks == []
        assert buttons[0].checked == 0

    driver, dialog, _buttons, state = _library_update_controls()
    language.return_value = "ja"
    try:
        driver._accept_library_update(dialog)
    except NativeEngineeringDriverError as error:
        assert "界面语言已经改变" in str(error)
    else:
        raise AssertionError("a changed native language must not receive an update action")
    assert state.clicks == []


@patch("gxworks2.engineering_driver.inspect_native_language", return_value="en")
def test_library_update_cannot_finish_while_confirmation_remains_open(_language):
    from gxworks2.engineering_errors import NativeEngineeringDriverError

    driver, dialog, _buttons, state = _library_update_controls(selected=(1, 1), close=False)
    try:
        driver._accept_library_update(dialog)
    except NativeEngineeringDriverError as error:
        assert "确认尚未完成" in str(error)
        assert state.clicks == [34123]
        assert state.active == [dialog]
        assert state.waits[-1] is False
        assert len(driver.open_updates) == 1
        assert driver.open_updates[0]["confirmation_dispatched"] is True
        assert driver.open_updates[0]["confirmed"] is False
    else:
        raise AssertionError("pressing OK alone must not report a completed library update")


CASES = (test_fbd_projection_preserves_all_fields,
         test_st_projection_preserves_text_labels_and_configuration,
         test_blank_ladder_projection_has_body_and_configuration,
         test_ladder_projection_tracks_source_changes,
         test_ladder_projection_preserves_undecoded_records,
         test_opaque_st_source_is_not_treated_as_ladder,
         test_native_inspect_reads_ladder_codepage_and_keeps_identity_checks,
         test_library_update_selects_only_enabled_unchecked_controls,
         test_library_update_does_not_click_unrelated_or_invalid_dialogs,
         test_library_update_cannot_finish_while_confirmation_remains_open)


if __name__ == "__main__":
    for case in CASES:
        case()
        print("ok", case.__name__)
