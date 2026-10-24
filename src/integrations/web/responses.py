"""Public HTTP resources; application owners select the public projection."""
from __future__ import annotations
from typing import Literal
from pydantic import ConfigDict, Field, JsonValue, StrictBool, model_validator
from .response_types import (JsonObject, EngineeringObject, EngineeringMap, EngineeringValue, PublicResource,
    ExtensibleResource, PublicObject, NullablePublicObject, _require_public)
from .program_contracts import (ProgramDocument, PLCProgram, LadderDocument, FBDModel, SFCModel,
    FBDNode, FBDLabel, FBDCatalog, FBDEditorView, FBDInspection, FBDDraftPreview, SFCSource,
    DeclarationEdit, IRNetwork, ProgramTiming, FBDParameterModel, SourceDefinition,
    PropertyChange, ProgramPropertyChanges, SourceIssue)

from .workbench_contracts import *
from .native_contracts import NativeCheck, NativeDiagnostic, NativeCheckState


class MCPIntegrationStatus(PublicResource):
    service_url: str
    project_id: str
    bound_project_id: str | None
    credential_ready: bool
    launcher_ready: bool
    codex_configured: bool
    codex_cli_available: bool
    codex_command: str | None
    client_observed: bool
    last_tool: str | None
    last_call_at: str | None
    generation_context_observed: bool
    candidate_proposal_id: str | None


class MCPIntegrationResult(PublicResource):
    status: Literal["connected", "failed"]
    project_id: str
    message: str
    service_url: str | None = None
    tool_count: int | None = Field(default=None, ge=0)
    codex_connected: bool | None = None
    replaced_existing: bool | None = None


class Artifact(PublicResource):
    id: str
    available: bool
    filename: str | None = None
    media_type: str | None = None
    size: int | None = Field(default=None, ge=0)


class Validation(ExtensibleResource):
    # Absence of an old validation record is unknown, never an implicit pass.
    status: str | None = None
    messages: list[str] = Field(default_factory=list)


class ProjectModeOption(PublicResource):
    value: str
    label: str


class ProjectCreationOptions(PublicResource):
    plc_model: str
    default_target_mode: str
    starter_requirement: str
    target_modes: list[ProjectModeOption]
    native_targets: list[JsonObject] = Field(default_factory=list)


class Capabilities(ExtensibleResource):
    creation: ProjectCreationOptions | None = None
    body_form: str
    operations: dict[str, bool]
    project_type: str | None = None
    representations: list[str] = Field(default_factory=list)
    sfc: str | None = None
    structured_semantics: str | None = None
    target_support: JsonObject | None = None


class Version(PublicResource):
    id: str
    created_at: str | None = None
    target_mode: str | None = None
    plc_model: str | None = None
    program_name: str | None = None
    summary: str | None = None
    validation: Validation | None = None
    lifecycle_status: str | None = None
    parent_version_id: str | None = None
    ir_sha256: str | None = None
    confirmed_spec_hash: str | None = None
    confirmed_spec_snapshot: Specification | None = None
    gx_sync: JsonObject | None = None
    simulator_runs: list[JsonObject] | None = None
    simulator_test_plans: list[JsonObject] | None = None
    debug_attempts: list[JsonObject] | None = None
    generation_handoff: JsonObject | None = None
    maintainability_review: JsonObject | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    capabilities: Capabilities


class ProjectMessage(PublicResource):
    id: str | None = None
    role: str | None = None
    kind: str | None = None
    content: str | None = None
    created_at: str | None = None


class ReportSummary(PublicResource):
    report_id: str
    report_type: str | None = None
    status: str | None = None
    summary: str | None = None
    created_at: str | None = None
    base_version_id: str | None = None


class Project(PublicResource):
    id: str
    name: str
    plc_model: str
    native_project_type: str | None = None
    use_labels: bool | None = None
    # Capability declarations, rather than this representation name, control
    # available operations. Future body forms need not become a second IR.
    target_mode: str
    version_count: int = Field(ge=0)
    created_at: str | None = None
    updated_at: str | None = None
    effort: str | None = None
    active_version_id: str | None = None
    confirmed_spec: Specification | None = None
    confirmed_spec_hash: str | None = None
    versions: list[Version] = Field(default_factory=list)
    messages: list[ProjectMessage] = Field(default_factory=list)
    reports: list[ReportSummary] = Field(default_factory=list)


