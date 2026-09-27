"""Raw-preserved, bounded observations of Q packed-BOOL call outputs.

This projects the native AST rewrite observed at ECCompiler_IEC B1E00 and
compares it with independently decoded PCode. It is not a general compiler
or PLC emulator. Other node shapes, call names and transfers stay opaque.
"""
from __future__ import annotations
import hashlib
import struct
from probe_gxw_template_operands import observed_descriptor_operand
from probe_gxw_operand_bindings import resolve_operand
from probe_gxw_index_arithmetic import source_array_reference,decoded_index_arithmetic,affine,add


def _nodes(rows):
    result={}
    for row in rows:
        raw=bytes.fromhex(row['raw_hex'])
        if len(raw)!=73 or row['id'] in result:raise ValueError('invalid or duplicate native AST record')
        result[row['id']]=dict(id=row['id'],kind=struct.unpack_from('<I',raw,4)[0],
          left=struct.unpack_from('<I',raw,12)[0],right=struct.unpack_from('<I',raw,16)[0],
          parent=struct.unpack_from('<I',raw,20)[0],reference=struct.unpack_from('<I',raw,32)[0],
          descriptor=raw[48:],raw=raw)
    return result


def _arguments(nodes,node_id,path=()):
    if node_id in path or len(path)>64:raise ValueError('cyclic or excessive argument list')
    n=nodes[node_id]
    if n['kind']!=0x2c:return [node_id]
    return _arguments(nodes,n['left'],path+(node_id,))+_arguments(nodes,n['right'],path+(node_id,))


def _token(nodes,node_id,path=()):
    if node_id in path or len(path)>64:raise ValueError('cyclic or excessive operand tree')
    n=nodes[node_id]
    if n['kind']==0x10f:return 'K'+str(n['reference'])
    if n['kind']==0x112:return 'C'+str(n['reference'])
    if n['kind']==0x155:return _token(nodes,n['left'],path+(node_id,))
    if n['kind'] in (0x164,0x165,0x166):
        op={0x164:'OR',0x165:'XOR',0x166:'AND'}[n['kind']]
        return '('+_token(nodes,n['left'],path+(node_id,))+' '+op+' '+_token(nodes,n['right'],path+(node_id,))+')'
    if n['kind']==0x16c:return '(NOT '+_token(nodes,n['left'],path+(node_id,))+')'
    if n['kind']==0x5b:
        return _token(nodes,n['left'],path+(node_id,))+'['+','.join(_token(nodes,i,path+(node_id,)) for i in _arguments(nodes,n['right']))+']'
    if n['kind']==0x16f and n['descriptor']==bytes.fromhex('00000000000000009001000000000000000000004d40000000'):
        return '##SM400'
    raise ValueError(f'operand node kind {n["kind"]:#x} remains opaque')


def _boolean_tree(nodes,node_id,path=()):
    if node_id in path or len(path)>64:raise ValueError('cyclic or excessive Boolean tree')
    n=nodes[node_id];path=path+(node_id,)
    if n['kind']==0x155:return _boolean_tree(nodes,n['left'],path)
    if n['kind'] in (0x164,0x165,0x166):
        return dict(op={0x164:'OR',0x165:'XOR',0x166:'AND'}[n['kind']],left=_boolean_tree(nodes,n['left'],path),right=_boolean_tree(nodes,n['right'],path))
    if n['kind']==0x16c:return dict(op='NOT',value=_boolean_tree(nodes,n['left'],path))
    return dict(op='atom',token=_token(nodes,node_id))


