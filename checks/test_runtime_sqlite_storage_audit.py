"""Small synthetic SQLite fixtures; no real fact or source material."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('storage_audit_tool',ROOT/'tools/audit_runtime_sqlite_storage.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)


def fixture(path):
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE runtime_definitions(owner TEXT PRIMARY KEY,payload_json TEXT);'
            'CREATE TABLE runtime_fact_fields(id INTEGER PRIMARY KEY,payload_json TEXT,sha256 TEXT UNIQUE);'
            'CREATE TABLE chunks(id TEXT PRIMARY KEY,text TEXT,metadata_json TEXT);'
            'CREATE INDEX chunks_id ON chunks(id);'
            'CREATE VIRTUAL TABLE chunks_fts USING fts5(text,content="chunks",content_rowid="rowid");')
        db.execute('INSERT INTO runtime_definitions VALUES (?,?)',('PRIVATE_OWNER_ID','{"private_body":"SECRET正文"}'))
        for field in ('scope','conditions','UNTRUSTED_PRIVATE_FIELD'):
            value=json.dumps({'field':field,'value':['NEVER_EMIT_THIS']},ensure_ascii=False)
            db.execute('INSERT INTO runtime_fact_fields(payload_json,sha256) VALUES (?,?)',(value,hashlib.sha256(value.encode()).hexdigest()))
        db.execute('INSERT INTO chunks VALUES (?,?,?)',('PRIVATE_CHUNK','hidden technical token','{"private_id":2}'))
        db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
        db.execute('CREATE TABLE "PRIVATE_TABLE_NAME" (private TEXT)')
        db.execute('INSERT INTO "PRIVATE_TABLE_NAME" VALUES (?)',('SECRET',))
    return path


class StorageAuditTests(unittest.TestCase):
    def test_actual_pages_logical_bytes_and_no_private_content(self):
        with tempfile.TemporaryDirectory() as temp:
            path=fixture(Path(temp)/'private-name.sqlite');before=path.read_bytes();result=audit.audit_storage(path)
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(result['database_file_bytes'],len(before))
            self.assertEqual(result['sqlite_page_bytes'],result['sqlite_page_size']*result['sqlite_page_count'])
            if result['dbstat_available']:
                self.assertEqual(result['dbstat_attributed_bytes']+result['freelist_bytes']+result['unattributed_nonfreelist_page_bytes'],result['sqlite_page_bytes'])
                self.assertIn('fts:chunks_fts_data',result['physical_objects'])
            self.assertEqual(result['logical_columns']['runtime_definitions']['rows'],1)
            counts={row['field']:row['rows'] for row in result['field_pool_distribution']}
            self.assertEqual(counts,{'scope':1,'conditions':1,'unknown_field':1})
            serialized=json.dumps(result,ensure_ascii=False)
            for secret in ('SECRET','正文','NEVER_EMIT_THIS','PRIVATE_OWNER','PRIVATE_TABLE','PRIVATE_CHUNK','UNTRUSTED_PRIVATE_FIELD',temp,'private-name'):
                self.assertNotIn(secret,serialized)

    def test_dbstat_absent_does_not_fake_physical_estimates(self):
        with tempfile.TemporaryDirectory() as temp:
            path=fixture(Path(temp)/'facts.sqlite')
            with patch.object(audit,'_physical_stats',side_effect=sqlite3.OperationalError('no such table: dbstat')):
                result=audit.audit_storage(path)
            self.assertFalse(result['dbstat_available']);self.assertEqual(result['physical_objects'],{})
            self.assertIsNone(result['dbstat_attributed_bytes']);self.assertIsNone(result['unattributed_nonfreelist_page_bytes'])
            self.assertGreater(result['logical_columns']['runtime_definitions']['selected_column_utf8_bytes']['payload_json'],0)

    def test_corruption_is_not_treated_as_unavailable_facility(self):
        with tempfile.TemporaryDirectory() as temp:
            path=fixture(Path(temp)/'facts.sqlite')
            with patch.object(audit,'_physical_stats',side_effect=sqlite3.DatabaseError('database disk image is malformed')):
                with self.assertRaises(sqlite3.DatabaseError):audit.audit_storage(path)

    def test_journal_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path=fixture(Path(temp)/'facts.sqlite');sidecar=Path(str(path)+'-wal');sidecar.write_bytes(b'candidate active')
            with self.assertRaises(ValueError):audit.audit_storage(path)
            sidecar.unlink();link=Path(temp)/'link.sqlite';link.symlink_to(path)
            with self.assertRaises(ValueError):audit.audit_storage(link)

    def test_json_functions_missing_have_bounded_honest_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            path=fixture(Path(temp)/'facts.sqlite')
            with patch.object(audit,'_field_stats',side_effect=sqlite3.OperationalError('no such function: json_valid')):
                result=audit.audit_storage(path)
            self.assertFalse(result['field_pool_json_functions_available']);self.assertEqual(result['field_pool_distribution'],[])
            self.assertEqual(result['logical_columns']['runtime_fact_fields']['rows'],3)

    def test_cli_exclusive_output_and_no_database_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            path=fixture(Path(temp)/'facts.sqlite');out=Path(temp)/'audit.json';before=path.read_bytes()
            cmd=[sys.executable,str(ROOT/'tools/audit_runtime_sqlite_storage.py'),'--sqlite',str(path),'--output',str(out)]
            self.assertEqual(subprocess.run(cmd,capture_output=True).returncode,0)
            saved=out.read_bytes();self.assertNotEqual(subprocess.run(cmd,capture_output=True).returncode,0);self.assertEqual(out.read_bytes(),saved)
            cmd[-1]=str(path);self.assertNotEqual(subprocess.run(cmd,capture_output=True).returncode,0);self.assertEqual(path.read_bytes(),before)

if __name__=='__main__':unittest.main()
