import json

import pytest
import subprocess
import sys
from pathlib import Path

from knowledge.structured_facts import resolve_instruction_records
from application.generation_context import _select_system_prompt
from plc.generation_contract import ladder_response_schema
from plc.ir import build_plc_ir
from plc.specification.approach import inspect_ladder_features
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, generation_app_instr_mnemonics
from plc.validation import APP_INSTR_WHITELIST, PLCJsonValidationError, validate_ladder_full


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "resources" / "instructions" / "mitsubishi"


def _app_ladder(opcode, operands):
    return {
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
                    "opcode": opcode,
                    "operands": list(operands),
                    "label": None,
                }],
            }],
        }],
    }


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


def test_manual_reviewed_opcodes_pass_the_full_ladder_validator():
    cases = (
        ("UNI", ["D0", "D10", "K4"]),
        ("DIS", ["D0", "D10", "K4"]),
        ("BK+", ["D0", "D10", "D20", "K4"]),
    )
    for opcode, operands in cases:
        ladder = _app_ladder(opcode, operands)
        assert validate_ladder_full(ladder, plc_model="FX3U") is ladder


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


def _opcode_instruction_results(opcode):
    return resolve_instruction_records(
        [{"opcode": opcode, "base_opcode": opcode}],
        plc_model="FX3U",
        task_type="generate",
    )


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


# Generation schema/prompt opcode exposure is owned here so the registry,
# validator and model-facing contract cannot drift independently.
def _opcode_rule(schema):
    full = schema["oneOf"][0] if "oneOf" in schema else schema
    return (
        full["properties"]["rungs"]["items"]["properties"]["branches"]["items"]
        ["properties"]["outputs"]["items"]["oneOf"][2]["properties"]["opcode"]
    )


def test_model_specific_app_instr_enum_matches_registry():
    fx3 = tuple(generation_app_instr_mnemonics("FX3U"))
    fx5 = tuple(generation_app_instr_mnemonics("FX5U"))
    assert fx3 and fx5
    assert _opcode_rule(ladder_response_schema(plc_model="FX3U"))["enum"] == list(fx3)
    assert _opcode_rule(ladder_response_schema(plc_model="FX5U"))["enum"] == list(fx5)
    assert set(fx3) <= APP_INSTR_WHITELIST
    assert set(fx5) <= APP_INSTR_WHITELIST
    for forbidden in ("OUT", "PLS", "PLF", "END", "NOT_A_REAL_OPCODE"):
        assert forbidden not in fx3
        assert forbidden not in fx5
    assert set(fx3) != set(fx5), "CPU-specific catalogue must constrain at least one opcode"


def test_normal_generation_prompt_exposes_only_catalogued_fx3u_opcodes():
    prompt = _select_system_prompt("ladder", plc_model="FX3U")
    rule = _opcode_rule(ladder_response_schema(plc_model="FX3U"))
    assert '"enum":[' in prompt
    assert "NOT_A_REAL_OPCODE" not in prompt
    assert "OUT" not in rule["enum"]
    assert len(rule["enum"]) >= 10


# The source corpus and the generation allowlist are different populations.
# Audit every admitted literal form, including pre-existing D/P variants.
def test_fx3u_batch_promotion_is_reproducible_and_does_not_promote_partial_rows():
    from tools.audit_fx3u_contracts import build, ledger_text, OUTPUT
    ledger, report = build()
    assert OUTPUT.read_text(encoding="utf-8") == ledger_text(ledger)
    assert {row["opcode"] for row in report["rows"]} == set(generation_app_instr_mnemonics("FX3U"))
    assert report["decisions"]["corroborated"] > 200
    assert report["after_exact_arity"] > report["before_exact_arity"]
    assert report["fully_verified_semantics"] == 0
    assert {"cpu_applicability", "hardware_applicability"} <= set(report["not_promoted"])
    assert report["not_promoted"]  # no blanket `full` based on an arity match
    assert report["quarantine"]
    for row in report["rows"]:
        spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(row["opcode"], cpu="FX3U")
        if row["decision"] == "corroborated":
            assert list(spec.native_operand_order) == row["native_order"]
            assert spec.min_operands == spec.max_operands == len(row["native_order"])
            assert spec.contract_sources
        else:
            assert not spec.verified_fields


def _promoted_fx3u_forms():
    return [op for op in generation_app_instr_mnemonics("FX3U")
            if "arity" in DEFAULT_INSTRUCTION_REGISTRY.resolve(op, cpu="FX3U").verified_fields]


