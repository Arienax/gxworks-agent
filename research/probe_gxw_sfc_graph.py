"""Probe compiled native SFC graph tokens while preserving opaque cache bytes.

Hypotheses are deliberately tested separately: changing the step-device operand
alone versus also changing its action registration. Native conversion, full
program checks, and later GUI reopen distinguish acceptance from consistency.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

from probe_gxw_sfc_sources import framing
from gxw.lossless import inspect_project
from gxw.container_writer import replace_project_stream, validate_cfb_streams
from gxw.project_metadata import logical_mapping, synchronize_history
from gxw.token_pou import parse_token_fragment
from replay_gxw_workspace import run


def compiled_graph(raw):
    layout = framing(raw)
    start, size = layout['graph']['offset'], layout['graph']['size']
    if struct.unpack_from('<II', raw, start + 4) != (0, 0):
        raise ValueError('only observed converted SFC graph storage is supported')
    block_size = struct.unpack_from('<I', raw, start + 12)[0]
    if block_size < 4 or 12 + block_size + 4 > size:
        raise ValueError('invalid converted SFC code extent')
    code_start, code_size = start + 16, block_size - 4
    cache_start = code_start + code_size
    cache_size = struct.unpack_from('<I', raw, cache_start)[0]
    if cache_size < 4 or cache_start + cache_size != start + size:
        raise ValueError('unsupported SFC opaque cache extent')
    tokens = parse_token_fragment(raw, code_start, code_size)
    return dict(code_offset=code_start, code_size=code_size,
                tokens=[dict(offset=t.offset, raw=t.raw.hex()) for t in tokens.tokens],
                cache_offset=cache_start, cache_size=cache_size,
                cache_sha256=hashlib.sha256(raw[cache_start:cache_start + cache_size]).hexdigest())


def patch_raw(source, logical, old, new):
    outer = validate_cfb_streams(source)
    nested = validate_cfb_streams(outer['_hdb'])
    physical = logical_mapping(outer['projectdatalist.xml'])[logical]
    if nested[physical] != old:
        raise ValueError('source binding mismatch')
    history, changes, preserved = synchronize_history(outer['history.xml'], {logical:(physical,old,new)})
    hdb, _ = replace_project_stream(outer['_hdb'],physical,new)
    updated, _ = replace_project_stream(source,'_hdb',hdb)
    updated, _ = replace_project_stream(updated,'history.xml',history)
    assert validate_cfb_streams(hdb) == dict(nested, **{physical:new})
    assert validate_cfb_streams(updated) == dict(outer, _hdb=hdb, **{'history.xml':history})
    return updated, dict(metadata_changes=changes, preserved_metadata=preserved,
                         unrelated_payloads='byte-identical')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('seed',type=Path)
    ap.add_argument('output',type=Path)
    args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    source=args.seed.read_bytes()
    seeds=[s for s in inspect_project(source).streams if s.logical_name and s.logical_name.endswith('.Program.pou')]
    if len(seeds)!=1:raise ValueError('probe requires one native SFC block')
    seed=seeds[0];graph=compiled_graph(seed.raw);layout=framing(seed.raw)
    step=[t for t in graph['tokens'] if t['raw']=='04980a04']
    jump=[t for t in graph['tokens'] if t['raw']=='04980004']
    action=[a for a in layout['actions'] if a['number']==10]
    if len(step)!=1 or len(jump)!=3 or len(action)!=1:raise ValueError('seed graph differs from inspected control')
    cases=[('step20-graph-only',[(step[0]['offset']+2,b'\x14')]),
           ('step20-with-action',[(step[0]['offset']+2,b'\x14'),(action[0]['number_offset'],struct.pack('<I',20))]),
           ('jump-to-step10',[(jump[-1]['offset']+2,b'\x01')]),
           ('invalid-jump-index2',[(jump[-1]['offset']+2,b'\x02')])]
    with (args.output/'cases.jsonl').open('w') as log:
        for name,changes in cases:
            directory=args.output/name;directory.mkdir()
            new=bytearray(seed.raw)
            for offset,data in changes:new[offset:offset+len(data)]=data
            new=bytes(new)
            after=compiled_graph(new)
            assert after['cache_sha256']==graph['cache_sha256']
            patched,evidence=patch_raw(source,seed.logical_name,seed.raw,new)
            path=directory/'patched.gxw';path.write_bytes(patched)
            evidence.update(graph_before=graph,graph_after=after,
                raw_changes=[dict(offset=o,old=seed.raw[o:o+len(d)].hex(),new=d.hex()) for o,d in changes])
            (directory/'mutation.json').write_text(json.dumps(evidence,indent=2)+'\n')
            outcome=run(path,directory/'native',compile=True,change_sfc=True,program_check=True,
                        snapshot_frontend=True,export_project=True)
            row=dict(case=name,outcome=outcome)
            log.write(json.dumps(row)+'\n');log.flush()
            print(name,'open',outcome['open_succeeded'],'rejected',outcome['compiler_rejected'],
                  'diagnostics',[(d['code'],d['name']) for d in outcome['diagnostics']],flush=True)


if __name__=='__main__':main()
