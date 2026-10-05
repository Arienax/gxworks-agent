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
must quote a span containing every reported device, including equivalent address
spellings; addresses elsewhere in the request cannot fill gaps in that span.
Button purpose alone cannot supply an unconfirmed trigger edge or active level.
Agent A never emits the final execution enum directly. Formal notation such as an explicit
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

The first exact-instance requirement for an opcode replaces its prior-turn calls;
additional calls of that opcode in the same operation batch accumulate. Explicit
later clear/forbid operations still take effect in order. Three SFTL calls specified
together therefore remain three exact instances in every approach's contract.

## Analysis protocol repair

`plc.specification.explicit_constraint_claims.TARGET_CONTRACTS` defines the fields,
operations and examples for `opcode`, `device`, `instruction_instance` and
`category`. Both prompts and the validator use this table. An exact instance has
`opcode` and `operands` directly in the target; category supports only `clear`.
`AnalysisProtocolError.violations` retains its string interface, and `details`
adds paths, actual values and the applicable Core contract. Device evidence uses
canonical device identity, including `Y000/Y0`, with the existing token boundaries
and exact current-request span checks.

`application.analysis_repair` converts recognized plural target names and exact
instance lists without supplying evidence or changing meaning. It then gathers
protocol failures and individual claim-compiler rejections. The shared analysis
entry allows one further model request with an independent short prompt: current
request, failed fragments, valid shapes and identified targets. Manuals, model
profiles and analysis history are excluded from this repair prompt.

The private `analysis_repair` response contains only
`{"repairs":[{"path":"/…","value":…}]}`. Every whitelisted path must appear
exactly once. Claim replacements are arrays and may split one failed claim into
several grounded claims. Other fields are value replacements; forbidden metadata
uses null removal. The application applies baseline indices in descending order,
preserves valid operations/scopes and identified devices/opcode+operands, then
rechecks the complete merged object. No third request follows a rejected patch.
Internal patches do not reach content callbacks, provisional previews or Web/MCP
analysis objects; diagnostics retain the literal responses of both attempts.

Unbacked scan candidates can be removed. A method description is not a supported
device/opcode candidate: its exact original text is retained in the application
repair receipt and in `intent_context.requests`, which still reaches generation.
The receipt does not turn these descriptions into machine-validated device bans.
Unprojected original target values are recorded as `unresolved_values` with
`projection_status=not_projected`.
Model-authored repair receipts are discarded. JSON syntax errors use the same
one-repair budget and a separate short prompt that preserves draft contents; the
full corrected object must pass protocol and grounding checks.

Initial analyses may leave structure tags empty. They must not approximate SFTL
tracking as a register counter. Per-item registration requires process facts about
the no-material value between pieces, classification changes within a piece,
rearming and the result's consumption at Encoder shift; numerical change alone
does not identify a new piece. Button purpose/address does not establish its
active input level. These questions belong to the first analysis and confirmation,
not protocol repair. Passing a repair proves its local scope and grounding; it
does not certify frozen scheme fields as correct PLC engineering.

The initial execution frame includes a literal Core example using scalar `from`
and `to`; failed transition frames receive the same example in their error
details. A request to generate immediately does not resolve missing process
facts. Neither prompt size reduction nor successful patch acceptance establishes
lower reasoning usage or latency; first-pass acceptance is measured separately.
The relevant model profile also scopes its existing `addressing` value to X/Y
and supplies per-device radix from `plc.validation.device_address_radix`, the
same rule used by the address parser. FX3U M/D numbering remains decimal; the
analysis must not turn a correct memory range into a wiring question because
X/Y use octal spelling. Retrieval and information-stage selection are unchanged.

Core projects `implementation_semantics + explicit_user_constraints` into
`generation_contract`. Model-authored `generation_contract` is not requested for
fresh analyses. Saved legacy specifications that only contain `generation_contract`
continue through the compatibility path and are not rewritten.
