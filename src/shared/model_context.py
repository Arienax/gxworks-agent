"""Technical context presented to models; audit/debug records stay elsewhere.

This is a data projection only. It never admits an instruction, changes a PLC
rule, upgrades a fact, or modifies the input used by deterministic validation.
Program, requirement, port and generation-context identities remain meaningful
engineering references; document/source identities are removed.
"""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping


_DEBUG_FIELDS = frozenset({
    'sources', 'source_id', 'source_ids', 'source_evidence', 'source_references',
    'source_chunk_id', 'source_section_id', 'source_address_locations',
    'document_id', 'manual_id', 'manual_number', 'manual_title', 'pdf_page', 'pdf_pages', 'pdf_page_end',
    'content_sha256', 'text_sha256', 'proof_sha256', 'source_sha256',
    'source_spans', 'value_spans', 'context_span', 'row_span', 'bbox',
    'source_materials', 'source_parameter_id', 'source_record_ids',
    'verification', 'verification_status', 'technical_status', 'distribution_status',
    'content_origin', 'rights_basis', 'source_contract_clearance',
    'source_verification', 'source_checked_fact_ids', 'candidate_fact_ids',
    'contract_sources', 'contract_coverage', 'contract_level',
    'source_applicability', 'table_binding_status', 'evidence_scope',
    'generation_handoff', 'baseline_handoff', 'provenance', 'provenance_receipt',
    'decision_receipt_id', 'audit', 'audit_trail', 'context_audit', 'budget_report',
    'validation', 'device_validation', 'validation_results', 'validation_messages',
    'semantic_validation', 'static_analysis', 'timing_analysis', 'behavior_check',
    'maintainability_review', 'diagnostics', 'local_findings', 'local_check_coverage',
    'test_evidence', 'test_results', 'test_report', 'test_reports', 'test_observations',
    'native_compilation', 'native_compile', 'hardware_effect', 'hardware_test',
    'diagnostic_projection', 'backend_recheck', 'public_check', 'compilation',
    'dimension_status', 'field_provenance', 'local_report', 'generation_evidence',
    'capability_discovery', 'fact_coverage',
    'uninterpreted_content', 'inventory_gaps', 'model_units', 'model_text',
    'source_notes', 'source_status', 'source_revision', 'source_records',
    'record_id', 'record_ids', 'evidence_ids', 'candidate_source_ids',
    'fact_id', 'fact_ids', 'definition_ids', 'verified_fields', 'verification_level', 'profile_status',
    'deterministic_analysis',
    'manual_text', 'layout_text', 'raw_text',
    'source_page', 'source_line', 'clause_index', 'complete_clause_review',
    'full_clause_review', 'source_block', 'source_kind',
    'superseded_facts', 'type_basis', 'direction_basis', 'condition_extraction_basis',
    'binding_basis', 'relation_coverage', 'covered_aspects',
})

_REVIEW_STATES = frozenset({
    'source_verified', 'source_checked', 'candidate_evidence',
    'declared_unverified', 'partially_source_verified', 'not_performed',
})


def _technical_facts(groups):
    """Deliver each full fact once and preserve prerequisite references."""
    by_id = {fact['id']: fact for fact in groups if fact.get('id')}
    local_ids = {identity: 'fact-' + str(index + 1) for index, identity in enumerate(by_id)}
    referenced = {key for fact in groups for key in fact.get('depends_on', ())}
    for key in sorted(referenced - set(local_ids)):
        local_ids[key] = 'fact-' + str(len(local_ids) + 1)
    facts, unresolved, seen = [], [], set()
    for fact in groups:
        dimension = str(fact.get('dimension') or '')
        if dimension == 'identity.source_model_mark' and fact.get('id') not in referenced:
            continue
        item = {key: technical_value(fact[key]) for key in
                ('dimension', 'value', 'scope', 'conditions', 'requires', 'note', 'selection_applicability',
                 'generation_requirements', 'avoid_relying_on', 'required_prerequisites') if key in fact}
        if fact.get('id'):
            item['id'] = local_ids[fact['id']]
        if fact.get('status'):
            item['knowledge_status'] = fact['status']
        elif fact.get('knowledge_status'):
            item['knowledge_status'] = fact['knowledge_status']
        if fact.get('status') in {'unknown', 'conflict'}:
            unresolved.append(dimension)
        dependencies = []
        for identity in fact.get('depends_on', ()):
            dependency = by_id.get(identity)
            if dependency is None:
                unresolved.append('missing_prerequisite')
                item.setdefault('missing_prerequisites', []).append(local_ids[identity])
            dependencies.append(identity)
        if dependencies:
            item['depends_on'] = [local_ids[key] for key in dependencies]
        identity = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if identity not in seen:
            seen.add(identity)
            facts.append(item)
    return facts, list(dict.fromkeys(unresolved))


