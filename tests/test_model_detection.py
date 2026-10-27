from application.model_detection import inspect_openai_compatible
from model_runtime.provider import TextDelta, ToolCallEnd
from agent_runtime.messages import ToolCall


class _Provider:
    profile = {"baseUrl": "https://test.invalid/v1"}

    def __init__(self):
        self.stream_calls = 0

    def list_models(self, *, timeout=None):
        assert 0 < timeout <= 5.0
        return ("model-b", "model-a", "model-b")

    def stream(self, request):
        self.stream_calls += 1
        assert 0 < request.timeout <= 5.0
        assert request.max_retries == 0
        if request.tools:
            yield ToolCallEnd(ToolCall("probe-1", "capability_probe", '{"value":"ok"}'))
        else:
            yield TextDelta('{"probe":true}')


def test_discovery_lists_models_without_guessing_or_probing_first_result():
    provider = _Provider()
    result = inspect_openai_compatible(
        provider,
        "__discover__",
        {"reasoning": True, "tools": False, "structured_output": False},
    )
    assert result["models"] == ["model-a", "model-b"]
    assert result["recommended_model"] is None
    assert result["selected_model_available"] is False
    assert "contract" not in result
    assert provider.stream_calls == 0


def test_explicit_selected_model_resolves_without_network_or_generating():
    provider = _Provider()
    result = inspect_openai_compatible(provider, "model-b")
    assert result["recommended_model"] == "model-b"
    assert result["contract"]["scope"]["model"] == "model-b"
    assert result["contract"]["capabilities"]["tools"]["status"] == "unknown"
    assert provider.stream_calls == 0 and result["generation_requests"] == 0


# Cached capability evidence
import copy
import pytest
from application.model_detection import inspect_openai_compatible, list_metadata
from model_runtime.catalog import resolve_capabilities
from model_runtime.request_policy import resolve_request
from test_model_capabilities import Endpoint, provider, profile


@pytest.mark.parametrize(('name','value'), [('reasoning_effort','high'),('temperature',.73)])
def test_changed_explicit_value_needs_no_new_probe_or_domain_expansion(name,value):
    endpoint = Endpoint()
    p = provider(endpoint)
    p.profile['capabilityContract'] = inspect_openai_compatible(p,'tenant-alias')['contract']
    p.profile['userModelSettings'] = {
        'scope': p.profile['capabilityContract']['scope'],
        'parameters': {name: {'mode':'value','value':value}},
    }
    before = copy.deepcopy(p.profile)
    result = inspect_openai_compatible(p,'tenant-alias')
    p.profile['capabilityContract'] = result['contract']
    assert resolve_request(p.profile,{},api_key=p.api_key).options[name] == value
    assert endpoint.calls == [] and endpoint.options == []
    assert not result['contract']['parameters'][name].get('evidence')
    assert p.profile['userModelSettings'] == before['userModelSettings']


def test_metadata_cache_is_scoped_to_exact_endpoint_and_credentials():
    endpoint = Endpoint(metadata=[{'id':'tenant-alias','parameters':{'knob':{'type':'boolean'}}}])
    p = provider(endpoint)
    list_metadata(p)
    assert 'knob' in inspect_openai_compatible(p,'tenant-alias')['contract']['parameters']
    for change in ({'api_key':'another-key'},{'baseUrl':'https://other.invalid/v1'}):
        other = provider(Endpoint())
        if 'api_key' in change:other.api_key=change['api_key']
        else:other.profile.update(change)
        assert 'knob' not in inspect_openai_compatible(other,'tenant-alias')['contract']['parameters']
        assert other._client.calls == [] and other._client.options == []


def test_expired_metadata_cache_falls_back_locally_without_refetch(monkeypatch):
    import application.model_detection as discovery
    endpoint=Endpoint(metadata=[{'id':'tenant-alias','parameters':{'knob':{'type':'boolean'}}}])
    p=provider(endpoint);list_metadata(p)
    with discovery._lock:
        key=next(iter(discovery._cache));stamp,data=discovery._cache[key]
        discovery._cache[key]=(stamp-1000,data)
    endpoint.list_error=RuntimeError('network must not be used')
    result=inspect_openai_compatible(p,'tenant-alias')
    assert 'knob' not in result['contract']['parameters'] and endpoint.calls==[]


def test_persisted_metadata_domain_survives_offline_resolve_without_becoming_probe_values():
    p=profile()
    raw=resolve_capabilities(p,metadata={'parameters':{'temperature':{'type':'number','minimum':0,'maximum':1}}},api_key='key')['contract']
    p['capabilityContract']=raw
    resolved=resolve_capabilities(p,api_key='key')['contract']['parameters']['temperature']
    assert resolved['domain']['maximum']==1 and resolved['domain']['source']=='metadata'


@pytest.mark.parametrize('change',[{'baseUrl':'https://other.invalid/v1'},{'model':'alias-other'},
    {'requestOverrides':{'extra_body':{'deployment_mode':'other'}}}])
