"""Freeze inspected FX lexical reading, raw partitions and local byte proposals.

Only self-authored synthetic records are portable. Original project material
and complete instrumentation remain in the local archive.
"""
from pathlib import Path
import base64
import collections
import json
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'research/experiments/sfc-graph-20260926/public-corpus-discovery'
DATASETS = ('fx-header-reader-20261001-v1', 'fx-operand-reader-20261001-v1',
            'fx-record-reader-retrospective-20261001-v1', 'fx3u-original-machine-corpus-20261001-v1',
            'fx3u-original-lossless-projection-20261001-v1', 'fx-lossless-reader-original-corpus-20261001-v1',
            'fx-lossless-patch-20261001-v1')
MODEL_FILES = ('source_fx_operand_rules.py', 'source_fx_header_reader.py', 'source_fx_operand_reader.py',
               'source_fx_record_reader.py', 'source_fx_lossless_reader.py', 'fx-operand-reader-tables-20261001-v1.json')
TOOLS = ('FXReadRecordOracle.cs', 'TraceFXHeaderReader.js', 'TraceFXOperandReader.js',
         'probe_fx_header_reader.py', 'probe_fx_operand_reader.py', 'extract_fx_reader_tables.py',
         'check_fx_lossless_reader.py', 'probe_fx_lossless_patch.py', 'check_fx_patch_text.py')


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')


def archive(path, entries):
    names = [name for _,name in entries]
    if len(names) != len(set(names)) or any(not file.is_file() for file,_ in entries):
        raise ValueError('Duplicate or missing archive source')
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for file,name in sorted(entries, key=lambda row:row[1]):
            z.write(file,name)
    with zipfile.ZipFile(path) as z:
        for file,name in entries:
            if z.read(name) != file.read_bytes():
                raise ValueError('Archive entry differs from original retained bytes')
    return dict(entries=len(entries), bytes=path.stat().st_size, source_bytes_exact=True)


