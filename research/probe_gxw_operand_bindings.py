"""Raw-preserved projection of observed Q compiler operands and array layouts.

This consumes source CGTables and separately captured post-allocation tables.
It does not allocate addresses or validate CPU ranges. Dynamic indices retain
their expression and require a separate lowered-code model.
"""
from __future__ import annotations
import base64,re
from gxw.compiler_assignment import parse_compiler_assignment
from probe_gxw_compiler_constants import constant_record,integer_operand


def split_operands(text: str) -> list[str]:
    """Separate outer commas while keeping index lists and expressions intact."""
    stack=[];start=0;result=[]
    for i,char in enumerate(text):
        if char in '[(':stack.append(char)
        elif char in '])':
            if not stack or stack.pop()!= {']':'[',')':'('}[char]:
                raise ValueError('unbalanced compiler operand delimiters')
        elif char==',' and not stack:result.append(text[start:i].strip());start=i+1
    if stack:raise ValueError('unterminated compiler operand delimiters')
    result.append(text[start:].strip())
    if any(not part for part in result):raise ValueError('empty compiler operand')
    return result


def integer_expression(token: str, constants: dict) -> tuple[int | None, dict | None]:
    match=re.fullmatch(r'(-\s*)?C([0-9]+)',token.strip())
    if not match:return None,None
    constant=constants.get(int(match[2]))
    if not constant or 'literal_integer' not in constant:return None,constant
    return constant['literal_integer']*(-1 if match[1] else 1),constant


def observed_element_layout(element_type: int, parameter: int | None = None) -> dict:
    result=dict(handling='opaque',element_type=element_type,parameter=parameter)
    if element_type==0:return dict(result,handling='observed-Q-layout',family_class='bit',stride=1)
    if element_type in (2,17):return dict(result,handling='observed-Q-layout',family_class='word',stride=1)
    if element_type in (3,9,11,18):return dict(result,handling='observed-Q-layout',family_class='word',stride=2,
                                           rule_provenance='native-519F0-byte-size-and-A0B70-word-size')
    if element_type==15 and parameter is not None and 1<=parameter<=255:
        # Native 519F0 returns character limit + 1 bytes. 98E9F..98EA6
        # computes (bytes + 1) >> 1 for STRING array elements. Six lengths
        # (1,2,3,4,5,20) also have independent compile/query controls.
        return dict(result,handling='observed-Q-layout',family_class='word',stride=parameter//2+1,
                    rule_provenance='native-519F0-and-98E20',sampled_lengths=[1,2,3,4,5,20])
    return result


def observed_q_address(address: bytes) -> dict:
    """Spelling projection only; no claim of legal range or instruction use."""
    result=dict(handling='opaque',iec_address=address.decode('ascii',errors='backslashreplace'),
                range_validity='not checked',instruction_validity='not checked')
    bit_match=re.fullmatch(rb'%MX0\.([0-9]+)\.([0-9]+)',address)
    if bit_match and 0<=int(bit_match[2])<16:
        return dict(result,handling='observed-Q-address-spelling',family='D',number=int(bit_match[1]),
                    radix=10,family_class='bit',addressing='word-bit',bit=int(bit_match[2]))
    match=re.fullmatch(rb'%([IQ])X([0-9]+)',address)
    if match:
        return dict(result,handling='observed-Q-address-spelling',family='X' if match[1]==b'I' else 'Y',
                    number=int(match[2]),radix=16,family_class='bit')
    match=re.fullmatch(rb'%M([XWD])([0-9]+)\.([0-9]+)',address)
    if not match:return result
    size,group,number=match[1],int(match[2]),int(match[3])
    # Supported by the original structured Q address-query corpus, plus full
    # project template controls. Other groups/forms remain untouched.
    form={(b'X',0):('M',10,'bit'),(b'X',1):('B',16,'bit'),
          (b'W',0):('D',10,'word'),(b'D',0):('D',10,'word'),
          (b'W',1):('W',16,'word'),(b'W',4):('C',10,'word')}.get((size,group))
    if form is None:return result
    family,radix,unit=form
    return dict(result,handling='observed-Q-address-spelling',family=family,number=number,radix=radix,family_class=unit)


def format_device(family: str, number: int, radix: int) -> str:
    digits=format(number,'X') if radix==16 else str(number)
    if radix==16 and digits[0] in 'ABCDEF':digits='0'+digits
    return family+digits


def format_bound_device(base: dict, relative: int = 0) -> str:
    if base.get('addressing')=='word-bit':
        word_offset,bit=divmod(base['bit']+relative,16)
        return format_device(base['family'],base['number']+word_offset,base['radix'])+'.'+format(bit,'X')
    return format_device(base['family'],base['number']+relative,base['radix'])


