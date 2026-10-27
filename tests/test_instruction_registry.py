import pytest
from hypothesis import given, strategies as st


@pytest.mark.parametrize('cpu,opcode,symbols,meanings', [
    ('FX1N', 'TWR', ['S'], ['clock_data_head']),
    ('Q03UDV', 'S.SOCRCVS', ['U0', 'S', 'D'], ['dummy_unit', 'connection_number', 'receive_buffer_head']),
])
def test_call_and_contract_keep_manual_symbols_and_semantic_names(cpu, opcode, symbols, meanings):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    call = registry.describe_call(opcode, cpu=cpu)
    annotations = registry.describe_contract(opcode, cpu=cpu)['operand_annotations']
    assert [row['name'] for row in call['parameters']] == symbols
    for rows in (call['parameters'], annotations):
        assert [row['position'] for row in rows] == list(range(1, len(symbols) + 1))
        assert [row['symbol'] for row in rows] == symbols
        assert [row['semantic_name'] for row in rows] == meanings


@given(st.integers(min_value=0, max_value=32))
def test_operand_placeholders_never_manufacture_semantic_names(index):
    from plc.instructions import OperandSpec
    for name in (f'operand_{index}', f'S{index}', 'D'):
        operand = OperandSpec.from_mapping({'name': name}, index)
        assert not operand.semantic_name
        assert 'semantic_name' not in operand.slot_metadata(index + 1, name)
    explicit = OperandSpec.from_mapping({'name': 'S', 'semantic_name': 'connection_number'}, index)
    assert explicit.slot_metadata(index + 1, 'S')['semantic_name'] == 'connection_number'


@given(st.sampled_from(['Q03UDV', 'FX3U', 'FX3UC', 'FX5U', 'L02']),
       st.sampled_from(['', 'CPU', 'CPU-T']), st.booleans())
def test_reference_cpu_aliases_preserve_identity_without_admitting_arbitrary_groups(cpu, suffix, lower):
    from plc.target_identity import reference_target_identity
    spelling = f'  {cpu}{suffix}  '
    if lower:
        spelling = spelling.lower()
    assert reference_target_identity(spelling) == cpu
    assert reference_target_identity(reference_target_identity(spelling)) == cpu
    assert reference_target_identity(cpu + '/BOGUS') is None
    assert reference_target_identity(cpu + '/') is None


@pytest.mark.parametrize('spelling,expected', [('Q02CPU/Q02HCPU', 'Q02/Q02H'),
    ('FX3U / FX3UC', 'FX3U/FX3UC'), ('FX3U/FX1S', None), ('Q02/BOGUS', None)])
def test_reference_cpu_groups_keep_the_whole_registered_scope(spelling, expected):
    from plc.target_identity import reference_target_identity
    assert reference_target_identity(spelling) == expected


@pytest.mark.parametrize('cpu,opcode,signature', [
    ('Q00J', 'DUTY', 'DUTY(EN, n1, n2, D)'),
    ('L02', 'ICCNTRD1', 'ICCNTRD1(EN)'),
    ('L02', 'DMOV', 'DMOV(EN, s, d)'),
    ('FX1N', 'TWR', 'TWR(EN, S)'),
    ('Q03UDV', 'S.SOCRCVS', 'S_SOCRCVS(EN, U0, S, D)'),
])
def test_source_language_declarations_include_all_parameters(cpu, opcode, signature):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    call = DEFAULT_INSTRUCTION_REGISTRY.describe_call(opcode, cpu=cpu)
    assert call['st_signature'] == signature
    assert call['target_model'] == cpu
    assert all(s['target_model'] == cpu for s in call['contract_sources'])
    assert call['st_parameters'][0] == {'name': 'EN', 'role': 'read', 'type': 'bit'}
    assert 'EN' not in call['ladder_signature'].split()
    assert not DEFAULT_INSTRUCTION_REGISTRY.describe_call(opcode, cpu='UNLISTED_CPU')

from gxworks2.csv_importer import RawInstruction, _output_element
from plc.instructions import (
    DEFAULT_INSTRUCTION_REGISTRY,
    InstructionCategory,
    generation_app_instr_mnemonics,
)
from plc.ir import analyze_instruction_access
from plc.validation import (
    PLCJsonValidationError,
    find_unverified_app_instructions,
    validate_ladder_full,
)


