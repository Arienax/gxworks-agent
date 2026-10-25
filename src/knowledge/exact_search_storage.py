"""Exact search-document sharing without changing owner-qualified FTS statistics.

The compatibility view preserves chunk rowids and every search/filter field.
FTS deliberately retains one row per chunk: deduplicating its documents would
change BM25 corpus frequencies, ranks and pre-limit model filtering.
"""
from __future__ import annotations
import hashlib

SEARCH_SERIALIZATION = 'exact-search-text-pool-v1'
COLUMNS = ('id','text','metadata_json','plc_models','instruction_opcode',
           'chunk_type','content_origin','distribution_status')


def _marker(connection):
    columns={row[1] for row in connection.execute('PRAGMA table_info(meta)')}
    if not {'key','value'}<=columns:return None
    return connection.execute("SELECT value FROM meta WHERE key='search_serialization'").fetchone()


def create_search_pool(connection):
    """Replace an EMPTY legacy chunks table during new-store construction."""
    if connection.execute('SELECT count(*) FROM chunks').fetchone()[0]:
        raise ValueError('Search pool must be created before writing chunks')
    connection.execute('DROP TABLE chunks')
    connection.execute('CREATE TABLE runtime_search_documents(id INTEGER PRIMARY KEY,sha256 TEXT NOT NULL UNIQUE,text TEXT NOT NULL)')
    connection.execute('''CREATE TABLE runtime_chunk_bindings(
      id TEXT PRIMARY KEY,document_id INTEGER NOT NULL,metadata_json TEXT NOT NULL,
      plc_models TEXT NOT NULL,instruction_opcode TEXT NOT NULL,chunk_type TEXT NOT NULL,
      content_origin TEXT NOT NULL,distribution_status TEXT NOT NULL,
      FOREIGN KEY(document_id) REFERENCES runtime_search_documents(id))''')
    connection.execute('''CREATE VIEW chunks AS SELECT b.rowid AS rowid,b.id,d.text,
      b.metadata_json,b.plc_models,b.instruction_opcode,b.chunk_type,b.content_origin,
      b.distribution_status FROM runtime_chunk_bindings b
      LEFT JOIN runtime_search_documents d ON d.id=b.document_id''')
    connection.execute('INSERT INTO meta(key,value) VALUES (?,?)',('search_serialization',SEARCH_SERIALIZATION))


def insert_chunk(connection, values, *, rowid=None):
    if len(values)!=len(COLUMNS):raise ValueError('Invalid chunk shape')
    marker=_marker(connection)
    if marker is None:
        if rowid is None:
            connection.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)',values)
        else:
            connection.execute('INSERT INTO chunks(rowid,'+','.join(COLUMNS)+') VALUES (?,?,?,?,?,?,?,?,?)',(rowid,*values))
        return
    if marker[0]!=SEARCH_SERIALIZATION:raise ValueError('Unsupported search serialization')
    identity,text,*rest=values
    if not isinstance(text,str):raise ValueError('Search text must be a string')
    digest=hashlib.sha256(text.encode('utf-8')).hexdigest()
    previous=connection.execute('SELECT id,text FROM runtime_search_documents WHERE sha256=?',(digest,)).fetchone()
    if previous:
        if previous[1]!=text:raise ValueError('Exact search document hash collision')
        document_id=previous[0]
    else:
        document_id=connection.execute('INSERT INTO runtime_search_documents(sha256,text) VALUES (?,?)',(digest,text)).lastrowid
    if rowid is None:
        connection.execute('INSERT INTO runtime_chunk_bindings VALUES (?,?,?,?,?,?,?,?)',(identity,document_id,*rest))
    else:
        connection.execute('INSERT INTO runtime_chunk_bindings(rowid,id,document_id,metadata_json,plc_models,instruction_opcode,chunk_type,content_origin,distribution_status) VALUES (?,?,?,?,?,?,?,?,?)',(rowid,identity,document_id,*rest))


def validate_search_schema(connection):
    """Fail closed on unknown transport or structurally missing pool tables."""
    marker=_marker(connection)
    schema={row[0]:row[1] for row in connection.execute("SELECT name,type FROM sqlite_master WHERE name IN ('chunks','runtime_search_documents','runtime_chunk_bindings')")}
    if marker is None:
        if 'runtime_search_documents' in schema or 'runtime_chunk_bindings' in schema:
            raise ValueError('Search pool lacks serialization version')
        return
    if marker[0]!=SEARCH_SERIALIZATION:raise ValueError('Unsupported search serialization')
    if schema!={'chunks':'view','runtime_search_documents':'table','runtime_chunk_bindings':'table'}:
        raise ValueError('Missing shared search schema')

    if connection.execute('SELECT 1 FROM runtime_chunk_bindings b LEFT JOIN runtime_search_documents d ON b.document_id=d.id WHERE d.id IS NULL LIMIT 1').fetchone():
        raise ValueError('Missing shared search document')


def validate_search_rows(connection, rows):
    marker=_marker(connection)
    if marker is None:return
    if marker[0]!=SEARCH_SERIALIZATION:raise ValueError('Unsupported search serialization')
    for row in rows:
        raw=connection.execute('SELECT d.text,d.sha256 FROM runtime_chunk_bindings b LEFT JOIN runtime_search_documents d ON b.document_id=d.id WHERE b.rowid=?',(row['_chunk_rowid'],)).fetchone()
        if raw is None or raw[0] is None or hashlib.sha256(raw[0].encode('utf-8')).hexdigest()!=raw[1]:
            raise ValueError('Invalid shared search document')
