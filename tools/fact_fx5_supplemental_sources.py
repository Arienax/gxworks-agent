"""Checked, partial supplemental PDF pages for offline FX5 source review.

PDF bytes and extracted prose remain private inputs, never bundled with this
module. The overlay copies its input SQLite connection and cannot alter it.
Each page is a new content-addressed supplemental chunk, not an original DB UID.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess


# Chinese diagram text has embedded font glyphs; its exact page hash binds the
# manually inspected rendered statement. Claim markers only locate the section.
SUPPLEMENTS = {
    'fx5_programming_zh_y': {'manual_number': 'JY997D58901',
 'revision': 'Y',
 'language': 'zh-CN',
 'pdf_pages': 1406,
 'title': 'MELSEC iQ-F FX5 编程手册（指令／通用 FUN／FB 篇）',
 'source_file': 'jy997d58901y.pdf',
 'source_sha256': '75c37ed61455684d38c9dd45353f8893fd3d48f05561ac0910c043dea5754c40',
 'official_url': 'https://www.mitsubishielectric.co.jp/dl/fa/members/document/manual/plcf/jy997d58901/jy997d58901y.pdf',
 'pages': {789: {'printed_page': '787',
 'section': '8.18 脉冲系统指令',
 'text_sha256': '1d784ace15ae759044ad90b1d6be676405c810b21b732c774558af2a0b718c68',
 'claim_markers': ['ON时间或周期不到1时']}, 783: {'printed_page': '781',
 'section': '8.18 脉冲系统指令',
 'text_sha256': '39904fe212cb8b6ca07294f8533681670dce1d29e5a5cbee04a20e25e8497f1f',
 'claim_markers': ['ON时间或周期不到1时']}, 781: {'printed_page': '779',
                 'section': '8.18 脉冲系统指令',
                 'text_sha256': 'd1c22e274de63e7fea5f0e1a9681f825ced058aed05d0642ac2c9a1b3672e0cb',
                 'claim_markers': ['输出脉冲为ON时']},
           782: {'printed_page': '780',
                 'section': '8.18 脉冲系统指令',
                 'text_sha256': '03cba78ba5e55d4f6b13a5717b8a5610a62e76c1791f2bceb98b23cc9ad3f5d3',
                 'claim_markers': ['输出脉冲为OFF时']},
           787: {'printed_page': '785',
                 'section': '8.18 脉冲系统指令',
                 'text_sha256': 'c5568a4c6cea20b36e4224f656326360fc2784512f42aafb585db2cc592e4017',
                 'claim_markers': ['输出脉冲为ON时']},
           788: {'printed_page': '786',
                 'section': '8.18 脉冲系统指令',
                 'text_sha256': 'bb6765c9790f65218753acbb1c0116007f9bcdc6a7245dd0cafa859d5c8fd372',
                 'claim_markers': ['输出脉冲为OFF时']},
           1402: {'printed_page': '1400',
                  'section': '修订记录',
                  'text_sha256': '47527a36251f1f899e8d190e3df62b18c807aecad1d349af0aac97713058ac64',
                  'claim_markers': ['日语版手册编号：JY997D54701AB', '2025年10月']}}},
    'fx5_communication_j': {
        'manual_number': 'SH-082625ENG', 'revision': 'J', 'pdf_pages': 960,
        'title': "MELSEC iQ-F FX5 User's Manual (Communication)",
        'source_file': 'sh082625engj.pdf',
        'source_sha256': 'a3b6781f99c890c84d116edb9753a8f54a7f93f5e163ce866fd687deb2e6185a',
        'official_url': 'https://www.mitsubishielectric.com/dl/fa/document/manual/plcf/sh082625eng/sh082625engj.pdf',
        'pages': {865: {'printed_page': '863', 'section': '47.3 MODBUS Serial Communication',
                       'text_sha256': 'c41796ad1f07c019f2f5f483a70146d5a27ce4355b69a8ea369355b18e811d08',
                       'claim_markers': ['ADPRW', '3600H', 'Slave mode']},
                  201: {'printed_page': '199',
                       'section': '10.1 File Transfer Specifications of File Transfer Function (FTP Client)',
                       'text_sha256': '11793918a09eacd69258c2d2f4afeb83a787821fee9871a19e69f4574ea26c8c',
                       'claim_markers': ['SP.FTPPUT', 'logging files', 'memory dump files', 'LOGGING', 'DEBUG']}},
    },
}


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _insert(connection, table, row):
    columns = [r[1] for r in connection.execute('PRAGMA table_info("%s")' % table)]
    missing = set(row) - set(columns)
    if missing:
        raise ValueError('Normalized source schema missing %s: %s' % (table, sorted(missing)))
    keys = list(row)
    connection.execute('INSERT INTO "%s" (%s) VALUES (%s)' %
                       (table, ','.join('"%s"' % k for k in keys), ','.join('?' for _ in keys)),
                       [row[k] for k in keys])


def supplemental_overlay(source, pdf_paths):
    """Return a read-only copied DB containing only explicitly supplied, pinned pages.

    pdf_paths maps approved manual IDs to actual PDF paths. Missing supplements
    are not fabricated: dependent fact construction subsequently fails closed.
    The supplemental_source_provenance table explicitly records partial coverage.
    """
    checked = []
    for manual_id, path in pdf_paths.items():
        if manual_id not in SUPPLEMENTS:
            raise ValueError('Unreviewed supplemental manual: ' + manual_id)
        expected = SUPPLEMENTS[manual_id]
        data = Path(path).read_bytes()
        if _digest(data) != expected['source_sha256']:
            raise ValueError('Supplemental PDF is missing or has changed: ' + manual_id)
        pages = {}
        for page, witness in expected['pages'].items():
            text = subprocess.check_output(['pdftotext', '-f', str(page), '-l', str(page), '-layout', str(Path(path).resolve()), '-']).decode('utf-8')
            if _digest(text.encode()) != witness['text_sha256'] or any(marker not in text for marker in witness['claim_markers']):
                raise ValueError('Supplemental page extraction/claim witness changed: %s page %s' % (manual_id, page))
            pages[page] = text
        checked.append((manual_id, expected, data, pages))
    overlay = sqlite3.connect(':memory:')
    overlay.deserialize(source.serialize())
    try:
        overlay.execute('CREATE TABLE supplemental_source_provenance (manual_id TEXT PRIMARY KEY, source_sha256 TEXT, coverage_json TEXT, extraction_method TEXT)')
        for manual_id, expected, data, pages in checked:
            if overlay.execute('SELECT 1 FROM manuals WHERE manual_id=?', (manual_id,)).fetchone():
                raise ValueError('Supplemental manual would overwrite existing source: ' + manual_id)
            manual = {k: expected[k] for k in ('manual_number', 'revision', 'title', 'source_file', 'source_sha256', 'official_url', 'pdf_pages')}
            manual.update(manual_id=manual_id, source_bytes=len(data), language=expected.get('language', 'en'), manual_type='supplemental_partial')
            _insert(overlay, 'manuals', manual)
            coverage = {'complete_manual': False, 'reviewed_physical_pages': sorted(pages), 'total_pdf_pages': expected['pdf_pages'], 'tables_normalized': False, 'geometry_normalized': False}
            overlay.execute('INSERT INTO supplemental_source_provenance VALUES (?,?,?,?)', (manual_id, expected['source_sha256'], json.dumps(coverage, sort_keys=True), 'pdftotext -layout; exact reviewed page hash and claim markers'))
            for page, text in pages.items():
                witness = expected['pages'][page]
                identity = '%s:supplemental-p%d:%s' % (manual_id, page, _digest(text.encode()))
                artifact = {'manual_id': manual_id, 'pdf_page': page, 'printed_page': witness['printed_page'], 'section': witness['section'], 'outline_path': witness['section'], 'section_key': identity, 'chunk_type': 'supplemental_page', 'plain_text': text, 'clean_text': text, 'layout_text': text, 'plain_text_sha256': _digest(text.encode()), 'fidelity_flags': 'partial_manual,layout_text,reviewed_claim_witness'}
                _insert(overlay, 'page_artifacts', artifact)
                chunk = {k: artifact[k] for k in ('manual_id', 'pdf_page', 'printed_page', 'section', 'outline_path', 'section_key', 'chunk_type', 'fidelity_flags')}
                chunk.update(chunk_uid=identity, manual_number=expected['manual_number'], revision=expected['revision'], source_file=expected['source_file'], pdf_page_end=page, printed_page_end=witness['printed_page'], text=text, text_sha256=_digest(text.encode()), char_count=len(text), source_pages_json=json.dumps([page]), page_text_sha256=_digest(text.encode()))
                _insert(overlay, 'chunks', chunk)
        overlay.commit()
        overlay.execute('PRAGMA query_only=ON')
        return overlay
    except Exception:
        overlay.close()
        raise


def validate_supplemental_page(source, declared, page):
    """Reject substituted, withdrawn or mis-revisioned supplemental DB evidence."""
    manual_id = declared['manual_id']
    if manual_id not in SUPPLEMENTS:
        return
    expected = SUPPLEMENTS[manual_id]
    witness = expected['pages'].get(page)
    if witness is None or any(declared.get(k) != expected[k] for k in ('manual_number', 'revision')):
        raise ValueError('Unreviewed supplemental source revision/page: ' + manual_id)
    try:
        manual = source.execute('SELECT manual_number,revision,source_sha256,official_url,pdf_pages FROM manuals WHERE manual_id=?', (manual_id,)).fetchone()
        provenance = source.execute('SELECT source_sha256,coverage_json FROM supplemental_source_provenance WHERE manual_id=?', (manual_id,)).fetchone()
        rows = source.execute('SELECT chunk_uid,text,text_sha256 FROM chunks WHERE manual_id=? AND pdf_page=? AND pdf_page_end=?', (manual_id, page, page)).fetchall()
    except sqlite3.Error as error:
        raise ValueError('Supplemental source provenance missing: ' + manual_id) from error
    identity = '%s:supplemental-p%d:%s' % (manual_id, page, witness['text_sha256'])
    if (not manual or tuple(manual) != tuple(expected[k] for k in ('manual_number','revision','source_sha256','official_url','pdf_pages'))
            or not provenance or provenance[0] != expected['source_sha256'] or len(rows) != 1):
        raise ValueError('Supplemental source missing or changed: ' + manual_id)
    coverage = json.loads(provenance[1])
    if coverage.get('complete_manual') is not False or coverage.get('reviewed_physical_pages') != sorted(expected['pages']):
        raise ValueError('Supplemental source coverage changed: ' + manual_id)
    uid, text, text_hash = rows[0]
    if uid != identity or text_hash != witness['text_sha256'] or _digest(text.encode()) != text_hash or any(marker not in text for marker in witness['claim_markers']):
        raise ValueError('Supplemental source claim changed: ' + manual_id)



def validate_source_declaration(declared):
    if (not isinstance(declared, dict)
            or any(not isinstance(declared.get(key), str) or not declared[key].strip() for key in ('manual_id', 'manual_number', 'revision'))
            or not isinstance(declared.get('pdf_pages'), list) or not declared['pdf_pages']
            or any(type(page) is not int or page < 1 for page in declared['pdf_pages'])):
        raise ValueError('Malformed additional source declaration')


def bind_condition_source_catalog(specs, catalog):
    """Resolve reviewed declarations without granting trust to citation notes.

    An absent catalog is unknown evidence, not permission to claim verification.
    This is deliberately shared by source-only inventory and authored builds.
    """
    for spec in specs:
        if not spec.get('additional_sources'):
            continue
        references, missing = [], []
        for declared in spec['additional_sources']:
            try:
                validate_source_declaration(declared)
            except ValueError:
                missing.append(declared)
                continue
            for page in declared['pdf_pages']:
                matches = catalog.get('%s:%s' % (declared['manual_id'], page), [])
                if not matches or any(any(ref.get(k) != declared.get(k) for k in ('manual_id','manual_number','revision')) for ref in matches):
                    missing.append(dict(declared, pdf_pages=[page]))
                else:
                    references.extend(ref for ref in matches if ref not in references)
        spec['additional_source_references'] = references
        if missing:
            spec['missing_additional_sources'] = missing
            if spec['status'] != 'conflict':
                spec['status'] = 'declared_unverified'
            value = spec['value']
            value['full_source_relation_complete'] = False
            value['context_binding'] = 'not_fully_resolved'
            value['source_evidence_status'] = 'missing_or_changed_additional_source'
            value['unresolved_items'] = list(dict.fromkeys([*value.get('unresolved_items', []), 'additional_source_evidence']))
    return specs


def supplemental_reference_metadata(reference):
    """Non-prose provenance retained in authored/public source references."""
    expected = SUPPLEMENTS.get(reference['manual_id'])
    if expected is None:
        return {}
    pages = reference['pdf_pages']
    return {'source_kind': 'supplemental_pdf_page',
            'pdf_sha256': expected['source_sha256'],
            'page_text_sha256': {str(page): expected['pages'][page]['text_sha256'] for page in pages if page in expected['pages']},
            'coverage': {'complete_manual': False, 'reviewed_physical_pages': sorted(expected['pages']), 'total_pdf_pages': expected['pdf_pages']},
            'official_url': expected['official_url']}
