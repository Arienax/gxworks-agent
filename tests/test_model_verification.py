"""The actual provider adapter with a synthetic SDK transport; no external API."""
import copy
import pytest
from model_runtime.catalog import resolve_capabilities
from model_runtime.contract import CapabilityContract
from model_runtime.provider import ModelRequest, UserMessage, ModelProviderError, TextDelta
from model_runtime.observations import ObservationStore, request_observer
from model_runtime.verification import verify_one
from test_model_capabilities import Endpoint, Rejection, provider
from test_application_settings import settings_env


class WireEndpoint(Endpoint):
    def create(self,**params):
        payload=super().create(**params)
        if not params.get('stream'):return payload
        message=payload['choices'][0]['message']
        return iter([{'choices':[{'delta':message,'finish_reason':'stop'}],
            'usage':{'prompt_tokens':5,'completion_tokens':2,'total_tokens':7}}])


def scoped_provider(endpoint=None,**changes):
    p=provider(endpoint or WireEndpoint(ignore=True),**changes)
    p.profile['capabilityContract']=resolve_capabilities(p.profile,api_key=p.api_key)['contract']
    p.profile['userModelSettings']={'scope':p.profile['capabilityContract']['scope'],'parameters':{}}
    return p


@pytest.mark.parametrize('consent',[False,None,'true',1])
def test_verification_has_no_pre_resolve_or_cleared_contract_bypass():
    import inspect
    import model_runtime.verification as verification

    source = inspect.getsource(verification.verify_one)
    helper = inspect.getsource(verification._probe_runtime)

    assert "resolve_request(" not in source
    assert "resolve_request(" not in helper
    assert "'capabilityContract':{}" not in source.replace(" ", "")
    assert '"capabilityContract":{}' not in source.replace(" ", "")
    assert "materialize_runtime_profile(" in helper
    assert "_runtime_profiles" in source


def test_consent_is_required_before_any_client_request(consent):
    p=scoped_provider()
    with pytest.raises(ValueError):verify_one(p,p.profile['capabilityContract'],'temperature',value=.73,consent=consent)
    assert p._client.calls==[] and p._client.options==[]


@pytest.mark.parametrize('target,kind,value',[('temperature','parameter',.73),('reasoning_effort','parameter','high'),
    ('tools','capability',None),('structured_output','capability','json_object'),('chat','chat',None)])
def test_verification_uses_one_actual_request_no_preflight_negative_or_fallback(target,kind,value,tmp_path):
    p=scoped_provider();before=copy.deepcopy(p.profile)
    store=ObservationStore(tmp_path/'observations.sqlite')
    result=verify_one(p,p.profile['capabilityContract'],target,kind=kind,value=value,consent=True,store=store)
    assert result['generation_requests']==1 and result['outcome'] in {'accepted','observed'}
    assert len(p._client.calls)==1 and len(p._client.options)==1
    assert p._client.options[0]=={'timeout':8.0,'max_retries':0}
    wire=p._client.calls[0]
    assert wire['max_completion_tokens']==64 and len(wire['messages'])==1
    assert '工程' not in str(wire) and 'secret' not in str(wire)
    if kind=='parameter':assert wire[target]==value
    assert p.profile==before
    assert result['input_tokens']==5 and result['output_tokens']==2


def test_named_rejection_records_only_target_and_never_retries(tmp_path):
    p=scoped_provider(WireEndpoint(unsupported=True))
    store=ObservationStore(tmp_path/'observations.sqlite')
    with pytest.raises(ModelProviderError):verify_one(p,p.profile['capabilityContract'],'temperature',value=.73,consent=True,store=store)
    assert len(p._client.calls)==1
    c=store.decorate(CapabilityContract.from_dict(p.profile['capabilityContract']))
    entries=c.parameters['temperature'].evidence['observations']
    assert entries[0]['outcome']=='rejected' and entries[0]['value']==.73
    assert c.parameters['temperature'].status=='unknown'
    assert b'secret-must-not-leak' not in store.path.read_bytes()


def test_unsupported_output_limit_does_not_generate_again_with_another_field():
    p=scoped_provider(WireEndpoint(legacy_limit=True))
    with pytest.raises(ModelProviderError):verify_one(p,p.profile['capabilityContract'],'temperature',value=.5,consent=True)
    assert len(p._client.calls)==1 and 'max_tokens' not in p._client.calls[0]


def test_private_transport_error_is_not_published_by_settings(settings_env,monkeypatch):
    endpoint=WireEndpoint(unsupported=True)
    from model_runtime.provider import OpenAICompatibleProvider
    monkeypatch.setattr('model_runtime.provider.create_provider',lambda p,k:OpenAICompatibleProvider(p,k,client=endpoint))
    result=settings_env.service.verify_profile(profile={'name':'Draft','base_url':'https://unknown.invalid/v1','model':'tenant-alias','api_key':'test-key'},
        target='temperature',kind='parameter',value=.73,consent=True)
    assert result['status']=='failed' and len(endpoint.calls)==1
    assert 'secret-must-not-leak' not in str(result) and 'test-key' not in str(result)


@pytest.mark.parametrize('options',[{'max_tokens':100000,'n':8,'timeout':10000},
    {'extra_body':{'max_tokens':100000,'max_completion_tokens':100000,'n':8,'timeout':10000}}])
def test_single_verification_budget_cannot_be_shadowed_by_advanced_options(options):
    p=scoped_provider(requestOverrides=options)
    verify_one(p,p.profile['capabilityContract'],'chat',kind='chat',consent=True)
    wire=p._client.calls[0]
    assert wire['max_completion_tokens']==64 and 'max_tokens' not in wire and 'n' not in wire
    assert not {'max_tokens','max_completion_tokens','n','timeout'}.intersection(wire.get('extra_body',{}))
    assert 'timeout' not in wire


@pytest.mark.parametrize('target,kind,value',[('max_completion_tokens','parameter',1000),('vision','capability',None),
    ('structured_output','capability','json_schema'),('other','chat',None)])
def test_unsupported_verification_target_or_budget_is_rejected_without_cost(target,kind,value):
    p=scoped_provider()
    with pytest.raises(ValueError):verify_one(p,p.profile['capabilityContract'],target,kind=kind,value=value,consent=True)
    assert p._client.calls==[]


def test_verification_keeps_materialized_legacy_transport_capabilities():
    p = scoped_provider(
        capabilities={
            "thinking_required": True,
            "tool_stream": True,
        }
    )
    result = verify_one(
        p,
        p.profile["capabilityContract"],
        "tools",
        kind="capability",
        consent=True,
    )

    assert result["outcome"] == "observed"
    wire = p._client.calls[0]
    assert wire["extra_body"]["thinking"]["type"] == "enabled"
    assert wire["extra_body"]["tool_stream"] is True
