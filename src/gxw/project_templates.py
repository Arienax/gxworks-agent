# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""CPU-bound, empty native project templates (no generated CPU metadata)."""
from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import json
import lzma
from zipfile import ZipFile

from gxworks2.project_profiles import native_project_profile

from .models import GXWFormatError
from .template_data import template_bytes


# Project support decision. Keep native menu facts and frozen template bytes.
_UNSUPPORTED_FBD_GENERATION = {
    'FX1': '当前 FBD 复现流程不支持 CPU FX1：全局 BOOL 验收被原生编译器以 0x050F1034 拒绝。',
}


def unsupported_fbd_models():
    return [dict(cpu=cpu, status='unsupported', reason=reason)
            for cpu, reason in _UNSUPPORTED_FBD_GENERATION.items()]


def fbd_generation_models():
    return tuple(cpu for cpu in blank_fbd_models() if cpu not in _UNSUPPORTED_FBD_GENERATION)


def require_fbd_generation_support(plc_model):
    profile = native_project_profile(plc_model)
    if profile is not None and profile.cpu in _UNSUPPORTED_FBD_GENERATION:
        raise GXWFormatError(_UNSUPPORTED_FBD_GENERATION[profile.cpu])


@lru_cache(maxsize=1)
def _templates():
    from .project_metadata import require_gui_project_entries
    with ZipFile(BytesIO(lzma.decompress(template_bytes('blank-fbd-projects.zip.xz')))) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        templates = {row['cpu']: (row, archive.read(row['file'])) for row in manifest['models']}
    for cpu, (_, raw) in templates.items():
        if cpu not in _UNSUPPORTED_FBD_GENERATION:
            require_gui_project_entries(raw)
    return templates


def blank_fbd_models():
    """Return actual merged menu identities, not execution-validated CPU claims."""
    return tuple(_templates())


def blank_fbd_baseline(plc_model):
    profile = native_project_profile(plc_model)
    if profile is None:
        raise GXWFormatError(f'No native FBD template for CPU {plc_model!r}')
    if not profile.program_supported:
        raise GXWFormatError(f'CPU {profile.cpu!r} is parameter-only; it has no FBD Program')
    selected = _templates().get(profile.cpu)
    if selected is None:
        raise GXWFormatError(f'Native FBD template is unavailable for CPU {profile.cpu!r}')
    return selected[1]

# A generated Ladder program needs a Ladder project to live in. The FBD archive holds
# FBD programs only, and reading one as Ladder is refused, so a separate captured set is
# required rather than a CPU string rewritten inside an FBD file.
LADDER_ARCHIVE = 'blank-ladder-projects.zip.xz'
LADDER_GENERATION_UNAVAILABLE = (
    '当前没有已抓取的 Ladder 空白工程，只能交付 CSV 与程序候选，不能声称已生成 .gxw。')


@lru_cache(maxsize=1)
def _ladder_archive():
    """Captured blank Ladder projects, or None when the archive is not shipped."""
    try:
        data = template_bytes(LADDER_ARCHIVE)
    except (OSError, ValueError):
        return None
    with ZipFile(BytesIO(lzma.decompress(data))) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        return {row['cpu']: (row, archive.read(row['file'])) for row in manifest['models']}


def blank_ladder_models():
    """CPUs claimed by the captured archive; verify each one before delivery."""
    return tuple(_ladder_archive() or ())


def ladder_generation_support(plc_model):
    """Report whether a generated Ladder program can be delivered as a project."""
    profile = native_project_profile(plc_model)
    if profile is None:
        return {'cpu': None, 'status': 'unsupported',
                'reason': f'No native project profile for CPU {plc_model!r}'}
    if not profile.program_supported:
        return {'cpu': profile.cpu, 'status': 'unsupported',
                'reason': f'CPU {profile.cpu!r} is parameter-only; it has no Ladder Program'}
    archive = _ladder_archive()
    if archive is None:
        return {'cpu': profile.cpu, 'status': 'unsupported',
                'reason': LADDER_GENERATION_UNAVAILABLE}
    if profile.cpu not in archive:
        return {'cpu': profile.cpu, 'status': 'unsupported',
                'reason': f'No captured blank Ladder project for CPU {profile.cpu!r}'}
    return {'cpu': profile.cpu, 'status': 'available', 'reason': None}


def require_ladder_generation_support(plc_model):
    support = ladder_generation_support(plc_model)
    if support['status'] != 'available':
        raise GXWFormatError(support['reason'])
    return support['cpu']


def blank_ladder_baseline(plc_model):
    """Return the captured blank Ladder project for a CPU, verified before use."""
    cpu = require_ladder_generation_support(plc_model)
    row, data = _ladder_archive()[cpu]
    verify_ladder_baseline(data, plc_model)
    return data


def captured_project_cpu(raw):
    """Read the CPU menu identity recorded inside a project, without trusting a name."""
    from .project_metadata import project_stream_mapping, read_project_text_context
    from .container_writer import validate_cfb_streams
    from .integrity import read_project_hdb
    outer = validate_cfb_streams(raw)
    mapping = project_stream_mapping(outer)
    payloads = read_project_hdb(outer).payloads
    contexts = [read_project_text_context(payloads[key]) for name, key in mapping.items()
                if name.endswith('.prj')]
    if len(contexts) != 1:
        raise GXWFormatError('captured project requires one CPU configuration')
    return contexts[0]['cpu']


def verify_ladder_baseline(raw, plc_model):
    """Prove a captured project is one blank Ladder Program for the claimed CPU.

    Used by the capture script before freezing and by the loader before delivering, so a
    mislabelled project fails instead of becoming a Ladder project that is not Ladder.
    """
    from .offline_project import read_offline_project
    from .project_metadata import project_stream_mapping, read_project_text_context
    from .container_writer import validate_cfb_streams
    from .integrity import read_project_hdb
    from gxworks2.project_profiles import native_project_profile
    requested = native_project_profile(plc_model)
    if requested is None or not requested.program_supported:
        raise GXWFormatError(f'CPU {plc_model!r} has no Ladder Program to place a program in')
    try:
        project = read_offline_project(raw)
    except GXWFormatError:
        raise
    except Exception as error:
        raise GXWFormatError(f'captured project does not open: {error}') from error
    sources = project.sources()
    ladders = [source for source in sources if source.get('language') == 'ladder']
    others = [source for source in sources if source.get('language') != 'ladder']
    if len(ladders) != 1 or others:
        raise GXWFormatError(
            'captured project must contain exactly one Ladder Program and nothing else; '
            f'found {[source.get("language") for source in sources]}')
    if not ladders[0].get('readable') or not ladders[0].get('offline_editable'):
        raise GXWFormatError('captured Ladder Program is not readable and editable offline')
    from .offline_project import read_ordered_program
    listing = read_ordered_program(project, ladders[0]['name'])['listing']
    carried = [instruction.mnemonic for instruction in listing.instructions
               if instruction.mnemonic.upper() != 'END']
    if carried:
        raise GXWFormatError(
            'captured Ladder Program is not empty: '
            + ', '.join(sorted(set(carried))[:5])
            + ' would be carried into every generated program')
    actual = native_project_profile(captured_project_cpu(raw))
    if actual is None or actual.cpu != requested.cpu:
        raise GXWFormatError(
            f'captured project CPU {captured_project_cpu(raw)!r} does not match claimed CPU '
            f'{plc_model!r}')
    return ladders[0]['source_name']
