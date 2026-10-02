"""Freeze derived array-reference evidence without original project files."""
from pathlib import Path
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'research/experiments/sfc-graph-20260926/public-corpus-discovery'
NAME = 'gxw-array-references-20261001'


def main():
    archive = ROOT / 'research/evidence' / (NAME + '.zip')
    manifest_path = ROOT / 'research/results' / (NAME + '.json')
    assert not archive.exists() and not manifest_path.exists(), 'preserve the frozen checkpoint'
    fixture = json.loads((ROOT / 'tests/fixtures/gxw_array_references_native.json').read_text(encoding='utf-8'))
    coverage = json.loads((P / 'real-array-reference-read-string-after-20261001.json').read_text(encoding='utf-8'))
    extents = json.loads((P / 'compiler-primitive-extents-20261001/comparison.json').read_text(encoding='utf-8'))
    manifest = dict(
        scope='Stored compiler-cache point references; no source freshness, current device capacity, PLC execution or writer certification.',
        handling='decoded derived references with native raw records attached; other assignments and unknown bytes retain their previous handling',
        evidence=dict(level='verified within the stated profiles',
            fixture_cases=len(fixture['cases']), fixture_references=sum(len(c['references']) for c in fixture['cases']),
            cache_crosscheck_references=64, native_pcode_string_references=8,
            native_extent_queries=len(extents), native_extent_matches=sum(r['equal'] for r in extents),
            extent_predictions='saved before the native helper calls',
            reference_checks='retrospective comparisons against previously saved native compilations'),
        independent_paths=dict(
            fx='Prior source-only layout/PCode predictions versus native compile/check/save/reopen supplied the independent path. The old cache cross-check and this reader share cache-layout assumptions.',
            string='Native-generated PCode operands from two accepted and reopened cases agree with cache-derived element addresses. These checks are retrospective, not prospective source compilation predictions.',
            extent='ECCompiler_IEC 15.50 native scalar extent helper; separate allocator inspection rounds each element to words before multiplying array count. Special type codes 65..68 return allocation units, not ordinary scalar byte widths.'),
        boundaries=dict(
            families=['compact M', 'compact D'],
            primitive_arrays=['BOOL', 'INT', 'WORD', 'DWORD', 'STRING'],
            structures='Flat structure arrays; BOOL/INT/WORD/DWORD scalar or array members. Exact type POU identity; separate bit/word strides in form 0x0c.',
            rank=dict(root_max=3, member_max=2),
            fixture_origins='One FX3G lineage and one Q03UDV lineage; STRING parameters 5, 6, 12 in native PCode checks.',
            original_string='One stored Q03UDV STRING parameter 1 array now resolves; not a new fresh compilation.',
            unsupported=['nested aggregate members', 'scalar STRING members', 'inline operand bases', 'other allocation forms', 'unbound generic arrays'],
            fixture='Sparse graph projection, not a complete CGTable. Unknown bytes in included records are preserved; omitted records have no preservation claim.'),
        coverage={k:v for k,v in coverage.items() if k!='rows'},
        validation=dict(command='python -m pytest -q tests/test_gxw_compiler.py', result='110 passed',
            diff_check='git diff --check passed; Windows line-ending notices only'),
        native_environment=dict(os='Windows x86 native helpers on Windows x64 host', compiler='ECCompiler_IEC.dll 15.50',
            guards='FileVersion and relevant instruction bytes, no new hash verification'),
        preceding_evidence=['research/results/gxw-fx-function-layout-20260930/manifest.json',
            'research/results/gxw-fx-member-arrays-20260930/manifest.json',
            'research/results/gxw-inline-assignments-20260930.json'],
        redistribution='Authored adapters, numeric observations, selected native cache records and derived controlled-source PCode only. No original GXW, original source, vendor DLL or original compiler parameter block.',
        archive=archive.relative_to(ROOT).as_posix())
    payloads = {}
    for name in ('src/gxw/compiler_assignment.py', 'src/gxw/compiler_symbols.py', 'src/gxw/compiler_tables.py',
                 'tests/test_gxw_compiler.py', 'tests/fixtures/gxw_array_references_native.json',
                 'research/extract_gxw_array_reference_cases.py', 'research/freeze_gxw_array_references_checkpoint.py'):
        payloads[name] = (ROOT / name).read_bytes()
    for name in ('CompilerPrimitiveExtentOracle.cs', 'probe_compiler_primitive_extents.py',
                 'recheck_array_reference_queries.py', 'real-array-reference-read-coverage-20261001.json',
                 'real-array-reference-read-string-after-20261001.json',
                 'nested-st-string-holdouts-plan.json', 'st-string-intrinsic-index-controls-plan.json'):
        payloads['observations/' + name] = (P / name).read_bytes()
    for path in sorted((P / 'compiler-primitive-extents-20261001').iterdir()):
        if path.suffix not in ('.exe', '.cs'):
            payloads['observations/compiler-primitive-extents-20261001/' + path.name] = path.read_bytes()
    for group in ('nested-st-string-holdouts-20260930', 'st-string-intrinsic-index-controls-20260930'):
        for name in ('comparison.json', 'native-listings.json'):
            payloads['observations/' + group + '/' + name] = (P / group / name).read_bytes()
    for case in fixture['cases']:
        if case['evidence']['kind'] == 'saved-cache-crosscheck':
            path = ROOT / case['evidence']['source']
            payloads['observations/' + case['case'] + '/independent-cache-member-references.json'] = path.read_bytes()
    manifest['files'] = [dict(path=name, bytes=len(raw)) for name,raw in sorted(payloads.items())]
    serialized = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        output.writestr('manifest.json', serialized)
        for name,raw in sorted(payloads.items()):
            output.writestr(name, raw)
    with zipfile.ZipFile(archive) as output:
        assert output.testzip() is None
        assert output.read('manifest.json') == serialized
        for name,raw in payloads.items():
            assert output.read(name) == raw
    manifest_path.write_bytes(serialized)
    print(json.dumps(dict(archive=str(archive),files=len(payloads)+1,bytes=archive.stat().st_size,
        cases=manifest['evidence']['fixture_cases'],references=manifest['evidence']['fixture_references'])))


if __name__ == '__main__':
    main()
