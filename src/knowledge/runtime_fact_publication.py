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
from knowledge.exact_search_storage import create_search_pool, insert_chunk


_SCHEMA_SQL = '''
          CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE runtime_fact_fields(id INTEGER PRIMARY KEY,payload_json TEXT NOT NULL,sha256 TEXT NOT NULL UNIQUE);
          CREATE TABLE runtime_owner_index(vendor TEXT NOT NULL,opcode TEXT NOT NULL,target_model TEXT NOT NULL,owner TEXT NOT NULL UNIQUE,packet_sha256 TEXT NOT NULL,PRIMARY KEY(vendor,opcode,target_model));
          CREATE TABLE runtime_language_features(target TEXT PRIMARY KEY,payload_json TEXT NOT NULL,sha256 TEXT NOT NULL);
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
        '''

def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def validate_language_feature(feature, *, published=False):
    """Validate production language-owner metadata without inferring fields.

    A missing language stays missing. Its target namespace and every fact's
    explicit language scope must agree. Model lists are upper bounds, not
    replacements for narrower fact/reference applicability.
    """
    import re
    from knowledge.fact_corpus import typed_value, source_reference, public_authority_decision
    from plc.instruction_definition import InstructionFactGroup
    allowed = {'target', 'language', 'models', 'aliases', 'references', 'facts'}
    if not isinstance(feature, dict) or set(feature) - allowed:
        raise ValueError('Unreviewed language feature metadata')
    target = feature.get('target')
    if not isinstance(target, str) or not re.fullmatch(r'(ST|FBD)\.[A-Z0-9_.]+', target):
        raise ValueError('Invalid exact language feature target')
    language = target.partition('.')[0].lower()
    if 'language' in feature and feature['language'] != language:
        raise ValueError('Language feature namespace mismatch')
    def strings(value, name, tokens=False):
        if not isinstance(value, list) or any(not isinstance(x,str) or not x or (tokens and not re.fullmatch(r'[A-Za-z0-9_.+-]+',x)) for x in value) or len(value)!=len(set(value)):
            raise ValueError('Invalid language '+name)
    if 'models' in feature:strings(feature['models'],'models',True)
    if 'aliases' in feature:
        strings(feature['aliases'],'aliases')
        if any(any(ord(c)<32 for c in alias) for alias in feature['aliases']):raise ValueError('Invalid language alias control character')
    facts=feature.get('facts',[])
    if not isinstance(facts,list):raise ValueError('Language facts must be a list')
    ids=set();graph={};by_id={}
    for fact in facts:
        allowed_fact={'id','dimension','value','status','sources','depends_on','members','scope','verification','conditions','authority_decision','note','note_kind'}
        if not isinstance(fact,dict) or set(fact)-allowed_fact or not isinstance(fact.get('id'),str) or not fact['id'] or fact['id'] in ids:
            raise ValueError('Invalid or unreviewed language fact metadata')
        if 'note_kind' in fact and fact['note_kind']!='explanation':raise ValueError('Unreviewed language note')
        scope=fact.get('scope',{})
        if not isinstance(scope,dict) or scope.get('forms')!=[target] or scope.get('languages')!=[language]:
            raise ValueError('Language fact must retain exact feature/language scope')
        strings(scope.get('models',[]),'fact models',True)
        if 'models' in feature and (not scope.get('models') or not set(scope['models'])<=set(feature['models'])):
            raise ValueError('Language fact exceeds feature model scope')
        ids.add(fact['id']);graph[fact['id']]=set(fact.get('depends_on',[]));by_id[fact['id']]=fact
        if published:
            InstructionFactGroup.from_mapping(fact)
            for key,default in [('value',{}),('scope',{}),('conditions',[])]:
                if typed_value(fact.get(key,default))!=fact.get(key,default):raise ValueError('Unprojected language technical value')
            if any(source_reference(source)!=source for source in fact.get('sources',[])):raise ValueError('Unprojected language source')
            if fact.get('status') in {'source_verified','candidate_evidence'} and (not fact.get('sources') or any(not s for s in fact['sources'])):raise ValueError('Missing public language source')
            if fact.get('authority_decision') and public_authority_decision(fact['authority_decision'])!=fact['authority_decision']:raise ValueError('Unprojected language authority')
    if published:
        if any(deps-ids for deps in graph.values()):raise ValueError('Missing language dependency')
        for identity,deps in graph.items():
            for dep in deps:
                for axis in ('models','forms','languages'):
                    required=set(by_id[identity]['scope'].get(axis,[]));available=set(by_id[dep]['scope'].get(axis,[]))
                    if required and available and not required<=available:raise ValueError('Inapplicable language dependency')
        while graph:
            ready={identity for identity,deps in graph.items() if not deps}
            if not ready:raise ValueError('Cyclic language fact dependencies')
            graph={identity:deps-ready for identity,deps in graph.items() if identity not in ready}
    references=feature.get('references',[])
    if not isinstance(references,list):raise ValueError('Language references must be a list')
    for ref in references:
        if not isinstance(ref,dict) or set(ref)!={'models','opcode','parameters','sources'}:raise ValueError('Unreviewed language reference metadata')
        strings(ref['models'],'reference models',True)
        if not ref['models'] or ('models' in feature and not set(ref['models'])<=set(feature['models'])):raise ValueError('Language reference exceeds feature model scope')
        if not isinstance(ref['opcode'],str) or not re.fullmatch(r'[A-Z][A-Z0-9_<>+*/=!.\-]*',ref['opcode']):raise ValueError('Invalid related instruction identity')
        if not isinstance(ref['parameters'],dict) or typed_value(ref['parameters'])!=ref['parameters'] or any(not isinstance(v,str) or not v for v in ref['parameters'].values()):raise ValueError('Invalid related parameter binding')
        if not isinstance(ref['sources'],list) or not ref['sources'] or any(not isinstance(s,dict) or not s for s in ref['sources']):raise ValueError('Missing language reference source')
        if published and any(source_reference(s)!=s or not source_reference(s) for s in ref['sources']):raise ValueError('Unprojected language reference source')


