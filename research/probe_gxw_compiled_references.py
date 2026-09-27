"""Correlate preserved label-bearing source records with native compiled regions.

This uses a hash-bound successful offline build, stored debug maps, and an
independent native token decoder. It distinguishes repeated FB expansions that
the native global assignment/xref view can collapse to the last call's address.
It reports groups of operands rather than guessing one-to-one substitutions,
read/write roles, execution reachability, or semantics for unsupported code.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import json
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from gxw.compiler_debug import parse_compiler_debug
from gxw.lossless import inspect_project,sha256
from gxw.models import StructuredNode,StructuredWire
from gxw.structured_pou import parse_structured_pou
from gxw.token_pou import frame_token_pou,parse_token_fragment
from native_gxw_tokens import native_batch,native_il_projection,Q_DLL,DEFAULT_DLL
from probe_gxw_native_listing import project_text_context
from probe_gxw_debug_locations import search_maps
from probe_gxw_st_sources import replace_observed_st
from probe_gxw_library_archive import decode_library_archive,frame_library_text,observed_source_body


def observed_st_lines(raw: bytes) -> list[dict]:
    """Retain physical UTF-16 lines; debug points are not statement spans."""
    _,text=replace_observed_st(raw,'')
    lines=[];offset=71
    for match in re.finditer(r'[^\r\n]*(?:\r\n|\r|\n|$)',text):
        if match.start()==len(text) and text and not text.endswith(('\r','\n')):continue
        line=match.group();encoded=line.encode('utf-16le')
        lines.append(dict(line_index=len(lines),offset=offset,text=line.rstrip('\r\n'),
                          raw_base64=base64.b64encode(encoded).decode()))
        offset+=len(encoded)
    if offset!=len(raw)-30:raise ValueError('ST line framing does not cover preserved text')
    return lines


def observed_graph_blocks(raw: bytes, name: str) -> list[dict]:
    """Stored block membership, not inferred rung or per-node instruction spans."""
    program=parse_structured_pou(raw,logical_name=name,preserve_unsupported_records=True)
    blocks=[]
    for index,(block,records) in enumerate(program.block_records()):
        items=[]
        for item in records:
            row=dict(offset=item.offset,raw_base64=base64.b64encode(item.raw).decode(),handling='opaque-preserved')
            if isinstance(item,StructuredNode):
                row.update(handling='bounded-graph-node',kind_code=item.kind_code,kind=item.kind.value,
                    symbol=item.symbol,type_name=item.type_name,
                    bbox=[item.bbox.left,item.bbox.top,item.bbox.right,item.bbox.bottom])
            elif isinstance(item,StructuredWire):
                row.update(handling='bounded-graph-wire',start=[item.start.x,item.start.y],end=[item.end.x,item.end.y])
            items.append(row)
        blocks.append(dict(network=index+1,offset=block.offset,byte_length=block.byte_length,
            raw_header_base64=base64.b64encode(block.raw_header).decode(),records=items))
    return blocks


def stored_records(frame, *, encoding: str) -> tuple[list[dict],int]:
    """Q GetStepSize 0x61ab/0x17e9/0x2a533; widths are stored, not recomputed.

    The native primitive groups tokens below 0x90 with following operands.
    Text/FB marker widths contribute as stored; no instruction table is used.
    A stored width is not proof of instruction validity or machine-code size.
    """
    groups=[];step=0
    for token in frame.tokens:
        raw=token.raw
        if len(raw)==2 or raw[1]<0x90:
            width=1 if len(raw)<=3 else int.from_bytes(raw[2:3],'little',signed=True)
            if width<0:raise ValueError('negative stored width; absolute positions retained as unknown')
            groups.append(dict(offset=token.offset,stored_step=step,stored_width=width,raw=raw,labels=[]))
            step+=width
        else:
            if not groups:raise ValueError('operand before a bounded source record')
            groups[-1]['raw']+=raw
            if raw[1]==0xdf:
                try:label=raw[2:-1].decode(encoding)
                except UnicodeDecodeError:label=None
                groups[-1]['labels'].append(dict(offset=token.offset,name=label,raw_hex=raw.hex(),
                    handling='decoded-text' if label is not None else 'opaque-preserved'))
    return groups,step


def inspect(native_directory: Path, output: Path) -> dict:
    native_directory=native_directory.resolve();output=output.resolve()
    outcome=json.loads((native_directory/'outcome.json').read_text(encoding='utf-8'))
    if not outcome.get('compile_completed') or outcome.get('compiler_rejected') or outcome.get('returncode')!=0:
        raise ValueError('requires a successful native offline build')
    export=outcome.get('native_export',{})
    if export.get('file')!='native-saved.gxw':raise ValueError('native export is missing')
    source=(native_directory/export['file']).read_bytes()
    if sha256(source)!=export.get('sha256'):raise ValueError('native export hash differs')
    image=inspect_project(source)
    if image.diagnostics or any(s.error for s in image.streams):raise ValueError('incomplete container inventory')
    streams={s.logical_name:s.raw for s in image.streams if s.logical_name}
    context=project_text_context(next(r for n,r in streams.items() if n.endswith('.prj')))
    encoding=context['text_encoding']
    profile={'Q03UDV':(Q_DLL,209),'FX3U/FX3UC':(DEFAULT_DLL,0x208),'FX1S':(DEFAULT_DLL,0x206)}.get(context['cpu'])
    if profile is None or encoding is None:raise ValueError('native CPU/codepage outside observed profile')
    debug=parse_compiler_debug(streams['DebugInformation2.dat'])
    indexes=search_maps(debug,encoding=encoding)
    events=[json.loads(s) for s in (native_directory/'native-events.jsonl').read_text(encoding='utf-8').splitlines()]
    resources={}
    for event in events:
        if event['operation']!='Resource':continue
        filename='pcode-'+str(event['index'])+'-0.bin'
        binary=(native_directory/filename).read_bytes()
        if sha256(binary)!=outcome['outputs'][filename]['sha256']:raise ValueError('native PCode hash differs')
        if event['name'] in resources:raise ValueError('ambiguous native resource name')
        groups,total=stored_records(parse_token_fragment(binary,0,len(binary)),encoding=encoding)
        resources[event['name']]=dict(file=filename,sha256=sha256(binary),groups=groups,stored_steps=total)
    output.mkdir(parents=True,exist_ok=False)
    result=dict(export_sha256=sha256(source),context=context,debug_sha256=sha256(debug.raw),
        programs=[],expansions=[],resources=[{k:v for k,v in r.items() if k!='groups'}|dict(name=n) for n,r in resources.items()],
        complete_semantic_proof=False)
    embedded_libraries={}
    result['embedded_library_streams']=[]
    section={'Q03UDV':'Q03UDV','FX3U/FX3UC':'FX3U','FX1S':'FX1S'}.get(context['cpu'])
    for name,raw in streams.items():
        if name.lower() not in ('iec.lif','iecfunction.lif'):continue
        entry=dict(logical_name=name,sha256=sha256(raw),raw_base64=base64.b64encode(raw).decode(),handling='opaque-preserved')
        result['embedded_library_streams'].append(entry)
        try:
            if len(raw)<24 or int.from_bytes(raw[:4],'little')!=20:raise ValueError('unobserved LIF metadata envelope')
            archive=decode_library_archive(raw[20:]);framed=frame_library_text(archive.decoded)
            entry.update(handling='decoded-library-source',decoded_sha256=sha256(archive.decoded),
                decoded_bytes=len(archive.decoded),metadata_raw_base64=base64.b64encode(raw[:20]).decode())
            for definition in framed['definitions']:
                if section is not None and (definition['sections'] is None or section in definition['sections']):
                    embedded_libraries.setdefault(('@IEC',definition['name']),[]).append((entry,definition))
        except ValueError as exc:entry['diagnostic']=str(exc)
    requests=[];dedup={}
    def record(group):
        raw=group['raw'];key=sha256(raw)
        item={k:v for k,v in group.items() if k!='raw'}
        item.update(raw_base64=base64.b64encode(raw).decode(),sha256=key)
        if len(raw)+1>32768:item['handling']='outside-native-decode-bound';return item
        if key not in dedup:
            dedup[key]=len(requests)
            requests.append(dict(mode='decode',input_base64=base64.b64encode(raw+b'\0').decode(),fragment_sha256=key))
        item['decode_request']=dedup[key]
        return item
    programs={};st_programs={};graph_programs={};seen=set()
    for name,raw in streams.items():
        if not name.endswith('.Program.pou'):continue
        item=dict(name=name,sha256=sha256(raw),handling='opaque-preserved')
        result['programs'].append(item)
        if len(raw)>54 and raw[54]==208:
            try:blocks=observed_graph_blocks(raw,name)
            except ValueError as exc:item.update(diagnostic=str(exc),raw_base64=base64.b64encode(raw).decode());continue
            item.update(handling='bounded-graph-blocks',blocks=blocks,raw_base64=base64.b64encode(raw).decode(),
                coordinate_meaning='one-based stored block order; node and wire coordinates are local to the block')
            graph_programs[name]=item
            continue
        if len(raw)>54 and raw[54]==193:
            try:lines=observed_st_lines(raw)
            except ValueError as exc:item.update(diagnostic=str(exc),raw_base64=base64.b64encode(raw).decode());continue
            item.update(handling='bounded-ST-physical-lines',lines=lines,raw_base64=base64.b64encode(raw).decode(),
                coordinate_meaning='zero-based physical source point; multiline statement extent is not inferred')
            st_programs[name]=item
            continue
        try:groups,total=stored_records(frame_token_pou(raw),encoding=encoding)
        except ValueError as exc:item.update(diagnostic=str(exc),raw_base64=base64.b64encode(raw).decode());continue
        item.update(handling='bounded-token-records',stored_steps=total,records=[record(g) for g in groups if g['labels']])
        programs[name]=item
    for i,e in enumerate(debug.elements):
        source_name=e.names[2].decode(encoding)+'.Program.pou'
        resource_name=e.resource_bytes.decode(encoding)
        paths=[entry['key']['text'] for entry in indexes['maps'][0] if any(i in g for g in entry['groups'])]
        expansion=dict(element_index=i,element_offset=e.offset,element_raw_base64=base64.b64encode(e.raw).decode(),
            source=source_name,resource=resource_name,stored_instance_paths=paths,stored_names=[n.decode(encoding) for n in e.names],
            fields=list(e.fields),handling='opaque-preserved',occurrences=[])
        result['expansions'].append(expansion)
        if (e.kind_code==192 and e.names[0]==b'@IEC'
                and resource_name in resources and source_name not in streams):
            table=debug.offset_tables[e.offset_table_index]
            expansion.update(handling='compiled-library-region-source-unavailable',
                offset_table_raw_base64=base64.b64encode(table.raw).decode(),debug_rows=[list(r) for r in table.rows],
                linked_interval=[e.linked_step_start,e.linked_step_end],
                compiled_records=[record(g) for g in resources[resource_name]['groups']
                                  if e.linked_step_start<=g['stored_step']<=e.linked_step_end],
                entry_boundary_records=[record(g) for g in resources[resource_name]['groups']
                    if g['stored_step']<e.linked_step_start<g['stored_step']+g['stored_width']],
                preceding_entry_records=[record(g) for g in resources[resource_name]['groups']
                    if g['stored_step']==e.linked_step_start-1 and g['stored_width']==1],
                coordinate_meaning='compiled element interval; raw offset rows may extend beyond that interval')
            matches=embedded_libraries.get((e.names[0].decode(encoding),e.names[2].decode(encoding)),[])
            if len(matches)==1:
                entry,definition=matches[0]
                try:
                    body=observed_source_body(definition)
                    body_offset=definition['offset']+definition['raw'].index(body) if body else None
                    expansion.update(handling='compiled-library-region-embedded-source',embedded_source=dict(
                        stream=entry['logical_name'],stream_sha256=entry['sha256'],decoded_sha256=entry['decoded_sha256'],
                        definition_offset=definition['offset'],definition_raw_base64=base64.b64encode(definition['raw']).decode(),
                        body_offset=body_offset,body_raw_base64=None if body is None else base64.b64encode(body).decode(),
                        implementation_warning='library text does not establish intrinsic implementation; named standard timers may use compiler-generated code'))
                    if body is not None and e.fields[0]>=0 and all(r[0]>=0 and r[3]==1 and r[4]==8 and r[5]==0 for r in table.rows):
                        lines=[];offset=body_offset
                        for index,line in enumerate(body.splitlines(keepends=True)):
                            lines.append(dict(line_index=index,offset=offset,text=line.rstrip(b'\r\n').decode('ascii'),raw_base64=base64.b64encode(line).decode()))
                            offset+=len(line)
                        by_point={}
                        for row in table.rows:by_point.setdefault(row[0],[]).append(row)
                        for point,rows in by_point.items():
                            if point>=len(lines):
                                expansion['occurrences'].append(dict(source_offset=body_offset,source_line_index=point,debug_rows=[list(r) for r in rows],compiled_records=[],handling='stored-library-point-outside-text'));continue
                            line=lines[point]
                            targets={g['offset']:g for r in rows for g in resources[resource_name]['groups'] if e.linked_step_start+r[1]<=g['stored_step']<=e.linked_step_start+r[2]}
                            expansion['occurrences'].append(dict(source_offset=line['offset'],source_line=line,debug_rows=[list(r) for r in rows],compiled_records=[record(g) for g in targets.values()],handling='stored-library-IL-source-point'))
                except (ValueError,UnicodeDecodeError) as exc:expansion['embedded_source_diagnostic']=str(exc)
            elif len(matches)>1:expansion['embedded_source_diagnostic']='ambiguous CPU-selected library definition'
            continue
        if e.kind_code==208 and source_name in graph_programs and resource_name in resources:
            table=debug.offset_tables[e.offset_table_index]
            if table.kind_code!=208 or any(r[0]<0 or r[3]<1 or r[4]!=8 or r[5]!=0 for r in table.rows):
                expansion['diagnostic']='graph offset table framing or row kind is outside observed controls';continue
            expansion['handling']='stored-graph-network-correlation'
            by_network={}
            for row in table.rows:by_network.setdefault(row[3],[]).append(row)
            for network,rows in by_network.items():
                blocks=graph_programs[source_name]['blocks']
                if not 1<=network<=len(blocks):
                    expansion['occurrences'].append(dict(network=network,debug_rows=[list(r) for r in rows],
                        compiled_records=[],handling='stored-network-outside-source'));continue
                block=blocks[network-1]
                targets={g['offset']:g for r in rows for g in resources[resource_name]['groups']
                         if e.linked_step_start+r[1]<=g['stored_step']<=e.linked_step_start+r[2]}
                expansion['occurrences'].append(dict(source_offset=block['offset'],network=network,source_block=block,
                    debug_rows=[list(r) for r in rows],compiled_records=[record(g) for g in targets.values()],
                    coordinate_meaning='whole network; no individual-node to compiled-record mapping is inferred',
                    handling='stored-graph-network' if targets else 'no-compiled-record-at-stored-range'))
            continue
        if e.kind_code==193 and source_name in st_programs and resource_name in resources:
            table=debug.offset_tables[e.offset_table_index]
            if table.kind_code!=193 or any(r[0]<0 or r[3]!=-1 or r[4]&7!=7 for r in table.rows):
                expansion['diagnostic']='ST offset table framing or row kind is outside observed controls';continue
            expansion['handling']='stored-ST-source-point-correlation'
            by_point={}
            for row in table.rows:by_point.setdefault(e.fields[0]+row[0],[]).append(row)
            for point,rows in by_point.items():
                if not 0<=point<len(st_programs[source_name]['lines']):
                    expansion['occurrences'].append(dict(source_line_index=point,debug_rows=[list(r) for r in rows],
                        compiled_records=[],handling='stored-source-point-outside-text'));continue
                line=st_programs[source_name]['lines'][point]
                targets={g['offset']:g for r in rows for g in resources[resource_name]['groups']
                         if e.linked_step_start+r[1]<=g['stored_step']<=e.linked_step_start+r[2]}
                expansion['occurrences'].append(dict(source_offset=line['offset'],source_line=line,
                    debug_rows=[list(r) for r in rows],compiled_records=[record(g) for g in targets.values()],
                    handling='stored-ST-source-point' if targets else 'no-compiled-record-at-stored-range'))
            continue
        if e.kind_code!=1 or source_name not in programs or resource_name not in resources:
            expansion['diagnostic']='source language, source record framing, or resource is outside observed correlation';continue
        table=debug.offset_tables[e.offset_table_index]
        if table.kind_code!=1:
            expansion['diagnostic']='offset table language differs';continue
        expansion['handling']='stored-simple-source-correlation'
        for source_record in programs[source_name]['records']:
            rows=[r for r in table.rows if r[0]+e.fields[0]==source_record['stored_step']]
            if not rows:continue
            targets={g['offset']:g for r in rows for g in resources[resource_name]['groups']
                     if e.linked_step_start+r[1]<=g['stored_step']<=e.linked_step_start+r[2]}
            seen.add((source_name,source_record['offset']))
            expansion['occurrences'].append(dict(source_offset=source_record['offset'],source_record=source_record,
                debug_rows=[list(r) for r in rows],compiled_records=[record(g) for g in targets.values()],
                handling='stored-correlated-records' if targets else 'no-compiled-record-at-stored-range'))
    answers=native_batch(requests,output/'native-decode',dll=profile[0],cpu_code=profile[1]) if requests else []
    decoded=[]
    for request,answer in zip(requests,answers):
        raw=base64.b64decode(request['input_base64'])[:-1]
        item=dict(fragment_sha256=sha256(raw),native_return_code=answer['return_code'],
            native_consumed_bytes=answer['consumed_bytes'],handling='opaque-preserved')
        if answer['return_code']=='0x00000000' and answer['consumed_bytes']==len(raw):
            item.update(native_il_projection(base64.b64decode(answer['output_base64']),encoding=encoding))
        else:item['diagnostic']='native decoder rejected or did not consume complete record'
        decoded.append(item)
    result['decoded_fragments']=decoded
    # A native-decoded CALL and the entry label identify the stored callee.
    # This is independent of declaration-only call trees and does not establish
    # runtime reachability. FX library debug intervals can start inside the
    # two-step label record; in Q the one-step label immediately precedes the
    # interval. Retain those distinct bounds and require a decoded label.
    library_entries={}
    for expansion in result['expansions']:
        if not expansion['handling'].startswith('compiled-library-region-'):continue
        start=expansion['linked_interval'][0]
        entries=expansion['entry_boundary_records']+expansion['preceding_entry_records']+[g for g in expansion['compiled_records'] if g['stored_step']==start]
        for group in entries:
            if 'decode_request' not in group:continue
            records=decoded[group['decode_request']].get('records',[])
            if len(records)!=1 or records[0]['kind']!='label':continue
            key=(expansion['resource'],records[0]['text'])
            library_entries.setdefault(key,[]).append(dict(element_index=expansion['element_index'],
                stored_names=expansion['stored_names'],stored_instance_paths=expansion['stored_instance_paths'],entry_record=group))
    calls={}
    for expansion in result['expansions']:
        for occurrence in expansion['occurrences']:
            for group in occurrence['compiled_records']:
                if 'decode_request' not in group:continue
                records=decoded[group['decode_request']].get('records',[])
                if len(records)!=1 or records[0].get('op')!='CALL' or len(records[0]['args'])!=1:continue
                label=records[0]['args'][0];targets=library_entries.get((expansion['resource'],label),[])
                if not targets:continue
                key=(expansion['resource'],group['offset'])
                call=calls.setdefault(key,dict(resource=expansion['resource'],compiled_record=group,target_label=label,
                    targets=targets,source_contexts=[],handling='unique-compiled-library-target' if len(targets)==1 else 'ambiguous-compiled-library-targets'))
                context=dict(element_index=expansion['element_index'],source=expansion['source'],source_offset=occurrence['source_offset'])
                if 'network' in occurrence:context['network']=occurrence['network']
                if context not in call['source_contexts']:call['source_contexts'].append(context)
    result['library_call_links']=list(calls.values())
    for program in result['programs']:
        for source_record in program.get('records',[]):
            source_record['correlation_handling']='has-stored-correlations' if (program['name'],source_record['offset']) in seen else 'no-stored-correlation'
    result['summary']=dict(expansions=len(result['expansions']),correlated_groups=sum(len(e['occurrences']) for e in result['expansions']),
        source_groups=sum(len(p.get('records',[])) for p in result['programs']),unmapped_source_groups=sum(
            r['correlation_handling']=='no-stored-correlation' for p in result['programs'] for r in p.get('records',[])),
        st_source_points=sum(len(e['occurrences']) for e in result['expansions'] if e['handling']=='stored-ST-source-point-correlation'),
        graph_networks=sum(len(e['occurrences']) for e in result['expansions'] if e['handling']=='stored-graph-network-correlation'),
        library_regions_without_source=sum(e['handling']=='compiled-library-region-source-unavailable' for e in result['expansions']),
        library_regions_with_embedded_source=sum(e['handling']=='compiled-library-region-embedded-source' for e in result['expansions']),
        library_IL_source_points=sum(o['handling']=='stored-library-IL-source-point' for e in result['expansions'] for o in e['occurrences']),
        library_call_sites=len(calls),
        native_fragment_handling=dict(Counter(d['handling'] for d in decoded)))
    (output/'compiled-references.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('native_directory',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    print(json.dumps(inspect(args.native_directory,args.output)['summary']))
