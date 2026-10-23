"""ST semantic binding over the existing GXW and PLC fact owners."""
from __future__ import annotations
from dataclasses import replace
import re
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
from plc.validation import (DEVICE_ADDRESS_RE, PLCJsonValidationError, is_bit_device,
    SUPPORTED_PLC_MODELS, parse_device_address, device_address_status, _validate_device_address,
    _validate_instruction_boundaries, _validate_source_operand_types, _validate_writable_device)
from plc.st_frontend import STUncovered, Declaration, Unit, parse_body, parse_initializer
from plc.instruction_definition import ValueType, DefinitionError
from gxw.declarations import _type_parts, CLASS_CODES, BASIC_TYPES
from gxw.models import GXWFormatError


def scalar_type(data_type):
    try:
        return ValueType.from_iec(data_type)
    except DefinitionError:
        return None


def type_family(value_type):
    if value_type is None:
        return 'unknown'
    if value_type.kind == 'bool':
        return 'bit'
    if value_type.unit == 'ms':
        return 'time'
    if value_type.kind == 'int':
        return 'word' if value_type.bits <= 16 else 'dword'
    return value_type.kind


def expression_numeric_type(node, symbols, *, reference):
    """One scalar promotion rule for static inspection and ScanMachine."""
    if node[0] == 'atom':
        literal = literal_value(node[1])
        if literal:
            return literal[1]
    if node[0] in {'atom', 'index', 'member'}:
        declaration = reference(node)
        if declaration:
            base, _ = symbols.parts(declaration.data_type)
            value_type = scalar_type(base)
            if value_type:
                return value_type
    if node[0] == 'unary':
        return expression_numeric_type(node[2], symbols, reference=reference)
    if node[0] == 'binary':
        left, right = [expression_numeric_type(item, symbols, reference=reference) for item in node[2:4]]
        if left.kind == 'real' or right.kind == 'real':
            return ValueType.from_iec('LREAL' if left.bits == 64 or right.bits == 64 else 'REAL')
        if left.kind == right.kind == 'int' and left.signed == right.signed:
            return ValueType.from_mapping(dict(kind='int', bits=max(left.bits, right.bits), signed=left.signed))
    if node[0] == 'call':
        definition, _ = symbols.callable(node[1])
        if definition and definition.return_type:
            value_type = scalar_type(definition.return_type)
            if value_type:
                return value_type
    from plc.instruction_definition import UnknownInstructionSemantics
    raise UnknownInstructionSemantics('st_numeric_promotion_unverified')


def literal_value(text):
    """Decode source literals; names return None, malformed literals raise."""
    token = text.upper().replace('_', '')
    declared, base, payload = None, None, text
    if '#' in token and token.split('#', 1)[0] not in {'2', '8', '16', 'T', 'TIME'}:
        prefix, token = token.split('#', 1)
        payload = text.split('#', 1)[1]
        declared = scalar_type(prefix)
        if declared is None:
            raise STUncovered('Unbound typed constant: ' + prefix)
    if token in {'TRUE', 'FALSE'}:
        value, kind = token == 'TRUE', ValueType.from_iec('BOOL')
    elif re.fullmatch(r'(TIME|T)#.+', token):
        duration = token.split('#', 1)[1]
        parts = re.findall(r'(\d+(?:\.\d+)?)(MS|D|H|M|S)', duration)
        if not parts or ''.join(a + b for a, b in parts) != duration:
            raise ValueError('Invalid TIME literal: ' + text)
        value = sum(float(a) * {'D': 86400000, 'H': 3600000, 'M': 60000, 'S': 1000, 'MS': 1}[b] for a, b in parts)
        if value != int(value) or not 0 <= value <= 2147483647:
            raise ValueError('TIME literal exceeds millisecond representation')
        value, kind = int(value), ValueType.from_iec('TIME')
    elif payload.startswith(("'", '"')):
        escapes = {'N': '\n', 'L': '\n', 'R': '\r', 'T': '\t', 'P': '\f', '$': '$', "'": "'", '"': '"'}
        def escape(match):
            word = match[1]
            if re.fullmatch('[0-9A-Fa-f]{2}|[0-9A-Fa-f]{4}', word):
                return chr(int(word, 16))
            if word.upper() not in escapes:
                raise ValueError('Unknown IEC string escape')
            return escapes[word.upper()]
        pattern = r'\$([0-9A-Fa-f]{4}|.)' if payload.startswith('"') else r'\$([0-9A-Fa-f]{2}|.)'
        value, kind = re.sub(pattern, escape, payload[1:-1]), ValueType.from_iec('WSTRING' if payload.startswith('"') else 'STRING')
    else:
        base = re.fullmatch(r'([+-]?)(2|8|16)#([0-9A-F]+)', token)
        if base:
            value = int(base[3], int(base[2])) * (-1 if base[1] == '-' else 1)
        elif re.fullmatch(r'H[0-9A-F]+', token):
            value = int(token[1:], 16)
        elif re.fullmatch(r'K[+-]?\d+', token):
            value = int(token[1:])
        elif re.fullmatch(r'[+-]?\d+', token):
            value = int(token)
        elif re.fullmatch(r'[+-]?(?:\d+\.\d*|\d*\.\d+|\d+[Ee][+-]?\d+)(?:[Ee][+-]?\d+)?', token):
            value = float(token)
        else:
            return None
        kind = ValueType.from_iec('REAL' if type(value) is float else 'INT' if -32768 <= value <= 32767 else 'DINT')
    if declared:
        if declared.kind != kind.kind or bool(declared.unit) != bool(kind.unit):
            raise ValueError('Typed constant value does not match its type')
        if declared.kind == 'int' and not base and not declared.bounds[0] <= value <= declared.bounds[1]:
            raise ValueError('Typed integer literal is out of range')
        kind = declared
    return kind.decode_bits(value), kind


