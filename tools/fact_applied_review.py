"""Authored behaviour facts for the FX native applied-instruction manuals.

The FX manuals state behaviour in several ways: structured variant tables
(``CMP 16 bits Continuous CMP(EN,s1,s2,d);``), structured operand records, and
ordinary sentences ("When the command input turns ON, ... is reset"). All three
are source. The authored rules below map documented statements onto typed facts,
so a section that prints no machine table is still reviewed and is never called
a source limitation just because it is prose.

Values are typed tokens and numbers, never prose, and nothing carries a
``behavior`` entry. Each fact keeps the instruction record it was read from
(manual, revision, pages) and the CPU scope the section declares. A section is
``behaviour_complete`` only when the required aspects (parameters, execution,
effects) are really extracted; otherwise it stays ``behaviour_partial`` or
``extraction_pending`` and is counted that way, not as authored.
"""
from __future__ import annotations

import json
import re

METHOD = 'authored_applied_behaviour_review_v1'

# FX native manuals with reviewed instruction sections. Structured application
# books (IEC/function style) are owned elsewhere and are not read here.
EXCLUDED_SECTIONS = (
    (r'How to Read Explanation of Instructions', 'editorial_section_not_instruction_behaviour'),
    (r'Performance Specification Of The FX2N\(C\)', 'cpu_performance_section_not_instruction_behaviour'),
)

MANUAL_LAYOUTS = {
    'fxcpu_basic_applied_m': 'structured_variant_table',
    'fx3_programming_r': 'programming_manual_operation',
    'jy992d88101e': 'legacy_comparison_table',
    'jy992d48301j': 'legacy_comparison_table',
}

LEGACY_HEADING_ALIASES = {'RUN': 'PRUN', 'VRSD': 'VRSC', 'EAUB': 'ESUB', 'RMMR': 'RMWR'}

REQUIRED_ASPECTS = ('parameters', 'execution', 'effects')

# Effects that only restate an operand's documented role do not prove that the
# operation behaviour was extracted; they never make an aspect substantive.
ROLE_DERIVED_EFFECTS = {'effects.source_read', 'effects.destination_write'}

# Aspect evidence must be a documented binding or behaviour, not bookkeeping.
SUBSTANTIVE_PARAMETERS = {'parameters.operand_roles', 'parameters.st_signature', 'parameters.order',
                          'parameters.source_order', 'parameters.value_type', 'parameters.types',
                          'parameters.device_form', 'parameters.connection_role', 'parameters.typed_operands'}
EVIDENCE_STATUSES = {'source_verified', 'candidate_evidence'}

_VARIANT_TABLE = re.compile(
    r'^(?P<name>[A-Z][A-Z0-9_+\-]*)[ \t]+(?P<width>\d+)[ \t]*bits?[ \t]+'
    r'(?P<form>Continuous|Pulse(?:[ \t]*\([A-Z]\))?)[ \t]+'
    r'(?P<call>[A-Z][A-Z0-9_+\-]*\([^()]*\))[ \t]*;', re.M)
_FORM_ROW = re.compile(
    r'^(?P<name>[A-Z][A-Z0-9_+\-]*)[ \t\n]+'
    r'(?:(?P<width>\d+)[ \t]*bits?[ \t\n]+)?'
    r'(?P<form>Cont[ \t]*in[ \t]*uous|Pulse(?:[ \t]*\([A-Z]\))?)'
    r'(?:[ \t]+(?P<tail>[^\n]*))?$', re.M)
_OPERANDS = re.compile(r'^OPERANDS:[ \t]*(?P<body>.+)$', re.M)
_OPERAND_ENTRY = re.compile(r'^(?P<name>[A-Z][A-Z0-9]*):[ \t]*(?P<purpose>.+?)'
                            r'(?:[ \t]*\[(?P<type>[^\]]+)\])?(?:[ \t]*applicable=|$)')
_VARIABLE = re.compile(r'variable[ \t]+(?P<name>EN|ENO)[ \t]+(?P<description>[A-Za-z ]{2,30}?)[ \t]+'
                       r'(?P<type>Bit|ANY[0-9A-Z_]+|Word[^\n]{0,12})')
_WIDTH_HEADING = re.compile(r'(?P<width>\d+)-bit operati\s*on[ \t]*\((?P<variants>[A-Za-z0-9_, /+\-]+?)\)', re.I)
_RESULT_BIT = re.compile(r'(?P<condition>[A-Za-z0-9 +<>=S.]+?)\s*-\s*bit device\s*(?P<device>D)'
                         r'(?P<offset>\+\d)?\s*is\s*ON')
_FNC = re.compile(r'FNC[ \t]*(?P<number>\d+)')
_VARIANT_GROUP = re.compile(r'(?<![A-Za-z0-9])(?P<variants>[A-Z][A-Z0-9]*(?:[ \t]*,[ \t]*[A-Z][A-Z0-9]*)+)[ \t]*:')
_LEGACY_HEADING = re.compile(r'^[ \t]*(?P<number>\d+(?:\.\d+)+)[ \t]+(?P<name>[A-Z][A-Z0-9]*)[ \t]*\(FNC', re.M)
_PROGRAM_STEPS = re.compile(r'(?P<steps>\d+)[ \t]*steps?')
_SECTION_HEADING = re.compile(r'^[ \t]*(?P<number>\d+(?:\.\d+)+)[ \t]+'
                              r'(?:FNC[ \t]*\d+[ \t]*[-\u2013\u2014][ \t]*)?(?P<name>[A-Z][A-Z0-9]*)', re.M)
_NO_ST_SYNTAX = re.compile(r'Syntax such as condition sentence is used|'
                          r'Refer to the following manual for syntaxes', re.I)
_NO_DEVICES = re.compile(r'(?:There (?:are|is) no (?:applicable devices|set data|target device)|'
                         r'No target device is available|N/A[ \t]*(?:\||\n|Automatically))', re.I)

HEADER_CONDITION = {'context': 'cpu_version_not_resolved', 'basis': 'instruction_header_icon'}
SCOPE_CONDITION = {'context': 'cpu_scope_only_from_manual_catalogue',
                   'basis': 'manual_declared_scope_not_section_header'}

RESULT_RELATIONS = (
    ('greater than both', None, 'greater_than_both'),
    ('less than', 'and s2', 'less_than_both'),
    ('equal to or between', None, 'equal_or_between'),
    ('less than', None, 'less_than'),
    ('greater than', None, 'greater_than'),
    ('equal to', None, 'equal'),
)

# Authored rules over documented sentences. A rule applies only when its own
# witness appears in the section, and its value stays a formal token or number:
# the sentence is the source, the published value is typed.
STATEMENT_RULES = (
    ('execution.condition', 'authored:applied:condition',
     re.compile(r'[Ww]hen the (?P<source>command input|input|drive contact|condition)[^.]{0,60}?'
                r'(?P<transition>turns ON|turns OFF|is ON|is OFF)'),
     lambda match: {'trigger': re.sub(r'[^a-z]+', '_', match.group('source').lower()).strip('_'),
                    'transition': re.sub(r'[^a-z]+', '_', match.group('transition').lower()).strip('_')}),
    ('execution.trigger', 'authored:applied:independent',
     re.compile(r'is the independent type, and does not require (?:command \()?drive contact'),
     lambda match: {'drive_contact_required': False, 'execution_type': 'independent'}),
    ('execution.jump', 'authored:applied:jump',
     re.compile(r'jumps? to (?:the )?(?:specified )?(?P<target>pointer|ladder block label)'),
     lambda match: {'target': re.sub(r'[^a-z]+', '_', match.group('target').lower()).strip('_'),
                    'intervening_steps': 'not_executed'}),
    ('execution.return', 'authored:applied:return',
     re.compile(r'(?:returns?|return) the program execution (?:from an? \w+ )?to the main'),
     lambda match: {'returns_to': 'main_program'}),
    ('execution.loop', 'authored:applied:loop',
     re.compile(r'loop between (?P<start>[A-Z]+) and (?P<end>[A-Z]+)[^.]{0,60}?repeated'),
     lambda match: {'start': match.group('start').lower(), 'end': match.group('end').lower(),
                    'repetitions': 'operand_n',
                    'after_loop': 'steps_after_end_instruction_executed'}),
    ('effects.device_span', 'authored:applied:device_span',
     re.compile(r'(?P<count>\d+)[ \t]*consecutive[ \t]*devices?[ \t]*(?:are|is)[ \t]*used'),
     lambda match: {'devices': int(match.group('count')), 'head_operand': 'D'}),
    ('effects.result_write', 'authored:applied:result_write',
     re.compile(r'(?:is |are )?(?:stored|moved|copied|output|written)[^.]{0,40}?'
                r'(?:at|in|to) the (?:specified )?destination(?: device)?'),
     lambda match: {'target': 'destination_operand', 'write': 'operation_result'}),
    ('effects.reset', 'authored:applied:reset',
     re.compile(r'reset[^.]{0,60}?(?P<range>S\d{3})[ \t]*to[ \t]*(?P<end>S\d{3})'),
     lambda match: {'device_range': '%s-%s' % (match.group('range'), match.group('end')),
                    'selection': 'smallest_number_in_ON_status'}),
    ('effects.logic', 'authored:applied:logic',
     re.compile(r'logical (?P<operation>XOR|AND|OR)\b'),
     lambda match: {'operation': match.group('operation').lower(),
                    'granularity': 'per_bit', 'target': 'destination_operand'}),
    ('constraints.operand_range', 'authored:applied:operand_range',
     re.compile(r'range[ \t]+(?P<min>\d+)[ \t]*to[ \t]*(?P<max>\d+)'),
     lambda match: {'min': int(match.group('min')), 'max': int(match.group('max')),
                    'basis': 'documented_range_statement'}),
    ('effects.program_end', 'authored:applied:program_end',
     re.compile(r'output processing, input processing and watchdog timer refresh are executed'),
     lambda match: {'phases': ['output_processing', 'input_processing', 'watchdog_timer_refresh'],
                    'next_step': 'step_0'}),
    ('effects.transfer', 'authored:applied:transfer',
     re.compile(r'[Cc]opied to the destination'),
     lambda match: {'write': 'copied_value', 'source_role': 'source_operand',
                    'target': 'destination_operand'}),
    ('effects.arithmetic', 'authored:applied:arithmetic',
     re.compile(r'(?P<op>combined|added|subtracted|multiplied|divided|logical AND|logical OR|logical XOR)'
                r'[^.]{0,90}?stored[^.]{0,30}destination'),
     lambda match: {'operation': {'combined': 'add', 'added': 'add', 'subtracted': 'subtract',
                                  'multiplied': 'multiply', 'divided': 'divide',
                                  'logical AND': 'and', 'logical OR': 'or',
                                  'logical XOR': 'xor'}[match.group('op')],
                    'target': 'destination_operand'}),
    ('execution.condition', 'authored:applied:control_input',
     re.compile(r'when the control input is active'),
     lambda match: {'trigger': 'control_input', 'state': 'active'}),
    ('effects.no_operation', 'authored:applied:not_driven',
     re.compile(r'If the \w+ instruction is not driven,? no operation takes place'),
     lambda match: {'when': 'instruction_not_driven', 'effect': 'no_operation'}),
    ('effects.bit_position', 'authored:applied:bit_position',
     re.compile(r'location of a bit within the destination device'),
     lambda match: {'target': 'destination_operand', 'selection': 'source_value'}),
    ('effects.device_state', 'authored:applied:device_state',
     re.compile(r'turns? ON or OFF according to the driven contact status'),
     lambda match: {'device_state': 'follows_drive_contact'}),
    ('effects.computation', 'authored:applied:computation',
     re.compile(r'executes (?P<name>[A-Za-z0-9\u2212 \-]{2,30}?)\s*(?:\([^)]*\)\s*)?operation'),
     lambda match: {'operation': _slug(match.group('name')),
                    'basis': 'documented_operation_statement'}),
    ('effects.read_operation', 'authored:applied:read_operation',
     re.compile(r'reads the (?P<object>[A-Za-z ]{3,30}?) data'),
     lambda match: {'operation': 'read', 'object': _slug(match.group('object')),
                    'basis': 'documented_operation_statement'}),
    ('effects.conversion', 'authored:applied:conversion',
     re.compile(r'data is converted into (?P<result>[A-Za-z ]{2,20}?)\s*(?:when|\.|$)'),
     lambda match: {'operation': 'convert', 'result': _slug(match.group('result')),
                    'basis': 'documented_operation_statement'}),
    ('effects.output_pattern', 'authored:applied:output_pattern',
     re.compile(r'creates (?P<pattern>[a-z ]{2,20}?) corresponding to the current value of a counter'),
     lambda match: {'target': _slug(match.group('pattern')), 'basis': 'counter_current_value'}),
    ('effects.computation', 'authored:applied:transformation',
     re.compile(r'is obtained from the [^.]{0,80}?stored in'),
     lambda match: {'operation': 'documented_transformation', 'source_role': 'source_operands',
                    'basis': 'documented_operation_statement'}),
    ('execution.pulse', 'authored:applied:pulse_execution',
     re.compile(r'executed (?:only )?(?:in|on|at) the (?P<edge>rising|falling) edge'),
     lambda match: {'execution': 'single_scan', 'edge': match.group('edge')}),
    ('execution.return', 'authored:applied:return_after_call',
     re.compile(r'returns? the pro ?gram (?:flow|execution) to the step immediately following the CALL'),
     lambda match: {'returns_to': 'step_after_CALL', 'returns_from': 'subroutine_program'}),
    ('execution.call', 'authored:applied:call',
     re.compile(r'calls? (?:and executes )?(?:a |the )?(?:subroutine|pro ?gram which should be processed)'),
     lambda match: {'enters': 'subroutine_program'}),
    ('effects.scan', 'authored:applied:skip',
     re.compile(r'(?:pro ?gram )?steps are skipped'),
     lambda match: {'executed': False, 'region': 'intervening_steps', 'scan_time_effect': 'reduced'}),
    ('effects.program_end', 'authored:applied:main_end',
     re.compile(r'(?:indicates|signifies) the end of the main pro ?gram'),
     lambda match: {'marker': 'end_of_main_program', 'next_step': 'step_0'}),
    ('parameters.operand_scope', 'authored:applied:no_operand',
     _NO_DEVICES,
     lambda match: {'applicable_devices': 'none'}),
)

# Control-flow behaviour the sections state in sentences. Every witness must
# appear in that record's own text before the fact is published.
CONTROL_FLOW_FACTS = {
    'fxcpu_basic_applied_m': {
        'CJ': ((
            ('execution.jump', 'authored:flow:jump',
             {'target': 'pointer_or_ladder_block_label', 'intervening_steps': 'not_executed',
              'scan_time_effect': 'reduced', 'double_coil_possible': True},
             'CJ or CJP instruction jumps to the specified pointer number or ladder block label.',
             'The sequence program steps between CJ or CJP instruction and the pointer are not executed.'),
            ('effects.scan', 'authored:flow:scan',
             {'skipped_region': 'between_cj_and_pointer', 'executed': False},
             'The sequence program steps between CJ or CJP instruction and the pointer are not executed.'),
            ('parameters.operand_scope', 'authored:flow:operand',
             {'applicable_devices': 'pointer_or_label', 'operand_width_bits': 16},
             'Pointer number or ladder block label for the jump destination ANY16'),
        ),),
        'CALL': ((
            ('execution.call', 'authored:flow:call',
             {'enters': 'subroutine_program', 'returns_via': 'SRET',
              'requires': ['FEND', 'SRET'], 'saves_program_steps': True},
             'This instruction calls and executes a program which should be processed commonly',
             'For creating a subroutine program, FEND and SRET instructions are required.'),
            ('effects.program_structure', 'authored:flow:structure',
             {'requires_programs': ['main_program', 'subroutine_program'],
              'subroutine_boundary': ['FEND', 'SRET']},
             'For creating a subroutine program, FEND and SRET instructions are required.'),
        ),),
        'SRET': ((
            ('execution.return', 'authored:flow:return',
             {'returns_to': 'main_program', 'returns_from': 'subroutine_program',
              'pairs_with': ['CALL', 'FEND'], 'operand_required': False},
             'This instruction returns the program execution from a subroutine to the main program.'),
        ),),
        'FOR': ((
            ('execution.loop', 'authored:flow:loop',
             {'start': 'FOR', 'end': 'NEXT', 'repetitions': 'operand_n',
              'after_loop': 'steps_after_end_instruction_executed', 'pairing': 'FOR_NEXT'},
             'FOR instruction specifies the number of repetition of the loop between FOR and NEXT instructions.',
             'FOR instruction and NEXT instruction are set as a pair in programming.'),
            ('constraints.cpu_scope', 'authored:flow:cpu_scope',
             {'operand': 'S1', 'models': ['FX3G', 'FX3GC', 'FX3U', 'FX3UC'],
              'rule': 'operand_device_scope_requires_CPU'},
             'S1: The FX3G, FX3GC, FX3U and FX3UC PLCs only are applicable.'),
            ('execution.trigger', 'authored:flow:enable',
             {'enable_condition': 'always_TRUE', 'drive_contact_required': False},
             'EN Execution condition Always TRUE'),
        ),),
        'NEXT': ((
            ('execution.loop', 'authored:flow:loop',
             {'start': 'FOR', 'end': 'NEXT', 'repetitions': 'operand_n',
              'after_loop': 'steps_after_end_instruction_executed', 'pairing': 'FOR_NEXT'},
             'The loop between FOR and NEXT instruction is repeated',
             'After the loop is repeated by the specified number of times, steps after NEXT instruction are executed.'),
            ('execution.trigger', 'authored:flow:enable',
             {'enable_condition': 'always_TRUE', 'drive_contact_required': False},
             'EN Execution condition Always TRUE'),
        ),),
        'OUT': ((
            ('effects.output_write', 'authored:flow:output',
             {'target': 'specified_device', 'write': 'operation_result',
              'device_state_follows': 'driven_contact_status', 'parallel_use': 'unlimited'},
             'outputs the operation result up to the execution of the OUT instruction to the specified device',
             'turns ON or OFF according to the driven contact status'),
            ('parameters.st_signature', 'authored:flow:signature',
             {'parameters': [{'name': 'EN', 'type': 'BOOL', 'direction': 'input',
                              'role': 'execution_condition'},
                             {'name': 'd', 'type': 'ANY_SIMPLE', 'direction': 'input',
                              'role': 'target_variable'}],
              'returns': 'unknown', 'enable': True, 'documented_form': 'OUT(X000,Y000)'},
             'OUT(EN,d);', 'Target variable ANY_SIMPLE'),
        ),),
    },
    'fx3_programming_r': {
        'CJ': ((
            ('execution.jump', 'authored:flow:jump',
             {'target': 'pointer', 'intervening_steps': 'not_executed',
              'scan_time_effect': 'reduced', 'double_coil_possible': True},
             'CJ or CJP instruction jumps to a pointer (P); The sequence program steps between CJ or CJP instruction and the',
             'CJ and CJP instructions reduce the scan time, and allow programs with double coils.'),
            ('effects.scan', 'authored:flow:scan',
             {'skipped_region': 'between_cj_and_pointer', 'executed': False},
             'The sequence program steps between CJ or CJP instruction and the'),
        ),),
        'CALL': ((
            ('execution.call', 'authored:flow:call',
             {'enters': 'subroutine_program', 'returns_via': 'SRET',
              'requires': ['FEND', 'SRET'], 'saves_program_steps': True},
             'This instruction calls and executes a program which should be processed commonly',
             'For creating a subroutine program, FEND (FNC 06) and SRET (FNC 02) instructions are required.'),
            ('effects.program_structure', 'authored:flow:structure',
             {'requires_programs': ['main_program', 'subroutine_program'],
              'subroutine_boundary': ['FEND', 'SRET']},
             'For creating a subroutine program, FEND (FNC 06) and SRET (FNC 02) instructions are required.'),
        ),),
        'SRET': ((
            ('execution.return', 'authored:flow:return',
             {'returns_to': 'main_program', 'returns_from': 'subroutine_program',
              'pairs_with': ['CALL', 'FEND'], 'operand_required': False},
             'This instruction returns the program execution from a subroutine to the main program.',
             'SRET instruction returns the program execution to the main routine.'),
        ),),
        'NEXT': ((
            ('execution.loop', 'authored:flow:loop',
             {'start': 'FOR', 'end': 'NEXT', 'repetitions': 'operand_n',
              'after_loop': 'steps_after_end_instruction_executed', 'pairing': 'FOR_NEXT'},
             'NEXT instruction specifies the end position of the loop.',
             'After the loop is repeated by the specified number of times, steps after NEXT instruction are executed.'),
        ),),
        'EI': ((
            ('execution.enable', 'authored:flow:enable',
             {'enables': 'interrupt', 'drive_contact_required': False, 'execution_type': 'independent',
              'interrupt_sources': ['input', 'timer', 'counter']},
             'This instruction enables interrupts in PLCs.',
             'EI instruction is the independent type, and does not require command (drive) contact.'),
        ),),
        'DI': ((
            ('execution.enable', 'authored:flow:disable',
             {'disables': 'interrupt', 'drive_contact_required': False, 'execution_type': 'independent',
              'pending_requests': 'processed_after_EI'},
             'This instruction disables interrupts after interrupts were enabled by EI (FNC 04) instruction.',
             'generated after DI instruction are processed after EI (FNC 04) instruction is executed'),
        ),),
        'IRET': ((
            ('execution.return', 'authored:flow:return',
             {'returns_to': 'main_program', 'returns_from': 'interrupt_routine',
              'interrupt_sources': ['input', 'timer', 'counter'], 'operand_required': False},
             'This instruction returns the program execution from an interrupt routine to the main program.',
             'IRET instruction returns the program execution to the main routine.'),
        ),),
        'FEND': ((
            ('effects.program_end', 'authored:flow:program_end',
             {'phases': ['output_processing', 'input_processing', 'watchdog_timer_refresh'],
              'next_step': 'step_0', 'same_as': 'END',
              'required_for': ['subroutine_program', 'interrupt_program']},
             'This instruction indicates the end of the main program.',
             'output processing, input processing and watchdog timer refresh are executed',
             'FEND instruction is required in creating subroutine programs and interrupt programs.'),
        ),),
        'WDT': ((
            ('resources.watchdog', 'authored:flow:watchdog',
             {'refreshes': 'watchdog_timer', 'period_ms': 200,
              'effect_on_exceed': 'watchdog_timer_error', 'plc_state': 'stops'},
             'This instruction refreshes the watchdog timer in a sequence program.',
             'exceeds 200 ms'),
        ),),
        'ANR': ((
            ('effects.annunciator_reset', 'authored:flow:annunciator',
             {'device_range': 'S900-S999', 'selection': 'smallest_number_in_ON_status',
              'repeat': 'next_smallest_on_next_execution', 'target_state': 'OFF'},
             'This instruction resets an annunciator (S900 to S999) in the ON status with the smallest number.',
             'If two or more state relays are ON, the stat e relay with the smallest number is reset.'),
        ),),
    },
}


