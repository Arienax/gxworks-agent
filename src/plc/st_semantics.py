# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""ST semantic binding over the existing GXW and PLC fact owners."""
from __future__ import annotations
from collections.abc import Mapping
from dataclasses import replace
from copy import copy
import re
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
from plc.validation import (DEVICE_ADDRESS_RE, PLCJsonValidationError, is_bit_device,
    SUPPORTED_PLC_MODELS, parse_device_address, device_address_status, _validate_device_address,
    _validate_instruction_boundaries, _validate_source_operand_types, _validate_writable_device)
from plc.st_frontend import STUncovered, Declaration, Unit, parse_body, parse_initializer
from plc.instruction_definition import ValueType, DefinitionError
from gxw.declarations import _type_parts, CLASS_CODES, BASIC_TYPES
from gxw.models import GXWFormatError

_STRUCTURED_EXPRESSION_OPERATORS = frozenset({'&', '**', '<', '<=', '<>', '=', '>', '>=',
                                              'MOD', 'XOR', 'NOT'})


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
            definition.project_source = {'language': 'unknown', 'source_status': 'unavailable',
                'library': interface.get('library'), 'source_kind': interface.get('source_kind'),
                'source_stream': interface.get('source_stream')}
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
        self.structured_call_result_types = {}
        self.assignment_targets = {}
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

    @staticmethod
    def _reviewed_structured_call_admitted(entry):
        details = entry.get('call_admission_details')
        if (entry.get('call_available') is not True
                or not isinstance(details, dict)
                or not isinstance(details.get('interfaces'), (list, tuple))
                or 'st' not in details['interfaces']):
            return False
        status = entry.get('support_status')
        structured = details.get('structured_call')
        if (status in {'reviewed_st_signature', 'reviewed_native_signature'}
                and isinstance(structured, dict) and structured.get('available') is True):
            return True
        # Compatibility for existing reviewed ST-only owner metadata. New
        # native+ST owners require the independent structured-call marker.
        return status == 'reviewed_st_signature' and structured is None

    @staticmethod
    def _reviewed_structured_expression_admitted(entry):
        details = entry.get('call_admission_details')
        marker = details.get('structured_expression') if isinstance(details, dict) else None
        return (entry.get('call_available') is True
            and isinstance(details, dict)
            and isinstance(details.get('interfaces'), (list, tuple))
            and 'st' in details['interfaces']
            and entry.get('support_status') in {
                'reviewed_st_expression', 'reviewed_st_signature', 'reviewed_native_signature'}
            and isinstance(marker, dict) and marker.get('available') is True
            and marker.get('call_kind') == 'expression')

    @staticmethod
    def _operator_expression_arity(node, operator):
        if node[0] == 'unary' and node[1] == operator:
            return 1
        if node[0] == 'binary' and node[1] == operator:
            return (Inspector._operator_expression_arity(node[2], operator)
                    + Inspector._operator_expression_arity(node[3], operator))
        return 1

    @staticmethod
    def _is_fx_k_expression_contract(contract):
        return any(source.get('manual_id') == 'fxcpu_application_k'
                   and source.get('revision') == 'K'
                   for source in contract.sources)

    def _structured_expression_contract(self, node):
        operator, offset = node[1], node[-1]
        expression_entry = getattr(DEFAULT_INSTRUCTION_REGISTRY, 'expression_entry', None)
        entry = (expression_entry(operator, cpu=self.model)
                 if callable(expression_entry) else None)
        if entry is None:
            # Older focused registries do not expose expression_entry yet.
            # Keep their exact-owner lookup behavior while the runtime registry
            # resolves admitted operator tokens through the source marker.
            entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry(operator, cpu=self.model)
        if not entry:
            self.finding('ST_EXPRESSION_COVERAGE',
                f'{operator} has no exact source-bound ST expression owner for {self.model}',
                offset=offset, severity='unverified')
            return None
        has_source_expression = any(fact.get('dimension') in {
                                        'parameters.language_expression',
                                        'parameters.operator_expression'}
                                    for fact in entry.get('facts', []))
        if not self._reviewed_structured_expression_admitted(entry):
            if has_source_expression:
                self.finding('ST_EXPRESSION_COVERAGE',
                    f'{operator} ST expression is not admitted from its reviewed source contract',
                    offset=offset, severity='unverified')
            else:
                self.finding('ST_EXPRESSION_COVERAGE',
                    f'{operator} has no reviewed ST expression contract for {self.model}',
                    offset=offset, severity='unverified')
            return None

        from plc.structured_expression_contract import resolve_structured_expression_contract
        feature = DEFAULT_INSTRUCTION_REGISTRY.language_feature(
            'ST.OPERATORS', language='st', cpu=self.model)
        result = resolve_structured_expression_contract(entry.get('facts', []),
            operator=operator, model=self.model,
            owner_opcode=entry.get('opcode'),
            version=self.symbols.context.get('software_version'),
            language_feature=feature if feature is not None else {'target': 'ST.OPERATORS', 'facts': []})
        if not result.accepted:
            reason = ', '.join(result.refusal_reasons)
            self.finding('ST_EXPRESSION_CONTRACT',
                f'{operator} reviewed ST expression contract is no longer valid: {reason}',
                offset=offset, severity='error')
            return None
        contract = result.contract
        is_prefix = node[0] == 'unary'
        if (contract.operator_form == 'prefix') != is_prefix:
            self.finding('ST_EXPRESSION_FORM',
                f'{operator} source contract requires {contract.operator_form} expression form',
                offset=offset, severity='error')
            return None
        if is_prefix:
            source = getattr(self.symbols.unit, 'source', None)
            if isinstance(source, str):
                cursor = offset + len(operator)
                while cursor < len(source) and source[cursor].isspace():
                    cursor += 1
                if cursor >= len(source) or source[cursor] != '(':
                    self.finding('ST_EXPRESSION_FORM',
                        f'{operator} source contract requires a parenthesized prefix operand',
                        offset=offset, severity='error')
                    return None
        owner_opcode = contract.owner_opcode or entry.get('opcode') or operator
        self._check_structured_call_applicability(
            contract, owner_opcode, offset, contract_kind='expression')
        arity = self._operator_expression_arity(node, operator)
        if not contract.minimum_operands <= arity <= contract.maximum_operands:
            self.finding('ST_EXPRESSION_ARITY',
                f'{operator} expression has {arity} operands; source allows '
                f'{contract.minimum_operands}..{contract.maximum_operands}',
                offset=offset, severity='error')
        return contract

    @staticmethod
    def _expression_type_matches(actual, expected):
        if actual == 'unknown':
            return True
        families = {
            'ANY_REAL': {'real'}, 'REAL': {'real'}, 'LREAL': {'real'},
            'ANY_INT': {'word', 'dword'}, 'INT': {'word'}, 'UINT': {'word'},
            'SINT': {'word'}, 'USINT': {'word'}, 'DINT': {'dword'},
            'UDINT': {'dword'}, 'LINT': {'dword'}, 'ULINT': {'dword'},
            'ANY_NUM': {'word', 'dword', 'real'},
            'ANY_BIT': {'bit', 'word', 'dword'}, 'BOOL': {'bit'}, 'BIT': {'bit'},
            'WORD': {'word'}, 'DWORD': {'dword'},
            'ANY_SIMPLE': {'bit', 'word', 'dword', 'real', 'time', 'string'},
            'TIME': {'time'}, 'STRING': {'string'},
        }
        return actual in families.get(str(expected).upper(), set())

    @staticmethod
    def _expression_result_family(contract, actual_operands):
        result_type = contract.result_type.upper()
        if result_type in {'BOOL', 'BIT'}:
            return 'bit'
        if result_type in {'REAL', 'LREAL', 'ANY_REAL'}:
            return 'real'
        allowed = {'ANY_INT': {'word', 'dword'},
                   'ANY_BIT': {'bit', 'word', 'dword'},
                   'ANY_NUM': {'word', 'dword', 'real'}}.get(result_type)
        if allowed is None:
            return 'unknown'
        known = [family for family in actual_operands if family != 'unknown']
        if known and all(family == known[0] for family in known) and known[0] in allowed:
            return known[0]
        return 'unknown'

    def _check_structured_call_applicability(self, contract, opcode, offset, *, contract_kind='call'):
        """Evaluate every retained contract scope and condition in this project."""
        from plc.instruction_constraints import _condition, _scope, version_condition_bounds
        from plc.instruction_definition import (DefinitionError, InstructionFactGroup,
                                                UnknownInstructionSemantics)

        version = self.symbols.context.get('software_version')
        context = {**self.constraint_context(), '$PLC_MODEL': self.model, '$LANGUAGE': 'st'}
        prefix = 'ST_EXPRESSION' if contract_kind == 'expression' else 'ST_CALL'
        noun = 'expression' if contract_kind == 'expression' else 'call'
        groups = {fact['id']: InstructionFactGroup.from_mapping(fact) for fact in contract.facts}
        if contract.conditions_evaluated:
            self.finding(prefix + '_CONDITION', f'{opcode} {noun} contract was already evaluated outside this project context',
                         offset=offset, severity='error')
            return False
        condition_rows = {}
        for row in contract.conditions:
            if isinstance(row, Mapping) and isinstance(row.get('fact_id'), str):
                scoped_fact = groups.get(row['fact_id'])
                if scoped_fact is None:
                    self.finding(prefix + '_CONDITION', f'{opcode} structured ST condition has no bound fact',
                                 offset=offset, severity='error')
                    return False
                condition_rows.setdefault(row['fact_id'], []).extend(row.get('conditions', ()))
                continue
            # Contract resolvers preserve source conditions in their original
            # form. Bind each one back to every fact that actually declares it;
            # this keeps the source association while allowing the shared
            # applicability evaluator to check raw condition objects.
            owners = [identity for identity, group in groups.items() if row in group.conditions]
            if not owners:
                self.finding(prefix + '_CONDITION', f'{opcode} structured ST condition has no bound fact',
                             offset=offset, severity='error')
                return False
            for identity in owners:
                condition_rows.setdefault(identity, []).append(row)

        for identity, group in groups.items():
            value = group.value
            states = value.get('model_states')
            model_state = states.get(self.model) if isinstance(states, dict) else None
            available = value.get('available')
            has_cpu_claim = ('available' in value or isinstance(states, dict) and self.model in states)
            is_cpu_applicability = (group.dimension == 'constraints.cpu_availability'
                                    or group.dimension == 'identity.applicability' and has_cpu_claim)
            conditional_cpu = (is_cpu_applicability and model_state == 'conditional'
                               and 'available' not in value)
            cpu_supported = ((available is None or available is True)
                             and (model_state is None or model_state == 'supported'))
            cpu_claim = is_cpu_applicability and has_cpu_claim and not cpu_supported
            scope_status = _scope(group, target=opcode, model=self.model, language='st', version=version)
            if scope_status != 'passed':
                if cpu_claim and scope_status == 'not_applicable':
                    continue
                severity = 'error' if scope_status == 'not_applicable' else 'unverified'
                self.finding(prefix + '_SCOPE', f'{opcode} structured ST {noun} scope is {scope_status}',
                             offset=offset, severity=severity)
                return False

            # Argument-dependent rule guards (for example EN) are evaluated
            # after binding, by check_fact_constraints with the actual values.
            if contract_kind == 'call' and group.dimension in {
                    'constraints.numeric_ranges', 'constraints.documented_requirements'}:
                continue

            matched_all = True
            unknown_reason = None
            conditional_version_guard_matched = False
            for condition in condition_rows.get(identity, ()):
                try:
                    matched = _condition(condition, {}, context)
                    version_bounds = version_condition_bounds(condition, model=self.model)
                except (DefinitionError, TypeError, ValueError, KeyError, OverflowError,
                        UnknownInstructionSemantics) as error:
                    unknown_reason = str(error)
                    continue
                if not matched:
                    matched_all = False
                    break
                if conditional_cpu and version_bounds is not None:
                    path, minimum, maximum, minimum_exclusive = version_bounds
                    conditional_version_guard_matched |= (
                        path == 'cpu_version' and not minimum_exclusive
                        and ((minimum is not None and maximum is None)
                             or (minimum is None and maximum is not None)))
            if not matched_all:
                if cpu_claim:
                    if conditional_cpu:
                        self.finding(prefix + '_CPU',
                            f'{opcode} CPU version is outside its documented range',
                            offset=offset, severity='error')
                        return False
                    continue
                self.finding(prefix + '_CONDITION', f'{opcode} structured ST condition does not apply',
                             offset=offset, severity='error')
                return False
            if unknown_reason is not None:
                self.finding(prefix + '_CONDITION',
                    f'{opcode} structured ST condition cannot be checked: {unknown_reason}',
                    offset=offset, severity='unverified')
                return False
            if cpu_claim:
                if available is False or model_state == 'unsupported':
                    self.finding(prefix + '_CPU', f'{opcode} is unavailable for the current CPU context',
                                 offset=offset, severity='error')
                elif conditional_cpu and conditional_version_guard_matched:
                    continue
                else:
                    self.finding(prefix + '_CPU', f'{opcode} CPU applicability is unresolved',
                                 offset=offset, severity='unverified')
                return False
        return True

    def _exact_iec_type(self, node):
        """Return an exact scalar IEC spelling when existing bindings establish it."""
        kind = node[0]
        if kind == 'call':
            return self.structured_call_result_types.get(node[-1])
        if kind == 'atom':
            token = node[1].strip()
            declaration = self.reference(node)
            if declaration is not None:
                try:
                    base, bounds = self.symbols.parts(declaration.data_type)
                except ValueError:
                    return None
                return base.upper() if not bounds and scalar_type(base) is not None else None
            if '#' in token:
                prefix = token.split('#', 1)[0].upper()
                if prefix not in {'2', '8', '16', 'T', 'TIME'} and scalar_type(prefix) is not None:
                    return prefix
            try:
                literal = literal_value(token)
            except (ValueError, STUncovered):
                return None
            if literal:
                value_type = literal[1]
                if value_type.kind == 'bool':
                    return 'BOOL'
                if value_type.kind == 'real':
                    return 'LREAL' if value_type.bits == 64 else 'REAL'
                if value_type.kind == 'int':
                    if value_type.unit == 'ms':
                        return 'TIME'
                    names = ({8: 'SINT', 16: 'INT', 32: 'DINT', 64: 'LINT'} if value_type.signed
                             else {8: 'USINT', 16: 'UINT', 32: 'UDINT', 64: 'ULINT'})
                    return names.get(value_type.bits)
            return None
        if kind in {'index', 'member'}:
            declaration = self.reference(node)
            if declaration is None:
                return None
            try:
                base, bounds = self.symbols.parts(declaration.data_type)
            except ValueError:
                return None
            return base.upper() if not bounds and scalar_type(base) is not None else None
        if kind == 'unary':
            return self._exact_iec_type(node[2])
        if kind == 'binary':
            try:
                exact = expression_numeric_type(node, self.symbols, reference=self.reference)
            except (ValueError, DefinitionError):
                return None
            left_exact, right_exact = self._exact_iec_type(node[2]), self._exact_iec_type(node[3])
            if left_exact and left_exact == right_exact and node[1] in {'+', '-', '*', '/', 'MOD'}:
                return left_exact
            if exact.kind == 'bool':
                return 'BOOL'
            if exact.kind == 'real':
                return 'LREAL' if exact.bits == 64 else 'REAL'
            if exact.kind == 'int':
                if exact.unit == 'ms':
                    return 'TIME'
                if exact.signed:
                    return {8: 'SINT', 16: 'INT', 32: 'DINT', 64: 'LINT'}.get(exact.bits)
        return None

    @staticmethod
    def _source_verifies_positional_output_order(contract):
        """Require explicit source evidence before binding an output by position.

        The general ST binder keeps VAR_OUTPUT out of positional arguments.
        This exception is limited to reviewed owner contracts whose source
        records either verify a complete documented call or cross-check the
        exact ST argument order and arity.
        """
        parameters = list(contract.parameters)
        if not parameters or not any(parameter.get('direction') == 'output' for parameter in parameters):
            return False
        if contract.variadic_parameter_name is not None:
            return contract.positional_arguments_verified
        if contract.call_kind == 'statement':
            return contract.positional_arguments_verified
        if contract.call_kind == 'instruction':
            return contract.positional_arguments_verified
        facts = [fact for fact in contract.facts
                 if fact.get('status') == 'source_verified'
                 and fact.get('verification') == 'source_checked']
        signatures = [fact for fact in facts if fact.get('dimension') == 'parameters.st_signature']
        if len(signatures) != 1 or not isinstance(signatures[0].get('value'), dict):
            return False
        value = signatures[0]['value']
        expected = [str(parameter.get('symbol') or parameter['name']).casefold()
                    for parameter in parameters]
        if (value.get('arguments_are_named') is False
                and value.get('call_form_verification') == 'documented_arguments_verified'):
            order = value.get('argument_order')
            return (isinstance(order, (list, tuple))
                    and [str(symbol).casefold() for symbol in order] == expected)

        orders = [fact for fact in facts if fact.get('dimension') == 'parameters.order'
                  and isinstance(fact.get('value'), dict)
                  and fact['value'].get('order_source') == 'ST_call_arguments']
        arities = [fact for fact in facts if fact.get('dimension') == 'parameters.arity'
                   and isinstance(fact.get('value'), dict)
                   and fact['value'].get('count_kind') == 'ST_call_arguments']
        if len(orders) != 1 or len(arities) != 1:
            return False
        order = orders[0]['value'].get('symbols')
        arity = arities[0]['value']
        return (isinstance(order, (list, tuple))
                and [str(symbol).casefold() for symbol in order] == expected
                and type(arity.get('minimum')) is int
                and type(arity.get('maximum')) is int
                and arity['minimum'] == arity['maximum'] == len(parameters))

    def _array_element_argument(self, actual, binding, arguments, *, label):
        reference = binding.get('array_element_reference')
        if not reference and binding.get('argument_passing') != 'array_element_reference':
            return True
        extent = reference.get('extent') if isinstance(reference, Mapping) else None
        if (not isinstance(extent, Mapping) or extent.get('unit') != 'logical_elements'
                or reference.get('start') != 'selected_array_element'):
            self.finding('ST_CALL_CONTRACT', label + ' has an unsupported array reference contract',
                         severity='unverified')
            return False
        if actual is None or actual[0] != 'index':
            self.finding('ST_ARRAY_REFERENCE', label + ' requires a declared array element reference')
            return False
        declaration = self.reference(actual[1])
        if declaration is None:
            self.finding('ST_ARRAY_REFERENCE', label + ' has no bound array declaration',
                         offset=actual[-1], severity='unverified')
            return False
        try:
            base, bounds = self.symbols.parts(declaration.data_type)
            expected = ValueType.from_mapping(reference['element_type'])
        except (ValueError, KeyError, DefinitionError) as error:
            self.finding('ST_ARRAY_REFERENCE', label + ': ' + str(error), offset=actual[-1])
            return False
        if scalar_type(base) != expected:
            self.finding('ST_TYPE', label + ' array element type differs from the source contract',
                         offset=actual[-1])
            return False
        if len(bounds) != 1 or len(actual[2]) != 1:
            self.finding('ST_ARRAY_REFERENCE', label + ' requires a verified contiguous array layout',
                         offset=actual[-1], severity='unverified')
            return False
        count_node = arguments.get(str(extent.get('parameter') or '').upper())
        count = self.constant(count_node) if count_node is not None else None
        if type(count) is not int:
            self.finding('ST_ARRAY_LENGTH', label + ' extent requires a bound integer constant',
                         offset=actual[-1], severity='error' if extent.get('constant_only') is True else 'unverified')
            return False
        allowed = extent.get('allowed_counts')
        if count < 1 or (allowed is not None and count not in allowed):
            self.finding('ST_ARRAY_LENGTH', label + ' extent is outside the source allowed counts',
                         offset=actual[-1])
            return False
        start = self.constant(actual[2][0])
        if type(start) is not int:
            self.finding('ST_ARRAY_EXTENT', label + ' array start/extent cannot be established',
                         offset=actual[-1], severity='unverified')
            return False
        low, high = bounds[0]
        if start < low or start + count - 1 > high:
            self.finding('ST_ARRAY_EXTENT', label + ' extent exceeds the declared array', offset=actual[-1])
            return False
        return True

    def _start_data_reference_argument(self, actual, binding, *, label):
        """Bind the source table's address input without treating it as a cast."""
        if actual[0] not in {'atom', 'index', 'member'}:
            self.finding('ST_REFERENCE_SOURCE_UNRESOLVED',
                         label + ' needs a source-supported data reference; expressions are not established',
                         offset=actual[-1], severity='unverified')
            return False
        declaration = self.reference(actual)
        root = actual
        while root[0] in {'index', 'member'}:
            root = root[1]
        root_name = root[1].split('.')[0].upper() if root[0] == 'atom' else ''
        is_global = (root_name in self.symbols.globals and root_name not in self.symbols.locals)
        direct_counter = False
        bounds = ()
        if declaration is not None:
            if 'CONSTANT' in declaration.class_name:
                self.finding('ST_REFERENCE_SOURCE_UNRESOLVED', label + ' constant-address input is not established',
                             offset=actual[-1], severity='unverified')
                return False
            try:
                base, bounds = self.symbols.parts(declaration.data_type)
            except ValueError as error:
                self.finding('ST_TYPE', str(error), offset=actual[-1])
                return False
            base = base.upper()
            is_global |= declaration.class_name.startswith('VAR_GLOBAL')
        elif actual[0] == 'atom' and DEVICE_ADDRESS_RE.fullmatch(actual[1]):
            from plc.device_profiles import native_word_value_bits
            from plc.validation import parse_device_address, is_bit_device
            parsed = parse_device_address(actual[1], self.model)
            if parsed is None:
                self.finding('ST_REFERENCE_SOURCE_UNRESOLVED', label + ' device type is not established',
                             offset=actual[-1], severity='unverified')
                return False
            bits = native_word_value_bits(parsed[0], parsed[1], self.model)
            direct_counter = parsed[0] == 'C' and bits == 32
            # C/T can also name contacts. This data-reference position uses
            # their numeric device value width, not their contact Boolean.
            base = ('DINT' if direct_counter else
                    'INT' if parsed[0] in {'C', 'T'} and bits == 16 else
                    'BOOL' if is_bit_device(actual[1], self.model) else
                    'INT' if bits == 16 else '')
        else:
            self.finding('ST_REFERENCE_SOURCE_UNRESOLVED',
                         label + ' has no bound data reference; literals are not established',
                         offset=actual[-1], severity='unverified')
            return False
        allowed = binding['array_element_types'] if bounds else binding['scalar_types']
        if base not in allowed:
            uncertain = 'BIT_STRING' in binding['source_type_domains'] and (
                base in {'BOOL', 'WORD', 'DWORD'} or bool(bounds))
            self.finding('ST_REFERENCE_SOURCE_UNRESOLVED' if uncertain else 'ST_TYPE',
                         label + ' input type is not established by its own source table',
                         offset=actual[-1], severity='unverified' if uncertain else 'error')
            return False
        requirement = binding.get('label_requirement')
        if requirement and (bounds or base in {'DINT', 'DWORD', 'REAL', 'TIME'}):
            if not is_global and not (direct_counter and requirement['direct_32bit_counter']):
                self.finding('ST_REFERENCE_GLOBAL_LABEL',
                             label + ' requires a global label for array or 32-bit data, or a direct 32-bit counter',
                             offset=actual[-1])
                return False
        return True

    def _structured_call(self, node, entry, *, statement_context=False, instance=None):
        from plc.structured_call_contract import resolve_structured_call_contract

        _, name, args, offset = node
        resolution = resolve_structured_call_contract(entry.get('facts', []), opcode=entry.get('opcode', name),
            model=self.model, version=self.symbols.context.get('software_version'))
        if not resolution.accepted:
            reason = ','.join(resolution.refusal_reasons) or 'source_contract_unavailable'
            self.finding('ST_CALL_CONTRACT', f'{name} structured ST contract refused: {reason}',
                         offset=offset, severity='error')
            return 'unknown'
        contract = resolution.contract
        if contract.instance_required:
            if instance is None or instance.data_type.upper() != contract.opcode.upper():
                self.finding('ST_FB_INSTANCE', 'FB invocation requires a declared instance of ' + contract.opcode,
                             offset=offset)
                return 'unknown'
            if not statement_context:
                self.finding('ST_CALL_CONTEXT', 'A function-block instance has no assignable return value',
                             offset=offset)
                return 'unknown'
            sources = self.symbols.callable_sources
            if (sources is None or not sources.st_instance_contract_interface(
                    contract.opcode, contract.as_mapping())):
                self.finding('ST_CALL_CONTRACT', 'The current project FB interface differs from its source contract',
                             offset=offset)
                return 'unknown'
            if any(argument[0] != 'named' for argument in args):
                self.finding('ST_CALL_ARGUMENT', 'This FB source documents named instance arguments', offset=offset)
                return 'unknown'
        if contract.call_kind == 'statement' and not statement_context:
            self.finding('ST_CALL_CONTEXT',
                f'{name} is documented as a statement and has no assignable function result',
                offset=offset, severity='error')
            return 'unknown'
        if contract.call_kind == 'instruction' and statement_context:
            self.finding('ST_CALL_CONTEXT',
                f'{name} is documented with an assigned BOOL result and cannot be used as a bare statement',
                offset=offset, severity='error')
            return 'unknown'
        if not self._check_structured_call_applicability(contract, contract.opcode, offset):
            return 'unknown'

        variadic = contract.variadic_parameter_name is not None
        source_parameters = list(contract.parameters)
        if variadic:
            if any(argument[0] == 'named' for argument in args):
                self.finding('ST_CALL_ARGUMENT',
                    'Variadic structured ST calls require the documented positional form', offset=offset)
                return 'unknown'
            positions = [index for index, parameter in enumerate(source_parameters)
                         if parameter['name'].casefold() == contract.variadic_parameter_name.casefold()]
            if not positions:
                self.finding('ST_CALL_CONTRACT', f'{name} has an invalid variadic source binding', offset=offset)
                return 'unknown'
            optional_positions = [index for index, parameter in enumerate(source_parameters)
                                  if parameter.get('optional') is True]
            if len(positions) == 1 and optional_positions:
                start = positions[0]
                optional_start, optional_end = optional_positions[0], optional_positions[-1] + 1
                if (optional_positions != list(range(optional_start, optional_end))
                        or optional_start <= start
                        or any(parameter.get('direction') != 'input'
                               for parameter in source_parameters[start:optional_end])
                        or any(parameter.get('direction') != 'output'
                               for parameter in source_parameters[optional_end:])):
                    self.finding('ST_CALL_CONTRACT', f'{name} has an invalid optional trailing source binding',
                                 offset=offset)
                    return 'unknown'
                variable_parameters = source_parameters[start:optional_end]
                fixed_prefix = source_parameters[:start]
                fixed_suffix = source_parameters[optional_end:]
                fixed_count = len(fixed_prefix) + len(fixed_suffix)
                variadic_count = len(args) - fixed_count
                if (contract.minimum_variadic_arguments != optional_start - start
                        or contract.maximum_variadic_arguments != optional_end - start):
                    self.finding('ST_CALL_CONTRACT', f'{name} optional parameter range differs from its source signature',
                                 offset=offset)
                    return 'unknown'
                if (type(variadic_count) is not int
                        or not contract.minimum_variadic_arguments <= variadic_count
                            <= contract.maximum_variadic_arguments):
                    self.finding('ST_CALL_ARITY',
                        f'{name} variadic input count must be between '
                        f'{contract.minimum_variadic_arguments} and {contract.maximum_variadic_arguments}',
                        offset=offset)
                    return 'unknown'
                source_parameters = (fixed_prefix + variable_parameters[:variadic_count]
                                     + fixed_suffix)
            else:
                if (len(positions) < 2
                        or positions != list(range(positions[0], positions[-1] + 1))
                        or optional_positions):
                    self.finding('ST_CALL_CONTRACT', f'{name} has an invalid repeated source binding', offset=offset)
                    return 'unknown'
                fixed_count = len(source_parameters) - len(positions)
                variadic_count = len(args) - fixed_count
                if (type(contract.minimum_variadic_arguments) is not int
                        or type(contract.maximum_variadic_arguments) is not int
                        or not contract.minimum_variadic_arguments <= variadic_count
                            <= contract.maximum_variadic_arguments):
                    self.finding('ST_CALL_ARITY',
                        f'{name} variadic input count must be between '
                        f'{contract.minimum_variadic_arguments} and {contract.maximum_variadic_arguments}',
                        offset=offset)
                    return 'unknown'
                template = source_parameters[positions[0]]
                source_parameters = (source_parameters[:positions[0]]
                                     + [template] * variadic_count
                                     + source_parameters[positions[-1] + 1:])

        formal_names = []
        used_formal_names = set()
        for index, parameter in enumerate(source_parameters):
            formal_name = parameter['name']
            suffix = 2
            while formal_name.upper() in used_formal_names:
                formal_name = f"{parameter['name']}_{suffix}"
                suffix += 1
            used_formal_names.add(formal_name.upper())
            formal_names.append(formal_name)

        declarations = []
        for parameter, formal_name in zip(source_parameters, formal_names):
            class_name = {'input': 'VAR_INPUT', 'output': 'VAR_OUTPUT', 'in_out': 'VAR_IN_OUT'}[
                parameter['direction']]
            declarations.append(Declaration(formal_name, parameter['type'], class_name))
        definition = Unit(name, 'function', declarations=declarations)
        positional_output_call = (bool(args) and all(argument[0] != 'named' for argument in args)
                                  and self._source_verifies_positional_output_order(contract))
        try:
            if variadic and any(parameter['direction'] == 'output'
                                for parameter in source_parameters):
                if (not positional_output_call
                        or len(args) != len(source_parameters)):
                    raise ValueError('Structured ST signature requires every declared parameter')
                formals = {declaration.name.upper(): declaration for declaration in declarations}
                bindings = {formal_name.upper(): (formals[formal_name.upper()], argument, ':=')
                            for formal_name, argument in zip(formal_names, args)}
            elif variadic:
                bindings = bind_arguments(definition, args)
            elif positional_output_call:
                if len(args) != len(contract.parameters):
                    raise ValueError('Structured ST signature requires every declared parameter')
                formals = {declaration.name.upper(): declaration for declaration in declarations}
                bindings = {parameter['name'].upper():
                            (formals[parameter['name'].upper()], argument, ':=')
                            for parameter, argument in zip(contract.parameters, args)}
            else:
                bindings = bind_arguments(definition, args)
            if set(bindings) != {formal_name.upper() for formal_name in formal_names}:
                raise ValueError('Structured ST signature requires every declared parameter')
            binding_rows = []
            for parameter, formal_name in zip(source_parameters, formal_names):
                formal, actual, direction = bindings[formal_name.upper()]
                binding_rows.append((parameter, formal, actual, direction))
                if (formal.class_name == 'VAR_OUTPUT' and direction != '=>'
                        and not contract.instance_required
                        and not positional_output_call):
                    raise ValueError('Output parameter requires => association: ' + formal.name)
                if formal.class_name != 'VAR_OUTPUT' and direction == '=>':
                    raise ValueError('=> association requires an output parameter: ' + formal.name)
        except ValueError as error:
            self.finding('ST_CALL_ARGUMENT', str(error), offset=offset)
            return 'unknown'

        argument_nodes = {parameter['name'].upper(): actual for parameter, _, actual, _ in binding_rows}
        for parameter, _, actual, _ in binding_rows:
            if not self._array_element_argument(actual, parameter, argument_nodes,
                                                 label=name + '.' + parameter['name']):
                return 'unknown'
        if contract.return_binding and not self._array_element_argument(
                self.assignment_targets.get(offset), contract.return_binding, argument_nodes,
                label=name + ' result'):
            return 'unknown'

        generic_families = {
            'ANY_REAL': {'real'},
            'ANY_INT': {'word', 'dword'},
            'ANY16': {'word'},
            'ANY32': {'dword'},
            'ANY_NUM': {'word', 'dword', 'real'},
            'ANY_BIT': {'bit', 'word', 'dword'},
            'ANY_SIMPLE': {'bit', 'word', 'dword', 'real', 'time', 'string'},
        }
        unified_generics = {}
        for parameter, formal, actual, _ in binding_rows:
            source_name = contract.variadic_parameter_name if (
                variadic and parameter['name'].casefold()
                    == contract.variadic_parameter_name.casefold()) else parameter['name']
            reference_binding = parameter.get('input_reference')
            if reference_binding:
                if not self._start_data_reference_argument(actual, reference_binding,
                                                          label=name + '.' + source_name):
                    return 'unknown'
                self.value_type(actual)
                continue
            actual_family = self.value_type(actual)
            expected = str(parameter['type']).upper()
            verified_branches = parameter.get('source_verified_type_branches')
            if verified_branches:
                exact_branch = self._exact_iec_type(actual)
                if (exact_branch is None
                        or exact_branch.upper() not in {item.upper() for item in verified_branches}):
                    self.finding('ST_TYPE_SOURCE_UNRESOLVED',
                                 f'{name} parameter {source_name} has source-verified branches '
                                 + ', '.join(verified_branches)
                                 + '; this input type remains unresolved',
                                 offset=actual[-1], severity='unverified')
                    return 'unknown'
            if expected in generic_families:
                if actual_family not in generic_families[expected] and actual_family != 'unknown':
                    self.finding('ST_TYPE', f'{name} parameter {source_name} requires {expected}',
                                 offset=actual[-1])
                    return 'unknown'
                exact = self._exact_iec_type(actual)
                if exact is None:
                    self.finding('ST_TYPE', f'{name} parameter {source_name} needs an exact bound IEC type',
                                 offset=actual[-1], severity='unverified')
                    return 'unknown'
                if actual_family == 'unknown':
                    return 'unknown'
                if expected == 'ANY16':
                    exact_type = scalar_type(exact)
                    if (exact_type is None or exact_type.kind != 'int'
                            or exact_type.bits != 16 or exact_type.unit):
                        self.finding('ST_TYPE', f'{name} parameter {source_name} requires ANY16 (a 16-bit IEC integer type)',
                                     offset=actual[-1])
                        return 'unknown'
                if expected == 'ANY32' and exact.upper() not in {'DINT', 'UDINT', 'DWORD'}:
                    self.finding('ST_TYPE', f'{name} parameter {source_name} requires ANY32 (a 32-bit integer or bit-string type)',
                                 offset=actual[-1])
                    return 'unknown'
                alternatives = parameter.get('type_alternatives')
                if (alternatives and exact.upper() not in {str(item).upper() for item in alternatives}):
                    self.finding('ST_TYPE', f'{name} parameter {source_name} requires one of '
                                 + ', '.join(str(item) for item in alternatives), offset=actual[-1])
                    return 'unknown'
                # In the documented M statement, ANY32 constrains each input
                # independently; the source does not bind S1 and S2 to one
                # exact IEC type. Keep ordinary function generic unification.
                independent_statement_any32 = (expected == 'ANY32'
                    and contract.call_kind == 'statement' and contract.returns is None)
                generic_key = (expected + ':' + formal.name.upper()
                               if independent_statement_any32 else expected)
                prior = unified_generics.setdefault(generic_key, exact)
                if prior != exact:
                    self.finding('ST_TYPE', f'{name} generic {expected} parameters have different exact types',
                                 offset=actual[-1])
                    return 'unknown'
            else:
                exact = self._exact_iec_type(actual)
                alternatives = parameter.get('type_alternatives')
                if alternatives:
                    if (not isinstance(alternatives, (list, tuple))
                            or any(not isinstance(item, str) for item in alternatives)):
                        self.finding('ST_CALL_CONTRACT',
                                     f'{name} parameter {source_name} has invalid type alternatives',
                                     offset=actual[-1])
                        return 'unknown'
                    allowed_types = {item.upper() for item in alternatives}
                    if exact is None:
                        self.finding('ST_TYPE', f'{name} parameter {source_name} needs an exact bound IEC type '
                                     'to select a documented alternative',
                                     offset=actual[-1], severity='unverified')
                        return 'unknown'
                    if exact.upper() not in allowed_types:
                        self.finding('ST_TYPE', f'{name} parameter {source_name} requires one of '
                                     + ', '.join(alternatives), offset=actual[-1])
                        return 'unknown'
                else:
                    expected_family = type_family(scalar_type(expected))
                    if actual_family != expected_family and actual_family != 'unknown':
                        self.finding('ST_TYPE', f'{name} parameter type differs: {source_name}', offset=actual[-1])
                        return 'unknown'
                    if exact is not None and scalar_type(expected) is not None:
                        actual_scalar, expected_scalar = scalar_type(exact), scalar_type(expected)
                        if actual_scalar != expected_scalar:
                            self.finding('ST_TYPE', f'{name} parameter type differs: {source_name}', offset=actual[-1])
                            return 'unknown'
                    elif actual_family == 'unknown':
                        return 'unknown'

            if formal.class_name in {'VAR_OUTPUT', 'VAR_IN_OUT'}:
                if contract.instance_required and formal.class_name == 'VAR_OUTPUT':
                    from plc.instruction_constraints import check_language_constraints
                    self.constraints(check_language_constraints('ST.FB.OUTPUT_ARGUMENT',
                        {'OPTION': self.symbols.options.get('allow_fb_output_arguments')},
                        plc_model=self.model, context={**self.constraint_context(), 'using_output_argument': True}),
                        offset)
                if actual[0] not in {'atom', 'index', 'member'}:
                    self.finding('ST_WRITE_TARGET', f'{name} parameter {source_name} requires a writable reference',
                                 offset=actual[-1])
                    return 'unknown'
                declaration = self.reference(actual, write=True)
                if declaration is None:
                    if actual[0] != 'atom' or not DEVICE_ADDRESS_RE.fullmatch(actual[1]):
                        self.finding('ST_WRITE_TARGET', f'{name} parameter {source_name} is not a bound writable reference',
                                     offset=actual[-1])
                        return 'unknown'
                    self.device_fact(actual[1], write=True)
                    if self.model in SUPPORTED_PLC_MODELS:
                        try:
                            _validate_writable_device(actual[1], '$.st_code', self.model, opcode=contract.opcode)
                        except PLCJsonValidationError as error:
                            self.finding('ST_OPERAND', str(error), offset=actual[-1])
                            return 'unknown'
                elif 'CONSTANT' in declaration.class_name:
                    self.finding('ST_WRITE_TARGET', f'{name} parameter {source_name} is constant',
                                 offset=actual[-1])
                    return 'unknown'

        constraint_roots = [fact['id'] for fact in contract.facts
            if fact.get('dimension') in {'constraints.numeric_ranges',
                                          'constraints.documented_requirements'}]
        constraint_status = None
        if constraint_roots:
            from plc.instruction_constraints import check_fact_constraints
            values = {}
            argument_tokens = []
            for index, (parameter, formal, actual, _) in enumerate(binding_rows):
                constant = self.constant(actual)
                values[parameter['name']] = constant
                values[parameter['name'].upper()] = constant
                values['$' + str(index)] = constant
                token = None
                if actual[0] in {'atom', 'index', 'member'}:
                    declaration = self.reference(actual)
                    token = ((getattr(declaration, 'address', '') or actual[1])
                             if declaration is not None and actual[0] == 'atom'
                             else actual[1] if actual[0] == 'atom' and DEVICE_ADDRESS_RE.fullmatch(actual[1])
                             else None)
                argument_tokens.append(token)
            values['$OPERANDS'] = argument_tokens
            values['$OPERAND_TOKENS'] = list(argument_tokens)
            values['$PARAMETER_NAMES'] = [parameter['name'] for parameter in source_parameters]
            report = check_fact_constraints(contract.facts, values, target=contract.opcode,
                plc_model=self.model, language='st',
                version=self.symbols.context.get('software_version'),
                context=self.constraint_context(), phase='input', root_ids=constraint_roots)
            self.constraints(report, offset)
            constraint_status = report.get('status')
            if constraint_status == 'failed':
                return 'unknown'

        call_row = {'name': name, 'opcode': contract.opcode,
            'kind': ('structured_function_block' if contract.instance_required else
                     'structured_statement' if contract.call_kind == 'statement' else
                     'structured_instruction' if contract.call_kind == 'instruction' else
                     'structured_function'),
            'source': 'reviewed_st_signature', 'structured_call': True,
            'parameters': (list(bindings) if not variadic else
                           [parameter['name'].upper() for parameter, _, _, _ in binding_rows]),
            'contract_fact_ids': list(contract.fact_ids)}
        if contract.instance_required:
            call_row.update(instance_name=instance.name, definition=contract.opcode,
                            return_role='no_return_value')
        elif contract.call_kind == 'statement':
            call_row.update(return_role=contract.return_role,
                            execution_outputs=[dict(output) for output in contract.execution_outputs],
                            documented_form=contract.documented_form)
        else:
            result_type = contract.returns.upper()
            if result_type in generic_families:
                generic_result = result_type
                result_type = unified_generics.get(generic_result)
                if result_type is None and generic_result in {'ANY16', 'ANY32', 'ANY_BIT'}:
                    # Output-only bit-pattern functions have no same-domain
                    # input to unify. Bind a direct assignment only within the
                    # return alternatives explicitly retained by its source.
                    target = self.assignment_targets.get(offset)
                    expected = self._exact_iec_type(target) if target is not None else None
                    alternatives = {item.upper() for item in contract.return_type_alternatives}
                    exact = scalar_type(expected) if expected else None
                    width_matches = (generic_result in {'ANY16', 'ANY32'}
                        and exact is not None and exact.kind == 'int'
                        and exact.bits == (16 if generic_result == 'ANY16' else 32)
                        and not exact.unit)
                    bit_type_matches = (generic_result == 'ANY_BIT'
                        and expected in {'BOOL', 'WORD', 'DWORD'})
                    if (expected in alternatives and exact is not None
                            and (width_matches or bit_type_matches)):
                        result_type = expected
                        call_row['source_return_type'] = generic_result
                if result_type is None:
                    self.finding('ST_TYPE', f'{name} return type has no unified generic binding', offset=offset)
                    return 'unknown'
            if (contract.return_type_alternatives
                    and result_type.upper() not in {item.upper() for item in contract.return_type_alternatives}):
                self.finding('ST_TYPE', f'{name} return type requires one of '
                             + ', '.join(contract.return_type_alternatives), offset=offset)
                return 'unknown'
            if scalar_type(result_type) is None:
                self.finding('ST_CALL_CONTRACT', f'{name} return type cannot be represented by the ST binder',
                             offset=offset, severity='unverified')
                return 'unknown'
            self.structured_call_result_types[offset] = result_type
            call_row['return_type'] = result_type
            if contract.return_binding and contract.return_binding.get('kind') == 'start_data_reference':
                call_row['return_binding'] = dict(contract.return_binding)
        if constraint_status is not None:
            call_row['applicability_constraints_status'] = constraint_status
        self.calls.append(call_row)
        return 'void' if contract.instance_required or contract.call_kind == 'statement' else type_family(scalar_type(result_type))

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
            expression_contract = None
            if node[1] == 'NOT':
                expression_entry = getattr(DEFAULT_INSTRUCTION_REGISTRY, 'expression_entry', None)
                if callable(expression_entry) and expression_entry('NOT', cpu=self.model):
                    expression_contract = self._structured_expression_contract(node)
            if expression_contract:
                expected = expression_contract.operand_types[0]
                if not self._expression_type_matches(family, expected):
                    self.finding('ST_TYPE', f'NOT operand 1 requires {expected}', offset=node[-1])
                return self._expression_result_family(expression_contract, [family])
            if node[1] in {'+', '-'} and family not in {'word', 'dword', 'real', 'time', 'unknown'}:
                self.finding('ST_TYPE', 'Unary numeric operator requires numeric data', offset=node[-1])
            if node[1] == 'NOT' and family not in {'bit', 'word', 'dword', 'unknown'}:
                self.finding('ST_TYPE', 'NOT requires Boolean or bit-string data', offset=node[-1])
            return family
        expression_contract = (self._structured_expression_contract(node)
            if node[1] in _STRUCTURED_EXPRESSION_OPERATORS else None)
        if node[1] == 'AND':
            # An admitted FX operator adds its own operand bounds to the
            # shared AND grammar; other models retain their language contract.
            expression_entry = getattr(DEFAULT_INSTRUCTION_REGISTRY, 'expression_entry', None)
            if callable(expression_entry) and expression_entry('AND', cpu=self.model):
                expression_contract = self._structured_expression_contract(node)
        left, right = self.value_type(node[2]), self.value_type(node[3])
        numeric = {'word', 'dword', 'real', 'time'}
        fx_k_expression = bool(expression_contract and self._is_fx_k_expression_contract(expression_contract))
        if (not fx_k_expression and left != right and 'unknown' not in {left, right}
                and not {left, right} <= numeric):
            self.finding('ST_TYPE', f'{node[1]} combines incompatible operand types {left}/{right}', offset=node[-1])
        from plc.st_frontend import operator_binding
        rule = next((rule for rule in operator_binding()['types'].values() if node[1] in rule['operators']), None)
        families = {'ANY_REAL': {'real'}, 'ANY_INT': {'word', 'dword'}, 'ANY_NUM': {'word', 'dword', 'real'},
                    'ANY_BIT': {'bit', 'word', 'dword'}, 'ANY_SIMPLE': {'bit', 'word', 'dword', 'real', 'time', 'string'},
                    'REAL': {'real'}, 'LREAL': {'real'}, 'INT': {'word'}, 'UINT': {'word'},
                    'SINT': {'word'}, 'USINT': {'word'}, 'DINT': {'dword'}, 'UDINT': {'dword'},
                    'LINT': {'dword'}, 'ULINT': {'dword'}, 'BOOL': {'bit'}, 'BIT': {'bit'},
                    'WORD': {'word'}, 'DWORD': {'dword'}, 'TIME': {'time'}, 'STRING': {'string'}}
        if rule or expression_contract:
            expected_types = (list(expression_contract.operand_types) if expression_contract
                              else list(rule['inputs']))
            for index, actual in enumerate((left, right)):
                expected = expected_types[min(index, len(expected_types) - 1)]
                if actual != 'unknown' and actual not in families[expected]:
                    self.finding('ST_TYPE', f'{node[1]} operand {index + 1} requires {expected}', offset=node[-1])
        if node[1] == '**' and not fx_k_expression:
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
        if node[1] in {'=', '<>', '<', '>', '<=', '>='}:
            return 'bit'
        if expression_contract:
            return self._expression_result_family(expression_contract, [left, right])
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
        left = self.value_type(target)
        if value[0] == 'call':
            self.assignment_targets[value[-1]] = target
        try:
            right = self.value_type(value)
        finally:
            if value[0] == 'call':
                self.assignment_targets.pop(value[-1], None)
        if declaration and declaration.address and not initializing:
            self.device_fact(declaration.address, write=True)
            self.guard(_validate_writable_device, declaration.address, '$.st_code', self.model)
        if target[0] == 'atom' and DEVICE_ADDRESS_RE.fullmatch(target[1]):
            self.device_fact(target[1], write=True)
            if self.model in SUPPORTED_PLC_MODELS:
                self.guard(_validate_writable_device, target[1], '$.st_code', self.model)
        numeric = {'word', 'dword', 'real'}
        if left != right and 'unknown' not in {left, right} and not {left, right} <= numeric:
            if not (left.startswith('array:') and right == 'array' or left.startswith('structure:') and right == 'structure'):
                self.finding('ST_TYPE', f'Assignment types differ: {left}/{right}', offset=target[-1])
        if value[0] == 'call':
            actual_return = self.structured_call_result_types.get(value[-1])
            destination_type = self._exact_iec_type(target)
            if (actual_return and destination_type and scalar_type(actual_return) != scalar_type(destination_type)):
                self.finding('ST_TYPE', f'Assignment type differs: {destination_type}/{actual_return}',
                             offset=target[-1])
        if declaration:
            self.initializer(declaration.data_type, value)

    def call(self, node, *, statement_context=False):
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
            if (instance is not None and definition.kind == 'function_block'
                    and getattr(definition, 'project_source', {}).get('library')):
                entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry(definition.name, cpu=self.model) or {}
                if any(fact.get('dimension') == 'identity.form'
                       and fact.get('value', {}).get('call_basis') == 'instance_call'
                       for fact in entry.get('facts', ())):
                    if not self._reviewed_structured_call_admitted(entry):
                        self.finding('ST_CALL_COVERAGE', 'This source-backed FB instance has no qualified call admission',
                                     offset=offset, severity='unverified')
                        return 'unknown'
                    return self._structured_call(node, entry, statement_context=statement_context, instance=instance)
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
                source = getattr(definition, 'project_source', {}).get('program')
                callable_sources = self.symbols.callable_sources
                if callable_sources is not None and source:
                    callable_sources = copy(callable_sources)
                    callable_sources.program_name = source
                symbols = Symbols(definition, callable_sources=callable_sources)
                symbols.context = (dict(self.symbols.context, selected_source=source)
                                   if source else self.symbols.context)
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
        if self._reviewed_structured_call_admitted(entry):
            return self._structured_call(node, entry, statement_context=statement_context)
        availability = [f for f in entry.get('facts', []) if f['dimension'] == 'constraints.cpu_availability']
        if availability:
            from plc.instruction_constraints import check_fact_constraints
            self.constraints(check_fact_constraints(availability, {}, target=name, plc_model=self.model,
                language='st', version=self.symbols.context.get('software_version'),
                context=self.constraint_context()), offset)
        form = DEFAULT_INSTRUCTION_REGISTRY.resolve_st_call(name, cpu=self.model)
        if not form and self._reviewed_structured_expression_admitted(entry):
            self.finding('ST_CALL_FORM', name + ' is an operator expression, not a function call',
                         offset=offset)
            for argument in args:
                self.value_type(argument[3] if argument[0] == 'named' else argument)
            return 'unknown'
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

    def _source_scalar_type_supported(self, data_type):
        """Resolve ST declaration support separately from native label codes."""
        from plc.instruction_constraints import _condition, _scope
        from plc.instruction_definition import (DefinitionError, InstructionFactGroup,
            UnknownInstructionSemantics, select_fact_dependencies)

        if scalar_type(data_type) is None:
            return False
        feature = DEFAULT_INSTRUCTION_REGISTRY.language_feature(
            'ST.DATATYPES', language='st', cpu=self.model)
        if not feature:
            return False
        version = self.symbols.context.get('software_version')
        context = {**self.constraint_context(), '$PLC_MODEL': self.model, '$LANGUAGE': 'st'}
        supported = False
        try:
            for raw in feature.get('facts', []):
                if (raw.get('dimension') != 'constraints.datatype_availability'
                        or raw.get('value', {}).get('data_type') != data_type):
                    continue
                group = InstructionFactGroup.from_mapping(raw)
                state = _scope(group, target='ST.DATATYPES', model=self.model,
                               language='st', version=version)
                if state == 'not_applicable':
                    continue
                if (state != 'passed' or self.model not in group.scope.get('models', [])
                        or 'ST.DATATYPES' not in group.scope.get('forms', [])
                        or 'st' not in group.scope.get('languages', [])):
                    return False
                closure = select_fact_dependencies(feature['facts'], [group.id],
                    opcode='ST.DATATYPES', model=self.model, language='st', version=version)
                if not closure['source_verification_complete']:
                    return False
                applicable = True
                for fact in closure['groups']:
                    if (fact.get('verification') != 'source_checked' or not any(
                            source.get('manual_id') and source.get('revision')
                            and (source.get('pdf_pages') or source.get('pdf_page'))
                            for source in fact.get('sources', []))):
                        return False
                    if not all(_condition(condition, {}, context)
                               for condition in fact.get('conditions', [])):
                        applicable = False
                if not applicable:
                    continue
                if group.value.get('available') is not True:
                    return False
                supported = True
        except (DefinitionError, UnknownInstructionSemantics, TypeError, ValueError, KeyError):
            return False
        return supported

    def declarations(self):
        for declaration in [*self.symbols.globals.values(), *self.symbols.locals.values()]:
            try:
                if declaration.address:
                    self.guard(_validate_device_address, declaration.address, '$.declarations', self.model)
                base, _ = self.symbols.parts(declaration.data_type)
                if (self.symbols.callable_sources is not None and base.upper() not in BASIC_TYPES
                        and base.upper() not in self.symbols.types and base.upper() not in self.symbols.pous):
                    self.symbols.callable(declaration.name)
                if (base.upper() not in BASIC_TYPES and base.upper() not in self.symbols.types
                        and base.upper() not in self.symbols.pous
                        and not self._source_scalar_type_supported(base.upper())):
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
                self.call(statement[1], statement_context=True)
            elif kind == 'assign':
                self.assignment(statement[1], statement[2])
            else:
                self.finding('ST_STATEMENT_COVERAGE', 'Statement semantics not mapped: ' + kind, severity='unverified')

