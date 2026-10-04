import pytest

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
