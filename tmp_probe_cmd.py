import sys, time, subprocess
sys.path.insert(0, "src")
from pathlib import Path
from gxworks2.engineering_driver import NativeEngineeringDriver
copy = Path("tmp_native_accept_demo/project.gxw").resolve()
driver = None
for attempt in range(3):
    subprocess.run(["taskkill", "/IM", "GD2.exe", "/F"], capture_output=True)
    time.sleep(4)
    try:
        driver = NativeEngineeringDriver.start_from_saved(copy, "FX3U", timeout=120)
        print(f"open ok attempt={attempt}", flush=True)
        break
    except Exception as error:
        print(f"open failed attempt={attempt}: {type(error).__name__} {str(error)[:70]}", flush=True)
        time.sleep(15)
if driver is None:
    raise SystemExit("could not open")
driver._idle()
summaries = driver._owned_control_handles("Edit", 10013, visible=False)
print("summary handles:", summaries, flush=True)
widget = driver.ui._application(driver.session).window(handle=summaries[0]) if summaries else None
print("summary before:", repr(widget.window_text()[:80]) if widget else None, flush=True)
driver._command(0x4A1)
print("sent 0x4A1", flush=True)
for step in range(24):
    time.sleep(1.2)
    try:
        dialogs = driver._dialogs()
    except Exception as error:
        dialogs = []
        print("  scan error:", str(error)[:50], flush=True)
    for dialog in dialogs:
        try:
            text = (driver._dialog_text(dialog) or "").replace("\r", " ").replace("\n", " ")
            print(f"  t={step*1.2:.0f}s DIALOG class={dialog.class_name()} text={text[:150]!r}", flush=True)
        except Exception as error:
            print(f"  t={step*1.2:.0f}s DIALOG read error {str(error)[:40]}", flush=True)
    try:
        current = (widget.window_text() or "").replace("\n", " ")
        if current:
            print(f"  t={step*1.2:.0f}s SUMMARY {current[:140]!r}", flush=True)
    except Exception:
        pass
