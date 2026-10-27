# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Stage project notices and installed dependency licenses for executable ZIPs."""
import importlib.metadata as metadata
from pathlib import Path
import re
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]


def stage(package: Path, component: str):
    for name in ('LICENSE', 'NOTICE', 'LICENSE-SCOPE.md', 'THIRD_PARTY_NOTICES.md',
                 'README.md', 'README.zh-CN.md', 'README.ja.md', 'CHANGELOG.md'):
        shutil.copyfile(ROOT / name, package / name)
    if component == 'full':
        assets = package / 'resources/assets'
        assets.mkdir(parents=True, exist_ok=True)
        for source in (ROOT / 'resources/assets').glob('*.gif'):
            shutil.copyfile(source, assets / source.name)
        shutil.copyfile(ROOT / 'start-web.cmd', package / 'start-web.cmd')
        (package / 'scripts').mkdir(exist_ok=True)
        shutil.copyfile(ROOT / 'scripts/start_web.ps1', package / 'scripts/start_web.ps1')
    else:
        # Keep README demos usable without shipping their animation payloads.
        for name in ('README.md', 'README.zh-CN.md', 'README.ja.md'):
            path = package / name
            path.write_text(path.read_text(encoding='utf-8').replace(
                '(resources/assets/', '(https://raw.githubusercontent.com/Arienax/gxworks-agent/main/resources/assets/'),
                encoding='utf-8')
    shutil.copyfile(ROOT / 'gxworks-agent-mcp.cmd', package / 'gxworks-agent-mcp.cmd')
    output = package / 'THIRD_PARTY_LICENSES'
    output.mkdir(exist_ok=True)
    index = ['# Included dependency licenses', '']
    seen = set()
    for distribution in sorted(metadata.distributions(), key=lambda item: item.metadata['Name'].lower()):
        name = distribution.metadata['Name']
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        folder = output / re.sub(r'[^A-Za-z0-9_.-]', '_', name)
        folder.mkdir(exist_ok=True)
        (folder / 'package.txt').write_text(name + ' ' + distribution.version + '\n', encoding='utf-8')
        for count, relative in enumerate(distribution.files or ()):
            if not re.match(r'(?i)^(license|licence|copying|notice|authors)([._-].*|$)', relative.name):
                continue
            source = Path(distribution.locate_file(relative))
            if source.is_file() and source.suffix.lower() not in {'.py', '.pyc', '.pyd', '.dll'}:
                shutil.copyfile(source, folder / (str(count) + '-' + source.name))
        index.append('- ' + name + ' ' + distribution.version)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copyfile(python_license, output / 'Python-LICENSE.txt')
    for source in (ROOT / 'web/node_modules').rglob('*'):
        if source.is_file() and re.fullmatch(r'(?i)(license|licence|copying|notice)(\.(md|txt))?', source.name):
            target = output / 'frontend' / source.relative_to(ROOT / 'web/node_modules')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    (output / 'README.md').write_text('\n'.join(index) + '\n', encoding='utf-8')
