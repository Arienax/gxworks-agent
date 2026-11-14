"""Synthetic production-shape language bindings; no private manual excerpts."""
import copy
from contextlib import closing
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
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM facts WHERE kind='language' AND target='OPERATOR ALIAS' AND plc_model='FX5U'").fetchone()[0],1)
                packet=hydrate_language_packet(connection,'ST.CALL','FX5U')
                self.assertEqual(packet['value']['related_instructions'][0]['parameters'],{'In1':'LEFT'})
                self.assertFalse(packet['value']['related_instructions'][0]['call_available'])
                with self.assertRaises(ValueError):hydrate_language_packet(connection,'ST.CALL','Q00')
                connection.execute("DELETE FROM runtime_owner_index WHERE target_model='FX5U'")
                with self.assertRaises(ValueError):hydrate_language_packet(connection,'ST.CALL','FX5U')

def _current_st_value(target, identity):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    feature=DEFAULT_INSTRUCTION_REGISTRY.language_feature(target,language='st')
    validate_language_feature(feature,published=True)
    return next(f['value'] for f in feature['facts'] if f['id']==identity)


def test_ST_call_binding_distinguishes_positional_function_and_named_FB_outputs():
    value=_current_st_value('ST.CALL.ARGUMENTS','st.call_arguments_semantics')
    function=value['function_call_arguments'];fb=value['fb_call_arguments']
    assert function['argument_binding']=='positional'
    assert function['en_eno']=={'applies_when':'function_has_EN_ENO','first_argument':'EN','return':'ENO'}
    assert fb['argument_binding']=='named' and fb['binding_operator']==':='
    assert fb['required_arguments']=={'callee_kind':'user_created_function_block',
        'classes':['VAR_INPUT','VAR_IN_OUT'],'explicit_values_required':True}
    assert fb['output_arguments']['when_result_unused']=='omission_allowed'
    assert fb['argument_label_classes']['VAR_INPUT']['writable_in_POU'] is False
    assert fb['argument_label_classes']['VAR_IN_OUT']['writable_in_POU'] is True
    assert fb['output_result']['access']=='instance.member'
    assert fb['var_output_option']['disabled']['explicit_VAR_OUTPUT_argument']=='compile_error'


def test_ST_loop_entry_exit_and_advisory_nesting_are_preserved_independently():
    rules=_current_st_value('ST.CONTROL_FLOW','st.control_flow_semantics')['own_source_semantics']
    assert rules['FOR']['order']==['initialize','test_last_value','execute_body','increment','return_to_test']
    assert rules['FOR']['default_increment']==1
    assert rules['FOR']['excluded_variable_forms']==['structure_element','array_element']
    assert rules['FOR']['repeat_variable_after_exit']=='retain_completion_value'
    assert rules['WHILE']['condition_position']=='before_body' and rules['WHILE']['body_executions_min']==0
    assert rules['REPEAT']['condition_position']=='after_body' and rules['REPEAT']['body_executions_min']==1
    assert rules['REPEAT']['repeat_when'] is False and rules['REPEAT']['stop_when'] is True
    assert rules['EXIT']['continuation']=='after_loop'
    assert rules['RETURN']['later_statements']=='skipped'
    assert rules['CASE']['duplicate_priority']=='upper_statement' and rules['CASE']['K_prefix_allowed'] is False
    assert rules['nesting_limit']['documented_levels']==16
    assert rules['nesting_limit']['levels_17_or_more_conversion_error'] is False


def test_ST_type_families_do_not_classify_word_bit_strings_as_integer_types():
    value=_current_st_value('ST.DATATYPES','st.datatype_scope_and_families')
    families=value['data_type_families']
    assert families['bit']==['BOOL'] and families['bit_string']==['WORD','DWORD']
    assert families['integer']==['INT','DINT','UINT','UDINT']
    assert families['real']['requires_CPU_support'] is True
    assert value['label_class_scope']['VAR_RETAIN']['unsupported_CPU_family']=='FX'
    assert value['selection_constraints']['literal_length_and_formal_parameter_length']=='independent_limits'


def test_ST_project_FX_hidden_item_is_device_initial_value_and_task_controls_remain_conditional():
    value=_current_st_value('ST.PROJECT','st.project_structure_scope')
    fx=value['cpu_family_differences']['FX']
    assert fx['hidden_project_tree_items']==['device_initial_value']
    assert fx['program_file_execution_condition'] is False
    assert 'structure' not in fx['hidden_project_tree_items']
    assert value['pou_language_scope']['function']==['st','fbd','structured_ladder']
    assert value['pou_language_scope']['FX_unavailable_languages']==['ladder','sfc']
    tasks=value['task_execution']
    assert tasks['event_field']['FALSE']=={'mode':'interval','period_field':'Interval'}
    assert tasks['timer_output_control']['when']=='task_not_executed'
    assert tasks['timer_output_control']['selected']=='reset' and tasks['timer_output_control']['cleared']=='retain'
    assert tasks['interrupt']['FX']['input_interrupt']=={'rising':'Ix01','falling':'Ix00'}
    assert tasks['interrupt']['QL']['pointer_maximum']==31


def test_ST_power_type_rule_does_not_certify_unspecified_numeric_precision():
    from shared.model_context import technical_value,model_knowledge_value
    value=_current_st_value('ST.OPERATOR.POWER','st:power_native_domain')
    assert value['base']=='ANY_REAL' and value['exponent']=='ANY_NUM' and value['result']=='ANY_REAL'
    assert value['runtime_execution']['intermediate_precision_specified_by_source'] is False
    assert value['runtime_execution']['underflow_specified_by_source'] is False
    assert value['runtime_execution']['numeric_engine_certified_for_these_models'] is False
    projected=model_knowledge_value(technical_value(value))
    assert projected=={'operator':'**','base':'ANY_REAL','exponent':'ANY_NUM','result':'ANY_REAL'}


def test_selected_ST_features_retrieve_for_all_69_GXWorks2_members(subtests):
    import json
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from knowledge.fact_corpus import resolve
    from shared.model_context import model_knowledge_value,technical_value
    targets=['ST.CALL.ARGUMENTS','ST.CONTROL_FLOW','ST.DATATYPES','ST.OPERATOR.POWER','ST.PROJECT']
    for target in targets:
        feature=DEFAULT_INSTRUCTION_REGISTRY.language_feature(target,language='st')
        models={m for f in feature['facts'] for m in f['scope']['models'] if not m.startswith('FX5')}
        assert len(models)==69
        for cpu in sorted(models):
            with subtests.test(target=target,cpu=cpu):
                rows=resolve('language',[target],plc_model=cpu)
                assert len(rows)==1
                packet=json.loads(rows[0]['text'])
                assert packet['target']==target and packet['scope']['models']==[cpu]
                assert packet['value']['facts']
                assert model_knowledge_value(packet['value'])['facts']
        projected=model_knowledge_value(technical_value(feature))
        assert projected['facts'] and all('sources' not in f and 'verification' not in f for f in projected['facts'])


if __name__=='__main__':unittest.main()
