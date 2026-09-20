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
and empty pros/cons are expected. The raw request and structured specification
remain complete; `generation_guide` is only a delta for non-obvious
approach-specific semantics that cannot be reconstructed from those fields.
For ordinary direct/self-hold logic it should normally be empty.

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
defaults are not answers. An unresolved address is not duplicated into
`suggested_io`, and tightly coupled address/polarity facts may share one
question. Generic PLC-behavior assumptions are not required. No approach-count/description-length rejection gate,
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


## Model output versus Core metadata

Agent A authors only `summary`, `approaches`, `missing_info`, `suggested_io`,
`hardware_config`, and `assumptions`. `control_type` is retired.
`format_diagnostics` is produced by normalization, and `execution_semantics`
is extracted by Core from user evidence; neither is requested from the model.
Confirmed execution semantics, original requests and selected-plan meaning
remain in generation context. The Design structure vocabulary is rendered from
`plc.specification.approach.SUPPORTED_STRUCTURES`, not maintained twice.

`flowchart_steps` was a legacy Qt display field, not the current program IR.
New analyses do not generate it. Old stored snapshots are not rewritten, and
old responses containing it still parse; it is not fed back into the normal
analysis baseline. No new SFC derivation or Qt feature is implied.
The generic debug prompt uses the selected model's evidence rather than an
unconditional FX3U special-device checklist. FX3U-only native simulation and
bounded repair/authorization restrictions remain unchanged.

## Known-address polarity questions

An `io_binding` identifies the physical point; it does not require every answer
to repeat its address. A question such as `X003 停止按钮的触点极性是？` accepts
`常闭（按下为 OFF）`. Core resolves its current owning row, preserves the answer,
and derives the active/inactive input levels. Those levels are not ladder
NO/NC instructions. Edited addresses and independent purpose labels follow the
same identity; stale answers cannot restore a deleted owner. A generation
projection that omits parameter metadata recovers the existing identity from
binding provenance rather than allocating a duplicate binding.

Legacy untyped polarity questions can reuse their single explicit X address;
Core does not infer an address from options, model defaults or an unrelated row.
Wrong-kind/multiple explicit addresses remain errors. Device-associated register
semantics (such as the meaning of `D0=0`) stay ordinary parameters, not address
selection. Real unanswered necessary parameters still require confirmation.
No additional model call, retry, user approval or reasoning-effort override is
introduced by this compatibility path.

The regression owners are `test_spec_choice_metadata.py` for identity/alias/
polarity matrices and `test_confirmed_reconfirmation.py` for one screenshot-
shaped HTTP analysis/save/edit/generate sentinel through IR, SVG and CSV. These
are offline provider fixtures, not Windows/GX Simulator2 or physical PLC proof.
