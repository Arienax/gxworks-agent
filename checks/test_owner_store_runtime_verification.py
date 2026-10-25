"""Tests for the isolated diagnostic, not PLC hardware support claims."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from scripts import verify_owner_store_runtime as tool

class RuntimeVerificationTests(unittest.TestCase):
    def test_equal_nonempty_samples_pass_but_unknown_and_difference_do_not(self):
        a={'status':'passed','semantics':{'menus':{'FX3U':{'count':1}},'constraints':['passed','failed','unverified']}}
        self.assertEqual(tool.compare_results(a,copy.deepcopy(a)),'passed')
        b=copy.deepcopy(a);b['status']='unverified';self.assertEqual(tool.compare_results(b,b),'unverified')
        b=copy.deepcopy(a);b['semantics']['constraints'][1]='passed';self.assertEqual(tool.compare_results(a,b),'failed')
        for empty in ({},{'status':'passed'},{'status':'passed','semantics':{}}):self.assertEqual(tool.compare_results(empty,empty),'failed')
    def test_same_manifest_database_or_file_identity_is_not_migration_proof(self):
        for key,value in [('manifest_path_sha256','hash1'),('database_path_sha256','hash2'),('database_file_identity',[4,18])]:
            row={'status':'passed','semantics':{'nonempty':True},'input_identity':{key:value}}
            self.assertEqual(tool.compare_results(row,copy.deepcopy(row)),'unverified')
    def test_file_hash_reads_actual_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db';path.write_bytes(b'before');before=tool._file_hash(path)
            path.write_bytes(b'after');self.assertNotEqual(tool._file_hash(path),before)
    def test_dependency_closure_is_complete_ordered_and_rejects_missing_cycles(self):
        facts=[{'id':'root','depends_on':['dep']},{'id':'unrelated'},{'id':'dep','depends_on':['leaf']},{'id':'leaf'}]
        self.assertEqual([x['id']for x in tool._closure(facts,['root'])],['root','dep','leaf'])
        for bad in ([{'id':'root','depends_on':['missing']}],[{'id':'root','depends_on':['root']}],[{'id':'root'},{'id':'root'}]):
            with self.assertRaises(ValueError):tool._closure(bad,['root'])
    def test_subprocess_environment_is_local_and_seed_catalog_is_preserved(self):
        before=dict(os.environ);response=type('Response',(),{'returncode':0,'stdout':'{"status":"passed","semantics":{"sample":true}}'})()
        with patch.object(tool.subprocess,'run',return_value=response)as run:
            result=tool._run_child(Path('candidate.manifest.json'))
        self.assertEqual(dict(os.environ),before);kw=run.call_args.kwargs
        self.assertEqual(kw['env']['GXW2_INSTRUCTION_OWNER_MANIFEST'],str(Path('candidate.manifest.json').resolve()))
        self.assertEqual(kw['env']['GXW2_INSTRUCTION_CATALOG'],str(tool.ROOT/'resources/instructions/mitsubishi'))
        self.assertIn('--_worker',run.call_args.args[0]);self.assertTrue(kw['capture_output']);self.assertEqual(result['status'],'passed')
    def test_timeout_and_bad_worker_output_are_explicit_failures(self):
        with patch.object(tool.subprocess,'run',side_effect=subprocess.TimeoutExpired('worker',1)):
            self.assertEqual(tool._run_child('missing')['coverage_gaps'],['worker_timed_out'])
        response=type('Response',(),{'returncode':0,'stdout':'private diagnostic garbage'})()
        with patch.object(tool.subprocess,'run',return_value=response):
            result=tool._run_child('missing');self.assertEqual(result['status'],'failed');self.assertNotIn('garbage',json.dumps(result))
    def test_windows_memory_fallback_is_null_not_linux_rss_or_fabricated_zero(self):
        with patch.object(tool.sys,'platform','win32'),patch.dict('sys.modules',{'ctypes':None}):
            result=tool.process_memory()
        self.assertIsNone(result['peak_rss_bytes']);self.assertIsNone(result['peak_working_set_bytes'])
        self.assertEqual(result['unavailable_reason'],'windows_process_counter_unavailable')
    def test_source_sidecars_rejected_and_output_never_overwritten(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);db=root/'db.sqlite';db.write_bytes(b'not SQLite');Path(str(db)+'-wal').write_bytes(b'')
            with self.assertRaises(ValueError):tool._sealed(db)
            output=root/'result.json';output.write_text('preserve')
            with patch.object(tool.sys,'argv',['verify','--source-manifest','source.json','--candidate-manifest','candidate.json','--output',str(output)]):
                with self.assertRaises(SystemExit):tool.main()
            self.assertEqual(output.read_text(),'preserve')

if __name__=='__main__':unittest.main()
