"""Actual shared manual-tool and MCP entry points select a manifest without path patches."""
from contextlib import closing
import json
import importlib.util
import importlib.metadata
import os
import shutil
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from knowledge.runtime_fact_projection import intern_runtime_sources
from knowledge.runtime_fact_publication import write_runtime_sqlite, write_owner_manifest

ROOT=Path(__file__).resolve().parents[1]

# Only the test subprocess transport is blocked; production components remain real.
NO_NETWORK=r'''
import os,socket
os.environ['HAYSTACK_TELEMETRY_ENABLED']='false'
def forbidden_network(*args,**kwargs):
    raise AssertionError('External network forbidden in production wiring tests')
socket.create_connection=forbidden_network
socket.socket.connect=forbidden_network
socket.socket.connect_ex=forbidden_network
socket.getaddrinfo=forbidden_network
'''


def build(root,marker,opcodes=('MOV',),search_pooling=True):
    root.mkdir(parents=True,exist_ok=True)
    def fact(target):
        return {'id':'sample:'+target,'dimension':'effects.synthetic','value':{'marker':marker},
          'scope':{'models':['FX3U'],'forms':[target]},'status':'source_verified','verification':'source_checked',
          'sources':[{'manual_id':'synthetic','revision':'A'}]}
    language=fact('ST.OPERATOR.POWER');language['scope']['languages']=['st']
    raw={'schema_version':1,'method':'instruction-material-compiler-v1','corpus_kind':'authored_plc_facts_v1',
         'fact_bundle_version':'synthetic:wiring',
         'entries':[{'vendor':'mitsubishi','opcode':op,'target_model':'FX3U','facts':[fact(op)]}for op in opcodes],
         'language_facts':[{'target':'ST.OPERATOR.POWER','language':'st','models':['FX3U'],'aliases':['**'],'facts':[language]}]}
    pooled=intern_runtime_sources(raw,intern_values=True)
    db=root/'plc_facts.sqlite';manifest=root/'instruction_definitions.manifest.json'
    write_runtime_sqlite(pooled,db,field_pooling=True,search_pooling=search_pooling)
    write_owner_manifest(pooled,db,manifest)
    return db,manifest


CHILD=r'''
import json,sys,sqlite3
from pathlib import Path
sys._MEIPASS=sys.argv[1] # Real frozen-resource resolution for the separate installed mixed corpus.
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
from agent_runtime.manual_tools import _search_plc_manual
from agent_runtime.tool_context import ToolContext
from agent_runtime.runtime import build_default_tool_runtime
from agent_runtime.messages import ToolCall
from knowledge.fact_corpus import resolve,runtime_index_path
from knowledge import index_access
ctx=ToolContext({'plc_model':'FX3U'})
args={'query':'MOV','plc_model':'FX3U','language':'st','aspects':['effects']}
manual=_search_plc_manual(ctx,args)
runtime=build_default_tool_runtime()
web=runtime.invoke(ToolCall('web','search_plc_manual',args),ctx)
from integrations.mcp.tool_adapter import MCPToolAdapter
class Provider:
    def get_context(self):return ctx
mcp=MCPToolAdapter(runtime,Provider()).call_tool('search_plc_manual',args,'mcp') if len(sys.argv)>2 else None
legacy=Path(sys.argv[1])/'knowledge/runtime/plc_facts.sqlite'
conn=sqlite3.connect('file:'+legacy.as_posix()+'?mode=ro',uri=True);conn.row_factory=sqlite3.Row
explicit=resolve('instruction',['MOV'],plc_model='FX3U',connection=conn)
conn.close()
missing=_search_plc_manual(ctx,{'query':'ADD','language':'st'})
language=_search_plc_manual(ctx,{'query':'**','language':'st'})
broad=_search_plc_manual(ctx,{'query':'PLC CANDIDATE_MARKER','language':'st'})
old_broad=_search_plc_manual(ctx,{'query':'PLC LEGACY_MARKER','language':'st'})
wrong=_search_plc_manual(ToolContext({'plc_model':'Q03UDV'}),args)
print(json.dumps({'manual':manual,'web':web.data,'mcp':mcp.model_dump(mode='json') if mcp is not None else None,
 'broad':broad,'old_broad':old_broad,'explicit':explicit,'missing':missing,'language':language,'wrong':wrong,
 'selected':str(runtime_index_path('instruction')),'device':str(runtime_index_path('device')),
 'error':str(runtime_index_path('error')),'version':registry.fact_bundle_version}))
'''


