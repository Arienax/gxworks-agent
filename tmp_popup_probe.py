import sys, time, subprocess
sys.path.insert(0, "src")
from pathlib import Path
from gxworks2.engineering_driver import NativeEngineeringDriver
from pywinauto.application import Application
from pywinauto import Desktop
copy = Path("tmp_native_accept_demo/project.gxw").resolve()
subprocess.run(["taskkill", "/IM", "GD2.exe", "/F"], capture_output=True)
time.sleep(4)
driver = NativeEngineeringDriver.start_from_saved(copy, "FX3U", timeout=120)
driver._idle()
print("open ok", flush=True)
app = Application(backend="uia").connect(process=driver.session.process_id)
window = app.window(handle=driver.session.window_handle)
top = None
for element in window.descendants(control_type="MenuItem"):
    if "转换" in (element.element_info.name or ""):
        top = element
        break
top.click_input()
time.sleep(2)
popup = None
for candidate in Desktop(backend="uia").windows():
    try:
        if candidate.class_name() == "#32768" and candidate.is_visible():
            popup = candidate
            break
    except Exception:
        pass
print("popup:", None if popup is None else popup.window_text()[:30], flush=True)
if popup is not None:
    entries = []
    for element in popup.descendants(control_type="MenuItem"):
        name = (element.element_info.name or "").strip()
        if name:
            entries.append((name, element))
    print("entries:", [name for name, _ in entries][:18], flush=True)
    chosen = next((element for name, element in entries if "全部编译" in name), None)
    print("chosen:", chosen is not None, flush=True)
    if chosen is not None:
        chosen.click_input()
        for step in range(25):
            time.sleep(1)
            for dialog in driver._dialogs():
                try:
                    text = (driver._dialog_text(dialog) or "").replace("\n", " ")
                    print(f"  t={step}s DIALOG {text[:90]!r}", flush=True)
                except Exception:
                    pass
            summaries = driver._owned_control_handles("Edit", 10013, visible=False)
            if summaries:
                text = (driver.ui._application(driver.session).window(handle=summaries[0]).window_text() or "").replace("\n", " ")
                if text:
                    print(f"  t={step}s SUMMARY {text[:150]!r}", flush=True)
                    break
print("done", flush=True)