@pytest.mark.parametrize("opcode", _promoted_fx3u_forms())
def test_every_promoted_form_uses_the_generic_validator_for_wrong_arity(opcode):
    from plc.validation import PLCJsonValidationError, validate_ladder_candidate_structure
    spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode, cpu="FX3U")
    assert spec.accepts_arity(spec.min_operands)
    assert not spec.accepts_arity(spec.min_operands + 1)
    # Failure must come from generic arity, not device/type or behavioral checks.
    with pytest.raises(PLCJsonValidationError, match="requires exactly"):
        validate_ladder_candidate_structure(_app_ladder(opcode, ["K0"] * (spec.min_operands + 1)), plc_model="FX3U")


def test_native_order_is_not_replaced_by_structured_st_argument_order():
    from tools.audit_fx3u_contracts import OUTPUT
    ledger = json.loads(OUTPUT.read_text())
    row = next(r for r in ledger["entries"] if "WSFL" in r["forms"])
    assert row["native_order"] == ["S", "D", "N1", "N2"]
    spec = DEFAULT_INSTRUCTION_REGISTRY.resolve("WSFL", cpu="FX3U")
    assert spec.native_operand_order == ("S", "D", "N1", "N2")
    assert "operand_types" not in spec.verified_fields
    assert "completion_ownership" not in spec.verified_fields
    # An invalid ledger is rejected before adding any partial override state.
    from plc.instructions import InstructionRegistry
    registry = InstructionRegistry.from_files([CATALOG / name for name in
        ("common.json", "fx3u.json", "fx5u.json", "fx3u_verified_opcodes.json", "modifier_rules.json")])
    assert registry.resolve("MOV", cpu="FX3U").min_operands is None


@pytest.mark.parametrize("opcode", ["MOV", "DMOVP", "WSFL", "PID", "DRVA", "DDRVA", "ANR"])
def test_fx3u_promotions_do_not_leak_into_fx5u_or_model_neutral_import(opcode):
    neutral = DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode)
    fx3 = DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode, cpu="FX3U")
    fx5 = DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode, cpu="FX5U")
    assert fx3.verified_fields and not neutral.verified_fields and not fx5.verified_fields
    assert fx5 is neutral
    assert fx3.operands == neutral.operands  # no guessed read/write semantics
    assert fx3.cpu_support == neutral.cpu_support


def test_selected_model_structured_contracts_expose_verified_syntax_and_real_gaps():
    from application.compact_protocol import compact_capability_prompt
    from knowledge.structured_facts import resolve_instruction_contract

    spec = {"selected_approach": {"generation_contract": {
        "required_opcodes": ["DRVA", "WSFL", "MOV"],
    }}}
    assert compact_capability_prompt("FX3U", spec) == ""
    rows = {
        opcode: resolve_instruction_contract({"opcode": opcode}, plc_model="FX3U")
        for opcode in ("DRVA", "WSFL", "MOV")
    }
    assert rows["DRVA"]["native_operand_order"] == ["S1", "S2", "D1", "D2"]
    assert rows["WSFL"]["native_operand_order"] == ["S", "D", "N1", "N2"]
    assert rows["MOV"]["min_operands"] == 2
    assert all(row["contract_level"] == "signature_verified" for row in rows.values())
    assert rows["DRVA"]["completion"] == {
        "device": "M8029",
        "placement": "same_rung_parallel_branch",
    }
    assert "completion_ownership" in rows["DRVA"]["verified_fields"]
    assert all(
        "completion_ownership" in rows[opcode]["unverified_fields"]
        for opcode in ("WSFL", "MOV")
    )


def test_instruction_template_icon_is_not_an_executable_mnemonic():
    # EADD in the template icon is not evidence for native 16-bit EADD.
    from tools.audit_fx3u_contracts import source_scan, DB
    _, native, _ = source_scan(DB)
    definition = native["EADD"][0]
    assert "EADD" in definition["format"]
    assert "EADD" not in definition["forms"] and "DEADD" in definition["forms"]
    assert not DEFAULT_INSTRUCTION_REGISTRY.resolve("EADD", cpu="FX3U").verified_fields


@pytest.mark.parametrize("mutation", ["arity", "duplicate", "missing_proof", "foreign_cpu", "bad_symbol", "bad_metadata"])
def test_promotion_ledger_is_validated_before_registry_mutation(tmp_path, mutation):
    from plc.instructions import InstructionRegistry
    from tools.audit_fx3u_contracts import OUTPUT
    registry = InstructionRegistry.from_files([CATALOG / name for name in
        ("common.json", "fx3u.json", "fx5u.json", "fx3u_verified_opcodes.json", "modifier_rules.json")])
    payload = json.loads(OUTPUT.read_text())
    if mutation == "arity":
        payload["entries"][-1]["arity"] += 1
    elif mutation == "duplicate":
        payload["entries"].append(payload["entries"][0])
    elif mutation == "missing_proof":
        payload["entries"][-1]["native_proof"] = ""
    elif mutation == "foreign_cpu":
        payload["cpu"] = "FX5U"
    elif mutation == "bad_symbol":
        payload["entries"][-1]["native_order"][0] = {}
    else:
        payload["entries"][-1]["execution_forms"] = []
    file = tmp_path / "invalid.json"
    file.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        registry.load_contract_promotions(file)
    assert not registry.resolve("MOV", cpu="FX3U").verified_fields


