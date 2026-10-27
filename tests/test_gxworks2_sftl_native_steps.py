"""All instruction step widths and CSV accumulation.

The historical filename is retained as the existing tests/README.md owner.
This is no longer an SFTL-only regression: it covers the entire width catalogue,
operand-dependent rules, unknowns and the actual CSV export boundary.
"""

from tools.offline_inputs import private_path
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
    ("LD", "M8000", 1), ("LDP", "M8002", 2), ("ANI", "M8340", 2), ("END", "", 1),
]


@pytest.mark.parametrize("opcode,operands,expected", CASES)
def test_catalogue_independent_width_regressions(opcode, operands, expected):
    result = instruction_step_width(opcode, operands.split(), plc_model="FX3U")
    assert result.steps == expected
    assert result.known and result.evidence


def test_catalogue_every_observed_native_form_and_every_variant_is_resolved():
    catalogue = default_step_width_catalog()
    forms = catalogue.observed_forms()
    assert len(forms) > 400
    for opcode, (width, arity) in forms.items():
        # Storage-width projection only, not an assertion that these sample
        # operands implement valid instruction semantics or a valid program.
        result = catalogue.resolve(opcode, [f"D{2 * i}" for i in range(arity)], plc_model="FX3U")
        assert result.steps == width, (opcode, result)
        assert result.source == "native_observation"


def test_catalogue_exact_native_basic_header_variant_precedes_broad_contact_rule():
    result = instruction_step_width("ANI", ["M8340"], plc_model="FX3U")
    assert result.steps == 2
    assert result.source == "exact_native_form"
    assert result.evidence and "040d0204" in result.evidence[0]
    assert instruction_step_width("ANI", ["X1"], plc_model="FX3U").steps == 1


def test_catalogue_aliases_and_device_zero_padding():
    assert instruction_step_width(" movp ", [" k1 ", " d0001 "], plc_model="FX3U").steps == 5
    assert instruction_step_width("OUT", ["t000", "k1"], plc_model="FX3U").steps == 3
    assert instruction_step_width("ANDI", ["x001"], plc_model="FX3U").steps == 1
    assert instruction_step_width("WSFL", ["D0", "D10", "K56", "K1"], plc_model="FX3UC").steps == 9
    # An index changes the operand encoding, not the number of stored words: the
    # applied instruction keeps its width and an indexed contact takes the
    # third word the indexed-device rule states.
    assert instruction_step_width("MOV", ["D10Z0", "D100"], plc_model="FX3U").steps == 5
    assert instruction_step_width("OUT", ["T10Z0"], plc_model="FX3U").steps == 3


@pytest.mark.parametrize("opcode,operands", [
    ("NOT_A_REAL_OPCODE", ["D0"]), ("WSFL", ["D0", "D10"]),
    ("$MOV", ['"abc"', "D10"]), ("$MOVP", ['"abc"', "D10"]),
    ("OUT", ["C300", "K3"]), ("RST", ["D10Z0"]),
])
def test_catalogue_unknown_never_becomes_one_step(opcode, operands):
    result = instruction_step_width(opcode, operands, plc_model="FX3U")
    assert result.steps is None and not result.known and result.reason


def test_catalogue_other_cpu_does_not_inherit_fx3u_widths():
    assert instruction_step_width("ADD", ["D0", "D1", "D2"], plc_model="FX5U").steps is None


def test_catalogue_conflicting_native_widths_remain_unknown():
    c = StepWidthCatalog({"054c050005": ("MOV", 2), "054c070005": ("MOV", 2)},
                         {"models": ["FX3U"], "rules": []})
    assert c.resolve("MOV", ["D0", "D10"], plc_model="FX3U").steps is None


def test_catalogue_header_width_is_not_operand_count_or_execution_validity():
    assert stored_header_width(bytes.fromhex("0551090905")) == 9
    assert native_token_encodings()["0551090905"] == ("WSFL", 4)
    # Even an incorrect positive width must remain a *stored* observation.
    assert stored_header_width(bytes.fromhex("0551070905")) == 7
    assert instruction_step_width("WSFL", ["D0", "D10", "K56", "K1"], plc_model="FX3U").steps == 9
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
    default_step_width_catalog().observed_forms()
    assert generation_app_instr_mnemonics("FX3U") == before


def test_export_gxw_decoder_uses_same_source_without_repairing_stored_width():
    from gxw.token_pou import _NATIVE_ENCODINGS, LadderToken
    assert _NATIVE_ENCODINGS is native_token_encodings()
    raw = bytes.fromhex("0551070905")
    assert LadderToken(0, raw).annotation()["mnemonic"] == "WSFL"
    assert stored_header_width(raw) == 7
    assert instruction_step_width("WSFL", ["D0", "D10", "K56", "K1"], plc_model="FX3U").steps == 9


def test_export_existing_native_csv_width_fixtures():
    path = private_path('tests/fixtures/gxw_token_native_20260919.json')
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    checked = 0
    for case in cases:
        result = instruction_step_width(case["opcode"], case["operands"], plc_model="FX3U")
        if result.known:
            assert result.steps == case["step_width"], case
            checked += 1
    assert checked > 20