class ProjectList(PublicResource):
    projects: list[Project] = Field(default_factory=list)


class GXWSourceSummary(PublicResource):
    source_name: str
    name: str
    language: Literal['ladder', 'st', 'fbd', 'sfc'] | None
    program_kind: int | None
    unit_kind: str
    readable: bool
    offline_editable: bool
    reason: str | None
    parse_status: str | None = None
    preserved_records: int | None = None
    capabilities: EngineeringObject | None = None
    native_requirements: list[EngineeringObject] | None = None


class GXWInspection(PublicResource):
    cpu_menu: str
    codepage: int
    programs: list[GXWSourceSummary]
    selected_source: GXWSourceSummary | None
    device_comments: EngineeringMap[str]
    comment_tables: list[EngineeringObject]
    declaration_tables: list[EngineeringObject]
    issues: list[EngineeringObject]
    write_backend: Literal['offline']
    integrity: EngineeringObject
    native_required: Literal[False]
    native_compile: Literal['not_run']
    runtime_verified: Literal[False]
    coverage: EngineeringObject | None = None
    networks: list[EngineeringObject] | None = None
    source_records: list[EngineeringObject] | None = None


class GXWImportResult(PublicResource):
    status: str
    project_id: str
    version_id: str | None
    proposal_id: str
    project: Project


class GXWSourceView(PublicResource):
    project_id: str
    version_id: str
    source_name: str
    language: Literal['ladder', 'st', 'fbd', 'sfc']
    unit_kind: str
    model: EngineeringObject
    source_records: list[EngineeringObject] | None = None
    svg: str | None = None
    programs: list[GXWSourceSummary]
    declaration_tables: list[EngineeringObject]
    capabilities: EngineeringObject
    write_backend: Literal['offline']
    native_required: Literal[False]


class ResponseViolation(PublicResource):
    path: str
    reason: Literal["unsupported_script", "non_english_script", "japanese_script", "latin_prose",
                    "ambiguous_han_only", "invalid_prose_field", "invalid_json_object", "invalid_code_field", "invalid_response",
                    "invalid_shared_input", "invalid_ladder_structure", "field_too_long", "repair_base_invalid",
                    "repair_identity_invalid", "repair_shape_invalid", "repair_scope_violation", "repair_no_progress"]
    observed_opcode: str | None = Field(
        default=None, max_length=64, pattern=r"^[A-Z0-9_.$@+\-]+$"
    )


class JobErrorDetails(PublicResource):
    response_language: Literal["zh-CN", "en", "ja", "unknown"]
    contract_name: str
    diagnostic_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{16}$")
    violations: list[ResponseViolation] = Field(default_factory=list, max_length=16)
    violation_count: int = Field(ge=0, le=1000000)
    truncated: bool
    repairable: bool = False
    stage: Literal["generation_validation"] | None = None
    attempt_count: int | None = Field(default=None, ge=0, le=3)
    max_attempts: int | None = Field(default=None, ge=0, le=3)
    stop_reason: Literal["attempt_limit", "time_budget", "final_validation"] | None = None


class Job(PublicResource):
    revision: int | None = Field(default=None, ge=1)
    id: str
    kind: str
    status: str
    last_sequence: int = Field(ge=0)
    project_id: str | None = None
    version_id: str | None = None
    created_at: str
    updated_at: str | None = None
    cancel_requested: bool | None = None
    result: JsonObject | None = None
    error_code: str | None = None
    error_details: JobErrorDetails | None = None
    model_usage: JsonObject = Field(default_factory=dict)


class JobList(PublicResource):
    jobs: list[Job] = Field(default_factory=list)


class JobEvent(PublicResource):
    job_id: str
    project_id: str | None = None
    version_id: str | None = None
    sequence: int = Field(ge=1)
    event_type: str
    payload: EventPayload = Field(default_factory=EventPayload)
    created_at: str


