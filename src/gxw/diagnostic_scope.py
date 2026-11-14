# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Recorded native diagnostic scopes and identity normalization."""
from __future__ import annotations


# Existing adapter plan/inventory allocation capacity, not a CPU program limit.
NATIVE_SOURCE_CAPACITY = 8192


NATIVE_COMPILATION_OPERATIONS = ('Compiler.Build', 'Compiler.ChangeSFCProgram')


# Original public 100-byte FBD Build reports, native version 1.635.0.1.
# Each actual menu choice has a cold rejected-Build control with identical
# node bounds in networks 1 and 3 and an empty middle source network.
# See tests/fixtures/gxw_compile_positions_native.json. This scope says nothing
# about ST, legacy raw reports, expanded FB instances or PLC execution.
PUBLIC_FBD_COMPILE_POSITION_CPUS = frozenset((
    'FX3U/FX3UC', 'Q04UDV', 'FX1N/FX1NC', 'L02', 'FX0N', 'FX1S',
    'FX2/FX2C', 'FX2N/FX2NC', 'FX3S', 'FX3G', 'Q00J', 'Q00UJ', 'Q00', 'Q00U',
    'Q01', 'Q01U', 'Q02/Q02H', 'Q02PH', 'Q02U', 'Q03UD', 'Q03UDE', 'Q03UDV',
    'Q04UDH', 'Q04UDEH', 'Q04UDPV', 'Q06H', 'Q06PH', 'Q06UDH', 'Q06UDEH',
    'Q06UDV', 'Q06UDPV', 'Q10UDH', 'Q10UDEH', 'Q12H', 'Q12PH', 'Q12PRH',
    'Q13UDH', 'Q13UDEH', 'Q13UDV', 'Q13UDPV', 'Q20UDH', 'Q20UDEH', 'Q25H',
    'Q25PH', 'Q25PRH', 'Q26UDH', 'Q26UDEH', 'Q26UDV', 'Q26UDPV', 'Q50UDEH',
    'Q100UDEH', 'L02S', 'L06', 'L26', 'L26-BT',
))


# Controlled line changes, original 1.635.0.1 public reports and declaration
# getters. These scopes describe positions, not instruction availability.
PUBLIC_DECLARATION_COMPILE_POSITION_CPUS = frozenset((
    'Q00J', 'Q00', 'Q01', 'Q02/Q02H', 'Q02PH', 'Q06H', 'Q06PH',
    'Q12H', 'Q12PH', 'Q12PRH', 'Q25H', 'Q25PH', 'Q25PRH',
    'FX3U/FX3UC',
))


PUBLIC_LIBRARY_ST_COMPILE_POSITION_CPUS = frozenset((
    'Q00UJ', 'Q00U', 'Q01U', 'Q02U', 'Q03UD', 'Q04UDH', 'Q06UDH',
    'Q10UDH', 'Q13UDH', 'Q20UDH', 'Q26UDH', 'Q50UDEH', 'Q100UDEH',
))


PUBLIC_GLOBAL_DECLARATION_COMPILE_POSITION_CPUS = frozenset((
    'FX3U/FX3UC', 'FX3G', 'FX3S', 'FX2N/FX2NC',
))


PUBLIC_LIBRARY_STRUCTURE_COMPILE_POSITION_CPUS = frozenset((
    'FX1N/FX1NC', 'FX1S', 'FX0N', 'FX1', 'FX0S/FX0', 'FXU/FX2C',
))


