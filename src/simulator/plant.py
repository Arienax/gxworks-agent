# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""A discrete sorting plant with independent device/workpiece state.

PLC outputs drive timed device responses; sensors come from the resulting state.
This is one explicit scenario, not a mechanical model or another PLC executor.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from math import isfinite

from plc.device_identity import canonical_device


PLANT_VERSION = "sorting-plant-v1"
SENSOR_ROLES = ("present", "class_a", "class_b", "done_a", "done_b")
OUTPUT_ROLES = ("conveyor", "sort_a", "sort_b")
RULES = ("task_completion", "correct_bin", "wait_for_completion", "shared_resource", "result_retention")


@dataclass(frozen=True)
class SortingScenario:
    id: str
    plc_model: str
    bindings: dict
    workpieces: tuple
    requirements: dict
    step_ms: int
    deadline_ms: int
    end_ms: int
    response_ms: int
    return_ms: int
    speed_per_ms: float
    gate_position: float
    gate_width: float
    external_frames: tuple = ()
    initial_memory: dict = field(default_factory=dict)
    execution_context: dict = field(default_factory=lambda: {"program_type": "scan", "initial_execution_program": False})

    def __post_init__(self):
        if not self.id or not self.plc_model:
            raise ValueError("scenario identity and exact CPU are required")
        if set(self.requirements) != set(RULES) or any(not isinstance(v, str) or not v for v in self.requirements.values()):
            raise ValueError("every plant evaluation rule needs a declared requirement")
        if any(type(v) is not int or v <= 0 for v in (self.step_ms, self.deadline_ms, self.end_ms, self.response_ms, self.return_ms)):
            raise ValueError("explicit positive millisecond boundaries are required")
        if self.end_ms < self.deadline_ms or self.end_ms % self.step_ms:
            raise ValueError("evaluation end must include the deadline and end on a scan boundary")
        if not all(type(v) in {int, float} and isfinite(v) and v > 0 for v in (self.speed_per_ms, self.gate_position, self.gate_width)):
            raise ValueError("plant dimensions and speed must be finite and positive")
        if set(self.bindings) != set(SENSOR_ROLES + OUTPUT_ROLES):
            raise ValueError("plant roles must use the explicitly supplied I/O bindings")
        identities, addresses = set(), set()
        for role, row in self.bindings.items():
            if not isinstance(row, dict) or not row.get("binding_id") or not row.get("address"):
                raise ValueError("plant binding needs identity and address")
            address = canonical_device(row["address"])
            if address in addresses or row["binding_id"] in identities:
                raise ValueError("plant bindings must be unique")
            if not address.startswith("X" if role in SENSOR_ROLES else "Y"):
                raise ValueError("plant binding direction mismatch")
            if type(row.get("active_level")) is not int or row["active_level"] not in {0, 1}:
                raise ValueError("plant bindings need explicit physical active levels")
            identities.add(row["binding_id"]); addresses.add(address)
        external_addresses = set()
        previous = -1
        for frame in self.external_frames:
            if type(frame.get("timestamp_ms")) is not int or not previous < frame["timestamp_ms"] <= self.end_ms:
                raise ValueError("external requests need strictly increasing explicit time")
            previous = frame["timestamp_ms"]
            for address, value in frame.get("inputs", {}).items():
                normalized = canonical_device(address)
                if not normalized.startswith("X") or type(value) is not bool or normalized in addresses:
                    raise ValueError("external requests cannot overwrite generated sensor feedback")
                external_addresses.add(normalized)
        ids = set()
        for piece in self.workpieces:
            if not piece.get("id") or piece["id"] in ids or piece.get("class") not in {"a", "b"}:
                raise ValueError("workpieces need unique identity and an explicit class")
            if type(piece.get("arrival_ms")) is not int or not 0 <= piece["arrival_ms"] <= self.deadline_ms:
                raise ValueError("workpiece arrival time is outside the task")
            ids.add(piece["id"])
        if not ids:
            raise ValueError("empty plant tasks cannot demonstrate progress")