class Symbols:
    """Bind one current POU using the existing GXW declaration projection."""
    def __init__(self, unit=None, *, parsed=None, project_context=None, callable_sources=None):
        self.unit = unit or Unit('__BODY__', 'program')
        self.context = project_context or {}
        selected = [pou for pou in self.context.get('pous', [])
                    if pou.get('program') == self.context.get('selected_source') and pou.get('kind') == 'function']
        if self.unit.name == '__BODY__' and self.unit.kind == 'program' and len(selected) == 1:
            # A saved FUNCTION stores only its implementation in the C1 source.
            # Its return slot is owned by the paired declaration header, rather
            # than a synthetic Program wrapper used by the body parser.
            source, = selected
            return_type = source.get('return_type')
            self.unit = replace(self.unit, name=source['name'], kind='function',
                return_type=return_type if return_type not in (None, "@'VOID'") else '')
        self.callable_sources = callable_sources
        self.locals, self.globals, self.pous, self.errors = {}, {}, {}, []
        self.types = dict(parsed.types if parsed else self.unit.types)
        self.options = self.context.get('configuration', {}).get('compile_options', {})
        source_names = {d.name.upper() for d in self.unit.declarations if not d.class_name.startswith('VAR_GLOBAL')}
        for structure in self.context.get('structures', []):
            if structure.get('status') != 'available':
                self.errors.append(('ST_STRUCTURE_COVERAGE', 'Ambiguous structure: ' + structure['name'], 'unverified'))
                continue
            self.types[structure['name'].upper()] = [self.row(r) for r in structure.get('members', [])]
        for table in self.context.get('globals', []):
            for row in table.get('declarations', []):
                self.add(self.globals, self.row(row))
        for declaration in (parsed.declarations if parsed else []):
            self.add(self.globals, declaration)
        for pou in self.context.get('pous', []):
            if pou.get('language') == 'st' and isinstance(pou.get('st_text'), str):
                try:
                    model = parse_body(pou['st_text'])
                except ValueError as error:
                    model = Unit(pou['name'], pou['kind'])
                    model.parse_diagnostic = str(error)
            else:
                model = Unit(pou['name'], pou.get('kind', 'unknown'))
            model.name, model.kind = pou['name'], pou.get('kind', 'unknown')
            model.declarations = [self.row(r) for r in pou.get('declarations', [])]
            model.return_type = pou.get('return_type', '')
            model.project_source = pou
            self.pous[model.name.upper()] = model
            if (pou.get('program') == self.context.get('selected_source') or pou['name'].upper() == self.unit.name.upper()):
                for declaration in model.declarations:
                    if declaration.name.upper() not in source_names:
                        self.add(self.locals, declaration)
        for pou in parsed.units if parsed else []:
            if pou.kind in {'function', 'function_block'}:
                if pou.name.upper() in self.pous:
                    self.errors.append(('ST_CALL_COVERAGE', 'Duplicate callable source: ' + pou.name, 'error'))
                self.pous[pou.name.upper()] = pou
        for declaration in self.unit.declarations:
            if declaration.class_name.startswith('VAR_GLOBAL'):
                self.add(self.globals, declaration)
            elif declaration.class_name == 'VAR_EXTERNAL':
                global_declaration = self.globals.get(declaration.name.upper())
                if global_declaration is None:
                    self.errors.append(('ST_EXTERNAL_COVERAGE', 'External declaration has no bound global: ' + declaration.name, 'unverified'))
                elif global_declaration.data_type.upper() != declaration.data_type.upper():
                    self.errors.append(('ST_DECLARATION', 'External declaration type differs from its global: ' + declaration.name, 'error'))
            else:
                self.add(self.locals, declaration)
        if self.unit.kind == 'function' and self.unit.return_type:
            self.add(self.locals, Declaration(self.unit.name, self.unit.return_type, 'RETURN'))

    @staticmethod
    def row(row):
        classes = {v: k for k, v in CLASS_CODES.items()}
        initial_text = row.get('initial_value') or ''
        initial = None
        if initial_text:
            try:
                initial = parse_initializer(initial_text, row['data_type'])
            except ValueError:
                pass
        return Declaration(row['name'], row['data_type'], row.get('class_name') or classes.get(row.get('class_code'), 'VAR'),
            initial, initial_text, row.get('device') or '')

    def add(self, table, declaration):
        name = declaration.name.upper()
        if name in table:
            self.errors.append(('ST_DECLARATION', 'Duplicate declaration: ' + declaration.name, 'error'))
        else:
            table[name] = declaration

    def lookup(self, name):
        name = name.upper()
        if self.callable_sources is not None:
            match = self.callable_sources.label(name)
            if match:
                return self.row({key: getattr(match[1], key) for key in ('name', 'data_type', 'class_code', 'device', 'initial_value')})
        if name in self.locals and name in self.globals and self.options.get('global_variable_hiding') is not True:
            raise ValueError('Ambiguous local/global declaration: ' + name)
        return self.locals.get(name) or self.globals.get(name)

    def parts(self, data_type, seen=()):
        # Keep the GX declaration parser as the type owner. Resolve named
        # constant bounds before handing it the normalized declaration text.
        text = data_type
        array = re.fullmatch(r'ARRAY\s*\[([^]]+)\]\s+OF\s+(.+)', text, re.I)
        if array:
            def bound(match):
                value = constant_expression(('atom', match[0].upper(), 0), self)
                if type(value) is not int:
                    raise STUncovered('Array bound is not a bound integer constant: ' + match[0])
                return str(value)
            dimensions = re.sub(r'\b[A-Za-z_]\w*\b', bound, array[1])
            text = 'ARRAY [' + dimensions + '] OF ' + array[2]
        base, bounds = _type_parts(text)
        if base.upper() in seen:
            raise ValueError('Recursive type alias: ' + base)
        alias = self.types.get(base.upper())
        if isinstance(alias, str):
            target, nested = self.parts(alias, (*seen, base.upper()))
            return target, bounds + nested
        return base, bounds

    def reference(self, node):
        if node[0] == 'atom':
            name = node[1]
            if DEVICE_ADDRESS_RE.fullmatch(name) and self.lookup(name.split('.')[0]) is None:
                return None
            parts = name.split('.')
            declaration = self.lookup(parts[0])
            for member in parts[1:]:
                declaration = self.member(declaration, member)
            return declaration
        if node[0] == 'member':
            return self.member(self.reference(node[1]), node[2])
        if node[0] == 'index':
            declaration = self.reference(node[1])
            if declaration is None:
                return None
            base, bounds = self.parts(declaration.data_type)
            if len(bounds) != len(node[2]):
                raise ValueError('Array index dimension mismatch')
            for index, (low, high) in zip(node[2], bounds):
                value = constant_expression(index, self)
                if value is not None and (type(value) is not int or not low <= value <= high):
                    raise ValueError('Array index out of bounds')
            return Declaration(declaration.name, base, declaration.class_name, address=declaration.address)
        return None

    def member(self, declaration, name):
        if declaration is None:
            return None
        base, bounds = self.parts(declaration.data_type)
        if bounds:
            raise ValueError('Array requires an index before member access')
        definition = self.pous.get(base.upper())
        members = definition.declarations if definition else self.types.get(base.upper(), [])
        if not isinstance(members, list):
            return None
        found = [row for row in members if row.name.upper() == name.upper()]
        if len(found) != 1:
            raise ValueError('Missing or ambiguous member: ' + name)
        if definition and found[0].class_name not in {'VAR_INPUT', 'VAR_INPUT_CONSTANT', 'VAR_OUTPUT', 'VAR_IN_OUT'}:
            raise ValueError('FB member is not exposed by its interface: ' + name)
        member = found[0]
        if 'CONSTANT' in declaration.class_name:
            from dataclasses import replace
            member = replace(member, class_name='VAR_CONSTANT')
        return member

    def callable(self, name):
        declaration = self.lookup(name)
        if declaration is not None and declaration.class_name == 'RETURN':
            # The implicit result slot shares its FUNCTION's spelling but is
            # never a function-block instance when that spelling is called.
            declaration = None
        definition = self.pous.get((declaration.data_type if declaration else name).upper())
        if definition is None and self.callable_sources is not None:
            target = declaration.data_type if declaration else name
            kind = 'FUNCTION_BLOCK' if declaration else 'FUNCTION'
            try:
                interface = self.callable_sources.st_interface(target, kind)
            except GXWFormatError:
                return None, declaration
            rows = {r['name'].upper(): r for r in interface['inputs'] + interface['outputs']}
            return_type = interface.get('return_type')
            if kind == 'FUNCTION' and return_type not in (None, "@'VOID'"):
                rows.pop(target.upper(), None)
            definition = Unit(target, kind.lower(), declarations=[self.row(dict(name=r['name'],
                data_type=r['declared_type'], class_code=r['class_code'])) for r in rows.values()],
                return_type=return_type if return_type not in (None, "@'VOID'") else '')
            definition.project_source = {'language': 'unknown', 'source_status': 'unavailable'}
            self.pous[target.upper()] = definition
        return definition, declaration


