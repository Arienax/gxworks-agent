import gzip
import io
import json
from pathlib import Path
import tempfile
import sqlite3
import subprocess
import sys
import unittest
from tools.audit_runtime_fact_package import JSONUnits, audit_package, audit_sqlite, audit_owner_store
from plc.instruction_definition_storage import compact_definitions


class RuntimePackageAudit(unittest.TestCase):
    def sample(self):
        return {'schema_version': 1, 'entries': [
            {'vendor': 'mitsubishi', 'target_model': model, 'opcode': 'X', 'facts': [
                {'id': 'a', 'dimension': 'constraints.example', 'status': 'unknown',
                 'value': {'literal': 'private example'}, 'sources': [{'id': 's'}]},
                {'id': 'b', 'dimension': 'constraints.other', 'status': 'conflict',
                 'depends_on': ['a'], 'value': {'n': 2}, 'sources': [{'id': 's'}]}]}
            for model in ['FX5U', 'FX5S']]}

    def audit(self, payload):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'data.gz'
            p.write_bytes(gzip.compress(json.dumps(payload).encode()))
            return audit_package(p)

    def test_owner_breakdown_distinguishes_empty_and_source_backed(self):
        payload = self.sample()
        payload['entries'][0].update(call_available=True, support_status='supported', source_materials=[{'manual_id': 'M'}])
        payload['entries'][0]['facts'][0].update(id='fx5-residual:test', scope={'models': ['FX5U'], 'forms': ['X']})
        payload['entries'].append({'target_model': 'Q00J', 'opcode': 'X', 'call_available': False,
                                   'support_status': 'unsupported', 'facts': []})
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'p.json'
            p.write_text(json.dumps(payload))
            report = audit_package(p, owner_breakdown=True)['owner_provenance']
        q = report['by_model']['Q00J']
        self.assertEqual(q['counts']['empty_owners'], 1)
        self.assertEqual(q['counts']['owners_with_neither_source_materials_nor_fact_sources'], 1)
        self.assertEqual(q['call_available']['false'], 1)
        fx = report['by_model']['FX5U']
        self.assertEqual(fx['counts']['owners_with_fact_source_references'], 1)
        self.assertEqual(fx['counts']['owners_with_source_materials'], 1)
        self.assertEqual(fx['fact_namespace_instances']['fx5-residual'], 1)
        self.assertNotIn('fx5-residual:test', json.dumps(report))

    def test_fx5_namespace_on_q_is_observation_not_invalidity(self):
        payload = {'schema_version': 1, 'entries': [{'target_model': 'Q00J', 'opcode': 'X', 'facts': [
            {'id': 'fx5-review:private-full-identity', 'value': {}, 'scope': {'models': ['Q00J'], 'forms': ['X']},
             'sources': [{'manual_id': 'jy997d55801ab'}]}]}]}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'p.json'
            p.write_text(json.dumps(payload))
            report = audit_package(p, owner_breakdown=True)['owner_provenance']
        q = report['by_model']['Q00J']
        self.assertEqual(q['fact_namespace_instances']['fx5-review'], 1)
        self.assertEqual(q['counts']['facts_models_scope_excludes_owner'], 0)
        self.assertEqual(q['source_manual_reference_occurrences']['jy997d55801ab'], 1)
        self.assertNotIn('private-full-identity', json.dumps(report))

    def test_owner_breakdown_bounds_and_scope_exclusion(self):
        from tools.audit_runtime_fact_package import OwnerBreakdown
        bins = OwnerBreakdown()
        for n in range(110):
            bins.owner({'target_model': 'CPU' + str(n), 'opcode': 'X', 'facts': [
                {'id': 'ns' + str(i) + ':secret', 'scope': {'models': ['OTHER'], 'forms': ['X']},
                 'sources': [{'manual_id': 'm' + str(i)}]} for i in range(35)]})
        report = bins.report()
        self.assertLessEqual(len(report['by_model']), 97)
        self.assertLess(len(json.dumps(report, indent=2).encode()), 1024 * 1024)
        self.assertEqual(report['by_model']['CPU0']['counts']['facts_models_scope_excludes_owner'], 35)
        self.assertIn('<overflow>', report['by_model']['CPU0']['fact_namespace_instances'])

    def admission_report(self, **fields):
        from tools.audit_runtime_fact_package import OwnerBreakdown
        bins = OwnerBreakdown()
        bins.owner({'target_model': 'FX5U', 'opcode': 'X', 'facts': [], **fields})
        return bins.report()['by_model']['FX5U']

    def test_legacy_admission_is_missing_not_inferred_from_false(self):
        r = self.admission_report(call_available=False)
        self.assertEqual(r['call_available'], {'false': 1})
        self.assertEqual(r['call_admission_reason'], {'<missing>': 1})
        self.assertEqual(r['call_admission_details_state'], {'<missing>': 1})
        self.assertEqual(r['call_admission_declared_scope'], {'<missing>': 1})
        self.assertEqual(r['call_admission_replacement_opcode_state'], {'<missing>': 1})

    def test_all_declared_compiler_admission_reasons_counted_literally(self):
        from tools.audit_runtime_fact_package import OwnerBreakdown
        for reason in OwnerBreakdown.ADMISSION_REASONS:
            with self.subTest(reason=reason):
                r = self.admission_report(call_admission_reason=reason)
                self.assertEqual(r['call_admission_reason'], {reason: 1})
                self.assertEqual(r['call_available'], {'<missing>': 1})

    def test_admission_empty_scope_is_not_promoted_to_cpu_support(self):
        r = self.admission_report(call_available=True,
            call_admission_reason='admitted_with_empty_declared_scope',
            call_admission_details={'declared_scope': 'empty'})
        self.assertEqual(r['call_admission_declared_scope'], {'empty': 1})
        self.assertEqual(r['support_status'], {'<missing>': 1})
        self.assertEqual(r['counts']['empty_owners'], 1)

    def test_admission_replacement_summary_does_not_expose_identity_or_prose(self):
        r = self.admission_report(call_available=False,
            call_admission_reason='cpu_replacement_required',
            call_admission_details={'declared_scope': 'explicit',
                'replacement_opcode': 'PRIVATE_REPLACEMENT',
                'private_note': 'Never export this source text'})
        self.assertEqual(r['call_admission_replacement_opcode_state'], {'technical-token-present': 1})
        self.assertEqual(r['counts']['owners_with_unreported_admission_detail_fields'], 1)
        for text in ['PRIVATE_REPLACEMENT', 'private_note', 'Never export']:
            self.assertNotIn(text, json.dumps(r))

    def test_admission_invalid_annotations_fail_closed_without_free_text(self):
        r = self.admission_report(call_admission_reason='Confidential source sentence',
            call_admission_details={'declared_scope': 'another secret', 'replacement_opcode': ['PRIVATE']})
        self.assertEqual(r['call_admission_reason'], {'<other-or-invalid>': 1})
        self.assertEqual(r['call_admission_declared_scope'], {'<other-or-invalid>': 1})
        self.assertEqual(r['call_admission_replacement_opcode_state'], {'<other-or-invalid>': 1})
        self.assertNotIn('secret', json.dumps(r))
        self.assertNotIn('Confidential', json.dumps(r))

    def test_admission_non_object_details_not_reported_as_absent(self):
        for value in [None, 'private prose', [], False]:
            with self.subTest(value=value):
                r = self.admission_report(call_admission_reason=None, call_admission_details=value)
                self.assertEqual(r['call_admission_reason'], {'<other-or-invalid>': 1})
                self.assertEqual(r['call_admission_details_state'], {'<non-object>': 1})
                self.assertEqual(r['call_admission_declared_scope'], {'<non-object-details>': 1})
                self.assertNotIn('private prose', json.dumps(r))

    def test_admission_breakdown_flat_and_compact_agree(self):
        payload = self.sample()
        payload['entries'][0].update(call_available=False,
            call_admission_reason='target_not_in_declared_scope',
            call_admission_details={'declared_scope': 'explicit'})
        reports = []
        with tempfile.TemporaryDirectory() as d:
            for n, value in enumerate([payload, compact_definitions(payload)]):
                p = Path(d) / (str(n) + '.json')
                p.write_text(json.dumps(value))
                reports.append(audit_package(p, owner_breakdown=True)['owner_provenance'])
        self.assertEqual(reports[0], reports[1])

    def test_flat_counts_distinguish_owner_instances(self):
        r = self.audit(self.sample())
        self.assertEqual(r['counts']['owner_qualified_fact_instances'], 4)
        self.assertEqual(r['counts']['distinct_bare_fact_ids'], 2)
        self.assertEqual(r['counts']['source_reference_occurrences'], 4)
        self.assertEqual(r['counts']['unique_exact_source_reference_objects'], 1)
        self.assertNotIn('private example', json.dumps(r))
        self.assertEqual(r['counts'].get('dependencies_not_found_within_owner', 0), 0)

    def test_diff_format_agrees_with_flat(self):
        a, b = self.audit(self.sample()), self.audit(compact_definitions(self.sample()))
        self.assertEqual(a['counts'], b['counts'])
        self.assertEqual(a['status_counts'], b['status_counts'])

    def test_metadata_may_follow_large_array(self):
        payload = self.sample()
        payload = {'entries': payload['entries'], 'schema_version': 1}
        self.assertEqual(self.audit(payload)['counts']['owners'], 2)

    def test_size_guard(self):
        parser = JSONUnits(io.StringIO('{"entries":[{"value":"' + 'x' * 500 + '"}]}'), 100)
        with self.assertRaisesRegex(ValueError, 'max-unit'):
            list(parser.fields())

    def test_trailing_and_duplicate_root_rejected(self):
        for raw in ['{"entries":[]} garbage', '{"entries":[],"entries":[]}']:
            with self.assertRaises(ValueError):
                list(JSONUnits(io.StringIO(raw)).fields())

    def test_duplicate_owner_rejected(self):
        p = self.sample(); p['entries'].append(p['entries'][0])
        with self.assertRaisesRegex(ValueError, 'Duplicate definition owner'):
            self.audit(p)

    def test_unsupported_format_not_guessed(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            self.audit({'schema_version': 77, 'entries': []})


    def test_sqlite_occurrences_not_definition_instances(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'chunks.sqlite'
            db = sqlite3.connect(p)
            db.execute('CREATE TABLE chunks (id TEXT, text TEXT)')
            entry = self.sample()['entries'][0]
            for n in range(3):
                db.execute('INSERT INTO chunks VALUES (?,?)', (str(n), json.dumps(entry)))
            db.execute('INSERT INTO chunks VALUES (?,?)', ('ref', json.dumps({'runtime_owner': ['m', 'FX5U', 'X']})))
            db.execute('INSERT INTO chunks VALUES (?,?)', ('prose', 'private raw text'))
            db.commit(); db.close()
            before = p.read_bytes()
            report = audit_sqlite(p)
            self.assertEqual(report['counts']['chunk_fact_occurrences'], 6)
            self.assertEqual(report['counts']['distinct_owner_qualified_fact_ids_in_chunks'], 2)
            self.assertEqual(report['counts']['runtime_owner_reference_chunks'], 1)
            self.assertEqual(report['counts']['non_json_chunk_rows'], 1)
            self.assertEqual(before, p.read_bytes())
            self.assertNotIn('private raw text', json.dumps(report))

    def test_pooled_transport_rejected_instead_of_miscounted(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'pooled.json'
            p.write_text(json.dumps({'schema_version': 1, 'entries': [], 'source_serialization': 'fact-source-pool-v1'}))
            with self.assertRaisesRegex(ValueError, 'Pooled runtime transport'):
                audit_package(p)

    def test_sqlite_search_tokens_with_owner_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'normalized.sqlite'
            c = sqlite3.connect(p)
            c.execute('CREATE TABLE chunks(text TEXT, metadata_json TEXT)')
            c.execute('INSERT INTO chunks VALUES (?,?)', ('PWM FX5U range', json.dumps({'runtime_owner': 'mitsubishi:FX5U:PWM'})))
            c.commit()
            c.close()
            report = audit_sqlite(p)
            self.assertEqual(report['counts']['runtime_owner_reference_chunks'], 1)
            self.assertEqual(report['counts']['non_json_chunk_rows'], 1)
            self.assertEqual(report['counts'].get('chunk_fact_occurrences', 0), 0)

    def test_sqlite_missing_schema_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'empty.sqlite'
            sqlite3.connect(p).close()
            with self.assertRaisesRegex(ValueError, 'no chunks'):
                audit_sqlite(p)

    def test_owner_manifest_audit_matches_legacy_counts_and_is_read_only(self):
        from checks.test_instruction_owner_store import build
        with tempfile.TemporaryDirectory() as d:
            raw, database, manifest = build(d)
            before = database.read_bytes(), manifest.read_bytes()
            legacy = Path(d) / 'legacy.json'
            legacy.write_text(json.dumps(raw))
            old = audit_package(legacy, owner_breakdown=True)
            new = audit_owner_store(manifest, owner_breakdown=True)
            self.assertEqual(new['counts'], old['counts'])
            self.assertEqual(new['owner_provenance'], old['owner_provenance'])
            self.assertEqual(new['store_validation_stats']['hydrated_owner_count'], 2)
            self.assertTrue(new['offline_full_owner_scan'])
            self.assertEqual(before, (database.read_bytes(), manifest.read_bytes()))
            self.assertNotIn('synthetic:owner-test', json.dumps(new))

    def test_owner_manifest_digest_failure_is_not_a_partial_success(self):
        from checks.test_instruction_owner_store import build
        with tempfile.TemporaryDirectory() as d:
            _, database, manifest = build(d)
            data = json.loads(manifest.read_text())
            data['database']['sha256'] = '0' * 64
            manifest.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'integrity'):
                audit_owner_store(manifest)

    def test_owner_manifest_missing_source_rejected_even_with_new_database_hash(self):
        from checks.test_instruction_owner_store import build, refresh_manifest
        with tempfile.TemporaryDirectory() as d:
            _, database, manifest = build(d)
            with sqlite3.connect(database) as connection:
                connection.execute('DELETE FROM runtime_sources')
            refresh_manifest(database, manifest)
            with self.assertRaisesRegex(ValueError, 'source'):
                audit_owner_store(manifest)

    def test_owner_manifest_size_guard_is_explicitly_after_hydration(self):
        from checks.test_instruction_owner_store import build
        with tempfile.TemporaryDirectory() as d:
            _, _, manifest = build(d)
            with self.assertRaisesRegex(ValueError, 'Hydrated owner exceeds max-unit'):
                audit_owner_store(manifest, max_unit_bytes=1)

    def test_owner_manifest_cli_is_available_without_legacy_bundle(self):
        from checks.test_instruction_owner_store import build
        with tempfile.TemporaryDirectory() as d:
            _, _, manifest = build(d)
            output = Path(d) / 'audit.json'
            script = Path(__file__).resolve().parents[1] / 'tools/audit_runtime_fact_package.py'
            completed = subprocess.run([sys.executable, str(script), '--owner-manifest', str(manifest),
                '--owner-breakdown', '--output', str(output)], capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(output.read_text())
            self.assertEqual(result['owner_store']['counts']['owners'], 2)
            self.assertNotIn('definitions', result)


if __name__ == '__main__':
    unittest.main()
