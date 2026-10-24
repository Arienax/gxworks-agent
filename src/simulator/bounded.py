"""Bounded instruction-order scan checks; neither native simulation nor hardware I/O.

The canonical IR lowerer determines execution order. Instruction definitions
interpret formal application effects; source-scoped runtime facts supply the
small control subset. Unknown memory and time remain unknown.
"""
from __future__ import annotations

import copy
import itertools
import json
import re

from plc.device_identity import canonical_device, canonical_reference_device, decimal_region_address, digit_specified_devices, word_region_addresses
from plc.instruction_definition import Expression, UnknownInstructionSemantics, evaluate_expression, select_fact_dependencies
from plc.instruction_effects import execute_behavior
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
from plc.ir import analyze_instruction_access, canonical_sha256, ir_to_ladder, is_plc_ir, lower_rung_instructions
from plc.runtime_semantics import control_runtime_facts, first_scan_fact, timer_runtime_fact
from plc.specification.behavior import initialization_values, normalize_behavior_constraints

MAX_SCANS = 8
MAX_TRACES = 4096
UNKNOWN = None


def _and(a, b):
    if a is False or b is False:
        return False
    return None if a is None or b is None else True


def _or(a, b):
    if a is True or b is True:
        return True
    return None if a is None or b is None else False


def _not(value):
    return None if value is None else not value