class ProductionWiringTests(unittest.TestCase):
    def run_pair(self,selected=True,child=CHILD,mcp=False,collision=False):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=Path(temp.name)
        legacy,_=build(root/'bundle/knowledge/runtime','LEGACY_MARKER',('MOV','ADD'),search_pooling=False)
        candidate,manifest=build(root/'candidate','CANDIDATE_MARKER')
        if collision:
            with closing(sqlite3.connect(candidate)) as conn, conn:
                identity=conn.execute("SELECT chunk_id FROM facts WHERE kind='instruction' AND target='MOV'").fetchone()[0]
            with closing(sqlite3.connect(legacy)) as conn, conn:
                conn.execute('DELETE FROM facts WHERE chunk_id=?',(identity,));conn.execute('DELETE FROM chunks WHERE id=?',(identity,))
                packet={'kind':'device','target':'D','scope':{'models':['FX3U']},'value':{'marker':'PLC CANDIDATE_MARKER DEVICE_MARKER'}}
                meta={'content_origin':'authored_fact','distribution_status':'fact_representation_reviewed','fact_kind':'device','fact_target':'D','source_lane':'fact'}
                conn.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)',(identity,json.dumps(packet),json.dumps(meta),'["FX3U"]','','device','authored_fact','fact_representation_reviewed'))
                conn.execute('INSERT INTO facts VALUES (?,?,?,?,?,?)',('synthetic:device','device','D','FX3U',identity,'{}'))
                conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
        # Isolate the catalogue from any locally activated production owner
        # manifest. The test selects its own candidate or legacy resource.
        catalog=root/'catalog';catalog.mkdir()
        for name in ('common.json','fx3u.json','fx5u.json','fx3u_verified_opcodes.json',
                     'modifier_rules.json','instruction_families.json','instruction_capabilities.json'):
            source=ROOT/'resources/instructions/mitsubishi'/name
            if source.is_file():shutil.copyfile(source,catalog/name)
        env=dict(os.environ);env['PYTHONPATH']=os.pathsep.join([str(ROOT/'src'),env.get('PYTHONPATH','')]);env['GXW2_INSTRUCTION_CATALOG']=str(catalog)
        env['HAYSTACK_TELEMETRY_ENABLED']='false'
        env.pop('GXW2_INSTRUCTION_OWNER_MANIFEST',None)
        if selected:env['GXW2_INSTRUCTION_OWNER_MANIFEST']=str(manifest)
        result=subprocess.run([sys.executable,'-c',NO_NETWORK+'\n'+child,str(root/'bundle')]+(['mcp']if mcp else []),cwd=ROOT,env=env,capture_output=True,text=True,timeout=45)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout),candidate,legacy

    @unittest.skipUnless(importlib.util.find_spec('haystack') and importlib.metadata.version('mcp')=='2.1.1', 'Actual adapters require repository dependencies')
    def test_cross_domain_same_chunk_id_survives_shared_handler_and_mcp(self):
        child=CHILD.replace("'query':'MOV'", "'query':'PLC CANDIDATE_MARKER'")
        result,_,_=self.run_pair(child=child,mcp=True,collision=True)
        for key in ('manual','web','mcp'):
            text=json.dumps(result[key])
            self.assertIn('DEVICE_MARKER',text,key)
            self.assertIn('instruction',text,key)
            self.assertIn('device',text,key)
        self.assertGreaterEqual(result['manual']['count'],2)

    def test_startup_env_exact_real_entry_without_any_path_patch(self):
        child=r"""
import json,sys,sqlite3
from contextlib import closing
from pathlib import Path
sys._MEIPASS=sys.argv[1]
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
from knowledge.fact_corpus import resolve,runtime_index_path
from unittest.mock import patch
with patch('plc.instruction_owner_store._digest',side_effect=AssertionError('no per-query hash')):
    rows=resolve('instruction',['MOV'],plc_model='FX3U')
    language=resolve('language',['ST.OPERATOR.POWER'],plc_model='FX3U')
    missing=resolve('instruction',['ADD'],plc_model='FX3U')
    wrong=resolve('instruction',['MOV'],plc_model='Q03UDV')
legacy=Path(sys.argv[1])/'knowledge/runtime/plc_facts.sqlite'
with closing(sqlite3.connect('file:'+legacy.as_posix()+'?mode=ro',uri=True)) as conn, conn:
    conn.row_factory=sqlite3.Row
    explicit=resolve('instruction',['MOV'],plc_model='FX3U',connection=conn)
from knowledge.core import retrieve_broad_knowledge
broad=retrieve_broad_knowledge('PLC CANDIDATE_MARKER',plc_model='FX3U')
old_broad=retrieve_broad_knowledge('PLC LEGACY_MARKER',plc_model='FX3U')
selected=runtime_index_path('instruction')
registry._definition_owner_store.manifest_path.write_text('{}')
try:resolve('instruction',['MOV'],plc_model='FX3U');rejected=False
except ValueError:rejected=True
print(json.dumps({'rows':rows,'language':language,'missing':missing,'wrong':wrong,'explicit':explicit,
 'broad':broad,'old_broad':old_broad,'rejected':rejected,'selected':str(selected),'device':str(runtime_index_path('device')),'error':str(runtime_index_path('error'))}))
"""
        result,candidate,legacy=self.run_pair(child=child)
        for key in ('rows','language','broad'):
            self.assertIn('CANDIDATE_MARKER',json.dumps(result[key]));self.assertNotIn('LEGACY_MARKER',json.dumps(result[key]))
        self.assertEqual(result['missing'],[]);self.assertEqual(result['wrong'],[])
        self.assertIn('LEGACY_MARKER',json.dumps(result['explicit']));self.assertTrue(result['rejected'])
        self.assertNotIn('LEGACY_MARKER',json.dumps(result.get('old_broad')))
        self.assertEqual(result['selected'],str(candidate))
        self.assertEqual(result['device'],str(legacy));self.assertEqual(result['error'],str(legacy))

    @unittest.skipUnless(importlib.util.find_spec('haystack') and importlib.metadata.version('mcp')=='2.1.1', 'Actual MCP handler requires repository-pinned mcp==2.1.1 and haystack-ai')
    def test_actual_mcp_adapter_uses_candidate(self):
        result,_,_=self.run_pair(mcp=True)
        self.assertIn('CANDIDATE_MARKER',json.dumps(result['mcp']))
        self.assertNotIn('LEGACY_MARKER',json.dumps(result['mcp']))

    @unittest.skipUnless(importlib.util.find_spec('haystack'), 'Production handler requires declared haystack-ai; not a passed handler verification')
    def test_manifest_reaches_actual_manual_and_web_runtime(self):
        result,candidate,legacy=self.run_pair()
        for key in ('manual','web','language','broad'):
            text=json.dumps(result[key]);self.assertIn('CANDIDATE_MARKER',text,key);self.assertNotIn('LEGACY_MARKER',text,key)
        self.assertNotIn('LEGACY_MARKER',json.dumps(result['old_broad']['results']))
        self.assertEqual(result['selected'],str(candidate))
        self.assertEqual(result['device'],str(legacy));self.assertEqual(result['error'],str(legacy))
        self.assertIn('LEGACY_MARKER',json.dumps(result['explicit']))
        self.assertNotIn('LEGACY_MARKER',json.dumps(result['missing']))
        self.assertNotIn('CANDIDATE_MARKER',json.dumps(result['wrong']))
        self.assertEqual(result['version'],'synthetic:wiring')

    @unittest.skipUnless(importlib.util.find_spec('haystack'), 'Production handler requires declared haystack-ai; not a passed handler verification')
    def test_without_manifest_installed_legacy_manual_remains_readable(self):
        result,_,legacy=self.run_pair(False)
        self.assertIn('LEGACY_MARKER',json.dumps(result['manual']))
        self.assertNotIn('CANDIDATE_MARKER',json.dumps(result['manual']))
        self.assertEqual(result['selected'],str(legacy))


