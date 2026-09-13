#!/usr/bin/env python3
"""One-shot repair of the prebuilt knowledge DB from stored authoritative artifacts.

The source PDFs are intentionally not required.  The checked-in database already
contains page_artifacts/tables extracted from those manuals, so this script
recomputes the affected structured stores from that authoritative intermediate
representation after the generic builder parsers have been patched.
"""
from __future__ import annotations
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys
from types import SimpleNamespace

DB = Path('resources/knowledge/fx3u_knowledge.sqlite')
BUILDER = Path('tools/build_fx3u_knowledge_v3.py')

spec = importlib.util.spec_from_file_location('fx3_builder_repair', BUILDER)
b = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = b
assert spec.loader is not None
spec.loader.exec_module(b)


def load_json(value, fallback):
    try:
        return json.loads(value or json.dumps(fallback))
    except Exception:
        return fallback


def instruction_regex(con: sqlite3.Connection):
    vocabulary: set[str] = set()
    for opcode, variants_json in con.execute('SELECT opcode,variants_json FROM instructions'):
        if str(opcode or '').strip():
            vocabulary.add(str(opcode).strip().upper())
        for value in load_json(variants_json, []):
            if str(value or '').strip():
                vocabulary.add(str(value).strip().upper())
    return b.compile_instruction_re(vocabulary)


def refresh_chunk_entity_columns(con: sqlite3.Connection, chunk_id: int) -> None:
    rows = con.execute(
        'SELECT entity,entity_type,occurrences FROM entity_index WHERE chunk_id=? ORDER BY entity_type,entity_norm',
        (chunk_id,),
    ).fetchall()
    tokens = sorted({str(row[0]) for row in rows})
    payload = [
        {'entity': str(entity), 'type': str(kind), 'occurrences': int(count)}
        for entity, kind, count in rows
    ]
    con.execute(
        'UPDATE chunks SET entities=?,entities_json=? WHERE id=?',
        (' '.join(tokens), json.dumps(payload, ensure_ascii=False, separators=(',', ':')), chunk_id),
    )


def strip_structured_prefix(text: str) -> str:
    if not text.startswith('[STRUCTURED INSTRUCTION RECORD]'):
        return text
    parts = re.split(r'\n\s*\n', text, maxsplit=1)
    return parts[1] if len(parts) == 2 else ''


