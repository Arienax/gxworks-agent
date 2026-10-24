import sys, time, subprocess
sys.path.insert(0, "src")
from pathlib import Path
from pywinauto.keyboard import send_keys
from gxworks2.finder import GXWorks2Finder
from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation

finder, automation = GXWorks2Finder(), PywinautoGXWorks2UIAutomation()

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
    print("  WARNING still running", flush=True)

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
    except Exception as error:
        print("   export retry after:", str(error)[:60].replace("\n", " "), flush=True)
        time.sleep(2)
        try:
            automation.prepare_export_retry(session)
        except Exception as retry_error:
            print("   prepare retry:", str(retry_error)[:60], flush=True)
        time.sleep(2)
        automation.export_current_program(session, target)

work = Path("tmp_rebuild")
samples = {
    "升降机": Path(r"D:\download\GXW_samples_part_01_of_07\community\jcpeixun\三菱FX3U升降机控制1.gxw"),
    "温室": Path(r"D:\download\GXW_samples_part_01_of_07\community\jcpeixun\SmartGreenhouse.gxw"),
}
for label, sample in samples.items():
    delivered = work / f"{sample.stem}.gxw"
    print(f"== {label}", flush=True)
    session = open_app(sample)
    export(session, work / f"{label}_orig.csv")
    print("   original rows:", len(rows(work / f"{label}_orig.csv")), flush=True)
    session = open_app(delivered)
    main = automation._main_window(session)
    main.restore(); main.set_focus(); time.sleep(1)
    send_keys("{F4}"); time.sleep(4)
    send_keys("^s"); time.sleep(6)
    print("   compiled+saved:", delivered.stat().st_size, flush=True)
    session = open_app(delivered)
    export(session, work / f"{label}_cold.csv")
    cold, orig = rows(work / f"{label}_cold.csv"), rows(work / f"{label}_orig.csv")
    print("   cold rows:", len(cold), "orig rows:", len(orig), "equal:", cold == orig, flush=True)
    if cold != orig:
        for index, (a, b) in enumerate(zip(orig, cold)):
            if a != b:
                print("    first diff at", index, "|", a[:60], "||", b[:60], flush=True); break
close_app()
print("DONE", flush=True)
