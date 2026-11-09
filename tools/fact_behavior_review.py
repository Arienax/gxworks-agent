"""Authored, source-checked instruction and device behaviour facts.

These records describe documented manual behaviour. They are *knowledge*, not
Core DSL statements: no value carries a ``behavior`` entry, so nothing here is
admitted by the bounded executor, and no fact promotes a CPU, a call, a native
compilation or a hardware result. Values use formal tokens only, so the authored
representation passes the same typed-value gate as every other published fact;
no manual prose is copied into the runtime bundle.

Every record is bound to an exact private chunk (manual, revision, section and
page list) and to witness phrases from that chunk. A moved chunk, a changed
revision or a re-worded source fails closed instead of silently re-binding the
authored fact. Section 5.2 is a grouped heading: all six symbols own the same
section, not only the symbol the index happened to attach to the chunk.
"""
from __future__ import annotations

import json
import re

METHOD = 'authored_behaviour_review_v1'

# Reviewed families: chunk id -> (section, witness phrases verified in that chunk).
SECTIONS = {
    'contacts': {
        'manual_id': 'fxcpu_basic_applied_m', 'revision': 'M', 'header_chunk': 1559,
        'chunks': {
            1559: ('5.1 LD, LDI, AND, ANI, OR, ORI', (
                'The LD and LDI instructions are contacts connected to bus lines.',
                'The AND and ANI instructions connect one contact in series.',
                'The OR and ORI instructions connect one contact in parallel.',
                'The ST language may not have the instructions (symbols)')),
            1560: ('5.1 LD, LDI, AND, ANI, OR, ORI', (
                '1. LD (Initial logical operation of NO (normally open) contacts)',
                '2. LDI (Initial logical operation of NC (normally closed) contact type)',
                '3. AND (Serial connection of NO (normally open) contacts)',
                'Y000:= X000;')),
            1561: ('5.1 LD, LDI, AND, ANI, OR, ORI', (
                'The parallel connection by OR or ORI',
                'connected to the second preceding LD or LDI',
                'Devices used in LD, LDI, AND, ANI, OR and ORI instructions can be indexed with index registers (V, Z).',
                'Applicable only to the FX3U and FX3UC PLCs.',
                'M8316 (Non-existing I/O specification error) turns ON.',
                'operation error (error code: 6706) occurs.')),
        },
        'forms': ('LD', 'LDI', 'AND', 'ANI', 'OR', 'ORI'),
    },
    'edges': {
        'manual_id': 'fxcpu_basic_applied_m', 'revision': 'M', 'header_chunk': 1562,
        'chunks': {
            1562: ('5.2 LDP, LDF, ANDP, ANDF, ORP, ORF', (
                'detect the rising edge, and become active during one operation',
                'detect the falling edge, and become active during one operation',
                'LDP(EN,s);',
                'ORP(EN,s);')),
            1564: ('5.2 LDP, LDF, ANDP, ANDF, ORP, ORF', (
                'A bit data register (D) can be specified as a device used in LDP, LDF, ANDP, ANDF, ORP and ORF',
                'Only 16-bit data resister is applicable.',
                'Not supported by the FX1S, FX1N or FX1NC PLC.',
                'the operation varies depending on the device number range',
                'only the first contact which detects the',
                'The following two circuits offer the same operation.')),
            1565: ('5.2 LDP, LDF, ANDP, ANDF, ORP, ORF', (
                'executed two or',
                'same as the time when the instruction was executed last',
                'the instruction for falling edge pulse is not executed without regard to the ON/',
                'It is necessary to set to ON the target device or operation condition device once and then set it to OFF',
                'instruction for rising edge pulse is executed if a target device',
                'PLS instruction is not executed.')),
        },
        'ladder_chunks': {
            210: ('7.5 LDP, LDF, ANDP, ANDF, ORP, ORF', (
                'detect the rising edge, and become acti ve during one operation cycle only',
                'detect the falling edge, and become active during one operation cycle only')),
            212: ('7.5 LDP, LDF, ANDP, ANDF, ORP, ORF', (
                'A bit in data register (D) can be specified as a device used in LDP',
                'is ON during only one operation cycle when X000 to X002 turn OFF from ON')),
            214: ('7.5 LDP, LDF, ANDP, ANDF, ORP, ORF', (
                'Differences in the operation caused by auxiliary relay (M) numbers',
                'M0 to M2799, M3072 to M7679')),
            215: ('7.5 LDP, LDF, ANDP, ANDF, ORP, ORF', (
                'programmed in the same step is executed two or more times within one operation cycle',
                'Program between FOR and NEXT instructions')),
        },
        'ladder_manual': {'manual_id': 'fx3_programming_r', 'revision': 'R'},
        'legacy_chunks': {
            'jy992d88101e': {'revision': 'E', 'chunks': {
                6292: ('2.8 Load Pulse, Load Trailing Pulse', ('LDP (LoaDPulse)', 'Rising edge pulse')),
                6293: ('2.9 And Pulse, And Trailing Pulse', ('ANP (ANd Pulse)', 'Rising edge pulse')),
                6294: ('2.10 Or Pulse, Or Trailing Pulse', ('ORP (OR Pulse)', 'Rising edge pulse'))}},
            'jy992d48301j': {'revision': 'J', 'chunks': {
                6698: ('2.8 Load Pulse, Load Trailing Pulse', ('LDP (LoaDPulse)', 'Rising edge pulse')),
                6699: ('2.9 And Pulse, And Trailing Pulse', ('ANP (ANd Pulse)', 'Rising edge pulse')),
                6700: ('2.10 Or Pulse, Or Trailing Pulse', ('ORP (OR Pulse)', 'Rising edge pulse'))}},
        },
        'legacy_mnemonics': {'LDP': 'LDP', 'LDF': 'LDF', 'ANDP': 'ANP', 'ANDF': 'ANF',
                             'ORP': 'ORP', 'ORF': 'ORF'},
        'legacy_roles': {6292: 'initial_logical_operation', 6293: 'serial_connection',
                         6294: 'parallel_connection', 6698: 'initial_logical_operation',
                         6699: 'serial_connection', 6700: 'parallel_connection'},
        'forms': ('LDP', 'LDF', 'ANDP', 'ANDF', 'ORP', 'ORF'),
    },
    'mep_mef': {
        'manual_id': 'fxcpu_basic_applied_m', 'revision': 'M', 'header_chunk': 1580,
        'chunks': {
            1580: ('5.9 MEP, MEF', (
                'change the operation results to pulses so that device numbers',
                'The operation results up to the MEP instruction become conductive when the driving contacts turn ON',
                'The operation results up to the MEF instruction become conductive when the driving contacts turn OFF',
                'MEP and MEF instructions are provided in the FX',
                'As the MEP and MEF instructions operate using t he operation results immediately before them',
                'The MEP and MEF instructions cannot be used at the list program as LD or OR.',
                'the MEP instruction result turns ON',
                'the MEF instruction result turns OFF')),
        },
        'forms': ('MEP', 'MEF'),
    },
    'set_rst': {
        'manual_id': 'fxcpu_basic_applied_m', 'revision': 'M', 'header_chunk': 1581,
        'chunks': {
            1581: ('5.10 SET, RST', (
                '1) Setting a bit device (SET instruction [holding operation])',
                'Even if the command input turns OFF after that, the device which was set to ON by SET instruction',
                'SET and RST instructions can be used for a same device as many times as necessary in an arbitrary',
                'SET(EN,d);',
                'SET(X000,Y000);')),
            1582: ('5.10 SET, RST', (
                'SET instructions located in parallel can be used consecutively as many times as necessary.',
                'Use RST instruction to reset a counter or retentive type timer.',
                'the current value of the counter remains unchanged',
                'are latched even after power failure.',
                'may be kept in the reset status and the timer or counter may be',
                'When RST instruction for a timer or counter is ex ecuted in a jumped program, subroutine program or')),
            1583: ('5.10 SET, RST', (
                'Devices used in SET and RST instructions can be indexed with index registers (V, Z).',
                'This is applicable only to the FX3U and FX3UC PLCs.',
                'instruction located nearest the END instruction',
                'be sure to create a program',
                'M8316 (non-existing',
                'an operation error (error code: 6706)')),
        },
        'forms': ('SET', 'RST'),
    },
    'pls_plf': {
        'manual_id': 'fxcpu_basic_applied_m', 'revision': 'M', 'header_chunk': 1585,
        'chunks': {
            1585: ('5.11 PLS, PLF', (
                'an applicable device is activated during only one operation cycle after a',
                'drive input turns ON',
                'because the status of M600 is latched even while the PLC is in the STOP',
                'The following two circuits cause a same operation.',
                'PLS(X000, M0);')),
        },
        'forms': ('PLS', 'PLF'),
    },
    'and_or_block': {
        'manual_id': 'fxcpu_basic_applied_m', 'revision': 'M', 'header_chunk': 1575,
        'chunks': {
            1575: ('5.6 AND(...), OR(...)', (
                'Use AND (...) instruction to connect a branch circuit (parallel circuit block) to the preceding circuit in series.',
                'Use OR (...) instruction to connect a series circuit block in parallel.',
                'There are no applicable devices.',
                'Y000:=(X000 OR X001) AND(X002 OR X003);',
                'Y001:=(X000 AND X001) OR(X002 AND X003);'))},
        'forms': ('AND(...)', 'OR(...)'),
    },
    'stack': {
        'manual_id': 'fxcpu_basic_applied_m', 'revision': 'M', 'header_chunk': 1576,
        'chunks': {
            1576: ('5.7 MPS, MRD, MPP', (
                'These PLCs have 11 memories called "Stack" which store the intermediate result (ON or OFF) of operations.',
                'MPS(EN);',
                'MRD(EN);',
                'MPP(EN);',
                'MRD, MPP: Always TRUE',
                'There are no applicable devices.',
                'MPS EN ENO'))},
        'forms': ('MPS', 'MRD', 'MPP'),
    },
    'inv': {
        'manual_id': 'fxcpu_basic_applied_m', 'revision': 'M', 'header_chunk': 1578,
        'chunks': {
            1578: ('5.8 INV', (
                'INV instruction inverts the operation result up to just before INV instruction.',
                'INV instruction can be used in a same position as serial contact instructions (AND, ANI, ANDP and ANDF).',
                'INV instruction cannot execute connection',
                'INV instruction cannot be used',
                'There are no applicable devices.',
                'INV(EN);',
                'Y000:= INV(X000)'))},
        'forms': ('INV',),
    },
    'program_end': {
        'manual_id': 'fxcpu_basic_applied_m', 'revision': 'M', 'header_chunk': 1590,
        'chunks': {
            1590: ('5.14 NOP (for simple project only)', (
                '5.13 END',
                'END instruction specifies the end of a program.',
                'PLCs immediately execute the output processing without executing steps after END',
                'At the first execution after the PLC mode was changed from STOP to RUN, PLCs start from END instruction.',
                'the watchdog timer',
                'Do not write END instruction in the middle of a program.',
                'This instruction is available for use only in the simple project. It cannot be programmed in the structured'))},
        'forms': ('END', 'NOP'),
    },
    'mc_mcr': {
        'manual_id': 'fxcpu_basic_applied_m', 'revision': 'M', 'header_chunk': 1587,
        'chunks': {
            1587: ('5.12 MC, MCR', (
                'When MC instruction is executed, instructions from MC to MCR are executed.',
                'MCR: Always TRUE',
                'Nesting level (0 to 7)')),
            1588: ('5.12 MC, MCR', (
                'the operation with the contact OFF is executed',
                'Timers (except retentive type timers) and devices driven by OUT instruction: Turn OFF',
                'Hold the current status.',
                'Use MC instruction and MCR instruction at a same nesting level as a pair.',
                'Always make MCR instruction "TRUE".',
                'The device specified by remains ON while MC instruction is executed.',
                'double coil operation')),
            1589: ('5.12 MC, MCR', (
                'if "MCR N5" is programmed without programming "MCR N6" and "MCR N7"',
                'Available nesting levels are from N0 to N7 (eight layers).')),
        },
        'forms': ('MC', 'MCR'),
    },
}

