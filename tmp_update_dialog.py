import sys, time, subprocess
sys.path.insert(0, "src")
from pathlib import Path
from pywinauto import Desktop
from pywinauto.application import Application
from pywinauto.keyboard import send_keys
from gxworks2.finder import GXWorks2Finder

finder = GXWorks2Finder()
subprocess.run(["taskkill", "/IM", "GD2.exe", "/F"], capture_output=True)
time.sleep(3)
target = Path("tmp_rebuild/clone_三菱 FX3U3轴定位控制案例.gxw").resolve()
subprocess.Popen([str(finder.find_executable()), str(target)])
time.sleep(10)
session = finder.find_running()
app = Application(backend="uia").connect(process=session.process_id)
main = app.top_window()

def update_dialog():
    for window in app.windows():
        try:
            title = window.window_text() or ""
            if "更新" in title or "确认" in title:
                return window
        except Exception:
            pass
    for window in main.descendants(control_type="Window"):
        try:
            if "更新" in (window.window_text() or ""):
                return window
        except Exception:
            pass
    return None

dialog = None
for _ in range(12):
    dialog = update_dialog()
    if dialog is not None:
        break
    time.sleep(5)
print("dialog:", None if dialog is None else repr(dialog.window_text()[:40]), flush=True)
if dialog is not None:
    for control in dialog.descendants():
        try:
            kind = control.element_info.control_type
            text = (control.window_text() or "").strip()
            if kind in ("CheckBox", "Button", "RadioButton"):
                print("   ", kind, repr(text[:26]), flush=True)
        except Exception:
            pass
    ticked = 0
    for control in dialog.descendants():
        try:
            if control.element_info.control_type == "CheckBox":
                control.click_input()
                ticked += 1
        except Exception as error:
            print("   tick failed:", str(error)[:50], flush=True)
    print("   ticked:", ticked, flush=True)
    time.sleep(1)
    for control in dialog.descendants():
        try:
            if control.element_info.control_type == "Button" and control.window_text().strip().startswith("确定"):
                control.click_input()
                print("   confirmed", flush=True)
                break
        except Exception:
            pass
    time.sleep(2)
try:
    for item in main.menu().items():
        print("menu:", item.text(), flush=True)
except Exception as error:
    print("menu error:", str(error)[:70], flush=True)
print("size before save:", target.stat().st_size, flush=True)