def technical_value(value):
    """Detach structured technical values from automatically attached audits."""
    if isinstance(value, Mapping):
        result = {}
        fact_groups = value.get('facts')
        fact_container = isinstance(fact_groups, list) and all(isinstance(f, Mapping) and 'dimension' in f for f in fact_groups)
        unresolved = []
        for key, item in value.items():
            if key == 'facts' and fact_container:
                result['facts'], unresolved = _technical_facts(item)
                continue
            if key in {'verification', 'validation'} and isinstance(item, Mapping):
                # Required native-save routing is an operational constraint,
                # separate from the attached checks and their outcomes.
                if 'native_source_save_required' in item:
                    result['native_source_save_required'] = copy.deepcopy(item['native_source_save_required'])
                continue
            if key == 'source_notes':
                result['notes'] = technical_value(item)
                continue
            if key in _DEBUG_FIELDS or str(key).startswith('_'):
                continue
            if key != 'knowledge_status' and (key == 'status' or str(key).endswith('_status')) and isinstance(item, str) and item in _REVIEW_STATES:
                continue
            if key == 'source_qualifications':
                if item:
                    result['additional_conditions'] = 'not_established'
                continue
            result[key] = technical_value(item)
        if unresolved:
            result['unresolved_aspects'] = list(dict.fromkeys([*result.get('unresolved_aspects', []), *unresolved]))
        return result
    if isinstance(value, (list, tuple)):
        return [technical_value(item) for item in value]
    return copy.deepcopy(value)


def technical_fact_packet(packet):
    """Render an authored record without its proof graph or source envelope."""
    raw = packet.get('value')
    if packet.get('kind') == 'instruction' and isinstance(raw, Mapping):
        from knowledge.instruction_presentation import instruction_card
        result = instruction_card(packet, technical_value)
    else:
        result = {key: technical_value(packet[key]) for key in ('kind', 'target', 'scope', 'conditions', 'requires') if key in packet}
        result['value'] = technical_value(raw)
    if packet.get('status'):
        result['knowledge_status'] = packet['status']
    if packet.get('gaps'):
        result['unresolved_aspects'] = technical_value(packet['gaps'])
    selection = packet.get('fact_selection')
    if isinstance(selection, Mapping):
        result['selection'] = {key: technical_value(selection[key]) for key in
            ('language', 'project_mode', 'version', 'aspects', 'partial_projection', 'whole_instruction_complete') if key in selection}
        if selection.get('gaps'):
            result['selection']['unresolved_reasons'] = list(dict.fromkeys(g.get('reason', 'unknown') for g in selection['gaps']))
    return result


def technical_block(value):
    return '[PLC KNOWLEDGE]\n' + json.dumps(technical_value(value), ensure_ascii=False,
        separators=(',', ':'), sort_keys=True) + '\n[/PLC KNOWLEDGE]'


def technical_context_units(context):
    """Structured complete knowledge units, without repeating instruction prose."""
    units, seen = [], set()
    for body in re.findall(r'(?s)\[PLC KNOWLEDGE\]\s*(.*?)\s*\[/PLC KNOWLEDGE\]', str(context or '')):
        value = json.loads(body)
        marker = json.dumps(value, sort_keys=True, ensure_ascii=False)
        if marker not in seen:
            seen.add(marker)
            units.append(value)
    if not units and str(context or '').strip():
        units.append({'text': str(context).strip()})
    from knowledge.instruction_presentation import merge_instruction_units, SHARED_INSTRUCTION_KINDS
    projected = [projected_unit for unit in merge_instruction_units(units)
                 if unit.get('kind') not in SHARED_INSTRUCTION_KINDS
                 and (projected_unit := model_knowledge_value(unit)) not in (None, {}, [])]
    described = {form['opcode'] for unit in projected if unit.get('kind') == 'instruction'
                 for form in unit.get('value', {}).get('forms', [])}
    families = {(unit['value']['family'], model) for unit in projected if unit.get('kind') == 'instruction'
                for model in unit.get('scope', {}).get('models', [])}
    def redundant_capability(unit):
        if unit.get('kind') != 'instruction_capability':
            return False
        if unit.get('name') in described:
            return True
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        return any((resolution := DEFAULT_INSTRUCTION_REGISTRY.resolve_catalog_form(unit.get('name', ''), cpu=model))
                   and resolution.base_mnemonic == family for family, model in families)
    return [unit for unit in projected if not redundant_capability(unit)]