VALIDATOR = '''"""Replay original native observations using the frozen source reader only."""
from pathlib import Path
import base64
import collections
import json
from source_fx_record_reader import FXRecordReader
from source_fx_lossless_reader import FXLosslessReader
from source_fx_operand_rules import Unsupported

p = Path(__file__).resolve().parent
model, lossless = FXRecordReader(), FXLosslessReader()
counts, descriptors, header_descriptors = collections.Counter(), set(), set()
for witness in json.loads((p / 'native-synthetic-witnesses.json').read_text(encoding='utf-8')):
    raw = bytes.fromhex(witness['raw'])
    body = lossless.read(raw, cpu=witness['cpu'])
    if lossless.reconstruct(body) != raw:
        raise ValueError('A native success/refusal witness lost source bytes')
    counts['raw-round-trips'] += 1
    try:
        prediction = model.read(raw, cpu=witness['cpu'])
    except Unsupported as exc:
        if witness.get('unsupported') != str(exc) or witness['public']['code'] == 0:
            raise
        counts['explicit-unsupported'] += 1
        continue
    if witness.get('unsupported'):
        raise ValueError('Explicitly unsupported edge widened silently')
    expected = witness['prediction_before_native']
    # The path-only cleanup of the header module and equivalent decimal
    # trimming cleanup do not alter any prior prediction.
    if prediction != expected:
        raise ValueError('Frozen reader differs from prospective source prediction')
    header = witness.get('header_observation')
    if header is not None:
        predicted = prediction['header']
        if (predicted['raw_header'], predicted['result'], predicted['arity'], predicted['native_text_token']) != (header['raw'], header['result'], header['arity'], header['output']):
            raise ValueError('Native header name/arity observation differs')
        if predicted['descriptor_index'] is not None:
            header_descriptors.add(predicted['descriptor_index'])
        counts['header-exact'] += 1
    groups = witness['native_groups']
    atoms = witness['native_atoms']
    if len(groups) != len(prediction['operands']):
        raise ValueError('Native group boundary count differs')
    predicted_atoms = [a for group in prediction['operands'] for a in group['atoms']]
    if len(atoms) != len(predicted_atoms):
        raise ValueError('Native text atom call count differs')
    for predicted, native in zip(prediction['operands'], groups, strict=True):
        if (predicted['result'], predicted['consumed'], predicted['native_text_token']) != (native['result'], native['consumed'], native['output']):
            raise ValueError('Native operand result/extent/text differs')
        if (native['cpu'], native['descriptor_class']) != (witness['cpu'],32):
            raise ValueError('Unmeasured native group context')
        counts['groups-exact'] += 1
    for predicted,native in zip(predicted_atoms, atoms, strict=True):
        if (predicted['raw_token'], predicted['result'], predicted['display_bytes'], predicted['descriptor_index']) != (native['raw'], native['result'], native['output'], native['descriptor_index']):
            raise ValueError('Native operand text table observation differs')
        if native['descriptor_index'] is not None:
            descriptors.add(native['descriptor_index'])
        counts['atoms-exact'] += 1
    public = witness['public']
    if prediction['native_text_record'] is not None:
        if public['code'] != 0 or public['output'] != prediction['native_text_record']:
            raise ValueError('Independent public record decoding differs')
        counts['full-records-exact'] += 1
    elif prediction['handling'] == 'native-operand-text-refused' and public['code'] != 0:
        counts['native-refusals-exact'] += 1
    else:
        raise ValueError('Unclassified public/source reader outcome')
for witness in json.loads((p / 'opaque-preservation-witnesses.json').read_text(encoding='utf-8')):
    before = lossless.read(bytes.fromhex(witness['source_body']), cpu=witness['cpu'])
    plan = witness['patch']
    proposal = lossless.replace_same_extent_token(before, token_index=plan['token_index'], expected_raw=plan['before'], replacement_raw=plan['after'])
    if proposal != plan:
        raise ValueError('Local proposal touch span differs')
    after = lossless.read(bytes.fromhex(proposal['candidate']), cpu=witness['cpu'])
    if before != witness['before'] or after != witness['after']:
        raise ValueError('Lossless known/opaque partition differs')
    if lossless.reconstruct(after).hex() != plan['candidate']:
        raise ValueError('Changed body reconstruction differs')
    for token in before['physical_tokens']:
        if token['index'] != plan['token_index'] and token['raw'] != after['physical_tokens'][token['index']]['raw']:
            raise ValueError('Untouched physical token was rewritten')
    before_opaque = [r['raw'] for r in before['records'] if r['handling'] != 'decoded-native-record']
    after_opaque = [r['raw'] for r in after['records'] if r['handling'] != 'decoded-native-record']
    if before_opaque != after_opaque:
        raise ValueError('Opaque regions were rewritten')
    record = after['records'][after['physical_tokens'][plan['token_index']]['owner_record']]
    if witness['native_check']['code'] != 0 or witness['native_check']['status'] not in (0,1) or base64.b64decode(witness['native_check']['output_base64']).hex() != record['raw']:
        raise ValueError('Native changed-record check differs')
    if witness['native_text']['code'] != 0 or base64.b64decode(witness['native_text']['output_base64']).hex() != record['lexical_projection']['native_text_record']:
        raise ValueError('Independent changed-record text read differs')
    if witness.get('unframed_replacement_refused'):
        token = before['physical_tokens'][-1]
        try:
            lossless.replace_same_extent_token(before, token_index=token['index'], expected_raw=token['raw'], replacement_raw=token['raw'])
        except Unsupported:
            counts['unframed-edit-refused'] += 1
        else:
            raise ValueError('Unframed region became editable')
    counts['opaque-preserving-proposals'] += 1
result = dict(counts=dict(counts), reached_header_descriptors=len(header_descriptors), reached_text_descriptors=sorted(descriptors))
expected = json.loads((p / 'expected-replay.json').read_text(encoding='utf-8'))
if expected is not None and result != expected:
    raise ValueError('Frozen native witness coverage differs')
print(json.dumps(result, ensure_ascii=True))
'''


