
from tools.offline_inputs import private_path
import json
import re
import sqlite3
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from shared.paths import resource_path


@pytest.fixture(scope='module')
def semantic_source_entries():
    import gzip
    import zipfile
    from plc.instruction_definition_storage import expand_definitions
    root = Path(__file__).resolve().parents[1]
    with zipfile.ZipFile(private_path('research/' + 'evidence/rag-source-definitions-20261016.zip')) as archive:
        payload = expand_definitions(json.loads(gzip.decompress(archive.read('instruction_definitions.json.gz'))))
    return {(e['target_model'], e['opcode']): e for e in payload['entries']}










def test_authored_fact_projection_preserves_nested_numbers_scopes_and_verification():
    from hypothesis import given, strategies as st
    from knowledge.fact_corpus import project_definition

    @given(st.integers(min_value=-(2**63), max_value=2**63-1),
           st.sampled_from(['source_verified', 'candidate_evidence', 'conflict', 'unknown']),
           st.sampled_from(['FX3U', 'FX3S', 'Q03UDV', 'FX5U']))
    def check(value, status, model):
        original = {'vendor': 'mitsubishi', 'opcode': 'TEST', 'target_model': model,
            'facts': [{'id': 'limit', 'dimension': 'constraints.range', 'scope': {'models': [model], 'forms': ['TEST']},
                'status': status, 'value': {'min': value, 'unit': 'ms', 'condition': {'not': True}}, 'sources': []}]}
        projected, held = project_definition(original)
        fact = projected['facts'][0]
        assert not held
        assert fact['value']['min'] == value and type(fact['value']['min']) is int
        assert fact['value']['condition'] == {'not': True}
        assert fact['scope'] == {'models': [model], 'forms': ['TEST']}
        assert fact['status'] == status
        assert original['facts'][0]['value']['min'] == value
    check()


def test_core_fact_roundtrip_keeps_conditions_and_dependencies():
    from hypothesis import given, strategies as st
    from plc.instruction_definition import InstructionFactGroup
    from plc.instruction_definition_storage import compact_definitions, expand_definitions

    @given(st.integers(-32768, 32767), st.booleans(), st.sampled_from(['st', 'ladder', 'fbd']))
    def check(boundary, required, language):
        raw = {'id': 'limit', 'dimension': 'constraints.range', 'value': {'minimum': boundary},
               'status': 'source_verified', 'verification': 'source_checked',
               'sources': [{'manual_id': 'synthetic', 'revision': '1', 'pdf_page': 1}],
               'scope': {'models': ['FX3U'], 'forms': ['TEST'], 'languages': [language], 'versions': ['1.635.0.1']},
               'conditions': [{'context': 'hardware.transistor', 'equals': required}], 'depends_on': ['scope']}
        mapped = InstructionFactGroup.from_mapping(raw).as_mapping()
        assert mapped['conditions'] == raw['conditions']
        assert mapped['depends_on'] == ['scope']
        payload = {'schema_version': 1, 'entries': [{'opcode': 'TEST', 'target_model': 'FX3U', 'facts': [mapped]}]}
        recovered = expand_definitions(compact_definitions(payload))['entries'][0]['facts'][0]
        assert recovered == mapped
    check()






@pytest.mark.parametrize('opcode,fact_id,operands,values,expected', [
    ('PLSY', 'source:restriction:124:6', ['K100', 'K0', 'Y0'], {'$1': 0}, 'passed'),
    ('PLSY', 'source:restriction:124:6', ['K100', 'K32768', 'Y0'], {'$1': 32768}, 'failed'),
    ('DPLSY', 'source:restriction:124:13', ['K100', 'K32768', 'Y0'], {'$1': 32768}, 'passed'),
    ('PLSR', 'source:restriction:122:3', ['K9', 'K10', 'K100', 'Y0'], {'$0': 9}, 'failed'),
    ('PLSR', 'source:restriction:122:5', ['K100', 'K10', 'K5000', 'Y0'], {'$2': 5000}, 'passed'),
    ('PWM', 'source:restriction:128:4', ['K0', 'K1', 'Y0'], {'$0': 0, '$1': 1}, 'passed'),
    ('PWM', 'source:restriction:128:4', ['K20', 'K10', 'Y0'], {'$0': 20, '$1': 10}, 'failed'),
    ('PWM', 'source:restriction:128:4', ['D0', 'K10', 'Y0'], {'$1': 10}, 'unverified'),
    ('INITR', 'source:restriction:94:0', ['R2048', 'K1'], {}, 'passed'),
    ('INITR', 'source:restriction:94:0', ['R2049', 'K1'], {}, 'failed'),
    ('IVDR', 'source:restriction:101:0', ['K0', 'HFD', 'H9696', 'K1'], {'$1': 253, '$2': 38550}, 'passed'),
    ('IVDR', 'source:restriction:101:0', ['K0', 'HFD', 'H9966', 'K1'], {'$1': 253, '$2': 39270}, 'failed'),
    ('IVDR', 'source:restriction:101:0', ['K0', 'HEE', 'K0', 'K1'], {'$1': 238, '$2': 0}, 'passed'),
    ('ZRST', 'source:restriction:201:2', ['M10', 'M0'], {}, 'passed'),
    ('ZRST', 'source:restriction:201:2', ['C199', 'C200'], {}, 'failed'),
])
def test_reviewed_source_rules_use_form_specific_values_without_changing_the_program(opcode, fact_id, operands, values, expected):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.instruction_constraints import check_fact_constraints
    entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry(opcode, cpu='FX3U')
    fact = next(f for f in entry['facts'] if f['id'] == fact_id)
    before = list(operands)
    result = check_fact_constraints([fact], {**values, '$OPERANDS': operands}, target=opcode,
        plc_model='FX3U', language='ladder')
    assert result['status'] == expected
    assert operands == before


@pytest.mark.parametrize('opcode,operands,failed', [
    ('PWM', ['K20', 'K10', 'Y0'], True), ('PWM', ['K0', 'K1', 'Y0'], False),
    ('PLSY', ['K100', 'K0', 'Y0'], False), ('DPLSY', ['K100', 'K32768', 'Y0'], False),
])
def test_existing_ladder_parameter_validation_consumes_shared_constraints(opcode, operands, failed):
    from plc.validation import _validate_instruction_boundaries, PLCJsonValidationError
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode, cpu='FX3U')
    if failed:
        with pytest.raises(PLCJsonValidationError, match='PWM_width_must_not_exceed_period'):
            _validate_instruction_boundaries(form.spec, operands, '$.rungs[0]', opcode=opcode)
    else:
        _validate_instruction_boundaries(form.spec, operands, '$.rungs[0]', opcode=opcode)


@pytest.mark.parametrize('arguments', ['In1:=0.0, In2:=2.0', 'In2:=2.0, In1:=0.0'])
def test_unadmitted_native_st_call_checks_named_values_without_using_ladder_order(arguments):
    from plc.st_validation import inspect_st_program
    report = inspect_st_program(f'PROGRAM MAIN VAR R:REAL; END_VAR R:=EXPT({arguments}); END_PROGRAM', plc_model='FX3U')
    assert report['semantic'] == 'failed'
    assert any(f['code'] == 'ST_NUMERIC_DOMAIN' and f['severity'] == 'error' for f in report['findings'])