_DELIVERY_AUDIT_FIELDS = frozenset({
    'knowledge_status', 'knowledge_status_by_field', 'selection',
    'selection_applicability', 'unresolved_aspects', 'unresolved_reasons',
    'core_unresolved_aspects', 'core_call_available', 'core_execution_capability',
    'call_available', 'call_admission_reason', 'call_admission_details',
    'support_status', 'binding_status', 'identity_knowledge', 'gap_kind',
    'unknown', 'core_unknown', 'gap_reasons', 'runtime_execution',
    'conflicts', 'depends_on', 'missing_prerequisites', 'required_prerequisites',
    'generation_requirements', 'avoid_relying_on', 'gaps',
    'reviewed_behavior_does_not_establish_Core_execution',
})
_ABSENT_MODEL_TEXT = frozenset({'not_stated_in_reviewed_source', 'not_stated'})
_ABSENT_KNOWLEDGE = frozenset({'unknown', 'unresolved', 'unverified', 'not_established'})
_UNPROVEN_STATES = _ABSENT_KNOWLEDGE | {
    'declared_unverified', 'candidate_evidence', 'source_review_pending',
    'source_present_not_extracted', 'partially_source_verified',
}


def _absent_knowledge(value):
    return isinstance(value, str) and value in _ABSENT_KNOWLEDGE


def model_knowledge_value(value):
    """Deliver only applicable programming semantics.

    Review states, coverage, conflicts and unresolved placeholders stay in the
    Core/debug record and private ledger. The model receives concrete
    applicability, parameters, operations, outputs, timing and state rules.
    """
    if isinstance(value, (list, tuple)):
        return [item for item in (model_knowledge_value(entry) for entry in value)
                if item not in (None, {}, [])]
    if not isinstance(value, Mapping):
        return None if _absent_knowledge(value) else copy.deepcopy(value)
    if value.get('kind') in {'instruction_modifiers', 'instruction_execution'}:
        return None
    if value.get('status') == 'conflict' or value.get('knowledge_status') == 'conflict':
        return None
    result = {}
    state = value.get('knowledge_status', value.get('status'))
    unproven_availability = (isinstance(state, str)
                            and state in _UNPROVEN_STATES
                            and isinstance(value.get('value'), Mapping)
                            and value['value'].get('available') is True)
    field_states = value.get('knowledge_status_by_field') or {}
    operator_source_details = (isinstance(value.get('operators'), Mapping)
        and (isinstance(value.get('source_resolution'), Mapping)
             or isinstance(value.get('source_conflicts'), Mapping)))
    conflicted_operators = ({symbol for symbol, conflict in value.get('source_conflicts', {}).items()
                            if conflict} if operator_source_details
                            and isinstance(value.get('source_conflicts'), Mapping) else set())
    for key, item in value.items():
        if operator_source_details:
            if key == 'source_conflicts':
                continue
            if key == 'operators' and isinstance(item, Mapping):
                item = {symbol: rule for symbol, rule in item.items() if symbol not in conflicted_operators}
            elif key == 'standard_function_aliases' and isinstance(item, Mapping):
                item = {symbol: target for symbol, target in item.items()
                        if symbol not in conflicted_operators and target not in conflicted_operators}
            elif key == 'source_resolution' and isinstance(item, Mapping):
                # This mixed source-review record also owns concrete pin/domain
                # rules. Keep those fields without publishing review attribution.
                item = {symbol: {field: detail for field, detail in resolution.items() if field in {
                    'formal_pin_domains', 'pin_count', 'QL_NE_and_not_equal_input_count',
                    'extend_ANY_BIT_to_signed_or_real_from_example'}}
                    for symbol, resolution in item.items()
                    if symbol not in conflicted_operators and isinstance(resolution, Mapping)}
        if key == 'unknown_fb_options':
            result['missing_fb_options'] = model_knowledge_value(item)
            continue
        if (key == 'other_type_branches' and item == 'unresolved'
                and value.get('source_verified_type_branches')):
            # This limits callable input branches; it is not a research note.
            result[key] = item
            continue
        if key == 'literal_or_expression_support' and item == 'not_stated':
            # A source-backed reference input does not establish value inputs.
            result[key] = item
            continue
        if (value.get('kind') == 'start_data_reference'
                and key in {'scalar_types', 'array_element_types'} and isinstance(item, (list, tuple))):
            # Empty is a binding limit, not optional presentation metadata.
            result[key] = list(item)
            continue
        if key == 'signed' and value.get('kind') == 'real':
            continue  # REAL's sign is part of its representation, not an integer signedness selector.
        if key == 'id' and (value.get('dimension') or 'value' in value):
            continue  # Fact identities stay in the dependency graph, not in model semantics.
        if key in _DELIVERY_AUDIT_FIELDS or key == 'unresolved_items':
            continue
        if key == 'status' and isinstance(item, str) and item in _REVIEW_STATES | _ABSENT_KNOWLEDGE:
            continue
        if key in field_states and _absent_knowledge(field_states[key]):
            continue
        if key == 'value' and unproven_availability:
            continue
        if key == 'conditions' and isinstance(item, (list, tuple)):
            conditions = []
            for condition in item:
                projected = model_knowledge_value(condition)
                if projected in (None, {}, []):
                    continue
                if isinstance(projected, Mapping) and set(projected) <= {'kind', 'context'}:
                    continue
                conditions.append(projected)
            if conditions:
                result[key] = conditions
            continue
        if key.endswith('.unresolved_source'):
            continue
        if key == 'dimension' and isinstance(item, str) and item.endswith('.unresolved_source'):
            continue
        if _absent_knowledge(item):
            continue
        if isinstance(item, str) and item in _ABSENT_MODEL_TEXT:
            continue
        projected = model_knowledge_value(item)
        if projected is None:
            continue
        if projected in ({}, []) and not (key == 'value' and 'forms' in value) and not (
                key == 'parameter_bindings' and 'semantic_ref' in value):
            continue
        result[key] = projected
    if not result:
        return {}
    if set(result) <= {'conditions', 'scope', 'forms'}:
        return None
    if result.get('kind') == 'instruction' and 'family' in (result.get('value') or {}):
        from knowledge.instruction_presentation import model_instruction_view
        return model_instruction_view(result)
    return result


