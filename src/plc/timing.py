"""Deterministic scan-time estimation and monitor profiles.

The estimator is deliberately separate from the LLM.  FX3U figures are based
on the bundled official programming manual, JY997D16601 Rev.R, Appendix B
(PDF pages 928-945).  Exact execution time still depends on the concrete CPU,
device ranges, instruction operands, branch state and installed I/O, so every
result carries coverage and uncertainty instead of presenting a false exact
number.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


TIMING_ANALYSIS_SCHEMA_VERSION = 1
FX3U_TIMING_PROFILE_VERSION = "JY997D16601-Rev.R-Appendix-B-v1"

_K_RE = re.compile(r"^K(-?\d+)$", re.I)
_DEVICE_RE = re.compile(r"^(SM|SD|X|Y|M|D|T|C|S)(\d+)$", re.I)


def _entry(
    on_us: float,
    off_us: Optional[float] = None,
    *,
    page: int,
    confidence: str = "high",
) -> Dict[str, Any]:
    off = float(on_us if off_us is None else off_us)
    on = float(on_us)
    return {
        "best_us": min(on, off),
        "typical_us": round((on + off) / 2.0, 6),
        "worst_us": max(on, off),
        "confidence": confidence,
        "source_pdf_page": page,
    }


# FX3U/FX3UC values.  Where a device range can turn a one-step instruction
# into a two/three-step form, the listed best/typical/worst values include that
# documented range rather than assuming the fastest address.
_FX3U_FIXED: Dict[str, Dict[str, Any]] = {
    "LD": {"best_us": 0.065, "typical_us": 0.129, "worst_us": 0.193, "confidence": "high", "source_pdf_page": 928},
    "LDI": {"best_us": 0.065, "typical_us": 0.129, "worst_us": 0.193, "confidence": "high", "source_pdf_page": 928},
    "AND": {"best_us": 0.065, "typical_us": 0.129, "worst_us": 0.193, "confidence": "high", "source_pdf_page": 928},
    "ANI": {"best_us": 0.065, "typical_us": 0.129, "worst_us": 0.193, "confidence": "high", "source_pdf_page": 928},
    "OR": {"best_us": 0.065, "typical_us": 0.129, "worst_us": 0.193, "confidence": "high", "source_pdf_page": 928},
    "ORI": {"best_us": 0.065, "typical_us": 0.129, "worst_us": 0.193, "confidence": "high", "source_pdf_page": 928},
    "ANB": _entry(0.065, page=928),
    "ORB": _entry(0.065, page=928),
    "MPS": _entry(0.065, page=928),
    "MRD": _entry(0.065, page=928),
    "MPP": _entry(0.065, page=928),
    "INV": _entry(0.065, page=928),
    "LDP": _entry(7.8, page=928),
    "LDF": _entry(7.8, page=928),
    "ANP": _entry(7.5, page=928),
    "ANDP": _entry(7.5, page=928),
    "ANF": _entry(7.5, page=928),
    "ANDF": _entry(7.5, page=928),
    "ORP": _entry(7.4, page=928),
    "ORF": _entry(7.4, page=928),
    "PLS": {"best_us": 0.257, "typical_us": 0.289, "worst_us": 0.321, "confidence": "high", "source_pdf_page": 929},
    "PLF": {"best_us": 0.257, "typical_us": 0.289, "worst_us": 0.321, "confidence": "high", "source_pdf_page": 929},
    "MOV": _entry(0.64, 0.32, page=938),
    "DMOV": _entry(1.48, 1.48, page=938),
    "CMP": _entry(15.5, 0.455, page=938),
    "DCMP": _entry(16.0, 0.845, page=938),
    "ZCP": _entry(18.9, 0.585, page=938),
    "DZCP": _entry(19.7, 1.105, page=938),
    "CML": _entry(10.6, 0.325, page=938),
    "DCML": _entry(10.2, 0.585, page=938),
    "XCH": _entry(10.7, 0.325, page=938),
    "DXCH": _entry(11.4, 0.585, page=938),
    "BCD": _entry(7.94, 0.325, page=938),
    "DBCD": _entry(12.49, 0.585, page=938),
    "BIN": _entry(4.38, 0.325, page=938),
    "DBIN": _entry(5.32, 0.585, page=938),
    "ADD": _entry(4.77, 0.455, page=938),
    "DADD": _entry(5.72, 0.845, page=938),
    "SUB": _entry(4.82, 0.455, page=938),
    "DSUB": _entry(5.78, 0.845, page=938),
    "MUL": _entry(4.6, 0.455, page=938),
    "DMUL": _entry(5.7, 0.845, page=938),
    "DIV": _entry(6.3, 0.455, page=938),
    "DDIV": _entry(7.67, 0.845, page=938),
    "INC": _entry(6.2, 0.195, page=938),
    "DINC": _entry(6.4, 0.325, page=938),
    "DEC": _entry(6.2, 0.195, page=938),
    "DDEC": _entry(6.4, 0.325, page=938),
    "WAND": _entry(3.57, 0.455, page=938),
    "WOR": _entry(3.57, 0.455, page=938),
    "WXOR": _entry(3.57, 0.455, page=938),
    "NEG": _entry(7.6, 0.195, page=938),
    "DNEG": _entry(8.0, 0.325, page=938),
    "ROR": _entry(10.5, 0.325, page=938),
    "DROR": _entry(11.5, 0.585, page=938),
    "ROL": _entry(10.5, 0.325, page=938),
    "DROL": _entry(11.5, 0.585, page=938),
    "RCR": _entry(10.9, 0.325, page=938),
    "DRCR": _entry(11.8, 0.585, page=938),
    "RCL": _entry(10.9, 0.325, page=938),
    "DRCL": _entry(11.8, 0.585, page=938),
    "SUM": _entry(12.7, 0.325, page=939),
    "DSUM": _entry(16.9, 0.585, page=939),
    "FLT": _entry(9.8, 0.325, page=939),
    "DFLT": _entry(9.5, 0.585, page=939),
    "SPD": _entry(16.0, 12.6, page=939),
    "PLSY": _entry(20.0, 6.9, page=939),
    "DPLSY": _entry(13.6, 6.9, page=939),
    "PWM": _entry(10.6, 6.2, page=939),
    "PLSR": _entry(11.2, 7.0, page=939),
    "DPLSR": _entry(11.2, 7.0, page=939),
    "ALT": _entry(11.6, 0.2, page=939),
    "RAMP": _entry(15.0, 7.5, page=939),
    "RS": _entry(15.6, 5.7, page=940),
    "RS2": _entry(18.1, 5.3, page=940),
    "PID": _entry(20.0, 8.9, page=940),
    "ECMP": _entry(18.2, 0.845, page=940),
    "EMOV": _entry(10.0, 0.585, page=940),
    "EADD": _entry(14.2, 0.845, page=940),
    "ESUB": _entry(14.2, 0.845, page=940),
    "EMUL": _entry(14.1, 0.845, page=940),
    "EDIV": _entry(17.7, 0.845, page=940),
    "INT": _entry(13.2, 0.325, page=941),
    "DINT": _entry(13.0, 0.585, page=941),
    "DSZR": _entry(170.0, 7.0, page=941),
    "DVIT": _entry(178.0, 7.1, page=941),
    "ZRN": _entry(62.0, 7.1, page=941),
    "PLSV": _entry(144.0, 7.1, page=941),
    "DRVI": _entry(178.0, 7.1, page=941),
    "DDRVI": _entry(178.0, 7.1, page=941),
    "DRVA": _entry(178.0, 7.1, page=941),
    "DDRVA": _entry(178.0, 7.1, page=941),
    "GRY": _entry(10.2, 0.325, page=942),
    "DGRY": _entry(10.7, 0.585, page=942),
    "GBIN": _entry(15.4, 0.325, page=942),
    "DGBIN": _entry(16.0, 0.585, page=942),
    "ADPRW": _entry(106.55, 30.4, page=944),
    "HSCT": _entry(30.0, 1.365, page=945),
}

_LINEAR_FX3U = {
    # opcode: base-on, per-point-on, off, operand index containing n, page
    "BMOV": (13.9, 0.44, 0.455, 2, 938),
    "FMOV": (14.2, 0.19, 0.455, 2, 938),
    "SFTL": (23.2, 0.08, 0.585, 2, 938),
    "SFTLP": (23.2, 0.08, 0.585, 2, 938),
    "SFTR": (23.2, 0.08, 0.585, 2, 938),
    "SFTRP": (23.2, 0.08, 0.585, 2, 938),
    "MEAN": (11.8, 0.41, 0.325, 2, 939),
    "DMEAN": (17.8, 2.13, 0.585, 2, 939),
    # Conservative FX3U/FX3UC special-function-block variant.
    "FROM": (107.0, 903.0, 0.585, 3, 940),
    "DFROM": (119.0, 1791.0, 1.105, 3, 940),
    "TO": (96.7, 119.2, 0.585, 3, 940),
    "DTO": (17.3, 297.7, 1.105, 3, 940),
}

_COMMUNICATION_OR_FLOW = {
    "FOR", "NEXT", "CALL", "SRET", "IRET", "MODRW", "MC_MOVEABSOLUTE",
    "DRVTBL", "DRVMUL", "RBFM", "WBFM", "LOADR", "SAVER", "INITR",
    "LOGR", "RWER", "INITER", "FLCRT", "FLDEL", "FLWR", "FLRD",
    "FLCMD", "FLSTRD",
}


def _constant_count(value: Any, default: int = 1) -> Tuple[int, bool]:
    match = _K_RE.fullmatch(str(value or "").strip())
    if not match:
        return max(1, int(default)), False
    return max(0, abs(int(match.group(1)))), True


def _out_timing(args: Sequence[Any]) -> Dict[str, Any]:
    address = str((args or [""])[0] or "").upper()
    if address.startswith("T"):
        return {
            "best_us": 0.71,
            "typical_us": 3.5,
            "worst_us": 11.6,
            "confidence": "medium",
            "source_pdf_page": 928,
            "basis": "timer range dependent",
        }
    if address.startswith("C"):
        return {
            "best_us": 0.71,
            "typical_us": 6.1,
            "worst_us": 9.5,
            "confidence": "medium",
            "source_pdf_page": 928,
            "basis": "counter range/state dependent",
        }
    if address.startswith("S"):
        return _entry(4.8, page=928)
    return {
        "best_us": 0.065,
        "typical_us": 0.129,
        "worst_us": 0.193,
        "confidence": "high",
        "source_pdf_page": 928,
    }


def estimate_instruction(
    opcode: Any,
    args: Optional[Sequence[Any]] = None,
    *,
    plc_model: str = "FX3U",
) -> Dict[str, Any]:
    """Estimate one lowered instruction and expose its evidence quality."""

    op = str(opcode or "").strip().upper()
    operands = list(args or [])
    if not str(plc_model or "").upper().startswith("FX3"):
        return {
            "opcode": op,
            "supported": False,
            "known": False,
            "best_us": None,
            "typical_us": None,
            "worst_us": None,
            "confidence": "unknown",
            "basis": "No model-specific official timing profile is bundled.",
        }

    if op == "OUT":
        timing = _out_timing(operands)
        known = True
    elif op in {"SET", "RST"}:
        target = str((operands or [""])[0] or "").upper()
        if target.startswith(("Y", "M")):
            timing = {
                "best_us": 0.065,
                "typical_us": 0.129,
                "worst_us": 0.193,
                "confidence": "high",
                "source_pdf_page": 928,
            }
        elif op == "RST" and target.startswith("D"):
            timing = _entry(5.4, 0.195, page=929)
        else:
            timing = _entry(4.8, 0.13, page=929, confidence="medium")
        known = True
    elif op in _LINEAR_FX3U:
        base, per_item, off, count_index, page = _LINEAR_FX3U[op]
        raw_count = operands[count_index] if count_index < len(operands) else ""
        count, exact_count = _constant_count(raw_count)
        on = base + per_item * count
        timing = {
            "best_us": min(float(off), on),
            "typical_us": round((float(off) + on) / 2.0, 6),
            "worst_us": max(float(off), on),
            "confidence": "high" if exact_count else "medium",
            "source_pdf_page": page,
            "basis": f"parameterized n={count}" + ("" if exact_count else " (default estimate)"),
            "parameter_count": count,
            "parameter_count_exact": exact_count,
        }
        known = True
    elif op.startswith(("LD=", "LD>", "LD<", "AND=", "AND>", "AND<", "OR=", "OR>", "OR<")):
        timing = _entry(1.48, 1.22, page=943)
        known = True
    elif op in _FX3U_FIXED:
        timing = copy.deepcopy(_FX3U_FIXED[op])
        known = True
    else:
        # The fallback is intentionally broad and visibly low confidence.  It
        # keeps a WCET upper estimate available without pretending the missing
        # opcode has an official table entry in this profile.
        worst = 5000.0 if op in _COMMUNICATION_OR_FLOW else 500.0
        timing = {
            "best_us": 0.0,
            "typical_us": worst / 10.0,
            "worst_us": worst,
            "confidence": "low",
            "source_pdf_page": None,
            "basis": "conservative unknown-instruction fallback",
        }
        known = False

    return {
        "opcode": op,
        "supported": True,
        "known": known,
        **timing,
    }


def scan_monitor_profile(plc_model: str = "FX3U", warning_ms: float = 15.0) -> Dict[str, Any]:
    model = str(plc_model or "").upper()
    if model.startswith("FX3"):
        return {
            "available": True,
            "enabled": True,
            "read_only": True,
            "devices": {
                "current": "D8010",
                "minimum": "D8011",
                "maximum": "D8012",
            },
            "unit_ms": 0.1,
            "warning_ms": float(warning_ms),
            "source": {
                "manual": "JY997D16601 Rev.R",
                "pdf_page": 869,
                "section": "37.2.5 Scan time (monitor) [D8010 to D8012]",
            },
        }
    return {
        "available": False,
        "enabled": False,
        "read_only": True,
        "devices": {},
        "unit_ms": None,
        "warning_ms": float(warning_ms),
        "reason": "No verified model-specific scan monitor profile is bundled.",
    }


def decode_scan_monitor_values(
    raw_values: Mapping[str, Any], plc_model: str = "FX3U"
) -> Dict[str, Optional[float]]:
    profile = scan_monitor_profile(plc_model)
    if not profile["available"]:
        return {"current_ms": None, "minimum_ms": None, "maximum_ms": None}
    unit = float(profile["unit_ms"])
    result: Dict[str, Optional[float]] = {}
    for label, address in profile["devices"].items():
        try:
            result[label + "_ms"] = round(float(raw_values[address]) * unit, 6)
        except (KeyError, TypeError, ValueError):
            result[label + "_ms"] = None
    return result


def _round_ms(value_us: float) -> float:
    return round(float(value_us) / 1000.0, 6)


def analyze_scan_timing(
    networks: Sequence[Mapping[str, Any]],
    *,
    plc_model: str = "FX3U",
    scan_budget_ms: Optional[float] = None,
    scan_warning_ms: float = 15.0,
    allocation: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Estimate best/typical/worst scan cost for deterministic PLC IR."""

    network_rows: List[Dict[str, Any]] = []
    total_best = total_typical = total_worst = 0.0
    known_count = instruction_count = 0
    unknown_opcodes = set()
    high_cost = []
    by_region_us: Dict[str, float] = {}
    referenced_x = set()
    referenced_y = set()

    for network in networks or []:
        best = typical = worst = 0.0
        instruction_rows = []
        for instruction in network.get("instructions") or []:
            if not isinstance(instruction, Mapping):
                continue
            estimate = estimate_instruction(
                instruction.get("op"),
                instruction.get("args") or [],
                plc_model=plc_model,
            )
            instruction_count += 1
            if estimate.get("known"):
                known_count += 1
            elif estimate.get("opcode"):
                unknown_opcodes.add(str(estimate["opcode"]))
            for raw_arg in instruction.get("args") or []:
                match = _DEVICE_RE.fullmatch(str(raw_arg or "").upper())
                if match and match.group(1).upper() == "X":
                    referenced_x.add(match.group(0).upper())
                elif match and match.group(1).upper() == "Y":
                    referenced_y.add(match.group(0).upper())
            if estimate.get("supported"):
                best += float(estimate.get("best_us") or 0.0)
                typical += float(estimate.get("typical_us") or 0.0)
                worst += float(estimate.get("worst_us") or 0.0)
                if float(estimate.get("worst_us") or 0.0) >= 100.0:
                    high_cost.append(
                        {
                            "network": str(network.get("id") or ""),
                            "opcode": estimate.get("opcode"),
                            "path": str(instruction.get("path") or ""),
                            "worst_us": estimate.get("worst_us"),
                            "confidence": estimate.get("confidence"),
                        }
                    )
            instruction_rows.append(
                {
                    "path": str(instruction.get("path") or ""),
                    **estimate,
                }
            )
        regions = list(network.get("regions") or ["CONTROL"])
        primary_region = str(regions[0] if regions else "CONTROL")
        by_region_us[primary_region] = by_region_us.get(primary_region, 0.0) + worst
        network_rows.append(
            {
                "network": str(network.get("id") or ""),
                "order": int(network.get("order") or 0),
                "primary_region": primary_region,
                "instruction_count": len(instruction_rows),
                "best_ms": _round_ms(best),
                "typical_ms": _round_ms(typical),
                "worst_ms": _round_ms(worst),
                "instructions": instruction_rows,
            }
        )
        total_best += best
        total_typical += typical
        total_worst += worst

    model_supported = str(plc_model or "").upper().startswith("FX3")
    if model_supported:
        # END includes I/O refresh.  Installed points are not represented in
        # ladder JSON; referenced X/Y points therefore form a documented lower
        # bound, never a claim about the physical rack size.
        overhead_us = 113.9 + 2.13 * len(referenced_x) + 3.25 * len(referenced_y)
        overhead_basis = "END formula using referenced X/Y points (lower bound)"
        total_best += overhead_us
        total_typical += overhead_us
        total_worst += overhead_us
    else:
        overhead_us = 0.0
        overhead_basis = "unavailable for selected model"

    budget_value = None
    try:
        if scan_budget_ms is not None and float(scan_budget_ms) > 0:
            budget_value = float(scan_budget_ms)
    except (TypeError, ValueError):
        budget_value = None
    warning_value = float(scan_warning_ms or 15.0)
    monitor = scan_monitor_profile(plc_model, warning_value)

    default_allocation = {
        "SAFETY": 0.10,
        "STATE_TRANSITION": 0.20,
        "STATE_OUTPUT": 0.20,
        "CONTROL": 0.10,
        "DIAGNOSTICS": 0.10,
        "MARGIN": 0.30,
    }
    allocation_values: Dict[str, float] = {}
    source_allocation = allocation if isinstance(allocation, Mapping) else {}
    if budget_value is not None:
        if source_allocation:
            for name, value in source_allocation.items():
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                allocation_values[str(name).upper()] = round(number, 6)
        else:
            allocation_values = {
                name: round(budget_value * ratio, 6)
                for name, ratio in default_allocation.items()
            }

    worst_ms = _round_ms(total_worst) if model_supported else None
    if budget_value is None:
        budget_status = "not_configured"
    elif worst_ms is None:
        budget_status = "unknown"
    elif worst_ms > budget_value:
        budget_status = "exceeded"
    else:
        budget_status = "within_budget"

    return {
        "schema_version": TIMING_ANALYSIS_SCHEMA_VERSION,
        "profile": (
            FX3U_TIMING_PROFILE_VERSION if model_supported else "unavailable"
        ),
        "supported": model_supported,
        "source": {
            "manual": "JY997D16601 Rev.R" if model_supported else "",
            "section": "Appendix B: Instruction Execution Time" if model_supported else "",
            "pdf_pages": [928, 929, 938, 939, 940, 941, 942, 943, 944, 945]
            if model_supported
            else [],
        },
        "estimate": {
            "best_ms": _round_ms(total_best) if model_supported else None,
            "typical_ms": _round_ms(total_typical) if model_supported else None,
            "worst_ms": worst_ms,
            "is_exact": False,
            "instruction_coverage": (
                round(known_count / instruction_count, 6) if instruction_count else 1.0
            ),
            "known_instruction_count": known_count,
            "instruction_count": instruction_count,
            "unknown_opcodes": sorted(unknown_opcodes),
            "uncertainty": [
                "Branch state and operand-dependent execution change scan time.",
                "Installed I/O point count is unavailable; END overhead uses referenced points.",
                "Interrupt/high-speed-counter coexistence is outside this static estimate.",
            ],
        },
        "cycle_overhead": {
            "estimated_us": round(overhead_us, 6),
            "basis": overhead_basis,
            "referenced_input_points": len(referenced_x),
            "referenced_output_points": len(referenced_y),
        },
        "networks": network_rows,
        "region_worst_ms": {
            name: _round_ms(value)
            for name, value in sorted(by_region_us.items())
        },
        "high_cost_instructions": sorted(
            high_cost,
            key=lambda item: (-float(item["worst_us"]), item["network"], item["path"]),
        ),
        "scan_monitor": monitor,
        "scan_budget": {
            "configured": budget_value is not None,
            "budget_ms": budget_value,
            "status": budget_status,
            "estimated_worst_ms": worst_ms,
            "allocation_ms": allocation_values,
        },
    }


