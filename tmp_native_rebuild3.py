import sys, time, subprocess
sys.path.insert(0, "src")
from pathlib import Path
from pywinauto.keyboard import send_keys
from gxworks2.finder import GXWorks2Finder
from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation

finder, automation = GXWorks2Finder(), PywinautoGXWorks2UIAutomation()
sample = Path(r"D:\download\GXW_samples_part_01_of_07\github\files\J1mmy4444\smart-greenhouse-control-system\plc\SmartGreenhouse.gxw")
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
            time.sleep(2.0)
            return session
        time.sleep(0.5)
    raise RuntimeError(f"did not open {target}")

def rows(path):
    return [line.strip() for line in Path(path).read_text(encoding="utf-16").splitlines() if line.strip()]

def export(session, target):
    target = Path(target).resolve()
    if target.exists():
        target.unlink()
    try:
        automation.export_current_program(session, target)
    except Exception:
        time.sleep(2)
        try:
            automation.prepare_export_retry(session)
        except Exception:
            pass
        time.sleep(2)
        automation.export_current_program(session, target)

print("== 温室", flush=True)
session = open_app(sample)
export(session, work / "温室_orig.csv")
print("   original rows:", len(rows(work / "温室_orig.csv")), flush=True)
session = open_app(delivered)
main = automation._main_window(session)
main.restore(); main.set_focus(); time.sleep(1)
send_keys("{F4}"); time.sleep(4)
send_keys("^s"); time.sleep(6)
print("   compiled+saved:", delivered.stat().st_size, flush=True)
session = open_app(delivered)
export(session, work / "温室_cold.csv")
cold, orig = rows(work / "温室_cold.csv"), rows(work / "温室_orig.csv")
print("   cold rows:", len(cold), "orig rows:", len(orig), "equal:", cold == orig, flush=True)
if cold != orig:
    for index, (a, b) in enumerate(zip(orig, cold)):
        if a != b:
            print("    first diff at", index, "|", a[:60], "||", b[:60], flush=True); break
    print("    tail:", orig[-2:], cold[-2:], flush=True)
comments = work / "温室_cold_comments.csv"
session_obj = finder.find_running()
try:
    automation.export_current_comments(session_obj, comments.resolve())
    print("   delivered comments exported:", len([l for l in rows(comments) if l]), flush=True)
except Exception as error:
    print("   comments export:", str(error)[:60], flush=True)
close_app()
print("DONE", flush=True)
