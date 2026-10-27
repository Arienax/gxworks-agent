# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Extend the canonical shared index, extracting each added manual only once.

Uses the existing evidence/chunk/entity builder. Existing source text, third
party attribution and reviewed FX instruction records are kept unchanged.
Run against a private output copy, then rebuild its shared dense artifact and
validate before promoting the database/manifest/dense files together.
"""
from __future__ import annotations

import sys as _private_sys
from pathlib import Path as _PrivatePath
_private_sys.path.insert(0, str(_PrivatePath(__file__).resolve().parents[1]))
from tools.offline_inputs import private_path

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import build_fx3u_knowledge_clean as clean
from knowledge.manual_applicability import apply_manual_scope, ql_header_symbols, source_instruction_symbols


def bind_symbol_sections(pages):
    """Use the source's own symbol heading for Q/L instruction ownership.

    No parameter schema, executable forms or runtime semantics are guessed.
    Separate operation headings stay separate even when their symbols overlap.
    """
    anchors = {p.outline_path: p.visual['source_applicability'] for p in pages
               if p.visual.get('source_applicability', {}).get('opcodes')}
    for page in pages:
        names = source_instruction_symbols(page.clean_text, page.outline_path)
        if names and page.outline_path not in anchors:
            anchors[page.outline_path] = {'opcodes': names}
    for page in pages:
        keys = [key for key in anchors if page.outline_path == key or page.outline_path.startswith(key+' > ')]
        if keys:
            key = max(keys, key=len)
            page.instruction_opcode = anchors[key]['opcodes'][0]
            page.chunk_type = 'instruction'
            # Keep outline identity: two different variants can share a base.
    return anchors


def insert_symbol_records(connection, spec, anchors):
    for outline, header in anchors.items():
        chunks = connection.execute('SELECT id,pdf_page,pdf_page_end FROM chunks WHERE manual_id=? AND '
                                    '(outline_path=? OR outline_path LIKE ?) ORDER BY pdf_page,id',
                                    (spec.id, outline, outline+' > %')).fetchall()
        if not chunks:
            continue
        if header.get('pdf_page'):
            chunks = [row for row in chunks if row[1] <= header['pdf_page'] <= row[2]]
            if not chunks:
                continue
        first = chunks[0]
        symbols = list(dict.fromkeys(header['opcodes']))
        source_pages = sorted({p for row in chunks for p in range(row[1], row[2]+1)})
        for symbol in symbols:
            if connection.execute('SELECT 1 FROM instructions WHERE manual_id=? AND opcode_norm=?', (spec.id,symbol.casefold())).fetchone():
                continue
            cursor = connection.execute('INSERT INTO instructions(opcode,opcode_norm,fnc_number,title,summary,'
                'variants_json,operands_json,completion_flags_json,restrictions_json,manual_id,manual_number,revision,'
                'page_start,page_end,source_pages_json,chunk_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (symbol,symbol.casefold(),'',outline,'',json.dumps([symbol]),'[]','[]','[]',spec.id,spec.manual_number,
                 spec.revision,min(source_pages),max(source_pages),json.dumps(source_pages),first[0]))
            connection.execute('INSERT OR IGNORE INTO instruction_aliases(alias_norm,alias,alias_type,instruction_id,chunk_id) '
                               'VALUES(?,?,?,?,?)',(symbol.casefold(),symbol,'opcode',cursor.lastrowid,first[0]))


def index_legacy_headings(connection, spec):
    """Bind older FX's literal OPCODE (FNC n) titles using retained evidence."""
    anchors = {}
    for (outline,) in connection.execute('SELECT DISTINCT outline_path FROM page_artifacts WHERE manual_id=?', (spec.id,)):
        match = re.match(r'^\s*\d+(?:\.\d+)+\s+([A-Z][A-Z0-9_]*)\s*\(FNC\s*(\d+)\)\s*$', outline.split(' > ')[-1])
        if not match:
            continue
        opcode = match[1]
        anchors[outline] = {'opcodes':[opcode]}
        connection.execute("UPDATE chunks SET instruction_opcode=?,chunk_type='instruction' WHERE manual_id=? AND "
                           '(outline_path=? OR outline_path LIKE ?)', (opcode,spec.id,outline,outline+' > %'))
        connection.execute("UPDATE page_artifacts SET instruction_opcode=?,fnc_number=?,chunk_type='instruction' WHERE manual_id=? AND "
                           '(outline_path=? OR outline_path LIKE ?)', (opcode,str(int(match[2])),spec.id,outline,outline+' > %'))
    insert_symbol_records(connection,spec,anchors)
    # Several old PDFs put two literal instruction titles on one page, while
    # their outline names only the second title. Preserve that page as shared
    # source evidence for both; do not recover symbols from prose mentions.
    for page,outline,text,visual in connection.execute(
            'SELECT pdf_page,outline_path,clean_text,visual_json FROM page_artifacts WHERE manual_id=? ORDER BY pdf_page', (spec.id,)):
        header = json.loads(visual or '{}').get('source_applicability')
        if not header or header.get('basis') != 'instruction_header':
            continue
        names = re.findall(r'(?m)^\d+(?:\.\d+)+\s+([A-Z][A-Z0-9_]*)\s*\(FNC\s*\d+\)\s*$',text)
        if names:
            insert_symbol_records(connection,spec,{outline:{'opcodes':names,'pdf_page':page}})


