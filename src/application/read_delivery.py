# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Bounded, dependency-preserving delivery of already-public read documents.

The store is owned by a tool registry/session. Cursors cannot be used with a
different request, project revision, CPU or runtime fact bundle. The protocol
is independent of a Web provider's configured model window.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import uuid
from collections import OrderedDict
from collections.abc import Mapping

from agent_runtime.tool_errors import ToolPreconditionError
from knowledge.evidence import estimate_tokens

PAGED_READ_TOOLS = frozenset({'search_plc_manual', 'get_generation_context', 'get_fbd_catalog', 'get_design_context'})
REVIEWED_READ_TOOLS = PAGED_READ_TOOLS - {'get_fbd_catalog'}
_CONTEXT_READ_TOOLS = frozenset({'get_design_context', 'get_generation_context'})
MAX_READ_TOKENS = 6000
MAX_READ_BYTES = 32 * 1024
# Leave room for the host's tool wrapper under Codex's default 10k output
# budget. These limits apply to the escaped MCP envelope, not just its body.
MAX_RESOLVED_TOKENS = 6000
MAX_RESOLVED_BYTES = 32 * 1024
DELIVERY_VERSION = 1

# The registered tool selects its business result contract before projection.
# These are transport shapes; PLC and source validation stay with their owners.
READ_RESULT_SCHEMAS = {
    'get_design_context': {'type': 'object', 'required': ['analysis_id', 'analysis_instructions', 'knowledge'],
                           'properties': {'analysis_instructions': {'type': 'string'}, 'knowledge': {'type': 'array'}}},
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


def received_read_envelope(tool, envelope, *, previous_objects=None, requested_budget=None):
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
    if isinstance(data, Mapping) and data.get('context_schema_version') == 4:
        from agent_runtime.runtime import _model_tool_value, _public_value
        if set(data) != {'context_schema_version', 'document', 'delivery'}:
            raise ValueError('Invalid resolved read shape')
        document, delivery = data['document'], data['delivery']
        tokens, byte_limit = MAX_RESOLVED_TOKENS, MAX_RESOLVED_BYTES
        if type(requested_budget) is int and 1000 <= requested_budget <= 128000:
            tokens = requested_budget
            byte_limit = max(byte_limit, tokens * 8)
        if (not isinstance(document, Mapping) or not isinstance(delivery, Mapping)
                or delivery.get('version') != DELIVERY_VERSION or delivery.get('scope') != 'read_sequence'
                or type(delivery.get('page')) is not int or delivery['page'] < 0
                or type(delivery.get('complete')) is not bool
                or not isinstance(delivery.get('document_id'), str) or not delivery['document_id']
                or not isinstance(delivery.get('fact_version'), str)
                or _public_value(delivery) != delivery or _model_tool_value(tool, document) != document
                or not _fits(envelope, tokens, byte_limit)):
            raise ValueError('Invalid or private resolved read document')
        follow = delivery.get('next')
        if delivery['complete']:
            if follow is not None:
                raise ValueError('Completed read response has a continuation')
        elif (not isinstance(follow, Mapping) or follow.get('tool') != tool
              or not isinstance(follow.get('arguments'), Mapping)
              or follow['arguments'].get('cursor') != delivery['document_id'] + ':' + str(delivery['page'] + 1)):
            raise ValueError('Invalid read response continuation')
        return PreparedReadEnvelope(copy.deepcopy(envelope))
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

    def receive(self, tool, envelope, *, binding, cursor=None, query_arguments=None):
        data = envelope.get('data', {})
        resolved = isinstance(data, Mapping) and data.get('context_schema_version') == 4
        delivery = data.get('delivery', {}) if isinstance(data, Mapping) else {}
        if not isinstance(delivery, Mapping):
            raise ValueError('Invalid read delivery')
        identity, page = delivery.get('document_id'), delivery.get('page')
        if not isinstance(identity, str) or type(page) is not int:
            raise ValueError('Invalid read delivery sequence')
        previous = self._documents.get(identity)
        query = query_arguments or {}
        if page and previous is not None:
            expected = previous['query']
            if any(key not in expected or expected[key] != value for key, value in query.items()):
                raise ValueError('Read continuation changed its query')
        if page and (previous is None or previous['binding'] != binding
                     or previous['version'] != data.get('context_schema_version')
                     or (not resolved and previous['root'] != data.get('document'))
                     or page > previous['page'] + 1 or cursor != identity + ':' + str(page)):
            raise ValueError('Read continuation has no matching first page')
        if previous is not None and previous['binding'] != binding:
            raise ValueError('Read document belongs to another request')
        objects = previous['objects'] if previous is not None else {}
        request = previous['query'] if page and previous is not None else query
        result = received_read_envelope(tool, envelope, previous_objects=objects,
                                        requested_budget=request.get('read_budget_tokens'))
        self._documents[identity] = {'binding': copy.deepcopy(binding), 'root': data['document'],
            'version': data['context_schema_version'],
            'page': max(page, previous['page'] if previous else 0),
            'query': copy.deepcopy(previous['query'] if previous else query),
            'objects': {**objects, **copy.deepcopy(data.get('objects', {}))}}
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


def read_limits(envelope):
    data = envelope.get('data') or {}
    if data.get('context_schema_version') == 4 or envelope.get('tool') == 'gxworks_read_context_unit':
        limits = (data.get('delivery') or envelope.get('delivery') or {}).get('limits') or {}
        return (limits.get('estimated_tokens', MAX_RESOLVED_TOKENS),
                limits.get('utf8_bytes', MAX_RESOLVED_BYTES))
    return MAX_READ_TOKENS, MAX_READ_BYTES


def _coverage_presence(row):
    if type(row.get('identity_found')) is bool:
        return row['identity_found']
    return {'present': True, 'absent': False}.get(row.get('corpus'))


def resolved_read(envelope, *, identity, fact_version):
    """Build the direct wire format from a public, fully projected document."""
    content = copy.deepcopy(envelope['data'])
    targets = list(_coverage_rows(content))
    for row in targets:
        row['delivery'] = 'not_available' if _coverage_presence(row) is False else 'delivered'
    return PreparedReadEnvelope(ok=True, tool=envelope['tool'], data={
        'context_schema_version': 4, 'document': content,
        'delivery': {'version': DELIVERY_VERSION, 'document_id': identity, 'page': 0,
                     'scope': 'read_sequence', 'complete': True, 'next': None,
                     'fact_version': fact_version,
                     **({'coverage': {'targets': len(targets),
                         'present': sum(_coverage_presence(row) is True for row in targets),
                         'absent': sum(_coverage_presence(row) is False for row in targets),
                         'delivery': 'delivered'}} if targets else {}),
                     'limits': {'estimated_tokens': MAX_RESOLVED_TOKENS, 'utf8_bytes': MAX_RESOLVED_BYTES}},
    })


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


def read_binding(context, *, durable=False):
    from plc.specification.fact_citations import fact_binding
    # Index identity includes the bundle replacement epoch. No raw project or
    # private source material is exposed in the cursor or model response.
    return _binding_value((context.project_id, context.version_id, context.plc_model,
            context.project, context.version, context.analysis_source, context.program_ir,
            context.ladder, context.st_text, context.sfc_graph, context.direct_input,
            context.read_revision if not durable and not context.version_id else None,
            *fact_binding()))


def read_review_binding(context):
    """Engineering identity for submission, without the process-local cursor epoch."""
    from agent_runtime.tool_context import _generation_spec
    from plc.specification.fact_citations import fact_binding
    document = context.project.get('_spec_document') or {}
    native = context.native_snapshot
    payload = _binding_value((
        context.project_id, context.version_id, context.plc_model,
        (context.version or {}).get('target_mode') or context.project.get('target_mode'),
        _generation_spec(context), document.get('draft_revision'),
        context.program_ir, context.ladder, context.st_text, context.sfc_graph, context.direct_input,
        hashlib.sha256(native).hexdigest() if isinstance(native, bytes) else None,
        *fact_binding(),
    ))
    return hashlib.sha256(repr(payload).encode('utf-8')).hexdigest()


def manual_binding(context, language=None):
    """Instruction facts do not depend on a saved program or project revision."""
    from plc.specification.fact_citations import fact_binding
    from plc.target_identity import reference_target_identity
    language = language or (context.version or {}).get('target_mode') or context.project.get('target_mode') or 'ladder'
    return (reference_target_identity(context.plc_model) or context.plc_model,
            language.lower(), *fact_binding())


def merge_read_documents(left, right):
    """Application consumers join readable sections; models simply read them."""
    result = copy.deepcopy(left)
    for key, value in right.items():
        if key not in result:
            result[key] = copy.deepcopy(value)
        elif isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_read_documents(result[key], value)
        elif isinstance(result[key], list) and isinstance(value, list):
            result[key].extend(copy.deepcopy(value))
        elif result[key] != value:
            raise ValueError('Conflicting resolved document sections')
    return result


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
    def __init__(self, *, capacity=32, max_tokens=MAX_READ_TOKENS, max_bytes=MAX_READ_BYTES,
                 receipt_store=None):
        self.capacity, self.max_tokens, self.max_bytes = capacity, max_tokens, max_bytes
        self._documents = OrderedDict()
        self._lock = threading.RLock()
        self._receipt_store = receipt_store

    @property
    def durable(self):
        return self._receipt_store is not None

    @staticmethod
    def _binding_key(binding):
        return hashlib.sha256(repr(binding).encode('utf-8')).hexdigest()

    def _resume_pending(self, identity, key):
        if self._receipt_store is None:
            return None
        saved = self._receipt_store.read('pending_reads', identity)
        if saved is None or (key is not None and saved['binding'] != key):
            return None
        progress = self._receipt_store.read('read_progress', identity)
        saved['last_read'] = progress['last_read'] if progress else 0
        saved['manual_scope'] = _binding_value(saved['manual_scope'])
        saved['fact_scope'] = _binding_value(saved.get('fact_scope'))
        return saved

    def _save_completed_review(self, identity, document):
        if (self._receipt_store is None
                or document['tool'] not in REVIEWED_READ_TOOLS):
            return
        review = {key: document[key] for key in
                  ('tool', 'review_binding', 'manual_scope', 'fact_scope',
                   'research', 'receipt', 'model_facts', 'citations')}
        review['project_id'] = document.get('project_id', (document.get('fact_scope') or ('',))[0])
        self._receipt_store.write('reads', identity, review)
        if document['tool'] == 'get_generation_context' and document['receipt']:
            self._receipt_store.write('read_ids', document['receipt'], {'document_id': identity})
            self._receipt_store.remember_read_binding(document['review_binding'], identity, document['receipt'])
        if document['fact_scope'] and document['citations']:
            self._receipt_store.remember_fact_read(document['fact_scope'], identity, document['citations'])

    def _load_completed_review(self, identity):
        if self._receipt_store is None:
            return None
        saved = self._receipt_store.read('reads', identity)
        if saved is None:
            return None
        return {**saved, 'manual_scope': _binding_value(saved['manual_scope']),
                'fact_scope': _binding_value(saved.get('fact_scope')), 'pages': [], 'last_read': -1}

    def finish_host_read(self, tool, identity, project_id, *, accepted):
        """Publish a read only once the host can deliver the assembled document."""
        with self._lock:
            saved = self._documents.get(identity) or self._resume_pending(identity, None)
            if saved is None and accepted:
                saved = self._load_completed_review(identity)
            if (saved is None or saved['tool'] != tool or saved.get('project_id') != project_id
                    or saved['last_read'] != len(saved['pages']) - 1):
                return None
            if saved.get('defer_read'):
                if accepted:
                    self._save_completed_review(identity, saved)
                    saved['defer_read'] = False
                    self._documents[identity] = saved
                    self._documents.move_to_end(identity)
                    while len(self._documents) > self.capacity:
                        self._documents.popitem(last=False)
                else:
                    self._documents.pop(identity, None)
                if self._receipt_store is not None:
                    self._receipt_store.remove('pending_reads', identity)
                    self._receipt_store.remove('read_progress', identity)
            elif not accepted:
                return None
            return {'receipt': saved['receipt']}

    def referenced_citations(self, project_id, manual_scope, references):
        """Resolve only the cited facts from completed reads of this project and target."""
        if not project_id or manual_scope is None or not references:
            return {}
        scope = (project_id, manual_scope)
        with self._lock:
            remaining = set(references)
            found = {}

            def add(document):
                if (not document.get('defer_read') and document.get('fact_scope') == scope
                        and document['last_read'] == len(document['pages']) - 1):
                    matches = remaining.intersection(document['citations'])
                    found.update({key: document['citations'][key] for key in matches})
                    remaining.difference_update(matches)

            for document in reversed(self._documents.values()):
                add(document)
                if not remaining:
                    break
            if remaining and self._receipt_store is not None:
                for identity in self._receipt_store.fact_reads(scope, remaining):
                    if saved := self._load_completed_review(identity):
                        add(saved)
                    if not remaining:
                        break
            return copy.deepcopy(found)

    def resume(self, tool, arguments, binding):
        cursor = str(arguments.get('cursor') or '')
        identity, separator, page = cursor.rpartition(':')
        with self._lock:
            key = self._binding_key(binding)
            saved = self._documents.get(identity) or (self._resume_pending(identity, key)
                    if separator and page.isdigit() else None)
            request = {k: v for k, v in arguments.items() if k != 'cursor'}
            if (not separator or not page.isdigit() or saved is None or saved['tool'] != tool
                    or saved['binding'] != key
                    or not 0 <= int(page) < len(saved['pages'])):
                raise ToolPreconditionError('STALE_READ_CURSOR',
                    '读取游标已失效或 CPU、工程版本、事实库发生变化；请去掉 cursor 重新读取。',
                    next_actions=[{'tool': tool}])
            follow = saved['pages'][saved['last_read']]['data']['delivery']['next']
            if int(page) > saved['last_read'] + 1:
                raise ToolPreconditionError('READ_PAGE_SKIPPED', '按 delivery.next 顺序读取，不能跳过中间页。',
                                            next_actions=[follow])
            expected = saved['arguments']
            # A context cursor owns the original query. Optional copies of its
            # parameters may be omitted, but a supplied value must still match.
            changed = sorted(key for key, value in request.items()
                             if key not in expected or expected[key] != value)
            if request and tool not in _CONTEXT_READ_TOOLS:
                changed.extend(sorted(expected.keys() - request.keys()))
            if changed:
                retry = saved['pages'][int(page) - 1]['data']['delivery']['next'] if int(page) else follow
                raise ToolPreconditionError('STALE_READ_CURSOR',
                    '续页参数与游标绑定的原查询不一致；按返回的 delivery.next 续读，另开查询请去掉 cursor。',
                    details={'changed_arguments': changed}, next_actions=[retry] if retry else [])
            if int(page) > saved['last_read']:
                saved['last_read'] = int(page)
                if saved['last_read'] == len(saved['pages']) - 1 and not saved.get('defer_read'):
                    self._save_completed_review(identity, saved)
                    if self._receipt_store is not None:
                        self._receipt_store.remove('pending_reads', identity)
                        self._receipt_store.remove('read_progress', identity)
                elif self._receipt_store is not None:
                    self._receipt_store.write('read_progress', identity, {'last_read': saved['last_read']})
            self._documents[identity] = saved
            self._documents.move_to_end(identity)
            while len(self._documents) > self.capacity:
                self._documents.popitem(last=False)
            return PreparedReadEnvelope(copy.deepcopy(saved['pages'][int(page)]))

    def prepare(self, envelope, arguments, binding, *, review_binding=None, manual_scope=None,
                origins=None, fact_owner=None, defer_read=False):
        tool = envelope['tool']
        content = copy.deepcopy(envelope['data'])
        from plc.specification.fact_citations import cite_document, fact_version
        version = fact_version(binding[-3:])
        model = content.get('plc_model') or (manual_scope or (None,))[0]
        language = content.get('target_mode') or content.get('language') or (
            manual_scope[1] if manual_scope else None)
        resolved = arguments.get('read_format', 'resolved') == 'resolved'
        citations = cite_document(content, model=model, language=language, version=version, origins=origins) if resolved else {}
        coverage_targets = []
        for row in _coverage_rows(content):
            if row not in coverage_targets:
                coverage_targets.append(copy.deepcopy(row))
            row['delivery'] = ('not_available' if _coverage_presence(row) is False
                               else 'see_delivery_complete')
        identity = 'read-' + uuid.uuid4().hex
        request = {k: copy.deepcopy(v) for k, v in arguments.items() if k != 'cursor'}
        continuation = {} if tool in _CONTEXT_READ_TOOLS else request
        pages = []
        if resolved:
            complete = resolved_read({**envelope, 'data': content}, identity=identity, fact_version=version)
            # Custom page budgets are used by callers that need a smaller transport.
            tokens = MAX_RESOLVED_TOKENS if self.max_tokens == MAX_READ_TOKENS else self.max_tokens
            byte_limit = MAX_RESOLVED_BYTES if self.max_bytes == MAX_READ_BYTES else self.max_bytes
            if arguments.get('read_budget_tokens'):
                tokens = arguments['read_budget_tokens']
                byte_limit = max(MAX_RESOLVED_BYTES, tokens * 8)
            complete['data']['delivery']['limits'] = {'estimated_tokens': tokens, 'utf8_bytes': byte_limit}
            if _fits(complete, tokens, byte_limit):
                pages = [complete]
            else:
                try:
                    pages = self._resolved_pages(complete, continuation, tokens, byte_limit)
                except ToolPreconditionError as error:
                    if error.code != 'READ_OBJECT_TOO_LARGE':
                        raise
                    required = error.details['required_read_budget_tokens']
                    raise ToolPreconditionError('READ_BUDGET_TOO_SMALL',
                        '当前单页预算容不下完整对象；资料未截断。先提高宿主输出预算，再用原查询和所需 read_budget_tokens 重读，不缩小工艺或检索范围。',
                        details={**error.details, 'host_output_tokens': required * 2},
                        next_actions=[{'tool': tool, 'arguments': {**request, 'read_budget_tokens': required}}]
                            if required <= 128000 else []) from error
        if not pages:
            pages = self._paginate(tool, content, identity, continuation, coverage_targets)
        with self._lock:
            self._documents[identity] = {
                'tool': tool, 'arguments': request, 'binding': self._binding_key(binding),
                'review_binding': binding if review_binding is None else review_binding,
                'manual_scope': manual_scope,
                'fact_scope': (fact_owner, manual_scope) if fact_owner and manual_scope is not None else None,
                'project_id': fact_owner or '',
                'defer_read': defer_read,
                'pages': pages, 'last_read': 0,
                'research': ({key: copy.deepcopy(value) for key, value in content['instruction_research'].items()
                              if key in {'executable_fragments', 'families'}
                              or (key == 'group_operations' and content.get('workflow_mode') == 'direct_generation')}
                             if 'instruction_research' in content else None),
                'receipt': content.get('generation_context_id') or content.get('analysis_id'),
                'model_facts': _review_facts(content) if tool in {'get_generation_context', 'search_plc_manual'} else [],
                'citations': citations,

            }
            if len(pages) == 1 and not defer_read:
                self._save_completed_review(identity, self._documents[identity])
            elif self._receipt_store is not None:
                self._receipt_store.write('pending_reads', identity, self._documents[identity])
            while len(self._documents) > self.capacity:
                self._documents.popitem(last=False)
        return copy.deepcopy(pages[0])

    def design_citations(self, analysis_id=None, *, manual_scope=None, manual_documents=()):
        with self._lock:
            result = {}
            documents = dict(self._documents)
            for identity in set(manual_documents) - documents.keys():
                saved = self._load_completed_review(identity)
                if saved is not None:
                    documents[identity] = saved
            for identity, doc in documents.items():
                if doc.get('defer_read') or doc['last_read'] != len(doc['pages']) - 1:
                    continue
                if ((analysis_id and doc['tool'] == 'get_design_context' and doc['receipt'] == analysis_id)
                        or (identity in manual_documents and manual_scope and doc['tool'] == 'search_plc_manual'
                            and doc['manual_scope'] == manual_scope)):
                    result.update(doc['citations'])
            return copy.deepcopy(result)

    def _resolved_pages(self, complete, continuation, tokens, byte_limit):
        """Split containers into readable sections, never facts, schemas or source bodies."""
        tool = complete['tool']
        identity = complete['data']['delivery']['document_id']
        document = complete['data']['document']
        identity_fields = {key: document[key] for key in ('project_id', 'plc_model', 'target_mode', 'language')
                           if key in document}
        def page(part, index, final=False):
            result = copy.deepcopy(complete)
            result['data']['document'] = {**identity_fields, **part}
            delivery = result['data']['delivery']
            delivery.update(page=index, complete=final,
                next=None if final else {'tool': tool, 'arguments': {
                    **continuation, 'cursor': identity + ':' + str(index + 1)}},
                assembly='Each document is a readable section. Read all pages; no transport reference resolution is required.')
            delivery['limits'] = {'estimated_tokens': tokens, 'utf8_bytes': byte_limit}
            if 'coverage' in delivery:
                delivery['coverage']['delivery'] = 'delivered' if final else 'pending'
            return result

        def wrap(path, value):
            for key in reversed(path):
                value = {key: value}
            return value

        def sections(value, path=()):
            part = wrap(path, value)
            if _fits(page(part, 99999), tokens, byte_limit):
                yield part
                return
            key = str(path[-1]) if path else ''
            atomic = (key == 'schema' or key.endswith('_schema')
                      or len(path) >= 2 and path[-2] in {'structures', 'call_interface', 'existing_declarations'})
            if isinstance(value, Mapping) and not atomic:
                for key, child in value.items():
                    yield from sections(child, (*path, key))
                return
            if isinstance(value, list) and not atomic:
                for child in value:
                    part = wrap(path, [child])
                    if not _fits(page(part, 99999), tokens, byte_limit):
                        raise ToolPreconditionError('READ_OBJECT_TOO_LARGE',
                            '一个完整事实、接口或源码对象超过正文上限；请缩小查询范围。',
                            details=oversize(part, child, path))
                    yield part
                return
            raise ToolPreconditionError('READ_OBJECT_TOO_LARGE',
                '完整正文对象超过单次上限，未截断内容。',
                details=oversize(part, value, path))

        def oversize(part, value, path):
            required = max(tokens, 1000)
            # Measure the actual envelope including escaping; do not estimate
            # a complex interface from object count or project topic.
            while not _fits(page(part, 99999), required, max(MAX_RESOLVED_BYTES, required * 8)):
                required *= 2
            return {'object_location': list(path), 'object_size': serialized_size(value),
                    'required_read_budget_tokens': required}

        pages, current = [], {}
        for part in sections(document):
            combined = merge_read_documents(current, part)
            if current and not _fits(page(combined, len(pages)), tokens, byte_limit):
                pages.append(page(current, len(pages)))
                current = part
            else:
                current = combined
        pages.append(page(current, len(pages), True))
        return pages

    def _paginate(self, tool, content, identity, continuation, coverage_targets):
        document, objects, locations = _split_document(content)
        items, pages = list(objects.items()), []

        def make_page(values, index, complete):
            data = {'context_schema_version': 3, 'document': document, 'objects': dict(values),
                    'delivery': {'version': DELIVERY_VERSION, 'document_id': identity,
                        'page': index, 'complete': complete,
                        'scope': 'read_sequence',
                        'limits': {'estimated_tokens': self.max_tokens, 'utf8_bytes': self.max_bytes},
                        'next': None if complete else {'tool': tool,
                            'arguments': {**continuation, 'cursor': identity + ':' + str(index + 1)}},
                        'assembly': 'Collect objects from every page, then resolve $ref from document. '
                                    'References may be pending until delivery.complete=true. Resolve $concat by joining its referenced lists, '
                                    '$merge by merging its referenced maps; these divide containers, never sources/facts. '
                                    'A target delivery=see_delivery_complete is pending until this sequence is complete.'}}
            if coverage_targets:
                # Existence/completeness describe the resolved corpus. Delivery
                # is a separate property of this read sequence.
                data['delivery']['coverage'] = {
                    'targets': len(coverage_targets),
                    'present': sum(_coverage_presence(row) is True for row in coverage_targets),
                    'absent': sum(_coverage_presence(row) is False for row in coverage_targets),
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
        return pages

    def check_design_read(self, analysis_id):
        with self._lock:
            document = next((doc for doc in self._documents.values()
                             if not doc.get('defer_read') and doc['tool'] == 'get_design_context' and doc['receipt'] == analysis_id), None)
            if document is None:
                raise ToolPreconditionError('DESIGN_CONTEXT_REQUIRED', '请重新读取当前 Design 上下文。',
                                            next_actions=[{'tool': 'get_design_context'}])
            follow = document['pages'][document['last_read']]['data']['delivery']['next']
            if follow:
                raise ToolPreconditionError('DESIGN_READ_INCOMPLETE', '设计资料尚有未读页。',
                                            details={'expected_analysis_id': analysis_id},
                                            next_actions=[follow])

    def completed_design(self, document_id):
        with self._lock:
            saved = self._documents.get(document_id)
            if (saved and not saved.get('defer_read') and saved['tool'] == 'get_design_context'
                    and saved['last_read'] == len(saved['pages']) - 1):
                return saved['receipt']
        return None

    def check_review(self, binding, review, receipt=None, *, manual_scope=None, fact_owner=None, references=()):
        from application.instruction_research import normalize_review_assumptions
        with self._lock:
            documents = {key: doc for key, doc in self._documents.items() if not doc.get('defer_read')}
            document_id = review.get('context_document') if review else None
            manuals = set(review.get('manual_documents', ())) if review else set()
            receipt_document = None
            if self._receipt_store is not None and receipt:
                alias = self._receipt_store.read('read_ids', receipt)
                if alias is not None:
                    receipt_document = alias['document_id']
                else:
                    receipt_document = next((key for key, value in
                        self._receipt_store.generation_reads(binding).items() if value == receipt), None)
            for identity in ({document_id, receipt_document, *manuals} - {None}) - documents.keys():
                saved = self._load_completed_review(identity)
                if saved is not None:
                    documents[identity] = saved
            matching = {key: doc for key, doc in documents.items() if doc['review_binding'] == binding
                        and (manual_scope is None or doc['tool'] != 'search_plc_manual')}
            if manual_scope is not None:
                matching.update({key: doc for key, doc in documents.items()
                                 if doc['tool'] == 'search_plc_manual' and doc['manual_scope'] == manual_scope})
            generations = {key: doc for key, doc in matching.items()
                           if doc['tool'] == 'get_generation_context'
                           and (doc['research'] is not None or doc['receipt'])}
            if receipt and document_id not in documents:
                matching_receipt = next((key for key, doc in generations.items()
                                         if doc['receipt'] == receipt), None)
                if matching_receipt is not None:
                    document_id = matching_receipt
                elif document_id is None:
                    document_id = receipt_document
            if document_id is None and not receipt:
                candidates = (self._receipt_store.generation_reads(binding)
                              if self._receipt_store is not None else {})
                candidates.update({key: doc['receipt'] for key, doc in generations.items()})
                if not candidates:
                    return  # Programmatic candidates do not require a model conversation.
                if len(candidates) != 1:
                    raise ToolPreconditionError('INSTRUCTION_REVIEW_REQUIRED',
                        '当前工程有多个生成上下文，请引用本次任务的上下文。',
                        details={'context_candidates': [
                            {'context_document': key, 'generation_context_id': value}
                            for key, value in candidates.items()]})
                document_id = next(iter(candidates))
                if document_id not in generations:
                    saved = self._load_completed_review(document_id)
                    if saved is not None and saved['review_binding'] == binding and saved['receipt'] == candidates[document_id]:
                        generations[document_id] = matching[document_id] = saved
            generation = generations.get(document_id)
            if generation is None:
                if document_id in documents:
                    raise ToolPreconditionError('INSTRUCTION_REVIEW_REQUIRED',
                        '提交的任务与当前工程、规格、型号或资料版本不一致。请由用户确认要实施的工程任务。')
                raise ToolPreconditionError('INSTRUCTION_REVIEW_REQUIRED',
                    '本地无法确认提交对应的工程任务。请由用户确认要继续的任务，保留已提交的程序。')
            if receipt and generation['receipt'] != receipt:
                known_receipt = (receipt_document is not None
                    or any(doc['tool'] == 'get_generation_context' and doc['receipt'] == receipt
                           for doc in documents.values())
                    or (self._receipt_store is not None and self._receipt_store.read('generation', receipt) is not None))
                if known_receipt or not generation['receipt']:
                    raise ToolPreconditionError('INSTRUCTION_REVIEW_REQUIRED',
                        '两个任务引用不一致。请选定要继续的任务，保留已提交的程序。',
                        details={'expected_context_document': document_id,
                                 'expected_generation_context_id': generation['receipt']})
            if review is not None:
                review['assumptions'] = normalize_review_assumptions(review)
                if 'context_document' in review:
                    review['context_document'] = document_id
            manuals = {key for key in manuals if key in matching
                       and matching[key]['tool'] == 'search_plc_manual'
                       and matching[key]['last_read'] == len(matching[key]['pages']) - 1}
            if review is not None and 'manual_documents' in review:
                review['manual_documents'] = sorted(manuals)
            if generation['last_read'] < len(generation['pages']) - 1:
                follow = generation['pages'][generation['last_read']]['data']['delivery']['next']
                raise ToolPreconditionError('INSTRUCTION_READ_INCOMPLETE', '生成资料尚有未读页，按 next_actions 续读。',
                                            next_actions=(follow,))
            research = copy.deepcopy(generation['research'] or {})
            research['model_facts'] = copy.deepcopy([
                *generation['model_facts'], *(fact for key in sorted(manuals) for fact in matching[key]['model_facts'])])
            research['citations'] = copy.deepcopy({
                **generation['citations'],
                **{identity: row for key in sorted(manuals) for identity, row in matching[key]['citations'].items()},
            })
            research['citations'].update(self.referenced_citations(
                fact_owner, manual_scope, set(references) - research['citations'].keys()))
            if generation['receipt']:
                research['generation_context_id'] = generation['receipt']
            return research



def assemble_read_pages(pages):
    """Reconstruct a complete read document for deterministic application clients."""
    pages = list(pages)
    if not pages or not pages[-1]['data']['delivery']['complete']:
        raise ValueError('read delivery incomplete')
    identity = pages[0]['data']['delivery']['document_id']
    if pages[0]['data'].get('context_schema_version') == 4:
        document = {}
        for index, page in enumerate(pages):
            delivery = page['data']['delivery']
            if (page['data'].get('context_schema_version') != 4
                    or delivery['document_id'] != identity or delivery['page'] != index):
                raise ValueError('read pages belong to different documents or are out of order')
            document = merge_read_documents(document, page['data']['document'])
        return document
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


def assemble_read_envelope(pages):
    """Present one complete document after the host has fetched every page."""
    document = assemble_read_pages(pages)
    delivery = dict(pages[-1]['data']['delivery'])
    delivery.pop('assembly', None)
    delivery.update(page=0, complete=True, next=None)
    return PreparedReadEnvelope(ok=True, tool=pages[0]['tool'], data={
        'context_schema_version': 4, 'document': document, 'delivery': delivery,
    })


def collect_host_read(tool, arguments, call_id, read_page, *, before_restart=None):
    """Collect a fresh document after a lost cursor without mixing reads."""
    for read_id in (call_id, f'{call_id}:restart'):
        first = read_page(read_id, arguments)
        if not first.get('ok'):
            return first
        pages = [first]
        while follow := pages[-1]['data']['delivery']['next']:
            page = read_page(f'{read_id}:page:{len(pages)}', follow['arguments'])
            if not page.get('ok'):
                if (page.get('error') or {}).get('code') not in {'STALE_READ_CURSOR', 'READ_PAGE_SKIPPED'}:
                    return page
                if before_restart is not None:
                    before_restart()
                break
            pages.append(page)
        else:
            return assemble_read_envelope(pages) if len(pages) > 1 else first
    return {'ok': False, 'tool': tool, 'error': {'code': 'ENGINEERING_CONTEXT_CHANGED',
            'message': '交付资料时无法确认当前工程状态，请由用户核对工程。'}}


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


def _review_facts(document):
    """Keep the public knowledge delivered with an explicitly referenced read."""
    inputs = document.get('generation_input') or {}
    manual = (document.get('instruction_research') or {}).get('manual') or {}
    return [*(document.get('knowledge') or []), *(inputs.get('knowledge') or []),
            *(manual.get('knowledge') or []),
            *(unit for row in document.get('results') or [] if isinstance(row, dict)
              for unit in row.get('knowledge', []))]
