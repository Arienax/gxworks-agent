"""Native CallTree component replay and conflicting cached reference indexes."""
import importlib
import json
from pathlib import Path
import struct
import zipfile

import pytest

from gxw.compiler_call_tree import parse_compiler_call_tree
from gxw.lossless import sha256
from gxw.models import GXWFormatError

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "research/evidence/gxw-calltree-20260920.zip"


def test_call_tree_primitive_reads_and_whole_native_component_replays(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "research"))
    oracle = importlib.import_module("native_gxw_archive")
    manifest = json.loads((ROOT / "research/results/token-20260919/calltree-manifest.json").read_text())
    assert sha256(ARCHIVE.read_bytes()) == manifest["archive_sha256"]
    with zipfile.ZipFile(ARCHIVE) as z:
        for member, expected in manifest["files"].items():
            assert sha256(z.read(member)) == expected["sha256"]
        cases = json.loads(z.read("calltree-scan/manifest.json"))
        read_count = absent_resource = 0
        for digest in cases:
            raw = z.read(f"calltree-scan/{digest}.bin")
            tree = parse_compiler_call_tree(raw)
            assert tree.reconstruct() == raw
            absent_resource += tree.resource_tree is None
            observations = [json.loads(s) for s in z.read(f"native-calltree-corpus/{digest}/native-reads.jsonl").splitlines()]
            plan = oracle.call_tree_plan(raw)
            assert len(plan) == len(observations)
            assert [r["index"] for r in observations] == list(range(len(plan)))
            for expected, observed in zip(plan, observations):
                assert all(expected[k] == observed[k] for k in ("operation", "end_offset", "value_base64"))
            assert observations[-1]["end_offset"] == len(raw)
            read_count += len(plan)
            prefix = f"native-calltree-replay-corpus/{digest}/"
            assert z.read(prefix + "after-load.dat") == raw
            saved = z.read(prefix + "native-save.dat")
            result = oracle.compare_call_tree_replay(raw, saved)
            assert not result["byte_identical"]
            assert result["structurally_identical"] and result["opaque_tail_preserved"]
        assert len(cases) == 14 and read_count == 4078 and absent_resource == 2


def test_call_tree_keeps_disagreeing_forward_and_reverse_names():
    digest = "4069c7b618031a81e7a8155b835c2268da66af330c86e9362b6dab1ca787dd98"
    with zipfile.ZipFile(ARCHIVE) as z:
        raw = z.read(f"calltree-scan/{digest}.bin")
        saved = z.read(f"native-calltree-replay-corpus/{digest}/native-save.dat")
    for value in (raw, saved):
        tree = parse_compiler_call_tree(value)
        forward = {(e.source_key, e.target_key) for e in tree.references(1)}
        reverse = {(e.source_key, e.target_key) for e in tree.references(2)}
        assert len(forward & reverse) == 7
        assert forward - reverse == {("23, , , 1", f"28, , TON, TON_{suffix}") for suffix in ("A", "B")}
        assert reverse - forward == {("23, , , 1", f"28, , TON, TIMER_{suffix}") for suffix in ("A", "B")}
        # Unlinked compiler fragments can still have stored source references.
        assert ("23, , , 1", "28, , CTU, COUNTER_A") in forward


def test_call_tree_native_save_is_not_an_opaque_preserving_writer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "research"))
    compare = importlib.import_module("native_gxw_archive").compare_call_tree_replay
    with zipfile.ZipFile(ARCHIVE) as z:
        raw = z.read("native-calltree-opaque-control/input.bin")
        saved = z.read("native-calltree-opaque-control/native-save.dat")
    tree = parse_compiler_call_tree(raw)
    assert tree.opaque_tail == b"OPAQUE-CALLTREE-EXTENSION\0\xff\x80"
    assert tree.reconstruct() == raw
    result = compare(raw, saved)
    assert result["map_content_equal_ignoring_iteration_order"] == [True, True, True]
    assert result["resource_tree_equal"]
    assert not result["opaque_tail_preserved"] and not result["structurally_identical"]
    modified = bytearray(saved)
    first = parse_compiler_call_tree(saved).maps[0][0]
    struct.pack_into("<I", modified, first.offset, first.flags ^ 1)
    assert not compare(saved, modified)["structurally_identical"]


def test_archive_length_encodings_match_native_and_remain_lossless(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "research"))
    plan = importlib.import_module("native_gxw_archive").call_tree_plan
    with zipfile.ZipFile(ARCHIVE) as z:
        raw = z.read("native-archive-string-controls/input.bin")
        observations = [json.loads(s) for s in z.read("native-archive-string-controls/native-reads.jsonl").splitlines()]
    tree = parse_compiler_call_tree(raw)
    assert tree.reconstruct() == raw
    refs = tree.maps[0][0].references
    assert len(refs) == 8 and sum(s.character_width == 2 for s in refs) == 3
    assert [s.text() for s in refs] == ["ABCDE", "A" * 255, "ABCDE", "ABCDE", "ABCDE", "ABCDE", "ABCDE", "ABCDE"]
    for expected, observed in zip(plan(raw), observations):
        assert all(expected[k] == observed[k] for k in ("operation", "end_offset", "value_base64"))
    for malformed in (raw[:-1], b"\xff\xff\xff\xff" + raw[4:]):
        with pytest.raises(GXWFormatError):
            parse_compiler_call_tree(malformed)