def observed_boolean_output_rewrite(event,tables,allocated):
    """Recover only the observed two-argument BOOL call/copy-back shape."""
    result=dict(handling='opaque-preserved',raw_event=event,gaps=[])
    try:
        if event.get('event')!='call-specialized-argument-rewrite':raise ValueError('not an observed output rewrite')
        before=_nodes(event['before_nodes']);after=_nodes(event['after_nodes'])
        a,b=before[event['node']],after[event['node']]
        if a['kind']!=0x10c or b['kind']!=a['kind'] or a['reference']!=b['reference'] or a['right']:
            raise ValueError('unobserved root call shape')
        definition=tables.pou_at(a['reference']);name=definition.name_bytes.decode('ascii')
        if name not in ('PLS_M','PLF_M','SET_M','RST_M'):raise ValueError('call outside observed BOOL output set')
        old_args=_arguments(before,a['left']);new_args=_arguments(after,b['left'])
        if len(old_args)!=2 or len(new_args)!=2 or old_args[1]!=event['argument_node']:
            raise ValueError('unobserved actual argument shape')
        temporary=after[new_args[1]]
        if temporary['kind']!=0x11e:raise ValueError('output was not replaced by observed temporary node')
        temp=observed_descriptor_operand(temporary['descriptor'],cpu='Q03UDV')
        if temp.get('base_family')!='M' or temp.get('index_register') is not None:raise ValueError('temporary device outside observed form')
        block=after[b['right']]
        if block['kind']!=0x11c or block['left']:raise ValueError('unobserved post-call sequence')
        copy=after[block['right']]
        if copy['kind']!=0x12c or copy['reference']!=123 or copy['right']:raise ValueError('unobserved synthetic transfer operation')
        copied_args=_arguments(after,copy['left'])
        if len(copied_args)!=3:raise ValueError('unobserved transfer arguments')
        target=_token(before,old_args[1]);enable_snapshot=None
        condition=before[old_args[0]]
        if condition['kind']==0x155:
            en=_token(before,condition['left']);temporary_en=before[condition['right']]
            copied_en=after[copied_args[0]];new_condition=after[new_args[0]]
            if (temporary_en['kind']!=0x11f or copied_en['kind']!=0x11e or new_condition['kind']!=0x155
                    or _token(after,new_condition['left'])!=en
                    or after[new_condition['right']]['kind']!=0x11e
                    or temporary_en['descriptor']!=copied_en['descriptor']
                    or after[new_condition['right']]['descriptor']!=temporary_en['descriptor']):
                raise ValueError('enable snapshot was not preserved across the call')
            enabled=observed_descriptor_operand(temporary_en['descriptor'],cpu='Q03UDV')
            if enabled.get('base_family')!='M' or enabled.get('index_register') is not None:
                raise ValueError('enable temporary outside observed form')
            enable_snapshot=dict(direction='original-enable-to-temporary',source_token=en,temporary=enabled,
                                 expression=_boolean_tree(before,condition['left']),read_before_call=True,reused_for_copy_back=True)
        else:
            en=_token(before,old_args[0])
            if _token(after,new_args[0])!=en or _token(after,copied_args[0])!=en:
                raise ValueError('enable operand not preserved')
            enabled=resolve_operand(en,tables,allocated,cpu='Q03UDV')
        if _token(after,copied_args[2])!=target:raise ValueError('original target not preserved')
        if after[copied_args[1]]['kind']!=0x11e or after[copied_args[1]]['descriptor']!=temporary['descriptor']:
            raise ValueError('transfer source differs from call temporary')
        binding=resolve_operand(target.split('[')[0],tables,allocated,cpu='Q03UDV')
        if binding.get('base',{}).get('addressing')!='word-bit' or binding.get('layout',{}).get('family_class')!='bit':
            raise ValueError('target outside observed packed BOOL arrays')
        if not enabled.get('operand'):raise ValueError('unresolved enable operand')
        result.update(handling='observed-Q-packed-BOOL-output-rewrite',call=name,call_pou_reference=a['reference'],
          instruction_source=('observed name-specialized branch 82EFF..830A8; body-edit and alias controls separate it from template semantics'
                              if name in ('PLS_M','PLF_M') else 'original library template observed in controls; this AST snapshot alone does not establish its body'),
          enable_token=en,enable_operand=enabled['operand'],enable_snapshot=enable_snapshot,target_token=target,target_binding=binding,
          temporary=temp,transfer=dict(direction='temporary-to-original-target',synthetic_node_kind=0x12c,
          synthetic_reference=123,enable_value_reused_after_call=True,target_address_evaluated_after_call=True),
          source_table_sha256=hashlib.sha256(tables.raw).hexdigest(),allocated_table_sha256=hashlib.sha256(allocated.raw).hexdigest())
    except (KeyError,ValueError,UnicodeError) as exc:result['gaps'].append(str(exc))
    return result