# Original SFC public reports, narrow/wide prefixes in actions and conditions.
# Each concrete menu is frozen in gxw-sfc-position-menus-20261014; this scope
# does not come from graph-reader or ordinary-ladder instruction support.
SFC_CHILD_INSTRUCTION_POSITION_CPUS = frozenset((
    'FX0S/FX0', 'FX0N', 'FX1', 'FX1S', 'FX1N/FX1NC', 'FXU/FX2C',
    'FX2N/FX2NC', 'FX3S', 'FX3G', 'FX3U/FX3UC',
    'Q00J', 'Q00UJ', 'Q00', 'Q00U', 'Q01', 'Q01U', 'Q02/Q02H', 'Q02PH', 'Q02U',
    'Q03UD', 'Q03UDE', 'Q03UDV', 'Q04UDH', 'Q04UDEH', 'Q04UDV', 'Q04UDPV',
    'Q06H', 'Q06PH', 'Q06UDH', 'Q06UDEH', 'Q06UDV', 'Q06UDPV', 'Q10UDH',
    'Q10UDEH', 'Q12H', 'Q12PH', 'Q12PRH', 'Q13UDH', 'Q13UDEH', 'Q13UDV',
    'Q13UDPV', 'Q20UDH', 'Q20UDEH', 'Q25H', 'Q25PH', 'Q25PRH', 'Q26UDH',
    'Q26UDEH', 'Q26UDV', 'Q26UDPV', 'Q50UDEH', 'Q100UDEH',
    'L02S', 'L02', 'L06', 'L26', 'L26-BT',
))


def _id(value):
    return tuple(value) if isinstance(value, list) and len(value) == 12 else None


PUBLIC_CHECK_REPORT_CONVERSION_CPUS = frozenset((
    'Q00', 'Q00J', 'Q00U', 'Q00UJ', 'Q01', 'Q01U', 'Q02/Q02H', 'Q02PH', 'Q02U',
    'Q03UD', 'Q03UDE', 'Q03UDV', 'Q04UDEH', 'Q04UDH', 'Q04UDPV', 'Q04UDV',
    'Q06H', 'Q06PH', 'Q06UDEH', 'Q06UDH', 'Q06UDPV', 'Q06UDV', 'Q100UDEH',
    'Q10UDEH', 'Q10UDH', 'Q12H', 'Q12PH', 'Q12PRH', 'Q13UDEH', 'Q13UDH',
    'Q13UDPV', 'Q13UDV', 'Q20UDEH', 'Q20UDH', 'Q25H', 'Q25PH', 'Q25PRH',
    'Q26UDEH', 'Q26UDH', 'Q26UDPV', 'Q26UDV', 'Q50UDEH',
))


# Observed GX Works2 1.635.0.1 compiler/check report codes verified against
# isolated structured FBD/ST/declaration witnesses. Unknown codes remain raw.
NATIVE_DIAGNOSTIC_CODE_HINTS: dict[int, dict[str, str]] = {
    0x500F1028: {
        'category': 'system_variable_pool_overlap',
        'summary': (
            "Explicit device address overlaps the project's reserved automatic label allocation range "
            '(system_variable_ranges); pick an address outside that pool or omit device to let the compiler auto-allocate'
        ),
    },
    0x500C5100: {
        'category': 'declaration_iec_mismatch',
        'summary': (
            'Global declaration MELSEC device and IEC address disagree '
            '(for example FX3U octal X10 maps to %IX8, Q-series hex X10 maps to %IX16, '
            'M0 maps to %MX0.0, D0 maps to %MW0.0); omit iec_address to auto-derive'
        ),
    },
    0x050F1000: {
        'category': 'declaration_iec_format',
        'summary': (
            'Invalid declaration IEC address or unsupported device mapping column '
            '(X/Y require %IX<decimal_index>/%QX<decimal_index> without .bit; M/D require %MX0.<n>/%MW0.<n>)'
        ),
    },
    0x500C2010: {
        'category': 'unconnected_graphic_pin',
        'summary': (
            'Graphic element has an unconnected input pin '
            "(structured FBD parameter networks have no implicit left power rail; "
            "drive contacts from input('TRUE') or use input/output terminals)"
        ),
    },
    0x500C2020: {
        'category': 'floating_wire_segment',
        'summary': (
            'Floating or unpowered wire segment in structured diagram '
            '(often cascades from an unconnected upstream contact/input 0x500c2010)'
        ),
    },
    0x500C2032: {
        'category': 'network_label_syntax',
        'summary': (
            'Structured network block or Jump label error '
            '(network.title must be empty "" or a valid <identifier>: Jump label; '
            'put descriptive headings in block_comment)'
        ),
    },
    0x500C1205: {
        'category': 'unconnected_sink_element',
        'summary': 'Unconnected or isolated output/coil element (IN port has no incoming wire)',
    },
    0x050C9300: {
        'category': 'duplicate_output',
        'summary': (
            'The same device is written as an output in more than one place (double coil); the '
            'vendor Program Check reports every conflicting write position for that device. '
            'Give the device one writing rung, or move the repeated writes into one rung '
            'whose branches only differ in their own conditions'
        ),
    },
    0x500C5027: {
        'category': 'unsupported_declaration_type',
        'summary': 'Unsupported data type in declaration for the selected CPU',
    },
    0x500C5150: {
        'category': 'compiler_summary_failure',
        'summary': 'Vendor compiler outer summary failure code; inspect the preceding specific diagnostic codes',
    },
}


