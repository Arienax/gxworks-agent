"""Freeze the completed FBD application module and native failure witnesses.

Raw projects and proprietary source-derived controls remain in the local
archive. Archive checks compare bytes directly; no digest verification.
"""
from __future__ import annotations

import base64
from collections import Counter
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'research/experiments/sfc-graph-20260926/public-corpus-discovery'
A=P/'application-fbd-v2-20261002-v1'
M=P/'cpu-fbd-matrix-20261002-v1'
PUBLIC=ROOT/'research/evidence/gxw-fbd-application-v2-20261002.zip'
LOCAL=P/'gxw-fbd-application-v2-20261002-local.zip'
RESULT=ROOT/'research/results/gxw-fbd-application-v2-20261002.json'
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'research')]
from gxw.object_model import read_project_context
from gxw.container_writer import validate_cfb_streams
from gxw.project_metadata import logical_mapping
from program_check_evidence import analyze_directory


def json_data(value):return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')


def events(directory):
    return [json.loads(line) for line in (directory/'native-events.jsonl').read_text(encoding='utf-8-sig').splitlines()]


def final_attempt(parent):
    candidates=sorted(parent.glob('*attempt-*'),key=lambda p:int(p.name.rsplit('-',1)[-1]))
    return next(d for d in reversed(candidates) if (d/'control-result.json').is_file()
                and json.loads((d/'control-result.json').read_text())['returncode']!=5)


def source_witness(raw):
    context=read_project_context(raw,'FBD_MATRIX.Program.pou')
    return {'cpu':context.sources.cpu,'program':context.program.logical_name,
        'program_base64':base64.b64encode(context.program.raw).decode(),
        'declarations':{name:base64.b64encode(doc.raw).decode() for name,doc in context.declarations.items()},
        'projection':context.object_model()}


