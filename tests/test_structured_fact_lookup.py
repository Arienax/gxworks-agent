from knowledge import index_access
"""Architecture tests for exact PLC facts outside the broad RAG scorer."""
import ast
import inspect
import json
import sqlite3

import pytest
from hypothesis import given, settings, strategies as st


def test_exact_corpus_lookup_keeps_every_explicit_target_and_source_variant(monkeypatch):
    from knowledge import fact_corpus

    class Cursor:
        def __init__(self, rows):
            self.rows = rows
        def fetchone(self):
            return self.rows[0] if self.rows else None
        def fetchall(self):
            return self.rows

    class Connection:
        def execute(self, query, arguments=()):
            if "key='corpus_kind'" in query:
                return Cursor([(fact_corpus.CORPUS_KIND,)])
            if "key='fact_bundle_version'" in query:
                return Cursor([])
            target = arguments[1]
            # A shared target can have more than fifty independent source
            # conditions; the later explicit target must also survive.
            count = 55 if target == 'MOV' else 1
            return Cursor([{'id': f'{target}:{i}', 'payload_json': '{"metadata":{}}'}
                           for i in range(count)])

    monkeypatch.setattr(fact_corpus, 'chunk_result', lambda row, *args: dict(row))
    result = fact_corpus.resolve('instruction', ['MOV', 'ANDF'],
                                plc_model='FX3U', connection=Connection())
    assert len(result) == 56
    assert result[-1]['id'] == 'ANDF:0'


@pytest.mark.parametrize('query,devices,errors', [
    ('D100 输出异常', ['D100'], []), ('C235 计数器故障', ['C235'], []),
    ('错误码 D100', [], ['D100']), ('error code C235', [], ['C235']),
    ('6106H / 6106 / 0x6106 故障', [], ['6106']),
    ('GX Works2 编译诊断 C0560', [], ['C0560']),
    ('错误码 ADD', [], ['ADD']),
    ('DEADD 指令操作数', [], []),
    ('错误码 D100，另查软元件 D100', ['D100'], ['D100']),
])
def test_diagnostic_namespace_preserves_the_explicit_entity_type(query, devices, errors):
    from knowledge.structured_facts import structured_fact_targets
    result = structured_fact_targets(query, plc_model='FX3U')
    assert result['devices'] == devices
    assert result['errors'] == errors
    if query == '错误码 ADD':
        assert not result['instructions']


@given(st.text(alphabet='0123456789ABCDEF', min_size=1, max_size=7))
@settings(deadline=None, max_examples=40)
def test_explicit_error_spellings_have_one_target_identity(code):
    from knowledge.text_matching import error_terms
    from knowledge.structured_facts import structured_fact_targets
    query = f'错误码 {code}，错误码 {code.lower()}h，错误码 0x{code}'
    assert error_terms(query) == [code]
    targets = structured_fact_targets(query, plc_model='FX3U')
    assert targets['errors'] == [code]
    assert not targets['devices']
    assert not any(row['opcode'] == code for row in targets['instructions'])


def test_diagnostic_candidates_use_identity_before_incidental_mentions():
    from knowledge.retriever import _diagnostic_candidate_rows
    rows = [
        {'id': 'neighbour', 'fact_kind': 'error', 'fact_target': '6107', 'text': 'Unlike 6106, this is 6107.'},
        {'id': 'exact', 'fact_kind': 'error', 'fact_target': '6106H', 'text': 'No repeated code needed.'},
        {'id': 'context', 'text': 'Check the current CPU and software version.'},
        {'id': 'other-domain', 'diagnostic_code': '6106', 'diagnostic_namespace': 'compiler'},
        {'id': 'heading-neighbour', 'chunk_type': 'error', 'section': 'Error 6107', 'text': 'See 6106 for comparison.'},
        {'id': 'body-reference', 'chunk_type': 'error', 'text': 'Error 6106 is the requested diagnostic.'},
    ]
    assert [row['id'] for row in _diagnostic_candidate_rows(rows, ['6106'], 'runtime')] == ['exact', 'context', 'body-reference']


pytestmark = pytest.mark.usefixtures('private_manual_evidence')

from knowledge import core
from knowledge.evidence import KnowledgeQuery
from knowledge.structured_facts import (
    resolve_device_records,
    resolve_error_records,
    resolve_instruction_contract,
    resolve_instruction_records,
    resolve_instruction_step_width,
    structured_fact_targets,
    without_structured_targets,
)


def _bundled_index():
    path = core._index_path()
    if core._index_identity(path)[0] == "missing":
        pytest.skip("Bundled knowledge index is not installed")
    return path


@pytest.mark.parametrize("query,homing,motion_topic", [
    ("D0 回零后才允许下一件物料", False, False),
    ("分类结果回零后重新登记，保持移位链", False, False),
    ("寄存器当前值回零", False, False),
    ("伺服轴的位置寄存器 D0 回零后记录", False, True),
    ("伺服轴回零后启动输送带", True, True),
    ("使用带 DOG 搜索回零", True, True),
    ("查询回零指令的操作数", True, True),
    ("回零", True, True),
    ("D0回零后登记；伺服轴回零后允许启动", True, True),
    ("D0回零后登记，不使用回零指令", False, False),
])
def test_zero_return_alias_requires_instruction_or_motion_context(query, homing, motion_topic):
    _bundled_index()
    from knowledge.analysis_router import route_analysis_request, generation_fact_query
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    spec = {"io_bindings": [{"kind": "D", "address": "D0"}]}
    projected = generation_fact_query(query, spec, "FX3U")
    targets = structured_fact_targets(projected, plc_model="FX3U")
    assert any(t["opcode"] in {"ZRN", "DSZR"} for t in targets["instructions"]) == homing
    route = route_analysis_request(query, resolve_opcode=DEFAULT_INSTRUCTION_REGISTRY.resolve_form)
    assert ("motion" in route.topics) == motion_topic


@pytest.mark.parametrize('cpu,query,address,status', [
    ('FX1S','selectable timer resolution','M8028','source_verified'),
    ('FX1N','M8028','M8028','not_applicable'),
    ('FX2N','M8028','M8028','conditions_missing'),
    ('L02','存储卡插拔状态','SM605','source_verified'),
    ('L02S','内置以太网','SD1282','not_applicable'),
    ('Q03UDE','SM605','SM605','conditions_missing'),
])
def test_reviewed_device_definitions_retain_scope_conditions_and_sources(cpu,query,address,status):
    _bundled_index()
    from knowledge.retriever import retrieve_fact_aware_knowledge, _covered_structured_kinds
    from knowledge.evidence import evidence_record
    records = retrieve_fact_aware_knowledge(query,plc_model=cpu,top_k=3,char_budget=6000)
    record = next(r for r in records if r.get('fact_target') == address)
    assert record['device_definition_status'] == status
    assert record['device_lookup_basis'] == 'reviewed_source_definition'
    assert evidence_record(record)['device_definition']['cpu'] == cpu
    assert record['source_evidence']
    closed = _covered_structured_kinds({'devices':[address]},[record])
    assert ('device' in closed) == (status in {'source_verified','not_applicable'})
    if status == 'conditions_missing':
        assert 'Condition:' in record['text']


@pytest.mark.parametrize('device', ['SM605', 'SM1515', 'SD1282'])
def test_device_class_heading_does_not_claim_an_individual_definition(monkeypatch, device):
    import knowledge.structured_facts as facts
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    connection.execute('CREATE TABLE chunks(id INTEGER,section TEXT,manual_type TEXT,text TEXT)')
    connection.executemany('INSERT INTO chunks VALUES(?,?,?,?)', [
        (1,'Special relay (SM)','cpu_hardware','General SM range and memory capacity'),
        (2,'Special register (SD)','cpu_hardware','Example uses SD1282; no definition or CPU applicability')])
    schema = {'chunks':{'name':'chunks','columns':{'id','section','manual_type','text'}}}
    monkeypatch.setattr(facts, '_runtime', lambda:(core,None,connection,schema,{}))
    monkeypatch.setattr(core, '_row_in_scope', lambda *a: True)
    assert facts._device_definition_ids(connection,schema,device,plc_model='Q03UDV',task_type='analysis') == []
    connection.close()


@pytest.mark.parametrize('residual', ['', 'SM605 memory card status'])
def test_device_mentions_do_not_close_residual_lookup(residual):
    from knowledge.retriever import _covered_structured_kinds, _residual_fact_text
    targets = {'instructions':[], 'devices':['SM605'], 'errors':[]}
    mention = {'fact_kind':'device_occurrence','fact_target':'SM605','text':'Example reads SM605'}
    covered = _covered_structured_kinds(targets,[mention])
    assert 'device' not in covered
    assert 'SM605' in _residual_fact_text(residual,targets,covered)


@pytest.mark.parametrize('requested,actual,same',[
    ('SB0041','SB41',True),('SD001282.0','SD1282.0',True),
    ('SB0041','SB42',False),('SD1282.0','SD1282.1',False),
])
def test_definition_coverage_coalesces_only_device_spelling_aliases(requested,actual,same):
    from knowledge.retriever import _covered_structured_kinds
    record={'fact_kind':'device','fact_target':actual,'device_definition_status':'source_verified'}
    assert ('device' in _covered_structured_kinds({'devices':[requested]},[record])) is same


@pytest.mark.parametrize("char_budget,token_budget,delivered", [(96000, 12000, True), (1000, 200, True)])
def test_process_dependencies_and_designated_instructions_survive_legacy_budgets(char_budget, token_budget, delivered):
    _bundled_index()
    from knowledge.retriever import build_knowledge_context
    from knowledge.evidence import estimate_tokens
    targets = {"instructions": [{"opcode": op, "base_opcode": op} for op in ("TCMP", "MUL", "DDIV", "WSFL", "BMOV")],
               "devices": [], "errors": [], "process": [
                   {"target": "FIRST_SCAN", "dimension": "definition", "devices": ["M8002"]},
                   {"target": "TIMER", "dimension": "operation", "devices": ["T0"]}]}
    query = KnowledgeQuery("TCMP MUL DDIV WSFL BMOV", precompiled=True,
        metadata={"structured_fact_targets": targets, "residual_fact_query": ""})
    context = build_knowledge_context(query, top_k=5, char_budget=char_budget, token_budget=token_budget)
    assert context == build_knowledge_context(query, top_k=5)
    assert context.manifest["primary_top_k"] == 5
    process_requirements = [r for r in context.manifest["fact_coverage"]["requirements"] if r["kind"] == "process"]
    assert len(process_requirements) == 2
    expected_status = "candidate_evidence" if delivered else "not_delivered"
    assert {r["status"] for r in process_requirements} == {expected_status}
    if delivered:
        assert {r.get("instruction_opcode") for r in context.manifest["records"] if r["chunk_type"] == "instruction"} >= {"TCMP", "MUL", "DDIV", "WSFL", "BMOV"}
        assert len(context.manifest["process_dependency_ids"]) == 2
        delivered_ids = {row['id'] for row in context.manifest['records']}
        assert all(source in delivered_ids for r in process_requirements for source in r["source_ids"])
    else:
        assert context.manifest["process_dependency_ids"] == []


def test_broad_retrieval_uses_unweighted_rrf_without_plc_topic_boosts():
    import knowledge.core as knowledge_core

    source = inspect.getsource(knowledge_core)
    broad = inspect.getsource(knowledge_core._retrieve_uncached)
    metadata = inspect.getsource(knowledge_core._broad_metadata_references)
    tree = ast.parse(source)

    named_score_assignments = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "score" for target in targets):
                named_score_assignments.append(getattr(node, "lineno", 0))

    assert named_score_assignments == []
    assert "_RRF_K" in broad
    assert "1.0 / (_RRF_K + signal[\"rank\"] + 1.0)" in broad
    for legacy in (
        "positioning_query", "timer_query", "timer_device_section",
        "task_boost", "alias_scores", "_base_score", "base_score",
        "score +=", "score -=", "_query_is_timer_semantics",
        "_timer_debug_case_matches_query",
    ):
        assert legacy not in source
        assert legacy not in metadata


