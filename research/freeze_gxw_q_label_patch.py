"""Freeze broad Q label edits, persistence and check correspondence evidence.

Original engineering projects and complete programs stay in a local archive.
The public archive contains numeric contact witnesses and bounded pure models.
Cold replay compares raw bytes and target identities; it runs no native helper.
"""
from pathlib import Path
import base64
import json
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
P = ROOT/'research/experiments/sfc-graph-20260926/public-corpus-discovery'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2)+'\n', encoding='utf-8')


def archived(path):
    return 'experiments/'+Path(path).relative_to(P).as_posix()


def archive(path, entries):
    if len({name for _, name in entries}) != len(entries):
        raise ValueError('Duplicate archive entry')
    if any(Path(name).is_absolute() or '..' in Path(name).parts for _, name in entries):
        raise ValueError('Archive entry escapes root')
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as output:
        for source, name in sorted(entries, key=lambda row: row[1]):
            output.write(source, name)
    with zipfile.ZipFile(path) as output:
        for source, name in entries:
            if output.read(name) != source.read_bytes():
                raise ValueError('Archived bytes differ: '+name)
    return dict(entries=len(entries), bytes=path.stat().st_size, entry_bytes_exact=True)


def main():
    result = ROOT/'research/results/gxw-q-label-patch-20261001.json'
    public = ROOT/'research/evidence/gxw-q-label-patch-20261001.zip'
    local = P/'gxw-q-label-patch-20261001-local.zip'
    proof = P/'q-label-patch-frozen-20261001'
    if any(path.exists() for path in (result, public, local, proof)):
        raise FileExistsError('Frozen evidence must not be replaced')
    candidate = P/'q-label-contact-candidates-20261001-v1'
    runs = P/'q-label-contact-native-20261001-v1'
    require = read(candidate/'summary.json')
    if require['proposals'] != 30 or require['memory_controls'] != 60 or require['failures']:
        raise ValueError('Prospective label controls incomplete')
    cases, reused = [], set()
    for directory in sorted(runs.glob('case-*')):
        if not (directory/'comparison.json').exists():
            continue
        row = read(directory/'comparison.json')
        if (not row['whole_compile_pcode_exact'] or not row['saved_source_body_exact'] or
            not row['saved_complete_source_predictions_exact'] or row['save']['failures'] or row['reopen']['failures'] or
            not all(r['save_pcode_exact'] and r['reopen_pcode_exact'] for r in row['resources'])):
            raise ValueError('Native compile/persistence incomplete')
        checks = {}
        for stage, check in row['checks'].items():
            path = Path(check['reused_from']) if 'reused_from' in check else Path(check['attempts'][-1]['directory'])
            checks[stage] = archived(path)
            if 'reused_from' in check:
                reused.add(path)
        cases.append(dict(case=directory.name, checks=checks, supplements={stage: [] for stage in checks},
            save=archived(directory/'candidate-save-separate'), reopen=archived(directory/'candidate-reopen-compile-separate')))
    if len(cases) != 30:
        raise ValueError('Native original-project coverage incomplete')
    for version in (1, 2):
        directory = P/f'q-label-contact-remaining-checks-20261001-v{version}'
        before, after = read(directory/'before-native.json')['selections'], read(directory/'comparison.json')
        if before != [row['selection'] for row in after]:
            raise ValueError('Supplemental target selection incomplete')
        for row in after:
            selection = row['selection']
            case, = (c for c in cases if c['case'] == selection['case'])
            path = directory/(selection['case']+'-'+selection['stage']+'-'+str(selection['target_index']))
            case['supplements'][selection['stage']].append(archived(path))
    proof.mkdir()
    manifest = dict(candidate_root=archived(candidate), cases=cases,
        handling='Whole public invocations and independently selected target checks retain separate outcomes. Own target IDs, current reads and completed backend progress establish coverage.')
    save(proof/'replay-manifest.json', manifest)
    sys.path[:0] = [str(P), str(ROOT/'src'), str(ROOT/'research')]
    from source_q_machine_reader import load_read_tables, read_machine
    from source_q_read_context import build_read_context
    from source_q_contact_patch import patch_bit_contact
    from validate_gxw_q_label_patch import replay_local
    def profile(parameter):
        return load_read_tables(P/'q-machine-static-read-tables-20261001-v3.json', build_read_context(bytes.fromhex(parameter)).devices)
    def decoded(machine, selected):
        rows = read_machine(machine, selected)
        if any(r['handling'] != 'decoded' for r in rows) or b''.join(bytes.fromhex(r['raw_hex']) for r in rows) != machine:
            raise ValueError('Whole machine read incomplete')
        return b''.join(bytes.fromhex(r['pcode_hex']) for r in rows)
    converted = {r['id']: r for r in (json.loads(line) for line in (candidate/'output.jsonl').read_text(encoding='utf-8-sig').splitlines())}
    controls = read(candidate/'controls-before-native.json')
    minimal, summaries = [], []
    for case in cases:
        proposal = read(candidate/case['case']/'proposal-before-native.json')
        index, mutation = proposal['machine_resource_index'], proposal['machine_mutation']
        selected = profile(proposal['parameter_hex'])
        records = {stage: read_machine((candidate/case['case']/f'{stage}-machine-{index}.bin').read_bytes(), selected)
                   for stage in ('original', 'expected')}
        record_index, at = mutation['record_index'], mutation['machine_offset']
        outputs = {}
        for stage in ('original', 'expected'):
            control, = (r for r in controls if r['case'] == case['case'] and r['stage'] == stage)
            outputs[stage] = base64.b64decode(converted[control['id']]['output_base64'])[at:at+4]
        minimal.append(dict(id=len(minimal), parameter_hex=proposal['parameter_hex'], device_code=proposal['device_code'],
            before_number=proposal['before_number'], after_number=proposal['after_number'], allocated_count=proposal['capacity'],
            original_machine_hex=outputs['original'].hex(), native_candidate_machine_hex=outputs['expected'].hex(),
            original_pcode_hex=records['original'][record_index]['pcode_hex'], expected_pcode_hex=records['expected'][record_index]['pcode_hex'],
            scope='One numeric contact from a whole-body native conversion; source label names and full programs remain local.'))
        row = read(runs/case['case']/'comparison.json')
        summaries.append(dict(case=case['case'], device_code=proposal['device_code'], before_number=proposal['before_number'],
            after_number=proposal['after_number'], changed_input_bytes=len(proposal['physical_changed_offsets']),
            resources=len(proposal['resources']), untouched_inner_streams=proposal['unchanged_inner_streams'],
            untouched_outer_streams=proposal['unchanged_outer_streams'], compile_save_reopen='passed',
            public_outcomes={stage: 'incomplete' if not check['result']['public_check_complete'] else
                'rejected' if check['result']['public_check']['rejected'] else 'passed' for stage, check in row['checks'].items()},
            supplemental_checks={stage:len(paths) for stage, paths in case['supplements'].items()}))
    save(proof/'minimal-contact-patches.json', minimal)
    save(proof/'project-summary.json', summaries)
    public_entries = [(proof/'minimal-contact-patches.json', 'controls/minimal-contact-patches.json'),
        (proof/'project-summary.json', 'controls/project-summary.json'),
        (P/'q-machine-static-read-tables-20261001-v3.json', 'profiles/static-read-tables.json'),
        (ROOT/'research/validate_gxw_q_label_patch.py', 'validate.py'), (Path(__file__), 'tools/'+Path(__file__).name),
        (ROOT/'LICENSE', 'LICENSE')]
    for name in ('source_q_machine_reader.py', 'source_q_read_context.py', 'source_q_contact_patch.py'):
        public_entries.append((P/name, 'models/'+name))
    for name in ('QMachineOracle.cs', 'probe_q_label_contact_candidates.py', 'probe_q_label_contact_native.py',
                 'probe_q_remaining_check_targets.py', 'probe_q_project_patch_check_save.py'):
        public_entries.append((P/name, 'tools/'+name))
    private = list(public_entries)
    private.append((proof/'replay-manifest.json', 'replay-manifest.json'))
    for name in ('container.py', 'models.py', 'project_resolver.py'):
        private.append((ROOT/'src/gxw'/name, 'core/gxw/'+name))
    directories = [candidate, runs, P/'q-label-contact-remaining-checks-20261001-v1',
                   P/'q-label-contact-remaining-checks-20261001-v2', *sorted(reused)]
    for directory in directories:
        for path in directory.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                private.append((path, archived(path)))
    for path in P.glob('source_q_*.py'):
        private.append((path, 'source-models/'+path.name))
    for name in ('probe_fx1s_lossless_project_patch.py', 'probe_fx_operand_rules.py'):
        private.append((P/name, 'retained-tools/'+name))
    by_name = dict((name, source) for source, name in private)
    class DiskEvidence:
        def read(self, name):
            return by_name[name].read_bytes()
    with tempfile.TemporaryDirectory(prefix='q-label-freeze-check-') as directory:
        counts = replay_local(DiskEvidence(), manifest, Path(directory), profile, decoded, patch_bit_contact)
    if (counts['projects_modified'] != 30 or counts['saved_resource_copies_exact'] != 116 or
        counts['own_backend_targets_completed'] != 116 or counts['backend_diagnostics_unchanged_projects'] != 30):
        raise ValueError('Own-target/patch persistence coverage differs')
    summary = dict(schema=1, date='2026-10-01', status='native-validated/research-prototype',
        versions=dict(plc='Q03UDV', provider='ECCodeGenerator2.dll', file_version='15.41', public_cpu=209,
                      internal_cpu_word=113, native_versions=[25], compiler_adapter_version='1.635.0.1'),
        model='Source-only declaration binding predicts one label-reference contact change. In-place CFB patching preserves every other input payload; independent project-bound machine reading verifies the generated change.',
        counts=counts, device_codes=require['device_codes'], physical_byte_changes=require['physical_byte_changes'],
        retained_failures=['One original project has two untyped local declaration rows; direct source projection remains unsupported and its raw project is retained.',
            'An initial batch selected an absent case-03 and stopped after case-00; the invocation failure is retained.',
            'Owned helper temporary-directory collisions are retained with successful fresh retries.',
            'Twenty-six original/candidate public full-check pairs remain incomplete after original backend diagnostics and 0x2d010025 projection failure.',
            'A PCode dependency read does not prove completion of its own resource check. Separate selected-target invocations cover missing completed targets.',
            'Supplemental v2 read attempt-1 for candidate case-22 rather than the final retry, causing two redundant controls. Both are retained and agree with the completed original invocation.'],
        limits=['Pinned Q03UDV/CPU209/version25 only, with measured project parameter banks.',
            'One same-length existing label reference in a plain X/Y bit contact per original project; no claim about larger edits or declaration rewrites.',
            'Compile/save/reopen succeeds for all thirty edits. Public full-check passes for three projects, completes with rejection for one and remains incomplete for twenty-six.',
            'Selected-target checks establish per-resource correspondence across separate invocations; they do not complete or accept the failed full invocation.',
            'Cold replay rechecks raw source edits, independent machine reading, current Native buffers, diagnostics and saved resources. It does not recompile original source declarations.',
            'No PLC execution or product parser/writer integration is claimed.'],
        archive_scope=dict(public='Thirty numeric contact witnesses, bounded context/reader/patch models, tools, cold verifier and counts.',
                           local='Thirty original/changed GXW projects, full source/PCode/machine predictions, native check/save/reopen helpers, reused controls, raw failures and target supplements.'),
        verification='Direct archived-byte comparison and isolated replay; no native rerun or new digest verification.')
    save(proof/'summary.json', summary)
    public_entries.append((proof/'summary.json', 'summary.json'))
    private.append((proof/'summary.json', 'summary.json'))
    local_info = archive(local, private)
    public_info = archive(public, public_entries)
    cold = proof/'cold-replay'
    cold.mkdir()
    with zipfile.ZipFile(public) as evidence:
        (cold/'validate.py').write_bytes(evidence.read('validate.py'))
    process = subprocess.run([sys.executable, str(cold/'validate.py'), '--public', str(public), '--local', str(local)],
                             cwd=cold, capture_output=True, timeout=60)
    (proof/'cold-replay.stdout.bin').write_bytes(process.stdout)
    (proof/'cold-replay.stderr.bin').write_bytes(process.stderr)
    if process.returncode:
        raise RuntimeError('Cold replay failed; original evidence remains retained')
    replay = json.loads(process.stdout.decode('utf-8'))
    if replay != dict(minimal_contact_patches_exact=30, **counts):
        raise ValueError('Isolated replay counts differ')
    summary.update(public_archive=dict(path=public.relative_to(ROOT).as_posix(), **public_info),
                   local_archive=dict(path=local.relative_to(ROOT).as_posix(), **local_info), cold_replay_counts=replay)
    save(result, summary)
    print(json.dumps(dict(result=str(result), public=public_info, local=local_info, cold_replay_counts=replay)), flush=True)


if __name__ == '__main__':
    main()
