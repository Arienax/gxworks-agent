"""Resolve read-only application resources in source and PyInstaller builds."""

from pathlib import Path
import sys


def source_root() -> Path:
    """Original source root, also the data root in a frozen build."""
    return Path(__file__).resolve().parents[1]


def resource_path(filename: str) -> Path:
    """Return an existing bundled resource path, preferring PyInstaller data."""
    candidates = []

    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        candidates.append(Path(bundle_dir) / filename)

    source_dir = source_root()
    candidates.append(source_dir / filename)
    candidates.append(source_dir.parent / "resources" / filename)
    # Keep source checkouts made before the directory cleanup readable.
    candidates.append(source_dir.parent / filename)

    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / filename)

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    return candidates[0]
