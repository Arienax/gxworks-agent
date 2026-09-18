"""Profile removal persists across Web/CLI reads using temporary config/keys."""
import copy
import json
from types import SimpleNamespace

import pytest

import storage.config as config_manager
import storage.credentials as credential_store
from application.settings import SettingsService


@pytest.fixture
def isolated_profiles(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    template = tmp_path / "template.json"
    initial = {"language": "zh-CN", "activeModelProfileId": "deepseek-default",
               "modelProfiles": config_manager._default_profiles()}
    template.write_text(json.dumps(initial), encoding="utf-8")
    path.write_text(json.dumps(initial), encoding="utf-8")
    keys, writes, deleted = {}, [], []

    def read(target=credential_store.CREDENTIAL_TARGET):
        return keys.get(target, "")

    def write(value, target=credential_store.CREDENTIAL_TARGET):
        keys[target] = value
        writes.append(target)

    def delete(target=credential_store.CREDENTIAL_TARGET):
        keys.pop(target, None)
        deleted.append(target)

    monkeypatch.setattr(config_manager, "get_config_path", lambda: path)
    monkeypatch.setattr(config_manager, "resource_path", lambda name: template)
    for module in (config_manager, credential_store):
        monkeypatch.setattr(module, "read_api_key", read)
        monkeypatch.setattr(module, "write_api_key", write)
    monkeypatch.setattr(credential_store, "delete_api_key", delete)
    return SimpleNamespace(path=path, initial=initial, keys=keys, writes=writes,
                           deleted=deleted, service=SettingsService())


@pytest.mark.parametrize("profile_id", sorted(config_manager.BUILTIN_MODEL_PROFILE_IDS))
def test_preinstalled_profile_deletion_survives_web_and_cli_reads(isolated_profiles, profile_id):
    env = isolated_profiles
    original = env.service.public_settings()
    selected = next(p for p in original["profiles"] if p["id"] == profile_id)
    assert selected["deletable"] is True
    env.service.update(active_profile_id=profile_id)
    expected_ids = [p["id"] for p in original["profiles"] if p["id"] != profile_id]
    result = env.service.delete_profile(profile_id)
    assert [p["id"] for p in result["profiles"]] == expected_ids
    assert result["active_profile_id"] == expected_ids[0]
    for _ in range(2):
        assert [p["id"] for p in config_manager.load_full_config()["modelProfiles"]] == expected_ids
        assert [p["id"] for p in SettingsService().public_settings()["profiles"]] == expected_ids
    assert env.deleted == [credential_store.credential_target_for_profile(profile_id)]
    assert env.keys == {} and env.writes == []


def test_delete_all_profiles_then_create_first_profile(isolated_profiles):
    env = isolated_profiles
    for profile in env.initial["modelProfiles"]:
        result = env.service.delete_profile(profile["id"])
    assert result == {"language": "zh-CN", "active_profile_id": "", "profiles": []}
    saved = env.path.read_bytes()
    for _ in range(2):
        loaded = config_manager.load_full_config()
        assert loaded["modelProfiles"] == [] and loaded["activeModelProfileId"] == ""
        assert SettingsService().public_settings() == result
        assert env.path.read_bytes() == saved
    with pytest.raises(ValueError, match="尚未配置模型"):
        env.service.model_snapshot()
    created = env.service.create_profile(name="Fresh", model="unit-test", base_url="https://example.invalid")
    assert len(created["profiles"]) == 1
    assert created["active_profile_id"] == created["profiles"][0]["id"]
    assert not created["profiles"][0]["configured"]
    assert len(config_manager.load_full_config()["modelProfiles"]) == 1
    assert env.keys == {} and env.writes == []


def test_saved_empty_list_is_not_seeded_but_missing_config_is(isolated_profiles):
    env = isolated_profiles
    config_manager.save_config({"language": "zh-CN", "activeModelProfileId": "", "modelProfiles": []})
    assert config_manager.load_full_config()["modelProfiles"] == []
    env.path.unlink()  # Only this fixture's known temporary config.
    assert {p["id"] for p in config_manager.load_full_config()["modelProfiles"]} == config_manager.BUILTIN_MODEL_PROFILE_IDS
    assert env.writes == []


def test_deleted_key_does_not_return_from_global_legacy_credential(isolated_profiles):
    env = isolated_profiles
    target = credential_store.credential_target_for_profile("deepseek-default")
    other = credential_store.credential_target_for_profile("zhipu-glm-5.3")
    # Sentinels live only in this fixture's dictionary, never in Credential Manager.
    env.keys.update({credential_store.CREDENTIAL_TARGET: "legacy-sentinel", target: "deleted-sentinel", other: "other-sentinel"})
    env.service.delete_profile("deepseek-default")
    expected = copy.deepcopy(env.keys)
    config_manager.load_full_config()
    assert env.keys == expected
    assert target not in env.keys and env.keys[other] == "other-sentinel"
    assert env.deleted == [target] and env.writes == []


def test_http_delete_last_profile_and_recreate_without_a_model_call(isolated_profiles, tmp_path):
    from fastapi.testclient import TestClient
    from application.workbench import WorkbenchService
    from integrations.web.app import create_app

    env = isolated_profiles
    config = copy.deepcopy(env.initial)
    config["modelProfiles"] = config["modelProfiles"][:1]
    config_manager.save_config(config)
    origin = "http://127.0.0.1:8765"
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state", settings=env.service)
    app = create_app(service.store.base_dir, service=service, origin=origin, operator_token="isolated-delete")
    with TestClient(app, base_url=origin) as client:
        login = client.post("/api/session", json={"token": "isolated-delete"}, headers={"Origin": origin}).json()
        headers = {"Origin": origin, "X-CSRF-Token": login["csrf"]}
        removed = client.delete("/api/settings/profiles/deepseek-default", headers=headers)
        assert removed.status_code == 200
        assert removed.json()["profiles"] == []
        assert client.get("/api/settings").json()["profiles"] == []
        project = client.post("/api/projects", json={"name": "No model"}, headers=headers).json()
        job = client.post("/api/jobs", json={"kind": "generation", "project_id": project["id"],
            "request_id": "empty-model-generation", "text": "合成工程"}, headers=headers)
        assert job.status_code == 400
        assert job.json()["error"]["code"] == "model_configuration_required"
        assert "模型" in job.json()["error"]["message"] and "新建配置" in job.json()["error"]["message"]
        created = client.post("/api/settings/profiles", json={"name": "Fresh", "model": "unit-test",
            "base_url": "https://example.invalid"}, headers=headers)
        assert created.status_code == 201
        assert len(created.json()["profiles"]) == 1
        assert created.json()["active_profile_id"] == created.json()["profiles"][0]["id"]
    assert env.keys == {} and env.writes == []