@pytest.mark.parametrize("cpu,opcode,arity,st_name", [
    ("FX1N", "TWR", 1, "TWR"), ("Q00J", "DUTY", 3, "DUTY"),
    ("Q03UDV", "S.SOCRCVS", 3, "S_SOCRCVS"), ("L02", "ICCNTRD1", 0, "ICCNTRD1"),
])
def test_reviewed_manual_contracts_align_schema_and_exact_cpu(cpu, opcode, arity, st_name):
    registry = DEFAULT_INSTRUCTION_REGISTRY
    spec = registry.resolve(opcode, cpu=cpu)
    assert spec.min_operands == spec.max_operands == arity
    assert opcode in generation_app_instr_mnemonics(cpu)
    assert registry.resolve_st_call(st_name, cpu=cpu).opcode == opcode
    assert spec.contract_sources and all(s['target_model'] == cpu for s in spec.contract_sources)
    other = 'Q00J' if cpu != 'Q00J' else 'L02'
    assert opcode not in generation_app_instr_mnemonics(other)
    assert registry.resolve_st_call(st_name, cpu=other) is None


def test_q_socket_st_spelling_is_not_a_ladder_opcode():
    assert 'S_SOCRCVS' not in generation_app_instr_mnemonics('Q03UDV')
    assert DEFAULT_INSTRUCTION_REGISTRY.resolve_st_call('S.SOCRCVS', cpu='Q03UDV') is None


def test_definition_dependency_closure_is_atomic_scoped_and_verification_independent():
    from plc.instruction_definition import InstructionFactGroup, select_fact_dependencies
    base = {'value': {}, 'status': 'candidate_evidence', 'sources': [{'id': 'independent-source'}],
            'scope': {'models': ['FX3U'], 'forms': ['TEST']}}
    groups = [
        {**base, 'id': 'width', 'dimension': 'parameters.width'},
        {**base, 'id': 'retention', 'dimension': 'execution.disabled_retention'},
        {**base, 'id': 'outputs', 'dimension': 'effects.result_mapping', 'depends_on': ['width', 'retention'],
         'members': ['first', 'equal', 'last']},
    ]
    selected = select_fact_dependencies(groups, ['outputs'], opcode='TEST', model='FX3U')
    assert selected['bundles'] == [{'root': 'outputs', 'fact_ids': ['outputs', 'retention', 'width']}]
    assert len(selected['groups']) == 3 and not selected['gaps']
    assert selected['source_verification_complete'] is False
    assert InstructionFactGroup.from_mapping(groups[2]).as_mapping()['members'] == ['first', 'equal', 'last']
    for shortened in (groups[1:], [groups[0], {**groups[1], 'status': 'unknown'}, groups[2]]):
        missing = select_fact_dependencies(shortened, ['outputs'], opcode='TEST', model='FX3U')
        assert not missing['groups'] and not missing['bundles'] and missing['gaps']
    assert not select_fact_dependencies(groups, ['outputs'], opcode='TESTP', model='FX3U')['groups']
    assert not select_fact_dependencies(groups, ['outputs'], opcode='TEST', model='FX5U')['groups']


def test_definition_source_inventory_does_not_admit_manual_only_calls(tmp_path):
    import json
    from plc.instructions import InstructionRegistry, InstructionSpec
    registry = InstructionRegistry([InstructionSpec.from_mapping({'mnemonic': 'MOV'})])
    before = registry.known_mnemonics()
    entry = {'vendor': 'mitsubishi', 'opcode': 'MANUAL_ONLY', 'target_model': 'FX3U', 'facts': [],
             'source_materials': [{'id': 'manual-chapter'}], 'uninterpreted_content': [{'id': 'timing-diagram'}]}
    path = tmp_path / 'definitions.json'
    path.write_text(json.dumps({'schema_version': 1, 'method': 'instruction-material-compiler-v1', 'entries': [entry]}), encoding='utf-8')
    registry.load_instruction_definitions(path)
    assert registry.known_mnemonics() == before and registry.resolve('MANUAL_ONLY') is None
    assert registry.definition_inventory[0]['uninterpreted_content'] == [{'id': 'timing-diagram'}]
    from plc.instruction_definition import materialize_instruction_definition
    definition = materialize_instruction_definition('MANUAL_ONLY', plc_model='FX3U', lanes={}, registry=registry)
    assert definition['source_materials'] == entry['source_materials']
    assert definition['uninterpreted_content'] == entry['uninterpreted_content']
    assert registry.resolve('MANUAL_ONLY') is None
    fetched = registry.definition_entry('MANUAL_ONLY', cpu='FX3U')
    fetched['source_materials'].clear()
    assert registry.definition_entry('MANUAL_ONLY', cpu='FX3U')['source_materials'] == entry['source_materials']


