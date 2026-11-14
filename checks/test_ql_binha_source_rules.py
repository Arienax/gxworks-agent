"""X535 source rows keep BINHA/BINHAP output devices distinct from input devices."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.fact_ql_behavior_review import _native_binha_output_device_range


PACKAGE = Path(r"D:\gxworks-agent-private\tmp\rag-gap-implementation-20261029\ql-binha-binhap-call-repair")
DESTINATION_PREFIXES = ["T", "ST", "C", "D", "W", "SD", "SW", "FD", "R", "ZR"]
SOURCE_WORD_PREFIXES = [*DESTINATION_PREFIXES, "Z"]
SOURCE_BIT_PREFIXES = ["X", "Y", "M", "L", "SM", "F", "B", "SB", "FX", "FY"]


def _spec(opcode: str):
    return {
        "dimension": "parameters.typed_operands",
        "value": {
            "native_form": {"operand_order": ["S", "D"]},
            "operands": [
                {"name": "S", "range": {"device_prefixes": list(SOURCE_WORD_PREFIXES),
                                           "digit_group_device_prefixes": list(SOURCE_BIT_PREFIXES),
                                           "literal_prefixes": ["K", "H"]}},
                {"name": "D", "range": {"device_prefixes": [*SOURCE_WORD_PREFIXES],
                                           "literal_prefixes": [], "address_forms": []}},
            ],
        },
        "scope": {"forms": [opcode], "languages": ["ladder"]},
    }


@pytest.mark.parametrize("opcode", ["BINHA", "BINHAP"])
def test_x535_binha_producer_removes_z_from_d_only(opcode):
    item = _spec(opcode)
    before_source = copy.deepcopy(item["value"]["operands"][0])
    section = SimpleNamespace(
        manual="sh080809engx",
        pages={535},
        _native_parameter_contracts=[{"allowed": [opcode], "order": ["S", "D"]}],
    )

    result = _native_binha_output_device_range(section, opcode, [item])

    assert result == [item]
    assert item["value"]["operands"][0] == before_source
    assert item["value"]["operands"][1]["range"]["device_prefixes"] == DESTINATION_PREFIXES


@pytest.mark.parametrize(
    "section,opcode",
    [
        (SimpleNamespace(manual="sh080809engp", pages={535}, _native_parameter_contracts=[]), "BINHA"),
        (SimpleNamespace(manual="sh080809engx", pages={536}, _native_parameter_contracts=[]), "BINHA"),
        (SimpleNamespace(manual="sh080809engx", pages={535}, _native_parameter_contracts=[]), "BINHA"),
        (SimpleNamespace(manual="sh080809engx", pages={535},
                         _native_parameter_contracts=[{"allowed": ["BINHA"], "order": ["D", "S"]}]), "BINHA"),
    ],
)
def test_x535_binha_producer_requires_exact_manual_page_and_order(section, opcode):
    item = _spec(opcode)
    before = copy.deepcopy(item)
    assert _native_binha_output_device_range(section, opcode, [item]) == [before]


def test_binha_package_preserves_all_owners_and_s_rules_with_exact_d_rules():
    payload = json.loads((PACKAGE / "source-facts.json").read_text(encoding="utf-8"))
    entries = payload["entries"]
    assert len(entries) == 106
    assert {opcode: sum(entry["opcode"] == opcode for entry in entries)
            for opcode in ("BINHA", "BINHAP")} == {"BINHA": 53, "BINHAP": 53}

    supported = {"BINHA": 0, "BINHAP": 0}
    for entry in entries:
        opcode, model = entry["opcode"], entry["target_model"]
        facts = entry["facts"]
        states = {
            fact["value"]["model_states"][model]
            for fact in facts
            if fact.get("dimension") == "constraints.cpu_availability"
            and model in fact.get("value", {}).get("model_states", {})
        }
        assert len(states) == 1, (model, opcode, states)
        typed = [fact for fact in facts
                 if fact.get("id") == "authored:ql:native_arguments_s_d:parameters.typed_operands"]
        if states == {"unsupported"}:
            assert not typed, (model, opcode, "unsupported owner must stay non-callable")
            continue
        assert states == {"supported"}
        supported[opcode] += 1
        assert len(typed) == 1, (model, opcode)
        fact = typed[0]
        assert fact["scope"] == {"forms": [opcode], "languages": ["ladder"], "models": [model]}
        assert any(source.get("manual_id") == "sh080809engx"
                   and source.get("revision") == "X"
                   and source.get("opcode") == opcode
                   and source.get("target_model") == model
                   and 535 in source.get("pdf_pages", [])
                   for source in fact.get("sources", []))
        source, destination = fact["value"]["operands"]
        assert source["name"] == "S" and destination["name"] == "D"
        assert source["range"]["device_prefixes"] == SOURCE_WORD_PREFIXES
        assert source["range"]["digit_group_device_prefixes"] == SOURCE_BIT_PREFIXES
        assert source["range"]["digit_groups_allowed"] == [1, 2, 3, 4]
        assert source["range"]["literal_prefixes"] == ["K", "H"]
        assert destination["range"]["device_prefixes"] == DESTINATION_PREFIXES
        assert destination["range"]["literal_prefixes"] == []
        assert destination["range"]["address_forms"] == []
        assert destination["word_device_layout"]["word_count"] == 2
        assert destination["conditional_tail_write"]["selector"] == "SM701"
        assert destination["conditional_tail_write"]["word_offset"] == 2
        ids = {item["id"] for item in facts}
        assert all(set(item.get("depends_on", ())) <= ids for item in facts), (model, opcode)
    assert supported == {"BINHA": 50, "BINHAP": 50}

    contracts = json.loads((PACKAGE / "contract-additions.json").read_text(encoding="utf-8"))["cpu_contracts"]
    matched = {"BINHA": 0, "BINHAP": 0}
    for contract in contracts:
        instruction = contract["instruction"]
        opcode = instruction["mnemonic"]
        if opcode not in matched:
            continue
        matched[opcode] += len(contract["cpus"])
        source, destination = instruction["operands"]
        assert source["device_prefixes"] == [*SOURCE_WORD_PREFIXES, *SOURCE_BIT_PREFIXES]
        assert destination["device_prefixes"] == DESTINATION_PREFIXES
    assert matched == {"BINHA": 50, "BINHAP": 50}
