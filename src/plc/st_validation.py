"""Bounded ST inspection using the shared instruction and device owners.

This is a body parser, not a GX Works2 compiler. Unsupported declarations,
labels and language constructs produce explicit coverage findings. Native
acceptance and requirement fulfilment cannot be inferred from this report.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
from plc.validation import (
    DEVICE_ADDRESS_RE, PLCJsonValidationError, is_bit_device,
    SUPPORTED_PLC_MODELS, parse_device_address, _validate_device_address,
    _validate_instruction_boundaries, _validate_source_operand_types,
    _validate_writable_device,
)


@dataclass(frozen=True)
class Token:
    text: str
    offset: int


class STParseError(ValueError):
    pass


class STUncovered(STParseError):
    pass


_TOKEN = re.compile(
    r'\s+|\(\*|//[^\n]*|"(?:\$\.|[^"\n])*"|\'(?:\$\.|[^\'\n])*\'|'
    r'\d+\#[0-9A-Fa-f_]+|\d+(?:\.\d+)?|'
    r'[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_0-9]+)*|'
    r':=|<>|<=|>=|[(),;:+*/=<>\-]', re.ASCII,
)
_PRECEDENCE = {'OR': 1, 'XOR': 2, 'AND': 3, '=': 4, '<>': 4,
               '<': 4, '>': 4, '<=': 4, '>=': 4, '+': 5, '-': 5,
               '*': 6, '/': 6, 'MOD': 6}
_UNCOVERED_STATEMENTS = {'VAR', 'VAR_INPUT', 'VAR_OUTPUT', 'VAR_IN_OUT',
    'VAR_TEMP', 'VAR_GLOBAL', 'PROGRAM', 'FUNCTION', 'FUNCTION_BLOCK',
    'FOR', 'WHILE', 'REPEAT', 'CASE', 'RETURN', 'EXIT'}


def _tokens(source):
    result, offset = [], 0
    while offset < len(source):
        match = _TOKEN.match(source, offset)
        if not match:
            if source[offset] in '[]^#':
                raise STUncovered(f'Indexing, indirection or typed literal at offset {offset} is outside the ST body subset')
            raise STParseError(f'Unexpected character at offset {offset}')
        text = match.group()
        if text == '(*':
            depth, end = 1, match.end()
            while depth:
                delimiter = re.search(r'\(\*|\*\)', source[end:])
                if delimiter is None:
                    raise STParseError(f'Unterminated comment at offset {offset}')
                end += delimiter.end()
                depth += 1 if delimiter.group() == '(*' else -1
            offset = end
            continue
        if not text.isspace() and not text.startswith('//'):
            result.append(Token(text, offset))
        offset = match.end()
    return result + [Token('', len(source))]


class _Parser:
    def __init__(self, source):
        self.tokens, self.index, self.statements = _tokens(source), 0, 0

    @property
    def token(self):
        return self.tokens[self.index]

    def take(self, expected=None):
        token = self.token
        if expected is not None and token.text.upper() != expected:
            raise STParseError(f'Expected {expected!r} at offset {token.offset}; received {token.text!r}')
        self.index += 1
        return token

    def expression(self, priority=0, depth=0):
        if depth > 100:
            raise STUncovered('Expression nesting exceeds the inspection limit')
        token = self.take()
        upper = token.text.upper()
        if upper in {'NOT', '+', '-'}:
            left = ('unary', upper, self.expression(7, depth + 1), token.offset)
        elif upper == '(':
            left = self.expression(depth=depth + 1)
            self.take(')')
        elif re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_0-9]+)*', token.text):
            if self.token.text == '(':
                self.take('(')
                args = []
                if self.token.text != ')':
                    while True:
                        args.append(self.expression(depth=depth + 1))
                        if self.token.text != ',':
                            break
                        self.take(',')
                self.take(')')
                left = ('call', upper, args, token.offset)
            else:
                left = ('atom', upper, token.offset)
        elif token.text and (token.text[0].isdigit() or token.text[0] in {'"', "'"}):
            left = ('atom', token.text, token.offset)
        else:
            raise STParseError(f'Expected expression at offset {token.offset}; received {token.text!r}')
        while _PRECEDENCE.get(self.token.text.upper(), 0) > priority:
            operator = self.take().text.upper()
            left = ('binary', operator, left, self.expression(_PRECEDENCE[operator], depth + 1), token.offset)
        return left

    def body(self, stops=frozenset(), depth=0):
        if depth > 80:
            raise STUncovered('Statement nesting exceeds the inspection limit')
        statements = []
        while self.token.text and self.token.text.upper() not in stops:
            if self.token.text.upper() in _UNCOVERED_STATEMENTS:
                raise STUncovered(f'{self.token.text} is outside the supported ST body subset')
            if self.token.text == ';':
                self.take(';')
                continue
            if self.token.text.upper() == 'IF':
                self.take('IF')
                arms = []
                while True:
                    condition = self.expression()
                    self.take('THEN')
                    arms.append((condition, self.body({'ELSIF', 'ELSE', 'END_IF'}, depth + 1)))
                    if self.token.text.upper() != 'ELSIF':
                        break
                    self.take('ELSIF')
                otherwise = []
                if self.token.text.upper() == 'ELSE':
                    self.take('ELSE')
                    otherwise = self.body({'END_IF'}, depth + 1)
                self.take('END_IF')
                self.take(';')
                statements.append(('if', arms, otherwise))
            else:
                expression = self.expression()
                if self.token.text == ':=':
                    if expression[0] != 'atom':
                        raise STParseError('Assignment requires a device or label target')
                    self.take(':=')
                    statements.append(('assign', expression, self.expression()))
                elif expression[0] == 'call':
                    statements.append(('invoke', expression))
                else:
                    raise STParseError(f'Expected assignment or ST call at offset {expression[-1]}')
                self.take(';')
                self.statements += 1
        return statements


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


class _Inspector:
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
                if self.model not in SUPPORTED_PLC_MODELS:
                    self.finding('ST_DEVICE_COVERAGE', f'No device profile for {self.model}', severity='unverified')
                    return self.partial_device_type(text)
                self.guard(_validate_device_address, text, '$.st_code', self.model)
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
                if not profile_available:
                    continue
                if arg[0] != 'atom' or parse_device_address(literal, self.model) is None:
                    self.finding('ST_WRITE_TARGET', f'{name} requires a device write target at operand {index}')
                else:
                    self.guard(_validate_writable_device, literal, '$.st_code', self.model, opcode=form.opcode)
        if profile_available and '__UNVERIFIED_EXPRESSION__' not in values:
            self.guard(_validate_source_operand_types, spec, values, '$.st_code', self.model)
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
                if self.model in SUPPORTED_PLC_MODELS and parse_device_address(target[1], self.model):
                    if target[1].startswith('X'):
                        self.finding('ST_WRITE_TARGET', 'Input device X cannot be an assignment target')
                    self.guard(_validate_writable_device, target[1], '$.st_code', self.model)
                if 'unknown' not in {left, right} and left != right:
                    self.finding('ST_TYPE', f'Assignment types differ: {left}/{right}', offset=target[-1])


def inspect_st_program(source, *, plc_model, hardware_context=None, confirmed_spec=None):
    """Return syntax/static coverage without modifying source or invoking a model."""
    from plc.target_support import normalize_target_cpu
    model = normalize_target_cpu(plc_model)
    inspector = _Inspector(model,hardware_context)
    statement_count, syntax = 0, 'unverified'
    try:
        if not isinstance(source, str) or len(source) > 512000:
            raise STUncovered('ST source is absent or exceeds the inspection size limit')
        parser = _Parser(source)
        body = parser.body()
        statement_count = parser.statements
        if not statement_count:
            raise STParseError('ST body has no executable statements')
        syntax = 'passed'
        inspector.body(body)
    except STUncovered as error:
        inspector.finding('ST_SYNTAX_COVERAGE', str(error), severity='unverified')
    except (STParseError, RecursionError) as error:
        syntax = 'failed'
        inspector.finding('ST_SYNTAX', str(error))
    counts = {key: sum(item['severity'] == key for item in inspector.findings)
              for key in ('error', 'warning', 'unverified')}
    status = 'failed' if counts['error'] else 'unverified' if counts['unverified'] else 'passed'
    result = {'available': True, 'valid': status == 'passed', 'status': status,
        'plc_model': model, 'profile': 'st_body_static', 'syntax': syntax,
        'statement_count': statement_count, 'counts': counts,
        'findings': inspector.findings, 'calls': inspector.calls,
        'devices': sorted(inspector.devices),
        'native': 'unverified', 'requirements': 'unverified', 'runtime': 'unverified'}
    if confirmed_spec and confirmed_spec.get('behavior_constraints'):
        from simulator.bounded import check_confirmed_behavior
        behavior = check_confirmed_behavior({'st_code':source}, {**confirmed_spec,'plc_model':model})
        result['behavior_check'] = behavior
        result['requirements'] = behavior['status']
        if behavior.get('activation_blocked'):
            result.update(valid=False,status='failed')
    return result


def bounded_st_instructions(source, *, plc_model, confirmed_spec=None):
    """Adapt the existing body AST to ScanMachine's ordered operations.

    No source rewriting or generated ladder is involved. Unsupported types and
    calls fail closed before execution; IF guards are sampled at execution time.
    """
    from plc.device_identity import canonical_reference_device
    from plc.instruction_definition import Expression
    spec = confirmed_spec or {}
    bound_types = {}
    for row in spec.get('io_bindings', spec.get('io_table', [])):
        address = row.get('address')
        match = DEVICE_ADDRESS_RE.fullmatch(address or '')
        kind = str(row.get('kind') or '').upper()
        # A confirmed ordinary M/S/D binding supplies a type, not an address
        # range, instruction permission, hardware condition or inferred label.
        if match and match[1].upper() == kind and match[3] is None and kind in {'M', 'S', 'D'}:
            bound_types[canonical_reference_device(address)] = 'word' if kind == 'D' else 'bit'
    inspector = _Inspector(plc_model, spec.get('hardware_profile'), bound_device_types=bound_types)
    def known_device(token):
        if plc_model in SUPPORTED_PLC_MODELS:
            return parse_device_address(token, plc_model) is not None
        return DEVICE_ADDRESS_RE.fullmatch(token) is not None and inspector.partial_device_type(token) in {'bit', 'word'}
    parser = _Parser(source)
    body = parser.body()
    if not parser.statements:
        raise STUncovered('st_empty_body')
    bit_type, word_type = {'kind':'bool'}, {'kind':'int','bits':16,'signed':True}

    def expression(node):
        kind = inspector.value_type(node)
        if kind not in {'bit','word'}:
            raise STUncovered('st_unbound_expression_type')
        value_type = bit_type if kind == 'bit' else word_type
        if node[0] == 'atom':
            token = node[1]
            if token in {'TRUE','FALSE'}:
                result = {'op':'constant','type':bit_type,'value':token=='TRUE'}
            elif known_device(token):
                if token.startswith(('T','C')):
                    raise STUncovered('st_device_view_unverified:' + token)
                result = {'op':'device','type':value_type,'name':canonical_reference_device(token)}
            else:
                literal = _literal(node)
                if not literal or not re.fullmatch(r'K[+-]?\d+',literal):
                    raise STUncovered('st_literal_unverified:' + token)
                result = {'op':'constant','type':word_type,'value':int(literal[1:])}
        elif node[0] == 'unary' and node[1] == 'NOT' and kind == 'bit':
            result = {'op':'not','type':bit_type,'args':[expression(node[2])]}
        elif node[0] == 'binary' and node[1] in {'AND','OR','XOR','=','<>','<','>','<=','>='}:
            op = {'AND':'and','OR':'or','XOR':'xor','=':'eq','<>':'ne','<':'lt','>':'gt','<=':'le','>=':'ge'}[node[1]]
            if node[1] in {'AND','OR','XOR'} and kind != 'bit':
                raise STUncovered('st_word_logic_unverified')
            result = {'op':op,'type':bit_type,'args':[expression(node[2]),expression(node[3])]}
        else:
            literal = _literal(node)
            if not literal or not re.fullmatch(r'K[+-]?\d+',literal):
                raise STUncovered('st_expression_outside_bounded_subset')
            result = {'op':'constant','type':word_type,'value':int(literal[1:])}
        return Expression.from_mapping(result).as_mapping()

    instructions = []
    def emit(op, *, writes=(), **fields):
        instructions.append(({'op':op,'args':[], 'path':f'st[{len(instructions)}]', **fields}, ([],list(writes))))
    def lower(statements):
        for statement in statements:
            if statement[0] == 'if':
                for index,(condition,arm) in enumerate(statement[1]):
                    expr = expression(condition)
                    if expr['type']['kind'] != 'bool':
                        raise STUncovered('st_condition_requires_bool')
                    emit('ST_IF' if index==0 else 'ST_ELSIF', expression=expr)
                    lower(arm)
                emit('ST_ELSE')
                lower(statement[2])
                emit('ST_END_IF')
            elif statement[0] == 'assign':
                target, value = statement[1:]
                inspector.body([statement])
                if not known_device(target[1]) or '.' in target[1]:
                    raise STUncovered('st_assignment_target_unverified')
                emit('ST_ASSIGN', target=canonical_reference_device(target[1]), expression=expression(value), writes=[canonical_reference_device(target[1])])
            else:
                call = statement[1]
                inspector.call(call)
                form = DEFAULT_INSTRUCTION_REGISTRY.resolve_st_call(call[1],cpu=plc_model)
                if not form or not form.spec.st_call or form.opcode not in {'MOV','DMOV'}:
                    raise STUncovered('st_call_outside_bounded_subset:' + call[1])
                args = call[2]
                if len(args)!=3 or any(_literal(arg) is None for arg in args[1:]):
                    raise STUncovered('st_call_operands_unverified')
                from plc.ir import analyze_instruction_access
                operands = [_literal(arg) for arg in args[1:]]
                emit('ST_ENABLE',expression=expression(args[0]))
                instructions.append(({'op':form.opcode,'args':operands,'path':f'st[{len(instructions)}]'},
                    analyze_instruction_access(form.opcode,operands,plc_model=plc_model)))
                emit('ST_RESTORE')
    lower(body)
    if any(f['severity']=='error' for f in inspector.findings):
        raise STUncovered('st_static_error')
    return instructions