def test_export_every_generation_form_with_an_observed_width(tmp_path):
    from gxworks2.csv_export import generate_gx_works2_csv
    from plc.instructions import generation_app_instr_mnemonics
    forms = default_step_width_catalog().observed_forms()
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


DIRECT_STATE_RELAYS = [("S0", 2), ("S1", 2), ("S1023", 2), ("S1024", 2),
                       ("S2000", 2), ("S4095", 2)]


@pytest.mark.parametrize("opcode", ["OUT", "SET", "RST"])
@pytest.mark.parametrize("operand,expected", DIRECT_STATE_RELAYS)
def test_direct_state_relay_targets_use_the_manual_two_step_width(opcode, operand, expected):
    # JY997D16601-R 7.16: OUT/SET/RST with a direct S0-S4095 target take two steps.
    result = instruction_step_width(opcode, [operand], plc_model="FX3U")
    assert (result.steps, result.status, result.source) == (expected, "known", "device_range")
    assert result.evidence and "7.16" in result.evidence[0]
    # Case and leading zeros are spellings, not different storage.
    assert instruction_step_width(opcode, [operand.lower()], plc_model="FX3UC").steps == expected
    padded = "S0" + operand[1:]
    assert instruction_step_width(opcode, [padded], plc_model="FX3U").steps == expected


def test_state_relay_width_rule_does_not_capture_other_targets():
    assert instruction_step_width("SET", ["M0"], plc_model="FX3U").steps == 1
    assert instruction_step_width("OUT", ["Y000"], plc_model="FX3U").steps == 1
    # An indexed state relay is a different encoding and keeps its own width.
    assert instruction_step_width("SET", ["S100Z0"], plc_model="FX3U").steps == 3
    assert instruction_step_width("RST", ["D10"], plc_model="FX3U").steps == 3


def test_merged_menu_target_requires_every_member_to_agree():
    for model in ("FX3U", "FX3UC", "FX3U/FX3UC"):
        assert instruction_step_width("SET", ["S100"], plc_model=model).steps == 2
    merged = instruction_step_width("SET", ["S100"], plc_model="FX3U/FX3UC")
    assert merged.source == "merged_members"
    assert [cpu for cpu, _member in merged.members] == ["FX3U", "FX3UC"]
    assert all(member.steps == 2 for _cpu, member in merged.members)
    for model in ("L02", "L02-P", "L02/L02-P"):
        assert instruction_step_width("LD", ["M0"], plc_model=model).steps == 1
        assert instruction_step_width("MOV", [], plc_model=model).status == "requires_operands"


def test_merged_members_that_disagree_or_are_uncovered_stay_unresolved():
    from plc.instruction_steps import _known, _merge_members, _unresolved

    first, second = _known(2, "operand_rule"), _known(3, "operand_rule")
    conflict = _merge_members((("FX3U", first), ("FX3UC", second)))
    assert conflict.steps is None and conflict.status == "conflict"
    assert "name the exact CPU" in conflict.reason
    uncovered = _merge_members((("FX3U", first), ("FX3UC", _unresolved("no_profile", "no rules"))))
    assert uncovered.steps is None and uncovered.status == "no_profile"


def test_other_fx_models_keep_their_identity_and_state_the_rule_scope():
    exact = instruction_step_width("SET", ["S100"], plc_model="FX3U")
    assert exact.scope == "exact"
    for model in ("FX1N", "FX2N/FX2NC", "FX3G", "FX0N"):
        family = instruction_step_width("SET", ["S100"], plc_model=model)
        assert (family.steps, family.scope) == (2, "family")
        assert any("FX3U/FX3UC" in item for item in family.evidence)
    fx5u = instruction_step_width("SET", ["S100"], plc_model="FX5U")
    assert fx5u.steps is None and fx5u.status == "no_profile"
    assert "not borrowed" in fx5u.reason


@pytest.mark.parametrize("model", [None, "", "   "])
def test_missing_target_is_reported_instead_of_defaulting_to_fx3u(model):
    result = instruction_step_width("MOV", ["K1", "D0"], plc_model=model)
    assert result.steps is None and result.status == "unknown_cpu"
    assert result.requirements == ("plc_model",)
    assert "saved CPU identity" in result.reason


def test_unregistered_cpu_identity_is_not_an_fx3u_width():
    result = instruction_step_width("MOV", ["K1", "D0"], plc_model="Q99")
    assert result.steps is None and result.status == "unknown_cpu"


@pytest.mark.parametrize("model,opcode,operands,expected", [
    ("Q03UDV", "MOV", ["D0", "D10"], 2),
    ("Q03UDV", "LD", ["M0"], 1),
    ("Q03UDV", "END", [], 2),
    ("Q02/Q02H", "MOV", ["D0", "D10"], 2),
    ("Q00J", "MOV", ["D0", "D10"], 2),
    ("Q02PH", "MOV", ["D0", "D10"], 2),
    ("L02/L02-P", "LD", ["M0"], 1),
    ("L26-BT/L26-PBT", "LD", ["M0"], 1),
])
def test_q_l_targets_answer_through_the_shared_interface(model, opcode, operands, expected):
    # A Q/L width of a D/W form depends on the saved allocation, so the caller
    # states that it has none instead of inheriting an unextended assumption.
    result = instruction_step_width(opcode, operands, plc_model=model,
                                    assume_default_allocation=True)
    assert (result.steps, result.status) == (expected, "known")
    answers = list(result.members) or [(model, result)]
    for cpu, answer in answers:
        assert answer.source == "native_stored_width_algorithm"
        assert answer.steps == expected
        assert answer.evidence and cpu in answer.evidence[0]


