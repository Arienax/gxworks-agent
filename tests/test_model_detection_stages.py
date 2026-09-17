"""Two-stage discovery with synthetic transports; never real API keys or calls."""
import copy
import json
import threading
import time
from dataclasses import replace

import pytest

from application import model_detection as detection
from model_contract import CapabilityContract, ParameterDescriptor, UserModelSettings
from model_provider import ModelProviderError, OpenAICompatibleProvider
from test_model_capabilities import Endpoint, Rejection, profile, provider
from test_application_settings import settings_env


def cached_provider(endpoint, result, *, selections=None, **changes):
    return provider(endpoint, capabilityContract=result['contract'],
        userModelSettings={'scope': result['contract']['scope'], 'parameters': selections or {}}, **changes)


def test_default_quick_uses_one_positive_per_parameter_not_all_levels():
    endpoint = Endpoint()
    result = detection.inspect_openai_compatible(provider(endpoint), 'tenant-alias')
    assert result['mode'] == 'quick' and result['partial']
    assert result['contract']['parameters']['reasoning_effort']['values'] == ['low']
    assert result['contract']['parameters']['temperature']['values'] == [1.0]
    assert result['contract']['parameters']['temperature']['status'] == 'supported'
    assert all(d['scan'] == 'partial' for d in result['contract']['parameters'].values())
    assert len(endpoint.calls) == 7  # baseline + 2 negative + 2 positive + 2 capabilities
    assert not any(call.get('reasoning_effort') in ('high', 'xhigh', 'max') for call in endpoint.calls)
    assert all(0 < opt['timeout'] <= 5 and opt['max_retries'] == 0 for opt in endpoint.options)
    assert result['elapsed_ms'] >= 0 and result['budget_seconds'] == 20


def test_deep_extends_quick_and_never_repeats_capability_generations():
    first = detection.inspect_openai_compatible(provider(Endpoint()), 'tenant-alias')
    endpoint = Endpoint()
    result = detection.inspect_openai_compatible(cached_provider(endpoint, first), 'tenant-alias', mode='deep')
    assert not any(c.get('tools') or c.get('response_format') for c in endpoint.calls)
    assert not any(c.get('reasoning_effort') == 'low' or c.get('temperature') == 1 for c in endpoint.calls)
    params = result['contract']['parameters']
    assert params['reasoning_effort']['values'] == ['low', 'high', 'max']
    assert params['temperature']['values'] == [0, .5, 1, 1.5, 2]
    assert all(d['scan'] == 'complete' for d in params.values())
    assert result['contract']['capabilities'] == first['contract']['capabilities']
    assert not result['partial']


def test_quick_after_deep_reuses_full_domain_and_keeps_user_settings():
    first = detection.inspect_openai_compatible(provider(Endpoint()), 'tenant-alias')
    deep = detection.inspect_openai_compatible(cached_provider(Endpoint(), first), 'tenant-alias', mode='deep')
    endpoint = Endpoint()
    p = cached_provider(endpoint, deep, selections={'reasoning_effort': {'mode': 'omit'}})
    before = copy.deepcopy(p.profile)
    quick = detection.inspect_openai_compatible(p, 'tenant-alias')
    assert quick['contract'] == deep['contract']
    assert set(quick['parameters_reused']) == {'reasoning_effort', 'temperature'}
    assert not endpoint.calls  # only listing, never a completion
    assert p.profile == before


def test_quick_reuses_partial_positive_results_until_explicit_deep_or_refresh():
    first = detection.inspect_openai_compatible(provider(Endpoint()), 'tenant-alias')
    endpoint = Endpoint()
    again = detection.inspect_openai_compatible(cached_provider(endpoint, first), 'tenant-alias')
    assert again['contract'] == first['contract'] and not endpoint.calls
    refreshed = detection.inspect_openai_compatible(cached_provider(endpoint, first), 'tenant-alias', refresh=True)
    assert len(endpoint.calls) == 7 and refreshed['partial']


@pytest.mark.parametrize('change', [
    {'baseUrl': 'https://different.invalid/v1'},
    {'requestOverrides': {'extra_body': {'non_parameter_context': True}}},
])
def test_different_endpoint_or_context_never_reuses_cached_checks(change):
    first = detection.inspect_openai_compatible(provider(Endpoint()), 'tenant-alias')
    endpoint = Endpoint()
    result = detection.inspect_openai_compatible(cached_provider(endpoint, first, **change), 'tenant-alias')
    assert not result['parameters_reused'] and len(endpoint.calls) == 7
    assert result['contract']['scope'] != first['contract']['scope']


def test_different_key_or_model_never_reuses_contract():
    first = detection.inspect_openai_compatible(provider(Endpoint()), 'tenant-alias')
    for mutate in ('key', 'model'):
        endpoint = Endpoint()
        p = cached_provider(endpoint, first)
        if mutate == 'key':
            p.api_key = 'rotated-synthetic-key'
        else:
            p.profile['capabilityContract']['scope']['model'] = 'other'
        result = detection.inspect_openai_compatible(p, 'tenant-alias')
        assert not result['parameters_reused'] and len(endpoint.calls) == 7
        assert 'synthetic-key' not in json.dumps(result)


