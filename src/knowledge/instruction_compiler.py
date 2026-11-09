"""Compile complete indexed instruction material into registry-owned facts.

Documents are joined by exact manual/revision/section, never a page window.
Every official source section is inventoried, including unrecognized material.
The resulting JSON contains facts and source locations, not duplicate manuals.
"""
from __future__ import annotations

import copy
from functools import lru_cache
import json
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path

from knowledge.instruction_document import (
    _operand_evidence_bindings, _operand_table_units, document_units,
    extract_binary_result_charts,
    extract_native_signature,
    extract_model_badge_marks,
)
from knowledge.source_authority import instruction_source_authority
from plc.instruction_definition import InstructionFactGroup
from plc.instruction_semantics import arbitrate_operand_usage


METHOD = 'instruction-material-compiler-v1'


from knowledge.source_identity import (build_index_identity, VERIFIED_AT_BUILD,
                                       NO_BUILD_REFERENCE, LEGACY_UNVERIFIED)


def instruction_heading_names(heading):
    """Literal instruction/function identities declared by a leaf heading."""
    leaf = str(heading or '').split(' > ')[-1]
    # Some native FNC headings omit spaces around '/'. Their explicit function
    # number distinguishes the declaration from ordinary titles such as I/O.
    native = re.match(r'^\s*\d+(?:\.\d+)*\.?\s+FNC\s*\d+\s*[–—-]\s*'
                      r'([A-Z][A-Z0-9_.$@+<>!=\-]*)\s*/', leaf)
    if native:
        return [native[1]]
    match = re.match(r'^\s*(?:\d+(?:\.\d+)*\.?\s+)?(?:FNC\s*\d+\s*[–—-]\s*)?([A-Z][A-Z0-9_.$@+<>!=\-]*)(\(_E\))?\s*/\s+', leaf)
    if match:
        return [match[1], match[1] + '_E'] if match[2] else [match[1]]
    match = re.search(r'[-(]\s*([A-Z][A-Z0-9_]*)(?:/(D\1))?\s+Instructions?\)?$', leaf)
    if match:
        return [name for name in match.groups() if name]
    # Basic IL instruction headings often list several literal forms without
    # a slash. Require a numbered heading and names separated by punctuation.
    match = re.match(r'^\s*\d+(?:\.\d+)+\s+([A-Z][A-Z0-9_.$@+<>!=\-]*(?:\s*[,/・]\s*[A-Z][A-Z0-9_.$@+<>!=\-]*)*)\s*$', leaf)
    return re.split(r'\s*[,/・]\s*', match[1]) if match else []


def call_admission_explanation(form, model):
    """Explain existing Registry CPU admission, not project/hardware validity.

    The caller retains the original supports_cpu boolean. In particular an
    empty declaration is a legacy permissive default, not verified all-CPU
    applicability. Reasons use the resolved CPU-specific spec, never base_spec.
    """
    if form is None:
        return 'form_not_registered', {'declared_scope': 'unavailable'}
    spec = form.spec
    scope = 'explicit' if spec.cpu_support else 'empty'
    details = {'declared_scope': scope}
    replacement = spec.replacement_for_cpu(model)
    if replacement:
        details['replacement_opcode'] = replacement
        return 'cpu_replacement_required', details
    if not spec.supports_cpu(model):
        return 'target_not_in_declared_scope', details
    if not spec.cpu_support:
        return 'admitted_with_empty_declared_scope', details
    return 'admitted', details


def source_knowledge_entry(opcode, model, facts, *, registry_member=False):
    """Adopt source-only knowledge without granting a new Core call."""
    source_ids = list(dict.fromkeys(str(source['id']) for fact in facts
                    for source in fact.get('sources', []) if source.get('id') is not None))
    return {'vendor': 'mitsubishi', 'opcode': opcode, 'target_model': model,
            'registry_member': registry_member, 'call_available': False,
            'call_admission_reason': 'knowledge_only_not_admitted',
            'call_admission_details': {'declared_scope': 'not_admitted_by_existing_owner_store'},
            'support_status': 'definition_only', 'facts': copy.deepcopy(facts),
            'source_reference_status': LEGACY_UNVERIFIED if source_ids else NO_BUILD_REFERENCE,
            'build_source_ids': source_ids, 'build_index_id': None,
            'source_materials': [], 'uninterpreted_content': [], 'conflicts': [],
            'gaps': ['manual_only_not_admitted'], 'semantic_completeness': 'not_established'}


def withdraw_native_call_admission(entry):
    """Remove a failed Native review, retaining separately admitted interfaces."""
    previous = entry.get('call_admission_details')
    details = copy.deepcopy(previous) if isinstance(previous, Mapping) else {}
    interfaces = []
    status = 'definition_only'
    for marker_name, interface, reviewed_status in (
        ('structured_call', 'st', 'reviewed_st_signature'),
        ('structured_expression', 'st', 'reviewed_st_expression'),
        ('fbd_call', 'fbd', 'reviewed_fbd_signature'),
    ):
        marker = details.get(marker_name)
        if (isinstance(marker, Mapping) and marker.get('available') is True
                and interface in details.get('interfaces', [])
                and (interface != 'fbd' or marker.get('graphical_interface_verified') is True)):
            if interface not in interfaces:
                interfaces.append(interface)
            status = reviewed_status
    for field in ('applicability', 'applicability_cases'):
        details.pop(field, None)
    details.update(interfaces=interfaces, declared_scope='explicit' if interfaces else 'not_admitted')
    changed = entry.get('call_available') is True and (
        not isinstance(previous, Mapping) or 'interfaces' not in previous
        or bool({'ladder', 'native_ladder_instruction'} & set(previous['interfaces'])))
    entry.update(call_available=bool(interfaces), support_status=status,
                 call_admission_reason='admitted' if interfaces else 'native_signature_not_admitted',
                 call_admission_details=details)
    return changed


