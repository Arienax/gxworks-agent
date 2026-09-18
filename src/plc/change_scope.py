"""Deterministic change boundaries and impact summaries for PLC IR candidates.

A scope limits changed networks, including every address used before and after
the change. It is checked against the frozen candidate, never model claims.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping


class ChangeScopeError(ValueError):
    """A valid candidate would change more than the user authorized."""


# The IR dependency table records operand bases. It does not yet expand block,
# double-word or indexed accesses, so it cannot prove a finite address boundary
# for those operations. Network scopes can still handle such networks.
_SCALAR_OPS = frozenset({
    "LD", "LDI", "LDP", "LDF", "AND", "ANI", "ANDP", "ANDF", "OR", "ORI", "ORP", "ORF",
    "OUT", "SET", "RST", "PLS", "PLF", "ANB", "ORB", "MPS", "MRD", "MPP", "INV", "NOP",
    "MOV", "MOVP", "ADD", "ADDP", "SUB", "SUBP", "INC", "INCP", "DEC", "DECP",
} | {prefix + suffix for prefix in ("LD", "AND", "OR") for suffix in ("=", "<>", ">=", "<=", ">", "<")})


def _has_bounded_addresses(network, plc_model):
    from plc.validation import parse_device_address
    for instruction in (network or {}).get("instructions", []):
        if instruction.get("op", "").upper() not in _SCALAR_OPS:
            return False
        for operand in instruction.get("args", []):
            value = str(operand).strip().upper()
            if parse_device_address(value, plc_model) is None and not re.fullmatch(r"K-?\d+|H[0-9A-F]+", value):
                return False
    return True


def normalize_change_scope(value, *, plc_model="FX3U"):
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) - {"network_ids", "addresses"}:
        raise ValueError("修改范围只支持 network_ids 和 addresses。")
    result = {}
    for key in ("network_ids", "addresses"):
        items = value.get(key)
        if items is None:
            continue
        if not isinstance(items, list) or not items or len(items) > 4096:
            raise ValueError("已启用的修改范围必须包含至少一个网络或地址。")
        normalized = []
        for item in items:
            if not isinstance(item, str) or not item.strip() or len(item) > 128:
                raise ValueError("修改范围中的网络和地址必须是非空字符串。")
            item = item.strip()
            if key == "addresses":
                from plc.validation import parse_device_address
                parsed = parse_device_address(item, plc_model)
                if parsed is None:
                    raise ValueError("修改范围包含无效 PLC 地址：" + item)
                prefix, index = parsed
                item = prefix + (format(index, "o") if plc_model == "FX3U" and prefix in {"X", "Y"} else str(index))
            if item not in normalized:
                normalized.append(item)
        result[key] = normalized
    if not result:
        raise ValueError("局部修改范围不能为空；整程序修改请使用 null。")
    return result


def validate_scope_baseline(scope, before, *, target_mode="ladder"):
    scope = normalize_change_scope(scope, plc_model=((before or {}).get("plc") or {}).get("cpu", "FX3U"))
    if scope is None:
        return None
    if target_mode != "ladder" or not before or before.get("kind") != "plc_program_ir":
        raise ValueError("网络和地址范围仅支持已有梯形图版本；ST、FBD 和首次生成请使用整程序范围。")
    existing = {network["id"] for network in before.get("networks", [])}
    unknown = set(scope.get("network_ids", [])) - existing
    if unknown:
        raise ValueError("修改范围包含基线中不存在的网络：" + "、".join(sorted(unknown)))
    return scope


def _network_addresses(network):
    if not network:
        return set()
    return set(network.get("reads", [])) | set(network.get("writes", []))


def candidate_impact(before, after):
    """Include order, shared comments and editable program-level settings."""
    from plc.ir import validate_plc_ir
    if before is not None:
        validate_plc_ir(before, validate_ladder=False)
    validate_plc_ir(after, validate_ladder=False)
    before = before or {}
    old = {network["id"]: network for network in before.get("networks", [])}
    new = {network["id"]: network for network in after.get("networks", [])}
    changed = {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
    addresses = set()
    for key in changed:
        addresses.update(_network_addresses(old.get(key)))
        addresses.update(_network_addresses(new.get(key)))
    def comments(program):
        return {address: (item.get("comment", ""), bool(item.get("comment_declared")))
                for address, item in program.get("devices", {}).items()
                if item.get("comment") or item.get("comment_declared")}
    old_comments, new_comments = comments(before), comments(after)
    comments_changed = {address for address in old_comments.keys() | new_comments.keys()
                        if old_comments.get(address) != new_comments.get(address)}
    addresses.update(comments_changed)
    # A shared device comment affects all its readers/writers, not only the
    # network the model happened to return in its claimed diff.
    for key in old.keys() | new.keys():
        if (_network_addresses(old.get(key)) | _network_addresses(new.get(key))) & comments_changed:
            changed.add(key)
    derived = {"networks", "devices", "timing", "logic", "analysis", "source", "revision"}
    metadata = [key for key in before.keys() | after.keys()
                if key not in derived and before.get(key) != after.get(key)]
    for field, subfield in (("logic", "requirements"), ("analysis", "config")):
        if (before.get(field) or {}).get(subfield) != (after.get(field) or {}).get(subfield):
            metadata.append(field + "." + subfield)
    old_order = [item["id"] for item in sorted(old.values(), key=lambda item: item["order"])]
    new_order = [item["id"] for item in sorted(new.values(), key=lambda item: item["order"])]
    return {"network_ids": sorted(changed), "addresses": sorted(addresses),
            "device_comments": sorted(comments_changed), "metadata_fields": sorted(metadata),
            "network_order_changed": old_order != new_order}


def enforce_change_scope(before, after, scope, *, target_mode="ladder"):
    scope = validate_scope_baseline(scope, before, target_mode=target_mode)
    impact = candidate_impact(before, after)
    if scope is None:
        return impact
    violations = []
    if impact["metadata_fields"]:
        violations.append("程序级设置：" + "、".join(impact["metadata_fields"]))
    if "network_ids" in scope:
        outside = set(impact["network_ids"]) - set(scope["network_ids"])
        if outside:
            violations.append("网络：" + "、".join(sorted(outside)))
        selected_addresses = set()
        for program in (before, after):
            for network in program.get("networks", []):
                if network["id"] in scope["network_ids"]:
                    selected_addresses.update(_network_addresses(network))
        unowned_comments = set(impact["device_comments"]) - selected_addresses
        if unowned_comments:
            violations.append("范围外地址注释：" + "、".join(sorted(unowned_comments)))
    if "addresses" in scope:
        old = {network["id"]: network for network in before["networks"]}
        new = {network["id"]: network for network in after["networks"]}
        uncertain = []
        for key in old.keys() | new.keys():
            if old.get(key) == new.get(key):
                continue
            if (not (_network_addresses(old.get(key)) | _network_addresses(new.get(key)))
                    or not _has_bounded_addresses(old.get(key), before["plc"]["cpu"])
                    or not _has_bounded_addresses(new.get(key), after["plc"]["cpu"])):
                uncertain.append(key)
        if uncertain:
            violations.append("无法界定地址范围的网络（含间接、多字、块或未支持指令，请使用网络范围）：" + "、".join(sorted(uncertain)))
        outside = set(impact["addresses"]) - set(scope["addresses"])
        if outside:
            violations.append("地址：" + "、".join(sorted(outside)))
    if violations:
        raise ChangeScopeError("候选超出允许修改范围（" + "；".join(violations) + "）。请调整范围或重新生成。")
    return copy.deepcopy(impact)


def scope_instruction(scope):
    if scope is None:
        return ""
    import json
    return ("\n\n用户已限定本次修改范围：" + json.dumps(scope, ensure_ascii=False)
            + "。仅修改指定网络；若限定地址，每个变化网络修改前后的所有读写地址必须都在范围内。"
              "保持范围外网络、共享注释和程序级设置不变。服务会在保存前拒绝越界候选。")