class JobOutput(ExtensibleResource):
    clarification_job_id: str | None = None
    missing_info: list[JsonObject] | None = None
    version_id: str | None = None
    analysis: Specification | None = None
    spec_draft: Specification | None = None
    spec_base_hash: str | None = None
    base_version_id: str | None = None
    generation: JsonObject | None = None
    proposal_id: str | None = None
    proposal_ids: list[str] | None = None
    report_id: str | None = None
    report: JsonObject | None = None
    plan_id: str | None = None
    plan: JsonObject | None = None
    status: str | None = None
    content: str | None = None
    audit: list[JsonObject] | JsonObject | None = None


class Proposal(PublicResource):
    revision: int | None = Field(default=None, ge=1)
    execution_job_id: str | None = None
    id: str
    action: str
    project_id: str
    base_version_id: str | None = None
    status: str
    created_at: str | None = None
    updated_at: str | None = None
    summary: JsonObject = Field(default_factory=dict)
    result: JsonObject | None = None
    error_code: str | None = None


class ProposalList(PublicResource):
    proposals: list[Proposal] = Field(default_factory=list)


class ProposalDecisionResult(PublicResource):
    proposal: Proposal | None = None
    job: Job | None = None


class NetworkChange(PublicResource):
    marker: Literal["+", "-", "~"]
    network: str
    comment: str
    instruction_count: int
    before: IRNetwork | None
    after: IRNetwork | None


class DeviceCommentChange(PublicResource):
    address: str
    before: str | None
    after: str | None


class GXWCommentChange(DeviceCommentChange):
    table: str
    address: str | None
    reason: str | None = None


class GXWSourceChange(PublicResource):
    source_name: str
    language: Literal['ladder', 'st', 'fbd', 'sfc']
    unit_kind: str
    unified_diff: str


class LadderProgramDiff(PublicResource):
    kind: Literal["ladder"]
    base_version_id: str | None = None
    has_changes: bool
    added: list[str]
    deleted: list[str]
    modified: list[str]
    changes: list[NetworkChange]
    device_comments_changed: bool
    device_comment_changes: list[DeviceCommentChange]
    before_network_count: int
    after_network_count: int
    before_network_order: list[str]
    after_network_order: list[str]
    network_order_changed: bool
    property_changes: ProgramPropertyChanges
    unified_diff: str | None = None
    definition_changed: bool | None = None
    definition_changes: EngineeringObject | None = None
    comment_changes: list[GXWCommentChange] = Field(default_factory=list)
    source_changes: list[GXWSourceChange] = Field(default_factory=list)


class STTextDiff(PublicResource):
    kind: Literal["st"]
    base_version_id: str | None = None
    has_changes: bool
    before: str
    after: str
    unified_diff: str
    definition_changed: bool | None = None
    definition_changes: EngineeringObject | None = None
    comment_changes: list[GXWCommentChange] = Field(default_factory=list)
    source_changes: list[GXWSourceChange] = Field(default_factory=list)


class LadderPreview(PublicResource):
    target_mode: Literal["ladder"]
    ladder: LadderDocument
    program: PLCProgram
    svg: str
    st: str
    diff: LadderProgramDiff


class STPreview(PublicResource):
    target_mode: Literal["st"]
    st: str
    diff: STTextDiff


class FBDLibraryChanges(PublicResource):
    added: list[str]
    definitions: list[str]
    structures: list[str]
    global_tables: list[str]


class FBDDefinitionChanges(PublicResource):
    before: SourceDefinition | None
    after: SourceDefinition


class SavedCodeChannel(PublicResource):
    channel: int
    saved_bytes: int
    generated_bytes: int
    matches: StrictBool


class SavedCodeResource(PublicResource):
    resource: str
    status: str
    channels: list[SavedCodeChannel]


class SavedCodeComparison(PublicResource):
    status: str
    resources: list[SavedCodeResource]


class FBDCompilationSave(PublicResource):
    public_check: NativeCheckState
    saved_code: SavedCodeComparison
    gui_configuration: str
    execution: str


class FBDDiff(PublicResource):
    kind: Literal["fbd"]
    base_version_id: str | None = None
    has_changes: bool
    unified_diff: str
    before_object_count: int
    after_object_count: int
    before_wire_count: int
    after_wire_count: int
    declarations_changed: bool
    library_changes: FBDLibraryChanges | None = None
    definition_changes: FBDDefinitionChanges | None = None
    compilation_save: FBDCompilationSave | None = None
    definition_changed: bool | None = None
    comment_changes: list[GXWCommentChange] = Field(default_factory=list)
    source_changes: list[GXWSourceChange] = Field(default_factory=list)
    declaration_changes: EngineeringObject | None = None


