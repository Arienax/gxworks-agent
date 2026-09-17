"""Explicit HTTP commands; candidates and credentials never occur in read DTOs."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Command):
    token: str = Field(min_length=1, max_length=1024)


class ProjectCreate(Command):
    name: str = Field(min_length=1, max_length=160)
    plc_model: str = Field(default="FX3U", max_length=32)
    target_mode: Literal["ladder", "st", "fbd"] = "ladder"


class ProjectUpdate(Command):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    plc_model: str | None = Field(default=None, max_length=32)
    target_mode: Literal["ladder", "st", "fbd"] | None = None
    effort: Literal["low", "medium", "high"] | None = None


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
    kind: Literal["analysis", "generation", "agent", "review", "test_plan", "debug_plan", "gx_read", "gx_inspect"]
    project_id: str
    version_id: str | None = None
    request_id: str = Field(min_length=1, max_length=128)
    text: str = Field(default="", max_length=64000)
    response_language: Literal["zh-CN", "en", "ja"] = "zh-CN"
    attachment_ids: list[str] = Field(default_factory=list, max_length=12)
    run_id: str | None = None
    deep: bool = True
    change_scope: ChangeScope | None = None


class GenerationRepair(Command):
    request_id: str = Field(min_length=1, max_length=128)


class ProposalDecision(Command):
    decision: Literal["accept", "reject"]


class ExecutionProposal(Command):
    action: Literal["gx_import", "simulation", "debug"]
    project_id: str
    version_id: str
    plan_id: str | None = None
    request_id: str = Field(min_length=1, max_length=128)
    manual_backup_acknowledged: bool = Field(default=False, strict=True,
        description="Operator acknowledged manual backup for an ordinary CSV GX send; does not grant execution approval.")


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
    capabilities: dict[str, bool] | None = None
    generation_defaults: dict[str, Any] | None = None
    request_overrides: dict[str, Any] | None = None
    parameter_support: dict[str, Any] | None = None
    contract: dict[str, Any] | None = None
    user_settings: dict[str, Any] | None = None


class ModelProfileCreate(Command):
    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    name: str = Field(min_length=1, max_length=256)
    model: str = Field(min_length=1, max_length=256)
    base_url: str = Field(min_length=1, max_length=2048)
    capabilities: dict[str, bool] = Field(default_factory=dict)
    generation_defaults: dict[str, Any] = Field(default_factory=dict)
    request_overrides: dict[str, Any] = Field(default_factory=dict)
    parameter_support: dict[str, Any] = Field(default_factory=dict)
    contract: dict[str, Any] = Field(default_factory=dict)
    user_settings: dict[str, Any] = Field(default_factory=dict)
    api_key: str | None = Field(default=None, min_length=1, max_length=8192)


class ModelDiscoveryRequest(Command):
    profile: ModelProfileCreate


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
    operation: Literal["generate", "edit", "import", "convert"]
    project_id: str
    version_id: str | None = None
    request_id: str = Field(min_length=1, max_length=128)
    model: dict[str, Any] | None = None
    data_base64: str | None = Field(default=None, max_length=42 * 1024 * 1024)
    program: str | None = Field(default=None, max_length=255)


class SFCStep(Command):
    name: str = Field(min_length=1, max_length=100)
    action: str = Field(max_length=1000)
    transition: str = Field(default="", max_length=1000)


class SFCInput(Command):
    steps: list[SFCStep] = Field(min_length=1, max_length=100)


class MCPIntegrationCommand(Command):
    project_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
