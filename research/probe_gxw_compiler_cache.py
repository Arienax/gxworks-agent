"""Read native compiler-cache Jet databases without treating them as source.

Only extracted copies are opened, with Mode=Read. Original stream bytes are
preserved and checked after each query. The installed, hash-bound vendor
CreateConnectString supplies its constant cache-format key; project passwords,
protection settings and user credentials are neither read nor changed.
These derived caches can be empty or stale even when source/compile succeeds.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from gxw.lossless import inspect_project

DLL = Path('D:/GXWORKS2/DNaviZero/DataAbsorber/DZDataABS_DataManager_IEC.dll')
DLL_SHA256 = '4e6785f6797f4b621b0df3876172e45b43bb8f60a85abe715b1edbba05fc2b6d'
CACHES = ('DeviceAssignment.dat', 'ProgramAnalysis.dat', 'DebugInformation.dat')


def _installed_cache_key() -> str:
    raw = DLL.read_bytes()
    if hashlib.sha256(raw).hexdigest() != DLL_SHA256:
        raise ValueError('uninspected native compiler-cache implementation')
    # CreateConnectString export 0x4f162 passes this literal to the database
    # connection formatter at 0x4f170. Resolve its PE RVA without rebasing.
    pe = struct.unpack_from('<I', raw, 0x3c)[0]
    count, optional_size = struct.unpack_from('<H', raw, pe + 6)[0], struct.unpack_from('<H', raw, pe + 20)[0]
    table = pe + 24 + optional_size
    for index in range(count):
        start = table + index * 40
        virtual_size, rva, raw_size, offset = struct.unpack_from('<4I', raw, start + 8)
        if rva <= 0x8a230 < rva + min(virtual_size, raw_size):
            value = raw[offset + 0x8a230 - rva:offset + 0x8a230 - rva + 128]
            if b'\0' not in value:
                break
            return value.split(b'\0', 1)[0].decode('ascii')
    raise ValueError('native cache constant is outside a bounded PE section')


def inspect(source: bytes, directory: Path) -> dict:
    if os.name != 'nt':
        raise ValueError('requires the installed Windows native cache provider')
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    image = inspect_project(source)
    if image.diagnostics or any(s.error for s in image.streams):
        raise ValueError('incomplete project inventory')
    streams = {s.logical_name: s.raw for s in image.streams if s.logical_name}
    result = dict(source_sha256=hashlib.sha256(source).hexdigest(), native_dll_sha256=DLL_SHA256,
        scope='read-only derived compiler caches; freshness and source consistency not established', caches=[])
    helper = directory / 'NativeJetSnapshot.cs'
    shutil.copyfile(ROOT / 'research/native/NativeJetSnapshot.cs', helper)
    exe = directory / 'NativeJetSnapshot.exe'
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework/v4.0.30319/csc.exe'
    build = subprocess.run([str(compiler), '/nologo', '/platform:x86', '/r:System.Data.dll',
        '/r:System.Web.Extensions.dll', '/out:' + str(exe), str(helper)], capture_output=True, timeout=30)
    (directory / 'build.stdout').write_bytes(build.stdout)
    (directory / 'build.stderr').write_bytes(build.stderr)
    build.check_returncode()
    result['helper_sha256'] = hashlib.sha256(helper.read_bytes()).hexdigest()
    env = dict(os.environ, GXW_NATIVE_JET_CACHE_KEY=_installed_cache_key())
    for name in CACHES:
        if name not in streams:
            result['caches'].append(dict(name=name, handling='absent'))
            continue
        raw = streams[name]
        item = dict(name=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), handling='opaque-preserved')
        result['caches'].append(item)
        if raw[:20] != b'\x00\x01\x00\x00Standard Jet DB\x00':
            item['diagnostic'] = 'outside the observed native Jet cache format'
            continue
        extracted, output = directory / name, directory / (name + '.json')
        extracted.write_bytes(raw)
        try:
            process = subprocess.run([str(exe), str(extracted), str(output)], env=env,
                capture_output=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
            item['returncode'] = process.returncode
            if output.exists():
                snapshot = json.loads(output.read_text(encoding='utf-8-sig'))
                item['error'] = snapshot.get('error')
                item['tables'] = [dict(name=t['name'], rows=len(t['rows'])) for t in snapshot.get('tables', [])]
                if process.returncode == 0 and not item['error']:
                    item['handling'] = 'native-queryable'
                    item['snapshot'] = output.name
        except subprocess.TimeoutExpired:
            item['diagnostic'] = 'native cache query timed out; copied input retained'
        if extracted.read_bytes() != raw:
            raise ValueError('read-only query changed its copied cache input')
    (directory / 'inventory.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect(args.source.read_bytes(), args.output)))
