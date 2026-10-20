"""Concrete workbench response resources, independent of engineering engines."""
from __future__ import annotations
from typing import Literal
from pydantic import Field, JsonValue
from .response_types import EngineeringMap, EngineeringObject, ExtensibleResource, JsonObject, PublicResource
from .program_contracts import (ProgramDocument, PLCProgram, LadderDocument, FBDModel, FBDNode,
    FBDLabel, SFCModel, Instruction, DeviceRecord, ProgramPropertyChanges)
from .spec_contracts import IORow, Specification, SpecificationApproach, SpecificationConstructionPlan
from .simulation_contracts import (SimulationInvariant, SimulationFault, SimulationViolation,
    SimulationResult, SimulationTimeline)


class OperationPresentation(PublicResource):
    effects: list[str]
    parameters: list[str]
    enable: str
    execution: str
    required_state: list[str]


class SpecPresentationResource(PublicResource):
    operation_intents: list[OperationPresentation]
    behavior_constraints: list[dict[str, str]]


class ToolData(ExtensibleResource):
    program: ProgramDocument | None = None
    model: FBDModel | SFCModel | None = None
    variables: EngineeringObject | None = None
    values: EngineeringObject | None = None


class EventPayload(ExtensibleResource):
    program: ProgramDocument | None = None
    values: EngineeringObject | None = None


class DeleteProjectResult(PublicResource):
    deleted: bool
    project_id: str


class VersionPreview(PublicResource):
    target_mode: Literal["ladder"]
    version_id: str
    read_only: bool
    ladder: LadderDocument
    program: PLCProgram
    svg: str
    st: str


class Finding(ExtensibleResource):
    code: str | None = None
    severity: str | None = None
    title: str | None = None
    message: str | None = None
    addresses: list[str] | None = None
    network_refs: list[str] | None = None
    details: EngineeringObject | None = None


class Diagnostics(ExtensibleResource):
    status: str | None = None
    valid: bool | None = None
    messages: list[str] | None = None
    findings: list[Finding] | None = None
    errors: list[JsonValue] | None = None
    warnings: list[JsonValue] | None = None
    variables: EngineeringObject | None = None
    values: EngineeringObject | None = None


class ReportDocument(ExtensibleResource):
    report_id: str
    report_type: str | None = None
    base_version_id: str | None = None
    status: str | None = None
    summary: str | None = None
    findings: list[Finding] | None = None
    created_at: str | None = None


class NativeAttachment(PublicResource):
    filename: str
    sha256: str
    size: int
    matches_source_bytes: bool
    selected_program_matches: bool
    integrity_verified: bool


class NativeRecord(PublicResource):
    id: str
    project_id: str
    version_id: str
    created_at: str
    source_gxw_sha256: str
    operator: str
    tool_version: str
    outcome: Literal["passed", "failed", "inconclusive"]
    report: str
    automatic_verification: str
    binding_current: bool
    native_gxw: NativeAttachment | None = None


class NativeEvidence(PublicResource):
    source_gxw_sha256: str
    automatic_verification: str
    records: list[NativeRecord]


class ExploreBounds(ExtensibleResource):
    top: float
    height: float
    display_number: int


class ExploreNetwork(ExtensibleResource):
    id: str
    comment: str
    reads: list[str]
    writes: list[str]
    instructions: list[Instruction]
    bounds: ExploreBounds


class AddressTarget(PublicResource):
    address: str
    x: float
    y: float
    width: float
    height: float


class Exploration(PublicResource):
    ir_sha256: str
    width: float
    height: float
    svg: str
    networks: list[ExploreNetwork]
    devices: EngineeringMap[DeviceRecord]
    address_targets: list[AddressTarget]


class IssueTest(PublicResource):
    plan_id: str
    test_names: list[str]


class IssueCard(ExtensibleResource):
    id: str
    source: str
    report_id: str | None
    severity: str
    title: str
    message: str
    suggestion: str
    evidence: list[JsonValue]
    addresses: list[str]
    networks: list[str]
    unresolved_networks: list[str]
    confidence: str
    tests: list[IssueTest]


class IssueList(PublicResource):
    version_id: str
    report_id: str | None
    issues: list[IssueCard]


class Expectation(ExtensibleResource):
    address: str
    operator: str
    value: JsonValue
    tolerance: float | None = None


class SimulationStep(ExtensibleResource):
    id: str
    at_ms: float
    set: EngineeringMap[int | float]
    expect: list[Expectation]
    wait_for: list[Expectation]
    timeout_ms: float | None = None
    poll_ms: float | None = None


class SimulationTest(ExtensibleResource):
    name: str
    description: str | None = None
    plc_model: str
    initial: EngineeringMap[int | float]
    steps: list[SimulationStep]
    sample_ms: float
    timeout_ms: float
    trace_devices: list[str]
    invariants: list[SimulationInvariant] | None = None
    fault_injections: list[SimulationFault] | None = None
    metadata: EngineeringObject | None = None


class SimulationSuite(PublicResource):
    schema_version: int | None = None
    name: str
    plc_model: str
    tests: list[SimulationTest]


class SimulationDraft(PublicResource):
    suite: SimulationSuite


class EvidenceBinding(ExtensibleResource):
    project_id: str | None = None
    version_id: str | None = None
    plan_id: str | None = None
    run_id: str | None = None
    ir_sha256: str | None = None
    suite_sha256: str | None = None
    result_sha256: str | None = None


class PlanBinding(EvidenceBinding):
    plan_id: str
    version_id: str


class Verification(ExtensibleResource):
    status: str
    category: str | None = None
    program_repair_allowed: bool | None = None


