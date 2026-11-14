"""Legacy positioning uses its own E header; modern K scope is separate."""
import copy
import json
from pathlib import Path
import pytest
from tools import fact_applied_review as producer

HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-ddrva-ddrvi-native-contract-repair')


def test_M_reviewed_table_header_is_a_source_premise_with_required_cell_identity():
    from plc.instruction_constraints import check_fact_constraints
    entries = json.loads((HERE.parent/'fx-positioning-structured-call-repair/source-facts.json').read_text(encoding='utf-8'))['entries']
    entry = next(e for e in entries if (e['target_model'], e['opcode']) == ('FX1S', 'DDRVI'))
    header = next(f for f in entry['facts'] if f['dimension'] == 'constraints.source_cpu_header')
    def check(fact):
        return check_fact_constraints([fact], {}, target='DDRVI', plc_model='FX1S', language='st')
    assert check(header)['status'] == 'passed'
    for field in ('model_group', 'source_mark'):
        incomplete = copy.deepcopy(header)
        incomplete['value'].pop(field)
        assert check(incomplete)['status'] == 'unverified'


def _structured_constraint_report(model, opcode, distance, frequency, *, pulse='Y0', direction='Y1', hardware=None):
    from plc.instruction_constraints import check_fact_constraints
    entries = []
    for name in ('source-facts.json', 'modern-source-facts.json'):
        entries.extend(json.loads((HERE/name).read_text(encoding='utf-8'))['entries'])
    facts = next(e['facts'] for e in entries if (e['target_model'], e['opcode']) == (model, opcode))
    additions = json.loads((HERE.parent/'fx-positioning-structured-call-repair/source-facts.json').read_text(encoding='utf-8'))['entries']
    extra = next(e['facts'] for e in additions if (e['target_model'], e['opcode']) == (model, opcode))
    roots = [f['id'] for f in extra if f['scope']['languages'] == ['st']]
    values = {'EN': True, 'S1': distance, 'S2': frequency,
              '$PARAMETER_NAMES': ['EN', 'S1', 'S2', 'D1', 'D2'],
              '$0': True, '$1': distance, '$2': frequency,
              '$OPERANDS': [None, None, None, pulse, direction]}
    context = {'hardware': hardware} if hardware else {}
    if model in {'FX3G', 'FX3GC', 'FX3S', 'FX3U', 'FX3UC'}:
        context['cpu_version'] = {'FX3G': '1.00', 'FX3GC': '1.40', 'FX3S': '1.00',
                                  'FX3U': '2.20', 'FX3UC': '1.00'}[model]
    return check_fact_constraints([*facts, *extra], values, target=opcode, plc_model=model,
        language='st', context=context, root_ids=roots)


@pytest.mark.parametrize('model,opcode,distance,frequency,expected', [
    ('FX1NC', 'DDRVI', -999999, 10000, 'unverified'),
    ('FX1NC', 'DDRVI', 0, 100, 'failed'),
    ('FX1NC', 'DDRVA', 0, 100, 'unverified'),
    ('FX1NC', 'DDRVA', 1000000, 100, 'failed'),
    ('FX1NC', 'DDRVI', 10, 10001, 'failed'),
    ('FX3U', 'DDRVI', 10, 200000, 'unverified'),
    ('FX3U', 'DDRVI', 10, 200001, 'failed'),
])
def test_M_structured_numeric_ranges_use_own_named_parameters(model, opcode, distance, frequency, expected):
    result = _structured_constraint_report(model, opcode, distance, frequency)
    assert result['status'] == expected, result


