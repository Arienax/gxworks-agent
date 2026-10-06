# Instruction-fact delivery

## Definition ownership and model differences

[InstructionSpec](../../src/plc/instructions.py) remains the single Registry
owner. [instruction_definition](../../src/plc/instruction_definition.py) covers
identity/applicability/version, typed parameters, execution/lifecycle,
effects/state, memory/layouts, resources/protocol and constraints/errors. Each
fact has its own CPU/form/language/version scope, dependencies, source locations
and status: unknown, not applicable, declared, candidate, source checked or
conflicted. A verified operand order does not verify its purposes or effects.

[instruction_definition_storage](../../src/plc/instruction_definition_storage.py)
stores schema 2 as `common + form_diffs + model_diffs`. Model lists appear once
in `model_profiles`; each family references a profile through its `owners`.
Dictionary field differences are recursive, arrays are replaced atomically,
and deletion uses the reserved `{"$remove":true}` marker. Runtime expansion at
Registry loading preserves every owner and value; it creates no second Registry.
Overlapping or unowned overrides are rejected. Source-checked fact scopes and
review receipts stay literal, so adding a CPU to a profile cannot inherit a
different CPU's verification. Runtime admission remains separate from source
catalogue presence and from actual native/hardware acceptance.

Registry keeps the full compiled owner inventory alongside callable specs.
`definition_entry` and the ordinary task-view resolver expose manual-only or
CPU-excluded entries through the same pipeline, including their source material
and gaps. Querying those entries does not admit their mnemonic into generation.

## Build-produced source definitions

The existing [knowledge builder](../../tools/build_fx3u_knowledge.py) invokes
[instruction_compiler](../../src/knowledge/instruction_compiler.py). The semantic
stage also runs with `--compile-definitions-only`, reading the existing SQLite
without changing it. [instruction_document](../../src/knowledge/instruction_document.py)
handles table cells, native symbols, cross-line reconstruction and complete
typed result charts during construction, without an opcode/CPU parser branch.

The input inventory is the union of Registry forms and literal official chapter
headings. Chapter ownership follows manual/revision, numbered leaf sections and
explicit shared-base definitions; an incidental opcode in a reading guide does
not own a chapter. References resolve by exact section identity with one hop and
a bounded count. Unresolved references remain visible. Original page artifacts,
geometry references, character spans and remaining uninterpreted intervals stay
addressed to SQLite; raw manuals are not replicated into definitions. The source
catalogue records manuals covering several models, while each fact retains its
actual applicability and verification scope.

Native forms come from the literal mnemonic column, separately from the family
icon. A family icon such as EADD does not establish a callable EADD when that
column lists DEADD. Source-reviewed exclusions give the selected CPU's actual
replacement without extending that review to other CPUs. Source-declared D/P
families share facts and form diffs; width and trigger are distinct modifiers.
Result footprints belong to each effect: MUL consumes signed16 values and writes
signed32 (two words), while DMUL consumes signed32 and writes signed64 (four
words). Input width does not imply result width.

Source-configured `instruction_layout.model_badges` locates header model marks
in the local PDF. Geometry, fill color and exact manual/revision/page remain in
candidate evidence; missing or ambiguous marks cannot create CPU support. A
future manual supplies its own template rather than adding an FX3U parser rule.
Standalone symbols without adjoining description cannot bind sidebar text;
multiline footnotes remain separate from wrapped row descriptions. Malformed
delimiters preserve the original source but cannot create a complete purpose.

## Targets and retrieval

Generation and editing also use [functional capability discovery](capability-selection.md).
This shares the existing retriever and definitions, supplies budgeted option
briefs and records evidence gaps; it creates neither a parallel index nor a
manual fact workflow for each opcode. Exact user calls retain priority.

[build_confirmed_generation_context](../../src/application/confirmed_generation_context.py) creates the shared generation context. [instruction_fact_targets](../../src/knowledge/instruction_facts.py) identifies instructions from the selected contract, positive selected-plan text and catalogued anchors. Targets select evidence; they do not add required opcodes or choose an analysis mode.

