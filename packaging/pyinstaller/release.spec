"""Ordinary PyInstaller distribution with reviewed resources and native helpers."""
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).resolve().parents[1]
sys.path.insert(0, str(root))
from scripts.runtime_knowledge_resources import runtime_knowledge_data, runtime_instruction_data, runtime_viewer_data

component = os.environ['GX_RELEASE_COMPONENT']
if component not in {'web', 'mcp'}:
    raise ValueError('Unknown release component')
resources = Path(os.environ['GX_RELEASE_RESOURCES']).resolve()
helpers = Path(os.environ['GX_RELEASE_HELPERS']).resolve()
name = 'GXWorks-Agent-Web' if component == 'web' else 'GXWorks-Agent-MCP'
entry = 'web_entry.py' if component == 'web' else 'mcp_entry.py'
executable_name = 'GXWorks-Agent-Web' if component == 'web' else 'gxworks-agent-mcp'

datas = [
    (str(root / 'src/gxw/templates'), 'gxw/templates'),
    (str(root / 'resources/model_catalog'), 'resources/model_catalog'),
    (str(root / 'resources/locales'), 'resources/locales'),
    (str(root / 'resources/config.default.json'), '.'),
    (str(root / 'resources/pattern_library.json'), '.'),
    (str(root / 'resources/plc_models.json'), '.'),
    (str(root / 'resources/instructions/control_runtime.json'), 'resources/instructions'),
]
datas += runtime_knowledge_data(resources) + runtime_instruction_data(resources)
datas += ([(str(root / 'web/dist'), 'web/dist')] if component == 'web' else runtime_viewer_data(root))
for directory, executable in (
    ('workspace-adapter', 'PlcAi.WorkspaceSourceSave.exe'),
    ('hardware-reader', 'PlcAi.HardwareReader.exe'),
    ('simulator-gateway', 'PlcAi.GxSimulator2Gateway.exe'),
):
    source = helpers / directory / executable
    if not source.is_file():
        raise FileNotFoundError(source)
    datas.append((str(source), directory))

hiddenimports = []
for source in (root / 'src').rglob('*.py'):
    parts = source.relative_to(root / 'src').with_suffix('').parts
    module = '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)
    if module and not (component == 'mcp' and module.startswith('integrations.web')):
        hiddenimports.append(module)
hiddenimports += [
    'openai', 'openai._client', 'numpy', 'pythoncom', 'pywinauto',
    'pywinauto.controls.uia_controls', 'comtypes.client', 'win32com.client',
    'anyio._backends._asyncio',
]
if component == 'web':
    hiddenimports += ['uvicorn.logging', 'uvicorn.loops.asyncio',
        'uvicorn.protocols.http.h11_impl', 'uvicorn.lifespan.on']
binaries = []
for package in ('pydantic', 'pydantic_core', 'annotated_types', 'typing_inspection',
                'jiter', 'haystack', 'haystack_experimental', 'lazy_imports', 'docstring_parser'):
    package_datas, package_binaries, package_hidden = collect_all(package, on_error='warn once')
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis([str(root / 'scripts' / entry)], pathex=[str(root / 'src')],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'tkinter', 'mcp.cli'] +
             (['integrations.web', 'fastapi', 'uvicorn'] if component == 'mcp' else []),
    noarchive=False, optimize=1)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=executable_name,
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True, disable_windowed_traceback=False, contents_directory='.',
    icon=[str(root / 'resources/app.ico')])
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=name)
