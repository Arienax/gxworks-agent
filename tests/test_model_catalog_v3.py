"""Data-driven domains, migration, exact matching, and no paid discovery."""
import copy
import json
import pytest
from model_contract import CapabilityContract, ParameterDescriptor, UserModelSettings, contract_scope
from model_catalog import ModelCatalog, resolve_capabilities
from model_request_policy import resolve_request
from test_model_capabilities import profile


def legacy_sample_profile():
    p=profile(generationDefaults={'temperature':.5,'reasoning_effort':'high'})
    params={
        'temperature': {'type':'number','status':'fixed','source':'probe','values':[1],
            'requires':{'reasoning_effort':['high']},'scan':'complete'},
        'reasoning_effort': {'type':'enum','status':'supported','source':'probe','values':['high'],'scan':'partial'}}
    # Reproduce v2's on-disk identity before its descriptors get normalized.
    parsed={k:ParameterDescriptor.from_dict(k,v) for k,v in params.items()}
    scope=contract_scope(p,parsed,api_key='key')
    p['capabilityContract']={'schema_version':2,'scope':scope,'parameters':params,'capabilities':{},
        'constraints':{'temperature':{'requires':{'reasoning_effort':['high']}}}}
    p['userModelSettings']={'scope':scope,'parameters':{'temperature':{'mode':'value','value':.5},
        'reasoning_effort':{'mode':'value','value':'high'}}}
    return p


def test_v2_probe_sample_and_inferred_fixed_are_only_evidence_not_domain():
    p=legacy_sample_profile(); before=copy.deepcopy(p)
    c=CapabilityContract.from_dict(p['capabilityContract'])
    d=c.parameters['temperature']
    assert c.schema_version==3 and d.status=='accepted' and d.values is None
    assert not d.constraints.requires and not c.constraints
    assert d.evidence['accepted_values']==[1] and d.evidence['scan']=='complete'
    d.validate(.733)
    p['userModelSettings']['parameters']['temperature']['value']=.733
    p['userModelSettings']['parameters']['reasoning_effort']['value']='a-new-mode'
    assert resolve_request(p,api_key='key').options['temperature']==.733
    assert before['capabilityContract']['schema_version']==2


def test_v2_authoritative_metadata_and_manual_bounds_remain_hard():
    for source in ('metadata','manual'):
        d=ParameterDescriptor.from_dict('temperature',{'type':'number','status':'supported','source':source,
            'minimum':0,'maximum':1,'step':.1})
        d.validate(.7)
        with pytest.raises(ValueError):d.validate(.73)
        assert d.to_dict()['domain']['enforcement']=='hard'


def test_ui_precision_is_not_a_wire_grid():
    d=ParameterDescriptor.from_dict('temperature',{'type':'number','status':'supported','source':'catalog',
        'domain':{'minimum':0,'maximum':2,'source':'catalog','enforcement':'hard'},'ui_hint':{'step':.01}})
    for value in (.73,.733,0,2):d.validate(value)
    with pytest.raises(ValueError):d.validate(2.0001)
    assert 'step' not in d.to_dict()['domain']


def test_multiple_of_is_anchored_at_zero_not_minimum():
    d=ParameterDescriptor.from_dict('knob',{'type':'number','status':'supported','source':'manual',
        'domain':{'minimum':.1,'maximum':1,'multiple_of':.2,'source':'manual'}})
    d.validate(.2)
    with pytest.raises(ValueError):d.validate(.3)
    assert ParameterDescriptor.from_dict('knob',d.to_dict()).zero_anchored


@pytest.mark.parametrize('bad',[True,'0.7',float('nan'),float('inf'),10**400,{},[]])
def test_generic_float_still_enforces_portable_safe_type(bad):
    d=CapabilityContract.from_dict(resolve_capabilities(profile())['contract']).parameters['temperature']
    with pytest.raises(ValueError):d.validate(bad)


@pytest.mark.parametrize('source',['probe','observation','generic'])
def test_evidence_and_hint_sources_cannot_claim_hard_domains(source):
    with pytest.raises(ValueError):ParameterDescriptor.from_dict('n',{'type':'number','status':'unknown','source':source,
        'domain':{'minimum':0,'maximum':1,'source':source,'enforcement':'hard'}})


def test_catalog_all_entries_validate_without_network():
    catalog=ModelCatalog()
    assert len(catalog.entries)>=7
    for item in catalog.entries.values():
        assert item.get('references') or item['id']=='generic-openai-compatible'
        for endpoint in item.get('match',{}).get('endpoints',[]):
            for model in item['match']['models']:
                result=resolve_capabilities(profile(baseUrl=endpoint,model=model),catalog=catalog)
                assert result['matched'] and result['generation_requests']==0
                CapabilityContract.from_dict(result['contract'])


@pytest.mark.parametrize('endpoint',['https://api.openai.com.evil.invalid/v1','https://proxy.invalid/v1',
    'http://api.openai.com/v1','https://api.openai.com/v2','https://api.openai.com:8443/v1'])