def _language_conflict_explanations(facts):
    """Keep explicit conflict explanations as notes, never executable prose."""
    import copy
    import re
    from knowledge.fact_corpus import typed_value, UnresolvedExpression
    result=copy.deepcopy(facts);changes=[]
    for fact in result:
        value=fact.get('value',{})
        conflicts=value.get('source_conflicts',{}) if isinstance(value,dict) else {}
        if not isinstance(conflicts,dict):continue
        for name,conflict in conflicts.items():
            if not isinstance(conflict,dict) or not isinstance(conflict.get('reason'),str):continue
            reason=conflict['reason']
            try:
                typed_value(reason)
                continue
            except UnresolvedExpression:
                pass
            if (set(conflict)!={'models','reason'} or not isinstance(name,str)
                    or not re.fullmatch(r'[A-Z][A-Z0-9_]*',name)
                    or not isinstance(conflict['models'],list)
                    or not set(conflict['models'])<=set(fact.get('scope',{}).get('models',[]))):
                raise ValueError('Unclassified language conflict explanation')
            path='source_conflicts.'+name+'.reason'
            note=(fact.get('note','').strip()+'\n'+path+': '+reason).strip()
            fact['note']=note;fact['note_kind']='explanation'
            conflict['reason']={'kind':'explanation_reference','note_ref':path}
            changes.append({'fact_id':fact['id'],'field':'value.'+path,'original':reason,
                            'projected':copy.deepcopy(conflict['reason']),
                            'destination':'note','semantics':'source_conflict_preserved_not_resolved'})
    return result,changes


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
        validate_language_feature(feature)
        target = feature['target']
        if target in seen:raise ValueError('Duplicate language feature target')
        seen.add(target)
        facts, fact_changes = _language_conflict_explanations(feature.get('facts', []))
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
        import copy
        from knowledge.fact_corpus import source_reference
        result = copy.deepcopy(feature)
        result['facts'] = accepted
        reference_changes=[]
        for index, reference in enumerate(result.get('references', [])):
            original=copy.deepcopy(reference['sources'])
            reference['sources']=[source_reference(source) for source in original]
            if any(not source for source in reference['sources']):raise ValueError('Missing public language reference source')
            if original != reference['sources']:
                reference_changes.append({'reference_index':index,'field':'sources','original':original,'projected':copy.deepcopy(reference['sources'])})
        validate_language_feature(result, published=True)
        output.append(result)
        if rejected or reference_changes or fact_changes:
            audit={'target':target,'withheld':rejected}
            if 'language' in feature:audit['language']=feature['language']
            if reference_changes or fact_changes:audit['metadata_projection']=[*reference_changes,*fact_changes]
            withheld.append(audit)
    return output, withheld