# Each row is a reviewed meaning of a source predicate, not a name-to-behaviour
# registry. An instruction name alone never emits a fact: its own section must
# contain the predicate as well. These summaries intentionally do not certify
# every operand, error condition or execution-state transition of a section.
OPERATION_REVIEW = (
    ('ADD ADDP', r'executes addition|two source devices.*added|two source values.*added',
     {'operation': 'addition'}),
    ('SUB SUBP', r'executes subtraction|source.*subtracted from', {'operation': 'subtraction'}),
    ('MUL MULP', r'executes multiplication|source.*multiplied', {'operation': 'multiplication'}),
    ('DIV DIVP', r'executes division|source.*divided by', {'operation': 'division', 'results': ['quotient', 'remainder']}),
    ('EADD DEADD', r'adds two binary floating|addition of.*floating', {'operation': 'addition', 'data': 'binary_floating_point'}),
    ('ESUB DESUB', r'subtracts two binary floating|subtraction of.*floating', {'operation': 'subtraction', 'data': 'binary_floating_point'}),
    ('EMUL DEMUL', r'multiplies two binary floating|multiplication of.*floating', {'operation': 'multiplication', 'data': 'binary_floating_point'}),
    ('EDIV DEDIV', r'divides two binary floating|division of.*floating', {'operation': 'division', 'data': 'binary_floating_point'}),
    ('BK+', r'adds binary block data|adds.*data.*block', {'operation': 'block_addition'}),
    ('BK-', r'subtracts binary block data|subtracts.*data.*block', {'operation': 'block_subtraction'}),
    ('INC', r'increments? .*by "?1"?|increased by 1', {'operation': 'increment', 'amount': 1}),
    ('DEC', r'decrements? .*by "?1"?|decreased by 1', {'operation': 'decrement', 'amount': 1}),
    ('NEG', r"2.s complement|two.s complement", {'operation': 'twos_complement'}),
    ('ENEG DENEG', r'inverts the sign|sign.*invert', {'operation': 'sign_inversion', 'data': 'binary_floating_point'}),
    ('WAND', r'logical product \(AND\)|logical AND', {'operation': 'bitwise_and'}),
    ('WOR', r'logical sum \(OR\)|logical OR', {'operation': 'bitwise_or'}),
    ('WXOR', r'exclusive logical sum \(XOR\)|logical XOR', {'operation': 'bitwise_xor'}),
    ('SQR ESQR DESQR', r'square root', {'operation': 'square_root'}),
    ('SIN DSIN', r'SIN value of angle|sine.*angle', {'operation': 'sine', 'angle_unit': 'radian'}),
    ('COS DCOS', r'COS value of angle|cosine.*angle', {'operation': 'cosine', 'angle_unit': 'radian'}),
    ('TAN DTAN', r'TAN value of angle|tangent.*angle', {'operation': 'tangent', 'angle_unit': 'radian'}),
    ('ASIN DASIN', r'SIN-1 operation|arc sine|inverse sine', {'operation': 'arcsine'}),
    ('ACOS DACOS', r'COS-1 operation|arc cosine|inverse cosine', {'operation': 'arccosine'}),
    ('ATAN DATAN', r'TAN-1 operation|arc tangent|inverse tangent', {'operation': 'arctangent'}),
    ('EXP DEXP', r'exponential operation', {'operation': 'exponential', 'base': 'e'}),
    ('LOGE DLOGE', r'natural logarithm', {'operation': 'natural_logarithm'}),
    ('LOG10 DLOG10', r'common logarithm', {'operation': 'common_logarithm', 'base': 10}),
    ('MEAN', r'mean value|average value', {'operation': 'arithmetic_mean'}),
    ('WSUM', r'total value of continuous|total.*word data', {'operation': 'word_data_sum'}),
    ('SUM', r'number of "1" \(ON\) bits|number of.*ON bits', {'operation': 'population_count'}),
    ('CMP ECMP DECMP', r'compares two (?:values|data)|comparison.*two.*values',
     {'operation': 'comparison', 'results': ['less_than', 'equal', 'greater_than'], 'output_bits': 3}),
    ('ZCP EZCP DEZCP', r'compares.*(?:zone|upper and lower)|comparison range',
     {'operation': 'zone_comparison', 'output_bits': 3}),
    ('MOV', r'transfers \(copies\) the contents|source.*copied to the destination', {'operation': 'copy'}),
    ('EMOV DEMOV', r'transfers binary floating|floating point.*copied', {'operation': 'copy', 'data': 'binary_floating_point'}),
    ('BMOV', r'transfers \(copies\) a specified number|block.*data.*copied', {'operation': 'block_copy'}),
    ('FMOV', r'transfers (?:the )?same data|same source.*copied', {'operation': 'fill'}),
    ('SMOV', r'units of digit \(4 bits\)|specified number of di ?gits', {'operation': 'digit_move', 'digit_bits': 4}),
    ('CML', r'inverts data in units of bit|each data bit.*inverted', {'operation': 'bitwise_complement_copy'}),
    ('XCH', r'exchanges data between two devices|data.*swapped', {'operation': 'exchange'}),
    ('ZRST', r'resets devices located in a zone|reset.*range of devices', {'operation': 'range_reset'}),
    ('BON', r'checks whether a specified bit|location of a bit within', {'operation': 'bit_test'}),
    ('ALT', r'inverting the bit device|alternates a bit device|inverted on every operation',
     {'operation': 'bit_state_inversion', 'states': [False, True]}),
    ('BCD', r'converts binary \(BIN\).*binary-coded decimal|binary.*converted.*BCD',
     {'operation': 'conversion', 'input_encoding': 'binary', 'output_encoding': 'bcd'}),
    ('BIN', r'converts binary-coded decimal.*binary \(BIN\)|BCD.*converted.*binary',
     {'operation': 'conversion', 'input_encoding': 'bcd', 'output_encoding': 'binary'}),
    ('FLT', r'converts a binary integer.*binary floating|integer.*floating point',
     {'operation': 'conversion', 'input_encoding': 'binary_integer', 'output_encoding': 'binary_floating_point'}),
    ('INT', r'converts the binary floating.*BIN integer|floating.*converted.*integer',
     {'operation': 'conversion', 'input_encoding': 'binary_floating_point', 'output_encoding': 'binary_integer'}),
    ('EBCD DEBCD', r'converts the binary floating.*decimal floating',
     {'operation': 'conversion', 'input_encoding': 'binary_floating_point', 'output_encoding': 'decimal_floating_point'}),
    ('EBIN DEBIN', r'converts the decimal floating.*binary floating',
     {'operation': 'conversion', 'input_encoding': 'decimal_floating_point', 'output_encoding': 'binary_floating_point'}),
    ('DECO', r'converts numeric data into ON bit|bit.*decoded', {'operation': 'decode_to_bit_position'}),
    ('ENCO', r'obtains positions in which bits are ON|position.*ON.*bit', {'operation': 'encode_bit_position'}),
    ('GRY DGRY', r'converts a binary value into a gray code', {'operation': 'binary_to_gray'}),
    ('GBIN DGBIN', r'converts a gray code into a binary value', {'operation': 'gray_to_binary'}),
    ('RAD DRAD', r'angle unit to the radian unit|degrees.*radians', {'operation': 'degrees_to_radians'}),
    ('DEG DDEG', r'radian unit value into the angle|radians.*degrees', {'operation': 'radians_to_degrees'}),
    ('ROL RCL', r'rotates the bit information leftward|rotate.*left', {'operation': 'rotate', 'direction': 'left'}),
    ('ROR RCR', r'rotates the bit information rightward|rotate.*right', {'operation': 'rotate', 'direction': 'right'}),
    ('RCL RCR', r'together with the carry flag|including.*carry', {'carry_participates': True}),
    ('ROL ROR', r'without the carry flag|excluding.*carry', {'carry_participates': False}),
    ('SFTL SFL', r'(?:shifts|shifted).*leftward|shift.*left', {'operation': 'shift', 'direction': 'left', 'unit': 'bit'}),
    ('SFTR SFR', r'(?:shifts|shifted).*rightward|shift.*right', {'operation': 'shift', 'direction': 'right', 'unit': 'bit'}),
    ('WSFL', r'(?:shifts|shifted).*leftward|word.*shift.*left', {'operation': 'shift', 'direction': 'left', 'unit': 'word'}),
    ('WSFR', r'(?:shifts|shifted).*rightward|word.*shift.*right', {'operation': 'shift', 'direction': 'right', 'unit': 'word'}),
    ('SFWR', r'writes data for first-in first-out|shift.*write', {'operation': 'queue_push', 'orders': ['fifo', 'filo']}),
    ('SFRD', r'reads data for first-in first-out|first data.*read', {'operation': 'queue_pop', 'order': 'fifo'}),
    ('POP', r'reads the last data written|last data.*read', {'operation': 'queue_pop', 'order': 'filo'}),
    ('BTOW', r'couples the lower 8 bits|lower byte.*coupl', {'operation': 'pack_low_bytes', 'unit_bits': 8}),
    ('WTOB', r'separates continuous 16-bit data in byte|word.*byte.*separat', {'operation': 'unpack_bytes', 'unit_bits': 8}),
    ('DIS', r'separates 16-bit data in 4-bit|four.*bit.*separat', {'operation': 'unpack_nibbles', 'unit_bits': 4}),
    ('UNI', r'couples lower 4 bits|lower 4 bits.*coupl', {'operation': 'pack_low_nibbles', 'unit_bits': 4}),
    ('SWAP', r'swaps higher 8 bits and lower 8 bits|swaps lower 8 bits and higher 8 bits', {'operation': 'swap_bytes', 'unit_bits': 8}),
    ('ASC', r'alphanumeric character string into ASCII|ASCII code.*character', {'operation': 'literal_to_ascii'}),
    ('ASCI', r'converts HEX code into ASCII|hexadecimal.*ASCII', {'operation': 'hex_to_ascii'}),
    ('HEX', r'converts ASCII code into HEX|ASCII.*hexadecimal', {'operation': 'ascii_to_hex'}),
    ('BINDA', r'converts binary data into decimal ASCII', {'operation': 'binary_to_decimal_ascii'}),
    ('DABIN', r'decimal ASCII codes.*into binary data', {'operation': 'decimal_ascii_to_binary'}),
    ('STR', r'converts binary data into character strings', {'operation': 'binary_to_ascii_string'}),
    ('VAL', r'converts a character string.*into binary data', {'operation': 'ascii_string_to_binary'}),
    ('ESTR DESTR', r'converts the binary floating.*into character string', {'operation': 'floating_point_to_ascii_string'}),
    ('EVAL DEVAL', r'converts the character string.*into binary floating', {'operation': 'ascii_string_to_floating_point'}),
    ('LEN', r'number of characters \(bytes\)|length of.*string', {'operation': 'string_length', 'unit': 'byte'}),
    ('LEFT', r'extracts.*from the left end', {'operation': 'substring', 'origin': 'left'}),
    ('RIGHT', r'extracts.*from the right end', {'operation': 'substring', 'origin': 'right'}),
    ('MIDR', r'extracts.*from arbitrary positions', {'operation': 'substring', 'origin': 'specified_position'}),
    ('MIDW', r'replaces the characters in arbitrary positions', {'operation': 'string_replace', 'origin': 'specified_position'}),
    ('INSTR', r'searches a specified character string within', {'operation': 'substring_search'}),
    ('FDEL', r'deletes an arbitrary piece of data|deletes.*data.*table', {'operation': 'table_delete'}),
    ('FINS', r'inserts data into an arbitrary position|inserts.*data.*table', {'operation': 'table_insert'}),
    ('SER', r'searching same data and maximum value, minimum value|search.*equal.*maximum.*minimum',
     {'operation': 'table_search', 'results': ['equal', 'maximum', 'minimum']}),
    ('SORT', r'ascending order in column unit|sorts.*ascending', {'operation': 'table_sort', 'order': 'ascending'}),
    ('SORT2', r'ascending order/descending order in row unit', {'operation': 'table_sort', 'orders': ['ascending', 'descending']}),
    ('LIMIT', r'upper limit value and lower limit value.*control', {'operation': 'limit_control'}),
    ('BAND', r'upper limit value and lower limit value of the dead band', {'operation': 'dead_band_control'}),
    ('ZONE', r'positive or negative.*bias value', {'operation': 'signed_bias_control'}),
    ('SCL SCL2', r'executes scaling.*specified data table', {'operation': 'table_scaling'}),
    ('RAMP', r'data changing n times.*initial value.*target value|ramp.*initial.*final', {'operation': 'ramp_interpolation'}),
    ('RND', r'pseudo-random number within the range from 0 to 32767', {'operation': 'pseudo_random', 'min': 0, 'max': 32767}),
    ('ANS', r'sets a state relay as an annunciator|annunciator.*set', {'operation': 'annunciator_set'}),
    ('ANR', r'resets an annunciator.*smallest number|state relay with the smallest number is reset',
     {'operation': 'annunciator_reset', 'selection': 'lowest_active_number'}),
    ('ABSD', r'output patterns corresponding to the present value|output patterns.*current value',
     {'operation': 'counter_pattern_output'}),
    ('INCD', r'output patterns by using a pair of counters', {'operation': 'dual_counter_pattern_output'}),
    ('IST', r'initial state and special auxiliary relay automatically', {'operation': 'step_ladder_initialization'}),
    ('STMR', r'off-delay timer, one-shot timer or flicker timer', {'operation': 'timer_patterns', 'patterns': ['off_delay', 'one_shot', 'flicker']}),
    ('TTMR', r'measuring the ON duration', {'operation': 'measure_enabled_duration'}),
    ('HOUR', r'measures the ON time duration.*one-hour unit', {'operation': 'accumulate_enabled_duration', 'unit': 'hour'}),
    ('HTOS', r'"hour, minute, second" unit.*into second unit', {'operation': 'hms_to_seconds'}),
    ('STOH', r'second unit into time data in "hour, minute, second"', {'operation': 'seconds_to_hms'}),
    ('TCMP', r'comparison time and the time data are compared', {'operation': 'time_comparison'}),
    ('TZCP', r'comparison time of higher and lower points', {'operation': 'time_zone_comparison'}),
    ('TADD', r'addition time data|two time data.*add', {'operation': 'time_addition', 'components': ['hour', 'minute', 'second']}),
    ('TSUB', r'subtracting result of two time data', {'operation': 'time_subtraction', 'components': ['hour', 'minute', 'second']}),
    ('TRD', r'clock data is read out', {'operation': 'read_real_time_clock'}),
    ('TWR', r'clock data is written into', {'operation': 'write_real_time_clock'}),
    ('REF', r'immediately outputs the latest input.*current output', {'operation': 'io_refresh'}),
    ('REFF', r'digital input filter time.*changed', {'operation': 'input_filter_refresh'}),
    ('MTR', r'reads matrix input.*time division', {'operation': 'matrix_input', 'method': 'time_division'}),
    ('SPD', r'counts the input pulse for a specified period', {'operation': 'timed_interrupt_pulse_count'}),
    ('DHSCS', r'immediately sets an external output|immediately turns ON', {'operation': 'high_speed_compare_set'}),
    ('DHSCR', r'immediately resets an external output', {'operation': 'high_speed_compare_reset'}),
    ('DHSZ', r'current value of a high speed counter with two values', {'operation': 'high_speed_zone_compare', 'output_bits': 3}),
    ('DHSCT', r'high speed counter.*data table.*up to 16 output', {'operation': 'high_speed_table_compare', 'max_outputs': 16}),
    ('DHCMOV', r'current value of a specified high speed counter or ring counter', {'operation': 'counter_current_value_copy'}),
    ('PLSY', r'generates a pulse signal', {'operation': 'pulse_output'}),
    ('PLSR', r'pulse output instruction has the acceleration/deceleration', {'operation': 'pulse_output', 'profile': 'acceleration_deceleration'}),
    ('PWM', r'outputs pulses with a specified period and ON duration', {'operation': 'pulse_width_modulation'}),
    ('PLSV', r'variable speed pulse with the rotating direction', {'operation': 'variable_speed_pulse_output'}),
    ('DRVA', r'one-speed positioning by absolute drive', {'operation': 'positioning', 'reference': 'absolute'}),
    ('DRVI', r'one-speed positioning by rela ?tive drive', {'operation': 'positioning', 'reference': 'relative'}),
    ('DSZR ZRN', r'mechanical position and the current value register.*zero return', {'operation': 'zero_return'}),
    ('DVIT', r'one-speed interrupt inching', {'operation': 'interrupt_positioning'}),
    ('DTBL', r'specifies one table and operates instructions', {'operation': 'positioning_table_execution'}),
    ('DABS ABS', r'MR-J4.*MR-J3.*MR-J2', {'operation': 'absolute_position_read', 'hardware': 'servo_amplifier'}),
    ('DUTY', r'timing signal whose one cycle corresponds.*operation cycles', {'operation': 'scan_count_pulse'}),
    ('WDT', r'refreshes the watchdog timer', {'operation': 'watchdog_refresh'}),
    ('FROM', r'reads out the content of buffer memory', {'operation': 'buffer_memory_read'}),
    ('TO', r'writes data from PLC into the buffer memory', {'operation': 'buffer_memory_write'}),
    ('RBFM', r'reads data from continuous buffer memories.*several operation cycles', {'operation': 'buffer_memory_read', 'method': 'time_division'}),
    ('WBFM', r'writes data to continuous buffer memories.*several operation cycles', {'operation': 'buffer_memory_write', 'method': 'time_division'}),
    ('RS', r'sends and receives data in no-protocol communication', {'operation': 'serial_send_receive', 'protocol': 'none'}),
    ('RS2', r'transmits and receives data by no- ?procedure communication', {'operation': 'serial_send_receive', 'protocol': 'none'}),
    ('ADPRW', r'MODBUS Master to communicate.*Slaves', {'operation': 'modbus_master_read_write'}),
    ('CRC', r'calculates the CRC \(cyclic redundancy check\)', {'operation': 'crc'}),
    ('CCD', r'horizontal parity value or check sum value', {'operation': 'parity_or_checksum'}),
    ('COMRD', r'reads the comment data for registered devices', {'operation': 'device_comment_read'}),
    ('IVCK', r'reads the operation status of an inverter', {'operation': 'inverter_status_read'}),
    ('IVDR', r'writes a inverter operation required control value', {'operation': 'inverter_control_write'}),
    ('IVRD', r'reads an inverter parameter', {'operation': 'inverter_parameter_read'}),
    ('IVWR', r'writes a parameter of an inverter', {'operation': 'inverter_parameter_write'}),
    ('IVBWR', r'writes parameters of an inverter at one time', {'operation': 'inverter_parameter_block_write'}),
    ('IVMC', r'writes 2 types of settings.*reads 2 types of data', {'operation': 'inverter_combined_read_write'}),
    ('LOADR', r'reads the current values of extension file registers.*transfers them to extension registers', {'operation': 'extension_file_register_load'}),
    ('SAVER', r'writes the current values of extension registers.*units of sector \(2048 points\)', {'operation': 'extension_file_register_sector_save', 'sector_points': 2048}),
    ('RWER', r'writes the current values of an arbitrary number of extension registers', {'operation': 'extension_file_register_write'}),
    ('INITR', r'initializes.*HFFFF', {'operation': 'extension_register_initialize', 'value': -1, 'targets': ['R', 'ER']}),
    ('INITER', r'initialize ?s extension file registers.*HFFFF', {'operation': 'extension_register_initialize', 'value': -1, 'targets': ['ER']}),
    ('LOGR', r'logs specified devices.*stores the logged data', {'operation': 'device_data_logging'}),
    ('ZPUSH', r'temporarily retracts the present values of index registers', {'operation': 'save_index_registers', 'registers': ['V0-V7', 'Z0-Z7']}),
    ('ZPOP', r'returns the contents of index registers.*original state', {'operation': 'restore_index_registers', 'registers': ['V0-V7', 'Z0-Z7']}),
    ('FLCRT', r'creates a file inside the CompactFlash', {'operation': 'cf_file_create_check'}),
    ('FLDEL', r'deletes files stored in the CompactFlash.*formats', {'operation': 'cf_file_delete_or_format'}),
    ('FLRD', r'reads data from the CompactFlash', {'operation': 'cf_file_read'}),
    ('FLWR', r'writes data to the CompactFlash', {'operation': 'cf_file_write'}),
    ('FLCMD', r'gives instruction for operation to the (?:FX3U-)?CF', {'operation': 'cf_adapter_command'}),
    ('FLSTRD', r'reads the status \(including the error information and file information\)', {'operation': 'cf_adapter_status_read'}),
    ('OUT_C OUT_C_32', r'counter starts counting when the condition turns ON from OFF', {'operation': 'count_rising_edges'}),
    ('RD3A', r'reads an analog input value from the analog block', {'operation': 'analog_input_read'}),
    ('WR3A', r'writes a digital value to the analog block', {'operation': 'analog_output_write'}),
    ('VRRD VRSC', r'reads out the value determined.*variable resistor', {'operation': 'potentiometer_read'}),
    ('DSW', r'reading in the set value of digital switch', {'operation': 'digital_switch_read'}),
    ('TKY', r'setting data in the timer or counter.*numeric keys', {'operation': 'numeric_key_input'}),
    ('HKY', r'input data of numerical value.*function keys', {'operation': 'hexadecimal_key_input'}),
    ('ARWS', r'enters data by arrow switches', {'operation': 'arrow_key_input'}),
    ('SEGD', r'7-seg(?:e)?ment display unit.*decoding', {'operation': 'seven_segment_decode'}),
    ('SEGL', r'7-segment display unit with latch', {'operation': 'latched_seven_segment_output'}),
    ('PR', r'parallel output of ASCII code data', {'operation': 'parallel_ascii_output'}),
    ('PRUN', r'device number.*octal number.*transfers data', {'operation': 'octal_device_data_transfer'}),
    ('ROTC', r'rotary table|turning the table by a shortcut route', {'operation': 'rotary_table_control'}),
    ('ANRD', r'analog input of F2-6A|read in analog input', {'operation': 'analog_input_read', 'hardware': 'F2-6A'}),
    ('ANWR', r'writes data.*F2-6A', {'operation': 'analog_output_write', 'hardware': 'F2-6A'}),
    ('BLK', r'specifies the block number.*F2-30GM', {'operation': 'motion_block_select', 'hardware': 'F2-30GM'}),
    ('MCDE', r'sends the M code numbers.*F2-30GM', {'operation': 'motion_m_code_read', 'hardware': 'F2-30GM'}),
    ('MNET', r'exchanges ON/OFF signals.*F-16NP/NT', {'operation': 'network_io_exchange', 'hardware': 'F-16NP-NT'}),
    ('RMMN', r'reads out the rotating speed.*present angle', {'operation': 'resolver_speed_or_angle_read'}),
    ('RMRD', r'reads out the ON/OFF state.*F ?2-32RM', {'operation': 'cam_output_state_read', 'hardware': 'F2-32RM'}),
    ('RMST', r'gives start signal.*receives status', {'operation': 'cam_start_or_status', 'hardware': 'F2-32RM'}),
    ('RMWR', r'sends output prohibit information', {'operation': 'cam_output_inhibit_write', 'hardware': 'F2-32RM'}),
    ('EXTR_IN', r'writes the operation control instructions and parameters', {'operation': 'extension_function_control_write'}),
    ('EXTR_OUT', r'short mail transmission.*inverter operation monitoring', {'operation': 'extension_function_read_or_transmit'}),
    ('BTOW', r'combines the low-order 8 bits', {'operation': 'pack_low_bytes', 'unit_bits': 8}),
    ('CCD', r'horizontal parity value and sum check value', {'operation': 'parity_or_checksum'}),
    ('DIS', r'separates 16-bit data into 4 bit units', {'operation': 'unpack_nibbles', 'unit_bits': 4}),
    ('DRVI', r'one-speed positioning by incr ?emental drive', {'operation': 'positioning', 'reference': 'relative'}),
    ('DSZR ZRN', r'executes a zero return.*aligns the mechanical position', {'operation': 'zero_return'}),
    ('DVIT', r'one-speed interrupt constant quantity feed', {'operation': 'interrupt_positioning'}),
    ('EBCD DEBCD', r'converts binary floating point into scientific notation',
     {'operation': 'conversion', 'input_encoding': 'binary_floating_point', 'output_encoding': 'decimal_floating_point'}),
    ('EBIN DEBIN', r'converts scientific notation.*into binary floating point',
     {'operation': 'conversion', 'input_encoding': 'decimal_floating_point', 'output_encoding': 'binary_floating_point'}),
    ('ESTR DESTR', r'converts binary floating point data into a character string', {'operation': 'floating_point_to_ascii_string'}),
    ('EVAL DEVAL', r'converts a character string.*into binary floating point data', {'operation': 'ascii_string_to_floating_point'}),
    ('FROM', r'reads the contents of buffer memories', {'operation': 'buffer_memory_read'}),
    ('HCMOV DHCMOV', r'updates the current value of a specified high-speed counter', {'operation': 'counter_current_value_copy'}),
    ('HKY', r'16 key \(0 to F\).*input', {'operation': 'hexadecimal_key_input'}),
    ('HOUR', r'measures the ON time.*units of hour', {'operation': 'accumulate_enabled_duration', 'unit': 'hour'}),
    ('HSCT DHSCT', r'high-speed counter.*data table.*up to 16 output', {'operation': 'high_speed_table_compare', 'max_outputs': 16}),
    ('HSZ DHSZ', r'current value of a high-speed counter with two values', {'operation': 'high_speed_zone_compare', 'output_bits': 3}),
    ('HTOS', r'units of "hour, minute, and second" into data in units of "second"', {'operation': 'hms_to_seconds'}),
    ('STOH', r'units of "second" into data in units of "hour, minute, and second"', {'operation': 'seconds_to_hms'}),
    ('INCD', r'creates many output patterns using a pair of counters', {'operation': 'dual_counter_pattern_output'}),
    ('IST', r'automatically controls the initial state and special auxiliary relays', {'operation': 'step_ladder_initialization'}),
    ('IVDR', r'inverter operation required cont ?rol value', {'operation': 'inverter_control_write'}),
    ('IVWR', r'writes an inverter parameter', {'operation': 'inverter_parameter_write'}),
    ('PID', r'executes PID control', {'operation': 'pid_control'}),
    ('PLSV', r'outputs variable speed pulses with an assigned rotation direction', {'operation': 'variable_speed_pulse_output'}),
    ('PR', r'outputs ASCII code data.*in parallel', {'operation': 'parallel_ascii_output'}),
    ('RAMP', r'data which changes between the start value.*end value.*specified "n" times', {'operation': 'ramp_interpolation'}),
    ('RND', r'pseudo-random number.*0 to 32,?767', {'operation': 'pseudo_random', 'min': 0, 'max': 32767}),
    ('RS RS2', r'sends and receives data using non-protoc ?ol communication', {'operation': 'serial_send_receive', 'protocol': 'none'}),
    ('SAVER', r'writes the current values of the extension registers.*sector units \(2048 points\)',
     {'operation': 'extension_file_register_sector_save', 'sector_points': 2048}),
    ('SEGD', r'decodes data.*seven-segment display unit', {'operation': 'seven_segment_decode'}),
    ('SEGL', r'seven-segment display units having the latch function', {'operation': 'latched_seven_segment_output'}),
    ('SER', r'searches for the same data, maximum value and minimum value', {'operation': 'table_search', 'results': ['equal', 'maximum', 'minimum']}),
    ('SORT2', r'ascending or descendin ?g order', {'operation': 'table_sort', 'orders': ['ascending', 'descending']}),
    ('STMR', r'off-delay timers, one-shot timers and flicker timers', {'operation': 'timer_patterns', 'patterns': ['off_delay', 'one_shot', 'flicker']}),
    ('SWAP', r'swaps the high-order 8 bits and low-order 8 bits', {'operation': 'swap_bytes', 'unit_bits': 8}),
    ('TBL DTBL', r'executes one specified table operation', {'operation': 'positioning_table_execution'}),
    ('TCMP', r'compares the comparison time with the time data', {'operation': 'time_comparison'}),
    ('TZCP', r'compares two comparison time \(comparison time zone\)', {'operation': 'time_zone_comparison'}),
    ('TKY', r'sets data for timers and counters through ten key inputs', {'operation': 'numeric_key_input'}),
    ('TO', r'writes data from a PLC to buffer memories', {'operation': 'buffer_memory_write'}),
    ('TRD', r'reads the clock data', {'operation': 'read_real_time_clock'}),
    ('TWR', r'writes the clock data', {'operation': 'write_real_time_clock'}),
    ('TSUB', r'executes subtraction of two time data', {'operation': 'time_subtraction', 'components': ['hour', 'minute', 'second']}),
    ('TTMR', r'measures the period of time in which TTMR instruction is ON', {'operation': 'measure_enabled_duration'}),
    ('UNI', r'combines the low-order 4 bits', {'operation': 'pack_low_nibbles', 'unit_bits': 4}),
    ('VRRD VRSC', r'reads the (?:analog )?value of a variable analog potentiometer', {'operation': 'potentiometer_read'}),
    ('WSUM', r'calculates the sum of consecutive 16-bit or 32-bit data', {'operation': 'word_data_sum'}),
    ('ZPUSH', r'temporarily batch-stores the present value of the index registers V0 to V7 and Z0 to Z7',
     {'operation': 'save_index_registers', 'registers': ['V0-V7', 'Z0-Z7']}),
    ('ZPOP', r'restores the contents of the index registers', {'operation': 'restore_index_registers'}),
    ('DSW', r'reads the set value of digital switches', {'operation': 'digital_switch_read'}),
    ('ARWS', r'inputs data through arrow switches', {'operation': 'arrow_key_input'}),
    ('DI', r'disables interrupts after interrupts were enabled', {'operation': 'disable_interrupts'}),
    ('EI', r'enables interrupts in PLCs', {'operation': 'enable_interrupts'}),
    ('IRET', r'returns the program execution from an interrupt routine', {'operation': 'return_from_interrupt'}),
    ('SRET', r'returns the program execution from a subroutine', {'operation': 'return_from_subroutine'}),
    ('FOR', r'specifies the number of repetition of the loop', {'operation': 'loop_start'}),
    ('NEXT', r'loop between FOR and NEXT instructions? is repeated', {'operation': 'loop_end'}),
)


