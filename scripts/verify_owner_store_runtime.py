# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Bounded, read-only owner-store comparison in two fresh subprocesses.

Never enables a candidate, loads a large gzip corpus, invokes a model/native PLC,
or scans all owner facts. A passed result covers only the named sample set.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
MODELS=('FX3U','Q03UDV','L02')
RULES=(('FX3U','DPLSY','constraints.numeric_ranges','pulse_operand_outside_form_range',({'$1':0},{'$1':-1},{})),
       ('Q03UDV','EXPT','constraints.predicate','power_base_must_be_positive',({'S1':2},{'S1':0},{})),
       ('L02','EXPT','constraints.predicate','power_base_must_be_positive',({'S1':2},{'S1':0},{})))


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _file_hash(path):
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):digest.update(block)
    return digest.hexdigest()


def _identity(path):
    s=path.stat();return(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


def _sealed(path):
    if any(Path(str(path)+suffix).exists()for suffix in ('-wal','-shm','-journal')):
        raise ValueError('Database must be a sealed inactive snapshot')


def process_memory():
    """Report platform counters honestly; unavailable measurements remain null."""
    if sys.platform=='win32':
        try:
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(n,ctypes.c_size_t)for n in (
                    'PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
                    'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
            kernel=ctypes.WinDLL('kernel32',use_last_error=True);api=ctypes.WinDLL('psapi',use_last_error=True)
            kernel.GetCurrentProcess.restype=wintypes.HANDLE;kernel.GetCurrentProcess.argtypes=[]
            api.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
            api.GetProcessMemoryInfo.restype=wintypes.BOOL
            counters=Counters();counters.cb=ctypes.sizeof(counters)
            if not api.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(counters),counters.cb):raise OSError('memory API unavailable')
            return {'source':'Windows GetProcessMemoryInfo (current process)','working_set_bytes':counters.WorkingSetSize,
                    'peak_working_set_bytes':counters.PeakWorkingSetSize,'peak_rss_bytes':None,'unavailable_reason':None}
        except (ImportError,AttributeError,OSError):
            return {'source':'Windows GetProcessMemoryInfo','working_set_bytes':None,'peak_working_set_bytes':None,
                    'peak_rss_bytes':None,'unavailable_reason':'windows_process_counter_unavailable'}
    try:
        import resource
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {'source':'resource.getrusage(RUSAGE_SELF).ru_maxrss','working_set_bytes':None,'peak_working_set_bytes':None,
                'peak_rss_bytes':int(peak if sys.platform=='darwin'else peak*1024),'unavailable_reason':None}
    except (ImportError,AttributeError,OSError):
        return {'source':None,'working_set_bytes':None,'peak_working_set_bytes':None,'peak_rss_bytes':None,
                'unavailable_reason':'platform_counter_unavailable'}


def _closure(facts,roots):
    by_id={f['id']:f for f in facts};seen=set();active=set()
    if len(by_id)!=len(facts):raise ValueError('Duplicate fact identity')
    def visit(identity):
        if identity in active or identity not in by_id:raise ValueError('Invalid selected dependency closure')
        if identity in seen:return
        active.add(identity)
        for dep in by_id[identity].get('depends_on',[]):visit(dep)
        active.remove(identity);seen.add(identity)
    for identity in roots:visit(identity)
    return [f for f in facts if f['id']in seen]


def _worker(manifest):
    manifest=Path(manifest).resolve();before_manifest=_identity(manifest)
    sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
    started=time.perf_counter()
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry,generation_app_instr_mnemonics
    store=registry._definition_owner_store
    if store is None or store.manifest_path!=manifest:raise ValueError('Explicit manifest was not loaded')
    if store._db.execute('PRAGMA query_only').fetchone()[0]!=1:raise ValueError('Store connection is not query-only')
    db=store.database_path;before_db=_identity(db);_sealed(db)
    attach={'seconds':time.perf_counter()-started,'memory':process_memory(),'store':store.stats(),
            'typed_owner_count':registry.loaded_definition_owner_count}
    # Runtime attachment checks identity/structure and reads owners on demand.
    # The explicit offline digest comparison runs after the selected requests.
    manifest_data=json.loads(manifest.read_text(encoding='utf-8'))
    semantics={'owner_count':store.owner_count,'menus':{},'owners':{},'constraints':[], 'language':{}}
    gaps=[];metrics={'attach':attach};typed_before=registry.loaded_definition_owner_count
    if typed_before or any(attach['store'].get(k,0) for k in ('hydrated_owner_count','owner_lookup_count','source_load_count','value_load_count','field_load_count')):
        gaps.append('fresh_attach_eagerly_loaded_facts')
    try:
        start=time.perf_counter()
        for model in MODELS:
            menu=generation_app_instr_mnemonics(model)
            semantics['menus'][model]={'count':len(menu),'sha256':_hash(menu),'contains_MOV':'MOV'in menu}
            if not menu or 'MOV'not in menu:gaps.append('required_generation_menu_missing:'+model)
        metrics['menus']={'seconds':time.perf_counter()-start,'memory':process_memory(),'store':store.stats(),
                          'typed_owner_count':registry.loaded_definition_owner_count}
        if store.stats()['hydrated_owner_count']!=attach['store']['hydrated_owner_count']or registry.loaded_definition_owner_count!=typed_before:
            gaps.append('generation_menu_eagerly_hydrated_facts')
        from knowledge.fact_selection import project_packet
        from plc.instruction_constraints import check_fact_constraints
        first=[];warm=[];entries={}
        for model,opcode in [(m,'MOV')for m in MODELS]+[(m,o)for m,o,*_ in RULES]:
            key=model+':'+opcode;start=time.perf_counter();entry=registry.definition_entry(opcode,cpu=model);first.append(time.perf_counter()-start)
            if not entry or not entry.get('facts'):
                gaps.append('required_owner_missing:'+key);semantics['owners'][key]=None;continue
            entries[(model,opcode)]=entry
            semantics['owners'][key]={'facts':len(entry['facts']),'sha256':_hash(entry),
                                      'call_available':entry.get('call_available')}
            if opcode=='MOV':
                packet=project_packet({'kind':'instruction','target':opcode,'scope':{'models':[model]},'value':entry},
                    plc_model=model,language='st',aspects=['parameters'])
                selected=packet['value']['facts'];count=sum(f['dimension'].startswith('parameters.')for f in selected)
                semantics['owners'][key]['parameter_selection']={'count':count,'sha256':_hash(packet)}
                if not count:gaps.append('parameter_selection_empty:'+key)
                form=registry.resolve_form(opcode,cpu=model)
                if form is None:gaps.append('registered_MOV_not_callable:'+model)
                cached_before=store.stats().copy()
                for _ in range(5):
                    start=time.perf_counter();again=registry.resolve_form(opcode,cpu=model);warm.append(time.perf_counter()-start)
                    if again is None:raise ValueError('Warm callable form disappeared')
                if store.stats()!=cached_before:gaps.append('warm_typed_cache_reread:'+model)
        for model,opcode,dimension,rule,cases in RULES:
            entry=entries.get((model,opcode));key=model+':'+opcode
            if entry is None:continue
            roots=[f['id']for f in entry['facts']if f.get('dimension')==dimension and f.get('value',{}).get('rule')==rule]
            if not roots:gaps.append('required_constraint_root_missing:'+key);continue
            closure=_closure(entry['facts'],roots)
            for values,expected in zip(cases,('passed','failed','unverified')):
                report=check_fact_constraints(entry['facts'],values,target=opcode,plc_model=model,language='st',root_ids=roots)
                semantics['constraints'].append({'model':model,'opcode':opcode,'expected':expected,'status':report['status'],
                    'selected_root_count':len(roots),'closure_fact_count':len(closure),'closure_sha256':_hash(closure),'report_sha256':_hash(report)})
                if report['status']!=expected:gaps.append('constraint_sample_not_exercised:'+key+':'+expected)
        # Only these explicit language identities/aliases, not a broad corpus scan.
        from knowledge import index_access
        from knowledge.structured_facts import structured_fact_targets
        from plc.target_identity import reference_target_identity,target_members
        from unittest.mock import patch
        with patch.object(index_access,'index_path',return_value=db):
            for target,model in [('ST.OPERATOR.POWER','FX3U'),('FBD.TIMERS','FX3U'),('FBD.TIMERS','Q03UDV')]:
                feature=registry.language_feature(target,cpu=model)
                semantics['language'][target+':'+model]=None if feature is None else {'facts':len(feature.get('facts',[])),'sha256':_hash(feature)}
                if model=='FX3U'and(not feature or not feature.get('facts')):gaps.append('required_language_feature_missing:'+target)
                if target=='FBD.TIMERS'and model=='Q03UDV'and feature is not None:gaps.append('timer_cross_model_boundary_not_exercised')
            for query,model,language,target in [('**','FX3U','st','ST.OPERATOR.POWER'),('FBD timer resolution','FX3U','fbd','FBD.TIMERS'),('FBD timer resolution','Q03UDV','fbd',None)]:
                targets=structured_fact_targets(query,plc_model=model,language=language)['language_features']
                semantics['language']['alias:'+query+':'+model]=targets
                if target is not None and target not in targets:gaps.append('required_alias_not_resolved:'+model+':'+language)
                if target is None and 'FBD.TIMERS'in targets:gaps.append('alias_cross_model_boundary_not_exercised')
        search_connection=getattr(index_access._thread_state,'connection',None)
        if search_connection is not None and search_connection.execute('PRAGMA query_only').fetchone()[0]!=1:
            raise ValueError('Search connection is not query-only')
        index_access.close_thread_connection()
        semantics['target_boundaries']={'L02_merged':target_members('L02/L02-P'),
            'invalid_merge_rejected':reference_target_identity('FX3U/FX1S')is None}
        if not semantics['target_boundaries']['invalid_merge_rejected']:gaps.append('invalid_model_merge_accepted')
        metrics['requests']={'first_owner_lookup_seconds':first,'warm_typed_lookup_median_seconds':statistics.median(warm)if warm else None,
            'warm_typed_samples':len(warm),'store':store.stats(),'typed_owner_count':registry.loaded_definition_owner_count,'memory':process_memory(),
            'explicit_distinct_owner_keys':len(entries)}
        start=time.perf_counter();end_hash=_file_hash(db)
        metrics['post_validation_hash_seconds']=time.perf_counter()-start
        if end_hash!=manifest_data['database']['sha256']:raise ValueError('Database digest changed')
        _sealed(db)
        unchanged=before_manifest==_identity(manifest)and before_db==_identity(db)and store.current()
        if not unchanged:raise ValueError('Snapshot changed during verification')
        if len(semantics['constraints'])!=9:gaps.append('required_three_state_sample_count_not_met')
        return {'status':'unverified'if gaps else 'passed','coverage_gaps':gaps,'semantics':semantics,'measurements':metrics,
                'input_identity':{'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
                    'database_sha256':end_hash,'database_bytes':manifest_data['database']['bytes'],
                    'database_path_sha256':_hash(str(db)),'manifest_path_sha256':_hash(str(manifest)),'database_file_identity':list(before_db[:2]),
                    'runtime_serialization':dict(store._db.execute('SELECT key,value FROM meta')).get('runtime_serialization')},
                'input_unchanged':True,'hash_verified_on_attach':False,'hash_verified_after_requests':True,'readonly_connections':True,'sidecars_absent':True}
    finally:
        store.close()


def _run_child(manifest,timeout=180):
    env=dict(os.environ)
    env['GXW2_INSTRUCTION_OWNER_MANIFEST']=str(Path(manifest).resolve())
    env['GXW2_INSTRUCTION_CATALOG']=str(ROOT/'resources/instructions/mitsubishi')
    try:
        result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--_worker',str(Path(manifest).resolve())],
            env=env,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'status':'failed','coverage_gaps':['worker_timed_out']}
    if result.returncode not in (0,3):return {'status':'failed','coverage_gaps':['worker_process_failed'],'worker_exit_code':result.returncode}
    try:return json.loads(result.stdout)
    except ValueError:return {'status':'failed','coverage_gaps':['invalid_worker_report']}


