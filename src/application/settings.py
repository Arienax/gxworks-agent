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
from storage.config import ModelConfigurationRequiredError
from model_runtime.contract import CapabilityContract, UserModelSettings, scoped_contract, normalize_contract, credential_fingerprint
from model_runtime.legacy_migration import (
    clear_legacy_detection,
    strip_legacy_runtime_fields,
)
from model_runtime.request_policy import public_contract_settings


_SETTINGS_LOCK = threading.RLock()
_PROFILE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_PROFILE_FIELDS = {"id", "name", "model", "base_url", "contract", "user_settings", "capability_overrides"}
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
        from storage.config import get_config_path, migrate_user_settings, _normalize_profile_selection
        from shared.paths import resource_path
        from shared.i18n import normalize_language
        migrate_user_settings()  # location copy only, no credential/schema migration
        path = Path(get_config_path())
        if not path.is_file():
            path = resource_path("config.default.json")
        config = json.loads(path.read_text(encoding="utf-8-sig"))
        config["language"] = normalize_language(config.get("language"))
        return _normalize_profile_selection(config)

    @staticmethod
    def _legacy_credential(config):
        from storage.config import _is_legacy_api_key
        from storage.credentials import CREDENTIAL_TARGET, read_api_key
        if not any(key in config for key in ("api_key", "base_url", "default_model", "request_template")):
            return None
        key = read_api_key(CREDENTIAL_TARGET).strip()
        if not key and _is_legacy_api_key(config.get("api_key")):
            key = str(config["api_key"]).strip()
        return (config["activeModelProfileId"], key) if key else None

    def _key(self, config, profile):
        from storage.credentials import read_api_key
        key = read_api_key(profile["credentialTarget"]).strip()
        if not key:
            legacy = self._legacy_credential(config)
            if legacy and legacy[0] == profile["id"]:
                key = legacy[1]
        return key

    def public_settings(self):
        from storage.config import get_model_profile
        with _SETTINGS_LOCK:
            config = self.read_config()
            profiles = []
            for item in config["modelProfiles"]:
                profile = get_model_profile(config, item["id"])
                contract, user_settings = public_contract_settings(profile, self._key(config, profile))
                profiles.append({
                    "id": profile["id"], "name": profile["name"], "model": profile["model"],
                    "base_url": _base_url(profile["baseUrl"], strict=False),
                    "configured": bool(self._key(config, profile)),
                    "deletable": True,
                    "contract": self._observations().decorate(CapabilityContract.from_dict(contract)).to_dict() if contract else {},
                    "capability_overrides": _safe_options(profile.get("capabilityOverrides") or {}),
                    "user_settings": user_settings,
                })
            return {"language": config["language"], "active_profile_id": config["activeModelProfileId"], "profiles": profiles}

    @staticmethod
    def _apply_profile(chosen, values):
        if not isinstance(values, dict) or set(values) - _PROFILE_FIELDS:
            raise ValueError("Unknown model profile fields")
        if "id" in values and _profile_id(values["id"]) != chosen["id"]:
            raise ValueError("Cannot change model profile ID")

        old_identity = (
            str(chosen.get("baseUrl") or "").rstrip("/"),
            str(chosen.get("model") or ""),
        )
        for key in ("name", "model"):
            if key in values:
                value = str(values[key]).strip()
                if not value or len(value) > 256:
                    raise ValueError("Model name and profile name must be nonempty")
                chosen[key] = value
        if "base_url" in values:
            chosen["baseUrl"] = _base_url(values["base_url"], strict=True)

        if "capability_overrides" in values:
            value = values["capability_overrides"]
            if not isinstance(value, dict) or len(json.dumps(value)) > 64000:
                raise ValueError("Model option groups must be JSON objects smaller than 64 KiB")
            chosen["capabilityOverrides"] = _safe_options(value, strict=True)

        new_identity = (
            str(chosen.get("baseUrl") or "").rstrip("/"),
            str(chosen.get("model") or ""),
        )
        if old_identity != new_identity:
            strip_legacy_runtime_fields(chosen)

        if "contract" in values:
            chosen["capabilityContract"] = normalize_contract(
                _safe_options(values["contract"], strict=True)
            )
            if chosen["capabilityContract"] and scoped_contract(chosen) is None:
                raise ValueError(
                    "Contract scope changed; detect this endpoint/model/context again"
                )
            chosen["userModelSettings"] = {}
            clear_legacy_detection(chosen)
        elif chosen.get("capabilityContract") and scoped_contract(chosen) is None:
            chosen["capabilityContract"] = {}
            chosen["userModelSettings"] = {}

        if "user_settings" in values and values["user_settings"]:
            if not chosen.get("capabilityContract"):
                raise ValueError("User selections require a scoped contract")
            chosen["userModelSettings"] = UserModelSettings.from_dict(
                _safe_options(values["user_settings"], strict=True),
                CapabilityContract.from_dict(chosen["capabilityContract"]),
            ).to_dict()
        elif "user_settings" in values:
            chosen["userModelSettings"] = {}

    @staticmethod
    def _validate_binding(profile, key, *, explicit=False):
        contract = profile.get("capabilityContract")
        if not contract:
            return
        if contract["scope"]["binding"] != credential_fingerprint(key):
            if explicit:
                raise ValueError("Credential changed; detect the contract with the current key again")
            profile["capabilityContract"] = {}
            profile["userModelSettings"] = {}

    @staticmethod
    def _validate_manual(profile):
        if profile.get("capabilityOverrides"):
            from model_runtime.catalog import resolve_capabilities
            resolve_capabilities(profile)

    @staticmethod
    def _save(config, legacy, *, skip_legacy=()):
        from storage.config import save_config
        from storage.credentials import read_api_key, write_api_key
        if legacy and legacy[0] not in skip_legacy:
            old = next((p for p in config["modelProfiles"] if p["id"] == legacy[0]), None)
            if old and not read_api_key(old["credentialTarget"]).strip():
                # An explicit save may migrate the old key; a GET never does.
                write_api_key(legacy[1], old["credentialTarget"])
        save_config(config)

    def update(self, *, language=None, active_profile_id=None, profile=None, api_key=None):
        from storage.config import get_model_profile
        from storage.credentials import write_api_key
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
                self._validate_manual(selected)
                self._validate_binding(selected, api_key if api_key is not None else self._key(config, selected),
                    explicit=bool(profile.get("contract")))
                config["modelProfiles"] = [selected if p["id"] == selected["id"] else p for p in config["modelProfiles"]]
            if active_profile_id is not None:
                get_model_profile(config, _profile_id(active_profile_id))
                config["activeModelProfileId"] = active_profile_id
            skip_legacy = ()
            if api_key is not None:
                selected = get_model_profile(config, (profile or {}).get("id") or active_profile_id)
                clear_legacy_detection(selected)
                config["modelProfiles"] = [selected if p["id"] == selected["id"] else p for p in config["modelProfiles"]]
                self._validate_binding(selected, api_key, explicit=bool((profile or {}).get("contract")))
                config["modelProfiles"] = [selected if p["id"] == selected["id"] else p for p in config["modelProfiles"]]
                write_api_key(api_key, selected["credentialTarget"])
                skip_legacy = (selected["id"],)
            self._save(config, legacy, skip_legacy=skip_legacy)
            return self.public_settings()

    def create_profile(self, *, id=None, api_key=None, **values):
        from storage.config import _normalize_profile
        from storage.credentials import credential_target_for_profile, write_api_key
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
            self._validate_manual(profile)
            self._validate_binding(profile, api_key or "", explicit=bool(values.get("contract")))
            config["modelProfiles"].append(profile)
            if not config.get("activeModelProfileId"):
                config["activeModelProfileId"] = profile_id
            if api_key is not None:
                write_api_key(api_key, profile["credentialTarget"])
            self._save(config, legacy)
            return self.public_settings()

    def delete_profile(self, profile_id):
        from storage.config import get_model_profile
        from storage.credentials import delete_api_key
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
        from storage.config import get_model_profile
        from storage.credentials import delete_api_key
        with _SETTINGS_LOCK:
            config = self.read_config()
            legacy = self._legacy_credential(config)
            profile = get_model_profile(config, _profile_id(profile_id))
            clear_legacy_detection(profile)
            profile["capabilityContract"] = {}
            profile["userModelSettings"] = {}
            config["modelProfiles"] = [profile if p["id"] == profile_id else p for p in config["modelProfiles"]]
            # Persist the explicit legacy conversion so a removed key cannot
            # silently reappear from the old inline/global-credential fallback.
            self._save(config, legacy, skip_legacy=(profile_id,))
            delete_api_key(profile["credentialTarget"])
            return self.public_settings()

    @staticmethod
    def _observations():
        from storage.config import get_observations_path
        from model_runtime.observations import ObservationStore
        return ObservationStore(get_observations_path())

    def _draft(self, id=None, api_key=None, **values):
        from storage.config import get_model_profile, _normalize_profile
        from storage.credentials import credential_target_for_profile
        with _SETTINGS_LOCK:
            if id:
                config = self.read_config()
                selected = get_model_profile(config, _profile_id(id))
                old_url = selected.get("baseUrl")
                self._apply_profile(selected, values)
                key = api_key if api_key is not None else (
                    self._key(config, selected) if old_url == selected.get("baseUrl") else "")
            else:
                selected = {"id": "discovery-draft", "adapter": "openai_compatible",
                            "credentialTarget": credential_target_for_profile("discovery-draft")}
                self._apply_profile(selected, values)
                key = api_key or ""
            return _normalize_profile(selected), key

    def detect_profile(self, *, id=None, api_key=None, mode="quick", refresh=False, **values):
        """Compatibility route: local resolve or model list; never completion."""
        from application.model_detection import resolve_profile_contract, inspect_openai_compatible
        if mode not in {"list", "quick", "resolve", "deep"} or not isinstance(refresh, bool):
            raise ValueError("Invalid discovery mode")
        if mode == "deep":
            raise ValueError("Batch scanning has been removed; use explicit single-target verification")
        selected, key = self._draft(id=id, api_key=api_key, **values)
        if mode != "list" and selected["model"] != "__discover__":
            result = resolve_profile_contract(selected, key, observations=self._observations())
            return {"status": "resolved", "message": result["note"], "discovery": result}
        if not str(key).strip():
            return {"status": "failed", "message": "获取模型列表需要当前服务的 API Key。", "error_code": "missing_key"}
        from model_runtime.provider import create_provider
        try:
            result = inspect_openai_compatible(create_provider(selected, key), selected["model"], mode="list")
            return {"status": "connected", "message": result["note"], "discovery": result}
        except Exception as error:
            code = getattr(error, "code", "provider_error")
            if code not in {"authentication", "rate_limit", "timeout", "invalid_request", "unavailable", "protocol"}:
                code = "provider_error"
            return {"status": "failed", "message": "模型列表不可用；可以手动填写模型并加载本地能力配置。未发送生成请求。", "error_code": code}

    def verify_profile(self, *, target, kind, value=None, consent=False, profile):
        if consent is not True:
            raise ValueError("Explicit verification consent is required")
        from application.model_detection import resolve_profile_contract
        from model_runtime.provider import create_provider
        from model_runtime.verification import verify_one
        selected, key = self._draft(**profile)
        if not str(key).strip():
            return {"status":"failed", "message":"请配置当前服务的 API Key。", "error_code":"missing_key"}
        store = self._observations()
        resolved = resolve_profile_contract(selected, key, observations=store)
        try:
            result = verify_one(create_provider(selected,key), resolved["contract"], target,
                kind=kind,value=value,consent=consent,store=store)
        except Exception as error:
            code = getattr(error, "code", "invalid_request")
            if code not in {"authentication", "rate_limit", "timeout", "invalid_request", "unavailable", "protocol"}:
                code = "provider_error"
            return {"status":"failed", "message":"单项验证未完成或被拒绝；未自动重试，也未修改设置。", "error_code":code}
        resolved = resolve_profile_contract(selected, key, observations=store)
        resolved.update(result)
        return {"status":"connected" if result["outcome"] != "inconclusive" else "unverified",
                "message":result["note"],"discovery":resolved}

    def test_connection(self, profile_id, *, profile=None, api_key=None):
        from storage.config import get_model_profile
        from model_runtime.provider import test_model_profile
        with _SETTINGS_LOCK:
            config = self.read_config()
            selected = get_model_profile(config, _profile_id(profile_id))
            old_url = selected.get("baseUrl")
            if profile is not None:
                self._apply_profile(selected, profile)
            key = api_key if api_key is not None else (
                self._key(config, selected) if selected.get("baseUrl") == old_url else "")
            if not str(key or "").strip():
                return {"status": "failed", "message": "请先配置 API Key。", "error_code": "missing_key"}
            frozen = copy.deepcopy(selected)
        try:
            # Connection testing is deliberately a fast path. Capability
            # discovery is an explicit, potentially expensive operation behind
            # /settings/detect and must never be triggered by this button.
            message = test_model_profile(copy.deepcopy(frozen), key)
            return {"status": "connected", "message": message}
        except Exception as error:
            code = getattr(error, "code", "provider_error")
            if code == "metadata_unavailable":
                return {"status":"unverified", "message":"服务未提供模型列表；当前模型与密钥尚未验证。没有发送生成请求。", "error_code":code}
            if code not in ("authentication", "rate_limit", "timeout", "invalid_request", "unavailable"):
                code = "provider_error"
            # Provider error bodies can echo authentication data. Never publish them.
            return {"status": "failed", "message": "连接测试失败，请检查服务地址、模型和密钥。", "error_code": code}

    def model_snapshot(self):
        from storage.config import get_model_profile
        from model_runtime.provider import create_provider
        with _SETTINGS_LOCK:
            config = self.read_config()
            profile = get_model_profile(config)
            key = self._key(config, profile)
            if not key:
                raise ValueError("请先在模型设置中配置 API Key。")
            # Provider/key lives only in the worker closure, never in job JSON.
            provider = create_provider(copy.deepcopy(profile), key)
            from model_runtime.observations import request_observer
            if hasattr(provider, "observation_sink"):
                provider.observation_sink = request_observer(provider, self._observations())
            return provider, {
                "profile_id": profile["id"], "model": profile.get("model"),
                "response_language": config.get("language", "zh-CN")}
