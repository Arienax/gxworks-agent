"""Replay the frozen Q reader without native software or original source parsers.

The public archive alone replays synthetic machine controls and configuration
aliases. --local adds retained original PCode-body comparisons. Native results
are read as historical observations; this program does not run native conversion.
"""
from pathlib import Path
from collections import Counter
import argparse
import base64
import json
import sys
import types
import zipfile


def read_json(archive, name):
    return json.loads(archive.read(name).decode('utf-8-sig'))


class FrozenJSON:
    def __init__(self, archive, name):
        self.archive, self.name = archive, name

    def read_text(self, encoding='utf-8'):
        return self.archive.read(self.name).decode(encoding)


def predicted(records):
    if not records or any(r['handling'] != 'decoded' for r in records):
        return None
    return b''.join(bytes.fromhex(r['pcode_hex']) for r in records)


def complete(row, size):
    return (row is not None and row['code']==0 and row['remaining']==0 and
            row['consumed']==size and row['input_unchanged'] and row['guards_intact'])


def compare(model, raw, profile, native, expected=None):
    records = model.read_machine(raw, profile)
    if b''.join(bytes.fromhex(r['raw_hex']) for r in records) != raw:
        raise ValueError('Reader changed preserved machine bytes')
    pcode = predicted(records)
    if pcode is not None:
        if not complete(native, len(raw)) or base64.b64decode(native['output_base64']) != pcode:
            raise ValueError('Independent reader differs from complete native observation')
        if expected is not None and pcode != expected:
            raise ValueError('Independent reader differs from original PCode bytes')
    return records, pcode


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public', type=Path, default=root / 'evidence/gxw-q-machine-reader-20261001.zip')
    parser.add_argument('--local', type=Path)
    args = parser.parse_args()
    with zipfile.ZipFile(args.public) as public:
        module_name = '_frozen_q_machine_reader'
        model = types.ModuleType(module_name)
        sys.modules[module_name] = model
        exec(compile(public.read('models/source_q_machine_reader.py'), 'frozen/models/source_q_machine_reader.py', 'exec'), model.__dict__)
        profile = model.load_profile(FrozenJSON(public, 'profiles/static-read-tables.json'),
                                     FrozenJSON(public, 'profiles/default-device-context.json'))
        configured = model.load_profile(FrozenJSON(public, 'profiles/static-read-tables.json'),
                                        FrozenJSON(public, 'profiles/observed-device-context.json'))
        checkpoint = read_json(public, 'checkpoint.json')
        holdouts = read_json(public, 'controls/holdouts-before-native.json')['requests']
        native = {r['id']:r for r in read_json(public, 'controls/holdouts-comparisons.json')}
        counts = Counter()
        for row in holdouts:
            raw = bytes.fromhex(row['machine_hex'])
            n = native[row['id']]
            records, pcode = compare(model, raw, profile, n['native_reverse'])
            if records != row['prediction']:
                raise ValueError('Frozen prospective prediction changed')
            counts['synthetic_controls'] += 1
            counts['synthetic_raw_preserved'] += 1
            counts['synthetic_decoded_native_exact'] += pcode is not None
            counts['synthetic_opaque_preserved'] += pcode is None
            f = n['reemission']
            if f:
                if not f['input_unchanged'] or not f['guards_intact']:
                    raise ValueError('Retained forward input or guard differs')
                counts['synthetic_native_forward_accepted'] += f['code']==0
                counts['synthetic_native_forward_refused'] += f['code']!=0
                if f['code']==0:
                    same = base64.b64decode(f['output_base64'])==raw
                    counts['synthetic_machine_reemission_exact'] += same
                    counts['synthetic_machine_reemission_changed'] += not same
        aliases = read_json(public, 'controls/configuration-aliases.json')
        for row in aliases:
            raw = bytes.fromhex(row['machine_hex'])
            _, correct = compare(model, raw, configured, row['configured_native'], bytes.fromhex(row['expected_pcode_hex']))
            _, wrong = compare(model, raw, profile, row['default_native'])
            if correct is None or wrong is None or correct==wrong:
                raise ValueError('Configuration alias counterexample no longer reproduces')
            counts['configuration_aliases_preserved'] += 1
            f = row['default_reemission']
            counts['wrong_context_machine_reemission_exact'] += f['code']==0 and base64.b64decode(f['output_base64'])==raw
        if dict(counts) != checkpoint['expected_public_counts']:
            raise ValueError('Public replay counts differ: '+repr(dict(counts)))
        result = dict(public=dict(counts))
        if args.local:
            with zipfile.ZipFile(args.local) as local:
                before = read_json(local, checkpoint['configured_before_entry'])
                n = {r['id']:r for r in (json.loads(line) for line in
                     local.read(checkpoint['configured_native_entry']).decode('utf-8-sig').splitlines())}
                corpus = Counter()
                by_id = {}
                for row in before['records']+before['bodies']:
                    raw = bytes.fromhex(row['machine_hex'])
                    expected = bytes.fromhex(row['original_pcode_hex'])
                    records, pcode = compare(model, raw, configured, n[row['id']], expected)
                    if records != row['prediction']:
                        raise ValueError('Original-body prospective prediction changed')
                    group = 'instructions' if row['id']<100000 else 'full_bodies'
                    corpus[group+'_inputs'] += 1
                    corpus[group+'_raw_preserved'] += 1
                    corpus[group+'_decoded_native_source_exact'] += pcode is not None
                    by_id[row['id']] = pcode is not None
                    if group=='full_bodies':
                        entry = checkpoint['configured_experiment_prefix']+'/'+row['original_entry']
                        if local.read(entry)!=expected:
                            raise ValueError('Retained original body differs')
                        for identifier in row['instruction_ids']:
                            corpus['weighted_original_instructions'] += 1
                            corpus['weighted_decoded_native_source_exact'] += by_id[identifier]
                if dict(corpus)!=checkpoint['expected_local_counts']:
                    raise ValueError('Local replay counts differ: '+repr(dict(corpus)))
                result['local'] = dict(corpus)
    print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