def main():
    result_path = ROOT / 'research/results/gxw-fx-lossless-reader-20261001.json'
    public = ROOT / 'research/evidence/gxw-fx-lossless-reader-20261001.zip'
    local = P / 'gxw-fx-lossless-reader-20261001-local.zip'
    proof = P / 'fx-lossless-reader-frozen-20261001'
    if any(path.exists() for path in (result_path, public, local, proof)):
        raise FileExistsError('Frozen results must not be replaced')
    summaries = {name:read(P / name / 'comparison.json') for name in DATASETS}
    for name in ('fx-header-reader-20261001-v1', 'fx-operand-reader-20261001-v1', 'fx3u-original-machine-corpus-20261001-v1'):
        s = summaries[name]
        if s['differences'] or s['hook_errors'] or not s['baseline_traced_exact']:
            raise ValueError('Unresolved native function comparison')
    whole = summaries['fx-lossless-reader-original-corpus-20261001-v1']
    if whole['differences'] or whole['total']['critical_gaps'] or whole['total']['decoded_instructions'] != 4625:
        raise ValueError('Whole-body lexical coverage differs')
    patch = summaries['fx-lossless-patch-20261001-v1']
    text = read(P / 'fx-lossless-patch-20261001-v1/independent-text-read/comparison.json')
    if patch['differences'] or patch['hook_errors'] or not patch['untouched_regions_preserved'] or text['native_text_exact'] != 9:
        raise ValueError('Unresolved raw proposal comparison')
    directory = P / 'fx-operand-reader-20261001-v1'
    requests = read(directory / 'before-native.json')['requests']
    predictions = read(directory / 'predictions-before-native.json')
    gaps = {r['id']:r['reason'] for r in read(directory / 'source-generation-gaps.json')}
    answers = {r['id']:r for r in map(json.loads,(directory / 'baseline.jsonl').read_text(encoding='utf-8-sig').splitlines())}
    native = collections.defaultdict(list)
    for line in (directory / 'events.jsonl').read_text(encoding='utf-8').splitlines():
        packet = json.loads(line)
        if packet['type'] != 'send':
            raise ValueError('Original hook failure retained')
        event = packet['payload']
        if event['event'] in ('operand-reader','operand-text-atom'):
            native[event['request']].append(event)
    header_observations = {r['id']:r['native'] for r in read(P / 'fx-header-reader-20261001-v1/header-comparisons.json')}
    portable = []
    for row in requests:
        # The first 4625 requests contain original source material. Subsequent
        # records are the own catalog, numeric, text and modifier controls.
        if row['id'] < 4625:
            continue
        answer = answers[row['id']]
        if not answer['guards_intact'] or not answer['input_preserved']:
            raise ValueError('Read-only owned helper invariant differs')
        witness = dict(id=len(portable), request=row['id'], cpu=row['cpu'], raw=row['raw'], provenance=row['provenance'],
            prediction_before_native=predictions.get(str(row['id'])), header_observation=header_observations.get(row['id']),
            public=dict(code=answer['code'], size=answer['size'], output=base64.b64decode(answer['output_base64']).hex()),
            native_groups=[e for e in native[row['id']] if e['event'] == 'operand-reader'],
            native_atoms=[e for e in native[row['id']] if e['event'] == 'operand-text-atom'])
        if row['id'] in gaps:
            witness['unsupported'] = gaps[row['id']]
        portable.append(witness)
    changed_checks = {r['case']:r['public'] for r in read(P / 'fx-lossless-patch-20261001-v1/native-comparisons.json')}
    changed_texts = {r['case']:r['original_native_read'] for r in read(P / 'fx-lossless-patch-20261001-v1/independent-text-read/comparisons.json')}
    preservation = []
    for case in ('synthetic-unknown','synthetic-broken-tail'):
        witness = read(P / 'fx-lossless-patch-20261001-v1' / (case + '.json'))
        witness.update(native_check=changed_checks[case], native_text=changed_texts[case])
        preservation.append(witness)
    proof.mkdir()
    for name in MODEL_FILES:
        (proof / name).write_bytes((P / name).read_bytes())
    catalog = proof / 'fx-native-instruction-catalog-20261001-v1'
    catalog.mkdir()
    (catalog / 'before-native.json').write_bytes((P / 'fx-native-instruction-catalog-20261001-v1/before-native.json').read_bytes())
    save(proof / 'native-synthetic-witnesses.json', portable)
    save(proof / 'opaque-preservation-witnesses.json', preservation)
    save(proof / 'experiment-summaries.json', summaries | {'changed-record-independent-text':text})
    save(proof / 'expected-replay.json', None)
    (proof / 'validate_witnesses.py').write_text(VALIDATOR, encoding='utf-8')
    replay = subprocess.run([sys.executable, str(proof / 'validate_witnesses.py')], capture_output=True, timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    (proof / 'replay-stdout.txt').write_bytes(replay.stdout)
    (proof / 'replay-stderr.txt').write_bytes(replay.stderr)
    if replay.returncode:
        raise RuntimeError('Frozen reader witnesses failed to replay')
    expected = json.loads(replay.stdout.decode('utf-8-sig'))
    save(proof / 'expected-replay.json', expected)
    manifest = dict(schema_version=1, date='2026-10-01',
        evidence_level='verified original lexical reader boundaries/public output within recorded scope',
        handling='decoded names and raw-bound operand spans; opaque metadata/unknown records/unframed remainders preserved',
        environment=dict(os='Windows', converter='ECCodeGeneratorFX2.dll', file_version='15.31', cpus=[520,521],
                         descriptor_class=32, open_mode=0, versions=[], fresh_native_object_per_read=True),
        static_readers=dict(header_rva='0x3d9d5', operand_group_rva='0x3d7b9', operand_atom_rva='0x3d514',
            reachable_header_descriptors=435, operand_text_descriptor_rows=32,
            native_descriptor_arity_field='First byte at descriptor+4, observed at the original header-reader write'),
        prospective_header=summaries['fx-header-reader-20261001-v1'],
        prospective_operands=summaries['fx-operand-reader-20261001-v1'],
        whole_body_corpus=whole, original_fx3u_machine=summaries['fx3u-original-machine-corpus-20261001-v1'],
        raw_byte_proposals=patch, changed_record_independent_text=text,
        discoveries=[
            'One native descriptor graph explains ordinary, pulse, LD/AND/OR comparative and embedded M./U. header names and arities.',
            'The lexical operand reader uses its own name/format table and grouping rules, distinct from the nine-byte type parser and machine-cell lowering.',
            'Repeated index and bit modifiers overwrite earlier display slots; ordered group/prefix tokens and every overwritten original token stay bound to raw offsets.',
            'Native octal display pads to three digits. Word K text reads at most the first WORD, while plain device text reads one-to-four-byte values.',
            'Float display can round or discard a zero sign, and decimal negative zero is refused on the inspected path. Display text is never used to reconstruct raw values.',
            'All twelve M./U. descriptor shapes decode bounded embedded-name controls; this establishes lexical headers rather than valid FB calls or machine emission.',
            'The original RET record 056c012405 is recognized generically. Seven independent FX3U originals have zero critical lexical gaps and retain all seventeen metadata records.',
            'A local same-extent proposal changes one selected parameter byte while unknown framed records and a broken framing remainder remain byte-identical.',
        ],
        source_provenance=dict(method_reference='https://github.com/purinzan/gx3-cli-mcp',
            method_usage='Raw-preserved IR, independent native decoding, failure corpus and scope-separated coverage only; no GX3 formats transferred.',
            originals=['https://github.com/Serhioromano/gxw2-libraries', 'https://github.com/cmz269/-plc-gxworks2-',
                       'https://github.com/factonation/modbus-rs485-fundamentals'],
            original_material_distribution='Local only; portable archive contains synthetic controls and derived static reader facts, not source GXW projects or the vendor DLL.'),
        limitations=[
            'Converter 15.31, class32, CPU520/521 lexical scope only; no Q or other CPU formats inferred.',
            'Full FX3G body comes from an accepted copied variant; seven FX3U bodies are unchanged independent originals. Repeated runs and synthetic cases are not extra project sources.',
            'Interior NUL payloads, unmeasured name-lookup edges, overlong modifier/text chains and invalid physical framing stay unsupported or opaque.',
            'Header names, arities and public lexical acceptance do not establish operand roles, address ranges, stored machine widths, project validity or execution equivalence.',
            'Nine changed records were independently checked/read; whole GXW compile/save/reopen after these proposals was not performed.',
            'The same-extent proposal helper is a research byte operation, not expanded product writer permission. Product code does not import this model.',
        ],
        proof=dict(portable_witnesses=len(portable), portable_opaque_proposals=len(preservation), replay=expected,
                   public_archive=public.relative_to(ROOT).as_posix(), local_archive=local.relative_to(ROOT).as_posix()))
    save(result_path,manifest)
    proof_files = [file for file in proof.rglob('*') if file.is_file() and '__pycache__' not in file.parts]
    public_entries = [(result_path,'manifest.json'), (Path(__file__),Path(__file__).name)]
    public_entries += [(file,'model/' + file.relative_to(proof).as_posix()) for file in proof_files]
    public_entries += [(P / name,'tools/' + name) for name in TOOLS]
    public_info = archive(public,public_entries)
    with tempfile.TemporaryDirectory(prefix='gxw-fx-reader-replay-') as temporary:
        with zipfile.ZipFile(public) as z:
            z.extractall(temporary)
        process = subprocess.run([sys.executable,str(Path(temporary) / 'model/validate_witnesses.py')], capture_output=True,
                                 timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
        if process.returncode or json.loads(process.stdout.decode('utf-8-sig')) != expected:
            raise RuntimeError('Extracted standalone evidence replay differs')
    local_files = [result_path,Path(__file__),*proof_files,*[P / name for name in TOOLS]]
    for name in DATASETS:
        local_files.extend(file for file in (P / name).rglob('*') if file.is_file() and '__pycache__' not in file.parts)
    local_files += list(P.glob('fx-pcode-*-20261001.txt'))
    local_files += [P / 'additional-real-projects/inventory.json']
    for project in read(P / 'fx3u-original-machine-corpus-20261001-v1/before-native.json')['projects']:
        local_files += [Path(project['local_project']),Path(project['local_compiled_body'])]
    local_entries = [(file,file.relative_to(ROOT).as_posix()) for file in dict.fromkeys(local_files)]
    local_info = archive(local,local_entries)
    save(P / 'fx-lossless-reader-freeze-status-20261001.json',dict(public=public_info,local=local_info,portable_archive_replay=expected))
    print(json.dumps(dict(manifest=str(result_path),public=public_info,local=local_info,replay=expected),ensure_ascii=True))


if __name__ == '__main__':
    main()