def reparse_completion_flags_and_instruction_chunks(con: sqlite3.Connection, instruction_re) -> dict:
    changed_flags = 0
    changed_chunks = 0
    seen_chunks: set[int] = set()
    duplicate_chunks: Counter[int] = Counter(
        int(row[0]) for row in con.execute('SELECT chunk_id FROM instructions WHERE chunk_id IS NOT NULL')
    )
    duplicate_chunks = Counter({key: value for key, value in duplicate_chunks.items() if value > 1})

    rows = con.execute(
        '''SELECT id,manual_id,opcode,source_pages_json,operands_json,
                  completion_flags_json,restrictions_json,chunk_id
           FROM instructions ORDER BY manual_id,opcode_norm'''
    ).fetchall()
    for instruction_id, manual_id, opcode, pages_json, operands_json, old_flags_json, restrictions_json, chunk_id in rows:
        source_pages = load_json(pages_json, [])
        page_objects = []
        if source_pages:
            placeholders = ','.join('?' for _ in source_pages)
            page_objects = [
                SimpleNamespace(clean_text=str(row[0] or ''))
                for row in con.execute(
                    f'SELECT clean_text FROM page_artifacts WHERE manual_id=? AND pdf_page IN ({placeholders}) ORDER BY pdf_page',
                    (manual_id, *source_pages),
                ).fetchall()
            ]
        new_flags = b.instruction_completion_flags(page_objects)
        old_flags = load_json(old_flags_json, [])
        if old_flags != new_flags:
            changed_flags += 1
        con.execute(
            'UPDATE instructions SET completion_flags_json=? WHERE id=?',
            (json.dumps(new_flags, ensure_ascii=False, separators=(',', ':')), instruction_id),
        )
        if chunk_id is None or int(chunk_id) in seen_chunks:
            continue
        seen_chunks.add(int(chunk_id))
        chunk = con.execute(
            'SELECT text,fidelity_flags,plc_models,manual_id,chunk_type FROM chunks WHERE id=?',
            (chunk_id,),
        ).fetchone()
        if not chunk:
            continue
        old_text, fidelity_flags, plc_models, chunk_manual, chunk_type = chunk
        base_text = strip_structured_prefix(str(old_text or ''))
        operands = load_json(operands_json, [])
        restrictions = load_json(restrictions_json, [])
        operand_summary = '; '.join(
            f"{item.get('position', '')}: {item.get('description', '')}"
            + (f" [{item.get('data_type')}]" if item.get('data_type') else '')
            + (' applicable=' + ','.join(item.get('applicable_devices') or []) if item.get('applicable_devices') else '')
            for item in operands if isinstance(item, dict)
        )[:900]
        restriction_summary = ' | '.join(str(value) for value in restrictions[:2])[:260]
        structured_lines = ['[STRUCTURED INSTRUCTION RECORD]']
        if operand_summary:
            structured_lines.append(f'OPERANDS: {operand_summary}')
        if new_flags:
            structured_lines.append(f"COMPLETION_FLAGS: {', '.join(new_flags)}")
        if restriction_summary:
            structured_lines.append(f'KEY_RESTRICTIONS: {restriction_summary}')
        enhanced_text = (
            '\n'.join(structured_lines) + '\n\n' + base_text
            if len(structured_lines) > 1 else base_text
        )
        if enhanced_text != str(old_text or ''):
            changed_chunks += 1
        entities = b.extract_entities(enhanced_text, instruction_re, chunk_type=str(chunk_type or 'instruction'))
        entity_tokens = sorted({entity for entity, _kind in entities})
        entity_json = [
            {'entity': entity, 'type': kind, 'occurrences': count}
            for (entity, kind), count in sorted(entities.items())
        ]
        bigrams, _ = b.cjk_bigrams(enhanced_text)
        flags = {value for value in str(fidelity_flags or '').split(',') if value}
        if len(structured_lines) > 1:
            flags.add('structured_instruction_record')
        else:
            flags.discard('structured_instruction_record')
        con.execute('DELETE FROM entity_index WHERE chunk_id=?', (chunk_id,))
        b.upsert_entity_rows(
            con,
            manual_id=str(chunk_manual),
            plc_models=str(plc_models),
            chunk_id=int(chunk_id),
            entities=entities,
        )
        con.execute(
            '''UPDATE chunks SET text=?,char_count=?,text_sha256=?,entities=?,entities_json=?,
                              cjk_bigrams=?,fidelity_flags=? WHERE id=?''',
            (
                enhanced_text,
                len(enhanced_text),
                hashlib.sha256(enhanced_text.encode('utf-8')).hexdigest(),
                ' '.join(entity_tokens),
                json.dumps(entity_json, ensure_ascii=False, separators=(',', ':')),
                bigrams,
                ','.join(sorted(flags)),
                int(chunk_id),
            ),
        )
    return {
        'instructions': len(rows),
        'completion_flags_changed': changed_flags,
        'instruction_chunks_changed': changed_chunks,
        'duplicate_instruction_chunk_ids': dict(duplicate_chunks),
    }


def remove_cross_family_ranges(con: sqlite3.Connection) -> dict:
    affected: set[int] = set()
    removed = []
    rows = con.execute(
        "SELECT entity_norm,entity,chunk_id FROM entity_index WHERE entity_type='device_range'"
    ).fetchall()
    for entity_norm, entity, chunk_id in rows:
        match = b.DEVICE_RANGE_RE.fullmatch(str(entity or ''))
        if match and match.group(1).upper() != (match.group(3) or match.group(1)).upper():
            con.execute(
                "DELETE FROM entity_index WHERE entity_norm=? AND entity_type='device_range' AND chunk_id=?",
                (entity_norm, chunk_id),
            )
            affected.add(int(chunk_id))
            removed.append({'entity': str(entity), 'chunk_id': int(chunk_id)})
    for chunk_id in affected:
        refresh_chunk_entity_columns(con, chunk_id)
    return {'removed': len(removed), 'affected_chunks': len(affected), 'examples': removed[:20]}


def refresh_error_code_entities(con: sqlite3.Connection, instruction_re) -> set[int]:
    affected: set[int] = set()
    rows = con.execute(
        "SELECT id,manual_id,plc_models,text,chunk_type FROM chunks WHERE chunk_type='error'"
    ).fetchall()
    for chunk_id, manual_id, plc_models, text, chunk_type in rows:
        entities = b.extract_entities(str(text or ''), instruction_re, chunk_type=str(chunk_type))
        error_entities = Counter({key: value for key, value in entities.items() if key[1] == 'error_code'})
        con.execute("DELETE FROM entity_index WHERE chunk_id=? AND entity_type='error_code'", (chunk_id,))
        if error_entities:
            b.upsert_entity_rows(
                con,
                manual_id=str(manual_id),
                plc_models=str(plc_models),
                chunk_id=int(chunk_id),
                entities=error_entities,
            )
        affected.add(int(chunk_id))
    return affected


