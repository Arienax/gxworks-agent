# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Reject research materials, development output and credentials in source trees."""
from __future__ import annotations

import argparse
import io
import json
import lzma
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import subprocess
import sys
from zipfile import BadZipFile, ZipFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from publication.source_policy import CACHE_DIRECTORIES, exclusion_reason


# Minimum delivery contents, not a list of permitted source files.
REQUIRED = {
    'LICENSE', 'NOTICE', 'LICENSING.md', 'ACCESSIBILITY.md', 'CODE_OF_CONDUCT.md', 'CONTRIBUTING.md', 'SECURITY.md', 'README.md', 'README.zh-CN.md',
    'README.ja.md', 'publication/README.md', 'publication/source_policy.py',
    'src/gxw/project_templates.py',
    'src/gxw/templates/blank-fbd-projects.zip.xz',
    'src/gxw/templates/blank-ladder-projects.zip.xz',
    'src/gxw/templates/fx3u.gxw', 'src/gxw/templates/nodes.json',
    'src/gxw/templates/provenance.json',
    'src/gxw/templates/q02-token-grammar.json',
    'src/gxw/templates/q03udv-token-grammar.json',
    'src/gxw/templates/fx-token-encoder-forms.json',
    'src/gxw/templates/q-token-encoder-forms.json',
    'resources/instructions/mitsubishi/common.json',
    'resources/instructions/mitsubishi/fbd_language_rules.json',
    'resources/instructions/mitsubishi/pwm_contract.generated.json',
    'resources/instructions/mitsubishi/fx3u.json',
    'resources/instructions/mitsubishi/fx5u.json',
    'resources/instructions/mitsubishi/fx3u_step_widths.json',
    'resources/instructions/mitsubishi/fx3u_verified_opcodes.json',
    'resources/instructions/mitsubishi/modifier_rules.json',
    'resources/instructions/mitsubishi/instruction_capabilities.json',
    'checks/source_smoke.py',
}
CFB_SIGNATURE = bytes.fromhex('d0cf11e0a1b11ae1')
MEDIA_SIGNATURES = {
    '.png': (b'\x89PNG\r\n\x1a\n',), '.gif': (b'GIF87a', b'GIF89a'),
    '.jpg': (b'\xff\xd8\xff',), '.jpeg': (b'\xff\xd8\xff',),
    '.ico': (b'\x00\x00\x01\x00',),
}
PATTERNS = {
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'GitHub token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    'API token': re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b'),
    'local user path': re.compile(r'(?i)[a-z]:[\\/]+Users[\\/]+[^\s"<>]+'),
    'private evidence path': re.compile(r'(?i)research[/\\]evidence[/\\]'),
}


def names_in_index(*, root: Path = ROOT) -> set[str]:
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=root, check=True,
        stdout=subprocess.PIPE).stdout
    return {item.decode('utf-8') for item in result.split(b'\0') if item}


def names_on_disk(*, root: Path = ROOT) -> set[str]:
    result = set()
    for directory, folders, names in os.walk(root):
        folders[:] = [name for name in folders if name.casefold() not in CACHE_DIRECTORIES]
        for name in names:
            if name != '.git':
                result.add((Path(directory) / name).relative_to(root).as_posix())
    return result


def check_content(text: str) -> list[str]:
    problems = []
    for label, pattern in PATTERNS.items():
        matches = list(pattern.finditer(text))
        if label == 'API token':
            matches = [match for match in matches if match.group() != 'sk-offline-package-self-test']
        if matches:
            problems.append(f'possible {label} (value withheld)')
    return problems


