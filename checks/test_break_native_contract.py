"""BREAK literal/source contracts do not establish a verified program CFG."""
import copy,json
from pathlib import Path
import pytest
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-break-native-contract-repair')

def source_entry(model,op):
    source=next(e for e in json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
                if e['target_model']==model and e['opcode']==op)
    entry=copy.deepcopy(registry.definition_entry(op,cpu=model))
    entry['facts']=[f for f in entry['facts'] if not f['id'].startswith('authored:ql:')]+source['facts']
    return entry

@pytest.fixture
def source_view(monkeypatch):
    import plc.instructions as instructions
    import plc.validation as validation
    import plc.ir as ir
    local=copy.copy(registry)
    for name in ('_cpu_contracts','_definition_contract_bases','_definition_owners'):
        setattr(local,name,dict(getattr(registry,name)))
    local._loaded_definition_contracts=set(registry._loaded_definition_contracts);local._clear_definition_contracts()
    for model in ('Q03UDV','Q02H','L02'):
        for op in ('BREAK','BREAKP'):
            local._definition_owners[('mitsubishi',op,model)]=source_entry(model,op)
    for module in (instructions,validation,ir):monkeypatch.setattr(module,'DEFAULT_INSTRUCTION_REGISTRY',local)
    return local

def app(op,args,model='Q03UDV'):
    from plc.ir import build_plc_ir,validate_plc_ir
    program=dict(device_comments={},rungs=[dict(rung_id=1,branches=[dict(branch_id=1,y_offset_level=0,
        inputs=[dict(type='NO',address='X0')],outputs=[dict(type='APP_INSTR',opcode=op,operands=list(args))])])])
    validate_plc_ir(build_plc_ir(program,plc_model=model))

def ordered(op,args,model='Q03UDV'):
    from plc.ir import build_plc_ir,validate_plc_ir
    program=dict(device_comments={},rungs=[dict(rung_id=1,ordered_instructions=[dict(op='LD',args=['X0']),dict(op=op,args=list(args))])])
    validate_plc_ir(build_plc_ir(program,plc_model=model))

def test_break_own_literal_signatures_keep_program_control_and_real_codecs():
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    for model,profile in [('Q03UDV','q03udv'),('Q02H','q02'),('L02','l02')]:
        for op in ('BREAK','BREAKP'):
            spec=registry.resolve(op,cpu=model,operand_count=2)
            assert spec.category.value=='program_control' and spec.semantic_kind.value=='vendor'
            assert spec.native_operand_order==('D','Pn') and spec.st_call is None
            assert spec.operands[1].data_type=='pointer' and spec.operands[1].device_prefixes==('P',)
            assert spec.execution_form==('pulse' if op.endswith('P') else 'continuous')
            raw=encode_instruction(op,['D0','P1'],profile=profile,plc_model=model,text_encoding='cp932')
            listing=decode_token_program(parse_token_fragment(raw,0,len(raw)),profile=profile,text_encoding='cp932')
            assert not listing.gaps and listing.reconstruct()==raw
            assert [(i.mnemonic,i.args) for i in listing.instructions]==[(op,('D0','P1'))]

def test_break_pointer_compiler_requires_real_identity_not_memory():
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    for model in ('Q03UDV','Q02H','L02'):
        for op in ('BREAK','BREAKP'):
            entry=source_entry(model,op)
            assert refresh_reviewed_call_admission(entry,registry) and entry['call_available'] is True
            for corruption in ('D_type','word_layout','missing_range','unreviewed'):
                bad=source_entry(model,op)
                typed=next(f for f in bad['facts'] if f['id']=='authored:ql:native_arguments_d_pn:parameters.typed_operands')
                pn=typed['value']['operands'][1]
                if corruption=='D_type':pn.update(type='ANY16',device_class='D')
                elif corruption=='word_layout':pn['word_device_layout']={'word_count':1,'device_unit_bits':16}
                elif corruption=='missing_range':pn.pop('range')
                else:typed.update(status='candidate_evidence',verification='not_performed')
                assert not refresh_reviewed_call_admission(bad,registry),corruption

def test_break_public_app_and_ordered_pointer_is_qualified_not_a_word(source_view):
    for model in ('Q03UDV','Q02H','L02'):
        for op in ('BREAK','BREAKP'):
            for check in (app,ordered):
                for args in [('D0','P1'),('K4M0','P1'),('J1\\W0','P1'),('U0\\G0','P1'),('Z0','P1')]:check(op,args,model)
                for args in [('D0','D1'),('D0','K1'),('D0','P-1'),('D0','P1Z0'),('D0','P1.0'),('K1','P1'),('M0','P1')]:
                    with pytest.raises(ValueError):check(op,args,model)
    # A P spelling does not authorize a program pointer on a word-only call.
    for check in (app,ordered):
        with pytest.raises(ValueError):check('BKCMP=',['D0','D100','M0','P1'])

