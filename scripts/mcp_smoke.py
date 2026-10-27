# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Offline initialize -> tools/list -> tools/call against the actual stdio CLI.

Run from any directory with a Python environment containing requirements/mcp.txt.
Creates and removes its own temporary application workspace. No desktop data,
GUI, GX Works2, simulator, PLC, API key, or network service is used.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from agent_runtime.plc_tools import FORBIDDEN_TOOL_NAMES, SAFE_TOOL_NAMES
from plc.core import PLCCore
from plc.ir import build_plc_ir, ir_to_ladder
from application.session import ProjectSession


async def read_context(client, arguments):
    from application.read_delivery import assemble_read_pages, serialized_size
    pages = []
    while True:
        result = await client.call_tool('get_generation_context', arguments)
        page = json.loads(result.content[0].text)
        assert not result.is_error, page
        size = serialized_size(result.model_dump(by_alias=True, exclude_none=True))
        assert size['estimated_tokens'] <= 6000 and size['utf8_bytes'] <= 32768
        pages.append(page)
        next_read = page['data']['delivery']['next']
        if next_read is None:
            return assemble_read_pages(pages)
        arguments = next_read['arguments']


async def smoke() -> dict:
    with tempfile.TemporaryDirectory(prefix="gxworks-mcp-smoke-") as directory:
        store = ProjectSession(base_dir=Path(directory) / "workspace")
        project = store.create_project("MCP smoke test")
        program = build_plc_ir({
            "device_comments": {"X0": "Start", "Y0": "Run"},
            "rungs": [{
                "rung_id": 1, "debug_note": "Read-only smoke fixture",
                "header_element": None, "shared_inputs": [],
                "branches": [{
                    "branch_id": 1, "y_offset_level": 0,
                    "inputs": [{"type": "NO", "address": "X0", "label": ""}],
                    "outputs": [{"type": "COIL", "address": "Y0", "label": ""}],
                }],
            }],
        }, plc_model="FX3U", revision=1)
        version_id, output_dir = store.prepare_version(project["id"])
        compiled = PLCCore().compile_project(program, output_dir)
        store.complete_version(project["id"], version_id, {
            **store._ir_metadata(program), "target_mode": "ladder", "plc_model": "FX3U",
            "artifacts": dict(compiled["artifacts"]),
        })
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "integrations.mcp", "--stdio", "--workspace", str(store.base_dir),
                  "--project", project["id"], "--version", version_id],
            cwd=ROOT / "src",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        with anyio.fail_after(25):
            async with stdio_client(parameters) as streams:
                async with ClientSession(*streams, read_timeout_seconds=10) as client:
                    initialized = await client.initialize()
                    tools = (await client.list_tools()).tools
                    names = {tool.name for tool in tools}
                    assert names == set(SAFE_TOOL_NAMES)
                    assert not names & FORBIDDEN_TOOL_NAMES
                    result = await client.call_tool("read_network", {"network_id": "N0001"})
                    assert not result.is_error
                    network = result.structured_content["data"]["network"]
                    assert network["writes"] == ["Y0"]
                    assert json.loads(result.content[0].text) == result.structured_content
                    summary = {
                        "ok": True,
                        "server": initialized.server_info.name,
                        "protocol_version": initialized.protocol_version,
                        "tool_count": len(tools),
                        "called_tool": "read_network",
                        "network_id": network["id"],
                    }
        # A separate, versionless project exercises the actual first-generation
        # entry point, not a patch disguised as generation on an existing IR.
        new_project = store.create_project("MCP first-generation smoke")
        before = {
            path.relative_to(store.base_dir): path.read_bytes()
            for path in store.base_dir.rglob("*") if path.is_file()
        }
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "integrations.mcp", "--stdio", "--workspace", str(store.base_dir),
                  "--project", new_project["id"]],
            cwd=ROOT / "src",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        with anyio.fail_after(25):
            async with stdio_client(parameters) as streams:
                async with ClientSession(*streams, read_timeout_seconds=10) as client:
                    await client.initialize()
                    assert {tool.name for tool in (await client.list_tools()).tools} == set(SAFE_TOOL_NAMES)
                    context = await read_context(client, {})
                    assert context["project_id"] == new_project["id"]
                    assert context["output_contract"]["format"] == "ladder_v1"
                    candidate = await client.call_tool("create_program_candidate", {"ladder": ir_to_ladder(program)})
                    assert not candidate.is_error
                    assert candidate.structured_content["status"] == "confirmation_required"
                    assert candidate.structured_content["data"]["revision"] == 1
                    assert "_candidate_ir" not in candidate.model_dump_json()
                    assert "_confirmed_spec" not in candidate.model_dump_json()
                    assert json.loads(candidate.content[0].text) == candidate.structured_content
        saved = store.get_project(new_project["id"])
        assert saved["versions"] == []
        assert saved["active_version_id"] is None
        assert {
            path.relative_to(store.base_dir): path.read_bytes()
            for path in store.base_dir.rglob("*") if path.is_file()
        } == before
        summary["generation"] = {
            "called_tools": ["get_generation_context", "create_program_candidate"],
            "status": "confirmation_required", "revision": 1,
            "version_count": 0, "active_version_id": None, "workspace_unchanged": True,
        }
        return summary


