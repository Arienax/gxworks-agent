"""Exact DABCD Native profile and public APP storage consumers."""
import copy,json,os
from pathlib import Path
import pytest
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-dabcd-native-contract-repair')
MODELS=('Q00U','Q02','L02')

@pytest.fixture(scope='module')
def composed_registry():
 from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as original
 from plc.instruction_knowledge import compose_programming_facts
 from knowledge.instruction_compiler import refresh_reviewed_call_admission
 if os.environ.get('GXW2_INSTRUCTION_OWNER_MANIFEST'):
  assert original.fact_bundle_version=='rag-semantic:2026-11-01.67'
  for model in MODELS:assert original.definition_entry('DABCD',cpu=model)['call_available']
  return original
 local=copy.copy(original)
 for name in ('_specs','_cpu_contracts','_definition_contract_bases','_definition_owners'):setattr(local,name,dict(getattr(original,name)))
 local._variant_index=None;local._loaded_definition_contracts=set(original._loaded_definition_contracts);local._clear_definition_contracts()
 for e in json.loads((HERE/'source-facts.json').read_text())['entries']:
  if e['target_model'] not in MODELS:continue
  m,o=e['target_model'],e['opcode'];form=local.resolve_catalog_form(o,cpu=m)
  assert form is not None and {'operand_types','operand_order','operand_roles','device_classes'}<=set(form.spec.verified_fields),'root common contract must be merged before this actual-default consumer test'
  owner=copy.deepcopy(original.definition_entry(o,cpu=m));owner['facts']=[f for f in owner['facts'] if not f['id'].startswith(('authored:ql:','authored:applied:'))]+copy.deepcopy(e['facts'])
  owner['facts'],_=compose_programming_facts(local,o,cpu=m,existing=owner['facts']);owner['call_available']=False
  assert refresh_reviewed_call_admission(owner,local)
  local._definition_owners['mitsubishi',o,m]=owner
 return local

@pytest.fixture
def source_view(monkeypatch,composed_registry):
 import plc.instructions as instructions
 import plc.validation as validation
 import plc.ir as ir
 for module in (instructions,validation,ir):monkeypatch.setattr(module,'DEFAULT_INSTRUCTION_REGISTRY',composed_registry)
 return composed_registry

def payload(args):
 from checks.test_ladder_project_delivery import _native_instruction_payload
 return _native_instruction_payload([('DABCD',args)])

def test_actual_default_constructor_resolves_only_own_literal_and_exact_50():
 from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as r
 c=json.loads((HERE/'contract-additions.json').read_text())['cpu_contracts'];assert len(c)==1
 for m in c[0]['cpus']:
  f=r.resolve_catalog_form('DABCD',cpu=m);assert f and f.opcode==f.base_mnemonic=='DABCD' and not f.double and not f.pulse
  s=f.spec;assert s.native_operand_order==('S','D') and s.min_operands==s.max_operands==2 and s.st_call is None
  assert [o.word_count for o in s.operands]==[1,1] and [o.role.value for o in s.operands]==['read','write']
  assert all(o.data_type=='word' for o in s.operands)
 assert not r.resolve_catalog_form('DABCD',cpu='Q00').spec.supports_cpu('Q00')

@pytest.mark.parametrize('model',MODELS)
def test_actual_app_fixed_two_word_read_one_word_write_and_literal(source_view,model):
 from plc.ir import build_plc_ir,validate_plc_ir,analyze_instruction_access
 from plc.device_identity import instruction_operand_regions
 for args in (['D100','D200'],['" 494"','D200'],['"8765"','D200'],['R100','Z1']):validate_plc_ir(build_plc_ir(payload(args),plc_model=model))
 assert analyze_instruction_access('DABCD',['D100','D200'],plc_model=model)==(['D100','D101'],['D200'])
 spec=source_view.resolve('DABCD',cpu=model,operand_count=2)
 regions=instruction_operand_regions(spec,'DABCD',['D100','D200'],model)
 assert any(r['operand_index']==0 and r['count']==2 and r['addresses']==['D100','D101'] for r in regions)

@pytest.mark.parametrize('model',MODELS)
def test_actual_app_rejects_wrong_source_classes_numeric_literal_and_arity(source_view,model):
 from plc.ir import build_plc_ir,validate_plc_ir
 for args in (['M0','D200'],['Z0','D200'],[r'J1\W100','D200'],[r'U0\G100','D200'],['K1234','D200'],['D100','K0'],['D100'],['D100','D200','D300']):
  with pytest.raises(ValueError):validate_plc_ir(build_plc_ir(payload(args),plc_model=model))

@pytest.mark.parametrize('model',MODELS)
def test_actual_app_destination_forms_are_own_table_permissions(source_view,model):
 from plc.ir import build_plc_ir,validate_plc_ir
 for destination in ('D200','Z1','K4Y40',r'J1\W100',r'U0\G100'):
  validate_plc_ir(build_plc_ir(payload(['D100',destination]),plc_model=model))
