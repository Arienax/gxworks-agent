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


## Implementation semantics ownership

Fresh Agent A candidates use `implementation_semantics` as the only model-authored
machine implementation vocabulary. Items describe a structure, opcode, device, an
any-of group, or an exact instruction instance. `plc.specification.approach`
normalizes that vocabulary and projects it into `generation_contract`; model-authored
`generation_contract` is no longer requested for new analyses.

The application provenance filter runs on semantic items before projection. Low-level
opcodes, devices and exact instruction instances still need matching caller evidence;
high-level selected structures remain candidate semantics, with generic intent guards
applied by semantic kind. Removed model choices may remain visible in non-enforcing
`implementation_preferences`. Saved legacy specifications that only contain
`generation_contract` continue through the compatibility path and are not rewritten.
