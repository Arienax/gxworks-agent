# -*- mode: python ; coding: utf-8 -*-
"""Build the product MCP launcher beside the Web workbench executable."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


root = Path(SPECPATH).resolve().parents[1]
required = (
    "resources/pattern_library.json", "resources/plc_models.json",
    "resources/knowledge/fx3u_knowledge.sqlite", "resources/knowledge/fx3u_dense_lsa.npz",
    "resources/knowledge/manifest.json", "resources/knowledge/THIRD_PARTY_NOTICES.md",
    "resources/app.ico", "LICENSE", "docs/integrations/mcp.md",
)
for relative in required:
    if not (root / relative).is_file():
        raise SystemExit("Missing MCP release resource: " + relative)
if not (root / "resources/instructions/mitsubishi").is_dir():
    raise SystemExit("Missing Mitsubishi instruction resources")


datas = [
    (str(root / "src/gxw/templates"), "gxw/templates"),
    (str(root / "resources/pattern_library.json"), "."),
    (str(root / "resources/plc_models.json"), "."),
    (str(root / "resources/instructions/mitsubishi"), "resources/instructions/mitsubishi"),
    (str(root / "resources/knowledge"), "knowledge"),
    (str(root / "resources/app.ico"), "."),
    (str(root / "LICENSE"), "."),
    (str(root / "docs/integrations/mcp.md"), "docs/integrations"),
]

binaries, hiddenimports = [], ["anyio._backends._asyncio"]
# Do not collect_all("mcp"). The MCP SDK deliberately keeps its command-line
# interface behind the optional ``mcp[cli]`` extra; recursively collecting the
# whole package imports ``mcp.cli`` during analysis and makes a core-only install
# fail when Typer is absent. The product launcher uses the SDK's server/stdio
# modules, which PyInstaller discovers from the normal application import graph.
for package in ("pydantic", "pydantic_core", "annotated_types", "typing_inspection"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package, on_error="warn once")
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hiddenimports)


a = Analysis(
    [str(root / "scripts/mcp_entry.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt5", "PyQt6", "PySide2", "PySide6", "ui.desktop", "main",
        "tkinter", "openai", "pywinauto", "win32com", "comtypes", "mcp.cli",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="gxworks-agent-mcp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    icon=[str(root / "resources/app.ico")],
)
