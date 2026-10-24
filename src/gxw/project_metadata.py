"""Lossless updates of observed GXW metadata (no XML reserialization)."""
from __future__ import annotations

import base64
from collections import defaultdict
from dataclasses import dataclass, field
from copy import deepcopy
from typing import Mapping
from functools import lru_cache
import hashlib
import struct
import re
from xml.parsers import expat

from .models import GXWFormatError
# Compatibility imports for recorded observations; implementations belong to the adapter.
from gxworks2.ui_observations import (native_allocation_controls, read_native_allocation_grid,
    read_native_allocation_grids, native_allocation_grid_state, native_allocation_observation_state)


def read_task_source(raw: bytes) -> dict:
    """Read observed native task configuration/rows, retaining unknown bytes.

    The common source header supplies the boundary, including variable-length
    descriptions. Event, interval and priority correspond to original task
    getters. The following word and each row's extra text remain uninterpreted.
    This is a source projection, not an execution or scheduling verdict.
    """
    from .source_header import source_payload_offset
    raw = bytes(raw)
    start = offset = source_payload_offset(raw)

    def word(*, signed=False):
        nonlocal offset
        if offset + 4 > len(raw):
            raise GXWFormatError('truncated native task word')
        value = struct.unpack_from('<i' if signed else '<I', raw, offset)[0]
        offset += 4
        return value

    def text():
        nonlocal offset
        count = word()
        end = offset + count * 2
        if count < 1 or end > len(raw) or raw[end - 2:end] != b'\0\0':
            raise GXWFormatError('unbounded native task string')
        try:
            value = raw[offset:end - 2].decode('utf-16le')
        except UnicodeError as error:
            raise GXWFormatError('invalid native task string') from error
        offset = end
        return value

    event_name, interval, priority = text(), text(), word(signed=True)
    opaque_word = raw[offset:offset + 4]
    word()
    count_offset = offset
    count = word()
    # Every row contains two counted UTF-16 strings and a line word.
    if count > (len(raw) - offset) // 16:
        raise GXWFormatError('unbounded native task row count')
    rows = []
    for _ in range(count):
        row_start = offset
        name, line, extra_text = text(), word(signed=True), text()
        rows.append(dict(name=name, line=line, extra_text=extra_text, raw=raw[row_start:offset]))
    return dict(event_name=event_name, interval=interval, priority=priority,
        header=raw[:start], opaque_word=opaque_word, count_offset=count_offset,
        rows=rows, suffix=raw[offset:])


def verify_task_source_append(before: bytes, after: bytes, *, program_name: str, line: int) -> dict:
    """Require the original task bytes plus exactly one observed Program row."""
    previous, saved = read_task_source(before), read_task_source(after)
    if (not isinstance(program_name, str) or not program_name
            or type(line) is not int or not 1 <= line < 2**31
            or line != 1 + max([0] + [row['line'] for row in previous['rows']])
            or len(saved['rows']) != len(previous['rows']) + 1):
        raise GXWFormatError('native task append has a conflicting Program count or line')
    for key in ('header', 'event_name', 'interval', 'priority', 'opaque_word', 'suffix'):
        if previous[key] != saved[key]:
            raise GXWFormatError('native task append changed unedited configuration or unknown bytes: ' + key)
    if [row['raw'] for row in previous['rows']] != [row['raw'] for row in saved['rows'][:-1]]:
        raise GXWFormatError('native task append changed existing Program records')
    created = saved['rows'][-1]
    if (created['name'], created['line'], created['extra_text']) != (program_name, line, ''):
        raise GXWFormatError('native task append differs from the selected new Program')
    return dict(configuration='exact', unknown_header_and_suffix='exact', old_rows='exact',
        created_program=program_name, created_line=line)


def read_resource_source(raw: bytes) -> dict:
    """Bound stored code and the observed counted-name cache; retain the rest.

    Cached names do not establish resource/task ownership or execution order.
    Scope writes use current directory roles and original native parents.
    """
    from .token_resource import resource_code_spans
    raw = bytes(raw)
    spans, start = resource_code_spans(raw)
    offset = start
    if offset + 4 > len(raw):
        raise GXWFormatError('truncated resource cached-name count')
    count = struct.unpack_from('<I', raw, offset)[0]
    offset += 4
    if count > (len(raw) - offset) // 6:
        raise GXWFormatError('unbounded resource cached-name count')
    names = []
    for _ in range(count):
        if offset + 4 > len(raw):
            raise GXWFormatError('truncated resource cached name')
        length = struct.unpack_from('<I', raw, offset)[0]
        end = offset + 4 + length * 2
        if length < 1 or end > len(raw) or raw[end - 2:end] != b'\0\0':
            raise GXWFormatError('unbounded resource cached name')
        try:
            names.append(raw[offset + 4:end - 2].decode('utf-16le'))
        except UnicodeError as error:
            raise GXWFormatError('invalid resource cached name') from error
        offset = end
    return dict(code_spans=spans, prefix=raw[:start], cached_names=names,
                cache_offset=start, cache_end=offset, suffix=raw[offset:])