def test_mcp_and_compact_do_not_restore_duplicate_instruction_contract_lane(monkeypatch):
    from application.compact_protocol import compact_capability_prompt
    from agent_runtime.plc_tools import build_tool_context, build_default_tool_registry
    import knowledge.retriever as retriever

    monkeypatch.setattr(retriever, "build_knowledge_context", lambda *a, **k: "")
    spec = {"summary": "MOV WSFL", "selected_approach": {"name": "selected",
            "generation_contract": {"required_opcodes": ["MOV", "WSFL"]}}}
    context = build_tool_context({"id": "contract-test", "plc_model": "FX3U", "confirmed_spec": spec})
    result = build_default_tool_registry().call("get_generation_context", {}, context)
    assert result["ok"], result
    full = result["data"]["generation_instructions"]
    marker = "# Selected instruction capability snapshot\n"
    assert compact_capability_prompt("FX3U", spec) == ""
    assert marker not in full
    assert "signature_verified" not in full


@pytest.mark.parametrize("opcode,operands", [
    ("MOV", ["K1", "D0"]), ("DMOVP", ["K100000", "D10"]),
    ("BMOV", ["D0", "D10", "K2"]), ("FMOV", ["K0", "D10", "K2"]),
    ("ADD", ["D0", "K1", "D1"]), ("CMP", ["D0", "K1", "M10"]),
    ("SFTL", ["M100", "M200", "K8", "K1"]),
    ("WSFL", ["D100", "D200", "K8", "K1"]),
    ("FROM", ["K0", "K1", "D0", "K1"]), ("TO", ["K0", "K1", "D0", "K1"]),
    ("DRVI", ["K1000", "K2000", "Y0", "Y4"]),
    ("DRVA", ["K1000", "K2000", "Y0", "Y4"]),
    ("PLSY", ["K1000", "K500", "Y0"]), ("ZRN", ["K2000", "K500", "X0", "Y0"]),
])
def test_batch_promoted_signatures_keep_valid_native_ladder_shapes(opcode, operands):
    from plc.validation import validate_ladder_candidate_structure
    ladder = _app_ladder(opcode, operands)
    assert validate_ladder_candidate_structure(ladder, plc_model="FX3U") is ladder


@pytest.mark.parametrize("damage", ["missing_independent", "conflicting_count", "missing_native_form", "conflicting_native"])
def test_auditor_does_not_promote_ambiguous_or_partial_source_evidence(monkeypatch, damage):
    import copy
    import tools.audit_fx3u_contracts as audit
    locks, natives, signatures = audit.source_scan(audit.DB)
    # Isolated source-shaped copies; the bundled database remains read-only.
    native = copy.deepcopy(natives["WSFL"][0])
    candidates = {op: copy.deepcopy(rows) for op, rows in signatures.items() if op in native["forms"]}
    native_rows = {"WSFL": [native]}
    if damage == "missing_independent":
        candidates = {}
    elif damage == "conflicting_count":
        for rows in candidates.values():
            for row in rows: row["symbols"] = row["symbols"][:-1]
    elif damage == "missing_native_form":
        native["forms"].pop("WSFL")
    else:
        other = copy.deepcopy(native)
        other["symbols"] = other["symbols"][:-1]
        native_rows["WSFL"].append(other)
    monkeypatch.setattr(audit, "source_scan", lambda db: (locks, native_rows, candidates))
    ledger, report = audit.build()
    row = next(row for row in report["rows"] if row["opcode"] == "WSFL")
    assert row["decision"] != "corroborated" and not row["verified_fields"]
    assert not any("WSFL" in row["forms"] for row in ledger["entries"])


def test_complete_native_symbols_are_required_not_partial_structured_operands():
    import sqlite3
    from tools.audit_fx3u_contracts import DB, NATIVE, native_definition, source_scan
    _, native, _ = source_scan(DB)
    page_number = native["WSFL"][0]["page"]
    with sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        page = dict(db.execute("SELECT * FROM page_artifacts WHERE manual_id=? AND pdf_page=?", (NATIVE,page_number)).fetchone())
        tables = [dict(row) for row in db.execute("SELECT * FROM tables WHERE manual_id=? AND pdf_page=?", (NATIVE,page_number))]
    assert native_definition(page, tables)["symbols"] == ["S", "D", "N1", "N2"]
    words = json.loads(page["word_geometry_json"])
    page["word_geometry_json"] = json.dumps([word for word in words if word["text"] != "n2"])
    assert native_definition(page, tables) is None


