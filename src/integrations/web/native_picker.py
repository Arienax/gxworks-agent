# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

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


VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
WS_POPUP = 0x80000000
_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                              wintypes.WPARAM, wintypes.LPARAM)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = (("style", ctypes.c_uint), ("lpfnWndProc", _WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR))


_owner_class_registered = False


def _pump_alt() -> None:
    """Synthesize one Alt press so Windows accepts our foreground request.

    A process started in the background is not allowed to take the foreground, but
    a synthetic key event makes the shell treat the next raise as user-initiated.
    """
    user32 = ctypes.windll.user32
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)


def _create_owner() -> int:
    """Create a hidden window that owns the dialog, so it has something to raise."""
    global _owner_class_registered
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    instance = kernel32.GetModuleHandleW(None)
    if not _owner_class_registered:
        procedure = _WNDPROC(("DefWindowProcW", user32))
        definition = _WNDCLASSW(0, procedure, 0, 0, instance, None, None, None, None,
                                "GXWorksAgentPickerHost")
        user32.RegisterClassW(ctypes.byref(definition))
        # Keep the callback alive for the lifetime of the process.
        _create_owner.procedure = procedure
        _owner_class_registered = True
    return user32.CreateWindowExW(0, "GXWorksAgentPickerHost", "GXWorks Agent",
                                  WS_POPUP, 0, 0, 0, 0, None, None, instance, None) or 0


def _raise_window(hwnd: int) -> None:
    """Bring a window to the foreground from a process the user did not just click."""
    if not hwnd:
        return
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    foreground = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    own_thread = kernel32.GetCurrentThreadId()
    attached = False
    if foreground_thread and foreground_thread != own_thread:
        # Foreground ownership is shared only between input-attached threads.
        attached = bool(user32.AttachThreadInput(foreground_thread, own_thread, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(foreground_thread, own_thread, False)


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
        # The dialog is modal inside Show, so it has to reach the foreground before
        # that call: raise the owner window, then hand it to the dialog.
        owner = 0
        try:
            owner = _create_owner()
            _pump_alt()
            _raise_window(owner)
            show = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_void_p)(
                _vtable(dialog.value, _SLOT_SHOW))
            shown = show(dialog, ctypes.c_void_p(owner) if owner else None) & 0xFFFFFFFF
        finally:
            if owner:
                ctypes.windll.user32.DestroyWindow(owner)
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
