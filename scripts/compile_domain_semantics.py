"""Compile reviewed non-instruction knowledge into the existing fact corpus.

The source index is read only. Other domains and instruction-owner resources are
preserved. Changed exact/FTS/LSA entries are published together to a new directory.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import shutil
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from knowledge.fact_corpus import CORPUS_KIND, PUBLICATION_STATUS
from knowledge.evidence import text_sha256
from tools.fact_diagnostic_review import expand_diagnostic_definitions, bind_diagnostic_sources
from tools.fact_device_review import reviewed_device_records


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _packet(text):
    for line in text.splitlines():
        if line.startswith('{'):
            value = json.loads(line)
            if isinstance(value, dict) and value.get('kind'):
                return value
    raise ValueError('Expected an authored domain packet')


def diagnostic_scope_records(connection, source, sections, *, reviewed_ids=()):
    """Narrow existing diagnostic scopes to reviewed source chapter headings.

    This changes applicability only. Unknown causes/actions remain unknown and
    do not acquire source verification from a chapter-scope correction.
    """
    records, claimed, reviewed = [], set(), set(reviewed_ids)
    for section in sections:
        identity = section['source']
        manual = source.execute('SELECT manual_number,revision,plc_models FROM manuals WHERE manual_id=?',
                                (identity['manual_id'],)).fetchone()
        if manual is None or tuple(manual[:2]) != (identity['manual_number'], identity['revision']):
            raise ValueError('Diagnostic scope heading has a different source edition')
        models = set(section['models'])
        if not models or not models <= set(re.split(r'[,|; ]+', manual[2] or '')):
            raise ValueError('Diagnostic scope heading escapes its manual')
        start, end = section['pdf_page_start'], section['pdf_page_end']
        pages = section['heading_pdf_pages']
        if type(start) is not int or type(end) is not int or start > end or not pages:
            raise ValueError('Diagnostic scope heading needs a bounded section')
        for page in [start, end, *pages]:
            if source.execute('SELECT 1 FROM page_artifacts WHERE manual_id=? AND pdf_page=?',
                              (identity['manual_id'], page)).fetchone() is None:
                raise ValueError('Diagnostic scope section page is absent')
        rows = connection.execute("SELECT DISTINCT f.id,c.text FROM facts f JOIN chunks c ON c.id=f.chunk_id "
            "WHERE f.kind='error' AND c.manual_id=? AND c.revision=? AND c.pdf_page>=? AND c.pdf_page_end<=?",
            (identity['manual_id'], identity['revision'], start, end)).fetchall()
        for record_id, text in rows:
            if record_id in reviewed:
                continue
            if record_id in claimed:
                raise ValueError('Diagnostic scope sections overlap reviewed identities')
            packet = _packet(text)
            previous = set(packet.get('scope', {}).get('models', ()))
            if previous <= models:
                continue
            narrowed = previous & models
            if not narrowed:
                raise ValueError('Existing diagnostic has no model in its reviewed source section')
            claimed.add(record_id)
            record = {key: copy.deepcopy(packet[key]) for key in
                      ('kind', 'target', 'value', 'sources', 'conditions', 'gaps', 'status') if key in packet}
            record.update(id=record_id, scope={**packet.get('scope', {}), 'models':sorted(narrowed)},
                          verification=packet.get('verification', 'not_performed'))
            for key, default in [('conditions', []), ('gaps', []), ('status', 'candidate_evidence')]:
                record.setdefault(key, default)
            record['conditions'].append({'source_section_models':sorted(models)})
            records.append(record)
    return records


def update_domain_records(connection, records):
    """Update source-qualified diagnostics and devices in the existing corpus."""
    from knowledge.core import _cjk_bigram_set
    connection.row_factory = sqlite3.Row
    marker = connection.execute("SELECT value FROM meta WHERE key='corpus_kind'").fetchone()
    if not marker or marker[0] != CORPUS_KIND:
        raise ValueError('Only an authored fact corpus can receive reviewed semantics')
    changed, added, refreshed, claimed_chunks = set(), 0, 0, set()
    for original_record in records:
        record = copy.deepcopy(original_record)
        kind = record['kind']
        if kind not in {'error','device'}:
            raise ValueError('This compiler step accepts reviewed diagnostics and devices')
        template = connection.execute('SELECT * FROM chunks ORDER BY fact_kind!=?,id LIMIT 1',(kind,)).fetchone()
        if template is None:
            raise ValueError('An existing authored fact corpus is required')
        template = dict(template)
        rows = connection.execute('SELECT DISTINCT c.* FROM facts f JOIN chunks c ON c.id=f.chunk_id WHERE f.id=?', (record['id'],)).fetchall()
        if len(rows) > 1:
            raise ValueError('One domain identity points to several corpus records')
        # A previously compiled table row may predate source-qualified IDs.
        # Reuse that exact row rather than leaving its old unqualified copy.
        if not rows:
            source = record['sources'][0]
            pages = source.get('pdf_pages') or [source['pdf_page']]
            candidates = connection.execute("SELECT DISTINCT c.* FROM facts f JOIN chunks c ON c.id=f.chunk_id "
                "WHERE f.kind=? AND f.target=? AND c.manual_id=? AND c.revision=? AND c.pdf_page=? AND c.pdf_page_end=?",
                (kind, record['target'], source['manual_id'], source['revision'], min(pages), max(pages))).fetchall()
            rows = [candidate for candidate in candidates
                    if (kind != 'error' or _packet(candidate['text']).get('value', {}).get('register') == record['value'].get('register'))
                    and set(_packet(candidate['text']).get('scope',{}).get('models',())) == set(record['scope']['models'])]
            if len(rows) > 1:
                raise ValueError('A domain source row is ambiguous across prior identities')
        before = dict(rows[0]) if rows else None
        if before:
            if before['id'] in claimed_chunks:
                raise ValueError('Two domain definitions claim one existing corpus row')
            claimed_chunks.add(before['id'])
        packet = _packet(before['text']) if before else {
            'kind': kind, 'target': record['target'], 'content_origin': 'authored_fact',
            'distribution_status': PUBLICATION_STATUS,
            'rights_basis': 'factual_representation_review;not_vendor_licence'}
        source = record['sources'][0]
        pages = source.get('pdf_pages') or [source['pdf_page']]
        if before:
            if (packet['kind'] != kind or packet['target'] != record['target']
                    or before['manual_id'] != source['manual_id'] or before['revision'] != source['revision']
                    or before['pdf_page'] not in pages):
                raise ValueError('Domain review targets a different corpus source record')
            if not set(record['scope']['models']) <= set(packet.get('scope', {}).get('models', [])):
                raise ValueError('An existing domain fact cannot acquire an unreviewed model scope')
        record.setdefault('gaps', copy.deepcopy(packet.get('gaps', ['device_semantic_completeness_not_reviewed'])))
        prior_claim = {key: copy.deepcopy(packet[key]) for key in
                       ('value', 'scope', 'sources', 'conditions', 'gaps', 'status', 'technical_status') if key in packet}
        # Replace old placeholder interpretations, retaining them in backend
        # metadata rather than putting superseded claims into search text.
        packet.update({key: copy.deepcopy(record[key]) for key in
                       ('kind', 'target', 'scope', 'value', 'sources', 'conditions', 'gaps', 'status', 'verification')})
        packet['technical_status'] = record['status']
        metadata = json.loads(before['metadata_json']) if before else {}
        current_claim = {key: copy.deepcopy(packet[key]) for key in prior_claim}
        if before and _json(prior_claim) != _json(current_claim):
            history = metadata.setdefault('superseded_facts', [])
            if all(_json(item) != _json(prior_claim) for item in history):
                history.append(prior_claim)
        metadata.update(content_origin='authored_fact', distribution_status=PUBLICATION_STATUS,
            fact_kind=kind, fact_target=record['target'], technical_status=record['status'],
            source_evidence=record['sources'], evidence_scope='typed_facts_only', structured_lookup=True,
            structured_fact_kind=kind, structured_fact_target=record['target'],
            match_type='structured_direct', instruction_lookup_basis='authored_typed_definition')
        models = record['scope']['models']
        qualifier = record['value'].get('register','unresolved_register') if kind == 'error' else record['value'].get('semantic_facts',{}).get('function','device_definition')
        text = ('错误码 ' if kind == 'error' else '软元件 ') + record['target'] + ' / ' + qualifier + ' / ' + record['value'].get('meaning', 'unknown' if kind == 'error' else kind) + '\n' + _json(packet)
        entities = list(dict.fromkeys([record['target'], *re.findall(r'\b(?:[A-Z][A-Z0-9_]{1,32}|[XYMDTCSZ]\d+)\b', _json(record['value']))]))
        row = copy.deepcopy(before or template)
        row.update(manual_id=source['manual_id'], manual_number=source['manual_number'], revision=source['revision'],
            plc_models='|'.join(models), pdf_page=min(pages), pdf_page_end=max(pages),
            section=kind + ' / ' + record['target'] + ' / ' + qualifier,
            chunk_type=kind, instruction_opcode='', text=text, text_sha256=text_sha256(text),
            entities=' '.join(entities), cjk_bigrams=' '.join(sorted(_cjk_bigram_set(text))),
            content_origin='authored_fact', distribution_status=PUBLICATION_STATUS,
            fact_kind=kind, fact_target=record['target'], metadata_json=_json(metadata))
        if kind == 'device':
            row['task_types'] = 'analysis|generate|edit|debug'
        if before:
            chunk_id = before['id']
            if connection.execute('SELECT count(DISTINCT id) FROM facts WHERE chunk_id=?', (chunk_id,)).fetchone()[0] != 1:
                raise ValueError('A domain chunk is shared with another identity')
            prior_ids = {item[0] for item in connection.execute('SELECT DISTINCT id FROM facts WHERE chunk_id=?', (chunk_id,))}
            if all(row[key] == before[key] for key in row) and prior_ids == {record['id']}:
                continue
            columns = [key for key in row if key != 'id']
            connection.execute('UPDATE chunks SET ' + ','.join(key+'=?' for key in columns) + ' WHERE id=?',
                               [*[row[key] for key in columns], chunk_id])
            connection.execute('DELETE FROM facts WHERE chunk_id=?', (chunk_id,))
            connection.execute('DELETE FROM entity_index WHERE chunk_id=?', (chunk_id,))
            refreshed += 1
        else:
            row.pop('id', None)
            columns = list(row)
            chunk_id = connection.execute('INSERT INTO chunks('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',
                                           [row[key] for key in columns]).lastrowid
            added += 1
        for model in models:
            connection.execute('INSERT INTO facts VALUES(?,?,?,?,?,?)',
                               (record['id'], kind, record['target'], model, chunk_id, _json({'metadata': {}})))
        for entity in entities:
            connection.execute('INSERT INTO entity_index VALUES(?,?,?,?,?,?,?,?)',
                (entity.casefold(), entity, kind, '|'.join(models), row['task_types'], source['manual_id'], chunk_id, 1))
        changed.add(chunk_id)
    connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
    return {'changed_ids': changed, 'records_updated': refreshed, 'records_added': added}


def update_diagnostic_records(connection, records):
    """Compatibility entry point for register-qualified diagnostic compilation."""
    if any(record['kind'] != 'error' for record in records):
        raise ValueError('This compiler step accepts reviewed diagnostics')
    return update_domain_records(connection, records)


def refresh_dense_entries(connection, basis_path, output_path, changed_ids):
    """Project changed entries through the installed local LSA basis."""
    import numpy as np
    from knowledge.dense import dense_features
    with np.load(basis_path, allow_pickle=False) as archive:
        arrays = {key: archive[key] for key in archive.files}
    metadata = json.loads(str(arrays['metadata_json'].item()))
    stored = dict(connection.execute('SELECT key,value FROM meta'))
    if (stored.get('vector_status') != 'ready' or stored.get('vector_model') != metadata['model']
            or stored.get('vector_corpus_sha256') != metadata['corpus_sha256']):
        raise ValueError('The dense basis does not match the source corpus')
    features = json.loads(str(arrays['feature_names_json'].item()))
    feature_index = {value: index for index, value in enumerate(features)}
    previous = {int(identity): index for index, identity in enumerate(arrays['chunk_ids'])}
    rows = connection.execute('SELECT id,text,text_sha256 FROM chunks ORDER BY id').fetchall()
    vectors = np.zeros((len(rows), arrays['components'].shape[0]), dtype=np.float32)
    digest = hashlib.sha256()
    for index, row in enumerate(rows):
        identity, text, content_hash = row
        digest.update((str(identity)+':'+content_hash+'\n').encode())
        if identity not in changed_ids:
            if identity not in previous:
                raise ValueError('An unchanged corpus row is missing from the dense basis')
            vectors[index] = arrays['chunk_vectors'][previous[identity]]
            continue
        values = np.zeros(len(features), dtype=np.float32)
        for token, count in dense_features(text).items():
            position = feature_index.get(token)
            if position is not None:
                values[position] = (1.0 + np.log(float(count))) * arrays['idf'][position]
        projected = arrays['components'] @ values
        norm = float(np.linalg.norm(projected))
        if not np.isfinite(norm) or norm <= 1e-12:
            raise ValueError('A reviewed diagnostic has no representation in the existing dense basis')
        vectors[index] = projected / norm
        connection.execute('DELETE FROM vector_embeddings WHERE chunk_id=?', (identity,))
        connection.execute('INSERT INTO vector_embeddings VALUES(?,?,?,?,?,?)',
            (identity, metadata['model'], len(projected), vectors[index].tobytes(), 1.0, content_hash))
    previous_metadata = copy.deepcopy(metadata)
    metadata.update(chunks=len(rows), corpus_sha256=digest.hexdigest(), built_at_utc=datetime.now(timezone.utc).isoformat(),
        basis={'mode':'reused','model':previous_metadata['model'],'corpus_sha256':previous_metadata['corpus_sha256'],
               'chunks':previous_metadata['chunks']})
    arrays.update(chunk_ids=np.array([row[0] for row in rows], dtype=np.int64), chunk_vectors=vectors,
                  metadata_json=np.array(_json(metadata)))
    np.savez_compressed(output_path, **arrays)
    artifact_hash = hashlib.sha256(Path(output_path).read_bytes()).hexdigest()
    for key, value in {'vector_corpus_sha256':metadata['corpus_sha256'], 'vector_artifact_sha256':artifact_hash,
                       'vector_artifact':Path(output_path).name}.items():
        connection.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, value))
    return {'changed_vectors':len(changed_ids),'chunks':len(rows),'basis':'reused'}


def compile_domain_semantics(database, dense, manifest, source_index, definitions, output_dir, *, version, device_definitions=None):
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError('Choose a new compilation output directory')
    output_dir.mkdir(parents=True, exist_ok=True)
    files = [Path(database), Path(dense), Path(manifest)]
    if any(path.resolve().is_relative_to(output_dir.resolve()) for path in files):
        raise ValueError('Compilation output must not contain its source inputs')
    definition_data = json.loads(Path(definitions).read_text(encoding='utf-8')) if definitions else {}
    with sqlite3.connect(Path(source_index).resolve().as_uri()+'?mode=ro&immutable=1', uri=True) as source:
        records = bind_diagnostic_sources(source, expand_diagnostic_definitions(definition_data)) if definitions else []
        devices = reviewed_device_records(source,json.loads(Path(device_definitions).read_text(encoding='utf-8'))) if device_definitions else []
    target_database = output_dir / Path(database).name
    with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro&immutable=1', uri=True) as original:
        with sqlite3.connect(target_database) as compiled:
            original.backup(compiled)
            with sqlite3.connect(Path(source_index).resolve().as_uri()+'?mode=ro&immutable=1', uri=True) as source:
                scope_records = diagnostic_scope_records(compiled, source, definition_data.get('scope_sections', []),
                                                         reviewed_ids=[record['id'] for record in records])
            result = update_domain_records(compiled, [*records, *scope_records, *devices])
            dense_result = refresh_dense_entries(compiled, dense, output_dir / Path(dense).name, result['changed_ids'])
            compiled.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('domain_semantics_version', version))
            compiled.commit()
            counts = dict(compiled.execute('SELECT kind,count(DISTINCT chunk_id) FROM facts GROUP BY kind'))
    data = json.loads(Path(manifest).read_text(encoding='utf-8'))
    data.update(database=target_database.name, database_bytes=target_database.stat().st_size,
                domain_semantics_version=version, stats={key:counts.get(key, count) for key,count in data.get('stats', {}).items()})
    if 'database_sha256' in data:
        data['database_sha256'] = hashlib.sha256(target_database.read_bytes()).hexdigest()
    target_dense = output_dir / Path(dense).name
    import numpy as np
    with np.load(target_dense, allow_pickle=False) as archive:
        dense_metadata = json.loads(str(archive['metadata_json'].item()))
    data.setdefault('retrieval', {}).update(dense_model=dense_metadata, dense_artifact=target_dense.name,
        dense_artifact_bytes=target_dense.stat().st_size,
        dense_artifact_sha256=hashlib.sha256(target_dense.read_bytes()).hexdigest(), vector_status='ready')
    verification = data.setdefault('verification', {})
    verification.setdefault('counts', {})['vector_embeddings'] = dense_result['chunks']
    verification['vector_status'] = 'ready'
    (output_dir/Path(manifest).name).write_bytes((json.dumps(data, ensure_ascii=False, indent=2)+'\n').encode('utf-8'))
    # Preserve the existing flat resource for builders consuming reviewed index
    # rows. Manually compiled rows missing from the index remain explicit too.
    existing, additional = [], []
    for record in records:
        value = {**record['value'], 'code':record['target'], 'pdf_page':min(record['sources'][0]['pdf_pages']),
                 'conditions':record['conditions'], 'gaps':record['gaps']}
        if re.fullmatch(r'diagnostic:[0-9]+', record['id']):
            value['source_record_id'] = int(record['id'].split(':')[-1]); existing.append(value)
        else:
            value.update(id=record['id'], models=record['scope']['models'], sources=record['sources']); additional.append(value)
    flat={'schema_version':1,'origin':'project_authored_factual_organization','review_basis':'source_bound_diagnostic_semantics',
          'records':existing,'additional_records':additional}
    if definitions:
        (output_dir/'diagnostic_facts.json').write_bytes((json.dumps(flat, ensure_ascii=False, indent=2)+'\n').encode('utf-8'))
    elif (Path(database).parent/'diagnostic_facts.json').is_file():
        shutil.copyfile(Path(database).parent/'diagnostic_facts.json',output_dir/'diagnostic_facts.json')
    return {'version':version,'reviewed_records':len(records), 'reviewed_device_records':len(devices), 'records_updated':result['records_updated'],
            'records_added':result['records_added'], 'scope_records_corrected':len(scope_records), **dense_result, 'output_directory':str(output_dir)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--dense', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--source-index', type=Path, required=True)
    parser.add_argument('--definitions', type=Path)
    parser.add_argument('--device-definitions', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--version', required=True)
    args=parser.parse_args()
    definitions = args.definitions if args.definitions or args.device_definitions else ROOT/'resources/knowledge/diagnostic_semantics.json'
    print(_json(compile_domain_semantics(args.database,args.dense,args.manifest,args.source_index,definitions,args.output_dir,version=args.version,device_definitions=args.device_definitions)))


if __name__=='__main__':main()
