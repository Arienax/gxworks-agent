"""Open a managed GXW copy through the existing GX desktop automation boundary."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import time


class GXWProjectAutomation:
    def __init__(self, timeout=30):
        from .ui_automation import PywinautoGXWorks2UIAutomation
        self.ui = PywinautoGXWorks2UIAutomation(timeout=timeout)
        self.timeout = timeout

    def open_project(self, session, path):
        ui = self.ui
        if ui._native_dialog_handles(session):
            return {"success": False, "error_code": "gx_modal_open",
                    "message": "请先处理 GX Works2 中已有的对话框，再重新提出导入。"}
        main = ui._main_window(session)
        main.set_focus()
        main.type_keys("^o")
        dialog = ui._wait_legacy_dialog(session, re.compile(r"^(打开工程|Open Project)$", re.I))
        ui._set_legacy_file_name(dialog, path)
        # GX itself truncates the directory in its actual window title. The
        # coordinator creates a fresh UUID filename, also visible in that title.
        expected = Path(path).name.casefold()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            title = main.window_text().replace("/", "\\").casefold()
            dialogs = ui._native_dialog_handles(session)
            if expected in title and main.is_enabled() and not dialogs:
                return {"success": True, "error_code": None,
                        "message": "GX Works2 已打开此版本的工程副本；请在 GX 中编译并检查结果。"}
            for handle in dialogs:
                text = ui._native_dialog_text(handle)
                # Never choose Save/Discard/Yes on an unrelated open project.
                if re.search(r"保存.*[?？]|是否.*保存|save.*changes|错误|失败|超出|无法|error|failed|too long|cannot", text, re.I | re.S):
                    return {"success": False, "error_code": "gx_requires_attention",
                            "message": "GX Works2 正等待处理保存提示或工程错误；请检查窗口。未自动确认该提示。"}
            time.sleep(0.1)
        return {"success": False, "error_code": "gx_open_unverified",
                "message": "尚未确认 GX Works2 已打开所选工程副本，请检查其窗口；不会自动重试。"}


def import_gxw_project(path, *, progress=None, finder=None, automation=None, expected_sha256=None):
    from .finder import GXWorks2Finder
    finder = finder or GXWorks2Finder()
    path = Path(path).resolve()
    if not path.is_file() or path.suffix.lower() != ".gxw":
        raise ValueError("A managed GXW project is required")
    raw = path.read_bytes()
    if expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("GXW execution copy changed before import")
    session = finder.find_running()
    if session is None:
        return {"success": False, "error_code": "gx_works2_not_running", "message": "请先启动 GX Works2。"}
    if progress:
        progress("gxw_open", "正在 GX Works2 中打开已确认版本的工程副本")
    result = (automation or GXWProjectAutomation()).open_project(session, path)
    return {**result, "gx_compile_status": "unverified", "source_sha256": hashlib.sha256(raw).hexdigest()}
