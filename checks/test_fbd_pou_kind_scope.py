from gxw.models import GXWFormatError
from gxw.native_write import native_source_creation_plan
from gxw.project_templates import blank_fbd_baseline
from gxworks2.workspace_protocol import workspace_business_request


def _creation_request(pou_type):
    return {
        'operation': 'create_source', 'protocol_version': 1,
        'installation': 'inst', 'directory': 'work', 'owner_token': 'owner',
        'source': {
            'program_name': 'NewPOU', 'cpu': 'FX3U/FX3UC', 'codepage': 932,
            'pou_type': pou_type, 'program_kind': 208, 'programs': [],
        },
    }


def test_workspace_protocol_accepts_function_pou_identity():
    request = workspace_business_request(_creation_request(0x01000003))
    assert request['change']['kind'] == 'function'
    assert request['change']['language'] == 'fbd'


def test_native_fbd_function_creation_is_scoped_to_the_manual_fx_domain():
    plan = native_source_creation_plan(blank_fbd_baseline('FX3U'), 'NewFunction',
                                       pou_kind='function')
    assert plan['pou_type'] == 0x01000003
    assert plan['program_kind'] == 208


def test_native_fbd_function_creation_uses_the_shared_Q_L_FX_POU_language_table(subtests):
    from gxw.project_templates import blank_fbd_models
    from gxworks2.project_profiles import native_project_profile
    for cpu in blank_fbd_models():
        with subtests.test(cpu=cpu):
            plan=native_source_creation_plan(blank_fbd_baseline(cpu),'NewFunction',pou_kind='function')
            assert native_project_profile(plan['cpu']).cpu==native_project_profile(cpu).cpu
            assert plan['pou_type']==0x01000003 and plan['program_kind']==208


def test_function_constructor_does_not_create_a_program_for_parameter_only_or_GXWorks3_targets():
    import pytest
    for cpu in ['QJ72LP25/QJ72BR15(RemoteI/O)','LJ72GF15-T2','LJ72MS15','FX5U']:
        with pytest.raises(GXWFormatError):
            blank_fbd_baseline(cpu)


def _sources_with_owner_result(pou_type, return_type):
    from dataclasses import replace
    from gxw.object_model import read_project_context
    context=read_project_context(blank_fbd_baseline('FX3U'))
    table=context.sources.local_table
    document=replace(context.declarations[table],owner_pou_type=pou_type,owner_return_type=return_type)
    return context.sources.with_declarations({table:document}),document


def test_function_own_name_uses_the_header_result_type_without_adding_a_label(subtests):
    for data_type in ['BOOL','INT','DINT','REAL']:
        with subtests.test(data_type=data_type):
            sources,document=_sources_with_owner_result(0x01000003,data_type)
            before=document.rows
            value=sources.actual_value(document.owner_name.lower())
            assert value['types']==[data_type]
            assert value['implicit_function_result'] is True
            assert value['readable'] is True and value['writable'] is True
            assert value['source_table']==document.logical_name and value['source_offset'] is None
            assert sources.declarations[document.logical_name].rows==before


def test_program_FB_and_void_function_names_do_not_become_implicit_result_variables(subtests):
    import pytest
    from plc.fbd_semantics import FBDSemanticError
    for kind,result_type in [(0x01000000,'BOOL'),(0x01000002,'BOOL'),(0x01000003,None),(0x01000003,"@'VOID'")]:
        with subtests.test(pou_kind=kind,return_type=result_type):
            sources,document=_sources_with_owner_result(kind,result_type)
            with pytest.raises(FBDSemanticError) as error:
                sources.actual_value(document.owner_name)
            assert error.value.finding['code']=='fbd_actual_unresolved'


def test_implicit_function_result_does_not_bind_another_function_name():
    import pytest
    from plc.fbd_semantics import FBDSemanticError
    sources,_=_sources_with_owner_result(0x01000003,'BOOL')
    with pytest.raises(FBDSemanticError):sources.actual_value('AnotherFunction')
