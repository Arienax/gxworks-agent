"""Independent real-input language regression. Private fixture is never embedded.
Set GXW_LANGUAGE_PRODUCTION_INPUT to the directory containing the uploaded bundle
and build_fact_knowledge.py. A skip is NOT production compatibility evidence.
"""
import ast
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import unittest
import tempfile

ROOT=os.environ.get('GXW_LANGUAGE_PRODUCTION_INPUT')

@unittest.skipUnless(ROOT, 'Actual private production input is required, not a synthetic replacement')
class ProductionLanguageBoundaryTests(unittest.TestCase):
    def setUp(self):
        from knowledge.runtime_fact_publication import project_language_features,validate_language_feature
        self.project=project_language_features;self.validate=validate_language_feature
        self.root=Path(ROOT);self.bundle=self.root/'instruction_definitions.json.gz'
        self.original=json.loads(gzip.decompress(self.bundle.read_bytes()))['language_facts']
    def test_actual_fixture_identity_and_shapes(self):
        self.assertEqual(hashlib.sha256(self.bundle.read_bytes()).hexdigest(),'129235ee7530ab95a95c448cf89e7ac4d438d9c48c4e1a3cb57fe687690b5ecf')
        self.assertEqual(len(self.original),18)
        self.assertEqual(sum(len(x['facts'])for x in self.original),25)
        self.assertEqual(sum(len(x.get('references',[]))for x in self.original),82)
    def test_all_original_metadata_and_reference_relationships_preserved(self):
        projected,receipt=self.project(self.original)
        self.assertEqual(len(projected),18)
        self.assertEqual(sum(len(x['facts']) for x in projected),25, 'Official technical facts must not be silently withheld')
        self.assertFalse([f for row in receipt for f in row.get('withheld',[])])
        for before,after in zip(self.original,projected):
            self.validate(after,published=True)
            for key in ('target','models','aliases','language'):
                self.assertEqual(key in before,key in after)
                self.assertEqual(before.get(key),after.get(key))
            self.assertEqual(len(before.get('references',[])),len(after.get('references',[])))
            for old,new in zip(before.get('references',[]),after.get('references',[])):
                self.assertEqual({k:v for k,v in old.items()if k!='sources'},{k:v for k,v in new.items()if k!='sources'})
        self.assertEqual(self.project(projected),(projected,[]),'Published representation must be idempotent')
    def test_remaining_facts_never_promote_status_or_expand_scope(self):
        projected,receipt=self.project(self.original)
        for before,after in zip(self.original,projected):
            originals={f['id']:f for f in before['facts']}
            for fact in after['facts']:
                self.assertEqual(fact['status'],originals[fact['id']]['status'])
                self.assertEqual(fact['scope'],originals[fact['id']]['scope'])
            missing=set(originals)-{f['id']for f in after['facts']}
            explained={f['id']for r in receipt if r['target']==before['target'] for f in r.get('withheld',[])}
            self.assertEqual(missing,explained)
    def test_unknown_metadata_and_model_scope_not_relaxed(self):
        variants=[]
        f=copy.deepcopy(self.original[0]);f['future_unreviewed_field']={'claim':'verified'};variants.append(f)
        f=copy.deepcopy(self.original[0]);f['facts'][0]['scope']['models']=['FX5U'];variants.append(f)
        f=copy.deepcopy(self.original[0]);f['language']='fbd';variants.append(f)
        f=copy.deepcopy(self.original[1]);f['references'][0]['models']=['FX5U'];variants.append(f)
        f=copy.deepcopy(self.original[1]);f['references'][0]['call_available']=True;variants.append(f)
        for i,f in enumerate(variants):
            with self.subTest(variant=i),self.assertRaises(ValueError):self.project([f])
    def test_future_metadata_fails_before_database_and_records_private_failure(self):
        from scripts.build_instruction_owner_store import build_owner_store
        doc=json.loads(gzip.decompress(self.bundle.read_bytes()))
        doc['language_facts'][0]['future_unreviewed_field']={'claim':'verified'}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'input.gz';source.write_bytes(gzip.compress(json.dumps(doc).encode()))
            with self.assertRaises(ValueError):
                build_owner_store(source,root/'runtime',project_raw=True,private_audit_dir=root/'private')
            self.assertFalse((root/'runtime/instruction_definitions.manifest.json').exists())
            self.assertFalse((root/'runtime/plc_facts.sqlite').exists())
            report=json.loads((root/'private/projection-failure.json').read_text())
            self.assertIn('language',json.dumps(report).lower())

    def test_uploaded_producer_merge_executes_and_preserves_original_references(self):
        tree=ast.parse((self.root/'build_fact_knowledge.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='_merge_structured_language_review')
        ns={'copy':copy};exec(compile(ast.Module(body=[fn],type_ignores=[]),'<uploaded producer merge>','exec'),ns)
        features=copy.deepcopy(self.original);fact=copy.deepcopy(features[0]['facts'][0])
        fact['id']='structured:data_type_table@independent_test';fact['scope']['models']=['FX3U'];fact['publishable']=True;fact['owner_hint']='test-only'
        ns[fn.name](features,{'language_reference':[fact],'tool_bindings':[]})
        self.assertEqual(len(features),19)
        out,receipt=self.project(features);lookup={f['target']:f for f in out}
        self.assertEqual(lookup['ST.DATATYPES']['models'],['FX3U'])
        self.assertEqual(lookup['ST.DATATYPES']['facts'][0]['scope']['languages'],['st'])
        for old in self.original:
            if old.get('references'):
                current=next(f for f in features if f['target']==old['target'])
                self.assertEqual(current['references'],old['references'])
if __name__=='__main__':unittest.main()
