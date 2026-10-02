"""Replay frozen Q label edits from raw evidence without native execution."""
from pathlib import Path
import argparse
import base64
import json
import struct
import sys
import tempfile
import zipfile


def read(evidence, name):
    return json.loads(evidence.read(name).decode('utf-8-sig'))


def lines(evidence, name):
    return [json.loads(line) for line in evidence.read(name).decode('utf-8-sig').splitlines()]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def source_body(raw):
    require(len(raw) >= 99 and raw[54] == 1, 'Source envelope differs')
    size, repeat = struct.unpack_from('<II', raw, 55)
    end = 79 + size - 20
    require(size == repeat and size >= 20 and end <= len(raw) and len(raw)-end in (20, 24), 'Source extent differs')
    return raw[79:end]


def resource_bodies(raw):
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
    return json.dumps({key: row[key] for key in fields}, sort_keys=True)


def check_run(evidence, run, expected, source):
    """Bind completed checks to their own resource IDs and actual PCode reads."""
    require(evidence.read(run+'/input.gxw') == source, 'Check input differs from prospective project')
    events = lines(evidence, run+'/native-events.jsonl')
    packets = lines(evidence, run+'/events.jsonl')
    require(not any(r.get('type') == 'error' or r.get('payload', {}).get('event') == 'observation-error'
                    for r in packets), 'Check observation incomplete')
    trace = [r['payload'] for r in packets if r.get('type') == 'send']
    require(any(r.get('operation') == 'Workspace.GetProjectCompiler' and r.get('code') == 0 for r in events),
            'Check did not use project-owned compiler')
    require(any(r.get('operation') == 'Progress' and r.get('percent') == 100 for r in events), 'Compile incomplete')
    names = {tuple(r['id']): r['name'] for r in events if r.get('operation') == 'NativeObject'}
    indices = {r['name']: r['index'] for r in events if r.get('operation') == 'Resource'}
    for index, raw in expected.items():
        require(evidence.read(run+f'/pcode-{index}-0.bin') == raw, 'Fresh compiled PCode differs')
    starts = {r['check_index']: r for r in trace if r.get('event') == 'check-start'}
    public_targets = [tuple(r['id']) for r in events if r.get('operation') == 'ProgramCheckTarget']
    require([tuple(r['target']) for r in starts.values()] == public_targets, 'Observed check target sequence differs')
    require(starts and all(r['kind'] == 0x7FFFFFFF for r in starts.values()), 'Full check mask absent')
    observers = [r for r in trace if r.get('event') == 'backend-diagnostic-observer']
    require(observers and all(r['supported'] for r in observers), 'Backend observer unsupported')
    progress = [r for r in trace if r.get('event') == 'backend-check-progress']
    completed = {}
    for row in progress:
        start = starts[row['check_index']]
        require(tuple(row['target']) == tuple(start['target']), 'Progress target differs from check start')
        if row['hresult'] >= 0 and row['code'] == 0 and row['percent'] == 100:
            index = indices[names[tuple(row['target'])]]
            own_reads = [r for r in trace if r.get('event') == 'workspace-pcode-read' and r.get('phase') == 'check'
                         and r.get('check_index') == row['check_index'] and tuple(r['target']) == tuple(row['target'])
                         and tuple(r['check_target']) == tuple(row['target']) and r['buffers'][0]['raw_hex'] is not None]
            require(own_reads and all(bytes.fromhex(r['buffers'][0]['raw_hex']) == expected[index] for r in own_reads),
                    'Completed target did not read its own current code')
            reports = {diagnostic_key(d) for r in progress if r['check_index'] == row['check_index']
                       for d in r['reports'] if d['kind'] in (2, 3)}
            completed[index] = reports
    public = [r for r in events if r.get('operation') == 'ProgramCheckCompleted']
    failures = [r for r in trace if r.get('event') == 'diagnostic-projection-failure']
    require(all(r['code'] == 0x2D010025 and r['hresult'] < 0 for r in failures), 'Unexpected projection failure')
    if public:
        require(len(public) == 1 and public[0]['targets'] == len(starts) and len(completed) == len(starts)
                and not failures, 'Public completion conflicts with raw check evidence')
        outcome = 'rejected' if public[0]['rejected'] else 'passed'
    else:
        require(failures, 'Missing public completion without retained projection failure')
        outcome = 'incomplete'
    return dict(completed=completed, outcome=outcome, projection_failures=len(failures))