@pytest.mark.parametrize('heading,expected', [
    ('17.1 FNC102 – ZPUSH/Batch Store of Index Register', ['ZPUSH']),
    ('3.2 I/O Response', []),
    ('4.3.7 Designation of Interrupt Input Signal for DVIT Instruction', []),
    ('9. One-speed Interrupt constant quantity feed - DVIT Instruction', ['DVIT']),
])
def test_literal_definition_heading_ownership_excludes_common_reference_topics(heading, expected):
    from knowledge.instruction_compiler import instruction_heading_names
    assert instruction_heading_names(heading) == expected


@pytest.mark.parametrize('entry', DEFAULT_INSTRUCTION_REGISTRY.definition_inventory,
                         ids=lambda e: e['target_model'] + ':' + e['opcode'])
def test_every_compiled_owner_enters_the_same_view_without_admission_or_review_inheritance(entry):
    from plc.instruction_definition import instruction_task_view, DEFINITION_DIMENSIONS
    from plc.instruction_resolution import resolve_instruction_lanes
    opcode, model = entry['opcode'], entry['target_model']
    definition = resolve_instruction_lanes(opcode, plc_model=model)['instruction_definition']
    assert set(definition['dimension_status']) == set(DEFINITION_DIMENSIONS)
    assert definition['semantic_completeness'] == 'not_established'
    assert definition['source_materials'] == entry['source_materials']
    assert definition['uninterpreted_content'] == entry['uninterpreted_content']
    for group in definition['facts']:
        assert group['scope']['forms'] == [opcode] and group['scope']['models'] == [model]
    view = instruction_task_view(definition)
    assert view['receipt']['generation_conformance'] == 'not_checked'
    assert not view['receipt']['packed_fact_ids'] and not view['receipt']['final_delivered_fact_ids']
    if not entry['call_available']:
        assert definition['applicability']['available'] is False
        assert opcode not in generation_app_instr_mnemonics(model)


def test_equal_result_expressions_do_not_erase_different_execution_dependencies():
    from plc.instruction_definition import instruction_task_view
    common = {'status': 'candidate_evidence', 'sources': [{'id': 'table'}],
              'scope': {'models': ['FX3U'], 'forms': ['TEST']}}
    output = {'behavior': 'expression_write', 'outputs': [{'target': {'parameter': 'D', 'kind': 'bit'},
              'expression': {'op': 'constant', 'value': True, 'type': {'kind': 'bool'}}}]}
    definition = {'opcode': 'TEST', 'target_model': 'FX3U', 'facts': [
        {**common, 'id': 'hold', 'dimension': 'execution.disabled', 'value': {'action': 'retain'}},
        {**common, 'id': 'clear', 'dimension': 'execution.disabled', 'value': {'action': 'clear'}},
        {**common, 'id': 'held-result', 'dimension': 'effects.result', 'value': output, 'depends_on': ['hold']},
        {**common, 'id': 'cleared-result', 'dimension': 'effects.result', 'value': output, 'depends_on': ['clear']},
    ]}
    view = instruction_task_view(definition, questions=['operation'])
    assert {b['root'] for b in view['bundles']} == {'held-result', 'cleared-result'}
    assert view['receipt']['equivalent_effect_alternatives'] == {}