class SeededOwnerStoreTests(unittest.TestCase):
    @staticmethod
    def entry(opcode, marker, extra=False):
        def fact(identity):
            return {'id':identity,'dimension':'effects.synthetic','value':{'marker':marker},
                'scope':{'models':['FX3U'],'forms':[opcode]},'status':'source_verified',
                'verification':'source_checked','sources':[{'manual_id':'synthetic','revision':'A'}]}
        return {'vendor':'mitsubishi','opcode':opcode,'target_model':'FX3U',
                'facts':[fact('shared:fact')]+([fact('extra:fact')] if extra else [])}

    @staticmethod
    def search(connection, token):
        return [row[0] for row in connection.execute(
            "SELECT c.instruction_opcode FROM chunks_fts f JOIN chunks c ON c.rowid=f.rowid "
            "WHERE chunks_fts MATCH ? AND c.chunk_type='instruction' ORDER BY c.instruction_opcode",(token,))]

    def test_seeded_replacement_preserves_shared_pools_unselected_rows_and_legacy_transport(self):
        from knowledge.runtime_fact_publication import OwnerStoreWriter, owner_key
        from plc.instruction_owner_store import OwnerDefinitionStore
        for fields, rows, search in ((False,False,False),(True,False,True),(True,True,True)):
            with self.subTest(fields=fields,rows=rows,search=search),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);source=root/'source.sqlite';output=root/'output.sqlite'
                metadata={'schema_version':1,'method':'instruction-material-compiler-v1',
                    'corpus_kind':'authored_plc_facts_v1','fact_bundle_version':'synthetic:before','context_marker':'retained'}
                entries=[self.entry('SYN_A','OLDTOKEN'),self.entry('SYN_B','OLDTOKEN')]
                language={'target':'FBD.SYNTH','language':'fbd','models':['FX3U'],'aliases':['SYNTHBLOCK'],
                    'facts':[{'id':'language:fact','dimension':'effects.synthetic','value':{'marker':'LANGTOKEN'},
                        'scope':{'models':['FX3U'],'forms':['FBD.SYNTH'],'languages':['fbd']},
                        'status':'source_verified','verification':'source_checked',
                        'sources':[{'manual_id':'synthetic','revision':'A'}]}]}
                writer=OwnerStoreWriter(source,metadata,field_pooling=fields,fact_rows=rows,search_pooling=search)
                for entry in entries:writer.add_entry(entry)
                writer.add_language_feature(language)
                writer.finalize(root/'source.manifest.json')
                untouched=owner_key(entries[1])
                with closing(sqlite3.connect(source)) as connection:
                    # Exercise an existing seed created before chunk deletion
                    # indexes were introduced, as well as fresh-store schemas.
                    connection.execute('DROP INDEX facts_chunk_id')
                    connection.execute('DROP INDEX entity_index_chunk_id')
                    connection.commit()
                    before=connection.execute('SELECT payload_json FROM runtime_definitions WHERE owner=?',(untouched,)).fetchone()
                    index=connection.execute('SELECT * FROM runtime_owner_index WHERE owner=?',(untouched,)).fetchone()
                    chunk=connection.execute("SELECT rowid,* FROM chunks WHERE instruction_opcode='SYN_B'").fetchone()
                    language_rows=connection.execute("SELECT rowid,* FROM chunks WHERE chunk_type='language'").fetchall()
                    pools={table:connection.execute('SELECT * FROM '+table).fetchall() for table in
                           ('runtime_sources','runtime_values','runtime_fact_fields')}
                writer=OwnerStoreWriter(output,{**metadata,'fact_bundle_version':'synthetic:after'},seed_database=source)
                writer.replace_entry(self.entry('SYN_A','NEWTOKEN',extra=True))
                writer.replace_entry(self.entry('SYN_C','ADDEDTOKEN'))
                result=writer.finalize(root/'output.manifest.json')
                self.assertEqual((result['sqlite']['owners'],result['sqlite']['stored_facts']),(3,4))
                with closing(sqlite3.connect(output)) as connection:
                    for table in ('facts', 'entity_index'):
                        plan = connection.execute('EXPLAIN QUERY PLAN DELETE FROM ' + table + ' WHERE chunk_id=?',
                                                  ('unused',)).fetchall()
                        self.assertTrue(any('SEARCH ' + table in row[3] and 'chunk_id' in row[3]
                                            for row in plan), plan)
                    self.assertEqual(connection.execute('SELECT payload_json FROM runtime_definitions WHERE owner=?',(untouched,)).fetchone(),before)
                    self.assertEqual(connection.execute('SELECT * FROM runtime_owner_index WHERE owner=?',(untouched,)).fetchone(),index)
                    self.assertEqual(connection.execute("SELECT rowid,* FROM chunks WHERE instruction_opcode='SYN_B'").fetchone(),chunk)
                    self.assertEqual(connection.execute("SELECT rowid,* FROM chunks WHERE chunk_type='language'").fetchall(),language_rows)
                    for table, original in pools.items():
                        self.assertTrue(set(original)<=set(connection.execute('SELECT * FROM '+table)))
                    self.assertEqual(self.search(connection,'OLDTOKEN'),['SYN_B'])
                    self.assertEqual(self.search(connection,'NEWTOKEN'),['SYN_A'])
                    self.assertEqual(self.search(connection,'ADDEDTOKEN'),['SYN_C'])
                    self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='stored_facts'").fetchone(),('4',))
                    self.assertEqual(json.loads(connection.execute("SELECT value FROM meta WHERE key='definition_metadata'").fetchone()[0])['context_marker'],'retained')
                with closing(OwnerDefinitionStore(root/'output.manifest.json')) as store:
                    self.assertEqual([f['value']['marker'] for f in store.get_entry('SYN_A',cpu='FX3U')['facts']],['NEWTOKEN','NEWTOKEN'])
                    self.assertEqual(store.get_entry('SYN_B',cpu='FX3U')['facts'][0]['value'],{'marker':'OLDTOKEN'})
                    self.assertEqual(store.get_entry('SYN_C',cpu='FX3U')['facts'][0]['value'],{'marker':'ADDEDTOKEN'})
                with closing(sqlite3.connect(source)) as connection:
                    self.assertEqual(self.search(connection,'OLDTOKEN'),['SYN_A','SYN_B'])
                    self.assertEqual(self.search(connection,'NEWTOKEN'),[])

    def test_instruction_only_compile_preserves_unselected_language_and_its_validation(self):
        import hashlib
        from scripts.compile_instruction_semantics import compile_semantics
        from knowledge.runtime_fact_publication import write_owner_manifest
        from plc.instruction_owner_store import OwnerDefinitionStore
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, manifest = build(root / 'input', 'OLDTOKEN', ('SYN_A', 'SYN_B'))
            with closing(sqlite3.connect(source)) as connection:
                feature = json.loads(connection.execute('SELECT payload_json FROM runtime_language_features').fetchone()[0])
                feature['facts'][0]['value'] = {'condition': 'unclassified language condition'}
                encoded = json.dumps(feature, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
                connection.execute('UPDATE runtime_language_features SET payload_json=?,sha256=?',
                                   (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
                connection.commit()
                before = connection.execute('SELECT * FROM runtime_language_features').fetchall()
            manifest.unlink()
            write_owner_manifest({'method': 'instruction-material-compiler-v1', 'fact_bundle_version': 'synthetic:wiring'},
                                 source, manifest, owner_count=2)
            replacement = self.entry('SYN_A', 'NEWTOKEN')['facts'][0]
            replacement['id'] = 'sample:SYN_A'
            result = compile_semantics(manifest, root / 'output',
                source_batches={('FX3U', 'SYN_A'): [replacement]},
                replace_source_prefixes=['sample:'], source_owners_only=True, version='synthetic:instructions-only')
            with closing(sqlite3.connect(root / 'output/plc_facts.sqlite')) as connection:
                self.assertEqual(connection.execute('SELECT * FROM runtime_language_features').fetchall(), before)
                self.assertEqual(self.search(connection, 'NEWTOKEN'), ['SYN_A'])
                self.assertEqual(self.search(connection, 'OLDTOKEN'), ['SYN_B'])
            with closing(OwnerDefinitionStore(result['manifest'])) as store:
                with self.assertRaises(ValueError):
                    store.language_feature(feature['target'])
                with self.assertRaises(ValueError):
                    store.metadata

    def test_mixed_source_namespaces_do_not_clear_other_selected_owners(self):
        from scripts.compile_instruction_semantics import compile_semantics
        from plc.instruction_owner_store import OwnerDefinitionStore
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, manifest = build(root / 'input', 'OLDTOKEN', ('SYN_A', 'SYN_B'))
            alpha = self.entry('SYN_A', 'NEW_A')['facts'][0]
            alpha['id'] = 'sample:SYN_A'
            beta = self.entry('SYN_B', 'NEW_B')['facts'][0]
            beta['id'] = 'beta:new'
            result = compile_semantics(manifest, root / 'output', source_batches={
                ('FX3U', 'SYN_A'): [alpha], ('FX3U', 'SYN_B'): [beta]},
                replace_source_prefixes=['sample:', 'beta:'], source_owners_only=True,
                version='synthetic:mixed-namespaces')
            with closing(OwnerDefinitionStore(result['manifest'])) as store:
                first = {f['id']: f for f in store.get_entry('SYN_A', cpu='FX3U')['facts']}
                second = {f['id']: f for f in store.get_entry('SYN_B', cpu='FX3U')['facts']}
                self.assertEqual(first['sample:SYN_A']['value'], {'marker': 'NEW_A'})
                self.assertEqual(second['sample:SYN_B']['value'], {'marker': 'OLDTOKEN'})
                self.assertEqual(second['beta:new']['value'], {'marker': 'NEW_B'})

    def test_empty_source_replacement_clears_only_declared_owner_and_retains_history(self):
        from scripts.compile_instruction_semantics import compile_semantics
        from knowledge.runtime_fact_publication import OwnerStoreWriter
        from plc.instruction_owner_store import OwnerDefinitionStore
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'input.sqlite'
            manifest = root / 'input.manifest.json'
            prefix = 'source:cpu_availability:'

            def entry(model):
                old = {'id': prefix + 'legacy:6359', 'dimension': 'constraints.cpu_availability',
                    'value': {'rule': 'CPU_INSTRUCTION_SUPPORT', 'available': True,
                              'model_states': {model: 'supported'}},
                    'scope': {'models': [model], 'forms': ['A']}, 'status': 'source_verified',
                    'verification': 'source_checked',
                    'sources': [{'manual_id': 'synthetic', 'revision': 'A', 'pdf_pages': [94]}]}
                return {'vendor': 'mitsubishi', 'opcode': 'A', 'target_model': model,
                        'call_available': False, 'support_status': 'definition_only', 'facts': [old]}

            metadata = {'schema_version': 1, 'method': 'instruction-material-compiler-v1',
                       'corpus_kind': 'authored_plc_facts_v1', 'fact_bundle_version': 'synthetic:before'}
            writer = OwnerStoreWriter(source, metadata)
            writer.add_entry(entry('FX1N'))
            writer.add_entry(entry('FX1S'))
            writer.finalize(manifest)

            owner = ('FX1N', 'A')
            result = compile_semantics(manifest, root / 'output', source_batches={owner: []},
                empty_source_replacements={(owner[0], owner[1], prefix)}, source_owners_only=True,
                version='synthetic:empty-source-replacement')
            with closing(OwnerDefinitionStore(result['manifest'])) as store:
                replaced = store.get_entry('A', cpu='FX1N')
                untouched = store.get_entry('A', cpu='FX1S')
                self.assertEqual(replaced['facts'], [])
                self.assertEqual([fact['id'] for fact in replaced['superseded_facts']],
                                 [prefix + 'legacy:6359'])
                self.assertIs(replaced['call_available'], False)
                self.assertEqual([fact['id'] for fact in untouched['facts']],
                                 [prefix + 'legacy:6359'])

    def test_selected_language_rebuild_restores_a_legacy_partial_model_index(self):
        import copy,hashlib
        from scripts.compile_instruction_semantics import compile_semantics
        from knowledge.runtime_fact_publication import write_owner_manifest
        from plc.instruction_owner_store import OwnerDefinitionStore
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source,manifest=build(root/'input','OLDTOKEN',('SYN_A','SYN_B'))
            with closing(sqlite3.connect(source)) as connection:
                row=connection.execute('SELECT target,payload_json FROM runtime_language_features').fetchone()
                expanded=json.loads(row[1]);expanded['models'].append('Q03UDV')
                for fact in expanded['facts']:fact['scope']['models'].append('Q03UDV')
                encoded=json.dumps(expanded,sort_keys=True,separators=(',',':'))
                connection.execute('UPDATE runtime_language_features SET payload_json=?,sha256=? WHERE target=?',
                    (encoded,hashlib.sha256(encoded.encode()).hexdigest(),row[0]));connection.commit()
                before=connection.execute('SELECT * FROM runtime_definitions').fetchall()
                self.assertEqual(connection.execute("SELECT count(*) FROM facts WHERE kind='language' AND target=?",(row[0],)).fetchone()[0],1)
            manifest.unlink();write_owner_manifest({'method':'instruction-material-compiler-v1','fact_bundle_version':'synthetic:wiring'},source,manifest,owner_count=2)
            result=compile_semantics(manifest,root/'output',source_batches={},language_batches=[expanded],source_owners_only=True,version='synthetic:reindex')
            self.assertEqual((result['changed_owners'],result['unchanged_owners_retained']),(0,2))
            with closing(sqlite3.connect(root/'output/plc_facts.sqlite')) as connection:
                self.assertEqual(connection.execute('SELECT * FROM runtime_definitions').fetchall(),before)
                self.assertEqual({r[0] for r in connection.execute("SELECT plc_model FROM facts WHERE kind='language' AND target=?",(row[0],))},{'FX3U','Q03UDV'})
                self.assertEqual(connection.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'OLDTOKEN'").fetchone()[0],4)
            with closing(OwnerDefinitionStore(result['manifest'])) as store:self.assertEqual(store.language_feature(row[0]),expanded)

    def test_selected_language_rebuild_repairs_invalid_payload_without_loading_instruction_owners(self):
        import copy,hashlib
        from scripts.compile_instruction_semantics import compile_semantics
        from knowledge.runtime_fact_publication import write_owner_manifest
        from plc.instruction_owner_store import OwnerDefinitionStore
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            source,manifest=build(root/'input','OLDTOKEN',('SYN_A','SYN_B'))
            with closing(sqlite3.connect(source)) as connection:
                row=connection.execute('SELECT target,payload_json FROM runtime_language_features').fetchone()
                original=json.loads(row[1]);broken=copy.deepcopy(original)
                broken['facts'][0]['value']={'condition':'unclassified language condition'}
                encoded=json.dumps(broken,sort_keys=True,separators=(',',':'))
                connection.execute('UPDATE runtime_language_features SET payload_json=?,sha256=? WHERE target=?',
                                   (encoded,hashlib.sha256(encoded.encode()).hexdigest(),row[0]))
                connection.commit()
                before=connection.execute('SELECT * FROM runtime_definitions').fetchall()
            manifest.unlink()
            write_owner_manifest({'method':'instruction-material-compiler-v1','fact_bundle_version':'synthetic:wiring'},
                                 source,manifest,owner_count=2)
            with closing(OwnerDefinitionStore(manifest)) as store:
                with self.assertRaises(ValueError):store.language_feature(row[0])
            result=compile_semantics(manifest,root/'output',source_batches={},language_batches=[original],
                replace_source_prefixes=['sample:'],source_owners_only=True,version='synthetic:language-repair')
            self.assertEqual((result['changed_owners'],result['unchanged_owners_retained']),(0,2))
            self.assertEqual(result['owner_access']['hydrated_owner_count'],0)
            with closing(OwnerDefinitionStore(result['manifest'])) as store:
                self.assertEqual(store.language_feature(row[0]),original)
            with closing(sqlite3.connect(root/'output/plc_facts.sqlite')) as connection:
                self.assertEqual(connection.execute('SELECT * FROM runtime_definitions').fetchall(),before)
            with self.assertRaises(ValueError):
                compile_semantics(manifest,root/'invalid',source_batches={},language_batches=[broken],
                    replace_source_prefixes=['sample:'],source_owners_only=True,version='synthetic:still-invalid')

    def test_source_owner_compile_hydrates_only_batch_keys_and_counts_added_owner(self):
        from unittest.mock import patch
        from scripts.compile_instruction_semantics import compile_semantics
        from plc.instruction_owner_store import OwnerDefinitionStore
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY  # Initialize metadata before guarding this compile.
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source,manifest=build(root/'input','OLDTOKEN',('SYN_A','SYN_B'))
            replacement=self.entry('SYN_A','NEWTOKEN')['facts'][0];replacement['id']='sample:SYN_A'
            addition=self.entry('SYN_C','ADDEDTOKEN')['facts'][0];addition['id']='sample:SYN_C'
            with closing(sqlite3.connect(source)) as connection:
                before=connection.execute("SELECT rowid,* FROM chunks WHERE instruction_opcode='SYN_B'").fetchone()
                language=connection.execute('SELECT * FROM runtime_language_features').fetchall()
                language_rowid=connection.execute("SELECT rowid FROM chunks WHERE chunk_type='language'").fetchone()[0]
                self.assertIsNone(connection.execute("SELECT value FROM meta WHERE key='stored_facts'").fetchone())
            revised_language=json.loads(language[0][1]);revised_language['aliases']=['NEWLANGALIAS']
            revised_language['facts'][0]['value']={'marker':'LANGNEWTOKEN'}
            with patch.object(OwnerDefinitionStore,'iter_owner_headers',side_effect=AssertionError('No full owner iteration')):
                result=compile_semantics(manifest,root/'output',source_batches={('FX3U','SYN_A'):[replacement],
                    ('FX3U','SYN_C'):[addition]},replace_source_prefixes=['sample:'],source_owners_only=True,
                    language_batches=[revised_language],version='synthetic:delta')
            self.assertEqual((result['owners'],result['changed_owners'],result['stored_facts']),(3,2,3))
            self.assertEqual(result['unchanged_owners_retained'],1)
            self.assertEqual((result['owner_access']['owner_lookup_count'],result['owner_access']['hydrated_owner_count']),(2,1))
            with closing(sqlite3.connect(root/'output/plc_facts.sqlite')) as connection:
                self.assertEqual(connection.execute("SELECT rowid,* FROM chunks WHERE instruction_opcode='SYN_B'").fetchone(),before)
                published=json.loads(connection.execute('SELECT payload_json FROM runtime_language_features').fetchone()[0])
                self.assertEqual(published['facts'][0]['value'],{'marker':'LANGNEWTOKEN'})
                self.assertEqual(connection.execute("SELECT rowid FROM chunks WHERE chunk_type='language'").fetchone()[0],language_rowid)
                self.assertEqual(connection.execute("SELECT count(*) FROM entity_index WHERE entity='**'").fetchone()[0],1)
                self.assertEqual(connection.execute("SELECT count(*) FROM entity_index WHERE entity='NEWLANGALIAS'").fetchone()[0],1)
                self.assertEqual(connection.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'LANGNEWTOKEN'").fetchone()[0],1)
                self.assertEqual(self.search(connection,'OLDTOKEN'),['SYN_B'])
                self.assertEqual(self.search(connection,'NEWTOKEN'),['SYN_A'])
                self.assertEqual(self.search(connection,'ADDEDTOKEN'),['SYN_C'])


class ReleaseMainStagingTests(unittest.TestCase):
    def test_real_main_stages_manifest_pair_without_gzip_before_any_build(self):
        from unittest.mock import patch
        import shutil
        sys.path.insert(0,str(ROOT/'scripts'))
        import build_release
        import runtime_knowledge_resources as collector
        class StopBeforeExternalBuild(Exception):pass
        for catalog_local in (False,True):
            with self.subTest(catalog_local=catalog_local),tempfile.TemporaryDirectory() as tmp:
                base=Path(tmp);resources=base/'resources-root';output=base/'output'
                runtime=resources/'resources/knowledge/runtime'
                legacy,_=build(runtime,'LEGACY_MARKER')
                if catalog_local:
                    (runtime/'instruction_definitions.manifest.json').unlink()
                    owner_dir=resources/'resources/instructions/mitsubishi'
                    db,manifest=build(owner_dir,'CANDIDATE_MARKER')
                else:db=legacy;manifest=runtime/'instruction_definitions.manifest.json'
                # Exercise a non-default authenticated DB basename, not only plc_facts.sqlite.
                renamed=db.with_name('selected-owner.sqlite');shutil.copyfile(db,renamed)
                data=json.loads(manifest.read_text());data['database']['filename']=renamed.name;manifest.write_text(json.dumps(data))
                for relative,_ in collector.FILES:
                    p=resources/relative
                    if not p.exists():p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
                catalog=resources/'resources/instructions/mitsubishi';catalog.mkdir(parents=True,exist_ok=True)
                for name in collector.INSTRUCTION_FILES:
                    if name!='instruction_definitions.json.gz':
                        original=ROOT/'resources/instructions/mitsubishi'/name
                        if original.is_file():shutil.copyfile(original,catalog/name)
                        else:(catalog/name).write_text('{}')
                argv=['build_release','--output',str(output),'--resource-root',str(resources),'--skip-frontend','--component','mcp']
                with patch.object(sys,'argv',argv),patch.object(build_release.subprocess,'run',side_effect=StopBeforeExternalBuild) as run:
                    with self.assertRaises(StopBeforeExternalBuild):build_release.main()
                self.assertEqual(run.call_count,1)
                staged=output/'inputs'/('resources/instructions/mitsubishi'if catalog_local else 'resources/knowledge/runtime')
                self.assertEqual((staged/manifest.name).read_bytes(),manifest.read_bytes())
                self.assertEqual((staged/renamed.name).read_bytes(),renamed.read_bytes())
                self.assertTrue((output/'inputs/resources/instructions/mitsubishi/common.json').is_file())
                self.assertFalse(list((output/'inputs').rglob('instruction_definitions.json.gz')))

if __name__=='__main__':unittest.main()