DEVICE_SECTIONS = {
    'state_relay': {
        'manual_id': 'fxcpu_device_common_l', 'revision': 'L',
        'chunks': {
            2345: ('2.4.1 Numbers of state relays', (
                'State relays (S) are important devices to simply program stepping type process control',
                'are combined',
                'with the step ladder instruction STL.',
                'The table below shows state relay (S) numbers. (Numbers are assigned in decimal.)')),
            2346: ('2.4.1 Numbers of state relays', (
                'S1000 to S4095',
                'S0 to S63 64 points',
                'S0 to S499 500 points',
                'S128 to S999')),
            2347: ('2.4.2 Functions and operation examples', (
                'When the operation proceeds to the next step,',
                'the state relay in the preceding step is',
                'When the PLC is turned OFF, all of general type',
                'When the ON/OFF status just before power',
                'in the same way as auxiliary relays')),
        },
    },
}

# Reviewed number table. ``class_binding`` records whether the reviewed columns
# were unambiguous for that CPU row; an unresolved binding keeps ranges only.
STATE_RELAY_RANGES = {
    ('FX3U', 'FX3UC'): {'class_binding': 'established', 'ranges': [
        {'class': 'initial_state', 'first': 0, 'last': 9, 'points': 10, 'latch': 'parameter_selectable'},
        {'class': 'general', 'first': 0, 'last': 499, 'points': 500, 'includes_initial_state': True,
         'latch': 'non_latched'},
        {'class': 'latched', 'first': 500, 'last': 899, 'points': 400, 'latch': 'battery_backed'},
        {'class': 'fixed_latched', 'first': 1000, 'last': 4095, 'points': 3096, 'latch': 'fixed'},
        {'class': 'annunciator', 'first': 900, 'last': 999, 'points': 100, 'latch': 'battery_backed'}]},
    ('FX3G', 'FX3GC'): {'class_binding': 'established', 'ranges': [
        {'class': 'initial_state', 'first': 0, 'last': 9, 'points': 10, 'latch': 'eeprom_backed'},
        {'class': 'general', 'first': 10, 'last': 899, 'points': 890, 'latch': 'non_latched'},
        {'class': 'annunciator', 'first': 900, 'last': 999, 'points': 100, 'latch': 'eeprom_backed'},
        {'class': 'fixed_latched', 'first': 1000, 'last': 4095, 'points': 3096,
         'latch': 'eeprom_backed_requires_optional_battery'}]},
    ('FX3S',): {'class_binding': 'established', 'ranges': [
        {'class': 'initial_state', 'first': 0, 'last': 9, 'points': 10, 'latch': 'eeprom_backed'},
        {'class': 'fixed_latched', 'first': 10, 'last': 127, 'points': 118, 'latch': 'eeprom_backed'},
        {'class': 'general', 'first': 128, 'last': 255, 'points': 128, 'latch': 'non_latched'}]},
    ('FX1S',): {'class_binding': 'column_binding_unresolved', 'ranges': [
        {'first': 0, 'last': 127, 'points': 128, 'includes_initial_state': True}]},
    ('FX1N', 'FX1NC'): {'class_binding': 'column_binding_unresolved', 'ranges': [
        {'first': 0, 'last': 127, 'points': 128, 'includes_initial_state': True},
        {'first': 128, 'last': 999, 'points': 872}]},
    ('FX2N', 'FX2NC'): {'class_binding': 'column_binding_unresolved', 'ranges': [
        {'first': 0, 'last': 499, 'points': 500, 'includes_initial_state': True},
        {'first': 500, 'last': 899, 'points': 400},
        {'first': 900, 'last': 999, 'points': 100}]},
    ('FX0S', 'FX0'): {'class_binding': 'column_binding_unresolved', 'ranges': [
        {'first': 0, 'last': 63, 'points': 64, 'includes_initial_state': True}]},
    ('FX0N',): {'class_binding': 'column_binding_unresolved', 'ranges': [
        {'first': 0, 'last': 127, 'points': 128, 'includes_initial_state': True}]},
    ('FXU', 'FX2C'): {'class_binding': 'established', 'ranges': [
        {'class': 'general', 'first': 0, 'last': 499, 'points': 500, 'includes_initial_state': True,
         'zero_return_members': 'S10-S19', 'latch': 'non_latched'},
        {'class': 'latched', 'first': 500, 'last': 899, 'points': 400, 'latch': 'battery_backed'}]},
}

CONTACTS = {
    'LD': {'contact_form': 'normally_open', 'connection': 'bus_start',
           'combination': 'result_assignment', 'operand_role': 'bus_start_operand', 'negation': False},
    'LDI': {'contact_form': 'normally_closed', 'connection': 'bus_start',
            'combination': 'result_assignment', 'operand_role': 'bus_start_operand', 'negation': True},
    'AND': {'contact_form': 'normally_open', 'connection': 'series_connection',
            'combination': 'operation_and', 'operand_role': 'preceding_result', 'negation': False},
    'ANI': {'contact_form': 'normally_closed', 'connection': 'series_connection',
            'combination': 'operation_and', 'operand_role': 'preceding_result', 'negation': True},
    'OR': {'contact_form': 'normally_open', 'connection': 'parallel_connection',
           'combination': 'operation_or', 'operand_role': 'preceding_result', 'negation': False},
    'ORI': {'contact_form': 'normally_closed', 'connection': 'parallel_connection',
            'combination': 'operation_or', 'operand_role': 'preceding_result', 'negation': True},
}

EDGES = {
    'LDP': {'edge': 'rising', 'connection': 'bus_start', 'from': False, 'to': True,
            'combination': 'result_assignment', 'operand_role': 'bus_start_operand'},
    'LDF': {'edge': 'falling', 'connection': 'bus_start', 'from': True, 'to': False,
            'combination': 'result_assignment', 'operand_role': 'bus_start_operand'},
    'ANDP': {'edge': 'rising', 'connection': 'series_connection', 'from': False, 'to': True,
             'combination': 'operation_and', 'operand_role': 'preceding_result'},
    'ANDF': {'edge': 'falling', 'connection': 'series_connection', 'from': True, 'to': False,
             'combination': 'operation_and', 'operand_role': 'preceding_result'},
    'ORP': {'edge': 'rising', 'connection': 'parallel_connection', 'from': False, 'to': True,
            'combination': 'operation_or', 'operand_role': 'preceding_result'},
    'ORF': {'edge': 'falling', 'connection': 'parallel_connection', 'from': True, 'to': False,
            'combination': 'operation_or', 'operand_role': 'preceding_result'},
}

EDGE_ST_FORMS = {'LDP': 'LDP(EN,s)', 'LDF': 'LDF(EN,s)', 'ANDP': 'ANDP(EN,s)',
                 'ANDF': 'ANDF(EN,s)', 'ORP': 'ORP(EN,s)', 'ORF': 'ORF(EN,s)'}

MEP_MEF = {'MEP': {'edge': 'rising', 'from': False, 'to': True},
           'MEF': {'edge': 'falling', 'from': True, 'to': False}}

PULSE = {'PLS': {'edge': 'rising', 'pulse_state': True},
         'PLF': {'edge': 'falling', 'pulse_state': False}}


def _page_list(row):
    pages = json.loads(row['source_pages_json'] or 'null') or [row['pdf_page']]
    return [int(page) for page in pages]


def _flat(text):
    return ' '.join((text or '').split())



