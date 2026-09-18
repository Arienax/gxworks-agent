"""Cached evidence is not an allowlist and never causes new paid probes."""
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
    before = copy.deepcopy(p.profile)
    p.profile['generationDefaults'][name] = value
    result = inspect_openai_compatible(p,'tenant-alias')
    p.profile['capabilityContract'] = result['contract']
    assert resolve_request(p.profile,{},api_key=p.api_key).options[name] == value
    assert endpoint.calls == [] and endpoint.options == []
    assert not result['contract']['parameters'][name].get('evidence')
    assert before['generationDefaults'] == {}


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
