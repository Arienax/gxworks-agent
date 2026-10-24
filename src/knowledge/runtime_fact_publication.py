"""Explicit, standalone owner-normalized runtime index (not the private builder).

Each complete owner is stored once. Search rows contain tokens and references,
not another copy of the fact graph. Sources are content-addressed and hydrated
only for a selected owner. Existing unnormalized indexes remain readable.
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
from pathlib import Path
from knowledge.runtime_fact_projection import SOURCE_SERIALIZATION, VALUE_SERIALIZATION, resolve_fact_sources


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def project_language_features(features):
    """Strictly project exact language features, without instruction aggregation.

    Features remain inline. No synthetic instruction/model owner is invented.
    Unknown metadata is rejected instead of being silently copied to publication.
    """
    from knowledge.fact_corpus import project_definition
    from plc.instruction_definition import InstructionFactGroup
    if not isinstance(features, list):
        raise ValueError('Language features must be a list')
    output, withheld, seen = [], [], set()
    for feature in features:
        if not isinstance(feature, dict) or set(feature) - {'target', 'language', 'facts'}:
            raise ValueError('Unreviewed language feature metadata')
        target = feature.get('target')
        language = feature.get('language', str(target).partition('.')[0].lower())
        if (not isinstance(target, str) or not target or target in seen
                or language not in {'st', 'fbd'} or not target.startswith(language.upper()+'.')):
            raise ValueError('Invalid or duplicate exact language feature')
        seen.add(target)
        facts = feature.get('facts', [])
        if not isinstance(facts, list):
            raise ValueError('Language facts must be a list')
        ids = set()
        for fact in facts:
            if not isinstance(fact, dict) or not isinstance(fact.get('id'), str) or fact['id'] in ids:
                raise ValueError('Invalid or duplicate language fact')
            allowed_fact = {'id', 'dimension', 'value', 'status', 'sources', 'depends_on', 'members',
                            'scope', 'verification', 'conditions', 'authority_decision', 'note', 'note_kind'}
            if set(fact) - allowed_fact or ('note_kind' in fact and fact['note_kind'] != 'explanation'):
                raise ValueError('Unreviewed language fact metadata')
            ids.add(fact['id'])
            scope = fact.get('scope', {})
            if not isinstance(scope, dict) or scope.get('forms') != [target] or scope.get('languages') != [language]:
                raise ValueError('Language fact must retain exact feature/language scope')
        projected, rejected = project_definition({'facts': facts})
        accepted = projected['facts']
        # The generic representation projector does not itself certify sources.
        # Withhold source claims with no surviving public reference, not promote
        # them or leave dependants looking unconditional.
        while True:
            ids = {fact['id'] for fact in accepted}
            removed = []
            for fact in accepted:
                reason = None
                if (fact.get('status') in {'source_verified', 'candidate_evidence'}
                        and (not fact.get('sources') or any(not source for source in fact['sources']))):
                    reason = 'missing_public_source'
                elif set(fact.get('depends_on', [])) - ids:
                    reason = 'withheld_dependency'
                if reason:
                    rejected.append({'id': fact['id'], 'reason': reason, 'status': fact.get('status', 'unknown')})
                    removed.append(fact)
            if not removed:
                break
            accepted = [fact for fact in accepted if fact not in removed]
        # Validate the same typed schema as the Registry. Reject cycles rather
        # than inferring or breaking an unresolved language dependency.
        pending = {fact['id']: set(fact.get('depends_on', [])) for fact in accepted}
        while pending:
            ready = {identity for identity, deps in pending.items() if not deps}
            if not ready:
                raise ValueError('Cyclic language fact dependencies')
            pending = {identity: deps-ready for identity, deps in pending.items() if identity not in ready}
        for fact in accepted:
            InstructionFactGroup.from_mapping(fact)
        output.append({'target': target, 'language': language, 'facts': accepted})
        if rejected:
            withheld.append({'target': target, 'language': language, 'withheld': rejected})
    return output, withheld


def owner_key(entry):
    return _json([entry.get('vendor', 'mitsubishi'), entry['target_model'], entry['opcode']])


def hydrate_owner_packet(connection, owner):
    version = connection.execute("SELECT value FROM meta WHERE key='runtime_serialization'").fetchone()
    if version is None or version[0] != 'owner-source-pool-v1':
        raise ValueError('Unsupported runtime owner transport')
    row = connection.execute('SELECT payload_json FROM runtime_definitions WHERE owner=?', (owner,)).fetchone()
    if row is None:
        raise ValueError('Missing runtime owner')
    packet = json.loads(row[0])
    facts = packet['value']['facts']
    catalog, values = {}, {}
    for ref in {ref for fact in facts for ref in fact.get('source_refs', [])}:
        source = connection.execute('SELECT payload_json FROM runtime_sources WHERE id=?', (ref,)).fetchone()
        if source is None:
            raise ValueError('Missing runtime source')
        catalog[ref] = json.loads(source[0])
    for ref in {fact['value_ref'] for fact in facts if 'value_ref' in fact}:
        value = connection.execute('SELECT payload_json FROM runtime_values WHERE id=?', (ref,)).fetchone()
        if value is None:
            raise ValueError('Missing runtime value')
        values[ref] = json.loads(value[0])
    packet['value']['facts'] = [resolve_fact_sources(fact, catalog, values) for fact in facts]
    return packet


def write_runtime_sqlite(payload, path):
    """Create a NEW instruction-only index; never overwrite an existing index."""
    from knowledge.fact_corpus import CORPUS_KIND, PUBLICATION_STATUS
    if (payload.get('source_serialization') != SOURCE_SERIALIZATION
            or not isinstance(payload.get('source_catalog'), dict)):
        raise ValueError('Expected pooled runtime definitions')
    if ('value_catalog' in payload or 'value_serialization' in payload) and (
            payload.get('value_serialization') != VALUE_SERIALIZATION
            or not isinstance(payload.get('value_catalog'), dict)):
        raise ValueError('Unsupported fact value serialization')
    path = Path(path)
    # Exclusive creation prevents accidentally replacing the full mixed corpus.
    with path.open('xb'):
        pass
    connection = sqlite3.connect(path)
    try:
        connection.executescript('''
          CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE runtime_sources(id TEXT PRIMARY KEY,payload_json TEXT NOT NULL);
          CREATE TABLE runtime_values(id TEXT PRIMARY KEY,payload_json TEXT NOT NULL);
          CREATE TABLE runtime_definitions(owner TEXT PRIMARY KEY,payload_json TEXT NOT NULL);
          CREATE TABLE chunks(id TEXT PRIMARY KEY,text TEXT NOT NULL,metadata_json TEXT NOT NULL,
            plc_models TEXT NOT NULL,instruction_opcode TEXT NOT NULL,chunk_type TEXT NOT NULL,
            content_origin TEXT NOT NULL,distribution_status TEXT NOT NULL);
          CREATE TABLE facts(id TEXT PRIMARY KEY,kind TEXT NOT NULL,target TEXT NOT NULL,
            plc_model TEXT NOT NULL,chunk_id TEXT NOT NULL,payload_json TEXT NOT NULL);
          CREATE INDEX facts_target ON facts(kind,target,plc_model);
          CREATE TABLE entity_index(entity TEXT NOT NULL,chunk_id TEXT NOT NULL,PRIMARY KEY(entity,chunk_id));
          CREATE VIRTUAL TABLE chunks_fts USING fts5(text,content='chunks',content_rowid='rowid');
        ''')
        meta = {'corpus_kind': CORPUS_KIND, 'runtime_serialization': 'owner-source-pool-v1',
                'corpus_scope': 'provided_instruction_definitions_only',
                'fact_bundle_version': str(payload.get('fact_bundle_version', ''))}
        connection.executemany('INSERT INTO meta VALUES (?,?)', meta.items())
        connection.executemany('INSERT INTO runtime_sources VALUES (?,?)',
            ((key, _json(value)) for key, value in payload['source_catalog'].items()))
        connection.executemany('INSERT INTO runtime_values VALUES (?,?)',
            ((key, _json(value)) for key, value in payload.get('value_catalog', {}).items()))
        count = 0
        for entry in payload['entries']:
            owner = owner_key(entry)
            identity = 'definition:' + hashlib.sha256(owner.encode()).hexdigest()
            model, opcode = entry['target_model'], entry['opcode']
            packet = {'kind': 'instruction', 'target': opcode, 'scope': {'models': [model]},
                      'content_origin': 'authored_fact', 'value': entry}
            metadata = {'runtime_owner': owner, 'content_origin': 'authored_fact',
                        'distribution_status': PUBLICATION_STATUS, 'fact_kind': 'instruction',
                        'fact_target': opcode, 'source_lane': 'fact', 'plc_models': [model]}
            connection.execute('INSERT INTO runtime_definitions VALUES (?,?)', (owner, _json(packet)))
            # Deterministic token set preserves searchable technical values but
            # does not replicate full graphs, provenance or JSON wrappers.
            import re
            tokens = set(re.findall(r'[\w.$:+/-]+', _json(entry)))
            for fact in entry.get('facts', []):
                if 'value_ref' in fact:
                    tokens.update(re.findall(r'[\w.$:+/-]+', _json(payload['value_catalog'][fact['value_ref']])))
            search = ' '.join(sorted(tokens))
            connection.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)',
                (identity, search, _json(metadata), _json([model]), opcode, 'instruction',
                 'authored_fact', PUBLICATION_STATUS))
            connection.execute('INSERT INTO entity_index VALUES (?,?)', (opcode, identity))
            connection.execute('INSERT INTO facts VALUES (?,?,?,?,?,?)',
                (identity, 'instruction', opcode, model, identity, '{}'))
            count += len(entry.get('facts', []))
        connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
        connection.commit()
        return {'owners': len(payload['entries']), 'stored_facts': count,
                'chunk_fact_copies': 0, 'sources': len(payload['source_catalog']),
                'values': len(payload.get('value_catalog', {})),
                'sqlite_bytes': path.stat().st_size, 'scope': meta['corpus_scope']}
    finally:
        connection.close()