def technical_knowledge_results(records):
    """Use the same batch composition for a manual-tool response as a prompt."""
    records = list(records)
    units = technical_context_units('\n'.join(technical_knowledge_text(row) for row in records))
    results = []
    for unit in units:
        original = next((row for row in records if row.get('fact_target') == unit.get('target')
                         and row.get('fact_kind') == unit.get('kind')), {})
        item = {key: copy.deepcopy(original[key]) for key in
                ('fact_kind', 'fact_target', 'plc_models', 'instruction_opcode') if original.get(key)}
        if unit.get('kind') != 'instruction':
            # Equal display identities can belong to different register/model
            # scopes. The delivered unit owns its scope, not the first hit.
            item.update(fact_kind=unit.get('kind'), fact_target=unit.get('target'))
            models = unit.get('scope', {}).get('models')
            if models:
                item['plc_models'] = '|'.join(models)
        if unit.get('targets'):
            item['instruction_opcodes'] = copy.deepcopy(unit['targets'])
        item['knowledge'] = [unit]
        results.append(item)
    return results


def technical_knowledge_coverage(context):
    """Keep a retrieval failure visible without exposing its local diagnostics."""
    manifest = getattr(context, 'manifest', {})
    if isinstance(manifest.get('retrieval_coverage'), Mapping):
        return model_knowledge_coverage(manifest['retrieval_coverage'])
    status = manifest.get('status')
    if status in {'unavailable', 'failed', 'error'}:
        return {'status': 'unavailable', 'reason': 'retrieval_failed', 'targets': [],
                'delivery': 'not_available'}
    if status == 'excluded':
        return {'status': 'not_requested', 'reason': manifest.get('reason'), 'targets': []}
    return None


def model_knowledge_coverage(coverage):
    """Keep transport delivery; semantic review states stay in Core/debug."""
    result = {key: copy.deepcopy(coverage[key]) for key in ('delivery',) if key in coverage}
    result['targets'] = []
    for target in coverage.get('targets', []):
        item = {key: copy.deepcopy(target[key]) for key in
                ('kind', 'target', 'identity_found', 'delivery') if key in target}
        result['targets'].append(item)
    return model_knowledge_value(result)


