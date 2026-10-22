"""Ordinary PyInstaller distribution with reviewed resources and native helpers."""
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata, get_package_paths, collect_submodules

root = Path(SPECPATH).resolve().parents[1]
sys.path.insert(0, str(root))
from scripts.runtime_knowledge_resources import runtime_knowledge_data, runtime_instruction_data, runtime_viewer_data

component = os.environ['GX_RELEASE_COMPONENT']
if component not in {'full', 'mcp'}:
    raise ValueError('Unknown release component')
resources = Path(os.environ['GX_RELEASE_RESOURCES']).resolve()
helpers = Path(os.environ['GX_RELEASE_HELPERS']).resolve()
name = 'GXWorks-Agent-Full' if component == 'full' else 'GXWorks-Agent-MCP'

datas = [
    (str(resources / 'gxw/templates'), 'gxw/templates'),
    (str(root / 'resources/model_catalog'), 'resources/model_catalog'),
    (str(root / 'resources/locales'), 'resources/locales'),
    (str(root / 'resources/config.default.json'), '.'),
    (str(root / 'resources/pattern_library.json'), '.'),
    (str(root / 'resources/plc_models.json'), '.'),
    (str(root / 'resources/instructions/control_runtime.json'), 'resources/instructions'),
    (str(root / 'skills'), 'skills'),
]
datas += runtime_knowledge_data(resources) + runtime_instruction_data(resources)
datas += ([(str(root / 'web/dist'), 'web/dist')] if component == 'full' else runtime_viewer_data(root))
datas += copy_metadata('haystack-ai')
datas += copy_metadata('tree-sitter') + copy_metadata('tree-sitter-iec61131-3-st')
for directory, executable in (
    ('workspace-adapter', 'PlcAi.WorkspaceSourceSave.exe'),
    ('native-active', 'PlcAi.ActiveDocument.exe'),
    ('hardware-reader', 'PlcAi.HardwareReader.exe'),
    ('simulator-gateway', 'PlcAi.GxSimulator2Gateway.exe'),
):
    source = helpers / directory / executable
    if not source.is_file():
        raise FileNotFoundError(source)
    datas.append((str(source), directory))

# Let Analysis follow the entry's imports. Collecting every application/Haystack
# module also ships offline PDF ingestion, optional integrations and model SDKs.
# Only imports resolved dynamically at runtime need explicit roots here.
hiddenimports = [
    'pythoncom', 'pywinauto',
    'pywinauto.controls.uia_controls', 'comtypes.client', 'win32com.client',
    'anyio._backends._asyncio',
]

# Haystack's lazy package exports contain TYPE_CHECKING imports of every
# optional component. Our retrieval boundary uses only MetadataRouter; prevent
# those type-only imports from pulling PDF/model integrations into either build.
haystack_components = Path(get_package_paths('haystack')[1]) / 'components'
unused_components = [
    'haystack.components.' + path.stem for path in haystack_components.iterdir()
    if path.name not in {'__init__.py', '__pycache__', 'routers'}
    and (path.is_dir() or path.suffix == '.py')
] + [
    'haystack.components.routers.' + path.stem
    for path in (haystack_components / 'routers').glob('*.py')
    if path.name not in {'__init__.py', 'metadata_router.py'}
]

def freeze(entry, executable_name, *, web=False):
    dynamic_imports = hiddenimports + collect_submodules('tree_sitter') + collect_submodules('tree_sitter_iec61131_3_st') + (['uvicorn.logging', 'uvicorn.loops.asyncio',
        'uvicorn.protocols.http.h11_impl', 'uvicorn.lifespan.on'] if web else [])
    a = Analysis([str(root / 'scripts' / entry)], pathex=[str(root / 'src')],
        binaries=[], datas=datas, hiddenimports=dynamic_imports,
        hookspath=[], hooksconfig={}, runtime_hooks=[],
        excludes=unused_components + ['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'tkinter', 'mcp.cli',
                 'pytest', '_pytest', 'IPython'] +
                 ([] if web else ['integrations.web', 'fastapi', 'uvicorn', 'openai', 'jiter']),
        noarchive=False, optimize=1)
    pyz = PYZ(a.pure)
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=executable_name,
        debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
        console=True, disable_windowed_traceback=False, contents_directory='.',
        icon=[str(root / 'resources/app.ico')])
    return exe, a.binaries, a.datas


collected = list(freeze('mcp_entry.py', 'gxworks-agent-mcp'))
if component == 'full':
    collected.extend(freeze('web_entry.py', 'GXWorks-Agent-Web', web=True))
# Both entrypoints share DLLs, templates, knowledge and frontend assets in one
# onedir distribution. No nested copy of the standalone MCP distribution.
coll = COLLECT(*collected, strip=False, upx=False, name=name)
