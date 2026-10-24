import sys, time
sys.path.insert(0, "src")
from pathlib import Path
import subprocess
subprocess.run(["taskkill", "/IM", "GD2.exe", "/F"], capture_output=True)
time.sleep(3)
from gxworks2.engineering_driver import NativeEngineeringDriver
target = Path("tmp_rebuild/clone_三菱 FX3U3轴定位控制案例.gxw").resolve()
try:
    driver = NativeEngineeringDriver.start_from_saved(target, "FX3U", timeout=120)
    print("driver started:", type(driver).__name__, flush=True)
    print("methods:", [name for name in dir(driver) if "compile" in name or "revision" in name][:10], flush=True)
    try:
        receipt = driver.compile_current({"revision": 0})
        print("compile receipt keys:", sorted(receipt)[:10] if isinstance(receipt, dict) else type(receipt), flush=True)
        if isinstance(receipt, dict):
            print("output:", str(receipt.get("output"))[:200], flush=True)
            print("completed:", receipt.get("completed"), "command:", receipt.get("command"), flush=True)
    except Exception as error:
        print("compile failed:", type(error).__name__, str(error)[:200], flush=True)
except Exception as error:
    print("start failed:", type(error).__name__, str(error)[:200], flush=True)