def test_q_l_width_states_distinguish_missing_operands_from_missing_context():
    from gxw.token_q_encoder import q_project_storage_context

    assert instruction_step_width("MOV", [], plc_model="Q03UDV").status == "requires_operands"
    unclear = q_project_storage_context(None, cpu="Q03UDV")
    result = instruction_step_width("MOV", ["D0", "D10"], plc_model="Q03UDV", context=unclear)
    assert result.steps is None and result.status == "requires_context"
    assert result.requirements == ("storage_context",)
    # Only the D/W forms need that allocation; a bit contact does not.
    assert instruction_step_width("LD", ["M0"], plc_model="Q03UDV", context=unclear).steps == 1


def test_q_l_allocation_context_has_three_states():
    from gxw.token_q_encoder import QStorageContext

    # Unknown: no saved project allocation was supplied.
    unknown = instruction_step_width("MOV", ["K1", "D12000"], plc_model="Q03UDV")
    assert unknown.steps is None and unknown.status == "requires_context"
    assert unknown.requirements == ("storage_context",)
    # Known-empty: a parsed parameter record without extended ranges.
    empty = QStorageContext(cpu="Q03UDV", parameter_record=b"stub", ranges=(), reason="probe")
    known = instruction_step_width("MOV", ["K1", "D12000"], plc_model="Q03UDV", context=empty)
    assert known.status == "known" and known.steps == 2
    # Explicit opt-in for a caller with no project: the premise is stated.
    assumed = instruction_step_width("MOV", ["K1", "D12000"], plc_model="Q03UDV",
                                    assume_default_allocation=True)
    assert assumed.status == "known" and assumed.steps == 2
    assert any("default (unextended) allocation" in item for item in assumed.evidence)
    # A form that names no D/W device never needs the allocation.
    assert instruction_step_width("LD", ["M0"], plc_model="Q03UDV").steps == 1

def test_device_range_step_table_is_scoped_to_its_cpu_series():
    # A direct M target widens with the FX3 index range; the older FX series
    # encode the same device in one step, so the FX3 split must not reach them.
    assert instruction_step_width("LD", ["M1536"], plc_model="FX3U").steps == 2
    assert instruction_step_width("SET", ["M3584"], plc_model="FX3U").steps == 3
    assert instruction_step_width("LD", ["M0"], plc_model="FX3U").steps == 1
    # FX2N keeps its own two-step direct M form from M1536 up to M3071 (native
    # measurement in GX Works2 1.635.0.1); the FX3 three-step range above M3584
    # still never reaches it, because the IDE refuses those addresses for FX2N.
    for two_step in ("FX2N", "FX2N/FX2NC"):
        assert instruction_step_width("LD", ["M1535"], plc_model=two_step).steps == 1
        assert instruction_step_width("LD", ["M1536"], plc_model=two_step).steps == 2
        assert instruction_step_width("SET", ["M3071"], plc_model=two_step).steps == 2
    # The FX1S M space ends at M511 and the FX1N space at M1535, so their direct M
    # contact stays at one step throughout and the FX3 split cannot reach them.
    for older in ("FX1S", "FX1N"):
        width = instruction_step_width("LD", ["M1536"], plc_model=older)
        assert (width.steps, width.scope) == (1, "family"), older
        assert width.evidence, older
    # Special relays sit above the user range and have their own manual span.
    assert instruction_step_width("LD", ["M8000"], plc_model="FX3U").steps == 1
    # A direct S coil outside the covered range is a stated gap, not a guess.
    gap = instruction_step_width("OUT", ["S5000"], plc_model="FX3U")
    assert gap.steps is None and gap.status == "no_profile"
    assert "outside the device-range step table" in gap.reason
    # An S contact widens at S1024 in the same table; the low native sample only
    # covers the first span.
    assert instruction_step_width("LD", ["S0"], plc_model="FX3U").steps == 1
    assert instruction_step_width("LD", ["S1024"], plc_model="FX3U").steps == 2


def test_shared_service_answers_the_forms_the_writer_used_to_compute_alone():
    # A 32-bit counter preset stores five words; an ordinary counter stores three.
    assert instruction_step_width("OUT", ["C200", "K3"], plc_model="FX3U").steps == 5
    assert instruction_step_width("OUT", ["C10", "K3"], plc_model="FX3U").steps == 3
    # A variable-string width is measured in the project code page, so the code
    # page is a stated requirement rather than a coverage gap or a guess.
    unknown = instruction_step_width("$MOV", ['"ABC"', "D0"], plc_model="FX3U")
    assert unknown.steps is None and unknown.status == "requires_context"
    assert unknown.requirements == ("text_encoding",)
    assert instruction_step_width("$MOV", ['"ABC"', "D0"], plc_model="FX3U",
                                 text_encoding="cp932").steps == 7
    assert instruction_step_width("$MOV", ['"AB"', "D0"], plc_model="FX3U",
                                 text_encoding="cp932").steps == 5
    # Direct D to D uses one source pair, so it is five words without a payload.
    assert instruction_step_width("$MOV", ["D0", "D2"], plc_model="FX3U").steps == 5


