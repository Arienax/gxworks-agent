import sys, json
sys.path.insert(0, "src")
from pathlib import Path
from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
from gxw.offline_project import read_offline_project
for label, name in (("FB", "clone_三菱 FX3U3轴定位控制案例.gxw"), ("instr", "clone_演示程序.gxw")):
    raw = Path("tmp_rebuild") / name
    data = raw.read_bytes()
    try:
        result = NativeWorkspaceSourceSave().compile_project(data)
    except Exception as error:
        print(f"{label}: FAILED {type(error).__name__} {str(error)[:160]}", flush=True)
        continue
    before, after = read_offline_project(data), read_offline_project(result.data)
    workspace = (result.report or {}).get("native_workspace", {})
    print(f"{label}: status={workspace.get('status')} compile={workspace.get('compile')} "
          f"cpu={workspace.get('cpu')} res {len(before.streams.get('MAIN.res') or b'')}->{len(after.streams.get('MAIN.res') or b'')} "
          f"size {len(data)}->{len(result.data)}", flush=True)
    messages = [row for row in (result.report or {}).get("steps", []) if row.get("code")]
    print(f"   non-zero steps: {len(messages)}", flush=True)