def mark_observed_source_compile_pending(raw: bytes, *, logical_name: str) -> bytes:
    """Mark an observed source/declaration payload pending without dropping caches.

    Native SetDataCompileStatus(1) changes this header word from 0 to 1.
    Reopening a source-only or labels-only change makes the owning POU and
    project report pending. Zero alone never proves source/cache freshness.
    Pending is a status signal, not a compiler-cache invalidation guarantee.
    Other marker values and header variants are deliberately not rewritten.
    Callers must bind this payload to the intended project stream and update
    its history/integrity metadata through their existing container writer.
    """
    raw = bytes(raw)
    from .source_header import source_payload_offset
    try:
        payload = source_payload_offset(raw)
    except GXWFormatError as error:
        raise GXWFormatError('outside observed source compile-status header') from error
    fixed_prefixes = (
        bytes.fromhex('01000a00000000000200000001000000010000000000'),
        bytes.fromhex('00000000000000000200000001000000010000000000'),
    )
    if (not logical_name.endswith((".pou", ".lh", ".gh", ".lnb", ".lnl"))
            or raw[payload - 42:payload - 20] not in fixed_prefixes):
        raise GXWFormatError("outside observed source compile-status header")
    # Independent native setters with nonempty descriptions establish this
    # same final word of the bounded common header; descriptions stay raw.
    # An independent Q13UDV ordinary/library source + labels setter/save/cold
    # control establishes the all-zero version prefix with this same word.
    offset = payload - 4
    marker = struct.unpack_from("<I", raw, offset)[0]
    if marker not in (0, 1):
        raise GXWFormatError("unsupported source compile-status marker; preserve it unchanged")
    if marker == 1:
        return raw
    return raw[:offset] + struct.pack("<I", 1) + raw[offset + 4:]


def read_project_text_context(raw: bytes) -> dict:
    """Read the observed .prj CPU/code-page prefix without interpreting its tail.

    The counted project name, CPU and description precede the fixed prefix.
    Native project attribute/code-page getters agree for the inspected Q/FX
    controls, including an independently authored project with a description.
    This is not a CPU capability or compiler-version description.
    Unsupported code pages remain None instead of using the host's encoding.
    """
    offset, values = 0, []
    for _ in range(3):
        if offset + 4 > len(raw):
            raise GXWFormatError("truncated project metadata string length")
        count = struct.unpack_from("<I", raw, offset)[0]
        end = offset + 4 + 2 * count
        if not 1 <= count <= 8192 or end > len(raw):
            raise GXWFormatError("unbounded project metadata string")
        try:
            text = raw[offset + 4:end].decode("utf-16le")
        except UnicodeError as exc:
            raise GXWFormatError("undecodable project metadata string") from exc
        if not text.endswith("\0") or "\0" in text[:-1]:
            raise GXWFormatError("unsupported project metadata string terminator")
        values.append(text[:-1])
        offset = end
    if len(raw) < offset + 46 or raw[offset:offset + 8] != bytes(8):
        raise GXWFormatError("unsupported project metadata prefix")
    codepage = struct.unpack_from("<I", raw, offset + 42)[0]
    # The inspected native converter substitutes 54936 for stored CP936.
    encoding = {932: "cp932", 936: "gb18030", 949: "cp949", 1252: "cp1252"}.get(codepage)
    return dict(cpu=values[1], codepage=codepage, text_encoding=encoding,
                codepage_offset=offset + 42, metadata_sha256=hashlib.sha256(raw).hexdigest(),
                handling="partially-decoded")


def read_project_system_variables(raw: bytes) -> dict:
    """Read the saved 88-byte native range record; do not infer CPU defaults.

    The original Workspace type library gives eleven pairs of DWORD bounds.
    0xffffffff is retained verbatim, including partially configured pairs.
    """
    context = read_project_text_context(raw)
    offset = context['codepage_offset'] + 4
    if offset + 88 > len(raw):
        raise GXWFormatError('truncated project system variable ranges')
    devices = ('D', 'R', 'W', 'T', 'ST', 'C', 'M', 'B', 'P', 'S', 'T10')
    return {'offset': offset, 'ranges': {name: struct.unpack_from('<II', raw, offset + 8 * index)
            for index, name in enumerate(devices)}}


_GUI_RANGE_NAMES = {name: ('10T' if name == 'T10' else name)
                    for name in ('D', 'R', 'W', 'T', 'ST', 'C', 'M', 'B', 'P', 'S', 'T10')}


def _project_allocation_records(raw: bytes) -> tuple[str, bytes, bytes | None]:
    """Read one current saved allocation source, without constructing a POU."""
    from .container_writer import validate_cfb_streams
    from .integrity import read_project_hdb
    outer = validate_cfb_streams(raw)
    nested = read_project_hdb(outer).payloads
    mapping = project_stream_mapping(outer)
    streams = {name: nested[key] for name, key in mapping.items() if key in nested}
    names = [name for name in streams if name.endswith('.prj')]
    if len(names) != 1:
        raise GXWFormatError('project allocation requires one current project metadata source')
    name, = names
    return name, streams[name], streams.get('Gppw2.gpj')


@lru_cache(maxsize=64)
def _native_allocation_template(cpu_menu: str) -> dict:
    from .project_templates import blank_fbd_baseline
    _, metadata, _ = _project_allocation_records(blank_fbd_baseline(cpu_menu))
    return {'stored_cpu': read_project_text_context(metadata)['cpu'],
            'ranges': read_project_system_variables(metadata)['ranges']}


