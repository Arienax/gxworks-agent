import sys, time, subprocess
sys.path.insert(0, "src")
from pathlib import Path
from pywinauto.keyboard import send_keys
from gxworks2.finder import GXWorks2Finder
from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation

finder, automation = GXWorks2Finder(), PywinautoGXWorks2UIAutomation()
delivered = Path("tmp_rebuild/SmartGreenhouse.gxw").resolve()
work = Path("tmp_rebuild")

def close_app():
    session = finder.find_running()
    if session is None:
        return
    try:
        automation._main_window(session).close()
    except Exception:
        pass
    for _ in range(40):
        time.sleep(0.5)
        if finder.find_running() is None:
            return

def open_app(path):
    close_app()
    target = Path(path).resolve()
    subprocess.Popen([str(finder.find_executable()), str(target)])
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        session = finder.find_running()
        if session is not None and target.name.casefold() in session.title.casefold():
            time.sleep(2.5)
            return session
        time.sleep(0.5)
    raise RuntimeError(f"did not open {target}")

def focus(main):
    try:
        main.set_focus()
    except Exception as error:
        print("   focus fallback:", str(error)[:40], flush=True)
        try:
            main.set_keyboard_focus()
        except Exception as inner:
            print("   keyboard focus failed:", str(inner)[:40], flush=True)

def rows(path):
    return [line.strip() for line in Path(path).read_text(encoding="utf-16").splitlines() if line.strip()]

session = open_app(delivered)
main = automation._main_window(session)
try:
    main.restore()
except Exception:
    pass
focus(main)
time.sleep(1)
size_before = delivered.stat().st_size
send_keys("{F4}"); time.sleep(4)
send_keys("^s"); time.sleep(6)
print("   size:", size_before, "->", delivered.stat().st_size, flush=True)
session = open_app(delivered)
out = (work / "温室_cold.csv").resolve()
if out.exists():
    out.unlink()
try:
    automation.export_current_program(session, out)
except Exception as error:
    print("   export retry:", str(error)[:60], flush=True)
    time.sleep(2)
    try:
        automation.prepare_export_retry(session)
    except Exception:
        pass
    time.sleep(2)
    automation.export_current_program(session, out)
cold, orig = rows(out), rows(work / "温室_orig.csv")
print("   cold rows:", len(cold), "orig rows:", len(orig), "equal:", cold == orig, flush=True)
if cold != orig:
    for index, (a, b) in enumerate(zip(orig, cold)):
        if a != b:
            print("    first diff", index, "|", a[:60], "||", b[:60], flush=True); break
close_app()
print("DONE", flush=True)
