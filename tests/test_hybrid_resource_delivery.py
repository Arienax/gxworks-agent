import hashlib
import json
import sqlite3

import numpy as np
import pytest


def test_dense_loader_binds_each_database_and_preserves_text_ids(tmp_path):
    from knowledge import core, dense, index_access
    from tools.build_dense_embeddings import publish
    for name, identity, owner in [('domain', 1, False), ('owner', 'definition:timer', True)]:
        database = tmp_path / (name + '.sqlite')
        output = tmp_path / (name + '.npz')
        manifest = tmp_path / (name + '.json')
        manifest.write_text('{}', encoding='utf-8')
        digest = hashlib.sha256(b'timer').hexdigest()
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
            connection.execute("INSERT INTO meta VALUES('corpus_kind','authored_plc_facts_v1')")
            if owner:
                connection.executescript('CREATE TABLE runtime_search_documents(id INTEGER PRIMARY KEY,sha256 TEXT,text TEXT);'
                    'CREATE TABLE runtime_chunk_bindings(id TEXT PRIMARY KEY,document_id INTEGER);'
                    'CREATE VIEW chunks AS SELECT b.id,d.text FROM runtime_chunk_bindings b '
                    'JOIN runtime_search_documents d ON d.id=b.document_id;')
                connection.execute('INSERT INTO runtime_search_documents VALUES(1,?,?)', (digest, 'timer'))
                connection.execute('INSERT INTO runtime_chunk_bindings VALUES(?,1)', (identity,))
            else:
                connection.execute('CREATE TABLE chunks(id INTEGER PRIMARY KEY,text TEXT,text_sha256 TEXT)')
                connection.execute('INSERT INTO chunks VALUES(?,?,?)', (identity, 'timer', digest))
        publish(database, output, manifest, ['w:timer'], np.array([1.0]), np.array([[1.0]]),
                np.array([[1.0]]), [identity], [digest])
        connection = index_access.connection(database, index_access.index_identity(database))
        schema = {name: {'name': name, 'columns': {row[1] for row in connection.execute('PRAGMA table_info('+name+')')}}
                  for name in ('meta', 'chunks', 'vector_embeddings')}
        state = core._dense_index_ready(connection, schema)
        assert state['ready'], state
        assert dense.dense_search('timer', model_path=state['model_path'])[0][0] == identity
    index_access.close_thread_connection()
    dense.clear_dense_cache()


def test_functional_mapping_does_not_skip_hybrid_resources(monkeypatch):
    from knowledge import retriever, fact_corpus
    from knowledge.evidence import KnowledgeQuery
    calls = []
    resource = {'id': 'clock', 'fact_kind': 'device', 'source_lane': 'fact', 'manual_type': 'authored_facts',
                'fact_target': 'M8011', 'text': 'clock pulse', 'retrieval_signals': ['vector']}
    aliases = [{'id': 'timer-alias-' + str(i), 'source_lane': 'fact',
                'manual_type': 'authored_facts', 'fact_kind': 'instruction', 'text': 'timer'} for i in range(5)]
    monkeypatch.setattr(fact_corpus, 'resolve', lambda *a, **kw: aliases)
    def hybrid(query, **options):
        calls.append(options)
        return [resource] if 'instruction' in options.get('exclude_structured_kinds', ()) else aliases
    monkeypatch.setattr(retriever.core, 'retrieve_broad_knowledge', hybrid)
    result = retriever.retrieve_knowledge(KnowledgeQuery('PLC timer delay', metadata={'semantic_function': 'timer'}),
                                          plc_model='FX3U', task_type='generate', top_k=5)
    assert calls and any(row['id'] == 'clock' for row in result)
    assert any(row['fact_kind'] == 'instruction' for row in result)
    assert len(result) <= 5
    assert any('instruction' in call.get('exclude_structured_kinds', ()) for call in calls)


