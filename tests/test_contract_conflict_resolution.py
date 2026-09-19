from plc.specification.approach import (
    contract_definition_issues,
    normalize_generation_contract,
)


def test_explicit_required_opcode_overrides_inferred_forbidden_opcode():
    contract = normalize_generation_contract(
        {"required_opcodes": ["MOV"]},
        approach={"generation_guide": "禁止 MOV，使用其他方式"},
    )

    assert contract["required_opcodes"] == ["MOV"]
    assert "MOV" not in contract["forbidden_opcodes"]
    assert contract["source"] == "explicit"
    assert not contract.get("normalization_warnings")


def test_explicit_forbidden_opcode_overrides_inferred_required_opcode():
    contract = normalize_generation_contract(
        {"forbidden_opcodes": ["MOV"]},
        approach={"generation_guide": "使用 MOV 完成状态转移"},
    )

    assert contract["forbidden_opcodes"] == ["MOV"]
    assert "MOV" not in contract["required_opcodes"]


def test_explicit_self_conflict_remains_a_hard_definition_error():
    approach = {
        "name": "冲突方案",
        "generation_guide": "使用 MOV",
        "generation_contract": {
            "required_opcodes": ["MOV"],
            "forbidden_opcodes": ["MOV"],
        },
    }

    issues = contract_definition_issues(approach)

    assert any("同时被要求和禁止" in item and "MOV" in item for item in issues)


def test_inferred_self_conflict_is_neutralized_instead_of_blocking_confirmation():
    contract = normalize_generation_contract(
        None,
        approach={"generation_guide": "使用 MOV 完成转移；禁止 MOV"},
    )

    assert "MOV" not in contract["required_opcodes"]
    assert "MOV" not in contract["forbidden_opcodes"]
    assert any("已取消该歧义约束" in item for item in contract.get("normalization_warnings", []))


def test_any_of_group_prunes_explicitly_forbidden_candidates():
    contract = normalize_generation_contract(
        {
            "forbidden_opcodes": ["SET"],
            "any_of_opcode_groups": [["SET", "RST", "MOV"]],
        },
        approach={"generation_guide": ""},
    )

    assert contract["any_of_opcode_groups"] == [["RST", "MOV"]]


def test_any_of_group_is_removed_when_separately_required_member_satisfies_it():
    contract = normalize_generation_contract(
        {
            "required_opcodes": ["MOV"],
            "any_of_opcode_groups": [["MOV", "RST"]],
        },
        approach={"generation_guide": ""},
    )

    assert contract["any_of_opcode_groups"] == []


def test_explicit_any_of_group_fully_forbidden_is_unsatisfiable():
    approach = {
        "name": "不可满足方案",
        "generation_contract": {
            "forbidden_opcodes": ["SET", "RST"],
            "any_of_opcode_groups": [["SET", "RST"]],
        },
    }

    issues = contract_definition_issues(approach)

    assert any("任选组没有可用候选" in item for item in issues)


def test_explicit_any_of_group_wins_when_only_inferred_forbids_make_it_impossible():
    contract = normalize_generation_contract(
        {"any_of_opcode_groups": [["SET", "RST"]]},
        approach={"generation_guide": "禁止 SET；禁止 RST"},
    )

    assert contract["any_of_opcode_groups"] == [["SET", "RST"]]
    assert "SET" not in contract["forbidden_opcodes"]
    assert "RST" not in contract["forbidden_opcodes"]
    # No prose inference was applied, so there is no fabricated conflict warning.
    assert not contract.get("normalization_warnings")


def test_explicit_fields_can_coexist_with_inference_for_omitted_dimensions():
    contract = normalize_generation_contract(
        {"forbidden_opcodes": ["SET"]},
        approach={"generation_guide": "使用 D0 寄存器状态机并用 MOV 进行状态转移"},
    )

    assert contract["forbidden_opcodes"] == ["SET"]
    # A present partial contract is authoritative. Missing dimensions remain
    # empty; the selected guide is delivered separately as engineering context.
    assert contract["required_structures"] == []
    assert contract["required_opcodes"] == []
