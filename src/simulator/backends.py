"""Simulator backend protocol, explicit test double, and fault wrapper."""

from __future__ import annotations

import heapq
from typing import Any, Callable, Dict, Mapping, Optional, Sequence


class InMemoryTestBackend:
    """Deterministic executor test double; it does not emulate PLC ladder."""

    backend_kind = "test_memory_not_plc_simulator"
    supports_scan_monitor = False
    supports_cpu_reset = False

    def __init__(
        self,
        initial: Optional[Mapping[str, Any]] = None,
        on_write: Optional[Callable[["InMemoryTestBackend", Dict[str, Any]], None]] = None,
        on_advance: Optional[Callable[["InMemoryTestBackend", int], None]] = None,
    ):
        self.values = {str(key).upper(): value for key, value in (initial or {}).items()}
        self.on_write = on_write
        self.on_advance = on_advance
        self.now_ms = 0
        self.connected = False

    def connect(self):
        self.connected = True
        return {"ok": True, "backend_kind": self.backend_kind}

    def disconnect(self):
        self.connected = False

    def read_many(self, addresses: Sequence[str]) -> Dict[str, Any]:
        if not self.connected:
            raise RuntimeError("test backend is not connected")
        return {str(address).upper(): self.values.get(str(address).upper(), 0) for address in addresses}

    def write_many(self, values: Mapping[str, Any]) -> None:
        if not self.connected:
            raise RuntimeError("test backend is not connected")
        normalized = {str(key).upper(): value for key, value in values.items()}
        self.values.update(normalized)
        if self.on_write:
            self.on_write(self, normalized)

    def advance_ms(self, milliseconds: int) -> None:
        milliseconds = max(0, int(milliseconds))
        self.now_ms += milliseconds
        if self.on_advance:
            self.on_advance(self, milliseconds)


