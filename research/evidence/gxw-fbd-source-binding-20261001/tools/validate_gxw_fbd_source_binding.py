"""Cold replay of frozen FBD source interfaces and native writeback witnesses.

No native DLL, digest gate or installed PLC tool is required. Public projections
retain bounded source bytes; the optional local archive also retains full GXW.
"""
from __future__ import annotations

from collections import defaultdict
import argparse
import base64
import json
from pathlib import Path
import sys
import types
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def model(archive):
    package = types.ModuleType('_frozen_fbd_gxw')
    package.__path__ = []
    sys.modules[package.__name__] = package
    modules = {}
    def execute(name, entry, substitutions=()):
        module = types.ModuleType(name)
        module.__file__ = entry
        sys.modules[name] = module
        source = archive.read(entry).decode('utf-8')
        for before, after in substitutions:
            source = source.replace(before, after)
        exec(compile(source, entry, 'exec'), module.__dict__)
        return module
    for name in ('models','source_header','container','declarations','connectivity',
                 'project_metadata','project_resolver','structured_pou','structured_pou_writer'):
        modules[name] = execute(package.__name__+'.'+name, 'tools/gxw/'+name+'.py')
    execute('_frozen_fbd_library_archive', 'tools/probe_gxw_library_archive.py')
    source = execute('_frozen_fbd_source', 'tools/source_fbd_interfaces.py', (
        ('from gxw.', 'from '+package.__name__+'.'),
        ('from probe_gxw_library_archive import', 'from _frozen_fbd_library_archive import'),
    ))
    return source, modules


def reader(witness, source, modules):
    result = source.SourceGraph.__new__(source.SourceGraph)
    result.cpu, result.library_cpu = witness['cpu'], witness['library_cpu']
    result.program = modules['structured_pou'].parse_structured_pou(
        base64.b64decode(witness['program_base64']), logical_name=witness['logical'],
        preserve_unsupported_records=True)
    result.declarations = {k:modules['declarations'].parse_declarations(base64.b64decode(v),logical_name=k)
                           for k,v in witness['declarations'].items()}
    result.catalog = defaultdict(list)
    result.library_gaps = []
    result.missing_logical_objects = witness['missing_logical_objects']
    for d in witness['definitions']:
        definition = {**d, 'raw':base64.b64decode(d['declaration_prefix_base64'])}
        result.catalog[d['name'].casefold()].append((d['source_stream'],definition))
    return result


