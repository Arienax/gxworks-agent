# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Migrate a sealed v2 owner store to a NEW v3 candidate; never enable it."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tools')]
from plc.instruction_owner_store import OwnerDefinitionStore,MANIFEST_NAME,_identity,_digest
from knowledge.runtime_fact_publication import _json,write_owner_manifest
from plc.instruction_definition import InstructionFactGroup
from knowledge.fact_field_codec import unpack_fact_fields,pack_fact_row,unpack_fact_row,POOLED_FIELDS,FACT_ROW_SERIALIZATION
from knowledge.exact_search_storage import create_search_pool,insert_chunk,COLUMNS,validate_search_schema
from audit_runtime_sqlite_storage import audit_storage


def _no_links(path):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('Symlink paths are not accepted')


def _sealed(path):
    if any(Path(str(path)+s).exists() for s in ('-wal','-shm','-journal')):raise ValueError('Source must be a sealed inactive snapshot')


def _write(path,value):
    with Path(path).open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')


def migrate(source_manifest,output_dir):
    start=time.monotonic();source_manifest=Path(source_manifest);output=Path(output_dir)
    _no_links(source_manifest);_no_links(output)
    source_manifest=source_manifest.resolve();output=output.resolve()
    if output.exists() or output.is_relative_to(source_manifest.parent) or source_manifest.parent.is_relative_to(output):
        raise ValueError('Use a fresh, separate, non-nested output directory')
    if not output.parent.is_dir():raise ValueError('Output parent must exist')
    # Conservative planning allowance, not a guarantee against all filesystem limits.
    source=OwnerDefinitionStore(source_manifest)
    dbpath=source.database_path
    try:
        _sealed(dbpath)
        if shutil.disk_usage(output.parent).free < dbpath.stat().st_size*3+64*1024*1024:
            raise ValueError('Insufficient free space for backup, rewrite and VACUUM planning allowance')
    except Exception:
        source.close();raise
    initial=(_identity(source_manifest),_identity(dbpath));source_digest=_digest(dbpath)
    conn=None;ro=None;phase='source_validation';owners=facts=chunks=0
    try:
        ro=sqlite3.connect(dbpath.as_uri()+'?mode=ro&immutable=1',uri=True)
        ro.execute('PRAGMA query_only=ON');ro.execute('PRAGMA trusted_schema=OFF')
        meta=dict(ro.execute('SELECT key,value FROM meta'))
        if meta.get('runtime_serialization')!='owner-field-pool-v2' or meta.get('search_serialization') is not None:
            raise ValueError('Only legacy-search v2 owner stores are migration inputs')
        if ro.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' LIMIT 1").fetchone():raise ValueError('Unexpected database triggers')
        validate_search_schema(ro)
        if ro.execute('PRAGMA quick_check').fetchone()!=('ok',):raise ValueError('Source SQLite integrity check failed')
        # Each immutable catalog row is checked once; references are checked below
        # by indexed existence queries, not a corpus-sized in-memory catalog.
        for table in ('runtime_sources','runtime_values'):
            for identity,text in ro.execute('SELECT id,payload_json FROM '+table):
                value=json.loads(text)
                if not isinstance(value,dict) or hashlib.sha256(_json(value).encode()).hexdigest()!=identity:
                    raise ValueError('Invalid content-addressed source/value catalog')
        output.mkdir();database=output/'instruction_owners.sqlite';phase='backup'
        conn=sqlite3.connect(database);ro.backup(conn)
        conn.execute('PRAGMA trusted_schema=OFF');conn.execute('PRAGMA temp_store=FILE')
        conn.execute('PRAGMA cache_size=-4096');conn.execute('PRAGMA temp.cache_size=-2048')
        conn.execute('CREATE TEMP TABLE migration_seen_ids(id TEXT PRIMARY KEY)')
        phase='owner_packets'
        field_cache={}  # bounded to fields referenced by one owner packet
        def resolve(identity):
            if identity in field_cache:return field_cache[identity]
            row=conn.execute('SELECT payload_json,sha256 FROM runtime_fact_fields WHERE id=?',(identity,)).fetchone()
            if row is None or hashlib.sha256(row[0].encode()).hexdigest()!=row[1]:raise ValueError('Invalid shared field')
            field_cache[identity]=json.loads(row[0]);return field_cache[identity]
        def intern(field,value):
            text=_json({'field':field,'value':value});digest=hashlib.sha256(text.encode()).hexdigest()
            row=conn.execute('SELECT id,payload_json FROM runtime_fact_fields WHERE sha256=?',(digest,)).fetchone()
            if row:
                if row[1]!=text:raise ValueError('Shared field digest collision')
                return row[0]
            return conn.execute('INSERT INTO runtime_fact_fields(payload_json,sha256) VALUES (?,?)',(text,digest)).lastrowid
        for owner,encoded,expected in ro.execute('SELECT d.owner,d.payload_json,i.packet_sha256 FROM runtime_definitions d LEFT JOIN runtime_owner_index i ON d.owner=i.owner ORDER BY d.owner'):
            if hashlib.sha256(encoded.encode()).hexdigest()!=expected:raise ValueError('Invalid owner packet digest')
            field_cache.clear()
            packet=json.loads(encoded);packed=[];checked_refs=set();graph={}
            if not isinstance(packet,dict) or not isinstance(packet.get('value'),dict):raise ValueError('Invalid owner packet shape')
            entry=packet['value']
            if not isinstance(entry.get('facts'),list):raise ValueError('Owner facts must be a list')
            identity=ro.execute('SELECT vendor,opcode,target_model FROM runtime_owner_index WHERE owner=?',(owner,)).fetchone()
            if identity!=(str(entry.get('vendor','mitsubishi')).lower(),str(entry.get('opcode')).upper(),str(entry.get('target_model')).upper()):
                raise ValueError('Owner identity mismatch')
            for key in ('source_materials','uninterpreted_content'):
                items=entry.get(key,[])
                if not isinstance(items,list) or any(not isinstance(item,dict) for item in items):raise ValueError('Invalid owner source receipt')
            def check_reference(table,identity):
                if not isinstance(identity,str):raise ValueError('Invalid source/value reference')
                key=(table,identity)
                if key in checked_refs:return
                if conn.execute('SELECT 1 FROM '+table+' WHERE id=?',(identity,)).fetchone() is None:
                    raise ValueError('Missing source/value pool row')
                checked_refs.add(key)
            for old in packet['value']['facts']:
                fact=unpack_fact_fields(old,resolve)
                if not isinstance(fact.get('id'),str):raise ValueError('Invalid original fact identity')
                refs=fact.get('source_refs')
                if not isinstance(refs,list) or 'sources' in fact or ('value_ref' in fact and 'value' in fact):raise ValueError('Invalid source/value references')
                for ref in refs:check_reference('runtime_sources',ref)
                if 'value_ref' in fact:check_reference('runtime_values',fact['value_ref'])
                # Catalog contents were independently checked as exact JSON objects.
                # The common fact validator only tests their Mapping shape/count;
                # use empty shape witnesses to avoid expanding technical bodies.
                shape={k:v for k,v in fact.items() if k not in {'source_refs','value_ref'}}
                shape['sources']=[{} for _ in refs]
                if 'value_ref' in fact:shape['value']={}
                group=InstructionFactGroup.from_mapping(shape)
                if group.scope.get('forms')!=[entry['opcode']] or group.scope.get('models')!=[entry['target_model']]:
                    raise ValueError('Invalid exact owner fact scope')
                deps=fact.get('depends_on',[])
                if not isinstance(deps,list) or any(not isinstance(d,str) for d in deps) or fact['id'] in graph:
                    raise ValueError('Invalid owner fact dependencies')
                graph[fact['id']]=set(deps)
                first=conn.execute('INSERT OR IGNORE INTO migration_seen_ids VALUES (?)',(fact['id'],)).rowcount
                row=pack_fact_row(fact,intern,fields=POOLED_FIELDS if first else POOLED_FIELDS|{'id'})
                if _json(unpack_fact_row(row,resolve))!=_json(fact):raise ValueError('Fact transport roundtrip mismatch')
                packed.append(row);facts+=1
            if any(deps-graph.keys() for deps in graph.values()):raise ValueError('Missing owner dependency')
            while graph:
                ready={key for key,deps in graph.items() if not deps}
                if not ready:raise ValueError('Cyclic owner dependencies')
                graph={key:deps-ready for key,deps in graph.items() if key not in ready}
            packet['value']['facts']=packed;new=_json(packet)
            conn.execute('UPDATE runtime_definitions SET payload_json=? WHERE owner=?',(new,owner))
            conn.execute('UPDATE runtime_owner_index SET packet_sha256=? WHERE owner=?',(hashlib.sha256(new.encode()).hexdigest(),owner));owners+=1
        if owners!=source.owner_count:raise ValueError('Owner count changed')
        conn.execute('DROP TABLE migration_seen_ids')
        conn.execute("UPDATE meta SET value='owner-field-row-v3' WHERE key='runtime_serialization'")
        conn.execute('INSERT INTO meta VALUES (?,?)',('fact_row_serialization',FACT_ROW_SERIALIZATION))
        phase='search_documents'
        # Read old chunks from the immutable source; retain the copied FTS index,
        # its original rowids and corpus statistics without rebuilding it.
        conn.execute('DELETE FROM chunks');create_search_pool(conn)
        for row in ro.execute('SELECT rowid,'+','.join(COLUMNS)+' FROM chunks ORDER BY rowid'):
            insert_chunk(conn,row[1:],rowid=row[0]);chunks+=1
        validate_search_schema(conn)
        for old,new in zip(ro.execute('SELECT rowid,'+','.join(COLUMNS)+' FROM chunks ORDER BY rowid'),conn.execute('SELECT rowid,'+','.join(COLUMNS)+' FROM chunks ORDER BY rowid')):
            if old!=new:raise ValueError('Search row equivalence mismatch')
        if conn.execute('SELECT count(*) FROM chunks').fetchone()[0]!=chunks:raise ValueError('Search row count changed')
        conn.commit();phase='vacuum';conn.execute('VACUUM')
        if conn.execute('PRAGMA quick_check').fetchone()!=('ok',):raise ValueError('Candidate SQLite integrity check failed')
        conn.close();conn=None;phase='physical_audit'
        audit=audit_storage(database);_write(output/'owner-store-storage-audit.json',audit)
        _sealed(dbpath)
        if initial!=(_identity(source_manifest),_identity(dbpath)) or _digest(dbpath)!=source_digest or not source.current():
            raise ValueError('Source changed during migration')
        payload=json.loads(meta['definition_metadata']);phase='candidate_validation'
        pending=output/'instruction_definitions.manifest.pending.json'
        manifest=write_owner_manifest(payload,database,pending,owner_count=owners)
        check=OwnerDefinitionStore(pending)
        try:check.language_features()
        finally:check.close()
        receipt={'migration_schema':1,'release_accepted':False,'original_input_modified':False,
            'source_database_sha256':source_digest,'source_database_bytes':initial[1][2],
            'output_database_sha256':manifest['database']['sha256'],'output_database_bytes':database.stat().st_size,
            'owners':owners,'fact_instances':facts,'chunks':chunks,'fact_transport_roundtrip_verified':True,
            'search_rows_exactly_equal':True,'source_value_pool_ids_preserved':True,
            'semantic_projection_performed':False,'facts_removed':0,'status_promotions':0,
            'vacuum_performed':True,'fts_rebuilt':False,'elapsed_seconds':time.monotonic()-start,
            'changes':['exact fact row transport','pool repeated fact IDs after their first occurrence','exact search text pooling','VACUUM'],
            'limitations':['Total size difference includes VACUUM; it is not solely structural sharing.',
                'Packed-format roundtrip and exact search rows are not full runtime or native PLC validation.',
                'Free-space allowance is a planning estimate, not a maximum disk-use guarantee.']}
        _write(output/'owner-store-migration-receipt.json',receipt)
        # No conventional final manifest is present on any earlier failure.
        os.link(pending,output/MANIFEST_NAME)  # atomic no-clobber publication
        # The pending file is retained as an identical candidate receipt.
        return receipt
    except Exception as error:
        if output.exists():
            try:_write(output/'migration-failure.json',{'status':'failed','release_accepted':False,'phase':phase,'owners_processed':owners,'error_type':type(error).__name__,'partial_artifacts_retained':True})
            except OSError:pass
        raise
    finally:
        if conn is not None:conn.close()
        if ro is not None:ro.close()
        source.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-manifest',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    try:result=migrate(args.source_manifest,args.output_dir)
    except (OSError,ValueError,sqlite3.Error,KeyError,TypeError) as error:parser.exit(2,'Migration failed ('+type(error).__name__+'); original input retained. Inspect the new candidate failure receipt; do not enable partial outputs.\n')
    print(json.dumps({'status':'completed','release_accepted':False,'owners':result['owners'],'output_database_bytes':result['output_database_bytes']}))
if __name__=='__main__':main()
