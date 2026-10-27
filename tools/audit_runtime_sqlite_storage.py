# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Read-only physical SQLite attribution, not expanded JSON or runtime loading.

No owner hydration, rebuild, extension loading, or original data is emitted.
Only this diagnostic's new JSON output file is written.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3
import time

TABLE_COLUMNS = {
    'runtime_definitions': ('payload_json',),
    'runtime_owner_index': ('owner', 'packet_sha256'),
    'runtime_sources': ('payload_json',),
    'runtime_values': ('payload_json',),
    'runtime_fact_fields': ('payload_json',),
    'runtime_language_features': ('payload_json',),
    'chunks': ('text', 'metadata_json'),
    'runtime_search_documents': ('text','sha256'),
    'runtime_chunk_bindings': ('metadata_json',),
    'facts': ('payload_json',),
    'entity_index': (),
    'meta': ('value',),
}
FTS_COLUMNS = {'chunks_fts_data': ('block',), 'chunks_fts_idx': ('term',),
               'chunks_fts_docsize': ('sz',), 'chunks_fts_config': ('v',)}
FTS_TABLES = set(FTS_COLUMNS)
FIELDS = ('scope','conditions','depends_on','source_refs','value_ref','dimension','status','verification','id')


def _identity(path):
    stat=path.stat()
    return (stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)


def _sealed(path):
    if any(Path(str(path)+suffix).exists() for suffix in ('-wal','-journal','-shm')):
        raise ValueError('Use a sealed candidate database without WAL/journal sidecars')


def _physical_stats(connection):
    # Basic dbstat works without the newer aggregate hidden column.
    return connection.execute("SELECT name,count(*),sum(pgsize),sum(payload),sum(unused),"
        "sum(CASE WHEN pagetype='overflow' THEN 1 ELSE 0 END) FROM dbstat GROUP BY name").fetchall()


def _field_stats(connection):
    # Classify untrusted field names inside SQLite; never expose unknown names.
    placeholders=','.join('?' for _ in FIELDS)
    return connection.execute(
        "SELECT CASE WHEN json_valid(payload_json) THEN CASE WHEN json_extract(payload_json,'$.field') IN ("
        +placeholders+") THEN json_extract(payload_json,'$.field') ELSE 'unknown_field' END ELSE 'invalid_json' END,"
        "count(*),sum(length(CAST(payload_json AS BLOB))),"
        "sum(CASE WHEN json_valid(payload_json) THEN length(CAST(json_quote(json_extract(payload_json,'$.value')) AS BLOB)) ELSE 0 END) "
        "FROM runtime_fact_fields GROUP BY 1",FIELDS).fetchall()