# These predicates were read from the legacy manuals' own Function cells. They
# describe an operation, without inventing its unstated first-scan/disabled
# behaviour. The exact table is selected before any predicate is evaluated.
LEGACY_OPERATION_REVIEW = (
    ('CJ', r'Jumps to the identified pointer position', {'operation': 'conditional_jump'}),
    ('CALL', r'Executes the subroutine program starting at the identified pointer position', {'operation': 'call_subroutine'}),
    ('SRET', r'Returns operation from a subroutine program', {'operation': 'return_from_subroutine'}),
    ('FEND', r'indicate the end of the main program block', {'operation': 'main_program_end'}),
    ('WDT', r'refresh the watch ?dog timer during a program scan', {'operation': 'watchdog_refresh'}),
    ('CMP', r'Compares two data values', {'operation': 'compare_data_values'}),
    ('ZCP', r'Compares a data value against a data range', {'operation': 'compare_value_to_range'}),
    ('FMOV', r'Copies a single data device to a range of destination devices', {'operation': 'fill_destination_range'}),
    ('XCH', r'Data in the designated devices is exchanged', {'operation': 'exchange_device_values'}),
    ('INC', r'designated device is incremented by 1', {'operation': 'increment', 'increment': 1}),
    ('DEC', r'designated device is decremented by 1', {'operation': 'decrement', 'decrement': 1}),
    ('NEG', r'Logically inverts the contents of the designated device', {'operation': 'negate_device_data'}),
    ('ROR ROL RCR RCL', r'(?:bit pattern|contents) of the destination device (?:is|are) rotated', {'operation': 'rotate_device_data'}),
    ('SFTR SFTL', r'source devices are copied to a controlled bit stack', {'operation': 'shift_bit_stack'}),
    ('WSFR WSFL', r'source devices are copied to a controlled word stack', {'operation': 'shift_word_stack'}),
    ('SFWR', r'creates and builds a FIFO stack', {'operation': 'write_fifo_stack'}),
    ('SFRD', r'reads and reduces FIFO stack', {'operation': 'read_fifo_stack'}),
    ('BON', r'status of the specified bit in the source device is indicated at the destination', {'operation': 'read_specified_bit'}),
    ('ANR', r'lowest active annunciator is reset on every operation', {'operation': 'reset_lowest_active_annunciator'}),
    ('FLT', r'convert data to and from floating point format', {'operation': 'floating_point_conversion'}),
    ('REF', r'Forces an immediate update of inputs or outputs as specified', {'operation': 'immediate_io_refresh'}),
    ('REFF', r'Inputs are refreshed, and their input filters are reset', {'operation': 'input_refresh_and_filter_update'}),
    ('MTR', r'Multiplexes a bank of inputs into a number of sets of devices', {'operation': 'multiplex_input_bank'}),
    ('HSCS', r'Sets the selected output when the specified high speed counter value equals the test value', {'operation': 'high_speed_counter_compare_set'}),
    ('HSCR', r'Resets the selected output when the specified high speed counter equals the test value', {'operation': 'high_speed_counter_compare_reset'}),
    ('HSZ', r'current value of a high speed counter is checked against a specified range', {'operation': 'high_speed_counter_range_compare'}),
    ('SPD', r'Detects the number of .encoder. pulses in a given time frame', {'operation': 'measure_pulse_count_in_interval'}),
    ('PLSY', r'Outputs a specified number of pulses at a set frequency', {'operation': 'pulse_output'}),
    ('PWM', r'Generates a pulse train with defined pulse characteristics', {'operation': 'pulse_width_modulation'}),
    ('PLSR', r'Outputs a specified number of pulses, ramping up to a set frequency and back down to stop', {'operation': 'pulse_output_with_ramp'}),
    ('IST', r'Automatically sets up a multi-mode STL operating system', {'operation': 'initialize_step_ladder_modes'}),
    ('SER', r'Generates a list of statistics about a single data value', {'operation': 'data_stack_search_statistics'}),
    ('ABSD', r'Generates multiple output patterns in response to counter data', {'operation': 'counter_multiple_output_patterns'}),
    ('INCD', r'Generates a single output sequence in response to counter data', {'operation': 'counter_output_sequence'}),
    ('ALT', r'assigned device is inverted on every operation', {'operation': 'bit_state_inversion'}),
    ('RAMP', r'Ramps a device from one value to another in the specified number of steps', {'operation': 'ramp_device_value'}),
    ('SORT', r'Data in a defined table can be sorted on selected fields while retaining record integrity', {'operation': 'sort_records', 'record_integrity': 'preserved'}),
    ('HKY', r'hexadecimal key ?pad|hexadecimal keyboard|hexadecimal 16 key|create a numeric keyboard with 6 function keys', {'operation': 'hexadecimal_keypad_input'}),
    ('DSW', r'Multiplexed reading of n sets of digital \(BCD\) thumbwheels', {'operation': 'read_bcd_thumbwheels'}),
    ('SEGD', r'Hex data is decoded into a format used to drive seven segment displays', {'operation': 'decode_seven_segment'}),
    ('SEGL', r'Writes data to multiplexed single digit displays', {'operation': 'multiplexed_display_output'}),
    ('ARWS', r'Creates a user defined, \(4 key\) numeric data entry panel', {'operation': 'numeric_entry_panel', 'key_count': 4}),
    ('PR', r'Outputs ASCII data', {'operation': 'ascii_data_output'}),
    ('FROM', r'Read data from K,? the buffer No memories|Read data from the buffer memories', {'operation': 'buffer_memory_read'}),
    ('TO', r'Writes data to the buffer memories', {'operation': 'buffer_memory_write'}),
    ('RS', r'control serial communications from/to the programmable controller', {'operation': 'serial_communication'}),
    ('PRUN', r'control the FX parallel link adapters', {'operation': 'parallel_link_data_transfer'}),
    ('VRSC', r'Reads the set position value, 0 to 10', {'operation': 'potentiometer_scale_read', 'min': 0, 'max': 10}),
    ('PID', r'calculates a corrective action to a specified level based on PID control', {'operation': 'pid_control'}),
    ('ANRD', r'read the F series analog module', {'operation': 'legacy_analog_module_read', 'required_adapter': 'FX2-24EI'}),
    ('RMST', r'start the F series CAM module', {'operation': 'legacy_cam_module_start', 'required_adapter': 'FX2-24EI'}),
    ('RMWR', r'Disables outputs.*F series CAM module|sends output disable data to an F2-', {'operation': 'legacy_cam_output_disable', 'required_adapter': 'FX2-24EI'}),
    ('RMMN', r'Monitors data states of F series CAM module', {'operation': 'legacy_cam_data_monitor', 'required_adapter': 'FX2-24EI'}),
    ('BLK', r'Identifies a block number to the F2-30GM', {'operation': 'legacy_motion_block_selection', 'required_adapter': 'FX2-24EI'}),
    ('MCDE', r'Reads a set codes from the F2-30GM', {'operation': 'legacy_motion_code_read', 'required_adapter': 'FX2-24EI'}),
    ('ECMP', r'Compares two floating point values', {'operation': 'floating_point_compare'}),
    ('EZCP', r'Compares a float range with a float value', {'operation': 'floating_point_range_compare'}),
    ('EBCD', r'Converts floating point number format to scientific number format', {'operation': 'floating_point_to_scientific'}),
    ('EBIN', r'Converts scientific number format to floating point number format', {'operation': 'scientific_to_floating_point'}),
    ('ESUB', r'Subtracts one floating point number from another', {'operation': 'floating_point_subtraction'}),
    ('EMUL', r'Multiplies two floating point numbers together', {'operation': 'floating_point_multiplication'}),
    ('EDIV', r'Divides one floating point number by another', {'operation': 'floating_point_division'}),
    ('INT', r'Converts a number from floating point format to decimal format', {'operation': 'floating_point_to_integer'}),
    ('SIN', r'Calculates the sine of a floating point value', {'operation': 'sine'}),
    ('COS', r'Calculates the cosine of a floating point value', {'operation': 'cosine'}),
    ('TAN', r'Calculates the tangent of a floating point value', {'operation': 'tangent'}),
    ('SWAP', r'high and low byte of the designated devices are exchanged', {'operation': 'swap_bytes'}),
    ('ZRN', r'Return to zero home point after machine ON or initial setting', {'operation': 'zero_return'}),
    ('PLSV', r'Variable speed pulse output', {'operation': 'variable_speed_pulse_output'}),
    ('DRVI', r'Increment positioning', {'operation': 'incremental_positioning'}),
    ('DRVA', r'Absolute positioning', {'operation': 'absolute_positioning'}),
    ('TCMP', r'Compares two times', {'operation': 'compare_times'}),
    ('GRY', r'Calculates the gray code value of an integer', {'operation': 'integer_to_gray_code'}),
    ('GBIN', r'Calculates the integer value of a gray code', {'operation': 'gray_code_to_integer'}),
    ('RD3A', r'Analog block read for FX0N-3A, FX2N-2AD', {'operation': 'analog_block_read', 'hardware': ['FX0N-3A', 'FX2N-2AD']}),
    ('WR3A', r'Write data to analog block FX0N-3A, FX2N- ?2AD, FX2N-2DA', {'operation': 'analog_block_write', 'hardware': ['FX0N-3A', 'FX2N-2AD', 'FX2N-2DA']}),
    ('EXTR', r'External ROM instruction,? execution commands', {'operation': 'external_rom_function_dispatch'}),
    ('OUT_T', r'An output is generated when a set time expires', {'operation': 'timer_set_time_output'}),
    ('ORB', r'connects parallel blocks|parallel connection of (?:circuit blocks|multiple contact circuits)', {'operation': 'parallel_block_connection'}),
    ('ANB', r'connects serial blocks|serial connection of (?:circuit blocks|multiple parallel circuits)', {'operation': 'series_block_connection'}),
)


def _operation_facts(record, text):
    flat = _normalised(text)
    root = _root(record['opcode'])
    found = []
    for index, (forms, predicate, value) in enumerate((*OPERATION_REVIEW, *LEGACY_OPERATION_REVIEW)):
        if root not in {_root(form) for form in forms.split()}:
            continue
        if re.search(predicate, flat, re.I):
            found.append(('effects.documented_operation', 'authored:applied:operation:%s' % index,
                          {**value, 'knowledge_scope': 'operation_summary',
                           'executor_capability': 'not_established'}))
    return found


SECTION_OWNERS = {
    ('fxcpu_basic_applied_m', '12.6.2 Table high speed comparison mode (M8130)'): 'DHSZ',
    ('fxcpu_basic_applied_m', '12.6.3 Frequency control mode (DHSZ, DPLSY) (M8132)'): 'DHSZ',
}

# These sections were read in full. Their declarations can be authored, while
# the missing operation/state transition is not stated by this section. This is
# distinct from a sentence that exists but has not yet been extracted.
SOURCE_GAP_REVIEW = {
    ('fx3_programming_r', '34.2.1 Detailed explanation of setting data'):
        {'execution': 'subsection_defines_setting_selectors_not_execution'},
    ('fxcpu_basic_applied_m', '6.2 STL'):
        {'effects': 'section_defines_representation_not_complete_state_transition'},
    ('fxcpu_basic_applied_m', '6.3 RET'):
        {'effects': 'section_defines_representation_not_complete_state_transition'},
}


def _section_specific_facts(record, text):
    """Subsection modes and selectors which an opcode-only review would lose."""
    flat = _normalised(text)
    section = (record['manual_id'], record['section'])
    if section == ('fx3_programming_r', '34.2.1 Detailed explanation of setting data'):
        required = ('K-1(H0FFFF)', 'K0 to K63', 'K512(H200)', 'K256(H100)',
                    'but does not delete the file itself', 'K1 : ch1', 'K2 : ch2')
        if not all(witness in flat for witness in required):
            return []
        return [
            ('parameters.typed_operands', 'authored:applied:fldel_selectors',
             {'operands': [{'name': 'S1', 'data_type': 'INT16', 'direction': 'input', 'role': 'file_id'},
                           {'name': 'S2', 'data_type': 'INT16', 'direction': 'input', 'role': 'deletion_or_format_method'},
                           {'name': 'n', 'data_type': 'INT16', 'direction': 'input', 'role': 'channel'}]}),
            ('effects.file_selection', 'authored:applied:fldel_selection',
             {'file_id': [{'value': -1, 'effect': 'delete_all_files'},
                          {'min': 0, 'max': 63, 'effect': 'delete_file_associated_with_id'},
                          {'value': 512, 'effect': 'format_card', 'format_selector': 256, 'format': 'FAT16'}],
              'deletion_method': [{'value': 0, 'effect': 'delete_file'},
                                  {'value': 1, 'effect': 'delete_name_id_association_only'}],
              'file_id_zero_exception': 'file_deleted_regardless_of_deletion_method',
              'channels': [1, 2]}),
            ('constraints.resource_conflict', 'authored:applied:fldel_port',
             {'resource': 'same_port', 'cannot_share_with': ['RS', 'RS2', 'ADPRW', 'IVCK-IVMC'],
              'applies_to': 'FLCRT-FLSTRD'}),
        ]
    if section not in SECTION_OWNERS:
        return []
    if '12.6.2' in record['section'] and all(w in flat for w in
            ('M8130', 'one line in the comparison table uses four devices',
             'K1 to K128', 'directly controlled as interrupt')):
        return [
            ('execution.mode', 'authored:applied:dhsz_table_mode',
             {'mode': 'table_high_speed_comparison', 'destination_selector': 'M8130',
              'cpu_version_requirements': [{'model': 'FXU', 'minimum_version': '3.07'}]}),
            ('parameters.typed_operands', 'authored:applied:dhsz_table_operands',
             {'operands': [{'name': 's1', 'data_type': 'ANY32', 'role': 'table_head'},
                           {'name': 's2', 'data_type': 'ANY32', 'role': 'table_line_count'},
                           {'name': 's', 'data_type': 'ANY32', 'role': 'high_speed_counter'},
                           {'name': 'd', 'data_type': 'BOOL', 'role': 'mode_selector'}],
              'line_count': {'min': 1, 'max': 128}, 'words_per_line': 4,
              'comparison_width_bits': 32, 'output_number_encoding': 'hexadecimal'}),
            ('effects.table_output', 'authored:applied:dhsz_table_output',
             {'output_actions': [{'value': 1, 'action': 'set'}, {'value': 0, 'action': 'reset'}],
              'update_context': 'interrupt', 'table_counter': 'D8130'}),
        ]
    if '12.6.3' in record['section'] and all(w in flat for w in
            ('M8132', 'DPLSY instruction is combined', '1 ≤ K, H ≤ 128', 'D8131')):
        return [
            ('execution.mode', 'authored:applied:dhsz_frequency_mode',
             {'mode': 'frequency_control', 'destination_selector': 'M8132', 'required_instruction': 'DPLSY',
              'cpu_version_requirements': [{'model': 'FXU', 'minimum_version': '3.07'}]}),
            ('parameters.typed_operands', 'authored:applied:dhsz_frequency_operands',
             {'operands': [{'name': 's1', 'data_type': 'ANY32', 'role': 'table_head'},
                           {'name': 's2', 'data_type': 'ANY32', 'role': 'table_line_count'},
                           {'name': 's', 'data_type': 'ANY32', 'role': 'high_speed_counter'},
                           {'name': 'd', 'data_type': 'BOOL', 'role': 'mode_selector'}],
              'head_device_family': 'D', 'line_count': {'min': 1, 'max': 128},
              'line_count_literal_families': ['K', 'H']}),
            ('effects.frequency_control', 'authored:applied:dhsz_frequency_effect',
             {'operation': 'table_frequency_control', 'table_counter': 'D8131',
              'pulse_output_instruction': 'DPLSY', 'user_changeable': 'pulse_output_only'}),
        ]
    return []


def _legacy_detail_facts(record, text, opcode, models):
    """Source predicates for state, memory and hardware the function cell omits."""
    if not record['manual_id'].startswith('jy992d'):
        return []
    flat = _normalised(text)
    rows = []
    root = _root(opcode)

    def add(identity, dimension, value, *witnesses, selected=None, conditions=()):
        if not all(re.search(witness, flat, re.I) for witness in witnesses):
            return
        selected_models = [model for model in models if selected is None or model in selected]
        if not selected_models:
            return
        scoped_record = dict(record, conditions=[*record.get('conditions', []), *conditions])
        rows.append(_fact('authored:applied:legacy:' + identity, dimension, value,
                          opcode, scoped_record, selected_models, languages=['ladder']))

    if root == 'PLSY':
        add('pulse_count_complete', 'effects.completion_flag',
            {'device': 'M8029', 'set_when': 'specified_number_of_pulses_completed'},
            r'M8029.*turned ON when the specified number of pulses has been completed')
        add('pulse_disabled_reset', 'execution.disabled_state',
            {'when': 'instruction_deenergized', 'pulse_count': 'reset', 'completion_flag': 'reset',
             'completion_device': 'M8029'},
            r'pulse count and completion flag \(M8029\) are reset when the PLSY instruction is de-energized')
        add('zero_count_pulses', 'effects.pulse_output',
            {'pulse_count': 'unbounded_while_energized', 'stop_when': 'instruction_deenergized'},
            r'zero.*PLSY instruction will continue generating pulses for as long as the instruction is energized',
            conditions=[{'operand': 'S2', 'operator': 'equals', 'value': 0}])
        add('pulse_duty', 'effects.pulse_train',
            {'on_percent': 50, 'off_percent': 50}, r'50% duty cycle', r'OFF for the remaining 50%')
        add('pulse_scan_independence', 'execution.output_cycle_timing',
            {'controlled_by': 'interrupt', 'affected_by_program_scan_time': False},
            r'actual output is controlled by interrupt handling',
            r'output cycle is NOT affected by the scan time')
        add('pulse_operand_change', 'execution.operand_update_timing',
            {'operand': 'S2', 'write_during_execution': 'permitted',
             'new_value_effective_after': ['current_operation_completed', 'drive_contact_removed']},
            r'data in operands S\s*1 and S2 may be changed during execution',
            r'new data in S\s*2 will not become effective until the current operation has been completed')
        add('pulse_transistor_output', 'resources.output_hardware',
            {'output_type': 'transistor', 'requirement': 'use_transistor_output_unit'},
            r'transistor output units should be used with this instruction')
        add('pulse_monitor_registers', 'memory.pulse_monitor',
            {'channels': [{'output': 'Y0', 'low_word': 'D8140', 'high_word': 'D8141'},
                          {'output': 'Y1', 'low_word': 'D8142', 'high_word': 'D8143'}],
             'total_low_word': 'D8136', 'total_high_word': 'D8137'},
            r'FX2N and FX2NC units can monitor', r'D8140.*D8141.*D8142.*D8143', r'D8136.*D8137',
            selected={'FX2N', 'FX2NC'})
    elif root == 'PID':
        add('pid_operand_roles', 'parameters.operand_roles',
            {'operands': [{'name': 'S1', 'role': 'set_point', 'direction': 'input'},
                          {'name': 'S2', 'role': 'current_process_value', 'direction': 'input'},
                          {'name': 'S3', 'role': 'parameter_stack_head', 'direction': 'in_out'},
                          {'name': 'D', 'role': 'corrected_output', 'direction': 'output'}]},
            r'current value \(S\s*2\)', r'predefined set value \(S\s*1\)',
            r'corrected output value in destination device \(D\)', r'25 consecutive data registers S3\+0 through S3\+24')
        add('pid_parameter_stack', 'memory.parameter_layout',
            {'head_operand': 'S3', 'register_count': 25, 'first_offset': 0, 'last_offset': 24,
             'loops_must_not_overlap': True},
            r'25 consecutive data registers S3\+0 through S3\+24',
            r'each PID loop must not have conflicting data registers')
        add('pid_stack_head_limit', 'constraints.operand_range',
            {'operand': 'S3', 'device_family': 'D', 'max_head_number': 975, 'span_registers': 25},
            r'head address of this data stack cannot be greater than D975')
        add('pid_diagnostic_devices', 'constraints.diagnostic_devices',
            {'flag_device': 'M8067', 'code_device': 'D8067', 'exclusive_to_instruction': False},
            r'error codes.*flagged by M8067.*error code being stored in D8067',
            r'error devices are not exclusive to the PID instruction')
        add('pid_fx1s_communications', 'resources.hardware_dependency',
            {'analog_capability': 'absent', 'pid_data_interface': 'RS232_communication'},
            r'FX\s*1S does not have analog capabilities.*necessary to use RS232', selected={'FX1S'})
        add('pid_proportional_required', 'constraints.control_mode',
            {'permitted_modes': ['P', 'PI', 'PD', 'PID'], 'proportional_element_required': True},
            r'in all situations there must be a proportional or .P. element to the loop',
            r'Proportional effect only', r'Proportional and integral effect', r'Proportional and derivative effect')
        add('pid_input_filter', 'effects.input_filter',
            {'zero': 'filter_disabled', 'higher_percentage': 'longer_lag',
             'zero_input_value': 'unfiltered_current_value'},
            r'greater the filter percentage is set the longer the lag time',
            r'input filter is set to zero.*removes all filtering')
        # The source table's parameter rows, including units, zero-disable
        # semantics and reserved/read-only regions. The numeric setup example on
        # another page does not override this table.
        setup = (
            r'Sampling time.*1 to 32767.*msec', r'Integral time.*100 msec',
            r'Derivative.*1 to 100%', r'Derivative time.*10 msec',
            r'Input filter.*0 to 99%', r'Proportional.*32767%',
            r'Forward operation\s*\(0\).*Reverse operation\s*\(1\)',
            r'Reserved for use for the internal processing', r'Alarm flags.*Read Only',
        )
        add('pid_setup_layout', 'memory.parameter_layout',
            {'head_operand': 'S3',
             'registers': [{'offset': 0, 'role': 'sampling_time', 'min': 1, 'max': 32767, 'unit_ms': 1},
                           {'offset': 1, 'role': 'action_and_alarm_control',
                            'bits': [{'bit': 0, 'zero': 'forward', 'one': 'reverse'},
                                     {'bit': 1, 'zero': 'process_alarm_disabled', 'one': 'process_alarm_enabled'},
                                     {'bit': 2, 'zero': 'output_alarm_disabled', 'one': 'output_alarm_enabled'}]},
                           {'offset': 2, 'role': 'input_filter_percent', 'min': 0, 'max': 99},
                           {'offset': 3, 'role': 'proportional_gain_percent', 'min': 1, 'max': 32767},
                           {'offset': 4, 'role': 'integral_time', 'min': 0, 'max': 32767, 'unit_ms': 100,
                            'zero_effect': 'integral_disabled'},
                           {'offset': 5, 'role': 'derivative_gain_percent', 'min': 1, 'max': 100},
                           {'offset': 6, 'role': 'derivative_time', 'min': 0, 'max': 32767, 'unit_ms': 10,
                            'zero_effect': 'derivative_disabled'},
                           {'first_offset': 7, 'last_offset': 19, 'role': 'internal_processing_reserved'},
                           {'offset': 20, 'role': 'process_high_alarm_limit', 'enable_register_offset': 1, 'enable_bit': 1},
                           {'offset': 21, 'role': 'process_low_alarm_limit', 'enable_register_offset': 1, 'enable_bit': 1},
                           {'offset': 22, 'role': 'output_positive_change_limit', 'enable_register_offset': 1, 'enable_bit': 2},
                           {'offset': 23, 'role': 'output_negative_change_limit', 'enable_register_offset': 1, 'enable_bit': 2},
                           {'offset': 24, 'role': 'alarm_flags', 'direction': 'read_only',
                            'bits': [{'bit': 0, 'condition': 'process_high_limit_exceeded'},
                                     {'bit': 1, 'condition': 'process_below_low_limit'},
                                     {'bit': 2, 'condition': 'excessive_positive_output_change'},
                                     {'bit': 3, 'condition': 'excessive_negative_output_change'}]}]}, *setup)
        # This printed example disagrees with the setup table's scale. It is a
        # source conflict, never a replacement numerical rule.
        if record['manual_id'] == 'jy992d88101e' and all(w in flat for w in
                ('4000 ms', 'K2000 D504', '1000 ms', 'K3000 D506')):
            conflict = _fact('authored:applied:legacy:pid_example_scale_conflict',
                             'constraints.source_conflict',
                             {'conflict': 'setup_example_values_disagree_with_parameter_units',
                              'integral': {'example_literal': 2000, 'example_label_ms': 4000, 'table_unit_ms': 100},
                              'derivative': {'example_literal': 3000, 'example_label_ms': 1000, 'table_unit_ms': 10},
                              'resolution': 'not_established'}, opcode, record, models,
                             languages=['ladder'], status='conflict')
            rows.append(conflict)
    elif root == 'EXTR':
        add('external_rom_selection', 'execution.function_selection',
            {'selector_operand': 'S', 'selected_by_function': ['operation', 'width_bits', 'pulse_form'],
             'fixed_execution_form': 'not_established'},
            r'type of operation \(16 bit, 32 bit, pulse\) is determined from the instruction number')
        add('external_rom_hardware', 'resources.hardware_dependency',
            {'hardware': ['FX2N-ROM-E1', 'FX2NC-ROM-CE1'], 'minimum_cpu_version': '3.00'},
            r'FX2N-ROM-E1 and FX2NC-ROM-CE1 are only operable with FX2N and FX2NC units of V3.00 or later',
            selected={'FX2N', 'FX2NC'})
        add('external_rom_optional_operands', 'parameters.operand_roles',
            {'operands': [{'name': 'S', 'purpose_class': 'function_number', 'data_type': 'unknown'},
                          {'name': 'SD1', 'purpose_class': 'function_selected', 'data_type': 'unknown'},
                          {'name': 'SD2', 'purpose_class': 'function_selected', 'data_type': 'unknown'},
                          {'name': 'SD3', 'purpose_class': 'function_selected', 'data_type': 'unknown'}],
             'unused_operand_literal': 0, 'unused_operand_effect': 'ignored'},
            r'parameters SD1 to SD3 may not be required.*K0 should be written', r'K0 is ignored')
    return rows