@pytest.mark.parametrize("flag", ["--output", "--report"])
def test_contract_audit_cannot_overwrite_its_source_database(tmp_path, monkeypatch, flag):
    import tools.audit_fx3u_contracts as audit
    database = tmp_path / "evidence.sqlite"
    database.write_bytes(b"source evidence")
    monkeypatch.setattr(audit, "build", lambda *a: pytest.fail("No source scan or promotion after path conflict"))
    args = ["--database", str(database), flag, str(database)]
    if flag == "--output":
        args.append("--promote")
    with pytest.raises(SystemExit) as error:
        audit.main(args)
    assert error.value.code == 2
    assert database.read_bytes() == b"source evidence"


# Model-specific instruction regressions belong to the registry/validator alignment owner.
def test_fx3u_registry_exposes_zrst_with_exact_two_operand_arity():
    zrst = DEFAULT_INSTRUCTION_REGISTRY.resolve("ZRST")
    assert zrst is not None
    assert zrst.supports_cpu("FX3U")
    assert zrst.accepts_arity(2)
    assert not zrst.accepts_arity(1)
    assert not zrst.accepts_arity(16)
    assert "ZRST" in generation_app_instr_mnemonics("FX3U")


def test_fx3u_zrst_m100_m115_is_a_valid_generated_instruction():
    ladder = _app_ladder("ZRST", ["M100", "M115"])
    assert validate_ladder_full(ladder, plc_model="FX3U") is ladder


def test_rst_does_not_masquerade_as_two_operand_zone_reset():
    rst = DEFAULT_INSTRUCTION_REGISTRY.resolve("RST")
    assert rst is not None
    assert rst.accepts_arity(1)
    assert not rst.accepts_arity(2)
    with pytest.raises(PLCJsonValidationError, match="RST requires exactly 1 operands"):
        validate_ladder_full(_app_ladder("RST", ["M100", "M115"]), plc_model="FX3U")


def _analog_ladder(opcode, operands):
    return {
        "device_comments": {},
        "rungs": [
            {
                "rung_id": 1,
                "debug_note": "模拟量传送",
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [
                            {"type": "NO", "address": "M8000", "label": "运行"}
                        ],
                        "outputs": [
                            {
                                "type": "APP_INSTR",
                                "opcode": opcode,
                                "operands": operands,
                                "label": "模拟量读写",
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_wr3a_is_a_supported_instruction_and_reads_its_source_operand():
    ladder = _analog_ladder("WR3A", ["K0", "K21", "D200"])

    assert validate_ladder_full(ladder, "FX3U") is ladder
    program = build_plc_ir(ladder, plc_model="FX3U")

    assert program["networks"][0]["reads"] == ["M8000", "D200"]
    assert program["networks"][0]["writes"] == []
    assert "analog_control" in inspect_ladder_features(ladder)["structures"]


def test_rd3a_third_operand_is_the_local_write_destination():
    ladder = _analog_ladder("RD3A", ["K0", "K21", "D100"])

    assert validate_ladder_full(ladder, "FX3U") is ladder
    program = build_plc_ir(ladder, plc_model="FX3U")

    assert program["networks"][0]["reads"] == ["M8000"]
    assert program["networks"][0]["writes"] == ["D100"]


@pytest.mark.parametrize(
    ("opcode", "operands"),
    (("RD3A", ["K0", "K21"]), ("WR3A", ["K0", "K21", "D0", "K1"])),
)
def test_rd3a_wr3a_require_the_documented_three_operands(opcode, operands):
    with pytest.raises(PLCJsonValidationError, match="requires exactly 3 operands"):
        validate_ladder_full(_analog_ladder(opcode, operands), "FX3U")


@pytest.mark.parametrize(
    ("opcode", "module"),
    (("RD3A", "FX3U-4AD-ADP"), ("WR3A", "FX3U-4DA-ADP")),
)
def test_rd3a_wr3a_report_precise_confirmed_module_mismatch(opcode, module):
    operands = ["K0", "K21", "D100"]
    confirmed = {
        "hardware_context": {
            "analog_module": {
                "input_module" if opcode == "RD3A" else "output_module": module,
                "input_channel" if opcode == "RD3A" else "output_channel": 1,
            }
        }
    }

    with pytest.raises(
        PLCJsonValidationError,
        match=r"valid instruction.*cannot access confirmed",
    ):
        validate_ladder_full(_analog_ladder(opcode, operands), "FX3U", confirmed)
