"""Evidence-bound Debug/Patch/regression loop for PLC IR versions.

The language model is deliberately kept outside the execution boundary.  It
may diagnose a persisted failed simulator run and propose replacements for a
small allow-list of networks.  This module validates that proposal, renders a
candidate version, imports it through the high-level GX Works2 service, runs a
full regression suite, and either activates the candidate or restores the
base version.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set
import uuid
from shared.i18n import language_scoped

from plc.artifacts import render_candidate_artifacts
from plc.errors import DebugLoopError
from knowledge.retriever import retrieve_knowledge
from plc.ir import (
    PLCIRValidationError,
    apply_network_patch,
    canonical_sha256,
    ir_to_ladder,
    validate_plc_ir,
)
from plc.st_renderer import (
    ST_RENDERER_SCHEMA_VERSION,
    render_plc_ir_to_st,
    validate_st_traceability,
)
from plc.static_analysis import STATIC_ANALYSIS_SCHEMA_VERSION, trace_upstream
from plc.timing import TIMING_ANALYSIS_SCHEMA_VERSION
from plc.semantics import SEMANTICS_SCHEMA_VERSION
from simulator.models import normalize_test_suite
from simulator.service import SimulatorRegressionService
from simulator.verification import (
    SimulatorEvidenceError, blocked_verification, evaluate_suite_result,
    execution_binding, require_matching_binding,
)


DEBUG_EVIDENCE_SCHEMA_VERSION = 1
DEBUG_DIAGNOSIS_SCHEMA_VERSION = 1
DEBUG_PATCH_SCHEMA_VERSION = 1
DEBUG_LOOP_SCHEMA_VERSION = 1
MAX_RELATED_NETWORKS = 40
MAX_TRACE_EVENTS = 120




def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _mapping(value: Any) -> Dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return copy.deepcopy(dict(value))
    raise TypeError("expected an object result")


def _device(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text or not text[0].isalpha() or not any(ch.isdigit() for ch in text):
        return ""
    return text


def _network_sort_key(value: str, network_order: Mapping[str, int]):
    return (int(network_order.get(value, 1_000_000)), value)


def _suite_by_name(suite: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {
        str(item.get("name") or ""): item
        for item in suite.get("tests", []) or []
        if isinstance(item, Mapping)
    }


def _failure_records(
    suite: Mapping[str, Any], result: Mapping[str, Any]
) -> tuple[List[Dict[str, Any]], Set[str], Set[str], Dict[str, List[int]]]:
    """Return normalized failures, affected devices and failure times by test."""

    tests = _suite_by_name(suite)
    failures: List[Dict[str, Any]] = []
    devices: Set[str] = set()
    stimulus_devices: Set[str] = set()
    failure_times: Dict[str, List[int]] = {}
    for test_result in result.get("results", []) or []:
        if not isinstance(test_result, Mapping):
            continue
        test_name = str(test_result.get("name") or "")
        case = tests.get(test_name, {})
        for index, assertion in enumerate(test_result.get("assertions", []) or []):
            if not isinstance(assertion, Mapping) or assertion.get("passed") is not False:
                continue
            address = _device(assertion.get("address"))
            if address:
                devices.add(address)
            at_ms = int(assertion.get("at_ms") or 0)
            if isinstance(case, Mapping):
                stimulus_devices.update(
                    _device(address) for address in (case.get("initial") or {})
                )
                for step in case.get("steps", []) or []:
                    if isinstance(step, Mapping) and int(step.get("at_ms") or 0) <= at_ms:
                        stimulus_devices.update(
                            _device(address) for address in (step.get("set") or {})
                        )
                stimulus_devices.update(
                    _device(item.get("device"))
                    for item in case.get("fault_injections", []) or []
                    if isinstance(item, Mapping)
                    and int(item.get("at_ms") or 0) <= at_ms
                )
            failure_times.setdefault(test_name, []).append(at_ms)
            failures.append(
                {
                    "ref": f"assertion:{test_name}:{assertion.get('step_id') or index}:{address}",
                    "kind": "assertion",
                    "test": test_name,
                    "step_id": str(assertion.get("step_id") or ""),
                    "at_ms": at_ms,
                    "address": address,
                    "detail": str(assertion.get("detail") or ""),
                    "wait_for": bool(assertion.get("wait_for")),
                }
            )
        invariants = case.get("invariants", []) if isinstance(case, Mapping) else []
        for index, violation in enumerate(
            test_result.get("invariant_violations", []) or []
        ):
            if not isinstance(violation, Mapping):
                continue
            invariant_index = int(violation.get("invariant_index") or 0)
            invariant = (
                invariants[invariant_index]
                if 0 <= invariant_index < len(invariants)
                and isinstance(invariants[invariant_index], Mapping)
                else {}
            )
            invariant_devices = {
                _device(item) for item in invariant.get("devices", []) or []
            }
            if invariant.get("device"):
                invariant_devices.add(_device(invariant.get("device")))
            invariant_devices.discard("")
            devices.update(invariant_devices)
            at_ms = int(violation.get("at_ms") or 0)
            if isinstance(case, Mapping):
                stimulus_devices.update(
                    _device(address) for address in (case.get("initial") or {})
                )
                for step in case.get("steps", []) or []:
                    if isinstance(step, Mapping) and int(step.get("at_ms") or 0) <= at_ms:
                        stimulus_devices.update(
                            _device(address) for address in (step.get("set") or {})
                        )
            failure_times.setdefault(test_name, []).append(at_ms)
            failures.append(
                {
                    "ref": f"invariant:{test_name}:{invariant_index}:{index}",
                    "kind": "invariant",
                    "test": test_name,
                    "at_ms": at_ms,
                    "type": str(violation.get("type") or invariant.get("type") or ""),
                    "name": str(violation.get("name") or invariant.get("name") or ""),
                    "devices": sorted(invariant_devices),
                    "message": str(violation.get("message") or ""),
                    "observed": {
                        address: (violation.get("values") or {}).get(address)
                        for address in sorted(invariant_devices)
                    },
                }
            )
    stimulus_devices.discard("")
    return failures, devices, stimulus_devices, failure_times


def _relevant_trace(
    result: Mapping[str, Any],
    devices: Set[str],
    failure_times: Mapping[str, Sequence[int]],
) -> List[Dict[str, Any]]:
    candidates: List[tuple[int, int, Dict[str, Any]]] = []
    for test_result in result.get("results", []) or []:
        if not isinstance(test_result, Mapping):
            continue
        name = str(test_result.get("name") or "")
        times = list(failure_times.get(name) or [])
        if not times:
            continue
        for event in test_result.get("trace", []) or []:
            if not isinstance(event, Mapping):
                continue
            at_ms = int(event.get("at_ms") or 0)
            values = event.get("values") if isinstance(event.get("values"), Mapping) else {}
            involved = devices.intersection(str(key).upper() for key in values)
            distance = min(abs(at_ms - failed_at) for failed_at in times)
            important_event = str(event.get("event") or "") in {
                "initial_write",
                "write",
                "expect",
                "wait_for",
                "runner_error",
            }
            if distance > 150 and not (important_event and involved):
                continue
            candidates.append(
                (
                    distance,
                    0 if important_event else 1,
                    {
                    "test": name,
                    "at_ms": at_ms,
                    "event": str(event.get("event") or ""),
                    "step_id": str(event.get("step_id") or ""),
                    "values": {
                        address: values.get(address)
                        for address in sorted(devices)
                        if address in values
                    },
                    "scan_monitor": copy.deepcopy(
                        event.get("scan_monitor")
                        if isinstance(event.get("scan_monitor"), Mapping)
                        else {}
                    ),
                    },
                )
            )
    candidates.sort(key=lambda item: (item[0], item[1], item[2]["test"], item[2]["at_ms"]))
    selected = [item[2] for item in candidates[:MAX_TRACE_EVENTS]]
    selected.sort(key=lambda item: (item["test"], item["at_ms"], item["event"]))
    return selected


def _related_networks(
    program: Mapping[str, Any], targets: Iterable[str]
) -> tuple[List[str], List[Dict[str, Any]], Set[str]]:
    analysis = program.get("analysis") or {}
    graph = analysis.get("dependency_graph") or {}
    traced_devices: Set[str] = set()
    traces: List[Dict[str, Any]] = []
    network_ids: Set[str] = set()
    for target in sorted(set(targets)):
        try:
            traced = trace_upstream(analysis, target, max_depth=8)
        except ValueError:
            continue
        traces.append(traced)
        traced_devices.update(traced.get("devices") or [])
        network_ids.update(
            str(edge.get("network") or "")
            for edge in traced.get("edges", []) or []
            if isinstance(edge, Mapping)
        )
    for address in traced_devices:
        for key in ("writers", "readers"):
            for row in (graph.get(key) or {}).get(address, []) or []:
                if isinstance(row, Mapping) and row.get("network"):
                    network_ids.add(str(row["network"]))
    for finding in analysis.get("findings", []) or []:
        if not isinstance(finding, Mapping):
            continue
        if traced_devices.intersection(finding.get("addresses") or []):
            network_ids.update(str(item) for item in finding.get("network_refs") or [])
    network_ids.discard("")
    order = {
        str(item.get("id") or ""): int(item.get("order") or 0)
        for item in program.get("networks", []) or []
        if isinstance(item, Mapping)
    }
    ordered = sorted(network_ids, key=lambda item: _network_sort_key(item, order))
    if len(ordered) > MAX_RELATED_NETWORKS:
        raise DebugLoopError(
            f"故障影响范围达到 {len(ordered)} 个网络，超过自动局部修复上限 {MAX_RELATED_NETWORKS}。"
        )
    return ordered, traces, traced_devices


def build_failure_evidence(
    program: Mapping[str, Any],
    simulator_run: Mapping[str, Any],
    *,
    project_id: str,
    version_id: str,
    retriever: Optional[Callable[..., Sequence[Mapping[str, Any]]]] = None,
    top_k: int = 6,
) -> Dict[str, Any]:
    """Build the only payload a Debug Agent is allowed to inspect."""

    validate_plc_ir(program, validate_ladder=False)
    if not isinstance(simulator_run, Mapping):
        raise DebugLoopError("调试证据必须来自已保存的仿真记录。")
    binding = simulator_run.get("binding")
    suite = simulator_run.get("suite")
    result = simulator_run.get("result")
    if not all(isinstance(item, Mapping) for item in (binding, suite, result)):
        raise DebugLoopError("仿真记录缺少版本绑定、测试套件或运行结果。")
    expected_hash = canonical_sha256(program)
    expected_revision = int(program.get("revision") or 0)
    expected_binding = {
        "project_id": str(project_id),
        "version_id": str(version_id),
        "revision": expected_revision,
        "ir_sha256": expected_hash,
    }
    mismatches = [
        key
        for key, expected in expected_binding.items()
        if binding.get(key) != expected
    ]
    if mismatches:
        raise DebugLoopError(
            "仿真证据不属于当前程序版本：" + ", ".join(mismatches)
        )
    if str(result.get("status") or "") != "failed":
        raise DebugLoopError(
            "只有断言或不变量失败的仿真结果才能触发程序修复；环境不可用和运行器错误不得触发补丁。"
        )
    normalized_suite = normalize_test_suite(
        suite, plc_model=str(program.get("plc", {}).get("cpu") or "FX3U")
    )
    verification = evaluate_suite_result(normalized_suite, result)
    if (
        binding.get("binding_scope") != "execution_snapshot"
        or binding.get("suite_sha256") != canonical_sha256(normalized_suite)
        or binding.get("result_sha256") != canonical_sha256(result)
        or verification != simulator_run.get("verification")
        or not verification["program_repair_allowed"]
    ):
        raise DebugLoopError("仿真证据未通过完整性与执行验收，请重新测试后再生成修复方案。")
    failures, affected_devices, stimulus_devices, failure_times = _failure_records(
        normalized_suite, result
    )
    if not failures or not affected_devices:
        raise DebugLoopError("失败结果中没有可定位的断言或不变量设备。")
    related, dependency_traces, traced_devices = _related_networks(
        program, affected_devices
    )
    if not related:
        raise DebugLoopError("依赖图中找不到与失败设备有关的可修改网络。")
    by_id = {
        str(item.get("id") or ""): item
        for item in program.get("networks", []) or []
        if isinstance(item, Mapping)
    }
    excerpts = [
        {
            "id": network_id,
            "order": by_id[network_id].get("order"),
            "comment": by_id[network_id].get("comment", ""),
            "instructions": copy.deepcopy(by_id[network_id].get("instructions") or []),
            "reads": list(by_id[network_id].get("reads") or []),
            "writes": list(by_id[network_id].get("writes") or []),
            "ladder": copy.deepcopy(by_id[network_id].get("ladder") or {}),
        }
        for network_id in related
        if network_id in by_id
    ]
    query_parts = [
        str(program.get("plc", {}).get("cpu") or "FX3U"),
        "PLC 仿真调试 断言失败",
    ]
    for failure in failures:
        query_parts.extend(
            str(failure.get(key) or "")
            for key in ("address", "type", "message", "detail")
        )
    for excerpt in excerpts:
        query_parts.extend(
            " ".join(
                [str(item.get("op") or "")]
                + [str(arg) for arg in item.get("args", []) or []]
            )
            for item in excerpt["instructions"]
        )
    knowledge_query = "\n".join(item for item in query_parts if item)[:24_000]
    lookup = retriever or retrieve_knowledge
    try:
        knowledge = list(
            lookup(
                knowledge_query,
                plc_model=str(program.get("plc", {}).get("cpu") or "FX3U"),
                task_type="debug",
                top_k=top_k,
                char_budget=8_000,
            )
            or []
        )
    except Exception:
        knowledge = []
    allowed_refs = [item["ref"] for item in failures]
    allowed_refs.extend("network:" + item for item in related)
    allowed_refs.extend(
        "knowledge:" + str(item.get("id") or "")
        for item in knowledge
        if isinstance(item, Mapping) and item.get("id")
    )
    return {
        "schema_version": DEBUG_EVIDENCE_SCHEMA_VERSION,
        "binding": copy.deepcopy(dict(binding)),
        "verification": verification,
        "plc": copy.deepcopy(dict(program.get("plc") or {})),
        "program_name": str(program.get("program_name") or "MAIN"),
        "failures": failures,
        "affected_devices": sorted(affected_devices),
        "stimulus_devices": sorted(stimulus_devices),
        "traced_devices": sorted(traced_devices),
        "allowed_patch_devices": sorted(
            traced_devices.union(stimulus_devices).union(affected_devices)
        ),
        "device_trace": _relevant_trace(
            result,
            traced_devices.union(stimulus_devices).union(affected_devices),
            failure_times,
        ),
        "scan_monitor": [
            {
                "test": str(item.get("name") or ""),
                **copy.deepcopy(dict(item.get("scan_monitor") or {})),
            }
            for item in result.get("results", []) or []
            if isinstance(item, Mapping)
            and isinstance(item.get("scan_monitor"), Mapping)
        ],
        "dependency_traces": dependency_traces,
        "related_networks": related,
        "network_excerpts": excerpts,
        "static_findings": [
            copy.deepcopy(dict(item))
            for item in (program.get("analysis") or {}).get("findings", []) or []
            if isinstance(item, Mapping)
            and (
                traced_devices.intersection(item.get("addresses") or [])
                or set(related).intersection(item.get("network_refs") or [])
            )
        ],
        "knowledge": knowledge,
        "allowed_evidence_refs": list(dict.fromkeys(allowed_refs)),
    }


def normalize_debug_diagnosis(
    diagnosis: Mapping[str, Any], evidence: Mapping[str, Any]
) -> Dict[str, Any]:
    """Reject diagnoses that are not anchored to supplied evidence."""

    if not isinstance(diagnosis, Mapping):
        raise DebugLoopError("诊断结果必须是 JSON 对象。")
    schema = diagnosis.get("schema_version", DEBUG_DIAGNOSIS_SCHEMA_VERSION)
    if schema != DEBUG_DIAGNOSIS_SCHEMA_VERSION:
        raise DebugLoopError("不支持的诊断 schema_version。")
    root_cause = str(diagnosis.get("root_cause") or "").strip()
    recommended = str(diagnosis.get("recommended_change") or "").strip()
    if not root_cause or not recommended:
        raise DebugLoopError("诊断必须包含根因和明确的建议修改。")
    related = set(evidence.get("related_networks") or [])
    affected = [str(item) for item in diagnosis.get("affected_networks", []) or []]
    affected = list(dict.fromkeys(affected))
    if not affected or not set(affected).issubset(related):
        raise DebugLoopError("诊断只能引用证据中列出的相关网络。")
    allowed_refs = set(evidence.get("allowed_evidence_refs") or [])
    refs = [str(item) for item in diagnosis.get("evidence_refs", []) or []]
    refs = list(dict.fromkeys(refs))
    if not refs or not set(refs).issubset(allowed_refs):
        raise DebugLoopError("诊断必须引用有效的失败、网络或知识证据编号。")
    confidence_value = diagnosis.get("confidence", 0.0)
    try:
        confidence = float(confidence_value)
    except (TypeError, ValueError):
        labels = {"low": 0.35, "medium": 0.65, "high": 0.9}
        confidence = labels.get(str(confidence_value).strip().lower(), -1.0)
    if not 0.0 <= confidence <= 1.0:
        raise DebugLoopError("诊断置信度必须在 0 到 1 之间。")
    return {
        "schema_version": DEBUG_DIAGNOSIS_SCHEMA_VERSION,
        "root_cause": root_cause,
        "confidence": confidence,
        "affected_networks": affected,
        "evidence_refs": refs,
        "recommended_change": recommended,
    }


def normalize_and_apply_debug_patch(
    program: Mapping[str, Any],
    patch: Mapping[str, Any],
    evidence: Mapping[str, Any],
    diagnosis: Mapping[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Apply an LLM patch only after a strict network allow-list check."""

    if not isinstance(patch, Mapping):
        raise DebugLoopError("补丁必须是 JSON 对象。")
    if patch.get("schema_version", DEBUG_PATCH_SCHEMA_VERSION) != DEBUG_PATCH_SCHEMA_VERSION:
        raise DebugLoopError("不支持的补丁 schema_version。")
    expected_revision = int(program.get("revision") or 0)
    expected_hash = canonical_sha256(program)
    if patch.get("base_revision") != expected_revision:
        raise DebugLoopError("补丁的基础 revision 与当前版本不一致。")
    if str(patch.get("base_ir_sha256") or "") != expected_hash:
        raise DebugLoopError("补丁的基础 IR 哈希与当前版本不一致。")
    if patch.get("target_revision", expected_revision + 1) != expected_revision + 1:
        raise DebugLoopError("调试补丁只能生成紧邻的下一个 revision。")
    operations = patch.get("operations")
    if not isinstance(operations, list) or not operations:
        raise DebugLoopError("补丁没有网络修改操作。")
    allowed = set(evidence.get("related_networks") or []).intersection(
        diagnosis.get("affected_networks") or []
    )
    touched: Set[str] = set()
    normalized_operations = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, Mapping):
            raise DebugLoopError(f"补丁操作 {index + 1} 不是对象。")
        if str(operation.get("operation") or "").strip().lower() != "modify_network":
            raise DebugLoopError("Debug/Patch 只允许 modify_network，不允许增删网络。")
        network = str(operation.get("network") or "").strip()
        if network not in allowed:
            raise DebugLoopError(f"补丁试图修改未获授权的网络 {network}。")
        if network in touched:
            raise DebugLoopError(f"同一补丁不能重复修改网络 {network}。")
        touched.add(network)
        normalized_operations.append(copy.deepcopy(dict(operation)))
    comment_updates = patch.get("device_comments") or {}
    if not isinstance(comment_updates, Mapping):
        raise DebugLoopError("device_comments 必须是对象。")
    allowed_devices = set(evidence.get("allowed_patch_devices") or [])
    invalid_comments = {
        str(address).upper() for address in comment_updates if str(address).upper() not in allowed_devices
    }
    if invalid_comments:
        raise DebugLoopError(
            "补丁只能更新故障依赖范围内的注释：" + ", ".join(sorted(invalid_comments))
        )
    normalized_patch = {
        "schema_version": DEBUG_PATCH_SCHEMA_VERSION,
        "base_revision": expected_revision,
        "base_ir_sha256": expected_hash,
        "target_revision": expected_revision + 1,
        "operations": normalized_operations,
        "device_comments": copy.deepcopy(dict(comment_updates)),
    }
    try:
        candidate = apply_network_patch(program, normalized_patch)
    except (PLCIRValidationError, TypeError, ValueError) as error:
        raise DebugLoopError(f"局部补丁未通过 PLC IR 校验：{error}") from error
    if canonical_sha256(candidate) == expected_hash:
        raise DebugLoopError("补丁没有改变程序。")
    before = {
        str(item.get("id") or ""): item
        for item in program.get("networks", []) or []
        if isinstance(item, Mapping)
    }
    after = {
        str(item.get("id") or ""): item
        for item in candidate.get("networks", []) or []
        if isinstance(item, Mapping)
    }
    if set(before) != set(after):
        raise DebugLoopError("局部补丁不得增加或删除网络。")
    changed = {network for network in before if before[network] != after[network]}
    if changed != touched:
        raise DebugLoopError(
            "补丁实际变更范围与声明不一致：" + ", ".join(sorted(changed))
        )
    new_reads = {
        address
        for network in changed
        for address in set(after[network].get("reads") or []).difference(
            before[network].get("reads") or []
        )
    }
    new_writes = {
        address
        for network in changed
        for address in set(after[network].get("writes") or []).difference(
            before[network].get("writes") or []
        )
    }
    invalid_reads = new_reads.difference(allowed_devices)
    if invalid_reads:
        raise DebugLoopError(
            "补丁引入了失败证据之外的新读取地址：" + ", ".join(sorted(invalid_reads))
        )
    original_writes = {
        address
        for network in program.get("networks", []) or []
        if isinstance(network, Mapping)
        for address in network.get("writes", []) or []
    }
    invalid_writes = new_writes.difference(original_writes).difference(
        evidence.get("affected_devices") or []
    )
    if invalid_writes:
        raise DebugLoopError(
            "补丁引入了未经证据证明的新写入地址：" + ", ".join(sorted(invalid_writes))
        )
    errors = int(((candidate.get("analysis") or {}).get("counts") or {}).get("error", 0))
    if errors:
        raise DebugLoopError(f"候选程序仍有 {errors} 个静态分析错误，禁止导入。")
    return candidate, normalized_patch