def test_old_declared_domains_are_not_reused_for_different_scope(change):
    p=profile()
    p['capabilityContract']=resolve_capabilities(p,metadata={'parameters':{'temperature':{'type':'number','maximum':1}}},api_key='key')['contract']
    p.update(change)
    assert resolve_capabilities(p,api_key='key')['contract']['parameters']['temperature']['source']=='generic'


# Discovery-stage and explicit-verification boundaries
import copy
import pytest
from application.model_detection import inspect_openai_compatible
from model_runtime.catalog import resolve_capabilities
from model_runtime.contract import CapabilityContract
from model_runtime.provider import ModelProviderError
from test_model_capabilities import Endpoint, provider, profile, Rejection
from test_application_settings import settings_env


@pytest.mark.parametrize('mode',['quick','resolve'])
@pytest.mark.parametrize('refresh',[False,True])
def test_read_only_resolve_has_zero_generation_and_zero_remote_requests(mode,refresh):
    endpoint=Endpoint(list_error=RuntimeError('No network'))
    p=provider(endpoint)
    before=copy.deepcopy(p.profile)
    result=inspect_openai_compatible(p,'tenant-alias',mode=mode,refresh=refresh)
    assert result['mode']=='resolve' and result['generation_requests']==0
    assert endpoint.calls==[] and endpoint.options==[] and p.profile==before


def test_deep_scan_requires_new_explicit_command_and_cannot_spend_silently():
    p=provider(Endpoint())
    with pytest.raises(ValueError,match='Batch scanning'):
        inspect_openai_compatible(p,'tenant-alias',mode='deep')
    assert p._client.calls==[] and p._client.options==[]


def test_list_does_not_choose_model_or_generate_or_overwrite_a_contract():
    p=provider(Endpoint());before=copy.deepcopy(p.profile)
    result=inspect_openai_compatible(p,'tenant-alias',mode='list')
    assert result['models']==['other-model','tenant-alias'] and result['recommended_model'] is None
    assert 'contract' not in result and p.profile==before and p._client.calls==[]
    assert len(p._client.options)==1 and p._client.options[0]['max_retries']==0


@pytest.mark.parametrize('status',[401,403,429,500])
def test_listing_errors_do_not_fallback_to_paid_generation(status):
    p=provider(Endpoint(list_error=Rejection('models',status=status)))
    with pytest.raises(ModelProviderError):inspect_openai_compatible(p,'tenant-alias',mode='list')
    assert p._client.calls==[]


def test_missing_key_does_not_block_offline_editable_contract(settings_env,monkeypatch):
    monkeypatch.setattr('model_runtime.provider.create_provider',lambda *a,**k:pytest.fail('no SDK on local resolution'))
    result=settings_env.service.detect_profile(name='New',model='not-in-catalog',base_url='https://new.invalid/v1')
    assert result['status']=='resolved' and result['discovery']['generation_requests']==0
    c=CapabilityContract.from_dict(result['discovery']['contract']);c.parameters['temperature'].validate(.73)
    assert not settings_env.path.exists() and not settings_env.writes


def test_http_resolve_and_verify_consent_keep_operator_csrf_boundary(settings_env,tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from application.workbench import WorkbenchService
    from integrations.web.app import create_app
    monkeypatch.setattr('model_runtime.provider.create_provider',lambda *a,**k:pytest.fail('No model client allowed'))
    origin='http://127.0.0.1:8765'
    service=WorkbenchService(tmp_path/'workspace',tmp_path/'state',settings=settings_env.service)
    app=create_app(service.store.base_dir,service=service,origin=origin,operator_token='operator')
    draft={'name':'Draft','base_url':'https://unknown.invalid/v1','model':'unknown'}
    with TestClient(app,base_url=origin) as client:
        login=client.post('/api/session',json={'token':'operator'},headers={'Origin':origin}).json()
        headers={'Origin':origin,'X-CSRF-Token':login['csrf']}
        assert client.post('/api/settings/resolve',json={'profile':draft},headers={'Origin':origin}).status_code==403
        resolved=client.post('/api/settings/resolve',json={'profile':draft},headers=headers)
        assert resolved.status_code==200 and resolved.json()['status']=='resolved'
        assert resolved.json()['discovery']['generation_requests']==0
        # Backwards quick requests resolve; backwards deep requests are refused.
        assert client.post('/api/settings/detect',json={'profile':draft,'mode':'quick'},headers=headers).json()['status']=='resolved'
        assert client.post('/api/settings/detect',json={'profile':draft,'mode':'deep'},headers=headers).status_code in {400,422}
        result=client.post('/api/settings/verify',json={'profile':draft,'target':'temperature','kind':'parameter','value':.73,'consent':False},headers=headers)
        assert result.status_code in {400,422}
        assert client.post('/api/settings/verify',json={'profile':draft,'target':'chat','kind':'chat','consent':'yes'},headers=headers).status_code==422
    assert not settings_env.path.exists()
