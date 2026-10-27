# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Display projections of Core expression and effect objects."""
from __future__ import annotations


def describe_expression(value, depth=0):
    if not isinstance(value, dict) or depth > 24:
        return "待核对"
    op = value.get("op")
    if not isinstance(op, str):
        return "待核对"
    args = [describe_expression(item, depth + 1) for item in value.get("args", [])] if isinstance(value.get("args"), list) else []
    if op == "constant":
        item = value.get("value")
        return ("true" if item else "false") if isinstance(item, bool) else str(item)
    if op in ("device", "parameter", "state"):
        kind = value.get("type") or {}
        suffix = ("（" + ("有符号" if kind.get("signed") else "无符号") + str(kind.get("bits")) + "位）"
                  if isinstance(kind, dict) and kind.get("kind") == "int" else "")
        return str(value.get("name", "待核对")) + suffix
    operators = {"gt": "大于", "ge": "大于等于", "lt": "小于", "le": "小于等于", "eq": "等于", "ne": "不等于",
                 "add": "+", "sub": "−", "mul": "×", "div": "÷", "and": "且", "or": "或", "xor": "异或",
                 "bit_and": "按位与", "bit_or": "按位或", "bit_xor": "按位异或", "shift_left": "左移", "shift_right": "右移"}
    if op == "vector":
        return "[" + "，".join(args) + "]"
    if op in ("not", "bit_not") and len(args) == 1:
        return ("非" if op == "not" else "按位取反") + "（" + args[0] + "）"
    if op in operators and args:
        return "（" + (" " + operators[op] + " ").join(args) + "）"
    return "待核对"


def describe_effect(value):
    if not isinstance(value, dict):
        return "待核对"
    target = value.get("target") or {}
    if not isinstance(target, dict):
        return "待核对"
    address = str(target.get("device") or "未指定结果地址")
    if target.get("offset"):
        address += "（偏移 " + str(target["offset"]) + "）"
    kind = value.get("kind")
    if kind == "external_action":
        return "外部设备调用，读出结果保存到 " + address
    if kind == "range_copy":
        return f"{describe_expression(value.get('source'))} 起始的 {describe_expression(value.get('count'))} 个元素复制到 {address}"
    if kind == "range_shift":
        direction = {"left": "左移", "right": "右移"}.get(value.get("direction"))
        if direction is None:
            return "待核对"
        return f"{address} 起始的 {describe_expression(value.get('count'))} 个元素{direction} {describe_expression(value.get('shift'))} 位，由 {describe_expression(value.get('source'))} 补入"
    if isinstance(value.get("value"), dict):
        return address + " = " + describe_expression(value["value"])
    return "待核对"


def describe_specification(spec):
    def rows(key):
        values = spec.get(key)
        return [row for row in values if isinstance(row, dict)] if isinstance(values, list) else []
    operations = []
    for intent in rows("operation_intents"):
        execution = intent.get("execution", {"trigger": "level"})
        execution = execution if isinstance(execution, dict) else {}
        parameters, states = intent.get("parameters"), execution.get("required_state")
        operations.append({"effects": [describe_effect(effect) for effect in intent.get("effects", [])]
                           if isinstance(intent.get("effects"), list) else [],
            "parameters": [name + " = " + describe_expression(value) for name, value in parameters.items()]
                          if isinstance(parameters, dict) else [],
            "enable": describe_expression(intent.get("enable")),
            "execution": {"rising": "条件从断开变为成立时执行", "level": "条件成立期间执行", "falling": "条件从成立变为断开时执行"}.get(execution.get("trigger"), "待核对"),
            "required_state": [name + " = " + (("ON" if value else "OFF") if isinstance(value, bool) else str(value))
                               for name, value in states.items()] if isinstance(states, dict) else []})
    return {"operation_intents": operations, "behavior_constraints": [
        {key: describe_expression(row[key]) for key in ("source", "accept", "enable") if key in row}
        for row in rows("behavior_constraints")]}
