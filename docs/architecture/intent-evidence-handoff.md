# Intent and evidence handoff

Audit baseline: `cbe046246d10238dfbcea847d0cfa3e79e685040` (main after PR #17).
Scope: analysis, candidate presentation, confirmation, confirmed-spec projection,
RAG, built-in ladder Agent B/full-wire generation, shared tools, existing repair,
and saved-version delivery. This is not a new semantic verifier or a claim of
GX Works2/native/physical PLC acceptance.

## Problems found at the baseline

| Boundary | Failure mechanism | Change |
| --- | --- | --- |
| User request → analysis | Original wording was concatenated into a model summary rather than carried as an independent source. | Store ordered caller-supplied requests in `engineering_context.requests`; ignore model-authored origin fields. |
| Analysis → candidate selection | Low-level guessed constraints were removed, and an entire candidate was discarded when all hard lists became empty. | Keep the candidate and its guide; retain the original proposed choices as `implementation_preferences` with `source=model_proposal` and `enforce=false`. Preserve the existing hard-constraint screening. |
| Partial contract → normalization | Omitted fields of a nonempty structured contract could be inferred from prose, accidentally upgrading a suggestion. | Do not fill explicit partial contracts from the guide. Missing/empty historical contracts retain the existing inference compatibility; keep their `inferred` source after repeated normalization. |
| Candidate → confirmation | No durable distinction between original proposal and an operator-edited selection. | Record the original proposal hash, selected hash, confirmed-field hash, request IDs, and `model_proposal`/`user_modified_proposal`/`unrecorded_origin` at the existing confirmation write. |
| Confirmation → generation | Shared projection explicitly deleted `name`, `description`, and `generation_guide`. | Preserve chosen plan semantics and selected source records; remove alternatives, hidden reasoning, drafts, provider state, and private data instead. |
| Specification → RAG | Every string was cut at 600 characters; alternatives/bans/hashes could enter the positive query. Design chunks could consume factual evidence capacity. | Query positive selected engineering fields and ordered requests; use an explicit overall budget, disclose truncation, reserve factual capacity, and preserve whole source blocks. |
| Retrieval → later stages | Prompt strings lost source IDs, source type, page/revision, inclusion status and content identity. | Backward-compatible `KnowledgeContext(str)` carries an engine-produced manifest of actually included blocks. |
| Context → built-in Agent B | Context construction and later persistence were not one tracked handoff. | Build once, pass the detached shared context to the prompt, and capture the exact receipt before the single model call. Clear unrelated first-generation chat history in the full-wire adapter too. |
| Tool result → model | Arbitrary 18,000-character slicing could produce invalid JSON and cut engineering evidence. | Serialize the full allowlisted result. Existing retrieval and tool-specific count budgets remain. |
| Tools → candidate → save | A context result and subsequent externally generated candidate had no source association. | An optional opaque context ID resolves only engine-issued, snapshot-bound receipts in a bounded runtime-local cache. No receipt or stale receipt is a trace gap, not an acceptance error. |
| Contract repair | The repair plan hash included the contract, but the actual prompt did not include that contract object. | Send the existing contract and source references with the already bounded baseline/scope. No fresh RAG or unrelated raw history is replayed. |
| Failed candidate → diagnostic version | Salvage kept ladder/CSV artifacts but could lose generation origin. | Preserve the same receipt in staging and the diagnostic version; damaged/missing metadata does not discard the candidate. |
| Saved version → delivery | Source information could disappear or be confused with the live project. | Deliver the version-bound specification and handoff, not the subsequently edited live specification; report origin/retrieval gaps explicitly. |

## Source roles, not another constraint layer

`engineering_context` is an additive specification field. It records:

- `requests`: application-captured user wording and stable IDs, ordered by revision;
- `proposals`: original model proposal identity and bounded post-analysis retrieval
  references (at most three candidate lookups, no extra model call);
- `analysis_evidence`: references injected for analysis;
- `confirmation`: what was accepted or edited at the normal confirmation write.

The model cannot authenticate an origin by emitting `engineering_context` or
`implementation_preferences`: analysis normalization discards those model-owned
fields and the application supplies its own. Confirmation hashes are consistency
and lineage identifiers, **not cryptographic proof of authorship or immutable
security attestations**. The existing proposal/version binding remains responsible
for acceptance consistency. A historical spec without sources stays readable;
source history is not fabricated from old assistant text.

`selected_approach.generation_guide` expresses the selected engineering method.
`implementation_preferences` preserves model-proposed opcode/device/structure
choices that are not promoted into hard obligations. Selecting an approach does
not retroactively turn that wording into the user's original request. An empty
`required_opcodes` (or all-empty hard lists) does not erase the selected method.
The definition check no longer rejects a proposal solely for empty lists;
contradiction checks, recognized structures, missing selection checks, and
existing downstream safety/consistency checks remain.

Current confirmed fields override superseded addresses/parameters from earlier
requests. Later explicit user amendments override earlier requests. The original
proposal hash remains the identity for its original retrieval; an edited selected
hash is distinguishable. Evidence is a technical reference, not user intent, not
an additional opcode obligation, and not proof that the candidate is correct.

## Retrieval and budgets

The generation query uses selected representation/guide, positive choices and
contract fields, newest request first, confirmed I/O, parameters and execution
semantics. Unselected approaches and structured forbidden lists are not dumped
into the positive query. Free-text requests may still legitimately mention a
rejected method; this code does not pretend to solve natural-language negation.

Per-string 600-character clipping is removed. The query remains capped at 24,000
characters, includes zero-valued numeric parameters, keeps a head/tail window for
an overlong fragment, and reports `query_truncated`. This is a bounded retrieval
query, not a lossless replacement for the full saved specification. The full
selected specification is passed separately to generation.

Analysis design references have at most two slots and one third of the character
budget. Factual retrieval gets the remaining space and unused design allocation;
it recalls a bounded larger pool so an oversized first chunk does not hide a
shorter usable reference. Included blocks are not cut mid-source. Manifests record
only actually injected sources and omitted IDs; source roles remain separate:
`technical_reference`, `design_reference`, `supporting_reference`.

Manifests keep available index IDs, source/manual identity, page/section/revision,
content hash and prompt-context hash. Missing metadata stays missing. An empty
low-level retriever result can mean no result or an unavailable index, so the
status is deliberately `empty_or_unavailable`, not a fabricated successful lookup.
`excluded`, `unavailable`, `budget_limited`, `not_recorded` and `untracked_text`
also remain distinct. Hashes and references are **not an archival copy of complete
manual passages**. Candidate evidence collection occurs after analysis; it does
not mean Agent A used those later references or that a second model validated
its proposed method.

## Built-in and external paths

The compact Agent B and full `ladder_v1` adapters share the same selected
projection and source boundaries, not the same completion syntax. Built-in
confirmed generation still makes one model generation call and enters the
existing `generation_structural` parser/compile/save route. This patch adds no
semantic retry loop, mandatory instruction, new validator, or CI gate.

`get_generation_context` additionally returns `generation_context_id` and
`generation_handoff`. A client may echo that ID to `create_program_candidate`.
Only the cache's own record for the same project/version/spec/program/model is
attached; arbitrary model-supplied manifests are not accepted. The cache holds
at most 32 copied receipts, is thread-safe and process-local, and may evict them
or disappear on restart. A missing, expired, foreign or stale ID produces
`not_recorded`, never an additional generation failure. Even a matched ID records
`external_model_use=not_observed`: the server can prove what it returned, not what
an external model read. Candidate confirmation and GX/native authorization are
unchanged.

Repair keeps the same authorized rung/address scope and the existing semantic
freeze. Its receipt references the baseline generation evidence but marks fresh
repair RAG excluded. Diagnostic salvage keeps the failed candidate's receipt and
validation state, rather than replacing it with current retrieval results.

The Web editor only displays caller text, selected method, advisory choices and
source records. No PLC semantics were added to TypeScript or C#. Qt remains
legacy/frozen; shared storage/workflow updates do not add Qt feature parity.

## Regression corpus and validation

`tests/fixtures/call_chain/intent_cases.json` is a reduced **source-handoff** corpus:
word/category stream, boolean presence stream, batch counter, register sequence,
one-shot delayed event and engineering-unit conversion. The word-stream case is
derived from the supplied WSFL failure pattern, but no runtime branch matches
WSFL or any other individual instruction. Raw traces, hidden reasoning, credentials
and local paths are not copied into the repository. Fixtures do **not** assert
that their illustrative opcode recipes are correct.

`tests/test_intent_evidence_handoff.py` covers original caller provenance,
selected/edited alternatives, deep-copy and normalization idempotence, empty and
partial contracts, long queries/zero values, source inclusion and budgets,
compact/full/tool projections, one model call, context receipt isolation and
expiry, existing repair scope, diagnostic recovery, and frozen-version delivery.
Provider tests use offline fixtures; they verify handoff and orchestration rather
than real-model instruction quality. Existing structural, reconfirmation, API,
MCP and frontend suites remain independently useful.

Run the existing confirmed-generation workflow for its dependency-complete Web
and generated-type checks. Local extended testing additionally exercises the RAG
and scoped-repair suites. One known baseline failure was reproduced unchanged in
an isolated base worktree:
`tests/test_fx3u_rag.py::test_st_prompt_uses_only_the_selected_model_special_device_prefixes`
expects the literal `GX Works3` in the FX5U/ST base prompt. It is not added to the
existing workflow or hidden by changing that test in this patch. Windows/GX Works2,
GX Simulator2, physical PLC behavior and live paid-model acceptance remain
separate, unperformed integration checks for this change.