@pytest.mark.parametrize('opcode,fact_id,operands,values,memory,context,expected', [
    ('FLCRT', 'source:restriction:70:5', ['K1', 'D100', 'D200', 'K1'], {}, {'D200': 7}, {}, 'passed'),
    ('FLCRT', 'source:restriction:70:5', ['K1', 'D100', 'D200', 'K1'], {}, {'D200': 8}, {}, 'failed'),
    ('FLCRT', 'source:restriction:70:7', ['K0', 'D100', 'D200', 'K1'], {'$0': 0}, {'D203': 10}, {}, 'passed'),
    ('FLCRT', 'source:restriction:70:7', ['K0', 'D100', 'D200', 'K1'], {'$0': 0}, {'D203': 9}, {}, 'failed'),
    ('FLCRT', 'source:restriction:70:7', ['K1', 'D100', 'D200', 'K1'], {'$0': 1}, {'D203': 1}, {}, 'passed'),
    ('VAL', 'source:restriction:182:0', ['D100', 'D200', 'D300'], {}, {'D100': 0x3120, 'D101': 0}, {}, 'passed'),
    ('VAL', 'source:restriction:182:0', ['D100', 'D200', 'D300'], {}, {'D100': 0x312D, 'D101': 0}, {}, 'passed'),
    ('VAL', 'source:restriction:182:0', ['D100', 'D200', 'D300'], {}, {'D100': 0x2D31, 'D101': 0}, {}, 'failed'),
    ('VAL', 'source:restriction:182:0', ['D100', 'D200', 'D300'], {}, {}, {}, 'unverified'),
    ('DABS', 'source:restriction:9:0', ['X0', 'Y0', 'D0'], {}, {}, {'hardware': {'output_type': 'transistor'}}, 'passed'),
    ('DABS', 'source:restriction:9:0', ['X0', 'Y0', 'D0'], {}, {}, {'hardware': {'output_type': 'relay'}}, 'failed'),
    ('DABS', 'source:restriction:9:0', ['X0', 'Y0', 'D0'], {}, {}, {}, 'unverified'),
    ('RS2', 'source:restriction:258:0', ['D0', 'K1', 'D10', 'K1', 'K0'], {'$4': 0}, {}, {}, 'failed'),
    ('RS2', 'source:restriction:258:0', ['D0', 'K1', 'D10', 'K1', 'K2'], {'$4': 2}, {}, {}, 'passed'),
])
def test_reviewed_configuration_and_memory_rules_are_deterministic(opcode, fact_id, operands, values, memory, context, expected):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.instruction_constraints import check_fact_constraints
    from plc.device_identity import decimal_region_address
    fact = next(f for f in DEFAULT_INSTRUCTION_REGISTRY.definition_entry(opcode, cpu='FX3U')['facts'] if f['id'] == fact_id)
    result = check_fact_constraints([fact], {**values, '$OPERANDS': operands}, target=opcode, plc_model='FX3U',
        language='ladder', context={**context, 'memory_reader': lambda base, offset: memory[decimal_region_address(base, offset)]})
    assert result['status'] == expected


@pytest.mark.parametrize('other_opcode,other_reads,other_writes,expected', [
    ('LD', ['T0'], [], 'passed'), ('OUT', [], ['T0'], 'failed'), ('OUT', [], ['T1'], 'passed'),
])
def test_special_timer_reservation_rejects_another_driver_and_allows_contact_reads(other_opcode, other_reads, other_writes, expected):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.instruction_constraints import check_fact_constraints
    fact = next(f for f in DEFAULT_INSTRUCTION_REGISTRY.definition_entry('STMR', cpu='FX3U')['facts']
                if f['id'] == 'source:restriction:164:0')
    result = check_fact_constraints([fact], {'$OPERANDS': ['T0', 'K10', 'M0']}, target='STMR', plc_model='FX3U',
        language='ladder', context={'current_instruction': 'timer', 'program_accesses': [
            {'id': 'timer', 'opcode': 'STMR', 'reads': [], 'writes': ['T0']},
            {'id': 'other', 'opcode': other_opcode, 'reads': other_reads, 'writes': other_writes}]})
    assert result['status'] == expected


def test_authored_fact_projection_withholds_unparsed_conditions_and_dependency_closure():
    from knowledge.fact_corpus import project_definition
    entry = {'opcode': 'TEST', 'target_model': 'FX3U', 'facts': [
        {'id': 'condition', 'value': {'condition': 'Only available with the optional expansion board'}, 'status': 'candidate_evidence'},
        {'id': 'effect', 'value': {'bits': 32}, 'depends_on': ['condition'], 'status': 'source_verified'},
        {'id': 'result', 'value': {'words': 2}, 'depends_on': ['effect'], 'status': 'source_verified'},
        {'id': 'independent', 'value': {'max': 32767}, 'status': 'candidate_evidence'}]}
    projected, held = project_definition(entry)
    assert [f['id'] for f in projected['facts']] == ['independent']
    assert {f['id'] for f in held} == {'condition', 'effect', 'result'}
    assert entry['facts'][0]['value']['condition'].startswith('Only available')


def test_authored_fact_projection_removes_rendering_but_preserves_formal_relation():
    from knowledge.fact_corpus import project_definition
    fact = {'id': 'chart', 'value': {'text': 'An expressive rendering must stay private.',
        'outputs': [{'target': {'parameter': 'D', 'offset': 1}, 'expression': {'op': 'eq', 'args': [1, 1]}}]},
        'scope': {'models': ['FX3U'], 'forms': ['CMP']}, 'status': 'source_verified'}
    projected, held = project_definition({'opcode': 'CMP', 'target_model': 'FX3U', 'facts': [fact]})
    assert not held and 'text' not in projected['facts'][0]['value']
    assert projected['facts'][0]['value']['outputs'] == fact['value']['outputs']
    assert projected['facts'][0]['status'] == 'source_verified'


def test_authored_fact_does_not_lose_conditions_carried_only_by_source():
    from knowledge.fact_corpus import project_definition
    projected, held = project_definition({'opcode': 'TEST', 'target_model': 'FX3U', 'facts': [
        {'id': 'source_condition', 'value': {'bits': 16}, 'status': 'source_verified',
         'sources': [{'manual_id': 'manual', 'conditions': ['Only with the optional adapter connected']}]}]})
    assert projected['facts'] == []
    assert held[0]['id'] == 'source_condition'


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_authored_fact_projection_rejects_nonfinite_numeric_values(value):
    from knowledge.fact_corpus import typed_value, UnresolvedExpression
    with pytest.raises(UnresolvedExpression):
        typed_value({'max': value})


def test_authored_runtime_rejects_renamed_original_database(tmp_path, monkeypatch):
    from knowledge import index_access
    path = tmp_path / 'plc_facts.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE meta(key TEXT,value TEXT)')
        db.execute("INSERT INTO meta VALUES('schema_version','4')")
    monkeypatch.setattr(index_access, 'resource_path', lambda name: path)
    assert index_access.index_path() is None


def test_authored_runtime_manifest_cases_and_exact_model_boundaries():
    from knowledge.fact_corpus import resolve, CORPUS_KIND
    from knowledge.retriever import retrieve_fact_aware_knowledge, build_knowledge_context
    path = Path(resource_path('knowledge/runtime/plc_facts.sqlite'))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key='corpus_kind'").fetchone()[0] == CORPUS_KIND
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not tables.intersection({'page_artifacts', 'tables', 'device_sections'})
    assert resolve('device', ['M8028'], plc_model='FX1N')[0]['device_definition_status'] == 'not_applicable'
    assert resolve('device', ['M8028'], plc_model='FX3U') == []
    assert resolve('device', ['D8020'], plc_model='FX1N')[0]['device_definition_status'] == 'conditions_missing'
    assert resolve('device', ['SB0041'], plc_model='L26-BT')[0]['id'] == resolve('device', ['SB41'], plc_model='L26-BT')[0]['id']
    rows = retrieve_fact_aware_knowledge('PLSY operands', plc_model='FX3U', top_k=3)
    assert rows and all(r['content_origin'] == 'authored_fact' for r in rows)
    assert all(r['distribution_status'] == 'fact_representation_reviewed' for r in rows)
    context = build_knowledge_context('PLSY operands', plc_model='FX3U')
    assert 'outputs' in context or 'operands' in context
    assert all(marker not in context for marker in ('[PAGE ', '[TABLE page=', 'Output pulse frequency'))


def test_authored_original_cases_have_independent_expected_observations():
    from plc.ir import validate_plc_ir, ir_to_ladder
    from simulator.bounded import ScanMachine
    payload = json.loads(Path(resource_path('knowledge/runtime/original_cases.json')).read_text(encoding='utf-8'))
    assert len({c['family'] for c in payload['cases']}) == 8
    for case in payload['cases']:
        validate_plc_ir(case['program'])
        machine = ScanMachine(ir_to_ladder(case['program']), plc_model=case['models'][0], initial=case['initial'], execution_context={})
        for frame in case['frames']:
            machine.scan(frame)
        assert case['verification']['bounded_scan'] == 'passed'
        assert {address: machine.memory.get(address) for address in case['expected']} == case['expected'], case['id']
        assert not machine.gaps
        assert case['verification']['native_compile'] == 'not_performed'


def test_authored_package_allowlist_excludes_all_original_evidence():
    from scripts.runtime_knowledge_resources import runtime_knowledge_data, assert_fact_database
    root = Path(__file__).resolve().parents[1]
    files = runtime_knowledge_data(root)
    assert not any('fx3u_knowledge' in source or source.endswith('.pdf') for source, _ in files)
    with pytest.raises(ValueError, match='Historical evidence'):
        assert_fact_database(private_path('resources/knowledge/fx3u_knowledge.sqlite'))


def test_authored_pwm_resources_are_collected_for_release_and_public_source():
    from scripts.export_open_source import STRUCTURED_CATALOGS
    from scripts.runtime_knowledge_resources import INSTRUCTION_FILES, runtime_instruction_data
    names = {'pwm_reviewed_facts.json', 'pwm_contract.generated.json'}
    assert names <= set(INSTRUCTION_FILES)
    assert names <= set(STRUCTURED_CATALOGS)
    root = Path(__file__).resolve().parents[1]
    collected = {Path(source).name for source, _ in runtime_instruction_data(root)}
    assert names <= collected


