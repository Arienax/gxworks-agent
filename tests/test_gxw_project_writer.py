import base64
from dataclasses import replace
import json
from pathlib import Path
import struct

import pytest

from src.gxw.container import CompoundFile, ENDOFCHAIN, FATSECT, FREESECT
from src.gxw.container_writer import validate_cfb_streams
from src.gxw.experiment import compare_programs, run_regression
from src.gxw.models import GXWFormatError, NodeKind
from src.gxw.project_metadata import current_rows, logical_mapping, md5_base64, synchronize_history
from src.gxw.project_writer import build_gxw_project, write_gxw_project
from src.gxw.structured_pou import parse_structured_pou
from src.gxw.structured_pou_writer import replace_node_symbol, serialize_structured_pou
from src.gxw.structured_writer import build_series_program, insert_parallel_contact, structured_from_ladder
from tests.test_gxw_container_writer import _directory_entry, _header
from tests.test_gxw_structured_writer import _simple_x1_y1_baseline_from_sample_51


def cfb_fixture(streams):
    """Synthetic CFB container; real extracted POU records remain unchanged."""
    sectors = [bytearray(512)]
    fat = [FREESECT] * 128
    fat[0] = ENDOFCHAIN

    def allocate(data):
        if not data:
            return ENDOFCHAIN
        start = len(sectors)
        for pos in range(0, len(data), 512):
            sid = len(sectors)
            sectors.append(bytearray(data[pos:pos + 512].ljust(512, b"\0")))
            fat[sid] = sid + 1 if pos + 512 < len(data) else ENDOFCHAIN
        return start

    mini, minifat, entries = bytearray(), [FREESECT] * 128, []
    for name, payload in streams.items():
        if len(payload) < 4096:
            start = len(mini) // 64 if payload else ENDOFCHAIN
            count = (len(payload) + 63) // 64
            for sid in range(start, start + count):
                minifat[sid] = sid + 1 if sid + 1 < start + count else ENDOFCHAIN
            mini.extend(payload.ljust(count * 64, b"\0"))
        else:
            start = allocate(payload)
        entries.append((name, start, len(payload)))
    root_start = allocate(mini)
    minifat_start = allocate(struct.pack("<128I", *minifat)) if mini else ENDOFCHAIN
    fat_sector = len(sectors)
    fat[fat_sector] = FATSECT
    sectors.append(bytearray(struct.pack("<128I", *fat)))
    sectors[0][:128] = _directory_entry("Root Entry", 5, child=1, start_sector=root_start, stream_size=len(mini))
    for index, (name, start, size) in enumerate(entries, 1):
        sectors[0][index * 128:(index + 1) * 128] = _directory_entry(
            name, 2, right=index + 1 if index < len(entries) else FREESECT,
            start_sector=start, stream_size=size)
    # Enough length for this CFB to be used as a regular outer _hdb stream.
    while len(sectors) < 7:
        sectors.append(bytearray(512))
    return _header(first_directory_sector=0, fat_sector=fat_sector,
                   first_minifat_sector=minifat_start, num_minifat_sectors=int(bool(mini))) + b"".join(sectors)


def history_xml(logical, stream, payload, *, size=None, digest=None, prefix=""):
    return (f'<DSHISTORY {prefix}><D_History><iID>987</iID><iProjectdataID>{stream}</iProjectdataID>'
            f'<szProjectdataName>{logical}</szProjectdataName><iFileSize> {len(payload) if size is None else size} </iFileSize>'
            f'<szMD5val>{md5_base64(payload) if digest is None else digest}</szMD5val>'
            '<Unknown attribute="保留&gt;值">untouched</Unknown></D_History></DSHISTORY>').encode()


