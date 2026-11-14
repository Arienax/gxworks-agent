"""Own-source single-word bit selection, distinct from block reset."""
import json
from pathlib import Path
import pytest

HERE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-bset-brst-native-contract-repair')
FORMS = ('BRST', 'BRSTP', 'BSET', 'BSETP')


def _validate_new_call(opcode, operands, model):
    from plc.ir import build_plc_ir, validate_plc_ir
    payload = {'device_comments': {}, 'rungs': [{'rung_id': 0, 'header_element': None,
        'branches': [{'branch_id': 1, 'y_offset_level': 0,
        'inputs': [{'type': 'NO', 'address': 'X0'}],
        'outputs': [{'type': 'APP_INSTR', 'opcode': opcode, 'operands': operands}]}]}]}
    return validate_plc_ir(build_plc_ir(payload, plc_model=model))


def test_single_bit_default_contracts_keep_their_own_word_order_and_cpus():
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from knowledge.instruction_compiler import call_admission_explanation
    rows = json.loads((HERE/'contract-additions.json').read_text(encoding='utf-8'))['cpu_contracts']
    assert len(rows) == 4
    for row in rows:
        op = row['instruction']['mnemonic']
        assert len(row['cpus']) == 53
        for model in row['cpus']:
            form = registry.resolve_catalog_form(op, cpu=model)
            assert form and form.spec.native_operand_order == ('D', 'n')
            assert [(p.role.value, p.data_type, p.word_count) for p in form.spec.operands] == [
                ('read_write', 'word', 1), ('read', 'word', 1)]
            assert form.spec.execution_form == ('pulse' if op.endswith('P') else 'continuous')
            assert set(form.spec.operands[0].address_forms) == {'link_direct', 'buffer_memory'}
            assert 'Z' in form.spec.operands[0].device_prefixes
            assert not form.spec.operands[0].integer_literal_widths
            assert call_admission_explanation(form, model)[0] == 'admitted'
            assert any(s['manual_id'] == 'sh080809engx' and s['pdf_page'] == 416 for s in form.spec.contract_sources)


@pytest.mark.usefixtures('source_view')
@pytest.mark.parametrize('model', ['Q03UDV', 'Q02H', 'L02'])
def test_single_bit_actual_app_new_calls_use_word_heads_and_do_not_cap_n_at_fifteen(model):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.device_identity import instruction_operand_regions
    from plc.device_profiles import DEVICE_LIMITS
    from plc.ir import analyze_instruction_access
    for op in FORMS:
        for args in (['D10','K0'], ['D10','K15'], ['D10','K16'], ['D10','K31'],
                     ['D10','HFFFF'], ['D10','D20'], ['K4M0','K6'], ['Z0','K1'],
                     [r'J1\W0','K1'], [r'U0\G0','K1']):
            _validate_new_call(op,args,model)
        for args in (['K0','K1'], ['M0','K1'], ['D10.2','K1'], ['K5M0','K1'],
                     ['D10','E1.0'], ['D10','K32768'], ['D10'], ['D10','K1','D20']):
            with pytest.raises(ValueError):
                _validate_new_call(op,args,model)
        last = DEVICE_LIMITS[model]['D']
        _validate_new_call(op,['D'+str(last),'K31'],model)
        with pytest.raises(ValueError):
            _validate_new_call(op,['D'+str(last+1),'K31'],model)
        spec = registry.resolve(op,cpu=model,operand_count=2)
        assert spec.definition_facts
        regions = instruction_operand_regions(spec,op,['D10','K31'],model)
        own = [r for r in regions if r['operand_index'] == 0]
        assert not own  # One fixed scalar word is covered by ordinary access, not a dynamic region.
        reads,writes = analyze_instruction_access(op,['D10','K31'],plc_model=model)
        assert set(reads) == {'D10'} and set(writes) == {'D10'}
        assert not any('D11' in r.get('addresses',[]) for r in regions)


@pytest.mark.parametrize('model,profile', [('Q03UDV','q03udv'),('Q02H','q02'),('L02','l02')])
def test_single_bit_existing_codec_retains_own_literals_and_word_head(model,profile):
    from gxw.token_encoder import encode_instruction
    from gxw.models import GXWFormatError
    for op in FORMS:
        for args in (['D10','K16'],['K4M0','HFFFF'],['Z0','K1'],[r'J1\W0','K1'],[r'U0\G0','K1']):
            encoded = encode_instruction(op,args,profile=profile,plc_model=model,text_encoding='cp932')
            assert encoded and (encoded[0] == 6 if op.endswith('P') else encoded[0] == 5)
        with pytest.raises(GXWFormatError,match='ambiguous'):
            encode_instruction(op,['D10.2','K1'],profile=profile,plc_model=model,text_encoding='cp932')


