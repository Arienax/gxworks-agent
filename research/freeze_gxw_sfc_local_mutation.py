"""Freeze SFC raw ownership, accepted local edits, resize and stale-code controls.

Portable code contains no third-party project/source witnesses. Complete raw
projects, native work and failed experiments remain in the matching local ZIP.
Archive contents are compared directly; no hashes are generated or verified.
"""
from pathlib import Path
import json
import struct
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'research/experiments/sfc-graph-20260926/public-corpus-discovery'
EXPERIMENTS=(
    'fx-sfc-multireg-20261001-v1','fx-sfc-multireg-20261001-v2','fx-sfc-multireg-20261001-v3',
    'fx-sfc-registration-positions-20261001-v1','fx-sfc-registration-positions-reopen-20261001-v1',
    'fx-sfc-ignored-secondary-20261001-v1','fx-sfc-multireg-bindings-20261001-v1',
    'fx-sfc-measured-projection-20261001-v1','fx-sfc-registration-read-20261001-v1',
    'fx-sfc-registration-read-20261001-v2','fx-sfc-action-selection-20261001-v1',
    'fx-sfc-assembly-entry-20261001-v1','fx-sfc-assembly-entry-20261001-v2',
    'fx-sfc-conversion-outcome-20261001-v1','fx-sfc-conversion-failure-gate-20261001-v1',
    'fx-sfc-empty-task-lifecycle-20261001-v1','fx-sfc-accepted-local-patch-20261001-v1',
    'fx-sfc-child-resize-20261001-v1')


def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=True,indent=2)+'\n',encoding='utf-8')


def archive(path,entries):
    if len({name for _,name in entries})!=len(entries):raise ValueError('Duplicate archive entries')
    if any(Path(name).is_absolute() or '..' in Path(name).parts for _,name in entries):
        raise ValueError('Archive entry escapes its root')
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for source,name in sorted(entries,key=lambda item:item[1]):z.write(source,name)
    with zipfile.ZipFile(path) as z:
        for source,name in entries:
            if z.read(name)!=source.read_bytes():raise ValueError('Archive differs from retained raw file: '+name)
    return dict(entries=len(entries),bytes=path.stat().st_size,entry_bytes_exact=True)