def refresh_reviewed_call_admission(entry, registry):
    """Recompile construction admission only from an exact reviewed contract.

    This is an offline compiler operation. A retrieved signature or a behavior
    fact cannot register a call. Native arity, roles and widths must agree with
    every signature explicitly reviewed by the registry for this CPU/form.
    A documented applicability guard stays on the facts and the admission
    details; construction support does not certify the current CPU context.
    Existing registry snapshots refresh only for fields proved by that same
    reviewed contract; their prior values remain in debug history.
    """
    model, opcode = entry['target_model'], entry['opcode']
    form = registry.resolve_catalog_form(opcode, cpu=model)
    required = {'form_identity', 'cpu_applicability', 'arity', 'operand_order', 'operand_roles', 'operand_types'}
    if (form is None or not form.spec.supports_cpu(model)
            or not required <= set(form.spec.verified_fields) or not form.spec.contract_sources):
        return False
    spec = form.spec
    facts = entry.get('facts', [])
    sources = set()
    for source in spec.contract_sources:
        # Capability overlays can contribute hardware/completion metadata with
        # a different evidence shape. Only signature provenance participates
        # in signature admission; incomplete signature evidence still refuses.
        if not required.intersection(source.get('verified_fields', spec.verified_fields)):
            continue
        if (not source.get('manual_id') or not source.get('revision')
                or type(source.get('pdf_page')) is not int or source['pdf_page'] < 1):
            return False
        sources.add((source['manual_id'], source['revision'], source['pdf_page']))
    if not sources:
        return False

    def own_source(fact):
        return any((s.get('manual_id'), s.get('revision'), page) in sources
            for s in fact.get('sources', []) for page in s.get('pdf_pages', []))

    from plc.instruction_definition import DefinitionError, Expression, UnknownInstructionSemantics, canonical_expression
    from plc.instruction_constraints import version_condition_bounds
    from plc.instruction_relations import _operands

    def predicate_parts(expression):
        if expression.op == 'and':
            return [part for argument in expression.args for part in predicate_parts(argument)]
        return [repr(canonical_expression(expression))]

    def guard(conditions):
        try:
            parts = []
            for condition in conditions:
                # This compiler grants only the reviewed Native construction
                # signature. Its exact interface qualifier remains on the
                # facts and admission details, as it does for typed operands.
                if condition == {'context': 'project_mode', 'equals': 'native_ladder_instruction'}:
                    continue
                bounds = version_condition_bounds(condition, model=model)
                if bounds is not None:
                    path, minimum, maximum, minimum_exclusive = bounds
                    if minimum is not None:
                        parts.append(('documented_version', path,
                                      'minimum_exclusive' if minimum_exclusive else 'minimum', minimum))
                    if maximum is not None:
                        parts.append(('documented_version', path, 'maximum_exclusive', maximum))
                    continue
                predicate = Expression.from_mapping(condition['predicate'])
                if predicate.value_type.kind != 'bool':
                    return None
                parts.extend(predicate_parts(predicate))
            return frozenset(parts)
        except (DefinitionError, UnknownInstructionSemantics, TypeError, KeyError):
            return None

    def complement(conditions):
        conditions = [condition for condition in conditions if condition !=
                      {'context': 'project_mode', 'equals': 'native_ladder_instruction'}]
        if len(conditions) != 1:
            return None
        try:
            bounds = version_condition_bounds(conditions[0], model=model)
            if bounds is not None:
                path, minimum, maximum, minimum_exclusive = bounds
                # An interval's complement is a disjunction, not another
                # interval. Only a single documented threshold proves this.
                if minimum_exclusive or (minimum is None) == (maximum is None):
                    return None
                direction, threshold = (('maximum_exclusive', minimum) if minimum is not None
                                        else ('minimum', maximum))
                return frozenset({('documented_version', path, direction, threshold)})
            predicate = Expression.from_mapping(conditions[0]['predicate'])
            if predicate.op == 'not' and predicate.value_type.kind == 'bool':
                return frozenset(predicate_parts(predicate.args[0]))
        except (DefinitionError, UnknownInstructionSemantics, TypeError, KeyError):
            pass
        return None

    def region_count(value):
        if type(value) is int:
            return value > 0
        if not isinstance(value, Mapping):
            return False
        try:
            count = Expression.from_mapping(value)
            return (count.value_type.kind == 'int'
                    and (count.op != 'constant' or type(count.value) is int and count.value > 0))
        except (DefinitionError, UnknownInstructionSemantics, TypeError, KeyError):
            return False

    available = [f for f in facts if f['dimension'] == 'constraints.cpu_availability'
        and f.get('status') == 'source_verified' and own_source(f)]
    positive = [f for f in available if f['value'].get('available') is True]
    positive_guards = {guard(f.get('conditions', [])) for f in positive}
    if not positive or len(positive_guards) != 1 or None in positive_guards:
        return False
    version_facts = {f['id']: f for f in facts
        if f['dimension'] == 'constraints.cpu_version'
        and f.get('status') == 'source_verified' and f.get('verification') == 'source_checked'
        and f.get('scope') == {'models': [model], 'forms': [opcode], 'languages': ['ladder']}
        and own_source(f)}
    positive_versions = {identity for fact in positive for identity in fact.get('depends_on', [])
                         if identity in version_facts}
    for fact in available:
        if fact['value'].get('available') is True:
            continue
        conditions = fact.get('conditions', [])
        # The negative side of a documented requirement is not a second,
        # unconditional CPU exclusion. Retain that requirement for validation.
        if fact['value'].get('available') is not False:
            # Catalogue/family summaries deliberately omit a Boolean for a
            # version-qualified CPU. Accept only the same guard and shared
            # own-source version dependency as an explicit positive fact.
            if ('available' in fact['value']
                    or fact['value'].get('model_states') != {model: 'conditional'}
                    or fact.get('verification') != 'source_checked'
                    or fact.get('scope') != {'models': [model], 'forms': [opcode], 'languages': ['ladder']}
                    or guard(conditions) not in positive_guards):
                return False
            linked = positive_versions.intersection(fact.get('depends_on', []))
            matching = []
            for identity in linked:
                version = version_facts[identity]
                minimum = version.get('value', {}).get('minimum_series_version')
                same_source = any(a.get('manual_id') == b.get('manual_id')
                    and a.get('revision') == b.get('revision')
                    and set(a.get('pdf_pages', [])).intersection(b.get('pdf_pages', []))
                    for a in fact.get('sources', []) for b in version.get('sources', []))
                if (same_source and isinstance(minimum, str) and minimum.startswith('Ver.')
                        and guard([{'context': 'cpu_version', 'minimum': minimum[4:]}]) in positive_guards):
                    matching.append(identity)
            if len(matching) != 1:
                return False
            continue
        if complement(conditions) not in positive_guards:
            return False
    signatures = [spec.for_operand_count(len(s.operands)) for s in spec.native_overloads] or [spec]
    for signature in signatures:
        matched = False
        mode_matches = {}
        for fact in facts:
            if (fact['dimension'] != 'parameters.typed_operands' or fact.get('status') != 'source_verified'
                    or fact.get('verification') != 'source_checked' or not own_source(fact)
                    or fact.get('scope', {}).get('languages') != ['ladder']):
                continue
            qualifiers = [c for c in fact.get('conditions', []) if c !=
                          {'context': 'project_mode', 'equals': 'native_ladder_instruction'}]
            modes = [c for c in qualifiers if isinstance(c, Mapping) and c.get('context') == 'device_state']
            applicability = [c for c in qualifiers if c not in modes]
            if guard(applicability) not in positive_guards:
                continue
            qualifiers = modes
            if qualifiers and not (len(qualifiers) == 1 and qualifiers[0].get('context') == 'device_state'
                    and set(qualifiers[0]) == {'context', 'device', 'equals'}
                    and isinstance(qualifiers[0].get('device'), str) and type(qualifiers[0].get('equals')) is bool):
                continue
            value = fact['value']
            parameters = value.get('operands')
            if not isinstance(parameters, list):
                continue
            if not parameters and (signature.min_operands != 0 or signature.max_operands != 0
                                   or _operands(fact) is None):
                continue
            order = value.get('native_form', {}).get('operand_order')
            if order is None and value.get('representation') == 'native_ladder_instruction':
                order = [p.get('name') for p in parameters]
            if (order != list(signature.native_operand_order)
                    or [p.get('name') for p in parameters] != list(signature.native_operand_order)):
                continue
            for parameter, operand in zip(parameters, signature.operands):
                kind = parameter.get('type')
                width = re.fullmatch(r'ANY(16|32|64)', str(kind))
                bits = int(width[1]) if width else {
                    'INT': 16, 'UINT': 16, 'WORD': 16, 'DINT': 32, 'UDINT': 32, 'DWORD': 32,
                    'LINT': 64, 'ULINT': 64, 'LWORD': 64, 'REAL': 32, 'LREAL': 64,
                    'BOOL': 1,
                }.get(kind) if isinstance(kind, str) else None
                if isinstance(kind, Mapping) and kind.get('kind') in {'int', 'real'} and type(kind.get('bits')) is int:
                    bits = kind['bits']
                if isinstance(kind, Mapping) and kind.get('kind') == 'bool':
                    bits = 1
                role = {'input': 'read', 'output': 'write', 'in_out': 'read_write'}.get(parameter.get('direction'))
                storage_matches = (bits == 1 and operand.word_count == 1
                                   and parameter.get('occupied_bit_count', 1) == operand.bit_count if operand.data_type in {'bit', 'bool'}
                                   else bits == operand.word_count * 16)
                layout = parameter.get('word_device_layout', {})
                reference = parameter.get('passing') == 'device_reference'
                if (kind == 'STRING' and reference
                        and parameter.get('addressing') == 'start_device'
                        and operand.data_type == 'string' and operand.word_count == 1):
                    # This contract slot is the source-declared storage head,
                    # not a claim that the variable-length string occupies one
                    # word. Its payload extent remains a separate source rule.
                    storage_matches = True
                literal_widths = parameter.get('range', {}).get('integer_literal_widths', {})
                literal_storage = (kind == 'STRING' and isinstance(literal_widths, Mapping)
                                   and bool(operand.integer_literal_widths)
                                   and dict(operand.integer_literal_widths) == literal_widths)
                if (not storage_matches and reference and isinstance(layout, Mapping)
                        and layout.get('kind') == 'independent_elements'
                        and layout.get('single_scalar') is False
                        and layout.get('device_unit_bits') == layout.get('element_bits') == bits == 16
                        and type(layout.get('element_count')) is int
                        and layout.get('element_count') == layout.get('word_count') == operand.word_count):
                    # A consecutive record of words is not one wide scalar.
                    storage_matches = operand.data_type in {'word', 'any'}
                if (not storage_matches and (reference or literal_storage) and operand.word_count == 1
                        and operand.data_type in {'word', 'any'}
                        and isinstance(kind, str) and kind in {'DEVICE_REFERENCE', 'ANY_SIMPLE', 'STRING'}):
                    # These declarations name a storage head. Their sourced
                    # region layout, rather than the head's type spelling,
                    # describes the payload extent.
                    storage_matches = (
                        isinstance(layout, Mapping) and layout.get('device_unit_bits') == 16
                        and region_count(layout.get('word_count'))
                        or operand.data_type == 'any'
                        and region_count(parameter.get('occupied_bit_count')))
                sequence = parameter.get('bit_sequence_layout')
                if (not storage_matches and kind == 'BOOL' and reference
                        and operand.data_type in {'any', 'bit', 'bool'} and operand.word_count == 1
                        and region_count(parameter.get('occupied_bit_count'))
                        and isinstance(sequence, Mapping)
                        and set(sequence) == {'word_bits', 'bit_order', 'cross_word'}
                        and type(sequence['word_bits']) is int and sequence['word_bits'] == 16
                        and sequence['bit_order'] == 'ascending'
                        and sequence['cross_word'] in {'not_stated', 'next_word_bit_zero'}):
                    # A sourced bit-sequence head need not be a scalar word.
                    # Physical extent (including unselected word heads) remains
                    # the source-bound region consumer's separate decision.
                    storage_matches = True
                if kind == 'DEVICE_REFERENCE' and parameter.get('selector_kind') == 'device_comment_identity':
                    from plc.instructions import reviewed_comment_device_identity
                    # The registry slot describes one device-name operand,
                    # independently of any word-sized storage payload.
                    storage_matches = (operand.data_type == 'any' and operand.word_count == 1
                                       and reviewed_comment_device_identity(parameter))
                if kind == 'DEVICE_REFERENCE' and parameter.get('selector_kind') == 'remote_CPU_device_identity':
                    from plc.instructions import reviewed_remote_device_identity
                    storage_matches = (operand.data_type == 'any' and operand.word_count == 1
                        and reviewed_remote_device_identity(parameter)
                        and parameter['selector_CPU_parameter'].upper() in
                            {name.upper() for name in signature.native_operand_order})
                if operand.data_type == 'pointer':
                    # A scalar's coincidental width must never establish a
                    # program-pointer identity. Index policy is an independent
                    # source condition, not a prerequisite for a direct label.
                    from plc.instructions import reviewed_program_pointer_identity
                    storage_matches = (operand.device_prefixes == ('P',)
                                       and reviewed_program_pointer_identity(parameter))
                    # A source-qualified program pointer identifies a branch
                    # target; it does not declare a word-sized memory region.
                if not storage_matches or role != operand.role.value:
                    break
            else:
                if len(parameters) == len(signature.operands):
                    if qualifiers:
                        mode_matches.setdefault(qualifiers[0]['device'], set()).add(qualifiers[0]['equals'])
                    else:
                        matched = True
            if matched:
                break
        matched = matched or any(states == {False, True} for states in mode_matches.values())
        if not matched:
            return False
    # Refresh existing registry snapshots only after this exact call is proved.
    # Unreviewed fields and independent source facts keep their prior claims.
    reviewed = {fact['id']: fact for fact in _legacy_facts(form, model)
                if fact['status'] == 'source_verified'}
    for fact in facts:
        if fact['id'] in reviewed and fact != reviewed[fact['id']]:
            history = entry.setdefault('superseded_facts', [])
            if fact not in history:
                history.append(copy.deepcopy(fact))
    entry['facts'] = [copy.deepcopy(reviewed.get(fact['id'], fact)) for fact in facts]
    had_structured_call = (entry.get('support_status') == 'reviewed_st_signature'
        or entry.get('call_admission_details', {}).get('structured_call', {}).get('available') is True)
    entry.update(registry_member=True, call_available=True, support_status='reviewed_native_signature',
        call_admission_reason='admitted',
        call_admission_details={'declared_scope': 'explicit', 'interfaces': ['ladder']})
    cases = list({json.dumps(f.get('conditions', []), sort_keys=True): copy.deepcopy(f.get('conditions', []))
                  for f in positive}.values())
    if all(cases):
        entry['call_admission_details'].update(applicability='conditional', applicability_cases=cases)
    entry['gaps'] = [gap for gap in entry.get('gaps', []) if gap not in {
        'manual_only_not_admitted', 'outside_declared_cpu_scope',
        'no_literal_official_definition_section', 'native_parameter_order_not_established'}]
    if had_structured_call:
        # A native-only refresh must not silently erase a separately reviewed
        # ST interface. Revalidate its current sources instead of copying stale
        # admission metadata after a source change.
        refresh_reviewed_structured_call_admission(entry, registry)
    return True


