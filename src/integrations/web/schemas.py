"""Explicit HTTP commands; candidates and credentials never occur in read DTOs."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Command):
    token: str = Field(min_length=1, max_length=1024)


class ProjectCreate(Command):
    name: str = Field(min_length=1, max_length=160)
    plc_model: str = Field(default="FX3U", max_length=96)
    target_mode: Literal["ladder", "st", "fbd"] = "ladder"
    native_project_type: Literal["simple", "structured", "parameter"] | None = None
    use_labels: bool | None = None


class ProjectUpdate(Command):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    plc_model: str | None = Field(default=None, max_length=96)
    target_mode: Literal["ladder", "st", "fbd"] | None = None
    native_project_type: Literal["simple", "structured", "parameter"] | None = None
    use_labels: bool | None = None
    effort: Literal["low", "medium", "high"] | None = None  # deprecated, ignored


class ActivateVersion(Command):
    version_id: str
    expected_active_version_id: str | None


class SpecUpdate(Command):
    spec: dict[str, Any]
    expected_hash: str | None


class ChangeScope(Command):
    network_ids: list[str] | None = Field(default=None, min_length=1, max_length=4096)
    addresses: list[str] | None = Field(default=None, min_length=1, max_length=4096)


class JobCreate(Command):
    kind: Literal["analysis", "generation", "direct_generation", "agent", "review", "test_plan", "debug_plan", "gx_read", "gx_inspect"]
    project_id: str
    version_id: str | None = None
    clarification_job_id: str | None = None
    clarification_answers: dict[str, str] | None = None
    request_id: str = Field(min_length=1, max_length=128)
    text: str = Field(default="", max_length=64000)
    response_language: Literal["zh-CN", "en", "ja"] = "zh-CN"
    analysis_mode: Literal["direct", "design"] = "direct"
    generation_action: Literal["edit", "regenerate"] | None = None
    attachment_ids: list[str] = Field(default_factory=list, max_length=12)
    run_id: str | None = None
    deep: bool = True
    change_scope: ChangeScope | None = None


class GenerationRepair(Command):
    request_id: str = Field(min_length=1, max_length=128)


class ProposalDecision(Command):
    decision: Literal["accept", "reject"]


class ExecutionProposal(Command):
    action: Literal["gx_import", "simulation", "debug", "native_engineering"]
    project_id: str
    version_id: str
    plan_id: str | None = None
    plan: dict[str, Any] | None = None
    request_id: str = Field(min_length=1, max_length=128)
    manual_backup_acknowledged: bool = Field(default=False, strict=True, deprecated=True,
        description="Legacy CSV-send acknowledgement. All CSV imports now back up the existing GXW file automatically; this field does not change backup or execution approval.")


class AgentCall(Command):
    project_id: str
    version_id: str | None = None
    name: str = Field(max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str = Field(min_length=1, max_length=128)
    change_scope: ChangeScope | None = None


class ModelProfileUpdate(Command):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    name: str | None = Field(default=None, min_length=1, max_length=256)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    contract: dict[str, Any] | None = None
    user_settings: dict[str, Any] | None = None
    capability_overrides: dict[str, Any] | None = None


class ModelProfileCreate(Command):
    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    name: str = Field(min_length=1, max_length=256)
    model: str = Field(min_length=1, max_length=256)
    base_url: str = Field(min_length=1, max_length=2048)
    contract: dict[str, Any] = Field(default_factory=dict)
    user_settings: dict[str, Any] = Field(default_factory=dict)
    capability_overrides: dict[str, Any] = Field(default_factory=dict)
    api_key: str | None = Field(default=None, min_length=1, max_length=8192)


class ModelDiscoveryRequest(Command):
    profile: ModelProfileCreate
    mode: Literal["list", "quick", "resolve", "deep"] = "resolve"
    refresh: bool = Field(default=False, strict=True)


class ModelVerificationRequest(Command):
    profile: ModelProfileCreate
    target: str = Field(min_length=1, max_length=64)
    kind: Literal["parameter", "capability", "chat"]
    value: Any = None
    consent: bool = Field(default=False, strict=True)


class ModelRequestPreview(Command):
    profile: ModelProfileCreate
    scenario: Literal["text", "tools", "structured"]


class ModelKeyUpdate(Command):
    api_key: str = Field(min_length=1, max_length=8192)


class ModelConnectionTest(Command):
    profile: ModelProfileUpdate | None = None
    api_key: str | None = Field(default=None, min_length=1, max_length=8192)


class ApprovalSettingsUpdate(Command):
    mode: Literal["ask", "auto", "full"]
    expected_revision: int = Field(ge=0)
    confirm_full_access: bool = False


class SettingsUpdate(Command):
    language: Literal["zh-CN", "en", "ja"] | None = None
    active_profile_id: str | None = None
    profile: ModelProfileUpdate | None = None
    api_key: str | None = Field(default=None, min_length=1, max_length=8192)


class AttachmentUpload(Command):
    filename: str = Field(min_length=1, max_length=255)
    data_base64: str = Field(max_length=42 * 1024 * 1024)


class FBDProposal(Command):
    operation: Literal["generate", "edit", "import", "convert", "create_fb", "create_program", "import_library", "create_structure", "create_st_fb", "edit_st_fb", "edit_global_labels", "create_global_labels", "edit_pou_labels", "configure_project", "remove_empty_program", "compile"]
    native_save: bool | None = Field(default=None, strict=True)
    options: dict[str, Any] | None = None
    project_id: str
    version_id: str | None = None
    request_id: str = Field(min_length=1, max_length=128)
    model: dict[str, Any] | None = None
    data_base64: str | None = Field(default=None, max_length=42 * 1024 * 1024)
    program: str | None = Field(default=None, max_length=255)
    source_name: str | None = Field(default=None, min_length=1, max_length=255)
    library_name: str | None = Field(default=None, min_length=1, max_length=255)
    st_text: str | None = Field(default=None, max_length=4 * 1024 * 1024)
    members: list[dict[str, str]] | None = Field(default=None, max_length=8192)
    declarations: list[dict[str, Any]] | None = Field(default=None, max_length=8192)
    resource: str | None = Field(default=None, min_length=1, max_length=255)
    task: str | None = Field(default=None, min_length=1, max_length=255)
    scope_mode: Literal['existing_task', 'new_task', 'new_resource'] | None = None


class SFCChildEdit(Command):
    kind: Literal['zoom', 'transition']
    name: str = Field(min_length=1, max_length=255)
    records_hex: str = Field(max_length=16 * 1024 * 1024)


class SFCGraphEdit(Command):
    step_reference: int = Field(strict=True, ge=0, le=65535)
    action_number: int = Field(strict=True, ge=0, le=65535)


class SFCJumpEdit(Command):
    kind: Literal['jump']
    x: int = Field(strict=True, ge=0, le=65535)
    y: int = Field(strict=True, ge=0, le=65535)
    before_step_reference: int = Field(strict=True, ge=0, le=65535)
    target_step_reference: int = Field(strict=True, ge=0, le=65535)


class SFCNodeSymbol(Command):
    kind: Literal['initial_step', 'step', 'transition']
    x: int = Field(strict=True, ge=0, le=32767)
    y: int = Field(strict=True, ge=0, le=32767)
    number: int = Field(strict=True, ge=0, le=32767)


class SFCEndSymbol(Command):
    kind: Literal['end']
    x: int = Field(strict=True, ge=0, le=32767)
    y: int = Field(strict=True, ge=0, le=32767)


class SFCLineSymbol(Command):
    kind: Literal['line']
    x: int = Field(strict=True, ge=0, le=32767)
    start: int = Field(strict=True, ge=0, le=32767)
    end: int = Field(strict=True, ge=0, le=32767)


class SFCBranchSymbol(Command):
    kind: Literal['parallel_branch', 'parallel_coupling', 'selective_branch', 'selective_coupling']
    start: int = Field(strict=True, ge=0, le=32767)
    end: int = Field(strict=True, ge=0, le=32767)
    y: int = Field(strict=True, ge=0, le=32767)


class SFCRebuildEdit(Command):
    kind: Literal['rebuild']
    symbols: list[SFCNodeSymbol | SFCEndSymbol | SFCLineSymbol | SFCBranchSymbol] = Field(min_length=1, max_length=8192)


class SFCStepSwap(Command):
    kind: Literal['swap_steps']
    left_step_reference: int = Field(strict=True, ge=0, le=32767)
    right_step_reference: int = Field(strict=True, ge=0, le=32767)


class SFCProposal(Command):
    operation: Literal['import', 'edit']
    project_id: str
    version_id: str | None = None
    request_id: str = Field(min_length=1, max_length=128)
    data_base64: str | None = Field(default=None, max_length=42 * 1024 * 1024)
    program: str | None = Field(default=None, min_length=1, max_length=255)
    child_edits: list[SFCChildEdit] | None = Field(default=None, min_length=1, max_length=8192)
    graph_edits: list[SFCGraphEdit | SFCJumpEdit | SFCRebuildEdit | SFCStepSwap] | None = Field(default=None, min_length=1, max_length=8192)


class SFCPreview(Command):
    project_id: str
    version_id: str
    program: str | None = Field(default=None, min_length=1, max_length=255)
    child_edits: list[SFCChildEdit] | None = Field(default=None, min_length=1, max_length=8192)
    graph_edits: list[SFCGraphEdit | SFCJumpEdit | SFCRebuildEdit | SFCStepSwap] | None = Field(default=None, min_length=1, max_length=8192)


class SFCStep(Command):
    name: str = Field(min_length=1, max_length=100)
    action: str = Field(max_length=1000)
    transition: str = Field(default="", max_length=1000)


class SFCInput(Command):
    steps: list[SFCStep] = Field(min_length=1, max_length=100)


class MCPIntegrationCommand(Command):
    project_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
