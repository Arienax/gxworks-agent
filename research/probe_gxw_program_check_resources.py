"""Bind ProgramCheck's actual workspace reads to current and exported code.

Only attach a newly spawned owned offline helper. Workspace.GetPCode ABI is
from the installed type library (slot 1720, 48-byte resource ID, three buffers).
No calls are injected by the trace; the native reader's own results are copied.
"""
from pathlib import Path
import argparse
import json
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'research')]
from replay_gxw_workspace import prepare,trace_prepared,source_output_context
from gxw.lossless import inspect_project,sha256
from gxw.token_resource import parse_token_resource
from gxw.project_metadata import read_project_text_context

SCRIPT=r'''
let serial=0,phase='startup',checkIndex=-1,checkTarget=null;
function id(a){return Array.from({length:12},(_,i)=>a[i+1].toUInt32());}
function loc(p){const m=Process.findModuleByAddress(p);return m?{module:m.name,rva:p.sub(m.base).toString()}:{address:p.toString()};}
function hex(p,n){if(n<0||n>16777216)throw new Error('buffer bound');return Array.from(new Uint8Array(p.readByteArray(n)),b=>b.toString(16).padStart(2,'0')).join('');}
// The public adapter can fail while projecting a completed backend error.
// Preserve that original diagnostic independently; never replace a return value.
const backendProgressTargets=new Set();
function sizedText(p,n){
  if(n<0||n>65536)throw new Error('diagnostic text bound');
  if(p.isNull()){if(n!==0)throw new Error('null diagnostic text');return null;}
  if(n===0)return '';
  const raw=hex(p,n);if(!raw.endsWith('00'))throw new Error('diagnostic text terminator');return raw.slice(0,-2);
}
function backendReport(p){
  const args=[],list=p.add(64).readPointer();
  if(!list.isNull()){
    const count=list.add(4).readS32();if(count<0||count>128)throw new Error('diagnostic argument bound');
    const base=list.readPointer();if(count&&base.isNull())throw new Error('null diagnostic arguments');
    for(let i=0;i<count;i++){const a=base.add(i*8);args.push(sizedText(a.readPointer(),a.add(4).readS32()));}
  }
  return {raw_hex:hex(p,68),kind:p.readS32(),code:p.add(4).readU32(),
    library_hex:sizedText(p.add(8).readPointer(),p.add(12).readS32()),
    name_hex:sizedText(p.add(16).readPointer(),p.add(20).readS32()),
    instance_kind:p.add(24).readS32(),instance_hex:sizedText(p.add(28).readPointer(),p.add(32).readS32()),
    program_kind:p.add(36).readS32(),step:p.add(40).readS32(),network:p.add(44).readS32(),
    left:p.add(48).readS32(),top:p.add(52).readS32(),right:p.add(56).readS32(),bottom:p.add(60).readS32(),arguments_hex:args};
}
Process.attachModuleObserver({onAdded(m){
  const name=m.name.toLowerCase();
  if(name==='dzdataabs_compileradapter.dll'){
   Interceptor.attach(m.base.add(86113),{
    onEnter(a){checkIndex++;phase='check';checkTarget=id(a);send({event:'check-start',serial:serial++,check_index:checkIndex,target:checkTarget,kind:a[13].toUInt32()});}
   });
   Interceptor.attach(m.base.add(0x9674),{onEnter(){
    if(phase!=='check')return;
    try{
      const backend=this.context.ecx.add(0x14).readPointer(),target=backend.readPointer().add(44).readPointer();
      if(backendProgressTargets.has(target.toString()))return;backendProgressTargets.add(target.toString());
      const location=loc(target),supported=!!location.module&&location.module.toLowerCase()==='dzdataabs_compiler_iec.dll'&&location.rva==='0x700b';
      send({event:'backend-diagnostic-observer',serial:serial++,target:location,supported});
      if(!supported)return;
      Interceptor.attach(target,{
        onEnter(a){if(phase!=='check')return;this.contextRow={check_index:checkIndex,target:checkTarget};this.percent=a[1];this.count=a[2];this.reports=a[3];this.code=a[4];},
        onLeave(r){if(!this.contextRow)return;try{
          const count=this.count.readS32(),rows=[];if(count<0||count>10000)throw new Error('backend report count');
          const data=this.reports.readPointer();
          if(!data.isNull())for(let i=0;i<count;i++)rows.push(backendReport(data.add(i*68)));
          send({event:'backend-check-progress',serial:serial++,...this.contextRow,hresult:r.toInt32(),code:this.code.readU32(),percent:this.percent.readS32(),
            count,reports_present:!data.isNull(),reports:rows});
        }catch(e){send({event:'observation-error',error:String(e)});}}
      });
    }catch(e){send({event:'observation-error',error:String(e)});}
   }});
   Interceptor.attach(m.base.add(0x8e48),{
    onEnter(a){if(phase!=='check')return;try{this.row={check_index:checkIndex,target:checkTarget,report:backendReport(a[0])};this.code=a[2];}catch(e){send({event:'observation-error',error:String(e)});}},
    onLeave(r){if(!this.row||r.toInt32()>=0)return;try{send({event:'diagnostic-projection-failure',serial:serial++,...this.row,hresult:r.toInt32(),code:this.code.readU32()});}catch(e){send({event:'observation-error',error:String(e)});}}
   });
  }
  if(name!=='dzdataabs_workspace.dll')return;
  Interceptor.attach(m.base.add(0x8663c),{
    onEnter(a){phase=checkIndex<0?'publish-before-check':'publish-before-export';send({event:'publish',serial:serial++,phase:phase,target:id(a)});}
  });
  Interceptor.attach(m.base.add(0x78d08),{
    onEnter(a){
      try{
        this.row={event:'workspace-pcode-read',serial:serial++,phase:phase,check_index:checkIndex,
          check_target:checkTarget,target:id(a),caller:loc(this.returnAddress),
          stack:Thread.backtrace(this.context,Backtracer.ACCURATE).slice(0,12).map(loc)};
        this.sizes=[a[13],a[15],a[17]];this.buffers=[a[14],a[16],a[18]];this.code=a[19];
        this.row.requested_sizes=this.sizes.map(p=>p.isNull()?null:p.readS32());
      }catch(e){send({event:'observation-error',error:String(e)});}
    },
    onLeave(r){if(!this.row)return;try{
      const code=this.code.readS32();
      this.row.hresult=r.toInt32();this.row.code=code;
      this.row.buffers=this.sizes.map((p,i)=>{const n=p.isNull()?null:p.readS32();return {size:n,
        raw_hex:code===0&&r.toInt32()>=0&&!this.buffers[i].isNull()&&n!==null?hex(this.buffers[i],n):null};});
      send(this.row);
    }catch(e){send({event:'observation-error',error:String(e)});}}
  });
}});
'''

