"""Desktop lifecycle; importing this module never creates a window or workspace."""
import sys


def run(argv=None):
    args = list(sys.argv if argv is None else argv)
    if "--self-test-openai-sdk" in args:
        from model_runtime.provider import sdk_runtime_self_test
        return 0 if sdk_runtime_self_test() else 1

    from ui.desktop.qt import QApplication, QMessageBox, QTimer
    from shared.i18n import tr
    from storage.session import SessionStore
    from application.workspace import WorkspaceBusyError, WorkspaceWriterLock
    from ui.desktop.main_window import _IndustrialWorkbenchUI

    app = QApplication(args)
    app.setOrganizationName("PLC AI Studio")
    app.setApplicationName("PLC AI Workbench")
    workspace = SessionStore(create=False).base_dir
    writer = WorkspaceWriterLock(workspace)
    try:
        writer.acquire()
    except WorkspaceBusyError as error:
        QMessageBox.warning(None, tr('工作区正在使用'), str(error))
        return 1
    try:
        window = _IndustrialWorkbenchUI()
        window.show()
        QTimer.singleShot(0, lambda: window._ensure_api_configured(initial_setup=True))
        return app.exec()
    finally:
        writer.release()