class FaultInjectingBackend:
    """Apply declared sensor faults without changing the wrapped backend API."""

    def __init__(self, backend, faults):
        self.backend = backend
        self.faults = list(faults or [])
        self.now_ms = 0
        self._queue = []
        self._counter = 0
        self._drop_restore = {}
        self._activated = set()
        self._restored = set()
        self._original_values = {}

    @property
    def backend_kind(self):
        wrapped = str(getattr(self.backend, "backend_kind", type(self.backend).__name__))
        return f"fault_injection_wrapper:{wrapped}"

    @property
    def connected(self):
        return bool(getattr(self.backend, "connected", False))

    @property
    def supports_scan_monitor(self):
        return bool(getattr(self.backend, "supports_scan_monitor", False))

    @property
    def supports_cpu_reset(self):
        return bool(getattr(self.backend, "supports_cpu_reset", False))

    def connect(self):
        result = self.backend.connect()
        devices = sorted(
            {
                str(item.get("device") or "").upper()
                for item in self.faults
                if str(item.get("device") or "").strip()
            }
        )
        self._original_values = (
            self.backend.read_many(devices) if devices else {}
        )
        self._activate_due()
        self._flush()
        return result

    def reset_cpu(self, devices=(), initial_values=None):
        if not self.supports_cpu_reset:
            raise RuntimeError("wrapped backend does not support CPU reset")
        result = self.backend.reset_cpu(devices, initial_values=initial_values)
        self.now_ms = 0
        self._queue.clear()
        self._drop_restore.clear()
        self._activated.clear()
        self._restored.clear()
        devices = sorted(
            {
                str(item.get("device") or "").upper()
                for item in self.faults
                if str(item.get("device") or "").strip()
            }
        )
        self._original_values = (
            self.backend.read_many(devices) if devices else {}
        )
        self._activate_due()
        self._flush()
        self._enforce_active_faults()
        return result

    def disconnect(self):
        try:
            # A fault is a test-local overlay.  Never leave stuck/bounced/
            # delayed inputs in GX Simulator2 after the evidence run ends.
            self._queue.clear()
            if self._original_values:
                self.backend.write_many(self._original_values)
        finally:
            return self.backend.disconnect()

    def _active(self, fault):
        return self.now_ms >= int(fault.get("at_ms", 0))

    def _schedule(self, due_ms: int, values: Mapping[str, Any]):
        self._counter += 1
        heapq.heappush(self._queue, (int(due_ms), self._counter, dict(values)))

    def _flush(self):
        while self._queue and self._queue[0][0] <= self.now_ms:
            _due, _order, values = heapq.heappop(self._queue)
            self.backend.write_many(values)

    def _activate_due(self):
        for index, fault in enumerate(self.faults):
            if index in self._activated or not self._active(fault):
                continue
            self._activated.add(index)
            device = fault["device"]
            kind = fault["type"]
            if kind == "stuck_on":
                self.backend.write_many({device: 1})
            elif kind == "stuck_off":
                self.backend.write_many({device: 0})
            elif kind == "drop_signal":
                prior = self.backend.read_many([device]).get(device, 0)
                self._drop_restore[device] = prior
                self.backend.write_many({device: 0})

    def _restore_expired_drops(self):
        for index, fault in enumerate(self.faults):
            if fault["type"] != "drop_signal" or index in self._restored:
                continue
            duration = int(fault.get("duration_ms", 0))
            if duration <= 0 or self.now_ms < int(fault["at_ms"]) + duration:
                continue
            self._restored.add(index)
            device = fault["device"]
            self.backend.write_many({device: self._drop_restore.get(device, 0)})

    def _enforce_active_faults(self):
        values = {}
        for index, fault in enumerate(self.faults):
            if index not in self._activated:
                continue
            device = fault["device"]
            kind = fault["type"]
            if kind == "stuck_on":
                values[device] = 1
            elif kind == "stuck_off":
                values[device] = 0
            elif kind == "drop_signal":
                duration = int(fault.get("duration_ms", 0))
                if duration == 0 or self.now_ms < int(fault["at_ms"]) + duration:
                    values[device] = 0
        if values:
            self.backend.write_many(values)

    def write_many(self, values: Mapping[str, Any]) -> None:
        self._activate_due()
        self._restore_expired_drops()
        immediate = dict(values)
        for index, fault in enumerate(self.faults):
            if index not in self._activated:
                continue
            device = fault["device"]
            if device not in immediate:
                continue
            kind = fault["type"]
            desired = immediate.pop(device)
            if kind == "stuck_on":
                immediate[device] = 1
            elif kind == "stuck_off":
                immediate[device] = 0
            elif kind == "signal_delay":
                self._schedule(self.now_ms + int(fault["delay_ms"]), {device: desired})
            elif kind == "signal_bounce":
                duration = int(fault["duration_ms"])
                interval = int(fault["interval_ms"])
                prior = self.backend.read_many([device]).get(device, 0)
                value = desired
                for offset in range(0, duration, interval):
                    value = prior if value == desired else desired
                    self._schedule(self.now_ms + offset, {device: value})
                self._schedule(self.now_ms + duration, {device: desired})
            elif kind == "drop_signal":
                immediate[device] = 0
                duration = int(fault.get("duration_ms", 0))
                self._drop_restore[device] = desired
                if duration > 0 and self.now_ms >= int(fault["at_ms"]) + duration:
                    immediate[device] = desired
        if immediate:
            self.backend.write_many(immediate)
        self._flush()

    def read_many(self, addresses: Sequence[str]) -> Dict[str, Any]:
        self._activate_due()
        self._restore_expired_drops()
        self._flush()
        self._enforce_active_faults()
        values = self.backend.read_many(addresses)
        for fault in self.faults:
            if not self._active(fault):
                continue
            device = fault["device"]
            if device not in values:
                continue
            if fault["type"] == "stuck_on":
                values[device] = 1
            elif fault["type"] in {"stuck_off", "drop_signal"}:
                duration = int(fault.get("duration_ms", 0))
                if duration == 0 or self.now_ms < int(fault["at_ms"]) + duration:
                    values[device] = 0
        return values

    def advance_ms(self, milliseconds: int) -> None:
        target_ms = self.now_ms + max(0, int(milliseconds))
        while self.now_ms < target_ms:
            boundaries = [target_ms]
            boundaries.extend(
                int(fault["at_ms"])
                for index, fault in enumerate(self.faults)
                if index not in self._activated
                and self.now_ms < int(fault["at_ms"]) <= target_ms
            )
            boundaries.extend(
                int(fault["at_ms"]) + int(fault.get("duration_ms", 0))
                for index, fault in enumerate(self.faults)
                if fault["type"] == "drop_signal"
                and index not in self._restored
                and int(fault.get("duration_ms", 0)) > 0
                and self.now_ms
                < int(fault["at_ms"]) + int(fault.get("duration_ms", 0))
                <= target_ms
            )
            if self._queue and self.now_ms < self._queue[0][0] <= target_ms:
                boundaries.append(self._queue[0][0])
            next_ms = min(boundaries)
            self.backend.advance_ms(next_ms - self.now_ms)
            self.now_ms = next_ms
            self._activate_due()
            self._restore_expired_drops()
            self._flush()
            self._enforce_active_faults()


__all__ = ["FaultInjectingBackend", "InMemoryTestBackend"]