def test_authored_diagnostic_facts_keep_code_register_and_threshold_distinct():
    from knowledge.fact_corpus import resolve
    rows = resolve('error', ['6105'], plc_model='FX3U')
    packets = [json.loads(row['text'].split('\n')[-1]) for row in rows]
    watchdog = next(p['value'] for p in packets if p['value'].get('category') == 'watchdog')
    assert watchdog['register'] == 'D8061'
    assert watchdog['trigger'] == {'left': 'scan_time', 'operator': 'gt', 'right': {'device': 'D8000'}}
    assert watchdog['recovery'] == 'unknown'
    assert watchdog['cpu_applicability'].endswith('individual_cpu_condition_not_certified')


def test_authored_mcp_viewer_asset_graph_excludes_workbench_entry(tmp_path):
    from scripts.runtime_knowledge_resources import runtime_viewer_data
    directory = tmp_path / 'web/dist'
    (directory / 'assets').mkdir(parents=True)
    files = {'simulator.html': '<script src="./assets/view.js"></script>',
        'assets/view.js': 'import "./shared.js";', 'assets/shared.js': 'import "./view.css";',
        'assets/view.css': 'body { src: url("./face.woff2"); }', 'assets/face.woff2': 'font',
        'index.html': '<script src="./assets/main.js"></script>', 'assets/main.js': 'workbench'}
    for name, content in files.items():
        (directory / name).write_text(content, encoding='utf-8')
    actual = {Path(path).relative_to(directory).as_posix() for path, _ in runtime_viewer_data(tmp_path)}
    assert actual == {'simulator.html', 'assets/view.js', 'assets/shared.js', 'assets/view.css', 'assets/face.woff2'}


def test_authored_function_candidates_use_the_selected_cpu_and_source_classification():
    from knowledge.retriever import discover_capabilities
    compare = discover_capabilities('比较大小', plc_model='FX3U')
    assert 'CMP' in {c['name'] for c in compare['candidates']}
    assert not {'BCD', 'MOV'}.intersection(c['name'] for c in compare['candidates'])
    clock = discover_capabilities('用户扫描时钟', plc_model='Q00J')
    candidate = next(c for c in clock['candidates'] if c['name'] == 'DUTY')
    assert candidate['target_call']['target_model'] == 'Q00J'


def _database() -> Path:
    return Path(private_path('resources/knowledge/fx3u_knowledge.sqlite'))


def _flags(opcode: str, manual_id: str) -> list[str]:
    with sqlite3.connect(_database()) as connection:
        row = connection.execute(
            "SELECT completion_flags_json FROM instructions WHERE opcode_norm=? AND manual_id=?",
            (opcode.casefold(), manual_id),
        ).fetchone()
    assert row is not None
    return json.loads(row[0] or "[]")


def test_device_ranges_never_cross_device_families():
    pattern = re.compile(
        r"^(ER|SM|SD|TS|TC|CS|CC|[XYMSTCDRVZPIN])(\d+)-(ER|SM|SD|TS|TC|CS|CC|[XYMSTCDRVZPIN])(\d+)$",
        re.I,
    )
    with sqlite3.connect(_database()) as connection:
        ranges = connection.execute(
            "SELECT device FROM device_records WHERE record_type='device_range'"
        ).fetchall()
    bad = []
    for (value,) in ranges:
        match = pattern.fullmatch(str(value or ""))
        if not match or match.group(1).upper() != match.group(3).upper():
            bad.append(value)
    assert bad == []


def test_known_operand_placeholders_are_not_indexed_as_devices_in_same_instruction_chunk():
    cases = {
        ("fx3_programming_r", "RBFM"): {"M1", "M2", "N1", "N2"},
        ("fx3_programming_r", "WBFM"): {"M1", "M2", "N1", "N2"},
        ("fxcpu_basic_applied_m", "FROM"): {"N1", "N2", "N3"},
        ("fxcpu_basic_applied_m", "TO"): {"N1", "N2", "N3"},
    }
    bad = []
    with sqlite3.connect(_database()) as connection:
        for (manual_id, opcode), expected in cases.items():
            row = connection.execute(
                "SELECT chunk_id FROM instructions WHERE manual_id=? AND opcode_norm=?",
                (manual_id, opcode.casefold()),
            ).fetchone()
            assert row is not None and row[0] is not None
            chunk_id = int(row[0])
            devices = {
                str(value).upper()
                for (value,) in connection.execute(
                    "SELECT entity FROM entity_index WHERE chunk_id=? AND entity_type='device'",
                    (chunk_id,),
                ).fetchall()
            }
            overlap = sorted(expected & devices)
            if overlap:
                bad.append((manual_id, opcode, overlap))
    assert bad == []


def test_completion_flags_exclude_generic_status_relays():
    assert "M8067" not in _flags("ACOS", "fx3_programming_r")
    assert "M8067" not in _flags("ASIN", "fx3_programming_r")
    assert not {"M8020", "M8021", "M8022"}.intersection(
        _flags("INT", "fx3_programming_r")
    )
    positioning = set(_flags("DRVA", "fx3_positioning_k"))
    assert "M8029" in positioning
    assert not {"M8329", "M8343", "M8350", "M8360", "M8370"}.intersection(positioning)


def test_manual_specific_execution_complete_flag_is_preserved():
    assert "M8012" in _flags("ZRN", "fxcpu_basic_applied_m")


def test_no_error_records_do_not_absorb_following_error_row_semantics():
    with sqlite3.connect(_database()) as connection:
        rows = connection.execute(
            """
            SELECT manual_id,pdf_page,message,cause,corrective_action,raw_text
            FROM error_records
            WHERE error_code_norm='0000'
            ORDER BY manual_id,pdf_page,id
            """
        ).fetchall()
    assert rows
    bad = []
    for manual_id, pdf_page, message, cause, corrective_action, raw_text in rows:
        normalized_message = re.sub(r"^[\s⎯—-]+", "", str(message or "")).strip()
        if normalized_message.casefold() != "no error":
            bad.append((manual_id, pdf_page, "message", message))
        if str(cause or "").strip():
            bad.append((manual_id, pdf_page, "cause", cause))
        if str(corrective_action or "").strip():
            bad.append((manual_id, pdf_page, "corrective_action", corrective_action))
        normalized_raw = re.sub(r"^[\s⎯—-]+", "", str(raw_text or "")).strip()
        if normalized_raw.casefold() != "no error":
            bad.append((manual_id, pdf_page, "raw_text", raw_text))
    assert bad == []


def test_reserved_no_error_ranges_do_not_become_fake_error_rows():
    with sqlite3.connect(_database()) as connection:
        rows = connection.execute(
            "SELECT manual_id,pdf_page,error_code,message FROM error_records WHERE error_code_norm <> '0000'"
        ).fetchall()
    bad = []
    for manual_id, pdf_page, code, message in rows:
        normalized = re.sub(r"^[\s⎯—-]+", "", str(message or "")).strip().casefold()
        if normalized in {"no error", "to", "through"}:
            bad.append((manual_id, pdf_page, code, message))
    assert bad == []



def test_primary_structured_quality_audit_uses_semantic_device_evidence(tmp_path):
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "structured-quality.json"
    subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "audit_structured_knowledge_quality.py"),
            "--database",
            str(_database()),
            "--output",
            str(output),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    device_stats = report["stats"]["device_records"]
    unresolved = [
        item
        for item in report["issues"]
        if item.get("code") == "possible_operand_placeholder_device_record"
    ]
    assert unresolved == []
    assert device_stats["placeholder_like_device_records"] == 0
    assert device_stats["placeholder_candidate_records"] > 0
    assert (device_stats["placeholder_warnings_suppressed_by_concrete_evidence"]
            == device_stats["placeholder_candidate_records"])
    assert device_stats["n_syntax_device_records"] == 0


@pytest.fixture(scope='module')
def refreshed_instruction_index(tmp_path_factory):
    from tools.build_fx3u_knowledge_v3 import refresh_instruction_index_copy
    output = tmp_path_factory.mktemp('source-bound-index') / 'knowledge.sqlite'
    report = refresh_instruction_index_copy(_database(), output)
    return output, report