def explain_native_diagnostic_code(code: int) -> dict[str, str] | None:
    """Return observed root-cause guidance for a known 32-bit native report code."""
    if type(code) is not int:
        return None
    normalized = code & 0xFFFFFFFF
    entry = NATIVE_DIAGNOSTIC_CODE_HINTS.get(normalized)
    if entry is None:
        return None
    return {'code_hex': f'0x{normalized:08x}', **entry}


def explain_native_diagnostic_codes(codes) -> dict[str, str]:
    """Map observed native report codes in `codes` to concise explanations."""
    seen = sorted({code & 0xFFFFFFFF for code in codes if type(code) is int})
    return {
        f'0x{code:08x}': NATIVE_DIAGNOSTIC_CODE_HINTS[code]['summary']
        for code in seen
        if code in NATIVE_DIAGNOSTIC_CODE_HINTS
    }


# Instructions whose first operand is a writing position. The vendor double-coil
# check counts these; contacts and other read-class forms never conflict.
WRITING_OPS = frozenset((
    'OUT', 'OUTP', 'SET', 'SETP', 'RST', 'RSTP', 'PLS', 'PLF', 'ALT', 'ALT P',
    'COIL', 'TIMER', 'COUNTER', 'MPSOUT',
))


def device_write_rungs(ladder):
    """Map every written device to the rungs that write it, in source order.

    Only the submitted ladder establishes this; a report that names a device the
    program does not write keeps no rung list rather than inheriting a guess.
    """
    rungs = ladder.get('rungs') if isinstance(ladder, dict) else None
    result = {}
    for rung in rungs or ():
        rung_id = rung.get('rung_id') if isinstance(rung, dict) else None
        if type(rung_id) is not int:
            continue
        for instruction in rung.get('ordered_instructions') or ():
            if not isinstance(instruction, dict):
                continue
            opcode = str(instruction.get('op') or instruction.get('type') or '').strip().upper()
            args = instruction.get('args') or ()
            if opcode not in WRITING_OPS or not args:
                continue
            device = str(args[0]).strip().upper()
            if device:
                positions = result.setdefault(device, [])
                if rung_id not in positions:
                    positions.append(rung_id)
    return result


def summarize_check_reports(reports, *, writes=None, limit=32):
    """Group vendor check reports into one finding per offending device.

    A rejected check repeats one code for every conflicting write position, so the
    raw list hides the cause in repetition. Each group keeps the literal code, the
    affected operand, the occurrence count and the writing rungs the submitted
    ladder establishes. Unrecognised codes stay listed under their own code; no
    report is dropped, reclassified or silently assumed harmless.
    """
    groups = {}
    for report in reports or ():
        if not isinstance(report, dict):
            continue
        kind, code = report.get('kind'), report.get('code')
        if type(code) is not int or kind not in (2, 3):
            continue
        arguments = [row.get('text') for row in report.get('arguments') or ()
                     if isinstance(row, dict) and isinstance(row.get('text'), str)]
        device = arguments[0].strip().upper() if arguments else ''
        entry = groups.setdefault((code & 0xFFFFFFFF, device), {
            'code': code & 0xFFFFFFFF, 'device': device,
            'severity': 'error' if kind == 2 else 'warning',
            'occurrences': 0, 'steps': []})
        entry['occurrences'] += 1
        step = report.get('step')
        if type(step) is int and step >= 0 and len(entry['steps']) < 16 and step not in entry['steps']:
            entry['steps'].append(step)
    findings = []
    for entry in sorted(groups.values(), key=lambda row: (not row['device'], row['device'], row['code'])):
        hint = NATIVE_DIAGNOSTIC_CODE_HINTS.get(entry['code'])
        finding = {
            'code': f'0x{entry["code"]:08x}',
            'category': hint['category'] if hint else 'unclassified_report',
            'severity': entry['severity'],
            'occurrences': entry['occurrences'],
            'steps': entry['steps'],
            'fixable': entry['severity'] == 'error',
        }
        if entry['device']:
            finding['device'] = entry['device']
            rungs = (writes or {}).get(entry['device']) or []
            if rungs:
                finding['rung_ids'] = list(rungs)[:16]
            finding['message'] = (entry['device'] + ': ' + hint['summary'] if hint else
                                  entry['device'] + ': unclassified native report ' + finding['code'])
        else:
            finding['message'] = hint['summary'] if hint else (
                'unclassified native report ' + finding['code'])
        findings.append(finding)
        if len(findings) >= limit:
            break
    return findings