def refresh_reviewed_structured_call_admission(entry, registry):
    """Admit an own-source ST call independently of its Native interface.

    Static ST binding and construction must be checked before installation.
    This compiler records only that interface; it makes no executor claim.
    A reviewed native contract keeps its separate admission and conditions.
    A catalogue name or another CPU's form cannot establish either interface.
    """
    from plc.structured_call_contract import resolve_structured_call_contract
    from gxw.models import GXWFormatError
    model, opcode = entry['target_model'], entry['opcode']
    native = registry.resolve_catalog_form(opcode, cpu=model)
    required_native = {'form_identity', 'cpu_applicability', 'arity', 'operand_order', 'operand_roles', 'operand_types'}
    native_admitted = (entry.get('call_available') is True
        and entry.get('support_status') == 'reviewed_native_signature'
        and native is not None and native.spec.supports_cpu(model)
        and required_native <= set(native.spec.verified_fields) and native.spec.contract_sources)
    result = resolve_structured_call_contract(entry.get('facts', []), opcode=opcode, model=model)
    details = entry.get('call_admission_details')
    details = details if isinstance(details, dict) else {}
    fb_signature = any(isinstance(fact, Mapping)
        and fact.get('dimension') == 'parameters.st_signature'
        and isinstance(fact.get('value'), Mapping)
        and fact['value'].get('call_kind') == 'function_block'
        for fact in entry.get('facts', []))
    previous_structured = details.get('structured_call')
    previous_fb_marker = (isinstance(previous_structured, Mapping)
                          and previous_structured.get('call_kind') == 'function_block')

    def withdraw_function_block(reason):
        current = dict(details)
        current.pop('structured_call', None)
        expression = current.get('structured_expression')
        if not (isinstance(expression, Mapping) and expression.get('available') is True):
            current['interfaces'] = [name for name in current.get('interfaces', []) if name != 'st']
        fbd = current.get('fbd_call')
        fbd_admitted = (isinstance(fbd, Mapping) and fbd.get('available') is True
                        and fbd.get('graphical_interface_verified') is True
                        and 'fbd' in current.get('interfaces', []))
        expression_admitted = (isinstance(expression, Mapping) and expression.get('available') is True)
        if native_admitted or fbd_admitted or expression_admitted:
            entry['call_admission_details'] = current
            return False
        entry.update(call_available=False, support_status='definition_only',
                     call_admission_reason='structured_instance_interface_not_admitted',
                     call_admission_details={'interfaces': [], 'reasons': [reason]})
        return False

    if not result.accepted:
        if ((fb_signature or previous_fb_marker)
                and (previous_fb_marker or entry.get('support_status') == 'reviewed_st_signature')):
            return withdraw_function_block(','.join(result.refusal_reasons)
                                           or 'structured_signature_not_admitted')
        if native_admitted and 'structured_call' in entry.get('call_admission_details', {}):
            details = entry['call_admission_details']
            details.pop('structured_call', None)
            details['interfaces'] = [interface for interface in details.get('interfaces', []) if interface != 'st']
        if entry.get('support_status') == 'reviewed_st_signature':
            entry.update(call_available=False, support_status='definition_only',
                         call_admission_reason='structured_signature_not_admitted',
                         call_admission_details={'interfaces': [], 'reasons': list(result.refusal_reasons)})
        return False
    contract = result.contract
    instance_interface = None
    if contract.call_kind == 'function_block':
        try:
            graphic_context = _fbd_graphic_context(model)
            sources = getattr(graphic_context, 'sources', None)
            if sources is not None:
                instance_interface = sources.st_instance_contract_interface(
                    opcode, contract.as_mapping())
        except (GXWFormatError, TypeError, ValueError, AttributeError):
            instance_interface = None
        if instance_interface is None:
            return withdraw_function_block('st_instance_interface_unavailable')

    structured = {'available': True, 'source_contract_fact_ids': list(contract.fact_ids),
                  'applicability_cases': copy.deepcopy(list(contract.conditions)),
                  'conditions_evaluated': False}
    if contract.call_kind == 'function_block':
        structured.update(call_kind='function_block', instance_required=True,
                          instance_interface_verified=True,
                          instance_interface_source={key: copy.deepcopy(instance_interface[key])
                              for key in ('source_kind', 'library', 'source_stream', 'definition',
                                          'kind', 'target_model', 'binding')})
    fbd = details.get('fbd_call')
    fbd_admitted = (isinstance(fbd, Mapping) and fbd.get('available') is True
                    and fbd.get('graphical_interface_verified') is True
                    and 'fbd' in details.get('interfaces', []))
    expression = details.get('structured_expression')
    expression_admitted = isinstance(expression, Mapping) and expression.get('available') is True
    if native_admitted or fbd_admitted or expression_admitted:
        details = entry.setdefault('call_admission_details', {})
        details['interfaces'] = list(dict.fromkeys([*details.get('interfaces', ['ladder']), 'st']))
        details['structured_call'] = structured
        return True
    details = {'declared_scope': 'explicit', 'interfaces': ['st'],
               'source_contract_fact_ids': list(contract.fact_ids), 'structured_call': structured}
    if contract.conditions:
        details.update(applicability='conditional',
                       applicability_cases=copy.deepcopy(list(contract.conditions)))
    entry.update(registry_member=True, call_available=True, support_status='reviewed_st_signature',
                 call_admission_reason='admitted', call_admission_details=details)
    entry['gaps'] = [gap for gap in entry.get('gaps', []) if gap != 'manual_only_not_admitted']
    return True


