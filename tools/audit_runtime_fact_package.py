"""Read-only, bounded-unit audit of a local runtime package; emits no fact prose.

The complete gzip document is never decompressed to disk or materialized in RAM.
JSON parsing retains at most one entry/family, with an explicit size guard.
Distinct identities live in a temporary SQLite file, not a growing Python set.
Owner manifests use the verified read-only store API, one hydrated owner at a
time; their size guard runs after hydration, not as a hard peak-RAM bound.
"""
from __future__ import annotations
import argparse
from collections import Counter
import gzip
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))


class JSONUnits:
    def __init__(self, stream, max_unit_bytes=64 * 1024 * 1024):
        self.stream, self.buffer, self.pos, self.eof = stream, '', 0, False
        self.limit, self.maximum = max_unit_bytes, 0
        self.decoder = json.JSONDecoder()

    def _more(self):
        self.buffer = self.buffer[self.pos:]
        self.pos = 0
        chunk = self.stream.read(65536)
        if not chunk:
            self.eof = True
        self.buffer += chunk

    def _space(self):
        while True:
            while self.pos < len(self.buffer) and self.buffer[self.pos].isspace():
                self.pos += 1
            if self.pos < len(self.buffer) or self.eof:
                return
            self._more()

    def peek(self):
        self._space()
        return self.buffer[self.pos:self.pos + 1]

    def take(self, expected):
        if self.peek() != expected:
            raise ValueError('Malformed JSON container')
        self.pos += 1

    def value(self):
        self._space()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.pos)
                size = len(self.buffer[self.pos:end].encode('utf-8'))
                if size > self.limit:
                    raise ValueError('JSON entry/family exceeds --max-unit-mib; audit stopped without loading the full document')
                self.maximum = max(self.maximum, size)
                self.pos = end
                return value
            except json.JSONDecodeError:
                if self.eof:
                    raise ValueError('Malformed or truncated JSON') from None
                # UTF-8 byte guard is applied before another read as well.
                if len(self.buffer[self.pos:].encode('utf-8')) > self.limit:
                    raise ValueError('JSON entry/family exceeds --max-unit-mib; audit stopped without loading the full document')
                self._more()

    def fields(self):
        self.take('{')
        seen = set()
        if self.peek() == '}':
            self.take('}')
        else:
            while True:
                key = self.value()
                if not isinstance(key, str) or key in seen:
                    raise ValueError('Duplicate or invalid root key')
                seen.add(key)
                self.take(':')
                if self.peek() == '[':
                    self.take('[')
                    if self.peek() != ']':
                        while True:
                            yield 'item', key, self.value()
                            if self.peek() != ',':
                                break
                            self.take(',')
                    self.take(']')
                    yield 'array_end', key, None
                else:
                    yield 'field', key, self.value()
                if self.peek() != ',':
                    break
                self.take(',')
            self.take('}')
        if self.peek():
            raise ValueError('Trailing JSON content')


