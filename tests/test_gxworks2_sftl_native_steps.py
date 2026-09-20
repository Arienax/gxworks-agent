"""All instruction step widths and CSV accumulation.

The historical filename is retained as the existing tests/README.md owner.
This is no longer an SFTL-only regression: it covers the entire width catalogue,
operand-dependent rules, unknowns and the actual CSV export boundary.
"""
import csv
import json
from pathlib import Path

import pytest

from plc.instruction_steps import (
    StepCursor, StepWidth, StepWidthCatalog, default_step_width_catalog,
    instruction_step_width, native_token_encodings, stored_header_width,
)


CASES = [
    ("SFTL", "M10 M100 K56 K1", 9),
    ("SFTLP", "M10 M100 K56 K1", 9),
    ("SFTR", "M10 M100 K56 K1", 9),
    ("SFTRP", "M10 M100 K56 K1", 9),
    ("WSFL", "D0 D10 K56 K1", 9),
    ("WSFLP", "D0 D10 K56 K1", 9),
    ("WSFR", "D0 D10 K56 K1", 9),
    ("WSFRP", "D0 D10 K56 K1", 9),
    ("MOV", "K1 D0", 5), ("MOVP", "K1 D0", 5),
    ("DMOV", "K100000 D0", 9), ("DMOVP", "K100000 D0", 9),
    ("ADD", "D0 D1 D2", 7), ("ADDP", "D0 D1 D2", 7),
    ("DADD", "D0 D2 D4", 13), ("DADDP", "D0 D2 D4", 13),
    ("SUB", "D0 D1 D2", 7), ("MUL", "D0 D1 D2", 7),
    ("DIV", "D0 D1 D2", 7), ("DDIV", "D0 D2 D4", 13),
    ("CMP", "D0 K1 M10", 7), ("ZCP", "K0 K5 D0 M10", 9),
    ("DCMP", "D0 D2 M10", 13), ("DZCP", "D0 D2 D4 M10", 17),
    ("BMOV", "D0 D10 K4", 7), ("FMOVP", "K0 D10 K56", 7),
    ("PLSY", "K100 K20 Y0", 7), ("PLSR", "K100 K20 K10 Y0", 9),
    ("DRVI", "K100 K20 Y0 Y1", 9), ("DDRVI", "K100 K20 Y0 Y1", 17),
    ("FROM", "K0 K10 D0 K1", 9), ("DFROM", "K0 K10 D0 K1", 17),
    ("TO", "K0 K10 D0 K1", 9), ("DTO", "K0 K10 D0 K1", 17),
    ("INC", "D0", 3), ("DEC", "D0", 3), ("DINCP", "D0", 5),
    ("PID", "D0 D2 D10 D100", 9), ("PLS", "M1", 2),
    ("OR<", "D0 K1", 5), ("OR<=", "D0 K1", 5), ("OR<>", "D0 K1", 5),
    ("LDD=", "D0 D2", 9), ("ANDD<>", "D0 D2", 9), ("ORD<=", "D0 D2", 9),
    ("RST", "D10", 3), ("RST", "T16", 2), ("RST", "M2", 1),
    ("RST", "M8123", 2), ("SET", "M8161", 2), ("SET", "M10", 1),
    ("OUT", "T0 K1", 3), ("OUT", "C10 K3", 3), ("OUT", "Y0", 1),
    ("OUT", "T10Z0 K10", 4), ("LD", "M10Z0", 3), ("RST", "D10.F", 3),
    ("LD", "M8000", 1), ("LDP", "M8002", 2), ("END", "", 1),
]


@pytest.mark.parametrize("opcode,operands,expected", CASES)
def test_catalogue_independent_width_regressions(opcode, operands, expected):
    result = instruction_step_width(opcode, operands.split())
    assert result.steps == expected
    assert result.known and result.evidence


def test_catalogue_every_fixed_native_form_and_every_variant_is_resolved():
    catalogue = default_step_width_catalog()
    forms = catalogue.fixed_forms()
    assert len(forms) > 400
    for opcode, (width, arity) in forms.items():
        # Storage-width projection only, not an assertion that these sample
        # operands implement valid instruction semantics or a valid program.
        result = catalogue.resolve(opcode, [f"D{2 * i}" for i in range(arity)])
        assert result.steps == width, (opcode, result)
        assert result.source == "native_observation"


def test_catalogue_aliases_and_device_zero_padding():
    assert instruction_step_width(" movp ", [" k1 ", " d0001 "]).steps == 5
    assert instruction_step_width("OUT", ["t000", "k1"]).steps == 3
    assert instruction_step_width("ANDI", ["x001"]).steps == 1
    assert instruction_step_width("WSFL", ["D0", "D10", "K56", "K1"], plc_model="FX3UC").steps == 9


