import copy
import json

import pytest

import storage.config as config_manager
from storage.credentials import CREDENTIAL_TARGET, credential_target_for_profile
from model_runtime.runtime_profile import materialize_runtime_profile


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
    runtime = materialize_runtime_profile(deepseek, api_key="sk-legacy")
    assert runtime.settings.parameters["temperature"] == {
        "mode": "value", "value": 0.25
    }
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
    runtime = materialize_runtime_profile(profile, api_key="glm-key")
    assert runtime.contract.capabilities["thinking_required"].status == "supported"
    assert runtime.contract.capabilities["tool_stream"].status == "supported"
    assert runtime.contract.capabilities["vision"].status == "supported"
    assert "reasoning_effort" not in runtime.settings.parameters


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
    runtime = materialize_runtime_profile(profile, api_key="")
    assert runtime.contract.capabilities["tool_stream"].status == "supported"
    assert "reasoning_effort" not in runtime.settings.parameters
    if model == "glm-5.3":
        assert runtime.contract.capabilities["thinking_required"].status == "supported"


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
    runtime = materialize_runtime_profile(profile, api_key="")
    vision = runtime.contract.capabilities.get("vision")
    assert bool(vision and vision.status == "supported") is multimodal


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
    assert profiles["zhipu-glm-5.2"]["model"] == "glm-5.2"
    assert profiles["deepseek-v4-flash"]["model"] == "deepseek-v4-flash"
    assert (
        profiles["deepseek-v4-flash-vision-exp"]["model"]
        == "deepseek-v4-flash-vision-exp"
    )
    retired = {"capabilities", "generationDefaults", "requestOverrides", "parameterSupport"}
    assert all(not retired.intersection(item) for item in profiles.values())

    glm = materialize_runtime_profile(
        config_manager._normalize_profile(profiles["zhipu-glm-5.3"]), api_key=""
    )
    flash = materialize_runtime_profile(
        config_manager._normalize_profile(profiles["zhipu-glm-5.3-flash"]), api_key=""
    )
    deepseek_vision = materialize_runtime_profile(
        config_manager._normalize_profile(profiles["deepseek-v4-flash-vision-exp"]), api_key=""
    )
    assert glm.contract.capabilities["thinking_required"].status == "supported"
    assert flash.contract.capabilities["vision"].status == "supported"
    assert deepseek_vision.contract.capabilities["vision"].status == "supported"
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


# Location migration is independent of the legacy profile-schema/key migration
# above. Only disposable synthetic directories/databases are used here.
def _location_source(tmp_path, name="old", owner="original", wal=False):
    import sqlite3
    directory = tmp_path / name
    directory.mkdir(parents=True)
    config = directory / "config.json"
    payload = b'\xef\xbb\xbf' + (json.dumps({"modelProfiles": [{"id": owner, "credentialTarget": "private-target", "baseUrl": "https://fixture.invalid/v1", "model": "fixture"}],
        "unknown": {"caption": "保留配置", "zero": 0}}, ensure_ascii=False, indent=2) + "\r\n").encode()
    config.write_bytes(payload)
    db = sqlite3.connect(directory / "model-observations.sqlite")
    if wal:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        db.execute("PRAGMA wal_autocheckpoint=0")
    db.execute("CREATE TABLE ownership (name TEXT)")
    db.execute("INSERT INTO ownership VALUES (?)", (owner,))
    db.commit()
    return config, db


