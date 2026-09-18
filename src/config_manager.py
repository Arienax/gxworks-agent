"""Configuration and model-profile persistence."""
import os
import sys
import json
import copy
import shutil
import tempfile

from credential_store import (
    CREDENTIAL_TARGET,
    credential_target_for_profile,
    read_api_key,
    write_api_key,
)
from resource_paths import resource_path
from i18n import normalize_language


class ModelConfigurationRequiredError(ValueError):
    """No selected model exists; callers can offer the model settings screen."""

    def __init__(self):
        super().__init__("尚未配置模型，请在模型 API 设置中新建配置。")


def get_config_path():
    """获取 config.json 的路径（兼容 PyInstaller 打包）"""
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "config.json")


def ensure_config_file():
    """Create the editable external config from the bundled safe template."""
    config_path = get_config_path()
    if os.path.isfile(config_path):
        return config_path

    template_path = resource_path("config.default.json")
    if not template_path.is_file():
        raise FileNotFoundError(f"找不到配置文件或默认模板: {config_path}")

    try:
        shutil.copyfile(str(template_path), config_path)
    except OSError as error:
        raise OSError(
            f"无法在程序目录创建 config.json，请检查目录写入权限: {config_path}"
        ) from error
    return config_path


def _is_legacy_api_key(value):
    value = str(value or "").strip()
    return bool(value and value not in {
        "请在此处填写你的API Key",
        "请在这里填写你的 API Key",
    })


