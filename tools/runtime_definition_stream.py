# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Offline bounded owner iteration for literal schema 1/2 definition bundles.

This is a transport reader, not a semantic projection or support classifier.
It retains one stored family plus one expanded owner, with bounded metadata.
Cross-family duplicate owner checks belong to the destination's unique index.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.audit_runtime_fact_package import JSONUnits, family_entries, open_text

DEFAULT_MAX_UNIT_BYTES = 64 * 1024 * 1024


def _identity(path):
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


class _DefinitionMetadata(dict):
    """Only ordinary root metadata is serialized; binding attributes stay local."""


def inspect_definition_stream(path, max_unit_bytes=DEFAULT_MAX_UNIT_BYTES):
    if type(max_unit_bytes) is not int or max_unit_bytes <= 0:
        raise ValueError('max_unit_bytes must be a positive integer')
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError('Definition input must be a regular file')
    before = _identity(path)
    metadata = _DefinitionMetadata()
    owner_arrays, metadata_bytes = set(), 0
    with open_text(path) as stream:
        parser = JSONUnits(stream, max_unit_bytes)
        for kind, key, value in parser.fields():
            if key in {'entries', 'definitions'}:
                if kind == 'field':
                    raise ValueError('Definition owners must be an array')
                if kind == 'array_end':
                    owner_arrays.add(key)
                continue
            if kind == 'field':
                metadata[key] = value
                metadata_bytes += len(_encoded(key)) + len(_encoded(value)) + 1
            elif kind == 'item':
                metadata.setdefault(key, []).append(value)
                metadata_bytes += len(_encoded(value)) + 1
            elif kind == 'array_end':
                metadata.setdefault(key, [])
                metadata_bytes += len(_encoded(key)) + 3
            if metadata_bytes > max_unit_bytes:
                raise ValueError('Combined root metadata exceeds max-unit limit')
    if any(key in metadata for key in ('source_serialization', 'value_serialization',
                                       'source_catalog', 'value_catalog')):
        raise ValueError('Pooled transport is not accepted by this legacy streaming reader')
    version = metadata.get('schema_version')
    if type(version) is not int:
        raise ValueError('Definition schema_version must be an integer')
    if version == 1 and owner_arrays == {'entries'}:
        array_key = 'entries'
    elif (version == 2 and owner_arrays == {'definitions'}
          and metadata.get('serialization') == 'common+form_diff+model_diff-v1'
          and metadata.get('verification_inheritance') is False
          and isinstance(metadata.get('model_profiles'), dict)):
        array_key = 'definitions'
    else:
        raise ValueError('Unsupported or ambiguous definition stream format')
    if _identity(path) != before:
        raise ValueError('Definition input changed while inspecting')
    metadata._path = path
    metadata._identity = before
    metadata._array_key = array_key
    metadata._digest = hashlib.sha256(_encoded(metadata)).hexdigest()
    metadata.maximum_unit_bytes = parser.maximum
    metadata.metadata_bytes = metadata_bytes
    return metadata


def iter_definition_owners(path, metadata, max_unit_bytes=DEFAULT_MAX_UNIT_BYTES):
    """Yield exact owners; exhaust the iterator before publishing a destination."""
    if not isinstance(metadata, _DefinitionMetadata):
        raise ValueError('Use the unchanged metadata returned by inspect_definition_stream')
    if type(max_unit_bytes) is not int or max_unit_bytes <= 0:
        raise ValueError('max_unit_bytes must be a positive integer')
    path = Path(path).resolve()

    def check():
        if (path != metadata._path or _identity(path) != metadata._identity
                or hashlib.sha256(_encoded(metadata)).hexdigest() != metadata._digest):
            raise ValueError('Definition input or inspected metadata changed')

    check()
    with open_text(path) as stream:
        parser = JSONUnits(stream, max_unit_bytes)
        for kind, key, value in parser.fields():
            if kind != 'item' or key != metadata._array_key:
                continue
            owners = (value,) if metadata['schema_version'] == 1 else family_entries(
                value, metadata['model_profiles'])
            for owner in owners:
                if (not isinstance(owner, dict)
                        or any(not isinstance(owner.get(key), str) or not owner[key]
                               for key in ('opcode', 'target_model'))
                        or not isinstance(owner.get('vendor', 'mitsubishi'), str)
                        or not owner.get('vendor', 'mitsubishi')):
                    raise ValueError('Owner must use literal nonempty identities')
                if len(_encoded(owner)) > max_unit_bytes:
                    raise ValueError('Expanded owner exceeds max-unit limit')
                yield owner
        metadata.maximum_unit_bytes = max(metadata.maximum_unit_bytes, parser.maximum)
    check()
