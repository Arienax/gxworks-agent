# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Credential and filesystem-field policy shared by public boundaries.

Resource owners still select their public schema. These predicates do not turn
arbitrary internal dictionaries into public records.
"""
from __future__ import annotations

import re

CREDENTIAL_FIELDS = frozenset({
    'api_key', 'apikey', 'access_token', 'refresh_token', 'accesstoken', 'refreshtoken',
    'authorization', 'credential', 'credentials', 'credentialtarget', 'credential_target',
    'password', 'secret', 'token', 'operator_token', 'agent_token', 'gateway_token',
    'cookie', 'set_cookie', 'proxy_authorization', 'provider_configuration', 'provider_instance',
})
_CREDENTIAL_FRAGMENTS = ('api_key', 'apikey', 'credential', 'password', 'secret',
                         'access_token', 'refresh_token')
_PRIVATE_PATH = re.compile(r'(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|tmp|var|etc)/)[^\s"<>|]*')


def normalized_field(key):
    """Canonical spelling for provider metadata, independent of separators/case."""
    return re.sub(r'[^a-z0-9]', '', str(key).casefold())


_COMPACT_CREDENTIAL_FIELDS = frozenset(normalized_field(key) for key in CREDENTIAL_FIELDS)
_COMPACT_CREDENTIAL_FRAGMENTS = tuple(normalized_field(key) for key in _CREDENTIAL_FRAGMENTS)
_PROVIDER_CONTROLS = frozenset({
    'key', 'accesskey', 'auth', 'bearer', 'headers', 'extraheaders',
    'cookie', 'cookies', 'authentication', 'proxyauth',
})


def credential_field(key, *, fragments=False, any_token=False, compact=False):
    name = (normalized_field(key) if compact else
            str(key).strip().casefold().lstrip('_').replace('-', '_'))
    fields = _COMPACT_CREDENTIAL_FIELDS if compact else CREDENTIAL_FIELDS
    parts = _COMPACT_CREDENTIAL_FRAGMENTS if compact else _CREDENTIAL_FRAGMENTS
    suffixes = ('apikey', 'password') if compact else ('_api_key', '_password')
    return (name in fields or name.endswith(suffixes)
            or fragments and any(part in name for part in parts)
            or any_token and 'token' in name)


def provider_control_field(key, *, private=False, token_suffix=False):
    """Provider extensions cannot own authentication or transport controls."""
    name = normalized_field(key)
    return (credential_field(key, fragments=True, compact=True)
            or name in _PROVIDER_CONTROLS or 'authorization' in name
            or token_suffix and name.endswith('token')
            or private and (str(key).startswith('_') or name == 'privatepayload'))


def private_field(key):
    name = str(key)
    return (name.startswith('_') or name.casefold() in {'private_payload', 'file_path', 'staging_dir', 'staging_directory'}
            or credential_field(name))


def filesystem_field(key):
    return str(key).lower().endswith(('_path', '_dir', '_file'))


def redact_paths(value, *, whole=False):
    if whole and _PRIVATE_PATH.search(value):
        return '[private path]'
    return _PRIVATE_PATH.sub('[private path]', value)
