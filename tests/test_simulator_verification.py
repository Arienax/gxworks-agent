import copy

import pytest
from hypothesis import given, settings, strategies as st

from plc.ir import build_plc_ir, canonical_sha256
from storage.session import SessionStore
from simulator import InMemoryTestBackend, PLCTestRunner, SimulatorRegressionService
from simulator.models import normalize_test_suite
from simulator.verification import (
    SimulatorEvidenceError, evaluate_suite_result, execution_binding,
)


def _bounded_expr(op, name=None, value=None, args=(), integer=False):
    result = {'op': op, 'type': {'kind': 'int', 'bits': 16, 'signed': False} if integer else {'kind': 'bool'}}
    if name is not None: result['name'] = name
    if value is not None: result['value'] = value
    if args: result['args'] = list(args)
    return result


def _bounded_row(identity, kind, **fields):
    return {'id': identity, 'kind': kind, 'status': 'confirmed',
            'provenance': {'source': 'user_authored', 'evidence': ['independently specified relation']}, **fields}


def _bounded_ladder(*rows):
    from application.compact_protocol import expand_compact_ladder
    return expand_compact_ladder({'r': [{'h': None, 's': [], 'b': [{'i': i, 'o': o}]} for i, o in rows]})


def _bounded_spec(rows, model='FX3U'):
    return {'plc_model': model, 'behavior_constraints': rows, 'selected_approach': {'construction_plan': {
        'instances': [], 'internal_ranges': [], 'execution_context': {'program_type': 'scan', 'initial_execution_program': False}}}}


@pytest.mark.parametrize('model,boot',[('FX3U','M8002'),('FX5U','SM402')])
def test_bounded_check_uses_ir_cpu_when_confirmed_projection_omits_runtime_identity(model,boot):
    from simulator.bounded import check_bounded_traces
    spec=_bounded_spec([_bounded_row('boot','initialize',values={'M100':False})],model)
    spec.pop('plc_model')
    trace={'initial':{'M100':True},'frames':[{'inputs':{}}]}
    correct=build_plc_ir(_bounded_ladder((['NO '+boot],['RST M100'])),plc_model=model)
    result=check_bounded_traces(correct,spec,[trace])
    assert result['status']=='no_violation_found_in_tested_scope'
    assert result['first_scan_fact']['available'] and boot in result['first_scan_fact']['devices']
    assert result['program_version']['plc_model']==model and 'plc_model' not in spec
    wrong=build_plc_ir(_bounded_ladder(([],['SET M100'])),plc_model=model)
    result=check_bounded_traces(wrong,spec,[trace])
    assert result['status']=='violated' and result['activation_blocked']


@pytest.mark.parametrize('conflict',[False,True])
def test_bounded_check_does_not_execute_with_missing_or_conflicting_cpu(conflict):
    from simulator.bounded import check_bounded_traces
    spec=_bounded_spec([_bounded_row('boot','initialize',values={'M100':False})])
    program=_bounded_ladder(([],['RST M100']))
    if conflict:
        program=build_plc_ir(program,plc_model='FX5U')
        reason='program_cpu_conflicts_with_specification'
    else:
        spec.pop('plc_model')
        reason='missing_cpu_control_facts'
    result=check_bounded_traces(program,spec,[{'initial':{'M100':True},'frames':[{'inputs':{}}]}])
    assert result['status']=='unverified' and result['coverage']['traces_tested']==0
    assert result['checks'][0]['reasons']==[reason]
    assert not result['violations'] and not result['activation_blocked']


@pytest.mark.parametrize('model,first', [('FX3U','M8002'), ('FX5U','SM402')])
def test_bounded_boot_old_state_and_nonzero_initialization(model, first):
    from simulator.bounded import check_bounded_traces
    spec = _bounded_spec([_bounded_row('boot', 'initialize', values={'D100': 17, 'Y0': True},
        execution_context={'program_type': 'scan', 'initial_execution_program': False})], model)
    trace = {'initial': {'D100': 923, 'Y0': False}, 'frames': [{'inputs': {'X0': True}}]}
    correct = _bounded_ladder((['NO '+first], ['MOV K17 D100', 'SET Y0']))
    wrong = _bounded_ladder((['NO '+first], ['MOV K17 D100', 'SET Y0']), (['NC '+first], ['COIL Y0']), (['NO X0'], ['MOV K0 D100']))
    assert check_bounded_traces(correct, spec, [trace])['status'] == 'no_violation_found_in_tested_scope'
    report = check_bounded_traces(wrong, spec, [trace], version_binding={'version_id': 'v3'})
    assert report['status'] == 'violated'
    witness = report['violations'][0]
    assert witness['scan_before']['D100'] == 923
    assert witness['program_version']['version_id'] == 'v3'
    assert witness['writes'][-1]['location']['rung_index'] >= 1


