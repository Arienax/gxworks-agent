"""Freeze pending FX SFC source predictions and their retained native controls.

Original projects, source grids, native traces and failed attempts are local.
Portable code and a numeric summary are separate. Files are compared directly.
"""
from pathlib import Path
import json
import struct
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'research/experiments/sfc-graph-20260926/public-corpus-discovery'
EXPERIMENTS = (
    'sfc-graph-backend-20261001-v1',
    *(f'sfc-graph-implementation-20261001-v{i}' for i in range(1, 7)),
    'fx-sfc-grid-records-20261001-v1', 'fx-sfc-grid-records-20261001-v2',
    'fx-sfc-grid-record-model-20261001-v1', 'fx-sfc-grid-record-model-20261001-v2',
    'fx-sfc-grid-prospective-20261001-v1', 'fx-sfc-grid-prospective-20261001-v2',
    'sfc-indirect-cell-reader-20261001-v1', 'fx-sfc-pending-source-projection-20261001-v1',
)


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')


def archive(path, entries):
    if len({name for _, name in entries}) != len(entries):
        raise ValueError('Duplicate archive entries')
    if any(Path(name).is_absolute() or '..' in Path(name).parts for _, name in entries):
        raise ValueError('Archive entry escapes its root')
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as output:
        for source, name in sorted(entries, key=lambda item: item[1]):
            output.write(source, name)
    with zipfile.ZipFile(path) as output:
        for source, name in entries:
            if output.read(name) != source.read_bytes():
                raise ValueError('Archived bytes differ: ' + name)
    return dict(entries=len(entries), bytes=path.stat().st_size, entry_bytes_exact=True)


