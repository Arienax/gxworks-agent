"""Model delivery keeps executable contracts and engineering-owned data intact."""
import copy
import json

import pytest

from shared.cpu_version_projection import project_cpu_version_knowledge, project_cpu_version_text, without_cpu_versions
from shared.model_context import (
    _positive_contract, model_knowledge_value, model_review_view, technical_block,
    technical_context_text, technical_context_units,
)


@pytest.mark.parametrize('kind', ['instruction', 'device_family', 'language', 'example'])
def test_unresolved_version_does_not_remove_parameters_or_other_conditions(kind):
    packet = {'kind': kind, 'value': {'parameter_tables': [{
        'conditions': [{'context': 'cpu_version_not_resolved'}, {'context': 'input_enabled'}],
        'parameters': [{'name': 'D', 'bits': 32, 'word_count': 2}]}]}}
    before = copy.deepcopy(packet)
    direct = project_cpu_version_knowledge(packet)
    nested = project_cpu_version_knowledge({'knowledge': [packet]})['knowledge'][0]
    assert direct == nested
    table, = direct['value']['parameter_tables']
    assert table['parameters'] == packet['value']['parameter_tables'][0]['parameters']
    assert table['conditions'] == [{'context': 'input_enabled'}]
    assert project_cpu_version_knowledge(packet, requested=True) == before == packet


def test_cpu_projection_preserves_explicit_nulls():
    raw = {'kind': 'instruction', 'documented_st_call_available': None,
           'st_has_enable_parameter': None, 'values': [None, 1]}
    assert without_cpu_versions(raw) == raw


@pytest.mark.parametrize('container', [None, 'hardware_profile', 'hardware_context', 'project_context'])
@pytest.mark.parametrize('field,value,enabled', [
    ('check_cpu_version', True, True), ('check_cpu_version', False, False),
    ('check_cpu_version', 'true', False), ('cpu_version', '2.20', True),
    ('firmware', '2.20', True), ('firmware', '', False),
])
def test_explicit_version_intent_survives_both_specification_projections(container, field, value, enabled):
    from application.generation_support import public_generation_specification
    from application.confirmed_generation_context import project_confirmed_specification
    from shared.cpu_version_projection import cpu_version_requested
    fields = {field: value, 'internal_extra': 'not a generation field'}
    source = {container: fields} if container else fields
    before = copy.deepcopy(source)
    for project in (public_generation_specification, project_confirmed_specification):
        projected = project(source)
        assert cpu_version_requested('', projected) is enabled
        assert cpu_version_requested('不检查 CPU 版本', projected) is False
        assert 'internal_extra' not in json.dumps(projected)
    assert source == before


def test_selected_plan_does_not_enable_version_check_after_projection():
    from application.generation_support import public_generation_specification
    from shared.cpu_version_projection import cpu_version_requested
    source = {'selected_approach': {'check_cpu_version': True, 'cpu_version': '2.20'}}
    assert not cpu_version_requested('', public_generation_specification(source))


@pytest.mark.parametrize('version_fields,enabled', [
    ({}, False), ({'check_cpu_version': True}, True),
    ({'hardware_profile': {'check_cpu_version': True}}, True),
    ({'hardware_context': {'check_cpu_version': True}}, True),
    ({'project_context': {'firmware': '2.20'}}, True),
])
def test_version_opt_in_reaches_confirmed_and_fbd_retrieval(version_fields, enabled, monkeypatch):
    from application.confirmed_generation_context import build_confirmed_generation_context
    from application.fbd import build_fbd_generation_context, catalog_snapshot
    from gxw.project_templates import blank_fbd_baseline
    from knowledge.evidence import KnowledgeContext
    import knowledge.retriever as retriever
    version_fact = {'kind': 'device_family', 'target': 'T',
                    'conditions': [{'context': 'cpu_version', 'minimum': '2.20'}],
                    'value': {'available': True}}
    monkeypatch.setattr(retriever, 'build_knowledge_context',
                        lambda *a, **k: KnowledgeContext(technical_block(version_fact)))
    monkeypatch.setattr(retriever, 'discover_capabilities',
                        lambda *a, **k: {'functions': [], 'candidates': [], 'gaps': [], 'model_calls': 0})
    monkeypatch.setattr('knowledge.instruction_facts.retrieve_instruction_facts',
                        lambda *a, **k: ([], {}))
    source = {'summary': 'Use WSFL', 'selected_approach': {
        'generation_contract': {'required_opcodes': ['WSFL']}}, **version_fields}
    confirmed = build_confirmed_generation_context(source, 'FX3U')
    assert confirmed.knowledge_context.manifest['cpu_version_requested'] is enabled
    assert any(unit.get('conditions') == version_fact['conditions']
               for unit in technical_context_units(confirmed.knowledge_context)) is enabled
    fbd = build_fbd_generation_context('Start and stop', plc_model='FX3U',
        confirmed_spec=source, catalog=catalog_snapshot(blank_fbd_baseline('FX3U')))
    assert any(unit.get('conditions') == version_fact['conditions']
               for unit in fbd['generation_input']['knowledge']) is enabled


