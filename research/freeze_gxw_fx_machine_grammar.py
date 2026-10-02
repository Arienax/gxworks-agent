"""Freeze raw-preserved FX cell lowering, mode-9 emission and width rewriting.

Synthetic raw witnesses are portable; the full project and complete original
traces remain local. Archive entries are compared directly with source bytes.
"""
from pathlib import Path
import collections
import json
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'research/experiments/sfc-graph-20260926/public-corpus-discovery'
SYNTHETIC = ('fx-instruction-cell-holdouts-20261001-v1', 'fx-machine-prospective-holdouts-20261001-v1')
DATASETS = ('fx-machine-step-emission-full-corpus-20261001-v1', 'fx-source-instruction-cells-full-corpus-20261001-v1',
            *SYNTHETIC, 'fx-source-machine-crosscheck-20261001-v1', 'fx-source-machine-crosscheck-20261001-v2',
            'fx-instruction-cell-phase-crosscheck-20261001-v1', 'fx-source-width-rewrite-20261001-v1')
MODEL_FILES = ('source_fx_operand_rules.py', 'source_fx_instruction_cells.py', 'source_fx_machine.py',
               'fx-instruction-cell-tables-20261001-v1.json', 'fx-machine-tables-20261001-v1.json')
TOOL_FILES = ('TraceFXInstructionCells.js', 'TraceFXMachineSteps.js', 'FXOperandCheckOracle.cs',
              'extract_fx_instruction_cell_tables.py', 'extract_fx_machine_tables.py',
              'probe_fx_instruction_cell_holdouts.py', 'probe_fx_machine_holdouts.py',
              'probe_fx_machine_steps.py', 'probe_fx_operand_rules.py', 'check_fx_source_instruction_cells.py',
              'check_fx_source_machine.py', 'check_fx_cell_phase_predictions.py', 'check_fx_width_rewrite.py')


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')


def archive(path, entries):
    names = [name for _, name in entries]
    if len(names) != len(set(names)) or any(not source.is_file() for source, _ in entries):
        raise ValueError('Duplicate or non-file archive input')
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for source, name in sorted(entries, key=lambda item:item[1]):
            z.write(source, name)
    with zipfile.ZipFile(path) as z:
        if len(z.infolist()) != len(entries):
            raise ValueError('Archive cardinality differs')
        for source, name in entries:
            if z.read(name) != source.read_bytes():
                raise ValueError('Archive entry differs from retained source bytes')
    return dict(entries=len(entries), bytes=path.stat().st_size, entries_byte_exact=True)


VALIDATOR = '''"""Replay frozen original native observations with no native binary or hooks."""
from pathlib import Path
import collections
import json
from source_fx_machine import FXMachine
from source_fx_operand_rules import Unsupported

p = Path(__file__).resolve().parent
model = FXMachine()
counts = collections.Counter()
keys, kinds = set(), set()
for witness in json.loads((p / 'native-witnesses.json').read_text(encoding='utf-8')):
    raw = bytes.fromhex(witness['raw'])
    try:
        prediction = model.record(raw)
    except Unsupported as exc:
        if witness.get('unsupported') != str(exc) or witness['native_emissions']:
            raise
        counts['explicit-unsupported'] += 1
        continue
    if witness.get('unsupported'):
        raise ValueError('Unsupported scope silently widened')
    counts['source-projections'] += 1
    if bytes.fromhex(prediction['raw']) != raw:
        raise ValueError('Original raw record changed')
    if len(witness['native_emissions']) != 1:
        counts['pre-emission-refusals'] += 1
        continue
    native = witness['native_emissions'][0]
    emission = prediction['emission']
    if prediction['native_cells'] != native['before'] or prediction['auxiliary'] != native['auxiliary']:
        raise ValueError('Native input representation differs')
    if emission['words'] != native['words'] or emission['cells_after'] != native['after']:
        raise ValueError('Native count or representation after emission differs')
    if emission['machine_output'] is None:
        counts['undefined-output-words'] += 1
    elif emission['machine_output'] != native['output']:
        raise ValueError('Native machine words differ')
    else:
        counts['machine-bytes-exact'] += 1
    counts['native-emission-exact'] += 1
    keys.add((raw[1], raw[3] if raw[0] >= 5 else 0))
    kinds.add(bytes.fromhex(native['before'])[2])
    public = witness['public']
    if public['code'] == 0 and public['status'] in (0, 1):
        rewrite = model.width_rewrite(raw)
        if rewrite['candidate'] != public['output'] or not rewrite['operand_bytes_preserved']:
            raise ValueError('Public record rewrite or raw operand preservation differs')
        counts['header-rewrite-exact'] += 1
    else:
        counts['native-emitter-refusals'] += 1
result = dict(counts=dict(counts), reached_instruction_keys=len(keys), observed_kinds=sorted(kinds))
expected = json.loads((p / 'expected-replay.json').read_text(encoding='utf-8'))
if result != expected:
    raise ValueError('Frozen witness coverage differs')
print(json.dumps(result, ensure_ascii=True))
'''