def _leaf(value, memory, bits=16, signed=True, plc_model='FX3U'):
    token = str(value).upper()
    if re.fullmatch(r'K-?\d+', token):
        return int(token[1:])
    if re.fullmatch(r'H[0-9A-F]+', token):
        return int(token[1:], 16)
    devices = digit_specified_devices(token, plc_model)
    if devices is not None:
        if any(memory.get(device) is None for device in devices):
            raise UnknownInstructionSemantics('unknown_digit_specified_memory:'+token)
        packed = sum(int(bool(memory[device])) << index for index, device in enumerate(devices))
        return packed-(1 << bits) if signed and packed & (1 << (bits-1)) else packed
    address = canonical_reference_device(token)
    if '.' in address and address not in memory:
        from plc.validation import SUPPORTED_PLC_MODELS, parse_device_address, is_bit_device
        from plc.device_definitions import resolve_device_definition
        # Only an established word-bit view may read a bit of a parent word.
        # Missing parent words remain unknown; never manufacture a zero value.
        known_bit = (parse_device_address(address, plc_model) is not None and is_bit_device(address, plc_model)
                     if plc_model in SUPPORTED_PLC_MODELS else
                     resolve_device_definition(address, plc_model).get('value_type') == 'bit')
        if known_bit:
            base, index = address.split('.')
            return bool((_leaf(base, memory, signed=False, plc_model=plc_model) >> int(index, 16)) & 1)
    if address not in memory or memory[address] is None:
        raise UnknownInstructionSemantics('unknown_memory:'+address)
    value = memory[address]
    if bits > 16:
        value = 0
        for offset, target in enumerate(word_region_addresses(address, bits//16, plc_model)):
            word = memory.get(target)
            if word is None:
                raise UnknownInstructionSemantics('unknown_multiword_memory:'+address)
            value |= (word & 65535) << (16*offset)
    if signed:
        value &= (1 << bits)-1
        if value & (1 << (bits-1)):
            value -= 1 << bits
    return value


class ScanMachine:
    """An explicit state for one finite trace; no default-zero memory."""
    def __init__(self, ladder, *, plc_model, initial, execution_context, previous_inputs=None, previous_enables=None, groups=(), instructions=None, applications=None, timer_started_at_ms=None, execution_budget=None):
        self.ladder, self.model = ladder, plc_model
        self.execution_context = copy.deepcopy(execution_context or {})
        self.memory = {canonical_reference_device(k): copy.deepcopy(v) for k, v in initial.items()}
        self.previous = {canonical_reference_device(k): v for k, v in (previous_inputs or {}).items()}
        self.previous_enables = copy.deepcopy(previous_enables or {})
        self.facts = control_runtime_facts(plc_model)
        self.first = first_scan_fact(plc_model, execution_context)
        self.gaps, self.writes = [], []
        self.constraint_violations = []
        self.scan_index = 0
        self.groups = {r['id']: ({r['output']} if r['kind'] == 'event' else {
            decimal_region_address(e['target']['device'], e['target'].get('offset', 0))
            for t in r['transitions'] for e in t['effects']}) for r in groups}
        self.instructions = instructions if instructions is not None else [
            [(instruction, analyze_instruction_access(instruction['op'], instruction['args'], plc_model=plc_model))
             for instruction in lower_rung_instructions(rung)] for rung in ladder.get('rungs', [])]
        from plc.instruction_constraints import instruction_program_accesses
        self.execution_context.setdefault('program_accesses', instruction_program_accesses(ladder, plc_model=plc_model))
        self.applications = applications if applications is not None else {}
        self.timer_starts = copy.deepcopy(timer_started_at_ms or {})
        self.timestamp_ms = None
        from simulator.st_execution import ExecutionBudget
        self.execution_budget = execution_budget or ExecutionBudget()
        self._st_executions = {}
        from simulator.pwm import PWMPeripheral
        self.pwm = PWMPeripheral(self)
        self._pwm_frame_timestamp_missing = True

    def bit(self, address):
        address = canonical_reference_device(address)
        value = self.memory.get(address+'.contact') if address.startswith(('T', 'C')) else self.memory.get(address)
        if value is None and '.' in address:
            try:
                value = _leaf(address, self.memory, signed=False, plc_model=self.model)
            except (UnknownInstructionSemantics, ValueError):
                return None
        return None if value is None else bool(value)

    def write(self, address, value, location, kind='word', bits=16):
        address = canonical_reference_device(address)
        devices = digit_specified_devices(address, self.model)
        if devices is not None:
            for index, device in enumerate(devices):
                self.write(device, None if value is None else bool(int(value) & (1 << index)), location, 'bit')
            return
        targets = word_region_addresses(address, bits//16, self.model) if kind != 'bit' and bits > 16 else (address,)
        for offset, target in enumerate(targets):
            prior = self.memory.get(target)
            new = None if value is None else bool(value) if kind == 'bit' else ((int(value) >> (16*offset)) & 65535)
            self.memory[target] = new
            self.writes.append({'scan': self.scan_index, 'location': location, 'device': target,
                                'before': prior, 'after': new})

    def gap(self, reason, location):
        row = {'scan': self.scan_index, 'reason': reason, 'location': location}
        if row not in self.gaps:
            self.gaps.append(row)

    def _check_constraints(self, op, args, *, parameter_values=None, phase='input'):
        from plc.instruction_constraints import check_instruction_constraints, require_constraints, numeric_operand_type
        form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(op, cpu=self.model)
        values = {**dict(parameter_values or {}), '$OPERANDS': list(args)}
        if form:
            for index, name in enumerate(form.spec.native_operand_order):
                if (index not in form.spec.read_indexes and not (phase == 'result' and index in form.spec.write_indexes)
                        or index >= len(args)):
                    continue
                try:
                    value_type = numeric_operand_type(form.spec, index)
                    if name in values and not (phase == 'result' and index in form.spec.write_indexes):
                        number = values[name]
                    elif value_type and value_type.kind == 'int':
                        number = _leaf(args[index], self.memory, value_type.bits, value_type.signed, plc_model=self.model)
                        if re.fullmatch(r'H[0-9A-F]+', str(args[index]), re.I):
                            number = value_type.decode_bits(number) if number < 2 ** value_type.bits else None
                    elif re.fullmatch(r'K[+-]?\d+', str(args[index]), re.I):
                        number = int(str(args[index])[1:])
                    else:
                        number = None
                except (UnknownInstructionSemantics, ValueError, TypeError, OverflowError):
                    number = None
                values[name.upper()] = values['$' + str(index)] = number
            if phase == 'result' and len(form.spec.write_indexes) == 1:
                values['RESULT'] = values.get('$' + str(form.spec.write_indexes[0]))
        values.setdefault('EN', True)
        require_constraints(check_instruction_constraints(op, values, plc_model=self.model,
            language='st' if parameter_values is not None else 'ladder', phase=phase,
            version=self.execution_context.get('software_version'), context={**self.execution_context,
                'project_mode': 'st' if parameter_values is not None else 'native_ladder',
                'program_context': 'st' if parameter_values is not None else 'ladder',
                'hardware': self.execution_context.get('hardware') or self.execution_context.get('hardware_profile', {}),
                'consume_step': self.execution_budget.charge,
                'memory_reader': lambda base, offset: _leaf(decimal_region_address(base, offset), self.memory, plc_model=self.model)}))

    def _application(self, op, args, enabled, location, prior, *, parameter_values=None, writer=None):
        if op in {'PWM', 'DPWM'} and self.pwm.enabled:
            return self.pwm.execute(op, args, enabled, location, parameter_values=parameter_values)
        if enabled is True:
            self._check_constraints(op, args, parameter_values=parameter_values)
        if parameter_values is not None:
            transfer_bits = self.facts.get('transfers', {}).get(op) or (16 if op == 'MOV' and 'MOV' in self.facts.get('control', {}).get('forms', []) else None)
            if transfer_bits:
                behavior = {'behavior': 'expression_write', 'outputs': [{'target': {'parameter': 'D', 'offset': 0, 'kind': 'word'},
                    'expression': {'op': 'parameter', 'name': 'S', 'type': {'kind': 'int', 'bits': transfer_bits, 'signed': True}}}]}
                source_values = {name.upper(): value for name, value in parameter_values.items()}
                source_value = source_values['S'] if 'S' in source_values else _leaf(args[0], self.memory, transfer_bits, True, plc_model=self.model)
                result = execute_behavior(behavior, {'S': source_value, 'D': args[1]}, memory=self.memory,
                    state=self.memory, enabled=enabled, previous_enabled=prior, trigger='level', disabled='retain')
                for (base, offset), value in result['writes'].items():
                    writer(base, offset, value, location, 'word', transfer_bits)
                return
        if op in self.applications and isinstance(self.applications[op], str):
            raise UnknownInstructionSemantics(self.applications[op])
        form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(op, cpu=self.model)
        if not form or not form.spec.supports_cpu(self.model):
            self.applications[op] = 'unsupported_instruction:'+op
            raise UnknownInstructionSemantics('unsupported_instruction:'+op)
        for group in form.spec.definition_facts:
            if not group.dimension.startswith('effects.') or not group.value.get('behavior'):
                continue
            closure = self.applications.get((op, group.id))
            if closure is None:
                closure = select_fact_dependencies(form.spec.definition_facts, [group.id], opcode=op, model=self.model)
                self.applications[(op, group.id)] = closure
            if closure['gaps'] or not closure['source_verification_complete']:
                continue
            parameters = {}
            types = copy.deepcopy(next((g.value.get('operands', {}) for g in form.spec.definition_facts if g.dimension == 'data_types.operands'), {}))
            # Behavior parameter types provide independent widths even where
            # registry type group names vary. Destinations stay address tokens.
            def collect(node):
                if not isinstance(node, dict):
                    return
                if node.get('op') == 'parameter':
                    types[node['name']] = node['type']
                for child in node.get('args', []):
                    collect(child)
            behavior = group.value
            for output in behavior.get('outputs', []):
                collect(output['expression'])
            for key in ('count', 'shift'):
                if key in behavior:
                    collect(behavior[key])
            destinations = {o['target']['parameter'] for o in behavior.get('outputs', [])}
            destinations.update(behavior.get(k) for k in ('destination', 'region', 'source') if behavior.get(k))
            for name, value in zip(form.spec.native_operand_order, args):
                value_type = types.get(name, {})
                parameters[name] = (parameter_values[name] if parameter_values is not None and name in parameter_values and name not in destinations else
                    value if name in destinations or value_type.get('kind') == 'opaque' else _leaf(
                    value, self.memory, value_type.get('bits', 16), value_type.get('signed', True), plc_model=self.model))
            trigger = 'rising' if form.pulse or form.spec.execution_form in {'pulse', 'single', 'pulse_single', 'single_pulse'} else 'level'
            memory = self.memory
            grouped = {name: value for name, value in parameters.items() if name in destinations
                       and isinstance(value, str) and digit_specified_devices(value, self.model) is not None}
            if grouped:
                memory = {**self.memory, **{(value, 0): _leaf(value, self.memory, signed=False, plc_model=self.model)
                                            for value in grouped.values()}}
            result = execute_behavior(behavior, parameters, memory=memory, state=self.memory,
                                      enabled=enabled, previous_enabled=prior, trigger=trigger, disabled='retain')
            for (base, offset), value in result['writes'].items():
                definition = next((o for o in behavior.get('outputs', []) if parameters.get(o['target']['parameter']) == base and o['target'].get('offset', 0) == offset), None)
                kind = definition['target']['kind'] if definition else 'word'
                bits = Expression.from_mapping(definition['expression']).value_type.bits if definition and kind != 'bit' else 16
                if writer:
                    writer(base, offset, value, location, kind, bits or 16)
                else:
                    self.write(base if offset == 0 else decimal_region_address(base, offset), value, location, kind, bits or 16)
            if result.get('external_contract'):
                raise UnknownInstructionSemantics('external_action_not_executed')
            self._check_constraints(op, args, parameter_values=parameter_values, phase='result')
            return
        self.applications[op] = 'effect_fact_unavailable:'+op
        raise UnknownInstructionSemantics(self.applications[op])

    def scan(self, frame, *, first_scan=False):
        self.writes = []
        self.execution_budget.begin_scan()
        self.execution_context['initial_scan'] = bool(first_scan)
        group_snapshots = {}
        self._st_group_snapshots = group_snapshots
        self.memory.update({canonical_reference_device(k): v for k, v in frame.get('inputs', {}).items()})
        for device in self.facts.get('always_on', []):
            self.memory[device] = True
        if self.first.get('available'):
            for device in self.first['devices']:
                self.memory[device] = bool(first_scan)
        before = copy.deepcopy(self.memory)
        timers_seen = set()
        timer_drives = {}
        edge_samples = {}
        timestamp = frame.get('timestamp_ms')
        self._pwm_frame_timestamp_missing = timestamp is None
        if timestamp is not None:
            if type(timestamp) not in {int,float} or timestamp < 0 or self.timestamp_ms is not None and timestamp < self.timestamp_ms:
                raise ValueError('explicit_clock_not_monotonic')
            self.timestamp_ms = timestamp
        for rung_index, rung in enumerate(self.ladder.get('rungs', [])):
            accumulator, logic_stack, saved = True, [], []
            st_stack, st_enable = [], True
            # A branch connected directly to the left bus has its own TRUE
            # enable. It cannot inherit a preceding independent branch's guard.
            root_outputs = {f'branches[{index}].outputs[0]' for index, branch in enumerate(rung.get('branches', []))
                            if not rung.get('header_element') and not rung.get('shared_inputs')
                            and not branch.get('inputs')}
            for step, (instruction, access) in enumerate(self.instructions[rung_index]):
                self.execution_context['current_instruction'] = (rung_index, instruction.get('path'))
                op, args = instruction['op'], instruction['args']
                if instruction.get('path') in root_outputs:
                    accumulator = True
                location = {'rung_id': rung.get('rung_id'), 'rung_index': rung_index,
                            'instruction_index': step, 'path': instruction.get('path'), 'opcode': op, 'operands': args}
                position = f'{rung_index}.{step}'
                _reads, possible_writes = access
                for identity, targets in self.groups.items():
                    if identity not in group_snapshots and targets.intersection(possible_writes) and (accumulator is not False or op == 'OUT'):
                        group_snapshots[identity] = copy.deepcopy(self.memory)
                try:
                    record_kind = instruction.get('kind', 'instruction')
                    if record_kind in {'statement', 'note'}:
                        continue
                    if record_kind != 'instruction':
                        raise UnknownInstructionSemantics('source_record_' + record_kind + '_unverified')
                    if not self.facts.get('control') and not (op.startswith('ST_') or (op in {'PWM', 'DPWM'} and self.pwm.enabled) or op in
                            {'LD','LDI','AND','ANI','OR','ORI','ORB','ANB','MPS','MRD','MPP','END','NOP',*self.facts.get('transfers',{})}):
                        raise UnknownInstructionSemantics('cpu_control_form_unverified:' + op)
                    if op == 'ST_PROGRAM':
                        from simulator.st_execution import STExecution
                        identity = id(instruction)
                        execution = self._st_executions.get(identity)
                        if execution is None:
                            execution = self._st_executions[identity] = STExecution(self, instruction)
                        try:
                            execution.run()
                        except (ValueError, ArithmeticError, UnknownInstructionSemantics) as error:
                            constraint = getattr(error, 'constraint_report', None)
                            if constraint and constraint['status'] == 'failed':
                                self.constraint_violations.append({'location': location, 'scan': self.scan_index,
                                                                  'constraint': constraint})
                            # Unknown control/call scope cannot preserve stale
                            # output values and manufacture a behavior result.
                            for key in set(self.memory) | set(instruction.get('devices', [])):
                                self.memory[key] = None
                            self.gap(str(error), location)
                        continue
                    if op.startswith('ST_'):
                        if op == 'ST_IF':
                            st_stack.append([accumulator, False])
                        if op in {'ST_IF','ST_ELSIF','ST_ELSE'}:
                            parent, taken = st_stack[-1]
                            eligible = _and(parent, _not(taken))
                            value = False
                            if eligible is not False:
                                try:
                                    value = True if op == 'ST_ELSE' else bool(_evaluate(instruction['expression'], self.memory, self.model))
                                except (UnknownInstructionSemantics, ValueError):
                                    value = None
                            accumulator = _and(eligible, value)
                            st_stack[-1][1] = _or(taken, value)
                        elif op == 'ST_END_IF':
                            accumulator = st_stack.pop()[0]
                        elif op == 'ST_ENABLE':
                            st_enable = accumulator
                            if accumulator is not False:
                                try:
                                    accumulator = _and(accumulator, bool(_evaluate(instruction['expression'], self.memory, self.model)))
                                except (UnknownInstructionSemantics, ValueError):
                                    accumulator = None
                        elif op == 'ST_RESTORE':
                            accumulator = st_enable
                        elif op == 'ST_ASSIGN' and accumulator is not False:
                            expr = instruction['expression']
                            value = _evaluate(expr, self.memory, self.model) if accumulator is True else None
                            self.write(instruction['target'], value, location, 'bit' if expr['type']['kind']=='bool' else 'word')
                        continue
                    match = re.fullmatch(r'(LD|AND|OR)(I|P|F|=|==|<>|>=|<=|>|<)?', op)
                    if op in {'ANI', 'ORI'}:
                        match = re.fullmatch(r'(AND|OR)(I)', 'ANDI' if op == 'ANI' else 'ORI')
                    if match:
                        connector, suffix = match[1], match[2] or ''
                        if suffix in {'=', '==', '<>', '>=', '<=', '>', '<'}:
                            left, right = (_leaf(a, self.memory, plc_model=self.model) for a in args)
                            value = {'=': left == right, '==': left == right, '<>': left != right,
                                     '>=': left >= right, '<=': left <= right, '>': left > right, '<': left < right}[suffix]
                        else:
                            value = self.bit(args[0])
                            if suffix == 'I':
                                value = _not(value)
                            elif suffix in {'P', 'F'}:
                                current = value
                                previous = self.previous.get(canonical_device(args[0]))
                                edge_samples[canonical_device(args[0])] = current
                                value = None if current is None or previous is None else (
                                    bool(current) and not previous if suffix == 'P' else bool(previous) and not current)
                                if value is None:
                                    self.gap('source_edge_history_unverified', location)
                        if connector == 'LD':
                            logic_stack.append(accumulator)
                            accumulator = value
                        elif connector == 'AND':
                            accumulator = _and(accumulator, value)
                        else:
                            accumulator = _or(accumulator, value)
                        continue
                    if op in {'ANB', 'ORB'}:
                        if not logic_stack:
                            raise UnknownInstructionSemantics('logic_stack_underflow')
                        accumulator = (_and if op == 'ANB' else _or)(logic_stack.pop(), accumulator)
                        continue
                    if op == 'MPS':
                        saved.append(accumulator)
                        continue
                    if op in {'MRD', 'MPP'}:
                        if not saved:
                            raise UnknownInstructionSemantics('shared_stack_underflow')
                        accumulator = saved.pop() if op == 'MPP' else saved[-1]
                        continue
                    if op in {'END', 'NOP'}:
                        continue
                    prior = self.previous_enables.get(position)
                    self.previous_enables[position] = accumulator
                    if op in {'OUT', 'OUTH', 'OUTHS'} and args and args[0].startswith('T'):
                        if args[0] in timers_seen:
                            raise UnknownInstructionSemantics('repeated_timer_execution_unverified')
                        timers_seen.add(args[0])
                        timer_drives[args[0]] = accumulator
                        fact = timer_runtime_fact(self.model, args[0], op)
                        if not fact:
                            raise UnknownInstructionSemantics('timer_fact_unavailable')
                        if accumulator is False:
                            self.timer_starts.pop(args[0], None)
                            self.write(args[0], 0, location)
                            self.memory[args[0]+'.contact'] = False
                        elif accumulator is True:
                            elapsed = frame.get('timer_elapsed_ms', {}).get(args[0])
                            if elapsed is None and timestamp is not None:
                                if args[0] not in self.timer_starts:
                                    if prior is False or self.memory.get(args[0]) == 0 and self.memory.get(args[0]+'.contact') is False:
                                        self.timer_starts[args[0]] = timestamp
                                    else:
                                        raise UnknownInstructionSemantics('initial_timer_clock_state_unknown')
                                elapsed = timestamp-self.timer_starts[args[0]]
                            if type(elapsed) not in {int, float} or elapsed < 0:
                                raise UnknownInstructionSemantics('explicit_timer_elapsed_time_missing')
                            preset = _leaf(args[1], self.memory, signed=False, plc_model=self.model)
                            count = int(elapsed // fact['unit_ms'])
                            self.write(args[0], min(count, preset), location)
                            self.memory[args[0]+'.contact'] = count >= preset
                        else:
                            self.write(args[0], None, location)
                            self.memory[args[0]+'.contact'] = None
                        continue
                    if op == 'OUT' and len(args) == 2 and args[0].startswith('C'):
                        fact = self.facts.get('counter', {})
                        number = int(args[0][1:])
                        if not fact or not fact['first'] <= number <= fact['last']:
                            raise UnknownInstructionSemantics('counter_fact_unavailable')
                        value = self.memory.get(args[0])
                        preset = _leaf(args[1], self.memory, signed=False, plc_model=self.model)
                        if accumulator is True and prior is False and value is not None:
                            self.write(args[0], min(value+1, preset), location)
                        elif accumulator is None or accumulator is True and prior is None:
                            self.write(args[0], None, location)
                            self.gap('counter_edge_history_unverified', location)
                        value = self.memory.get(args[0])
                        self.memory[args[0]+'.contact'] = None if value is None else value >= preset
                        continue
                    if op == 'OUT' and len(args) == 1:
                        self.write(args[0], accumulator, location, 'bit')
                        continue
                    if op in {'PLS', 'PLF'}:
                        # Unknown history still prevents claiming an edge. A
                        # known OFF PLS drive (or ON PLF drive) cannot fire under
                        # either possible history: JY997D16601-R 7.12, p213-214.
                        value = (_and(accumulator, _not(prior)) if op == 'PLS'
                                 else _and(prior, _not(accumulator)))
                        self.write(args[0], value, location, 'bit')
                        if value is None:
                            self.gap('pulse_history_unverified', location)
                        continue
                    if op in {'PWM', 'DPWM'} and self.pwm.enabled:
                        self.pwm.execute(op, args, accumulator, location)
                        continue
                    if accumulator is False:
                        continue
                    if accumulator is None:
                        raise UnknownInstructionSemantics('enable_depends_on_unknown_memory')
                    if op in {'SET', 'RST'} and op in self.facts.get('control', {}).get('forms', []):
                        self.write(args[0], op == 'SET' if args[0].startswith(('M', 'Y', 'S')) else 0, location,
                                   'bit' if args[0].startswith(('M', 'Y', 'S')) else 'word')
                        if op == 'RST' and args[0].startswith(('T', 'C')):
                            self.memory[args[0]+'.contact'] = False
                            self.timer_starts.pop(args[0], None)
                            if args[0].startswith('T') and timer_drives.get(args[0]) is True and timestamp is not None:
                                # RST clears an already executing timer now. Its
                                # next continuously enabled OUT must not add a
                                # second scan before starting a fresh interval.
                                self.timer_starts[args[0]] = timestamp
                            # A reset establishes the count drive's disabled
                            # phase only when its OUT is also evaluated false.
                        continue
                    if op == 'ZRST' and op in self.facts.get('control', {}).get('forms', []):
                        _reads, writes = access
                        if not writes:
                            raise UnknownInstructionSemantics('reset_range_unverified')
                        for address in sorted(writes):
                            self.write(address, 0, location, 'bit' if address.startswith(('M', 'Y', 'S')) else 'word')
                            if address.startswith(('T', 'C')):
                                self.memory[address+'.contact'] = False
                                self.timer_starts.pop(address,None)
                                if address.startswith('T') and timer_drives.get(address) is True and timestamp is not None:
                                    self.timer_starts[address] = timestamp
                        continue
                    transfer_bits = self.facts.get('transfers', {}).get(op)
                    if transfer_bits or op == 'MOV' and 'MOV' in self.facts.get('control', {}).get('forms', []):
                        self._check_constraints(op, args)
                        bits = transfer_bits or 16
                        # The same formal expression/effect interpreter is used
                        # for the shared source-scoped MOV control subset.
                        behavior = {'behavior': 'expression_write', 'outputs': [{'target': {'parameter': 'D', 'offset': 0, 'kind': 'word'},
                            'expression': {'op': 'parameter', 'name': 'S', 'type': {'kind': 'int', 'bits': bits, 'signed': False}}}]}
                        result = execute_behavior(behavior, {'S': _leaf(args[0], self.memory, bits=bits, signed=False, plc_model=self.model), 'D': args[1]}, memory=self.memory)
                        for (base, offset), value in result['writes'].items():
                            self.write(base if offset == 0 else decimal_region_address(base, offset), value, location, bits=bits)
                        continue
                    self._application(op, args, accumulator, location, prior)
                except (UnknownInstructionSemantics, ValueError, KeyError, IndexError) as error:
                    constraint = getattr(error, 'constraint_report', None)
                    if constraint and constraint['status'] == 'failed':
                        self.constraint_violations.append({'location': location, 'scan': self.scan_index,
                                                          'constraint': constraint})
                    self.gap(str(error), location)
                    if instruction.get('kind') in {'opaque', 'label'}:
                        # The remaining control flow and write scope are not
                        # known. A suffix executed against unchanged memory
                        # would manufacture conclusions about this source.
                        self.memory = {key: None for key in self.memory}
                        self.scan_index += 1
                        return {'before': before, 'after': copy.deepcopy(self.memory),
                                'writes': copy.deepcopy(self.writes), 'group_snapshots': group_snapshots, 'peripherals': {'pwm': self.pwm.snapshot()} if self.pwm.enabled else {}}
                    if op.startswith(('LD', 'AND', 'OR')):
                        accumulator = None
                    else:
                        _reads, writes = access
                        if op in {'OUT', 'OUTH', 'OUTHS'} and args and args[0].startswith('T'):
                            writes = sorted(set(writes) | {args[0]})
                        for address in writes:
                            self.write(address, None, location, 'bit' if address.startswith(('M', 'Y', 'S')) else 'word')
                            if address.startswith(('T', 'C')):
                                self.memory[address+'.contact'] = None
                        # A known opcode does not prove the write scope of an
                        # unsupported operand form (e.g. digit-specified MOV).
                        # Continuing with unchanged memory cannot establish a
                        # violation of the actual program.
                        if not writes:
                            self.gap('unknown_instruction_write_scope', location)
        self.previous.update(edge_samples)
        self.scan_index += 1
        return {'before': before, 'after': copy.deepcopy(self.memory), 'writes': copy.deepcopy(self.writes), 'group_snapshots': group_snapshots, 'peripherals': {'pwm': self.pwm.snapshot()} if self.pwm.enabled else {}}


def _evaluate(expression, memory, plc_model='FX3U'):
    required = {}
    def collect(expr):
        if expr.op == 'device':
            if expr.name not in memory or memory[expr.name] is None:
                return
            if expr.value_type.kind == 'bool' and expr.name.startswith(('T', 'C')):
                if memory.get(expr.name+'.contact') is not None:
                    required[expr.name] = bool(memory[expr.name+'.contact'])
            else:
                required[expr.name] = _leaf(expr.name, memory, expr.value_type.bits or 16, expr.value_type.signed, plc_model=plc_model)
        for arg in expr.args:
            collect(arg)
    expr = Expression.from_mapping(expression)
    collect(expr)
    return evaluate_expression(expr, required)


def _expect_relation(row, observation, histories, *, first_scan, plc_model='FX3U'):
    before, after = observation['before'], observation['after']
    kind = row['kind']
    if kind == 'initialize':
        return initialization_values(row) if first_scan else {}
    if kind == 'assertion':
        # Assertions describe the end-of-scan relation; transition conditions
        # separately describe the shared pre-transition state.
        if _evaluate(row['when'], after, plc_model):
            return {'__assertion__': bool(_evaluate(row['predicate'], after, plc_model))}
        return {}
    if kind == 'event':
        source = bool(_evaluate(row['source'], before, plc_model))
        record = histories.setdefault(row['id'], {'previous': None, 'armed': False})
        edge = row['edge']
        opposite = not source if edge == 'rising' else source
        if first_scan:
            fired = row['startup_policy'] == 'allow_initial_event' and not opposite
            record['armed'] = opposite
        else:
            fired = record['previous'] is not None and (source and not record['previous'] if edge == 'rising' else record['previous'] and not source)
            if row['startup_policy'] == 'require_opposite':
                fired = fired and record['armed']
            if opposite:
                record['armed'] = True
        record['previous'] = source
        accepted = bool(fired and _evaluate(row['accept'], before, plc_model))
        histories['event.'+row['id']] = accepted
        return {row['output']: accepted}
    if kind == 'merge_events':
        if any('event.'+identity not in histories for identity in row['events']):
            raise UnknownInstructionSemantics('event_reference_unverified')
        value = any(histories['event.'+identity] for identity in row['events'])
        histories['event.'+row['id']] = value
        return {row['output']: value}
    if first_scan:
        return {}
    targets = {decimal_region_address(e['target']['device'], e['target'].get('offset', 0))
               for t in row['transitions'] for e in t['effects']}
    expected = {}
    for target in targets:
        if before.get(target) is None:
            raise UnknownInstructionSemantics('transition_prestate_unknown:'+target)
        expected[target] = before[target]
    if not _evaluate(row['enable'], before, plc_model):
        return expected
    for transition in row['transitions']:
        if _evaluate(transition['when'], before, plc_model):
            for effect in transition['effects']:
                if effect.get('kind', 'value') != 'value':
                    raise UnknownInstructionSemantics('transition_effect_check_unverified')
                target = decimal_region_address(effect['target']['device'], effect['target'].get('offset', 0))
                value = _evaluate(effect['value'], before, plc_model)
                value_type = Expression.from_mapping(effect['value']).value_type
                if value_type.kind == 'int':
                    for offset in range(max(1,value_type.bits//16)):
                        expected[decimal_region_address(target,offset)] = (int(value) >> (16*offset)) & 65535
                else:
                    expected[target] = value
            break
    return expected


def check_bounded_traces(program, spec, traces, *, version_binding=None, max_traces=MAX_TRACES, max_scans=MAX_SCANS, frozen_time_traces=()):
    """Every result is version-bound and states its finite coverage and assumptions."""
    ir_program = is_plc_ir(program)
    ladder = ir_to_ladder(program) if ir_program else program
    program_model = str((program.get('plc') or {}).get('cpu') or '').strip().upper() if ir_program else ''
    declared_model = str(spec.get('plc_model') or '').strip().upper()
    # Positive specification projection deliberately omits runtime identity.
    # The validated IR owns that identity; never replace it with an FX default.
    model = program_model or declared_model
    st_instructions, st_reason = None, None
    if isinstance(program.get('st_code'), str):
        from plc.st_validation import bounded_st_instructions
        try:
            st_instructions = bounded_st_instructions(program['st_code'], plc_model=model, confirmed_spec=spec,
                                                     project_context=program.get('project_context'))
        except (ValueError, RecursionError) as error:
            st_reason = str(error)
        ladder = {'rungs':[{'rung_id':1,'branches':[]}]}
    model_reason = ('program_cpu_conflicts_with_specification' if program_model and declared_model and program_model != declared_model
                    else 'missing_cpu_control_facts' if not (control_runtime_facts(model).get('control') or control_runtime_facts(model).get('transfers')) else st_reason)
    binding = {'revision': program.get('revision'), 'ir_sha256': canonical_sha256(program)} if ir_program else {
        'st_sha256' if 'st_code' in program else 'ladder_sha256': canonical_sha256(program)}
    binding.update(version_binding or {})
    binding['plc_model'] = model or None
    rows = [r for r in normalize_behavior_constraints(spec.get('behavior_constraints', [])) if r['status'] == 'confirmed']
    context = (spec.get('selected_approach') or {}).get('construction_plan', {}).get('execution_context', {})
    first = first_scan_fact(model, context)
    checks = {r['id']: {'requirement_id': r['id'], 'status': 'unverified', 'traces_tested': 0,
                       'scans_tested': 0, 'unknown_observations': 0, 'violations': [], 'reasons': []} for r in rows}
    if model_reason:
        for check in checks.values():
            check['reasons'].append(model_reason)
        return {'schema_version': 1, 'stage': 'Core_bounded_scan_check', 'status': 'unverified',
                'program_version': binding, 'checks': list(checks.values()), 'violations': [],
                'coverage': {'traces_tested': 0, 'maximum_scans': 0, 'automatic_trace_limit': min(max_traces, MAX_TRACES),
                             'scan_limit': min(max_scans, MAX_SCANS), 'budget_exhausted': False,
                             'frozen_time_traces': len(frozen_time_traces)},
                'unsupported_or_missing': [{'reason': model_reason, 'program_cpu': program_model or None,
                                            'specification_cpu': declared_model or None}],
                'checker_exceptions': [], 'assumptions': ['execution not attempted without an unambiguous source-scoped CPU'],
                'first_scan_fact': first, 'activation_blocked': False}
    tested, truncated, maximum_scans, unsupported, exceptions = 0, False, 0, [], []
    instructions = [[(instruction, analyze_instruction_access(instruction['op'], instruction['args'], plc_model=model))
                     for instruction in lower_rung_instructions(rung)] for rung in ladder.get('rungs', [])]
    if st_instructions is not None:
        instructions = [st_instructions]
    applications, unsupported_index = {}, {}
    constraint_violations = []
    peripheral_traces = []
    from simulator.st_execution import ExecutionBudget
    execution_budget = ExecutionBudget()
    groups = [(False, traces), (True, frozen_time_traces)]
    for frozen_time, source in groups:
        for index, trace in enumerate(source):
            if not frozen_time and index >= min(max_traces, MAX_TRACES):
                truncated = True
                break
            frames = trace.get('frames', [])
            if not isinstance(trace.get('initial'), dict) or not frames or len(frames) > min(max_scans, MAX_SCANS):
                for check in checks.values():
                    check['reasons'].append('trace_initial_or_scan_bound_invalid')
                continue
            machine = ScanMachine(ladder, plc_model=model, initial=trace['initial'],
                                  execution_context=trace.get('execution_context', context),
                                  previous_inputs=trace.get('previous_inputs'), previous_enables=trace.get('previous_enables'),
                                  groups=[r for r in rows if r['kind'] in {'event', 'transition_group'}], instructions=instructions,
                                  applications=applications,timer_started_at_ms=trace.get('timer_started_at_ms'), execution_budget=execution_budget)
            explicit = trace.get('expectations', [])
            try:
                if not isinstance(explicit, list) or len(explicit) > 128:
                    raise ValueError('explicit_trace_expectations_invalid')
                for expectation in explicit:
                    if set(expectation) != {'scan', 'requirement_id', 'predicate'} or type(expectation['scan']) is not int or not 0 <= expectation['scan'] < len(frames):
                        raise ValueError('explicit_trace_expectations_invalid')
                    declared = normalize_behavior_constraints([{'id':expectation['requirement_id'], 'kind':'assertion', 'status':'confirmed',
                        'when':{'op':'constant','type':{'kind':'bool'},'value':True}, 'predicate':expectation['predicate'],
                        'provenance':{'source':'user_authored','evidence':['explicit independently frozen trace expectation']}}])[0]
                    identity = declared['id']
                    if identity not in checks:
                        checks[identity] = {'requirement_id':identity,'status':'unverified','traces_tested':0,'scans_tested':0,
                            'unknown_observations':0,'violations':[],'reasons':[],'scope':'explicit_trace_expectation'}
                for identity in {e['requirement_id'] for e in explicit}:
                    checks[identity]['traces_tested'] += 1
            except (ValueError, TypeError, KeyError) as error:
                for check in checks.values(): check['reasons'].append('explicit_trace_expectations_invalid')
                continue
            histories = copy.deepcopy(trace.get('event_history', {}))
            tested += 1
            maximum_scans = max(maximum_scans, len(frames))
            for row in rows:
                checks[row['id']]['traces_tested'] += 1
            for scan_index, frame in enumerate(frames):
                boot = bool(trace.get('starts_in_run', True) and scan_index == 0)
                try:
                    observation = machine.scan(frame, first_scan=boot)
                    if observation.get('peripherals'):
                        peripheral_traces.append({'trace_id': trace.get('id', index), 'scan': scan_index,
                                                  'peripherals': observation['peripherals']})
                except Exception as error:
                    exceptions.append({'trace_id': trace.get('id', index), 'scan': scan_index,
                                       'reason': 'checker_exception', 'exception_type': type(error).__name__})
                    break
                # Groups sample conditions where their first target write is
                # executed. Independent groups may see earlier groups' writes;
                # all members within one group use that single snapshot.
                for row in rows:
                    check = checks[row['id']]
                    check['scans_tested'] += 1
                    check_observation = observation
                    if row['kind'] == 'event' and row['id'] in observation['group_snapshots']:
                        check_observation = {**observation, 'before': observation['group_snapshots'][row['id']]}
                    if row['kind'] == 'transition_group':
                        snapshot = copy.deepcopy(observation['group_snapshots'].get(row['id'], observation['before']))
                        for event in rows:
                            if event['kind'] in {'event', 'merge_events'} and 'event.'+event['id'] in histories:
                                snapshot[event['output']] = histories['event.'+event['id']]
                        check_observation = {**observation, 'before': snapshot}
                    try:
                        if row['kind'] in {'initialize', 'event', 'transition_group'} and not machine.first.get('available'):
                            raise UnknownInstructionSemantics('first_scan_condition_unverified')
                        expected = _expect_relation(row, check_observation, histories, first_scan=boot, plc_model=model)
                        if row['kind'] == 'transition_group' and not boot:
                            repeated = [device for device in expected if sum(
                                w['device'] == device and w['after'] is not None
                                for w in observation['writes']) > 1]
                            if repeated and not check['violations']:
                                check['violations'].append({'requirement_id': row['id'], 'construct_group': row['id'],
                                    'trace_id': trace.get('id',index), 'scan': scan_index, 'reason':'repeated_group_write',
                                    'scan_before': check_observation['before'], 'input_sequence': copy.deepcopy(frames[:scan_index+1]),
                                    'writes':[w for w in observation['writes'] if w['device'] in repeated], 'program_version':binding})
                        for device, value in expected.items():
                            actual = expected[device] if device == '__assertion__' else observation['after'].get(device)
                            if device == '__assertion__':
                                actual, value = actual, True
                            if actual is None:
                                raise UnknownInstructionSemantics('result_unknown:'+device)
                            if actual != value:
                                writes = [w for w in observation['writes'] if w['device'] == device]
                                witness = {'requirement_id': row['id'], 'construct_group': row['id'],
                                           'trace_id': trace.get('id', index), 'scan': scan_index,
                                           'scan_before': observation['before'], 'input_sequence': copy.deepcopy(frames[:scan_index+1]),
                                           'initial': copy.deepcopy(trace['initial']), 'device': device, 'expected': value, 'actual': actual,
                                           'writes': writes, 'program_version': binding}
                                if not check['violations']:
                                    check['violations'].append(witness)
                        check['status'] = 'violated' if check['violations'] else 'no_violation_found_in_tested_scope'
                    except (ValueError, KeyError, IndexError) as error:
                        check['unknown_observations'] += 1
                        if str(error) not in check['reasons']:
                            check['reasons'].append(str(error))
                for expectation in explicit:
                    if expectation['scan'] != scan_index:
                        continue
                    identity = expectation['requirement_id']
                    check = checks[identity]
                    check['scans_tested'] += 1
                    try:
                        actual = bool(_evaluate(expectation['predicate'], observation['after'], model))
                        if not actual and not check['violations']:
                            from plc.construction import expression_devices
                            affected = expression_devices(expectation['predicate'])
                            check['violations'].append({'requirement_id':identity,'construct_group':identity,
                                'reason':'explicit_progress_or_time_expectation','trace_id':trace.get('id',index),'scan':scan_index,
                                'initial':copy.deepcopy(trace['initial']),'scan_before':observation['before'],
                                'input_sequence':copy.deepcopy(frames[:scan_index+1]),'expected':True,'actual':False,
                                'predicate':copy.deepcopy(expectation['predicate']),
                                'writes':[w for w in observation['writes'] if w['device'] in affected],'program_version':binding})
                        check['status'] = 'violated' if check['violations'] else 'no_violation_found_in_tested_scope'
                    except (ValueError, KeyError, IndexError) as error:
                        check['unknown_observations'] += 1
                        if str(error) not in check['reasons']: check['reasons'].append(str(error))
            for gap in machine.gaps:
                key = (gap['reason'], json.dumps(gap['location'], sort_keys=True))
                if key not in unsupported_index:
                    entry = {**gap, 'first_trace_id': trace.get('id', index), 'observations': 0}
                    unsupported_index[key] = entry
                    unsupported.append(entry)
                unsupported_index[key]['observations'] += 1
            constraint_violations.extend({**row, 'trace_id': trace.get('id', index), 'program_version': binding}
                                         for row in machine.constraint_violations)
    for check in checks.values():
        if check['violations']:
            check['status'] = 'violated'
        elif check['unknown_observations'] or check['reasons'] or exceptions or truncated or not tested:
            check['status'] = 'unverified'
            if truncated:
                check['reasons'].append('trace_budget_exhausted')
    unknown_write_scope = any(r['reason'] == 'unknown_instruction_write_scope' for r in unsupported)
    if unknown_write_scope:
        for check in checks.values():
            check['status'] = 'unverified'
            check['reasons'].append('unknown_instruction_write_scope')
            if check['violations']:
                check['unverified_witnesses'] = check['violations']
                check['violations'] = []
    statuses = {c['status'] for c in checks.values()}
    status = 'violated' if 'violated' in statuses or constraint_violations else 'unverified' if not checks or 'unverified' in statuses else 'no_violation_found_in_tested_scope'
    return {'schema_version': 1, 'stage': 'Core_bounded_scan_check', 'status': status, 'program_version': binding,
            'checks': list(checks.values()), 'violations': [v for c in checks.values() for v in c['violations']],
            'constraint_violations': constraint_violations,
            'peripheral_observations': peripheral_traces,
            'coverage': {'traces_tested': tested, 'maximum_scans': maximum_scans, 'automatic_trace_limit': min(max_traces, MAX_TRACES),
                         'scan_limit': min(max_scans, MAX_SCANS), 'budget_exhausted': truncated, 'frozen_time_traces': len(frozen_time_traces)},
            'unsupported_or_missing': unsupported, 'checker_exceptions': exceptions,
            'assumptions': ['only declared initial memory is known', 'source-scoped exact CPU control subset',
                            'timer elapsed time is explicit; never inferred from scan count', 'bounded instruction-order execution; no native PLC acceptance'],
            'first_scan_fact': first, 'activation_blocked': status == 'violated'}


def finite_behavior_traces(spec, *, max_traces=MAX_TRACES):
    """Finite bit combinations and 2-8 scan motifs, not random default memory."""
    from plc.construction import expression_devices
    rows = [r for r in normalize_behavior_constraints(spec.get('behavior_constraints', [])) if r['status'] == 'confirmed']
    initial = {}
    from plc.specification.conditions import external_input_bindings
    external = {canonical_device(row['address']) for row in external_input_bindings(spec.get('io_bindings', []))
                if re.fullmatch(r'[XMS]\d+', str(row.get('address', '')), re.I)}
    inputs = set(external)
    for row in rows:
        if row['kind'] == 'initialize':
            initial.update(initialization_values(row))
        for key in ('source', 'accept', 'enable', 'predicate', 'when'):
            if key in row:
                inputs.update(d for d in expression_devices(row[key]) if d.startswith('X') or d in external)
        for transition in row.get('transitions', []):
            inputs.update(d for d in expression_devices(transition['when']) if d.startswith('X') or d in external)
    ordered = sorted(inputs)
    limit = min(max_traces, MAX_TRACES)
    produced = 0
    # Boot from old nonzero bits/words, followed by source low/high/recovery and
    # adjacent events. These explicit initial alternatives are assumptions,
    # never a declaration that other PLC memory is zero.
    initial_options = [initial, {d: (not bool(v) if d.startswith(('M', 'Y')) else 7) for d, v in initial.items()}]
    combinations = itertools.product((False, True), repeat=len(ordered))
    for values in combinations:
        state = dict(zip(ordered, values))
        complement = {d: not v for d,v in state.items()}
        for old in initial_options:
            sequences = [[state], [state, state, complement, state, complement, state],
                         [state, complement, state, state, complement, complement, state, state]]
            # A single input may withdraw while stop/fault stays unchanged.
            # Flipping all inputs together can hide a missing OFF assignment.
            sequences.extend([state, {**state, device:not state[device]}, state] for device in ordered)
            for sequence in sequences:
                # One sentinel trace lets the checker record truncation rather
                # than confusing a cut-off generator with exhaustive coverage.
                if produced > limit:
                    return
                produced += 1
                yield {'id': f'finite.{produced}', 'initial': copy.deepcopy(old),
                       'frames': [{'inputs': copy.deepcopy(frame)} for frame in sequence], 'starts_in_run': True}


def check_confirmed_behavior(program, spec, *, version_binding=None, traces=None, frozen_time_traces=()):
    if not spec or not spec.get('behavior_constraints'):
        return {'status': 'not_applied', 'checks': [], 'violations': [], 'activation_blocked': False}
    return check_bounded_traces(program, spec, finite_behavior_traces(spec) if traces is None else traces,
                               version_binding=version_binding, frozen_time_traces=frozen_time_traces)
