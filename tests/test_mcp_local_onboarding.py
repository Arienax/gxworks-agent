"""Local MCP onboarding stays model-free and never exposes the Agent secret."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def isolated_codex_config(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))


def test_windows_service_binding_roundtrip(monkeypatch):
    from integrations.mcp import service_credentials as credentials

    store = {}
    fake = SimpleNamespace(
        write_secret=lambda value, target: store.__setitem__(target, value),
        read_secret=lambda target: store.get(target, ""),
        delete_secret=lambda target: store.pop(target, None),
    )
    monkeypatch.setitem(sys.modules, 'storage.windows_credentials', fake)
    monkeypatch.setattr(credentials, "_is_windows", lambda: True)

    assert credentials.save_service_binding("http://127.0.0.1:8765", "agent-secret")
    saved = credentials.load_service_binding()
    assert saved == {
        "version": 1,
        "service_url": "http://127.0.0.1:8765",
        "agent_token": "agent-secret",
        "project_id": None,
    }
    public = credentials.bind_project("project_123")
    assert public == {"service_url": "http://127.0.0.1:8765", "project_id": "project_123"}
    assert credentials.load_service_binding()["project_id"] == "project_123"
    credentials.clear_service_binding("http://127.0.0.1:8765", "wrong")
    assert credentials.load_service_binding() is not None
    credentials.clear_service_binding("http://127.0.0.1:8765", "agent-secret")
    assert credentials.load_service_binding() is None


def test_launcher_check_uses_saved_service_and_bound_project(monkeypatch, capsys):
    from integrations.mcp import __main__ as launcher
    import integrations.mcp.service_credentials as credentials
    import integrations.mcp.service_client as service_client

    monkeypatch.delenv("PLC_WEB_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("GXWORKS_AGENT_SERVICE_URL", raising=False)
    monkeypatch.delenv("GXWORKS_AGENT_TOKEN_ENV", raising=False)

    monkeypatch.setattr(credentials, "load_service_binding", lambda: {
        "version": 1,
        "service_url": "http://127.0.0.1:8765",
        "agent_token": "private-agent-token",
        "project_id": "project_abc",
    })

    class Client:
        def __init__(self, url, token):
            assert url == "http://127.0.0.1:8765"
            assert token == "private-agent-token"

        def list_tools(self):
            return [{"function": {"name": "get_current_project", "parameters": {"type": "object"}}}]

    monkeypatch.setattr(service_client, "ApplicationServiceClient", Client)
    assert launcher.main(["--check"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "service_url": "http://127.0.0.1:8765",
        "project_id": "project_abc",
        "tool_count": 1,
    }


@pytest.mark.parametrize("option", ["--service-url", "environment"])
def test_launcher_never_sends_saved_token_to_another_origin(monkeypatch, option):
    from integrations.mcp import __main__ as launcher
    from integrations.mcp import service_credentials as credentials

    monkeypatch.setattr(credentials, "load_service_binding", lambda: {
        "service_url": "http://127.0.0.1:8765", "agent_token": "saved-secret", "project_id": "saved-project",
    })
    monkeypatch.delenv("PLC_WEB_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("GXWORKS_AGENT_TOKEN_ENV", raising=False)
    monkeypatch.delenv("GXWORKS_AGENT_SERVICE_URL", raising=False)
    args = ["--check"]
    if option == "environment":
        monkeypatch.setenv("GXWORKS_AGENT_SERVICE_URL", "http://127.0.0.1:8766")
    else:
        args.append("--service-url=http://127.0.0.1:8766")
    with pytest.raises(SystemExit) as error:
        launcher.main(args)
    assert error.value.code == 2


def test_launcher_does_not_reuse_project_from_another_origin(monkeypatch):
    from integrations.mcp import __main__ as launcher
    from integrations.mcp import service_credentials as credentials

    monkeypatch.setattr(credentials, "load_service_binding", lambda: {
        "service_url": "http://127.0.0.1:8765", "agent_token": "saved-secret", "project_id": "saved-project",
    })
    monkeypatch.setenv("ISOLATED_MCP_AGENT_TOKEN", "explicit-token")
    args = SimpleNamespace(service_url="http://127.0.0.1:8766", service_token_env="ISOLATED_MCP_AGENT_TOKEN", project=None, check=False)
    url, token, project = launcher._service_connection(args, [], launcher._StderrParser())
    assert (url, token, project) == ("http://127.0.0.1:8766", "explicit-token", None)


def test_explicit_connection_check_never_reads_local_credentials(monkeypatch, capsys):
    from integrations.mcp import __main__ as launcher
    from integrations.mcp import service_credentials as credentials
    from integrations.mcp import service_client

    def forbidden():
        pytest.fail("An explicit connection must not read local credentials")

    monkeypatch.setattr(credentials, "load_service_binding", forbidden)
    monkeypatch.setenv("ISOLATED_MCP_AGENT_TOKEN", "explicit-token")
    monkeypatch.setattr(service_client, "ApplicationServiceClient", lambda url, token: SimpleNamespace(list_tools=lambda: []))
    assert launcher.main(["--check", "--service-url=http://127.0.0.1:8766", "--service-token-env=ISOLATED_MCP_AGENT_TOKEN"]) == 0
    assert json.loads(capsys.readouterr().out)["project_id"] is None


def test_equals_workspace_option_still_selects_standalone(monkeypatch, tmp_path):
    from integrations.mcp import __main__ as launcher
    from integrations.mcp import service_credentials as credentials

    monkeypatch.setattr(credentials, "load_service_binding", lambda: pytest.fail("Standalone must not discover a service"))
    with pytest.raises(SystemExit) as error:
        launcher.main(["--workspace=" + str(tmp_path / "missing"), "--project=p1"])
    assert error.value.code == 2


def test_codex_connect_replaces_only_gxworks_table(monkeypatch, tmp_path):
    import application.mcp_integrations as integrations

    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        "model = 'deepseek-v4-flash'\n\n"
        "[mcp_servers.keep]\ncommand = 'keep-me'\n\n"
        "[mcp_servers.gxworks]\ncommand = 'old-launcher'\n"
        "env = { PLC_WEB_AGENT_TOKEN = 'old-secret' }\n\n"
        "[projects.'D:/trusted']\ntrust_level = 'trusted'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(integrations, "_codex_config_path", lambda: config)
    monkeypatch.setattr(
        integrations,
        "launcher_invocation",
        lambda: [r"C:\GXWorks Agent\gxworks-agent-mcp.exe"],
    )
    monkeypatch.setattr(integrations, "test_connection", lambda project_id, service_url: {
        "status": "connected", "project_id": project_id, "service_url": service_url, "tool_count": 12,
    })

    result = integrations.connect_codex("project_abc", "http://127.0.0.1:8765")
    text = config.read_text(encoding="utf-8")
    assert result["codex_connected"] is True and result["replaced_existing"] is True
    assert "deepseek-v4-flash" in text and "keep-me" in text and "trust_level = 'trusted'" in text
    assert text.count("[mcp_servers.gxworks]") == 1
    assert "gxworks-agent-mcp.exe" in text
    assert "old-secret" not in text and "PLC_WEB_AGENT_TOKEN" not in text
    assert "127.0.0.1" not in text and "project_abc" not in text


@pytest.mark.parametrize("action", ["test_connection", "connect_codex"])
def test_onboarding_rejects_another_service_without_changing_its_binding(monkeypatch, action):
    import application.mcp_integrations as integrations
    from integrations.mcp import service_credentials as credentials

    saved = json.dumps({
        "version": 1,
        "service_url": "http://127.0.0.1:8765",
        "agent_token": "isolated-agent-token",
        "project_id": "original_project",
    })
    store = {credentials.SERVICE_CREDENTIAL_TARGET: saved}
    monkeypatch.setitem(sys.modules, 'storage.windows_credentials', SimpleNamespace(
        write_secret=lambda value, target: store.__setitem__(target, value),
        read_secret=lambda target: store.get(target, ""),
    ))
    monkeypatch.setattr(credentials, "_is_windows", lambda: True)
    monkeypatch.setattr(integrations, "_run", lambda *args, **kwargs: pytest.fail("Wrong service must not launch MCP"))
    monkeypatch.setattr(integrations, "_write_codex_config", lambda *args: pytest.fail("Wrong service must not update Codex"))

    with pytest.raises(integrations.MCPIntegrationError, match="Web 服务与本机 MCP 凭据不一致"):
        getattr(integrations, action)("another_service_project", "http://127.0.0.1:8766")

    assert store == {credentials.SERVICE_CREDENTIAL_TARGET: saved}


def test_codex_table_replacement_preserves_following_server():
    import application.mcp_integrations as integrations

    original = (
        "[mcp_servers.gxworks]\ncommand='old'\n\n"
        "[mcp_servers.gxworks.env]\nOLD='1'\n\n"
        "[mcp_servers.other]\ncommand='other'\n"
    )
    updated, replaced = integrations._replace_gxworks_table(
        original, "[mcp_servers.gxworks]\ncommand='new'\n"
    )
    assert replaced
    assert "OLD='1'" not in updated
    assert "command='other'" in updated
    assert updated.count("[mcp_servers.gxworks]") == 1


def test_integrations_ui_does_not_require_token_copy():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "web" / "src" / "features" / "Settings.tsx").read_text(encoding="utf-8")
    assert "<paste PLC_WEB_AGENT_TOKEN>" not in text
    assert "连接 Codex" in text and "/integrations/mcp/codex/connect" in text
    assert "测试 MCP 连接" in text and "/integrations/mcp/test" in text