def test_bounded_shared_condition_is_saved_but_independent_condition_reads_immediate_write():
    from simulator.bounded import ScanMachine
    shared = {'device_comments': {}, 'rungs': [{'rung_id':1, 'header_element': {'type':'NO','address':'M0'},
        'shared_inputs': [], 'branches': [
            {'inputs': [], 'outputs': [{'type':'APP_INSTR','opcode':'RST','operands':['M0']}]},
            {'inputs': [], 'outputs': [{'type':'APP_INSTR','opcode':'SET','operands':['M1']}]}]}]}
    machine = ScanMachine(shared, plc_model='FX3U', initial={'M0': True, 'M1':False}, execution_context={'program_type':'scan'})
    assert machine.scan({'inputs':{}})['after']['M1'] is True
    independent = _bounded_ladder((['NO M0'], ['RST M0']), (['NO M0'], ['SET M1']))
    machine = ScanMachine(independent, plc_model='FX3U', initial={'M0':True,'M1':False}, execution_context={'program_type':'scan'})
    assert machine.scan({'inputs':{}})['after']['M1'] is False


def test_bounded_unknown_memory_and_unsupported_instruction_are_not_zero_or_pass():
    from simulator.bounded import check_bounded_traces
    spec = _bounded_spec([_bounded_row('must_run', 'assertion', when=_bounded_expr('constant', value=True), predicate=_bounded_expr('device', name='Y0'))])
    missing = _bounded_ladder((['NO M9'], ['COIL Y0']))
    assert check_bounded_traces(missing, spec, [{'initial': {}, 'frames':[{'inputs':{}}]}])['status'] == 'unverified'
    unknown = _bounded_ladder(([], ['VENDOR_UNKNOWN M0']), ([], ['COIL Y0']))
    report = check_bounded_traces(unknown, spec, [{'initial':{},'frames':[{'inputs':{}}]}])
    assert report['status'] == 'unverified'
    assert any(r['reason'] == 'unknown_instruction_write_scope' for r in report['unsupported_or_missing'])


@pytest.mark.parametrize('model,address,opcode,elapsed,preset,expected', [
    ('FX3U','T201','OUT',1699,170,False), ('FX3U','T201','OUT',1700,170,True),
    ('FX5U','T32','OUT',1699,17,False), ('FX5U','T32','OUT',1700,17,True),
    ('FX5U','T0','OUTH',1700,170,True), ('FX5U','T700','OUTHS',1700,1700,True)])
def test_bounded_explicit_time_by_form_not_scan_count(model,address,opcode,elapsed,preset,expected):
    from simulator.bounded import ScanMachine
    ladder = _bounded_ladder((['NO X0'], [f'{opcode} {address} K{preset}']), (['NO '+address], ['COIL Y0']))
    machine = ScanMachine(ladder, plc_model=model, initial={}, execution_context={'program_type':'scan','initial_execution_program':False})
    result = machine.scan({'inputs':{'X0': True}, 'timer_elapsed_ms':{address:elapsed}})
    assert result['after']['Y0'] == expected
    without_time = machine.scan({'inputs': {'X0':True}})
    assert without_time['after']['Y0'] is None
    assert 'explicit_timer_elapsed_time_missing' in [g['reason'] for g in machine.gaps]


def test_bounded_all_off_does_not_pass_normal_progress():
    from simulator.bounded import check_bounded_traces
    spec = _bounded_spec([_bounded_row('start_progress', 'assertion', when=_bounded_expr('device', name='X0'), predicate=_bounded_expr('device', name='Y0'))])
    ladder = _bounded_ladder((['NC M8000'], ['COIL Y0']))
    report = check_bounded_traces(ladder, spec, [{'initial':{},'frames':[{'inputs':{'X0':True}}]}])
    assert report['status'] == 'violated'


