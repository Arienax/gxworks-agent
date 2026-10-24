import sys, time, subprocess
sys.path.insert(0, "src")
from pathlib import Path
from pywinauto.keyboard import send_keys
from gxworks2.finder import GXWorks2Finder
from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation
finder, automation = GXWorks2Finder(), PywinautoGXWorks2UIAutomation()
target = Path("tmp_rebuild/axis_body_clone.gxw").resolve()
for process in ("GD2",):
    import subprocess as sp
    sp.run(["taskkill", "/IM", "GD2.exe", "/F"], capture_output=True)
time.sleep(3)
sp.Popen([str(finder.find_executable()), str(target)])
deadline = time.monotonic() + 180
session = None
while time.monotonic() < deadline:
    candidate = finder.find_running()
    if candidate is not None and "axis_body_clone" in candidate.title:
        session = candidate; time.sleep(3); break
    time.sleep(0.5)
print("opened:", None if session is None else session.title, flush=True)
if session is None:
    raise SystemExit(1)
main = automation._main_window(session)
def dialogs():
    out = []
    for window in main.descendants(class_name="#32770"):
        try:
            if window.is_visible():
                texts = [c.window_text() for c in window.descendants() if c.window_text().strip()][:5]
                out.append(" | ".join(texts)[:180])
        except Exception:
            pass
    return out
try:
    main.restore()
except Exception:
    pass
try:
    main.set_focus()
except Exception:
    try:
        main.set_keyboard_focus()
    except Exception:
        pass
time.sleep(1)
out_csv = Path("tmp_rebuild/axis_clone_pre.csv").resolve()
if out_csv.exists(): out_csv.unlink()
try:
    automation.export_current_program(session, out_csv)
    rows = [l.strip() for l in out_csv.read_text(encoding="utf-16").splitlines() if l.strip()]
    print("pre-compile export rows:", len(rows), flush=True)
    for row in rows[300:306]:
        print("   ", row[:110], flush=True)
except Exception as error:
    print("export failed:", str(error)[:100], flush=True)
send_keys("{F4}")
time.sleep(6)
print("dialogs after F4:", dialogs(), flush=True)
send_keys("^s")
time.sleep(8)
print("dialogs after save:", dialogs(), flush=True)
print("size:", target.stat().st_size, flush=True)