class SortingPlant:
    def __init__(self, scenario):
        self.scenario = scenario
        self.pieces = {p["id"]: {**deepcopy(p), "position": 0.0, "state": "pending", "bin": None} for p in scenario.workpieces}
        self.actuator = {"phase": "idle", "bin": None, "owner": None, "elapsed_ms": 0}
        self.events, self.violations = [], []
        self.time_ms = 0
        self._detected = set()

    def _at_gate(self):
        s = self.scenario
        return [p for p in self.pieces.values() if p["state"] == "on_belt"
                and s.gate_position <= p["position"] <= s.gate_position + s.gate_width]

    def sensors(self):
        pieces = self._at_gate()
        active = pieces[0] if len(pieces) == 1 else None
        values = {"present": bool(pieces), "class_a": active is not None and active["class"] == "a",
                  "class_b": active is not None and active["class"] == "b",
                  "done_a": self.actuator["phase"] == "complete" and self.actuator["bin"] == "a",
                  "done_b": self.actuator["phase"] == "complete" and self.actuator["bin"] == "b"}
        return {canonical_device(self.scenario.bindings[role]["address"]):
                bool(self.scenario.bindings[role]["active_level"] if value else 1-self.scenario.bindings[role]["active_level"])
                for role, value in values.items()}

    def _event(self, kind, piece_id=None, **data):
        self.events.append({"time_ms": self.time_ms, "kind": kind, "workpiece_id": piece_id, **data})

    def _violation(self, rule, reason, piece_id=None):
        row = {"rule": rule, "requirement_id": self.scenario.requirements[rule], "reason": reason,
               "time_ms": self.time_ms, "workpiece_id": piece_id}
        if not any((v["rule"], v["reason"], v["workpiece_id"]) == (rule, reason, piece_id) for v in self.violations):
            self.violations.append(row)

    def step(self, outputs):
        s = self.scenario
        command = {}
        for role in OUTPUT_ROLES:
            value = outputs.get(canonical_device(s.bindings[role]["address"]))
            if type(value) is not bool:
                raise ValueError("plant_output_unknown:" + role)
            command[role] = value == bool(s.bindings[role]["active_level"])
        for piece in self.pieces.values():
            if piece["state"] == "pending" and piece["arrival_ms"] <= self.time_ms:
                piece["state"] = "on_belt"
                self._event("arrival", piece["id"])
        at_gate = self._at_gate()
        if len(at_gate) > 1:
            self._violation("shared_resource", "station_occupancy_conflict")
        if command["sort_a"] and command["sort_b"]:
            self._violation("shared_resource", "simultaneous_sort_commands")
        desired = "a" if command["sort_a"] and not command["sort_b"] else "b" if command["sort_b"] and not command["sort_a"] else None
        actuator = self.actuator
        if actuator["phase"] == "idle" and desired and len(at_gate) == 1:
            actuator.update(phase="moving", bin=desired, owner=at_gate[0]["id"], elapsed_ms=0)
            self._event("sort_started", actuator["owner"], bin=desired)
        elif actuator["phase"] == "moving" and desired != actuator["bin"]:
            # Removing the command cancels the incomplete response. It does
            # not invent a successful transfer or a completed PLC action.
            self._event("sort_cancelled", actuator["owner"])
            actuator.update(phase="returning", elapsed_ms=0)
        elif actuator["phase"] == "complete" and desired != actuator["bin"]:
            actuator.update(phase="returning", elapsed_ms=0)
        if command["conveyor"]:
            for piece in self.pieces.values():
                if piece["state"] == "on_belt":
                    piece["position"] += s.speed_per_ms * s.step_ms
        self.time_ms += s.step_ms
        for piece in self.pieces.values():
            if piece["state"] == "on_belt" and piece["position"] >= s.gate_position and piece["id"] not in self._detected:
                self._detected.add(piece["id"]); self._event("detected", piece["id"])
            if piece["state"] == "on_belt" and piece["position"] > s.gate_position + s.gate_width:
                piece["state"] = "missed"
                self._violation("wait_for_completion", "left_station_before_sort_completed", piece["id"])
                self._event("missed", piece["id"])
        if actuator["phase"] == "moving":
            actuator["elapsed_ms"] += s.step_ms
            piece = self.pieces[actuator["owner"]]
            if piece["state"] != "on_belt":
                actuator.update(phase="returning", elapsed_ms=0)
            elif actuator["elapsed_ms"] >= s.response_ms:
                piece.update(state="sorted", bin=actuator["bin"])
                if piece["bin"] != piece["class"]:
                    self._violation("correct_bin", "wrong_workpiece_bin", piece["id"])
                actuator["phase"] = "complete"
                self._event("sorted", piece["id"], bin=piece["bin"])
        elif actuator["phase"] == "returning":
            actuator["elapsed_ms"] += s.step_ms
            if actuator["elapsed_ms"] >= s.return_ms:
                actuator.update(phase="idle", bin=None, owner=None, elapsed_ms=0)
                self._event("actuator_ready")
        return self.snapshot()

    def snapshot(self):
        return {"time_ms": self.time_ms, "actuator": deepcopy(self.actuator),
                "workpieces": deepcopy(list(self.pieces.values()))}

    def evaluate(self):
        s = self.scenario
        sorted_events = [e for e in self.events if e["kind"] == "sorted"]
        times = {e["workpiece_id"]: e["time_ms"] for e in sorted_events}
        completed = all(p["state"] == "sorted" and p["bin"] == p["class"] and times.get(p["id"], s.end_ms + 1) <= s.deadline_ms
                        for p in self.pieces.values())
        # Completion, mandatory intermediate events, and retention are independent
        # observations. An all-OFF program has no violations but cannot finish.
        mandatory = all({"arrival", "detected", "sort_started", "sorted"} <= {e["kind"] for e in self.events if e["workpiece_id"] == identity}
                        for identity in self.pieces)
        retained = completed and len(sorted_events) == len(self.pieces) and self.time_ms >= s.end_ms
        return {"task_completed": completed, "required_events_observed": mandatory,
                "constraint_violations": deepcopy(self.violations), "result_retained": retained,
                "completed_without_violation": completed and mandatory and retained and not self.violations,
                "rule_requirements": deepcopy(s.requirements), "deadline_ms": s.deadline_ms, "end_ms": s.end_ms}
