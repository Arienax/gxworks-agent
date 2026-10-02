"""Freeze the accepted controls and their preserved native refusal cases."""
from pathlib import Path
import json, sys, zipfile

ROOT = Path(__file__).resolve().parents[1]
P = ROOT/'research/experiments/sfc-graph-20260926/public-corpus-discovery'
sys.path.insert(0,str(P))
from analyze_primitive_array_geometry import GROUPS

NAME = 'gxw-primitive-array-geometry-20261001'


def write_archive(path, payloads):
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as output:
        for name,raw in sorted(payloads.items()):output.writestr(name,raw)
    with zipfile.ZipFile(path) as output:
        assert output.testzip() is None
        for name,raw in payloads.items():assert output.read(name)==raw


def main():
    target=ROOT/'research/results'/(NAME+'.json')
    archive=ROOT/'research/evidence'/(NAME+'.zip')
    local=P/(NAME+'-local.zip')
    assert not target.exists() and not archive.exists() and not local.exists(), 'preserve frozen observations'
    analysis=json.loads((P/'primitive-array-geometry-analysis-20261001.json').read_text(encoding='utf-8'))
    fixture=json.loads((ROOT/'tests/fixtures/gxw_array_references_native.json').read_text(encoding='utf-8'))
    manifest=dict(
        scope='Compiler-cache array addresses and native compile/check/save/reopen. No PLC execution or new writer certification.',
        handling='Decoded stored-cache references; unknown records remain raw. Generic unbound arrays and unmeasured member/allocation forms remain unsupported.',
        evidence_level='Verified within the recorded native profiles; no claim of general production safety.',
        findings=dict(
            primitive_root_types=['BOOL','INT','WORD','DWORD','DINT','REAL','TIME','STRING'],
            units='BOOL uses M bits; INT/WORD use one D word; DWORD/DINT/REAL/TIME use two D words.',
            string='The stored length parameter + 1 is rounded up to words per element, before multiplication by array count. Native PCode controls cover parameters 1, 4, 5, 12.',
            coordinates='Negative lower bounds and rank 2/3 row-major coordinates match native operands. The axis control distinguishes first, middle and final dimension strides.',
            structure_members='BOOL/INT/WORD/DWORD only in the current reader profile; exact referenced type POU identities and separate word/bit strides.',
            refusals='Typed literal syntax failed at the first WORD assignment; the 48-word-operation flat case failed its fresh backend check; branch splitting encountered the existing unset P range. Original attempts remain available.',
            limits='The exact long-segment length limit and its interaction with instruction mix remain undetermined.'),
        summary=analysis['summary'], observations=analysis['cases'],
        independence=dict(
            predictions='Reservations and relative offsets were saved before each native invocation. Absolute bases were read after native use; complete output bytes were not predicted.',
            addresses='47 unique native PCode literal writes match cache-derived addresses; 3 BOOL points are presence-only observations and excluded from the added address fixture.',
            decoder='The existing Q lexical decoder has earlier independent native converter comparisons. This batch did not call the native converter again.',
            check_freshness='Nonempty buffers actually read by each native checker and rechecker equal all corresponding current generated channels byte-for-byte. No hash comparison is used for this check.'),
        native_environment=dict(compiler='ECCompiler_IEC.dll 15.50',cpu='Q03UDV',host='Windows x64; native helpers x86',
            isolation='Copied engineering projects; no PLC/simulator execution. Existing native adapter initialization, version binding and fresh-check sequence retained.'),
        product=dict(entry='src/gxw/compiler_symbols.py:compiler_array_reference',
            fixture='tests/fixtures/gxw_array_references_native.json',cases=len(fixture['cases']),references=sum(len(c['references']) for c in fixture['cases']),
            original_cache_queries='172 resolved / 4 unsupported, 65 byte-identical original cache reconstructions; the 4 remaining queries are unbound generic arrays.',
            validation='python -m pytest -q tests/test_gxw_compiler.py: 114 passed'),
        preceding='research/results/gxw-array-references-20261001.json',
        redistribution='Public archive contains authored source, controlled-source PCode, selected native records and numeric observations. Original GXW containers, vendor DLLs and full native traces remain in the local archive.',
        archive=archive.relative_to(ROOT).as_posix(),local_archive=local.relative_to(ROOT).as_posix())
    payloads={}
    for name in ('src/gxw/compiler_assignment.py','src/gxw/compiler_symbols.py','src/gxw/compiler_tables.py',
                 'tests/test_gxw_compiler.py','tests/fixtures/gxw_array_references_native.json',
                 'research/extract_gxw_array_reference_cases.py','research/freeze_gxw_primitive_array_geometry.py'):
        payloads[name]=(ROOT/name).read_bytes()
    for name in ('probe_primitive_array_geometry.py','analyze_primitive_array_geometry.py','primitive-array-geometry-analysis-20261001.json'):
        payloads['observations/'+name]=(P/name).read_bytes()
    local_files={}
    for group in GROUPS:
        for name in ('prediction-before-native.json','comparison.json','core-listing.json'):
            path=P/group/name
            if path.exists():payloads['observations/'+group+'/'+name]=path.read_bytes()
        for path in sorted((P/group).rglob('*')):
            if path.is_file():local_files[path.relative_to(P).as_posix()]=path.read_bytes()
    manifest['files']=[dict(path=name,bytes=len(raw)) for name,raw in sorted(payloads.items())]
    serialized=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    payloads['manifest.json']=serialized
    local_files['manifest.json']=serialized
    write_archive(archive,payloads)
    write_archive(local,local_files)
    target.write_bytes(serialized)
    print(json.dumps(dict(public_files=len(payloads),public_bytes=archive.stat().st_size,
                         local_files=len(local_files),local_bytes=local.stat().st_size,summary=analysis['summary'])))


if __name__=='__main__':main()