def _bit_address(token,records,tables,allocated):
    """Recognize the observed complete address prefix, excluding the bit access."""
    def ins(op,*args):return dict(kind='instruction',op=op,args=list(args))
    divisions=[i for i,r in enumerate(records) if r.get('op')=='D/']
    if len(divisions)!=1:raise ValueError('division pattern differs')
    pos=divisions[0];div=records[pos]['args']
    if len(div)!=3 or div[:2]!=['Z16','H10'] or not div[2].startswith('D'):raise ValueError('division operands differ')
    slot=int(div[2][1:]);arithmetic=decoded_index_arithmetic(records[:pos])
    source=source_array_reference(token,tables,allocated)
    if source['binding']['base'].get('addressing')!='word-bit':raise ValueError('address outside observed packed bit form')
    expected=add(affine(source['binding']['base']['bit']),source['relative_device_units'])
    observed=arithmetic['state'].get('Z16')
    if arithmetic['gaps'] or expected!=observed:raise ValueError('source bit coordinate differs from decoded arithmetic')
    result_slots=[ins('DMOV','D'+str(slot),'Z16'),ins('DMOV','D'+str(slot+2),'Z18')]
    if records[pos+1:]!=result_slots:raise ValueError('division result register transfers differ')
    return dict(source_reference=source,expected_bit_coordinate=expected,observed_bit_coordinate=observed,
                arithmetic=arithmetic,division_record=records[pos],division_result_slots=result_slots,
                word_operand='D'+str(source['binding']['base']['number'])+'Z16')


def _enable_before_call(projected,records,tables,allocated):
    """Finite truth-table comparison of the observed straight Boolean prefix."""
    tree=projected['enable_snapshot']['expression'];atoms=[]
    def collect(n):
        if n['op']=='atom':
            if n['token'] not in atoms:atoms.append(n['token'])
        elif n['op']=='NOT':collect(n['value'])
        else:collect(n['left']);collect(n['right'])
    collect(tree)
    if len(atoms)>8:raise ValueError('Boolean prefix exceeds bounded truth-table scope')
    full=(1<<(1<<len(atoms)))-1
    values={t:sum(1<<row for row in range(1<<len(atoms)) if row&(1<<i)) for i,t in enumerate(atoms)}
    def evaluate(n):
        op=n['op']
        if op=='atom':return values[n['token']]
        if op=='NOT':return full^evaluate(n['value'])
        a,b=evaluate(n['left']),evaluate(n['right'])
        return a&b if op=='AND' else a|b if op=='OR' else a^b
    state={};arrays=[]
    for t in atoms:
        if '[' in t:arrays.append(t);continue
        b=resolve_operand(t,tables,allocated,cpu='Q03UDV')
        if not b.get('operand') or b.get('base',{}).get('family_class')!='bit':raise ValueError('Boolean atom lacks an observed bit binding')
        state[b['operand']]=values[t]
    read_rows=[];cursor=0;read_tokens=set()
    for i,r in enumerate(records):
        if r.get('op')!='TEST':continue
        candidates=[]
        for token in arrays:
            try:a=_bit_address(token,records[cursor:i],tables,allocated)
            except ValueError:continue
            if r['args'][:2]==[a['word_operand'],'Z18'] and len(r['args'])==3:candidates.append((token,a))
        if len(candidates)!=1:raise ValueError('ambiguous or unmatched enable array read')
        token,address=candidates[0];state[r['args'][2]]=values[token];read_tokens.add(token)
        read_rows.append(dict(token=token,address=address,test=r));cursor=i+1
    if set(arrays)!=read_tokens:raise ValueError('enable array reads differ from the Boolean expression')
    accumulator=None;stack=[];steps=[]
    for r in records[cursor:]:
        op=r['op'];args=r['args']
        if op in ('LD','LDI') and len(args)==1:
            if accumulator is not None:stack.append(accumulator)
            accumulator=state[args[0]]
            if op=='LDI':accumulator^=full
        elif op in ('AND','ANI','OR','ORI') and len(args)==1 and accumulator is not None:
            value=state[args[0]]^(full if op in ('ANI','ORI') else 0)
            accumulator=accumulator&value if op in ('AND','ANI') else accumulator|value
        elif op in ('ORB','ANB') and not args and accumulator is not None and stack:
            other=stack.pop();accumulator=accumulator|other if op=='ORB' else accumulator&other
        elif op=='OUT' and args==[projected['enable_operand']] and accumulator is not None:state[args[0]]=accumulator
        else:raise ValueError('instruction outside observed Boolean enable prefix')
        steps.append(dict(record=r,truth_mask=accumulator))
    expected=evaluate(tree);stored=state.get(projected['enable_operand'])
    if stack or accumulator!=expected or stored!=expected:raise ValueError('Boolean prefix differs from the captured enable expression')
    return dict(expression=tree,atoms=atoms,truth_table_rows=1<<len(atoms),expected_truth_mask=expected,
                observed_truth_mask=accumulator,stored_enable_truth_mask=stored,array_reads=read_rows,steps=steps,
                scope='finite straight Boolean expression comparison; stateful call execution is not emulated')


