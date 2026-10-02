"""Replay frozen Q context and patch evidence without native execution or digests."""
from pathlib import Path
import argparse
import base64
import json
import struct
import sys
import tempfile
import zipfile


def read(archive, name):
    return json.loads(archive.read(name).decode('utf-8-sig'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def complete(row, size):
    return row['code'] == 0 and row['consumed'] == size and row['remaining'] == 0 and row['input_unchanged'] and row['guards_intact']


def native_rows(archive, name):
    return {r['id']: r for r in (json.loads(line) for line in archive.read(name).decode('utf-8-sig').splitlines())}


def source_body(raw):
    require(len(raw) >= 99 and raw[54] == 1, 'Source envelope differs')
    size, repeat = struct.unpack_from('<II', raw, 55)
    require(size == repeat and size >= 20, 'Source sizes differ')
    end = 79+size-20
    require(end <= len(raw) and len(raw)-end in (20, 24), 'Source trailer outside observations')
    return raw[79:end]


def resource_bodies(raw):
    # Structural frame only: terminal lexical grammar/freshness comes from the
    # independently predicted PCode and the current native compiler outputs.
    prefix = bytes.fromhex('01000000000001000000000001000a00000000000200000001000000010000000000')
    require(raw.startswith(prefix) and len(raw) >= 78, 'Resource prefix differs')
    cursor, result = 54, []
    for _ in range(2):
        require(cursor+4 <= len(raw), 'Missing resource size')
        size = struct.unpack_from('<I', raw, cursor)[0]
        end = cursor+4+size
        require(end+8 <= len(raw) and raw[end:end+8] == bytes(8), 'Resource separator/extent differs')
        result.append(raw[cursor+4:end])
        cursor = end+8
    return result


def diagnostic_key(row):
    fields = ('kind', 'code', 'library_hex', 'name_hex', 'instance_kind', 'instance_hex',
              'program_kind', 'step', 'network', 'left', 'top', 'right', 'bottom', 'arguments_hex')
    return json.dumps({k: row[k] for k in fields}, sort_keys=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public', type=Path, required=True)
    parser.add_argument('--local', type=Path)
    args = parser.parse_args()
    counts = {}
    with tempfile.TemporaryDirectory(prefix='q-project-bound-replay-') as folder, zipfile.ZipFile(args.public) as public:
        temp = Path(folder)
        models = temp/'models'
        models.mkdir()
        for name in ('source_q_read_context.py', 'source_q_machine_reader.py', 'source_q_contact_patch.py'):
            (models/name).write_bytes(public.read('models/'+name))
        tables = temp/'static-read-tables.json'
        tables.write_bytes(public.read('profiles/static-read-tables.json'))
        sys.path.insert(0, str(models))
        from source_q_read_context import build_read_context, Unsupported
        from source_q_machine_reader import load_read_tables, read_machine
        from source_q_contact_patch import patch_st_contact
        def profile(parameter):
            return load_read_tables(tables, build_read_context(bytes.fromhex(parameter)).devices)
        def decoded(machine, selected):
            rows = read_machine(machine, selected)
            require(b''.join(bytes.fromhex(r['raw_hex']) for r in rows) == machine, 'Machine raw reconstruction differs')
            if any(r['handling'] != 'decoded' for r in rows):
                return None
            return b''.join(bytes.fromhex(r['pcode_hex']) for r in rows)
        controls = read(public, 'controls/context-before-native.json')
        native = native_rows(public, 'controls/context-native.jsonl')
        observed = read(public, 'controls/context-observed.json')
        require(observed['errors'] == [] and observed['traced_baseline_exact'], 'Context trace changed baseline')
        comparisons = {r['id']: r for r in observed['comparisons']}
        for row in controls['controls']:
            context = build_read_context(bytes.fromhex(row['parameter_hex']))
            expected = bytes.fromhex(row['table_hex'])
            require(context.devices == expected == bytes.fromhex(comparisons[row['id']]['actual_table_hex']), 'Context table prediction differs')
            machine = bytes.fromhex(row['machine_hex'])
            require(complete(native[row['id']], len(machine)), 'Context native input incomplete')
            require(decoded(machine, profile(row['parameter_hex'])) == bytes.fromhex(row['expected_pcode_hex']) ==
                    base64.b64decode(native[row['id']]['output_base64']), 'Context END decoding differs')
        for row in controls['unsupported']:
            try:
                build_read_context(bytes.fromhex(row['raw_hex']))
            except Unsupported:
                pass
            else:
                raise ValueError('Retained unsupported parameter now silently accepted')
            require(row['native_called'] is False, 'Unsupported parameter was called natively')
        counts.update(context_tables_exact=len(controls['controls']), unsupported_parameters=len(controls['unsupported']))
        minimal = read(public, 'controls/minimal-contact-patches.json')
        for row in minimal:
            selected = profile(row['parameter_hex'])
            machine = bytes.fromhex(row['original_machine_hex'])
            require(decoded(machine, selected) == bytes.fromhex(row['original_pcode_hex']), 'Minimal contact decoding differs')
            patched, _ = patch_st_contact(machine, selected, record_index=0, before_number=row['before_number'],
                after_number=row['after_number'], allocated_count=row['allocated_count'], expected_pcode=bytes.fromhex(row['expected_pcode_hex']))
            require(patched == bytes.fromhex(row['native_candidate_machine_hex']), 'Minimal contact mutation differs')
        counts['minimal_contact_patches_exact'] = len(minimal)
        require(counts == dict(context_tables_exact=95, unsupported_parameters=4, minimal_contact_patches_exact=5), 'Public coverage differs')
        if args.local:
            with zipfile.ZipFile(args.local) as local:
                core = temp/'core/gxw'
                core.mkdir(parents=True)
                (core/'__init__.py').write_text('"""Isolated frozen container reader modules."""\n', encoding='utf-8')
                for name in ('container.py', 'models.py', 'project_resolver.py'):
                    (core/name).write_bytes(local.read('core/gxw/'+name))
                sys.path.insert(0, str(core.parent))
                from gxw.container import CompoundFile
                from gxw.project_resolver import GXWProjectResolver
                def project(name):
                    return GXWProjectResolver(CompoundFile(local.read(name)))
                bound = 'experiments/q-machine-project-bound-corpus-20261001-v1'
                before = read(local, bound+'/before-reverse.json')
                reverses = native_rows(local, bound+'/reverse/output.jsonl')
                for case, row in before['projects'].items():
                    parameter = project('original-corpus/'+case+'/input.gxw').read_logical_file('Param.wpa')
                    expected = bytes.fromhex(row['parameter_hex'])
                    at = row['record_offset']
                    require(parameter[at:at+len(expected)] == expected, 'Parameter not bound to original project')
                    require(build_read_context(expected).devices.hex() == row['device_table_hex'], 'Project-derived read table differs')
                exact = {'isolated': 0, 'bodies': 0}
                opaque = 0
                for family, rows in (('isolated', before['requests']), ('bodies', before['bodies'])):
                    for row in rows:
                        machine = bytes.fromhex(row['machine_hex'])
                        result = decoded(machine, profile(row['parameter_hex']))
                        require(complete(reverses[row['id']], len(machine)), 'Project-bound reverse did not consume input')
                        if result is None:
                            require(family == 'isolated', 'Whole body unexpectedly opaque')
                            opaque += 1
                        else:
                            require(result == bytes.fromhex(row['original_pcode_hex']) == base64.b64decode(reverses[row['id']]['output_base64']), 'Project-bound independent decode differs')
                            exact[family] += 1
                require(exact == dict(isolated=625, bodies=55) and opaque == 1, 'Project-bound coverage differs')
                counts.update(projects_bound=len(before['projects']), isolated_native_source_exact=exact['isolated'],
                    full_bodies_native_source_exact=exact['bodies'], isolated_opaque_preserved=opaque)
                patch_root = 'experiments/q-lossless-project-patch-20261001-v1'
                run_root = 'experiments/q-lossless-project-patch-check-save-20261001-v3'
                conversions = read(local, patch_root+'/before-native-machine.json')['controls']
                converted = native_rows(local, patch_root+'/output.jsonl')
                for row in conversions:
                    label = 'original' if row['stage'] == 'original' else 'expected'
                    require(complete(converted[row['id']], len(row['pcode_hex'])//2), 'Patch conversion input incomplete')
                    require(base64.b64decode(converted[row['id']]['output_base64']) == local.read(patch_root+'/'+row['case']+f'/{label}-machine-{row["resource_index"]}.bin'), 'Whole machine prediction differs')
                totals = dict(physical_one_byte_patches=0, untouched_inner_streams=0, untouched_outer_streams=0,
                    machine_mutations_exact=0, current_check_resources_exact=0, backend_check_targets_complete=0,
                    public_check_projection_failures=0, preserved_backend_errors=0, saved_resources_exact=0,
                    reopened_pcode_bodies_exact=0)
                for case in ('case-16', 'case-18', 'case-23', 'case-30', 'case-31'):
                    base = patch_root+'/'+case
                    proposal = read(local, base+'/proposal-before-native.json')
                    original = local.read(base+'/original.gxw')
                    candidate = local.read(base+'/candidate.gxw')
                    differences = [i for i, (a,b) in enumerate(zip(original, candidate, strict=True)) if a != b]
                    require(differences == proposal['physical_changed_offsets'] and len(differences) == 1, 'Whole-file patch extent differs')
                    totals['physical_one_byte_patches'] += 1
                    a, b = project(base+'/original.gxw'), project(base+'/candidate.gxw')
                    stream = proposal['stream']
                    for entry in a.hdb.iter_streams():
                        if entry.name != stream:
                            require(a.hdb.read_stream(entry.name) == b.hdb.read_stream(entry.name), 'Unrelated input inner payload changed')
                            totals['untouched_inner_streams'] += 1
                    for entry in a.outer.iter_streams():
                        if entry.name != '_hdb':
                            require(a.outer.read_stream(entry.name) == b.outer.read_stream(entry.name), 'Unrelated input outer payload changed')
                            totals['untouched_outer_streams'] += 1
                    selected = profile(proposal['parameter_hex'])
                    for resource in proposal['resources']:
                        index = resource['index']
                        machine = local.read(base+f'/original-machine-{index}.bin')
                        expected = local.read(base+f'/expected-machine-{index}.bin')
                        patch = resource['machine_patch']
                        if patch is not None:
                            parameter = bytes.fromhex(proposal['parameter_hex'])
                            capacity, = (struct.unpack_from('<H', parameter, at+2)[0] for at in range(6,len(parameter),4) if struct.unpack_from('<H', parameter, at)[0] == 0x1C8)
                            changed, _ = patch_st_contact(machine, selected, record_index=patch['record_index'],
                                before_number=patch['before_number'], after_number=patch['after_number'], allocated_count=capacity,
                                expected_pcode=local.read(base+f'/expected-pcode-{index}.bin'))
                            require(changed == expected, 'Whole-body contact mutation differs')
                            totals['machine_mutations_exact'] += 1
                        else:
                            require(machine == expected, 'Untouched machine body changed')
                    diagnostics = []
                    for stage in ('original','candidate'):
                        run = run_root+'/'+case+'/'+stage+'-check-1'
                        events = [json.loads(line) for line in local.read(run+'/native-events.jsonl').decode('utf-8-sig').splitlines()]
                        packets = [json.loads(line) for line in local.read(run+'/events.jsonl').decode('utf-8').splitlines()]
                        trace = [r['payload'] for r in packets if r.get('type') == 'send']
                        require(not any(r.get('type') == 'error' or r.get('payload',{}).get('event') == 'observation-error' for r in packets), 'Check observation incomplete')
                        require(any(e.get('operation') == 'Workspace.GetProjectCompiler' and e.get('code') == 0 for e in events), 'Check used a separate compiler')
                        names = {tuple(e['id']):e['name'] for e in events if e.get('operation') == 'NativeObject'}
                        indices = {e['name']:e['index'] for e in events if e.get('operation') == 'Resource'}
                        checked = set()
                        for row in trace:
                            if row.get('event') != 'workspace-pcode-read' or row.get('phase') != 'check' or row['buffers'][0]['raw_hex'] is None:
                                continue
                            index = indices[names[tuple(row['target'])]]
                            expected = local.read(base+f'/{"original" if stage == "original" else "expected"}-pcode-{index}.bin')
                            require(bytes.fromhex(row['buffers'][0]['raw_hex']) == local.read(run+f'/pcode-{index}-0.bin') == expected, 'Check consumed stale code')
                            checked.add(index)
                        require(len(checked) == 2, 'A checked resource was not observed')
                        totals['current_check_resources_exact'] += len(checked)
                        progress = [r for r in trace if r.get('event') == 'backend-check-progress']
                        completed = {r['check_index'] for r in progress if r['hresult'] >= 0 and r['code'] == 0 and r['percent'] == 100}
                        require(completed == {0,1}, 'Backend did not complete both targets')
                        totals['backend_check_targets_complete'] += len(completed)
                        errors = {diagnostic_key(d) for r in progress for d in r['reports'] if d['kind'] == 2}
                        require(errors and not any(e.get('operation') == 'ProgramCheckCompleted' for e in events), 'Check outcome unexpectedly reclassified')
                        failures = [r for r in trace if r.get('event') == 'diagnostic-projection-failure']
                        require(len(failures) == 1 and failures[0]['code'] == 0x2D010025, 'Retained projection failure differs')
                        totals['public_check_projection_failures'] += 1
                        totals['preserved_backend_errors'] += len(errors)
                        diagnostics.append(errors)
                    require(diagnostics[0] == diagnostics[1], 'Mutation changed backend diagnostics')
                    persist = read(local, run_root+'/'+case+'/comparison.json')
                    persist_root = 'experiments/q-lossless-project-patch-check-save-20261001-v2/'+case if case == 'case-16' else run_root+'/'+case
                    saved = project(persist_root+'/candidate-save-separate/native-saved.gxw')
                    require(source_body(saved.read_logical_file(proposal['logical'])) == local.read(base+'/expected-source-body.bin'), 'Saved source differs')
                    require(not persist['save']['failures'] and not persist['reopen']['failures'], 'Persistence had native failures')
                    for resource in proposal['resources']:
                        index = resource['index']
                        expected = local.read(base+f'/expected-pcode-{index}.bin')
                        require(resource_bodies(saved.read_logical_file(resource['name'])) == [expected, expected], 'Saved code copies are stale')
                        totals['saved_resources_exact'] += 1
                        require(local.read(persist_root+f'/candidate-reopen-compile-separate/pcode-{index}-0.bin') == expected, 'Reopened compiler body differs')
                        totals['reopened_pcode_bodies_exact'] += 1
                require(totals['preserved_backend_errors'] == 66 and totals['physical_one_byte_patches'] == 5, 'Patch/failure coverage differs')
                counts.update(totals, whole_machine_controls_exact=len(conversions))
                # Preserve the contrast: current compiler output changed but a
                # separate compiler's workspace checks still consumed ST1.
                stale = 'experiments/q-lossless-project-patch-check-save-20261001-v2/case-16/candidate-check-1'
                packets = [json.loads(line) for line in local.read(stale+'/events.jsonl').decode('utf-8').splitlines()]
                old = local.read(patch_root+'/case-16/original-pcode-1.bin')
                new = local.read(stale+'/pcode-1-0.bin')
                matches = [r['payload'] for r in packets if r.get('type') == 'send' and r['payload'].get('event') == 'workspace-pcode-read'
                           and r['payload'].get('check_index') == 1 and r['payload']['buffers'][0]['raw_hex'] is not None]
                require(old != new and len(matches) == 2 and all(bytes.fromhex(r['buffers'][0]['raw_hex']) == old for r in matches), 'Stale-cache counterexample lost')
                counts['separate_compiler_stale_reads_preserved'] = len(matches)
    print(json.dumps(counts, ensure_ascii=True))


if __name__ == '__main__':
    main()
