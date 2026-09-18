"""Check a built Web package without creating a project or invoking GX/MX.

This launches only the Web executable, in read-only mode against a temporary
absent workspace. The bundled Simulator2 gateway is checked as a file only and
the MCP product executable is invoked only with --help.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


def check_package(package, archive_path=None):
    required = (
        "GXWorks-Agent-Web.exe", "gxworks-agent-mcp.exe", "gxworks-agent-mcp.cmd",
        "web/dist/index.html", "config.default.json",
        "pattern_library.json", "plc_models.json", "knowledge/fx3u_knowledge.sqlite",
        "knowledge/fx3u_dense_lsa.npz", "knowledge/manifest.json",
        "knowledge/THIRD_PARTY_NOTICES.md", "resources/locales/en.json",
        "resources/locales/ja.json", "simulator-gateway/PlcAi.GxSimulator2Gateway.exe",
        "start-web.cmd", "scripts/start_web.ps1",
        "README.md", "README.zh-CN.md", "docs/integrations/web.md",
        "docs/architecture/web-migration-checklist.md", "docs/architecture/web-migration-baseline.md",
    )
    for name in required:
        if not (package / name).is_file():
            raise RuntimeError("Missing package resource: " + name)
    if (package / "config.json").exists():
        raise RuntimeError("Do not ship user config.json in a clean release package.")
    help_result = subprocess.run(
        [str(package / "gxworks-agent-mcp.exe"), "--help"],
        cwd=package,
        capture_output=True,
        timeout=15,
    )
    if help_result.returncode != 0 or help_result.stdout or b"GXWorks Agent MCP server" not in help_result.stderr:
        raise RuntimeError("Bundled MCP product launcher did not expose a clean stdio-safe help entry.")
    qt_excluded = None
    if archive_path is not None:
        from PyInstaller.archive.readers import ZlibArchiveReader

        archive = ZlibArchiveReader(str(archive_path))
        forbidden = {"main", "ui", "PyQt5", "PyQt6", "PySide2", "PySide6"}
        qt_excluded = not any(name.split(".")[0] in forbidden for name in archive.toc)
        if not qt_excluded:
            raise RuntimeError("Desktop UI modules were included in the Web archive.")
    return qt_excluded


def smoke(package, archive_path=None):
    package = Path(package).resolve()
    qt_excluded = check_package(package, archive_path)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    token = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix="gx-web-package-smoke-") as scratch:
        scratch = Path(scratch)
        workspace = scratch / "absent-workspace"
        with (scratch / "server.log").open("w+", encoding="utf-8") as errors:
            process = subprocess.Popen(
                [str(package / "GXWorks-Agent-Web.exe"), "--workspace", str(workspace), "--read-only", "--port", str(port)],
                env={**os.environ, "PLC_WEB_OPERATOR_TOKEN": token, "PLC_WEB_AGENT_TOKEN": secrets.token_urlsafe(32)},
                stdout=subprocess.DEVNULL, stderr=errors,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                origin = "http://127.0.0.1:" + str(port)
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                ready = False
                for _ in range(100):
                    if process.poll() is not None:
                        break
                    try:
                        ready = json.load(opener.open(origin + "/api/health", timeout=0.5))["status"] == "ok"
                        if ready:
                            break
                    except Exception:
                        time.sleep(0.1)
                if not ready:
                    errors.flush()
                    errors.seek(0)
                    raise RuntimeError(errors.read().replace(token, "[redacted]"))
                html = opener.open(origin + "/", timeout=3).read().decode("utf-8")
                assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)
                if not assets or not all(opener.open(origin + asset, timeout=3).status == 200 for asset in assets):
                    raise RuntimeError("Packaged static assets are missing.")
                request = urllib.request.Request(origin + "/api/session", data=json.dumps({"token": token}).encode(), headers={"Content-Type": "application/json", "Origin": origin})
                if not json.load(opener.open(request, timeout=3))["authenticated"]:
                    raise RuntimeError("Packaged operator login failed.")
                if json.load(opener.open(origin + "/api/projects", timeout=3)) != {"projects": []}:
                    raise RuntimeError("Temporary workspace should have no projects.")
                capabilities = json.load(opener.open(origin + "/api/capabilities", timeout=3))
                if capabilities["operations"]["gx_compile"] is not False or workspace.exists():
                    raise RuntimeError("Read-only or capability boundary failed.")
                return {
                    "ok": True, "packaged_server_started": True, "static_assets_loaded": len(assets),
                    "operator_login": True, "read_only_workspace_unchanged": True,
                    "qt_modules_excluded": qt_excluded, "gateway_binary_bundled_not_started": True,
                    "mcp_launcher_bundled": True, "native_gx_not_tested": True,
                }
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", type=Path, default=root / "dist/GXWorks-Agent-Web")
    parser.add_argument("--archive", type=Path, default=root / "build/web/PYZ-00.pyz")
    args = parser.parse_args(argv)
    print(json.dumps(smoke(args.package_dir, args.archive), ensure_ascii=False))


if __name__ == "__main__":
    main()