class SimulationPlan(ExtensibleResource):
    schema_version: int
    binding: PlanBinding
    source: str
    suite: SimulationSuite
    suite_sha256: str
    requirement_links: EngineeringMap[list[str]]
    issue_ids: list[str]
    source_plan_id: str | None


class RequirementRun(PublicResource):
    run_id: str
    test_name: str
    created_at: str | None
    backend_kind: str | None
    status: str
    recorded_status: str | None
    verification: Verification


class RequirementTest(PublicResource):
    plan_id: str
    test_name: str
    latest_run: RequirementRun | None


class Requirement(ExtensibleResource):
    id: str
    text: str
    devices: list[str]
    status: str
    latest_run: RequirementRun | None
    tests: list[RequirementTest]


class SimulationDevice(PublicResource):
    address: str
    label: str
    writable: bool


class OperatorOption(PublicResource):
    value: str
    label: str
    initial_value: JsonValue
    placeholder: str


class SimulationEditor(PublicResource):
    empty_suite: SimulationSuite
    default_expectation: Expectation
    default_input_value: int | float
    wait_timeout_ms: float
    poll_ms: float
    minimum_interval_ms: float
    maximum_duration_ms: float
    operators: list[OperatorOption]


class UnavailablePlan(PublicResource):
    plan_id: str | None
    message: str


class SimulationRunSummary(ExtensibleResource):
    run_id: str | None
    suite_name: str | None = None
    status: str | None = None
    created_at: str | None = None
    verification: Verification | None = None


class SimulationWorkbench(PublicResource):
    project_id: str
    version_id: str
    ir_sha256: str
    plc_model: str
    devices: list[SimulationDevice]
    plans: list[SimulationPlan]
    unavailable_plans: list[UnavailablePlan]
    unavailable_runs: list[str]
    requirements: list[Requirement]
    runs: list[SimulationRunSummary]
    editor: SimulationEditor


class Observation(PublicResource):
    at_ms: float
    event: str
    values: EngineeringMap[JsonValue]


class ReplayExpected(ExtensibleResource):
    operator: str | None = None
    value: JsonValue = None
    tolerance: float | None = None


class ReplayAssertion(ExtensibleResource):
    passed: bool | None = None
    at_ms: float | None = None
    step_id: str | None = None
    address: str | None = None
    actual: JsonValue = None
    expected: ReplayExpected | None = None
    message: str | None = None


class ReplayCase(PublicResource):
    name: str
    status: str
    backend_kind: str | None
    duration_ms: float | None
    observations: list[Observation]
    assertions: list[ReplayAssertion]
    invariant_violations: list[SimulationViolation]
    requirement_ids: list[str]
    issue_ids: list[str]
    steps: list[SimulationStep]
    sample_ms: float | None


class SimulationReplay(PublicResource):
    run_id: str
    version_id: str
    binding: EvidenceBinding
    verification: Verification
    status: str
    cases: list[ReplayCase]
    timeline: SimulationTimeline | None = None


class SimulationRun(ExtensibleResource):
    schema_version: int
    binding: EvidenceBinding
    verification: Verification
    suite: SimulationSuite
    result: SimulationResult


class HardwareTarget(PublicResource):
    logical_station: int
    target_label: str
    addresses: list[str]
    plc_model: str
    ttl_seconds: int


class HardwareAudit(ExtensibleResource):
    sequence: int
    created_at: str
    action: str
    addresses: list[str] | None = None
    values: EngineeringMap[int | float] | None = None
    reason: str | None = None


class HardwareSession(PublicResource):
    id: str
    status: str
    created_at: str
    approved_at: str | None
    expires_at: float | None
    remaining_seconds: int
    owned_by_current_operator: bool
    target: HardwareTarget
    project_id: str
    version_id: str
    audit: list[HardwareAudit]
    read_count: int
    target_verification: str


class HardwareReader(ExtensibleResource):
    available: bool
    message: str


class HardwareState(PublicResource):
    default_enabled: Literal[False]
    reader: HardwareReader
    sessions: list[HardwareSession]


class HardwareObservation(ExtensibleResource):
    values: EngineeringMap[int | float]
    logical_station: int
    target_label: str
    observed_at: str
    replayed: bool


class DeliveryArtifact(ExtensibleResource):
    id: str
    available: bool
    filename: str | None = None
    sha256: str | None = None


class DeliveryDeviceCommentChange(PublicResource):
    address: str
    before: str | None
    after: str | None


class DeliveryDiff(PublicResource):
    added: list[str]
    deleted: list[str]
    modified: list[str]
    device_comment_changes: list[DeliveryDeviceCommentChange]
    network_order_changed: bool
    property_changes: ProgramPropertyChanges


class DeliverySummary(ExtensibleResource):
    project_id: str
    project_name: str
    version_id: str
    created_at: str
    is_active_version: bool
    target_mode: str
    plc_model: str
    summary: str | None = None
    validation_profile: str | None = None
    ir_sha256: str | None = None
    confirmed_spec_hash: str | None = None
    decision_receipt_id: str | None = None
    decision_receipt_status: str | None = None
    artifacts: list[DeliveryArtifact]
    requirements: list[Requirement]
    simulation_runs: list[SimulationRunSummary]
    native_validation: NativeEvidence | None
    device_io: EngineeringMap[DeviceRecord]
    fbd_objects: list[FBDNode]
    fbd_declarations: EngineeringMap[list[FBDLabel]]
    limitations: list[str]
    markdown: str
    confirmed_spec: Specification | None = None
    changes: DeliveryDiff | None = None