def project_allocation_profile(cpu: str) -> dict:
    """Describe exact-menu storage evidence separately from native UI evidence.

    Template ranges are observed legal intervals, not device capacity limits.
    An unverified UI profile does not become supported merely because another
    CPU uses the same resource or the same saved range record.
    """
    from gxworks2.project_profiles import native_project_profile
    profile = native_project_profile(cpu)
    if profile is None:
        return {'allocation_profile': None, 'cpu_menu': None, 'program_supported': None,
                'status': 'unknown', 'reason': 'unknown_native_cpu_menu',
                'observed_ranges': {}, 'saved_fields': [], 'native_controls': {}}
    result = {'allocation_profile': 'gxworks2-1.635-system-variables/' + profile.cpu,
              'cpu_menu': profile.cpu, 'software_version': '1.635.0.1',
              'program_supported': profile.program_supported,
              'status': 'saved_template_observed' if profile.program_supported else 'parameter_only',
              'saved_fields': list(_GUI_RANGE_NAMES) if profile.program_supported else [],
              'observed_ranges': {}, 'native_controls': {'status': 'unverified'},
              'saved_gui_mirror': 'unverified', 'compile_option_ui': 'unverified'}
    if not profile.program_supported:
        return result
    observed = _native_allocation_template(profile.cpu)
    result.update(stored_cpu=observed['stored_cpu'],
                  observed_ranges={name: list(pair) for name, pair in observed['ranges'].items()},
                  source='native_cpu_bound_blank_project_template')
    if profile.cpu == 'FX3U/FX3UC':
        result.update(saved_gui_mirror='verified', compile_option_ui='verified', native_ui_evidence_languages=['zh-CN'])
        result['native_controls'] = {**native_allocation_controls('device_range_fx'), 'status': 'verified',
            'coverage': 'verified_D_M_controls_only'}
    elif profile.cpu in ('L02S/L02S-P', 'L02/L02-P', 'L06/L06-P', 'L26/L26-P', 'L26-BT/L26-PBT'):
        # Separate native GUI save/cold-open witnesses retain all -1 values in
        # this FX-shaped host section while .prj contains effective L ranges.
        result['saved_gui_mirror'] = 'inactive_host_values_observed'
    return result


def validate_project_allocation_ranges(edits, *, cpu: str, known_devices=None) -> dict:
    """Validate exact-target ranges without borrowing another CPU's limits.

    Reviewed device profiles may establish a wider address interval. Otherwise
    an edit must stay inside that exact menu template's configured interval.
    This is intentionally narrower than claiming a physical device maximum.
    The live UI planner separately rejects unobserved disable operations.
    """
    profile = project_allocation_profile(cpu)
    if profile['status'] == 'unknown':
        raise GXWFormatError('allocation configuration has no known exact native CPU menu')
    if not profile['program_supported']:
        raise GXWFormatError('parameter-only native targets have no program allocation ranges')
    allowed = set(_GUI_RANGE_NAMES) if known_devices is None else set(known_devices)
    if (not isinstance(edits, dict) or not set(edits) <= allowed
            or not set(edits) <= _GUI_RANGE_NAMES.keys()):
        raise GXWFormatError('range changes require known device classes')
    # Reuse the existing reviewed address catalogue; do not create a second
    # per-family device limit table for engineering allocation.
    from plc.validation import SOURCE_DEVICE_PROFILES
    members = profile['cpu_menu'].split('/')
    sources = [SOURCE_DEVICE_PROFILES.get(member) for member in members]
    normalized = {}
    for device, pair in edits.items():
        if pair is None:
            pair = [0xffffffff, 0xffffffff]
        if (not isinstance(pair, list) or len(pair) != 2 or any(type(v) is not int for v in pair)
                or not (pair == [0xffffffff, 0xffffffff] or 0 <= pair[0] <= pair[1] < 2**31)):
            raise GXWFormatError('system variable range requires ordered integer bounds or null')
        if pair != [0xffffffff, 0xffffffff]:
            intervals = []
            observed = profile['observed_ranges'].get(device)
            if observed and 0 <= observed[0] <= observed[1] < 2**31:
                intervals.append(tuple(observed))
            if sources and all(source is not None and device in source['device_limits'] for source in sources):
                upper = min(source['device_limits'][device] for source in sources)
                # Allocation must not occupy the source-defined system region.
                special = [region for source in sources
                           for region in source.get('special_regions', {}).get(device, [])]
                upper = min([upper] + [low - 1 for low, _ in special])
                if upper >= 0:
                    intervals.append((0, upper))
            if profile['cpu_menu'] == 'FX3U/FX3UC' and device == 'D':
                intervals.append((0, 7999))  # Existing exact-menu native D profile.
            if not any(low <= pair[0] <= pair[1] <= high for low, high in intervals):
                raise GXWFormatError('allocation range is outside the observed exact CPU interval: '
                                     + profile['cpu_menu'] + '/' + device)
        normalized[device] = tuple(pair)
    return normalized


