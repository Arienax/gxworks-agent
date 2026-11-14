"""Compatibility between the public runtime and compiled RAG owner facts."""
import copy
import json
from dataclasses import replace
from types import SimpleNamespace

from plc.instruction_constraints import check_instruction_constraints
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, InstructionRegistry


def test_owner_sections_from_one_manual_revision_are_one_authority(monkeypatch):
    from knowledge.source_authority import instruction_source_authority
    from plc import instructions
    owner = {'source_materials': [
        {'manual_id': 'synthetic', 'revision': 'K', 'pdf_pages': [12], 'primary_semantic_source': True},
        {'manual_id': 'synthetic', 'revision': 'K', 'pdf_pages': [18, 19], 'primary_semantic_source': True},
    ]}
    monkeypatch.setattr(instructions, 'DEFAULT_INSTRUCTION_REGISTRY', SimpleNamespace(
        _definition_owner_store=object(), definition_entry=lambda *a, **kw: copy.deepcopy(owner)))
    rule = instruction_source_authority('NOT', 'FX3U')
    assert rule['manual_id'] == 'synthetic' and rule['revision'] == 'K'


def test_multiple_primary_manuals_do_not_imply_whole_instruction_conflict(monkeypatch):
    from knowledge.source_authority import instruction_source_authority, adjudicate_fact_sources
    from plc import instructions
    materials = [{'manual_id': name, 'revision': 'A', 'primary_semantic_source': True}
                 for name in ('instruction_manual', 'language_manual')]
    monkeypatch.setattr(instructions, 'DEFAULT_INSTRUCTION_REGISTRY', SimpleNamespace(
        _definition_owner_store=object(), definition_entry=lambda *a, **kw: {'source_materials': materials}))
    assert instruction_source_authority('TEST', 'FX3U') is None
    candidates = [{'manual_id': row['manual_id'], 'revision': 'A', 'value': 1} for row in materials]
    assert adjudicate_fact_sources(opcode='TEST', plc_model='FX3U', fact_type='effect',
                                   candidates=candidates)['status'] == 'equivalent'
    candidates[1]['value'] = 2
    assert adjudicate_fact_sources(opcode='TEST', plc_model='FX3U', fact_type='effect',
                                   candidates=candidates)['status'] == 'conflict'


def test_constraint_reuse_keeps_inputs_cpu_scope_and_reloaded_owners_current():
    fact = {
        'id': 'synthetic-input', 'dimension': 'constraints.input',
        'value': {'predicate': {'op': 'parameter', 'name': 'READY', 'type': {'kind': 'bool'}}},
        'status': 'source_verified', 'verification': 'source_checked',
        'sources': [{'source_id': 'synthetic'}],
        'scope': {'forms': ['TEST'], 'models': ['FX3U'], 'languages': ['ladder']},
    }
    reads = []
    registry = SimpleNamespace(_definition_owner_store=object(), _family_knowledge=(),
                               _knowledge_profiles=(), fact_bundle_current=lambda: True)
    def load(opcode, *, cpu):
        reads.append((opcode, cpu))
        return {'facts': [copy.deepcopy(fact)]}
    registry.definition_entry = load
    def check(ready, cpu='FX3U'):
        return check_instruction_constraints('TEST', {'READY': ready}, plc_model=cpu,
                                             language='ladder', registry=registry)

    assert check(True)['status'] == 'passed'
    assert check(False)['status'] == 'failed'
    assert reads == [('TEST', 'FX3U')]
    assert check(False, 'FX5U')['status'] != 'failed'
    assert reads[-1] == ('TEST', 'FX5U')

    fact['value']['predicate'] = {'op': 'constant', 'value': True, 'type': {'kind': 'bool'}}
    registry._definition_owner_store = object()
    assert check(False)['status'] == 'passed'
    assert len(reads) == 3
    registry.fact_bundle_current = lambda: False
    assert check(False)['status'] == 'unverified'
    assert len(reads) == 3


def test_legacy_promotions_fill_gaps_without_replacing_compiled_cpu_contracts(tmp_path):
    mov = DEFAULT_INSTRUCTION_REGISTRY.resolve('MOV')
    shift = DEFAULT_INSTRUCTION_REGISTRY.resolve('SFTL')
    registry = InstructionRegistry([mov, shift])
    key = ('mitsubishi', 'MOV', 'FX3U')
    compiled = replace(mov, native_operand_order=('SOURCE', 'DESTINATION'))
    registry._cpu_contracts[key] = compiled
    payload = {
        'schema_version': 1, 'cpu': 'FX3U',
        'method': 'native-table-geometry+independent-ST-signature-v1',
        'sources': {name: {'manual': 'synthetic', 'revision': '1', 'url': 'https://example.invalid'}
                    for name in ('fx3_programming_r', 'fxcpu_basic_applied_m')},
        'entries': [
            {'forms': ['MOV'], 'arity': 2, 'native_order': ['S', 'D'],
             'native_page': 1, 'structured_page': 1},
            {'forms': ['SFTL'], 'arity': 4, 'native_order': ['S', 'D', 'N1', 'N2'],
             'native_page': 1, 'structured_page': 1},
        ],
    }
    ledger = tmp_path / 'promotions.json'
    ledger.write_text(json.dumps(payload), encoding='utf-8')
    registry.load_contract_promotions(ledger, preserve_contracts={key})
    assert registry._cpu_contracts[key] is compiled
    assert registry.resolve('SFTL', cpu='FX3U').native_operand_order == ('S', 'D', 'N1', 'N2')
    assert ('mitsubishi', 'MOV', 'FX5U') not in registry._cpu_contracts
    assert registry.resolve('MOV').native_operand_order == mov.native_operand_order
