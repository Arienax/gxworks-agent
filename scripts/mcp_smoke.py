"""Offline initialize -> tools/list -> tools/call against the actual stdio CLI.

Run from any directory with a Python environment containing requirements/mcp.txt.
Creates and removes its own temporary SessionStore workspace. No desktop data,
GUI, GX Works2, simulator, PLC, API key, or network service is used.
"""

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
from storage.session import SessionStore


async def smoke() -> dict:
    with tempfile.TemporaryDirectory(prefix="gxworks-mcp-smoke-") as directory:
        store = SessionStore(base_dir=Path(directory) / "workspace")
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
                    context = await client.call_tool("get_generation_context", {})
                    assert not context.is_error
                    assert context.structured_content["data"]["project_id"] == new_project["id"]
                    assert context.structured_content["data"]["output_contract"]["format"] == "ladder_v1"
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


if __name__ == "__main__":
    print(json.dumps(anyio.run(smoke), ensure_ascii=False))
