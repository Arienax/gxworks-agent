"""Frozen teaching journeys through the application, with no model repairs.

Reference programs and independent expectations stay in this measurement module.
Product context, model transport, Core, IR, persistence and CSV remain shared.
"""
from __future__ import annotations

import argparse
import copy
import csv
from dataclasses import replace
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import re
import shutil
import statistics
import sys
import time
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from scripts.benchmark_user_path import Journey, private_directory, write_new


class NoReplayProvider:
    """The experiment changes retry policy, never saved model parameters."""
    def __init__(self, provider):
        self.provider = provider

    def __getattr__(self, name):
        return getattr(self.provider, name)

    @property
    def observation_sink(self):
        return self.provider.observation_sink

    @observation_sink.setter
    def observation_sink(self, value):
        self.provider.observation_sink = value

    def stream(self, request):
        if request.response_contract.name not in {"analysis", "compact_ladder", "direct_generation"}:
            raise RuntimeError("The primary contrast prohibits summary/repair model calls")
        yield from self.provider.stream(replace(request, max_retries=0))


class MeasuredJourney(Journey):
    def __enter__(self):
        super().__enter__()
        from plc.candidate_service import CandidateService
        import gxworks2.csv_export as export
        for owner, name, phase in ((CandidateService, "prepare", "core_and_IR"),
                                   (CandidateService, "compile", "artifact_compile"),
                                   (export, "generate_gx_works2_csv", "CSV_export")):
            original = getattr(owner, name)
            def observed(*args, _original=original, _phase=phase, **kwargs):
                start = time.perf_counter()
                try:
                    return _original(*args, **kwargs)
                finally:
                    if self.current is not None:
                        self.current.setdefault("local_timings", []).append({"phase": _phase,
                            "elapsed_ms": (time.perf_counter()-start)*1000})
            self.stack.enter_context(patch.object(owner, name, observed))
        return self


def schedule(cases, seed=20261006):
    pairs = [(case["case_id"], repeat) for repeat in range(1, 4) for case in cases]
    random.Random(seed).shuffle(pairs)
    return [{"journey_id": f"{index*2+offset+1:02d}_{case_id}_{arm}_{repeat}",
             "case_id": case_id, "arm": arm, "repeat": repeat}
            for index, (case_id, repeat) in enumerate(pairs)
            for offset, arm in enumerate(("legacy", "direct") if index % 2 == 0 else ("direct", "legacy"))]


def questions_from(record):
    output = record.get("output") or {}
    return (output.get("missing_info") or (output.get("analysis") or {}).get("missing_info")
            or (output.get("generation") or {}).get("missing_info") or [])


def question_review(case, questions):
    return [{"id": row.get("id"), "question": row.get("question", ""),
             "necessary": case["category"] == "missing_fact" and bool(re.search(
                 r"延时|延迟|多久|几秒|多少秒|时间|时长", str(row.get("question", ""))))}
            for row in questions]


def reviewed_confirmation(case, output, complete_text):
    """Review A against the frozen user facts, recording every correction.

    This is automated source review, not measured human confirmation. Only the
    selected model proposal is retained; no reference program is supplied to B.
    """
    from application.confirmed_generation_context import project_direct_user_facts
    from plc.specification.confirmed import validate_spec_draft
    draft = copy.deepcopy(output.get("spec_draft") or {})
    selected = draft.get("selected_approach") or next(iter(draft.get("approaches") or []), None)
    if not isinstance(selected, dict) or not selected.get("name"):
        raise ValueError("Agent A did not produce a selectable implementation")
    facts = project_direct_user_facts(complete_text, "FX3U")
    expected_io = {row["address"] for row in case["io"]}
    proposed_io = {row.get("address") for row in draft.get("io_table", [])}
    receipt = {"source": "frozen_requirement_automatic_review", "human_time_measured": False,
        "io_missing": sorted(expected_io-proposed_io), "io_extra": sorted(proposed_io-expected_io),
        "proposed_parameters": copy.deepcopy(draft.get("parameters", [])),
        "model_assumptions": copy.deepcopy((output.get("analysis") or {}).get("assumptions", [])),
        "corrections": ["Use frozen I/O, explicit electrical levels and source-reviewed process facts"],
        "selected_approach": copy.deepcopy(selected)}
    confirmed = {"schema_version": 4, "summary": complete_text,
        "io_table": copy.deepcopy(case["io"]), "io_bindings": facts.get("io_bindings", []),
        "parameters": copy.deepcopy(case["parameters"]), "selected_approach": selected,
        "intent_context": {"schema_version": 1, "requests": [{"id": "frozen-user-requirement",
            "source": "user_request", "text": complete_text}]}}
    if case["case_id"] == "segd_countdown":
        selected.setdefault("generation_contract", {})["required_opcodes"] = ["SEGD"]
    issues = validate_spec_draft(confirmed, "FX3U")
    receipt["issues"] = issues
    if issues.get("errors"):
        raise ValueError("Frozen confirmation did not pass the existing specification validator: " + str(issues["errors"]))
    return confirmed, receipt


