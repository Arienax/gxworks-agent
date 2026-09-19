# Explicit analysis modes

`analysis_mode` is a per-analysis job input: `direct` (default) or `design`.
It is not a PLC generation contract, an approval mode, or a complexity score.
Only the submitted field enables architecture exploration. Request keywords,
missing parameters, instruction names and the existence of a selected approach
must never enable or disable Design on their own.

## Call path

The Web composer sends the selected mode with an analysis job. `JobCreate`
defaults older clients to Direct. `WorkbenchService.submit` copies the command,
resolves the default before hashing it, and the job snapshot freezes the value.
Both analysis API functions pass it to the same prompt assembler. The output
records the application-selected mode; model-authored mode metadata is ignored.
Changing the picker affects the next submission, not a running job. A new
project/page starts in Direct. Qt stays frozen; shared old callers get Direct.

Direct assembles the common JSON/intent/missing-parameter contract, the compact
Direct contract, relevant PLC facts and targeted fact evidence. It requests one
concrete implementation. An existing `selected_approach` adds the internal
pinned/extract delta, preserving unchanged implementation semantics and explicit
constraints. No selected approach is needed to use Direct. Brief description
and empty pros/cons are allowed; the generation guide remains complete.

Design adds the Design contract and explicitly enables the design evidence lane,
while preserving fact evidence. It offers 1–3 genuinely distinct candidates.
An existing selected approach does not force pinned mode. Exploration does not
clear confirmed I/O, parameters or unreleased user constraints, and does not
replace the confirmed specification before normal review.

The fact router still identifies devices, opcodes and hardware topics. Its
internal `pinned` state only refines Direct. The application knowledge builder
and the low-level retriever no longer infer a mode at all: omitted/None
`include_design` is false. Curated design chunks remain excluded from the fact
lane. Candidate-specific fact collection is retained and adds no model call.

## Confirmation and compatibility

The existing Core review-draft builder already preselects the sole approach.
Real required parameter questions stay unanswered until confirmed; suggested
defaults are not answers. No approach-count/description-length rejection gate,
automatic regeneration or additional approval step is introduced. Counts and
brevity are prompt requirements, not a promise of perfect model compliance.

Mode is not copied into the confirmed specification or `generation_contract`.
Generation/other jobs ignore it. A retry must retain its original mode. For an
old command record with no mode, a default-Direct retry may return the original
job without reinterpreting it or issuing another request; an explicit Design
request cannot reuse that request ID. Existing approval and native-operation
boundaries are unchanged.

## Regression checks

Run the affected suites in the repository environment:

```sh
python -m pytest -q tests/test_analysis_prompt_routing.py tests/test_analysis_prompt_integration.py tests/test_analysis_design_rag.py tests/test_analysis_mode_jobs.py tests/test_analysis_format_repair.py tests/test_confirmed_reconfirmation.py
cd web
npm run types
npm run build
```

Cover default/explicit Direct, Design, selected/unselected baselines, both
streaming APIs, no design retriever calls or design-as-fact leakage in Direct,
separate queries and retained facts in Design, snapshot/retry identity, and
required-parameter confirmation. No paid model or actual PLC operation is
needed for the mocked cases; the bundled-RAG tests use the local knowledge DB.
