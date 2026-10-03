# Instruction-fact delivery

## Targets and retrieval

[build_confirmed_generation_context](../../src/application/confirmed_generation_context.py) creates the shared generation context. [instruction_fact_targets](../../src/knowledge/instruction_facts.py) identifies instructions from the selected contract, positive selected-plan text and catalogued anchors. Targets select evidence; they do not add required opcodes or choose an analysis mode.

[retrieve_instruction_facts](../../src/knowledge/instruction_facts.py) selects definition prose, operand tables, execution conditions and limits. Exact instruction definitions take precedence over body mentions. Related units stay within their explicit section or parent and manual revision; neighboring pages are not assumed to belong to the same instruction.

When the same opcode exists in multiple official manuals, [knowledge.source_authority](../../src/knowledge/source_authority.py) reads [instruction_source_authority.json](../../resources/instructions/mitsubishi/instruction_source_authority.json). The same CPU/opcode authority rule is consumed by direct structured lookup and the broad-retrieval compatibility path; manual precedence is not maintained as a second hard-coded table.

The [scoped retrieval facade](runtime-ownership.md#retrieval) applies task and source lanes before candidate limits. CPU applicability remains owned by the index and instruction catalog.

For a form whose native order is already verified, direct lookup also follows
its existing Core contract sources by manual identity, revision and definition
page. This recovers shared D/P definitions absent from `instructions.opcode_norm`
and keeps overview examples from displacing the cited definition. It does not
verify a purpose merely because the definition's signature is verified.

## Operand usage evidence

[_operand_gap_details](../../src/knowledge/instruction_facts.py) requires purpose
evidence independently of role, type, symbol/order and device-class evidence.
Other declared usage facets retain their own gaps until source-verified. An
instruction-wide signature or role promotion cannot certify an operand purpose.

[_pack_target](../../src/knowledge/instruction_facts.py) receives these slot/facet
gaps. Operand tables bind by unique symbols in the verified native order,
preserving the original table header, rows and following notes. Tables flattened
into layout text can be recovered without delivering neighboring conflicting
diagram syntax. Missing symbols may use `operands_json` description associations
only when a unique matching description occurs in the original table. Repeated
symbols, merged/ambiguous rows and description cells containing only data types
leave the affected purpose unresolved.

Each `operand_evidence_bindings` entry identifies position, facet, candidate
value and source. Row/context spans use the original `chunks.text` when it is
available; otherwise `offset_basis` explicitly names `resolved_record.text`.
Manual identity, revision, PDF page and selected CPU/form remain attached.
Flattened multiline descriptions bind through an explicit native symbol;
`value_spans` identifies each original fragment used to reconstruct their value.
Font placeholders are removed only from symbol identity. Indexed descriptions
may recover a missing symbol but cannot override a symbol present in the source.
Runtime binding supplies candidate evidence, not semantic certification. The
role/type promotion audit and its existing acceptance criteria remain separate.

[arbitrate_operand_usage](../../src/plc/instruction_semantics.py) owns candidate
resolution for each slot/facet. Verified owners take precedence. Equivalent
candidates merge their sources; different values or condition scopes leave the
facet unresolved. Whitespace is normalized, without assuming equivalent units
or rewriting conditions. An explicit candidate supersedes an unverified
declaration without becoming source-verified.

Packing checks all candidates before source ordering or budget selection.
Conflicting source units and overlapping renditions are quarantined together.
`operand_candidate_conflicts` retains the competing values and sources in the
diagnostic receipt. Agent B receives only the unresolved facet, reason and
candidate count; cached index `OPERANDS` summaries cannot reintroduce a discarded
meaning through the structured prefix. Unrelated slots/facets remain independent.

## Packing and receipts

Evidence is packed as complete source units within the available allowance. Tables and source identities remain intact; duplicate diagram/layout renditions do not replace missing prose. Records retain manual identity, revision, offsets and content hashes where available.

[knowledge.fact_coverage](../../src/knowledge/fact_coverage.py) is the canonical delivery-accounting layer for exact instruction, device and error facts. It projects targets into dimension-addressed requirements, records candidate source IDs, and reconciles them again after final prompt compilation using complete block identities and hashes. `candidate_evidence`, `budget_omitted` and `unresolved` retain different meanings.

[delivered_fact_report](../../src/knowledge/instruction_facts.py) remains a compatibility view for existing instruction diagnostics, but its status calculation delegates to the generic coverage owner. A recorded keyword category is a packing hint; a fact is only marked as delivered when its block survives compilation. Missing facts remain visible without creating an instruction prohibition or inventing an I/O. Context compilation is owned by [application.context_compiler](../../src/application/context_compiler.py).

Slot requirements use dimensions such as `operand:3:purpose` through the same
coverage owner, including exact instruction-instance matching. The compatibility
receipt exposes them in `operand_facts`; canonical `fact_coverage.requirements`
contains both coarse questions and these slot/facet requirements. A table is
delivered once while its explicit bindings can answer several requirements.
Whole-block removal during final compilation changes delivered candidates to
`budget_omitted`; a delivered table never supplies evidence to an unbound slot.

## Representation and measurement

Required compact fields are rendered from [application.compact_protocol](../../src/application/compact_protocol.py). Local aliases preserve the same representation; polarity and scan behavior are engineering semantics, not syntax cleanup.

[test_instruction_fact_context.py](../../tests/test_instruction_fact_context.py) covers source integrity, all row permutations of a small frozen binding table and final-budget reconciliation. [test_generation_agent_boundary.py](../../tests/test_generation_agent_boundary.py) captures the actual offline Agent B request after context compilation and checks bound purposes, source references and execution form with changed addresses/values. These checks establish delivery, not model reasoning or PLC behavior. [test_agent_b_measurement.py](../../tests/test_agent_b_measurement.py) covers the paired experiment runner. The runner's usage and comparison groups are documented in [Agent B measurements](../guides/agent-b-measurement.md).

`python tools/audit_operand_semantics.py --purpose-coverage --report <path>`
replays all forms in the existing FX3U signature ledger through the shared
knowledge context. It records every position's purpose status and failure bucket
separately from role/type promotions, without a provider call or index mutation.
The [2026-10-03 coverage report](../reports/2026-10-03-fx3u-operand-purpose.md)
records its denominator, parser baseline, remaining failures and verification
limits. Final context compilation retains its separate budget reconciliation.