def _write_json_atomic(config_path, config):
    directory = os.path.dirname(config_path)
    os.makedirs(directory, exist_ok=True)
    handle, temporary_path = tempfile.mkstemp(
        prefix="config-",
        suffix=".tmp",
        dir=directory,
        text=True,
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(config, stream, ensure_ascii=False, indent=4)
        os.replace(temporary_path, config_path)
    except Exception:
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        raise


DEEPSEEK_PROFILE_ID = "deepseek-default"
DEEPSEEK_V4_FLASH_PROFILE_ID = "deepseek-v4-flash"
DEEPSEEK_V4_FLASH_VISION_PROFILE_ID = "deepseek-v4-flash-vision-exp"
ZHIPU_PROFILE_ID = "zhipu-glm-5.3-flash"
ZHIPU_GLM_53_PROFILE_ID = "zhipu-glm-5.3"
ZHIPU_GLM_52_PROFILE_ID = "zhipu-glm-5.2"
BUILTIN_MODEL_PROFILE_IDS = frozenset(
    {
        DEEPSEEK_PROFILE_ID,
        DEEPSEEK_V4_FLASH_PROFILE_ID,
        DEEPSEEK_V4_FLASH_VISION_PROFILE_ID,
        ZHIPU_PROFILE_ID,
        ZHIPU_GLM_53_PROFILE_ID,
        ZHIPU_GLM_52_PROFILE_ID,
    }
)


DEFAULT_MODEL_PROFILES = (
    {
        "id": DEEPSEEK_PROFILE_ID,
        "name": "DeepSeek",
        "adapter": "openai_compatible",
        "baseUrl": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "capabilities": {
            "reasoning": True,
            "tools": True,
            "structured_output": True,
            "disable_tool_choice_with_thinking": True,
        },
        "generationDefaults": {
            "response_format": {"type": "json_object"},
        },
        "requestOverrides": {
            "extra_body": {"thinking": {"type": "enabled"}},
        },
        "credentialTarget": credential_target_for_profile(DEEPSEEK_PROFILE_ID),
    },
    {
        "id": DEEPSEEK_V4_FLASH_PROFILE_ID,
        "name": "DeepSeek V4 Flash",
        "adapter": "openai_compatible",
        "baseUrl": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "capabilities": {
            "reasoning": True,
            "tools": True,
            "structured_output": True,
            "disable_tool_choice_with_thinking": True,
        },
        "generationDefaults": {
            "response_format": {"type": "json_object"},
        },
        "requestOverrides": {
            "extra_body": {"thinking": {"type": "enabled"}},
        },
        "credentialTarget": credential_target_for_profile(
            DEEPSEEK_V4_FLASH_PROFILE_ID
        ),
    },
    {
        "id": DEEPSEEK_V4_FLASH_VISION_PROFILE_ID,
        "name": "DeepSeek V4 Flash Vision Exp",
        "adapter": "openai_compatible",
        "baseUrl": "https://api.deepseek.com",
        "model": "deepseek-v4-flash-vision-exp",
        "capabilities": {
            "reasoning": True,
            "tools": True,
            "structured_output": True,
            "multimodal": True,
            "disable_tool_choice_with_thinking": True,
        },
        "generationDefaults": {
            "response_format": {"type": "json_object"},
        },
        "requestOverrides": {
            "extra_body": {"thinking": {"type": "enabled"}},
        },
        "credentialTarget": credential_target_for_profile(
            DEEPSEEK_V4_FLASH_VISION_PROFILE_ID
        ),
    },
    {
        "id": ZHIPU_PROFILE_ID,
        "name": "智谱 GLM-5.3-Flash",
        "adapter": "openai_compatible",
        "baseUrl": "https://open.bigmodel.cn/api/paas/v4/",
        "model": "glm-5.3-flash",
        "capabilities": {
            "reasoning": True,
            "tools": True,
            "tool_stream": True,
            "structured_output": True,
            "multimodal": True,
            "thinking_required": True,
        },
        "generationDefaults": {
            "temperature": 1.0,
            "top_p": 0.95,
            "reasoning_effort": "max",
            "response_format": {"type": "json_object"},
        },
        "requestOverrides": {
            "extra_body": {
                "thinking": {"type": "enabled", "clear_thinking": False}
            },
        },
        "credentialTarget": credential_target_for_profile(ZHIPU_PROFILE_ID),
    },
    {
        "id": ZHIPU_GLM_53_PROFILE_ID,
        "name": "智谱 GLM-5.3",
        "adapter": "openai_compatible",
        "baseUrl": "https://open.bigmodel.cn/api/paas/v4/",
        "model": "glm-5.3",
        "capabilities": {
            "reasoning": True,
            "tools": True,
            "tool_stream": True,
            "structured_output": True,
            "thinking_required": True,
        },
        "generationDefaults": {
            "temperature": 1.0,
            "reasoning_effort": "max",
            "response_format": {"type": "json_object"},
        },
        "requestOverrides": {
            "extra_body": {"thinking": {"type": "enabled"}},
        },
        "credentialTarget": credential_target_for_profile(
            ZHIPU_GLM_53_PROFILE_ID
        ),
    },
    {
        "id": ZHIPU_GLM_52_PROFILE_ID,
        "name": "智谱 GLM-5.2",
        "adapter": "openai_compatible",
        "baseUrl": "https://open.bigmodel.cn/api/paas/v4/",
        "model": "glm-5.2",
        "capabilities": {
            "reasoning": True,
            "tools": True,
            "tool_stream": True,
            "structured_output": True,
        },
        "generationDefaults": {
            "temperature": 1.0,
            "reasoning_effort": "max",
            "response_format": {"type": "json_object"},
        },
        "requestOverrides": {
            "extra_body": {
                "thinking": {"type": "enabled", "clear_thinking": False}
            },
        },
        "credentialTarget": credential_target_for_profile(
            ZHIPU_GLM_52_PROFILE_ID
        ),
    },
)


def _default_profiles():
    return copy.deepcopy(list(DEFAULT_MODEL_PROFILES))


def _normalize_profile(profile):
    if not isinstance(profile, dict):
        raise ValueError("模型配置必须是 JSON 对象。")
    normalized = copy.deepcopy(profile)
    profile_id = str(normalized.get("id") or "").strip()
    if not profile_id:
        raise ValueError("模型配置缺少 id。")
    normalized["id"] = profile_id
    normalized["name"] = str(normalized.get("name") or profile_id).strip()
    normalized["adapter"] = str(
        normalized.get("adapter") or "openai_compatible"
    ).strip()
    normalized["baseUrl"] = str(normalized.get("baseUrl") or "").strip()
    normalized["model"] = str(normalized.get("model") or "").strip()
    if normalized["adapter"] != "openai_compatible":
        raise ValueError(f"当前版本不支持模型 adapter：{normalized['adapter']}")
    if not normalized["baseUrl"]:
        raise ValueError(f"模型 Profile {profile_id} 缺少 baseUrl。")
    if not normalized["model"]:
        raise ValueError(f"模型 Profile {profile_id} 缺少 model。")
    for key in ("capabilities", "generationDefaults", "requestOverrides", "parameterSupport", "capabilityContract", "userModelSettings", "capabilityOverrides"):
        value = normalized.get(key)
        if value is not None and not isinstance(value, dict):
            raise ValueError(f"模型 Profile {profile_id} 的 {key} 必须是对象。")
        normalized[key] = copy.deepcopy(value) if isinstance(value, dict) else {}
    from model_capabilities import normalize_parameter_support
    normalized["parameterSupport"] = normalize_parameter_support(normalized["parameterSupport"])
    from model_contract import CapabilityContract, UserModelSettings, normalize_contract
    normalized["capabilityContract"] = normalize_contract(normalized["capabilityContract"])
    if normalized["userModelSettings"]:
        if not normalized["capabilityContract"]:
            raise ValueError("User selections require a capability contract")
        normalized["userModelSettings"] = UserModelSettings.from_dict(
            normalized["userModelSettings"], CapabilityContract.from_dict(normalized["capabilityContract"])).to_dict()
    normalized["credentialTarget"] = str(
        normalized.get("credentialTarget")
        or credential_target_for_profile(profile_id)
    )
    return normalized


def _normalize_profiles(profiles):
    normalized = [_normalize_profile(item) for item in profiles]
    ids = [item["id"] for item in normalized]
    duplicates = sorted({profile_id for profile_id in ids if ids.count(profile_id) > 1})
    if duplicates:
        raise ValueError("模型 Profile id 重复：" + "、".join(duplicates))
    return normalized


def _normalize_profile_selection(config):
    """Respect an explicitly saved profile list, including an empty one.

    Defaults seed a missing/legacy schema only. New bundled presets must never
    override a user's removals when the Web, desktop or CLI reads the file.
    """
    profiles = config.get("modelProfiles")
    if not isinstance(profiles, list):
        config["activeModelProfileId"], config["modelProfiles"] = _profile_from_legacy(config)
    else:
        config["modelProfiles"] = _normalize_profiles(profiles)
        ids = {item["id"] for item in config["modelProfiles"]}
        active = str(config.get("activeModelProfileId") or "").strip()
        config["activeModelProfileId"] = active if active in ids else (
            config["modelProfiles"][0]["id"] if profiles else ""
        )
    return config


def _profile_from_legacy(config):
    base_url = str(config.get("base_url") or "https://api.deepseek.com").strip()
    model = str(config.get("default_model") or "deepseek-v4-pro").strip()
    lowered = (base_url + " " + model).lower()
    zhipu_profiles = {
        "glm-5.3-flash": ZHIPU_PROFILE_ID,
        "glm-5.3": ZHIPU_GLM_53_PROFILE_ID,
        "glm-5.2": ZHIPU_GLM_52_PROFILE_ID,
    }
    deepseek_profiles = {
        "deepseek-v4-pro": DEEPSEEK_PROFILE_ID,
        "deepseek-v4-flash": DEEPSEEK_V4_FLASH_PROFILE_ID,
        "deepseek-v4-flash-vision-exp": DEEPSEEK_V4_FLASH_VISION_PROFILE_ID,
    }
    if model.lower() in zhipu_profiles:
        profile_id = zhipu_profiles[model.lower()]
    elif model.lower() in deepseek_profiles:
        profile_id = deepseek_profiles[model.lower()]
    elif "bigmodel.cn" in lowered or model.lower().startswith("glm-"):
        profile_id = ZHIPU_PROFILE_ID
    elif "deepseek" in lowered:
        profile_id = DEEPSEEK_PROFILE_ID
    else:
        profile_id = "custom-current"

    profiles = _default_profiles()
    if profile_id == "custom-current":
        profiles.append(
            {
                "id": profile_id,
                "name": "原有自定义服务",
                "adapter": "openai_compatible",
                "baseUrl": base_url,
                "model": model,
                "capabilities": {},
                "generationDefaults": {},
                "requestOverrides": {},
                "credentialTarget": credential_target_for_profile(profile_id),
            }
        )
    selected = next(item for item in profiles if item["id"] == profile_id)
    selected["baseUrl"] = base_url
    selected["model"] = model

    template = config.get("request_template")
    if isinstance(template, dict):
        portable = {
            "temperature",
            "top_p",
            "max_tokens",
            "stop",
            "response_format",
            "seed",
            "frequency_penalty",
            "presence_penalty",
        }
        defaults = {}
        overrides = {}
        for key, value in template.items():
            if key in {"model", "messages", "stream", "tools", "tool_choice"}:
                continue
            if value == "{effort}":
                continue
            target = defaults if key in portable else overrides
            target[key] = copy.deepcopy(value)
        selected["generationDefaults"].update(defaults)
        selected["requestOverrides"].update(overrides)
    return profile_id, [_normalize_profile(item) for item in profiles]


def _migrate_configuration(config_path, config):
    """Atomically migrate legacy API settings and copy, never delete, its key."""

    original = copy.deepcopy(config)
    config["language"] = normalize_language(config.get("language"))
    legacy_settings = not isinstance(config.get("modelProfiles"), list) or any(
        key in config for key in ("api_key", "base_url", "default_model", "request_template")
    )
    _normalize_profile_selection(config)
    legacy_key = config.get("api_key", "")
    # Once migrated, a global old key must not repopulate a deliberately
    # cleared/deleted profile or leak into a newly selected provider.
    migrate_key = legacy_settings and bool(config["modelProfiles"])
    stored_key = read_api_key(CREDENTIAL_TARGET) if migrate_key else ""
    if migrate_key and not stored_key and _is_legacy_api_key(legacy_key):
        try:
            write_api_key(legacy_key, CREDENTIAL_TARGET)
            stored_key = legacy_key
        except OSError:
            stored_key = ""

    if migrate_key:
        target = get_model_profile(config)["credentialTarget"]
        try:
            if stored_key and not read_api_key(target):
                write_api_key(stored_key, target)
        except OSError:
            pass

    if "api_key" in config and (stored_key or not _is_legacy_api_key(legacy_key)):
        config.pop("api_key", None)
    for key in ("base_url", "default_model", "request_template"):
        config.pop(key, None)
    if config != original:
        _write_json_atomic(config_path, config)
    return config


def load_full_config():
    """Load and, when necessary, atomically migrate the external config."""
    config_path = ensure_config_file()

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    return _migrate_configuration(config_path, config)


def save_config(config: dict):
    """Save non-secret settings to config.json."""
    config_path = get_config_path()
    sanitized = copy.deepcopy(config)
    sanitized["language"] = normalize_language(sanitized.get("language"))
    sanitized.pop("api_key", None)
    for key in ("base_url", "default_model", "request_template"):
        sanitized.pop(key, None)
    profiles = sanitized.get("modelProfiles")
    if not isinstance(profiles, list):
        raise ValueError("模型 Profile 列表必须是数组。")
    sanitized["modelProfiles"] = _normalize_profiles(profiles)
    active = str(sanitized.get("activeModelProfileId") or "").strip()
    if (profiles or active) and active not in {item["id"] for item in sanitized["modelProfiles"]}:
        raise ValueError("activeModelProfileId 未指向有效的模型 Profile。")
    sanitized["activeModelProfileId"] = active
    _write_json_atomic(config_path, sanitized)


def get_model_profile(config=None, profile_id=None):
    config = config if config is not None else load_full_config()
    profiles = config.get("modelProfiles") or []
    if not profiles:
        raise ModelConfigurationRequiredError()
    selected_id = str(profile_id or config.get("activeModelProfileId") or "")
    for profile in profiles:
        if isinstance(profile, dict) and str(profile.get("id") or "") == selected_id:
            return _normalize_profile(profile)
    raise ValueError(f"找不到模型 Profile：{selected_id or '未选择'}")


def get_active_model_name(config=None):
    """Return the selected model without exposing the profile schema to callers."""

    return str(get_model_profile(config).get("model") or "")


def get_api_key(config=None, profile_id=None):
    """Return the isolated Windows credential for one model profile."""
    config = config if config is not None else load_full_config()
    profile = get_model_profile(config, profile_id)
    stored_key = read_api_key(profile["credentialTarget"]).strip()
    if stored_key:
        return stored_key
    return ""
