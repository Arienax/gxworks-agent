"""Cross-check observed timer FOREIGN_NET text against native compiled records.

The text is generated compiler input, not the library's placeholder Program.pou.
Use Core's existing CGTable and assignment readers. Preserve unresolved lines and
operands; storage assignment roles do not establish instruction access roles.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from gxw.compiler_assignment import parse_compiler_assignment
from gxw.compiler_tables import parse_compiler_tables
from gxw.lossless import sha256


def project_foreign_net(raw: bytes, symbols: bytes, *, cpu: str) -> dict:
    if cpu not in ('FX3U/FX3UC','Q03UDV'):raise ValueError('CPU outside observed timer controls')
    opcodes={'LD','LDI','AND','OR','OUT','SET','RST'}
    opcodes.update(('DDIV','MUL') if cpu=='FX3U/FX3UC' else ('D/','*','OUTH','/','MOV','D*','+','DMOV'))
    tables=parse_compiler_tables(symbols)
    text=raw.decode('ascii')
    lines=[];records=[];gaps=[];offset=0;component_cursor=None
    for physical in text.splitlines(keepends=True):
        line=physical.rstrip('\r\n');item=dict(offset=offset,text=line,handling='opaque-preserved')
        offset+=len(physical);lines.append(item)
        if not line or re.fullmatch(r'N\d+ (?:FOREIGN_NET|IL_NET)(?: \(\*[^\r\n]*\*\))?',line):
            item['handling']='observed-network-header' if line else 'blank';continue
        if re.fullmatch(r'RET D\d+',line):
            record=dict(kind='instruction',op='SRET' if cpu=='FX3U/FX3UC' else 'RET',args=[])
            item.update(handling='observed-subroutine-return',projected_record=record);records.append(record);continue
        match=re.fullmatch(r'D\d+ #([A-Z*/+]+)(\d+) ((?:##[^#\s]+)+)',line)
        if match is None:
            component_cursor=None
            gaps.append(dict(offset=item['offset'],reason='outside observed FOREIGN_NET line framing'));continue
        op,count,values=match.groups();operands=values.split('##')[1:]
        if op=='P' and count=='0' and len(operands)==1 and re.fullmatch(r'P\d+',operands[0]):
            record=dict(kind='label',text=operands[0])
            item.update(handling='observed-entry-label',projected_record=record);records.append(record);continue
        if op not in opcodes or int(count)!=len(operands):
            component_cursor=None
            gaps.append(dict(offset=item['offset'],reason='opcode or operand count outside timer controls'));continue
        projected=[];bindings=[]
        for operand in operands:
            component=re.fullmatch(r'S\.K(-1|\d+)(?:\.O(\d+))?',operand)
            if component:
                requested=int(component[1]);cursor_before=component_cursor
                # ECCompiler_IEC CgTab Read (11870, CMP wrapper 15a20) skips
                # seeking for -1 and consumes the next physical table record.
                # The Q high-150 control independently observes all 21 reads
                # in source order, including two -1 references. Never resolve
                # this sentinel by name or as a negative Python index.
                index=component_cursor if requested==-1 else requested
                if index is None:
                    bindings.append(dict(operand=operand,handling='unbound-component-cursor'))
                    projected.append(None);continue
                try:c=tables.component_at(index)
                except ValueError:
                    component_cursor=None
                    bindings.append(dict(operand=operand,handling='component-cursor-outside-record-boundary',component_offset=index))
                    projected.append(None);continue
                component_cursor=c.record.table_offset+len(c.record.raw)
                a=parse_compiler_assignment(c.user_info)
                binding=dict(operand=operand,component_offset=index,component_name=c.name_bytes.decode('ascii'),
                    component_raw_base64=base64.b64encode(c.record.raw).decode(),user_info_hex=c.user_info.hex(),
                    assignment_handling=a.status,storage_role=a.role,physical_operand=a.fx_operand,
                    component_reference='observed-sequential-cursor' if requested==-1 else 'explicit-record-offset',
                    component_cursor_before=cursor_before,component_cursor_after=component_cursor)
                if component[2] is not None:
                    relative=int(component[2]);array_offset=tables.component_array_offset(c)
                    array=tables.array_at(array_offset) if array_offset is not None else None
                    # Independently decoded DDIV remainder storage in the
                    # four-INT Preset buffer establishes .O2 -> D(base+2).
                    # Other .O forms and other storage units remain unknown.
                    if (cpu=='Q03UDV' and relative==2 and c.name_bytes==b'Preset'
                            and a.status=='decoded' and a.device_family=='D' and a.role=='word'
                            and a.reserved_count==4 and array is not None and array.element_type==2
                            and array.total_count==4 and len(array.dimensions)==1
                            and array.dimensions[0].lower==0 and array.dimensions[0].extent==4):
                        binding.update(physical_operand='D'+str(a.number+relative),
                            offset_handling='observed-Preset-word-offset',word_offset=relative,
                            array_raw_base64=base64.b64encode(array.record.raw).decode())
                    else:
                        binding.update(physical_operand=None,offset_handling='opaque-preserved',stored_offset=relative)
                bindings.append(binding);projected.append(binding['physical_operand'])
            elif re.fullmatch(r'(?:K-?\d+|S?M\d+)',operand):
                bindings.append(dict(operand=operand,handling='preserved-literal'));projected.append(operand)
            else:
                component_cursor=None
                bindings.append(dict(operand=operand,handling='opaque-preserved'));projected.append(None)
        item['bindings']=bindings
        if any(p is None for p in projected):
            gaps.append(dict(offset=item['offset'],reason='unresolved operand or allocation'));continue
        record=dict(kind='instruction',op=op,args=projected)
        item.update(handling='observed-timer-instruction',projected_record=record);records.append(record)
    return dict(cpu=cpu,source_sha256=sha256(raw),source_raw_base64=base64.b64encode(raw).decode(),
        symbols_sha256=sha256(symbols),lines=lines,projected_records=records,gaps=gaps)


def inspect(trace_directory: Path, correlation_path: Path, output: Path) -> dict:
    trace_directory=trace_directory.resolve();correlation_path=correlation_path.resolve();output=output.resolve()
    read=json.loads(correlation_path.read_text(encoding='utf-8'))
    cpu=read['context']['cpu']
    if cpu not in ('FX3U/FX3UC','Q03UDV'):raise ValueError('CPU outside observed timer controls')
    native=[json.loads(l) for l in (trace_directory/'native-events.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    if (not any(e.get('operation')=='Progress' and e.get('percent')==100 for e in native)
            or any(r.get('kind')==2 for e in native for r in e.get('reports',[]))
            or not any(e.get('operation')=='NativeExport' for e in native)):
        raise ValueError('trace has no successful offline build/export')
    for resource in read['resources']:
        if sha256((trace_directory/resource['file']).read_bytes())!=resource['sha256']:
            raise ValueError('traced PCode differs from independently decoded correlation input')
    trace=trace_directory/'events.jsonl'
    messages=[json.loads(l) for l in trace.read_text(encoding='utf-8').splitlines()]
    if any(m.get('type')=='error' for m in messages):raise ValueError('incomplete instrumentation trace')
    events=[m['payload'] for m in messages if m.get('type')=='send']
    leaves={e['id']:e for e in events if e.get('event')=='leave'}
    result=dict(trace_sha256=sha256(trace.read_bytes()),correlation_sha256=sha256(correlation_path.read_bytes()),
        pcode_binding='byte-identical to independently native-decoded successful export',regions=[],
        complete_semantic_proof=False)
    for event in events:
        if event.get('event')!='enter' or event.get('name')!='Compile':continue
        if leaves.get(event['id'],{}).get('result')!='0x0':continue
        buffers={b['label']:b for b in event['buffers']}
        for label,buffer in buffers.items():
            if not label.startswith('source_') or not buffer.get('bytes'):continue
            name_buffer=buffers['name_'+label.split('_')[1]]
            name_raw=bytes.fromhex(name_buffer['bytes'])
            if b'\0' not in name_raw:raise ValueError('unbounded captured source name')
            name=name_raw.split(b'\0',1)[0].decode('ascii')
            matches=[e for e in read['expansions'] if e['handling'].startswith('compiled-library-region-')
                     and e['stored_names'][3]==name]
            if not matches:continue
            raw=bytes.fromhex(buffer['bytes']);symbols=bytes.fromhex(buffers['symbolic_data']['bytes'])
            if len(raw)!=buffer['requested_size'] or len(symbols)!=buffers['symbolic_data']['requested_size']:
                raise ValueError('truncated generated source or symbol table')
            projection=project_foreign_net(raw,symbols,cpu=cpu)
            region=dict(trace_event=event['id'],instance=name,projection=projection,compiled_candidates=[])
            result['regions'].append(region)
            for e in matches:
                groups=e['entry_boundary_records']+e.get('preceding_entry_records',[])+e['compiled_records']
                native_records=[r for g in groups for r in read['decoded_fragments'][g['decode_request']].get('records',[])]
                region['compiled_candidates'].append(dict(element_index=e['element_index'],native_records=native_records,
                    exact=not projection['gaps'] and projection['projected_records']==native_records))
    result['summary']=dict(regions=len(result['regions']),exact_regions=sum(
        len(r['compiled_candidates'])==1 and r['compiled_candidates'][0]['exact'] for r in result['regions']),
        source_lines=sum(len(r['projection']['lines']) for r in result['regions']),
        projected_records=sum(len(r['projection']['projected_records']) for r in result['regions']),
        gaps=sum(len(r['projection']['gaps']) for r in result['regions']))
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():raise FileExistsError(output)
    output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace_directory',type=Path);parser.add_argument('correlation',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args()
    print(json.dumps(inspect(args.trace_directory,args.correlation,args.output)['summary']))
