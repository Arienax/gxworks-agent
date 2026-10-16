"""Rebuildable composition views. Confirmed behavior remains in the specification.

No device allocation, callable creation, or confirmation takes place here.
Labels provide grouping suggestions; stable binding IDs provide identity.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
import re

from plc.construction import effect_devices, expression_devices
from plc.device_identity import canonical_device
from plc.specification.behavior import normalize_behavior_constraints, normalize_construction_plan


COMPOSITION_VERSION = "composition-view-v1"
RELATION_KINDS = frozenset({"requires", "cooperates_with", "per_equipment", "shared_by", "maps_to"})
_ROLE_WORDS = re.compile(
    r"启动|停止|复位|完成|到位|传感器|按钮|线圈|接触器|输入|输出|"
    r"\b(?:start|stop|reset|done|sensor|input|output)\b", re.I)


def _devices(row, *, writes_only=False):
    devices = set()
    if not writes_only:
        for key in ("source", "accept", "enable", "predicate", "when"):
            if row.get(key):
                devices.update(expression_devices(row[key]))
    if row.get("output"):
        devices.add(row["output"])
    if row["kind"] == "initialize":
        from plc.specification.behavior import initialization_values
        devices.update(initialization_values(row))
    for transition in row.get("transitions", []):
        if not writes_only:
            devices.update(expression_devices(transition["when"]))
        for effect in transition["effects"]:
            devices.update(effect_devices(effect))
            if not writes_only and effect.get("value"):
                devices.update(expression_devices(effect["value"]))
    return devices


def equipment_groups(spec):
    """Suggest groups only from current stable bindings; missing IDs stay gaps."""
    bindings, gaps, seen = [], [], {}
    table = {r.get("binding_id"): r for r in spec.get("io_table", [])
             if isinstance(r, Mapping) and r.get("binding_id")}
    raw_rows = list(spec.get("io_bindings", []))
    bound_ids = {r.get("binding_id") for r in raw_rows if isinstance(r, Mapping)}
    raw_rows.extend(r for identity, r in table.items() if identity not in bound_ids)
    for raw in raw_rows:
        if not isinstance(raw, Mapping) or not raw.get("binding_id"):
            gaps.append({"reason": "binding_identity_missing"})
            continue
        identity = str(raw["binding_id"])
        address = raw.get("address")
        if not address:
            gaps.append({"binding_id": identity, "reason": "binding_address_missing"})
            continue
        address = canonical_device(address)
        if identity in seen:
            if seen[identity]["address"] != address:
                gaps.append({"binding_id": identity, "reason": "binding_identity_conflict"})
            continue
        row = {k: deepcopy(raw[k]) for k in ("binding_id", "role", "label", "active_level") if k in raw}
        row.update(address=address)
        row.setdefault("label", table.get(identity, {}).get("label", ""))
        seen[identity] = row
        bindings.append(row)
    by_stem = defaultdict(list)
    for row in bindings:
        label = str(row.get("label") or "").strip()
        stem = _ROLE_WORDS.sub("", label).strip(" _-:：()（）")
        # An uninformative/empty label cannot merge otherwise unrelated signals.
        by_stem[("label", stem) if stem else ("binding", row["binding_id"])].append(row)
    groups = []
    for (basis, label), members in sorted(by_stem.items()):
        ids = sorted(row["binding_id"] for row in members)
        groups.append({"id": "equipment:" + ids[0], "label": label,
                       "binding_ids": ids, "members": sorted(members, key=lambda r: r["binding_id"]),
                       "confirmation": "candidate", "source_status": "current_binding_labels",
                       "binding_status": "resolved" if not any(g.get("binding_id") in ids for g in gaps) else "conflict",
                       "basis": basis, "state_ownership": "unknown",
                       "gaps": ["state_ownership_not_confirmed"]})
    return groups, bindings, gaps


def derive_composition(spec, *, input_version=None, relations=()):
    """Reference the existing behavior and chosen construction IDs, without copying rules."""
    spec = spec if isinstance(spec, Mapping) else {}
    groups, bindings, gaps = equipment_groups(spec)
    behavior = normalize_behavior_constraints(spec.get("behavior_constraints"))
    construction = normalize_construction_plan((spec.get("selected_approach") or {}).get("construction_plan"))
    instances = (construction or {}).get("instances", [])
    by_requirement = {r["id"]: r for r in behavior}
    by_instance = {i["id"]: i for i in instances}
    address_bindings = defaultdict(list)
    for row in bindings:
        address_bindings[row["address"]].append(row["binding_id"])
    units, edges, owners = [], [], defaultdict(list)
    for instance in instances:
        row = by_requirement.get(instance["requirement_id"])
        resolved = row is not None and row["status"] == "confirmed"
        unit = {"id": instance["id"], "requirement_ref": instance["requirement_id"],
                "implementation_ref": instance["id"], "purpose": row["label"] if row else None,
                "confirmation": "confirmed" if resolved else "unverified",
                "binding_status": "resolved" if resolved else "unresolved", "binding_ids": [],
                "state_devices": [], "native_fb_count": None}
        if not resolved:
            gaps.append({"unit_id": instance["id"], "reason": "construction_requirement_not_confirmed"})
        if row:
            unit["binding_ids"] = sorted({b for device in _devices(row) for b in address_bindings[device]})
            unit["state_devices"] = sorted(d for d in _devices(row, writes_only=True) if d.startswith(("M", "D")))
            unit["equipment_refs"] = [g["id"] for g in groups if set(g["binding_ids"]).intersection(unit["binding_ids"])]
            if any(g.get("binding_id") in unit["binding_ids"] for g in gaps):
                unit["binding_status"] = "conflict"
            # Initialization and later processing may legitimately own the same cell
            # in different phases. Do not interpret that as a second runtime writer.
            if resolved and row["kind"] != "initialize":
                for device in unit["state_devices"]:
                    owners[device].append(instance["id"])
        for binding_id in unit["binding_ids"]:
            edges.append({"id": f"maps:{instance['id']}:{binding_id}", "kind": "maps_to",
                          "from": instance["id"], "to": binding_id, "dependency_type": "data",
                          "confirmation": "confirmed" if resolved else "candidate",
                          "source_status": "specification_reference", "binding_status": unit["binding_status"],
                          "preconditions": ["current binding and referenced behavior remain unchanged"],
                          "sources": [{"kind": "behavior_constraints", "identity": instance["requirement_id"]}]})
        for dependency in instance["depends_on"]:
            valid = dependency in by_instance
            edges.append({"id": f"order:{dependency}:{instance['id']}", "kind": "requires",
                          "from": dependency, "to": instance["id"], "dependency_type": "execution_order",
                          "confirmation": "selected_construction", "source_status": "construction_reference",
                          "binding_status": "resolved" if valid else "unresolved",
                          "preconditions": ["existing construction execution order"],
                          "sources": [{"kind": "construction_plan", "identity": instance["id"]}]})
            if not valid:
                gaps.append({"unit_id": instance["id"], "reference": dependency, "reason": "dependency_reference_missing"})
        units.append(unit)
    for unit in units:
        for group_id in unit.get("equipment_refs", []):
            edges.append({"id": f"scope:{unit['id']}:{group_id}", "kind": "per_equipment",
                          "from": unit["id"], "to": group_id, "dependency_type": "equipment_scope",
                          "confirmation": "candidate", "source_status": "derived_binding_scope",
                          "binding_status": unit["binding_status"],
                          "preconditions": ["label grouping remains a candidate until its functional boundary is confirmed"],
                          "sources": [{"kind": "behavior_constraints", "identity": unit["requirement_ref"]}]})
    shared_state = []
    for device, unit_ids in sorted(owners.items()):
        if len(unit_ids) > 1:
            gaps.append({"reason": "multiple_state_writers_require_execution_review", "device": device,
                         "unit_ids": unit_ids, "status": "unverified"})
            shared_state.append({"device": device, "unit_refs": deepcopy(unit_ids), "execution_status": "unverified"})
            for owner in unit_ids:
                edges.append({"id": f"shared:{device}:{owner}", "kind": "shared_by", "from": "device:" + device,
                              "to": owner, "dependency_type": "state_sharing", "confirmation": "candidate",
                              "source_status": "existing_state_references", "binding_status": "resolved",
                              "preconditions": ["multiple confirmed behaviors reference this existing state; scan-order review is still required"],
                              "sources": [{"kind": "construction_plan", "identity": owner}]})
    # Evidence suggestions never gain the authority of the specification.
    ids = {r["id"] for r in units} | {g["id"] for g in groups} | {b["binding_id"] for b in bindings}
    suggestions = []
    for relation in relations:
        item = deepcopy(relation)
        item["confirmation"] = "candidate"
        item["binding_status"] = "resolved" if item.get("from") in ids and item.get("to") in ids else "unresolved"
        suggestions.append(item)
    candidate_units = [{"id": "control:" + group["id"], "purpose": group["label"],
                        "equipment_ref": group["id"], "binding_ids": deepcopy(group["binding_ids"]),
                        "confirmation": "candidate", "source_status": "current_binding_labels",
                        "state_ownership": "unknown", "quantity": None, "native_fb_count": None,
                        "implementation_refs": [], "gaps": ["functional_boundary_not_confirmed"]}
                       for group in groups]
    selected_requirements = {i["requirement_id"] for i in instances}
    candidate_units.extend({"id": "behavior:" + row["id"], "purpose": row["label"],
                            "requirement_ref": row["id"], "confirmation": row["status"],
                            "source_status": row["provenance"]["source"], "quantity": None,
                            "native_fb_count": None, "implementation_refs": [],
                            "gaps": ["implementation_not_selected"]}
                           for row in behavior if row["id"] not in selected_requirements)
    confirmed_ids = [r["id"] for r in behavior if r["status"] == "confirmed"]
    return {"version": COMPOSITION_VERSION, "label": "功能组合计划（引用已有确认）", "input_version": deepcopy(input_version or {}),
            "plc_model": spec.get("plc_model"), "requirement_refs": confirmed_ids,
            "equipment_groups": groups, "functional_units": units, "functional_unit_candidates": candidate_units, "bindings": bindings,
            "relations": edges, "suggestions": suggestions, "shared_state": shared_state, "gaps": gaps,
            "counts": {"equipment_candidates": len(groups), "selected_construction_instances": len(instances),
                       "native_fb_instances": None}, "allocations": [], "model_calls": 0,
            "authority": "derived_references_only"}
