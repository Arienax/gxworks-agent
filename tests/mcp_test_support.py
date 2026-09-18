"""Subprocess launchers with an empty, in-memory credential store for tests."""

import os
import sys


def isolated_mcp_environment(**overrides):
    env = dict(os.environ)
    for name in ("PLC_WEB_AGENT_TOKEN", "GXWORKS_AGENT_SERVICE_URL", "GXWORKS_AGENT_TOKEN_ENV", "ISOLATED_MCP_AGENT_TOKEN"):
        env.pop(name, None)
    env.update(overrides)
    return env


def isolated_mcp_command(*args, entry=None):
    # Stub the low-level store before running the real entry. Tests must not
    # depend on, inspect, or update the signed-in user's Credential Manager.
    setup = "import runpy, sys, types\ndef forbidden(*args, **kwargs):\n    raise AssertionError('A launcher test cannot write credentials')\nsys.modules['storage.windows_credentials'] = types.SimpleNamespace(\n    read_secret=lambda target: '', write_secret=forbidden, delete_secret=forbidden)\nmode, target, *arguments = sys.argv[1:]\nsys.argv = [target, *arguments]\nif mode == 'module':\n    runpy.run_module(target, run_name='__main__')\nelse:\n    runpy.run_path(target, run_name='__main__')\n"
    return [sys.executable, "-c", setup, "path" if entry else "module", str(entry or "integrations.mcp"), *args]