def project_fixture():
    program, _ = _simple_x1_y1_baseline_from_sample_51()
    nested = cfb_fixture({"83": program.raw, "opaque": b"KEEP THIS UNKNOWN PAYLOAD"})
    mapping = b'<DSPROJECTDATA><D_Projectdata><iID>83</iID><szName>1.Program.pou</szName><bScrapFlag>false</bScrapFlag></D_Projectdata></DSPROJECTDATA>'
    return cfb_fixture({"_hdb": nested, "projectdatalist.xml": mapping,
                        "history.xml": history_xml("1.Program.pou", "83", program.raw)}), program


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16le", "utf-16be"])
def test_history_exact_size_digest_and_unknown_lexical_bytes(encoding):
    old, new = b"old", b"a different length"
    text = history_xml("p.Program.pou", "83", old).decode()
    raw = text.encode(encoding)
    if encoding == "utf-16le":
        raw = b"\xff\xfe" + raw
    elif encoding == "utf-16be":
        raw = b"\xfe\xff" + raw
    result, changes, preserved = synchronize_history(raw, {"p.Program.pou": ("83", old, new)})
    expected = raw.replace(b" 3 ".decode().encode(encoding), f" {len(new)} ".encode(encoding))
    expected = expected.replace(md5_base64(old).encode(encoding), md5_base64(new).encode(encoding))
    assert result == expected and preserved == []
    assert {c["field"] for c in changes} == {"iFileSize", "szMD5val"}


def test_unknown_digest_preserved_size_repaired_and_diffgram_before_untouched():
    old, new = b"old", b"new payload"
    current = history_xml("p.Program.pou", "83", old, size=1, digest="unknown")
    raw = b'<diffgr:diffgram xmlns:diffgr="urn:test">' + current + b'<diffgr:before>' + current + b'</diffgr:before></diffgr:diffgram>'
    result, changes, preserved = synchronize_history(raw, {"p.Program.pou": ("83", old, new)})
    assert result.count(b" 1 ") == 1 and result.count(b"unknown") == 2
    assert len(changes) == 1 and preserved[0]["status"] == "unknown"


@pytest.mark.parametrize("damage", ["duplicate", "missing", "name", "id", "dtd"])
def test_history_invalid_mapping_fails_closed(damage):
    raw = history_xml("p.Program.pou", "83", b"old")
    if damage == "duplicate":
        row = raw[raw.index(b"<D_History>"):raw.index(b"</D_History>") + len(b"</D_History>")]
        raw = raw.replace(b"</DSHISTORY>", row + b"</DSHISTORY>")
    elif damage == "missing":
        raw = raw.replace(b"iFileSize", b"unknownSize")
    elif damage == "name":
        raw = raw.replace(b"p.Program.pou", b"other.Program.pou")
    elif damage == "id":
        raw = raw.replace(b">83<", b">82<")
    else:
        raw = b'<!DOCTYPE DSHISTORY [<!ENTITY x "bad">]>' + raw
    with pytest.raises(GXWFormatError):
        synchronize_history(raw, {"p.Program.pou": ("83", b"old", b"new")})


def test_project_noop_is_byte_identical():
    raw, model = project_fixture()
    result = build_gxw_project(raw, model)
    assert result.data == raw
    assert result.report["metadata_changes"] == []


def test_project_growth_preserves_other_streams_and_reports_real_offsets():
    raw, model = project_fixture()
    edited = build_series_program(model, ["X0", "X100"], "Y0")
    result = build_gxw_project(raw, edited)
    outer = CompoundFile(result.data)
    nested = CompoundFile(outer.read_stream("_hdb"))
    actual = nested.read_stream("83")
    assert nested.read_stream("opaque") == b"KEEP THIS UNKNOWN PAYLOAD"
    assert outer.read_stream("projectdatalist.xml") == CompoundFile(raw).read_stream("projectdatalist.xml")
    parsed = parse_structured_pou(actual)
    assert [n.symbol for n in parsed.nodes] == ["X0", "X100", "Y0"]
    rows, _ = current_rows(outer.read_stream("history.xml"), "DSHISTORY", "D_History")
    assert rows[0].fields()["iFileSize"].text.strip() == str(len(actual))
    assert rows[0].fields()["szMD5val"].text == md5_base64(actual)
    changes = result.report["objects"][0]["binary_changes"]
    replayed = model.raw
    for change in reversed(changes):
        start = change["old_offset"]
        assert replayed[start:start + change["old_length"]].hex() == change["before_hex"]
        replayed = replayed[:start] + bytes.fromhex(change["after_hex"]) + replayed[start + change["old_length"]:]
    assert replayed == actual


