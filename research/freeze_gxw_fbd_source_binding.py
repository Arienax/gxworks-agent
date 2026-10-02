"""Freeze the source-bound FBD interface milestone and length/binding failures."""
from __future__ import annotations

from collections import defaultdict
import base64
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
P = ROOT/'research/experiments/sfc-graph-20260926/public-corpus-discovery'
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'research'),str(P)]
from source_fbd_interfaces import SourceGraph
from probe_gxw_library_archive import observed_source_declarations
from gxw.container_writer import validate_cfb_streams
from gxw.project_metadata import current_rows


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def encoded(raw):
    return base64.b64encode(raw).decode('ascii')


def archived(path):
    return 'experiments/'+path.relative_to(P).as_posix()


def snapshot(raw, logical='1.Program.pou'):
    source=SourceGraph(raw,logical)
    names=set()
    for node in source.program.nodes:
        if node.kind_code in (1,2):
            name=node.type_name if node.kind_code==2 else node.symbol
            key=name.casefold()
            if key not in source.catalog and node.kind_code==1:
                key=re.sub(r'-[1-9][0-9]*$','',key)
            names.add(key)
    definitions=[]
    for key in sorted(names):
        for stream,d in source.catalog.get(key,[]):
            decl=observed_source_declarations(d)
            end=max([d['raw'].find(b'\n')+1, *[r['end'] for r in decl['rows']],
                     *[r['offset']+len(r['raw']) for r in decl['transitions']]])
            excerpt=d['raw'][:end]
            if observed_source_declarations(dict(d,raw=excerpt))!=decl:
                raise ValueError('declaration prefix no longer preserves source framing')
            definitions.append(dict(source_stream=stream,name=d['name'],kind=d['kind'],
                return_type_raw=d['return_type_raw'],offset=d['offset'],sections=d['sections'],
                declaration_prefix_base64=encoded(excerpt),
                scope='Exact original definition prefix through declarations; full original .lif retained locally'))
    return dict(cpu=source.cpu,library_cpu=source.library_cpu,logical=logical,
                program_base64=encoded(source.streams[logical]),
                declarations={k:encoded(v.raw) for k,v in source.declarations.items()},
                definitions=definitions,missing_logical_objects=source.missing_logical_objects,
                final_source_view=source.view())


def archive(path, entries):
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED) as output:
        for name,value in sorted(entries.items()):
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('archive entry escapes root')
            output.writestr(name,value)
    with zipfile.ZipFile(path) as output:
        if any(output.read(k)!=v for k,v in entries.items()):
            raise ValueError('archive bytes differ')
    return dict(entries=len(entries),bytes=path.stat().st_size,entry_bytes_exact=True)


