"""Freeze the recursive BOOL/IF breakthrough without rerunning vendor code.

The local archive includes successes, rejected inputs, incomplete observations,
before-native predictions and current source fits. Prior checkpoints stay intact.
"""
from datetime import datetime,timezone
import hashlib,json,platform,subprocess,sys,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'research/experiments/sfc-graph-20260926/public-corpus-discovery'
OUT=ROOT/'research/results/gxw-st-boolean-20260930'
PUBLIC=ROOT/'research/evidence/gxw-st-boolean-20260930.zip'
LOCAL=P/'gxw-st-boolean-local-20260930.zip'
PREVIOUS=ROOT/'research/results/gxw-st-transfers-20260930/manifest.json'
GROUPS=[
    'st-bool-port-direct-store-order-entry-forward-20260930',
    'st-bool-expression-graph-discovery-20260930',
    'st-bool-port-recursive-forward-20260930',
    'st-bool-expression-XOR-discovery-20260930',
    'st-bool-port-XOR-forward-20260930',
    'st-bool-port-XOR-input-forward-20260930',
    'st-bool-expression-right-tree-discovery-20260930',
    'st-bool-port-right-tree-forward-20260930',
    'st-condition-branch-discovery-20260930',
    'st-condition-branch-pointer-discovery-20260930',
    'st-condition-branch-forward-20260930',
]
INCOMPLETE='st-bool-port-direct-store-order-forward-20260930'
EXCLUDED={'.exe','.dll','.pdb','.pyc','.zip'}
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'research'),str(P)]
from source_q_st_calls import predict
from verify_temporary_check_correspondence import verify as checked_resources


