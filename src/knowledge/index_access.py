"""Read-only index access and per-thread cache lifecycle.

No connection or filesystem probing occurs at import time. Each caller thread
owns its SQLite connection; replacing the index closes the old connection and
invalidates schema and derived query caches together.
"""
from __future__ import annotations

from pathlib import Path
from contextlib import closing
import sqlite3
import threading
from urllib.parse import quote

from shared.paths import resource_path

INDEX_RESOURCE = "knowledge/runtime/plc_facts.sqlite"
_thread_state = threading.local()

CHUNK_ID_COLUMNS = ("chunk_id", "chunk_key", "id")

TEXT_COLUMNS = ("text", "content", "content_text", "chunk_text", "body")

SOURCE_COLUMNS = (
    "source",
    "source_name",
    "manual_title",
    "manual_id",
    "document",
    "document_title",
    "document_id",
    "title",
)

PAGE_COLUMNS = (
    "printed_page",
    "page",
    "page_number",
    "manual_page",
    "page_start",
    "start_page",
    "source_page",
)

PAGE_END_COLUMNS = ("page_end", "end_page")

PDF_PAGE_COLUMNS = ("pdf_page", "pdf_page_number", "page_pdf")

SECTION_COLUMNS = (
    "outline_path",
    "section_path",
    "section",
    "section_title",
    "heading",
    "chapter",
)

MODEL_COLUMNS = (
    "plc_model",
    "plc_models",
    "model",
    "plc_family",
    "applies_to",
)

TASK_COLUMNS = (
    "task_type",
    "task_types",
    "tasks",
    "workflow",
    "applies_to_task",
)

ENTITY_TERM_COLUMNS = (
    "entity_norm",
    "normalized_entity",
    "normalized_alias",
    "normalized",
    "entity",
    "entity_value",
    "canonical",
    "canonical_key",
    "term",
    "alias",
    "alias_norm",
    "opcode",
    "device",
    "name",
    "key",
)

ENTITY_CHUNK_COLUMNS = ("chunk_id", "target_chunk_id", "content_id")

ENTITY_OCCURRENCE_COLUMNS = (
    "occurrences",
    "occurrence_count",
    "frequency",
    "count",
    "weight",
)


def quote_identifier(value):
    return '"' + str(value).replace('"', '""') + '"'


def first_column(columns, candidates):
    lookup = {str(column).casefold(): str(column) for column in columns}
    for candidate in candidates:
        matched = lookup.get(candidate.casefold())
        if matched:
            return matched
    return None


def matching_columns(columns, candidates):
    lookup = {str(column).casefold(): str(column) for column in columns}
    return [lookup[item.casefold()] for item in candidates if item.casefold() in lookup]


def row_value(row, columns, default=None):
    keys = {str(key).casefold(): key for key in row.keys()}
    for column in columns:
        key = keys.get(column.casefold())
        if key is None:
            continue
        value = row[key]
        if value is not None and str(value).strip():
            return value
    return default


def index_path():
    try:
        path = Path(resource_path(INDEX_RESOURCE))
    except (OSError, TypeError, ValueError):
        return None
    if not path.is_file():
        return None
    # Renaming/copying the historical full-text corpus must not re-enable it.
    from knowledge.fact_corpus import CORPUS_KIND
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as check:
            row = check.execute("SELECT value FROM meta WHERE key='corpus_kind'").fetchone()
        return path if row and row[0] == CORPUS_KIND else None
    except sqlite3.Error:
        return None


def index_identity(path):
    if path is None:
        return ("missing", 0, 0)
    try:
        stat = path.stat()
    except OSError:
        return ("missing", 0, 0)
    return (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))


def close_thread_connection():
    connection = getattr(_thread_state, "connection", None)
    if connection is not None:
        try:
            connection.close()
        except sqlite3.Error:
            pass
    _thread_state.connection = None
    _thread_state.identity = None
    _thread_state.schema = None
    _thread_state.derived = {}
    _thread_state.schema_connection = None