def test_version_only_array_does_not_leave_empty_knowledge_block():
    wire = '[PLC KNOWLEDGE]\n[{"context":"cpu_version"}]\n[/PLC KNOWLEDGE]'
    assert project_cpu_version_text(wire) == ''
    assert project_cpu_version_text(wire, requested=True) == wire
    ordinary = '[PLC KNOWLEDGE]\n[{"context":"input_enabled"}]\n[/PLC KNOWLEDGE]'
    assert json.loads(project_cpu_version_text(ordinary).splitlines()[1]) == [{'context': 'input_enabled'}]


@pytest.mark.parametrize('kind', ['positioning', 'error', 'process'])
def test_cpu_only_unit_is_removed_from_text_and_arrays(kind):
    guarded = {'kind': kind, 'conditions': [{'context': 'cpu_version', 'minimum': '2.20'}],
               'value': {'available': False}}
    keep = {'kind': 'device_family', 'target': 'T', 'value': {'unit': 'ms'}}
    wire = technical_block(guarded) + '\n\n' + technical_block(keep)
    assert technical_context_units(project_cpu_version_text(wire)) == [keep]
    assert project_cpu_version_knowledge([None, guarded, keep]) == [None, keep]
    assert technical_context_units('[PLC KNOWLEDGE]\nnull\n[/PLC KNOWLEDGE]') == []


@pytest.mark.parametrize('reason', ['Compile failed', 'source_unreadable', 'OPERAND_COUNT'])
def test_real_diagnostics_survive_review_status(reason):
    raw = {'status': 'unverified', 'valid': False, 'reason': reason, 'actual': 2, 'expected': 3}
    output = model_review_view(raw)
    assert output == {key: value for key, value in raw.items() if key != 'status'}
    assert raw['status'] == 'unverified'


def test_unrecognized_diagnostic_and_nested_engineering_fields_are_preserved():
    fields = ('user_facts', 'prior_user_input', 'behavior_cases', 'previous', 'source_context',
              'catalog', 'existing_declarations', 'call_interface', 'structures',
              'declaration_tables', 'call_bindings', 'project_options')
    item = {'kind': 'instruction', 'cpu_version': '2.20', 'severity': 'unverified',
            'status': 'unverified', 'message': 'Operator annotation', 'unverified_count': 4}
    raw = {key: [item] for key in fields}
    raw['findings'] = [{'severity': 'unverified', 'message': 'Operator annotation'}]
    assert model_review_view(raw) == raw
    assert project_cpu_version_knowledge(raw) == raw


def test_schema_and_engineering_content_survive_final_runtime_wire():
    from agent_runtime.runtime import _model_tool_value
    schema = {'properties': {'unverified_count': {'type': 'integer'},
                            'bbox': {'type': 'string'}, 'source_notes': {'type': 'string'},
                            'raw_text': {'type': 'string'}}}
    cases = [{'status': 'unverified', 'valid': False, 'reason': 'user-owned',
              'values': {'unverified': 1}}]
    program = {'source_notes': 'user comment', 'r': [['LD', 'M0']]}
    raw = {'ok': True, 'tool': 'get_generation_context', 'data': {'generation_input': {
        'output_schema': schema, 'behavior_cases': cases, 'current_program': program}}}
    before = copy.deepcopy(raw)
    assert _model_tool_value('get_generation_context', raw) == raw == before
    spec = {'parameters': [{'value': {'unverified_count': 4, 'status': 'unverified'}}]}
    assert _model_tool_value('get_project_spec', spec) == spec


def test_programming_conditions_and_list_notes_are_not_review_states():
    raw = {'conditions': [{'context': 'input_enabled'}],
           'notes': ['Reset explicitly.', 'Unverified by the local executor.']}
    assert model_knowledge_value(raw) == {
        'conditions': raw['conditions'], 'notes': ['Reset explicitly.']}
    conflicted = {'knowledge_status': 'conflict', 'value': {'bits': 16}}
    assert _positive_contract(conflicted)['knowledge_status'] == 'conflict'
    assert model_knowledge_value(_positive_contract(conflicted)) is None


