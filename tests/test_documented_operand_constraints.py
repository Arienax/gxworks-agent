"""Shared source-shaped relations and their actual native execution binding."""
import copy

import pytest

from plc.instruction_constraints import check_fact_constraints, check_instruction_constraints
from plc.instruction_constraints import _condition
from plc.instruction_definition import UnknownInstructionSemantics


def report(value, *, operands=('M10', 'M100', 'K64', 'K1'), numbers=None,
           model='FX3U', verified=True, extra_signature=None):
    def fact(identity, dimension, body):
        return {'id': identity, 'dimension': dimension, 'value': body,
                'status': 'source_verified' if verified else 'unknown',
                'verification': 'source_checked' if verified else 'not_checked',
                'sources': [{'source_id': 'synthetic_relation_test'}],
                'scope': {'models': [model], 'forms': ['TEST'], 'languages': ['ladder']},
                'depends_on': [], 'conditions': []}
    signature = fact('signature', 'parameters.typed_operands', {
        'operand_count': 4, 'operands': [{'name': name} for name in ('S', 'D', 'N1', 'N2')]})
    rule = fact('relation', 'constraints.documented_restriction', value)
    rule['depends_on'] = ['signature']
    facts = [signature, rule]
    if extra_signature:
        extra = copy.deepcopy(signature)
        extra['id'] = 'conflicting-signature'
        extra['value']['operands'][0]['name'] = extra_signature
        facts.append(extra)
    return check_fact_constraints(facts, {'$OPERANDS': list(operands),
        **({'N1': 64, 'N2': 1} if numbers is None else numbers)},
        target='TEST', plc_model=model, language='ladder')


BOUNDS = {'ordered_bounds': [{'parameter': 'N2', 'minimum': 0, 'maximum_operand': 'N1'},
                             {'parameter': 'N1', 'maximum': 1024}]}
HEADS = {'rules': [{'kind': 'same_head', 'operands': ['S', 'D'],
                    'models': ['FX3U', 'FX3UC'], 'result': 'operation_error', 'code': 6710}]}
WORD_BIT = {'rules': [{'kind': 'word_bit_operand', 'operand': 'S',
                       'models': ['FX3U', 'FX3UC'], 'index_modification': False}]}


@pytest.mark.parametrize('numbers,status', [({'N1':64,'N2':1}, 'passed'),
    ({'N1':0,'N2':0}, 'passed'), ({'N1':1024,'N2':1024}, 'passed'),
    ({'N1':64,'N2':65}, 'failed'), ({'N1':-1,'N2':0}, 'failed'),
    ({'N1':1025,'N2':1}, 'failed'), ({'N1':64,'N2':-1}, 'failed'),
    ({'N1':64}, 'unverified'), ({'N1':64,'N2':True}, 'unverified')])
def test_ordered_numeric_relations(numbers, status):
    assert report(BOUNDS, numbers=numbers)['status'] == status


@pytest.mark.parametrize('source,destination,status', [('M10','M100','passed'),
    ('M010','M10','failed'), ('D10.0','D10.1','passed'),
    ('M10Z0','M100','unverified')])
def test_heads_use_bound_operand_roles_and_device_identity(source, destination, status):
    assert report(HEADS, operands=(source,destination,'K64','K1'))['status'] == status


@pytest.mark.parametrize('source,model,status', [('M10','FX3G','passed'),
    ('D0.0','FX3G','failed'), ('D0.F','FX3U','passed'),
    ('D0.0Z0','FX3U','failed'), ('D0.0V1','FX3UC','failed')])
def test_word_bit_qualification_does_not_restrict_ordinary_bits(source, model, status):
    assert report(WORD_BIT, operands=(source,'M100','K64','K1'), model=model)['status'] == status


def test_unrecognised_clauses_and_ambiguous_bindings_remain_unknown():
    unknown = copy.deepcopy(BOUNDS)
    unknown['ordered_bounds'][0]['undocumented_rule'] = True
    assert report(unknown)['status'] == 'unverified'
    assert report({'rules': [*HEADS['rules'], {'kind': 'unimplemented'}]})['status'] == 'unverified'
    assert report(HEADS, extra_signature='SOURCE')['status'] == 'unverified'


@pytest.mark.parametrize('op', ['SFTL', 'SFTR', 'SFTLP', 'SFTRP', 'WSFL', 'WSFR'])
def test_actual_native_shift_constraints_do_not_require_invented_cpu_version(op):
    word = op.startswith('WS')
    operands = ['D10','D100','K8','K1'] if word else ['M10','M100','K64','K1']
    result = check_instruction_constraints(op, {'$OPERANDS':operands, 'S':None, 'D':None,
        'N1':8 if word else 64, 'N2':1, '$2':8 if word else 64, '$3':1},
        plc_model='FX3U', language='ladder', context={'project_mode':'native_ladder_instruction'})
    assert result['status'] == 'passed', result['findings']


@pytest.mark.parametrize('model,minimum', [('FX3U','2.20'), ('FX3UC','1.00'), ('FX3GC','1.40')])
def test_raw_condition_has_no_runtime_first_release_fallback(model, minimum):
    context = {'$PLC_MODEL':model}
    with pytest.raises(UnknownInstructionSemantics):
        _condition({'context':'cpu_version','minimum':minimum}, {}, context)
    assert context == {'$PLC_MODEL':model}


@pytest.mark.parametrize('condition,context', [
    ({'context':'cpu_version','minimum':'1.10'}, {'$PLC_MODEL':'FX3G'}),
    ({'context':'cpu_version','minimum_exclusive':'2.20'}, {'$PLC_MODEL':'FX3U'}),
    ({'context':'cpu_version','minimum':'2.20','maximum_exclusive':'3.00'}, {'$PLC_MODEL':'FX3U'}),
    ({'context':'cpu_version','maximum_exclusive':'2.20'}, {'$PLC_MODEL':'FX3U'}),
    ({'context':'cpu_version','minimum':'2.20'}, {'$PLC_MODEL':'FX3U/FX3UC'}),
    ({'context':'software_version','minimum':'2.20'}, {'$PLC_MODEL':'FX3U'}),
])
def test_later_features_upper_bounds_unknown_models_and_software_stay_unknown(condition, context):
    with pytest.raises(UnknownInstructionSemantics):
        _condition(condition, {}, context)


def test_explicit_version_is_not_overridden_by_first_release():
    assert not _condition({'context':'cpu_version','minimum':'2.20'}, {},
                          {'$PLC_MODEL':'FX3U','cpu_version':'2.19'})


@pytest.mark.parametrize('op', ['WSFL', 'WSFR'])
@pytest.mark.parametrize('source,destination,status', [('D10','D10','failed'),
    ('K4M10','K2M100','failed'), ('K4M10','K4M100','passed')])
def test_word_shift_alias_and_group_width_are_not_waived(op, source, destination, status):
    result = check_instruction_constraints(op,
        {'$OPERANDS':[source,destination,'K8','K1'], 'N1':8, 'N2':1, '$2':8,'$3':1},
        plc_model='FX3U', language='ladder', context={'project_mode':'native_ladder_instruction'})
    assert result['status'] == status, result['findings']