def audit_storage(path):
    started=time.perf_counter()
    original=Path(path)
    if original.is_symlink() or not original.is_file():raise ValueError('Expected a regular candidate SQLite file')
    path=original.resolve();_sealed(path);before=_identity(path)
    connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('PRAGMA cache_size=-4096')
        connection.execute('PRAGMA temp_store=MEMORY')
        connection.execute('BEGIN')
        page_size=connection.execute('PRAGMA page_size').fetchone()[0]
        page_count=connection.execute('PRAGMA page_count').fetchone()[0]
        free_pages=connection.execute('PRAGMA freelist_count').fetchone()[0]
        schema=connection.execute("SELECT name,type,tbl_name FROM sqlite_schema WHERE type IN ('table','index')").fetchall()
        tables={name for name,kind,_ in schema if kind=='table'}
        labels={}
        for name,kind,table in schema:
            if table in TABLE_COLUMNS:label=kind+':'+table
            elif table in FTS_TABLES or name in FTS_TABLES:label='fts:'+table
            else:label='other_'+kind
            labels[name]=label
        labels['sqlite_schema']='sqlite_schema'
        logical={}
        for table,columns in {**TABLE_COLUMNS,**FTS_COLUMNS}.items():
            if table not in tables:continue
            available={row[1] for row in connection.execute('PRAGMA table_info("'+table+'")')}
            selected=[column for column in columns if column in available]
            expressions=['count(*)']+['coalesce(sum(length(CAST("'+column+'" AS BLOB))),0)' for column in selected]
            row=connection.execute('SELECT '+','.join(expressions)+' FROM "'+table+'"').fetchone()
            logical[table]={'rows':row[0],'selected_column_utf8_bytes':dict(zip(selected,row[1:])),
                            'missing_expected_columns':[column for column in columns if column not in available]}
        physical={};physical_available=True
        try:
            rows=_physical_stats(connection)
        except sqlite3.OperationalError as exc:
            # Only a genuinely unavailable dbstat facility is a valid fallback.
            if 'no such table: dbstat' not in str(exc).lower() and 'no such module: dbstat' not in str(exc).lower():raise
            physical_available=False;rows=[]
        for name,pages,allocated,payload,unused,overflow in rows:
            label=labels.get(name,'other_object')
            item=physical.setdefault(label,dict(pages=0,allocated_bytes=0,cell_payload_bytes=0,unused_bytes=0,overflow_pages=0))
            for key,value in zip(item,(pages,allocated,payload,unused,overflow)):item[key]+=value or 0
        field_distribution=[];field_available=False
        if 'runtime_fact_fields' in logical and not logical['runtime_fact_fields']['missing_expected_columns']:
            try:
                for field,count,payload,value_bytes in _field_stats(connection):
                    field_distribution.append({'field':field,'rows':count,'payload_json_utf8_bytes':payload or 0,'reserialized_value_utf8_bytes':value_bytes or 0})
                field_available=True
            except sqlite3.OperationalError as exc:
                if 'no such function: json_' not in str(exc).lower():raise
        field_total=sum(row['payload_json_utf8_bytes'] for row in field_distribution)
        for row in field_distribution:
            row['share_of_field_pool_json_bytes']=row['payload_json_utf8_bytes']/field_total if field_total else 0.0
        connection.rollback()
        _sealed(path)
        if before!=_identity(path):raise ValueError('Database changed during read-only audit; discard the result')
        tracked=sum(item['allocated_bytes'] for item in physical.values())
        result={'audit_schema':1,'measurement_stage':'stored_sqlite_physical_not_expanded_json',
            'database_file_bytes':before[2],'sqlite_page_size':page_size,'sqlite_page_count':page_count,
            'sqlite_page_bytes':page_size*page_count,'freelist_pages':free_pages,'freelist_bytes':free_pages*page_size,
            'dbstat_available':physical_available,'physical_objects':physical,
            'dbstat_attributed_bytes':tracked if physical_available else None,
            'unattributed_nonfreelist_page_bytes':page_count*page_size-free_pages*page_size-tracked if physical_available else None,
            'file_bytes_minus_sqlite_page_bytes':before[2]-page_size*page_count,
            'logical_columns':logical,'field_pool_json_functions_available':field_available,
            'field_pool_distribution':field_distribution,'elapsed_seconds':time.perf_counter()-started,
            'input_unchanged':True,'read_only':True,
            'limitations':['Physical B-tree/index/FTS allocation is not expanded owner JSON size or process RSS.',
                'Cell payload includes SQLite record encoding; unused bytes exclude headers/pointers and freelist pages.',
                'Selected logical column bytes are UTF-8/BLOB lengths, not physical allocation or complete table row sizes.',
                'Field value bytes use SQLite JSON reserialization and are not exact source byte spans.',
                'No dbstat means per-object physical attribution is unavailable; logical sizes are not substituted.',
                'This scans stored cells/shared field JSON only, not hydrated owners or dependency graphs; it is not a runtime attach benchmark.']}
        return result
    finally:
        connection.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.sqlite.resolve()==args.output.resolve() or args.output.exists():parser.error('Use a new separate JSON output path')
    try:
        result=audit_storage(args.sqlite)
        with args.output.open('x',encoding='utf-8') as stream:
            json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
    except (OSError,ValueError,sqlite3.Error):
        parser.exit(2,'Storage audit failed; input must be a readable, sealed, unchanged SQLite snapshot. No database changes were requested.\n')
    print(json.dumps({'status':'completed','measurement_stage':result['measurement_stage'],'dbstat_available':result['dbstat_available']}))

if __name__=='__main__':main()