def _version_metadata(
    program: Mapping[str, Any],
    artifacts: Mapping[str, str],
    *,
    base_version: Mapping[str, Any],
    base_version_id: str,
    source_run_id: str,
) -> Dict[str, Any]:
    st_text = render_plc_ir_to_st(program)
    timing = (program.get("timing") or {}).get("performance") or {}
    logic = program.get("logic") or {}
    analysis = program.get("analysis") or {}
    return {
        "target_mode": "ladder",
        "plc_model": str(program.get("plc", {}).get("cpu") or "FX3U"),
        "program_name": str(program.get("program_name") or "MAIN"),
        "revision": int(program.get("revision") or 0),
        "summary": "仿真失败后的局部调试候选版本",
        "ir_schema_version": program.get("schema_version"),
        "ir_sha256": canonical_sha256(program),
        "ladder_sha256": (program.get("source") or {}).get("ladder_sha256"),
        "st_from_ir_sha256": hashlib.sha256(st_text.encode("utf-8")).hexdigest(),
        "st_renderer_schema_version": ST_RENDERER_SCHEMA_VERSION,
        "semantic_schema_version": SEMANTICS_SCHEMA_VERSION,
        "semantic_summary": {
            "requirements": copy.deepcopy(logic.get("requirements") or []),
            "coverage": copy.deepcopy((program.get("timing") or {}).get("coverage") or []),
            "state_machine_count": len(logic.get("state_machines") or []),
        },
        "static_analysis_schema_version": STATIC_ANALYSIS_SCHEMA_VERSION,
        "static_analysis_summary": {
            "counts": copy.deepcopy(analysis.get("counts") or {}),
            "rules_checked": list(analysis.get("rules_checked") or []),
            "dependency_nodes": len((analysis.get("dependency_graph") or {}).get("nodes") or []),
            "dependency_edges": len((analysis.get("dependency_graph") or {}).get("device_edges") or []),
        },
        "timing_analysis_schema_version": TIMING_ANALYSIS_SCHEMA_VERSION,
        "timing_summary": {
            "profile": timing.get("profile"),
            "estimate": copy.deepcopy(timing.get("estimate") or {}),
            "scan_budget": copy.deepcopy(timing.get("scan_budget") or {}),
            "scan_monitor": copy.deepcopy(timing.get("scan_monitor") or {}),
        },
        "artifacts": dict(artifacts),
        "validation": {"status": "passed", "messages": ["局部补丁和静态分析已通过"]},
        "confirmed_spec_snapshot": copy.deepcopy(base_version.get("confirmed_spec_snapshot")),
        "confirmed_spec_hash": base_version.get("confirmed_spec_hash"),
        "parent_version_id": base_version_id,
        "source_simulator_run_id": source_run_id,
        "lifecycle_status": "candidate_pending",
    }


