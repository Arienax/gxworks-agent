# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Bounded, dependency-preserving delivery of already-public read documents.

The store is owned by a tool registry/session. Cursors cannot be used with a
different request, project revision, CPU or runtime fact bundle. The protocol
is independent of a Web provider's configured model window.
"""
from __future__ import annotations

import copy
import json
import re
import threading
import uuid
from collections import OrderedDict
from collections.abc import Mapping

from agent_runtime.tool_errors import ToolPreconditionError
from knowledge.evidence import estimate_tokens

PAGED_READ_TOOLS = frozenset({'search_plc_manual', 'get_generation_context', 'get_fbd_catalog'})
MAX_READ_TOKENS = 6000
MAX_READ_BYTES = 32 * 1024
DELIVERY_VERSION = 1

# The registered tool selects its business result contract before projection.
# These are transport shapes; PLC and source validation stay with their owners.
READ_RESULT_SCHEMAS = {
    'search_plc_manual': {'type': 'object', 'properties': {
        'results': {'type': 'array', 'items': {'type': 'object'}},
        'coverage': {'type': 'object'},
    }},
    'get_fbd_catalog': {'type': 'object', 'properties': {
        'nodes': {'type': 'array', 'items': {'type': 'object'}},
        **{key: {'type': ['object', 'null']} for key in
           ('parameter_model', 'empty_parameter_model', 'parameter_schema', 'empty_model')},
    }},
    'get_generation_context': {'type': 'object', 'properties': {
        **{key: {'type': ['object', 'null']} for key in
           ('current_model', 'catalog', 'confirmed_spec', 'output_contract',
            'generation_handoff', 'verification', 'validation')},
        'generation_input': {'type': 'object', 'properties': {
            'previous': {'type': ['object', 'null']},
            'catalog': {'type': 'array', 'items': {'type': 'object'}},
            **{key: {'type': ['object', 'null']} for key in
               ('source_context', 'parameter_schema', 'existing_declarations', 'call_interface', 'structures')},
        }},
    }},
}


class PreparedReadEnvelope(dict):
    """A public document paginated by its registered server-side owner."""


def received_read_envelope(tool, envelope, *, previous_objects=None):
    """Validate a paging response on the explicit application-service route.

    Called by the transport owner for registered read tools, never by a payload
    marker. Reconstruct the available document before applying its field owners;
    an object table received over HTTP is not an internal prepared result.
    """
    if tool not in PAGED_READ_TOOLS or not isinstance(envelope, Mapping):
        raise ValueError('Not a registered read response')
    if set(envelope) != {'ok', 'tool', 'data'} or envelope['ok'] is not True or envelope['tool'] != tool:
        raise ValueError('Invalid read response owner')
    data = envelope['data']
    if not isinstance(data, Mapping) or set(data) != {'context_schema_version', 'document', 'objects', 'delivery'}:
        raise ValueError('Invalid read response shape')
    if type(data['context_schema_version']) is not int or data['context_schema_version'] != 3:
        raise ValueError('Unsupported read response version')
    root, delivery = data['document'], data['delivery']
    if (not isinstance(root, Mapping) or set(root) != {'$ref'}
            or not isinstance(root['$ref'], str) or not re.fullmatch(r'#/objects/unit-[1-9][0-9]*', root['$ref'])
            or not isinstance(data['objects'], Mapping) or not isinstance(delivery, Mapping)):
        raise ValueError('Invalid read response objects')
    if (delivery.get('version') != DELIVERY_VERSION or type(delivery.get('page')) is not int
            or delivery['page'] < 0 or type(delivery.get('complete')) is not bool
            or not isinstance(delivery.get('document_id'), str) or not delivery['document_id']
            or delivery.get('scope') != 'read_sequence'):
        raise ValueError('Invalid read response sequence')
    followup = delivery.get('next')
    if delivery['complete']:
        if followup is not None:
            raise ValueError('Completed read response has a continuation')
    elif (not isinstance(followup, Mapping) or followup.get('tool') != tool
            or not isinstance(followup.get('arguments'), Mapping)
            or followup['arguments'].get('cursor') != delivery['document_id'] + ':' + str(delivery['page'] + 1)):
        raise ValueError('Invalid read response continuation')
    from agent_runtime.runtime import _model_tool_value, _public_value
    if _public_value(delivery) != delivery:
        raise ValueError('Private read response metadata')
    objects = dict(previous_objects or {})
    for key, value in data['objects'].items():
        if not isinstance(key, str) or not re.fullmatch(r'unit-[1-9][0-9]*', key):
            raise ValueError('Invalid read object identity')
        if key in objects and objects[key] != value:
            raise ValueError('Read object changed during delivery')
        objects[key] = value
    used, missing = set(), set()

    def resolve(value, visiting=frozenset(), schema=False):
        if isinstance(value, Mapping):
            if set(value) == {'$ref'} and isinstance(value['$ref'], str) and value['$ref'].startswith('#/objects/'):
                key = value['$ref'].removeprefix('#/objects/')
                if key in visiting:
                    raise ValueError('Cyclic read reference')
                if key not in objects:
                    missing.add(key)
                    return value
                used.add(key)
                return copy.deepcopy(objects[key]) if schema else resolve(objects[key], visiting | {key})
            if schema:
                return copy.deepcopy(value)
            for operator in ('$merge', '$concat'):
                if set(value) != {operator}:
                    continue
                parts = resolve(value[operator], visiting)
                if isinstance(parts, Mapping) and set(parts) == {'$ref'}:
                    return {} if operator == '$merge' else []
                if not isinstance(parts, list):
                    raise ValueError('Invalid read container')
                result = {} if operator == '$merge' else []
                for part in parts:
                    if isinstance(part, Mapping) and set(part) == {'$ref'}:
                        continue  # This container part belongs to a later page.
                    if operator == '$merge':
                        if not isinstance(part, Mapping) or result.keys() & part.keys():
                            raise ValueError('Invalid or overlapping read map parts')
                        result.update(part)
                    else:
                        if not isinstance(part, list):
                            raise ValueError('Invalid read list part')
                        result.extend(part)
                return result
            return {key: resolve(item, visiting, schema=key == 'schema' or str(key).endswith('_schema'))
                    for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item, visiting) for item in value]
        return copy.deepcopy(value)

    document = resolve(root)
    if used != set(objects) or delivery['complete'] and missing:
        raise ValueError('Unbound or missing read objects')
    if not isinstance(document, Mapping) or _model_tool_value(tool, document) != document:
        raise ValueError('Private fields in read objects')
    return PreparedReadEnvelope(copy.deepcopy(envelope))


class ReadDeliveryReceiver:
    """Keep public page objects only for the matching service read sequence."""

    def __init__(self, capacity=32):
        self.capacity = capacity
        self._documents = OrderedDict()

    def receive(self, tool, envelope, *, binding, cursor=None):
        data = envelope.get('data', {})
        delivery = data.get('delivery', {}) if isinstance(data, Mapping) else {}
        if not isinstance(delivery, Mapping):
            raise ValueError('Invalid read delivery')
        identity, page = delivery.get('document_id'), delivery.get('page')
        if not isinstance(identity, str) or type(page) is not int:
            raise ValueError('Invalid read delivery sequence')
        previous = self._documents.get(identity)
        if page and (previous is None or previous['binding'] != binding
                     or previous['root'] != data.get('document')
                     or page > previous['page'] + 1 or cursor != identity + ':' + str(page)):
            raise ValueError('Read continuation has no matching first page')
        if previous is not None and previous['binding'] != binding:
            raise ValueError('Read document belongs to another request')
        objects = previous['objects'] if previous is not None else {}
        result = received_read_envelope(tool, envelope, previous_objects=objects)
        self._documents[identity] = {'binding': copy.deepcopy(binding), 'root': data['document'],
            'page': max(page, previous['page'] if previous else 0),
            'objects': {**objects, **copy.deepcopy(data['objects'])}}
        self._documents.move_to_end(identity)
        while len(self._documents) > self.capacity:
            self._documents.popitem(last=False)
        return result



def serialized_size(value):
    text = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return {'estimated_tokens': estimate_tokens(text), 'utf8_bytes': len(text.encode('utf-8'))}


def _fits(value, tokens, byte_limit):
    # MCP carries one JSON text block. Count its JSON escaping and enclosing
    # fields as well; the page body is not copied into structuredContent again.
    from shared.project_info import MCP_SERVER_INFO
    size = serialized_size({'_meta': {'io.modelcontextprotocol/serverInfo': MCP_SERVER_INFO},
                            'resultType': 'complete', 'content': [{'type': 'text', 'text': json.dumps(
        value, ensure_ascii=False, separators=(',', ':'))}], 'isError': False})
    return size['estimated_tokens'] <= tokens and size['utf8_bytes'] <= byte_limit


def _binding_value(value):
    if isinstance(value, Mapping):
        return tuple((str(key), _binding_value(item)) for key, item in sorted(value.items(), key=lambda row: str(row[0])))
    if isinstance(value, (list, tuple)):
        return tuple(_binding_value(item) for item in value)
    if value is None or isinstance(value, (str, bytes, bool, int, float)):
        return value
    # UI widgets/callbacks are not engineering revision data, and a deepcopy of
    # an opaque object has a new identity even when the project did not change.
    return ('opaque', type(value).__module__, type(value).__qualname__)


def read_binding(context):
    from knowledge.index_access import index_path, index_identity
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    owner_path = DEFAULT_INSTRUCTION_REGISTRY.selected_owner_database_path()
    # Index identity includes the bundle replacement epoch. No raw project or
    # private source material is exposed in the cursor or model response.
    return _binding_value((context.project_id, context.version_id, context.plc_model,
            context.project, context.version, context.analysis_source, context.program_ir,
            context.ladder, context.st_text, context.sfc_graph,
            context.read_revision if not context.version_id else None,
            index_identity(index_path()), index_identity(owner_path),
            getattr(DEFAULT_INSTRUCTION_REGISTRY, 'fact_bundle_version', None)))


def _split_document(document):
    """Replace whole semantic objects by references, sharing identical values.

    Schema documents, source bodies, interface entries and facts are indivisible.
    Container maps/lists may refer to them; strings are never sliced.
    """
    objects, interned, locations = OrderedDict(), {}, {}
    atomic_fields = {'parameter_schema', 'model_schema', 'schema', 'output_schema'}
    atomic_lists = {'facts', 'catalog', 'nodes', 'st_sources', 'fbd_callers', 'pous', 'networks'}

    def store(value, location=()):
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        if marker not in interned:
            identity = 'unit-' + str(len(objects) + 1)
            interned[marker] = identity
            objects[identity] = copy.deepcopy(value)
            locations[identity] = list(location)
        return {'$ref': '#/objects/' + interned[marker]}

    def container(value, path=()):
        # Container indexes are divisible even when the complete interfaces or
        # facts they reference are not. Keep large directories from becoming a
        # single oversized list of thousands of object references.
        size = serialized_size(value)['utf8_bytes']
        if size <= 750:
            return value
        if size <= 4000:
            return store(value, path)
        chunks, current = [], {} if isinstance(value, Mapping) else []
        entries = list(value.items()) if isinstance(value, Mapping) else list(value)
        for entry in entries:
            candidate = {**current, entry[0]: entry[1]} if isinstance(value, Mapping) else [*current, entry]
            if current and serialized_size(candidate)['utf8_bytes'] > 4000:
                chunks.append(store(current))
                current = {entry[0]: entry[1]} if isinstance(value, Mapping) else [entry]
            else:
                current = candidate
        if current:
            chunks.append(store(current))
        return store({'$merge' if isinstance(value, Mapping) else '$concat': container(chunks)})

    def walk(value, path=()):
        key = str(path[-1]) if path else ''
        if (key in atomic_fields or key.endswith('_schema')) and value is not None:
            return store(value, path)
        if len(path) >= 2 and path[-2] in {'structures', 'call_interface', 'existing_declarations'}:
            return store(value, path)
        if isinstance(value, Mapping):
            # Each rule is a complete dependency-closed language rule packet.
            if len(path) >= 2 and path[-2] == 'rules':
                return store(value, path)
            if len(path) >= 2 and path[-2] in {'structures', 'call_interface', 'existing_declarations'}:
                return store(value, path)
            out = {k: walk(v, (*path, k)) for k, v in value.items()}
            # The container is also shareable. Its child refs are delivered first.
            return container(out, path)
        if isinstance(value, (list, tuple)):
            if key in atomic_lists:
                out = [store(v, (*path, index)) for index, v in enumerate(value)]
            else:
                out = [walk(v, (*path, i)) for i, v in enumerate(value)]
            return container(out, path)
        if isinstance(value, str) and len(value.encode('utf-8')) > 750:
            return store(value, path)
        return copy.deepcopy(value)

    root = store(walk(document))
    # Parents establish field ownership before their children cross a service
    # boundary. Assembly remains independent of object order.
    ordered, expanded = OrderedDict(), set()
    def visit(value, schema=False):
        if isinstance(value, Mapping):
            if set(value) == {'$ref'} and isinstance(value['$ref'], str) and value['$ref'].startswith('#/objects/'):
                key = value['$ref'].removeprefix('#/objects/')
                if key not in ordered:
                    ordered[key] = objects[key]
                if not schema and key not in expanded:
                    expanded.add(key)
                    visit(objects[key])
            elif not schema:
                for key, item in value.items():
                    visit(item, schema=key == 'schema' or str(key).endswith('_schema'))
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(root)
    return root, ordered, locations


class ReadDeliveryStore:
    def __init__(self, *, capacity=32, max_tokens=MAX_READ_TOKENS, max_bytes=MAX_READ_BYTES):
        self.capacity, self.max_tokens, self.max_bytes = capacity, max_tokens, max_bytes
        self._documents = OrderedDict()
        self._lock = threading.RLock()

    def resume(self, tool, arguments, binding):
        cursor = str(arguments.get('cursor') or '')
        identity, separator, page = cursor.rpartition(':')
        with self._lock:
            saved = self._documents.get(identity)
            request = {k: v for k, v in arguments.items() if k != 'cursor'}
            if (not separator or not page.isdigit() or saved is None or saved['tool'] != tool
                    or saved['arguments'] != request or saved['binding'] != binding
                    or not 0 <= int(page) < len(saved['pages'])):
                raise ToolPreconditionError('STALE_READ_CURSOR',
                    '读取游标已失效或查询、CPU、工程版本、事实库发生变化；请去掉 cursor 重新读取。')
            self._documents.move_to_end(identity)
            return copy.deepcopy(saved['pages'][int(page)])

    def prepare(self, envelope, arguments, binding):
        tool = envelope['tool']
        content = copy.deepcopy(envelope['data'])
        coverage_targets = []
        for row in _coverage_rows(content):
            if row not in coverage_targets:
                coverage_targets.append(copy.deepcopy(row))
            row['delivery'] = ('not_available' if row.get('corpus') == 'absent'
                               else 'see_delivery_complete')
        document, objects, locations = _split_document(content)
        identity = 'read-' + uuid.uuid4().hex
        request = {k: copy.deepcopy(v) for k, v in arguments.items() if k != 'cursor'}
        items, pages = list(objects.items()), []

        def make_page(values, index, complete):
            data = {'context_schema_version': 3, 'document': document, 'objects': dict(values),
                    'delivery': {'version': DELIVERY_VERSION, 'document_id': identity,
                        'page': index, 'complete': complete,
                        'scope': 'read_sequence',
                        'limits': {'estimated_tokens': self.max_tokens, 'utf8_bytes': self.max_bytes},
                        'next': None if complete else {'tool': tool,
                            'arguments': {**request, 'cursor': identity + ':' + str(index + 1)}},
                        'assembly': 'Collect objects from every page, then resolve $ref from document. '
                                    'References may be pending until delivery.complete=true. Resolve $concat by joining its referenced lists, '
                                    '$merge by merging its referenced maps; these divide containers, never sources/facts. '
                                    'A target delivery=see_delivery_complete is pending until this sequence is complete.'}}
            if coverage_targets:
                # Existence/completeness describe the resolved corpus. Delivery
                # is a separate property of this read sequence.
                data['delivery']['coverage'] = {
                    'targets': len(coverage_targets),
                    'present': sum(row.get('corpus') == 'present' for row in coverage_targets),
                    'absent': sum(row.get('corpus') == 'absent' for row in coverage_targets),
                    'delivery': 'delivered' if complete else 'pending',
                }
            return PreparedReadEnvelope(ok=True, tool=tool, data=data)

        current = []
        for item in items:
            candidate = make_page([*current, item], len(pages), False)
            if not _fits(candidate, self.max_tokens, self.max_bytes):
                if not current:
                    raise ToolPreconditionError('READ_OBJECT_TOO_LARGE',
                        '一个完整必需对象超过单次读取上限；未截断内容，也未标记交付完成。',
                        details={'object_id': item[0], 'object_location': locations[item[0]], 'object_size': serialized_size(item[1]),
                                 'limits': {'estimated_tokens': self.max_tokens, 'utf8_bytes': self.max_bytes}})
                pages.append(make_page(current, len(pages), False))
                current = [item]
                if not _fits(make_page(current, len(pages), False), self.max_tokens, self.max_bytes):
                    raise ToolPreconditionError('READ_OBJECT_TOO_LARGE',
                        '一个完整必需对象超过单次读取上限；请缩小读取对象。', details={'object_id': item[0], 'object_location': locations[item[0]]})
            else:
                current.append(item)
        pages.append(make_page(current, len(pages), True))
        # Include the complete final public envelope and delivery metadata, not
        # just the document body. No source or fact is truncated to make it fit.
        if any(not _fits(page, self.max_tokens, self.max_bytes) for page in pages):
            raise ToolPreconditionError('READ_OBJECT_TOO_LARGE', '查询参数或必需说明超过单次读取上限。')
        with self._lock:
            self._documents[identity] = {'tool': tool, 'arguments': request,
                'binding': copy.deepcopy(binding), 'pages': pages}
            while len(self._documents) > self.capacity:
                self._documents.popitem(last=False)
        return copy.deepcopy(pages[0])


def assemble_read_pages(pages):
    """Reconstruct a complete read document for deterministic application clients."""
    pages = list(pages)
    if not pages or not pages[-1]['data']['delivery']['complete']:
        raise ValueError('read delivery incomplete')
    identity = pages[0]['data']['delivery']['document_id']
    objects = {}
    for index, page in enumerate(pages):
        delivery = page['data']['delivery']
        if delivery['document_id'] != identity or delivery['page'] != index:
            raise ValueError('read pages belong to different documents or are out of order')
        objects.update(page['data']['objects'])

    def resolve(value, visiting=frozenset(), schema=False):
        if isinstance(value, Mapping):
            if set(value) == {'$ref'} and value['$ref'].startswith('#/objects/'):
                key = value['$ref'].removeprefix('#/objects/')
                if key in visiting or key not in objects:
                    raise ValueError('missing or cyclic read reference: ' + key)
                if schema:
                    return copy.deepcopy(objects[key])
                return resolve(objects[key], visiting | {key})
            if schema:
                return copy.deepcopy(value)
            if set(value) == {'$concat'}:
                return [item for part in resolve(value['$concat'], visiting) for item in part]
            if set(value) == {'$merge'}:
                return {key: item for part in resolve(value['$merge'], visiting) for key, item in part.items()}
            return {k: resolve(v, visiting, schema=k == 'schema' or str(k).endswith('_schema'))
                    for k, v in value.items()}
        if isinstance(value, list):
            return [resolve(v, visiting) for v in value]
        return copy.deepcopy(value)
    result = resolve(pages[0]['data']['document'])
    for row in _coverage_rows(result):
        if row.get('delivery') == 'see_delivery_complete':
            row['delivery'] = 'delivered'
    return result


def _coverage_rows(document):
    # Only application-owned coverage fields. Identically named members of a
    # user schema, declaration or source object are engineering data.
    for owner in (document, document.get('generation_input')):
        if not isinstance(owner, Mapping):
            continue
        for key in ('coverage', 'knowledge_coverage'):
            coverage = owner.get(key)
            rows = coverage.get('targets') if isinstance(coverage, Mapping) else None
            if isinstance(rows, list):
                yield from (row for row in rows if isinstance(row, dict)
                            and 'kind' in row and 'target' in row)
