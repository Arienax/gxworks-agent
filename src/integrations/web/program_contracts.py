"""Source document wire shapes. PLC meaning and validation remain in Core."""
from __future__ import annotations

from typing import Literal
from pydantic import ConfigDict, Field, JsonValue, StrictBool, StrictInt
from .response_types import EngineeringMap, EngineeringObject, EngineeringValue, ExtensibleResource, PublicResource


class Instruction(ExtensibleResource):
    op: str
    args: list[str] = Field(default_factory=list)
    kind: Literal['instruction', 'opaque', 'label', 'statement', 'note'] | None = None
    record_index: int | None = None
    source_offset: int | None = None
    raw_hex: str | None = None
    step: int | None = None
    text: str | None = None
    reason: str | None = None


class PLCDescriptor(ExtensibleResource):
    cpu: str


class IRNetwork(ExtensibleResource):
    id: str
    comment: str = ""
    instructions: list[Instruction]


class DeviceRecord(ExtensibleResource):
    comment: str | None = None
    label: str | None = None
    kind: str | None = None
    read_by: list[str] = Field(default_factory=list)
    written_by: list[str] = Field(default_factory=list)


class LadderElement(ExtensibleResource):
    type: str
    address: str | None = None
    label: str | None = None
    expression: str | None = None
    value: str | int | float | None = None
    opcode: str | None = None
    operands: list[str] | None = None
    branches: list[list[LadderElement]] | None = None


class LadderBranch(ExtensibleResource):
    branch_id: int | None = None
    y_offset_level: int | None = None
    inputs: list[LadderElement]
    outputs: list[LadderElement]


class LadderRung(ExtensibleResource):
    rung_id: int | None = None
    debug_note: str | None = None
    header_element: LadderElement | None = None
    shared_inputs: list[LadderElement] | None = None
    branches: list[LadderBranch] = Field(default_factory=list)
    ordered_instructions: list[Instruction] | None = None


class ProgramRequirement(ExtensibleResource):
    semantic: str
    devices: list[str]
    evidence: str
    source: str
    strict: StrictBool
    effect_devices: list[str] | None = None
    effect_kind: str | None = None
    rearm: str | None = None
    intent_id: str | None = None
    period_ms: float | None = None
    pulse_width_ms: float | None = None


class ExecutionTrigger(ExtensibleResource):
    semantic: str
    path: str
    device: str | None = None
    period_ms: float | None = None


class NetworkSemantics(ExtensibleResource):
    network: str
    execution_context: str
    semantics: list[str]
    triggers: list[ExecutionTrigger]


class ProgramEdgeTrigger(ExtensibleResource):
    network: str
    device: str
    edge: str


class ProgramCyclicSource(ExtensibleResource):
    network: str
    device: str | None
    period_ms: float | None
    path: str


class ProgramPreset(ExtensibleResource):
    network: str
    preset: str


class ProgramInitialization(ExtensibleResource):
    network: str
    status: str
    trigger_devices: list[str]
    writes: list[str]


class ProgramSemanticCoverage(ExtensibleResource):
    requirement_id: str
    semantic: str
    devices: list[str]
    strict: StrictBool
    status: str
    network_refs: list[str]
    message: str
    pulse_width_ms: float | None = None
    period_ms: float | None = None


class TimingSource(ExtensibleResource):
    manual: str
    section: str
    pdf_pages: list[int] | None = None
    pdf_page: int | None = None


class InstructionTiming(ExtensibleResource):
    opcode: str
    supported: StrictBool
    known: StrictBool
    best_us: float | None = None
    typical_us: float | None = None
    worst_us: float | None = None
    confidence: str
    path: str | None = None
    source_pdf_page: int | None = None
    basis: str | None = None
    parameter_count: int | None = None
    parameter_count_exact: StrictBool | None = None


class ScanEstimate(ExtensibleResource):
    best_ms: float | None
    typical_ms: float | None
    worst_ms: float | None
    is_exact: StrictBool
    instruction_coverage: float
    known_instruction_count: int
    instruction_count: int
    unknown_opcodes: list[str]
    uncertainty: list[str]


class ScanMonitorDevices(ExtensibleResource):
    current: str | None = None
    minimum: str | None = None
    maximum: str | None = None


