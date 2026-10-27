# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Select a source grammar independently of native adapter admission.

Profiles describe lexical storage, not instruction availability or native
acceptance.  The saved CPU identity remains authoritative for those checks.
Unknown records in a recognised grammar are retained by the source reader.
"""
from __future__ import annotations

from gxworks2.project_profiles import native_project_profile
from plc.storage.errors import StorageRuleError
from plc.storage.tables import storage_profile

from .models import GXWFormatError

#: Lexical codec profiles whose stored program is the FX statement list.  The
#: native CSV column format and the FX device widths belong to this set, so a
#: capability check asks this set rather than naming one or two of its members.
FX_CODEC_PROFILES = frozenset(("fx", "fx3u", "fx1s", "fx3g"))


def source_token_profile(cpu: str, *, language: str = 'ladder') -> str:
    """Return the lexical codec for an existing FX/Q/L source or SFC child.

    The selection belongs to the shared storage tables, so Core and this codec agree on
    which grammar a saved CPU uses. Native read/build evidence has a deliberately
    narrower admission policy; it must not decide whether an offline file can be
    inspected, and the family codecs preserve every record they do not recognise.
    """
    try:
        return storage_profile(cpu, language=language)
    except StorageRuleError as error:
        raise GXWFormatError(str(error)) from None


def source_token_capability(cpu: str, *, language: str = 'ladder') -> dict:
    """Expose codec availability separately from exact-CPU verification."""
    target = native_project_profile(cpu)
    try:
        profile = source_token_profile(cpu, language=language)
    except GXWFormatError as error:
        return {'profile': None, 'status': 'unsupported' if target and not target.program_supported
                else 'unimplemented', 'reason': str(error), 'native_validation': 'not_run'}
    return {'profile': profile, 'status': 'available', 'scope': 'recognised_source_records',
            'menu_cpu': target.cpu, 'native_validation': 'not_run', 'execution': 'unverified'}
