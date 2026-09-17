"""Model configuration acceptance without network, real keys or user files."""
import copy
import json
from types import SimpleNamespace

import pytest

from application.settings import SettingsService


@pytest.fixture
def settings_env(tmp_path, monkeypatch):
    import config_manager
    import credential_store
    import model_provider
    import resource_paths
    config_path = tmp_path / "user" / "config.json"
    template = tmp_path / "config.default.json"
    config = {"language": "zh-CN", "activeModelProfileId": "fake", "modelProfiles": [{
        "id": "fake", "name": "Fake", "adapter": "openai_compatible", "baseUrl": "https://example.invalid/v1",
        "model": "model-a", "credentialTarget": "test-target", "capabilities": {"tools": True},
        "generationDefaults": {"temperature": 0.3}, "requestOverrides": {"extra_body": {"thinking": {"type": "enabled"}}}}]}
    template.write_text(json.dumps(config), encoding="utf-8")
    keys, writes, deletes, calls = {}, [], [], []
    def put(key, target):
        if not str(key).strip():
            raise ValueError("Empty key")
        keys[target] = str(key).strip()
        writes.append((target, key))
    def remove(target):
        keys.pop(target, None)
        deletes.append(target)
    def probe(profile, key):
        calls.append((copy.deepcopy(profile), key))
        return "连接成功，API Key 和服务地址有效。"
    monkeypatch.setattr(config_manager, "get_config_path", lambda: config_path)
    monkeypatch.setattr(resource_paths, "resource_path", lambda name: template)
    monkeypatch.setattr(config_manager, "load_full_config", lambda: pytest.fail("Settings must not invoke desktop migration"))
    monkeypatch.setattr(credential_store, "read_api_key", lambda target: keys.get(target, ""))
    monkeypatch.setattr(credential_store, "write_api_key", put)
    monkeypatch.setattr(credential_store, "delete_api_key", remove)
    monkeypatch.setattr(model_provider, "test_model_profile", probe)
    monkeypatch.setattr(model_provider, "create_provider", lambda profile, key: SimpleNamespace(profile=copy.deepcopy(profile), api_key=key))
    def persist(value):
        config_path.parent.mkdir(exist_ok=True)
        config_path.write_text(json.dumps(value), encoding="utf-8")
    return SimpleNamespace(path=config_path, template=template, config=config, keys=keys, writes=writes,
                           deletes=deletes, calls=calls, persist=persist, service=SettingsService())


def _profile(settings, profile_id="fake"):
    return next(p for p in settings["profiles"] if p["id"] == profile_id)


def test_legacy_projection_and_model_snapshot_never_migrate(settings_env):
    env = settings_env
    legacy = {"language": "en", "base_url": "https://custom.invalid/v1", "default_model": "old-model",
              "api_key": "inline-old-secret", "request_template": {"temperature": 0.8, "max_tokens": 200}}
    env.persist(legacy)
    before = env.path.read_bytes()
    result = env.service.public_settings()
    selected = _profile(result, result["active_profile_id"])
    assert selected["model"] == "old-model" and selected["configured"]
    assert selected["generation_defaults"]["temperature"] == 0.8
    provider, snapshot = env.service.model_snapshot()
    assert provider.api_key == "inline-old-secret"
    assert snapshot == {"profile_id": "custom-current", "model": "old-model", "response_language": "en"}
    assert env.path.read_bytes() == before and not env.writes and not env.deletes
    assert "inline-old-secret" not in json.dumps(result)


def test_missing_config_reads_template_without_creating_file(settings_env):
    env = settings_env
    before = env.template.read_bytes()
    assert _profile(env.service.public_settings())["model"] == "model-a"
    assert not env.path.parent.exists() and env.template.read_bytes() == before
    assert not env.writes and not env.calls


def test_explicit_save_migrates_legacy_key_to_original_profile_when_switching(settings_env):
    from credential_store import credential_target_for_profile
    env = settings_env
    env.persist({"base_url": "https://custom.invalid", "default_model": "old-model", "api_key": "old-key"})
    env.service.update(active_profile_id="deepseek-default", language="ja")
    saved = json.loads(env.path.read_text(encoding="utf-8"))
    assert "api_key" not in saved and "base_url" not in saved
    assert env.keys[credential_target_for_profile("custom-current")] == "old-key"
    assert credential_target_for_profile("deepseek-default") not in env.keys