def resource_regions(source):
    rows=[]
    for s in inspect_project(source).streams:
        if not (s.logical_name or '').endswith('.res'):continue
        row=dict(name=s.logical_name,sha256=sha256(s.raw))
        try:
            parsed=parse_token_resource(s.raw)
            row['regions']=[dict(offset=o,size=n,sha256=sha256(s.raw[o:o+n])) for o,n in parsed.code_spans]
        except ValueError as e:row['unsupported']=str(e)
        rows.append(row)
    return rows

def inspect_run(native):
    events=[json.loads(l) for l in (native/'native-events.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    messages=[json.loads(l) for l in (native/'events.jsonl').read_text(encoding='utf-8').splitlines()]
    errors=[m for m in messages if m.get('type')=='error' or m.get('payload',{}).get('event')=='observation-error']
    trace=[m['payload'] for m in messages if m.get('type')=='send']
    names={tuple(e['id']):e['name'] for e in events if e.get('operation')=='NativeObject'}
    generated={e['name']:dict(index=e['index'],channels=[(native/f'pcode-{e["index"]}-{i}.bin').read_bytes() for i in range(3)])
        for e in events if e.get('operation')=='Resource'}
    reads=[]
    for r in trace:
        if r.get('event')!='workspace-pcode-read':continue
        name=names.get(tuple(r['target']));g=generated.get(name)
        row={k:v for k,v in r.items() if k!='buffers'}
        row['resource_name']=name;row['buffers']=[]
        for i,b in enumerate(r['buffers']):
            raw=None if b['raw_hex'] is None else bytes.fromhex(b['raw_hex'])
            if raw is not None:(native/f'checked-read-{r["serial"]}-{i}.bin').write_bytes(raw)
            row['buffers'].append(dict(size=b['size'],sha256=None if raw is None else sha256(raw),
                matches_current=None if raw is None or g is None else raw==g['channels'][i]))
        reads.append(row)
    check=next((e for e in reversed(events) if e.get('operation')=='ProgramCheckCompleted'),None)
    exported=native/'native-saved.gxw'
    source=(native/'input.gxw').read_bytes()
    context=read_project_text_context(next(s.raw for s in inspect_project(source).streams if (s.logical_name or '').endswith('.prj')))
    def diagnostic(row):
        result=dict(row)
        for field in ('library','name','instance'):
            raw=row[field+'_hex'];result[field]=None if raw is None else bytes.fromhex(raw).decode(context['text_encoding'],errors='backslashreplace')
        result['arguments']=[None if raw is None else bytes.fromhex(raw).decode(context['text_encoding'],errors='backslashreplace') for raw in row['arguments_hex']]
        return result
    progress=[dict(r,reports=[diagnostic(d) for d in r['reports']]) for r in trace if r.get('event')=='backend-check-progress']
    backend_diagnostics=[dict(d,check_index=r['check_index'],target=r['target']) for r in progress for d in r['reports'] if d['kind'] in (2,3)]
    completed={r['check_index'] for r in progress if r['hresult']>=0 and r['code']==0 and r['percent']==100}
    starts={r['check_index'] for r in trace if r.get('event')=='check-start'}
    backend_rejected=True if any(d['kind']==2 for d in backend_diagnostics) else False if starts and starts<=completed else None
    result=dict(check=check,trace_errors=errors,last_event=events[-1] if events else None,
        input_sha256=sha256(source),input_resources=resource_regions(source),
        generated=[dict(name=k,index=v['index'],channels=[dict(size=len(b),sha256=sha256(b)) for b in v['channels']]) for k,v in generated.items()],
        source_context=source_output_context(source,{k:v['channels'][0] for k,v in generated.items()}),
        check_starts=[r for r in trace if r.get('event')=='check-start'],resource_reads=reads,
        backend_check_progress=progress,backend_check_diagnostics=backend_diagnostics,
        backend_completed_check_indices=sorted(completed),backend_check_rejected=backend_rejected,
        diagnostic_projection_failures=[dict(r,report=diagnostic(r['report'])) for r in trace if r.get('event')=='diagnostic-projection-failure'],
        diagnostic_observers=[r for r in trace if r.get('event')=='backend-diagnostic-observer'],
        exported_sha256=sha256(exported.read_bytes()) if exported.exists() else None,
        exported_resources=resource_regions(exported.read_bytes()) if exported.exists() else None,
        diagnostics=[dict(phase=e['operation'],**r) for e in events for r in e.get('reports',[]) if r.get('kind') in (2,3)])
    (native/'resource-correspondence.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('input',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--change-sfc',action='store_true');p.add_argument('--sfc-chars',type=Path);p.add_argument('--legacy-check',action='store_true')
    a=p.parse_args();out=a.output.resolve();out.mkdir(exist_ok=False)
    script=out/'trace.js';script.write_text(SCRIPT,encoding='utf-8')
    (out/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    for attempt in range(1,5):
        native=out/f'native-{attempt}'
        prepare(a.input.resolve(),native,compile=True,export_project=True,program_check=True,snapshot_frontend=True,
            project_alias='CHECKMATRIX',change_sfc=a.change_sfc,sfc_graph_chars=a.sfc_chars,refresh_program_check=not a.legacy_check)
        trace=trace_prepared(native,script)
        events=[json.loads(l) for l in (native/'native-events.jsonl').read_text(encoding='utf-8-sig').splitlines()]
        if not any(e.get('operation')=='NativeTemporaryDirectoryCollision' for e in events):break
    result=inspect_run(native)
    print(json.dumps(dict(native=str(native),trace=trace,check=result['check'],reads=len(result['resource_reads']),
        errors=result['trace_errors'],export=bool(result['exported_sha256']),
        backend_rejected=result['backend_check_rejected'],backend_diagnostics=result['backend_check_diagnostics'],
        diagnostic_projection_failures=result['diagnostic_projection_failures'],
        read_contexts=[dict(phase=r['phase'],name=r['resource_name'],caller=r['caller'],buffers=r['buffers']) for r in result['resource_reads']]),ensure_ascii=True))
    if trace['timed_out'] or result['trace_errors'] or result['check'] is None:
        raise RuntimeError('resource check observation incomplete; raw failure evidence retained')

if __name__=='__main__':main()
