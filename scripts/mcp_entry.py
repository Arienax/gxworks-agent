"""Product launcher entry for GXWorks Agent MCP.

This file makes source checkouts invokable without PYTHONPATH or knowledge of the
internal module path.  Packagers may use the same entry for GXWorks-Agent-MCP.exe.
"""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from integrations.mcp.__main__ import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
