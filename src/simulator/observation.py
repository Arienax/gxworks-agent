# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Bounded, thread-safe observations shared by live views and recorded replay."""
from __future__ import annotations

import copy
import threading
import time
import uuid
from datetime import datetime, timezone

from .signals import decode_values


class SimulationObservation:
    def __init__(self, config, *, source=None, binding=None, limit=6001):
        self.config = copy.deepcopy(config)
        self.view_id = uuid.uuid4().hex
        self.source = copy.deepcopy(source or {"kind": "runtime_device_observation"})
        self.binding = copy.deepcopy(binding or {})
        self.samples, self.events = [], []
        self.status, self.error = "prepared", ""
        self.revision, self.sequence, self.dropped = 0, 0, 0
        self.limit = limit
        self._lock = threading.RLock()
        self.started = time.monotonic()
        self._case_offset, self._case_end = 0, 0
        self._case = ""

    def elapsed(self):
        return round((time.monotonic() - self.started) * 1000)

    def _sequence(self):
        self.sequence += 1
        self.revision += 1
        return self.sequence

    def sample(self, raw, *, at_ms=None, case=""):
        values, errors = decode_values(self.config["signals"], raw, case=case or None)
        return self.sample_values(values, errors=errors, at_ms=at_ms, case=case)

    def sample_values(self, values, *, errors=None, at_ms=None, read_at=None, case=""):
        """Accept a recorded decoder's values; preserve its provenance and gaps."""
        allowed = {s["id"] for s in self.config["signals"]}
        if not isinstance(values, dict) or set(values) - allowed:
            raise ValueError("Observed values must reference declared signals")
        import math
        if any(value is not None and (not isinstance(value, (bool, int, float))
               or not math.isfinite(value)) for value in values.values()):
            raise ValueError("Observed values must be finite JSON numbers or booleans")
        with self._lock:
            row = {"seq": self._sequence(), "at_ms": self.elapsed() if at_ms is None else at_ms,
                   "read_at": read_at or datetime.now(timezone.utc).isoformat(),
                   "values": {key: values.get(key) for key in allowed},
                   "errors": dict(errors or {}), "case": case}
            self.samples.append(row)
            if len(self.samples) > self.limit:
                self.samples.pop(0)
                self.dropped += 1
        return row

    def event(self, kind, *, at_ms=None, label="", **payload):
        with self._lock:
            self.events.append({"seq": self._sequence(), "at_ms": self.elapsed() if at_ms is None else at_ms,
                                "kind": kind, "label": label, **copy.deepcopy(payload)})
            if len(self.events) > self.limit:
                self.events.pop(0)

    def finish(self, status, error=""):
        with self._lock:
            self.status, self.error = status, str(error)[:1000]
            self._sequence()

    def progress(self, update):
        """Consume the existing runner's real trace; planned events stay separate."""
        kind = update.get("event")
        if kind == "test_started":
            self._case_offset = self._case_end
            self._case = update.get("test_name", "")
            self.status = "recording"
            self.event("case", at_ms=self._case_offset, label=update.get("test_display_name") or self._case)
        elif kind == "trace":
            trace = update.get("trace", {})
            at = self._case_offset + int(trace.get("at_ms", 0))
            self._case_end = max(self._case_end, at)
            event = trace.get("event", "")
            # Assertion reads request only their operands. They remain assertion
            # events and must not replace a complete sample with partial values.
            if "values" in trace and event in {"sample", "initial_sample", "post_write_sample", "final_sample"}:
                self.sample(trace["values"], at_ms=at, case=self._case)
            if event in {"write", "initial_write", "initial_state", "cpu_reset", "runner_error", "disconnect_error"}:
                requested, _errors = decode_values(self.config["signals"], trace.get("values") or {}, case=self._case)
                self.event(event, at_ms=at, label=self._case, raw=trace.get("values"),
                           values={key: value for key, value in requested.items() if value is not None},
                           error=trace.get("error", ""))
        elif kind == "assertion":
            matches = [s for s in self.config["signals"] if s["address"] == update.get("address")
                       and (not update.get("data_type") or s["data_type"] == update["data_type"])]
            for signal in matches:
                self.event("assertion", at_ms=self._case_offset + int(update.get("at_ms", 0)),
                           label=signal["label"], signal=signal["id"], actual=update.get("actual"),
                           expected=update.get("expected"), passed=bool(update.get("passed")))
        elif kind == "test_error":
            self.event("error", label=update.get("error", ""))

    def snapshot(self, *, after=0, include_samples=True):
        with self._lock:
            last = self.samples[-1] if self.samples else None
            return copy.deepcopy({"schema_version": 1, "view_id": self.view_id, "config": self.config, "source": self.source,
                "binding": self.binding, "status": self.status, "error": self.error,
                "revision": self.revision, "sequence": self.sequence,
                "sample_count": len(self.samples) + self.dropped, "dropped_samples": self.dropped,
                "latest": last, "samples": [s for s in self.samples if s["seq"] > after] if include_samples else [],
                "events": [e for e in self.events if e["seq"] > after],
                "elapsed_ms": self.samples[-1]["at_ms"] if self.samples else 0})


def recorded_observation(suite, result, *, binding=None):
    """Project saved runner output into the same protocol without running it."""
    from .signals import decode_signal, signal_addresses, suite_view_config
    view = SimulationObservation(suite_view_config(suite), binding=binding,
        source={"kind": "recorded_test_execution", "backend_kind": ", ".join(result.get("backend_kinds", [])),
                "program_correspondence": "see_version_verification", "physical_plc": False})
    tests = {test["name"]: test for test in suite["tests"]}
    for row in result.get("results", []):
        test = tests.get(row.get("name"), {})
        view.progress({"event": "test_started", "test_name": row.get("name", "")})
        updates = [(trace.get("at_ms", 0), 0, {"event": "trace", "trace": trace}) for trace in row.get("trace", [])]
        for assertion in row.get("assertions", []):
            step = next((s for s in test.get("steps", []) if s["id"] == assertion.get("step_id")), {})
            mode = "wait_for" if assertion.get("wait_for") else "expect"
            expected = next((item for item in step.get(mode, []) if item["address"] == assertion.get("address")
                             and (not assertion.get("data_type") or item.get("data_type") == assertion["data_type"])), {})
            actual = assertion.get("actual")
            if "actual" not in assertion:
                observed = next((trace.get("values", {}) for trace in row.get("trace", [])
                                 if trace.get("event") == mode and trace.get("step_id") == assertion.get("step_id")
                                 and trace.get("at_ms") == assertion.get("at_ms")), {})
                try:
                    actual = decode_signal([observed.get(a) for a in signal_addresses(expected["address"], expected["data_type"], suite["plc_model"])], expected["data_type"]) if "data_type" in expected else observed.get(assertion.get("address"))
                except ValueError:
                    actual = None
            updates.append((assertion.get("at_ms", 0), 1, {"event": "assertion", **assertion,
                "actual": actual, "expected": expected.get("value"), "data_type": expected.get("data_type")}))
        for _at, _order, update in sorted(updates, key=lambda entry: entry[:2]):
            view.progress(update)
    view.finish(result.get("status", "error"), result.get("error", ""))
    return view.snapshot()
