import copy
import json

import pytest

import storage.config as config_manager
from storage.credentials import CREDENTIAL_TARGET, credential_target_for_profile


def _credential_fakes(monkeypatch, initial=None):
    values = dict(initial or {})
    writes = []

    def read(target=CREDENTIAL_TARGET):
        return values.get(target, "")

    def write(value, target=CREDENTIAL_TARGET):
        values[target] = value
        writes.append((target, value))

    monkeypatch.setattr(config_manager, "read_api_key", read)
    monkeypatch.setattr(config_manager, "write_api_key", write)
    return values, writes


def test_legacy_config_migrates_idempotently_and_copies_but_keeps_credential(
    monkeypatch, tmp_path
):
    credentials, writes = _credential_fakes(monkeypatch)
    path = tmp_path / "config.json"
    legacy = {
        "api_key": "sk-legacy",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-pro",
        "request_template": {
            "model": "{model}",
            "messages": "{messages}",
            "temperature": 0.25,
            "extra_body": {"thinking": {"type": "enabled"}},
        },
        "plc_model": "FX3U",
    }

    migrated = config_manager._migrate_configuration(path, copy.deepcopy(legacy))
    first_bytes = path.read_bytes()
    migrated_again = config_manager._migrate_configuration(
        path, copy.deepcopy(migrated)
    )

    assert migrated_again == migrated
    assert path.read_bytes() == first_bytes
    assert migrated["activeModelProfileId"] == "deepseek-default"
    assert {item["id"] for item in migrated["modelProfiles"]} >= {
        "deepseek-default",
        "deepseek-v4-flash",
        "deepseek-v4-flash-vision-exp",
        "zhipu-glm-5.3-flash",
        "zhipu-glm-5.3",
        "zhipu-glm-5.2",
    }
    assert not {"api_key", "base_url", "default_model", "request_template"}.intersection(
        migrated
    )
    deepseek = config_manager.get_model_profile(migrated)
    assert deepseek["generationDefaults"]["temperature"] == 0.25
    assert credentials[CREDENTIAL_TARGET] == "sk-legacy"
    assert credentials[deepseek["credentialTarget"]] == "sk-legacy"
    assert writes.count((CREDENTIAL_TARGET, "sk-legacy")) == 1
    assert writes.count((deepseek["credentialTarget"], "sk-legacy")) == 1


def test_glm_legacy_config_selects_glm_profile_and_keeps_required_defaults(
    monkeypatch, tmp_path
):
    _credential_fakes(monkeypatch, {CREDENTIAL_TARGET: "glm-key"})
    path = tmp_path / "config.json"
    migrated = config_manager._migrate_configuration(
        path,
        {
            "base_url": "https://open.bigmodel.cn/api/paas/v4/",
            "default_model": "glm-5.3-flash",
            "request_template": {},
        },
    )

    profile = config_manager.get_model_profile(migrated)
    assert profile["id"] == "zhipu-glm-5.3-flash"
    assert profile["capabilities"]["thinking_required"] is True
    assert profile["capabilities"]["tool_stream"] is True
    assert profile["generationDefaults"]["reasoning_effort"] == "max"
    assert profile["requestOverrides"]["extra_body"]["thinking"]["type"] == "enabled"


@pytest.mark.parametrize(
    ("model", "expected_profile_id"),
    [
        ("glm-5.3", "zhipu-glm-5.3"),
        ("glm-5.2", "zhipu-glm-5.2"),
    ],
)
def test_glm_legacy_config_selects_matching_official_profile(
    monkeypatch, tmp_path, model, expected_profile_id
):
    _credential_fakes(monkeypatch)
    migrated = config_manager._migrate_configuration(
        tmp_path / "config.json",
        {
            "base_url": "https://open.bigmodel.cn/api/paas/v4/",
            "default_model": model,
            "request_template": {},
        },
    )

    profile = config_manager.get_model_profile(migrated)
    assert profile["id"] == expected_profile_id
    assert profile["model"] == model
    assert profile["capabilities"]["tool_stream"] is True
    assert profile["generationDefaults"]["reasoning_effort"] == "max"
    assert profile["requestOverrides"]["extra_body"]["thinking"]["type"] == "enabled"


@pytest.mark.parametrize(
    ("model", "expected_profile_id", "multimodal"),
    [
        ("deepseek-v4-flash", "deepseek-v4-flash", False),
        (
            "deepseek-v4-flash-vision-exp",
            "deepseek-v4-flash-vision-exp",
            True,
        ),
    ],
)
def test_deepseek_legacy_config_selects_matching_official_profile(
    monkeypatch, tmp_path, model, expected_profile_id, multimodal
):
    _credential_fakes(monkeypatch)
    migrated = config_manager._migrate_configuration(
        tmp_path / "config.json",
        {
            "base_url": "https://api.deepseek.com",
            "default_model": model,
            "request_template": {},
        },
    )

    profile = config_manager.get_model_profile(migrated)
    assert profile["id"] == expected_profile_id
    assert profile["model"] == model
    assert bool(profile["capabilities"].get("multimodal")) is multimodal


