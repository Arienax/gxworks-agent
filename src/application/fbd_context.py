"""Task-scoped FBD generation context.

One builder serves the Web entry (`application.gxw_files`) and the MCP entry
(`agent_runtime.generation_tools`) so both see the same engineering facts and
the same reduction choices.

Three rules this module keeps:

* Core FBD language rules are required.  A missing rule resource stops
  generation; it is not reported as an optional gap and there is no switch that
  turns the requirement off.
* Complete selected objects pass to the delivery layer. MCP pages the public
  response within its fixed limits; Web measures the complete provider request
  against the configured model window before sending it.
* A task-scoped catalog is selected from the request, the current graph, the
  declarations and the existing capability discovery.  An unrecognised request
  keeps a small primitive/operator set and an explicit expansion contract; it
  never falls back to the full directory.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

DEFAULT_CATALOG_SCOPE = "task"
CATALOG_SCOPES = ("task", "full")
IMAGE_RESERVE_TOKENS = 1024

# Core rules that must exist before FBD generation is allowed to run.
FBD_RULE_TARGETS = (
    "FBD.PROJECT",
    "FBD.SYMBOLS",
    "FBD.TYPES",
    "FBD.LITERALS",
    "FBD.OPERATORS",
    "FBD.CALL.BINDING",
    "FBD.EXECUTION",
    "FBD.INSTANCE.STATE",
    "FBD.CONTROL_FLOW",
)
# FBD.TIMERS describes FX12 models only; it is read when a timer is selected but
# never blocks a project that does not use one.
OPTIONAL_RULE_TARGETS = ("FBD.TIMERS",)

_TIMER_MARKERS = ("ton", "tof", "tp", "ctu", "ctd", "ctud", "timer")

_PRIMITIVE_KINDS = frozenset({
    "contact", "contact_nc", "contact_rising_negated", "contact_falling_negated",
    "coil", "input", "output", "comment", "jump", "return", "inout",
})

# Small always-useful operator set; entries that this CPU does not offer are
# dropped and reported instead of being invented.
_COMMON_TEMPLATES = (
    "function:MOV", "function:@ADD-2", "function:@SUB", "function:@MUL-2",
    "function:@DIV", "function:@EQ-2", "function:@NE", "function:@GT-2",
    "function:@GE-2", "function:@LT-2", "function:@LE-2", "function:@AND-2",
    "function:@OR-2", "function:@XOR-2", "function:NOT", "function_block:TON",
    "function_block:CTU",
)

_BASIC_TYPES = frozenset({
    "BOOL", "BYTE", "WORD", "DWORD", "LWORD", "SINT", "USINT", "INT", "UINT",
    "DINT", "UDINT", "LINT", "ULINT", "REAL", "LREAL", "TIME", "DATE", "TOD",
    "DT", "STRING", "ANY", "ANY_BIT", "ANY_NUM", "ANY_INT", "ANY_REAL",
    "ANY_ELEMENTARY", "ANY_DERIVED", "VOID",
})

_CALLABLE_PREFIXES = ("function_block:", "function:")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# The existing capability discovery ranks every candidate; only the leading
# ranked callables are engineering-relevant for one task.
DISCOVERY_CALLABLE_LIMIT = 24


class FBDContextError(ValueError):
    """The engineering context cannot be built as requested."""


class FBDContextBudgetError(FBDContextError):
    """Mandatory context alone exceeds the provider input budget."""


class FBDContextRuleError(FBDContextError):
    """A required FBD language rule has no definition for this CPU."""


def json_text(value):
    """Serialize exactly like the request path, so the measurement is not optimistic."""
    return json.dumps(value, ensure_ascii=False)


@dataclass(frozen=True)
class FBDUnit:
    """One indivisible context unit; units are included or removed whole."""

    id: str
    kind: str
    section: str
    value: object
    mandatory: bool = True
    reason: str = ""
    extend: bool = False

    @property
    def priority(self):
        try:
            return _OPTIONAL_PRIORITY.index(self.kind)
        except ValueError:
            return len(_OPTIONAL_PRIORITY)


# Removal order for optional units: least specific engineering detail first.
_OPTIONAL_PRIORITY = (
    "catalog_alternative", "project_gui", "comments", "device_comments",
    "source_graph", "source_text", "structures", "type",
)


def menu_identity(plc_model):
    """Real menu/CPU identity, including merged menu members."""
    name = str(plc_model or "").strip()
    if not name:
        return {"status": "unverified", "reason": "no PLC model was supplied"}
    try:
        from gxworks2.project_profiles import native_project_profile
        profile = native_project_profile(name)
    except Exception as error:  # pragma: no cover - defensive
        return {"status": "unverified", "reason": str(error), "plc_model": name}
    return {
        "status": "available",
        "plc_model": name,
        "cpu": profile.cpu,
        "generation_menu": profile.cpu,
        "merged_menu_members": list(dict.fromkeys(profile.cpu.split("/"))),
    }


def language_rule_report(plc_model, *, targets=None,
                         optional_targets=OPTIONAL_RULE_TARGETS, selected_templates=()):
    """Read every applicable FBD rule; required rules missing means no generation."""
    from plc.fbd_semantics import language_rules

    wanted = tuple(targets or FBD_RULE_TARGETS)
    rules, gaps = {}, []
    for target in wanted:
        try:
            rules[target] = copy.deepcopy(language_rules(target, plc_model))
        except Exception as error:
            finding = dict(getattr(error, "finding", None) or {})
            gaps.append({
                "rule": target,
                "code": finding.get("code") or "fbd_rule_unavailable",
                "severity": finding.get("severity") or "unverified",
                "message": finding.get("message") or str(error),
                "required": True,
            })
    timer_selected = any(any(marker in str(name).casefold() for marker in _TIMER_MARKERS)
                         for name in selected_templates)
    for target in tuple(optional_targets or ()) if timer_selected else ():
        try:
            rules[target] = copy.deepcopy(language_rules(target, plc_model))
        except Exception as error:
            finding = dict(getattr(error, "finding", None) or {})
            gaps.append({
                "rule": target,
                "code": finding.get("code") or "fbd_rule_unavailable",
                "severity": finding.get("severity") or "unverified",
                "message": finding.get("message") or str(error),
                "required": False,
            })
    mandatory = [item for item in gaps if item["required"]]
    report = {
        "plc_model": plc_model,
        "status": "failed" if mandatory else ("verified" if not gaps else "verified_with_optional_gaps"),
        "targets": list(wanted),
        "required_targets": list(wanted),
        "rules": rules,
        "gaps": gaps,
        "mandatory_missing": mandatory,
    }
    if mandatory:
        summary = ", ".join(f"{item['rule']}({item['code']})" for item in mandatory)
        raise FBDContextRuleError(
            f"缺少 {plc_model} 的必需 FBD 语言规则，已停止生成：{summary}。"
            "规则资源缺失表示当前构建无法证明这些语义，不能改用省略规则继续生成。")
    return report


def model_catalog_nodes(nodes):
    """Drop editor geometry; the model does not need pixel layout."""
    result = []
    for node in nodes or ():
        if not isinstance(node, Mapping):
            continue
        clean = {key: copy.deepcopy(value) for key, value in node.items()
                 if key not in ("width", "height", "prototype_offset")}
        ports = clean.get("ports")
        if isinstance(ports, list):
            clean["ports"] = [
                {key: value for key, value in port.items() if key not in ("x", "y")}
                if isinstance(port, Mapping) else port for port in ports
            ]
        result.append(clean)
    return result


def has_graph(model):
    """Whether a saved model carries any object a task must preserve."""
    if not isinstance(model, Mapping):
        return False
    if model.get("preserve_blocks") or model.get("preserve_block_details"):
        return True
    if model.get("nodes") or model.get("wires") or model.get("calls"):
        return True
    for network in model.get("networks") or []:
        if not isinstance(network, Mapping):
            continue
        if network.get("nodes") or network.get("calls") or network.get("wires"):
            return True
    return False


def graph_items(model):
    """Yield (label, carrier, item) for both saved projections.

    The object model keeps flat `nodes`/`wires`; the parameter model groups
    `nodes`, `calls` and `wires` per network.  Callable instances appear as
    `function_block:<Name>` nodes in one and as `calls` entries in the other, so
    every reader needs both carriers.
    """
    if not isinstance(model, Mapping):
        return
    for node in model.get("nodes") or ():
        if isinstance(node, Mapping):
            yield ("graph", "node", node)
    for call in model.get("calls") or ():
        if isinstance(call, Mapping):
            yield ("graph", "call", call)
    for position, network in enumerate(model.get("networks") or ()):
        if not isinstance(network, Mapping):
            continue
        label = network.get("id") or network.get("title") or f"network_{position + 1}"
        for node in network.get("nodes") or ():
            if isinstance(node, Mapping):
                yield (label, "node", node)
        for call in network.get("calls") or ():
            if isinstance(call, Mapping):
                yield (label, "call", call)


def _template_reference(carrier, item):
    """Callable template one graph item refers to, if any."""
    if carrier == "call":
        name = str(item.get("type") or "").strip()
        if not name:
            return ""
        return name if name.casefold().startswith(_CALLABLE_PREFIXES) else "function_block:" + name
    template = str(item.get("template") or "").strip()
    return template if template.casefold().startswith(_CALLABLE_PREFIXES) else ""


def _template_keys(template):
    """Case-insensitive lookup keys for one catalog template."""
    text = str(template or "")
    if not text:
        return set()
    body = text.split(":", 1)[1] if ":" in text else text
    keys = {text.casefold()}
    for name in (body, body.lstrip("@"), re.sub(r"-\d+$", "", body).lstrip("@")):
        if name:
            keys.add(name.casefold())
    return keys


def _catalog_index(nodes):
    by_key, by_template = {}, {}
    for node in nodes or ():
        template = str(node.get("template") or "")
        if not template:
            continue
        by_template.setdefault(template.casefold(), node)
        for key in _template_keys(template):
            by_key.setdefault(key, node)
    return by_key, by_template


def _resolve(name, by_key, by_template):
    text = str(name or "").strip()
    if not text:
        return None
    return (by_template.get(text.casefold())
            or by_key.get(text.casefold())
            or by_key.get(re.sub(r"-\d+$", "", text).casefold()))


def _normalize_templates(templates):
    if templates is None:
        return []
    if isinstance(templates, str):
        raise FBDContextError("templates 必须是模板名数组，不能是单个字符串")
    if not isinstance(templates, Sequence):
        raise FBDContextError("templates 必须是模板名数组")
    names = []
    for item in templates:
        if not isinstance(item, str):
            raise FBDContextError("templates 只能包含模板名字符串")
        name = item.strip()
        if name and name not in names:
            names.append(name)
    return names


def _port_types(node):
    names = []
    for port in node.get("ports") or []:
        if not isinstance(port, Mapping):
            continue
        data_type = str(port.get("data_type") or "").strip()
        if data_type:
            names.append(data_type)
    return names


def _base_type(text):
    from gxw.declarations import _type_parts
    try:
        return _type_parts(text)[0]
    except ValueError:
        return str(text).strip()  # Kept as an explicit unresolved dependency.


def _dependency_types(node, structures):
    """Visit nested member types before deciding which callable is required."""
    known = {str(name).casefold(): rows for name, rows in (structures or {}).items()}
    pending, seen = list(_port_types(node)), set()
    while pending:
        base = _base_type(pending.pop(0))
        if not base or base.casefold() in seen:
            continue
        seen.add(base.casefold())
        yield base
        pending.extend(str(row.get('data_type') or '') for row in known.get(base.casefold(), ())
                       if isinstance(row, Mapping))


def _closure(node_list, by_key, by_template, structures=None):
    """Expand selected nodes with callables their ports reference."""
    included, pending = {}, list(node_list)
    for node in pending:
        included.setdefault(str(node.get("template")), node)
    while pending:
        node = pending.pop(0)
        for base in _dependency_types(node, structures):
            if not base or base.upper() in _BASIC_TYPES or base.upper().startswith("ANY"):
                continue
            match = _resolve("function_block:" + base, by_key, by_template) \
                or _resolve("function:" + base, by_key, by_template)
            if match is None:
                continue
            template = str(match.get("template"))
            if template not in included:
                included[template] = match
                pending.append(match)
    return included


def _referenced_templates(previous):
    """Callable templates the current graph already uses."""
    references = set()
    for _label, carrier, item in graph_items(previous):
        reference = _template_reference(carrier, item)
        if reference:
            references.add(reference)
    return sorted(references)


def _request_names(request):
    """Identifiers the request itself names, keeping order and ignoring digits."""
    names = []
    for match in _IDENTIFIER.finditer(str(request or "")):
        name = match.group(0)
        if len(name) < 2 or name in names:
            continue
        names.append(name)
    return names


def _discovery_names(discovery, *, limit=DISCOVERY_CALLABLE_LIMIT):
    """Ranked callables the existing capability discovery kept for this request."""
    if not isinstance(discovery, Mapping):
        return []
    names = []
    for candidate in discovery.get("candidates") or ():
        if not isinstance(candidate, Mapping):
            continue
        if ('included_candidates' in discovery
                and candidate.get('id') not in discovery['included_candidates']):
            continue
        name = str(candidate.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def _declared_types(declarations, previous):
    seen, names = set(), []
    for rows in (declarations or {}).values():
        for row in rows or ():
            if not isinstance(row, Mapping):
                continue
            data_type = str(row.get("data_type") or "").strip()
            base = _base_type(data_type)
            if base and base.upper() not in _BASIC_TYPES and base not in seen:
                seen.add(base)
                names.append(base)
    for table in ((previous or {}).get("labels") or {}).values():
        rows = table.get("rows") if isinstance(table, Mapping) else table
        for row in rows or ():
            if not isinstance(row, Mapping):
                continue
            data_type = str(row.get("data_type") or "").strip()
            base = _base_type(data_type)
            if base and base.upper() not in _BASIC_TYPES and base not in seen:
                seen.add(base)
                names.append(base)
    return names


def _required_structures(node_list, available):
    """Complete member rows for every composite a selected port needs, nested included."""
    known = {str(name).casefold(): (name, rows) for name, rows in (available or {}).items()}
    required, unresolved, pending = {}, [], list(node_list)
    callable_types = {callable_name(node.get('template')).casefold() for node in node_list
                      if str(node.get('template', '')).casefold().startswith('function_block:')}
    while pending:
        node = pending.pop(0)
        for data_type in _port_types(node):
            base = _base_type(data_type)
            if not base or base.upper() in _BASIC_TYPES or base.upper().startswith("ANY"):
                continue
            entry = known.get(base.casefold())
            if entry is None:
                # A nested FB type is supplied by its complete callable
                # interface, not by the structure table.
                if base.casefold() not in callable_types and base not in unresolved:
                    unresolved.append(base)
                continue
            name, rows = entry
            if name in required:
                continue
            required[name] = copy.deepcopy(rows)
            pending.append({"ports": [{"data_type": str((row or {}).get("data_type") or "")}
                                      for row in rows or ()]})
    return required, sorted(unresolved)


def _structure_closure(required, available):
    """Add members of structures referenced by already-required structures."""
    known = {str(name).casefold(): (name, rows) for name, rows in (available or {}).items()}
    changed = True
    while changed:
        changed = False
        for rows in list(required.values()):
            for row in rows or ():
                base = _base_type(str((row or {}).get("data_type") or ""))
                if not base or base.upper() in _BASIC_TYPES:
                    continue
                entry = known.get(base.casefold())
                if entry and entry[0] not in required:
                    required[entry[0]] = copy.deepcopy(entry[1])
                    changed = True
    return required


def select_task_catalog(catalog, *, scope=DEFAULT_CATALOG_SCOPE, templates=None, request="",
                        previous=None, discovery=None, declarations=None):
    """Select the catalog this task actually needs, with its dependency closure."""
    if scope not in CATALOG_SCOPES:
        raise FBDContextError(f"未知的 catalog_scope：{scope}")
    nodes = [node for node in (catalog or {}).get("nodes") or () if isinstance(node, Mapping)]
    by_key, by_template = _catalog_index(nodes)
    available_structures = (catalog or {}).get("structures") or {}
    requested = _normalize_templates(templates)
    selected, reasons, missing = {}, {}, []
    unresolved = []

    if requested:
        for name in requested:
            node = _resolve(name, by_key, by_template)
            if node is None:
                missing.append({"template": name, "reason": "not in this CPU's FBD template table"})
                continue
            selected[str(node.get("template"))] = node
            reasons[str(node.get("template"))] = "explicitly_requested"
        chosen = _closure(list(selected.values()), by_key, by_template, available_structures)
        for template, node in chosen.items():
            selected.setdefault(template, node)
            reasons.setdefault(template, "port_type_dependency")
        ordering = {str(node.get("template")): index for index, node in enumerate(nodes)}
        ordered = sorted(selected.values(), key=lambda item: ordering.get(str(item.get("template")), 10 ** 6))
        required, unresolved = _required_structures(ordered, available_structures)
        required = _structure_closure(required, available_structures)
        return {"catalog_scope": scope, "nodes": ordered, "selected": reasons,
                "requested_templates": requested, "missing_templates": missing,
                "unresolved_references": [], "structured_types": required, "structure_gaps": unresolved,
                "available_template_count": len(nodes),
                "expansion": {"tool": "get_fbd_catalog",
                              "arguments": {"catalog_scope": "full"},
                              "reason": "request a template table wider than the requested set"}}

    if scope == "full":
        required, unresolved = _required_structures(nodes, available_structures)
        required = _structure_closure(required, available_structures)
        return {"catalog_scope": scope, "nodes": list(nodes),
                "selected": {str(node.get("template")): "full_scope_requested" for node in nodes},
                "requested_templates": [], "missing_templates": [],
                "unresolved_references": [], "structured_types": required, "structure_gaps": unresolved,
                "available_template_count": len(nodes),
                "expansion": {"tool": "get_fbd_catalog", "arguments": {"catalog_scope": "full"},
                              "reason": "full scope was requested explicitly"}}

    def add(node, reason):
        if node is None:
            return
        template = str(node.get("template"))
        if template and template not in selected:
            selected[template] = node
            reasons[template] = reason
        elif reasons.get(template) in {'common_operator', 'capability_discovery'} and reason not in {
                'common_operator', 'capability_discovery'}:
            reasons[template] = reason

    for node in nodes:
        if str(node.get("kind")) in _PRIMITIVE_KINDS:
            add(node, "connection_primitive")
    common_missing = []
    for name in _COMMON_TEMPLATES:
        node = _resolve(name, by_key, by_template)
        if node is None:
            common_missing.append(name)
            continue
        add(node, "common_operator")
    for name in _discovery_names(discovery):
        add(_resolve(name, by_key, by_template), "capability_discovery")
    for template in _referenced_templates(previous):
        node = _resolve(template, by_key, by_template)
        if node is None:
            unresolved.append(template)
            continue
        add(node, "current_graph_reference")
    for name in _request_names(request):
        add(_resolve(name, by_key, by_template), "request_symbol")
    for name in _declared_types(declarations, previous):
        add(_resolve("function_block:" + name, by_key, by_template)
            or _resolve("function:" + name, by_key, by_template), "declared_type")

    chosen = _closure(list(selected.values()), by_key, by_template, available_structures)
    dependencies = set()
    for node in chosen.values():
        for base in _dependency_types(node, available_structures):
            dependency = (_resolve('function_block:' + base, by_key, by_template)
                          or _resolve('function:' + base, by_key, by_template))
            if dependency is not None:
                dependencies.add(str(dependency.get('template')))
    for template, node in chosen.items():
        if template not in selected:
            selected[template] = node
            reasons[template] = "port_type_dependency"
        elif template in dependencies and reasons[template] in {'common_operator', 'capability_discovery'}:
            reasons[template] = "required_port_type_dependency"
    ordering = {str(node.get("template")): index for index, node in enumerate(nodes)}
    ordered = sorted(selected.values(), key=lambda item: ordering.get(str(item.get("template")), 10 ** 6))
    required, structure_gaps = _required_structures(ordered, available_structures)
    required = _structure_closure(required, available_structures)
    return {
        "catalog_scope": scope,
        "nodes": ordered,
        "selected": reasons,
        "optional_templates": [template for template, reason in reasons.items()
                               if reason in {'common_operator', 'capability_discovery'}],
        "requested_templates": [],
        "missing_templates": [],
        "unresolved_references": sorted(set(unresolved)),
        "structured_types": required,
        "available_template_count": len(nodes),
        "common_templates_unavailable": common_missing,
        "structure_gaps": structure_gaps,
        "expansion": {
            "tool": "get_fbd_catalog",
            "arguments": {"catalog_scope": "full"},
            "templates_argument": "pass templates=[...] for a targeted table",
            "reason": ("task scope keeps a small primitive/operator/request set; "
                       "the full directory is never inlined by default"),
        },
    }


def build_payload(units):
    """Assemble the payload from whole units; nothing is ever truncated."""
    payload = {}
    for unit in units:
        if unit.extend:
            payload.setdefault(unit.section, []).extend(copy.deepcopy(unit.value))
        else:
            payload[unit.section] = copy.deepcopy(unit.value)
    return payload


def _compile_messages(*, instructions, get_messages, model_profile, plc_model, request,
                      confirmed_spec, current_program, task_type):
    from application.context_compiler import ContextCompiler, ContextCompilerInput

    signature_seen = {}

    def renderer(runtime, evidence_text, generation_request, program, checkpoint, history):
        messages = get_messages()
        signature_seen["messages"] = copy.deepcopy(messages)
        return messages

    compiled = ContextCompiler().compile(ContextCompilerInput(
        confirmed_spec=confirmed_spec or {},
        plc_model=str(plc_model or ""),
        model_profile=model_profile or {},
        task_type=task_type,
        generation_request=str(request or ""),
        include_generation_request=True,
        current_program=current_program,
        wire_renderer=renderer,
    ))
    return compiled, signature_seen.get("messages") or []


def budget_receipt(compiled, *, image_count=0, dropped=(), rounds=0, basis="fbd_generation_input"):
    """Compiler budget report plus the pruning facts that produced it."""
    report = copy.deepcopy(compiled.budget_report)
    usable = report.get("usable_input_tokens")
    reserve = IMAGE_RESERVE_TOKENS * max(0, int(image_count or 0))
    measured = report.get("estimated_input_tokens")
    exceeded = None
    if usable is not None and measured is not None:
        exceeded = bool(measured + reserve > usable)
    report.update({
        "compiler": "application.context_compiler.ContextCompiler",
        "receipt_basis": basis,
        "image_count": int(image_count or 0),
        "image_reserve_tokens": reserve,
        "image_reserve_is_an_assumption": True,
        "exceeded_including_images": exceeded,
        "fits": None if exceeded is None else not exceeded,
        "dropped_units": [dict(item) for item in dropped],
        "prune_rounds": int(rounds),
        "drops_reason": "whole_unit_removal_no_json_truncation",
        "wire_sha256": (compiled.provenance_receipt or {}).get("wire_sha256"),
        "context_plan_sha256": (compiled.provenance_receipt or {}).get("context_plan_sha256"),
        "context_pressure": (compiled.provenance_receipt or {}).get("context_pressure"),
    })
    return report


def fit_units(units, *, instructions, model_profile=None, image_count=0, plc_model="FX3U",
              request="", confirmed_spec=None, current_program=None, task_type="generate"):
    """Compile with the real provider profile, removing optional units until it fits."""
    active, dropped, rounds = list(units), [], 0
    while True:
        from agent_runtime.runtime import _model_tool_value
        from application.generation_wire import structured_context_text
        payload = _model_tool_value('get_generation_context', {'generation_input': build_payload(active)})['generation_input']

        def get_messages(payload=payload):
            return [{"role": "system", "content": instructions},
                    {"role": "user", "content": structured_context_text(payload)}]

        compiled, _ = _compile_messages(
            instructions=instructions, get_messages=get_messages, model_profile=model_profile,
            plc_model=plc_model, request=request, confirmed_spec=confirmed_spec,
            current_program=current_program, task_type=task_type)
        report = budget_receipt(compiled, image_count=image_count, dropped=dropped, rounds=rounds)
        if report["fits"] is not False:
            return payload, report
        victim = min((unit for unit in active if not unit.mandatory),
                     key=lambda unit: (unit.priority, unit.id), default=None)
        if victim is None:
            raise FBDContextBudgetError(
                "FBD 生成上下文的必需单元已超出模型输入预算："
                f"{report['estimated_input_tokens']} > {report['usable_input_tokens']} tokens，"
                "未截断任何单元。请缩小工程上下文或使用更大窗口的模型。")
        active.remove(victim)
        dropped.append({"id": victim.id, "kind": victim.kind, "section": victim.section,
                        "reason": victim.reason or "optional_unit_over_budget"})
        rounds += 1


def drop_path(envelope, path):
    """Remove one optional envelope path in place and report what was removed."""
    target = envelope
    for key in path[:-1]:
        if not isinstance(target, Mapping) or key not in target:
            return None
        target = target[key]
    if not isinstance(target, dict) or path[-1] not in target:
        return None
    return target.pop(path[-1])


def compile_envelope(envelope, *, instructions, optional_paths=(), model_profile=None,
                     image_count=0, plc_model="FX3U", request="", confirmed_spec=None,
                     current_program=None, task_type="edit", optional_templates=()):
    """Measure the real request envelope through ContextCompiler, dropping whole sections."""
    dropped, rounds = [], 0
    while True:
        def get_messages(envelope=envelope):
            return [{"role": "system", "content": instructions},
                    {"role": "user", "content": json_text(envelope)}]

        compiled, _ = _compile_messages(
            instructions=instructions, get_messages=get_messages, model_profile=model_profile,
            plc_model=plc_model, request=request, confirmed_spec=confirmed_spec,
            current_program=current_program, task_type=task_type)
        report = budget_receipt(compiled, image_count=image_count, dropped=dropped,
                                rounds=rounds, basis="application_request_envelope")
        if report["fits"] is not False:
            return envelope, report
        removed = None
        # The final tool envelope adds schema overhead after the shared context
        # was assembled. Remove only complete auxiliary interfaces here too;
        # referenced/requested calls and every type dependency remain required.
        catalog = ((envelope.get('context') or {}).get('generation_input') or {}).get('catalog', [])
        for template in optional_templates:
            row = next((row for row in catalog if row.get('template') == template), None)
            if row is not None:
                catalog.remove(row)
                dropped.append({'id': 'catalog.' + template, 'kind': 'catalog_alternative',
                                'section': 'catalog', 'reason': 'optional_interface_over_budget'})
                removed = template
                break
        if removed is not None:
            rounds += 1
            continue
        for path in optional_paths:
            if any(tuple(item["path"]) == tuple(path) for item in dropped):
                continue
            if drop_path(envelope, tuple(path)) is not None:
                dropped.append({"id": ".".join(str(part) for part in path), "kind": "section",
                                "section": ".".join(str(part) for part in path),
                                "path": list(path),
                                "reason": "optional_section_over_budget"})
                removed = path
                break
        if removed is None:
            raise FBDContextBudgetError(
                "FBD 请求信封的必需部分已超出模型输入预算："
                f"{report['estimated_input_tokens']} > {report['usable_input_tokens']} tokens，"
                "未截断任何部分。")
        rounds += 1


def call_bindings(previous, catalog):
    """Every callable instance in the current graph, with its real interface.

    The saved projection puts FB/function instances in `network.nodes` as
    `function_block:<Name>` (with the instance symbol on the node) and only some
    of them in `network.calls`, so both carriers are read here.
    """
    if not isinstance(previous, Mapping):
        return []
    by_key, by_template = _catalog_index((catalog or {}).get("nodes") or ())
    rows, seen = [], set()
    connected = {}
    # The saved parameter projection has logical endpoint names even when the
    # companion object model contains only coordinate wires.
    for projection in ((catalog or {}).get('parameter_model'), previous):
        if not isinstance(projection, Mapping):
            continue
        for network in [projection, *(projection.get('networks') or [])]:
            nodes = {item.get('id'): item for item in network.get('nodes') or ()}
            for wire in network.get('wires') or ():
                if not isinstance(wire.get('from'), str) or not isinstance(wire.get('to'), str):
                    continue
                left, _, left_port = wire['from'].partition('.')
                right, _, right_port = wire['to'].partition('.')
                for call_id, formal, other_id, side in ((right, right_port, left, 'in'), (left, left_port, right, 'out')):
                    call, other = nodes.get(call_id, {}), nodes.get(other_id, {})
                    if not _template_reference('node', call):
                        continue
                    terminal = other.get('template') in ('input', 'output', 'inout')
                    binding = {'name': formal, 'side': side, 'value': other.get('symbol') if terminal else None,
                               'endpoint': wire['from'] if side == 'in' else wire['to']}
                    bucket = connected.setdefault(call_id, [])
                    if binding not in bucket:
                        bucket.append(binding)

    def interface(template):
        definition = _resolve(template, by_key, by_template) or {}
        ports = copy.deepcopy(definition.get("ports"))
        return ports, ("available" if ports else "unavailable")

    for label, carrier, item in graph_items(previous):
        template = _template_reference(carrier, item)
        if not template:
            continue
        instance = str(item.get("symbol") or item.get("instance") or "").strip()
        # Repeated calls of one FB instance are separate ordered operations.
        # A caller passes one graph projection, so only repeated record IDs dedupe.
        identity = (label, carrier, str(item.get("id")), template)
        if identity in seen:
            continue
        seen.add(identity)
        ports, status = interface(template)
        if carrier == "node":
            arguments = connected.get(item.get('id')) or [{"name": port.get("name"), "data_type": port.get("data_type"),
                          "value": port.get("value")}
                         for port in item.get("ports") or () if isinstance(port, Mapping)]
            if item.get('arguments'):
                arguments = [{'name': name, 'value': value} for name, value in item['arguments'].items()]
        else:
            arguments = [{"name": key, "value": value}
                         for key, value in (item.get("arguments") or {}).items()]
        rows.append({
            "network": label, "carrier": carrier, "id": item.get("id"),
            "template": template, "instance": item.get("symbol") or item.get("instance"),
            "arguments": arguments, "interface_status": status, "interface": ports,
        })
    return rows


def callable_name(reference):
    """The definition name inside a callable template reference, or ''."""
    text = str(reference or "").strip()
    if text.casefold().startswith(_CALLABLE_PREFIXES) and ":" in text:
        return text.split(":", 1)[1].strip()
    return ""


def _model_callables(model):
    """Callable definition references used by a graph, in either projection."""
    found = set()
    for _label, carrier, item in graph_items(model):
        reference = _template_reference(carrier, item)
        if callable_name(reference):
            found.add(reference)
    return found


def _definition_rows(rows):
    """Every declared row of one source, in declaration order, without truncation."""
    result = []
    for row in rows or ():
        if isinstance(row, Mapping):
            result.append({key: copy.deepcopy(row.get(key)) for key in
                           ("name", "data_type", "class_name", "class", "initial_value", "device",
                            "iec_address", "comment")
                           if key in row})
        else:
            result.append({"name": getattr(row, "name", None),
                           "data_type": getattr(row, "data_type", None)})
    return result


def _graph_view(*models):
    """One readable graph view over the object model and the parameter model."""
    view = {"nodes": [], "networks": [], "labels": {}}
    for model in models:
        if not isinstance(model, Mapping):
            continue
        view["nodes"].extend(item for item in model.get("nodes") or () if isinstance(item, Mapping))
        view["networks"].extend(item for item in model.get("networks") or ()
                                if isinstance(item, Mapping))
        for name, table in (model.get("labels") or {}).items():
            view["labels"].setdefault(name, table)
    return view


def _name_pattern(name):
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", re.IGNORECASE)


def _mentions(text, names):
    """Names a body/declaration text actually references."""
    found = set()
    for name in names:
        if name and _name_pattern(name).search(text):
            found.add(name)
    return found


def source_program_context(baseline, *, selected_program=None, request="", templates=()):
    """Read complete related bodies using the same bound call graph as checking."""
    if baseline is None:
        return {"status": "unavailable", "reason": "no saved project baseline for this request"}
    try:
        from copy import copy as shallow_copy
        from gxw.st_program import project_context_snapshot
        from gxw.fb_authoring import _context
        from gxw.offline_authoring import _st_dependencies
        from gxw.models import GXWFormatError
        snapshot = project_context_snapshot(baseline, selected_program)
        _, _, groups, sources, _ = _context(baseline, require_fbd=False)
    except (ValueError, OSError) as error:
        return {"status": "unavailable", "reason": str(error)}
    pous = snapshot.get("pous") or []
    by_source = {row["program"]: row for row in pous}
    selected = snapshot.get("selected_source") or next(
        (row["program"] for row in pous if row.get("language") == "fbd"), None)
    owners = {group["labels"]: logical for logical, group in groups.items()}
    dependencies = _st_dependencies(snapshot, sources)
    issues = list(snapshot.get("issues") or [])
    for row in pous:
        if row.get("language") != "fbd":
            continue
        current_sources = shallow_copy(sources)
        current_sources.program_name = row["program"]
        for reference in _model_callables(row.get("model")):
            name = callable_name(reference)
            if name.startswith('@'):
                continue  # Serialized operators are not project/library POU calls.
            try:
                stream, definition = current_sources.definition(name,
                    "FUNCTION_BLOCK" if reference.casefold().startswith("function_block:") else "FUNCTION")
            except GXWFormatError as error:
                issues.append({"code": "fbd_dependency_binding", "source_name": row["program"],
                               "template": reference, "message": str(error)})
                continue
            target = definition.get("source_program", owners.get(stream))
            if target in by_source:
                dependencies.setdefault(row["program"], set()).add(target)
    requested = set(_request_names(request))
    selected_names = {callable_name(str(template)).casefold() for template in templates or ()}
    involved = {selected} if selected else set()
    for row in pous:
        name = str(row.get("name") or "")
        if name.casefold() in selected_names or name and _name_pattern(name).search(str(request)):
            involved.add(row["program"])
    # Forward dependencies supply nested implementations; reverse dependencies
    # supply every affected caller, including callers that are themselves FBs.
    while True:
        expanded = involved | {target for name in involved for target in dependencies.get(name, ())}
        expanded.update(name for name, targets in dependencies.items() if targets & involved)
        expanded.intersection_update(by_source)
        if expanded == involved:
            break
        involved = expanded
    inventory, st_sources, callers, declarations = [], [], [], {}
    for row in pous:
        logical = row["program"]
        inventory.append({"source_name": logical, "source_id": row.get("labels"),
            "name": row.get("name"), "kind": row.get("kind"), "language": row.get("language"),
            "source_status": row.get("source_status"), "interface_status": row.get("interface_status"),
            "task_bindings": copy.deepcopy(row.get("task_bindings") or [])})
        if logical not in involved:
            continue
        declaration_rows = _definition_rows(row.get("declarations"))
        declarations[logical] = declaration_rows
        if row.get("language") == "st":
            st_sources.append({"source_name": logical, "source_id": row.get("labels"),
                "name": row.get("name"), "kind": row.get("kind"),
                "role": "definition" if row.get("kind") in ("function_block", "function") else "st_caller",
                "return_type": row.get("return_type") or "", "interface_status": row.get("interface_status"),
                "interface": copy.deepcopy(row.get("interface") or []),
                "task_bindings": copy.deepcopy(row.get("task_bindings") or []),
                "declarations": declaration_rows, "st_text": row.get("st_text"),
                "source_status": row.get("source_status")})
        elif row.get("language") == "fbd":
            from gxw.ladder_lowering import fbd_topology_from_context
            from gxw.object_model import read_project_context, source_checks
            entry = {"source_name": logical, "source_id": row.get("labels"), "language": "fbd",
                "calls": sorted(_model_callables(row.get("model"))), "model_status": "unavailable",
                "is_selected_source": logical == selected}
            try:
                context = read_project_context(baseline, logical)
                entry.update(model_status="available", parameter_model=fbd_topology_from_context(context))
                checks = source_checks(context)
                entry['source_checks'] = {key: checks[key] for key in ('status', 'findings', 'execution')}
            except (ValueError, OSError) as error:
                entry["model_diagnostic"] = str(error)
                issues.append({"code": "fbd_caller_model_unavailable", "source_name": logical, "message": str(error)})
            callers.append(entry)
    omitted = [{"source_name": row["program"], "source_id": row.get("labels"),
                "reason": "outside the current source and bound call dependency closure"}
               for row in pous if row.get("language") == "st" and row["program"] not in involved]
    editable = [row["source_name"] for row in inventory if row.get("source_status") in ("available", "interface_only")]
    return {"status": "available", "cpu": snapshot.get("cpu"), "selected_source": selected,
        "relevance": {"request_names": sorted(requested), "selected_callables": sorted(selected_names),
            "current_graph_calls": sorted(_model_callables(by_source.get(selected, {}).get("model"))),
            "involved_definitions": [row["name"] for row in pous if row["program"] in involved
                                     and row.get("kind") in ("function_block", "function")]},
        "pous": inventory, "st_sources": st_sources, "fbd_callers": callers,
        "declarations": declarations, "omitted_st_sources": omitted,
        "structures": copy.deepcopy(snapshot.get("structures") or {}), "gaps": issues,
        "transaction": {"tool": "edit_gxw_project", "editable_sources": editable,
            "affected_sources": sorted(involved), "one_transaction": True,
            "note": "ST 接口和正文与所有受影响 FBD 调用者在同一事务合并后检查，再保存。"},
        "native": {"compilation": "not_run", "execution": "not_run"}}


def _supported_paths(catalog, source):
    capabilities = ((catalog or {}).get("selected_source") or {}).get("capabilities") or {}
    edit = dict(capabilities.get("source_edit") or {})
    editable = (source or {}).get("transaction", {}).get("editable_sources") or []
    # A fresh project has a blank CPU template but no saved source, so nothing
    # existing can be edited or joined into one transaction yet.
    has_saved_source = (source or {}).get("status") == "available" and bool(editable)
    return {
        "edit_existing_source": {
            "supported": has_saved_source and edit.get("status") == "available",
            "status": edit.get("status") or "unverified",
            "operations": list(edit.get("operations") or ()),
            "scope": edit.get("scope"),
            "reason": None if has_saved_source else "本次请求没有可编辑的已保存源码",
        },
        "joint_multi_source_transaction": {
            "supported": has_saved_source, "tool": "edit_gxw_project",
            "editable_sources": editable,
            "save_policy": ["draft", "checked"],
            "reason": None if has_saved_source else
                      "ST 接口/正文与 FBD 调用者的联合事务需要已保存版本",
        },
        "create_pou": {
            "supported": False,
            "reason": "离线写作只改既有 POU 源码；新建 POU 需要原生会话",
        },
        "library_import": {
            "supported": has_saved_source, "tool": "create_fbd_candidate", "operation": "import_library",
            "requires_native": True, "source_transfer_languages": ["st"],
            "fbd_source_transfer": "unavailable",
            "reason": "原生库导入只转移已覆盖的 ST 定义、接口、结构和库全局表；FBD 接口可读不代表可转移正文",
        },
        "ladder_fbd_conversion": {
            "supported": False,
            "core_subset": {"cpu": "FX3U", "symbols": ["NO", "NC", "COIL"], "native_required": False},
            "reason": "已有离线转换只覆盖 FX3U 的 NO/NC 触点和普通 COIL 子集，不能据模板型号目录推广",
        },
        "native_compilation": {"supported": False, "status": "not_run",
                               "reason": "生成不编译，需单独原生检查"},
        "execution_feedback": {"supported": False, "status": "not_run",
                               "reason": "生成不读取 PLC/仿真反馈"},
    }


def _unknown_boundaries(catalog, rules):
    options = (catalog or {}).get("compile_options") or {}
    boundaries = []
    if not options.get("values"):
        boundaries.append({"code": "compile_option_array_unavailable",
                           "message": "编译选项字未读出，除 global_variable_hiding 外未解释"})
    else:
        boundaries.append({"code": "compile_option_words_unverified",
                           "message": "编译选项字仅解释 global_variable_hiding，其余位未解码"})
    boundaries.extend([
        {"code": "fb_option_bits_unverified",
         "message": "FB 宏代码/MC·MCR 位影响实例状态，当前未解码为字段"},
        {"code": "native_compilation_not_run",
         "message": "本次生成未做原生编译，编译与保存结果需单独验证"},
        {"code": "plc_execution_not_run",
         "message": "生成不推断 PLC 执行结果，运行行为需另行取证"},
    ])
    for gap in (rules or {}).get("gaps") or ():
        boundaries.append({"code": "language_rule_optional_gap:" + str(gap.get("rule")),
                           "message": str(gap.get("message"))})
    return boundaries


def _context_gaps(catalog, source, selection):
    gaps = []
    if (catalog or {}).get("structures") is None:
        gaps.append({"code": "structure_member_rows_unavailable",
                     "message": "工程复合类型成员未读出，结构体成员需另行确认"})
    if (catalog or {}).get("project_sources_status") not in (None, "available"):
        gaps.append({"code": "project_sources_unavailable",
                     "message": str((catalog or {}).get("project_sources_error"))})
    for name in selection.get("unresolved_references") or ():
        gaps.append({"code": "graph_reference_missing",
                     "message": f"当前图形引用的模板不在本 CPU 目录中：{name}"})
    for name in selection.get("structure_gaps") or ():
        gaps.append({"code": "structure_members_missing",
                     "message": f"缺少复合类型 {name} 的成员定义"})
    for gap in (source or {}).get("gaps") or ():
        gaps.append(dict(gap))
    return gaps


def _scope_paragraph(selection, rules, paths=None):
    paths = paths or {}
    lines = [
        "## 本次目录范围",
        f"- 目录按请求收窄（scope={selection['catalog_scope']}），可用模板 {selection['available_template_count']} 个，"
        "最终提供的完整接口以 `catalog` 为准；预算不足时先移除辅助候选。",
        "- 只使用本次提供的模板；缺少模板时先按 `catalog_contract` 用 "
        "`get_fbd_catalog` 的 `templates` 补取（MCP），Web 端请在答复中报告缺少的模板，"
        "不要凭记忆补写模板或假设全目录可用。",
        "- 引用数据与输入输出的连线端口一律以随附接口为准；嵌套 FB/复合类型依赖已按端口类型展开。",
        f"- 必需语言规则：{', '.join(rules.get('required_targets') or ())}（已读取）。",
        "- `call_interface`、`structures`、`source_context`、`project_options` 与 `call_bindings` "
        "是必需单元：被选函数的完整接口、所需类型（含嵌套成员）、受影响 ST 正文与 FBD 调用者、"
        "工程条件都不会因预算被移除；`source_context.omitted_st_sources` 只是与本请求无关的正文，"
        "不是截断。",
        "- `call_bindings` 给出既有实例的载体（`node` 或 `call`）、实例名、实参与真实接口；"
        "改动实例必须与随附接口一致。",
    ]
    joint = (paths.get("joint_multi_source_transaction") or {})
    if joint.get("supported"):
        lines.append("- 可以返回 `edit_gxw_project`：在一次提交中同时修改 ST 接口/正文与全部受影响 "
                     "FBD 调用者（" + ", ".join(joint.get("editable_sources") or ()) + "）。")
    else:
        lines.append("- 本次没有可编辑的已保存源码，只返回 {\"summary\",\"model\"} 的新图形；"
                     "跨源码事务需要已保存版本入口。")
    return "\n".join(lines)


@dataclass
class ComposedFBDContext:
    instructions: str
    payload: dict
    wire_messages: list = field(default_factory=list)
    budget_report: dict = field(default_factory=dict)
    catalog_report: dict = field(default_factory=dict)
    language_rules: dict = field(default_factory=dict)
    omitted_units: list = field(default_factory=list)
    context_receipt: dict = field(default_factory=dict)
    source_context: dict = field(default_factory=dict)


def compose_fbd_context(*, request, plc_model, catalog, confirmed_spec=None, previous=None,
                        instances_model=None,
                        discovery=None, model_profile=None, catalog_scope=None,
                        templates=None, instructions_extra="", images=(), image_count=None,
                        knowledge_units=(), knowledge_coverage=None,
                        device_comments=None,
                        comment_tables=None, source_baseline=None, source_context=None,
                        task_type="generate"):
    """Build the one FBD generation context used by every entry point."""
    from gxw.generation_contract import FBD_GENERATION_PROMPT

    if catalog is None:
        raise FBDContextError("缺少 FBD 目录，无法构造生成上下文")
    instructions_extra = str(instructions_extra or "")
    model_profile = model_profile or {}
    scope = catalog_scope or catalog.get("catalog_scope") or DEFAULT_CATALOG_SCOPE
    source = (copy.deepcopy(source_context) if source_context is not None else
              source_program_context(source_baseline, selected_program=catalog.get("program"), request=request)
              if source_baseline is not None else {"status": "unavailable",
                                                   "reason": "本次请求未绑定已保存工程"})
    source_graphs = [row.get('parameter_model') for row in source.get('fbd_callers', ())]
    selection = select_task_catalog(
        catalog, scope=scope, templates=templates, request=request,
        previous=_graph_view(previous, instances_model, *source_graphs), discovery=discovery,
        declarations=(source or {}).get("declarations"))
    if source.get("status") == "available":
        # The selection can add a project callable the first pass could not know
        # about; its interface, body and callers then join the same closure.
        known = {str(name).casefold()
                 for name in (source.get("relevance") or {}).get("involved_definitions") or ()}
        selected_names = {str(node.get("template")).split(":", 1)[1].casefold()
                          for node in selection["nodes"] if ":" in str(node.get("template"))}
        definitions = {str(row.get('name')).casefold() for row in source.get('pous', ())
                       if row.get('kind') in ('function_block', 'function')}
        missing = (selected_names & definitions) - known
        if missing:
            source = source_program_context(
                source_baseline, selected_program=catalog.get("program"), request=request,
                templates=[f"function_block:{name}" for name in sorted(missing)])
            source_graphs = [row.get('parameter_model') for row in source.get('fbd_callers', ())]
            selection = select_task_catalog(catalog, scope=scope, templates=templates, request=request,
                previous=_graph_view(previous, instances_model, *source_graphs), discovery=discovery,
                declarations=source.get('declarations'))
    rules = language_rule_report(
        plc_model, selected_templates=[str(node.get("template")) for node in selection["nodes"]])
    menu = menu_identity(plc_model)
    from plc.device_profiles import device_address_rules
    from shared.model_context import technical_value
    address_rules = technical_value(device_address_rules(plc_model))
    if address_rules['status'] != 'available':
        raise FBDContextRuleError(f'缺少 {plc_model} 的地址编号规则，无法生成 FBD')
    catalog_nodes = model_catalog_nodes(selection["nodes"])
    required_structures = selection.get("structured_types") or {}
    graph_model = _graph_view(previous, instances_model)
    current = previous if has_graph(graph_model) else None
    call_interface = copy.deepcopy(
        (source or {}).get("declarations") or {}) if source.get("status") == "available" else {}

    supported_paths = _supported_paths(catalog, source)
    instructions = "\n\n".join(part for part in (
        FBD_GENERATION_PROMPT,
        _scope_paragraph(selection, rules, supported_paths)) if part)

    common = {
        "schema_version": 2,
        "cpu": plc_model,
        "program": catalog.get("program"),
        "target": {
            "plc_model": plc_model,
            "cpu_menu": menu,
            "project_mode": catalog.get("project_mode"),
            "graphical_language_mode": catalog.get("graphical_language_mode"),
            "graphical_language_mode_status": catalog.get("graphical_language_mode_status"),
            "program_mode": (catalog.get("selected_source") or {}).get("unit_kind"),
            "programs": catalog.get("programs"),
        },
        "language_rules": rules,
        "catalog_scope": selection["catalog_scope"],
        "catalog": catalog_nodes,
        "catalog_contract": selection.get("expansion"),
        "parameter_schema": catalog.get("parameter_schema"),
        "declaration_tables": catalog.get("declaration_tables"),
        "existing_declarations": (current or {}).get("labels"),
        "call_interface": call_interface,
        "capabilities": (catalog.get("selected_source") or {}).get("capabilities"),
        "supported_paths": supported_paths,
        "task_scopes": catalog.get("task_scopes"),
        "project_options": {
            "compile_options": catalog.get("compile_options"),
            "configuration": catalog.get("project_configuration"),
        },
        "context_gaps": _context_gaps(catalog, source, selection),
        "unknown_boundaries": _unknown_boundaries(catalog, rules),
        "request": str(request or ""),
        "confirmed_spec": copy.deepcopy(confirmed_spec),
        "call_bindings": call_bindings(instances_model if has_graph(instances_model) else current, catalog),
        "source_context": source,
        "previous": current,
    }
    units = [
        FBDUnit("knowledge", "facts", "knowledge", list(knowledge_units)),
        FBDUnit("knowledge_coverage", "coverage", "knowledge_coverage", knowledge_coverage),
        FBDUnit("schema", "schema", "schema_version", common["schema_version"]),
        FBDUnit("request", "request", "request", common["request"]),
        FBDUnit("target", "target", "target", common["target"]),
        FBDUnit("device_address_rules", "rules", "device_address_rules", address_rules),
        FBDUnit("language_rules", "rules", "language_rules", common["language_rules"]),
        FBDUnit("catalog_scope", "catalog", "catalog_scope", common["catalog_scope"]),
        FBDUnit("catalog_contract", "catalog", "catalog_contract", common["catalog_contract"]),
        FBDUnit("capabilities", "capabilities", "capabilities", common["capabilities"]),
        FBDUnit("supported_paths", "capabilities", "supported_paths", common["supported_paths"]),
        FBDUnit("task_scopes", "task_scopes", "task_scopes", common["task_scopes"]),
        FBDUnit("declaration_tables", "declarations", "declaration_tables",
                common["declaration_tables"]),
        FBDUnit("unknown_boundaries", "boundaries", "unknown_boundaries",
                common["unknown_boundaries"]),
        FBDUnit("context_gaps", "gaps", "context_gaps", common["context_gaps"]),
        FBDUnit("catalog", "catalog", "catalog", []),
        FBDUnit("parameter_schema", "schema", "parameter_schema", common["parameter_schema"]),
        FBDUnit("confirmed_spec", "spec", "confirmed_spec", common["confirmed_spec"]),
        FBDUnit("cpu", "target", "cpu", common["cpu"]),
        FBDUnit("program", "target", "program", common["program"]),
        FBDUnit("existing_declarations", "declarations", "existing_declarations",
                common["existing_declarations"]),
        FBDUnit("previous", "baseline", "previous", common["previous"],
                reason="既有图形必须随请求保留；不存在时为 null"),
        FBDUnit("call_bindings", "type", "call_bindings", common["call_bindings"],
                reason="既有 FB/函数实例的接口与实参必须与图形一起保留"),
        FBDUnit("call_interface", "type", "call_interface", common["call_interface"],
                reason="被调 FB/函数的完整接口与受影响 ST 声明不可裁剪"),
        FBDUnit("structures", "structures", "structures", required_structures,
                reason="被选端口所需的完整类型成员（含嵌套）不可裁剪"),
        FBDUnit("source_context", "source_graph", "source_context", common["source_context"],
                reason="受影响 ST 正文、接口、FBD 调用者与可编辑源码身份不可裁剪"),
        FBDUnit("project_options", "project_gui", "project_options", common["project_options"],
                reason="工程条件（编译选项/GUI 映射）不可裁剪"),
    ]
    optional_templates = set(selection.get('optional_templates') or ())
    for node in catalog_nodes:
        template = node['template']
        optional = template in optional_templates
        units.append(FBDUnit('catalog.' + template, 'catalog_alternative' if optional else 'catalog',
            'catalog', [node], mandatory=not optional, extend=True,
            reason='辅助调用按完整接口移出' if optional else '当前使用、明确请求或依赖的完整接口不可裁剪'))
    if device_comments:
        units.append(FBDUnit("device_comments", "device_comments", "device_comments",
                             copy.deepcopy(device_comments), mandatory=False,
                             reason="软元件注释按预算移出"))
    if comment_tables:
        units.append(FBDUnit("comment_tables", "comments", "comment_tables",
                             copy.deepcopy(comment_tables), mandatory=False,
                             reason="注释表按预算移出"))

    payload, report = fit_units(
        units, instructions=instructions, model_profile=model_profile,
        image_count=len(images or ()) if image_count is None else int(image_count or 0),
        plc_model=plc_model, request=request,
        confirmed_spec=confirmed_spec, current_program=current, task_type=task_type)
    from application.generation_wire import structured_context_text
    wire = [{"role": "system", "content": instructions},
            {"role": "user", "content": structured_context_text(payload)}]
    catalog_report = {key: value for key, value in selection.items() if key != "nodes"}
    delivered = {row['template'] for row in payload['catalog']}
    catalog_report['selected'] = {key: value for key, value in selection['selected'].items() if key in delivered}
    catalog_report['optional_templates'] = [key for key in selection.get('optional_templates', ()) if key in delivered]
    catalog_report.update({"template_count": len(delivered),
                           "available_template_count": selection["available_template_count"]})
    return ComposedFBDContext(
        instructions=instructions, payload=payload, wire_messages=wire,
        budget_report=report, catalog_report=catalog_report, language_rules=rules,
        omitted_units=list(report.get("dropped_units") or ()),
        context_receipt={
            "wire_sha256": report.get("wire_sha256"),
            "context_plan_sha256": report.get("context_plan_sha256"),
            "budget_state": report.get("budget_state"),
            "fits": report.get("fits"),
            "catalog_scope": selection["catalog_scope"],
            "template_count": len(delivered),
            "language_rules_status": rules.get("status"),
        },
        source_context=source)


def handoff_entry(composed):
    """Receipt section shared by the Web and MCP generation entries."""
    return {
        "budget_report": composed.budget_report,
        "catalog_report": composed.catalog_report,
        "language_rules": {key: value for key, value in composed.language_rules.items()
                           if key != "rules"},
        "omitted_units": composed.omitted_units,
        "context_receipt": composed.context_receipt,
    }
