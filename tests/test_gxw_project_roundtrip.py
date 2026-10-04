"""Recorded GX Works2 observations; these tests do not launch vendor software."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import runpy
import struct
import zipfile

import pytest

from src.gxw.experiment import compare_programs
from src.gxw.fb_connectivity import fb_connectivity_model
from src.gxw.models import Point
from src.gxw.structured_pou import parse_structured_pou


FIXTURE = json.loads((Path(__file__).parent / "fixtures/gxw_project_roundtrip_20260910.json").read_text(encoding="utf-8"))


def payload(case, name="1.Program.pou"):
    stream = FIXTURE[case]["streams"][name]
    raw = bytes.fromhex(stream["hex"])
    assert hashlib.sha256(raw).hexdigest() == stream["sha256"]
    return raw


def program(case):
    return parse_structured_pou(payload(case), logical_name="1.Program.pou")


def test_recorded_series_compile_save_reload_preserves_model():
    before, after = program("s"), program("sc")
    assert all(compare_programs(before, after)["checks"].values())
    assert [n.symbol for n in after.nodes] == ["X0", "X1", "Y0"]
    assert len(after.wires) == 4
    assert payload("s", "MAIN.res") != payload("sc", "MAIN.res")


def test_recorded_fb_wire_links_distinct_formals_and_survives_compiler():
    before, after = program("f2"), program("f2c")
    assert all(compare_programs(before, after)["checks"].values())
    assert payload("f2") == payload("f2c")
    model = fb_connectivity_model(after)
    assert len(model["nets"]) == 1
    net = model["nets"][0]
    source, target = net["sources"][0], net["sinks"][0]
    assert (source["instance"], source["formal"], source["port_index"]) == ("TON_A", "Q", 2)
    assert (target["instance"], target["formal"], target["port_index"]) == ("TON_B", "IN", 0)
    assert source["point"] != target["point"]
    assert net["connection"] == "wire_network" and len(net["wire_offsets"]) == 1
    # Removing/misrouting the actual wire must remove the relationship.
    broken = replace(after, wires=tuple(w for w in after.wires if w.offset not in net["wire_offsets"]))
    assert fb_connectivity_model(broken)["nets"] == []
    assert not compare_programs(after, broken)["checks"]["semantic_graph"]
    misrouted = replace(after, wires=tuple(replace(w, start=Point(w.start.x, w.start.y + 1),
                                                 end=Point(w.end.x, w.end.y + 1))
                                         if w.offset in net["wire_offsets"] else w for w in after.wires))
    wrong = fb_connectivity_model(misrouted)["nets"][0]
    assert wrong["sinks"][0]["formal"] == "PT"


def test_recorded_declaration_fix_is_separate_from_pou_and_global_labels():
    assert payload("bu") == payload("b2")
    assert payload("bu", "Global1.gh") == payload("b2c", "Global1.gh")
    old, new = payload("bu", "1.Labels.lh"), payload("b2", "1.Labels.lh")
    for name in ("TON_A", "TON_B"):
        assert name.encode("utf-16le") not in old
        assert name.encode("utf-16le") in new
    assert payload("b2", "MAIN.res") != payload("b2c", "MAIN.res")
    # Recorded successful compilation preserves declared instance names/types.
    assert all(compare_programs(program("b2"), program("b2c"))["checks"].values())


def test_recorded_type_binding_requires_matching_local_declaration():
    def pair(kind):
        return (struct.pack("<I", 6) + "TON_A\0".encode("utf-16le")
                + struct.pack("<I", 4) + (kind + "\0").encode("utf-16le"))

    # A failed compile did not repair the mismatch. The UI declaration edit
    # fixes Labels independently; the successful compiler preserves both.
    assert next(n.type_name for n in program("tu").nodes if n.symbol == "TON_A") == "TOF"
    assert pair("TON") in payload("tu", "1.Labels.lh")
    assert pair("TOF") not in payload("tu", "1.Labels.lh")
    assert payload("tu") == payload("t2")
    assert all(compare_programs(program("t2"), program("t2c"))["checks"].values())
    assert [r.raw for r in program("t2").iter_records()] == [r.raw for r in program("t2c").iter_records()]
    assert pair("TOF") in payload("t2c", "1.Labels.lh")
    assert pair("TON") not in payload("t2c", "1.Labels.lh")
    assert payload("tu", "Global1.gh") == payload("t2c", "Global1.gh")


@pytest.mark.parametrize("mode,source,target", [("rename", "b0", "b1"),
                                               ("type", "b2c", "t1"),
                                               ("connect", "b2c", "f2")])
def test_reproduction_controls_match_recorded_project_bytes(mode, source, target):
    root = Path(__file__).resolve().parents[1]
    # The standalone research CLI uses the same top-level gxw import as tools/.
    make_control = runpy.run_path(str(root / "research/reproduce_fb_controls.py"))["make_control"]
    from gxw.project_writer import build_gxw_project
    from gxw.structured_pou import parse_structured_pou as parse_for_cli

    with zipfile.ZipFile(root / "research/evidence/gxw-20260910.zip") as archive:
        baseline = archive.read(source + ".gxw")
        expected = archive.read(target + ".gxw")
    assert hashlib.sha256(baseline).hexdigest() == FIXTURE[source]["gxw_sha256"]
    template = parse_for_cli(payload("s"), logical_name="1.Program.pou").wires[0]
    model = parse_for_cli(payload(source), logical_name="1.Program.pou")
    result = build_gxw_project(baseline, make_control(mode, model, template))
    assert result.data == expected