def _manual_supported_models(source, manual_id):
    """Models the same manual marks supported elsewhere in its own sections."""
    rows = source.execute(
        "SELECT c.source_applicability_json AS applicability FROM instructions i "
        "JOIN chunks c ON c.id=i.chunk_id WHERE i.manual_id=?", (manual_id,)).fetchall()
    supported = []
    for row in rows:
        try:
            marks = (json.loads(row['applicability'] or '{}') or {}).get('model_marks') or []
        except (TypeError, ValueError):
            continue
        for mark in marks:
            if mark.get('state') == 'supported' and mark.get('model') not in supported:
                supported.append(mark['model'])
    return supported


def _manual_scope(source, manual_id):
    row = source.execute("SELECT plc_models FROM manuals WHERE manual_id=?", (manual_id,)).fetchone()
    if row is None or not row[0]:
        return []
    try:
        models = json.loads(row[0])
    except (TypeError, ValueError):
        models = re.split(r'[/,;]+', str(row[0]))
    if isinstance(models, str):
        models = re.split(r'[/,;]+', models)
    cleaned = []
    for model in models:
        token = str(model).strip().upper()
        if re.fullmatch(r'FX[0-9A-Z]+(?:[A-Z]{0,2})?', token):
            cleaned.append(token)
    return list(dict.fromkeys(cleaned))


def _declared_operands(raw):
    """Operand letters the record itself declares (used to keep mappings bound)."""
    try:
        entries = json.loads(raw or '[]')
    except (TypeError, ValueError):
        return []
    names = []
    for entry in entries:
        symbol = str(entry.get('position') or entry.get('symbol') or '').strip().upper()
        if symbol:
            names.append(symbol)
    return names


def _heading_symbols(section):
    """Symbols a reviewed section heading declares, in heading order."""
    legacy = _LEGACY_HEADING.match(section or '')
    if legacy:
        return [legacy['name'].upper()]
    from knowledge.instruction_compiler import instruction_heading_names
    names = [name.upper() for name in instruction_heading_names(section or '')]
    return [name for name in names if re.fullmatch(r"[A-Z][A-Z0-9_<>.$@+*/=!\-]*", name)]


def _chunk_records(source, manuals, known, known_pages=()):
    """Sections the instruction index never listed, taken from the chunks themselves."""
    rows = source.execute(
        "SELECT c.id AS id,c.manual_id AS manual_id,c.manual_number AS manual_number,c.revision AS revision,"
        "c.section AS section,c.pdf_page AS pdf_page,c.source_pages_json AS pages,"
        "c.source_applicability_json AS applicability,c.text AS text "
        "FROM chunks c WHERE c.manual_id IN (%s) ORDER BY c.manual_id,c.id" % ','.join('?' * len(manuals)),
        tuple(sorted(manuals))).fetchall()
    grouped = {}
    for row in rows:
        if not row['section']:
            continue
        grouped.setdefault((row['manual_id'], row['section']), []).append(row)
    for (manual_id, section), group in sorted(grouped.items()):
        if (manual_id, section) in known:
            continue
        names = _heading_symbols(section)
        if not names:
            continue
        applicability, pages = {}, set()
        for row in group:
            try:
                candidate = json.loads(row['applicability'] or '{}')
            except ValueError:
                candidate = {}
            if not applicability and candidate.get('basis') == 'instruction_header' and candidate.get('model_marks'):
                applicability = candidate
            try:
                pages.update(int(page) for page in (json.loads(row['pages'] or 'null') or []))
            except (ValueError, TypeError):
                pass
            if row['pdf_page']:
                pages.add(int(row['pdf_page']))
        # Several independent instructions may share a physical page. The
        # section heading, not page exclusivity, determines source ownership.
        text = '\n'.join(re.sub(r'\[PAGE \d+ LAYOUT\].*?(?=\[PAGE |\Z)', '', row['text'] or '', flags=re.S)
                          for row in group)
        scope_models = _manual_supported_models(source, manual_id)
        yield {'id': 'chunk:%s:%s' % (manual_id, group[0]['id']), 'opcode': names[0], 'title': '',
               'manual_id': manual_id, 'manual_number': group[0]['manual_number'],
               'revision': group[0]['revision'], 'pdf_pages': sorted(pages), 'section': section,
               'text': text, 'applicability': applicability,
               'manual_models': scope_models or _manual_scope(source, manual_id),
               'manual_scope_basis': 'sections_of_same_manual' if scope_models else 'manual_catalogue',
               'declared_operands': [], 'chunk_derived': True}


def _instruction_records(source, manuals):
    section_chunks = {}
    for chunk in source.execute(
            "SELECT id,manual_id,section,pdf_page,source_pages_json,text FROM chunks "
            "WHERE manual_id IN (%s) ORDER BY id" % ','.join('?' * len(manuals)),
            tuple(sorted(manuals))):
        section_chunks.setdefault((chunk['manual_id'], chunk['section']), []).append(chunk)
    rows = source.execute(
        "SELECT i.id AS id,i.opcode AS opcode,i.title AS title,i.manual_id AS manual_id,"
        "i.manual_number AS manual_number,i.revision AS revision,i.source_pages_json AS pages,"
        "i.operands_json AS operands_json,"
        "c.text AS text,c.source_applicability_json AS applicability,c.section AS section "
        "FROM instructions i JOIN chunks c ON c.id=i.chunk_id "
        "WHERE i.manual_id IN (%s) ORDER BY i.manual_id,i.id" % ','.join('?' * len(manuals)),
        tuple(sorted(manuals))).fetchall()
    seen = set()
    for row in rows:
        pages = set(json.loads(row['pages'] or 'null') or [])
        chunks = section_chunks.get((row['manual_id'], row['section']), [])
        for chunk in chunks:
            pages.update(json.loads(chunk['source_pages_json'] or 'null') or [])
            if chunk['pdf_page']:
                pages.add(chunk['pdf_page'])
        text = '\n'.join(dict.fromkeys(chunk['text'] or '' for chunk in chunks)) or row['text'] or ''
        heading_names = _heading_symbols(row['section'])
        opcode = (heading_names[0] if len(heading_names) == 1 else str(row['opcode']).upper())
        if (row['manual_id'] == 'fx3_programming_r' and opcode == 'MODBUS'
                and re.search(r'16-bit operation\s*\(ADPRW\)', text)):
            opcode = 'ADPRW'
        identity = (row['manual_id'], row['section'], opcode)
        if identity in seen:
            continue
        seen.add(identity)
        yield {'id': row['id'], 'opcode': opcode, 'title': row['title'] or '',
               'manual_id': row['manual_id'], 'manual_number': row['manual_number'],
               'revision': row['revision'], 'pdf_pages': sorted(int(page) for page in pages),
               'section': row['section'] or '',
               'text': re.sub(r'\[PAGE \d+ LAYOUT\].*?(?=\[PAGE |\Z)', '', text, flags=re.S),
               'applicability': json.loads(row['applicability'] or '{}'),
               'manual_models': _manual_supported_models(source, row['manual_id'])
               or _manual_scope(source, row['manual_id']),
               'manual_scope_basis': 'sections_of_same_manual' if _manual_supported_models(source, row['manual_id'])
               else 'manual_catalogue',
               'declared_operands': _declared_operands(row['operands_json'])}


def _models(applicability, manual_models, scope_basis='manual_catalogue'):
    """CPU scope from the section header, else the manual catalogue."""
    if applicability.get('basis') == 'instruction_header':
        marks = applicability.get('model_marks') or []
        supported = [mark['model'] for mark in marks if mark['state'] == 'supported']
        conditional = [mark['model'] for mark in marks if mark['state'] == 'conditional']
        if supported or conditional:
            return supported + conditional, None, conditional, HEADER_CONDITION
    if manual_models:
        return list(manual_models), None, list(manual_models), SCOPE_CONDITION
    return None, 'cpu_scope_not_declared_for_section', [], None


def _source_reference(record):
    return {'id': str(record['id']), 'manual_id': record['manual_id'],
            'manual_number': record['manual_number'], 'revision': record['revision'],
            'pdf_pages': record['pdf_pages']}


def _fact(identity, dimension, value, opcode, record, models, *, languages=None,
          depends_on=(), status='source_verified', unresolved=None):
    suffix = '@' + record['manual_id']
    if record.get('identity_suffix'):
        # Two documented sections of one opcode are two facts, not a conflict.
        suffix += ':' + record['identity_suffix']
    scope = {'models': list(models), 'forms': [opcode]}
    if languages is None:
        languages = (['ladder', 'fbd', 'st'] if record['manual_id'] == 'fxcpu_basic_applied_m'
                     else ['ladder'])
    if languages:
        scope['languages'] = list(languages)
    fact = {'id': identity + suffix, 'dimension': dimension, 'value': value, 'status': status,
            'verification': 'source_checked', 'sources': [_source_reference(record)], 'scope': scope}
    if record.get('conditions'):
        fact['conditions'] = record['conditions']
    if depends_on:
        fact['depends_on'] = [identifier if identifier.endswith(suffix) else identifier + suffix
                              for identifier in depends_on]
    if unresolved:
        fact['note'] = 'unresolved_parameters: ' + ','.join(sorted(unresolved))
    return fact


def _for_model(rows, model, conditional, condition=HEADER_CONDITION):
    """Bind rows to one CPU; a version-conditioned CPU never becomes verified."""
    bound = []
    for row in rows:
        copy = {'id': row['id'], 'dimension': row['dimension'], 'value': _parameter_value_for_model(row['value'], model),
                'status': row['status'], 'verification': row['verification'],
                'sources': row['sources'], 'scope': {**row['scope'], 'models': [model]}}
        for optional in ('depends_on', 'note', 'conditions'):
            if optional in row:
                copy[optional] = row[optional]
        if model in conditional:
            if copy['status'] in {'source_verified', 'candidate_evidence'}:
                copy['status'] = 'declared_unverified'
            copy['conditions'] = [*copy.get('conditions', []), dict(condition)]
        bound.append(copy)
    return bound


def _parameter_value_for_model(value, model):
    if isinstance(value, list):
        return [_parameter_value_for_model(item, model) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _parameter_value_for_model(item, model) for key, item in value.items()}
    if model in result.get('types_by_model', {}):
        for field in ('type', 'data_type'):
            if field in result:
                result[field] = result['types_by_model'][model]
    return result


def _native_parameter_conventions(source, manual_id):
    """Read the same manual's explicit symbol and width convention."""
    pages = {'fx3_programming_r': (176, 177), 'jy992d88101e': (121,),
             'jy992d48301j': (127,)}.get(manual_id)
    if not pages or not source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='page_artifacts'").fetchone():
        return {}
    rows = source.execute('SELECT pdf_page, clean_text FROM page_artifacts WHERE manual_id=? '
                          'AND pdf_page IN (%s)' % ','.join('?' for _ in pages),
                          (manual_id, *pages)).fetchall()
    text = _normalised(' '.join(row['clean_text'] or '' for row in rows))
    role_witness = (re.search(r'D-\s*destination, S-\s*source', text, re.I)
                    or (manual_id == 'fx3_programming_r'
                        and 'An operand whose contents do not change by execution of the instruction is called "source"' in text
                        and 'An operand whose contents change by execution of the instruction is called "destination"' in text))
    width_witness = (re.search(r'32 bit operation requires a [‘\']D[’\'] to be added before the mnemonic', text)
                     or 'In a 32-bit type instruction, the symbol "D" is added' in text)
    return {'source_destination_symbols': bool(role_witness), 'd_prefix_is_32_bit': bool(width_witness),
            'pdf_pages': sorted(row['pdf_page'] for row in rows)}


def _variable_types(text):
    """Documented EN/ENO variable rows, if the section prints them."""
    rows = {}
    for match in _VARIABLE.finditer(text):
        kind = match.group('type').strip().split()[0]
        rows[match.group('name')] = {'type': 'BOOL' if kind == 'Bit' else
                                     re.sub(r'[^A-Za-z0-9]+', '_', kind).upper(),
                                     'direction': 'input' if match.group('name') == 'EN' else 'output'}
    return rows


def _parameter_source_pages(source, record):
    """Original page structures for this section, never another manual's ABI."""
    if not source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='page_artifacts'").fetchone():
        return []
    pages = []
    for page in record.get('pdf_pages', ()):
        row = source.execute('SELECT layout_text, word_geometry_json, instruction_opcode, section, visual_json '
                             'FROM page_artifacts WHERE manual_id=? AND pdf_page=?',
                             (record['manual_id'], page)).fetchone()
        if row is None:
            continue
        try:
            words = json.loads(row['word_geometry_json'] or '[]')
        except (TypeError, ValueError):
            words = []
        top, bottom = -1, 10000
        number = re.match(r'\d+(?:\.\d+)+', record.get('section', ''))
        if number:
            headings = [word for word in words if re.fullmatch(r'\d+(?:\.\d+)+', word['text'])
                        and word['x0'] < 120 and word['top'] > 60
                        and word['bottom'] - word['top'] >= 10]
            own = [word['top'] for word in headings if word['text'] == number[0]]
            top = min(own) - 2 if own else -1
            following = [word['top'] for word in headings if word['top'] > top + 3
                         and word['text'] != number[0] and not word['text'].startswith(number[0] + '.')]
            bottom = min(following) if following else 10000
            words = [word for word in words if top < word['top'] < bottom]
        pages.append({'page': page, 'layout': row['layout_text'] or '', 'words': words,
                      'opcode': row['instruction_opcode'], 'section': row['section'],
                      'parameter_tables': [table for table in
                          (json.loads(row['visual_json'] or '{}') or {}).get('parameter_tables', [])
                          if top < table['bbox'][1] and table['bbox'][3] <= bottom]})
    return pages


def _parameter_lines(words, *, top=-1, bottom=10000, left=-1, right=550):
    lines = []
    for word in sorted((word for word in words if top < word['top'] < bottom
                        and left < word['x0'] < right), key=lambda word: (word['top'], word['x0'])):
        line = next((line for line in lines if abs(line[0]['top'] - word['top']) < 2), None)
        if line is None:
            lines.append([word])
        else:
            line.append(word)
    return [sorted(line, key=lambda word: word['x0']) for line in lines]


def _wrapped_array_element(word, words):
    for array in words:
        if (not array['text'].startswith('ARRAY') or not 2 < word['top'] - array['top'] < 20
                or abs(word['x0'] - array['x0']) >= 20):
            continue
        of_words = [item for item in words if item['text'] == 'OF' and
                    abs(item['top'] - array['top']) < 2 and 0 < item['x0'] - array['x0'] < 75]
        if of_words and not any(_documented_type(item['text']) != 'unknown' and
                                abs(item['top'] - array['top']) < 2 and
                                of_words[0]['x1'] <= item['x0'] < of_words[0]['x1'] + 45 for item in words):
            return True
    return False


def _variable_type_qualifications(record, raw_type):
    """A type-cell footnote stays CPU specific; an undecoded one stays pending."""
    result = {}
    for marker in sorted(set(re.findall(r'\*(\d+)', raw_type))):
        notes = re.finditer(r'\*' + re.escape(marker) + r'\.\s+(.+?)(?=\*\d+\.|SOURCE:|\[PAGE|$)',
                            _normalised(record['text'].replace('|', ' ')))
        matched = False
        for note in notes:
            override = re.search(r'In the case of (FX.+?) series, the data type of ([A-Z0-9_, ]+) '
                                 r'of 32-bit operation is (ANY\d+)\b', note[1])
            if override:
                models = re.findall(r'\bFX[A-Z0-9]+\b', override[1])
                result.setdefault('types_by_model', {}).update({model: override[3] for model in models})
                result['type_qualification_basis'] = 'same_type_cell_cpu_footnote_' + marker
                matched = True
            if re.search(r'When a constant \(K or H\) is specified, it is automatically converted into '
                         r'binary floating point \(real number\)', note[1]):
                result['constant_coercion'] = {'source_radices': ['K', 'H'], 'destination_type': 'REAL'}
                result['type_qualification_basis'] = 'same_type_cell_constant_conversion_footnote_' + marker
                matched = True
        if not matched:
            result.setdefault('unresolved_type_qualifications', []).append(int(marker))
    return result


def _documented_type(text, width='unknown'):
    text = _normalised(re.sub(r'\*\d+', '', text))
    if re.fullmatch(r'\d+', text):
        return 'unknown'
    if re.fullmatch(r'FLOAT\s*\(\s*Single\s+Precision\s*\)', text, re.I):
        return 'REAL'
    if re.fullmatch(r'Real(?:\s+number)?(?:\s*\(\s*Binary floating point\s*\))?|'
                    r'Real number\s*\(binary\)|(?:32-bit\s+)?Binary floating point', text, re.I):
        return 'REAL'
    if re.fullmatch(r'Character string(?:\s*\(only ASCII codes?\))?|String data', text, re.I):
        return 'STRING'
    if re.fullmatch(r'32-bit decimal floating point|Real number\s*\(decimal\)', text, re.I):
        return {'kind': 'scientific_notation', 'mantissa_bits': 16, 'exponent_bits': 16}
    if re.fullmatch(r'Device name', text, re.I):
        return {'kind': 'device_reference'}
    if re.fullmatch(r'Pointer number', text, re.I):
        return 'POINTER'
    union = re.fullmatch(r'(.+?)\s+or\s+(character string|16-bit binary)', text, re.I)
    if union:
        alternatives = [_documented_type(union[1], width), _documented_type(union[2], width)]
        if all(item != 'unknown' for item in alternatives):
            return {'kind': 'one_of', 'types': alternatives}
    bcd = re.fullmatch(r'([48])(?:/([48]))?-digit BCD', text, re.I)
    if bcd:
        bits = [int(digits) * 4 for digits in bcd.groups() if digits]
        size = width if width in bits else bits[0] if len(bits) == 1 else None
        return {'kind': 'packed_bcd', 'bits': size} if size else 'unknown'
    string = re.fullmatch(r'String(?:\s*\(\s*(\d+)\s*\))?', text, re.I)
    if string:
        return 'STRING' + ('(' + string[1] + ')' if string[1] else '')
    short_array = re.fullmatch(r'(ANY[A-Z0-9_]+)\s*\(\s*(\d+)\.\.(\d+)\s*\)', text)
    if short_array:
        return {'kind': 'array', 'index_min': int(short_array[2]), 'index_max': int(short_array[3]),
                'element_type': short_array[1]}
    array = re.fullmatch(r'ARRAY\s*\[\s*(-?\d+)\s*\.\.\s*(-?\d+)\s*\]\s*OF\s+(.+)', text, re.I)
    if array:
        element = _documented_type(array[3], width)
        return ({'kind': 'array', 'index_min': int(array[1]), 'index_max': int(array[2]),
                 'element_type': element} if element != 'unknown' else 'unknown')
    if re.fullmatch(r'(?:Bit|BOOL|Always TRUE)', text, re.I):
        return 'BOOL'
    if re.fullmatch(r'ANY[A-Z0-9_]*|BOOL|WORD|DWORD|INT|DINT|REAL|STRING|TIME|POINTER', text):
        return text
    binary = re.fullmatch(r'(?:(16|32|64)(?:\s*-?\s*bit)?(?:\s*or\s*(16|32|64)\s*-?\s*bit)?|' 
                          r'(16|32)\s*-\s*or\s*(32|64)\s*-\s*bit)\s*(binary|BCD|floating point|real number)?', text, re.I)
    if binary:
        sizes = {int(value) for value in binary.groups()[:4] if value}
        size = width if width in sizes else next(iter(sizes)) if len(sizes) == 1 else None
        kind = (binary[5] or 'binary').lower()
        if kind in {'floating point', 'real number'} and size == 32:
            return 'REAL'
        if size:
            return 'ANY' + str(size) if kind == 'binary' else {'kind': 'packed_bcd', 'bits': size}
    if re.fullmatch(r'Bit device|Bit data', text, re.I):
        return 'BOOL'
    return 'unknown'


def _cell_at(cells, x, y):
    matches = [cell for cell in cells if cell['bbox'][0] <= x <= cell['bbox'][2]
               and cell['bbox'][1] <= y <= cell['bbox'][3]]
    return matches[0] if len(matches) == 1 else None


def _parameter_cell_rows(page, width, *, structured):
    """Use actual merged-cell extents; an empty neighbouring cell is no proof."""
    result = []
    for table in page.get('parameter_tables', ()):
        cells = table['cells']
        description = next((cell for cell in cells if _normalised(cell['text']) == 'Description'), None)
        type_head = next((cell for cell in cells if re.fullmatch(r'Data [Tt]ype', _normalised(cell['text']))), None)
        variable = next((cell for cell in cells if _normalised(cell['text']) == ('Variable' if structured else 'Operand Type')
                         or not structured and _normalised(cell['text']) == 'Operand type'), None)
        if not description or not type_head or not variable:
            continue
        left, _, right, header_bottom = description['bbox']
        description_x = (left + right) / 2
        type_x = (type_head['bbox'][0] + type_head['bbox'][2]) / 2
        width_heads = [cell for cell in cells if re.fullmatch(r'(?:16|32)-bit operation', _normalised(cell['text']))]
        selected = [cell for cell in width_heads if _normalised(cell['text']).startswith(str(width) + '-bit')]
        if len(selected) == 1:
            type_x = (selected[0]['bbox'][0] + selected[0]['bbox'][2]) / 2
        for cell in cells:
            x0, y0, x1, y1 = cell['bbox']
            y = (y0 + y1) / 2
            if not x0 <= description_x <= x1 or y0 < header_bottom - 1 or not cell['text'].strip():
                continue
            kind_cell = _cell_at(cells, type_x, y)
            if kind_cell is None:
                continue
            kind = _documented_type(kind_cell['text'], width)
            alternatives = re.fullmatch(r'(16|32)-?\s*or\s*(32|64)-bit\s+binary', _normalised(kind_cell['text']), re.I)
            if alternatives and width in {16, 32}:
                kind = 'ANY' + alternatives[1 if width == 16 else 2]
            variable_cells = [item for item in cells if item['bbox'][0] >= variable['bbox'][0] - 1
                              and item['bbox'][2] <= left + 1 and item['bbox'][1] <= y <= item['bbox'][3]]
            words = [word['text'] for word in page['words'] if variable['bbox'][0] - 1 <= word['x0'] < left
                     and y0 <= (word['top'] + word['bottom']) / 2 < y1]
            names = [text for text in [*(item['text'].strip() for item in variable_cells), *words]
                     if re.fullmatch(r'EN|ENO|EN0|Pn|[SDNMPsdnmp][0-9]*|sd[0-9]*|TCoil|TValue|CCoil|CValue', text)]
            names = list(dict.fromkeys(names))
            combined = [re.sub(r'\s+', '', item['text']) for item in variable_cells
                        if re.fullmatch(r'S\d+\s*/\s*D\d*', item['text'].strip())]
            if len(combined) == 1:
                names = combined
            direction_cell = next((item for item in variable_cells if re.fullmatch(
                r'(?:Input|Output|Input/output|Input/Output) variable', _normalised(item['text']))), None)
            direction = (_normalised(direction_cell['text']).split()[0].lower().replace('input/output', 'in_out')
                         if direction_cell else 'unknown')
            name = names[0] if len(names) == 1 else None
            description_text = _normalised(cell['text'])
            source_label = name
            if structured and description_text == 'Execution condition':
                name = 'EN'
            elif structured and description_text == 'Execution state':
                name = 'ENO'
            row = {'name': name, 'type': kind, 'direction': direction,
                   'description': description_text, 'page': page['page'], 'y': y,
                   'raw_type': kind_cell['text'],
                   'type_basis': 'own_data_type_cell_with_actual_merged_span',
                   'direction_basis': 'explicit_variable_direction_cell' if structured else 'own_operand_read_write_description'}
            row.update(_variable_type_qualifications({'text': page['layout']}, kind_cell['text']))
            if source_label and source_label != name:
                row['source_variable_label'] = source_label
            result.append(row)
    return result


def _bind_structured_cell_rows(record, opcode, width, pins):
    for page in record.get('parameter_pages', ()):
        rows = _parameter_cell_rows(page, width, structured=True)
        if not rows or any(row['direction'] == 'unknown' for row in rows):
            continue
        for row in rows:
            row.update(_variable_type_qualifications(record, row['raw_type']))
        by_name = {}
        for direction in ('input', 'output', 'in_out'):
            group = [row for row in rows if row['direction'] == direction]
            group_pins = [pin for pin in pins if pin['direction'] == direction]
            if len(group_pins) == len(group):
                for row, pin in zip(group, group_pins):
                    if row['name'] and row['name'] != pin['name']:
                        break
                    by_name[pin['name'].upper()] = {**row, 'name': pin['name']}
                else:
                    continue
            by_name = {}
            break
        if by_name:
            return by_name
    return {}


