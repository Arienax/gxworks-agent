"""Synthetic production-shape language bindings; no private manual excerpts."""
import copy
from pathlib import Path
import sqlite3
import tempfile
import unittest
from knowledge.runtime_fact_publication import (project_language_features,validate_language_feature,
    OwnerStoreWriter,hydrate_language_packet)
from test_runtime_fact_publication import language_feature
from test_instruction_owner_store import fixture

class ProductionShapeTests(unittest.TestCase):
    def feature(self):
        feature=language_feature();feature.pop('language');feature['models']=['FX5U']
        feature['aliases']=['多词 别名','**','operator alias']
        feature['references']=[{'models':['FX5U'],'opcode':'PWM','parameters':{'In1':'LEFT'},
                                'sources':[{'id':'42','manual_id':'synthetic','revision':'A'}]}]
        return feature

    def test_metadata_missing_language_and_source_disposition(self):
        raw=self.feature();original=copy.deepcopy(raw)
        out,audit=project_language_features([raw]);feature=out[0]
        self.assertEqual(raw,original);self.assertNotIn('language',feature)
        self.assertEqual(feature['models'],raw['models']);self.assertEqual(feature['aliases'],raw['aliases'])
        self.assertEqual(feature['references'][0]['parameters'],{'In1':'LEFT'})
        self.assertEqual(audit[0]['metadata_projection'][0]['original'],raw['references'][0]['sources'])
        self.assertEqual(audit[0]['withheld'],[]);validate_language_feature(feature,published=True)
        wrong=copy.deepcopy(raw);wrong['references'][0]['models']=['Q00']
        with self.assertRaises(ValueError):project_language_features([wrong])
        wrong=copy.deepcopy(raw);wrong['private_evidence']='not permitted'
        with self.assertRaises(ValueError):project_language_features([wrong])

    def test_conflict_note_keeps_dispute_and_scope(self):
        raw=self.feature();fact=raw['facts'][0]
        fact['value']={'source_conflicts':{'GT':{'models':['FX5U'],'reason':'ANY_BIT table conflicts with numeric operation contract'}}}
        out,audit=project_language_features([raw]);result=out[0]['facts'][0]
        self.assertEqual(result['status'],fact['status'])
        self.assertEqual(result['value']['source_conflicts']['GT']['models'],['FX5U'])
        self.assertEqual(result['value']['source_conflicts']['GT']['reason']['note_ref'],'source_conflicts.GT.reason')
        self.assertIn('ANY_BIT table conflicts with numeric operation contract',result['note'])
        self.assertEqual(result['note_kind'],'explanation')
        self.assertTrue(any(x.get('semantics')=='source_conflict_preserved_not_resolved' for x in audit[0]['metadata_projection']))

    def test_alias_index_hydrates_actual_related_owner_without_callable_promotion(self):
        raw=fixture();feature=project_language_features([self.feature()])[0][0]
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'facts.sqlite';writer=OwnerStoreWriter(db,{k:v for k,v in raw.items() if k!='entries'})
            for entry in raw['entries']:writer.add_entry(entry)
            writer.add_language_feature(feature);writer.finalize(Path(tmp)/'instruction_definitions.manifest.json')
            with sqlite3.connect(db) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM facts WHERE kind='language' AND target='OPERATOR ALIAS' AND plc_model='FX5U'").fetchone()[0],1)
                packet=hydrate_language_packet(connection,'ST.CALL','FX5U')
                self.assertEqual(packet['value']['related_instructions'][0]['parameters'],{'In1':'LEFT'})
                self.assertFalse(packet['value']['related_instructions'][0]['call_available'])
                with self.assertRaises(ValueError):hydrate_language_packet(connection,'ST.CALL','Q00')
                connection.execute("DELETE FROM runtime_owner_index WHERE target_model='FX5U'")
                with self.assertRaises(ValueError):hydrate_language_packet(connection,'ST.CALL','FX5U')

if __name__=='__main__':unittest.main()
