"""Exercise release.main staging without building Windows executables."""
import gzip
import json
import os
import subprocess
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_release
import runtime_knowledge_resources as collector
from test_instruction_owner_store import fixture
from knowledge.runtime_fact_publication import OwnerStoreWriter
from plc.instruction_owner_store import OwnerDefinitionStore


class StopBeforeHelpers(Exception):
    pass


class ReleaseStagingTests(unittest.TestCase):
    def prepare(self, root, mode):
        catalog = root / 'resources/instructions/mitsubishi'
        knowledge = root / 'resources/knowledge/runtime'
        catalog.mkdir(parents=True); knowledge.mkdir(parents=True)
        for name in collector.INSTRUCTION_FILES:
            if name not in ('instruction_definitions.json.gz', 'fx3u_step_widths.json'):
                (catalog / name).write_text('{}')
        raw = fixture()
        writer = OwnerStoreWriter(knowledge / 'plc_facts.sqlite', {k:v for k,v in raw.items() if k != 'entries'})
        for entry in raw['entries']: writer.add_entry(entry)
        canonical = knowledge / 'instruction_definitions.manifest.json'
        writer.finalize(canonical)
        for relative, _ in collector.FILES:
            path = root / relative
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True); path.write_text('{}')
        if mode == 'local':
            shutil.copyfile(knowledge / 'plc_facts.sqlite', catalog / 'facts.sqlite')
            data = json.loads(canonical.read_text()); data['database']['filename'] = 'facts.sqlite'
            (catalog / canonical.name).write_text(json.dumps(data)); canonical.unlink()
        elif mode == 'custom':
            shutil.copyfile(knowledge / 'plc_facts.sqlite', knowledge / 'owners.sqlite')
            data = json.loads(canonical.read_text()); data['database']['filename'] = 'owners.sqlite'
            canonical.write_text(json.dumps(data))
        elif mode == 'legacy':
            canonical.unlink()
            with gzip.open(catalog / 'instruction_definitions.json.gz', 'wt') as out: json.dump(raw, out)
        return catalog, knowledge

    def invoke(self, root, output):
        argv = ['build_release', '--resource-root', str(root), '--output', str(output), '--skip-frontend', '--component', 'mcp']
        with patch.object(sys, 'argv', argv), patch.object(build_release, 'TEMPLATE_NAMES', ()), patch.object(build_release.subprocess, 'run', side_effect=StopBeforeHelpers):
            build_release.main()

    def test_actual_main_manifest_and_legacy_source_graph(self):
        for mode in ('canonical', 'local', 'custom', 'legacy'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)/'source'; output=Path(tmp)/'build'
                self.prepare(root, mode)
                with self.assertRaises(StopBeforeHelpers): self.invoke(root, output)
                inputs = output/'inputs'
                self.assertTrue((inputs/'resources/knowledge/runtime/plc_facts.sqlite').is_file())
                names = {Path(p).name for p, _ in collector.runtime_instruction_data(inputs)}
                self.assertIn('common.json', names)
                if mode == 'legacy': self.assertIn('instruction_definitions.json.gz', names)
                else:
                    self.assertNotIn('instruction_definitions.json.gz', names)
                    manifest = inputs / ('resources/instructions/mitsubishi' if mode == 'local' else 'resources/knowledge/runtime') / 'instruction_definitions.manifest.json'
                    store = OwnerDefinitionStore(manifest)
                    try: self.assertEqual(store.get_entry('PWM', cpu='FX5U'), fixture()['entries'][0])
                    finally: store.close()
                self.assertEqual((inputs/'resources/instructions/mitsubishi/fx3u_step_widths.json').read_bytes(), build_release.safe_native_encoding(build_release.ROOT/'resources/instructions/mitsubishi/fx3u_step_widths.json'))

    def test_fresh_cli_process_without_pythonpath(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source'; output=Path(tmp)/'build'
            self.prepare(root, 'canonical')
            script_dir=Path(build_release.__file__).parent
            code = """import sys
from unittest.mock import patch
sys.path.insert(0, sys.argv.pop(1))
import build_release
class Stop(Exception): pass
with patch.object(build_release, 'TEMPLATE_NAMES', ()), patch.object(build_release.subprocess, 'run', side_effect=Stop):
    try: build_release.main()
    except Stop: pass
"""
            env=dict(os.environ);env.pop('PYTHONPATH', None)
            result=subprocess.run([sys.executable, '-c', code, str(script_dir), '--resource-root', str(root), '--output', str(output), '--skip-frontend'], env=env, cwd=tmp, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output/'inputs/resources/knowledge/runtime/instruction_definitions.manifest.json').is_file())

    def test_bad_manifest_does_not_fall_back_to_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source'; output=Path(tmp)/'build'
            catalog, knowledge=self.prepare(root, 'legacy')
            (knowledge/'instruction_definitions.manifest.json').write_text('{}')
            with self.assertRaises(ValueError): self.invoke(root, output)
            self.assertFalse((output/'inputs').exists())

    def test_reusing_staged_inputs_rejected_without_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source'; output=Path(tmp)/'build'
            self.prepare(root, 'canonical')
            marker=output/'inputs/stale';marker.parent.mkdir(parents=True);marker.write_text('keep')
            with self.assertRaisesRegex(ValueError, 'fresh build'): self.invoke(root, output)
            self.assertEqual(marker.read_text(), 'keep')

if __name__ == '__main__': unittest.main()
