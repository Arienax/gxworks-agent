"""Synthetic pooled storage and selected-owner retrieval regressions."""
import copy
import json
import sqlite3
import subprocess
import sys
import gzip
import tempfile
import unittest
from pathlib import Path
from knowledge.runtime_fact_projection import intern_runtime_sources
from knowledge.runtime_fact_publication import write_runtime_sqlite, hydrate_owner_packet, owner_key, project_language_features
from knowledge.fact_corpus import resolve, source_reference, UnresolvedExpression
from knowledge.fact_selection import project_packet
from knowledge.structured_facts import authored_packet
from plc.instruction_definition_storage import expand_definitions


def fixture():
    def fact(identity, dimension, **extra):
        return dict(id=identity, dimension=dimension, value={'synthetic': True},
            scope={'models':['FX5U'], 'forms':['PWM']}, status='source_verified',
            verification='source_checked', sources=[{'manual_id':'synthetic', 'revision':'A',
            'conditions':[{'context':'module', 'equals':'installed'}]}], **extra)
    return {'schema_version':1,'method':'instruction-material-compiler-v1', 'entries':[
        {'vendor':'mitsubishi','opcode':'PWM','target_model':'FX5U','facts':[
            fact('root','parameters.1.range',depends_on=['dep']),
            fact('dep','execution.scan', conditions=[{'context':'enabled','equals':True}]),
            fact('noise','effects.unrelated')]}]}


def language_feature(language='st'):
    target=language.upper()+'.CALL'
    fact=fixture()['entries'][0]['facts'][0]
    fact.update(id='call', scope={'models':['FX5U'], 'forms':[target], 'languages':[language]}, depends_on=[])
    return {'target':target,'language':language,'facts':[fact]}


