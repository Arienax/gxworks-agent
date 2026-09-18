"""Live acceptance controls verified without a key or network service."""
import json

import httpx
import pytest

from scripts.web_live_acceptance import (
    AcceptanceLimitError, BoundedDeepSeekTransport, DEEPSEEK_URL, RequestBudget, live_app, live_settings,
)


def test_live_key_stays_in_memory_and_real_provider_functions_are_preserved(tmp_path, monkeypatch):
    import storage.config as config_manager
    import storage.credentials as credential_store
    import model_runtime.provider as model_provider

    original_create = model_provider.create_provider
    original_test = model_provider.test_model_profile
    sentinel = "synthetic-key-not-a-real-credential"
    monkeypatch.setattr(credential_store, "read_secret", lambda *_a, **_k: pytest.fail("Host credential read"))
    monkeypatch.setattr(credential_store, "write_secret", lambda *_a, **_k: pytest.fail("Host credential write"))
    with live_settings(tmp_path, sentinel) as (settings, budget):
        assert model_provider.create_provider is original_create
        assert model_provider.test_model_profile is original_test
        config = settings.read_config()
        profile = config_manager.get_model_profile(config)
        assert profile["baseUrl"] == DEEPSEEK_URL
        assert credential_store.read_api_key(profile["credentialTarget"]) == sentinel
        credential_store.write_api_key("another-synthetic-value", profile["credentialTarget"])
        assert config_manager.read_api_key(profile["credentialTarget"]) == "another-synthetic-value"
        assert sentinel not in json.dumps(settings.public_settings())
        assert budget.count == 0
        for path in tmp_path.rglob("*"):
            if path.is_file():
                assert sentinel.encode() not in path.read_bytes()
                assert b"another-synthetic-value" not in path.read_bytes()
        bad = {**profile, "baseUrl": "https://example.invalid"}
        with pytest.raises(AcceptanceLimitError):
            model_provider.create_provider(bad, sentinel)


def test_live_transport_limits_actual_requests_including_fallbacks():
    sent = []
    def receive(request):
        sent.append(request)
        return httpx.Response(200, json={"data": []})
    budget = RequestBudget()
    transport = BoundedDeepSeekTransport(budget, transport=httpx.MockTransport(receive))
    with httpx.Client(transport=transport) as client:
        for _ in range(12):
            assert client.post(DEEPSEEK_URL + "/chat/completions", json={}).status_code == 200
        with pytest.raises(AcceptanceLimitError):
            client.get(DEEPSEEK_URL + "/models")
        with pytest.raises(AcceptanceLimitError):
            client.get("https://example.invalid/models")
    assert len(sent) == 12 and budget.count == 12
    assert all(value == 120 for request in sent for value in request.extensions["timeout"].values())


def test_live_workspace_starts_empty_and_host_devices_are_unavailable(tmp_path):
    from fastapi.testclient import TestClient
    with live_app(tmp_path, "synthetic-test-value") as (app, budget), TestClient(app, base_url="http://127.0.0.1:8877"):
        assert app.state.service.projects.list_projects() == []
        assert not app.state.service.hardware.reader.availability()["available"]
        assert budget.count == 0
