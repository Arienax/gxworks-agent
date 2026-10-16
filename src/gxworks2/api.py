from pathlib import Path

from .csv_manager import CSVManager
from .finder import GXWorks2Finder
from .import_service import ImportService
from .models import ImportErrorCode, ImportResult
from .sync_service import GXWorks2SyncService
from .ui_automation import (
    PywinautoGXWorks2UIAutomation,
    UnavailableGXWorks2UIAutomation,
)


_service = None
_sync_service = None
_current_csv_path = None


def _default_backup_root():
    return Path.home() / "Documents" / "PLC AI Studio" / "GX Works2 Backups"


def configure(*, current_csv_path=None, service=None):
    """Configure the application-owned current artifact and optional driver."""
    global _current_csv_path, _service, _sync_service
    if current_csv_path is not None:
        _current_csv_path = Path(current_csv_path).expanduser().resolve()
    if service is not None:
        _service = service
        _sync_service = None


def _get_service():
    global _service
    if _service is None:
        automation = (
            PywinautoGXWorks2UIAutomation()
            if PywinautoGXWorks2UIAutomation.available()
            else UnavailableGXWorks2UIAutomation()
        )
        _service = ImportService(
            finder=GXWorks2Finder(),
            automation=automation,
            csv_manager=CSVManager(),
            backup_root=_default_backup_root(),
        )
    return _service


def _get_sync_service():
    global _sync_service
    if _sync_service is None:
        import_service = _get_service()
        _sync_service = GXWorks2SyncService(
            finder=import_service.finder,
            automation=import_service.automation,
            csv_manager=import_service.csv_manager,
            backup_root=import_service.backup_root,
            baseline_store=import_service.baseline_store,
        )
    return _sync_service


def import_current_program(
    csv_path=None,
    *,
    comment_csv_path=None,
    start_if_needed=False,
    progress=None,
    import_context=None,
    project_identity=None,
    rollback_expected_current_sha256=None,
    expected_current_comment_sha256=None,
    synchronize_comments=False,
    verify_roundtrip=False,
    save_project=False,
    pre_import_policy="protected",
    create_project_path=None,
):
    """Validate and import a complete CSV, with pre-import protection by default.

    This is the only public import operation. UI click/key primitives remain
    internal to an injected automation driver.
    """
    selected = Path(csv_path).expanduser().resolve() if csv_path else _current_csv_path
    if selected is None:
        return ImportResult(
            False,
            "resolve_csv",
            "尚未配置当前生成版本的程序CSV。",
            ImportErrorCode.INVALID_REQUEST,
        )
    return _get_service().import_current_program(
        selected,
        comment_csv_path=(
            Path(comment_csv_path).expanduser().resolve()
            if comment_csv_path is not None
            else None
        ),
        start_if_needed=start_if_needed,
        progress=progress,
        import_context=import_context,
        project_identity=project_identity,
        rollback_expected_current_sha256=rollback_expected_current_sha256,
        expected_current_comment_sha256=expected_current_comment_sha256,
        synchronize_comments=synchronize_comments,
        verify_roundtrip=verify_roundtrip,
        save_project=save_project,
        pre_import_policy=pre_import_policy,
        create_project_path=create_project_path,
    )



def read_current_snapshot(
    *,
    progress=None,
    import_context=None,
    project_identity=None,
    save_project=True,
    target_session=None,
    identity_observer=None,
):
    """Read GX Works2 MAIN/comments without requiring a local application version."""
    return _get_sync_service().read_current_snapshot(
        progress=progress,
        import_context=import_context,
        project_identity=project_identity,
        save_project=save_project,
        target_session=target_session,
        identity_observer=identity_observer,
    )

def inspect_current_sync(
    program_csv_path,
    comment_csv_path,
    *,
    progress=None,
    import_context=None,
    project_identity=None,
    save_project=True,
    persist_baseline=True,
):
    return _get_sync_service().inspect(
        program_csv_path,
        comment_csv_path,
        progress=progress,
        import_context=import_context,
        project_identity=project_identity,
        save_project=save_project,
        persist_baseline=persist_baseline,
    )


def record_sync_snapshot(
    identity,
    *,
    app_program_path,
    app_comment_path,
    gx_program_path,
    gx_comment_path,
    import_context=None,
):
    return _get_sync_service().record_snapshot(
        identity,
        app_program_path=app_program_path,
        app_comment_path=app_comment_path,
        gx_program_path=gx_program_path,
        gx_comment_path=gx_comment_path,
        import_context=import_context,
    )