@pytest.mark.parametrize("query", [
    "generic PLC reference", "FX3U DRVI direction output fixed assignment Y0",
    "FX3U T0 preset K10 time base", "FX3U M8013 clock blink",
    "FX3U step state machine",
])
def test_broad_rrf_scores_only_candidate_channel_ranks(monkeypatch, tmp_path, query):
    """The ranking oracle is the RRF formula, independent of query routing."""
    rows = {( "id", name): {"id": name, "text": "candidate " + name}
            for name in ("metadata", "entity", "lexical", "dense", "combined")}
    monkeypatch.setattr(core, "_connection", lambda *a: object())
    monkeypatch.setattr(core, "_schema", lambda *a: {"chunks": {}})
    monkeypatch.setattr(core, "_load_meta", lambda *a: {})
    monkeypatch.setattr(core, "_fetch_chunks", lambda *a: rows)
    monkeypatch.setattr(core, "_chunk_result", lambda row, *a: row)
    monkeypatch.setattr(core, "_fts_match_quality", lambda *a: (1.0, []))
    monkeypatch.setattr(core, "_broad_metadata_references", lambda *a, **k: [
        {"kind": "id", "value": "metadata", "match_type": "manual_section", "matched": "", "rank": 0},
        {"kind": "id", "value": "combined", "match_type": "manual_section", "matched": "", "rank": 2},
        # Two metadata selectors cannot count as two independent channels.
        {"kind": "id", "value": "combined", "match_type": "manual_id", "matched": "", "rank": 8},
    ])
    monkeypatch.setattr(core, "_entity_references", lambda *a, **k: [
        ("id", "entity", "MOV", 0), ("id", "combined", "MOV", 1)])
    monkeypatch.setattr(core, "_fts_references", lambda *a, **k: [
        ("id", "lexical", 0, -1000000), ("id", "combined", 4, -0.001)])
    monkeypatch.setattr(core, "_dense_references", lambda *a, **k: [
        ("dense", 0.999, 0), ("combined", 0.01, 3)])

    results = core._retrieve_uncached(tmp_path / "unused.sqlite", ("unused", 0, 0),
                                     query, "", "analysis", 10, None)
    by_id = {row["id"]: row for row in results}
    assert results[0]["id"] == "combined"
    assert by_id["combined"]["score"] == round(sum(1 / (60 + rank + 1) for rank in (2, 1, 4, 3)), 8)
    assert {by_id[name]["score"] for name in ("metadata", "entity", "lexical", "dense")} == {round(1 / 61, 8)}
    assert set(by_id["combined"]["retrieval_signals"]) == {"manual_section", "entity", "bm25", "vector"}


def test_index_replacement_invalidates_connection_schema_and_derived_state(tmp_path):
    from knowledge import index_access
    path = tmp_path / "replace.sqlite"
    with sqlite3.connect(path) as writer:
        writer.execute("CREATE TABLE chunks(id INTEGER)")
    before_identity = index_access.index_identity(path)
    first = index_access.connection(path, before_identity)
    assert index_access.schema(first)["chunks"]["columns"] == ("id",)
    assert first.execute("PRAGMA query_only").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError):
        first.execute("INSERT INTO chunks VALUES(1)")
    index_access.cache_value(first, "instruction_sections", ["old"])
    with sqlite3.connect(path) as writer:
        writer.execute("ALTER TABLE chunks ADD COLUMN text TEXT")
    try:
        after_identity = index_access.index_identity(path)
        assert after_identity != before_identity
        second = index_access.connection(path, after_identity)
        assert second is not first
        assert index_access.cached_value(second, "instruction_sections") is None
        assert index_access.schema(second)["chunks"]["columns"] == ("id", "text")
        with pytest.raises(sqlite3.ProgrammingError):
            first.execute("SELECT 1")
    finally:
        index_access.close_thread_connection()


def test_index_connections_and_derived_caches_are_thread_local(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from knowledge import index_access
    path = tmp_path / "threads.sqlite"
    with sqlite3.connect(path) as writer:
        writer.execute("CREATE TABLE chunks(id INTEGER)")
    identity = index_access.index_identity(path)
    barrier = Barrier(2)
    def read(marker):
        connection = index_access.connection(path, identity)
        try:
            index_access.cache_value(connection, "owner", marker)
            barrier.wait(timeout=5)
            assert index_access.connection(path, identity) is connection
            return id(connection), index_access.cached_value(connection, "owner"), connection.execute("SELECT 7").fetchone()[0]
        finally:
            index_access.close_thread_connection()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(read, ("first", "second")))
    assert first[0] != second[0]
    assert (first[1:], second[1:]) == (("first", 7), ("second", 7))


@pytest.mark.parametrize("query,wiring,timer", [
    ("FX3U Y0 pulse output: must direction output be fixed to Y4?", True, False),
    ("FX3U-2HSY-ADP direction output fixed pair", True, False),
    ("FX3U 将 Y0 指定为指示灯输出", False, False),
    ("FX3U DRVI direction signal ON/OFF timing", False, False),
    ("FX3U timer T0 preset K100 time base", False, True),
    ("FX3U M8013 clock relay 1 second flashing", False, False),
    ("FX3U step state machine", False, False),
])
def test_topic_fact_plan_only_requests_explicit_wiring_and_timer_questions(query, wiring, timer):
    from knowledge.fact_queries import query_is_timer_preset
    from knowledge.evidence import KnowledgeQuery
    query = KnowledgeQuery(query, metadata={"hardware_context": {"output_type": "transistor"}})
    targets = structured_fact_targets(query, plc_model="FX3U")
    assert bool(targets["positioning"]) is wiring
    assert query_is_timer_preset(query) is timer
    if wiring:
        target, = targets["positioning"]
        assert target["plc_model"] == "FX3U"
        assert target["hardware_context"] == {"output_type": "transistor"}
        query.metadata["hardware_context"]["output_type"] = "relay"
        assert target["hardware_context"] == {"output_type": "transistor"}


def test_legacy_wiring_fact_lookup_keeps_cpu_and_unresolved_hardware_context(monkeypatch, tmp_path):
    from knowledge import index_access, structured_facts as facts
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE chunks(id INTEGER,section TEXT,manual_type TEXT,text TEXT,plc_models TEXT)")
    connection.executemany("INSERT INTO chunks VALUES(?,?,?,?,?)", [
        (1, "Assignment of Output Numbers", "positioning", "FX3U-2HSY-ADP: full output conditions", "FX3U"),
        (2, "Assignment of Output Numbers", "positioning", "FX5U: incompatible CPU wiring", "FX5U"),
    ])
    schema = index_access.schema(connection)
    monkeypatch.setattr(facts, "_runtime", lambda: (index_access, tmp_path / "fixture.sqlite", connection, schema, {}))
    try:
        target = {"target": "DIRECTION_OUTPUT_ASSIGNMENT", "plc_model": "FX3U",
                  "hardware_context": None, "products": ["FX3U-2HSY-ADP"]}
        records = facts.resolve_positioning_records([target], plc_model="FX3U")
        assert [row["id"] for row in records] == ["1"]
        assert records[0]["positioning_hardware_status"] == "source_scoped"
        assert facts.retrieval_coverage("", records, plc_model="FX3U", targets={"positioning": [target]})["status"] == "complete"
        assert facts.resolve_positioning_records([target], plc_model="FX5U") == []
        missing = {**target, "products": ["FX3U-UNKNOWN-ADAPTER"]}
        unresolved = facts.resolve_positioning_records([missing], plc_model="FX3U")
        assert [row["id"] for row in unresolved] == ["1"]
        assert unresolved[0]["positioning_hardware_status"] == "unverified"
        assert facts.retrieval_coverage("", unresolved, plc_model="FX3U", targets={"positioning": [missing]})["status"] == "incomplete"
        contextual = {**target, "hardware_context": {"output_type": "relay"}}
        assert facts.resolve_positioning_records([contextual], plc_model="FX3U")[0]["positioning_hardware_status"] == "unverified"
    finally:
        connection.close()


def test_missing_structured_wiring_fact_stays_unresolved(monkeypatch):
    from knowledge import index_access, structured_facts as facts
    target = {"target": "DIRECTION_OUTPUT_ASSIGNMENT", "plc_model": "FX3U"}
    monkeypatch.setattr(facts, "_runtime", lambda: (index_access, None, None, None, {}))
    records = facts.resolve_positioning_records([target], plc_model="FX3U")
    assert records == []
    coverage = facts.retrieval_coverage("", records, plc_model="FX3U", targets={"positioning": [target]})
    assert coverage["targets"] == [{"kind": "positioning", "target": "DIRECTION_OUTPUT_ASSIGNMENT",
                                   "identity_found": False, "status": "unresolved", "reason": "exact_fact_not_found",
                                   "corpus": "absent", "knowledge": "unresolved", "delivery": "not_available"}]


@pytest.mark.parametrize("status", ["source_scoped", "unverified"])
def test_positioning_technical_projection_preserves_applicability_without_source_identity(status):
    from knowledge.retriever import format_knowledge_reference
    record = {"id": "private-source", "manual_id": "restricted-manual", "source": "private.pdf",
              "fact_kind": "positioning", "text": "SOURCE: private.pdf\nDirection output conditions.",
              "positioning_lookup_basis": "legacy_official_property_section",
              "positioning_context": {"plc_model": "FX3U", "products": ["FX3U-2HSY-ADP"],
                                      "hardware_context": {"output_type": "transistor"}},
              "positioning_hardware_status": status}
    text = format_knowledge_reference(record)
    payload = json.loads(text.splitlines()[1])
    assert payload["applicability"] == {"plc_model": "FX3U", "products": ["FX3U-2HSY-ADP"],
                                       "hardware_context": {"output_type": "transistor"}, "hardware_status": status}
    assert "Direction output conditions." in text
    for private in ("private-source", "restricted-manual", "private.pdf", "legacy_official_property_section"):
        assert private not in text
    assert "private.pdf" in format_knowledge_reference(record, debug=True)


@pytest.mark.parametrize("context,bound,expected", [
    (None, False, "source_scoped"),
    ({"adapter": "example-adapter"}, False, "unverified"),
    ({"adapter": "example-adapter"}, True, "source_scoped"),
])
def test_authored_cpu_fact_only_satisfies_explicitly_bound_hardware_context(monkeypatch, context, bound, expected):
    from knowledge import index_access, structured_facts as facts, fact_corpus
    target = {"target": "DIRECTION_OUTPUT_ASSIGNMENT", "plc_model": "FX3U", "hardware_context": context}
    record = {"id": "typed-wiring-fact", "fact_kind": "positioning", "fact_target": target["target"],
              "text": "typed fact", "positioning_hardware_status": "source_scoped"}
    if bound:
        record["positioning_context"] = dict(target)
    original = dict(record)
    monkeypatch.setattr(facts, "_runtime", lambda: (index_access, None, object(), {}, {}))
    monkeypatch.setattr(fact_corpus, "resolve", lambda *a, **k: [record])
    result, = facts.resolve_positioning_records([target], plc_model="FX3U")
    assert result["positioning_context"] == target
    assert result["positioning_hardware_status"] == expected
    assert record == original


def test_public_retriever_does_not_forward_arbitrary_private_backend_helpers():
    from knowledge import retriever
    for name in ("__getattr__", "_retrieve_cached", "_close_thread_connection", "_thread_state"):
        with pytest.raises(AttributeError):
            getattr(retriever, name)


