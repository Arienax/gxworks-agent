import sys, time, subprocess
sys.path.insert(0, "src")
from pathlib import Path
from gxw.ladder_program_writer import build_ladder_project_from_source
index = {}
for path in Path(r"D:\download\GXW_samples_part_01_of_07").rglob("*.gxw"):
    index.setdefault(path.name, path)
out = Path("tmp_native_fresh"); out.mkdir(exist_ok=True)
built = build_ladder_project_from_source(index["演示程序.gxw"].read_bytes(), plc_model="FX3U")
target = out / "project.gxw"
target.write_bytes(built.data)
print("fresh project:", target.stat().st_size, built.report["instructions"], "instr", flush=True)
subprocess.run(["taskkill", "/IM", "GD2.exe", "/F"], capture_output=True)
time.sleep(4)
from gxworks2.engineering_driver import NativeEngineeringDriver
from pywinauto.keyboard import send_keys
driver = NativeEngineeringDriver.start_from_saved(target.resolve(), "FX3U", timeout=120)
driver._idle()
print("open ok", flush=True)
main = driver.ui._application(driver.session).window(handle=driver.session.window_handle)
for child in main.children():
    if child.class_name().startswith("Afx:") and "MAIN" in child.window_text():
        rect = child.rectangle()
        child.click_input(coords=(rect.width() - 70, rect.height() - 50))
        break
time.sleep(1)
send_keys("+%{F4}")
seen = set()
for step in range(35):
    time.sleep(1)
    for dialog in driver._dialogs():
        if dialog.handle in seen:
            continue
        seen.add(dialog.handle)
        text = (driver._dialog_text(dialog) or "").replace("\n", " | ")
        print(f"  t={step}s DIALOG {text[:150]!r}", flush=True)
        buttons = [(c.control_id(), c.window_text()[:10]) for c in dialog.children() if c.class_name() == "Button"]
        print("     buttons:", buttons[:6], flush=True)
        try:
            driver._click(dialog, 1)
            print("     clicked id=1", flush=True)
        except Exception:
            pass
    for handle in driver._owned_control_handles("Edit", 10013, visible=False):
        text = (driver.ui._application(driver.session).window(handle=handle).window_text() or "").replace("\n", " | ")
        if text:
            print(f"  t={step}s SUMMARY {text[:180]!r}", flush=True)
print("done; .res:", flush=True)
