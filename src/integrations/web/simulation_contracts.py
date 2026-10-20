"""Recorded simulation and signal wire records, without execution semantics."""
from __future__ import annotations

from pydantic import JsonValue

from .response_types import EngineeringMap, ExtensibleResource, PublicResource


class SimulationInvariant(ExtensibleResource):
    type: str
    name: str | None = None
    devices: list[str] | None = None
    device: str | None = None
    duration_ms: int | None = None
    allow_repeat: bool | None = None
    allowed: list[JsonValue] | None = None


class SimulationFault(ExtensibleResource):
    type: str
    device: str
    at_ms: int
    delay_ms: int | None = None
    duration_ms: int | None = None
    interval_ms: int | None = None


class SimulationViolation(ExtensibleResource):
    invariant_index: int
    type: str
    name: str
    at_ms: int
    message: str
    values: EngineeringMap[JsonValue]


class SimulationAssertion(ExtensibleResource):
    step_id: str
    at_ms: int
    address: str
    passed: bool
    detail: str
    actual: JsonValue = None
    expected: JsonValue = None
    data_type: str | None = None
    wait_for: bool | None = None


class SimulationTrace(ExtensibleResource):
    at_ms: int
    event: str
    values: EngineeringMap[JsonValue] | None = None
    step_id: str | None = None
    backend_kind: str | None = None
    error: str | None = None


class ScanDevices(ExtensibleResource):
    current: str | None = None
    minimum: str | None = None
    maximum: str | None = None


class ScanMonitor(ExtensibleResource):
    supported: bool
    backend_capable: bool
    sampled: bool
    sample_count: int
    devices: ScanDevices
    unit_ms: float | None
    warning_ms: float | None
    warning_exceeded: bool
    latest_current_ms: float | None
    observed_minimum_ms: float | None
    observed_maximum_ms: float | None
    error: str | None


class SimulationCaseResult(ExtensibleResource):
    name: str
    status: str
    passed: bool | None = None
    description: str | None = None
    display_name: str | None = None
    plc_model: str | None = None
    backend_kind: str | None = None
    started_at: str | None = None
    duration_ms: int | None = None
    error: str | None = None
    execution_started: bool | None = None
    environment_failure: bool | None = None
    setup_stage: str | None = None
    assertions: list[SimulationAssertion]
    invariant_violations: list[SimulationViolation]
    trace: list[SimulationTrace]
    fault_injections: list[SimulationFault] | None = None
    scan_monitor: ScanMonitor | None = None


class SimulationCounts(ExtensibleResource):
    passed: int | None = None
    failed: int | None = None
    error: int | None = None
    unavailable: int | None = None


class SimulationResult(ExtensibleResource):
    schema_version: int | None = None
    suite_sha256: str | None = None
    name: str | None = None
    display_name: str | None = None
    plc_model: str | None = None
    status: str
    passed: bool | None = None
    counts: SimulationCounts | None = None
    test_count: int | None = None
    attempted_count: int | None = None
    executed_count: int | None = None
    not_executed_count: int | None = None
    backend_kinds: list[str] | None = None
    results: list[SimulationCaseResult]


class TimelineSignal(ExtensibleResource):
    id: str
    label: str
    address: str
    addresses: list[str]
    data_type: str
    role: str
    unit: str
    cases: list[str] | None = None


class TimelineSample(PublicResource):
    seq: int
    at_ms: int
    values: EngineeringMap[int | float | bool | None]
    errors: EngineeringMap[str]
    case: str | None = None
    read_at: str | None = None


class TimelineEvent(ExtensibleResource):
    seq: int
    at_ms: int
    kind: str
    label: str
    signal: str | None = None
    expected: JsonValue = None
    actual: JsonValue = None
    passed: bool | None = None
    error: str | None = None
    raw: EngineeringMap[int | float | bool] | None = None
    values: EngineeringMap[int | float | bool] | None = None


class TimelinePlannedEvent(ExtensibleResource):
    at_ms: int
    label: str
    kind: str


class TimelineConfig(ExtensibleResource):
    title: str
    plc_model: str
    signals: list[TimelineSignal]
    duration_ms: int
    sample_ms: int
    events: list[TimelinePlannedEvent] | None = None


class TimelineSource(ExtensibleResource):
    kind: str
    route: str | None = None
    program_correspondence: str | None = None
    physical_plc: bool | None = None
    backend_kind: str | None = None
    note: str | None = None


class TimelineBinding(ExtensibleResource):
    project_id: str | None = None
    version_id: str | None = None


class SimulationTimeline(PublicResource):
    schema_version: int
    view_id: str
    config: TimelineConfig
    source: TimelineSource
    binding: TimelineBinding
    status: str
    error: str
    revision: int
    sequence: int
    sample_count: int
    dropped_samples: int
    latest: TimelineSample | None
    samples: list[TimelineSample]
    events: list[TimelineEvent]
    elapsed_ms: int