async def local_smoke(launcher=None) -> dict:
    """Exercise independent source or packaged runtime in disposable state."""
    from application.engineering_session import SESSION_TOOL_NAMES

    with tempfile.TemporaryDirectory(prefix="gxworks-mcp-local-smoke-") as directory:
        root = Path(directory)
        command = str(launcher) if launcher else sys.executable
        entry = [] if launcher else [str(ROOT / "scripts/mcp_entry.py")]
        env = {key: value for key, value in os.environ.items() if key not in {
            "GXWORKS_AGENT_SERVICE_URL", "GXWORKS_AGENT_TOKEN_ENV", "PLC_WEB_AGENT_TOKEN",
            "PLC_AI_WORKSPACE_DIR", "GXWORKS_MCP_WORKSPACE_DIR"}}
        parameters = StdioServerParameters(command=command,
            args=[*entry, "--local", "--workspace", str(root / "workspace"), "--state-dir", str(root / "state")],
            cwd=root, env={**env, "PYTHONIOENCODING": "utf-8"})
        ladder = {"device_comments": {"X0": "Start", "Y0": "Run"}, "rungs": [{
            "rung_id": 1, "header_element": None, "shared_inputs": [], "branches": [{
                "branch_id": 1, "y_offset_level": 0,
                "inputs": [{"type": "NO", "address": "X0"}],
                "outputs": [{"type": "COIL", "address": "Y0"}],
            }],
        }]}
        with anyio.fail_after(60):
            async with stdio_client(parameters) as streams:
                async with ClientSession(*streams) as client:
                    initialized = await client.initialize()
                    names = {tool.name for tool in (await client.list_tools()).tools}
                    assert names == set(SAFE_TOOL_NAMES) | set(SESSION_TOOL_NAMES)
                    created = await client.call_tool("create_project", {
                        "name": "Independent smoke", "plc_model": "FX3U", "target_mode": "ladder"})
                    assert not created.is_error, created.structured_content
                    project_id = created.structured_content["data"]["selected_project_id"]
                    context = await read_context(client, {
                        "user_requirement": "X0 controls Y0", "workflow": "direct_generation"})
                    candidate = await client.call_tool("create_program_candidate", {"ladder": ladder,
                        "generation_context_id": context["generation_context_id"]})
                    assert not candidate.is_error, candidate.structured_content
                    assert candidate.structured_content["status"] == "saved"
                    version_id = candidate.structured_content["data"]["version_id"]
                    artifacts = await client.call_tool("get_project_artifacts", {})
                    assert not artifacts.is_error, artifacts.structured_content
                    assert artifacts.structured_content["data"]["version_id"] == version_id
                    assert all(Path(item["path"]).is_file() for item in artifacts.structured_content["data"]["artifacts"])
                    assert "_candidate_ir" not in candidate.model_dump_json()
        saved = SessionStore(base_dir=root / "workspace", create=False).get_project(project_id)
        assert len(saved["versions"]) == 1 and saved["active_version_id"] == version_id
        return {"ok": True, "mode": "local", "server": initialized.server_info.name,
                "tool_count": len(names), "status": "saved", "version_count": 1,
                "artifacts_available": True, "workbench_required": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true", help="Check independent project creation, generation and saving.")
    parser.add_argument("--launcher", type=Path, help="Use a built MCP executable for the independent smoke test.")
    args = parser.parse_args()
    if args.launcher and not args.local:
        parser.error("--launcher requires --local")
    print(json.dumps(anyio.run(local_smoke, args.launcher) if args.local else anyio.run(smoke), ensure_ascii=False))