def main():
    result = ROOT / 'research/results/gxw-sfc-pending-grid-20261001.json'
    public = ROOT / 'research/evidence/gxw-sfc-pending-grid-20261001.zip'
    local = P / 'gxw-sfc-pending-grid-20261001-local.zip'
    proof = P / 'sfc-pending-grid-frozen-20261001'
    if any(path.exists() for path in (result, public, local, proof)):
        raise FileExistsError('Frozen evidence must not be replaced')
    if any(not (P / name).is_dir() for name in EXPERIMENTS):
        raise FileNotFoundError('A retained experiment directory is missing')
    sys.path[:0] = [str(P), str(ROOT / 'src'), str(ROOT / 'research')]
    from probe_fx_sfc_shared_child import source_programs
    proof.mkdir()
    def entry(path):
        return 'experiments/' + Path(path).relative_to(P).as_posix()
    grids, unsupported = [], []
    for group, prospective in (('fx-sfc-grid-records-20261001-v2', False), ('fx-sfc-grid-prospective-20261001-v2', True)):
        for row in read(P / group / 'comparison.json'):
            directory = P / group / row['case']
            events = entry(directory / 'native/native-events.jsonl')
            if prospective and row['prediction']['handling'] != 'reference-projected':
                check, = [r for r in row['native_check'] if r['operation'] == 'SFC.CheckResult']
                unsupported.append(dict(case=row['case'], chars_entry=entry(directory / 'source-chars.bin'),
                    events_entry=events, native_check_result=check['result']))
                continue
            if row['hook_errors'] or (prospective and (not row['encoded_prediction_exact'] or not row['work_prediction_exact'])):
                raise ValueError('An accepted graph comparison differs')
            work = sorted(directory.glob('native-work-*.bin' if prospective else 'work-*.bin'), key=lambda f: int(f.stem.rsplit('-', 1)[1]))
            witness = dict(case=row['case'], prospective=prospective, chars_entry=entry(directory / 'source-chars.bin'),
                native_tokens_entry=entry(directory / 'native/sfc-encoded-tokens.bin'),
                native_work_entries=[entry(f) for f in work], events_entry=events)
            if prospective:
                witness['expected_tokens_entry'] = entry(directory / 'expected-tokens.bin')
            grids.append(witness)
    projects = []
    group = 'fx-sfc-pending-source-projection-20261001-v1'
    for row in read(P / group / 'comparison.json'):
        exact = ('resource_pcode_exact', 'independent_graph_exact', 'saved_child_bodies_exact',
                 'saved_action_registrations_exact', 'saved_source_prediction_exact', 'fresh_saved_compile_exact')
        if not all(row.get(k) is True for k in exact):
            raise ValueError('An accepted full-project comparison differs')
        directory = P / group / row['case']
        stages = []
        for source_path, native in ((directory / 'input.gxw', directory / 'compile'),
                                    (directory / 'compile/native-saved.gxw', directory / 'reopen')):
            resolver, programs = source_programs(source_path.read_bytes())
            (owner, program), = programs.items()
            declaration, = [f for f in resolver.logical_files() if f.logical_name.split('.')[0] == owner and f.logical_name.lower().endswith('.lh')]
            raw = resolver.hdb.read_stream(declaration.stream_name)
            count = struct.unpack_from('<I', raw, 54)[0]
            end = 58 + count * 2
            if not 1 <= count <= 8192 or raw[end:end+20] != bytes.fromhex('0000000100000000010000000000010000000000'):
                raise ValueError('Block declaration extent or shape differs')
            stages.append(dict(input_entry=entry(source_path), source_stream=program['stream'],
                block_number=struct.unpack_from('<I', raw, end + 20)[0],
                pcode_entry=entry(native / 'pcode-0-0.bin'), events_entry=entry(native / 'native-events.jsonl')))
        projects.append(dict(case=row['case'], input_entry=entry(directory / 'input.gxw'),
            source_stream=stages[0]['source_stream'], expected_pcode_entry=entry(directory / 'expected-pcode.bin'), stages=stages))
    counts = dict(source_graphs_exact=10, prospective_graphs_exact=6, native_work_records_exact=31,
        encoded_reference_bytes_exact=1261, unsupported_graphs_preserved=2,
        full_resource_predictions_exact=6, retained_native_full_checks_accepted=6, local_project_candidates_preserved=3)
    manifest = dict(schema=1, grids=grids, unsupported=unsupported, projects=projects,
        original_project_entry='inputs/three-steps-reopen.gxw', expected_cold_counts=counts)
    save(proof / 'checkpoint.json', manifest)
    private = [(proof / 'checkpoint.json', 'checkpoint.json'),
               (P.parent / 'three-steps-reopen.gxw', manifest['original_project_entry'])]
    for name in EXPERIMENTS:
        for path in (P / name).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                private.append((path, entry(path)))
    for pattern in ('dz-sfc-*-20261001.txt', 'fx-sfc-*-20261001.txt'):
        for path in P.glob(pattern):
            private.append((path, 'static/' + path.name))
    portable = []
    dependencies = proof / 'dependencies'
    dependencies.mkdir()
    replaced = {'models/source_fx_sfc_multireg_projection.py'}
    with zipfile.ZipFile(ROOT / 'research/evidence/gxw-sfc-local-mutation-20261001.zip') as old:
        for name in old.namelist():
            if not name.startswith(('models/', 'product/')) or name in replaced:
                continue
            path = (dependencies / name).resolve()
            if not path.is_relative_to(dependencies.resolve()):
                raise ValueError('Dependency entry escapes its root')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(old.read(name))
            portable.append((path, name))
    for name in ('source_sfc_grid.py', 'source_fx_sfc_grid_encoder.py', 'source_fx_sfc_pending_projection.py', 'source_fx_sfc_multireg_projection.py'):
        portable.append((P / name, 'models/' + name))
    portable.append((ROOT / 'src/gxw/container.py', 'product/src/gxw/container.py'))
    for name in ('TraceSFCGraphBackend.js', 'TraceSFCGraphImplementation.js', 'TraceFXSFCGridRecords.js',
                 'disassemble_sfc_codegen.py', 'disassemble_sfc_dnavi.py', 'disassemble_sfc_easysocket.py',
                 'trace_sfc_graph_backend.py', 'trace_sfc_graph_implementation.py', 'trace_fx_sfc_grid_records.py',
                 'probe_fx_sfc_grid_encoder.py', 'probe_fx_sfc_pending_projection.py'):
        portable.append((P / name, 'tools/' + name))
    for name in ('NativeFrontendSnapshot.cs', 'NativeSfcGraphOracle.cs', 'NativeWorkspaceCopyOracle.cs', 'WorkspaceReplayOracle.cs'):
        portable.append((P / group / 'untouched-control/compile' / name, 'tools/native/' + name))
    portable.extend(((Path(__file__), 'tools/' + Path(__file__).name),
                     (ROOT / 'research/validate_gxw_sfc_grid_checkpoint.py', 'validate_local.py'),
                     (ROOT / 'LICENSE', 'LICENSE')))
    for source, name in portable:
        if name.startswith(('tools/', 'models/')) or name == 'validate_local.py':
            private.append((source, name))
    local_info = archive(local, private)
    summary = dict(schema=1, date='2026-10-01', status='native-validated/research-prototype', counts=counts,
        versions=dict(graph_provider='ECCodeGeneratorFX2.dll 15.31', gxworks2_components='1.635.0.1', cpu=520, plc='FX3U/FX3UC'),
        source_model='21512-byte source grid -> native intermediate work records -> reference-only tokens -> first registered owned child bodies',
        evidence=dict(retrofitted_graphs=4, prospective_graphs=6, full_project_candidates=3,
            full_project_changes=['unchanged pending source', 'swap indirect action indexes', 'action reference 256 with matching declaration']),
        corrected_hypotheses=['SJMP opcode is 0x6200', 'Step number indexes an indirect action-record table; it does not directly select the action record'],
        retained_counterexamples=['Four early SJMP header predictions differed', 'Four early source edits omitted indirect action indexes',
            'Incomplete source is rejected by the native graph checker', 'Missing action index passes the graph checker but leaves a native output field unwritten'],
        handling='Unmeasured kinds, missing indexes and unmeasured traversal are refused; source, cache and opaque bytes remain raw',
        limits=['FX3U/FX3UC and the measured simple-SFC profile only',
            'Reference-only graph model excludes external ladder buffers; child expansion is checked separately',
            'Grid branches are controlled experiments, not broad real-corpus coverage',
            'Three full-project controls are derived from one pending native project',
            'Core mode-01 parsing has not been extended and no PLC execution is claimed'],
        archive_scope=dict(public='Models, pinned parser dependencies, native trace tools and offline validator; no third-party raw source or projects',
            local='Full source/native controls, proposed projects, fresh saved-project checks and all listed failed attempts'),
        local_archive=dict(path=local.relative_to(ROOT).as_posix(), **local_info),
        verification='Direct source/native byte comparison and offline replay; no new digests')
    save(proof / 'summary.json', summary)
    portable.append((proof / 'summary.json', 'summary.json'))
    public_info = archive(public, portable)
    cold = proof / 'cold-replay'
    cold.mkdir()
    with zipfile.ZipFile(public) as output:
        for name in output.namelist():
            path = (cold / name).resolve()
            if not path.is_relative_to(cold.resolve()):
                raise ValueError('Cold replay entry escapes its root')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(output.read(name))
    process = subprocess.run([sys.executable, str(cold / 'validate_local.py'), str(local)], cwd=cold,
                             capture_output=True, timeout=45)
    (proof / 'cold-replay.stdout.bin').write_bytes(process.stdout)
    (proof / 'cold-replay.stderr.bin').write_bytes(process.stderr)
    if process.returncode:
        raise RuntimeError('Cold replay failed; archives and raw failure output are retained')
    replay = json.loads(process.stdout.decode('utf-8'))
    summary.update(public_archive=dict(path=public.relative_to(ROOT).as_posix(), **public_info), cold_replay_counts=replay)
    save(result, summary)
    print(json.dumps(dict(result=str(result), counts=replay, public=public_info, local=local_info), ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
