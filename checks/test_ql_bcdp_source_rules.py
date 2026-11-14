"""Own X273 Q/L BCDP source contract; no candidate or native run."""
import json
from pathlib import Path
import sys

HERE = Path(r"D:\gxworks-agent-private\tmp\rag-gap-implementation-20261029\ql-bcdp-native-contract-repair")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
QUEUE = HERE.parent / "execution-queue.json"


def read(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def facts(entry):
    return {fact["id"]: fact for fact in entry["facts"]}


def test_bcdp_keeps_exact_queue_ids_and_all_53_ql_owners():
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))["instructions"]["BCDP"]
    models = [model for model in queue["models"] if model.startswith(("Q", "L"))]
    unit = read("original-unit.json")
    selectors = read("owner-selectors.json")
    rows = read("source-facts.json")["entries"]
    assert unit["unit_ids"] == ["I-00504", "I-00505", "I-00506", "I-00507", "I-00508",
                                 "I-00510", "I-00511", "I-00512", "I-00513"]
    assert "I-00509" not in unit["unit_ids"]
    assert len(models) == len(rows) == len(selectors) == 53
    assert unit["models"] == models
    assert {(row["target_model"], row["opcode"]) for row in selectors} == {
        (model, "BCDP") for model in models}


def test_bcdp_native_form_is_narrowed_to_one_word_and_bound_to_own_pages():
    for row in read("source-facts.json")["entries"]:
        owner = facts(row)
        typed = owner["authored:ql:native_arguments_s_d:parameters.typed_operands"]
        assert typed["status"] == "source_verified"
        source, destination = typed["value"]["operands"]
        assert [source["name"], destination["name"]] == ["S", "D"]
        assert all(pin["type"] == "ANY16" and "documented_type_alternatives" not in pin
                   for pin in (source, destination))
        assert all(pin["word_device_layout"] == {
            "device_unit_bits": 16, "word_count": 1, "value_bits": 16}
            for pin in (source, destination))
        assert not any(key in source["range"] for key in ("minimum", "maximum"))
        assert not any(key in destination["range"] for key in ("minimum", "maximum"))
        assert {(ref["manual_id"], page) for ref in typed["sources"]
                for page in ref["pdf_pages"]} == {
                    ("sh080809engx", 273), ("sh080809engx", 86),
                    ("sh080809engx", 87), ("sh080809engx", 133)}
        assert "authored:ql:cpu_applicability:constraints.cpu_availability" in typed["depends_on"]
        assert owner["authored:ql:native_arguments_s_d:parameters.arity"]["value"]["minimum"] == 2
        assert owner["authored:ql:native_arguments_s_d:parameters.order"]["value"]["symbols"] == ["S", "D"]


def test_bcdp_source_range_devices_pulse_and_form_arity_are_source_checked():
    expected_devices = ["X", "Y", "M", "L", "SM", "F", "B", "SB", "FX", "FY",
                        "T", "ST", "C", "D", "W", "SD", "SW", "FD", "R", "ZR", "Z"]
    for row in read("source-facts.json")["entries"]:
        owner = facts(row)
        boundary = owner["authored:ql:bcdp_source_range:constraints.numeric_ranges:documented"]
        assert boundary["status"] == "source_verified"
        assert boundary["value"]["ranges"] == [
            {"binding": {"name": "S"}, "minimum": 0, "maximum": 9999}]
        devices = owner["authored:ql:bcdp_operand_devices:constraints.documented_operand_devices"]["value"]
        assert devices["source"]["device_prefixes"] == expected_devices
        assert devices["destination"]["device_prefixes"] == expected_devices
        assert devices["source"]["constant_prefixes"] == ["K", "H"]
        assert devices["destination"]["constant_prefixes"] == []
        assert devices["digit_designation"]["groups_allowed"] == [1, 2, 3, 4]
        assert devices["digit_designation"]["direct_access_prefixes_forbidden"] == ["DX", "DY"]
        pulse = owner["authored:ql:bcdp_pulse_form:execution.operation_condition"]
        assert pulse["status"] == "source_verified" and pulse["value"]["execution_form"] == "pulse"


