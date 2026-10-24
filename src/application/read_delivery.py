"""Bounded, dependency-preserving delivery of already-public read documents.

The store is owned by a tool registry/session. Cursors cannot be used with a
different request, project revision, CPU or runtime fact bundle. The protocol
is independent of a Web provider's configured model window.
"""
from __future__ import annotations

import copy
import json
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
    # Index identity includes the bundle replacement epoch. No raw project or
    # private source material is exposed in the cursor or model response.
    return _binding_value((context.project_id, context.version_id, context.plc_model,
            context.project, context.version, context.analysis_source, context.program_ir,
            context.ladder, context.st_text, context.native_snapshot, context.fbd_baseline,
            index_identity(index_path())))


def _split_document(document):
    """Replace whole semantic objects by references, sharing identical values.

    Schema documents, source bodies, interface entries and facts are indivisible.
    Container maps/lists may refer to them; strings are never sliced.
    """
    objects, interned = OrderedDict(), {}
    atomic_fields = {'parameter_schema', 'model_schema', 'schema', 'output_schema', 'previous', 'current_model'}
    atomic_lists = {'facts', 'catalog', 'nodes', 'st_sources', 'fbd_callers', 'pous', 'networks'}

    def store(value):
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        if marker not in interned:
            identity = 'unit-' + str(len(objects) + 1)
            interned[marker] = identity
            objects[identity] = copy.deepcopy(value)
        return {'$ref': '#/objects/' + interned[marker]}

    def container(value):
        # Container indexes are divisible even when the complete interfaces or
        # facts they reference are not. Keep large directories from becoming a
        # single oversized list of thousands of object references.
        size = serialized_size(value)['utf8_bytes']
        if size <= 750:
            return value
        if size <= 4000:
            return store(value)
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
            return store(value)
        if isinstance(value, Mapping):
            # Each rule is a complete dependency-closed language rule packet.
            if len(path) >= 2 and path[-2] == 'rules':
                return store(value)
            if len(path) >= 2 and path[-2] in {'structures', 'call_interface', 'existing_declarations'}:
                return store(value)
            out = {k: walk(v, (*path, k)) for k, v in value.items()}
            # The container is also shareable. Its child refs are delivered first.
            return container(out)
        if isinstance(value, (list, tuple)):
            if key in atomic_lists:
                out = [store(v) for v in value]
            else:
                out = [walk(v, (*path, i)) for i, v in enumerate(value)]
            return container(out)
        if isinstance(value, str) and len(value.encode('utf-8')) > 750:
            return store(value)
        return copy.deepcopy(value)

    return walk(document), objects


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
        document, objects = _split_document(content)
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
            return {'ok': True, 'tool': tool, 'data': data}

        current = []
        for item in items:
            candidate = make_page([*current, item], len(pages), False)
            if not _fits(candidate, self.max_tokens, self.max_bytes):
                if not current:
                    raise ToolPreconditionError('READ_OBJECT_TOO_LARGE',
                        '一个完整必需对象超过单次读取上限；未截断内容，也未标记交付完成。',
                        details={'object_id': item[0], 'object_size': serialized_size(item[1]),
                                 'limits': {'estimated_tokens': self.max_tokens, 'utf8_bytes': self.max_bytes}})
                pages.append(make_page(current, len(pages), False))
                current = [item]
                if not _fits(make_page(current, len(pages), False), self.max_tokens, self.max_bytes):
                    raise ToolPreconditionError('READ_OBJECT_TOO_LARGE',
                        '一个完整必需对象超过单次读取上限；请缩小读取对象。', details={'object_id': item[0]})
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