def main():
    public=ROOT/'research/evidence/gxw-fbd-source-binding-20261001.zip'
    local=P/'gxw-fbd-source-binding-20261001-local.zip'
    result=ROOT/'research/results/gxw-fbd-source-binding-20261001.json'
    if any(x.exists() for x in (public,local,result)):
        raise FileExistsError('Frozen evidence must not be replaced')
    manifest=dict(interfaces=[],controls=[],size_recoveries=[],
                  scope='FX3U/CPU520 source graph, source declarations and CPU-selected source library interfaces; no execution effects inferred')
    private={}
    def keep(path):
        if not path.is_file():
            raise FileNotFoundError(path)
        key=archived(path)
        value=path.read_bytes()
        if key in private and private[key]!=value:
            raise ValueError('local evidence changed during freeze')
        private[key]=value
        return key
    fixture=read(ROOT/'tests/fixtures/gxw_callable_ports.json')
    grouped=defaultdict(list)
    for native in fixture['interfaces']:
        grouped[native['case']].append(native)
    for case,witnesses in grouped.items():
        directory=P/case
        path=directory/'source.gxw'
        if not path.exists():
            path=directory/'changed.gxw'
        projected=snapshot(path.read_bytes())
        comparison=read(directory/'comparison.json')
        native=directory/Path(comparison['native_directory']).name
        traces=[json.loads(l).get('payload',{}) for l in (native/'events.jsonl').read_text().splitlines()]
        formals=[e for e in traces if e.get('event')=='native-callable-formals']
        observations=[]
        for witness in witnesses:
            matching=[e for e in formals if e['node'] and e['node']['symbol']==witness['symbol']]
            if not matching:
                raise ValueError('retained independent native formal observation missing')
            first=matching[0]
            keys=('name','class_code','type_code')
            actual=[{k:p[k] for k in keys} for p in first['formals']]
            if actual!=witness['expected']:
                raise ValueError('fixture differs from original native formal trace')
            observations.append(dict(symbol=witness['symbol'],formals=actual,
                                     sorted_ports=first['node']['ports']))
        projected.update(case=case,native=observations,local_source=keep(path))
        manifest['interfaces'].append(projected)
        for file in (directory/'comparison.json',native/'events.jsonl',native/'native-events.jsonl',native/'frontend-snapshot.json'):
            keep(file)
    successful=[('v1','original'),('v1','rename-instance'),('v2','grow-instance'),('v2','named-port-bend'),
                ('v2','named-global-terminal'),('v2','global-instance'),('v2','inout-output-binding')]
    rejected=[('v2','rename-label-only'),('v2','rename-graph-only'),('v2','declaration-type-conflict'),('v2','local-shadows-global')]
    for version,case in successful+rejected:
        directory=P/('fbd-label-graph-binding-20261001-'+version)/case
        comparison=read(directory/'comparison.json')
        native=Path(comparison['native_directory']) if 'native_directory' in comparison else directory/'native'
        row=snapshot((directory/'source.gxw').read_bytes())
        row.update(case=case,version=version,local_source=archived(directory/'source.gxw'),
                   accepted=(version,case) in successful,
                   **{k:comparison[k] for k in ('input_unchanged','failures','diagnostics','checks','pcode')})
        if row['accepted']:
            saved=native/'native-saved.gxw'
            row['saved_sources']=snapshot(saved.read_bytes())
            reopened=directory/('reopen-'+native.name[7:]) if native.name.startswith('native-') else directory/'reopen'
            row['reopened_pcode']={x.name:x.read_bytes().hex() for x in reopened.glob('pcode-*-*.bin')}
            row['reopen']=comparison['reopen']
        manifest['controls'].append(row)
    roots=[P/'fbd-source-interfaces-20261001-v1',P/'fbd-source-interfaces-20261001-v2',
           P/'fbd-label-graph-binding-20261001-v1',P/'fbd-label-graph-binding-20261001-v2']
    for directory in roots:
        for path in directory.rglob('*'):
            if path.is_file():
                keep(path)
    for name in ('source_fbd_interfaces.py','crosscheck_fbd_source_interfaces.py','probe_fbd_label_graph_binding.py',
                 'probe_fx1s_lossless_project_patch.py','probe_fx_operand_rules.py','TraceCallableFormals.js'):
        keep(P/name)
    for case in ('grow-instance','named-port-bend','named-global-terminal','global-instance','inout-output-binding'):
        a=P/'fbd-label-graph-binding-20261001-v1'/case
        b=P/'fbd-label-graph-binding-20261001-v2'/case
        left=validate_cfb_streams((a/'source.gxw').read_bytes())
        right=validate_cfb_streams((b/'source.gxw').read_bytes())
        same={k:v for k,v in left.items() if k!='history.xml'}=={k:v for k,v in right.items() if k!='history.xml'}
        lr,_=current_rows(left['history.xml'],'DSHISTORY','D_History')
        rr,_=current_rows(right['history.xml'],'DSHISTORY','D_History')
        unchanged=[{k:v.text for k,v in r.fields().items() if k!='iFileSize'} for r in lr]==[
            {k:v.text for k,v in r.fields().items() if k!='iFileSize'} for r in rr]
        before=read(a/'comparison.json');after=read(b/'comparison.json')
        old=snapshot((a/'source.gxw').read_bytes());new=snapshot((b/'source.gxw').read_bytes())
        manifest['size_recoveries'].append(dict(case=case,local_before=archived(a/'source.gxw'),local_after=archived(b/'source.gxw'),
            source_program_before=old['program_base64'],source_program_after=new['program_base64'],
            source_declarations_before=old['declarations'],source_declarations_after=new['declarations'],
            only_history_size_fields_changed=same and unchanged,failed_before=not before['exported'],accepted_after=after['exported'],
            original_failure_diagnostics=before['diagnostics'],original_failure_operations=before['failures']))
    public_entries={'witnesses.json':(json.dumps(manifest,indent=2)+'\n').encode()}
    for name in ('models','source_header','container','declarations','connectivity','project_metadata',
                 'project_resolver','structured_pou','structured_pou_writer'):
        public_entries['tools/gxw/'+name+'.py']=(ROOT/'src/gxw'/ (name+'.py')).read_bytes()
    public_entries['tools/source_fbd_interfaces.py']=(P/'source_fbd_interfaces.py').read_bytes()
    public_entries['tools/probe_gxw_library_archive.py']=(ROOT/'research/probe_gxw_library_archive.py').read_bytes()
    for name in ('freeze_gxw_fbd_source_binding.py','validate_gxw_fbd_source_binding.py'):
        public_entries['tools/'+name]=(ROOT/'research'/name).read_bytes()
    public_info=archive(public,public_entries)
    private_info=archive(local,private)
    from validate_gxw_fbd_source_binding import replay
    cold_public=replay(public)
    cold_local=replay(public,local)
    summary=dict(schema=1,date='2026-10-01',status='native-validated/research-prototype',
                 versions=dict(plc='FX3U/FX3UC',cpu=520,compiler_adapter='1.635.0.1',sic_converter='1.635.0.1'),
                 counts=cold_local,public_cold_replay=cold_public,
                 interface_scope='Project source .lif declaration prefixes, actual source label bindings and sorted graph ports; no native ANSI declaration input',
                 native_edits=[dict(case=r['case'],accepted=r['accepted']) for r in manifest['controls']],
                 findings=[
                     'Callable port binding uses geometric side and ordinate; original serialized port order and fixed row numbers are insufficient.',
                     'IN_OUT has separate input/output endpoints; extensible input multiplicity and compiler EN/ENO come from source declarations.',
                     'FB graph instance name/type must agree with a source declaration. Missing or inconsistent labels yield empty native formal interfaces and compiler rejection.',
                     'A global FB instance and a mapped global BOOL terminal both compile/save/reopen. A local/global duplicate name is rejected in this observed FX3U profile.',
                     'Five growth failures recover when only history iFileSize fields change. All other history fields and stream payloads are identical across each pair.',
                     'Native source conversion receives truncated/zero graph records with stale lengths. Length-only recovery restores the proposed nodes and wire bends.',
                 ],
                 retained_failures=[
                     'Initial source binder omitted function port flags; its 23/38 partial result is retained.',
                     'Initial node type-only prediction ignored missing/mismatched instance declarations; native empty formal interfaces are retained.',
                     'Original and length-only recovered growth proposals retain failed open, malformed graph/declaration observations and diagnostics.',
                     'Local/global duplicate-name rejection and temporary native-directory collisions are retained.',
                 ],
                 limits=[
                     'Native complete-project writeback controls are owned FX3U examples, not Q/L or independent real-world FBD coverage.',
                     'The reader currently handles source .lif definitions. Separate user-library .lnl interfaces remain unsupported.',
                     'Formal binding does not establish execution or IN_OUT copyback effects; unknown flags, types and geometry remain unsupported.',
                     'Six byte-identical complete compiled bodies cover baseline, two instance renames, wire bend, global terminal and global instance. The IN_OUT output control intentionally changes code.',
                     'Public cold replay validates bounded source/native witnesses. Full input projects and all native processes require the paired local archive.',
                     'No product parser/writer or MCP integration is claimed.',
                 ],
                 archives=dict(public={'path':str(public.relative_to(ROOT)),**public_info},local={'path':str(local),**private_info}))
    result.write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(dict(counts=cold_local,public=public_info,local=private_info),indent=2))


if __name__=='__main__':
    main()