def replay_local(local, manifest, temp, profile, decoded, patch_bit_contact):
    core = temp/'core/gxw'
    core.mkdir(parents=True)
    (core/'__init__.py').write_text('"""Frozen container readers."""\n', encoding='utf-8')
    for name in ('container.py', 'models.py', 'project_resolver.py'):
        (core/name).write_bytes(local.read('core/gxw/'+name))
    sys.path.insert(0, str(core.parent))
    from gxw.container import CompoundFile
    from gxw.project_resolver import GXWProjectResolver
    def project(raw):
        return GXWProjectResolver(CompoundFile(raw))
    counts = dict(projects_modified=0, physical_changed_bytes=0, untouched_inner_streams=0, untouched_outer_streams=0,
                  full_machine_mutations_exact=0, whole_machine_controls_exact=0, compiled_check_resources_exact=0,
                  own_backend_targets_completed=0, backend_diagnostics_unchanged_projects=0,
                  saved_resource_copies_exact=0, reopened_pcode_bodies_exact=0,
                  public_passed_projects=0, public_rejected_projects=0, public_incomplete_projects=0,
                  projection_failures=0, completed_supplemental_targets=0, preserved_backend_errors=0)
    controls = read(local, manifest['candidate_root']+'/controls-before-native.json')
    native = {r['id']: r for r in lines(local, manifest['candidate_root']+'/output.jsonl')}
    for row in controls:
        result = native[row['id']]
        require(result['code'] == 0 and result['consumed'] == len(row['pcode_hex'])//2 and result['remaining'] == 0
                and result['input_unchanged'] and result['guards_intact'], 'Memory conversion incomplete')
        machine = local.read(manifest['candidate_root']+'/'+row['case']+f'/{row["stage"]}-machine-{row["resource_index"]}.bin')
        require(base64.b64decode(result['output_base64']) == machine, 'Whole machine prediction differs from Native')
        counts['whole_machine_controls_exact'] += 1
    for case in manifest['cases']:
        base = manifest['candidate_root']+'/'+case['case']
        proposal = read(local, base+'/proposal-before-native.json')
        original, candidate = (local.read(base+'/'+stage+'.gxw') for stage in ('original', 'candidate'))
        differences = [i for i, (a, b) in enumerate(zip(original, candidate, strict=True)) if a != b]
        require(differences == proposal['physical_changed_offsets'] and 1 <= len(differences) <= 4, 'Input patch extent differs')
        counts['physical_changed_bytes'] += len(differences)
        a, b = project(original), project(candidate)
        parameter = bytes.fromhex(proposal['parameter_hex'])
        require(parameter in a.read_logical_file('Param.wpa') and local.read(base+'/project-parameters.bin') == parameter,
                'Read context not bound to original project')
        old_source, new_source = (r.read_logical_file(proposal['logical']) for r in (a, b))
        at, old, new = proposal['source_token_offset'], bytes.fromhex(proposal['original_token_hex']), bytes.fromhex(proposal['replacement_token_hex'])
        require(old_source[at:at+len(old)] == old and new_source == old_source[:at]+new+old_source[at+len(old):],
                'Source edit changed more than selected reference')
        require(source_body(new_source) == local.read(base+'/expected-source-body.bin'), 'Prospective source body differs')
        for entry in a.hdb.iter_streams():
            if entry.name != proposal['stream']:
                require(a.hdb.read_stream(entry.name) == b.hdb.read_stream(entry.name), 'Unrelated inner stream changed')
                counts['untouched_inner_streams'] += 1
        for entry in a.outer.iter_streams():
            if entry.name != '_hdb':
                require(a.outer.read_stream(entry.name) == b.outer.read_stream(entry.name), 'Unrelated outer stream changed')
                counts['untouched_outer_streams'] += 1
        selected = profile(proposal['parameter_hex'])
        index = proposal['machine_resource_index']
        machine = local.read(base+f'/original-machine-{index}.bin')
        expected_machine = local.read(base+f'/expected-machine-{index}.bin')
        mutation = proposal['machine_mutation']
        expected_pcode = local.read(base+f'/expected-pcode-{index}.bin')
        require(decoded(machine, selected) == local.read(base+f'/original-pcode-{index}.bin'), 'Original independent read differs')
        changed, actual_mutation = patch_bit_contact(machine, selected, record_index=mutation['record_index'],
            device_code=proposal['device_code'], before_number=proposal['before_number'], after_number=proposal['after_number'],
            allocated_count=proposal['capacity'], expected_pcode=expected_pcode)
        require(changed == expected_machine and actual_mutation == mutation, 'Whole-body mutation differs')
        counts['full_machine_mutations_exact'] += 1
        diagnostics, outcomes = [], []
        for stage in ('original', 'candidate'):
            label = 'original' if stage == 'original' else 'expected'
            expected = {r['index']: local.read(base+f'/{label}-pcode-{r["index"]}.bin') for r in proposal['resources']}
            source = original if stage == 'original' else candidate
            full = check_run(local, case['checks'][stage], expected, source)
            counts['compiled_check_resources_exact'] += len(expected)
            outcomes.append(full['outcome'])
            counts['projection_failures'] += full['projection_failures']
            completed = full['completed']
            for supplement in case['supplements'][stage]:
                extra = check_run(local, supplement, expected, source)
                counts['projection_failures'] += extra['projection_failures']
                counts['completed_supplemental_targets'] += len(extra['completed'])
                for target, reports in extra['completed'].items():
                    if target in completed:
                        require(completed[target] == reports, 'Repeated own-target diagnostics differ')
                    completed[target] = reports
            require(set(completed) == set(expected), 'Own backend target coverage incomplete')
            diagnostics.append(completed)
            counts['own_backend_targets_completed'] += len(completed)
            counts['preserved_backend_errors'] += sum(json.loads(d)['kind'] == 2 for rows in completed.values() for d in rows)
        require(diagnostics[0] == diagnostics[1], 'Original/candidate own-target diagnostics differ')
        require(outcomes[0] == outcomes[1], 'Original/candidate public outcome differs')
        counts['backend_diagnostics_unchanged_projects'] += 1
        counts['public_'+outcomes[0]+'_projects'] += 1
        saved = project(local.read(case['save']+'/native-saved.gxw'))
        require(local.read(case['save']+'/input.gxw') == candidate, 'Save input differs')
        require(source_body(saved.read_logical_file(proposal['logical'])) == source_body(new_source), 'Saved source differs')
        for resource in proposal['resources']:
            index = resource['index']
            expected = local.read(base+f'/expected-pcode-{index}.bin')
            require(local.read(case['save']+f'/pcode-{index}-0.bin') == expected, 'Save compiler output differs')
            require(resource_bodies(saved.read_logical_file(resource['name'])) == [expected, expected], 'Saved code copies differ')
            counts['saved_resource_copies_exact'] += 2
            require(local.read(case['reopen']+f'/pcode-{index}-0.bin') == expected, 'Reopened compiler output differs')
            counts['reopened_pcode_bodies_exact'] += 1
        require(local.read(case['reopen']+'/input.gxw') == local.read(case['save']+'/native-saved.gxw'), 'Reopen input differs')
        counts['projects_modified'] += 1
    failure = read(local, manifest['candidate_root']+'/case-10-unsupported/failure.json')
    require(failure['unsupported'] and not failure['native_called'] and 'type' in failure['reason'].lower(), 'Unsupported source failure lost')
    counts['unsupported_source_projects_preserved'] = 1
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public', type=Path, required=True)
    parser.add_argument('--local', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='q-label-replay-') as directory, zipfile.ZipFile(args.public) as public:
        temp = Path(directory)
        for name in ('source_q_machine_reader.py', 'source_q_read_context.py', 'source_q_contact_patch.py'):
            (temp/name).write_bytes(public.read('models/'+name))
        tables = temp/'static-read-tables.json'
        tables.write_bytes(public.read('profiles/static-read-tables.json'))
        sys.path.insert(0, str(temp))
        from source_q_machine_reader import load_read_tables, read_machine
        from source_q_read_context import build_read_context
        from source_q_contact_patch import patch_bit_contact
        def profile(parameter):
            return load_read_tables(tables, build_read_context(bytes.fromhex(parameter)).devices)
        def decoded(machine, selected):
            rows = read_machine(machine, selected)
            require(b''.join(bytes.fromhex(r['raw_hex']) for r in rows) == machine, 'Raw machine reconstruction differs')
            require(all(r['handling'] == 'decoded' for r in rows), 'Unexpected opaque machine record')
            return b''.join(bytes.fromhex(r['pcode_hex']) for r in rows)
        minimal = read(public, 'controls/minimal-contact-patches.json')
        for row in minimal:
            selected = profile(row['parameter_hex'])
            machine = bytes.fromhex(row['original_machine_hex'])
            require(decoded(machine, selected) == bytes.fromhex(row['original_pcode_hex']), 'Minimal independent decoding differs')
            result, _ = patch_bit_contact(machine, selected, record_index=0, device_code=row['device_code'],
                before_number=row['before_number'], after_number=row['after_number'], allocated_count=row['allocated_count'],
                expected_pcode=bytes.fromhex(row['expected_pcode_hex']))
            require(result == bytes.fromhex(row['native_candidate_machine_hex']), 'Minimal numeric mutation differs')
        counts = dict(minimal_contact_patches_exact=len(minimal))
        require(len(minimal) == 30, 'Public witness coverage differs')
        if args.local:
            with zipfile.ZipFile(args.local) as local:
                manifest = read(local, 'replay-manifest.json')
                counts.update(replay_local(local, manifest, temp, profile, decoded, patch_bit_contact))
    print(json.dumps(counts, ensure_ascii=True))


if __name__ == '__main__':
    main()
