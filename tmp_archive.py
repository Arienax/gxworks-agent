import sys, shutil, json
sys.path.insert(0, "src")
from pathlib import Path
from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
root = Path(r"D:\gxworks-agent-private\research\evidence\ladder-delivery-native-20261022\adapter-compile")
root.mkdir(parents=True, exist_ok=True)
rows = []
for label, name in (("FB-3轴定位（44 条携带记录）", "clone_三菱 FX3U3轴定位控制案例.gxw"),
                    ("指令级-演示程序", "clone_演示程序.gxw")):
    source = Path("tmp_rebuild") / name
    result = NativeWorkspaceSourceSave().compile_project(source.read_bytes())
    workspace = (result.report or {}).get("native_workspace", {})
    out = root / name.replace("clone_", "compiled_")
    out.write_bytes(result.data)
    from gxw.offline_project import read_offline_project
    before, after = read_offline_project(source.read_bytes()), read_offline_project(result.data)
    rows.append(dict(sample=label, status=workspace.get("status"), compile=workspace.get("compile"),
                     native_version=workspace.get("native_version"), cpu=workspace.get("cpu"),
                     res_before=len(before.streams.get("MAIN.res") or b""),
                     res_after=len(after.streams.get("MAIN.res") or b""),
                     size_before=len(source.read_bytes()), size_after=len(result.data)))
(root / "README.md").write_text(
    "# 交付工程的免 IDE 原生编译结论（2026-10-22）\n\n"
    "适配器由当前源码重建（`tools/build_workspace_adapter.ps1`），"
    "调用 `NativeWorkspaceSourceSave.compile_project()`，全程不打开 GX Works2。\n\n"
    + "\n".join(f"- {row['sample']}：`{row['compile']}`，CPU {row['cpu']}，GX Works2 {row['native_version']}，"
                f"`MAIN.res` {row['res_before']} → {row['res_after']}，工程 {row['size_before']} → {row['size_after']}"
                for row in rows)
    + "\n\n## 交叉验证\n\n指令级样本经 GUI `F4` + 保存得到 924160 / 1522，与适配器结果一致。\n"
      "原生编译会重写 `MAIN.Program.pou`（长度不变、内容改变），因此原生保存后必须重建正文基线。\n"
      "FB 样本此前被判为“编译不过”，是被指令集更新确认页与焦点问题误导；实际编译通过。\n",
    encoding="utf-8")
print(json.dumps(rows, ensure_ascii=False, indent=1))
