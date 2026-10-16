"""Build independent Windows MCP/Web executable distributions from source."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from runtime_knowledge_resources import FILES, INSTRUCTION_FILES, runtime_knowledge_data
from release_notices import stage as stage_notices


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resource-root', type=Path, required=True,
                        help='Root containing the separately reviewed runtime fact resources')
    parser.add_argument('--component', choices=('web', 'mcp', 'both'), default='both')
    parser.add_argument('--skip-frontend', action='store_true')
    parser.add_argument('--version', default='0.2.1')
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or output.is_relative_to(ROOT) or ROOT.is_relative_to(output):
        parser.error('use a separate build directory outside the source checkout')
    output.mkdir(parents=True, exist_ok=True)
    logs = output / 'logs'
    logs.mkdir(exist_ok=True)

    def run(label, command, *, env=None, cwd=ROOT):
        print(label, flush=True)
        with (logs / (label + '.log')).open('wb') as stream:
            result = subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            print((logs / (label + '.log')).read_text(encoding='utf-8', errors='replace')[-5000:])
            raise SystemExit(label + ' failed; inspect the build log')

    if not args.skip_frontend:
        npm = shutil.which('npm.cmd')
        if not npm:
            parser.error('Node.js/npm is required to build the frontend')
        run('frontend-install', [npm, 'ci'], cwd=ROOT / 'web')
        run('frontend-build', [npm, 'run', 'build'], cwd=ROOT / 'web')
    resource_root = args.resource_root.resolve()
    inputs = output / 'inputs'
    paths = [relative for relative, _ in FILES]
    paths += ['resources/instructions/mitsubishi/' + name for name in INSTRUCTION_FILES]
    for name in paths:
        source = (ROOT if name.endswith('/fx3u_step_widths.json') else resource_root) / name
        if not source.is_file():
            raise FileNotFoundError(source)
        target = inputs / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    runtime_knowledge_data(inputs)
    helpers = output / 'helpers'
    for label, script, directory in (
        ('workspace-helper', 'build_workspace_adapter.ps1', 'workspace-adapter'),
        ('hardware-helper', 'build_hardware_reader.ps1', 'hardware-reader'),
        ('simulator-helper', 'build_simulator_gateway.ps1', 'simulator-gateway'),
    ):
        run(label, ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                    '-File', str(ROOT / 'tools' / script), '-OutputDirectory', str(helpers / directory)])
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    components = ('mcp', 'web') if args.component == 'both' else (args.component,)
    artifacts = output / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    for component in components:
        env = os.environ.copy()
        env.update(GX_RELEASE_COMPONENT=component, GX_RELEASE_RESOURCES=str(inputs), GX_RELEASE_HELPERS=str(helpers))
        run('freeze-' + component, [sys.executable, '-m', 'PyInstaller', '--noconfirm',
            '--distpath', str(output / 'dist'), '--workpath', str(output / 'work' / component),
            str(ROOT / 'packaging/pyinstaller/release.spec')], env=env)
        name = 'GXWorks-Agent-' + ('Web' if component == 'web' else 'MCP')
        package = output / 'dist' / name
        stage_notices(package, component)
        (package / 'release.json').write_text(json.dumps({'version': args.version,
            'source_commit': commit, 'component': component, 'license': 'Apache-2.0',
            'runtime_corpus': 'authored_plc_facts_v1'}, indent=2) + '\n', encoding='utf-8')
        run('smoke-' + component, [sys.executable, str(ROOT / 'scripts/release_smoke.py'), component, str(package)])
        archive = artifacts / (name + '-windows-x64.zip')
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for path in sorted(package.rglob('*')):
                if path.is_file():
                    bundle.write(path, name + '/' + path.relative_to(package).as_posix())
        print(str(archive), flush=True)
    print('Executable packages built and checked', flush=True)


if __name__ == '__main__':
    main()
