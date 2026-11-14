"""DELTA own direct-output source; no ordinary-Y alias or hardware claim."""
import json
from pathlib import Path
HERE=Path("D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-delta-native-contract-repair")
def read(n):return json.loads((HERE/n).read_text(encoding="utf-8"))
def rows():return read("source-facts.json")["entries"]
def facts(e):return {f["id"]:f for f in e["facts"]}

def test_delta_exact106_namespace_history_and_contracts():
 es=rows();assert len(es)==106 and sum(len(e["facts"]) for e in es)==2544
 assert {(e["target_model"],e["opcode"]) for e in es}=={(e["target_model"],e["opcode"]) for e in read("original-owner-selectors.json")}
 cs=read("contract-additions.json")["cpu_contracts"];assert len(cs)==2 and all(len(c["cpus"])==53 and c["native_order"]==["D"] for c in cs)
 prior={(e["target_model"],e["opcode"]):e for e in read("prior-owner-contracts.json")}
 assert sum(len(e["replaced_facts"]) for e in prior.values())==1272
 assert sum(len(e["preserved_facts"]) for e in prior.values())==1166
 for e in es:
  fs=facts(e);assert all(fs[f["id"]]==f for f in prior[e["target_model"],e["opcode"]]["preserved_facts"])
  assert all(set(f.get("depends_on",[]))<=fs.keys() for f in e["facts"])

def test_delta_native_and_structured_direct_identity_and_honest_boundaries():
 for e in rows():
  fs=facts(e)
  for key,parameter,page in [("authored:ql:native_arguments_d:parameters.typed_operands","D",173),*[("authored:ql:argument_table_"+lang+":parameters.typed_operands","d",127) for lang in ["st","fbd","ladder"]]]:
   f=fs[key];assert f["status"]=="source_verified" and any(page in s["pdf_pages"] for s in f["sources"])
   p=next(p for p in f["value"]["operands"] if p["name"]==parameter)
   assert p["type"]=="BOOL" and p["passing"]=="device_reference" and p["semantic_access"]=="write" and p["occupied_bit_count"]==1
   assert p["device_identity"]==dict(kind="direct_access_output",prefix="DY") and p["range"]["device_prefixes"]==["DY"]
   assert p["index_modification_applicability_established"] is p["index_modification_allowed"] is p["range"]["index_modification"] is True
   assert any((116 if parameter=="D" else 69) in r["pdf_pages"] for r in f["sources"])
   for k in ["current_index_value_verified","effective_output_number_verified","ZZ_configuration_verified","ZZ_index_modification_applicability_established"]:assert p[k] is False
   assert p["range"]["digit_groups_allowed"] is False and "word_device_layout" not in p
   for k in ["cpu_Y_number_range_connection_established","installed_module_verified","actual_output_configuration_verified","hardware_target_configuration_verified"]:assert p[k] is False

def test_delta_own_pulse_relation_trigger_and_error():
 for e in rows():
  fs=facts(e)
  for suffix,page in [("",173),(":variant:2",127)]:
   f=fs["authored:ql:family:effects.operation"+suffix];v=f["value"]
   assert any(page in s["pdf_pages"] for s in f["sources"])
   assert v["operation"]=="pulse_direct_access_output" and v["documented_output_action_sequence"]==["SET","RST"]
   assert v["pulse_occurs_at_instruction_execution"] is True
   for k in ["numeric_output_bound_in_this_section_established","physical_pulse_width_established","pulse_width_equals_one_scan_established","command_FALSE_output_behavior_established","core_execution_established","native_execution_verified","full_source_relation_complete"]:assert v[k] is False
  assert fs["authored:ql:delta_execution_gate:execution.operation_condition"]["value"]["enabled_when"]==("execution_condition_rising_edge" if e["opcode"]=="DELTAP" else "execution_condition_ON")
  assert fs["authored:ql:error:effects.error_response:SM0|SD0"]["value"]["documented_codes"]=={"4101":"direct_access_output_number_exceeds_CPU_module_output_range"}

def test_delta_existing_own_cpu_support_without_invented_serial():
 for e in rows():
  fs=facts(e)
  for k in ["authored:ql:cpu_applicability:constraints.cpu_availability","authored:ql:cpu_applicability:constraints.cpu_availability:variant:2"]:
   f=fs[k];assert f["value"]["available"] is True and not f.get("conditions")
   assert f["value"]["model_states"]=={e["target_model"]:"supported"}

def test_delta_actual_compose_strict_and_complete_namespace_survive():
 report=read("local-admission-check.json");assert report["passed"] and len(report["owners"])==106
 assert all(e["refreshed"] and e["call_available"] and e["own_layout_survived"] for e in report["owners"])
 composed={(e["target_model"],e["opcode"]):facts(e) for e in read("composed-own-source.json")["entries"]}
 for e in rows():assert all(composed[e["target_model"],e["opcode"]].get(f["id"])==f for f in e["facts"])

def test_delta_default_native_and_structured_relation_delivered():
 report=read("default-projection-check.json");assert report["passed"] and len(report["checks"])==212
 assert all(e["passed"] for e in report["checks"])