def expected_traces(case_id):
    """Independent user-visible traces; these never inspect candidate addresses."""
    if case_id == "motor_latch":
        signals = [(0,0), (1,0), (0,0), (1,1), (0,1), (0,0), (1,0), (0,0), (0,1), (0,0)]
        state = False
        frames = []
        for i, (start, stop) in enumerate(signals):
            state = bool((start or state) and not stop)
            frames.append({"timestamp_ms": i*10, "inputs": {"X1": start, "X2": stop},
                           "expected": {"Y0": state}, "check": "自保持、停止优先与重启"})
        return [{"id": "start_stop_restart", "frames": frames}]
    if case_id == "delayed_start":
        events = [(0,0,0,False), (100,1,0,False), (110,0,0,False),
                  (5090,0,0,False), (5100,0,0,True), (5110,0,1,False),
                  (5120,0,0,False), (5200,1,0,False), (5210,0,0,False),
                  (7000,0,1,False), (7010,0,0,False), (8000,1,0,False),
                  (8010,0,0,False), (12990,0,0,False), (13000,0,0,True), (13010,1,1,False)]
        return [{"id": "timer_boundaries_cancel_restart", "frames": [{"timestamp_ms": t,
            "inputs": {"X0": x0, "X1": x1}, "expected": {"Y0": y},
            "check": "5秒边界、停止取消及重启等待"} for t,x0,x1,y in events]}]
    if case_id == "segd_countdown":
        # The frozen segment wiring uses a..g at bit0..6, decimal point at bit7.
        # JY997D16601-R PDF p435: Mitsubishi SEGD includes segment f for 7.
        codes = [0x3F,0x06,0x5B,0x4F,0x66,0x6D,0x7D,0x27,0x7F,0x6F]
        traces = []
        for clock_offset in (0, 450):
            frames = []
            for t in range(0, 22101, 50):
                on = 100 <= t < 10350 or 11250 <= t < 21750
                start = 100 if t < 11250 else 11250
                digit = max(0, 9-(t-start)//1000) if on else None
                value = codes[digit] if digit is not None else 0
                expected = {f"Y{i}": bool(value & (1 << i)) for i in range(8)}
                expected["Y10"] = bool(on and t-start >= 9000)
                frames.append({"timestamp_ms": t,
                    "inputs": {"X0": on, "M8013": (t+clock_offset)%1000 >= 500},
                    "expected": expected, "check": "显示、计时、报警与重新启动"})
            traces.append({"id": f"countdown_clock_phase_{clock_offset}", "frames": frames,
                "external_clock": "M8013 supplied waveform; CPU oscillator/native execution not verified"})
        return traces
    frames = []
    for t in range(0, 77101, 50):
        expected = {f"Y{i}": False for i in range(6)}
        if t >= 100:
            phase = (t-100)%25000
            if phase < 15000:
                expected["Y0"] = True
                expected["Y1"] = phase < 10000 or (10000 <= phase < 13000 and (phase-10000)%1000 < 500)
                expected["Y2"] = phase >= 13000
            else:
                expected["Y3"] = True
                expected["Y4"] = phase < 20000 or (20000 <= phase < 23000 and (phase-20000)%1000 < 500)
                expected["Y5"] = phase >= 23000
        frames.append({"timestamp_ms": t, "inputs": {"X0": 100 <= t < 150,
            "M8013": t%1000 >= 500}, "expected": expected, "check": "阶段时长、闪烁、互斥和三周期"})
    return [{"id": "three_continuous_cycles", "frames": frames,
             "external_clock": "M8013 waveform supplied only if referenced"}]


def criterion_observations(case_id, scan, timestamp, expected, actual):
    """Split frozen I/O expectations into inspectable requirement checks."""
    if case_id == "motor_latch":
        names = (["RUN初态OFF"] if scan == 0 else ["启动与自保持"] if scan in (1, 2)
                 else ["停止优先与输入电平"] if scan in (3, 4, 8, 9)
                 else ["释放停止不自启及重新启动"])
        return {name: actual.get("Y0") == expected["Y0"] for name in names}
    if case_id == "delayed_start":
        names = (["RUN初态OFF"] if scan == 0 else ["启动自保持与5秒边界"] if scan in (1, 2, 3, 4)
                 else ["重启完整等待"] if scan in (11, 12, 13, 14)
                 else ["停止优先、取消等待及释放不自启"])
        return {name: actual.get("Y0") == expected["Y0"] for name in names}
    if case_id == "segd_countdown":
        return {"显示、关闭清屏与重新启动": all(actual.get(f"Y{i}") == expected[f"Y{i}"] for i in range(8)),
                "九秒报警与关闭复位": actual.get("Y10") == expected["Y10"]}
    values = {key: bool(value) for key, value in actual.items()}
    result = {"红灯15秒与10秒相位": all(actual.get(key) == expected[key] for key in ("Y0", "Y3")),
              "黄灯2秒相位": all(actual.get(key) == expected[key] for key in ("Y2", "Y5")),
              "双向放行及同向颜色互斥": not (
                  (values["Y1"] or values["Y2"]) and (values["Y4"] or values["Y5"]))
                  and sum(values[key] for key in ("Y0", "Y4", "Y5")) <= 1
                  and sum(values[key] for key in ("Y3", "Y1", "Y2")) <= 1}
    if timestamp < 100:
        result["RUN初态与未启动全灭"] = not any(values.values())
    else:
        phase = (timestamp-100) % 25000
        if phase < 10000 or 15000 <= phase < 20000:
            result["绿灯常亮10秒与5秒"] = all(actual.get(key) == expected[key] for key in ("Y1", "Y4"))
        if 10000 <= phase < 13000 or 20000 <= phase < 23000:
            result["三次闪烁及0.5秒亮灭"] = all(actual.get(key) == expected[key] for key in ("Y1", "Y4"))
        if timestamp >= 25100:
            result["一次启动后连续周期波形"] = actual == expected
    return result


def evaluate_program(case, program, *, native_instructions=None):
    """Execute existing ScanMachine against independently frozen expectations.

    Unknown opcodes/effects remain unverified; the runner adds no PLC semantics.
    A 50 ms observation tolerance covers one sampled scan at phase transitions.
    """
    from plc.ir import ir_to_ladder, lower_rung_instructions, analyze_instruction_access
    from simulator.bounded import ScanMachine
    ladder = ir_to_ladder(program)
    lowered = (native_instructions if native_instructions is not None
               else [lower_rung_instructions(rung) for rung in ladder["rungs"]])
    if len(lowered) != len(ladder['rungs']):
        raise ValueError('Native instruction groups must match the parsed networks')
    opcodes = {ins["op"] for rung in lowered for ins in rung}
    field_io = {row["address"] for row in case["io"]}
    used_io = {address for address in program["devices"] if re.fullmatch(r"[XY]\d+", address)}
    results = [{"check": "现场I/O未扩充", "status": "pass" if used_io <= field_io else "fail",
                "extra": sorted(used_io-field_io)}]
    if case["case_id"] == "segd_countdown":
        results.append({"check": "指定SEGD指令", "status": "pass" if "SEGD" in opcodes else "fail"})
    control_unknown = opcodes.intersection({"STL", "RET", "MC", "MCR", "SFC", "JMP", "CJ"})
    instructions = [[(ins, analyze_instruction_access(ins["op"], ins["args"], plc_model="FX3U"))
                     for ins in rung] for rung in lowered]
    traces = []
    for trace in expected_traces(case["case_id"]):
        initial = {}
        for device in program["devices"]:
            if re.fullmatch(r"[XYMS]\d+", device) and not (device.startswith("M") and int(device[1:]) >= 8000):
                initial[device] = False
            elif re.fullmatch(r"[DTC]\d+", device):
                initial[device] = 0
                if device.startswith(("T", "C")):
                    initial[device+".contact"] = False
        initial.update({row["address"]: False for row in case["io"]})
        machine = ScanMachine(ladder, plc_model="FX3U", initial=initial,
            execution_context={"program_type": "scan"}, previous_inputs=copy.deepcopy(initial),
            instructions=instructions)
        failures, unknown, observations, tolerance, criteria = [], [], [], 0, {}
        prior_expected = None
        for scan, frame in enumerate(trace["frames"]):
            try:
                value = machine.scan({k:v for k,v in frame.items() if k not in {"expected", "check"}}, first_scan=scan == 0)
            except Exception as error:
                unknown.append({"scan": scan, "reason": type(error).__name__+":"+str(error)})
                break
            expected = frame["expected"]
            actual = {key: value["after"].get(key) for key in expected}
            reliable = not control_unknown
            mismatches = {key: {"expected": exp, "actual": actual[key]} for key, exp in expected.items()
                          if actual[key] is not None and bool(actual[key]) != exp}
            # Traffic timer chains can enter the next state one sampled scan
            # later. Freeze this tolerance; do not move deadlines to fit outputs.
            near_transition = (case["case_id"] == "traffic_cycle" and prior_expected is not None
                               and expected != prior_expected)
            if near_transition and actual == prior_expected:
                tolerance += 1
                mismatches = {}
            inspected = expected if near_transition and actual == prior_expected else actual
            for name, passed in criterion_observations(case["case_id"], scan, frame["timestamp_ms"], expected, inspected).items():
                criterion = criteria.setdefault(name, {"samples": 0, "mismatch_count": 0, "first_mismatch_ms": None})
                criterion["samples"] += 1
                if not passed:
                    criterion["mismatch_count"] += 1
                    if criterion["first_mismatch_ms"] is None:
                        criterion["first_mismatch_ms"] = frame["timestamp_ms"]
            if any(actual[key] is None for key in expected) or machine.gaps:
                unknown.extend(machine.gaps[len(unknown):] or [{"scan": scan, "reason": "unknown_observation"}])
            if mismatches and reliable and len(failures) < 12:
                failures.append({"scan": scan, "timestamp_ms": frame["timestamp_ms"], "mismatches": mismatches})
            if (mismatches or scan == 0 or near_transition) and len(observations) < 40:
                observations.append({"scan": scan, "timestamp_ms": frame["timestamp_ms"], "expected": expected, "actual": actual})
            prior_expected = expected
        reasons = sorted({str(row.get("reason")) for row in unknown})
        # Unknown write scope can leave a destination at its supplied initial
        # value. That value is not evidence about executing the instruction.
        # Keep the mismatches for audit, but never turn them into candidate faults.
        unknown_scope = "unknown_instruction_write_scope" in reasons or "Device offset policy is unavailable" in reasons
        unchecked = copy.deepcopy(failures) if unknown_scope or control_unknown else []
        if unchecked:
            failures = []
        status = "unverified" if control_unknown or unknown_scope else "fail" if failures else "unverified" if reasons else "pass"
        traces.append({"trace_id": trace["id"], "status": status, "scans": machine.scan_index,
            "failures": failures, "unknown_reasons": reasons, "unsupported_control": sorted(control_unknown),
            "unchecked_observation_mismatches": unchecked,
            "boundary_tolerance_scans": tolerance, "observations": observations,
            "criteria": [{"check": name, **value, "status": "unverified" if reasons or control_unknown
                         else "fail" if value["mismatch_count"] else "pass"} for name, value in criteria.items()],
            "external_clock": trace.get("external_clock")})
        results.append({"check": trace["frames"][0]["check"], "status": status, "trace_id": trace["id"]})
    status = "fail" if any(r["status"] == "fail" for r in results) else "unverified" if any(r["status"] == "unverified" for r in results) else "pass"
    return {"status": status, "checks": results, "traces": traces,
        "method": "existing Core ScanMachine with independently authored expected I/O",
        "representation": "native_CSV_instructions" if native_instructions is not None else "candidate_IR",
        "native_execution": False, "hardware_execution": False}


def evaluate_csv(case, program_path, comment_path):
    """Read the delivered listing and reuse Core for its actual instruction order."""
    from gxworks2.csv_importer import parse_gxworks2_csv
    from plc.ir import build_plc_ir
    parsed = parse_gxworks2_csv(program_path, comment_path)
    program = build_plc_ir(parsed.ladder, plc_model='FX3U')
    return evaluate_program(case, program, native_instructions=parsed.network_instructions)


def reviewed_question(case, question):
    """Review wording against the source and the actually frozen input."""
    result = copy.deepcopy(question)
    identity, text = str(question.get("id", "")), str(question.get("question", ""))
    if identity in {"delay_adjustable", "delay_time_source"}:
        result.update(category="optional_configuration", necessary=False,
                      reason="本轮时长答复即可实现；现场可调或HMI属于未要求的可选功能。")
    elif identity == "restart_while_waiting":
        result.update(category="unfrozen_process_detail", necessary=None,
                      reason="等待中重复按启动的行为未明确冻结；本轮未回答或验收该细节，不能归为确定多余。")
    elif case["category"] == "missing_fact" and re.search(r"延时|延迟|时间|时长", text):
        result.update(category="required_delay", necessary=True,
                      reason="原始文字无具体时长，必须先询问。")
        result["wording_burden"] = bool(re.search(r"时基|定时器设定值", text))
    else:
        result.update(category="unnecessary", necessary=False, reason="完整输入已有实现所需事实。")
    return result


def review_saved_measurement(directory, output_directory=None):
    """Offline only: keep original records, independently inspect saved IR/CSV."""
    from application.confirmed_generation_context import project_direct_user_facts
    from application.model_api import _validate_fresh_analysis_content
    destination = output_directory or directory
    destination.mkdir(parents=True, exist_ok=True)
    frozen = json.loads((directory / "frozen_inputs.json").read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in frozen["cases"]}
    if frozen["expected_traces"] != {key: expected_traces(key) for key in cases}:
        raise ValueError("Frozen behavior expectations changed; do not move the oracle to fit candidates")
    assessments = []
    for task in frozen["schedule"]:
        target = directory / task["journey_id"]
        record = json.loads((target / "journey.json").read_text(encoding="utf-8"))
        case = cases[record["case_id"]]
        final = record["jobs"][-1]
        first_submission_offset = record.get("first_submission_offset_ms")
        if first_submission_offset is None:
            first_submission_offset = (datetime.fromisoformat(record["jobs"][0]["started_at_utc"])
                - datetime.fromisoformat(record["started_at_utc"])).total_seconds()*1000
        submitted_duration = record["machine_total_ms"]-first_submission_offset
        start = time.perf_counter()
        acceptance = evaluate_program(case, final["program"]) if final.get("program") else copy.deepcopy(record["acceptance"])
        ir_acceptance_ms = (time.perf_counter()-start)*1000
        csv_start = time.perf_counter()
        csv_acceptance = {"status": "no_candidate", "checks": []}
        csv_error = None
        if record["csv_readable"]:
            try:
                csv_acceptance = evaluate_csv(case, target/"delivery"/"program.csv", target/"delivery"/"comments.csv")
            except Exception as error:
                csv_error = str(error)
                csv_acceptance = {"status": "unverified", "checks": [], "error": csv_error}
        audited_at = datetime.now(timezone.utc).isoformat()
        failure_class = record["failure_class"]
        if not failure_class:
            if acceptance["status"] == "fail":
                failure_class = "behavior_trace_failed"
            elif csv_acceptance["status"] == "fail":
                failure_class = "CSV_behavior_trace_failed"
            elif acceptance["status"] != "pass" or csv_acceptance["status"] != "pass":
                failure_class = "behavior_unverified"
        assessed = {**task, "raw_acceptance_status": record["acceptance"]["status"],
                    "acceptance": acceptance, "offline_acceptance_ms": ir_acceptance_ms,
                    "csv_acceptance": csv_acceptance, "csv_error": csv_error,
                    "offline_csv_acceptance_ms": (time.perf_counter()-csv_start)*1000,
                    "CSV_behavior_audited_at_utc": audited_at,
                    "CSV_behavior_was_measured_online": "csv_acceptance" in record,
                    "recorded_failure_class": record["failure_class"],
                    "failure_class": failure_class,
                    "questions": [reviewed_question(case, q) for q in record["questions"]],
                    "machine_total_ms": record["machine_total_ms"], "model_calls": record["model_calls"],
                    "first_submission_offset_ms": first_submission_offset,
                    "submission_to_end_ms": submitted_duration,
                    "submission_clock": "perf_counter" if "first_submission_offset_ms" in record else "saved UTC origin offset projected onto perf_counter duration",
                    "csv_readable": record["csv_readable"], "recovery_calls": record["recovery_calls"],
                    "recovery_wait_ms": record["recovery_wait_ms"],
                    "phase_times_ms": {key: record.get(key) for key in ("submission_ms", "question_delivery_ms",
                        "confirmation_review_ms", "candidate_JSON_ms", "model_wait_ms", "time_to_saved_candidate_ms",
                        "independent_acceptance_ms", "csv_readback_ms", "csv_behavior_acceptance_ms")},
                    "local_timings": copy.deepcopy(record["local_timings"])}
        assessed["correct_CSV"] = bool(record["csv_readable"] and acceptance["status"] == "pass"
            and csv_acceptance["status"] == "pass" and not record["failure_class"])
        assessed["time_to_correct_CSV_ms"] = submitted_duration if assessed["correct_CSV"] else None
        if record["failure_class"]:
            try:
                _validate_fresh_analysis_content(record["jobs"][0]["attempts"][0]["raw_content"],
                    contract_stage="bound", user_text=case["initial_request"], plc_model="FX3U")
            except Exception as error:
                assessed["offline_failure_diagnostic"] = {"type": type(error).__name__, "message": str(error)}
        if record["arm"] == "direct" and len(record["jobs"]) > 1:
            combined = case["initial_request"] + "\n用户续答：\n" + case["clarification_answer"]
            raw = case["initial_request"] + "\n\n待补充事实：\n" + "\n".join(
                q["question"] for q in questions_from(record["jobs"][0])) + "\n用户续答：\n" + case["clarification_answer"]
            assessed["human_fact_provenance_fix_changes_measured_bindings"] = (
                project_direct_user_facts(combined, "FX3U") != project_direct_user_facts(raw, "FX3U"))
        if record.get("confirmation_review"):
            assessed["confirmation_review"] = copy.deepcopy(record["confirmation_review"])
        assessments.append(assessed)
    summary = {"journeys": len(assessments), "model_calls": sum(r["model_calls"] for r in assessments),
               "correct_CSV": sum(r["correct_CSV"] for r in assessments), "groups": [],
               "offline_model_calls": 0, "recovery_calls": 0,
               "evaluator_revision": "IR and actual delivered CSV behavior checked; unknown write scope remains unverified; frozen inputs/oracle unchanged",
               "raw_records_unchanged": True, "native_execution": False, "hardware_execution": False}
    for case_id in cases:
        for arm in ("legacy", "direct"):
            group = sorted((r for r in assessments if r["case_id"] == case_id and r["arm"] == arm), key=lambda r:r["repeat"])
            times = [r["time_to_correct_CSV_ms"]/1000 for r in group if r["correct_CSV"]]
            summary["groups"].append({"case_id": case_id, "arm": arm,
                "correct_CSV": len(times), "correct_CSV_seconds": times,
                "median_correct_CSV_seconds": statistics.median(times) if times else None,
                "machine_seconds": [r["machine_total_ms"]/1000 for r in group],
                "submission_seconds": [r["submission_to_end_ms"]/1000 for r in group],
                "quality": [r["acceptance"]["status"] for r in group],
                "calls": [r["model_calls"] for r in group],
                "questions_by_category": {category: sum(q["category"] == category for r in group for q in r["questions"])
                    for category in ("required_delay", "optional_configuration", "unfrozen_process_detail", "unnecessary")}})
    usage = [a.get("usage") for task in frozen["schedule"]
             for j in json.loads((directory/task["journey_id"]/"journey.json").read_text(encoding="utf-8"))["jobs"] for a in j["attempts"]]
    summary["supplier_usage"] = {"attempts": len(usage), "missing": sum(value is None for value in usage),
                                 "tokens_total": None if any(value is None for value in usage) else usage}
    write_new(destination / "reviewed_assessments.json", {"summary": summary, "journeys": assessments})
    for name, selected in (("correct-csv.zip", [r for r in assessments if r["correct_CSV"]]),
                           ("all-candidates.zip", [r for r in assessments if r["csv_readable"]])):
        with zipfile.ZipFile(destination/name, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(selected, ensure_ascii=False, indent=2))
            for record in selected:
                source = directory/record["journey_id"]
                for artifact in (source/"delivery").iterdir():
                    archive.write(artifact, f"{record['journey_id']}/{artifact.name}")
                raw = json.loads((source/"journey.json").read_text(encoding="utf-8"))
                archive.writestr(f"{record['journey_id']}/program.ir.json", json.dumps(raw["jobs"][-1]["program"], ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def run_journey(directory, task, case, provider):
    record = {**task, "seed": 20261006, "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "jobs": [], "questions": [], "confirmation_review_ms": 0, "recovery_calls": 0,
              "human_reading_operation_wait_ms": None, "failure_class": None}
    start = time.perf_counter()
    with MeasuredJourney(directory, NoReplayProvider(provider)) as journey:
        project = journey.service.create_project(name=case["name"], plc_model="FX3U", target_mode="ladder")
        base = {"project_id": project["id"], "text": case["initial_request"], "construction_examples": False}
        def submit(kind, **changes):
            command = {**base, "kind": kind, **changes}
            if not record["jobs"]:
                record["first_submission_offset_ms"] = (time.perf_counter()-start)*1000
            job_record = journey.job(command, case_id=case["case_id"], arm=task["arm"], repeat=task["repeat"], experiment_seed=20261006)
            job_record["journey_elapsed_ms"] = (time.perf_counter()-start)*1000
            record["jobs"].append(job_record)
            return job_record
        first = submit("direct_generation" if task["arm"] == "direct" else "analysis",
                       analysis_mode="direct", allow_analysis_repair=False)
        record["questions"].extend(question_review(case, questions_from(first)))
        final = first
        complete = case["initial_request"]
        if first["job"]["status"] != "completed":
            record["failure_class"] = first["job"].get("error_code") or "first_call_failed"
        elif task["arm"] == "direct":
            if (first.get("output") or {}).get("status") == "needs_input":
                if case.get("clarification_answer") and any(q["necessary"] for q in record["questions"]):
                    complete += "\n用户补充：" + case["clarification_answer"]
                    final = submit("direct_generation", text=case["clarification_answer"], clarification_job_id=first["job"]["id"])
                    record["questions"].extend(question_review(case, questions_from(final)))
                else:
                    record["failure_class"] = "unnecessary_question_no_regeneration"
            elif case["category"] == "missing_fact":
                record["failure_class"] = "missing_delay_silently_assumed"
        else:
            if case["category"] == "missing_fact":
                if any(q["necessary"] for q in record["questions"]):
                    complete += "\n用户补充：" + case["clarification_answer"]
                else:
                    record["failure_class"] = "missing_delay_not_asked"
            if not record["failure_class"]:
                review_start = time.perf_counter()
                try:
                    spec, receipt = reviewed_confirmation(case, first["output"], complete)
                    record["confirmation_review"] = receipt
                    saved = journey.service.set_spec(project["id"], spec, None)
                    if not saved.get("valid"):
                        raise ValueError(str(saved.get("issues")))
                except Exception as error:
                    record["failure_class"] = "confirmation_review_failed"
                    record["confirmation_error"] = str(error)
                finally:
                    record["confirmation_review_ms"] = (time.perf_counter()-review_start)*1000
                if not record["failure_class"]:
                    final = submit("generation", text="请严格按照已确认规格生成候选程序。")
        if final["job"]["status"] != "completed" and not record["failure_class"]:
            record["failure_class"] = final["job"].get("error_code") or "candidate_failed"
        output = final.get("output") or {}
        if output.get("status") == "needs_input" and len(record["jobs"]) > 1:
            record["failure_class"] = "clarification_still_unresolved"
        record["csv_readable"] = False
        record["independent_acceptance_ms"] = 0
        if final.get("program"):
            evaluation_start = time.perf_counter()
            record["acceptance"] = evaluate_program(case, final["program"])
            record["independent_acceptance_ms"] = (time.perf_counter()-evaluation_start)*1000
            version_id = final["version_id"]
            csv_start = time.perf_counter()
            try:
                csv_file = journey.service.projects.artifact(project["id"], version_id, "program_csv")
                comment_file = journey.service.projects.artifact(project["id"], version_id, "comment_csv")
                with csv_file.open(encoding="utf-16", newline="") as stream:
                    rows = list(csv.reader(stream, delimiter="\t"))
                record["csv_readable"] = len(rows) > 2
                record["csv_rows"] = len(rows)
                delivery = directory / "delivery"
                delivery.mkdir()
                for source in (csv_file, comment_file):
                    shutil.copyfile(source, delivery / source.name)
                record["CSV"] = str(delivery / csv_file.name)
                behavior_start = time.perf_counter()
                record['csv_acceptance'] = evaluate_csv(case, csv_file, comment_file)
                record['csv_behavior_acceptance_ms'] = (time.perf_counter()-behavior_start)*1000
            except Exception as error:
                record["csv_error"] = str(error)
            record["csv_readback_ms"] = (time.perf_counter()-csv_start)*1000
        else:
            record["acceptance"] = {"status": "no_candidate", "checks": [], "native_execution": False, "hardware_execution": False}
        if output.get("status") in {"saved_invalid", "contract_mismatch", "saved_behavior_draft"}:
            record["failure_class"] = record["failure_class"] or str(output["status"])
        if not record["failure_class"]:
            if record["acceptance"]["status"] == "fail":
                record["failure_class"] = "behavior_trace_failed"
            elif record.get('csv_acceptance', {}).get('status') == 'fail':
                record["failure_class"] = "CSV_behavior_trace_failed"
            elif record["acceptance"]["status"] != "pass" or record.get('csv_acceptance', {}).get('status') != 'pass':
                record["failure_class"] = "behavior_unverified"
        record["model_calls"] = sum(len(job["actual_requests"]) for job in record["jobs"])
        record["provider_attempts"] = sum(len(job["attempts"]) for job in record["jobs"])
        record["raw_requirements_preserved"] = complete
        record["correct_CSV"] = bool(record["csv_readable"] and record["acceptance"]["status"] == "pass"
            and record.get('csv_acceptance', {}).get('status') == 'pass' and not record["failure_class"])
        record["machine_total_ms"] = (time.perf_counter()-start)*1000
        record["submission_to_end_ms"] = record["machine_total_ms"]-record["first_submission_offset_ms"]
        record["time_to_correct_CSV_ms"] = record["submission_to_end_ms"] if record["correct_CSV"] else None
        record["time_to_candidate_ms"] = final["journey_elapsed_ms"] if final.get("program") else None
        record["time_to_saved_candidate_ms"] = record.pop("time_to_candidate_ms")
        record["submission_ms"] = sum(job.get("submission_ms", 0) for job in record["jobs"])
        record["model_wait_ms"] = sum(attempt.get("transport_ms", 0) for job in record["jobs"] for attempt in job["attempts"])
        record["question_delivery_ms"] = first["wall_ms"] if record["questions"] else None
        generation_attempts = [attempt for job in record["jobs"] for attempt in job["attempts"]
                               if attempt["response_contract"] in {"compact_ladder", "direct_generation"}]
        record["candidate_JSON_ms"] = generation_attempts[-1].get("json_complete_ms") if generation_attempts else None
        record["local_timings"] = [row for job in record["jobs"] for row in job.get("local_timings", [])]
        record["recovery_wait_ms"] = 0
        record["first_candidate_quality"] = (record["acceptance"]["status"]
            if not record["failure_class"] or record["failure_class"] == "behavior_unverified" else "fail")
    write_new(directory / "journey.json", record)
    print(json.dumps({"journey": task["journey_id"], "correct_CSV": record["correct_CSV"],
        "acceptance": record["acceptance"]["status"], "failure": record["failure_class"],
        "model_calls": record["model_calls"], "seconds": round(record["machine_total_ms"]/1000, 2)}, ensure_ascii=False), flush=True)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile-id", default="custom_5e724037ec2f4ab49be541ff9c215ed7")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--journey-id", action="append", default=[], help="Run selected frozen journey IDs; completed records remain immutable")
    parser.add_argument("--review-only", action="store_true", help="Inspect saved candidates without network calls or overwriting raw results")
    parser.add_argument("--review-output", type=Path, help="Separate directory for offline assessments and deliveries")
    args = parser.parse_args(argv)
    directory = private_directory(args.output)
    if args.review_only:
        if args.live:
            parser.error("--review-only cannot call live models")
        return review_saved_measurement(directory, private_directory(args.review_output) if args.review_output else None)
    cases = json.loads((ROOT / "benchmarks/direct_teaching_cases.json").read_text(encoding="utf-8"))["cases"]
    tasks = schedule(cases)
    requested = set(args.journey_id)
    unknown = requested-{task["journey_id"] for task in tasks}
    if unknown:
        parser.error("Unknown frozen journey IDs: " + ", ".join(sorted(unknown)))
    if not args.live:
        print(json.dumps({"journeys": len(tasks), "network_calls": 0, "schedule": tasks}, ensure_ascii=False, indent=2))
        return 0
    from storage.config import load_full_config, get_model_profile
    from model_runtime.provider import get_active_provider
    config = load_full_config()
    profile = get_model_profile(config, args.profile_id)
    if (profile.get("name") != "model" or profile.get("model") != "deepseek-ai/DeepSeek-V4.1-Flash"
            or str(profile.get("baseUrl", "")).rstrip("/") != "https://api-inference.modelscope.cn/v1"):
        parser.error("Use the saved model / ModelScope DeepSeek V4.1 Flash profile")
    provider = get_active_provider({**config, "activeModelProfileId": args.profile_id})
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / "frozen_inputs.json").exists():
        write_new(directory / "frozen_inputs.json", {"frozen_at_utc": datetime.now(timezone.utc).isoformat(),
            "cases": cases, "schedule": tasks, "seed": 20261006,
            "profile": {key: profile.get(key) for key in ("id", "name", "adapter", "model", "baseUrl", "userModelSettings", "capabilityOverrides")},
            "global_active_profile_id": config.get("activeModelProfileId"), "construction_examples": False,
            "retries": 0, "model_repairs": 0, "human_confirmation_time_measured": False,
            "expected_traces": {case["case_id"]: expected_traces(case["case_id"]) for case in cases}})
    else:
        frozen = json.loads((directory / "frozen_inputs.json").read_text(encoding="utf-8"))
        if frozen["cases"] != cases or frozen["schedule"] != tasks:
            parser.error("Inputs/schedule changed after freezing")
        if frozen["profile"]["userModelSettings"] != profile.get("userModelSettings"):
            parser.error("Saved model parameters changed after freezing")
    by_id = {case["case_id"]: case for case in cases}
    for task in tasks:
        if requested and task["journey_id"] not in requested:
            continue
        target = directory / task["journey_id"]
        if (target / "journey.json").exists():
            continue  # Preserve completed first attempts; never rerun them.
        if target.exists():
            parser.error("Interrupted journey exists; retain its raw records and review it before continuing")
        target.mkdir()
        run_journey(target, task, by_id[task["case_id"]], provider)
    records = [json.loads((directory / task["journey_id"] / "journey.json").read_text(encoding="utf-8"))
               for task in tasks if (directory / task["journey_id"] / "journey.json").exists()]
    summary = {"journeys": len(records), "model_calls": sum(r["model_calls"] for r in records),
               "correct_CSV": sum(r["correct_CSV"] for r in records), "groups": [],
               "pending_journeys": [task["journey_id"] for task in tasks
                                    if not (directory/task["journey_id"]/"journey.json").exists()]}
    for case in cases:
        for arm in ("legacy", "direct"):
            group = [r for r in records if r["case_id"] == case["case_id"] and r["arm"] == arm]
            correct = [r["time_to_correct_CSV_ms"]/1000 for r in group if r["correct_CSV"]]
            summary["groups"].append({"case_id": case["case_id"], "arm": arm,
                "correct_CSV": len(correct), "latency_seconds": correct,
                "median_seconds": statistics.median(correct) if correct else None,
                "first_candidate_quality": [r["first_candidate_quality"] for r in group],
                "failures": [r["failure_class"] for r in group],
                "calls": [r["model_calls"] for r in group],
                "necessary_questions": sum(q["necessary"] for r in group for q in r["questions"]),
                "unnecessary_questions": sum(not q["necessary"] for r in group for q in r["questions"])})
    if not (directory/"summary.json").exists():
        write_new(directory / "summary.json", summary)
    elif not summary["pending_journeys"]:
        write_new(directory / "completed-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
