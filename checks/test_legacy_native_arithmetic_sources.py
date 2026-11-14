import json
from pathlib import Path

from plc.instruction_knowledge import family_fact_groups
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry


def _facts(opcode, cpu):
    return [f for f in family_fact_groups(registry, opcode, cpu=cpu)
            if ':legacy:arithmetic:' in f['id']]


def test_fx1nc_plain_arithmetic_uses_its_literal_R_catalogue_and_own_sections():
    for opcode, page in [('ADD', 273), ('SUB', 275), ('MUL', 277), ('DIV', 280)]:
        facts = _facts(opcode, 'FX1NC')
        verified = [f for f in facts if f['status'] == 'source_verified']
        assert verified
        assert not any(':jy992d88101e:' in f['id'] for f in verified)
        own = [f for f in verified if ':fx3_programming_r:' in f['id']]
        typed, = [f for f in own if f['dimension'] == 'parameters.typed_operands']
        assert [p['name'] for p in typed['value']['operands']] == ['S1', 'S2', 'D']
        assert typed['scope']['forms'] == [opcode]
        assert typed['scope']['models'] == ['FX1NC']
        assert all(s['manual_id'] == 'fx3_programming_r' and 76 in s['pdf_pages']
                   and page in s['pdf_pages'] for s in typed['sources'])
        assert not any(c.get('device') == 'M8023' for c in typed['conditions'])
        assert any(f['status'] == 'candidate_evidence' and ':jy992d88101e:' in f['id'] for f in facts)
        assert any(f['dimension'] == 'execution.disabled_state' for f in own)


def test_old_five_signed_plain_DIV_has_direct_R_negative_rule_evidence():
    from plc.instruction_definition import evaluate_expression
    for cpu in ['FX1S', 'FX1N', 'FX1NC', 'FX2N', 'FX2NC']:
        facts = _facts('DIV', cpu)
        body, = [f for f in facts if f['dimension'] == 'effects.documented_processing'
                 and any(r.get('kind') == 'expression_write' for r in f['value'].get('rules', []))
                 and f['status'] == 'source_verified']
        assert any(s['manual_id'] == 'fx3_programming_r' and set([76, 280, 281]) <= set(s['pdf_pages'])
                   for s in body['sources'])
        outputs = body['value']['rules'][0]['outputs']
        for dividend, divisor, quotient, remainder in [(36, -5, -7, 1), (-36, 5, -7, -1), (-36, -5, 7, -1)]:
            assert [evaluate_expression(o['expression'], {'S1': dividend, 'S2': divisor}) for o in outputs] == [quotient, remainder]
        assert 'M8304' not in json.dumps(body['value']) and 'M8306' not in json.dumps(body['value'])


def test_J_plain_DIV_preserves_layout_and_divide_zero_but_not_unproved_signed_relation():
    from tools.inventory_instruction_semantics import _concrete_relation, _partial_effect_content
    for cpu in ['FX0', 'FX0N', 'FX0S', 'FX1', 'FX2C', 'FXU']:
        facts = _facts('DIV', cpu)
        partial, = [f for f in facts if f['dimension'] == 'effects.documented_processing'
                    and f['value'].get('full_source_relation_complete') is False]
        assert not _concrete_relation(partial)
        assert _partial_effect_content(partial)
        assert partial['value']['result_relation']['remainder_offset_words'] == 1
        assert partial['value']['unresolved_items'] == ['negative_quotient_rounding', 'negative_remainder_sign']
        assert not any(r.get('kind') == 'expression_write' for f in facts for r in f['value'].get('rules', []))
        assert any(r.get('kind') == 'division_zero' for f in facts for r in f['value'].get('rules', []))


def test_R_repair_branch_does_not_grant_other_width_or_pulse_forms():
    path = Path(__file__).resolve().parents[1] / 'resources/instructions/mitsubishi/instruction_families.json'
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    for definition in data['instruction_families']:
        base = definition['base_opcode']
        if base not in {'ADD', 'SUB', 'MUL', 'DIV'}:
            continue
        facts = [f for f in definition['facts']
                 if f['id'].startswith('family:' + base + ':legacy:arithmetic:fx3_programming_r:')]
        assert facts and all(f['scope']['forms'] == [base] for f in facts)
        form, = [f for f in facts if f['dimension'] == 'parameters.variant_family']
        assert form['value']['section_variants'] == [base]


def test_producer_does_not_reverify_E_arithmetic_for_FX1NC_or_touch_M_and_R():
    from copy import deepcopy
    from tools.fact_applied_review import _qualify_fx1nc_arithmetic_e_scope
    for opcode in ['ADD', 'SUB', 'MUL', 'DIV']:
        facts = [{'id': 'old:' + manual, 'status': 'source_verified', 'verification': 'source_checked',
                  'sources': [{'manual_id': manual}], 'value': {'original': True}}
                 for manual in ['jy992d88101e', 'fxcpu_basic_applied_m', 'fx3_programming_r']]
        before = deepcopy(facts)
        _qualify_fx1nc_arithmetic_e_scope(facts, opcode)
        assert facts[0]['status'] == 'candidate_evidence'
        assert facts[0]['verification'] == 'not_performed'
        assert facts[0]['value'] == before[0]['value']
        assert facts[1:] == before[1:]
    untouched = deepcopy(before)
    _qualify_fx1nc_arithmetic_e_scope(untouched, 'ADDP')
    assert untouched == before
