"""Freeze a bounded FX raw-operand parser and native type predicates.

Native binaries and full project traces stay local. Archived entries are
compared directly with their source bytes. This creates no format hash oracle.
"""
from pathlib import Path
import collections
import json
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'research/experiments/sfc-graph-20260926/public-corpus-discovery'
sys.path.insert(0, str(P))
from source_fx_operand_rules import FXOperandRules, Unsupported
from source_fx_operand_parser import parse

DATASETS = [
    'fx-operand-rule-encode-trace-20261001-v1',
    'fx-operand-rule-catalog-holdouts-20261001-v1',
    'fx-operand-rule-full-corpus-20261001-v1',
    'fx-operand-parser-full-corpus-20261001-v1',
    'fx-operand-rule-raw-holdouts-20261001-v1',
    'fx-instruction-phase-boundaries-20261001-v1',
    'fx-operand-modifier-boundaries-20261001-v1',
]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')


def validate(model, e):
    if e['event'] == 'operand-capability':
        status, after = model.capability(bytes.fromhex(e['before']))
        return status == e['result'] and after.hex() == e['after'], None
    if e['event'] == 'operand-rule':
        status, provenance = model.rule(e['family'], e['variant'], e['index'], bytes.fromhex(e['descriptors']))
        return status == e['result'], provenance
    predicted = parse(bytes.fromhex(e['raw']), bytes.fromhex(e['initial_descriptor']))
    return (predicted['status'] == e['result'] and predicted['descriptor'].hex() == e['descriptor']
            and predicted['base_offset'] == e['base_offset']
            and (predicted['last_value'] is None or predicted['last_value'] == e['last_value'])), predicted


def selection_key(model, e, provenance):
    kind = e['event']
    if kind == 'operand-capability':
        return kind, e['before'][:14], e['result'], e['after']
    if kind == 'operand-rule':
        roles = tuple(r['role'] for r in provenance.get('alternatives', []))
        special = (e['family'], e['index'], e['descriptors']) if e['family'] in (0x21, 0x3c) else None
        return kind, roles, e['result'], e['descriptor'], special
    # Keep token widths, modifier codes/order, value-size boundaries and every
    # parser status, without publishing all original project addresses.
    d = bytes.fromhex(e['descriptor'])
    raw = bytes.fromhex(e['raw'])
    value = e['last_value']
    value_class = value if value in (0, 1, 65536, 0xffffffff) else value.bit_length()
    modifiers = tuple((r['code'], r['raw_value']) for r in provenance['modifiers'])
    return kind, d[:7], len(raw), e['base_offset'], e['result'], modifiers, value_class


VALIDATOR = '''"""Replay frozen native function-boundary witnesses without GX Works2."""
from pathlib import Path
import collections
import json
from source_fx_operand_rules import FXOperandRules, Unsupported
from source_fx_operand_parser import parse

P = Path(__file__).resolve().parent
model = FXOperandRules(json.loads((P / 'fx-operand-tables.json').read_text(encoding='utf-8')))
counts = collections.Counter()
roles = set()
for row in json.loads((P / 'native-boundary-witnesses.json').read_text(encoding='utf-8')):
    e = row['native']
    try:
        if e['event'] == 'operand-capability':
            status, after = model.capability(bytes.fromhex(e['before']))
            equal = status == e['result'] and after.hex() == e['after']
        elif e['event'] == 'operand-rule':
            status, provenance = model.rule(e['family'], e['variant'], e['index'], bytes.fromhex(e['descriptors']))
            equal = status == e['result']
            roles.update(r['role'] for r in provenance.get('alternatives', []))
        else:
            result = parse(bytes.fromhex(e['raw']), bytes.fromhex(e['initial_descriptor']))
            equal = (result['status'] == e['result'] and result['descriptor'].hex() == e['descriptor']
                and result['base_offset'] == e['base_offset']
                and (result['last_value'] is None or result['last_value'] == e['last_value']))
        if row.get('unsupported') or not equal:
            raise ValueError('Frozen boundary mismatch: ' + str(row['id']))
        counts[e['event']] += 1
    except Unsupported as exc:
        if row.get('unsupported') != str(exc):
            raise
        counts['explicit-unsupported'] += 1
if len(roles) != 82:
    raise ValueError('Frozen role coverage differs')
print(json.dumps(dict(matched=dict(counts), reached_roles=len(roles)), ensure_ascii=True))
'''