def test_structured_topic_read_failure_is_retried_without_breaking_public_retrieval(monkeypatch):
    from knowledge import structured_facts as facts, retriever
    calls = []
    def read(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("temporary structured read failure")
        return [{"id": "wiring", "manual_type": "positioning", "text": "CPU-specific wiring conditions"}]
    monkeypatch.setattr(facts, "resolve_positioning_records", read)
    monkeypatch.setattr(core, "retrieve_broad_knowledge", lambda *a, **k: [])
    query = "FX3U Y0 pulse output: must direction output be fixed to Y4?"
    assert retriever.retrieve_knowledge(query) == []
    assert [row["id"] for row in retriever.retrieve_knowledge(query)] == ["wiring"]
    assert len(calls) == 2


def test_direct_fact_module_has_no_broad_retriever_calls():
    import knowledge.structured_facts as facts

    tree = ast.parse(inspect.getsource(facts))
    calls = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            calls.add(node.func.attr)
    assert "retrieve_knowledge" not in calls
    assert "dense_search" not in calls
    assert "_fts_references" not in calls


@pytest.mark.parametrize("model,expected", [("FX3U", ["M8002"]),
                                            ("FX5U", ["SM402", "SM8002"]),
                                            ("unknown_cpu", [])])
def test_process_fact_plan_uses_confirmed_semantics_and_keeps_model_scope(model, expected):
    from plc.semantics import generation_process_fact_needs

    spec = {"plc_model": model, "io_table": [{"address": "X0", "label": "按钮"}],
            "execution_semantics": [{"semantic": "FIRST_SCAN"}],
            "selected_approach": {"generation_guide": "T0至T19定时器，C0单瓶计数",
                "generation_contract": {"required_structures": ["hardware_counter"]}}}
    needs = generation_process_fact_needs(spec)
    assert needs == [{"target": "FIRST_SCAN", "dimension": "definition", "devices": expected,
                      "basis": "confirmed_execution_semantics"},
                     {"target": "TIMER", "dimension": "operation", "devices": ["T0", "T19"],
                      "basis": "declared_timer_references"},
                     {"target": "COUNTER", "dimension": "operation", "devices": ["C0"],
                      "basis": "confirmed_hardware_counter_structure"}]
    assert generation_process_fact_needs({"plc_model": model, "io_table": spec["io_table"]}) == []


def test_process_facts_resolve_official_operating_sections_and_preserve_raw_sources():
    from knowledge.structured_facts import resolve_process_records
    _bundled_index()
    needs = [{"target": "FIRST_SCAN", "dimension": "definition", "devices": ["M8002"]},
             {"target": "TIMER", "dimension": "operation", "devices": ["T0", "T200"]},
             {"target": "COUNTER", "dimension": "operation", "devices": ["C0", "C1"]}]
    rows = resolve_process_records(needs, plc_model="FX3U")
    assert {row["fact_target"] for row in rows} == {"FIRST_SCAN", "TIMER", "COUNTER"}
    with sqlite3.connect(f"file:{_bundled_index().as_posix()}?mode=ro", uri=True) as connection:
        for row in rows:
            original = connection.execute("SELECT text FROM chunks WHERE id=?", (row["id"],)).fetchone()[0]
            assert row["text"] == original
            assert row["structured_lookup"] is True
    fx5 = resolve_process_records(needs, plc_model='FX5U')
    assert {r['fact_target'] for r in fx5} == {'FIRST_SCAN','TIMER'}
    assert all(r['plc_models'] == ['FX5U'] and r['revision'] in {'AG','AB'} for r in fx5)
    first = json.loads(next(r['text'] for r in fx5 if r['fact_target']=='FIRST_SCAN'))
    assert first['conditions'] == {'program_type':'scan','initial_execution_program':False}
    timer = json.loads(next(r['text'] for r in fx5 if r['fact_target']=='TIMER'))
    assert timer['forms']['OUT'][0]['unit_ms'] == 100
    assert timer['forms']['OUTH'][0]['unit_ms'] == 10
    assert timer['forms']['OUTHS'][0]['unit_ms'] == 1
    assert resolve_process_records([{"target": "COUNTER", "dimension": "operation",
                                     "devices": ["C235"]}], plc_model="FX3U") == []
    assert resolve_process_records([{"target": "TIMER", "dimension": "operation",
                                     "devices": ["T250"]}], plc_model="FX3U") == []


def test_projected_process_plan_keeps_the_explicit_runtime_cpu():
    from application.confirmed_generation_context import build_confirmed_generation_context
    queries = []
    def retrieve(query, **_kwargs):
        queries.append(query)
        return ""
    spec = {"plc_model": "FX3U", "execution_semantics": [{"semantic": "FIRST_SCAN"}]}
    build_confirmed_generation_context(spec, "FX3U", knowledge_builder=retrieve)
    targets = queries[0].metadata["structured_fact_targets"]
    assert targets["process"][0]["devices"] == ["M8002"]
    assert queries[0].metadata["residual_fact_query"] == ""


@pytest.mark.parametrize("opcode", ["SFTL", "ZRN", "MOV"])
def test_exact_instruction_is_resolved_from_structured_table(opcode):
    _bundled_index()
    targets = [{"opcode": opcode, "base_opcode": opcode}]
    rows = resolve_instruction_records(targets, plc_model="FX3U", task_type="generate")
    assert rows
    assert all(row["structured_lookup"] is True for row in rows)
    assert all(row["structured_fact_kind"] == "instruction" for row in rows)
    assert any(str(row.get("instruction_opcode") or "").upper() == opcode for row in rows)
    assert all(row["match_type"] == "structured_direct" for row in rows)
    # These targets name a mnemonic without operands, and all three take operands,
    # so the width is the operand question rather than a fixed number.
    assert all(row["instruction_step_width"]["status"] == "requires_operands"
               for row in rows)
    assert all("STEP_WIDTH:" not in row["text"] for row in rows)


@pytest.mark.parametrize("opcode,manual_opcode,page", [
    ("ADD", "ADD", 273), ("DFLT", "FLT", 338), ("DPLSY", "PLSY", 377),
])
def test_verified_form_source_pages_own_lookup_even_without_an_exact_indexed_form(opcode, manual_opcode, page):
    _bundled_index()
    rows = resolve_instruction_records([{"opcode": opcode}], plc_model="FX3U", task_type="generate")
    assert rows
    primary = rows[0]
    assert primary["instruction_lookup_basis"] == "compiled_definition_section"
    assert primary["instruction_opcode"] == opcode
    assert primary["manual_instruction_opcode"] == manual_opcode
    assert primary["instruction_contract_source"]["pdf_page"] == page
    assert primary["manual_id"] == "fx3_programming_r"
    assert primary["revision"] == "R"
    assert primary["instruction_contract"]["opcode"] == opcode
    assert all(slot["symbol_status"] == "source_verified" for slot in primary["operand_slots"])


def test_source_page_lookup_does_not_borrow_fx3u_verification_for_other_cpus():
    from knowledge.structured_facts import _instruction_contract_source_records
    _bundled_index()
    assert _instruction_contract_source_records({"opcode": "DFLT"}, plc_model="FX5U", task_type="generate") == []


def test_every_promoted_fx3u_contract_uses_registry_owner():
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, generation_app_instr_mnemonics

    promoted = [
        opcode
        for opcode in generation_app_instr_mnemonics("FX3U")
        if DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode, cpu="FX3U").verified_fields
    ]
    assert len(promoted) > 200
    for opcode in promoted:
        expected = DEFAULT_INSTRUCTION_REGISTRY.describe_contract(opcode, cpu="FX3U")
        actual = resolve_instruction_contract(
            {"opcode": opcode, "base_opcode": expected["base_mnemonic"]},
            plc_model="FX3U",
        )
        assert actual == expected, opcode


@pytest.mark.parametrize("opcode", ["MOV", "WSFL", "DRVA"])
def test_structured_instruction_record_merges_registry_contract(opcode):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    _bundled_index()
    rows = resolve_instruction_records(
        [{"opcode": opcode, "base_opcode": opcode}],
        plc_model="FX3U",
        task_type="generate",
    )
    assert rows
    expected = DEFAULT_INSTRUCTION_REGISTRY.describe_contract(opcode, cpu="FX3U")
    assert rows[0]["instruction_contract"] == expected
    assert "OPERAND_SEMANTICS:" in rows[0]["text"]
    # Target/runtime lanes are sparse and appear only when they add a decision.
    assert rows[0]["target_applicability"]["target_model"] == "FX3U"
    assert "runtime_semantics" in rows[0]
    assert rows[0]["instruction_contract"]["contract_level"] == "signature_verified"


def test_structured_contract_prompt_view_is_compact_but_metadata_keeps_sources():
    rows = resolve_instruction_records(
        [{"opcode": "SFTL", "base_opcode": "SFTL"}],
        plc_model="FX3U",
        task_type="analysis",
    )
    assert rows
    row = rows[0]
    assert row["instruction_contract"].get("sources")
    operand_line = next(
        line for line in row["text"].splitlines()
        if line.startswith("OPERAND_SEMANTICS:")
    )
    assert '"slots"' in operand_line
    assert '"role"' in operand_line
    usage = json.loads(operand_line.split(": ", 1)[1])
    assert usage["sources"]  # usage-specific references are shared, not a mixed contract blob
    assert all(fact.get("source_refs") for slot in usage["slots"] for fact in slot.get("usage_facts", []))
    assert "STEP_WIDTH:" not in row["text"]


def test_instruction_instance_contract_keeps_exact_operands_and_source():
    target = {
        "opcode": "SFTL",
        "base_opcode": "SFTL",
        "operands": ["M10", "M100", "K128", "K1"],
        "instance_source": "generation_contract",
    }
    contract = resolve_instruction_contract(target, plc_model="FX3U")
    assert contract["confirmed_operands"] == target["operands"]
    assert contract["instance_source"] == "generation_contract"
    assert contract["native_operand_order"] == ["S", "D", "N1", "N2"]
    assert {"arity", "operand_order", "form_identity"} <= set(contract["verified_fields"])


def test_instruction_lanes_split_common_semantics_target_overlay_and_runtime():
    from plc.instruction_resolution import resolve_instruction_lanes

    add_fx3 = resolve_instruction_lanes(
        {"opcode": "ADD", "operands": ["K1", "D1", "D2"]},
        plc_model="FX3U",
    )
    add_fx5 = resolve_instruction_lanes(
        {"opcode": "ADD", "operands": ["K1", "D1", "D2"]},
        plc_model="FX5U",
    )

    assert add_fx3["operand_semantics"]["operands"] == add_fx5["operand_semantics"]["operands"]
    assert [slot["role"] for slot in add_fx3["operand_slots"]] == ["read", "read", "write"]
    assert [slot["value"] for slot in add_fx3["operand_slots"]] == ["K1", "D1", "D2"]
    assert {slot["role_status"] for slot in add_fx3["operand_slots"]} == {"source_verified"}
    assert {slot["symbol_status"] for slot in add_fx3["operand_slots"]} == {"source_verified"}
    assert {slot["device_class_status"] for slot in add_fx3["operand_slots"]} == {"unresolved"}
    assert {slot["role_status"] for slot in add_fx5["operand_slots"]} != {"source_verified"}
    assert add_fx3["target_applicability"]["target_model"] == "FX3U"
    assert add_fx5["target_applicability"]["target_model"] == "FX5U"
    assert add_fx3["runtime_semantics"]["special_devices"] == []
    assert add_fx5["runtime_semantics"]["special_devices"] == []

    zrn_fx3 = resolve_instruction_lanes({"opcode": "ZRN"}, plc_model="FX3U")
    zrn_fx5 = resolve_instruction_lanes({"opcode": "ZRN"}, plc_model="FX5U")
    assert zrn_fx3["target_applicability"]["available"] is True
    assert zrn_fx3["runtime_semantics"]["completion"]["device"] == "M8029"
    assert zrn_fx5["target_applicability"]["available"] is False
    assert zrn_fx5["runtime_semantics"]["special_devices"] == []


def test_sparse_lane_view_omits_empty_target_and_runtime_but_keeps_real_runtime_fact():
    from plc.instruction_resolution import resolve_instruction_lanes
    from knowledge.structured_facts import instruction_lane_prompt_lines

    add = instruction_lane_prompt_lines(
        resolve_instruction_lanes(
            {"opcode": "ADD", "operands": ["K1", "D1", "D2"]},
            plc_model="FX3U",
        )
    )
    assert len(add) == 1
    assert add[0].startswith("OPERAND_SEMANTICS:")
    pulse = instruction_lane_prompt_lines(resolve_instruction_lanes("DADDP", plc_model="FX3U"))
    target = json.loads(next(line for line in pulse if line.startswith("TARGET_APPLICABILITY:")).split(": ", 1)[1])
    assert target["execution_form"] == "pulse"
    assert target["execution_form_status"] == "source_verified"

    zrn = instruction_lane_prompt_lines(
        resolve_instruction_lanes({"opcode": "ZRN"}, plc_model="FX3U")
    )
    assert any(line.startswith("TARGET_APPLICABILITY:") for line in zrn)
    assert any(line.startswith("RUNTIME_SEMANTICS:") for line in zrn)
    assert any("M8029" in line for line in zrn)


def test_model_facing_instruction_fact_uses_split_lane_headers():
    rows = resolve_instruction_records(
        [{
            "opcode": "SFTL",
            "base_opcode": "SFTL",
            "operands": ["M10", "M100", "K56", "K1"],
        }],
        plc_model="FX3U",
        task_type="generate",
    )
    assert rows
    row = rows[0]
    assert "OPERAND_SEMANTICS:" in row["text"]
    assert "INSTRUCTION_CONTRACT:" not in row["text"]
    assert "STEP_WIDTH:" not in row["text"]
    assert row["instruction_contract"]
    assert [slot["value"] for slot in row["operand_slots"]] == [
        "M10", "M100", "K56", "K1",
    ]


def test_local_instruction_fallback_keeps_contract_and_step_width_provenance_separate(tmp_path, monkeypatch):
    # Exercise a genuinely unavailable manual index, not a particular mnemonic
    # that happened to be missing from a prior catalogue revision.
    monkeypatch.setattr(index_access, "index_path", lambda: tmp_path / "not-installed.sqlite")
    rows = resolve_instruction_records(
        [{"opcode": "RST", "base_opcode": "RST", "operands": ["D10"]}],
        plc_model="FX3U",
        task_type="generate",
    )
    assert rows
    row = rows[0]
    assert row["source"] == "local_structured_instruction_owners"
    assert row["instruction_contract"]["opcode"] == "RST"
    assert isinstance(row["instruction_contract"].get("sources"), list)
    assert row["instruction_step_width"]["steps"] == 3
    assert row["instruction_step_width"]["source"] in {"exact_native_form", "operand_rule", "native_observation"}


