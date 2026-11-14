"""Composing shared CMP facts must preserve the corrected Native proof."""
import copy
import json
from pathlib import Path

import pytest

HERE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-cmp-native-scope-repair')


@pytest.mark.parametrize('opcode', ['CMP', 'CMPP', 'DCMP', 'DCMPP'])
def test_cmp_family_composition_keeps_cpu_support_and_three_output_bits(opcode):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.instruction_knowledge import compose_programming_facts
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    source = next(e for e in json.loads((HERE/'source-facts.json').read_text())['entries']
                  if e['target_model'] == 'Q00U' and e['opcode'] == opcode)
    entry = registry.definition_entry(opcode, cpu='Q00U')
    entry['facts'] = [f for f in entry['facts'] if not f['id'].startswith(('authored:ql:', 'authored:applied:'))] + copy.deepcopy(source['facts'])
    entry['facts'], replaced = compose_programming_facts(registry, opcode, cpu='Q00U', existing=entry['facts'])
    family = [f for f in entry['facts'] if f['id'].startswith('family:CMP:ql:comparison:')]
    assert family
    assert not any(c.get('basis') == 'unclassified_clause_not_formalised'
                   for f in family for c in f.get('conditions', []))
    typed = next(f for f in family if f['dimension'] == 'parameters.typed_operands')
    assert typed['value']['operands'][-1]['occupied_bit_count'] == 3
    availability = next(f for f in family if f['dimension'] == 'constraints.cpu_availability')
    assert availability['value']['available'] is True
    assert availability['value']['model_states']['Q00U'] == 'supported'
    assert refresh_reviewed_call_admission(entry, registry) is True
    assert entry['call_available'] is True