def read_gui_system_variables(raw: bytes | None, *, cpu: str | None = None) -> dict:
    """Read the GUI's separate Option/SystemVariable record, without defaults.

    GX Works2 1.635 loads these values from Gppw2.gpj and can overwrite the
    workspace's .prj allocation configuration before compiling. Absence is
    unknown, not agreement with the workspace record.
    """
    if raw is None:
        return {'ranges': {}, 'stored_ranges': {}, 'retained_fields': {},
                'word_device': None, 'bit_device': None, 'unverified_selectors': []}
    try:
        text = raw.decode('utf-16le')
    except UnicodeError as error:
        raise GXWFormatError('invalid GUI application configuration encoding') from error
    values, retained = {}, {}
    section = subsection = None
    for line in text.removeprefix('\ufeff').rstrip('\0').splitlines():
        if line.startswith('[') and line.endswith(']'):
            section, subsection = line, None
        elif line.startswith('\t') and not line.startswith('\t\t') and line.endswith(':'):
            subsection = line[1:-1]
        elif section == '[Option]' and subsection == 'SystemVariable':
            match = re.fullmatch(r'\t\t(\w+)\tTYPE_(DWORD|WORD)\t(\d+)', line)
            if match:
                key, kind, number = match.groups()
                if key in values:
                    raise GXWFormatError('duplicate GUI system variable field: ' + key)
                number = int(number)
                if number > (0xffffffff if kind == 'DWORD' else 0xffff):
                    raise GXWFormatError('GUI system variable field exceeds its storage type')
                values[key] = number
                retained[key] = {'storage_type': kind, 'value': number}
    stored = {name: (values[key+'Min'], values[key+'Max'])
              for name,key in {**_GUI_RANGE_NAMES, '100T': '100T'}.items()
              if key+'Min' in values and key+'Max' in values}
    ranges = {name: pair for name, pair in stored.items() if name in _GUI_RANGE_NAMES}
    unresolved = []
    if cpu == 'FX3U/FX3UC':
        # Independent GUI 1.635 FX3U observations: the 100 ms timer controls
        # use 100T, while T is a retained field for another timer profile.
        # The selected D/M classes enable their ranges; R/W/B can retain
        # inactive host values without becoming compiler allocation ranges.
        ranges.pop('T', None)
        if '100T' in stored:
            ranges['T'] = stored['100T']
        if values.get('WordDevice') == 0:
            ranges.update(R=(0xffffffff, 0xffffffff), W=(0xffffffff, 0xffffffff))
        else:
            unresolved.append('WordDevice')
            for name in ('D', 'R', 'W'):
                ranges.pop(name, None)
        if values.get('BitDevice') == 3:
            ranges['B'] = (0xffffffff, 0xffffffff)
        else:
            unresolved.append('BitDevice')
            for name in ('M', 'B'):
                ranges.pop(name, None)
    elif cpu is not None:
        # The common field names do not prove this CPU reads the host section.
        # The independently saved L witnesses retain all -1 host values while
        # their actual .prj allocation ranges are enabled. Keep those bytes as
        # raw evidence; never compare them as an effective compiler profile.
        ranges = {}
        unresolved.append('CPUProfile')
    return {'ranges': ranges, 'stored_ranges': stored, 'retained_fields': retained,
            'word_device': values.get('WordDevice'), 'bit_device': values.get('BitDevice'),
            'unverified_selectors': unresolved}


def write_gui_system_variables(raw: bytes | None, ranges: dict, *, cpu: str | None = None) -> bytes:
    """Update known fields, with an explicit native profile for effective ranges.

    Omitting CPU retains the raw field API. FX3U's effective timer uses 100T;
    inactive device backups and the generic T profile remain untouched.
    """
    existing = read_gui_system_variables(raw)  # Validate existing fields first.
    if cpu not in (None, 'FX3U/FX3UC'):
        raise GXWFormatError('GUI allocation writer is not observed for this CPU')
    profiled = cpu == 'FX3U/FX3UC'
    if profiled and (existing['word_device'] not in (None, 0) or existing['bit_device'] not in (None, 3)):
        raise GXWFormatError('GUI allocation writer requires the observed FX3U D/M selectors')
    if set(ranges) - _GUI_RANGE_NAMES.keys():
        raise GXWFormatError('unknown GUI system variable device')
    text = (raw.decode('utf-16le') if raw is not None else
            '[App]\n\tFileVersion:\n\t\tProjectDataVersion\tTYPE_DWORD\t9\n'
            '\t\tProjectDataMinorVersion\tTYPE_DWORD\t0\n')
    terminator = '\0' if text.endswith('\0') else ''
    text = text[:-1] if terminator else text
    newline = '\r\n' if '\r\n' in text else '\n'
    lines = text.splitlines(keepends=True)
    option = [i for i,line in enumerate(lines) if line.rstrip('\r\n').removeprefix('\ufeff') == '[Option]']
    if len(option) > 1:
        raise GXWFormatError('duplicate GUI Option section')
    if not option:
        if lines and not lines[-1].endswith(('\n','\r')):
            lines[-1] += newline
        lines.append('[Option]' + newline)
        option = [len(lines)-1]
    start = option[0]+1
    end = next((i for i in range(start,len(lines)) if lines[i].startswith('[')),len(lines))
    subsection = [i for i in range(start,end) if lines[i].rstrip('\r\n') == '\tSystemVariable:']
    if len(subsection) > 1:
        raise GXWFormatError('duplicate GUI SystemVariable section')
    if not subsection:
        if end and not lines[end-1].endswith(('\n','\r')):
            lines[end-1] += newline
        lines.insert(end, '\tSystemVariable:' + newline)
        start = end+1
        end = start
    else:
        start = subsection[0]+1
        end = next((i for i in range(start,end) if not lines[i].startswith('\t\t')),end)
    updates = {}
    for name,pair in ranges.items():
        if (not isinstance(pair,(list,tuple)) or len(pair)!=2
                or any(type(v) is not int or not 0 <= v <= 0xffffffff for v in pair)):
            raise GXWFormatError('invalid GUI allocation range')
        if profiled and name in {'R', 'W', 'B'}:
            if tuple(pair) != (0xffffffff, 0xffffffff):
                raise GXWFormatError('inactive FX3U selector ranges cannot be enabled through the D/M profile')
            continue  # Preserve the stored backup, including its absence.
        if (profiled and name == 'ST' and tuple(pair) == (0xffffffff, 0xffffffff)
                and existing['ranges'].get('ST') == (0xfffffffe, 0xfffffffe)):
            continue  # Native FX disabled sentinel, equivalent to workspace -1.
        field = '100T' if profiled and name == 'T' else _GUI_RANGE_NAMES[name]
        for bound,value in zip(('Min','Max'),pair):
            updates[field+bound] = ('DWORD',value)
    # These selectors were observed in the FX3U D/M controls. Existing choices
    # are retained; other CPUs are not assigned a guessed selector here.
    if existing['word_device'] is None: updates['WordDevice'] = ('WORD',0)
    if existing['bit_device'] is None: updates['BitDevice'] = ('WORD',3)
    for i in range(start,end):
        match = re.fullmatch(r'\t\t(\w+)\tTYPE_(DWORD|WORD)\t\d+(\r?\n)?',lines[i])
        if match and match[1] in updates:
            kind,value = updates.pop(match[1])
            lines[i] = f'\t\t{match[1]}\tTYPE_{kind}\t{value}' + (match[3] or '')
    if updates and end and not lines[end-1].endswith(('\n','\r')):
        lines[end-1] += newline
    lines[end:end] = [f'\t\t{key}\tTYPE_{kind}\t{value}{newline}' for key,(kind,value) in updates.items()]
    return (''.join(lines)+terminator).encode('utf-16le')


