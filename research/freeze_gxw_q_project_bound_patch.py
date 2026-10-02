"""Freeze project-derived Q read context, local patch witnesses and cache failures.

Public evidence contains numeric parameter controls and five minimal contacts.
Original engineering projects and complete code remain in the local archive.
Archive contents are compared directly; cold replay executes no native helper.
"""
from pathlib import Path
import base64
import json
import struct
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
P = ROOT/'research/experiments/sfc-graph-20260926/public-corpus-discovery'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2)+'\n', encoding='utf-8')


def archive(path, entries):
    if len({name for _,name in entries}) != len(entries):
        raise ValueError('Duplicate archive entry')
    if any(Path(name).is_absolute() or '..' in Path(name).parts for _,name in entries):
        raise ValueError('Archive entry escapes root')
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as output:
        for source, name in sorted(entries, key=lambda r:r[1]):
            output.write(source, name)
    with zipfile.ZipFile(path) as output:
        for source, name in entries:
            if output.read(name) != source.read_bytes():
                raise ValueError('Archived bytes differ: '+name)
    return dict(entries=len(entries), bytes=path.stat().st_size, entry_bytes_exact=True)


def main():
    result = ROOT/'research/results/gxw-q-project-bound-patch-20261001.json'
    public = ROOT/'research/evidence/gxw-q-project-bound-patch-20261001.zip'
    local = P/'gxw-q-project-bound-patch-20261001-local.zip'
    proof = P/'q-project-bound-patch-frozen-20261001'
    if any(path.exists() for path in (result, public, local, proof)):
        raise FileExistsError('Frozen evidence must not be replaced')
    contexts = P/'q-read-context-model-20261001-v2'
    bound = P/'q-machine-project-bound-corpus-20261001-v1'
    patch = P/'q-lossless-project-patch-20261001-v1'
    runs = P/'q-lossless-project-patch-check-save-20261001-v3'
    require_context = read(contexts/'summary.json')
    require_bound = read(bound/'summary.json')
    if require_context['exact'] != 95 or require_context['mismatches'] or require_bound['independent_mismatches']:
        raise ValueError('Context/corpus comparison incomplete')
    memory = read(patch/'memory-comparison.json')
    if len(memory) != 20 or not all(r['native_complete'] and r['machine_exact'] and r['input_unchanged'] for r in memory):
        raise ValueError('Patch machine prediction incomplete')
    cases = ('case-16', 'case-18', 'case-23', 'case-30', 'case-31')
    summaries = []
    for case in cases:
        row = read(runs/case/'comparison.json')
        if (row['observed_backend_diagnostics_unchanged'] is not True or not row['all_backend_targets_observed'] or
            not row['saved_source_body_exact'] or not row['saved_source_prediction_exact'] or
            not all(r['save_pcode_exact'] and r['reopen_pcode_exact'] for r in row['resources'])):
            raise ValueError('Patch persistence/check correspondence incomplete')
        for check in row['checks'].values():
            if (not check['project_owned_compiler'] or check['observation_errors'] or not check['backend_rejected'] or
                check['public_check_complete'] or not all(r['compiler_pcode_exact'] and r['checked_current_pcode'] for r in check['resources'])):
                raise ValueError('Full-check failure was not explicitly preserved')
        proposal = read(patch/case/'proposal-before-native.json')
        summaries.append(dict(case=case, before=proposal['chosen']['before'], after=proposal['chosen']['after'],
            physical_changed_offsets=proposal['physical_changed_offsets'], unchanged_inner_streams=proposal['unchanged_inner_streams'],
            unchanged_outer_streams=proposal['unchanged_outer_streams'], native_resources=len(row['resources']),
            backend_errors_per_stage=len(row['checks']['original']['backend_diagnostics']),
            source_machine_prediction='exact', native_compile_save_reopen='passed',
            backend_check='rejected with unchanged original diagnostics', public_check='incomplete: 0x2d010025'))
    proof.mkdir()
    context_rows = read(contexts/'before-native.json')
    save(proof/'context-before-native.json', dict(controls=context_rows['controls'], unsupported=context_rows['unsupported']))
    sys.path[:0] = [str(P), str(ROOT/'src')]
    from source_q_allocation import groups
    converted = {r['id']:r for r in (json.loads(line) for line in (patch/'output.jsonl').read_text(encoding='utf-8-sig').splitlines())}
    conversions = read(patch/'before-native-machine.json')['controls']
    minimal = []
    for case in cases:
        proposal = read(patch/case/'proposal-before-native.json')
        resource, = (r for r in proposal['resources'] if r['machine_patch'] is not None)
        mutation = resource['machine_patch']
        index, at, record_index = resource['index'], mutation['machine_offset'], mutation['record_index']
        outputs = {}
        for stage in ('original', 'candidate'):
            control, = (r for r in conversions if r['case'] == case and r['resource_index'] == index and r['stage'] == stage)
            outputs[stage] = base64.b64decode(converted[control['id']]['output_base64'])[at:at+4]
        parameter = bytes.fromhex(proposal['parameter_hex'])
        capacity, = (struct.unpack_from('<H', parameter, offset+2)[0] for offset in range(6,len(parameter),4)
                     if struct.unpack_from('<H', parameter, offset)[0] == 0x1C8)
        original_pcode = b''.join(groups((patch/case/f'original-pcode-{index}.bin').read_bytes())[record_index])
        expected_pcode = b''.join(groups((patch/case/f'expected-pcode-{index}.bin').read_bytes())[record_index])
        minimal.append(dict(id=len(minimal), parameter_hex=proposal['parameter_hex'],
            before_number=mutation['before_number'], after_number=mutation['after_number'], allocated_count=capacity,
            original_machine_hex=outputs['original'].hex(), native_candidate_machine_hex=outputs['candidate'].hex(),
            original_pcode_hex=original_pcode.hex(), expected_pcode_hex=expected_pcode.hex(),
            observation_scope='One ordinary AND ST contact extracted from accepted whole-body memory controls; complete source remains local.'))
    save(proof/'minimal-contact-patches.json', minimal)
    save(proof/'project-patch-summary.json', summaries)
    public_entries = [(proof/'context-before-native.json','controls/context-before-native.json'),
        (contexts/'comparison.json','controls/context-observed.json'), (contexts/'output.jsonl','controls/context-native.jsonl'),
        (proof/'minimal-contact-patches.json','controls/minimal-contact-patches.json'),
        (proof/'project-patch-summary.json','controls/project-patch-summary.json'),
        (bound/'static-read-tables.json','profiles/static-read-tables.json'),
        (ROOT/'research/validate_gxw_q_project_bound_patch.py','validate.py'),
        (Path(__file__),'tools/'+Path(__file__).name), (ROOT/'LICENSE','LICENSE')]
    for name in ('source_q_machine_reader.py','source_q_read_context.py','source_q_contact_patch.py'):
        public_entries.append((P/name,'models/'+name))
    for name in ('QMachineOracle.cs','TraceQReadContext.js','probe_q_read_context_model.py',
                 'probe_q_machine_project_bound.py','probe_q_lossless_project_patch.py','probe_q_project_patch_check_save.py'):
        public_entries.append((P/name,'tools/'+name))
    private = [(source, name) for source, name in public_entries]
    for name in ('container.py','models.py','project_resolver.py'):
        private.append((ROOT/'src/gxw'/name,'core/gxw/'+name))
    directories = sorted(directory for pattern in ('q-read-context-model-20261001-v*', 'q-machine-project-bound-corpus-20261001-v*',
        'q-lossless-project-patch-20261001-v*', 'q-lossless-project-patch-check-save-20261001-v*') for directory in P.glob(pattern) if directory.is_dir())
    for directory in directories:
        for path in directory.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                private.append((path, 'experiments/'+path.relative_to(P).as_posix()))
    for case in read(bound/'before-reverse.json')['projects']:
        private.append((P/'noeul-native-batch'/case/'input.gxw','original-corpus/'+case+'/input.gxw'))
    for path in P.glob('source_q_*.py'):
        private.append((path,'source-models/'+path.name))
    for pattern in ('q-machine-*-20261001.txt', 'q-compiler-backend-*-20261001.txt'):
        for path in P.glob(pattern):
            private.append((path,'static/'+path.name))
    for name in ('inventory_q_saved_code_payloads.py','q-saved-code-inventory-20261001-v1.json',
                 'probe_fx1s_lossless_project_patch.py','probe_fx_operand_rules.py'):
        private.append((P/name,'retained-tools/'+name))
    private.append((ROOT/'research/probe_gxw_program_check_resources.py','retained-tools/probe_gxw_program_check_resources.py'))
    local_info = archive(local, private)
    summary = dict(schema=1,date='2026-10-01',status='native-validated/research-prototype',
        versions=dict(provider='ECCodeGenerator2.dll',file_version='15.41',public_cpu=209,internal_cpu_word=113,
                      native_versions=[25],plc='Q03UDV',compiler_adapter_version='1.635.0.1'),
        model='Construct reverse device ranges from the same project Param.wpa/2000 record; preserve raw source and machine bytes around one contact mutation.',
        counts=dict(original_projects_bound=31,parameter_profiles=2,prospective_context_tables_exact=95,unsupported_parameters_preserved=4,
            isolated_parameter_pcode_pairs=626,isolated_independent_native_source_exact=625,isolated_opaque_preserved=1,
            complete_nonempty_bodies_exact=55,original_instruction_occurrences=6062,isolated_weighted_exact=6060,
            original_projects_locally_modified=5,physical_changed_bytes_per_input=1,whole_machine_controls_exact=20,
            fresh_compile_resource_bodies_exact=20,saved_resource_code_copies_exact=20,reopened_resource_bodies_exact=10,
            backend_targets_completed=20,backend_rejected_project_stages=10,preserved_backend_errors=66,
            public_check_projection_failures=10),
        project_patches=summaries,
        retained_failures=['Early context probe had a wrong END constant and failed before native conversion.',
            'Four malformed/unmeasured parameter records remain unsupported with raw bytes retained.',
            'The first check observer did not support the non-IEC backend; static GetProgress inspection confirmed 68-byte records.',
            'Initial Native/logical resource-name mismatch produced a false correspondence flag; both records remain retained.',
            'A separate compiler produced changed code while workspace checks still read original ST1 at offset 296. Project-owned compiler controls bind fresh code before checking.',
            'Two empty projected report sets were initially marked unchanged; a retained correction marks that comparison not-checkable.',
            'All five original and changed projects retain the same full-mask duplicate-coil errors. Backend checks complete, then public diagnostic projection fails with 0x2d010025.'],
        limits=['Pinned Q03UDV/CPU209/version25 only; unmeasured parameter banks and framing are refused.',
            'Context equivalence is a decoder convention, not proof of legally usable project allocations.',
            'Only one same-width plain ST contact per original project; no claim about larger or different edits.',
            'Whole-project acceptance is not established. Separate save/reopen does not suppress or resolve full-check errors.',
            'Original machine inputs are fresh memory conversions, not saved machine-cache freshness evidence.',
            'No PLC execution or product parser/writer integration is claimed.'],
        archive_scope=dict(public='Pure context/reader/patch models, 95 numeric context controls, four raw refusals, five minimal instruction witnesses, tools and cold verifier.',
            local='31 original GXW projects, full PCode and machine bodies, five changed GXW inputs, complete native helper runs, saved projects, old failures and static inspection.'),
        local_archive=dict(path=local.relative_to(ROOT).as_posix(),**local_info),
        verification='Direct archive-byte comparison and isolated replay of archived models/readers; no native rerun or new digest verification.')
    save(proof/'summary.json',summary)
    public_entries.append((proof/'summary.json','summary.json'))
    public_info = archive(public,public_entries)
    cold = proof/'cold-replay'
    cold.mkdir()
    with zipfile.ZipFile(public) as output:
        (cold/'validate.py').write_bytes(output.read('validate.py'))
    process = subprocess.run([sys.executable,str(cold/'validate.py'),'--public',str(public),'--local',str(local)],
                             cwd=cold,capture_output=True,timeout=60)
    (proof/'cold-replay.stdout.bin').write_bytes(process.stdout)
    (proof/'cold-replay.stderr.bin').write_bytes(process.stderr)
    if process.returncode:
        raise RuntimeError('Cold replay failed; original archives/results retained')
    replay = json.loads(process.stdout.decode('utf-8'))
    summary.update(public_archive=dict(path=public.relative_to(ROOT).as_posix(),**public_info),cold_replay_counts=replay)
    save(result,summary)
    print(json.dumps(dict(result=str(result),public=public_info,local=local_info,cold_replay_counts=replay)),flush=True)


if __name__ == '__main__':
    main()