def test_existing_profile_config_keeps_user_list_without_adding_builtins(
    monkeypatch, tmp_path
):
    _credential_fakes(monkeypatch)
    custom = {
        "id": "custom-1",
        "name": "公司模型",
        "adapter": "openai_compatible",
        "baseUrl": "https://models.example.invalid/v1",
        "model": "company-model",
        "capabilities": {},
        "generationDefaults": {},
        "requestOverrides": {},
    }
    source = {
        "activeModelProfileId": "custom-1",
        "modelProfiles": [
            copy.deepcopy(config_manager.DEFAULT_MODEL_PROFILES[0]),
            custom,
        ],
    }

    migrated = config_manager._migrate_configuration(
        tmp_path / "config.json", copy.deepcopy(source)
    )
    profile_ids = [item["id"] for item in migrated["modelProfiles"]]

    assert profile_ids == [config_manager.DEFAULT_MODEL_PROFILES[0]["id"], "custom-1"]
    assert config_manager.get_model_profile(migrated)["model"] == "company-model"
    assert profile_ids.count("custom-1") == 1


def test_unknown_legacy_service_becomes_openai_compatible_custom_profile(
    monkeypatch, tmp_path
):
    _credential_fakes(monkeypatch)
    migrated = config_manager._migrate_configuration(
        tmp_path / "config.json",
        {
            "base_url": "https://models.example.invalid/v1",
            "default_model": "example-model",
            "request_template": {"temperature": 0.4, "vendor_flag": True},
        },
    )

    profile = config_manager.get_model_profile(migrated)
    assert profile["id"] == "custom-current"
    assert profile["adapter"] == "openai_compatible"
    assert profile["generationDefaults"] == {"temperature": 0.4}
    assert profile["requestOverrides"] == {"vendor_flag": True}


def test_profile_credentials_are_isolated_and_never_fall_back_to_legacy(monkeypatch):
    deepseek_target = credential_target_for_profile("deepseek-default")
    glm_target = credential_target_for_profile("zhipu-glm-5.3-flash")
    calls = []
    values = {
        CREDENTIAL_TARGET: "legacy-key",
        deepseek_target: "deepseek-key",
        glm_target: "glm-key",
    }

    def read(target=CREDENTIAL_TARGET):
        calls.append(target)
        return values.get(target, "")

    monkeypatch.setattr(config_manager, "read_api_key", read)
    config = {
        "activeModelProfileId": "deepseek-default",
        "modelProfiles": copy.deepcopy(list(config_manager.DEFAULT_MODEL_PROFILES)),
    }

    assert config_manager.get_api_key(config, "deepseek-default") == "deepseek-key"
    assert config_manager.get_api_key(config, "zhipu-glm-5.3-flash") == "glm-key"
    values.pop(glm_target)
    assert config_manager.get_api_key(config, "zhipu-glm-5.3-flash") == ""
    assert CREDENTIAL_TARGET not in calls


def test_save_config_rejects_missing_or_invalid_active_profile(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_manager, "get_config_path", lambda: str(path))
    with pytest.raises(ValueError, match="必须是数组"):
        config_manager.save_config({"activeModelProfileId": "none"})
    with pytest.raises(ValueError, match="未指向有效"):
        config_manager.save_config({"activeModelProfileId": "none", "modelProfiles": []})
    with pytest.raises(ValueError, match="未指向有效"):
        config_manager.save_config(
            {
                "activeModelProfileId": "missing",
                "modelProfiles": copy.deepcopy(
                    list(config_manager.DEFAULT_MODEL_PROFILES)
                ),
            }
        )
    duplicate = copy.deepcopy(list(config_manager.DEFAULT_MODEL_PROFILES))
    duplicate.append(copy.deepcopy(duplicate[0]))
    with pytest.raises(ValueError, match="id 重复"):
        config_manager.save_config(
            {"activeModelProfileId": "deepseek-default", "modelProfiles": duplicate}
        )
    assert not path.exists()


def test_default_config_file_uses_only_profile_schema():
    path = config_manager.resource_path("config.default.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["activeModelProfileId"] == "deepseek-default"
    profiles = {item["id"]: item for item in payload["modelProfiles"]}
    assert set(config_manager.BUILTIN_MODEL_PROFILE_IDS).issubset(profiles)
    assert profiles["zhipu-glm-5.3"]["model"] == "glm-5.3"
    assert profiles["zhipu-glm-5.3"]["capabilities"]["thinking_required"] is True
    assert profiles["zhipu-glm-5.2"]["model"] == "glm-5.2"
    assert profiles["deepseek-v4-flash"]["model"] == "deepseek-v4-flash"
    assert not profiles["deepseek-v4-flash"]["capabilities"].get("multimodal")
    assert (
        profiles["deepseek-v4-flash-vision-exp"]["model"]
        == "deepseek-v4-flash-vision-exp"
    )
    assert profiles["deepseek-v4-flash-vision-exp"]["capabilities"]["multimodal"] is True
    assert profiles["zhipu-glm-5.3-flash"]["capabilities"]["multimodal"] is True
    assert not {"api_key", "base_url", "default_model", "request_template"}.intersection(
        payload
    )


def test_legacy_chat_import_converts_reasoning_to_unified_metadata(tmp_path):
    from storage.session import SessionStore

    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    (legacy_dir / "chat_history.json").write_text(
        json.dumps(
            [
                {
                    "role": "assistant",
                    "content": "旧回答",
                    "reasoning_content": "旧推理字段",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=legacy_dir)

    imported = store.import_legacy_once()

    message = imported["messages"][0]
    assert message["metadata"] == {"reasoning": "旧推理字段"}
    assert "reasoning_content" not in message["metadata"]