def _import_result_payload(result: Any) -> Dict[str, Any]:
    payload = _mapping(result)
    payload["success"] = bool(payload.get("success"))
    error_code = payload.get("error_code")
    if hasattr(error_code, "value"):
        payload["error_code"] = error_code.value
    return payload


def _import_may_have_changed(result: Mapping[str, Any]) -> bool:
    details = result.get("details") or {}
    gx = details.get("gxworks2") if isinstance(details, Mapping) else None
    explicit_change_flags = [result.get("program_changed")]
    if isinstance(gx, Mapping):
        explicit_change_flags.append(gx.get("program_changed"))
    if any(flag is True for flag in explicit_change_flags):
        return True
    if any(flag is False for flag in explicit_change_flags):
        return False

    messages = [str(result.get("message") or "")]
    if isinstance(gx, Mapping):
        messages.append(str(gx.get("message") or ""))
    message = " ".join(messages)
    # GX Works2 can reject Read from CSV before touching the program when the
    # target MDI editor is in read-only mode.  Stage="import" only identifies
    # where the call failed; it is not evidence that a physical write began.
    if not result.get("success") and re.search(
        r"(?:写入|改写).{0,16}(?:禁止|不允许)|只读|read[ -]?only|"
        r"(?:write|writing).{0,24}(?:prohibited|disabled|not allowed)",
        message,
        re.I,
    ):
        return False
    return bool(
        result.get("success")
        or (isinstance(gx, Mapping) and gx.get("success"))
        or str(result.get("stage") or "")
        in {
            "import",
            "verify",
            "import_comments",
            "verify_comments",
            "complete",
            "complete_with_warning",
        }
    )