@pytest.mark.parametrize("opcode,operands", [
    ("NOT_A_REAL_OPCODE", ["D0"]), ("WSFL", ["D0", "D10"]),
    ("$MOV", ['"abc"', "D10"]), ("$MOVP", ['"abc"', "D10"]),
    ("MOV", ["D10Z0", "D100"]), ("OUT", ["T10Z0"]),
    ("OUT", ["C200", "K3"]), ("RST", ["D10Z0"]),
])
def test_catalogue_unknown_never_becomes_one_step(opcode, operands):
    result = instruction_step_width(opcode, operands)
    assert result.steps is None and not result.known and result.reason


def test_catalogue_other_cpu_does_not_inherit_fx3u_widths():
    assert instruction_step_width("ADD", ["D0", "D1", "D2"], plc_model="FX5U").steps is None


def test_catalogue_conflicting_native_widths_remain_unknown():
    c = StepWidthCatalog({"054c050005": ("MOV", 2), "054c070005": ("MOV", 2)},
                         {"models": ["FX3U"], "rules": []})
    assert c.resolve("MOV", ["D0", "D10"]).steps is None


def test_catalogue_header_width_is_not_operand_count_or_execution_validity():
    assert stored_header_width(bytes.fromhex("0551090905")) == 9
    assert native_token_encodings()["0551090905"] == ("WSFL", 4)
    # Even an incorrect positive width must remain a *stored* observation.
    assert stored_header_width(bytes.fromhex("0551070905")) == 7
    assert instruction_step_width("WSFL", ["D0", "D10", "K56", "K1"]).steps == 9
    assert stored_header_width(bytes.fromhex("0551000905")) is None
    assert stored_header_width(bytes.fromhex("0551ff0905")) is None


def test_catalogue_cursor_preserves_known_prefix_without_guessing_after_gap():
    cursor = StepCursor()
    assert cursor.label == "0"
    cursor.advance(StepWidth(9, "test"))
    assert cursor.label == "9"
    cursor.advance(StepWidth(None, "unknown"))
    cursor.advance(StepWidth(7, "test"))
    assert cursor.step is None and cursor.label == ""