class ProgramScanMonitor(ExtensibleResource):
    available: StrictBool
    enabled: StrictBool
    read_only: StrictBool
    devices: ScanMonitorDevices
    unit_ms: float | None
    warning_ms: float
    reason: str | None = None
    source: TimingSource | None = None


class ProgramScanBudget(ExtensibleResource):
    configured: StrictBool
    budget_ms: float | None
    status: str
    estimated_worst_ms: float | None
    allocation_ms: EngineeringMap[float]


class ScanCycleOverhead(ExtensibleResource):
    estimated_us: float
    basis: str
    referenced_input_points: int
    referenced_output_points: int


class NetworkTiming(ExtensibleResource):
    network: str
    order: int
    primary_region: str
    instruction_count: int
    best_ms: float
    typical_ms: float
    worst_ms: float
    instructions: list[InstructionTiming]


class HighCostInstruction(ExtensibleResource):
    network: str
    opcode: str
    path: str
    worst_us: float
    confidence: str


class ProgramScanPerformance(ExtensibleResource):
    schema_version: int
    profile: str
    supported: StrictBool
    source: TimingSource
    estimate: ScanEstimate
    cycle_overhead: ScanCycleOverhead
    networks: list[NetworkTiming]
    region_worst_ms: EngineeringMap[float]
    high_cost_instructions: list[HighCostInstruction]
    scan_monitor: ProgramScanMonitor
    scan_budget: ProgramScanBudget


class PulseCaptureAssessment(ExtensibleResource):
    devices: list[str]
    pulse_width_ms: float
    requirement_ids: list[str]
    semantics: list[str]
    network_refs: list[str]
    evidence: list[str]
    interrupt_requested: StrictBool
    interrupt_path_declared: StrictBool
    status: str
    decision: str
    estimated_worst_scan_ms: float | None
    scan_budget_ms: float | None
    comparison_bound_ms: float | None
    comparison_basis: list[str]
    instruction_coverage: float | None
    is_guarantee: StrictBool


class ProgramTiming(ExtensibleResource):
    schema_version: int | None = None
    scan_model: str | None = None
    supported_semantics: list[str] | None = None
    network_semantics: list[NetworkSemantics] | None = None
    first_scan_networks: list[str] | None = None
    edge_triggers: list[ProgramEdgeTrigger] | None = None
    cyclic_sources: list[ProgramCyclicSource] | None = None
    interrupt_networks: list[str] | None = None
    timers: EngineeringMap[ProgramPreset] | None = None
    counters: EngineeringMap[ProgramPreset] | None = None
    initialization: list[ProgramInitialization] | None = None
    requirements: list[ProgramRequirement] | None = None
    coverage: list[ProgramSemanticCoverage] | None = None
    performance: ProgramScanPerformance | None = None
    pulse_capture_assessments: list[PulseCaptureAssessment] | None = None


class ProgramActionRef(ExtensibleResource):
    network: str
    path: str


class ProgramLogicRegion(ExtensibleResource):
    code: str
    kind: str
    label: str
    action_refs: list[ProgramActionRef]
    network_refs: list[str]


class ProgramState(ExtensibleResource):
    value: int
    name: str
    network_refs: list[str]


class ProgramCondition(ExtensibleResource):
    path: str
    type: str
    address: str
    expression: str
    label: str
    semantic: str
    period_ms: float | None = None


class ProgramStateTransition(ProgramActionRef):
    model_config = ConfigDict(serialize_by_alias=True)
    id: str
    from_: int | None = Field(alias="from")
    to: int
    label: str
    conditions: list[ProgramCondition]
    devices: list[str]
    semantics: list[str]


class ProgramStateInitialization(ProgramActionRef):
    target_state: int


class ProgramStateOutput(ProgramActionRef):
    state: int
    op: str
    args: list[str]
    label: str


class ProgramStateSeparation(ExtensibleResource):
    logical_regions_separate: StrictBool
    mixed_physical_networks: list[str]


class ProgramStateMachine(ExtensibleResource):
    id: str
    type: str
    state_register: str
    states: list[ProgramState]
    initialization: list[ProgramStateInitialization]
    transitions: list[ProgramStateTransition]
    state_outputs: list[ProgramStateOutput]
    separation: ProgramStateSeparation
    unreachable_state_candidates: list[int]
    dead_end_state_candidates: list[int]


