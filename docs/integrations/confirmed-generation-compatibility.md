# Confirmed generation compatibility

The acceptance target is a saved, usable ladder and its IR/SVG/CSV artifacts after a valid user confirmation, not a larger collection of error conditions.

## Specification identity

`plc/specification/bindings.py` binds address answers to explicit I/O identities. Stable parameter IDs migrate older start/stop/output questions; typed `io_binding` metadata supports multiple machines with identical display labels. A missing I/O table is no longer populated by successively replacing fuzzy-matched labels. Swapping addresses preserves the identities of the rows. The structured table is authoritative; `io_allocation_raw` is a derived display/legacy-import format. Consumed address answers remain available in `io_bindings` and the audit projection, but their old raw values are not replayed over subsequent direct edits.

Already-corrupted saved specifications are not silently rewritten from historical audit records. Reopen the specification, explicitly confirm the intended complete allocation, and save it again. Existing projects and versions need not be deleted. The regression fixture covers this explicit recovery.

## One generation request, several documented representations

`application/compact_protocol.py` defines `compact_ladder/1.1`. The only new empty-value conversion is optional rung `s: null` to `s: []`, with a recorded rule and path. Missing optional fields and existing empty arrays keep their established meaning. Invalid required outputs, null branch inputs, unknown wrappers, duplicate JSON keys and non-finite JSON numbers are not guessed into a program. No addresses, contact polarities or conditions are invented by this conversion.

Agent B also recognizes the existing `ladder_v1` and the repository's existing long-key compact alias. These pass through the existing converter and PLC validators rather than forcing another model call solely because the representation differs from the preferred compact form. Mixed or unknown formats are not silently unwrapped. The normal candidate/IR validation and atomic local autosave remain in place.

`model_response_format.response_plan` selects a declared response mode and streaming mode locally before the request. It reuses the scoped capability contract instead of dispatching on model names or preferring stale legacy flags. The compact strict schema requires every declared object property and uses disjoint `anyOf` alternatives. Endpoints without server-side format enforcement still receive the JSON prompt and face the same local validation. User values, explicit omission, zero, false and fractional precision are preserved. No capability scans, provider probes or hidden full-generation retries are added.

## Semantic coverage and diagnostics

For a narrowly specified, explicitly confirmed direct self-hold circuit with unambiguous start/stop/output bindings and physical stop polarity, `plc/specification/checks.py` verifies all eight Boolean states. It does not infer arbitrary machinery requirements, rewrite a candidate, or impose this gate on unsupported instruction/structure shapes. General PLC review and simulation remain separate. The original missing-stop candidate is a negative fixture, not a successful example after null normalization.

Local compact/PLC errors retain their category rather than being relabelled as an API outage. Public diagnostics keep schema paths and bounded identifiers, not raw responses or credentials. Intentional first-object stream closure is recorded separately from a provider finish signal; absent usage is not evidence of zero billing.

## Validation

`tests/test_confirmed_compatibility.py` and `tests/test_confirmed_reconfirmation.py` contain 113 parameterized cases, including 58 real HTTP confirmation-to-autosaved-artifact paths. Those paths use the actual service, provider serializer, decoder, converter, IR and renderer with a synthetic SDK-shaped endpoint. They span three response modes, three documented response representations, omitted/empty/null shared inputs and both explicitly specified physical stop polarities. Every successful path checks the control truth table, non-empty saved artifacts and exactly one model request. The original 54 HTTP cases also verify idempotent submission replay; the additional four exercise explicit re-confirmation after an I/O-table edit.

Run the read-only `Confirmed Generation Compatibility` workflow for the expanded shared-boundary regression selection. Its artifacts contain the exact tested source and JUnit results. Qt has been retired; the three former worker/IR tests now call headless generation services and are included without Qt exclusions. Synthetic endpoint coverage is not live verification of every commercial model; no private engineering transcript, credential, GX operation or physical PLC write is used by these tests.

## Resumed PR #13: confirmation edits and re-confirmation

The e655a08 checkpoint passed 430 cases in GitHub Actions run 35318441182.
The resumed review then reproduced a missing regression: changing a stop row to
X3 could be undone by its retained combined answer `X1，常闭`. The follow-up makes
the bound row authoritative when only a historical address is repeated; contact
text and explicit new address answers are still preserved independently.

