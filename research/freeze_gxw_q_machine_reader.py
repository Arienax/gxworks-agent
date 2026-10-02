"""Freeze the independent Q reader, native controls, and configuration failures.

Portable synthetic controls and numeric evidence are public. Original projects,
PCode bodies, complete traces, refused conversions and early failures stay local.
Archive entries are checked by direct byte comparison, without new digests.
"""
from pathlib import Path
import base64
import json
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'research/experiments/sfc-graph-20260926/public-corpus-discovery'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2)+'\n', encoding='utf-8')


def archive(path, entries):
    if len({name for _, name in entries})!=len(entries):
        raise ValueError('Duplicate archive entry')
    if any(Path(name).is_absolute() or '..' in Path(name).parts for _, name in entries):
        raise ValueError('Archive entry escapes its root')
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as output:
        for source, name in sorted(entries, key=lambda row:row[1]):
            output.write(source, name)
    with zipfile.ZipFile(path) as output:
        for source, name in entries:
            if output.read(name)!=source.read_bytes():
                raise ValueError('Archived bytes differ: '+name)
    return dict(entries=len(entries), bytes=path.stat().st_size, entry_bytes_exact=True)


def main():
    result = ROOT / 'research/results/gxw-q-machine-reader-20261001.json'
    public = ROOT / 'research/evidence/gxw-q-machine-reader-20261001.zip'
    local = P / 'gxw-q-machine-reader-20261001-local.zip'
    proof = P / 'q-machine-reader-frozen-20261001'
    if any(path.exists() for path in (result, public, local, proof)):
        raise FileExistsError('Frozen evidence must not be replaced')
    corpus = P / 'q-machine-configured-corpus-20261001-v1'
    holdouts = P / 'q-machine-reader-holdouts-20261001-v1'
    configured_summary = read(corpus / 'summary.json')
    held_summary = read(holdouts / 'summary.json')
    if (configured_summary['counts']['full_bodies_independent_native_raw_exact']!=55 or
        configured_summary['counts']['full_bodies_independent_source_raw_exact']!=55 or
        not configured_summary['native_inputs_unchanged'] or held_summary['independent_mismatches']):
        raise ValueError('Accepted comparison differs; keep original failure')
    proof.mkdir()
    prefix = 'experiments/'+corpus.name
    checkpoint = dict(schema=1,
        configured_experiment_prefix=prefix,
        configured_before_entry=prefix+'/before-reverse.json',
        configured_native_entry=prefix+'/reverse/output.jsonl',
        expected_public_counts=dict(synthetic_controls=1556, synthetic_raw_preserved=1556,
            synthetic_decoded_native_exact=1550, synthetic_opaque_preserved=6,
            synthetic_native_forward_accepted=1419, synthetic_native_forward_refused=133,
            synthetic_machine_reemission_exact=776, synthetic_machine_reemission_changed=643,
            configuration_aliases_preserved=4, wrong_context_machine_reemission_exact=3),
        expected_local_counts=dict(instructions_inputs=468, instructions_raw_preserved=468,
            instructions_decoded_native_source_exact=467, full_bodies_inputs=55,
            full_bodies_raw_preserved=55, full_bodies_decoded_native_source_exact=55,
            weighted_original_instructions=6062, weighted_decoded_native_source_exact=6060))
    save(proof / 'checkpoint.json', checkpoint)
    before = read(corpus / 'before-reverse.json')
    def native_rows(path):
        return {r['id']:r for r in (json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines())}
    configured = native_rows(corpus / 'reverse/output.jsonl')
    wrong = native_rows(corpus / 'wrong-context-reverse/output.jsonl')
    again = native_rows(corpus / 'wrong-context-reemission/output.jsonl')
    aliases = []
    examples = {343:'OUT ST1 K20', 344:'AND ST1', 354:'BKRST ST1 K5', 378:'RST ST0'}
    for row in before['wrong_context']:
        aliases.append(dict(id=row['id'], instruction=examples[row['id']], machine_hex=row['machine_hex'],
            expected_pcode_hex=row['original_pcode_hex'], configured_native=configured[row['id']],
            default_native=wrong[row['id']], default_reemission=again[row['id']]))
    save(proof / 'configuration-aliases.json', aliases)
    experiments = sorted(path for path in P.glob('q-machine-*') if path.is_dir() and path!=proof)
    private = [(proof / 'checkpoint.json', 'checkpoint.json')]
    for directory in experiments:
        for path in directory.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                private.append((path, 'experiments/'+path.relative_to(P).as_posix()))
    original = read(P / 'q-machine-original-corpus-20261001-v1/before-forward.json')
    source_dirs = sorted({Path(row['source']).parent for row in original['programs']})
    for directory in source_dirs:
        for path in directory.iterdir():
            if path.is_file() and (path.name in ('input.gxw', 'native-saved.gxw', 'plan.json', 'outcome.json', 'native-events.jsonl') or
                                    path.name.startswith('pcode-') and path.suffix=='.bin'):
                private.append((path, 'original-corpus/'+directory.name+'/'+path.name))
    for path in P.glob('q-machine-*-20261001.txt'):
        private.append((path, 'static/'+path.name))
    for path in P.glob('q-machine-static-read-tables-20261001-v*.json'):
        private.append((path, 'static/'+path.name))
    public_entries = [
        (P / 'source_q_machine_reader.py', 'models/source_q_machine_reader.py'),
        (corpus / 'static-read-tables.json', 'profiles/static-read-tables.json'),
        (corpus / 'default-device-context.json', 'profiles/default-device-context.json'),
        (corpus / 'observed-device-context.json', 'profiles/observed-device-context.json'),
        (corpus / 'project-parameters.bin', 'profiles/observed-project-parameters.bin'),
        (holdouts / 'before-native.json', 'controls/holdouts-before-native.json'),
        (holdouts / 'comparisons.json', 'controls/holdouts-comparisons.json'),
        (proof / 'configuration-aliases.json', 'controls/configuration-aliases.json'),
        (proof / 'checkpoint.json', 'checkpoint.json'),
        (Path(__file__), 'tools/'+Path(__file__).name),
        (ROOT / 'research/validate_gxw_q_machine_reader.py', 'validate.py'),
        (ROOT / 'LICENSE', 'LICENSE')]
    for name in ('QMachineOracle.cs', 'TraceQMachineDeviceTable.js', 'TraceQMachineProjectContext.js',
                 'disassemble_q_machine.py', 'extract_q_machine_read_tables.py',
                 'probe_q_machine_reader_holdouts.py', 'probe_q_machine_configured_corpus.py'):
        public_entries.append((P / name, 'tools/'+name))
    for source, name in public_entries:
        if name.startswith(('tools/', 'models/')) or name=='validate.py':
            private.append((source, name))
    local_info = archive(local, private)
    summary = dict(schema=1, date='2026-10-01', status='native-validated/research-prototype',
        versions=dict(provider='ECCodeGenerator2.dll', file_version='15.41', adapter_cpu=209,
            internal_cpu_word=113, versions=[25], observed_plc='Q03UDV'),
        model='Independent reverse boundary scanner, reverse opcode tables and explicit initialized device ranges; raw machine words remain preserved',
        counts=dict(original_source_bodies=60, distinct_nonempty_original_bodies=55,
            original_instruction_occurrences=6062, whole_body_instruction_occurrences_exact=6062,
            isolated_unique_instructions=468, isolated_independent_native_source_exact=467,
            isolated_weighted_native_source_exact=6060, configured_full_bodies_native_source_exact=55,
            default_full_bodies_native_source_exact=50, synthetic_controls=1556,
            synthetic_independent_native_exact=1550, synthetic_opaque_preserved=6,
            configured_alias_counterexamples=4, wrong_context_machine_reemission_exact=3),
        context=dict(default='Null public project context: 21 ranges, no ST allocation',
            observed='Unmodified 70-byte parameter input from an owned case-16 compiler call: 23 ranges; initialized config matches the actual compiler call',
            binding='The observed profile is a conversion control for all 55 bodies, not an assertion that their saved project caches share the same allocation'),
        retained_failures=['Early reverse kind-2 boundary test used the wrong bit field',
            'Initial Frida observer used a reserved property and failed before a clean replacement run',
            '36 ST fragments are refused in a null context with native code 0x4010004',
            'Isolated terminal ANB remains opaque because its lookahead is outside input; complete bodies decode it',
            'Six malformed, truncated or unmeasured machine controls preserve opaque bytes',
            'Native reverse accepts odd parity and the sentinel address without proving forward acceptance',
            'Four ST examples decode to T devices under the wrong context; three re-emit identical machine bytes',
            '643 synthetic re-emissions change machine encoding and 133 are refused'],
        limits=['Only the pinned Q compiler profile and inspected instruction branches',
            'Machine inputs for the original corpus are fresh forward-conversion outputs, not verified saved machine caches',
            'Project parameter to read-table construction still uses retained native initialization observations',
            'No whole-project modification, complete program check, PLC execution or product parser extension is claimed'],
        archive_scope=dict(public='Pure reader, reverse tables, initialized profiles, synthetic and minimal instruction controls, memory-only native helper and offline validator',
            local='Original Q projects/PCode bodies, complete listed experiments, refused output arenas, old failures and static inspection records'),
        local_archive=dict(path=local.relative_to(ROOT).as_posix(), **local_info),
        verification='Direct preserved-byte comparison and cold replay of the archived pure reader; no new digests')
    save(proof / 'summary.json', summary)
    public_entries.append((proof / 'summary.json', 'summary.json'))
    public_info = archive(public, public_entries)
    cold = proof / 'cold-replay'
    cold.mkdir()
    with zipfile.ZipFile(public) as output:
        (cold / 'validate.py').write_bytes(output.read('validate.py'))
    process = subprocess.run([sys.executable, str(cold / 'validate.py'), '--public', str(public), '--local', str(local)],
                             cwd=cold, capture_output=True, timeout=45)
    (proof / 'cold-replay.stdout.bin').write_bytes(process.stdout)
    (proof / 'cold-replay.stderr.bin').write_bytes(process.stderr)
    if process.returncode:
        raise RuntimeError('Cold replay failed; archives and raw outputs retained')
    replay = json.loads(process.stdout.decode('utf-8'))
    summary.update(public_archive=dict(path=public.relative_to(ROOT).as_posix(), **public_info), cold_replay_counts=replay)
    save(result, summary)
    print(json.dumps(dict(result=str(result), cold_replay_counts=replay, public=public_info, local=local_info), ensure_ascii=True), flush=True)


if __name__=='__main__':
    main()
