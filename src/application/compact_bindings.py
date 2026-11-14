# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Resolve compact references against Core facts for the active Ladder CPU."""
from __future__ import annotations

import re
from collections import Counter

from application.compact_protocol import CompactProtocolError
from plc.specification.conditions import generation_input_conditions

_FORM_ID = re.compile(r"([^:]+):(.+):(\d{1,2})\Z")
_CONDITION_FIELDS = {"active": "active_when", "inactive": "inactive_when", "run_permit": "run_permit_when"}


class CompactBindingError(CompactProtocolError):
    def __init__(self, path, reason, *, actual=None, expected=None, opcode=None):
        super().__init__(f"{path}: {reason}", reason=reason)
        self.observed_opcode = opcode
        self.binding_diagnostic = {
            "category": "binding", "path": self.path, "reason": reason,
            "actual": actual, "expected": expected,
            **({"opcode": opcode} if opcode else {}),
        }


class CompactBindings:
    def __init__(self, plc_model, confirmed_spec=None):
        self.plc_model = str(plc_model).strip().upper()
        bindings = (confirmed_spec or {}).get("io_bindings")
        self.conditions = generation_input_conditions(bindings, plc_model=self.plc_model)
        rows = self.conditions["level_predicates"]
        counts = Counter(row["binding_id"] for row in rows)
        self._conditions = {row["binding_id"]: row for row in rows if counts[row["binding_id"]] == 1}
        self._forms = {}

    def condition(self, ref, path):
        row = self._conditions.get(ref["condition_ref"], {})
        contact = row.get(_CONDITION_FIELDS[ref["when"]])
        if contact is None:
            raise CompactBindingError(path, "input_condition_unresolved", actual=ref,
                                      expected={"available": [name for name, key in _CONDITION_FIELDS.items() if key in row]})
        return contact

    def timer(self, value, path):
        from plc.runtime_semantics import timer_runtime_fact
        from plc.timing import exact_duration_ticks
        from plc.validation import device_address_status

        address = value['timer'].upper()
        if device_address_status(address, self.plc_model) == 'invalid':
            raise CompactBindingError(path, 'invalid_timer_address', actual={'timer': address},
                                      expected={'plc_model': self.plc_model, 'device_class': 'T'}, opcode='OUT')
        fact = timer_runtime_fact(self.plc_model, address)
        if fact and value['tick_ms'] != fact['unit_ms']:
            raise CompactBindingError(path, 'timer_timebase_mismatch', actual=value,
                expected={'timer': address, 'tick_ms': fact['unit_ms'], 'source': fact['source']})
        try:
            count = exact_duration_ticks(value['duration_ms'], value['tick_ms'])
        except ValueError as error:
            raise CompactBindingError(path, 'invalid_timer_duration', actual=value,
                expected={'duration_ms': 'exact multiple of tick_ms', 'detail': str(error)}) from error
        return {'type': 'TIMER', 'address': address, 'value': f'K{count}'}

    def ordered_record(self, value, path):
        if 'condition_ref' in value:
            polarity, address = self.condition(value, path).split()
            op = value['op']
            if polarity == 'NC':
                op = {'LD': 'LDI', 'AND': 'ANI', 'OR': 'ORI'}[op]
            return {'op': op, 'args': [address]}
        if 'timer' in value:
            timer = self.timer(value, path)
            return {'op': 'OUT', 'args': [timer['address'], timer['value']]}
        if 'form_ref' in value:
            call = self.call(value, path)
            return {'op': call['opcode'], 'args': call['operands']}
        return value

    def _form(self, opcode, count=None):
        from plc.instructions import (
            DEFAULT_INSTRUCTION_REGISTRY, GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES,
            GENERATION_TYPED_OUTPUT_OPCODES,
        )
        registry = DEFAULT_INSTRUCTION_REGISTRY
        form = registry.resolve_form(opcode, cpu=self.plc_model, operand_count=count)
        if (form is None or form.opcode != opcode or opcode in GENERATION_TYPED_OUTPUT_OPCODES
                or form.spec.category in GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES
                or not form.spec.supports_cpu(self.plc_model)):
            return None
        owner = registry.definition_header(opcode, cpu=self.plc_model) or {}
        return None if owner.get("call_available") is False else form

    def _signature(self, opcode, count):
        key = opcode, count
        if key in self._forms:
            return self._forms[key]
        form = self._form(opcode, count)
        if form is None:
            return None
        from plc.instruction_constraints import bind_operand_signature
        spec = form.spec
        if {"arity", "operand_order"} <= set(spec.verified_fields) and spec.contract_sources:
            names = spec.native_operand_order
        else:
            names = bind_operand_signature(spec.definition_facts, target=opcode, plc_model=self.plc_model,
                language="ladder", context={"project_mode": "native_ladder_instruction"})
        if names is None or len(names) != count or not spec.accepts_arity(count) or len(set(names)) != count:
            return None
        params = []
        for index, name in enumerate(names):
            item = {"name": name}
            if index < len(spec.operands):
                operand = spec.operands[index]
                if operand.data_type != "any":
                    item.update(type=operand.data_type, role=operand.role.value)
                if operand.word_count > 1:
                    item["words"] = operand.word_count
            params.append(item)
        result = {"form_ref": f"{self.plc_model}:{opcode}:{count}", "opcode": opcode, "parameters": params}
        self._forms[key] = result
        return result

    def instruction_forms(self, opcodes):
        result = []
        for opcode in sorted(set(opcodes)):
            form = self._form(opcode)
            if form is None:
                continue
            spec = form.spec
            counts = {len(spec.native_operand_order), *(len(s.operands) for s in spec.native_overloads)}
            if spec.min_operands is not None:
                counts.add(spec.min_operands)
            if spec.max_operands is not None:
                counts.add(spec.max_operands)
            for count in sorted(counts):
                if count <= 32 and (signature := self._signature(opcode, count)):
                    result.append(signature)
        return result

    def accepts_positional(self, opcode, count):
        form = self._form(opcode, count)
        if form is None:
            return False
        spec = form.spec
        # Existing complete positional contracts remain callable. An opcode
        # entry with no arity does not authorize arbitrary operands.
        return (spec.min_operands is not None and spec.max_operands is not None
                and spec.accepts_arity(count)) or self._signature(opcode, count) is not None

    def check_literal_widths(self, opcode, operands):
        from plc.validation import _validate_reviewed_integer_literal_width

        form = self._form(opcode, len(operands))
        if form is None:
            return
        for index, token in enumerate(operands):
            _validate_reviewed_integer_literal_width(form.spec, index, token, f'operands[{index}]')

    def call(self, value, path):
        ref = value["form_ref"]
        match = _FORM_ID.fullmatch(ref)
        signature = None
        opcode = None
        if match and match[1] == self.plc_model:
            opcode = match[2]
            signature = self._signature(opcode, int(match[3]))
        if signature is None or signature["form_ref"] != ref:
            raise CompactBindingError(path, "instruction_form_unresolved", actual=ref,
                                      expected={"plc_model": self.plc_model, "language": "ladder"}, opcode=opcode)
        names = [item["name"] for item in signature["parameters"]]
        supplied = {item["name"]: item["value"] for item in value["parameters"]}
        if set(supplied) != set(names) or len(supplied) != len(value["parameters"]):
            raise CompactBindingError(path, "instruction_parameters_mismatch",
                                      actual=[item["name"] for item in value["parameters"]],
                                      expected=signature, opcode=opcode)
        operands = []
        for name in names:
            value = supplied[name]
            if isinstance(value, dict):
                from plc.timing import exact_duration_ticks
                try:
                    if set(value) != {"duration_ms", "tick_ms"}:
                        raise ValueError("expected duration_ms and tick_ms")
                    value = f"K{exact_duration_ticks(value['duration_ms'], value['tick_ms'])}"
                except ValueError as error:
                    raise CompactBindingError(path, "invalid_instruction_parameters",
                        actual={"parameter": name, "value": value, "detail": str(error)},
                        expected=signature, opcode=opcode) from error
            operands.append(value)
        output = {"type": "APP_INSTR", "opcode": opcode, "operands": operands}
        from plc.validation import _validate_element, PLCJsonValidationError, VALID_OUTPUT_TYPES
        try:
            _validate_element(output, path, VALID_OUTPUT_TYPES, is_output=True,
                              plc_model=self.plc_model, require_catalogued_instructions=True)
        except PLCJsonValidationError as error:
            location = re.fullmatch(re.escape(path) + r"\.operands\[(\d+)\]", getattr(error, "path", ""))
            actual = {"detail": str(error)}
            if location and (index := int(location[1])) < len(names):
                name = names[index]
                actual.update(parameter=name, operand_index=index, value=supplied[name])
            else:
                actual["parameters"] = supplied
            raise CompactBindingError(path, "invalid_instruction_parameters", actual=actual,
                                      expected=signature, opcode=opcode) from error
        return output


