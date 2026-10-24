import sys, time, subprocess
sys.path.insert(0, "src")
from pathlib import Path
from gxworks2.engineering_driver import NativeEngineeringDriver
from pywinauto.keyboard import send_keys
copy = Path("tmp_native_accept_demo/project.gxw").resolve()
subprocess.run(["taskkill", "/IM", "GD2.exe", "/F"], capture_output=True)
time.sleep(4)
driver = NativeEngineeringDriver.start_from_saved(copy, "FX3U", timeout=120)
driver._idle()
print("open ok", flush=True)
main = driver.ui._application(driver.session).window(handle=driver.session.window_handle)
editor = None
for child in main.children():
    if child.class_name().startswith("Afx:") and "MAIN" in child.window_text():
        editor = child
        break
if editor is not None:
    rect = editor.rectangle()
    editor.click_input(coords=(rect.width() - 70, rect.height() - 50))
    time.sleep(1)
print("editor focused:", editor is not None, flush=True)
send_keys("+%{F4}")
print("sent shift+alt+F4", flush=True)
accepted = set()
for step in range(30):
    time.sleep(1)
    for dialog in driver._dialogs():
        try:
            text = (driver._dialog_text(dialog) or "").replace("\n", " ")
            if dialog.handle not in accepted:
                print(f"  t={step}s DIALOG class={dialog.class_name()} text={text[:120]!r}", flush=True)
                for control in dialog.children():
                    if control.class_name() == "Button":
                        print(f"      button id={control.control_id()} text={control.window_text()[:12]!r}", flush=True)
                accepted.add(dialog.handle)
                try:
                    driver._click(dialog, 1)
                    print("      clicked id=1", flush=True)
                except Exception as error:
                    print("      click failed", str(error)[:40], flush=True)
        except Exception:
            pass
    summaries = driver._owned_control_handles("Edit", 10013, visible=False)
    if summaries:
        try:
            text = (driver.ui._application(driver.session).window(handle=summaries[0]).window_text() or "").replace("\n", " ")
        except Exception:
            text = ""
        if text:
            print(f"  t={step}s SUMMARY {text[:180]!r}", flush=True)
            break
send_keys("^s")
time.sleep(8)
print("size:", copy.stat().st_size, flush=True)