def index_source_symbols(connection, spec):
    """Recover retained declarations without reparsing PDFs or changing text.

    Instruction aliases keep every form on a shared page. The chunk's primary
    symbol remains a routing hint; it is not the only instruction association.
    Error records and original source applicability are preserved separately.
    """
    count = 0
    pages = connection.execute(
        'SELECT pdf_page,outline_path,clean_text FROM page_artifacts WHERE manual_id=?', (spec.id,)
    ).fetchall()
    for page, outline, text in pages:
        names = source_instruction_symbols(text, outline)
        if not names or not outline:
            continue
        insert_symbol_records(connection, spec, {outline: {'opcodes': names, 'pdf_page': page}})
        connection.execute(
            "UPDATE chunks SET instruction_opcode=?,chunk_type='instruction' WHERE manual_id=? "
            "AND (outline_path=? OR outline_path LIKE ?) AND pdf_page<=? AND pdf_page_end>=? "
            "AND instruction_opcode='' AND chunk_type IN ('section','device','error')",
            (names[0], spec.id, outline, outline+' > %', page, page),
        )
        connection.execute(
            "UPDATE page_artifacts SET instruction_opcode=?,chunk_type='instruction' "
            "WHERE manual_id=? AND pdf_page=? AND instruction_opcode=''",
            (names[0], spec.id, page),
        )
        count += 1
    return count