[retrieve_instruction_facts](../../src/knowledge/instruction_facts.py) selects definition prose, operand tables, execution conditions and limits. Exact instruction definitions take precedence over body mentions. Related units stay within their explicit section or parent and manual revision; neighboring pages are not assumed to belong to the same instruction.

When the same opcode exists in multiple official manuals, [knowledge.source_authority](../../src/knowledge/source_authority.py) reads [instruction_source_authority.json](../../resources/instructions/mitsubishi/instruction_source_authority.json). The same CPU/opcode authority rule is consumed by direct structured lookup and the broad-retrieval compatibility path; manual precedence is not maintained as a second hard-coded table.

The [scoped retrieval facade](runtime-ownership.md#retrieval) applies task and source lanes before candidate limits. CPU applicability remains owned by the index and instruction catalog.

For a form whose native order is already verified, direct lookup also follows
its existing Core contract sources by manual identity, revision and definition
page. This recovers shared D/P definitions absent from `instructions.opcode_norm`
and keeps overview examples from displacing the cited definition. It does not
verify a purpose merely because the definition's signature is verified.

Compiled chapter lookup verifies manual, revision, source ID and the numbered
leaf heading against the hierarchical index section. It returns chapter units
in source order, preferring the configured semantic manual, rather than a
historical first body mention of the mnemonic.

## Operand usage evidence

[_operand_gap_details](../../src/knowledge/instruction_facts.py) requires purpose
evidence independently of role, type, symbol/order and device-class evidence.
Other declared usage facets retain their own gaps until source-verified. An
instruction-wide signature or role promotion cannot certify an operand purpose.

Build-time [_operand_facts](../../src/knowledge/instruction_compiler.py) receives
slot/facet gaps. Operand tables bind by unique symbols in the verified native order,
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
Fragments join with a space by default. A span's optional `join_before: ""`
records a source-supported adjacent-cell word join. The binder requires another
operand-table rendition in the same source record, manual revision and PDF page
to contain the exact complete description on the same explicit native-symbol
row. Both split fragments retain their original offsets; different negation,
units, values, pages or symbols cannot supply that witness. This reconstruction
does not relax candidate arbitration or certify the purpose.
Font placeholders are removed only from symbol identity. Indexed descriptions
may recover a missing symbol but cannot override a symbol present in the source.
Table interpretation supplies candidate evidence, not semantic certification. The
role/type promotion audit and its existing acceptance criteria remain separate.

[arbitrate_operand_usage](../../src/plc/instruction_semantics.py) owns candidate
resolution for each slot/facet. Verified owners take precedence. Equivalent
candidates merge their sources; different values or condition scopes leave the
facet unresolved. Whitespace is normalized, without assuming equivalent units
or rewriting conditions. An explicit candidate supersedes an unverified
declaration without becoming source-verified.

Construction checks all candidates before source ordering or budget selection.
Conflicting source units and overlapping renditions are quarantined together.
`operand_candidate_conflicts` retains the competing values and sources in the
diagnostic receipt. Agent B receives only the unresolved facet, reason and
candidate count; cached index `OPERANDS` summaries cannot reintroduce a discarded
meaning through the structured prefix. Independent row facts can still be packed
without repeating the contradictory raw unit; unrelated slots/facets retain
their own arbitration.

## Packing and receipts

Evidence is packed as complete source units within the available allowance. Tables and source identities remain intact; duplicate diagram/layout renditions do not replace missing prose. Records retain manual identity, revision, offsets and content hashes where available.

[instruction_task_view](../../src/plc/instruction_definition.py) chooses task
dimensions and complete dependency closures. A result mapping brings its types,
parameter identity, enable and disabled behavior together. Equal result formulas
with different conditions or retention are not deduplicated. Source checked
facts may be preferred only when the complete semantic dependency closures are
equal. The compact fact packet keeps source details in its detached manifest.

The runtime packs an atomic instruction packet before uninterpreted source
units. It meters final markers and conflict annotations as well as fact text;
budget cuts omit complete packets. The original order-conflict filter remains
in force for raw layout evidence. When the final compiler supplies a token
allowance, the instruction packer uses the same deterministic token estimate
for dependency closures, source units and markers. Its per-target allowance
accounts for the number of primary packets that can reach final top-k; long
source prose cannot consume another primary packet's share. The final compiler
still enforces the total budget and reports omitted facts.

The primary top-k limits instruction, device and error packets. Explicit
FIRST_SCAN, TIMER and COUNTER needs from the confirmed specification use a
separate, bounded dependency lane (at most three packets) inside the same
character and token allowances. This prevents five primary instructions from
displacing their needed process evidence when budget remains. The manifest
records `primary_top_k` and `process_dependency_ids`; final compilation still
reconciles each complete packet. These are candidate sources, not verified
behavior or a broader background-retrieval query.

`relation_evidence_groups` is a compatibility
projection of compiled conditional-result facts, with typed expressions,
dependent lifecycle facts and original source locations. There is no runtime
`_cmp_relation_groups` or online operand-table interpreter.

[knowledge.fact_coverage](../../src/knowledge/fact_coverage.py) is the canonical delivery-accounting layer for exact instruction, device and error facts. It projects targets into dimension-addressed requirements, records candidate source IDs, and reconciles them again after final prompt compilation using complete block identities and hashes. `candidate_evidence`, `budget_omitted` and `unresolved` retain different meanings.

[delivered_fact_report](../../src/knowledge/instruction_facts.py) remains a compatibility view for existing instruction diagnostics, but its status calculation delegates to the generic coverage owner. A recorded keyword category is a packing hint; a fact is only marked as delivered when its block survives compilation. Missing facts remain visible without creating an instruction prohibition or inventing an I/O. Context compilation is owned by [application.context_compiler](../../src/application/context_compiler.py).

Slot requirements use dimensions such as `operand:3:purpose` through the same
coverage owner, including exact instruction-instance matching. The compatibility
receipt exposes them in `operand_facts`; canonical `fact_coverage.requirements`
contains both coarse questions and these slot/facet requirements. A table is
delivered once while its explicit bindings can answer several requirements.
Whole-block removal during final compilation changes delivered candidates to
`budget_omitted`; a delivered table never supplies evidence to an unbound slot.

The instruction adapter derives `operation.result_mapping` and
`execution.disabled_retention` requirements from compiled conditional-result
facts. These dimensions require a complete group and all declared output members;
an `operation` keyword hit cannot satisfy them. `relation_evidence` records source
hints, complete candidate groups, conflicts and packing status before generic
coverage tracks the packed block and reconciles it after final compilation.
Source hints do not establish completeness, and any gap remains diagnostic rather
than prohibiting generation or starting an unlimited retrieval loop.

Definition receipts separately record processed sources, uninterpreted content,
evidenced facts, source-checked facts, selected closures, packed facts, final
delivered facts and generation conformance. Final delivery is reconciled for
the exact instruction/model, so repeated local fact IDs in different families
cannot certify one another. Whole-instruction semantic completeness is not
established merely because these per-fact stages are available.

## Confirmed operation binding and checking

Agent A may propose typed `operation_intent_claims`. Core forces these to
candidate status and records exact evidence grounding; they do not confirm
themselves. The specification editor shows target effects, typed values,
enable, trigger, named parameters and necessary state. Only explicitly selected
`confirmed_operation_intent_ids` pass through the user confirmation boundary.
Existing confirmed I/O, native instances and forbidden opcodes remain binding.

[instruction_binding](../../src/plc/instruction_binding.py) matches typed
expressions against scoped definitions and resolves native order deterministically.
`OP <id>` is a literal compact output reference. The independent
`Core_operation_binding` stage materializes it after representation normalization
and before existing compact-to-ladder/IR expansion, with input/output receipts
and zero model calls. Unknown or ambiguous effects retain a visible diagnostic;
the formatter never swaps operands or rewrites control logic.

The same Core stage handles native compatibility outputs in compact, long-key
compact and ladder_v1 representations. For a single designated call and a unique
source-checked binding, it can reorder the same read values to satisfy confirmed
effects. It preserves the destination, values and enable path, respects explicit
native-instance constraints, and records `native_read_parameter_rebinding` with
both calls. Duplicate calls, ambiguous facts and changed addresses/values remain
visible to validation rather than being guessed. Typed constant equivalence uses
the declared width; a signed hexadecimal bit pattern does not make an
out-of-range decimal literal valid.

Native compatibility calls receive the same checks: exact effect call occurrence,
known equivalent whole effects, enable under explicitly confirmed bit
preconditions, full consecutive result regions and additional write overlap.
An extra instruction with an unknown write extent leaves conformance unresolved.
Candidate effect dependencies leave conformance unresolved. Unknown semantics do
not trigger an unlimited repair loop or certify a result. Generation metadata
retains the Core-stage receipt alongside the original model response audit.

`bind_confirmed_predicates` is a separately recorded semantic stage for a
uniquely designated closed control structure with explicit physical input
levels. It may bind existing NO/NC contacts at the same addresses to those
confirmed predicates; it does not add contacts or infer an unconfirmed control
requirement. Shared inputs enter the same required-device and structure checks
as header and branch inputs.

[instruction_effects](../../src/plc/instruction_effects.py) provides bounded
expression writes, conditional results, range copies/shifts, state updates,
parameter layouts and external-action contracts. Finite typed arithmetic has
explicit overflow policy; unspecified overflow remains unknown. Stateful
reference tests inspect execution sequences. External observations describe
call/protocol contracts without touching hardware. Pulse sequence acceptance,
special-memory modes, flags and external timing are established only within
each fact's declared scope; native compilation and physical device behavior
require their own evidence.

The authored FX3U `SEGD` effect uses JY997D16601 Rev.R, PDF pages 434–435:
it decodes the source's low nibble, clears destination bit 7 and preserves the
upper destination byte. Its exact form/model scope does not verify `SEGDP` or
another CPU. [digit_specified_devices](../../src/plc/device_identity.py) and the
bounded scan machine cover the documented 16-bit K1–K4 bit-device groups for
FX3U, including octal X/Y addressing; wider/indexed groups remain unverified.
The source, dependency closures and independent regression expectations retain
their respective ownership.

Source-scoped Boolean guards preserve unknown runtime values while reporting
known constraint violations, including zero divisors and excluded signed
division boundaries. A decisive false/true operand resolves conjunction or
disjunction without inventing the other operand. Explicit disjoint-region
subsets check complete word footprints and reject overlapping exchange regions
from that subset; they do not assert hardware behavior for excluded cases.

## Representation and measurement

Required compact fields are rendered from [application.compact_protocol](../../src/application/compact_protocol.py). Local aliases preserve the same representation; polarity and scan behavior are engineering semantics, not syntax cleanup.
The exact outer `type:"json_object"` annotation can be removed with a recorded
normalization receipt; conflicting fields and other annotation values still fail.

The request renderer places stable protocol, model capability, evidence and
examples before changing confirmed specifications, with stable specification
key order. Budget and source authority remain unchanged. Benchmark cache
accounting reads actual provider token fields and keeps missing fields unknown;
token-weighted cache hit rate is separate from correctness and elapsed time.

[test_instruction_fact_context.py](../../tests/test_instruction_fact_context.py) covers source integrity, all row permutations of a small frozen binding table and final-budget reconciliation. [test_generation_agent_boundary.py](../../tests/test_generation_agent_boundary.py) captures the actual offline Agent B request after context compilation and checks bound purposes, source references and execution form with changed addresses/values. These checks establish delivery, not model reasoning or PLC behavior. [test_agent_b_measurement.py](../../tests/test_agent_b_measurement.py) covers the paired experiment runner. The runner's usage and comparison groups are documented in [Agent B measurements](../guides/agent-b-measurement.md).

`python tools/audit_operand_semantics.py --purpose-coverage --report <path>`
replays all forms in the existing FX3U signature ledger through the shared
knowledge context. It records every position's purpose status and failure bucket
separately from role/type promotions, without a provider call or index mutation.
The [2026-10-03 coverage report](../reports/2026-10-03-fx3u-operand-purpose.md)
records its denominator, parser baseline, remaining failures and verification
limits. Final context compilation retains its separate budget reconciliation.
