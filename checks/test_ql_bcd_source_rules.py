"""Focused source-package checks for the one-word Q/L BCD form."""
import json
from pathlib import Path
import sys

HERE = Path(r"D:\gxworks-agent-private\tmp\rag-gap-implementation-20261029\ql-bcd-native-contract-repair")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
QUEUE = HERE.parent / "execution-queue.json"


def read(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def facts(entry):
    return {fact["id"]: fact for fact in entry["facts"]}


def refs(fact):
    return {(row["manual_id"], row.get("revision"), page)
            for row in fact.get("sources", []) for page in row.get("pdf_pages", [])}


def test_bcd_keeps_exact_original_queue_ids_and_all_53_ql_owners():
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))["instructions"]["BCD"]
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
        (model, "BCD") for model in models}


def test_bcd_native_signature_is_single_word_and_uses_own_x_pages():
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
        assert {page for manual, revision, page in refs(typed)} == {273, 86, 87, 133}
        assert {manual for manual, revision, page in refs(typed)} == {"sh080809engx"}
        assert owner["authored:ql:native_arguments_s_d:parameters.arity"]["value"]["minimum"] == 2
        assert owner["authored:ql:native_arguments_s_d:parameters.order"]["value"]["symbols"] == ["S", "D"]


def test_bcd_range_device_and_continuous_form_are_bound_without_pulse_witness():
    expected_devices = ["X", "Y", "M", "L", "SM", "F", "B", "SB", "FX", "FY",
                        "T", "ST", "C", "D", "W", "SD", "SW", "FD", "R", "ZR", "Z"]
    for row in read("source-facts.json")["entries"]:
        owner = facts(row)
        boundary = owner["authored:ql:bcd_source_range:constraints.numeric_ranges:documented"]
        assert boundary["status"] == "source_verified"
        assert boundary["value"]["ranges"] == [
            {"binding": {"name": "S"}, "minimum": 0, "maximum": 9999}]
        devices = owner["authored:ql:bcd_operand_devices:constraints.documented_operand_devices"]["value"]
        assert devices["source"]["device_prefixes"] == expected_devices
        assert devices["destination"]["device_prefixes"] == expected_devices
        assert devices["source"]["constant_prefixes"] == ["K", "H"]
        assert devices["destination"]["constant_prefixes"] == []
        assert devices["digit_designation"]["groups_allowed"] == [1, 2, 3, 4]
        assert devices["digit_designation"]["direct_access_prefixes_forbidden"] == ["DX", "DY"]
        form = owner["authored:ql:bcd_continuous_form:execution.operation_condition"]
        assert form["status"] == "source_verified"
        assert form["value"]["execution_form"] == "continuous"
        assert form["value"]["source_form"] == "BCD"
        assert refs(form) == {("sh080809engx", "X", 273)}


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
        refs_all = set().union(*(refs(fact) for fact in errors))
        assert refs_all == {("sh080809engx", "X", 274),
                            ("sh080809engx", "X", 273),
                            ("sh080783engp", "P", 204),
                            ("sh080783engp", "P", 205)}
        x_error = next(fact for fact in errors if ("sh080809engx", "X", 274) in refs(fact))
        p_error = next(fact for fact in errors if ("sh080783engp", "P", 205) in refs(fact))
        assert x_error["value"]["cpu_error_table_basis"] == "4100_row_marked_for_all_CPU_columns"
        assert x_error["value"]["cpu_error_table_columns"] == p_error["value"]["cpu_error_table_columns"]
        assert p_error["scope"]["languages"] == ["st", "fbd"]
        assert "authored:ql:error:effects.error_response:SM0|none" not in owner


def test_full_authored_namespace_and_prior_history_are_retained():
    rows = read("source-facts.json")["entries"]
    baseline = {(row["target_model"], row["opcode"]): row
                for row in read("baseline-source.json")["entries"]}
    history = {(row["target_model"], row["opcode"]): row
               for row in read("prior-owner-contracts.json")}
    changes = {(row["target_model"], row["opcode"], row["id"]): row
               for row in read("source-corrections.json")["changes"]}
    assert len(rows) == len(baseline) == len(history) == 53
    for row in rows:
        key = (row["target_model"], row["opcode"])
        current = facts(row)
        old = facts(baseline[key])
        retained = {item["id"]: item for item in history[key]["preserved_facts"]}
        assert all(current[identity] == item for identity, item in retained.items())
        assert all(set(item.get("depends_on", [])) <= current.keys() for item in row["facts"])
        for old_id, old_fact in old.items():
            change = changes.get((key[0], key[1], old_id))
            if change is None:
                assert current.get(old_id) == old_fact
            elif change["before"] is not None:
                assert change["before"] == old_fact
                assert any(item == old_fact for item in history[key]["replaced_facts"])
                if change["action"] == "retire":
                    assert change["after"] is None and old_id not in current


def test_native_contract_proposal_has_q_l_groups_and_continuous_form():
    from plc.instructions import InstructionSpec

    groups = read("contract-additions.json")["cpu_contracts"]
    assert [len(group["cpus"]) for group in groups] == [43, 10]
    for group in groups:
        assert group["instruction"]["mnemonic"] == "BCD"
        assert group["native_order"] == ["S", "D"]
        assert group["execution_form"] == "continuous"
        assert group["instruction"]["arity"] == {"min": 2, "max": 2}
        spec = InstructionSpec.from_mapping(group["instruction"])
        assert [pin.word_count for pin in spec.operands] == [1, 1]
        assert [pin.data_type for pin in spec.operands] == ["word", "word"]
        assert dict(spec.operands[0].integer_literal_widths) == {"H": 16, "K": 16}
        assert spec.operands[1].integer_literal_widths == ()
        assert group["numeric_boundaries"] == [{
            "operand_index": 0, "minimum": 0, "maximum": 9999,
            "unit": "BIN_value", "label": "source S"}]


def _p205_test_words(*, missing_cpu=None, code_top=100):
    row = "The data of (s) is other than 0 to 9999 at the execution of the BCD(P) instruction."
    headers = [(label, x) for label, x in zip(
        ("Q00J/", "QnH", "QnPH", "QnPRH", "QnU", "LCPU"),
        (300, 350, 400, 450, 500, 550))]
    words = [{"text": "Description", "x0": 100, "top": 80}]
    words.extend({"text": label, "x0": x, "top": 80} for label, x in headers)
    words.append({"text": "4100", "x0": 55, "top": code_top})
    tokens = row.split()
    first_line = tokens[:-1]
    words.extend({"text": token, "x0": 110 + index * 10, "top": 100}
                  for index, token in enumerate(first_line))
    words.append({"text": tokens[-1], "x0": 110, "top": 115})
    words.append({"text": "The", "x0": 100, "top": 130})
    words.extend({"text": "\uf0c6", "x0": x, "top": 100}
                  for label, x in headers if label != missing_cpu)
    return words, row


def test_p205_4100_geometry_accepts_wrapped_row_and_rejects_missing_or_misaligned_evidence():
    from tools.fact_ql_behavior_review import _p_bcd_4100_geometry_witness

    words, row = _p205_test_words()
    assert _p_bcd_4100_geometry_witness(words, "4100", row) == "4100 " + row

    missing_mark, _ = _p205_test_words(missing_cpu="QnU")
    assert _p_bcd_4100_geometry_witness(missing_mark, "4100", row) is None

    wrong_row, _ = _p205_test_words(code_top=90)
    assert _p_bcd_4100_geometry_witness(wrong_row, "4100", row) is None
