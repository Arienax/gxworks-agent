"""Review guards and semantic boundaries; no PLC execution claim."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from tools import fact_fx5_numeric_backlog_review as review


def entry(page, suffix):
    return next(e for e in review.NUMERIC_BACKLOG_REVIEWS
                if e['page'] == page and e['source_key'].endswith(suffix))


def rules(page, suffix):
    return entry(page, suffix)['review']['rules']


def test_every_entry_has_complete_and_fail_closed_witness():
    for e in review.NUMERIC_BACKLOG_REVIEWS:
        group=e['review'].get('content_role')=='source_group_summary'
        assert e['review']['full_clause_review'] is (not group)
        assert bool(e['review']['rules']) is (not group)
        assert e['review']['value_updates']['core_execution_capability'] == 'not_established'
        for key in ('clause_sha256', 'block_sha256', 'outline_sha256', 'section_sha256', 'block_structure_sha256', 'section_artifacts_sha256'):
            assert len(e[key]) == 64
        assert review.review_numeric_backlog_clause(e['kind'], 'unreviewed source text',
            section={'first_page':e['first_page']}, block={'page':e['page'],'text':'unreviewed'},
            value={}, declared=e['required_operands']) is None


def test_witness_guards_and_defensive_copy():
    e=deepcopy(review.NUMERIC_BACKLOG_REVIEWS[0])
    text={'clause':'fixture clause','block':'fixture block','outline':'fixture outline','section':'fixture context'}
    for k,v in text.items():e[k+'_sha256']=review._digest(v)
    old_index=review._INDEX
    review._INDEX={(e['kind'],e['page'],e['clause_sha256']):e}
    section={'first_page':e['first_page'],'outline':text['outline'],'text':text['section']}
    block={'page':e['page'],'text':text['block'],'line':12,'bound_subjects':[{'operand':'s1'}]}
    e['block_structure_sha256']=review._structure_digest(block)
    e['section_artifacts_sha256']=review._artifact_digest(section)
    def call(**kw):
        args=dict(section=section,block=block,value={},declared=set(e['required_operands']))
        args.update(kw)
        return review.review_numeric_backlog_clause(e['kind'],text['clause'],**args)
    accepted=call(); assert accepted == e['review']
    accepted['rules'].clear(); assert call()['rules']
    for name in ('first_page','outline','text','tables','page_layouts','page_texts'):
        changed=dict(section);changed[name]='changed'
        assert call(section=changed) is None
    for name in ('page','text','line','bound_subjects','codes','operand_type_conditions','domain'):
        changed=dict(block);changed[name]='changed'
        assert call(block=changed) is None
    assert call(declared=set()) is None
    review._INDEX=old_index


def test_signed_conversion_is_not_storage_domain():
    rr=rules(370,'r1_c7')
    assert [(x['minimum'],x['maximum']) for x in rr]==[(-32768,32767),(0,65535)]
    assert [x['when']['instruction_signedness'] for x in rr]==['signed','unsigned']
    assert all('conversion_result' in x['subject'] for x in rr)
    assert -32768 >= rr[0]['minimum'] and 32768 > rr[0]['maximum']
    assert 65535 <= rr[1]['maximum'] and -1 < rr[1]['minimum']


def test_unsigned_sign_does_not_accept_minus():
    assert rules(370,'r1_c4')[0]['allowed_values']==[32]
    assert rules(370,'r1_c3')[1]['allowed_values']==[32,45]


def test_digit_charset_does_not_reject_padding_by_global_whitelist():
    r=rules(373,'r1_c5')[0]
    assert r['part']=='numeric_digit_positions'
    assert r['padding_and_sign_positions_excluded']
    assert r['allowed_code_intervals']==[[48,57]] and r['allowed_codes']==[46]


def test_binary32_input_classes_do_not_invent_sqrt_zero_semantics():
    r=rules(1260,'r0_c0')[0]
    assert r['allowed_zero']=='positive_only'
    assert set(r['forbidden_classes'])=={'negative_zero','subnormal','nan','positive_infinity','negative_infinity'}
    assert r['normal_magnitude']['minimum_inclusive']
    assert not r['normal_magnitude']['maximum_inclusive']
    assert not any(e['page']==1293 for e in review.NUMERIC_BACKLOG_REVIEWS)


def test_byte_span_rounding_is_address_not_numeric_value():
    import math
    for p,operand in ((394,'s'),(397,'d')):
        r=rules(p,'r0_c0')[0]
        assert r['subject']=={'address_offset_from':{'operand':operand}}
        assert r['not_a_numeric_input_rounding_rule']
        assert r['mode']=='ceiling'
        assert [math.ceil(n/2-1) for n in (1,2,3,5)]==[0,0,1,2]


def test_divisor_binding_not_dividend():
    for p in (684,688):
        r=rules(p,'r0_c0' if p==684 else 'r2_c0')[0]
        assert r['left']=={'operand':'s2'} and r['operator']=='=' and r['right']==0


def test_32bit_block_span_multiplies_source_not_result():
    rr=rules(211,'r0_c0')
    assert len(rr)==3
    assert rr[0]['points']=={'operator':'*','left':{'operand':'n'},'right':2}
    assert rr[1]['points']==rr[0]['points']
    assert rr[2]['points']=={'operand':'n'} and rr[2]['point_unit']=='result_bit'


def test_module_footnote_only_restricts_marked_doubleword_devices():
    for p,target,form in ((936,'d','DFROM'),(940,'s2','DTO'),(943,'d','DFROMD'),(946,'s2','DTOD')):
        r=next(e for e in review.NUMERIC_BACKLOG_REVIEWS if e['page']==p)['review']['rules'][0]
        assert r['when']=={'operand':target,'device_classes':['LC','LZ']}
        assert r['allowed_forms']==[form,form+'P']
        assert 'FX5S' not in r['cpu_models']


def test_version_gate_is_not_all_languages_or_ENO_forms():
    for p in (1341,1343):
        r=next(e for e in review.NUMERIC_BACKLOG_REVIEWS if e['page']==p)['review']['rules'][0]
        assert r['language']=='st' and r['with_EN_ENO'] is False
        assert r['minimum_version']=='1.035M'


def test_conflicted_string_time_and_estr_bounds_not_accepted():
    keys={e['source_key'] for e in review.NUMERIC_BACKLOG_REVIEWS}
    assert 'condition_error_p1281_r0_c1' not in keys
    for c in (2,3,4,5):assert f'condition_error_p631_r1_c{c}' not in keys
    assert 'condition_error_p624_r0_c0' not in keys


def test_concurrency_not_number_of_scans():
    for p,key,limit in ((782,'r0_c0',4),(830,'r2_c0',1)):
        r=rules(p,key)[0]
        assert r['maximum']==limit
        assert 'simultaneously' in r['count_basis']


def test_fins_reserves_inserted_word_and_header():
    a=rules(481,'r0_c0')[0];b=rules(483,'r0_c0')[0]
    assert a['points']['right']==2 and b['points']['right']==1
    assert a['stored_count_phase']=='before_execution'
    assert a['inserted_word_included'] and not b['inserted_word_included']
    assert entry(481,'r0_c0')['review']['value_updates']['complete_legal_call_proof'] is False
    # Four stored entries occupy five words; insertion must fit a sixth.
    assert 4+a['points']['right']==6
    assert 4+b['points']['right']==5


def test_eno_false_binds_function_result_not_missing_argument():
    e=entry(1331,'r1_c0');r=e['review']['rules'][0]
    assert e['required_operands']==['eno']
    assert r['result']=={'role':'function_result','source_symbol':'d','state':'undefined','must_not_consume':True}
    binding=e['review']['value_updates']['result_binding']
    assert binding['not_an_input_argument'] and binding['declaration_type']=='ANY_BIT'
    assert binding['forms']==['AND_E','OR_E','XOR_E']


def test_ne_string_error_applies_to_each_bound_input_only():
    for suffix in ('r0_c0','r1_c0'):
        rr=rules(1344,suffix)
        assert [r['when']['operand'] for r in rr]==['s1','s2']
        assert all(r['when']['data_type']=='STRING' for r in rr)
        assert entry(1344,suffix)['required_operands']==['s1','s2']


def test_extrema_invalid_real_has_cross_manual_error_witness():
    for p in (735,737):
        e=entry(p,'r1_c0');r=e['review']['rules'][0]
        assert r['subject']['element_count']=={'operand':'n'}
        assert r['subject']['stride_words']==2 and r['quantifier']=='any_input_element'
        witness=e['review']['additional_sources'][0]
        assert witness=={'manual_id':'fx5_application_ag','manual_number':'JY997D55401','revision':'AG','pdf_pages':[935]}
        assert 'external_source_witnesses' not in e['review']['value_updates']


def test_exp_published_precision_is_not_fabricated_ieee_threshold():
    r=rules(1297,'r0_c0')[0]
    assert r['ieee_exact_threshold_not_inferred'] and not r['input_numeric_gate']
    assert r['allowed_intervals'][1]=={'minimum_decimal':{'coefficient':117549,'base':10,'exponent':-43,'significant_digits':6},'maximum_decimal':{'coefficient':340282,'base':10,'exponent':33,'significant_digits':6}}


def test_asci_diagram_is_mode_bound_byte_layout_not_timing_claim():
    e=entry(617,'r1_c3');r=e['review']['rules'][0]
    assert r['when']=={'device':'SM8161','state':True}
    assert r['source_bits']==8 and r['destination_bits']==16 and r['upper_byte']==0
    assert e['review']['value_updates']['execution_timing_claim'] is False


def test_estr_exact_signed_binary32_domain_and_bit_classes():
    from fractions import Fraction
    import struct
    e=entry(631,'r2_c0');r=e['review']['rules'][0]
    assert r['kind']=='invalid_binary32_input_domain' and r['subject']=={'operand':'s1'}
    assert r['allowed_zero']=='positive_only'
    assert r['normal_magnitude']=={'minimum_power_of_two':-126,'minimum_inclusive':True,'maximum_power_of_two':128,'maximum_inclusive':False}
    assert r['signed_intervals'][0]['minimum_inclusive'] is False
    assert r['signed_intervals'][0]['maximum_inclusive'] is True
    lo=Fraction(1,2**126);hi=Fraction(2**128)
    def allowed_word(word):
        sign=word>>31;exp=(word>>23)&255;mantissa=word&0x7fffff
        if exp==0:return sign==0 and mantissa==0
        if exp==255:return False
        number=Fraction.from_float(struct.unpack('!f',struct.pack('!I',word))[0])
        return lo<=abs(number)<hi
    valid=[0x00000000,0x00800000,0x80800000,0x3f800000,0xbf800000,0x7f7fffff,0xff7fffff]
    invalid=[0x80000000,0x00000001,0x007fffff,0x80000001,0x807fffff,0x7f800000,0xff800000,0x7fc00000,0xffc00000,0x7f800001]
    assert all(allowed_word(x) for x in valid)
    assert not any(allowed_word(x) for x in invalid)
    assert lo<=lo<hi and lo<=abs(-lo)<hi
    assert not lo<=hi<hi and not lo<=abs(-hi)<hi
    # Approximate printed decimals must not replace exact binary endpoints.
    assert Fraction('1.17549E-38')<lo
    assert Fraction.from_float(struct.unpack('!f',bytes.fromhex('7f7fffff'))[0])>Fraction('3.40282E38')
    assert e['review']['additional_pages']==[34,35]
    assert e['review']['additional_sources']==[
        {'manual_id':'fx5_programming_ab','manual_number':'JY997D55801','revision':'AB','pdf_pages':[34,35]},
        {'manual_id':'fx5_application_ag','manual_number':'JY997D55401','revision':'AG','pdf_pages':[935]}]
    assert e['review']['value_updates']['complete_legal_call_proof'] is False
    # Separate ESTR display/format boundaries remain unresolved.
    keys={x['source_key'] for x in review.NUMERIC_BACKLOG_REVIEWS}
    assert all(f'condition_error_p631_r1_c{x}' not in keys for x in (2,3,4,5))


def test_group_summaries_are_nonexhaustive_containers_not_predicates():
    for p in (1275,1277):
        e=entry(p,'r0_c0');r=e['review'];v=r['value_updates']
        assert r['content_role']=='source_group_summary'
        assert r['dimension']=='resources.error_group_summary'
        assert r['rules']==[] and r['full_clause_review'] is False
        assert v['subjects']==[{'operand':'s'}]
        assert v['source_checked']=='container_structure_only'
        assert v['exhaustiveness_not_established'] and not v['complete_legal_call_proof']
        assert v['parser_capability']=='unverified' and v['source_group_error_code']=='3401H'
        children=[f'condition_error_p{p}_r0_c{i}' for i in (1,2)]
        assert v['member_source_keys']==children and r['depends_on_keys']==children
        assert e['source_key'] not in children
        for key in children:
            child=next(x for x in review.NUMERIC_BACKLOG_REVIEWS if x['source_key']==key)
            assert child['review']['full_clause_review'] is True
            assert not child['review'].get('depends_on_keys')


def test_group_dependencies_split_without_cross_language_or_cycle():
    from tools.fact_fx5_condition_review import _structured_ladder_condition_specs
    for p in (1275,1277):
        source=[]
        for index in (0,1,2):
            key=f'condition_error_p{p}_r0_c{index}'
            spec={'key':key,'languages':['ladder','st','fbd'],'status':'declared_unverified' if index==0 else 'source_verified',
                  'value':{'source_kind':'error','source_page':p,'source_line':1,'clause_index':index}}
            if index==0:spec['depends_on_keys']=entry(p,'r0_c0')['review']['depends_on_keys']
            source.append(spec)
        result=_structured_ladder_condition_specs({'outline':'PART 5 TYPE CONVERSION FUNCTIONS'},source)
        by_key={v['key']:v for v in result}
        parents=[v for v in result if v['value']['clause_index']==0]
        assert len(parents)==2
        for parent in parents:
            assert len(parent['depends_on_keys'])==2
            for child_key in parent['depends_on_keys']:
                child=by_key[child_key]
                assert set(child['languages'])==set(parent['languages'])
                assert not child.get('depends_on_keys')
                assert child['key']!=parent['key']


def test_exp_displayed_decimal_bounds_reconstruct_exactly_and_project():
    from decimal import Decimal
    from knowledge.fact_corpus import typed_value,project_definition
    r=rules(1297,'r0_c0')[0]
    expected=[('-3.40282E+38','-1.17549E-38'),('1.17549E-38','3.40282E+38')]
    for interval,pair in zip(r['allowed_intervals'],expected):
        for key,target in zip(('minimum_decimal','maximum_decimal'),pair):
            val=interval[key]
            assert type(val['coefficient']) is int and type(val['exponent']) is int
            assert val['base']==10 and val['significant_digits']==6
            assert Decimal(val['coefficient'])*(Decimal(val['base'])**val['exponent'])==Decimal(target)
    assert typed_value(r)==r
    for suffix,language in (('', 'st'),('_structured_ladder','ladder')):
        fact={'id':'condition_error_p1297_r0_c0'+suffix,'dimension':'constraints.documented_errors','status':'source_verified',
              'value':{'rules':[r]},'scope':{'language':language},'sources':[{'manual_id':'fx5_programming_ab','revision':'AB','pdf_pages':[1297]}]}
        result,withheld=project_definition({'opcode':'EXP','target_model':'FX5U','facts':[fact]})
        assert not withheld and result['facts'][0]['value']=={'rules':[r]}
        assert result['facts'][0]['sources']==fact['sources']


def test_every_numeric_review_value_survives_typed_projection():
    from knowledge.fact_corpus import typed_value,project_definition
    for e in review.NUMERIC_BACKLOG_REVIEWS:
        value=dict(e['review']['value_updates'],rules=e['review']['rules'])
        assert typed_value(value)==value,e['source_key']
        fact={'id':e['source_key'],'dimension':e['review']['dimension'],'status':'declared_unverified' if e['review'].get('content_role')=='source_group_summary' else 'source_verified','value':value}
        projected,withheld=project_definition({'opcode':'REVIEW_TEST','facts':[fact]})
        assert not withheld,e['source_key']
        assert projected['facts'][0]['value']==value


class NumericBacklogTests(unittest.TestCase):
    pass

for _name, _function in list(globals().items()):
    if _name.startswith("test_") and callable(_function):
        setattr(NumericBacklogTests, _name, lambda self, f=_function: f())

del _name, _function

if __name__ == "__main__":
    unittest.main()