def test_instruction_view_keeps_conditional_claim_instead_of_inventing_mismatch():
    raw = {'kind': 'instruction', 'target': 'TEST', 'value': {
        'family': 'TEST', 'forms': [{'opcode': 'TEST'}], 'additional_knowledge': [{
            'conditions': [{'context': 'project_mode_not_resolved'}],
            'semantics': {'effects.result': {'operation': 'copy'}}}]}}
    result = model_knowledge_value(raw)
    assert result['value']['additional_knowledge'] == raw['value']['additional_knowledge']


def test_instruction_card_accepts_singular_model_scope():
    from knowledge.instruction_presentation import instruction_card
    from shared.model_context import technical_value
    raw = {'kind': 'instruction', 'target': 'TEST', 'scope': {'model': 'FX3U'}, 'value': {
        'opcode': 'TEST', 'facts': [{'id': 'params', 'dimension': 'parameters.typed_operands',
            'status': 'source_verified', 'value': {'operands': [{'name': 'S', 'type': 'int'}]}}]}}
    card = instruction_card(raw, technical_value)
    assert card['value']['parameter_tables'][0]['parameters'][0]['name'] == 'S'


def test_mixed_context_keeps_authored_text_and_legacy_blocks_once():
    prose = '资料缺失要指出，不能把候选名称当作支持证据。'
    legacy = '[KNOWLEDGE {"id":"old"}]\n{"kind":"device_family","target":"T"}\n[/KNOWLEDGE]'
    raw = prose + '\n' + legacy + '\n' + technical_block({'kind': 'example', 'value': {'n': 2}}) + '\nEnd rule.'
    units = technical_context_units(raw)
    assert any(unit.get('target') == 'T' for unit in units)
    wire = technical_context_text(raw)
    assert wire.count(prose) == wire.count('End rule.') == 1
    assert technical_context_units(wire) == units
    assert technical_context_text(wire) == wire


def test_literal_knowledge_delimiters_in_json_and_prose_are_not_block_boundaries():
    raw = {'kind': 'example', 'value': {'comment': 'Use [PLC KNOWLEDGE] text [/PLC KNOWLEDGE] here.'}}
    prose = 'Mention [PLC KNOWLEDGE] inline, followed by [/PLC KNOWLEDGE].'
    wire = prose + '\r\n' + technical_block(raw).replace('\n', '\r\n')
    assert technical_context_units(wire) == [{'kind': 'context_text', 'text': prose}, raw]
    assert technical_context_units(project_cpu_version_text(wire)) == technical_context_units(wire)


def test_delivery_receipt_compares_projected_legacy_body_and_rejects_missing_fact():
    from knowledge.fact_coverage import included_evidence_ids
    packet = {'kind': 'instruction', 'target': 'TEST', 'scope': {'models': ['FX3U']},
        'value': {'family': 'TEST', 'forms': [{'opcode': 'TEST'}],
                  'parameter_tables': [{'parameters': [{'name': 'S', 'purpose': {'value': 'read data'},
                                                       'type': {'kind': 'int', 'bits': 16}}]}]}}
    raw = '[KNOWLEDGE {"id":"test"}]\n' + json.dumps(packet) + '\n[/KNOWLEDGE]'
    record = {'id': 'test', 'model_text': raw}
    wire = technical_context_text(raw)
    assert included_evidence_ids(wire, [record]) == ['test']
    assert included_evidence_ids(wire.replace('read data', 'different role'), [record]) == []
    assert included_evidence_ids('', [record]) == []


@pytest.mark.parametrize('hashed', [False, True])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_legacy_receipt_requires_source_content_and_returns_unique_ids(hashed, newline):
    from knowledge.evidence import text_sha256
    from knowledge.fact_coverage import included_evidence_ids
    body = 'Actual instruction operands'
    raw = newline.join(('[KNOWLEDGE {"id":"r1"}]', body, '[/KNOWLEDGE]'))
    record = {'id': 'r1', 'model_text': raw}
    if hashed:
        record['content_sha256'] = text_sha256(body)
    assert included_evidence_ids(raw + newline + raw, [record, record]) == ['r1']
    assert included_evidence_ids(raw.replace(body, 'FORGED'), [record]) == []
    assert included_evidence_ids(raw.replace('r1', 'unknown'), [record]) == []
    assert included_evidence_ids(raw, []) == []
    assert included_evidence_ids(raw.replace('[/KNOWLEDGE]', ''), [record]) == []
    # Old receipts may retain only the source digest, without model_text.
    digest_only = {'id': 'r1', 'content_sha256': text_sha256(body)}
    assert included_evidence_ids(raw, [digest_only, digest_only]) == ['r1']


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
@pytest.mark.parametrize('opening,closing', [
    ('PLC KNOWLEDGE', 'PLC KNOWLEDGE'), ('KNOWLEDGE {"id":"r1"}', 'KNOWLEDGE'),
])
def test_compiler_deduplicates_only_matching_complete_blocks(opening, closing, newline):
    from application.context_compiler import deduplicate_evidence_text
    valid = newline.join(('Reference role: source', '[' + opening + ']', 'Original body', '[/' + closing + ']'))
    result, dropped = deduplicate_evidence_text(valid + newline + valid)
    assert dropped == 1 and result.count('Original body') == 1
    wrong = 'KNOWLEDGE' if closing == 'PLC KNOWLEDGE' else 'PLC KNOWLEDGE'
    malformed = valid.replace('[/' + closing + ']', '[/' + wrong + ']')
    mixed = newline.join((malformed, valid, malformed, valid))
    result, dropped = deduplicate_evidence_text(mixed)
    assert dropped == 1
    assert result.count(malformed) == 2 and result.count(valid) == 1


