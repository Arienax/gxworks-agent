"""Exact P-form Gray/BIN source; preserve existing complete relations."""
import json
from pathlib import Path
HERE=Path("D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-gray-bin-native-contract-repair")
def read(n):return json.loads((HERE/n).read_text(encoding="utf-8"))
def rows():return read("source-facts.json")["entries"]
def facts(e):return {f["id"]:f for f in e["facts"]}

def test_gray_original106_and_complete_namespace_history():
 es=rows();assert len(es)==106 and sum(len(e["facts"]) for e in es)==2703
 assert {(e["target_model"],e["opcode"]) for e in es}=={(e["target_model"],e["opcode"]) for e in read("original-owner-selectors.json")}
 prior={(e["target_model"],e["opcode"]):e for e in read("prior-owner-contracts.json")}
 assert sum(len(e["replaced_facts"]) for e in prior.values())==424
 assert sum(len(e["preserved_facts"]) for e in prior.values())==1749
 for e in es:
  fs=facts(e);assert all(fs[f["id"]]==f for f in prior[e["target_model"],e["opcode"]]["preserved_facts"])
  assert all(set(f.get("depends_on",[]))<=fs.keys() for f in e["facts"])

def test_gray_native32_two_word_constant_input_and_output_reference():
 for e in rows():
  f=facts(e)["authored:ql:native_arguments_s_d:parameters.typed_operands"];s,d=f["value"]["operands"]
  assert f["status"]=="source_verified" and f["value"]["native_form"]["operand_order"]==["S","D"]
  assert any((289 if e["opcode"]=="DGBINP" else 287) in src["pdf_pages"] for src in f["sources"])
  for p in [s,d]:
   assert p["type"]=="ANY32" and p["word_device_layout"]==dict(device_unit_bits=16,word_count=2,value_bits=32,signed=False,word_order="low_word_first")
   assert p["range"]["digit_groups_max"]==8 and p["range"]["address_forms"]==["buffer_memory","link_direct"]
  assert "passing" not in s and s["range"]["literal_prefixes"]==["K","H"] and s["range"]["minimum"]==0 and s["range"]["maximum"]==2147483647
  assert d["passing"]=="device_reference" and d["range"]["literal_prefixes"]==[]

def test_gray_structured_own_constants_interfaces_preserved():
 for e in rows():
  fs=facts(e)
  for lang in ["st","fbd","ladder"]:
   for dimension in ["typed_operands",lang+"_signature"]:
    f=fs["authored:ql:argument_table_"+lang+":parameters."+dimension]
    pins=next(f["value"][k] for k in ["operands","parameters","pins"] if k in f["value"])
    s=next(p for p in pins if p["name"]=="s");d=next(p for p in pins if p["name"]=="d")
    assert "passing" not in s and s["range"]["literal_prefixes"]==["K","H"]
    assert d["passing"]=="device_reference" and d["range"]["literal_prefixes"]==[]
    assert all(p["word_device_layout"]["word_count"]==2 for p in [s,d])
    assert any(src["manual_id"]=="sh080783engp" and (227 if e["opcode"]=="DGBINP" else 225) in src["pdf_pages"] for src in f["sources"])

def test_gray_own_P_gate_CPU_and_error():
 for e in rows():
  fs=facts(e);gate=fs["authored:ql:gray_p_execution_gate:execution.operation_condition"]
  assert gate["value"]["enabled_when"]=="execution_condition_rising_edge" and gate["conditions"]==[dict(context="project_mode",equals="native_ladder_instruction")]
  assert gate["value"]["core_execution_established"] is False
  for suffix in ["",":variant:2"]:
   f=fs["authored:ql:cpu_applicability:constraints.cpu_availability"+suffix];assert f["value"]["available"] and not f.get("conditions")
  error=fs["authored:ql:error:effects.error_response:SM0|SD0"]["value"]
  assert error["documented_codes"]=={"4100":"Gray_source_outside_0_to_2147483647" if e["opcode"]=="DGBINP" else "negative_BIN_source"}
  assert error["valid_source_range"]==dict(minimum=0,maximum=2147483647)

def test_gray_P_only_split_keeps_continuous_value_and_all_full_relations():
 proposal=read("family-corrections-proposal.json");assert len(proposal["corrections"])==len(proposal["additions"])==8
 for c in proposal["corrections"]:
  before,after=c["before"],c["after"];assert before["value"]==after["value"] and before["id"]==after["id"]
  assert all(before.get(k)==after.get(k) for k in before if k!="scope")
  assert after["scope"]["forms"] in [["DGRY"],["DGBIN"]]
 report=read("local-admission-check.json");assert report["passed"] and len(report["owners"])==106
 final=read("final-composition-check.json");assert final["passed"] and final["old_shared_typed_active"]==0 and final["old_shared_typed_in_history"]==424
 assert final["typed_all_inputs_allow_constants"] and final["typed_all_outputs_require_devices"]
 base={(e["target_model"],e["opcode"]):e for e in read("baseline-source.json")["entries"]}
 for e in read("composed-own-source.json")["entries"]:
  fs=facts(e)
  for f in base[e["target_model"],e["opcode"]]["facts"]:
   if f["id"].startswith("family:") and f["dimension"]=="effects.documented_operation":assert fs[f["id"]]==f

def test_gray_exact_physical_processing_range_sources():
 for e in rows():
  fs=facts(e);gbin=e["opcode"]=="DGBINP"
  native=fs["authored:ql:native_arguments_s_d:parameters.typed_operands"]
  assert {289,290}<=set(p for s in native["sources"] if s["manual_id"]=="sh080809engx" for p in s["pdf_pages"]) if gbin else any(287 in s["pdf_pages"] for s in native["sources"])
  for lang in ["st","fbd","ladder"]:
   for dimension in ["typed_operands",lang+"_signature"]:
    f=fs["authored:ql:argument_table_"+lang+":parameters."+dimension]
    assert {227,228}<=set(p for s in f["sources"] if s["manual_id"]=="sh080783engp" for p in s["pdf_pages"]) if gbin else {225,226}<=set(p for s in f["sources"] if s["manual_id"]=="sh080783engp" for p in s["pdf_pages"])
