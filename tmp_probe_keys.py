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
print("editor:", None if editor is None else editor.window_text()[:40], flush=True)
def dump(tag, seconds):
    seen = []
    for step in range(int(seconds / 1.0)):
        time.sleep(1.0)
        try:
            for dialog in driver._dialogs():
                text = (driver._dialog_text(dialog) or "").replace("\n", " ")
                seen.append(f"{tag} DIALOG class={dialog.class_name()} text={text[:120]!r}")
        except Exception:
            pass
    print(f"{tag}: dialogs={len(seen)}", flush=True)
    for line in seen[:4]:
        print("   ", line, flush=True)
if editor is not None:
    rect = editor.rectangle()
    editor.click_input(coords=(rect.width() - 70, rect.height() - 50))
    time.sleep(1)
    driver._command(0x4A1)
    dump("0x4A1-with-focus", 12)
    send_keys("{F4}")
    dump("F4", 10)
    send_keys("+{F4}")
    dump("shift+F4", 12)
    send_keys("^s")
    time.sleep(6)
print("file size:", copy.stat().st_size, flush=True)
