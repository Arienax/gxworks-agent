"""Resolve read-only application resources in source and PyInstaller builds."""

from pathlib import Path
import sys


def bundled_executable(directory: str, name: str) -> Path | None:
    """Locate a release helper only in the current frozen application's directory."""
    if getattr(sys, "frozen", False):
        path = Path(sys.executable).resolve().parent / directory / name
        if path.is_file():
            return path
    return None


def resource_path(filename: str) -> Path:
    """Return an existing bundled resource path, preferring PyInstaller data."""
    candidates = []

    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        candidates.append(Path(bundle_dir) / filename)
        candidates.append(Path(bundle_dir) / "resources" / filename)

    source_dir = Path(__file__).resolve().parents[1]
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