def owner_key(entry):
    return _json([entry.get('vendor', 'mitsubishi'), entry['target_model'], entry['opcode']])


def hydrate_owner_packet(connection, owner, *, load_counts=None):
    version = connection.execute("SELECT value FROM meta WHERE key='runtime_serialization'").fetchone()
    if version is None or version[0] not in {'owner-source-pool-v1','owner-field-pool-v2','owner-field-row-v3'}:
        raise ValueError('Unsupported runtime owner transport')
    row = connection.execute('SELECT payload_json FROM runtime_definitions WHERE owner=?', (owner,)).fetchone()
    if row is None:
        raise ValueError('Missing runtime owner')
    packet = json.loads(row[0])
    facts = packet['value']['facts']
    if version[0] in {'owner-field-pool-v2','owner-field-row-v3'}:
        from knowledge.fact_field_codec import unpack_fact_fields, FIELD_SERIALIZATION
        marker = connection.execute("SELECT value FROM meta WHERE key='field_serialization'").fetchone()
        if marker is None or marker[0] != FIELD_SERIALIZATION:
            raise ValueError('Unsupported exact fact field transport')
        fields = {}
        def resolve_field(identity):
            if type(identity) is not int or identity <= 0:
                raise ValueError('Invalid fact field reference')
            if identity not in fields:
                row = connection.execute('SELECT payload_json,sha256 FROM runtime_fact_fields WHERE id=?',(identity,)).fetchone()
                if row is None or hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
                    raise ValueError('Missing or altered exact fact field')
                fields[identity] = json.loads(row[0])
            return fields[identity]
        if version[0]=='owner-field-row-v3':
            from knowledge.fact_field_codec import unpack_fact_row, FACT_ROW_SERIALIZATION
            row_marker=connection.execute("SELECT value FROM meta WHERE key='fact_row_serialization'").fetchone()
            if row_marker is None or row_marker[0]!=FACT_ROW_SERIALIZATION:raise ValueError('Unsupported exact fact row transport')
            facts=[unpack_fact_row(fact,resolve_field) for fact in facts]
        else:
            facts = [unpack_fact_fields(fact,resolve_field) for fact in facts]
        if load_counts is not None:load_counts['field_load_count'] = len(fields)
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
    if load_counts is not None:
        load_counts.update(source_load_count=len(catalog),value_load_count=len(values))
    return packet


