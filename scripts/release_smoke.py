"""Audit and exercise executable distributions in isolated temporary state."""
from __future__ import annotations
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.request
import urllib.parse

URL = "https://github.com/Arienax/gxworks-agent"


def audit(package, component):
    from runtime_knowledge_resources import audit_packaged_knowledge
    audit_packaged_knowledge(package)
    required = {"LICENSE", "LICENSE-SCOPE.md", "THIRD_PARTY_NOTICES.md",
                "THIRD_PARTY_LICENSES/README.md", "gxw/templates/fx3u.gxw",
                "gxw/templates/blank-fbd-projects.zip.xz",
                "resources/instructions/mitsubishi/fx3u_step_widths.json"}
    for name in required:
        assert (package / name).is_file(), name
    assert "Apache License" in (package / "LICENSE").read_text(encoding="utf-8")[:500]
    internal_template_keys = {'source_evidence', 'evidence', 'float32_evidence',
                              'dll_sha256', 'instruction_lookup_rva', 'instruction_text_rva', 'operand_text_rva'}
    for path in (package / 'gxw/templates').glob('*.json'):
        assert not internal_template_keys.intersection(json.loads(path.read_text(encoding='utf-8'))), path
    widths = json.loads((package / 'resources/instructions/mitsubishi/fx3u_step_widths.json').read_text(encoding='utf-8'))
    assert 'description' not in widths['step_width_profile']
    assert all(row.get('evidence') in {None, 'native encoding observation'} for row in
               (*widths['step_width_profile'].get('rules', []), *widths['step_width_profile'].get('exact_forms', [])))
    for path in package.rglob("*"):
        if path.is_file():
            assert not path.is_symlink(), path
            assert path.suffix.lower() not in {".pdf", ".pdb", ".map", ".cs", ".ipynb"}, path
            assert not {"research", "reports", "evidence", ".git"} & set(path.relative_to(package).parts), path
    for directory, name in (("workspace-adapter", "PlcAi.WorkspaceSourceSave.exe"),
                            ("native-active", "PlcAi.ActiveDocument.exe"),
                            ("hardware-reader", "PlcAi.HardwareReader.exe"),
                            ("simulator-gateway", "PlcAi.GxSimulator2Gateway.exe")):
        assert (package / directory / name).is_file()
    from PyInstaller.archive.readers import CArchiveReader
    mcp_exe = package / "gxworks-agent-mcp.exe"
    assert mcp_exe.is_file()
    assert (package / "gxworks-agent-mcp.cmd").is_file()
    modules = set(CArchiveReader(str(mcp_exe)).open_embedded_archive('PYZ.pyz').toc)
    forbidden = {'openai', 'jiter', 'fastapi', 'uvicorn', 'pdfplumber', 'pdfminer', 'pypdfium2',
                 'haystack_experimental', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'pytest', '_pytest', 'IPython'}
    unexpected = {name.split('.')[0] for name in modules} & forbidden
    assert not unexpected, 'Non-runtime MCP dependencies: ' + repr(sorted(unexpected))
    assert 'integrations.web' not in modules
    assert 'haystack.components.routers.metadata_router' in modules
    exe = package / ("GXWorks-Agent-Web.exe" if component == "full" else "gxworks-agent-mcp.exe")
    assert exe.is_file()
    if component == "mcp":
        assert not (package / "web/dist/index.html").exists()
        assert not (package / "GXWorks-Agent-Web.exe").exists()
        assert not (package / "resources/assets").exists()
        assert (package / "web/dist/simulator.html").is_file()
    else:
        assert (package / "start-web.cmd").is_file()
        assert (package / "web/dist/index.html").is_file()
        assert (package / "web/dist/simulator.html").is_file()
    return exe


def environment(scratch):
    # No inherited credentials or installed service/workspace configuration.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PLC_", "GXWORKS_", "GX_", "OPENAI_", "ANTHROPIC_"))
           and key not in {"PYTHONPATH", "PYTHONHOME"}}
    env.update(PLC_AI_CONFIG_PATH=str(scratch / "config.json"),
               PLC_AI_DATA_DIR=str(scratch / "data"), GXWORKS_MCP_WORKSPACE_DIR=str(scratch / "workspace"))
    return env