def instruction_targets(units):
    return {row["opcode"] for unit in units if unit.get("kind") == "instruction"
            for row in unit.get("value", {}).get("forms", []) if isinstance(row.get("opcode"), str)}


def instruction_form_units(plc_model, units, *, targets=()):
    forms = CompactBindings(plc_model).instruction_forms(instruction_targets(units) | set(targets))
    return [{"kind": "ladder_instruction_form", **form} for form in forms]


def positional_form_issues(ladder, spec, bindings, *, previous=None):
    """Check documented application instruction forms on changed graphical rungs."""
    from plc.validation import PLCJsonValidationError
    from plc.generation_contract import intent_context

    issues = []
    selected = spec.get("selected_approach") or {}
    explicit = [*selected.get("explicit_user_constraints", {}).get("instruction_instances", []),
                *intent_context(spec).get("explicit_instruction_instances", [])]
    prior = {rung["rung_id"]: rung for rung in (previous or {}).get("rungs", [])}
    rows = ladder.get("rungs")
    if not isinstance(rows, list):
        return issues
    for rung_index, rung in enumerate(rows):
        if not isinstance(rung, dict) or prior.get(rung.get("rung_id")) == rung:
            continue
        for branch_index, branch in enumerate(rung.get("branches", []) or []):
            if not isinstance(branch, dict):
                continue
            for output_index, output in enumerate(branch.get("outputs", []) or []):
                if not isinstance(output, dict) or output.get("type") != "APP_INSTR":
                    continue
                opcode, operands = output.get("opcode"), output.get("operands", [])
                if not isinstance(opcode, str) or not isinstance(operands, list):
                    continue
                accepted = bindings.accepts_positional(opcode, len(operands)) or any(
                    row.get("opcode") == opcode and row.get("operands") == operands
                    for row in explicit if isinstance(row, dict)
                )
                reason, actual = "instruction_form_unresolved", operands
                path = f'$.rungs[{rung_index}].branches[{branch_index}].outputs[{output_index}]'
                field = 'opcode'
                if accepted:
                    try:
                        bindings.check_literal_widths(opcode, operands)
                    except PLCJsonValidationError as error:
                        reason = "invalid_instruction_parameters"
                        field = error.path
                        actual = {"operands": operands, "detail": str(error)}
                    else:
                        continue
                issues.append({
                    "category": "binding", "reason": reason, "opcode": opcode, "path": f'{path}.{field}',
                    "actual": actual, "expected": bindings.instruction_forms([opcode]),
                    "rung_ids": [rung.get("rung_id")],
                })
    return issues