def test_bounded_double_flip_and_stop_completion_competition():
    from simulator.bounded import check_bounded_traces
    dev = lambda a: _bounded_expr('device', name=a)
    const = lambda v: _bounded_expr('constant', value=v)
    effect = lambda v: {'target': {'device':'M100','kind':'bit'}, 'value':const(v)}
    group = _bounded_row('choice','transition_group', enable=const(True), transitions=[
        {'id':'stop', 'when':dev('X2'), 'effects':[effect(False)]},
        {'id':'complete', 'when':dev('X1'), 'effects':[effect(True)]}])
    spec = _bounded_spec([group])
    wrong = _bounded_ladder((['NO X2'], ['RST M100']), (['NO X1'], ['SET M100']))
    correct = _bounded_ladder((['NO X1','NC X2'], ['SET M100']), (['NO X2'], ['RST M100']))
    trace = {'starts_in_run':False,'initial':{'M100':False}, 'frames':[{'inputs':{'X1':True,'X2':True}}]}
    assert check_bounded_traces(wrong,spec,[trace])['status'] == 'violated'
    assert check_bounded_traces(correct,spec,[trace])['status'] == 'no_violation_found_in_tested_scope'


@given(index=st.integers(100,200), sequence=st.lists(st.tuples(st.booleans(),st.booleans()), min_size=2,max_size=8))
@settings(max_examples=50)
def test_bounded_event_tracks_source_not_acceptance(index, sequence):
    from simulator.bounded import check_bounded_traces
    output = 'M'+str(index)
    row = _bounded_row('edge','event', source=_bounded_expr('device',name='X0'), accept=_bounded_expr('device',name='X1'),
                       output=output, edge='rising', startup_policy='require_opposite')
    # Independently authored history/arming program. No constructor output is
    # used as the oracle or program under test.
    ladder = _bounded_ladder((['NO M8002'],['RST M10']), (['NC X0'],['SET M10']),
        (['NC M8002','NO M10','NO X0','NC M11','NO X1'],['COIL '+output]),
        (['NO X0'],['COIL M11']))
    frames = [{'inputs':{'X0':signal,'X1':accept}} for signal,accept in sequence]
    trace = {'initial':{'M10': True,'M11':True},'frames':frames}
    report = check_bounded_traces(ladder,_bounded_spec([row]),[trace])
    assert report['status'] == 'no_violation_found_in_tested_scope'


def test_bounded_adjacent_merged_events_and_wrong_second_edge():
    from simulator.bounded import check_bounded_traces
    const = _bounded_expr('constant',value=True)
    a = _bounded_row('a','event',source=_bounded_expr('device',name='X0'),accept=const,edge='rising',startup_policy='allow_initial_event',output='M100')
    b = _bounded_row('b','event',source=_bounded_expr('device',name='X1'),accept=const,edge='rising',startup_policy='allow_initial_event',output='M101')
    merge = _bounded_row('union','merge_events',events=['a','b'],output='M102')
    # Events are explicit test inputs to the merge-only relation, independently
    # frozen in the trace; adjacent scans must both remain ON.
    spec = _bounded_spec([merge])
    trace = {'starts_in_run':False,'initial':{'M100':True,'M101':False},
        'event_history':{'event.a':True,'event.b':False}, 'previous_inputs':{'M100':False},
        'frames':[{'inputs':{}},{'inputs':{}}]}
    wrong = _bounded_ladder((['P M100'],['COIL M102']))
    correct = _bounded_ladder(([{'or':[['NO M100'],['NO M101']]}],['COIL M102']))
    assert check_bounded_traces(correct,spec,[trace])['status'] == 'no_violation_found_in_tested_scope'
    assert check_bounded_traces(wrong,spec,[trace])['status'] == 'violated'


def test_bounded_trace_budget_and_scans_are_explicit():
    from simulator.bounded import check_bounded_traces
    row = _bounded_row('ok','assertion',when=_bounded_expr('constant',value=True),predicate=_bounded_expr('constant',value=True))
    trace = {'initial':{},'frames':[{'inputs':{}}]}
    report = check_bounded_traces(_bounded_ladder(([],['COIL M0'])), _bounded_spec([row]), [trace]*3, max_traces=2)
    assert report['status'] == 'unverified'
    assert report['coverage']['budget_exhausted']
    report = check_bounded_traces(_bounded_ladder(([],['COIL M0'])), _bounded_spec([row]), [{'initial':{},'frames':[{}]*9}])
    assert report['coverage']['traces_tested'] == 0
    assert report['status'] == 'unverified'


