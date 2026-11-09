"""Independent lazy-owner integrity, admission and isolation regressions."""
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from knowledge.runtime_fact_projection import intern_runtime_sources
from knowledge.runtime_fact_publication import write_runtime_sqlite, write_owner_manifest
from plc.instruction_owner_store import OwnerDefinitionStore
from plc.instructions import InstructionRegistry, InstructionSpec


def fixture():
    entries=[]
    for opcode in ('KNOWN','REFERENCE'):
        entries.append({'vendor':'mitsubishi','opcode':opcode,'target_model':'FX5U',
            'call_available':opcode=='KNOWN','facts':[
            {'id':'dep','dimension':'execution.synthetic','value':{'items':[1,2]},
             'status':'declared_unverified','verification':'not_performed',
             'scope':{'models':['FX5U'],'forms':[opcode]},'sources':[], 'depends_on':[]},
            {'id':'root','dimension':'constraints.synthetic','value':{'minimum':1,'unit':'ms'},
             'status':'source_verified','verification':'source_checked',
             'scope':{'models':['FX5U'],'forms':[opcode]},'conditions':[{'context':'enabled','equals':True}],
             'sources':[{'manual_id':'synthetic','revision':'A'}],'depends_on':['dep']}]})
    return {'schema_version':1,'method':'instruction-material-compiler-v1',
            'corpus_kind':'authored_plc_facts_v1','fact_bundle_version':'synthetic-owner-v1','entries':entries}


def registry():
    return InstructionRegistry([InstructionSpec('KNOWN',cpu_support=frozenset({'FX5U'}))])


