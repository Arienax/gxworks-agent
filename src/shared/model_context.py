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
})

_REVIEW_STATES = frozenset({
    'source_verified', 'source_checked', 'candidate_evidence',
    'declared_unverified', 'partially_source_verified', 'not_performed',
})


def _technical_facts(groups):
    """Keep prerequisite meaning while removing backend fact identities."""
    by_id = {fact['id']: fact for fact in groups if fact.get('id')}
    facts, unresolved, seen = [], [], set()
    for fact in groups:
        dimension = str(fact.get('dimension') or '')
        if dimension == 'identity.source_model_mark':
            continue
        if fact.get('status') in {'unknown', 'conflict', 'not_applicable'}:
            unresolved.append(dimension)
            continue
        item = {key: technical_value(fact[key]) for key in
                ('dimension', 'status', 'value', 'scope', 'conditions', 'requires') if key in fact}
        dependencies = []
        for identity in fact.get('depends_on', ()):
            dependency = by_id.get(identity)
            if dependency is None:
                unresolved.append('missing_prerequisite')
                continue
            value = dependency.get('value', {})
            dependencies.append({'dimension': dependency.get('dimension'),
                **{key: technical_value(value[key]) for key in ('phase', 'rule') if key in value}})
        if dependencies:
            item['requires'] = dependencies
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
            if (key == 'status' or str(key).endswith('_status')) and isinstance(item, str) and item in _REVIEW_STATES:
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
    result = {key: technical_value(packet[key]) for key in ('kind', 'target', 'scope') if key in packet}
    raw = packet.get('value')
    value = technical_value(raw)
    if packet.get('content_origin') == 'original_case' and isinstance(value, Mapping):
        value = {key: value[key] for key in ('requirement', 'models', 'family', 'program') if key in value}
    result['value'] = value
    if packet.get('gaps'):
        result['unresolved_aspects'] = technical_value(packet['gaps'])
    return result


def technical_block(value):
    return '[PLC KNOWLEDGE]\n' + json.dumps(technical_value(value), ensure_ascii=False,
        separators=(',', ':'), sort_keys=True) + '\n[/PLC KNOWLEDGE]'


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
    # Custom builders may return one JSON payload without a knowledge wrapper.
    try:
        data = json.loads(value)
    except ValueError:
        return value
    return json.dumps(technical_value(data), ensure_ascii=False, separators=(',', ':'))
