"""Replacement cost contract: list/resolve never generate; verify is explicit."""
import copy
import pytest
from application.model_detection import inspect_openai_compatible
from model_catalog import resolve_capabilities
from model_contract import CapabilityContract
from model_provider import ModelProviderError
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
    monkeypatch.setattr('model_provider.create_provider',lambda *a,**k:pytest.fail('no SDK on local resolution'))
    result=settings_env.service.detect_profile(name='New',model='not-in-catalog',base_url='https://new.invalid/v1')
    assert result['status']=='resolved' and result['discovery']['generation_requests']==0
    c=CapabilityContract.from_dict(result['discovery']['contract']);c.parameters['temperature'].validate(.73)
    assert not settings_env.path.exists() and not settings_env.writes


def test_http_resolve_and_verify_consent_keep_operator_csrf_boundary(settings_env,tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from application.workbench import WorkbenchService
    from integrations.web.app import create_app
    monkeypatch.setattr('model_provider.create_provider',lambda *a,**k:pytest.fail('No model client allowed'))
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