class ProgramLogic(ExtensibleResource):
    schema_version: int | None = None
    execution_model: str | None = None
    requirements: list[ProgramRequirement] | None = None
    requirements_sha256: str | None = None
    regions: list[ProgramLogicRegion] | None = None
    state_machines: list[ProgramStateMachine] | None = None


class ProgramDeviceAccess(ExtensibleResource):
    network: str
    order: int
    rung_id: int | None
    path: str | None = None
    op: str | None = None
    reads: list[str] | None = None


class ProgramDependencyEdge(ExtensibleResource):
    model_config = ConfigDict(serialize_by_alias=True)
    from_: str = Field(alias="from")
    to: str
    network: str
    rung_id: int | None
    feedback: StrictBool
    precision: str


class ProgramScanDependency(ExtensibleResource):
    device: str
    writer_network: str
    writer_order: int
    reader_network: str
    reader_order: int
    relation: str


class ProgramDependencyGraph(ExtensibleResource):
    schema_version: int
    precision: str
    nodes: list[str]
    device_edges: list[ProgramDependencyEdge]
    forward: EngineeringMap[list[str]]
    reverse: EngineeringMap[list[str]]
    writers: EngineeringMap[list[ProgramDeviceAccess]]
    readers: EngineeringMap[list[ProgramDeviceAccess]]
    network_edges: list[ProgramScanDependency]


class ProgramMutexPair(ExtensibleResource):
    devices: list[str]
    source: str


class ProgramMutexCheck(ProgramActionRef):
    target: str
    other: str
    writer: str
    protected: StrictBool


class ProgramMutexResult(ProgramMutexPair):
    status: str
    checks: list[ProgramMutexCheck]


class ProgramScanExpectation(ExtensibleResource):
    device: str
    reader_network: str
    source: str
    writer_network: str | None = None
    status: str | None = None


class ProgramTimingConfig(ExtensibleResource):
    scan_budget_ms: float | None
    scan_warning_ms: float
    allocation: EngineeringObject


class ProgramAnalysisConfig(ExtensibleResource):
    mutex: list[ProgramMutexPair]
    same_scan_expectations: list[ProgramScanExpectation]
    terminal_states: EngineeringMap[list[int]]
    timing: ProgramTimingConfig


class ProgramStateAnalysis(ExtensibleResource):
    state_machine: str
    state_register: str
    unreachable_states: list[int]
    dead_end_states: list[int]
    terminal_states: list[int]


class ProgramFinding(ExtensibleResource):
    code: str
    category: str
    severity: str
    message: str
    suggestion: str = ''
    addresses: list[str] = Field(default_factory=list)
    network_refs: list[str] = Field(default_factory=list)
    rung_ids: list[int]
    json_paths: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: str = 'unknown'
    fixable: StrictBool = False


class ProgramFindingCounts(ExtensibleResource):
    error: int
    warning: int
    info: int
    unverified: int | None = None


class ProgramAnalysis(ExtensibleResource):
    schema_version: int | None = None
    plc_model: str | None = None
    config: ProgramAnalysisConfig | None = None
    rules_checked: list[str] | None = None
    dependency_graph: ProgramDependencyGraph | None = None
    mutex: list[ProgramMutexResult] | None = None
    same_scan_expectations: list[ProgramScanExpectation] | None = None
    state_analysis: list[ProgramStateAnalysis] | None = None
    findings: list[ProgramFinding] | None = None
    counts: ProgramFindingCounts | None = None


class ProgramSource(ExtensibleResource):
    format: str | None = None
    ladder_sha256: str | None = None


class PLCProgram(ExtensibleResource):
    kind: Literal["plc_program_ir"]
    schema_version: int
    plc: PLCDescriptor
    program_name: str
    revision: JsonValue
    networks: list[IRNetwork]
    devices: EngineeringMap[DeviceRecord]
    io_map: EngineeringMap[JsonValue]
    timing: ProgramTiming
    logic: ProgramLogic
    analysis: ProgramAnalysis
    source: ProgramSource
    metadata: EngineeringObject | None = None


