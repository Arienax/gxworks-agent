import sys, time, subprocess
sys.path.insert(0, "src")
from pathlib import Path
from gxworks2.engineering_driver import NativeEngineeringDriver
from pywinauto.application import Application
copy = Path("tmp_native_accept_demo/project.gxw").resolve()
subprocess.run(["taskkill", "/IM", "GD2.exe", "/F"], capture_output=True)
time.sleep(4)
driver = NativeEngineeringDriver.start_from_saved(copy, "FX3U", timeout=120)
driver._idle()
print("open ok", flush=True)
app = Application(backend="uia").connect(process=driver.session.process_id)
window = app.window(handle=driver.session.window_handle)
names = []
for element in window.descendants():
    try:
        kind = element.element_info.control_type
        name = (element.element_info.name or "").strip()
        if kind in ("Button", "MenuItem", "TabItem") and name:
            names.append((kind, name))
    except Exception:
        pass
print("uia buttons:", len(names), flush=True)
for kind, name in names[:40]:
    print(f"   {kind}: {name[:24]}", flush=True)