def gui_application_save_plan(raw: bytes, source: dict) -> bytes | None:
    """Preserve GUI settings, mirroring only source-bound allocation changes.

    An ordinary source save must not reinterpret another CPU's host settings or
    reject an unchanged device selector. Only FX3U's D/M mirror is observed.
    Legacy factory projects need that mirror initialized from their workspace;
    inactive R/W/B backups are not requests to enable those selectors.
    """
    _, metadata, application = _project_allocation_records(raw)
    cpu = read_project_text_context(metadata)['cpu']
    if cpu != 'FX3U/FX3UC':
        # Native export retains an existing application file, or registers its
        # format-only bootstrap. Q/L/other FX host fields are not FX3U controls.
        return None
    ranges = {}
    if application is None:
        workspace = read_project_system_variables(metadata)['ranges']
        ranges = {name: pair for name, pair in workspace.items() if name not in {'R', 'W', 'B'}}
    if source.get('operation') == 'configure_project':
        for edit in source['ranges']:
            after = tuple(value & 0xffffffff for value in edit['after'])
            before = tuple(value & 0xffffffff for value in edit.get('before', ()))
            if after != before:
                ranges[edit['device']] = after
    if not ranges:
        return None  # Preserve every byte, including unobserved selector modes.
    return write_gui_system_variables(application, ranges, cpu=cpu)


def require_gui_project_entries(raw: bytes) -> None:
    """Reject factory containers that have no registered GUI project envelope."""
    from .container_writer import validate_cfb_streams
    from .integrity import read_project_hdb
    outer = validate_cfb_streams(raw)
    mapping = project_stream_mapping(outer)
    if 'Project.gd2' not in mapping or 'Project.gd2' not in outer:
        raise GXWFormatError('GX Works2 GUI project entry Project.gd2 is missing')
    application = mapping.get('Gppw2.gpj')
    if application is None or not read_project_hdb(outer).payloads.get(application):
        raise GXWFormatError('GX Works2 GUI application entry Gppw2.gpj is missing')


def project_gui_configuration(raw: bytes) -> dict:
    """Report GUI/workspace allocation agreement independently of compilation."""
    source_name, metadata, application = _project_allocation_records(raw)
    cpu = read_project_text_context(metadata)['cpu']
    profile = project_allocation_profile(cpu)
    workspace = read_project_system_variables(metadata)['ranges']
    common = {'allocation_profile': profile['allocation_profile'], 'cpu_menu': profile['cpu_menu'],
              'workspace_source': source_name, 'workspace_ranges': {name: list(pair) for name, pair in workspace.items()},
              'gui_mirror_status': profile.get('saved_gui_mirror', 'unverified')}
    gui = read_gui_system_variables(application, cpu=cpu)
    if profile.get('saved_gui_mirror') != 'verified':
        return {**common, 'status': 'not_verified', 'reason': 'GUI allocation profile not observed for this CPU',
                'source': 'Gppw2.gpj/Option/SystemVariable', 'missing': [], 'differences': {},
                'word_device': gui['word_device'], 'bit_device': gui['bit_device'],
                'effective_ranges': {}, 'stored_ranges': {name: list(pair) for name, pair in gui['stored_ranges'].items()},
                'retained_fields': gui['retained_fields'], 'unverified_selectors': gui['unverified_selectors']}
    missing = sorted(set(workspace)-gui['ranges'].keys())
    differences = {name:{'workspace':list(pair),'gui':list(gui['ranges'][name])}
                   for name,pair in workspace.items() if name in gui['ranges'] and pair!=gui['ranges'][name]
                   and not (pair==(0xffffffff,0xffffffff) and gui['ranges'][name]==(0xfffffffe,0xfffffffe))}
    return {**common, 'status':'missing' if missing else 'not_verified' if gui['unverified_selectors'] else 'differs' if differences else 'matched',
            'source':'Gppw2.gpj/Option/SystemVariable','missing':missing,'differences':differences,
            'word_device':gui['word_device'],'bit_device':gui['bit_device'],
            'effective_ranges':{name:list(pair) for name,pair in gui['ranges'].items()},
            'stored_ranges':{name:list(pair) for name,pair in gui['stored_ranges'].items()},
            'retained_fields':gui['retained_fields'], 'unverified_selectors':gui['unverified_selectors']}


_LIVE_FBD_POINTER_DEFAULTS = {
    # Exact native P grid and saved defaults; no family/language fallback.
    ('L02/L02-P', 'en', 932): (2048, 4095),
    ('L06/L06-P', 'en', 932): (2048, 4095),
    ('L26/L26-P', 'en', 932): (2048, 4095),
    ('Q00U', 'en', 932): (256, 511),
    ('Q03UDV', 'en', 932): (2048, 4095),
    ('Q03UDV', 'en', 936): (2048, 4095),
    ('Q06UDEH', 'en', 932): (2048, 4095),
}


