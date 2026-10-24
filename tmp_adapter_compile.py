import sys
sys.path.insert(0, "src")
from pathlib import Path
from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
from gxw.offline_project import read_offline_project
raw = Path("tmp_native_fresh/project.gxw").read_bytes()
result = NativeWorkspaceSourceSave().compile_project(raw)
print("fields:", [name for name in dir(result) if not name.startswith("_")], flush=True)
for name in ("report", "diagnostics", "warnings", "errors", "findings", "summary"):
    value = getattr(result, name, None)
    if value is not None:
        print(f"  {name}: {str(value)[:200]}", flush=True)
before = read_offline_project(raw)
after = read_offline_project(result.data)
for stream in ("MAIN.Program.pou", "MAIN.res", "MAIN.Labels.lh"):
    print(f"{stream:18s} in={len(before.streams.get(stream) or b''):6d} out={len(after.streams.get(stream) or b''):6d}", flush=True)
print("body identical:", before.streams.get("MAIN.Program.pou") == after.streams.get("MAIN.Program.pou"), flush=True)
Path("tmp_native_fresh/compiled_adapter.gxw").write_bytes(result.data)
print("compiled size:", len(result.data), flush=True)
