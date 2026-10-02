"""Freeze the full FX3G reading/source proof and retained refusal controls.

Public evidence contains authored tools and derived numeric observations.
Original projects, library bodies, native buffers and traces stay in a local
archive. Archives are checked by direct bytes; no hashes are written or used.
"""
from pathlib import Path
import collections
import json
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'research/experiments/sfc-graph-20260926/public-corpus-discovery'


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def save(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=True)+'\n',encoding='utf-8')


def main():
    original=P/'fx-original-full-st-20261001'
    actual=read(original/'source-reference-correspondence.json')
    listing=read(original/'independent-fx-reader/summary.json')
    prefixes=read(original/'independent-source-prefixes/summary.json')
    required=dict(raw_bytes=49049,lexical_records=3724,native_records=3724,native_decode_consumed=49049,
        whole_listing_exact=True,individual_step_queries=3724,individual_step_matches=3724,error_prefix_queries=22,error_prefix_matches=22)
    if listing!=required or prefixes!=dict(finalized_source_points=876,distinct_prefix_queries=798,exact_prefix_matches=798,source_points_verified=876):
        raise ValueError('native reading observations differ')
    if not actual['summary']['all_instructions_matched'] or actual['summary']['whole_compiler_groups']!=64:
        raise ValueError('full group matching unavailable')
    cascade=P/'fx-full-check-cascade-20261001'
    replay=read(cascade/'independent-conversion/comparison.json')
    if len(replay)!=8 or not all(all(r[k] for k in ('code_equal','consumed_equal','height_equal','error_offset_equal','guarded')) for r in replay):
        raise ValueError('independent conversion replay differs')
    controls=[]
    for directory in ('fx-full-source-edits-20261001-v2','fx-full-source-edits-20261001-v3'):
        for row in read(P/directory/'comparison.json'):
            before=read(P/directory/row['case']/'before-native.json')
            controls.append(dict(group=directory,case=row['case'],changed_streams=before['changed_streams'],
                unrelated_payloads_byte_exact=before['unrelated_payloads_byte_exact'],
                expected_error_counts=before['expected_error_counts'],observed_error_counts=row['observed_error_counts'],
                prospective_error_profile_exact=row['expected_errors_exact'],check=row['check'],
                handling='backend-rejected' if row['check'] is not None else 'frontend-rejected',saved_reopen='not-checkable after rejection'))
    point_evidence=[]
    comparisons={p:c for c in read(original/'independent-source-prefixes/comparisons.json') for p in c['point_indices']}
    for i,m in enumerate(actual['finalized_source_points']):
        native=comparisons[i]
        point_evidence.append(dict(compile_id=m['compile_id'],location=m['location'],
            parent_name=m['identity']['parent_name'],definition=m['identity']['definition'],instance=m['identity']['instance'],
            native_data_hex=m['data_hex'],native_user_info_hex=m['user_info_hex'],incoming_refs=m['incoming_refs'],
            relative_step=m['relative_native_step'],linked_step=m['linked_step'],prefix_byte_offset=m['prefix_byte_offset'],
            independently_counted_steps=native['native_steps']))
    width_rows=[]
    comparisons=read(original/'independent-fx-reader/step-comparison.json')
    for row,comparison in zip(actual['joined_instructions'],[c for c in comparisons if not c['prefix']],strict=True):
        width_rows.append(dict(record_index=row['record_index'],byte_offset=row['byte_offset'],step=row['step'],
            kind=row['compiled']['kind'],mnemonic=row['compiled'].get('op'),
            stored_width=comparison['expected'],native_width=comparison['native']))
    diagnostic_rows=[]
    for row in actual['backend_diagnostics']:
        d=row['diagnostic']
        if d['kind']!=2:continue
        instruction=row['matching_instruction'];point=instruction['observed']['preceding_source_point']
        diagnostic_rows.append(dict(code=hex(d['code']),step=d['step'],opcode=instruction['compiled'].get('op','label'),
            compile_id=instruction['compile_id'],physical_source_candidates=[dict(stream=s['stream'],line=s['line']) for s in point['source_candidates']],
            definition=point['identity']['definition'],instance=point['identity']['instance'],location=point['location']))
    proof=P/'fx-full-source-public-numeric-20261001';proof.mkdir()
    save(proof/'native-source-points.json',point_evidence);save(proof/'native-record-widths.json',width_rows)
    save(proof/'reported-error-contexts.json',diagnostic_rows)
    coverage=read(cascade/'conversion-coverage.json')
    save(proof/'conversion-coverage.json',[dict(case=r['case'],summary=r['summary'],limitations=r['limitations']) for r in coverage])
    save(proof/'prospective-controls.json',controls);save(proof/'independent-refusals.json',replay)
    manifest=dict(schema_version=1,date='2026-10-01',evidence_level='verified within one original FX3G source project and copied controls',
        handling='native-decoded instructions and live source correspondence; project check remains rejected',
        environment=dict(os='Windows',compiler_iec='15.50',fx_code_generator='15.31',adapter_cpu=521,mode=0,versions=[]),
        source=dict(repository='https://github.com/Serhioromano/gxw2-libraries',independent_original_projects=1,
            original_project='cross-family-source-holdouts/case-00/input.gxw',original_bytes='retained locally'),
        original_reading=listing,source_correspondence=actual['summary'],independent_source_prefixes=prefixes,
        prospective_controls=dict(cases=len(controls),exact_error_profile_predictions=sum(r['prospective_error_profile_exact'] for r in controls),
            counterexamples=sum(not r['prospective_error_profile_exact'] for r in controls),accepted_projects=0),
        conversion_path=[dict(case=r['case'],summary=r['summary']) for r in coverage],
        independent_refusals=dict(requests=8,all_return_codes_consumption_heights_offsets_exact=True,fresh_object_per_request=True),
        discoveries=['All 3724 final records and 5976 native operands agree with the independently decoded full 49049-byte program.',
            'The compiler links 64 emitted instruction groups in a different order. Event-order record pairing is invalid; every complete group has one disjoint exact final-code occurrence.',
            'All 64 normalized ST sources reconstruct directly from retained segments. 3138 table references resolve; 88 synthetic source sentinels remain separate.',
            'All 876 live finalized physical points agree with 798 independently counted final-code prefixes. Twelve points retain three matching incoming references each.',
            'All 22 initially reported errors join to Modbus demo source or its outlined protocol functions. This does not mean there are no later defects.',
            'One protocol selection removes the sixteen duplicate-output errors. Tautological branch boundaries around two member assignments remove the two measured structure-copy size errors.',
            'A simple BOOL comparison capture is a retained counterexample: it exposes other refusals and does not produce an accepted project.',
            'The original captured ladder-conversion path stops near byte 21499 of 49049. The combined edit reaches the final program bytes and exposes failures in unchanged AlarmManager instruction groups.',
            'All eight refusals in the combined control reproduce in fresh public converter objects; they are not residual state from a preceding rejected request.'],
        limitations=actual['limitations']+[
            'Source correspondence is compiler-version-specific and is not a source-only compilation prediction.',
            'Normalized reference resolution does not establish complete ST semantics or complete declaration type coverage.',
            'Ordered packet coverage retains ambiguous positions and does not prove full consumption, acceptance or every ProgramCheck validation path.',
            'No changed project passed this checkpoint. Save/reopen was not performed after rejection.',
            'No controller or simulator execution, commit, push, release or public distribution occurred.'])
    output=ROOT/'research/results/gxw-fx-full-source-correspondence-20261001.json'
    public=ROOT/'research/evidence/gxw-fx-full-source-correspondence-20261001.zip'
    private=P/'gxw-fx-full-source-correspondence-20261001-local.zip'
    if any(path.exists() for path in (output,public,private)):raise FileExistsError('frozen checkpoint already exists')
    manifest['archives']=dict(public=public.relative_to(ROOT).as_posix(),local=private.relative_to(ROOT).as_posix())
    save(output,manifest)
    authored=[P/name for name in ('FXSourceStepOracle.cs','inspect_fx_full_st.py','inspect_fx_conversion_coverage.py',
        'probe_fx_full_source_edits.py','probe_fx_check_cascade.py','capture_original_fx_st.py','inspect_native_st_references.py','trace_native_source_points.js')]
    authored+=[ROOT/'research/native/LadderByteOracle.cs',Path(__file__),output]
    files=authored+list(proof.iterdir())
    with zipfile.ZipFile(public,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:archive.write(path,path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(public) as archive:
        if any(archive.read(path.relative_to(ROOT).as_posix())!=path.read_bytes() for path in files):raise ValueError('public archive bytes differ')
    local_files=[]
    for name in ('fx-original-full-st-20261001','fx-full-source-edits-20261001-v2','fx-full-source-edits-20261001-v3','fx-full-check-cascade-20261001'):
        local_files.extend(path for path in (P/name).rglob('*') if path.is_file())
    with zipfile.ZipFile(private,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in local_files:archive.write(path,path.relative_to(P).as_posix())
    with zipfile.ZipFile(private) as archive:
        if any(archive.read(path.relative_to(P).as_posix())!=path.read_bytes() for path in local_files):raise ValueError('local archive bytes differ')
    print(json.dumps(dict(public_files=len(files),public_bytes=public.stat().st_size,local_files=len(local_files),local_bytes=private.stat().st_size,
        original_records=3724,source_points=876,all_archive_file_bytes_equal=True)),flush=True)


if __name__=='__main__':main()