@pytest.mark.usefixtures('source_view')
def test_single_bit_own_four_forms_roundtrip_the_existing_q03udv_source():
    from gxw.offline_project import edit_ladder_project, read_offline_project, read_ordered_program
    from gxw.project_templates import blank_ladder_baseline
    source = blank_ladder_baseline('Q03UDV')
    before = read_ordered_program(read_offline_project(source))
    end = next(i for i,row in enumerate(before['listing'].records) if getattr(row,'mnemonic',None) == 'END')
    calls = [(op,args) for op in FORMS for args in (['D10','K16'],['K4M0','HFFFF'])]
    instructions = []
    for op,args in calls:
        _validate_new_call(op,args,'Q03UDV')
        instructions += [{'op':'LD','args':['X0']},{'op':op,'args':args}]
    result = edit_ladder_project(source,before['selected']['name'],[{
        'operation':'insert_before','record_index':end,'expected':{'op':'END','args':[]},'instructions':instructions}])
    after = read_ordered_program(read_offline_project(result.data),before['selected']['name'])
    assert [(i.mnemonic,i.args) for i in after['listing'].instructions if i.mnemonic in FORMS] == [
        (op,tuple('H0FFFF' if a == 'HFFFF' else a for a in args)) for op,args in calls]


@pytest.fixture
def source_view(monkeypatch):
    """Use complete own reviewed input while preserving the public call chain."""
    import copy
    import plc.instructions as instructions
    import plc.validation as validation
    import plc.ir as ir
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    entries = json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
    registry = instructions.DEFAULT_INSTRUCTION_REGISTRY
    local = copy.copy(registry)
    for name in ('_cpu_contracts','_definition_contract_bases','_definition_owners'):
        setattr(local,name,dict(getattr(registry,name)))
    local._loaded_definition_contracts = set(registry._loaded_definition_contracts)
    local._clear_definition_contracts()
    for source in entries:
        owner = copy.deepcopy(registry.definition_entry(source['opcode'],cpu=source['target_model']))
        owner['facts'] = [f for f in owner['facts'] if not f['id'].startswith('authored:ql:')] + source['facts']
        assert refresh_reviewed_call_admission(owner,local)
        local._definition_owners[('mitsubishi',source['opcode'],source['target_model'])] = owner
    monkeypatch.setattr(instructions,'DEFAULT_INSTRUCTION_REGISTRY',local)
    monkeypatch.setattr(validation,'DEFAULT_INSTRUCTION_REGISTRY',local)
    monkeypatch.setattr(ir,'DEFAULT_INSTRUCTION_REGISTRY',local)
    return local


def test_single_bit_source_projection_retains_normalization_and_unselected_bits():
    import copy
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_context_units, technical_knowledge_text
    entries = json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
    assert len(entries) == 212
    def walk(value):
        if isinstance(value,dict):
            yield value
            for child in value.values():
                yield from walk(child)
        elif isinstance(value,list):
            for child in value:
                yield from walk(child)
    for source in entries:
        model,op = source['target_model'],source['opcode']
        entry = copy.deepcopy(registry.definition_entry(op,cpu=model))
        entry['facts'] = [f for f in entry['facts'] if not f['id'].startswith('authored:ql:')] + source['facts']
        packet = project_packet(dict(kind='instruction',target=op,scope=dict(models=[model]),
            content_origin='authored_fact',value=entry),plc_model=model,language='ladder',
            project_mode='native_ladder_instruction',aspects=['programming_semantics'])
        record = dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,
            _knowledge_context=dict(model=model,language='ladder',project_mode='native_ladder_instruction',
                                    delivery_languages=['ladder']))
        delivered = technical_context_units(technical_knowledge_text(record))
        assert any(node.get('bit_position_normalization') ==
                   {'when_n_exceeds':15,'use':'lower_4_bits_of_data'}
                   and node.get('other_bits') == 'unchanged'
                   and node.get('full_source_relation_complete') is False
                   and node.get('negative_bit_position_semantics_established') is False
                   for node in walk(delivered)),(model,op)