@pytest.mark.parametrize('form_count', [1, 2, 4])
@pytest.mark.parametrize('model_count', [1, 2, 3])
def test_definition_diffs_preserve_owners_fields_and_literal_reviews(form_count, model_count):
    from plc.instruction_definition_storage import compact_definitions, expand_definitions
    forms = ['TEST', 'TESTP', 'DTEST', 'DTESTP'][:form_count]
    models = ['FX3U', 'FX3UC', 'FX5U'][:model_count]
    entries = []
    for index, model in enumerate(models):
        for form in forms:
            scope = {'forms': [form], 'models': [model]}
            entries.append({'vendor': 'mitsubishi', 'opcode': form, 'target_model': model,
                            'call_available': index != 2, 'source_materials': [], 'uninterpreted_content': [],
                            'facts': [{'id': 'types', 'dimension': 'parameters.types', 'scope': scope,
                                       'status': 'candidate_evidence', 'sources': [{'id': 'table'}],
                                       'value': {'bits': 32 if form.startswith('D') else 16,
                                                 'devices': ['D'] if index == 0 else ['D', 'R']}},
                                      {'id': 'effect', 'dimension': 'effects.expression_write', 'scope': scope,
                                       'status': 'source_verified' if index == 0 else 'unknown',
                                       'verification': 'source_checked' if index == 0 else 'not_performed',
                                       'sources': [{'id': 'review', 'target_model': model, 'opcode': form}] if index == 0 else [],
                                       'value': {'nested': {'keep': True, **({'limit': 8} if index == 0 else {})}}}]})
    payload = {'schema_version': 1, 'method': 'instruction-material-compiler-v1', 'entries': entries}
    compact = compact_definitions(payload, base_forms={form: 'TEST' for form in forms})
    assert len(compact['definitions']) == 1
    expanded = expand_definitions(compact)
    indexed = lambda items: {(e['opcode'], e['target_model']): {**e, 'facts': sorted(e['facts'], key=lambda f: f['id'])}
                             for e in items}
    assert indexed(expanded['entries']) == indexed(entries)
    assert len(compact['model_profiles']) == 1
    # The reviewed common fact stays literal even in the factored file.
    common = compact['definitions'][0]['common']['facts']['effect']
    if common['status'] == 'source_verified':
        assert common['scope']['models'] == ['FX3U']


def test_extending_an_applicability_profile_cannot_inherit_a_cpu_review(tmp_path):
    import json
    from plc.instructions import InstructionRegistry, InstructionSpec
    from plc.instruction_definition_storage import compact_definitions, expand_definitions
    payload = {'schema_version': 1, 'method': 'instruction-material-compiler-v1', 'entries': [
        {'vendor': 'mitsubishi', 'opcode': 'TEST', 'target_model': 'FX3U', 'source_materials': [],
         'uninterpreted_content': [], 'facts': [
             {'id': 'effect', 'dimension': 'effects.expression_write', 'value': {},
              'scope': {'models': ['FX3U'], 'forms': ['TEST']}, 'status': 'source_verified',
              'verification': 'source_checked', 'sources': [{'id': 'FX3U-review', 'target_model': 'FX3U'}]}]}]}
    compact = compact_definitions(payload)
    next(iter(compact['model_profiles'].values())).append('FX3UC')
    fx3uc = next(e for e in expand_definitions(compact)['entries'] if e['target_model'] == 'FX3UC')
    assert fx3uc['facts'][0]['scope']['models'] == ['FX3U']
    assert fx3uc['facts'][0]['sources'][0]['target_model'] == 'FX3U'
    path = tmp_path / 'definitions.json'
    path.write_text(json.dumps(compact), encoding='utf-8')
    registry = InstructionRegistry([InstructionSpec.from_mapping({'mnemonic': 'TEST'})])
    with pytest.raises(ValueError, match='exact CPU/form scopes'):
        registry.load_instruction_definitions(path)


def test_definition_diff_rejects_overlapping_cpu_overrides():
    from plc.instruction_definition_storage import compact_definitions, expand_definitions
    compact = compact_definitions({'entries': [
        {'opcode': 'TEST', 'target_model': 'FX3U', 'facts': []},
        {'opcode': 'TEST', 'target_model': 'FX3UC', 'facts': [], 'call_available': False}]})
    overrides = compact['definitions'][0]['model_diffs']
    assert len(overrides) == 1
    overrides.append(dict(overrides[0]))
    with pytest.raises(ValueError, match='Overlapping or unowned'):
        expand_definitions(compact)