def test_fx3u_contract_promotions_do_not_leak_into_fx5u_structured_facts():
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    expected = DEFAULT_INSTRUCTION_REGISTRY.describe_contract("MOV", cpu="FX5U")
    actual = resolve_instruction_contract({"opcode": "MOV"}, plc_model="FX5U")
    assert actual == expected
    assert actual["verified_fields"] == []
    assert actual["contract_level"] != "signature_verified"


def test_structured_step_width_uses_shared_owner_for_instruction_instance():
    _bundled_index()
    cases = [
        ("RST", ["D10"], 3),
        ("RST", ["T16"], 2),
        ("RST", ["M2"], 1),
        ("SFTL", ["M10", "M100", "K56", "K1"], 9),
    ]
    for opcode, operands, expected in cases:
        rows = resolve_instruction_records(
            [{"opcode": opcode, "base_opcode": opcode, "operands": operands}],
            plc_model="FX3U",
            task_type="generate",
        )
        assert rows, opcode
        fact = rows[0]["instruction_step_width"]
        assert fact["known"] is True
        assert fact["steps"] == expected
        assert fact["resolution"] == "instruction_instance"
        assert fact["operands"] == operands
        assert "STEP_WIDTH:" not in rows[0]["text"]
        if opcode == "RST":
            assert rows[0]["instruction_lookup_basis"] == "compiled_definition_section"
            assert rows[0]["manual_number"] == "JY997D16601"
            assert rows[0]["instruction_contract"]["opcode"] == "RST"


def test_opcode_only_width_is_exposed_only_for_an_operandless_mnemonic():
    # SFTL stores nine words as an instance, but its operands decide that, so the
    # mnemonic alone has no width and nothing is invented for it.
    instance = resolve_instruction_step_width(
        {"opcode": "SFTL", "base_opcode": "SFTL", "operands": ["D0", "D1", "K1", "K1"]},
        plc_model="FX3U",
    )
    assert instance["known"] is True and instance["steps"] == 9
    fact = resolve_instruction_step_width(
        {"opcode": "SFTL", "base_opcode": "SFTL"},
        plc_model="FX3U",
    )
    assert fact["known"] is False and fact["steps"] is None
    assert fact["resolution"] == "requires_operands"
    assert fact["operands"] == []
    # An instruction that takes no operands has its width on its own.
    end = resolve_instruction_step_width(
        {"opcode": "END", "base_opcode": "END"},
        plc_model="FX3U",
    )
    assert (end["known"], end["steps"], end["resolution"]) == (True, 1, "fixed_mnemonic")
    assert end["operands"] == []


def test_operand_dependent_opcode_without_operands_stays_unresolved():
    fact = resolve_instruction_step_width(
        {"opcode": "RST", "base_opcode": "RST"},
        plc_model="FX3U",
    )
    assert fact["known"] is False
    assert fact["steps"] is None
    assert fact["resolution"] == "requires_operands"
    assert "depends on the operands" in fact["reason"]


def test_step_width_does_not_borrow_fx3u_catalogue_for_other_cpu():
    fact = resolve_instruction_step_width(
        {"opcode": "SFTL", "base_opcode": "SFTL"},
        plc_model="FX5U",
    )
    assert fact["known"] is False
    assert fact["steps"] is None
    # FX5U keeps its Core target identity; the missing coverage is its own state,
    # not an operand question and not evidence that the instruction is unsupported.
    assert fact["status"] == "no_profile"
    assert fact["resolution"] == "uncovered"


def test_opcode_only_target_reports_missing_cpu_separately_from_coverage():
    missing = resolve_instruction_step_width({"opcode": "SFTL"}, plc_model="")
    assert missing["known"] is False and missing["status"] == "unknown_cpu"
    assert missing["requirements"] == ["plc_model"]
    unknown = resolve_instruction_step_width({"opcode": "SFTL"}, plc_model="Q99")
    assert unknown["status"] == "unknown_cpu" and unknown["resolution"] == "unknown_cpu"
    uncovered = resolve_instruction_step_width({"opcode": "NOT_A_REAL_OPCODE"}, plc_model="FX3U")
    assert uncovered["status"] == "no_profile" and uncovered["resolution"] == "uncovered"


@pytest.mark.parametrize("model,expected", [
    ("Q03UDV", 2), ("Q02/Q02H", 2), ("L02/L02-P", 1),
])
def test_instruction_instance_width_uses_the_real_cpu_family(model, expected):
    opcode, operands = ("MOV", ["D0", "D10"]) if expected == 2 else ("LD", ["M0"])
    # No project allocation is available in a knowledge query, so a D/W form
    # answers requires_context while a bit contact answers from the stored rule.
    fact = resolve_instruction_step_width(
        {"opcode": opcode, "operands": operands}, plc_model=model)
    if expected == 2:
        assert fact["status"] == "requires_context"
        assert fact["requirements"] == ["storage_context"]
        return
    assert fact["status"] == "known" and fact["steps"] == expected
    assert fact["plc_model"] == model.upper()
    assert fact["resolution"] == "instruction_instance"


def test_instruction_instance_width_keeps_context_and_operand_gaps_apart():
    from gxw.token_q_encoder import q_project_storage_context

    # An opcode-only operand-dependent target stays that state, not "uncovered".
    unresolved = resolve_instruction_step_width({"opcode": "RST"}, plc_model="FX3U")
    assert unresolved["status"] == "requires_operands"
    unclear = q_project_storage_context(None, cpu="Q03UDV")
    fact = resolve_instruction_step_width(
        {"opcode": "MOV", "operands": ["D0", "D10"]}, plc_model="Q03UDV", context=unclear)
    assert fact["status"] == "requires_context"
    assert fact["resolution"] == "requires_context"


@pytest.mark.parametrize(
    "opcode,manual_id",
    [
        ("DRVA", "fx3_positioning_k"),
        ("DRVI", "fx3_positioning_k"),
        ("DVIT", "fx3_positioning_k"),
        ("PLSV", "fx3_positioning_k"),
        ("ZRN", "fx3_positioning_k"),
        ("TBL", "fx3_programming_r"),
    ],
)
def test_instruction_source_authority_contract_is_data_backed(opcode, manual_id):
    from knowledge.source_authority import (
        authoritative_instruction_manual,
        instruction_source_authority,
    )

    _bundled_index()
    authority = instruction_source_authority(opcode, "FX3U")
    assert authority is not None
    assert authority["manual_id"] == manual_id
    assert authoritative_instruction_manual(opcode, "FX3U") == manual_id

    rows = resolve_instruction_records(
        [{"opcode": opcode, "base_opcode": opcode}],
        plc_model="FX3U",
        task_type="generate",
    )
    assert rows
    assert {row["manual_id"] for row in rows} == {manual_id}
    assert {
        row.get("instruction_source_authority_status") for row in rows
    } == {"authoritative"}


def test_instruction_source_authority_is_owned_only_by_structured_facts():
    import knowledge.core as knowledge_core
    import knowledge.structured_facts as structured_facts

    broad_source = inspect.getsource(knowledge_core._retrieve_uncached)
    direct_source = inspect.getsource(structured_facts.resolve_instruction_records)
    assert "authoritative_instruction_manual" not in broad_source
    assert "instruction_source_authority" not in broad_source
    assert "instruction_source_authority" in direct_source


def test_instruction_source_authority_capability_is_enforced():
    from tools.audit_capability_coverage import audit_coverage

    report = audit_coverage()
    states = {row["id"]: row["state"] for row in report["capabilities"]}
    assert states["instruction_source_authority"] == "enforced"


def test_device_target_extractor_covers_special_indexed_families():
    from knowledge.analysis_router import route_analysis_request

    route = route_analysis_request(
        "TS0 TC1 CS2 CC3 ER4 R5 P6 I7 SM8 SD9 X10 Y11 M12 D13 V14 Z15"
    )
    assert set(route.devices) == {
        "TS0", "TC1", "CS2", "CC3", "ER4", "R5", "P6", "I7",
        "SM8", "SD9", "X10", "Y11", "M12", "D13", "V14", "Z15",
    }


def test_reference_device_target_prefixes_cover_bundled_occurrence_index():
    from knowledge.analysis_router import route_analysis_request
    from plc.device_identity import reference_device_parts

    path = _bundled_index()
    with sqlite3.connect(path) as connection:
        indexed = {
            str(row[0] or "").upper()
            for row in connection.execute(
                "SELECT DISTINCT prefix FROM device_records "
                "WHERE chunk_id IS NOT NULL AND record_type='device' AND prefix <> ''"
            )
        }

    # N is instruction nesting/operand syntax and is intentionally cleaned out,
    # not a runtime device target. Any other new indexed family must be routed.
    indexed.discard("N")
    assert indexed
    for prefix in sorted(indexed):
        device = prefix + "0"
        assert reference_device_parts(device) == (prefix, "0")
        assert device in route_analysis_request(device).devices


def test_structured_fact_targets_carry_extended_device_families():
    targets = structured_fact_targets("查 TS0 TC1 CS2 CC3 ER4 R5 P6 I7")
    assert set(targets["devices"]) == {
        "TS0", "TC1", "CS2", "CC3", "ER4", "R5", "P6", "I7",
    }


def test_router_and_core_share_the_same_device_token_owner():
    import knowledge.analysis_router as analysis_router
    import knowledge.core as knowledge_core
    from plc.device_identity import REFERENCE_DEVICE_TOKEN_RE as DEVICE_TOKEN_RE

    assert analysis_router._DEVICE is DEVICE_TOKEN_RE
    assert knowledge_core._DEVICE_RE is DEVICE_TOKEN_RE


def test_reference_device_vocabulary_covers_actual_index_without_granting_runtime_support():
    from knowledge.analysis_router import route_analysis_request
    from plc.device_identity import reference_device_parts, DEVICE_PREFIXES

    path = _bundled_index()
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT prefix, MIN(device) FROM device_records "
            "WHERE record_type='device' AND chunk_id IS NOT NULL "
            "GROUP BY prefix ORDER BY prefix"
        ).fetchall()

    assert rows
    actual_prefixes = {str(prefix or "").upper() for prefix, _device in rows if prefix}
    # N rows in the source corpus are operand/nesting placeholders and are
    # intentionally not promoted to runtime device targets.
    assert all(reference_device_parts(prefix + '0') == (prefix, '0')
               for prefix in actual_prefixes - {'N'})
    assert "N" not in DEVICE_PREFIXES

    for prefix, device in rows:
        prefix = str(prefix or "").upper()
        if not prefix or prefix == "N":
            continue
        token = str(device or "").upper()
        route = route_analysis_request(f"查 {token} 的软元件定义")
        assert token in route.devices, (prefix, token, route.devices)


def test_device_family_target_coverage_capability_is_enforced():
    from tools.audit_capability_coverage import audit_coverage

    report = audit_coverage()
    states = {row["id"]: row["state"] for row in report["capabilities"]}
    assert states["device_family_target_coverage"] == "enforced"


def test_router_and_core_share_device_token_owner():
    import knowledge.analysis_router as analysis_router
    import knowledge.core as knowledge_core
    from plc.device_identity import REFERENCE_DEVICE_TOKEN_RE as DEVICE_TOKEN_RE

    assert analysis_router._DEVICE is DEVICE_TOKEN_RE
    assert knowledge_core._DEVICE_RE is DEVICE_TOKEN_RE


def test_runtime_device_target_vocabulary_covers_extended_families():
    from knowledge.analysis_router import route_analysis_request

    text = "ER10 SM8000 SD0 TS0 TC0 CS0 CC0 R10 P1 I2"
    route = route_analysis_request(text)
    assert set(route.devices) == {
        "ER10", "SM8000", "SD0", "TS0", "TC0", "CS0", "CC0",
        "R10", "P1", "I2",
    }


def test_runtime_device_target_vocabulary_rejects_constants_and_placeholders():
    from knowledge.analysis_router import route_analysis_request

    route = route_analysis_request("MOV K10 D0，常数 H100、浮点 E1、操作数 N1")
    assert route.devices == ("D0",)


def test_structured_fact_targets_use_full_runtime_device_vocabulary():
    targets = structured_fact_targets(
        "ER10 SM8000 SD0 TS0 TC0 CS0 CC0 R10 P1 I2 K10 H100 E1 N1"
    )
    assert set(targets["devices"]) == {
        "ER10", "SM8000", "SD0", "TS0", "TC0", "CS0", "CC0",
        "R10", "P1", "I2",
    }