def technical_knowledge_text(record):
    """Use the existing record's technical payload, without looking up sources."""
    def render(value):
        context = record.get('positioning_context')
        if record.get('fact_kind') == 'positioning' and isinstance(context, Mapping):
            value = dict(value) if isinstance(value, Mapping) else {'value': value}
            value['applicability'] = {
                'plc_model': technical_value(context.get('plc_model')),
                'hardware_context': technical_value(context.get('hardware_context')),
                'products': technical_value(context.get('products') or []),
                'hardware_status': ('source_scoped' if record.get('positioning_hardware_status') == 'source_scoped'
                                    else 'unverified'),
            }
        return technical_block(value)

    text = str(record.get('text') or '')
    for line in text.splitlines():
        if line.startswith('{'):
            try:
                packet = json.loads(line)
            except ValueError:
                continue
            if isinstance(packet, dict) and packet.get('content_origin') in {'authored_fact', 'original_case'}:
                packet['_knowledge_context'] = record.get('_knowledge_context', {})
                value = technical_fact_packet(packet)
                if isinstance(record.get('device_definition'), Mapping):
                    value['device_definition'] = _device_definition(record['device_definition'])
                return render(value)
            if text.strip() == line.strip():
                return render(packet)
    if isinstance(record.get('device_definition'), Mapping):
        return render({'kind': 'device', 'value': _device_definition(record['device_definition'])})
    match = re.search(r'(?ms)^\[INSTRUCTION FACTS ([^\n]*)\]\n(.*?)\n\[/INSTRUCTION FACTS\]', text)
    if match:
        marker, body = json.loads(match[1]), json.loads(match[2])
        packet = technical_fact_packet({'kind': 'instruction', 'target': marker['opcode'],
            'scope': {'model': marker.get('target_model')}, 'value': {'facts': body.get('groups', [])}})
        if record.get('operand_candidate_conflicts'):
            packet['value']['unresolved_operands'] = [{key: conflict[key] for key in
                ('position', 'facet', 'reason') if key in conflict}
                for conflict in record['operand_candidate_conflicts']]
        return render(packet)
    # Historical parsers/debug fixtures may provide technical prose. Remove
    # their known citation wrappers without interpreting or truncating prose.
    text = re.sub(r'(?m)^\[/?(?:KNOWLEDGE|INSTRUCTION FACTS|UNINTERPRETED SOURCE|SHARED SOURCE REFERENCES)[^\n]*\]\s*$', '', text)
    text = re.sub(r'(?m)^(?:Reference role:|SOURCE:|SECTION:|MANUAL:|PDF PAGE:|CPU SOURCE SCOPE:)[^\n]*\n?', '', text)
    return render({'text': text.strip()}) if text.strip() else ''


def _device_definition(definition):
    value = technical_value(definition)
    for item in value.get('definitions', []):
        item.pop('id', None)
    return value


def technical_context_text(text):
    """Project complete injected JSON blocks, including custom knowledge builders."""
    value = str(text or '')
    pattern = re.compile(r'(?ms)^\[(?:KNOWLEDGE [^\n]*|PLC KNOWLEDGE)\]\n(.*?)\n\[/(?:KNOWLEDGE|PLC KNOWLEDGE)\]')
    def project(match):
        body = match[1]
        rendered = technical_knowledge_text({'text': body})
        if rendered.startswith('[PLC KNOWLEDGE]'):
            return rendered
        try:
            data = json.loads(body)
        except ValueError:
            return rendered
        return technical_block(data)
    value = pattern.sub(project, value)
    if '[PLC KNOWLEDGE]' in value:
        units = technical_context_units(value)
        rendered = '\n\n'.join(technical_block(unit) for unit in units)
        blocks = re.compile(r'(?ms)^\[PLC KNOWLEDGE\]\n.*?\n\[/PLC KNOWLEDGE\]')
        first = True
        def replace_blocks(match):
            nonlocal first
            if not first:
                return ''
            first = False
            return rendered
        return blocks.sub(replace_blocks, value)
    # Custom builders may return one JSON payload without a knowledge wrapper.
    try:
        data = json.loads(value)
    except ValueError:
        return value
    return json.dumps(model_knowledge_value(technical_value(data)), ensure_ascii=False, separators=(',', ':'))