def reparse_error_records(con: sqlite3.Connection, instruction_re) -> dict:
    error_chunks = refresh_error_code_entities(con, instruction_re)
    con.execute('DELETE FROM error_records')
    inserted = 0
    pages_seen = 0
    no_error_records = 0
    page_rows = con.execute(
        "SELECT manual_id,pdf_page,chunk_type,outline_path,clean_text FROM page_artifacts WHERE chunk_type='error' ORDER BY manual_id,pdf_page"
    ).fetchall()
    for manual_id, pdf_page, chunk_type, outline_path, clean_text in page_rows:
        table_rows = con.execute(
            'SELECT table_index,rows_json,table_text FROM tables WHERE manual_id=? AND pdf_page=? ORDER BY table_index',
            (manual_id, pdf_page),
        ).fetchall()
        tables = [
            {'index': int(index), 'rows': load_json(rows_json, []), 'text': str(table_text or '')}
            for index, rows_json, table_text in table_rows
        ]
        page = SimpleNamespace(
            chunk_type=str(chunk_type),
            outline_path=str(outline_path or ''),
            clean_text=str(clean_text or ''),
            tables=tables,
        )
        records = b.parse_error_records(page)
        if not records:
            continue
        pages_seen += 1
        manual = con.execute(
            'SELECT manual_number,revision FROM manuals WHERE manual_id=?', (manual_id,)
        ).fetchone()
        if not manual:
            continue
        chunk = con.execute(
            '''SELECT id,plc_models FROM chunks
               WHERE manual_id=? AND pdf_page<=? AND pdf_page_end>=?
               ORDER BY id LIMIT 1''',
            (manual_id, pdf_page, pdf_page),
        ).fetchone()
        chunk_id = int(chunk[0]) if chunk else None
        plc_models = str(chunk[1]) if chunk else ''
        if chunk_id is not None:
            error_chunks.add(chunk_id)
        for record in records:
            cursor = con.execute(
                '''INSERT OR IGNORE INTO error_records(
                       error_code,error_code_norm,message,cause,corrective_action,
                       raw_text,manual_id,manual_number,revision,pdf_page,table_index,
                       row_index,chunk_id
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    record['code'], record['code'].casefold(), record['message'],
                    record['cause'], record['corrective_action'], record['raw_text'],
                    manual_id, manual[0], manual[1], int(pdf_page),
                    int(record['table_index']), int(record['row_index']), chunk_id,
                ),
            )
            if cursor.rowcount:
                inserted += 1
                if record['code'].casefold() == '0000':
                    no_error_records += 1
                if chunk_id is not None:
                    b.upsert_entity_rows(
                        con,
                        manual_id=str(manual_id),
                        plc_models=plc_models,
                        chunk_id=chunk_id,
                        entities=Counter({(record['code'], 'error_code'): 1}),
                    )
    for chunk_id in error_chunks:
        refresh_chunk_entity_columns(con, chunk_id)
    return {
        'error_pages_with_records': pages_seen,
        'error_records': inserted,
        'no_error_records': no_error_records,
        'error_chunks_refreshed': len(error_chunks),
    }


def rebuild_device_records(con: sqlite3.Connection) -> dict:
    before = int(con.execute('SELECT COUNT(*) FROM device_records').fetchone()[0])
    con.execute('DELETE FROM device_records')
    inserted = b.insert_device_records(con)
    after = int(con.execute('SELECT COUNT(*) FROM device_records').fetchone()[0])
    return {'before': before, 'inserted': inserted, 'after': after}


def main() -> int:
    con = sqlite3.connect(DB)
    con.execute('PRAGMA foreign_keys=ON')
    instruction_re = instruction_regex(con)
    with con:
        instruction_stats = reparse_completion_flags_and_instruction_chunks(con, instruction_re)
        range_stats = remove_cross_family_ranges(con)
        error_stats = reparse_error_records(con, instruction_re)
        device_stats = rebuild_device_records(con)
        con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('builder_version',?)", (str(b.BUILDER_VERSION),))
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('vector_status','stale')")
    integrity = con.execute('PRAGMA integrity_check').fetchone()[0]
    fk = con.execute('PRAGMA foreign_key_check').fetchall()
    counts = {
        'chunks': int(con.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]),
        'entity_index': int(con.execute('SELECT COUNT(*) FROM entity_index').fetchone()[0]),
        'device_records': int(con.execute('SELECT COUNT(*) FROM device_records').fetchone()[0]),
        'error_records': int(con.execute('SELECT COUNT(*) FROM error_records').fetchone()[0]),
        'instructions': int(con.execute('SELECT COUNT(*) FROM instructions').fetchone()[0]),
    }
    con.close()
    report = {
        'builder_version': str(b.BUILDER_VERSION),
        'instruction_stats': instruction_stats,
        'cross_family_ranges': range_stats,
        'error_stats': error_stats,
        'device_stats': device_stats,
        'counts': counts,
        'integrity_check': integrity,
        'foreign_key_violations': len(fk),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert integrity == 'ok'
    assert not fk
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