def native_fbd_pointer_initialization(metadata: bytes, program_kind: int, *, native_identity=None):
    return _native_pointer_initialization(metadata, program_kind, native_identity=native_identity)


def _native_pointer_initialization(metadata: bytes, program_kind: int, *, native_identity=None):
    """Missing P default observed separately in five GX Works2 1.635 L menus.

    Original GUI compilation, original Workspace setter/save and cold compile
    controls are frozen in gxw-fbd-ui-entry-20261013.zip/l-pointer-allocation/.
    Configured and partial ranges are preserved. This is not a family rule.
    Live controls are separately measured per menu, language and code page.
    The live identity must match the actual stored project code page.
    """
    text_context = read_project_text_context(metadata)
    cpu = text_context['cpu']
    measured = cpu in ('L02S', 'L02', 'L06', 'L26', 'L26-BT')
    after = (2048, 4095) if measured else None
    if native_identity is not None:
        from gxworks2.project_profiles import native_project_profile
        profile = native_project_profile(cpu)
        measured = (isinstance(native_identity, dict) and profile is not None
            and native_identity.get('cpu_menu') == profile.cpu
            and native_identity.get('software_version') == '1.635.0.1'
            and type(native_identity.get('project_codepage')) is int
            and native_identity['project_codepage'] == text_context['codepage'])
        after = (_LIVE_FBD_POINTER_DEFAULTS.get((profile.cpu,
            native_identity.get('native_ui_language'),native_identity['project_codepage'])) if measured else None)
    if program_kind != 208 or not measured or after is None:
        return None
    if read_project_system_variables(metadata)['ranges']['P'] != (0xffffffff, 0xffffffff):
        return None
    return {'offset': 68, 'before': [-1, -1], 'after': list(after)}


def read_project_compile_options(raw: bytes) -> dict:
    """Read the observed saved option array, retaining unknown option words.

    The code-page DWORD is followed by the 88-byte SystemVariablesInfo record
    and a counted DWORD array including its final 0xffffffff sentinel. Native
    PLCProjectData.Set/GetCompileOption stores at most 32 words. Only the
    global-variable hiding tag is interpreted, as in OnMemoryDataManager's
    ParseData; this describes source binding, not CPU or runtime acceptance.
    """
    context = read_project_text_context(raw)
    offset = context['codepage_offset'] + 4 + 88
    if offset + 4 > len(raw):
        raise GXWFormatError('truncated project compile option count')
    count = struct.unpack_from('<I', raw, offset)[0]
    end = offset + 4 + 4 * count
    if not 1 <= count <= 32 or end > len(raw):
        raise GXWFormatError('project compile options outside native storage bound')
    values = struct.unpack_from('<' + str(count) + 'I', raw, offset + 4)
    if values[-1] != 0xffffffff or 0xffffffff in values[:-1]:
        raise GXWFormatError('unsupported project compile option sentinel')
    hiding = False
    for value in values[:-1]:
        if value & 0xffff0000 == 0x10010000:
            hiding = bool(value & 1)
    return dict(count_offset=offset, values=values, global_variable_hiding=hiding)


@dataclass
class XmlElement:
    name: str
    start: int
    content_start: int
    content_end: int = 0
    end: int = 0
    text: str = ""
    children: list = field(default_factory=list)

    def fields(self) -> dict:
        result = {}
        for child in self.children:
            if child.name in result:
                raise GXWFormatError(f"duplicate XML field {child.name}")
            result[child.name] = child
        return result


def xml_elements(raw: bytes) -> tuple[list[XmlElement], str]:
    """Return an XML tree with original byte spans, including UTF-16 input."""
    encoding = ("utf-16le" if raw.startswith((b"\xff\xfe", b"<\x00")) else
                "utf-16be" if raw.startswith((b"\xfe\xff", b"\x00<")) else "utf-8")
    parser = expat.ParserCreate(namespace_separator="}")
    roots, stack = [], []
    step = 2 if encoding.startswith("utf-16") else 1

    def tag_end(start):
        # Respect quoted '>' characters in attributes.
        quote = None
        for offset in range(start, len(raw), step):
            char = raw[offset:offset + step]
            if quote:
                if char == quote:
                    quote = None
            elif char in ('"'.encode(encoding), "'".encode(encoding)):
                quote = char
            elif char == ">".encode(encoding):
                return offset + step
        raise GXWFormatError("unterminated metadata tag")

    def start(name, attrs):
        pos = parser.CurrentByteIndex
        node = XmlElement(name.split("}")[-1], pos, tag_end(pos))
        (stack[-1].children if stack else roots).append(node)
        stack.append(node)

    def end(name):
        node = stack.pop()
        node.content_end = parser.CurrentByteIndex
        opening = raw[node.start:node.content_start].decode(encoding)
        node.end = node.content_start if opening.endswith("/>") else tag_end(node.content_end)

    def chars(value):
        if stack:
            stack[-1].text += value

    def reject(*args):
        raise GXWFormatError("DTD/entities are unsupported in GXW metadata")

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    parser.StartDoctypeDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    try:
        parser.Parse(raw, True)
    except (expat.ExpatError, UnicodeError) as exc:
        raise GXWFormatError(f"invalid or unsupported GXW XML: {exc}") from exc
    return roots, encoding


