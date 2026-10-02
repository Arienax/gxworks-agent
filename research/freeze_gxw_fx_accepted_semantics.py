"""Freeze accepted full FX code and an independently checked BOOL counterexample.

Author-created minimal sources and numeric derived evidence are distributable.
Original project/library source, full native buffers and traces stay local.
Archives are compared directly to their input file bytes; no hashes are used.
"""
from pathlib import Path
import collections
import json
import zipfile

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'research/experiments/sfc-graph-20260926/public-corpus-discovery'


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def save(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=True)+'\n',encoding='utf-8')


def main():
    minimal=P/'fx-indexed-boolean-semantics-20261001-v1'
    grid=P/'fx-indexed-boolean-native-grid-20261001-v1'
    full=P/'fx-full-sampled-comparisons-20261001-v2'
    cases=read(minimal/'comparison.json');graphs=read(grid/'comparison.json');controls=read(full/'comparison.json')
    accepted=next(r for r in controls if r['case']=='partitioned-full-blocks')
    folder=full/accepted['case']
    sources=read(folder/'source-reference-correspondence-v2.json')
    saved=read(folder/'saved-source-point-correspondence.json')
    prefixes=read(folder/'independent-source-prefixes/summary.json')
    roundtrip=read(folder/'source-save-roundtrip.json')
    if len(cases)!=6 or not all(r['check'] is not None and not r['check']['rejected'] and
        r['reopen']['check'] is not None and not r['reopen']['check']['rejected'] and
        all(c['byte_identical'] for c in r['reopen']['channels']) and r['native_reading']['whole_listing_exact'] for r in cases):
        raise ValueError('six accepted minimal controls and native reopen are required')
    if {r['case'] for r in cases if not r['truth_table_exact']}!={'indexed-pair','indexed-condition'}:
        raise ValueError('semantic witness profile changed')
    if (len(graphs)!=10 or {(r['case'],r['comparison']) for r in graphs if not r['truth_table_exact']}!=
        {('indexed-pair','not_equal'),('indexed-pair','equal')}):
        raise ValueError('independent contact-graph witness profile changed')
    if (accepted['check'] is None or accepted['check']['rejected'] or accepted['backend_rejected'] is not False or accepted['errors'] or
        accepted['reopen']['check']['rejected'] or not all(r['byte_identical'] for r in accepted['reopen']['channels'])):
        raise ValueError('fresh full project acceptance and reopen required')
    if (accepted['native_reading']['raw_bytes']!=54203 or accepted['native_reading']['lexical_records']!=4196 or
        accepted['native_reading']['individual_step_matches']!=4196 or not accepted['native_reading']['whole_listing_exact']):
        raise ValueError('complete independent native listing and widths differ')
    if (sources['summary']['native_operand_matches']!=6672 or not sources['summary']['native_check_accepted'] or
        not sources['summary']['all_instructions_matched'] or sources['summary']['whole_compiler_groups']!=59):
        raise ValueError('whole source groups differ')
    if (saved['summary']['independent_start_coordinates']!=1055 or not saved['summary']['reopen_debug_bytes_exact'] or
        prefixes!=dict(finalized_source_points=1055,distinct_prefix_queries=917,exact_prefix_matches=917,source_points_verified=1055)):
        raise ValueError('saved/live source-coordinate proof differs')
    if not all(r.get('text_exact_after_save',r.get('name_type_exact_after_save',False)) for r in roundtrip):
        raise ValueError('edited source or declarations changed on save')
    output=ROOT/'research/results/gxw-fx-accepted-semantics-20261001.json'
    public=ROOT/'research/evidence/gxw-fx-accepted-semantics-20261001.zip'
    local=P/'gxw-fx-accepted-semantics-20261001-local.zip'
    proof=P/'fx-accepted-public-numeric-20261001'
    if any(path.exists() for path in (output,public,local,proof)):raise FileExistsError('preserve existing frozen checkpoint')
    proof.mkdir()
    save(proof/'minimal-source-controls.json',[dict(case=r['case'],check=r['check'],backend_rejected=r['backend_rejected'],
        source=read(minimal/r['case']/'before-native.json')['source'],native_reading=r['native_reading'],
        intended_truth_table=read(minimal/r['case']/'before-native.json')['expected_truth_table'],
        witnesses=r['witnesses'],truth_table_exact=r['truth_table_exact'],reopen=r['reopen']) for r in cases])
    save(proof/'minimal-compiled-main.json',[dict(case=r['case'],records=read(minimal/r['case']/'evaluated-main-records.json')) for r in cases])
    save(proof/'native-contact-graphs.json',graphs)
    save(proof/'native-contact-graph-inputs.json',read(grid/'before-native.json'))
    save(proof/'full-source-controls.json',[dict(case=r['case'],check=r['check'],backend_rejected=r['backend_rejected'],errors=r['errors'],
        hypothesis=read(full/r['case']/'before-native.json')['hypothesis'],
        changed_streams=read(full/r['case']/'before-native.json')['changed_streams'],
        unrelated_payloads_byte_exact=read(full/r['case']/'before-native.json')['unrelated_payloads_byte_exact'],
        native_reading=r['native_reading'],conversion_summary={k:v for k,v in r['conversion_summary'].items() if k!='resolved_packet_intervals'},
        reopen=r.get('reopen')) for r in controls])
    save(proof/'saved-source-coordinates.json',saved)
    width_rows=[c for c in read(folder/'independent-fx-reader/step-comparison.json') if not c['prefix']]
    save(proof/'native-record-widths.json',[dict(record_index=r['record_index'],byte_offset=r['byte_offset'],step=r['step'],
        kind=r['compiled']['kind'],mnemonic=r['compiled'].get('op'),stored_width=w['expected'],native_width=w['native'])
        for r,w in zip(sources['joined_instructions'],width_rows,strict=True)])
    resources=read(folder/'native-1/resource-correspondence.json')
    reads=[dict(phase=r['phase'],resource_name=r.get('resource_name'),check_index=r['check_index'],
        buffers=[dict(size=b['size'],matches_current=b['matches_current']) for b in r['buffers']]) for r in resources['resource_reads']]
    if not any(r['phase']=='check' and all(b['matches_current'] is True for b in r['buffers']) for r in reads):
        raise ValueError('check input bytes do not match generated code')
    save(proof/'checked-resource-bytes.json',dict(check=accepted['check'],backend_rejected=accepted['backend_rejected'],reads=reads))
    save(proof/'edited-source-save.json',roundtrip)
    warnings=collections.Counter(hex(d['code']) for d in accepted['diagnostics'] if d['kind']==3)
    manifest=dict(schema_version=1,date='2026-10-01',
        evidence_level='accepted copied full FX3G project; independent source coordinates and bounded Boolean semantic witnesses',
        environment=dict(os='Windows',compiler_iec='15.50',fx_code_generator='15.31',adapter_cpu=521,mode=0,versions=[]),
        source=dict(repository='https://github.com/Serhioromano/gxw2-libraries',independent_original_projects=1,
            original_project='cross-family-source-holdouts/case-00/input.gxw',original_bytes='retained locally'),
        minimal_controls=dict(cases=6,fresh_native_accepted=6,saved_reopen_accepted=6,truth_table_exact=4,retained_semantic_counterexamples=2),
        independent_contact_graphs=dict(exact_native_fragments=10,input_assignments=40,
            truth_tables_matching_intended_source=8,counterexample_truth_tables=2,
            fresh_converter_object_per_request=True,all_native_requests_accepted=True),
        full_project=dict(check=accepted['check'],backend_rejected=False,reopen=accepted['reopen'],
            warning_counts=dict(warnings),native_reading=accepted['native_reading'],source_correspondence=sources['summary'],
            independent_source_prefixes=prefixes,saved_source_coordinates=saved['summary'],
            converter_calls=1223,converter_refusals=0,changed_source_payloads_preserved_after_save=True),
        modifications=['Select master port 2 instead of the six mutually competing protocol demo initialization calls.',
            'Capture each of two indexed BOOL operands in a distinct declared BOOL local before either comparison site.',
            'Preserve all flat structure members while partitioning oversized channel and alarm copies into tautological branch blocks.',
            'Partition the eight alarm initialization writes and ten adjacent ticker/duration conversion calls without deleting a call or assignment.'],
        discoveries=['Fresh native check, save, reopen and byte-identical code do not establish the intended source truth table.',
            'For two indexed BOOL operands, the compiler retains the first sampled value but emits the second branch against the overwritten shared temporary.',
            'The assignment NE fragment represents left AND NOT right; EQ represents (left AND right) OR NOT right, instead of XOR/XNOR.',
            'An independent native contact grid agrees with the bounded instruction evaluator for both counterexamples and all scalar, one-indexed-operand and two-local controls.',
            'Distinct explicit operand captures remove both observed full-project comparison conversion failures. Separate branch partitioning removes seven remaining size refusals.',
            'All 4196 full-project records, 6672 operands and 1055 live source coordinates are independently checked; all 1055 saved debug starts agree and survive reopen unchanged.'],
        limitations=['All original full-project evidence comes from one FX3G project source and the recorded native versions.',
            'Boolean witnesses describe sampled operand values and bounded ordinary operations; they are not PLC or simulator execution tests.',
            'The independent contact-graph check covers assignment comparisons; the indexed IF counterexample has the separate instruction-level witness.',
            'The accepted full project is the single-protocol copied configuration, not the original six-protocol demo accepted unchanged.',
            'Whole-program runtime equivalence, complete source-only compilation prediction, hardware behavior and index-address execution semantics are not established.',
            'Forty native warnings remain as recorded; native acceptance does not resolve them.',
            'Stored expansion names and endpoints remain separate from live outlined-body identity and exclusive instruction ownership.',
            'Ordered conversion coverage retains 31 ambiguous packet positions and is not universal validation coverage.',
            'The new completed-check projection is in source-reference-correspondence-v2.json; older derived views and all raw observations are retained.',
            'No PLC, simulator, commit, push, release or public distribution occurred.'],
        archives=dict(public=public.relative_to(ROOT).as_posix(),local=local.relative_to(ROOT).as_posix()))
    save(output,manifest)
    authored=[P/name for name in ('probe_fx_indexed_boolean_semantics.py','probe_fx_indexed_boolean_graph.py','check_native_ladder_boolean_graph.py',
        'probe_fx_full_sampled_comparisons.py','inspect_fx_saved_source_points.py','inspect_fx_full_st.py','inspect_fx_conversion_coverage.py',
        'FXSourceStepOracle.cs','trace_native_source_points.js')]+[ROOT/'research/native/LadderByteOracle.cs',Path(__file__),output]
    files=authored+list(proof.iterdir())
    with zipfile.ZipFile(public,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:archive.write(path,path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(public) as archive:
        if any(archive.read(path.relative_to(ROOT).as_posix())!=path.read_bytes() for path in files):raise ValueError('public archived bytes differ')
    local_files=[path for directory in (minimal,grid,full) for path in directory.rglob('*') if path.is_file()]
    with zipfile.ZipFile(local,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in local_files:archive.write(path,path.relative_to(P).as_posix())
    with zipfile.ZipFile(local) as archive:
        if any(archive.read(path.relative_to(P).as_posix())!=path.read_bytes() for path in local_files):raise ValueError('local archived bytes differ')
    print(json.dumps(dict(public_files=len(files),public_bytes=public.stat().st_size,local_files=len(local_files),local_bytes=local.stat().st_size,
        accepted_full_records=4196,saved_live_source_matches=1055,all_archive_file_bytes_equal=True)),flush=True)


if __name__=='__main__':main()
