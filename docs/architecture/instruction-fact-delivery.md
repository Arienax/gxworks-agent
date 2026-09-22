# Instruction-fact delivery

## Targets and retrieval

[build_confirmed_generation_context](../../src/application/confirmed_generation_context.py) creates the shared generation context. [instruction_fact_targets](../../src/knowledge/instruction_facts.py) identifies instructions from the selected contract, positive selected-plan text and catalogued anchors. Targets select evidence; they do not add required opcodes or choose an analysis mode.

[retrieve_instruction_facts](../../src/knowledge/instruction_facts.py) selects definition prose, operand tables, execution conditions and limits. Exact instruction definitions take precedence over body mentions. Related units stay within their explicit section or parent and manual revision; neighboring pages are not assumed to belong to the same instruction.

When the same opcode exists in multiple official manuals, [knowledge.source_authority](../../src/knowledge/source_authority.py) reads [instruction_source_authority.json](../../resources/instructions/mitsubishi/instruction_source_authority.json). The same CPU/opcode authority rule is consumed by direct structured lookup and the broad-retrieval compatibility path; manual precedence is not maintained as a second hard-coded table.

The [scoped retrieval facade](runtime-ownership.md#retrieval) applies task and source lanes before candidate limits. CPU applicability remains owned by the index and instruction catalog.

## Packing and receipts

Evidence is packed as complete source units within the available allowance. Tables and source identities remain intact; duplicate diagram/layout renditions do not replace missing prose. Records retain manual identity, revision, offsets and content hashes where available.

[knowledge.fact_coverage](../../src/knowledge/fact_coverage.py) is the canonical delivery-accounting layer for exact instruction, device and error facts. It projects targets into dimension-addressed requirements, records candidate source IDs, and reconciles them again after final prompt compilation using complete block identities and hashes. `candidate_evidence`, `budget_omitted` and `unresolved` retain different meanings.

[delivered_fact_report](../../src/knowledge/instruction_facts.py) remains a compatibility view for existing instruction diagnostics, but its status calculation delegates to the generic coverage owner. A recorded keyword category is a packing hint; a fact is only marked as delivered when its block survives compilation. Missing facts remain visible without creating an instruction prohibition or inventing an I/O. Context compilation is owned by [application.context_compiler](../../src/application/context_compiler.py).

## Representation and measurement

Required compact fields are rendered from [application.compact_protocol](../../src/application/compact_protocol.py). Local aliases preserve the same representation; polarity and scan behavior are engineering semantics, not syntax cleanup.

[test_instruction_fact_context.py](../../tests/test_instruction_fact_context.py) covers source integrity and final-budget reconciliation. [test_agent_b_measurement.py](../../tests/test_agent_b_measurement.py) covers the paired experiment runner. The runner's usage and comparison groups are documented in [Agent B measurements](../guides/agent-b-measurement.md).