#: Mitsubishi FX3U/FX3UC Programming Manual, Basic & Applied Instruction Edition
#: (JY997D16601-R) section 7.16, printed page 219. These are the manual's numbers,
#: written down here independently of the catalogue that has to reproduce them.
MANUAL_FX3_WIDTHS = [
    ("LD", "S0", 1), ("LD", "S1023", 1), ("LD", "S1024", 2), ("LD", "S4095", 2),
    ("AND", "S1024", 2), ("OUT", "S0", 2),
    ("LD", "M0", 1), ("LD", "M1535", 1), ("LD", "M1536", 2), ("LD", "M3583", 2),
    ("LD", "M3584", 3), ("LD", "M7679", 3),
    ("LDP", "M0", 2), ("LDP", "M3583", 2), ("LDP", "M3584", 3),
    ("PLS", "M3584", 3), ("PLF", "M3584", 3),
    ("LD", "M8000", 1), ("LD", "M8255", 1), ("LD", "M8256", 2), ("LD", "M8511", 2),
    ("ANI", "M8340", 2),
    ("SET", "M8161", 2), ("RST", "M8123", 2),
]


@pytest.mark.parametrize("opcode,operand,steps", MANUAL_FX3_WIDTHS)
def test_fx_step_width_follows_the_manual_device_range_table(opcode, operand, steps):
    width = instruction_step_width(opcode, [operand], plc_model="FX3U")
    assert (width.status, width.steps) == ("known", steps), (opcode, operand, width.reason)


def test_older_fx_series_keep_their_own_high_address_widths():
    # The table is a per-series claim: the FX3 contact split and the M3584 pulse
    # step do not transfer to the older manuals.
    assert instruction_step_width("LD", ["S1024"], plc_model="FX2N").steps == 1
    assert instruction_step_width("LD", ["M3584"], plc_model="FX1S").steps == 1
    assert instruction_step_width("LDP", ["M3584"], plc_model="FX2N").steps == 2
    for older in ("FX2N", "FX1N", "FX1S"):
        # The older series share the observed two/three-word MC form; what they
        # must not inherit is the FX3 M3584 split. The observation is family
        # evidence, declared as such in the resource.
        assert instruction_step_width("MC", ["N0", "M3584"], plc_model=older).steps == 3


def test_mc_mcr_and_next_take_their_width_from_the_shared_service():
    assert instruction_step_width("MC", ["N0", "M0"], plc_model="FX3U").steps == 3
    assert instruction_step_width("MC", ["N0", "M3584"], plc_model="FX3U").steps == 4
    assert instruction_step_width("MCR", ["N0"], plc_model="FX3U").steps == 2
    assert instruction_step_width("NEXT", [], plc_model="FX3U").steps == 1
    # The writer may only pick a header that carries the shared width, so the
    # stored words are checked against the manual, not against the service.
    from gxw.token_encoder import encode_instruction
    for opcode, operands, words in (("MC", ["N0", "M0"], 3), ("MC", ["N0", "M3584"], 4),
                                    ("MCR", ["N0"], 2), ("NEXT", [], 1)):
        raw = encode_instruction(opcode, operands, profile="fx3u", plc_model="FX3U",
                                 text_encoding="cp936")
        # A stored FX header starts with its own byte length.
        header = raw[:raw[0]]
        assert stored_header_width(header) == words, (opcode, operands, raw.hex())


def test_device_range_tables_do_not_depend_on_their_order():
    resource = json.loads((Path(__file__).resolve().parents[1]
                           / "resources/instructions/mitsubishi/fx3u_step_widths.json"
                           ).read_text(encoding="utf-8"))
    profile = json.loads(json.dumps(resource["step_width_profile"]))
    # Same rules, same observations, reversed device-range tables: only the table
    # order changes, so every answer must stay the same.
    reversed_profile = json.loads(json.dumps(profile))
    reversed_profile["device_ranges"] = list(reversed(reversed_profile["device_ranges"]))
    catalog = default_step_width_catalog()
    reversed_catalog = StepWidthCatalog(resource["opcodes"], reversed_profile)
    probes = [("LD", ["S1024"]), ("LD", ["M1536"]), ("LD", ["M3584"]), ("LD", ["M8256"]),
              ("LD", ["M8340"]), ("LDP", ["M3584"]), ("SET", ["M8161"]), ("MC", ["N0", "M3584"]),
              ("OUT", ["S5000"]), ("LD", ["X0"])]
    for opcode, operands in probes:
        forward = catalog.resolve(opcode, operands, plc_model="FX3U")
        backward = reversed_catalog.resolve(opcode, operands, plc_model="FX3U")
        assert (forward.status, forward.steps) == (backward.status, backward.steps), (opcode, operands)


