"""Bounded simple-ladder FB input lowering from each caller code region.

The final component UserInfo can change both address and representation after
a later call. This comparison uses the source connection fragment and its own
caller region; it never obtains the BOOL input destination from the FB body
being checked. Unknown templates or unconsumed native records remain gaps.
"""
from __future__ import annotations
import re


def _groups(records):
    groups=[]
    for record in records:
        if record.get('kind')!='instruction':raise ValueError('non-instruction in caller fragment')
        if record['op'] in ('LD','LDI'):groups.append([])
        if not groups:raise ValueError('caller fragment does not begin with LD/LDI')
        groups[-1].append(record)
    return groups


def compare_callsite_inputs(source,ports,native,allocations=None):
    """Compare observed direct BOOL, materialized Boolean, and scalar copies.

    Direct BOOL and D-device copies can be eliminated into pin aliases. Other
    observed groups must match a complete native prefix, opcode and source;
    their destination comes from that caller, never from the final allocation.
    Native groups are consumed in source order. Optional final allocations
    are recorded for comparison only. This is not a general optimizer.
    """
    result=dict(handling='opaque-preserved',source_records=source,native_records=native,bindings=[],steps=[],gaps=[])
    try:
        inputs={"'_"+p['name']:p for p in ports if p['direction']=='input'}
        if len(inputs)!=sum(p['direction']=='input' for p in ports):raise ValueError('duplicate input port names')
        source_groups=_groups(source);native_groups=_groups(native);cursor=0;bound=set()
        for ordinal,group in enumerate(source_groups):
            last=group[-1];target=last['args'][-1] if last['args'] else None
            port=inputs.get(target)
            if port is None or port['name'] in bound:raise ValueError('missing or repeated input target')
            actual=native_groups[cursor] if cursor<len(native_groups) else []
            binding=None;consume=False
            if last['op']=='OUT' and len(last['args'])==1 and port['type_marker']=='B':
                prefix=group[:-1]
                if (not prefix or prefix[0]['op'] not in ('LD','LDI')
                        or any(r['op'] not in ('AND','ANI','OR','ORI') for r in prefix[1:])
                        or any(len(r['args'])!=1 or not re.fullmatch(r'(?:X[0-9A-F]+|(?:SM|M)[0-9]+)',r['args'][0]) for r in prefix)):
                    raise ValueError('BOOL expression outside observed native source grammar')
                if (len(actual)==len(group) and actual[:-1]==prefix and actual[-1]['op']=='OUT'
                        and len(actual[-1]['args'])==1 and re.fullmatch(r'M[0-9]+',actual[-1]['args'][0])):
                    binding=dict(port=port['name'],operand=actual[-1]['args'][0],mode='materialized-BOOL',expression_records=prefix)
                    consume=True
                elif len(prefix)==1 and prefix[0]['op']=='LD':
                    binding=dict(port=port['name'],operand=prefix[0]['args'][0],mode='eliminated-direct-BOOL')
                else:raise ValueError('compound BOOL source has no exact caller materialization')
            elif (len(group)==2 and group[0]['op']=='LD' and group[0]['args']==["'_TRUE"]
                    and last['op'] in ('MOV','DMOV','$MOV') and len(last['args'])==2
                    and port['type_marker']=={'MOV':'W','DMOV':'D','$MOV':'S'}[last['op']]):
                value=last['args'][0]
                if (len(actual)==2 and actual[0]==dict(kind='instruction',op='LD',args=['SM400'])
                        and actual[1]['op']==last['op'] and len(actual[1]['args'])==2
                        and actual[1]['args'][0]==value and re.fullmatch(r'D[0-9]+',actual[1]['args'][1])):
                    binding=dict(port=port['name'],operand=actual[1]['args'][1],mode='materialized-scalar',source_operand=value)
                    consume=True
                elif re.fullmatch(r'D[0-9]+',value):
                    binding=dict(port=port['name'],operand=value,mode='eliminated-direct-scalar',source_operand=value)
                else:raise ValueError('scalar source has no exact caller materialization')
                binding['stored_final_allocation']=(allocations or {}).get(port['name'])
            else:raise ValueError('input connection template outside observed forms')
            binding.update(source_group=ordinal,native_group=cursor if consume else None)
            result['bindings'].append(binding);bound.add(port['name'])
            result['steps'].append(dict(source_group=group,native_group=actual if consume else [],binding=binding))
            cursor+=int(consume)
        if cursor!=len(native_groups):raise ValueError('unconsumed native caller groups')
        result['handling']='observed-callsite-input-lowering'
    except ValueError as exc:
        result['gaps'].append(str(exc))
        # Partial candidates remain evidence, never a usable alias overlay.
    return result
