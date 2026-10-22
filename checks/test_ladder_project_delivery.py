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


def test_a_lowered_program_becomes_a_project():
    if not project_templates.blank_ladder_models():
        return  # this checkout carries no captured Ladder baseline
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project, read_ordered_program
    from gxworks2.csv_export import lower_ladder_program

    rungs = {"rungs": [{"branches": [{"inputs": [{"type": "NO", "address": "X0"}],
                                     "outputs": [{"type": "COIL", "address": "Y0"}]}]}]}
    program = lower_ladder_program(rungs, plc_model="FX3U")
    built = build_ladder_project(program, plc_model="FX3U")
    readback = read_ordered_program(read_offline_project(built.data))
    assert [(item.mnemonic, [operand.text for operand in item.operands])
            for item in readback["listing"].instructions] == [
                ("LD", ["X0"]), ("OUT", ["Y0"]), ("END", [])]
    before = read_offline_project(project_templates.blank_ladder_baseline("FX3U")).streams
    after = read_offline_project(built.data).streams
    assert sorted(before) == sorted(after)
    assert [name for name in before if before[name] != after[name]] == ["MAIN.Program.pou"]
    # The delivery states what it did not prove.
    assert built.report["validation"]["compile_state"] == "stale"
    assert built.report["validation"]["native_save"] == "not_run"
    assert built.report["validation"]["gxworks_compile"] == "not_run"
    assert built.instructions == (("LD", ("X000",)), ("OUT", ("Y000",)))
    assert built.statements == ()


def test_a_line_statement_is_delivered_with_its_step():
    """行间声明 shares the next instruction's step, as the CSV rows already say."""
    if not project_templates.blank_ladder_models():
        return
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project, read_ordered_program
    from gxworks2.csv_export import lower_ladder_program

    rungs = {"rungs": [{"debug_note": "启动后点亮",
                        "branches": [{"inputs": [{"type": "NO", "address": "X0"}],
                                      "outputs": [{"type": "COIL", "address": "Y0"}]}]}]}
    program = lower_ladder_program(rungs, plc_model="FX3U")
    assert [entry.mnemonic for entry in program.instructions] == ["LD", "OUT", "END"]
    built = build_ladder_project(program, plc_model="FX3U")
    assert built.statements == (("statement", "启动后点亮"),)
    listing = read_ordered_program(read_offline_project(built.data))["listing"]
    kinds = [(type(record).__name__, getattr(record, "role", None),
              getattr(record, "text", None) or getattr(record, "mnemonic", None))
             for record in listing.records]
    assert kinds == [("TokenText", "statement", "启动后点亮"),
                     ("TokenInstruction", None, "LD"),
                     ("TokenInstruction", None, "OUT"),
                     ("TokenInstruction", None, "END")]
    # The statement must not take a step of its own: GX Works2 stores that form as flag 1.
    text_raw = b"".join(token.raw for token in listing.records[0].tokens)
    assert text_raw[1] == 0x80 and text_raw[2] == 0
    assert [step.step for step in listing.instructions] == [0, 1, 2]


def test_delivery_refuses_what_it_cannot_place():
    from gxw.ladder_program_writer import build_ladder_project
    from gxworks2.csv_export import lower_ladder_program

    empty = lower_ladder_program({"rungs": []}, plc_model="FX3U")
    try:
        build_ladder_project(empty, plc_model="FX3U")
    except GXWFormatError as error:
        assert "no instruction" in str(error)
    else:
        raise AssertionError("an empty program must not be delivered")
    try:
        build_ladder_project(lower_ladder_program({"rungs": []}, plc_model="L02/L02-P"),
                             plc_model="L02/L02-P")
    except GXWFormatError:
        pass
    else:
        raise AssertionError("a CPU without a captured baseline must not be delivered")

def test_a_source_program_is_reproduced_record_for_record():
    """The clone path carries records the reader cannot decode instead of interpreting them."""
    if not project_templates.blank_ladder_models():
        return
    from gxw.ladder_program_writer import (build_ladder_project,
                                          build_ladder_project_from_source)
    from gxw.offline_project import read_offline_project
    from gxw.token_pou import frame_token_pou
    from gxworks2.csv_export import lower_ladder_program

    rungs = {"rungs": [{"debug_note": "第一步",
                        "branches": [{"inputs": [{"type": "NO", "address": "X0"}],
                                      "outputs": [{"type": "COIL", "address": "Y0"}]}]}]}
    built = build_ladder_project(lower_ladder_program(rungs, plc_model="FX3U"), plc_model="FX3U")
    cloned = build_ladder_project_from_source(built.data, plc_model="FX3U")
    source_body = frame_token_pou(read_offline_project(built.data).streams["MAIN.Program.pou"]).body
    cloned_body = frame_token_pou(read_offline_project(cloned.data).streams["MAIN.Program.pou"]).body
    assert cloned_body == source_body
    assert cloned.report["instructions"] == 2 and cloned.report["statements"] == 1
    assert cloned.carried_records == 0
    assert "delivery_limits" not in cloned.report


def test_a_clone_refuses_a_foreign_cpu_and_an_empty_program():
    from gxw.ladder_program_writer import build_ladder_project_from_source
    from gxworks2.csv_export import lower_ladder_program

    built_data = None
    if project_templates.blank_ladder_models():
        from gxw.ladder_program_writer import build_ladder_project

        rungs = {"rungs": [{"branches": [{"inputs": [{"type": "NO", "address": "X0"}],
                                         "outputs": [{"type": "COIL", "address": "Y0"}]}]}]}
        built_data = build_ladder_project(lower_ladder_program(rungs, plc_model="FX3U"),
                                          plc_model="FX3U").data
    if built_data is not None:
        try:
            build_ladder_project_from_source(built_data, plc_model="Q03UDV")
        except GXWFormatError as error:
            assert "belongs to" in str(error)
        else:
            raise AssertionError("a clone must refuse a different CPU than its source")
    empty = project_templates.blank_ladder_baseline("FX3U") if project_templates.blank_ladder_models() else None
    if empty is not None:
        try:
            build_ladder_project_from_source(empty, plc_model="FX3U")
        except GXWFormatError as error:
            assert "no instruction" in str(error)
        else:
            raise AssertionError("an empty source program must not be delivered")


CASES = (test_the_surface_says_whether_a_ladder_project_can_be_delivered,
         test_an_fbd_project_is_not_a_ladder_baseline,
         test_the_capture_script_refuses_projects_that_carry_logic,
         test_the_archive_manifest_is_read_but_never_trusted,
         test_a_lowered_program_becomes_a_project,
         test_a_line_statement_is_delivered_with_its_step,
         test_delivery_refuses_what_it_cannot_place,
         test_a_source_program_is_reproduced_record_for_record,
         test_a_clone_refuses_a_foreign_cpu_and_an_empty_program)

if __name__ == "__main__":
    for case in CASES:
        case()
        print("ok", case.__name__)
