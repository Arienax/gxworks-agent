"""Native CSV declaration oracles, preservation, and transactional project edits."""
from dataclasses import replace
import json
from pathlib import Path
import struct
import zipfile

import pytest

from src.gxw.container import CompoundFile
from src.gxw.declarations import edit_declarations, parse_declarations, serialize_declarations, serialize_label
from src.gxw.models import GXWFormatError
from src.gxw.project_resolver import GXWProjectResolver
from src.gxw.project_writer import build_gxw_project
from src.gxw.structured_pou import parse_structured_pou
from src.gxw.structured_pou_writer import replace_node_symbol


ROOT = Path(__file__).resolve().parents[1]
NATIVE = json.loads((ROOT / "tests/fixtures/gxw_declarations_20260910.json").read_text())
SIZED_STRINGS = json.loads((ROOT / "tests/fixtures/gxw_sized_string_declarations.json").read_text())


def document(case="dtypes", logical="1.Labels.lh"):
    return parse_declarations(bytes.fromhex(NATIVE[case][logical]), logical_name=logical)


def baseline(case):
    with zipfile.ZipFile(ROOT / "research/evidence/gxw-20260910.zip") as archive:
        return archive.read(case + ".gxw")


@pytest.mark.parametrize("case", list(NATIVE))
@pytest.mark.parametrize("logical", ["1.Labels.lh", "Global1.gh"])
def test_native_tables_are_lossless(case, logical):
    doc = document(case, logical)
    assert serialize_declarations(doc) == doc.raw


def test_all_sixteen_native_type_records_can_be_written_from_empty_table():
    native = document()
    edits = [{"name": r.name, "data_type": r.data_type, "comment": r.comment,
              **({"kind": "function_block"} if r.type_code == 15 else {})} for r in native.rows]
    generated = edit_declarations(document("d0"), upserts=edits)
    assert [serialize_label(r) for r in generated.rows] == [r.raw for r in native.rows]
    assert generated.header == document("d0").header
    assert generated.trailer == document("d0").trailer


def test_constant_class_and_value_match_native_record():
    generated = edit_declarations(document(), upserts=[{
        "name": "item_1", "class_name": "VAR_CONSTANT", "initial_value": "TRUE"}])
    assert [serialize_label(r) for r in generated.rows] == [r.raw for r in document("dconst").rows]


@pytest.mark.parametrize("case", SIZED_STRINGS["cases"], ids=lambda c: c["name"])
def test_sized_string_edits_match_independently_saved_native_rows(case):
    empty = document("d0")
    header = bytearray(empty.header)
    struct.pack_into("<I", header, empty.count_offset, 1)
    source = parse_declarations(bytes(header) + bytes.fromhex(case["old_row_hex"]) + empty.trailer,
                                logical_name=empty.logical_name)
    changed = edit_declarations(source, upserts=[{"name": case["target"], "data_type": case["data_type"]}])
    assert serialize_label(changed.rows[0]) == bytes.fromhex(case["native_row_hex"])
    assert changed.header == source.header
    assert changed.trailer == source.trailer
    assert serialize_declarations(source) == source.raw


def test_native_global_variables_arrays_function_blocks_and_constant():
    native = document("dglobal", "Global1.gh")
    edits = [{"name": r.name, "data_type": r.data_type, "comment": r.comment,
              **({"kind": "function_block"} if r.type_code == 15 else {})} for r in native.rows[3:-1]]
    edits.append({"name": "global_constant", "data_type": "INT", "class_name": "VAR_GLOBAL_CONSTANT",
                  "initial_value": "42", "comment": "native constant"})
    generated = edit_declarations(document("d0", "Global1.gh"), upserts=edits)
    assert [serialize_label(r) for r in generated.rows] == [r.raw for r in native.rows]