def compare_observed_boolean_output(projected,records,tables,allocated):
    """Compare source binding, native AST transfer and decoded instruction order.

    D/ and its two result slots are matched structurally. Their runtime
    division semantics, finite-width overflow and PLC execution are not
    emulated or established by this comparison.
    """
    result=dict(handling='opaque-preserved',records=records,gaps=[],exact=False)
    try:
        if projected['handling']!='observed-Q-packed-BOOL-output-rewrite':raise ValueError('rewrite was not resolved')
        en=projected['enable_operand'];temp=projected['temporary']['operand'];op=projected['call'][:-2]
        def ins(op,*args):return dict(kind='instruction',op=op,args=list(args))
        remaining=records;enable_read=None
        if projected.get('enable_snapshot'):
            calls=[i for i,r in enumerate(remaining) if r.get('op')==op]
            if len(calls)!=1 or remaining[calls[0]]!=ins(op,temp):raise ValueError('native call operation differs')
            enable_read=_enable_before_call(projected,remaining[:calls[0]],tables,allocated)
            remaining=remaining[calls[0]+1:]
        else:
            if remaining[:2]!=[ins('LD',en),ins(op,temp)]:raise ValueError('native call prefix differs')
            remaining=remaining[2:]
        sets=[i for i,r in enumerate(remaining) if r.get('op')=='BSET']
        if len(sets)!=1:raise ValueError('copy-back must have one observed BSET')
        suffix_start=sets[0]-(1 if en=='SM400' else 2)
        address=_bit_address(projected['target_token'],remaining[:suffix_start],tables,allocated)
        word=address['word_operand']
        suffix=([ins('LD',temp),ins('BSET',word,'Z18'),ins('LDI',temp),ins('BRST',word,'Z18')] if en=='SM400' else
          [ins('LD',en),ins('AND',temp),ins('BSET',word,'Z18'),ins('LD',en),ins('ANI',temp),ins('BRST',word,'Z18')])
        if remaining[suffix_start:]!=suffix:raise ValueError('guarded bit copy-back differs')
        result.update(handling='observed-call-transfer-and-address-pattern',exact=True,call=projected['call'],
          **address,enable_read=enable_read,
          copy_back=suffix,scope='native code shape and unbounded affine equality; no PLC execution, overflow or runtime bounds proof')
    except (KeyError,ValueError) as exc:result['gaps'].append(str(exc))
    return result