def _write_language_feature(connection, feature):
    """Index canonical/alias identities; store each full feature only once."""
    import re
    from knowledge.fact_corpus import PUBLICATION_STATUS
    validate_language_feature(feature, published=True)
    encoded=_json(feature)
    connection.execute('INSERT INTO runtime_language_features VALUES (?,?,?)',(feature['target'],encoded,hashlib.sha256(encoded.encode()).hexdigest()))
    models=feature.get('models',sorted({m for fact in feature.get('facts',[]) for m in fact['scope'].get('models',[])}))
    aliases=list(dict.fromkeys([feature['target'],*feature.get('aliases',[])]))
    for model in models:
        selected=[f for f in feature.get('facts',[]) if model in f['scope'].get('models',[])]
        refs=[r for r in feature.get('references',[]) if model in r['models']]
        identity='language:'+hashlib.sha256(_json([feature['target'],model]).encode()).hexdigest()
        technical={'target':feature['target'],'aliases':feature.get('aliases',[]),'model':model,
                   'facts':[{k:f[k] for k in ('dimension','value','scope','conditions') if k in f} for f in selected],
                   'references':[{k:r[k] for k in ('opcode','parameters','models')} for r in refs]}
        text=' '.join(sorted(set(re.findall(r'[\w.$:+/-]+',_json(technical)))))
        metadata={'runtime_language':feature['target'],'runtime_language_model':model,'content_origin':'authored_fact',
                  'distribution_status':PUBLICATION_STATUS,'fact_kind':'language','fact_target':feature['target'],'source_lane':'fact','plc_models':[model]}
        insert_chunk(connection,(identity,text,_json(metadata),_json([model]),'','language','authored_fact',PUBLICATION_STATUS))
        for alias in aliases:
            name=alias.upper()
            connection.execute('INSERT OR IGNORE INTO entity_index VALUES (?,?)',(name,identity))
            fact_id=identity+':alias:'+hashlib.sha256(name.encode()).hexdigest()
            connection.execute('INSERT OR IGNORE INTO facts VALUES (?,?,?,?,?,?)',(fact_id,'language',name,model,identity,'{}'))


def _check_language_reference_owners(connection):
    for (text,) in connection.execute('SELECT payload_json FROM runtime_language_features'):
        feature=json.loads(text)
        for ref in feature.get('references',[]):
            for model in ref['models']:
                if connection.execute('SELECT 1 FROM runtime_owner_index WHERE vendor=? AND opcode=? AND target_model=?',('mitsubishi',ref['opcode'],model)).fetchone() is None:
                    raise ValueError('Language feature dependency owner is missing: '+feature['target']+'/'+model+'/'+ref['opcode'])


def hydrate_language_packet(connection, target, model):
    """Restore only this model's feature facts and explicit instruction bindings."""
    import copy
    row=connection.execute('SELECT payload_json,sha256 FROM runtime_language_features WHERE target=?',(target,)).fetchone()
    if row is None or hashlib.sha256(row[0].encode()).hexdigest()!=row[1]:raise ValueError('Missing or altered language feature')
    feature=json.loads(row[0]);validate_language_feature(feature,published=True)
    models=feature.get('models',sorted({m for fact in feature.get('facts',[]) for m in fact['scope'].get('models',[])}))
    if model not in models:raise ValueError('Language feature outside requested model')
    value=copy.deepcopy(feature)
    value['facts']=[fact for fact in value.get('facts',[]) if model in fact['scope'].get('models',[])]
    ids={fact['id'] for fact in value['facts']}
    if any(set(fact.get('depends_on',[]))-ids for fact in value['facts']):raise ValueError('Inapplicable language dependency')
    if 'references' in value:value['references']=[ref for ref in value['references'] if model in ref['models']]
    related=[]
    for ref in value.get('references',[]):
        index=connection.execute('SELECT owner,packet_sha256 FROM runtime_owner_index WHERE vendor=? AND opcode=? AND target_model=?',('mitsubishi',ref['opcode'],model)).fetchone()
        if index is None:raise ValueError('Language feature dependency owner is missing')
        raw=connection.execute('SELECT payload_json FROM runtime_definitions WHERE owner=?',(index[0],)).fetchone()
        if raw is None or hashlib.sha256(raw[0].encode()).hexdigest()!=index[1]:raise ValueError('Altered language instruction dependency')
        entry=hydrate_owner_packet(connection,index[0])['value']
        from plc.instruction_definition import InstructionFactGroup
        if entry.get('opcode')!=ref['opcode'] or entry.get('target_model')!=model:raise ValueError('Wrong related instruction identity')
        graph={}
        for fact in entry.get('facts',[]):
            group=InstructionFactGroup.from_mapping(fact)
            if group.id in graph or group.scope.get('models')!=[model] or group.scope.get('forms')!=[ref['opcode']]:raise ValueError('Wrong related instruction scope')
            graph[group.id]=set(group.depends_on)
        if any(deps-set(graph) for deps in graph.values()):raise ValueError('Missing related instruction dependency')
        while graph:
            ready={identity for identity,deps in graph.items() if not deps}
            if not ready:raise ValueError('Cyclic related instruction dependency')
            graph={identity:deps-ready for identity,deps in graph.items() if identity not in ready}
        related.append({'opcode':entry['opcode'],'parameters':copy.deepcopy(ref['parameters']),'facts':entry['facts'],
                        'gaps':entry.get('gaps',[]),'call_available':entry.get('call_available',False)})
    value['related_instructions']=related
    return {'kind':'language','target':target,'scope':{'models':[model]},'content_origin':'authored_fact','value':value}