def current_rows(raw: bytes, dataset: str, row_name: str) -> tuple[list[XmlElement], str]:
    roots, encoding = xml_elements(raw)
    # Historical diffgr:before rows must never become current write targets.
    if len(roots) != 1:
        raise GXWFormatError("expected one metadata XML root")
    candidates = ([roots[0]] if roots[0].name == dataset else
                  [child for child in roots[0].children if child.name == dataset])
    if len(candidates) != 1:
        raise GXWFormatError(f"missing or ambiguous current dataset {dataset}")
    return [child for child in candidates[0].children if child.name == row_name], encoding


def project_source_groups(raw: bytes, *, folder_type: int = 7) -> tuple[dict[str, dict], list[dict]]:
    """Pair current project POU source/labels by native folder and file roles.

    Folder type 7 selects structured POUs; type 8 selects simple/SFC POUs.
    File roles 1/2 pair labels and source in the inspected native projects.
    Child display names are not binding keys. Missing/duplicate roles remain
    explicit gaps; unknown file roles and their payloads are left untouched.
    """
    rows, _ = current_rows(raw, 'DSPROJECTDATA', 'D_Projectdata')
    groups = defaultdict(list)
    for row in rows:
        fields = {key: value.text.strip() for key, value in row.fields().items()}
        if fields.get('bScrapFlag', 'false').lower() in ('true', '1'):
            continue
        if fields.get('ucFolderType') == str(folder_type):
            groups[tuple(fields.get(key) for key in
                         ('ucProductType', 'uiFolderNo', 'ucReserve'))].append(fields)
    result, issues = {}, []
    for key, members in groups.items():
        labels = [m.get('szName', '') for m in members if m.get('ucFileType') == '1']
        sources = [m.get('szName', '') for m in members if m.get('ucFileType') == '2']
        if (key[0] != '1' or key[2] != '0' or not key[1] or not key[1].isascii()
                or not key[1].isdecimal() or len(key[1]) > 10
                or not 1 <= int(key[1]) <= 0xffffffff or len(labels) != 1 or len(sources) != 1
                or not labels[0].endswith('.lh') or not sources[0].endswith('.pou')):
            issues.append({'code': 'project_source_owner_gap',
                           'streams': [m.get('szName', '') for m in members],
                           'message': 'missing or ambiguous native project declaration/source group'})
            continue
        result[sources[0]] = {'labels': labels[0], 'folder': int(key[1])}
    return result, issues


def project_resource_groups(raw: bytes) -> tuple[dict[str, dict], list[dict]]:
    """Current resource/task ownership from directory roles, not cached names.

    Folder type 3 pairs one resource (file role 1) with its tasks (role 2).
    These roles agree with original resource/task parent getters in the frozen
    engineering controls. Other folder/file roles remain uninterpreted.
    """
    rows, _ = current_rows(raw, 'DSPROJECTDATA', 'D_Projectdata')
    groups = defaultdict(list)
    for row in rows:
        fields = {key: value.text.strip() for key, value in row.fields().items()}
        if fields.get('bScrapFlag', 'false').lower() in ('true', '1'):
            continue
        if fields.get('ucFolderType') == '3':
            groups[tuple(fields.get(key) for key in
                         ('ucProductType', 'uiFolderNo', 'ucReserve'))].append(fields)
    result, issues, folders = {}, [], set()
    for key, members in groups.items():
        resources = [m.get('szName', '') for m in members if m.get('ucFileType') == '1']
        tasks = [m.get('szName', '') for m in members if m.get('ucFileType') == '2']
        if (key[0] != '1' or key[2] != '0' or not key[1] or not key[1].isascii()
                or not key[1].isdecimal() or len(key[1]) > 10
                or not 1 <= int(key[1]) <= 0xffffffff or len(resources) != 1
                or not resources[0].endswith('.res') or any(not name.endswith('.tsk') for name in tasks)
                or len(tasks) != len(set(tasks)) or resources[0] in result):
            issues.append({'code': 'project_resource_owner_gap',
                           'streams': [m.get('szName', '') for m in members],
                           'message': 'missing or ambiguous native resource/task group'})
            continue
        result[resources[0]] = {'folder': int(key[1]), 'tasks': tasks}
        if int(key[1]) in folders:
            issues.append({'code': 'project_resource_owner_gap',
                           'streams': [m.get('szName', '') for m in members],
                           'message': 'duplicate native resource folder identity'})
        folders.add(int(key[1]))
    return result, issues


def existing_source_groups(metadata: bytes, streams: Mapping[str, bytes]) -> tuple[dict, list]:
    """Bind editable project and user-library sources to existing declarations.

    Library folder numbers are local to their namespace. Reuse the native
    directory roles already used by the callable resolver rather than pairing
    streams by their display name or suffix alone.
    """
    groups, issues = {}, []
    for folder_type in (7, 8):
        current, gaps = project_source_groups(metadata, folder_type=folder_type)
        groups.update(current)
        issues.extend(gaps)
    from .callable_sources import _user_library_owners
    owners, gaps, _descriptors = _user_library_owners(metadata, streams)
    issues.extend(gaps)
    for labels, owner in owners.items():
        logical = owner['source_program']
        if logical in groups:
            raise GXWFormatError('ambiguous project/library source ownership: ' + logical)
        groups[logical] = {'labels': labels, 'library': owner['library'],
                           'owner_name': owner['name'], 'folder': None}
    return groups, issues


