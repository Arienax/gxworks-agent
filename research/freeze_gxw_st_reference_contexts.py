"""Freeze native ST reference, instruction and saved-coordinate evidence."""
from pathlib import Path
import json
import zipfile

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'research/experiments/sfc-graph-20260926/public-corpus-discovery'
NAME='gxw-st-reference-contexts-20261001'


def json_read(path):return json.loads(path.read_text(encoding='utf-8'))


def archive(path,entries):
    if path.exists():raise FileExistsError(path)
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,data in sorted(entries.items()):z.writestr(name,data)
    with zipfile.ZipFile(path) as z:
        if z.testzip() is not None:raise ValueError('archive CRC failure')
        if set(z.namelist())!=set(entries) or any(z.read(name)!=data for name,data in entries.items()):
            raise ValueError('archived bytes differ')


def main():
    manifest_path=ROOT/'research/results'/f'{NAME}.json'
    public_path=ROOT/'research/evidence'/f'{NAME}.zip'
    local_path=P/f'{NAME}-local.zip'
    if any(path.exists() for path in (manifest_path,public_path,local_path)):raise FileExistsError(NAME)
    original=json_read(P/'q-original-st-source-map-20261001/source-reference-correspondence.json')
    original_steps=json_read(P/'q-original-st-source-map-20261001/source-step-correspondence.json')
    contexts=json_read(P/'st-reference-contexts-20261001/comparison.json')
    accepted=next(r for r in contexts if r['case']=='two-instances')
    refused=next(r for r in contexts if r['case']=='local-shadow')
    saved_steps=json_read(P/'st-reference-contexts-20261001/two-instances/source-step-correspondence.json')
    coverage=json_read(P/'real-st-source-point-read-coverage-20261001.json')
    if original['validation']['native_operand_matches']!=179 or accepted['check']['rejected'] or refused['check'] is not None:
        raise ValueError('unexpected checkpoint observations')
    original_diagnostics=[]
    for row in original['backend_diagnostics']:
        d=row['diagnostic']
        original_diagnostics.append(dict(code=d['code'],step=d['step'],kind=d['kind'],
            arguments=[bytes.fromhex(v).decode('cp936') for v in d['arguments_hex']],
            source=[dict(definition=r['sink']['marker']['source']['definition'],line=r['sink']['marker']['source']['line']) for r in row['matching_instructions']]))
    result=dict(name=NAME,scope='GX Works2 project format, binding and compiler compatibility research; owned offline copies only',
        environment=dict(cpu='Q03UDV',compiler_iec='15.50',q_code_generator='15.41',native_cpu=209,native_versions=[25]),
        product_api='gxw.compiler_debug.compiler_st_source_points',
        original_project=dict(validation=original['validation'],independent_steps=original_steps['summary'],
            backend_rejected=True,backend_diagnostics=original_diagnostics),
        two_instances=dict(validation=accepted['correspondence'],native_check=accepted['check'],
            reopen_check=accepted['reopen']['check'],reopen_channels=accepted['reopen']['channels'],
            independent_steps=saved_steps['summary'],useLcd_binding=accepted['useLcd_binding']),
        local_global_collision=dict(frontend_rejected=True,diagnostics=[{k:r[k] for k in ('phase','kind','code','name','instance','top','left','arguments')} for r in refused['diagnostics']]),
        real_cached_project_reading=coverage['summary'],
        verification=dict(compiler_owner_tests_passed=123,diff_check_passed=True),
        limitations=['Native ST references use the snapshot accompanying that source; transient source-table offsets can change during compilation.',
            'Source points are physical coordinates and preserve each expansion identity. Multiline extents and statement ownership are not inferred.',
            'A caller point and an FB point can refer to the same code; stored endpoints can reach another expansion or FEND.',
            'The original project failed duplicate-coil checks; complete code emission does not turn that refusal into acceptance.',
            'The local/global same-name declaration was refused by this measured frontend; local shadowing was not adopted.',
            'Cached-coordinate coverage does not establish source freshness, project acceptance or runtime semantics.',
            'Portable whole-project recompilation is not completed by this checkpoint.'],
        public_archive=str(public_path.relative_to(ROOT)).replace('\\','/'),
        local_archive=str(local_path),
        original_and_vendor_content='Original GXW copies, normalized FB source, full traces and binaries are retained in the local archive only.')
    selected=[ROOT/'src/gxw/compiler_debug.py',ROOT/'tests/test_gxw_compiler.py',
        ROOT/'tests/fixtures/gxw_st_source_points_native.json',ROOT/'research/extract_gxw_st_source_points_fixture.py',
        Path(__file__).resolve()]
    selected += [P/name for name in ('CompilerSourcePointOracle.cs','QSourceStepOracle.cs','trace_native_source_points.js',
        'inspect_native_st_references.py','probe_st_reference_contexts.py','verify_st_reference_steps.py')]
    public={str(path.relative_to(ROOT)).replace('\\','/'):path.read_bytes() for path in selected}
    public['observations/native-source-point-reader-comparison.json']=(P/'q-original-st-source-20261001/source-reference-native/comparison.json').read_bytes()
    public['observations/real-cached-project-read-coverage.json']=(P/'real-st-source-point-read-coverage-20261001.json').read_bytes()
    public['observations/checkpoint.json']=(json.dumps(result,indent=2,ensure_ascii=True)+'\n').encode()
    local=dict(public)
    for name in ('q-original-st-source-20261001','q-original-st-source-map-20261001','st-reference-contexts-20261001'):
        for path in (P/name).rglob('*'):
            if path.is_file():local[str(path.relative_to(ROOT)).replace('\\','/')]=path.read_bytes()
    archive(public_path,public);archive(local_path,local)
    result['archive_contents']=dict(public_files=len(public),local_files=len(local),
        public_bytes=public_path.stat().st_size,local_bytes=local_path.stat().st_size,archived_file_bytes_verified=True)
    manifest_path.write_text(json.dumps(result,indent=2,ensure_ascii=True)+'\n',encoding='utf-8')
    print(json.dumps(dict(manifest=str(manifest_path),**result['archive_contents'])))


if __name__=='__main__':main()
