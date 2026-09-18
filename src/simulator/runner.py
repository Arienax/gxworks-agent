"""Strict PLC Test DSL runner with trace and invariant evaluation."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from shared.display_names import naturalize_display_text, naturalize_identifier, preferred_display_name
from plc.ir import canonical_sha256
from plc.timing import decode_scan_monitor_values, scan_monitor_profile

from .backends import FaultInjectingBackend
from .gateway import is_gateway_environment_error
from .models import normalize_test_case, normalize_test_suite


TEST_RESULT_SCHEMA_VERSION = 1


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _truthy(value: Any) -> bool:
    try:
        return float(value) != 0.0
    except (TypeError, ValueError):
        return bool(value)


def _compare(actual: Any, expectation: Mapping[str, Any]) -> Tuple[bool, str]:
    operator = expectation["operator"]
    expected = expectation["value"]
    tolerance = float(expectation.get("tolerance") or 0.0)
    try:
        numeric_actual = float(actual)
        if operator == "between":
            low, high = map(float, expected)
            passed = low - tolerance <= numeric_actual <= high + tolerance
        else:
            numeric_expected = float(expected)
            if operator == "eq":
                passed = abs(numeric_actual - numeric_expected) <= tolerance
            elif operator == "ne":
                passed = abs(numeric_actual - numeric_expected) > tolerance
            elif operator == "gt":
                passed = numeric_actual > numeric_expected
            elif operator == "ge":
                passed = numeric_actual >= numeric_expected
            elif operator == "lt":
                passed = numeric_actual < numeric_expected
            else:
                passed = numeric_actual <= numeric_expected
    except (TypeError, ValueError):
        if operator == "eq":
            passed = actual == expected
        elif operator == "ne":
            passed = actual != expected
        else:
            passed = False
    return passed, f"actual={actual!r}, {operator} {expected!r}, tolerance={tolerance}"


class InvariantMonitor:
    def __init__(self, invariants: Sequence[Mapping[str, Any]]):
        self.invariants = list(invariants or [])
        self.violations: List[Dict[str, Any]] = []
        self.on_since: Dict[Tuple[int, str], int] = {}
        self.off_since: Dict[Tuple[int, str], int] = {}
        self.last_values: Dict[Tuple[int, str], Any] = {}
        self.sequence_positions: Dict[int, int] = {}
        self.initialized: set = set()

    def addresses(self):
        result = set()
        for item in self.invariants:
            result.update(item.get("devices") or [])
            if item.get("device"):
                result.add(item["device"])
        return sorted(result)

    def _violate(self, index: int, now_ms: int, item: Mapping[str, Any], message: str, values):
        if any(row.get("invariant_index") == index for row in self.violations):
            return
        self.violations.append(
            {
                "invariant_index": index,
                "type": item["type"],
                "name": item.get("name") or "",
                "at_ms": now_ms,
                "message": message,
                "values": copy.deepcopy(dict(values)),
            }
        )

    def sample(self, now_ms: int, values: Mapping[str, Any]) -> None:
        for index, item in enumerate(self.invariants):
            kind = item["type"]
            if kind == "mutual_exclusion":
                active = [address for address in item["devices"] if _truthy(values.get(address, 0))]
                if len(active) > 1:
                    self._violate(index, now_ms, item, "互斥设备同时为 ON：" + ", ".join(active), values)
            elif kind == "state_constraint":
                address = item["device"]
                if values.get(address, 0) not in item["allowed"]:
                    self._violate(
                        index,
                        now_ms,
                        item,
                        f"{address}={values.get(address)!r} 不在允许状态 {item['allowed']}",
                        values,
                    )
            elif kind == "maximum_on_time":
                address = item["device"]
                key = (index, address)
                if _truthy(values.get(address, 0)):
                    self.on_since.setdefault(key, now_ms)
                    elapsed = now_ms - self.on_since[key]
                    if elapsed > item["duration_ms"]:
                        self._violate(
                            index,
                            now_ms,
                            item,
                            f"{address} 连续 ON {elapsed} ms，超过 {item['duration_ms']} ms",
                            values,
                        )
                else:
                    self.on_since.pop(key, None)
            elif kind == "minimum_off_time":
                address = item["device"]
                key = (index, address)
                current = values.get(address, 0)
                if key not in self.initialized:
                    self.initialized.add(key)
                    if not _truthy(current):
                        self.off_since[key] = now_ms
                    self.last_values[key] = current
                    continue
                previous = self.last_values.get(key, 0)
                if not _truthy(current):
                    self.off_since.setdefault(key, now_ms)
                elif not _truthy(previous):
                    elapsed = now_ms - self.off_since.get(key, now_ms)
                    if elapsed < item["duration_ms"]:
                        self._violate(
                            index,
                            now_ms,
                            item,
                            f"{address} 仅 OFF {elapsed} ms，短于 {item['duration_ms']} ms",
                            values,
                        )
                    self.off_since.pop(key, None)
                self.last_values[key] = current
            elif kind == "sequence_constraint":
                devices = item["devices"]
                position = self.sequence_positions.get(index, 0)
                for device_index, address in enumerate(devices):
                    key = (index, address)
                    current = values.get(address, 0)
                    previous = self.last_values.get(key, 0)
                    if position >= len(devices) and not item.get("allow_repeat"):
                        self.last_values[key] = current
                        continue
                    if _truthy(current) and not _truthy(previous):
                        if device_index == position:
                            position += 1
                            if item.get("allow_repeat") and position == len(devices):
                                position = 0
                        elif device_index > position or not item.get("allow_repeat"):
                            self._violate(
                                index,
                                now_ms,
                                item,
                                f"{address} 在顺序位置 {device_index + 1} 提前出现，当前期望 {position + 1}",
                                values,
                            )
                    self.last_values[key] = current
                self.sequence_positions[index] = position

    def public_violations(self):
        return copy.deepcopy(self.violations)


class PLCTestRunner:
    def __init__(
        self,
        backend,
        progress: Optional[Callable[[Mapping[str, Any]], None]] = None,
        reset_devices: Sequence[str] = (),
    ):
        self.backend = backend
        self.progress = progress
        self.reset_devices = sorted(
            {str(address).upper() for address in (reset_devices or [])}
        )

    @staticmethod
    def _trace(trace, now_ms, event, **payload):
        trace.append({"at_ms": int(now_ms), "event": event, **copy.deepcopy(payload)})

    def _emit_progress(self, event: str, message: str, **payload) -> None:
        if self.progress is None:
            return
        update = {
            "event": str(event),
            "message": str(message),
            **copy.deepcopy(payload),
        }
        try:
            self.progress(update)
        except Exception:
            # Progress rendering is observational and must never change the
            # simulator result or terminate a test worker.
            pass

    @staticmethod
    def _progress_percent(context) -> int:
        total = max(1, int(context.get("total_steps") or 1))
        completed = max(0, int(context.get("completed_steps") or 0))
        return max(0, min(100, round(completed * 100 / total)))

    def run(
        self,
        test_case: Mapping[str, Any],
        *,
        plc_model: str = "FX3U",
        _progress_context=None,
    ) -> Dict[str, Any]:
        case = normalize_test_case(test_case, plc_model=plc_model)
        progress_context = _progress_context or {
            "test_index": 1,
            "test_count": 1,
            "total_steps": max(1, len(case["steps"])),
            "completed_steps": 0,
        }
        test_index = int(progress_context.get("test_index") or 1)
        test_count = int(progress_context.get("test_count") or 1)
        case_display_name = preferred_display_name(
            case,
            kind="测试项目",
            index=test_index,
        )
        backend = (
            FaultInjectingBackend(self.backend, case["fault_injections"])
            if case["fault_injections"]
            else self.backend
        )
        trace: List[Dict[str, Any]] = []
        assertions: List[Dict[str, Any]] = []
        monitor = InvariantMonitor(case["invariants"])
        scan_profile = scan_monitor_profile(case["plc_model"])
        scan_enabled = bool(
            scan_profile.get("available")
            and getattr(backend, "supports_scan_monitor", False)
        )
        scan_addresses = sorted((scan_profile.get("devices") or {}).values())
        scan_samples: List[Dict[str, Any]] = []
        scan_error = ""
        now_ms = 0
        started_at = _utc_now()
        continuous_sampling = bool(case["invariants"])
        execution_started = False
        environment_failure = False
        setup_stage = "connect"

        self._emit_progress(
            "test_started",
            f"测试 {test_index}/{test_count}：{case_display_name}",
            percent=self._progress_percent(progress_context),
            test_index=test_index,
            test_count=test_count,
            test_name=case["name"],
            test_display_name=case_display_name,
            step_count=len(case["steps"]),
        )

        def sample(reason="sample"):
            nonlocal scan_enabled, scan_error
            addresses = sorted(set(case["trace_devices"]) | set(monitor.addresses()))
            requested = sorted(
                set(addresses) | (set(scan_addresses) if scan_enabled else set())
            )
            try:
                sampled_values = backend.read_many(requested) if requested else {}
            except Exception as exc:
                if not scan_enabled:
                    raise
                # Some FX3-compatible targets/Simulator2 editions may reject
                # one or more diagnostic registers. Retry only the functional
                # trace addresses so a diagnostic capability gap does not
                # become a false program regression failure.
                scan_error = str(exc)[:1000]
                scan_enabled = False
                sampled_values = backend.read_many(addresses) if addresses else {}
            values = {
                address: sampled_values.get(address)
                for address in addresses
            }
            monitor.sample(now_ms, values)
            payload: Dict[str, Any] = {"values": values}
            if scan_enabled:
                try:
                    raw_scan = {
                        address: sampled_values.get(address)
                        for address in scan_addresses
                    }
                    decoded_scan = decode_scan_monitor_values(
                        raw_scan, case["plc_model"]
                    )
                    sample_row = {
                        "at_ms": int(now_ms),
                        "raw": {
                            address: raw_scan.get(address)
                            for address in scan_addresses
                        },
                        **decoded_scan,
                    }
                    scan_samples.append(sample_row)
                    payload["scan_monitor"] = copy.deepcopy(sample_row)
                except Exception as exc:
                    # Scan monitoring is diagnostic-only.  A CPU/edition that
                    # cannot expose D8010-D8012 must not turn a valid control
                    # regression into a logic failure.
                    scan_error = str(exc)[:1000]
                    scan_enabled = False
                    payload["scan_monitor_error"] = scan_error
            elif scan_error:
                payload["scan_monitor_error"] = scan_error
            self._trace(trace, now_ms, reason, **payload)

        def advance_to(target_ms):
            nonlocal now_ms
            if not continuous_sampling:
                delta = max(0, int(target_ms) - now_ms)
                if delta:
                    backend.advance_ms(delta)
                    now_ms += delta
                return
            while now_ms < target_ms:
                delta = min(case["sample_ms"], target_ms - now_ms)
                backend.advance_ms(delta)
                now_ms += delta
                sample()

        try:
            connection = backend.connect()
            self._trace(
                trace,
                now_ms,
                "connected",
                backend_kind=str(getattr(backend, "backend_kind", type(backend).__name__)),
                details=connection if isinstance(connection, Mapping) else {},
            )
            self._emit_progress(
                "test_connected",
                f"测试 {test_index}/{test_count} 已连接 GX Simulator2",
                percent=self._progress_percent(progress_context),
                test_index=test_index,
                test_count=test_count,
                test_name=case["name"],
                test_display_name=case_display_name,
            )
            if bool(getattr(backend, "supports_cpu_reset", False)):
                setup_stage = "cpu_reset"
                reset_details = backend.reset_cpu(
                    self.reset_devices,
                    initial_values=case["initial"],
                )
                self._trace(
                    trace,
                    now_ms,
                    "cpu_reset",
                    details=(
                        reset_details
                        if isinstance(reset_details, Mapping)
                        else {}
                    ),
                )
                self._emit_progress(
                    "cpu_reset",
                    f"测试 {test_index}/{test_count} 已复位 CPU 并重新进入 RUN",
                    percent=self._progress_percent(progress_context),
                    test_index=test_index,
                    test_count=test_count,
                    test_name=case["name"],
                    test_display_name=case_display_name,
                )
            if case["initial"]:
                setup_stage = "initial_write"
                backend.write_many(case["initial"])
                self._trace(trace, now_ms, "initial_write", values=case["initial"])
                self._emit_progress(
                    "initial_write",
                    "初始化输入："
                    + "，".join(
                        f"{address}={value}"
                        for address, value in sorted(case["initial"].items())
                    ),
                    percent=self._progress_percent(progress_context),
                    test_index=test_index,
                    test_count=test_count,
                    test_name=case["name"],
                    test_display_name=case_display_name,
                    values=case["initial"],
                )
            setup_stage = "initial_sample"
            sample("initial_sample")
            execution_started = True
            setup_stage = "steps"

            for step_index, step in enumerate(case["steps"], start=1):
                assertion_start = len(assertions)
                step_display_name = naturalize_identifier(
                    step["id"],
                    kind="步骤",
                    index=step_index,
                )
                self._emit_progress(
                    "step_started",
                    f"步骤 {step_index}/{len(case['steps'])}：{step_display_name}",
                    percent=self._progress_percent(progress_context),
                    test_index=test_index,
                    test_count=test_count,
                    test_name=case["name"],
                    test_display_name=case_display_name,
                    step_index=step_index,
                    step_count=len(case["steps"]),
                    step_id=step["id"],
                    step_display_name=step_display_name,
                    at_ms=step["at_ms"],
                )
                if step["at_ms"] > case["timeout_ms"]:
                    raise TimeoutError("test exceeded overall timeout")
                advance_to(step["at_ms"])
                if step["set"]:
                    setup_stage = "stimulus_write"
                    backend.write_many(step["set"])
                    setup_stage = "steps"
                    self._trace(trace, now_ms, "write", step_id=step["id"], values=step["set"])
                    self._emit_progress(
                        "device_write",
                        "写入："
                        + "，".join(
                            f"{address}={value}"
                            for address, value in sorted(step["set"].items())
                        ),
                        percent=self._progress_percent(progress_context),
                        test_index=test_index,
                        test_count=test_count,
                        test_name=case["name"],
                        test_display_name=case_display_name,
                        step_index=step_index,
                        step_count=len(case["steps"]),
                        step_id=step["id"],
                        step_display_name=step_display_name,
                        at_ms=now_ms,
                        values=step["set"],
                    )
                    sample("post_write_sample")
                if step["expect"]:
                    addresses = sorted({item["address"] for item in step["expect"]})
                    values = backend.read_many(addresses)
                    for expectation in step["expect"]:
                        passed, detail = _compare(values.get(expectation["address"]), expectation)
                        assertions.append(
                            {
                                "step_id": step["id"],
                                "at_ms": now_ms,
                                "address": expectation["address"],
                                "passed": passed,
                                "detail": detail,
                            }
                        )
                        expected = expectation["value"]
                        actual = values.get(expectation["address"])
                        self._emit_progress(
                            "assertion",
                            (
                                f"{expectation['address']}：实际 {actual}，"
                                f"期望 {expectation['operator']} {expected} — "
                                f"{'通过' if passed else '失败'}"
                            ),
                            percent=self._progress_percent(progress_context),
                            test_index=test_index,
                            test_count=test_count,
                            test_name=case["name"],
                            test_display_name=case_display_name,
                            step_index=step_index,
                            step_count=len(case["steps"]),
                            step_id=step["id"],
                            step_display_name=step_display_name,
                            at_ms=now_ms,
                            address=expectation["address"],
                            actual=actual,
                            operator=expectation["operator"],
                            expected=expected,
                            passed=passed,
                            wait_for=False,
                        )
                    self._trace(trace, now_ms, "expect", step_id=step["id"], values=values)
                if step["wait_for"]:
                    deadline = min(case["timeout_ms"], now_ms + step["timeout_ms"])
                    pending = list(step["wait_for"])
                    last_values = {}
                    while now_ms <= deadline:
                        addresses = sorted({item["address"] for item in pending})
                        last_values = backend.read_many(addresses)
                        checks = [
                            _compare(last_values.get(item["address"]), item)[0]
                            for item in pending
                        ]
                        if all(checks):
                            break
                        if now_ms == deadline:
                            break
                        advance_to(min(deadline, now_ms + step["poll_ms"]))
                    for expectation in pending:
                        passed, detail = _compare(last_values.get(expectation["address"]), expectation)
                        assertions.append(
                            {
                                "step_id": step["id"],
                                "at_ms": now_ms,
                                "address": expectation["address"],
                                "passed": passed,
                                "detail": detail,
                                "wait_for": True,
                                "deadline_ms": deadline,
                            }
                        )
                        actual = last_values.get(expectation["address"])
                        self._emit_progress(
                            "assertion",
                            (
                                f"等待 {expectation['address']}：实际 {actual}，"
                                f"期望 {expectation['operator']} {expectation['value']} — "
                                f"{'通过' if passed else '超时/失败'}"
                            ),
                            percent=self._progress_percent(progress_context),
                            test_index=test_index,
                            test_count=test_count,
                            test_name=case["name"],
                            test_display_name=case_display_name,
                            step_index=step_index,
                            step_count=len(case["steps"]),
                            step_id=step["id"],
                            step_display_name=step_display_name,
                            at_ms=now_ms,
                            address=expectation["address"],
                            actual=actual,
                            operator=expectation["operator"],
                            expected=expectation["value"],
                            passed=passed,
                            wait_for=True,
                        )
                    self._trace(trace, now_ms, "wait_for", step_id=step["id"], values=last_values)

                progress_context["completed_steps"] = int(
                    progress_context.get("completed_steps") or 0
                ) + 1
                step_assertions = assertions[assertion_start:]
                step_passed = all(item.get("passed") for item in step_assertions)
                self._emit_progress(
                    "step_completed",
                    (
                        f"步骤 {step_index}/{len(case['steps'])} 完成"
                        + ("" if step_passed else "，存在失败断言")
                    ),
                    percent=self._progress_percent(progress_context),
                    test_index=test_index,
                    test_count=test_count,
                    test_name=case["name"],
                    test_display_name=case_display_name,
                    step_index=step_index,
                    step_count=len(case["steps"]),
                    step_id=step["id"],
                    step_display_name=step_display_name,
                    at_ms=now_ms,
                    passed=step_passed,
                )

            sample("final_sample")
            violations = monitor.public_violations()
            failed_assertions = [item for item in assertions if not item["passed"]]
            status = "passed" if not failed_assertions and not violations else "failed"
            error = ""
            setup_stage = "complete"
        except Exception as exc:
            violations = monitor.public_violations()
            environment_failure = is_gateway_environment_error(exc) or (
                "unavailable" in str(exc).lower()
            )
            status = "unavailable" if environment_failure else "error"
            error = str(exc)
            self._trace(
                trace,
                now_ms,
                "runner_error",
                error=error,
                setup_stage=setup_stage,
                environment_failure=environment_failure,
            )
            self._emit_progress(
                "test_error",
                f"测试 {test_index}/{test_count} 出错：{naturalize_display_text(error)}",
                percent=self._progress_percent(progress_context),
                test_index=test_index,
                test_count=test_count,
                test_name=case["name"],
                test_display_name=case_display_name,
                error=error,
            )
        finally:
            try:
                backend.disconnect()
            except Exception as exc:
                self._trace(trace, now_ms, "disconnect_error", error=str(exc))

        latest_current = next(
            (
                item.get("current_ms")
                for item in reversed(scan_samples)
                if item.get("current_ms") is not None
            ),
            None,
        )
        minimum_values = [
            item["minimum_ms"]
            for item in scan_samples
            if item.get("minimum_ms") is not None
        ]
        maximum_values = [
            item["maximum_ms"]
            for item in scan_samples
            if item.get("maximum_ms") is not None
        ]
        observed_minimum = min(minimum_values) if minimum_values else None
        observed_maximum = max(maximum_values) if maximum_values else None
        warning_ms = scan_profile.get("warning_ms")
        warning_exceeded = bool(
            observed_maximum is not None
            and warning_ms is not None
            and observed_maximum > float(warning_ms)
        )

        result = {
            "schema_version": TEST_RESULT_SCHEMA_VERSION,
            "test_sha256": canonical_sha256(case),
            "name": case["name"],
            "description": case.get("description") or "",
            "display_name": case_display_name,
            "plc_model": case["plc_model"],
            "status": status,
            "passed": status == "passed",
            "backend_kind": str(getattr(backend, "backend_kind", type(backend).__name__)),
            "started_at": started_at,
            "duration_ms": now_ms,
            "error": error,
            "execution_started": execution_started,
            "environment_failure": environment_failure,
            "setup_stage": setup_stage,
            "assertions": assertions,
            "invariant_violations": violations,
            "trace": trace,
            "fault_injections": copy.deepcopy(case["fault_injections"]),
            "scan_monitor": {
                "supported": bool(scan_profile.get("available")),
                "backend_capable": bool(
                    getattr(backend, "supports_scan_monitor", False)
                ),
                "sampled": bool(scan_samples),
                "sample_count": len(scan_samples),
                "devices": copy.deepcopy(scan_profile.get("devices") or {}),
                "unit_ms": scan_profile.get("unit_ms"),
                "warning_ms": warning_ms,
                "warning_exceeded": warning_exceeded,
                "latest_current_ms": latest_current,
                "observed_minimum_ms": observed_minimum,
                "observed_maximum_ms": observed_maximum,
                "error": scan_error,
            },
        }
        self._emit_progress(
            "test_completed",
            (
                f"测试 {test_index}/{test_count} {case_display_name}："
                + {
                    "passed": "通过",
                    "failed": "失败",
                    "unavailable": "环境不可用",
                    "error": "执行出错",
                }.get(status, status)
            ),
            percent=self._progress_percent(progress_context),
            test_index=test_index,
            test_count=test_count,
            test_name=case["name"],
            test_display_name=case_display_name,
            status=status,
            passed=status == "passed",
            assertion_count=len(assertions),
            failed_assertion_count=len(
                [item for item in assertions if not item.get("passed")]
            ),
        )
        return result

    def run_suite(self, suite: Mapping[str, Any], *, plc_model: str = "FX3U") -> Dict[str, Any]:
        normalized = normalize_test_suite(suite, plc_model=plc_model)
        suite_display_name = preferred_display_name(
            normalized,
            kind="测试方案",
            descriptive_keys=("display_name", "description", "title", "label"),
        )
        progress_context = {
            "test_index": 0,
            "test_count": len(normalized["tests"]),
            "total_steps": sum(
                max(1, len(item.get("steps") or []))
                for item in normalized["tests"]
            ),
            "completed_steps": 0,
        }
        self._emit_progress(
            "suite_started",
            f"开始执行 {len(normalized['tests'])} 项仿真测试",
            percent=0,
            test_count=len(normalized["tests"]),
            suite_name=normalized["name"],
            suite_display_name=suite_display_name,
        )
        results = []
        for test_index, item in enumerate(normalized["tests"], start=1):
            progress_context["test_index"] = test_index
            results.append(
                self.run(
                    item,
                    plc_model=normalized["plc_model"],
                    _progress_context=progress_context,
                )
            )
            if results[-1].get("environment_failure") or results[-1]["status"] == "unavailable":
                break
        counts = {"passed": 0, "failed": 0, "error": 0, "unavailable": 0}
        for result in results:
            counts[result["status"]] = counts.get(result["status"], 0) + 1
        if counts.get("unavailable"):
            status = "unavailable"
        elif counts.get("error"):
            status = "error"
        elif counts.get("failed"):
            status = "failed"
        else:
            status = "passed"
        attempted_count = len(results)
        executed_count = sum(
            1 for item in results if bool(item.get("execution_started"))
        )
        result = {
            "schema_version": TEST_RESULT_SCHEMA_VERSION,
            "suite_sha256": canonical_sha256(normalized),
            "name": normalized["name"],
            "display_name": suite_display_name,
            "plc_model": normalized["plc_model"],
            "status": status,
            "passed": status == "passed",
            "counts": counts,
            "test_count": len(normalized["tests"]),
            "attempted_count": attempted_count,
            "executed_count": executed_count,
            "not_executed_count": len(normalized["tests"]) - executed_count,
            "backend_kinds": sorted(
                {str(item.get("backend_kind") or "") for item in results}
            ),
            "results": results,
        }
        self._emit_progress(
            "suite_completed",
            f"仿真测试执行结束：通过 {counts.get('passed', 0)}，失败 {counts.get('failed', 0)}，错误 {counts.get('error', 0)}",
            percent=100,
            test_count=len(normalized["tests"]),
            attempted_count=attempted_count,
            executed_count=executed_count,
            status=status,
            counts=counts,
            suite_name=normalized["name"],
            suite_display_name=suite_display_name,
        )
        return result


__all__ = ["InvariantMonitor", "PLCTestRunner", "TEST_RESULT_SCHEMA_VERSION"]
