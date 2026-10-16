"""Executable, bounded behavior primitives owned by instruction definitions.

This is a reference for the explicitly described subset, not a PLC simulator.
External actions produce contract observations; they never perform hardware I/O.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping

from plc.instruction_definition import (
    DefinitionError, Expression, UnknownInstructionSemantics, ValueType,
    evaluate_expression,
    canonical_expression,
)
from plc.device_identity import decimal_region_address

BEHAVIOR_KINDS = frozenset({
    "expression_write", "conditional_results", "range_copy", "range_shift",
    "state_update", "parameter_layout", "external_action",
})


def validate_behavior(raw):
    if not isinstance(raw, Mapping) or raw.get("behavior") not in BEHAVIOR_KINDS:
        raise UnknownInstructionSemantics("Behavior has not been formalized")
    value = copy.deepcopy(dict(raw))
    kind = value["behavior"]
    required_state = value.get('required_state', {})
    if not isinstance(required_state, Mapping) or any(
            not isinstance(name, str) or not name or type(item) is not bool
            for name, item in required_state.items()):
        raise DefinitionError('Required state must name explicit bit values')
    conditions = value.get('preconditions', [])
    if (not isinstance(conditions, list) or len(conditions) > 16 or
            any(Expression.from_mapping(condition).value_type.kind != 'bool' for condition in conditions)):
        raise DefinitionError('Instruction preconditions must be bounded Boolean expressions')
    if kind in {"expression_write", "conditional_results", "state_update"}:
        outputs = value.get("outputs")
        if not isinstance(outputs, list) or not 1 <= len(outputs) <= 512:
            raise DefinitionError("Behavior needs bounded explicit outputs")
        seen = set()
        for output in outputs:
            target = output.get("target") if isinstance(output, Mapping) else None
            if (not isinstance(target, Mapping) or not isinstance(target.get("parameter"), str)
                    or not target["parameter"] or type(target.get("offset", 0)) is not int
                    or not 0 <= target.get("offset", 0) <= 511
                    or target.get("kind") not in {"bit", "word", "state"}):
                raise DefinitionError("Invalid effect target")
            marker = (target["parameter"], target.get("offset", 0))
            if marker in seen:
                raise DefinitionError("Duplicate effect target")
            seen.add(marker)
            expression = Expression.from_mapping(output.get("expression"))
            if target["kind"] == "bit" and expression.value_type.kind != "bool":
                raise DefinitionError("Bit result needs a boolean expression")
    elif kind in {"range_copy", "range_shift"}:
        for key in ("source", "destination") if kind == "range_copy" else ("region", "source"):
            if not isinstance(value.get(key), str) or not value[key]:
                raise DefinitionError("Range behavior needs named address parameters")
        ValueType.from_mapping(value.get("element_type"))
        Expression.from_mapping(value.get("count"))
        if value.get("read_mode", "snapshot") not in {"snapshot", "forward", "backward"}:
            raise DefinitionError("Unknown range read ordering")
        if kind == "range_shift":
            Expression.from_mapping(value.get("shift"))
            if value.get("direction") not in {"left", "right"}:
                raise DefinitionError("Unknown range direction")
            if value.get("fill") != "source":
                raise UnknownInstructionSemantics("Range fill has not been formalized")
    elif kind == "parameter_layout":
        fields = value.get("fields")
        if not isinstance(fields, list) or len(fields) > 512:
            raise DefinitionError("Parameter fields must be bounded")
        for item in fields:
            if (not isinstance(item, Mapping) or type(item.get("offset")) is not int
                    or not 0 <= item["offset"] <= 511 or item.get("access") not in {"read", "write", "read_write"}):
                raise DefinitionError("Invalid parameter block field")
            ValueType.from_mapping(item.get("type"))
            if "bits" in item:
                bits = item["bits"]
                if (not isinstance(bits, list) or len(set(bits)) != len(bits)
                        or any(type(b) is not int or not 0 <= b < item["type"].get("bits", 0) for b in bits)):
                    raise DefinitionError("Invalid parameter bit field")
    elif kind == "external_action":
        if not isinstance(value.get("resource_parameter"), str) or not value["resource_parameter"]:
            raise DefinitionError("External action needs a resource parameter")
        protocol = value.get("protocol", {})
        if not isinstance(protocol, Mapping):
            raise DefinitionError("External action protocol must be an object")
        for key in ("busy", "complete", "error"):
            if key in protocol and not isinstance(protocol[key], (str, Mapping)):
                raise DefinitionError("Invalid external status contract")
    return value


def effect_signature(raw):
    """Exact typed behavior equality, independent of prose and source status."""
    value = validate_behavior(raw)
    if value['behavior'] not in {'conditional_results', 'expression_write', 'state_update'}:
        return None
    import json
    other = {k: v for k, v in value.items() if k not in {'behavior', 'outputs', 'text', 'representation'}}
    return (value['behavior'], json.dumps(other, ensure_ascii=False, sort_keys=True),
            tuple(sorted((o['target']['parameter'], o['target'].get('offset', 0),
            o['target']['kind'], canonical_expression(o['expression'])) for o in value['outputs'])))


def check_point_memory_scope(behavior, parameters):
    """Enforce declared point-write subsets before claiming a reference result."""
    if behavior.get('scope_limits', {}).get('memory') != 'disjoint_word_regions':
        return
    regions = {}
    for output in behavior.get('outputs', []):
        target = output['target']
        if target['kind'] != 'word':
            raise UnknownInstructionSemantics('Disjoint scope requires word results')
        base = parameters.get(target['parameter'])
        if not isinstance(base, str):
            raise UnknownInstructionSemantics('Result region is unbound')
        width = Expression.from_mapping(output['expression']).value_type.bits
        addresses = {decimal_region_address(base, target.get('offset', 0) + i)
                     for i in range(max(1, width // 16))}
        regions.setdefault(target['parameter'], set()).update(addresses)
    occupied = set()
    for region in regions.values():
        if occupied & region:
            raise UnknownInstructionSemantics('Overlapping results are outside the formalized subset')
        occupied.update(region)


def execute_behavior(raw, parameters, *, memory=None, state=None, enabled=True, previous_enabled=False,
                     trigger="level", disabled="unknown"):
    """Return observations/writes for one bounded scan; never mutate inputs."""
    behavior = validate_behavior(raw)
    if trigger not in {"level", "rising", "falling"}:
        raise UnknownInstructionSemantics("Trigger lifecycle has not been formalized")
    fired = enabled if trigger == "level" else (enabled and not previous_enabled if trigger == "rising"
                                                else previous_enabled and not enabled)
    if not fired:
        if disabled not in {"retain", "no_action"}:
            raise UnknownInstructionSemantics("Disabled/retention behavior is unknown")
        return {"executed": False, "writes": {}, "state_writes": {}, "hardware_effect": "not_tested"}
    if any((state or {}).get(name) != item for name, item in behavior.get('required_state', {}).items()):
        raise UnknownInstructionSemantics('Required instruction state is not established')
    if any(not evaluate_expression(condition, parameters, state=state) for condition in behavior.get('preconditions', [])):
        raise DefinitionError('Instruction precondition is violated')
    check_point_memory_scope(behavior, parameters)
    def memory_key(base, offset):
        try:
            return decimal_region_address(base, offset)
        except ValueError:
            return (base, offset)
    data = {memory_key(k[0], k[1]) if isinstance(k, tuple) and len(k) == 2 else k: copy.deepcopy(v)
            for k, v in (memory or {}).items()}
    writes, state_writes = {}, {}

    def read(base, offset):
        key = memory_key(base, offset)
        if key not in data:
            raise UnknownInstructionSemantics("Missing range input")
        return data[key]

    kind = behavior["behavior"]
    if kind in {"expression_write", "conditional_results", "state_update"}:
        for output in behavior["outputs"]:
            target = output["target"]
            if target["parameter"] not in parameters:
                raise UnknownInstructionSemantics("Missing destination binding")
            key = (parameters[target["parameter"]], target.get("offset", 0))
            value = evaluate_expression(output["expression"], parameters, state=state, memory_reader=read)
            (state_writes if target["kind"] == "state" else writes)[key] = value
    elif kind in {"range_copy", "range_shift"}:
        element = ValueType.from_mapping(behavior["element_type"])
        count = evaluate_expression(behavior["count"], parameters)
        limit = behavior.get("max_count", 1024)
        if type(limit) is not int or not 1 <= limit <= 1024 or type(count) is not int or not 1 <= count <= limit:
            raise UnknownInstructionSemantics("Range exceeds formal interpretation limit")
        source = parameters[behavior["source"]]
        destination = parameters[behavior["destination"] if kind == "range_copy" else behavior["region"]]
        if kind == "range_copy":
            if behavior.get("required_state") and any((state or {}).get(k) != v for k, v in behavior["required_state"].items()):
                raise UnknownInstructionSemantics("Required instruction state is not established")
            indexes = range(count) if behavior.get("read_mode", "snapshot") != "backward" else range(count - 1, -1, -1)
            snapshot = [element.decode_bits(read(source, i)) for i in range(count)]
            for i in indexes:
                value = snapshot[i] if behavior.get("read_mode", "snapshot") == "snapshot" else element.decode_bits(read(source, i))
                writes[(destination, i)] = value
                if behavior.get("read_mode", "snapshot") != "snapshot":
                    data[memory_key(destination, i)] = value
        else:
            shift = evaluate_expression(behavior["shift"], parameters)
            if type(shift) is not int or not 0 <= shift <= count:
                raise UnknownInstructionSemantics("Shift exceeds region")
            if behavior.get("source_must_differ") and ({memory_key(source, i) for i in range(shift)} &
                                                       {memory_key(destination, i) for i in range(count)}):
                raise DefinitionError("Transfer source overlaps shifted region")
            old = [element.decode_bits(read(destination, i)) for i in range(count)]
            fill = [element.decode_bits(read(source, i)) for i in range(shift)]
            result = fill + old[:count - shift] if behavior["direction"] == "left" else old[shift:] + fill
            writes.update({(destination, i): v for i, v in enumerate(result)})
    elif kind == "external_action":
        resource = parameters.get(behavior["resource_parameter"])
        if resource is None:
            raise UnknownInstructionSemantics("External resource is unbound")
        return {"executed": True, "writes": {}, "state_writes": {}, "hardware_effect": "not_tested",
                "external_contract": {"resource": resource, "protocol": copy.deepcopy(behavior.get("protocol", {}))}}
    elif kind == "parameter_layout":
        return {"executed": True, "writes": {}, "state_writes": {}, "hardware_effect": "not_tested",
                "layout": copy.deepcopy(behavior["fields"])}
    return {"executed": True, "writes": writes, "state_writes": state_writes, "hardware_effect": "not_tested"}