# The original ladder manual states the ladder form of each basic family; the
# structured manual states the structured forms. Ladder-scoped facts therefore
# cite the ladder section as well, instead of borrowing the structured text alone.
LADDER_SECTIONS = {
    'contacts': {'manual_id': 'fx3_programming_r', 'revision': 'R',
                 'chunks': {199: '7.1 LD, LDI', 206: '7.3 AND, ANI', 207: '7.4 OR, ORI'}},
    'mep_mef': {'manual_id': 'fx3_programming_r', 'revision': 'R',
                'chunks': {224: '7.11 MEP, MEF'}},
    'set_rst': {'manual_id': 'fx3_programming_r', 'revision': 'R',
                'chunks': {228: '7.13 SET, RST', 231: '7.13 SET, RST'}},
    'pls_plf': {'manual_id': 'fx3_programming_r', 'revision': 'R',
                'chunks': {225: '7.12 PLS, PLF', 227: '7.12 PLS, PLF'}},
    'stack': {'manual_id': 'fx3_programming_r', 'revision': 'R',
              'chunks': {218: '7.8 MPS, MRD, MPP'}},
    'inv': {'manual_id': 'fx3_programming_r', 'revision': 'R',
            'chunks': {223: '7.10 INV'}},
    'nop': {'manual_id': 'fx3_programming_r', 'revision': 'R',
            'chunks': {232: '7.14 NOP'}},
    'end': {'manual_id': 'fx3_programming_r', 'revision': 'R',
            'chunks': {233: '7.15 END'}},
    'mc_mcr': {'manual_id': 'fx3_programming_r', 'revision': 'R',
               'chunks': {222: '7.9 MC, MCR'}},
}


def _manual_cpu_models(source):
    """CPU coverage of every registered manual; a source may not exceed it."""
    from knowledge.core import _scope_values
    coverage = {}
    for row in source.execute("SELECT manual_id, plc_models FROM manuals"):
        coverage[row['manual_id']] = set(_scope_values(row['plc_models']))
    for row in source.execute("SELECT DISTINCT manual_id, document_plc_models FROM chunks "
                              "WHERE document_plc_models IS NOT NULL AND document_plc_models != ''"):
        models = set(_scope_values(row['document_plc_models']))
        coverage[row['manual_id']] = coverage.get(row['manual_id'], set()) | models
    return coverage


def _enforce_source_cpu_scope(owner_facts, coverage, receipt, family):
    """A fact keeps only sources whose manual documents that CPU.

    A source that never covered the CPU is removed rather than generalised, and a
    fact left without any source is dropped so the gap stays visible.
    """
    dropped_sources = dropped_facts = 0
    for owner, rows in list(owner_facts.items()):
        model = owner[0]
        kept = []
        for row in rows:
            usable = [reference for reference in row['sources']
                      if model in coverage.get(reference['manual_id'], set())]
            removed = len(row['sources']) - len(usable)
            if removed:
                dropped_sources += removed
                receipt.setdefault('source_cpu_scope_excluded', []).append(
                    {'family': family, 'model': model, 'opcode': owner[1], 'fact_id': row['id'],
                     'excluded_manuals': sorted({reference['manual_id'] for reference in row['sources']
                                                 if reference not in usable})})
            if not usable:
                dropped_facts += 1
                continue
            row = dict(row, sources=usable)
            kept.append(row)
        if kept:
            owner_facts[owner] = kept
        else:
            owner_facts.pop(owner)
            receipt.setdefault('facts_dropped_without_covering_source', []).append(
                {'family': family, 'model': model, 'opcode': owner[1]})
    if dropped_sources or dropped_facts:
        receipt['source_scope_counts'] = {
            'excluded_source_references': dropped_sources, 'dropped_facts': dropped_facts}
    return owner_facts


def _ladder_evidence(source, family):
    """Ladder-manual references for a family; the heading must still be there."""
    spec = LADDER_SECTIONS[family]
    refs = []
    for chunk_id, section in spec['chunks'].items():
        row = source.execute('SELECT id,manual_id,revision,manual_number,pdf_page,source_pages_json,section,text '
                             'FROM chunks WHERE id=?', (chunk_id,)).fetchone()
        if row is None:
            raise ValueError('Ladder family evidence is missing: %s:%s' % (family, chunk_id))
        if row['manual_id'] != spec['manual_id'] or row['revision'] != spec['revision']:
            raise ValueError('Ladder family evidence moved: %s:%s' % (family, chunk_id))
        if (row['section'] or '').split('>')[-1].strip() != section:
            raise ValueError('Ladder family section moved: %s:%s' % (family, chunk_id))
        if _flat(section) not in _flat(row['text']):
            raise ValueError('Ladder family heading missing: %s:%s' % (family, chunk_id))
        refs.append({'id': str(row['id']), 'manual_id': row['manual_id'],
                     'manual_number': row['manual_number'], 'revision': row['revision'],
                     'pdf_pages': _page_list(row), 'section_role': 'ladder_manual_section'})
    return refs


def _evidence(source, sections, family):
    spec = sections[family]
    refs = {}
    for chunk_id, (section, witnesses) in (spec.get('ladder_chunks') or {}).items():
        row = source.execute('SELECT id,manual_id,revision,manual_number,pdf_page,source_pages_json,section,text '
                             'FROM chunks WHERE id=?', (chunk_id,)).fetchone()
        if row is None:
            raise ValueError('Behaviour review evidence is missing: %s:ladder:%s' % (family, chunk_id))
        ladder = spec['ladder_manual']
        if row['manual_id'] != ladder['manual_id'] or row['revision'] != ladder['revision'] or row['section'] != section:
            raise ValueError('Behaviour review evidence moved: %s:ladder:%s' % (family, chunk_id))
        if any(_flat(witness) not in _flat(row['text']) for witness in witnesses):
            raise ValueError('Behaviour review source changed: %s:ladder:%s' % (family, chunk_id))
        refs[chunk_id] = {'id': str(row['id']), 'manual_id': row['manual_id'],
                          'manual_number': row['manual_number'], 'revision': row['revision'],
                          'pdf_pages': _page_list(row), 'language_scope': 'ladder'}
    for manual_id, legacy in (spec.get('legacy_chunks') or {}).items():
        for chunk_id, (section, witnesses) in legacy['chunks'].items():
            row = source.execute('SELECT id,manual_id,revision,manual_number,pdf_page,source_pages_json,'
                                 'section,text FROM chunks WHERE id=?', (chunk_id,)).fetchone()
            if row is None or row['manual_id'] != manual_id:
                raise ValueError('Legacy family evidence is missing: %s:%s' % (family, chunk_id))
            if _flat(section) not in _flat(row['text']):
                raise ValueError('Legacy family section moved: %s:%s' % (family, chunk_id))
            if any(_flat(witness) not in _flat(row['text']) for witness in witnesses):
                raise ValueError('Legacy family statement changed: %s:%s' % (family, chunk_id))
            models = source.execute('SELECT plc_models FROM manuals WHERE manual_id=?', (manual_id,)).fetchone()
            from knowledge.core import _scope_values
            refs[chunk_id] = {'id': str(row['id']), 'manual_id': row['manual_id'],
                              'manual_number': row['manual_number'], 'revision': row['revision'],
                              'pdf_pages': _page_list(row), 'language_scope': 'ladder',
                              'manual_models': sorted(_scope_values(models[0])) if models else []}
    for chunk_id, (section, witnesses) in spec['chunks'].items():
        row = source.execute('SELECT id,manual_id,revision,manual_number,pdf_page,source_pages_json,section,text '
                             'FROM chunks WHERE id=?', (chunk_id,)).fetchone()
        if row is None:
            raise ValueError('Behaviour review evidence is missing: %s:%s' % (family, chunk_id))
        if row['manual_id'] != spec['manual_id'] or row['revision'] != spec['revision'] or row['section'] != section:
            raise ValueError('Behaviour review evidence moved: %s:%s' % (family, chunk_id))
        if any(_flat(witness) not in _flat(row['text']) for witness in witnesses):
            raise ValueError('Behaviour review source changed: %s:%s' % (family, chunk_id))
        refs[chunk_id] = {'id': str(row['id']), 'manual_id': row['manual_id'],
                          'manual_number': row['manual_number'], 'revision': row['revision'],
                          'pdf_pages': _page_list(row),
                          'language_scope': 'structured'}
    return refs


def model_states(source, chunk_id):
    """Read the documented CPU header; never infer support from a heading title."""
    row = source.execute('SELECT source_applicability_json FROM chunks WHERE id=?', (int(chunk_id),)).fetchone()
    header = json.loads(row[0] or '{}') if row is not None else {}
    if header.get('basis') != 'instruction_header' or not header.get('model_marks'):
        raise ValueError('Instruction header applicability has not been extracted for chunk %s' % chunk_id)
    states = {}
    for mark in header['model_marks']:
        if mark['state'] not in {'supported', 'conditional', 'unsupported'}:
            raise ValueError('Ambiguous CPU availability header')
        if mark['model'] in states:
            raise ValueError('Ambiguous CPU availability header')
        states[mark['model']] = mark['state']
    return states


def _refs(refs, chunk_ids):
    return [refs[int(chunk_id)] for chunk_id in chunk_ids]


def _fact(identity, dimension, value, opcode, sources, model, *, conditions=(), depends_on=(),
          languages=None, status='source_verified'):
    scope = {'models': [model], 'forms': [opcode]}
    if languages:
        scope['languages'] = list(languages)
    fact = {'id': identity, 'dimension': dimension, 'value': value, 'status': status,
            'verification': 'source_checked', 'sources': sources, 'scope': scope}
    if conditions:
        fact['conditions'] = list(conditions)
    if depends_on:
        fact['depends_on'] = list(depends_on)
    return fact


def _st_signature(identity, opcode, parameters, returns, documented_form, sources, model, *,
                  conditions=(), depends_on=()):
    value = {'parameters': [{'name': parameter['name'], 'type': parameter['type'],
                            'direction': parameter['direction']} for parameter in parameters],
             'returns': returns, 'enable': True, 'available': True,
             'documented_form': documented_form}
    return _fact(identity, 'parameters.st_signature', value, opcode, sources, model,
                 conditions=conditions, depends_on=depends_on, languages=['st'])


def _operand_contract(identity, opcode, operands, sources, model, language):
    """An explicitly reviewed source table, separate for each language shape."""
    return _fact(identity, 'parameters.typed_operands',
                 {'operands': operands, 'operand_count': len(operands),
                  'order_basis': 'documented_instruction_form'},
                 opcode, sources, model, languages=[language])


