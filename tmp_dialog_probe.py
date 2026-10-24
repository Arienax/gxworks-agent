import sys, time, subprocess
sys.path.insert(0, "src")
from pathlib import Path
from pywinauto import Desktop
from gxworks2.finder import GXWorks2Finder
finder = GXWorks2Finder()
subprocess.run(["taskkill", "/IM", "GD2.exe", "/F"], capture_output=True)
time.sleep(3)
target = Path("tmp_rebuild/clone_三菱 FX3U3轴定位控制案例.gxw").resolve()
subprocess.Popen([str(finder.find_executable()), str(target)])
time.sleep(12)
desktop = Desktop(backend="uia")
for window in desktop.windows():
    try:
        title = window.window_text()
        if not title or "GX Works2" not in title and "更新" not in title and "MELSOFT" not in title:
            continue
        print("WINDOW:", repr(title[:60]), window.class_name(), window.is_visible(), flush=True)
        if "更新" in title:
            for control in window.descendants():
                try:
                    text = (control.window_text() or "").strip()
                    if control.element_info.control_type in ("CheckBox", "Button"):
                        print("   ", control.element_info.control_type, repr(text[:30]),
                              "auto_id=", control.element_info.automation_id, flush=True)
                except Exception:
                    pass
    except Exception as error:
        print("scan error:", str(error)[:60], flush=True)