def _described_operand_direction(description, name=None, text=''):
    """Read/write meaning must be stated by a role or a symbol-bound sentence."""
    phrase = _normalised(description).lower()
    if re.search(r'(?:data|device).{0,35}(?:to be |whose .{0,30}(?:is |are )?)(?:incremented|decremented|exchanged|rotated|shifted|inverted|alternated)|'
                 r'whose .{0,45}(?:inverted|swapped|alternated)|'
                 r'(?:device|data).{0,30}to exchange|swapped for each other|(?:input/output|read/write)', phrase):
        return 'in_out'
    if re.search(r'(?:comparison|operation) result|'
                 r'(?:result|teaching data).{0,35}(?:stored|output)|'
                 r'head (?:bit|word) device.{0,35}(?:to which|from which).{0,30}(?:output|stored)', phrase):
        return 'output'
    if re.search(r'pointer number|ladder block label|(?:number|length|position) of |'
                 r'(?:lower|upper) limit value|(?:negative|positive) bias value|'
                 r'magnification|multiplying factor|source|comparison value|data (?:to|for) (?:be compared|comparison|multiplication|division|addition|subtraction)|'
                 r'input (?:signal|device|number|value)|inputting |to be input|frequency data|pulse quantity|time data|set value|specified value|'
                 r'(?:unit|station|channel|volume|parameter|counter|timer|state relay) (?:no\.|number)|buffer memory.*number|'
                 r'frequency|speed|acceleration/deceleration time|period \(|output pulse width|'
                 r'data (?:to be|used (?:in|for)) (?:decoded|encoded|converted|inserted|written|addition|subtraction|multiplication|division|exponential|logical|exclusive)|'
                 r'instruction (?:code|for operation)|command (?:code|parameter)|file (?:id|name|creation parameter|delete method)|'
                 r'used channel|channel to be used|search start position|data insertion position|digit specification', phrase):
        return 'input'
    if re.search(r'bit position to be checked|ramp transfer time|special block number|input filter time|'
                 r'head digit position to be moved|table entry number', phrase):
        return 'input'
    if re.search(r'destination|(?:storing|store|output|write).{0,50}(?:result|teaching data|output value)|'
                 r'(?:result|teaching data).{0,35}(?:stored|output)|device.{0,25}(?:to be set|to be reset|to be cleared)|'
                 r'head (?:bit|word) device.{0,30}(?:output|result)|signal output|outputting |'
                 r'(?:device|register).{0,35}(?:storing|receiving).{0,25}(?:converted|acquired|obtained|last-out|taken out|read data|received data|random number)|'
                 r'(?:pulses|rotation direction signal|alarm).{0,20}(?:are to be output|is output)|'
                 r'turns on or off according to the comparison result', phrase):
        return 'output'
    if name:
        token = re.escape(name)
        bound = re.sub(r'\[GLYPH-[^\]]+\]', '', text)
        if re.search(r'(?:source|comparison value|input value|input device)\s*(?:device\s*)?\(\s*' + token + r'\s*\)', bound, re.I):
            return 'input'
        if re.search(r'(?:destination|output value|output device)\s*(?:device\s*)?\(\s*' + token + r'\s*\)|'
                     r'(?:transferred|stored|written|output)\s+(?:to|in)\s+(?:the\s+)?\(?\s*' + token + r'\b', bound, re.I):
            return 'output'
    return 'unknown'


def _native_row_direction(record, row):
    if '/' in (row['name'] or ''):
        return 'unknown'
    direction = _described_operand_direction(row['description'], row['name'], record['text'])
    if direction == 'unknown' and record.get('parameter_conventions', {}).get('source_destination_symbols'):
        if re.fullmatch(r'S\d*', row['name'] or ''):
            direction = 'input'
        elif re.fullmatch(r'D\d*', row['name'] or ''):
            direction = 'output'
        if direction != 'unknown':
            row['direction_basis'] = 'same_manual_explicit_source_destination_symbol_definition'
    return direction


def _native_set_data_rows(record, width):
    rows = []
    for page in record.get('parameter_pages', ()):
        actual_cells = _parameter_cell_rows(page, width, structured=False)
        if actual_cells and all(row['name'] for row in actual_cells):
            for row in actual_cells:
                row.update(_variable_type_qualifications(record, row['raw_type']))
                row['direction'] = _native_row_direction(record, row)
            rows.extend(actual_cells)
            continue
        words = [word for word in page['words'] if word['x0'] < 550]
        for header in (word for word in words if word['text'] == 'Operand'):
            same = [word for word in words if abs(word['top'] - header['top']) < 3]
            description = next((word for word in same if word['text'] == 'Description'), None)
            type_head = next((word for word in same if word['text'] == 'Data' and word['x0'] > header['x0'] + 100), None)
            if not description or not type_head:
                continue
            stops = [word['top'] for word in words if word['top'] > header['bottom']
                     and word['text'] in {'Applicable', 'Explanation', 'Function'}]
            bottom = min(stops) if stops else header['top'] + 300
            body = [word for word in words if header['bottom'] < word['top'] < bottom]
            symbols = sorted((word for word in body if word['x0'] < header['x1'] + 25 and _slot_symbol(word['text'])),
                             key=lambda word: (word['top'], word['x0']))
            if not symbols:
                continue
            type_x = min((word['x0'] for word in body if word['x0'] > description['x0'] + 60
                          and re.match(r'^(?:\d+-?|Bit|ANY|FLOAT|String)', word['text'])), default=type_head['x0'] - 15)
            for index, symbol in enumerate(symbols):
                lower = (symbols[index - 1]['top'] + symbol['top']) / 2 if index else header['bottom']
                upper = (symbol['top'] + symbols[index + 1]['top']) / 2 if index + 1 < len(symbols) else bottom
                cell = [word for word in body if lower <= word['top'] < upper]
                raw_type = ' '.join(word['text'] for word in sorted(cell, key=lambda word: (word['top'], word['x0']))
                                    if word['x0'] >= type_x - 1)
                value = _documented_type(raw_type, width)
                # The Set-data cell lists its alternatives in the same order as
                # the section's explicit 16/32-bit operation headings.
                alternatives = re.fullmatch(r'(16|32)-?\s*or\s*(32|64)-bit\s+binary', _normalised(raw_type), re.I)
                if alternatives and width in {16, 32}:
                    value = 'ANY' + alternatives[1 if width == 16 else 2]
                description_text = ' '.join(word['text'] for word in sorted(cell, key=lambda word: (word['top'], word['x0']))
                                            if header['x1'] + 25 <= word['x0'] < type_x - 1)
                name = symbol['text']
                rows.append({'name': name, 'type': value,
                             'description': description_text, 'page': page['page'],
                             'type_basis': 'own_set_data_type_cell_and_operation_width',
                             'direction_basis': 'own_operand_read_write_description'})
                rows[-1]['direction'] = _native_row_direction(record, rows[-1])
    _native_file_data_types(record, rows)
    return rows


def _native_file_data_types(record, rows):
    """The CF-ADP data type is selected by the parameter block, not fixed."""
    if record['manual_id'] != 'fx3_programming_r' or record['opcode'] not in {'FLWR', 'FLRD'}:
        return
    text = _normalised(record['text'])
    if not (re.search(r'K0\s*:\s*Mixed type', text) and re.search(r'K1\s*:\s*Bit type', text)
            and re.search(r'K6\s*:\s*Real numbers.*?32-bit', text)
            and re.search(r'K7\s*:\s*Character string.*?512', text)):
        return
    selector = [row for row in rows if re.search(r'Data (?:read|write) parameter', row['description'])]
    data = [row for row in rows if row['type'] == 'unknown' and row.get('raw_type', '').strip() == '-']
    if len(selector) != 1 or len(data) != 1:
        return
    cases = [{'value': 0, 'type': {'kind': 'mixed_data'}}, {'value': 1, 'type': 'BOOL'},
             {'value': 2, 'type': {'kind': 'integer', 'bits': 16, 'radix': 10}},
             {'value': 3, 'type': {'kind': 'integer', 'bits': 32, 'radix': 10}},
             {'value': 4, 'type': {'kind': 'bit_pattern', 'bits': 16, 'radix': 16}},
             {'value': 5, 'type': {'kind': 'bit_pattern', 'bits': 32, 'radix': 16}},
             {'value': 6, 'type': 'REAL'},
             {'value': 7, 'type': {'kind': 'character_string', 'maximum_characters': 512}}]
    if record['opcode'] == 'FLWR' and re.search(r'K8\s*:\s*Data name.*?32 half-width/full-width', text):
        cases.append({'value': 8, 'type': {'kind': 'file_data_name', 'maximum_characters': 32,
                                         'automatic_fields': ['index', 'date_time']}})
    data[0]['type'] = {'kind': 'operand_selected',
                      'selector': {'parameter': selector[0]['name'], 'offset': 0}, 'cases': cases}
    data[0]['type_basis'] = 'own_detailed_setting_data_type_selector_table'
    if record['opcode'] == 'FLRD':
        for row in rows:
            if re.search(r'Number of data points existing in the specified line', row['description']):
                row['direction'] = 'output'
                row['direction_basis'] = 'own_read_result_and_explicit_destination_symbol'


def _native_diagram_order(record, opcode, names):
    orders = []
    aliases = {part: name for name in names for part in name.split('/')}
    for page in record.get('parameter_pages', ()):
        words = [dict(word, text=re.sub(r'\[GLYPH-F0A0\]$', '', word['text']))
                 for word in page['words'] if word['x0'] < 550]
        for instruction in (word for word in words if word['text'] == opcode):
            nearby = [word for word in words if instruction['top'] - 28 < word['top'] < instruction['bottom'] + 4
                      and word['x0'] > instruction['x1'] - 5 and word['text'] in aliases]
            for line in _parameter_lines(nearby):
                order = list(dict.fromkeys(aliases[word['text']] for word in line))
                if set(order) == set(names) and order not in orders:
                    orders.append(order)
    return orders[0] if len(orders) == 1 else None


def _native_parameter_facts(record, variant, models):
    if record['manual_id'] == 'fxcpu_basic_applied_m':
        return []
    opcode, width = variant['opcode'], variant['width_bits']
    rows = _native_set_data_rows(record, width)
    legacy = None
    if record['manual_id'].startswith('jy992d'):
        legacy = _legacy_native_parameter_rows(record, variant)
        rows = legacy['rows'] if legacy else []
    if not rows:
        if not (legacy and legacy['order'] == []) and not _NO_DEVICES.search(record['text']):
            return []
        names, operands = [], []
    else:
        by_name = {}
        for row in rows:
            by_name.setdefault(row['name'], []).append(row)
        rows = [items[0] for items in by_name.values()
                if len({json.dumps({key: item[key] for key in ('type', 'direction')}, sort_keys=True)
                        for item in items}) == 1]
        if len(rows) != len(by_name):
            return []
        declared = [row['name'] for row in rows]
        names = legacy['order'] if legacy else _native_diagram_order(record, opcode, declared)
        if names is None:
            # The instruction-format template is shared by the section's own
            # explicitly declared variants, including the optional pulse form.
            families = [item['opcode'] for item in _programming_manual_variants(record)]
            alternatives = [_native_diagram_order(record, form, declared) for form in families]
            unique = {tuple(order) for order in alternatives if order is not None}
            names = list(next(iter(unique))) if len(unique) == 1 else None
        if names is None:
            return []
        lookup = {row['name']: row for row in rows}
        operands = [{key: lookup[name][key] for key in ('name', 'type', 'direction', 'type_basis', 'direction_basis',
                    'constant_coercion', 'types_by_model', 'type_qualification_basis', 'unresolved_type_qualifications',
                    'width_bits', 'type_completeness', 'role')
                     if key in lookup[name]}
                    for name in names]
    complete = all(item['type'] != 'unknown' and item['direction'] in {'input', 'output', 'in_out'}
                   and not item.get('unresolved_type_qualifications') and item.get('type_completeness') != 'partial'
                   for item in operands)
    common = {'declaration_kind': 'instruction', 'representation': 'native_ladder_instruction',
              'basis': 'own_native_operand_header_and_manual_conventions' if legacy else
                       'own_native_instruction_diagram_and_set_data'}
    facts = [_fact('authored:applied:native_' + key, dimension, {**common, **value},
                  opcode, record, models, languages=['ladder'], status=status)
            for key, dimension, value, status in (
                ('order', 'parameters.order', {'symbols': names}, 'source_verified'),
                ('arity', 'parameters.arity', {'minimum': len(names), 'maximum': len(names)}, 'source_verified'),
                ('typed_operands', 'parameters.typed_operands', {'operands': operands, 'operand_count': len(names)},
                 'source_verified' if complete else 'declared_unverified'))]
    if legacy or any(item.get('direction_basis') == 'same_manual_explicit_source_destination_symbol_definition' for item in operands):
        reference = {**_source_reference(record), 'id': 'native_parameter_conventions:' + record['manual_id'],
                     'pdf_pages': record['parameter_conventions']['pdf_pages']}
        for fact in facts:
            fact['sources'].append(reference)
    if record['manual_id'] == 'fx3_programming_r' and opcode == 'ADPRW':
        profiles = _native_modbus_parameter_profiles(record, operands, models)
        if profiles:
            facts = [fact for fact in facts if fact['dimension'] != 'parameters.typed_operands'] + profiles
    elif legacy:
        profiles = _legacy_float_parameter_profiles(record, variant, operands, models)
        if profiles:
            facts = [fact for fact in facts if fact['dimension'] != 'parameters.typed_operands'] + profiles
    return facts


def _legacy_float_parameter_profiles(record, variant, operands, models):
    root, width = _root(variant['opcode']), variant['width_bits']
    text = _normalised(record['text'])
    if (root not in {'BCD', 'BIN', 'FLT', 'ADD', 'SUB', 'MUL', 'DIV', 'SQR'} or width not in {16, 32}
            or 'M8023' not in text or not re.search(r'float', text, re.I)):
        return []
    if not operands or any(not re.fullmatch(r'[SD]\d*', item['name'], re.I) for item in operands):
        return []
    result = []
    for state in (False, True):
        if state and root != 'FLT' and width != 32:
            continue
        selected = list(models)
        if state and re.search(r'Operation 2:\s*\(Applicable units:\s*FX\(2C\)\)', text):
            selected = [model for model in selected if model in {'FX1', 'FX2C'}]
        if not selected:
            continue
        rows = []
        for item in operands:
            row = dict(item)
            source = row['name'].upper().startswith('S')
            if root == 'FLT':
                row['type'] = ('REAL' if source else 'ANY' + str(width)) if state else ('ANY' + str(width) if source else 'REAL')
            elif root in {'BCD', 'BIN'}:
                encoded = not source if root == 'BCD' else source
                row['type'] = ({'kind': 'scientific_notation', 'mantissa_bits': 16, 'exponent_bits': 16}
                               if encoded else 'REAL') if state else (
                              {'kind': 'packed_bcd', 'bits': width} if encoded else 'ANY' + str(width))
            else:
                bits = width * 2 if root == 'MUL' and not source else width
                row['type'] = 'REAL' if state else 'ANY' + str(bits)
            row.pop('type_completeness', None)
            row['type_basis'] = 'own_m8023_format_mode_and_operation_width'
            rows.append(row)
        scoped = dict(record, conditions=[*record.get('conditions', []),
                       {'context': 'device_state', 'device': 'M8023', 'equals': state}])
        fact = _fact('authored:applied:native_typed_operands:m8023_' + ('on' if state else 'off'),
                     'parameters.typed_operands', {'operands': rows, 'operand_count': len(rows),
                      'declaration_kind': 'instruction', 'representation': 'native_ladder_instruction',
                      'basis': 'own_float_flag_operand_format_branches'},
                     variant['opcode'], scoped, selected, languages=['ladder'])
        fact['sources'].append({**_source_reference(record), 'id': 'native_parameter_conventions:' + record['manual_id'],
                                'pdf_pages': record['parameter_conventions']['pdf_pages']})
        result.append(fact)
    return result


def _native_modbus_parameter_profiles(record, operands, models):
    """The manual's S4/D slot is selected by the actual MODBUS command."""
    if [item['name'] for item in operands] != ['S', 'S1', 'S2', 'S3', 'S4/D']:
        return []
    text = _normalised(record['text'])
    commands = (
        (1, r'1H\s+Read Coils', 'output', False),
        (2, r'2H\s+Read Discrete Inputs', 'output', False),
        (3, r'3H\s+Read Holding Register', 'output', False),
        (4, r'4H\s+Read Input Register', 'output', False),
        (5, r'5H\s+Write Single Coil', 'input', False),
        (6, r'6H\s+Write Single Register', 'input', False),
        (7, r'7H\s+Read Exception State', 'output', True),
        (11, r'BH\s+Get Comm\. Event Counter', 'output', True),
        (12, r'CH\s+Get Comm\. Event Log', 'output', True),
        (15, r'FH\s+Write Multiple Coils', 'input', False),
        (16, r'10H\s+Write Multiple Registers', 'input', False),
        (17, r'11H\s+Report Slave ID', 'output', True),
        (22, r'16H\s+Mask Write Register', 'input', True),
        (23, r'17H\s+Read/Write Multiple Registers', 'in_out', True),
    )
    profiles = []

    def profile(command, direction, restricted, subfunction=None):
        selected_models = [model for model in models if not restricted or model in {'FX3U', 'FX3UC'}]
        if not selected_models:
            return
        parameters = [dict(item) for item in operands]
        final = parameters[-1]
        final.update(name='S4' if direction == 'input' else 'D', direction=direction,
                     direction_basis='own_command_parameter_table_source_or_destination',
                     type_basis='own_set_data_type_and_command_parameter_table')
        if command not in {1, 2, 5, 15}:
            final['type'] = 'ANY16'
        scoped = dict(record, conditions=[*record.get('conditions', []),
                         {'context': 'operand_value', 'parameter': 'S1', 'equals': command}])
        identity = 'authored:applied:native_typed_operands:command_' + str(command)
        if subfunction is not None:
            scoped['conditions'].append({'context': 'operand_value', 'parameter': 'S2', 'equals': subfunction})
            identity += ':subfunction_' + str(subfunction)
        profiles.append(_fact(identity, 'parameters.typed_operands',
            {'operands': parameters, 'operand_count': 5, 'declaration_kind': 'instruction',
             'representation': 'native_ladder_instruction', 'basis': 'own_modbus_command_parameter_table'},
            'ADPRW', scoped, selected_models, languages=['ladder']))

    for command, witness, direction, restricted in commands:
        if re.search(witness, text):
            profile(command, direction, restricted)
    if re.search(r'8H\s+Diagnosis', text):
        for code in (0, 1, 2, 3, 4, 10, 11, 12, 13, 14, 15, 16, 17, 18):
            if re.search(r'Sub-function:\s*' + format(code, 'X') + r'H\b', text):
                profile(8, 'input' if code == 4 else 'output', True, code)
    return profiles


