"""Own N/O source facts for Q/L ST literal operators; not callable contracts."""
import sqlite3
from pathlib import Path

from tools.fact_ql_behavior_review import reviewed_ql_behavior


PRIVATE = Path('D:/gxworks-agent-private/resources/knowledge')
QUEUE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/execution-queue.json')
OPERATORS = ('&', '**', '<', '<=', '<>', '=', '>', '>=')
ORIGINAL_IDS = {
    '&': 'I-00015', '**': 'I-00020', '<': 'I-00029', '<=': 'I-00030',
    '<>': 'I-00031', '=': 'I-00032', '>': 'I-00033', '>=': 'I-00034',
}
PRIORITY = {'&': 3, '**': 9, '<': 5, '<=': 5, '>': 5, '>=': 5, '=': 4, '<>': 4}


def _fact_map(entry):
    return {fact['id']: fact for fact in entry}


def _source_pages(fact, manual):
    return {page for source in fact.get('sources', ()) if source.get('manual_id') == manual
            for page in source.get('pdf_pages', ())}


def test_exact_original_ql_operator_owners_have_source_bound_expression_contracts():
    import json

    queue = json.loads(QUEUE.read_text(encoding='utf-8'))['instructions']
    model_sets = {
        opcode: {str(model).upper() for model in queue[opcode].get('models', ())
                 if str(model).upper().startswith(('Q', 'L'))}
        for opcode in OPERATORS
    }
    assert {opcode: queue[opcode].get('unit_ids') for opcode in OPERATORS} == {
        opcode: [unit_id] for opcode, unit_id in ORIGINAL_IDS.items()}
    expected_models = next(iter(model_sets.values()))
    assert len(expected_models) == 53
    assert all(models == expected_models for models in model_sets.values())

    source = sqlite3.connect(
        (PRIVATE / 'fx3u_knowledge.sqlite').as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        reviewed, _receipt = reviewed_ql_behavior(
            source, manuals=['sh080784engn'], opcodes=OPERATORS,
            source_pdf_root=PRIVATE / 'sources')
    finally:
        source.close()

    for opcode in OPERATORS:
        actual_models = {model for model, form in reviewed if form == opcode}
        assert actual_models == expected_models, opcode
        for model in expected_models:
            facts = _fact_map(reviewed[(model, opcode)])
            expression = facts['authored:ql:literal_expression:parameters.language_expression']
            typed = facts['authored:ql:literal_typed:parameters.typed_operands']
            arity = facts['authored:ql:literal_argument_ST_expression_operands:parameters.arity']
            order = facts['authored:ql:literal_argument_ST_expression_operands:parameters.order']
            not_function = facts['authored:ql:literal_not_function:parameters.st_signature']

            assert expression['status'] == 'source_verified', (model, opcode)
            assert expression['value']['representation'] == 'ST_expression'
            assert expression['value']['available'] is True
            assert expression['value']['operator'] == opcode
            assert expression['value']['operator_priority'] == PRIORITY[opcode]
            assert expression['value']['same_priority_evaluation'] == 'leftmost_operator_first'
            assert set(expression.get('depends_on', ())) >= {
                typed['id'], arity['id'], order['id'],
                'authored:ql:cpu_applicability:constraints.cpu_availability'}
            assert typed['status'] == arity['status'] == order['status'] == 'source_verified'
            assert arity['value']['minimum'] == 2
            assert arity['value']['maximum'] == (2 if opcode in {'**', '<>'} else 28)
            assert not_function['status'] == 'source_verified'
            assert not_function['value']['available'] is False
            assert not_function['value']['reason'] == 'literal_ST_operator_is_an_expression'
            no_fbd = facts['authored:ql:literal_no_FBD_block:parameters.fbd_signature']
            assert no_fbd['status'] == 'source_verified'
            assert no_fbd['value']['available'] is False
            assert not any(fact['dimension'] == 'parameters.st_signature'
                           and fact.get('value', {}).get('available') is True
                           for fact in reviewed[(model, opcode)])
            assert _source_pages(expression, 'structured_fundamentals_o') == {76}
            assert _source_pages(typed, 'sh080784engn') == (
                {214} if opcode == '**' else {215} if opcode == '&' else {217})

            operand_types = [operand['type'] for operand in typed['value']['operands']]
            if opcode == '&':
                assert operand_types == ['ANY_BIT'] * 28
                assert _source_pages(expression, 'sh080784engn') == {215, 216}
                history = expression['value']['source_syntax_history']
                assert history == {
                    'historical_page': 215,
                    'issue': 'operator_ST_panel_contains_function_call_tail',
                    'trailing_output_parameter': 'd',
                    'unmatched_closing_parenthesis': True,
                    'resolution': 'resolved_by_own_page_216_ST_program_example',
                    'resolving_source_page': 216,
                }
                assert not_function['value']['source_syntax_history'] == history
                assert _source_pages(not_function, 'sh080784engn') == {215, 216}
                assert 'source_syntax_conflict' not in expression['value']
            elif opcode == '**':
                assert operand_types == ['ANY_REAL', 'ANY_NUM']
            else:
                expected_count = 2 if opcode == '<>' else 28
                assert len(operand_types) == expected_count
                assert operand_types == ['ANY_SIMPLE'] * expected_count
                assert expression['value']['returns'] == 'BOOL'
                assert expression['value']['comparison_chain_intermediate_result_type'] == 'Bit'
                assert expression['value']['comparison_operand_type_compatibility'] == (
                    'not_stated_in_reviewed_source')
