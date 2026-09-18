from pathlib import Path


def test_simple_gx_bridge_actions_are_primary_ui():
    text = Path('src/ui/desktop/main_window.py').read_text(encoding="utf-8")
    assert "QPushButton(tr('写入 GX Works2'))" in text
    assert "QPushButton(tr('读取 GX Works2'))" in text
    assert "QPushButton(tr('高级同步'))" in text
    assert "def _publish_current_version_to_gxworks2" in text
    assert "def _pull_current_version_from_gxworks2" in text
    assert 'self._start_gxworks2_inspection("pull")' in text
    assert 'self._start_gxworks2_inspection("reconcile")' in text


def test_empty_project_pull_has_bootstrap_request():
    text = Path('src/ui/desktop/main_window.py').read_text(encoding="utf-8")
    assert "def _gx_pull_request(self):" in text
    assert '"bootstrap": True' in text
    assert 'snapshot_only=bool(request.get("bootstrap"))' in text
    assert 'if intent == "pull" and request.get("bootstrap"):' in text
    assert "self._start_gxworks2_pull(result, request)" in text


def test_pull_button_only_requires_selected_project():
    text = Path('src/ui/desktop/main_window.py').read_text(encoding="utf-8")
    state = text.split("def _update_gx_sync_button_enabled", 1)[1].split("    def ", 1)[0]
    assert "project_ready = bool(self.current_project_id)" in state
    assert "ladder_ready = bool(version" in state
    assert "self.gxworks2_pull_button.setEnabled(project_ready and available)" in state
    assert "self.gxworks2_import_button.setEnabled(ladder_ready and available)" in state
    assert "self.gxworks2_advanced_button.setEnabled(ladder_ready and available)" in state


def test_bootstrap_pull_creates_root_version():
    text = Path('src/ui/desktop/main_window.py').read_text(encoding="utf-8")
    completed = text.split("def _gxworks2_pull_completed", 1)[1].split("    def ", 1)[0]
    assert "tr('从GX Works2导入的初始程序')" in completed
    assert '"gxworks2_bootstrap"' in completed
    assert "None if bootstrap" in completed


def test_direct_pull_does_not_force_three_way_choice():
    text = Path('src/ui/desktop/main_window.py').read_text(encoding="utf-8")
    assert 'if intent == "pull":' in text
    assert "self._start_gxworks2_pull(result, request)" in text


def test_external_modification_points_to_advanced_sync():
    text = Path("src/gxworks2/import_service.py").read_text(encoding="utf-8")
    assert '请使用“高级同步”选择保留哪一方' in text


def test_snapshot_only_thread_uses_public_read_api():
    text = Path('src/ui/desktop/workers.py').read_text(encoding="utf-8")
    thread = text.split("class GXWorks2SyncInspectThread", 1)[1].split(
        "class GXWorks2PullThread", 1
    )[0]
    assert "snapshot_only=False" in thread
    assert "from gxworks2 import read_current_snapshot" in thread
    assert "result = read_current_snapshot(" in thread