class RuntimeOwnerLoadingRegression(unittest.TestCase):
    FIELD_POOLING = False
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db=self.root/'facts.sqlite';self.manifest=self.root/'instruction_definitions.manifest.json'
        self.original=fixture();self.pooled=intern_runtime_sources(self.original,intern_values=True)
        write_runtime_sqlite(self.pooled,self.db,field_pooling=self.FIELD_POOLING);write_owner_manifest(self.pooled,self.db,self.manifest)
    def open(self):
        s=OwnerDefinitionStore(self.manifest);self.addCleanup(s.close);return s
    def change_manifest(self,mutate):
        value=json.loads(self.manifest.read_text());mutate(value);self.manifest.write_text(json.dumps(value))
    def refresh_hash(self):
        self.change_manifest(lambda v:v['database'].update(bytes=self.db.stat().st_size,sha256=hashlib.sha256(self.db.read_bytes()).hexdigest()))
    def sql(self,query,args=()):
        with sqlite3.connect(self.db) as conn:conn.execute(query,args)
    def rewrite_owner(self,mutate,*,packed=False):
        with sqlite3.connect(self.db) as conn:
            key,text=conn.execute('SELECT owner,payload_json FROM runtime_definitions ORDER BY owner LIMIT 1').fetchone()
            value=json.loads(text)
            shared=any('field_refs' in f for f in value['value']['facts']) and not packed
            if shared:
                from knowledge.fact_field_codec import unpack_fact_fields,pack_fact_fields
                def resolve_field(identity):
                    return json.loads(conn.execute('SELECT payload_json FROM runtime_fact_fields WHERE id=?',(identity,)).fetchone()[0])
                value['value']['facts']=[unpack_fact_fields(f,resolve_field) for f in value['value']['facts']]
            mutate(value['value'])
            if shared:
                def intern_field(field,item):
                    encoded=json.dumps({'field':field,'value':item},sort_keys=True,separators=(',',':'))
                    digest=hashlib.sha256(encoded.encode()).hexdigest()
                    row=conn.execute('SELECT id FROM runtime_fact_fields WHERE sha256=?',(digest,)).fetchone()
                    if row:return row[0]
                    return conn.execute('INSERT INTO runtime_fact_fields(payload_json,sha256) VALUES (?,?)',(encoded,digest)).lastrowid
                value['value']['facts']=[pack_fact_fields(f,intern_field) for f in value['value']['facts']]
            text=json.dumps(value,sort_keys=True,separators=(',',':'))
            conn.execute('UPDATE runtime_definitions SET payload_json=? WHERE owner=?',(text,key))
            conn.execute('UPDATE runtime_owner_index SET packet_sha256=? WHERE owner=?',(hashlib.sha256(text.encode()).hexdigest(),key))
        self.refresh_hash()
    def test_startup_and_headers_do_not_load_owner_facts(self):
        s=self.open();self.assertEqual(s.stats()['hydrated_owner_count'],0)
        self.assertEqual(len(list(s.iter_owner_headers())),2)
        self.assertEqual(s.stats()['hydrated_owner_count'],0)
        self.assertIsNone(s.get_entry('MISSING',cpu='FX5U'))
        self.assertEqual(s.stats()['hydrated_owner_count'],0)
        self.assertEqual(s.get_entry('KNOWN',cpu='FX5U'),self.original['entries'][0])
        self.assertEqual(s.stats()['hydrated_owner_count'],1)
    def test_registry_only_attaches_requested_callable_owner(self):
        r=registry();r.load_instruction_owner_store(self.manifest);s=r._definition_owner_store
        self.addCleanup(s.close);self.assertEqual(s.stats()['hydrated_owner_count'],0)
        self.assertEqual(r.loaded_definition_owner_count,0)
        self.assertIsNotNone(r.resolve_fact_form('REFERENCE',cpu='FX5U'))
        self.assertIsNone(r.resolve_form('REFERENCE',cpu='FX5U'))
        self.assertEqual(s.stats()['hydrated_owner_count'],0)
        form=r.resolve_form('KNOWN',cpu='FX5U');self.assertEqual(r.loaded_definition_owner_count,1)
        count=s.stats()['hydrated_owner_count'];r.resolve_form('KNOWN',cpu='FX5U')
        self.assertEqual(s.stats()['hydrated_owner_count'],count)
        self.assertEqual(form.spec.definition_facts[0].status,'declared_unverified')
        self.assertFalse(r.definition_entry('REFERENCE',cpu='FX5U')['call_available'])
    def test_owner_and_spec_mutation_do_not_modify_evidence_or_peer(self):
        r=registry();r.load_instruction_owner_store(self.manifest);self.addCleanup(r._definition_owner_store.close)
        row=r.definition_entry('KNOWN',cpu='FX5U');row['facts'][0]['value']['items'].append(999)
        row['facts'][1]['sources'][0]['revision']='changed'
        spec=r.resolve_form('KNOWN',cpu='FX5U').spec;spec.definition_facts[0].value['items'].append(888)
        self.assertEqual(r.definition_entry('KNOWN',cpu='FX5U'),self.original['entries'][0])
        self.assertEqual(r.definition_entry('REFERENCE',cpu='FX5U'),self.original['entries'][1])
    def test_database_connection_is_read_only_and_query_only(self):
        s=self.open();self.assertEqual(s._db.execute('PRAGMA query_only').fetchone()[0],1)
        with self.assertRaises(sqlite3.OperationalError):s._db.execute('DELETE FROM runtime_sources')
        s._db.execute('PRAGMA query_only=OFF')
        try:
            with self.assertRaises(sqlite3.OperationalError):s._db.execute('DELETE FROM runtime_sources')
        finally:s._db.execute('PRAGMA query_only=ON')
    def test_manifest_rejects_traversal_absolute_and_wrong_versions(self):
        original=self.manifest.read_text()
        for name in ('../facts.sqlite','/tmp/facts.sqlite','C:\\tmp\\facts.sqlite','x/../facts.sqlite'):
            self.manifest.write_text(original);self.change_manifest(lambda v:v['database'].update(filename=name))
            with self.subTest(filename=name),self.assertRaises(ValueError):OwnerDefinitionStore(self.manifest)
        for key,value in [('schema_version',2),('schema_version',True),('serialization','future'),('owners',99)]:
            self.manifest.write_text(original);self.change_manifest(lambda v:v.update({key:value}))
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):OwnerDefinitionStore(self.manifest)
    def test_bad_manifest_never_silently_falls_back_to_legacy_json(self):
        import plc.instructions as instructions
        for name in ('common.json','fx3u.json','fx5u.json'):
            (self.root/name).write_text('[]')
        old=self.root/'instruction_definitions.json';old.write_text(json.dumps(self.original))
        self.change_manifest(lambda v:v.update(serialization='broken'))
        r=registry()
        with patch.object(instructions,'_candidate_catalog_directories',return_value=(self.root,)), \
             patch.object(InstructionRegistry,'from_files',return_value=r), \
             patch.object(r,'load_instruction_definitions',wraps=r.load_instruction_definitions) as legacy:
            with self.assertRaises(ValueError):instructions.load_default_instruction_registry()
            legacy.assert_not_called()

    def test_legacy_json_remains_supported_when_no_manifest_exists(self):
        import plc.instructions as instructions
        for name in ('common.json','fx3u.json','fx5u.json'):
            (self.root/name).write_text('[]')
        old=self.root/'instruction_definitions.json';old.write_text(json.dumps(self.original))
        missing=self.root/'absent.manifest.json';r=registry()
        with patch.object(instructions,'_candidate_catalog_directories',return_value=(self.root,)), \
             patch.object(InstructionRegistry,'from_files',return_value=r), \
             patch('shared.paths.resource_path',return_value=missing):
            self.manifest.rename(self.root/'not-selected.json')
            loaded=instructions.load_default_instruction_registry()
            self.assertEqual(loaded.definition_entry('KNOWN',cpu='FX5U'),self.original['entries'][0])

    def test_hash_and_bundle_receipt_mismatches_are_rejected(self):
        original=self.manifest.read_text()
        self.change_manifest(lambda v:v['database'].update(sha256='0'*64))
        with self.assertRaises(ValueError):OwnerDefinitionStore(self.manifest)
        self.manifest.write_text(original);self.change_manifest(lambda v:v.update(fact_bundle_version='different'))
        with self.assertRaises(ValueError):OwnerDefinitionStore(self.manifest)
    def test_pool_corruption_and_source_withdrawal_fail_closed(self):
        self.sql("UPDATE runtime_values SET payload_json='{}'");self.refresh_hash()
        s=self.open()
        with self.assertRaises(ValueError):s.get_entry('KNOWN',cpu='FX5U')
    def test_missing_source_before_first_read_is_not_fallback(self):
        self.sql('DELETE FROM runtime_sources');self.refresh_hash();s=self.open()
        with self.assertRaises(ValueError):s.get_entry('KNOWN',cpu='FX5U')
    def test_backing_store_change_invalidates_warm_registry(self):
        r=registry();r.load_instruction_owner_store(self.manifest);self.addCleanup(r._definition_owner_store.close)
        r.resolve_form('KNOWN',cpu='FX5U');self.sql('DELETE FROM runtime_sources')
        self.assertFalse(r.fact_bundle_current())
        with self.assertRaises(ValueError):r.resolve_form('KNOWN',cpu='FX5U')
        with self.assertRaises(ValueError):r.definition_entry('KNOWN',cpu='FX5U')
    def test_symlink_database_and_manifest_are_rejected(self):
        alias=self.root/'alias.manifest.json';alias.symlink_to(self.manifest)
        with self.assertRaises(ValueError):OwnerDefinitionStore(alias)
        real=self.root/'real.sqlite';self.db.rename(real);self.db.symlink_to(real)
        with self.assertRaises(ValueError):OwnerDefinitionStore(self.manifest)

    def test_unsealed_sqlite_sidecar_is_rejected(self):
        for suffix in ('-wal','-journal'):
            sidecar=Path(str(self.db)+suffix);sidecar.write_bytes(b'pending')
            try:
                with self.subTest(suffix=suffix),self.assertRaises(ValueError):OwnerDefinitionStore(self.manifest)
            finally:sidecar.unlink()

    def test_technical_values_stay_searchable_and_selected_graph_matches(self):
        from knowledge.fact_corpus import resolve
        from knowledge.structured_facts import authored_packet
        from knowledge.fact_selection import project_packet
        from knowledge.core import retrieve_broad_knowledge
        from knowledge import index_access
        raw=fixture()
        root=raw['entries'][0]['facts'][1]
        root.update(dimension='parameters.1.range',value={
            'maximum':9973,'unit':'microseconds','semantic_tag':'independent_guard_token'})
        root['conditions']=[{'context':'operating_mode','equals':'special_context_token'}]
        for f in raw['entries'][0]['facts']:f['scope']['languages']=['ladder']
        db=self.root/'technical.sqlite'
        write_runtime_sqlite(intern_runtime_sources(raw,intern_values=True),db,field_pooling=True)
        connection=sqlite3.connect(db);connection.row_factory=sqlite3.Row
        self.addCleanup(connection.close)
        for term in ('independent_guard_token','9973','microseconds','special_context_token'):
            rows=connection.execute('SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?',
                                    ('"'+term+'"',)).fetchall()
            self.assertTrue(rows,term)
        rows=resolve('instruction',['KNOWN'],plc_model='FX5U',connection=connection)
        self.assertEqual(len(rows),1)
        actual=authored_packet(rows[0])
        expected={'kind':'instruction','target':'KNOWN','scope':{'models':['FX5U']},
                  'content_origin':'authored_fact','value':raw['entries'][0]}
        for language in ('ladder','st'):
            for context in ({},{'operating_mode':'special_context_token'},{'operating_mode':'other'}):
                kwargs=dict(plc_model='FX5U',language=language,aspects=['operand_range'],instance_context=context)
                got=project_packet(actual,**kwargs);want=project_packet(expected,**kwargs)
                self.assertEqual(got['value']['facts'],want['value']['facts'])
                self.assertEqual(got['fact_selection'],want['fact_selection'])
        with patch.object(index_access,'index_path',return_value=db):
            found=retrieve_broad_knowledge('PLC independent_guard_token',plc_model='FX5U',top_k=3)
            self.assertTrue(any(authored_packet(row)['target']=='KNOWN' for row in found))

    def use_field_store(self):
        self.db=self.root/'field-pool.sqlite';self.manifest=self.root/'field-pool.manifest.json'
        write_runtime_sqlite(self.pooled,self.db,field_pooling=True)
        write_owner_manifest(self.pooled,self.db,self.manifest)

    def test_field_shared_owner_roundtrip_and_isolation(self):
        self.use_field_store();s=self.open()
        self.assertEqual(s.stats()['hydrated_owner_count'],0)
        for expected in self.original['entries']:
            self.assertEqual(s.get_entry(expected['opcode'],cpu='FX5U'),expected)
        a=s.get_entry('KNOWN',cpu='FX5U');a['facts'][0]['value']['items'].append(123)
        self.assertEqual(s.get_entry('KNOWN',cpu='FX5U'),self.original['entries'][0])
        self.assertEqual(s.get_entry('REFERENCE',cpu='FX5U'),self.original['entries'][1])
        self.assertGreater(s.stats()['field_load_count'],0)

    def test_field_pool_corruption_is_not_trusted_by_manifest_hash(self):
        self.use_field_store();self.sql("UPDATE runtime_fact_fields SET payload_json='{}'")
        self.refresh_hash();s=self.open()
        with self.assertRaises(ValueError):s.get_entry('KNOWN',cpu='FX5U')

    def test_missing_shared_field_rejected(self):
        self.use_field_store();self.sql('DELETE FROM runtime_fact_fields');self.refresh_hash();s=self.open()
        with self.assertRaises(ValueError):s.get_entry('KNOWN',cpu='FX5U')

    def test_duplicate_shared_field_reference_rejected(self):
        self.use_field_store()
        self.rewrite_owner(lambda e:e['facts'][0]['field_refs'].append(e['facts'][0]['field_refs'][0]),packed=True)
        s=self.open()
        with self.assertRaises(ValueError):s.get_entry('KNOWN',cpu='FX5U')

    def test_inline_and_shared_field_collision_rejected(self):
        self.use_field_store();self.rewrite_owner(lambda e:e['facts'][0].update(status='source_verified'),packed=True)
        s=self.open()
        with self.assertRaises(ValueError):s.get_entry('KNOWN',cpu='FX5U')

    def test_missing_required_table_rejected_at_open(self):
        self.sql('DROP TABLE runtime_values');self.refresh_hash()
        with self.assertRaises((ValueError,sqlite3.DatabaseError)):OwnerDefinitionStore(self.manifest)

    def test_missing_owner_payload_rejected(self):
        self.sql('DELETE FROM runtime_definitions');self.refresh_hash();s=self.open()
        with self.assertRaises(ValueError):s.get_entry('KNOWN',cpu='FX5U')
    def test_dangling_owner_dependency_rejected_even_with_updated_hashes(self):
        self.rewrite_owner(lambda e:e['facts'][1].update(depends_on=['missing']))
        s=self.open()
        with self.assertRaises(ValueError):s.get_entry('KNOWN',cpu='FX5U')
    def test_wrong_owner_scope_rejected_even_with_updated_hashes(self):
        self.rewrite_owner(lambda e:e['facts'][0]['scope'].update(models=['FX5UC']))
        s=self.open()
        with self.assertRaises(ValueError):s.get_entry('KNOWN',cpu='FX5U')

