# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Resolve explicitly configured optional offline inputs for development tools."""
from __future__ import annotations

import os
from pathlib import Path


def private_root() -> Path:
    """Use the sibling archive, or an explicitly configured archive checkout."""
    configured = os.environ.get("GXWORKS_AGENT_PRIVATE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "gxworks-agent-private"


def private_path(relative: str | Path) -> Path:
    """Resolve an archive-relative path for offline tools and evidence tests."""
    name = Path(relative)
    if name.is_absolute() or name.drive or name.root or ".." in name.parts:
        raise ValueError("private material paths must be archive-relative")
    return private_root() / name