def connection(path, identity):
    if (
        getattr(_thread_state, "connection", None) is not None
        and getattr(_thread_state, "identity", None) == identity
    ):
        return _thread_state.connection

    close_thread_connection()
    encoded_path = quote(path.resolve().as_posix(), safe="/:")
    connection = sqlite3.connect(
        "file:{}?mode=ro&immutable=1".format(encoded_path),
        uri=True,
        timeout=0.2,
        check_same_thread=True,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA temp_store=MEMORY")
        try:
            connection.execute("PRAGMA mmap_size=268435456")
        except sqlite3.Error:
            pass
    except sqlite3.Error:
        connection.close()
        raise

    _thread_state.connection = connection
    _thread_state.identity = identity
    _thread_state.schema = None
    _thread_state.derived = {}
    _thread_state.schema_connection = None
    return connection


def table_columns(connection, table_name):
    rows = connection.execute(
        "PRAGMA table_info({})".format(quote_identifier(table_name))
    ).fetchall()
    return tuple(str(row[1]) for row in rows)


def schema(connection):
    cached = getattr(_thread_state, "schema", None)
    if cached is not None and getattr(_thread_state, "schema_connection", None) is connection:
        return cached

    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone():
        from knowledge.exact_search_storage import validate_search_schema
        validate_search_schema(connection)
    table_rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
    ).fetchall()
    table_lookup = {str(row[0]).casefold(): str(row[0]) for row in table_rows}
    schema = {}
    for expected in (
        "meta",
        "manuals",
        "chunks",
        "entity_index",
        "chunks_fts",
        "instructions",
        "instruction_aliases",
        "device_records",
        "device_sections",
        "error_records",
        "debug_cases",
        "vector_embeddings",
        "facts",
    ):
        actual = table_lookup.get(expected.casefold())
        if actual:
            schema[expected] = {
                "name": actual,
                "columns": table_columns(connection, actual),
            }
    if getattr(_thread_state, "connection", None) is connection:
        _thread_state.schema = schema
        _thread_state.schema_connection = connection
    return schema


def load_meta(connection, schema):
    table = schema.get("meta")
    if not table:
        return {}
    columns = table["columns"]
    if not columns:
        return {}
    rows = connection.execute(
        "SELECT * FROM {} LIMIT 128".format(quote_identifier(table["name"]))
    ).fetchall()
    if not rows:
        return {}

    key_column = first_column(columns, ("key", "name", "meta_key"))
    value_column = first_column(columns, ("value", "meta_value", "content"))
    if key_column and value_column:
        return {
            str(row[key_column]).strip().casefold(): row[value_column]
            for row in rows
            if row[key_column] is not None
        }
    return {str(key).casefold(): rows[0][key] for key in rows[0].keys()}


def fetch_chunks(connection, schema, references):
    table = schema.get("chunks")
    if not table or not references:
        return {}
    columns = table["columns"]
    id_column = first_column(columns, CHUNK_ID_COLUMNS)
    by_id = []
    by_rowid = []
    for kind, value in references:
        if kind == "id" and id_column:
            by_id.append(value)
        else:
            by_rowid.append(value)

    found = {}
    table_name = quote_identifier(table["name"])
    if by_id and id_column:
        placeholders = ",".join("?" for _item in by_id)
        sql = "SELECT rowid AS _chunk_rowid, * FROM {} WHERE {} IN ({})".format(
            table_name,
            quote_identifier(id_column),
            placeholders,
        )
        for row in connection.execute(sql, by_id).fetchall():
            found[("id", str(row[id_column]))] = row
    if by_rowid:
        placeholders = ",".join("?" for _item in by_rowid)
        sql = "SELECT rowid AS _chunk_rowid, * FROM {} WHERE rowid IN ({})".format(
            table_name,
            placeholders,
        )
        for row in connection.execute(sql, by_rowid).fetchall():
            found[("rowid", str(row["_chunk_rowid"]))] = row
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone():
        from knowledge.exact_search_storage import validate_search_rows
        validate_search_rows(connection, found.values())
    return found


def cached_value(connection, name):
    """Read derived state only for the connection which owns that state."""
    if getattr(_thread_state, "connection", None) is not connection:
        return None
    return getattr(_thread_state, "derived", {}).get(name)


def cache_value(connection, name, value):
    """Store successful derived reads until this thread changes index identity."""
    if getattr(_thread_state, "connection", None) is connection:
        _thread_state.derived[name] = value
    return value
