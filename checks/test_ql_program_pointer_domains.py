"""Sourced CPU P identities are neither data words nor project allocation."""
import copy
import pytest


@pytest.mark.parametrize('model,maximum,manual',[
    ('Q00',299,'sh080808engm'),('Q00J',299,'sh080808engm'),('Q01',299,'sh080808engm'),
    ('Q00U',511,'sh080807engaf'),('Q00UJ',511,'sh080807engaf'),('Q01U',511,'sh080807engaf'),
    ('Q50UDEH',8191,'sh080807engaf'),('Q100UDEH',8191,'sh080807engaf'),
    ('Q03UDV',4095,'sh080807engaf'),('Q02H',4095,'sh080808engm'),
    ('L02',4095,'sh080889engz'),('L26',4095,'sh080889engz')])
def test_ql_pointer_domains_use_exact_model_sources_not_memory(model,maximum,manual):
    from plc.device_profiles import program_pointer_domain,DEVICE_LIMITS,device_address_rules
    domain=program_pointer_domain(model)
    assert domain['minimum_number']==0 and domain['maximum_number']==maximum and domain['radix']==10
    assert domain['kind']=='program_pointer_identity' and domain['allocation_verified'] is False
    assert all(s['target_model']==model and s['covered_models']==[model] and s['manual_id']==manual for s in domain['sources'])
    assert 'P' not in DEVICE_LIMITS[model] and 'P' not in device_address_rules(model)['families']


def test_ql_pointer_wrong_model_unreviewed_source_and_malformed_domain_are_unknown(monkeypatch):
    import plc.device_profiles as profiles
    original=profiles.SOURCE_DEVICE_PROFILES['Q00']
    for corrupt in ('wrong_model','wrong_manual','conditional_source','candidate','bool_maximum','radix'):
        profile=copy.deepcopy(original);domain=profile['program_pointer_domain']
        if corrupt=='wrong_model':domain['sources'][0].update(target_model='Q03UDV',covered_models=['Q03UDV'])
        elif corrupt=='wrong_manual':domain['sources'][0].update(manual_id='sh080807engaf',revision='AF')
        elif corrupt=='conditional_source':domain['sources'][0]['conditions']=[{'serial_minimum':'99999'}]
        elif corrupt=='candidate':domain['status']='candidate_evidence'
        elif corrupt=='bool_maximum':domain['maximum_number']=True
        else:domain['radix']=16
        with monkeypatch.context() as m:
            m.setitem(profiles.SOURCE_DEVICE_PROFILES,'Q00',profile)
            assert profiles.program_pointer_domain('Q00') is None,corrupt
    assert profiles.program_pointer_domain('Q02/Q02H')['maximum_number']==4095
    for model in ('FX3U','Q999','QJ72LP25/QJ72BR15(RemoteI/O)'):
        assert profiles.program_pointer_domain(model) is None


@pytest.mark.parametrize('model,maximum',[('Q00',299),('Q00UJ',511),('Q50UDEH',8191),('Q03UDV',4095),('L02',4095),('L26',4095)])
def test_break_public_APP_and_ordered_reject_real_CPU_P_number_overflow(model,maximum):
    from checks.test_break_native_contract import app,ordered
    for op in ('BREAK','BREAKP'):
        for check in (app,ordered):
            check(op,['D0','P0'],model);check(op,['D0','P'+str(maximum)],model)
            with pytest.raises(ValueError,match='outside the source-verified CPU domain'):
                check(op,['D0','P'+str(maximum+1)],model)


def test_legacy_QL_CALL_numeric_preflight_does_not_grant_a_signature(monkeypatch):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.validation import _validate_program_pointer_number_domain
    from checks.test_break_native_contract import app,ordered
    for model,maximum in [('Q00',299),('Q00UJ',511),('Q50UDEH',8191),('L02',4095)]:
        spec=registry.resolve('CALL',cpu=model,operand_count=1)
        assert _validate_program_pointer_number_domain(spec,['P'+str(maximum)],0,'CALL.Pn',model) is True
        for check in (app,ordered):
            with pytest.raises(ValueError,match='outside the source-verified CPU domain'):
                check('CALL',['P'+str(maximum+1)],model)
            # Preserve the legacy acceptance without claiming its empty
            # signature is now complete. Removing the numeric profile leaves
            # this existing P1 behavior exactly as it was.
            check('CALL',['P1'],model)
            with monkeypatch.context() as m:
                m.setattr('plc.device_profiles.program_pointer_domain',lambda _model:None)
                check('CALL',['P1'],model)
            assert spec.native_operand_order==() and spec.operands==()
    assert _validate_program_pointer_number_domain(registry.resolve('BKAND',cpu='Q00',operand_count=4),
        ['D0','D10','D20','P300'],3,'BKAND.n','Q00') is False


def test_all_53_QL_domains_match_the_reviewed_model_source_plan():
    import json
    from pathlib import Path
    from plc.device_profiles import program_pointer_domain
    plan=json.loads(Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-call-native-contract-repair/pointer-limits-preparation.json').read_text(encoding='utf-8'))
    checked=set()
    for group in plan['groups']:
        maximum=group.get('maximum',group.get('explicit_pointer_maximum'))
        manual,pages=next(iter(group['sources'].items()))
        for model in group['models']:
            domain=program_pointer_domain(model)
            assert domain is not None and domain['maximum_number']==maximum,model
            assert domain['sources'][0]['manual_id']==manual
            assert domain['sources'][0]['pdf_page']==pages[0]
            assert domain['sources'][0]['target_model']==model
            checked.add(model)
    assert len(checked)==53


def test_QL_domain_unknown_source_does_not_use_a_nonempty_mapping(monkeypatch):
    import plc.device_profiles as profiles
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.validation import _validate_program_pointer_number_domain
    profile=copy.deepcopy(profiles.SOURCE_DEVICE_PROFILES['Q00'])
    profile['program_pointer_domain']['sources']=[]
    monkeypatch.setitem(profiles.SOURCE_DEVICE_PROFILES,'Q00',profile)
    assert _validate_program_pointer_number_domain(registry.resolve('BREAK',cpu='Q00',operand_count=2),
        ['D0','P300'],1,'BREAK.Pn','Q00') is False


def test_QL_pointer_projection_keeps_allocation_false_L_capacity_and_writer_boundary():
    from plc.device_profiles import device_address_rules,program_pointer_domain
    from shared.model_context import technical_value
    from integrations.web.responses import GenerationInput
    from gxw.token_encoder import encode_label
    from gxw.models import GXWFormatError
    rules=device_address_rules('L26')
    delivered=technical_value(rules)
    assert delivered['program_pointer_domain']['maximum_number']==4095
    assert delivered['program_pointer_domain']['allocation_verified'] is False
    capacity=delivered['program_pointer_auto_assignment']
    assert capacity['pointer_points']==32768 and capacity['literal_domain_extension_established'] is False
    assert capacity['conditions']['serial_first_five_minimum']=='16042'
    # This is an existing ExtensibleResource/open object, not a new HTTP API.
    http=GenerationInput.model_validate({'device_address_rules':delivered}).model_dump(mode='json')
    assert http['device_address_rules']==delivered
    assert program_pointer_domain('Q50UDEH')['maximum_number']==8191
    with pytest.raises(GXWFormatError):
        encode_label('P4096',profile='q03udv',plc_model='Q50UDEH',text_encoding='cp932')
