# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Explicitly budgeted long plant runs using the existing ScanMachine semantics."""
from __future__ import annotations

from copy import deepcopy
from plc.device_identity import canonical_device

from plc.ir import is_plc_ir, ir_to_ladder, validate_plc_ir, build_plc_ir
from plc.runtime_semantics import control_runtime_facts, first_scan_fact
from simulator.bounded import ScanMachine
from simulator.plant import PLANT_VERSION, SortingPlant, SortingScenario


def run_closed_loop(program, scenario, *, max_scans=10000, version_binding=None):
    if type(max_scans) is not int or not 1 <= max_scans <= 100000:
        raise ValueError("closed-loop scan budget must be explicit and bounded")
    # Freeze and revalidate nested input mappings before observing the first scan.
    # The result carries this snapshot, not references mutable by the caller.
    scenario = SortingScenario(**deepcopy(vars(scenario)))
    program = deepcopy(program)
    result = {"schema_version": 1, "execution_layer": "discrete_plant_source_scoped_scan", "plant_version": PLANT_VERSION,
              "scenario_id": scenario.id, "plc_model": scenario.plc_model, "program_version": deepcopy(version_binding or {}),
              "scenario": deepcopy(vars(scenario)),
              "step_ms": scenario.step_ms, "scan_budget": max_scans, "trace": [], "events": [], "status": "unverified",
              "complete_run": False, "coverage": {"scans_executed": 0}, "reasons": [], "evaluation": None,
              "native_compile": "not_executed", "hardware_execution": "not_executed"}
    if is_plc_ir(program) and program["plc"]["cpu"] != scenario.plc_model:
        result["reasons"].append("program_cpu_conflicts_with_scenario")
        return result
    if not control_runtime_facts(scenario.plc_model).get("control") or not first_scan_fact(scenario.plc_model, scenario.execution_context)["available"]:
        result["reasons"].append("exact_cpu_execution_facts_unavailable")
        return result
    ir = program if is_plc_ir(program) else build_plc_ir(program, plc_model=scenario.plc_model)
    validate_plc_ir(ir)
    plant = SortingPlant(scenario)
    machine = ScanMachine(ir_to_ladder(ir), plc_model=scenario.plc_model, initial=scenario.initial_memory,
                          execution_context=scenario.execution_context)
    requests = {}
    for index in range(min(max_scans, scenario.end_ms // scenario.step_ms)):
        now = index * scenario.step_ms
        for frame in scenario.external_frames:
            if frame["timestamp_ms"] <= now:
                requests.update({canonical_device(k): v for k, v in frame["inputs"].items()})
        sensors = plant.sensors()
        inputs = {**requests, **sensors}
        try:
            observation = machine.scan({"inputs": inputs, "timestamp_ms": now}, first_scan=index == 0)
            if machine.gaps:
                result["reasons"].extend(deepcopy(machine.gaps))
                break
            state = plant.step(observation["after"])
        except ValueError as error:
            result["reasons"].append(str(error))
            break
        result["trace"].append({"scan": index, "timestamp_ms": now, "external_inputs": deepcopy(requests),
                                "sensor_feedback": sensors, "outputs": {canonical_device(r["address"]): observation["after"].get(canonical_device(r["address"]))
                                                                          for r in scenario.bindings.values() if canonical_device(r["address"]).startswith("Y")},
                                "writes": observation["writes"], "plant": state})
    result["coverage"]["scans_executed"] = len(result["trace"])
    result["events"] = deepcopy(plant.events)
    result["complete_run"] = plant.time_ms == scenario.end_ms and not result["reasons"]
    if result["complete_run"]:
        result["evaluation"] = plant.evaluate()
        result["status"] = "passed_in_modeled_scope" if result["evaluation"]["completed_without_violation"] else "failed_in_modeled_scope"
    elif not result["reasons"]:
        result["reasons"].append("scene_scan_budget_exhausted")
    result["limits"] = ["discrete response model only", "no native scan synchronization", "no hardware acceptance"]
    return result