def refresh_reviewed_structured_expression_admission(entry, registry):
    """Admit a reviewed literal ST expression for its exact operator owner.

    Expression operators are not callable signatures or Native forms. This
    refresh records a separate ST-only marker after the source expression,
    type/arity/order dependencies, CPU scope, and shared ST operator grammar
    agree. Runtime project conditions remain unevaluated here.
    """
    from plc.structured_expression_contract import resolve_structured_expression_contract

    model, operator = entry['target_model'], entry['opcode']
    details = entry.get('call_admission_details')
    details = details if isinstance(details, dict) else {}
    existing = details.get('structured_expression')
    facts = entry.get('facts', [])
    has_expression_fact = any(isinstance(fact, dict)
        and fact.get('dimension') in {'parameters.language_expression',
                                      'parameters.operator_expression'}
        and isinstance(fact.get('scope'), dict)
        and 'st' in fact['scope'].get('languages', ()) for fact in facts)
    # Most refreshed owners are ordinary native or ST calls. Do not load the
    # shared language grammar for them; still re-resolve stale expression
    # metadata so a removed source expression is withdrawn below.
    if not has_expression_fact and not isinstance(existing, dict):
        return False

    feature = registry.language_feature('ST.OPERATORS', language='st', cpu=model)
    result = resolve_structured_expression_contract(
        facts, operator=operator, model=model, owner_opcode=operator,
        language_feature=feature if feature is not None else {'target': 'ST.OPERATORS', 'facts': []})

    if not result.accepted:
        if isinstance(existing, dict):
            details.pop('structured_expression', None)
            if (not details.get('structured_call')
                    and entry.get('support_status') != 'reviewed_st_signature'):
                details['interfaces'] = [name for name in details.get('interfaces', []) if name != 'st']
            if details:
                entry['call_admission_details'] = details
            else:
                entry['call_admission_details'] = {'interfaces': [],
                    'reasons': list(result.refusal_reasons)}
        if entry.get('support_status') == 'reviewed_st_expression':
            entry.update(call_available=False, support_status='definition_only',
                call_admission_reason='structured_expression_not_admitted',
                call_admission_details={'interfaces': [], 'reasons': list(result.refusal_reasons)})
        return False

    contract = result.contract
    marker = {'available': True, 'call_kind': 'expression',
        'representation': contract.representation,
        'operator': contract.operator,
        'owner_opcode': contract.owner_opcode or operator,
        'operator_form': contract.operator_form,
        'minimum_operands': contract.minimum_operands,
        'maximum_operands': contract.maximum_operands,
        'variadic': contract.variadic,
        'operand_names': list(contract.operand_names),
        'operand_types': list(contract.operand_types),
        'result_type': contract.result_type,
        'operator_priority': contract.operator_priority,
        'same_priority_evaluation': contract.same_priority_evaluation,
        'associativity': contract.associativity,
        'comparison_chain_intermediate_result_type': contract.comparison_chain_intermediate_result_type,
        'comparison_operand_type_compatibility': contract.comparison_operand_type_compatibility,
        'source_contract_fact_ids': list(contract.fact_ids),
        'applicability_cases': copy.deepcopy(list(contract.conditions)),
        'grammar_fact_id': contract.grammar_fact_id,
        'conditions_evaluated': False}

    native = registry.resolve_catalog_form(operator, cpu=model)
    required_native = {'form_identity', 'cpu_applicability', 'arity', 'operand_order',
                       'operand_roles', 'operand_types'}
    native_admitted = (entry.get('call_available') is True
        and entry.get('support_status') == 'reviewed_native_signature'
        and native is not None and native.spec.supports_cpu(model)
        and required_native <= set(native.spec.verified_fields) and native.spec.contract_sources)
    structured_call = details.get('structured_call')
    existing_reviewed_admission = (entry.get('call_available') is True
        and entry.get('support_status') in {'reviewed_native_signature', 'reviewed_st_signature'})
    if native_admitted or (existing_reviewed_admission
                           and (entry.get('support_status') in {
                                    'reviewed_native_signature', 'reviewed_st_signature'}
                                or isinstance(structured_call, dict)
                                and structured_call.get('available') is True)):
        details = dict(details)
        details['interfaces'] = list(dict.fromkeys([*details.get('interfaces', []), 'st']))
        details['structured_expression'] = marker
        details['source_contract_fact_ids'] = list(contract.fact_ids)
        entry['call_admission_details'] = details
        return True

    details = {'declared_scope': 'explicit', 'interfaces': ['st'],
        'source_contract_fact_ids': list(contract.fact_ids),
        'structured_expression': marker}
    if contract.conditions:
        details.update(applicability='conditional',
                       applicability_cases=copy.deepcopy(list(contract.conditions)))
    entry.update(registry_member=entry.get('registry_member', False), call_available=True,
        support_status='reviewed_st_expression', call_admission_reason='admitted',
        call_admission_details=details)
    entry['gaps'] = [gap for gap in entry.get('gaps', []) if gap != 'manual_only_not_admitted']
    return True


@lru_cache(maxsize=64)
def _fbd_graphic_context(model):
    """Cache the exact CPU's bundled blank-project MELSEC.lif interface view."""
    from gxw.object_model import read_project_context
    from gxw.project_templates import blank_fbd_baseline
    return read_project_context(blank_fbd_baseline(model), target_model=model)


def refresh_reviewed_fbd_instruction_block_admission(entry, registry):
    """Admit only a source-verified FBD block that matches the bundled graph pins.

    This is independent from Native and ST admission. It checks the owner's
    exact FBD source closure, then uses the selected CPU's existing MELSEC.lif
    interface and the ordinary FBD node/binding path before setting the FBD
    marker. Conditions remain attached and unevaluated at compile time.
    """
    from plc.fbd_instruction_block_contract import resolve_fbd_instruction_block_contract

    model, opcode = str(entry.get('target_model') or '').upper(), str(entry.get('opcode') or '').upper()
    details = entry.get('call_admission_details')
    details = details if isinstance(details, dict) else {}
    existing_marker = details.get('fbd_call')
    facts = entry.get('facts', [])
    has_fbd_fact = any(isinstance(fact, dict) and fact.get('dimension') == 'parameters.fbd_signature'
                       for fact in facts)
    if not has_fbd_fact and not isinstance(existing_marker, dict):
        return False

    resolution = resolve_fbd_instruction_block_contract(entry, opcode=opcode, model=model)
    refusal = list(resolution.refusal_reasons)
    graphic_binding = None
    contract = resolution.contract
    if contract is not None:
        try:
            from gxw.models import GXWFormatError
            from gxw.object_model import source_node_template
            context = _fbd_graphic_context(model)
            contract_mapping = contract.as_mapping()
            graphic_interface = context.sources.fbd_instruction_block_graphic_interface(
                opcode, contract_mapping)
            if graphic_interface is None:
                refusal.append('fbd_graphic_interface_missing_or_mismatched')
                contract = None
            if contract is None:
                raise GXWFormatError('no matching MELSEC.lif graphic interface')
            template = source_node_template('function:' + opcode, context.sources,
                                            fbd_contract=contract_mapping)
            graphic_binding = context.sources.callable(template, bind_instance=False,
                                                       fbd_contract=contract_mapping)
            if (graphic_binding.get('binding') != 'source_instruction_block'
                    or graphic_binding.get('source_contract', {}).get('fact_ids')
                        != list(contract.fact_ids)):
                refusal.append('fbd_graphic_binding_not_verified')
                contract = None
        except (GXWFormatError, ValueError, AttributeError, TypeError) as error:
            refusal.append('fbd_graphic_interface_unavailable:' + type(error).__name__)
            contract = None

    if contract is None:
        had_fbd_interface = ('fbd' in details.get('interfaces', [])
                             or isinstance(existing_marker, dict))
        details.pop('fbd_call', None)
        details['interfaces'] = [name for name in details.get('interfaces', []) if name != 'fbd']
        if entry.get('support_status') == 'reviewed_fbd_signature' or (
                had_fbd_interface and not details['interfaces'] and entry.get('call_available') is True):
            entry.update(call_available=False, support_status='definition_only',
                         call_admission_reason='fbd_instruction_block_not_admitted',
                         call_admission_details={'interfaces': [], 'reasons': refusal or ['fbd_signature_missing']})
        elif details:
            entry['call_admission_details'] = details
        return False

    marker = {
        'available': True,
        'call_kind': 'instruction_block',
        'representation': 'fbd_instruction_block',
        'source_contract_fact_ids': list(contract.fact_ids),
        'applicability_cases': copy.deepcopy(list(contract.conditions)),
        'conditions_evaluated': False,
        'graphical_interface_verified': True,
        'graphical_interface_source': {
            'library': graphic_interface['library'],
            'definition': graphic_interface['definition'],
            'kind': graphic_interface['kind'],
            'target_model': graphic_interface['target_model'],
            'binding': graphic_interface['binding'],
        },
    }
    had_other_admission = (entry.get('call_available') is True
        and entry.get('support_status') in {'reviewed_native_signature', 'reviewed_st_signature',
                                            'reviewed_st_expression'})
    details['interfaces'] = list(dict.fromkeys([*details.get('interfaces', []), 'fbd']))
    details['fbd_call'] = marker
    if had_other_admission:
        entry['call_admission_details'] = details
        return True

    details.setdefault('declared_scope', 'explicit')
    if contract.conditions:
        details.update(applicability='conditional',
                       applicability_cases=copy.deepcopy(list(contract.conditions)))
    entry.update(registry_member=True, call_available=True, support_status='reviewed_fbd_signature',
                 call_admission_reason='admitted', call_admission_details=details)
    entry['gaps'] = [gap for gap in entry.get('gaps', []) if gap != 'manual_only_not_admitted']
    return True