def test_list_mode_with_selected_model_is_list_only():
    endpoint = Endpoint()
    result = detection.inspect_openai_compatible(provider(endpoint), 'tenant-alias', mode='list')
    assert result['mode'] == 'list' and result['recommended_model'] is None
    assert 'contract' not in result and not endpoint.calls
    assert result['selected_model_available']


def test_deep_without_quick_does_not_claim_unprobed_capabilities():
    endpoint = Endpoint()
    result = detection.inspect_openai_compatible(provider(endpoint), 'tenant-alias', mode='deep')
    assert all(d['status'] == 'unknown' for d in result['contract']['capabilities'].values())
    assert not any(c.get('tools') or c.get('response_format') for c in endpoint.calls)


def test_metadata_can_fulfil_quick_check_without_any_generations():
    endpoint = Endpoint(metadata=[{'id': 'tenant-alias', 'parameters': {
        'reasoning_effort': {'enum': ['efficient', 'balanced', 'thorough']},
        'temperature': {'type': 'number', 'minimum': 0, 'maximum': 2, 'multipleOf': .1},
        'unknown_vendor_toggle': {'type': 'boolean'}},
        'capabilities': {'tools': True, 'structured_output': {'status': 'supported', 'modes': ['json_schema']}}}])
    result = detection.inspect_openai_compatible(provider(endpoint), 'tenant-alias')
    assert not endpoint.calls and not result['partial']
    assert result['contract']['parameters']['unknown_vendor_toggle']['type'] == 'boolean'
    assert result['contract']['parameters']['reasoning_effort']['values'] == ['efficient', 'balanced', 'thorough']


def test_one_quick_temperature_sample_is_not_fixed():
    p = provider(Endpoint(temperatures=(1.,)))
    quick = detection.inspect_openai_compatible(p, 'tenant-alias')
    assert quick['contract']['parameters']['temperature']['status'] == 'supported'
    assert quick['contract']['parameters']['temperature']['scan'] == 'partial'
    p.profile['capabilityContract'] = quick['contract']
    deep = detection.inspect_openai_compatible(p, 'tenant-alias', mode='deep')
    assert deep['contract']['parameters']['temperature']['scan'] == 'complete'
    assert deep['contract']['parameters']['temperature']['status'] == 'fixed'


def test_dependent_temperature_is_rescanned_for_changed_effort():
    first = detection.inspect_openai_compatible(provider(Endpoint()), 'tenant-alias')
    endpoint = Endpoint()
    result = detection.inspect_openai_compatible(cached_provider(endpoint, first,
        selections={'reasoning_effort': {'mode': 'value', 'value': 'low'}}), 'tenant-alias', mode='deep')
    temp = result['contract']['parameters']['temperature']
    assert temp['requires'] == {'reasoning_effort': ['low']}
    assert any(c.get('temperature') == 1 for c in endpoint.calls)
    assert all(c.get('reasoning_effort') == 'low' for c in endpoint.calls if 'temperature' in c)


def test_quick_respects_explicit_high_draft_instead_of_substituting_low():
    endpoint = Endpoint()
    result = detection.inspect_openai_compatible(provider(endpoint,
        generationDefaults={'reasoning_effort': 'high', 'temperature': .5}), 'tenant-alias')
    assert result['contract']['parameters']['reasoning_effort']['values'] == ['high']
    assert result['contract']['parameters']['temperature']['values'] == [.5]
    assert result['contract']['parameters']['temperature']['requires'] == {'reasoning_effort': ['high']}


def test_partial_cached_domain_does_not_hide_new_explicit_dependency_value():
    first = detection.inspect_openai_compatible(provider(Endpoint()), 'tenant-alias')
    p = cached_provider(Endpoint(), first, generationDefaults={'reasoning_effort': 'high'})
    result = detection.inspect_openai_compatible(p, 'tenant-alias', mode='deep')
    assert result['contract']['parameters']['temperature']['requires'] == {'reasoning_effort': ['high']}


def test_preflight_timeout_stops_remaining_calls_without_marking_unsupported():
    class Slow(Endpoint):
        def create(self, **params):
            self.calls.append(params)
            raise ModelProviderError('private-timeout-text', code='timeout')
    endpoint = Slow()
    result = detection.inspect_openai_compatible(provider(endpoint), 'tenant-alias')
    assert len(endpoint.calls) == 1 and result['partial']
    assert result['preflight_failed'] and not result['budget_exhausted']
    assert all(d['status'] == 'unknown' for d in result['contract']['capabilities'].values())
    assert all(d['status'] == 'unknown' for d in result['contract']['parameters'].values())
    assert 'private-timeout-text' not in json.dumps(result)


def test_deadline_exhaustion_schedules_no_more_model_calls(monkeypatch):
    monkeypatch.setattr(detection, 'QUICK_BUDGET_SECONDS', .01)
    class SlowList(Endpoint):
        def list(self):
            time.sleep(.02)
            return super().list()
    endpoint = SlowList()
    result = detection.inspect_openai_compatible(provider(endpoint), 'tenant-alias')
    assert result['budget_exhausted'] and result['partial'] and not endpoint.calls


