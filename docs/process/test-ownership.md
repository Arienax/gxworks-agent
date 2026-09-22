# Test ownership consolidation

Source record: `f2f1781a846c9f7073724b4cca485082d57f414c`, [tests/README.md](https://github.com/Arienax/gxworks-agent/blob/f2f1781a846c9f7073724b4cca485082d57f414c/tests/README.md). The source introduction reported 170 Python test files and 3 Web test files after consolidation; this is a historical count.

| Previous file | Retained owner |
| --- | --- |
| `test_contract_repair_policy.py` | `test_contract_repair_planner.py` |
| `test_runtime_diagnostics_context.py` | `test_runtime_diagnostics.py` |
| `test_generation_opcode_contract.py` | `test_instruction_contract_alignment.py` |

Current ownership and addition rules are in [the registry](../../tests/README.md). Consolidation preserves regression cases and their stable test identities.
