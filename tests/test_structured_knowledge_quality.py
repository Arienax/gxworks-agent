import json
import re
import sqlite3
from pathlib import Path

from resource_paths import resource_path


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