def observed_embedded_assignment(raw: bytes, *, cpu: str) -> dict:
    """Tag 7 embeds a 25-byte operand, not the compact allocation record.

    ECCompiler 15.22 51680..516C9 tests tag 7, uppercases the descriptor code
    at UserInfo+21, and passes UserInfo+1 to the operand formatter. These exact
    scalar forms are compared against real SCPI native code; other flags stay
    opaque. A stored pin alias can belong only to the last expansion.
    """
    result=dict(handling='opaque',raw_hex=raw.hex())
    if (cpu!='Q03UDV' or len(raw)!=26 or raw[0]!=7 or any(raw[1:9])
            or any(raw[17:21]) or raw[25]):return result
    flags=int.from_bytes(raw[21:25],'little');number=int.from_bytes(raw[9:17],'little',signed=True)
    form={ord('M'):('M',10,'bit'),ord('D'):('D',10,'word'),ord('X'):('X',16,'bit'),
          0x404d:('SM',10,'bit')}.get(flags)
    if form is None or number<0:return result
    family,radix,unit=form
    return dict(result,handling='stored-embedded-operand',family=family,number=number,radix=radix,
                family_class=unit,descriptor_raw_hex=raw[1:].hex(),
                allocation_scope='stored pin alias; may reflect only the final call')


def resolve_operand(token: str, tables, allocated_tables, *, cpu: str) -> dict:
    result=dict(token=token,handling='opaque-preserved',operand=None,gaps=[])
    if cpu!='Q03UDV':return dict(result,gaps=['CPU layout not observed'])
    if re.fullmatch(r'##SM[0-9]+',token):return dict(result,handling='native-device-literal',operand=token[2:])
    member=re.fullmatch(r'K([0-9]+)\.(K([0-9]+)(?:\[.*\])?)',token)
    if member:
        instance=tables.component_at(int(member[1]));allocated_instance=allocated_tables.component_at(int(member[1]))
        if (instance.name_bytes!=allocated_instance.name_bytes or instance.instance_pou_offset is None
                or instance.instance_pou_offset!=allocated_instance.instance_pou_offset):
            return dict(result,gaps=['instance identity or instance POU reference unresolved'])
        pou=tables.pou_at(instance.instance_pou_offset)
        if int(member[3]) not in {c.record.table_offset for c in tables.components(pou)}:
            return dict(result,gaps=['member does not belong to the referenced instance POU'])
        child=resolve_operand(member[2],tables,allocated_tables,cpu=cpu)
        return dict(child,token=token,member_token=member[2],
                    qualified_component=instance.name_bytes.decode('ascii')+'.'+child.get('component',member[2]),
                    instance=dict(component_offset=int(member[1]),name=instance.name_bytes.decode('ascii'),
                                  pou_offset=instance.instance_pou_offset,pou_name=pou.name_bytes.decode('ascii'),
                                  raw_component_base64=base64.b64encode(instance.record.raw).decode(),
                                  raw_pou_base64=base64.b64encode(pou.record.raw).decode()),
                    member_binding_rule='member allocation in the exact instance POU; no name-based merging or base-address addition')
    constants={r.table_offset:constant_record(r) for r in tables.tables[5].records}
    cm=re.fullmatch(r'(-\s*)?C([0-9]+)',token)
    if cm:
        constant=constants.get(int(cm[2]));operand=integer_operand(constant,negate=bool(cm[1])) if constant else None
        return dict(result,handling='typed-constant-integer' if operand else 'opaque-preserved',constant=constant,operand=operand)
    match=re.fullmatch(r'K([0-9]+)(?:\[(.*)\])?',token)
    if not match:return dict(result,gaps=['expression outside observed component/constant grammar'])
    offset=int(match[1]);component=tables.component_at(offset);allocated=allocated_tables.component_at(offset)
    if (component.name_bytes!=allocated.name_bytes or tables.component_type(component)!=allocated_tables.component_type(allocated)):
        raise ValueError('source and allocated component identity differs')
    result.update(component=component.name_bytes.decode('ascii'),component_offset=offset,
                  component_raw_base64=base64.b64encode(component.record.raw).decode(),
                  allocated_component_raw_base64=base64.b64encode(allocated.record.raw).decode(),
                  allocation_raw_hex=allocated.user_info.hex())
    assignment=parse_compiler_assignment(allocated.user_info)
    address_offset=tables.component_address_offset(component)
    address=tables.address_at(address_offset) if address_offset>=0 else None
    if assignment.status=='decoded' and assignment.device_family in ('M','D','T'):
        base=dict(handling='stored-allocation',family=assignment.device_family,number=assignment.number,radix=10,
                  family_class='bit' if assignment.device_family=='M' else 'timer' if assignment.device_family=='T' else 'word',
                  reserved_count=assignment.reserved_count,stored_role=assignment.role)
    elif allocated.user_info[0]==7:
        base=observed_embedded_assignment(allocated.user_info,cpu=cpu)
    elif not any(allocated.user_info) and address and address.iec_address_bytes:
        base=observed_q_address(address.iec_address_bytes)
        base['record_raw_base64']=base64.b64encode(address.record.raw).decode()
    else:return dict(result,gaps=['component allocation or declared address unresolved'])
    result['base']=base
    if base['handling']=='opaque':return dict(result,gaps=['declared address form outside observed projection'])
    array_offset=tables.component_array_offset(component)
    if array_offset is None:
        if match[2] is not None:return dict(result,gaps=['index on non-array component'])
        return dict(result,handling='observed-scalar-binding',operand=format_bound_device(base))
    array=tables.array_at(array_offset)
    result['array']=dict(raw_base64=base64.b64encode(array.record.raw).decode(),element_type=array.element_type,
                         element_parameter=array.element_parameter,total_count=array.total_count,
                         dimensions=[dict(lower=d.lower,extent=d.extent) for d in array.dimensions])
    layout=observed_element_layout(array.element_type,array.element_parameter);result['layout']=layout
    if not array.count_matches_dimensions or not 1<=len(array.dimensions)<=2:
        return dict(result,gaps=['array shape outside observed consistent one/two dimensional layout'])
    if layout['handling']=='opaque' or layout['family_class']!=base['family_class']:
        return dict(result,gaps=['element layout or device unit unresolved'])
    if base.get('reserved_count') is not None and base['reserved_count']!=layout['stride']*array.total_count:
        return dict(result,gaps=['allocation size does not match typed array layout'])
    if match[2] is None:
        return dict(result,handling='observed-array-base-binding',operand=format_bound_device(base))
    index_tokens=split_operands(match[2]);indices=[]
    if len(index_tokens)!=len(array.dimensions):return dict(result,gaps=['array index rank differs'])
    linear=0
    for text,dimension in zip(index_tokens,array.dimensions):
        value,constant=integer_expression(text,constants)
        indices.append(dict(token=text,value=value,constant=constant))
        if value is None:
            return dict(result,handling='dynamic-index-expression',indices=indices,index_tokens=index_tokens,
                        access_lowering=('packed-BOOL-word-and-bit-selection-with-temporaries' if base.get('addressing')=='word-bit' else 'indexed-device-expression'),
                        gaps=['dynamic address computation requires lowered native instructions'])
        relative=value-dimension.lower
        if not 0<=relative<dimension.extent:return dict(result,indices=indices,gaps=['constant index outside declared bounds'])
        linear=linear*dimension.extent+relative
    relative=linear*layout['stride']
    return dict(result,handling='observed-array-element-binding',indices=indices,linear_element=linear,
                relative_device_units=relative,operand=format_bound_device(base,relative))