@pytest.mark.parametrize('model',['FX3U','FX5U'])
def test_independent_transition_groups_observe_prior_groups_but_members_share_prestate(model):
    from simulator.bounded import check_bounded_traces
    dev = lambda a: _bounded_expr('device',name=a)
    true = _bounded_expr('constant',value=True)
    effect = lambda a: {'target':{'device':a,'kind':'bit'},'value':true}
    first = _bounded_row('first','transition_group',enable=true,transitions=[{'id':'go','when':dev('X0'),'effects':[effect('M100')]}])
    second = _bounded_row('second','transition_group',enable=true,transitions=[{'id':'go','when':dev('M100'),'effects':[effect('M101')]}])
    ladder = _bounded_ladder((['NO X0'],['SET M100']),(['NO M100'],['SET M101']))
    trace = {'starts_in_run':False,'initial':{'M100':False,'M101':False},'frames':[{'inputs':{'X0':True}}]}
    result = check_bounded_traces(ladder,_bounded_spec([first,second],model),[trace])
    assert result['status'] == 'no_violation_found_in_tested_scope'


def test_two_transitions_are_detected_even_when_final_state_is_restored():
    from simulator.bounded import check_bounded_traces
    true = _bounded_expr('constant',value=True)
    row = _bounded_row('stop','transition_group',enable=true,transitions=[{'id':'stop','when':true,
        'effects':[{'target':{'device':'M100','kind':'bit'},'value':_bounded_expr('constant',value=False)}]}])
    ladder = _bounded_ladder(([],['SET M100']),([],['RST M100']))
    trace = {'starts_in_run':False,'initial':{'M100':False},'frames':[{'inputs':{}}]}
    result = check_bounded_traces(ladder,_bounded_spec([row]),[trace])
    assert result['violations'][0]['reason'] == 'repeated_group_write'
    assert len(result['violations'][0]['writes']) == 2


@pytest.mark.parametrize('model',['FX3U','FX5U'])
def test_uncertain_write_is_not_a_confirmed_duplicate_transition(model):
    from simulator.bounded import check_bounded_traces
    true = _bounded_expr('constant',value=True)
    row = _bounded_row('start','transition_group',enable=true,transitions=[{'id':'go','when':true,
        'effects':[{'target':{'device':'M100','kind':'bit'},'value':true}]}])
    # X9 is intentionally undeclared; the possible reset must remain unknown.
    ladder = _bounded_ladder(([],['SET M100']),(['NO X9'],['RST M100']))
    trace = {'starts_in_run':False,'initial':{'M100':False},'frames':[{'inputs':{}}]}
    result = check_bounded_traces(ladder,_bounded_spec([row],model),[trace])
    assert result['status'] == 'unverified' and not result['activation_blocked']
    assert not result['violations']
    assert result['checks'][0]['unknown_observations'] == 1


@pytest.mark.parametrize('model',['FX3U','FX5U'])
def test_unsupported_bit_group_transfer_cannot_create_a_false_counterexample(model):
    from simulator.bounded import check_bounded_traces
    row = _bounded_row('history','assertion',when=_bounded_expr('constant',value=True),
        predicate=_bounded_expr('device',name='M121'))
    ladder = _bounded_ladder(([],['MOV K1X0 K1M120']))
    trace = {'starts_in_run':False,'initial':{'M121':False},
             'frames':[{'inputs':{'X0':False,'X1':True,'X2':False,'X3':False}}]}
    result = check_bounded_traces(ladder,_bounded_spec([row],model),[trace])
    # A native digit-specified transfer would copy X1 to M121. This limited
    # checker has no supported bit-group effect and must not call it false.
    assert result['status'] == 'unverified' and not result['activation_blocked']
    assert not result['violations']
    assert result['checks'][0]['unverified_witnesses']
    assert 'unknown_instruction_write_scope' in result['checks'][0]['reasons']


def test_generated_trace_domain_reports_budget_cutoff_and_checker_exceptions(monkeypatch):
    import simulator.bounded as bounded
    signals = [_bounded_expr('device',name='X'+str(i)) for i in range(6)]
    predicate = signals[0]
    for signal in signals[1:]:
        predicate = _bounded_expr('or',args=[predicate,signal])
    row = _bounded_row('progress','assertion',when=predicate,predicate=_bounded_expr('constant',value=True))
    spec = _bounded_spec([row])
    result = bounded.check_bounded_traces(_bounded_ladder(([],['COIL Y0'])),spec,
        bounded.finite_behavior_traces(spec,max_traces=2),max_traces=2)
    assert result['coverage']['budget_exhausted'] and result['status'] == 'unverified'
    def broken(*args,**kwargs): raise RuntimeError('fixture')
    monkeypatch.setattr(bounded.ScanMachine,'scan',broken)
    result = bounded.check_bounded_traces(_bounded_ladder(([],['COIL Y0'])),spec,[{'initial':{},'frames':[{}]}])
    assert result['status'] == 'unverified' and result['checker_exceptions'][0]['reason'] == 'checker_exception'