@pytest.mark.parametrize('manual,opcode,page,symbols', [
    ('fx3_programming_r', 'ADD', 273, ['S1', 'S2', 'D']),
    ('fx3_programming_r', 'WSFL', 310, ['S', 'D', 'N1', 'N2']),
    ('fx3_programming_r', 'IVCK', 687, ['S1', 'S2', 'D', 'N']),
    ('fx3_programming_r', 'TCMP', 565, ['S1', 'S2', 'S3', 'S', 'D']),
    ('fx3_programming_r', 'CML', 256, ['S', 'D']),
    ('fx3_positioning_k', 'DVIT', 176, ['S1', 'S2', 'D1', 'D2']),
])
def test_rebuilt_index_uses_definition_page_and_explicit_operands(
        refreshed_instruction_index, manual, opcode, page, symbols):
    output, _ = refreshed_instruction_index
    with sqlite3.connect(output) as con:
        row = con.execute('SELECT page_start,chunk_id,operands_json FROM instructions '
                          'WHERE manual_id=? AND opcode=?', (manual, opcode)).fetchone()
        assert row and row[0] == page
        source = con.execute('SELECT pdf_page,pdf_page_end,section FROM chunks WHERE id=?', (row[1],)).fetchone()
    assert source and source[0] <= page <= source[1]
    assert 'How to Read' not in source[2] and 'Designation' not in source[2]
    operands = json.loads(row[2])
    assert [item['position'] for item in operands] == symbols
    assert all(item['description'] for item in operands)
    assert all(item['extraction_status'] == 'candidate_evidence' for item in operands)
    assert all(item['source_pages'] == [page] for item in operands)


def test_rebuilt_index_preserves_all_frozen_source_tables(refreshed_instruction_index):
    from itertools import zip_longest
    output, report = refreshed_instruction_index
    assert report['integrity'] == ['ok'] and report['foreign_key_violations'] == 0
    assert report['source_verification_promoted'] is False
    with sqlite3.connect(_database()) as original, sqlite3.connect(output) as refreshed:
        for table in report['source_tables_unchanged']:
            # Stream source bytes, including geometry and embeddings; do not
            # generate an expectation with the rebuilding parser.
            sentinel = object()
            for before, after in zip_longest(original.execute(f'SELECT * FROM {table}'),
                                            refreshed.execute(f'SELECT * FROM {table}'), fillvalue=sentinel):
                assert before == after, table
        assert refreshed.execute("SELECT COUNT(*) FROM instructions WHERE manual_id='fxcpu_basic_applied_m'").fetchone()[0] > 77
        assert refreshed.execute("SELECT COUNT(*) FROM instructions WHERE opcode='ST' AND manual_id='structured_fundamentals_o'").fetchone()[0] == 0


def test_st_signature_does_not_inherit_native_call_order(refreshed_instruction_index):
    output, _ = refreshed_instruction_index
    with sqlite3.connect(output) as con:
        row = con.execute("SELECT operands_json FROM instructions WHERE manual_id='fxcpu_basic_applied_m' AND opcode='WSFL'").fetchone()
    operands = json.loads(row[0])
    assert [item['position'] for item in operands] == ['S', 'N1', 'N2', 'D']
    # Missing font glyphs are not repaired by assigning descriptions through
    # physical row order. The available ST signature establishes order only.
    assert all(item['extraction_status'] == 'unknown' for item in operands)


def test_source_index_refresh_cannot_overwrite_frozen_database(tmp_path):
    from tools.build_fx3u_knowledge_v3 import refresh_instruction_index_copy
    with pytest.raises(ValueError, match='separate output'):
        refresh_instruction_index_copy(_database(), _database())
    existing = tmp_path / 'existing.sqlite'
    existing.write_bytes(b'existing')
    with pytest.raises(ValueError, match='already exists'):
        refresh_instruction_index_copy(_database(), existing)
    assert existing.read_bytes() == b'existing'


@pytest.mark.parametrize('extra', ['N | fabricated | Word', 'N\nextra text'])
def test_operand_projection_rejects_symbols_outside_st_signature(extra):
    from tools.build_fx3u_knowledge_v3 import ManualSpec, PageArtifact, parse_operand_schema
    manual = ManualSpec('fixture', Path('fixture.pdf'), 'Fixture', '1', '', '', 'en',
                        'structured_instruction', 'FixtureCPU', 1, '', '')
    text = ('XOP(EN,S1,D);\nVariable | Description | Data type\n'
            'S1 | Value to read | Word\nD | Destination device | Word\n' + extra)
    page = PageArtifact(manual, 1, '', '', '1.1 XOP / Fixture', '1.1 XOP / Fixture',
                        '', 'instruction', 'XOP', '', '', text, text, text, text)
    operands = parse_operand_schema([page], 'XOP')
    assert [item['position'] for item in operands] == ['S1', 'D']
    assert operands[0]['description'] == 'Value to read'


@pytest.fixture(scope='module')
def device_source_pages():
    """Independent original manual pages and PDF geometry, not builder output."""
    return json.loads((private_path('tests/fixtures/device_evidence_source_pages.json')).read_text(encoding='utf-8'))['pages']


@pytest.mark.parametrize('family,number,definition,other_heading', [
    ('X',59,'Provides the CPU module with commands','Output (Y)'),
    ('Y',59,'Outputs program control results','Provides the CPU module with commands'),
    ('M',60,'auxiliary relay inside the CPU module','Latch relay (L)'),
    ('S',62,'process stepping control','Timer (T/ST)'),
    ('T',62,'measurement starts when the timer coil','Step relay (S)'),
    ('C',65,'counts number of rises','Data register (D)'),
    ('D',67,'storing numerical data','Link register (W)'),
    ('SM',67,'internal relays with fixed specifications','Link special register (SW)'),
    ('SD',68,'internal register with fixed specifications','4.4 Module Access Device'),
])
def test_unnumbered_source_outline_siblings_keep_independent_device_definitions(
        device_source_pages, family, number, definition, other_heading):
    from knowledge.device_evidence import build_device_sections, word_lines
    page=dict(next(p for p in device_source_pages if p['manual_id']=='fx5_application_ag' and p['pdf_page']==number))
    page['device_text']='\n'.join(line for _,line in word_lines(page['words']))
    rows=build_device_sections(page, [])
    selected=next(row for row in rows if row['family']==family)
    assert definition.casefold() in selected['text'].casefold()
    assert other_heading not in selected['text']
    assert 'FX5U' in selected['plc_models'].split(',')
    assert selected['manual_number']=='JY997D55401' and selected['revision']=='AG'
    assert selected['table_binding_status']=='complete_source_section'


@pytest.mark.parametrize('family,number,good,bad',[
    ('S',46,'Initial state type','EEPROM'),
    ('T',50,'For 100 ms pulses','M8028'),
])
def test_selected_fx3u_row_keeps_headers_and_only_its_referenced_notes(device_source_pages,family,number,good,bad):
    from knowledge.device_evidence import build_device_sections
    page=next(p for p in device_source_pages if p['manual_id']=='fxcpu_device_common_l' and p['pdf_page']==number)
    rows=build_device_sections(page,page['tables'])
    selected=next(r for r in rows if r['family']==family and r['table_binding_status']=='cpu_row_with_notes'
                  and r['plc_models']=='FX3U,FX3UC')
    assert good in selected['text'] and bad not in selected['text']
    assert '*1.' in selected['text']
    assert 'source_excerpt' in selected
    other=next(r for r in rows if r['family']==family and r['table_binding_status']=='cpu_row_with_notes'
               and r['plc_models']=='FX3S')
    assert bad in other['text']
    if family=='S':assert 'setting.' in selected['text']


def test_same_page_multiple_families_use_their_actual_section(device_source_pages):
    from knowledge.device_evidence import build_device_sections
    page=next(p for p in device_source_pages if p['manual_id']=='sh080889engz')
    rows=build_device_sections(page,[])
    actual={r['family']:r for r in rows}
    assert set(actual)=={'M','L','B'}
    assert actual['M']['section']=='5.2.3 Internal relay (M)'
    assert actual['L']['section']=='5.2.4 Latch relay (L)'
    assert actual['B']['section']=='5.2.5 Link relay (B)'
    assert '5.2.5 Link relay' not in actual['L']['text']
    spans=[tuple(r['source_excerpt']['section_span']) for r in rows]
    assert all(left[1]<=right[0] for left,right in zip(spans,spans[1:]))


def test_device_list_cpu_scope_carries_only_the_current_subsection(device_source_pages):
    from knowledge.device_evidence import page_model_scope
    pages={p['pdf_page']:p for p in device_source_pages if p['manual_id']=='fx3_programming_r'}
    scope,last=page_model_scope(pages[86])
    assert scope==('FX3S',) and last==('FX3S',)
    scope,last=page_model_scope(pages[87],last)
    assert scope==('FX3S',)
    scope,last=page_model_scope(pages[89],('FX3G','FX3GC'))
    assert scope==('FX3G','FX3GC')
    scope,last=page_model_scope(pages[90],last)
    assert set(scope)=={'FX3U','FX3UC'}