def test_break_missing_own_pointer_contract_rejects_both_public_paths(source_view):
    from dataclasses import replace
    for corruption in ('range','word_layout','role','status'):
        local=copy.copy(source_view);local._definition_owners=dict(source_view._definition_owners)
        entry=copy.deepcopy(source_view.definition_entry('BREAK',cpu='Q03UDV'))
        typed=next(f for f in entry['facts'] if f['id']=='authored:ql:native_arguments_d_pn:parameters.typed_operands')
        pn=typed['value']['operands'][1]
        if corruption=='range':pn.pop('range')
        elif corruption=='word_layout':pn['word_device_layout']={'word_count':1}
        elif corruption=='role':pn['role']='count'
        else:typed.update(status='candidate_evidence',verification='not_performed')
        # Temporarily replace only the source owner; never loosen metadata.
        original=source_view._definition_owners.get(('mitsubishi','BREAK','Q03UDV'))
        source_view._definition_owners[('mitsubishi','BREAK','Q03UDV')]=entry
        source_view._loaded_definition_contracts.discard(('mitsubishi','BREAK','Q03UDV'))
        try:
            for check in (app,ordered):
                with pytest.raises(ValueError):check('BREAK',['D0','P1'])
        finally:
            source_view._definition_owners[('mitsubishi','BREAK','Q03UDV')]=original
            source_view._loaded_definition_contracts.discard(('mitsubishi','BREAK','Q03UDV'))

