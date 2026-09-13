#!/usr/bin/env python3
"""One-shot branch migration: promote the evidence-aware audit to the canonical script."""
from pathlib import Path


def promote_canonical_audit() -> None:
    path = Path("tools/audit_structured_knowledge_quality.py")
    text = path.read_text(encoding="utf-8")
    marker = "placeholder_warnings_suppressed_by_concrete_evidence"
    if marker in text:
        return

    old_import = "from typing import Any\n"
    new_import = (
        "from typing import Any\n\n"
        "from audit_device_placeholder_evidence import ("
        "TOKEN_BOUNDARY, occurrence_signals, token_source_pattern)\n"
    )
    assert old_import in text
    text = text.replace(old_import, new_import, 1)

    old_def = "def audit_devices(con: sqlite3.Connection):\n"
    assert text.count(old_def) == 1
    text = text.replace(
        old_def,
        "def _audit_devices_structural(con: sqlite3.Connection):\n",
        1,
    )

    semantic = '''_REAL_DEVICE_SIGNAL_NAMES = {
    "concrete_instruction_use",
    "concrete_range_use",
    "pointer_or_label_use",
    "example_semantics",
}


def has_concrete_device_evidence(
    connection: sqlite3.Connection,
    device_norm: str,
    record_type: str,
    token: str,
) -> bool:
    """Return whether this exact token has concrete PLC address/pointer evidence.

    The broad S/D/N/M/P-number heuristic is only candidate discovery. Final
    warning emission is occurrence-local and provenance-aware. N<number> is not
    a PLC device family here; it represents operand/count or MC/MCR nesting
    semantics and must stay outside ``device_records``.
    """
    token = normalize(token).upper()
    prefix_match = re.match(r"[A-Z]+", token)
    prefix = prefix_match.group(0) if prefix_match else ""
    if prefix == "N":
        return False

    pattern = re.compile(
        rf"(?<!{TOKEN_BOUNDARY}){token_source_pattern(token)}(?!{TOKEN_BOUNDARY})",
        re.I,
    )
    rows = connection.execute(
        """
        SELECT c.text
        FROM entity_index e
        JOIN chunks c ON c.id=e.chunk_id
        WHERE e.entity_norm=? AND e.entity_type=?
        """,
        (device_norm, record_type),
    ).fetchall()
    for (raw_text,) in rows:
        source = str(raw_text or "")
        for match in pattern.finditer(source):
            line_start = source.rfind("\\n", 0, match.start()) + 1
            line_end = source.find("\\n", match.end())
            if line_end < 0:
                line_end = len(source)
            line = source[line_start:line_end]
            # A PDF instruction-size row such as ``D | 17 steps`` can flatten
            # to ``D 17 steps``; that occurrence is not concrete D17 evidence.
            if re.search(rf"{re.escape(match.group(0))}\\s+steps\\b", line, re.I):
                continue
            signals = occurrence_signals(token, source, match)
            if any(signals.get(name) for name in _REAL_DEVICE_SIGNAL_NAMES):
                return True
    return False


def audit_devices(con: sqlite3.Connection):
    """Run structural checks, then semantically adjudicate placeholder candidates."""
    issues, stats = _audit_devices_structural(con)
    kept = []
    suppressed = 0
    for item in issues:
        if item.get("code") != "possible_operand_placeholder_device_record":
            kept.append(item)
            continue
        row = con.execute(
            "SELECT device_norm,record_type FROM device_records WHERE id=?",
            (item.get("id"),),
        ).fetchone()
        if row and has_concrete_device_evidence(
            con,
            str(row[0]),
            str(row[1]),
            str(item.get("device", "")),
        ):
            suppressed += 1
            continue
        kept.append(item)

    # N is semantic syntax, not a device family. Make any future regression a
    # hard audit error even if it appears outside the old instruction heuristic.
    existing_n_errors = {
        int(item.get("id"))
        for item in kept
        if item.get("code") == "n_syntax_in_device_records" and item.get("id") is not None
    }
    for row_id, device, record_type in con.execute(
        "SELECT id,device,record_type FROM device_records "
        "WHERE prefix='N' AND record_type IN ('device','device_range') ORDER BY id"
    ).fetchall():
        if int(row_id) not in existing_n_errors:
            kept.append(
                issue(
                    "error",
                    "device_records",
                    "n_syntax_in_device_records",
                    id=int(row_id),
                    device=str(device),
                    record_type=str(record_type),
                )
            )

    stats = dict(stats)
    stats["placeholder_like_device_records"] = sum(
        1
        for item in kept
        if item.get("code") == "possible_operand_placeholder_device_record"
    )
    stats["placeholder_warnings_suppressed_by_concrete_evidence"] = suppressed
    stats["n_syntax_device_records"] = sum(
        1 for item in kept if item.get("code") == "n_syntax_in_device_records"
    )
    return kept, stats


'''
    insert_before = "\ndef field_quality("
    assert text.count(insert_before) == 1
    text = text.replace(insert_before, "\n" + semantic + "def field_quality(", 1)
    text = text.replace(
        '"""Audit device records, error records, and instruction completion flags."""',
        '"""Evidence-aware audit of structured PLC knowledge quality."""',
        1,
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def promote_workflows() -> None:
    primary = Path(".github/workflows/audit-structured-knowledge-quality.yml")
    text = primary.read_text(encoding="utf-8")
    text = text.replace(
        "      - tools/audit_structured_knowledge_quality_v2.py\n",
        "",
    )
    text = text.replace(
        "python tools/audit_structured_knowledge_quality_v2.py",
        "python tools/audit_structured_knowledge_quality.py",
    )
    # Validate the triggering commit, not whichever branch head exists later.
    text = text.replace(
        "          ref: fix/rejected-json-repair-20260913\n",
        "",
    )

    gate_anchor = (
        "          assert not [item for item in report['issues'] "
        "if item['severity'] == 'error']\n"
    )
    if "unresolved_device_evidence" not in text:
        assert text.count(gate_anchor) == 1
        replacement = gate_anchor + '''          device_placeholder_warnings = [
              item for item in report['issues']
              if item.get('code') == 'possible_operand_placeholder_device_record'
          ]
          unresolved_device_evidence = [
              item for item in evidence['candidates']
              if item.get('suggested_class') in {'ambiguous', 'operand_placeholder_likely'}
          ]
          assert device_placeholder_warnings == [], device_placeholder_warnings
          assert unresolved_device_evidence == [], unresolved_device_evidence
          assert report['stats']['device_records']['placeholder_like_device_records'] == 0
          assert report['stats']['device_records']['n_syntax_device_records'] == 0
'''
        text = text.replace(gate_anchor, replacement, 1)
    primary.write_text(text, encoding="utf-8", newline="\n")

    rebuild = Path(".github/workflows/rebuild-device-entity-cleanup.yml")
    rebuild_text = rebuild.read_text(encoding="utf-8").replace(
        "tools/audit_structured_knowledge_quality_v2.py",
        "tools/audit_structured_knowledge_quality.py",
    )
    rebuild.write_text(rebuild_text, encoding="utf-8", newline="\n")

    assert "audit_structured_knowledge_quality_v2.py" not in primary.read_text(encoding="utf-8")
    assert "audit_structured_knowledge_quality_v2.py" not in rebuild.read_text(encoding="utf-8")


def add_regression_test() -> None:
    path = Path("tests/test_structured_knowledge_quality.py")
    text = path.read_text(encoding="utf-8")
    name = "test_primary_structured_quality_audit_uses_semantic_device_evidence"
    if name in text:
        return
    text += '''\n\n
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
'''
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    promote_canonical_audit()
    promote_workflows()
    add_regression_test()
    v2 = Path("tools/audit_structured_knowledge_quality_v2.py")
    if v2.exists():
        v2.unlink()


if __name__ == "__main__":
    main()
