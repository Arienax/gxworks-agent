import json
import re
import sqlite3
from pathlib import Path

import pytest

from shared.paths import resource_path


def _database() -> Path:
    return Path(resource_path("knowledge/fx3u_knowledge.sqlite"))


def _flags(opcode: str, manual_id: str) -> list[str]:
    with sqlite3.connect(_database()) as connection:
        row = connection.execute(
            "SELECT completion_flags_json FROM instructions WHERE opcode_norm=? AND manual_id=?",
            (opcode.casefold(), manual_id),
        ).fetchone()
    assert row is not None
    return json.loads(row[0] or "[]")


def test_device_ranges_never_cross_device_families():
    pattern = re.compile(
        r"^(ER|SM|SD|TS|TC|CS|CC|[XYMSTCDRVZPIN])(\d+)-(ER|SM|SD|TS|TC|CS|CC|[XYMSTCDRVZPIN])(\d+)$",
        re.I,
    )
    with sqlite3.connect(_database()) as connection:
        ranges = connection.execute(
            "SELECT device FROM device_records WHERE record_type='device_range'"
        ).fetchall()
    bad = []
    for (value,) in ranges:
        match = pattern.fullmatch(str(value or ""))
        if not match or match.group(1).upper() != match.group(3).upper():
            bad.append(value)
    assert bad == []


def test_known_operand_placeholders_are_not_indexed_as_devices_in_same_instruction_chunk():
    cases = {
        ("fx3_programming_r", "RBFM"): {"M1", "M2", "N1", "N2"},
        ("fx3_programming_r", "WBFM"): {"M1", "M2", "N1", "N2"},
        ("fxcpu_basic_applied_m", "FROM"): {"N1", "N2", "N3"},
        ("fxcpu_basic_applied_m", "TO"): {"N1", "N2", "N3"},
    }
    bad = []
    with sqlite3.connect(_database()) as connection:
        for (manual_id, opcode), expected in cases.items():
            row = connection.execute(
                "SELECT chunk_id FROM instructions WHERE manual_id=? AND opcode_norm=?",
                (manual_id, opcode.casefold()),
            ).fetchone()
            assert row is not None and row[0] is not None
            chunk_id = int(row[0])
            devices = {
                str(value).upper()
                for (value,) in connection.execute(
                    "SELECT entity FROM entity_index WHERE chunk_id=? AND entity_type='device'",
                    (chunk_id,),
                ).fetchall()
            }
            overlap = sorted(expected & devices)
            if overlap:
                bad.append((manual_id, opcode, overlap))
    assert bad == []


def test_completion_flags_exclude_generic_status_relays():
    assert "M8067" not in _flags("ACOS", "fx3_programming_r")
    assert "M8067" not in _flags("ASIN", "fx3_programming_r")
    assert not {"M8020", "M8021", "M8022"}.intersection(
        _flags("INT", "fx3_programming_r")
    )
    positioning = set(_flags("DRVA", "fx3_positioning_k"))
    assert "M8029" in positioning
    assert not {"M8329", "M8343", "M8350", "M8360", "M8370"}.intersection(positioning)


def test_manual_specific_execution_complete_flag_is_preserved():
    assert "M8012" in _flags("ZRN", "fxcpu_basic_applied_m")


def test_no_error_records_do_not_absorb_following_error_row_semantics():
    with sqlite3.connect(_database()) as connection:
        rows = connection.execute(
            """
            SELECT manual_id,pdf_page,message,cause,corrective_action,raw_text
            FROM error_records
            WHERE error_code_norm='0000'
            ORDER BY manual_id,pdf_page,id
            """
        ).fetchall()
    assert rows
    bad = []
    for manual_id, pdf_page, message, cause, corrective_action, raw_text in rows:
        normalized_message = re.sub(r"^[\s⎯—-]+", "", str(message or "")).strip()
        if normalized_message.casefold() != "no error":
            bad.append((manual_id, pdf_page, "message", message))
        if str(cause or "").strip():
            bad.append((manual_id, pdf_page, "cause", cause))
        if str(corrective_action or "").strip():
            bad.append((manual_id, pdf_page, "corrective_action", corrective_action))
        normalized_raw = re.sub(r"^[\s⎯—-]+", "", str(raw_text or "")).strip()
        if normalized_raw.casefold() != "no error":
            bad.append((manual_id, pdf_page, "raw_text", raw_text))
    assert bad == []


def test_reserved_no_error_ranges_do_not_become_fake_error_rows():
    with sqlite3.connect(_database()) as connection:
        rows = connection.execute(
            "SELECT manual_id,pdf_page,error_code,message FROM error_records WHERE error_code_norm <> '0000'"
        ).fetchall()
    bad = []
    for manual_id, pdf_page, code, message in rows:
        normalized = re.sub(r"^[\s⎯—-]+", "", str(message or "")).strip().casefold()
        if normalized in {"no error", "to", "through"}:
            bad.append((manual_id, pdf_page, code, message))
    assert bad == []



def test_primary_structured_quality_audit_uses_semantic_device_evidence(tmp_path):
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "structured-quality.json"
    subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "audit_structured_knowledge_quality.py"),
            "--database",
            str(_database()),
            "--output",
            str(output),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    device_stats = report["stats"]["device_records"]
    unresolved = [
        item
        for item in report["issues"]
        if item.get("code") == "possible_operand_placeholder_device_record"
    ]
    assert unresolved == []
    assert device_stats["placeholder_like_device_records"] == 0
    assert device_stats["placeholder_warnings_suppressed_by_concrete_evidence"] == 29
    assert device_stats["n_syntax_device_records"] == 0