def test_break_corrected_effect_default_preserves_unverified_control_flow(source_view):
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_context_units,technical_knowledge_text
    def walk(x):
        if isinstance(x,dict):
            yield x
            for v in x.values():yield from walk(v)
        elif isinstance(x,list):
            for v in x:yield from walk(v)
    for model in ('Q03UDV','Q02H','L02'):
        for op in ('BREAK','BREAKP'):
            entry=source_view.definition_entry(op,cpu=model)
            assert not any(f['dimension']=='effects.operation' and f['value'].get('operation')=='repeat_execution' for f in entry['facts'])
            packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=[model]),content_origin='authored_fact',value=entry),
                plc_model=model,language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
            record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,_knowledge_context=dict(
                model=model,language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
            nodes=list(walk(technical_context_units(technical_knowledge_text(record))))
            effect=next(n for n in nodes if n.get('operation')=='exit_FOR_NEXT_loop' and 'branch_pointer' in n)
            assert effect['control_flow_context_verified'] is False and effect['core_execution_established'] is False
            assert effect['required_loop_context']=='FOR_NEXT' and effect['exits_one_nesting_level_per_execution'] is True
            assert effect['target_requirement']=='same_program_file_and_defined'
            assert effect['stored_value']=='remaining_iterations_including_current_iteration'


def bound_program(rows, model='Q03UDV'):
    """Use the existing codec's actual records, including P label bytes."""
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program,TokenInstruction,TokenLabel
    from gxw.token_pou import parse_token_fragment
    from gxw.token_profiles import source_token_profile
    profile=source_token_profile(model)
    raw=b''.join(encode_instruction(op,args,profile=profile,plc_model=model,text_encoding='cp932') for op,args in rows)
    listing=decode_token_program(parse_token_fragment(raw,0,len(raw)),profile=profile,text_encoding='cp932')
    records=[]
    for index,record in enumerate(listing.records):
        row=dict(record_index=index,source_offset=record.tokens[0].offset,
                 raw_hex=b''.join(t.raw for t in record.tokens).hex())
        if isinstance(record,TokenInstruction):row.update(kind='instruction',op=record.mnemonic,args=list(record.args))
        else:
            assert isinstance(record,TokenLabel)
            row.update(kind='label',op='',args=[],text=record.text)
        records.append(row)
    return dict(device_comments={},rungs=[dict(rung_id=1,ordered_instructions=records)])


def context_rows(program):
    from plc.ir import build_plc_ir
    ir=build_plc_ir(program,plc_model='Q03UDV',_canonicalize_devices=False)
    return ir,[f for f in ir['analysis']['findings'] if f['code']=='BREAK_PROGRAM_CONTEXT']


def test_break_complete_saved_records_bind_own_pointer_and_innermost_loop():
    from plc.ir import validate_plc_ir
    rows=[('FOR',['K10']),('FOR',['K2']),('LD',['X0']),('BREAK',['D0','P1']),('NEXT',[]),
          ('P1',[]),('LD',['X0']),('BREAKP',['D1','P2']),('NEXT',[]),('P2',[]),('END',[])]
    ir,findings=context_rows(bound_program(rows));validate_plc_ir(ir)
    assert len(findings)==2
    for finding,begin,end in zip(findings,[1,0],[4,8]):
        assert finding['complete_saved_program_source'] is True
        assert finding['context_checks']==dict(pointer_defined_in_same_program='passed',inside_FOR_NEXT='passed')
        assert finding['innermost_FOR_record_index']==begin and finding['matching_NEXT_record_index']==end
        assert finding['exits_one_nesting_level_per_execution'] is True
        assert finding['control_flow_context_verified'] is False
    # The manual does not restrict the target's position relative to NEXT.
    ir,findings=context_rows(bound_program([('FOR',['K10']),('P1',[]),('LD',['X0']),
        ('BREAK',['D0','P1']),('NEXT',[]),('END',[])]))
    validate_plc_ir(ir);assert all(f['severity']=='unverified' for f in findings)


def test_break_definite_missing_current_program_pointer_and_outside_loop_reject():
    from plc.ir import validate_plc_ir
    for rows,reason in [
        ([('FOR',['K10']),('LD',['X0']),('BREAK',['D0','P2']),('NEXT',[]),('P1',[]),('END',[])],
         'pointer_not_defined_in_this_program'),
        ([('LD',['X0']),('BREAKP',['D0','P1']),('P1',[]),('END',[])], 'BREAK_outside_FOR_NEXT')]:
        ir,findings=context_rows(bound_program(rows))
        assert findings[0]['severity']=='error' and reason in findings[0]['requires']
        with pytest.raises(ValueError,match=reason):validate_plc_ir(ir)


def test_break_fragments_opaque_jumps_and_unbound_record_identity_stay_unverified():
    from plc.ir import validate_plc_ir
    base=bound_program([('FOR',['K10']),('LD',['X0']),('BREAK',['D0','P1']),('NEXT',[]),('P1',[]),('END',[])])
    variants=[]
    fragment=copy.deepcopy(base)
    for row in fragment['rungs'][0]['ordered_instructions']:
        for key in ('kind','record_index','source_offset','raw_hex'):row.pop(key,None)
    fragment['rungs'][0]['ordered_instructions']=[row for row in fragment['rungs'][0]['ordered_instructions'] if row['op']]
    variants.append(fragment)
    foreign_label=copy.deepcopy(base);foreign_label['rungs'][0]['ordered_instructions'][4]['text']='P2';variants.append(foreign_label)
    opaque=copy.deepcopy(base);opaque['rungs'][0]['ordered_instructions'][0].update(kind='opaque',op='',args=[],raw_hex='03ff03');variants.append(opaque)
    variants.append(bound_program([('LD',['X0']),('CJ',['P1']),('BREAK',['D0','P1']),('P1',[]),('END',[])]))
    variants.append(bound_program([('FOR',['K10']),('LD',['X0']),('BREAK',['D0','P1']),('P1',[]),('END',[])]))
    # An inner BREAK can jump past the outer NEXT; a second BREAK there may
    # exit the still-active outer loop. Lexical position cannot reject it.
    variants.append(bound_program([('FOR',['K10']),('FOR',['K2']),('LD',['X0']),('BREAK',['D0','P1']),
        ('NEXT',[]),('NEXT',[]),('P1',[]),('LD',['X0']),('BREAK',['D1','P2']),('P2',[]),('END',[])]))
    for program in variants:
        ir,findings=context_rows(program);validate_plc_ir(ir)
        assert findings and all(f['severity']=='unverified' and f['control_flow_context_verified'] is False for f in findings)
        assert findings[0]['context_checks']['inside_FOR_NEXT']=='unverified'


def test_break_actual_saved_gxw_retains_program_labels_and_context():
    from gxw.offline_project import read_offline_project,read_ordered_program,edit_ladder_project
    from gxw.project_templates import blank_ladder_baseline
    from plc.ir import validate_plc_ir
    baseline=blank_ladder_baseline('Q03UDV');before=read_ordered_program(read_offline_project(baseline))
    index=next(i for i,r in enumerate(before['listing'].records) if getattr(r,'mnemonic',None)=='END')
    rows=[('FOR',['K10']),('LD',['X0']),('BREAK',['D0','P1']),('NEXT',[]),('P1',[])]
    saved=edit_ladder_project(baseline,before['selected']['name'],[dict(operation='insert_before',record_index=index,
        expected=dict(op='END',args=[]),instructions=[dict(op=op,args=args) for op,args in rows])])
    after=read_ordered_program(read_offline_project(saved.data));validate_plc_ir(after['program_ir'])
    findings=[f for f in after['program_ir']['analysis']['findings'] if f['code']=='BREAK_PROGRAM_CONTEXT']
    assert len(findings)==1 and findings[0]['complete_saved_program_source'] is True
    assert findings[0]['context_checks']==dict(pointer_defined_in_same_program='passed',inside_FOR_NEXT='passed')


def test_break_public_diagnostics_and_issue_projection_keep_each_record_context():
    from agent_runtime.tool_context import build_tool_context
    from agent_runtime.program_tools import _get_diagnostics
    from application.projects import public
    from application.program_projection import issue_cards
    rows=[('FOR',['K10']),('FOR',['K2']),('LD',['X0']),('BREAK',['D0','P1']),
          ('NEXT',[]),('NEXT',[]),('P1',[]),('LD',['X0']),('BREAKP',['D1','P2']),('P2',[]),('END',[])]
    ir,_=context_rows(bound_program(rows))
    context=build_tool_context(dict(plc_model='Q03UDV',target_mode='ladder'),program_ir=ir)
    result=public(_get_diagnostics(context,{}))
    findings=[f for f in result['findings'] if f['code']=='BREAK_PROGRAM_CONTEXT']
    assert len(findings)==2 and [f['record_index'] for f in findings]==[3,8]
    assert all(f['context_checks']['inside_FOR_NEXT']=='unverified' for f in findings)
    assert all(f['control_flow_context_verified'] is False for f in findings)
    cards=public(issue_cards(ir,findings))
    assert len(cards)==2 and len({c['id'] for c in cards})==2
    assert all('Program source record: '+str(f['record_index']) in c['evidence'] for c,f in zip(cards,findings))
    assert all('inside_FOR_NEXT: unverified' in c['evidence'] and 'Dynamic control flow verified: False' in c['evidence']
               for c in cards)
    invalid,_=context_rows(bound_program([('LD',['X0']),('BREAK',['D0','P1']),
        ('BREAKP',['D1','P1']),('P1',[]),('END',[])]))
    invalid_context=build_tool_context(dict(plc_model='Q03UDV',target_mode='ladder'),program_ir=invalid)
    rejected=public(_get_diagnostics(invalid_context,{}))
    assert rejected['valid'] is False
    errors=[f for f in rejected['findings'] if f['code']=='BREAK_PROGRAM_CONTEXT']
    assert len(errors)==2 and [f['record_index'] for f in errors]==[1,2]
    assert rejected['counts']['error']==2
    # Cached findings are not published before the existing IR pass verifies
    # that they still describe this exact source.
    stale=copy.deepcopy(invalid);stale['analysis']['findings'].append(copy.deepcopy(errors[0]))
    stale_context=build_tool_context(dict(plc_model='Q03UDV',target_mode='ladder'),program_ir=stale)
    stale_result=public(_get_diagnostics(stale_context,{}))
    assert stale_result['valid'] is False and 'findings' not in stale_result
    # Unbound APP fragments have JSON paths, not invented source indices.
    graph=dict(device_comments={},rungs=[dict(rung_id=1,branches=[dict(branch_id=1,y_offset_level=0,
        inputs=[dict(type='NO',address='X0')],outputs=[dict(type='APP_INSTR',opcode='BREAK',operands=[d,'P1'])
                                                    for d in ('D0','D1')])])])
    partial,_=context_rows(graph)
    partial_context=build_tool_context(dict(plc_model='Q03UDV',target_mode='ladder'),program_ir=partial)
    partial_result=public(_get_diagnostics(partial_context,{}))
    partial_findings=[f for f in partial_result['findings'] if f['code']=='BREAK_PROGRAM_CONTEXT']
    assert len(partial_findings)==2 and all(f['record_index'] is None for f in partial_findings)
    assert len({tuple(f['json_paths']) for f in partial_findings})==2
    partial_cards=public(issue_cards(partial,partial_findings))
    assert len({c['id'] for c in partial_cards})==2