def _contacts(refs, states):
    owners = {}
    for model, state in states.items():
        if state != 'supported':
            continue
        for opcode, form in CONTACTS.items():
            facts = [
                _fact('authored:contact:role', 'parameters.connection_role',
                      {'contact_form': form['contact_form'], 'connection': form['connection'],
                       'operation_condition': 'continuous_not_edge', 'device_operand': 'bit'},
                      opcode, _refs(refs, (1559,)), model, languages=['ladder']),
                _fact('authored:contact:st_expression', 'parameters.language_expression',
                      {'st': 'assignment_expression', 'normally_closed': 'NOT_operand',
                       'operator_role': 'boolean_operator', 'expression_is_assignment_statement': True},
                      opcode, _refs(refs, (1559, 1560)), model, languages=['st']),
                _fact('authored:contact:ladder_expression', 'parameters.language_expression',
                      {'structured_ladder': 'contact', 'fbd': 'contact', 'ladder': 'contact'},
                      opcode, _refs(refs, (1559,)), model, languages=['ladder', 'fbd']),
                _fact('authored:contact:result_composition', 'effects.result_composition',
                      {'result_type': 'BOOL', 'combination': form['combination'],
                       'operand_role': form['operand_role'], 'negation': form['negation'],
                       'operation_condition': 'continuous'},
                      opcode, _refs(refs, (1559, 1560)), model, languages=['ladder'],
                      depends_on=['authored:contact:role']),
                _fact('authored:contact:connection_order', 'execution.connection_order',
                      {'default_anchor': 'preceding_LD_or_LDI',
                       'after_AND_block': 'second_preceding_LD_or_LDI',
                       'principle_applies_to_parallel_connection': True},
                      opcode, _refs(refs, (1561,)), model, languages=['ladder']),
            ]
            if model in {'FX3U', 'FX3UC'}:
                facts.extend([
                    _fact('authored:contact:indexing', 'constraints.indexing',
                          {'supported': True, 'models': ['FX3U', 'FX3UC'], 'index_registers': ['V', 'Z'],
                           'excluded_device_classes': ['state_S', 'special_M', '32bit_C', 'D_bit'],
                           'X_Y_index_conversion': 'octal'},
                          opcode, _refs(refs, (1561,)), model, languages=['ladder']),
                    _fact('authored:contact:bit_spec', 'parameters.device_form',
                          {'kind': 'data_register_bit', 'syntax': 'Dn.bit', 'bit_range': [0, 15],
                           'bit_number_radix': 16, 'register_width_bits': 16,
                           'wider_registers_permitted': False},
                          opcode, _refs(refs, (1561,)), model, languages=['ladder']),
                    _fact('authored:contact:indexing_errors', 'constraints.error_response',
                          {'nonexistent_IO_due_to_indexing': 'M8316_ON',
                           'nonexistent_M_T_C_due_to_indexing': 'operation_error_6706'},
                          opcode, _refs(refs, (1561,)), model, languages=['ladder']),
                ])
            owners[(model, opcode)] = facts
    return owners


def _legacy_edge_facts(opcode, form, model, refs):
    """Ladder facts a legacy FX manual documents for this CPU, and nothing more."""
    roles = SECTIONS['edges']['legacy_roles']
    mnemonics = SECTIONS['edges']['legacy_mnemonics']
    facts = []
    for chunk_id, reference in sorted(refs.items()):
        if 'manual_models' not in reference or model not in reference['manual_models']:
            continue
        role = roles.get(int(chunk_id))
        if not role:
            continue
        facts.append(_fact('authored:edge:legacy_ladder_form@' + reference['manual_id'],
                           'parameters.documented_form',
                           {'documented_mnemonic': mnemonics[opcode], 'edge': form['edge'],
                            'connection': role, 'program_steps': 2,
                            'documented_devices': 'X,Y,M,S,T,C', 'document_language': 'ladder'},
                           opcode, [reference], model, languages=['ladder']))
        facts.append(_fact('authored:edge:legacy_repeat_contexts@' + reference['manual_id'],
                           'execution.same_scan_repeat',
                           {'state': 'unknown', 'basis': 'not_stated_in_this_manual',
                            'searched_contexts': ['FOR_NEXT_repeat', 'repeated_CALL_of_same_subroutine',
                                                  'CJ_jump_region'],
                            'searched_manuals': [reference['manual_id']]},
                           opcode, [reference], model, languages=['ladder'],
                           status='unknown',
                           depends_on=['authored:edge:legacy_ladder_form@' + reference['manual_id']]))
    return facts


