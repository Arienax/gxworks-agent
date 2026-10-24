import sys, time
sys.path.insert(0, "src")
from pywinauto.application import Application
from pywinauto.keyboard import send_keys
from gxworks2.finder import GXWorks2Finder
finder = GXWorks2Finder()
session = finder.find_running()
app = Application(backend="win32").connect(process=session.process_id)
main = app.top_window()
target = None
for child in main.children():
    if child.class_name().startswith("Afx:") and "MAIN" in child.window_text():
        target = child
        break
print("editor:", None if target is None else target.window_text(), flush=True)
try:
    main.set_focus()
except Exception:
    main.set_keyboard_focus()
if target is not None:
    rect = target.rectangle()
    target.click_input(coords=(rect.width() - 60, rect.height() - 40))
time.sleep(1)
send_keys("+{F4}")
time.sleep(8)
print("--- windows after shift+F4", flush=True)
for window in app.windows():
    try:
        print("  ", window.class_name(), repr(window.window_text()[:60]), flush=True)
    except Exception:
        pass
send_keys("^s")
time.sleep(8)
size = __import__("pathlib").Path(r"tmp_rebuild/clone_三菱 FX3U3轴定位控制案例.gxw").stat().st_size
print("after save size:", size, flush=True)
send_keys("^s")
time.sleep(3)