class LadderDocument(ExtensibleResource):
    rungs: list[LadderRung]
    device_comments: EngineeringMap[str] = Field(default_factory=dict)


class FBDPort(ExtensibleResource):
    name: str
    x: int
    y: int
    formal_name: str | None = None
    side: str | None = None
    data_type: str | None = None


class FBDPortEdit(PublicResource):
    negated: StrictBool


class FBDNodeSize(PublicResource):
    width: int | None = None
    height: int | None = None


class FBDStoredPort(PublicResource):
    flags: StrictInt
    x: StrictInt
    y: StrictInt


class FBDPortLayout(PublicResource):
    width: StrictInt
    height: StrictInt
    ports: list[FBDStoredPort]


class FBDNode(ExtensibleResource):
    id: str
    template: str
    symbol: str
    x: int
    y: int
    source_offset: int | None = None
    prototype_offset: int | None = None
    width: int | None = None
    height: int | None = None
    ports: list[FBDPort] | None = None
    port_edits: EngineeringMap[FBDPortEdit] | None = None
    block: int | None = None
    resize: FBDNodeSize | None = None
    refresh_interface: StrictBool | None = None
    port_layout: FBDPortLayout | None = None


class FBDWire(ExtensibleResource):
    model_config = ConfigDict(serialize_by_alias=True)
    kind: Literal['wire', 'junction'] | None = None
    source_offset: int | None = None
    start: list[int] | None = None
    end: list[int] | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    via: list[list[int]] | None = None
    block: int | None = None


class FBDMemberDevice(PublicResource):
    device: str
    iec_address: str


class StructureArrayAddresses(PublicResource):
    word_address: str
    word_offset: str
    word_use_bit: Literal[0, 1]
    bit_address: str
    bit_offset: str


class FBDLabel(ExtensibleResource):
    name: str
    data_type: str | None = None
    kind: str | None = None
    class_name: str | None = None
    initial_value: str | None = None
    device: str | None = None
    iec_address: str | None = None
    comment: str | None = None
    member_devices: EngineeringMap[FBDMemberDevice] | None = None
    structure_array_addresses: StructureArrayAddresses | None = None


class DeclarationEdit(PublicResource):
    upserts: list[FBDLabel] = Field(default_factory=list)
    renames: EngineeringMap[str] = Field(default_factory=dict)
    remove: list[str] = Field(default_factory=list)
    order: list[str] | None = None
    return_type: str | None = None


class SourceIssue(ExtensibleResource):
    code: str | None = None
    message: str | None = None


class FBDBlock(PublicResource):
    source_offset: int | None = None
    canvas_height: int
    title: str | None = None
    comment: str | None = None
    index: int | None = None


class FBDModel(ExtensibleResource):
    schema_version: int
    program: str
    nodes: list[FBDNode]
    wires: list[FBDWire]
    canvas_height: int | None = None
    labels: EngineeringMap[list[FBDLabel]] | None = None
    declaration_edits: EngineeringMap[DeclarationEdit] | None = None
    unknown_record_count: int | None = None
    issues: list[SourceIssue] | None = None
    cpu: str | None = None
    metadata: EngineeringObject | None = None
    blocks: list[FBDBlock] | None = None


class DefinitionInterfaceParameter(ExtensibleResource):
    name: str
    data_type: str | None = None
    declared_type: str | None = None
    class_code: int | None = None
    type_code: int | None = None


class DefinitionInterface(ExtensibleResource):
    inputs: list[DefinitionInterfaceParameter]
    outputs: list[DefinitionInterfaceParameter]


class SourceDefinition(ExtensibleResource):
    name: str
    kind: str
    language: str | None = None
    program: str | None = None
    declarations: list[FBDLabel] | None = None
    members: list[FBDLabel] | None = None
    model: FBDModel | None = None
    interface: DefinitionInterface | None = None
    st_text: str | None = None


class PropertyChange(PublicResource):
    before: JsonValue
    after: JsonValue


class EngineeringPropertyChange(PublicResource):
    before: EngineeringValue
    after: EngineeringValue


class TimingPropertyChange(PublicResource):
    before: ProgramTiming | None
    after: ProgramTiming | None