def open_text(path):
    with Path(path).open('rb') as stream:
        gz = stream.read(2) == b'\x1f\x8b'
    return gzip.open(path, 'rt', encoding='utf-8') if gz else Path(path).open('rt', encoding='utf-8')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def family_entries(family, profiles):
    """Resolve one owner at a time using the production literal diff semantics."""
    from plc.instruction_definition_storage import _apply, _owner_tokens
    selections = []
    for selection in family['owners']:
        models, forms = profiles.get(selection['model_profile']), selection['forms']
        if not isinstance(models, list) or not models or len(set(models)) != len(models):
            raise ValueError('Invalid model profile')
        if not isinstance(forms, list) or not forms or len(set(forms)) != len(forms):
            raise ValueError('Invalid form selection')
        if any(not isinstance(value, str) for value in forms + models):
            raise ValueError('Nonliteral owner')
        form_set, model_set = set(forms), set(models)
        if any(form_set & prior_forms and model_set & prior_models
               for prior_forms, prior_models in selections):
            raise ValueError('Duplicate owner selection')
        selections.append((form_set, model_set))
    overridden = set()
    for override in family.get('model_diffs', []):
        for form in override['forms']:
            marker = (form, override['model'])
            if (not any(form in forms and override['model'] in models for forms, models in selections)
                    or marker in overridden):
                raise ValueError('Unowned or overlapping model difference')
            overridden.add(marker)
    all_forms = set().union(*(forms for forms, _ in selections))
    if set(family.get('form_diffs', {})) - all_forms:
        raise ValueError('Unowned form difference')
    # Never materialize the forms x models Cartesian product just to iterate it.
    for form in sorted(all_forms):
        models = set().union(*(models for forms, models in selections if form in forms))
        for model in sorted(models):
            value = _apply(family['common'], family.get('form_diffs', {}).get(form, {}))
            for override in family.get('model_diffs', []):
                if override['model'] == model and form in override['forms']:
                    value = _apply(value, override['changes'])
            value = _owner_tokens(value, model, form, restore=True)
            raw_facts = value.get('facts', {})
            if not isinstance(raw_facts, dict):
                raise ValueError('Invalid compact fact map')
            if any(k != f.get('id') for k, f in raw_facts.items()):
                raise ValueError('Fact map key does not match its ID')
            value['facts'] = list(raw_facts.values())
            yield {'vendor': family['vendor'], 'opcode': form, 'target_model': model, **value}



