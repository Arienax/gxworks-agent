# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Opt-in bounded PWM software peripheral, called by the production ScanMachine.

This models command/state contracts, not electrical phase or native hardware.
The source-scoped admission contract is shared with PLC validation. Full graph
admission, physical output levels, count monitors and precise stop latency remain
unverified. No per-scan toggling of a hardware-offloaded Y output is performed.
"""
from __future__ import annotations
from collections.abc import Mapping
import copy
import math
import re
from plc.instruction_definition import UnknownInstructionSemantics
from plc.device_identity import canonical_reference_device


class PWMPeripheral:
    def __init__(self, machine):
        self.machine = machine
        self.channels = {}
        self.events = []
        self.last_attempt = None
        self.attempts = {}
        self.invalidated_owners = {}

    @property
    def enabled(self):
        pwm = self.machine.execution_context.get('pwm')
        return isinstance(pwm, Mapping) and pwm.get('execution_mode') == 'core_software_model'

    def execute(self, opcode, args, enabled, location, *, parameter_values=None):
        try:
            result = self._execute(opcode, args, enabled, location, parameter_values=parameter_values)
        except (UnknownInstructionSemantics, ValueError, KeyError, IndexError) as error:
            self.last_attempt = {'scan': self.machine.scan_index, 'status': 'unverified', 'reason': str(error)}
            self.attempts[self.owner_id(location)] = copy.deepcopy(self.last_attempt)
            self.invalidate_failed_request(args, location)
            raise
        self.last_attempt = {'scan': self.machine.scan_index, 'status': 'accepted_bounded_contract'}
        self.attempts[self.owner_id(location)] = copy.deepcopy(self.last_attempt)
        return result

    @staticmethod
    def owner_id(location):
        return str(location['rung_index']) + ':' + str(location['path'])

    def instruction_owners(self):
        return [{'owner_id': str(index) + ':' + str(instruction.get('path')),
                 'rung_index': index, 'path': instruction.get('path'),
                 'opcode': instruction['op'], 'operands': instruction['args']}
                for index, instructions in enumerate(self.machine.instructions)
                for instruction, _access in instructions if instruction['op'] in {'PWM', 'DPWM'}]

    def resolve_request(self, opcode, args, owner):
        from plc.pwm_contract import validate_pwm_request
        from simulator.bounded import _leaf
        m = self.machine
        if len(args) != 3:
            raise UnknownInstructionSemantics('pwm_operand_arity_unverified')
        context = copy.deepcopy(m.execution_context)
        # This dispatcher consumes Ladder instructions. Caller metadata cannot
        # disable its mandatory predicates by claiming another language.
        context['language'] = 'ladder'
        bindings = context['pwm'].pop('bindings', None)
        if bindings is not None:
            if not isinstance(bindings, Mapping) or not isinstance(bindings.get(owner), Mapping):
                raise UnknownInstructionSemantics('pwm_instruction_binding_required:' + owner)
            context['pwm'].update(copy.deepcopy(bindings[owner]))
        if re.fullmatch(r'(?:D|W|R|SD|SW|T|ST|C|Z)\d+', str(args[2]).upper()):
            context['pwm']['destination_value'] = _leaf(args[2], m.memory, bits=16, signed=False, plc_model=m.model)
        bits = 32 if opcode == 'DPWM' else 16
        if bits == 32:
            from plc.device_identity import word_region_addresses
            for operand in args[:2]:
                if re.fullmatch(r'(?:D|W|R|SD|SW|T|ST|C|Z)\d+', str(operand).upper()):
                    for word in word_region_addresses(str(operand).upper(), 2, m.model):
                        value = m.memory.get(word)
                        if type(value) is not int or not 0 <= value <= 65535:
                            raise UnknownInstructionSemantics('pwm_multiword_memory_unknown:' + word)
        request = validate_pwm_request(opcode, m.model,
            _leaf(args[0], m.memory, bits=bits, signed=opcode == 'DPWM', plc_model=m.model),
            _leaf(args[1], m.memory, bits=bits, signed=opcode == 'DPWM', plc_model=m.model),
            args[2], context)
        request['time_unit'] = context['pwm']['time_unit']
        return request

    def preflight(self, owner_id=None):
        m = self.machine
        requests = {}
        channels, outputs = set(), set()
        reserved = set()
        for row in self.instruction_owners():
            owner = row['owner_id']
            if owner_id is not None and owner != owner_id:
                continue
            request = self.resolve_request(row['opcode'], row['operands'], owner)
            channel, output = request['channel'], request['output_device']
            bindings = m.execution_context.get('pwm', {}).get('bindings')
            base_binding = dict(m.execution_context.get('pwm', {}))
            for peer in self.instruction_owners():
                if peer['owner_id'] == owner:
                    continue
                if bindings is not None:
                    if not isinstance(bindings, Mapping) or not isinstance(bindings.get(peer['owner_id']), Mapping):
                        raise UnknownInstructionSemantics('pwm_instruction_binding_required:' + peer['owner_id'])
                    peer_binding = {**base_binding, **bindings[peer['owner_id']]}
                else:
                    peer_binding = base_binding
                peer_output = canonical_reference_device(peer_binding.get('output_device'))
                if len(peer['operands']) == 3 and re.fullmatch(r'Y[0-7]', peer['operands'][2], re.I):
                    peer_output = canonical_reference_device(peer['operands'][2])
                if peer_binding.get('channel') == channel or peer_output == output:
                    raise UnknownInstructionSemantics('pwm_resource_conflict:' + output)
            if channel in channels or output in outputs:
                raise UnknownInstructionSemantics('pwm_resource_conflict:' + output)
            channels.add(channel); outputs.add(output)
            base = 5300 + 16 * (channel - 1)
            reserved.update({output, *('SD' + str(base + offset) for offset in range(8)),
                             *('SM' + str(start + channel - 1) for start in (5300, 5316, 5332))})
            for register in ('SD' + str(base), 'SD' + str(base + 1)):
                if register in m.memory and (type(m.memory[register]) is not int or m.memory[register] != 0):
                    raise UnknownInstructionSemantics('pwm_count_register_not_unlimited:' + register)
            for old_channel, prior in self.channels.items():
                if prior['owner_id'] == owner and old_channel != channel:
                    raise UnknownInstructionSemantics('pwm_channel_reconfiguration_unverified')
            prior = self.channels.get(channel)
            if prior and prior['owner_id'] != owner:
                raise UnknownInstructionSemantics('pwm_channel_already_owned')
            if prior and any(prior['request'][key] != request[key] for key in
                             ('on_time_us', 'period_us', 'output_device', 'on_time', 'period', 'time_unit', 'source_binding')):
                raise UnknownInstructionSemantics('pwm_dynamic_update_unverified')
            requests[owner] = request
        # Validate every peer and resource before the first channel can commit.
        for instructions in m.instructions:
            for instruction, access in instructions:
                op = instruction['op']
                if op in {'PWM', 'DPWM'}:
                    continue
                writes = {canonical_reference_device(address) for address in access[1]}
                if reserved.intersection(writes):
                    raise UnknownInstructionSemantics('pwm_resource_conflict:' + ','.join(sorted(reserved.intersection(writes))))
                if not access[1]:
                    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, InstructionCategory
                    spec = DEFAULT_INSTRUCTION_REGISTRY.resolve_source_form(op, len(instruction.get('args', ())), cpu=m.model)
                    known_read_only = (spec is not None and not spec.write_indexes
                        or spec is not None and spec.category in {InstructionCategory.CONDITION, InstructionCategory.BRANCH_CONTROL})
                    if not known_read_only and op not in {'END', 'FEND', 'RET', 'NOP', 'MPS', 'MRD', 'MPP', 'ANB', 'ORB', 'INV', 'MEP', 'MEF'}:
                        raise UnknownInstructionSemantics('pwm_peer_write_scope_unverified:' + op)
        return requests

    def _execute(self, opcode, args, enabled, location, *, parameter_values=None):
        m = self.machine
        if parameter_values is not None:
            raise UnknownInstructionSemantics('pwm_st_call_unverified')
        if len(args) != 3:
            raise UnknownInstructionSemantics('pwm_operand_arity_unverified')
        if type(enabled) is not bool:
            raise UnknownInstructionSemantics('pwm_enable_unknown')
        if not enabled and not any(state['owner_id'] == self.owner_id(location) for state in self.channels.values()):
            return
        if type(m.timestamp_ms) not in (int, float) or not math.isfinite(m.timestamp_ms):
            raise UnknownInstructionSemantics('pwm_explicit_clock_required')
        if m._pwm_frame_timestamp_missing:
            raise UnknownInstructionSemantics('pwm_explicit_clock_required')
        owner_id = self.owner_id(location)
        prior = next((state for state in self.channels.values() if state['owner_id'] == owner_id), None)
        if not enabled and prior is None:
            return
        request = prior['request'] if not enabled else self.preflight(owner_id)[owner_id]
        stop = m.memory.get('SM8034')
        if type(stop) is not bool:
            raise UnknownInstructionSemantics('pwm_stop_bit_unknown:SM8034')
        channel, output = request['channel'], request['output_device']
        owner = (location['rung_index'], location['path'])
        prior = self.channels.get(channel)
        # All admission and safety checks precede channel/state/memory writes.
        next_state = copy.deepcopy(prior) if prior else {
            'owner': list(owner), 'owner_id': owner_id, 'request': request, 'generation': 0,
            'first_command_ms': None, 'run_command_ms': None,
            'pulse_count_mode': 'unlimited', 'pulse_count_limit': 0,
            'physical_pulse_count': None, 'physical_level': None,
            'stop_latency_us': None, 'monitor_registers': 'unverified',
            'full_graph_admission': 'unverified', 'hardware_effect': 'not_tested',
            'native_execution': 'not_tested', 'scope': 'bounded_core_software_model'}
        desired = 'drive_disabled' if not enabled else 'forced_stop_commanded' if stop else 'run_commanded'
        old = prior['command_state'] if prior else 'initially_idle'
        transition = desired != old
        if transition:
            event = {'scan': m.scan_index, 'timestamp_ms': m.timestamp_ms,
                     'channel': channel, 'from': old, 'to': desired,
                     'effective_edge_time_us': None, 'electrical_level': None}
            self.events.append(event)
            if desired == 'run_commanded':
                next_state['generation'] += 1
                next_state['run_command_ms'] = m.timestamp_ms
                if next_state['first_command_ms'] is None:
                    next_state['first_command_ms'] = m.timestamp_ms
        next_state.update(command_state=desired, enabled=enabled, stop_bit=stop,
                          observed_at_ms=m.timestamp_ms, scan=m.scan_index,
                          timing_status='phase_and_stop_latency_unverified')
        # Nominal period is analytical, independent of scan count. It does not
        # imply an elapsed hardware cycle count or exact startup time.
        next_state['nominal_frequency_hz'] = 1000000 / request['period_us']
        self.channels[channel] = next_state
        m.write(output, None, location, 'bit')
        self.invalidate_observables(location, channel=channel, include_settings=False)
        if enabled:
            base = 5302 + 16 * (channel - 1)
            for offset, value in ((0, request['on_time']), (2, request['period'])):
                m.write('SD' + str(base + offset), value & 65535, location)
                m.write('SD' + str(base + offset + 1), value >> 16, location)
        m.gap('pwm_physical_waveform_and_full_graph_unverified', location)

    def invalidate_failed_request(self, args, location):
        """Invalidate only the known or possible footprint of this owner.

        This is uncertainty propagation, not a validated hardware mapping. A
        rejected migration can affect both historical and candidate resources.
        If binding is missing, conservatively consider the CPU subset, never
        unrelated external-module channels 5..12.
        """
        from simulator.bounded import _leaf
        owner = self.owner_id(location)
        channels, outputs = set(), set()
        for channel, state in self.channels.items():
            if state['owner_id'] == owner:
                channels.add(channel)
                outputs.add(state['request']['output_device'])
        raw = self.machine.execution_context.get('pwm', {})
        pwm = dict(raw) if isinstance(raw, Mapping) else {}
        bindings = pwm.pop('bindings', None)
        binding_known = bindings is None
        if isinstance(bindings, Mapping) and isinstance(bindings.get(owner), Mapping):
            pwm.update(bindings[owner])
            binding_known = True
        candidate_channels, candidate_outputs = set(), set()
        if binding_known:
            channel = pwm.get('channel')
            if type(channel) is int and 1 <= channel <= 4:
                candidate_channels.add(channel)
            output = canonical_reference_device(pwm.get('output_device'))
            if isinstance(output, str) and re.fullmatch(r'Y[0-7]', output):
                candidate_outputs.add(output)
        destination = canonical_reference_device(args[2]) if len(args) > 2 else None
        if isinstance(destination, str) and re.fullmatch(r'Y[0-7]', destination):
            candidate_outputs.add(destination)
        elif destination is not None:
            try:
                channel = _leaf(destination, self.machine.memory, bits=16, signed=False,
                                plc_model=self.machine.model)
            except (UnknownInstructionSemantics, ValueError, TypeError, KeyError, IndexError):
                channel = None
            if type(channel) is int and 1 <= channel <= 4:
                candidate_channels.add(channel)
        channels.update(candidate_channels or range(1, 5))
        outputs.update(candidate_outputs or ('Y' + str(i) for i in range(8)))
        for channel in channels:
            self.invalidate_observables(location, channel=channel)
        for output in outputs:
            self.machine.write(output, None, location, 'bit')
        for channel, state in self.channels.items():
            if channel in channels or state['request']['output_device'] in outputs:
                self.invalidated_owners[state['owner_id']] = self.machine.scan_index

    def invalidate_observables(self, location, *, channel, include_settings=True):
        # Scope is explicit: callers cannot accidentally invalidate all channels.
        m = self.machine
        base = 5300 + 16 * (channel - 1)
        offsets = range(2, 8) if include_settings else (6, 7)
        for offset in offsets:
            m.write('SD' + str(base + offset), None, location)
        for start in (5300, 5316, 5332):
            m.write('SM' + str(start + channel - 1), None, location, 'bit')

    def snapshot(self):
        channels = copy.deepcopy(self.channels)
        for state in channels.values():
            attempt = self.attempts.get(state['owner_id'])
            current = bool(attempt and attempt['status'] == 'accepted_bounded_contract'
                           and attempt['scan'] == self.machine.scan_index - 1
                           and self.invalidated_owners.get(state['owner_id']) != self.machine.scan_index - 1
                           and state['scan'] == attempt['scan']
                           and self.machine.memory.get('SM8034') is state['stop_bit'])
            state['current_observation_status'] = 'accepted_bounded_contract' if current else 'unverified'
            if not current:
                state['last_accepted_command_state'] = state['command_state']
                state['command_state'] = 'unknown'
        return {'scope': 'bounded_core_software_model', 'hardware_effect': 'not_tested',
                'native_execution': 'not_tested', 'channels': channels, 'last_attempt': copy.deepcopy(self.last_attempt),
                'events': copy.deepcopy(self.events), 'instruction_bindings': self.instruction_owners(),
                'owner_attempts': copy.deepcopy(self.attempts)}
