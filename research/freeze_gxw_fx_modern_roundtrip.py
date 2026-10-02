"""Freeze independent FX3U/FX3G decoding and original-project patch evidence.

Portable witnesses are synthetic. Original projects and their native work stay
in the local archive. Frozen files are compared directly, without digests.
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
MODELS = ('source_fx_modern_decoder.py', 'source_fx_machine.py', 'source_fx3u_machine.py',
    'source_fx_instruction_cells.py', 'source_fx_operand_rules.py', 'source_fx_lossless_reader.py',
    'source_fx_record_reader.py', 'source_fx_header_reader.py', 'source_fx_operand_reader.py',
    'fx-modern-decoder-tables-20261001-v1.json', 'fx-machine-tables-20261001-v1.json',
    'fx-instruction-cell-tables-20261001-v1.json', 'fx-operand-reader-tables-20261001-v1.json',
    'fx-native-instruction-catalog-20261001-v1/before-native.json')
EXPERIMENTS = ('fx-modern-independent-machine-decoder-20261001-v1',
    'fx-modern-independent-machine-decoder-20261001-v2',
    'fx-modern-independent-machine-holdouts-20261001-v1',
    'fx-modern-independent-machine-holdouts-20261001-v2',
    'fx-modern-uninitialized-history-20261001-v1',
    'fx-modern-public-machine-roundtrips-20261001-v1')
PROJECTS = ('fx3u-gxw-patch-20261001-v1', 'fx3u-gxw-patch-20261001-v2',
    'fx3u-gxw-patch-20261001-v3', 'fx3u-gxw-native-patches-20261001-v1',
    'fx3u-native-source-footer-20261001-v1', 'fx3u-short-source-footer-20261001-v1',
    'fx3u-saved-source-reaudit-20261001-v1')


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines()]


def archive(path, entries):
    if len({name for _, name in entries}) != len(entries):
        raise ValueError('Duplicate archive entry')
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for source, name in sorted(entries, key=lambda pair: pair[1]):
            z.write(source, name)
    with zipfile.ZipFile(path) as z:
        for source, name in entries:
            if z.read(name) != source.read_bytes():
                raise ValueError('Archive bytes differ from retained file: ' + name)
    return dict(entries=len(entries), bytes=path.stat().st_size, entry_bytes_exact=True)


VALIDATOR = '''"""Cold replay; no native DLL, hooks or network."""
from pathlib import Path
import base64,collections,json,os,sys
p=Path(__file__).resolve().parent
os.environ['GXW2_INSTRUCTION_CATALOG']=str(p/'product/resources/instructions/mitsubishi')
sys.path.insert(0,str(p/'models'))
sys.path.insert(0,str(p/'product/src'))
from source_fx_modern_decoder import FXModernDecoder
from source_fx_machine import FXMachine
from source_fx3u_machine import FX3UMachine,FX3UCells
from source_fx_lossless_reader import FXLosslessReader
from source_fx_operand_rules import Unsupported
from gxw.token_pou import frame_token_pou,parse_token_pou
from gxw.models import GXWFormatError
def load(name):return json.loads((p/name).read_text(encoding='utf-8'))
decoders={cpu:FXModernDecoder(load('models/fx-modern-decoder-tables-20261001-v1.json'),cpu=cpu) for cpu in (520,521)}
emitters={521:FXMachine(),520:FX3UMachine(load('models/fx3u-machine-tables-before-native.json'),FX3UCells(load('models/fx3u-cell-tables-before-native.json')))}
counts=collections.Counter();gap_ids=set()
for r in load('decoder-witnesses.json'):
    raw=bytes.fromhex(r['machine']);at=0;records=[];gap=None
    while at<len(raw):
        try:record=decoders[r['cpu']].record(raw[at:])
        except Unsupported as exc:gap=str(exc);break
        n=record['words'] if record['words']>0 else 1
        record.update(offset=at,consumed_machine=raw[at:at+2*n].hex());records.append(record);at+=2*n
    prediction=dict(records=records,consumed=at,gap=gap)
    if prediction!=r['prediction']:raise ValueError('Prospective independent decoder snapshot differs')
    counts['decoder_groups']+=1
    if gap:counts['opaque_source_gaps']+=1;gap_ids.add(r['id'])
    elif len(records)!=len(r['native']) or r['public']['consumed']!=at:raise ValueError('Native decoder extent/count differs')
    else:counts['complete_decoder_groups']+=1
    if len(r['native'])<len(records):raise ValueError('Predicted native decoder call absent')
    for expected,native in zip(records,r['native']):
        if (expected['words'],expected['cells'],expected['auxiliary'])!=(native['words'],native['cells'],native['auxiliary']):raise ValueError('Native decoder fields differ')
        if (native['cpu'],native['mode'],native['before'],native['auxiliary_before'])!=(r['cpu'],8 if r['cpu']==520 else 9,'ff'*88,'00'*68):raise ValueError('Native decoder context differs')
        if expected['words']>0 and expected['consumed_machine']!=native['machine']:raise ValueError('Native machine span differs')
        counts['native_decoder_records_exact']+=1
    if not r['public']['guards_intact'] or not r['public']['input_unchanged']:raise ValueError('Owned native arena changed')
    counts['native_reverse_refusals']+=r['public']['code']!=0
changed=load('opaque-public-output-changes.json')
if {r['id'] for r in changed}!=gap_ids:raise ValueError('Opaque output changes were lost or normalized')
counts['opaque_public_output_changes']=len(changed)
reader=FXLosslessReader()
for r in load('reemission-witnesses.json'):
    raw=bytes.fromhex(r['raw']);ir=reader.read(raw,cpu=r['cpu'])
    if reader.reconstruct(ir)!=raw:raise ValueError('Canonical raw partition differs')
    records=[];machines=[]
    for record in ir['records']:
        if record['critical_gap']:raise ValueError('Canonical lexical gap')
        prediction=emitters[r['cpu']].record(bytes.fromhex(record['raw']))
        if prediction['emission']['words']<=0 or prediction['emission']['machine_output'] is None:raise ValueError('Canonical emission gap')
        records.append(dict(offset=record['offset'],prediction=prediction));machines.append(prediction['emission']['machine_output'])
    expected=dict(records=records,gaps=[],expected_machine=''.join(machines))
    if expected!=r['prediction']:raise ValueError('Prospective reemission snapshot differs')
    answer=r['public'];output=base64.b64decode(answer['output_base64']).hex()
    if answer['code']!=0 or output!=expected['expected_machine'] or answer['consumed']!=len(raw) or answer['remaining']!=0:raise ValueError('Public canonical reemission differs')
    if not answer['guards_intact'] or not answer['input_unchanged']:raise ValueError('Reemission native arena changed')
    if len(r['native'])!=len(records):raise ValueError('Reemission call count differs')
    for record,native in zip(records,r['native'],strict=True):
        prediction=record['prediction'];emission=prediction['emission']
        if (prediction['native_cells'],prediction['auxiliary'],emission['words'],emission['cells_after'],emission['machine_output'])!=(native['before'],native['auxiliary'],native['words'],native['after'],native['output']):raise ValueError('Native reemission fields differ')
        if (native['cpu'],native['mode'])!=(r['cpu'],8 if r['cpu']==520 else 9):raise ValueError('Reemission context differs')
        counts['native_reemission_records_exact']+=1
    counts['canonical_reemission_groups']+=1
    counts['canonical_machine_roundtrip_exact']+=output==r['original_machine']
    counts['canonical_native_machine_changes']+=output!=r['original_machine']
for r in load('opaque-trailer-witnesses.json'):
    raw=bytes.fromhex(r['synthetic_source']);frame=frame_token_pou(raw)
    if frame.body.hex()!=r['body'] or raw[frame.body_end:].hex()!=r['trailer'] or frame.reconstruct()!=raw:raise ValueError('Opaque source trailer differs')
    if len(bytes.fromhex(r['trailer']))==24 and parse_token_pou(raw).reconstruct()!=raw:raise ValueError('24-byte FX trailer differs')
    for extent in (19,21,23,25):
        try:frame_token_pou(raw[:frame.body_end]+bytes(extent))
        except GXWFormatError:pass
        else:raise ValueError('Unobserved trailer extent accepted')
    counts['opaque_trailer_witnesses']+=1
print(json.dumps(dict(counts),ensure_ascii=True))
'''


def main():
    result = ROOT / 'research/results/gxw-fx-modern-roundtrip-20261001.json'
    public = ROOT / 'research/evidence/gxw-fx-modern-roundtrip-20261001.zip'
    local = P / 'gxw-fx-modern-roundtrip-20261001-local.zip'
    proof = P / 'fx-modern-roundtrip-frozen-20261001'
    if any(path.exists() for path in (result, public, local, proof)):
        raise FileExistsError('Frozen evidence must not be replaced')
    original = read(P / EXPERIMENTS[1] / 'comparison.json')
    synthetic = read(P / EXPERIMENTS[3] / 'comparison.json')
    forward = read(P / EXPERIMENTS[5] / 'comparison.json')
    patches = read(P / PROJECTS[-1] / 'comparison.json')
    for name, measured in ((EXPERIMENTS[1], original), (EXPERIMENTS[3], synthetic), (EXPERIMENTS[5], forward)):
        if measured.get('differences', 0) or measured.get('source_native_differences', 0) or measured.get('original_differences', 0) or measured['hook_errors'] or not measured['guards_intact'] or not measured['input_unchanged']:
            raise ValueError('Unresolved native fields: ' + name)
    if not original['baseline_traced_exact'] or not forward['baseline_traced_exact'] or not synthetic['baseline_traced_complete_source_exact']:
        raise ValueError('Complete native output changed under observation')
    if (patches['projects'], patches['saved_source_body_exact'], patches['full_check_accepted'], patches['original_check_failures_retained']) != (7, 7, 4, 3):
        raise ValueError('Saved original-project source audit differs')
    proof.mkdir();public_entries=[];private_entries=[]
    for name in MODELS:
        public_entries.append((P / name, 'models/' + name))
    for name in ('fx3u-machine-tables-before-native.json', 'fx3u-cell-tables-before-native.json'):
        public_entries.append((P / 'fx3u-original-machine-corpus-20261001-v1' / name, 'models/' + name))
    for name in ('extract_fx_modern_decoder_tables.py', 'probe_fx_modern_machine_decoder.py',
                 'probe_fx_modern_uninitialized_history.py', 'probe_fx_modern_machine_roundtrips.py',
                 'analyze_fx_modern_roundtrip_changes.py', 'probe_fx3u_lossless_source_patches.py',
                 'run_fx3u_lossless_project_patches.py', 'probe_fx3u_saved_source_footer.py',
                 'audit_fx3u_saved_sources.py', 'FXModernHistoryOracle.cs'):
        public_entries.append((P / name, 'tools/' + name))
    public_entries.append((Path(__file__), 'tools/' + Path(__file__).name))
    for name in ('FXModernFromMachineOracle.cs', 'TraceFXModernDecoder.js'):
        public_entries.append((P / EXPERIMENTS[3] / name, 'tools/' + name))
    for name in ('FXModernToMachineOracle.cs', 'TraceFXPublicReemission.js'):
        public_entries.append((P / EXPERIMENTS[5] / name, 'tools/' + name))
    public_entries.append((P / 'probe_fx1s_lossless_project_patch.py', 'tools/probe_fx1s_lossless_project_patch.py'))
    directory = P / EXPERIMENTS[3]
    rows = read(directory / 'before-native.json')['requests']
    predictions = read(directory / 'predictions-before-native.json')
    observations = read(directory / 'observations.json')
    save(proof / 'decoder-witnesses.json', [dict(id=r['id'], cpu=r['cpu'], machine=r['machine'],
        origins=r['origins'], prediction=predictions[str(r['id'])], native=o['native'], public=o['public'])
        for r, o in zip(rows, observations, strict=True)])
    save(proof / 'opaque-public-output-changes.json', read(directory / 'baseline-traced-differences.json'))
    directory = P / EXPERIMENTS[5]
    events = collections.defaultdict(list)
    for packet in lines(directory / 'events.jsonl'):
        if packet['type'] == 'send' and packet['payload']['event'] == 'machine-emission':
            events[packet['payload']['request']].append(packet['payload'])
    observations = read(directory / 'observations.json')
    save(proof / 'reemission-witnesses.json', [dict(cpu=r['source']['cpu'], raw=r['source']['raw'],
        original_machine=r['source']['original_machine'], prediction=r['prediction'], public=r['public'],
        native=events[r['source']['id']]) for r in observations if not r['source']['original_case']])
    trailers = []
    for name in PROJECTS[4:6]:
        measured = read(P / name / 'comparison.json')
        if not all(measured[k] for k in ('pcode_exact', 'resaved_body_exact', 'footer_byte_identical')):
            raise ValueError('Native opaque footer measurement differs')
        outcome = measured['compile']
        if outcome['compiler_rejected'] or outcome['program_check'][0]['rejected'] or not outcome['exported']:
            raise ValueError('Native opaque footer compile/check/save failed')
        if measured['reopen']['attempts'][-1]['returncode'] != 0 or measured['reopen']['failures']:
            raise ValueError('Native opaque footer reopen failed')
        source = measured['resaved_source'];body=bytes.fromhex(source['body']);trailer=bytes.fromhex(source['footer'])
        import struct
        prefix=bytearray(79);prefix[54]=1;prefix[63:79]=bytes.fromhex('010000000c00000000000000ffffffff')
        struct.pack_into('<II',prefix,55,len(body)+20,len(body)+20)
        trailers.append(dict(body=source['body'], trailer=source['footer'],
            synthetic_source=(bytes(prefix)+body+trailer).hex(), native_measurement=name,
            native_compile_and_full_check_accepted=True, native_footer_retained_after_save=True,
            scope='Synthetic header with own LD M0 / OUT M1 / END body and measured opaque native footer; original project stays local.'))
    save(proof / 'opaque-trailer-witnesses.json', trailers)
    (proof / 'replay.py').write_text(VALIDATOR, encoding='utf-8')
    (proof / 'empty-init.py').write_text('', encoding='utf-8')
    for name in ('decoder-witnesses.json', 'opaque-public-output-changes.json', 'reemission-witnesses.json', 'opaque-trailer-witnesses.json', 'replay.py'):
        public_entries.append((proof / name, name))
    for package in ('gxw', 'plc'):
        public_entries.append((proof / 'empty-init.py', 'product/src/' + package + '/__init__.py'))
    for name in ('src/gxw/token_pou.py', 'src/gxw/models.py', 'src/plc/instruction_steps.py',
                 'resources/instructions/mitsubishi/fx3u_step_widths.json'):
        public_entries.append((ROOT / name, 'product/' + name))
    public_entries.append((ROOT / 'LICENSE', 'LICENSE'))
    for name in EXPERIMENTS:
        for filename in ('comparison.json', 'harness-failure.json', 'source-gaps.json'):
            path = P / name / filename
            if path.exists():
                if filename == 'harness-failure.json':
                    original_failure = read(path)
                    path = proof / 'initial-decoder-harness-failure.json'
                    save(path, {key: original_failure[key] for key in ('stage', 'exception', 'reason', 'traceback')})
                public_entries.append((path, 'measurements/' + name + '/' + filename))
    for filename in ('change-analysis.json', 'change-analysis-v2.json', 'native-roundtrip-change-classification-v2.json'):
        public_entries.append((P / EXPERIMENTS[5] / filename, 'measurements/roundtrip-changes/' + filename))
    for filename in ('before-native.json', 'predictions-before-native.json', 'observations.json', 'baseline.jsonl', 'traced.jsonl'):
        public_entries.append((P / EXPERIMENTS[4] / filename, 'measurements/opaque-history/' + filename))
    summary = dict(projects=7, cpu=520, source_proposals={'one_byte_contact_changes': 6, 'empty_body_insertion': 1},
        original_instruction_records=429, original_opaque_metadata_records=17,
        compiled_pcode_exact=7, saved_source_body_exact=7, fresh_reopen_completed=7,
        full_program_check_accepted=4, original_full_check_rejections_retained=3,
        error_diagnostics_unchanged_from_original=7, corrected_reader_false_negatives=1,
        opaque_nonzero_trailer_extents=[20, 24], native_opaque_footer_recompile_full_check_save_reopen=2,
        product_reader_verification={'test_owner': 'tests/test_gxw_lossless.py', 'passed': 326},
        scope='Specific offline copied-project proposals. Six retain file extent and change one byte; one inserts LD M0 / OUT M1 before unchanged END. Unrelated stream payloads preserved. Fresh reopen was observed separately and does not imply a second compilation or controller execution.')
    save(proof / 'project-patch-summary.json', summary)
    public_entries.append((proof / 'project-patch-summary.json', 'measurements/project-patch-summary.json'))
    public_archive = archive(public, public_entries)
    with tempfile.TemporaryDirectory(prefix='fx-modern-cold-', dir=proof) as temporary:
        with zipfile.ZipFile(public) as z:
            z.extractall(temporary)
        cold = subprocess.run([sys.executable, str(Path(temporary) / 'replay.py')], capture_output=True, timeout=55, cwd=temporary)
        (proof / 'cold-stdout.txt').write_bytes(cold.stdout);(proof / 'cold-stderr.txt').write_bytes(cold.stderr)
        if cold.returncode:
            raise ValueError('Cold replay failed; archive and diagnostics retained')
        replay = json.loads(cold.stdout)
    for name in (*EXPERIMENTS, *PROJECTS):
        directory = P / name
        for path in directory.rglob('*'):
            if not path.is_file() or any(part in ('home', 'compiler', 'compiler_DZComp', 'native-temp', '__pycache__') for part in path.relative_to(directory).parts):
                continue
            if path.suffix.lower() not in ('.json', '.jsonl', '.gxw', '.bin', '.py', '.cs', '.js', '.txt', '.tsv'):
                continue
            private_entries.append((path, name + '/' + path.relative_to(directory).as_posix()))
    local_archive = archive(local, private_entries)
    manifest = dict(converter='15.31', cpus=[520, 521], modes=[8, 9],
        scope='Independent machine decoding and separate public reemission; GX Works3 methods only, no format transfer',
        original_instruction_pcode_machine_pcode_byte_exact=4625, original_public_machine_roundtrip_exact=4625,
        native_independent_synthetic_decoder_records_exact=5934,
        native_public_reemission_predictions_exact=10333,
        synthetic_canonical_machine_changes=62, synthetic_changed_machine_groups=31,
        synthetic_native_reverse_refusals=66, opaque_source_gaps=36,
        opaque_public_output_changes=36, prior_valid_address_propagation_rejected=True,
        project_patches=summary, cold_replay=replay, public_archive=public_archive, local_archive=local_archive,
        limitations=['Opaque uninitialized branches stay unsupported despite native success; changed outputs retained verbatim',
            'Canonical machine/PCode conversion cannot replace original raw tokens for lossless editing',
            'Three modified original projects retain their original complete-check error diagnostics',
            'No controller execution, device-range certification or whole-family writer validation'],
        original_distribution='Original third-party project bodies and native project work remain in the local archive')
    save(result, manifest)
    print(json.dumps(manifest, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