def test_exact_device_occurrence_is_not_promoted_to_a_definition():
    _bundled_index()
    rows = resolve_device_records(["M8029"], plc_model="FX3U", task_type="analysis")
    assert rows
    assert all(row["structured_lookup"] is True for row in rows)
    assert all(row["structured_fact_kind"] == "device_occurrence" for row in rows)
    assert all(row["device_definition_status"] == "unverified" for row in rows)
    assert {row["structured_fact_target"] for row in rows} == {"M8029"}


def test_device_context_keeps_definition_unresolved_when_only_mentions_exist():
    _bundled_index()
    import knowledge.retriever as retriever

    context = retriever.build_knowledge_context(
        KnowledgeQuery("M8029"), plc_model="FX3U",
        task_type="analysis", top_k=5, char_budget=7000,
    )
    report = context.manifest["fact_coverage"]
    requirement = next(
        row for row in report["requirements"]
        if row["kind"] == "device"
        and row["target"] == "M8029"
        and row["dimension"] == "definition"
    )
    assert requirement["status"] == "unresolved"
    assert requirement["source_ids"] == []


def test_device_lookup_uses_canonical_device_identity_owner():
    _bundled_index()

    canonical = resolve_device_records(
        ["M8029"], plc_model="FX3U", task_type="analysis",
    )
    aliased = resolve_device_records(
        ["M08029"], plc_model="FX3U", task_type="analysis",
    )

    assert canonical
    assert aliased
    assert [row["id"] for row in aliased] == [row["id"] for row in canonical]
    assert {row["structured_fact_target"] for row in aliased} == {"M8029"}
    assert {row["structured_fact_requested_target"] for row in aliased} == {"M08029"}


def test_device_aliases_do_not_duplicate_structured_records():
    _bundled_index()
    rows = resolve_device_records(
        ["M08029", "M8029"],
        plc_model="FX3U",
        task_type="analysis",
    )
    assert rows
    assert len({row["id"] for row in rows}) == len(rows)
    assert {row["structured_fact_target"] for row in rows} == {"M8029"}


def test_device_canonical_identity_capability_is_enforced():
    from tools.audit_capability_coverage import audit_coverage

    report = audit_coverage()
    states = {row["id"]: row["state"] for row in report["capabilities"]}
    assert states["device_canonical_identity"] == "enforced"


def test_exact_error_is_resolved_from_error_records():
    path = _bundled_index()
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT error_code FROM error_records WHERE error_code_norm <> '0000' "
            "AND chunk_id IN (SELECT id FROM chunks WHERE plc_models LIKE '%FX3U%') "
            "ORDER BY pdf_page,id LIMIT 1"
        ).fetchone()
    assert row is not None
    code = str(row[0])
    rows = resolve_error_records([code], plc_model="FX3U", task_type="debug")
    assert rows
    assert all(item["structured_lookup"] is True for item in rows)
    assert all(item["structured_fact_kind"] == "error" for item in rows)


def test_fact_aware_row_api_keeps_exact_instruction_out_of_broad_retrieval(monkeypatch):
    _bundled_index()
    import knowledge.retriever as retriever

    def broad_lookup(*args, **kwargs):
        pytest.fail("exact-only SFTL manual lookup must not enter broad RAG")

    monkeypatch.setattr(retriever, "retrieve_knowledge", broad_lookup)
    rows = retriever.retrieve_fact_aware_knowledge(
        "SFTL", plc_model="FX3U", task_type="analysis", top_k=5, char_budget=7000,
    )
    assert rows
    assert rows[0]["structured_lookup"] is True
    assert rows[0]["structured_fact_kind"] == "instruction"
    assert rows[0]["structured_fact_target"] == "SFTL"
    assert rows[0]["structured_text_compacted"] is True
    assert rows[0]["instruction_contract"]["opcode"] == "SFTL"
    assert "OPERAND_SEMANTICS:" in rows[0]["text"]
    assert "STEP_WIDTH:" not in rows[0]["text"]


def test_application_runtime_cannot_bypass_fact_aware_retrieval():
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    offenders = []
    for folder in ("src/application", "src/agent_runtime", "src/integrations"):
        for source_file in (root / folder).rglob("*.py"):
            source_text = source_file.read_text(encoding="utf-8")
            if "from knowledge.retriever import retrieve_knowledge" in source_text:
                offenders.append(source_file.relative_to(root).as_posix())
    assert offenders == []


def test_agent_manual_search_uses_fact_aware_entry_point():
    from agent_runtime import plc_tools

    source_text = inspect.getsource(plc_tools._search_plc_manual)
    assert "retrieve_fact_aware_knowledge" in source_text
    assert "retrieve_knowledge(" not in source_text


@pytest.mark.parametrize('query,expected',[
    ('FX3U 的状态继电器是什么？',['S']),('L02 的链接特殊继电器是什么？',['SB']),
    ('Q03UDV retentive timer definition',['ST']),('FX3U extension file register definition',['ER']),
    ('FX3U input/output relay definition',['X','Y']),('请解释 S 软元件',['S']),
    ('S0 是什么？',[]),('PLSY 参数 S1/S2 是状态继电器地址吗？',['S']),
    ('PLSY S1/S2 operand purpose',[]),
])
def test_family_names_and_symbols_have_explicit_targets(query,expected):
    from knowledge.device_evidence import family_targets
    assert family_targets(query)==expected


def test_family_source_does_not_certify_an_individual_address_or_permission(monkeypatch):
    import knowledge.structured_facts as facts
    from knowledge.device_evidence import resolve_section_records
    connection=sqlite3.connect(':memory:');connection.row_factory=sqlite3.Row
    connection.execute('CREATE TABLE device_sections(id TEXT,family TEXT,manual_id TEXT,manual_number TEXT,revision TEXT,manual_type TEXT,source TEXT,pdf_page INTEGER,page TEXT,section TEXT,plc_models TEXT,text TEXT,table_binding_status TEXT,source_chunk_id TEXT,source_excerpt_json TEXT)')
    connection.execute("INSERT INTO device_sections VALUES('source','S','fixture','Fixture','R','programming','Fixture',1,'1','State relay (S)','FX3U','State relays are used with STL.','complete_source_section','1','{}')")
    schema={'device_sections':{'name':'device_sections','columns':set()}}
    family=resolve_section_records(connection,schema,['S'],plc_model='FX3U')[0]
    address=resolve_section_records(connection,schema,['S0'],plc_model='FX3U')[0]
    assert family['fact_kind']=='device_family' and family['device_definition_status']=='original_source'
    assert address['fact_kind']=='device_occurrence' and address['device_definition_status']=='unverified'
    assert address['source_applicability']['individual_address_support']=='unverified'
    from knowledge.retriever import _covered_structured_kinds
    assert _covered_structured_kinds({'devices':['S0']},[address])==frozenset()
    assert _covered_structured_kinds({'device_families':['S']},[family])==frozenset({'device_family'})
    assert not resolve_section_records(connection,schema,['S'],plc_model='FX3G')
    connection.close()


def test_public_manual_search_retains_whole_evidence_and_verification_metadata(monkeypatch):
    import knowledge.retriever as retriever
    from agent_runtime.plc_tools import ToolContext,_search_plc_manual
    row={'id':'witness','manual_id':'fixture','text':'x'*2000+'\n*1. Complete CPU-bound note.',
         'device_definition_status':'conditions_missing','fact_kind':'device','fact_target':'SM605',
         'source_applicability':{'supported_models':['Q03UDE'],'conditional_models':['Q03UDE']},
         'source_evidence':[{'manual_number':'Fixture','revision':'AF','pdf_pages':[1]}],
         'content_status':'complete','table_binding_status':'cpu_row_with_notes'}
    monkeypatch.setattr(retriever,'retrieve_fact_aware_knowledge',lambda *a,**kw:[row])
    public=_search_plc_manual(ToolContext(project={'plc_model':'Q03UDE'}),{'query':'SM605','debug':True})
    result=public['results'][0]
    assert result['text']==row['text'] and result['text'].endswith('CPU-bound note.')
    for key in ('device_definition_status','fact_kind','fact_target','source_applicability','source_evidence','content_status','table_binding_status'):
        assert result[key]==row[key]
    result['source_applicability']['supported_models'].append('WRONG')
    assert row['source_applicability']['supported_models']==['Q03UDE']
    assert public['evidence_delivery']['omitted_evidence_is_not_unsupported'] is True
    assert result['role']=='technical_reference'


def test_relevance_selection_retains_long_records_and_complete_notes():
    rows=[{'id':'large','text':'x'*2200+'\n*3. CPU condition.'}, {'id':'small','text':'Complete source.'}]
    selected=core._select_ranked(rows,5)
    assert selected == rows
    assert selected[0]['text'].endswith('*3. CPU condition.')


@pytest.mark.parametrize('address,text,context,expected',[
    ('T32','T0 to T199','',['T0 to T199']),('T200','T0 to T199','',[]),
    ('X8','X000 to X017','numbers are octal',[]),('X10','X000 to X017','numbers are octal',['X000 to X017']),
    ('B1F','B0 to B20','numbers are hexadecimal',['B0 to B20']),
])
def test_source_range_location_does_not_admit_outside_or_invalid_radix_addresses(address,text,context,expected):
    from knowledge.device_evidence import source_range_matches
    assert source_range_matches(text,address,context=context)==expected


def test_broad_reference_projects_complete_cpu_columns_without_changing_source_rank():
    from knowledge.device_evidence import project_source_record
    connection=sqlite3.connect(':memory:');connection.row_factory=sqlite3.Row
    connection.execute('CREATE TABLE chunks(id TEXT,pdf_page INTEGER,pdf_page_end INTEGER)')
    connection.execute("INSERT INTO chunks VALUES('parent',828,828)")
    connection.execute('CREATE TABLE device_sections(id TEXT,manual_id TEXT,pdf_page INTEGER,section TEXT,plc_models TEXT,text TEXT,table_binding_status TEXT,source_excerpt_json TEXT)')
    connection.executemany('INSERT INTO device_sections VALUES(?,?,?,?,?,?,?,?)',[
        ('FX3S','manual',828,'Special relay','FX3S','M8028: 100 ms/10 ms timer changeover','cpu_column_with_notes','{}'),
        ('FX3U','manual',828,'Special relay','FX3U','M8028: Interrupt permission during FROM/TO execution','cpu_column_with_notes','{}')])
    source={'id':'parent','manual_id':'manual','source':'Original manual','pdf_page':828,
            'text':'Mixed CPU source text','table_binding_status':'cpu_columns_require_projection','rrf_score':0.25}
    projected=project_source_record(connection,{'device_sections':{'name':'device_sections'}},source,plc_model='FX3U')
    assert projected['id']=='parent' and projected['rrf_score']==0.25
    assert 'FROM/TO' in projected['text'] and 'timer changeover' not in projected['text']
    assert projected['table_binding_status']=='cpu_column_projection' and projected['content_status']=='complete'
    assert source['text']=='Mixed CPU source text'
    connection.close()


def test_targeted_generation_context_does_not_call_broad_retriever(monkeypatch):
    _bundled_index()
    import knowledge.retriever as retriever

    def broad_lookup(*args, **kwargs):
        pytest.fail("explicit SFTL fact lookup must not enter broad RAG")

    monkeypatch.setattr(retriever, "retrieve_knowledge", broad_lookup)
    targets = {
        "version": "structured-facts-v1",
        "instructions": [{"opcode": "SFTL", "base_opcode": "SFTL"}],
        "devices": [],
        "errors": [],
    }
    query = KnowledgeQuery(
        "SFTL",
        precompiled=True,
        metadata={
            "structured_fact_mode": "direct",
            "structured_fact_targets": targets,
            "instruction_fact_mode": "targeted",
            "instruction_fact_targets": targets["instructions"],
        },
    )
    context = retriever.build_knowledge_context(
        query, plc_model="FX3U", task_type="generate", top_k=5, char_budget=7000,
    )
    assert context
    assert context.manifest["structured_facts"]["residual_retrieval"] is False
    assert context.manifest["instruction_facts"]["retrieval_mode"] == "structured_direct"
    assert "SFTL" in context


def test_explicit_targets_are_removed_before_residual_retrieval():
    targets = {
        "instructions": [{"opcode": "SFTL", "base_opcode": "SFTL"}],
        "devices": ["M8029"],
        "errors": ["1234H"],
    }
    residual = without_structured_targets(
        "FX3U SFTL 配合 M8029，错误码 1234H 时查手册", targets,
    )
    assert "SFTL" not in residual
    assert "M8029" not in residual
    assert "1234H" not in residual
    assert "查手册" in residual