@pytest.mark.parametrize('model,frequency,pulse,direction,hardware,expected', [
    ('FX1NC', 100, 'Y0', 'Y1', {'output_type': 'relay', 'modules': []}, 'failed'),
    ('FX1NC', 100, 'Y2', 'Y1', {'output_type': 'transistor', 'modules': []}, 'failed'),
    ('FX3G', 100, 'Y2', 'Y1', {'output_type': 'transistor', 'modules': [], 'point_count': 14}, 'failed'),
    ('FX3G', 100, 'Y2', 'Y1', {'output_type': 'transistor', 'modules': []}, 'unverified'),
    ('FX3G', 100, 'Y2', 'Y1', {'output_type': 'transistor', 'modules': [], 'point_count': 40}, 'unverified'),
    ('FX3U', 150000, 'Y0', 'Y1', {'output_type': 'transistor', 'modules': []}, 'failed'),
    ('FX3U', 150000, 'Y0', 'Y4', {'output_type': 'relay', 'positioning_module_model': 'FX3U-2HSY-ADP', 'positioning_module_quantity': 1}, 'unverified'),
    ('FX3U', 150000, 'Y0', 'Y5', {'output_type': 'relay', 'positioning_module_model': 'FX3U-2HSY-ADP', 'positioning_module_quantity': 1}, 'failed'),
    ('FX3UC', 100, 'Y0', 'Y4', {'output_type': 'transistor', 'positioning_module_model': 'FX3U-2HSY-ADP', 'positioning_module_quantity': 1}, 'failed'),
])
def test_M_structured_output_paths_keep_model_hardware_and_unverified_wiring(model, frequency, pulse, direction, hardware, expected):
    result = _structured_constraint_report(model, 'DDRVI', 10, frequency,
        pulse=pulse, direction=direction, hardware=hardware)
    assert result['status'] == expected, result


def test_E_own_FX1S_FX1N_four_signatures_and_runtime_limits():
    entries=json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
    positive=[e for e in entries if e['target_model']!='FX1NC']
    assert len(positive)==4
    for e in positive:
        rows={f['id']:f for f in e['facts']}
        t=rows['authored:applied:native_typed_operands@jy992d88101e']
        assert t['status']=='source_verified' and t['verification']=='source_checked'
        assert t['conditions']==[dict(context='project_mode',equals='native_ladder_instruction')]
        pins=t['value']['operands']
        assert [p['name'] for p in pins]==['S1','S2','D1','D2']
        assert [p['direction'] for p in pins]==['input','input','output','output']
        for p in pins[:2]:
            assert p['type']=='ANY32' and p['signed'] is True
            assert p['word_device_layout']==dict(device_unit_bits=16,word_count=2,value_bits=32,signed=True)
            assert p['range']['device_prefixes']==['T','C','D','V','Z']
            assert p['range']['literal_prefixes']==['K','H']
            assert p['range']['address_forms']==[]
        assert pins[0]['range']['minimum']==-999999 and pins[0]['range']['maximum']==999999
        assert pins[1]['range']['minimum']==10 and pins[1]['range']['maximum']==100000
        assert pins[2]['range']['allowed_devices']==['Y0','Y1']
        assert pins[3]['range']['device_prefixes']==['Y','M','S']
        assert 'D' not in pins[3]['range']['device_prefixes']
        v=rows['authored:applied:variant_family@jy992d88101e']['value']
        base=e['opcode'][1:]
        assert v['base_opcode']==base and v['variant_bindings'][e['opcode']]==dict(data_bits=32,double=True,pulse=False)
        op=rows['authored:applied:legacy_positioning_documented_processing@jy992d88101e']['value']
        assert op['command_OFF_during_motion']==dict(pulse_output='decelerate_and_stop',M8029_set_ON=False)
        assert op['pulse_output_flags']==dict(Y0='M8147',Y1='M8148')
        assert op['source_zero_exclusion_established'] is False
        assert op['current_hardware_and_wiring_verified'] is False
        assert op['full_source_relation_complete'] is op['Core_execution_verified'] is op['Native_execution_verified'] is False


