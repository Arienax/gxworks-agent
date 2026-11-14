"""Retrieval reuse preserves scope, per-term limits and owner replacement."""
import sqlite3
from contextlib import closing

from knowledge import core, index_access, scope
from plc.instructions import InstructionRegistry


def test_batched_entities_keep_per_term_limits_and_cpu_scope():
    with sqlite3.connect(':memory:') as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript('''
            CREATE TABLE chunks(id INTEGER PRIMARY KEY, text TEXT, section TEXT,
                manual_type TEXT, plc_models TEXT);
            CREATE TABLE entity_index(entity TEXT, chunk_id INTEGER, occurrences INTEGER);
        ''')
        for term, base in [('MOV', 0), ('BMOV', 1000)]:
            for i in range(150):
                connection.execute('INSERT INTO chunks VALUES(?,?,?,?,?)',
                    (base+i, term, term, 'programming', 'FX3U' if i < 80 else 'Q03UDV'))
                connection.execute('INSERT INTO entity_index VALUES(?,?,?)', (term, base+i, 1000-i))
        rows = core._entity_references(connection, core._schema(connection),
                                      ['MOV', 'BMOV'], 'Q03UDV', 'generate', source_lanes=('fact',))
        assert {row[1] for row in rows if row[2] == 'MOV'} == set(range(80, 144))
        assert {row[1] for row in rows if row[2] == 'BMOV'} == set(range(1080, 1144))
        assert [row[2] for row in rows[:4]] == ['MOV', 'BMOV', 'MOV', 'BMOV']


def test_batched_device_aliases_keep_deduplication():
    with sqlite3.connect(':memory:') as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript('''
            CREATE TABLE chunks(id INTEGER PRIMARY KEY, text TEXT, section TEXT, manual_type TEXT);
            CREATE TABLE entity_index(entity TEXT, chunk_id INTEGER, occurrences INTEGER);
            INSERT INTO chunks VALUES(1,'input','X001','programming');
            INSERT INTO chunks VALUES(2,'input','X01','programming');
            INSERT INTO entity_index VALUES('X1',1,5),('X001',1,5),('X01',2,1);
        ''')
        rows = core._entity_references(connection, core._schema(connection), ['X1'], 'FX3U', 'generate')
        assert [row[1] for row in rows] == [1, 2]


def test_scope_partitions_refresh_with_connection_identity(tmp_path):
    path = tmp_path / 'facts.sqlite'
    def write(target, model):
        with closing(sqlite3.connect(target)) as connection, connection:
            connection.execute('CREATE TABLE chunks(id INTEGER, manual_type TEXT, plc_models TEXT)')
            connection.execute('INSERT INTO chunks VALUES(1,?,?)', ('programming', model))
    write(path, 'FX3U')
    try:
        connection = index_access.connection(path, index_access.index_identity(path))
        schema = index_access.schema(connection)
        for _ in range(2):
            sql, values = scope.source_subquery(connection, schema, ('fact',), plc_model='FX3U')
            assert [row[0] for row in connection.execute(sql, values)] == [1]
        replacement = tmp_path / 'replacement.sqlite'
        write(replacement, 'Q03UDV')
        # Close before replacement on Windows; derived metadata follows the
        # existing connection lifecycle rather than surviving as global data.
        index_access.close_thread_connection()
        replacement.replace(path)
        connection = index_access.connection(path, index_access.index_identity(path))
        sql, values = scope.source_subquery(connection, index_access.schema(connection),
                                           ('fact',), plc_model='FX3U')
        assert list(connection.execute(sql, values)) == []
    finally:
        index_access.close_thread_connection()


def test_fact_owner_index_keeps_cpu_vendor_and_replacement_semantics():
    class Owners(dict):
        scans = 0
        def __iter__(self):
            self.scans += 1
            return super().__iter__()
    registry = InstructionRegistry()
    owners = Owners({('mitsubishi', 'REFERENCE', 'FX3U'): {},
                     ('other', 'REFERENCE', 'Q03UDV'): {}})
    registry._definition_owners = owners
    assert registry.resolve_fact_form('reference', cpu='FX3U') is not None
    assert registry.resolve_fact_form('REFERENCE', cpu='Q03UDV') is not None
    assert registry.resolve_fact_form('REFERENCE', cpu='FX5U') is None
    assert registry.resolve_fact_form('REFERENCE') is not None
    for token in ('unknown', 'prose', 'not_an_instruction'):
        assert registry.resolve_fact_form(token, cpu='FX3U') is None
    assert owners.scans == 1
    registry._definition_owners = {('mitsubishi', 'NEW', 'FX5U'): {}}
    assert registry.resolve_fact_form('REFERENCE') is None
    assert registry.resolve_fact_form('NEW', cpu='FX5U') is not None
    assert registry.resolve_form('NEW', cpu='FX5U') is None