def main():
    output = ROOT / 'research/results/gxw-fx-machine-grammar-20261001.json'
    public = ROOT / 'research/evidence/gxw-fx-machine-grammar-20261001.zip'
    local = P / 'gxw-fx-machine-grammar-20261001-local.zip'
    proof = P / 'fx-machine-grammar-frozen-20261001'
    if any(path.exists() for path in (output, public, local, proof)):
        raise FileExistsError('Frozen evidence must not be replaced')
    sys.path.insert(0, str(P))
    from source_fx_machine import FXMachine
    from source_fx_operand_rules import Unsupported
    summaries = {name:read(P / name / 'comparison.json') for name in DATASETS}
    for name in SYNTHETIC:
        if not summaries[name]['baseline_traced_exact'] or summaries[name]['hook_errors'] or summaries[name]['differences']:
            raise ValueError('Unresolved prospective native comparison')
    full = summaries['fx-source-instruction-cells-full-corpus-20261001-v1']
    if full['native_instruction_cells_exact'] != 4196 or full['gaps'] or full['differences']:
        raise ValueError('Full native state witness differs')
    machine = summaries['fx-source-machine-crosscheck-20261001-v2']
    if machine['differences'] or machine['generation_gaps'] or machine['comparison']['fx-machine-step-emission-full-corpus-20261001-v1']['machine_bytes'] != 4196:
        raise ValueError('Full source-only machine comparison differs')
    width = summaries['fx-source-width-rewrite-20261001-v1']
    if width['differences'] or sum(v['exact'] for v in width['comparisons'].values()) != 7286:
        raise ValueError('Native header-only rewrite comparison differs')
    phases = summaries['fx-instruction-cell-phase-crosscheck-20261001-v1']
    if phases['differences'] or phases['raw_header_changes'] != phases['width_byte_only']:
        raise ValueError('Token boundary or raw header transition differs')
    witnesses, count, keys, kinds = [], collections.Counter(), set(), set()
    model = FXMachine()
    for name in SYNTHETIC:
        directory = P / name
        requests = read(directory / 'before-native.json')['requests']
        answers = {r['id']:r for r in map(json.loads, (directory / 'baseline.jsonl').read_text(encoding='utf-8-sig').splitlines())}
        events = collections.defaultdict(list)
        for line in (directory / 'events.jsonl').read_text(encoding='utf-8').splitlines():
            packet = json.loads(line)
            if packet['type'] != 'send':
                raise ValueError('Original native instrumentation error retained')
            e = packet['payload']
            if e['event'] == 'machine-emission':
                events[e['request']].append(e)
        for request in requests:
            answer = answers[request['id']]
            if not answer['guards_intact']:
                raise ValueError('Owned public helper guard failure')
            import base64
            witness = dict(id=len(witnesses), dataset=name, request=request['id'], raw=request['raw'],
                category=request.get('category', request.get('profile')), description=request.get('description', request.get('name')),
                native_emissions=events[request['id']], public=dict(code=answer['code'], status=answer['status'],
                output=base64.b64decode(answer['output_base64']).hex(), guards_intact=answer['guards_intact']))
            witnesses.append(witness)
            try:
                predicted = model.record(bytes.fromhex(request['raw']))
            except Unsupported as exc:
                if witness['native_emissions']:
                    raise ValueError('Unhandled original native emission')
                witness['unsupported'] = str(exc)
                count['explicit-unsupported'] += 1
                continue
            count['source-projections'] += 1
            if len(witness['native_emissions']) != 1:
                count['pre-emission-refusals'] += 1
                continue
            native = witness['native_emissions'][0]
            emission = predicted['emission']
            if predicted['native_cells'] != native['before'] or predicted['auxiliary'] != native['auxiliary'] or emission['words'] != native['words'] or emission['cells_after'] != native['after']:
                raise ValueError('Frozen source state/count prediction differs')
            if emission['machine_output'] is None:
                count['undefined-output-words'] += 1
            elif emission['machine_output'] != native['output']:
                raise ValueError('Frozen source machine bytes differ')
            else:
                count['machine-bytes-exact'] += 1
            count['native-emission-exact'] += 1
            raw = bytes.fromhex(request['raw'])
            keys.add((raw[1], raw[3] if raw[0] >= 5 else 0))
            kinds.add(bytes.fromhex(native['before'])[2])
            if answer['code'] == 0 and answer['status'] in (0, 1):
                if model.width_rewrite(raw)['candidate'] != witness['public']['output']:
                    raise ValueError('Frozen header rewrite differs')
                count['header-rewrite-exact'] += 1
            else:
                count['native-emitter-refusals'] += 1
    proof.mkdir()
    for name in MODEL_FILES:
        (proof / name).write_bytes((P / name).read_bytes())
    save(proof / 'native-witnesses.json', witnesses)
    expected = dict(counts=dict(count), reached_instruction_keys=len(keys), observed_kinds=sorted(kinds))
    save(proof / 'expected-replay.json', expected)
    save(proof / 'experiment-summaries.json', summaries)
    save(proof / 'rejected-short-header-canonicalization.json', read(P / 'fx-source-width-rewrite-20261001-v1/rejected-short-header-canonicalization.json'))
    (proof / 'validate_witnesses.py').write_text(VALIDATOR, encoding='utf-8')
    replay = subprocess.run([sys.executable, str(proof / 'validate_witnesses.py')], capture_output=True, timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    (proof / 'replay-stdout.txt').write_bytes(replay.stdout)
    (proof / 'replay-stderr.txt').write_bytes(replay.stderr)
    if replay.returncode:
        raise RuntimeError('Frozen native witnesses do not replay')
    manifest = dict(schema_version=1, date='2026-10-01',
        evidence_level='verified original native boundaries and machine-byte observations within recorded scope',
        handling='raw-preserved PCode tokens; decoded native cell projection and bounded mode-9 emission; unsupported and undefined cases retained',
        environment=dict(os='Windows', converter='ECCodeGeneratorFX2.dll', file_version='15.31', adapter_cpu=521,
                         cpu_class=32, open_mode=0, versions=[], emission_mode=9),
        static_tables=dict(opcode_mapping_rows=451, device_mapping_rows=112, state_bytes=88, cell_bytes=8, cells=11,
                           auxiliary_bytes=68, dispatch_rows=36, operation_rows=77, application_rows=310),
        full_project=dict(independent_original_projects=1, source='https://github.com/Serhioromano/gxw2-libraries',
                          prior_acceptance='research/results/gxw-fx-accepted-semantics-20261001.json',
                          raw_records=4196, operand_tokens=6672, native_cells_exact=4196, native_machine_bytes_exact=4196,
                          header_rewrites_byte_exact=4196),
        fresh_raw_cases=dict(requests=len(witnesses), earlier_cell_probe=dict(requests=1727, observed_emitters=1483,
            cells_exact=1483, model_saved_before_native_calls=True), prospective_machine_probe=dict(requests=2071,
            model_saved_before_native_calls=True, observed_emitters=1637, machine_bytes_exact=1637,
            explicit_unsupported_unmapped_headers=359, unpredicted_public_accepts=0), frozen_replay=expected),
        earlier_model_failure=dict(machine_encoding_attempt=1, differences=22,
            repairs=['Corrected S versus T compact encoding branches.', 'Corrected M8000..M8255 and M8256..M8511 boundaries.',
                     'Restored the C000 flag for one-word OUT.'],
            original_differences_local=DATASETS[4] + '/differences.json'),
        header_rewrite=dict(public_accepted_records=7286, exact_outputs=7286, all_operand_bytes_preserved=True,
            earlier_canonical_short_header_failures=122, native_short_header_preserves_uninterpreted_last_byte=True,
            evidence_kind='Static native routine and post-hoc comparison; earlier public checks are not predicted'),
        discoveries=[
            'Native 88-byte instruction state consists of eleven eight-byte cells; double-word operands consume pairs and retain Z/V identity.',
            'Mode-9 emission uses a 36-row dispatch table, 77 operation rows and 310 two-byte application entries.',
            'Complete raw records predict all 4196 full-project native machine bytes and word counts without native calls.',
            'DTBL function 152 in double-word mode inserts four K0 cells before emission; input operand-cell count alone does not determine its width.',
            'Original D0, D65536 and D16777216 raw tokens can yield identical native cells and machine bytes. Native conversion is not lossless or a numeric-range validator.',
            'A public-accepted comparative-header case reaches uninitialized operand cells. Undefined emitted words remain explicit rather than normalized to zero.',
            'A native one-word short header retains its raw final byte. Only the observed width/header rewrite is reconstructed; operand bytes remain exact.',
        ],
        limitations=[
            'Converter 15.31, CPU 521/class32 and mode9 only; no other FX or Q formats inferred.',
            'Only one independent original full-project source. Fresh raw cases widen grammar coverage, not project diversity.',
            'No strings, opaque reference operands, M./U. headers or other unmeasured operand categories are decoded by this cell model.',
            'A positive emitter width or public raw check does not establish address range, source validity, whole-project acceptance or execution equivalence.',
            'One synthetic emission has undefined output words; its raw state, count and public output are retained without claiming complete machine-byte decoding.',
            'The width-rewrite model is conditional on earlier checks reaching the inspected path; earlier refusal statuses remain native observations.',
            'No PLC execution or product writer permission expanded. Product code does not import the research models.',
        ],
        proof=dict(public_archive=public.relative_to(ROOT).as_posix(), local_archive=local.relative_to(ROOT).as_posix(),
                   portable_witnesses=len(witnesses), frozen_replay=json.loads(replay.stdout.decode('utf-8-sig'))))
    save(output, manifest)
    proof_files = [file for file in proof.iterdir() if file.is_file()]
    public_entries = [(output, 'manifest.json'), (Path(__file__), 'freeze_gxw_fx_machine_grammar.py')]
    public_entries += [(file, 'model/' + file.name) for file in proof_files]
    public_entries += [(P / name, 'tools/' + name) for name in TOOL_FILES]
    public_info = archive(public, public_entries)
    with tempfile.TemporaryDirectory(prefix='gxw-fx-machine-replay-') as temporary:
        with zipfile.ZipFile(public) as z:
            z.extractall(temporary)
        check = subprocess.run([sys.executable, str(Path(temporary) / 'model/validate_witnesses.py')], capture_output=True,
                               timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        if check.returncode or json.loads(check.stdout.decode('utf-8-sig')) != expected:
            raise RuntimeError('Portable archive does not reproduce frozen witness coverage')
    local_files = [output, Path(__file__), *proof_files, *[P / name for name in TOOL_FILES]]
    for name in DATASETS:
        local_files.extend(file for file in (P / name).rglob('*') if file.is_file() and '__pycache__' not in file.parts)
    for file in P.glob('fx-machine-emitter-*-20261001.txt'):
        local_files.append(file)
    local_files.extend(P / name for name in ('fx-instruction-mapping-3e241-20261001.txt', 'fx-instruction-state-3e339-20261001.txt',
        'fx-operand-state-3dc7a-20261001.txt', 'fx-machine-width-3e3dd-20261001.txt', 'fx-width-3e534-20261001.txt'))
    local_entries = [(file, file.relative_to(ROOT).as_posix()) for file in dict.fromkeys(local_files)]
    local_info = archive(local, local_entries)
    save(P / 'fx-machine-grammar-freeze-status-20261001.json', dict(public=public_info, local=local_info,
                                                                portable_archive_replay=expected))
    print(json.dumps(dict(manifest=str(output), public=public_info, local=local_info, replay=expected), ensure_ascii=True))


if __name__ == '__main__':
    main()
