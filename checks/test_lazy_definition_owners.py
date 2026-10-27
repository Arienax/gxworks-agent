"""Lazy owner views preserve exact admission and detached reference knowledge."""
import copy
import json
import tempfile
import types
import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import patch

from knowledge.runtime_fact_projection import intern_runtime_sources
from plc.instructions import InstructionRegistry, InstructionSpec


def fixture():
    rows = []
    for opcode in ('KNOWN', 'REFERENCE'):
        rows.append({'vendor':'mitsubishi','target_model':'FX5U','opcode':opcode,
            'call_available': opcode == 'KNOWN', 'facts':[{
            'id':'one','dimension':'constraints.synthetic','value':{'items':[1,2]},
            'status':'source_verified','verification':'source_checked',
            'scope':{'models':['FX5U'],'forms':[opcode]},
            'sources':[{'manual_id':'test','revision':'A'}]}]})
    return {'schema_version':1,'method':'instruction-material-compiler-v1',
            'fact_bundle_version':'synthetic','entries':rows}


def registry():
    return InstructionRegistry([InstructionSpec('KNOWN', cpu_support=frozenset({'FX5U'}))])


class FakeStore:
    def __init__(self, path):
        self.rows = fixture()['entries']
        self.metadata = {'fact_bundle_version':'synthetic'}
        self.loads = 0
        self.active = True
    def iter_owner_headers(self):
        return ({k:v for k,v in row.items() if k != 'facts'} for row in self.rows)
    def load_owner(self,key):
        self.loads += 1
        return copy.deepcopy(next(r for r in self.rows if key ==
            (r['vendor'],r['opcode'],r['target_model'])))
    def get_owner_header(self,key):
        row=next((r for r in self.rows if key == (r['vendor'],r['opcode'],r['target_model'])),None)
        return {k:copy.deepcopy(v) for k,v in row.items() if k != 'facts'} if row else None
    def current(self):
        return self.active
    def close(self):
        self.active = False


