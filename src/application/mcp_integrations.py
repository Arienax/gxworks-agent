# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Operator-triggered local MCP onboarding.

This service never receives arbitrary commands from the browser. It binds the
current GXWorks project to the private local MCP credential, exercises the real
product launcher, and updates only the fixed `mcp_servers.gxworks` Codex config
section. PLC engineering operations remain in ToolRuntime.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from integrations.mcp.service_credentials import bind_project, load_service_binding
from integrations.mcp.service_client import validate_service_url


class MCPIntegrationError(RuntimeError):
    pass


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def launcher_invocation() -> list[str]:
    """Return a fixed launcher command; users never need its Python internals."""
    if getattr(sys, "frozen", False):
        companion = Path(sys.executable).resolve().with_name("gxworks-agent-mcp.exe")
        if companion.is_file():
            return [str(companion)]
        configured = os.environ.get("GXWORKS_MCP_EXE", "").strip()
        if configured:
            path = Path(configured).expanduser().resolve()
            if path.name.lower() == "gxworks-agent-mcp.exe" and path.is_file():
                return [str(path)]
        raise MCPIntegrationError("未找到 MCP 启动器；请完整解压 Full 包，或安装 MCP 独立下载包并将 GXWORKS_MCP_EXE 指向 gxworks-agent-mcp.exe 后重启 Web。")
    script = _root() / "scripts" / "mcp_entry.py"
    if not script.is_file():
        raise MCPIntegrationError("源码 MCP launcher 不完整。")
    return [str(Path(sys.executable).resolve()), str(script)]


def _run(parts: list[str], *, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    command = parts
    if os.name == "nt" and Path(parts[0]).suffix.lower() in {".cmd", ".bat"}:
        command = ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(parts)]
    try:
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            creationflags=flags,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MCPIntegrationError("本机集成命令无法启动或超时。") from error


def _codex_executable() -> str | None:
    for name in ("codex", "codex.exe", "codex.cmd"):
        value = shutil.which(name)
        if value:
            return value
    return None


def _codex_config_path() -> Path:
    home = os.environ.get("CODEX_HOME", "").strip()
    return Path(home).expanduser() / "config.toml" if home else Path.home() / ".codex" / "config.toml"


def _toml_string(value: str) -> str:
    # JSON quoted strings are valid TOML basic strings for the path characters
    # used here and correctly escape Windows backslashes and quotes.
    return json.dumps(str(value), ensure_ascii=False)


def _gxworks_codex_block(invocation: list[str]) -> str:
    command, *args = invocation
    lines = [
        "[mcp_servers.gxworks]",
        "command = " + _toml_string(command),
    ]
    if args:
        lines.append("args = [" + ", ".join(_toml_string(value) for value in args) + "]")
    lines.extend(("startup_timeout_sec = 30", "tool_timeout_sec = 120"))
    return "\n".join(lines) + "\n"


def _replace_gxworks_table(text: str, block: str) -> tuple[str, bool]:
    """Replace only mcp_servers.gxworks and its nested subtables."""
    lines = text.splitlines(keepends=True)
    header = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$")
    start = None
    end = None
    for index, line in enumerate(lines):
        match = header.match(line.rstrip("\r\n"))
        if not match:
            continue
        table = match.group(1).strip()
        if start is None:
            if table == "mcp_servers.gxworks":
                start = index
        elif not (table == "mcp_servers.gxworks" or table.startswith("mcp_servers.gxworks.")):
            end = index
            break
    replaced = start is not None
    if start is not None:
        end = len(lines) if end is None else end
        lines[start:end] = [block]
        return "".join(lines), replaced
    prefix = "".join(lines)
    if prefix and not prefix.endswith(("\n", "\r")):
        prefix += "\n"
    if prefix and not prefix.endswith("\n\n"):
        prefix += "\n"
    return prefix + block, False