class AnalysisPropertyChange(PublicResource):
    before: ProgramAnalysis | None
    after: ProgramAnalysis | None


class LabelsPropertyChange(PublicResource):
    before: EngineeringMap[list[FBDLabel]] | None
    after: EngineeringMap[list[FBDLabel]] | None


class ProgramPropertyChanges(ExtensibleResource):
    metadata: EngineeringPropertyChange | None = None
    variables: EngineeringPropertyChange | None = None
    structs: EngineeringPropertyChange | None = None
    labels: LabelsPropertyChange | None = None
    io_map: EngineeringPropertyChange | None = None
    device_comments: EngineeringPropertyChange | None = None
    timing: TimingPropertyChange | None = None
    analysis: AnalysisPropertyChange | None = None


class SFCRegistration(PublicResource):
    qualifier: int
    zoom: str


class SFCBinding(ExtensibleResource):
    status: str
    step_reference: int | None = None
    action_number: int | None = None
    transition_number: int | None = None
    child: str | None = None
    registrations: list[SFCRegistration] | None = None


class SFCCell(ExtensibleResource):
    id: str
    x: int
    y: int
    type: int
    result: int
    role: str
    binding: SFCBinding


class SFCSymbol(ExtensibleResource):
    cell: str
    kind: str
    ports: list[Literal["down", "up", "right", "left"]]
    reference: int | None = None
    initial: bool | None = None


class SFCSegment(ExtensibleResource):
    source: str
    target: str
    direction: str
    style: str


class SFCJump(ExtensibleResource):
    source: str
    step_reference: int
    targets: list[str]
    status: str


class SFCTopologyIssue(ExtensibleResource):
    reason: str
    cell: str | None = None
    branch: int | None = None
    port: str | None = None


class SFCTopology(ExtensibleResource):
    status: str
    symbols: list[SFCSymbol]
    segments: list[SFCSegment]
    jumps: list[SFCJump]
    issues: list[SFCTopologyIssue]


class SFCGraphRecord(ExtensibleResource):
    kind: str
    references: list[int]


class SFCWriteSymbol(ExtensibleResource):
    kind: str
    number: int | None = None
    x: int | None = None
    y: int | None = None
    start: int | None = None
    end: int | None = None
    action_number: int | None = None


class SFCSourceNode(ExtensibleResource):
    id: str
    kind: str
    number: int | None = None
    action_number: int | None = None
    child_names: list[str] = Field(default_factory=list)
    binding_status: str | None = None


class SFCSourceEdge(ExtensibleResource):
    source: str
    target: str
    kind: str


class SFCSourceGraph(ExtensibleResource):
    status: str
    nodes: list[SFCSourceNode]
    edges: list[SFCSourceEdge]
    position_resolution: str
    issues: list[EngineeringObject]
    linear_symbols: list[SFCWriteSymbol] | None = None


class SFCGraph(ExtensibleResource):
    position_resolution: str
    edit_profile: str | None = None
    width: int | None = None
    height: int | None = None
    cells: list[SFCCell] | None = None
    topology: SFCTopology | None = None
    write_symbols: list[SFCWriteSymbol] | None = None
    records: list[SFCGraphRecord]
    source_graph: SFCSourceGraph | None = None


class SFCChildRecord(ExtensibleResource):
    kind: str
    op: str | None = None
    args: list[str] | None = None
    raw_hex: str
    native_requirements: EngineeringObject | None = None


class SFCChild(ExtensibleResource):
    kind: str
    name: str
    number: int | None
    records: list[SFCChildRecord]


class SFCAction(ExtensibleResource):
    name: str
    number: int
    registrations: list[SFCRegistration]


class SFCModel(ExtensibleResource):
    program: str
    cpu: str
    codepage: int
    graph: SFCGraph
    actions: list[SFCAction]
    children: list[SFCChild]
    issues: list[str | SourceIssue]
    metadata: EngineeringObject | None = None


ProgramDocument = PLCProgram | FBDModel | SFCModel | LadderDocument


class CatalogNode(ExtensibleResource):
    template: str
    kind: str
    symbol: str
    width: int
    height: int
    ports: list[FBDPort]


class FBDPresentationNode(ExtensibleResource):
    id: str
    symbol_editable: bool
    resizable: bool | None = None
    callable: bool | None = None
    ports: list[FBDPort]