def resolve_simple_instance_label(token: str, instance_offset: int, tables, *, cpu: str, encoding: str) -> dict:
    """Resolve a stored simple-ladder label in one exact allocated FB instance.

    Decimal literal array subscripts are source text, not CG constant offsets.
    The result is a stored allocation candidate: repeated-call pin aliases must
    still be checked against the code region for that individual expansion.
    """
    result=dict(token=token,handling='opaque-preserved',operand=None,gaps=[])
    match=re.fullmatch(r"'([^\[\]]+)(?:\[(-?[0-9]+(?:\s*,\s*-?[0-9]+)*)\])?",token)
    if not match:return dict(result,gaps=['simple label outside observed literal-index grammar'])
    instance=tables.component_at(instance_offset)
    if instance.instance_pou_offset is None:return dict(result,gaps=['component is not an instance'])
    pou=tables.pou_at(instance.instance_pou_offset)
    # Native real source uses both rBuf and rbuf for the same exact member.
    # Fold ASCII bytes only; ambiguous names and other casing rules stay raw.
    candidates=[c for c in tables.components(pou) if c.name_bytes.lower()==match[1].encode(encoding).lower()]
    if len(candidates)!=1:return dict(result,gaps=['label is not unique in the exact instance POU'])
    component=candidates[0]
    bound=resolve_operand(f'K{instance_offset}.K{component.record.table_offset}',tables,tables,cpu=cpu)
    result=dict(bound,token=token,compiler_token=bound['token'],
                matched_member_spelling=component.name_bytes.decode(encoding),
                allocation_scope='stored instance allocation; call-site aliases require independent comparison')
    if match[2] is None or bound['operand'] is None:return result
    if 'array' not in bound:return dict(result,operand=None,handling='opaque-preserved',gaps=['index on non-array label'])
    indices=[int(v.strip()) for v in match[2].split(',')];dims=bound['array']['dimensions']
    if len(indices)!=len(dims):return dict(result,operand=None,handling='opaque-preserved',gaps=['literal index rank differs'])
    linear=0
    for value,dimension in zip(indices,dims):
        relative=value-dimension['lower']
        if not 0<=relative<dimension['extent']:
            return dict(result,operand=None,handling='opaque-preserved',gaps=['literal index outside declared bounds'])
        linear=linear*dimension['extent']+relative
    relative=linear*bound['layout']['stride']
    return dict(result,handling='observed-simple-label-element-binding',literal_indices=indices,
                linear_element=linear,relative_device_units=relative,
                operand=format_bound_device(bound['base'],relative))
