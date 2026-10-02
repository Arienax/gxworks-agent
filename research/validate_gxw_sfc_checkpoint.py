"""Cold-check retained SFC source, independent control decoding and native ABI.

Run from an extracted public checkpoint, passing its matching local archive.
No native DLL, project operation, network, or digest verification is used.
"""
from pathlib import Path
import base64
import collections
import json
import os
import struct
import sys
import zipfile


def main():
    root=Path(__file__).resolve().parent
    os.environ['GXW2_INSTRUCTION_CATALOG']=str(root/'product/resources/instructions/mitsubishi')
    sys.path[:0]=[str(root/'models'),str(root/'product/src')]
    from gxw.sfc_pou import parse_sfc_pou
    from source_fx_sfc_lossless_reader import FXSfcLosslessReader

    def native_ownership(snapshot):
        def raw(blob):return base64.b64decode(blob['raw'])
        def child(blob,offset):
            rows=[r['data'] for r in blob['relocations'] if r['offset']==offset]
            if len(rows)!=1:raise ValueError('Native relocation is absent or ambiguous')
            return rows[0]
        def name(blob,offset):return raw(child(blob,offset))[:-1].decode('cp936')
        pool=child(snapshot['build'],44);owners={}
        if len(raw(pool))%56:raise ValueError('Native POU array extent differs')
        for offset in range(0,len(raw(pool)),56):
            owner=name(pool,offset+8);children=[];actions=[]
            for field,stride,body_at,kind in ((48,12,4,'zoom'),(40,16,8,'transition')):
                sub=child(pool,offset+field);data=raw(sub)
                if len(data)%stride:raise ValueError('Native child array extent differs')
                for at in range(0,len(data),stride):
                    body=raw(child(sub,at+body_at))
                    if len(body)<84:raise ValueError('Native child buffer header is truncated')
                    children.append(dict(kind=kind,name=name(sub,at),
                        number=struct.unpack_from('<I',data,at+4)[0] if kind=='transition' else None,
                        token_hex=body[84:].hex()))
            sub=child(pool,offset+32);data=raw(sub)
            if len(data)%16:raise ValueError('Native action array extent differs')
            for at in range(0,len(data),16):
                registrations=child(sub,at+8);registry=raw(registrations)
                if len(registry)%8:raise ValueError('Native registration array extent differs')
                actions.append(dict(name=name(sub,at),number=struct.unpack_from('<I',data,at+4)[0],
                    registrations=[dict(qualifier=struct.unpack_from('<I',registry,pos)[0],zoom=name(registrations,pos+4))
                                   for pos in range(0,len(registry),8)]))
            if owner in owners:raise ValueError('Native source owner is duplicated')
            owners[owner]=dict(children=sorted(children,key=str),actions=sorted(actions,key=str))
        return owners

    counts=collections.Counter()
    with zipfile.ZipFile(sys.argv[1]) as archive:
        load=lambda name:json.loads(archive.read(name).decode('utf-8-sig'))
        manifest=load('checkpoint.json')
        for witness in manifest['source_witnesses']:
            sources={};ownership={}
            for item in witness['blocks']:
                raw=archive.read(item['source_entry']);source=parse_sfc_pou(raw)
                if source.reconstruct()!=raw:raise ValueError('Source raw reconstruction differs')
                if item['number'] in sources:raise ValueError('Source block number is duplicated')
                sources[item['number']]=(item['owner'],source)
                ownership[item['owner']]=dict(
                    children=sorted([{k:c[k] for k in ('kind','name','number','token_hex')} for c in source.layout['children']],key=str),
                    actions=sorted([{k:a[k] for k in ('name','number','registrations')} for a in source.layout['actions']],key=str))
            frontend=native_ownership(load(witness['frontend_entry']))
            if frontend!=ownership:raise ValueError('Independent native ABI ownership differs')
            body=archive.read(witness['pcode_entry']);reader=FXSfcLosslessReader();image=reader.read(body,cpu=520)
            if reader.reconstruct(image)!=body:raise ValueError('Independent native body partition differs')
            block=None;inline=0
            for node in image['sfc_nodes']:
                if node['kind']=='block-start':block=node['number'];continue
                if node['kind']=='block-end':block=None;continue
                if node['kind'] not in ('step','transition'):continue
                if block not in sources:raise ValueError('Native control has no declared source block')
                owner,source=sources[block]
                if node['kind']=='step':
                    reference=node['substep'] if node['key'] in (0x6c14,0x6c1c) else node['number']
                    action,=[a for a in source.layout['actions'] if a['number']==reference]
                    if not action['registrations']:raise ValueError('Native body is not source-bound after an empty registration')
                    first=action['registrations'][0]['zoom']
                    child,=[c for c in source.layout['children'] if c['kind']=='zoom' and c['name']==first]
                else:
                    child,=[c for c in source.layout['children'] if c['kind']=='transition' and c['number']==node['number']]
                if node['program_raw']!=child['token_hex']:raise ValueError('Native inline body differs from selected raw source')
                inline+=1
            if inline!=witness['inline_bindings']:raise ValueError('Inline binding count differs')
            counts['source_bound_native_bodies']+=1;counts['raw_inline_bindings_exact']+=inline
            counts['native_body_lexical_gap_cases']+=bool(image['coverage']['critical_gaps'])
            if witness['full_check_accepted']:
                events=[json.loads(l) for l in archive.read(witness['events_entry']).decode('utf-8-sig').splitlines()]
                publication=[i for i,e in enumerate(events) if e['operation']=='Workspace.UpdatePCodeBeforeProgramCheck' and e.get('hresult')==e.get('code')==0]
                checks=[i for i,e in enumerate(events) if e['operation']=='Compiler.ProgramCheck']
                completed=[e for e in events if e['operation']=='ProgramCheckCompleted']
                if len(publication)!=1 or len(checks)!=1 or publication[0]>=checks[0]:
                    raise ValueError('Current-code publication did not precede full check')
                if completed!=[dict(operation='ProgramCheckCompleted',targets=1,rejected=False)]:
                    raise ValueError('Native full check was not accepted')
                if any(r['kind'] in (2,3) for e in events if e['operation']=='ProgramCheckProgress' for r in e.get('reports',[])):
                    raise ValueError('Accepted check retained an error or warning diagnostic')
                if image['coverage']['critical_gaps']:raise ValueError('Accepted compiled body retains an unresolved lexical gap')
                expected=archive.read(witness['expected_pcode_entry'])
                if body!=expected:raise ValueError('Native output differs from prediction saved before native work')
                counts['native_full_check_accepted']+=1
        for witness in manifest['failure_gate_witnesses']:
            events=[json.loads(l) for l in archive.read(witness['events_entry']).decode('utf-8-sig').splitlines()]
            marker=[e for e in events if e['operation']=='SFCConversionFailure']
            if not marker or any(e['code']!=32 for e in marker):raise ValueError('Swallowed native failure marker is lost')
            if not any(e['operation']=='ExportSkipped' for e in events):raise ValueError('Failed conversion could export')
            if any(e['operation'] in ('Compiler.ProgramCheck','NativeExport','Project.SaveProject') for e in events):
                raise ValueError('Failed conversion was checked or saved as generated output')
            if archive.read(witness['pcode_entry'])!=archive.read(witness['cached_entry']):
                raise ValueError('Retained failed conversion cache differs')
            counts['failed_conversion_export_and_check_blocked']+=1
        for witness in manifest['empty_task_witnesses']:
            messages=[json.loads(l) for l in archive.read(witness['trace_entry']).decode('utf-8').splitlines()]
            if any(m['type']!='send' for m in messages):raise ValueError('Empty-task trace has an instrumentation error')
            stages=[m['payload'] for m in messages if m['type']=='send']
            if any(e.get('role') in ('assemble-source-programs','publish-resource-code') for e in stages):
                raise ValueError('Empty task unexpectedly assembled or published new code')
            load_returns=[e for e in stages if e.get('event')=='sfc-stage-return' and e.get('role')=='load-source-programs']
            if len(load_returns)!=1 or load_returns[0]['result']!=1 or load_returns[0]['code']!=0:
                raise ValueError('Empty-task source load outcome differs')
            if archive.read(witness['pcode_entry'])!=archive.read(witness['cached_entry']):
                raise ValueError('Empty task did not return the retained resource cache')
            counts['empty_task_cache_controls_exact']+=1
    expected=manifest['expected_cold_counts']
    if dict(counts)!=expected:raise ValueError('Frozen evidence counts differ: '+repr(dict(counts)))
    print(json.dumps(dict(counts),ensure_ascii=True))


if __name__=='__main__':main()
