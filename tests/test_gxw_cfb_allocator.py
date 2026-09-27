import io
from pathlib import Path
import struct
import zipfile

import pytest

from src.gxw.cfb_allocator import resize_cfb_stream
from src.gxw.container import CompoundFile, DIFSECT, FATSECT, FREESECT, ENDOFCHAIN
from src.gxw.container_writer import replace_project_stream, validate_cfb_streams
from src.gxw.models import GXWFormatError
from src.gxw.project_metadata import logical_mapping
from tests.test_gxw_project_writer import cfb_fixture
from tests.test_gxw_container_writer import _directory_entry, _header


def verify(raw, expected):
    assert validate_cfb_streams(raw) == expected


def verify_external(raw, expected):
    # Independent reader checks directory traversal and actual stream contents.
    olefile = pytest.importorskip("olefile")
    with olefile.OleFileIO(io.BytesIO(raw)) as external:
        assert {path[0]: external.openstream(path).read() for path in external.listdir()} == expected


def partial_native_container():
    archive = Path(__file__).resolve().parents[1] / "research/evidence/gxw-partial-sector-20260927.zip"
    with zipfile.ZipFile(archive) as z:
        outer = CompoundFile(z.read("native/native-saved.gxw"))
    name = logical_mapping(outer.read_stream("projectdatalist.xml"))["CGTable.dat"]
    return outer.read_stream("_hdb"), name


@pytest.mark.parametrize("size", [6767, 7000, 8000, 80])
@pytest.mark.parametrize("writer", ["dispatch", "allocator"])
def test_native_partial_final_sector_can_be_edited_after_payload_preflight(size, writer):
    raw, name = partial_native_container()
    expected = validate_cfb_streams(raw)
    assert len(raw) % 512 == 111
    if writer == "dispatch":
        assert replace_project_stream(raw, name, expected[name])[0] == raw
    else:
        assert resize_cfb_stream(raw, name, expected[name]) == raw
    expected[name] = bytes(i % 251 for i in range(size))
    if writer == "dispatch":
        result, mode = replace_project_stream(raw, name, expected[name])
        assert mode.startswith("pad_final_sector+")
    else:
        result = resize_cfb_stream(raw, name, expected[name])
    assert len(result) % 512 == 0
    verify(result, expected)
    verify_external(result, expected)


@pytest.mark.parametrize("writer", ["dispatch", "allocator"])
def test_padding_never_repairs_truncated_declared_native_payload(writer):
    raw, name = partial_native_container()
    with pytest.raises(GXWFormatError, match="outside the file"):
        if writer == "dispatch":
            replace_project_stream(raw[:-1], name, b"replacement")
        else:
            resize_cfb_stream(raw[:-1], name, b"replacement")


def test_partial_root_ministream_tail_can_be_extended_but_not_repaired():
    expected = {"edit": b"A" * 64, "other": b"unknown payload"}
    raw = cfb_fixture(expected)
    expected["edit"] = b"C" * 1300
    raw = resize_cfb_stream(raw, "edit", expected["edit"])
    cfb = CompoundFile(raw)
    chain = cfb._walk_chain(cfb.root_entry.start_sector, cfb._fat)
    assert chain[-1] == len(raw) // 512 - 2
    padding = (-cfb.root_entry.stream_size) % 512
    assert padding
    partial = raw[:-padding]
    verify(partial, expected)
    verify_external(partial, expected)
    with pytest.raises(GXWFormatError, match="outside the file"):
        replace_project_stream(partial[:-1], "edit", b"different")
    expected["edit"] = b"D" * 2200
    result, mode = replace_project_stream(partial, "edit", expected["edit"])
    assert mode.startswith("pad_final_sector+")
    verify(result, expected)
    verify_external(result, expected)


def test_metadata_sector_truncation_is_not_padding():
    raw = cfb_fixture({"edit": b"A" * 4096})
    cfb = CompoundFile(raw)
    assert cfb._fat_sector_ids[-1] == len(raw) // 512 - 2
    with pytest.raises(GXWFormatError, match="outside the file"):
        replace_project_stream(raw[:-1], "edit", b"new")


def test_mini_regular_empty_roundtrips_preserve_unrelated_data_and_directory_fields():
    expected = {"edit": b"A" * 64, "other": b"unknown payload"}
    raw = bytearray(cfb_fixture(expected))
    # Unused v3 high size DWORD and opaque directory CLSID/timestamps survive.
    raw[512 + 128 + 80:512 + 128 + 116] = b"K" * 36
    raw[512 + 128 + 124:512 + 256] = b"HIGH"
    opaque = bytes(raw[512 + 128 + 80:512 + 128 + 116])
    for size in (4095, 4096, 65537, 4095, 1, 0, 80):
        expected["edit"] = bytes(i % 251 for i in range(size))
        raw = resize_cfb_stream(bytes(raw), "edit", expected["edit"])
        verify(raw, expected)
        assert raw[512 + 128 + 80:512 + 128 + 116] == opaque
        assert raw[512 + 128 + 124:512 + 256] == b"HIGH"