def _write_codex_config(invocation: list[str]) -> bool:
    path = _codex_config_path()
    try:
        original = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    except (OSError, UnicodeError) as error:
        raise MCPIntegrationError("无法读取 Codex 配置文件。") from error
    updated, replaced = _replace_gxworks_table(original, _gxworks_codex_block(invocation))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".gxworks-agent-" + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(updated, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise MCPIntegrationError("无法原子更新 Codex MCP 配置。") from error
    return replaced


def status(project_id: str, service_url: str) -> dict[str, Any]:
    service_url = validate_service_url(service_url)
    binding = load_service_binding() or {}
    codex = _codex_executable()
    config_path = _codex_config_path()
    configured = False
    try:
        if config_path.is_file():
            configured = "[mcp_servers.gxworks]" in config_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        configured = False
    return {
        "service_url": service_url,
        "project_id": project_id,
        "bound_project_id": binding.get("project_id") if binding.get("service_url") == service_url else None,
        "credential_ready": bool(binding and binding.get("service_url") == service_url),
        "launcher_ready": _launcher_ready(),
        "codex_configured": configured,
        "codex_cli_available": bool(codex),
        "codex_command": Path(codex).name if codex else None,
    }


def _launcher_ready() -> bool:
    try:
        launcher_invocation()
        return True
    except MCPIntegrationError:
        return False


def test_connection(project_id: str, service_url: str) -> dict[str, Any]:
    service_url = validate_service_url(service_url)
    # A different running service must not overwrite the saved project binding.
    saved_binding = load_service_binding()
    if saved_binding and saved_binding.get("service_url") != service_url:
        raise MCPIntegrationError("当前浏览器连接的 Web 服务与本机 MCP 凭据不一致，请重启当前 Web 工作台。")
    binding = bind_project(project_id)
    if binding["service_url"] != service_url:
        raise MCPIntegrationError("当前浏览器连接的 Web 服务与本机 MCP 凭据不一致，请重启当前 Web 工作台。")
    result = _run([*launcher_invocation(), "--service", "--check"], timeout=20.0)
    if result.returncode != 0:
        raise MCPIntegrationError("MCP launcher 无法通过当前 Web 服务认证。")
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as error:
        raise MCPIntegrationError("MCP launcher 返回了无效的连接检查结果。") from error
    if payload.get("ok") is not True or payload.get("project_id") != project_id:
        raise MCPIntegrationError("MCP launcher 未绑定到当前工程。")
    return {
        "status": "connected",
        "project_id": project_id,
        "service_url": service_url,
        "tool_count": int(payload.get("tool_count") or 0),
        "message": "MCP 服务检查通过。客户端是否已加载连接，请以实际工具调用记录为准。",
    }


def connect_codex(project_id: str, service_url: str) -> dict[str, Any]:
    """Check the service and save MCP config without claiming client activity."""
    check = test_connection(project_id, service_url)
    try:
        replaced = _write_codex_config([*launcher_invocation(), "--service"])
    except OSError as error:
        raise MCPIntegrationError("Codex 连接配置未完成。请检查配置目录的写入权限后重试连接。") from error
    return {
        **check,
        "status": "connected",
        "codex_connected": True,
        "replaced_existing": replaced,
        "message": "Codex MCP 连接配置已写入。请重启 Codex App 并创建新任务，用 gxworks 描述工程需求。实际连接使用情况请查看客户端工具调用记录。",
    }


def install_local_codex(session) -> dict[str, Any]:
    """Explicit operator CLI setup; never a tool exposed to an Agent."""
    diagnostics = session.check()
    invocation = [*launcher_invocation(), "--local", "--workspace", str(session.workspace)]
    if session.project_id:
        invocation.extend(("--project", session.project_id))
    if session.version_id:
        invocation.extend(("--version", session.version_id))
    invocation.extend(("--state-dir", str(session.service.state_dir)))
    if session.approval_mode:
        invocation.extend(("--approval-mode", session.approval_mode))
    if session.confirm_full_access:
        invocation.append("--confirm-full-access")
    replaced = _write_codex_config(invocation)
    return {**diagnostics, "status": "configured", "codex_configured": True,
            "replaced_existing": replaced,
            "message": "独立 MCP 已写入 Codex 配置。刷新 MCP 连接后可直接创建、选择和修改工程，无需启动工作台。"}