def test_overlapping_device_range_tables_report_a_conflict():
    tables = [
        {"devices": ["M"], "opcodes": ["LD"], "series": ["FX3"],
         "outside": "fall_through", "ranges": [{"from": 0, "to": 4095, "steps": 1}],
         "evidence": "first table"},
        {"devices": ["M"], "opcodes": ["LD"], "series": ["FX3"],
         "outside": "fall_through", "ranges": [{"from": 0, "to": 4095, "steps": 2}],
         "evidence": "second table"},
    ]
    catalog = StepWidthCatalog({}, {"models": ["FX3U"], "rules": [], "device_ranges": tables})
    width = catalog.resolve("LD", ["M10"], plc_model="FX3U")
    assert (width.status, width.steps) == ("conflict", None)
    assert "first table" in width.reason and "second table" in width.reason


def test_a_table_that_does_not_cover_the_address_does_not_end_the_search():
    legacy_rule = {"opcodes": ["LD"], "arity": 1, "operands": "S[0-9]+", "steps": 1,
                   "scope": "family", "evidence": "broad legacy contact rule"}
    low_only = {"devices": ["S"], "opcodes": ["LD"], "series": ["FX3"],
                "outside": "gap", "ranges": [{"from": 0, "to": 1023, "steps": 1}],
                "evidence": "low S contacts"}
    open_table = {"devices": ["S"], "opcodes": ["LD"], "series": ["FX3"],
                  "outside": "fall_through", "ranges": [], "evidence": "no span of its own"}
    # A closed table owns the form, so the address outside it is a stated gap.
    closed = StepWidthCatalog({}, {"models": ["FX3U"], "rules": [legacy_rule],
                                   "device_ranges": [low_only]})
    width = closed.resolve("LD", ["S2000"], plc_model="FX3U")
    assert (width.status, width.steps) == ("no_profile", None)
    # An open table lets the other rule kinds answer instead of ending the search.
    opened = StepWidthCatalog({}, {"models": ["FX3U"], "rules": [legacy_rule],
                                   "device_ranges": [low_only, open_table]})
    assert opened.resolve("LD", ["S2000"], plc_model="FX3U").steps == 1


def test_the_writer_no_longer_invents_a_width_the_catalogue_lacks():
    from gxw.models import GXWFormatError
    from gxw.token_encoder import encode_instruction

    # A direct S coil above S4095 has no width in the table, so the writer
    # reports the catalogue's own gap instead of choosing a header itself.
    with pytest.raises(GXWFormatError, match="outside the device-range step table"):
        encode_instruction("OUT", ["S5000"], profile="fx3u", plc_model="FX3U",
                           text_encoding="cp936")


#: A Param.wpa record that maps 1024 words from D12000 onto ZR65536. It is a
#: synthetic parameter stream, not GX Works2 output: it proves the parser ->
#: QStorageContext -> address mapping -> width chain, not native behaviour.
ALLOCATION_STREAM = bytes.fromhex("160000200400a800e02ea8020004b4000020b0034000ffff")


def test_allocation_dependency_follows_the_operand_not_its_spelling():
    from gxw.token_q_encoder import q_project_storage_context

    context = q_project_storage_context(ALLOCATION_STREAM, cpu="Q03UDV")
    assert context.status == "available" and context.ranges == ((0xa8, 12000, 1024, 65536),)

    # W is a hexadecimal device, so WA00 and W0A00 are the same local word. Both
    # must wait for the allocation instead of one spelling answering from nothing.
    for operands in (["K1", "WA00"], ["K1", "W0A00"]):
        unknown = instruction_step_width("MOV", operands, plc_model="Q03UDV")
        assert (unknown.status, unknown.requirements) == ("requires_context",
                                                          ("storage_context",)), operands
        assert instruction_step_width("MOV", operands, plc_model="Q03UDV",
                                      context=context).steps == 2, operands

    # An @-indirect D operand reads the local allocation as well.
    for operands, extended in ((["K1", "@D12000"], 5), (["K1", "D12000"], 3)):
        unknown = instruction_step_width("MOV", operands, plc_model="Q03UDV")
        assert (unknown.status, unknown.requirements) == ("requires_context",
                                                          ("storage_context",)), operands
        assert instruction_step_width("MOV", operands, plc_model="Q03UDV",
                                      context=context).steps == extended, operands

    # Opting into the unextended allocation still records what it assumed.
    assumed = instruction_step_width("MOV", ["K1", "@D12000"], plc_model="Q03UDV",
                                     assume_default_allocation=True)
    assert assumed.steps == 4
    assert any("assumed the default (unextended) allocation for @D12000" in item
               for item in assumed.evidence)

    # Another station's W and a module buffer never use the local mapping, so the
    # allocation must not become a requirement for them.
    for operands in (["K1", "J1\\WA00"], ["K1", "U0\\G0"]):
        width = instruction_step_width("MOV", operands, plc_model="Q03UDV")
        assert (width.status, width.steps) == ("known", 4), operands
        assert instruction_step_width("MOV", operands, plc_model="Q03UDV",
                                      context=context).steps == width.steps, operands

    # An unexpanded native macro placeholder is one word whatever it is given.
    for opcode in ("M.FOO", "U.FOO"):
        placeholder = instruction_step_width(opcode, ["K1", "D12000"], plc_model="Q03UDV")
        assert (placeholder.steps, placeholder.status) == (1, "known"), opcode


