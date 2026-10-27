"""Exact field codec tests, without private source material."""
import copy
import json
import unittest
from knowledge.fact_field_codec import (pack_fact_fields, unpack_fact_fields, FactFieldCodecError,
    pack_fact_row, unpack_fact_row, FACT_ROW_SERIALIZATION, FIELD_SERIALIZATION, POOLED_FIELDS)


class Pool:
    def __init__(self): self.rows={};self.keys={}
    def intern(self, field, value):
        key=json.dumps([field,value],ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
        if key not in self.keys:
            index=len(self.rows)+1;self.keys[key]=index;self.rows[index]={'field':field,'value':value}
        return self.keys[key]
    def resolve(self,key):return self.rows.get(key)


class FactFieldCodecTests(unittest.TestCase):
    def test_exact_roundtrip_unknown_fields_and_absent_empty(self):
        p=Pool()
        variants=[{}, {'id':'a'}, {'id':'a','conditions':[]}, {'id':'a','conditions':None},
          {'id':'a','dimension':'effects.test','scope':{'models':['L02','L02-P'],'forms':['TEST']},
           'depends_on':['z','a'],'conditions':[{'flag':True},{'flag':1},{'flag':1.0}],
           'status':'conflict','verification':'unverified','source_refs':['x','y'],
           'value_ref':'hash','unknown':{'order':[3,2,1]},'value':{'unresolved_items':['a']}}]
        for fact in variants:
            before=copy.deepcopy(fact);packed=pack_fact_fields(fact,p.intern)
            self.assertEqual(unpack_fact_fields(packed,p.resolve),fact)
            self.assertEqual(fact,before)
            self.assertEqual(json.dumps(unpack_fact_fields(packed,p.resolve),sort_keys=True),json.dumps(fact,sort_keys=True))

    def test_optional_id_pool_is_literal_and_default_keeps_id_inline(self):
        p=Pool();fact={'id':'literal:CPU:form','scope':{'models':['Q00']}}
        self.assertEqual(pack_fact_fields(fact,p.intern)['id'],fact['id'])
        packed=pack_fact_fields(fact,p.intern,fields={'id'})
        self.assertNotIn('id',packed)
        self.assertEqual(unpack_fact_fields(packed,p.resolve),fact)
        self.assertEqual(packed['scope'],fact['scope'])
        with self.assertRaises(FactFieldCodecError):pack_fact_fields(fact,p.intern,fields={'unknown'})

    def test_exact_typed_values_are_not_equated(self):
        p=Pool()
        for value in (True,1,1.0):pack_fact_fields({'conditions':value},p.intern)
        self.assertEqual(len(p.rows),3)

    def test_detached_input_output_and_callback_values(self):
        p=Pool();fact={'id':'a','scope':{'models':['Q00']},'unknown':{'nested':[]}}
        packed=pack_fact_fields(fact,p.intern);restored=unpack_fact_fields(packed,p.resolve)
        restored['scope']['models'].append('Q01');restored['unknown']['nested'].append(1)
        packed['unknown']['nested'].append(2)
        self.assertEqual(fact,{'id':'a','scope':{'models':['Q00']},'unknown':{'nested':[]}})
        self.assertEqual(next(iter(p.rows.values()))['value'],{'models':['Q00']})

    def test_invalid_refs_and_ambiguous_fields_rejected(self):
        for refs in ([True],[0],[-1],['1'],[1,1],{},None):
            with self.subTest(refs=refs),self.assertRaises(FactFieldCodecError):
                unpack_fact_fields({'field_refs':refs},lambda _: {'field':'scope','value':{}})
        for row in (None,{}, {'field':'unexpected','value':'x'}, {'field':1,'value':{}},
                    {'field':'scope','value':{},'unknown':True}):
            with self.subTest(row=row),self.assertRaises(FactFieldCodecError):
                unpack_fact_fields({'field_refs':[1]},lambda _:row)
        with self.assertRaises(FactFieldCodecError):
            unpack_fact_fields({'scope':{},'field_refs':[1]},lambda _: {'field':'scope','value':{}})
        with self.assertRaises(FactFieldCodecError):
            unpack_fact_fields({'field_refs':[1,2]},lambda _: {'field':'scope','value':{}})
        with self.assertRaises(FactFieldCodecError):pack_fact_fields({'field_refs':[]},lambda *_:1)
        with self.assertRaises(FactFieldCodecError):pack_fact_fields({'scope':{},'conditions':[]},lambda *_:1)

    def test_non_json_and_invalid_intern_result_rejected(self):
        for value in (float('nan'),float('inf'),(1,2),{1:'bad'}):
            with self.subTest(value=value),self.assertRaises(FactFieldCodecError):
                pack_fact_fields({'scope':value},lambda *_:1)
        for ref in (True,None,'1',0):
            with self.subTest(ref=ref),self.assertRaises(FactFieldCodecError):
                pack_fact_fields({'scope':{}},lambda *_:ref)


class FactRowCodecTests(unittest.TestCase):
    def test_explicit_distinct_transport_and_old_codec_unchanged(self):
        self.assertNotEqual(FACT_ROW_SERIALIZATION, FIELD_SERIALIZATION)
        pool=Pool();fact={'id':'single','scope':{'models':['FX3U']}}
        old=pack_fact_fields(fact,pool.intern);new=pack_fact_row(fact,pool.intern)
        self.assertEqual(unpack_fact_fields(old,pool.resolve),fact)
        self.assertEqual(unpack_fact_row(new,pool.resolve),fact)
        with self.assertRaises(FactFieldCodecError):unpack_fact_row(old,pool.resolve)
        with self.assertRaises(FactFieldCodecError):unpack_fact_fields(new,pool.resolve)

    def test_repeat_id_opt_in_exact_owner_conditions_and_dependencies(self):
        pool=Pool();rows=[];facts=[];seen=set()
        for identity,model in [('shared','Q00'),('unique','L02'),('shared','FX3U')]:
            fact={'id':identity,'scope':{'models':[model]},'status':'conflict',
                  'depends_on':['other','prior'],'conditions':[{'value':True},{'value':1}],
                  'unknown':{'ordered':[3,2,1]},'verification':'unverified'}
            fields=POOLED_FIELDS | ({'id'} if identity in seen else set())
            rows.append(pack_fact_row(fact,pool.intern,fields=fields));facts.append(fact);seen.add(identity)
        self.assertEqual(rows[0][1]['id'],'shared');self.assertEqual(rows[1][1]['id'],'unique')
        self.assertNotIn('id',rows[2][1])
        self.assertEqual([v['value'] for v in pool.rows.values() if v['field']=='id'],['shared'])
        self.assertEqual([unpack_fact_row(r,pool.resolve) for r in rows],facts)

    def test_missing_null_empty_and_numeric_types_exact(self):
        pool=Pool()
        for fact in ({},{'id':None},{'id':''},{'conditions':None},{'conditions':[]},
                     {'conditions':{}},{'conditions':[True,1,1.0]},
                     {'parameters':{'typed_operands':[],'order':['b','a'],'arity':2}}):
            restored=unpack_fact_row(pack_fact_row(fact,pool.intern),pool.resolve)
            self.assertEqual(json.dumps(restored,sort_keys=True),json.dumps(fact,sort_keys=True))

    def test_mutation_isolation(self):
        pool=Pool();fact={'id':'a','scope':{'models':['Q00']},'unknown':{'items':[]}}
        row=pack_fact_row(fact,pool.intern);restored=unpack_fact_row(row,pool.resolve)
        restored['scope']['models'].append('Q01');restored['unknown']['items'].append(1)
        row[1]['unknown']['items'].append(2)
        self.assertEqual(fact['unknown']['items'],[])
        self.assertEqual(unpack_fact_row(row,pool.resolve)['scope']['models'],['Q00'])

    def test_malformed_rows_refs_unknown_fields_and_collisions(self):
        pool=Pool();pool.intern('id','a')
        for row in ({},None,(),[],[[]],[[],{},0],[{},{}],[[],[]],
                    [[],{'field_refs':[]}],[[True],{}],[[0],{}],[[999],{}],
                    [[1,1],{}],[[1],{'id':'b'}],[[],{1:'bad'}]):
            with self.subTest(row=row),self.assertRaises(FactFieldCodecError):
                unpack_fact_row(row,pool.resolve)
        with self.assertRaises(FactFieldCodecError):
            unpack_fact_row([[1],{}],lambda _: {'field':'unreviewed_field','value':None})


if __name__=='__main__':unittest.main()
