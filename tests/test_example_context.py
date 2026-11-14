"""Model-facing examples retain code and behavior, not preview reports."""
import copy

from shared.model_context import model_knowledge_value


def test_example_program_preserves_ordered_logic_and_case_conditions():
    instructions = [{'op': 'LD', 'args': ['X0']}, {'op': 'OUT', 'args': ['T0', 'K3']}]
    example = {'kind': 'example', 'scope': {'models': ['FX3U']}, 'value': {
        'requirement': 'Enable the delay while input is active.',
        'initial': {'T0': 0}, 'frames': [{'inputs': {'X0': True}, 'timer_elapsed_ms': {'T0': 300}}],
        'expected': {'Y0': True}, 'limitation': 'No physical timing verification.',
        'program': {'kind': 'plc_program_ir', 'schema_version': 3, 'plc': {'cpu': 'FX3U'},
                    'networks': [{'id': 'N1', 'instructions': instructions,
                                  'execution': {'execution_context': 'CYCLIC'}, 'ladder': {'duplicate': True}}],
                    'analysis': {'counts': {'warning': 0}}, 'timing': {'estimated_scan_ms': 1},
                    'devices': {'X0': {'read_by': ['N1']}}, 'source': {'ladder_sha256': 'preview'}}}}
    before = copy.deepcopy(example)
    result = model_knowledge_value(example)
    assert example == before
    for key in ('requirement', 'initial', 'frames', 'expected', 'limitation'):
        assert result['value'][key] == example['value'][key]
    program = result['value']['program']
    assert program['plc'] == {'cpu': 'FX3U'}
    assert program['networks'] == [{'id': 'N1', 'instructions': instructions,
                                  'execution': {'execution_context': 'CYCLIC'}}]
    assert not {'analysis', 'timing', 'devices', 'source'} & program.keys()


def test_non_ir_program_facts_and_networks_without_instruction_code_are_kept():
    fact = {'kind': 'instruction', 'value': {'timing': {'period_ms': 10},
                                           'analysis': 'source-defined calculation', 'devices': ['D0']}}
    assert model_knowledge_value(fact) == fact
    program = {'kind': 'plc_program_ir', 'networks': [{'ladder': {'statement': 'source'}}]}
    assert model_knowledge_value(program) == program
