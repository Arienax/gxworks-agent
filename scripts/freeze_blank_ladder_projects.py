"""Freeze captured blank Ladder projects into the shipped template archive.

A generated Ladder program needs a project to live in, and the shipped FBD archive holds
FBD programs only: reading one as Ladder is refused, and rewriting a CPU string inside an
FBD file would not make it a Ladder project. This script freezes real captures instead.

Capture, in GX Works2, one new project per CPU menu identity, with a single empty Ladder
Program and no edits, and save each as <capture-dir>/<anything>.gxw. Then run:

    py scripts/freeze_blank_ladder_projects.py --capture-dir <dir>
    py scripts/freeze_blank_ladder_projects.py --capture-dir <dir> --write

Without --write the run only reports what it would freeze. Every capture is opened,
required to hold exactly one readable, offline-editable Ladder Program, and required to
record the CPU it is filed under, so a mislabelled file fails here rather than becoming a
"Ladder" project that is not Ladder.

After --write, the archive exists but is not yet delivered: add
src/gxw/templates/blank-ladder-projects.zip.xz to template_data's allow-list (already
present), then run the template and release checks. Public export includes
runtime templates automatically; no per-file publication registration is needed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import sys
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gxw.models import GXWFormatError  # noqa: E402
from gxw.project_templates import (LADDER_ARCHIVE, captured_project_cpu,  # noqa: E402
                                   verify_ladder_baseline)
from gxworks2.project_profiles import native_project_profile  # noqa: E402

ARCHIVE = ROOT / "src" / "gxw" / "templates" / LADDER_ARCHIVE


def _cpu_for(raw, override):
    """Menu identity of a capture: explicit override, else the CPU recorded in it."""
    cpu = override or captured_project_cpu(raw)
    profile = native_project_profile(cpu)
    if profile is None:
        raise GXWFormatError(f'capture records unknown CPU {cpu!r}')
    if not profile.program_supported:
        raise GXWFormatError(f'CPU {profile.cpu!r} is parameter-only; it has no Ladder Program')
    return profile.cpu


def collect(capture_dir, overrides=None):
    overrides = overrides or {}
    captures = sorted(Path(capture_dir).glob('*.gxw'))
    if not captures:
        raise SystemExit(f'no .gxw capture in {capture_dir}')
    models, failures = {}, []
    for path in captures:
        raw = path.read_bytes()
        try:
            cpu = _cpu_for(raw, overrides.get(path.name))
            source_name = verify_ladder_baseline(raw, cpu)
        except GXWFormatError as error:
            failures.append({'file': path.name, 'reason': str(error)})
            continue
        if cpu in models:
            failures.append({'file': path.name,
                             'reason': f'second capture for CPU {cpu!r}; keep one per CPU'})
            continue
        models[cpu] = {'cpu': cpu, 'file': f'projects/{cpu}.gxw',
                       'sha256': hashlib.sha256(raw).hexdigest(),
                       'size': len(raw), 'program': source_name, 'capture': path.name}
        models[cpu]['data'] = raw
    if failures:
        for failure in failures:
            print(f'refused {failure["file"]}: {failure["reason"]}')
        raise SystemExit('capture set rejected; nothing was frozen')
    return models


def write_archive(models, target=ARCHIVE):
    manifest = {'kind': 'blank-ladder-projects', 'version': 1,
                'models': [{key: row[key] for key in ('cpu', 'file', 'sha256', 'size', 'program')}
                           for _, row in sorted(models.items())]}
    buffer = BytesIO()
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps(manifest, indent=1, ensure_ascii=False))
        for _, row in sorted(models.items()):
            archive.writestr(row['file'], row['data'])
    target.write_bytes(lzma.compress(buffer.getvalue(), preset=6))
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture-dir', required=True)
    parser.add_argument('--cpu', action='append', default=[],
                        help='FILE=CPU, for captures whose CPU cannot be read')
    parser.add_argument('--write', action='store_true', help='write the archive')
    parser.add_argument('--target', default=str(ARCHIVE))
    options = parser.parse_args(argv)

    overrides = {}
    for entry in options.cpu:
        name, _, cpu = entry.partition('=')
        if not cpu:
            raise SystemExit('--cpu takes FILE=CPU')
        overrides[name] = cpu

    models = collect(options.capture_dir, overrides)
    for cpu, row in sorted(models.items()):
        print(f'{cpu:14s} {row["program"]:16s} {row["size"]:8d} bytes  {row["capture"]}')
    if not options.write:
        print(f'{len(models)} capture(s) accepted; re-run with --write to freeze them')
        return 0
    written = write_archive(models, Path(options.target))
    print(f'wrote {written} ({written.stat().st_size} bytes)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