def _same_input(source,candidate):
    a=source.get('input_identity',{});b=candidate.get('input_identity',{})
    return any(a.get(key) and a.get(key)==b.get(key) for key in ('manifest_path_sha256','database_path_sha256','database_file_identity'))


def compare_results(source,candidate):
    if not all(isinstance(row,dict)and isinstance(row.get('semantics'),dict)and row['semantics'] for row in (source,candidate)):return 'failed'
    if source.get('status')=='failed'or candidate.get('status')=='failed':return 'failed'
    if source.get('semantics')!=candidate.get('semantics'):return 'failed'
    if source.get('status')!='passed'or candidate.get('status')!='passed':return 'unverified'
    if _same_input(source,candidate):return 'unverified'
    return 'passed'


def verify(source_manifest,candidate_manifest):
    tracked=[Path(__file__).resolve(),*(ROOT/'src'/name for name in ('plc/instructions.py','plc/instruction_owner_store.py','plc/instruction_constraints.py','knowledge/fact_selection.py','knowledge/structured_facts.py','knowledge/index_access.py')),*sorted((ROOT/'resources/instructions/mitsubishi').glob('*.json'))]
    def fingerprint():return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()for p in tracked}
    before=fingerprint()
    source=_run_child(source_manifest);candidate=_run_child(candidate_manifest)
    if fingerprint()!=before:raise ValueError('Verification code or seed catalogue changed')
    return {'verification_schema':1,'status':compare_results(source,candidate),'release_accepted':False,
        'candidate_enabled':False,'comparison_reason':'same_input'if _same_input(source,candidate)else 'representative_samples','sample_scope':{'models':list(MODELS),'distinct_owner_keys':6,'constraint_cases':9},'source':source,'candidate':candidate,'code_and_seed_sha256':before,
        'semantic_samples_equal':source.get('semantics')is not None and source.get('semantics')==candidate.get('semantics'),
        'limitations':['Representative runtime acceptance only; not all owner facts are hydrated. Same manifest/database is unverified.',
            'Language aliases/model filtering are checked, not BM25/FTS ranking. Fingerprints cover the listed modules and seed files.',
            'Constraint statuses cover selected rule roots and complete dependency closures, not whole instruction completeness.',
            'Fresh Python processes, not flushed OS/disk caches; offline database hash verification follows the selected requests.',
            'Store counters are cumulative read events, not distinct owners or total operating-system I/O.',
            'Windows working-set counters are not Linux RSS and are reported under different names.',
            'No native PLC/compiler/model invocation, deployment change, or new hardware support claim.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-manifest',type=Path);parser.add_argument('--candidate-manifest',type=Path)
    parser.add_argument('--output',type=Path);parser.add_argument('--_worker',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args._worker:
        try:result=_worker(args._worker)
        except Exception as error:result={'status':'failed','coverage_gaps':['runtime_worker_exception:'+type(error).__name__]}
        print(json.dumps(result,ensure_ascii=True,allow_nan=False));return 0 if result['status']=='passed'else 3
    if not all((args.source_manifest,args.candidate_manifest,args.output)):parser.error('Both manifests and a new output path are required')
    if args.output.exists()or args.output.is_symlink()or args.output.resolve()in {args.source_manifest.resolve(),args.candidate_manifest.resolve()}:
        parser.error('Use a new separate JSON output file')
    try:
        report=verify(args.source_manifest,args.candidate_manifest)
        with args.output.open('x',encoding='utf-8')as stream:json.dump(report,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
    except (OSError,ValueError,subprocess.SubprocessError):
        parser.exit(2,'Runtime verification could not complete; no candidate was enabled.\n')
    print(json.dumps({'status':report['status'],'candidate_enabled':False}))
    return 0 if report['status']=='passed'else 3

if __name__=='__main__':raise SystemExit(main())
