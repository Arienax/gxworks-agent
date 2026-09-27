"""Compare bounded affine index expressions with decoded native arithmetic.

This is an offline comparison over integer arithmetic, not a PLC emulator.
Finite-width overflow, control flow, runtime index validity and aliasing of
overlapping physical registers are not established by an affine equality.
"""
from __future__ import annotations
import ast,re
from probe_gxw_compiler_constants import constant_record
from probe_gxw_operand_bindings import resolve_operand,split_operands


def affine(constant=0,coefficients=None):
    return dict(constant=constant,coefficients={k:v for k,v in sorted((coefficients or {}).items()) if v})


def add(a,b,sign=1):
    coefficients=dict(a['coefficients'])
    for k,v in b['coefficients'].items():coefficients[k]=coefficients.get(k,0)+sign*v
    return affine(a['constant']+sign*b['constant'],coefficients)


def multiply(a,b):
    if a['coefficients'] and b['coefficients']:raise ValueError('non-affine multiplication')
    if a['coefficients']:a,b=b,a
    return affine(a['constant']*b['constant'],{k:a['constant']*v for k,v in b['coefficients'].items()})


def source_expression(text,tables,allocated):
    constants={r.table_offset:constant_record(r) for r in tables.tables[5].records}
    def visit(node):
        if isinstance(node,ast.Name) and re.fullmatch(r'[CK][0-9]+',node.id):
            if node.id.startswith('C'):
                value=constants[int(node.id[1:])]
                if 'literal_integer' not in value:raise ValueError('noninteger constant in index')
                return affine(value['literal_integer'])
            binding=resolve_operand(node.id,tables,allocated,cpu='Q03UDV')
            if binding['operand'] is None:raise ValueError('unresolved index variable')
            return affine(coefficients={binding['operand']:1})
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,ast.USub):return multiply(affine(-1),visit(node.operand))
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,ast.UAdd):return visit(node.operand)
        if isinstance(node,ast.BinOp):
            a,b=visit(node.left),visit(node.right)
            if isinstance(node.op,ast.Add):return add(a,b)
            if isinstance(node.op,ast.Sub):return add(a,b,-1)
            if isinstance(node.op,ast.Mult):return multiply(a,b)
        raise ValueError('compiler index expression outside observed affine grammar')
    return visit(ast.parse(text.strip(),mode='eval').body)


def source_array_reference(token,tables,allocated):
    match=re.fullmatch(r'(K[0-9]+)\[(.*)\]',token)
    if not match:raise ValueError('not an indexed component')
    binding=resolve_operand(match[1],tables,allocated,cpu='Q03UDV')
    if binding['operand'] is None or 'array' not in binding:raise ValueError('array base/layout unresolved')
    indices=split_operands(match[2]);dimensions=binding['array']['dimensions']
    if len(indices)!=len(dimensions):raise ValueError('array rank differs')
    value=affine();terms=[]
    for index,dimension in zip(indices,dimensions):
        expression=source_expression(index,tables,allocated);terms.append(dict(token=index,expression=expression))
        relative=add(expression,affine(dimension['lower']),-1)
        value=add(multiply(value,affine(dimension['extent'])),relative)
    base=binding['base']
    return dict(token=token,binding=binding,index_expressions=terms,
                coordinate_unit='bit-within-word' if base.get('addressing')=='word-bit' else base['family_class'],
                base_coordinate=base['number']*16+base['bit'] if base.get('addressing')=='word-bit' else base['number'],
                relative_device_units=multiply(value,affine(binding['layout']['stride'])))


def decoded_index_arithmetic(records):
    state={};steps=[];gaps=[]
    def read(token):
        if re.fullmatch(r'K-?[0-9]+',token):return affine(int(token[1:]))
        if re.fullmatch(r'H[0-9A-F]+',token):return affine(int(token[1:],16))
        if not re.fullmatch(r'[DZ][0-9]+',token):raise ValueError('unobserved arithmetic operand '+token)
        return state.get(token,affine(coefficients={token:1}))
    for record in records:
        op=record.get('op');args=record.get('args',[])
        if op=='LD' and args==['SM400'] or op=='ANB' and not args:continue
        try:
            if op in ('MOV','DMOV') and len(args)==2:value=read(args[0]);destination=args[1]
            elif op in ('+','D+','-','D-','*','D*') and len(args) in (2,3):
                destination=args[-1]
                left,right=(read(args[0]),read(args[1])) if len(args)==3 else (read(args[1]),read(args[0]))
                value=add(left,right,-1 if op.endswith('-') else 1) if not op.endswith('*') else multiply(left,right)
            else:raise ValueError('unobserved arithmetic instruction '+str(op))
            if not re.fullmatch(r'[DZ][0-9]+',destination):raise ValueError('unobserved arithmetic destination')
            state[destination]=value
            steps.append(dict(record=record,destination=destination,expression=value,
                              stored_result_bits=32 if op.startswith('D') or op=='*' else 16))
        except ValueError as exc:gaps.append(str(exc));break
    return dict(state=state,steps=steps,gaps=gaps,
                scope='affine address arithmetic assuming no finite-width overflow; control flow, execution and runtime bounds not emulated')
