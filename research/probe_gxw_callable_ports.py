"""Reproduce native callable graph controls without PLC access.

The fixture retains original library FB source, source/declaration replacements,
failure diagnostics and independently decoded PCode. This runner appends that
source to an isolated archived library and compares complete compiled bytes.
Optional lowering observations run in a second helper and must match the fresh
uninstrumented compile. They do not establish PLC runtime effects.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from gxw.lossless import inspect_project, sha256
from native_gxw_tokens import DEFAULT_DLL, native_batch, native_il_projection
from probe_gxw_library_archive import decode_library_archive, encode_library_literals
from probe_gxw_sfc_graph import patch_raw
from replay_gxw_workspace import NATIVE, prepare, run, trace_prepared


def trace_lowering(source: Path, output: Path, expected_pcode: bytes) -> dict:
    compiler = (NATIVE / '../Easysocket/Compiler/ECCompiler_IEC.dll').resolve()
    compiler_hash = 'f58e7c70b855526bf2f943399a14089f771a43109b13ee9af34c88fb065cf4d7'
    if sha256(compiler.read_bytes()) != compiler_hash:
        raise ValueError('callable lowering requires the observed ECCompiler_IEC 15.50')
    script = ROOT / 'research/native/TraceCallableLowering.js'
    attempts = []
    for attempt in range(3):
        directory = output / f'attempt-{attempt + 1}'
        prepare(source, directory, compile=True, export_project=True, project_alias='CALLABLEPORTS')
        process = trace_prepared(directory, script)
        native = [json.loads(line) for line in (directory / 'native-events.jsonl').read_text().splitlines()]
        attempts.append(dict(directory=directory.name, process=process, last_event=native[-1] if native else None))
        if not native or native[-1].get('operation') != 'NativeTemporaryDirectoryCollision':
            break
    raw = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]
    events = [row['payload'] for row in raw if row.get('type') == 'send']
    errors = [row for row in raw if row.get('type') == 'error'
              or row.get('payload', {}).get('event', '').endswith('-error')]
    modules = [row for row in events if row.get('event') == 'lowering-module']
    checks = [row for row in native if row.get('operation') == 'NativeCrtCharacterCase']
    code = directory / 'pcode-0-0.bin'
    # The native helper can unload and reload the same compiler during import.
    complete = (not process['timed_out'] and not errors and bool(modules)
                and all(Path(row['path']).resolve() == compiler for row in modules)
                and any(row.get('event') == 'lowering-compile' and row['observed'] for row in events)
                and any(row.get('event') == 'emit-record' for row in events)
                and len(checks) == 2 and all(row['valid'] for row in checks)
                and any(row.get('operation') == 'Progress' and row.get('percent') == 100 for row in native)
                and not any(d['kind'] == 2 for row in native for d in row.get('reports', []))
                and (directory / 'native-saved.gxw').is_file()
                and sha256((directory / 'input.gxw').read_bytes()) == sha256(source.read_bytes()))
    result = dict(schema=1, compiler_sha256=compiler_hash, script_sha256=sha256(script.read_bytes()),
                  source_sha256=sha256(source.read_bytes()), attempts=attempts, errors=errors,
                  observation_complete=bool(complete), events=events,
                  matches_uninstrumented_pcode=bool(complete and code.is_file()
                                                    and code.read_bytes() == expected_pcode),
                  pcode_sha256=sha256(code.read_bytes()) if code.is_file() else None,
                  instruction_coverage='not measured; observations cover generation of fixture program 1 only',
                  execution_effects='not inferred')
    (output / 'lowering.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def probe(output: Path, selected: list[str] | None = None, *, observe_lowering: bool = False) -> dict:
    fixture_path = ROOT / 'tests/fixtures/gxw_callable_ports.json'
    fixture = json.loads(fixture_path.read_text())
    provenance = fixture['provenance']
    archive = ROOT / provenance['source_archive']
    if sha256(archive.read_bytes()) != provenance['source_archive_sha256']:
        raise ValueError('archived native seed hash mismatch')
    with zipfile.ZipFile(archive) as z:
        source = z.read(provenance['source_member'])
    if sha256(source) != provenance['source_sha256']:
        raise ValueError('archived native project hash mismatch')
    cases = {row['case']: row for row in fixture['cases']}
    if selected is not None and (len(selected) != len(set(selected)) or set(selected) - cases.keys()):
        raise ValueError('unknown or repeated case')
    streams = {s.logical_name: s.raw for s in inspect_project(source).streams if s.logical_name}
    library = streams['IECFunction.lif']
    plain = decode_library_archive(library[20:]).decoded
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'probe.py').write_bytes(Path(__file__).read_bytes())
    (output / 'fixture.json').write_bytes(fixture_path.read_bytes())
    result = dict(schema=1, fixture_sha256=sha256(fixture_path.read_bytes()), provenance=provenance, cases=[])
    for name in selected if selected is not None else cases:
        case = cases[name]
        directory = output / name
        directory.mkdir()
        replacements = {n: base64.b64decode(v['base64'], validate=True) for n, v in case['replacements'].items()}
        if any(sha256(raw) != case['replacements'][n]['sha256'] for n, raw in replacements.items()):
            raise ValueError('fixture replacement hash mismatch')
        if 'library_append_base64' in case:
            appended = base64.b64decode(case['library_append_base64'], validate=True)
            replacements['IECFunction.lif'] = library[:20] + encode_library_literals(plain + appended)
        order = case.get('replacement_order', list(replacements))
        if len(order) != len(set(order)) or set(order) != replacements.keys():
            raise ValueError('fixture replacement order differs from changed streams')
        changed = source
        receipts = []
        for logical in order:
            raw = replacements[logical]
            changed, receipt = patch_raw(changed, logical, streams[logical], raw)
            receipts.append(receipt)
        actual = {s.logical_name: s.raw for s in inspect_project(changed).streams if s.logical_name}
        if actual != dict(streams, **replacements):
            raise ValueError('unrelated logical payload changed')
        if sha256(changed) != case['source_sha256']:
            raise ValueError('reproduced project differs from observed source')
        path = directory / 'source.gxw'
        path.write_bytes(changed)
        (directory / 'mutation.json').write_text(json.dumps(dict(source_sha256=sha256(changed),
            receipts=receipts, unrelated_payloads='byte-identical'), indent=2) + '\n')
        native = run(path, directory / 'native', compile=True, export_project=True, project_alias='CALLABLEPORTS')
        finished = (native.get('returncode') == 0 and native.get('compile_completed')
                    and len(native.get('native_crt_checks', [])) == 2
                    and all(check['valid'] for check in native['native_crt_checks']))
        expected_errors = {(d['code'], tuple(d['arguments'])) for d in case['diagnostics'] if d['kind'] == 2}
        actual_errors = {(d['code'], tuple(d['arguments'])) for d in native['diagnostics'] if d['kind'] == 2}
        row = dict(case=name, source_sha256=sha256(changed), native=native,
                   matches_observed_acceptance=bool(finished
                       and bool(native.get('native_export')) == case['native_exported']
                       and actual_errors == expected_errors
                       and bool(native.get('compiler_rejected')) == bool(expected_errors)))
        code = directory / 'native/pcode-0-0.bin'
        if native.get('native_export') and not native.get('compiler_rejected') and code.exists():
            blob = code.read_bytes()
            answer, = native_batch([dict(mode='decode', input_base64=base64.b64encode(blob + b'\0').decode())],
                directory / 'decode', dll=DEFAULT_DLL, cpu_code=0x208)
            row['decode_result'] = answer
            if answer['return_code'] == '0x00000000' and answer['consumed_bytes'] == len(blob):
                row['listing'] = native_il_projection(base64.b64decode(answer['output_base64']), encoding='gb18030')
            row['matches_observed_pcode'] = blob == base64.b64decode(case['pcode_base64'])
            row['pcode_sha256'] = sha256(blob)
            if observe_lowering:
                lowered = trace_lowering(path, directory / 'lowering', blob)
                row['lowering'] = {key: lowered[key] for key in
                    ['observation_complete', 'matches_uninstrumented_pcode', 'script_sha256']}
        result['cases'].append(row)
        (output / 'comparison.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({k: row.get(k) for k in ['case', 'matches_observed_acceptance', 'matches_observed_pcode', 'lowering']}), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='new directory; existing results are never overwritten')
    parser.add_argument('--case', action='append', help='repeat to select fixture cases; defaults to all')
    parser.add_argument('--trace-lowering', action='store_true', help='observe the pinned compiler in a second helper and compare against the fresh uninstrumented PCode')
    args = parser.parse_args()
    probe(args.output, args.case, observe_lowering=args.trace_lowering)