@pytest.mark.parametrize('plain,layout,expected',[
    ('375\nFX3S Series Programming Manual\ntext\n14','text\n375','375'),
    ('294\n5.2.3 Internal relay (M)\ntext','','294'),
    ('2 Devices in Detail\n2.5 Timer [T]\n48\nFXCPU Structured Programming Manual','','48'),
    ('FX Series Programmable Controllers\n4 - 46\nDevices in Detail','','4-46'),
    ('table values\n14','',''),
])
def test_printed_page_uses_physical_footer_or_source_header(plain,layout,expected):
    from knowledge.device_evidence import printed_page_from_source
    assert printed_page_from_source(plain,layout)==expected


def test_missing_cpu_row_note_is_not_delivered_as_complete(device_source_pages):
    import copy
    from knowledge.device_evidence import build_device_sections
    page=copy.deepcopy(next(p for p in device_source_pages if p['pdf_page']==50))
    page['words']=[w for w in page['words'] if w['top']<575]
    rows=build_device_sections(page,page['tables'])
    assert not any(r['plc_models']=='FX3U,FX3UC' and r['table_binding_status']=='cpu_row_with_notes' for r in rows)


def test_physical_reading_order_keeps_preceding_table_out_of_next_section(device_source_pages):
    import copy
    from knowledge.device_evidence import build_device_sections,word_lines
    page=copy.deepcopy(next(p for p in device_source_pages if p['manual_id']=='fxcpu_device_common_l' and p['pdf_page']==44))
    page['device_text']='\n'.join(line for _,line in word_lines(page['words']))
    rows=build_device_sections(page,page['tables'])
    operation=next(r for r in rows if 'Functions and operation examples' in r['section'])
    assert 'turn OFF' in operation['text']
    assert 'FX0S/FX0' not in operation['text'] and 'M496' not in operation['text']
    assert operation['source_excerpt']['text_basis']=='physical_word_reading_order'


def test_retentive_family_is_owned_by_literal_st_instruction_reference(device_source_pages):
    from knowledge.device_evidence import build_device_sections,heading_families
    page=next(p for p in device_source_pages if p['manual_id']=='sh080807engaf')
    rows=build_device_sections(page,[])
    record=next(r for r in rows if r['family']=='ST')
    assert 'OUT ST0' in record['text'] and 'retentive' in record['text']
    assert heading_families('Special Data Register (D8000 to D8511)')==['D']
    assert heading_families('Special register (SD)')==['SD']
    assert heading_families('Input/Output Relays [X and Y]')==['X','Y']


@pytest.mark.parametrize('cpu,changeover,interrupt',[
    ('FX3S','\uf050','–'),('FX3U','–','\uf050'),('FX3UC','–','\uf050'),
])
def test_cpu_columns_keep_both_functions_of_the_same_merged_device_number(device_source_pages,cpu,changeover,interrupt):
    from knowledge.device_evidence import build_device_sections
    page=next(p for p in device_source_pages if p['manual_id']=='fx3_programming_r' and p['pdf_page']==828)
    rows=build_device_sections(page,page['tables'])
    row=next(r for r in rows if r['plc_models']==cpu and 'Device reference: M8028\n' in r['text'])
    assert row['table_binding_status']=='cpu_column_with_notes'
    text=re.sub(r'\s+',' ',row['text'])
    if changeover=='–':assert 'timer changeover' not in text
    else:assert '100 ms/10 ms timer changeover | '+changeover in text
    if interrupt=='–':assert 'Interrupt permission' not in text
    else:assert 'Interrupt permission during FROM/TO (FNC 78 and 79) instruction execution | '+interrupt in text
    assert 'cpu_column_bbox' in row['source_excerpt']
    assert len(row['source_excerpt']['excluded_cpu_cells'])==1
    assert row['source_excerpt']['excluded_cpu_cells'][0]['source_mark']=='–'


def test_missing_column_geometry_keeps_mixed_cpu_functions_unresolved(device_source_pages):
    import copy
    from knowledge.device_evidence import build_device_sections
    page=copy.deepcopy(next(p for p in device_source_pages if p['manual_id']=='fx3_programming_r' and p['pdf_page']==828))
    for table in page['tables']:table.pop('cell_bboxes',None)
    assert build_device_sections(page,page['tables'])==[]


def test_device_evidence_refresh_requires_an_isolated_new_destination(tmp_path):
    from tools.build_fx3u_knowledge_v3 import refresh_device_evidence_copy
    with pytest.raises(ValueError,match='new output'):
        refresh_device_evidence_copy(_database(),_database())
    existing=tmp_path/'existing.sqlite'
    existing.write_bytes(b'existing')
    with pytest.raises(ValueError,match='new output'):
        refresh_device_evidence_copy(_database(),existing)
    assert existing.read_bytes()==b'existing'


def test_device_list_scope_is_consistent_in_source_metadata_and_occurrence_indexes():
    with sqlite3.connect(_database()) as db:
        db.row_factory=sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='device_sections'").fetchone():
            pytest.skip('Device evidence migration is not installed')
        rows=db.execute("SELECT * FROM chunks WHERE manual_id='fx3_programming_r' AND pdf_page<=86 AND pdf_page_end>=86").fetchall()
        assert rows
        for row in rows:
            assert row['plc_models']=='FX3S'
            assert 'FX3U' in row['document_plc_models']
            metadata=json.loads(row['source_applicability_json'])
            assert metadata['basis']=='device_cpu_subsection'
            assert metadata['supported_models']==['FX3S']
            assert metadata['individual_address_support']=='unverified'
            assert metadata['instruction_support']=='unknown'
            assert db.execute('SELECT COUNT(*) FROM entity_index WHERE chunk_id=? AND plc_models!=?',(row['id'],'FX3S')).fetchone()[0]==0
            assert db.execute('SELECT COUNT(*) FROM device_records WHERE chunk_id=? AND plc_models!=?',(row['id'],'FX3S')).fetchone()[0]==0


@pytest.mark.parametrize('query', ['FX3U ST ** 幂运算 限制', 'FX3U ST 幂运算符限制', 'FX3U ST exponentiation limits'])
def test_st_power_query_delivers_atomic_rules_and_dependencies_at_one_slot(query):
    from knowledge.retriever import retrieve_fact_aware_knowledge, format_knowledge_reference
    from knowledge.structured_facts import authored_packet, retrieval_coverage
    rows = retrieve_fact_aware_knowledge(query, plc_model='FX3U', top_k=1)
    assert len(rows) == 1
    assert rows[0]['fact_kind'] == 'language' and rows[0]['fact_target'] == 'ST.OPERATOR.POWER'
    packet = authored_packet(rows[0])
    related = packet['value']['related_instructions'][0]
    assert related['opcode'] == 'EXPT' and related['call_available'] is False
    assert {'source:st_signature', 'source:positive_base', 'source:power_result'} <= {f['id'] for f in related['facts']}
    assert retrieval_coverage(query, rows, plc_model='FX3U')['status'] == 'complete'
    public = format_knowledge_reference(rows[0])
    assert 'power_base_must_be_positive' in public and 'K6706' in public
    assert 'manual_id' not in public and 'pdf_pages' not in public


def test_fbd_language_fact_chain_keeps_runtime_retrieval_and_core_in_one_scope():
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.fbd_semantics import language_rules
    from knowledge.structured_facts import resolve_language_records, authored_packet, structured_fact_targets
    from knowledge.retriever import format_knowledge_reference
    feature = registry.language_feature('FBD.LITERALS', language='fbd')
    assert feature and registry.language_feature('FBD.LITERALS', language='st') is None
    assert registry.language_feature('ST.OPERATOR.POWER', language='fbd') is None
    rules = language_rules('FBD.LITERALS', 'FX3U/FX3UC')
    rows = resolve_language_records(['FBD.LITERALS'], plc_model='FX3U')
    assert len(rows) == 1
    packet = authored_packet(rows[0])
    assert any(fact['value'] == rules for fact in packet['value']['facts'])
    assert all(fact['scope']['languages'] == ['fbd'] for fact in packet['value']['facts'])
    rendered = format_knowledge_reference(rows[0])
    assert 'typed_iec_prefixes_admitted' in rendered and 'time_range_ms' in rendered
    assert 'manual_id' not in rendered and 'pdf_pages' not in rendered
    assert 'FBD.LITERALS' in structured_fact_targets('FBD 常量 INT#0', plc_model='FX3U')['language_features']
    assert all(target.startswith('ST.') for target in structured_fact_targets('ST 幂运算 INT#0', plc_model='FX3U')['language_features'])


