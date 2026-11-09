"""Conservative checks of approved implementation locations against current sources.

These are static source checks, never native compile or PLC execution results.
Free-form planning prose remains unverified; names and comments cannot establish
stop priority, state isolation, or SFC execution semantics.
"""
from __future__ import annotations

from collections.abc import Mapping


def check_source_plan(spec, *, language, source=None, model=None, baseline=None,
                      declarations=None, callable_sources=None):
    """Compare current ST text or existing Ladder/SFC models with approved rows.

    ``declarations`` is the existing ST project_context (pous/globals/selected_source),
    not a second declaration format. SFC locations bind ``transition:<number>`` or
    ``child:<stored-name>``. Ladder locations bind ``network:<id>``/``rung:<id>``.
    Missing/ambiguous bindings produce warnings, not rejection. ``baseline`` must
    be the caller's same-program SFC model before the requested edit.
    """
    rows = [row for row in (spec or {}).get("implementation_items", [])
            if isinstance(row, Mapping) and row.get("language") == language]
    issues, checks = [], []

    def record(row, code, message, *, violated=False, verified=False, evidence=None):
        result = dict(implementation_id=row.get("id"), code=code, message=message,
                      path="$.implementation_items", status="violated" if violated else "verified" if verified else "unverified")
        if evidence is not None:
            result["evidence"] = evidence
        checks.append(result)
        if not verified:
            issues.append({**result, "severity": "error" if violated else "warning"})

    if not rows:
        return {"status": "unverified", "issues": [], "checks": [], "scope": "static_source"}
    if language == "st":
        _st(rows, source, declarations, callable_sources, record)
    elif language == "ladder":
        _ladder(rows, model, spec, record)
    elif language == "sfc":
        _sfc(rows, model, baseline, record)
    else:
        for row in rows:
            record(row, "source_plan_language_unverified", "此语言的源码规划核对未覆盖")
    status = ("violated" if any(row["status"] == "violated" for row in checks) else
              "unverified" if any(row["status"] == "unverified" for row in checks) else "verified")
    return {"status": status, "issues": issues, "checks": checks, "scope": "static_source"}


def _walk(value):
    from plc.st_frontend import Node
    if isinstance(value, Node):
        yield value
    if isinstance(value, (tuple, list)):
        for child in value:
            yield from _walk(child)


def _st(rows, source, context, callable_sources, record):
    from plc.st_frontend import parse_source, STParseError
    from plc.st_semantics import Symbols
    if not isinstance(source, str):
        for row in rows:
            record(row, "source_plan_st_missing", "缺少当前 ST 源码，需进一步验证")
        return
    try:
        parsed = parse_source(source)
        units = {unit.name.upper(): unit for unit in parsed.units}
        symbols = [Symbols(unit, parsed=parsed, project_context=context,
                           callable_sources=callable_sources) for unit in parsed.units]
    except (STParseError, ValueError) as error:
        for row in rows:
            record(row, "source_plan_st_uncovered", "当前 ST 构造尚不能完成规划核对", evidence=str(error))
        return
    # Symbols already binds native body-only sources to their stored POU kind,
    # return slot, declarations and imported callable definitions.
    for table in symbols:
        units[table.unit.name.upper()] = table.unit
        units.update(table.pous)
    for row in rows:
        location = str(row.get("location") or "").strip()
        parts = location.split(None, 1)
        promised = parts[0].lower() if len(parts) == 2 and parts[0].lower() in {"function", "function_block", "program"} else None
        name = (parts[1] if promised else location).upper()
        unit = units.get(name)
        if unit is None and len(parsed.units) == 1 and parsed.units[0].name == "__BODY__" and not context:
            record(row, "source_plan_st_location_unbound", "ST 仅含程序体，未绑定工程中的 POU 名称", evidence=location)
            continue
        if unit is None:
            # The provided source can be one member of a larger project.
            record(row, "source_plan_st_location_unbound", "规划位置未在本次提供的 ST 源码或工程上下文中找到", evidence=location)
            continue
        if promised and unit.kind != promised:
            record(row, "source_plan_st_pou_kind", "实际 POU 类型与确认的分工不符", violated=True,
                   evidence={"location": location, "actual_kind": unit.kind})
        else:
            record(row, "source_plan_st_location", "已绑定当前 POU 的名称和类型", verified=True,
                   evidence={"name": unit.name, "kind": unit.kind})
        table = next((table for table in symbols if table.unit.name.upper() == unit.name.upper()), None)
        table = table or Symbols(unit, parsed=parsed, project_context=context, callable_sources=callable_sources)
        expected = row.get("instances") or []
        if isinstance(expected, list) and expected:
            _instances(row, expected, unit, table, record)
        method = str(row.get("method") or "").lower()
        pure = method.strip() in {"stateless", "stateless_function", "pure_function", "无状态 function", "无状态函数"}
        if pure:
            _stateless(row, unit, table, record)
        elif not expected:
            record(row, "source_plan_st_method_unverified", "仅核对了 POU 位置；自由文字描述的实现语义仍待验证")


