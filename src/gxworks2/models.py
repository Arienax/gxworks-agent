from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class ImportErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    CSV_NOT_FOUND = "csv_not_found"
    CSV_INVALID = "csv_invalid"
    GXWORKS2_NOT_FOUND = "gxworks2_not_found"
    GXWORKS2_NOT_RUNNING = "gxworks2_not_running"
    TARGET_PROJECT_NOT_OPEN = "target_project_not_open"
    TARGET_PROGRAM_NOT_READY = "target_program_not_ready"
    BACKUP_FAILED = "backup_failed"
    EXTERNAL_MODIFICATION_DETECTED = "external_modification_detected"
    BASELINE_READ_FAILED = "baseline_read_failed"
    BASELINE_WRITE_FAILED = "baseline_write_failed"
    PROJECT_SAVE_REQUIRED = "project_save_required"
    IMPORT_DIALOG_NOT_FOUND = "import_dialog_not_found"
    IMPORT_REJECTED = "import_rejected"
    IMPORT_VERIFICATION_FAILED = "import_verification_failed"
    AUTOMATION_UNAVAILABLE = "automation_unavailable"
    AUTOMATION_FAILED = "automation_failed"


class GXSyncErrorCode(str, Enum):
    """Stable, machine-readable failures for the read-side GX sync flow."""

    GX_LOCAL_CSV_INVALID = "gx_local_csv_invalid"
    GX_WORKS2_NOT_RUNNING = "gx_works2_not_running"
    GX_PROJECT_NOT_OPEN = "gx_project_not_open"
    GX_PROGRAM_NOT_READY = "gx_program_not_ready"
    GX_AUTOMATION_UNAVAILABLE = "gx_automation_unavailable"
    GX_PROJECT_INSPECT_FAILED = "gx_project_inspect_failed"
    GX_MAIN_ACTIVATE_FAILED = "gx_main_activate_failed"
    GX_EXPORT_MENU_FAILED = "gx_export_menu_failed"
    GX_FILE_DIALOG_TIMEOUT = "gx_file_dialog_timeout"
    GX_UIA_TIMEOUT = "gx_uia_timeout"
    GX_UIA_ACCESS_DENIED = "gx_uia_access_denied"
    GX_PROGRAM_EXPORT_FAILED = "gx_program_export_failed"
    GX_PROGRAM_EXPORT_INVALID = "gx_program_export_invalid"
    GX_COMMENT_EXPORT_FAILED = "gx_comment_export_failed"
    GX_COMMENT_EXPORT_INVALID = "gx_comment_export_invalid"
    GX_EXPORT_MANIFEST_FAILED = "gx_export_manifest_failed"
    GX_BASELINE_READ_FAILED = "gx_baseline_read_failed"
    GX_BASELINE_WRITE_FAILED = "gx_baseline_write_failed"
    GX_UNEXPECTED_ERROR = "gx_unexpected_error"


class SyncStatus(str, Enum):
    UNKNOWN = "unknown"
    SYNCED = "synced"
    NEEDS_PUSH = "needs_push"
    NEEDS_PULL = "needs_pull"
    CONFLICT = "conflict"
    UNBOUND = "unbound"
    ERROR = "error"


@dataclass(frozen=True)
class CSVValidationResult:
    valid: bool
    path: str
    encoding: str = ""
    row_count: int = 0
    instruction_count: int = 0
    program_name: str = ""
    plc_info: str = ""
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class CommentCSVValidationResult:
    valid: bool
    path: str
    encoding: str = ""
    row_count: int = 0
    comment_count: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class GXWorks2Session:
    process_id: int
    window_handle: int
    title: str
    executable: str = ""
    project_open: bool = False
    project_name: str = ""
    project_state_known: bool = False
    process_started: str = ""
    software_version: str = ""


@dataclass(frozen=True)
class ImportResult:
    success: bool
    stage: str
    message: str
    error_code: Optional[ImportErrorCode] = None
    csv_path: str = ""
    backup_path: str = ""
    project_name: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        payload = asdict(self)
        if self.error_code is not None:
            payload["error_code"] = self.error_code.value
        return payload


@dataclass(frozen=True)
class SyncResult:
    success: bool
    status: SyncStatus
    message: str
    error_code: Optional[Union[ImportErrorCode, GXSyncErrorCode]] = None
    project_name: str = ""
    exported_program_path: str = ""
    exported_comment_path: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    stage: str = ""
    retryable: bool = False

    def to_dict(self):
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["ok"] = self.success
        if self.error_code is not None:
            payload["error_code"] = self.error_code.value
        return payload