def test_settled_io_bindings_do_not_become_structured_fact_targets():
    spec = {
        "io_bindings": [
            {"kind": "X", "address": "X0", "source_parameter_id": "start"},
            {"kind": "Y", "address": "Y0", "source_parameter_id": "motor"},
        ],
        "io_table": [
            {"kind": "X", "address": "X0"},
            {"kind": "Y", "address": "Y0"},
        ],
        "selected_approach": {
            "generation_contract": {
                "required_opcodes": ["WSFL"],
                "required_devices": ["M0"],
            }
        },
    }
    targets = structured_fact_targets("", spec)
    assert targets["instructions"][0]["opcode"] == "WSFL"
    assert targets["devices"] == []
    assert targets["errors"] == []


def test_confirmed_generation_does_not_lookup_required_devices_as_manual_facts():
    _bundled_index()
    from application.confirmed_generation_context import build_confirmed_generation_context

    spec = {
        "summary": "字寄存器队列",
        "selected_approach": {
            "generation_contract": {
                "required_opcodes": ["WSFL"],
                "required_devices": ["M0", "D10", "D20"],
            }
        },
    }
    context = build_confirmed_generation_context(spec, "FX3U")
    receipt = context.handoff["structured_facts"]
    assert receipt["targets"]["instructions"][0]["opcode"] == "WSFL"
    assert receipt["targets"]["devices"] == []
    assert receipt["targets"]["errors"] == []


def test_confirmed_targets_include_selected_opcode_without_broad_search_text():
    spec = {"selected_approach": {"generation_contract": {"required_opcodes": ["WSFL"]}}}
    targets = structured_fact_targets("", spec)
    assert targets["instructions"][0]["opcode"] == "WSFL"
    assert targets["devices"] == []
    assert targets["errors"] == []


def test_compact_row_view_does_not_mutate_full_manual_backed_record():
    from knowledge.structured_facts import compact_structured_fact_record

    rows = resolve_instruction_records(
        [{"opcode": "SFTL", "base_opcode": "SFTL"}],
        plc_model="FX3U",
        task_type="analysis",
    )
    assert rows
    full = rows[0]
    compact = compact_structured_fact_record(full)
    assert compact is not full
    assert compact["instruction_contract"] == full["instruction_contract"]
    assert len(compact["text"]) < len(full["text"])
    assert "OPERAND_SEMANTICS:" in compact["text"]
    assert "STEP_WIDTH:" not in compact["text"]
    assert "INSTRUCTION_CONTRACT:" not in compact["text"]
    assert full["text"].startswith("[STRUCTURED INSTRUCTION RECORD]")


def test_instruction_contract_delivery_has_one_structured_owner():
    import application.compact_protocol as compact_protocol
    import application.confirmed_generation_context as confirmed_context
    import knowledge.structured_facts as structured_facts

    assert hasattr(structured_facts, "resolve_instruction_contract")
    assert not hasattr(confirmed_context, "selected_instruction_capability_prompt")
    assert compact_protocol.compact_capability_prompt(
        "FX3U",
        {"selected_approach": {"generation_contract": {"required_opcodes": ["MOV"]}}},
    ) == ""



def test_source_subquery_prefilters_instruction_chunks_before_candidate_limit():
    from knowledge.scope import source_subquery

    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE chunks (id TEXT, manual_type TEXT, chunk_type TEXT, manual_id TEXT)"
        )
        connection.execute("CREATE TABLE device_records (chunk_id TEXT, evidence_kind TEXT)")
        connection.execute("CREATE TABLE error_records (chunk_id TEXT)")
        connection.executemany(
            "INSERT INTO chunks VALUES (?,?,?,?)",
            [
                ("or", "programming", "instruction", "manual"),
                ("device-owner", "structured_device", "prose", "device-manual"),
                ("device-mention", "programming", "prose", "manual"),
                ("error-owner", "programming", "error", "error-manual"),
                ("general", "programming", "prose", "manual"),
            ],
        )
        connection.execute("INSERT INTO device_records VALUES ('device-owner','definition')")
        connection.execute("INSERT INTO device_records VALUES ('device-mention','occurrence')")
        connection.execute("INSERT INTO error_records VALUES ('error-owner')")
        schema = {
            "chunks": {
                "name": "chunks",
                "columns": ("id", "manual_type", "chunk_type", "manual_id"),
            },
            "device_records": {
                "name": "device_records",
                "columns": ("chunk_id", "evidence_kind"),
            },
            "error_records": {
                "name": "error_records",
                "columns": ("chunk_id",),
            },
        }

        sql, values = source_subquery(connection, schema, ("fact",))
        assert {row[0] for row in connection.execute(sql, values)} == {
            "or", "device-owner", "device-mention", "error-owner", "general",
        }

        sql, values = source_subquery(
            connection, schema, ("fact",),
            exclude_chunk_types=("instruction",),
            exclude_structured_kinds=("device", "error"),
        )
        assert {row[0] for row in connection.execute(sql, values)} == {"device-mention", "general"}
    finally:
        connection.close()


def test_exact_instruction_residual_broad_retrieval_uses_instruction_prefilter(monkeypatch):
    _bundled_index()
    import knowledge.retriever as retriever

    targets = {
        "version": "structured-facts-v2-contract-merged",
        "instructions": [{
            "opcode": "SFTL",
            "base_opcode": "SFTL",
            "operands": ["M100", "M200", "K20", "K1"],
            "instance_source": "generation_contract",
        }],
        "devices": [],
        "errors": [],
    }
    query = KnowledgeQuery(
        "SFTL M100 M200 K20 K1 timer scan cycle",
        precompiled=True,
        metadata={"structured_fact_targets": targets},
    )
    calls = []

    def broad_lookup(*args, **kwargs):
        calls.append((args, kwargs))
        assert kwargs.get("exclude_chunk_types") == ("instruction",)
        assert kwargs.get("exclude_structured_kinds") == ()
        return []

    monkeypatch.setattr(retriever, "retrieve_knowledge", broad_lookup)
    context = retriever.build_knowledge_context(
        query, plc_model="FX3U", task_type="generate", top_k=5, char_budget=7000,
    )
    assert len(calls) == 1
    receipt = context.manifest["structured_facts"]
    assert receipt["direct_covered_kinds"] == ["instruction"]
    assert receipt["residual_retrieval"] is True
    assert receipt["residual_pre_filters"] == {
        "exclude_chunk_types": ["instruction"],
    }




def test_device_mentions_keep_device_lane_open_while_defined_error_lane_closes(monkeypatch):
    _bundled_index()
    import knowledge.retriever as retriever

    targets = {
        "version": "structured-facts-v2-contract-merged",
        "instructions": [],
        "devices": ["M8029"],
        "errors": ["6105"],
    }
    query = KnowledgeQuery(
        "M8029 6105 explain scan cycle",
        precompiled=True,
        metadata={"structured_fact_targets": targets},
    )
    calls = []

    def broad_lookup(*args, **kwargs):
        calls.append((args, kwargs))
        assert kwargs.get("exclude_chunk_types") == ()
        assert kwargs.get("exclude_structured_kinds") == ("error",)
        assert "M8029" in args[0]
        return []

    monkeypatch.setattr(retriever, "retrieve_knowledge", broad_lookup)
    context = retriever.build_knowledge_context(
        query, plc_model="FX3U", task_type="analysis", top_k=5, char_budget=10000,
    )
    assert len(calls) == 1
    receipt = context.manifest["structured_facts"]
    assert receipt["direct_covered_kinds"] == ["error"]
    assert receipt["residual_pre_filters"] == {
        "exclude_structured_kinds": ["error"],
    }


def test_unresolved_targets_keep_general_context_without_neighbouring_diagnostic_definitions(monkeypatch):
    import knowledge.retriever as retriever
    import knowledge.structured_facts as facts

    targets = {
        "version": "structured-facts-v2-contract-merged",
        "instructions": [{"opcode": "NOTREAL", "base_opcode": "NOTREAL"}],
        "devices": ["M999999"],
        "errors": ["FFFF"],
    }
    query = KnowledgeQuery(
        "NOTREAL M999999 FFFF explain scan cycle",
        precompiled=True,
        metadata={"structured_fact_targets": targets},
    )
    monkeypatch.setattr(facts, "resolve_instruction_records", lambda *a, **k: [])
    monkeypatch.setattr(facts, "resolve_device_records", lambda *a, **k: [])
    monkeypatch.setattr(facts, "resolve_error_records", lambda *a, **k: [])
    seen = []

    def broad_lookup(*args, **kwargs):
        seen.append(kwargs)
        return []

    monkeypatch.setattr(retriever, "retrieve_knowledge", broad_lookup)
    context = retriever.build_knowledge_context(
        query, plc_model="FX3U", task_type="analysis", top_k=5, char_budget=10000,
    )
    assert len(seen) == 1
    assert seen[0].get("exclude_chunk_types") == ()
    assert seen[0].get("exclude_structured_kinds") == ("error",)
    receipt = context.manifest["structured_facts"]
    assert receipt["direct_covered_kinds"] == []
    assert receipt["residual_pre_filters"] == {"exclude_structured_kinds": ["error"]}


def test_broad_retrieval_does_not_own_exact_plc_fact_tables():
    import inspect
    import knowledge.core as knowledge_core

    broad = inspect.getsource(knowledge_core._retrieve_uncached)
    routed = inspect.getsource(knowledge_core._broad_metadata_references)
    for forbidden in (
        "instruction_aliases",
        "error_records",
        "device_records",
        "_manual_instruction_references",
        "authoritative_instruction_manual",
        "structured_instruction",
        "structured_error",
    ):
        assert forbidden not in broad
        assert forbidden not in routed


@pytest.mark.parametrize('model,first,clock', [
    ('Q00J', 'SM402', 'SM412'), ('Q03UDV', 'SM402', 'SM412'),
    ('L02', 'SM402', 'SM412'), ('FX1N', 'M8002', 'M8013'),
    ('FX2C', 'M8002', 'M8013'), ('FX3U', 'M8002', 'M8013'),
])
@pytest.mark.parametrize('purpose', ['FIRST_SCAN', 'CLOCK'])
def test_process_questions_deliver_original_cpu_scoped_device_definition(model, first, clock, purpose):
    from knowledge.structured_facts import structured_fact_targets, resolve_process_records, _runtime
    query = ('ST。CPU 从 STOP 进入 RUN 后首次扫描的特殊继电器。' if purpose == 'FIRST_SCAN'
             else 'ST。使用 CPU 管理的 1 秒时钟特殊继电器，每 0.5 秒切换电平。')
    targets = structured_fact_targets(query, plc_model=model)
    assert not targets['instructions']
    records = resolve_process_records(targets['process'], plc_model=model)
    assert len(records) == 1
    record, = records
    assert record['fact_target'] == purpose
    assert (first if purpose == 'FIRST_SCAN' else clock) in record['text']
    assert record['manual_number'] and record['revision'] and record['pdf_page']
    from knowledge.scope import row_in_scope
    index, _, connection, schema, _ = _runtime()
    source = connection.execute(f"SELECT * FROM {index.quote_identifier(schema['chunks']['name'])} WHERE id=?", (record['id'],)).fetchone()
    assert record['text'] == source['text']
    assert row_in_scope(source, model, 'generate')


@pytest.mark.parametrize('query', ['STOP 指令', 'ST instruction', 'RUN', '查询 `STOP`'])
def test_explicit_state_named_instruction_lookup_is_preserved(query):
    from knowledge.structured_facts import structured_fact_targets
    assert structured_fact_targets(query)['instructions']


@pytest.mark.parametrize('query', ['不要使用首次扫描特殊继电器，只用 X0 控制 Y0。',
                                  '不需要时钟，只用 X0 控制 Y0。', 'RTC clock setting'])
def test_process_fact_questions_do_not_promote_negations_or_rtc(query):
    from knowledge.structured_facts import structured_fact_targets
    assert not structured_fact_targets(query, plc_model='Q00J')['process']


def test_process_fact_lookup_does_not_inherit_a_different_cpu():
    from knowledge.structured_facts import resolve_process_records
    assert not resolve_process_records([{'target': 'CLOCK', 'basis': 'explicit_process_question'}], plc_model='UNKNOWN_CPU')


