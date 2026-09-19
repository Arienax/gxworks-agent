"""Public HTTP resource contracts, independent of desktop and model runtimes.

Transport models validate an already-public application projection. Unknown
resource fields fail explicitly instead of Pydantic silently dropping them.
Engineering documents and extension maps retain their complete JSON content.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, model_validator


JsonObject = dict[str, JsonValue]
_PRIVATE_FIELDS = frozenset({
    "apikey", "api_key", "access_token", "refresh_token", "authorization",
    "credential", "credentials", "credentialtarget", "credential_target",
    "password", "secret", "token", "accesstoken", "refreshtoken",
    "operator_token", "agent_token", "gateway_token",
    "private_payload", "provider_configuration", "provider_instance",
})


def _require_public(value):
    """Reject private keys without removing legitimate engineering metadata.

    In particular, json_path, source locations, validation details and new
    capability keys remain intact. Absolute-path redaction belongs to the
    application projection, which can distinguish paths from source text.
    """
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key = str(key)
            if key.startswith("_") or key.casefold() in _PRIVATE_FIELDS:
                # Do not include a rejected key/value in the diagnostic message.
                raise ValueError("Private data cannot be returned as an HTTP resource")
            _require_public(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _require_public(nested)
    return value


class PublicResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def require_public(cls, value):
        return _require_public(value)


class ExtensibleResource(PublicResource):
    """Typed common fields plus lossless public engineering extensions."""
    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)


class PublicObject(RootModel[JsonObject]):
    @model_validator(mode="before")
    @classmethod
    def require_public(cls, value):
        return _require_public(value)


class NullablePublicObject(RootModel[JsonObject | None]):
    @model_validator(mode="before")
    @classmethod
    def require_public(cls, value):
        return _require_public(value)


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


class Capabilities(ExtensibleResource):
    creation: ProjectCreationOptions | None = None
    body_form: str
    operations: dict[str, bool]
    project_type: str | None = None
    representations: list[str] = Field(default_factory=list)
    sfc: str | None = None
    structured_semantics: str | None = None


class Version(PublicResource):
    id: str
    created_at: str | None = None
    target_mode: str | None = None
    plc_model: str | None = None
    summary: str | None = None
    validation: Validation | None = None
    lifecycle_status: str | None = None
    parent_version_id: str | None = None
    ir_sha256: str | None = None
    confirmed_spec_hash: str | None = None
    confirmed_spec_snapshot: JsonObject | None = None
    gx_sync: JsonObject | None = None
    simulator_runs: list[JsonObject] | None = None
    simulator_test_plans: list[JsonObject] | None = None
    debug_attempts: list[JsonObject] | None = None
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
    # Capability declarations, rather than this representation name, control
    # available operations. Future body forms need not become a second IR.
    target_mode: str
    version_count: int = Field(ge=0)
    created_at: str | None = None
    updated_at: str | None = None
    effort: str | None = None
    active_version_id: str | None = None
    confirmed_spec: JsonObject | None = None
    confirmed_spec_hash: str | None = None
    versions: list[Version] = Field(default_factory=list)
    messages: list[ProjectMessage] = Field(default_factory=list)
    reports: list[ReportSummary] = Field(default_factory=list)


class ProjectList(PublicResource):
    projects: list[Project] = Field(default_factory=list)


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
    stage: Literal["generation_validation"] | None = None
    attempt_count: int | None = Field(default=None, ge=0, le=3)
    max_attempts: int | None = Field(default=None, ge=0, le=3)
    stop_reason: Literal["attempt_limit", "time_budget", "final_validation"] | None = None


class Job(PublicResource):
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


class JobList(PublicResource):
    jobs: list[Job] = Field(default_factory=list)


class JobEvent(PublicResource):
    job_id: str
    project_id: str | None = None
    version_id: str | None = None
    sequence: int = Field(ge=1)
    event_type: str
    payload: JsonObject = Field(default_factory=dict)
    created_at: str


class JobOutput(ExtensibleResource):
    version_id: str | None = None
    analysis: JsonObject | None = None
    spec_draft: JsonObject | None = None
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
    before: JsonObject | None
    after: JsonObject | None


class DeviceCommentChange(PublicResource):
    address: str
    before: str | None
    after: str | None


class PropertyChange(PublicResource):
    before: JsonValue
    after: JsonValue


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
    property_changes: dict[str, PropertyChange]


class STTextDiff(PublicResource):
    kind: Literal["st"]
    base_version_id: str | None = None
    has_changes: bool
    before: str
    after: str
    unified_diff: str


class LadderPreview(PublicResource):
    target_mode: Literal["ladder"]
    ladder: JsonObject
    program: JsonObject
    svg: str
    st: str
    diff: LadderProgramDiff


class STPreview(PublicResource):
    target_mode: Literal["st"]
    st: str
    diff: STTextDiff


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


class FBDPreview(PublicResource):
    target_mode: Literal["fbd"]
    program: JsonObject
    svg: str
    diff: FBDDiff


class ExecutionPreview(PublicResource):
    action: str
    version_id: str
    version: Version
    target_mode: str
    program: JsonObject | None
    svg: str | None
    st: str | None
    plan: JsonObject | None = None


ProposalPreview = LadderPreview | STPreview | FBDPreview | ExecutionPreview


class ModelProfile(PublicResource):
    id: str
    name: str | None = None
    model: str | None = None
    base_url: str | None = None
    configured: bool
    capabilities: dict[str, bool] = Field(default_factory=dict)
    deletable: bool = False
    generation_defaults: JsonObject = Field(default_factory=dict)
    request_overrides: JsonObject = Field(default_factory=dict)
    parameter_support: JsonObject = Field(default_factory=dict)
    contract: JsonObject = Field(default_factory=dict)
    user_settings: JsonObject = Field(default_factory=dict)
    capability_overrides: JsonObject = Field(default_factory=dict)


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
    spec: JsonObject | None = None
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


class AgentToolResult(PublicResource):
    version_id: str | None = None
    execution_job_id: str | None = None
    data: JsonObject
    content: str
    is_error: bool
    call_id: str
    name: str
    proposal_id: str | None = None


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
