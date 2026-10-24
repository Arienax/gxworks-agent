import sys, time, subprocess
sys.path.insert(0, "src")
from pathlib import Path
from pywinauto.application import Application
from gxworks2.finder import GXWorks2Finder
finder = GXWorks2Finder()
subprocess.run(["taskkill", "/IM", "GD2.exe", "/F"], capture_output=True)
time.sleep(3)
target = Path("tmp_rebuild/clone_三菱 FX3U3轴定位控制案例.gxw").resolve()
subprocess.Popen([str(finder.find_executable()), str(target)])
time.sleep(12)
session = finder.find_running()
app = Application(backend="win32").connect(process=session.process_id)
main = app.top_window()
print("main:", main.window_text()[:40], flush=True)
for window in app.windows():
    try:
        text = window.window_text()
        if text and ("更新" in text or "确认" in text):
            print("DIALOG:", repr(text[:40]), window.class_name(), flush=True)
            for control in window.descendants():
                print("   ", control.class_name(), repr(control.window_text()[:24]),
                      "id=", control.control_id(), flush=True)
    except Exception:
        pass
for child in main.children():
    if child.class_name() == "XTPToolBar" and child.window_text() in ("标准", "程序通用"):
        print("TOOLBAR:", child.window_text(), flush=True)
        for button in child.descendants():
            try:
                print("    ", button.class_name(), repr(button.window_text()[:20]),
                      "id=", button.control_id(), flush=True)
            except Exception:
                pass