@pytest.mark.parametrize('family,maximum,definition', [
    ('X','1777','Provides the CPU module with commands'),
    ('Y','1777','Outputs program control results'),
    ('M','7679','auxiliary relay inside the CPU module'),
    ('S','4095','process stepping control'),
    ('T','1023','measurement starts when the timer coil'),
    ('C','1023','counts number of rises'),
    ('D','7999','storing numerical data'),
    ('SM','8999','internal relays with fixed specifications'),
    ('SD','8999','internal register with fixed specifications'),
])
@pytest.mark.parametrize('address_kind', ['family','zero','maximum'])
def test_fx5u_family_and_boundary_questions_deliver_official_type_evidence(
        family, maximum, definition, address_kind):
    from agent_runtime.plc_tools import ToolContext, _search_plc_manual
    from knowledge.structured_facts import resolve_device_family_records

    target=family if address_kind=='family' else family+('0' if address_kind=='zero' else maximum)
    response=_search_plc_manual(ToolContext(project={'plc_model':'FX5U'}), {
        'query':f'FX5U 的 {target} 是什么软元件？请给出该型号的官方定义、用途及限制。', 'top_k':5,'debug':True})
    row=response['results'][0]
    assert row['manual_number']=='JY997D55401' and row['revision']=='AG'
    assert any(definition.casefold() in evidence['text'].casefold()
               and evidence.get('manual_number')=='JY997D55401' for evidence in response['results'])
    assert row['content_status']=='complete' and 'FX5U' in row['plc_models'].split(',')
    if address_kind!='family':
        assert row['device_definition_status']=='unverified'
        assert row['source_applicability']['individual_address_support']=='unverified'
    other=resolve_device_family_records([family], plc_model='FX3U')
    assert all(record.get('manual_id')!='fx5_application_ag' for record in other)


@pytest.mark.parametrize('context,expected', [
    ({'cpu_full_model':'FX3S-20MR/ES'}, {'hardware_variant':'FX3S-20MR/ES'}),
    ({'firmware':'1.20'}, {'cpu_version':'1.20'}),
    ({'firmware':'unconfirmed 1.20'}, {}),
    ({'firmware':'1.20','cpu_version':'2.00'}, {}),
    ({'cpu_full_model':'FX3S-20MR/ES','hardware_variant':'FX3S-30MR/ES'}, {}),
])
def test_confirmed_hardware_fact_projection_is_idempotent_and_conflicts_stay_unknown(context,expected):
    from plc.device_definitions import device_fact_context
    before = dict(context)
    actual = device_fact_context(context)
    assert actual == expected and device_fact_context(actual) == expected
    assert context == before


def test_the_knowledge_query_validates_the_whole_target_identity():
    from knowledge.structured_facts import resolve_instruction_step_width
    from plc.instruction_steps import instruction_step_width

    # A merged spelling has to name a saved menu option: every part being a real
    # CPU is not enough, and a stray slash is not a spelling of one. The query
    # answers exactly what Core answers, including the reason.
    for target in ("FX3U/FX1S", "FX1S/", "/FX3U", "FX3U//FX3UC"):
        core = instruction_step_width("END", [], plc_model=target)
        queried = resolve_instruction_step_width("END", plc_model=target)
        assert core.status == queried["status"] == "unknown_cpu", target
        assert queried["steps"] is None and queried["reason"] == core.reason, target
        assert queried["requirements"] == ["plc_model"], target
    # A real menu option still answers in both, spacing included.
    for target in ("FX3U", "FX3U/FX3UC", "FX1S", "Q02 / Q02H"):
        core = instruction_step_width("END", [], plc_model=target)
        queried = resolve_instruction_step_width("END", plc_model=target)
        assert core.steps is not None, target
        assert (queried["status"], queried["steps"]) == (core.status, core.steps), target
    # An unrecognised name is the same unknown CPU on both sides, same wording.
    core = instruction_step_width("END", [], plc_model="FX3X")
    queried = resolve_instruction_step_width("END", plc_model="FX3X")
    assert queried["status"] == core.status == "unknown_cpu"
    assert queried["reason"] == core.reason


def test_the_mnemonic_question_uses_the_same_rules_as_the_instance():
    """An opcode-only query must not claim a width that the operands decide."""
    from plc.instruction_steps import instruction_step_width, mnemonic_step_width

    # MC is operand-dependent on the FX3 models: the 7.16 device table gives the
    # nesting operand three words up to M3583 and four from M3584, so the mnemonic
    # alone has no width even though the native samples agreed on one number.
    assert instruction_step_width("MC", ["N0", "M0"], plc_model="FX3U").steps == 3
    assert instruction_step_width("MC", ["N0", "M3584"], plc_model="FX3U").steps == 4
    mnemonic = mnemonic_step_width("MC", plc_model="FX3U")
    assert (mnemonic.status, mnemonic.steps) == ("requires_operands", None)
    queried = resolve_instruction_step_width("MC", plc_model="FX3U")
    assert (queried["status"], queried["steps"], queried["resolution"]) == (
        "requires_operands", None, "requires_operands")

    # An instruction that takes no operands does have a width of its own.
    for opcode, steps in (("END", 1), ("NOP", 1), ("RET", 1)):
        assert mnemonic_step_width(opcode, plc_model="FX3U").steps == steps, opcode
        assert resolve_instruction_step_width(opcode, plc_model="FX3U")["steps"] == steps

    # LD is one word for a low M contact and two from M8256 (the 7.16 device
    # table), so its mnemonic has no single width either; a mnemonic nobody
    # records is missing coverage rather than an operand question.
    assert instruction_step_width("LD", ["M0"], plc_model="FX3U").steps == 1
    assert instruction_step_width("LD", ["M8256"], plc_model="FX3U").steps == 2
    assert mnemonic_step_width("LD", plc_model="FX3U").status == "requires_operands"
    assert mnemonic_step_width("MOV", plc_model="FX3U").status == "requires_operands"
    assert mnemonic_step_width("NOT_A_MNEMONIC", plc_model="FX3U").status == "no_profile"
    assert resolve_instruction_step_width("NOT_A_MNEMONIC",
                                          plc_model="FX3U")["status"] == "no_profile"


def test_a_merged_q_target_keeps_its_identity_on_a_mnemonic_query():
    from gxw.token_q_encoder import QStorageContext
    from plc.instruction_steps import instruction_step_width

    # The project records Q02 and the target is the Q02/Q02H menu option. Splitting
    # the target into members would compare the saved CPU with the other member and
    # call it a mismatch, so Core answers the whole identity at once.
    saved = QStorageContext(cpu="Q02", parameter_record=None, ranges=())
    for opcode in ("NOP", "END"):
        core = instruction_step_width(opcode, [], plc_model="Q02/Q02H", context=saved)
        queried = resolve_instruction_step_width(opcode, plc_model="Q02/Q02H", context=saved)
        assert core.status == "known", (opcode, core.reason)
        assert (queried["status"], queried["steps"]) == ("known", core.steps), opcode
    # An operand-dependent mnemonic still asks for its operands, one answer only.
    assert resolve_instruction_step_width("MOV", plc_model="Q02/Q02H",
                                          context=saved)["status"] == "requires_operands"
    # A saved CPU that is none of the members stays a context mismatch.
    other = QStorageContext(cpu="Q03UDV", parameter_record=None, ranges=())
    mismatch = resolve_instruction_step_width("NOP", plc_model="Q02/Q02H", context=other)
    assert (mismatch["status"], mismatch["requirements"]) == ("requires_context",
                                                              ["storage_context"])


def test_a_zero_operand_rule_answers_the_mnemonic_query_like_an_instance():
    """A rule that takes no operands must not be reported as needing operands."""
    from plc.instruction_steps import instruction_step_width, mnemonic_step_width

    # NEXT is owned by a rule whose arity is zero, so the mnemonic query evaluates
    # that form with no operands instead of calling the whole rule operand-dependent.
    for opcode, steps in (("NEXT", 1), ("NOP", 1), ("END", 1), ("RET", 1)):
        instance = instruction_step_width(opcode, [], plc_model="FX3U")
        mnemonic = mnemonic_step_width(opcode, plc_model="FX3U")
        assert (instance.status, instance.steps) == ("known", steps), opcode
        assert (mnemonic.status, mnemonic.steps) == ("known", steps), opcode
        assert resolve_instruction_step_width(opcode, plc_model="FX3U")["steps"] == steps
    # MNEMONICS whose forms all take operands keep asking for them.
    for opcode in ("MC", "MOV", "SFTL"):
        assert mnemonic_step_width(opcode, plc_model="FX3U").status == "requires_operands", opcode


