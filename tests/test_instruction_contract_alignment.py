import json
import subprocess
import sys
from pathlib import Path

import knowledge_retriever_core as core
from application.field_repair import plan
from instruction_registry import DEFAULT_INSTRUCTION_REGISTRY, generation_app_instr_mnemonics


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "resources" / "instructions" / "mitsubishi"


def test_generated_verified_opcode_overlay_is_current():
    subprocess.run(
        [sys.executable, "tools/build_fx3u_verified_opcode_overlay.py", "--check"],
        cwd=ROOT,
        check=True,
    )


def test_verified_opcode_identity_does_not_harden_partial_signature():
    payload = json.loads((CATALOG / "fx3u_verified_opcodes.json").read_text(encoding="utf-8"))
    entries = {item["mnemonic"]: item for item in payload["instructions"]}
    assert len(entries) >= 80
    for opcode in ("ABS", "ALT", "MODBUS", "SQR", "ANR"):
        assert opcode in entries
        spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode)
        assert spec is not None
        assert spec.contract_level == "opcode_only"
        assert spec.min_operands is None
        assert spec.max_operands is None
        assert spec.operands == ()
        assert opcode in generation_app_instr_mnemonics("FX3U")

    # Manually verified contracts keep their stronger semantics.
    zrst = DEFAULT_INSTRUCTION_REGISTRY.resolve("ZRST")
    assert zrst is not None
    assert zrst.contract_level == "full"
    assert zrst.min_operands == zrst.max_operands == 2


def test_source_manual_reviewed_fx3u_contracts_are_generation_safe():
    allowed = set(generation_app_instr_mnemonics("FX3U"))
    expected = {
        "UNI": 3,
        "DIS": 3,
        "BK+": 4,
    }
    for opcode, arity in expected.items():
        spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode)
        assert spec is not None, opcode
        assert spec.contract_level == "full", opcode
        assert spec.min_operands == spec.max_operands == arity, opcode
        assert opcode in allowed, opcode

    # Manual review proved these are not ordinary generation opcodes:
    # BK is an extraction alias of BK+; FLDE is a truncation of FLDEL;
    # FLDEL is real but hardware/version gated by FX3U-CF-ADP.
    for opcode in ("BK", "FLDE", "FLDEL"):
        assert DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode) is None
        assert opcode not in allowed


def test_manual_reviewed_quarantine_reasons_are_explicit():
    payload = json.loads((CATALOG / "fx3u_instruction_quarantine.json").read_text(encoding="utf-8"))
    entries = {item["opcode"]: item for item in payload["entries"]}
    assert set(entries) == {"BK", "FLDE", "FLDEL"}

    assert entries["BK"]["reason"] == "manual_review_alias"
    assert entries["BK"]["correct_mnemonic"] == "BK+"
    assert entries["BK"]["manual"] == "JY997D16601"

    assert entries["FLDE"]["reason"] == "manual_review_alias"
    assert entries["FLDE"]["correct_mnemonic"] == "FLDEL"
    assert entries["FLDE"]["manual"] == "JY997D16601"

    assert entries["FLDEL"]["reason"] == "manual_verified_hardware_gated"
    assert entries["FLDEL"]["correct_mnemonic"] == "FLDEL"
    assert entries["FLDEL"]["manual"] == "JY997D35401"


def test_known_extraction_aliases_stay_quarantined():
    payload = json.loads((CATALOG / "fx3u_instruction_quarantine.json").read_text(encoding="utf-8"))
    quarantined = {item["opcode"] for item in payload["entries"]}
    assert "FLDE" in quarantined  # heading is FLDEL
    assert "BK" in quarantined    # heading is BK+, not bare BK
    assert DEFAULT_INSTRUCTION_REGISTRY.resolve("FLDE") is None
    assert DEFAULT_INSTRUCTION_REGISTRY.resolve("BK") is None


def test_field_repair_does_not_guess_from_opcode_only_contracts():
    base = {
        "device_comments": {},
        "rungs": [{
            "rung_id": 1,
            "header_element": None,
            "shared_inputs": [],
            "branches": [{
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [],
                "outputs": [{
                    "type": "APP_INSTR",
                    "opcode": "NOT_A_REAL_OPCODE",
                    "operands": ["D0", "D1"],
                    "label": None,
                }],
            }],
        }],
    }
    repair = plan(base, [{
        "path": "content$.rungs.0.branches.0.outputs.0.opcode",
        "reason": "invalid_ladder_structure",
    }], "FX3U")
    allowed = set(repair["target"]["value_schema"]["enum"])
    assert "MOV" in allowed
    assert "ABS" not in allowed
    assert "SQR" not in allowed


def _opcode_instruction_results(opcode):
    return [
        item for item in core.retrieve_knowledge(
            f"FX3U {opcode} 指令的操作数和适用软元件是什么？",
            plc_model="FX3U",
            task_type="generate",
            top_k=8,
            char_budget=12000,
        )
        if (
            str(item.get("instruction_opcode") or "").upper() == opcode
            and item.get("chunk_type") == "instruction"
        )
    ]


def test_exact_opcode_retrieval_uses_audited_manual_precedence():
    expected = {
        "DRVA": "fx3_positioning_k",
        "DRVI": "fx3_positioning_k",
        "DVIT": "fx3_positioning_k",
        "PLSV": "fx3_positioning_k",
        "ZRN": "fx3_positioning_k",
        "TBL": "fx3_programming_r",
    }
    for opcode, manual_id in expected.items():
        results = _opcode_instruction_results(opcode)
        assert len(results) == 1, (opcode, results)
        assert results[0]["manual_id"] == manual_id, (opcode, results[0])
