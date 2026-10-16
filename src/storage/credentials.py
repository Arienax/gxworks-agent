"""Model API-key targets stored in Windows Credential Manager."""

from storage.windows_credentials import delete_secret, read_secret, write_secret


CREDENTIAL_TARGET = "PLC AI Studio/GXWorks2 Ladder Generator/API Key"
PROFILE_CREDENTIAL_PREFIX = "PLC-AI-Studio/model-profile/"


def credential_target_for_profile(profile_id):
    """Return the stable Windows Credential Manager target for one profile."""
    value = str(profile_id or "").strip()
    if not value or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in value):
        raise ValueError("模型配置 ID 只能包含字母、数字、点、下划线和连字符。")
    return PROFILE_CREDENTIAL_PREFIX + value


def read_api_key(target=CREDENTIAL_TARGET):
    return read_secret(target)


def write_api_key(api_key, target=CREDENTIAL_TARGET):
    value = str(api_key or "").strip()
    if not value:
        raise ValueError("API Key 不能为空。")
    write_secret(value, target, comment="GXWorks2 梯形图生成系统 API Key", username="API Key")


def delete_api_key(target=CREDENTIAL_TARGET):
    delete_secret(target)


def has_api_key(target=CREDENTIAL_TARGET):
    return bool(read_api_key(target).strip())
