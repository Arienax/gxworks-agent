#!/usr/bin/env python3
"""One-shot branch bootstrap for the generic structured-instruction RAG fix.

This script intentionally contains no FMOV/ZRST instruction rules.  It fixes the
manual-table extractor generically, backfills empty structured operand records
from the already-bundled authoritative page/table artifacts, and makes the
runtime retriever render those structured fields directly from SQLite.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "tools" / "build_fx3u_knowledge_v3.py"
RETRIEVER = ROOT / "src" / "knowledge_retriever_core.py"
DATABASE = ROOT / "resources" / "knowledge" / "fx3u_knowledge.sqlite"
MANIFEST = ROOT / "resources" / "knowledge" / "manifest.json"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_builder() -> None:
    text = BUILDER.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'BUILDER_VERSION = "3.0.0"',
        'BUILDER_VERSION = "3.0.1"',
        "builder version",
    )
    start = text.index("def parse_operand_schema(pages: list[PageArtifact])")
    end = text.index("\ndef instruction_summary(", start)
    replacement = '''def _operand_name(value: str) -> str:
    """Normalize operand placeholders printed in Mitsubishi instruction tables."""
    token = normalize_line(str(value or "")).strip()
    if not token or token.upper() in {"EN", "ENO"}:
        return ""
    if not re.fullmatch(r"[A-Za-z](?:\\d{0,3})?", token):
        return ""
    return token.upper()


def _device_column_name(value: str) -> str:
    """Normalize one column label from an Applicable devices matrix."""
    token = normalize_line(str(value or ""))
    token = re.sub(r"\\[GLYPH-[0-9A-F]+\\]|\\(cid:\\d+\\)", "", token, flags=re.I)
    token = re.sub(r"\\s+", "", token)
    if not token or token.casefold() in {"<blank>", "blank"}:
        return ""
    upper = token.upper()
    if upper in {"X", "Y", "M", "T", "C", "S", "D", "R", "V", "Z", "K", "H", "E", "P"}:
        return upper
    if re.fullmatch(r"KN[XYMS]", upper):
        return "Kn" + upper[2:]
    if upper in {"D.B", "DB"}:
        return "D.b"
    if upper.startswith("U") and "\\\\G" in upper:
        return "U\\\\G"
    if upper.startswith("MODIF"):
        return "Modifier"
    if token.startswith('"'):
        return "String"
    return ""


def _applicable_devices_by_operand(page: PageArtifact) -> dict[str, set[str]]:
    """Decode official Applicable-devices matrices without opcode-specific rules."""
    found: dict[str, set[str]] = defaultdict(set)
    for table in page.tables:
        rows = table.get("rows") or []
        if not rows:
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
        for row in rows[best_index + 1:]:
            operand = next(
                (_operand_name(cell) for cell in row[:4] if _operand_name(cell)),
                "",
            )
            if not operand:
                continue
            for column, device in best_headers.items():
                if column >= len(row):
                    continue
                marker = normalize_line(str(row[column] or ""))
                if marker and marker.casefold() not in {"<blank>", "blank", "-", "—"}:
                    found[operand].add(device)
    return found


def parse_operand_schema(pages: list[PageArtifact]) -> list[dict[str, Any]]:
    """Extract operand meaning, data type and applicable device families.

    The extractor is table-driven.  Instruction-specific facts remain in the
    authoritative Mitsubishi manual/SQLite instead of being copied into prompts.
    """
    operands: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}
    applicability: dict[str, set[str]] = defaultdict(set)

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

    type_pattern = re.compile(
        r"\\b(?:ANY(?:16|32|_SIMPLE)|bit|binary|word|double\\s*word|integer|real|bool|string)\\b",
        flags=re.I,
    )
    for page in pages[:4]:
        for name, devices in _applicable_devices_by_operand(page).items():
            applicability[name].update(devices)

        in_operands = False
        for raw_line in page.compact_layout.splitlines():
            line = normalize_line(raw_line.replace("|", " | "))
            if re.search(r"\\bSet data\\b|\\bOperand\\s+Type\\b|^Variable\\s*\\|", line, flags=re.I):
                in_operands = True
                continue
            if in_operands and re.search(
                r"Applicable devices|Explanation of function|Function and operation explanation",
                line,
                flags=re.I,
            ):
                break
            if not in_operands:
                continue
            match = re.match(r"^([A-Za-z](?:\\d{0,3})?)\\s*\\|\\s*(.+)$", raw_line.strip())
            if not match:
                continue
            name = _operand_name(match.group(1))
            if not name:
                continue
            cells = [normalize_line(value) for value in match.group(2).split("|") if normalize_line(value)]
            description = cells[0] if cells else ""
            data_type = next((value for value in cells[1:] if type_pattern.search(value)), "")
            remember(name, description, data_type)

        for table in page.tables:
            table_text = str(table.get("text", ""))
            if not re.search(r"Operand\\s+Type|Variable", table_text, flags=re.I):
                continue
            for row in table.get("rows", [])[1:]:
                cells = [normalize_line(value) for value in row]
                name = next((_operand_name(value) for value in cells[:5] if _operand_name(value)), "")
                if not name:
                    continue
                meaningful = [value for value in cells if value and _operand_name(value) != name]
                description = meaningful[0] if meaningful else ""
                data_type = next((value for value in meaningful[1:] if type_pattern.search(value)), "")
                remember(name, description, data_type)

    for name, devices in applicability.items():
        remember(name)
        if devices:
            by_name[name]["applicable_devices"] = sorted(devices)
    return operands

'''
    text = text[:start] + replacement + text[end + 1:]

    old_summary = '''            operand_summary = "; ".join(
                f"{item.get('position', '')}: {item.get('description', '')}"
                + (f" [{item.get('data_type')}]" if item.get("data_type") else "")
                for item in operands
            )[:520]
'''
    new_summary = '''            operand_summary = "; ".join(
                f"{item.get('position', '')}: {item.get('description', '')}"
                + (f" [{item.get('data_type')}]" if item.get("data_type") else "")
                + (" applicable=" + ",".join(item.get("applicable_devices") or [])
                   if item.get("applicable_devices") else "")
                for item in operands
            )[:900]
'''
    text = replace_once(text, old_summary, new_summary, "structured operand summary")
    BUILDER.write_text(text, encoding="utf-8")


def patch_retriever() -> None:
    text = RETRIEVER.read_text(encoding="utf-8")
    marker = "\ndef _format_result_block(result):\n"
    helper = '''
def _structured_instruction_record(connection, schema, chunk_id):
    """Render authoritative instruction fields from SQLite at retrieval time."""
    table = schema.get("instructions")
    if not table:
        return ""
    required = {"chunk_id", "opcode", "operands_json", "restrictions_json"}
    if not required.issubset(set(table["columns"])):
        return ""
    selected = ["opcode", "operands_json", "restrictions_json"]
    if "completion_flags_json" in table["columns"]:
        selected.append("completion_flags_json")
    row = connection.execute(
        "SELECT {} FROM {} WHERE chunk_id=? LIMIT 1".format(
            ",".join(_quote_identifier(column) for column in selected),
            _quote_identifier(table["name"]),
        ),
        (chunk_id,),
    ).fetchone()
    if row is None:
        return ""
    try:
        operands = json.loads(str(row["operands_json"] or "[]"))
    except (TypeError, ValueError):
        operands = []
    try:
        restrictions = json.loads(str(row["restrictions_json"] or "[]"))
    except (TypeError, ValueError):
        restrictions = []
    flags = []
    if "completion_flags_json" in selected:
        try:
            flags = json.loads(str(row["completion_flags_json"] or "[]"))
        except (TypeError, ValueError):
            flags = []
    lines = ["[STRUCTURED INSTRUCTION RECORD]", f"INSTRUCTION: {row['opcode']}"]
    rendered = []
    for item in operands if isinstance(operands, list) else []:
        if not isinstance(item, dict):
            continue
        part = f"{item.get('position', '')}: {item.get('description', '')}".strip()
        if item.get("data_type"):
            part += f" [{item['data_type']}]"
        devices = item.get("applicable_devices") or []
        if isinstance(devices, list) and devices:
            part += " applicable=" + ",".join(str(value) for value in devices)
        if part:
            rendered.append(part)
    if rendered:
        lines.append("OPERANDS: " + "; ".join(rendered))
    if flags:
        lines.append("COMPLETION_FLAGS: " + ", ".join(str(value) for value in flags))
    if restrictions:
        lines.append("KEY_RESTRICTIONS: " + " | ".join(str(value) for value in restrictions[:3]))
    return "\n".join(lines) if len(lines) > 2 else ""


def _augment_structured_instruction(connection, schema, result):
    if result is None:
        return None
    prefix = _structured_instruction_record(connection, schema, result.get("id"))
    if not prefix:
        return result
    enriched = dict(result)
    body = str(enriched.get("text") or "")
    if body.startswith("[STRUCTURED INSTRUCTION RECORD]"):
        _old, separator, remainder = body.partition("\n\n")
        body = remainder if separator else ""
    enriched["text"] = prefix + ("\n\n" + body if body else "")
    return enriched

'''
    if marker not in text:
        raise RuntimeError("retriever format marker not found")
    text = text.replace(marker, helper + "def _format_result_block(result):\n", 1)
    old_loop = '''        result = _chunk_result(row, meta, path, plc_model, task_type)
        if reference["match_type"] == "manual_instruction" and (
'''
    new_loop = '''        result = _chunk_result(row, meta, path, plc_model, task_type)
        if reference["match_type"] == "structured_instruction":
            result = _augment_structured_instruction(connection, schema, result)
        if reference["match_type"] == "manual_instruction" and (
'''
    text = replace_once(text, old_loop, new_loop, "structured retrieval augmentation")
    RETRIEVER.write_text(text, encoding="utf-8")


def load_builder_module():
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("fxbuilder_bootstrap", BUILDER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load patched builder")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def backfill_database() -> int:
    module = load_builder_module()
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT id,manual_id,source_pages_json,operands_json FROM instructions ORDER BY id"
    ).fetchall()
    changed = 0
    for row in rows:
        try:
            existing = json.loads(row["operands_json"] or "[]")
        except (TypeError, ValueError):
            existing = []
        if existing:
            continue
        pages = []
        try:
            source_pages = json.loads(row["source_pages_json"] or "[]")
        except (TypeError, ValueError):
            source_pages = []
        for page_number in source_pages[:4]:
            page = connection.execute(
                "SELECT compact_layout FROM page_artifacts WHERE manual_id=? AND pdf_page=?",
                (row["manual_id"], page_number),
            ).fetchone()
            if page is None:
                continue
            table_rows = connection.execute(
                "SELECT table_index,rows_json,table_text FROM tables "
                "WHERE manual_id=? AND pdf_page=? ORDER BY table_index",
                (row["manual_id"], page_number),
            ).fetchall()
            tables = []
            for table in table_rows:
                try:
                    parsed_rows = json.loads(table["rows_json"] or "[]")
                except (TypeError, ValueError):
                    parsed_rows = []
                tables.append(
                    {
                        "index": table["table_index"],
                        "rows": parsed_rows,
                        "text": table["table_text"] or "",
                    }
                )
            from types import SimpleNamespace
            pages.append(SimpleNamespace(compact_layout=page["compact_layout"] or "", tables=tables))
        operands = module.parse_operand_schema(pages)
        if operands:
            connection.execute(
                "UPDATE instructions SET operands_json=? WHERE id=?",
                (json.dumps(operands, ensure_ascii=False, separators=(",", ":")), row["id"]),
            )
            changed += 1
    connection.commit()

    # Generic integrity checks: the problematic use-case must now be expressible
    # solely from structured manual data, with no opcode-specific prompt rule.
    fmov_row = connection.execute(
        "SELECT operands_json FROM instructions "
        "WHERE opcode_norm='fmov' AND manual_id='fx3_programming_r'"
    ).fetchone()
    zrst_row = connection.execute(
        "SELECT operands_json FROM instructions "
        "WHERE opcode_norm='zrst' AND manual_id='fx3_programming_r'"
    ).fetchone()
    if fmov_row is None or zrst_row is None:
        raise RuntimeError("FMOV/ZRST authoritative rows disappeared")
    fmov = json.loads(fmov_row[0])
    zrst = json.loads(zrst_row[0])
    destination = next((item for item in fmov if item.get("position") == "D"), None)
    reset_head = next((item for item in zrst if item.get("position") == "D1"), None)
    if not destination or not reset_head:
        raise RuntimeError(f"operand extraction incomplete: FMOV={fmov!r} ZRST={zrst!r}")
    fmov_devices = set(destination.get("applicable_devices") or [])
    zrst_devices = set(reset_head.get("applicable_devices") or [])
    if "KnM" not in fmov_devices or "M" in fmov_devices:
        raise RuntimeError(f"FMOV applicability matrix decoded incorrectly: {sorted(fmov_devices)}")
    if "M" not in zrst_devices:
        raise RuntimeError(f"ZRST applicability matrix decoded incorrectly: {sorted(zrst_devices)}")
    connection.close()
    return changed


def update_manifest(changed: int) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["builder_version"] = "3.0.1"
    manifest["database_bytes"] = DATABASE.stat().st_size
    digest = hashlib.sha256()
    with DATABASE.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    manifest["database_sha256"] = digest.hexdigest()
    manifest["structured_instruction_backfill"] = {
        "method": "generic operand/applicable-device matrix extraction",
        "source": "authoritative page_artifacts and tables",
        "changed_empty_instruction_rows": changed,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    patch_builder()
    patch_retriever()
    changed = backfill_database()
    update_manifest(changed)
    print(f"backfilled {changed} empty structured instruction rows")


if __name__ == "__main__":
    main()