def test_create_update_sample_and_delete_profile_with_isolated_key(settings_env):
    from credential_store import credential_target_for_profile
    env = settings_env
    created = env.service.create_profile(id="custom-two", name="Second", base_url="http://localhost:8080/v1",
        model="other", api_key="second-key", capabilities={"tool_stream": True, "thinking_required": True},
        generation_defaults={"temperature": 0.7, "top_p": 0.95, "reasoning_effort": "high"},
        request_overrides={"extra_body": {"thinking": {"type": "enabled", "clear_thinking": False}}})
    assert _profile(created, "custom-two")["deletable"]
    assert env.keys[credential_target_for_profile("custom-two")] == "second-key"
    assert "second-key" not in json.dumps(created) and "credentialTarget" not in json.dumps(created)
    changed = env.service.update(active_profile_id="custom-two", profile={"id": "custom-two", "name": "Renamed",
        "generation_defaults": {}, "request_overrides": {}})
    assert _profile(changed, "custom-two")["generation_defaults"] == {}
    final = env.service.delete_profile("custom-two")
    assert not any(p["id"] == "custom-two" for p in final["profiles"])
    assert final["active_profile_id"] != "custom-two"
    assert credential_target_for_profile("custom-two") in env.deletes


def test_backend_generates_custom_id_and_credential_target(settings_env):
    result = settings_env.service.create_profile(name="Auto", base_url="https://example.invalid", model="auto")
    selected = next(p for p in result["profiles"] if p["name"] == "Auto")
    assert selected["id"].startswith("custom_")
    saved = json.loads(settings_env.path.read_text(encoding="utf-8"))
    private = next(p for p in saved["modelProfiles"] if p["id"] == selected["id"])
    assert private["credentialTarget"].endswith(selected["id"])


def test_delete_missing_profile_rejected_without_writes(settings_env):
    with pytest.raises(ValueError):
        settings_env.service.delete_profile("missing-profile")
    assert not settings_env.path.exists() and not settings_env.deletes