class FBDPreviewResource(PublicResource):
    target_mode: Literal["fbd"]
    program: FBDModel
    svg: str
    diff: FBDDiff
    editor: FBDEditorView


class SFCModifiedChild(PublicResource):
    kind: str
    name: str


class SFCDiff(PublicResource):
    kind: Literal['sfc']
    base_version_id: str | None = None
    has_changes: bool
    modified_children: list[SFCModifiedChild]
    unified_diff: str
    definition_changed: bool | None = None
    definition_changes: EngineeringObject | None = None
    comment_changes: list[GXWCommentChange] = Field(default_factory=list)
    source_changes: list[GXWSourceChange] = Field(default_factory=list)
    declaration_changes: EngineeringObject | None = None


class SFCPreviewValidation(ExtensibleResource):
    status: str
    gx_compile: str | None = None
    native_save: str | None = None
    native_source_save_required: StrictBool | None = None
    position_resolution: str | None = None
    issues: list[str | SourceIssue] | None = None
    messages: list[str] | None = None


class SFCPreviewResource(PublicResource):
    target_mode: Literal['sfc']
    program: SFCModel
    validation: SFCPreviewValidation
    diff: SFCDiff
    gx_compile: str | None = None


class ExecutionPreview(PublicResource):
    action: str
    version_id: str
    version: Version
    target_mode: str
    program: ProgramDocument | None
    svg: str | None
    st: str | None
    plan: JsonObject | None = None


ProposalPreview = LadderPreview | STPreview | FBDPreviewResource | SFCPreviewResource | ExecutionPreview
# Existing Python consumers keep their imports; schema names distinguish these
# resources from the request models with the same historical short names.
FBDPreview = FBDPreviewResource
SFCPreview = SFCPreviewResource


class GenerationLadderPreview(LadderPreview):
    diff: LadderProgramDiff | None = None
    job_id: str
    read_only: bool
    proposal_id: str | None = None
    status: str | None = None
    contract_mismatch: JsonObject | None = None
    validation: JsonObject | None = None


class GenerationFBDPreview(FBDPreviewResource):
    job_id: str
    read_only: bool
    proposal_id: str | None = None


class GenerationSFCPreview(SFCPreviewResource):
    job_id: str
    read_only: bool
    proposal_id: str | None = None


class GenerationSTPreview(STPreview):
    job_id: str
    read_only: bool
    proposal_id: str | None = None


GenerationPreview = GenerationLadderPreview | GenerationFBDPreview | GenerationSFCPreview | GenerationSTPreview


class ModelProfile(PublicResource):
    id: str
    name: str | None = None
    model: str | None = None
    base_url: str | None = None
    configured: bool
    deletable: bool = False
    contract: JsonObject = Field(default_factory=dict)
    user_settings: JsonObject = Field(default_factory=dict)
    capability_overrides: JsonObject = Field(default_factory=dict)
    preferences: JsonObject = Field(default_factory=dict)
    checks: JsonObject = Field(default_factory=dict)


class ModelConnectionResult(PublicResource):
    status: Literal["connected", "failed", "resolved", "unverified"]
    message: str
    error_code: str | None = None


class ModelDiscoveryResult(ModelConnectionResult):
    discovery: JsonObject | None = None


class ApprovalSettings(PublicResource):
    mode: Literal["ask", "auto", "full"]
    revision: int = Field(ge=0)
    local_autosave: bool
    read_only: bool


class ModelSettings(PublicResource):
    language: str
    active_profile_id: str | None = None
    profiles: list[ModelProfile] = Field(default_factory=list)


class Session(PublicResource):
    authenticated: bool
    csrf: str | None = None
    read_only: bool | None = None


class Health(PublicResource):
    status: str
    application: str


class Environment(PublicResource):
    status: str
    passed: bool
    desktop_execution_required: bool
    gateway_started: bool
    evidence: JsonObject


class SpecResult(PublicResource):
    valid: bool
    spec: Specification | None = None
    hash: str | None = None
    issues: JsonObject | None = None