def _instruction_rows(path):
    with path.open("r", encoding="utf-16", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    return [row for row in rows[3:] if len(row) >= 4 and str(row[2] or "").strip()]


def _ladder(*instructions):
    return {"device_comments": {}, "rungs": [
        {"rung_id": 0, "header_element": None, "shared_inputs": [], "branches": [
            {"inputs": [{"type": "NO", "address": "X0"}], "outputs": [
                {"type": "APP_INSTR", "opcode": opcode, "operands": list(args)}
                for opcode, args in instructions]}]}]}


def test_fx3u_sftl_uses_nine_native_program_steps(tmp_path):
    from gxworks2.csv_export import generate_gx_works2_csv
    program, comments = tmp_path / "program.csv", tmp_path / "comments.csv"
    ladder = _ladder(("SFTL", ["M10", "M100", "K128", "K1"]))
    ladder["rungs"].append({"rung_id": 1, "branches": [
        {"inputs": [{"type": "NO", "address": "X1"}],
         "outputs": [{"type": "COIL", "address": "Y0"}]}]})
    assert generate_gx_works2_csv(ladder, program, comments)
    assert [(row[0], row[2], row[3]) for row in _instruction_rows(program)][:4] == [
        ("0", "LD", "X000"), ("1", "SFTL", "M10 M100 K128 K1"),
        ("10", "LD", "X001"), ("11", "OUT", "Y000")]


def test_export_mixed_variants_get_exact_cumulative_steps_once(tmp_path):
    from gxworks2.csv_export import generate_gx_works2_csv
    program, comments = tmp_path / "program.csv", tmp_path / "comments.csv"
    ladder = _ladder(("MOVP", ["K1", "D0"]), ("ADD", ["D0", "K1", "D1"]),
                     ("WSFL", ["D0", "D10", "K56", "K1"]),
                     ("DADD", ["D0", "D2", "D4"]), ("RST", ["D10"]))
    assert generate_gx_works2_csv(ladder, program, comments)
    assert [(row[2], int(row[0])) for row in _instruction_rows(program)] == [
        ("LD", 0), ("MOVP", 1), ("ADD", 6), ("WSFL", 13),
        ("DADD", 22), ("RST", 35), ("END", 38)]
    first = program.read_bytes()
    assert generate_gx_works2_csv(ladder, program, comments)
    assert program.read_bytes() == first


def test_export_unknown_width_is_nonblocking_and_keeps_instruction(tmp_path, caplog):
    from gxworks2.csv_export import generate_gx_works2_csv
    from gxworks2.csv_manager import CSVManager
    program, comments = tmp_path / "program.csv", tmp_path / "comments.csv"
    findings = []
    ladder = _ladder(("CUSTOM_VENDOR_OP", ["D0"]), ("MOV", ["K1", "D1"]))
    assert generate_gx_works2_csv(ladder, program, comments, step_diagnostics=findings)
    rows = _instruction_rows(program)
    assert [(row[2], row[0]) for row in rows] == [
        ("LD", "0"), ("CUSTOM_VENDOR_OP", "1"), ("MOV", ""), ("END", "")]
    assert rows[1][3] == "D0"
    assert findings[0]["opcode"] == "CUSTOM_VENDOR_OP"
    assert "unresolved" in caplog.text
    # This asserts the application's existing format contract, not GX compile.
    assert CSVManager().validate(program).valid


def test_export_catalogue_applies_to_compare_inputs_not_only_app_instructions(tmp_path):
    from gxworks2.csv_export import generate_gx_works2_csv
    ladder = {"rungs": [{"branches": [{"inputs": [{"type": "parallel_block", "branches": [
        [{"type": "NO", "address": "X0"}],
        [{"type": "COMPARE", "expression": "< D0 K1"}],
        [{"type": "COMPARE", "expression": "<= D0 K2"}],
        [{"type": "COMPARE", "expression": "<> D0 K3"}]
    ]}], "outputs": [{"type": "COIL", "address": "Y0"}]}]}]}
    program, comments = tmp_path / "program.csv", tmp_path / "comments.csv"
    assert generate_gx_works2_csv(ladder, program, comments)
    assert [(row[2], int(row[0])) for row in _instruction_rows(program)] == [
        ("LD", 0), ("OR<", 1), ("OR<=", 6), ("OR<>", 11), ("OUT", 16), ("END", 17)]


def test_export_generation_opcode_policy_is_not_expanded_by_native_evidence():
    from plc.instructions import generation_app_instr_mnemonics
    # This layer owns sizes only. Reading the table must not mutate policy.
    before = generation_app_instr_mnemonics("FX3U")
    default_step_width_catalog().fixed_forms()
    assert generation_app_instr_mnemonics("FX3U") == before


def test_export_gxw_decoder_uses_same_source_without_repairing_stored_width():
    from gxw.token_pou import _NATIVE_ENCODINGS, LadderToken
    assert _NATIVE_ENCODINGS is native_token_encodings()
    raw = bytes.fromhex("0551070905")
    assert LadderToken(0, raw).annotation()["mnemonic"] == "WSFL"
    assert stored_header_width(raw) == 7
    assert instruction_step_width("WSFL", ["D0", "D10", "K56", "K1"]).steps == 9


def test_export_existing_native_csv_width_fixtures():
    path = Path(__file__).parent / "fixtures/gxw_token_native_20260919.json"
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    checked = 0
    for case in cases:
        result = instruction_step_width(case["opcode"], case["operands"])
        if result.known:
            assert result.steps == case["step_width"], case
            checked += 1
    assert checked > 20


def test_export_every_generation_form_with_known_fixed_width(tmp_path):
    from gxworks2.csv_export import generate_gx_works2_csv
    from plc.instructions import generation_app_instr_mnemonics
    forms = default_step_width_catalog().fixed_forms()
    program, comments = tmp_path / "program.csv", tmp_path / "comments.csv"
    checked = 0
    for opcode in generation_app_instr_mnemonics("FX3U"):
        if opcode not in forms:
            continue
        width, arity = forms[opcode]
        # Exercise the real serializer, not just the resolver. Dummy direct
        # operands test storage widths, not instruction execution or safety.
        ladder = _ladder((opcode, [f"D{2 * i}" for i in range(arity)]))
        assert generate_gx_works2_csv(ladder, program, comments)
        rows = _instruction_rows(program)
        assert rows[1][0] == "1", opcode
        assert rows[-1][2] == "END", opcode
        assert rows[-1][0] == str(1 + width), opcode
        checked += 1
    assert checked > 150


def test_export_keeps_explicit_cpu_and_does_not_guess_fx5u_steps(tmp_path):
    from gxworks2.csv_export import generate_gx_works2_csv, _step_width_model
    assert _step_width_model({"kind": "plc_program_ir", "plc": {"cpu": "FX5U"}}, None) == "FX5U"
    ladder = _ladder(("ADD", ["D0", "D1", "D2"]))
    ladder["plc_model"] = "FX5U"
    program, comments = tmp_path / "program.csv", tmp_path / "comments.csv"
    findings = []
    assert generate_gx_works2_csv(ladder, program, comments, step_diagnostics=findings)
    assert findings and all("FX5U" in item["reason"] for item in findings)
    assert [row[0] for row in _instruction_rows(program)] == ["0", "", ""]
    assert generate_gx_works2_csv(ladder, program, comments, plc_model="FX3U")
    assert [row[0] for row in _instruction_rows(program)] == ["0", "1", "8"]