def _edges(refs, states):
    """Grouped ownership: every symbol of section 5.2 owns the shared section."""
    owners = {}
    for model, state in states.items():
        for opcode, form in EDGES.items():
            rising = form['edge'] == 'rising'
            if state == 'unsupported':
                # The structured header excludes this CPU; only the legacy manual
                # text that documents it may speak for it.
                legacy_only = _legacy_edge_facts(opcode, form, model, refs)
                if legacy_only:
                    owners[(model, opcode)] = legacy_only
                continue
            facts = [
                _fact('authored:edge:direction', 'execution.edge',
                      {'edge': form['edge'], 'source': 'operand_bit',
                       'transition': {'from': form['from'], 'to': form['to']},
                       'active_cycles': 1, 'cycle_unit': 'operation_cycle',
                       'active_when': 'transition_detected', 'inactive_when': 'no_transition'},
                      opcode, _refs(refs, (210,)), model, languages=['ladder']),
                _fact('authored:edge:result_composition', 'effects.result_composition',
                      {'result_type': 'BOOL', 'combination': form['combination'],
                       'operand_role': form['operand_role'],
                       'edge_term': 'transition_flag_of_operand_bit',
                       'inactive_term_value': False},
                      opcode, _refs(refs, (210, 212)), model, languages=['ladder'],
                      depends_on=['authored:edge:direction']),
                _fact('authored:edge:connection', 'parameters.connection_role',
                      {'contact_form': 'normally_open', 'connection': form['connection'],
                       'operation_condition': 'pulse_single_operation_cycle', 'device_operand': 'bit'},
                      opcode, _refs(refs, (1562,)), model, languages=['fbd', 'st']),
                _fact('authored:edge:fbd_direction', 'execution.edge',
                      {'edge': form['edge'], 'source': 'operand_bit',
                       'transition': {'from': form['from'], 'to': form['to']},
                       'active_cycles': 1, 'cycle_unit': 'operation_cycle',
                       'execution_form': 'fbd_block', 'documented_for': 'fbd'},
                      opcode, _refs(refs, (1562,)), model, languages=['fbd']),
                _fact('authored:edge:st_operation', 'execution.edge',
                      {'active_when': 'transition_detected', 'execution_form': 'st_call',
                       'operation_condition': 'pulse_single_operation_cycle',
                       'documented_call': EDGE_ST_FORMS[opcode]},
                      opcode, _refs(refs, (1562,)), model, languages=['st']),
                _fact('authored:edge:repeat_execution', 'execution.same_scan_repeat',
                      {'first_execution_within_operation_cycle': 'active_on_transition',
                       'subsequent_execution': 'inactive_when_device_status_equals_status_at_last_execution',
                       'comparison_basis': 'device_status_at_previous_execution_of_this_instruction',
                       'state_owner': 'per_instruction_execution',
                       'repeating_contexts': ['FOR_NEXT', 'repeated_CALL_of_same_subroutine',
                                              'CJ_jump_to_smaller_ladder_block'],
                       'not_a_previous_scan_comparison': True,
                       'documented_for': ['ladder']},
                      opcode, _refs(refs, (215,)), model, languages=['ladder'],
                      depends_on=['authored:edge:direction']),
                _fact('authored:edge:fbd_repeat_execution', 'execution.same_scan_repeat',
                      {'first_execution_within_operation_cycle': 'active_on_transition',
                       'subsequent_execution': 'inactive_when_device_status_equals_status_at_last_execution',
                       'comparison_basis': 'device_status_at_previous_execution_of_this_instruction',
                       'repeating_contexts': ['FOR_NEXT', 'same_subroutine_from_two_or_more_CALL',
                                              'CJ_jump_to_smaller_ladder_block'],
                       'documented_for': ['fbd'], 'language_form_of': 'fbd_direction'},
                      opcode, _refs(refs, (1565,)), model, languages=['fbd']),
                _fact('authored:edge:st_repeat_execution', 'execution.same_scan_repeat',
                      {'first_execution_within_operation_cycle': 'active_on_transition',
                       'subsequent_execution': 'inactive_when_device_status_equals_status_at_last_execution',
                       'comparison_basis': 'device_status_at_previous_execution_of_this_instruction',
                       'repeating_contexts': ['FOR_NEXT', 'same_subroutine_from_two_or_more_CALL',
                                              'CJ_jump_to_smaller_ladder_block'],
                       'documented_for': ['st'], 'language_form_of': 'st_operation'},
                      opcode, _refs(refs, (1565,)), model, languages=['st']),
                _fact('authored:edge:st_effect', 'effects.operation_result',
                      {'result': 'active_for_one_operation_cycle_at_transition',
                       'result_owner': 'instruction_operation_result', 'writes_device': False,
                       'documented_for': ['st'], 'language_form_of': 'st_operation',
                       'comparison_basis': 'device_status_at_previous_execution_of_this_instruction'},
                      opcode, _refs(refs, (1564,)), model, languages=['st']),
                _fact('authored:edge:fbd_effect', 'effects.operation_result',
                      {'result': 'active_for_one_operation_cycle_at_transition',
                       'result_owner': 'instruction_operation_result', 'writes_device': False,
                       'documented_for': ['fbd'], 'language_form_of': 'fbd_direction',
                       'comparison_basis': 'device_status_at_previous_execution_of_this_instruction'},
                      opcode, _refs(refs, (1564,)), model, languages=['fbd']),
                _fact('authored:edge:first_execution', 'execution.first_execution',
                      {'state': 'unknown',
                       'question_scope': 'first_execution_after_cpu_start_or_first_RUN',
                       'not_the_same_as': 'first_execution_within_operation_cycle',
                       'searched_contexts': ['STOP_to_RUN_initial_processing', 'RUN_first_scan',
                                             'CJ_jump_region', 'MC_disabled_zone', 'FOR_NEXT_repeat',
                                             'instruction_execution_from_come_from'],
                       'documented_languages': ['ladder', 'fbd'],
                       'basis': 'section_states_repeat_contexts_only'},
                      opcode, _refs(refs, (215,)), model, languages=['ladder'],
                      status='unknown', depends_on=['authored:edge:repeat_execution']),
                _fact('authored:edge:jump_effect', 'execution.skipped_execution',
                      {'state': 'unknown',
                       'unstored_question': 'whether_stored_status_updates_when_instruction_is_skipped',
                       'contexts': ['CJ_jump', 'MC_MCR_zone', 'interlock_off'], 'basis': 'not_stated',
                       'searched_contexts': ['CJ_jump_region', 'MC_disabled_zone',
                                             'STOP_to_RUN_initial_processing', 'RUN_first_scan'],
                       'searched_manuals': ['fx3_programming_r', 'fxcpu_basic_applied_m',
                                            'jy992d88101e', 'jy992d48301j'],
                       'nearest_documented': {'CJ_section': 'steps_between_CJ_and_pointer_not_executed',
                                              'MC_section': 'MC_zone_executed_only_while_ON'}},
                      opcode, _refs(refs, (215,)), model, languages=['ladder'],
                      status='unknown', depends_on=['authored:edge:repeat_execution']),
                _fact('authored:edge:disable_effect', 'execution.disabled_execution',
                      {'state': 'unknown',
                       'unstored_question': 'whether_disabled_instruction_keeps_or_clears_stored_status',
                       'basis': 'not_stated',
                       'searched_contexts': ['CJ_jump_region', 'MC_disabled_zone',
                                             'STOP_to_RUN_initial_processing', 'RUN_first_scan'],
                       'searched_manuals': ['fx3_programming_r', 'fxcpu_basic_applied_m',
                                            'jy992d88101e', 'jy992d48301j'],
                       'nearest_documented': {'MC_section': 'disabled_MC_zone_executes_with_contact_OFF',
                                              'interlock': 'no_edge_stored_status_statement'}},
                      opcode, _refs(refs, (215,)), model, languages=['ladder'],
                      status='unknown', depends_on=['authored:edge:repeat_execution']),
                _fact('authored:edge:same_scan_change', 'execution.same_scan_change',
                      {'state': 'unknown',
                       'unstored_question': 'device_change_between_two_executions_in_one_scan',
                       'basis': 'not_stated',
                       'searched_contexts': ['CJ_jump_region', 'MC_disabled_zone',
                                             'STOP_to_RUN_initial_processing', 'RUN_first_scan'],
                       'searched_manuals': ['fx3_programming_r', 'fxcpu_basic_applied_m',
                                            'jy992d88101e', 'jy992d48301j'],
                       'nearest_documented': {'5_2': 'compares_device_status_at_last_execution'}},
                      opcode, _refs(refs, (215,)), model, languages=['ladder'],
                      status='unknown', depends_on=['authored:edge:repeat_execution']),
                _fact('authored:edge:st_expression', 'parameters.language_expression',
                      {'st': 'boolean_function_call', 'left_operand': 'preceding_boolean_expression',
                       'bus_start_left_operand': 'TRUE', 'returns': 'BOOL',
                       'documented_form': EDGE_ST_FORMS[opcode]},
                      opcode, _refs(refs, (1562,)), model, languages=['st'],
                      depends_on=['authored:edge:st_operation']),
                _fact('authored:edge:fbd_expression', 'parameters.language_expression',
                      {'structured_ladder': 'EN_ENO_block', 'fbd': 'EN_ENO_block',
                       'documented_form': EDGE_ST_FORMS[opcode]},
                      opcode, _refs(refs, (1562,)), model, languages=['fbd'],
                      depends_on=['authored:edge:fbd_direction']),
                _fact('authored:edge:bit_spec', 'parameters.device_form',
                      {'kind': 'data_register_bit', 'syntax': 'Dn.bit', 'bit_range': [0, 15],
                       'bit_number_radix': 16, 'register_width_bits': 16,
                       'wider_registers_permitted': False, 'bit_spec_models': ['FX3U', 'FX3UC']},
                      opcode, _refs(refs, (212,)), model, languages=['ladder']),
            ]
            # Legacy FX manuals state the same symbols with their own table text;
            # they never inherit the FX3 manual's repeat-context statements.
            facts.extend(_legacy_edge_facts(opcode, form, model, refs))
            if rising:
                facts.append(_fact('authored:edge:online_edit', 'execution.online_edit',
                    {'event': 'write_during_RUN_completed',
                     'contact_status': {'OFF': 'not_executed', 'ON': 'executed'},
                     'applies_to': ['LDP', 'ANDP', 'ORP', 'pulse_operation_type_applied_instructions'],
                     'PLS_exception': 'not_executed'},
                    opcode, _refs(refs, (215,)), model, languages=['ladder'],
                    depends_on=['authored:edge:direction']))
            else:
                facts.append(_fact('authored:edge:online_edit', 'execution.online_edit',
                    {'event': 'write_during_RUN_completed',
                     'contact_status': {'OFF': 'not_executed', 'ON': 'not_executed'},
                     'rearm_required': {'set_once': True, 'then_off': True},
                     'applies_to': ['LDF', 'ANDF', 'ORF', 'PLF']},
                    opcode, _refs(refs, (215,)), model, languages=['ladder'],
                    depends_on=['authored:edge:direction']))
            if model not in {'FX1S', 'FX1N', 'FX1NC'}:
                facts.append(_fact('authored:edge:device_scope', 'execution.detection_scope',
                    {'device_family': 'M',
                     'all_contacts_detect_ranges': ['M0-M2799', 'M3072-M7679'],
                     'model_range_overrides': {'FX2N': 'M0-M2799', 'FX2NC': 'M0-M2799', 'FX3S': 'M0-M1535'},
                     'block_edge_range': 'M2800-M3071', 'block_division_device': 'M2800',
                     'block_rule': 'only_first_edge_contact_in_each_block_A_and_B',
                     'plain_LD_contact_conducts_while_device_ON': True},
                    opcode, _refs(refs, (214,)), model, languages=['ladder']))
            # The grouped section documents one call shape for all six symbols;
            # each symbol carries its own documented form.
            if True:
                facts.extend([
                    _st_signature('authored:edge:st_signature', opcode,
                        [{'name': 'EN', 'type': 'BOOL', 'direction': 'input'},
                         {'name': 's', 'type': 'BOOL', 'direction': 'input'}], 'BOOL',
                        EDGE_ST_FORMS[opcode], _refs(refs, (1562,)), model,
                        depends_on=['authored:edge:st_operation']),
                    _fact('authored:edge:st_call_scope', 'identity.call_scope',
                        {'source_documented_call': True, 'executor_capability': 'not_established',
                         'admission_effect': 'none', 'st_signature_is_not_fbd_semantics': True},
                        opcode, _refs(refs, (1562,)), model, languages=['st'],
                        depends_on=['authored:edge:st_signature']),
                ])
            facts.extend([
                _operand_contract('authored:edge:ladder_operands', opcode,
                    [{'name': 'S', 'data_type': 'BOOL', 'direction': 'input', 'role': 'edge_source'}],
                    _refs(refs, (210, 212)), model, 'ladder'),
                _operand_contract('authored:edge:st_operands', opcode,
                    [{'name': 'EN', 'data_type': 'BOOL', 'direction': 'input', 'role': 'execution_condition'},
                     {'name': 's', 'data_type': 'BOOL', 'direction': 'input', 'role': 'edge_source'}],
                    _refs(refs, (1562,)), model, 'st'),
                _fact('authored:edge:fbd_signature', 'parameters.fbd_signature',
                    {'parameters': [
                        {'name': 'EN', 'type': 'BOOL', 'direction': 'input', 'role': 'execution_condition'},
                        {'name': 's', 'type': 'BOOL', 'direction': 'input', 'role': 'edge_source'},
                        {'name': 'ENO', 'type': 'BOOL', 'direction': 'output', 'role': 'execution_state'}],
                     'available': True, 'enable': True, 'returns': 'BOOL'},
                    opcode, _refs(refs, (1562,)), model, languages=['fbd']),
                _operand_contract('authored:edge:fbd_operands', opcode,
                    [{'name': 'EN', 'data_type': 'BOOL', 'direction': 'input', 'role': 'execution_condition'},
                     {'name': 's', 'data_type': 'BOOL', 'direction': 'input', 'role': 'edge_source'},
                     {'name': 'ENO', 'data_type': 'BOOL', 'direction': 'output', 'role': 'execution_state'}],
                    _refs(refs, (1562,)), model, 'fbd'),
            ])
            if opcode in {'LDP', 'LDF'}:
                facts.append(_fact('authored:edge:initial_enable', 'constraints.enable',
                    {'parameter': 'EN', 'required_value': True},
                    opcode, _refs(refs, (1562,)), model, languages=['st', 'fbd']))
            owners[(model, opcode)] = facts
    return owners