class RuntimePublicationTests(unittest.TestCase):
    def test_owner_pools_are_loaded_in_batches_without_changing_facts(self):
        from knowledge.runtime_fact_publication import OwnerStoreWriter

        raw = fixture()
        raw.update(corpus_kind='authored_plc_facts_v1', fact_bundle_version='synthetic-batch')
        template = raw['entries'][0]['facts'][1]
        raw['entries'][0]['facts'] = []
        for index in range(180):
            fact = copy.deepcopy(template)
            fact.update(id=f'fact-{index}', value={'number': index},
                        conditions=[{'context': 'channel', 'equals': index}],
                        sources=[{'manual_id': f'synthetic-{index}', 'revision': 'A'}])
            raw['entries'][0]['facts'].append(fact)
        other = copy.deepcopy(raw['entries'][0])
        other['opcode'] = 'OTHER'
        for fact in other['facts']:
            fact['scope']['forms'] = ['OTHER']
        for storage in ('source', 'fields', 'rows'):
            with self.subTest(storage=storage), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'facts.sqlite'
                if storage == 'rows':
                    writer = OwnerStoreWriter(path, {k: v for k, v in raw.items() if k != 'entries'}, fact_rows=True)
                    try:
                        writer.add_entry(raw['entries'][0])
                        writer.add_entry(other)
                        writer.finalize(Path(temp) / 'facts.manifest.json')
                    finally:
                        writer.close()
                else:
                    write_runtime_sqlite(intern_runtime_sources({**raw, 'entries': [raw['entries'][0], other]},
                        intern_values=True), path, field_pooling=storage == 'fields')
                conn = sqlite3.connect(path)
                conn.row_factory = sqlite3.Row
                try:
                    queries = []
                    conn.set_trace_callback(queries.append)
                    actual = hydrate_owner_packet(conn, owner_key(raw['entries'][0]))
                    conn.set_trace_callback(None)
                    self.assertEqual(actual['value'], raw['entries'][0])
                    for table in ('runtime_sources', 'runtime_values', 'runtime_fact_fields'):
                        count = sum(f'FROM {table} WHERE' in query for query in queries)
                        self.assertEqual(count, 0 if table == 'runtime_fact_fields' and storage == 'source' else 1)
                    actual['value']['facts'][0]['scope']['models'].append('changed')
                    self.assertEqual(hydrate_owner_packet(conn, owner_key(raw['entries'][0]))['value'], raw['entries'][0])
                finally:
                    conn.close()

    def test_language_bodies_are_validated_only_when_selected(self):
        from unittest.mock import patch
        from knowledge.runtime_fact_publication import write_owner_manifest
        from knowledge.structured_facts import structured_fact_targets
        from plc.instructions import InstructionRegistry
        from contextlib import closing
        import hashlib

        for invalid in ('value', 'scope', 'no_alias'):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as temp:
                raw = fixture()
                raw['fact_bundle_version'] = 'synthetic:language-selection'
                good, bad = language_feature('st'), language_feature('fbd')
                for feature in (good, bad):
                    feature['aliases'] = [feature['target']]
                if invalid == 'no_alias':
                    bad.pop('aliases')
                raw['language_facts'] = [good, bad]
                database = Path(temp) / 'facts.sqlite'
                manifest = Path(temp) / 'instruction_definitions.manifest.json'
                pooled = intern_runtime_sources(raw, intern_values=True)
                write_runtime_sqlite(pooled, database)
                changed = copy.deepcopy(bad)
                if invalid != 'scope':
                    changed['facts'][0]['value'] = {'condition': 'unclassified language condition'}
                else:
                    changed['facts'][0]['scope']['forms'] = ['FBD.OTHER']
                text = json.dumps(changed, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
                with closing(sqlite3.connect(database)) as connection:
                    connection.execute('UPDATE runtime_language_features SET payload_json=?,sha256=? WHERE target=?',
                        (text, hashlib.sha256(text.encode()).hexdigest(), bad['target']))
                    connection.commit()
                write_owner_manifest(raw, database, manifest)
                registry = InstructionRegistry()
                registry.load_instruction_owner_store(manifest)
                try:
                    self.assertEqual(registry.language_feature('ST.CALL', cpu='FX5U'), good)
                    with patch('plc.instructions.DEFAULT_INSTRUCTION_REGISTRY', registry), \
                            patch('knowledge.structured_facts._declared_instruction_alias_targets', return_value=[]):
                        self.assertEqual(structured_fact_targets('MOV', plc_model='FX5U', language='ladder')['language_features'], [])
                        self.assertEqual(structured_fact_targets('ST.CALL', plc_model='FX5U', language='st')['language_features'], ['ST.CALL'])
                        if invalid == 'no_alias':
                            self.assertEqual(structured_fact_targets('FBD.CALL', plc_model='FX5U', language='fbd')['language_features'], [])
                        else:
                            with self.assertRaises(ValueError):
                                structured_fact_targets('FBD.CALL', plc_model='FX5U', language='fbd')
                    with self.assertRaises(ValueError):
                        registry.language_feature('FBD.CALL', cpu='FX5U')
                    with self.assertRaises(ValueError):
                        registry._definition_owner_store.metadata
                finally:
                    registry._definition_owner_store.close()

    def test_pooled_inventory_mutation_does_not_alias_other_owner_or_spec(self):
        from plc.instructions import load_default_instruction_registry
        raw=fixture(); other=copy.deepcopy(raw['entries'][0]); other['target_model']='FX5UC'
        for fact in other['facts']: fact['scope']['models']=['FX5UC']
        raw['entries'].append(other)
        pooled=intern_runtime_sources(raw,intern_values=True)
        before=copy.deepcopy(pooled)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'definitions.json';path.write_text(json.dumps(pooled))
            registry=load_default_instruction_registry();registry.load_instruction_definitions(path)
            registry.definition_inventory[0]['facts'][0]['value']['synthetic']=False
            registry.definition_inventory[0]['facts'][0]['sources'][0]['revision']='mutated'
            second=registry.definition_entry('PWM',cpu='FX5UC')['facts'][0]
            self.assertTrue(second['value']['synthetic'])
            self.assertEqual(second['sources'][0]['revision'],'A')
            spec=registry.resolve('PWM',cpu='FX5U')
            self.assertTrue(spec.definition_facts[0].value['synthetic'])
            self.assertEqual(spec.definition_facts[0].sources[0]['revision'],'A')
            self.assertEqual(pooled,before)
            for value in pooled['value_catalog'].values(): value['synthetic']='changed_input'
            self.assertTrue(registry.definition_entry('PWM',cpu='FX5UC')['facts'][0]['value']['synthetic'])

    def test_complete_language_namespace_supports_existing_dotted_and_colon_extractors(self):
        from scripts.compile_instruction_semantics import replace_source_namespaces
        for prefix,identity in [('fbd.','fbd.types'),('authored:ql:','authored:ql:arguments')]:
            with self.subTest(prefix=prefix):
                original={'id':identity,'dimension':'parameters.synthetic','value':{'limit':1},'sources':[]}
                fresh={**original,'value':{'limit':2}}
                facts,history=replace_source_namespaces([original],[fresh],[prefix])
                self.assertEqual(facts,[fresh]);self.assertEqual(history,[original])
        for prefix in ['fbd','fbd.*','fbd..',' fbd.']:
            with self.subTest(prefix=prefix),self.assertRaises(ValueError):
                replace_source_namespaces([],[],[prefix])

    def test_fbd_type_profile_values_are_formal_without_merging_interface_limits(self):
        from tools.fbd_fact_review import formal_type_profile_values
        from knowledge.fact_corpus import typed_value
        values=formal_type_profile_values()
        self.assertEqual(typed_value(values),values)
        string=values['fbd.string_profile_selection']['string_length_profile_selection']
        self.assertEqual(string['fxcpu_application_table']['maximum_characters'],50)
        self.assertEqual(string['general_structured_project']['maximum_characters'],255)
        self.assertEqual(string['general_structured_project']['FX_support_footnote'],['FX3U','FX3UC'])
        self.assertIs(string['support_note']['older_FX_inherits_general_STRING_support'],False)
        bounds=values['fbd.data_representation_basics']
        self.assertEqual(bounds['word_data']['signed'],{'bits':16,'minimum':-32768,'maximum':32767})
        self.assertEqual(bounds['double_word_data']['unsigned_bit_string']['maximum'],4294967295)

    def test_fbd_language_resource_failures_remain_unverified_and_source_bound(self):
        from plc.fbd_semantics import FBDSemanticError, language_rules
        from knowledge.fact_corpus import UnresolvedExpression
        from unittest.mock import Mock
        for target in ['FBD.TYPES','FBD.CALL.BINDING','FBD.OPERATORS']:
            for error in [UnresolvedExpression('unclassified_expression'), ValueError('Wrong language scope')]:
                with self.subTest(target=target,error=type(error).__name__):
                    registry=Mock()
                    registry.fact_bundle_current.return_value=True
                    registry.language_feature.side_effect=error
                    with self.assertRaises(FBDSemanticError) as raised:
                        language_rules(target,'Q03UDV',registry=registry)
                    finding=raised.exception.finding
                    self.assertEqual(finding['code'],'fbd_rule_resource')
                    self.assertEqual(finding['severity'],'unverified')
                    self.assertEqual((finding['rule'],finding['plc_model']),(target,'Q03UDV'))
                    self.assertEqual(finding['reason'],type(error).__name__)
                    registry.language_feature.assert_called_once_with(target,language='fbd')
        registry=Mock()
        registry.fact_bundle_current.return_value=True
        registry.language_feature.side_effect=KeyError('unexpected_implementation_error')
        with self.assertRaises(KeyError):
            language_rules('FBD.TYPES','Q03UDV',registry=registry)

    def test_fbd_graph_inspection_reports_invalid_rules_instead_of_crashing(self):
        from gxw.project_templates import blank_fbd_baseline
        from gxw.object_model import read_project_context
        from gxw.fbd_validation import inspect_graph
        from knowledge.fact_corpus import UnresolvedExpression
        from unittest.mock import Mock,patch
        context=read_project_context(blank_fbd_baseline('Q03UDV'))
        registry=Mock()
        registry.fact_bundle_current.return_value=True
        registry.language_feature.side_effect=UnresolvedExpression('unclassified_expression')
        with patch('plc.instructions.DEFAULT_INSTRUCTION_REGISTRY',registry):
            result=inspect_graph(context.program,context.sources)
        findings=[f for f in result['findings'] if f['code']=='fbd_rule_resource']
        self.assertEqual({f['rule'] for f in findings},
                         {'FBD.LITERALS','FBD.TYPES','FBD.OPERATORS','FBD.CALL.BINDING'})
        self.assertTrue(all(f['severity']=='unverified' and f['source_name']==context.program.logical_name for f in findings))

    def test_language_features_remain_separate_and_inline(self):
        from knowledge.runtime_fact_projection import project_runtime_definitions, expand_runtime_sources
        from plc.instructions import InstructionRegistry
        features=[language_feature('st'),language_feature('fbd')]
        before=copy.deepcopy(features)
        projected,rejected=project_language_features(features)
        self.assertFalse(rejected)
        self.assertEqual(features,before)
        self.assertEqual(projected,features)
        raw=fixture();raw['language_facts']=projected
        semantic,_,_=project_runtime_definitions(raw)
        pooled=intern_runtime_sources(semantic,intern_values=True)
        self.assertEqual(pooled['language_facts'],features)
        self.assertEqual(expand_runtime_sources(pooled)['language_facts'],features)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'definitions.json';path.write_text(json.dumps(pooled))
            registry=InstructionRegistry();registry.load_instruction_definitions(path)
            self.assertEqual(len(registry.language_facts),2)
            self.assertEqual(registry.language_feature('ST.CALL',language='st'),features[0])

    def test_language_source_withdrawal_withholds_dependants(self):
        feature=language_feature()
        dependant=copy.deepcopy(feature['facts'][0]);dependant.update(id='effect',depends_on=['call'])
        feature['facts'][0]['sources']=[]
        feature['facts'].append(dependant)
        out,rejected=project_language_features([feature])
        self.assertEqual(out[0]['facts'],[])
        reasons={fact['id']:fact['reason'] for fact in rejected[0]['withheld']}
        self.assertEqual(reasons,{'call':'missing_public_source','effect':'withheld_dependency'})

    def test_language_wrong_scope_unknown_metadata_and_cycle_rejected(self):
        variants=[]
        wrong=language_feature();wrong['facts'][0]['scope']['languages']=['fbd'];variants.append(wrong)
        wrong=language_feature();wrong['facts'][0]['scope']['forms']=['ST.OTHER'];variants.append(wrong)
        wrong=language_feature();wrong['private_review']='not_public';variants.append(wrong)
        wrong=language_feature();wrong['facts'][0]['depends_on']=['call'];variants.append(wrong)
        for wrong in variants:
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                project_language_features([wrong])


    def test_cli_keeps_private_evidence_separate_and_rejects_overwrite(self):
        script=Path(__file__).resolve().parents[1]/"scripts"/"build_runtime_projection.py"
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); source=root/"input.json"; raw=fixture();raw["language_facts"]=[language_feature()];source.write_text(json.dumps(raw))
            output=root/"runtime"; evidence=root/"private"/"evidence.gz"
            command=[sys.executable,str(script),"--input",str(source),"--output-dir",str(output),"--private-evidence",str(evidence)]
            result=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            with gzip.open(evidence,"rt") as stream: self.assertEqual(json.load(stream),raw)
            self.assertEqual({p.name for p in output.iterdir()},{"instruction_definitions.json.gz","plc_facts.sqlite","instruction_definitions.manifest.json","receipt.json"})
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)


    def test_public_source_reference_projection_is_idempotent(self):
        raw = {"id": "123", "manual_id": "synthetic", "revision": "A", "pdf_pages": [1,2]}
        once = source_reference(raw)
        self.assertEqual(once["source_ref"], "synthetic:A:pages=1,2")
        self.assertEqual(source_reference(once), once)
        with self.assertRaises(UnresolvedExpression):
            source_reference(dict(once, source_ref="unrelated prose"))

    def test_pool_storage_and_selected_owner_round_trip(self):
        source=fixture()
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        source['fact_bundle_version']=DEFAULT_INSTRUCTION_REGISTRY.fact_bundle_version
        before=copy.deepcopy(source)
        pooled=intern_runtime_sources(source, intern_values=True)
        self.assertEqual(expand_definitions(pooled),source)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'facts.sqlite'
            receipt=write_runtime_sqlite(pooled,path)
            conn=sqlite3.connect(path); conn.row_factory=sqlite3.Row
            try:
                packet=hydrate_owner_packet(conn,owner_key(source['entries'][0]))
                self.assertEqual(packet['value'],source['entries'][0])
                self.assertEqual(receipt['chunk_fact_copies'],0)
                from knowledge.core import _fts_references, _entity_references
                from knowledge.index_access import schema
                self.assertTrue(_fts_references(conn,schema(conn),'synthetic',10,plc_model='FX5U',task_type='analysis'))
                self.assertTrue(_entity_references(conn,schema(conn),['PWM'],'FX5U','analysis'))
                self.assertNotIn('"facts"',conn.execute('SELECT text FROM chunks').fetchone()[0])
                rows=resolve('instruction',['PWM'],plc_model='FX5U',connection=conn)
                self.assertEqual(len(rows),1)
                self.assertEqual(authored_packet(rows[0])['value'],source['entries'][0])
                selected=project_packet(authored_packet(rows[0]),plc_model='FX5U',aspects=['operand_range'])
                self.assertEqual([f['id'] for f in selected['value']['facts']],['root','dep'])
                self.assertTrue(selected['fact_selection']['gaps'])
                self.assertEqual(resolve('instruction',['PWM'],plc_model='FX3U',connection=conn),[])
                conn.execute('DELETE FROM runtime_sources')
                with self.assertRaises(ValueError):
                    resolve('instruction',['PWM'],plc_model='FX5U',connection=conn)
                with self.assertRaises(FileExistsError):
                    write_runtime_sqlite(pooled,path)
            finally: conn.close()
        self.assertEqual(source,before)

    def test_registry_pooled_round_trip_and_unknown_transport(self):
        from plc.instructions import InstructionRegistry
        source = fixture()
        pooled = intern_runtime_sources(source, intern_values=True)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"definitions.json"
            path.write_text(json.dumps(pooled))
            registry = InstructionRegistry()
            registry.load_instruction_definitions(path)
            self.assertEqual(registry.definition_entry("PWM",cpu="FX5U"),source["entries"][0])
            returned=registry.definition_entry("PWM",cpu="FX5U")
            returned['facts'][0]['value']['synthetic']=False
            self.assertTrue(registry.definition_entry("PWM",cpu="FX5U")['facts'][0]['value']['synthetic'])
            pooled["value_serialization"] = "unknown"
            path.write_text(json.dumps(pooled))
            with self.assertRaises(ValueError): registry.load_instruction_definitions(path)
            with self.assertRaises(ValueError): write_runtime_sqlite(pooled,Path(temp)/"bad.sqlite")

    def test_missing_value_fails_closed(self):
        pooled=intern_runtime_sources(fixture(),intern_values=True)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"facts.sqlite"; write_runtime_sqlite(pooled,path)
            conn=sqlite3.connect(path)
            try:
                conn.execute("DELETE FROM runtime_values")
                with self.assertRaises(ValueError): hydrate_owner_packet(conn,owner_key(pooled["entries"][0]))
            finally: conn.close()

    def test_tampered_source_fails_closed(self):
        pooled=intern_runtime_sources(fixture())
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'facts.sqlite'; write_runtime_sqlite(pooled,path)
            conn=sqlite3.connect(path)
            try:
                conn.execute("UPDATE runtime_sources SET payload_json='{}'")
                with self.assertRaises(ValueError): hydrate_owner_packet(conn,owner_key(pooled['entries'][0]))
            finally: conn.close()