class OwnerBreakdown:
    """Bounded descriptive bins; never infer support or provenance from names."""
    ADMISSION_REASONS = frozenset({
        'form_not_registered', 'cpu_replacement_required',
        'target_not_in_declared_scope', 'admitted',
        'admitted_with_empty_declared_scope',
    })
    DECLARED_SCOPES = frozenset({'unavailable', 'empty', 'explicit'})

    def __init__(self):
        self.models = {}

    @staticmethod
    def label(value):
        if value is None:
            return '<missing>'
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.+/-]{1,64}', value):
            return '<other-or-non-token>'
        return value

    @staticmethod
    def add(counter, key, limit=24):
        if key not in counter and len(counter) >= limit:
            key = '<overflow>'
        counter[key] += 1

    @staticmethod
    def enum_field(mapping, name, allowed):
        if name not in mapping:
            return '<missing>'
        value = mapping[name]
        return value if isinstance(value, str) and value in allowed else '<other-or-invalid>'

    def admission(self, row, entry):
        # These are literal compiler annotations, not a second admission engine.
        # Unknown/free-text values and replacement identities never leave this audit.
        self.add(row['call_admission_reason'], self.enum_field(
            entry, 'call_admission_reason', self.ADMISSION_REASONS))
        details = entry.get('call_admission_details')
        state = '<missing>' if 'call_admission_details' not in entry else 'object' if isinstance(details, dict) else '<non-object>'
        self.add(row['call_admission_details_state'], state)
        if not isinstance(details, dict):
            self.add(row['call_admission_declared_scope'], '<missing>' if state == '<missing>' else '<non-object-details>')
            self.add(row['call_admission_replacement_opcode_state'], '<missing>' if state == '<missing>' else '<non-object-details>')
            return
        self.add(row['call_admission_declared_scope'], self.enum_field(
            details, 'declared_scope', self.DECLARED_SCOPES))
        replacement = details.get('replacement_opcode')
        replacement_state = '<missing>' if 'replacement_opcode' not in details else 'technical-token-present' if isinstance(replacement, str) and re.fullmatch(r'[A-Za-z0-9_.+/-]{1,64}', replacement) else '<other-or-invalid>'
        self.add(row['call_admission_replacement_opcode_state'], replacement_state)
        row['counts']['owners_with_unreported_admission_detail_fields'] += bool(
            set(details) - {'declared_scope', 'replacement_opcode'})

    def owner(self, entry):
        model = self.label(entry.get('target_model'))
        if model not in self.models and len(self.models) >= 96:
            model = '<overflow>'
        row = self.models.setdefault(model, {
            'counts': Counter(), 'call_available': Counter(), 'support_status': Counter(),
            'call_admission_reason': Counter(), 'call_admission_details_state': Counter(),
            'call_admission_declared_scope': Counter(), 'call_admission_replacement_opcode_state': Counter(),
            'fact_namespace_instances': Counter(), 'source_manual_reference_occurrences': Counter()})
        counts = row['counts']
        facts = entry.get('facts', [])
        counts['owners'] += 1
        counts['fact_instances'] += len(facts)
        counts['empty_owners'] += not facts
        material = bool(entry.get('source_materials'))
        sourced = any(bool(f.get('sources')) for f in facts)
        counts['owners_with_source_materials'] += material
        counts['owners_with_fact_source_references'] += sourced
        counts['owners_with_neither_source_materials_nor_fact_sources'] += not material and not sourced
        counts['empty_owners_without_source_materials'] += not facts and not material
        call = entry.get('call_available')
        label = 'true' if call is True else 'false' if call is False else '<missing>' if 'call_available' not in entry else '<non-boolean>'
        self.add(row['call_available'], label)
        self.add(row['support_status'], self.label(entry.get('support_status')))
        self.admission(row, entry)
        for fact in facts:
            ident = fact.get('id')
            # A delimiter-free ID is not emitted: it might be a complete identity.
            namespace = self.label(ident.split(':', 1)[0]) if isinstance(ident, str) and ':' in ident else '<no-colon-namespace>'
            self.add(row['fact_namespace_instances'], namespace)
            scope = fact.get('scope')
            if not isinstance(scope, dict):
                counts['facts_without_object_scope'] += 1
            else:
                for field, expected in [('models', entry.get('target_model')), ('forms', entry.get('opcode'))]:
                    value = scope.get(field)
                    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
                        counts['facts_without_literal_' + field + '_scope'] += 1
                    else:
                        counts['facts_with_literal_' + field + '_scope'] += 1
                        counts['facts_' + field + '_scope_excludes_owner'] += expected not in value
                        counts['facts_' + field + '_scope_not_exact_single_owner'] += value != [expected]
            for source in fact.get('sources', []):
                self.add(row['source_manual_reference_occurrences'], self.label(source.get('manual_id') if isinstance(source, dict) else None))
        return row

    def report(self):
        return {'by_model': {model: {k: dict(v) for k, v in row.items()} for model, row in self.models.items()},
                'bounds': {'model_bins': 96, 'category_bins_per_model': 24, 'technical_label_characters': 64,
                           'overflow_bins_are_explicit': True},
                'interpretation': [
                    'Counts describe stored owner instances, not executable instruction registrations.',
                    'call_available/support_status are literal entry fields; missing values are not inferred.',
                    'call_admission_reason/details are literal compiler annotations; absent fields remain missing, never reconstructed from call_available.',
                    'Compiler admission is not engineering-tool support, native compilation evidence or hardware validity; false does not remove knowledge.',
                    'admitted_with_empty_declared_scope records legacy permissive admission, not verified support for every CPU.',
                    'Admission details report only declared_scope and replacement-token presence; free text, replacement identities and unknown fields are not emitted.',
                    'Namespace and manual IDs identify labels, not CPU applicability or validity.',
                    'FX5-like namespace on a Q owner is counted without automatically declaring invalid cross-model reuse.',
                    'Source-reference counts are occurrences, not unique evidence or proof of reviewed authorship.',
                    'Scope exclusion and non-exact scope are separate observations, not automatic semantic rejection.',
                    'No-source and empty-owner bins are descriptive; neither is labeled an unsupported or invalid instruction.']}