def check_archive(data: bytes, expected: int | None = 57) -> list[str]:
    """Inspect runtime template contents as well as their outer archive path."""
    problems = []
    try:
        with ZipFile(io.BytesIO(lzma.decompress(data))) as archive:
            names = archive.namelist()
            projects = [name for name in names if name.startswith('projects/') and name.endswith('.gxw')]
            if expected is not None and len(projects) != expected:
                problems.append(f'blank template archive must contain {expected} projects')
            if (not projects or len(names) != len(set(names))
                    or set(names) != set(projects) | {'manifest.json'}):
                problems.append('blank template archive must contain projects and one manifest')
            for name in names:
                path = PurePosixPath(name.replace('\\', '/'))
                if path.is_absolute() or PureWindowsPath(name).drive or '..' in path.parts:
                    problems.append('template archive contains an unsafe path')
                reason = exclusion_reason(name)
                # The only material allowed inside a blank template is its GXW payload.
                if reason and name not in projects:
                    problems.append(f'{name}: {reason}')
                problems.extend(f'{name}: {problem}' for problem in check_content(name))
                raw = archive.read(name)
                if raw.startswith(b'version https://git-lfs.github.com/spec/v1'):
                    problems.append(f'{name}: unresolved LFS pointer')
                problems.extend(f'{name}: {problem}' for problem in check_content(raw.decode('latin-1')))
                if name in projects and not raw.startswith(CFB_SIGNATURE):
                    problems.append(f'{name}: not a GXW compound document')
            manifest = json.loads(archive.read('manifest.json'))
            models = manifest.get('models') if isinstance(manifest, dict) else None
            if (not isinstance(models, list) or any(not isinstance(row, dict)
                    or not isinstance(row.get('file'), str) for row in models)):
                problems.append('invalid template manifest models')
            elif {row['file'] for row in models} != set(projects) or len(models) != len(projects):
                problems.append('template manifest does not match archived projects')
    except (OSError, ValueError, KeyError, RuntimeError, EOFError, lzma.LZMAError, BadZipFile) as exc:
        problems.append(f'template archive cannot be read: {type(exc).__name__}')
    return problems


def check_file(name: str, *, root: Path = ROOT) -> list[str]:
    reason = exclusion_reason(name)
    if reason:
        return [f'{name}: {reason}']
    path = root / name
    if not path.is_file() or path.is_symlink():
        return [f'{name}: missing, linked or not a file']
    data = path.read_bytes()
    if data.startswith(b'version https://git-lfs.github.com/spec/v1'):
        return [f'{name}: unresolved LFS pointer']
    value = name.replace('\\', '/').casefold()
    if value.startswith('src/gxw/templates/') and value.endswith('.zip.xz'):
        expected = 57 if value.endswith('/blank-fbd-projects.zip.xz') else None
        return [f'{name}: {problem}' for problem in check_archive(data, expected)]
    if value.startswith('src/gxw/templates/') and value.endswith('.gxw'):
        if not data.startswith(CFB_SIGNATURE):
            return [f'{name}: not a GXW compound document']
        return [f'{name}: {problem}' for problem in check_content(data.decode('latin-1'))]
    suffix = path.suffix.casefold()
    if suffix in MEDIA_SIGNATURES or suffix == '.webp':
        valid = (data.startswith(MEDIA_SIGNATURES[suffix]) if suffix in MEDIA_SIGNATURES
            else data.startswith(b'RIFF') and data[8:12] == b'WEBP')
        if not valid:
            return [f'{name}: invalid {suffix} media signature']
        return [f'{name}: {problem}' for problem in check_content(data.decode('latin-1'))]
    if b'\0' in data:
        return [f'{name}: unexpected binary content']
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return [f'{name}: not UTF-8; unsupported binary format']
    if path.name == '.gitattributes':
        text = '\n'.join(line.replace('research/' + 'evidence/', 'research-material-pattern/')
            if line.split()[1:] == ['filter=lfs', 'diff=lfs', 'merge=lfs', '-text'] else line
            for line in text.splitlines() if line.strip())
    return [f'{name}: {problem}' for problem in check_content(text)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--disk', action='store_true', help='inspect snapshot before Git staging')
    args = parser.parse_args()
    names = names_on_disk(root=ROOT) if args.disk else names_in_index(root=ROOT)
    problems = [f'missing required source/template: {name}' for name in sorted(REQUIRED - names)]
    problems.extend(problem for name in sorted(names) for problem in check_file(name, root=ROOT))
    if problems:
        print('\n'.join(problems), file=sys.stderr)
        return 1
    print(f'Public material separation OK: {len(names)} files; no per-file allowlist')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
