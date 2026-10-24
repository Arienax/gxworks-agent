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
        source=fixture(); before=copy.deepcopy(source)
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

if __name__=='__main__': unittest.main()