def _ladder_with_app_instruction(opcode, operands):
    return {
        "device_comments": {},
        "rungs": [
            {
                "rung_id": 0,
                "debug_note": "",
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [],
                        "outputs": [
                            {
                                "type": "APP_INSTR",
                                "opcode": opcode,
                                "operands": list(operands),
                                "label": "",
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_registry_loads_common_and_model_specific_instructions():
    mov = DEFAULT_INSTRUCTION_REGISTRY.resolve("MOV")
    assert mov is not None
    assert mov.canonical_op == "MOVE"
    assert mov.category == InstructionCategory.ACTION
    assert mov.write_indexes == (1,)

    zrn = DEFAULT_INSTRUCTION_REGISTRY.resolve("ZRN")
    assert zrn is not None
    assert zrn.supports_cpu("FX3U")
    assert not zrn.supports_cpu("FX5U")

    drvtbl = DEFAULT_INSTRUCTION_REGISTRY.resolve("DRVTBL")
    assert drvtbl is not None
    assert drvtbl.supports_cpu("FX5U")
    assert not drvtbl.supports_cpu("FX3U")


def test_plc_ir_access_comes_from_registry_roles():
    reads, writes = analyze_instruction_access("MOV", ["D0", "D10"])
    assert reads == ["D0"]
    assert writes == ["D10"]

    reads, writes = analyze_instruction_access("INC", ["D20"])
    assert reads == ["D20"]
    assert writes == ["D20"]


def test_unknown_instruction_access_is_conservative():
    reads, writes = analyze_instruction_access(
        "FUTURE_VENDOR_OP", ["D0", "D10", "K1"]
    )
    assert reads == ["D0", "D10"]
    assert writes == []


def test_agent_validation_remains_strict_for_unknown_instruction():
    ladder = _ladder_with_app_instruction("FUTURE_VENDOR_OP", ["D0", "D10"])
    with pytest.raises(PLCJsonValidationError, match="unsupported APP_INSTR opcode"):
        validate_ladder_full(ladder, plc_model="FX3U")


def test_gx_import_validation_can_preserve_unknown_instruction():
    ladder = _ladder_with_app_instruction("FUTURE_VENDOR_OP", ["D0", "D10"])
    assert (
        validate_ladder_full(
            ladder,
            plc_model="FX3U",
            require_catalogued_instructions=False,
        )
        is ladder
    )
    findings = find_unverified_app_instructions(ladder)
    assert findings == [
        {
            "path": "$.rungs[0].branches[0].outputs[0]",
            "opcode": "FUTURE_VENDOR_OP",
            "operands": ["D0", "D10"],
            "status": "unverified",
            "edit_policy": "preserve_only",
        }
    ]


def test_known_instruction_still_checks_cpu_support():
    ladder = _ladder_with_app_instruction(
        "ZRN", ["K1000", "K100", "X0", "Y0"]
    )
    with pytest.raises(PLCJsonValidationError, match="ZRN is not supported by FX5U"):
        validate_ladder_full(ladder, plc_model="FX5U")


def test_csv_unknown_instruction_keeps_existing_app_instr_shape():
    raw = RawInstruction(
        step="10",
        op="FUTURE_VENDOR_OP",
        args=["D0", "D10"],
        label="vendor extension",
        source_row=4,
    )
    assert _output_element(raw, {}) == {
        "type": "APP_INSTR",
        "opcode": "FUTURE_VENDOR_OP",
        "operands": ["D0", "D10"],
        "label": "vendor extension",
    }


def test_mitsubishi_modifier_grammar_resolves_standard_and_irregular_forms():
    cases = {
        "MOV": ("MOV", False, False),
        "MOVP": ("MOV", False, True),
        "DMOV": ("MOV", True, False),
        "DMOVP": ("MOV", True, True),
        "ADDP": ("ADD", False, True),
        "DADDP": ("ADD", True, True),
        "DAND": ("WAND", True, False),
        "DANDP": ("WAND", True, True),
    }
    for opcode, (base, double, pulse) in cases.items():
        resolved = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode)
        assert resolved is not None, opcode
        assert resolved.base_mnemonic == base
        assert resolved.double is double
        assert resolved.pulse is pulse


def test_modifier_grammar_does_not_reinterpret_exact_d_prefixed_mnemonics():
    for opcode in ("DSW", "DECO", "DUTY"):
        resolved = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode)
        assert resolved is not None
        assert resolved.base_mnemonic == opcode
        assert resolved.double is False
        assert resolved.pulse is False
    assert DEFAULT_INSTRUCTION_REGISTRY.resolve_form("DDADDP") is None


def test_generation_enum_materializes_modifier_forms():
    fx3 = set(generation_app_instr_mnemonics("FX3U"))
    for opcode in (
        "MOVP",
        "DMOVP",
        "ADDP",
        "DADDP",
        "CMPP",
        "DCMPP",
        "BMOVP",
        "DFMOVP",
        "INCP",
        "DINCP",
        "DANDP",
    ):
        assert opcode in fx3
    assert "DDADDP" not in fx3


@pytest.mark.parametrize(
    "opcode,operands",
    [
        ("MOVP", ["D0", "D10"]),
        ("DMOVP", ["D0", "D10"]),
        ("ADDP", ["D0", "K1", "D10"]),
        ("DADDP", ["D0", "K1", "D10"]),
        ("DANDP", ["D0", "D2", "D10"]),
    ],
)
def test_validator_accepts_catalogued_modifier_forms(opcode, operands):
    ladder = _ladder_with_app_instruction(opcode, operands)
    assert validate_ladder_full(ladder, plc_model="FX3U") is ladder


def test_modifier_forms_reuse_registry_access_roles():
    reads, writes = analyze_instruction_access("DMOVP", ["D0", "D10"])
    assert reads == ["D0"]
    assert writes == ["D10"]

    reads, writes = analyze_instruction_access("DANDP", ["D0", "D2", "D10"])
    assert reads == ["D0", "D2"]
    assert writes == ["D10"]


def test_invalid_repeated_modifier_is_still_rejected():
    ladder = _ladder_with_app_instruction("DDADDP", ["D0", "K1", "D10"])
    with pytest.raises(PLCJsonValidationError, match="unsupported APP_INSTR opcode"):
        validate_ladder_full(ladder, plc_model="FX3U")


def test_operand_usage_facts_keep_independent_status_conditions_and_sources():
    from plc.instructions import OperandSpec
    payload = {
        "name": "length", "role": "read",
        "usage_facts": [
            {"facet": "purpose", "value": "Affected data length"},
            {"facet": "unit", "value": "word", "status": "source_verified", "sources": [{"manual_id": "fixture", "revision": "1"}]},
            {"facet": "range", "value": {"minimum": 1, "maximum": 16}, "status": "candidate_evidence",
             "sources": [{"id": "row-3"}], "conditions": ["16-bit form only"]},
        ],
    }
    spec = OperandSpec.from_mapping(payload, 3)
    facts = [fact.as_mapping() for fact in spec.usage_facts]
    assert [item["status"] for item in facts] == ["declared_unverified", "source_verified", "candidate_evidence"]
    assert facts[2]["conditions"] == ["16-bit form only"]
    payload["usage_facts"][2]["value"]["maximum"] = 512
    facts[1]["sources"][0]["revision"] = "changed"
    assert spec.usage_facts[2].as_mapping()["value"]["maximum"] == 16
    assert spec.usage_facts[1].as_mapping()["sources"][0]["revision"] == "1"
    assert OperandSpec.from_mapping({"name": "legacy"}, 1).usage_facts == ()


@pytest.mark.parametrize("fact", [
    {"facet": "purpose", "value": "Length", "status": "source_verified"},
    {"facet": "purpose", "value": "Length", "status": "candidate_evidence"},
    {"facet": "purpose", "value": " ", "sources": [{"id": "row"}]},
    {"facet": "unknown", "value": "Length"},
    {"facet": "purpose", "value": "Length", "status": "complete"},
    {"facet": "purpose", "value": "Length", "sources": [{"unrelated": "metadata"}]},
    {"facet": "purpose", "value": "Length", "conditions": "when enabled"},
])
def test_operand_usage_cannot_claim_evidence_without_its_own_source(fact):
    from plc.instructions import OperandSpec
    with pytest.raises(ValueError):
        OperandSpec.from_mapping({"name": "length", "usage_facts": [fact]}, 1)


@pytest.mark.parametrize('family', [
    'HSCS', 'HSCR', 'HSZ', 'ECMP', 'EZCP', 'EMOV', 'ESTR', 'EVAL', 'EBCD', 'EBIN',
    'EADD', 'ESUB', 'EMUL', 'EDIV', 'EXP', 'LOGE', 'LOG10', 'ESQR', 'ENEG',
    'SIN', 'COS', 'TAN', 'ASIN', 'ACOS', 'ATAN', 'RAD', 'DEG', 'SORT2', 'TBL', 'ABS', 'HCMOV', 'HSCT',
])
def test_source_reviewed_family_icon_is_not_an_FX3U_literal_call(family):
    from plc.instructions import generation_app_instr_mnemonics
    spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(family, cpu='FX3U')
    assert not spec.supports_cpu('FX3U') and spec.replacement_for_cpu('FX3U') == 'D' + family
    assert family not in generation_app_instr_mnemonics('FX3U')
    source = next(s for s in spec.contract_sources if s.get('review', {}).get('method') ==
                  'local_official_instruction_format_column_review')
    assert source['pdf_page'] and source['revision'] == 'R'
    ladder = _ladder_with_app_instruction(family, ['K1'] * (spec.min_operands or 0))
    with pytest.raises(PLCJsonValidationError, match='use D' + family):
        validate_ladder_full(ladder, plc_model='FX3U')

class _StubOwnerRegistry:
    """Minimal registry surface: only the exact owner lookup the projection uses."""

    def __init__(self, owners):
        self._owners = owners

    def definition_entry(self, opcode, *, cpu, vendor="mitsubishi"):
        return self._owners.get((vendor, str(opcode).upper(), str(cpu).upper()))


def test_the_exact_owner_admission_reaches_both_availability_layers():
    """One admission record answers the direct query, the lane and the definition.

    A form the compiled build did not admit must read unavailable everywhere, and the
    record that decided it stays visible: the answer is not a bare False.
    """
    from plc.instruction_resolution import resolve_instruction_lanes
    from plc.target_capabilities import resolve_target_applicability

    lanes = resolve_instruction_lanes("ADD", plc_model="FX2N")
    direct = resolve_target_applicability("ADD", "FX2N")
    nested = lanes["instruction_definition"]["applicability"]
    assert direct["available"] is lanes["target_applicability"]["available"] is nested["available"] is False
    admission = direct["owner_admission"]
    assert admission["available"] is False and admission["reasons"]
    assert admission["basis"] == {"vendor": "mitsubishi", "opcode": "ADD", "target_model": "FX2N"}
    for layer in (direct, lanes["target_applicability"], nested):
        assert layer["support_status"] == "definition_only"
        assert layer["owner_admission"]["reasons"] == admission["reasons"]
    # The admission state is now recorded inside the applicability answer, so the
    # caller can see that the shared spec alone would have said available: this pair
    # is one of the 769 where the generic answer reported a shared-catalog match.
    assert admission["generic_definition_available"] is True


def test_an_admitted_owner_keeps_the_runtime_answer_and_its_facts():
    """Admission does not erase the rest of the answer."""
    from plc.instruction_resolution import resolve_instruction_lanes

    lanes = resolve_instruction_lanes("ADD", plc_model="FX3U")
    applicability = lanes["target_applicability"]
    assert applicability["available"] is True
    assert applicability["owner_admission"]["available"] is True
    assert "inconsistent" not in applicability["owner_admission"]
    assert applicability["owner_admission"]["reasons"] == []
    definition = lanes["instruction_definition"]
    assert definition["applicability"]["available"] is True
    assert definition["facts"] and definition["source_materials"]


def test_a_missing_owner_record_keeps_the_previous_fallback_answer():
    """No record is not an explicit refusal: absence must not become unavailable."""
    from plc.target_capabilities import _generic_target_applicability, apply_owner_admission

    for opcode, model in (("MOV", "FX3U"), ("ADD", "FX2N")):
        generic = _generic_target_applicability(opcode, model)
        empty = _StubOwnerRegistry({})
        projected = apply_owner_admission(generic, opcode, model, registry=empty)
        assert projected == generic, (opcode, model)
        assert "owner_admission" not in projected


def test_an_owner_that_admits_a_missing_form_reports_the_disagreement():
    """The reverse mismatch is a finding, not a reason to restore availability."""
    from plc.target_capabilities import apply_owner_admission

    registry = _StubOwnerRegistry({("mitsubishi", "ADD", "FX2N"): {
        "vendor": "mitsubishi", "opcode": "ADD", "target_model": "FX2N",
        "call_available": True, "gaps": ["st_signature_not_measured"],
    }})
    generic = {"target_model": "FX2N", "opcode": "ADD", "available": False,
               "support_status": "unknown_instruction"}
    projected = apply_owner_admission(generic, "ADD", "FX2N", registry=registry)
    assert projected["available"] is False
    assert projected["owner_admission"]["inconsistent"] is True
    assert "owner_admits_call_without_runtime_form" in projected["owner_admission"]["reasons"]
    assert "st_signature_not_measured" in projected["owner_admission"]["reasons"]


def test_an_owner_record_only_speaks_for_its_own_model_and_literal_form():
    """Another CPU or another literal form must not carry the record over."""
    from plc.target_capabilities import resolve_target_applicability

    fx2n = resolve_target_applicability("ADD", "FX2N")
    fx3u = resolve_target_applicability("ADD", "FX3U")
    assert fx2n["available"] is False and fx3u["available"] is True
    assert fx3u["owner_admission"]["basis"]["target_model"] == "FX3U"
    # The unadmitted FX2N owner must not narrow the FX3U answer, and vice versa.
    assert fx3u["owner_admission"]["reasons"] == []

def test_the_materializer_revalidates_a_lane_carrying_an_older_projection():
    """A stale projection must not survive a call made with the current registry."""
    from plc.instruction_definition import materialize_instruction_definition
    from plc.instruction_resolution import resolve_instruction_lanes

    lanes = resolve_instruction_lanes("ADD", plc_model="FX2N")
    stale = {"target_applicability": {"target_model": "FX2N", "opcode": "ADD",
                                      "available": True, "support_status": "shared_catalog",
                                      "owner_admission": {"available": True, "reasons": [],
                                                          "basis": {"opcode": "ADD"}},
                                      "generic_definition_available": True}}
    assert lanes["target_applicability"]["available"] is False

    materialized = materialize_instruction_definition(
        {"opcode": "ADD"}, plc_model="FX2N", lanes=stale)
    assert materialized["applicability"]["available"] is False
    assert materialized["applicability"]["owner_admission"]["available"] is False

    # Repeating the same call with the same state is idempotent and keeps the
    # provenance of the first projection.
    again = materialize_instruction_definition(
        {"opcode": "ADD"}, plc_model="FX2N", lanes={"target_applicability":
                                                    materialized["applicability"]})
    assert again["applicability"]["available"] is False
    assert again["applicability"]["owner_admission"] == materialized["applicability"]["owner_admission"]
    assert again["applicability"]["owner_admission"].get("generic_definition_available") is True


def test_word_bit_references_preserve_selectors_and_reject_word_stimuli():
    from plc.validation import parse_device_reference, parse_device_address
    from plc.device_policy import stimulus_value, DevicePolicyError
    assert parse_device_address('D0.F', 'FX3U') == ('D', 0)
    assert parse_device_reference('D0.F', 'FX3U')['bit'] == 15
    assert stimulus_value('D0.0', 1, 'FX3U') == 1
    with pytest.raises(DevicePolicyError, match='0 or 1'):
        stimulus_value('D0.0', 1234, 'FX3U')


@given(st.integers(min_value=0, max_value=100), st.integers(min_value=0, max_value=15))
def test_word_bit_reference_keeps_number_and_bit(number, bit):
    from plc.validation import parse_device_reference
    parsed = parse_device_reference(f'D{number}.{bit:X}', 'FX3U')
    assert (parsed['number'], parsed['bit']) == (number, bit)


def test_unnumbered_index_spelling_uses_its_selected_cpu_profile():
    from plc.validation import parse_indexed_device_address
    assert parse_indexed_device_address('D0Z', 'FX0N')['index_text'] == 'Z'
    assert parse_indexed_device_address('D0Z0', 'FX0N') is None
    assert parse_indexed_device_address('D0Z', 'FX3U') is None
    assert parse_indexed_device_address('D0Z0', 'FX3U')['index_text'] == 'Z0'


def test_q_l_source_addition_has_destination_write_role_on_every_applicable_menu():
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.device_profiles import SUPPORTED_PLC_MODELS
    from plc.target_identity import target_family, is_parameter_only
    from plc.ir import analyze_instruction_access
    for cpu in SUPPORTED_PLC_MODELS:
        if target_family(cpu) in {'Q', 'L'} and not is_parameter_only(cpu):
            spec = registry.resolve_source_form('+', 3, cpu=cpu)
            assert spec is not None, cpu
            reads, writes = analyze_instruction_access('+', ['D0', 'D1', 'D2'], plc_model=cpu)
            assert 'D2' in writes and {'D0', 'D1'} <= set(reads), cpu


def test_pwm_channel_word_is_read_and_physical_output_is_written():
    from plc.ir import analyze_instruction_access
    for cpu in ('FX5U', 'FX5UC', 'FX5UJ', 'FX5S'):
        reads, writes = analyze_instruction_access('PWM', ['D10', 'D11', 'D0'], plc_model=cpu)
        assert 'D0' in reads and 'D0' not in writes
        _, writes = analyze_instruction_access('PWM', ['K10', 'K20', 'Y0'], plc_model=cpu)
        assert 'Y0' in writes