class AttachmentUploadResult(PublicResource):
    attachment_id: str
    filename: str
    media_type: str
    size_bytes: int = Field(ge=0)


class SFCRequirement(PublicResource):
    text: str
    capability: Literal["requirement_input"]


class AgentTools(PublicResource):
    tools: list[JsonObject]


class NativeToolError(ExtensibleResource):
    code: str
    message: str
    next_actions: list[JsonObject] | None = None


class NativeCheckEnvelope(ExtensibleResource):
    ok: bool
    tool: Literal["validate_project"]
    data: NativeCheck | None = None
    error: NativeToolError | None = None


class DeviceSummary(ExtensibleResource):
    count: int
    by_type: dict[str, int]
    addresses: list[str]
    truncated: bool | None = None


class NetworkSummary(ExtensibleResource):
    id: str
    comment: str | None = None
    instruction_count: int
    reads: list[str] | None = None
    writes: list[str] | None = None


class ProgramInfoToolData(ExtensibleResource):
    available: bool | None = None
    version_id: str | None = None
    target_mode: str | None = None
    program_name: str | None = None
    plc_model: str | None = None
    devices: DeviceSummary | None = None
    timing: ProgramTiming | None = None
    network_count: int | None = None
    networks_truncated: bool | None = None
    networks: list[NetworkSummary] | None = None
    metadata: EngineeringObject | None = None
    declaration_edits: EngineeringMap[DeclarationEdit] | None = None


class SourceSnapshotToolData(ExtensibleResource):
    available: bool | None = None
    version_id: str | None = None
    model: FBDModel | SFCModel | None = None
    programs: list[str] | None = None
    gx_compile: str | None = None
    definitions: list[SourceDefinition] | None = None
    definition: SourceDefinition | None = None


class ProgramInfoEnvelope(ExtensibleResource):
    ok: bool
    tool: Literal["get_current_program_info"]
    data: ProgramInfoToolData | None = None
    error: NativeToolError | None = None


class ReadNetworkSnapshot(ExtensibleResource):
    id: str
    comment: str | None = None
    instructions: list[Instruction] | None = None
    raw_instructions: list[JsonObject] | None = None


class ReadNetworkData(ExtensibleResource):
    network: ReadNetworkSnapshot
    raw_instructions: list[JsonObject] | None = None
    coverage: JsonObject | str | None = None


class NetworkEnvelope(ExtensibleResource):
    ok: bool
    tool: Literal["read_network"]
    data: ReadNetworkData | None = None
    error: NativeToolError | None = None


class DiagnosticsEnvelope(ExtensibleResource):
    ok: bool
    tool: Literal["get_diagnostics"]
    data: Diagnostics | None = None
    error: NativeToolError | None = None


class SourceSnapshotEnvelope(ExtensibleResource):
    ok: bool
    tool: Literal["read_fbd_project", "read_sfc_project", "read_fbd_definition",
                  "create_fbd_candidate", "create_sfc_candidate"]
    data: SourceSnapshotToolData | None = None
    error: NativeToolError | None = None


class ReadContinuation(PublicResource):
    tool: Literal['search_plc_manual', 'get_generation_context', 'get_fbd_catalog']
    arguments: JsonObject


class ReadDelivery(PublicResource):
    version: Literal[1]
    document_id: str
    page: int
    complete: bool
    scope: Literal['read_sequence']
    limits: dict[str, int]
    next: ReadContinuation | None
    assembly: str
    coverage: JsonObject | None = None


class PagedReadData(PublicResource):
    context_schema_version: Literal[3]
    document: EngineeringObject
    objects: EngineeringMap[EngineeringValue]
    delivery: ReadDelivery


class ManualSearchEnvelope(ExtensibleResource):
    ok: bool
    tool: Literal['search_plc_manual']
    data: PagedReadData | None = None
    error: NativeToolError | None = None


class CatalogEnvelope(ExtensibleResource):
    ok: bool
    tool: Literal["get_fbd_catalog"]
    data: PagedReadData | FBDCatalog | None = None
    error: NativeToolError | None = None


class GenerationCatalog(FBDCatalog):
    empty_model: FBDModel | FBDParameterModel


class FBDGenerationSTSource(ExtensibleResource):
    source_name: str
    st_text: str | None = None
    declarations: list[FBDLabel] | None = None


