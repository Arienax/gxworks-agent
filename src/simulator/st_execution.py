"""ST control flow and scoped storage hosted by the shared ScanMachine.

There is no independent PLC instruction engine here: device access, instruction
effects, scans, traces and observations belong to bounded.ScanMachine.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import math
import re
import struct

from plc.st_frontend import STUncovered, Parser, Declaration, parse_body
from plc.st_semantics import Symbols, Inspector, literal_value, scalar_type, bind_arguments
from plc.instruction_definition import UnknownInstructionSemantics, ValueType
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
from plc.validation import DEVICE_ADDRESS_RE, is_bit_device, SUPPORTED_PLC_MODELS


class ExecutionBudget:
    def __init__(self, *, per_scan=10000, total=1000000, call_depth=64):
        self.per_scan, self.remaining, self.max_depth = per_scan, total, call_depth
        self.scan_remaining, self.depth = per_scan, 0

    def begin_scan(self):
        self.scan_remaining = self.per_scan

    def charge(self):
        if self.remaining <= 0 or self.scan_remaining <= 0:
            raise UnknownInstructionSemantics('st_step_budget_exhausted')
        self.remaining -= 1
        self.scan_remaining -= 1

    def enter(self):
        if self.depth >= self.max_depth:
            raise UnknownInstructionSemantics('st_call_depth_exhausted')
        self.depth += 1


@dataclass
class Reference:
    scope: object
    key: str
    data_type: str
    selectors: tuple = ()
    physical: bool = False

    def get(self):
        machine = self.scope.runtime.machine
        scalar = scalar_type(self.data_type)
        if self.physical:
            if scalar and scalar.kind == 'bool':
                value = machine.bit(self.key)
                if value is None:
                    raise UnknownInstructionSemantics('unknown_memory:' + self.key)
                return value
            from simulator.bounded import _leaf
            raw = _leaf(self.key, machine.memory, scalar.bits if scalar else 16,
                        scalar.signed if scalar and scalar.kind == 'int' else False, plc_model=machine.model)
            if scalar and scalar.kind == 'real':
                return struct.unpack('<f', struct.pack('<I', raw))[0]
            return raw
        value = machine.memory.get(self.key)
        for selector in self.selectors:
            if value is None:
                break
            value = value[selector]
        if value is None:
            raise UnknownInstructionSemantics('unknown_memory:' + self.key)
        return copy.deepcopy(value)

    def set(self, value, location):
        machine = self.scope.runtime.machine
        scalar = scalar_type(self.data_type)
        if value is not None:
            value = self.scope.coerce(value, self.data_type)
        for identity, targets in machine.groups.items():
            snapshots = getattr(machine, '_st_group_snapshots', {})
            if identity not in snapshots and self.key in targets:
                snapshots[identity] = copy.deepcopy(machine.memory)
        if self.physical:
            if scalar and scalar.kind == 'real' and value is not None:
                value = struct.unpack('<I', struct.pack('<f', value))[0]
            if '.' in self.key and scalar and scalar.kind == 'bool':
                base, bit = self.key.rsplit('.', 1)
                prior = machine.memory.get(base)
                if prior is None or value is None:
                    machine.write(base, None, location)
                else:
                    mask = 1 << int(bit, 16)
                    machine.write(base, (int(prior) | mask) if value else (int(prior) & ~mask), location)
                return
            machine.write(self.key, value, location, 'bit' if scalar and scalar.kind == 'bool' else 'word',
                          scalar.bits if scalar and scalar.bits else 16)
            return
        prior = None
        if not self.selectors:
            prior = machine.memory.get(self.key)
            machine.memory[self.key] = copy.deepcopy(value)
        else:
            container = machine.memory.get(self.key)
            if container is None:
                raise UnknownInstructionSemantics('unknown_aggregate_layout:' + self.key)
            for selector in self.selectors[:-1]:
                container = container[selector]
                if container is None:
                    raise UnknownInstructionSemantics('unknown_aggregate_layout:' + self.key)
            prior = container[self.selectors[-1]]
            container[self.selectors[-1]] = copy.deepcopy(value)
        machine.writes.append(dict(scan=machine.scan_index, location=location, device=self.key,
                                   before=copy.deepcopy(prior), after=copy.deepcopy(value)))


class _Exit(Exception):
    pass


class _Return(Exception):
    pass


class Scope:
    def __init__(self, runtime, symbols, prefix, aliases=None):
        self.runtime, self.symbols, self.prefix = runtime, symbols, prefix
        self.aliases, self.initialized, self.instances = aliases or {}, False, {}

    def layout(self, data_type, seen=()):
        base, bounds = self.symbols.parts(data_type)
        if base.upper() in seen:
            raise UnknownInstructionSemantics('recursive_storage_layout')
        def shape(dimensions):
            self.runtime.budget.charge()
            if dimensions:
                low, high = dimensions[0]
                if high - low > 4096:
                    raise UnknownInstructionSemantics('st_storage_budget_exhausted')
                return [shape(dimensions[1:]) for _ in range(high - low + 1)]
            fields = self.symbols.types.get(base.upper())
            if isinstance(fields, list):
                result = {}
                for declaration in fields:
                    result[declaration.name.upper()] = (self.coerce(self.evaluate(declaration.initial), declaration.data_type)
                        if declaration.initial is not None else self.layout(declaration.data_type, (*seen, base.upper())))
                return result
            return None
        return shape(bounds)

    def coerce(self, value, data_type):
        self.runtime.budget.charge()
        base, bounds = self.symbols.parts(data_type)
        if bounds:
            count = math.prod(high - low + 1 for low, high in bounds)
            def flatten(item):
                if isinstance(item, list):
                    for child in item:
                        yield from flatten(child)
                else:
                    yield item
            values = list(flatten(value)) if isinstance(value, list) else []
            if len(values) != count:
                raise UnknownInstructionSemantics('st_array_initializer_shape_unverified')
            values = iter(values)
            def shape(dimensions):
                if not dimensions:
                    item = next(values)
                    return None if item is None else self.coerce(item, base)
                low, high = dimensions[0]
                return [shape(dimensions[1:]) for _ in range(high - low + 1)]
            return shape(bounds)
        scalar = scalar_type(base)
        if scalar:
            if scalar.kind == 'int' and (type(value) is not int or not scalar.bounds[0] <= value <= scalar.bounds[1]):
                raise UnknownInstructionSemantics('st_integer_conversion_or_overflow_unverified')
            length = re.fullmatch(r'STRING\s*\[(\d+)\]', data_type, re.I)
            if length and isinstance(value, str) and len(value) > int(length[1]):
                raise UnknownInstructionSemantics('st_string_length_exceeded')
            return scalar.decode_bits(value)
        fields = self.symbols.types.get(base.upper())
        if isinstance(fields, list) and isinstance(value, dict):
            if set(value) - {d.name.upper() for d in fields}:
                raise UnknownInstructionSemantics('st_unknown_structure_initializer_member')
            result = self.layout(data_type)
            for declaration in fields:
                key = declaration.name.upper()
                if key in value:
                    result[key] = None if value[key] is None else self.coerce(value[key], declaration.data_type)
            return result
        raise UnknownInstructionSemantics('st_storage_type_unverified:' + data_type)

    def initialize(self):
        if self.initialized:
            return
        self.initialized = True
        for global_, table in ((True, self.symbols.globals), (False, self.symbols.locals)):
            for name, declaration in table.items():
                if name in self.aliases:
                    continue
                reference = self.simple(name)
                if declaration.data_type.upper() in self.symbols.pous and self.symbols.pous[declaration.data_type.upper()].kind == 'function_block':
                    continue
                if reference.key in self.runtime.machine.memory or reference.physical:
                    continue
                self.runtime.machine.memory[reference.key] = self.layout(declaration.data_type)
                if declaration.initial is not None:
                    reference.set(self.evaluate(declaration.initial), {'path': 'declaration:' + reference.key, 'opcode': 'ST_INIT'})
                elif declaration.initial_text:
                    raise UnknownInstructionSemantics('declaration_initializer_unmapped:' + name)

    def simple(self, name):
        name = name.upper()
        if name in self.aliases:
            return self.aliases[name]
        declaration = self.symbols.lookup(name)
        if declaration is None and DEVICE_ADDRESS_RE.fullmatch(name):
            family = self.runtime.instruction.get('device_types', {}).get(name)
            return Reference(self, name, 'BOOL' if family == 'bit' else 'INT', physical=True)
        if declaration is None:
            raise UnknownInstructionSemantics('st_unbound_reference:' + name)
        if declaration.address:
            if self.symbols.parts(declaration.data_type)[1]:
                raise UnknownInstructionSemantics('physical_array_layout_unverified')
            return Reference(self, declaration.address.upper(), declaration.data_type, physical=True)
        prefix = 'GLOBAL' if name in self.symbols.globals and name not in self.symbols.locals else self.prefix
        return Reference(self, prefix + '.' + name, declaration.data_type)

    def instance(self, name):
        name = name.upper()
        if name not in self.instances:
            definition, declaration = self.symbols.callable(name)
            if definition is None or declaration is None or definition.kind != 'function_block':
                raise UnknownInstructionSemantics('st_unbound_fb_instance:' + name)
            symbols = self.runtime.child_symbols(definition)
            self.instances[name] = Scope(self.runtime, symbols, self.simple(name).key)
        return self.instances[name]

    def reference(self, node):
        if node[0] == 'atom':
            name = node[1]
            if DEVICE_ADDRESS_RE.fullmatch(name) or '.' not in name:
                return self.simple(name)
            base, member = name.split('.', 1)
            declaration = self.symbols.lookup(base)
            if declaration and declaration.data_type.upper() in self.symbols.pous:
                instance = self.instance(base)
                instance.initialize()
                return instance.reference(('atom', member, node[-1]))
            reference = self.simple(base)
            for member in member.split('.'):
                reference = self.member(reference, member)
            return reference
        if node[0] == 'member':
            return self.member(self.reference(node[1]), node[2])
        if node[0] == 'index':
            reference = self.reference(node[1])
            base, bounds = self.symbols.parts(reference.data_type)
            indices = [self.evaluate(index) for index in node[2]]
            if len(bounds) != len(indices):
                raise UnknownInstructionSemantics('st_array_dimension_mismatch')
            selectors = []
            for index, (low, high) in zip(indices, bounds):
                if type(index) is not int or not low <= index <= high:
                    raise UnknownInstructionSemantics('st_array_index_out_of_bounds')
                selectors.append(index - low)
            return Reference(self, reference.key, base, (*reference.selectors, *selectors), reference.physical)
        raise UnknownInstructionSemantics('st_expected_writable_reference')

    def member(self, reference, member):
        declaration = self.symbols.member(Declaration('', reference.data_type), member)
        if declaration is None:
            raise UnknownInstructionSemantics('st_unbound_structure_member:' + member)
        return Reference(self, reference.key, declaration.data_type, (*reference.selectors, member.upper()), reference.physical)

    def evaluate(self, node):
        self.runtime.budget.charge()
        kind = node[0]
        if kind == 'atom':
            literal = literal_value(node[1])
            return literal[0] if literal else self.reference(node).get()
        if kind in {'index', 'member'}:
            return self.reference(node).get()
        if kind == 'array':
            result = []
            for value in node[1]:
                if value[0] == 'array_repeat':
                    count = self.evaluate(value[1])
                    if type(count) is not int or count < 0 or count * len(value[2]) > 4096:
                        raise UnknownInstructionSemantics('st_array_repetition_budget_exhausted')
                    for _ in range(count):
                        self.runtime.budget.charge()
                        result.extend(self.evaluate(element) for element in value[2])
                else:
                    result.append(self.evaluate(value))
            return result
        if kind == 'structure':
            return {name: self.evaluate(value) for name, value in node[1]}
        if kind == 'call':
            return self.call(node)
        if kind == 'unary':
            value = self.evaluate(node[2])
            if node[1] == 'NOT':
                if type(value) is bool:
                    return not value
                value_type = self.numeric_type(node[2])
                return value_type.decode_bits(~value)
            return -value if node[1] == '-' else +value
        left, right, op = self.evaluate(node[2]), self.evaluate(node[3]), node[1]
        if op == '**':
            from plc.instruction_constraints import check_language_constraints, require_constraints
            values = {'LEFT': left, 'RIGHT': right}
            require_constraints(check_language_constraints('ST.OPERATOR.POWER', values,
                plc_model=self.runtime.machine.model, version=self.symbols.context.get('software_version'),
                context={**self.symbols.context, 'numeric_precision':self.numeric_type(node[2]).bits}))
        if op in {'=', '<>', '<', '>', '<=', '>='}:
            return {'=': lambda: left == right, '<>': lambda: left != right, '<': lambda: left < right,
                    '>': lambda: left > right, '<=': lambda: left <= right, '>=': lambda: left >= right}[op]()
        if op in {'AND', '&', 'OR', 'XOR'}:
            return (left & right) if op in {'AND', '&'} else (left | right) if op == 'OR' else (left ^ right)
        if op in {'/', 'MOD'} and right == 0:
            raise UnknownInstructionSemantics('st_division_by_zero')
        if op == '**' and (abs(right) > 1024 or abs(left) > 1e30):
            raise UnknownInstructionSemantics('st_numeric_budget_exhausted')
        if op == '+':
            result = left + right
        elif op == '-':
            result = left - right
        elif op == '*':
            result = left * right
        elif op == '/':
            result = (abs(left) // abs(right)) * (-1 if (left < 0) != (right < 0) else 1) if type(left) is type(right) is int else left / right
        elif op == 'MOD':
            quotient = (abs(left) // abs(right)) * (-1 if (left < 0) != (right < 0) else 1)
            result = left - quotient * right
        elif op == '**':
            from plc.instruction_constraints import evaluate_language_numeric
            result = evaluate_language_numeric('ST.OPERATOR.POWER', left, right,
                plc_model=self.runtime.machine.model, precision=self.numeric_type(node[2]).bits,
                version=self.symbols.context.get('software_version'))
        else:
            raise UnknownInstructionSemantics('st_operator_unmapped:' + op)
        value_type = self.numeric_type(node)
        if op == '**':
            require_constraints(check_language_constraints('ST.OPERATOR.POWER', {**values, 'RESULT': result},
                plc_model=self.runtime.machine.model, version=self.symbols.context.get('software_version'), phase='result',
                context={**self.symbols.context, 'numeric_precision':value_type.bits}))
        if type(result) is float:
            return value_type.decode_bits(result)
        if type(result) is not int or not value_type.bounds[0] <= result <= value_type.bounds[1]:
            raise UnknownInstructionSemantics('st_numeric_overflow_unverified')
        return result

    def numeric_type(self, node):
        from plc.st_semantics import expression_numeric_type
        return expression_numeric_type(node, self.symbols, reference=self.reference)

    def call(self, node, *, want_result=True):
        self.runtime.budget.enter()
        try:
            definition, declaration = self.symbols.callable(node[1])
            if definition is None:
                return self.native_call(node, want_result=want_result)
            bindings = bind_arguments(definition, node[2])
            scope = self.instance(node[1]) if declaration else Scope(self.runtime,
                self.runtime.child_symbols(definition), self.prefix + '.$' + definition.name.upper() + '.' + str(self.runtime.budget.depth))
            scope.aliases = {name: self.reference(value) for name, (formal, value, _) in bindings.items()
                             if formal.class_name == 'VAR_IN_OUT'}
            scope.initialize()
            for name, (formal, value, _) in bindings.items():
                if formal.class_name in {'VAR_INPUT', 'VAR_INPUT_CONSTANT', 'VAR_IN_EXT'}:
                    scope.simple(name).set(self.evaluate(value), self.runtime.location(node))
            if getattr(definition, 'project_source', {}).get('language', 'st') != 'st':
                self.runtime.execute_graph(scope, definition)
            else:
                try:
                    scope.execute(definition.body)
                except _Return:
                    pass
            for name, (formal, value, _) in bindings.items():
                if formal.class_name == 'VAR_OUTPUT':
                    self.reference(value).set(scope.simple(name).get(), self.runtime.location(node))
            result = scope.simple(definition.name).get() if definition.kind == 'function' else None
            if definition.kind == 'function':
                for key in list(self.runtime.machine.memory):
                    if key.startswith(scope.prefix + '.'):
                        del self.runtime.machine.memory[key]
            return result
        finally:
            self.runtime.budget.depth -= 1

    def native_call(self, node, *, want_result=True):
        form = DEFAULT_INSTRUCTION_REGISTRY.resolve_st_call(node[1], cpu=self.runtime.machine.model)
        if not form or not form.spec.st_call:
            raise UnknownInstructionSemantics('st_call_unverified:' + node[1])
        args = node[2]
        if any(a[0] == 'named' for a in args):
            signature = DEFAULT_INSTRUCTION_REGISTRY.describe_call(form.opcode, cpu=self.runtime.machine.model)['st_parameters']
            names = {a[1]: a[3] for a in args if a[0] == 'named'}
            if len(names) != len(args) or set(names) != {p['name'].upper() for p in signature}:
                raise UnknownInstructionSemantics('st_native_argument_binding_unverified')
            args = [names[p['name'].upper()] for p in signature]
        enable = self.evaluate(args[0]) if form.spec.st_call.get('enable') else True
        if type(enable) is not bool:
            raise UnknownInstructionSemantics('st_enable_requires_bool')
        operands = args[1:] if form.spec.st_call.get('enable') else args
        if not enable:
            return False if form.spec.st_call.get('returns') == 'bit' else None
        destinations, values, tokens = {}, {}, []
        for index, (formal, argument) in enumerate(zip(form.spec.native_operand_order, operands)):
            if index in form.spec.write_indexes:
                reference = self.reference(argument)
                destinations[reference.key] = reference
                tokens.append(reference.key)
                if index in form.spec.read_indexes:
                    values[formal] = reference.get()
            else:
                # Device operands keep the instruction fact's width (DMOV,
                # special registers, vectors); labels/expressions supply values.
                if not (argument[0] == 'atom' and DEVICE_ADDRESS_RE.fullmatch(argument[1]) and self.symbols.lookup(argument[1]) is None):
                    values[formal] = self.evaluate(argument)
                tokens.append(str(argument[1]) if argument[0] == 'atom' else '__expression__')
        def write(base, offset, value, location, kind, bits):
            reference = destinations.get(base)
            if reference is None or offset:
                raise UnknownInstructionSemantics('st_instruction_region_mapping_unverified')
            if reference.physical:
                self.runtime.machine.write(reference.key, value, location, kind, bits)
            else:
                reference.set(value, location)
        self.runtime.machine._application(form.opcode, tokens, True, self.runtime.location(node), None,
                                           parameter_values=values, writer=write)
        if want_result and form.spec.st_call.get('returns') not in {None, 'void'}:
            raise UnknownInstructionSemantics('st_native_return_value_unverified:' + node[1])
        return None

    def execute(self, body):
        for statement in body:
            self.runtime.budget.charge()
            kind, location = statement[0], self.runtime.location(statement)
            if kind == 'assign':
                self.reference(statement[1]).set(self.evaluate(statement[2]), location)
            elif kind == 'invoke':
                self.call(statement[1], want_result=False)
            elif kind == 'if':
                for condition, arm in statement[1]:
                    value = self.evaluate(condition)
                    if type(value) is not bool:
                        raise UnknownInstructionSemantics('st_condition_requires_bool')
                    if value:
                        self.execute(arm)
                        break
                else:
                    self.execute(statement[2])
            elif kind == 'case':
                value = self.evaluate(statement[1])
                for selections, arm in statement[2]:
                    if any(self.evaluate(low) <= value <= self.evaluate(high) if high else self.evaluate(low) == value for low, high in selections):
                        self.execute(arm)
                        break
                else:
                    self.execute(statement[3])
            elif kind == 'for':
                variable = self.reference(statement[1])
                start, stop, step = [self.evaluate(n) for n in statement[2:5]]
                if any(type(v) is not int for v in (start, stop, step)) or step == 0:
                    raise UnknownInstructionSemantics('st_invalid_for_bounds')
                variable.set(start, location)
                while variable.get() <= stop if step > 0 else variable.get() >= stop:
                    self.runtime.budget.charge()
                    try:
                        self.execute(statement[5])
                    except _Exit:
                        break
                    variable.set(variable.get() + step, location)
            elif kind in {'while', 'repeat'}:
                while True:
                    self.runtime.budget.charge()
                    if kind == 'while' and not self.evaluate(statement[1]):
                        break
                    try:
                        self.execute(statement[2])
                    except _Exit:
                        break
                    if kind == 'repeat' and self.evaluate(statement[1]):
                        break
            elif kind == 'exit':
                raise _Exit()
            elif kind == 'return':
                raise _Return()
            else:
                raise UnknownInstructionSemantics('st_statement_unmapped:' + kind)


class STExecution:
    def __init__(self, machine, instruction):
        self.machine, self.instruction = machine, instruction
        self.budget = machine.execution_budget
        self.root = Scope(self, instruction['symbols'], instruction['unit'].name.upper())

    def child_symbols(self, definition):
        symbols = Symbols(definition)
        symbols.globals = self.root.symbols.globals
        symbols.pous = self.root.symbols.pous
        symbols.types = self.root.symbols.types
        symbols.options = self.root.symbols.options
        return symbols

    @staticmethod
    def location(node):
        span = getattr(node, 'span', None)
        return dict(opcode='ST_' + node[0].upper(), path='st:' + str(span.start if span else node[-1]),
                    **(dict(offset=span.start, line=span.line, column=span.column) if span else {}))

    def execute_graph(self, scope, definition):
        from gxw.execution import executable_source
        source = executable_source(definition.project_source)
        if source['language'] == 'st':
            scope.execute(parse_body(source['body']).body)
        else:
            raise UnknownInstructionSemantics('native_graph_execution_schedule_unverified')

    def run(self):
        self.root.initialize()
        try:
            self.root.execute(self.instruction['unit'].body)
        except _Return:
            pass


def compile_st(source, *, plc_model, confirmed_spec=None, project_context=None):
    parser = Parser(source)
    parser.body()
    if not parser.unit.body:
        raise STUncovered('st_empty_body')
    symbols = Symbols(parser.unit, parsed=parser.parsed, project_context=project_context)
    bound_types = {}
    from plc.device_identity import canonical_reference_device
    for row in (confirmed_spec or {}).get('io_bindings', (confirmed_spec or {}).get('io_table', [])):
        address, kind = row.get('address'), str(row.get('kind') or '').upper()
        match = DEVICE_ADDRESS_RE.fullmatch(address or '')
        if match and match[1].upper() == kind and match[3] is None and kind in {'M', 'S', 'D'}:
            bound_types[canonical_reference_device(address)] = 'word' if kind == 'D' else 'bit'
    inspector = Inspector(plc_model, (confirmed_spec or {}).get('hardware_profile'), symbols=symbols, bound_device_types=bound_types)
    inspector.declarations()
    inspector.body(parser.unit.body)
    if any(f['severity'] == 'error' for f in inspector.findings):
        raise STUncovered('st_static_error')
    gaps = [f for f in inspector.findings if f['severity'] == 'unverified' and
            f['code'] not in {'ST_CALL_OPERAND_COVERAGE', 'ST_DEVICE_COVERAGE', 'ST_CALL_GRAPH_COVERAGE',
                             'ST_NUMERIC_DOMAIN', 'ST_NUMERIC_RANGE'} and not f['code'].startswith('DEVICE_')]
    if gaps:
        raise STUncovered(gaps[0]['code'] + ':' + gaps[0]['message'])
    if plc_model not in SUPPORTED_PLC_MODELS and any(inspector.partial_device_type(device) == 'unknown' for device in inspector.devices):
        raise STUncovered('st_unbound_expression_type')
    device_types = {d: inspector.value_type(('atom', d, 0)) for d in list(inspector.devices)}
    return [({'op': 'ST_PROGRAM', 'args': [], 'path': 'st', 'unit': parser.unit, 'symbols': symbols,
              'devices': sorted(inspector.devices), 'device_types': device_types}, ([], []))]
