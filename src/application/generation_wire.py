# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Canonical model-message rendering for generation context budgeting.

This module owns only the model-visible message envelope. Engineering projection,
prompt policy and retrieval remain with their existing owners.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence

from knowledge.evidence import estimate_tokens


class GenerationWirePrompt(str):
    """System-prompt compatible string carrying the exact application wire packet."""

    def __new__(cls, system_prompt="", wire_packet=None):
        result = super().__new__(cls, str(system_prompt or ""))
        result.wire_packet = detached_wire_packet(wire_packet or {
            "messages": [{"role": "system", "content": str(system_prompt or "")}]
        })
        return result


def render_context_checkpoint(checkpoint):
    text = str(checkpoint or "").strip()
    if not text:
        return ""
    return (
        "\n\n# Compacted historical context\n"
        "This checkpoint preserves older conversation/intent for continuity only. "
        "It is not a confirmed engineering fact and cannot override the Confirmed "
        "project specification, current user request, structured PLC facts, or "
        "current program context.\n"
        + text
    )


CONTEXT_REFERENCE_HELP = (
    '\nStructured context can contain {"$ref":"#/path","$context_reference":true}. '
    'This references the complete object at that JSON Pointer in the same context document. '
    'Resolve it as that object; it is not omitted or missing information. '
    'Ordinary JSON Schema $ref keeps its schema-local meaning.'
)


def context_system_prompt(prompt):
    text = str(prompt or '')
    return text if CONTEXT_REFERENCE_HELP.strip() in text else text + CONTEXT_REFERENCE_HELP


def render_wire_messages(system_prompt, history=()):
    """Return the canonical system/user/assistant messages sent to a provider."""
    messages = [{"role": "system", "content": context_system_prompt(system_prompt)}]
    for raw in history or ():
        if not isinstance(raw, Mapping):
            continue
        role = raw.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = str(raw.get('content', ''))
        if role == 'user':
            try:
                payload = json.loads(content)
            except (TypeError, ValueError):
                pass
            else:
                if isinstance(payload, dict) and payload.get('context_schema_version') == 3:
                    content = structured_context_text(payload)
        messages.append({"role": role, "content": content})
    return messages


def structured_context_text(payload):
    """Keep one complete copy of repeated engineering objects on the wire."""
    seen = {}
    def walk(value, path='#'):
        if isinstance(value, (Mapping, list)):
            marker = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            if len(marker.encode('utf-8')) > 256:
                if marker in seen:
                    return {'$ref': seen[marker], '$context_reference': True}
                seen[marker] = path
        if _schema_path(path):
            # JSON Schema owns its own reference root. Share the whole schema,
            # never turn its internal nodes into references to the context.
            return copy.deepcopy(value)
        if isinstance(value, Mapping):
            return {key: walk(item, path + '/' + str(key).replace('~', '~0').replace('/', '~1'))
                    for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item, path + '/' + str(i)) for i, item in enumerate(value)]
        return copy.deepcopy(value)
    return json.dumps(walk(payload), ensure_ascii=False, separators=(',', ':'))


def _schema_path(path):
    key = str(path).rsplit('/', 1)[-1]
    return key == 'schema' or key.endswith('_schema')


def expand_context_document(document):
    """Resolve local JSON pointers for deterministic application consumers."""
    def resolve(value, visiting=frozenset(), path='#'):
        if isinstance(value, Mapping):
            if (set(value) == {'$ref', '$context_reference'} and value['$context_reference'] is True
                    and str(value['$ref']).startswith('#/')):
                pointer = value['$ref']
                if pointer in visiting:
                    raise ValueError('cyclic context reference')
                target = document
                for part in pointer[2:].split('/'):
                    key = part.replace('~1', '/').replace('~0', '~')
                    target = target[int(key)] if isinstance(target, list) else target[key]
                if _schema_path(path) or _schema_path(pointer):
                    return copy.deepcopy(target)
                return resolve(target, visiting | {pointer}, pointer)
            if _schema_path(path):
                return copy.deepcopy(value)
            return {key: resolve(item, visiting, path + '/' + str(key).replace('~', '~0').replace('/', '~1'))
                    for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item, visiting, path + '/' + str(index)) for index, item in enumerate(value)]
        return copy.deepcopy(value)
    return resolve(document)


def normalize_wire_packet(value):
    """Detach and validate a renderer result without interpreting its content."""
    messages = value.get("messages") if isinstance(value, Mapping) else value
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        raise TypeError("wire renderer must return model messages")
    normalized = []
    for item in messages:
        if not isinstance(item, Mapping):
            raise TypeError("wire message must be an object")
        role = str(item.get("role") or "")
        if role not in {"system", "user", "assistant"}:
            raise ValueError("unsupported wire message role")
        normalized.append({"role": role, "content": str(item.get("content", ""))})
    if not normalized or normalized[0]["role"] != "system":
        raise ValueError("generation wire must start with a system message")
    return {"messages": normalized}


def wire_payload_text(packet):
    normalized = normalize_wire_packet(packet)
    return json.dumps(
        normalized["messages"],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def wire_token_estimate(packet):
    return estimate_tokens(wire_payload_text(packet))


def wire_sha256(packet):
    return hashlib.sha256(wire_payload_text(packet).encode("utf-8")).hexdigest()


def detached_wire_packet(packet):
    return copy.deepcopy(normalize_wire_packet(packet))