def main():
    result=ROOT/'research/results/gxw-sfc-local-mutation-20261001.json'
    public=ROOT/'research/evidence/gxw-sfc-local-mutation-20261001.zip'
    local=P/'gxw-sfc-local-mutation-20261001-local.zip'
    proof=P/'sfc-local-mutation-frozen-20261001'
    if any(path.exists() for path in (result,public,local,proof)):
        raise FileExistsError('Frozen evidence must not be replaced')
    for name in EXPERIMENTS:
        if not (P/name).is_dir():raise FileNotFoundError('Required retained experiment is missing: '+name)
    sys.path[:0]=[str(P),str(ROOT/'src')]
    from probe_fx_sfc_shared_child import source_programs
    proof.mkdir();witnesses=[];private=[]

    def entry(path):
        path=Path(path)
        relative=path.relative_to(P).as_posix()
        if relative.split('/')[0] not in EXPERIMENTS:raise ValueError('Witness is outside retained experiment roots')
        return 'experiments/'+relative

    def witness(source_path,native,inline,*,full=False,expected=None):
        index=len(witnesses);resolver,programs=source_programs(source_path.read_bytes());blocks=[]
        logical_files=resolver.logical_files()
        for owner,item in sorted(programs.items()):
            declarations=[f for f in logical_files if f.logical_name.split('.')[0]==owner and f.logical_name.lower().endswith('.lh')]
            if len(declarations)!=1:raise ValueError('Frozen source block declaration is ambiguous')
            raw=resolver.hdb.read_stream(declarations[0].stream_name)
            n=struct.unpack_from('<I',raw,54)[0];end=58+2*n
            if not 1<=n<=8192 or end+24>len(raw):raise ValueError('Source declaration name extent differs')
            if raw[end:end+20]!=bytes.fromhex('0000000100000000010000000000010000000000'):
                raise ValueError('Source block declaration header differs')
            number=struct.unpack_from('<I',raw,end+20)[0]
            if number>255 or any(b['number']==number for b in blocks):raise ValueError('Source block number is outside frozen scope')
            source_file=proof/f'witness-{index:02d}-{owner}.pou';source_file.write_bytes(item['source'].raw)
            archive_entry=f'witnesses/{index:02d}/{owner}.pou';private.append((source_file,archive_entry))
            blocks.append(dict(number=number,owner=owner,source_entry=archive_entry))
        row=dict(source_project_entry=entry(source_path),blocks=blocks,
            pcode_entry=entry(native/'pcode-0-0.bin'),frontend_entry=entry(native/'frontend-snapshot.json'),
            events_entry=entry(native/'native-events.jsonl'),inline_bindings=inline,full_check_accepted=full)
        if expected is not None:row['expected_pcode_entry']=entry(expected)
        witnesses.append(row)

    old=read(P/'fx-sfc-multireg-bindings-20261001-v1/comparison.json')
    if (old['usable_source_bindings'],old['bound_inline_bodies'])!=(20,320):raise ValueError('Earlier native raw binding counts differ')
    for path in sorted((P/'fx-sfc-multireg-bindings-20261001-v1').glob('*.json')):
        row=read(path)
        if not row.get('source_binding_evidence_usable'):continue
        witness(Path(row['source']),Path(row['native_directory']),row['source_native_binding_count'])
    if len(witnesses)!=20:raise ValueError('Earlier usable source witness count differs')
    accepted_projects=[]
    for group in ('fx-sfc-accepted-local-patch-20261001-v1','fx-sfc-child-resize-20261001-v1'):
        for row in read(P/group/'comparison.json'):
            flags=('native_full_check_accepted','pcode_byte_exact','saved_source_ownership_exact',
                'saved_recompile_full_check_accepted','saved_recompile_pcode_byte_exact',
                'fresh_frontend_all_children_and_registrations_exact')
            if not all(row.get(k) for k in flags) or row['diagnostic_rows'] or row['saved_recompile_diagnostic_rows']:
                raise ValueError('A proposed accepted source edit was rejected or changed')
            d=P/group/row['case'];proposal=read(d/'proposal-before-native.json')
            expected=d/'expected-pcode.bin'
            for source_path,native,key in ((d/'input.gxw',d/'compile-check-save','source_binding'),
                (d/'compile-check-save/native-saved.gxw',d/'saved-recompile-check','saved_source_binding')):
                binding=row[key]
                if not binding['source_binding_evidence_usable'] or not binding['native_frontend_all_children_and_registrations_exact']:
                    raise ValueError('Accepted project has an unresolved source/native binding')
                witness(source_path,native,binding['source_native_binding_count'],full=True,expected=expected)
            accepted_projects.append(dict(experiment=group,case=row['case'],
                prospective_prediction=True,expected_pcode_bytes=proposal['expected_pcode_bytes'],
                full_check_accepted_before_and_after_native_save=True,
                unknown_registered_child_preserved=row['case']=='registered-opaque-secondary',
                mutation='child body growth by 7 bytes, two nested size words and source history size' if 'resize' in group else
                    'three one-byte Y operands'+(' and one same-width SET-to-RST opcode' if row['case']=='registered-opaque-secondary' else ''),
                graph_cache_and_unrelated_stream_payloads_preserved=True))
    gate=P/'fx-sfc-conversion-failure-gate-20261001-v1';failure_gate=[]
    for name in ('empty-no-check','empty-with-check'):
        row=read(gate/name/'comparison.json')
        if not row['conversion_rejected'] or row['program_check_attempted'] or row['outcome']['exported']:
            raise ValueError('SFC source-load failure was not blocked')
        failure_gate.append(dict(events_entry=entry(gate/name/'native-events.jsonl'),
            pcode_entry=entry(gate/name/'pcode-0-0.bin'),cached_entry=entry(P/'fx-sfc-multireg-20261001-v3/empty/compile/pcode-0-0.bin')))
    lifecycle=P/'fx-sfc-empty-task-lifecycle-20261001-v1';empty_tasks=[]
    for name in ('empty-task-good-cache','empty-task-poisoned-cache'):
        row=read(lifecycle/name/'comparison.json')
        if row['hook_errors'] or not row['returned_cache_byte_exact'] or not row['input_copy_unchanged']:
            raise ValueError('Empty-task conversion lifecycle control differs')
        empty_tasks.append(dict(trace_entry=entry(lifecycle/name/'native/events.jsonl'),
            pcode_entry=entry(lifecycle/name/'native/pcode-0-0.bin'),cached_entry=entry(lifecycle/name/'expected-cached.bin')))
    counts=dict(source_bound_native_bodies=28,raw_inline_bindings_exact=448,native_body_lexical_gap_cases=1,
        native_full_check_accepted=8,failed_conversion_export_and_check_blocked=2,empty_task_cache_controls_exact=2)
    manifest=dict(schema=1,source_witnesses=witnesses,failure_gate_witnesses=failure_gate,
        empty_task_witnesses=empty_tasks,expected_cold_counts=counts)
    save(proof/'checkpoint.json',manifest);private.append((proof/'checkpoint.json','checkpoint.json'))

    public_entries=[]
    # Reuse the already distributable lexical readers and their exact pinned
    # dependencies; the new archive does not copy any original project body.
    dependencies=proof/'portable-dependencies';dependencies.mkdir()
    with zipfile.ZipFile(ROOT/'research/evidence/gxw-fx-modern-roundtrip-20261001.zip') as z:
        for name in z.namelist():
            if not name.startswith(('models/','product/')):continue
            target=(dependencies/name).resolve()
            if not target.is_relative_to(dependencies.resolve()):raise ValueError('Frozen dependency entry escapes its directory')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(name))
            public_entries.append((target,name))
    for name in ('source_fx_sfc_lossless_reader.py','source_fx_sfc_controls.py','source_fx_sfc_tokens.py',
                 'source_fx_sfc_multireg_projection.py'):
        public_entries.append((P/name,'models/'+name))
    public_entries.append((ROOT/'src/gxw/sfc_pou.py','product/src/gxw/sfc_pou.py'))
    public_entries.append((ROOT/'research/validate_gxw_sfc_checkpoint.py','validate_local.py'))
    public_entries.append((Path(__file__),'tools/'+Path(__file__).name))
    for name in ('TraceSFCConversionOutcome.js','TraceSFCConversionLifecycle.js','TraceSFCActionSelection.js',
                 'disassemble_sfc_adapter.py','disassemble_sfc_converter.py','disassemble_sfc_workspace.py'):
        public_entries.append((P/name,'tools/'+name))
    native_source=gate/'empty-no-check'
    for name in ('WorkspaceReplayOracle.cs','NativeFrontendSnapshot.cs','NativeSfcGraphOracle.cs','NativeWorkspaceCopyOracle.cs'):
        public_entries.append((native_source/name,'tools/native/'+name))
    public_entries.append((ROOT/'LICENSE','LICENSE'))
    for name in EXPERIMENTS:
        for path in (P/name).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:private.append((path,entry(path)))
    for path in sorted(P.glob('dz-sfc-*-20261001.txt')):
        private.append((path,'static/'+path.name))
    for path in (Path(__file__),ROOT/'research/validate_gxw_sfc_checkpoint.py',ROOT/'research/native/WorkspaceReplayOracle.cs',
                 ROOT/'research/replay_gxw_workspace.py'):
        private.append((path,'tools/'+path.name))
    local_info=archive(local,private)
    summary=dict(schema=1,date='2026-10-01',status='native-validated/offline-project-scope',
        versions=dict(converter='1.635.0.1',compiler_adapter='1.635.0.1',cpu=520,plc='FX3U/FX3UC'),
        counts=counts,accepted_local_candidates=accepted_projects,
        registration_selection=dict(first_stored_entry='native validated',concatenation='rejected',numeric_sort='rejected',
            measured_registration_counts=[1,2,3],measured_numeric_values=[0,1,3,7],numeric_field_meaning='uninterpreted'),
        stale_output_controls=dict(source_load_failure='kind 1/code 0x20; native error was cleared; helper blocks export and ProgramCheck',
            empty_task='source load returns success, but assembly/publication do not run and cached PCode is returned',
            admission='Progress 100 and S_OK alone are insufficient; native PCode must be bound to the selected raw source'),
        retained_failure_experiments=[n for n in EXPERIMENTS if n in ('fx-sfc-multireg-20261001-v1','fx-sfc-multireg-20261001-v2',
            'fx-sfc-registration-read-20261001-v1','fx-sfc-registration-read-20261001-v2','fx-sfc-assembly-entry-20261001-v1')],
        archive_scope=dict(public='Readers, native ABI tools and cold validator; no third-party project or raw source witnesses',
            local='Complete original-derived proposals, native project work, traces, failures and 28 raw source/native witnesses'),
        local_archive=dict(path=local.relative_to(ROOT).as_posix(),**local_info),
        limitations=['One independently sourced converted simple FX3U SFC project and its controlled derivatives',
            'Unknown secondary child is retained and excluded from the measured selected body; its opcode meaning is not decoded',
            'Native project check acceptance does not establish PLC execution behavior or original program intent',
            'Original repeated-coil rejection remains recorded; accepted copies intentionally change output addresses',
            'Graph layout/cache, registration numeric semantics and other PLC families are not established by these edits'],
        verification='Direct archive byte equality, source/native byte ownership and retained prospective predictions; no new hashes')
    save(proof/'public-summary.json',summary);public_entries.append((proof/'public-summary.json','summary.json'))
    public_info=archive(public,public_entries)
    cold=proof/'cold-replay';cold.mkdir()
    with zipfile.ZipFile(public) as z:
        for name in z.namelist():
            target=(cold/name).resolve()
            if not target.is_relative_to(cold.resolve()):raise ValueError('Public archive entry escapes cold replay directory')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(name))
    process=subprocess.run([sys.executable,str(cold/'validate_local.py'),str(local)],capture_output=True,timeout=45,cwd=cold)
    (proof/'cold-replay.stdout.bin').write_bytes(process.stdout);(proof/'cold-replay.stderr.bin').write_bytes(process.stderr)
    if process.returncode:
        save(proof/'cold-replay-failure.json',dict(returncode=process.returncode,public_archive_preserved=str(public),local_archive_preserved=str(local)))
        raise RuntimeError('Frozen cold validation failed; both archives and raw output retained')
    cold_counts=json.loads(process.stdout.decode('utf-8'))
    if cold_counts!=counts:raise ValueError('Cold replay counts differ')
    summary.update(public_archive=dict(path=public.relative_to(ROOT).as_posix(),**public_info),cold_replay_counts=cold_counts)
    save(result,summary);print(json.dumps(dict(result=str(result),counts=cold_counts,public=public_info,local=local_info),ensure_ascii=True),flush=True)


if __name__=='__main__':main()