@pytest.fixture(scope='module')
def refreshed_instruction_index(tmp_path_factory):
    from tools.build_fx3u_knowledge_v3 import refresh_instruction_index_copy
    output = tmp_path_factory.mktemp('source-bound-index') / 'knowledge.sqlite'
    report = refresh_instruction_index_copy(_database(), output)
    return output, report


@pytest.mark.parametrize('manual,opcode,page,symbols', [
    ('fx3_programming_r', 'ADD', 273, ['S1', 'S2', 'D']),
    ('fx3_programming_r', 'WSFL', 310, ['S', 'D', 'N1', 'N2']),
    ('fx3_programming_r', 'IVCK', 687, ['S1', 'S2', 'D', 'N']),
    ('fx3_programming_r', 'TCMP', 565, ['S1', 'S2', 'S3', 'S', 'D']),
    ('fx3_programming_r', 'CML', 256, ['S', 'D']),
    ('fx3_positioning_k', 'DVIT', 176, ['S1', 'S2', 'D1', 'D2']),
])
def test_rebuilt_index_uses_definition_page_and_explicit_operands(
        refreshed_instruction_index, manual, opcode, page, symbols):
    output, _ = refreshed_instruction_index
    with sqlite3.connect(output) as con:
        row = con.execute('SELECT page_start,chunk_id,operands_json FROM instructions '
                          'WHERE manual_id=? AND opcode=?', (manual, opcode)).fetchone()
        assert row and row[0] == page
        source = con.execute('SELECT pdf_page,pdf_page_end,section FROM chunks WHERE id=?', (row[1],)).fetchone()
    assert source and source[0] <= page <= source[1]
    assert 'How to Read' not in source[2] and 'Designation' not in source[2]
    operands = json.loads(row[2])
    assert [item['position'] for item in operands] == symbols
    assert all(item['description'] for item in operands)
    assert all(item['extraction_status'] == 'candidate_evidence' for item in operands)
    assert all(item['source_pages'] == [page] for item in operands)


def test_rebuilt_index_preserves_all_frozen_source_tables(refreshed_instruction_index):
    from itertools import zip_longest
    output, report = refreshed_instruction_index
    assert report['integrity'] == ['ok'] and report['foreign_key_violations'] == 0
    assert report['source_verification_promoted'] is False
    with sqlite3.connect(_database()) as original, sqlite3.connect(output) as refreshed:
        for table in report['source_tables_unchanged']:
            # Stream source bytes, including geometry and embeddings; do not
            # generate an expectation with the rebuilding parser.
            sentinel = object()
            for before, after in zip_longest(original.execute(f'SELECT * FROM {table}'),
                                            refreshed.execute(f'SELECT * FROM {table}'), fillvalue=sentinel):
                assert before == after, table
        assert refreshed.execute("SELECT COUNT(*) FROM instructions WHERE manual_id='fxcpu_basic_applied_m'").fetchone()[0] > 77
        assert refreshed.execute("SELECT COUNT(*) FROM instructions WHERE opcode='ST' AND manual_id='structured_fundamentals_o'").fetchone()[0] == 0


def test_st_signature_does_not_inherit_native_call_order(refreshed_instruction_index):
    output, _ = refreshed_instruction_index
    with sqlite3.connect(output) as con:
        row = con.execute("SELECT operands_json FROM instructions WHERE manual_id='fxcpu_basic_applied_m' AND opcode='WSFL'").fetchone()
    operands = json.loads(row[0])
    assert [item['position'] for item in operands] == ['S', 'N1', 'N2', 'D']
    # Missing font glyphs are not repaired by assigning descriptions through
    # physical row order. The available ST signature establishes order only.
    assert all(item['extraction_status'] == 'unknown' for item in operands)


def test_source_index_refresh_cannot_overwrite_frozen_database(tmp_path):
    from tools.build_fx3u_knowledge_v3 import refresh_instruction_index_copy
    with pytest.raises(ValueError, match='separate output'):
        refresh_instruction_index_copy(_database(), _database())
    existing = tmp_path / 'existing.sqlite'
    existing.write_bytes(b'existing')
    with pytest.raises(ValueError, match='already exists'):
        refresh_instruction_index_copy(_database(), existing)
    assert existing.read_bytes() == b'existing'


@pytest.mark.parametrize('extra', ['N | fabricated | Word', 'N\nextra text'])
def test_operand_projection_rejects_symbols_outside_st_signature(extra):
    from tools.build_fx3u_knowledge_v3 import ManualSpec, PageArtifact, parse_operand_schema
    manual = ManualSpec('fixture', Path('fixture.pdf'), 'Fixture', '1', '', '', 'en',
                        'structured_instruction', 'FixtureCPU', 1, '', '')
    text = ('XOP(EN,S1,D);\nVariable | Description | Data type\n'
            'S1 | Value to read | Word\nD | Destination device | Word\n' + extra)
    page = PageArtifact(manual, 1, '', '', '1.1 XOP / Fixture', '1.1 XOP / Fixture',
                        '', 'instruction', 'XOP', '', '', text, text, text, text)
    operands = parse_operand_schema([page], 'XOP')
    assert [item['position'] for item in operands] == ['S1', 'D']
    assert operands[0]['description'] == 'Value to read'