def _mep(refs, states):
    owners = {}
    for model, state in states.items():
        if state not in {'supported', 'conditional'}:
            continue
        conditions = ([{'context': 'firmware', 'minimum': '2.30', 'families': ['FX3U', 'FX3UC']}]
                      if model in {'FX3U', 'FX3UC'} else [])
        for opcode, form in MEP_MEF.items():
            online = ({'operation_result_OFF': 'inactive', 'operation_result_ON': 'active'}
                      if opcode == 'MEP' else
                      {'operation_result_OFF': 'inactive', 'operation_result_ON': 'inactive',
                       'activation': 'when_operation_result_turns_off'})
            owners[(model, opcode)] = [
                _fact('authored:mep_mef:edge', 'execution.edge',
                      {'edge': form['edge'], 'source': 'operation_result', 'device_operand': 'none',
                       'transition': {'from': form['from'], 'to': form['to']},
                       'active_cycles': 1, 'cycle_unit': 'operation_cycle',
                       'active_when': 'operation_result_transition'},
                      opcode, _refs(refs, (1580,)), model, conditions=conditions),
                _fact('authored:mep_mef:result_composition', 'effects.result_composition',
                      {'result_type': 'BOOL', 'combination': 'operation_and',
                       'operand_role': 'preceding_operation_result',
                       'edge_term': 'operation_result_transition_flag'},
                      opcode, _refs(refs, (1580,)), model, conditions=conditions,
                      depends_on=['authored:mep_mef:edge']),
                _fact('authored:mep_mef:position', 'parameters.position',
                      {'list_program_usage': 'AND', 'allowed_as_LD': False, 'allowed_as_OR': False,
                       'uses_immediately_preceding_operation_result': True},
                      opcode, _refs(refs, (1580,)), model, conditions=conditions,
                      depends_on=['authored:mep_mef:edge']),
                _fact('authored:mep_mef:firmware', 'constraints.firmware_availability',
                      {'families': ['FX3U', 'FX3UC'], 'minimum_firmware': '2.30'},
                      opcode, _refs(refs, (1580,)), model),
                _fact('authored:mep_mef:indexed_contact_caution', 'execution.precondition',
                      {'condition': 'indexed_contact_pulse_conversion_in_subroutine_or_FOR_NEXT',
                       'effect': 'normal_operation_not_guaranteed'},
                      opcode, _refs(refs, (1580,)), model,
                      conditions=[{'context': 'indexed_contact', 'modified_by': ['subroutine', 'FOR_NEXT']}]),
                _fact('authored:mep_mef:online_edit', 'execution.online_edit',
                      {'event': 'write_during_RUN_completed', **online},
                      opcode, _refs(refs, (1580,)), model, conditions=conditions,
                      depends_on=['authored:mep_mef:edge']),
                _fact('authored:mep_mef:st_expression', 'parameters.language_expression',
                      {'st': 'boolean_function_call', 'argument': 'EN', 'separate_operand': False,
                       'documented_form': opcode + '(EN)'},
                      opcode, _refs(refs, (1580,)), model, conditions=conditions, languages=['st']),
            ]
    return owners


def _set_rst(refs, states):
    owners = {}
    for model, state in states.items():
        if state != 'supported':
            continue
        def shared(opcode):
            # Section 5.10 documents one shared rule set; each owning symbol
            # carries it scoped to its own form.
            data_type = 'BOOL' if opcode == 'SET' else 'ANY_SIMPLE'
            structured_operands = [
                {'name': 'EN', 'data_type': 'BOOL', 'direction': 'input', 'role': 'execution_condition'},
                {'name': 'd', 'data_type': data_type, 'direction': 'output', 'role': 'modified_device'}]
            return [
                _operand_contract('authored:set_rst:ladder_operands', opcode,
                    [{'name': 'D', 'data_type': data_type, 'direction': 'output', 'role': 'modified_device'}],
                    _refs(refs, (1581,)), model, 'ladder'),
                _operand_contract('authored:set_rst:st_operands', opcode,
                    structured_operands, _refs(refs, (1581,)), model, 'st'),
                _operand_contract('authored:set_rst:fbd_operands', opcode,
                    [*structured_operands, {'name': 'ENO', 'data_type': 'BOOL',
                                           'direction': 'output', 'role': 'execution_state'}],
                    _refs(refs, (1581,)), model, 'fbd'),
                _fact('authored:set_rst:fbd_signature', 'parameters.fbd_signature',
                    {'parameters': [
                        {'name': 'EN', 'type': 'BOOL', 'direction': 'input'},
                        {'name': 'd', 'type': data_type, 'direction': 'output'},
                        {'name': 'ENO', 'type': 'BOOL', 'direction': 'output'}],
                     'available': True, 'enable': True, 'returns': 'BOOL'},
                    opcode, _refs(refs, (1581,)), model, languages=['fbd']),
                _fact('authored:set_rst:indexing', 'constraints.indexing',
                      {'supported': True, 'models': ['FX3U', 'FX3UC'], 'index_registers': ['V', 'Z'],
                       'excluded_device_classes': ['state_S', 'special_M', '32bit_C', 'D_bit', 'word_devices'],
                       'X_Y_index_conversion': 'octal'},
                      opcode, _refs(refs, (1583,)), model),
                _fact('authored:set_rst:bit_spec', 'parameters.device_form',
                      {'kind': 'data_register_bit', 'syntax': 'Dn.bit', 'bit_range': [0, 15],
                       'bit_number_radix': 16, 'register_width_bits': 16,
                       'wider_registers_permitted': False, 'bit_spec_models': ['FX3U', 'FX3UC']},
                      opcode, _refs(refs, (1583,)), model),
                _fact('authored:set_rst:indexing_errors', 'constraints.error_response',
                      {'nonexistent_IO_due_to_indexing': 'M8316_ON',
                       'nonexistent_M_T_C_due_to_indexing': 'operation_error_6706'},
                      opcode, _refs(refs, (1583,)), model),
            ]
        owners[(model, 'SET')] = [*shared('SET'),
            _st_signature('authored:set:st_signature', 'SET',
                [{'name': 'EN', 'type': 'BOOL', 'direction': 'input'},
                 {'name': 'd', 'type': 'BOOL', 'direction': 'output'}], 'BOOL', 'SET(EN,d)',
                _refs(refs, (1581, 1583)), model),
            _fact('authored:set:retention', 'effects.retention',
                  {'device_classes': ['Y', 'M', 'S', 'D_bit'], 'written_state': True,
                   'retained_after_command_off': True, 'driver': 'SET'},
                  'SET', _refs(refs, (1581,)), model),
            _fact('authored:set:timing_state', 'execution.timing_state',
                  {'state_owner': 'bit_device_operand', 'driven_by': 'SET',
                   'update_timing': 'when_command_input_turns_ON',
                   'previous_value': 'retained_when_command_input_turns_OFF',
                   'not_a_previous_scan_comparison': True},
                  'SET', _refs(refs, (1581, 1583)), model),
            _fact('authored:set:parallel_use', 'execution.parallel_use',
                  {'parallel_SET_allowed': True, 'repeated_times': 'unlimited',
                   'order': 'arbitrary', 'additional_RST_may_follow': True},
                  'SET', _refs(refs, (1582,)), model),
            _fact('authored:set:same_end_priority', 'execution.same_scan_priority',
                  {'when': 'SET_and_RST_executed_for_same_Y_in_same_operation',
                   'result': 'instruction_nearest_END_is_output', 'END_defines_program_end': True},
                  'SET', _refs(refs, (1583,)), model),
        ]
        rst = [*shared('RST'),
            _st_signature('authored:rst:st_signature', 'RST',
                [{'name': 'EN', 'type': 'BOOL', 'direction': 'input'},
                 {'name': 'd', 'type': 'ANY_SIMPLE', 'direction': 'output'}], 'BOOL', 'RST(EN,d)',
                _refs(refs, (1581, 1583)), model),
            _fact('authored:rst:bit_reset', 'effects.reset_bit',
                  {'device_classes': ['Y', 'M', 'S', 'T', 'C', 'D_bit'], 'written_state': False,
                   'driver': 'RST'},
                  'RST', _refs(refs, (1582, 1583)), model),
            _fact('authored:rst:word_reset', 'effects.reset_word',
                  {'device_classes': ['T', 'C', 'D', 'R', 'V', 'Z'], 'written_value': 0,
                   'counter_output_contact': 'reset', 'retentive_current_value_reset': True},
                  'RST', _refs(refs, (1582, 1583)), model),
            _fact('authored:rst:timing_state', 'execution.timing_state',
                  {'state_owner': 'device_operand', 'driven_by': 'RST',
                   'update_timing': 'when_command_input_turns_ON',
                   'previous_value': 'unchanged_when_command_input_turns_OFF',
                   'not_a_previous_scan_comparison': True},
                  'RST', _refs(refs, (1581, 1583)), model),
            _fact('authored:rst:same_end_priority', 'execution.same_scan_priority',
                  {'when': 'SET_and_RST_executed_for_same_Y_in_same_operation',
                   'result': 'instruction_nearest_END_is_output', 'END_defines_program_end': True},
                  'RST', _refs(refs, (1583,)), model),
            _fact('authored:rst:non_main_program', 'execution.non_main_program',
                  {'contexts': ['jumped_program', 'subroutine_program', 'interrupt_program'],
                   'effect': 'timer_or_counter_may_stay_reset_and_be_disabled', 'deterministic': False},
                  'RST', _refs(refs, (1582,)), model, depends_on=['authored:rst:word_reset']),
        ]
        if model in {'FX1N', 'FX1NC', 'FX2N', 'FX2NC'}:
            rst.append(_fact('authored:rst:retentive_timer_circuit', 'constraints.context',
                {'rule': 'retentive_timer_requires_reset_program',
                 'context': 'retentive_timer_used_in_program',
                 'requirement': 'RST_circuit_present',
                 'without_requirement': 'timers_may_remain_reset_and_not_operate'},
                'RST', _refs(refs, (1583,)), model, conditions=[{'context': 'retentive_timer'}],
                depends_on=['authored:rst:word_reset']))
        owners[(model, 'RST')] = rst
    return owners


def _pulse(refs, states):
    owners = {}
    for model, state in states.items():
        if state != 'supported':
            continue
        for opcode, form in PULSE.items():
            facts = [
                _st_signature('authored:pls_plf:st_signature', opcode,
                    [{'name': 'EN', 'type': 'BOOL', 'direction': 'input'},
                     {'name': 'd', 'type': 'BOOL', 'direction': 'input'}], 'BOOL',
                    opcode + '(EN,d)', _refs(refs, (1585,)), model),
                _fact('authored:pls_plf:pulse_write', 'effects.pulse_write',
                      {'device': 'operand_bit', 'active_cycles': 1, 'cycle_unit': 'operation_cycle',
                       'pulse_state': form['pulse_state'], 'edge': form['edge'],
                       'trigger': 'drive_input_transition'},
                      opcode, _refs(refs, (1585,)), model,
                      depends_on=['authored:pls_plf:st_signature']),
                _fact('authored:pls_plf:mode_change', 'execution.mode_change',
                      {'event': 'RUN_to_STOP_to_RUN_with_drive_input_ON',
                       'non_latched_device': 'pulse_emitted', 'battery_backed_device': 'no_pulse',
                       'reason': 'device_status_latched_through_STOP', 'example_devices': ['M0', 'M600']},
                      opcode, _refs(refs, (1585,)), model, depends_on=['authored:pls_plf:pulse_write']),
                _fact('authored:pls_plf:online_edit', 'execution.online_edit',
                      {'event': 'write_during_RUN_completed',
                       'rising_edge_pulse': {'contact_OFF': 'not_executed', 'contact_ON': 'executed'},
                       'PLS_exception': 'not_executed' if opcode == 'PLS' else 'not_applicable',
                       'falling_edge_pulse': {'contact_OFF': 'not_executed', 'contact_ON': 'not_executed'},
                       'rearm_required': {'set_once': True, 'then_off': True}},
                      opcode, _refs(refs, (1585,)), model,
                      depends_on=['authored:pls_plf:pulse_write']),
                _fact('authored:pls_plf:output_drive_equivalence', 'effects.output_equivalence',
                      {'equivalent_circuits': ['LDP_contact_with_OUT', 'PLS_instruction'],
                       'pulse_operation_applied_equivalent': 'MOVP',
                       'same_operation_cycle_output': True},
                      opcode, _refs(refs, (1585,)), model),
            ]
            owners[(model, opcode)] = facts
    return owners


