"""Quick checks must validate changed draft samples, not just reuse a model scope."""
import copy

import pytest

from application.model_detection import inspect_openai_compatible
from model_contract import member
from model_request_policy import resolve_request
from test_model_capabilities import Endpoint, provider


@pytest.mark.parametrize(('name', 'value'), [
    ('reasoning_effort', 'high'),
    ('temperature', .5),
])
def test_quick_cache_extends_samples_for_changed_explicit_draft(name, value):
    first = inspect_openai_compatible(provider(Endpoint()), 'tenant-alias')
    endpoint = Endpoint()
    p = provider(endpoint, capabilityContract=first['contract'],
                 generationDefaults={name: value})
    before = copy.deepcopy(p.profile)
    result = inspect_openai_compatible(p, 'tenant-alias', mode='quick')
    observed = result['contract']['parameters'][name]
    assert member(value, observed['values'])
    assert observed['scan'] == 'partial'
    assert name not in result['parameters_reused']
    assert any(call.get(name) == value for call in endpoint.calls)
    assert not any(call.get('tools') or call.get('response_format') for call in endpoint.calls)
    assert len(endpoint.calls) <= 5
    if name == 'reasoning_effort':
        assert observed['values'] == ['low', 'high']
        assert result['contract']['parameters']['temperature']['requires'] == {
            'reasoning_effort': ['high']}
    else:
        assert observed['values'] == [.5, 1.0]
    p.profile['capabilityContract'] = result['contract']
    assert resolve_request(p.profile, {}, api_key=p.api_key).options[name] == value
    p.profile['capabilityContract'] = before['capabilityContract']
    assert p.profile == before


def test_new_quick_sample_timeout_preserves_old_positive_evidence():
    from model_provider import ModelProviderError

    first = inspect_openai_compatible(provider(Endpoint()), 'tenant-alias')

    class Interrupted(Endpoint):
        def create(self, **params):
            if params.get('temperature') == .5:
                self.calls.append(params)
                raise ModelProviderError('private-provider-error', code='timeout')
            return super().create(**params)

    endpoint = Interrupted()
    result = inspect_openai_compatible(provider(endpoint, capabilityContract=first['contract'],
        generationDefaults={'temperature': .5}), 'tenant-alias')
    temp = result['contract']['parameters']['temperature']
    assert any(call.get('temperature') == .5 for call in endpoint.calls)
    assert temp['values'] == [1.0] and temp['scan'] == 'partial'
    assert temp['status'] == 'supported'
    assert 'private-provider-error' not in str(result)


def test_capability_probe_reads_remaining_budget_once_before_dispatch():
    from model_probes import CapabilityProbe, ProbeContext

    budgets = iter([.01, 0.0])
    timeouts = []

    def check(_provider, _model, timeout):
        timeouts.append(timeout)
        return True

    probe = CapabilityProbe('test_capability', check)
    result = probe.run(ProbeContext(object(), 'model', lambda: next(budgets)))
    assert result.status == 'supported' and timeouts == [.01]
