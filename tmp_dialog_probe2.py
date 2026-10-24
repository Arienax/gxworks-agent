import sys, time
sys.path.insert(0, "src")
from pywinauto import Desktop
from pywinauto.application import Application
from gxworks2.finder import GXWorks2Finder
finder = GXWorks2Finder()
session = finder.find_running()
print("session:", None if session is None else session.title, flush=True)
app = Application(backend="uia").connect(process=session.process_id)
main = app.top_window()
print("main:", main.window_text()[:50], flush=True)
time.sleep(20)
print("--- top level windows", flush=True)
for window in Desktop(backend="uia").windows():
    try:
        print("  ", repr(window.window_text()[:54]), window.class_name(), flush=True)
    except Exception:
        pass
print("--- menus", flush=True)
try:
    menu = main.menu()
    for item in menu.items():
        print("   menu:", item.text(), flush=True)
except Exception as error:
    print("menu error:", str(error)[:80], flush=True)