Active binding provenance now follows explicit row identity, including multiple
uses of one physical input. Removed rows are not re-created by unchanged answers
and their stale bindings are not sent to generation. The original operator audit
is not rewritten. Reanalysis reuses an already confirmed answer for the same
stable question ID, never adopts a new AI default, and does not re-add an old
suggested row for the exact already-bound purpose. New questions stay unanswered.

Four additional HTTP integration paths save a specification, change a combined
stop-address/contact answer through the I/O table, re-confirm with the real hash,
and generate/save JSON, IR, SVG and CSV against the new X3 wiring. They cover both
stop polarities and streaming/non-streaming serialization, verify every Boolean
state, and assert that confirmation sends no generation request and generation
sends exactly one. Ten additional local cases cover retention, removal, shared
addresses and reanalysis; no new model regeneration or semantic rewrite is added.

Reproduced locally on Python 3.13 during resume: 123 core cases passed; the full
selected workflow group had 439 passes and 5 skips. The skips require the real
OpenAI SDK import, unavailable in that local environment. Final CI uses the pinned
Web dependency environment; consult that run's JUnit rather than counting skipped
local cases as passed. The read-only workflow also checks generated OpenAPI/types,
all three frontend test files, and the actual TypeScript/Vite build. No live model,
GX Works2 or physical PLC acceptance is claimed.

## Hardware intent and device-alias follow-up

The hardware question boundary now separates current user evidence and already
confirmed project facts from Agent-A analysis. Model-generated summaries,
questions, options, assumptions and derived flags cannot establish VFD hardware.
Ordinary contactor start/hold/stop requests discard speculative VFD-only questions
and their dependent parameters before the confirmation card. Genuine VFD or
motor-frequency requirements still ask for an unresolved command interface;
explicitly chosen interfaces are retained as facts, not asked as a new required
choice. Current explicit removal overrides an old VFD context, while unrelated
edits can retain genuinely confirmed drive facts. Cached flags alone no longer
manufacture a required question. The prompt states this scope too; the local
boundary does not rely on every model obeying the prompt.

`plc/device_identity.py` gives simple device aliases one representation without
renumbering: `X000 -> X0`, `X001 -> X1`, `Y001 -> Y1`, `X010 -> X10` (not X8).
The same normalization now covers generated ladder operands, comments, canonical
I/O rows and bindings, new IR device/reference indexes, typed semantic references,
and CSV export. Constants, quoted text, labels, polarities and network order are
not rewritten. Conflicting comment aliases retain the exporter's established
first-declaration rule, including an explicitly empty comment; source evidence
is not erased. Native GX CSV can still use its required padded spelling, with
only one comment row per physical device.

Existing saved IR and native-GX snapshots retain their source spelling and
integrity checks. The explorer creates a transient canonical view with merged
comments and actual references but returns the original saved hash. It does not
rewrite the version on read. Local/scoped patches leave unrelated historical
networks untouched, and explicit comment updates/deletions target the physical
device regardless of zero padding.

The two new regression files contain 57 cases, including four real HTTP paths
from adversarial analysis through user confirmation, generation, artifact save
and explorer retrieval. Those paths deliberately return speculative VFD questions
and mix padded/unpadded compact/full-ladder outputs; they verify no unwanted VFD
confirmation, one physical device entry, correct comments/references and saved
JSON/IR/SVG/program-CSV/comment-CSV artifacts. Model services are offline fixtures,
not live paid endpoints. Additional regressions cover aliases in typed semantics,
read-only historical projections and unchanged scoped neighbors.

Historical pre-retirement local headless selected run: 627 passed, 5 SDK-dependent skips, 3 explicitly
excluded Qt-worker integration cases. The read-only CI job includes the two new
files plus the related hardware/motion, comment export, native read, core IR,
semantic/static-analysis and existing Web/model tests. The Qt exclusions are
named in the workflow; they are not counted as passing. Final published CI
results are recorded in PR #13 after the branch is updated.

Qt retirement (2026-09-21): the historical three-worker exclusion above no longer applies. Those IR integration tests now run against `GenerationWorkflow`; the workflow contains no Qt-specific deselection. Earlier CI numbers are historical evidence, not results for the retired client.
