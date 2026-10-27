# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Bounded common source prefix; descriptive fields and flags remain raw.

Native project and library source objects start with two counted UTF-16 strings
and 42 further bytes. Nonempty descriptions move the language payload; locating
it by searching for a familiar byte sequence would lose that source boundary.
"""
from __future__ import annotations

import struct

from .models import GXWFormatError


# Native before/after controls, not family capabilities. Public dispatch still
# requires separate operation evidence for this exact menu/version/language.
_MEASURED_COMPILED_HEADER_SAVES = {
    # Exact native compiled Save witnesses; no family or language fallback.
    ('FX3G/FX3GC', 'en', 932): {
        'edited_source_save': (0, 1),
        'compiled_task_save': (1,),
        'edited_st_source_save': (0,),
    },
    ('FX3S', 'en', 932): {
        'edited_source_save': (0, 1),
        'compiled_task_save': (1,),
        'edited_st_source_save': (0,),
    },
    ('FX3U/FX3UC', 'en', 932): {
        'edited_source_save': (0, 1),
        'compiled_task_save': (1,),
        'edited_st_source_save': (0,),
    },
    ('L02/L02-P', 'en', 932): {
        'edited_source_save': (0, 1),
        'compiled_task_save': (1,),
        'edited_st_source_save': (0,),
    },
    ('L06/L06-P', 'en', 932): {
        'edited_source_save': (0, 1),
        'compiled_task_save': (1,),
        'edited_st_source_save': (0,),
    },
    ('L26/L26-P', 'en', 932): {
        'edited_source_save': (0, 1),
        'compiled_task_save': (1,),
        'edited_st_source_save': (0,),
    },
    ('Q00U', 'en', 932): {
        'edited_source_save': (0, 1),
        'compiled_task_save': (1,),
        'edited_st_source_save': (0,),
    },
    ('Q03UDV', 'en', 932): {
        'edited_source_save': (0, 1),
        'compiled_task_save': (1,),
        'edited_st_source_save': (0,),
    },
    ('Q06UDEH', 'en', 932): {
        'edited_source_save': (0, 1),
        'compiled_task_save': (1,),
        'edited_st_source_save': (0,),
    },
}


def source_payload_offset(raw: bytes) -> int:
    offset = 0
    for _ in range(2):
        if offset + 4 > len(raw):
            raise GXWFormatError("truncated source header string count")
        count = struct.unpack_from("<I", raw, offset)[0]
        end = offset + 4 + 2 * count
        if count < 1 or end > len(raw) or raw[end - 2:end] != b"\0\0":
            raise GXWFormatError("unbounded source header string")
        offset = end
    if offset + 42 >= len(raw):
        raise GXWFormatError("truncated common source header")
    return offset + 42


def _visible_header_save_policy(cpu, native_identity, operation):
    """Keep native metadata exceptions exact-target, version and operation bound."""
    if native_identity is None:
        # Preserve the original FX3U-only callers and frozen observations.
        if cpu == 'FX3U/FX3UC' and operation in (None, 'source_save', 'task_append'):
            return dict(compiled_clear=True, initial_timestamp=True, before_flags=(0, 1))
        raise GXWFormatError('visible save changed an unverified native header')
    from gxworks2.project_profiles import native_project_profile
    profile = native_project_profile(cpu)
    if (not isinstance(native_identity, dict) or profile is None
            or native_identity.get('cpu_menu') != profile.cpu
            or native_identity.get('software_version') != '1.635.0.1'):
        raise GXWFormatError('visible save changed an unverified native header')
    menu = profile.cpu
    language = native_identity.get('native_ui_language')
    if (operation in ('edited_st_source_save', 'compiled_st_source_save') and language in ('zh-CN', 'en', 'ja')
            and type(native_identity.get('project_codepage')) is int):
        # This common ST source envelope describes a pending compilation, not
        # a CPU capability. The caller must bind the successful current build,
        # compare the actual body/declarations and preserve every other byte.
        # Rebuilding an unchanged ST body can refresh the same save timestamp;
        # it needs the identical current-build proof, not an edited-body test.
        return dict(compiled_clear=True, initial_timestamp=False, before_flags=(0, 1),
                    after_flags=(0,), compilation_required=True)
    if menu == 'FX3U/FX3UC' and language == 'zh-CN' and operation in (
            'source_save', 'task_append', 'edited_source_save', 'edited_st_source_save', 'compiled_task_save'):
        return dict(compiled_clear=True, initial_timestamp=True, before_flags=(0, 1))
    codepage = native_identity.get('project_codepage')
    measured = (_MEASURED_COMPILED_HEADER_SAVES.get((menu, language, codepage), {})
                if type(codepage) is int else {})
    if operation in measured:
        return dict(compiled_clear=True, initial_timestamp=False, before_flags=measured[operation],
                    after_flags=(0,), compilation_required=True)
    if cpu == 'Q03UDV' and language == 'en' and operation == 'task_append':
        # One native English Q03UDV Save appended an ST Program to Task_01;
        # only its initialized timestamp changed and pending stayed 1. This
        # witness grants neither a Q source-header change nor compile clearing.
        return dict(compiled_clear=False, initial_timestamp=False, before_flags=(1,))
    if (cpu == 'Q03UDV' and language == 'en' and operation == 'edited_source_save'
            and native_identity.get('project_codepage') == 936):
        # Explicit Save of the changed FBD source updated its timestamp with
        # pending 1 -> 1. Untouched sources and compile clearing remain closed.
        return dict(compiled_clear=False, initial_timestamp=False, before_flags=(1,))
    raise GXWFormatError('visible save changed an unverified native header')


def normalize_visible_save_header(before: bytes, after: bytes, *, cpu: str, native_compiled=False,
                                  native_identity=None, operation=None):
    """Bound only the common save metadata witnessed for the current operation.

    The unchanged header passes through. Changed headers must have the exact
    observed 54-byte layout: a fixed 34-byte prefix, valid SYSTEMTIME fields and
    compile-pending 0/1 -> 1. A caller with independently bound current compile
    evidence may also permit 0/1 -> 0. No payload, description or other header
    bytes are normalized; this function does not establish compilation itself.
    An all-zero initial timestamp may become a valid date on the measured FX3U
    first save. The English Q03UDV task-append witness requires an explicit
    current native identity, an initialized timestamp and pending 1 -> 1. It
    does not grant compile clearing. The CP936 edited-source Save permits
    initialized timestamps with pending 1 -> 1 only for a changed retained FBD
    payload. Separately observed CP932 changed-FBD and task saves require a
    current compile receipt before clearing pending 1 -> 0. A changed final
    timestamp must always be valid. A currently compiled ST source may refresh
    that timestamp without changing its body; the same exact envelope and
    bound successful compilation are required.
    """
    from datetime import datetime
    if type(native_compiled) is not bool:
        raise GXWFormatError('visible save compilation permission must be boolean')
    a, b = bytes(before), bytes(after)
    if a == b:
        return b, None
    policy = _visible_header_save_policy(cpu, native_identity, operation)
    if policy.get('compilation_required') and not native_compiled:
        raise GXWFormatError('visible save changed an unverified native header')
    prefix = bytes.fromhex('01000000000001000000000001000a00000000000200000001000000010000000000')
    if (len(a) != 54 or len(b) != 54
            or a[:34] != prefix or b[:34] != prefix
            or int.from_bytes(a[50:54], 'little') not in policy['before_flags']
            or int.from_bytes(b[50:54], 'little') not in policy.get('after_flags',
               ((0, 1) if native_compiled and policy['compiled_clear'] else (1,)))):
        raise GXWFormatError('visible save changed an unverified native header')

    def stamp(raw, *, initial=False):
        values = list(struct.unpack('<8H', raw[34:50]))
        if initial and raw[34:50] == bytes(16):
            return values
        year, month, weekday, day, hour, minute, second, millis = values
        try:
            value = datetime(year, month, day, hour, minute, second, millis * 1000)
        except ValueError as error:
            raise GXWFormatError('visible save has an invalid native save timestamp') from error
        if weekday != (value.weekday() + 1) % 7:
            raise GXWFormatError('visible save has an invalid native save weekday')
        return values

    pending_after = int.from_bytes(b[50:54], 'little')
    observed = dict(status='native_header_metadata_inference',
                    timestamp_before=stamp(a, initial=policy['initial_timestamp']), timestamp_after=stamp(b),
                    compile_pending_before=int.from_bytes(a[50:54], 'little'), compile_pending_after=pending_after)
    if a[34:50] == bytes(16):
        observed['timestamp_before_status'] = 'unset_native_default'
    if native_compiled and pending_after == 0:
        observed['native_compiled_permission_used'] = True
    if native_identity is not None:
        from gxworks2.project_profiles import native_project_profile
        observed['native_binding'] = dict(cpu_menu=native_project_profile(cpu).cpu, software_version=native_identity['software_version'],
            native_ui_language=native_identity['native_ui_language'], operation=operation)
    return a, observed