def phase_witness(name,directory,expected):
    native=events(directory)
    event_file=next((directory/n for n in ('observation-events.jsonl','events.jsonl') if (directory/n).exists()),None)
    observations=[json.loads(line) for line in event_file.read_text(encoding='utf-8').splitlines()] if event_file else []
    names={tuple(e['id']):e['name'] for e in native if e.get('operation')=='NativeObject' and e.get('name')}
    resources={e['name']:[base64.b64encode((directory/f"pcode-{e['index']}-{i}.bin").read_bytes()).decode() for i in range(3)]
               for e in native if e.get('operation')=='Resource'}
    return {'case':name,'directory':str(directory.relative_to(ROOT)),
        'native_events':[e for e in native if e.get('operation') in {
            'Progress','Resource','GetPCode','PCodeCount','Workspace.GetProgramCheckCollection','InventoryCount','NativeObject',
            'Workspace.UpdatePCodeBeforeProgramCheck','ProgramCheckTarget','ProgramCheckCompleted','ProgramCheckProgress',
            'ProgramCheckProgress.GetProgress','SaveProject','NativeExport','ExportSkipped'}],
        'observations':[e for e in observations if e.get('type')=='error' or e.get('payload',{}).get('event') in {
            'workspace-pcode-read','check-start','backend-check-progress','diagnostic-projection-failure','observation-error',
            'projection-object-lookup','projection-argument-transfer'}],
        'generated':[{'id':list(identity),'channels_base64':resources[name]} for identity,name in names.items() if name in resources],
        'selection':{'status':'not_observed'},'expected':expected}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    for path in (PUBLIC,LOCAL,RESULT):
        if path.exists():raise FileExistsError('Frozen checkpoint already exists: '+str(path))
    apps=json.loads((A/'application-results.json').read_text())
    native=json.loads((A/'native-results.json').read_text())
    replay=json.loads((A/'current-core-replay.json').read_text())
    gaps=json.loads((A/'source-gap-controls/results.json').read_text())
    assert len(replay)==67 and all(r['candidate_bytes_exact'] for r in replay)
    assert len(native)==55 and all(r['status']=='passed' for r in native)
    assert len(gaps)==4 and all(r['status']=='bounded_edit_passed' for r in gaps)
    witness_rows=[]
    for cpu in ('FX3U-FX3UC','Q03UDV','L02'):
        witness_rows.append(phase_witness(cpu+'-current',P/'application-current-check-20261002-v1'/(cpu+'-attempt-1'),
            {'correspondence':'current','public_check':'passed','projection':'completed'}))
        parent=P/'owned-diagnostic-controls-20261002-v1'
        directory=final_attempt_for_cpu(parent,cpu)
        witness_rows.append(phase_witness(cpu+'-duplicate-output',directory,
            {'correspondence':'current','public_check':'incomplete','projection':'failed'}))
    witness_rows.append(phase_witness('Q03UDV-current-compiler-stale-workspace',final_attempt(P/'owned-q-stale-check-saved-20261002-v1'),
        {'correspondence':'stale','public_check':'passed','projection':'completed'}))
    for mode,expected in (('legacy',{'correspondence':'stale','public_check':'passed','projection':'completed'}),
                          ('fresh',{'correspondence':'current','public_check':'rejected','projection':'completed'})):
        witness_rows.append(phase_witness('FX3G-duplicate-coil-'+mode,
            P/('fresh-check-matrix-20260930/duplicate-coil-'+mode+'/compile/native-1'),expected))
    empty=P/'real-sfc-empty-task-cache/native-1'
    raw=(empty/'input.gxw').read_bytes();outer=validate_cfb_streams(raw)
    mapping=logical_mapping(outer['projectdatalist.xml']);inner=validate_cfb_streams(outer['_hdb'])
    task=inner[mapping['MAIN.tsk']]
    # The observed empty task keeps its source framing and final entry count.
    assert task[-4:]==b'\0'*4
    frontend=json.loads((empty/'frontend-snapshot.json').read_text(encoding='utf-8-sig'))
    task_frontend=next(r['data'] for r in frontend['build']['relocations'] if r['offset']==36)
    witness_rows.append({'case':'SFC-empty-task-cache','directory':str(empty.relative_to(ROOT)),
        'native_events':[e for e in events(empty) if e.get('operation') in ('Progress','Resource','SaveProject','NativeExport')],
        'observations':[],'generated':[],
        'selection':{'status':'empty','empty_task':True,'selected_sources':[],
            'task_base64':base64.b64encode(task).decode(),'native_task_frontend':task_frontend},
        'expected':{'correspondence':'not_observed','public_check':'not_requested','projection':'not_completed'}})
    public_files={'check-witnesses.json':json_data(witness_rows)}
    tracked_paths=[Path(line) for line in __import__('subprocess').check_output(['git','diff','--name-only'],cwd=ROOT).decode().splitlines()]
    tracked_paths += [Path(p) for p in ('src/gxw/callable_sources.py','src/gxw/library_sources.py',
        'tests/fixtures/gxw_fbd_source_library.json','tests/fixtures/gxw_fbd_library_cpu_sections_native.json',
        'research/program_check_evidence.py','research/freeze_gxw_fbd_application.py')]
    for path in tracked_paths:
        if path.as_posix().startswith(('src/','tests/','web/')) or path.name in ('program_check_evidence.py','freeze_gxw_fbd_application.py'):
            public_files['source/'+path.as_posix()]=(ROOT/path).read_bytes()
    for name in ('validate_application_fbd_v2.py','verify_application_fbd_v2_final.py',
        'probe_application_check_resources.py','probe_owned_projection_controls.py','probe_q_projection_name_control.py','TraceCurrentCheckResources.js'):
        public_files['tools/'+name]=(P/name).read_bytes()
    source_rows=[]
    for row in apps:
        if row['application_status']!='passed':continue
        directory=Path(row['directory'])
        record={'cpu':row['cpu'],'group':row['group'],
            'original':source_witness((Path(row['prior_native_case'])/'candidate.gxw').read_bytes()),
            'candidate':source_witness((directory/'candidate.gxw').read_bytes())}
        saved=directory/'native-compile/native-saved.gxw';cold=directory/'native-reopen/native-saved.gxw'
        if saved.exists():record['native_saved']=source_witness(saved.read_bytes())
        if cold.exists():record['native_cold']=source_witness(cold.read_bytes())
        source_rows.append(record)
    public_files['application-source-witnesses.json']=json_data(source_rows)
    matrix=[]
    for group,name in (('full','module-combined-v2-results.json'),('type-control','module-fx-type-boundaries-results.json')):
        for r in json.loads((M/name).read_text()):
            matrix.append({'group':group,'cpu':r['cpu'],'status':r['status'],'directory':r['directory'],
                'case':r['case'],'source_cpu':r.get('source_cpu'),'write_mode':r.get('write_mode'),
                'core_edit_failure':r.get('core_edit_failure'),
                'factory_failures':r['factory'].get('failures',[]),
                'native_diagnostics':r.get('native',{}).get('diagnostics',[])})
    public_files['cpu-module-boundaries.json']=json_data(matrix)
    public_files['application-results.json']=json_data(apps)
    public_files['native-results.json']=json_data(native)
    public_files['current-core-replay.json']=json_data(replay)
    public_files['source-gap-controls.json']=json_data(gaps)
    public_files['q-name-audit.json']=(P/'q-projection-frozen-corpus-name-audit-20261002.json').read_bytes()
    name_control=final_attempt(P/'q-diagnostic-name-control-20261002-v1')
    public_files['q-name-control.json']=(name_control/'control-result.json').read_bytes()
    local_files={}
    directories=set()
    for row in apps:
        if row.get('directory'):directories.add(Path(row['directory']))
        if row.get('prior_native_case'):directories.add(Path(row['prior_native_case']))
    for row in gaps:directories.add(Path(row['directory']))
    directories.update(Path(row['directory']) for row in witness_rows)
    directories.add(name_control)
    directories.add(P/'application-current-check-20261002-v1/Q03UDV-diagnostic-negative-attempt-1')
    directories.add(P/'q-label-contact-native-20261001-v1/case-00/candidate-check-1')
    for directory in directories:
        if not directory.is_absolute():directory=ROOT/directory
        for selected in (directory,directory/'compile-native',directory/'native-compile',directory/'native-reopen'):
            if not selected.exists():continue
            for pattern in ('*.gxw','*.json','*.jsonl','*.cs','*.js','pcode-*.bin','check-read-*.bin','*stderr.bin','*stdout.bin'):
                for path in selected.glob(pattern):
                    if path.is_file():local_files[path.relative_to(ROOT).as_posix()]=path.read_bytes()
    # Include all failed startup attempts in the new diagnostic controls.
    for base in ('owned-diagnostic-controls-20261002-v1','owned-q-stale-check-20261002-v1','owned-q-stale-check-saved-20261002-v1'):
        for path in (P/base).glob('*/*'):
            if path.is_file() and path.suffix in ('.json','.jsonl','.cs','.js','.gxw','.bin'):
                local_files[path.relative_to(ROOT).as_posix()]=path.read_bytes()
    for path,contents in ((PUBLIC,public_files),(LOCAL,local_files)):
        with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for name,data in sorted(contents.items()):archive.writestr(name,data)
        with zipfile.ZipFile(path) as archive:
            assert len(archive.namelist())==len(contents)
            assert all(archive.read(name)==data for name,data in contents.items())
    report={
        'schema':1,'date':'2026-10-02','status':'application-integrated/native-validated-bounded-module',
        'baseline_commit':__import__('subprocess').check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
        'scope':'Owned offline source-bound FBD v2 application module; exact native CPU options, not all physical models',
        'application':{'cases':len(apps),'statuses':dict(Counter(r['application_status'] for r in apps)),
            'final_core_byte_replay':{'cases':len(replay),'exact':sum(r['candidate_bytes_exact'] for r in replay)},
            'source_gap_controls':{'cases':len(gaps),'statuses':dict(Counter(r['status'] for r in gaps))}},
        'native':{'distinct_cpu_options':len(native),'statuses':dict(Counter(r['status'] for r in native)),
            'cpu_options':[r['cpu'] for r in native],
            'phases':'Compile, publication, public targets/checks, diagnostics, save, cold-reopen code and graph/labels are separate',
            'checker_read_trace_scope':['FX3U/FX3UC','Q03UDV','L02'],
            'checker_read_limit':'The other 52 CPU options have compile/check/save/reopen observations without read instrumentation'},
        'diagnostic_projection':{'adapter_version':'1.635.0.1','adapter_rva':'0x8e48',
            'workspace_lookup_rva':'0x793aa','lookup_slot':1784,'backend_instance_kind':6,'lookup_kind':32,
            'source_collection':25,'failed_lookup_code':'0x2d010025',
            'frozen_full_check_pairs':26,'frozen_failures_audited':52,'missing_same_name_pous':52,
            'name_control':'One native POU rename to MAIN; all three PCode channels identical; public check completes with rejection',
            'owned_duplicate_output_cpu_options':['FX3U/FX3UC','Q03UDV','L02'],
            'limit':'No vendor return value is replaced. Rename is a mechanism control; source locations are not certified.'},
        'failure_chain':{'tool':'research/program_check_evidence.py','public_frozen_cases':len(witness_rows),
            'q_stale_control':'Current compiler buffers differ from checker workspace reads; public check passes old code',
            'sfc_empty_task_control':'100 percent completion plus returned cached code does not establish source conversion'},
        'verification':'Archived entries and final Core candidates compared as raw bytes; no new digest verification',
        'archives':{'public':{'path':str(PUBLIC.relative_to(ROOT)),'entries':len(public_files),'bytes':PUBLIC.stat().st_size},
                    'local':{'path':str(LOCAL.relative_to(ROOT)),'entries':len(local_files),'bytes':LOCAL.stat().st_size}},
        'limits':['No PLC or simulator execution. BOOL runtime counterexamples are not resolved by this checkpoint.',
            'Unknown framed record preservation is an application regression; arbitrary unknown native objects are not certified.',
            'Full module rejects and factory failures remain in the CPU boundary witness; narrow type controls do not widen full-module claims.',
            'Native source/task/config selection proof is not yet automatic in the generic check classifier.',
            'FBD tools prepare candidates with gx_compile=not_run; native checks are separate offline research observations.'],
    }
    RESULT.write_bytes(json_data(report))
    print(json.dumps({'public_entries':len(public_files),'local_entries':len(local_files),'public_bytes':PUBLIC.stat().st_size,
        'local_bytes':LOCAL.stat().st_size,'check_witnesses':len(witness_rows),'report':str(RESULT.relative_to(ROOT))}),flush=True)


def final_attempt_for_cpu(parent,cpu):
    return next(path for path in reversed(sorted(parent.glob(cpu+'-attempt-*'),key=lambda p:int(p.name.rsplit('-',1)[-1])))
                if (path/'control-result.json').is_file() and json.loads((path/'control-result.json').read_text())['returncode']!=5)


if __name__=='__main__':main()