def _master_control(refs, states):
    owners = {}
    for model, state in states.items():
        if state != 'supported':
            continue
        owners[(model, 'MC')] = [
            _st_signature('authored:mc_mcr:st_signature', 'MC',
                [{'name': 'EN', 'type': 'BOOL', 'direction': 'input'},
                 {'name': 'n', 'type': 'ANY16', 'direction': 'input'},
                 {'name': 'd', 'type': 'BOOL', 'direction': 'input'}], 'BOOL', 'MC(EN,n,d)',
                _refs(refs, (1587, 1589)), model),
            _fact('authored:mc:enable', 'execution.enable',
                  {'executed_range': 'MC_to_MCR', 'when_MC_executed': 'body_executed',
                   'when_MC_not_executed': 'contact_off_operation'},
                  'MC', _refs(refs, (1587, 1588)), model,
                  depends_on=['authored:mc_mcr:st_signature']),
            _fact('authored:mc:disabled_retention', 'effects.disabled_retention',
                  {'turned_off': ['non_retentive_timers', 'OUT_driven_devices'],
                   'held': ['retentive_timers', 'counters', 'SET_RST_driven_devices']},
                  'MC', _refs(refs, (1588,)), model, depends_on=['authored:mc:enable']),
            _fact('authored:mc:structure', 'parameters.structure',
                  {'nesting_levels': [0, 7], 'pairing': 'same_level_MC_MCR_pair',
                   'MCR_condition': 'always_TRUE', 'contact_before_MCR_allowed': False,
                   'unnested_level': 0, 'same_level_reuse': 'unlimited_with_different_device',
                   'nested_increase': '0_to_7', 'return_order': 'highest_first',
                   'skipped_level_returns_at_once': True},
                  'MC', _refs(refs, (1588, 1589)), model),
            _fact('authored:mc:enabled_device', 'execution.mc_device',
                  {'device_ON_while_MC_executed': True, 'double_coil_conflict_possible': True},
                  'MC', _refs(refs, (1588,)), model),
        ]
        owners[(model, 'MCR')] = [
            _st_signature('authored:mc_mcr:st_signature', 'MCR',
                [{'name': 'EN', 'type': 'BOOL', 'direction': 'input'},
                 {'name': 'n', 'type': 'ANY16', 'direction': 'input'}], 'BOOL', 'MCR(EN,n)',
                _refs(refs, (1587, 1589)), model),
            _fact('authored:mcr:structure', 'parameters.structure',
                  {'pairing': 'same_level_MC_MCR_pair', 'condition': 'always_TRUE',
                   'contact_before_MCR_allowed': False, 'nesting_levels': [0, 7],
                   'return_order': 'highest_first', 'skipped_level_returns_at_once': True,
                   'no_applicable_devices': True},
                  'MCR', _refs(refs, (1587, 1588, 1589)), model,
                  depends_on=['authored:mc_mcr:st_signature']),
        ]
    return owners


def _and_or(refs, states):
    owners = {}
    rules = {'AND(...)': {'connection': 'series_connection_of_parallel_block',
                          'operand_form': 'parallel_circuit_block',
                          'left_operand': 'preceding_circuit_result'},
             'OR(...)': {'connection': 'parallel_connection_of_series_block',
                         'operand_form': 'series_circuit_block',
                         'left_operand': 'preceding_circuit_result'}}
    for model, state in states.items():
        if state != 'supported':
            continue
        for opcode, rule in rules.items():
            owners[(model, opcode)] = [
                _fact('authored:block:connection_role', 'parameters.connection_role',
                      {'contact_form': 'composed_circuit_block', **rule,
                       'operation_condition': 'continuous_not_edge', 'device_operand': 'none'},
                      opcode, _refs(refs, (1575,)), model),
                _fact('authored:block:result_composition', 'effects.result_composition',
                      {'result_type': 'BOOL',
                       'combination': 'operation_and' if opcode == 'AND(...)' else 'operation_or',
                       'operand_role': 'composed_block_result',
                       'block_internal_combination': 'operation_or' if opcode == 'AND(...)' else 'operation_and',
                       'operation_condition': 'continuous'},
                      opcode, _refs(refs, (1575,)), model,
                      depends_on=['authored:block:connection_role']),
                _fact('authored:block:st_expression', 'parameters.language_expression',
                      {'st': 'boolean_operator_block', 'block_operand': 'parenthesized_expression',
                       'no_applicable_devices': True},
                      opcode, _refs(refs, (1575,)), model, languages=['st'],
                      depends_on=['authored:block:result_composition']),
                _fact('authored:block:fbd_expression', 'parameters.language_expression',
                      {'structured_ladder': 'circuit_block', 'fbd': 'circuit_block', 'ladder': 'circuit_block'},
                      opcode, _refs(refs, (1575,)), model, languages=['ladder', 'fbd']),
                _fact('authored:block:operand_scope', 'parameters.device_form',
                      {'applicable_devices': 'none', 'operand_is_composed_circuit': True},
                      opcode, _refs(refs, (1575,)), model),
            ]
    return owners


def _stack(refs, states):
    owners = {}
    for model, state in states.items():
        if state != 'supported':
            continue
        for opcode, operation in (('MPS', 'push'), ('MRD', 'read_without_remove'), ('MPP', 'pop')):
            owners[(model, opcode)] = [
                _fact('authored:stack:storage', 'memory.stack',
                      {'stack_depth': 11, 'stored_value': 'intermediate_operation_result',
                       'stored_type': 'BOOL', 'operation': operation,
                       'operation_order_required': ['MPS', 'MRD', 'MPP']},
                      opcode, _refs(refs, (1576,)), model),
                _fact('authored:stack:st_signature', 'parameters.st_signature',
                      {'parameters': [{'name': 'EN', 'type': 'BOOL', 'direction': 'input'}],
                       'returns': 'BOOL', 'enable': True, 'documented_form': opcode + '(EN)',
                       'EN_condition': 'bit' if opcode == 'MPS' else 'always_TRUE'},
                      opcode, _refs(refs, (1576,)), model, languages=['st'],
                      depends_on=['authored:stack:storage']),
                _fact('authored:stack:operand_scope', 'parameters.device_form',
                      {'applicable_devices': 'none', 'operand_is_operation_result': True},
                      opcode, _refs(refs, (1576,)), model),
            ]
    return owners


def _invert(refs, states):
    owners = {}
    for model, state in states.items():
        if state != 'supported':
            continue
        owners[(model, 'INV')] = [
            _fact('authored:inv:result_composition', 'effects.result_composition',
                  {'result_type': 'BOOL', 'combination': 'operation_not',
                   'operand_role': 'preceding_result', 'operation_condition': 'continuous',
                   'inverted_range': 'all_operations_up_to_just_before_INV'},
                  'INV', _refs(refs, (1578,)), model),
            _fact('authored:inv:position', 'parameters.position',
                  {'allowed_in_series_position': True, 'allowed_as_bus_start': False,
                   'allowed_alone_in_parallel': False,
                   'same_position_as': ['AND', 'ANI', 'ANDP', 'ANDF'],
                   'contrasted_with': ['LD', 'LDI', 'LDP', 'LDF', 'OR', 'ORI', 'ORP', 'ORF']},
                  'INV', _refs(refs, (1578,)), model,
                  depends_on=['authored:inv:result_composition']),
            _fact('authored:inv:st_signature', 'parameters.st_signature',
                  {'parameters': [{'name': 'EN', 'type': 'BOOL', 'direction': 'input'}],
                   'returns': 'BOOL', 'enable': True, 'documented_form': 'INV(EN)'},
                  'INV', _refs(refs, (1578,)), model, languages=['st'],
                  depends_on=['authored:inv:result_composition']),
            _fact('authored:inv:operand_scope', 'parameters.device_form',
                  {'applicable_devices': 'none', 'operand_is_operation_result': True},
                  'INV', _refs(refs, (1578,)), model),
        ]
    return owners


def _program_end(refs, states):
    owners = {}
    for model, state in states.items():
        if state != 'supported':
            continue
        owners[(model, 'END')] = [
            _fact('authored:end:terminator', 'execution.program_terminator',
                  {'position': 'program_end', 'defines_program_end': True,
                   'output_processing': 'executed_immediately',
                   'steps_after_END': 'not_executed',
                   'auto_written_in_simple_project': True,
                   'programmable_in_structured_pou': False,
                   'mid_program_use': 'prohibited'},
                  'END', _refs(refs, (1590,)), model),
            _fact('authored:end:first_execution', 'execution.first_execution',
                  {'event': 'STOP_to_RUN', 'entry_point': 'END_instruction',
                   'not_a_ladder_step_zero_start': True},
                  'END', _refs(refs, (1590,)), model,
                  depends_on=['authored:end:terminator']),
            _fact('authored:end:scan_cycle', 'resources.scan_cycle',
                  {'phases': ['input_processing', 'program_execution', 'output_processing'],
                   'watchdog_refreshed_at_END': True,
                   'watchdog_purpose': 'operation_cycle_too_long_check'},
                  'END', _refs(refs, (1590,)), model),
            _fact('authored:end:st_signature', 'parameters.st_signature',
                  {'parameters': [], 'returns': 'BOOL', 'enable': False, 'documented_form': 'END'},
                  'END', _refs(refs, (1590,)), model, languages=['st']),
        ]
        owners[(model, 'NOP')] = [
            _fact('authored:nop:effect', 'execution.effect',
                  {'effect': 'none', 'project_scope': 'simple_project_only',
                   'structured_project_programmable': False},
                  'NOP', _refs(refs, (1590,)), model),
            _fact('authored:nop:project_binding', 'constraints.project_scope',
                  {'allowed_projects': ['simple_project'], 'rejected_projects': ['structured_project'],
                   'executor_capability': 'not_established', 'admission_effect': 'none'},
                  'NOP', _refs(refs, (1590,)), model,
                  depends_on=['authored:nop:effect']),
        ]
    return owners


