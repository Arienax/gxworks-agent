"""Synthetic regressions for delivering a generated Ladder program as a project.

The shipped FBD archive cannot stand in for a Ladder project, so these cases pin the
boundary between "a Ladder project can be delivered" and "only the CSV and program
candidate can": what the surface reports, what it refuses, and what a capture must be
before it may become a template. No manual text, private sample or user project is used.
"""
from __future__ import annotations

import json
import lzma
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gxw import project_templates  # noqa: E402
from gxw.models import GXWFormatError  # noqa: E402
from gxw.object_model import default_baseline  # noqa: E402
from gxworks2.project_profiles import native_project_profile  # noqa: E402

SCRIPT = ROOT / "scripts" / "freeze_blank_ladder_projects.py"


def test_the_surface_says_whether_a_ladder_project_can_be_delivered():
    for cpu in ("FX3U", "Q03UDV"):
        support = project_templates.ladder_generation_support(cpu)
        assert support["cpu"] == native_project_profile(cpu).cpu
        assert support["status"] in {"available", "unsupported"}
        assert (support["reason"] is None) == (support["status"] == "available")
    if project_templates.blank_ladder_models():
        baseline = project_templates.blank_ladder_baseline("FX3U")
        assert project_templates.verify_ladder_baseline(baseline, "FX3U")
    else:
        try:
            project_templates.blank_ladder_baseline("FX3U")
        except GXWFormatError as error:
            assert "CSV" in str(error)
        else:
            raise AssertionError("a missing capture must not yield a baseline")


def test_an_fbd_project_is_not_a_ladder_baseline():
    with_assertion = default_baseline()
    try:
        project_templates.verify_ladder_baseline(with_assertion, "FX3U")
    except GXWFormatError as error:
        assert "Ladder Program" in str(error) and "fbd" in str(error)
    else:
        raise AssertionError("an FBD project must not become a Ladder baseline")
    assert project_templates.captured_project_cpu(with_assertion) == "FX3U/FX3UC"


def test_the_capture_script_refuses_projects_that_carry_logic(tmp_path=None):
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        capture = Path(directory) / "claimed-ladder.gxw"
        capture.write_bytes(default_baseline())
        completed = subprocess.run([sys.executable, str(SCRIPT), "--capture-dir", directory],
                                   capture_output=True, text=True, cwd=str(ROOT))
        assert completed.returncode != 0
        assert "nothing was frozen" in completed.stderr or "nothing was frozen" in completed.stdout
        assert "Ladder" in completed.stdout
        target = Path(directory) / "archive.zip.xz"
        assert not target.exists()
    with tempfile.TemporaryDirectory() as directory:
        completed = subprocess.run([sys.executable, str(SCRIPT), "--capture-dir", directory],
                                   capture_output=True, text=True, cwd=str(ROOT))
        assert completed.returncode != 0
        assert "no .gxw capture" in completed.stderr


def test_the_archive_manifest_is_read_but_never_trusted():
    import tempfile

    models = {"FX3U/FX3UC": {"cpu": "FX3U/FX3UC", "file": "FX3U.gxw",
                             "sha256": "0" * 64, "size": 4, "program": "1",
                             "capture": "x.gxw", "data": b"not a project"}}
    import importlib.util

    spec = importlib.util.spec_from_file_location("freeze_blank_ladder_projects", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    write_archive = module.write_archive
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "archive.zip.xz"
        write_archive(models, target)
        archive_bytes = target.read_bytes()
    original = project_templates.template_bytes
    project_templates._ladder_archive.cache_clear()
    project_templates.template_bytes = lambda name: archive_bytes
    try:
        assert project_templates.blank_ladder_models() == ("FX3U/FX3UC",)
        assert project_templates.ladder_generation_support("FX3U")["status"] == "available"
        try:
            project_templates.blank_ladder_baseline("FX3U")
        except GXWFormatError as error:
            assert "does not open" in str(error) or "container" in str(error)
        else:
            raise AssertionError("a manifest row must not become a usable baseline by itself")
    finally:
        project_templates.template_bytes = original
        project_templates._ladder_archive.cache_clear()


CASES = (test_the_surface_says_whether_a_ladder_project_can_be_delivered,
         test_an_fbd_project_is_not_a_ladder_baseline,
         test_the_capture_script_refuses_projects_that_carry_logic,
         test_the_archive_manifest_is_read_but_never_trusted)


if __name__ == "__main__":
    for case in CASES:
        case()
        print("ok", case.__name__)
