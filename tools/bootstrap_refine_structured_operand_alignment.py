#!/usr/bin/env python3
"""Refine the one-shot operand alignment migration and its generic audit."""

from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, got {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


bootstrap = Path("tools/bootstrap_fix_structured_operand_alignment.py")

replace_once(
    bootstrap,
    '''    def remember(name: str, description: str = "", data_type: str = "") -> None:\n        if not name:\n            return\n        current = by_name.get(name)\n''',
    '''    def remember(name: str, description: str = "", data_type: str = "") -> None:\n        if not name:\n            return\n        description = normalize_line(description)\n        data_type = normalize_line(data_type)\n        type_only = re.compile(\n            r"^(?:ANY(?:16|32|_SIMPLE)|BIN\\s*\\d+(?:/\\d+)?-?bit|bit|binary|word|double\\s*word|integer|real|bool|string)(?:\\s+binary)?$",\n            flags=re.I,\n        )\n        if description and not data_type and type_only.fullmatch(description):\n            data_type, description = description, ""\n        if description and re.fullmatch(\n            r"(?:(?:\\[GLYPH-[0-9A-F]+\\]|\\(cid:\\d+\\))\\s*)+", description, flags=re.I\n        ):\n            description = ""\n        if description and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", description) and len(description) <= 3:\n            description = ""\n        if description and len(description) < 24:\n            words = re.findall(r"[A-Za-z]+", description)\n            if words and not any(len(word) >= 4 for word in words) and not re.search(r"\\d{2,}", description):\n                description = ""\n        current = by_name.get(name)\n''',
)

replace_once(
    bootstrap,
    '''    compact_order = list(dict.fromkeys(name for name, _description, _data_type in compact_rows))\n    signature_order = _signature_operand_order(pages, opcode)\n    expected_order = compact_order or signature_order\n\n    for name, description, data_type in compact_rows:\n''',
    '''    signature_order = _signature_operand_order(pages, opcode)\n    if signature_order:\n        compact_rows = [row for row in compact_rows if row[0] in signature_order]\n    compact_order = list(dict.fromkeys(name for name, _description, _data_type in compact_rows))\n    expected_order = signature_order or compact_order\n\n    for name, description, data_type in compact_rows:\n''',
)

audit = Path("tools/audit_structured_instruction_backfill.py")
replace_once(
    audit,
    '''        semantic = f"{description} {data_type}".casefold()\n        word_only = "word device" in semantic and "bit or word" not in semantic and "bit/word" not in semantic\n''',
    '''        semantic = f"{description} {data_type}".casefold()\n        word_only = bool(re.search(\n            r"\\b(?:head\\s+word\\s+device|word\\s+device\\s+(?:number|of)|word\\s+device$)",\n            semantic,\n        )) and "bit or word" not in semantic and "bit/word" not in semantic\n''',
)

replace_once(
    audit,
    '''        if description and description_blank:\n            issues.append(_issue("error", "blank_marker_used_as_description", row, index=index, position=position, description=description))\n        if isinstance(item.get("applicable_devices"), list) and not devices:\n''',
    '''        if description and description_blank:\n            issues.append(_issue("error", "blank_marker_used_as_description", row, index=index, position=position, description=description))\n        if description and re.fullmatch(\n            r"(?:(?:\\[GLYPH-[0-9A-F]+\\]|\\(cid:\\d+\\))\\s*)+", description, flags=re.I\n        ):\n            issues.append(_issue("error", "glyph_only_description", row, index=index, position=position, description=description))\n        if description and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", description) and len(description) <= 3:\n            issues.append(_issue("error", "description_looks_like_table_token", row, index=index, position=position, description=description))\n        if description and len(description) < 24:\n            words = re.findall(r"[A-Za-z]+", description)\n            if words and not any(len(word) >= 4 for word in words) and not re.search(r"\\d{2,}", description):\n                issues.append(_issue("error", "low_quality_description", row, index=index, position=position, description=description))\n        if isinstance(item.get("applicable_devices"), list) and not devices:\n''',
)

print("refined bootstrap migration and audit")