def _observed_owner(path):
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(path)) as db:
        return db.execute("SELECT name FROM ownership").fetchall()


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_user_settings_location_does_not_follow_clone_cwd_or_executable(monkeypatch, tmp_path, platform):
    from storage import user_data as data
    monkeypatch.delenv("PLC_AI_CONFIG_PATH", raising=False)
    monkeypatch.delenv("PLC_AI_DATA_DIR", raising=False)
    monkeypatch.setattr(data.sys, "platform", platform)
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(data.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    root = {"win32": tmp_path / "roaming", "linux": tmp_path / "xdg", "darwin": tmp_path / "home" / "Library" / "Application Support"}[platform]
    expected = root / "PLC-AI-Studio" / "config.json"
    assert data.config_path() == expected
    monkeypatch.setattr(data.sys, "frozen", True, raising=False)
    monkeypatch.setattr(data.sys, "executable", str(tmp_path / "new-clone" / "app.exe"))
    monkeypatch.chdir(tmp_path)
    assert data.config_path() == expected and not root.exists()


def test_location_migration_preserves_raw_json_keys_and_committed_wal(tmp_path, monkeypatch):
    from storage import user_data as data
    source, db = _location_source(tmp_path, wal=True)
    try:
        before = source.read_bytes()
        main_before = (source.parent / data.OBSERVATIONS).read_bytes()
        assert (source.parent / (data.OBSERVATIONS + "-wal")).stat().st_size > 0
        monkeypatch.setattr(config_manager, "read_api_key", lambda *a: pytest.fail("no credential reads"))
        monkeypatch.setattr(config_manager, "write_api_key", lambda *a: pytest.fail("no credential writes"))
        target = tmp_path / "user" / "config.json"
        assert data.prepare(target, [source]) == target
        assert target.read_bytes() == before == source.read_bytes()
        assert (source.parent / data.OBSERVATIONS).read_bytes() == main_before
        assert _observed_owner(target.parent / data.OBSERVATIONS) == [("original",)]
        assert not (target.parent / (data.OBSERVATIONS + "-wal")).exists()
        receipt = (target.parent / data.RECEIPT).read_bytes()
        data.prepare(target, [tmp_path / "other-clone" / "config.json"])
        assert (target.parent / data.RECEIPT).read_bytes() == receipt
        assert not (target.parent / data.PENDING).exists()
    finally:
        db.close()


def test_existing_user_settings_and_observations_are_never_overwritten(tmp_path):
    from storage import user_data as data
    source, db = _location_source(tmp_path)
    db.close()
    target, current_db = _location_source(tmp_path, "user", "user")
    current_db.close()
    original = target.read_bytes()
    data.prepare(target, [source])
    assert target.read_bytes() == original and _observed_owner(target.parent / data.OBSERVATIONS) == [("user",)]
    # An existing observation DB without config also remains authoritative.
    target.unlink()
    data.prepare(target, [source])
    assert target.read_bytes() == source.read_bytes()
    assert _observed_owner(target.parent / data.OBSERVATIONS) == [("user",)]
    assert data.OBSERVATIONS not in json.loads((target.parent / data.RECEIPT).read_text())["files"]


@pytest.mark.parametrize("damage", ["json", "database"])
def test_invalid_legacy_state_does_not_fall_back_to_defaults(tmp_path, damage):
    from storage import user_data as data
    source, db = _location_source(tmp_path)
    db.close()
    bad = source if damage == "json" else source.parent / data.OBSERVATIONS
    bad.write_bytes(b"intentionally invalid")
    template = tmp_path / "default.json"
    template.write_text('{"modelProfiles": []}')
    target = tmp_path / "user" / "config.json"
    import sqlite3
    expected = data.SettingsMigrationError if damage == "json" else sqlite3.DatabaseError
    with pytest.raises(expected):
        data.prepare(target, [source], template=template, create_default=True)
    assert bad.read_bytes() == b"intentionally invalid" and not target.exists()
    assert not (target.parent / data.RECEIPT).exists()
    assert not list(target.parent.glob(".settings-stage-*"))


@pytest.mark.parametrize("phase", ["observations", "config", "receipt"])
def test_location_migration_recovers_each_interrupted_publication(tmp_path, monkeypatch, phase):
    from storage import user_data as data
    source, db = _location_source(tmp_path)
    db.close()
    target = tmp_path / "user" / "config.json"
    original = source.read_bytes()
    real = data.os.replace
    failed_name = {"observations": data.OBSERVATIONS, "config": target.name, "receipt": data.RECEIPT}[phase]
    def interrupt(src, dst):
        if data.Path(dst).parent == target.parent and data.Path(dst).name == failed_name:
            raise OSError("synthetic interruption")
        return real(src, dst)
    monkeypatch.setattr(data.os, "replace", interrupt)
    with pytest.raises(OSError, match="synthetic interruption"):
        data.prepare(target, [source])
    assert (target.parent / data.PENDING).is_dir()
    assert source.read_bytes() == original
    monkeypatch.setattr(data.os, "replace", real)
    # Recovery uses the sealed snapshot, not new settings from a different clone.
    other, other_db = _location_source(tmp_path, "other-clone", "wrong")
    other_db.close()
    data.prepare(target, [other])
    assert target.read_bytes() == original and _observed_owner(target.parent / data.OBSERVATIONS) == [("original",)]
    assert not (target.parent / data.PENDING).exists()


def test_interrupted_cleanup_recovers_without_reapplying_settings(tmp_path, monkeypatch):
    from storage import user_data as data
    source, db = _location_source(tmp_path)
    db.close()
    target = tmp_path / "user" / "config.json"
    real = data.Path.rmdir
    def interrupted(path):
        if path.name == data.PENDING:
            raise OSError("cleanup interrupted")
        return real(path)
    monkeypatch.setattr(data.Path, "rmdir", interrupted)
    with pytest.raises(OSError, match="cleanup interrupted"):
        data.prepare(target, [source])
    assert target.exists() and (target.parent / data.RECEIPT).exists()
    monkeypatch.setattr(data.Path, "rmdir", real)
    data.prepare(target, [source])
    assert target.read_bytes() == source.read_bytes() and not (target.parent / data.PENDING).exists()


def test_pending_migration_refuses_conflicting_target_and_preserves_staging(tmp_path, monkeypatch):
    from storage import user_data as data
    source, db = _location_source(tmp_path)
    db.close()
    target = tmp_path / "user" / "config.json"
    resume = data._resume
    monkeypatch.setattr(data, "_resume", lambda p: None)
    data.prepare(target, [source])
    target.write_bytes(b'{"user": "new value"}')
    monkeypatch.setattr(data, "_resume", resume)
    with pytest.raises(data.SettingsMigrationError, match="refusing to overwrite"):
        data.prepare(target, [source])
    assert target.read_bytes() == b'{"user": "new value"}'
    assert (target.parent / data.PENDING / "manifest.json").exists()


def test_migration_receipt_prevents_resurrecting_deleted_legacy_profiles(tmp_path):
    from storage import user_data as data
    source, db = _location_source(tmp_path)
    db.close()
    target = tmp_path / "user" / "config.json"
    data.prepare(target, [source])
    target.unlink()
    data.prepare(target, [source])
    assert not target.exists()
    template = tmp_path / "default.json"
    template.write_text('{"modelProfiles": []}')
    data.prepare(target, [source], template=template, create_default=True)
    assert target.read_bytes() == template.read_bytes()
    assert _observed_owner(target.parent / data.OBSERVATIONS) == [("original",)]


def test_parallel_clones_publish_one_consistent_settings_pair(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from storage import user_data as data
    sources = []
    for owner in ("a", "b"):
        source, db = _location_source(tmp_path, owner, owner)
        db.close()
        sources.append(source)
    target = tmp_path / "user" / "config.json"
    script = "from storage.user_data import prepare; import sys; prepare(sys.argv[1], [sys.argv[2]])"
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    processes = [subprocess.Popen([sys.executable, "-c", script, str(target), str(sources[i % 2])],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) for i in range(4)]
    try:
        for process in processes:
            out, err = process.communicate(timeout=25)
            assert process.returncode == 0, err.decode()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()
    selected = json.loads(target.read_text(encoding="utf-8-sig"))["modelProfiles"][0]["id"]
    assert _observed_owner(target.parent / data.OBSERVATIONS) == [(selected,)]
    assert target.read_bytes() in [s.read_bytes() for s in sources]


def test_settings_service_and_observation_reader_share_location_migration(tmp_path, monkeypatch):
    from storage import user_data as data
    from application.settings import SettingsService
    source, db = _location_source(tmp_path)
    db.close()
    target = tmp_path / "user" / "config.json"
    monkeypatch.delenv("PLC_AI_CONFIG_PATH", raising=False)
    monkeypatch.setenv("PLC_AI_DATA_DIR", str(target.parent))
    monkeypatch.setattr(config_manager, "_legacy_config_paths", lambda: [source])
    monkeypatch.setattr(config_manager, "read_api_key", lambda *a: pytest.fail("location read does not use credentials"))
    before = source.read_bytes()
    config = SettingsService().read_config()
    assert config["unknown"]["caption"] == "保留配置"
    assert target.read_bytes() == before
    assert config_manager.get_observations_path() == target.parent / data.OBSERVATIONS
    assert _observed_owner(config_manager.get_observations_path()) == [("original",)]


def test_explicit_file_override_does_not_import_legacy_state_or_create_on_read(tmp_path, monkeypatch):
    from storage import user_data as data
    from application.settings import SettingsService
    override = tmp_path / "isolated" / "my-settings.json"
    monkeypatch.setenv("PLC_AI_CONFIG_PATH", str(override))
    monkeypatch.setattr(config_manager, "_legacy_config_paths", lambda: pytest.fail("isolated override must not scan legacy"))
    assert data.config_path() == override
    assert config_manager.migrate_user_settings() == str(override)
    SettingsService().read_config()
    assert not override.parent.exists()


def test_source_and_frozen_migration_candidates_never_follow_arbitrary_cwd(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_manager.sys, "frozen", False, raising=False)
    expected = Path(config_manager.__file__).resolve().parents[1]
    assert config_manager._legacy_config_paths() == [expected / "config.json", expected.parent / "config.json"]
    monkeypatch.setattr(config_manager.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config_manager.sys, "executable", str(tmp_path / "release" / "app.exe"))
    monkeypatch.setattr(config_manager.sys, "_MEIPASS", str(tmp_path / "temporary"), raising=False)
    assert config_manager._legacy_config_paths() == [tmp_path / "release" / "config.json"]


def test_settings_path_override_rejects_relative_ambiguity(monkeypatch):
    from storage import user_data as data
    monkeypatch.setenv("PLC_AI_CONFIG_PATH", "relative/config.json")
    with pytest.raises(ValueError, match="absolute"):
        data.config_path()


@pytest.mark.parametrize("model", ["glm-5.3-flash", "glm-5.3", "glm-5.2", "custom-fixture"])
@pytest.mark.parametrize("template,expected,nested", [
    ({}, None, False),
    ({"reasoning_effort": "{effort}"}, None, False),
    ({"reasoning_effort": "high"}, "high", False),
    ({"reasoning_effort": None}, None, False),
    ({"extra_body": {"reasoning_effort": "medium", "fixture": True}}, "medium", True),
    ({"extra_body": {"reasoning_effort": "{effort}", "fixture": True}}, None, True),
])
def test_flat_legacy_config_never_inherits_preset_effort(model, template, expected, nested):
    from model_runtime.provider import OpenAICompatibleProvider, ModelRequest, UserMessage
    config = {"base_url": "https://fixture.invalid/v1", "default_model": model,
              "request_template": template}
    before = copy.deepcopy(config)
    defaults = config_manager._default_profiles()
    selected, profiles = config_manager._profile_from_legacy(config)
    profile = next(p for p in profiles if p["id"] == selected)
    provider = OpenAICompatibleProvider(profile, "synthetic-key", client=object())
    params = provider._request_params(ModelRequest((UserMessage("fixture"),)))
    actual = params.get("extra_body", {}) if nested else params
    assert actual.get("reasoning_effort") == expected
    if expected is None:
        assert "reasoning_effort" not in params
        assert "reasoning_effort" not in params.get("extra_body", {})
    if nested:
        assert params["extra_body"]["fixture"] is True
    assert config == before and config_manager._default_profiles() == defaults