def test_merged_target_and_its_member_share_one_allocation():
    from gxw.token_q_encoder import q_project_storage_context

    # One saved project carries one allocation, whether the caller names the menu
    # option or one of its members.
    for context_cpu, target in (("Q02", "Q02/Q02H"), ("Q02/Q02H", "Q02"),
                                ("L02", "L02/L02-P"), ("L02/L02-P", "L02")):
        context = q_project_storage_context(ALLOCATION_STREAM, cpu=context_cpu)
        width = instruction_step_width("MOV", ["K1", "D12000"], plc_model=target,
                                       context=context)
        assert (width.status, width.steps) == ("known", 4 if target.startswith("Q") else 3), \
            (context_cpu, target, width.reason)

    # A different CPU's allocation stays a stated gap instead of being reused.
    other = q_project_storage_context(ALLOCATION_STREAM, cpu="Q03UDV")
    mismatch = instruction_step_width("MOV", ["K1", "D12000"], plc_model="Q02/Q02H",
                                      context=other)
    assert mismatch.status == "requires_context"
    assert mismatch.requirements == ("storage_context",)


def test_mapped_w_address_answers_the_same_in_both_hex_spellings():
    from gxw.token_q_encoder import QStorageContext

    # A synthetic context that maps W from W0A00 onto ZR: the two spellings of
    # that one address must both widen, and the address just below must not.
    context = QStorageContext(cpu="Q03UDV", parameter_record=None,
                              ranges=((0xa8, 12000, 1024, 65536), (0xb4, 0x0A00, 16, 66000)))
    assert instruction_step_width("MOV", ["K1", "WA00"], plc_model="Q03UDV",
                                  context=context).steps == 3
    assert instruction_step_width("MOV", ["K1", "W0A00"], plc_model="Q03UDV",
                                  context=context).steps == 3
    assert instruction_step_width("MOV", ["K1", "W0A00Z0"], plc_model="Q03UDV",
                                  context=context).steps == 3
    assert instruction_step_width("MOV", ["K1", "W09FF"], plc_model="Q03UDV",
                                  context=context).steps == 2
    # Neither spelling may answer while the allocation is unknown.
    for operands in (["K1", "WA00"], ["K1", "W0A00"]):
        assert instruction_step_width("MOV", operands,
                                      plc_model="Q03UDV").status == "requires_context", operands


def test_a_width_rule_answers_only_for_the_models_its_evidence_covers():
    # The two-word special-relay contact above M8256 was observed on an FX3, and
    # the 7.16 table that also puts it there is the FX3 device table.
    for model in ("FX3U", "FX3UC", "FX3G"):
        width = instruction_step_width("ANI", ["M8340"], plc_model=model)
        assert (width.status, width.steps) == ("known", 2), model
        assert any("FX3" in item for item in width.evidence), (model, width.evidence)
    # An FX2N or FX1S contact at the same address is not that encoding: the broad
    # family rule answers one word and claims no FX3 special-relay evidence.
    for model in ("FX2N", "FX1S"):
        width = instruction_step_width("ANI", ["M8340"], plc_model=model)
        assert (width.status, width.steps, width.source) == ("known", 1, "operand_rule"), model
        assert not any("040d0204" in item for item in width.evidence), model
    # A form recorded only for FX3 is a stated gap on another series, not a guess.
    for model in ("FX2N", "FX1S"):
        width = instruction_step_width("$MOV", ['"abc"', "D10"], plc_model=model,
                                       text_encoding="cp936")
        assert (width.status, width.steps) == ("no_profile", None), model
        assert "FX3" in (width.reason or ""), (model, width.reason)
    # The FX3 series itself keeps the form.
    assert instruction_step_width("$MOV", ['"abc"', "D10"], plc_model="FX3U",
                                  text_encoding="cp936").steps == 7


def test_a_width_rule_must_declare_the_models_its_evidence_covers():
    from plc.instruction_steps import StepWidthCatalog

    unscoped = {"opcodes": ["LD"], "arity": 1, "operands": "M[0-9]+", "steps": 1,
                "evidence": "no scope recorded"}
    with pytest.raises(ValueError, match="no evidence scope"):
        StepWidthCatalog({}, {"models": ["FX3U"], "rules": [unscoped]})