def audit_package(path, *, max_unit_bytes=64 * 1024 * 1024, owner_breakdown=False):
    path = Path(path).resolve()
    started = time.perf_counter()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(data)
    # Pass one collects root metadata even when it follows the large array.
    metadata, array_names = {}, set()
    with open_text(path) as stream:
        parser = JSONUnits(stream, max_unit_bytes)
        for kind, key, value in parser.fields():
            if kind == 'field':
                metadata[key] = value
            elif kind == 'array_end':
                array_names.add(key)
        maximum_unit = parser.maximum
    if any(k in metadata for k in ('source_serialization', 'value_serialization', 'source_catalog', 'value_catalog')):
        raise ValueError('Pooled runtime transport is not supported by this audit; no source counts were inferred')
    version = metadata.get('schema_version')
    if version == 1 and 'entries' in array_names:
        array_key = 'entries'
    elif (version == 2 and 'definitions' in array_names and
          metadata.get('serialization') == 'common+form_diff+model_diff-v1' and
          metadata.get('verification_inheritance') is False):
        array_key = 'definitions'
    else:
        raise ValueError('Unsupported runtime schema; no counts were inferred')
    counts, statuses, dimensions, field_bytes = Counter(), Counter(), Counter(), Counter()
    provenance = OwnerBreakdown() if owner_breakdown else None
    with tempfile.TemporaryDirectory(prefix='runtime-fact-audit-') as temporary:
        db = sqlite3.connect(str(Path(temporary) / 'identities.sqlite'))
        db.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-4096;'
                         'CREATE TABLE owners(id TEXT PRIMARY KEY);'
                         'CREATE TABLE bare_ids(id TEXT PRIMARY KEY);'
                         'CREATE TABLE source_refs(id TEXT PRIMARY KEY);')
        with open_text(path) as stream:
            parser = JSONUnits(stream, max_unit_bytes)
            for kind, key, value in parser.fields():
                if kind != 'item' or key != array_key:
                    continue
                entries = (value,) if version == 1 else family_entries(value, metadata['model_profiles'])
                for entry in entries:
                    if not isinstance(entry, dict):
                        raise ValueError('Entry must be an object')
                    owner = [entry.get('vendor', 'mitsubishi'), entry.get('target_model'), entry.get('opcode')]
                    if any(not isinstance(v, str) or not v for v in owner):
                        raise ValueError('Owner must use literal nonempty strings')
                    try:
                        db.execute('INSERT INTO owners VALUES (?)', (canonical(owner),))
                    except sqlite3.IntegrityError:
                        raise ValueError('Duplicate definition owner') from None
                    counts['owners'] += 1
                    facts = entry.get('facts', [])
                    if not isinstance(facts, list):
                        raise ValueError('Facts must be a list')
                    if provenance is not None:
                        provenance.owner(entry)
                    ids = {fact['id'] for fact in facts}
                    if len(ids) != len(facts):
                        raise ValueError('Duplicate fact ID within owner')
                    counts['owner_qualified_fact_instances'] += len(facts)
                    counts['expanded_owner_json_bytes'] += len(canonical(entry).encode('utf-8'))
                    counts['maximum_facts_per_owner'] = max(counts['maximum_facts_per_owner'], len(facts))
                    for fact in facts:
                        db.execute('INSERT OR IGNORE INTO bare_ids VALUES (?)', (fact['id'],))
                        statuses[fact.get('status', '<missing>')] += 1
                        dimensions[fact.get('dimension', '<missing>')] += 1
                        for k, v in fact.items():
                            field_bytes[k] += len(canonical(v).encode('utf-8'))
                        for dep in fact.get('depends_on', []):
                            counts['dependency_references'] += 1
                            counts['dependencies_not_found_within_owner'] += dep not in ids
                        for source in fact.get('sources', []):
                            counts['source_reference_occurrences'] += 1
                            db.execute('INSERT OR IGNORE INTO source_refs VALUES (?)',
                                       (hashlib.sha256(canonical(source).encode()).hexdigest(),))
            maximum_unit = max(maximum_unit, parser.maximum)
        counts['distinct_bare_fact_ids'] = db.execute('SELECT count(*) FROM bare_ids').fetchone()[0]
        counts['unique_exact_source_reference_objects'] = db.execute('SELECT count(*) FROM source_refs').fetchone()[0]
        db.close()
    # Exact decompressed bytes, bounded read; unlike expanded-owner bytes above,
    # this measures the actual stored JSON representation, including whitespace.
    with path.open('rb') as stream:
        gz = stream.read(2) == b'\x1f\x8b'
    decompressed_bytes = 0
    with (gzip.open(path, 'rb') if gz else path.open('rb')) as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            decompressed_bytes += len(data)
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == 'darwin':
            peak //= 1024
    except ImportError:
        peak = None
    return {**({'owner_provenance': provenance.report()} if provenance is not None else {}),
            'audit_schema': 1, 'input_name': path.name, 'input_sha256': digest.hexdigest(),
            'input_bytes': path.stat().st_size, 'stored_json_decompressed_bytes': decompressed_bytes,
            'runtime_schema_version': version, 'counts': dict(counts), 'status_counts': dict(statuses),
            'dimension_counts': dict(dimensions), 'fact_field_value_json_bytes': dict(field_bytes),
            'maximum_parsed_json_unit_bytes': maximum_unit, 'max_unit_limit_bytes': max_unit_bytes,
            'peak_rss_kib': peak, 'peak_rss_note': 'Audit process peak, not runtime-loader peak; null when unavailable.',
            'elapsed_seconds': time.perf_counter() - started,
            'limitations': ['No fact text, source prose or operand examples are emitted.',
                           'Expanded owner byte count is a sum of canonical entry JSON, not the complete output document.',
                           'This does not inspect SQLite chunks. Chunk fact occurrences cannot be inferred from definition facts.',
                           'Parsing is bounded by one entry/family, not one fact. Oversized units fail closed.']}