class LazyDefinitionOwnersTests(unittest.TestCase):
    def test_json_pool_keeps_views_and_hydrates_contract_only_on_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'runtime.json'
            path.write_text(json.dumps(intern_runtime_sources(fixture(),intern_values=True)))
            r=registry();r.load_instruction_definitions(path)
            self.assertEqual(r.loaded_definition_owner_count,0)
            self.assertIsInstance(r.definition_inventory[0],Mapping)
            self.assertNotIsInstance(r.definition_inventory[0],dict)
            ref=r.definition_entry('REFERENCE',cpu='FX5U')
            self.assertFalse(ref['call_available'])
            self.assertIsNone(r.resolve_form('REFERENCE',cpu='FX5U'))
            self.assertIsNotNone(r.resolve_fact_form('REFERENCE',cpu='FX5U'))
            self.assertEqual(r.loaded_definition_owner_count,0)
            ref['facts'][0]['value']['items'].append(3)
            self.assertEqual(r.definition_entry('REFERENCE',cpu='FX5U')['facts'][0]['value']['items'],[1,2])
            spec=r.resolve_form('KNOWN',cpu='FX5U').spec
            self.assertEqual(r.loaded_definition_owner_count,1)
            spec.definition_facts[0].value['items'].append(7)
            self.assertEqual(r.definition_entry('KNOWN',cpu='FX5U')['facts'][0]['value']['items'],[1,2])

    def test_generation_menu_uses_only_admission_headers(self):
        from plc.instructions import generation_app_instr_mnemonics
        class MenuStore(FakeStore):
            def __init__(self,path):
                super().__init__(path)
                for name,available in [('BLOCKED',False),('UNDECIDED',None)]:
                    row=copy.deepcopy(self.rows[0]);row['opcode']=name
                    if available is None:row.pop('call_available')
                    else:row['call_available']=available
                    for fact in row['facts']:fact['scope']['forms']=[name]
                    self.rows.append(row)
        r=InstructionRegistry([InstructionSpec(name,cpu_support=frozenset({'FX5U'}))
                               for name in ('KNOWN','BLOCKED','UNDECIDED')])
        with patch.dict('sys.modules',{'plc.instruction_owner_store':types.SimpleNamespace(OwnerDefinitionStore=MenuStore)}):
            r.load_instruction_owner_store(Path('menu.manifest.json'))
        with patch('plc.instructions.DEFAULT_INSTRUCTION_REGISTRY',r):
            self.assertEqual(generation_app_instr_mnemonics('FX5U'),('KNOWN','UNDECIDED'))
        self.assertEqual(r._definition_owner_store.loads,0)
        self.assertEqual(r.loaded_definition_owner_count,0)
        self.assertFalse(r.definition_header('BLOCKED',cpu='FX5U')['call_available'])
        self.assertNotIn('call_available',r.definition_header('UNDECIDED',cpu='FX5U'))

    def test_language_metadata_preserved_and_literal_cpu_filtering_in_both_loaders(self):
        from knowledge.runtime_fact_publication import write_runtime_sqlite, write_owner_manifest
        raw=fixture();fact=copy.deepcopy(raw['entries'][0]['facts'][0])
        fact['scope']={'forms':['ST.CALL'],'languages':['st'],'models':['FX3U','FX3UC']}
        feature={'target':'ST.CALL','models':['FX3U','FX3UC'],'aliases':['call syntax'],
                 'facts':[fact],'references':[{'models':['FX3U'],'opcode':'KNOWN',
                 'parameters':{'In':'LEFT'},'sources':[{'manual_id':'synthetic'}]}]}
        raw['language_facts']=[feature]
        related=copy.deepcopy(raw['entries'][0]);related['target_model']='FX3U';related['call_available']=False
        related['facts'][0]['scope']['models']=['FX3U'];raw['entries'].append(related)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/'definitions.json';path.write_text(json.dumps(raw))
            pooled=intern_runtime_sources(raw,intern_values=True)
            database=root/'plc_facts.sqlite';manifest=root/'instruction_definitions.manifest.json'
            write_runtime_sqlite(pooled,database);write_owner_manifest(pooled,database,manifest)
            for mode in ('json','manifest'):
                r=registry()
                if mode=='json':r.load_instruction_definitions(path)
                else:r.load_instruction_owner_store(manifest)
                self.assertEqual(r.language_facts,(feature,))
                self.assertNotIn('language',r.language_feature('ST.CALL',language='st'))
                self.assertIsNone(r.language_feature('ST.CALL',language='fbd'))
                self.assertIsNone(r.language_feature('ST.CALL',cpu='Q00'))
                self.assertEqual(len(r.language_feature('ST.CALL',cpu='FX3U')['references']),1)
                self.assertEqual(r.language_feature('ST.CALL',cpu='FX3UC')['references'],[])
                result=r.language_feature('ST.CALL');result['aliases'].append('mutated')
                self.assertEqual(r.language_feature('ST.CALL'),feature)
                if r._definition_owner_store:r._definition_owner_store.close()

    def test_real_manifest_reads_only_requested_owner(self):
        from knowledge.runtime_fact_publication import write_runtime_sqlite, write_owner_manifest
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);database=root/'plc_facts.sqlite';manifest=root/'instruction_definitions.manifest.json'
            pooled=intern_runtime_sources(fixture(),intern_values=True)
            write_runtime_sqlite(pooled,database);write_owner_manifest(pooled,database,manifest)
            r=registry();r.load_instruction_owner_store(manifest)
            store=r._definition_owner_store
            self.assertEqual(store.stats()['hydrated_owner_count'],0)
            self.assertEqual(store.stats()['value_load_count'],0)
            reference=r.definition_entry('REFERENCE',cpu='FX5U')
            self.assertFalse(reference['call_available'])
            self.assertIsNone(r.resolve_form('REFERENCE',cpu='FX5U'))
            self.assertEqual(store.stats()['hydrated_owner_count'],1)
            self.assertEqual(r.loaded_definition_owner_count,0)
            r.resolve_form('KNOWN',cpu='FX5U')
            self.assertEqual(r.loaded_definition_owner_count,1)
            self.assertEqual(store.stats()['hydrated_owner_count'],2)
            manifest.write_text(manifest.read_text()+' ')
            self.assertFalse(r.fact_bundle_current())
            with self.assertRaises(ValueError):r.resolve_form('KNOWN',cpu='FX5U')
            with self.assertRaises(ValueError):r.definition_entry('REFERENCE',cpu='FX5U')
            store.close()

    def test_manifest_does_not_query_owner_rows_until_access(self):
        module=types.SimpleNamespace(OwnerDefinitionStore=FakeStore)
        with patch.dict('sys.modules',{'plc.instruction_owner_store':module}):
            r=registry();r.load_instruction_owner_store(Path('synthetic.manifest.json'))
        store=r._definition_owner_store
        self.assertEqual(store.loads,0)
        self.assertIsNotNone(r.resolve_fact_form('REFERENCE',cpu='FX5U'))
        self.assertEqual(store.loads,0)
        r.definition_entry('REFERENCE',cpu='FX5U')
        self.assertEqual(store.loads,1)
        self.assertEqual(r.loaded_definition_owner_count,0)
        r.resolve_form('KNOWN',cpu='FX5U')
        self.assertEqual(store.loads,2)
        r.resolve_form('KNOWN',cpu='FX5U')
        self.assertEqual(store.loads,2)
        store.active=False
        self.assertFalse(r.fact_bundle_current())
        with self.assertRaises(ValueError):r.resolve_form('KNOWN',cpu='FX5U')

    def test_reload_withdraws_previously_attached_definition_facts(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'runtime.json';path.write_text(json.dumps(fixture()))
            r=registry();r.load_instruction_definitions(path)
            self.assertEqual(len(r.resolve_form('KNOWN',cpu='FX5U').spec.definition_facts),1)
            empty=dict(fixture(),entries=[]);path.write_text(json.dumps(empty))
            r.load_instruction_definitions(path)
            self.assertEqual(r.loaded_definition_owner_count,0)
            self.assertEqual(r.resolve_form('KNOWN',cpu='FX5U').spec.definition_facts,())

    def test_failed_replacement_preserves_previous_bundle_and_contract(self):
        module=types.SimpleNamespace(OwnerDefinitionStore=FakeStore)
        with patch.dict('sys.modules',{'plc.instruction_owner_store':module}):
            r=registry();r.load_instruction_owner_store(Path('old.manifest.json'))
        old=r._definition_owner_store
        previous=r.resolve_form('KNOWN',cpu='FX5U').spec
        class InvalidDocumentsStore(FakeStore):
            def __init__(self,path):
                super().__init__(path)
                self.metadata['documents']=[{}]
        module.OwnerDefinitionStore=InvalidDocumentsStore
        with patch.dict('sys.modules',{'plc.instruction_owner_store':module}):
            with self.assertRaises(ValueError):r.load_instruction_owner_store(Path('bad.manifest.json'))
        self.assertIs(r._definition_owner_store,old)
        self.assertTrue(old.current())
        self.assertIs(r.resolve_form('KNOWN',cpu='FX5U').spec,previous)
        self.assertEqual(r.loaded_definition_owner_count,1)
        with tempfile.TemporaryDirectory() as temp:
            bad=dict(fixture(),documents=[{}])
            path=Path(temp)/'bad.json';path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):r.load_instruction_definitions(path)
        self.assertIs(r._definition_owner_store,old)
        self.assertIs(r.resolve_form('KNOWN',cpu='FX5U').spec,previous)

    def test_explicit_manifest_override_retains_seed_catalog(self):
        from plc.instructions import load_default_instruction_registry
        module=types.SimpleNamespace(OwnerDefinitionStore=FakeStore)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ('common.json','fx3u.json','fx5u.json'):
                instructions=([{'mnemonic':'KNOWN','cpu_support':['FX5U'],
                               'category':'action','semantic_kind':'vendor','operands':[]}]
                              if name=='common.json' else [])
                (root/name).write_text(json.dumps({'schema_version':1,'instructions':instructions}))
            selected=root/'explicit'/'candidate.manifest.json'
            with patch('plc.instructions._candidate_catalog_directories',return_value=(root,)), \
                 patch.dict('os.environ',{'GXW2_INSTRUCTION_OWNER_MANIFEST':str(selected)}), \
                 patch.dict('sys.modules',{'plc.instruction_owner_store':module}):
                r=load_default_instruction_registry()
            self.assertIn('KNOWN',r.known_mnemonics())
            self.assertEqual(r.resolve('KNOWN',cpu='FX5U').cpu_support,frozenset({'FX5U'}))
            self.assertFalse(r.resolve('KNOWN',cpu='Q00').supports_cpu('Q00'))
            self.assertEqual(len(r.definition_inventory),2)

    def test_explicit_missing_bad_or_empty_manifest_never_falls_back(self):
        from plc.instructions import load_default_instruction_registry
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ('common.json','fx3u.json','fx5u.json'):
                (root/name).write_text(json.dumps({'schema_version':1,'instructions':[]}))
            corrupt=root/'bad.json';corrupt.write_text('not json')
            for selected in (str(root/'missing.json'),str(corrupt),''):
                with self.subTest(path=selected), \
                     patch('plc.instructions._candidate_catalog_directories',return_value=(root,)), \
                     patch.dict('os.environ',{'GXW2_INSTRUCTION_OWNER_MANIFEST':selected}):
                    with self.assertRaises((ValueError,OSError)):load_default_instruction_registry()

    def test_no_override_retains_legacy_loader(self):
        from plc.instructions import load_default_instruction_registry
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ('common.json','fx3u.json','fx5u.json'):
                (root/name).write_text(json.dumps({'schema_version':1,'instructions':[]}))
            (root/'instruction_definitions.json').write_text(json.dumps(fixture()))
            with patch('plc.instructions._candidate_catalog_directories',return_value=(root,)), \
                 patch('shared.paths.resource_path',return_value=root/'missing.manifest.json'), \
                 patch.dict('os.environ',{},clear=True):
                r=load_default_instruction_registry()
            self.assertIsNone(r._definition_owner_store)
            self.assertEqual(len(r.definition_inventory),2)

    def test_bad_manifest_presence_never_falls_back(self):
        from plc.instructions import load_default_instruction_registry
        for kind in ('directory', 'dangling_symlink'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                directory=Path(temp)
                for name in ('common.json','fx3u.json','fx5u.json'):
                    (directory/name).write_text(json.dumps({'schema_version':1,'instructions':[]}))
                manifest=directory/'instruction_definitions.manifest.json'
                if kind=='directory':manifest.mkdir()
                else:manifest.symlink_to(directory/'does_not_exist.json')
                with patch('plc.instructions._candidate_catalog_directories',return_value=(directory,)):
                    with self.assertRaises((OSError,ValueError)):
                        load_default_instruction_registry()

    def test_legacy_invalid_scope_fails_before_install(self):
        broken=fixture();broken['entries'][1]['facts'][0]['scope']['models']=['Q00']
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'runtime.json';path.write_text(json.dumps(intern_runtime_sources(broken)))
            r=registry()
            with self.assertRaises(ValueError):r.load_instruction_definitions(path)
            self.assertEqual(r.definition_inventory,())


if __name__=='__main__':unittest.main()


def test_legacy_bibliography_is_not_misread_as_a_runtime_source_pool(tmp_path):
    raw=fixture();raw['source_catalog']=[{'manual_id':'synthetic'}]
    path=tmp_path/'legacy.json';path.write_text(json.dumps(raw),encoding='utf-8')
    registry=InstructionRegistry();registry.load_instruction_definitions(path)
    assert registry.definition_entry('KNOWN',cpu='FX5U')['facts']==raw['entries'][0]['facts']
    raw['source_catalog']={'undeclared-pool':{'manual_id':'synthetic'}}
    path.write_text(json.dumps(raw),encoding='utf-8')
    import pytest
    with pytest.raises(ValueError,match='serialization'):
        registry.load_instruction_definitions(path)
