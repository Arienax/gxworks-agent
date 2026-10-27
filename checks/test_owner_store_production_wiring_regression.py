"""Actual shared manual-tool and MCP entry points select a manifest without path patches."""
from contextlib import closing
import json
import importlib.util
import importlib.metadata
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import unittest
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
        env=dict(os.environ);env['PYTHONPATH']=os.pathsep.join([str(ROOT/'src'),env.get('PYTHONPATH','')]);env['GXW2_INSTRUCTION_CATALOG']=str(ROOT/'resources/instructions/mitsubishi')
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