class FBDEndpoint(ExtensibleResource):
    value: str
    label: str
    point: list[int]


class TaskProgram(PublicResource):
    name: str
    line: int


class TaskScope(PublicResource):
    resource: str
    task: str
    programs: list[TaskProgram]
    event_name: str | None = None
    interval: str | None = None
    priority: int | None = None


class TaskScopes(ExtensibleResource):
    status: str
    resources: list[str]
    tasks: list[TaskScope]
    message: str | None = None


class UnsupportedModel(PublicResource):
    cpu: str
    status: str
    reason: str


class FBDPresentation(ExtensibleResource):
    tables: list[str]
    rows: EngineeringMap[list[FBDLabel]]
    nodes: list[FBDPresentationNode]
    endpoints: list[FBDEndpoint]
    blocks: list[FBDBlock] = Field(default_factory=list)


class FBDEditorView(ExtensibleResource):
    model: FBDModel
    presentation: FBDPresentation
    catalog: list[CatalogNode]
    programs: list[str]
    task_scopes: TaskScopes
    source_svg: str
    gx_compile: str | None = None


class FBDParameterCall(ExtensibleResource):
    id: str
    type: str
    instance: str
    arguments: EngineeringMap[str] | None = None


class FBDParameterNode(ExtensibleResource):
    id: str
    template: str
    symbol: str | None = None
    arguments: EngineeringMap[str] | None = None
    port_edits: EngineeringMap[FBDPortEdit] | None = None


class FBDParameterWire(ExtensibleResource):
    model_config = ConfigDict(serialize_by_alias=True)
    from_: str = Field(alias="from")
    to: str


class FBDParameterNetwork(ExtensibleResource):
    name: str | None = None
    comment: str | None = None
    source_block: int | None = None
    calls: list[FBDParameterCall] | None = None
    nodes: list[FBDParameterNode] | None = None
    wires: list[FBDParameterWire] | None = None


class FBDParameterModel(ExtensibleResource):
    networks: list[FBDParameterNetwork]
    schema_version: int | None = None
    program: str | None = None
    preserve_blocks: list[int] | None = None
    declaration_edits: EngineeringMap[DeclarationEdit] | None = None


class FBDAllocationDifference(ExtensibleResource):
    workspace: list[int]
    gui: list[int]


class FBDStoredAllocationField(ExtensibleResource):
    storage_type: str
    value: int


class FBDGUIConfiguration(ExtensibleResource):
    status: str
    allocation_profile: str
    cpu_menu: str
    workspace_source: str
    workspace_ranges: EngineeringMap[list[int]]
    gui_mirror_status: str
    source: str
    missing: list[str]
    differences: EngineeringMap[FBDAllocationDifference]
    word_device: int | None
    bit_device: int | None
    effective_ranges: EngineeringMap[list[int]]
    stored_ranges: EngineeringMap[list[int]]
    retained_fields: EngineeringMap[FBDStoredAllocationField]
    unverified_selectors: list[str]
    reason: str | None = None


class FBDProjectConfiguration(ExtensibleResource):
    source_name: str
    system_variable_ranges: EngineeringMap[list[int]]
    gui_configuration: FBDGUIConfiguration


class FBDCatalog(ExtensibleResource):
    schema_version: int
    program: str
    cpu: str
    nodes: list[CatalogNode]
    programs: list[str]
    empty_model: FBDModel
    generation_plc_models: list[str]
    unsupported_generation_plc_models: list[UnsupportedModel]
    task_scopes: TaskScopes
    # This is a JSON Schema document; arbitrary property names are schema data.
    parameter_schema: EngineeringObject
    parameter_model: FBDParameterModel
    empty_parameter_model: FBDParameterModel
    project_configuration: FBDProjectConfiguration
    declaration_tables: EngineeringMap[str]


class FBDInspection(ExtensibleResource):
    programs: list[str]


class FBDDraftPreview(ExtensibleResource):
    model: FBDModel
    svg: str
    gx_compile: str
    model_sha256: str
    gxw_sha256: str


class SFCSource(ExtensibleResource):
    model: SFCModel
    programs: list[str]
    gx_compile: str