def test_key_commands_do_not_affect_other_profiles_or_revive_legacy_key(settings_env):
    from credential_store import CREDENTIAL_TARGET
    env = settings_env
    env.service.set_key("fake", "new-key")
    assert _profile(env.service.public_settings())["configured"]
    env.keys[CREDENTIAL_TARGET] = "legacy-global"
    env.service.delete_key("fake")
    assert not _profile(env.service.public_settings())["configured"]
    assert env.keys[CREDENTIAL_TARGET] == "legacy-global"
    env.persist({"base_url": "https://custom.invalid", "default_model": "old-model", "api_key": "inline-key"})
    env.service.delete_key("custom-current")
    assert not _profile(env.service.public_settings(), "custom-current")["configured"]
    assert "api_key" not in json.loads(env.path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("values", [
    {"credentialTarget": "stolen-target"}, {"id": "different"},
    {"request_overrides": {"extra_body": {"api_key": "nested-secret"}}},
    {"generation_defaults": {"headers": {"Authorization": "Bearer secret"}}},
    {"request_overrides": {"extra_body": [{"credential_target": "secret"}]}},
    {"request_overrides": {"extra_body": {"gateway_token": "nested-secret"}}},
    {"request_overrides": {"extra_body": {"key": "nested-secret"}}},
    {"capabilities": {"private_payload": True}}, {"capabilities": {"tools": "yes"}},
    {"base_url": "https://name:password@example.invalid/v1"},
    {"base_url": "https://example.invalid/v1?api_key=secret"},
    {"generation_defaults": {"temperature": 3}}, {"generation_defaults": {"top_p": -0.1}},
    {"generation_defaults": {"temperature": float("nan")}},
])
def test_unsafe_profile_values_rejected_before_persistence(settings_env, values):
    profile = {"id": "fake", **values}
    with pytest.raises(ValueError):
        settings_env.service.update(profile=profile)
    assert not settings_env.path.exists() and not settings_env.writes


def test_public_advanced_values_strip_nested_credentials_and_url_credentials(settings_env):
    env = settings_env
    config = copy.deepcopy(env.config)
    item = config["modelProfiles"][0]
    item["baseUrl"] = "https://name:password@example.invalid/v1?api_key=url-secret"
    item["requestOverrides"] = {"headers": {"Authorization": "header-secret"},
        "extra_body": {"thinking": {"type": "enabled"}, "apiKey": "nested-secret", "_private": "private-secret"}}
    item["capabilities"] = {"tool_stream": True, "thinking_required": True, "password": True}
    env.persist(config)
    before = env.path.read_bytes()
    public = env.service.public_settings()
    wire = json.dumps(public)
    for private in ("password", "url-secret", "header-secret", "nested-secret", "private-secret"):
        assert private not in wire
    assert _profile(public)["capabilities"] == {"tool_stream": True, "thinking_required": True}
    assert env.path.read_bytes() == before


def test_test_connection_uses_unsaved_draft_and_key_without_any_mutation(settings_env):
    env = settings_env
    result = env.service.test_connection("fake", profile={"id": "fake", "model": "draft-model",
        "generation_defaults": {"top_p": 0.3}}, api_key="temporary-secret")
    assert result["status"] == "connected"
    profile, key = env.calls[0]
    assert key == "temporary-secret" and profile["model"] == "draft-model"
    assert not env.path.exists() and not env.writes and not env.deletes
    assert _profile(env.service.public_settings())["model"] == "model-a"


def test_test_connection_does_not_echo_provider_errors(settings_env, monkeypatch):
    import model_provider
    env = settings_env
    def failed(*args):
        raise model_provider.ModelProviderError("Authentication Bearer very-private-key D:\\private\\config.json", code="authentication")
    monkeypatch.setattr(model_provider, "test_model_profile", failed)
    result = env.service.test_connection("fake", api_key="very-private-key")
    assert result["status"] == "failed" and result["error_code"] == "authentication"
    assert "very-private-key" not in json.dumps(result) and "private" not in result["message"]
    assert not env.path.exists()


def test_settings_change_does_not_modify_existing_provider_snapshot(settings_env):
    env = settings_env
    env.keys["test-target"] = "key-a"
    provider, snapshot = env.service.model_snapshot()
    env.service.update(profile={"id": "fake", "model": "model-b", "generation_defaults": {"temperature": 1.0}}, api_key="key-b", language="ja")
    assert provider.profile["model"] == "model-a" and provider.profile["generationDefaults"]["temperature"] == 0.3
    assert provider.api_key == "key-a" and snapshot["response_language"] == "zh-CN"
    assert "key-a" not in json.dumps(snapshot)


def test_http_settings_commands_require_operator_and_csrf(settings_env, tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from application.workbench import WorkbenchService
    from integrations.web.app import create_app
    origin = "http://127.0.0.1:8765"
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state", settings=settings_env.service)
    app = create_app(service.store.base_dir, service=service, origin=origin, operator_token="operator", agent_token="agent")
    with TestClient(app, base_url=origin) as client:
        commands = [("post", "/api/settings/profiles", {"id": "new", "name": "New", "model": "new", "base_url": "https://example.invalid"}),
                    ("put", "/api/settings/profiles/fake/key", {"api_key": "secret"}),
                    ("delete", "/api/settings/profiles/fake/key", None),
                    ("delete", "/api/settings/profiles/fake", None),
                    ("post", "/api/settings/profiles/fake/test", {"api_key": "secret"})]
        for method, path, body in commands:
            assert client.request(method, path, json=body, headers={"Authorization": "Bearer agent", "Origin": origin}).status_code == 401
        login = client.post("/api/session", json={"token": "operator"}, headers={"Origin": origin}).json()
        for method, path, body in commands:
            assert client.request(method, path, json=body, headers={"Origin": origin}).status_code == 403
        headers = {"Origin": origin, "X-CSRF-Token": login["csrf"]}
        result = client.put("/api/settings/profiles/fake/key", json={"api_key": "secret"}, headers=headers)
        assert result.status_code == 200 and "secret" not in result.text
        result = client.post("/api/settings/profiles/fake/test", json={}, headers=headers)
        assert result.status_code == 200 and result.json()["status"] == "connected"
        result = client.post("/api/settings/profiles", json={"name": "New", "model": "new", "base_url": "https://example.invalid",
            "credentialTarget": "secret-target"}, headers=headers)
        assert result.status_code == 422 and "secret-target" not in result.text
        result = client.put("/api/settings", json={"profile": {"id": "fake", "request_overrides": {
            "extra_body": {"api_key": "nested-secret"}}}}, headers=headers)
        assert result.status_code == 400 and "nested-secret" not in result.text


def test_read_only_server_rejects_even_explicit_connection_test(settings_env, tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from application.workbench import WorkbenchService
    from integrations.web.app import create_app
    origin = "http://127.0.0.1:8765"
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state", read_only=True, settings=settings_env.service)
    with TestClient(create_app(service.store.base_dir, service=service, origin=origin, operator_token="operator"), base_url=origin) as client:
        login = client.post("/api/session", json={"token": "operator"}, headers={"Origin": origin}).json()
        response = client.post("/api/settings/profiles/fake/test", json={"api_key": "secret"},
            headers={"Origin": origin, "X-CSRF-Token": login["csrf"]})
        assert response.status_code == 403
    assert not settings_env.calls and not settings_env.path.exists()


def test_browser_demo_uses_real_settings_with_only_temporary_io(tmp_path, monkeypatch):
    import config_manager
    import credential_store
    import model_provider
    from scripts.web_demo import isolated_demo_settings
    def forbidden(*args, **kwargs):
        pytest.fail("Demo touched a real credential or network dependency")
    for module in (credential_store, config_manager):
        monkeypatch.setattr(module, "read_api_key", forbidden)
        monkeypatch.setattr(module, "write_api_key", forbidden)
    monkeypatch.setattr(credential_store, "delete_api_key", forbidden)
    monkeypatch.setattr(model_provider, "test_model_profile", forbidden)
    with isolated_demo_settings(tmp_path, lambda profile, key: SimpleNamespace(profile=profile, api_key=key)) as service:
        assert type(service) is SettingsService
        assert _profile(service.public_settings(), "offline")["configured"]
        service.create_profile(id="demo-custom", name="Custom demo", model="demo-model", base_url="https://example.invalid",
                               generation_defaults={"temperature": 0.8}, api_key="only-in-demo-memory")
        service.update(active_profile_id="demo-custom")
        assert service.model_snapshot()[0].api_key == "only-in-demo-memory"
        assert service.test_connection("demo-custom")["status"] == "connected"
        assert service.test_connection("demo-custom", api_key="demo-fail")["error_code"] == "authentication"
        service.delete_key("demo-custom")
        assert not _profile(service.public_settings(), "demo-custom")["configured"]
        service.delete_profile("demo-custom")
        assert config_manager.get_config_path() == tmp_path / "demo-config.json"
    assert "only-in-demo-memory" not in (tmp_path / "demo-config.json").read_text(encoding="utf-8")


def test_browser_demo_main_analysis_confirm_generation_autosaves_with_private_audit(monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import model_provider
    import uvicorn
    from fastapi.testclient import TestClient
    from scripts import web_demo
    import sys
    def forbidden_client(*args, **kwargs):
        pytest.fail("Demo generation tried to instantiate a network model")
    monkeypatch.setattr(model_provider.OpenAICompatibleProvider, "_create_client", forbidden_client)
    def run_locally(app, **kwargs):
        with TestClient(app, base_url="http://127.0.0.1:8765"):
            service = app.state.service
            project = service.projects.list_projects()[0]
            command = {"project_id": project["id"], "version_id": "v0001", "response_language": "zh-CN",
                       "attachment_ids": [], "run_id": None, "deep": True, "text": "生成电机启动、停止及保护控制。"}
            analysis = service.submit({**command, "kind": "analysis", "request_id": "demo-analysis"})
            service.jobs._futures[analysis["id"]].result(timeout=10)
            assert service.jobs.get(analysis["id"])["status"] == "completed"
            output = service.output(analysis["id"])
            draft = output["spec_draft"]
            assert output["spec_base_hash"] is not None
            assert service.set_spec(project["id"], spec=draft, expected_hash=output["spec_base_hash"])["valid"]
            job = service.submit({**command, "kind": "generation", "request_id": "demo-generation"})
            service.jobs._futures[job["id"]].result(timeout=10)
            completed = service.jobs.get(job["id"])
            assert completed["status"] == "completed", completed
            proposal_id = completed["result"]["proposal_id"]
            proposal = service.proposals.get(proposal_id)
            assert proposal["status"] == "accepted" and proposal["action"] == "accept_local"
            assert proposal["summary"]["approval"]["source"] == "local_autosave"
            assert completed["result"]["version_id"] == proposal["result"]["version_id"]
            assert service.proposals.read_private(proposal_id)["_candidate_ir"]
            assert len(service.store.get_project(project["id"])["versions"]) == 2
    monkeypatch.setattr(uvicorn, "run", run_locally)
    monkeypatch.setattr(sys, "argv", ["web_demo.py", "--model-delay", "0"])
    web_demo.main()


def test_demo_failure_diagnostics_only_contain_exception_types_and_code_locations(capsys):
    from scripts.web_demo import _demo_exception_diagnostic
    import model_provider
    try:
        try:
            raise model_provider.ModelProviderError("Bearer sdk-secret-and-private-path", code="authentication")
        except Exception as error:
            raise RuntimeError("Wrapper may also contain sdk-secret-and-private-path") from error
    except Exception as error:
        _demo_exception_diagnostic(error)
    stderr = capsys.readouterr().err
    assert "RuntimeError" in stderr and "ModelProviderError" in stderr
    assert "test_application_settings.py" in stderr
    assert "sdk-secret" not in stderr and "Bearer" not in stderr


def test_first_unsaved_profile_can_detect_without_writing_settings_or_credentials(settings_env, monkeypatch):
    from test_model_capabilities import Endpoint
    from model_provider import OpenAICompatibleProvider
    import model_provider
    env = settings_env
    endpoint = Endpoint()
    monkeypatch.setattr(model_provider, "create_provider", lambda p, k: OpenAICompatibleProvider(p, k, client=endpoint))
    result = env.service.detect_profile(name="First", base_url="https://gateway.invalid/custom/v2/",
                                       model="tenant-alias", api_key="temporary-key")
    assert result["status"] == "connected"
    assert result["discovery"]["contract"]["parameters"]["reasoning_effort"]["values"] == ["low", "high", "max"]
    assert not env.path.exists() and not env.writes and not env.deletes
    assert "temporary-key" not in json.dumps(result)


def test_detect_does_not_reuse_saved_key_for_changed_endpoint(settings_env):
    env = settings_env
    env.keys["test-target"] = "do-not-send-to-another-host"
    result = env.service.detect_profile(id="fake", base_url="https://other.invalid/v1")
    assert result["status"] == "failed" and result["error_code"] == "missing_key"
    assert not env.calls and not env.path.exists()


def test_parameter_contract_roundtrips_and_is_invalidated_on_model_or_key_change(settings_env):
    from model_capabilities import capability_scope
    env = settings_env
    source = copy.deepcopy(env.config["modelProfiles"][0])
    support = {"scope": capability_scope(source), "parameters": {
        "reasoning_effort": {"status": "supported", "source": "probe", "values": ["low", "high"]}}}
    result = env.service.update(profile={"id": "fake", "parameter_support": support})
    assert _profile(result)["parameter_support"] == support
    env.service.set_key("fake", "new-key")
    assert not _profile(env.service.public_settings())["parameter_support"]
    env.service.update(profile={"id": "fake", "parameter_support": support})
    changed = env.service.update(profile={"id": "fake", "model": "different"})
    assert not _profile(changed)["parameter_support"]
    with pytest.raises(ValueError):
        env.service.update(profile={"id": "fake", "parameter_support": support})


def test_discovery_http_requires_operator_csrf_and_accepts_an_unsaved_profile(settings_env, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from application.workbench import WorkbenchService
    from integrations.web.app import create_app
    from model_provider import OpenAICompatibleProvider
    from test_model_capabilities import Endpoint
    import model_provider
    monkeypatch.setattr(model_provider, "create_provider", lambda p, k: OpenAICompatibleProvider(p, k, client=Endpoint()))
    env = settings_env
    origin = "http://127.0.0.1:8765"
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state", settings=env.service)
    app = create_app(service.store.base_dir, service=service, origin=origin, operator_token="operator", agent_token="agent")
    body = {"profile": {"name": "Unsaved", "base_url": "https://gateway.invalid/custom/v2/",
                        "model": "tenant-alias", "api_key": "private-draft-key"}}
    with TestClient(app, base_url=origin) as client:
        assert client.post("/api/settings/detect", json=body, headers={"Origin": origin, "Authorization": "Bearer agent"}).status_code == 401
        login = client.post("/api/session", json={"token": "operator"}, headers={"Origin": origin}).json()
        assert client.post("/api/settings/detect", json=body, headers={"Origin": origin}).status_code == 403
        response = client.post("/api/settings/detect", json=body, headers={"Origin": origin, "X-CSRF-Token": login["csrf"]})
        assert response.status_code == 200 and response.json()["status"] == "connected"
        assert "private-draft-key" not in response.text
    assert not env.path.exists() and not env.writes
