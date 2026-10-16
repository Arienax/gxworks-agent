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
    with ZipFile(BytesIO(lzma.decompress(template_bytes('blank-fbd-projects.zip.xz')))) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        return {row['cpu']: (row, archive.read(row['file'])) for row in manifest['models']}


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
