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
menus = {}
for element in window.descendants():
    try:
        if element.element_info.control_type == "MenuItem":
            name = (element.element_info.name or "").strip()
            if name:
                menus.setdefault(name, element)
    except Exception:
        pass
print("menu items:", len(menus), flush=True)
print("names sample:", list(menus)[:16], flush=True)
compile_items = [name for name in menus if "编译" in name or "变换" in name]
print("compile-related:", compile_items[:8], flush=True)
if compile_items:
    menus[compile_items[0]].click_input()
    time.sleep(2)
    after = {}
    for element in window.descendants():
        try:
            if element.element_info.control_type == "MenuItem":
                name = (element.element_info.name or "").strip()
                if name:
                    after.setdefault(name, element)
        except Exception:
            pass
    print("after open:", list(after)[:20], flush=True)
    pick = [name for name in after if "全部编译" in name]
    print("pick:", pick[:3], flush=True)
    if pick:
        after[pick[0]].click_input()
        for step in range(25):
            time.sleep(1)
            for dialog in driver._dialogs():
                try:
                    print(f"  t={step} DIALOG {driver._dialog_text(dialog)[:90]!r}", flush=True)
                except Exception:
                    pass
            summaries = driver._owned_control_handles("Edit", 10013, visible=False)
            if summaries:
                text = (driver.ui._application(driver.session).window(handle=summaries[0]).window_text() or "").replace("\n", " ")
                if text:
                    print(f"  t={step} SUMMARY {text[:150]!r}", flush=True)
                    break
print("done", flush=True)
