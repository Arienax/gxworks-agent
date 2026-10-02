"""Replay retained source-grid predictions without GX Works2 or digests.

Run from the extracted public archive with its matching private archive path.
Native check events are retained observations, not new native executions.
"""
from pathlib import Path
import collections
import json
import os
import struct
import sys
import zipfile


def main():
    root = Path(__file__).resolve().parent
    os.environ['GXW2_INSTRUCTION_CATALOG'] = str(root / 'product/resources/instructions/mitsubishi')
    sys.path[:0] = [str(root / 'models'), str(root / 'product/src')]
    from gxw.container import CompoundFile
    from gxw.sfc_pou import parse_sfc_pou
    from source_sfc_grid import read_grid
    from source_fx_sfc_grid_encoder import encode_grid, UnsupportedGrid
    from source_fx_sfc_pending_projection import source_grid, reference_view
    from source_fx_sfc_multireg_projection import expand_block
    from source_fx_sfc_lossless_reader import FXSfcLosslessReader

    counts = collections.Counter()
    with zipfile.ZipFile(sys.argv[1]) as archive:
        load = lambda name: json.loads(archive.read(name).decode('utf-8-sig'))
        events = lambda name: [json.loads(line) for line in archive.read(name).decode('utf-8-sig').splitlines()]
        manifest = load('checkpoint.json')
        for row in manifest['grids']:
            raw = archive.read(row['chars_entry'])
            prediction = encode_grid(raw)
            if prediction.reconstruction() != raw:
                raise ValueError('Source character reconstruction differs')
            if prediction.tokens != archive.read(row['native_tokens_entry']):
                raise ValueError('Source-only token prediction differs')
            if len(prediction.records) != len(row['native_work_entries']):
                raise ValueError('Native work record count differs')
            for record, entry in zip(prediction.records, row['native_work_entries'], strict=True):
                if record['raw'] != archive.read(entry):
                    raise ValueError('Source-only work record differs')
            if row['prospective']:
                if prediction.tokens != archive.read(row['expected_tokens_entry']):
                    raise ValueError('Retained prospective prediction differs')
                counts['prospective_graphs_exact'] += 1
            checks = [e for e in events(row['events_entry']) if e['operation'] == 'SFC.CheckResult']
            if len(checks) != 1 or checks[0]['result'] != 0:
                raise ValueError('Retained native graph check was not accepted')
            counts['source_graphs_exact'] += 1
            counts['native_work_records_exact'] += len(prediction.records)
            counts['encoded_reference_bytes_exact'] += len(prediction.tokens)

        for row in manifest['unsupported']:
            raw = archive.read(row['chars_entry'])
            if read_grid(raw).reconstruction() != raw:
                raise ValueError('Unsupported source was not preserved')
            try:
                encode_grid(raw)
            except UnsupportedGrid:
                pass
            else:
                raise ValueError('A retained unsupported graph was admitted')
            checks = [e for e in events(row['events_entry']) if e['operation'] == 'SFC.CheckResult']
            if len(checks) != 1 or checks[0]['result'] != row['native_check_result']:
                raise ValueError('Retained graph admission counterexample differs')
            counts['unsupported_graphs_preserved'] += 1

        original = CompoundFile(archive.read(manifest['original_project_entry']))
        original_hdb = CompoundFile(original.read_stream('_hdb'))
        def payloads(cfb):
            return {e.name: cfb.read_stream(e.name) for e in cfb.iter_streams()}
        original_outer, original_inner = payloads(original), payloads(original_hdb)
        for row in manifest['projects']:
            candidate = archive.read(row['input_entry'])
            outer = CompoundFile(candidate)
            inner = CompoundFile(outer.read_stream('_hdb'))
            op, ip = payloads(outer), payloads(inner)
            if op.keys() != original_outer.keys() or ip.keys() != original_inner.keys():
                raise ValueError('Proposal changed the unrelated stream set')
            if any(op[n] != original_outer[n] for n in op if n != '_hdb'):
                raise ValueError('Proposal changed an unrelated outer stream')
            if any(ip[n] != original_inner[n] for n in ip if n != row['source_stream']):
                raise ValueError('Proposal changed an unrelated inner stream')
            stored = ip[row['source_stream']]
            _, _, extent = source_grid(stored)
            _, _, before_extent = source_grid(original_inner[row['source_stream']])
            if stored[extent['cache_offset']:extent['start'] + extent['size']] != original_inner[row['source_stream']][before_extent['cache_offset']:before_extent['start'] + before_extent['size']]:
                raise ValueError('Proposal changed the pending opaque cache or suffix')
            expected = archive.read(row['expected_pcode_entry'])
            initial_source = parse_sfc_pou(stored)
            for stage in row['stages']:
                project = CompoundFile(archive.read(stage['input_entry']))
                hdb = CompoundFile(project.read_stream('_hdb'))
                source_raw = hdb.read_stream(stage['source_stream'])
                source = parse_sfc_pou(source_raw)
                if source.reconstruct() != source_raw:
                    raise ValueError('Raw POU reconstruction differs')
                start = source.layout['graph']['offset']
                mode = struct.unpack_from('<II', source_raw, start + 4)
                if mode == (0, 1):
                    _, chars, framing = source_grid(source_raw)
                    view = reference_view(source_raw, encode_grid(chars).tokens, framing)
                elif mode == (0, 0):
                    view = source_raw
                else:
                    raise ValueError('Frozen source has an unmeasured storage mode')
                body, _ = expand_block(view, registration_policy='first')
                number = stage['block_number']
                if not 0 <= number <= 255:
                    raise ValueError('Frozen source block number is outside the measured range')
                resource = bytes.fromhex('056c010005056c01020504dc') + bytes((number, 4)) + body + bytes.fromhex('056c010305056c010105033403')
                if resource != expected or resource != archive.read(stage['pcode_entry']):
                    raise ValueError('Source-only full resource prediction differs')
                decoded = FXSfcLosslessReader().read(resource, cpu=520)
                if FXSfcLosslessReader.reconstruct(decoded) != resource:
                    raise ValueError('Independent code partition reconstruction differs')
                if [(c['kind'], c['name'], c['number'], c['token_hex']) for c in source.layout['children']] != [(c['kind'], c['name'], c['number'], c['token_hex']) for c in initial_source.layout['children']]:
                    raise ValueError('Native save changed an owned child body')
                if [(a['name'], a['number'], a['registrations']) for a in source.layout['actions']] != [(a['name'], a['number'], a['registrations']) for a in initial_source.layout['actions']]:
                    raise ValueError('Native save changed an action registration')
                observed = events(stage['events_entry'])
                conversion = [e for e in observed if e['operation'] == 'SFCConversionCompleted']
                checks = [e for e in observed if e['operation'] == 'ProgramCheckCompleted']
                if not conversion or any(e['rejected'] for e in conversion) or not checks or any(e['rejected'] for e in checks):
                    raise ValueError('Retained native conversion or full check was rejected')
                counts['full_resource_predictions_exact'] += 1
                counts['retained_native_full_checks_accepted'] += len(checks)
            counts['local_project_candidates_preserved'] += 1
        if dict(counts) != manifest['expected_cold_counts']:
            raise ValueError('Cold replay counts differ: ' + repr(dict(counts)))
    print(json.dumps(dict(counts), ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