def test_catalogue_instruction_is_targeted_without_admitting_its_call():
    from knowledge.structured_facts import structured_fact_targets, retrieval_coverage
    from knowledge.retriever import retrieve_fact_aware_knowledge
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    targets = structured_fact_targets('FX3U EXPT 参数限制', plc_model='FX3U')
    assert [t['opcode'] for t in targets['instructions']] == ['EXPT']
    rows = retrieve_fact_aware_knowledge('FX3U EXPT 参数限制', plc_model='FX3U', top_k=1)
    assert rows[0]['fact_target'] == 'EXPT'
    assert retrieval_coverage('FX3U EXPT 参数限制', rows, plc_model='FX3U')['status'] == 'complete'
    assert DEFAULT_INSTRUCTION_REGISTRY.resolve_st_call('EXPT', cpu='FX3U') is None


def test_reviewed_compiler_warning_keeps_compiler_and_runtime_domains_separate():
    from knowledge.retriever import retrieve_fact_aware_knowledge, build_knowledge_context
    from knowledge.structured_facts import structured_fact_targets, retrieval_coverage
    query = 'GX Works2 C9146 编译警告'
    targets = structured_fact_targets(query, plc_model='FX3U')
    assert targets['devices'] == [] and targets['errors'] == ['C9146']
    rows = retrieve_fact_aware_knowledge(query, plc_model='FX3U')
    assert rows and all(row.get('fact_target') != 'K6706' for row in rows)
    assert retrieval_coverage(query, rows, plc_model='FX3U')['status'] == 'complete'
    context = build_knowledge_context(query, plc_model='FX3U')
    assert 'EXP(In2*LOG(In1))' in context and 'compiler_base_minimum' in context


def test_unreviewed_cpu_power_domain_does_not_inherit_fx3u_acceptance():
    from knowledge.retriever import retrieve_fact_aware_knowledge
    from knowledge.structured_facts import retrieval_coverage
    from plc.st_validation import inspect_st_program
    query = 'Q02H ST 幂运算限制'
    rows = retrieve_fact_aware_knowledge(query, plc_model='Q02H', top_k=1)
    assert rows and retrieval_coverage(query, rows, plc_model='Q02H')['status'] == 'incomplete'
    checked = inspect_st_program('PROGRAM MAIN VAR R:REAL; END_VAR R:=2.0 ** 3.0; END_PROGRAM', plc_model='Q02H')
    assert checked['syntax'] == 'passed' and checked['semantic'] == 'unverified'


@pytest.mark.parametrize('model', ['Q03UDV', 'L02'])
@pytest.mark.parametrize('base', ['0.0', '(-2.0)'])
def test_ql_power_uses_its_reviewed_log_domain_and_own_error_code(model, base):
    from plc.st_validation import inspect_st_program
    from plc.instruction_constraints import check_language_constraints
    values = {'LEFT': 0.0 if base == '0.0' else -2.0, 'RIGHT': 2.0}
    report = check_language_constraints('ST.OPERATOR.POWER', values, plc_model=model)
    assert report['status'] == 'failed'
    assert any(f.get('error_code') == '4100' for f in report['findings'])
    assert not any(f.get('error_code') == 'K6706' for f in report['findings'])
    checked = inspect_st_program(f'PROGRAM MAIN VAR R:REAL; END_VAR R:={base} ** 2.0; END_PROGRAM', plc_model=model)
    assert checked['syntax'] == 'passed' and checked['semantic'] == 'failed'


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_power_nonfinite_values_are_known_violations_and_disabled_calls_are_inapplicable(value):
    from plc.instruction_constraints import check_instruction_constraints
    result = check_instruction_constraints('EXPT', {'IN1': value, 'IN2': 2.0}, plc_model='FX3U', language='st')
    assert result['status'] == 'failed'
    disabled = check_instruction_constraints('EXPT_E', {'EN': False, 'IN1': value, 'IN2': 2.0},
        plc_model='FX3U', language='st')
    assert disabled['status'] == 'passed'
    assert any(check['status'] == 'not_applicable' for check in disabled['checks'])


def test_constraint_case_and_outer_whitespace_cannot_bypass_the_numeric_domain():
    from hypothesis import given, settings, strategies as st
    from plc.instruction_constraints import check_instruction_constraints
    @settings(max_examples=50, derandomize=True, deadline=None)
    @given(base=st.sampled_from([-2.0, 0.0, 2.0]), mask=st.lists(st.booleans(), min_size=4, max_size=4),
           padding=st.sampled_from(['', ' ', '\t', '\n']))
    def check(base, mask, padding):
        opcode = ''.join(ch.lower() if lower else ch for ch, lower in zip('EXPT', mask))
        result = check_instruction_constraints(padding + opcode + padding, {'iN1': base, 'In2': 2.0},
            plc_model=padding + 'fx3u' + padding, language='ST')
        assert result['status'] == ('passed' if base > 0 else 'failed')
        assert result['target'] == 'EXPT'
    check()


def test_differently_conditioned_effects_are_not_deduplicated():
    from plc.instruction_definition import instruction_task_view
    integer = {'kind': 'int', 'bits': 16, 'signed': True}
    value = {'behavior': 'expression_write', 'outputs': [{'target': {'parameter': 'D', 'offset': 0, 'kind': 'word'},
            'expression': {'op': 'parameter', 'type': integer, 'name': 'S'}}]}
    facts = [{'id': identity, 'dimension': 'effects.result', 'value': value,
              'status': 'source_verified', 'verification': 'source_checked',
              'sources': [{'manual_id': 'synthetic'}],
              'conditions': [{'context': 'hardware.option', 'equals': option}]} for identity, option in [('a', True), ('b', False)]]
    result = instruction_task_view({'opcode': 'TEST', 'target_model': 'FX3U', 'facts': facts})
    assert {row['id'] for row in result['groups']} == {'a', 'b'}
    assert result['receipt']['equivalent_effect_alternatives'] == {}


def test_fact_dependency_closure_cannot_survive_a_missing_link():
    from hypothesis import given, strategies as st
    from plc.instruction_definition import select_fact_dependencies

    @given(st.integers(min_value=2, max_value=18), st.integers(min_value=0, max_value=17))
    def check(length, removed):
        removed %= length - 1
        groups = [{'id': str(i), 'dimension': 'constraints.range', 'value': {'minimum': i},
                   'status': 'source_verified', 'verification': 'source_checked',
                   'sources': [{'manual_id': 'synthetic'}], 'depends_on': [str(i - 1)] if i else []}
                  for i in range(length)]
        complete = select_fact_dependencies(groups, [str(length - 1)], opcode='TEST', model='FX3U')
        assert {g['id'] for g in complete['groups']} == {str(i) for i in range(length)}
        broken = select_fact_dependencies([g for g in groups if g['id'] != str(removed)],
                                         [str(length - 1)], opcode='TEST', model='FX3U')
        assert not broken['groups'] and broken['gaps']
        assert not broken['source_verification_complete']
    check()


def test_replacing_fact_resource_invalidates_existing_core_context(tmp_path):
    from plc.instructions import InstructionRegistry
    from plc.instruction_constraints import check_language_constraints
    import gzip
    resource = tmp_path / 'facts.json.gz'
    resource.write_bytes(Path(resource_path('instructions/mitsubishi/instruction_definitions.json.gz')).read_bytes())
    registry = InstructionRegistry()
    registry.load_instruction_definitions(resource)
    assert registry.fact_bundle_current()
    resource.write_bytes(resource.read_bytes() + b'changed')
    checked = check_language_constraints('ST.OPERATOR.POWER', {'LEFT': 2.0, 'RIGHT': 3.0},
                                         plc_model='FX3U', registry=registry)
    assert checked['status'] == 'unverified'
    assert checked['findings'][0]['code'] == 'PLC_FACT_CONTEXT'


@pytest.mark.parametrize('cpu,supported', [('FX3U',True),('FX3UC',True),('FX3G',False),
    ('FX3GC',False),('FX3S',False),('FX2N',False),('FX1S',False)])
@pytest.mark.parametrize('call', [False,True])
def test_structured_header_availability_reaches_operator_and_unadmitted_call(cpu,supported,call):
    from plc.instruction_constraints import check_language_constraints,check_instruction_constraints
    if call:
        checked=check_instruction_constraints('EXPT',{'IN1':2.0,'IN2':3.0},plc_model=cpu,language='st')
    else:
        checked=check_language_constraints('ST.OPERATOR.POWER',{'LEFT':2.0,'RIGHT':3.0},plc_model=cpu)
    assert checked['status']==('passed' if supported else 'failed')
    checks=[r for r in checked['checks'] if r['dimension']=='constraints.cpu_availability']
    assert checks and any(r['status']==('passed' if supported else 'failed') for r in checks)
    if not supported:assert any(r['code']=='PLC_CPU_UNSUPPORTED' for r in checked['findings'])