def smoke_mcp(exe, scratch):
    import anyio
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    env = environment(scratch)
    result = subprocess.run([str(exe), "--help"], cwd=scratch, env=env, capture_output=True, timeout=30)
    assert result.returncode == 0 and not result.stdout and URL.encode() in result.stderr
    wrapper = subprocess.run([str(exe.with_suffix('.cmd')), "--help"], cwd=scratch,
                             env=env, capture_output=True, timeout=30)
    assert wrapper.returncode == 0 and not wrapper.stdout and URL.encode() in wrapper.stderr
    params = StdioServerParameters(command=str(exe), args=["--local", "--workspace", str(scratch / "workspace"),
                                  "--state-dir", str(scratch / "state")], cwd=scratch, env=env)
    async def exercise():
        with anyio.fail_after(120):
            with (scratch / "mcp.log").open("w", encoding="utf-8") as errors:
                async with stdio_client(params, errlog=errors) as streams:
                    async with ClientSession(*streams) as client:
                        initialized = await client.initialize()
                        assert str(initialized.server_info.website_url) == URL
                        assert URL in initialized.instructions
                        count = len((await client.list_tools()).tools)
                        created = await client.call_tool("create_project", {"name": "Release smoke", "plc_model": "FX3U", "target_mode": "ladder"})
                        assert not created.is_error, created
                        facts = await client.call_tool('search_plc_manual', {'query': 'PLSY 操作数', 'plc_model': 'FX3U', 'top_k': 2})
                        assert not facts.is_error, facts
                        fact_rows = facts.structured_content['data']['results']
                        assert fact_rows and all('[PLC KNOWLEDGE]' in row['text'] for row in fact_rows)
                        assert all(not set(row) & {'id', 'source_id', 'manual_id', 'technical_status', 'distribution_status'} for row in fact_rows)
                        assert all('source_verified' not in row['text'] and 'native_compile' not in row['text'] for row in fact_rows)
                        assert all('[PAGE ' not in row['text'] and '[TABLE page=' not in row['text'] for row in fact_rows)
                        debug_facts = await client.call_tool('search_plc_manual', {'query': 'PLSY 操作数', 'plc_model': 'FX3U', 'top_k': 2, 'debug': True})
                        assert not debug_facts.is_error, debug_facts
                        debug_rows = debug_facts.structured_content['data']['results']
                        assert debug_rows and all(row['id'] and row.get('content_origin') == 'authored_fact' for row in debug_rows)
                        context = await client.call_tool("get_generation_context", {"user_requirement": "X0 controls Y0", "workflow": "direct_generation"})
                        assert not context.is_error, context
                        assert 'generation_handoff' not in context.structured_content['data']
                        ladder = {"result": {"r": [
                            {"c": ["NO X0"], "b": [{"i": [], "o": ["COIL Y0"]}]},
                            {"h": ["NO X0"], "s": [], "b": [{"i": [], "o": ["COIL M0"]}]},
                        ]}}
                        candidate = await client.call_tool("create_program_candidate", {"ladder": ladder,
                            "generation_context_id": context.structured_content["data"]["generation_context_id"]})
                        assert not candidate.is_error and candidate.structured_content["status"] == "saved", candidate
                        artifacts = await client.call_tool("get_project_artifacts", {})
                        assert not artifacts.is_error, artifacts
                        files = artifacts.structured_content["data"]["artifacts"]
                        assert files and all(Path(item["path"]).is_file() for item in files)
                        return {"tool_count": count, "project_created": True, "candidate_saved": True, "artifacts": len(files), "github_metadata": True}
    return anyio.run(exercise)


def smoke_web(exe, scratch):
    env = environment(scratch)
    for flag in ("--self-test-core", "--self-test-knowledge", "--self-test-settings", "--self-test-openai-sdk"):
        result = subprocess.run([str(exe), flag], cwd=scratch, env=env, capture_output=True, timeout=90)
        assert result.returncode == 0, (flag, result.stdout.decode(errors="replace"), result.stderr.decode(errors="replace"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0)); port = listener.getsockname()[1]
    token = secrets.token_urlsafe(32)
    env.update(PLC_WEB_OPERATOR_TOKEN=token, PLC_WEB_AGENT_TOKEN=secrets.token_urlsafe(32))
    workspace = scratch / "absent"
    with (scratch / "web.log").open("w+", encoding="utf-8") as log:
        process = subprocess.Popen([str(exe), "--workspace", str(workspace), "--state-dir", str(scratch / "state"),
             "--read-only", "--no-service-discovery", "--port", str(port)], cwd=scratch, env=env,
             stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            origin = "http://127.0.0.1:" + str(port)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            for attempt in range(200):
                if process.poll() is not None:
                    log.seek(0); raise RuntimeError(log.read().replace(token, "[redacted]"))
                try:
                    if json.load(opener.open(origin + "/api/health", timeout=.5))["status"] == "ok": break
                except Exception:
                    time.sleep(.15)
            else: raise RuntimeError("Web startup timeout")
            html = opener.open(origin + "/").read().decode()
            assets = re.findall(r'(?:src|href)="((?:[.]/|/)?assets/[^"]+)"', html)
            assert assets, "No frontend asset references in the served HTML"
            bodies = [opener.open(urllib.parse.urljoin(origin + "/", name)).read() for name in assets]
            assert any(URL.encode() in body for body in bodies)
            request = urllib.request.Request(origin + "/api/session", data=json.dumps({"token": token}).encode(),
                headers={"Content-Type": "application/json", "Origin": origin})
            assert json.load(opener.open(request))["authenticated"]
            assert json.load(opener.open(origin + "/api/projects")) == {"projects": []}
            assert isinstance(json.load(opener.open(origin + "/api/capabilities")), dict)
            bridge = subprocess.run([str(exe.with_name('gxworks-agent-mcp.exe')), '--service',
                '--service-url', origin, '--check'], cwd=scratch, env=env, capture_output=True, timeout=30)
            assert bridge.returncode == 0, bridge.stderr.decode(errors='replace')
            bridge_status = json.loads(bridge.stdout)
            assert bridge_status['ok'] and bridge_status['tool_count'] > 0
            assert not workspace.exists()
            return {"http_started": True, "login": True, "assets": len(assets), "github_link": True,
                    "core_and_knowledge": True, "mcp_service_bridge": True}
        finally:
            process.terminate()
            try: process.wait(timeout=8)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=8)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("full", "web", "mcp"))
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    component = 'full' if args.component == 'web' else args.component
    exe = audit(args.package.resolve(), component)
    with tempfile.TemporaryDirectory(prefix="gx-release-smoke-") as directory:
        scratch = Path(directory)
        mcp_state = scratch / 'mcp'
        mcp_state.mkdir()
        result = {'mcp': smoke_mcp(exe.with_name('gxworks-agent-mcp.exe'), mcp_state)}
        if component == 'full':
            web_state = scratch / 'web'
            web_state.mkdir()
            result['web'] = smoke_web(exe, web_state)
    print(json.dumps({"component": component, "package_audit": True, **result}))
