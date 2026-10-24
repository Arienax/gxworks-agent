import sys, time
sys.path.insert(0, "src")
from pywinauto.application import Application
from gxworks2.finder import GXWorks2Finder
finder = GXWorks2Finder()
session = finder.find_running()
print("session:", None if session is None else session.title[:50], flush=True)
app = Application(backend="win32").connect(process=session.process_id)
main = app.top_window()
try:
    menu = main.menu()
    for item in menu.items():
        print("menu:", item.text(), flush=True)
except Exception as error:
    print("menu error:", str(error)[:90], flush=True)
    for child in main.children():
        print("  child:", child.class_name(), repr(child.window_text()[:30]), flush=True)
