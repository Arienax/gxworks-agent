# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files


root = Path(SPECPATH).resolve().parents[1]
source = root / "src"

sdk_datas = collect_data_files('PyQt5', includes=['**/translations/qtbase_ja.qm', '**/translations/qtbase_zh_CN.qm'])
sdk_binaries = []
sdk_hiddenimports = []
for package_name in (
    'pydantic',
    'pydantic_core',
    'annotated_types',
    'typing_inspection',
    'jiter',
):
    package_datas, package_binaries, package_hiddenimports = collect_all(
        package_name,
        on_error='warn once',
    )
    sdk_datas += package_datas
    sdk_binaries += package_binaries
    sdk_hiddenimports += package_hiddenimports

a = Analysis(
    [str(source / 'main.py')],
    pathex=[str(source)],
    binaries=sdk_binaries,
    datas=[
        (str(root / 'resources/config.default.json'), '.'),
        (str(root / 'resources/locales'), 'resources/locales'),
        (str(root / 'resources/pattern_library.json'), '.'),
        (str(root / 'resources/plc_models.json'), '.'),
        (str(root / 'resources/model_catalog'), 'resources/model_catalog'),
        (str(root / 'resources/instructions/mitsubishi'), 'resources/instructions/mitsubishi'),
        (str(root / 'README.md'), '.'),
        (str(root / 'resources/app.ico'), '.'),
        (str(root / 'resources/assets/codicons'), 'assets/codicons'),
        (str(root / 'resources/knowledge/fx3u_knowledge.sqlite'), 'knowledge'),
        (str(root / 'resources/knowledge/fx3u_dense_lsa.npz'), 'knowledge'),
        (str(root / 'resources/knowledge/manifest.json'), 'knowledge'),
    ] + sdk_datas,
    hiddenimports=[
        'ui.desktop.workbench.editor',
        'ui.desktop.workbench.review',
        'openai',
        'openai._client',
        'pywinauto',
        'pywinauto.controls.uia_controls',
        'comtypes.client',
        'numpy',
    ] + sdk_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'xmlrpc', 'pydoc', 'PyQt6'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GXWorks2-ST-Ladder-Helper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(root / 'resources/app.ico')],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='GXWorks2-ST-Ladder-Helper',
)