def test_final_receipt_matches_current_library_after_cpu_opt_out():
    from application.confirmed_generation_context import build_confirmed_generation_context
    from knowledge.fact_coverage import included_evidence_ids
    context = build_confirmed_generation_context({'summary': 'Use WSFL', 'selected_approach': {
        'generation_contract': {'required_opcodes': ['WSFL']}}}, 'FX3U')
    records = context.handoff['instruction_facts']['records']
    assert records and all(row['included'] for row in records)
    assert context.knowledge_context.manifest['cpu_version_requested'] is False
    assert included_evidence_ids(str(context.knowledge_context), records) == [row['id'] for row in records]
    assert not included_evidence_ids('', records)


def test_augmentation_filters_new_instruction_and_dependency_blocks(monkeypatch):
    from application.capability_context import augment_generation_knowledge
    from knowledge.evidence import KnowledgeContext

    def reference(target):
        return technical_block({'kind': 'device_family', 'target': target, 'value': {
            'behavior': 'retain', 'branches': [{'conditions': [{'context': 'cpu_version',
                                                                'minimum': '2.20'}],
                                                'available': False}]}})

    monkeypatch.setattr('knowledge.retriever.discover_capabilities',
                        lambda *args, **kwargs: {'functions': [], 'candidates': [], 'gaps': []})
    monkeypatch.setattr('knowledge.instruction_facts.retrieve_instruction_facts',
                        lambda *args, **kwargs: ([{'id': 'extra', 'text': reference('T')}], {}))
    monkeypatch.setattr('knowledge.fact_dependencies.related_device_facts',
                        lambda *args, **kwargs: [{'id': 'related', 'text': reference('C')}])
    monkeypatch.setattr('knowledge.retriever.format_knowledge_reference', lambda row: row['text'])
    base = KnowledgeContext(reference('M'), {'structured_facts': {'targets': {'instructions': [{'opcode': 'TEST'}]}}})
    for enabled in (False, True):
        result = augment_generation_knowledge('', base, target_mode='fbd', check_cpu_version=enabled)
        assert result.manifest['cpu_version_requested'] is enabled
        assert ('cpu_version' in result) is enabled
        assert {unit['target'] for unit in technical_context_units(result) if unit.get('kind') == 'device_family'} == {
            'M', 'T', 'C'}
        discovery = result.manifest['capability_discovery']
        assert all(discovery['model_units'][key] in result for key in ('extra', 'related'))


def test_dsh_http_does_not_reproject_prepared_document(tmp_path, monkeypatch):
    from urllib.request import Request, build_opener, ProxyHandler
    from application.dsh_jobs import ToolBridge
    from application.read_delivery import PreparedReadEnvelope
    envelope = PreparedReadEnvelope({'ok': True, 'tool': 'get_generation_context', 'data': {
        'document': {'generation_input': {'output_schema': {
            'properties': {'unverified_count': {'type': 'integer'}}}},
            'user_facts': {'status': 'unverified', 'reason': 'user data'}},
        'delivery': {'complete': True, 'next': None}}})
    bridge = ToolBridge(None, 'offline', tmp_path)
    monkeypatch.setattr(bridge, 'invoke', lambda payload: envelope)
    try:
        request = Request(bridge.url + '/invoke', data=json.dumps({'session_id': 'offline'}).encode(),
                          headers={'Authorization': 'Bearer ' + bridge.token, 'Content-Type': 'application/json'})
        with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
            assert json.load(response) == envelope
    finally:
        bridge.server.shutdown()
        bridge.server.server_close()
        bridge.thread.join(timeout=5)
