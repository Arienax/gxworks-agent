import sys, time, subprocess
sys.path.insert(0, "src")
from pathlib import Path
from gxw.ladder_program_writer import build_ladder_project_from_source
from gxw.offline_project import read_offline_project
index = {}
for path in Path(r"D:\download\GXW_samples_part_01_of_07").rglob("*.gxw"):
    index.setdefault(path.name, path)
source = index["演示程序.gxw"].read_bytes()
base = Path("tmp_effect"); base.mkdir(exist_ok=True)
from gxworks2.engineering_driver import NativeEngineeringDriver
from pywinauto.keyboard import send_keys
results = {}
for label, keys in (("F4", "{F4}"), ("shift_alt_F4", "+%{F4}"), ("none", None)):
    path = base / f"{label}.gxw"
    path.write_bytes(build_ladder_project_from_source(source, plc_model="FX3U").data)
    before = len(read_offline_project(path.read_bytes()).streams.get("MAIN.res") or b"")
    subprocess.run(["taskkill", "/IM", "GD2.exe", "/F"], capture_output=True)
    time.sleep(4)
    driver = NativeEngineeringDriver.start_from_saved(path.resolve(), "FX3U", timeout=120)
    driver._idle()
    main = driver.ui._application(driver.session).window(handle=driver.session.window_handle)
    for child in main.children():
        if child.class_name().startswith("Afx:") and "MAIN" in child.window_text():
            rect = child.rectangle()
            child.click_input(coords=(rect.width() - 70, rect.height() - 50))
            break
    time.sleep(1)
    if keys:
        send_keys(keys)
    time.sleep(10)
    send_keys("^s")
    time.sleep(8)
    subprocess.run(["taskkill", "/IM", "GD2.exe", "/F"], capture_output=True)
    time.sleep(2)
    after_project = read_offline_project(path.read_bytes())
    results[label] = (before, len(after_project.streams.get("MAIN.res") or b""),
                      path.stat().st_size, after_project.streams.get("MAIN.Program.pou") ==
                      read_offline_project(path.read_bytes()).streams.get("MAIN.Program.pou"))
for label, (before, after, size, _) in results.items():
    print(f"{label:14s} .res before={before:5d} after={after:5d} file={size}")