def audit_owner_store(path, *, max_unit_bytes=64 * 1024 * 1024, owner_breakdown=False):
    """Offline full-owner audit through the store API, not a startup benchmark."""
    from plc.instruction_owner_store import OwnerDefinitionStore, SERIALIZATION
    started = time.perf_counter()
    store = OwnerDefinitionStore(path)
    counts, statuses, dimensions, field_bytes = Counter(), Counter(), Counter(), Counter()
    provenance = OwnerBreakdown() if owner_breakdown else None
    maximum = 0
    try:
        manifest = json.loads(store.manifest_path.read_text(encoding='utf-8'))
        manifest_sha = hashlib.sha256(store.manifest_path.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix='runtime-owner-audit-') as temporary:
            db = sqlite3.connect(str(Path(temporary) / 'identities.sqlite'))
            try:
                db.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-4096;'
                    'CREATE TABLE bare_ids(id TEXT PRIMARY KEY); CREATE TABLE source_refs(id TEXT PRIMARY KEY);')
                for header in store.iter_owner_headers():
                    entry = store.get_entry(header['opcode'], cpu=header['target_model'], vendor=header['vendor'])
                    if entry is None:
                        raise ValueError('Owner header has no stored definition')
                    size = len(canonical(entry).encode('utf-8'))
                    maximum = max(maximum, size)
                    if size > max_unit_bytes:
                        raise ValueError('Hydrated owner exceeds max-unit limit')
                    facts = entry.get('facts', [])
                    counts['owners'] += 1
                    counts['owner_qualified_fact_instances'] += len(facts)
                    counts['expanded_owner_json_bytes'] += size
                    counts['maximum_facts_per_owner'] = max(counts['maximum_facts_per_owner'], len(facts))
                    if provenance is not None:
                        provenance.owner(entry)
                    ids = {fact['id'] for fact in facts}
                    for fact in facts:
                        db.execute('INSERT OR IGNORE INTO bare_ids VALUES (?)', (fact['id'],))
                        statuses[fact.get('status', '<missing>')] += 1
                        dimensions[fact.get('dimension', '<missing>')] += 1
                        for key, value in fact.items():
                            field_bytes[key] += len(canonical(value).encode('utf-8'))
                        for dependency in fact.get('depends_on', []):
                            counts['dependency_references'] += 1
                            counts['dependencies_not_found_within_owner'] += dependency not in ids
                        for source in fact.get('sources', []):
                            counts['source_reference_occurrences'] += 1
                            db.execute('INSERT OR IGNORE INTO source_refs VALUES (?)',
                                (hashlib.sha256(canonical(source).encode()).hexdigest(),))
                    del entry, facts, ids
                    fact = source = value = None  # Do not retain the previous owner's payload.
                counts['distinct_bare_fact_ids'] = db.execute('SELECT count(*) FROM bare_ids').fetchone()[0]
                counts['unique_exact_source_reference_objects'] = db.execute('SELECT count(*) FROM source_refs').fetchone()[0]
            finally:
                db.close()
        if not store.current() or counts['owners'] != store.owner_count:
            raise ValueError('Owner store changed or owner count differs')
        stats = store.stats()
        try:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform == 'darwin':
                peak //= 1024
        except ImportError:
            peak = None
        return {**({'owner_provenance': provenance.report()} if provenance is not None else {}),
            'audit_schema': 1, 'serialization': SERIALIZATION, 'offline_full_owner_scan': True,
            'manifest_sha256': manifest_sha, 'manifest_bytes': store.manifest_path.stat().st_size,
            'database_bytes': manifest['database']['bytes'], 'database_sha256': manifest['database']['sha256'],
            'counts': dict(counts), 'status_counts': dict(statuses), 'dimension_counts': dict(dimensions),
            'fact_field_value_json_bytes': dict(field_bytes), 'store_validation_stats': stats,
            'maximum_hydrated_owner_json_bytes': maximum, 'max_unit_limit_bytes': max_unit_bytes,
            'elapsed_seconds': time.perf_counter() - started, 'peak_rss_kib': peak,
            'limitations': [
                'This is an offline scan of every owner through the verified read-only store API, not lazy runtime startup.',
                'Size is checked after one owner is hydrated; max-unit is not a hard peak-RAM bound for this mode.',
                'Database size is physical SQLite size, not compressed size or proof that storage expansion is solved.',
                'Counts do not prove compiler admission, engineering-tool support, native compilation or hardware validity.',
                'No fact prose or owner identities are emitted; language-feature payloads are not scanned by this owner audit.']}
    finally:
        store.close()


