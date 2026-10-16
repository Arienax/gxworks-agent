"""Read stability and conservative output effects for condition factoring.

This consumer of Core address/runtime/Registry facts never supplies missing
instruction semantics. Every condition/output has a classification, including
explicit barriers. A known destination does not make a stateful call removable.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re

from plc.device_identity import canonical_device
from plc.validation import device_address_radix, normalize_plc_model, parse_device_address


@dataclass(frozen=True)
class Condition:
    kind: str
    identity: tuple | None = None
    reads: frozenset[str] = frozenset()
    reason: str | None = None


@dataclass(frozen=True)
class Effect:
    kind: str
    writes: frozenset[str] | None
    precision: str = "unknown"
    reason: str | None = None


class ConditionAnalysis:
    def __init__(self, plc_model="FX3U"):
        from plc.runtime_semantics import control_runtime_facts
        self.model = normalize_plc_model(plc_model)
        self.runtime = control_runtime_facts(self.model)
        self._effects = {}

    @lru_cache(maxsize=4096)
    def device(self, value, word=False):
        if not isinstance(value, str):
            return None
        token = canonical_device(value)
        parsed = parse_device_address(token, self.model)
        if parsed is None:
            return None
        family, number = parsed
        if token in self.runtime.get("always_on", []):
            return token
        if family in {"SM", "SD"} or (self.model == "FX3U" and family in {"M", "D"} and number >= 8000):
            return None
        if family not in ({"X", "Y", "M", "S", "T", "C", "D", "V", "Z"} if word else {"X", "Y", "M", "S", "T", "C"}):
            return None
        if family == "T":
            from plc.runtime_semantics import timer_runtime_fact
            if timer_runtime_fact(self.model, token) is None:
                return None
        if family == "C":
            fact = self.runtime.get("counter", {})
            if not fact or not fact["first"] <= number <= fact["last"]:
                return None
        return token

    def operand(self, value):
        if not isinstance(value, str):
            return None
        token = value.strip().upper()
        if re.fullmatch(r"K[+-]?\d+|H[0-9A-F]+", token):
            return token, frozenset()
        device = self.device(token, True)
        return (device, frozenset({device})) if device else None

    def condition(self, element):
        if not isinstance(element, dict):
            return Condition("unknown", reason="unsupported_condition")
        kind = element.get("type")
        if not isinstance(kind, str):
            return Condition("unknown", reason="unsupported_condition")
        if kind in {"P", "RISING", "F", "FALLING"}:
            return Condition("rising_edge" if kind in {"P", "RISING"} else "falling_edge",
                             reason="edge_evaluation_site")
        if kind in {"NO", "NC"} and not set(element) - {"type", "address", "label"}:
            value = element.get("address")
            device = self.device(value) if isinstance(value, str) else None
            if device:
                return Condition("contact", (kind, device), frozenset({device}))
            return Condition("contact", reason="volatile_or_unverified_read")
        if kind in {"COMPARE", "BLOCK_INPUT"} and not set(element) - {"type", "expression", "label"}:
            parts = str(element.get("expression") or "").split()
            operators = {"=", "==", "<>", "<", ">", "<=", ">="}
            if len(parts) == 3:
                if parts[0] in operators:
                    op, left, right = parts
                elif parts[1] in operators:
                    left, op, right = parts
                else:
                    return Condition("comparison", reason="unsupported_comparison")
                left, right = self.operand(left), self.operand(right)
                if left and right:
                    return Condition("comparison", ("COMPARE", "=" if op == "==" else op, left[0], right[0]), left[1] | right[1])
            return Condition("comparison", reason="volatile_or_unverified_read")
        if kind == "parallel_block" and not set(element) - {"type", "branches", "label"}:
            branches = element.get("branches")
            if isinstance(branches, list) and branches and all(isinstance(b, list) and b for b in branches):
                children = [[self.condition(e) for e in b] for b in branches]
                if all(c.identity is not None and c.kind != "parallel" for b in children for c in b):
                    return Condition("parallel", ("OR", tuple(tuple(c.identity for c in b) for b in children)),
                                     frozenset().union(*(c.reads for b in children for c in b)))
            return Condition("parallel", reason="stateful_or_opaque_parallel")
        return Condition("unknown", reason="unsupported_condition")

    def _region(self, base, offset, count):
        if type(offset) is not int or type(count) is not int or not 0 <= offset or not 1 <= count <= 1024:
            return None
        parsed = parse_device_address(str(base), self.model)
        if parsed is None:
            return None
        family, number = parsed
        radix = "o" if device_address_radix(family, self.model) == 8 else "d"
        values = {family + format(number + offset + i, radix) for i in range(count)}
        if any(parse_device_address(v, self.model) is None for v in values):
            return None
        return values

    def _defined_writes(self, spec, operands):
        from plc.instruction_definition import select_fact_dependencies, evaluate_expression
        if len(spec.native_operand_order) != len(operands):
            return None
        parameters = {}
        for name, value in zip(spec.native_operand_order, operands):
            token = str(value).strip().upper()
            parameters[name] = (int(token[1:]) if re.fullmatch(r"K[+-]?\d+", token) else
                                int(token[1:], 16) if re.fullmatch(r"H[0-9A-F]+", token) else token)
        effects = []
        for group in spec.definition_facts:
            if not group.dimension.startswith("effects.") or not group.value.get("behavior"):
                continue
            closure = select_fact_dependencies(spec.definition_facts, [group.id], opcode=spec.mnemonic, model=self.model)
            if not closure["gaps"] and closure["source_verification_complete"]:
                effects.append(group.value)
        if not effects:
            return None
        writes = set()
        for effect in effects:
            # A source-backed subset still needs its execution-state premises.
            # Without those, fall back to the Registry's conservative family
            # footprint rather than treating the subset as a complete effect.
            if effect.get("required_state"):
                return None
            kind = effect["behavior"]
            if kind in {"expression_write", "conditional_results", "state_update"}:
                for output in effect.get("outputs", []):
                    target = output["target"]
                    if target["kind"] == "state":
                        return None
                    bits = output["expression"].get("type", {}).get("bits", 1)
                    base = parameters.get(target["parameter"])
                    if target["kind"] == "word" and not re.fullmatch(r"D\d+", str(base)):
                        return None
                    values = self._region(base, target.get("offset", 0),
                                          max(1, (bits + 15) // 16) if target["kind"] == "word" else 1)
                    if values is None:
                        return None
                    writes.update(values)
            elif kind in {"range_copy", "range_shift"}:
                try:
                    count = evaluate_expression(effect["count"], parameters)
                except (ValueError, TypeError, KeyError):
                    return None
                element = effect.get("element_type", {})
                base = parameters.get(effect["destination" if kind == "range_copy" else "region"])
                if (type(count) is not int
                        or element.get("kind") == "bool" and not re.fullmatch(r"M\d+", str(base))
                        or element.get("kind") != "bool" and (element.get("bits") != 16 or not re.fullmatch(r"D\d+", str(base)))
                        or not 1 <= count <= effect.get("max_count", 1024)):
                    return None
                values = self._region(base, 0, count)
                if values is None:
                    return None
                writes.update(values)
            else:
                return None
        return writes or None

    def output(self, output):
        import json
        key = json.dumps(output, sort_keys=True, ensure_ascii=True)
        if key not in self._effects:
            effect = self._output(output)
            if effect.writes is not None and effect.precision != "family" and any(
                    self.device(address, True) is None or address in self.runtime.get("always_on", [])
                    for address in effect.writes):
                # Special/control destinations can affect other runtime state;
                # an explicit destination alone does not bound those effects.
                effect = Effect(effect.kind, None, reason="implicit_or_external_effect")
            self._effects[key] = effect
        return self._effects[key]

    def _output(self, output):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, InstructionCategory, OperandRole, SemanticKind
        from plc.ir import analyze_instruction_access, _zero_reset_range
        if not isinstance(output, dict) or not isinstance(output.get("type"), str):
            return Effect("unknown", None, reason="unsupported_output")
        kind = output.get("type")
        if kind in {"COIL", "PLS", "PLF", "TIMER", "COUNTER"}:
            allowed = {"type", "address", "label"} | ({"value"} if kind in {"TIMER", "COUNTER"} else set())
            address = canonical_device(output.get("address"))
            parsed = parse_device_address(address, self.model) if isinstance(address, str) else None
            expected = {"T"} if kind == "TIMER" else {"C"} if kind == "COUNTER" else {"M", "Y"}
            if set(output) - allowed or parsed is None or parsed[0] not in expected:
                return Effect(str(kind), None, reason="unsupported_output")
            return Effect(str(kind), frozenset({address}), "exact")
        if kind == "APP_INSTR" and not set(output) - {"type", "opcode", "operands", "label"}:
            op, operands = output.get("opcode"), output.get("operands", [])
        elif kind == "BLOCK_OUTPUT" and not set(output) - {"type", "expression", "label"}:
            parts = str(output.get("expression") or "").split()
            op, operands = (parts[0], parts[1:]) if parts else ("", [])
        else:
            return Effect("unknown", None, reason="unsupported_output")
        if not isinstance(op, str) or not isinstance(operands, list):
            return Effect("unknown", None, reason="unsupported_output")
        op = op.upper()
        spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(op, cpu=self.model)
        if spec is None or not spec.supports_cpu(self.model):
            return Effect("unknown", None, reason="instruction_fact_gap")
        if spec.category != InstructionCategory.ACTION:
            return Effect(spec.category.value, None, reason="control_flow_or_logic_stack")
        if spec.completion is not None or spec.pulse_output is not None or spec.semantic_kind == SemanticKind.FUNCTION_BLOCK:
            return Effect("stateful_or_external", None, reason="implicit_or_external_effect")
        if any(f.value.get("behavior") in {"state_update", "external_action", "parameter_layout"}
               for f in spec.definition_facts if f.dimension.startswith("effects.")):
            return Effect("stateful_or_external", None, reason="implicit_or_external_effect")
        if not spec.accepts_arity(len(operands)):
            return Effect(spec.semantic_kind.value, None, reason="instruction_fact_gap")
        # Reuse Core's explicit control effects, including range boundaries.
        if op in {"SET", "RST"} and op in self.runtime.get("control", {}).get("forms", []):
            if len(operands) == 1 and parse_device_address(str(operands[0]), self.model):
                return Effect("retained_write", frozenset(analyze_instruction_access(op, operands, plc_model=self.model)[1]), "exact")
        if op == "ZRST":
            values = _zero_reset_range(operands, self.model)
            if values is not None:
                return Effect("range_write", frozenset(values), "exact")
        defined = self._defined_writes(spec, operands)
        if defined is not None:
            return Effect(spec.semantic_kind.value, frozenset(defined), "definition")
        if spec.semantic_kind not in {SemanticKind.FUNCTION, SemanticKind.COIL, SemanticKind.COMPARISON} or not spec.write_indexes:
            return Effect(spec.semantic_kind.value, None, reason="instruction_fact_gap")
        if len(operands) != len(spec.operands):
            return Effect(spec.semantic_kind.value, None, reason="instruction_fact_gap")
        writes = set()
        for operand, contract in zip(operands, spec.operands):
            if contract.role == OperandRole.CONTROL:
                return Effect("control", None, reason="control_flow_or_logic_stack")
            token = str(operand).strip().upper()
            # No hidden aliases are guessed from strings, indirect or packed operands.
            if self.operand(token) is None:
                return Effect(spec.semantic_kind.value, None, reason="unresolved_operand_effect")
            if contract.role in {OperandRole.WRITE, OperandRole.READ_WRITE}:
                parsed = parse_device_address(token, self.model)
                if parsed is None:
                    return Effect(spec.semantic_kind.value, None, reason="unresolved_operand_effect")
                writes.add(parsed[0] + "*")
        return Effect(spec.semantic_kind.value, frozenset(writes), "family")

    def writes(self, branch):
        effects = [self.output(output) for output in branch.get("outputs", [])]
        return None if any(e.writes is None for e in effects) else set().union(*(e.writes for e in effects))