def digest(raw):return hashlib.sha256(raw).hexdigest()
def rel(path):return path.resolve().relative_to(ROOT).as_posix()
def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    for path in (OUT,PUBLIC,LOCAL):
        if path.exists():raise FileExistsError(path)
    prior=read(PREVIOUS);comparisons=[];cases=[];local={};public={}
    def pair(case,candidate,native,source,kind):
        a,b=candidate.read_bytes(),native.read_bytes()
        if a!=b:raise ValueError(f'Byte pair differs: {case}')
        comparisons.append(dict(case=case,candidate=rel(candidate),native=rel(native),source=rel(source),
            source_sha256=digest(source.read_bytes()),predicted_sha256=digest(a),native_sha256=digest(b),
            bytes=len(a),candidate_kind=kind))
    for group in GROUPS:
        directory=P/group
        for row in read(directory/'comparison.json'):
            case=directory/row['case'];source=case/'input.gxw';native=Path(row['native_directory'])
            before=read(case/'before-native.json');assert digest(source.read_bytes())==before['input_sha256']
            outcome=read(native/'trace-outcome.json')
            assert not outcome['timed_out'] and any('process-terminated' in value for value in outcome['detached'])
            accepted=bool(row['check'] and not row['check']['rejected'])
            code=(native/'pcode-0-0.bin').read_bytes()
            if (case/'predicted-0.bin').exists():
                assert ((case/'predicted-0.bin').read_bytes()==code)==row['predicted_equal']
            else:assert row['predicted_equal'] is None and 'unsupported' in before['prediction']
            options=dict(allow_inputs=True,allow_omitted=True,allow_members=True,allow_concat=True,
                allow_dynamic_stores=True,allow_alternate_banks=True,allow_string_intrinsics=True,
                allow_enabled_instructions=True,allow_recursive_booleans=True,
                allow_general_conditions=group.startswith('st-condition-branch-'),owner='MAIN')
            try:model=predict(source.read_bytes(),('SCPI',),**options)
            except ValueError as error:
                if accepted:raise
                handling='unsupported';model_reason=str(error)
            else:
                assert accepted and model['output']==code,(group,row['case'])
                fit=case/'checkpoint-source-fit.bin';fit.write_bytes(model['output'])
                pair(group+'/'+row['case']+'/current-fit',fit,native/'pcode-0-0.bin',source,'post-observation-source-fit')
                handling='native-byte-reproduced';model_reason=None
            if row['predicted_equal'] is True:
                pair(group+'/'+row['case']+'/before-native',case/'predicted-0.bin',native/'pcode-0-0.bin',source,'prediction-saved-before-native')
            phases=[native]+([Path(row['reopen']['native_directory'])] if accepted else [])
            if accepted:assert row['reopen']['identical'] and not row['reopen']['check']['rejected']
            for phase in phases:
                result=read(phase/'resource-correspondence.json')
                generated={item['name']:item['index'] for item in result['generated']}
                for observation in result['resource_reads']:
                    if observation['phase']!='check' or observation['caller']['rva']!='0x82a34' or observation['hresult']<0 or observation['code']!=0:continue
                    for channel in range(3):
                        pair(group+'/'+row['case']+'/'+rel(phase).split(group+'/',1)[1]+f'/read-{observation["serial"]}-{channel}',
                            phase/f'checked-read-{observation["serial"]}-{channel}.bin',
                            phase/f'pcode-{generated[observation["resource_name"]]}-{channel}.bin',phase/'input.gxw','actual-checker-read')
            cases.append(dict(group=group,case=row['case'],input_sha256=before['input_sha256'],accepted=accepted,
                before_native_byte_match=row['predicted_equal'],handling=handling,model_reason=model_reason,
                diagnostic_codes=sorted({hex(d['code']) for d in row['diagnostics'] if d['kind']==2})))
    correspondence=checked_resources(GROUPS)
    assert correspondence['incomplete_checks']==correspondence['incomplete_before_check']==0
    failure=read(P/INCOMPLETE/'observation-failure.json')
    assert failure['status']=='observation-incomplete' and failure['check'] is None
    OUT.mkdir()
    claims=dict(cases=cases,completed_native_cases=len(cases),accepted_compile_and_reopen=sum(c['accepted'] for c in cases),
        rejected_cases=sum(not c['accepted'] for c in cases),
        before_native_byte_matches=sum(c['before_native_byte_match'] is True for c in cases),
        current_source_byte_matches=sum(c['handling']=='native-byte-reproduced' for c in cases),
        incomplete_native_attempts=[dict(group=INCOMPLETE,case='BOOL-store-before-enable-B1',status='observation-incomplete')],
        evidence_level='native observations and bounded verified byte reproduction; no production-safe promotion',
        handling='partially-decoded source model and opaque-preserved node observations; unsupported cases explicit',
        findings=[
            'Postorder whole-tree BOOL capture reservation explains recursive OR/AND and indexed right subtrees; both-indexed trees can require three B bits before emitted arithmetic permits any release.',
            'XOR leaf/comparison lowering expands into opposite-condition AND branches joined by ORB. Dynamic right versus left operands have different capture requirements.',
            'Configured-bank internal/direct-store captures, original-left-bank instruction enables, fresh BOOL INPUT buffers and stored-type slot reuse remain separate contexts. Statement order can change B exhaustion.',
            'Direct IF leaves/relations are complemented; compound conditions use INV before CJ. Sequential automatic P labels require an explicitly configured range.',
            'The real FX3U Keypad seven-WORD-comparison guard is transplanted into controlled Q03UDV source with a recorded port rename. K0/nonzero H literal spelling, skip/take and IN_OUT clearing are reproduced; this is not whole-project or cross-CPU equivalence.',
            'Failed non-entry instrumentation, invalid named-output argument syntax, missing P range and bit/pointer exhaustion are retained separately. Neither absent observations nor an empty generated set is acceptance.'],
        limits=['Q03UDV CPU209/native lexical version25 only; compound XOR operands, compound NOT, ELSE and nested IF remain unsupported.',
            'WORD relation constants are restricted to0..32767; finite initialized witnesses do not certify PLC execution.',
            'Inputs/declarations and unknown unrelated logical payloads are preserved in local evidence; this checkpoint does not extend production writers.'])
    save(OUT/'claims.json',claims);save(OUT/'checked-resource-correspondence.json',correspondence)
    (OUT/'worktree-status.txt').write_bytes(subprocess.check_output(['git','status','--porcelain=v1'],cwd=ROOT))
    (OUT/'research-working-diff.patch').write_bytes(subprocess.check_output(['git','diff','--','research','docs/reports'],cwd=ROOT))
    def add(target,path):
        if path.is_file() and path.suffix.lower() not in EXCLUDED and '__pycache__' not in path.parts:target[rel(path)]=path
    for group in GROUPS+[INCOMPLETE]:
        for file in (P/group).rglob('*'):add(local,file)
    for file in P.iterdir():
        if file.suffix in ('.py','.js','.cs'):add(public,file)
    for file in (ROOT/'research').glob('*.py'):add(public,file)
    for folder,patterns in [(ROOT/'src/gxw',['**/*.py','**/*.json']),(ROOT/'research/native',['*.cs','*.js'])]:
        for pattern in patterns:
            for file in folder.glob(pattern):add(public,file)
    for name in ('LICENSE','AGENTS.md','research/README.md'):
        add(public,ROOT/name)
    for file in OUT.iterdir():add(public,file)
    # Full original FX3U source is retained locally for the guard provenance.
    real=P/'boxid-keypad-call-native/input.gxw';add(local,real)
    for name in public:local.pop(name,None)
    def archive(files,path,visibility):
        entries=[]
        with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as zipped:
            for name,file in sorted(files.items()):
                raw=file.read_bytes();entries.append(dict(path=name,bytes=len(raw),sha256=digest(raw)));zipped.writestr(name,raw)
            zipped.writestr('checkpoint-files.json',json.dumps(dict(visibility=visibility,files=entries),indent=2))
        return dict(path=rel(path),bytes=path.stat().st_size,sha256=digest(path.read_bytes()),visibility=visibility,files=entries)
    archives=[archive(public,PUBLIC,'repository-source-snapshot'),archive(local,LOCAL,'local-only; third-party redistribution rights not established')]
    environment=[]
    for entry in prior['environment']:
        path=Path(entry['path']);environment.append(dict(path=str(path),bytes=path.stat().st_size,sha256=digest(path.read_bytes())))
    manifest=dict(schema=1,created_utc=datetime.now(timezone.utc).isoformat(),
        base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        python=platform.python_version(),platform=platform.platform(),environment=environment,
        prior_checkpoint=dict(path=rel(PREVIOUS),sha256=digest(PREVIOUS.read_bytes())),
        selection=dict(whole_experiment_directories=[rel(P/g) for g in GROUPS+[INCOMPLETE]],exclusions=sorted(EXCLUDED),
            policy='Explicit completed BOOL/IF groups plus all failed/incomplete preparations. No vendor code rerun; original before-native files untouched.'),
        archives=archives,comparisons=comparisons,claims=claims,
        limits=['Archive hash verification is not a native recompile or semantic certification.','Local and prior archives are required for complete recovery. No commit, push or release.'])
    save(OUT/'manifest.json',manifest)
    print(json.dumps(dict(accepted=claims['accepted_compile_and_reopen'],rejected=claims['rejected_cases'],
        before_native_matches=claims['before_native_byte_matches'],source_fits=claims['current_source_byte_matches'],
        byte_pairs=len(comparisons),archives=[{k:v for k,v in a.items() if k!='files'}|dict(files=len(a['files'])) for a in archives])),flush=True)


if __name__=='__main__':main()