@pytest.mark.parametrize('heading,expected', [('5.11 INT_TO_REAL(_E) / conversion',['INT_TO_REAL','INT_TO_REAL_E']),
    ('7.6 EXPT(_E) / Exponentiation',['EXPT','EXPT_E']),('5.1 BOOL_TO_INT / conversion',['BOOL_TO_INT']),
    ('An example calling EXPT(In1, In2)',[])])
def test_shared_heading_identity_does_not_parse_example_calls(heading,expected):
    from knowledge.manual_applicability import source_instruction_symbols
    assert source_instruction_symbols('',heading)==expected


@pytest.mark.parametrize('model', ['Q00U','Q06UDEH','L06','L26','L02/L02-P','FX3U/FX3UC'])
def test_documented_power_domain_covers_exact_models_and_all_merged_members(model):
    from plc.instruction_constraints import check_language_constraints
    result=check_language_constraints('ST.OPERATOR.POWER',{'LEFT':0.0,'RIGHT':2.0},plc_model=model)
    assert result['status']=='failed'
    assert any(row['message']=='power_base_must_be_positive' for row in result['findings'])


@pytest.mark.parametrize('state,status', [('supported','passed'),('unsupported','failed'),('conditional','unverified')])
def test_structured_availability_retains_firmware_conditions(state,status):
    from plc.instruction_constraints import check_fact_constraints
    fact={'id':'cpu','dimension':'constraints.cpu_availability','status':'source_verified','verification':'source_checked',
          'scope':{'models':['FX3G'],'forms':['CONVERT']},'sources':[{'manual_id':'synthetic'}],
          'value':{'model_states':{'FX3G':state},'rule':'cpu_unsupported'}}
    assert check_fact_constraints([fact],{},target='CONVERT',plc_model='FX3G')['status']==status


@pytest.mark.parametrize('device,unit_ms,timer_class', [
    ('T0', 100, 'general'),
    ('T192', 100, 'subroutine_and_interrupt_subroutine'),
    ('T200', 10, 'general'),
    ('T246', 1, 'accumulative'),
    ('T250', 100, 'accumulative'),
    ('T300', 1, 'general'),
])
def test_timer_address_is_located_by_the_reviewed_model_range(device, unit_ms, timer_class):
    from knowledge.retriever import retrieve_fact_aware_knowledge
    from knowledge.structured_facts import retrieval_coverage

    query = f'FX3U {device} 定时器 时基'
    rows = retrieve_fact_aware_knowledge(query, plc_model='FX3U', top_k=4)
    coverage = retrieval_coverage(query, rows, plc_model='FX3U')
    entry = next(item for item in coverage['targets'] if item['kind'] == 'device')
    assert entry['identity_found'] is True
    assert entry['status'] == 'complete'
    assert entry['reason'] == ''
    assert entry['runtime_execution'] == 'not_established'
    assert entry['range_fact']['unit_ms'] == unit_ms
    assert entry['range_fact']['timer_class'] == timer_class


@pytest.mark.parametrize('device', ['M8000', 'M8001', 'M8002', 'M8003', 'D8000', 'C235'])
def test_fx3u_special_device_definitions_are_source_reviewed(device):
    from plc.device_definitions import resolve_device_definition

    row = resolve_device_definition(device, 'FX3U')
    assert row is not None and row['status'] == 'source_verified'
    assert row['members'][0]['cpu'] == 'FX3U'
    assert row['definitions'][0]['access'] in {'system', 'user_system'}


def test_parameter_slots_keep_manual_symbols_and_separate_semantic_names():
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form('PID', cpu='FX3U')
    assert form is not None
    actual = [(operand.name, operand.semantic_name) for operand in form.spec.operands]
    assert actual == [('S1', 'setpoint_value'), ('S2', 'process_value'),
                      ('S3', 'parameter_block'), ('D', 'output_value')]



def test_unresolved_error_code_does_not_answer_with_neighbouring_error_records():
    from knowledge.retriever import retrieve_fact_aware_knowledge
    from knowledge.structured_facts import retrieval_coverage

    query = '错误代码 46 运算 结果 溢出'
    rows = retrieve_fact_aware_knowledge(query, plc_model='FX3U', top_k=5)
    assert all(row.get('fact_kind') != 'error' for row in rows)
    entry = next(item for item in retrieval_coverage(query, rows, plc_model='FX3U')['targets']
                 if item['kind'] == 'error')
    assert entry['target'] == '46'
    assert entry['status'] == 'unresolved' and entry['reason'] == 'exact_fact_not_found'


@pytest.mark.parametrize('query,expected', [
    ('T246 时基', 'complete'), ('T246 定时器类别', 'complete'),
    ('T246 时基和掉电复位行为', 'incomplete'), ('T246 完整定义', 'incomplete'),
])
def test_timer_property_coverage_does_not_certify_device_execution(query, expected):
    from knowledge.structured_facts import resolve_device_records, retrieval_coverage
    rows = resolve_device_records(['T246'], plc_model='FX3UC', query=query)
    entry = retrieval_coverage(query, rows, plc_model='FX3UC', targets={'devices': ['T246']})['targets'][0]
    assert entry['status'] == expected
    assert entry['range_fact']['first'] == 246 and entry['range_fact']['last'] == 249
    assert entry['runtime_execution'] == 'not_established'
    assert all(row['fact_kind'] == 'device_family' for row in rows)


@pytest.mark.parametrize('cpu,address', [('FX3G', 'T246'), ('FX5U', 'T246'), ('FX3U', 'T512'), ('FX3U', 'T24A')])
def test_timer_ranges_do_not_escape_their_model_or_number_scope(cpu, address):
    from knowledge.structured_facts import resolve_device_records
    assert not any(row.get('device_range') for row in resolve_device_records([address], plc_model=cpu))


@given(st.integers(min_value=0, max_value=511))
@settings(deadline=None, max_examples=40)
def test_timer_address_range_matches_the_reference_intervals(number):
    from knowledge.structured_facts import resolve_device_records
    intervals = [(0, 191, 100), (192, 199, 100), (200, 245, 10),
                 (246, 249, 1), (250, 255, 100), (256, 511, 1)]
    expected = next(item for item in intervals if item[0] <= number <= item[1])
    rows = resolve_device_records([f'T{number}'], plc_model='FX3U')
    ranges = [row['device_range'] for row in rows if row.get('device_range')]
    assert len(ranges) == 1
    assert (ranges[0]['first'], ranges[0]['last'], ranges[0]['unit_ms']) == expected


def _coverage_instruction(facts=(), *, call=None, models=('FX3U',)):
    packet = {'kind': 'instruction', 'target': 'SAMPLE', 'scope': {'models': list(models)},
              'content_origin': 'authored_fact', 'value': {'facts': list(facts), 'call_available': True,
              'registry_contract': {'st_call': call, 'native_operand_order': ['S1', 'S2'],
                                    'verification': {'st_call': 'source_verified'}}}}
    return {'fact_kind': 'instruction', 'fact_target': 'SAMPLE', 'text': json.dumps(packet)}


@pytest.mark.parametrize('second_status,dependency,complete', [
    ('source_verified', False, True), ('candidate_evidence', False, False),
    ('unknown', False, False), ('conflict', False, False), ('source_verified', True, False),
])
def test_operand_coverage_requires_every_slot_and_its_dependencies(second_status, dependency, complete):
    from knowledge.structured_facts import retrieval_coverage
    common = {'scope': {'models': ['FX3U'], 'forms': ['SAMPLE']}, 'sources': [{'manual_id': 'synthetic'}],
              'verification': 'source_checked'}
    facts = [dict(common, id='arity', dimension='parameters.arity', value={'minimum': 2, 'maximum': 2}, status='source_verified'),
             dict(common, id='p1', dimension='parameters.1.purpose', value={'purpose': 'source'}, status='source_verified'),
             dict(common, id='p2', dimension='parameters.2.purpose', value={'purpose': 'destination'}, status=second_status,
                  depends_on=['absent'] if dependency else [])]
    result = retrieval_coverage('操作数含义', [_coverage_instruction(facts)], plc_model='FX3U',
                                targets={'instructions': [{'opcode': 'SAMPLE'}]})
    assert result['status'] == ('complete' if complete else 'incomplete')


@pytest.mark.parametrize('enable', [True, False])
def test_st_signature_coverage_treats_enable_as_a_parameter_flag(enable):
    from knowledge.structured_facts import retrieval_coverage
    row = _coverage_instruction(call={'name': 'SAMPLE', 'enable': enable})
    entry = retrieval_coverage('ST 调用', [row], plc_model='FX3U',
                              targets={'instructions': [{'opcode': 'SAMPLE'}]})['targets'][0]
    assert entry['status'] == 'complete'
    assert entry['call_available'] is True and entry['st_call_available'] is True
    assert entry['st_has_enable_parameter'] is enable