def _import_is_complete(result: Mapping[str, Any]) -> bool:
    """P7 requires both physical import and a trustworthy protection baseline."""

    return bool(result.get("success")) and not result.get("error_code")


class DebugPatchLoopService:
    """Run one user-approved, immutable, version-bound repair attempt."""

    def __init__(
        self,
        store,
        *,
        importer: Optional[Callable[..., Any]] = None,
        simulator_service: Optional[SimulatorRegressionService] = None,
        simulator_backend: Any = None,
        simulator_preparer: Any = None,
        retriever: Optional[Callable[..., Sequence[Mapping[str, Any]]]] = None,
    ):
        self.store = store
        self.importer = importer
        self.simulator_preparer = simulator_preparer
        self.simulator_service = simulator_service or SimulatorRegressionService(
            store,
            backend=simulator_backend,
            preparer=simulator_preparer,
        )
        self.retriever = retriever

    @language_scoped
    def prepare_plan(
        self,
        project_id: str,
        base_version_id: str,
        run_id: str,
        diagnosis: Mapping[str, Any],
        patch: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Validate a proposed diagnosis/patch without writing a new version."""

        project = self.store.get_project(project_id)
        if not project:
            raise KeyError(project_id)
        if project.get("active_version_id") != base_version_id:
            raise DebugLoopError("只能调试当前启用版本，防止基于历史版本覆盖工程。")
        base_version = self.store.get_version(project_id, base_version_id)
        program = self.store.load_program_ir(project_id, base_version_id)
        run = self.store.load_simulator_run(project_id, base_version_id, run_id)
        if not isinstance(base_version, Mapping) or not isinstance(program, Mapping):
            raise DebugLoopError("当前版本没有可调试的 PLC IR。")
        if not isinstance(run, Mapping):
            raise DebugLoopError("找不到指定的仿真失败记录。")
        evidence = build_failure_evidence(
            program,
            run,
            project_id=project_id,
            version_id=base_version_id,
            retriever=self.retriever,
        )
        normalized_diagnosis = normalize_debug_diagnosis(diagnosis, evidence)
        candidate, normalized_patch = normalize_and_apply_debug_patch(
            program, patch, evidence, normalized_diagnosis
        )
        return {
            "schema_version": DEBUG_LOOP_SCHEMA_VERSION,
            "project_id": project_id,
            "base_version_id": base_version_id,
            "source_run_id": run_id,
            "evidence": evidence,
            "diagnosis": normalized_diagnosis,
            "patch": normalized_patch,
            "candidate_ir": candidate,
            "regression_suite": copy.deepcopy(run["suite"]),
        }

    def _import_version(
        self,
        project_id: str,
        version_id: str,
        *,
        phase: str,
        rollback_expected_current_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        if self.importer is None:
            raise DebugLoopError("未配置 GX Works2 高层导入服务，不能形成调试闭环。")
        version = self.store.get_version(project_id, version_id)
        if not isinstance(version, Mapping):
            raise DebugLoopError(f"找不到版本 {version_id}。")
        artifacts = version.get("artifacts") or {}
        version_dir = self.store.version_dir(project_id, version_id)
        program_csv = version_dir / str(artifacts.get("program_csv") or "")
        comment_csv = version_dir / str(artifacts.get("comment_csv") or "")
        result = self.importer(
            program_csv,
            comment_csv_path=comment_csv,
            start_if_needed=False,
            import_context={
                "project_id": project_id,
                "version_id": version_id,
                "revision": version.get("revision"),
                "ir_sha256": version.get("ir_sha256"),
                "debug_phase": phase,
            },
            rollback_expected_current_sha256=rollback_expected_current_sha256,
        )
        return _import_result_payload(result)

    def execute_approved_plan(self, plan: Mapping[str, Any]) -> Dict[str, Any]:
        """Execute a previously validated plan after explicit caller approval."""

        if not isinstance(plan, Mapping) or plan.get("schema_version") != DEBUG_LOOP_SCHEMA_VERSION:
            raise DebugLoopError("无效的调试计划。")
        project_id = str(plan.get("project_id") or "")
        base_version_id = str(plan.get("base_version_id") or "")
        run_id = str(plan.get("source_run_id") or "")
        # Rebuild the plan from immutable inputs to catch stale/tampered data.
        checked = self.prepare_plan(
            project_id,
            base_version_id,
            run_id,
            plan.get("diagnosis") or {},
            plan.get("patch") or {},
        )
        if canonical_sha256(checked["candidate_ir"]) != canonical_sha256(
            plan.get("candidate_ir") or {}
        ):
            raise DebugLoopError("待执行计划已被修改，请重新生成调试方案。")
        base_version = self.store.get_version(project_id, base_version_id)
        attempt_id = "dbg_" + uuid.uuid4().hex[:16]
        candidate_version_id = None
        candidate_import: Dict[str, Any] = {}
        regression: Dict[str, Any] = {}
        rollback: Dict[str, Any] = {"required": False, "attempted": False}
        status = "error"
        message = "调试闭环未完成。"
        candidate_completed = False
        try:
            candidate_version_id, output_dir = self.store.prepare_version(project_id)
            artifacts = render_candidate_artifacts(checked["candidate_ir"], output_dir)
            metadata = _version_metadata(
                checked["candidate_ir"],
                artifacts,
                base_version=base_version,
                base_version_id=base_version_id,
                source_run_id=run_id,
            )
            self.store.complete_version(
                project_id,
                candidate_version_id,
                metadata,
                activate=False,
            )
            candidate_completed = True
            if self.simulator_preparer is not None:
                stopped = self.simulator_preparer.stop_if_running()
                if not stopped.success:
                    raise DebugLoopError(
                        "候选程序导入前无法停止 GX Simulator2：" + stopped.message
                    )
            candidate_import = self._import_version(
                project_id, candidate_version_id, phase="candidate"
            )
            if not _import_is_complete(candidate_import):
                rollback["required"] = _import_may_have_changed(candidate_import)
                status = "import_failed"
                message = "候选程序未能完整导入 GX Works2。"
            else:
                expected = execution_binding(
                    project_id, candidate_version_id,
                    checked["candidate_ir"], checked["regression_suite"],
                )
                execution = self.simulator_service.run_version_suite(
                    project_id,
                    candidate_version_id,
                    checked["regression_suite"],
                    expected_binding=expected,
                )
                regression = {
                    "record": copy.deepcopy(execution.get("record") or {}),
                    "result": copy.deepcopy(execution.get("result") or {}),
                }
                # A callback's "passed" is not acceptance. Reopen the indexed
                # artifacts and check the exact candidate and approved suite.
                regression_run_id = regression["record"].get("run_id")
                if not regression_run_id:
                    raise SimulatorEvidenceError("evidence_invalid", "候选回归缺少已保存的仿真记录。")
                saved = self.store.load_simulator_run(
                    project_id, candidate_version_id, regression_run_id
                )
                if not saved:
                    raise SimulatorEvidenceError("evidence_invalid", "候选回归的仿真记录不存在。")
                require_matching_binding(saved["binding"], expected)
                if (
                    canonical_sha256(regression["result"]) != saved["binding"].get("result_sha256")
                    or regression["record"] != saved["record"]
                ):
                    raise SimulatorEvidenceError("evidence_invalid", "候选回归返回值与已保存证据不一致。")
                regression["verification"] = saved["verification"]
                if saved["verification"]["status"] == "passed":
                    self.store.activate_version(project_id, candidate_version_id)
                    self.store.update_version_metadata(
                        project_id,
                        candidate_version_id,
                        {"lifecycle_status": "accepted"},
                    )
                    status = "passed"
                    message = "候选程序已通过完整回归并成为当前版本。"
                else:
                    rollback["required"] = True
                    regression_status = str(
                        (execution.get("result") or {}).get("status") or "error"
                    )
                    if regression_status == "passed":
                        regression_status = "unverified"
                    status = f"regression_{regression_status}"
                    message = "候选程序未通过完整回归，已保留原版本。"
        except Exception as error:
            status = "error"
            message = str(error)
            if isinstance(error, SimulatorEvidenceError):
                regression["verification"] = blocked_verification(error.category)
            if candidate_import and _import_may_have_changed(candidate_import):
                rollback["required"] = True
        finally:
            if rollback.get("required"):
                rollback["attempted"] = True
                try:
                    if self.simulator_preparer is not None:
                        stopped = self.simulator_preparer.stop_if_running()
                        rollback["simulator_stop"] = (
                            stopped.to_dict()
                            if hasattr(stopped, "to_dict")
                            else _mapping(stopped)
                        )
                        if not stopped.success:
                            raise DebugLoopError(
                                "回滚前无法停止 GX Simulator2：" + stopped.message
                            )
                    rollback_result = self._import_version(
                        project_id,
                        base_version_id,
                        phase="rollback",
                        rollback_expected_current_sha256=str(
                            (
                                (candidate_import.get("details") or {}).get(
                                    "version_protection"
                                )
                                or {}
                            ).get("target_program_semantic_sha256")
                            or ""
                        ),
                    )
                    rollback.update(rollback_result)
                    rollback["restored"] = bool(rollback_result.get("success"))
                except Exception as error:
                    rollback.update({"success": False, "restored": False, "message": str(error)})
                if not rollback.get("restored"):
                    status = "rollback_failed"
                    message = "候选程序失败且 GX Works2 原版本自动恢复失败，请使用备份人工恢复。"
            if candidate_version_id and candidate_completed and status != "passed":
                # The project pointer is authoritative even when physical GX
                # recovery needs manual intervention.
                self.store.activate_version(project_id, base_version_id)
                self.store.update_version_metadata(
                    project_id,
                    candidate_version_id,
                    {
                        "lifecycle_status": (
                            "rollback_failed" if status == "rollback_failed" else "rejected"
                        )
                    },
                )
            elif candidate_version_id and not candidate_completed:
                self.store.discard_version(project_id, candidate_version_id)

        attempt = {
            "schema_version": DEBUG_LOOP_SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "created_at": _utc_now(),
            "status": status,
            "message": message,
            "candidate_version_id": candidate_version_id,
            "source_run_id": run_id,
            "base_binding": copy.deepcopy(checked["evidence"].get("binding") or {}),
            "evidence": copy.deepcopy(checked["evidence"]),
            "diagnosis": copy.deepcopy(checked["diagnosis"]),
            "patch": copy.deepcopy(checked["patch"]),
            "candidate_import": candidate_import,
            "regression": regression,
            "rollback": rollback,
        }
        self.store.save_debug_attempt(project_id, base_version_id, attempt)
        return attempt


__all__ = [
    "DEBUG_DIAGNOSIS_SCHEMA_VERSION",
    "DEBUG_EVIDENCE_SCHEMA_VERSION",
    "DEBUG_LOOP_SCHEMA_VERSION",
    "DEBUG_PATCH_SCHEMA_VERSION",
    "DebugLoopError",
    "DebugPatchLoopService",
    "build_failure_evidence",
    "normalize_and_apply_debug_patch",
    "normalize_debug_diagnosis",
    "render_candidate_artifacts",
]