class DiagnosticCompilationTests(unittest.TestCase):
    def definitions(self):
        return {'schema_version':1,'recovery_profiles':{'review':{'actions':[{'action':'review_program'}]}},
            'groups':[{'source':{'manual_id':'synthetic','manual_number':'TEST','revision':'A'},
                'models':['FX3U'],'register':'D8061','error_flag':'M8061','subsystem':'hardware',
                'pdf_pages':[7],'plc_operation':'stopped','recovery_profile':'review',
                'records':[{'code':'6105','meaning':'watchdog_timeout','trigger':{'left':'scan_time','operator':'gt','right':{'device':'D8000'}},
                            'cause':{'limit_exceeded':True}}]}]}

    def source(self):
        c=sqlite3.connect(':memory:')
        c.executescript("""CREATE TABLE manuals(manual_id TEXT,manual_number TEXT,revision TEXT,plc_models TEXT);
            CREATE TABLE page_artifacts(manual_id TEXT,pdf_page INTEGER);
            CREATE TABLE error_records(id INTEGER,manual_id TEXT,error_code_norm TEXT,pdf_page INTEGER);
            INSERT INTO manuals VALUES('synthetic','TEST','A','FX3U,FX3UC');
            INSERT INTO page_artifacts VALUES('synthetic',7);
            INSERT INTO error_records VALUES(20,'synthetic','6105',7);""")
        self.addCleanup(c.close);return c

    def corpus(self):
        from knowledge.evidence import text_sha256
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        c.executescript("""CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE chunks(id INTEGER PRIMARY KEY,manual_id TEXT,manual_number TEXT,revision TEXT,
                plc_models TEXT,pdf_page INTEGER,pdf_page_end INTEGER,section TEXT,chunk_type TEXT,instruction_opcode TEXT,
                text TEXT,text_sha256 TEXT,entities TEXT,cjk_bigrams TEXT,content_origin TEXT,distribution_status TEXT,
                fact_kind TEXT,fact_target TEXT,metadata_json TEXT,task_types TEXT);
            CREATE TABLE facts(id TEXT,kind TEXT,target TEXT,plc_model TEXT,chunk_id INTEGER,payload_json TEXT);
            CREATE TABLE entity_index(entity_norm TEXT,entity TEXT,entity_type TEXT,plc_models TEXT,task_types TEXT,
                                      manual_id TEXT,chunk_id INTEGER,occurrences INTEGER);
            CREATE VIRTUAL TABLE chunks_fts USING fts5(text,content='chunks',content_rowid='id');
            CREATE TABLE vector_embeddings(chunk_id INTEGER,model TEXT,dimensions INTEGER,vector BLOB,vector_norm REAL,content_sha256 TEXT);
            INSERT INTO meta VALUES('corpus_kind','authored_plc_facts_v1');""")
        packet={'kind':'error','target':'6105','scope':{'models':['FX3U']},'value':{'cause':'unknown','recovery':'unknown'},
            'technical_status':'candidate_evidence','gaps':['specific_trigger_cause_recovery_require_review'],
            'sources':[{'manual_id':'synthetic','manual_number':'TEST','revision':'A','pdf_page':7}]}
        text=json.dumps(packet)
        c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (1,'synthetic','TEST','A','FX3U',7,7,'error / 6105','error','',text,text_sha256(text),'6105','',
             'authored_fact','fact_representation_reviewed','error','6105','{}','analysis|debug'))
        c.execute('INSERT INTO facts VALUES(?,?,?,?,?,?)',('diagnostic:20','error','6105','FX3U',1,'{}'))
        c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (2,'synthetic','TEST','A','FX3U',7,7,'example','example','','untouched',text_sha256('untouched'),'','',
             'original_case','fact_representation_reviewed','example','case','{}','analysis'))
        self.addCleanup(c.close);return c

    def test_exact_source_binding_and_finite_fields_preserve_register_model_identity(self):
        from tools.fact_diagnostic_review import expand_diagnostic_definitions,bind_diagnostic_sources
        d=self.definitions();g=d['groups'][0]
        g['records'].append({'code_template':'{unit}020','instances':[{'unit':0},{'unit':7}],
                             'faulting_unit':{'binding':'unit'},'meaning':'block_checksum'})
        result=bind_diagnostic_sources(self.source(),expand_diagnostic_definitions(d))
        self.assertEqual([r['target'] for r in result],['6105','0020','7020'])
        self.assertEqual(result[0]['id'],'diagnostic:20')
        self.assertEqual(result[-1]['value']['faulting_unit'],7)
        self.assertEqual(result[0]['conditions'],[{'required_device_register':'D8061'}])
        self.assertTrue(all(r['scope']=={'models':['FX3U']} for r in result))
        self.assertEqual(result[0]['value']['recovery']['actions'],[{'action':'review_program'}])
        self.assertEqual(result[0]['status'],'candidate_evidence')
        self.assertIn('individual_CPU_feature_conditions_not_certified',result[0]['gaps'])

    def test_invalid_source_scope_or_ambiguous_index_row_is_rejected(self):
        from tools.fact_diagnostic_review import expand_diagnostic_definitions,bind_diagnostic_sources
        for change in ('revision','page','model'):
            with self.subTest(change=change):
                d=self.definitions();g=d['groups'][0]
                if change=='revision':g['source']['revision']='B'
                elif change=='page':g['pdf_pages']=[8]
                else:g['models']=['FX5U']
                with self.assertRaises(ValueError):bind_diagnostic_sources(self.source(),expand_diagnostic_definitions(d))
        c=self.source();c.execute("INSERT INTO error_records VALUES(21,'synthetic','6105',7)")
        with self.assertRaises(ValueError):bind_diagnostic_sources(c,expand_diagnostic_definitions(self.definitions()))
        d=self.definitions();d['groups'][0]['records'][0]['source_record_id']=20
        self.assertEqual(bind_diagnostic_sources(c,expand_diagnostic_definitions(d))[0]['id'],'diagnostic:20')

    def test_missing_and_partial_fields_are_not_certified_by_a_record_marker(self):
        from tools.fact_diagnostic_review import expand_diagnostic_definitions
        d=self.definitions();r=d['groups'][0]['records'][0]
        r.update(trigger={},cause={'mechanism':'known','threshold':'not_stated'},recovery={'operator_action':'not_stated'})
        value=expand_diagnostic_definitions(d)[0]
        self.assertEqual(value['value']['knowledge_status_by_field']['trigger'],'unknown')
        self.assertEqual(value['value']['knowledge_status_by_field']['cause'],'partially_source_verified')
        self.assertEqual(value['value']['knowledge_status_by_field']['recovery'],'unknown')
        self.assertIn('cause_not_stated',value['gaps'])
        self.assertEqual(value['status'],'candidate_evidence')

    def test_update_preserves_other_domains_and_old_claims_and_is_idempotent(self):
        from tools.fact_diagnostic_review import expand_diagnostic_definitions,bind_diagnostic_sources
        from scripts.compile_domain_semantics import update_diagnostic_records,_packet
        from shared.model_context import technical_fact_packet
        c=self.corpus();other=tuple(c.execute('SELECT * FROM chunks WHERE id=2').fetchone())
        records=bind_diagnostic_sources(self.source(),expand_diagnostic_definitions(self.definitions()))
        first=update_diagnostic_records(c,records)
        self.assertEqual(first,{'changed_ids':{1},'records_added':0,'records_updated':1})
        row=c.execute('SELECT * FROM chunks WHERE id=1').fetchone();packet=_packet(row['text'])
        self.assertEqual(packet['value']['recovery']['actions'][0]['action'],'review_program')
        self.assertEqual(packet['conditions'],[{'required_device_register':'D8061'}])
        self.assertIn('unknown',json.dumps(json.loads(row['metadata_json'])['superseded_facts']))
        self.assertNotIn('superseded_facts',technical_fact_packet(packet))
        self.assertEqual(tuple(c.execute('SELECT * FROM chunks WHERE id=2').fetchone()),other)
        self.assertEqual(update_diagnostic_records(c,records),{'changed_ids':set(),'records_added':0,'records_updated':0})
        self.assertEqual(c.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'watchdog_timeout'").fetchone()[0],1)

    def test_equal_display_codes_in_different_registers_remain_distinct(self):
        from tools.fact_diagnostic_review import expand_diagnostic_definitions,bind_diagnostic_sources
        from scripts.compile_domain_semantics import update_diagnostic_records,_packet
        d=self.definitions();second=copy.deepcopy(d['groups'][0]);second['register']='D8067';second['error_flag']='M8067'
        second['records']=[{'code':'0020','meaning':'different_subsystem_error'}]
        d['groups'][0]['records'].append({'code':'0020','meaning':'hardware_error'})
        d['groups'].append(second)
        c=self.corpus();update_diagnostic_records(c,bind_diagnostic_sources(self.source(),expand_diagnostic_definitions(d)))
        packets=[_packet(row[0]) for row in c.execute("SELECT text FROM chunks WHERE fact_kind='error' AND fact_target='0020'")]
        self.assertEqual({p['value']['register'] for p in packets},{'D8061','D8067'})
        self.assertEqual({p['value']['meaning'] for p in packets},{'hardware_error','different_subsystem_error'})

    def test_diagnostic_coverage_uses_content_context_and_all_requested_fields(self):
        from tools.fact_diagnostic_review import expand_diagnostic_definitions
        from knowledge.structured_facts import retrieval_coverage, fact_record_coverage
        packet=expand_diagnostic_definitions(self.definitions())[0]
        def row(value):return {'id':'synthetic','fact_kind':'error','fact_target':'6105','text':json.dumps({**value,'content_origin':'authored_fact'})}
        report=retrieval_coverage('错误码6105的原因和处理方法，检查D8000', [row(packet)], plc_model='FX3U', targets={'errors':['6105']})
        item=report['targets'][0];states={a['aspect']:a['status'] for a in item['aspects']}
        self.assertEqual(states['cause'],'answered');self.assertEqual(states['recovery'],'answered')
        self.assertEqual(states['applicability'],'unverified');self.assertEqual(item['status'],'incomplete')
        incomplete=copy.deepcopy(packet);incomplete.update(status='source_verified',gaps=[],conditions=[])
        incomplete['value']['cpu_applicability']={'scope_basis':'explicit_CPU_row'}
        incomplete['value']['cause']='unknown';incomplete['value']['covered_aspects']=['all']
        self.assertFalse(fact_record_coverage(row(incomplete)))
        states={a['aspect']:a['status'] for a in retrieval_coverage('错误码6105原因',[row(incomplete)],plc_model='FX3U',targets={'errors':['6105']})['targets'][0]['aspects']}
        self.assertNotEqual(states['cause'],'answered')
        complete=copy.deepcopy(packet);complete.update(status='source_verified',gaps=[],conditions=[])
        complete['value']['cpu_applicability']={'scope_basis':'explicit_CPU_row'}
        self.assertEqual(retrieval_coverage('错误码6105的原因和处理方法',[row(complete)],plc_model='FX3U',targets={'errors':['6105']})['targets'][0]['status'],'complete')

    def test_diagnostic_coverage_cannot_choose_one_register_for_an_ambiguous_code(self):
        from tools.fact_diagnostic_review import expand_diagnostic_definitions
        from knowledge.structured_facts import retrieval_coverage
        packet=expand_diagnostic_definitions(self.definitions())[0]
        other=copy.deepcopy(packet);other['value']['register']='D8067'
        other['value']['cause']='not_stated'
        rows=[{'id':str(i),'fact_kind':'error','fact_target':'6105','text':json.dumps({**value,'content_origin':'authored_fact'})} for i,value in enumerate([packet,other])]
        ambiguous=retrieval_coverage('错误码6105的原因',rows,plc_model='FX3U',targets={'errors':['6105']})['targets'][0]
        self.assertEqual(ambiguous['reason'],'register_context_required')
        explicit=retrieval_coverage('D8067 错误码6105的原因',rows,plc_model='FX3U',targets={'errors':['6105']})['targets'][0]
        self.assertEqual(next(a['status'] for a in explicit['aspects'] if a['aspect']=='cause'),'missing')

    def test_overlapping_diagnostic_editions_with_opposite_actions_remain_conflicting(self):
        from tools.fact_diagnostic_review import expand_diagnostic_definitions
        from knowledge.structured_facts import retrieval_coverage
        packet=expand_diagnostic_definitions(self.definitions())[0]
        other=copy.deepcopy(packet)
        packet['value']['recovery']={'actions':[{'action':'increase_setting'}]}
        other['value']['recovery']={'actions':[{'action':'decrease_setting'}]}
        def row(value):
            return {'fact_kind':'error','fact_target':'6105','text':json.dumps({**value,'content_origin':'authored_fact'})}
        result=retrieval_coverage('D8061 错误码6105处理方法',[row(packet),row(other)],plc_model='FX3U',targets={'errors':['6105']})['targets'][0]
        self.assertEqual(next(a['status'] for a in result['aspects'] if a['aspect']=='recovery'),'conflict')
        self.assertEqual(result['status'],'incomplete')
        other['scope']['models']=['FX3G']
        result=retrieval_coverage('D8061 错误码6105处理方法',[row(packet),row(other)],plc_model='FX3U',targets={'errors':['6105']})['targets'][0]
        self.assertEqual(next(a['status'] for a in result['aspects'] if a['aspect']=='recovery'),'answered')
        packet['value']['cause']={'available':True,'knowledge_status':'source_verified'}
        result=retrieval_coverage('错误码6105原因',[row(packet)],plc_model='FX3U',targets={'errors':['6105']})['targets'][0]
        self.assertEqual(next(a['status'] for a in result['aspects'] if a['aspect']=='cause'),'missing')

    def test_chapter_scope_correction_keeps_unknown_content_and_other_domains(self):
        from scripts.compile_domain_semantics import diagnostic_scope_records, update_diagnostic_records, _packet
        c=self.corpus();source=self.source()
        before=tuple(c.execute('SELECT * FROM chunks WHERE id=2').fetchone())
        packet=_packet(c.execute('SELECT text FROM chunks WHERE id=1').fetchone()[0])
        packet['scope']['models']=['FX3U','FX3UC']
        c.execute("UPDATE chunks SET text=?,plc_models='FX3U|FX3UC' WHERE id=1",(json.dumps(packet),))
        c.execute("INSERT INTO facts VALUES('diagnostic:20','error','6105','FX3UC',1,'{}')")
        section={'source':{'manual_id':'synthetic','manual_number':'TEST','revision':'A'},
                 'models':['FX3UC'],'pdf_page_start':7,'pdf_page_end':7,'heading_pdf_pages':[7]}
        records=diagnostic_scope_records(c,source,[section])
        self.assertEqual(records[0]['value'],packet['value'])
        self.assertEqual(records[0]['verification'],'not_performed')
        update_diagnostic_records(c,records)
        result=_packet(c.execute('SELECT text FROM chunks WHERE id=1').fetchone()[0])
        self.assertEqual(result['scope']['models'],['FX3UC'])
        self.assertEqual(result['value']['cause'],'unknown')
        self.assertEqual(result['status'],'candidate_evidence')
        self.assertEqual([r[0] for r in c.execute('SELECT plc_model FROM facts WHERE chunk_id=1')],['FX3UC'])
        self.assertEqual(tuple(c.execute('SELECT * FROM chunks WHERE id=2').fetchone()),before)
        self.assertEqual(diagnostic_scope_records(c,source,[section]),[])
        section['source']['revision']='B'
        with self.assertRaises(ValueError):diagnostic_scope_records(c,source,[section])

    def test_cpu_section_variants_and_source_qualified_ids_do_not_replace_each_other(self):
        from tools.fact_diagnostic_review import expand_diagnostic_definitions,bind_diagnostic_sources
        from scripts.compile_domain_semantics import update_diagnostic_records
        d=self.definitions();second=copy.deepcopy(d['groups'][0]);second['models']=['FX3UC']
        second['records'][0]['meaning']='different_CPU_fault'
        # No indexed row for this code: source/model qualification must still
        # keep the two authored instances independently addressable.
        d['groups'][0]['records'][0]['code']='6123';second['records'][0]['code']='6123'
        d['groups'].append(second)
        records=bind_diagnostic_sources(self.source(),expand_diagnostic_definitions(d))
        self.assertNotEqual(records[0]['id'],records[1]['id'])
        c=self.corpus()
        update_diagnostic_records(c,records)
        self.assertEqual(c.execute("SELECT count(DISTINCT id) FROM facts WHERE target='6123'").fetchone()[0],2)
        # Stable adoption of the old unqualified identity changes no claim and
        # leaves one row, rather than duplicating the same source definition.
        new_id=records[0]['id'];old_id='diagnostic:synthetic:A:D8061:6123'
        c.execute('UPDATE facts SET id=? WHERE id=?',(old_id,new_id))
        result=update_diagnostic_records(c,records)
        self.assertEqual(result['records_added'],0)
        self.assertEqual(result['records_updated'],1)
        self.assertEqual(c.execute('SELECT count(*) FROM facts WHERE id=?',(old_id,)).fetchone()[0],0)
        self.assertEqual(c.execute("SELECT count(DISTINCT chunk_id) FROM facts WHERE target='6123'").fetchone()[0],2)
        self.assertFalse(update_diagnostic_records(c,records)['changed_ids'])

    def test_dense_refresh_changes_only_the_reviewed_vectors(self):
        import numpy as np
        from tools.fact_diagnostic_review import expand_diagnostic_definitions,bind_diagnostic_sources
        from scripts.compile_domain_semantics import update_diagnostic_records,refresh_dense_entries
        from knowledge.evidence import text_sha256
        c=self.corpus();c.executemany('INSERT INTO meta VALUES(?,?)',[
            ('vector_status','ready'),('vector_model','synthetic_lsa'),('vector_corpus_sha256','original')])
        untouched=np.array([0.6,0.8],dtype=np.float32)
        for identity,vector,text in [(1,np.array([0,1],dtype=np.float32),c.execute('SELECT text FROM chunks WHERE id=1').fetchone()[0]),(2,untouched,'untouched')]:
            c.execute('INSERT INTO vector_embeddings VALUES(?,?,?,?,?,?)',(identity,'synthetic_lsa',2,vector.tobytes(),1.0,text_sha256(text)))
        result=update_diagnostic_records(c,bind_diagnostic_sources(self.source(),expand_diagnostic_definitions(self.definitions())))
        with tempfile.TemporaryDirectory() as temp:
            basis,output=Path(temp)/'basis.npz',Path(temp)/'updated.npz'
            np.savez_compressed(basis,chunk_ids=np.array([1,2]),chunk_vectors=np.array([[0,1],untouched],dtype=np.float32),
                idf=np.ones(2,dtype=np.float32),components=np.eye(2,dtype=np.float32),
                feature_names_json=np.array(json.dumps(['w:watchdog_timeout','w:absent'])),
                metadata_json=np.array(json.dumps({'model':'synthetic_lsa','chunks':2,'corpus_sha256':'original'})))
            refresh_dense_entries(c,basis,output,result['changed_ids'])
            with np.load(output,allow_pickle=False) as archive:
                self.assertTrue(np.array_equal(archive['chunk_vectors'][1],untouched))
                self.assertTrue(np.allclose(archive['chunk_vectors'][0],[1,0]))
                meta=json.loads(str(archive['metadata_json'].item()))
            self.assertEqual(meta['basis']['corpus_sha256'],'original')
            self.assertEqual(c.execute("SELECT value FROM meta WHERE key='vector_corpus_sha256'").fetchone()[0],meta['corpus_sha256'])
            self.assertEqual(c.execute('SELECT content_sha256 FROM vector_embeddings WHERE chunk_id=1').fetchone()[0],c.execute('SELECT text_sha256 FROM chunks WHERE id=1').fetchone()[0])


class DeviceCompilationTests(unittest.TestCase):
    source=DiagnosticCompilationTests.source
    corpus=DiagnosticCompilationTests.corpus

    def definitions(self):
        return {'schema_version':1,'definitions':[{
            'id':'synthetic_status','devices':['D9000'],'models':['FX3U'],
            'status':'source_verified','description':'Private prose is not compiled.',
            'access':'system','value_type':'word','conditions':[
                {'field':'cpu_version','operator':'at_least','value':'3.00','note':'Source footnote prose'}],
            'notes':[],'search_terms':['synthetic status'],
            'gaps':['clear_rule_not_stated'],
            'semantic_facts':{'function':'unit_access_error_bitmap','width_bits':16,
                'bit_layout':[{'offset':0,'unit':0,'when_one':'access_error'}],
                'unavailable_bits':{'first':8,'last':15}},
            'sources':[{'manual_id':'synthetic','manual_number':'TEST','revision':'A','pdf_pages':[7]}]}]}

    def test_canonical_source_bindings_keep_device_semantics_and_conditions(self):
        from tools.fact_device_review import reviewed_device_records
        from plc.device_definitions import parse_device_definitions
        original=self.definitions();before=copy.deepcopy(original)
        result,=reviewed_device_records(self.source(),original)
        self.assertEqual(original,before)
        self.assertEqual(result['kind'],'device')
        self.assertEqual(result['target'],'D9000')
        self.assertEqual(result['value']['semantic_facts'],original['definitions'][0]['semantic_facts'])
        self.assertEqual(result['conditions'],[{'field':'cpu_version','operator':'at_least','value':'3.00'}])
        self.assertNotIn('Private prose',json.dumps(result))
        self.assertNotIn('Source footnote prose',json.dumps(result))
        self.assertNotIn('word_bit_view',result['value'])
        parsed=parse_device_definitions(original);parsed[0]['semantic_facts']['width_bits']=32
        self.assertEqual(original,before)

    def test_wrong_manual_edition_page_and_model_are_rejected(self):
        from tools.fact_device_review import reviewed_device_records
        for change in ('revision','page','model'):
            d=self.definitions();row=d['definitions'][0]
            if change=='revision':row['sources'][0]['revision']='B'
            elif change=='page':row['sources'][0]['pdf_pages']=[8]
            else:row['models']=['FX5U']
            with self.subTest(change=change),self.assertRaises(ValueError):
                reviewed_device_records(self.source(),d)

    def test_complementary_manuals_keep_exact_sources_and_their_combined_model_scope(self):
        from tools.fact_device_review import reviewed_device_records
        c=self.source()
        c.execute("INSERT INTO manuals VALUES('feature','FEATURE','B','FX5U')")
        c.execute("INSERT INTO page_artifacts VALUES('feature',8)")
        d=self.definitions();d['definitions'][0]['sources'].insert(0,{
            'manual_id':'feature','manual_number':'FEATURE','revision':'B','pdf_pages':[8]})
        row,=reviewed_device_records(c,d)
        self.assertEqual(row['scope']['models'],['FX3U'])
        self.assertEqual([s['manual_id'] for s in row['sources']],['feature','synthetic'])
        # Neither manual covers this identity: a companion is not blanket support.
        d['definitions'][0]['models']=['FX1S']
        with self.assertRaises(ValueError):reviewed_device_records(c,d)

    def test_device_update_preserves_other_domains_keeps_gaps_and_is_idempotent(self):
        from tools.fact_device_review import reviewed_device_records
        from scripts.compile_domain_semantics import update_domain_records,_packet
        from shared.model_context import technical_fact_packet
        from knowledge.structured_facts import fact_record_coverage
        c=self.corpus();before=[tuple(r) for r in c.execute('SELECT * FROM chunks ORDER BY id')]
        records=reviewed_device_records(self.source(),self.definitions())
        original=copy.deepcopy(records)
        first=update_domain_records(c,records)
        self.assertEqual(records,original)
        self.assertEqual(first,{'changed_ids':{3},'records_added':1,'records_updated':0})
        self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM chunks WHERE id<3 ORDER BY id')],before)
        row=c.execute('SELECT * FROM chunks WHERE id=3').fetchone();packet=_packet(row['text'])
        self.assertEqual(row['fact_kind'],'device');self.assertIn('generate',row['task_types'])
        view=technical_fact_packet(packet)
        self.assertEqual(view['value']['semantic_facts'],records[0]['value']['semantic_facts'])
        self.assertEqual(view['unresolved_aspects'],['clear_rule_not_stated'])
        self.assertEqual(view['conditions'],records[0]['conditions'])
        self.assertFalse(fact_record_coverage({'text':json.dumps(packet)}))
        self.assertFalse(update_domain_records(c,records)['changed_ids'])
        self.assertEqual(c.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'unit_access_error_bitmap'").fetchone()[0],1)
        # An authoring record without a completeness review cannot erase an old gap.
        records[0].pop('gaps');records[0]['value']['semantic_facts']['width_bits']=32
        update_domain_records(c,records)
        self.assertEqual(_packet(c.execute('SELECT text FROM chunks WHERE id=3').fetchone()[0])['gaps'],['clear_rule_not_stated'])

    def test_equal_addresses_with_different_CPU_definitions_are_not_combined(self):
        from tools.fact_device_review import reviewed_device_records
        from scripts.compile_domain_semantics import update_domain_records,_packet
        d=self.definitions();other=copy.deepcopy(d['definitions'][0]);other.update(id='different_CPU',models=['FX3UC'])
        other['semantic_facts']['function']='different_function';d['definitions'].append(other)
        c=self.corpus();update_domain_records(c,reviewed_device_records(self.source(),d))
        rows=[_packet(row[0]) for row in c.execute("SELECT text FROM chunks WHERE fact_kind='device'")]
        self.assertEqual(len(rows),2)
        self.assertEqual({tuple(p['scope']['models']) for p in rows},{('FX3U',),('FX3UC',)})
        self.assertEqual({p['value']['semantic_facts']['function'] for p in rows},{'unit_access_error_bitmap','different_function'})


class PIDSourceReviewTests(unittest.TestCase):
    def source(self):
        from tools.fact_pid_review import MANUALS,PROFILES,FX3_MODELS,FX5_MODELS,QL_MODELS
        c=sqlite3.connect(':memory:');self.addCleanup(c.close)
        c.executescript('CREATE TABLE manuals(manual_id,manual_number,revision,plc_models); CREATE TABLE page_artifacts(manual_id,pdf_page,clean_text);')
        scopes={'fx3_analog_r':FX3_MODELS,'fx3_programming_r':FX3_MODELS,'fxcpu_basic_applied_m':FX3_MODELS,
            'fx5_application_ag':FX5_MODELS,'fx5_programming_ab':FX5_MODELS,'sh080809engx':QL_MODELS+['Q03UD']}
        for manual,(number,revision) in MANUALS.items():
            c.execute('INSERT INTO manuals VALUES(?,?,?,?)',(manual,number,revision,','.join(scopes[manual])))
        wanted=set()
        for profile in PROFILES.values():
            for key in ['formula','parameters','mode','sampling','alarm','tuning']:
                wanted.update((profile['manual'],page) for page in profile[key])
            for manual,pages in profile['interfaces']:wanted.update((manual,p) for p in pages)
        wanted.update(('fx5_application_ag',p) for p in [582,583,584,1035,1036])
        for manual,page in wanted:
            c.execute('INSERT INTO page_artifacts VALUES(?,?,?)',(manual,page,'PID PVnf auto 32767 100 1.040 1.030G Overshoot Hunting QnUDVCPU QnUDPVCPU LCPU'))
        return c

    def batch(self):
        from tools.fact_pid_review import reviewed_pid_behavior
        return reviewed_pid_behavior(self.source())

    def test_source_profiles_share_equations_and_retain_actual_layout_differences(self):
        from plc.instruction_definition import InstructionFactGroup
        from tools.fact_pid_review import PROFILES,pid_reference_relation
        batches=self.batch()
        for key,profile in PROFILES.items():
            for model in profile['models']:
                rows=batches[(model,'PID')]
                for fact in rows:
                    self.assertEqual(fact['scope']['forms'],['PID'])
                    self.assertEqual(fact['scope']['models'],[model]);InstructionFactGroup.from_mapping(fact)
                    self.assertFalse(fact['value'].get('behavior'))
                relation=next(f for f in rows if f['id']=='pid_review:'+key+':reference')
                self.assertEqual(relation['value'],pid_reference_relation(profile['names']))
                tuning=next(f['value'] for f in rows if f['id']=='pid_review:'+key+':tuning')
                self.assertEqual(tuning['step_response']['SV_PV_difference_magnitude_minimum'],75 if key=='fx5' else 150)
        def value(model,suffix):return next(f['value'] for f in batches[(model,'PID')] if f['id'].endswith(':'+suffix))
        self.assertEqual(value('FX5U','occupancy')['normal_PID'],25)
        self.assertEqual(value('FX5U','occupancy')['step_response']['ACT_bit8_true'],28)
        self.assertEqual(value('FX3U','occupancy')['step_response']['ACT_bits_1_2_5_all_false'],20)
        self.assertEqual(value('Q03UDV','occupancy')['limit_cycle_tuning'],29)
        self.assertEqual(value('FX3U','alarm')['variation_as_printed'],'previous_minus_current')
        self.assertEqual(value('FX5U','alarm')['variation_as_printed'],'current_minus_previous')
        self.assertNotIn('latched_parameters',value('Q03UDV','startup'))

    def test_optional_feature_version_gaps_are_owned_by_their_specific_CPU(self):
        from knowledge.structured_facts import _unresolved_programming_content
        from knowledge.fact_selection import _applicability
        batches=self.batch()
        def feature(model,suffix):return next(f for f in batches[(model,'PID')] if f['id'].endswith(':'+suffix))
        for model in ['FX5U','FX5UC']:
            minimum=feature(model,'suppression')
            self.assertEqual(minimum['value']['firmware_minimum'],'1.040')
            self.assertEqual(minimum['value']['GXWorks3_minimum'],'1.030G')
            self.assertFalse(_unresolved_programming_content(minimum))
            rule=feature(model,'suppression_rules')
            self.assertEqual(_applicability(rule,opcode='PID',model=model,language='ladder',version=None,context={}), 'unverified')
            self.assertEqual(_applicability(rule,opcode='PID',model=model,language='ladder',version=None,
                context={'cpu_firmware_version':'1.030'}), 'not_applicable')
        for model in ['FX5S','FX5UJ']:
            minimum=feature(model,'suppression')
            self.assertEqual(minimum['status'],'unknown')
            self.assertTrue(_unresolved_programming_content(minimum))
            self.assertNotIn('firmware_minimum',minimum['value'])

    def test_serial_and_model_exclusions_do_not_admit_related_PID_families(self):
        from knowledge.fact_selection import _applicability
        batches=self.batch();rows=batches[('Q03UDV','PID')]
        positive=next(f for f in rows if f['id']=='pid_review:ql:applicability')
        negative=next(f for f in rows if f['id'].endswith(':serial_excluded'))
        def state(fact,number):
            context={} if number is None else {'cpu_serial_first_five':number}
            return _applicability(fact,opcode='PID',model='Q03UDV',language='ladder',version=None,context=context)
        self.assertEqual(state(positive,None),'unverified');self.assertEqual(state(negative,None),'unverified')
        self.assertEqual(state(positive,16111),'not_applicable');self.assertEqual(state(negative,16111),'applicable')
        self.assertEqual(state(positive,16112),'applicable');self.assertEqual(state(negative,16112),'not_applicable')
        self.assertIs(batches[('Q03UD','PID')][0]['value']['available'],False)
        self.assertEqual({opcode for model,opcode in batches},{'PID'})

    def test_parameter_tables_keep_storage_access_and_unknown_history_in_the_common_projection(self):
        from plc.instruction_knowledge import parameter_usage_facts
        from shared.model_context import technical_fact_packet
        from knowledge.fact_selection import project_packet
        rows=self.batch()[('FX3U','PID')]
        original=copy.deepcopy(rows)
        assembled=rows+parameter_usage_facts(rows)
        packet={'kind':'instruction','target':'PID','scope':{'models':['FX3U']},
            'value':{'opcode':'PID','target_model':'FX3U','facts':assembled},'content_origin':'authored_fact'}
        selected=project_packet(packet,plc_model='FX3U',language='ladder',project_mode='native_ladder',aspects=['programming_semantics'])
        view=technical_fact_packet(selected)
        tables=view['value']['parameter_tables']
        self.assertTrue(any(len(t['parameters'])==4 and all(p.get('purpose') for p in t['parameters']) for t in tables))
        self.assertTrue(any(p.get('semantic_access')=='read_write' for t in tables for p in t['parameters'] if p['name']=='D'))
        unknown=[f for f in selected['value']['facts'] if f['id'].endswith(':unresolved_state')]
        self.assertEqual(len(unknown),1);self.assertEqual(unknown[0]['status'],'unknown')
        self.assertTrue(any(f['status']=='conflict' for f in selected['value']['facts']))
        self.assertEqual(rows,original)

    def test_source_edition_model_scope_and_missing_pages_are_rejected(self):
        from tools.fact_pid_review import reviewed_pid_behavior,PIDSourcePages
        c=self.source();c.execute("UPDATE manuals SET revision='WRONG' WHERE manual_id='fx3_analog_r'")
        with self.assertRaisesRegex(ValueError,'edition'):reviewed_pid_behavior(c)
        c.execute("UPDATE manuals SET revision='R',plc_models='FX3U' WHERE manual_id='fx3_analog_r'")
        with self.assertRaisesRegex(ValueError,'exceeds'):reviewed_pid_behavior(c)
        c=self.source();c.execute("DELETE FROM page_artifacts WHERE manual_id='sh080809engx' AND pdf_page=736")
        with self.assertRaisesRegex(ValueError,'page'):reviewed_pid_behavior(c)
        c=self.source();c.execute("DELETE FROM manuals WHERE manual_id='fx3_analog_r'")
        with PIDSourcePages(c) as source:
            with self.assertRaisesRegex(ValueError,'not registered'):source.pages('fx3_analog_r',[502])

    def test_reference_formulas_preserve_the_printed_fraction_order(self):
        from fractions import Fraction as F
        from tools.fact_pid_review import _tuning_reference,pid_reference_relation
        reference=_tuning_reference('S3')
        def calc(expression,values):return eval(expression,{'__builtins__':{}},values)
        values={'amplitude':F(20),'ULV':F(100),'LLV':F(0),'on_period':F(3),'period':F(10)}
        # The integral table is 20*ton*(1-ton/tau), not a reordered tau/ton expression.
        self.assertEqual(calc(reference['limit_cycle']['PID']['TI_100ms'],values),42)
        self.assertEqual(calc(reference['limit_cycle']['PID']['TD_10ms'],values),105)
        env={'PVn':F(10),'L':F(0),'PVnf_previous':F(8),'PVnf_two_cycles_before':F(6),
             'SV':F(0),'TS':F(1000),'TI':F(1000),'TD':F(100),'KD':F(0),'KP':F(2),'Dn_previous':F(0)}
        formula=pid_reference_relation(['S1','S2','S3','D']);env['PVnf']=calc(formula['filter']['PVnf'],env)
        for case,previous,expected in [(formula['action_cases'][0],8,24),(formula['action_cases'][1],-8,-24)]:
            values={**env,'EVn_previous':F(previous)};values['EVn']=calc(case['EVn'],values);values['Dn']=calc(case['Dn'],values)
            self.assertEqual(calc(formula['delta_MV'],values),expected)

    def test_action_reversal_changes_sign_in_the_mathematical_reference_domain(self):
        from fractions import Fraction as F
        from hypothesis import given,settings,strategies as st
        from tools.fact_pid_review import pid_reference_relation
        formula=pid_reference_relation(['S1','S2','S3','D'])
        @settings(max_examples=35,deadline=None)
        @given(st.lists(st.integers(-1000,1000),min_size=7,max_size=7),st.integers(1,1000),st.integers(1,1000),st.integers(0,1000),st.integers(0,100))
        def run(v,ts,ti,td,kd):
            env=dict(zip(['PVn','SV','PVnf_previous','PVnf_two_cycles_before','EVn_previous','Dn_previous','KP'],map(F,v)))
            env.update(L=F(1,4),KP=F(abs(v[-1])+1,100),TS=F(ts),TI=F(ti*100),TD=F(td*10),KD=F(kd,100))
            calc=lambda expression,values:eval(expression,{'__builtins__':{}},values)
            env['PVnf']=calc(formula['filter']['PVnf'],env)
            results=[]
            for index,case in enumerate(formula['action_cases']):
                values=env.copy()
                if index:values.update(EVn_previous=-env['EVn_previous'],Dn_previous=-env['Dn_previous'])
                values['EVn']=calc(case['EVn'],values);values['Dn']=calc(case['Dn'],values)
                results.append(calc(formula['delta_MV'],values))
            self.assertEqual(results[0],-results[1])
        run()


class LanguageSourceRefreshTests(unittest.TestCase):
    def feature(self,model='FX5U',identity='fx5lang:type',limit=20):
        f=language_feature('fbd');f['target']='FBD.TYPES'
        f.update(models=[model],aliases=['synthetic types'])
        fact=f['facts'][0];fact.update(id=identity,value={'limit':limit},conditions=[{'context':'engineering_product','equals':'GXWorks3'}])
        fact['scope'].update(forms=['FBD.TYPES'],models=[model])
        return project_language_features([f])[0][0]

    def test_mixed_language_namespaces_preserve_unsubmitted_owner_facts(self):
        from scripts.compile_instruction_semantics import merge_language_features
        first = self.feature(identity='fx5lang:first', limit=10)
        second = self.feature(identity='fx5lang:second', limit=11)
        second['target'] = 'FBD.OTHER'
        second['facts'][0]['scope']['forms'] = ['FBD.OTHER']
        addition = copy.deepcopy(second)
        addition['facts'][0].update(id='otherlang:new', value={'limit': 12})
        fresh = self.feature(identity='fx5lang:first', limit=20)
        merged, _ = merge_language_features([first, second], [fresh, addition],
            replace_source_prefixes=['fx5lang:', 'otherlang:'])
        other = next(item for item in merged if item['target'] == 'FBD.OTHER')
        self.assertIn(second['facts'][0], other['facts'])
        self.assertIn(addition['facts'][0], other['facts'])
        untouched, history = merge_language_features([second], [], replace_source_prefixes=['fx5lang:'])
        self.assertEqual(untouched, [second])
        self.assertFalse(history)

    def test_namespace_refresh_retains_legacy_scopes_aliases_and_source_history(self):
        from scripts.compile_instruction_semantics import merge_language_features
        old=self.feature('FX3U','legacy',10);old['facts'][0].pop('conditions',None)
        fresh=self.feature();before=copy.deepcopy([old,fresh])
        merged,history=merge_language_features([old],[fresh],replace_source_prefixes=['fx5lang:'])
        self.assertFalse(history);self.assertEqual([old,fresh],before)
        self.assertEqual(len(merged[0]['facts']),2)
        self.assertIn(old['facts'][0],merged[0]['facts'])
        self.assertEqual(set(merged[0]['models']),{'FX3U','FX5U'})
        corrected=copy.deepcopy(fresh);corrected['facts'][0]['value']['limit']=30
        changed,history=merge_language_features(merged,[corrected],replace_source_prefixes=['fx5lang:'])
        self.assertEqual(history['FBD.TYPES'],[merged[0]['facts'][1]])
        self.assertEqual(changed[0]['facts'][0],merged[0]['facts'][0])
        again,history=merge_language_features(changed,[corrected],replace_source_prefixes=['fx5lang:'])
        self.assertEqual(again,changed);self.assertFalse(history)
        with self.assertRaises(ValueError):merge_language_features([old],[fresh,fresh])

    def test_model_union_and_idempotence_preserve_every_old_fact(self):
        from hypothesis import given,settings,strategies as st
        from scripts.compile_instruction_semantics import merge_language_features
        @settings(max_examples=30,deadline=None)
        @given(st.sets(st.sampled_from(['FX5S','FX5UJ','FX5U','FX5UC']),min_size=1),st.integers(-65535,65535))
        def run(models,limit):
            old=self.feature('FX3U','legacy',10);old['facts'][0].pop('conditions',None)
            fresh=self.feature(limit=limit);fresh['models']=sorted(models);fresh['facts'][0]['scope']['models']=sorted(models)
            before=copy.deepcopy((old,fresh))
            current,_=merge_language_features([old],[fresh],replace_source_prefixes=['fx5lang:'])
            self.assertEqual((old,fresh),before)
            self.assertEqual(set(current[0]['models']),{'FX3U'}|models)
            self.assertIn(old['facts'][0],current[0]['facts'])
            again,history=merge_language_features(current,[fresh],replace_source_prefixes=['fx5lang:'])
            self.assertEqual(current,again);self.assertFalse(history)
        run()

    def test_product_condition_keeps_manual_knowledge_separate_from_Core_rules(self):
        from types import SimpleNamespace
        from plc.fbd_semantics import language_rules,FBDSemanticError
        from scripts.compile_instruction_semantics import merge_language_features
        from shared.model_context import technical_fact_packet
        old=self.feature('FX3U','legacy',10);old['facts'][0].pop('conditions',None)
        merged,_=merge_language_features([old],[self.feature()],replace_source_prefixes=['fx5lang:'])
        registry=SimpleNamespace(fact_bundle_current=lambda:True,language_feature=lambda *args,**kwargs:merged[0])
        self.assertEqual(language_rules('FBD.TYPES','FX3U',registry=registry),{'limit':10})
        with self.assertRaises(FBDSemanticError):language_rules('FBD.TYPES','FX5U',registry=registry)
        packet={'kind':'language','target':'FBD.TYPES','value':{'facts':[merged[0]['facts'][1]]}}
        view=technical_fact_packet(packet)
        self.assertEqual(view['value']['facts'][0]['conditions'],self.feature()['facts'][0]['conditions'])
        self.assertEqual(view['value']['facts'][0]['value'],{'limit':20})

    def test_language_completeness_requires_content_and_resolved_source_conditions(self):
        from knowledge.structured_facts import fact_record_coverage,retrieval_coverage
        f=self.feature('FX5U','language:types');fact=f['facts'][0]
        fact.pop('conditions',None)
        fact['sources']=[{'manual_id':'synthetic','revision':'A'}]
        packet={'kind':'language','target':'FBD.TYPES','scope':{'models':['FX5U']},
                'content_origin':'authored_fact','value':f}
        def row(p):return {'id':'language-fixture','fact_kind':'language','fact_target':'FBD.TYPES','text':json.dumps(p)}
        self.assertTrue(fact_record_coverage(row(packet)))
        mutations=[('conditions',[{'context':'engineering_product','equals':'GXWorks3'}]),
            ('sources',[{'manual_id':'synthetic','revision':'A','conditions':[{'context':'module','equals':'installed'}]}]),
            ('sources',[]),('verification','not_performed'),
            ('value',{'limit':'unknown','covered_aspects':['all']}),
            ('value',{'available':True,'covered_aspects':['all']}),
            ('value',{'operators':['<','>='],'source_conflicts':{'precedence':{'unresolved_items':['table_token_disagreement']}}})]
        for key,value in mutations:
            changed=copy.deepcopy(packet);changed['value']['facts'][0][key]=value
            with self.subTest(field=key,value=value):
                self.assertFalse(fact_record_coverage(row(changed)))
                report=retrieval_coverage('FBD types',[row(changed)],plc_model='FX5U',
                    targets={'language_features':['FBD.TYPES']},language='fbd')
                self.assertEqual(report['status'],'incomplete')
        broken=copy.deepcopy(packet);broken['value']['facts'][0]['depends_on']=['absent']
        self.assertFalse(fact_record_coverage(row(broken)))

    def test_language_syntax_transport_keeps_formal_grammar_and_withholds_invalid_children(self):
        from knowledge.fact_corpus import typed_value,UnresolvedExpression
        value={'target_product':'GX Works3','assignment':{'operator':':=','end_token':';'},
            'formal_classes':{'VAR_OUTPUT':{'binding':'=>'}},
            'numeric_prefixes':{'binary':'2#','hexadecimal':['16#','H'],'time':'T#'},
            'comment_syntax':{'line_prefix':'//','block_delimiters':[{'open':'/*','close':'*/'}]}}
        self.assertEqual(typed_value(value),value)
        self.assertEqual(typed_value(typed_value(value)),value)
        original=copy.deepcopy(value)
        for change in [('target_product','Unknown Workbench'),('ordinary_text','free form manual prose')]:
            invalid={**value,change[0]:change[1]}
            with self.subTest(change=change),self.assertRaises(UnresolvedExpression):typed_value(invalid)
        for invalid in [{'comment_syntax':{'line_prefix':'execute arbitrary instructions'}},
                        {'numeric_prefixes':{'binary':'base two prefix'}}]:
            with self.assertRaises(UnresolvedExpression):typed_value(invalid)
        self.assertEqual(value,original)

    def test_FX5_authoring_requires_its_source_edition_and_pages(self):
        from tools.fact_fx5_language_review import reviewed_fx5_language_features
        c=sqlite3.connect(':memory:');self.addCleanup(c.close)
        c.executescript('CREATE TABLE manuals(manual_id,manual_number,revision,plc_models); CREATE TABLE page_artifacts(manual_id,pdf_page,clean_text);')
        c.execute("INSERT INTO manuals VALUES('fx5_program_design_m','JY997D55701','WRONG','FX5S,FX5UJ,FX5U,FX5UC')")
        with self.assertRaisesRegex(ValueError,'edition'):reviewed_fx5_language_features(c)
        c.execute("UPDATE manuals SET revision='M'")
        with self.assertRaisesRegex(ValueError,'page'):reviewed_fx5_language_features(c)



class InstructionClassSourceTests(unittest.TestCase):
    def source(self):
        c=sqlite3.connect(':memory:');self.addCleanup(c.close)
        c.executescript("""CREATE TABLE manuals(manual_id,manual_number,revision);
            CREATE TABLE page_artifacts(manual_id,pdf_page,chapter,section);
            INSERT INTO manuals VALUES('fxcpu_basic_applied_m','JY997D34701','M');
            INSERT INTO manuals VALUES('fx3_programming_r','JY997D16601','R');
            INSERT INTO page_artifacts VALUES('fxcpu_basic_applied_m',100,'15. Applied Instructions (External Device)','15.9 PID / Control');""")
        return c

    def entry(self):
        proof={'id':'authored:applied:variant_family@fxcpu_basic_applied_m','dimension':'parameters.variant_family',
            'value':{'section_variants':['PID']},'scope':{'models':['FX3U'],'forms':['PID'],'languages':['st']},
            'status':'source_verified','verification':'source_checked',
            'sources':[{'manual_id':'fxcpu_basic_applied_m','manual_number':'JY997D34701','revision':'M','pdf_pages':[100]}]}
        return {'target_model':'FX3U','opcode':'PID','facts':[proof]}

    def test_classification_uses_source_chapter_and_preserves_interface_dependencies(self):
        from tools.fact_instruction_class_review import reviewed_instruction_classes,instruction_class_fact
        source=self.source();entry=self.entry()
        batches=reviewed_instruction_classes(source,[entry])
        result,=batches[('FX3U','PID')]
        self.assertEqual(result['value']['instruction_class'],'applied')
        self.assertEqual(result['depends_on'],[entry['facts'][0]['id']])
        self.assertEqual(result['scope'],entry['facts'][0]['scope'])
        self.assertEqual(result,instruction_class_fact({'manual_id':'fxcpu_basic_applied_m',
            'chapter':'15. Applied Instructions (External Device)','section':'15.9 PID / Control'},entry['facts'][0]))
        source.execute("UPDATE page_artifacts SET chapter='5. Sequence Instructions'")
        self.assertFalse(reviewed_instruction_classes(source,[entry]))

    def test_classification_requires_matching_source_edition_and_actual_pages(self):
        from tools.fact_instruction_class_review import reviewed_instruction_classes
        source=self.source();entry=self.entry();entry['facts'][0]['sources'][0]['pdf_pages']=[101]
        self.assertFalse(reviewed_instruction_classes(source,[entry]))
        source.execute("UPDATE manuals SET revision='OLD' WHERE manual_id='fxcpu_basic_applied_m'")
        with self.assertRaisesRegex(ValueError,'edition'):reviewed_instruction_classes(source,[self.entry()])

    def test_multiple_source_spans_keep_one_class_identity_and_real_disagreement(self):
        from tools.fact_instruction_class_review import reviewed_instruction_classes
        source=self.source();entry=self.entry()
        source.execute("INSERT INTO page_artifacts VALUES('fxcpu_basic_applied_m',101,'15. Applied Instructions (External Device)','15.9 PID / Control')")
        another=copy.deepcopy(entry['facts'][0]['sources'][0]);another['pdf_pages']=[101]
        entry['facts'][0]['sources'].append(another)
        result,=reviewed_instruction_classes(source,[entry])[('FX3U','PID')]
        self.assertEqual(len(result['sources']),2)
        self.assertEqual(result['status'],'source_verified')
        source.execute("UPDATE page_artifacts SET chapter='5. Basic Instructions' WHERE pdf_page=101")
        conflicted,=reviewed_instruction_classes(source,[entry])[('FX3U','PID')]
        self.assertEqual(conflicted['status'],'conflict')
        self.assertNotIn('instruction_class',conflicted['value'])

    def test_FNC_identity_uses_leaf_heading_not_ancestor_or_prose(self):
        from tools.fact_instruction_class_review import own_fnc_number,instruction_class
        for section,expected in [('16. External FX Device - FNC 80 to FNC 89 > 16.9 FNC 88 - PID / Control',88),
            ('9. Move - FNC 10 to FNC 19 > 9.6 FNC 15 - BMOV / Move',15),
            ('16. FNC 80-FNC89',None),('16.9 FNC 80-FNC89',None),('5.3 OUT (Excluding timers and counters)',None)]:
            with self.subTest(section=section):self.assertEqual(own_fnc_number(section),expected)
        self.assertIsNone(instruction_class({'manual_id':'unrelated','section':'16.9 FNC 88 - PID'}))
        self.assertEqual(instruction_class({'manual_id':'fx3_programming_r','section':'16.9 FNC 88 - PID'})[0],'applied')

if __name__=='__main__': unittest.main()