def index_reference_devices(connection):
    """Add the Core lexer's Q/L tokens to retained source entities once."""
    from plc.device_identity import reference_device_tokens
    base = clean._pdf_builder()
    rows = connection.execute('SELECT id,manual_id,plc_models,text FROM chunks').fetchall()
    for chunk_id, manual_id, models, text in rows:
        entities = Counter((token,'device') for token in reference_device_tokens(text)
                           if not base.DEVICE_RE.fullmatch(token))
        if entities:
            # A resume must replace counts, rather than count the same retained
            # source a second time. The PDF builder's additive upsert is used
            # only after removing these exact entities for this chunk.
            connection.executemany('DELETE FROM entity_index WHERE chunk_id=? AND entity_type=? AND entity_norm=?',
                                   [(chunk_id,kind,token.casefold()) for token,kind in entities])
            base.upsert_entity_rows(connection,manual_id=manual_id,plc_models=models,chunk_id=chunk_id,entities=entities)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, default=private_path('resources/knowledge/sources.json'))
    parser.add_argument('--source-index', type=Path, default=private_path('resources/knowledge/fx3u_knowledge.sqlite'))
    parser.add_argument('--source-manifest', type=Path, default=private_path('resources/knowledge/manifest.json'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--skip-tables', action='store_true')
    args = parser.parse_args(argv)
    if args.output.resolve() == args.source_index.resolve():
        parser.error('Output must be an isolated database copy')
    if args.output.exists() and not args.resume:
        parser.error('Output exists; explicitly use --resume for this private copy')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    base = clean._pdf_builder()
    base.extract_entities = clean.extract_entities_clean
    sources = json.loads(args.sources.read_text('utf8'))
    specs = base.load_manual_specs(args.sources, [])
    by_id = {m['id']:m for m in sources['manuals']}
    if not args.output.exists():
        with sqlite3.connect(f'file:{args.source_index.resolve().as_posix()}?mode=ro', uri=True) as original, sqlite3.connect(args.output) as copied:
            original.backup(copied)
    report = {'started_at_utc':datetime.now(timezone.utc).isoformat(), 'shared_index':True, 'manuals':{}}
    started = time.perf_counter()
    with sqlite3.connect(args.output) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA journal_mode=MEMORY')
        connection.execute('PRAGMA synchronous=OFF')
        for spec in specs:
            source = by_id[spec.id]
            indexed = connection.execute('SELECT pdf_pages,plc_models FROM manuals WHERE manual_id=?',(spec.id,)).fetchone()
            state = connection.execute('SELECT value FROM meta WHERE key=?',('source_scope:'+spec.id,)).fetchone()
            layout = source.get('applicability_layout') or source.get('instruction_layout')
            from knowledge.manual_applicability import SCOPE_EXTRACTOR_VERSION
            if (indexed and state and set(indexed[1].split(',')) == set(source['plc_models'])
                    and json.loads(state[0]).get('extractor_version') == SCOPE_EXTRACTOR_VERSION
                    and json.loads(state[0]).get('applicability_layout') == layout):
                report['manuals'][spec.id] = json.loads(state[0])
                continue
            page_headers = None
            if not indexed:
                pages, vocabulary, instruction_re, outline = base.extract_manual_pages(
                    spec,skip_tables=args.skip_tables,progress_every=100,started=started,
                    applicability_layout=source.get('applicability_layout') or source.get('instruction_layout'))
                anchors = bind_symbol_sections(pages)
                drafts = base.make_section_chunks(pages,target_chars=base.DEFAULT_TARGET_CHARS,max_chars=base.DEFAULT_MAX_CHARS)
                base.insert_manual(connection,spec,len(pages))
                base.insert_page_artifacts(connection,pages)
                first_chunks, first_instructions, _ = base.insert_chunks(connection,pages,drafts,instruction_re)
                if source.get('applicability_layout',{}).get('kind') == 'ql_icons':
                    insert_symbol_records(connection,spec,anchors)
                else:
                    base.insert_instruction_records(connection,pages,{spec.id:instruction_re},first_instructions)
                base.insert_error_records(connection,pages,first_chunks)
                page_headers = [(p.pdf_page,p.outline_path,p.visual.get('source_applicability')) for p in pages]
                print(f'[{spec.id}] indexed pages={len(pages)} chunks={len(drafts)} source symbol headings={len(anchors)}',flush=True)
                del pages, drafts
            scope_report = apply_manual_scope(connection,spec,source,page_headers=page_headers)
            base.put_meta(connection,{'source_scope:'+spec.id:scope_report})
            connection.commit()
            report['manuals'][spec.id] = scope_report
            args.report.parent.mkdir(parents=True,exist_ok=True)
            args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n','utf8')
            print(f'[{spec.id}] source scope={scope_report}',flush=True)
        for spec in specs:
            cached = []
            for page,outline,visual,words in connection.execute(
                    'SELECT pdf_page,outline_path,visual_json,word_geometry_json FROM page_artifacts WHERE manual_id=?', (spec.id,)):
                header = json.loads(visual or '{}').get('source_applicability')
                if not header:
                    continue
                if by_id[spec.id].get('applicability_layout',{}).get('kind') == 'ql_icons':
                    header['opcodes'] = ql_header_symbols(json.loads(words))
                    insert_symbol_records(connection,spec,{outline:{**header,'pdf_page':page}})
                cached.append((page,outline,header))
            if by_id[spec.id].get('applicability_layout',{}).get('kind') == 'ql_icons':
                ids = [row[0] for row in connection.execute("SELECT id FROM instructions WHERE manual_id=? AND opcode_norm='p'",(spec.id,))]
                connection.executemany('DELETE FROM instruction_aliases WHERE instruction_id=?',[(i,) for i in ids])
                connection.executemany('DELETE FROM instructions WHERE id=?',[(i,) for i in ids])
            if cached:
                report['manuals'][spec.id] = apply_manual_scope(connection,spec,by_id[spec.id],page_headers=cached)
                base.put_meta(connection,{'source_scope:'+spec.id:report['manuals'][spec.id]})
            if spec.id in ('jy992d48301j', 'jy992d88101e'):
                index_legacy_headings(connection, spec)
            report['manuals'][spec.id]['symbol_pages'] = index_source_symbols(connection, spec)
            report['manuals'][spec.id]['geometry_repair'] = base.backfill_instruction_geometry(connection, spec)
        index_reference_devices(connection)
        # The occurrence index is derived from all sources, including manuals
        # already present before this extension. Never retain the FX-only index.
        device_count = base.insert_device_records(connection)
        report['device_sections'] = base.insert_device_sections(connection)
        base.put_meta(connection, {'schema_version': base.SCHEMA_VERSION,
                                   'device_index_version': 'source-occurrences-v2'})
        # Cover immutable metadata filtering without reading the long source
        # text for every DISTINCT partition or CPU candidate prefilter.
        connection.execute('CREATE INDEX IF NOT EXISTS idx_chunks_source_model ON chunks(plc_models,task_types,manual_type,chunk_type,manual_id,id)')
        connection.execute('CREATE INDEX IF NOT EXISTS idx_chunks_source_lane ON chunks(manual_type,chunk_type,manual_id,plc_models,task_types,id)')
        connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        connection.execute('ANALYZE')
        connection.commit()
        integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity != 'ok' or connection.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('Extended index failed integrity checks')
        counts = {t:connection.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in
                  ['manuals','page_artifacts','chunks','entity_index','instructions','instruction_aliases','tables','error_records']}
        manuals = []
        for row in connection.execute('SELECT manual_id,manual_number,revision,published,title,manual_type,plc_models,priority,'
                                     'source_file,source_bytes,source_sha256,official_url,pdf_pages FROM manuals ORDER BY priority DESC,manual_id'):
            manuals.append(dict(zip(['id','manual_number','revision','published','title','manual_type','plc_models','priority',
                                     'file','source_bytes','sha256','official_url','pdf_pages'],row)))
            manuals[-1]['plc_models'] = [m for m in row[6].split(',') if m]
        base.put_meta(connection,{'source_manuals':manuals,'source_scope_version':'native-menu-shared-scope-v1',
                                 'vector_status':'rebuild_required_after_extension'})
        connection.commit()
    manifest = json.loads(args.source_manifest.read_text('utf8'))
    manifest['manuals'] = manuals
    # Both the original builder and the append-only importer produce manifests.
    # The latter uses counts without the historical stats/structured containers.
    manifest.setdefault('stats', {})
    manifest.setdefault('structured', {})
    manifest['counts'] = counts
    manifest['stats']['pages'] = counts['page_artifacts']
    manifest['stats']['tables'] = counts['tables']
    manifest['stats']['entities'] = counts['entity_index']
    manifest['structured']['instructions'] = counts['instructions']
    manifest['structured']['errors'] = counts['error_records']
    manifest['structured']['devices'] = device_count
    manifest['structured']['device_sections'] = report['device_sections']['device_sections']
    manifest['schema_version'] = base.SCHEMA_VERSION
    manifest['device_index_version'] = 'source-occurrences-v2'
    manifest['model_adaptation'] = {'version':'native-menu-shared-scope-v1','manuals':len(manuals),
                                   'scope_report':report['manuals'],'native_menu_targets':60,'native_concrete_models':73}
    benchmark = manifest.get('retrieval', {}).get('benchmark')
    if isinstance(benchmark, dict):
        benchmark['scope'] = 'historical FX3U benchmark; rerun required for extended index'
    manifest['vector_status'] = 'rebuild_required_after_extension'
    manifest['database_bytes'] = args.output.stat().st_size
    args.manifest.parent.mkdir(parents=True,exist_ok=True)
    args.manifest.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n','utf8')
    report.update({'counts':counts,'integrity_check':integrity,'elapsed_seconds':round(time.perf_counter()-started,2)})
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n','utf8')
    print(json.dumps(report['counts']),flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