def _legacy_facts(form, model):
    """Project already-owned facts, keeping verification field-specific."""
    if form.spec.native_overloads:
        from dataclasses import replace
        facts = []
        for index, signature in enumerate(form.spec.native_overloads):
            selected = form.spec.for_operand_count(len(signature.operands))
            for fact in _legacy_facts(replace(form, spec=selected), model):
                if fact['dimension'].startswith('parameters.'):
                    fact['id'] += ':native_arity_' + str(len(signature.operands))
                    fact['value']['native_form'] = {'operand_order': list(signature.native_order),
                        'minimum_operands': len(signature.operands), 'maximum_operands': len(signature.operands)}
                    facts.append(fact)
                elif index == 0:
                    facts.append(fact)
        return facts
    spec, coverage = form.spec, form.spec.contract_coverage()
    scope = {'models': [model], 'forms': [form.opcode], 'languages': ['ladder']}
    facts = []
    devices = {'prefixes': [list(o.device_prefixes) for o in spec.operands]}
    address_forms = [list(o.address_forms) for o in spec.operands]
    if coverage.get('device_classes') == 'source_verified':
        if any(address_forms):
            devices['address_forms'] = address_forms
        address_form_limits = [{form: dict(limits) for form, limits in o.address_form_limits}
                               for o in spec.operands]
        if any(address_form_limits):
            devices['address_form_limits'] = address_form_limits
    values = [
        ('identity.form', 'form_identity', {'opcode': form.opcode, 'base_opcode': form.base_mnemonic,
                                         'double': form.double, 'pulse': form.pulse}),
        ('identity.applicability', 'cpu_applicability', {'declared_models': sorted(spec.cpu_support),
                                                       'available': spec.supports_cpu(model)}),
        ('parameters.arity', 'arity', {'minimum': spec.min_operands, 'maximum': spec.max_operands}),
        ('parameters.order', 'operand_order', {'symbols': list(spec.native_operand_order)}),
        ('parameters.roles', 'operand_roles', {'roles': [o.role.value for o in spec.operands]}),
        ('parameters.types', 'operand_types', {'types': [o.data_type for o in spec.operands]}),
        ('parameters.devices', 'device_classes', devices),
        ('parameters.width', 'instruction_width', {'bits': spec.instruction_width}),
        ('execution.form', 'execution_form', {'form': spec.execution_form}),
        ('resources.completion', 'completion_ownership',
         {'device': spec.completion.device, 'placement': spec.completion.placement} if spec.completion else {}),
        ('resources.pulse_output', 'hardware_applicability',
         {'frequency_positions': [i + 1 for i in spec.pulse_output.frequency_operand_indexes],
          'output_position': spec.pulse_output.pulse_output_operand_index + 1,
          'direction_position': (spec.pulse_output.direction_output_operand_index + 1)
                                if spec.pulse_output.direction_output_operand_index is not None else None}
         if spec.pulse_output else {}),
        ('constraints.numeric_ranges', 'numeric_and_memory_boundaries',
         {'ranges': [dict(vars(item)) for item in spec.numeric_operand_boundaries]}),
        ('memory.disjoint_ranges', 'numeric_and_memory_boundaries',
         {'ranges': [dict(vars(item)) for item in spec.disjoint_bit_ranges]}),
    ]
    for dimension, legacy, value in values:
        if not value or all(v in (None, [], {}) for v in value.values()):
            continue
        status = coverage.get(legacy, 'unresolved')
        status = 'unknown' if status == 'unresolved' else status
        sources = [{**dict(source), 'target_model': model, 'opcode': form.opcode,
                    'legacy_field': legacy} for source in spec.contract_sources
                   if not source.get('verified_fields') or legacy in source['verified_fields']]
        if status == 'source_verified' and not sources:
            # A legacy label alone is never new source evidence.
            status = 'declared_unverified'
        if not sources:
            sources = [{'reference': 'existing_instruction_registry', 'opcode': form.opcode, 'target_model': model,
                        'legacy_field': legacy}]
        facts.append({'id': 'registry:' + dimension, 'dimension': dimension, 'value': value,
                      'status': status, 'sources': sources, 'scope': scope,
                      'verification': 'source_checked' if status == 'source_verified' else 'not_performed',
                      'conditions': [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}]})
    return facts


def _record(chunk):
    return {**chunk, 'id': str(chunk['id']), 'manual_text': chunk['text'],
            'source': chunk.get('manual_title', ''), 'indexed_operands': []}


def _signature_facts(signature, opcode, model):
    """Sourced structural candidates; extraction never inherits review status."""
    source = {'manual_id': signature['manual_id'], 'pdf_page': signature['page'],
              'manual_number': signature.get('manual_number'), 'revision': signature.get('revision'),
              'id': signature.get('source_id'),
              'section': signature['section'], 'offset_basis': 'page_artifacts.word_geometry_json',
              'table': signature['table_index'], 'bbox': signature['table_bbox'],
              'opcode': opcode, 'target_model': model}
    scope = {'models': [model], 'forms': [opcode], 'languages': ['ladder']}
    metadata = signature['forms'][opcode]
    values = [('native:order', 'parameters.source_order', {'symbols': signature['symbols']}),
              ('native:identity', 'identity.literal_mnemonic',
               {'opcode': opcode, 'family_heading': signature['base'], 'bbox': metadata['bbox']})]
    if metadata['instruction_width']:
        values.append(('native:width', 'parameters.source_width', {'bits': metadata['instruction_width']}))
    if metadata['execution_form']:
        values.append(('native:execution', 'execution.source_form', {'form': metadata['execution_form']}))
    mark = next((mark for mark in signature.get('model_marks', []) if mark['model'] == model), None)
    if mark:
        values.append(('native:model_mark', 'identity.source_model_mark',
                       {'target_model': model, 'highlighted': mark['highlighted'], 'bbox': mark['bbox'],
                        'interpretation': 'source_highlight_only; CPU_support_not_promoted'}))
    return [{'id': identity, 'dimension': dimension, 'value': value, 'status': 'candidate_evidence',
             'sources': [{**source, **({'offset_basis': 'local_official_PDF.rects', 'bbox': value['bbox'], 'table': None}
                                      if dimension == 'identity.source_model_mark' else {})}],
             'scope': scope, 'verification': 'not_performed',
             'conditions': [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}]}
            for identity, dimension, value in values]


