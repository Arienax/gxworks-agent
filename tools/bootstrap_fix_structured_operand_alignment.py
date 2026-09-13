#!/usr/bin/env python3
"""One-shot branch migration for generic structured operand alignment."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "tools" / "build_fx3u_knowledge_v3.py"
CANDIDATE_DB = ROOT / "resources" / "knowledge" / "fx3u_knowledge.sqlite"
BASELINE_DB = ROOT / "baseline-main" / "resources" / "knowledge" / "fx3u_knowledge.sqlite"
MANIFEST = ROOT / "resources" / "knowledge" / "manifest.json"


def patch_builder() -> None:
    text = BUILDER.read_text(encoding="utf-8")
    text = text.replace('BUILDER_VERSION = "3.0.1"', 'BUILDER_VERSION = "3.0.2"', 1)
    start = text.index("def _operand_name(value: str) -> str:")
    end = text.index("\ndef instruction_summary(", start)
    replacement = r'''def _operand_name(value: str) -> str:
    """Normalize Mitsubishi instruction operand placeholders.

    Data operands in the source manuals use S/D/N/M/P families (optionally
    numbered). Device headers such as X/Y/K are not operand placeholders.
    """
    token = normalize_line(str(value or "")).strip()
    if not token or token.upper() in {"EN", "ENO"}:
        return ""
    if not re.fullmatch(r"[SDNMPsdnmp](?:\d{0,3})?", token):
        return ""
    return token.upper()


def _clean_table_cell(value: str) -> str:
    token = normalize_line(str(value or "")).strip()
    if token.casefold() in {"", "<blank>", "blank", "-", "—"}:
        return ""
    return token


def _device_column_name(value: str) -> str:
    """Normalize one column label from an Applicable devices matrix."""
    token = normalize_line(str(value or ""))
    token = re.sub(r"\[GLYPH-[0-9A-F]+\]|\(cid:\d+\)", "", token, flags=re.I)
    token = re.sub(r"\s+", "", token)
    if not token or token.casefold() in {"<blank>", "blank"}:
        return ""
    upper = token.upper()
    if upper in {"X", "Y", "M", "T", "C", "S", "D", "R", "V", "Z", "K", "H", "E", "P"}:
        return upper
    if re.fullmatch(r"KN[XYMS]", upper):
        return "Kn" + upper[2:]
    if upper in {"D.B", "DB"}:
        return "D.b"
    if upper.startswith("U") and "\\G" in upper:
        return "U\\G"
    if upper.startswith("MODIF"):
        return "Modifier"
    if token.startswith('"'):
        return "String"
    return ""


def _signature_operand_order(pages: list[PageArtifact], opcode: str) -> list[str]:
    if not opcode:
        return []
    pattern = re.compile(rf"(?<![A-Z0-9_]){re.escape(opcode)}\s*\(([^)]{{1,180}})\)", re.I)
    for page in pages[:4]:
        for text in (page.compact_layout, page.layout_text, page.clean_text):
            for match in pattern.finditer(text or ""):
                raw_args = [normalize_line(value) for value in match.group(1).split(",")]
                names: list[str] = []
                valid = True
                for raw in raw_args:
                    if raw.upper() in {"EN", "ENO"}:
                        continue
                    name = _operand_name(raw)
                    if not name:
                        valid = False
                        break
                    names.append(name)
                if valid and names:
                    return list(dict.fromkeys(names))
    return []


def _compact_operand_rows(page: PageArtifact) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    active = False
    type_pattern = re.compile(
        r"\b(?:ANY(?:16|32|_SIMPLE)|BIN\s*\d+(?:/\d+)?-?bit|bit|binary|word|double\s*word|integer|real|bool|string)\b",
        flags=re.I,
    )
    for raw_line in page.compact_layout.splitlines():
        line = normalize_line(raw_line.replace("|", " | "))
        if re.search(r"\bSet data\b|\bOperand\s+Type\b|^Variable\s*\|", line, flags=re.I):
            active = True
            continue
        if active and re.search(
            r"Applicable devices|Explanation of function|Function and operation explanation",
            line,
            flags=re.I,
        ):
            break
        if not active:
            continue
        cells = [_clean_table_cell(value) for value in raw_line.split("|")]
        if not cells:
            continue
        name = next((_operand_name(value) for value in cells[:2] if _operand_name(value)), "")
        if not name:
            continue
        meaningful = [value for value in cells[1:] if value]
        description = meaningful[0] if meaningful else ""
        data_type = next((value for value in meaningful[1:] if type_pattern.search(value)), "")
        if description:
            rows.append((name, description, data_type))
    return rows


def _definition_table_rows(
    page: PageArtifact,
    expected_order: list[str],
) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    type_pattern = re.compile(
        r"\b(?:ANY(?:16|32|_SIMPLE)|BIN\s*\d+(?:/\d+)?-?bit|bit|binary|word|double\s*word|integer|real|bool|string)\b",
        flags=re.I,
    )
    for table in page.tables:
        rows = table.get("rows") or []
        table_text = normalize_line(str(table.get("text", "")))
        if not rows or not re.search(r"\bDescription\b", table_text, flags=re.I):
            continue
        if re.search(r"Bit\s+Devices", table_text, flags=re.I) and re.search(r"Word\s+Devices", table_text, flags=re.I):
            continue

        header_index = -1
        desc_col = -1
        for row_index, row in enumerate(rows[:6]):
            for column, raw in enumerate(row):
                if re.search(r"\bDescription\b", _clean_table_cell(raw), flags=re.I):
                    header_index, desc_col = row_index, column
                    break
            if header_index >= 0:
                break
        if header_index < 0 or desc_col < 0:
            continue

        expected_cursor = 0
        consumed: set[str] = set()
        for row in rows[header_index + 1:]:
            cells = [_clean_table_cell(value) for value in row]
            if desc_col >= len(cells):
                continue
            name_area = cells[:desc_col]
            if any(str(value).upper() in {"EN", "ENO"} for value in name_area if value):
                continue
            explicit = next((_operand_name(value) for value in name_area if _operand_name(value)), "")
            description = cells[desc_col]
            if not description:
                continue
            data_type = next((value for value in cells[desc_col + 1:] if value and type_pattern.search(value)), "")
            name = explicit
            if not name:
                while expected_cursor < len(expected_order) and expected_order[expected_cursor] in consumed:
                    expected_cursor += 1
                if expected_cursor < len(expected_order):
                    name = expected_order[expected_cursor]
                    expected_cursor += 1
            if not name:
                continue
            consumed.add(name)
            result.append((name, description, data_type))
    return result


def _applicable_devices_by_operand(
    page: PageArtifact,
    expected_order: list[str],
) -> dict[str, set[str]]:
    """Decode official Applicable-devices matrices without opcode rules."""
    found: dict[str, set[str]] = defaultdict(set)
    for table in page.tables:
        rows = table.get("rows") or []
        table_text = normalize_line(str(table.get("text", "")))
        if not rows:
            continue
        if not (
            re.search(r"Bit\s+Devices", table_text, flags=re.I)
            and re.search(r"Word\s+Devices", table_text, flags=re.I)
        ):
            continue

        best_index = -1
        best_headers: dict[int, str] = {}
        for row_index, row in enumerate(rows[:8]):
            headers = {
                index: name
                for index, cell in enumerate(row)
                if (name := _device_column_name(cell))
            }
            if len(headers) > len(best_headers):
                best_index, best_headers = row_index, headers
        if len(best_headers) < 4:
            continue

        first_device_col = min(best_headers)
        expected_cursor = 0
        consumed: set[str] = set()
        for row in rows[best_index + 1:]:
            cells = [_clean_table_cell(value) for value in row]
            marked = [
                column
                for column in best_headers
                if column < len(cells) and cells[column]
            ]
            if not marked:
                continue
            explicit = next(
                (_operand_name(value) for value in cells[:first_device_col] if _operand_name(value)),
                "",
            )
            name = explicit
            if not name:
                while expected_cursor < len(expected_order) and expected_order[expected_cursor] in consumed:
                    expected_cursor += 1
                if expected_cursor < len(expected_order):
                    name = expected_order[expected_cursor]
                    expected_cursor += 1
            if not name:
                continue
            consumed.add(name)
            for column in marked:
                found[name].add(best_headers[column])
    return found


def parse_operand_schema(
    pages: list[PageArtifact],
    opcode: str = "",
) -> list[dict[str, Any]]:
    """Extract operand semantics and device applicability from manual tables.

    The parser distinguishes operand-definition tables from Applicable-devices
    matrices and uses instruction signatures only to align rows whose operand
    glyph was lost by PDF table extraction.
    """
    operands: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}

    def remember(name: str, description: str = "", data_type: str = "") -> None:
        if not name:
            return
        current = by_name.get(name)
        if current is None:
            current = {"position": name, "description": description, "data_type": data_type}
            operands.append(current)
            by_name[name] = current
        else:
            if description and not current.get("description"):
                current["description"] = description
            if data_type and not current.get("data_type"):
                current["data_type"] = data_type

    compact_rows: list[tuple[str, str, str]] = []
    for page in pages[:4]:
        compact_rows.extend(_compact_operand_rows(page))
    compact_order = list(dict.fromkeys(name for name, _description, _data_type in compact_rows))
    signature_order = _signature_operand_order(pages, opcode)
    expected_order = compact_order or signature_order

    for name, description, data_type in compact_rows:
        remember(name, description, data_type)
    for page in pages[:4]:
        for name, description, data_type in _definition_table_rows(page, expected_order):
            remember(name, description, data_type)

    applicability: dict[str, set[str]] = defaultdict(set)
    for page in pages[:4]:
        for name, devices in _applicable_devices_by_operand(page, expected_order).items():
            applicability[name].update(devices)
    for name, devices in applicability.items():
        remember(name)
        if devices:
            by_name[name]["applicable_devices"] = sorted(devices)

    # Do not publish a placeholder that was seen only as an unlabeled/empty row.
    return [
        item for item in operands
        if item.get("description") or item.get("data_type") or item.get("applicable_devices")
    ]


'''
    text = text[:start] + replacement + text[end + 1:]
    old_call = "        operands = parse_operand_schema(instruction_pages)\n"
    new_call = "        operands = parse_operand_schema(instruction_pages, opcode)\n"
    if old_call not in text:
        raise SystemExit("insert_instruction_records parse call not found")
    text = text.replace(old_call, new_call, 1)
    BUILDER.write_text(text, encoding="utf-8")


def load_module():
    spec = importlib.util.spec_from_file_location("fx_builder_alignment", BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def baseline_empty_keys() -> set[tuple[str, str]]:
    connection = sqlite3.connect(BASELINE_DB)
    rows = connection.execute(
        "SELECT manual_id,opcode_norm,operands_json FROM instructions"
    ).fetchall()
    connection.close()
    return {
        (str(manual_id), str(opcode_norm))
        for manual_id, opcode_norm, payload in rows
        if json.loads(payload or "[]") == []
    }


def page_objects(connection: sqlite3.Connection, manual_id: str, pages: list[int]):
    result = []
    for page_number in pages[:4]:
        page = connection.execute(
            """
            SELECT clean_text,layout_text,compact_layout
            FROM page_artifacts WHERE manual_id=? AND pdf_page=?
            """,
            (manual_id, page_number),
        ).fetchone()
        if page is None:
            continue
        tables = []
        for table in connection.execute(
            """
            SELECT table_index,rows_json,table_text FROM tables
            WHERE manual_id=? AND pdf_page=? ORDER BY table_index
            """,
            (manual_id, page_number),
        ).fetchall():
            tables.append({
                "index": table[0],
                "rows": json.loads(table[1] or "[]"),
                "text": table[2] or "",
            })
        result.append(SimpleNamespace(
            clean_text=page[0] or "",
            layout_text=page[1] or "",
            compact_layout=page[2] or "",
            tables=tables,
        ))
    return result


def migrate_database() -> dict[str, int]:
    module = load_module()
    target_keys = baseline_empty_keys()
    connection = sqlite3.connect(CANDIDATE_DB)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT id,manual_id,opcode,opcode_norm,source_pages_json,operands_json FROM instructions"
    ).fetchall()
    originally_backfilled = 0
    recomputed_nonempty = 0
    recomputed_empty = 0
    changed = 0
    for row in rows:
        key = (str(row["manual_id"]), str(row["opcode_norm"]))
        if key not in target_keys:
            continue
        old = json.loads(row["operands_json"] or "[]")
        if old:
            originally_backfilled += 1
        pages = page_objects(
            connection,
            str(row["manual_id"]),
            json.loads(row["source_pages_json"] or "[]"),
        )
        operands = module.parse_operand_schema(pages, str(row["opcode"]))
        if operands:
            recomputed_nonempty += 1
        else:
            recomputed_empty += 1
        if operands != old:
            connection.execute(
                "UPDATE instructions SET operands_json=? WHERE id=?",
                (json.dumps(operands, ensure_ascii=False, separators=(",", ":")), row["id"]),
            )
            changed += 1
    connection.commit()
    connection.close()
    return {
        "baseline_empty_rows": len(target_keys),
        "originally_backfilled_rows": originally_backfilled,
        "recomputed_nonempty_rows": recomputed_nonempty,
        "recomputed_empty_rows": recomputed_empty,
        "changed_rows": changed,
    }


def update_manifest(stats: dict[str, int]) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["builder_version"] = "3.0.2"
    manifest["database_bytes"] = CANDIDATE_DB.stat().st_size
    manifest["database_sha256"] = hashlib.sha256(CANDIDATE_DB.read_bytes()).hexdigest()
    manifest["structured_instruction_alignment_fix"] = {
        "method": "definition-table/signature alignment plus applicable-device matrix extraction",
        "source": "authoritative page_artifacts and tables",
        **stats,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    patch_builder()
    stats = migrate_database()
    update_manifest(stats)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
