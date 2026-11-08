# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Native Windows folder chooser for the workspace.

The workbench asks the operating system for the folder before it starts serving,
so the operator uses the same dialog as Windows Explorer instead of a web page or
the legacy tree control. Only the modern IFileOpenDialog is opened here; when the
platform has no desktop session the caller decides what to do instead.
"""
from __future__ import annotations

import ctypes
import json
import sys
from ctypes import wintypes

# Windows constants (documented values, not read from the OS).
CLSCTX_INPROC_SERVER = 0x1
FOS_PATHMUSTEXIST = 0x00000008
FOS_FORCEFILESYSTEM = 0x00000040
FOS_PICKFOLDERS = 0x00000020
SIGDN_FILESYSPATH = 0x80058000
ERROR_CANCELLED_HRESULT = 0x800704C7

CLSID_FileOpenDialog = "{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}"
IID_IFileOpenDialog = "{D57C7288-D4AD-4768-BE02-9D969532D960}"

# IFileDialog vtable slots after IUnknown(3) and IModalWindow(1).
_SLOT_SHOW = 3
_SLOT_SET_OPTIONS = 9
_SLOT_GET_OPTIONS = 10
_SLOT_SET_TITLE = 17
_SLOT_GET_RESULT = 20
# IFileOpenDialog adds GetResults after the IFileDialog slots.
_SLOT_OPEN_GET_RESULTS = 27
# IShellItem: IUnknown(3) then GetDisplayName.
_SLOT_ITEM_DISPLAY_NAME = 5
# IUnknown
_SLOT_RELEASE = 2


class _GUID(ctypes.Structure):
    _fields_ = (("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8))

    @classmethod
    def parse(cls, text: str) -> "_GUID":
        value = cls()
        ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(value))
        return value


def _vtable(pointer: int, slot: int):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return table[slot]


def _invoke(pointer: int, slot: int, *arguments, prototype):
    """Call one COM slot with the signature the interface documents."""
    function = prototype(_vtable(pointer, slot))
    return function(ctypes.c_void_p(pointer), *arguments)


def native_folder_chooser_available() -> bool:
    """True when this process can show the modern Windows folder dialog."""
    return sys.platform == "win32"


def choose_folder(title: str = "选择工作区文件夹", *, probe: bool = False) -> str | None:
    """Open the modern folder dialog and return the chosen path, or None on cancel.

    `probe` builds the dialog and applies the folder options without showing it,
    which is how a headless check proves the COM wiring without a UI.
    """
    if not native_folder_chooser_available():
        raise RuntimeError("native_folder_dialog_unavailable")
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    dialog = ctypes.c_void_p()
    item = ctypes.c_void_p()
    try:
        clsid, iid = _GUID.parse(CLSID_FileOpenDialog), _GUID.parse(IID_IFileOpenDialog)
        created = ole32.CoCreateInstance(
            ctypes.byref(clsid), None, CLSCTX_INPROC_SERVER, ctypes.byref(iid), ctypes.byref(dialog))
        if created != 0 or not dialog.value:
            raise RuntimeError("native_dialog_create_failed")
        # SetOptions replaces the option set, so read it first and add folder mode.
        HRESULT = ctypes.c_long
        get_options = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD))(
            _vtable(dialog.value, _SLOT_GET_OPTIONS))
        current = wintypes.DWORD(0)
        if get_options(dialog, ctypes.byref(current)) == 0:
            options = current.value | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST
            set_options = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, wintypes.DWORD)(
                _vtable(dialog.value, _SLOT_SET_OPTIONS))
            if set_options(dialog, options) != 0:
                raise RuntimeError("native_dialog_options_failed")
        set_title = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_wchar_p)(
            _vtable(dialog.value, _SLOT_SET_TITLE))
        set_title(dialog, title)
        if probe:
            return None
        show = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_void_p)(
            _vtable(dialog.value, _SLOT_SHOW))
        shown = show(dialog, None) & 0xFFFFFFFF
        if shown == ERROR_CANCELLED_HRESULT:
            return None
        if shown != 0:
            raise RuntimeError("native_dialog_show_failed")
        get_result = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
            _vtable(dialog.value, _SLOT_GET_RESULT))
        if get_result(dialog, ctypes.byref(item)) != 0 or not item.value:
            raise RuntimeError("native_dialog_result_failed")
        name = ctypes.c_wchar_p()
        display = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_wchar_p))(
            _vtable(item.value, _SLOT_ITEM_DISPLAY_NAME))
        if display(item, SIGDN_FILESYSPATH, ctypes.byref(name)) != 0:
            raise RuntimeError("native_dialog_path_failed")
        return name.value or None
    finally:
        if item.value:
            release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(_vtable(item.value, _SLOT_RELEASE))
            release(item)
        if dialog.value:
            release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(_vtable(dialog.value, _SLOT_RELEASE))
            release(dialog)
        ole32.CoUninitialize()


def main(argv=None) -> int:
    """Standalone entry point: print one JSON result so a child process can report it."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    probe = "--probe" in arguments
    try:
        chosen = choose_folder(probe=probe)
    except Exception as error:  # the caller decides how to degrade
        print(json.dumps({"error": str(error)}, ensure_ascii=True))
        return 1
    if probe:
        print(json.dumps({"probe": "ok"}, ensure_ascii=True))
        return 0
    print(json.dumps({"path": chosen, "cancelled": chosen is None}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