def test_stale_model_and_invalid_output_do_not_write(tmp_path):
    raw, model = project_fixture()
    source, output = tmp_path / "base.gxw", tmp_path / "new.gxw"
    source.write_bytes(raw)
    foreign = replace(model, raw=serialize_structured_pou(replace_node_symbol(model, "X1", "X2")))
    with pytest.raises(GXWFormatError, match="stale"):
        write_gxw_project(source, output, foreign)
    assert not output.exists() and source.read_bytes() == raw
    with pytest.raises(GXWFormatError, match="new file"):
        write_gxw_project(source, source, model)
    output.write_bytes(b"existing")
    with pytest.raises(GXWFormatError):
        write_gxw_project(source, output, model)
    assert output.read_bytes() == b"existing"


def test_repeated_series_symbols_and_parallel_nets():
    _, model = project_fixture()
    series = parse_structured_pou(serialize_structured_pou(build_series_program(model, ["X0", "X0", "X2"], "Y0")))
    assert [n.symbol for n in series.nodes] == ["X0", "X0", "X2", "Y0"]
    parallel = parse_structured_pou(serialize_structured_pou(insert_parallel_contact(model, "X2")))
    from src.gxw.semantic import build_semantic_model
    semantic = build_semantic_model(parallel)
    a, b = semantic.contacts
    assert a.execution_in.net_index == b.execution_in.net_index
    assert a.execution_out.net_index == b.execution_out.net_index == semantic.coils[0].execution_in.net_index
    assert a.execution_in.net_index != a.execution_out.net_index


def test_roundtrip_detects_lost_wire_and_ignores_offset_noise():
    _, model = project_fixture()
    series = parse_structured_pou(serialize_structured_pou(build_series_program(model, ["X0", "X1"], "Y0")))
    shifted = replace(series, nodes=tuple(replace(n, offset=n.offset + 1000) for n in series.nodes),
                       wires=tuple(replace(w, offset=w.offset + 1000) for w in series.wires))
    assert all(compare_programs(series, shifted)["checks"].values())
    broken = replace(series, wires=series.wires[:-1])
    checks = compare_programs(series, broken)["checks"]
    assert checks["devices"] and not checks["topology"] and not checks["wires"] and not checks["semantic_graph"]


def test_existing_ladder_json_and_ir_generate_same_object_model():
    _, template = project_fixture()
    model = json.loads(Path("research/models/series.json").read_text())
    from plc.ir import build_plc_ir
    plain = structured_from_ladder(template, model)
    ir = structured_from_ladder(template, build_plc_ir(model))
    assert serialize_structured_pou(plain) == serialize_structured_pou(ir)
    assert [n.symbol for n in plain.nodes] == ["X0", "X1", "Y0"]


def test_regression_suite_a_to_f_and_original_hash(tmp_path):
    raw, _ = project_fixture()
    baseline = tmp_path / "base.gxw"
    baseline.write_bytes(raw)
    report = run_regression(baseline, tmp_path / "projects", tmp_path / "results")
    assert list(report["variants"]) == list("ABCDEF")
    assert (tmp_path / "projects/A.gxw").read_bytes() == raw
    for key in "ABCDEF":
        assert (tmp_path / f"results/{key}.json").is_file()
    for key, delta in [("B", 0), ("C", 1), ("D", 1), ("E", 2), ("F", 3)]:
        p = report["variants"][key]["comparison"]["programs"]["1.Program.pou"]
        assert p["counts_after"]["records"] - p["counts_before"]["records"] == delta
    assert baseline.read_bytes() == raw


@pytest.mark.parametrize("count", [1, 3, 7, 13])
def test_sizes_follow_serialized_program_not_experiment_constants(count):
    raw, model = project_fixture()
    result = build_gxw_project(raw, build_series_program(model, [f"M{i}" for i in range(count)], "Y0"))
    cfb = CompoundFile(result.data)
    pou = CompoundFile(cfb.read_stream("_hdb")).read_stream("83")
    rows, _ = current_rows(cfb.read_stream("history.xml"), "DSHISTORY", "D_History")
    assert int(rows[0].fields()["iFileSize"].text) == len(pou)
    parsed = parse_structured_pou(pou)
    assert len(parsed.nodes) == count + 1 and len(parsed.wires) == count + 2