def native_check_findings(validation, ladder=None, *, limit=32):
    """Project a native check result into actionable findings for a caller.

    Reads the Program Check reports the adapter retained, never the raw event log:
    a code without a retained report list is reported as an unexplained rejection
    instead of an empty success.
    """
    projection = (validation or {}).get('diagnostic_projection') or {}
    reports = projection.get('public_diagnostics') or ()
    writes = device_write_rungs(ladder) if ladder else {}
    return summarize_check_reports(reports, writes=writes, limit=limit)


def check_rejection_is_advisory(validation, ladder, *, plc_model, program_name):
    """Delivery policy for a completed check after an accepted compilation.

    The vendor reports stay intact. Classify the full report lists rather than
    the grouped UI findings, which may omit positions or diagnostic codes.
    """
    validation = validation or {}
    compilation = validation.get('compilation') or {}
    check = validation.get('public_check') or {}
    if (compilation.get('acceptance') != 'accepted' or compilation.get('completed') is not True
            or check.get('status') != 'rejected' or check.get('completed') is not True):
        return False
    projection = validation.get('diagnostic_projection') or {}
    public = projection.get('public_diagnostics') or ()
    backend = projection.get('backend_diagnostics') or ()
    if not isinstance(public, (list, tuple)) or not public or not isinstance(backend, (list, tuple)):
        return False
    reports = [*public, *backend]
    if any(not isinstance(row, dict) or row.get('kind') not in (2, 3)
           or type(row.get('code')) is not int for row in reports):
        return False
    errors = [row for row in reports if row['kind'] == 2]
    if not errors:
        return True

    # GXW metadata stores the logical stream name; the checker names its POU.
    program_name = (program_name or '').removesuffix('.Program.pou')
    selection = validation.get('selection') or {}
    if (not isinstance(ladder, dict) or not program_name
            or selection.get('status') != 'verified' or selection.get('source_bytes_current') is not True
            or selection.get('selected_sources') != [program_name]):
        return False
    from plc.device_identity import canonical_reference_device
    from plc.validation import is_bit_device

    steps = {}
    for rung in ladder.get('rungs', ()):
        for row in rung.get('ordered_instructions', ()):
            if row.get('kind') == 'instruction' and type(row.get('step')) is int:
                steps.setdefault(row['step'], []).append(row)
    for report in errors:
        name = report.get('name')
        arguments = report.get('arguments')
        step = report.get('step')
        if (report['code'] != 0x050C9300 or not isinstance(name, dict) or name.get('text') != program_name
                or not isinstance(arguments, list) or len(arguments) != 1 or not isinstance(arguments[0], dict)
                or type(step) is not int or len(steps.get(step, ())) != 1):
            return False
        instruction = steps[step][0]
        device = canonical_reference_device(arguments[0].get('text'))
        if (instruction.get('op') not in {'SET', 'SETP', 'RST', 'RSTP'} or not device
                or [canonical_reference_device(arg) for arg in instruction.get('args', ())] != [device]
                or not is_bit_device(device, plc_model)):
            return False
    return True
