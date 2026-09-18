"""Rebuild a reviewed tree from immutable base blobs; never modify the worktree."""
import hashlib
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile

BASE = '295378d6ac5d24949c03a1c799906f4afe0fd4c9'
TREE = '5750218c3f3a93e90301bb33493855a5f4efe5e9'
DIGEST = '1747d8379e0c6975a906130d72e1d078cde48ff9a2c216a77fadb130e593b895'


def git(*args, input=None, env=None):
    return subprocess.check_output(['git', *args], input=input, env=env)


def safe_path(value):
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or '.' in path.parts or '\\' in value:
        raise ValueError('Unsafe path')
    if path.parts[0] not in {'src', 'tests', 'scripts', 'tools', 'docs', 'research', '.github', '.gitignore', 'AGENTS.md', 'README.md', 'README.zh-CN.md', 'packaging'}:
        raise ValueError('Unexpected edit scope: ' + value)
    return value


def rebuild(data):
    if hashlib.sha256(data).hexdigest() != DIGEST:
        raise ValueError('Transport payload digest differs from the tested bundle')
    payload = json.loads(lzma.decompress(data))
    if (payload['base'], payload['tree']) != (BASE, TREE):
        raise ValueError('Unexpected base or tree')
    cache = {}
    with tempfile.TemporaryDirectory(prefix='source-layout-index-') as temporary:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temporary) / 'index'))
        git('read-tree', BASE, env=env)
        for path in payload['delete']:
            git('update-index', '--force-remove', '--', safe_path(path), env=env)
        for item in payload['files']:
            path = safe_path(item['p'])
            lines = []
            for source in item['s']:
                safe_path(source)
                if source not in cache:
                    cache[source] = git('show', BASE + ':' + source).decode('utf-8')
                lines.extend(cache[source].splitlines(keepends=True))
            parts = []
            for chunk in item['o']:
                if isinstance(chunk, str):
                    parts.append(chunk)
                elif isinstance(chunk, list) and len(chunk) == 2 and all(type(n) is int for n in chunk) and 0 <= chunk[0] <= chunk[1] <= len(lines):
                    parts.append(''.join(lines[chunk[0]:chunk[1]]))
                else:
                    raise ValueError('Invalid copy record for ' + path)
            blob = git('hash-object', '-w', '--stdin', input=''.join(parts).encode('utf-8')).decode().strip()
            mode = item.get('m', '100644')
            if mode not in {'100644', '100755'}:
                raise ValueError('Invalid source mode')
            git('update-index', '--add', '--cacheinfo', mode, blob, path, env=env)
        actual = git('write-tree', env=env).decode().strip()
        if actual != TREE:
            raise ValueError('Rebuilt tree does not match the locally tested tree: ' + actual)
    print('Verified exact source tree:', actual)
    print('Updated files:', len(payload['files']), 'Removed old paths:', len(payload['delete']))
    return actual


if __name__ == '__main__':
    import sys
    data = b''.join(Path(p).read_bytes() for p in sys.argv[1:])
    rebuild(data)
