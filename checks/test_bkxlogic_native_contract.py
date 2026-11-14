"""Own BKX exclusive logical block contracts, offline only."""
from types import SimpleNamespace
import pytest
from checks import test_bklogic_native_contract as common_checks
from pathlib import Path

OPS=('BKXNR','BKXNRP','BKXOR','BKXORP')
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-bkxlogic-native-contract-repair')

@pytest.fixture
def source_registry(monkeypatch):
    monkeypatch.setattr(common_checks,'OPS',OPS)
    monkeypatch.setattr(common_checks,'SOURCE_DIR',HERE)
    return common_checks.source_delta_registry.__wrapped__(monkeypatch)

def test_bkx_actual_app_calls_use_own_device_cells(source_registry):
    common_checks.test_bklogic_actual_app_instr_new_calls_respect_parameter_device_cells()

def test_bkx_dynamic_n_word_extents(source_registry):
    common_checks.test_bklogic_source_delta_public_new_call_rejects_actual_n_word_overrun(source_registry)

def test_bkx_dynamic_count_and_repeated_constant(source_registry):
    common_checks.test_bklogic_source_delta_access_keeps_dynamic_count_and_repeated_literal(source_registry)

def test_bkx_partial_overlap_rejected_identical_head_allowed(source_registry):
    common_checks.test_bklogic_source_delta_public_validation_rejects_partial_overlap_and_keeps_identical_head(source_registry)

@pytest.mark.parametrize('op',OPS)
def test_bkx_uses_only_own_complete_exclusive_clause(op):
    from tools.fact_ql_behavior_review import _native_bk_logic_witnesses
    operation='exclusive NOR' if op.startswith('BKXNR') else 'exclusive OR'
    clause='Performs an '+operation+' operation on the data located in the n points from the device designated by (S1), and the data located in the n points from the device designated by (S2), and stores the results into the area starting from the device designated by (D).'
    constant='The constant designated by (S2) can be between -32768 and 32767 (BIN 16-bit data).'
    section=SimpleNamespace(manual='sh080809engx',bind=lambda w:[('own',377)] if w in [clause,constant] else [])
    assert _native_bk_logic_witnesses(section,op)==[clause,constant]
    for other in ['BKAND','BKOR','DBKXOR','BKXNRX']:
        assert _native_bk_logic_witnesses(section,other)==[]
    section.bind=lambda w:[('own',377)] if w==constant else []
    assert _native_bk_logic_witnesses(section,op)==[]
    section.manual='sh080783engp';section.bind=lambda w:[('own',377)]
    assert _native_bk_logic_witnesses(section,op)==[]
