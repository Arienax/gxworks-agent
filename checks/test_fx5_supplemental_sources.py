"""Provenance boundary regressions; private PDF integration is separately replayed."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from tools import fact_fx5_supplemental_sources as supplements
from tools.fact_fx5_programming_residual import (
    _additional_source_references, _condition_source_references,
    _cross_manual_source_catalog, _fact, _partition_condition_owners, validate_condition_source_inventory)
from knowledge.fact_corpus import project_definition


DECLARED = {'manual_id': 'fx5_communication_j', 'manual_number': 'SH-082625ENG', 'revision': 'J', 'pdf_pages': [201]}


def database():
    connection = sqlite3.connect(':memory:')
    connection.execute('CREATE TABLE manuals (manual_id,manual_number,revision,title,source_file,source_sha256,official_url,pdf_pages,source_bytes,language,manual_type)')
    connection.execute('CREATE TABLE chunks (chunk_uid,manual_id,pdf_page,pdf_page_end,printed_page,printed_page_end,section,outline_path,section_key,chunk_type,fidelity_flags,manual_number,revision,source_file,text,text_sha256,char_count,source_pages_json,page_text_sha256)')
    connection.execute('CREATE TABLE page_artifacts (manual_id,pdf_page,printed_page,section,outline_path,section_key,chunk_type,plain_text,clean_text,layout_text,plain_text_sha256,fidelity_flags)')
    return connection


def spec():
    return {'key': 'condition_example', 'status': 'source_verified', 'dimension': 'constraints.documented_errors',
            'additional_sources': [copy.deepcopy(DECLARED)],
            'value': {'rules': [{'kind': 'source_domain', 'permitted_roots': ['LOGGING', 'DEBUG']}],
                      'full_source_relation_complete': True, 'context_binding': 'resolved', 'unresolved_items': []}}


class SupplementalSourceTests(unittest.TestCase):
    def checked_overlay(self):
        # A synthetic pinned document exercises code mechanics, not manual truth.
        data = b'%PDF-unit-fixture'
        text = 'SP.FTPPUT: LOGGING DEBUG\n'
        registry = copy.deepcopy(supplements.SUPPLEMENTS)
        manual = registry[DECLARED['manual_id']]
        manual['source_sha256'] = hashlib.sha256(data).hexdigest()
        manual['pages'] = {201: manual['pages'][201]}
        manual['pages'][201]['text_sha256'] = hashlib.sha256(text.encode()).hexdigest()
        manual['pages'][201]['claim_markers'] = ['SP.FTPPUT', 'LOGGING DEBUG']
        self.patch_registry = patch.dict(supplements.SUPPLEMENTS, registry)
        self.patch_registry.start(); self.addCleanup(self.patch_registry.stop)
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'source.pdf'; path.write_bytes(data)
        original = database(); self.addCleanup(original.close)
        with patch.object(supplements.subprocess, 'check_output', return_value=text.encode()):
            overlay = supplements.supplemental_overlay(original, {DECLARED['manual_id']: path})
        self.addCleanup(overlay.close)
        self.assertEqual(original.execute('SELECT count(*) FROM manuals').fetchone()[0], 0)
        return overlay

    def test_genuine_distinct_chunk_identity_and_partial_coverage(self):
        overlay = self.checked_overlay()
        refs = _additional_source_references(overlay, {'additional_sources': [DECLARED]}, {})
        self.assertEqual(len(refs), 1)
        self.assertTrue(refs[0]['id'].startswith('fx5_communication_j:supplemental-p201:'))
        self.assertEqual(refs[0]['manual_number'], 'SH-082625ENG')
        self.assertEqual(refs[0]['pdf_pages'], [201])
        coverage = json.loads(overlay.execute('SELECT coverage_json FROM supplemental_source_provenance').fetchone()[0])
        self.assertFalse(coverage['complete_manual'])
        self.assertEqual(coverage['reviewed_physical_pages'], [201])
        with self.assertRaises(sqlite3.OperationalError):
            overlay.execute('DELETE FROM chunks')

    def test_source_only_fact_and_public_projection_keep_same_evidence(self):
        overlay = self.checked_overlay()
        claim = spec()
        validate_condition_source_inventory(overlay, [claim])
        refs = _condition_source_references(overlay, claim, {})
        fact = _fact('sample', claim['dimension'], claim['value'], refs, ['FX5U'], status=claim['status'])
        projected, withheld = project_definition({'opcode': 'SP.FTPPUT', 'facts': [fact]})
        self.assertEqual(fact['sources'], claim['additional_source_references'])
        self.assertEqual(projected['facts'][0]['sources'], fact['sources'])
        self.assertEqual(fact['status'], 'source_verified')

    def test_missing_manual_downgrades_source_only_and_published(self):
        source = database(); self.addCleanup(source.close)
        claim = spec()
        validate_condition_source_inventory(source, [claim])
        self.assertEqual(claim['status'], 'declared_unverified')
        self.assertFalse(claim['value']['full_source_relation_complete'])
        self.assertEqual(_condition_source_references(source, claim, {}), [])
        self.assertEqual(claim['missing_additional_sources'], [DECLARED])
        with self.assertRaises(ValueError):
            _additional_source_references(source, {'additional_sources': [DECLARED]}, {})

    def test_absent_catalog_does_not_trust_review_note(self):
        claim = spec()
        supplements.bind_condition_source_catalog([claim], {})
        self.assertEqual(claim['status'], 'declared_unverified')

    def test_wrong_revision_downgrades(self):
        source = self.checked_overlay(); claim = spec()
        claim['additional_sources'][0]['revision'] = 'F'
        validate_condition_source_inventory(source, [claim])
        self.assertEqual(claim['status'], 'declared_unverified')
        with self.assertRaises(ValueError):
            _additional_source_references(source, claim, {})

    def test_wrong_pdf_bytes_rejected_before_overlay(self):
        source = database(); self.addCleanup(source.close)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wrong.pdf'; path.write_bytes(b'wrong revision')
            with self.assertRaisesRegex(ValueError, 'PDF.*changed'):
                supplements.supplemental_overlay(source, {DECLARED['manual_id']: path})
        self.assertEqual(source.execute('SELECT count(*) FROM manuals').fetchone()[0], 0)

    def test_withdrawal_and_source_hash_and_claim_tampering_rejected(self):
        for change in ['DELETE FROM supplemental_source_provenance',
                       "UPDATE manuals SET source_sha256='changed'",
                       "UPDATE chunks SET text='changed claim'",
                       "UPDATE chunks SET revision='F'",
                       "UPDATE supplemental_source_provenance SET coverage_json='{}'"]:
            with self.subTest(change=change):
                source = self.checked_overlay()
                source.execute('PRAGMA query_only=OFF'); source.execute(change)
                claim = spec(); validate_condition_source_inventory(source, [claim])
                self.assertEqual(claim['status'], 'declared_unverified')
                with self.assertRaises(ValueError):
                    _additional_source_references(source, claim, {})

    def test_malformed_source_declarations_never_verify(self):
        source = database(); self.addCleanup(source.close)
        for bad in [dict(DECLARED, pdf_pages=[]), dict(DECLARED, pdf_pages=[True]),
                    dict(DECLARED, pdf_pages=[0]), dict(DECLARED, pdf_pages='201'),
                    dict(DECLARED, manual_id=''), dict(DECLARED, revision=None)]:
            claim = spec(); claim['additional_sources'] = [bad]
            supplements.bind_condition_source_catalog([claim], {})
            self.assertEqual(claim['status'], 'declared_unverified')
            with self.assertRaises(ValueError):
                _additional_source_references(source, claim, {})
            self.assertEqual(_condition_source_references(source, claim, {}), [])

    def test_existing_ag_cross_manual_reference_uses_real_chunk_and_revision(self):
        source = database(); self.addCleanup(source.close)
        source.execute("INSERT INTO chunks(chunk_uid,manual_id,pdf_page,pdf_page_end,manual_number,revision) VALUES (?,?,?,?,?,?)",
                       ('actual-ag-chunk', 'fx5_application_ag', 935, 935, 'JY997D55401', 'AG'))
        claim = spec(); claim['additional_sources'] = [{'manual_id': 'fx5_application_ag', 'manual_number': 'JY997D55401', 'revision': 'AG', 'pdf_pages': [935]}]
        validate_condition_source_inventory(source, [claim])
        self.assertEqual(claim['status'], 'source_verified')
        self.assertEqual(claim['additional_source_references'][0]['id'], 'actual-ag-chunk')
        source.execute("UPDATE chunks SET revision='AF'")
        withdrawn = spec(); withdrawn['additional_sources'] = claim['additional_sources']
        validate_condition_source_inventory(source, [withdrawn])
        self.assertEqual(withdrawn['status'], 'declared_unverified')
        self.assertEqual(_condition_source_references(source, withdrawn, {}), [])

    def test_same_manual_additional_chapters_are_verified_and_deduplicated(self):
        source = database(); self.addCleanup(source.close)
        source.execute("INSERT INTO chunks(chunk_uid,manual_id,pdf_page,pdf_page_end,manual_number,revision) VALUES (?,?,?,?,?,?)",
                       ('actual-ab-common-real', 'fx5_programming_ab', 34, 35, 'JY997D55801', 'AB'))
        declaration = {'manual_id': 'fx5_programming_ab', 'manual_number': 'JY997D55801', 'revision': 'AB', 'pdf_pages': [34, 35]}
        claim = spec(); claim['additional_sources'] = [declaration]
        validate_condition_source_inventory(source, [claim])
        self.assertEqual(claim['status'], 'source_verified')
        self.assertEqual(len(claim['additional_source_references']), 1)
        self.assertEqual(claim['additional_source_references'][0]['id'], 'actual-ab-common-real')
        self.assertEqual(_condition_source_references(source, claim, {}), claim['additional_source_references'])
        for invalid in [dict(declaration, pdf_pages=[36]), dict(declaration, revision='AA')]:
            changed = spec(); changed['additional_sources'] = [invalid]
            validate_condition_source_inventory(source, [changed])
            self.assertEqual(changed['status'], 'declared_unverified')
            self.assertEqual(_condition_source_references(source, changed, {}), [])
        source.execute('DELETE FROM chunks')
        withdrawn = spec(); withdrawn['additional_sources'] = [declaration]
        validate_condition_source_inventory(source, [withdrawn])
        self.assertEqual(withdrawn['status'], 'declared_unverified')
        self.assertEqual(_condition_source_references(source, withdrawn, {}), [])

    def test_related_owner_keeps_same_verified_cross_manual_refs(self):
        source = self.checked_overlay(); claim = spec()
        claim['scope_forms'] = ['SOURCE_FORM', 'RELATED_FORM']
        claim['value']['complete_clause_review'] = True
        validate_condition_source_inventory(source, [claim])
        own, related = _partition_condition_owners([claim], 'SOURCE_FORM', ['SOURCE_FORM'])
        for variant in [own[0], related['RELATED_FORM'][0]]:
            references = _condition_source_references(source, variant, {})
            self.assertEqual(references, claim['additional_source_references'])
            fact = _fact(variant['key'], variant['dimension'], variant['value'], references, ['FX5U'], status=variant['status'])
            projected, withheld = project_definition({'opcode': variant['scope_forms'][0], 'facts': [fact]})
            self.assertEqual(withheld, [])
            self.assertEqual(projected['facts'][0]['sources'], references)

    def test_chinese_y_overlay_preserves_language_and_checks_revision_witness(self):
        data = b'%PDF-synthetic-chinese-Y'
        text = 'Chinese diagram fixture and revision fixture'
        manual_id = 'fx5_programming_zh_y'
        registry = copy.deepcopy(supplements.SUPPLEMENTS)
        manual = registry[manual_id]
        manual['source_sha256'] = hashlib.sha256(data).hexdigest()
        for witness in manual['pages'].values():
            witness['text_sha256'] = hashlib.sha256(text.encode()).hexdigest()
            witness['claim_markers'] = ['fixture']
        source = database(); self.addCleanup(source.close)
        with tempfile.TemporaryDirectory() as directory, patch.dict(supplements.SUPPLEMENTS, registry):
            path = Path(directory) / 'source.pdf'; path.write_bytes(data)
            with patch.object(supplements.subprocess, 'check_output', return_value=text.encode()):
                overlay = supplements.supplemental_overlay(source, {manual_id: path})
            self.addCleanup(overlay.close)
            self.assertEqual(overlay.execute('SELECT language FROM manuals').fetchone()[0], 'zh-CN')
            declaration = {'manual_id': manual_id, 'manual_number': 'JY997D58901', 'revision': 'Y', 'pdf_pages': [781, 1402]}
            claim = spec(); claim['additional_sources'] = [declaration]
            validate_condition_source_inventory(overlay, [claim])
            self.assertEqual(claim['status'], 'source_verified')
            self.assertEqual(len(claim['additional_source_references']), 2)
            for revision in ('J', 'X', 'AB'):
                changed = spec(); changed['additional_sources'] = [dict(declaration, revision=revision)]
                validate_condition_source_inventory(overlay, [changed])
                self.assertEqual(changed['status'], 'declared_unverified')
            overlay.execute('PRAGMA query_only=OFF')
            overlay.execute('DELETE FROM chunks WHERE pdf_page=1402')
            changed = spec(); changed['additional_sources'] = [declaration]
            validate_condition_source_inventory(overlay, [changed])
            self.assertEqual(changed['status'], 'declared_unverified')

    def test_chinese_y_source_bytes_or_rendered_page_witness_changes_fail_closed(self):
        source = database(); self.addCleanup(source.close)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.pdf'; path.write_bytes(b'altered Chinese PDF')
            with self.assertRaisesRegex(ValueError, 'PDF.*changed'):
                supplements.supplemental_overlay(source, {'fx5_programming_zh_y': path})

    def test_unreviewed_manual_not_accepted(self):
        source = database(); self.addCleanup(source.close)
        with self.assertRaisesRegex(ValueError, 'Unreviewed'):
            supplements.supplemental_overlay(source, {'arbitrary_source': '/not/read'})


if __name__ == '__main__':
    unittest.main()
