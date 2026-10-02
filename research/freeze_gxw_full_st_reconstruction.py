"""Freeze complete Q ST predictions, counterexamples and native holdouts."""
from pathlib import Path
import json
import zipfile

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'research/experiments/sfc-graph-20260926/public-corpus-discovery'
NAME='gxw-full-st-reconstruction-20261001'
GROUPS=('st-full-source-20261001-v1','st-unsigned-order-20261001-v1',
    'st-full-source-20261001-v2','st-full-source-20261001-v3','st-full-source-20261001-v4')


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def archive(path,entries):
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,data in sorted(entries.items()):z.writestr(name,data)
    with zipfile.ZipFile(path) as z:
        if set(z.namelist())!=set(entries) or any(z.read(name)!=data for name,data in entries.items()):raise ValueError('archived bytes differ')


def main():
    manifest=ROOT/'research/results'/f'{NAME}.json'
    public_path=ROOT/'research/evidence'/f'{NAME}.zip'
    local_path=P/f'{NAME}-local.zip'
    if any(p.exists() for p in (manifest,public_path,local_path)):raise FileExistsError(NAME)
    after=read(P/'st-full-source-after-fit-20261001.json')
    isolation=read(P/'st-source-inventory-isolation-20261001.json')
    steps=read(P/'st-full-source-20261001-v4/whole-program-new/source-step-correspondence.json')
    prospective=[]
    for group in GROUPS:
        for row in read(P/group/'comparison.json'):
            before=read(P/group/row['case']/'before-native.json')['prediction']
            prospective.append(dict(group=group,case=row['case'],before_native_handling=before['handling'],
                before_native_bytes=before.get('bytes'),reason=before.get('reason'),
                comparison=row.get('comparison'),native_check=row['check'],backend_rejected=row['backend_rejected'],
                diagnostic_counts={str(kind):sum(d['kind']==kind for d in row['diagnostics']) for kind in {d['kind'] for d in row['diagnostics']}},
                diagnostic_codes=sorted({d['code'] for d in row['diagnostics']}),
                reopen_check=row['reopen']['check'],reopen_channels=row['reopen']['channels']))
    matches=[r for r in prospective if (r.get('comparison') or {}).get('byte_identical')]
    mismatches=[r for r in prospective if r.get('comparison') and not r['comparison']['byte_identical']]
    unsupported=[r for r in prospective if r['before_native_handling']=='unsupported']
    if after['summary']['byte_identical_cases']!=18 or len(matches)!=7 or len(mismatches)!=8 or len(unsupported)!=1:raise ValueError('unexpected checkpoint counts')
    if any(r['native_check']['rejected'] or r['reopen_check']['rejected'] or not all(c['byte_identical'] for c in r['reopen_channels']) for r in prospective):raise ValueError('native acceptance/reopen evidence incomplete')
    if not all(r['detached_inventory_byte_exact'] and r['unreadable_cache_objects_ignored'] for r in isolation):raise ValueError('source isolation evidence incomplete')
    result=dict(name=NAME,scope='Complete emitted Q03UDV ST program and selected inline FB bodies; source declarations/configuration only; research model',
        evidence_level='verified within measured native profile',handling='native-validated experimental compiler-output reproduction',production_safe=False,
        environment=dict(cpu='Q03UDV',compiler_iec='15.50',q_code_generator='15.41',native_cpu=209,native_versions=[25]),
        previous_checkpoint='research/results/gxw-st-reference-contexts-20261001.json',
        after_fit=after,prospective=dict(total_cases=len(prospective),predicted_cases=len(matches)+len(mismatches),
            complete_byte_matches=len(matches),counterexamples=len(mismatches),initially_unsupported=len(unsupported),
            accepted_saved_reopened_cases=len(prospective),cases=prospective),
        final_unseen_holdouts=[r for r in prospective if r['group']=='st-full-source-20261001-v4'],
        source_inventory_isolation=isolation,independent_native_steps=steps['summary'],
        rules=['Only source metadata, declaration and POU payloads are admitted; detached inventory produces the same complete code.',
            'Local/global names must be unique; native same-name refusal remains preserved by the previous checkpoint.',
            'BOOL equality retains literal SM400/SM401 contacts and expanded Boolean blocks.',
            'Scalar WORD ordering expands through signed comparisons/sign tests; 32767 greater-than has an observed sign-only form.',
            'Same logical operators associate left; a right logical subtree is prepared before an atomic left term, while two atomic comparisons retain source order.',
            'Empty branches alter predicate polarity and label allocation; STRING comparisons and quoted punctuation remain typed.',
            'Computed OUT_T enables allocate fresh static bit buffers at each call; scalar NOT remains direct. OUT Boolean destination uses the condition directly.'],
        method_reference=dict(repository='https://github.com/purinzan/gx3-cli-mcp',reviewed_files=[
            'gx3cli/gx3_intermediate_tool.py','gx3cli/gx3_roundtrip.py','gx3cli/gx3_analysis_state.py','gx3cli/gx3_validation_ledger.py'],
            applied=['Preserve raw payloads outside targeted edits.', 'Keep unavailable/unsupported/failed checks separate from successful zero findings.',
                'Freeze predictions and failure inputs before native comparison; retain counterexamples after repair.',
                'Use direct complete bytes and a separate native decoder/step reader rather than decoder self-status.'],
            boundary='No GX3 container, SQLite, ladder or instruction format facts transferred. GX3 equivalent access sets alone do not certify Boolean semantics.'),
        limitations=['One Q03UDV source-project origin; native-supported authored variants do not establish independent origin coverage.',
            'The original unmodified program still fails the observed 11 duplicate-coil checks despite exact complete code reproduction.',
            'Other CPU families/versions, full project/compiler-state regeneration, arbitrary libraries/types and generic PLC runtime equivalence remain unsupported.',
            'ASCII literals only; no escaped quotes, indexed timer enable or generic standard-library lowering claim.',
            'After-fit matches are separate from prospective matches; eight failed predictions and one initial unsupported case remain intact.',
            'Source points with multiple observations on one physical line can be ambiguous; no arbitrary choice of native marker is made.',
            'Original project/FB source and full native traces remain local. Vendor DLLs and helper executables are excluded from the public archive.'],
        public_archive=str(public_path.relative_to(ROOT)).replace('\\','/'),local_archive=str(local_path))
    tools=('source_q_st_calls.py','source_q_direct_declarations.py','source_q_st_expressions.py','source_q_label_binding.py',
        'source_q_width.py','q-width-static-tables.json','source_st_branch_trees.py',
        'probe_full_st_reconstruction.py','probe_st_unsigned_order.py','probe_full_st_holdouts.py',
        'probe_full_st_composed_holdouts.py','probe_full_st_fresh_arguments.py',
        'QSourceStepOracle.cs','verify_st_reference_steps.py','inspect_native_st_references.py','trace_native_source_points.js')
    selected=[P/name for name in tools]+[Path(__file__).resolve()]
    public={str(path.relative_to(ROOT)).replace('\\','/'):path.read_bytes() for path in selected}
    public['observations/checkpoint.json']=(json.dumps(result,indent=2,ensure_ascii=True)+'\n').encode()
    public['observations/after-fit.json']=(P/'st-full-source-after-fit-20261001.json').read_bytes()
    public['observations/source-inventory-isolation.json']=(P/'st-source-inventory-isolation-20261001.json').read_bytes()
    for case in ('word-with-else','word-without-else'):
        path=P/'st-unsigned-order-20261001-v1'/case/'unsigned-relations-observed.json'
        public[f'observations/{case}-unsigned-relations.json']=path.read_bytes()
    local=dict(public)
    for group in GROUPS:
        for path in (P/group).rglob('*'):
            if path.is_file():local[str(path.relative_to(ROOT)).replace('\\','/')]=path.read_bytes()
    archive(public_path,public);archive(local_path,local)
    result['archive_contents']=dict(public_files=len(public),local_files=len(local),
        public_bytes=public_path.stat().st_size,local_bytes=local_path.stat().st_size,archived_file_bytes_verified=True)
    manifest.write_text(json.dumps(result,indent=2,ensure_ascii=True)+'\n',encoding='utf-8')
    print(json.dumps(dict(manifest=str(manifest),**result['archive_contents'])))


if __name__=='__main__':main()
