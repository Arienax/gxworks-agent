import copy

import pytest

from application.generation_repair import GenerationValidationError
from plc_generation import prepare_ladder_candidate
from plc_json_validator import (
    PLCJsonValidationAggregateError,
    collect_ladder_candidate_structure_errors,
)
from test_generation_repair_assembly import ladder40


def _multi_bad_candidate():
    candidate = ladder40(invalid=False)
    rung = candidate["rungs"][0]
    rung["debug_note"] = "x" * 100
    rung["branches"][0]["outputs"] = [
        {"type": "APP_INSTR", "opcode": "DDADDP", "operands": ["D0", "D2", "D4"], "label": None},
        {"type": "APP_INSTR", "opcode": "DDMOVP", "operands": ["D10", "D12"], "label": None},
    ]
    return candidate


def test_collector_reports_independent_sibling_failures_in_one_pass():
    errors = collect_ladder_candidate_structure_errors(_multi_bad_candidate(), "FX3U")
    assert len(errors) == 3
    texts = [str(error) for error in errors]
    assert any("debug_note" in text for text in texts)
    assert {getattr(error, "observed_opcode", None) for error in errors} >= {"DDADDP", "DDMOVP"}


def test_generation_raises_one_aggregate_with_all_collected_violations():
    with pytest.raises(PLCJsonValidationAggregateError) as rejected:
        prepare_ladder_candidate(_multi_bad_candidate(), plc_model="FX3U")
    wrapped = GenerationValidationError(
        [rejected.value], attempts=0, max_attempts=0, language="zh-CN", stop_reason="final_validation"
    )
    assert wrapped.diagnostics["violation_count"] == 3
    assert wrapped.diagnostics["truncated"] is False
    observed = {row.get("observed_opcode") for row in wrapped.diagnostics["violations"]}
    assert {"DDADDP", "DDMOVP"} <= observed