def test_same_model_name_never_matches_wrong_endpoint(endpoint):
    result=resolve_capabilities(profile(baseUrl=endpoint,model='gpt-4.1'))
    assert not result['matched'] and result['contract']['parameters']['temperature']['source']=='generic'


def test_exact_matching_normalizes_hostname_port_and_trailing_slash():
    result=resolve_capabilities(profile(baseUrl='https://API.OPENAI.COM:443/v1/',model='gpt-4.1'))
    assert result['matched']
    d=CapabilityContract.from_dict(result['contract']).parameters['temperature']
    d.validate(.733)


def test_unknown_model_never_inherits_family_by_guessing():
    result=resolve_capabilities(profile(baseUrl='https://api.openai.com/v1',model='gpt-4.1-future-unknown'))
    assert not result['matched']


def test_manual_override_is_scoped_complete_descriptor_and_new_parameter_works():
    manual={'parameters':{'my_flag':{'type':'boolean','status':'supported'},
        'budget':{'type':'integer','status':'supported','domain':{'minimum':0,'maximum':32768},
            'wire_location':'extra_body','wire_path':['thinking','budget_tokens']}}}
    p=profile(capabilityOverrides=manual)
    result=resolve_capabilities(p,api_key='key');p['capabilityContract']=result['contract']
    p['userModelSettings']={'scope':result['contract']['scope'],'parameters':{
        'my_flag':{'mode':'value','value':False},'budget':{'mode':'value','value':8192}}}
    options=resolve_request(p,api_key='key').options
    assert options['my_flag'] is False and options['extra_body']['thinking']['budget_tokens']==8192
    changed=copy.deepcopy(p);changed['capabilityOverrides']['parameters']['my_flag']['status']='unsupported'
    with pytest.raises(ValueError):resolve_request(changed,api_key='key')


@pytest.mark.parametrize('wire',[['messages'],['tools'],['api_key'],['extra_headers'],['constructor'],['foo','password']])
def test_manual_descriptors_cannot_own_protocol_or_credential_fields(wire):
    with pytest.raises(ValueError):resolve_capabilities(profile(capabilityOverrides={'parameters':{
        'unsafe':{'type':'string','status':'unknown','wire_path':wire}}}))


def test_bad_metadata_does_not_break_fallback_or_create_dangling_requirements():
    result=resolve_capabilities(profile(),metadata={'parameters':{
        'bad':{'type':'number','requires':{'missing_reference':[True]}},
        'broken':{'type':'integer','wire_path':['messages']}}})
    c=CapabilityContract.from_dict(result['contract'])
    assert 'broken' not in c.parameters and c.parameters['bad'].status=='unknown'
    assert c.parameters['bad'].constraints.requires=={}


def test_catalog_cycle_is_rejected_before_use(tmp_path):
    for a,b in [('a','b'),('b','a')]:
        (tmp_path/(a+'.json')).write_text(json.dumps({'id':a,'schema_version':1,'extends':b}))
    with pytest.raises(ValueError,match='inheritance'):ModelCatalog(tmp_path)


def test_explicit_omission_wins_over_workflow_without_mutating_declaration():
    p=profile();p['capabilityContract']=resolve_capabilities(p,api_key='key')['contract']
    p['userModelSettings']={'scope':p['capabilityContract']['scope'],'parameters':{'reasoning_effort':{'mode':'omit'},'temperature':{'mode':'value','value':.733}}}
    before=copy.deepcopy(p)
    result=resolve_request(p,{'reasoning_effort':'high','temperature':1},api_key='key')
    assert 'reasoning_effort' not in result.options and result.options['temperature']==.733
    assert p==before

@pytest.mark.parametrize('hint',[{'step':None},{'minimum':None},{'maximum':None},{'step':True}])
def test_malformed_hint_is_rejected_as_validation_error(hint):
    with pytest.raises(ValueError):
        ParameterDescriptor.from_dict('temperature',{'type':'number','status':'unknown','source':'generic','ui_hint':hint})

def test_endpoint_metadata_cannot_manufacture_local_capability_evidence():
    from model_contract import metadata_contract_parts
    _, caps, _ = metadata_contract_parts({'capabilities': {'tools': {
        'status':'supported','evidence':{'accepted_values':[True]}}}})
    assert caps['tools'].source=='metadata' and not caps['tools'].evidence

def test_profile_normalization_does_not_freeze_runtime_observations_past_ttl():
    from model_contract import normalize_contract
    c=resolve_capabilities(profile())['contract']
    c['parameters']['temperature']['evidence']={'accepted_values':[1], 'observations':[
        {'outcome':'accepted','value':.733,'context':'0'*64,'at':1,'source':'observation'}]}
    normalized=normalize_contract(c)
    assert normalized['parameters']['temperature']['evidence']=={'accepted_values':[1]}
    assert c['parameters']['temperature']['evidence']['observations']
