import sys, time, subprocess
sys.path.insert(0, "src")
from pathlib import Path
from pywinauto.keyboard import send_keys
from gxworks2.finder import GXWorks2Finder
from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation
finder, automation = GXWorks2Finder(), PywinautoGXWorks2UIAutomation()
index = {}
for path in Path(r"D:\download\GXW_samples_part_01_of_07").rglob("*.gxw"):
    index.setdefault(path.name, path)
original = index["三菱 FX3U3轴定位控制案例.gxw"]
clone = Path("tmp_rebuild/clone_三菱 FX3U3轴定位控制案例.gxw").resolve()
work = Path("tmp_rebuild")

def kill():
    subprocess.run(["taskkill", "/IM", "GD2.exe", "/F"], capture_output=True)
    time.sleep(3)

def open_app(path):
    kill()
    target = Path(path).resolve()
    subprocess.Popen([str(finder.find_executable()), str(target)])
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        session = finder.find_running()
        if session is not None and target.name.casefold() in session.title.casefold():
            time.sleep(3.0)
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
    except Exception as error:
        print("   export first attempt failed:", str(error)[:70], flush=True)
        time.sleep(3)
        automation.export_current_program(session, target)

print("== original export", flush=True)
session = open_app(original)
export(session, work / "axis_orig.csv")
print("   rows:", len(rows(work / "axis_orig.csv")), flush=True)
print("== clone: export + compile + save", flush=True)
session = open_app(clone)
main = automation._main_window(session)
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
export(session, work / "axis_clone_pre.csv")
print("   pre rows:", len(rows(work / "axis_clone_pre.csv")), flush=True)
send_keys("{F4}"); time.sleep(6)
send_keys("^s"); time.sleep(8)
print("   saved size:", clone.stat().st_size, flush=True)
print("== clone cold reopen", flush=True)
session = open_app(clone)
export(session, work / "axis_clone_cold.csv")
cold, orig = rows(work / "axis_clone_cold.csv"), rows(work / "axis_orig.csv")
print("   cold rows:", len(cold), "original rows:", len(orig), "equal:", cold == orig, flush=True)
if cold != orig:
    shown = 0
    for i, (a, b) in enumerate(zip(orig, cold)):
        if a != b:
            print("   diff", i, "|", a[:70], "||", b[:70], flush=True)
            shown += 1
            if shown >= 4:
                break
kill()
print("DONE", flush=True)