@pytest.mark.parametrize('query,complete', [('S1 参数单位', True), ('操作数单位', False)])
def test_operand_facet_coverage_matches_only_the_requested_slots(query, complete):
    from knowledge.structured_facts import retrieval_coverage
    common = {'scope': {'models': ['FX3U'], 'forms': ['SAMPLE']}, 'sources': [{'manual_id': 'synthetic'}],
              'verification': 'source_checked'}
    facts = [dict(common, id='arity', dimension='parameters.arity', value={'minimum': 2, 'maximum': 2}, status='source_verified'),
             dict(common, id='order', dimension='parameters.order', value={'symbols': ['S1', 'S2']}, status='source_verified'),
             dict(common, id='unit', dimension='parameters.1.unit', value={'unit': 'ms'}, status='source_verified')]
    result = retrieval_coverage(query, [_coverage_instruction(facts)], plc_model='FX3U',
                                targets={'instructions': [{'opcode': 'SAMPLE'}]})
    assert result['status'] == ('complete' if complete else 'incomplete')


def test_st_parameter_question_requires_more_than_a_valid_st_signature():
    from knowledge.structured_facts import retrieval_coverage
    row = _coverage_instruction(call={'name': 'SAMPLE', 'enable': False})
    result = retrieval_coverage('ST 参数', [row], plc_model='FX3U',
                               targets={'instructions': [{'opcode': 'SAMPLE'}]})
    assert result['status'] == 'incomplete'
    states = {aspect['aspect']: aspect['status'] for aspect in result['targets'][0]['aspects']}
    assert states['st_call'] == 'answered' and states['purpose'] == 'missing'


@pytest.mark.parametrize('available,verified', [(False, True), (True, False)])
def test_st_call_admission_and_signature_verification_are_independent(available, verified):
    from knowledge.structured_facts import retrieval_coverage
    row = _coverage_instruction(call={'name': 'SAMPLE', 'enable': False})
    packet = json.loads(row['text'])
    packet['value']['call_available'] = available
    packet['value']['registry_contract']['verification']['st_call'] = 'source_verified' if verified else 'declared_unverified'
    row['text'] = json.dumps(packet)
    entry = retrieval_coverage('ST 调用', [row], plc_model='FX3U',
                              targets={'instructions': [{'opcode': 'SAMPLE'}]})['targets'][0]
    assert entry['status'] == 'incomplete' and entry['call_available'] is available
    assert entry['st_call_available'] is available
    assert entry['st_has_enable_parameter'] is False


def test_a_full_definition_does_not_follow_from_one_verified_arity():
    from knowledge.structured_facts import retrieval_coverage
    fact = {'id': 'arity', 'dimension': 'parameters.arity', 'value': {'minimum': 2, 'maximum': 2},
            'status': 'source_verified', 'verification': 'source_checked', 'sources': [{'manual_id': 'synthetic'}]}
    result = retrieval_coverage('SAMPLE 完整定义', [_coverage_instruction([fact])], plc_model='FX3U',
                               targets={'instructions': [{'opcode': 'SAMPLE'}]})
    assert result['status'] == 'incomplete'


def test_error_aliases_share_one_semantic_and_delivery_requirement():
    from knowledge.structured_facts import retrieval_coverage
    from knowledge.fact_coverage import build_fact_coverage
    targets = {'errors': ['6106H', '0x6106', '6106']}
    row = {'id': 'error-row', 'fact_kind': 'error', 'fact_target': '6106H', 'text': 'Diagnostic reference'}
    semantic = retrieval_coverage('错误码 6106', [row], plc_model='FX3U', targets=targets)
    delivered = build_fact_coverage(targets, [row], ['error-row'])
    assert len(semantic['targets']) == len(delivered['requirements']) == 1
    assert semantic['targets'][0]['target'] == delivered['requirements'][0]['target'] == '6106'
    assert delivered['requirements'][0]['status'] == 'candidate_evidence'


def test_authored_diagnostic_definitions_are_filtered_before_the_candidate_limit():
    from knowledge import index_access
    from knowledge.scope import source_subquery
    with sqlite3.connect(':memory:') as db:
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE chunks(id INTEGER, manual_type TEXT, chunk_type TEXT, fact_kind TEXT)')
        db.executemany('INSERT INTO chunks VALUES(?,?,?,?)', [
            (1, 'authored_facts', 'error', 'error'), (2, 'authored_facts', 'error', 'error'),
            (3, 'authored_facts', 'example', 'example')])
        sql, values = source_subquery(db, index_access.schema(db), exclude_structured_kinds=('error',))
        rows = db.execute('SELECT id FROM chunks WHERE id IN (' + sql + ') ORDER BY id LIMIT 1', values).fetchall()
        assert [row['id'] for row in rows] == [3]

@pytest.fixture(scope='module')
def behaviour_source():
    path = private_path('resources/knowledge/fx3u_knowledge_scoped.sqlite')
    source = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    try:
        yield source
    finally:
        source.close()








def _core_accepts(owners):
    """Run the real Core contract, not only this module's own validation."""
    from knowledge.fact_corpus import project_definition
    from plc.instruction_definition import InstructionFactGroup
    accepted = rejected = 0
    for (model, opcode), rows in owners.items():
        result, withheld = project_definition({'vendor': 'Mitsubishi', 'opcode': opcode,
                                              'target_model': model, 'facts': rows})
        assert not withheld, (model, opcode, withheld[:2])
        for fact in result['facts']:
            InstructionFactGroup.from_mapping(fact)
            accepted += 1
        rejected += len(rows) - len(result['facts'])
    assert rejected == 0
    return accepted




































































def test_applied_review_has_no_dead_code():
    from pathlib import Path
    text = Path('tools/fact_applied_review.py').read_text(encoding='utf-8')
    assert 'if False' not in text and 'TODO' not in text


















def _fx5_condition_section(layout, page=10):
    return {'page_layouts': [(page, layout)], 'pages': [page], 'first_page': page, 'tables': []}










































def _fx5_reviewed_signature(language, parameters, *, page=10, identity=None):
    return {'id': identity or 'call-' + language, 'dimension': 'parameters.typed_operands',
            'value': {'operands': parameters, 'documented_page': page},
            'status': 'source_verified', 'verification': 'source_checked',
            'sources': [{'manual_id': 'fx5_programming_ab', 'pdf_pages': [page]}],
            'scope': {'models': ['FX5U'], 'forms': ['TEST'], 'languages': [language]}}






































@pytest.mark.parametrize('program_context,expected', [('ladder', 'passed'),
                                                      ('sfc', 'unverified'), (None, 'unverified')])
def test_unverified_sfc_only_rule_keeps_its_applicability(program_context, expected):
    from plc.instruction_constraints import check_fact_constraints
    fact = {'id': 'sfc-mov-rule', 'dimension': 'constraints.source_qualifications',
            'value': {'state': 'unknown'}, 'status': 'declared_unverified',
            'verification': 'not_performed', 'sources': [{'manual_id': 'fx5_programming_ab'}],
            'scope': {'models': ['FX5U'], 'forms': ['MOV'], 'languages': ['ladder']},
            'conditions': [{'context': 'program_context', 'equals': 'sfc'}]}
    result = check_fact_constraints([fact], {}, target='MOV', plc_model='FX5U', language='ladder',
                                    context={'program_context': program_context})
    assert result['status'] == expected
    if expected == 'passed':
        assert result['checks'][0]['status'] == 'not_applicable'


@pytest.mark.parametrize('status,value,expected', [
    ('unknown', {'basis': 'not_stated_in_this_manual'}, ['source_not_stated']),
    ('conflict', {'claims': [1, 2]}, ['source_conflict']),
    ('declared_unverified', {'unresolved_items': ['antecedent_or_consequent']}, ['source_present_not_extracted']),
    ('unknown', {'gap_kind': 'native_validation_pending'}, ['native_validation_pending']),
    ('source_verified', {'operation': 'edge', 'core_execution_capability': 'not_established'}, []),
])
def test_knowledge_gap_classification_keeps_extraction_source_and_execution_separate(status, value, expected):
    from knowledge.structured_facts import _aspect_state, instruction_aspect_gap_reasons
    fact = {'id': 'operation', 'dimension': 'execution.edge', 'value': value, 'status': status,
            'verification': 'source_checked' if status == 'source_verified' else 'not_performed',
            'sources': [{'manual_id': 'source'}],
            'scope': {'models': ['FX3U'], 'forms': ['ANDP'], 'languages': ['ladder']}}
    packet = {'target': 'ANDP', 'value': {'facts': [fact]}}
    state = _aspect_state(packet, 'execution', model='FX3U', language='ladder', query='ANDP')
    assert instruction_aspect_gap_reasons(packet, 'execution', state=state,
                                         model='FX3U', language='ladder') == expected
