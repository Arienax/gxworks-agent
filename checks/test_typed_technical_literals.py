"""Narrow publication syntax, never a PLC compilation/admission verdict."""
import copy
import unittest
from knowledge.fact_corpus import typed_value, project_definition, UnresolvedExpression


class TypedTechnicalLiteralsTests(unittest.TestCase):
    def test_formal_based_and_single_unit_time_literals(self):
        for value in ('2#0010','16#FF','8#337','T#1s','T#100ms',
                      '2#0010_0011','16#ff','T#-1ms'):
            with self.subTest(value=value):self.assertEqual(typed_value(value),value)
        for value in ('2#102','8#338','16#FG','10#123','2#','2#_1','16#FF_',
                      'T#1fortnight','T#1s2h','T#NaNs','T#1 s','16#FF\nPRIVATE'):
            with self.subTest(value=value),self.assertRaises(UnresolvedExpression):typed_value(value)

    def test_schema_scoped_spellings_preserved(self):
        value={'examples':{'string':["'ABC'",'"ABC"',"'A''B'"]},
               'target_product':'GX Works2','constant_input_formal_classes':['VAR_INPUT CONSTANT'],
               'block_open':'(*','block_close':'*)'}
        self.assertEqual(typed_value(value),value)
        for value in ('GX Works2','VAR_INPUT CONSTANT',"'ABC'",'(*','*)'):
            with self.subTest(value=value),self.assertRaises(UnresolvedExpression):typed_value(value)
        with self.assertRaises(UnresolvedExpression):typed_value({'unrelated':{'target_product':'GX Works2'}})

    def test_prose_private_paths_and_multiline_not_newly_admitted(self):
        for value in ('Run this private instruction now','C:\\private\\audit.json',
                      '/home/private/review.txt',"'quoted prose outside literal schema'",'GX Works2\nsecret'):
            with self.subTest(value=value),self.assertRaises(UnresolvedExpression):typed_value(value)
        for value in ("'bad\nline'",'"bad\rline"',"'bad\tline'", "'bad\u2028line'",
                      "'C:\\private\\audit'", "'/home/private/audit'", "'unmatched", "'A'B'"):
            with self.subTest(value=value),self.assertRaises(UnresolvedExpression):
                typed_value({'examples':{'string':[value]}})
        for value in ({'target_product':'GX Works2 arbitrary prose'},
                      {'constant_input_formal_classes':['VAR_INPUT CONSTANT secret']},
                      {'block_open':'(* secret *)'}):
            with self.subTest(value=value),self.assertRaises(UnresolvedExpression):typed_value(value)

    def test_literal_fact_is_preserved_without_promoting_other_semantics(self):
        fact={'id':'synthetic:literal','dimension':'parameters.fbd_literals',
              'status':'declared_unverified','verification':'not_performed',
              'scope':{'forms':['FBD.LITERALS'],'languages':['fbd'],'models':['FX3U']},
              'sources':[],'value':{'examples':{'binary':['2#0010'],'octal':['8#337'],
                'hexadecimal':['16#FF'],'string':["'ABC'",'"ABC"'],'time':['T#1s','T#100ms']},
                'target_product':'GX Works2','typed_iec_prefixes_admitted':False}}
        before=copy.deepcopy(fact);out,bad=project_definition({'facts':[fact]})
        self.assertFalse(bad);self.assertEqual(out['facts'][0],fact);self.assertEqual(fact,before)
        broken=copy.deepcopy(fact);broken['value']['source_conflicts']={'GT':{'reason':'A natural language conflict explanation'}}
        out,bad=project_definition({'facts':[broken]})
        self.assertEqual(out['facts'],[]);self.assertEqual(bad[0]['reason'],'unclassified_expression')

    def test_binding_and_comment_schema_values(self):
        self.assertEqual(typed_value({'constant_input_formal_classes':['VAR_INPUT CONSTANT']}),
                         {'constant_input_formal_classes':['VAR_INPUT CONSTANT']})
        self.assertEqual(typed_value({'block_open':'(*','block_close':'*)','nested_allowed':False}),
                         {'block_open':'(*','block_close':'*)','nested_allowed':False})

if __name__=='__main__':unittest.main()
