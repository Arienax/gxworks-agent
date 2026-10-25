"""Startup-selected owner domains never borrow stale mixed-index owners."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from knowledge import fact_corpus, index_access
from knowledge.runtime_fact_publication import OwnerStoreWriter
from plc.instructions import InstructionRegistry


def build(root, flag):
    root.mkdir()
    metadata={'schema_version':1,'method':'instruction-material-compiler-v1',
              'corpus_kind':'authored_plc_facts_v1','fact_bundle_version':'synthetic:selection'}
    fact={'id':'sample','dimension':'effects.synthetic','value':{'flag':flag},
          'scope':{'models':['FX3U'],'forms':['MOV']},'depends_on':[],
          'status':'source_verified','verification':'source_checked',
          'sources':[{'manual_id':'synthetic','revision':'A'}]}
    entry={'vendor':'mitsubishi','opcode':'MOV','target_model':'FX3U','facts':[fact]}
    db=root/'plc_facts.sqlite'; manifest=root/'instruction_definitions.manifest.json'
    writer=OwnerStoreWriter(db,metadata)
    writer.add_entry(entry);writer.finalize(manifest)
    return db,manifest


class OwnerDomainSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.old,_=build(self.root/'old',False)
        with sqlite3.connect(self.old) as connection:
            for kind,target,marker in [('device','D','DEVICE_MARKER'),('error','1234','ERROR_MARKER')]:
                identity='synthetic:'+kind
                metadata={'content_origin':'authored_fact','distribution_status':'fact_representation_reviewed',
                          'fact_kind':kind,'fact_target':target,'source_lane':'fact'}
                packet={'kind':kind,'target':target,'scope':{'models':['FX3U']},'value':{'marker':marker}}
                connection.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)',
                    (identity,json.dumps(packet),json.dumps(metadata),'["FX3U"]','',kind,'authored_fact','fact_representation_reviewed'))
                connection.execute('INSERT INTO facts VALUES (?,?,?,?,?,?)',(identity,kind,target,'FX3U',identity,'{}'))
            connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
        self.new,self.manifest=build(self.root/'new',True)
        self.registry=InstructionRegistry();self.registry.load_instruction_owner_store(self.manifest)
        self.registry_patch=patch('plc.instructions.DEFAULT_INSTRUCTION_REGISTRY',self.registry)
        self.resource_patch=patch('knowledge.index_access.resource_path',return_value=self.old)
        self.registry_patch.start();self.resource_patch.start()
    def tearDown(self):
        index_access.close_thread_connection();self.registry._definition_owner_store.close()
        self.resource_patch.stop();self.registry_patch.stop();self.tmp.cleanup()
    def test_selected_owner_domain_and_installed_other_domains(self):
        self.assertEqual(index_access.index_path(),self.old)
        for kind in ('instruction','language'):
            self.assertEqual(fact_corpus.runtime_index_path(kind),self.new.resolve())
        for kind in ('device','error','positioning',None):
            self.assertEqual(fact_corpus.runtime_index_path(kind),self.old)
        rows=fact_corpus.resolve('instruction',['MOV'],plc_model='FX3U')
        self.assertTrue(rows);self.assertIn('true',json.dumps(rows))
        self.assertEqual(fact_corpus.resolve('instruction',['MISSING'],plc_model='FX3U'),[])
        self.assertEqual(fact_corpus.resolve('instruction',['MOV'],plc_model='Q00'),[])
    def test_natural_language_broad_uses_selected_owner_without_opcode(self):
        from knowledge.core import retrieve_broad_knowledge
        rows=retrieve_broad_knowledge('PLC flag',plc_model='FX3U',task_type='analysis')
        self.assertTrue(rows)
        self.assertEqual({r.get('retrieval_domain') for r in rows},{'selected_owner'})
        self.assertIn('true',json.dumps(rows));self.assertNotIn('false',json.dumps(rows))

    def test_same_database_selection_does_not_exclude_its_owners(self):
        from knowledge.core import retrieve_broad_knowledge
        with patch('knowledge.index_access.resource_path',return_value=self.new):
            rows=retrieve_broad_knowledge('PLC flag',plc_model='FX3U',task_type='analysis')
        self.assertTrue(rows);self.assertEqual(rows[0]['fact_target'],'MOV')

    def test_installed_device_error_records_survive_domain_partition(self):
        from knowledge.core import retrieve_broad_knowledge
        for kind,target,marker in [('device','D','DEVICE_MARKER'),('error','1234','ERROR_MARKER')]:
            exact=fact_corpus.resolve(kind,[target],plc_model='FX3U')
            self.assertTrue(exact);self.assertIn(marker,json.dumps(exact))
            broad=retrieve_broad_knowledge('PLC '+marker,plc_model='FX3U',task_type='analysis')
            self.assertTrue(broad);self.assertIn(marker,json.dumps(broad))
            self.assertEqual({row['fact_kind'] for row in broad},{kind})
            self.assertEqual({row['retrieval_domain'] for row in broad},{'installed_other'})

    def test_cross_database_id_collision_preserves_both_domains_and_exact_identity(self):
        from knowledge.core import retrieve_broad_knowledge
        with sqlite3.connect(self.new) as connection:
            identity=connection.execute('SELECT id FROM chunks LIMIT 1').fetchone()[0]
        with sqlite3.connect(self.old) as connection:
            connection.execute("DELETE FROM facts WHERE kind='instruction'")
            connection.execute('DELETE FROM chunks WHERE id=?',(identity,))
            packet={'kind':'device','target':'D','scope':{'models':['FX3U']},'value':{'marker':'PLC flag DEVICE_MARKER'}}
            connection.execute('UPDATE chunks SET id=?,text=? WHERE id=?',(identity,json.dumps(packet),'synthetic:device'))
            connection.execute('UPDATE facts SET chunk_id=? WHERE chunk_id=?',(identity,'synthetic:device'))
            connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
        rows=retrieve_broad_knowledge('PLC flag',plc_model='FX3U',task_type='analysis',top_k=10)
        self.assertEqual({row['fact_kind'] for row in rows},{'instruction','device'})
        self.assertEqual(len({row['id'] for row in rows}),2)
        self.assertEqual({row['retrieval_chunk_id'] for row in rows},{identity})
        for kind,target in [('instruction','MOV'),('device','D')]:
            exact=fact_corpus.resolve(kind,[target],plc_model='FX3U')
            self.assertEqual(exact[0]['id'],next(row['id'] for row in rows if row['fact_kind']==kind))
            self.assertNotIn(str(self.root),exact[0]['id'])

    def test_legacy_source_followups_use_original_database_identity(self):
        from knowledge.instruction_facts import _related_units, _OFFICIAL
        with sqlite3.connect(self.old) as connection:
            for name in ('manual_id','section','parent_chunk_id','revision','manual_type'):
                connection.execute('ALTER TABLE chunks ADD COLUMN '+name+' TEXT')
            for identity,section,parent in [('original','seed','parent'),('child','other','original'),('parent','parent-section',None)]:
                connection.execute('INSERT INTO chunks (id,text,plc_models,manual_id,section,parent_chunk_id,revision,manual_type,metadata_json,instruction_opcode,chunk_type,content_origin,distribution_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (identity,'PLC source '+identity,'FX3U','manual',section,parent,'A',sorted(_OFFICIAL)[0],'{}','','instruction','authored_fact','fact_representation_reviewed'))
        seed={'id':'runtime-record:["installed_other","original"]','retrieval_chunk_id':'original',
              'manual_id':'manual','section':'seed','revision':'A','retrieval_domain':'installed_other'}
        related=_related_units(seed,'FX3U','analysis')
        self.assertEqual({row['retrieval_chunk_id'] for row in related},{'original','child','parent'})
        self.assertEqual(_related_units(dict(seed,retrieval_domain='selected_owner'),'FX3U','analysis'),[])

    def test_device_pdf_projection_preserves_original_source_key(self):
        from knowledge.device_evidence import project_source_record
        connection=sqlite3.connect(':memory:');connection.row_factory=sqlite3.Row
        try:
            connection.execute('CREATE TABLE chunks(id TEXT,pdf_page INTEGER,pdf_page_end INTEGER)')
            connection.execute("INSERT INTO chunks VALUES ('raw',1,2)")
            connection.execute('CREATE TABLE device_sections(id TEXT,manual_id TEXT,pdf_page INTEGER,table_binding_status TEXT,plc_models TEXT,text TEXT,section TEXT,source_excerpt_json TEXT)')
            connection.execute("INSERT INTO device_sections VALUES ('section','manual',2,'cpu_column_with_notes','FX3U','complete column','D','{}')")
            record={'id':'runtime-record:["installed_other","raw"]','retrieval_chunk_id':'raw',
                    'source_chunk_id':'explicit-source','table_binding_status':'cpu_columns_require_projection',
                    'manual_id':'manual','pdf_page':1,'source':'manual'}
            result=project_source_record(connection,{'device_sections':{'name':'device_sections'}},record,plc_model='FX3U')
            self.assertIsNotNone(result);self.assertEqual(result['source_chunk_id'],'explicit-source')
            self.assertEqual(result['id'],record['id']);self.assertIn('complete column',result['text'])
            self.assertIsNone(project_source_record(connection,{'device_sections':{'name':'device_sections'}},dict(record,retrieval_domain='selected_owner'),plc_model='FX3U'))
        finally:connection.close()

    def test_explicit_connection_and_none_remain_authoritative(self):
        with sqlite3.connect(self.old) as explicit:
            explicit.row_factory=sqlite3.Row
            rows=fact_corpus.resolve('instruction',['MOV'],plc_model='FX3U',connection=explicit)
            self.assertTrue(rows);self.assertIn('false',json.dumps(rows))
            self.assertFalse(rows[0]['id'].startswith('runtime-record:'))
        self.assertIsNone(fact_corpus.resolve('instruction',['MOV'],plc_model='FX3U',connection=None))
    def test_selection_is_startup_bound_and_no_rehash_per_query(self):
        with patch.dict('os.environ',{'GXW2_INSTRUCTION_OWNER_MANIFEST':str(self.root/'missing.json')}), \
             patch('plc.instruction_owner_store._digest',side_effect=AssertionError('no repeated full hash')):
            self.assertEqual(fact_corpus.runtime_index_path('instruction'),self.new.resolve())
            self.assertTrue(fact_corpus.resolve('instruction',['MOV'],plc_model='FX3U'))
            self.assertTrue(fact_corpus.resolve('instruction',['MOV'],plc_model='FX3U'))
    def test_withdrawn_pair_does_not_fall_back(self):
        self.manifest.write_text('{}')
        with self.assertRaises(ValueError):fact_corpus.resolve('instruction',['MOV'],plc_model='FX3U')
        self.assertEqual(fact_corpus.runtime_index_path('device'),self.old)
    def test_no_selected_store_preserves_installed_path(self):
        registry=InstructionRegistry()
        with patch('plc.instructions.DEFAULT_INSTRUCTION_REGISTRY',registry):
            self.assertEqual(fact_corpus.runtime_index_path('instruction'),self.old)


if __name__=='__main__':unittest.main()