class StreamingOwnerProjectionRegression(unittest.TestCase):
    def test_schema2_stream_matches_whole_projection_and_reports_withheld_closure(self):
        from tools.runtime_definition_stream import inspect_definition_stream,iter_definition_owners
        from plc.instruction_definition_storage import compact_definitions,expand_definitions
        from knowledge.fact_corpus import project_definition
        from knowledge.runtime_fact_publication import OwnerStoreWriter
        raw=fixture()
        bad=copy.deepcopy(raw['entries'][0]['facts'][0]);bad.update(id='bad',value={'rule':'This condition is not formalized.'})
        child=copy.deepcopy(bad);child.update(id='child',value={'effect':'hold'},depends_on=['bad'])
        raw['entries'][0]['facts'].extend([bad,child])
        raw['entries'][0]['facts'][1]['value']['manual_text']='Private source wording must not be published.'
        compact=compact_definitions(raw,base_forms={'KNOWN':'KNOWN','REFERENCE':'KNOWN'})
        expanded=expand_definitions(compact)
        whole={ (e['opcode'],e['target_model']):project_definition(e) for e in expanded['entries'] }
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'schema2.json';source.write_text(json.dumps(compact))
            meta=inspect_definition_stream(source)
            owners=list(iter_definition_owners(source,meta))
            self.assertEqual(owners,expanded['entries'])
            db=root/'stream.sqlite';manifest=root/'stream.manifest.json'
            writer=OwnerStoreWriter(db,{k:v for k,v in raw.items() if k!='entries'})
            withheld=[]
            for owner in owners:
                projected,rejected=project_definition(owner)
                self.assertEqual((projected,rejected),whole[(owner['opcode'],owner['target_model'])])
                withheld.extend(rejected);writer.add_entry(projected)
            writer.finalize(manifest);store=OwnerDefinitionStore(manifest)
            try:
                for key,(expected,rejected) in whole.items():
                    got=store.get_entry(key[0],cpu=key[1]);self.assertEqual(got,expected)
                    ids={f['id'] for f in got['facts']}
                    self.assertTrue(all(set(f.get('depends_on',[]))<=ids for f in got['facts']))
                    self.assertNotIn('Private source wording',json.dumps(got))
                self.assertEqual({r['id'] for r in withheld},{'bad','child'})
                self.assertEqual(next(r['reason'] for r in withheld if r['id']=='child'),'withheld_dependency')
                self.assertIn('linguistic_conditions_require_review',store.get_entry('KNOWN',cpu='FX5U')['gaps'])
            finally:store.close()

    def test_stream_rejects_changed_metadata_and_oversized_units(self):
        from tools.runtime_definition_stream import inspect_definition_stream,iter_definition_owners
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'source.json';path.write_text(json.dumps(fixture()))
            with self.assertRaises(ValueError):inspect_definition_stream(path,max_unit_bytes=32)
            metadata=inspect_definition_stream(path);metadata['method']='changed'
            with self.assertRaises(ValueError):list(iter_definition_owners(path,metadata))


class RuntimeOwnerFieldPoolLoadingRegression(RuntimeOwnerLoadingRegression):
    FIELD_POOLING = True


if __name__=='__main__':unittest.main()
