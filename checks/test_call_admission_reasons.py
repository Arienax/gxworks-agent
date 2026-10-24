"""CPU admission explanations do not change Registry call admission."""
import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from knowledge.instruction_compiler import call_admission_explanation, compile_instruction_definitions
from plc.instructions import InstructionSpec, InstructionRegistry


class CallAdmissionReasonsTests(unittest.TestCase):
    def test_reason_matrix_matches_existing_boolean(self):
        cases = [
            (None, 'form_not_registered', False),
            (InstructionSpec('X', cpu_support=frozenset({'FX3U'})), 'admitted', True),
            (InstructionSpec('X', cpu_support=frozenset({'FX5U'})), 'target_not_in_declared_scope', False),
            (InstructionSpec('X'), 'admitted_with_empty_declared_scope', True),
            (InstructionSpec('X', cpu_replacements=(('FX3U', 'Y'),)), 'cpu_replacement_required', False),
            (InstructionSpec('X', cpu_support=frozenset({'FX3U'}),
                             cpu_replacements=(('FX3U', 'Y'),)), 'cpu_replacement_required', False),
        ]
        for spec, expected, boolean in cases:
            with self.subTest(reason=expected, spec=spec):
                form = SimpleNamespace(spec=spec) if spec else None
                before = bool(form is not None and form.spec.supports_cpu('FX3U'))
                reason, details = call_admission_explanation(form, 'FX3U')
                self.assertEqual(reason, expected)
                self.assertEqual(before, boolean)
                self.assertEqual(bool(form is not None and form.spec.supports_cpu('FX3U')), before)
                if reason == 'cpu_replacement_required':
                    self.assertEqual(details['replacement_opcode'], 'Y')
                if reason == 'admitted_with_empty_declared_scope':
                    self.assertEqual(details, {'declared_scope': 'empty'})

    def test_effective_cpu_override_and_normalized_model(self):
        base = InstructionSpec('X', cpu_support=frozenset({'FX5U'}))
        actual = InstructionSpec('X', cpu_support=frozenset({'FX3U'}))
        registry = InstructionRegistry([base])
        registry._cpu_contracts[('mitsubishi', 'X', 'FX3U')] = actual
        form = registry.resolve_form('X', cpu='fx3u')
        self.assertEqual(call_admission_explanation(form, ' fx3u ')[0], 'admitted')
        self.assertFalse(base.supports_cpu('FX3U'))

    def test_real_compiler_keeps_original_booleans_and_database(self):
        specs = [InstructionSpec('SYNTH_A', cpu_support=frozenset({'FX3U'})),
                 InstructionSpec('SYNTH_B', cpu_support=frozenset({'FX5U'})),
                 InstructionSpec('SYNTH_OPEN'),
                 InstructionSpec('SYNTH_OLD', cpu_replacements=(('FX3U', 'SYNTH_A'),))]
        registry = InstructionRegistry(specs)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'sources.sqlite'
            with sqlite3.connect(path) as db:
                db.executescript('CREATE TABLE chunks(id INTEGER,manual_id TEXT);'
                                 'CREATE TABLE instructions(manual_id TEXT);'
                                 'CREATE TABLE tables(manual_id TEXT,pdf_page INTEGER,table_index INTEGER);'
                                 'CREATE TABLE page_artifacts(manual_id TEXT,pdf_page INTEGER);')
            before = path.read_bytes()
            payload, report = compile_instruction_definitions(path, {'manuals': []}, registry)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(report['database_mutated'])
            self.assertEqual(len(payload['entries']), 8)
            for entry in payload['entries']:
                form = registry.resolve_form(entry['opcode'], cpu=entry['target_model'])
                expected = bool(form is not None and form.spec.supports_cpu(entry['target_model']))
                self.assertEqual(entry['call_available'], expected)
                self.assertEqual(entry['call_admission_reason'], call_admission_explanation(form, entry['target_model'])[0])
                self.assertNotIn('project_support', entry['call_admission_details'])

    def test_admission_metadata_survives_publication_roundtrip(self):
        from knowledge.fact_corpus import project_definition
        from knowledge.runtime_fact_projection import project_runtime_definitions, intern_runtime_sources, expand_runtime_sources
        from plc.instruction_definition_storage import compact_definitions, expand_definitions
        original = {'vendor':'mitsubishi', 'opcode':'SYNTH_OLD', 'target_model':'FX3U',
                    'call_available':False, 'call_admission_reason':'cpu_replacement_required',
                    'call_admission_details':{'declared_scope':'empty', 'replacement_opcode':'SYNTH_A'},
                    'facts':[]}
        projected, rejected = project_definition(original)
        self.assertFalse(rejected)
        raw = {'schema_version':1, 'entries':[projected]}
        semantic, _, _ = project_runtime_definitions(raw)
        restored = expand_runtime_sources(intern_runtime_sources(semantic, intern_values=True))
        restored = expand_definitions(compact_definitions(restored))
        entry = restored['entries'][0]
        for key in ('call_available','call_admission_reason','call_admission_details'):
            self.assertEqual(entry[key],original[key])

    def test_legacy_bundle_without_reasons_still_projects(self):
        from knowledge.fact_corpus import project_definition
        original = {'opcode':'SYNTH_A', 'target_model':'FX3U', 'call_available':False, 'facts':[]}
        before = copy.deepcopy(original)
        projected, _ = project_definition(original)
        self.assertFalse(projected['call_available'])
        self.assertNotIn('call_admission_reason', projected)
        self.assertEqual(original, before)


if __name__ == '__main__':
    unittest.main()
