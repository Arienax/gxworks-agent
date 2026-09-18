"""Purpose-neutral Windows Credential Manager storage with explicit targets.

This module has no model configuration or default API-key target. The Windows
API is loaded only when storage is used.
"""

import ctypes
from ctypes import wintypes


_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_PCREDENTIALW = ctypes.POINTER(_CREDENTIALW)


def _api():
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_PCREDENTIALW)]
    api.CredReadW.restype = wintypes.BOOL
    api.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    api.CredDeleteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    return api


def read_secret(target):
    api = _api()
    credential_pointer = _PCREDENTIALW()
    if not api.CredReadW(str(target), _CRED_TYPE_GENERIC, 0, ctypes.byref(credential_pointer)):
        error = ctypes.get_last_error()
        if error == _ERROR_NOT_FOUND:
            return ""
        raise ctypes.WinError(error)
    try:
        credential = credential_pointer.contents
        if not credential.CredentialBlob or not credential.CredentialBlobSize:
            return ""
        return ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize).decode("utf-8")
    finally:
        api.CredFree(credential_pointer)


def write_secret(value, target, *, comment="", username=""):
    raw = str(value).encode("utf-8")
    if not raw:
        raise ValueError("Credential must not be empty")
    blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    credential = _CREDENTIALW()
    credential.Type = _CRED_TYPE_GENERIC
    credential.TargetName = str(target)
    credential.Comment = str(comment)
    credential.CredentialBlobSize = len(raw)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = str(username)
    if not _api().CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def delete_secret(target):
    if _api().CredDeleteW(str(target), _CRED_TYPE_GENERIC, 0):
        return
    error = ctypes.get_last_error()
    if error != _ERROR_NOT_FOUND:
        raise ctypes.WinError(error)