def test_minifat_grows_and_root_preserves_other_mini_streams():
    expected = {"edit": b"A" * 2600, "second": b"B" * 2600, "third": b"C" * 2600}
    before = cfb_fixture(expected)
    expected["edit"] = b"D" * 4095
    result = resize_cfb_stream(before, "edit", expected["edit"])
    verify(result, expected)
    assert CompoundFile(before).num_minifat_sectors == 1
    assert CompoundFile(result).num_minifat_sectors == 2


def test_ministream_and_minifat_created_for_previously_regular_only_file():
    expected = {"edit": b"A" * 4096, "other": b"B" * 4096}
    before = cfb_fixture(expected)
    assert CompoundFile(before).num_minifat_sectors == 0
    expected["edit"] = b"hello"
    result = resize_cfb_stream(before, "edit", expected["edit"])
    verify(result, expected)
    assert CompoundFile(result).num_minifat_sectors == 1


@pytest.mark.parametrize("size,min_difat", [(70001, 0), (8 * 1024 * 1024 + 13, 1), (16 * 1024 * 1024 + 7, 2)])
def test_fat_and_chained_difat_grow_at_real_table_boundaries(size, min_difat):
    expected = {"edit": b"A" * 4096, "other": b"keep"}
    before = cfb_fixture(expected)
    expected["edit"] = (bytes(range(251)) * ((size + 250) // 251))[:size]
    result = resize_cfb_stream(before, "edit", expected["edit"])
    verify(result, expected)
    cfb = CompoundFile(result)
    assert cfb.num_fat_sectors > 1 and cfb.num_difat_sectors == min_difat
    assert len(cfb._fat) >= len(result) // 512 - 1
    assert all(cfb._fat[sid] == FATSECT for sid in cfb._fat_sector_ids)
    sid = cfb.first_difat_sector
    for _ in range(cfb.num_difat_sectors):
        assert cfb._fat[sid] == DIFSECT
        sid = struct.unpack_from("<I", cfb._sector(sid), 508)[0]
    # Subsequent edits must work with preexisting multi-sector DIFAT as well.
    expected["other"] = b"new smaller-stream value" * 50
    verify(resize_cfb_stream(result, "other", expected["other"]), expected)


def test_empty_stream_dispatch_can_reuse_existing_root():
    expected = {"edit": b"A" * 200, "other": b"keep"}
    raw = cfb_fixture(expected)
    for payload in (b"", b"again", b"D" * 4096, b""):
        expected["edit"] = payload
        raw, _ = replace_project_stream(raw, "edit", payload)
        verify(raw, expected)


def test_independent_reader_large_allocation_and_cutoff_transitions():
    expected = {"edit": b"A", "other": b"keep"}
    raw = cfb_fixture(expected)
    for size in (16 * 1024 * 1024 + 7, 4095, 0, 70001):
        expected["edit"] = b"X" * size
        raw, _ = replace_project_stream(raw, "edit", expected["edit"])
        verify(raw, expected)
        verify_external(raw, expected)


def test_version_four_sector_geometry_and_fat_growth():
    header = bytearray(_header(first_directory_sector=0, fat_sector=3,
                               first_minifat_sector=ENDOFCHAIN, num_minifat_sectors=0).ljust(4096, b"\0"))
    struct.pack_into("<H", header, 26, 4)
    struct.pack_into("<H", header, 30, 12)
    struct.pack_into("<I", header, 40, 1)
    directory = bytearray(4096)
    directory[:128] = _directory_entry("Root Entry", 5, child=1, start_sector=ENDOFCHAIN, stream_size=0)
    directory[128:256] = _directory_entry("edit", 2, right=2, start_sector=1, stream_size=4096)
    directory[256:384] = _directory_entry("other", 2, start_sector=2, stream_size=4096)
    fat = [ENDOFCHAIN, ENDOFCHAIN, ENDOFCHAIN, FATSECT] + [FREESECT] * (1024 - 4)
    raw = bytes(header + directory + b"A" * 4096 + b"B" * 4096 + struct.pack("<1024I", *fat))
    expected = {"edit": b"A" * 4096, "other": b"B" * 4096}
    for size in (4095, 4 * 1024 * 1024 + 7, 0, 64):
        expected["edit"] = b"C" * size
        raw, _ = replace_project_stream(raw, "edit", expected["edit"])
        verify(raw, expected)
        assert CompoundFile(raw).sector_size == 4096
        assert struct.unpack_from("<I", raw, 40)[0] == 1
    assert CompoundFile(raw).num_fat_sectors > 1