def _instances(row, expected, unit, table, record):
    if len({str(name).upper() for name in expected}) != len(expected):
        record(row, "source_plan_st_instance_alias", "确认的独立实例使用了重复名称", violated=True, evidence=expected)
        return
    calls = {node[1].upper() for node in _walk(unit.body) if node.kind == "call"}
    declarations = {name: value for name, value in {**table.globals, **table.locals}.items()}
    for name in expected:
        declaration = declarations.get(str(name).upper())
        if declaration is None:
            record(row, "source_plan_st_instance_missing", "当前 POU 未声明确认的独立实例", violated=True, evidence=name)
            continue
        target, _ = table.callable(str(name))
        if target is None:
            record(row, "source_plan_st_instance_type_unverified", "实例的 FB 类型无法绑定", evidence=name)
        elif target.kind != "function_block":
            record(row, "source_plan_st_instance_type", "确认的状态实例实际不是 FUNCTION_BLOCK", violated=True,
                   evidence={"instance": name, "kind": target.kind})
        elif str(name).upper() not in calls:
            record(row, "source_plan_st_instance_not_called", "已声明实例，但当前源码未调用该实例", violated=True, evidence=name)
        else:
            record(row, "source_plan_st_instance", "已绑定独立 FB 声明和调用；实例内部及外部状态隔离仍待验证", verified=True,
                   evidence={"instance": name, "type": declaration.data_type})
    # Absolute bindings can make distinct names share the same retained state.
    bound = [(name, declarations.get(str(name).upper())) for name in expected]
    addresses = {}
    for name, declaration in bound:
        if declaration and declaration.address:
            key = declaration.address.upper()
            if key in addresses:
                record(row, "source_plan_st_instance_storage_alias", "独立实例绑定到同一绝对存储地址", violated=True,
                       evidence={"instances": [addresses[key], name], "address": key})
            addresses[key] = name
    record(row, "source_plan_st_instance_state_unverified", "独立名称和调用不能证明 FB 内部及外部状态完全隔离")


def _stateless(row, unit, table, record):
    if unit.kind != "function":
        record(row, "source_plan_st_stateless_kind", "无状态 FUNCTION 分工实际由其他 POU 类型实现", violated=True,
               evidence={"name": unit.name, "kind": unit.kind})
        return
    violations, unresolved = [], []
    for declaration in unit.declarations:
        if "RETAIN" in declaration.class_name:
            violations.append({"declaration": declaration.name, "class": declaration.class_name})
    for node in _walk(unit.body):
        if node.kind == "assign":
            target = node[1]
            if target.kind != "atom":
                unresolved.append("non-scalar assignment")
                continue
            name = target[1].split(".", 1)[0]
            declaration = table.lookup(name)
            if declaration is None:
                # A direct-device write or unresolved target is a visible side
                # effect only when Core can bind it; unknown names stay unknown.
                from plc.device_identity import reference_device_parts
                try:
                    device = reference_device_parts(name)
                except (ValueError, TypeError):
                    device = None
                if device is not None:
                    violations.append({"write": name})
                else:
                    unresolved.append(name)
            elif name.upper() in table.globals or declaration.class_name in {"VAR_IN_OUT", "VAR_EXTERNAL"} or declaration.address:
                violations.append({"write": name, "class": declaration.class_name})
        elif node.kind == "call":
            target, instance = table.callable(node[1])
            if target and target.kind == "function_block":
                violations.append({"call": node[1], "kind": target.kind})
            else:
                # Do not recursively infer purity from a signature or opaque
                # standard/library implementation.
                unresolved.append("call:" + node[1])
    if violations:
        record(row, "source_plan_st_stateless_side_effect", "确认的无状态 FUNCTION 包含可见持久状态或外部写入", violated=True, evidence=violations)
    elif unresolved:
        record(row, "source_plan_st_stateless_unverified", "函数包含尚未绑定的目标或未证明无副作用的调用", evidence=unresolved)
    else:
        record(row, "source_plan_st_stateless", "函数体内未见持久状态、外部写入或未知调用（静态范围）", verified=True)


