"""Read bounded per-call connections from preserved simple-ladder FB fragments.

Native assignment/xref addresses can reflect the last call to an instance.
This probe instead decodes each exact source fragment through the native token
converter. Only observed two-instruction BOOL, 16-bit, 32-bit, and string wiring templates are
projected. Everything else retains its bytes, offsets, records, and explicit
gap; this is not a general ladder evaluator or complete dependency graph.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from gxw.lossless import inspect_project,sha256
from native_gxw_tokens import native_batch,native_il_projection,Q_DLL,DEFAULT_DLL
from probe_gxw_native_listing import project_text_context
from probe_gxw_simple_fb import frame_source,observe_calls


def observed_bindings(records: list[dict], ports: list[dict], direction: str) -> dict:
    """Recognize entire two-record groups; no skipped instruction is inferred."""
    formals={"'_"+p['name']:p for p in ports if p['direction']==direction}
    bindings,opaque=[],[]
    index=0
    while index<len(records):
        end=index+1
        while end<len(records) and not (records[end].get('kind')=='instruction' and records[end].get('op')=='LD'):
            end+=1
        group=records[index:end]
        binding=None
        if len(group)==2 and all(r.get('kind')=='instruction' for r in group):
            first,second=group
            if first['op']=='LD' and len(first['args'])==1:
                if second['op']=='OUT' and len(second['args'])==1:
                    pin,operand=(second['args'][0],first['args'][0]) if direction=='input' else (first['args'][0],second['args'][0])
                    if pin in formals and formals[pin]['type_marker']=='B':
                        binding=dict(port=formals[pin]['name'],operand=operand,template='LD-OUT',type_marker='B')
                elif first['args']==["'_TRUE"] and second['op'] in {'MOV','DMOV','$MOV'} and len(second['args'])==2:
                    pin,operand=(second['args'][1],second['args'][0]) if direction=='input' else (second['args'][0],second['args'][1])
                    marker={'MOV':'W','DMOV':'D','$MOV':'S'}[second['op']]
                    if pin in formals and formals[pin]['type_marker']==marker:
                        binding=dict(port=formals[pin]['name'],operand=operand,
                                     template='TRUE-'+second['op'],type_marker=marker)
        if binding is None:
            opaque.append(dict(record_start=index,record_end=end,records=group,handling='opaque-preserved'))
        else:
            bindings.append(dict(binding,direction=direction,record_start=index,record_end=end,
                                 handling='native-source-template-observed'))
        index=end
    return dict(bindings=bindings,uninterpreted_groups=opaque)


def inspect(source:bytes,directory:Path)->dict:
    directory=directory.resolve()
    directory.mkdir(parents=True,exist_ok=False)
    image=inspect_project(source)
    if image.diagnostics or any(s.error for s in image.streams):raise ValueError('incomplete container inventory')
    streams={s.logical_name:s.raw for s in image.streams if s.logical_name}
    context=project_text_context(next(raw for name,raw in streams.items() if name.endswith('.prj')))
    profile={'Q03UDV':(Q_DLL,209),'FX3U/FX3UC':(DEFAULT_DLL,0x208),'FX1S':(DEFAULT_DLL,0x206)}.get(context['cpu'])
    if profile is None or context['text_encoding'] is None:raise ValueError('CPU/codepage outside observed native profile')
    result=dict(source_sha256=sha256(source),context=context,programs=[],complete_dependency_proof=False)
    requests,fragments=[],[]
    for name,raw in streams.items():
        if not name.endswith('.Program.pou'):continue
        item=dict(name=name,sha256=sha256(raw),handling='opaque-preserved')
        result['programs'].append(item)
        try:
            program=frame_source(raw)
        except ValueError as exc:
            item['diagnostic']=str(exc);continue
        item.update(observe_calls(program),handling='partially-decoded')
        for call in item['calls']:
            call['connection_fragments']={}
            for direction in ['input','output']:
                span=call[direction+'_fragment']
                blob=raw[span['offset']:span['offset']+span['length']]
                fragment=dict(span,raw_base64=base64.b64encode(blob).decode())
                call['connection_fragments'][direction]=fragment
                if call['gaps']:
                    fragment['diagnostic']='unresolved FB marker structure; connection semantics retained as opaque'
                    continue
                if not blob:
                    fragment.update(handling='empty',bindings=[],uninterpreted_groups=[]);continue
                if len(blob)+1>32768:
                    fragment['diagnostic']='outside native token converter input bound';continue
                requests.append(dict(mode='decode',input_base64=base64.b64encode(blob+b'\0').decode(),
                                     program=name,call_offset=call['offset'],direction=direction))
                fragments.append((call,direction,fragment))
    if requests:
        answers=native_batch(requests,directory/'native-token-decode',dll=profile[0],cpu_code=profile[1])
        for (call,direction,fragment),answer in zip(fragments,answers):
            fragment.update(native_return_code=answer['return_code'],native_consumed_bytes=answer['consumed_bytes'])
            if answer['return_code']!='0x00000000' or answer['consumed_bytes']!=fragment['length']:
                fragment['diagnostic']='native token decoder rejected or did not consume the fragment';continue
            projection=native_il_projection(base64.b64decode(answer['output_base64']),encoding=context['text_encoding'])
            fragment.update(projection)
            if projection['text_gaps']:
                fragment['diagnostic']='native text has undecoded regions';continue
            fragment.update(observed_bindings(projection['records'],call['ports'],direction))
    for program in result['programs']:
        for call in program.get('calls',[]):
            for port in call['ports']:
                fragment=call['connection_fragments'][port['direction']]
                port['observed_binding_count']=sum(b['port']==port['name'] for b in fragment.get('bindings',[]))
                port['connection_handling']=('native-source-template-observed' if port['observed_binding_count']==1 else
                    'multiple-observed-bindings' if port['observed_binding_count'] else 'no-binding-decoded')
    result['summary']=dict(calls=sum(len(p.get('calls',[])) for p in result['programs']),
        bindings=sum(len(f.get('bindings',[])) for p in result['programs'] for c in p.get('calls',[]) for f in c['connection_fragments'].values()),
        uninterpreted_groups=sum(len(f.get('uninterpreted_groups',[])) for p in result['programs'] for c in p.get('calls',[]) for f in c['connection_fragments'].values()))
    (directory/'connections.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    print(json.dumps(inspect(args.source.read_bytes(),args.output)['summary']))