def test_x274_and_p205_4100_error_rows_are_uniform_for_all_selected_q_and_l_owners():
    rows = read("source-facts.json")["entries"]
    q_rows = [row for row in rows if row["target_model"].startswith("Q")]
    l_rows = [row for row in rows if row["target_model"].startswith("L")]
    assert (len(q_rows), len(l_rows)) == (43, 10)
    for row in rows:
        owner = facts(row)
        errors = [fact for fact in owner.values()
                  if fact["dimension"] == "effects.error_response"
                  and fact["value"].get("documented_codes", {}).get("4100") ==
                      "source_S_outside_0_to_9999"]
        assert len(errors) == 2
        assert all(fact["status"] == "source_verified" for fact in errors)
        assert all(fact["value"]["error_flags"] == ["SM0"] for fact in errors)
        assert all(fact["value"]["error_code_registers"] == ["SD0"] for fact in errors)
        assert all(fact["value"]["cpu_error_row_applicability"] is True for fact in errors)
        assert all(fact["value"]["source_value_error"] == {
            "code": "4100", "source_parameter": "S", "rule": "outside_inclusive_range",
            "minimum": 0, "maximum": 9999} for fact in errors)
        refs_all = {(ref["manual_id"], ref["revision"], page)
                    for fact in errors for ref in fact.get("sources", [])
                    for page in ref.get("pdf_pages", [])}
        assert refs_all == {("sh080809engx", "X", 274),
                            ("sh080809engx", "X", 273),
                            ("sh080783engp", "P", 204),
                            ("sh080783engp", "P", 205)}
        x_error = next(fact for fact in errors if any(
            ref["manual_id"] == "sh080809engx" and 274 in ref["pdf_pages"]
            for ref in fact.get("sources", [])))
        p_error = next(fact for fact in errors if any(
            ref["manual_id"] == "sh080783engp" and 205 in ref["pdf_pages"]
            for ref in fact.get("sources", [])))
        assert x_error["value"]["cpu_error_table_basis"] == "4100_row_marked_for_all_CPU_columns"
        assert x_error["value"]["cpu_error_table_columns"] == p_error["value"]["cpu_error_table_columns"]
        assert p_error["scope"]["languages"] == ["st", "fbd"]
        assert "authored:ql:error:effects.error_response:SM0|none" not in owner


def test_full_owner_namespaces_retain_non_bcdp_facts_and_prior_history():
    rows = read("source-facts.json")["entries"]
    baseline = {(row["target_model"], row["opcode"]): row
                for row in read("baseline-source.json")["entries"]}
    history = {(row["target_model"], row["opcode"]): row
               for row in read("prior-owner-contracts.json")}
    corrections = read("source-corrections.json")["changes"]
    correction_ids = {(row["target_model"], row["opcode"], row["id"]): row for row in corrections}
    assert len(rows) == 53
    retired = [row for row in corrections if row.get("action") == "retire"]
    assert len(retired) == 53
    assert all(row["id"] == "authored:ql:error:effects.error_response:SM0|none"
               and row["after"] is None for row in retired)
    for row in rows:
        key = (row["target_model"], row["opcode"])
        current = facts(row)
        old = facts(baseline[key])
        preserved = {item["id"]: item for item in history[key]["preserved_facts"]}
        assert all(current[identity] == item for identity, item in preserved.items())
        assert all(set(item.get("depends_on", [])) <= current.keys() for item in row["facts"])
        for old_id, old_fact in old.items():
            correction = correction_ids.get((key[0], key[1], old_id))
            if correction and correction["before"] is not None:
                assert correction["before"] == old_fact
                assert any(item == old_fact for item in history[key]["replaced_facts"])
                if correction["action"] == "retire":
                    assert correction["after"] is None and old_id not in current
            elif old_id in current:
                assert current[old_id] == old_fact or correction_ids.get((key[0], key[1], old_id))


def test_native_contract_proposal_has_two_exact_cpu_groups_and_16bit_width():
    from plc.instructions import InstructionSpec

    groups = read("contract-additions.json")["cpu_contracts"]
    assert [len(group["cpus"]) for group in groups] == [43, 10]
    assert all(group["instruction"]["mnemonic"] == "BCDP"
               and group["native_order"] == ["S", "D"]
               and group["execution_form"] == "pulse"
               and group["instruction"]["arity"] == {"min": 2, "max": 2}
               for group in groups)
    for group in groups:
        spec = InstructionSpec.from_mapping(group["instruction"])
        assert [pin.word_count for pin in spec.operands] == [1, 1]
        assert [pin.data_type for pin in spec.operands] == ["word", "word"]
        assert dict(spec.operands[0].integer_literal_widths) == {"H": 16, "K": 16}
        assert spec.operands[1].integer_literal_widths == ()
        assert group["numeric_boundaries"] == [{
            "operand_index": 0,
            "minimum": 0,
            "maximum": 9999,
            "unit": "BIN_value",
            "label": "source S",
        }]
