"""Construction diagnostic adapter for the existing user-path runner.

All generation, binding, compilation and checks remain in product Core.
Materials, scheduling, transport observations, blind packets and review outcomes
reuse benchmark_user_path. This module introduces no product configuration.
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import time
import uuid
import zipfile
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.benchmark_agent_b import PreviewProvider, run_case, schedule, summarize
from scripts.benchmark_user_path import (
    ROOT, CONSTRUCTION_ARMS, Journey, RequestCheckedProvider, diagnostic_specification,
    assert_unfilled_spec, assert_frozen_code, freeze_code, write_new, export_blind_candidate,
    paired_outcome, review_outcome, final_delivery_status,
    case_cluster_test, _latency_summary,
)

_BLOCK = re.compile(r'\n# Core construction delivery\n.*?\n# End Core construction delivery\n', re.S)


def construction_specification(case):
    from plc.specification.confirmed import canonicalize_confirmed_spec
    spec = diagnostic_specification(case)
    spec['behavior_constraints'] = copy.deepcopy(case['behavior_constraints'])
    spec['selected_approach']['construction_plan'] = copy.deepcopy(case['construction_plan'])
    spec['selected_approach']['generation_guide'] = case['confirmed_method_note']
    for binding in spec.get('io_bindings', []):
        binding['active_level'] = case.get('input_active_levels', {}).get(binding['address'],1)
        binding['inactive_level'] = 1-binding['active_level']
    return canonicalize_confirmed_spec(spec)


def common_construction_request(request):
    value = copy.deepcopy(request)
    messages = value.get('messages', [])
    if not messages or not isinstance(messages[0].get('content'), str) or len(_BLOCK.findall(messages[0]['content'])) != 1:
        raise ValueError('One complete construction delivery block is required')
    messages[0]['content'] = _BLOCK.sub('', messages[0]['content'])
    # A strict provider may carry the two documented wire schemas. Only their
    # reference mechanism differs; transport/model parameters cannot differ.
    if 'response_format' in value:
        fmt = value['response_format']
        if fmt.get('type') == 'json_schema':
            from application.compact_protocol import compact_response_schema
            fmt['json_schema']['schema'] = compact_response_schema()
    return value


def construction_preflight(case, spec, provider):
    from application import generation_agent as agent
    from application.confirmed_generation_context import project_confirmed_specification
    from plc.construction import compile_constructions
    from plc.validation import validate_ladder_candidate_structure
    from plc.ir import build_plc_ir
    from plc.construction import construction_prompt
    from simulator.bounded import check_bounded_traces
    projected = project_confirmed_specification(spec)
    receipts = compile_constructions({**spec,'plc_model':case['plc_model']})
    if receipts['gaps']:
        raise ValueError('Frozen selected methods need available facts and resources: '+str(receipts['gaps']))
    cache, records, requests = {}, {}, {}
    for arm in CONSTRUCTION_ARMS:
        delivery = 'instantiate' if arm == 'core_instantiation' else 'description'
        # Exercise the same positive projection and candidate boundary used after
        # a real model response. A request-only preview cannot check expansion.
        probe = {'r': ([{'construct': row['id']} for row in receipts['instances']]
                       if delivery == 'instantiate' else []) +
                     [{'h':None,'s':[],'b':[{'i':[],'o':['COIL Y0']}]}]}
        decoded = agent.prepare_model_candidate(probe, projected,
                                                case['plc_model'], construction_delivery=delivery)
        validate_ladder_candidate_structure(decoded['ladder'], plc_model=case['plc_model'])
        if decoded['construction_binding'].get('gaps') or decoded['construction_binding'].get('reference_violations'):
            raise ValueError('Candidate decode preflight failed: '+str(decoded['construction_binding']))
        checked = check_bounded_traces(build_plc_ir(decoded['ladder'], plc_model=case['plc_model']),
            projected, [{'initial':{}, 'frames':[{'inputs':{}}]}])
        if checked['program_version']['plc_model'] != case['plc_model'] or not checked['first_scan_fact'].get('available'):
            raise ValueError('Candidate check preflight lost the runtime CPU')
        with ExitStack() as scopes:
            scopes.enter_context(patch.object(agent, '_construction_delivery', return_value=delivery))
            if case.get('frozen_evidence') is not None:
                from knowledge.evidence import KnowledgeContext
                frozen_evidence = case['frozen_evidence']
                scopes.enter_context(patch.object(agent, '_build_knowledge_context',
                    lambda *a, **k: KnowledgeContext(frozen_evidence['text'], copy.deepcopy(frozen_evidence['manifest']))))
            record = run_case({**case, 'confirmed_spec':spec, 'construction_examples':False}, 'automatic',
                              provider=PreviewProvider(provider), model=provider.profile['model'], evidence_cache=cache)
        if len(record['actual_requests']) != 1 or len(record['evidence']) != 1:
            raise ValueError('Preflight must have exactly one final request and evidence context')
        request = record['actual_requests'][0]
        text = request['messages'][0]['content']
        marker = '\n# Confirmed project specification\n'
        if text.count(marker) != 1:
            raise ValueError('One complete confirmed specification is required')
        try:
            delivered_spec, _ = json.JSONDecoder().raw_decode(text.split(marker,1)[1].lstrip())
        except ValueError as error:
            raise ValueError('Confirmed relation delivery was truncated') from error
        if (delivered_spec.get('behavior_constraints') != projected.get('behavior_constraints') or
                (delivered_spec.get('selected_approach') or {}).get('construction_plan') !=
                (projected.get('selected_approach') or {}).get('construction_plan')):
            raise ValueError('Complete confirmed relations or construction plan were lost')
        expected_block = construction_prompt({**delivered_spec, 'plc_model':case['plc_model']}, delivery=delivery)
        if _BLOCK.findall(text) != [expected_block]:
            raise ValueError('Complete construction method delivery was lost')
        if '# Routed construction examples' in text or '# Compacted historical context' in text:
            raise ValueError('Examples/history leaked into a construction arm')
        if record.get('handoff', {}).get('construction_examples', {}).get('enabled'):
            raise ValueError('Reference examples must remain disabled')
        for row in receipts['instances']:
            if '"id":"'+row['id']+'"' not in text:
                raise ValueError('Required construction relation was lost to budgeting')
        records[arm], requests[arm] = record, request
    if common_construction_request(requests[CONSTRUCTION_ARMS[0]]) != common_construction_request(requests[CONSTRUCTION_ARMS[1]]):
        raise ValueError('Non-construction request content differs between arms')
    evidence = [records[arm]['evidence'][0] for arm in CONSTRUCTION_ARMS]
    if evidence[0]['text'] != evidence[1]['text'] or evidence[0]['manifest'] != evidence[1]['manifest']:
        raise ValueError('Evidence differs between construction arms')
    return {'passed': True, 'network_calls':0,'case_id':case['case_id'],
        'non_construction_content_identical':True,'examples_enabled':False,'history_enabled':False,
        'all_required_relations_delivered':True,'model_parameters_identical':True,
        'candidate_decode_validated':True,'runtime_cpu':case['plc_model'],
        'candidate_check_cpu_validated':True,'complete_relation_and_method_values_validated':True,
        'requests':requests,'frozen_evidence':{'text':evidence[0]['text'],'manifest':evidence[0]['manifest']},
        'construction_receipt':receipts}


def prepare_construction_experiment(directory, cases, provider, *, repeats=3, seed=20261005):
    if (directory/'experiment.json').exists():
        raise ValueError('Existing frozen experiment cannot be prepared again')
    prepared, flights, receipts = [], {}, {}
    with Journey(directory/'preparation',PreviewProvider(provider)) as journey:
        for case in cases:
            spec = construction_specification(case)
            assert_unfilled_spec(spec)
            project = journey.service.create_project(name=case['name'],plc_model=case['plc_model'],target_mode='ladder')
            confirmed = journey.service.set_spec(project['id'],spec,None)
            if not confirmed.get('valid'):
                raise ValueError(case['case_id']+': rejected confirmation: '+str(confirmed.get('issues')))
            spec = confirmed['spec']
            flight = construction_preflight(case,spec,provider)
            enriched = {**case, 'confirmed_spec':spec,'frozen_evidence':flight['frozen_evidence']}
            prepared.append(enriched)
            flights[case['case_id']] = flight['requests']
            receipts[case['case_id']] = flight
    for case in prepared:
        write_new(directory/'confirmed'/(case['case_id']+'.json'),case['confirmed_spec'])
        write_new(directory/'preflight'/(case['case_id']+'.json'),receipts[case['case_id']])
    tasks = [{'case_id':c['case_id'],'arm':arm,'repeat':repeat,'block':i//2,
              'run_id':f"{c['case_id']}.{repeat}.{arm}",'blind_id':'candidate_'+uuid.uuid4().hex[:16]}
             for i,(c,arm,repeat) in enumerate(schedule(prepared,list(CONSTRUCTION_ARMS),repeats,seed,blocked=True))]
    code = subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
    dirty = subprocess.run(['git','status','--porcelain'],cwd=ROOT,capture_output=True,text=True,check=True).stdout
    identity = {'experiment':'construction-value','profile_id':provider.profile.get('id'),'profile_model':provider.profile.get('model'),
        'endpoint':provider.profile.get('baseUrl'),'user_model_settings':provider.profile.get('userModelSettings'),
        'seed':seed,'repeats':repeats,'git_commit':code,'git_status':dirty,'python':sys.version,'platform':sys.platform,
        'frozen_at_utc':datetime.now(timezone.utc).isoformat(),
        'conclusion_scope':'This saved ModelScope DeepSeek V4.1 configuration and these confirmed variants only',
        'review':'Codex anonymous static review; no independent human signoff','native_execution':'not_measured'}
    freeze_code(directory)
    write_new(directory/'experiment.json',{'identity':identity,'cases':prepared,'tasks':tasks,'expected_requests':flights})
    with zipfile.ZipFile(directory/'materials_snapshot.zip','x',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(directory/'experiment.json','experiment.json')
    return {'passed':True,'network_calls':0,'cases':len(prepared),'scheduled_jobs':len(tasks),**identity}


def pending_construction_tasks(directory, frozen, *, worker_index=0, workers=1):
    if not 0 <= worker_index < workers:
        raise ValueError('Invalid worker partition')
    return [task for task in frozen['tasks'] if task['block'] % workers == worker_index and
            not (directory/'runs'/(task['run_id']+'.json')).exists() and
            not (directory/'runs'/(task['run_id']+'.started.json')).exists()]


_MEASUREMENT_ADAPTERS = {'scripts/benchmark_construction.py', 'scripts/benchmark_user_path.py'}


def _read_record(directory, task):
    path = directory/'runs'/(task['run_id']+'.json')
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def _frozen_materials(directory):
    with zipfile.ZipFile(directory/'materials_snapshot.zip') as archive:
        if (directory/'experiment.json').read_bytes() != archive.read('experiment.json'):
            raise ValueError('Frozen materials changed')
    return json.loads((directory/'experiment.json').read_text(encoding='utf-8'))


def _completed_generation(record):
    # Semantic failure or a diagnostic-only save is still an observed program;
    # neither authorizes another generation.
    return bool(record and (record.get('job') or {}).get('status') == 'completed' and
                ((record.get('first_candidate') or {}).get('ladder') or record.get('ladder')))


def select_construction_supplement_tasks(source, frozen):
    seen, result = set(), []
    for task in frozen['tasks']:
        if task['run_id'] in seen:
            raise ValueError('Duplicate source job identity')
        seen.add(task['run_id'])
        record = _read_record(source, task)
        if _completed_generation(record):
            continue
        if record and not record.get('transport_errors'):
            raise ValueError('Only transport failures or unfinished jobs may be supplemented')
        reason = ('transport_failed' if record else 'interrupted' if
                  (source/'runs'/(task['run_id']+'.started.json')).exists() else 'unstarted')
        result.append({**copy.deepcopy(task), 'source_blind_id':task['blind_id'],
            'blind_id':'candidate_'+uuid.uuid4().hex[:16], 'source_status':reason,
            'source_run_id':task['run_id'], 'supplement_attempt':1})
    return result


def assert_construction_product_unchanged(source):
    compared = 0
    with zipfile.ZipFile(source/'code_snapshot.zip') as archive:
        for name in archive.namelist():
            if name in _MEASUREMENT_ADAPTERS:
                continue
            if (ROOT/name).read_bytes() != archive.read(name):
                raise ValueError('Product or experiment material changed after source freeze: '+name)
            compared += 1
    return {'compared_files':compared, 'allowed_adapter_changes':sorted(_MEASUREMENT_ADAPTERS)}


def _assert_source_observations(directory, source):
    with zipfile.ZipFile(directory/'source_observations.zip') as archive:
        frozen_runs = {name for name in archive.namelist() if name.startswith('runs/')}
        actual_runs = {p.relative_to(source).as_posix() for p in (source/'runs').glob('*.json')}
        if frozen_runs != actual_runs:
            raise ValueError('Original source execution inventory changed')
        for name in archive.namelist():
            if (source/name).read_bytes() != archive.read(name):
                raise ValueError('Original source observation changed: '+name)


@contextmanager
def source_aligned_official_request(provider, source):
    """Match the original explicit wire settings, without editing either profile.

    Official adapter defaults add thinking and JSON mode. Omitting those fields
    retains the endpoint's own defaults, just as the original request did. Only
    these two known additions can be removed; every other difference is rejected
    by preflight and the existing RequestCheckedProvider.
    """
    original_parameters = (source['identity'].get('user_model_settings') or {}).get('parameters',{})
    official_parameters = (provider.profile.get('userModelSettings') or {}).get('parameters',{})
    if original_parameters != official_parameters:
        raise ValueError('Explicit saved model parameters differ between endpoints')
    requests = [r for arms in source['expected_requests'].values() for r in arms.values()]
    original_resolver = provider._request_params
    def resolve(request):
        params = copy.deepcopy(original_resolver(request))
        if all('response_format' not in r for r in requests) and params.get('response_format') == {'type':'json_object'}:
            params.pop('response_format')
        if (all('extra_body' not in r for r in requests) and
                params.get('extra_body') == {'thinking':{'type':'enabled'}}):
            params.pop('extra_body')
        return params
    with patch.object(provider,'_request_params',resolve):
        yield {'omitted_adapter_defaults':['json_object_response_format','thinking_enabled'],
               'endpoint_default_thinking_mode':'not independently established for both services'}


def prepare_construction_supplement(directory, source, provider):
    """Explicitly authorized completion, preserving every original observation."""
    directory, source = Path(directory).resolve(), Path(source).resolve()
    if directory == source or source in directory.parents or directory in source.parents:
        raise ValueError('Supplement needs a separate private sibling directory')
    if (directory/'experiment.json').exists():
        raise ValueError('Existing frozen supplement cannot be prepared again')
    frozen = _frozen_materials(source)
    if frozen['identity'].get('experiment') != 'construction-value':
        raise ValueError('Supplement requires an original construction experiment')
    if (str(provider.profile.get('baseUrl','')).rstrip('/') != 'https://api.deepseek.com' or
            provider.profile.get('model') != 'deepseek-flash'):
        raise ValueError('Supplement requires the saved official DeepSeek Flash profile')
    code_audit = assert_construction_product_unchanged(source)
    tasks = select_construction_supplement_tasks(source, frozen)
    if not tasks:
        raise ValueError('No unfinished or transport-failed jobs to supplement')
    requests, flights = {}, {}
    for case in frozen['cases']:
        original_flight = json.loads((source/'preflight'/(case['case_id']+'.json')).read_text(encoding='utf-8'))
        if not original_flight.get('complete_relation_and_method_values_validated'):
            raise ValueError('Invalid source method delivery cannot be supplemented')
        assert_unfilled_spec(case['confirmed_spec'])
        with source_aligned_official_request(provider,frozen):
            flight = construction_preflight(case, case['confirmed_spec'], provider)
        if flight['frozen_evidence'] != case['frozen_evidence']:
            raise ValueError('Frozen source evidence changed')
        for arm in CONSTRUCTION_ARMS:
            expected = copy.deepcopy(frozen['expected_requests'][case['case_id']][arm])
            expected['model'] = provider.profile['model']
            if flight['requests'][arm] != expected:
                raise ValueError('Official request differs from source beyond model identity: '+case['case_id'])
        requests[case['case_id']], flights[case['case_id']] = flight['requests'], flight
    directory.mkdir(parents=True, exist_ok=True)
    for case_id, flight in flights.items():
        write_new(directory/'preflight'/(case_id+'.json'), flight)
    identity = {**copy.deepcopy(frozen['identity']), 'experiment':'construction-value-supplement',
        'profile_id':provider.profile.get('id'), 'profile_model':provider.profile.get('model'),
        'endpoint':provider.profile.get('baseUrl'), 'user_model_settings':provider.profile.get('userModelSettings'),
        'frozen_at_utc':datetime.now(timezone.utc).isoformat(), 'source_directory':str(source),
        'source_frozen_at_utc':frozen['identity']['frozen_at_utc'],
        'git_commit':subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip(),
        'git_status':subprocess.run(['git','status','--porcelain'],cwd=ROOT,capture_output=True,text=True,check=True).stdout,
        'conclusion_scope':'These saved ModelScope and official DeepSeek V4.1 Flash endpoints and confirmed variants only',
        'source_code_audit':code_audit, 'supplement_policy':'Keep completed programs; explicitly replace only transport-failed or unfinished jobs'}
    identity['request_alignment'] = {'actual_wire_difference':'model only',
        'omitted_official_adapter_defaults':['json_object_response_format','thinking_enabled'],
        'endpoint_defaults':'Unspecified as in original requests; cross-service behavior equivalence is not asserted'}
    freeze_code(directory)
    write_new(directory/'experiment.json', {'identity':identity, 'cases':copy.deepcopy(frozen['cases']),
        'tasks':tasks, 'expected_requests':requests})
    with zipfile.ZipFile(directory/'materials_snapshot.zip','x',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(directory/'experiment.json','experiment.json')
    with zipfile.ZipFile(directory/'source_observations.zip','x',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob('*.json')):
            if path.parent.name in {'runs','reviews','blind','preflight','confirmed'} or path.name == 'experiment.json':
                archive.write(path,path.relative_to(source).as_posix())
    return {'passed':True, 'network_calls':0, 'scheduled_jobs':len(tasks),
        'retained_source_jobs':len(frozen['tasks'])-len(tasks),
        'source_status_counts':{s:sum(t['source_status']==s for t in tasks)
                                for s in ('transport_failed','interrupted','unstarted')}, **identity}


def fatal_construction_transport_failure(record):
    for error in record.get('transport_errors', []):
        kind, message = error.get('type'), str(error.get('message','')).lower()
        if kind in {'AuthenticationError','PermissionDeniedError'}:
            return 'authentication_or_permission_failure'
        if kind == 'RateLimitError' and any(word in message for word in
                ('insufficient balance','insufficient_quota','quota exhausted','额度','余额')):
            return 'quota_exhausted'
    return None


def recheck_frozen_construction_candidates(source, destination, *, worker_index=0, workers=1):
    """Recheck unchanged programs with a separately frozen current Core, offline.

    Original generation, delivery gates and anonymous reviews remain observations
    of the original run. Execution and reference-contract results stay separate.
    Neither a model provider nor a new candidate is created here.
    """
    from application.confirmed_generation_context import project_confirmed_specification
    from plc.construction import apply_construction_check
    from plc.ir import build_plc_ir, is_plc_ir
    from simulator.bounded import check_confirmed_behavior
    source, destination = Path(source), Path(destination)
    if not 0 <= worker_index < workers:
        raise ValueError('Invalid worker partition')
    assert_frozen_code(destination)
    with zipfile.ZipFile(source/'materials_snapshot.zip') as archive:
        if (source/'experiment.json').read_bytes() != archive.read('experiment.json'):
            raise ValueError('Frozen materials changed')
    frozen = json.loads((source/'experiment.json').read_text(encoding='utf-8'))
    cases = {c['case_id']:c for c in frozen['cases']}
    result = []
    for index, task in enumerate(frozen['tasks']):
        if index % workers != worker_index:
            continue
        record = json.loads((source/'runs'/(task['run_id']+'.json')).read_text(encoding='utf-8'))
        case = cases[task['case_id']]
        spec = project_confirmed_specification(case['confirmed_spec'])
        first = record.get('first_candidate') or {}
        receipt = first.get('construction_binding') or (((record.get('output') or {}).get('generation') or {})
                  .get('first_pass_pipeline') or {}).get('construction_binding') or {}
        for stage in ('first','final'):
            path = destination/'checks'/(task['run_id']+'.'+stage+'.json')
            if path.exists():
                result.append(json.loads(path.read_text(encoding='utf-8'))['observation'])
                continue
            program = first.get('ladder') if stage=='first' else record.get('program') or record.get('ladder')
            binding = {'source_run_id':task['run_id'],'stage':stage,'source_version_id':record.get('version_id'),
                       'plc_model':case['plc_model']}
            if not program or stage=='first' and not first.get('structural_valid'):
                reason = 'candidate_not_delivered' if not program else 'candidate_structure_not_validated'
                execution = {'status':'not_delivered' if not program else 'unverified',
                    'program_version':binding,'checks':[],'violations':[],
                    'coverage':{'traces_tested':0,'maximum_scans':0},'activation_blocked':False,
                    'unsupported_or_missing':[{'reason':reason}],'checker_exceptions':[]}
                combined = copy.deepcopy(execution)
            else:
                if not is_plc_ir(program):
                    program = build_plc_ir(program,plc_model=case['plc_model'])
                execution = check_confirmed_behavior(program,spec,version_binding=binding,
                    frozen_time_traces=case.get('bounded_time_traces',()))
                combined = apply_construction_check(execution,receipt)
            observation = {**task,'stage':stage,'execution_status':execution['status'],
                'contract_status':combined['status'],'activation_blocked':combined['activation_blocked'],
                'violations':len(execution.get('violations',[])),
                'reference_violations':len(combined.get('construction_violations',[])),
                'coverage':execution['coverage'],'plc_model':execution['program_version']['plc_model']}
            write_new(path,{'observation':observation,'execution':execution,'with_reference_contract':combined})
            result.append(observation)
    return {'network_calls':0,'worker_index':worker_index,'workers':workers,'observations':result,
        'scope':'Post-run checks of unchanged frozen candidates; original live outcomes stay frozen'}


def replay_frozen_candidates(source, annotations, destination):
    """Read frozen candidates; independently authored relations/traces are inputs.

    The previous report is never rewritten and no new model request is made.
    Candidate programs are not used to derive expectations or initial memory.
    """
    from simulator.bounded import check_bounded_traces
    source, destination = Path(source), Path(destination)
    frozen = json.loads((source/'experiment.json').read_text(encoding='utf-8'))
    result = []
    for task in frozen['tasks']:
        path = source/'runs'/(task['run_id']+'.json')
        record = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        packet = annotations[task['case_id']]
        case = next(c for c in frozen['cases'] if c['case_id']==task['case_id'])
        spec = {**copy.deepcopy(case['confirmed_spec']), 'behavior_constraints':packet['behavior_constraints']}
        spec.setdefault('selected_approach',{})['construction_plan'] = {'instances':[], 'internal_ranges':[],
            'execution_context':packet['execution_context']}
        for stage in ('first','final'):
            program = (record.get('first_candidate') or {}).get('ladder') if stage=='first' else record.get('program') or record.get('ladder')
            if not program:
                report = {'status':'not_delivered','checks':[],'violations':[],'coverage':{'traces_tested':0}}
            else:
                report = check_bounded_traces(program,spec,packet.get('traces',[]),
                    frozen_time_traces=packet.get('frozen_time_traces',[]),
                    version_binding={'source_run_id':task['run_id'],'stage':stage,'source_version_id':record.get('version_id')})
            result.append({**task,'stage':stage,'status':report['status'],'violations':len(report['violations'])})
            write_new(destination/'checks'/(task['run_id']+'.'+stage+'.json'),report)
    summary = {'network_calls':0,'scheduled_candidates':len(frozen['tasks']), 'stages':{
        stage:{status:sum(r['stage']==stage and r['status']==status for r in result)
               for status in ('violated','no_violation_found_in_tested_scope','unverified','not_delivered')} for stage in ('first','final')},
        'records':result,'scope':'Independent post-hoc bounded relations; original study and static reviews stay frozen'}
    write_new(destination/'annotations.json',annotations)
    write_new(destination/'summary.json',summary)
    return summary


def run_construction_experiment(directory, provider, *, worker_index=0, workers=1):
    assert_frozen_code(directory)
    frozen = _frozen_materials(directory)
    identity = frozen['identity']
    source_frozen = None
    if identity.get('experiment') == 'construction-value-supplement':
        source = Path(identity['source_directory'])
        source_frozen = _frozen_materials(source)
        assert_construction_product_unchanged(source)
        _assert_source_observations(directory, source)
    for key,value in {'profile_id':provider.profile.get('id'),'profile_model':provider.profile.get('model'),
                      'endpoint':provider.profile.get('baseUrl'),'user_model_settings':provider.profile.get('userModelSettings')}.items():
        if identity[key] != value:
            raise ValueError('Saved model profile changed after freeze')
    cases = {c['case_id']:c for c in frozen['cases']}
    blocks = sorted({t['block'] for t in pending_construction_tasks(directory,frozen,worker_index=worker_index,workers=workers)})
    layout = directory/'execution_layout.json'
    try:
        write_new(layout, {'workers':workers})
    except FileExistsError:
        if json.loads(layout.read_text(encoding='utf-8')) != {'workers':workers}:
            raise ValueError('Worker partition changed after execution started')
    for task in frozen['tasks']:
        if task['block'] % workers == worker_index and (directory/'runs'/(task['run_id']+'.json')).exists() and not (directory/'blind'/(task['blind_id']+'.json')).exists():
            export_blind_candidate(directory,task,cases[task['case_id']],_read_record(directory,task))
    import application.generation_agent as agent
    for block in blocks:
        for task in [t for t in frozen['tasks'] if t['block']==block]:
            if (directory/'transport_stop.json').exists():
                return {'stopped':True, 'reason':'shared_transport_stop'}
            path = directory/'runs'/(task['run_id']+'.json')
            started = path.with_name(task['run_id']+'.started.json')
            if path.exists() or started.exists():
                continue
            try:
                write_new(started,{**task,'started_at_utc':datetime.now(timezone.utc).isoformat(),'worker':worker_index})
            except FileExistsError:
                continue
            case = cases[task['case_id']]
            delivery = 'instantiate' if task['arm']=='core_instantiation' else 'description'
            try:
                with (source_aligned_official_request(provider,source_frozen) if source_frozen else nullcontext()), patch.object(agent,'_construction_delivery',return_value=delivery), Journey(directory/'workspaces'/task['run_id'],provider,cases=list(cases.values()),expected_requests=frozen['expected_requests']) as journey:
                    project = journey.service.create_project(name=case['name'],plc_model=case['plc_model'],target_mode='ladder')
                    confirmation = journey.service.set_spec(project['id'],case['confirmed_spec'],None)
                    if not confirmation.get('valid') or confirmation['spec'] != case['confirmed_spec']:
                        raise ValueError('Frozen confirmation drifted')
                    record = journey.job({'kind':'generation','project_id':project['id'],'text':'按确认规格和已选方法生成程序。',
                                          'fresh_confirmed_generation':True,'construction_examples':False},
                                         case_id=case['case_id'],arm=task['arm'],repeat=task['repeat'],experiment_seed=identity['seed'])
            except Exception as error:
                record = {'case_id':case['case_id'],'arm':task['arm'],'attempts':[],'actual_requests':[],
                    'first_candidate':{},'job':{'status':'failed'},'wall_ms':None,
                    'runner_failure':{'type':type(error).__name__,'reason':str(error)[:300]}}
            record.update(task,category=case['category'],plc_model=case['plc_model'],worker=worker_index)
            record['provider_identity'] = {key:identity.get(key) for key in
                                          ('endpoint','profile_id','profile_model','user_model_settings')}
            write_new(path,record)
            export_blind_candidate(directory,task,case,record)
            reason = fatal_construction_transport_failure(record)
            if reason:
                try:
                    write_new(directory/'transport_stop.json', {'reason':reason,'run_id':task['run_id'],
                        'at_utc':datetime.now(timezone.utc).isoformat()})
                except FileExistsError:
                    pass
                return {'stopped':True, 'reason':reason}
    return {'stopped':False}


def _construction_summary_inputs(directory):
    local = json.loads((directory/'experiment.json').read_text(encoding='utf-8'))
    supplement = local['identity'].get('experiment') == 'construction-value-supplement'
    if not supplement:
        return local, {t['run_id']:(directory,local,t) for t in local['tasks']}, None
    _frozen_materials(directory)
    source = Path(local['identity']['source_directory'])
    _assert_source_observations(directory,source)
    original = _frozen_materials(source)
    if local['cases'] != original['cases']:
        raise ValueError('Supplement cases differ from original frozen materials')
    additions = {t['run_id']:t for t in local['tasks']}
    expected = {t['run_id'] for t in select_construction_supplement_tasks(source,original)}
    if len(additions) != len(local['tasks']) or set(additions) != expected:
        raise ValueError('Supplement inventory differs from authorized missing jobs')
    observations, tasks = {}, []
    for task in original['tasks']:
        if task['run_id'] in additions:
            new_task = additions[task['run_id']]
            if any(new_task[key] != task[key] for key in ('case_id','arm','repeat','block','run_id')):
                raise ValueError('Supplement changed source job identity')
            origin, experiment, chosen = directory,local,new_task
        else:
            origin, experiment, chosen = source,original,task
        tasks.append(chosen)
        observations[task['run_id']] = origin,experiment,chosen
    frozen = {**original,'tasks':tasks,'identity':{
        'experiment':'construction-value-combined','repeats':original['identity']['repeats'],
        'source':original['identity'],'supplement':local['identity'],
        'conclusion_scope':local['identity']['conclusion_scope']}}
    source_records = [r for t in original['tasks'] if (r:=_read_record(source,t)) is not None]
    starts = list((source/'runs').glob('*.started.json'))
    history = {'original_scheduled_jobs':len(original['tasks']), 'original_recorded_jobs':len(source_records),
        'original_completed_programs':sum(_completed_generation(r) for r in source_records),
        'original_transport_failed_jobs':sum(bool(r.get('transport_errors')) for r in source_records),
        'original_started_jobs':len(starts), 'original_started_without_result':sum(
            not (source/'runs'/p.name.replace('.started.json','.json')).exists() for p in starts),
        'original_observed_calls':sum(len(r.get('attempts',[])) for r in source_records),
        'original_observed_requests':sum(len(r.get('actual_requests',[])) for r in source_records),
        'supplement_scheduled_jobs':len(local['tasks']),
        'selection':'Original completed program preferred; otherwise the authorized official supplement',
        'interrupted_source_transport':'unknown; never inferred from a start marker'}
    return frozen, observations, history


def _provider_key(identity):
    return (identity.get('endpoint'),identity.get('profile_model'))


def _provider_label(identity):
    endpoint = str(identity.get('endpoint','')).rstrip('/')
    return ('modelscope' if endpoint == 'https://api-inference.modelscope.cn/v1' else
            'deepseek_official' if endpoint == 'https://api.deepseek.com' else 'unknown')


def _measured_construction_metrics(rows):
    # Zero/absent elapsed time and absent provider usage stay distinguishable.
    measured=[{**r,'end_to_end_ms':r['wall_ms'],'generation_status':(r.get('job') or {}).get('status'),
               'behavior':{'status':'not_covered'}} for r in rows if type(r.get('wall_ms')) in {int,float}]
    stats=summarize(measured)['groups'][rows[0]['arm']] if measured else {}
    # This experiment reviews the common-Core-bound program, not a separately
    # executed raw model response or a native simulator result.
    for name in ('behavior_verified','first_pass_semantic_correct','first_pass_usable','raw_model_semantic_correct'):
        stats[name] = None
    stats['raw_model_semantic_reviewed_runs'] = 0
    stats['latency_ms'] = _latency_summary([r['wall_ms'] for r in rows if type(r.get('wall_ms')) in {int,float}])
    stats['repair_calls'] = sum(max(0,len(r.get('attempts',[]))-1) for r in rows)
    stats['transport_failed_jobs'] = sum(bool(r.get('transport_errors')) for r in rows)
    for name in ('input_tokens','output_tokens','total_tokens'):
        values = [sum(a['usage'][name] for a in r['attempts']) for r in rows
                  if r.get('attempts') and all(type((a.get('usage') or {}).get(name)) is int for a in r['attempts'])]
        stats.setdefault(name,{'known_runs':0,'median':None})['total'] = sum(values) if values and len(values)==len(rows) else None
    return stats


def summarize_construction_experiment(directory):
    frozen, observations, history = _construction_summary_inputs(directory)
    tasks,cases = frozen['tasks'],{c['case_id']:c for c in frozen['cases']}
    if any((origin/'runs'/(t['run_id']+'.json')).exists() and not (origin/'reviews'/(t['blind_id']+'.json')).exists()
           for origin,experiment,t in observations.values()):
        raise ValueError('Complete anonymous reviews before unblinding')
    grades,records = [],[]
    audit, providers = [], {}
    for task in tasks:
        origin, experiment, selected = observations[task['run_id']]
        provider_identity = experiment['identity']
        providers[task['run_id']] = provider_identity
        path = origin/'runs'/(task['run_id']+'.json')
        record = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        review_path = origin/'reviews'/(task['blind_id']+'.json')
        review = json.loads(review_path.read_text(encoding='utf-8')) if review_path.exists() else {}
        if record:
            if (review.get('performed_by') != 'Codex' or review.get('human_signoff') is not False
                    or review.get('native_execution') != 'not_measured' or not review.get('completed_at')
                    or not review.get('method') or review.get('blind_id') != task['blind_id']):
                raise ValueError('Review requires honest anonymous static-review provenance')
            records.append(record)
            if record.get('provider_identity') and any(record['provider_identity'].get(k) != provider_identity.get(k)
                    for k in ('endpoint','profile_id','profile_model','user_model_settings')):
                raise ValueError('Recorded provider identity differs from frozen profile')
            expected = experiment['expected_requests'][task['case_id']][task['arm']]
            actual = record.get('actual_requests',[])
            audit.append({'run_id':task['run_id'],'one_request':len(actual)==1,'exact_frozen_request':actual == [expected],
                          'provider':_provider_label(provider_identity),
                          'confirmed_spec_unchanged': record.get('confirmed_spec_after') == cases[task['case_id']].get('confirmed_spec')
                              if 'confirmed_spec_after' in record else None})
        for stage in ('first','final'):
            candidate = (record.get('first_candidate') or {}) if record and stage=='first' else {'ladder':record.get('ladder')} if record else {}
            criteria = review.get(stage,[])
            required = {r['id'] for r in cases[task['case_id']]['acceptance']}
            if record and (not required or len(criteria) != len(required) or {r.get('id') for r in criteria} != required
                           or any(r.get('status') not in {'passed','failed','unverified'} or not r.get('evidence') for r in criteria)):
                raise ValueError('Every criterion must be reviewed exactly once with evidence')
            if not record:
                outcome = 'unverified' if candidate.get('ladder') else 'not_delivered' if record else 'missing'
            else:
                outcome = review_outcome(candidate,criteria)
                if record.get('transport_errors') and not candidate.get('ladder'):
                    outcome = 'unverified'
            static_outcome = outcome
            if record and stage == 'first' and ((record.get('first_candidate') or {}).get('behavior_check') or {}).get('activation_blocked'):
                if outcome == 'passed': outcome = 'failed'
            if record and stage == 'final' and (record.get('final_trace_check') or {}).get('activation_blocked'):
                if outcome == 'passed': outcome = 'failed'
            if stage=='final' and record and final_delivery_status(record) != 'delivered' and outcome=='passed':
                outcome = 'not_delivered'
            grades.append({**task,'stage':stage,'outcome':outcome,'static_outcome':static_outcome,
                           'provider':_provider_label(provider_identity),
                           'transport_failed':bool(record and record.get('transport_errors'))})
    by = {(g['case_id'],g['repeat'],g['arm'],g['stage']):g['outcome'] for g in grades}
    pairs=[]
    for case in cases.values():
        for repeat in range(frozen['identity']['repeats']):
            for stage in ('first','final'):
                baseline=by[(case['case_id'],repeat,CONSTRUCTION_ARMS[0],stage)]
                bound=by[(case['case_id'],repeat,CONSTRUCTION_ARMS[1],stage)]
                identities = [providers[next(t['run_id'] for t in tasks if t['case_id']==case['case_id']
                                  and t['repeat']==repeat and t['arm']==arm)] for arm in CONSTRUCTION_ARMS]
                same_endpoint = _provider_key(identities[0]) == _provider_key(identities[1])
                pairs.append({'case_id':case['case_id'],'category':case['category'],'repeat':repeat,'stage':stage,
                              'baseline':baseline,'instantiated':bound,'outcome':paired_outcome(baseline,bound),
                              'same_endpoint':same_endpoint,
                              'provider_pair':_provider_label(identities[0]) if same_endpoint else 'cross_endpoint'})
    contrast = {stage:{outcome:sum(p['stage']==stage and p['outcome']==outcome for p in pairs)
                for outcome in ('rescue','harm','both_pass','both_fail','undetermined')} for stage in ('first','final')}
    summaries={}
    for arm in CONSTRUCTION_ARMS:
        rows=[r for r in records if r['arm']==arm]
        stats=_measured_construction_metrics(rows)
        stats['raw_model_semantic_correct'] = None
        stats['raw_model_semantic_reviewed_runs'] = 0
        stats['latency_ms'] = _latency_summary([r['wall_ms'] for r in rows if type(r.get('wall_ms')) in {int,float}])
        for stage in ('first','final'):
            stats[stage+'_full_pass']=sum(g['arm']==arm and g['stage']==stage and g['outcome']=='passed' for g in grades)
            stats[stage+'_unverified']=sum(g['arm']==arm and g['stage']==stage and g['outcome']=='unverified' for g in grades)
            stats[stage+'_static_full_pass']=sum(g['arm']==arm and g['stage']==stage and g['static_outcome']=='passed' for g in grades)
            stats[stage+'_outcomes']={o:sum(g['arm']==arm and g['stage']==stage and g['outcome']==o for g in grades)
                                     for o in ('passed','failed','unverified','not_delivered','missing')}
        decision_checks = [(r.get('first_candidate') or {}).get('behavior_check') for r in rows]
        stats['confirmed_decision_changes'] = {
            'observed_jobs': sum(c is not None for c in decision_checks),
            'jobs_with_detected_violation': sum(bool(c and c.get('activation_blocked')) for c in decision_checks),
            'requirement_violations': sum(len(c.get('violations', [])) for c in decision_checks if c),
            'reference_violations': sum(len(c.get('construction_violations', [])) for c in decision_checks if c),
            'unverified_jobs':sum(not c or c.get('status')=='unverified' for c in decision_checks),
            'confirmation_mutations':sum(a.get('confirmed_spec_unchanged') is False for a in audit if
                next(t['arm'] for t in tasks if t['run_id']==a['run_id']) == arm),
        }
        stats['first_usable'] = sum(g['outcome']=='passed' and
            next((r.get('first_candidate') or {}).get('structural_valid', False) and
                 not ((r.get('first_candidate') or {}).get('behavior_check') or {}).get('activation_blocked',False)
                 for r in rows if r['run_id']==g['run_id']) for g in grades if g['arm']==arm and g['stage']=='first')
        stats['scheduled_jobs'] = sum(t['arm']==arm for t in tasks)
        stats['first_usable_rate'] = stats['first_usable']/stats['scheduled_jobs']
        stats['final_full_pass_rate'] = stats['final_full_pass']/stats['scheduled_jobs']
        stats['repair_calls'] = sum(max(0,len(r.get('attempts',[]))-1) for r in rows)
        stats['recorded_jobs'] = len(rows)
        stats['formal_delivery'] = {s:sum(final_delivery_status(r)==s for r in rows)
                                   for s in ('delivered','diagnostic_only','not_delivered','unverified')}
        stats['bounded_status_counts'] = {s:sum(((r.get('first_candidate') or {}).get('behavior_check') or {}).get('status')==s for r in rows)
                                         for s in ('violated','no_violation_found_in_tested_scope','unverified')}
        summaries[arm]=stats
    cross_cases = sorted({p['case_id'] for p in pairs if not p['same_endpoint']})
    cluster_grading = {(g['case_id'],g['repeat'],g['arm']):
        {s:('missing' if g['case_id'] in cross_cases else by[(g['case_id'],g['repeat'],g['arm'],s)])
         for s in ('first','final')} for g in grades}
    provider_metrics = {label:{arm:_measured_construction_metrics([r for r in records if r['arm']==arm and
                    _provider_label(providers[r['run_id']])==label]) for arm in CONSTRUCTION_ARMS}
                for label in sorted({_provider_label(v) for v in providers.values()})}
    for label, arms in provider_metrics.items():
        for arm, stats in arms.items():
            selected = [g for g in grades if g['provider']==label and g['arm']==arm]
            stats['whole_program'] = {stage:{outcome:sum(g['stage']==stage and g['outcome']==outcome for g in selected)
                for outcome in ('passed','failed','unverified','not_delivered','missing')} for stage in ('first','final')}
            stats['whole_program_static'] = {stage:{outcome:sum(g['stage']==stage and g['static_outcome']==outcome for g in selected)
                for outcome in ('passed','failed','unverified','not_delivered','missing')} for stage in ('first','final')}
    if history:
        for stats in summaries.values():
            for name in ('latency_ms','median_end_to_end_ms','p95_end_to_end_ms','median_reasoning_tokens',
                         'reasoning_usage_known_runs','input_tokens','output_tokens','total_tokens','prompt_cache'):
                stats.pop(name,None)
        history['supplement_recorded_jobs'] = sum(_provider_label(providers[r['run_id']])=='deepseek_official' for r in records)
        history['supplement_observed_calls'] = sum(len(r.get('attempts',[])) for r in records if
                                                  _provider_label(providers[r['run_id']])=='deepseek_official')
    return {'schema_version':2,'identity':frozen['identity'],'scheduled_jobs':len(tasks),'recorded_jobs':len(records),
        'reviewed_jobs':sum((origin/'reviews'/(t['blind_id']+'.json')).exists() for origin,experiment,t in observations.values()),
        'arms':summaries,'contrast_counts':contrast,'pairs':pairs,'grading':grades,'request_audit':audit,
        'call_history':history, 'provider_metrics':provider_metrics,
        'pair_provider_counts':{label:sum(p['stage']=='first' and p['provider_pair']==label for p in pairs)
                                for label in ('modelscope','deepseek_official','cross_endpoint')},
        'same_endpoint_contrast_counts':{stage:{o:sum(p['stage']==stage and p['same_endpoint'] and p['outcome']==o for p in pairs)
            for o in ('rescue','harm','both_pass','both_fail','undetermined')} for stage in ('first','final')},
        'case_cluster_tests':{stage:{**case_cluster_test(cluster_grading,
             list(cases.values()), frozen['identity']['repeats'], CONSTRUCTION_ARMS[1], stage,
             baseline_arm=CONSTRUCTION_ARMS[0]), 'cross_endpoint_cases':cross_cases} for stage in ('first','final')},
        'response_models':sorted({p['model'] for r in records for p in r.get('provider_results',[]) if p.get('model')}),
        'category_counts':{category:{stage:{o:sum(p['category']==category and p['stage']==stage and p['outcome']==o for p in pairs)
                              for o in ('rescue','harm','both_pass','both_fail','undetermined')} for stage in ('first','final')}
                          for category in ('ordinary','boundary','complex')},
        'limitations':['12 requirement clusters; repeats are not independent requirements','static anonymous Codex review; no human signoff',
                       'bounded execution evidence only; no native simulation or real PLC acceptance','missing provider usage/cache stays missing',
                       'cross-endpoint pairs are descriptive only; latency and usage stay separated by endpoint']}


def construction_main(args,cases,directory):
    if args.phase=='summary':
        result=summarize_construction_experiment(directory)
        path=directory/('summary-'+uuid.uuid4().hex[:8]+'.json')
        write_new(path,result)
        print(json.dumps({'output':str(path),'recorded_jobs':result['recorded_jobs'],'contrast_counts':result['contrast_counts']},ensure_ascii=False))
        return 0
    if args.phase=='generation' and not args.live:
        count = (len(select_construction_supplement_tasks(args.supplement_from,
                 _frozen_materials(args.supplement_from))) if getattr(args,'supplement_from',None)
                 else len(cases)*args.repeats*2)
        print(json.dumps({'live':False,'scheduled_jobs':count,'network_calls':0}))
        return 0
    from storage.config import load_full_config,get_model_profile
    from model_runtime.provider import get_active_provider
    config=load_full_config()
    profile=get_model_profile(config,args.profile_id)
    supplement = getattr(args,'supplement_from',None)
    endpoint, model = (('https://api.deepseek.com','deepseek-flash') if supplement else
                       ('https://api-inference.modelscope.cn/v1','deepseek-ai/DeepSeek-V4.1-Flash'))
    if str(profile.get('baseUrl','')).rstrip('/')!=endpoint or profile.get('model')!=model:
        raise ValueError('Construction diagnostic requires its explicitly selected saved DeepSeek V4.1 profile')
    provider=get_active_provider({**config,'activeModelProfileId':args.profile_id})
    directory.mkdir(parents=True,exist_ok=True)
    if args.phase in {'prepare','preflight'}:
        result = (prepare_construction_supplement(directory,supplement,provider) if supplement else
                  prepare_construction_experiment(directory,cases,provider,repeats=args.repeats,seed=args.seed))
        print(json.dumps(result,ensure_ascii=False))
    elif args.phase=='generation':
        identity = _frozen_materials(directory)['identity']
        if bool(supplement) != (identity.get('experiment') == 'construction-value-supplement') or supplement and Path(identity['source_directory']).resolve() != supplement.resolve():
            raise ValueError('Explicit supplement source must match the frozen run')
        run_construction_experiment(directory,provider,worker_index=args.worker_index,workers=args.workers)
    else:
        raise ValueError('Construction diagnostic uses prepare, generation and summary')
    return 0