def test_unknown_type_and_opaque_fields_survive_comment_and_name_edits():
    source = document()
    odd = replace(source.rows[0], data_type="VENDOR TYPE?", type_code=991, array_marker=7,
                  unknown_u32=345, unknown_text="opaque", type_reference="vendor", record_id=88)
    source = replace(source, rows=(odd,), trailer=b"opaque trailing bytes")
    edited = edit_declarations(source, upserts=[{"name": odd.name, "comment": "new"}],
                               renames={})
    assert edited.rows[0] == replace(odd, comment="new")
    assert edited.trailer == source.trailer
    renamed = edit_declarations(edited, renames={odd.name: "renamed"})
    assert renamed.rows[0] == replace(odd, comment="new", name="renamed")


@pytest.mark.parametrize("edits", [
    {"renames": {"item_1": "item_2"}}, {"renames": {"absent": "new"}},
    {"upserts": [{"name": "bad name", "data_type": "BOOL"}]},
    {"upserts": [{"name": "new", "data_type": "NotAKnownScalar"}]},
    {"upserts": [{"name": "new", "data_type": "ARRAY [3..0] OF BOOL"}]},
    {"upserts": [{"name": "new", "data_type": "BOOL", "mystery": 1}]},
] + [{"upserts": [{"name": "new", "data_type": dtype}]} for dtype in (
    "STRING[0]", "STRING[256]", "STRING[-1]", "string[20]", "WSTRING[20]",
    "STRING[+20]", "ARRAY [0..1] OF STRING[256]",
)])
def test_invalid_edits_fail_without_mutating_source(edits):
    source = document()
    with pytest.raises(GXWFormatError):
        edit_declarations(source, **edits)
    assert serialize_declarations(source) == source.raw


def test_program_and_missing_fb_declarations_written_together():
    raw = baseline("b0")
    resolver = GXWProjectResolver(CompoundFile(raw))
    program = parse_structured_pou(resolver.read_logical_file("1.Program.pou"), logical_name="1.Program.pou")
    program = replace_node_symbol(program, "timer_a", "TON_LONG_INSTANCE_A")
    program = replace_node_symbol(program, "timer_b", "TON_LONG_INSTANCE_B")
    result = build_gxw_project(raw, program, sync_fb_declarations=True)
    after = GXWProjectResolver(CompoundFile(result.data))
    labels = parse_declarations(after.read_logical_file("1.Labels.lh"), logical_name="1.Labels.lh")
    assert [(r.name, r.type_reference) for r in labels.rows[-2:]] == [
        ("TON_LONG_INSTANCE_A", "TON"), ("TON_LONG_INSTANCE_B", "TON")]
    assert after.read_logical_file("Global1.gh") == resolver.read_logical_file("Global1.gh")
    assert {c["object"] for c in result.report["objects"]} == {"1.Program.pou", "1.Labels.lh"}
    assert build_gxw_project(result.data,
        parse_structured_pou(after.read_logical_file("1.Program.pou"), logical_name="1.Program.pou"),
        sync_fb_declarations=True).data == result.data


def test_declaration_only_write_grows_both_nested_layers():
    raw = baseline("fn")
    resolver = GXWProjectResolver(CompoundFile(raw))
    source = parse_declarations(resolver.read_logical_file("1.Labels.lh"), logical_name="1.Labels.lh")
    edited = edit_declarations(source, upserts=[{"name": f"value_{i}", "data_type": "BOOL",
        "comment": "Native layout declaration"} for i in range(2000)])
    result = build_gxw_project(raw, declarations={source.logical_name: edited})
    after = GXWProjectResolver(CompoundFile(result.data))
    assert len(parse_declarations(after.read_logical_file(source.logical_name), logical_name=source.logical_name).rows) == 2000
    assert after.read_logical_file("1.Program.pou") == resolver.read_logical_file("1.Program.pou")
    assert len(result.data) > len(raw)
    assert {x["layer"] for x in result.report["allocations"]} == {"outer", "nested"}
    with pytest.raises(GXWFormatError, match="stale"):
        build_gxw_project(result.data, declarations={source.logical_name: edited})
