"""Explicit Core stage: typed, confirmed effects -> existing native calls.

Binding performs expression matching, not natural-language interpretation. Model
claims remain candidates until a separate user confirmation records their IDs.
Native calls use the same matcher; a separate, recorded Core stage can rebind
the same read values when the confirmed effect determines a unique mapping.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping

from plc.device_identity import canonical_device, canonical_operand, decimal_region_address
from plc.instruction_definition import (
    DefinitionError, Expression, UnknownInstructionSemantics, ValueType,
    canonical_expression, select_fact_dependencies,
)
from plc.instruction_effects import validate_behavior

_IDENTITY = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")
_ADDRESS = re.compile(r"(SM|SD|[MD R])([0-9]+)\Z".replace(" ", ""), re.I)


def _intent_expression(raw):
    expression = Expression.from_mapping(raw)
    def normalize(expr):
        return Expression(expr.op, expr.value_type, tuple(normalize(a) for a in expr.args),
                          canonical_device(expr.name) if expr.op == 'device' else expr.name,
                          expr.value, expr.overflow)
    return normalize(expression)


def offset_device(address, amount):
    """Offset decimal memory/relay regions only; other policies stay unknown."""
    try:
        return decimal_region_address(address, amount)
    except ValueError as error:
        raise UnknownInstructionSemantics(str(error)) from error


def normalize_operation_intents(raw, *, candidate=False, evidence_text=None):
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 128:
        raise DefinitionError("Operation intents must be a bounded array")
    result, seen = [], set()
    for item in raw:
        if not isinstance(item, Mapping) or set(item) - {
            "id", "opcode", "effects", "enable", "parameters", "status", "provenance", "execution", "label",
        }:
            raise DefinitionError("Invalid operation intent fields")
        identity = item.get("id")
        if not isinstance(identity, str) or not _IDENTITY.fullmatch(identity) or identity in seen:
            raise DefinitionError("Operation intent needs a unique ID")
        seen.add(identity)
        opcode = str(item.get("opcode") or "").strip().upper()
        if opcode and not re.fullmatch(r"[A-Z][A-Z0-9_.$@+<>!=\-]{0,63}", opcode):
            raise DefinitionError("Invalid intent opcode")
        provenance = item.get("provenance") or {}
        if not isinstance(provenance, Mapping):
            raise DefinitionError("Operation intent needs provenance")
        source = "model_candidate" if candidate else str(provenance.get("source") or "model_candidate")
        if source not in {"model_candidate", "user_confirmed", "user_authored", "imported_unverified"}:
            raise DefinitionError("Unknown operation intent source")
        evidence = provenance.get("evidence", [])
        if not isinstance(evidence, list) or len(evidence) > 16 or any(not isinstance(e, str) or not e for e in evidence):
            raise DefinitionError("Invalid operation-intent evidence")
        grounded = evidence_text is None or bool(evidence) and all(e in evidence_text for e in evidence)
        status = "candidate" if candidate else item.get("status", "candidate")
        if status not in {"candidate", "confirmed"}:
            raise DefinitionError("Invalid operation intent status")
        if status == "confirmed" and source not in {"user_confirmed", "user_authored"}:
            raise DefinitionError("A model claim cannot confirm itself")
        effects = item.get("effects", [])
        if not isinstance(effects, list) or not 1 <= len(effects) <= 512:
            raise DefinitionError("Operation intent needs explicit effects")
        normalized_effects = []
        for effect in effects:
            if not isinstance(effect, Mapping) or set(effect) - {"target", "value", "kind", "source", "count", "shift", "direction"}:
                raise DefinitionError("Invalid desired effect")
            target = effect.get("target")
            if (not isinstance(target, Mapping) or set(target) - {"device", "kind", "offset"}
                    or not isinstance(target.get("device"), str)
                    or not re.fullmatch(r"(?:SM|SD|[XYMDTCSR])\d+", target["device"], re.I)
                    or target.get("kind") not in {"bit", "word", "state"}
                    or type(target.get("offset", 0)) is not int or not 0 <= target.get("offset", 0) <= 511):
                raise DefinitionError("Invalid desired-effect target")
            row = {"target": {"device": canonical_device(target["device"]), "kind": target["kind"],
                               "offset": target.get("offset", 0)}, "kind": effect.get("kind", "value")}
            if row["kind"] == "value":
                expression = _intent_expression(effect.get("value"))
                if target["kind"] == "bit" and expression.value_type.kind != "bool":
                    raise DefinitionError("Desired bit effect needs a boolean value")
                row["value"] = expression.as_mapping()
            elif row["kind"] in {"range_copy", "range_shift"}:
                row["source"] = _intent_expression(effect.get("source")).as_mapping()
                row["count"] = _intent_expression(effect.get("count")).as_mapping()
                if row["kind"] == "range_shift":
                    row["shift"] = _intent_expression(effect.get("shift")).as_mapping()
                    if effect.get("direction") not in {"left", "right"}:
                        raise DefinitionError("Unknown desired shift direction")
                    row["direction"] = effect["direction"]
            elif row["kind"] != "external_action":
                raise UnknownInstructionSemantics("Desired effect kind has not been formalized")
            normalized_effects.append(row)
        enable = _intent_expression(item.get("enable"))
        if enable.value_type.kind != "bool":
            raise DefinitionError("Execution enable must be boolean")
        parameters = item.get("parameters", {})
        if not isinstance(parameters, Mapping) or len(parameters) > 32 or any(not isinstance(k, str) or not k for k in parameters):
            raise DefinitionError("Invalid named parameters")
        parameters = {k: _intent_expression(v).as_mapping() for k, v in parameters.items()}
        execution = item.get("execution", {"trigger": "level"})
        if not isinstance(execution, Mapping) or set(execution) - {"trigger", "required_state"} or execution.get("trigger") not in {"level", "rising", "falling"}:
            raise DefinitionError("Execution trigger must be explicit")
        required_state = execution.get('required_state', {})
        if (not isinstance(required_state, Mapping) or len(required_state) > 32 or any(
                not isinstance(k, str) or not re.fullmatch(r'(?:SM|SD|M|D)\d+', k, re.I)
                or type(v) not in {bool, int} for k, v in required_state.items())):
            raise DefinitionError('Invalid confirmed instruction state')
        result.append({"id": identity, "opcode": opcode, "effects": normalized_effects,
                       "enable": enable.as_mapping(), "parameters": parameters, "status": status,
                       "execution": {"trigger": execution['trigger'], **({'required_state': {
                           canonical_device(k): v for k, v in required_state.items()}} if required_state else {})},
                       "label": str(item.get("label") or "")[:240],
                       "provenance": {"source": source, "evidence": copy.deepcopy(evidence),
                                      "grounding_status": "exact_source" if grounded else "unresolved",
                                      **({"request_id": provenance["request_id"]} if isinstance(provenance.get("request_id"), str) else {})}})
    return result


def confirm_operation_intents(raw, ids):
    """Called only with explicitly selected IDs at the user review boundary."""
    rows = normalize_operation_intents(raw)
    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or set(ids) - {r["id"] for r in rows}:
        raise DefinitionError("Unknown operation intent confirmation ID")
    for row in rows:
        if row["id"] in ids:
            row["status"] = "confirmed"
            row["provenance"]["source"] = "user_confirmed"
    return rows


def _node(expression):
    expr = expression if isinstance(expression, Expression) else Expression.from_mapping(expression)
    if expr.op in {"lt", "le"}:
        return Expression({"lt": "gt", "le": "ge"}[expr.op], expr.value_type, tuple(reversed(expr.args)), overflow=expr.overflow)
    return expr


def _set_parameter(bindings, name, value):
    if value.op not in {"constant", "device"}:
        return None
    prior = bindings.get(name)
    if prior is not None and canonical_expression(prior) != canonical_expression(value):
        return None
    return {**bindings, name: value}


def match_expression(template, desired, bindings=None, *, _budget=None):
    """All exact matches, including comparison reversal and commutativity."""
    budget = [0] if _budget is None else _budget
    budget[0] += 1
    if budget[0] > 2048:
        raise UnknownInstructionSemantics('Expression matching budget exceeded')
    a, b, initial = _node(template), _node(desired), dict(bindings or {})
    if a.op == "parameter":
        if a.value_type != b.value_type:
            return []
        found = _set_parameter(initial, a.name, b)
        return [found] if found is not None else []
    if a.op == "read" and b.op == "device" and a.value_type == b.value_type:
        try:
            base = offset_device(b.name, -a.args[1].value)
        except UnknownInstructionSemantics:
            return []
        found = _set_parameter(initial, a.args[0].name, Expression("device", a.args[0].value_type, name=base))
        return [found] if found is not None else []
    if a.op != b.op or a.value_type != b.value_type or a.overflow != b.overflow or len(a.args) != len(b.args):
        return []
    if not a.args:
        return [initial] if canonical_expression(a) == canonical_expression(b) else []
    orders = [b.args]
    if a.op in {"eq", "ne", "and", "or", "xor", "bit_and", "bit_or", "bit_xor", "add", "mul"} and len(a.args) == 2:
        orders.append(tuple(reversed(b.args)))
    result = []
    for order in orders:
        states = [initial]
        for left, right in zip(a.args, order):
            states = [next_state for state in states for next_state in match_expression(left, right, state, _budget=budget)]
            if len(states) > 128:
                raise UnknownInstructionSemantics('Too many equivalent expression bindings')
        result.extend(states)
    return result


def _operand(expression):
    if expression.op == "device":
        return canonical_device(expression.name)
    if expression.op == "constant" and expression.value_type.kind in {"int", "bool"}:
        if (expression.value_type.kind == "int" and not expression.value_type.signed
                and expression.value >= 1 << (expression.value_type.bits - 1)):
            return "H" + format(expression.value, "X")
        return "K" + str(int(expression.value))
    raise UnknownInstructionSemantics("Named parameter is not a native operand")


def _effect_matches(behavior, desired, initial):
    kind = behavior["behavior"]
    if kind in {"conditional_results", "expression_write", "state_update"}:
        states = [initial]
        for effect in desired:
            if effect["kind"] != "value":
                return []
            next_states = []
            for output in behavior["outputs"]:
                target = output["target"]
                if (target["kind"] != effect["target"]["kind"]
                        or target.get("offset", 0) != effect["target"]["offset"]):
                    continue
                # The intent names a region base and an explicit offset. Core
                # cannot allocate a different base to use another result bit.
                address = effect["target"]["device"]
                for state in states:
                    assigned = _set_parameter(state, target["parameter"], Expression("device", ValueType("opaque"), name=address))
                    if assigned is not None:
                        next_states.extend(match_expression(output["expression"], effect["value"], assigned))
            states = next_states
        return states
    if len(desired) != 1 or desired[0]["kind"] != kind:
        return []
    effect = desired[0]
    if kind in {"range_copy", "range_shift"}:
        if kind == "range_shift" and behavior["direction"] != effect["direction"]:
            return []
        destination = behavior["destination"] if kind == "range_copy" else behavior["region"]
        assigned = _set_parameter(initial, destination, Expression("device", ValueType("opaque"), name=effect["target"]["device"]))
        if assigned is None:
            return []
        source = Expression.from_mapping(effect["source"])
        assigned = _set_parameter(assigned, behavior["source"], source)
        if assigned is None:
            return []
        states = match_expression(behavior["count"], effect["count"], assigned)
        if kind == "range_shift":
            states = [s for state in states for s in match_expression(behavior["shift"], effect["shift"], state)]
        return states
    if kind == 'external_action' and behavior.get('result_parameter'):
        assigned = _set_parameter(initial, behavior['result_parameter'], Expression(
            'device', ValueType('opaque'), name=effect['target']['device']))
        return [assigned] if assigned is not None and effect['target']['offset'] == 0 else []
    return []


def _bound_point_effects(behavior, bindings):
    """Compare all point writes, so a subset match cannot hide other results."""
    if behavior['behavior'] not in {'conditional_results', 'expression_write', 'state_update'}:
        return None
    def substitute(expr):
        if expr.op == 'parameter':
            if expr.name not in bindings:
                raise UnknownInstructionSemantics('Effect has an unbound parameter')
            return bindings[expr.name]
        if expr.op == 'read':
            return Expression('device', expr.value_type, name=offset_device(
                bindings[expr.args[0].name].name, expr.args[1].value))
        return Expression(expr.op, expr.value_type, tuple(substitute(a) for a in expr.args), expr.name,
                          expr.value, expr.overflow)
    return tuple(sorted((bindings[o['target']['parameter']].name, o['target'].get('offset', 0),
                         o['target']['kind'], canonical_expression(substitute(Expression.from_mapping(o['expression']))))
                        for o in behavior['outputs']))


def bind_operation_intent(raw, *, target_model, registry=None, confirmed_spec=None):
    """Resolve one exact call or return a visible gap; never guess ambiguity."""
    if registry is None:
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        registry = DEFAULT_INSTRUCTION_REGISTRY
    intent = normalize_operation_intents([raw])[0]
    base = {"intent_id": intent["id"], "target_model": target_model, "input": intent,
            "source_verification": "not_established", "hardware_effect": "not_tested"}
    if intent["status"] != "confirmed":
        return {**base, "status": "unresolved", "reason": "intent_not_user_confirmed"}
    matches, failures = [], []
    for opcode in [intent["opcode"]] if intent["opcode"] else registry.known_mnemonics():
        form = registry.resolve_form(opcode, cpu=target_model)
        if form is None or not form.spec.supports_cpu(target_model):
            continue
        spec = form.spec
        expected_trigger = "rising" if spec.execution_form in {"pulse", "single", "pulse_single", "single_pulse"} or form.pulse else "level"
        if intent["execution"]["trigger"] != expected_trigger:
            continue
        groups = [g for g in spec.definition_facts if g.dimension.startswith("effects.") and g.value.get("behavior")]
        for group in groups:
            closure = select_fact_dependencies(spec.definition_facts, [group.id], opcode=opcode, model=target_model)
            if closure["gaps"]:
                failures.extend(closure["gaps"])
                continue
            try:
                behavior = validate_behavior(group.value)
                initial = {name: Expression.from_mapping(value) for name, value in intent["parameters"].items()}
                states = _effect_matches(behavior, intent["effects"], initial)
            except DefinitionError as error:
                failures.append({"fact_id": group.id, "reason": str(error)})
                continue
            for state in states:
                if not spec.native_operand_order or set(spec.native_operand_order) - set(state):
                    continue
                operands = [_operand(state[name]) for name in spec.native_operand_order]
                if behavior.get('required_state') and any(
                        intent['execution'].get('required_state', {}).get(k) != value
                        for k, value in behavior['required_state'].items()):
                    failures.append({'fact_id': group.id, 'reason': 'required_state_not_confirmed'})
                    continue
                try:
                    from plc.validation import _validate_instruction_boundaries, _validate_element, VALID_OUTPUT_TYPES
                    _validate_instruction_boundaries(spec, operands, '$.operation_binding', target_model)
                    _validate_element({'type': 'APP_INSTR', 'opcode': opcode, 'operands': operands},
                                      '$.operation_binding', VALID_OUTPUT_TYPES, is_output=True,
                                      plc_model=target_model, require_catalogued_instructions=False)
                    known_violation = False
                    for condition in behavior.get('preconditions', []):
                        try:
                            from plc.instruction_definition import evaluate_expression
                            holds = evaluate_expression(condition, {name: value.value for name, value in state.items()
                                                                    if value.op == 'constant'})
                        except UnknownInstructionSemantics:
                            continue  # A device's runtime value is not known at binding time.
                        if not holds:
                            failures.append({'fact_id': group.id, 'reason': 'known_parameter_constraint_violation',
                                             'source_checked': closure['source_verification_complete']})
                            known_violation = True
                            break
                    if known_violation:
                        continue
                    from plc.instruction_effects import check_point_memory_scope
                    check_point_memory_scope(behavior, {name: value.name if value.op == 'device' else value.value
                                                       for name, value in state.items()})
                    if behavior.get('scope_limits', {}).get('destination') == 'ordinary_D_word_region':
                        from plc.device_policy import device_address
                        for output in behavior.get('outputs', []):
                            target = output['target']
                            region_base = state[target['parameter']].name
                            if not re.fullmatch(r'D\d+', region_base):
                                raise UnknownInstructionSemantics('Destination is outside formalized memory class')
                            width = Expression.from_mapping(output['expression']).value_type.bits
                            for i in range(max(1, width // 16)):
                                device_address(offset_device(region_base, target.get('offset', 0) + i),
                                               target_model, access='reset')
                    if behavior['behavior'] in {'range_copy', 'range_shift'}:
                        from plc.instruction_definition import evaluate_expression
                        constants = {k: v.value for k, v in state.items() if v.op == 'constant'}
                        count = evaluate_expression(behavior['count'], constants)
                        if not 1 <= count <= behavior.get('max_count', 1024):
                            raise UnknownInstructionSemantics('Range is outside formalized bounds')
                        limits = behavior.get('scope_limits', {})
                        prefixes = {'M'} if limits.get('memory') == 'ordinary_M_bit_regions' else {'D'}
                        names = [behavior['source'], behavior.get('destination') or behavior.get('region')]
                        if any(not re.fullmatch('(?:' + '|'.join(prefixes) + ')\\d+', state[name].name) for name in names):
                            raise UnknownInstructionSemantics('Memory class is outside formalized subset')
                        from plc.device_policy import device_address
                        for name in names:
                            extent = evaluate_expression(behavior['shift'], constants) if (
                                behavior['behavior'] == 'range_shift' and name == behavior['source']) else count
                            for i in (0, extent - 1):
                                device_address(offset_device(state[name].name, i), target_model, access='reset')
                        if behavior['behavior'] == 'range_shift':
                            shift = evaluate_expression(behavior['shift'], constants)
                            if not 1 <= shift <= count:
                                raise DefinitionError('Shift exceeds the region')
                            source = {offset_device(state[names[0]].name, i) for i in range(shift)}
                            destination = {offset_device(state[names[1]].name, i) for i in range(count)}
                            if source & destination:
                                raise DefinitionError('Source overlaps shifted region')
                except ValueError as error:
                    failures.append({'fact_id': group.id, 'reason': str(error)})
                    continue
                selected = (confirmed_spec or {}).get("selected_approach", {})
                constraints = selected.get("explicit_user_constraints", {})
                pinned = [*constraints.get("instruction_instances", []),
                          *selected.get("generation_contract", {}).get("instruction_instances", [])]
                same_opcode = [p for p in pinned if p.get('opcode') == opcode]
                if same_opcode and not any(list(p.get('operands', [])) == operands for p in same_opcode):
                    failures.append({"reason": "confirmed_instruction_instance_conflict", "opcode": opcode})
                    continue
                if opcode in [*constraints.get("forbidden_opcodes", []),
                              *selected.get('generation_contract', {}).get('forbidden_opcodes', [])]:
                    failures.append({"reason": "confirmed_opcode_forbidden", "opcode": opcode})
                    continue
                try:
                    whole_effects = _bound_point_effects(behavior, state)
                except (KeyError, UnknownInstructionSemantics) as error:
                    failures.append({'fact_id': group.id, 'reason': str(error)})
                    continue
                matches.append({"opcode": opcode, "operands": operands, "named_parameters": {k: v.as_mapping() for k, v in state.items()},
                                "fact_group": group.id, "dependencies": closure["bundles"][0]["fact_ids"],
                                "source_verification": "source_checked" if closure["source_verification_complete"] else "candidate_evidence",
                                "_all_point_effects": whole_effects,
                                "scope_limits": copy.deepcopy(behavior.get('scope_limits', {}))})
    unique = {}
    for match in matches:
        key = (match["opcode"], tuple(match["operands"]))
        if key not in unique or match["source_verification"] == "source_checked":
            unique[key] = match
    equivalent_calls = []
    if len(unique) > 1:
        whole_effects = {(m['opcode'], m['_all_point_effects']) for m in unique.values()}
        if len(whole_effects) == 1 and next(iter(whole_effects))[1] is not None:
            equivalent_calls = [{'opcode': m['opcode'], 'operands': list(m['operands'])}
                                for _, m in sorted(unique.items())]
            key = min(unique)
            unique = {key: unique[key]}
    if len(unique) != 1:
        reason = 'ambiguous_binding' if unique else ('known_parameter_constraint_violation' if any(
            failure.get('reason') == 'known_parameter_constraint_violation' and failure.get('source_checked')
            for failure in failures) else 'no_formal_binding')
        return {**base, "status": "unresolved", "reason": reason,
                "candidate_count": len(unique), "gaps": failures}
    result = next(iter(unique.values()))
    result.pop('_all_point_effects', None)
    return {**base, **result, "status": "bound", "claim": "deterministic_binding_within_fact_scope",
            **({'equivalent_native_calls': equivalent_calls} if equivalent_calls else {})}


def operation_intent_prompt(spec, *, target_model='FX3U'):
    rows = normalize_operation_intents((spec or {}).get("operation_intents", []))
    confirmed = [row["id"] for row in rows if row["status"] == "confirmed" and bind_operation_intent(
        row, target_model=target_model, confirmed_spec=spec)['status'] == 'bound']
    if not confirmed:
        return ""
    return ("\n# Confirmed operation effects\n对于已确认 operation_intents，输出字符串 `OP <id>` 引用该效果，"
            "OP 是固定字面标记，不替换为指令名。例如 ID 为 operation 时，输出必须是 \"OP operation\"。"
            "Core 会在独立参数绑定阶段转换为原生调用。不要为该调用另写操作数或重复原生指令。"
            "使能由梯级输入实现；指令自身的触发形式按交付事实，避免重复添加边沿门控。未确认候选不作为正确性答案。可引用 ID："
            + ", ".join(confirmed) + "\n")


def instruction_write_footprint(output, target_model, registry):
    """Return known writes and whether the whole footprint is formalized."""
    from plc.ir import analyze_instruction_access
    from plc.instruction_definition import evaluate_expression
    _reads, writes = analyze_instruction_access(output.get('opcode'), output.get('operands', []), plc_model=target_model)
    known = {canonical_device(w) for w in writes}
    form = registry.resolve_form(output.get('opcode'), cpu=target_model)
    if form is None or not form.spec.native_operand_order:
        return known, False
    operands = output.get('operands', [])
    if len(operands) != len(form.spec.native_operand_order):
        return known, False
    parameters = dict(zip(form.spec.native_operand_order, map(canonical_operand, operands)))
    constants = {k: int(v[1:], 16 if v.startswith('H') else 10) for k, v in parameters.items()
                 if re.fullmatch(r'K[+-]?\d+|H[0-9A-F]+', v)}
    groups = sorted(form.spec.definition_facts, key=lambda g: g.status != 'source_verified')
    for group in groups:
        if not group.dimension.startswith('effects.') or not group.value.get('behavior'):
            continue
        closure = select_fact_dependencies(groups, [group.id], opcode=output['opcode'], model=target_model)
        if closure['gaps']:
            continue
        try:
            behavior = validate_behavior(group.value)
            footprint = set(known)
            if behavior.get('outputs'):
                for item in behavior['outputs']:
                    target = item['target']
                    width = Expression.from_mapping(item['expression']).value_type.bits
                    count = max(1, width // 16) if target['kind'] == 'word' else 1
                    footprint.update(offset_device(parameters[target['parameter']], target.get('offset', 0) + i)
                                     for i in range(count))
            elif behavior['behavior'] in {'range_copy', 'range_shift'}:
                count = evaluate_expression(behavior['count'], constants)
                if not 1 <= count <= behavior.get('max_count', 1024):
                    continue
                destination = behavior.get('destination') or behavior.get('region')
                footprint.update(offset_device(parameters[destination], i) for i in range(count))
            else:
                # External protocols/layouts can write statuses beyond the base.
                continue
            return footprint, True
        except (DefinitionError, KeyError, ValueError):
            continue
    return known, False


def _same_native_operand(actual, expected, value_type):
    actual, expected = canonical_operand(actual), canonical_operand(expected)
    if actual == expected:
        return True
    if value_type.kind not in {'int', 'bool'} or value_type.encoding != 'binary':
        return False
    def value(operand):
        if not re.fullmatch(r'K[+-]?\d+|H[0-9A-F]+', operand):
            raise DefinitionError('Not a native integer constant')
        number = int(operand[1:], 16 if operand.startswith('H') else 10)
        if operand.startswith('K') and value_type.kind == 'int':
            minimum, maximum = value_type.bounds
            if not minimum <= number <= maximum:
                raise DefinitionError('Decimal constant exceeds the declared type')
        return value_type.decode_bits(number)
    try:
        return value(actual) == value(expected)
    except DefinitionError:
        return False


def _native_operands_match(actual, expected, receipt, order):
    return len(actual) == len(expected) and all(_same_native_operand(a, e,
        Expression.from_mapping(receipt['named_parameters'][name]).value_type)
        for a, e, name in zip(actual, expected, order))


def _same_read_values(actual, expected, receipt, order, indexes):
    """Bipartite matching preserves the multiset, including repeated values."""
    matches = {}
    def assign(expected_index, visited):
        value_type = Expression.from_mapping(receipt['named_parameters'][order[expected_index]]).value_type
        for actual_index in indexes:
            if actual_index in visited or not _same_native_operand(actual[actual_index], expected[expected_index], value_type):
                continue
            visited.add(actual_index)
            if actual_index not in matches or assign(matches[actual_index], visited):
                matches[actual_index] = expected_index
                return True
        return False
    return all(assign(index, set()) for index in indexes)


def _binding_write_indexes(form, receipt):
    group = next(g for g in form.spec.definition_facts if g.id == receipt['fact_group'])
    behavior = group.value
    names = {o['target']['parameter'] for o in behavior.get('outputs', [])}
    if behavior.get('behavior') in {'range_copy', 'range_shift'}:
        names.add(behavior.get('destination') or behavior['region'])
    if behavior.get('result_parameter'):
        names.add(behavior['result_parameter'])
    order = form.spec.native_operand_order
    return set(form.spec.write_indexes) | {order.index(name) for name in names if name in order}


def materialize_operation_references(compact, spec, *, target_model, registry=None, bind_native=True):
    """Independent stage after formatting, before existing compact expansion."""
    value = copy.deepcopy(compact)
    intents = {r["id"]: r for r in normalize_operation_intents((spec or {}).get("operation_intents", []))}
    receipts = []
    native_occurrences = {}
    if registry is None:
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        registry = DEFAULT_INSTRUCTION_REGISTRY
    def native_call(output):
        if isinstance(output, Mapping) and output.get('type') == 'APP_INSTR':
            return str(output.get('opcode', '')).upper(), list(output.get('operands', []))
        parts = output.split() if isinstance(output, str) else []
        if parts and parts[0].upper() == 'APP':
            parts = parts[1:]
        return (parts[0].upper(), parts[1:]) if parts else ('', [])
    slots = []
    for rung in value.get('r', value.get('rungs', [])) if isinstance(value, Mapping) else []:
        for branch in rung.get('b', rung.get('branches', [])) if isinstance(rung, Mapping) else []:
            outputs = branch.get('o', branch.get('outputs', [])) if isinstance(branch, Mapping) else []
            for index, output in enumerate(outputs):
                slots.append((outputs, index, output))
                if isinstance(output, str) and output.startswith('OP '):
                    opcode = intents.get(output[3:].strip(), {}).get('opcode', '')
                else:
                    opcode, _ = native_call(output)
                if registry.resolve_form(opcode, cpu=target_model):
                    native_occurrences[opcode] = native_occurrences.get(opcode, 0) + 1
    for outputs, index, output in slots:
        if not (isinstance(output, str) and output.startswith('OP ')):
            if not bind_native:
                continue
            opcode, operands = native_call(output)
            candidates = [i for i in intents.values() if i['status'] == 'confirmed' and i['opcode'] == opcode]
            if len(candidates) != 1 or native_occurrences.get(opcode) != 1:
                continue
            receipt = bind_operation_intent(candidates[0], target_model=target_model,
                                            registry=registry, confirmed_spec=spec)
            if receipt['status'] != 'bound' or receipt['source_verification'] != 'source_checked':
                continue
            expected = receipt['operands']
            form = registry.resolve_form(opcode, cpu=target_model)
            writes = _binding_write_indexes(form, receipt)
            if len(operands) != len(expected) or not writes:
                continue
            # Only relocate the same read values. Do not allocate new
            # addresses, change a count, overwrite a target or bypass
            # an explicit complete native instance supplied by the user.
            order = form.spec.native_operand_order
            if any(not _same_native_operand(operands[i], expected[i],
                Expression.from_mapping(receipt['named_parameters'][order[i]]).value_type) for i in writes):
                continue
            reads = [i for i in range(len(expected)) if i not in writes]
            if not _same_read_values(operands, expected, receipt, order, reads):
                continue
            valid = receipt.get('equivalent_native_calls', [{'operands': expected}])
            if any(_native_operands_match(operands, call['operands'], receipt, order) for call in valid):
                continue
            outputs[index] = ({**output, 'opcode': receipt['opcode'], 'operands': list(expected)}
                              if isinstance(output, Mapping) else ' '.join([receipt['opcode'], *expected]))
            receipts.append({**receipt, 'binding_mode': 'native_read_parameter_rebinding',
                             'input_call': {'opcode': opcode, 'operands': list(operands)},
                             'output_call': {'opcode': receipt['opcode'], 'operands': list(expected)},
                             'reason': 'confirmed_effect_requires_different_parameter_mapping'})
            continue
        identity = output[3:].strip()
        if identity not in intents:
            raise DefinitionError("Unknown operation reference: " + identity)
        receipt = bind_operation_intent(intents[identity], target_model=target_model, registry=registry, confirmed_spec=spec)
        receipts.append({**receipt, 'binding_mode': 'operation_reference'})
        if receipt["status"] != "bound":
            # There is no truthful native opcode to invent. The caller
            # retains the original model response and this diagnostic.
            raise UnknownInstructionSemantics("Operation " + identity + ": " + receipt["reason"])
        if receipt['source_verification'] != 'source_checked':
            raise UnknownInstructionSemantics('Operation ' + identity + ': effect_fact_not_source_checked')
        outputs[index] = " ".join([receipt["opcode"], *receipt["operands"]])
    return value, {"stage": "Core_operation_binding", "receipts": receipts, "model_calls": 0}


def _enable_paths_match(expression, paths, assumptions, trigger='level'):
    """Prove Boolean gates and bounded edge sequences for every assignment."""
    from itertools import product
    from plc.instruction_definition import evaluate_expression
    devices = set()
    def inspect(node):
        if node.value_type.kind != 'bool':
            raise UnknownInstructionSemantics('Enable expression contains a non-bit value')
        if node.op == 'device':
            devices.add(canonical_device(node.name))
        elif node.op not in {'constant', 'and', 'or', 'xor', 'not', 'eq', 'ne', 'select'}:
            raise UnknownInstructionSemantics('Enable operator has not been formalized as contacts')
        for child in node.args:
            inspect(child)
    inspect(expression)
    normalized = []
    for path in paths:
        row = []
        for contact in path:
            kind, address = str(contact).split()
            if kind not in {'NO', 'NC', 'P', 'F'}:
                raise UnknownInstructionSemantics('Edge or non-bit contact requires its own execution evidence')
            address = canonical_device(address)
            devices.add(address)
            row.append((kind, address))
        normalized.append(row)
    unknown = sorted(devices - set(assumptions))
    edges = any(kind in {'P', 'F'} for path in normalized for kind, _ in path)
    frames = 3 if edges and trigger != 'level' else 2 if edges else 1
    if len(unknown) * frames > 10:
        raise UnknownInstructionSemantics('Enable equivalence exceeds the bounded proof domain')
    def contact_gate(current, previous):
        def active(kind, address):
            if kind == 'NO':
                return current[address]
            if kind == 'NC':
                return not current[address]
            return (current[address] and not previous[address]) if kind == 'P' else (previous[address] and not current[address])
        return any(all(active(kind, address) for kind, address in path) for path in normalized)
    def fired(current, previous):
        return current if trigger == 'level' else current and not previous if trigger == 'rising' else previous and not current
    for bits in product((False, True), repeat=len(unknown) * frames):
        snapshots = [{**assumptions, **dict(zip(unknown, bits[i * len(unknown):(i + 1) * len(unknown)]))}
                     for i in range(frames)]
        current = snapshots[-1]
        previous = snapshots[-2] if edges else current
        wanted = evaluate_expression(expression, current)
        actual = contact_gate(current, previous)
        if edges and trigger != 'level':
            wanted = fired(wanted, evaluate_expression(expression, previous))
            actual = fired(actual, contact_gate(previous, snapshots[-3]))
        if wanted != actual:
            return False
    return True


def check_operation_intents(ladder, spec, *, target_model, registry=None):
    """Check actual raw native calls and conditions, with explicit unknowns."""
    rows, violations = [], []
    if registry is None:
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        registry = DEFAULT_INSTRUCTION_REGISTRY
    from plc.specification.approach import _expand_contact_paths
    calls, all_outputs = [], []
    def understood_inputs(elements):
        return all(e is None or (isinstance(e, Mapping) and (e.get('type') in {'NO', 'NC', 'P', 'F'} or
                   e.get('type') == 'parallel_block' and all(understood_inputs(path) for path in
                       e.get('paths', e.get('branches', [])))))
                   for e in elements)
    for rung in (ladder or {}).get("rungs", []):
        common = [rung.get("header_element"), *rung.get("shared_inputs", [])]
        for branch in rung.get("branches", []):
            elements = [*common, *branch.get('inputs', [])]
            paths = _expand_contact_paths(elements, include_edges=True)
            for output in branch.get("outputs", []):
                all_outputs.append(output)
                if output.get("type") == "APP_INSTR":
                    calls.append((output, paths, understood_inputs(elements)))
    for intent in normalize_operation_intents((spec or {}).get("operation_intents", [])):
        row = {"requirement_id": "operation_intents:" + intent["id"], "check": "operation_effect_binding"}
        receipt = bind_operation_intent(intent, target_model=target_model, registry=registry, confirmed_spec=spec)
        if receipt["status"] != "bound":
            status = 'violated' if receipt['reason'] == 'known_parameter_constraint_violation' else 'unresolved'
            record = {**row, 'status': status, 'reason': receipt['reason']}
            rows.append(record)
            if status == 'violated':
                violations.append(record)
            continue
        expected = (receipt["opcode"], tuple(receipt["operands"]))
        if receipt['source_verification'] != 'source_checked':
            rows.append({**row, 'status': 'unresolved', 'reason': 'effect_fact_not_source_checked',
                         'source_verification': receipt['source_verification'], 'hardware_effect': 'not_tested'})
            continue
        order = registry.resolve_form(receipt['opcode'], cpu=target_model).spec.native_operand_order
        valid_operands = receipt.get('equivalent_native_calls', [{'operands': expected[1]}])
        relevant = [(out, paths, understood) for out, paths, understood in calls if str(out.get("opcode", "")).upper() == receipt["opcode"]]
        exact = [(out, paths, understood) for out, paths, understood in relevant if
                 any(_native_operands_match(out.get('operands', []), call['operands'], receipt, order)
                     for call in valid_operands)]
        status, reason = "verified", None
        if len(exact) != 1:
            status, reason = "violated", "missing_or_duplicate_effect_call"
        elif len(relevant) > 1 and sum(1 for i in normalize_operation_intents(spec.get('operation_intents', []))
                                     if i['status'] == 'confirmed' and i['opcode'] == receipt['opcode']) == 1:
            status, reason = 'violated', 'extra_designated_instruction_call'
        enable = Expression.from_mapping(intent["enable"])
        if status == "verified":
            if not exact[0][2]:
                status, reason = 'unresolved', 'additional_enable_predicate_not_formalized'
            else:
                # A confirmed bit precondition may make a contact redundant;
                # unknown states and arbitrary extra conditions never do.
                assumptions = {k: v for k, v in intent['execution'].get('required_state', {}).items()
                               if type(v) is bool and re.fullmatch(r'(?:M|SM)\d+', k)}
                try:
                    if not _enable_paths_match(enable, exact[0][1], assumptions, intent['execution']['trigger']):
                        status, reason = 'violated', 'enable_path_differs_from_confirmed_effect'
                except UnknownInstructionSemantics:
                    status, reason = 'unresolved', 'enable_expression_not_formalized_as_ladder_path'
        if status == 'verified':
            protected = {canonical_device(effect['target']['device']) for effect in intent['effects']}
            # Full consecutive effects are protected, including unselected
            # neighboring result bits. Incomplete footprints stay unresolved.
            actual_form = registry.resolve_form(receipt['opcode'], cpu=target_model)
            group = next(g for g in actual_form.spec.definition_facts if g.id == receipt['fact_group'])
            parameters = {k: Expression.from_mapping(v) for k, v in receipt['named_parameters'].items()}
            if group.value.get('outputs'):
                for output in group.value['outputs']:
                    base = parameters[output['target']['parameter']].name
                    expr_type = Expression.from_mapping(output['expression']).value_type
                    count = max(1, expr_type.bits // 16) if output['target']['kind'] == 'word' else 1
                    try:
                        protected.update(offset_device(base, output['target'].get('offset', 0) + i) for i in range(count))
                    except UnknownInstructionSemantics:
                        status, reason = 'unresolved', 'result_region_addressing_not_formalized'
            elif group.value.get('behavior') in {'range_copy', 'range_shift'}:
                from plc.instruction_definition import evaluate_expression
                count = evaluate_expression(group.value['count'], {k: v.value for k, v in parameters.items() if v.op == 'constant'})
                destination = group.value.get('destination') or group.value.get('region')
                protected.update(offset_device(parameters[destination].name, i) for i in range(count))
            protected.update(intent['execution'].get('required_state', {}))
            incomplete_footprint = False
            for output in all_outputs:
                if output is exact[0][0]:
                    continue
                if output.get('type') == 'APP_INSTR':
                    writes, complete = instruction_write_footprint(output, target_model, registry)
                    incomplete_footprint |= not complete
                else:
                    writes = [output.get('address') or output.get('device')] if output.get('type') in {'COIL', 'SET', 'RST', 'TIMER', 'COUNTER', 'PLS', 'PLF'} else []
                if protected & {canonical_device(w) for w in writes}:
                    status, reason = 'violated', 'additional_write_overlaps_required_effect'
                    break
            if status == 'verified' and incomplete_footprint:
                status, reason = 'unresolved', 'additional_instruction_write_extent_not_formalized'
        record = {**row, "status": status, "expected": {"opcode": expected[0], "operands": list(expected[1])},
                  "source_verification": receipt["source_verification"], "hardware_effect": "not_tested",
                  "verification_scope": "native_parameters_boolean_gate_and_declared_trigger",
                  "whole_program_execution": "not_measured",
                  "scope_limits": receipt.get('scope_limits', {}),
                  "confirmed_preconditions": intent['execution'].get('required_state', {}),
                  **({"reason": reason} if reason else {})}
        rows.append(record)
        if status == "violated":
            violations.append(record)
    return rows, violations