class FBDGenerationCaller(ExtensibleResource):
    source_name: str
    parameter_model: FBDParameterModel | None = None


class FBDGenerationSourceContext(ExtensibleResource):
    declarations: EngineeringMap[list[FBDLabel]] | None = None
    structures: EngineeringMap[list[FBDLabel]] | None = None
    st_sources: list[FBDGenerationSTSource] | None = None
    fbd_callers: list[FBDGenerationCaller] | None = None


class GenerationInput(ExtensibleResource):
    previous: FBDModel | FBDParameterModel | None = None
    parameter_schema: EngineeringObject | None = None
    declaration_tables: EngineeringMap[str] | None = None
    existing_declarations: EngineeringMap[list[FBDLabel]] | None = None
    call_interface: EngineeringMap[list[FBDLabel]] | None = None
    structures: EngineeringMap[list[FBDLabel]] | None = None
    source_context: FBDGenerationSourceContext | None = None


class GenerationOutputContract(ExtensibleResource):
    model_config = ConfigDict(serialize_by_alias=True)
    tool: str | None = None
    operation: str | None = None
    protocol: str | None = None
    schema_version: int | None = None
    input_shape: str | None = None
    schema_document: EngineeringObject | None = Field(default=None, alias="schema")
    model_schema: EngineeringObject | None = None
    model_schema_ref: str | None = None
    schema_ref: str | None = None
    version_id: str | None = None
    program: str | None = None
    editable_sources: list[str] | None = None
    required_fields: list[str] | None = None
    requires_any: list[str] | None = None
    save_policy: Literal['draft', 'checked'] | None = None
    alternatives: list[GenerationOutputContract] | None = None


class GenerationContextToolData(ExtensibleResource):
    project_id: str | None = None
    target_mode: str | None = None
    plc_model: str | None = None
    confirmed_spec: Specification | None = None
    user_facts: Specification | None = None
    current_model: FBDModel | FBDParameterModel | SFCModel | None = None
    catalog: GenerationCatalog | None = None
    generation_input: GenerationInput | None = None
    output_contract: GenerationOutputContract | None = None


class GenerationContextEnvelope(ExtensibleResource):
    ok: bool
    tool: Literal["get_generation_context"]
    data: PagedReadData | GenerationContextToolData | None = None
    error: NativeToolError | None = None


class MetadataToolEnvelope(ExtensibleResource):
    ok: bool
    tool: str
    data: JsonObject | None = None
    error: NativeToolError | None = None


ToolEnvelope = (ProgramInfoEnvelope | NetworkEnvelope | DiagnosticsEnvelope | SourceSnapshotEnvelope
                | CatalogEnvelope | GenerationContextEnvelope | ManualSearchEnvelope | NativeCheckEnvelope | MetadataToolEnvelope)


class AgentToolResult(PublicResource):
    version_id: str | None = None
    execution_job_id: str | None = None
    data: ToolEnvelope
    content: str
    is_error: bool
    call_id: str
    name: str
    proposal_id: str | None = None

    @model_validator(mode="after")
    def tool_name_matches_owner(self):
        if self.name != self.data.tool:
            raise ValueError("Tool result owner does not match its registered name")
        return self


class NativeValidationToolResult(AgentToolResult):
    name: Literal["validate_project"]
    data: NativeCheckEnvelope


class ErrorDetail(PublicResource):
    code: str
    message: str


class ErrorResponse(PublicResource):
    error: ErrorDetail


def document_sse_events(schema):
    """Register the SSE data contract without advertising the stream as JSON."""
    event_schema = JobEvent.model_json_schema(ref_template="#/components/schemas/{model}")
    definitions = event_schema.pop("$defs", {})
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for name, definition in definitions.items():
        components.setdefault(name, definition)
    components["JobEvent"] = event_schema
    operation = schema.get("paths", {}).get("/api/jobs/{job_id}/events", {}).get("get")
    if operation:
        response = operation.setdefault("responses", {}).setdefault("200", {"description": "Monotonic job events"})
        response["content"] = {"text/event-stream": {"schema": {"type": "string"}}}
        response["x-event-schema"] = {"$ref": "#/components/schemas/JobEvent"}
    return schema
