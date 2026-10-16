"""Stable public API for importing generated programs into GX Works2."""

from .api import (
    configure,
    import_current_program,
    inspect_current_sync,
    read_current_snapshot,
    record_sync_snapshot,
)
from .diagnostics import describe_exception
from .models import (
    GXSyncErrorCode,
    ImportErrorCode,
    ImportResult,
    SyncResult,
    SyncStatus,
)
from .simulation import GXSimulator2PreparationService, SimulatorPreparationResult

__all__ = [
    "ImportErrorCode",
    "GXSyncErrorCode",
    "ImportResult",
    "SyncResult",
    "SyncStatus",
    "GXSimulator2PreparationService",
    "SimulatorPreparationResult",
    "configure",
    "import_current_program",
    "inspect_current_sync",
    "read_current_snapshot",
    "record_sync_snapshot",
    "describe_exception",
]