def test_only_the_placeholder_named_calls_skip_the_allocation():
    from gxw.token_q_encoder import QStorageContext, named_call_family

    # The stored-width algorithm gives 0x78/0x79 (M./U.) one placeholder word and
    # no other named call, and both the algorithm and the allocation classifier
    # ask the same function instead of keeping a second mnemonic list.
    for opcode, family in (("S.FOO", 0x70), ("Z.FOO", 0x71), ("G.FOO", 0x72),
                           ("J.FOO", 0x73), ("D.FOO", 0x74), ("M.FOO", 0x78),
                           ("U.FOO", 0x79), ("SP.FOO", 0x70), ("MP.FOO", 0x78)):
        assert named_call_family(opcode) == family, opcode
    assert named_call_family("MOV") is None and named_call_family("") is None

    context = QStorageContext(cpu="Q03UDV", parameter_record=None,
                              ranges=((0xa8, 12000, 1024, 65536),))
    # A placeholder stores one word whatever its unexpanded arguments name, so a
    # D address inside it cannot make the width depend on the allocation.
    for opcode in ("M.FOO", "U.FOO", "MP.FOO"):
        width = instruction_step_width(opcode, ["D12000"], plc_model="Q03UDV")
        assert (width.status, width.steps) == ("known", 1), opcode
        assert instruction_step_width(opcode, ["D12000"], plc_model="Q03UDV",
                                      context=context).steps == 1, opcode

    # The other named calls are ordinary applied instructions: their D/W argument
    # is encoded in the header, so the saved allocation decides its width.
    for opcode in ("S.FOO", "Z.FOO", "D.FOO", "SP.FOO"):
        unknown = instruction_step_width(opcode, ["D12000"], plc_model="Q03UDV")
        assert (unknown.status, unknown.requirements) == ("requires_context",
                                                          ("storage_context",)), opcode
        assumed = instruction_step_width(opcode, ["D12000"], plc_model="Q03UDV",
                                        assume_default_allocation=True)
        assert assumed.steps is not None, opcode
        assert any("assumed the default (unextended) allocation for D12000" in item
                   for item in assumed.evidence), opcode
        mapped = instruction_step_width(opcode, ["D12000"], plc_model="Q03UDV",
                                        context=context)
        # An extended device code is wider than the ordinary one it replaces.
        assert mapped.steps is not None and mapped.steps > assumed.steps, opcode

    # The already closed forms keep their states.
    for operands in (["K1", "WA00"], ["K1", "W0A00"], ["K1", "@D12000"]):
        assert instruction_step_width("MOV", operands,
                                      plc_model="Q03UDV").status == "requires_context", operands
    for operands in (["K1", "J1\\WA00"], ["K1", "U0\\G0"]):
        assert instruction_step_width("MOV", operands, plc_model="Q03UDV").status == "known", operands


def _synthetic_catalog(encodings=None, **profile):
    from plc.instruction_steps import StepWidthCatalog

    rules = {"models": ["FX3U", "FX3UC"], "rules": [], "variable_opcodes": [],
             "variable_forms": [], "exact_forms": [], "device_ranges": []}
    rules.update(profile)
    return StepWidthCatalog(encodings if encodings is not None
                            else {"054c050005": ("MOV", 2), "030003": ("LD", 1)},
                            rules)


def test_an_operand_count_mismatch_is_an_operand_question_not_a_conflict():
    # Asking with no operands is a wrong question about an operand-dependent form,
    # not evidence that two widths disagree.
    for opcode in ("MC", "MOV", "SFTL"):
        width = instruction_step_width(opcode, [], plc_model="FX3U")
        assert (width.status, width.steps) == ("requires_operands", None), opcode
        assert width.requirements == ("operands",), opcode
    # A zero-operand form is still answered directly.
    assert instruction_step_width("NOP", [], plc_model="FX3U").steps == 1


def test_one_observation_scope_decides_both_width_questions():
    # MOV's sample is FX3 evidence; LD's stays family evidence by the declared
    # default. The instance query and the mnemonic query ask the same filter.
    catalogue = _synthetic_catalog(
        observation_scopes={"default": {"scope": "FAMILY"},
                            "overrides": {"MOV": {"scope": "FX3"}}})
    for model in ("FX3U", "FX3G"):
        assert catalogue.resolve("MOV", ["D0", "D1"], plc_model=model).steps == 5, model
    for model in ("FX2N", "FX1S"):
        instance = catalogue.resolve("MOV", ["D0", "D1"], plc_model=model)
        mnemonic = catalogue.mnemonic_width("MOV", plc_model=model)
        # No applicable evidence is a coverage gap on both sides, not an operand
        # question borrowed from the models that do have samples.
        assert instance.status == "no_profile", model
        assert mnemonic.status == "no_profile", model
        assert "FX3" in (instance.reason or ""), (model, instance.reason)
        # A family observation is still answered for those same targets.
        assert catalogue.resolve("LD", ["M0"], plc_model=model).steps == 1, model


def test_a_width_answer_does_not_depend_on_the_order_of_the_tables():
    from plc.instruction_steps import StepWidthCatalog

    normal = {"devices": ["M"], "opcodes": ["LD"], "series": ["FX3"],
              "outside": "fall_through", "evidence": "7.16 M contact table",
              "ranges": [{"from": 0, "to": 1535, "steps": 1},
                         {"from": 1536, "to": 3583, "steps": 2},
                         {"from": 3584, "to": 7679, "steps": 3}]}
    special = {"devices": ["M"], "opcodes": ["LD"], "series": ["FX3"],
               "outside": "fall_through", "evidence": "7.16 special M contact table",
               "ranges": [{"from": 8000, "to": 8255, "steps": 1},
                          {"from": 8256, "to": 8511, "steps": 2}]}
    rules = {"models": ["FX3U", "FX3UC"], "rules": [], "variable_opcodes": [],
             "variable_forms": [], "exact_forms": []}
    answers = []
    for order in ([normal, special], [special, normal]):
        catalogue = StepWidthCatalog({}, {**rules, "device_ranges": order})
        answers.append({address: catalogue.resolve("LD", [address], plc_model="FX3U").steps
                        for address in ("M0", "M2000", "M4000", "M8100", "M8300", "M9000")})
    assert answers[0] == answers[1] == {
        "M0": 1, "M2000": 2, "M4000": 3, "M8100": 1, "M8300": 2, "M9000": None}


