"""Version-bound random owner access without reading the full definition JSON.

A tiny manifest authenticates one sibling SQLite file. Opening scans its bytes
for integrity, not its fact rows. Only explicitly requested owner graphs are
hydrated. This is storage evidence, never a CPU/native compilation admission.
"""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
from collections.abc import Mapping

SERIALIZATION = 'instruction-owner-store-v1'
MANIFEST_NAME = 'instruction_definitions.manifest.json'


def _identity(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


class OwnerDefinitionStore:
    def __init__(self, manifest_path):
        original = Path(manifest_path)
        if original.is_symlink() or original.stat().st_size > 65536:
            raise ValueError('Invalid owner manifest path or size')
        self.manifest_path = original.resolve()
        before = _identity(self.manifest_path)
        manifest = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        if (not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int or manifest.get('schema_version') != 1
                or manifest.get('serialization') != SERIALIZATION
                or manifest.get('method') != 'instruction-material-compiler-v1'
                or manifest.get('corpus_kind') != 'authored_plc_facts_v1'
                or not isinstance(manifest.get('fact_bundle_version'), str)
                or not manifest['fact_bundle_version']):
            raise ValueError('Unsupported instruction owner manifest')
        database = manifest.get('database', {})
        name = database.get('filename') if isinstance(database, dict) else None
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*\.sqlite', name)
                or Path(name).name != name or '\\' in name):
            raise ValueError('Owner database must be a sibling filename')
        path = self.manifest_path.parent / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != self.manifest_path.parent:
            raise ValueError('Invalid owner database path')
        self.database_path = path.resolve()
        db_before = _identity(self.database_path)
        if (type(database.get('bytes')) is not int or database['bytes'] != db_before[2]
                or not isinstance(database.get('sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', database['sha256'])
                or _digest(self.database_path) != database['sha256']):
            raise ValueError('Owner database integrity mismatch')
        self._manifest_identity, self._database_identity = before, db_before
        self._lock = threading.RLock()
        if any(Path(str(self.database_path)+suffix).exists() for suffix in ('-wal','-journal')):
            raise ValueError('Owner database must be a sealed snapshot without write journals')
        self._closed = False
        self._db = sqlite3.connect(self.database_path.as_uri()+'?mode=ro&immutable=1', uri=True, check_same_thread=False)
        self._db.execute('PRAGMA query_only=ON')
        self._db.execute('PRAGMA trusted_schema=OFF')
        self._stats = {'hydrated_owner_count': 0, 'owner_lookup_count': 0,
                       'source_load_count': 0, 'value_load_count': 0, 'field_load_count':0, 'owner_cache_hits': 0}
        try:
            meta = dict(self._db.execute('SELECT key,value FROM meta'))
            if (meta.get('owner_store_serialization') != SERIALIZATION
                    or meta.get('runtime_serialization') not in {'owner-source-pool-v1','owner-field-pool-v2'}
                    or meta.get('corpus_kind') != manifest['corpus_kind']
                    or meta.get('fact_bundle_version') != manifest['fact_bundle_version']):
                raise ValueError('Owner database version mismatch')
            required = {'runtime_owner_index', 'runtime_definitions', 'runtime_sources',
                        'runtime_values', 'runtime_language_features'}
            if meta.get('runtime_serialization') == 'owner-field-pool-v2':
                from knowledge.fact_field_codec import FIELD_SERIALIZATION
                if meta.get('field_serialization') != FIELD_SERIALIZATION:
                    raise ValueError('Unsupported exact fact field transport')
                required.add('runtime_fact_fields')
            tables = {row[0] for row in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not required <= tables:
                raise ValueError('Missing owner store tables')
            count = self._db.execute('SELECT count(*) FROM runtime_owner_index').fetchone()[0]
            if type(manifest.get('owners')) is not int or manifest['owners'] != count:
                raise ValueError('Owner count mismatch')
            self.owner_count = count
            self.fact_bundle_version = manifest['fact_bundle_version']
            self._metadata = json.loads(meta.get('definition_metadata', '{}'))
            if (not isinstance(self._metadata, dict)
                    or self._metadata.get('fact_bundle_version') != self.fact_bundle_version
                    or self._metadata.get('method') != manifest['method']):
                raise ValueError('Owner definition metadata mismatch')
            if not self.current():
                raise ValueError('Owner files changed while opening')
        except Exception as error:
            self._db.close()
            self._closed = True
            if isinstance(error, sqlite3.Error):
                raise ValueError("Invalid owner database schema") from error
            raise

    def current(self):
        try:
            return (not self._closed
                    and not any(Path(str(self.database_path)+suffix).exists() for suffix in ('-wal','-journal'))
                    and _identity(self.manifest_path) == self._manifest_identity
                    and _identity(self.database_path) == self._database_identity)
        except OSError:
            return False

    def _check(self):
        if not self.current():
            raise ValueError('Owner store changed; reload checking context')

    @property
    def metadata(self):
        result = copy.deepcopy(self._metadata)
        result['language_facts'] = self.language_features()
        return result

    def documents(self):
        self._check()
        return copy.deepcopy(self._metadata.get('documents', []))

    def iter_owner_headers(self):
        with self._lock:
            self._check()
            # No owner facts/materials are selected by this query.
            rows = self._db.execute('SELECT vendor,opcode,target_model FROM runtime_owner_index ORDER BY vendor,opcode,target_model').fetchall()
        for vendor, opcode, model in rows:
            yield {'vendor': vendor, 'opcode': opcode, 'target_model': model}

    def get_owner_header(self, key):
        """Read only identity/admission metadata, without decoding a fact graph."""
        if not isinstance(key, (tuple, list)) or len(key) != 3:
            raise ValueError('Expected vendor/opcode/model owner key')
        key = (str(key[0]).lower(), str(key[1]).upper(), str(key[2]).upper())
        fields = ('call_available', 'call_admission_reason', 'call_admission_details')
        selections = ','.join(
            "json_type(d.payload_json,'$.value.%s'),json_extract(d.payload_json,'$.value.%s')" % (field, field)
            for field in fields)
        with self._lock:
            self._check()
            row = self._db.execute(
                'SELECT d.owner,' + selections + ' FROM runtime_owner_index i '
                'LEFT JOIN runtime_definitions d ON d.owner=i.owner '
                'WHERE i.vendor=? AND i.opcode=? AND i.target_model=?', key).fetchone()
            if row is None:
                return None
            if row[0] is None:
                raise ValueError('Missing owner packet')
            result = dict(zip(('vendor', 'opcode', 'target_model'), key))
            for index, field in enumerate(fields):
                kind, value = row[1 + 2 * index:3 + 2 * index]
                if kind is None:
                    continue
                if kind in ('object', 'array'):
                    value = json.loads(value)
                elif kind in ('true', 'false'):
                    value = kind == 'true'
                result[field] = value
            return result

    def language_features(self):
        with self._lock:
            self._check()
            features = []
            for text, digest in self._db.execute('SELECT payload_json,sha256 FROM runtime_language_features ORDER BY target'):
                if hashlib.sha256(text.encode()).hexdigest() != digest:
                    raise ValueError('Altered language feature')
                feature = json.loads(text)
                from knowledge.runtime_fact_publication import validate_language_feature
                validate_language_feature(feature, published=True)
                features.append(feature)
            return features

    def load_owner(self, key):
        if not isinstance(key, (tuple, list)) or len(key) != 3:
            raise ValueError('Expected vendor/opcode/model owner key')
        return self.get_entry(key[1], cpu=key[2], vendor=key[0])

    def get_entry(self, opcode, *, cpu, vendor='mitsubishi'):
        from knowledge.runtime_fact_publication import hydrate_owner_packet
        from plc.instruction_definition import InstructionFactGroup
        key = (str(vendor).lower(), str(opcode).upper(), str(cpu).upper())
        with self._lock:
            self._check()
            self._stats['owner_lookup_count'] += 1
            row = self._db.execute('SELECT owner,packet_sha256 FROM runtime_owner_index WHERE vendor=? AND opcode=? AND target_model=?', key).fetchone()
            if row is None:
                return None
            stored = self._db.execute('SELECT payload_json FROM runtime_definitions WHERE owner=?', (row[0],)).fetchone()
            if stored is None or hashlib.sha256(stored[0].encode()).hexdigest() != row[1]:
                raise ValueError('Missing or altered owner packet')
            load_counts = {}
            packet = hydrate_owner_packet(self._db, row[0],load_counts=load_counts)
            entry = packet['value']
            actual = (str(entry.get('vendor', 'mitsubishi')).lower(), str(entry.get('opcode')).upper(), str(entry.get('target_model')).upper())
            if actual != key:
                raise ValueError('Owner identity mismatch')
            materials, uninterpreted = entry.get('source_materials', []), entry.get('uninterpreted_content', [])
            if any(not isinstance(items, list) or any(not isinstance(item, Mapping) for item in items)
                   for items in (materials, uninterpreted)):
                raise ValueError('Invalid owner source processing receipt')
            facts = entry.get('facts', [])
            if not isinstance(facts, list):
                raise ValueError('Owner facts must be a list')
            ids = set()
            for fact in facts:
                group = InstructionFactGroup.from_mapping(fact)
                if group.id in ids or group.scope.get('forms') != [entry['opcode']] or group.scope.get('models') != [entry['target_model']]:
                    raise ValueError('Invalid exact owner fact scope or duplicate identity')
                ids.add(group.id)
            graph = {f['id']: set(f.get('depends_on', [])) for f in facts}
            if any(deps - ids for deps in graph.values()):
                raise ValueError('Missing owner dependency')
            while graph:
                ready = {identity for identity, deps in graph.items() if not deps}
                if not ready:
                    raise ValueError('Cyclic owner dependencies')
                graph = {identity: deps-ready for identity, deps in graph.items() if identity not in ready}
            for name,count in load_counts.items():
                self._stats[name] += count
            self._stats['hydrated_owner_count'] += 1
            self._check()
            return entry

    def stats(self):
        with self._lock:
            return {'owner_count': self.owner_count, **self._stats}

    def close(self):
        with self._lock:
            self._db.close()
            self._closed = True