def logical_mapping(raw: bytes) -> dict[str, str]:
    rows, _ = current_rows(raw, "DSPROJECTDATA", "D_Projectdata")
    result = {}
    for row in rows:
        values = {k: v.text.strip() for k, v in row.fields().items()}
        scrap = values.get("bScrapFlag", "false").lower()
        if scrap in {"true", "1"}:
            continue
        if scrap not in {"false", "0"}:
            raise GXWFormatError("unsupported bScrapFlag")
        name, stream = values.get("szName"), values.get("iID")
        if not name or not stream:
            raise GXWFormatError("current project row lacks name/ID")
        if name in result or stream in result.values():
            raise GXWFormatError("ambiguous current project name/stream mapping")
        result[name] = stream
    return result


def project_stream_mapping(outer) -> dict[str, str]:
    """Resolve directory identities through the current history storage IDs.

    D_Projectdata.iID identifies the engineering object; D_History.iID names
    its physical _hdb stream. They coincide in many files but diverge after
    native history/project compaction. Join on iProjectdataID, never on array
    position or source contents. Historical names may remain after a rename.
    """
    if not isinstance(outer, Mapping):
        values = {'projectdatalist.xml': outer.read_stream('projectdatalist.xml')}
        if outer.find_streams('history.xml'):
            values['history.xml'] = outer.read_stream('history.xml')
        outer = values
    directory = logical_mapping(outer['projectdatalist.xml'])
    if 'history.xml' not in outer:
        return directory
    rows, _ = current_rows(outer['history.xml'], 'DSHISTORY', 'D_History')
    storage = {}
    for row in rows:
        fields = {key: value.text.strip() for key, value in row.fields().items()}
        identity = fields.get('iProjectdataID')
        physical = fields.get('iID', identity)
        if not identity or not physical:
            raise GXWFormatError('current history row lacks project/storage identity')
        if identity in storage:
            raise GXWFormatError('ambiguous current project/history storage identity: ' + identity)
        storage[identity] = physical
    result = {name: storage.get(identity, identity) for name, identity in directory.items()}
    if len(set(result.values())) != len(result):
        raise GXWFormatError('ambiguous current physical project stream identity')
    return result


def md5_base64(raw: bytes) -> str:
    return base64.b64encode(hashlib.md5(raw).digest()).decode("ascii")


def synchronize_history(raw: bytes, replacements: dict, *, current_mapping: dict | None = None) -> tuple[bytes, list, list]:
    """replacements: logical name -> (stream ID, old payload, new payload).

    Native POU/child renaming can retain previous source or label history names
    across saves and payload changes. Resolve aliases through the current directory's unique
    stream ID, retaining the historical name bytes. A name belonging to another
    current object remains a conflicting identity.
    """
    rows, encoding = current_rows(raw, "DSHISTORY", "D_History")
    edits, changes, preserved = [], [], []
    for logical, (stream, old, new) in replacements.items():
        matches = []
        for row in rows:
            fields = row.fields()
            identity = fields.get('iID') or fields.get('iProjectdataID')
            if identity is not None and identity.text.strip() == stream:
                matches.append(fields)
        if len(matches) != 1:
            raise GXWFormatError(f"expected one current history row for {logical}; found {len(matches)}")
        fields = matches[0]
        history_name = fields["szProjectdataName"].text.strip() if "szProjectdataName" in fields else None
        if history_name != logical:
            source_alias = (current_mapping is not None and current_mapping.get(logical) == stream
                            and list(current_mapping.values()).count(stream) == 1
                            and history_name and history_name not in current_mapping
                            and any(logical.endswith(suffix) and history_name.endswith(suffix)
                                    for suffix in (".pou", ".lh", ".lnb", ".lnl")))
            if not source_alias:
                raise GXWFormatError(f"history name/ID mismatch for {logical}")
            preserved.append({"object": logical, "stream": stream, "field": "szProjectdataName",
                              "status": "native-source-name-alias", "value": history_name,
                              "reason": "current directory uniquely binds the renamed source stream; history name preserved"})
        if "iFileSize" not in fields or not fields["iFileSize"].text.strip().isdigit():
            raise GXWFormatError(f"missing/invalid history iFileSize for {logical}")
        updates = {"iFileSize": str(len(new))}
        digest = fields.get("szMD5val")
        if digest and digest.text.strip() == md5_base64(old):
            updates["szMD5val"] = md5_base64(new)
        else:
            preserved.append({"object": logical, "field": "szMD5val", "status": "unknown",
                              "reason": "source digest absent or differs from Base64(MD5(payload)); preserved"})
        for field_name, value in updates.items():
            node = fields[field_name]
            if node.text.strip() == value:
                continue
            lexical = raw[node.content_start:node.content_end].decode(encoding)
            if node.children or lexical.strip() != node.text.strip():
                raise GXWFormatError(f"unsupported lexical representation of {field_name}")
            leading = lexical[:len(lexical) - len(lexical.lstrip())]
            trailing = lexical[len(lexical.rstrip()):]
            replacement = (leading + value + trailing).encode(encoding)
            edits.append((node.content_start, node.content_end, replacement))
            changes.append({"object": logical, "stream": stream, "field": field_name,
                            "before": node.text.strip(), "after": value,
                            "old_offset": node.content_start, "old_length": node.content_end - node.content_start,
                            "new_length": len(replacement), "offset_space": "history.xml bytes",
                            "status": "confirmed"})
    result = raw
    for start, end, payload in sorted(edits, reverse=True):
        result = result[:start] + payload + result[end:]
    shift = 0
    for change in sorted(changes, key=lambda c: c["old_offset"]):
        change["new_offset"] = change["old_offset"] + shift
        shift += change["new_length"] - change["old_length"]
    current_rows(result, "DSHISTORY", "D_History")
    return result, changes, preserved
