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
targets = []
for element in window.descendants(control_type="Button"):
    try:
        name = (element.element_info.name or "").strip()
        if "编译" in name or "变换" in name:
            targets.append((name, element))
            print("   candidate:", name[:24], flush=True)
    except Exception:
        pass
chosen = None
for name, element in targets:
    if "全部" in name:
        chosen = (name, element)
        break
if chosen is None and targets:
    chosen = targets[0]
print("chosen:", None if chosen is None else chosen[0], flush=True)
if chosen is not None:
    chosen[1].click_input()
    time.sleep(2)
    for step in range(20):
        for dialog in driver._dialogs():
            try:
                text = (driver._dialog_text(dialog) or "").replace("\n", " ")
                print(f"  t={step}s DIALOG class={dialog.class_name()} text={text[:100]!r}", flush=True)
            except Exception:
                pass
        time.sleep(1)
        try:
            summaries = driver._owned_control_handles("Edit", 10013, visible=False)
            if summaries:
                widget = driver.ui._application(driver.session).window(handle=summaries[0])
                text = (widget.window_text() or "").replace("\n", " ")
                if text:
                    print(f"  t={step}s SUMMARY {text[:160]!r}", flush=True)
                    break
        except Exception:
            pass
print("done", flush=True)