def write_runtime_sqlite(payload, path, *, field_pooling=False, search_pooling=False):
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
        connection.executescript(_SCHEMA_SQL)
        meta = {'corpus_kind': CORPUS_KIND, 'runtime_serialization': 'owner-field-pool-v2' if field_pooling else 'owner-source-pool-v1',
                'corpus_scope': 'provided_instruction_definitions_only',
                'fact_bundle_version': str(payload.get('fact_bundle_version', '')),
                'owner_store_serialization': 'instruction-owner-store-v1',
                'definition_metadata': _json({k: v for k, v in payload.items() if k not in {'entries', 'source_catalog', 'value_catalog', 'language_facts'}})}
        if field_pooling:
            from knowledge.fact_field_codec import pack_fact_fields, FIELD_SERIALIZATION
            meta['field_serialization'] = FIELD_SERIALIZATION
        connection.executemany('INSERT INTO meta VALUES (?,?)', meta.items())
        if search_pooling:create_search_pool(connection)
        field_ids = {}
        def intern_field(field, value):
            encoded = _json({'field':field,'value':value})
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            if digest not in field_ids:
                cursor = connection.execute('INSERT INTO runtime_fact_fields(payload_json,sha256) VALUES (?,?)',(encoded,digest))
                field_ids[digest] = cursor.lastrowid
            elif connection.execute('SELECT payload_json FROM runtime_fact_fields WHERE id=?',(field_ids[digest],)).fetchone()[0] != encoded:
                raise ValueError('Exact fact field hash collision')
            return field_ids[digest]
        connection.executemany('INSERT INTO runtime_sources VALUES (?,?)',
            ((key, _json(value)) for key, value in payload['source_catalog'].items()))
        connection.executemany('INSERT INTO runtime_values VALUES (?,?)',
            ((key, _json(value)) for key, value in payload.get('value_catalog', {}).items()))
        for feature in payload.get('language_facts', []):
            _write_language_feature(connection, feature)
        count = 0
        for entry in payload['entries']:
            owner = owner_key(entry)
            identity = 'definition:' + hashlib.sha256(owner.encode()).hexdigest()
            model, opcode = entry['target_model'], entry['opcode']
            packet = {'kind': 'instruction', 'target': opcode, 'scope': {'models': [model]},
                      'content_origin': 'authored_fact', 'value': ({**entry, 'facts':[pack_fact_fields(fact,intern_field) for fact in entry.get('facts',[])]} if field_pooling else entry)}
            metadata = {'runtime_owner': owner, 'content_origin': 'authored_fact',
                        'distribution_status': PUBLICATION_STATUS, 'fact_kind': 'instruction',
                        'fact_target': opcode, 'source_lane': 'fact', 'plc_models': [model]}
            encoded = _json(packet)
            connection.execute('INSERT INTO runtime_definitions VALUES (?,?)', (owner, encoded))
            connection.execute('INSERT INTO runtime_owner_index VALUES (?,?,?,?,?)',
                (entry.get('vendor', 'mitsubishi').lower(), opcode.upper(), model.upper(), owner, hashlib.sha256(encoded.encode()).hexdigest()))
            # Index technical values, not serialization hashes, identity wrappers
            # or dependency-reference text. Complete facts remain owner-bound.
            import re
            tokens = set()
            def technical(value):
                tokens.update(re.findall(r'[\w.$:+/-]+', _json(value)))
            technical({key:entry[key] for key in ('vendor','opcode','target_model') if key in entry})
            for fact in entry.get('facts', []):
                technical({key:fact[key] for key in ('dimension','scope','conditions','members') if key in fact})
                technical(payload['value_catalog'][fact['value_ref']] if 'value_ref' in fact else fact.get('value',{}))
            search = ' '.join(sorted(tokens))
            insert_chunk(connection,
                (identity, search, _json(metadata), _json([model]), opcode, 'instruction',
                 'authored_fact', PUBLICATION_STATUS))
            connection.execute('INSERT INTO entity_index VALUES (?,?)', (opcode, identity))
            connection.execute('INSERT INTO facts VALUES (?,?,?,?,?,?)',
                (identity, 'instruction', opcode, model, identity, '{}'))
            count += len(entry.get('facts', []))
        _check_language_reference_owners(connection)
        connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
        connection.commit()
        return {'owners': len(payload['entries']), 'stored_facts': count,
                'chunk_fact_copies': 0, 'sources': len(payload['source_catalog']),
                'values': len(payload.get('value_catalog', {})), 'exact_field_objects':len(field_ids),
                'fact_field_serialization': meta.get('field_serialization'),
                'sqlite_bytes': path.stat().st_size, 'scope': meta['corpus_scope']}
    finally:
        connection.close()


