"""Product MCP entry must hide the repository's internal Python module layout."""

import os
import subprocess
import sys
from pathlib import Path

from mcp_test_support import isolated_mcp_command, isolated_mcp_environment


ROOT = Path(__file__).resolve().parents[1]


def _run(*args, workspace=None):
    env = isolated_mcp_environment(PYTHONPATH="", PLC_AI_WORKSPACE_DIR="",
                                   GXWORKS_MCP_WORKSPACE_DIR=str(workspace) if workspace else "")
    return subprocess.run(
        isolated_mcp_command(*args, entry=ROOT / "scripts" / "mcp_entry.py"),
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=15,
    )


def test_product_entry_help_requires_no_pythonpath():
    result = _run("--help")
    assert result.returncode == 0
    assert result.stdout == b""
    assert b"GXWorks Agent MCP server" in result.stderr


def test_product_entry_requires_credentials_only_for_explicit_service():
    result = _run("--service", "--project", "p1")
    assert result.returncode == 2
    assert result.stdout == b""
    assert b"PLC_WEB_AGENT_TOKEN" in result.stderr


def test_product_entry_default_check_is_independent_of_web(tmp_path):
    import json

    result = _run("--check", workspace=tmp_path / "not-created")
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["mode"] == "local" and report["local_autosave"] is True
    assert report["project_id"] is None
    assert not (tmp_path / "not-created").exists()


def test_explicit_standalone_mode_requires_workspace():
    result = _run("--standalone", "--project", "p1")
    assert result.returncode == 2
    assert result.stdout == b""
    assert b"workspace" in result.stderr.lower()


def test_product_entry_parses_the_installed_pwm_contract():
    import json

    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "mcp_entry.py"), "--self-test-pwm"],
                            cwd=ROOT, env=isolated_mcp_environment(PYTHONPATH=""),
                            capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "passed" and report["contract_matches_reviewed_facts"] is True
    assert report["forms"] == ["DPWM", "PWM"] and report["models"] == 4
    assert Path(report["source_path"]).name == "pwm_reviewed_facts.json"
    assert Path(report["generated_path"]).name == "pwm_contract.generated.json"
    assert report["source_binding"].endswith(report["source_sha256"])