def test_series_insertion_rejects_hidden_tap_on_wire_interior():
    from src.gxw.structured_pou_writer import insert_series_contact_after
    from src.gxw.models import Point
    _, model = project_fixture()
    contact = next(n for n in model.nodes if n.kind == NodeKind.CONTACT)
    output = contact.port_point(1)
    wire = next(w for w in model.wires if w.start == output or w.end == output)
    x = (wire.start.x + wire.end.x) // 2
    tap = replace(wire, offset=max(r.offset for r in model.iter_records()) + 1,
                  start=Point(x, output.y), end=Point(x, output.y + 2))
    with pytest.raises(GXWFormatError, match="untapped"):
        insert_series_contact_after(replace(model, wires=(*model.wires, tap)), contact.symbol, "X2")


def test_cfb_preflight_rejects_aliased_ministreams():
    raw = cfb_fixture({"one": b"A" * 64, "two": b"B" * 64})
    damaged = bytearray(raw)
    # Both stream slots now claim the first mini-sector.
    struct.pack_into("<I", damaged, 512 + 2 * 128 + 116, 0)
    with pytest.raises(GXWFormatError, match="overlapping"):
        validate_cfb_streams(bytes(damaged))


def test_multiple_programs_use_distinct_mapping_and_history_ids():
    _, model = project_fixture()
    nested = cfb_fixture({"83": model.raw, "917": model.raw, "opaque": b"preserve"})
    mapping = b'<DSPROJECTDATA>' + b''.join(
        f'<D_Projectdata><iID>{sid}</iID><szName>{name}.Program.pou</szName></D_Projectdata>'.encode()
        for sid, name in [("83", "a"), ("917", "b")]) + b'</DSPROJECTDATA>'
    h = b'<DSHISTORY>' + b''.join(history_xml(f'{name}.Program.pou', sid, model.raw)
        .split(b'>', 1)[1].rsplit(b'</DSHISTORY>', 1)[0]
        for sid, name in [("83", "a"), ("917", "b")]) + b'</DSHISTORY>'
    raw = cfb_fixture({"_hdb": nested, "projectdatalist.xml": mapping, "history.xml": h})
    a = replace(build_series_program(model, ["X0", "X1"], "Y0"), logical_name="a.Program.pou")
    b = replace(replace_node_symbol(model, "X1", "M100"), logical_name="b.Program.pou")
    result = build_gxw_project(raw, {a.logical_name: a, b.logical_name: b})
    container = CompoundFile(result.data)
    payloads = validate_cfb_streams(container.read_stream("_hdb"))
    assert payloads["opaque"] == b"preserve"
    rows, _ = current_rows(container.read_stream("history.xml"), "DSHISTORY", "D_History")
    for row in rows:
        fields = row.fields()
        assert int(fields["iFileSize"].text) == len(payloads[fields["iProjectdataID"].text])


@pytest.mark.parametrize("tool", ["gxw_patch_symbol.py", "gxw_patch_symbol_same_size.py", "gxw_insert_series_contact.py"])
def test_legacy_commands_sync_history_and_never_overwrite(tmp_path, tool):
    import subprocess
    import sys
    raw, _ = project_fixture()
    source, output = tmp_path / "base.gxw", tmp_path / "out.gxw"
    source.write_bytes(raw)
    args = [sys.executable, str(Path("tools") / tool), str(source), "X1", "X2", "-o", str(output)]
    subprocess.run(args, check=True, capture_output=True)
    report = json.loads(output.with_suffix(".write.json").read_text(encoding="utf-8"))
    assert any(c["field"] == "szMD5val" for c in report["metadata_changes"])
    assert source.read_bytes() == raw
    saved = output.read_bytes()
    assert subprocess.run(args, capture_output=True).returncode != 0
    assert output.read_bytes() == saved
