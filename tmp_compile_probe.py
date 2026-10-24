import sys, time
sys.path.insert(0, "src")
from pathlib import Path
import subprocess
Get = lambda: subprocess.run(["taskkill", "/IM", "GD2.exe", "/F"], capture_output=True)
Get(); time.sleep(3)
from gxworks2.engineering_driver import NativeEngineeringDriver
copy = Path("tmp_native_accept_demo/project.gxw").resolve()
driver = NativeEngineeringDriver.start_from_saved(copy, "FX3U", timeout=120)
driver._idle()
print("idle ok", flush=True)
driver._command(0x4A1)
seen = {}
for step in range(20):
    try:
        for dialog in driver._dialogs():
            text = (driver._dialog_text(dialog) or "").replace("\n", " ")[:50]
            seen.setdefault((dialog.handle, text), step)
    except Exception as error:
        print("dialog scan error:", str(error)[:60], flush=True)
    time.sleep(1.5)
print("dialogs seen:", list(seen.items())[:8] or "none", flush=True)
try:
    summaries = driver._owned_control_handles("Edit", 10013, visible=False)
    print("summary handles:", summaries, flush=True)
    if summaries:
        widget = driver.ui._application(driver.session).window(handle=summaries[0])
        print("summary text:", repr(widget.window_text()[:120]), flush=True)
except Exception as error:
    print("summary error:", str(error)[:80], flush=True)
