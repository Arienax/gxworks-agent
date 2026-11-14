# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Stable references to the technical records delivered for one PLC target."""
import copy
import hashlib
import json
import re
from collections.abc import Mapping


def referenced_fact_ids(value):
    """Read explicit technical references in a submitted decision or repair."""
    identities, pending = set(), [value]
    while pending:
        item = pending.pop()
        if isinstance(item, Mapping):
            identity = item.get('ref') if item.get('source') == 'model_fact' else None
            if isinstance(identity, str) and re.fullmatch(r'fact-[0-9a-f]{24}', identity):
                identities.add(identity)
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return identities


def fact_binding():
    from knowledge.index_access import index_identity, index_path
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    registry = DEFAULT_INSTRUCTION_REGISTRY
    return (index_identity(index_path()), index_identity(registry.selected_owner_database_path()),
            getattr(registry, 'fact_bundle_version', None))


def fact_version(binding=None):
    return hashlib.sha256(repr(fact_binding() if binding is None else binding).encode()).hexdigest()[:24]


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def cite_units(units, *, model, language, version, path='/knowledge', origins=None):
    """Add IDs to public units and retain their complete text for local receipts."""
    from plc.target_identity import target_series
    menu = target_series(model)
    identity_model = menu[1] if menu else model
    result, citations = [], {}
    for index, unit in enumerate(units):
        if not isinstance(unit, Mapping):
            result.append(unit)
            continue
        record = {key: value for key, value in unit.items() if key != 'citation_id'}
        text = record.get('text')
        if not isinstance(text, str) or set(record) != {'text'}:
            text = _encode(record)
        identity = 'fact-' + hashlib.sha256(_encode([identity_model, language, version, record]).encode()).hexdigest()[:24]
        result.append({**record, 'citation_id': identity})
        citations[identity] = {'path': f'{path}/{index}', 'plc_model': model,
                               'language': language, 'fact_version': version, 'text': text}
        if origins:
            from shared.model_context import knowledge_unit_key, technical_value
            if sources := origins.get(knowledge_unit_key(technical_value(record))):
                citations[identity]['sources'] = copy.deepcopy(sources)
    return result, citations


def cite_document(document, *, model, language, version, origins=None):
    """Annotate only engineering knowledge arrays, never source code or schemas."""
    citations = {}

    def add(owner, path):
        if isinstance(owner, dict) and isinstance(owner.get('knowledge'), list):
            owner['knowledge'], rows = cite_units(owner['knowledge'], model=model, language=language,
                                                  version=version, path=path, origins=origins)
            for identity, row in rows.items():
                citations.setdefault(identity, row)

    add(document, '/knowledge')
    add(document.get('generation_input'), '/generation_input/knowledge')
    add((document.get('instruction_research') or {}).get('manual'),
        '/instruction_research/manual/knowledge')
    for index, row in enumerate(document.get('results') or []):
        add(row, f'/results/{index}/knowledge')
    return citations