def test_independent_frozen_clock_progress_cannot_be_satisfied_by_all_off():
    from simulator.bounded import check_bounded_traces
    correct = _bounded_ladder((['NO X0'],['TIMER T0 K13']),(['NO T0'],['COIL Y0']))
    wrong = _bounded_ladder((['NC X0'],['COIL Y0']))
    trace={'id':'clock-progress','initial':{'Y0':False},'starts_in_run':False,
        'frames':[{'inputs':{'X0':True},'timer_elapsed_ms':{'T0':0}},
                  {'inputs':{'X0':True},'timer_elapsed_ms':{'T0':1299}},
                  {'inputs':{'X0':True},'timer_elapsed_ms':{'T0':1300}}],
        'expectations':[{'scan':1,'requirement_id':'delay','predicate':_bounded_expr('not',args=[_bounded_expr('device',name='Y0')])},
                        {'scan':2,'requirement_id':'progress','predicate':_bounded_expr('device',name='Y0')}]}
    result=check_bounded_traces(correct,_bounded_spec([]),[],frozen_time_traces=[trace])
    assert result['status'] == 'no_violation_found_in_tested_scope'
    assert result['coverage']['frozen_time_traces'] == 1
    result=check_bounded_traces(wrong,_bounded_spec([]),[],frozen_time_traces=[trace])
    assert result['violations'][0]['reason'] == 'explicit_progress_or_time_expectation'


@pytest.mark.parametrize('edge,opposite,active',[('rising',False,True),('falling',True,False)])
def test_startup_opposite_level_arms_the_first_new_edge(edge,opposite,active):
    from simulator.bounded import check_bounded_traces
    source=_bounded_expr('device',name='X0')
    row=_bounded_row('source','event',source=source,accept=_bounded_expr('constant',value=True),
        edge=edge,startup_policy='require_opposite',output='M100')
    source_kind='NO' if edge=='rising' else 'NC'
    opposite_kind='NC' if edge=='rising' else 'NO'
    history_kind='NC' if edge=='rising' else 'NO'
    ladder=_bounded_ladder((['NO M8002'],['RST M10']),([opposite_kind+' X0'],['SET M10']),
        (['NC M8002','NO M10',source_kind+' X0',history_kind+' M11'],['COIL M100']),(['NO X0'],['COIL M11']))
    trace={'initial':{'M10':True,'M11':active},'frames':[{'inputs':{'X0':opposite}},{'inputs':{'X0':active}}],
        'expectations':[{'scan':1,'requirement_id':'fresh_edge','predicate':_bounded_expr('device',name='M100')}]}
    assert check_bounded_traces(ladder,_bounded_spec([row]),[trace])['status']=='no_violation_found_in_tested_scope'


@pytest.mark.parametrize('model',['FX3U','FX5U'])
@pytest.mark.parametrize('projected_snapshot',[False,True])
def test_behavior_diagnostic_draft_cannot_activate_and_corrected_version_is_rechecked(tmp_path,model,projected_snapshot):
    from application.confirmed_generation_context import project_confirmed_specification
    from plc.ir import build_plc_ir
    store = SessionStore(base_dir=tmp_path/'workspace',legacy_dir=tmp_path)
    project = store.create_project(name='behavior',plc_model=model)
    row = _bounded_row('progress','assertion',when=_bounded_expr('device',name='X0'),predicate=_bounded_expr('device',name='Y0'))
    spec = _bounded_spec([row],model)
    store.set_confirmed_spec(project['id'],spec)
    snapshot_spec = project_confirmed_specification(spec) if projected_snapshot else spec
    def save(inputs):
        ladder = _bounded_ladder((inputs,['COIL Y0']))
        ir = build_plc_ir(ladder,plc_model=model)
        identity,folder = store.prepare_version(project['id'])
        store._write_json(folder/'program.ir.json',ir)
        return store.complete_version(project['id'],identity,{'target_mode':'ladder','plc_model':model,
            'validation_profile':'generation_structural','artifacts':{'ir':'program.ir.json'},'confirmed_spec_snapshot':snapshot_spec})
    wrong = save(['NC X0'])
    assert wrong['activation_blocked'] and wrong['lifecycle_status'] == 'diagnostic'
    assert wrong['behavior_check']['program_version']['version_id'] == wrong['id']
    assert wrong['behavior_check']['program_version']['plc_model'] == model
    with pytest.raises(ValueError,match='不能激活'): store.activate_version(project['id'],wrong['id'])
    correct = save(['NO X0'])
    assert correct['behavior_check']['status'] == 'no_violation_found_in_tested_scope'
    assert correct['behavior_check']['program_version'] != wrong['behavior_check']['program_version']
    assert store.get_project(project['id'])['active_version_id'] == correct['id']