def assess_pulse_capture(
    requirements: Sequence[Mapping[str, Any]],
    coverage: Sequence[Mapping[str, Any]],
    performance: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Assess explicitly specified physical-input pulses against scan bounds.

    This is intentionally a decision aid rather than proof that a pulse will
    be captured.  Static WCET is not exact and an interrupt label is not GX
    Works2 task metadata, so the result retains both limitations as evidence.
    """

    coverage_by_id = {
        str(item.get("requirement_id") or ""): item
        for item in coverage or []
        if isinstance(item, Mapping)
    }
    grouped: Dict[tuple, Dict[str, Any]] = {}
    for index, requirement in enumerate(requirements or []):
        if not isinstance(requirement, Mapping):
            continue
        try:
            pulse_width_ms = float(requirement.get("pulse_width_ms"))
        except (TypeError, ValueError):
            continue
        devices = sorted(
            {
                str(device or "").upper()
                for device in requirement.get("devices") or []
                if str(device or "").upper().startswith("X")
            }
        )
        if pulse_width_ms <= 0 or not devices:
            continue
        requirement_id = f"SEM{index + 1:03d}"
        item_coverage = coverage_by_id.get(requirement_id, {})
        key = (tuple(devices), round(pulse_width_ms, 9))
        record = grouped.setdefault(
            key,
            {
                "devices": devices,
                "pulse_width_ms": round(pulse_width_ms, 9),
                "requirement_ids": [],
                "semantics": [],
                "network_refs": [],
                "evidence": [],
                "interrupt_requested": False,
                "interrupt_path_declared": False,
            },
        )
        semantic = str(requirement.get("semantic") or "")
        record["requirement_ids"].append(requirement_id)
        if semantic and semantic not in record["semantics"]:
            record["semantics"].append(semantic)
        for network in item_coverage.get("network_refs") or []:
            network = str(network or "")
            if network and network not in record["network_refs"]:
                record["network_refs"].append(network)
        evidence = str(requirement.get("evidence") or "").strip()
        if evidence and evidence not in record["evidence"]:
            record["evidence"].append(evidence)
        if semantic == "INTERRUPT":
            record["interrupt_requested"] = True
            if item_coverage.get("status") == "satisfied":
                record["interrupt_path_declared"] = True

    estimate = performance.get("estimate") if isinstance(performance, Mapping) else {}
    budget = performance.get("scan_budget") if isinstance(performance, Mapping) else {}
    estimate = estimate if isinstance(estimate, Mapping) else {}
    budget = budget if isinstance(budget, Mapping) else {}
    try:
        worst_ms = float(estimate.get("worst_ms"))
    except (TypeError, ValueError):
        worst_ms = None
    try:
        budget_ms = float(budget.get("budget_ms"))
    except (TypeError, ValueError):
        budget_ms = None

    assessments = []
    for record in grouped.values():
        bounds = []
        if worst_ms is not None and worst_ms > 0:
            bounds.append(("static_worst_scan", worst_ms))
        if budget_ms is not None and budget_ms > 0:
            bounds.append(("configured_scan_budget", budget_ms))
        comparison_ms = max((value for _, value in bounds), default=None)
        pulse_width_ms = float(record["pulse_width_ms"])
        if comparison_ms is None:
            status = "unknown"
            decision = "measure_scan_before_capture_decision"
        elif comparison_ms >= pulse_width_ms:
            status = "pulse_loss_risk"
            decision = (
                "verify_interrupt_task_configuration"
                if record["interrupt_path_declared"]
                else "consider_verified_interrupt_or_high_speed_capture"
            )
        else:
            status = "within_known_static_bound"
            decision = "cyclic_scan_capture_possible_but_measure_to_confirm"
        assessments.append(
            {
                **record,
                "status": status,
                "decision": decision,
                "estimated_worst_scan_ms": worst_ms,
                "scan_budget_ms": budget_ms,
                "comparison_bound_ms": comparison_ms,
                "comparison_basis": [name for name, _ in bounds],
                "instruction_coverage": estimate.get("instruction_coverage"),
                "is_guarantee": False,
            }
        )
    return sorted(
        assessments,
        key=lambda item: (tuple(item["devices"]), item["pulse_width_ms"]),
    )


__all__ = [
    "FX3U_TIMING_PROFILE_VERSION",
    "TIMING_ANALYSIS_SCHEMA_VERSION",
    "analyze_scan_timing",
    "assess_pulse_capture",
    "decode_scan_monitor_values",
    "estimate_instruction",
    "scan_monitor_profile",
]
