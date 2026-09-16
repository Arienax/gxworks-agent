"""Operator model settings, with read-only legacy projection and private keys."""
from __future__ import annotations

import copy
import json
import math
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

# Public service error shared with desktop/CLI model selection.
from config_manager import ModelConfigurationRequiredError
from model_capabilities import capability_scope, normalize_parameter_support


_SETTINGS_LOCK = threading.RLock()
_PROFILE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_PROFILE_FIELDS = {"id", "name", "model", "base_url", "capabilities", "generation_defaults", "request_overrides", "parameter_support"}
_SENSITIVE_NAMES = {"key", "accesskey", "auth", "bearer", "token", "headers", "extraheaders", "cookie", "cookies", "authentication", "proxyauth"}


def _sensitive(key):
    if str(key).startswith("_"):
        return True
    key = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return key in _SENSITIVE_NAMES or key.endswith("token") or key in {"privatepayload", "providerconfiguration", "providerinstance"} or any(part in key for part in (
        "apikey", "credential", "authorization", "password", "secret", "accesstoken", "refreshtoken", "bearertoken"))


def _safe_options(value, *, strict=False, depth=0):
    """Provider extensions remain extensible, but cannot carry credentials."""
    if depth > 12:
        raise ValueError("Model options are too deeply nested")
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or _sensitive(key):
                if strict:
                    raise ValueError("Credentials and headers require the dedicated credential command")
                continue
            result[key] = _safe_options(item, strict=strict, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [_safe_options(item, strict=strict, depth=depth + 1) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("Model options must contain finite JSON values")


def _base_url(value, *, strict):
    value = str(value or "").strip()
    parsed = urlsplit(value)
    valid = parsed.scheme in ("https", "http") and parsed.hostname and not any((
        parsed.username, parsed.password, parsed.query, parsed.fragment))
    if strict and not valid:
        raise ValueError("Base URL must be an HTTP(S) URL without embedded credentials or query parameters")
    if valid:
        return value
    # Old local configuration may contain a credential in the URL; never publish it.
    if parsed.scheme in ("http", "https") and parsed.hostname:
        host = parsed.hostname
        if ":" in host:
            host = "[" + host + "]"
        if parsed.port:
            host += ":" + str(parsed.port)
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    return ""


def _profile_id(value):
    if not isinstance(value, str) or not _PROFILE_ID.fullmatch(value):
        raise ValueError("Invalid model profile ID")
    return value


class SettingsService:
    def read_config(self):
        # Reuse the desktop normalizers without its file/credential migration.
        from config_manager import get_config_path, _normalize_profile_selection
        from resource_paths import resource_path
        from i18n import normalize_language
        path = Path(get_config_path())
        if not path.is_file():
            path = resource_path("config.default.json")
        config = json.loads(path.read_text(encoding="utf-8"))
        config["language"] = normalize_language(config.get("language"))
        return _normalize_profile_selection(config)

    @staticmethod
    def _legacy_credential(config):
        from config_manager import _is_legacy_api_key
        from credential_store import CREDENTIAL_TARGET, read_api_key
        if not any(key in config for key in ("api_key", "base_url", "default_model", "request_template")):
            return None
        key = read_api_key(CREDENTIAL_TARGET).strip()
        if not key and _is_legacy_api_key(config.get("api_key")):
            key = str(config["api_key"]).strip()
        return (config["activeModelProfileId"], key) if key else None

    def _key(self, config, profile):
        from credential_store import read_api_key
        key = read_api_key(profile["credentialTarget"]).strip()
        if not key:
            legacy = self._legacy_credential(config)
            if legacy and legacy[0] == profile["id"]:
                key = legacy[1]
        return key

    def public_settings(self):
        from config_manager import get_model_profile
        with _SETTINGS_LOCK:
            config = self.read_config()
            profiles = []
            for item in config["modelProfiles"]:
                profile = get_model_profile(config, item["id"])
                profiles.append({
                    "id": profile["id"], "name": profile["name"], "model": profile["model"],
                    "base_url": _base_url(profile["baseUrl"], strict=False),
                    "configured": bool(self._key(config, profile)),
                    "deletable": True,
                    "capabilities": {key: value for key, value in profile["capabilities"].items()
                                     if not _sensitive(key) and isinstance(value, bool)},
                    "generation_defaults": _safe_options(profile["generationDefaults"]),
                    "request_overrides": _safe_options(profile["requestOverrides"]),
                    "parameter_support": _safe_options(profile.get("parameterSupport") or {})
                        if (profile.get("parameterSupport") or {}).get("scope") == capability_scope(profile) else {},
                })
            return {"language": config["language"], "active_profile_id": config["activeModelProfileId"], "profiles": profiles}

    @staticmethod
    def _apply_profile(chosen, values):
        if not isinstance(values, dict) or set(values) - _PROFILE_FIELDS:
            raise ValueError("Unknown model profile fields")
        if "id" in values and _profile_id(values["id"]) != chosen["id"]:
            raise ValueError("Cannot change model profile ID")
        old_scope = capability_scope(chosen)
        for key in ("name", "model"):
            if key in values:
                value = str(values[key]).strip()
                if not value or len(value) > 256:
                    raise ValueError("Model name and profile name must be nonempty")
                chosen[key] = value
        if "base_url" in values:
            chosen["baseUrl"] = _base_url(values["base_url"], strict=True)
        for key, stored in (("capabilities", "capabilities"), ("generation_defaults", "generationDefaults"),
                            ("request_overrides", "requestOverrides")):
            if key not in values:
                continue
            value = values[key]
            if not isinstance(value, dict) or len(json.dumps(value)) > 64000:
                raise ValueError("Model option groups must be JSON objects smaller than 64 KiB")
            value = _safe_options(value, strict=True)
            if key == "capabilities" and any(not isinstance(item, bool) for item in value.values()):
                raise ValueError("Capabilities must contain boolean values")
            if key == "generation_defaults":
                for name, upper in (("temperature", 2), ("top_p", 1)):
                    number = value.get(name)
                    if number is not None and (isinstance(number, bool) or not isinstance(number, (int, float)) or not 0 <= number <= upper):
                        raise ValueError("Sampling parameter outside supported range")
            chosen[stored] = value
        if "parameter_support" in values:
            support = normalize_parameter_support(_safe_options(values["parameter_support"], strict=True))
            if support and support["scope"] != capability_scope(chosen):
                raise ValueError("Model, endpoint or thinking options changed; detect parameters again")
            chosen["parameterSupport"] = support
        elif old_scope != capability_scope(chosen):
            chosen["parameterSupport"] = {}

    @staticmethod
    def _save(config, legacy, *, skip_legacy=()):
        from config_manager import save_config
        from credential_store import read_api_key, write_api_key
        if legacy and legacy[0] not in skip_legacy:
            old = next((p for p in config["modelProfiles"] if p["id"] == legacy[0]), None)
            if old and not read_api_key(old["credentialTarget"]).strip():
                # An explicit save may migrate the old key; a GET never does.
                write_api_key(legacy[1], old["credentialTarget"])
        save_config(config)

    def update(self, *, language=None, active_profile_id=None, profile=None, api_key=None):
        from config_manager import get_model_profile
        from credential_store import write_api_key
        with _SETTINGS_LOCK:
            config = self.read_config()
            legacy = self._legacy_credential(config)
            if language is not None:
                if language not in ("zh-CN", "en", "ja"):
                    raise ValueError("Unsupported response language")
                config["language"] = language
            if profile is not None:
                selected = get_model_profile(config, _profile_id(profile["id"]))
                self._apply_profile(selected, profile)
                config["modelProfiles"] = [selected if p["id"] == selected["id"] else p for p in config["modelProfiles"]]
            if active_profile_id is not None:
                get_model_profile(config, _profile_id(active_profile_id))
                config["activeModelProfileId"] = active_profile_id
            skip_legacy = ()
            if api_key is not None:
                selected = get_model_profile(config, (profile or {}).get("id") or active_profile_id)
                if "parameter_support" not in (profile or {}):
                    selected["parameterSupport"] = {}
                    config["modelProfiles"] = [selected if p["id"] == selected["id"] else p for p in config["modelProfiles"]]
                write_api_key(api_key, selected["credentialTarget"])
                skip_legacy = (selected["id"],)
            self._save(config, legacy, skip_legacy=skip_legacy)
            return self.public_settings()

    def create_profile(self, *, id=None, api_key=None, **values):
        from config_manager import _normalize_profile
        from credential_store import credential_target_for_profile, write_api_key
        with _SETTINGS_LOCK:
            config = self.read_config()
            legacy = self._legacy_credential(config)
            profile_id = _profile_id(id or "custom_" + uuid.uuid4().hex)
            if any(p["id"] == profile_id for p in config["modelProfiles"]):
                raise ValueError("Model profile ID already exists")
            profile = {"id": profile_id, "adapter": "openai_compatible",
                       "credentialTarget": credential_target_for_profile(profile_id)}
            self._apply_profile(profile, values)
            profile = _normalize_profile(profile)
            config["modelProfiles"].append(profile)
            if not config.get("activeModelProfileId"):
                config["activeModelProfileId"] = profile_id
            if api_key is not None:
                write_api_key(api_key, profile["credentialTarget"])
            self._save(config, legacy)
            return self.public_settings()

    def delete_profile(self, profile_id):
        from config_manager import get_model_profile
        from credential_store import delete_api_key
        with _SETTINGS_LOCK:
            config = self.read_config()
            legacy = self._legacy_credential(config)
            profile = get_model_profile(config, _profile_id(profile_id))
            config["modelProfiles"] = [p for p in config["modelProfiles"] if p["id"] != profile_id]
            if config["activeModelProfileId"] == profile_id:
                config["activeModelProfileId"] = config["modelProfiles"][0]["id"] if config["modelProfiles"] else ""
            self._save(config, legacy, skip_legacy=(profile_id,))
            delete_api_key(profile["credentialTarget"])
            return self.public_settings()

    def set_key(self, profile_id, api_key):
        return self.update(profile={"id": _profile_id(profile_id)}, api_key=api_key)

    def delete_key(self, profile_id):
        from config_manager import get_model_profile
        from credential_store import delete_api_key
        with _SETTINGS_LOCK:
            config = self.read_config()
            legacy = self._legacy_credential(config)
            profile = get_model_profile(config, _profile_id(profile_id))
            profile["parameterSupport"] = {}
            config["modelProfiles"] = [profile if p["id"] == profile_id else p for p in config["modelProfiles"]]
            # Persist the explicit legacy conversion so a removed key cannot
            # silently reappear from the old inline/global-credential fallback.
            self._save(config, legacy, skip_legacy=(profile_id,))
            delete_api_key(profile["credentialTarget"])
            return self.public_settings()

    def detect_profile(self, *, id=None, api_key=None, **values):
        """Read-only draft discovery, including the very first unsaved profile."""
        from config_manager import get_model_profile, _normalize_profile
        from credential_store import credential_target_for_profile
        from model_provider import create_provider
        from application.model_detection import inspect_openai_compatible
        with _SETTINGS_LOCK:
            if id:
                config = self.read_config()
                selected = get_model_profile(config, _profile_id(id))
                old_url = selected.get("baseUrl")
                self._apply_profile(selected, values)
                # Do not send a saved credential to a different endpoint merely
                # because the user selected another connection preset.
                key = api_key if api_key is not None else (
                    self._key(config, selected) if old_url == selected.get("baseUrl") else "")
            else:
                selected = {"id": "discovery-draft", "adapter": "openai_compatible",
                            "credentialTarget": credential_target_for_profile("discovery-draft")}
                self._apply_profile(selected, values)
                key = api_key or ""
            selected = _normalize_profile(selected)
            if not str(key).strip():
                return {"status": "failed", "message": "请先配置 API Key；更换服务地址时请重新输入密钥。", "error_code": "missing_key"}
        try:
            result = inspect_openai_compatible(create_provider(selected, key), selected["model"], selected["capabilities"])
            return {"status": "connected", "message": "模型列表与能力检测完成", "discovery": result}
        except Exception as error:
            code = getattr(error, "code", "provider_error")
            if code not in {"authentication", "rate_limit", "timeout", "invalid_request", "unavailable", "protocol"}:
                code = "provider_error"
            return {"status": "failed", "message": "检测失败，请检查兼容 API 地址、模型 ID 和密钥；模型列表不可用时请手动填写模型。", "error_code": code}

    def test_connection(self, profile_id, *, profile=None, api_key=None):
        from config_manager import get_model_profile
        from model_provider import create_provider, test_model_profile
        from application.model_detection import inspect_openai_compatible
        with _SETTINGS_LOCK:
            config = self.read_config()
            selected = get_model_profile(config, _profile_id(profile_id))
            if profile is not None:
                self._apply_profile(selected, profile)
            key = api_key if api_key is not None else self._key(config, selected)
            if not str(key or "").strip():
                return {"status": "failed", "message": "请先配置 API Key。", "error_code": "missing_key"}
            frozen = copy.deepcopy(selected)
        try:
            # Preserve the existing connection-test behavior and error mapping.
            message = test_model_profile(copy.deepcopy(frozen), key)
            try:
                provider = create_provider(frozen, key)
                inspection = inspect_openai_compatible(
                    provider,
                    str(frozen.get("model") or ""),
                    frozen.get("capabilities") or {},
                )
            except Exception:
                # Some compatible services expose a usable selected model but do
                # not support model listing or one of the optional probes.
                return {"status": "connected", "message": message}
            return {
                "status": "connected",
                # Keep the public response contract stable for one release.
                # The Web client recognizes this JSON payload and falls back to
                # ordinary text for older/non-discoverable backends.
                "message": json.dumps({
                    "kind": "model_discovery_v1",
                    **inspection,
                }, ensure_ascii=False),
            }
        except Exception as error:
            code = getattr(error, "code", "provider_error")
            if code not in ("authentication", "rate_limit", "timeout", "invalid_request", "unavailable"):
                code = "provider_error"
            # Provider error bodies can echo authentication data. Never publish them.
            return {"status": "failed", "message": "连接测试失败，请检查服务地址、模型和密钥。", "error_code": code}

    def model_snapshot(self):
        from config_manager import get_model_profile
        from model_provider import create_provider
        with _SETTINGS_LOCK:
            config = self.read_config()
            profile = get_model_profile(config)
            key = self._key(config, profile)
            if not key:
                raise ValueError("请先在模型设置中配置 API Key。")
            # Provider/key lives only in the worker closure, never in job JSON.
            return create_provider(copy.deepcopy(profile), key), {
                "profile_id": profile["id"], "model": profile.get("model"),
                "response_language": config.get("language", "zh-CN")}