def audit_sqlite(path, *, max_unit_bytes=64 * 1024 * 1024):
    """Count structured facts in each stored chunk JSON, not definition entries.

    Read-only; no VACUUM, migration, journal mode change or automatic hydration.
    Unstructured text and reference-only packets are reported separately.
    """
    path = Path(path).resolve()
    source = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    source.execute('PRAGMA query_only=ON')
    tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'chunks' not in tables:
        source.close()
        raise ValueError('SQLite has no chunks table; chunk counts were not inferred')
    columns = {r[1] for r in source.execute('PRAGMA table_info(chunks)')}
    text_column = next((k for k in ('text', 'content', 'chunk_text') if k in columns), None)
    if text_column is None:
        source.close()
        raise ValueError('SQLite chunks has no supported text column')
    counts = Counter()
    dimensions = Counter()
    # All column identifiers are fixed literals selected from the schema above.
    with tempfile.TemporaryDirectory(prefix='runtime-chunk-audit-') as temporary:
        distinct = sqlite3.connect(str(Path(temporary) / 'identities.sqlite'))
        distinct.execute('CREATE TABLE ids(owner TEXT,id TEXT,PRIMARY KEY(owner,id))')
        try:
            for rowid, size in source.execute('SELECT rowid,length(CAST("' + text_column + '" AS BLOB)) FROM chunks'):
                counts['chunk_rows'] += 1
                if size is not None and size > max_unit_bytes:
                    raise ValueError('SQLite chunk exceeds --max-unit-mib; audit stopped without omitting it')
                text = source.execute('SELECT "' + text_column + '" FROM chunks WHERE rowid=?', (rowid,)).fetchone()[0]
                counts['stored_chunk_text_bytes'] += size or 0
                counts['maximum_chunk_text_bytes'] = max(counts['maximum_chunk_text_bytes'], size or 0)
                metadata_ref = False
                if 'metadata_json' in columns:
                    metadata_size = source.execute('SELECT length(CAST(metadata_json AS BLOB)) FROM chunks WHERE rowid=?', (rowid,)).fetchone()[0]
                    if metadata_size is not None and metadata_size > max_unit_bytes:
                        raise ValueError('SQLite chunk metadata exceeds --max-unit-mib')
                    metadata = source.execute('SELECT metadata_json FROM chunks WHERE rowid=?', (rowid,)).fetchone()[0]
                    try:
                        metadata = json.loads(metadata or '{}')
                        metadata_ref = isinstance(metadata, dict) and 'runtime_owner' in metadata
                    except (ValueError, TypeError):
                        counts['non_json_metadata_rows'] += 1
                try:
                    packet = json.loads(text or '')
                except (ValueError, TypeError):
                    counts['non_json_chunk_rows'] += 1
                    counts['runtime_owner_reference_chunks'] += metadata_ref
                    continue
                counts['json_chunk_rows'] += 1
                stack = [(packet, None)]
                refs = metadata_ref
                while stack:
                    item, owner = stack.pop()
                    if isinstance(item, dict):
                        if isinstance(item.get('target_model'), str) and isinstance(item.get('opcode'), str):
                            owner = canonical([item.get('vendor', 'mitsubishi'), item['target_model'], item['opcode']])
                        if 'runtime_owner' in item:
                            refs = True
                        if all(k in item for k in ('id', 'dimension', 'status', 'value')):
                            counts['chunk_fact_occurrences'] += 1
                            dimensions[str(item['dimension'])] += 1
                            if owner is None:
                                counts['fact_occurrences_without_literal_owner'] += 1
                            else:
                                distinct.execute('INSERT OR IGNORE INTO ids VALUES (?,?)', (owner, str(item['id'])))
                        stack.extend((v, owner) for v in item.values() if isinstance(v, (dict, list)))
                    elif isinstance(item, list):
                        stack.extend((v, owner) for v in item if isinstance(v, (dict, list)))
                counts['runtime_owner_reference_chunks'] += refs
            counts['distinct_owner_qualified_fact_ids_in_chunks'] = distinct.execute('SELECT count(*) FROM ids').fetchone()[0]
        finally:
            distinct.close()
            source.close()
    return {'input_name': path.name, 'input_bytes': path.stat().st_size,
            'selected_chunk_text_column': text_column, 'counts': dict(counts),
            'dimension_occurrence_counts': dict(dimensions),
            'limitations': ['Occurrences count embedded structured fact objects in the selected chunk text column only.',
                           'Non-JSON chunks and literal-owner omissions are explicit; no owners are guessed.',
                           'Reference-only chunks are counted but not expanded; zero embedded facts does not mean no runtime knowledge.',
                           'This audit does not read private prose into its report or mutate the database.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--definitions', type=Path)
    parser.add_argument('--owner-manifest', type=Path, help='Audit every owner through a verified sibling SQLite store manifest')
    parser.add_argument('--sqlite', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--max-unit-mib', type=int, default=64)
    parser.add_argument('--owner-breakdown', action='store_true', help='Include bounded per-model owners, literal compiler admission annotations, namespaces and source-label counts')
    args = parser.parse_args()
    if args.max_unit_mib <= 0:
        parser.error('--max-unit-mib must be positive')
    if not args.definitions and not args.sqlite and not args.owner_manifest:
        parser.error('Provide --definitions, --owner-manifest, --sqlite or a combination')
    if any(args.output.resolve() == p.resolve() for p in (args.definitions, args.sqlite, args.owner_manifest) if p is not None):
        parser.error('Output must not overwrite the input package')
    if args.output.exists():
        parser.error('Output already exists; choose a new audit filename')
    try:
        result = {'audit_schema': 1}
        if args.definitions:
            result['definitions'] = audit_package(args.definitions, max_unit_bytes=args.max_unit_mib * 1024 * 1024, owner_breakdown=args.owner_breakdown)
        if args.owner_manifest:
            result['owner_store'] = audit_owner_store(args.owner_manifest, max_unit_bytes=args.max_unit_mib * 1024 * 1024, owner_breakdown=args.owner_breakdown)
        if args.sqlite:
            result['sqlite_chunks'] = audit_sqlite(args.sqlite, max_unit_bytes=args.max_unit_mib * 1024 * 1024)
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        parser.exit(2, 'Audit failed: ' + str(exc) + '\n')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Audit written: ' + str(args.output))


if __name__ == '__main__':
    main()
