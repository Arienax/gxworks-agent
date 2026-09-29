# Intent and evidence handoff

## Current intent and audit history

The persisted engineering specification and decision history have separate owners. [CONFIRMED_SPEC_FIELDS](../../src/plc/specification/provenance.py) defines the positive specification projection; `CONFIRMED_SPEC_VERSION` and `DECISION_RECEIPT_VERSION` define their versions in the same module.

`intent_context.requests` retains ordered, application-captured user requests. The confirmed specification contains the selected implementation, current I/O bindings, parameters and execution semantics. `decision_receipt` records candidate history, selection provenance and evidence. Historical candidates and retrieval metadata are not current engineering facts.

[analysis_context](../../src/plc/specification/provenance.py) creates a fresh analysis receipt. [confirmed_spec_fields](../../src/plc/specification/provenance.py) projects current state, and [decision_context](../../src/plc/specification/provenance.py) reads current or legacy audit records. These transformations do not invent user authorship from model prose. Hashes identify content and relationships; the application owns the write that associates them.

## Confirmation and compatibility

Current structured fields take precedence over superseded addresses and parameters in older requests. Explicit later amendments take precedence over the baseline for the amendment. Selecting a model proposal retains its origin; it does not turn the proposal into the original user request.

Legacy `engineering_context` is split into intent and audit views. Writable persistence stores the migrated pair through [SessionStore](../../src/storage/session.py); read-only paths and old saved versions retain their recorded bytes and hashes. Unknown legacy material remains recoverable in audit data rather than being promoted into generation state.

I/O identity and manual label edits follow [I/O purpose labels](io-binding-labels.md). Model profile ownership is independent of project history and is defined in [runtime ownership](runtime-ownership.md).

## Generation

[ConfirmedGenerationContext](../../src/application/confirmed_generation_context.py) builds a detached view for compact and full-wire generation. It retains selected method semantics and current intent while excluding unselected alternatives, review drafts and private provider state. [application.context_compiler](../../src/application/context_compiler.py) applies the model budget to the actual prompt representation.

Retrieval contributes technical references. Included source IDs, content hashes and omission states describe the evidence delivered to the prompt; they do not add required instructions. [Instruction-fact delivery](instruction-fact-delivery.md) defines this selection and receipt contract.

The compact and full-wire adapters share this engineering view, not a response grammar. Their call path is documented in [generation call contracts](generation-call-contracts.md).

## External tools and saved versions

`get_generation_context` may issue a snapshot-bound context ID. `create_program_candidate` can correlate an echoed ID with the runtime's own receipt. A missing or stale ID records a trace gap without bypassing candidate validation. The server observes returned context and submitted candidates; external model consumption remains `not_observed`.

Saved-version delivery uses the version's specification and receipt, not the live project's later state. Failed diagnostic candidates retain their own validation status and available origin. [delivery_summary](../../src/application/delivery.py) assembles that report.

Regression coverage belongs to [test_intent_evidence_handoff.py](../../tests/test_intent_evidence_handoff.py) and [test_context_compiler.py](../../tests/test_context_compiler.py). The earlier investigation is preserved in the [process archive](../process/README.md).


## Device-comment ownership

GX Works2 device comments are display names, not requirement storage. Core derives
them from confirmed device-purpose labels, cuts at the first natural clause
separator, and caps generated/exported comments at 16 characters. Agent B's compact
wire protocol does not author device comments. Behavioral requirements remain in
intent/semantic fields instead of being copied into contact/coil annotations.

## Implementation semantics ownership

Fresh Agent A candidates use `implementation_semantics` for control-structure
semantics only. Free-form scan/event language is carried separately as
`execution_intent_claims`: Agent A supplies a non-authoritative trigger frame plus
exact spans copied from the current user request. Core verifies the evidence and
device grounding before projecting a claim into `execution_semantics`. Agent A
never emits the final execution enum directly. Formal notation such as an explicit
`0 -> 1`, `上升沿`, `首扫` or numeric cycle remains a narrow deterministic fast
path; summary/description/generation_guide prose is never re-parsed into hard
execution constraints.

Fresh Agent A candidates use `implementation_semantics` for control-structure
semantics only. Agent A does not own opcode selection, operand layout, or internal
M/D/T/C allocation. `plc.specification.approach` normalizes the structure vocabulary.

Caller-fixed low-level choices use the same claim/grounding boundary as execution
intent. Agent A emits non-authoritative `explicit_constraint_claims` with
`operation`, `scope`, a typed PLC target, and exact spans copied from the current
request. Core validates claim shape, exact evidence membership, opcode/device
identity and exact instruction form. It does not classify natural-language
required/forbidden/scoped/clear wording. Only grounded `scope=global` claims
produce structured operations; `scoped` and `ambiguous` claims remain visible in
the receipt but never become global hard constraints.

`plc.specification.explicit_constraints` owns only canonical constraint state and
structured mutation. It persists required/forbidden opcodes and devices plus exact
opcode+operands instances. Pinned reanalysis starts from persisted constraints for
the same approach and applies only grounded current-turn operations. A later global
opcode ban removes any persisted exact instance for that opcode, preventing stale
self-conflicts.

Core projects `implementation_semantics + explicit_user_constraints` into
`generation_contract`. Model-authored `generation_contract` is not requested for
fresh analyses. Saved legacy specifications that only contain `generation_contract`
continue through the compatibility path and are not rewritten.
