# Instruction-fact delivery

## Targets and retrieval

[build_confirmed_generation_context](../../src/application/confirmed_generation_context.py) creates the shared generation context. [instruction_fact_targets](../../src/knowledge/instruction_facts.py) identifies instructions from the selected contract, positive selected-plan text and catalogued anchors. Targets select evidence; they do not add required opcodes or choose an analysis mode.

[retrieve_instruction_facts](../../src/knowledge/instruction_facts.py) selects definition prose, operand tables, execution conditions and limits. Exact instruction definitions take precedence over body mentions. Related units stay within their explicit section or parent and manual revision; neighboring pages are not assumed to belong to the same instruction.

The [scoped retrieval facade](runtime-ownership.md#retrieval) applies task and source lanes before candidate limits. CPU applicability remains owned by the index and instruction catalog.

## Packing and receipts

Evidence is packed as complete source units within the available allowance. Tables and source identities remain intact; duplicate diagram/layout renditions do not replace missing prose. Records retain manual identity, revision, offsets and content hashes where available.

[delivered_fact_report](../../src/knowledge/instruction_facts.py) reconciles candidate evidence with the compiled prompt using block identities and hashes. `candidate_evidence`, `budget_omitted` and `unresolved` retain different meanings. A recorded keyword category is a packing hint; a fact is only marked as delivered when its block survives compilation.

Missing facts remain visible in receipts without creating an instruction prohibition or inventing an I/O. Context compilation is owned by [application.context_compiler](../../src/application/context_compiler.py).

## Representation and measurement

Required compact fields are rendered from [application.compact_protocol](../../src/application/compact_protocol.py). Local aliases preserve the same representation; polarity and scan behavior are engineering semantics, not syntax cleanup.

[test_instruction_fact_context.py](../../tests/test_instruction_fact_context.py) covers source integrity and final-budget reconciliation. [test_agent_b_measurement.py](../../tests/test_agent_b_measurement.py) covers the paired experiment runner. The runner's usage and comparison groups are documented in [Agent B measurements](../guides/agent-b-measurement.md).
