import unittest
from tools.fact_fx5_sorting_review import review_sorting_precaution, _PARTIAL, _SAME_RIGHT, _LEFT, _TEXT
from tools.fact_fx5_precaution_review import review_precaution_clause

class SortingReviewTest(unittest.TestCase):
    def section(self,page):
        form,first = ('SORTTBL2',874) if page == 876 else ('DSORTTBL2',877)
        return {'first_page':first,'text':f'FX5S FX5UJ FX5U FX5UC ENO:={form}(EN,s,n1,n2,n3,d);'}
    def review(self,page,clause):
        return review_precaution_clause(clause,section=self.section(page),block={'page':page},value={},declared={'s','n1','n2','n3','d'})
    def test_all_six_diagram_identities_bind_same_region_exception(self):
        for page in (876,879):
            for clause in (_PARTIAL,_SAME_RIGHT,_LEFT):
                result=self.review(page,clause)
                self.assertTrue(result['full_clause_review'])
                self.assertEqual(result['rules'][0]['exception'],{'same_device':['s','d']})
                self.assertEqual(result['rules'][0]['left']['element_bits'],16 if page==876 else 32)
                self.assertEqual(result['value_updates']['core_execution_capability'],'not_established')
                self.assertEqual(len(result['value_updates']['subjects']),4)
    def test_visual_marks_and_text_agree(self):
        for page in (876,879):
            partial=self.review(page,_PARTIAL)['value_updates']['diagram_review']['examples']
            self.assertTrue(all(not x['permitted'] for x in partial))
            exact=self.review(page,_SAME_RIGHT)['value_updates']['diagram_review']['examples']
            self.assertTrue(all(x['permitted'] for x in exact))
            self.assertEqual(self.review(page,_TEXT)['rules'],self.review(page,_PARTIAL)['rules'])
    def test_source_and_operand_changes_do_not_reuse_review(self):
        kwargs={'section':self.section(876),'block':{'page':876},'declared':{'s','n1','n2','n3','d'}}
        self.assertIsNone(review_sorting_precaution(_TEXT+' changed',**kwargs))
        kwargs['declared'].remove('n2');self.assertIsNone(review_sorting_precaution(_TEXT,**kwargs))
        kwargs['declared'].add('n2');kwargs['section']['first_page']=877
        self.assertIsNone(review_sorting_precaution(_TEXT,**kwargs))


class SortingInstanceLimitTest(unittest.TestCase):
    def test_error_count_is_program_occurrences_not_scan_reexecution(self):
        from tools.fact_fx5_condition_review import _review_auxiliary_clause
        section={'first_page':871,'text':'ENO:=SORTTBL(EN,s,n1,n2,n3,d); These instructions can only be used once in any program.'}
        kwargs={'section':section,'block':{'page':873,'codes':['1811H']},'value':{},'declared':{'s','n1','n2','n3','d'}}
        result=_review_auxiliary_clause('error','These instructions are used more than once.',**kwargs)
        self.assertEqual(result['rules'][0],{'kind':'sort_instance_limit','maximum':1,'scope':'program','operation_error':True})
        section['first_page']=874
        other=_review_auxiliary_clause('error','These instructions are used more than once.',**kwargs)
        self.assertIsNone(other)

if __name__=='__main__':unittest.main()
