from pathlib import Path
path = Path("scripts/native_ladder_acceptance.py")
text = path.read_text(encoding="utf-8")
target = "        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)\nineeringDriver.start_from_saved("
replacement = ("        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)\n"
               "        try:\n"
               "            stage(\"open\")\n"
               "            driver = NativeEngineeringDriver.start_from_saved(")
assert text.count(target) == 1
path.write_text(text.replace(target, replacement, 1), encoding="utf-8")
import py_compile
py_compile.compile("scripts/native_ladder_acceptance.py", doraise=True)
print("repaired and compiles")