def write_owner_manifest(payload, database_path, manifest_path, *, owner_count=None):
    """Bind a random-access store to the existing same-directory retrieval DB."""
    from plc.instruction_owner_store import SERIALIZATION, _digest
    database_path, manifest_path = Path(database_path).resolve(), Path(manifest_path).resolve()
    if database_path.parent != manifest_path.parent:
        raise ValueError('Manifest and owner database must be siblings')
    version = payload.get('fact_bundle_version')
    if not isinstance(version, str) or not version:
        raise ValueError('Owner store requires an explicit bundle version')
    manifest = {'schema_version': 1, 'serialization': SERIALIZATION,
                'method': 'instruction-material-compiler-v1', 'corpus_kind': 'authored_plc_facts_v1',
                'fact_bundle_version': version, 'owners': len(payload['entries']) if owner_count is None else owner_count,
                'database': {'filename': database_path.name, 'bytes': database_path.stat().st_size,
                             'sha256': _digest(database_path)}}
    with manifest_path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)+'\n')
    return manifest


class OwnerStoreWriter:
    """Incremental, disk-deduplicated writer for reviewed expanded owners.

    No corpus-wide owner/fact/catalog collection is retained in Python. The
    caller controls bounded input iteration; this writer retains one owner.
    It performs exact reversible transport, not semantic regrouping.
    """
    def __init__(self, path, metadata, *, field_pooling=True, search_pooling=False, fact_rows=False):
        from knowledge.fact_corpus import CORPUS_KIND
        from knowledge.fact_field_codec import FIELD_SERIALIZATION
        import copy
        if (not isinstance(metadata, dict) or type(metadata.get('schema_version')) is not int or metadata.get('schema_version') != 1
                or metadata.get('method') != 'instruction-material-compiler-v1'
                or metadata.get('corpus_kind') != CORPUS_KIND or metadata.get('documents')
                or not isinstance(metadata.get('fact_bundle_version'), str)
                or not metadata['fact_bundle_version']):
            raise ValueError('Incremental writer needs reviewed expanded-definition metadata')
        if any(key in metadata for key in ('entries','definitions','source_catalog','value_catalog')):
            raise ValueError('Incremental writer does not accept embedded owner/catalog collections')
        self.path = Path(path)
        self.metadata = copy.deepcopy(metadata)
        self.metadata.pop('language_facts',None)
        self.field_pooling = bool(field_pooling)
        self.fact_rows = bool(fact_rows)
        if self.fact_rows and not self.field_pooling:raise ValueError('Fact rows require exact field pooling')
        with self.path.open('xb'):
            pass
        self._db = sqlite3.connect(self.path)
        self._db.execute('PRAGMA cache_size=-4096')
        self._db.execute('PRAGMA temp_store=FILE')
        self._db.execute('PRAGMA temp.cache_size=-2048')
        self._db.executescript(_SCHEMA_SQL)
        self._closed = False
        self._finalized = False
        self._owners = self._facts = 0
        meta = {'corpus_kind':CORPUS_KIND,'runtime_serialization':'owner-field-pool-v2' if self.field_pooling else 'owner-source-pool-v1',
                'corpus_scope':'provided_instruction_definitions_only','fact_bundle_version':metadata['fact_bundle_version'],
                'owner_store_serialization':'instruction-owner-store-v1','definition_metadata':_json(self.metadata)}
        if self.field_pooling:meta['field_serialization'] = FIELD_SERIALIZATION
        if self.fact_rows:
            from knowledge.fact_field_codec import FACT_ROW_SERIALIZATION
            meta['runtime_serialization']='owner-field-row-v3'
            meta['fact_row_serialization']=FACT_ROW_SERIALIZATION
            self._db.execute('CREATE TEMP TABLE seen_fact_ids(id TEXT PRIMARY KEY) WITHOUT ROWID')
        self._db.executemany('INSERT INTO meta VALUES(?,?)',meta.items())
        if search_pooling:create_search_pool(self._db)

    def _intern(self, table, value):
        encoded = _json(value)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        if table == 'runtime_fact_fields':
            row = self._db.execute('SELECT id,payload_json FROM runtime_fact_fields WHERE sha256=?',(digest,)).fetchone()
            if row:
                if row[1] != encoded:raise ValueError('Exact field hash collision')
                return row[0]
            return self._db.execute('INSERT INTO runtime_fact_fields(payload_json,sha256) VALUES (?,?)',(encoded,digest)).lastrowid
        if table not in {'runtime_sources','runtime_values'}:raise ValueError('Invalid pool')
        row = self._db.execute('SELECT payload_json FROM '+table+' WHERE id=?',(digest,)).fetchone()
        if row:
            if row[0] != encoded:raise ValueError('Exact content hash collision')
        else:self._db.execute('INSERT INTO '+table+' VALUES (?,?)',(digest,encoded))
        return digest

    def _intern_field(self, field, value):
        return self._intern('runtime_fact_fields',{'field':field,'value':value})

    @staticmethod
    def _public(value):
        if isinstance(value, dict):
            if set(value)&{'bbox','raw_text','manual_text','layout_text','geometry_reference','source_span'}:
                raise ValueError('Private original evidence cannot enter runtime transport')
            for child in value.values():OwnerStoreWriter._public(child)
        elif isinstance(value,list):
            for child in value:OwnerStoreWriter._public(child)

    def add_entry(self, entry):
        from knowledge.fact_corpus import PUBLICATION_STATUS
        from knowledge.fact_field_codec import pack_fact_fields
        from plc.instruction_definition import InstructionFactGroup
        import re
        if self._closed or self._finalized:raise ValueError('Writer is closed')
        self._public(entry)
        if not isinstance(entry,dict) or any(not isinstance(entry.get(key),str) or not entry[key] for key in ('vendor','opcode','target_model')):
            raise ValueError('Literal owner identity required')
        facts = entry.get('facts',[])
        if not isinstance(facts,list):raise ValueError('Invalid owner facts')
        for key in ('source_materials','uninterpreted_content'):
            items=entry.get(key,[])
            if not isinstance(items,list) or any(not isinstance(item,dict) for item in items):raise ValueError('Invalid source receipt')
        ids=set();graph={}
        for fact in facts:
            group=InstructionFactGroup.from_mapping(fact)
            if group.id in ids or group.scope.get('models') != [entry['target_model']] or group.scope.get('forms') != [entry['opcode']]:
                raise ValueError('Invalid owner fact identity/scope')
            ids.add(group.id);graph[group.id]=set(group.depends_on)
        if any(deps-ids for deps in graph.values()):raise ValueError('Missing owner dependency')
        while graph:
            ready={identity for identity,deps in graph.items() if not deps}
            if not ready:raise ValueError('Cyclic owner dependencies')
            graph={identity:deps-ready for identity,deps in graph.items() if identity not in ready}
        tokens=set()
        def technical(value):tokens.update(re.findall(r'[\w.$:+/-]+',_json(value)))
        technical({key:entry[key] for key in ('vendor','opcode','target_model')})
        packed=[]
        for fact in facts:
            if 'source_refs' in fact or 'value_ref' in fact or 'field_refs' in fact:raise ValueError('Writer requires expanded facts')
            technical({key:fact[key] for key in ('dimension','scope','conditions','members') if key in fact})
            technical(fact.get('value',{}))
            value={key:item for key,item in fact.items() if key not in {'sources','value'}}
            value['source_refs']=[self._intern('runtime_sources',source) for source in fact.get('sources',[])]
            if 'value' in fact:value['value_ref']=self._intern('runtime_values',fact['value'])
            if self.fact_rows:
                from knowledge.fact_field_codec import pack_fact_row, POOLED_FIELDS
                first=self._db.execute('INSERT OR IGNORE INTO seen_fact_ids VALUES (?)',(value['id'],)).rowcount
                fields=POOLED_FIELDS if first else POOLED_FIELDS|{'id'}
                packed.append(pack_fact_row(value,self._intern_field,fields=fields))
            else:
                packed.append(pack_fact_fields(value,self._intern_field) if self.field_pooling else value)
        owner=owner_key(entry);model,opcode=entry['target_model'],entry['opcode']
        identity='definition:'+hashlib.sha256(owner.encode()).hexdigest()
        packet={'kind':'instruction','target':opcode,'scope':{'models':[model]},'content_origin':'authored_fact','value':{**entry,'facts':packed}}
        encoded=_json(packet)
        self._db.execute('INSERT INTO runtime_definitions VALUES (?,?)',(owner,encoded))
        self._db.execute('INSERT INTO runtime_owner_index VALUES (?,?,?,?,?)',(entry['vendor'].lower(),opcode.upper(),model.upper(),owner,hashlib.sha256(encoded.encode()).hexdigest()))
        metadata={'runtime_owner':owner,'content_origin':'authored_fact','distribution_status':PUBLICATION_STATUS,'fact_kind':'instruction','fact_target':opcode,'source_lane':'fact','plc_models':[model]}
        insert_chunk(self._db,(identity,' '.join(sorted(tokens)),_json(metadata),_json([model]),opcode,'instruction','authored_fact',PUBLICATION_STATUS))
        self._db.execute('INSERT INTO entity_index VALUES (?,?)',(opcode,identity))
        self._db.execute('INSERT INTO facts VALUES (?,?,?,?,?,?)',(identity,'instruction',opcode,model,identity,'{}'))
        self._owners+=1;self._facts+=len(facts)

    def add_language_feature(self, feature):
        if self._closed or self._finalized:raise ValueError('Writer is closed')
        self._public(feature)
        validate_language_feature(feature, published=True)
        _write_language_feature(self._db, feature)

    def finalize(self, manifest_path):
        if self._closed or self._finalized:raise ValueError('Writer is closed')
        _check_language_reference_owners(self._db)
        self._db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
        counts={name:self._db.execute('SELECT count(*) FROM '+table).fetchone()[0] for name,table in [('sources','runtime_sources'),('values','runtime_values'),('exact_field_objects','runtime_fact_fields')]}
        self._db.commit();self._finalized=True
        self.close()
        manifest=write_owner_manifest(self.metadata,self.path,manifest_path,owner_count=self._owners)
        return {'manifest':manifest,'sqlite':{'owners':self._owners,'stored_facts':self._facts,'chunk_fact_copies':0,**counts,'sqlite_bytes':self.path.stat().st_size},'semantic_projection_performed':False,'release_accepted':False}

    def close(self):
        if not self._closed:
            self._db.close();self._closed=True