def _structured_diagram_ports(record, opcode):
    """Same-form left/right pins, before Set data, in their printed order."""
    blocks = []
    for page in record.get('parameter_pages', ()):
        words = [word for word in page['words'] if word['x0'] < 550]
        stops = [word['top'] for word in words if word['text'] == 'Variable'
                 and any(other['text'] == 'Description' and abs(other['top'] - word['top']) < 3
                         and other['x0'] > word['x1'] for other in words)]
        if not stops:
            continue
        call_names = {name.strip() for match in re.finditer(re.escape(opcode) + r'\(([^();]*)\)', record['text'])
                      for name in match[1].split(',') if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', name.strip())}
        for en in (word for word in words if word['text'] == 'EN' and (not stops or word['top'] < min(stops))):
            outputs = [word for word in words if word['text'] == 'ENO'
                       and abs(word['top'] - en['top']) < 2 and 5 < word['x0'] - en['x0'] < 160]
            if not outputs:
                continue
            eno = min(outputs, key=lambda word: word['x0'])
            captions = [word for word in words if re.fullmatch(r'[A-Z][A-Z0-9_+\-]*', word['text'])
                        and 0 < en['top'] - word['bottom'] < 16
                        and en['x0'] - 8 <= word['x0'] < eno['x1']]
            if not any(word['text'].replace('_P', 'P') == opcode for word in captions):
                continue
            next_block = [word['top'] for word in words if word['text'] == 'EN'
                          and word['top'] > en['top'] + 3 and abs(word['x0'] - en['x0']) < 3]
            bottom = min([*next_block, *[value for value in stops if value > en['top']], en['top'] + 150])
            pins = []
            for word in sorted(words, key=lambda word: (word['top'], word['x0'])):
                if not en['top'] - 2 <= word['top'] < bottom:
                    continue
                if (word['text'] not in call_names and
                        not re.fullmatch(r'EN|ENO|[sdnmp][0-9]*|TCoil|TValue|CCoil|CValue', word['text'])):
                    continue
                direction = ('input' if abs(word['x0'] - en['x0']) < 3 else
                             'output' if abs(word['x1'] - eno['x1']) < 5 and word['x0'] > en['x1'] else None)
                if direction:
                    pins.append({'name': word['text'], 'direction': direction, 'page': page['page']})
            pins = [pin for index, pin in enumerate(pins)
                    if (pin['name'], pin['direction']) not in
                    [(other['name'], other['direction']) for other in pins[:index]]]
            pins = [pin for direction in ('input', 'output') for pin in pins if pin['direction'] == direction]
            identity = [(pin['name'], pin['direction']) for pin in pins]
            if identity and identity not in [[(pin['name'], pin['direction']) for pin in block] for block in blocks]:
                blocks.append(pins)
    return blocks


def _structured_variable_rows(record, opcode, width):
    """Bind explicit variable directions/types to the same diagram's pin rows.

    Some PDF fonts omit the s/d glyph in the Variable column. Such a row is
    bound only if the complete direction group and actual diagram have the same
    number of rows; a letter's spelling never establishes direction.
    """
    diagrams = _structured_diagram_ports(record, opcode)
    if len(diagrams) != 1:
        return {}, []
    pins = diagrams[0]
    cell_rows = _bind_structured_cell_rows(record, opcode, width, pins)
    if cell_rows:
        return cell_rows, pins
    for page in record.get('parameter_pages', ()):
        words = [word for word in page['words'] if word['x0'] < 550]
        for variable in (word for word in words if word['text'] == 'Variable'):
            headings = [word for word in words if abs(word['top'] - variable['top']) < 20]
            descriptions = [word for word in headings if word['text'] == 'Description' and word['x0'] > variable['x0']]
            type_heads = [word for word in headings if word['text'] == 'Data' and word['x0'] > variable['x0'] + 80]
            if not descriptions or not type_heads:
                continue
            stops = [word['top'] for word in words if word['top'] > variable['bottom'] + 5
                     and word['x0'] < variable['x0'] and (word['text'] in {'Applicable', 'Function', 'Cautions'}
                     or re.fullmatch(r'\*\d+\.?', word['text']))]
            bottom = min(stops) if stops else variable['top'] + 300
            body = [word for word in words if variable['bottom'] < word['top'] < bottom]
            types_x = min(word['x0'] for word in type_heads) - 10
            # The description's body starts to the right of Variable and left
            # of its centred header. Use the leftmost actual description word.
            desc_candidates = [word['x0'] for word in body
                               if variable['x0'] + 45 < word['x0'] < types_x and word['text'] not in {'operation', '16-bit', '32-bit'}]
            if not desc_candidates:
                continue
            desc_x = min(desc_candidates)
            types_x = min((word['x0'] for word in body if _documented_type(word['text'], width) != 'unknown'
                           and word['x0'] > max(desc_x + 50, min(head['x0'] for head in type_heads) - 75)),
                          default=types_x)
            role_words = [word for word in body if word['x0'] < variable['x0'] - 10
                          and word['text'] in {'Input', 'Output', 'Input/output', 'Input/Output'}]
            if not role_words:
                continue
            anchors = []
            for line in _parameter_lines(body, left=desc_x - 2, right=types_x - 1):
                if all(word['text'] in {'operation', '16-bit', '32-bit'} for word in line):
                    continue
                anchors.append(line)
            if not anchors:
                continue
            # Wrapped descriptions form one row. EN/ENO and each type cell
            # anchor establish row boundaries, not the physical text lines.
            type_words = [word for word in body if word['x0'] >= types_x - 1
                          and (_documented_type(word['text'], width) != 'unknown' or word['text'].startswith('ARRAY')
                               or word['text'].startswith('FLOAT('))
                          and not any(other['text'] == 'OF' and abs(other['top'] - word['top']) < 2
                                      and 0 < word['x0'] - other['x0'] < 20 for other in body)
                          and not _wrapped_array_element(word, body)]
            row_y = sorted(set(round(word['top'], 1) for word in type_words))
            row_y += [word['top'] for word in body if word['text'] in {'EN', 'ENO'} and word['x0'] < desc_x]
            row_y = sorted(row_y)
            merged = []
            for value in row_y:
                if not merged or value - merged[-1] > 9:
                    merged.append(value)
            if not merged:
                continue
            rows = []
            for index, y in enumerate(merged):
                lower = (merged[index - 1] + y) / 2 if index else variable['bottom']
                upper = (y + merged[index + 1]) / 2 if index + 1 < len(merged) else bottom
                row_words = [word for word in body if lower <= word['top'] < upper]
                names = [word['text'] for word in row_words if variable['x0'] - 2 <= word['x0'] < desc_x - 2
                         and any(pin['name'] == word['text'] for pin in pins)]
                description = ' '.join(word['text'] for word in sorted(row_words, key=lambda word: (word['top'], word['x0']))
                                       if desc_x - 1 <= word['x0'] < types_x - 1)
                if not description:
                    continue
                row_types = [word for word in row_words if word['x0'] >= types_x - 1]
                arrays = sorted(word['x0'] for word in row_types if word['text'].startswith('ARRAY'))
                values = []
                whole_type = ' '.join(word['text'] for word in sorted(row_types, key=lambda word: (word['top'], word['x0'])))
                if arrays:
                    for column_index, x in enumerate(arrays):
                        end_x = arrays[column_index + 1] if column_index + 1 < len(arrays) else 10000
                        column = [word for word in row_types if x - 1 <= word['x0'] < end_x - 1]
                        value = _documented_type(' '.join(word['text'] for word in sorted(column, key=lambda word: (word['top'], word['x0']))), width)
                        if value != 'unknown':
                            values.append((x, value))
                elif _documented_type(whole_type, width) != 'unknown':
                    values.append((types_x, _documented_type(whole_type, width)))
                else:
                    values = [(word['x0'], _documented_type(word['text'], width)) for word in row_types
                              if _documented_type(word['text'], width) != 'unknown']
                if not values:
                    continue
                value = values[-1 if width == 32 and len(values) > 1 else 0][1]
                selected_x = values[-1 if width == 32 and len(values) > 1 else 0][0]
                selected_type = ' '.join(word['text'] for word in row_types
                                         if selected_x - 1 <= word['x0'] < selected_x + 40)
                name = names[0] if len(names) == 1 else None
                source_label = next((word['text'] for word in row_words
                                     if variable['x0'] - 2 <= word['x0'] < desc_x - 2
                                     and word['text'] in {'EN', 'EN0', 'ENO'}), None)
                if re.fullmatch(r'Execution state', description, re.I):
                    name = 'ENO'
                elif re.fullmatch(r'Execution condition', description, re.I):
                    name = 'EN'
                rows.append({'name': name, 'type': value,
                             'description': description, 'y': y, 'page': page['page']})
                rows[-1].update(_variable_type_qualifications(record, selected_type))
                if source_label and source_label != name:
                    rows[-1]['source_variable_label'] = source_label
            if len(rows) != len(pins):
                continue
            result = {}
            # The diagram defines left/right groups. Each group's explicit
            # EN/ENO row identifies its corresponding table group even when the
            # group heading is vertically centred across multiple rows.
            output_start = next((index for index, row in enumerate(rows) if row['name'] == 'ENO'), None)
            if output_start is None:
                continue
            input_rows, output_rows = rows[:output_start], rows[output_start:]
            pin_groups = ([pin for pin in pins if pin['direction'] == 'input'],
                          [pin for pin in pins if pin['direction'] == 'output'])
            if any(len(group) != len(group_pins) for group, group_pins in zip((input_rows, output_rows), pin_groups)):
                continue
            for group, group_pins in zip((input_rows, output_rows), pin_groups):
                for row, pin in zip(group, group_pins):
                    if row['name'] and row['name'] != pin['name']:
                        break
                    result[pin['name'].upper()] = {**row, 'name': pin['name'], 'direction': pin['direction'],
                        'type_basis': 'source_variable_type_column',
                        'direction_basis': 'explicit_variable_group_and_same_form_pin_side'}
                else:
                    continue
                result = {}
                break
            if result:
                return result, pins
    # A shared Set-data table can be bound by another explicitly declared form
    # on the same page. Keep the selected form's actual diagram, including a
    # missing printed pin; do not silently repair the source drawing.
    own = next((variant for variant in _structured_variant_table(record)
                if variant['opcode'] == opcode), None)
    same_call = []
    if own and own.get('call_form'):
        arguments = own['call_form'].split('(', 1)[1]
        for sibling in _structured_variant_table(record):
            if (sibling['opcode'] == opcode or sibling['width_bits'] != width
                    or not sibling.get('call_form')
                    or sibling['call_form'].split('(', 1)[1] != arguments):
                continue
            other = _structured_diagram_ports(record, sibling['opcode'])
            if len(other) != 1 or {pin['page'] for pin in other[0]} != {pin['page'] for pin in pins}:
                continue
            bound = _bind_structured_cell_rows(record, sibling['opcode'], width, other[0])
            if bound and bound not in same_call:
                same_call.append(bound)
        if len(same_call) == 1:
            return same_call[0], pins
    return {}, pins


def _signature_from_form(record, opcode, call, operand_types, variable_rows):
    """Published from the documented call form only.

    A parameter keeps ``unknown`` type or direction unless this section states
    it (variable row or structured operand record); the signature is then
    ``declared_unverified`` instead of being completed by a naming guess.
    """
    call = re.sub(r'\s+', '', call)
    body = call[call.index('(') + 1: call.rindex(')')]
    parameters, unresolved = [], []
    documented_rows = record.get('structured_parameter_rows', {}).get(opcode, {})
    arguments = [item.strip() for item in body.split(',') if item.strip()]
    expected_count = len(documented_rows) - int('ENO' in documented_rows)
    for position, part in enumerate(arguments, start=1):
        key = part.upper()
        alias = (documented_rows.get('EN') if position == 1 and key != 'EN'
                 and len(arguments) == expected_count
                 and key not in documented_rows and 'EN' not in body.upper().split(',') else None)
        if alias:
            documented_rows = dict(documented_rows, **{key: {**alias, 'name': part,
                'source_variable_label': 'EN', 'direction_basis': 'first_call_slot_and_explicit_execution_condition_row'}})
        if key in documented_rows or key in variable_rows:
            row = documented_rows.get(key) or variable_rows[key]
            parameters.append({'name': part, 'type': row['type'], 'direction': row['direction'],
                               **({'role': 'execution_condition' if key == 'EN' or alias else 'execution_state'}
                                  if key in {'EN', 'ENO'} or alias else {}),
                               **{field: row[field] for field in ('type_basis', 'direction_basis', 'types_by_model',
                                   'type_qualification_basis', 'constant_coercion', 'unresolved_type_qualifications', 'source_variable_label')
                                  if row.get(field)}})
            if (row.get('unresolved_type_qualifications') or row['type'] == 'unknown'
                    or row['direction'] not in {'input', 'output', 'in_out'}):
                unresolved.append(part)
            continue
        operand_type = operand_types.get(key, 'unknown')
        parameters.append({'name': part, 'type': operand_type, 'direction': 'unknown'})
        unresolved.append(part)
    # This manual prints instruction statements without an assigned result.
    # ENO is an explicit output pin, not an invented ST return assignment.
    signature = {'parameters': parameters, 'returns': {'role': 'not_assigned_in_documented_statement'},
                 'enable': any(item['name'].upper() == 'EN' or item.get('role') == 'execution_condition' for item in parameters),
                 'available': True, 'documented_form': call, 'call_form': opcode, 'call_kind': 'statement'}
    if documented_rows.get('ENO'):
        signature['execution_output'] = {key: documented_rows['ENO'][key] for key in ('name', 'type', 'direction')}
    conflicts = [{'pin': row['name'], 'table_variable': row['source_variable_label']}
                 for row in documented_rows.values() if row.get('source_variable_label')]
    if conflicts:
        signature['source_variable_conflicts'] = conflicts
    invalid = [item['name'] for item in parameters if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', item['name'])]
    if invalid:
        signature['source_syntax_conflicts'] = [{'token': name, 'conflict': 'invalid_parameter_separator_or_identifier'} for name in invalid]
    if documented_rows and len(arguments) != expected_count:
        signature.setdefault('source_syntax_conflicts', []).append(
            {'conflict': 'call_and_variable_table_argument_counts_disagree',
             'documented_argument_count': len(arguments), 'variable_table_argument_count': expected_count})
    return signature, unresolved


def _structured_parameter_facts(record, variant, models):
    """One documented block's ports; structured Ladder retains project mode."""
    opcode = variant['opcode']
    pins = record.get('structured_parameter_pins', {}).get(opcode, [])
    if not pins:
        return []
    rows = record.get('structured_parameter_rows', {}).get(opcode, {})
    parameters = []
    for pin in pins:
        row = rows.get(pin['name'].upper(), {})
        parameters.append({'name': pin['name'], 'type': row.get('type', 'unknown'),
                           'direction': row.get('direction', pin['direction']),
                           'direction_basis': row.get('direction_basis', 'same_form_pin_side_geometry'),
                           **{field: row[field] for field in ('type_basis', 'types_by_model', 'type_qualification_basis', 'constant_coercion',
                               'unresolved_type_qualifications', 'source_variable_label') if row.get(field)}})
    complete = all(item['type'] != 'unknown' and item['direction'] in {'input', 'output', 'in_out'}
                   and not item.get('unresolved_type_qualifications')
                   for item in parameters)
    conflicts = [{'pin': row['name'], 'table_variable': row['source_variable_label']}
                 for row in rows.values() if row.get('source_variable_label')]
    printed = {pin['name'].upper() for pin in pins}
    conflicts += [{'pin': row['name'], 'diagram_binding': 'absent_from_printed_form'}
                  for name, row in rows.items() if name not in printed]
    common = {'call_form': opcode, 'declaration_kind': 'instruction',
              'executor_capability': 'not_established', 'admission_effect': 'none'}
    result = []
    for language in ('fbd', 'ladder'):
        scoped_record = dict(record)
        if language == 'ladder':
            scoped_record['conditions'] = [*record.get('conditions', []),
                {'context': 'project_mode', 'equals': 'structured_ladder_block'}]
        available = {**common, 'language': language, 'available': True,
                     'basis': 'same_form_structured_block_diagram'}
        signature = {**common, 'parameters': parameters, 'returns': {'role': 'outputs_via_pins'},
                     'enable': any(item['name'] == 'EN' for item in parameters), 'available': True,
                     'representation': 'structured_ladder_block' if language == 'ladder' else 'fbd_instruction_block'}
        for identity, dimension, value, status in (
            ('availability', 'identity.language_availability', available, 'source_verified'),
            ('signature', 'parameters.' + language + '_signature', signature,
             'conflict' if conflicts else 'source_verified' if complete else 'declared_unverified'),
            ('typed_operands', 'parameters.typed_operands',
             {**common, 'operands': parameters, 'operand_count': len(parameters),
              'binding': 'documented_block_pin_groups'},
             'conflict' if conflicts else 'source_verified' if complete else 'declared_unverified'),
        ):
            result.append(_fact('authored:applied:' + language + '_' + identity, dimension, value,
                                opcode, scoped_record, models, languages=[language], status=status))
        if conflicts:
            result.append(_fact('authored:applied:' + language + '_variable_label_conflict',
                'constraints.source_identity', {'conflict': 'variable_table_and_diagram_label_disagree',
                 'bindings': conflicts}, opcode, scoped_record, models, languages=[language], status='conflict'))
    return result


def _structured_variant_table(record):
    variants, seen = [], set()
    for match in _FORM_ROW.finditer(record['text']):
        printed_name = name = match.group('name')
        call_match = re.search(r'([A-Z][A-Z0-9_+\-]*[ \t]*\([^()]*\))\s*;?', match.group('tail') or '')
        if not call_match:
            call_match = re.match(r'\s*([A-Z][A-Z0-9_+\-]*[ \t]*\([^()]*\))\s*;',
                                  record['text'][match.end():])
        call = call_match[1] if call_match else None
        if call and call.split('(', 1)[0].strip() in {'EN', 'ENO'}:
            # A pin followed by a parenthesised manual title is no call form.
            call = None
        if call:
            call_name = call[:call.index('(')].strip()
            if _root(call_name) == _root(record['opcode']):
                name = call_name
        if _root(name) != _root(record['opcode']) or name in seen:
            continue
        mismatch = call if call and name != call[:call.index('(')].strip() else None
        if mismatch:
            call = None
        seen.add(name)
        qualifier = (re.search(r'\(([A-Z])\)', match.group('form')) or [None, None])[1]
        variants.append({'opcode': name,
                         'width_bits': int(match.group('width')) if match.group('width') else 'unknown',
                         'execution_form': re.sub(r'\s+', '', match.group('form').split('(')[0]).lower(),
                         'execution_qualifier': qualifier, 'call_form': call,
                         **({'source_variant_mismatch': printed_name} if printed_name != name else {}),
                         **({'source_call_mismatch': mismatch} if mismatch else {})})
    # A few printed rows have a wrong instruction-name cell (VRRD on the VRSC
    # page; DBKP+P on the BK+ page). The diagram/call still identifies the form;
    # keep that disagreement rather than silently fixing the printed cell.
    for match in re.finditer(
            r'(?P<label>[A-Z][A-Z0-9_+\-]*)\s+EN\s+ENO\s+'
            r'(?P<printed>[A-Z][A-Z0-9_+\-]*)\s+(?P<width>16|32)\s+bits\s+'
            r'(?P<form>Continuous|Pulse)\b', record['text']):
        name = match['label'].replace('_P', 'P')
        if _root(name) != _root(record['opcode']) or name in seen:
            continue
        seen.add(name)
        variants.append({'opcode': name, 'width_bits': int(match['width']),
                         'execution_form': match['form'].lower(), 'call_form': None,
                         **({'source_variant_mismatch': match['printed']}
                            if match['printed'] != match['label'] else {})})
    # The literal instruction-name column can itself be wrong (REFF's page
    # prints REF). An actual named block on that format row still establishes
    # its FBD identity; the disagreeing ST name remains a source conflict.
    for page in record.get('parameter_pages', ()):
        words = page['words']
        for caption in words:
            name = caption['text'].replace('_P', 'P')
            if (name in seen or not re.fullmatch(r'[A-Z][A-Z0-9_+\-]*', name)
                    or _root(name) != _root(record['opcode'])
                    or not _structured_diagram_ports(record, name)):
                continue
            forms = [word for word in words if word['text'] in {'Continuous', 'Pulse'}
                     and word['x0'] < caption['x0'] - 10 and abs(word['top'] - caption['top']) < 25]
            if not forms:
                continue
            form = min(forms, key=lambda word: abs(word['top'] - caption['top']))
            widths = [word for word in words if word['text'] in {'16', '32'}
                      and word['x0'] < form['x0'] and abs(word['top'] - form['top']) < 4]
            if len(widths) != 1:
                continue
            width = int(widths[0]['text'])
            candidates = [match for match in _FORM_ROW.finditer(record['text'])
                          if match.group('width') == str(width)
                          and re.sub(r'\s+', '', match.group('form').split('(')[0]) == form['text']]
            unique = {}
            for match in candidates:
                printed_call = re.search(r'[A-Z][A-Z0-9_+\-]*[ \t]*\([^()]*\)', match.group('tail') or '')
                key = (match.group('name'), re.sub(r'\s+', '', printed_call[0] if printed_call else match.group('tail') or ''))
                unique[key] = match
            candidates = list(unique.values())
            if len(candidates) != 1:
                continue
            printed = candidates[0].group('name')
            call = re.search(r'([A-Z][A-Z0-9_+\-]*[ \t]*\([^()]*\))', candidates[0].group('tail') or '')
            seen.add(name)
            variants.append({'opcode': name, 'width_bits': width, 'execution_form': form['text'].lower(),
                             'call_form': call[1] if call and call[1].split('(', 1)[0].strip() == name else None,
                             **({'source_variant_mismatch': printed} if printed != name else {}),
                             **({'source_call_mismatch': call[1]} if call and call[1].split('(', 1)[0].strip() != name else {})})
    return variants


def _programming_manual_variants(record):
    variants, seen = [], set()
    for match in _WIDTH_HEADING.finditer(record['text']):
        width = int(match.group('width'))
        for name in [item.strip().upper()
                     for item in re.split(r'[,/]| and ', match.group('variants')) if item.strip()]:
            name = name.split()[-1] if ' ' in name else name
            if not re.fullmatch(r'[A-Z][A-Z0-9_+\-]*', name) or name in seen:
                continue
            if _root(name) != _root(record['opcode']):
                continue
            seen.add(name)
            variants.append({'opcode': name, 'width_bits': width,
                             'execution_form': 'pulse' if name.endswith('P') and not name.endswith('PP')
                             else 'continuous', 'call_form': None})
    # Some native instructions (including BMOV and WDT) give the forms in the
    # mnemonic/operation-condition table rather than a "16-bit operation (...)"
    # heading. Read that table, never a program-example instruction token.
    for grid in record.get('table_groups') or ():
        text = ' '.join(_normalised(cell) for row in grid for cell in row if cell)
        if not re.search(r'Mnemonic.*Operation Condition|Operation Condition.*Mnemonic', text, re.I):
            continue
        for match in re.finditer(r'\b(?P<name>[A-Z][A-Z0-9_+\-]*)\s+'
                                 r'(?P<form>Continuous|Pulse(?:\s*\(Single\))?)', text):
            name = match['name']
            if _root(name) != _root(record['opcode']) or name in seen:
                continue
            widths = list(re.finditer(r'\b(16|32)-bit Instruction', text[:match.start()]))
            width = int(widths[-1][1]) if widths else 'unknown'
            seen.add(name)
            variants.append({'opcode': name, 'width_bits': width,
                             'execution_form': match['form'].split('(')[0].strip().lower(),
                             'call_form': None})
    if not variants:
        # The layout projection preserves row delimiters even where PDF table
        # detection lost the horizontal boundary.
        for match in re.finditer(r'^\s*(?P<name>[A-Z][A-Z0-9_+\-]*)\s*\|\s*'
                                 r'(?P<form>Continuous|Pulse(?:\s*\(Single\))?)', record['text'], re.M):
            name = match['name']
            if _root(name) == _root(record['opcode']) and name not in seen:
                seen.add(name)
                variants.append({'opcode': name, 'width_bits': 'unknown',
                                 'execution_form': match['form'].split('(')[0].strip().lower(),
                                 'call_form': None})
    for match in re.finditer(r'(?:\n|^)1\. Instruction format\s+(?P<body>.*?)'
                             r'(?=\n2\. (?:Set data|Applicable devices))', record['text'], re.S):
        body = match['body']
        widths = set(re.findall(r'\b(16|32)-bit Instruction', body))
        for row in re.finditer(r'\b(?P<name>[A-Z][A-Z0-9_+\-]*)\s+'
                               r'(?P<form>Continuous|Pulse(?:\s*\(Single\))?)', body):
            name = row['name']
            if _root(name) != _root(record['opcode']) or name in seen:
                continue
            seen.add(name)
            variants.append({'opcode': name, 'width_bits': int(next(iter(widths))) if len(widths) == 1 else 'unknown',
                             'execution_form': row['form'].split('(')[0].strip().lower(), 'call_form': None})
    return variants


def _legacy_width(text):
    """Operating width only when the page states exactly one of 16/32 bits."""
    widths = {int(found) for found in re.findall(r'\b(16|32)[ \t]*-?[ \t]*bit', text)}
    return widths.pop() if len(widths) == 1 else 'unknown'


def _legacy_variants(record):
    variants, seen = [], set()
    width = _legacy_width(record['text'])
    table = _legacy_table_data(record)
    # The program-steps cell declares single as well as comma-separated forms.
    # On a shared page the selected table is bound to this mnemonic first.
    text = table['steps'] if table else record['text']
    documented = table.get('documented_opcode', record['opcode']) if table else record['opcode']
    alias = LEGACY_HEADING_ALIASES.get(documented)
    if alias and re.search(r'\b%s\b' % re.escape(alias), text):
        documented = alias
    root = _root(documented)
    for candidate in sorted({documented, documented + 'P', 'D' + documented,
                             'D' + documented + 'P'}, key=len, reverse=True):
        text = re.sub(r'(?<![A-Z0-9])' + r'\s*'.join(map(re.escape, candidate)) + r'(?![A-Z0-9])',
                      candidate, text)
    declarations = re.finditer(r'(?<![A-Z0-9])(?P<variants>[A-Z][A-Z0-9]*(?:\s*,\s*[A-Z][A-Z0-9]*)*)'
                               r'\s*(?::|(?=\s+\d+\s*steps?))', text)
    for match in declarations:
        for name in re.split(r'\s*,\s*', match.group('variants')):
            name = name.strip().upper()
            if (not re.fullmatch(r'[A-Z][A-Z0-9]*', name) or name in seen
                    or _root(name) != root):
                continue
            seen.add(name)
            operation_width = width
            if record.get('parameter_conventions', {}).get('d_prefix_is_32_bit'):
                operation_width = 32 if _root(name) != name.rstrip('P') else 16
            variants.append({'opcode': name, 'width_bits': operation_width,
                             'execution_form': 'function_selected' if root == 'EXTR' else
                             'pulse' if name.endswith('P') else 'continuous',
                             'call_form': None})
    return variants


def _slot_symbol(cell):
    compact = re.sub(r'\s+', '', cell or '')
    return compact if re.fullmatch(r'(?:[sSdDnNmMpP]|SD)[0-9]?', compact) else None


def _slot_symbols(cell):
    compact = re.sub(r'([SDsdNMnm])\s+(\d)', r'\1\2', _normalised(cell or ''))
    return compact.split() if compact and all(_slot_symbol(part) for part in compact.split()) else []


def _source_table_groups(source, record, page_tables=None):
    """Original table cells, preserving both table and physical page boundaries."""
    groups = []
    if record.get('pdf_pages') and source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tables'").fetchone():
        for page in record['pdf_pages']:
            for row in source.execute('SELECT rows_json FROM tables WHERE manual_id=? AND pdf_page=? '
                                       'ORDER BY table_index', (record['manual_id'], page)):
                try:
                    group = json.loads(row[0])
                except (TypeError, ValueError):
                    continue
                if isinstance(group, list):
                    groups.append([[str(cell or '') for cell in row] for row in group])
    if record['manual_id'].startswith('jy992d'):
        source_rows = page_tables if page_tables is not None else page_tables_source()
        for page in record.get('pdf_pages') or ():
            # The recovered cache flattens tables within one page. Header rows
            # below recover each operand table; pages are never concatenated.
            rows = source_rows.get('%s:%s' % (record['manual_id'], page), [])
            if rows:
                groups.append([[str(cell or '') for cell in row] for row in rows])
    # Some chunks retain a table that the separate table index did not classify.
    for part in re.split(r'\[TABLE[^\]]*\]', record['text'])[1:]:
        part = re.split(r'\[(?:PAGE|STRUCTURED)|\nSOURCE:', part, maxsplit=1)[0]
        rows = [[cell.strip().replace('<blank>', '') for cell in line.split('|')]
                for line in part.splitlines() if '|' in line]
        if rows:
            groups.append(rows)
    return groups


def _legacy_table_data(record):
    """The operand header and function row belonging to this exact mnemonic."""
    wanted = record['opcode'].upper()
    documented = LEGACY_HEADING_ALIASES.get(wanted, wanted)
    for grid in record.get('table_groups') or ():
        headers = [index for index, row in enumerate(grid)
                   if any(_normalised(cell).startswith('Operands') for cell in row)
                   and any(_normalised(cell).endswith('Function') for cell in row)]
        for offset, start in enumerate(headers):
            end = headers[offset + 1] if offset + 1 < len(headers) else len(grid)
            block = grid[start:end]
            function_column = next(index for index, cell in enumerate(block[0])
                                   if _normalised(cell).endswith('Function'))
            mnemonic = re.compile(r'(?:^|\s)%s\s*FNC\s*\d+' % re.escape(documented), re.I)
            declaration = re.compile(r'\bD?%sP?\s*(?:,|:|\s+\d+\s*steps?)' % re.escape(documented))
            if not (any(row and mnemonic.search(_normalised(row[0]).replace('Mnemonic ', ''))
                        for row in block)
                    or any(row and declaration.search(_normalised(row[-1])) for row in block)):
                continue
            operand_row = next((row for row in block[1:]
                                if any(_slot_symbols(cell) for cell in row)
                                and all(not cell.strip() or _slot_symbols(cell) for cell in row)), None)
            if operand_row is None:
                continue
            columns = [(index, symbol) for index, cell in enumerate(operand_row)
                       for symbol in _slot_symbols(cell)]
            for index, cell in enumerate(block[0]):
                if _normalised(cell).startswith('Operands '):
                    names = _slot_symbols(_normalised(cell)[len('Operands '):])
                    if names and len(names) == len(operand_row) - index - 1:
                        columns = [(index + offset, name) for offset, name in enumerate(names)]
            start_data = block.index(operand_row) + 1
            data = next((row for row in block[start_data:]
                         if len(row) == len(operand_row) and row[function_column].strip()
                         and not _slot_symbol(row[function_column])
                         and not row[function_column].startswith('FX')), None)
            if data:
                return {'symbols': [name for _, name in columns], 'columns': columns,
                        'documented_opcode': documented,
                        'row': data, 'function': data[function_column],
                        'steps': data[-1] + '\n' + block[0][-1].replace('Program steps', '')}
    return None


def _legacy_geometry_domains(record, table):
    """Bind each operand column to its actual, possibly merged, body cell."""
    domains = {}
    wanted = table.get('documented_opcode', record['opcode'])
    for page in record.get('parameter_pages', ()):
        for grid in page.get('parameter_tables', ()):
            cells = grid['cells']
            mnemonic = next((cell for cell in cells if re.search(
                r'\b' + re.escape(wanted) + r'\s+FNC\s*\d+', _normalised(cell['text']))), None)
            if not mnemonic:
                continue
            # The first body row gives the accepted devices. Notes below can
            # span all operand columns; the mnemonic's vertical midpoint may
            # fall inside such a note instead of the actual device cells.
            y = mnemonic['bbox'][1] + 1
            for name in table['symbols']:
                header = next((cell for cell in cells if _slot_symbol(cell['text']) == name
                               and cell['bbox'][3] <= mnemonic['bbox'][1] + 1), None)
                if header:
                    x = (header['bbox'][0] + header['bbox'][2]) / 2
                else:
                    labels = [word for word in page['words'] if word['text'] == name
                              and grid['bbox'][0] < word['x0'] < grid['bbox'][2]
                              and grid['bbox'][1] < word['top'] < mnemonic['bbox'][1]]
                    if not labels:
                        continue
                    label = max(labels, key=lambda word: word['top'])
                    x = (label['x0'] + label['x1']) / 2
                body = _cell_at(cells, x, y)
                if body:
                    domains[name] = {'text': _normalised(body['text']),
                                     'basis': 'own_operand_column_and_actual_merged_span'}
    return domains


def _legacy_native_parameter_rows(record, variant):
    table = _legacy_table_data(record)
    if not table:
        return None
    explicit = _legacy_geometry_domains(record, table)
    cells = [table['row'][index] for index, _ in table['columns']]
    if cells and all(re.search(r'\bN/A\b', cell) for cell in cells):
        return {'order': [], 'rows': []}
    rows = []
    width = variant['width_bits']
    conventions = record.get('parameter_conventions', {})
    root = _root(variant['opcode'])
    function = _normalised(table['function'])
    table_text = _normalised(' '.join(cell for grid in record.get('table_groups', ()) for line in grid for cell in line))
    for index, name in table['columns']:
        raw = explicit.get(name, {}).get('text', table['row'][index])
        forms = list(dict.fromkeys(re.findall(r'(?<![A-Za-z0-9])(?:Kn[XYMS]|[KHTCDVZRXYMS])(?![A-Za-z0-9])', raw)))
        forms = list(dict.fromkeys([*forms, *re.findall(r'(?<![A-Za-z0-9])([XYMS])\d+\b', raw)]))
        row = {'name': name, 'type': 'unknown', 'direction': 'unknown', 'description': function,
               'type_basis': 'own_operand_device_column_and_explicit_operation_width',
               'direction_basis': 'own_symbol_bound_description'}
        if re.search(r'valid pointers?|pointer number', raw, re.I):
            row.update(type='POINTER', direction='input')
        elif forms and all(form in {'X', 'Y', 'M', 'S'} for form in forms):
            row['type'] = 'BOOL'
        elif forms and any(form.startswith('Kn') or form in {'T', 'C', 'D', 'V', 'Z', 'R', 'K', 'H'} for form in forms):
            if re.fullmatch(r'[mn]\d*', name, re.I):
                row.update(type={'kind': 'integer'}, width_bits='unknown', type_completeness='partial')
            elif width in {16, 32} and conventions.get('d_prefix_is_32_bit'):
                row['type'] = 'ANY' + str(width)
        if re.fullmatch(r'[SD]\d*', name, re.I) and conventions.get('source_destination_symbols'):
            row['direction'] = 'input' if name.upper().startswith('S') else 'output'
            row['direction_basis'] = 'same_manual_explicit_source_destination_symbol_definition'
        elif re.fullmatch(r'[mn]\d*', name, re.I) and conventions.get('source_destination_symbols'):
            row['direction'] = 'input'
            row['direction_basis'] = 'same_manual_explicit_count_or_operational_constant_definition'
            if row['type'] == 'unknown':
                row.update(type={'kind': 'integer'}, width_bits='unknown', type_completeness='partial')
                row['type_basis'] = 'same_manual_explicit_count_or_operational_constant_definition'
        if root == 'PID' and name in {'S1', 'S2'} and re.search(
                r'S1 and S2 use a single data register', table_text):
            row['type'] = 'ANY16'
            row['type_basis'] = 'own_explicit_single_data_register_note'
        if root == 'PID' and name == 'S3' and re.search(
                r'25 consecutive data registers S3\+0 through S3\+24', _normalised(record['text'])) and re.search(
                r'Reserved for use for the internal processing', _normalised(record['text']), re.I):
            row.update(direction='in_out', direction_basis='own_parameter_stack_internal_processing_regions')
        if root in {'INC', 'DEC', 'NEG', 'ROL', 'ROR', 'RCL', 'RCR', 'SFWR', 'SFRD', 'WSFL', 'WSFR', 'SFTL', 'SFTR'}:
            if row['direction'] == 'output' and re.search(
                    r'increment|decrement|sign|rotat|shift|first.in|first.out|FIFO', record['text'], re.I):
                row['direction'] = 'in_out'
                row['direction_basis'] = 'own_documented_read_modify_write_operation'
        if root == 'XCH' and re.search(r'exchang|swap', function, re.I):
            row['direction'] = 'in_out'
            row['direction_basis'] = 'own_documented_mutual_exchange'
        if row['type'] != 'BOOL' and name.upper().startswith(('S', 'D')):
            if root == 'FLT' and name.upper().startswith('D') and re.search(r'float', record['text'], re.I):
                row['type'] = 'REAL'
            elif root == 'INT' and name.upper().startswith('S') and re.search(r'float', record['text'], re.I):
                row['type'] = 'REAL'
            elif root in {'EBCD', 'EBIN'} and re.search(r'scientific notation', record['text'], re.I):
                floating = name.upper().startswith('S') if root == 'EBCD' else name.upper().startswith('D')
                row['type'] = 'REAL' if floating else {'kind': 'scientific_notation', 'mantissa_bits': 16, 'exponent_bits': 16}
            elif root in {'EADD', 'ESUB', 'EMUL', 'EDIV', 'ESQR', 'ECMP', 'EZCP', 'SIN', 'COS', 'TAN'} and re.search(r'float', record['text'], re.I):
                row['type'] = 'REAL'
            elif root in {'BCD', 'BIN'} and 'M8023' in record['text']:
                # The flag selects a different data representation. Keep the
                # source dependence visible until that profile is resolved.
                row.update(type='unknown', type_completeness='partial')
        if row['type'] == 'unknown' and re.fullmatch(r'[SD]\d*', name, re.I):
            if root in {'ADD', 'SUB', 'MUL', 'DIV', 'WAND', 'WOR', 'WXOR', 'SQR'} and width in {16, 32}:
                row['type'] = 'ANY' + str(width)
                row['type_basis'] = 'own_numeric_or_word_operation_and_explicit_width'
        if root == 'MUL' and width in {16, 32} and name.upper().startswith('D') and re.search(
                r'(?:produce|produces) a (?:32|64) bit result', _normalised(record['text']), re.I):
            row['type'] = 'ANY' + str(width * 2)
            row['type_basis'] = 'own_explicit_double_width_multiplication_result'
        if re.fullmatch(r'SD\d+', name) and re.search(
                r'S or D varies depending on the function number', _normalised(record['text'])):
            row.update(role='function_selected_source_or_destination', direction='unknown',
                       type={'kind': 'function_selected', 'selector': {'parameter': 'S'},
                             'accepted_device_forms': forms, 'operation_width_options': [16, 32]},
                       type_completeness='partial',
                       type_basis='own_external_rom_function_number_selected_contract',
                       direction_basis='own_explicit_function_selected_source_or_destination')
        rows.append(row)
    return {'order': table['symbols'], 'rows': rows}


def _table_parameter_facts(record):
    table = _legacy_table_data(record)
    if not table:
        return []
    row, columns = table['row'], table['columns']
    cells = [row[index] for index, _ in columns]
    if cells and all(re.search(r'\bN/A\b', cell) for cell in cells):
        return [('parameters.operand_scope', 'authored:applied:no_table_operands',
                 {'applicable_devices': 'none', 'basis': 'documented_operand_table'})]
    facts = [('parameters.source_order', 'authored:applied:source_operand_order',
              {'symbols': table['symbols'], 'binding': 'documented_operand_table',
               'types_and_directions': 'separate_facts'})]
    slots = []
    geometry = _legacy_geometry_domains(record, table)
    for index, symbol in columns:
        # A blank cell may be merged with its neighbour in the PDF. Retain that
        # unresolved cell rather than borrowing its neighbour's devices.
        text = geometry.get(symbol, {}).get('text', row[index]).split('Note:', 1)[0]
        devices = re.findall(r'(?<![A-Za-z0-9])(?:Kn[XYMS]|[KHTCDVZXYMS])(?![A-Za-z0-9])', text)
        slots.append({'name': symbol, 'devices': list(dict.fromkeys(devices)),
                      'cell_binding': geometry[symbol]['basis'] if symbol in geometry else
                                      'explicit' if text.strip() else 'merged_cell_unresolved'})
    facts.append(('parameters.device_form', 'authored:applied:source_operand_devices',
                  {'operands': slots, 'binding': 'documented_operand_table',
                   'directions': 'not_inferred'}))
    if table['documented_opcode'] == 'WAND':
        printed = [name for name in ('DAND', 'DANDP') if re.search(r'\b' + name + r'\b', table['steps'])]
        if printed:
            facts.append(('constraints.source_identity', 'authored:applied:program_steps_variant_conflict',
                          {'documented_mnemonic': 'WAND', 'printed_variants': printed,
                           'conflict': 'program_steps_variant_and_mnemonic_family_disagree',
                           'alias_equivalence': 'not_established'}))
    return facts


def _structured_port_facts(record):
    """Ports are a set of named connections, not a guessed ST argument order."""
    if record['manual_id'] != 'fxcpu_basic_applied_m':
        return []
    ports = []
    for grid in record.get('table_groups') or ():
        text = ' '.join(_normalised(cell) for row in grid for cell in row if cell)
        if not re.search(r'\bEN\b.*\bENO\b', text):
            continue
        for cell in (cell for row in grid for cell in row):
            match = re.fullmatch(r'([A-Z][A-Z0-9_+\-]*)\s+((?:EN|ENO|[a-z][a-z0-9]*)(?:\s+(?:EN|ENO|[a-z][a-z0-9]*))*)',
                                 _normalised(cell))
            if match and _root(match[1].replace('_P', 'P')) == _root(record['opcode']):
                names = match[2].split()
                if names not in ports:
                    ports.append(names)
    # Recovered prose preserves the two-dimensional block's port names as
    # individual tokens. They establish connections, but no positional call ABI.
    if not ports:
        for match in re.finditer(r'\b([A-Z][A-Z0-9_+\-]*)\s+EN\s+ENO\s+'
                                 r'([A-Z][A-Z0-9_+\-]*)\s+(?:16|32)\s+bits\s+'
                                 r'(?:Continuous|Pulse)\s+(?P<ports>(?:[sdn][0-9]*|[- \n]){1,100})',
                                 record['text']):
            if _root(match[1].replace('_P', 'P')) == _root(record['opcode']):
                names = ['EN', 'ENO', *re.findall(r'\b[sdn][0-9]*\b', match['ports'])]
                if names not in ports:
                    ports.append(names)
    if not ports:
        return []
    return [('parameters.connection_role', 'authored:applied:block_ports',
             {'symbols': sorted({name for row in ports for name in row}),
              'binding': 'documented_structured_block_ports', 'argument_order': 'not_inferred',
              'types_and_directions': 'separate_facts'})]


def _source_operand_declarations(record, text):
    """Read literal symbols from Set data, before the device-availability table."""
    if record['manual_id'].startswith('jy992d'):
        body = text.split('Operation:', 1)[0]
        header = re.search(r'Mnemonic\s+Function\s+Program steps\s*(?P<body>.*)', body, re.S)
        if not header:
            return []
        for line in header['body'].splitlines()[:6]:
            names = _slot_symbols(line)
            if names:
                return [('parameters.source_order', 'authored:applied:prose_operand_header',
                         {'symbols': names, 'binding': 'documented_operand_table_header',
                          'types_and_directions': 'separate_facts'})]
        return []
    if record['manual_id'] != 'fx3_programming_r':
        return []
    if record['opcode'] == 'OUT' and re.search(
            r'OUT instruction drives coils of output relays \(Y\), auxiliary relays \(M\), '
            r'state relays \(S\), timers \(T\) and counters \(C\)', _normalised(text)):
        return [('parameters.device_form', 'authored:applied:out_target_families',
                 {'device_families': ['Y', 'M', 'S', 'T', 'C'], 'binding': 'documented_coil_target_list',
                  'timer_counter_parameters': 'separate_source_facts'})]
    declarations = []
    for match in re.finditer(r'(?:\n|^)2\. Set data\s+(?P<body>.*?)(?=\n3\. Applicable devices)', text, re.S):
        for line in match['body'].splitlines():
            found = re.match(r'^\s*(?P<name>Pn|[SDN][0-9]?|n[0-9]?)\s+.+', line)
            if found and found['name'] not in declarations:
                declarations.append(found['name'])
    if not declarations:
        return []
    return [('parameters.source_order', 'authored:applied:set_data_symbols',
             {'symbols': declarations, 'binding': 'documented_set_data_rows',
              'types_and_directions': 'separate_facts'})]


def _operand_types(record):
    """name -> documented data-type token from the structured operand record."""
    match = _OPERANDS.search(record['text'])
    roles, types = [], {}
    if not match:
        return roles, types
    for chunk in match.group('body').split(';'):
        entry = _OPERAND_ENTRY.match(chunk.strip())
        if not entry:
            continue
        raw_type = entry.group('type') or 'unknown'
        head = re.match(r'[A-Za-z][A-Za-z0-9_ ]{0,18}', raw_type)
        raw_type = head.group(0).strip() if head else 'unknown'
        token = re.sub(r'[^A-Za-z0-9]+', '_', raw_type).strip('_').lower() or 'unknown'
        purpose = (entry.group('purpose') or '').lower()
        name = entry.group('name').upper()
        if ('destination' in purpose
                or re.search(r'to which .*result is output', purpose)):
            purpose_class = 'destination'
        elif 'source' in purpose or 'comparison value' in purpose:
            purpose_class = 'source'
        else:
            purpose_class = 'operand'
        roles.append({'name': entry.group('name'), 'purpose_class': purpose_class,
                      'data_type': token})
        types[entry.group('name').upper()] = token.upper() if token != 'unknown' else 'unknown'
    return roles, types


def _relation(condition):
    lowered = condition.lower()
    for first, second, relation in RESULT_RELATIONS:
        if first not in lowered:
            continue
        if second and second not in lowered:
            continue
        operands = sorted(set(re.findall(r'\bS\d\b', condition)))
        return relation, operands
    return None, []


def _result_mapping(record, family=()):
    """Only result lines under this record's own heading belong to it."""
    mapping = []
    declared = set(record.get('declared_operands') or [])
    headings = [(match.start(), match.group('name').upper()) for match in _LEGACY_HEADING.finditer(record['text'])]
    owned = {record['opcode'].upper(), *[name.upper() for name in family]}
    for match in _RESULT_BIT.finditer(record['text']):
        condition = re.sub(r'\s+', ' ', match.group('condition')).strip()
        relation, operands = _relation(condition)
        if relation is None or not operands:
            continue
        if declared and not set(operands) <= declared:
            continue
        if headings:
            preceding = [name for position, name in headings if position < match.start()]
            if preceding and preceding[-1] not in owned:
                continue
        mapping.append({'relation': relation, 'operands': operands,
                        'result_device': match.group('device'),
                        'result_offset': int((match.group('offset') or '+0')[1:])})
    return mapping


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "documented"


def _normalised(text):
    return ' '.join(text.split())


def _root(name):
    """Variant-agnostic instruction name (CMP, CMPP, DCMP, DCMPP -> CMP)."""
    token = name.upper().rstrip('P')
    # D is part of these documented base mnemonics, rather than their double-
    # word modifier. In particular DIV/DDIV and DEG/DDEG must share a family.
    d_initial = {'DABIN', 'DEC', 'DECO', 'DEG', 'DI', 'DIS', 'DIV', 'DRVA', 'DRVI',
                 'DSW', 'DSZR', 'DUTY', 'DVIT'}
    if len(token) > 2 and token.startswith('D') and token not in d_initial:
        token = token[1:]
    return token


PAGE_TEXT_CACHE = {}


def page_text_source():
    """Page prose recovered from the private PDFs, keyed ``manual:page``.

    Encrypted or glyph-encoded pages carry no usable chunk text; the same manual's
    PDF still states the prose, so it is read from there instead of guessing.
    """
    if 'data' in PAGE_TEXT_CACHE:
        return PAGE_TEXT_CACHE['data']
    import json
    import os
    from pathlib import Path as _Path
    root = os.environ.get('GXWORKS_AGENT_PRIVATE_ROOT') or r'D:\gxworks-agent-private'
    path = _Path(root) / 'resources' / 'knowledge' / 'page_text.json'
    data = {}
    if path.exists():
        data = json.loads(path.read_text(encoding='utf-8'))
    PAGE_TEXT_CACHE['data'] = data
    return data


def _page_prose(record, page_text=None):
    """Prose for this record's pages, when the chunk text itself states nothing."""
    pages = record.get('pdf_pages') or []
    if not pages:
        return ''
    source = page_text if page_text is not None else page_text_source()
    parts = [source.get('%s:%s' % (record['manual_id'], page)) for page in pages]
    prose = '\n'.join(part for part in parts if part)
    if not prose:
        return ''
    if not record['manual_id'].startswith('jy992d'):
        return _owned_text(dict(record, text=prose))
    return _instruction_segment(prose, str(record['opcode']).strip())


FNC_HEADING = re.compile(r'\b([A-Z][A-Z0-9]{1,7})\s*\(\s*FNC\s*\d+\s*\)')


def _instruction_segment(prose, symbol):
    """The part of a page that belongs to one instruction, not the whole page.

    A page usually documents several instructions; the section only owns the text
    from its own heading up to the next heading.
    """
    if not symbol:
        return prose
    # Only numbered section headings delimit ownership. An inline "see CALL
    # (FNC 01)" reference is not the beginning of another instruction section.
    headings = list(_LEGACY_HEADING.finditer(prose))
    for index, match in enumerate(headings):
        if match.group('name').upper() != symbol.upper():
            continue
        start = match.start()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(prose)
        return prose[start:end].strip()
    pattern = re.compile(r'\b%s\b' % re.escape(symbol), re.I)
    owned = [line for line in prose.splitlines() if pattern.search(line)]
    return '\n'.join(owned)


PAGE_TABLES_CACHE = {}
TABLE_STEPS = re.compile(r'\b(\d{1,2})\s*steps?\b', re.I)
TABLE_DEVICES = re.compile(r'^[A-Z](\s*,\s*[A-Z])+(\s+and\s+[A-Z])?$')
TABLE_FUNCTION = (
    (re.compile(r'inverted on (?:every|each) operation', re.I), 'state_inverted_on_every_operation'),
    (re.compile(r'alternate state', re.I), 'alternate_state'),
    (re.compile(r'rising edge pulse', re.I), 'rising_edge_pulse'),
    (re.compile(r'falling / trailing edge pulse', re.I), 'falling_edge_pulse'),
    (re.compile(r'serial connection of', re.I), 'serial_connection'),
    (re.compile(r'parallel connection of', re.I), 'parallel_connection'),
    (re.compile(r'initial logical operation', re.I), 'initial_logical_operation'),
    (re.compile(r'^(?:set|sets)\b.*\breset\b', re.I), 'set_and_reset'),
    (re.compile(r'^(?:reset|resets)\b', re.I), 'reset'),
    (re.compile(r'^(?:set|sets)\b', re.I), 'set'),
    (re.compile(r'\bcounter\b', re.I), 'counter_operation'),
    (re.compile(r'\btimer\b', re.I), 'timer_operation'),
    (re.compile(r'\bcompar', re.I), 'comparison'),
    (re.compile(r'\badd', re.I), 'addition'),
    (re.compile(r'\bsubtract', re.I), 'subtraction'),
    (re.compile(r'\bmultipl', re.I), 'multiplication'),
    (re.compile(r'\bdivi', re.I), 'division'),
    (re.compile(r'\bmove|\btransfers?\b', re.I), 'data_transfer'),
    (re.compile(r'\bconver', re.I), 'conversion'),
    (re.compile(r'\bdecode', re.I), 'decoding'),
    (re.compile(r'\bencode', re.I), 'encoding'),
    (re.compile(r'\bshift', re.I), 'shift'),
    (re.compile(r'\brotat', re.I), 'rotation'),
)


TABLE_STATE_EFFECTS = {
    'state_inverted_on_every_operation': 'assigned_device_state_inverted_each_operation',
    'set_and_reset': 'assigned_device_set_or_reset',
    'set': 'assigned_device_set',
    'reset': 'assigned_device_reset',
    'data_transfer': 'destination_loaded_from_source',
    'conversion': 'destination_holds_converted_value',
    'shift': 'destination_shifted',
    'rotation': 'destination_rotated',
    'counter_operation': 'counter_value_updated',
    'timer_operation': 'timer_state_updated',
}


def page_tables_source():
    """Table cells of the legacy manual pages, keyed ``manual:page``."""
    if 'data' in PAGE_TABLES_CACHE:
        return PAGE_TABLES_CACHE['data']
    import json
    import os
    from pathlib import Path as _Path
    root = os.environ.get('GXWORKS_AGENT_PRIVATE_ROOT') or r'D:\gxworks-agent-private'
    path = _Path(root) / 'resources' / 'knowledge' / 'page_tables.json'
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    PAGE_TABLES_CACHE['data'] = data
    return data


def _table_rows(record, page_tables=None):
    pages = record.get('pdf_pages') or []
    if not pages:
        return []
    source = page_tables if page_tables is not None else page_tables_source()
    rows = []
    for page in pages:
        rows.extend(source.get('%s:%s' % (record['manual_id'], page), []))
    return rows


def _opcode_table_row(rows, symbol):
    """The table row that documents this mnemonic, and only that row."""
    wanted = symbol.strip().upper()
    for row in rows:
        for cell in row:
            lines = [line.strip().upper() for line in (cell or '').splitlines()]
            if wanted in lines or (cell or '').strip().upper() == wanted:
                return row
    return None


def _table_facts(record, row, opcode, models, languages=('ladder',)):
    """Facts a documented table row states: function, devices and program steps.

    The documented mnemonic cell keeps the identity, so a 16-bit row and a 32-bit
    row of the same family are two facts instead of one conflict.
    """
    facts = []
    documented = ''
    for cell in row:
        lines = [line.strip() for line in (cell or '').splitlines() if line.strip()]
        if lines and lines[0].upper().startswith(opcode.upper()):
            documented = lines[0]
            break
    stem = 'authored:applied:' + (_slug(documented) + ':' if documented else '')
    steps = next((int(match.group(1)) for cell in row
                  for match in [TABLE_STEPS.search(cell)] if match), None)
    if steps:
        facts.append(_fact(stem + 'program_steps', 'parameters.program_steps',
                           {'steps': steps}, opcode, record, models, languages=list(languages)))
    devices = next(([part.strip() for part in re.split(r',|and', cell) if part.strip()]
                    for cell in row if TABLE_DEVICES.match(cell.replace(' ', ''))), None)
    if devices:
        facts.append(_fact(stem + 'operand_scope', 'parameters.operand_scope',
                           {'devices': sorted(set(devices)), 'device_basis': 'documented_table_row'},
                           opcode, record, models, languages=list(languages)))
    function = next(((token, cell) for cell in row for pattern, token in TABLE_FUNCTION
                     if pattern.search(cell)), None)
    if function:
        facts.append(_fact(stem + 'documented_function', 'execution.documented_function',
                           {'documented_function': function[0]}, opcode, record, models,
                           languages=list(languages)))
        effect = TABLE_STATE_EFFECTS.get(function[0])
        if effect:
            # The row states what happens to the assigned device, so the effect is
            # documented rather than inferred from an operand role.
            facts.append(_fact(stem + 'documented_effect', 'effects.documented_effect',
                               {'documented_effect': effect, 'target': 'assigned_device',
                                'evidence_basis': 'documented_table_function'},
                               opcode, record, models, languages=list(languages)))
    return facts


def _owned_text(record):
    """The part of the chunk that belongs to this instruction section.

    A chunk can carry neighbouring instruction sections; statements are only
    read from this record's own headings so a related-device note on the same
    page cannot be attributed to the wrong instruction.
    """
    text = record['text']
    headings = list(_SECTION_HEADING.finditer(text))
    if not headings:
        return text
    section_number = re.match(r'^\s*(\d+(?:\.\d+)+)\b', record.get('section') or '')
    owned = {_root(record['opcode'])}
    parts = [text[:headings[0].start()]]
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        number = heading.group('number')
        if ((section_number and (number == section_number[1]
                                 or number.startswith(section_number[1] + '.')))
                or (not section_number and _root(heading.group('name')) in owned)):
            parts.append(text[heading.start():end])
    return '\n'.join(parts)


def _statement_facts(text):
    """Typed facts from the sentences this section actually prints."""
    flat = _normalised(text)
    found = []
    for dimension, identity, pattern, builder in STATEMENT_RULES:
        match = pattern.search(flat)
        if match is None:
            continue
        found.append((dimension, identity, builder(match)))
    return found


def _control_flow_facts(record, text):
    blocks = CONTROL_FLOW_FACTS.get(record['manual_id'], {}).get(record['opcode'])
    if not blocks:
        return []
    flat = _normalised(text)
    found = []
    for block in blocks:
        for dimension, identity, value, *witnesses in block:
            if witnesses and all(_normalised(witness) in flat for witness in witnesses):
                found.append((dimension, identity, value))
    return found


def _counts_as_evidence(row):
    """An unknown value or a metadata-only row never proves an aspect was extracted."""
    if row.get('status') not in EVIDENCE_STATUSES:
        return False
    if str(row.get('note', '')).startswith('unresolved_parameters'):
        return False
    value = row.get('value') or {}
    if value.get('returns') == 'unknown' or value.get('available') is False:
        return False
    dimension = row['dimension']
    if dimension in ROLE_DERIVED_EFFECTS:
        return False
    if (dimension == 'parameters.operand_scope'
            and value.get('applicable_devices') == 'none'):
        return True
    if dimension.startswith('parameters.') and dimension not in SUBSTANTIVE_PARAMETERS:
        return False
    return True


def _deduplicate(facts):
    """Same identity with two documented values keeps both, each with its own id."""
    deduplicated, conflicts = {}, []
    for owner, rows in facts.items():
        kept, positions = [], {}
        for row in rows:
            position = positions.get(row['id'])
            if position is None:
                positions[row['id']] = len(kept)
                kept.append(row)
                continue
            previous = kept[position]
            if previous['value'] == row['value']:
                continue
            previous_form = (previous.get('value') or {}).get('documented_form')
            row_form = (row.get('value') or {}).get('documented_form')
            if previous_form and row_form and previous_form != row_form:
                # Two documented call forms are two signatures, not a conflict.
                copy = dict(row, id=row['id'] + ':' + _slug(row_form))
                copy['depends_on'] = [identifier + ':' + _slug(row_form)
                                      for identifier in row.get('depends_on', [])]
                positions[copy['id']] = len(kept)
                kept.append(copy)
                continue
            alternative = len([entry for entry in kept if entry['id'].startswith(row['id'] + '#')]) + 1
            suffix = '#conflict%d' % alternative
            copy = dict(row, id=row['id'] + suffix, status='conflict')
            copy['depends_on'] = [identifier + suffix for identifier in row.get('depends_on', [])]
            kept.append(copy)
            kept[position] = dict(previous, status='conflict')
            conflicts.append({'model': owner[0], 'opcode': owner[1], 'fact_id': row['id'],
                              'kept_values': [previous['value'], row['value']]})
        deduplicated[owner] = kept
    return deduplicated, conflicts


def reviewed_applied_behavior(source, reviewed_forms=(), manuals=None, page_text=None,
                               page_tables=None):
    """Behaviour facts for FX native applied sections plus a per-section receipt."""
    layouts = {manual: MANUAL_LAYOUTS[manual] for manual in (manuals or MANUAL_LAYOUTS)
               if manual in MANUAL_LAYOUTS}
    reviewed = {form.upper() for form in reviewed_forms}
    known_opcodes = {str(row['opcode']).upper() for row in
                     source.execute('SELECT opcode FROM instructions') if row['opcode']}
    # A device number, board name or FNC number is not an instruction symbol.
    not_instruction = re.compile(r'^(?:[XYZMDWRTLCFVS]\d+|SM\d+|SD\d+|SB\d+|SW\d+|FNC\d+'
                                 r'|\d+.*)$', re.I)
    known_opcodes |= reviewed
    facts, sections, limitations, excluded = {}, [], [], []
    indexed = list(_instruction_records(source, layouts))
    parameter_conventions = {manual: _native_parameter_conventions(source, manual) for manual in layouts}
    reviewed_sections = {(record['manual_id'], record['section']) for record in indexed}
    records = [*indexed, *_chunk_records(source, layouts, reviewed_sections,
                                         {(record['manual_id'], page) for record in indexed
                                          for page in record['pdf_pages']})]
    for record in records:
        owner = SECTION_OWNERS.get((record['manual_id'], record['section']))
        if owner:
            selector = 'M8130' if record['section'].startswith('12.6.2') else 'M8132'
            record['opcode'] = owner
            record['conditions'] = [{'context': 'instruction_mode_selected', 'selector_device': selector}]
    occurrences = {}
    for record in records:
        occurrences[(record['manual_id'], record['opcode'])] = \
            occurrences.get((record['manual_id'], record['opcode']), 0) + 1
    for record in records:
        # Every row reports the pages its own evidence came from.
        record['pdf_pages'] = sorted({int(page) for page in (record.get('pdf_pages') or [])})
        if occurrences[(record['manual_id'], record['opcode'])] > 1:
            leaf = (record['section'] or '').split('>')[-1]
            pages = record['pdf_pages'] or []
            record['identity_suffix'] = '%s-p%s' % (_slug(leaf)[:40] or 'section',
                                                    pages[0] if pages else 0)
    reviewed_pages = {(record['manual_id'], page) for record in indexed for page in record['pdf_pages']}
    for record in records:
        if re.fullmatch(r'FX\d[A-Z0-9]*-(?:\d[A-Z]+-)?(?:ADP|BD)', record['opcode']):
            excluded.append({'manual_id': record['manual_id'], 'section': record['section'],
                             'reason': 'hardware_expansion_board_section', 'pdf_pages': record['pdf_pages']})
            continue
        editorial = next((reason for pattern, reason in EXCLUDED_SECTIONS
                          if re.search(pattern, record['section'] or '', re.I)), None)
        if editorial:
            # A chapter that explains how to read the manual states no instruction
            # behaviour; it is excluded by name, not reported as a source gap.
            excluded.append({'manual_id': record['manual_id'], 'section': record['section'],
                             'reason': editorial, 'pdf_pages': record['pdf_pages']})
            continue
        models, model_gap, conditional, model_condition = _models(
            record['applicability'], record['manual_models'], record['manual_scope_basis'])
        layout = layouts[record['manual_id']]
        extractor = {'structured_variant_table': _structured_variant_table,
                     'programming_manual_operation': _programming_manual_variants,
                     'legacy_comparison_table': _legacy_variants}[layout]
        scoped = dict(record, text=_owned_text(record))
        if layout != 'legacy_comparison_table':
            recovered = _page_prose(record, page_text)
            if recovered:
                scoped['text'] += '\n' + recovered
        scoped['table_groups'] = _source_table_groups(source, scoped, page_tables)
        scoped['parameter_pages'] = _parameter_source_pages(source, scoped)
        scoped['parameter_conventions'] = parameter_conventions[record['manual_id']]
        # Structure (forms, operands, widths) is read from this section's own chunk.
        # When that text states no sentence at all, the same manual's page prose
        # supplies the statements, and only the statements.
        prose_text = scoped['text']
        if (layout == 'legacy_comparison_table' or
                not _statement_facts(prose_text) and not _operand_types(scoped)[0]
                and not _PROGRAM_STEPS.search(prose_text)):
            prose = _page_prose(record, page_text)
            if prose:
                # A sentence which no current rule recognizes is still source.
                # The fallback supplements it; it must never replace that prose.
                prose_text = prose if layout == 'legacy_comparison_table' else prose_text + '\n' + prose
                if layout == 'legacy_comparison_table':
                    scoped['text'] += '\n' + prose
                scoped = dict(scoped, text_basis='pdf_page_text')
        table_rows = []
        if layout == 'legacy_comparison_table':
            table_rows = _table_rows(record, page_tables)
        variants = extractor(scoped)
        scoped['structured_parameter_rows'], scoped['structured_parameter_pins'] = {}, {}
        if layout == 'structured_variant_table':
            for variant in variants:
                rows, pins = _structured_variable_rows(scoped, variant['opcode'], variant['width_bits'])
                scoped['structured_parameter_rows'][variant['opcode']] = rows
                scoped['structured_parameter_pins'][variant['opcode']] = pins
        roles, operand_types = _operand_types(scoped)
        variable_rows = _variable_types(scoped['text'])
        mapping = _result_mapping(scoped, [variant['opcode'] for variant in variants])
        statements = _statement_facts(prose_text)
        own_table = _legacy_table_data(scoped)
        documented_opcode = own_table['documented_opcode'] if own_table else record['opcode']
        alias = LEGACY_HEADING_ALIASES.get(documented_opcode)
        if alias and re.search(r'\b%s\b' % re.escape(alias), prose_text):
            documented_opcode = alias
        operation_record = dict(record, opcode=documented_opcode)
        operation_text = prose_text + ('\n' + _normalised(own_table['function']) if own_table else '')
        operations = _operation_facts(operation_record, operation_text)
        specific = _section_specific_facts(record, prose_text)
        table_parameters = _table_parameter_facts(scoped)
        structured_ports = _structured_port_facts(scoped)
        source_declarations = _source_operand_declarations(scoped, prose_text)
        flow = _control_flow_facts(record, prose_text)
        fnc = _FNC.search(prose_text)
        steps = _PROGRAM_STEPS.search(prose_text)
        heading_symbols = [symbol for symbol in _heading_symbols(record['section'])
                           if not not_instruction.match(symbol)]
        family = ([record['opcode']] if (record['manual_id'], record['section']) in SECTION_OWNERS else
                  sorted({variant['opcode'] for variant in variants})
                  or [symbol for symbol in heading_symbols if symbol.upper() in known_opcodes]
                  or heading_symbols
                  or [record['opcode']])
        if not variants and not heading_symbols and not any(
                symbol.upper() in known_opcodes for symbol in family):
            # A chapter or device title is not an instruction section.
            sections.append({'manual_id': record['manual_id'], 'section': record['section'],
                             'layout': layout, 'opcodes': family, 'models': [],
                             'pdf_pages': record['pdf_pages'],
                             'disposition': 'section_not_an_instruction',
                             'reason': 'heading_names_are_not_instruction_symbols',
                             'aspects': [], 'missing_aspects': [], 'facts_added': 0})
            continue
        if family and all(symbol.upper() in reviewed for symbol in family):
            # Contact/edge/stack forms are authored by the behaviour module, so this
            # section is not an applied-extraction gap.
            sections.append({'manual_id': record['manual_id'], 'section': record['section'],
                             'layout': layout, 'opcodes': family, 'models': sorted(models or []),
                             'pdf_pages': record['pdf_pages'],
                             'disposition': 'authored_in_behaviour_review',
                             'reason': 'behaviour_module_owns_these_symbols',
                             'aspects': [], 'missing_aspects': [], 'facts_added': 0})
            continue
        if models is None:
            limitations.append({'manual_id': record['manual_id'], 'opcode': record['opcode'],
                                'pdf_pages': record['pdf_pages'], 'reason': model_gap})
            sections.append({'manual_id': record['manual_id'], 'section': record['section'],
                             'opcodes': family, 'models': [], 'pdf_pages': record['pdf_pages'],
                             'disposition': 'extraction_pending',
                             'aspects': [], 'missing_aspects': list(REQUIRED_ASPECTS),
                             'facts_added': 0, 'reason': model_gap})
            continue
        added, aspects, aspects_by_opcode = 0, set(), {}

        def attach(opcode, rows):
            nonlocal added
            if not rows:
                return
            for model in models:
                eligible = [row for row in rows if model in row['scope']['models']]
                if eligible:
                    facts.setdefault((model, opcode), []).extend(
                        _for_model(eligible, model, conditional, model_condition))
                    added += len(eligible)
            bucket = aspects_by_opcode.setdefault(opcode, set())
            for row in rows:
                if not _counts_as_evidence(row):
                    continue
                aspect = row['dimension'].split('.')[0]
                aspects.add(aspect)
                bucket.add(aspect)

        # A documented table row of this page states this instruction's function,
        # device list and program steps; it is attached like any other evidence.
        table_used = []
        if table_rows:
            own_table = _legacy_table_data(scoped)
            table_row = own_table['row'] if own_table else _opcode_table_row(table_rows, record['opcode'])
            if table_row is not None:
                for opcode in ([variant['opcode'] for variant in variants] or [record['opcode']]):
                    if opcode not in reviewed:
                        attach(opcode, _table_facts(record, table_row, opcode, models))
                table_used = [cell for cell in table_row if cell]
        for variant in variants:
            opcode = variant['opcode']
            if opcode in reviewed:
                continue
            rows = []
            if variant['call_form']:
                call = re.sub(r'\s+', '', variant['call_form'])
                rows.append(_fact('authored:applied:st_parameter_order', 'parameters.order',
                                  {'symbols': [name for name in call[call.index('(') + 1:-1].split(',') if name],
                                   'binding': 'documented_call_form', 'types_and_directions': 'separate_facts'},
                                  opcode, record, models, languages=['st']))
                signature, unresolved = _signature_from_form(scoped, opcode, variant['call_form'],
                                                             operand_types, variable_rows)
                rows.append(_fact('authored:applied:st_signature', 'parameters.st_signature', signature,
                                  opcode, record, models, languages=['st'],
                                  status='conflict' if signature.get('source_variable_conflicts') else
                                         'declared_unverified' if unresolved else 'source_verified',
                                  unresolved=unresolved))
                rows.append(_fact('authored:applied:st_typed_operands', 'parameters.typed_operands',
                                  {'operands': signature['parameters'], 'operand_count': len(signature['parameters']),
                                   'binding': 'documented_st_statement_and_variable_table',
                                   'declaration_kind': 'instruction', 'call_form': opcode,
                                   'executor_capability': 'not_established', 'admission_effect': 'none'},
                                  opcode, record, models, languages=['st'],
                                  status='conflict' if signature.get('source_variable_conflicts') else
                                         'declared_unverified' if unresolved else 'source_verified'))
                if signature.get('source_syntax_conflicts'):
                    rows.append(_fact('authored:applied:st_parameter_syntax_conflict', 'constraints.source_identity',
                        {'conflict': 'invalid_parameter_separator_or_identifier',
                         'documented_form': signature['documented_form'], 'bindings': signature['source_syntax_conflicts']},
                        opcode, record, models, languages=['st'], status='conflict'))
                rows.append(_fact('authored:applied:st_call_scope', 'identity.call_scope',
                                  {'source_documented_call': True, 'executor_capability': 'not_established',
                                   'admission_effect': 'none'}, opcode, record, models, languages=['st'],
                                  depends_on=['authored:applied:st_signature']))
            elif variant.get('source_call_mismatch'):
                signature, unresolved = _signature_from_form(scoped, opcode, variant['source_call_mismatch'],
                                                             operand_types, variable_rows)
                signature.update(expected_instruction=opcode, available='unknown',
                                 call_form=variant['source_call_mismatch'].split('(', 1)[0].strip(),
                                 conflict='table_instruction_and_call_name_disagree')
                rows.append(_fact('authored:applied:st_signature_conflict', 'parameters.st_signature',
                                  signature, opcode, record, models, languages=['st'], status='conflict',
                                  unresolved=unresolved))
                rows.append(_fact('authored:applied:st_typed_operands_conflict', 'parameters.typed_operands',
                    {'operands': signature['parameters'], 'operand_count': len(signature['parameters']),
                     'binding': 'documented_statement_with_disagreeing_call_name', 'declaration_kind': 'instruction'},
                    opcode, record, models, languages=['st'], status='conflict', unresolved=unresolved))
            elif layout == 'structured_variant_table':
                if re.search(r'instruction is not available in ST\b', _normalised(prose_text)):
                    rows.append(_fact('authored:applied:st_unavailable', 'identity.language_availability',
                        {'language': 'st', 'available': False, 'basis': 'own_explicit_language_exclusion'},
                        opcode, record, models, languages=['st']))
                    rows.append(_fact('authored:applied:st_signature_unavailable', 'parameters.st_signature',
                        {'available': False, 'parameters': [], 'returns': {'role': 'language_excluded'},
                         'enable': False, 'basis': 'own_explicit_language_exclusion'},
                        opcode, record, models, languages=['st']))
                alternative = ('structured_statement' if _NO_ST_SYNTAX.search(_normalised(prose_text)) else
                               'subroutine_function_block' if re.search(
                                   r'Use a subroutine program by reading out the function block', _normalised(prose_text)) else None)
                if alternative:
                    rows.append(_fact('authored:applied:st_direct_call_not_used', 'parameters.st_signature',
                        {'available': False, 'call_kind': 'direct_instruction', 'alternative_representation': alternative,
                         'parameters': [], 'returns': {'role': 'alternative_representation'}, 'enable': False,
                         'syntax_dependency': 'referenced_structured_programming_manual'},
                        opcode, record, models, languages=['st']))
            if variant.get('source_variant_mismatch'):
                rows.append(_fact('authored:applied:variant_identity_conflict', 'constraints.source_identity',
                                  {'documented_diagram_form': opcode,
                                   'printed_instruction_name': variant['source_variant_mismatch'],
                                   'conflict': 'instruction_name_and_block_diagram_disagree'},
                                  opcode, record, models, status='conflict'))
            rows.append(_fact('authored:applied:execution_form', 'execution.form',
                              {'form': variant['execution_form'], 'width_bits': variant['width_bits'],
                               **({'edge_qualifier': variant['execution_qualifier']}
                                  if variant.get('execution_qualifier') else {})},
                              opcode, record, models))
            rows.append(_fact('authored:applied:variant_family', 'parameters.variant_family',
                              {'section_variants': family, 'section_variant_count': len(family),
                               'family_basis': 'documented_section_variant_table'
                               if variant['call_form'] else 'documented_operation_width_heading',
                               **({'fnc_number': int(fnc.group('number'))} if fnc else {})},
                              opcode, record, models))
            rows.extend(_structured_parameter_facts(scoped, variant, models))
            rows.extend(_native_parameter_facts(scoped, variant, models))
            attach(opcode, rows)
        for opcode in ([variant['opcode'] for variant in variants] or [record['opcode']]):
            if opcode in reviewed:
                continue
            for dimension, identity, value in table_parameters:
                attach(opcode, [_fact(identity, dimension, value, opcode, record, models, languages=['ladder'],
                                      status='conflict' if dimension == 'constraints.source_identity' else 'source_verified')])
            for dimension, identity, value in structured_ports:
                attach(opcode, [_fact(identity, dimension, value, opcode, record, models,
                                      languages=['ladder', 'fbd'])])
            for dimension, identity, value in source_declarations:
                attach(opcode, [_fact(identity, dimension, value, opcode, record, models,
                                      languages=['ladder'])])
            if documented_opcode != record['opcode']:
                attach(opcode, [_fact('authored:applied:heading_identity_conflict',
                                      'constraints.source_identity',
                                      {'heading_symbol': record['opcode'],
                                       'documented_mnemonic': documented_opcode,
                                       'conflict': 'section_heading_and_mnemonic_disagree'},
                                      opcode, record, models, status='conflict')])
            if roles:
                attach(opcode, [_fact('authored:applied:operand_roles', 'parameters.operand_roles',
                                      {'operands': roles, 'role_basis': 'structured_operand_record'},
                                      opcode, record, models)])
                outputs = [role['name'] for role in roles if role['purpose_class'] == 'destination']
                if outputs:
                    attach(opcode, [_fact('authored:applied:destination_write',
                                          'effects.destination_write',
                                          {'target': 'destination_operand',
                                           'target_operands': outputs,
                                           'write': 'documented_output',
                                           'basis': 'documented_operand_role',
                                           'evidence_kind': 'operand_role_classification'},
                                          opcode, record, models)])
                sources = [role['name'] for role in roles if role['purpose_class'] == 'source']
                if sources:
                    attach(opcode, [_fact('authored:applied:source_read',
                                          'effects.source_read',
                                          {'source_operands': sources,
                                           'read': 'operand_value',
                                           'basis': 'documented_operand_role',
                                           'evidence_kind': 'operand_role_classification'},
                                          opcode, record, models)])
            if steps:
                attach(opcode, [_fact('authored:applied:program_steps', 'parameters.documented_steps',
                                      {'steps': int(steps.group('steps'))}, opcode, record, models)])
            if _NO_ST_SYNTAX.search(_normalised(record['text'])):
                attach(opcode, [_fact('authored:applied:st_availability',
                                      'constraints.language_availability',
                                      {'language': 'st', 'available': 'unknown',
                                       'basis': 'section_defers_syntax_to_other_manual',
                                       'dependency': 'cross_manual_syntax_reference',
                                       'referenced_manual_role': 'structured_programming_manual'},
                                      opcode, record, models, languages=['st'],
                                      status='declared_unverified')])
            for dimension, identity, value in statements:
                attach(opcode, [_fact(identity, dimension, value, opcode, record, models)])
            for dimension, identity, value in flow:
                attach(opcode, [_fact(identity, dimension, value, opcode, record, models)])
            for dimension, identity, value in operations:
                attach(opcode, [_fact(identity, dimension, value, opcode, record, models)])
            for dimension, identity, value in specific:
                attach(opcode, [_fact(identity, dimension, value, opcode, record, models)])
            attach(opcode, _legacy_detail_facts(record, prose_text, opcode, models))
        if mapping:
            for opcode in ([variant['opcode'] for variant in variants] or [record['opcode']]):
                if opcode in reviewed:
                    continue
                attach(opcode, [_fact('authored:applied:result_mapping', 'effects.result_mapping',
                                            {'mapping': mapping, 'result_points': len(mapping),
                                             'mapping_basis': 'documented_comparison_result_lines'},
                                            opcode, record, models)])
        # Completeness is decided per opcode, never by one fact anywhere in the
        # family making the whole section look finished.
        opcode_dispositions, missing_by_opcode = {}, {}
        for opcode in family:
            if opcode in reviewed:
                continue
            found = aspects_by_opcode.get(opcode, set())
            missing_by_opcode[opcode] = [aspect for aspect in REQUIRED_ASPECTS if aspect not in found]
            opcode_dispositions[opcode] = ('behaviour_complete' if found and not missing_by_opcode[opcode]
                                           else 'behaviour_partial' if found
                                           else 'extraction_pending')
        opcode_aspects = [aspects_by_opcode.get(opcode, set()) for opcode in family if opcode not in reviewed]
        aspects = set.intersection(*opcode_aspects) if opcode_aspects else set()
        missing = [aspect for aspect in REQUIRED_ASPECTS if aspect not in aspects]
        unresolved = []
        reviewed_gaps = SOURCE_GAP_REVIEW.get((record['manual_id'], record['section']), {})
        for aspect in missing:
            reason = reviewed_gaps.get(aspect)
            unresolved.append({'aspect': aspect,
                               'source_status': 'source_not_stated' if reason else 'source_present_not_extracted',
                               'reason': reason or 'no_rule_has_extracted_this_aspect'})
            if reason:
                for opcode in family:
                    if opcode not in reviewed:
                        attach(opcode, [_fact('authored:applied:source_gap:' + aspect,
                                              aspect + '.unresolved_source',
                                              {'state': 'unknown', 'reason': reason},
                                              opcode, record, models, status='unknown')])
        states = set(opcode_dispositions.values())
        if states and states == {'behaviour_complete'}:
            disposition = 'behaviour_complete'
        elif aspects or added:
            # Facts exist but not every aspect is proven: partial, never complete.
            disposition = 'behaviour_partial'
        else:
            disposition = 'extraction_pending'
        if not added:
            limitations.append({'manual_id': record['manual_id'], 'opcode': record['opcode'],
                                'pdf_pages': record['pdf_pages'],
                                'reason': 'section_text_states_no_extractable_statement'})
        sections.append({'manual_id': record['manual_id'], 'section': record['section'],
                         'layout': layout, 'opcodes': family, 'models': sorted(models),
                         'pdf_pages': record['pdf_pages'], 'aspects': sorted(aspects),
                         'missing_aspects': missing, 'facts_added': added,
                         'unresolved_aspects': unresolved,
                         'opcode_dispositions': opcode_dispositions,
                         'missing_aspects_by_opcode': missing_by_opcode,
                         'disposition': disposition})
    deduplicated, conflicts = _deduplicate(facts)
    # A source manual never speaks for a CPU it does not document.
    from tools.fact_behavior_review import _enforce_source_cpu_scope, _manual_cpu_models
    scope_receipt = {}
    deduplicated = _enforce_source_cpu_scope(deduplicated, _manual_cpu_models(source),
                                             scope_receipt, 'applied')
    # Section progress and merged knowledge are reported apart: another verified
    # section may already state the same knowledge, but its evidence pages stay
    # with that section and are never re-attributed here.
    aspects_by_owner, coverage_detail = {}, {}
    for owner, rows in deduplicated.items():
        covered = {}
        for row in rows:
            if not _counts_as_evidence(row):
                continue
            aspect = row['dimension'].split('.')[0]
            if aspect not in REQUIRED_ASPECTS:
                continue
            reference = row['sources'][0]
            covered.setdefault(aspect, []).append({'fact_id': row['id'],
                                                   'manual_id': reference['manual_id'],
                                                   'pdf_pages': reference['pdf_pages']})
        aspects_by_owner[owner] = covered
        coverage_detail['%s|%s' % owner] = {
            'covered_aspects': sorted(covered),
            'missing_aspects': [aspect for aspect in REQUIRED_ASPECTS if aspect not in covered]}
    for row in sections:
        owners = [(model, opcode) for model in row.get('models') or ()
                  for opcode in row.get('opcodes') or ()]
        elsewhere = {}
        for aspect in row.get('missing_aspects') or ():
            evidence = []
            for owner in owners:
                evidence.extend(aspects_by_owner.get(owner, {}).get(aspect, []))
            if evidence:
                elsewhere[aspect] = evidence
        if elsewhere:
            row['covered_elsewhere'] = elsewhere
            row['evidence_not_from_this_section'] = True
    uncovered = {}
    for covered in aspects_by_owner.values():
        for aspect in REQUIRED_ASPECTS:
            if aspect not in covered:
                uncovered[aspect] = uncovered.get(aspect, 0) + 1
    complete_owners = [owner for owner, covered in aspects_by_owner.items()
                       if all(aspect in covered for aspect in REQUIRED_ASPECTS)]
    residual_inventory = [
        {'manual_id': row['manual_id'], 'section': row['section'], 'opcodes': row['opcodes'],
         'pdf_pages': row['pdf_pages'], 'unresolved_aspects': row['unresolved_aspects']}
        for row in sections if row.get('unresolved_aspects')]
    receipt = {'method': METHOD, 'layouts': layouts, 'sections': len(sections),
               'completeness_scope': {'required_aspects': list(REQUIRED_ASPECTS),
                                      'full_instruction_semantics': 'not_established',
                                      'core_execution_capability': 'not_established'},
               'dispositions': {disposition: sum(1 for row in sections
                                                 if row['disposition'] == disposition)
                                for disposition in {row['disposition'] for row in sections}},
               'aspect_coverage': {aspect: sum(1 for row in sections if aspect in row['aspects'])
                                   for aspect in ('parameters', 'execution', 'effects',
                                                  'constraints', 'resources', 'memory')},
               'owner_count': len(deduplicated),
               'fact_count': sum(len(rows) for rows in deduplicated.values()),
               'conflicts': conflicts, 'source_limitations': limitations,
               'source_cpu_scope_excluded': scope_receipt.get('source_cpu_scope_excluded', []),
               'facts_dropped_without_covering_source':
                   scope_receipt.get('facts_dropped_without_covering_source', []),
               'excluded_sections': excluded, 'section_inventory': sections,
               'residual_inventory': residual_inventory,
               'knowledge_coverage': coverage_detail,
               'knowledge_summary': {'owners_total': len(aspects_by_owner),
                                     'owners_knowledge_complete': len(complete_owners),
                                     'uncovered_aspects': uncovered,
                                     'sections_partially_covered_elsewhere':
                                         sum(1 for row in sections if row.get('covered_elsewhere'))}}
    return deduplicated, receipt