def _ladder(rows, model, spec, record):
    rungs = model.get("rungs") if isinstance(model, Mapping) else None
    network_ids = {}
    if isinstance(model, Mapping) and isinstance(model.get("networks"), list):
        rungs = []
        for network in model["networks"]:
            if isinstance(network.get("ladder"), Mapping):
                rung = network["ladder"]
                rungs.append(rung)
                network_ids[str(network.get("id"))] = rung
    for row in rows:
        location = str(row.get("location") or "")
        prefix, _, identity = location.partition(":")
        if not isinstance(rungs, list) or prefix.lower() not in {"network", "rung"}:
            record(row, "source_plan_ladder_location_unbound", "需使用实际网络标识绑定规划分工，当前条目仍待验证", evidence=location)
            continue
        matches = ([network_ids[identity]] if prefix.lower() == "network" and identity in network_ids else
                   [rung for rung in rungs if str(rung.get("rung_id")) == identity])
        if len(matches) != 1:
            record(row, "source_plan_ladder_network_missing", "确认的网络位置不存在或不唯一", violated=True, evidence=location)
            continue
        record(row, "source_plan_ladder_network", "确认的网络位置已绑定", verified=True, evidence=location)
        # Scan order, stop polarity and final writers need explicit executable
        # behavior constraints; prose and network comments do not prove them.
        record(row, "source_plan_ladder_behavior_unverified", "网络分工已定位；停止优先、网络顺序和保持复位语义须由明确行为约束另行验证")


def _content(value):
    """Ignore relocated offsets while retaining all source and ownership bytes."""
    if isinstance(value, Mapping):
        return {key: _content(child) for key, child in value.items() if key not in {"offset", "source_offset"}}
    if isinstance(value, list):
        return [_content(child) for child in value]
    return value


def _sfc(rows, model, baseline, record):
    if not isinstance(model, Mapping) or not isinstance(model.get("children"), list):
        for row in rows:
            record(row, "source_plan_sfc_missing", "缺少当前已有 SFC 对象模型")
        return
    if isinstance(baseline, Mapping) and any(baseline.get(key) != model.get(key) for key in ("program", "cpu")):
        for row in rows:
            record(row, "source_plan_sfc_baseline_unbound", "SFC 对照对象的工程程序或 CPU 不一致")
        return
    targeted, bound = set(), True
    for row in rows:
        location = str(row.get("location") or "")
        prefix, _, identity = location.partition(":")
        if prefix == "transition" and identity.isdecimal():
            matches = [child for child in model["children"] if child.get("kind") == "transition" and child.get("number") == int(identity)]
        elif prefix == "child":
            matches = [child for child in model["children"] if child.get("name") == identity]
        else:
            matches = []
            bound = False
            record(row, "source_plan_sfc_location_unbound", "SFC 分工未绑定明确的 transition:<编号> 或 child:<名称>", evidence=location)
            continue
        if len(matches) != 1:
            bound = False
            record(row, "source_plan_sfc_target_missing", "指定的 SFC 源对象不存在或不唯一", violated=True, evidence=location)
            continue
        child = matches[0]
        targeted.add((child.get("kind"), child.get("name")))
        record(row, "source_plan_sfc_target", "已绑定指定的 SFC 源对象", verified=True,
               evidence={"kind": child.get("kind"), "name": child.get("name"), "number": child.get("number")})
    if not isinstance(baseline, Mapping) or not isinstance(baseline.get("children"), list):
        for row in rows:
            record(row, "source_plan_sfc_baseline_missing", "缺少同工程编辑前的 SFC 对照，其他步骤动作是否保持仍待验证")
        return
    if not bound:
        return
    untouched_before = {(child.get("kind"), child.get("name")): _content(child) for child in baseline["children"]
                        if (child.get("kind"), child.get("name")) not in targeted}
    untouched_after = {(child.get("kind"), child.get("name")): _content(child) for child in model["children"]
                       if (child.get("kind"), child.get("name")) not in targeted}
    changed = []
    if untouched_before != untouched_after:
        changed.append("children")
    for field in ("actions", "graph", "labels"):
        if _content(baseline.get(field)) != _content(model.get(field)):
            changed.append(field)
    for row in rows:
        record(row, "source_plan_sfc_preserved" if not changed else "source_plan_sfc_unplanned_change",
               "未指定的步骤、动作、图连接和声明保持" if not changed else "指定范围外的 SFC 源对象发生变化",
               verified=not changed, violated=bool(changed), evidence=changed)
    # This verifies source preservation, not the truth or runtime behavior of
    # the newly edited transition condition.
    for row in rows:
        record(row, "source_plan_sfc_behavior_unverified", "SFC 转换条件与工艺语义尚需编译和有界/原生运行验证")
