# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Offline replay and accounting for independently frozen known-error cases.

Uses the existing Core bounded checker. Literal observations come from the case,
not from the generator or construction plan. This does not activate candidates.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy

from plc.ir import build_plc_ir, validate_plc_ir
from simulator.bounded import ScanMachine, check_bounded_traces, MAX_SCANS


def _predicate(device, value):
    boolean = type(value) is bool
    value_type = {"kind": "bool"} if boolean else {"kind": "int", "bits": 16, "signed": False}
    return {"op": "eq", "type": {"kind": "bool"}, "args": [
        {"op": "device", "type": value_type, "name": device},
        {"op": "constant", "type": value_type, "value": value}]}


def replay_calibration(case, programs):
    frames, expected = case["frames"], case["expected"]
    if not frames or len(frames) != len(expected) or len(frames) > MAX_SCANS:
        raise ValueError("calibration observations must match the bounded trace")
    if not case.get("requirement") or not case.get("requirement_id"):
        raise ValueError("calibration needs an independent requirement")
    trace = {"id": case["id"], "initial": deepcopy(case["initial"]), "frames": deepcopy(frames),
             "execution_context": deepcopy(case["execution_context"]), "expectations": [
                 {"scan": index, "requirement_id": case["requirement_id"], "predicate": _predicate(device, value)}
                 for index, row in enumerate(expected) for device, value in row.items()]}
    results = []
    for role, ladder in programs.items():
        try:
            ir = build_plc_ir(ladder, plc_model=case["plc_model"])
            validate_plc_ir(ir)
        except ValueError as error:
            results.append({"role": role, "outcome": "structural_rejection", "reason": str(error),
                            "behavior_eligible": False, "native_compile": "not_executed"})
            continue
        report = check_bounded_traces(ir, {"plc_model": case["plc_model"]}, [trace])
        machine = ScanMachine(ladder, plc_model=case["plc_model"], initial=case["initial"],
                              execution_context=case["execution_context"])
        observations, first_difference = [], None
        for index, frame in enumerate(frames):
            observed = machine.scan(frame, first_scan=index == 0)
            actual = {device: observed["after"].get(device) for device in expected[index]}
            observations.append({"scan": index, "actual": actual, "expected": deepcopy(expected[index])})
            if first_difference is None and any(actual[d] is not None and actual[d] != v for d, v in expected[index].items()):
                first_difference = observations[-1]
        if report["status"] == "unverified":
            outcome = "unverified"
        elif role == "mutant":
            outcome = "detected" if report["status"] == "violated" else "missed"
        else:
            outcome = "false_positive" if report["status"] == "violated" else "correct_control"
        results.append({"role": role, "outcome": outcome, "behavior_eligible": True,
                        "execution_layer": "bounded_source_scoped_scan", "native_compile": "not_executed",
                        "first_difference": first_difference, "observations": observations, "checker": report})
    return {"case_id": case["id"], "category": case["category"], "source_group": case["source_group"],
            "requirement_id": case["requirement_id"], "requirement": case["requirement"],
            "plc_model": case["plc_model"], "mutation": deepcopy(case["mutation"]),
            "initial": deepcopy(case["initial"]), "frames": deepcopy(frames), "results": results}


def calibration_summary(records):
    counts = Counter(row["outcome"] for record in records for row in record["results"])
    executed = sum(row["outcome"] not in {"unverified", "structural_rejection"} for record in records for row in record["results"])
    total = sum(len(record["results"]) for record in records)
    return {"independent_cases": len(records), "programs": total, "counts": dict(counts),
            "execution_coverage": executed / total if total else None,
            "limits": ["offline bounded scan only", "native compile not executed", "human review pending"],
            "activation_policy": "unchanged"}