def test_the_published_step_width_resource_keeps_the_local_semantics():
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    target = root / "resources/instructions/mitsubishi/fx3u_step_widths.json"
    spec = importlib.util.spec_from_file_location(
        "check_step_width_parity", root / "scripts/check_step_width_resource_parity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        published = module._load_source("git:HEAD:" + module.RESOURCE, str(root))
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover - no checkout
        pytest.skip("the published copy needs a Git checkout")
    local = module._load_source(str(target))
    assert module._differences(module._normalise(local),
                               module._normalise(published)) == []
    # The native encoding map and every declared scope are compared as well.
    assert local["opcodes"] == published["opcodes"]
    published_scopes = module._scopes(published)
    local_scopes = module._scopes(local)
    for key, value in published_scopes.items():
        assert local_scopes.get(key) == value, key


def test_adjudicated_ordering_never_drops_another_material_or_target():
    """Order by the ruling; keep every row and never lend a ruling to another target."""
    from knowledge.structured_facts import _adjudicated_rows

    rows = [
        {"id": "1", "instruction_opcode": "DRVA", "manual_id": "fx3_programming_r",
         "revision": "R", "pdf_page": 562},
        {"id": "2", "instruction_opcode": "DRVA", "manual_id": "fx3_positioning_k",
         "revision": "K", "pdf_page": 172},
        {"id": "3", "instruction_opcode": "MOV", "manual_id": "fx3_programming_r",
         "revision": "R", "pdf_page": 100},
        {"id": "4", "instruction_opcode": "MOV", "manual_id": "fx3_positioning_k",
         "revision": "K", "pdf_page": 100},
    ]
    ordered, decided = _adjudicated_rows(
        rows,
        [{"opcode": "DRVA", "base_opcode": "DRVA"}, {"opcode": "MOV", "base_opcode": "MOV"}],
        "FX3U",
    )
    assert decided is True
    assert {row["id"] for row in ordered} == {"1", "2", "3", "4"}
    assert ordered[0]["id"] == "2"
    assert ordered[0]["authority_decision"]["opcode"] == "DRVA"
    # MOV has no audited rule: its rows keep their own state, with no borrowed ruling.
    for row in ordered:
        if row["instruction_opcode"] == "MOV":
            assert "authority_decision" not in row
    # The losing material's DRVA row survives with no ruling attached.
    loser = next(row for row in ordered if row["id"] == "1")
    assert "authority_decision" not in loser


def test_the_adjudication_path_invents_no_chapter_claim():
    """A chapter flag that exists was earned by a section lookup; never add one."""
    from knowledge.structured_facts import _adjudicated_rows

    rows = [
        {"id": "1", "instruction_opcode": "DRVA", "manual_id": "fx3_positioning_k",
         "revision": "K", "pdf_page": 172},
        {"id": "2", "instruction_opcode": "MOV", "manual_id": "fx3_programming_r",
         "revision": "R", "pdf_page": 100, "compiled_source_chapter": True},
    ]
    ordered, _decided = _adjudicated_rows(
        rows, [{"opcode": "DRVA", "base_opcode": "DRVA"}, {"opcode": "MOV", "base_opcode": "MOV"}],
        "FX3U")
    owner = next(row for row in ordered if row["id"] == "1")
    assert "compiled_source_chapter" not in owner
    untouched = next(row for row in ordered if row["id"] == "2")
    assert untouched["compiled_source_chapter"] is True




def test_exact_merge_keeps_conflicting_claims_and_rebinds_each_source_dependency():
    from knowledge.structured_facts import merge_exact_records, authored_packet
    def record(identity, value, catalogue=False):
        facts = [{'id': 'claim', 'dimension': 'execution.edge', 'status': 'source_verified',
                  'value': {'edge': value}, 'scope': {'models': ['FX3U'], 'forms': ['ANDP'], 'languages': ['ladder']},
                  'sources': [{'source': identity}]},
                 {'id': 'dependent', 'dimension': 'execution.state', 'status': 'source_verified',
                  'value': {'owner': identity}, 'depends_on': ['claim']}]
        packet = {'content_origin': 'authored_fact', 'kind': 'instruction', 'target': 'ANDP',
                  'scope': {'models': ['FX3U'], 'forms': ['ANDP'], 'languages': ['ladder']},
                  'value': {'facts': [] if catalogue else facts, 'catalogue_only': catalogue},
                  'gaps': ['catalogue_placeholder'] if catalogue else []}
        return {'id': identity, 'fact_kind': 'instruction', 'fact_target': 'ANDP', 'text': json.dumps(packet)}
    rows = merge_exact_records([record('directory', 'rising', True), record('source-a', 'rising'),
                                record('source-b', 'falling')], plc_model='FX3U')
    assert len(rows) == 1
    packet = authored_packet(rows[0])
    facts = packet['value']['facts']
    assert packet['gaps'] == [] and not packet['value'].get('catalogue_only')
    assert len({fact['id'] for fact in facts}) == len(facts) == 4
    claims = [fact for fact in facts if fact['dimension'] == 'execution.edge']
    assert {fact['value']['edge'] for fact in claims} == {'rising', 'falling'}
    assert all(fact['status'] == 'conflict' for fact in claims)
    dependent = next(fact for fact in facts if fact['value'].get('owner') == 'source-b')
    assert dependent['depends_on'] == [claims[1]['id']]


def test_exact_coverage_separates_undelivered_and_absent_corpus():
    from knowledge.structured_facts import retrieval_coverage
    rows = [{'id': 'present', 'fact_kind': 'error', 'fact_target': 'C1020'}]
    coverage = retrieval_coverage('C1020 C9999', rows, plc_model='FX3U',
        targets={'errors': ['C1020', 'C9999']}, delivered_records=[])
    present, missing = coverage['targets']
    assert present['corpus'] == 'present' and present['delivery'] == 'pending'
    assert present['reason'] != 'exact_fact_not_found'
    assert missing['corpus'] == 'absent' and missing['delivery'] == 'not_available'
    assert missing['reason'] == 'exact_fact_not_found'


def test_equal_unreviewed_witness_does_not_downgrade_a_reviewed_claim():
    from knowledge.structured_facts import _merge_fact_rows
    base = {'id': 'edge', 'dimension': 'execution.edge', 'value': {'edge': 'rising'},
            'scope': {'models': ['FX3U'], 'forms': ['ANDP'], 'languages': ['ladder']},
            'conditions': [{'executions_per_scan': 1}]}
    left = [{**base, 'status': 'source_verified', 'verification': 'source_checked',
             'sources': [{'manual_id': 'reviewed'}]}]
    _merge_fact_rows(left, [{**base, 'status': 'candidate_evidence', 'verification': 'not_performed',
                             'sources': [{'manual_id': 'candidate'}]}])
    assert len(left) == 1 and left[0]['status'] == 'source_verified'
    assert {source['manual_id'] for source in left[0]['sources']} == {'reviewed', 'candidate'}
    _merge_fact_rows(left, [{**base, 'conditions': [{'executions_per_scan': 2}],
                             'status': 'candidate_evidence', 'verification': 'not_performed',
                             'sources': [{'manual_id': 'different_condition'}]}])
    assert len(left) == 2 and left[1]['status'] == 'candidate_evidence'
    assert left[0]['conditions'] == [{'executions_per_scan': 1}]


def test_complementary_source_details_are_not_reported_as_a_conflict():
    from knowledge.structured_facts import _merge_fact_rows
    base = {'id': 'increment', 'dimension': 'effects.documented_operation',
            'scope': {'models': ['Q03UDV'], 'forms': ['INC'], 'languages': ['ladder']},
            'status': 'source_verified', 'verification': 'source_checked'}
    left = [{**base, 'value': {'operation': 'add', 'delta': 1},
             'sources': [{'manual_id': 'native'}]}]
    _merge_fact_rows(left, [{**base, 'value': {'operation': 'add', 'destination': 'operand'},
                             'sources': [{'manual_id': 'other'}]}])
    assert len(left) == 2 and all(f['status'] == 'source_verified' for f in left)
    assert left[0]['value'] == {'operation': 'add', 'delta': 1}
    _merge_fact_rows(left, [{**base, 'value': {'operation': 'subtract', 'delta': 1},
                             'sources': [{'manual_id': 'contradictory'}]}])
    assert left[0]['status'] == left[-1]['status'] == 'conflict'


def test_merge_compares_all_conditional_variants_and_reuses_equal_witness():
    from knowledge.structured_facts import _merge_fact_rows
    def fact(condition, edge, manual):
        return {'id': 'edge', 'dimension': 'execution.edge', 'value': {'edge': edge},
                'scope': {'models': ['FX3U'], 'forms': ['LDP']},
                'conditions': [{'mode': condition}], 'status': 'source_verified',
                'verification': 'source_checked', 'sources': [{'manual_id': manual}]}
    rows = [fact('first', 'rising', 'first')]
    _merge_fact_rows(rows, [fact('repeated', 'rising', 'second')])
    _merge_fact_rows(rows, [fact('repeated', 'rising', 'third')])
    assert len(rows) == 2
    assert {s['manual_id'] for s in rows[1]['sources']} == {'second', 'third'}
    _merge_fact_rows(rows, [fact('repeated', 'falling', 'contrary')])
    assert rows[0]['status'] == 'source_verified'
    assert rows[1]['status'] == rows[2]['status'] == 'conflict'


@pytest.mark.parametrize('query,aspect', [
    ('ANDP previous scan ST equivalent', 'timing_state'),
    ('SET RST 同一扫描周期', 'same_scan'),
    ('LDP first execution', 'first_execution'),
    ('ANDP 条件禁用', 'disabled'), ('LDP jump', 'jump'),
])
def test_timing_questions_require_the_requested_detail(query, aspect):
    from knowledge.structured_facts import requested_aspects, _aspect_state
    assert aspect in requested_aspects(query)
    packet = {'target': 'ANDP', 'value': {'facts': [{
        'id': 'edge', 'dimension': 'execution.edge', 'status': 'source_verified',
        'verification': 'source_checked', 'sources': [{'manual_id': 'manual'}],
        'scope': {'models': ['FX3U'], 'forms': ['ANDP'], 'languages': ['st']},
        'value': {'edge': 'rising'}}]}}
    assert _aspect_state(packet, aspect, model='FX3U', language='st', query=query) == 'missing'


@pytest.mark.parametrize('change,expected', [
    ({}, 'answered'), ({'returns': 'unknown'}, 'missing'),
    ({'parameters': [{'name': 's', 'type': {}, 'direction': 'input'}]}, 'missing'),
    ({'parameters': [{'name': 's', 'type': 'BOOL', 'direction': 'unknown'}]}, 'missing'),
    ({'available': False}, 'not_applicable'),
])
def test_documented_signature_completeness_does_not_grant_core_execution(change, expected):
    from knowledge.structured_facts import _aspect_state
    signature = {'parameters': [{'name': 's', 'type': 'BOOL', 'direction': 'input'}],
                 'returns': 'BOOL', 'enable': False, **change}
    packet = {'target': 'ANDP', 'value': {'call_available': False, 'facts': [{
        'id': 'signature', 'dimension': 'parameters.st_signature', 'status': 'source_verified',
        'verification': 'source_checked', 'sources': [{'manual_id': 'manual'}],
        'scope': {'models': ['FX3U'], 'forms': ['ANDP'], 'languages': ['st']},
        'value': signature}]}}
    assert _aspect_state(packet, 'st_call', model='FX3U', language='st', query='ANDP ST') == expected
    assert packet['value']['call_available'] is False


@pytest.mark.parametrize('aspect', ['arity', 'order', 'roles', 'types'])
def test_structured_operand_table_answers_without_duplicate_parameter_facts(aspect):
    from knowledge.structured_facts import _aspect_state
    packet = {'target': 'SEL', 'value': {'facts': [{
        'id': 'operands', 'dimension': 'parameters.typed_operands', 'status': 'source_verified',
        'verification': 'source_checked', 'sources': [{'manual_id': 'manual'}],
        'scope': {'models': ['FX3U'], 'forms': ['SEL'], 'languages': ['st', 'fbd']},
        'value': {'operands': [{'name': 'G', 'direction': 'input', 'data_type': {'kind': 'bool'}}]}}]}}
    assert _aspect_state(packet, aspect, model='FX3U', language='fbd', query='SEL') == 'answered'
    if aspect == 'types':
        packet['value']['facts'][0]['value']['operands'][0]['data_type'] = {'kind': 'unknown'}
        assert _aspect_state(packet, aspect, model='FX3U', language='fbd', query='SEL') == 'missing'


def test_operand_read_and_write_roles_do_not_answer_instruction_behaviour():
    from knowledge.structured_facts import _aspect_state
    packet = {'target': 'MOV', 'value': {'facts': [{
        'id': 'role', 'dimension': 'effects.destination_write', 'status': 'source_verified',
        'verification': 'source_checked', 'sources': [{'manual_id': 'manual'}],
        'value': {'target': 'destination_operand'}}]}}
    assert _aspect_state(packet, 'effects', model='FX3U', language=None, query='MOV behavior') == 'missing'


@pytest.mark.parametrize('legacy_status,expected', [('unknown', 'answered'), ('conflict', 'unresolved')])
def test_complete_manual_operand_table_is_not_masked_by_legacy_placeholder(legacy_status, expected):
    from knowledge.structured_facts import _aspect_state
    scope = {'models': ['FX3U'], 'forms': ['SEL'], 'languages': ['fbd']}
    facts = [
        {'id': 'old', 'dimension': 'parameters.types', 'value': {'types': ['unknown']},
         'status': legacy_status, 'scope': scope},
        {'id': 'table', 'dimension': 'parameters.typed_operands',
         'value': {'operands': [{'name': 'G', 'type': 'BOOL', 'direction': 'input'}]},
         'status': 'source_verified', 'verification': 'source_checked',
         'sources': [{'manual_id': 'manual'}], 'scope': scope}]
    assert _aspect_state({'target': 'SEL', 'value': {'facts': facts}}, 'types',
                         model='FX3U', language='fbd', query='SEL data types') == expected


def test_documented_language_denial_is_not_reported_as_missing_extraction():
    from knowledge.structured_facts import _aspect_state
    fact = {'id': 'availability', 'dimension': 'identity.language_availability',
        'value': {'language': 'fbd', 'available': False}, 'status': 'source_verified',
        'verification': 'source_checked', 'sources': [{'manual_id': 'manual'}],
        'scope': {'models': ['FX3U'], 'forms': ['LD'], 'languages': ['fbd']}}
    packet = {'target': 'LD', 'value': {'facts': [fact]}}
    assert _aspect_state(packet, 'fbd_call', model='FX3U', language='fbd', query='LD') == 'not_applicable'
    fact['conditions'] = [{'firmware_before': '1.00'}]
    assert _aspect_state(packet, 'fbd_call', model='FX3U', language='fbd', query='LD') == 'missing'


def test_overlapping_cpu_packets_cannot_hide_conflicting_behaviour():
    from knowledge.structured_facts import _instruction_aspects
    records = []
    for models, edge in [(['FX3U'], 'rising'), (['FX3U', 'FX3UC'], 'falling')]:
        packet = {'content_origin': 'authored_fact', 'kind': 'instruction', 'target': 'ANDP',
            'scope': {'models': models}, 'value': {'facts': [{
                'id': 'edge', 'dimension': 'execution.edge', 'status': 'source_verified',
                'verification': 'source_checked', 'sources': [{'manual_id': 'manual'}],
                'value': {'edge': edge}}]}}
        records.append({'text': json.dumps(packet)})
    assert _instruction_aspects(records, ['execution'], plc_model='FX3U', language=None,
                                query='ANDP edge') == [{'aspect': 'execution', 'status': 'unresolved'}]


@pytest.mark.parametrize('query,language,expected', [
    ('EN/ENO', 'st', {'ST.CALL.ENENO'}),
    ('EN/ENO', 'fbd', {'FBD.CALL.ENENO'}),
    ('FBD 主程序调用 ST FB 的 EN/ENO', 'fbd', {'ST.CALL.ENENO', 'FBD.CALL.ENENO'}),
])
def test_language_targets_keep_both_sides_of_a_mixed_language_call(monkeypatch, query, language, expected):
    from knowledge.structured_facts import structured_fact_targets
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    monkeypatch.setattr(DEFAULT_INSTRUCTION_REGISTRY, 'language_facts', [
        {'target': target, 'aliases': ['EN/ENO']} for target in ['ST.CALL.ENENO', 'FBD.CALL.ENENO']])
    assert set(structured_fact_targets(query, plc_model='FX3U', language=language)['language_features']) == expected


def test_zero_supplementary_slots_keeps_explicit_targets_in_generation():
    from knowledge.retriever import build_knowledge_context
    context = build_knowledge_context('ANDP ANDF', plc_model='FX3U', top_k=0, language='ladder')
    targets = context.manifest['retrieval_coverage']['targets']
    assert {row['target'] for row in targets if row['corpus'] == 'present'} >= {'ANDP', 'ANDF'}
    assert context.manifest['structured_facts']['residual_retrieval'] is False
    assert all(row['delivery'] == 'delivered' for row in targets if row['corpus'] == 'present')
