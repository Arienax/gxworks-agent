"""Shared instruction facts, finite-width expressions and dependency views.

The instruction registry owns these definitions. Source compilation belongs to
knowledge; this module neither reads manuals nor calls a model. A formal
expression's evaluability, source verification and CPU applicability are
independent claims.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping


DEFINITION_DIMENSIONS = (
    "identity", "parameters", "execution", "effects", "memory", "resources", "constraints",
)
FACT_STATUSES = frozenset({
    "unknown", "not_applicable", "declared_unverified", "candidate_evidence", "source_verified", "conflict",
})


class DefinitionError(ValueError):
    """A definition is malformed or an expression cannot be interpreted safely."""


class UnknownInstructionSemantics(DefinitionError):
    """The requested operation is outside the explicitly formalized behavior."""


@dataclass(frozen=True)
class ValueType:
    kind: str
    bits: int = 0
    signed: bool = False
    unit: str = ""
    encoding: str = "binary"
    element: ValueType | None = None
    count: int = 0

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> ValueType:
        if not isinstance(raw, Mapping):
            raise DefinitionError("An expression needs an explicit value type")
        allowed = {"kind", "bits", "signed", "unit", "encoding", "element", "count"}
        if set(raw) - allowed:
            raise DefinitionError("Unknown value-type fields")
        kind = raw.get("kind")
        bits, signed = raw.get("bits", 0), raw.get("signed", False)
        count = raw.get("count", 0)
        if type(bits) is not int or type(signed) is not bool or type(count) is not int:
            raise DefinitionError("Invalid finite-width type")
        if kind not in {"bool", "int", "vector", "opaque"}:
            raise DefinitionError("Unknown value type")
        if kind == "bool" and (bits not in (0, 1) or signed):
            raise DefinitionError("Boolean type must be one unsigned bit")
        if kind == "int" and bits not in {8, 16, 32, 64}:
            raise DefinitionError("Integer width must be explicit")
        element = None
        if kind == "vector":
            if not 1 <= count <= 512 or bits or signed:
                raise DefinitionError("Invalid vector type")
            if not isinstance(raw.get("element"), Mapping) or raw["element"].get("kind") not in {"bool", "int"}:
                raise DefinitionError("Vector elements must be scalar")
            element = cls.from_mapping(raw.get("element"))
            if element.kind not in {"bool", "int"}:
                raise DefinitionError("Unsupported vector element type")
        elif count or raw.get("element") is not None:
            raise DefinitionError("Only vectors have an element type")
        if kind == "opaque" and (bits or signed):
            raise DefinitionError("Opaque values have no inferred width")
        unit, encoding = raw.get("unit", ""), raw.get("encoding", "binary")
        if not isinstance(unit, str) or not isinstance(encoding, str) or not encoding:
            raise DefinitionError("Invalid unit or encoding")
        return cls(kind, 1 if kind == "bool" else bits, signed, unit, encoding, element, count)

    def as_mapping(self):
        result = {"kind": self.kind}
        if self.kind in {"bool", "int"}:
            result.update(bits=self.bits, signed=self.signed)
        if self.unit:
            result["unit"] = self.unit
        if self.encoding != "binary":
            result["encoding"] = self.encoding
        if self.element is not None:
            result.update(element=self.element.as_mapping(), count=self.count)
        return result

    @property
    def bounds(self):
        if self.kind != "int":
            raise DefinitionError("Only integers have numeric bounds")
        return (-(1 << (self.bits - 1)), (1 << (self.bits - 1)) - 1) if self.signed else (0, (1 << self.bits) - 1)

    def decode_bits(self, value):
        if self.kind == "bool":
            if type(value) is bool:
                return value
            if type(value) is int and value in (0, 1):
                return bool(value)
            raise DefinitionError("Boolean value must be a bit")
        if self.kind == "vector":
            if not isinstance(value, (list, tuple)) or len(value) != self.count:
                raise DefinitionError("Wrong vector length")
            return tuple(self.element.decode_bits(item) for item in value)
        if self.kind != "int" or type(value) is not int:
            raise UnknownInstructionSemantics("Value is not a finite-width integer")
        minimum, maximum = self.bounds
        if not minimum <= value <= (1 << self.bits) - 1:
            raise DefinitionError("Input exceeds its finite representation")
        raw = value & ((1 << self.bits) - 1)
        return raw - (1 << self.bits) if self.signed and raw >= (1 << (self.bits - 1)) else raw


_COMPARISONS = frozenset({"eq", "ne", "gt", "ge", "lt", "le"})
_ARITHMETIC = frozenset({"add", "sub", "mul", "div", "mod", "bit_and", "bit_or", "bit_xor", "shift_left", "shift_right"})
_BOOLEAN = frozenset({"and", "or", "xor"})
_LEAVES = frozenset({"parameter", "device", "constant", "state"})
_OPERATORS = _COMPARISONS | _ARITHMETIC | _BOOLEAN | _LEAVES | {"not", "bit_not", "select", "vector", "read"}


@dataclass(frozen=True)
class Expression:
    op: str
    value_type: ValueType
    args: tuple[Expression, ...] = ()
    name: str = ""
    value: Any = None
    overflow: str = "unknown"

    @classmethod
    def from_mapping(cls, raw, *, _depth=0, _nodes=None):
        nodes = [0] if _nodes is None else _nodes
        nodes[0] += 1
        if _depth > 24 or nodes[0] > 256 or not isinstance(raw, Mapping):
            raise DefinitionError("Expression is too deep, large or malformed")
        if set(raw) - {"op", "type", "args", "name", "value", "overflow"}:
            raise DefinitionError("Unknown expression fields")
        op = raw.get("op")
        if op not in _OPERATORS:
            raise UnknownInstructionSemantics("Unknown expression operator")
        value_type = ValueType.from_mapping(raw.get("type"))
        args_raw = raw.get("args", [])
        if not isinstance(args_raw, (list, tuple)):
            raise DefinitionError("Expression arguments must be an array")
        args = tuple(cls.from_mapping(item, _depth=_depth + 1, _nodes=nodes) for item in args_raw)
        name, value = raw.get("name", ""), raw.get("value")
        overflow = raw.get("overflow", "unknown")
        if not isinstance(name, str) or overflow not in {"unknown", "wrap", "reject"}:
            raise DefinitionError("Invalid expression metadata")
        if op in _LEAVES:
            if args:
                raise DefinitionError("Leaf expression has arguments")
            if op == "constant":
                normalized = value_type.decode_bits(value)
                # Constants denote typed mathematical values, not implicit bit
                # reinterpretations. Raw device words use decode_bits instead.
                if normalized != value or type(value) is bool and value_type.kind != "bool":
                    raise DefinitionError("Constant does not fit its declared type")
            elif not name or value is not None:
                raise DefinitionError("A symbolic leaf needs a name")
        elif name or value is not None:
            raise DefinitionError("Operator expression contains leaf fields")
        elif op in _COMPARISONS:
            if len(args) != 2 or args[0].value_type != args[1].value_type or value_type.kind != "bool":
                raise DefinitionError("Comparison needs equal input types and a boolean result")
            if args[0].value_type.kind not in {"int", "bool", "vector"}:
                raise UnknownInstructionSemantics("Opaque values cannot be compared")
            if op not in {"eq", "ne"} and args[0].value_type.kind == "bool":
                raise DefinitionError("Ordered boolean comparison is unsupported")
        elif op in _ARITHMETIC:
            if len(args) != 2 or value_type.kind != "int" or args[0].value_type.kind != "int":
                raise DefinitionError("Invalid typed arithmetic")
            if op in {"shift_left", "shift_right"}:
                if args[0].value_type != value_type or args[1].value_type.kind != "int" or args[1].value_type.signed:
                    raise DefinitionError("Shift amount needs an unsigned integer type")
            elif args[1].value_type != args[0].value_type:
                raise DefinitionError("Arithmetic input types differ")
            elif (value_type.bits < args[0].value_type.bits or
                  (value_type.signed, value_type.unit, value_type.encoding) !=
                  (args[0].value_type.signed, args[0].value_type.unit, args[0].value_type.encoding)):
                raise DefinitionError("Arithmetic result cannot narrow or reinterpret its inputs")
        elif op in _BOOLEAN:
            if len(args) != 2 or value_type.kind != "bool" or any(a.value_type != value_type for a in args):
                raise DefinitionError("Invalid boolean expression")
        elif op in {"not", "bit_not"}:
            if len(args) != 1 or args[0].value_type != value_type or value_type.kind != ("bool" if op == "not" else "int"):
                raise DefinitionError("Invalid unary expression")
        elif op == "select":
            if len(args) != 3 or args[0].value_type.kind != "bool" or any(a.value_type != value_type for a in args[1:]):
                raise DefinitionError("Invalid conditional expression")
        elif op == "vector":
            if value_type.kind != "vector" or len(args) != value_type.count or any(a.value_type != value_type.element for a in args):
                raise DefinitionError("Invalid vector construction")
        elif op == "read":
            if len(args) != 2 or args[0].op != "parameter" or args[1].op != "constant" or args[1].value_type.kind != "int" or args[1].value < 0:
                raise DefinitionError("Memory read needs a parameter base and explicit nonnegative offset")
        return cls(op, value_type, args, name, copy.deepcopy(value), overflow)

    def as_mapping(self):
        result = {"op": self.op, "type": self.value_type.as_mapping()}
        if self.args:
            result["args"] = [a.as_mapping() for a in self.args]
        if self.name:
            result["name"] = self.name
        if self.op == "constant":
            result["value"] = copy.deepcopy(self.value)
        if self.overflow != "unknown":
            result["overflow"] = self.overflow
        return result


def evaluate_expression(expression, values, *, state=None, memory_reader=None):
    """Evaluate the formal subset; unknown overflow/protocol behavior stays unknown."""
    expr = expression if isinstance(expression, Expression) else Expression.from_mapping(expression)
    if expr.op == "constant":
        return expr.value_type.decode_bits(expr.value)
    if expr.op in {"parameter", "device", "state"}:
        environment = state if expr.op == "state" else values
        if environment is None or expr.name not in environment:
            raise UnknownInstructionSemantics("Missing formal input")
        return expr.value_type.decode_bits(environment[expr.name])
    if expr.op == "read":
        if memory_reader is None or expr.args[0].name not in values:
            raise UnknownInstructionSemantics("Memory interpretation is unavailable")
        return expr.value_type.decode_bits(memory_reader(values[expr.args[0].name], expr.args[1].value))
    if expr.op == "select":
        condition = evaluate_expression(expr.args[0], values, state=state, memory_reader=memory_reader)
        return evaluate_expression(expr.args[1 if condition else 2], values, state=state, memory_reader=memory_reader)
    if expr.op in {'and', 'or'}:
        unknown = False
        for argument in expr.args:
            try:
                value = evaluate_expression(argument, values, state=state, memory_reader=memory_reader)
            except UnknownInstructionSemantics:
                unknown = True
                continue
            if value == (expr.op == 'or'):
                return value
        if unknown:
            raise UnknownInstructionSemantics('Boolean condition depends on an unknown input')
        return expr.op == 'and'
    args = [evaluate_expression(a, values, state=state, memory_reader=memory_reader) for a in expr.args]
    operations = {
        "eq": lambda: args[0] == args[1], "ne": lambda: args[0] != args[1],
        "gt": lambda: args[0] > args[1], "ge": lambda: args[0] >= args[1],
        "lt": lambda: args[0] < args[1], "le": lambda: args[0] <= args[1],
        "and": lambda: args[0] and args[1], "or": lambda: args[0] or args[1],
        "xor": lambda: bool(args[0]) != bool(args[1]), "not": lambda: not args[0],
        "add": lambda: args[0] + args[1], "sub": lambda: args[0] - args[1],
        "mul": lambda: args[0] * args[1], "bit_and": lambda: args[0] & args[1],
        "bit_or": lambda: args[0] | args[1], "bit_xor": lambda: args[0] ^ args[1],
        "bit_not": lambda: ~args[0], "vector": lambda: tuple(args),
    }
    if expr.op in {"div", "mod"}:
        if args[1] == 0:
            raise UnknownInstructionSemantics("Division exception behavior is not defined by an expression")
        quotient = (abs(args[0]) // abs(args[1])) * (-1 if (args[0] < 0) != (args[1] < 0) else 1)
        value = quotient if expr.op == "div" else args[0] - quotient * args[1]
    elif expr.op in {"shift_left", "shift_right"}:
        if not 0 <= args[1] < expr.value_type.bits:
            raise UnknownInstructionSemantics("Shift boundary behavior is not specified")
        value = args[0] << args[1] if expr.op == "shift_left" else args[0] >> args[1]
    else:
        value = operations[expr.op]()
    if expr.value_type.kind == "int":
        minimum, maximum = expr.value_type.bounds
        if expr.overflow == "wrap":
            return expr.value_type.decode_bits(value & ((1 << expr.value_type.bits) - 1))
        if not minimum <= value <= maximum:
            if expr.overflow == "reject":
                raise DefinitionError("Formal expression overflow")
            raise UnknownInstructionSemantics("Instruction overflow behavior has not been formalized")
    return value


def canonical_expression(expression):
    """Normalize exact algebraic symmetries, never natural-language meanings."""
    expr = expression if isinstance(expression, Expression) else Expression.from_mapping(expression)
    args = [canonical_expression(a) for a in expr.args]
    op = expr.op
    if op in {"lt", "le"}:
        op = {"lt": "gt", "le": "ge"}[op]
        args.reverse()
    if op in {"eq", "ne", "and", "or", "xor", "bit_and", "bit_or", "bit_xor", "add", "mul"}:
        args.sort(key=repr)
    return (op, expr.value_type, tuple(args), expr.name, expr.value, expr.overflow)


@dataclass(frozen=True)
class InstructionFactGroup:
    id: str
    dimension: str
    value: Mapping[str, Any]
    status: str
    sources: tuple[Mapping[str, Any], ...] = ()
    depends_on: tuple[str, ...] = ()
    members: tuple[str, ...] = ()
    scope: Mapping[str, Any] | None = None
    verification: str = "not_performed"

    @classmethod
    def from_mapping(cls, raw):
        if not isinstance(raw, Mapping):
            raise DefinitionError("Fact group must be an object")
        identity, dimension, status = raw.get("id"), raw.get("dimension"), raw.get("status", "unknown")
        if not isinstance(identity, str) or not identity or not isinstance(dimension, str) or not dimension:
            raise DefinitionError("Fact group identity is required")
        if dimension.split('.')[0] not in DEFINITION_DIMENSIONS or status not in FACT_STATUSES:
            raise DefinitionError("Unknown fact dimension or status")
        value, sources = raw.get("value", {}), raw.get("sources", [])
        if not isinstance(value, Mapping) or not isinstance(sources, (list, tuple)) or any(not isinstance(s, Mapping) for s in sources):
            raise DefinitionError("Invalid fact content or sources")
        if status in {"candidate_evidence", "source_verified"} and not sources:
            raise DefinitionError("Sourced fact group has no evidence")
        dependencies, members = raw.get("depends_on", []), raw.get("members", [])
        if any(not isinstance(v, (list, tuple)) or any(not isinstance(s, str) or not s for s in v) or len(set(v)) != len(v)
               for v in (dependencies, members)):
            raise DefinitionError("Invalid fact dependencies or members")
        scope = raw.get("scope") or {}
        if not isinstance(scope, Mapping):
            raise DefinitionError("Invalid fact scope")
        for key, values in scope.items():
            if key not in {"models", "forms", "languages", "versions"} or not isinstance(values, (list, tuple)) or any(not isinstance(v, str) or not v for v in values):
                raise DefinitionError("Invalid scoped fact applicability")
        verification = raw.get("verification", "not_performed")
        if verification not in {"not_performed", "source_checked"}:
            raise DefinitionError("Invalid source verification receipt")
        if status == "source_verified" and verification != "source_checked":
            raise DefinitionError("Source-verified fact needs its own review receipt")
        return cls(identity, dimension, copy.deepcopy(dict(value)), status, tuple(copy.deepcopy(sources)),
                   tuple(dependencies), tuple(members), copy.deepcopy(dict(scope)), verification)

    def as_mapping(self):
        return {"id": self.id, "dimension": self.dimension, "value": copy.deepcopy(dict(self.value)),
                "status": self.status, "sources": copy.deepcopy(list(self.sources)),
                "depends_on": list(self.depends_on), "members": list(self.members),
                "scope": copy.deepcopy(dict(self.scope or {})), "verification": self.verification}

    def applies_to(self, *, opcode, model, language="ladder", version=None):
        scope = self.scope or {}
        for key, value in (("forms", opcode), ("models", model), ("languages", language), ("versions", version)):
            if scope.get(key) and value not in scope[key]:
                return False
        return True


def select_fact_dependencies(groups, requested_ids, *, opcode, model, language="ladder", version=None):
    """Return complete dependency closures and explicit gaps, without a budget cut."""
    by_id = {}
    for raw in groups:
        group = raw if isinstance(raw, InstructionFactGroup) else InstructionFactGroup.from_mapping(raw)
        if group.id in by_id:
            raise DefinitionError("Duplicate fact group identity")
        by_id[group.id] = group
    selected, gaps, visiting = {}, [], set()

    def visit(identity, root):
        if len(visiting) > 128:
            gaps.append({"root": root, "fact_id": identity, "reason": "dependency_depth_limit"})
            return False
        if identity in visiting:
            gaps.append({"root": root, "fact_id": identity, "reason": "cyclic_dependency"})
            return False
        group = by_id.get(identity)
        if group is None:
            gaps.append({"root": root, "fact_id": identity, "reason": "missing_dependency"})
            return False
        if not group.applies_to(opcode=opcode, model=model, language=language, version=version):
            gaps.append({"root": root, "fact_id": identity, "reason": "inapplicable_dependency"})
            return False
        if group.status in {"unknown", "conflict", "not_applicable"}:
            gaps.append({"root": root, "fact_id": identity, "reason": group.status})
            return False
        visiting.add(identity)
        okay = all([visit(dependency, root) for dependency in group.depends_on])
        visiting.remove(identity)
        if okay:
            selected[identity] = group
        return okay

    bundles = []
    for identity in dict.fromkeys(requested_ids):
        before = set(selected)
        if visit(identity, identity):
            closure = set()
            def collect(key):
                if key in closure:
                    return
                closure.add(key)
                for dependency in by_id[key].depends_on:
                    collect(dependency)
            collect(identity)
            bundles.append({"root": identity, "fact_ids": sorted(closure)})
        else:
            for key in set(selected) - before:
                del selected[key]
    return {"groups": [g.as_mapping() for g in selected.values()], "bundles": bundles, "gaps": gaps,
            "source_verification_complete": bool(selected) and all(g.status == "source_verified" for g in selected.values())}


def materialize_instruction_definition(target, *, plc_model, lanes, registry=None):
    """One resolved definition projects existing independently owned lanes.

    Existing signature, purpose, boundary and runtime information is preserved;
    compiled effect facts augment it without inheriting verification. A missing
    operation model remains a gap even when all operand purposes are present.
    """
    if registry is None:
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        registry = DEFAULT_INSTRUCTION_REGISTRY
    opcode = str(target.get("opcode") or target.get("base_opcode") or "") if isinstance(target, Mapping) else str(target or "")
    opcode, model = opcode.strip().upper(), str(plc_model or "").strip().upper()
    form = registry.resolve_form(opcode, cpu=model)
    spec = form.spec if form is not None else None
    inventory = registry.definition_entry(opcode, cpu=model) or {}
    applicability = copy.deepcopy(lanes.get("target_applicability") or {})
    groups = [f.as_mapping() for f in spec.definition_facts if f.applies_to(opcode=opcode, model=model)] if spec else []
    if not groups:
        groups = [InstructionFactGroup.from_mapping(f).as_mapping() for f in inventory.get('facts', [])]
    parameters = copy.deepcopy(lanes.get("operand_slots") or [])
    if not parameters:
        source_orders = {tuple(g['value']['symbols']) for g in groups if g['dimension'] == 'parameters.source_order'
                         and g['status'] in {'candidate_evidence', 'source_verified'}}
        if len(source_orders) == 1:
            from plc.instruction_semantics import bind_operand_slots
            parameters = bind_operand_slots({}, {'native_operand_order': list(next(iter(source_orders))),
                                                 'operand_order_status': 'candidate_evidence'})
    # Compiled operand facts use the same strict slot/facet arbitration.
    bindings = []
    for group in groups:
        value = group["value"]
        if (group["dimension"].startswith("parameters.") and group["status"] in
                {"declared_unverified", "candidate_evidence", "source_verified"}
                and value.get("facet") in {"purpose", "unit", "encoding", "range", "condition"}):
            bindings.append({"position": value["position"], "fact": {
                "facet": value["facet"], "value": value["value"], "status": group["status"],
                "sources": copy.deepcopy(group["sources"]),
                **({"conditions": copy.deepcopy(value["conditions"])} if value.get("conditions") else {}),
            }})
    if bindings:
        from plc.instruction_semantics import attach_operand_usage
        parameters = attach_operand_usage(parameters, bindings)
    grouped = {dimension: [g for g in groups if g['dimension'].split('.')[0] == dimension]
               for dimension in DEFINITION_DIMENSIONS}
    coverage = spec.contract_coverage() if spec else {}
    dimension_status = {}
    legacy_dimensions = {
        "identity": ("form_identity", "cpu_applicability"),
        "parameters": ("arity", "operand_order", "operand_roles", "operand_types"),
        "execution": ("execution_form", "execution_conditions"),
        "memory": ("numeric_and_memory_boundaries",),
        "resources": ("completion_ownership", "hardware_applicability"),
        "constraints": ("numeric_and_memory_boundaries",),
    }
    for dimension in DEFINITION_DIMENSIONS:
        evidence = [g['status'] for g in grouped[dimension]]
        evidence.extend(coverage.get(key, "unresolved") for key in legacy_dimensions.get(dimension, ()))
        if "conflict" in evidence:
            status = "conflict"
        elif "candidate_evidence" in evidence:
            status = "candidate_evidence"
        elif "source_verified" in evidence:
            status = "partially_source_verified"
        elif "declared_unverified" in evidence:
            status = "declared_unverified"
        else:
            status = "unknown"
        dimension_status[dimension] = status
    return {
        "schema_version": 1, "vendor": spec.vendor if spec else "mitsubishi", "opcode": opcode,
        "target_model": model, "base_opcode": form.base_mnemonic if form else opcode,
        "form": {"double": form.double, "pulse": form.pulse} if form else {},
        "applicability": applicability, "parameters": parameters,
        "execution": {"form": spec.execution_form, "status": coverage.get("execution_form", "unknown")}
                     if spec else {"status": "unknown"},
        "runtime": copy.deepcopy(lanes.get("runtime_semantics") or {}),
        "constraints": {key: copy.deepcopy(applicability[key]) for key in
                        ("operand_constraints", "numeric_operand_boundaries", "disjoint_bit_ranges") if applicability.get(key)},
        "facts": groups, "dimension_status": dimension_status,
        "source_materials": copy.deepcopy(list(spec.source_materials)) if spec and spec.source_materials else
                            copy.deepcopy(inventory.get('source_materials', [])),
        "uninterpreted_content": copy.deepcopy(list(spec.uninterpreted_content)) if spec and spec.uninterpreted_content else
                                 copy.deepcopy(inventory.get('uninterpreted_content', [])),
        "inventory_gaps": copy.deepcopy(inventory.get('gaps', [])),
        "semantic_completeness": "not_established",
        "verification_scope": "per_fact_exact_cpu_and_form",
    }


def instruction_task_view(definition, *, questions=None):
    """Choose task facts and atomic dependencies; receipt stages stay separate."""
    dimensions = set()
    for question in questions or ("operands", "operation", "execution", "limits"):
        dimensions.update({"operands": {"parameters"}, "operation": {"effects", "memory"},
                           "execution": {"execution", "resources"}, "limits": {"constraints"}}.get(question, ()))
    groups = definition.get("facts", [])
    by_id = {g['id']: g for g in groups}
    roots = [g['id'] for g in groups if g['dimension'].split('.')[0] in dimensions]
    from plc.instruction_effects import effect_signature
    preferred, alternatives = {}, {}
    for group in sorted(groups, key=lambda g: (g['status'] != 'source_verified', g['id'])):
        if group['id'] not in roots or not group['value'].get('behavior') or group['status'] in {'unknown', 'conflict'}:
            continue
        signature = effect_signature(group['value'])
        if signature is not None:
            # Equal result expressions alone cannot erase different enable,
            # retention, resource or constraint dependencies. Compare the
            # complete semantic closure; status/source identity stays separate.
            dependency = select_fact_dependencies(groups, [group['id']], opcode=definition['opcode'],
                                                  model=definition['target_model'])
            if dependency['gaps']:
                continue
            def semantic_value(value):
                return {k: v for k, v in value.items() if k not in {'text', 'representation'}}
            import json
            signature = (signature, tuple(sorted(json.dumps({'dimension': g['dimension'],
                'value': semantic_value(g['value']), 'scope': g['scope'], 'members': g['members']},
                sort_keys=True, ensure_ascii=False) for g in dependency['groups'])))
            if signature in preferred:
                alternatives[group['id']] = preferred[signature]
            else:
                preferred[signature] = group['id']
    roots = [identity for identity in roots if identity not in alternatives]
    closure = select_fact_dependencies(groups, roots, opcode=definition['opcode'], model=definition['target_model'])
    return {**closure, "opcode": definition['opcode'], "target_model": definition['target_model'],
            "requirements": [{"kind": "instruction", "target": definition['opcode'],
                              "dimension": "definition." + identity, "members": by_id[identity].get('members', []),
                              "fact_status": by_id[identity]['status']} for identity in roots],
            "receipt": {"equivalent_effect_alternatives": alternatives,
                        "source_processed": copy.deepcopy(definition.get('source_materials', [])),
                        "uninterpreted_content": copy.deepcopy(definition.get('uninterpreted_content', [])),
                        "evidenced_fact_ids": [g['id'] for g in groups if g['sources'] and
                                                g['status'] in {'candidate_evidence', 'source_verified'}],
                        "source_checked_fact_ids": [g['id'] for g in groups if g['status'] == 'source_verified'],
                        "selected_fact_ids": [g['id'] for g in closure['groups']],
                        "packed_fact_ids": [], "final_delivered_fact_ids": [],
                        "generation_conformance": "not_checked", "semantic_completeness": "not_established"}}