def _operand_facts(records, symbols, opcode, model, *, definition_pages=None):
    bindings = []
    gaps = [{'position': i + 1, 'symbol': symbol, 'facet': 'purpose'} for i, symbol in enumerate(symbols)]
    for record in records:
        for start, end in _operand_table_units(record['text']):
            values = _operand_evidence_bindings(record, start, end, gaps, symbols)
            # Later operation-mode tables may redefine the same symbol under
            # conditions absent from this flat slot view. Preserve that source
            # as unprocessed material instead of claiming a global purpose.
            if definition_pages is not None:
                values = [value for value in values if
                          (record['manual_id'], value['source']['pdf_page']) in definition_pages]
            bindings.extend(values)
    groups = defaultdict(list)
    for binding in bindings:
        if binding.get('fact'):
            groups[(binding['position'], binding['facet'])].append(binding['fact'])
    facts, conflicts = [], []
    for (position, facet), candidates in sorted(groups.items()):
        resolved, collision = arbitrate_operand_usage(candidates)
        dimension = f'parameters.{position}.{facet}'
        identity = f'operand:{position}:{facet}'
        if collision:
            conflicts.append({'position': position, 'facet': facet, 'candidates': candidates})
            facts.append({'id': identity, 'dimension': dimension, 'status': 'conflict',
                          'value': {'position': position, 'symbol': symbols[position - 1], 'facet': facet,
                                    'reason': 'multiple_candidate_values', 'candidates': candidates},
                          'sources': [s for c in candidates for s in c.get('sources', [])],
                          'scope': {'models': [model], 'forms': [opcode], 'languages': ['ladder']},
                          'conditions': [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}]})
        else:
            for index, fact in enumerate(resolved):
                sources = [{**s, 'target_model': model, 'opcode': opcode} for s in fact.get('sources', [])]
                facts.append({'id': identity + (f':{index}' if len(resolved) > 1 else ''), 'dimension': dimension,
                              'value': {'position': position, 'symbol': symbols[position - 1], 'facet': facet,
                                        'value': fact['value'], **({'conditions': fact['conditions']} if fact.get('conditions') else {})},
                              'status': fact['status'], 'sources': sources,
                              'scope': {'models': [model], 'forms': [opcode], 'languages': ['ladder']},
                              'conditions': [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}]})
    # Independent row facts retain their own strict slot/facet arbitration.
    # The runtime quarantines the whole contradictory raw unit, while packing
    # these unambiguous facts without copying the conflicting row back in.
    return facts, conflicts


def _chart_facts(charts, opcode, model):
    signatures = {tuple((m['output'], m['comparison']) for m in chart['members']) for chart in charts}
    if len(signatures) != 1:
        conflict = [{'reason': 'different_complete_result_charts', 'chart_ids': [c['id'] for c in charts]}] if charts else []
        facts = [{'id': 'chart:results', 'dimension': 'effects.result_mapping', 'status': 'conflict',
                  'value': {'reason': 'different_complete_result_charts'}, 'sources': [c['source'] for c in charts],
                  'scope': {'models': [model], 'forms': [opcode], 'languages': ['ladder']},
                  'conditions': [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}]}] if charts else []
        return facts, conflict
    chart = charts[0]
    chart = copy.deepcopy(chart)
    for member in chart['members']:
        member['target']['kind'] = member['target'].pop('unit', 'bit')
    scope = {'models': [model], 'forms': [opcode], 'languages': ['ladder']}
    sources = [chart['source']]
    members = [m['output'] for m in chart['members']]
    facts = [
        {'id': 'chart:input_type', 'dimension': 'parameters.value_type', 'value': chart['value_type']},
        {'id': 'chart:enable', 'dimension': 'execution.enable', 'value': {'condition': 'command_input', 'active': 1}},
        {'id': 'chart:disabled', 'dimension': 'execution.disabled_retention',
         'value': {'action': chart['disabled'], 'outputs': [m['target'] for m in chart['members']]}, 'members': members},
        {'id': 'chart:results', 'dimension': 'effects.result_mapping', 'members': members,
         'depends_on': ['chart:input_type', 'chart:enable', 'chart:disabled', 'registry:parameters.order'],
         'value': {'behavior': 'conditional_results', 'outputs': chart['members'], 'text': chart['text'],
                   'source_unit_id': chart['source_unit_id'], 'representation': chart['representation']}},
    ]
    return [{**f, 'status': 'candidate_evidence', 'sources': sources, 'scope': scope,
             'verification': 'not_performed',
             'conditions': [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}]} for f in facts], []


def _declared_instruction_names(rows, outline, section, manual):
    """Names a section declares in literal form even when its outline omits them.

    The shared document parser owns this reading; the compiler only borrows its
    result for section ownership, never for CPU or language availability. Each
    page of the section is offered its own text, with the whole section as the
    context it may quote from.
    """
    from knowledge.instruction_document import instruction_declaration_names
    ordered = sorted(rows, key=lambda r: (r['pdf_page'], r.get('block_index', 0), r['id']))
    whole = '\n'.join(str(r.get('text') or '') for r in ordered)
    manual_type = str(manual.get('manual_type') or '').strip().lower()
    path = str(outline or section or '')
    names = []
    for row in ordered:
        names.extend(instruction_declaration_names(str(row.get('text') or ''), path,
                                                   manual_type, source_text=whole))
    return [name for name in dict.fromkeys(names) if name]


def _section_identity(chunk):
    """Chapter identity: the full outline path, or the leaf title when it has none.

    Two sections can share a leaf title under different parents (several FX5
    application sections are all titled 'Outline of operation'), so the leaf
    alone cannot key a document.
    """
    return str(chunk.get('outline_path') or chunk.get('section') or '').strip()


def _document_catalog(chunks, manuals):
    grouped = defaultdict(list)
    for chunk in chunks:
        if chunk['manual_id'] in manuals:
            grouped[(chunk['manual_id'], chunk['revision'], _section_identity(chunk))].append(chunk)
    documents, by_form = [], defaultdict(list)
    for (manual, revision, outline), rows in sorted(grouped.items()):
        section = str(rows[0].get('section') or outline).strip()
        names = instruction_heading_names(section) or instruction_heading_names(outline)
        if not names:
            # iQ-F style sections declare their instructions without naming them
            # in the outline; without this they would claim no literal section.
            names = _declared_instruction_names(rows, outline, section, manuals[manual])
        identity = f'{manual}:{revision}:{outline}'
        record_rows = sorted(rows, key=lambda r: (r['pdf_page'], r.get('block_index', 0), r['id']))
        models = set(manuals[manual].get('plc_models', []))
        for raw in record_rows:
            scope = str(raw.get('plc_models') or '').strip()
            if scope and scope.upper() not in ('ALL', '*', 'COMMON'):
                models.intersection_update(value.strip() for value in scope.split(','))
        units = []
        for raw in record_rows:
            for unit in document_units(_record(raw)):
                units.append({'id': unit.id, 'chunk_id': str(raw['id']), 'representation': unit.representation,
                              'pdf_page': unit.page, 'source_span': {'start': unit.start, 'end': unit.end},
                              'cell_count': len(unit.cells), 'processing_status': 'inventoried'})
                table = re.match(r'\[TABLE page=(\d+) index=(\d+) bbox=(\[[^\n]+?\])\]', raw['text'][unit.start:unit.end])
                units[-1]['geometry_reference'] = {'table': int(table[2]), 'bbox': json.loads(table[3])} if table else {
                    'table': None, 'page_artifact': {'manual_id': manual, 'pdf_page': unit.page},
                }
        document = {'id': identity, 'manual_id': manual, 'manual_number': manuals[manual]['manual_number'],
                    'revision': revision, 'section': section, 'outline_path': outline,
                    'forms': names,
                    'models': sorted(models),
                    'source_ids': [str(r['id']) for r in record_rows],
                    'pdf_pages': sorted({p for r in record_rows for p in range(r['pdf_page'], r['pdf_page_end'] + 1)}),
                    'units': units, 'explicit_references': [], 'unresolved_references': [],
                    'section_association': 'literal_instruction_heading' if names else 'non_instruction_or_unclassified'}
        for name in names:
            by_form[name].append((document, record_rows))
        documents.append(document)
    # Child sections belong to the nearest explicit instruction heading in
    # this same manual/revision. Numeric ancestry, never page adjacency.
    numbered = {}
    for document in documents:
        match = re.match(r'^(\d+(?:\.\d+)+)\s', document['section'])
        if match and document['forms']:
            numbered[(document['manual_id'], document['revision'], match[1])] = document
    rows_by_document = {(m + ':' + r + ':' + s): rows for (m, r, s), rows in grouped.items()}
    for document in documents:
        match = re.match(r'^(\d+(?:\.\d+)+)\s', document['section'])
        if not match or document['forms']:
            continue
        pieces = match[1].split('.')
        for length in range(len(pieces) - 1, 1, -1):
            parent = numbered.get((document['manual_id'], document['revision'], '.'.join(pieces[:length])))
            if parent:
                document['forms'] = list(parent['forms'])
                document['definition_parent'] = parent['id']
                document['section_association'] = 'explicit_section_ancestry'
                for name in document['forms']:
                    by_form[name].append((document, rows_by_document[document['id']]))
                break
    # Resolve every explicit same-manual section number. Retrieval limits do
    # not limit the source dependency graph; ambiguous references stay visible.
    by_number = defaultdict(list)
    for document in documents:
        match = re.match(r'^(\d+(?:\.\d+)+)\s', document['section'])
        if match:
            by_number[(document['manual_id'], document['revision'], match[1])].append(document)
    by_id = {d['id']: d for d in documents}
    raw_by_id = {str(c['id']): c for c in chunks}
    for document in documents:
        text = '\n'.join(raw_by_id[s]['text'] for s in document['source_ids'])
        references = sorted(set(re.findall(r'(?i)(?:refer to|see)\s+(?:sub)?section\s+(\d+(?:\.\d+)+)', text)))
        for number in references:
            targets = by_number.get((document['manual_id'], document['revision'], number), [])
            if len(targets) == 1 and targets[0]['id'] != document['id']:
                document['explicit_references'].append({'section_number': number, 'document_id': targets[0]['id']})
            else:
                document['unresolved_references'].append({'section_number': number, 'reason': 'missing_or_ambiguous_exact_section'})
    for document in documents:
        visited, pending = {document['id']}, [r['document_id'] for r in document['explicit_references']]
        dependencies = []
        while pending:
            identity = pending.pop(0)
            if identity in visited:
                continue
            visited.add(identity)
            dependencies.append(identity)
            pending.extend(r['document_id'] for r in by_id[identity]['explicit_references'])
        document['reference_dependency_ids'] = dependencies
    return documents, by_form, by_id


