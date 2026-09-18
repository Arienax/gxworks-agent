"""Exercise credential byte handling with a fake Win32 API; no OS store access."""

import ctypes

import pytest

import storage.windows_credentials as storage


class MemoryApi:
    def __init__(self):
        self.records = {}
        self.allocated = []
        self.freed = 0

    def CredWriteW(self, pointer, flags):
        credential = ctypes.cast(pointer, storage._PCREDENTIALW).contents
        self.records[credential.TargetName] = (
            ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize),
            credential.Comment, credential.UserName,
        )
        return True

    def CredReadW(self, target, kind, flags, pointer):
        if target not in self.records:
            return False
        raw, _, _ = self.records[target]
        blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        credential = storage._CREDENTIALW()
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.CredentialBlobSize = len(raw)
        self.allocated.append((credential, blob))
        ctypes.cast(pointer, ctypes.POINTER(storage._PCREDENTIALW))[0] = ctypes.pointer(credential)
        return True

    def CredFree(self, pointer):
        self.freed += 1

    def CredDeleteW(self, target, kind, flags):
        return self.records.pop(target, None) is not None


@pytest.fixture
def memory_api(monkeypatch):
    api = MemoryApi()
    monkeypatch.setattr(storage, "_api", lambda: api)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: storage._ERROR_NOT_FOUND, raising=False)
    return api


def test_credential_targets_and_utf8_payloads_remain_separate(memory_api):
    storage.write_secret("模型 key", "model", comment="description", username="user")
    storage.write_secret('{"agent_token":"本地-token"}', "service")
    assert storage.read_secret("model") == "模型 key"
    assert storage.read_secret("service") == '{"agent_token":"本地-token"}'
    assert memory_api.records["model"][1:] == ("description", "user")
    assert memory_api.freed == 2
    storage.delete_secret("service")
    assert storage.read_secret("service") == ""
    assert storage.read_secret("model") == "模型 key"
    storage.delete_secret("service")  # An absent target is an idempotent no-op.


def test_read_frees_native_buffer_when_utf8_is_invalid(memory_api):
    memory_api.records["invalid"] = (b"\xff", "", "")
    with pytest.raises(UnicodeDecodeError):
        storage.read_secret("invalid")
    assert memory_api.freed == 1


def test_model_key_wrapper_preserves_legacy_target_and_validation(memory_api):
    import storage.credentials as credential_store

    credential_store.write_api_key("  model-key  ")
    assert credential_store.read_api_key() == "model-key"
    assert set(memory_api.records) == {credential_store.CREDENTIAL_TARGET}
    with pytest.raises(ValueError):
        credential_store.write_api_key(" ")
    credential_store.delete_api_key()
    assert not credential_store.has_api_key()