@pytest.mark.parametrize("patch,message", [
    ({"observation_scopes": {"default": {"scope": ""}}}, "empty evidence scope"),
    ({"observation_scopes": {"overrides": {"MOV": {"scope": "MX9"}}}}, "unknown evidence scope"),
    ({"observation_scopes": {"overrides": {"MOV": {"models": []}}}}, "empty models list"),
    ({"observation_scopes": {"overrides": {"MOV": {"series": ["FX9"]}}}}, "unknown evidence series"),
    ({"observation_scopes": {"overrides": {"MOV": {"scope": "FAMILY",
                                                   "models": ["FX3U"]}}}}, "both the whole FX family"),
])
def test_an_observation_scope_must_name_real_targets(patch, message):
    with pytest.raises(ValueError, match=message):
        _synthetic_catalog(**patch)


def test_a_table_that_contradicts_itself_is_refused():
    from plc.instruction_steps import StepWidthCatalog

    rules = {"models": ["FX3U", "FX3UC"], "rules": [], "variable_opcodes": [],
             "variable_forms": [], "exact_forms": []}
    contradictory = {"devices": ["M"], "opcodes": ["LD"], "series": ["FX3"], "outside": "gap",
                     "evidence": "synthetic table",
                     "ranges": [{"from": 0, "to": 100, "steps": 1},
                                {"from": 50, "to": 200, "steps": 2}]}
    with pytest.raises(ValueError, match="overlap with different widths"):
        StepWidthCatalog({}, {**rules, "device_ranges": [contradictory]})
    # The same width written twice is one statement, not a contradiction.
    repeated = dict(contradictory, ranges=[{"from": 0, "to": 100, "steps": 1},
                                           {"from": 50, "to": 200, "steps": 1}])
    catalogue = StepWidthCatalog({}, {**rules, "device_ranges": [repeated]})
    assert catalogue.resolve("LD", ["M120"], plc_model="FX3U").steps == 1
    # A table that states no scope cannot widen itself to every FX model.
    unscoped = {"devices": ["M"], "opcodes": ["LD"], "outside": "gap",
                "evidence": "synthetic table",
                "ranges": [{"from": 0, "to": 100, "steps": 1}]}
    with pytest.raises(ValueError, match="no evidence scope"):
        StepWidthCatalog({}, {**rules, "device_ranges": [unscoped]})


def test_the_observation_scope_is_declared_by_the_resource():
    from plc.instruction_steps import default_step_width_catalog

    catalogue = default_step_width_catalog()
    declared = dict(catalogue.observation_default)
    # The scope is a declaration with a stated basis, not an implicit reading.
    assert str(declared.get("evidence") or "").strip()
    assert ("scope" in declared) or ("models" in declared) or ("series" in declared)
    # Every observed mnemonic resolves its scope through the same parser, so an
    # entry can never be usable in one query and unscoped in the other.
    for opcode in catalogue.observations:
        spec = catalogue.observation_scope(opcode)
        assert spec.get("scope") or spec.get("models") or spec.get("series"), opcode

def test_the_fx2n_direct_m_form_widens_where_the_older_cpus_stop():
    """A native FX2N project stores a two-step direct M form from M1536 on.

    GX Works2 1.635.0.1 compiled an FX2N/FX2NC program and the project exported from
    it reports one step at M1535 and two steps for LD, LDI, AND, ANI, OR, ORI, OUT,
    SET and RST at M1536, M2048 and M3071. The FX1S M space ends at M511 and the
    FX1N space at M1535, so neither reaches the wider form: their direct M contact
    keeps one step, which is what the shared older-series entry now says.
    """
    for opcode in ("LD", "LDI", "AND", "ANI", "OR", "ORI", "OUT", "SET", "RST"):
        assert instruction_step_width(opcode, ["M1535"], plc_model="FX2N").steps == 1
        assert instruction_step_width(opcode, ["M1536"], plc_model="FX2N").steps == 2
        assert instruction_step_width(opcode, ["M3071"], plc_model="FX2N").steps == 2
        assert instruction_step_width(opcode, ["M1536"], plc_model="FX2NC").steps == 2
    assert instruction_step_width("LD", ["M511"], plc_model="FX1S").steps == 1
    assert instruction_step_width("LDP", ["M511"], plc_model="FX1S").steps == 2
    assert instruction_step_width("LD", ["M1535"], plc_model="FX1N").steps == 1
    assert instruction_step_width("LD", ["S512"], plc_model="FX2N").steps == 1
    assert instruction_step_width("OUT", ["S512"], plc_model="FX2N").steps == 2
    assert instruction_step_width("OUT", ["T200", "K10"], plc_model="FX2N").steps == 3
    assert instruction_step_width("OUT", ["C200", "K10"], plc_model="FX2N").steps == 5