def compile_instruction_definitions(index_path, source_catalog, registry, *, source_dir=None, authored_facts=None):
    """Process the Registry/manual union without changing the evidence database."""
    catalog = json.loads(Path(source_catalog).read_text(encoding='utf-8')) if not isinstance(source_catalog, dict) else source_catalog
    manuals = {m['id']: copy.deepcopy(m) for m in catalog['manuals']}
    index_path = Path(index_path)
    with sqlite3.connect(index_path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        from tools.fact_instruction_catalogue import instruction_cpu_catalogue, catalogue_availability_facts
        cpu_catalogue = instruction_cpu_catalogue(db, pdf_root=source_dir)
        chunks = [dict(row) for row in db.execute('SELECT * FROM chunks ORDER BY id') if row['manual_id'] in manuals]
        instruction_rows = [dict(row) for row in db.execute('SELECT * FROM instructions') if row['manual_id'] in manuals]
        table_map = defaultdict(list)
        signatures = []
        for row in db.execute('SELECT * FROM tables ORDER BY manual_id,pdf_page,table_index'):
            table_map[(row['manual_id'], row['pdf_page'])].append(dict(row))
        fx5_parameter_rows = {}
        for row in db.execute('SELECT * FROM page_artifacts ORDER BY manual_id,pdf_page'):
            if row['manual_id'] not in manuals:
                continue
            if row['manual_id'] == 'fx5_programming_ab':
                from tools.fact_fx5_signatures import _geometry_operands
                artifact = dict(row)
                fx5_parameter_rows[row['pdf_page']] = _geometry_operands(json.loads(artifact.get('word_geometry_json') or '[]'))
            signature = extract_native_signature(dict(row), table_map[(row['manual_id'], row['pdf_page'])])
            if signature:
                signatures.append(signature)
    documents, by_form, document_ids = _document_catalog(chunks, manuals)
    layout_receipts = []
    if source_dir:
        for manual in manuals.values():
            layout = manual.get('instruction_layout')
            local = Path(source_dir) / Path(manual['file']).name
            if not layout or not local.is_file():
                continue
            try:
                import pdfplumber
            except ImportError:
                layout_receipts.append({'manual_id': manual['id'], 'status': 'PDF_geometry_runtime_unavailable'})
                continue
            count = 0
            with pdfplumber.open(local) as pdf:
                for signature in signatures:
                    if signature['manual_id'] != manual['id']:
                        continue
                    page = pdf.pages[signature['page'] - 1]
                    marks = extract_model_badge_marks(page.rects, layout)
                    if marks:
                        signature['model_marks'] = marks
                        count += 1
                    page.close()
            layout_receipts.append({'manual_id': manual['id'], 'status': 'candidate_marks_only', 'pages': count,
                                    'support_promotion': False})
    for signature in signatures:
        manual = manuals[signature['manual_id']]
        signature.update(manual_number=manual['manual_number'], revision=manual['revision'])
        witnesses = [c for c in chunks if c['manual_id'] == signature['manual_id'] and
                     c['pdf_page'] <= signature['page'] <= c['pdf_page_end'] and
                     instruction_heading_names(c['section']) == [signature['base']]]
        if witnesses:
            signature['source_id'] = str(witnesses[0]['id'])
    # The instruction icon may name a family whose only callable form has a D
    # prefix. Literal mnemonic columns associate every actual D/P variant with
    # its chapter, including forms absent from the old Registry or chunk index.
    for signature in signatures:
        owners = [(d, rows) for d, rows in by_form.get(signature['base'], [])
                  if d['manual_id'] == signature['manual_id'] and signature['page'] in d['pdf_pages']]
        for opcode in signature['forms']:
            for pair in owners:
                if pair not in by_form[opcode]:
                    by_form[opcode].append(pair)
    # Manuals with topic headings (e.g. a positioning chapter's "Instruction
    # Format") are still reached through explicit indexed opcode ownership.
    # This is chapter membership only; it does not verify the indexed signature.
    for document in documents:
        if document['forms'] or not re.search(r'\bInstruction\s+Format\s*$', document['section'], re.I):
            continue
        rows = [c for c in chunks if c['id'] in {int(i) for i in document['source_ids']}]
        names = sorted({c['instruction_opcode'].upper() for c in rows
                        if c['chunk_type'] == 'instruction' and c['instruction_opcode']})
        if len(names) == 1:
            document.update(forms=names, section_association='explicit_indexed_instruction_identity')
            for name in names:
                by_form[name].append((document, rows))
    # Explicit variants from indexed definition rows are considered only when
    # that row's base has a literal definition heading in the same manual.
    for row in instruction_rows:
        base = row['opcode'].strip().upper()
        owners = [(d, rs) for d, rs in by_form.get(base, []) if d['manual_id'] == row['manual_id']]
        for variant in json.loads(row['variants_json'] or '[]'):
            if isinstance(variant, str) and variant.upper() != base:
                for pair in owners:
                    if pair not in by_form[variant.upper()]:
                        by_form[variant.upper()].append(pair)
    models = set(m for manual in manuals.values() for m in manual.get('plc_models', []))
    models.update(cpu for spec in registry._specs.values() for cpu in spec.cpu_support)
    models.update(key[2] for key in registry._cpu_contracts)
    known = set(registry.known_mnemonics())
    forms = sorted(known | set(by_form))
    family_owners = defaultdict(set)
    for signature in signatures:
        for name in signature['forms']:
            family_owners[name].add(signature['base'])
    form_families = {name: next(iter(owners)) for name, owners in family_owners.items() if len(owners) == 1}
    raw_by_id = {str(c['id']): c for c in chunks}
    operands_by_owner = defaultdict(list)
    for row in instruction_rows:
        operands_by_owner[(row['manual_id'], row['opcode'].upper())].extend(
            json.loads(row.get('operands_json') or '[]'))
    index_identity = build_index_identity(index_path)
    authored = defaultdict(list)
    signatures_by_form = defaultdict(list)
    for signature in signatures:
        for opcode in signature['forms']:
            signatures_by_form[opcode].append(signature)
    if authored_facts:
        from plc.instruction_definition_storage import expand_definitions
        authored_facts = expand_definitions(authored_facts)
    for entry in (authored_facts or {}).get('entries', []):
        authored[(entry['opcode'], entry['target_model'])].extend(entry.get('facts', []))
    entries, mapped_spans = [], defaultdict(list)
    for model in sorted(models):
        for opcode in forms:
            form = registry.resolve_form(opcode, cpu=model)
            ownership = by_form.get(opcode, [])
            # Registry D/P forms can use an explicitly shared base chapter;
            # evidence remains literal-form scoped, and widths do not inherit.
            if not ownership and form is not None:
                ownership = by_form.get(form.base_mnemonic, [])
            ownership = [(d, rs) for d, rs in ownership if model in d['models']]
            if form is None and not ownership:
                continue
            admitted = bool(form is not None and form.spec.supports_cpu(model))
            admission_reason, admission_details = call_admission_explanation(form, model)
            facts = _legacy_facts(form, model) if admitted else []
            authority = instruction_source_authority(opcode, model)
            preferred = authority.get('manual_id') if authority else None
            if not preferred and ownership:
                preferred = max(ownership, key=lambda pair: manuals[pair[0]['manual_id']].get('priority', 0))[0]['manual_id']
            active = [(d, rs) for d, rs in ownership if d['manual_id'] == preferred]
            records = [_record(r) for d, rs in active for r in rs]
            for record in records:
                record['manual_operand_rows'] = [operand
                    for name in {opcode, form.base_mnemonic if form else opcode}
                    for operand in operands_by_owner[(record['manual_id'], name)]]
            literal_signatures = [s for s in signatures_by_form[opcode] if s['manual_id'] == preferred]
            if not literal_signatures:
                literal_signatures = [s for s in signatures_by_form[opcode]
                                      if model in manuals[s['manual_id']].get('plc_models', [])]
            direct_signatures = [s for s in literal_signatures if s['base'] in
                                 {opcode, form.base_mnemonic if form else opcode}]
            if direct_signatures:
                literal_signatures = direct_signatures
            signature_shapes = {tuple(s['symbols']) for s in literal_signatures}
            order = list(form.spec.native_operand_order) if admitted else []
            if not order and len(signature_shapes) == 1:
                order = list(next(iter(signature_shapes)))
            if len(literal_signatures) == 1:
                facts.extend(_signature_facts(literal_signatures[0], opcode, model))
            definition_pages = {(s['manual_id'], s['page']) for s in literal_signatures
                                if s['manual_id'] == preferred} or None
            operand_facts, operand_conflicts = _operand_facts(records, order, opcode, model,
                definition_pages=definition_pages) if order else ([], [])
            facts.extend(operand_facts)
            charts = [chart for record in records for chart in extract_binary_result_charts(
                record, symbols=order, opcode=opcode, target_model=model, width=form.spec.instruction_width)] if admitted else []
            chart_facts, chart_conflicts = _chart_facts(charts, opcode, model)
            facts.extend(chart_facts)
            facts.extend(copy.deepcopy(authored[(opcode, model)]))
            facts.extend(catalogue_availability_facts(opcode, facts, model, cpu_catalogue))
            from tools.fact_fx5_signatures import enrich_operand_storage
            enrich_operand_storage(facts, opcode, fx5_parameter_rows)
            from plc.instruction_knowledge import compose_programming_facts
            facts, _superseded = compose_programming_facts(registry, opcode, cpu=model, existing=facts)
            for fact in facts:
                parsed = InstructionFactGroup.from_mapping(fact)
                if parsed.dimension.startswith('effects.') and parsed.value.get('behavior'):
                    from plc.instruction_effects import validate_behavior
                    validate_behavior(parsed.value)
                if parsed.scope.get('models') != [model] or parsed.scope.get('forms') != [opcode]:
                    raise ValueError('Authored fact exceeds exact model/form ownership')
                for source in fact.get('sources', []):
                    if source.get('id') in raw_by_id:
                        for span in source.get('value_spans', []):
                            if not 0 <= span['start'] < span['end'] <= len(raw_by_id[source['id']]['text']):
                                raise ValueError('Fact witness is outside its original source')
                            mapped_spans[source['id']].append((span['start'], span['end'], fact['id']))
                        if not source.get('value_spans') and source.get('row_span'):
                            span = source['row_span']
                            mapped_spans[source['id']].append((span['start'], span['end'], fact['id']))
            materials = [{'document_id': d['id'], 'manual_id': d['manual_id'], 'revision': d['revision'],
                          'source_ids': d['source_ids'], 'pdf_pages': d['pdf_pages'],
                          'primary_semantic_source': d['manual_id'] == preferred,
                          'explicit_reference_ids': [r['document_id'] for r in d['explicit_references']],
                          'reference_dependency_ids': d['reference_dependency_ids']} for d, _ in ownership]
            # Display order only: the adjudication below decides which material and
            # which fact actually answers, and every material stays listed.
            materials.sort(key=lambda item: (not item['primary_semantic_source'], item['manual_id']))
            unexplained = [{'document_id': d['id'], 'reason': 'remaining_source_content', 'unit_count': len(d['units'])}
                           for d, _ in ownership]
            gaps = []
            if not admitted:
                gaps.append('manual_only_not_admitted' if form is None else 'outside_declared_cpu_scope')
            if not ownership:
                gaps.append('no_literal_official_definition_section')
            if not admitted or form.spec.contract_coverage().get('operand_order') != 'source_verified':
                gaps.append('native_parameter_order_not_established')
            if not any(f['dimension'].startswith('effects.') for f in facts):
                gaps.append('effects_not_formalized')
            build_source_ids = [str(value) for d, _ in ownership
                                for value in d.get('source_ids') or ()]
            entries.append({'vendor': 'mitsubishi', 'opcode': opcode, 'target_model': model,
                            'build_source_ids': build_source_ids,
                            'build_index_id': (index_identity or {}).get('build_index_id'),
                            'source_reference_status': (NO_BUILD_REFERENCE if not build_source_ids
                                else VERIFIED_AT_BUILD if index_identity and all(
                                    identity in raw_by_id for identity in build_source_ids)
                                else LEGACY_UNVERIFIED),
                            'registry_member': opcode in known, 'call_available': admitted,
                            'call_admission_reason': admission_reason,
                            'call_admission_details': admission_details,
                            'support_status': 'existing_declared_scope' if admitted else 'definition_only',
                            'facts': facts, 'source_materials': materials, 'uninterpreted_content': unexplained,
                            'conflicts': [*operand_conflicts, *chart_conflicts], 'gaps': gaps,
                            'semantic_completeness': 'not_established'})
    for document in documents:
        for unit in document['units']:
            lo, hi = unit['source_span']['start'], unit['source_span']['end']
            matches = [(start, end, fact) for start, end, fact in mapped_spans[unit['chunk_id']] if lo <= start < end <= hi]
            residual, cursor = [], lo
            for start, end, _ in sorted(matches):
                if cursor < start:
                    residual.append({'start': cursor, 'end': start})
                cursor = max(cursor, end)
            if cursor < hi:
                residual.append({'start': cursor, 'end': hi})
            unit.update(mapped_fact_ids=sorted({fact for _, _, fact in matches}),
                        uninterpreted_spans=residual,
                        interpretation_status='partially_interpreted' if matches else 'uninterpreted')
    catalog_state = []
    for manual in manuals.values():
        local = Path(source_dir) / Path(manual['file']).name if source_dir else None
        catalog_state.append({'id': manual['id'], 'manual_number': manual['manual_number'], 'revision': manual['revision'],
                              'models': manual.get('plc_models', []), 'source_file': Path(manual['file']).name,
                              'local_file_available': bool(local and local.is_file()),
                              'indexed_sections': sum(d['manual_id'] == manual['id'] for d in documents)})
    payload = {'schema_version': 1, 'method': METHOD, 'models': sorted(models),
               'form_families': form_families,
               'registry_forms': len(known), 'manual_heading_forms': len(by_form), 'union_forms': len(forms),
               'source_catalog': catalog_state, 'source_layout_receipts': layout_receipts, 'documents': documents, 'entries': entries,
               'reference_policy': {'max_explicit_references_per_section': None, 'max_depth': None,
                                    'cycle_policy': 'each_document_once', 'adjacent_page_inference': False},
               'source_verification': 'preserve_existing_and_per_fact_only', 'database_mutated': False}
    report = {'schema_version': 1, 'method': METHOD, 'registry_forms': len(known), 'manual_heading_forms': len(by_form),
              'union_forms': len(forms), 'target_models': sorted(models), 'entries': len(entries),
              'source_layout_receipts': layout_receipts,
              'documents': len(documents), 'units': sum(len(d['units']) for d in documents),
              'partially_interpreted_units': sum(u['interpretation_status'] == 'partially_interpreted' for d in documents for u in d['units']),
              'fact_statuses': dict(sorted(Counter(f['status'] for e in entries for f in e['facts']).items())),
              'effect_forms': [{'opcode': e['opcode'], 'target_model': e['target_model']} for e in entries
                               if any(f['dimension'].startswith('effects.') for f in e['facts'])],
              'gap_counts': dict(sorted(Counter(gap for e in entries for gap in e['gaps']).items())),
              'source_unattributed_sections': sum(not d['forms'] for d in documents),
              'database_mutated': False, 'support_completion': 'per_form_not_established'}
    return payload, report
