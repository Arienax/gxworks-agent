"""Open optional local research archives without weakening checks of their contents."""
from pathlib import Path
import zipfile

import pytest


def require_evidence_path(path):
    """A missing local archive is unverified; other missing files still fail."""
    path = Path(path)
    root = Path(__file__).resolve().parents[1] / "research/evidence"
    if path.resolve().is_relative_to(root) and not path.exists():
        pytest.skip(f"Local research evidence required: {path.name}; packages are not uploaded")
    return path


def open_evidence_archive(path):
    """Present archives retain the standard corruption and missing-member errors."""
    return zipfile.ZipFile(require_evidence_path(path))