def _suite():
    return normalize_test_suite({
        "name": "motor",
        "tests": [{
            "name": "start_stop", "initial": {"X0": 0},
            "steps": [
                {"id": "start", "at_ms": 0, "set": {"X0": 1}, "expect": {"Y0": 1}},
                {"id": "stop", "at_ms": 10, "set": {"X0": 0}, "wait_for": {"Y0": 0}},
            ],
        }],
    })


def _logic(backend, values):
    if "X0" in values:
        backend.values["Y0"] = values["X0"]


def _result(suite=None, backend=None):
    return PLCTestRunner(backend or InMemoryTestBackend(on_write=_logic)).run_suite(suite or _suite())


@pytest.fixture
def version(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project_id = store.create_project(name="evidence", plc_model="FX3U")["id"]
    version_id, folder = store.prepare_version(project_id)
    ladder = {
        "device_comments": {},
        "rungs": [{
            "rung_id": 1, "header_element": None, "shared_inputs": [],
            "branches": [{
                "branch_id": 1, "y_offset_level": 0,
                "inputs": [{"type": "NO", "address": "X0"}],
                "outputs": [{"type": "COIL", "address": "Y0"}],
            }],
        }],
    }
    program = build_plc_ir(ladder)
    store._write_json(folder / "ladder.json", ladder)
    store._write_json(folder / "program.ir.json", program)
    store.complete_version(project_id, version_id, {
        "target_mode": "ladder", "plc_model": "FX3U",
        "revision": program["revision"], "ir_sha256": canonical_sha256(program),
        "artifacts": {"json": "ladder.json", "ir": "program.ir.json"},
    })
    return store, project_id, version_id, program, folder


def _execute(version):
    store, project_id, version_id, _, _ = version
    return SimulatorRegressionService(
        store, backend=InMemoryTestBackend(on_write=_logic),
    ).run_version_suite(project_id, version_id, _suite())


def test_complete_observed_run_passes_without_mutating_evidence():
    suite = _suite()
    result = _result(suite)
    before = copy.deepcopy((suite, result))
    verification = evaluate_suite_result(suite, result)
    assert verification["status"] == "passed"
    assert verification["program_repair_allowed"] is False
    assert (suite, result) == before


@pytest.mark.parametrize("corruption,category", [
    ("missing_case", "incomplete"), ("duplicate_case", "incomplete"),
    ("missing_assertion", "incomplete"), ("duplicate_assertion", "incomplete"),
    ("missing_observation", "incomplete"), ("missing_final_values", "incomplete"),
    ("not_executed", "incomplete"), ("partial_execution", "incomplete"),
    ("contradictory_assertion", "evidence_invalid"),
    ("wrong_counts", "evidence_invalid"), ("wrong_suite_hash", "evidence_invalid"),
    ("wrong_case_hash", "evidence_invalid"), ("malformed_counts", "evidence_invalid"),
])
def test_pass_claim_needs_complete_consistent_observations(corruption, category):
    result = _result()
    case = result["results"][0]
    if corruption == "missing_case":
        result["results"] = []
    elif corruption == "duplicate_case":
        result["results"].append(copy.deepcopy(case))
    elif corruption == "missing_assertion":
        case["assertions"].pop()
    elif corruption == "duplicate_assertion":
        case["assertions"].append(copy.deepcopy(case["assertions"][0]))
    elif corruption == "missing_observation":
        case["trace"] = [event for event in case["trace"] if event["event"] != "wait_for"]
    elif corruption == "missing_final_values":
        next(event for event in case["trace"] if event["event"] == "final_sample")["values"] = {}
    elif corruption == "not_executed":
        case["execution_started"] = False
    elif corruption == "partial_execution":
        result["executed_count"] = 0
    elif corruption == "contradictory_assertion":
        case["assertions"][0]["passed"] = False
    elif corruption == "wrong_counts":
        result["counts"]["failed"] = 1
    elif corruption == "wrong_suite_hash":
        result["suite_sha256"] = "0" * 64
    elif corruption == "wrong_case_hash":
        case["test_sha256"] = "0" * 64
    elif corruption == "malformed_counts":
        result["counts"] = []
    verdict = evaluate_suite_result(_suite(), result)
    assert verdict["status"] == "blocked"
    assert verdict["category"] == category
    assert verdict["program_repair_allowed"] is False


def test_action_only_run_is_not_behavioral_verification():
    suite = normalize_test_suite({"tests": [{
        "name": "stimulus_only", "initial": {"X0": 0},
        "steps": [{"set": {"X0": 1}}],
    }]})
    result = _result(suite)
    assert result["status"] == "passed"  # The runner did complete its actions.
    assert evaluate_suite_result(suite, result)["category"] == "incomplete"


def test_changed_expectation_cannot_reuse_run_with_same_case_and_step_names():
    original = _suite()
    result = _result(original)
    changed = copy.deepcopy(original)
    changed["tests"][0]["steps"][0]["expect"][0]["value"] = 0
    assert evaluate_suite_result(changed, result)["category"] == "evidence_invalid"


def test_missing_device_value_cannot_satisfy_not_equal_assertion():
    class MissingValueBackend(InMemoryTestBackend):
        def read_many(self, addresses):
            return {address: None if address == "Y0" else 0 for address in addresses}

    suite = normalize_test_suite({"tests": [{
        "name": "observed",
        "steps": [{"expect": [{"address": "Y0", "operator": "ne", "value": 0}]}],
    }]})
    result = _result(suite, MissingValueBackend())
    assert result["status"] == "passed"
    assert evaluate_suite_result(suite, result)["category"] == "incomplete"


def test_assertion_and_invariant_failures_are_reviewable():
    suite = _suite()
    failed = _result(suite, InMemoryTestBackend())
    verification = evaluate_suite_result(suite, failed)
    assert verification["category"] == "assertion"
    assert verification["program_repair_allowed"]
    suite["tests"][0]["invariants"] = [{"type": "mutual_exclusion", "devices": ["Y0", "Y1"]}]
    suite = normalize_test_suite(suite)
    result = _result(suite, InMemoryTestBackend(initial={"Y1": 1}, on_write=_logic))
    verification = evaluate_suite_result(suite, result)
    assert verification["category"] == "invariant"
    assert verification["program_repair_allowed"]


@pytest.mark.parametrize("phase,category", [
    ("connect", "environment"), ("initial", "setup"), ("stimulus", "setup"), ("read", "runtime"),
])
def test_failure_taxonomy_uses_execution_stage_not_program_patch(phase, category):
    class BrokenBackend(InMemoryTestBackend):
        def connect(self):
            if phase == "connect":
                raise RuntimeError("simulator unavailable")
            return super().connect()

        def write_many(self, values):
            if phase == "initial" or (phase == "stimulus" and values.get("X0") == 1):
                raise RuntimeError("invalid test stimulus")
            return super().write_many(values)

        def read_many(self, addresses):
            if phase == "read" and self.values.get("X0") == 1:
                raise RuntimeError("observation read failed")
            return super().read_many(addresses)

    verdict = evaluate_suite_result(_suite(), _result(backend=BrokenBackend(on_write=_logic)))
    assert verdict["category"] == category
    assert verdict["status"] == "blocked"
    assert verdict["program_repair_allowed"] is False


def test_saved_run_binds_program_suite_result_and_keeps_backend_identity(version):
    execution = _execute(version)
    store, project_id, version_id, program, _ = version
    loaded = store.load_simulator_run(project_id, version_id, execution["record"]["run_id"])
    binding = loaded["binding"]
    assert binding["ir_sha256"] == canonical_sha256(program)
    assert binding["suite_sha256"] == canonical_sha256(loaded["suite"])
    assert binding["result_sha256"] == canonical_sha256(loaded["result"])
    assert binding["binding_scope"] == "execution_snapshot"
    assert loaded["verification"]["status"] == "passed"
    assert loaded["record"]["backend_kinds"] == ["test_memory_not_plc_simulator"]


@pytest.mark.parametrize("corruption", ["suite_binding", "trace_binding", "suite_contents", "trace_contents", "missing_trace"])
def test_mismatched_or_modified_artifacts_cannot_be_loaded_as_evidence(version, corruption):
    execution = _execute(version)
    store, project_id, version_id, _, folder = version
    record = execution["record"]
    target = folder / record["suite_artifact" if corruption.startswith("suite") else "trace_artifact"]
    payload = store._read_json(target)
    if corruption.endswith("binding"):
        payload["binding"]["version_id"] = "another_version"
    elif corruption == "suite_contents":
        payload["suite"]["tests"][0]["steps"][0]["expect"][0]["value"] = 0
    elif corruption == "missing_trace":
        target.unlink()
        assert store.load_simulator_run(project_id, version_id, record["run_id"]) is None
        return
    else:
        payload["result"]["results"][0]["trace"].clear()
    store._write_json(target, payload)
    with pytest.raises(SimulatorEvidenceError):
        store.load_simulator_run(project_id, version_id, record["run_id"])


def test_program_change_during_execution_cannot_be_rebound_at_save(version):
    store, project_id, version_id, program, folder = version
    changed = copy.deepcopy(program)
    changed["revision"] += 1
    changed_once = False

    def change_program(backend, values):
        nonlocal changed_once
        _logic(backend, values)
        if not changed_once:
            changed_once = True
            store._write_json(folder / "program.ir.json", changed)
            store.update_version_metadata(project_id, version_id, {
                "revision": changed["revision"], "ir_sha256": canonical_sha256(changed),
            })

    service = SimulatorRegressionService(store, backend=InMemoryTestBackend(on_write=change_program))
    with pytest.raises(SimulatorEvidenceError) as error:
        service.run_version_suite(project_id, version_id, _suite())
    assert error.value.category == "version_conflict"
    assert store.list_simulator_runs(project_id, version_id) == []


def test_invalid_pass_is_not_persisted_as_success(version):
    store, project_id, version_id, program, _ = version
    result = _result()
    result["results"] = []
    with pytest.raises(SimulatorEvidenceError):
        store.save_simulator_run(
            project_id, version_id, _suite(), result,
            execution_snapshot=execution_binding(project_id, version_id, program, _suite()),
        )
    assert store.list_simulator_runs(project_id, version_id) == []


def test_stale_approved_binding_is_rejected_before_runtime_preparation(version):
    store, project_id, version_id, program, _ = version

    class Preparer:
        def prepare(self):
            pytest.fail("A stale plan must not prepare the runtime")

    binding = execution_binding(project_id, version_id, program, _suite())
    binding["suite_sha256"] = "0" * 64
    service = SimulatorRegressionService(store, backend=InMemoryTestBackend(), preparer=Preparer())
    with pytest.raises(SimulatorEvidenceError):
        service.run_version_suite(project_id, version_id, _suite(), expected_binding=binding)
    assert store.list_simulator_runs(project_id, version_id) == []


@pytest.mark.parametrize("field,value", [
    ("backend_kinds", ["gx_simulator2_gateway"]), ("counts", {"passed": 999}), ("passed", False),
])
def test_index_cannot_change_result_or_backend_provenance(version, field, value):
    execution = _execute(version)
    store, project_id, version_id, _, _ = version
    project = store.get_project(project_id)
    project["versions"][0]["simulator_runs"][0][field] = value
    store.save_project(project)
    with pytest.raises(SimulatorEvidenceError):
        store.load_simulator_run(project_id, version_id, execution["record"]["run_id"])


def test_legacy_record_remains_readable_without_migration_or_new_verdict(version):
    execution = _execute(version)
    store, project_id, version_id, _, folder = version
    record = execution["record"]
    new_fields = (
        "evidence_schema_version", "binding_scope", "suite_sha256", "result_sha256",
    )
    for key in ("suite_artifact", "trace_artifact"):
        target = folder / record[key]
        payload = store._read_json(target)
        for field in new_fields:
            payload["binding"].pop(field)
        store._write_json(target, payload)
    project = store.get_project(project_id)
    old_record = project["versions"][0]["simulator_runs"][0]
    for field in (*new_fields, "verification"):
        old_record.pop(field)
    # A genuinely old version may not have a persisted IR yet.
    project["versions"][0]["artifacts"].pop("ir")
    store.save_project(project)
    (folder / "program.ir.json").unlink()
    project_folder = store.project_dir(project_id)
    before = {path: path.read_bytes() for path in project_folder.rglob("*") if path.is_file()}
    loaded = store.load_simulator_run(project_id, version_id, record["run_id"])
    assert loaded["result"]["status"] == "passed"
    assert loaded["verification"]["category"] == "legacy_evidence"
    assert loaded["verification"]["program_repair_allowed"] is False
    assert before == {path: path.read_bytes() for path in project_folder.rglob("*") if path.is_file()}
