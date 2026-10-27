"""Launcher checks never open a browser, create a project, or invoke GX."""

import json
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from integrations.web import __main__ as launcher


# A fresh Windows runner spent 13.2s starting its first PowerShell process and
# another exceeded 15s; subsequent preflights took <1s. This is a functional
# no-side-effects test, not a cold OS/runtime performance benchmark. Keep a
# finite deadline without letting first-launch initialization hide assertions.
POWERSHELL_PREFLIGHT_TIMEOUT = 45


def test_browser_opens_only_after_this_server_is_ready(monkeypatch):
    server = SimpleNamespace(started=False)
    stopped = threading.Event()
    opened = threading.Event()
    urls = []

    def open_browser(url, new):
        urls.append((url, new))
        opened.set()
        return True

    monkeypatch.setattr("webbrowser.open", open_browser)
    thread = threading.Thread(target=launcher._open_when_started, args=(server, "http://127.0.0.1:8765/#token=local", stopped))
    thread.start()
    try:
        assert not opened.wait(0.15)
        server.started = True
        assert opened.wait(2)
        assert urls == [("http://127.0.0.1:8765/#token=local", 2)]
    finally:
        stopped.set()
        thread.join(2)
    assert not thread.is_alive()


def test_stopped_or_failed_server_never_opens_login(monkeypatch):
    monkeypatch.setattr("webbrowser.open", lambda *args, **kwargs: pytest.fail("Unexpected browser launch"))
    stopped = threading.Event()
    stopped.set()
    launcher._open_when_started(SimpleNamespace(started=False), "http://127.0.0.1:8765/#token=local", stopped)


@pytest.mark.parametrize("started,expected", [(False, 1), (True, 0)])
def test_cli_exit_code_and_loopback_defaults(monkeypatch, tmp_path, started, expected):
    import integrations.web.app as web_app

    config_calls = []
    app_calls = []
    monkeypatch.setattr(web_app, "create_app", lambda workspace, **kwargs: app_calls.append((workspace, kwargs)) or object())
    monkeypatch.setattr("webbrowser.open", lambda *args, **kwargs: pytest.fail("Browser is opt-in"))
    monkeypatch.setenv("PLC_WEB_OPERATOR_TOKEN", "local-token")
    monkeypatch.delenv("PLC_WEB_AGENT_TOKEN", raising=False)

    class Server:
        def __init__(self, config):
            self.started = False

        def run(self):
            self.started = started

    def configure(app, **kwargs):
        config_calls.append(kwargs)

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(Config=configure, Server=Server))
    workspace = tmp_path / "uncreated workspace"
    assert launcher.main(["--workspace", str(workspace), "--read-only"]) == expected
    assert config_calls == [{"host": "127.0.0.1", "port": 8765, "workers": 1, "access_log": False}]
    assert app_calls[0][1]["read_only"] is True
    assert not workspace.exists()


def test_explicit_browser_login_encodes_token_fragment(monkeypatch, tmp_path):
    import integrations.web.app as web_app

    opened = threading.Event()
    urls = []
    monkeypatch.setenv("PLC_WEB_OPERATOR_TOKEN", "test+/& token")
    monkeypatch.setattr(web_app, "create_app", lambda *args, **kwargs: object())

    def open_browser(url, new):
        urls.append(url)
        opened.set()
        return True

    class Server:
        def __init__(self, config):
            self.started = False

        def run(self):
            self.started = True
            assert opened.wait(2)

    monkeypatch.setattr("webbrowser.open", open_browser)
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(Config=lambda *args, **kwargs: None, Server=Server))
    assert launcher.main(["--workspace", str(tmp_path), "--open-browser"]) == 0
    assert urls == ["http://127.0.0.1:8765/#token=test%2B%2F%26%20token"]


@pytest.fixture
def windows_release_launcher(tmp_path):
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell is required for the Windows double-click launcher")
    bundle = tmp_path / "bundle & launcher"
    (bundle / "scripts").mkdir(parents=True)
    (bundle / "web/dist").mkdir(parents=True)
    (bundle / "web/dist/index.html").write_text("static fixture", encoding="utf-8")
    # Validation must not attempt to execute this intentionally invalid binary.
    (bundle / "GXWorks-Agent-Web.exe").write_text("not executable", encoding="utf-8")
    source = Path(__file__).resolve().parents[1] / "scripts/start_web.ps1"
    assert source.read_bytes().startswith(b"\xef\xbb\xbf"), "PowerShell 5.1 needs a BOM for the Chinese prompts"
    target = bundle / "scripts/start_web.ps1"
    shutil.copyfile(source, target)
    return [powershell, "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass", "-File", str(target)]


def test_windows_launcher_validates_literal_paths_without_starting(windows_release_launcher, tmp_path):
    workspace = tmp_path / "empty workspace & untouched"
    result = subprocess.run(windows_release_launcher + ["-Workspace", str(workspace), "-ReadOnly", "-NoBrowser", "-ValidateOnly"], capture_output=True, timeout=POWERSHELL_PREFLIGHT_TIMEOUT)
    assert result.returncode == 0, result.stdout.decode(errors="replace")
    record = json.loads(result.stdout.decode(errors="replace").splitlines()[-1])
    assert record["validated"] and record["read_only"]
    assert record["backend"] == "release" and record["workspace"] == str(workspace)
    assert record["browser_requested"] is False and record["service_started"] is False
    assert not workspace.exists()


def test_windows_launcher_rejects_single_project_and_occupied_port(windows_release_launcher, tmp_path):
    project = tmp_path / "single-project"
    project.mkdir()
    (project / "project.json").write_text("{}", encoding="utf-8")
    result = subprocess.run(windows_release_launcher + ["-Workspace", str(project), "-ReadOnly", "-ValidateOnly"], capture_output=True, timeout=POWERSHELL_PREFLIGHT_TIMEOUT)
    assert result.returncode == 1
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        result = subprocess.run(windows_release_launcher + ["-Workspace", str(tmp_path / "absent"), "-Port", str(listener.getsockname()[1]), "-ReadOnly", "-ValidateOnly"], capture_output=True, timeout=POWERSHELL_PREFLIGHT_TIMEOUT)
    assert result.returncode == 1
    assert not (tmp_path / "absent").exists()


def test_windows_launcher_defaults_to_workspace_without_role_selection(windows_release_launcher, tmp_path):
    workspace = tmp_path / "default workspace & untouched"
    result = subprocess.run(
        windows_release_launcher + ["-Workspace", str(workspace), "-NoBrowser", "-ValidateOnly"],
        capture_output=True, timeout=POWERSHELL_PREFLIGHT_TIMEOUT,
    )
    assert result.returncode == 0, result.stdout.decode(errors="replace")
    record = json.loads(result.stdout.decode(errors="replace").splitlines()[-1])
    assert record["validated"] is True and record["read_only"] is False
    assert record["workspace"] == str(workspace)
    assert record["browser_requested"] is False and record["service_started"] is False
    assert not workspace.exists()
