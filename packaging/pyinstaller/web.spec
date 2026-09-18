# -*- mode: python ; coding: utf-8 -*-
"""Build after scripts/build_web_package.ps1 validates assets and dependencies."""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


root = Path(SPECPATH).resolve().parents[1]
required = (
    "web/dist/index.html", "resources/config.default.json", "resources/pattern_library.json",
    "resources/plc_models.json", "resources/knowledge/fx3u_knowledge.sqlite",
    "resources/knowledge/fx3u_dense_lsa.npz", "resources/knowledge/manifest.json",
    "resources/knowledge/THIRD_PARTY_NOTICES.md", "resources/locales/en.json",
    "resources/locales/ja.json", "resources/app.ico", "LICENSE", "start-web.cmd", "scripts/start_web.ps1",
)
for relative in required:
    if not (root / relative).is_file():
        raise SystemExit("Missing release resource: " + relative)
if not (root / "resources/instructions/mitsubishi").is_dir():
    raise SystemExit("Missing Mitsubishi instruction resources")

datas = [
    (str(root / "src/gxw/templates"), "gxw/templates"),
    (str(root / "web/dist"), "web/dist"),
    (str(root / "resources/model_catalog"), "resources/model_catalog"),
    (str(root / "resources/config.default.json"), "."),
    (str(root / "resources/pattern_library.json"), "."),
    (str(root / "resources/plc_models.json"), "."),
    (str(root / "resources/locales"), "resources/locales"),
    (str(root / "resources/instructions/mitsubishi"), "resources/instructions/mitsubishi"),
    (str(root / "resources/knowledge"), "knowledge"),
    (str(root / "resources/app.ico"), "."),
    (str(root / "README.md"), "."),
    (str(root / "README.zh-CN.md"), "."),
    (str(root / "start-web.cmd"), "."),
    (str(root / "scripts/start_web.ps1"), "scripts"),
    (str(root / "LICENSE"), "."),
    (str(root / "docs/integrations/web.md"), "docs/integrations"),
    (str(root / "docs/architecture/web-migration-checklist.md"), "docs/architecture"),
    (str(root / "docs/architecture/web-migration-baseline.md"), "docs/architecture"),
    (str(root / "simulator_gateway/README.md"), "docs/simulator_gateway"),
]
gateway_dir = os.environ.get("GX_WEB_PACKAGE_GATEWAY_DIR", "").strip()
if gateway_dir:
    gateway_dir = Path(gateway_dir).resolve()
    if not (gateway_dir / "PlcAi.GxSimulator2Gateway.exe").is_file():
        raise SystemExit("Configured gateway directory does not contain the gateway executable")
    datas.append((str(gateway_dir), "simulator-gateway"))
elif os.environ.get("GX_WEB_PACKAGE_ALLOW_WITHOUT_GATEWAY") != "1":
    raise SystemExit("Provide a built gateway directory or explicitly permit a package without the gateway")

binaries, hiddenimports = [], [
    "openai", "openai._client", "numpy", "pythoncom", "pywinauto",
    "pywinauto.controls.uia_controls", "comtypes.client", "win32com.client",
    "uvicorn.logging", "uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl",
    "uvicorn.lifespan.on", "anyio._backends._asyncio",
]
for package in ("pydantic", "pydantic_core", "annotated_types", "typing_inspection", "jiter"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package, on_error="warn once")
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hiddenimports)

a = Analysis(
    [str(root / "scripts/web_entry.py")], pathex=[str(root / "src")],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "PySide6", "ui.desktop", "main", "tkinter", "mcp"],
    noarchive=False, optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="GXWorks-Agent-Web",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True, disable_windowed_traceback=False,
    # Runtime gateway lookup expects simulator-gateway beside the executable.
    contents_directory=".", icon=[str(root / "resources/app.ico")],
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="GXWorks-Agent-Web")