_BUILDERS = {'contacts': _contacts, 'edges': _edges, 'mep_mef': _mep,
             'set_rst': _set_rst, 'pls_plf': _pulse, 'mc_mcr': _master_control,
             'and_or_block': _and_or, 'stack': _stack, 'inv': _invert,
             'program_end': _program_end}


# A section is only excluded from behaviour authoring for a stated reason; an
# unlisted section stays visibly pending instead of being counted as covered.
SECTION_EXCLUSIONS = (
    ("appendix", "source_appendix_not_instruction_behaviour"),
    ("factors that increase the scan time", "source_scan_time_table"),
    ("how to read", "source_reading_guide"),
    ("^outline", "source_outline"),
    ("device specification", "source_device_table_not_instruction"),
)


def _section_key(heading):
    """One comparable identity for a section heading across the index and specs."""
    leaf = (heading or '').split('>')[-1]
    return ' '.join(leaf.split()).strip()


def behaviour_sections(source):
    """Every registered manual section with its behaviour-extraction disposition."""
    from knowledge.instruction_compiler import instruction_heading_names
    # Ownership is bound to the manual, the section and the symbol that were read.
    # Another section of the same manual does not inherit this section's work.
    authored_by_section = set()
    for spec in SECTIONS.values():
        for section in (spec.get('chunks') or {}).values():
            addressed = section[0] if isinstance(section, tuple) else section
            for form in spec['forms']:
                authored_by_section.add((spec['manual_id'], _section_key(addressed), form))
        ladder = spec.get('ladder_manual')
        if ladder:
            for section in (spec.get('ladder_chunks') or {}).values():
                addressed = section[0] if isinstance(section, tuple) else section
                for form in spec['forms']:
                    authored_by_section.add((ladder['manual_id'], _section_key(addressed), form))
        mnemonics = spec.get('legacy_mnemonics') or {}
        for manual_id, legacy in (spec.get('legacy_chunks') or {}).items():
            for section, _witnesses in legacy['chunks'].values():
                for form in spec['forms']:
                    authored_by_section.add((manual_id, _section_key(section),
                                             mnemonics.get(form, form)))
    inventory = {}
    for row in source.execute("SELECT i.opcode AS opcode,i.manual_id AS manual_id,c.section AS section "
                              "FROM instructions i LEFT JOIN chunks c ON c.id=i.chunk_id"):
        heading = row['section'] or ''
        names = [name.upper() for name in instruction_heading_names(heading)] or [str(row['opcode']).upper()]
        entry = inventory.setdefault((row['manual_id'], heading),
                                     {'manual_id': row['manual_id'], 'section': heading, 'symbols': set()})
        entry['symbols'].update(names)
    # Sections whose text is in the source library but not in the instruction
    # index still own behaviour; they are read here rather than assumed absent.
    for row in source.execute("SELECT manual_id, section, instruction_opcode FROM chunks "
                              "WHERE section IS NOT NULL AND section != ''"):
        heading = row['section']
        key = (row['manual_id'], heading)
        if key in inventory:
            continue
        names = [name.upper() for name in instruction_heading_names(heading)]
        if row['instruction_opcode']:
            names.append(str(row['instruction_opcode']).upper())
        if not names:
            continue
        inventory[key] = {'manual_id': row['manual_id'], 'section': heading,
                          'symbols': set(names), 'chunk_derived': True}
    rows, dispositions = [], {}
    for (manual_id, heading), entry in sorted(inventory.items()):
        symbols = sorted(entry['symbols'])
        addressed = _section_key(heading)
        authored = [symbol for symbol in symbols
                    if (manual_id, addressed, symbol) in authored_by_section]
        excluded = next((reason for pattern, reason in SECTION_EXCLUSIONS
                         if re.search(pattern, heading, re.I)), None)
        if authored and len(authored) == len(symbols):
            disposition = 'behaviour_authored'
        elif authored:
            disposition = 'behaviour_authored_partially'
        elif excluded:
            disposition = excluded
        else:
            disposition = 'behaviour_extraction_pending'
        dispositions[disposition] = dispositions.get(disposition, 0) + 1
        row = {'manual_id': manual_id, 'section': heading, 'symbol_count': len(symbols),
               'disposition': disposition}
        if disposition == 'behaviour_authored':
            row['symbols'] = symbols
        elif disposition == 'behaviour_authored_partially':
            row['authored_symbols'] = authored
            row['pending_symbols'] = [symbol for symbol in symbols
                                      if (manual_id, addressed, symbol) not in authored_by_section]
        else:
            row['symbols'] = symbols
        rows.append(row)
    return {'sections': len(rows), 'dispositions': dispositions,
            'authored_forms': sorted({address[2] for address in authored_by_section}),
            'pending_sections': sum(1 for row in rows if row['disposition'].startswith('behaviour_extraction_pending')),
            'pending_symbols': sum(row['symbol_count'] for row in rows
                                   if row['disposition'] == 'behaviour_extraction_pending'),
            'inventory': rows}


def reviewed_behavior(source, entries=None):
    """Return authored behaviour facts per owner plus an explicit coverage receipt."""
    facts, receipt = {}, {'method': METHOD, 'reviewed_families': {},
                          'source_conflicts': [], 'native_unverified': [], 'omissions': {}}
    coverage = _manual_cpu_models(source)
    for family, builder in _BUILDERS.items():
        refs = _evidence(source, SECTIONS, family)
        states = model_states(source, SECTIONS[family]['header_chunk'])
        owner_facts = builder(refs, states)
        owner_facts = _enforce_source_cpu_scope(owner_facts, coverage, receipt, family)
        ladder = _ladder_evidence(source, family) if family in LADDER_SECTIONS else []
        if ladder:
            # Ladder behaviour keeps the ladder manual as its own reference.
            for owner, rows in owner_facts.items():
                for row in rows:
                    languages = row['scope'].get('languages')
                    if languages and 'ladder' in languages:
                        known_pages = {(source_row['manual_id'], tuple(source_row['pdf_pages']))
                                       for source_row in row['sources']}
                        row['sources'].extend(reference for reference in ladder
                                              if owner[0] in coverage.get(reference['manual_id'], set())
                                              and (reference['manual_id'],
                                                  tuple(reference['pdf_pages'])) not in known_pages)
        for owner, rows in owner_facts.items():
            facts.setdefault(owner, []).extend(rows)
        receipt['reviewed_families'][family] = {
            'forms': list(SECTIONS[family]['forms']),
            'chunks': sorted(refs),
            'pages': sorted({page for ref in refs.values() for page in ref['pdf_pages']}),
            'model_states': states,
            'fact_count': sum(len(rows) for rows in owner_facts.values()),
            'owner_count': len(owner_facts),
            'ladder_sections': sorted(LADDER_SECTIONS.get(family, {}).get('chunks', {}).values())}
    receipt['fact_count'] = sum(len(rows) for rows in facts.values())
    receipt['owner_count'] = len(facts)
    inventory = behaviour_sections(source)
    receipt['section_inventory'] = {key: value for key, value in inventory.items() if key != 'inventory'}
    receipt['section_inventory']['rows'] = inventory['inventory']
    if entries is not None:
        universe = {(entry['target_model'], entry['opcode']) for entry in entries}
        reviewed_forms = {form for family in SECTIONS.values() for form in family['forms']}
        absent = sorted(owner for owner in facts if owner not in universe)
        unreviewed = sorted(owner for owner in universe if owner[1] in reviewed_forms and owner not in facts)
        other = sorted({opcode for _, opcode in universe} - reviewed_forms)
        receipt['omissions'].update({
            'behaviour_owner_absent_from_definitions': [list(owner) for owner in absent],
            'reviewed_form_owner_without_behaviour': [list(owner) for owner in unreviewed],
            'owners_outside_reviewed_families': len([1 for owner in universe if owner[1] not in reviewed_forms]),
            'unreviewed_form_count': len(other),
            'unreviewed_forms': other})
    return facts, receipt


def reviewed_device_families(source):
    """Authored state-relay family facts: ranges, classes and documented behaviour."""
    refs = _evidence(source, DEVICE_SECTIONS, 'state_relay')
    records = []
    for models, table in STATE_RELAY_RANGES.items():
        established = table['class_binding'] == 'established'
        value = {'family': 'S', 'numbering': 'decimal', 'numbering_radix': 10,
                 'step_control_instruction': 'STL', 'class_binding': table['class_binding'],
                 'ranges': table['ranges'],
                 'step_sequence_behavior': {
                     'next_step_resets_preceding_state': True,
                     'general_type_power_off': 'all_off',
                     'latched_type_power_off': 'retained',
                     'usable_as_auxiliary_relay_outside_STL': True,
                     'set_instruction_drives_state': True}}
        records.append({
            'identity': 'device-family:state-relay:' + '/'.join(models),
            'target': 'S', 'models': list(models), 'value': value,
            'status': 'source_verified' if established else 'declared_unverified',
            'sources': _refs(refs, (2345, 2346, 2347)),
            'gaps': ([] if established else ['state_class_column_binding_not_reviewed']) + [
                'family_fact_is_not_address_support'],
            'metadata': {'device_family_basis': 'source_number_table_and_function_section',
                         'class_binding': table['class_binding']}})
    return records