def archive(path, files, root):
    if len(set(files)) != len(files):
        raise ValueError('Duplicate archive input')
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for file in sorted(files):
            z.write(file, file.relative_to(root).as_posix())
    with zipfile.ZipFile(path) as z:
        if len(z.infolist()) != len(files):
            raise ValueError('Archived input cardinality differs')
        for file in files:
            if z.read(file.relative_to(root).as_posix()) != file.read_bytes():
                raise ValueError('Archived entry bytes differ')
    return dict(entries=len(files), bytes=path.stat().st_size, entries_byte_exact=True)


def main():
    output = ROOT / 'research/results/gxw-fx-operand-grammar-20261001.json'
    public = ROOT / 'research/evidence/gxw-fx-operand-grammar-20261001.zip'
    local = P / 'gxw-fx-operand-grammar-20261001-local.zip'
    proof = P / 'fx-operand-grammar-frozen-20261001'
    if any(path.exists() for path in (output, public, local, proof)):
        raise FileExistsError('Existing frozen evidence must not be replaced')
    tables = read(P / DATASETS[0] / 'static-tables-before-native.json')
    model = FXOperandRules(tables)
    keys, slots, roles = set(), set(), set()
    selected, selected_keys = [], set()
    all_counts, matched, unsupported = collections.Counter(), collections.Counter(), []
    summaries = {}
    for name in DATASETS:
        directory = P / name
        comparison = read(directory / 'comparison.json')
        exact = comparison.get('baseline_traced_exact', comparison.get('traced_baseline_exact'))
        if exact is not True:
            raise ValueError('Instrumentation changed public results')
        boundary = read(directory / 'model-boundary-comparison.json')
        if boundary['failures']:
            raise ValueError('Unresolved type-model failure')
        keys.update(map(tuple, boundary['instruction_keys']))
        roles.update(boundary['reached_roles'])
        summaries[name] = comparison
        for line in (directory / 'events.jsonl').read_text(encoding='utf-8').splitlines():
            packet = json.loads(line)
            if packet['type'] != 'send':
                raise ValueError('Unresolved instrumentation error')
            e = packet['payload']
            if e['event'] not in ('operand-capability', 'operand-rule', 'operand-parse'):
                continue
            all_counts[e['event']] += 1
            if e['event'] == 'operand-rule':
                slots.add((e['family'], e['variant'], e['index']))
            try:
                equal, provenance = validate(model, e)
                if not equal:
                    raise ValueError('Unresolved native predicate mismatch')
                matched[e['event']] += 1
                key = selection_key(model, e, provenance)
                gap = None
            except Unsupported as exc:
                gap = str(exc)
                key = ('explicit-unsupported', e['event'], e.get('before'), gap)
                unsupported.append(dict(dataset=name, event=e['event'], request=e['request'], reason=gap))
            if key not in selected_keys:
                selected_keys.add(key)
                selected.append(dict(id=len(selected), dataset=name, native=e, **({'unsupported':gap} if gap else {})))
    gap_counts = collections.Counter(r['event'] for r in unsupported)
    if len(roles) != 82 or any(matched[k] + gap_counts[k] != all_counts[k] for k in all_counts):
        raise ValueError('Frozen boundary completeness differs')
    catalog = read(P / 'fx-native-instruction-catalog-20261001-v1/before-native.json')
    ordinary = [r for r in catalog['rows'][:-1] if r['uninterpreted_fields'][0] and r['uninterpreted_fields'][1] < 0x70]
    expected_slots = {(r['uninterpreted_fields'][1], r['uninterpreted_fields'][2], index)
                      for r in ordinary for index in range(1, r['uninterpreted_fields'][0] + 1)}
    full = summaries['fx-operand-parser-full-corpus-20261001-v1']
    if full['parser_counts']['matched'] != 6672 or full['type_matched'] != {'operand-capability':6672, 'operand-rule':6672}:
        raise ValueError('Full-project predicate witness differs')
    phases = summaries['fx-instruction-phase-boundaries-20261001-v1']
    if phases['prior_refusal_phases'] != {'width-rewrite-refused':707, 'instruction-check-refused':5}:
        raise ValueError('Catalog refusal classification differs')
    proof.mkdir()
    save(proof / 'fx-operand-tables.json', tables)
    save(proof / 'native-boundary-witnesses.json', selected)
    save(proof / 'experiment-summaries.json', summaries)
    save(proof / 'instruction-descriptor-observations.json', dict(
        comparison=read(P / 'fx-native-instruction-catalog-20261001-v1/comparison.json'),
        descriptors=[{k:r[k] for k in ('index', 'name', 'tail_hex', 'uninterpreted_fields')} for r in catalog['rows']]))
    save(proof / 'descriptor-collisions.json', read(P / 'fx-operand-modifier-boundaries-20261001-v1/descriptor-collisions.json'))
    save(proof / 'phase-classifications.json', read(P / 'fx-instruction-phase-boundaries-20261001-v1/phase-classifications.json'))
    for name in ('source_fx_operand_rules.py', 'source_fx_operand_parser.py'):
        (proof / name).write_bytes((P / name).read_bytes())
    (proof / 'validate_witnesses.py').write_text(VALIDATOR, encoding='utf-8')
    import subprocess
    replay = subprocess.run([sys.executable, str(proof / 'validate_witnesses.py')], capture_output=True, timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    (proof / 'replay-stdout.txt').write_bytes(replay.stdout)
    (proof / 'replay-stderr.txt').write_bytes(replay.stderr)
    if replay.returncode:
        raise RuntimeError('Frozen predicate witnesses do not reproduce')
    manifest = dict(schema_version=1, date='2026-10-01', evidence_level='verified native function-boundary predicates within recorded scope',
        handling='lossless raw operand framing and decoded temporary type-check descriptors; whole-instruction gaps retained',
        environment=dict(os='Windows', fx_code_generator='15.31', adapter_cpu=521, cpu_class=32, mode=0, versions=[]),
        catalog=dict(descriptors=436, reachable=435, queried_public_edges=3962, holes=3527,
                     all_static_public_edges_equal=True, unread_descriptor_fields='retained as raw uninterpreted bytes'),
        static_type_tables=dict(map_rows=530, distinct_schemas=213, roles=82, capability_codes=112),
        dynamic_coverage=dict(instruction_keys=len(keys), catalog_regular_operand_keys=len(ordinary),
            catalog_regular_operand_keys_reached=sum((r['uninterpreted_fields'][1], r['uninterpreted_fields'][2]) in keys for r in ordinary),
            roles_reached=len(roles), catalog_operand_slots=len(expected_slots), catalog_operand_slots_reached=len(expected_slots & slots),
            unvisited_slots=[list(s) for s in sorted(expected_slots - slots)], boundary_calls=dict(all_counts),
            boundary_matches=dict(matched), explicit_unsupported=unsupported),
        full_project=dict(independent_original_projects=1, repository='https://github.com/Serhioromano/gxw2-libraries',
            prior_project_evidence='research/results/gxw-fx-accepted-semantics-20261001.json',
            raw_records=4196, raw_bytes=54203, raw_record_checks_accepted=4196, raw_records_byte_exact=4196,
            operands=6672, raw_parser_matches=6672, capability_matches=6672, role_matches=6672),
        synthetic=dict(text_requests=5900, raw_requests=5900, all_public_results_exact_with_and_without_hooks=True,
            type_pass_raw_refused=712, instruction_phase_refused=5, width_rewrite_refused=707,
            word_literal_retries_accepted=57, native_word_to_double_word_return=-4,
            modifier_requests=48, modifier_parser_matches=67),
        discoveries=[
            'The CPU 521 path uses 530 map entries, 213 schema offsets, 82 role masks and 112 device capability triples.',
            'All 403 ordinary catalog keys with operands reached the native role routine; 1203 of 1228 ordinary operand positions were observed.',
            'The native parser initializes eight bytes of a nine-byte slot. The caller high flag byte remains opaque until the capability routine overwrites the flags word.',
            'Raw Z/V modifier identity and some modifier ordering disappear in the temporary descriptor. The raw tokens must remain in a lossless model.',
            'A native -4 type status requests word-literal widening; the public text encoder retries these operands as double-word literals.',
            'Of 712 type-accepted raw controls refused publicly, 707 fail width rewriting and only five fail the instruction check.',
            'MC N0 M16 and MC N0 Y0 reach the remaining role; MC N0 X0 and MC K0 M16 remain explicit refusals.',
        ],
        limitations=[
            'The predicates are scoped to converter 15.31, adapter CPU 521, class32 and the recorded context. No Q or other FX CPU grammar is transferred.',
            'All original full-project observations still derive from one source; synthetic cases widen predicate coverage, not original project diversity.',
            'Role/type acceptance is not a complete instruction, address-range, project, execution or mutation-validity oracle.',
            'Twenty-five ordinary operand positions remain unvisited; M./U. record shapes are outside this ordinary header model.',
            'Digit groups above eight stay explicitly unsupported by the model; opaque native bypasses do not establish semantic validity.',
            'The temporary descriptor is a lossy native checking structure, not a replacement for raw-preserved operands.',
            'No PLC execution or new whole-program runtime-equivalence claim is made.',
        ],
        proof=dict(selected_native_witnesses=len(selected), frozen_replay=read_json_stdout(replay.stdout),
                   public_archive=public.relative_to(ROOT).as_posix(), local_archive=local.relative_to(ROOT).as_posix()))
    save(output, manifest)
    tools = [P / name for name in ('FXOperandCheckOracle.cs', 'NumericOperandOracle.cs', 'TraceFXOperandRules.js',
        'TraceFXOperandParser.js', 'TraceFXInstructionPhases.js', 'probe_fx_operand_rules.py',
        'probe_fx_operand_rule_breadth.py', 'probe_fx_operand_rule_full_corpus.py', 'probe_fx_operand_parser.py',
        'probe_fx_operand_rule_raw_holdouts.py', 'probe_fx_instruction_phases.py', 'probe_fx_operand_modifier_boundaries.py')]
    proof_files = [path for path in proof.iterdir() if path.is_file()]
    public_files = [output, Path(__file__)] + proof_files + tools
    public_info = archive(public, public_files, ROOT)
    local_files = tools + proof_files + [Path(__file__), output]
    for name in DATASETS + ['fx-native-instruction-catalog-20261001-v1']:
        local_files.extend(path for path in (P / name).rglob('*') if path.is_file())
    local_info = archive(local, local_files, ROOT)
    save(P / 'fx-operand-grammar-freeze-status-20261001.json', dict(public=public_info, local=local_info))
    print(json.dumps(dict(manifest=str(output), public=public_info, local=local_info, coverage=manifest['dynamic_coverage'],
                          frozen_replay=manifest['proof']['frozen_replay']), ensure_ascii=True))


def read_json_stdout(raw):
    return json.loads(raw.decode('utf-8-sig'))


if __name__ == '__main__':
    main()
