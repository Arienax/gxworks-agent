import json
import re
import sqlite3
from pathlib import Path

import knowledge_retriever_core as core
from resource_paths import resource_path


def _database() -> Path:
    path = Path(resource_path("knowledge/fx3u_knowledge.sqlite"))
    with path.open("rb") as stream:
        assert stream.read(16) == b"SQLite format 3\0"
    return path


def _operands(opcode: str, manual_id: str = "fx3_programming_r"):
    with sqlite3.connect(_database()) as connection:
        row = connection.execute(
            "SELECT operands_json FROM instructions WHERE opcode_norm=? AND manual_id=?",
            (opcode.casefold(), manual_id),
        ).fetchone()
    assert row is not None
    return json.loads(row[0])


def test_structured_operand_applicability_is_extracted_from_official_tables():
    fmov = _operands("FMOV")
    destination = next(item for item in fmov if item.get("position") == "D")
    assert "KnM" in destination.get("applicable_devices", [])
    assert "M" not in destination.get("applicable_devices", [])

    zrst = _operands("ZRST")
    first = next(item for item in zrst if item.get("position") == "D1")
    assert "M" in first.get("applicable_devices", [])


def test_structured_operand_rows_do_not_leak_applicable_device_headers():
    """Applicable-device matrix headers must never become operand records."""
    with sqlite3.connect(_database()) as connection:
        rows = connection.execute(
            "SELECT manual_id,opcode,operands_json FROM instructions WHERE operands_json <> '[]'"
        ).fetchall()

    bad = []
    for manual_id, opcode, payload in rows:
        for item in json.loads(payload or "[]"):
            if not isinstance(item, dict):
                continue
            position = str(item.get("position") or "").upper()
            description = str(item.get("description") or "").strip()
            if position == "X":
                bad.append((manual_id, opcode, position, description))
            if description.casefold() in {"<blank>", "blank"}:
                bad.append((manual_id, opcode, position, description))
            if description and re.fullmatch(
                r"(?:(?:\[GLYPH-[0-9A-F]+\]|\(cid:\d+\))\d*\s*)+",
                description,
                flags=re.I,
            ):
                bad.append((manual_id, opcode, position, description))
    assert bad == []


def test_structured_instruction_retrieval_surfaces_applicability_without_prompt_patch():
    results = core.retrieve_knowledge(
        "FX3U FMOV 指令的操作数、适用软元件和主要限制是什么？",
        plc_model="FX3U",
        task_type="generate",
        top_k=5,
        char_budget=9000,
    )
    assert results
    text = "\n".join(item["text"] for item in results[:3])
    assert "[STRUCTURED INSTRUCTION RECORD]" in text
    assert "applicable=" in text
    assert "KnM" in text

    reset_results = core.retrieve_knowledge(
        "FX3U 批量清零连续 M 软元件区间应该使用什么指令？",
        plc_model="FX3U",
        task_type="generate",
        top_k=5,
        char_budget=9000,
    )
    assert any(
        str(item.get("instruction_opcode", "")).upper() == "ZRST"
        for item in reset_results[:5]
    )
