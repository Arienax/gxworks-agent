# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Reuse already delivered public facts within a tool conversation."""
from __future__ import annotations

import copy
import hashlib
import json
from threading import RLock

from agent_runtime.tool_errors import ToolPreconditionError
from application.read_delivery import MAX_RESOLVED_BYTES, MAX_RESOLVED_TOKENS, _fits, read_limits
from application.workspace import atomic_json, read_json

RESTORE_TOOL = 'gxworks_read_context_unit'


def restore_tool_schema():
    return {'type': 'function', 'function': {'name': RESTORE_TOOL,
        'description': '批量恢复本会话 $conversation_ref 指向的正文。已能使用前文时无需恢复。',
        'parameters': {'type': 'object', 'additionalProperties': False,
            'properties': {'read_budget_tokens': {'type': 'integer', 'minimum': 1000, 'maximum': 128000,
                'description': '单次恢复交付预算；宿主输出预算至少取此值的2倍。不会减少恢复内容。'},
                'id': {'type': 'string', 'minLength': 1},
                'ids': {'type': 'array', 'minItems': 1, 'maxItems': 32, 'uniqueItems': True,
                        'items': {'type': 'string', 'minLength': 1}}},
            'required': []}}}


def _knowledge_owners(document):
    yield '/knowledge', document
    if isinstance(inputs := document.get('generation_input'), dict):
        yield '/generation_input/knowledge', inputs
    if isinstance(manual := (document.get('instruction_research') or {}).get('manual'), dict):
        yield '/instruction_research/manual/knowledge', manual
    for index, row in enumerate(document.get('results') or []):
        if isinstance(row, dict):
            yield f'/results/{index}/knowledge', row


def without_knowledge(document):
    """Keep engineering fields for the host delta independently of fact delivery."""
    result = copy.deepcopy(document)
    for _, owner in _knowledge_owners(result):
        owner.pop('knowledge', None)
    return result


def _knowledge_scope(data):
    from plc.target_identity import target_series
    document = data['document']
    model = document.get('plc_model')
    menu = target_series(model)
    return [data['delivery']['fact_version'], menu[1] if menu else model,
            document.get('target_mode') or document.get('language')]


def _unit_identity(unit, scope):
    wire = json.dumps(unit, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if any(marker in wire for marker in ('"$ref"', '"$concat"', '"$merge"', '"$conversation_ref"')):
        return None, wire
    material = wire if scope is None else json.dumps(scope, ensure_ascii=False) + wire
    return hashlib.sha256(material.encode()).hexdigest()[:24], wire


class ContextUnits:
    def __init__(self, path=None):
        self.path = path
        self.units = read_json(path) if path is not None and path.is_file() else {}
        self._lock = RLock()

    def new_facts(self, data):
        """Find new facts before the host hands a turn to the conversation."""
        scope = _knowledge_scope(data)
        with self._lock:
            additions = {}
            for _, owner in _knowledge_owners(data['document']):
                rows = owner.get('knowledge')
                if not isinstance(rows, list):
                    continue
                for unit in rows:
                    identity, _ = _unit_identity(unit, scope)
                    if identity is not None and identity not in self.units:
                        additions.setdefault(identity, unit)
            return additions

    def remember(self, additions):
        if additions:
            with self._lock:
                self.units.update(additions)
                if self.path is not None:
                    atomic_json(self.path, self.units)

    def project(self, value, *, fits=None):
        data = value.get('data') or {}
        resolved = data.get('context_schema_version') == 4
        if not resolved and (self.path is None or not isinstance(data.get('objects'), dict)):
            return value
        result = copy.deepcopy(value)
        data = result['data']
        with self._lock:
            if resolved:
                document = data['document']
                # A saved native project can report the owning CPU menu. Reuse
                # only byte-identical facts from the same menu and fact version.
                scope = _knowledge_scope(data)
                entries = ((f'{prefix}/{i}', rows, i, scope)
                           for prefix, owner in _knowledge_owners(document)
                           if isinstance(rows := owner.get('knowledge'), list)
                           for i in range(len(rows)))
            else:
                # Existing DSH conversations may still reference legacy pages.
                objects = data['objects']
                entries = ((key, objects, key, None) for key in objects)
            additions = {}
            for location, container, key, scope in entries:
                unit = container[key]
                identity, wire = _unit_identity(unit, scope)
                if identity is None:
                    continue
                known = identity in self.units or identity in additions
                if not known:
                    additions[identity] = unit
                if len(wire) >= 512:
                    if known:
                        container[key] = {'$conversation_ref': identity, 'restore_tool': RESTORE_TOOL}
                    data.setdefault('conversation_unit_ids', {})[location] = identity
            if resolved and not (fits(result) if fits is not None else _fits(result, *read_limits(value))):
                return value
            self.remember(additions)
        return result

    def restore(self, arguments, *, hosted=False):
        from agent_runtime.tool_registry import ToolRegistry
        ToolRegistry._validate_arguments(restore_tool_schema()['function']['parameters'], arguments)
        if ('id' in arguments) == ('ids' in arguments):
            raise ValueError('Supply exactly one of id or ids.')
        single = 'id' in arguments
        ids = [arguments['id']] if single else arguments['ids']
        with self._lock:
            if any(identity not in self.units for identity in ids) and self.path is not None and self.path.is_file():
                saved = read_json(self.path)
                self.units.update({identity: saved[identity] for identity in ids if identity in saved})
            missing = [identity for identity in ids if identity not in self.units]
            data = (self.units[ids[0]] if single and not missing else
                    {'units': {identity: self.units[identity] for identity in ids if identity in self.units}})
            result = {'ok': True, 'tool': RESTORE_TOOL, 'data': copy.deepcopy(data)}
            if missing:
                result['data'].update(unavailable_ids=missing)
        tokens = arguments.get('read_budget_tokens', MAX_RESOLVED_TOKENS)
        byte_limit = max(MAX_RESOLVED_BYTES, tokens * 8) if 'read_budget_tokens' in arguments else MAX_RESOLVED_BYTES
        if 'read_budget_tokens' in arguments:
            result['delivery'] = {'limits': {'estimated_tokens': tokens, 'utf8_bytes': byte_limit}}
        if not _fits(result, tokens, byte_limit):
            if hosted:
                raise ToolPreconditionError('HOST_CONTEXT_TOO_LARGE',
                    '这批资料超过本轮可交付容量，请按具体技术缺项查询。')
            required = tokens * 2
            while not _fits(result, required, max(MAX_RESOLVED_BYTES, required * 8)):
                required *= 2
            raise ToolPreconditionError('READ_BUDGET_TOO_SMALL',
                '完整资料超过本次交付预算；提高宿主输出预算后原样重试，或将 ids 分批恢复。',
                details={'required_read_budget_tokens': required, 'host_output_tokens': required * 2},
                next_actions=[{'tool': RESTORE_TOOL, 'arguments': {**arguments, 'read_budget_tokens': required}}]
                    if required <= 128000 else [])
        return result
