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
    print("  WARNING app still running", flush=True)

def open_app(path):
    close_app()
    target = Path(path).resolve()
    subprocess.Popen([str(finder.find_executable()), str(target)])
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        session = finder.find_running()
        if session is not None and target.name.casefold() in session.title.casefold():
            time.sleep(1.5)
            return session
        time.sleep(0.5)
    raise RuntimeError(f"did not open {target}")

def dismiss_dialogs(main):
    seen = []
    for window in main.descendants(class_name="#32770"):
        try:
            if not window.is_visible():
                continue
            title = window.window_text()
            seen.append(title)
            for button in window.descendants(class_name="Button"):
                if button.window_text() in {"确定", "OK", "是(&Y)", "はい"}:
                    button.click(); break
        except Exception:
            pass
    return seen

def export(session, target):
    automation.export_current_program(session, Path(target).resolve())

def rows(path):
    return [line.strip() for line in Path(path).read_text(encoding="utf-16").splitlines() if line.strip()]

work = Path("tmp_rebuild"); work.mkdir(exist_ok=True)
samples = {
    "fenjian": Path(r"D:\download\GXW_samples_part_01_of_07\community\jcpeixun\fenjian.gxw"),
    "升降机": Path(r"D:\download\GXW_samples_part_01_of_07\community\jcpeixun\三菱FX3U升降机控制1.gxw"),
    "温室": Path(r"D:\download\GXW_samples_part_01_of_07\community\jcpeixun\SmartGreenhouse.gxw"),
}
for label, sample in samples.items():
    delivered = work / f"{sample.stem}.gxw"
    print(f"== {label}: {sample.name}", flush=True)
    if not sample.exists():
        match = list(Path(r"D:\download\GXW_samples_part_01_of_07").rglob(sample.name))
        if not match:
            print("  sample not found", flush=True); continue
        sample = match[0]
    print("  original export", flush=True)
    session = open_app(sample)
    export(session, work / f"{label}_orig.csv")
    print("   rows:", len(rows(work / f"{label}_orig.csv")), flush=True)
    print("  delivered: compile + save", flush=True)
    session = open_app(delivered)
    main = automation._main_window(session)
    main.restore(); main.set_focus(); time.sleep(1)
    send_keys("{F4}"); time.sleep(4)
    print("   dialogs after F4:", dismiss_dialogs(main), flush=True)
    send_keys("^s"); time.sleep(6)
    print("   dialogs after save:", dismiss_dialogs(main), flush=True)
    size_before = delivered.stat().st_size
    print("   saved size:", size_before, flush=True)
    print("  cold reopen + export", flush=True)
    session = open_app(delivered)
    export(session, work / f"{label}_cold.csv")
    cold_rows = rows(work / f"{label}_cold.csv")
    orig_rows = rows(work / f"{label}_orig.csv")
    print("   cold rows:", len(cold_rows), "orig rows:", len(orig_rows),
          "equal:", cold_rows == orig_rows, flush=True)
    if cold_rows != orig_rows:
        for index, (a, b) in enumerate(zip(orig_rows, cold_rows)):
            if a != b:
                print("    first diff", index, "|", a[:70], "||", b[:70], flush=True)
                break
close_app()
print("DONE", flush=True)