def test_FX1NC_E_scope_demoted_without_touching_M_or_other_models():
    old=json.loads((HERE/'baseline-owners.json').read_text(encoding='utf-8'))['entries']
    new={(e['target_model'],e['opcode']):{f['id']:f for f in e['facts']} for e in json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']}
    for e in old:
        rows=new[e['target_model'],e['opcode']]
        for f in e['entry']['facts']:
            if any(s.get('manual_id')=='fxcpu_basic_applied_m' for s in f.get('sources',[])) and f['id'] in rows:
                assert rows[f['id']]==f
            if e['target_model']=='FX1NC' and f['id'].endswith('@jy992d88101e'):
                a=rows[f['id']]
                assert a['status']=='candidate_evidence' and a['verification']=='not_performed'
                assert a['value']['source_scope_confirmed'] is False
                assert 'native_form_source_scope_not_confirmed_for_FX1NC' in a['value']['unresolved_items']
    assert {e['target_model'] for e in old}=={'FX1N','FX1S','FX1NC'}


def test_legacy_positioning_guard_does_not_import_modern_or_other_page_rules():
    record=dict(opcode='DRVI',manual_id='jy992d88101e',manual_number='JY992D88101',revision='E',pdf_pages=[288,289],text='DDRVI 999,999 100kHz Y001 M8029 M8147 M8148')
    for changed in [dict(record,pdf_pages=[290,291]),dict(record,manual_id='fx3_positioning_k'),dict(record,text=record['text'].replace('DDRVI','DRVI'))]:
        output={};producer._repair_fx_legacy_positioning_source(None,[changed],output)
        assert not output


def test_R_DDRVI_admission_uses_signature_sources_not_hardware_overlay_sources():
    from dataclasses import replace
    import tempfile
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    from plc.instructions import InstructionRegistry, InstructionSpec

    catalog = Path(__file__).resolve().parents[1] / 'resources' / 'instructions' / 'mitsubishi'
    common = json.loads((catalog / 'common.json').read_text(encoding='utf-8'))
    capabilities = json.loads((catalog / 'instruction_capabilities.json').read_text(encoding='utf-8'))
    forms = {'DRVI', 'DDRVI', 'DRVA', 'DDRVA'}
    modern_forms = {'DDRVI', 'DDRVA'}
    base_specs = [InstructionSpec.from_mapping(row, default_vendor='mitsubishi')
                  for row in common['instructions'] if row.get('mnemonic') in forms]
    contracts = [row for row in common['cpu_contracts']
                 if row.get('instruction', {}).get('mnemonic') in forms
                 and (row.get('cpu') == 'FX3U' or 'FX3U' in row.get('cpus', []))]
    overlays = [row for row in capabilities['entries']
                if row.get('cpu') == 'FX3U' and set(row.get('forms', [])) & forms]
    assert {spec.mnemonic for spec in base_specs} == forms
    assert {row['instruction']['mnemonic'] for row in contracts} == modern_forms
    assert {tuple(row['forms']) for row in overlays} == {
        ('DRVI', 'DDRVI'), ('DRVA', 'DDRVA')}

    registry = InstructionRegistry(base_specs)
    registry.load_target_contracts(contracts)
    # Load only the real positioning overlay rows; their source shape and
    # capability dimensions remain exactly as authored in the catalogue.
    with tempfile.TemporaryDirectory() as temporary:
        overlay_path = Path(temporary) / 'capabilities.json'
        overlay_path.write_text(json.dumps({
            'schema_version': capabilities['schema_version'],
            'vendor': capabilities.get('vendor', 'mitsubishi'),
            'entries': overlays,
        }), encoding='utf-8')
        registry.load_capability_contract(overlay_path)

    spec = registry.resolve_catalog_form('DDRVI', cpu='FX3U').spec
    signature_fields = {'form_identity', 'cpu_applicability', 'arity',
                        'operand_order', 'operand_roles', 'operand_types'}
    signature_sources = [source for source in spec.contract_sources
                         if signature_fields.intersection(source.get('verified_fields', spec.verified_fields))]
    hardware_sources = [source for source in spec.contract_sources
                        if not signature_fields.intersection(source.get('verified_fields', spec.verified_fields))]
    assert len(signature_sources) == 2
    assert all((source['manual_id'], source['revision'], source['pdf_page']) in {
        ('fx3_programming_r', 'R', 560), ('fx3_programming_r', 'R', 561)
    } for source in signature_sources)
    assert hardware_sources == [{
        'manual_id': 'fx3_positioning_k',
        'verified_fields': ['completion_ownership', 'hardware_applicability'],
    }]

    source = {'manual_id': 'fx3_programming_r', 'revision': 'R', 'pdf_pages': [560, 561]}
    conditions = [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}]
    scope = {'models': ['FX3U'], 'forms': ['DDRVI'], 'languages': ['ladder']}
    parameters = []
    for name, operand in zip(spec.native_operand_order, spec.operands):
        parameters.append({
            'name': name,
            'direction': {'read': 'input', 'write': 'output', 'read_write': 'in_out'}[operand.role.value],
            'type': 'BOOL' if operand.data_type in {'bit', 'bool'} else 'DINT',
        })
    entry = {
        'vendor': 'mitsubishi', 'target_model': 'FX3U', 'opcode': 'DDRVI',
        'call_available': False,
        'facts': [
            {'id': 'fx3u-ddrvi-availability', 'dimension': 'constraints.cpu_availability',
             'value': {'available': True}, 'status': 'source_verified',
             'verification': 'source_checked', 'scope': scope,
             'conditions': conditions, 'depends_on': [], 'sources': [source]},
            {'id': 'fx3u-ddrvi-signature', 'dimension': 'parameters.typed_operands',
             'value': {'native_form': {'operand_order': list(spec.native_operand_order)},
                       'operands': parameters},
             'status': 'source_verified', 'verification': 'source_checked',
             'scope': scope, 'conditions': conditions, 'depends_on': [], 'sources': [source]},
        ],
    }
    assert refresh_reviewed_call_admission(entry, registry)
    assert entry['call_available'] is True

    original = registry._cpu_contracts[('mitsubishi', 'DDRVI', 'FX3U')]
    for missing in ('revision', 'pdf_page'):
        malformed = copy.deepcopy([dict(source) for source in original.contract_sources])
        reviewed_index = next(index for index, item in enumerate(malformed)
                              if 'operand_order' in item.get('verified_fields', []))
        malformed[reviewed_index].pop(missing)
        registry._cpu_contracts[('mitsubishi', 'DDRVI', 'FX3U')] = replace(
            original, contract_sources=tuple(malformed))
        rejected = copy.deepcopy(entry)
        rejected['call_available'] = False
        assert not refresh_reviewed_call_admission(rejected, registry), missing
        assert rejected['call_available'] is False
    registry._cpu_contracts[('mitsubishi', 'DDRVI', 'FX3U')] = original


def test_E_output_path_requirements_use_only_own_main_unit_facts():
    entries=json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
    for e in entries:
        matching=[f for f in e['facts'] if 'legacy_positioning_output_requirements' in f['id']]
        if e['target_model']=='FX1NC':
            assert not matching
            continue
        assert len(matching)==1
        f=matching[0];v=f['value']
        assert f['status']=='source_verified' and f['verification']=='source_checked'
        assert f['conditions']==[dict(context='project_mode',equals='native_ladder_instruction')]
        assert all(s['manual_id']=='jy992d88101e' for s in f['sources'])
        assert v['kind']=='pulse_output_path'
        assert v['main_unit']==dict(output_type='transistor',pulse_devices=['Y0','Y1'],minimum_Hz=10,maximum_Hz=100000)
        assert 'high_speed_output_adapter' not in v
        assert v['software_direction_destinations']==['Y','M','S']
        assert v['FX1S_FX1N_load_current_mA']==[10,100]
        for key in ['current_hardware_profile_verified','current_physical_direction_routing_verified','current_axis_busy_verified','hardware_validation_complete','full_source_relation_complete']:
            assert v[key] is False