def replay(public, local=None):
    with zipfile.ZipFile(public) as archive:
        source, modules = model(archive)
        manifest = json.loads(archive.read('witnesses.json'))
        counts = dict(interface_projects=0, source_native_interfaces_exact=0,
                      native_accepted_projects=0, native_rejected_controls=0,
                      saved_node_wire_label_records_exact=0, reopened_code_sets_exact=0,
                      unchanged_compiled_bodies=0, size_only_recoveries=0,
                      full_local_inputs_replayed=0, unrelated_payload_sets_exact=0)
        for witness in manifest['interfaces']:
            selected = reader(witness,source,modules)
            for observed in witness['native']:
                node, = [n for n in selected.program.nodes if n.symbol==observed['symbol']]
                predicted = selected.callable(node)
                keys = ('name','class_code','type_code')
                if [{k:p[k] for k in keys} for p in predicted['ports']] != observed['formals']:
                    raise ValueError('source/native formal mismatch: '+witness['case'])
                native_ports = [p.raw.hex() for p in sorted(node.ports, key=lambda p:(p.local_x,p.local_y))]
                if observed['sorted_ports'] != native_ports:
                    raise ValueError('source/native graph port ordering differs')
                counts['source_native_interfaces_exact'] += 1
            counts['interface_projects'] += 1
        original = bytes.fromhex(manifest['controls'][0]['pcode']['pcode-0-0.bin'])
        for witness in manifest['controls']:
            selected = reader(witness,source,modules)
            if selected.view() != witness['final_source_view']:
                raise ValueError('source graph/declaration view differs: '+witness['case'])
            if witness['accepted']:
                if (not witness['input_unchanged'] or witness['failures'] or witness['diagnostics']
                        or not witness['checks'] or any(r['rejected'] or r['targets']!=1 for r in witness['checks'])):
                    raise ValueError('accepted control has incomplete native check')
                saved = reader(witness['saved_sources'],source,modules)
                if ([modules['structured_pou_writer'].serialize_structured_node(n) for n in selected.program.nodes]
                        != [modules['structured_pou_writer'].serialize_structured_node(n) for n in saved.program.nodes]
                    or [(w.start,w.end,w.prefix_fields,w.suffix) for w in selected.program.wires]
                        != [(w.start,w.end,w.prefix_fields,w.suffix) for w in saved.program.wires]
                    or {k:[r.raw for r in d.rows] for k,d in selected.declarations.items()}
                        != {k:[r.raw for r in d.rows] for k,d in saved.declarations.items()}):
                    raise ValueError('saved graph or declarations differ')
                if witness['pcode'] != witness['reopened_pcode']:
                    raise ValueError('reopened native code differs')
                if witness['reopen']['failures'] or witness['reopen']['compiler_rejected']:
                    raise ValueError('reopened compile did not complete cleanly')
                counts['native_accepted_projects'] += 1
                counts['saved_node_wire_label_records_exact'] += 1
                counts['reopened_code_sets_exact'] += 1
                counts['unchanged_compiled_bodies'] += bytes.fromhex(witness['pcode']['pcode-0-0.bin'])==original
            else:
                if not witness['diagnostics'] or not selected.view()['gaps']:
                    raise ValueError('rejected label binding is no longer preserved')
                counts['native_rejected_controls'] += 1
        for pair in manifest['size_recoveries']:
            if (pair['source_program_before'] != pair['source_program_after']
                    or pair['source_declarations_before'] != pair['source_declarations_after']
                    or not pair['only_history_size_fields_changed'] or not pair['failed_before']
                    or not pair['accepted_after']):
                raise ValueError('length-only recovery control is incomplete')
            counts['size_only_recoveries'] += 1
        if local:
            with zipfile.ZipFile(local) as complete:
                for witness in manifest['interfaces']+manifest['controls']:
                    raw = complete.read(witness['local_source'])
                    bound = source.SourceGraph(raw,witness['logical'])
                    if bound.view()!=reader(witness,source,modules).view():
                        raise ValueError('full project differs from public source projection')
                    counts['full_local_inputs_replayed'] += 1
                for pair in manifest['size_recoveries']:
                    before = complete.read(pair['local_before'])
                    after = complete.read(pair['local_after'])
                    a = modules['container'].CompoundFile(before)
                    b = modules['container'].CompoundFile(after)
                    a_streams={e.name:a.read_entry(e) for e in a.iter_streams()}
                    b_streams={e.name:b.read_entry(e) for e in b.iter_streams()}
                    if {k:v for k,v in a_streams.items() if k!='history.xml'} != {
                            k:v for k,v in b_streams.items() if k!='history.xml'}:
                        raise ValueError('recovery changed bytes beyond history stream')
                    left,enc = modules['project_metadata'].current_rows(a_streams['history.xml'],'DSHISTORY','D_History')
                    right,_ = modules['project_metadata'].current_rows(b_streams['history.xml'],'DSHISTORY','D_History')
                    rows_a=[{k:v.text for k,v in r.fields().items() if k!='iFileSize'} for r in left]
                    rows_b=[{k:v.text for k,v in r.fields().items() if k!='iFileSize'} for r in right]
                    if rows_a!=rows_b:
                        raise ValueError('recovery changed non-length history fields')
                    counts['unrelated_payload_sets_exact'] += 1
        return counts


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public',type=Path,default=ROOT/'research/evidence/gxw-fbd-source-binding-20261001.zip')
    parser.add_argument('--local',type=Path)
    args=parser.parse_args()
    print(json.dumps(replay(args.public,args.local),indent=2))
