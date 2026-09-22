# Test ownership consolidation

Source record: `f2f1781a846c9f7073724b4cca485082d57f414c`, [tests/README.md](https://github.com/Arienax/gxworks-agent/blob/f2f1781a846c9f7073724b4cca485082d57f414c/tests/README.md). The source introduction reported 170 Python test files and 3 Web test files after consolidation; this is a historical count.

| Previous file | Retained owner |
| --- | --- |
| `test_contract_repair_policy.py` | `test_contract_repair_planner.py` |
| `test_runtime_diagnostics_context.py` | `test_runtime_diagnostics.py` |
| `test_generation_opcode_contract.py` | `test_instruction_contract_alignment.py` |
| `test_zrst_registry_alignment.py` | `test_instruction_contract_alignment.py` |
| `test_analog_instruction_regressions.py` | `test_instruction_contract_alignment.py` |
| `test_gxw_function_abi_67_71.py` | `test_gxw_function_abi.py` |
| `test_fresh_gxworks2_csv_http.py` | `test_fresh_gxworks2_csv_export.py` |
| `test_repair_render_delivery.py` | `test_generation_delivery.py` |

Current ownership and addition rules are in [the registry](../../tests/README.md). Consolidation preserves regression cases and their stable test identities.

## Removed obsolete test infrastructure

`tests/test_source_layout_regression_gate.py` and `scripts/source_layout_regression.py` were removed after the source-layout workflow stopped using the historical baseline-vs-candidate comparison. The current workflow requires the complete candidate suite to pass directly, so retaining a self-test for the retired comparison layer added maintenance without protecting a live contract.
