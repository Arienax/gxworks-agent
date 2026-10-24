"""Portable schema/semantic regressions; full source replay is a separate audit.

Unit fixtures replace only the long section-text digest. Clause hashes and
reviewed relations remain unmodified. Private full-source audit checks real
section digests and the end-to-end condition pipeline for all 132 identities.
"""
import hashlib
import json
from copy import deepcopy
import unittest
from unittest.mock import patch
from tools import fact_fx5_file_backlog_review as review


class FileBacklogTest(unittest.TestCase):
    def call(self, token, page, clause, kind='error', declared=(), **changes):
        section={'section_token':token,'text':'unit test section context'}
        key=(token,kind,page,hashlib.sha256(clause.encode()).hexdigest())
        block={'page':page,**deepcopy(review.SOURCE_BLOCK_GUARDS.get(key,{}))}
        baseline={k:section.get(k) for k in ['text','tables','page_layouts','pages','outline','first_page']}
        expected_context=hashlib.sha256(json.dumps(baseline,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        section.update(changes.pop('section',{}));block.update(changes.pop('block',{}))
        with patch.dict(review.SECTION_TEXT_SHA256,{token:hashlib.sha256(b'unit test section context').hexdigest()}), patch.dict(review.SECTION_CONTEXT_SHA256,{token:expected_context}):
            return review.review_auxiliary(kind,clause,section=section,block=block,value={},declared=set(declared))

    def item(self, token, page, rule_kind):
        hits=[v for (s,k,p,h),v in review.REVIEWED_RELATIONS.items() if s==token and p==page and any(r['kind']==rule_kind for r in v['rules'])]
        self.assertEqual(len(hits),1)
        return hits[0]

    def test_rename_new_name_not_original_path(self):
        result=self.call('renaming_the_specified_file',574,'The specified folder name string contains 244 characters or more.',declared=['s3'])
        self.assertEqual(result['rules'][0]['subject'],{'operand':'s3'})
        self.assertEqual(result['rules'][0]['count'],244)
        self.assertEqual(result['rules'][0]['when'],{'target_type':'folder'})

    def test_complete_exact_clause_required(self):
        clause='The specified folder name string contains 244 characters or more.'
        for changed in [clause.replace('244','245'),clause+' Except for files.',clause[:-1]]:
            self.assertIsNone(self.call('renaming_the_specified_file',574,changed,declared=['s3']))

    def test_section_page_and_operand_guards(self):
        clause='The specified folder name string contains 244 characters or more.'
        self.assertIsNone(self.call('renaming_the_specified_file',575,clause,declared=['s3']))
        self.assertIsNone(self.call('renaming_the_specified_file',574,clause,declared=['s2']))
        self.assertIsNone(self.call('renaming_the_specified_file',574,clause,declared=['s3'],section={'text':'changed qualification'}))
        self.assertIsNone(self.call('copying_the_specified_file',574,clause,declared=['s3']))

    def test_action_column_cannot_be_confused_with_cause(self):
        clause='No SD memory card is inserted.'
        self.assertIsNone(self.call('error_codes_generated_for_file_operation_instructions',583,clause,block={'has_action_column':True,'cause_cell_bound':False}))
        result=self.call('error_codes_generated_for_file_operation_instructions',583,clause,block={'has_action_column':True,'cause_cell_bound':True})
        self.assertEqual(result['rules'][0]['predicate'],'not_inserted')

    def test_ftp_delimiter_direction(self):
        for token,page,operand in [('sending_ftp_client_files',1091,'s2'),('retrieving_ftp_client_files',1096,'s3')]:
            clause=f"The delimiter used to separate the drive number in ({operand}) is neither ':\\' nor ':/'."
            result=self.call(token,page,clause,declared=[operand])
            self.assertEqual(result['rules'][0]['subject'],{'operand':operand})
            self.assertEqual([self.decode_literal(x) for x in result['rules'][0]['allowed']],[':\\',':/'])

    def test_ftp_wildcard_scope_is_each_segment(self):
        result=self.call('sending_ftp_client_files',1091,"Two or more asterisks '*' are used in the file name (before the period) or the extension specified by (s2).",declared=['s2'])
        rule=result['rules'][0]
        self.assertEqual(rule['count'],2)
        self.assertEqual(rule['segments'],['filename_before_period','extension'])
        self.assertEqual(rule['quantifier'],'any_segment')

    def test_ftp_temporary_file_bit_polarity(self):
        for token,page in [('sending_ftp_client_files',1088),('retrieving_ftp_client_files',1093)]:
            rr=self.item(token,page,'control_bit_encoding')['rules']
            bit3=next(r for r in rr if r['subject']['bit']==3)
            self.assertEqual(bit3['values'],{'0':'create_temporary_file','1':'do_not_create_temporary_file'})
            self.assertEqual(bit3['temporary_filename'],'FTPCLI_I.TMP')

    def test_status_merged_cell_second_range(self):
        result=self.call('acquiring_the_status_of_the_specified_file',578,'Last update date/time: Second',kind='control_data',declared=['d2'])
        self.assertEqual(result['rules'][0]['subject'],{'operand':'d2','offset':9})
        self.assertEqual(result['rules'][0]['inclusive_intervals'],[[0,59]])

    def test_status_size_external_environment_qualification(self):
        result=self.call('acquiring_the_status_of_the_specified_file',578,'File size (in units of bytes) 0 to 4294967294*1',kind='control_data',declared=['d2'])
        rule=result['rules'][0]
        self.assertEqual(rule['word_offsets'],[2,3])
        self.assertEqual(rule['inclusive_intervals'],[[0,4294967294]])
        self.assertEqual(rule['when'],{'file_access_environment':'CPU_module'})
        self.assertEqual(rule['otherwise'],{'domain_depends_on':'external_operating_system'})

    def test_copy_or_move_and_not_silently_normalized(self):
        copy=self.item('copying_the_specified_file',549,'file_transfer_collision_policy')
        move=self.item('moving_the_specified_file',559,'file_transfer_collision_policy')
        cr=next(r for r in copy['rules'] if r['kind']=='file_transfer_collision_policy')
        mr=next(r for r in move['rules'] if r['kind']=='file_transfer_collision_policy')
        self.assertIn('any',cr['when']);self.assertIn('all',mr['when'])
        self.assertEqual(cr['nested_items_under_skipped_folder'],'continue_transfer')

    def test_table_layout_model_and_field_mutations_rejected(self):
        args=('acquiring_the_status_of_the_specified_file',578,'Last update date/time: Second')
        for section in [{'tables':[{'rows_json':'changed selector'}]},{'page_layouts':[[575,'FX5U only changed model badge']]}]:
            self.assertIsNone(self.call(*args,kind='control_data',declared=['d2'],section=section))
        self.assertIsNone(self.call(*args,kind='control_data',declared=['d2'],block={'bound_subjects':[{'operand':'d2','offset':8}]}))

    def test_put_null_path_is_not_literal_NULL(self):
        r=self.item('sending_ftp_client_files',1090,'file_path_default')
        rule=next(v for v in r['rules'] if v['kind']=='file_path_default')
        self.assertEqual(rule['when']['any'][0],{'string_terminator_at_start':True,'character_code':0})
        delimiter=next(v for v in r['rules'] if v['kind']=='file_path_delimiter_contract')
        self.assertNotIn('width_bytes',delimiter)
        self.assertEqual(delimiter['character_codes'],[92,47])

    def test_matrix_unsigned_csv_modes_differ_between_read_and_write(self):
        read=self.item('reading_data_from_the_specified_file',515,'file_mode_unit_combination_error')['rules'][0]
        write=self.item('writing_data_to_the_specified_file',538,'file_mode_unit_combination_error')['rules'][0]
        self.assertNotIn([0x101,2],read['valid_pairs'])
        self.assertIn([0x101,2],write['valid_pairs'])
        self.assertNotIn([0x101,0],write['valid_pairs'])

    def test_odd_byte_read_preserves_high_byte(self):
        rr=self.item('reading_data_from_the_specified_file',493,'partial_word_preservation')['rules']
        r=next(x for x in rr if x['kind']=='partial_word_preservation')
        self.assertEqual(r['written_bits'],[0,7]);self.assertEqual(r['preserved_bits'],[8,15])
        self.assertEqual(r['when']['equals'],3)

    def test_put_source_domain_is_not_guessed_from_extension(self):
        reviewed=self.call('sending_ftp_client_files',1091,'The file name that cannot be transferred is specified by (s2).',declared=['s2'])
        r=reviewed['rules'][0]
        self.assertEqual(r['permitted_roots'],['LOGGING','DEBUG'])
        self.assertEqual(r['permitted_file_roles'],['logging_file','memory_dump_file'])
        self.assertEqual(r['file_role_classification'],'requires_source_file_metadata_not_extension_guess')
        self.assertNotIn('supplementary_source',r)
        self.assertEqual(reviewed['additional_sources'],[{'manual_id':'fx5_communication_j','manual_number':'SH-082625ENG','revision':'J','pdf_pages':[201]}])

    @staticmethod
    def decode_literal(value):
        assert value['kind']=='unicode_codepoint_sequence'
        return ''.join(chr(code) for code in value['codepoints'])

    def test_exact_literal_roundtrip_through_projection(self):
        from knowledge.fact_corpus import project_definition
        # Every special character sequence must survive JSON and public projection
        # as code points. This is not a byte sequence or a Unicode storage stride.
        seen=[]
        def visit(value):
            if isinstance(value,dict):
                if value.get('kind')=='unicode_codepoint_sequence':
                    self.assertEqual(set(value),{'kind','codepoints'})
                    seen.append(self.decode_literal(value))
                for child in value.values():visit(child)
            elif isinstance(value,list):
                for child in value:visit(child)
        for index,record in enumerate(review.REVIEWED_RELATIONS.values()):
            value={**deepcopy(record['value_updates']),'rules':deepcopy(record['rules'])}
            original=json.loads(json.dumps(value))
            fact={'id':'file:'+str(index),'dimension':record['dimension'],'status':record.get('status','source_verified'),'value':original,'sources':[],'scope':{},'depends_on':[]}
            projected,withheld=project_definition({'opcode':'FILE_REVIEW','facts':[fact]})
            self.assertEqual(withheld,[],index)
            self.assertEqual(projected['facts'][0]['value'],original,index)
            self.assertEqual(projected['facts'][0]['status'],fact['status'])
            visit(projected['facts'][0]['value'])
        for required in ['~$EXFILER.ERD',':\\',':/','?','\\','*','/']:
            self.assertIn(required,seen)
        filename=self.item('writing_extended_file_register',589,'file_capacity_condition')['rules'][0]['required_for']['file']
        self.assertEqual(filename['codepoints'],[126,36,69,88,70,73,76,69,82,46,69,82,68])
        self.assertEqual(self.decode_literal(filename),'~$EXFILER.ERD')
        for token,page,operand in [('sending_ftp_client_files',1091,'s2'),('retrieving_ftp_client_files',1096,'s3')]:
            clause=f"The delimiter used to separate the drive number in ({operand}) is neither ':\\' nor ':/'."
            r=self.call(token,page,clause,declared=[operand])['rules'][0]
            self.assertEqual([v['codepoints'] for v in r['allowed']],[[58,92],[58,47]])
        wildcard=self.call('sending_ftp_client_files',1091,"Wild card characters, '*' and '?', are used in the string specified by (s3).",declared=['s3'])['rules'][0]['wildcards']
        self.assertEqual([v['codepoints'] for v in wildcard],[[42],[63]])

    def test_all_review_values_pass_strict_typed_projection(self):
        from knowledge.fact_corpus import typed_value,UnresolvedExpression
        for key,record in review.REVIEWED_RELATIONS.items():
            value={**deepcopy(record['value_updates']),'rules':deepcopy(record['rules'])}
            self.assertEqual(typed_value(value),value,key)
        # No global relaxation is used: bare punctuation remains rejected.
        for raw in ['~$EXFILER.ERD',':\\',':/','?','\\']:
            with self.assertRaises(UnresolvedExpression):typed_value(raw)

    def test_literal_source_conflicts_stay_conflicts(self):
        for token,page in [('writing_data_to_the_specified_file',519),('moving_the_specified_file',563)]:
            r=self.item(token,page,'source_conflict')
            self.assertEqual(r['status'],'conflict')
            self.assertEqual(r['rules'][0]['resolution'],'not_established')

    def test_review_results_are_not_shared_mutable_state(self):
        args=('renaming_the_specified_file',574,'The specified folder name string contains 244 characters or more.')
        a=self.call(*args,declared=['s3']);a['rules'][0]['count']=0
        self.assertEqual(self.call(*args,declared=['s3'])['rules'][0]['count'],244)

    def test_bounded_review_and_no_execution_claim(self):
        self.assertEqual(len(review.REVIEWED_RELATIONS),125)
        for entry in review.REVIEWED_RELATIONS.values():
            self.assertTrue(entry['full_clause_review'])
            self.assertEqual(entry['value_updates']['core_execution_capability'],'not_established')
            self.assertTrue(entry['rules'])
        self.assertIsNone(review.review_auxiliary('error','invented rule',section={},block={},value={},declared=set()))

if __name__=='__main__':unittest.main()
