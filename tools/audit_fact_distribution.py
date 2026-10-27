#!/usr/bin/env python3
# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Source/category coverage and textual-overlap triage for local fact packages.

Overlap windows are review triggers, never a copyright clearance test. Counts
are source associations: a multi-source fact is counted under each source.
"""
from __future__ import annotations

import sys as _private_sys
from pathlib import Path as _PrivatePath
_private_sys.path.insert(0, str(_PrivatePath(__file__).resolve().parents[1]))
from tools.offline_inputs import private_path

import ast
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path
import re
import sqlite3
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT))


def windows(text):
    # Keep CPU/device identifiers intact; dropping digits turns unrelated model
    # inventories into artificial repeated prose such as "fx fx n fx s".
    words = re.findall(r"[a-z0-9_]+(?:'[a-z]+)?|[\u3400-\u9fff]", text.casefold())
    return (' '.join(words[i:i + 12]) for i in range(len(words) - 11))


def main():
    from plc.instruction_definition_storage import expand_definitions
    from scripts.runtime_knowledge_resources import INSTRUCTION_FILES, FILES
    migration = json.loads((private_path('research/results/rag-fact-migration-20261016.json')).read_text(encoding='utf-8'))
    with zipfile.ZipFile(private_path('research/' + 'evidence/rag-source-definitions-20261016.zip')) as archive:
        original = expand_definitions(json.loads(gzip.decompress(archive.read('instruction_definitions.json.gz'))))
    current = expand_definitions(json.loads(gzip.decompress((ROOT / 'resources/instructions/mitsubishi/instruction_definitions.json.gz').read_bytes())))
    after = {(e['target_model'], e['opcode']): e for e in current['entries']}
    coverage = defaultdict(Counter)
    missing = defaultdict(Counter)
    for entry in original['entries']:
        owner = after[entry['target_model'], entry['opcode']]
        kept = {f['id'] for f in owner['facts']}
        fallback = {s['manual_id'] for s in entry.get('source_materials', []) if s.get('manual_id')} or {'source_unresolved'}
        for fact in entry['facts']:
            sources = {s['manual_id'] for s in fact.get('sources', []) if s.get('manual_id')} or fallback
            dimension = fact.get('dimension', 'unknown')
            for source in sources:
                row = coverage[source, dimension]
                row['input'] += 1
                row['organized' if fact['id'] in kept else 'withheld'] += 1
                row['technical:' + fact.get('status', 'unknown')] += 1
                if fact.get('status') not in {'source_verified', 'not_applicable', 'conflict'}:
                    row['pending_verification'] += 1
                if fact.get('status') == 'conflict':
                    row['conflict'] += 1
        for source in fallback:
            for gap in owner.get('gaps', []):
                missing[source][gap] += 1
            if not owner['facts']:
                missing[source]['owners_without_formalized_facts'] += 1

    db = sqlite3.connect((ROOT / 'resources/knowledge/runtime/plc_facts.sqlite').as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    records = list(db.execute('SELECT id,manual_id,fact_kind,metadata_json,text FROM chunks'))
    runtime = defaultdict(Counter)
    targets = []
    for record in records:
        meta = json.loads(record['metadata_json'])
        count = runtime[record['manual_id'], record['fact_kind']]
        count['organized_records'] += 1
        count['technical:' + meta['technical_status']] += 1
        count['representation_reviewed'] += int(meta['distribution_status'] == 'fact_representation_reviewed')
        if meta['technical_status'] not in {'source_verified', 'not_applicable', 'conflict'}:
            count['pending_verification'] += 1
        if meta['technical_status'] == 'conflict':
            count['conflict'] += 1
        targets.append(('database:chunk:' + str(record['id']), record['text']))
    # Inspect shipped sidecars, derived JSON, and string constants in the actual
    # application source used to build the packages.
    paths = {ROOT / relative for relative, _ in FILES if Path(relative).suffix in {'.json', '.md'}}
    paths.update(ROOT / 'resources/instructions/mitsubishi' / name for name in INSTRUCTION_FILES)
    for path in sorted(paths):
        content = path.read_bytes()
        if path.suffix == '.gz':
            content = gzip.decompress(content)
        targets.append((path.relative_to(ROOT).as_posix(), content.decode('utf-8')))
    snapshots = ROOT / 'src'
    snapshot_paths = sorted(snapshots.rglob('*.py'))
    assert snapshot_paths, 'Application source is required for constant review'
    for path in snapshot_paths:
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                targets.append((path.relative_to(ROOT).as_posix() + ':' + str(node.lineno), node.value))
    index = defaultdict(set)
    for location, content in targets:
        for window in set(windows(content)):
            index[window].add(location)
    source = sqlite3.connect((private_path('resources/knowledge/fx3u_knowledge.sqlite')).as_uri() + '?mode=ro&immutable=1', uri=True)
    overlaps = defaultdict(lambda: {'source_chunks': set(), 'windows': set()})
    for identity, manual, content in source.execute('SELECT id,manual_id,text FROM chunks'):
        if manual.startswith('curated_') or manual == 'gxw2_skill_1_6_1':
            continue
        for window in set(windows(content)):
            for location in index.get(window, ()):
                overlaps[location]['source_chunks'].add(str(identity))
                overlaps[location]['windows'].add(window)
    reviewed, unresolved = [], []
    for location, value in sorted(overlaps.items()):
        item = dict(location=location, source_chunk_ids=sorted(value['source_chunks']), windows=sorted(value['windows']))
        if all(re.fullmatch(r'(?:l\d+s?|p|bt|pbt)', token) for window in value['windows'] for token in window.split()):
            item.update(disposition='reviewed_technical_identifier_list',
                reason='Overlaps contain only L-series CPU identifiers and suffixes; no explanatory sentence or program.')
            reviewed.append(item)
        else:
            unresolved.append(item)
    result = {
        'counting': 'Source associations; not unique fact totals. Technical states and representation review are independent.',
        'definition_unique': {
            'owners': len(after), 'input_facts': sum(len(e['facts']) for e in original['entries']),
            'organized': sum(len(e['facts']) for e in current['entries']), 'withheld': len(migration['withheld_facts'])},
        'definition_by_source_and_dimension': [dict(source=s, category=k,
            **{name: v[name] for name in ('input', 'organized', 'pending_verification', 'conflict', 'withheld')},
            **{name: count for name, count in v.items() if name.startswith('technical:')}) for (s, k), v in sorted(coverage.items())],
        'runtime_by_primary_source_and_category': [dict(source=s, category=k,
            **{name: v[name] for name in ('organized_records', 'pending_verification', 'conflict', 'representation_reviewed')},
            **{name: count for name, count in v.items() if name.startswith('technical:')}) for (s, k), v in sorted(runtime.items())],
        'unresolved_by_source': {source: dict(counts) for source, counts in sorted(missing.items())},
        'original_source_disposition': migration['source_dispositions'],
        'original_source_inventory': migration['source_inventory'],
        'overlap_review': {
            'method': 'Twelve normalized tokens (including individual CJK characters) trigger review only; no legal safe threshold.',
            'coverage': 'Every original manual chunk versus runtime chunks, allowlisted knowledge/instruction sidecars, and compiled-source string constants.',
            'compiled_source_files': len(snapshot_paths),
            'reviewed_matches': reviewed,
            'review_required': unresolved,
            'legal_clearance': 'not_established'},
    }
    output = private_path('research/results/rag-distribution-review-20261016.json')
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'coverage_receipt': str(output), 'unique_facts': result['definition_unique'],
        'source_category_rows': len(coverage), 'overlap_locations': len(overlaps),
        'unresolved_overlap_locations': len(unresolved)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