def constant_expression(node, symbols=None, seen=(), *, numeric_evaluator=None):
    if node is None:
        return None
    if node[0] == 'atom':
        result = literal_value(node[1])
        if result:
            return result[0]
        declaration = symbols.lookup(node[1]) if symbols else None
        if declaration and 'CONSTANT' in declaration.class_name and declaration.name.upper() not in seen:
            return constant_expression(declaration.initial, symbols, (*seen, declaration.name.upper()),
                                       numeric_evaluator=numeric_evaluator)
        return None
    if node[0] == 'unary':
        value = constant_expression(node[2], symbols, seen, numeric_evaluator=numeric_evaluator)
        return None if value is None else -value if node[1] == '-' else +value if node[1] == '+' else not value
    if node[0] == 'binary':
        left, right = (constant_expression(n, symbols, seen, numeric_evaluator=numeric_evaluator) for n in node[2:4])
        if left is None or right is None or type(left) not in {int, bool, float} or type(right) not in {int, bool, float}:
            return None
        op = node[1]
        if op in {'/', 'MOD'} and right == 0:
            raise ValueError('Constant division by zero')
        if op == '**':
            if abs(right) > 1024 or abs(left) > 1e30 or left < 0:
                return None
            try:
                if numeric_evaluator is not None:
                    return numeric_evaluator(node, left, right)
                return left ** right
            except (ValueError, OverflowError):
                return None
        if op in {'+', '-', '*', '/', 'MOD'}:
            quotient = (abs(left) // abs(right)) * (-1 if (left < 0) != (right < 0) else 1) if op in {'/', 'MOD'} else 0
            result = {'+': lambda: left + right, '-': lambda: left - right, '*': lambda: left * right,
                      '/': lambda: left / right if float in {type(left), type(right)} else quotient,
                      'MOD': lambda: left - quotient * right}[op]()
            if not -2**63 <= result < 2**64:
                raise STUncovered('Constant arithmetic exceeds the inspection bound')
            return result
    return None

def _literal(node):
    if node[0] == 'atom':
        text = node[1]
        if re.fullmatch(r'\d+', text):
            return 'K' + text
        base = re.fullmatch(r'(2|8|16)\#([0-9A-F_]+)', text, re.I)
        if base:
            try:
                return 'K' + str(int(base[2].replace('_', ''), int(base[1])))
            except ValueError:
                return None
        return text
    if node[0] == 'unary' and node[1] in {'+', '-'}:
        value = _literal(node[2])
        if value and re.fullmatch(r'K\d+', value):
            return 'K' + node[1] + value[1:]
    return None


class _DeviceInspector:
    def __init__(self, model, context=None, *, bound_device_types=None):
        self.model, self.findings, self.calls, self.devices = model, [], [], set()
        self.context, self.device_fact_seen = context, set()
        self.bound_device_types = bound_device_types or {}

    def partial_device_type(self, text):
        from plc.device_definitions import resolve_device_definition
        from plc.device_identity import canonical_reference_device
        fact = resolve_device_definition(text, self.model, context=self.context)
        if fact['definitions']:
            return fact.get('value_type') or 'unknown'
        return self.bound_device_types.get(canonical_reference_device(text), 'unknown')

    def device_fact(self, text, *, write=False):
        from plc.device_definitions import device_access_finding
        key = (text,write)
        if key not in self.device_fact_seen:
            self.device_fact_seen.add(key)
            finding = device_access_finding(text,self.model,write=write,context=self.context)
            if finding:
                self.findings.append({**finding,'offset':0})

    def finding(self, code, message, *, offset=0, severity='error'):
        self.findings.append({'code': code, 'severity': severity, 'message': message, 'offset': offset})

    def guard(self, function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PLCJsonValidationError as error:
            self.finding('ST_OPERAND', str(error))
            return None

    def value_type(self, node):
        kind = node[0]
        if kind == 'call':
            return self.call(node)
        if kind == 'atom':
            text = node[1].upper()
            if text in {'TRUE', 'FALSE'}:
                return 'bit'
            if text[0] in {'"', "'"}:
                return 'string'
            if re.fullmatch(r'K[+-]?\d+|H[0-9A-F]+|\d+(?:\.\d+)?', text) or '#' in text:
                if '#' in text and _literal(node) is None:
                    self.finding('ST_LITERAL', f'Invalid based integer {text}', offset=node[-1])
                return 'word'
            if DEVICE_ADDRESS_RE.fullmatch(text):
                self.devices.add(text)
                self.device_fact(text)
                status = device_address_status(text, self.model)
                if status == 'unverified':
                    self.finding('ST_DEVICE_COVERAGE', f'No address coverage for {self.model} device {text}', severity='unverified')
                    return self.partial_device_type(text)
                self.guard(_validate_device_address, text, '$.st_code', self.model)
                if status == 'invalid':
                    return 'unknown'
                return 'bit' if is_bit_device(text, self.model) else 'word'
            self.finding('ST_LABEL_COVERAGE', f'Label {text} has no bound declaration/type', offset=node[-1], severity='unverified')
            return 'unknown'
        if kind == 'unary':
            value = self.value_type(node[2])
            if node[1] == 'NOT':
                return value
            if value == 'bit':
                self.finding('ST_TYPE', 'Unary numeric operator requires numeric data', offset=node[-1])
            return value
        left, right = self.value_type(node[2]), self.value_type(node[3])
        if 'unknown' not in {left, right} and left != right:
            self.finding('ST_TYPE', f'{node[1]} combines incompatible operand types {left}/{right}', offset=node[-1])
        if node[1] in {'=', '<>', '<', '>', '<=', '>='}:
            return 'bit'
        if node[1] in {'+', '-', '*', '/', 'MOD'} and 'bit' in {left, right}:
            self.finding('ST_TYPE', 'Numeric operator requires numeric data', offset=node[-1])
        return left if left == right else 'unknown'

    def expect_bit(self, node):
        kind = self.value_type(node)
        if kind not in {'bit', 'unknown'}:
            self.finding('ST_TYPE', 'Condition/EN requires Boolean data', offset=node[-1])

    def call(self, node):
        _, name, args, offset = node
        form = DEFAULT_INSTRUCTION_REGISTRY.resolve_st_call(name, cpu=self.model)
        if form is None:
            native = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(name, cpu=self.model)
            self.finding('ST_CALL_FORM' if native else 'ST_CALL_COVERAGE',
                f'{name} is not a supported ST call for {self.model}', offset=offset,
                severity='error' if native else 'unverified')
            for arg in args:
                self.value_type(arg)
            return 'unknown'
        spec = form.spec
        profile_available = self.model in SUPPORTED_PLC_MODELS
        if not profile_available:
            self.finding('ST_DEVICE_COVERAGE', f'No operand/device profile for {self.model}', severity='unverified')
        enable = bool(spec.st_call.get('enable')) if spec.st_call else True
        operands = args[1:] if enable else args
        if enable and args:
            self.expect_bit(args[0])
        if (enable and not args) or not spec.accepts_arity(len(operands)):
            self.finding('ST_CALL_ARITY', f'{name} has an incorrect argument count', offset=offset)
            return 'unknown'
        if not spec.st_call:
            self.finding('ST_CALL_COVERAGE', f'{name} ST signature has not been source-verified for {self.model}', offset=offset, severity='unverified')
        values = []
        for index, arg in enumerate(operands):
            self.value_type(arg)
            literal = _literal(arg)
            if literal is None:
                self.finding('ST_EXPRESSION_COVERAGE', f'{name} operand {index} is an unbound expression', severity='unverified')
                literal = '__UNVERIFIED_EXPRESSION__'
            values.append(literal)
            if index in spec.write_indexes:
                if arg[0] == 'atom' and DEVICE_ADDRESS_RE.fullmatch(literal):
                    self.device_fact(literal,write=True)
                    if profile_available:
                        self.guard(_validate_writable_device, literal, '$.st_code', self.model, opcode=form.opcode)
                if not profile_available or device_address_status(literal, self.model) == 'unverified':
                    continue
                if arg[0] != 'atom' or parse_device_address(literal, self.model) is None:
                    self.finding('ST_WRITE_TARGET', f'{name} requires a device write target at operand {index}')
        if profile_available and '__UNVERIFIED_EXPRESSION__' not in values:
            self.guard(_validate_source_operand_types, spec, values, '$.st_code', self.model,
                       allow_unverified_devices=True)
            self.guard(_validate_instruction_boundaries, spec, values, '$.st_code', self.model)
        self.calls.append({'name': name, 'opcode': form.opcode, 'operands': values})
        return spec.st_call.get('returns', 'unknown') if spec.st_call else 'unknown'

    def body(self, statements):
        for statement in statements:
            if statement[0] == 'if':
                for condition, body in statement[1]:
                    self.expect_bit(condition)
                    self.body(body)
                self.body(statement[2])
            elif statement[0] == 'invoke':
                self.call(statement[1])
            else:
                target, value = statement[1:]
                left, right = self.value_type(target), self.value_type(value)
                if DEVICE_ADDRESS_RE.fullmatch(target[1]):
                    self.device_fact(target[1],write=True)
                if self.model in SUPPORTED_PLC_MODELS and DEVICE_ADDRESS_RE.fullmatch(target[1]):
                    self.guard(_validate_writable_device, target[1], '$.st_code', self.model)
                if 'unknown' not in {left, right} and left != right:
                    self.finding('ST_TYPE', f'Assignment types differ: {left}/{right}', offset=target[-1])


def bind_arguments(definition, args):
    formals = [d for d in definition.declarations if d.class_name in
        {'VAR_INPUT', 'VAR_INPUT_CONSTANT', 'VAR_IN_EXT', 'VAR_OUTPUT', 'VAR_IN_OUT'}]
    inputs = [d for d in formals if d.class_name != 'VAR_OUTPUT']
    by_name = {d.name.upper(): d for d in formals}
    bound, positional, named = {}, 0, False
    for argument in args:
        if argument[0] == 'named':
            named = True
            name, direction, value = argument[1:4]
        else:
            if named or positional >= len(inputs):
                raise ValueError('Positional arguments must precede named arguments and match the interface')
            name, direction, value = inputs[positional].name.upper(), ':=', argument
            positional += 1
        if name not in by_name:
            raise ValueError('Unknown formal parameter: ' + name)
        if name in bound:
            raise ValueError('Duplicate formal parameter: ' + name)
        formal = by_name[name]
        if direction == '=>' and formal.class_name != 'VAR_OUTPUT':
            raise ValueError('Output association requires a VAR_OUTPUT formal')
        bound[name] = (formal, value, direction)
    required = [d for d in inputs if definition.kind == 'function' or d.class_name == 'VAR_IN_OUT']
    if any(d.name.upper() not in bound for d in required):
        raise ValueError('Missing required input or VAR_IN_OUT parameter')
    return bound


class Inspector(_DeviceInspector):
    def __init__(self, model, context=None, *, bound_device_types=None, symbols=None):
        super().__init__(model, context, bound_device_types=bound_device_types)
        self.symbols = symbols or Symbols()
        self._checked, self._checking = set(), set()
        self.constraint_checks = []
        for code, message, severity in self.symbols.errors:
            self.finding(code, message, severity=severity)

    def finding(self, code, message, *, offset=0, severity='error'):
        row = {'code': code, 'severity': severity, 'message': message, 'offset': offset}
        if row not in self.findings:
            self.findings.append(row)

    def constraints(self, report, offset):
        for check in report.get('checks', []):
            if check not in self.constraint_checks:
                self.constraint_checks.append(check)
        for finding in report.get('findings', []):
            self.finding(finding['code'], finding['message'], offset=offset, severity=finding['severity'])

    def constraint_context(self):
        return {**self.symbols.context, 'project': self.symbols.context,
                'options': self.symbols.options, 'hardware': self.context or self.symbols.context.get('hardware_profile', {})}

    def native_constraints(self, node, form):
        from plc.instruction_constraints import check_instruction_constraints
        entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry(form.opcode, cpu=self.model) or {}
        source_signatures = [f['value'] for f in entry.get('facts', [])
                             if f['dimension'] == 'parameters.st_signature' and f['status'] == 'source_verified']
        described = DEFAULT_INSTRUCTION_REGISTRY.describe_call(form.opcode, cpu=self.model)
        signature = source_signatures[0] if len(source_signatures) == 1 else None
        parameters = signature['parameters'] if signature else described.get('st_parameters')
        if not parameters:
            return None
        formal = Unit(node[1], 'function', declarations=[Declaration(p['name'],
            'BOOL' if p.get('type') in {'BOOL', 'bit'} else 'REAL' if p.get('type') == 'REAL' else 'INT',
            'VAR_INPUT') for p in parameters])
        try:
            bound = bind_arguments(formal, node[2])
            if len(bound) != len(parameters):
                raise ValueError('Native ST signature requires all parameters')
        except ValueError as error:
            self.finding('ST_CALL_ARGUMENT', str(error), offset=node[-1])
            return signature
        values, tokens = {}, {}
        for index, p in enumerate(parameters):
            argument = bound[p['name'].upper()][1]
            if p['name'].upper() != 'EN':
                declaration = self.reference(argument) if argument[0] in {'atom', 'member', 'index'} else None
                tokens[p['name'].upper()] = ((getattr(declaration, 'address', '') or argument[1])
                    if argument[0] == 'atom' else None)
            values[p['name'].upper()] = self.constant(argument)
            if signature:
                actual = self.value_type(argument)
                expected = 'real' if p['type'] == 'REAL' else 'bit' if p['type'] == 'BOOL' else None
                if expected and actual not in {expected, 'unknown'}:
                    self.finding('ST_TYPE', 'Native ST parameter type differs: ' + p['name'], offset=argument[-1])
                if p.get('direction') == 'output':
                    if argument[0] not in {'atom', 'member', 'index'} or self.constant(argument) is not None:
                        self.finding('ST_WRITE_TARGET', 'Native ST output requires a writable reference', offset=argument[-1])
                    else:
                        self.reference(argument, write=True)
        # ST formal order differs from the native operand order (for example
        # SFTL(EN,s,n1,n2,d)). Bind shared operand rules by symbol, never index.
        spec = getattr(form, 'spec', None)
        source_orders = {tuple(f['value']['symbols']) for f in entry.get('facts', [])
                         if f['dimension'] == 'parameters.source_order' and f['status'] == 'source_verified'}
        order = spec.native_operand_order if spec else next(iter(source_orders)) if len(source_orders) == 1 else ()
        values['$OPERANDS'] = [tokens.get(name.upper()) for name in order]
        for index, name in enumerate(order):
            values['$' + str(index)] = values.get(name.upper())
        self.constraints(check_instruction_constraints(form.opcode, values, plc_model=self.model, language='st',
            version=self.symbols.context.get('software_version'), context=self.constraint_context()), node[-1])
        return signature

    def reference(self, node, *, write=False):
        try:
            declaration = self.symbols.reference(node)
        except (ValueError, GXWFormatError) as error:
            self.finding('ST_BINDING', str(error), offset=node[-1])
            return None
        if write and declaration and 'CONSTANT' in declaration.class_name:
            self.finding('ST_WRITE_TARGET', 'Constant declaration cannot be assigned', offset=node[-1])
        return declaration

    def declaration_type(self, declaration):
        try:
            base, bounds = self.symbols.parts(declaration.data_type)
        except ValueError as error:
            self.finding('ST_TYPE', str(error))
            return 'unknown'
        if bounds:
            return 'array:' + declaration.data_type.upper()
        family = type_family(scalar_type(base))
        if family == 'unknown' and base.upper() in self.symbols.types:
            return 'structure:' + base.upper()
        return family

    def value_type(self, node):
        kind = node[0]
        if kind == 'call':
            return self.call(node)
        if kind == 'named':
            return self.value_type(node[3])
        if kind == 'array_repeat':
            if self.value_type(node[1]) not in {'word', 'dword', 'unknown'}:
                self.finding('ST_TYPE', 'Array repetition requires an integer count', offset=node[-1])
            for value in node[2]:
                self.value_type(value)
            return 'array'
        if kind in {'array', 'structure'}:
            for value in node[1]:
                self.value_type(value[1] if kind == 'structure' else value)
            return kind
        if kind == 'atom':
            try:
                literal = literal_value(node[1])
            except (ValueError, STUncovered) as error:
                self.finding('ST_LITERAL', str(error), offset=node[-1], severity='unverified' if isinstance(error, STUncovered) else 'error')
                return 'unknown'
            if literal:
                return type_family(literal[1])
            if DEVICE_ADDRESS_RE.fullmatch(node[1]) and self.symbols.lookup(node[1].split('.')[0]) is None:
                return super().value_type(node)
        if kind in {'atom', 'index', 'member'}:
            if kind == 'index':
                for index in node[2]:
                    if self.value_type(index) not in {'word', 'dword', 'unknown'}:
                        self.finding('ST_TYPE', 'Array index requires an integer', offset=index[-1])
            declaration = self.reference(node)
            if declaration:
                return self.declaration_type(declaration)
            self.finding('ST_LABEL_COVERAGE', 'Label or member has no bound declaration/type', offset=node[-1], severity='unverified')
            return 'unknown'
        if kind == 'unary':
            family = self.value_type(node[2])
            if node[1] in {'+', '-'} and family not in {'word', 'dword', 'real', 'time', 'unknown'}:
                self.finding('ST_TYPE', 'Unary numeric operator requires numeric data', offset=node[-1])
            if node[1] == 'NOT' and family not in {'bit', 'word', 'dword', 'unknown'}:
                self.finding('ST_TYPE', 'NOT requires Boolean or bit-string data', offset=node[-1])
            return family
        left, right = self.value_type(node[2]), self.value_type(node[3])
        numeric = {'word', 'dword', 'real', 'time'}
        if left != right and 'unknown' not in {left, right} and not {left, right} <= numeric:
            self.finding('ST_TYPE', f'{node[1]} combines incompatible operand types {left}/{right}', offset=node[-1])
        if node[1] in {'=', '<>', '<', '>', '<=', '>='}:
            return 'bit'
        from plc.st_frontend import operator_binding
        rule = next((rule for rule in operator_binding()['types'].values() if node[1] in rule['operators']), None)
        families = {'ANY_REAL': {'real'}, 'ANY_INT': {'word', 'dword'}, 'ANY_NUM': {'word', 'dword', 'real'},
                    'ANY_BIT': {'bit', 'word', 'dword'}, 'ANY_SIMPLE': {'bit', 'word', 'dword', 'real', 'time', 'string'}}
        if rule:
            for index, actual in enumerate((left, right)):
                expected = rule['inputs'][min(index, len(rule['inputs']) - 1)]
                if actual != 'unknown' and actual not in families[expected]:
                    self.finding('ST_TYPE', f'{node[1]} operand {index + 1} requires {expected}', offset=node[-1])
        if node[1] == '**':
            from plc.instruction_constraints import check_language_constraints
            values = {'LEFT': self.constant(node[2]), 'RIGHT': self.constant(node[3])}
            from plc.instruction_definition import UnknownInstructionSemantics
            try:
                precision = expression_numeric_type(node[2], self.symbols, reference=self.reference).bits
            except UnknownInstructionSemantics:
                precision = None
            context = {**self.constraint_context(), 'numeric_precision': precision}
            self.constraints(check_language_constraints('ST.OPERATOR.POWER', values, plc_model=self.model,
                version=self.symbols.context.get('software_version'), context=context), node[-1])
            result = None
            if all(type(value) in {int, float} for value in values.values()) and values['LEFT'] > 0:
                from plc.instruction_constraints import evaluate_language_numeric
                try:
                    result = evaluate_language_numeric('ST.OPERATOR.POWER', values['LEFT'], values['RIGHT'],
                        plc_model=self.model, precision=precision, version=self.symbols.context.get('software_version'))
                except (ValueError, UnknownInstructionSemantics):
                    pass
            self.constraints(check_language_constraints('ST.OPERATOR.POWER', {**values, 'RESULT': result},
                plc_model=self.model, version=self.symbols.context.get('software_version'),
                context=context, phase='result'), node[-1])
        return 'unknown' if 'unknown' in {left, right} else 'real' if 'real' in {left, right} else 'dword' if 'dword' in {left, right} else left

    def constant(self, node):
        def numeric(expression, left, right):
            from plc.instruction_constraints import evaluate_language_numeric
            precision = expression_numeric_type(expression[2], self.symbols, reference=self.reference).bits
            return evaluate_language_numeric('ST.OPERATOR.POWER', left, right, plc_model=self.model,
                precision=precision, version=self.symbols.context.get('software_version'))
        try:
            return constant_expression(node, self.symbols, numeric_evaluator=numeric)
        except ValueError as error:
            self.finding('ST_CONSTANT', str(error), offset=node[-1],
                severity='unverified' if isinstance(error, STUncovered) else 'error')
            return None

    def initializer(self, data_type, value):
        base, bounds = self.symbols.parts(data_type)
        if bounds and value[0] == 'array':
            expected = 1
            for low, high in bounds:
                expected *= high - low + 1
            count = 0
            for item in value[1]:
                if item[0] == 'array_repeat':
                    repeats = self.constant(item[1])
                    if type(repeats) is not int or repeats < 0:
                        self.finding('ST_INITIALIZER', 'Array repetition requires a nonnegative constant', offset=item[-1])
                        continue
                    count += repeats * len(item[2])
                    elements = item[2]
                else:
                    count += 1
                    elements = [item]
                for element in elements:
                    family = self.declaration_type(Declaration('', base))
                    actual = self.value_type(element)
                    if family != actual and 'unknown' not in {family, actual} and not {family, actual} <= {'word', 'dword', 'real'}:
                        self.finding('ST_TYPE', 'Array initializer element type differs', offset=element[-1])
                    self.initializer(base, element)
            if count > expected:
                self.finding('ST_INITIALIZER', 'Too many array initializer elements', offset=value[-1])
            elif count < expected:
                self.finding('ST_INITIALIZER_COVERAGE', 'Partial array initialization needs native default-value evidence', offset=value[-1], severity='unverified')
        elif not bounds and value[0] == 'structure':
            members = self.symbols.types.get(base.upper())
            if not isinstance(members, list):
                self.finding('ST_TYPE', 'Structure initializer requires a structure destination', offset=value[-1])
                return
            seen = set()
            for name, item in value[1]:
                fields = [d for d in members if d.name.upper() == name]
                if len(fields) != 1 or name in seen:
                    self.finding('ST_INITIALIZER', 'Missing or duplicate structure member: ' + name, offset=item[-1])
                else:
                    field = fields[0]
                    family, actual = self.declaration_type(field), self.value_type(item)
                    if family != actual and 'unknown' not in {family, actual} and not {family, actual} <= {'word', 'dword', 'real'}:
                        self.finding('ST_TYPE', 'Structure initializer member type differs', offset=item[-1])
                    self.initializer(field.data_type, item)
                seen.add(name)
        elif not bounds:
            scalar = scalar_type(base)
            constant = self.constant(value)
            if scalar and scalar.kind == 'int' and type(constant) is int and not scalar.bounds[0] <= constant <= scalar.bounds[1]:
                self.finding('ST_LITERAL_RANGE', 'Constant exceeds destination type', offset=value[-1])
            length = re.fullmatch(r'STRING\s*\[(\d+)\]', data_type, re.I)
            if length and isinstance(constant, str) and len(constant) > int(length[1]):
                self.finding('ST_LITERAL_RANGE', 'String exceeds its declared length', offset=value[-1])

    def assignment(self, target, value, *, initializing=False):
        if target[0] not in {'atom', 'index', 'member'}:
            self.finding('ST_WRITE_TARGET', 'Assignment requires a writable reference', offset=target[-1])
            return
        declaration = self.reference(target, write=not initializing)
        left, right = self.value_type(target), self.value_type(value)
        if target[0] == 'atom' and DEVICE_ADDRESS_RE.fullmatch(target[1]):
            self.device_fact(target[1], write=True)
            if self.model in SUPPORTED_PLC_MODELS:
                self.guard(_validate_writable_device, target[1], '$.st_code', self.model)
        numeric = {'word', 'dword', 'real'}
        if left != right and 'unknown' not in {left, right} and not {left, right} <= numeric:
            if not (left.startswith('array:') and right == 'array' or left.startswith('structure:') and right == 'structure'):
                self.finding('ST_TYPE', f'Assignment types differ: {left}/{right}', offset=target[-1])
        if declaration:
            self.initializer(declaration.data_type, value)

    def call(self, node):
        _, name, args, offset = node
        try:
            definition, instance = self.symbols.callable(name)
        except (ValueError, GXWFormatError) as error:
            self.finding('ST_BINDING', str(error), offset=offset)
            return 'unknown'
        if definition:
            if definition.kind == 'function_block' and instance is None:
                self.finding('ST_FB_INSTANCE', 'FB invocation requires a declared instance', offset=offset)
                return 'unknown'
            if getattr(definition, 'parse_diagnostic', None):
                self.finding('ST_CALL_SOURCE_COVERAGE', 'Callable source could not be parsed: ' + definition.name + ': ' + definition.parse_diagnostic,
                    offset=offset, severity='unverified')
            try:
                bindings = bind_arguments(definition, args)
            except ValueError as error:
                self.finding('ST_CALL_ARGUMENT', str(error), offset=offset)
                return 'unknown'
            for formal, actual, direction in bindings.values():
                actual_type, formal_type = self.value_type(actual), self.declaration_type(formal)
                if actual_type != formal_type and 'unknown' not in {actual_type, formal_type} and not {actual_type, formal_type} <= {'word', 'dword', 'real'}:
                    self.finding('ST_TYPE', 'Callable parameter type mismatch: ' + formal.name, offset=actual[-1])
                if formal.class_name in {'VAR_OUTPUT', 'VAR_IN_OUT'}:
                    if actual[0] not in {'atom', 'index', 'member'} or actual[0] == 'atom' and literal_value(actual[1]) is not None:
                        self.finding('ST_WRITE_TARGET', 'Callable parameter requires a writable reference', offset=actual[-1])
                    else:
                        self.reference(actual, write=True)
                        if actual[0] == 'atom' and DEVICE_ADDRESS_RE.fullmatch(actual[1]):
                            self.guard(_validate_writable_device, actual[1], '$.st_code', self.model)
                    if formal.class_name == 'VAR_OUTPUT':
                        allowed = self.symbols.options.get('allow_fb_output_arguments')
                        from plc.instruction_constraints import check_language_constraints
                        self.constraints(check_language_constraints('ST.FB.OUTPUT_ARGUMENT', {'OPTION': allowed},
                            plc_model=self.model, context={**self.constraint_context(), 'using_output_argument': True}), offset)
            self.calls.append(dict(name=name, kind=definition.kind, definition=definition.name,
                parameters=list(bindings), source='current_declarations'))
            key = definition.name.upper()
            if key in self._checking:
                self.finding('ST_CALL_GRAPH_COVERAGE', 'Recursive callable requires native and bounded execution checks', offset=offset, severity='unverified')
            elif key not in self._checked and getattr(definition, 'project_source', {}).get('language', 'st') == 'st':
                self._checking.add(key)
                symbols = Symbols(definition)
                symbols.globals, symbols.types, symbols.pous, symbols.options = self.symbols.globals, self.symbols.types, self.symbols.pous, self.symbols.options
                child = Inspector(self.model, self.context, symbols=symbols)
                child._checking, child._checked = self._checking, self._checked
                child.declarations()
                child.body(definition.body)
                self.findings.extend({**finding, 'source_name': definition.name,
                    'line': definition.source[:finding['offset']].count('\n') + 1,
                    'column': len(definition.source[:finding['offset']].rsplit('\n', 1)[-1]) + 1} for finding in child.findings)
                self._checking.remove(key)
                self._checked.add(key)
            return self.declaration_type(Declaration('', definition.return_type)) if definition.kind == 'function' and definition.return_type else 'void'
        if instance:
            self.finding('ST_CALL_COVERAGE', 'Declared callable type has no bound interface: ' + instance.data_type, offset=offset, severity='unverified')
            return 'unknown'
        entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry(name, cpu=self.model) or {}
        availability = [f for f in entry.get('facts', []) if f['dimension'] == 'constraints.cpu_availability']
        if availability:
            from plc.instruction_constraints import check_fact_constraints
            self.constraints(check_fact_constraints(availability, {}, target=name, plc_model=self.model,
                language='st', version=self.symbols.context.get('software_version'),
                context=self.constraint_context()), offset)
        form = DEFAULT_INSTRUCTION_REGISTRY.resolve_st_call(name, cpu=self.model)
        identified = form or DEFAULT_INSTRUCTION_REGISTRY.resolve_fact_form(name, cpu=self.model)
        signature = self.native_constraints(node, identified) if identified else None
        if signature and not form:
            self.calls.append({'name': name, 'opcode': identified.opcode, 'source': 'source_signature', 'call_available': False})
            self.finding('ST_CALL_COVERAGE', name + ' is catalogued but has not been admitted as an executable call',
                         offset=offset, severity='unverified')
            return type_family(scalar_type(signature['returns']))
        if not form:
            return super().call(node)
        if not form.spec.st_call:
            return super().call(node)
        if any(arg[0] == 'named' for arg in args):
            signature = DEFAULT_INSTRUCTION_REGISTRY.describe_call(form.opcode, cpu=self.model).get('st_parameters', [])
            formal = Unit(name, 'function', declarations=[Declaration(p['name'], 'BOOL' if p['type'] == 'bit' else 'INT',
                'VAR_OUTPUT' if p['role'] == 'write' else 'VAR_IN_OUT' if p['role'] == 'read_write' else 'VAR_INPUT') for p in signature])
            try:
                bound = bind_arguments(formal, args)
                if len(bound) != len(signature):
                    raise ValueError('Native ST signature requires all parameters')
                args = [bound[p['name'].upper()][1] for p in signature]
            except ValueError as error:
                self.finding('ST_CALL_ARGUMENT', str(error), offset=offset)
                return 'unknown'
            node = ('call', name, args, offset)
        if any(self.reference(arg) is not None for arg in args if arg[0] in {'atom', 'index', 'member'}):
            enable = bool(form.spec.st_call.get('enable'))
            if enable and args:
                self.expect_bit(args[0])
            operands = args[1:] if enable else args
            if not form.spec.accepts_arity(len(operands)):
                self.finding('ST_CALL_ARITY', name + ' has an incorrect argument count', offset=offset)
            for index, argument in enumerate(operands):
                self.value_type(argument)
                if index in form.spec.write_indexes:
                    if self.reference(argument, write=True) is None and not (argument[0] == 'atom' and DEVICE_ADDRESS_RE.fullmatch(argument[1])):
                        self.finding('ST_WRITE_TARGET', name + ' requires a writable operand', offset=argument[-1])
            self.calls.append(dict(name=name, opcode=form.opcode, operands=[_literal(a) for a in operands]))
            self.finding('ST_CALL_OPERAND_COVERAGE', 'Label/expression operands require native address/type checks', offset=offset, severity='unverified')
            return form.spec.st_call.get('returns', 'unknown')
        return super().call(node)

    def declarations(self):
        for declaration in [*self.symbols.globals.values(), *self.symbols.locals.values()]:
            try:
                base, _ = self.symbols.parts(declaration.data_type)
                if base.upper() not in BASIC_TYPES and base.upper() not in self.symbols.types and base.upper() not in self.symbols.pous:
                    self.finding('ST_TYPE_COVERAGE', 'Declared type has no GX target evidence: ' + base, severity='unverified')
                if declaration.initial:
                    self.assignment(('atom', declaration.name.upper(), declaration.span.start if declaration.span else 0), declaration.initial, initializing=True)
                elif declaration.initial_text:
                    self.finding('ST_INITIALIZER_COVERAGE', 'Declaration initializer is not mapped: ' + declaration.name, severity='unverified')
                if declaration.class_name == 'VAR_TEMP':
                    self.finding('ST_DECLARATION_COVERAGE', 'GX temporary storage lifetime needs native evidence', severity='unverified')
                if declaration.class_name == 'VAR_RETAIN':
                    self.finding('ST_RETAIN_COVERAGE',
                        'Retained declaration storage is recognized; initialization, reset and power-cycle behavior need separate evidence',
                        severity='unverified')
            except ValueError as error:
                self.finding('ST_DECLARATION', str(error))

    def body(self, statements, loop_depth=0):
        for statement in statements:
            kind = statement[0]
            if kind == 'if':
                for condition, body in statement[1]:
                    self.expect_bit(condition)
                    self.body(body, loop_depth)
                self.body(statement[2], loop_depth)
            elif kind == 'case':
                selector_type = self.value_type(statement[1])
                if selector_type not in {'word', 'dword', 'unknown'}:
                    self.finding('ST_TYPE', 'CASE selector requires an integer')
                ranges = []
                for selections, body in statement[2]:
                    for lower, upper in selections:
                        low, high = self.constant(lower), self.constant(upper) if upper else self.constant(lower)
                        if type(low) is not int or type(high) is not int:
                            self.finding('ST_CASE_COVERAGE', 'CASE selection must resolve to integer constants', severity='unverified')
                        elif low > high or any(low <= b and a <= high for a, b in ranges):
                            self.finding('ST_CASE_SELECTION', 'Invalid or overlapping CASE selections')
                        else:
                            ranges.append((low, high))
                    self.body(body, loop_depth)
                self.body(statement[3], loop_depth)
            elif kind == 'for':
                self.assignment(statement[1], statement[2])
                for expression in statement[1:5]:
                    if self.value_type(expression) not in {'word', 'dword', 'unknown'}:
                        self.finding('ST_TYPE', 'FOR control and bounds require integers')
                if self.constant(statement[4]) == 0:
                    self.finding('ST_LOOP_STEP', 'FOR step cannot be zero')
                self.body(statement[5], loop_depth + 1)
            elif kind in {'while', 'repeat'}:
                self.expect_bit(statement[1])
                self.finding('ST_SCAN_TIME', 'Loop termination and scan time require bounded behavior verification', severity='warning')
                self.body(statement[2], loop_depth + 1)
            elif kind == 'exit':
                if not loop_depth:
                    self.finding('ST_EXIT_SCOPE', 'EXIT requires an enclosing loop')
            elif kind == 'return':
                if statement[1] is not None:
                    self.finding('ST_RETURN_DIALECT', 'GX RETURN has no value expression')
            elif kind == 'invoke':
                self.call(statement[1])
            elif kind == 'assign':
                self.assignment(statement[1], statement[2])
            else:
                self.finding('ST_STATEMENT_COVERAGE', 'Statement semantics not mapped: ' + kind, severity='unverified')

