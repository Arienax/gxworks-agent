import sys, time, subprocess
sys.path.insert(0, "src")
from pathlib import Path
from gxworks2.finder import GXWorks2Finder
from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation
finder, automation = GXWorks2Finder(), PywinautoGXWorks2UIAutomation()
index = {}
for path in Path(r"D:\download\GXW_samples_part_01_of_07").rglob("*.gxw"):
    index.setdefault(path.name, path)
sample = index["三菱 FX3U3轴定位控制案例.gxw"].resolve()
session = finder.find_running()
if session is not None:
    try:
        automation._main_window(session).close()
    except Exception:
        pass
    for _ in range(40):
        time.sleep(0.5)
        if finder.find_running() is None:
            break
subprocess.Popen([str(finder.find_executable()), str(sample)])
deadline = time.monotonic() + 180
session = None
while time.monotonic() < deadline:
    candidate = finder.find_running()
    if candidate is not None and sample.name.casefold() in candidate.title.casefold():
        session = candidate
        time.sleep(2.5)
        break
    time.sleep(0.5)
print("opened:", None if session is None else session.title, flush=True)
out = Path("tmp_rebuild/axis_orig.csv").resolve()
if out.exists():
    out.unlink()
automation.export_current_program(session, out)
rows = [line.strip() for line in out.read_text(encoding="utf-16").splitlines() if line.strip()]
print("rows:", len(rows), flush=True)
for row in rows[:20]:
    print("  ", row, flush=True)
try:
    automation._main_window(session).close()
except Exception:
    pass
