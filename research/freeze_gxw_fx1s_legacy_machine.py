"""Freeze legacy FX1S grammar, independent reader and retained counterexamples.

Only synthetic witnesses and derived models enter the portable archive. Original
projects, native project work and full original tokens remain in the local archive.
Archive entries are compared directly; this script creates no digest validation.
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'research/experiments/sfc-graph-20260926/public-corpus-discovery'
MODELS=('source_fx_old_machine.py','source_fx_old_decoder.py','source_fx_instruction_cells.py','source_fx_machine.py',
    'source_fx_operand_rules.py','source_fx1s_reader.py','source_fx_lossless_reader.py','source_fx_record_reader.py',
    'source_fx_header_reader.py','source_fx_operand_reader.py','fx-old-machine-tables-20261001-v1.json',
    'fx-old-decoder-tables-20261001-v1.json','fx1s-cell-tables-20261001-v1.json','fx-operand-reader-tables-20261001-v1.json',
    'fx-native-instruction-catalog-20261001-v1/before-native.json')
TOOLS=('extract_fx_old_machine_tables.py','extract_fx_old_decoder_tables.py','probe_fx1s_old_machine_corpus.py',
    'probe_fx1s_old_machine_holdouts.py','probe_fx1s_machine_decoder.py','probe_fx1s_machine_decoder_holdouts.py',
    'probe_fx1s_public_reemission.py','analyze_fx1s_machine_roundtrips.py','probe_fx1s_lossless_project_patch.py',
    'probe_fx1s_project_patch_controls.py','FX1SFromMachineOracle.cs','FXOperandCheckOracle.cs',
    'TraceFXInstructionCells.js','TraceFXOldDecoder.js')
SYNTHETIC=('fx1s-old-machine-holdouts-20261001-v1','fx1s-old-machine-holdouts-20261001-v2',
    'fx1s-independent-machine-holdouts-20261001-v1','fx1s-independent-machine-holdouts-20261001-v2')


def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=True,indent=2)+'\n',encoding='utf-8')


def archive(path,entries):
    if len({name for _,name in entries})!=len(entries):raise ValueError('Duplicate archive entry')
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for source,name in sorted(entries,key=lambda pair:pair[1]):z.write(source,name)
    with zipfile.ZipFile(path) as z:
        for source,name in entries:
            if z.read(name)!=source.read_bytes():raise ValueError('Archive entry differs from retained source bytes')
    return dict(entries=len(entries),bytes=path.stat().st_size,entry_bytes_exact=True)


VALIDATOR='''"""Cold replay of frozen native witnesses; no native DLL, hooks or network."""
from pathlib import Path
import collections,json,sys
p=Path(__file__).resolve().parent
sys.path.insert(0,str(p/'models'))
from source_fx_old_machine import FXOldMachine,FX1SCells
from source_fx_old_decoder import FXOldDecoder
from source_fx1s_reader import FX1SLosslessReader
from source_fx_operand_rules import Unsupported
def load(name):return json.loads((p/name).read_text(encoding='utf-8'))
emitter=FXOldMachine(load('models/fx-old-machine-tables-20261001-v1.json'),FX1SCells(load('models/fx1s-cell-tables-20261001-v1.json')))
decoder=FXOldDecoder(load('models/fx-old-decoder-tables-20261001-v1.json'))
counts=collections.Counter()
for r in load('emission-witnesses.json'):
    try:prediction=emitter.record(bytes.fromhex(r['raw']))
    except Unsupported as exc:
        if str(exc)!=r.get('gap'):raise
        counts['emission_source_gaps']+=1;continue
    if prediction!=r['prediction']:raise ValueError('Prospective emission snapshot differs')
    counts['emission_source_predictions']+=1
    if r['native'] is None:counts['pre_emission_refusals']+=1;continue
    e=r['native'];actual=prediction['emission']
    if prediction['native_cells']!=e['before'] or prediction['auxiliary']!=e['auxiliary'] or actual['words']!=e['words'] or actual['cells_after']!=e['after'] or actual['machine_output']!=e['output']:
        raise ValueError('Original emission witness differs')
    counts['native_emission_exact']+=1
    if e['words']>0:
        if emitter.width_rewrite(bytes.fromhex(r['raw']))['candidate']!=r['public_output']:raise ValueError('Raw width rewrite differs')
        counts['raw_width_rewrites_exact']+=1
for r in load('decoder-witnesses.json'):
    raw=bytes.fromhex(r['machine']);at=0;records=[];gap=None
    while at<len(raw):
        try:expected=decoder.record(raw[at:])
        except Unsupported as exc:gap=str(exc);break
        n=expected['words'] if expected['words']>0 else 1
        expected['offset']=at;expected['consumed_machine']=raw[at:at+2*n].hex();records.append(expected);at+=2*n
    predicted=r['prediction']
    if records!=predicted['records'] or at!=predicted['consumed'] or gap!=predicted.get('gap'):raise ValueError('Prospective decoder snapshot differs')
    counts['decoder_groups']+=1
    if gap:counts['decoder_source_gaps']+=1
    elif len(records)!=len(r['native']):raise ValueError('Native decoder call count differs')
    else:counts['decoder_complete_groups']+=1
    for expected,e in zip(records,r['native']):
        if expected['words']!=e['words'] or expected['cells']!=e['cells'] or expected['auxiliary']!=e['auxiliary']:
            raise ValueError('Independent machine reader differs')
        if expected['words']>0 and expected['consumed_machine']!=e['machine']:raise ValueError('Machine extent differs')
        counts['native_decoder_records_exact']+=1
    counts['native_reverse_refusals']+=r['public_code']!=0
reader=FX1SLosslessReader()
for r in load('reemission-witnesses.json'):
    raw=bytes.fromhex(r['raw']);ir=reader.read(raw,cpu=518)
    if reader.reconstruct(ir)!=raw:raise ValueError('Canonical raw partition differs')
    projected=[];gap=False
    for record in ir['records']:
        if record['critical_gap']:gap=True;break
        try:emission=emitter.record(bytes.fromhex(record['raw']))['emission']
        except Unsupported:gap=True;break
        if emission['words']<=0 or emission['machine_output'] is None:gap=True;break
        projected.append(emission['machine_output'])
    output=None if gap else ''.join(projected)
    if output!=r['expected_machine']:raise ValueError('Source canonical reemission differs')
    if output is not None and output!=r['native_machine']:raise ValueError('Public P-to-M output differs')
    counts['canonical_reemission_groups']+=1
    counts['canonical_reemission_source_gaps']+=gap
    counts['canonical_machine_roundtrip_exact']+=r['native_code']==0 and r['native_machine']==r['original_machine']
    counts['canonical_native_machine_changes']+=r['native_code']==0 and r['native_machine']!=r['original_machine']
    counts['canonical_native_reemission_refusals']+=r['native_code']!=0
print(json.dumps(dict(counts),ensure_ascii=True))
'''


def main():
    result=ROOT/'research/results/gxw-fx1s-legacy-machine-20261001.json'
    public=ROOT/'research/evidence/gxw-fx1s-legacy-machine-20261001.zip'
    local=P/'gxw-fx1s-legacy-machine-20261001-local.zip';proof=P/'fx1s-legacy-machine-frozen-20261001'
    if any(path.exists() for path in (result,public,local,proof)):raise FileExistsError('Frozen evidence must not be replaced')
    checks={name:read(P/name/'comparison.json') for name in ('fx1s-old-machine-corpus-20261001-v1',
        'fx1s-independent-machine-decoder-20261001-v1','fx1s-old-machine-holdouts-20261001-v2',
        'fx1s-independent-machine-holdouts-20261001-v2','fx1s-public-machine-reemission-20261001-v2')}
    for name,row in checks.items():
        if row.get('differences',0) or row.get('original_differences',0) or row['hook_errors'] or not row['baseline_traced_exact'] or not row['guards_intact']:
            raise ValueError('Unresolved final native comparison: '+name)
    patches=read(P/'fx1s-lossless-project-patch-controls-20261001-v2/comparison.json')
    for row in patches:
        if not all(row[k] for k in ('untouched_control_pcode_exact','program_check_diagnostics_unchanged','save_run_pcode_exact','saved_source_body_exact')):
            raise ValueError('Native source patch comparison differs')
        for stage in ('save_run','reopen'):
            if row[stage]['attempts'][-1]['returncode']!=0 or row[stage]['failures'] or not row[stage]['input_copy_unchanged']:
                raise ValueError('Native patch save/reopen failed')
        if not row['save_run']['exported']:raise ValueError('Native patch export absent')
    proof.mkdir();public_entries=[];private_entries=[]
    for name in MODELS:public_entries.append((P/name,'models/'+name))
    for name in TOOLS:public_entries.append((P/name,'tools/'+name))
    public_entries.append((Path(__file__),'tools/'+Path(__file__).name))
    directory=P/'fx1s-old-machine-holdouts-20261001-v2'
    rows=read(directory/'before-native.json')['requests'];predictions=read(directory/'predictions-before-native.json')
    gaps={r['id']:r['reason'] for r in read(directory/'source-gaps.json')};observations={r['id']:r for r in read(directory/'observations.json')}
    answers=[json.loads(l) for l in (directory/'baseline.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    import base64
    witnesses=[dict(id=r['id'],raw=r['raw'],prediction=predictions.get(str(r['id'])),gap=gaps.get(r['id']),
        native=observations[r['id']]['native'] if r['id'] in observations else None,
        public_output=base64.b64decode(answers[r['id']]['output_base64']).hex()) for r in rows]
    save(proof/'emission-witnesses.json',witnesses)
    directory=P/'fx1s-independent-machine-holdouts-20261001-v2'
    rows=read(directory/'before-native.json')['requests'];predictions=read(directory/'predictions-before-native.json');observations=read(directory/'observations.json')
    save(proof/'decoder-witnesses.json',[dict(id=r['id'],machine=r['machine'],prediction=predictions[str(r['id'])],native=o['native'],public_code=o['public']['code']) for r,o in zip(rows,observations,strict=True)])
    rows=read(P/'fx1s-public-machine-reemission-20261001-v2/observations.json')
    save(proof/'reemission-witnesses.json',[dict(raw=r['source']['raw'],expected_machine=r['source']['expected_machine'],
        original_machine=r['source']['original_machine'],native_machine=base64.b64decode(r['public']['output_base64']).hex(),native_code=r['public']['code']) for r in rows if 'machine_group' in r['source']])
    (proof/'replay.py').write_text(VALIDATOR,encoding='utf-8')
    for name in ('emission-witnesses.json','decoder-witnesses.json','reemission-witnesses.json','replay.py'):public_entries.append((proof/name,name))
    for dataset in SYNTHETIC:
        for name in ('comparison.json','differences.json','source-gaps.json','retained-predecessor.json'):
            path=P/dataset/name
            if path.exists():public_entries.append((path,'failures-and-measurements/'+dataset+'/'+name))
    for name,row in checks.items():
        target=proof/(name+'-summary.json');save(target,row);public_entries.append((target,'summaries/'+target.name))
    save(proof/'project-patch-summary.json',dict(projects=2,source_contact_change='M11 to M12',bytes_changed_per_input=1,
        untouched_inner_streams_per_input=46,untouched_outer_streams_per_input=11,compiled_pcode_exact=2,saved_source_body_exact=2,reopened=2,
        full_program_check_accepted=0,full_check_diagnostics_unchanged_from_original=2,full_check_diagnostic='Repeated coil reports retained in both original and modified copies',
        scope='Specific original source proposals and native COM compile/save/reopen; original project bytes and full reports are local-only. No PLC execution or whole-family production-safe assertion.'))
    public_entries.append((proof/'project-patch-summary.json','summaries/project-patch-summary.json'))
    public_archive=archive(public,public_entries)
    with tempfile.TemporaryDirectory(prefix='fx1s-cold-',dir=proof) as temporary:
        with zipfile.ZipFile(public) as z:z.extractall(temporary)
        cold=subprocess.run([sys.executable,str(Path(temporary)/'replay.py')],capture_output=True,timeout=40)
        (proof/'cold-stdout.txt').write_bytes(cold.stdout);(proof/'cold-stderr.txt').write_bytes(cold.stderr)
        if cold.returncode:raise ValueError('Cold replay failed; archive and diagnostics retained')
        replay=json.loads(cold.stdout)
    for directory_name in ('fx1s-old-machine-corpus-20261001-v1','fx1s-independent-machine-decoder-20261001-v1',
        'fx1s-machine-roundtrip-analysis-20261001-v1','fx1s-public-machine-reemission-20261001-v1','fx1s-public-machine-reemission-20261001-v2',*SYNTHETIC):
        for path in (P/directory_name).iterdir():
            if path.is_file():private_entries.append((path,directory_name+'/'+path.name))
    for directory_name in ('fx1s-lossless-project-patch-20261001-v1','fx1s-lossless-project-patch-controls-20261001-v1','fx1s-lossless-project-patch-controls-20261001-v2'):
        directory=P/directory_name
        for path in directory.rglob('*'):
            if not path.is_file() or any(part in ('home','compiler','compiler_DZComp','native-temp') for part in path.relative_to(directory).parts):continue
            if path.suffix.lower() not in ('.json','.jsonl','.gxw','.bin','.cs','.py','.txt'):continue
            private_entries.append((path,directory_name+'/'+path.relative_to(directory).as_posix()))
    private_archive=archive(local,private_entries)
    manifest=dict(converter='15.31',cpu=518,emission_mode=5,scope='Separate legacy emitter and independent machine decoder; no GX Works3 format transfer',
        evidence_level='verified native byte/count/representation observations and two specific native source-patch save/reopen controls',
        handling='decoded lexical/machine projections with original tokens preserved; explicit source gaps and native refusals',
        cold_replay=replay,original_records=240,original_pcode_machine_pcode_byte_exact=240,original_public_reemission_exact=240,
        synthetic_native_reverse_refusals=40,canonical_native_machine_changes=12,canonical_native_reemission_refusals=2,
        lexical_alias_groups=150,retained_initial_emission_prediction_differences=52,retained_initial_decoder_prediction_differences=155,
        project_patches=read(proof/'project-patch-summary.json'),public_archive=public_archive,local_archive=private_archive,
        limitations=['No controller execution, device range or whole-family writer validation',
            'Ten synthetic machine fragments remain explicitly truncated for the independent source decoder',
            'Two original projects retain identical repeated-coil full-check diagnostics before and after mutation; save/reopen measured separately',
            'Canonical machine/PCode conversion is not a lossless substitute for original raw tokens'],
        original_distribution='Original third-party projects, raw bodies and native project work remain in the local-only archive')
    save(result,manifest);print(json.dumps(manifest,ensure_ascii=True),flush=True)


if __name__=='__main__':main()