@pytest.mark.parametrize('requirement,function,term', [
    ('暂停续行', 'sequence', 'retain'), ('量程换算', 'scaling', 'float'),
    ('状态字位拼装', 'logic', 'mask'), ('工步到站延时', 'timer', 'timebase'),
    ('报警确认', 'latch', 'acknowledge'), ('报警清除', 'bulk_reset', 'clear'),
])
def test_case_vocabulary_expands_without_instruction_assignment(requirement, function, term):
    from knowledge.capability_discovery import functional_queries
    from knowledge.query_vocabulary import expand_query
    assert term in expand_query(requirement)
    assert any(row['function'] == function and term in row['query'] for row in functional_queries(requirement))


def test_form_specific_array_does_not_leak_missing_values_into_delivery():
    from knowledge.instruction_presentation import instruction_form_units
    unit = {'kind': 'instruction', 'target': 'A', 'value': {'family': 'A',
        'forms': [{'opcode': 'A'}, {'opcode': 'B'}],
        'effects': [{'by_form': [{'forms': ['A'], 'value': {'write': 'destination'}}]}]}}
    first, second = instruction_form_units(unit)
    assert first['value']['effects'] == [{'write': 'destination'}]
    assert second['value']['effects'] == []
    json.dumps([first, second])


def test_dense_vocabulary_keeps_concepts_from_structured_names():
    from knowledge.dense import dense_features
    from tools.build_dense_embeddings import select_features
    features = dense_features('clock_pulse period_ms')
    assert {'w:clock', 'w:pulse', 'w:period', 'w:clock_pulse'} <= features.keys()
    frequency = {'a:ion': 1000, 'w:100': 1000, 'w:clock': 4, 'w:period': 12, 'c:时钟': 4}
    assert set(select_features(frequency, frequency, minimum_frequency=2, limit=3)) == {'w:clock', 'w:period', 'c:时钟'}


def test_timing_quantity_discovers_timing_without_clock_keyword():
    from knowledge.capability_discovery import functional_queries
    assert 'timer' in {row['function'] for row in functional_queries('到站0.6s，动作1秒')}


def test_installed_indexes_pair_each_database_with_its_vectors():
    from knowledge import core, index_access
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    paths = [index_access.index_path(), DEFAULT_INSTRUCTION_REGISTRY.selected_owner_database_path()]
    assert all(paths)
    artifacts = set()
    for path in paths:
        connection = core._connection(path, core._index_identity(path))
        state = core._dense_index_ready(connection, core._schema(connection))
        assert state['ready'], state
        artifacts.add(state['model_path'])
    assert len(artifacts) == 2
    rows = core.retrieve_broad_knowledge('PLC clock pulse period', plc_model='FX3U', top_k=12,
        source_lanes=('fact',), exclude_structured_kinds=('instruction', 'language', 'example', 'error'))
    assert {'M8011', 'M8012'} <= {row.get('fact_target') for row in rows}
    assert {'bm25', 'vector'} <= {signal for row in rows for signal in row.get('retrieval_signals', ())}


def test_package_collects_the_selected_owner_vector_sidecar(tmp_path, monkeypatch):
    from scripts import runtime_knowledge_resources as resources
    from plc import instruction_owner_store
    from plc.instruction_catalog_store import CATALOG_FILENAME
    directory = tmp_path / 'resources/instructions/mitsubishi'
    directory.mkdir(parents=True)
    for name in (*resources.INSTRUCTION_FILES, 'instruction_definitions.manifest.json', 'owner.npz', CATALOG_FILENAME):
        (directory / name).write_text('{}', encoding='utf-8')
    class Store:
        database_path = directory / 'owner.sqlite'
        metadata = {'corpus_kind': 'authored_plc_facts_v1', 'vector_artifact': 'owner.npz'}
        def __init__(self, *args, **kwargs): pass
        def iter_owner_headers(self): return iter(())
        def close(self): pass
    monkeypatch.setattr(instruction_owner_store, 'OwnerDefinitionStore', Store)
    monkeypatch.setattr(resources, 'assert_fact_database', lambda path: None)
    data = resources.runtime_instruction_data(tmp_path, include_compiled_catalog=True)
    assert (str(directory / 'owner.npz'), 'resources/instructions/mitsubishi') in data
    assert (str(directory / CATALOG_FILENAME), 'resources/instructions/mitsubishi') in data