def test_deep_failure_retains_validated_samples_not_a_fake_complete_range():
    first = detection.inspect_openai_compatible(provider(Endpoint()), 'tenant-alias')
    class Interrupted(Endpoint):
        def create(self, **params):
            if params.get('reasoning_effort') == 'minimal':
                self.calls.append(params)
                raise ModelProviderError('private', code='timeout')
            return super().create(**params)
    result = detection.inspect_openai_compatible(cached_provider(Interrupted(), first), 'tenant-alias', mode='deep')
    effort = result['contract']['parameters']['reasoning_effort']
    assert effort['values'] == ['low'] and effort['scan'] == 'partial'
    assert result['partial']


def test_independent_quick_probes_overlap_but_parameter_dependency_stays_ordered():
    class Concurrent(Endpoint):
        def __init__(self):
            super().__init__()
            self.barrier = threading.Barrier(3)
        def create(self, **params):
            # Preflight runs before the three independent lanes. Reaching this
            # barrier proves overlap without relying on noisy stopwatch limits.
            if (params.get('reasoning_effort') == '__gxw_invalid_effort__' or
                params.get('tools') or params.get('response_format')):
                self.barrier.wait(timeout=2)
            if 'temperature' in params:
                assert any(c.get('reasoning_effort') == 'low' for c in self.calls)
            return super().create(**params)
    endpoint = Concurrent()
    result = detection.inspect_openai_compatible(provider(endpoint), 'tenant-alias')
    assert result['contract']['capabilities']['tools']['status'] == 'supported'
    assert result['contract']['capabilities']['structured_output']['status'] == 'supported'
    assert result['contract']['parameters']['temperature']['status'] == 'supported'


@pytest.mark.parametrize('status', [401, 403, 429])
def test_model_generation_auth_errors_do_not_become_unknown_capabilities(status):
    class Denied(Endpoint):
        def create(self, **params):
            self.calls.append(params)
            raise Rejection('private', status=status)
    endpoint = Denied()
    with pytest.raises(ModelProviderError):
        detection.inspect_openai_compatible(provider(endpoint), 'tenant-alias')
    assert len(endpoint.calls) == 1


def test_scan_coverage_roundtrips_but_cannot_be_metadata_claim():
    desc = ParameterDescriptor.from_dict('temperature', {'type': 'number', 'status': 'supported',
        'source': 'probe', 'values': [1], 'scan': 'partial'})
    assert ParameterDescriptor.from_dict('temperature', desc.to_dict()).scan == 'partial'
    with pytest.raises(ValueError):
        ParameterDescriptor.from_dict('temperature', {**desc.to_dict(), 'source': 'metadata'})
    with pytest.raises(ValueError):
        ParameterDescriptor.from_dict('temperature', {**desc.to_dict(), 'scan': 'all_models'})


def test_http_modes_validate_and_deep_is_explicit(settings_env, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from application.workbench import WorkbenchService
    from integrations.web.app import create_app
    import model_provider
    endpoints = []
    def factory(p, k):
        endpoint = Endpoint()
        endpoints.append(endpoint)
        return OpenAICompatibleProvider(p, k, client=endpoint)
    monkeypatch.setattr(model_provider, 'create_provider', factory)
    origin = 'http://127.0.0.1:8765'
    service = WorkbenchService(tmp_path / 'workspace', tmp_path / 'state', settings=settings_env.service)
    app = create_app(service.store.base_dir, service=service, origin=origin, operator_token='operator')
    draft = {'name': 'Draft', 'base_url': 'https://gateway.invalid/custom/v2/',
             'model': 'tenant-alias', 'api_key': 'temporary-key'}
    with TestClient(app, base_url=origin) as client:
        login = client.post('/api/session', json={'token': 'operator'}, headers={'Origin': origin}).json()
        headers = {'Origin': origin, 'X-CSRF-Token': login['csrf']}
        assert client.post('/api/settings/detect', json={'profile': draft, 'mode': 'all'}, headers=headers).status_code == 422
        assert client.post('/api/settings/detect', json={'profile': draft, 'refresh': 'yes'}, headers=headers).status_code == 422
        listing = client.post('/api/settings/detect', json={'profile': draft, 'mode': 'list'}, headers=headers).json()
        assert 'contract' not in listing['discovery'] and not endpoints[-1].calls
        quick = client.post('/api/settings/detect', json={'profile': draft}, headers=headers).json()
        assert quick['discovery']['mode'] == 'quick'
        draft['contract'] = quick['discovery']['contract']
        deep = client.post('/api/settings/detect', json={'profile': draft, 'mode': 'deep'}, headers=headers)
        assert deep.status_code == 200 and deep.json()['discovery']['mode'] == 'deep'
        assert deep.json()['discovery']['contract']['parameters']['reasoning_effort']['values'] == ['low', 'high', 'max']
        assert not any(c.get('tools') or c.get('response_format') for c in endpoints[-1].calls)
        assert 'temporary-key' not in deep.text
    assert not settings_env.path.exists() and not settings_env.writes
