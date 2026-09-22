# I/O purpose labels

## Identity and purpose

An address question and a device purpose are separate fields. `io_binding.binding_id` identifies the binding; `kind` identifies its address category; optional `label` holds a short purpose. For example:

```json
{"id":"start_device","question":"启动按钮接在哪个输入点？","required":true,"io_binding":{"binding_id":"machine.start","kind":"X","label":"启动按钮"}}
```

[binding_hint](../../src/plc/specification/bindings.py) retains this metadata through question normalization. Confirmation binds the actual answer and seeds a new row with the independent label. Question wording and model reasoning are not device comments. A missing optional label does not create a required question or an extra model call.

## Edits and aliases

An existing label, including an explicit clear, remains authoritative. Its active binding carries the purpose through address changes. Reanalysis preserves manual edits and deletions; a stale answer cannot recreate a deleted row. Canonical device aliases are shared by the I/O table, IR references, explorer view and comment export.

[application.analysis_results](../../src/application/analysis_results.py) normalizes supported grouped and flat suggested-I/O forms. The bounded declaration adapter can retain explicit `address: purpose`, `address 为/是 purpose` and `address is purpose` statements when the model omits them. It excludes state predicates, numeric classification rules and instruction operands from purpose names. Physical-level suffixes remain in the original request.

Legacy labels are not rewritten merely because they resemble questions: old records cannot reliably distinguish a generated label from a manual name. Correct an existing purpose in the specification editor before creating a new version. Historical versions remain immutable.

## Input levels

An address already present in a question need not be repeated in its polarity answer. Current binding identity resolves the owning point. Physical action levels and Ladder NO/NC instructions are distinct: an active-low stop uses a low-level action predicate and a high-level run-permit predicate.

[generation_input_conditions](../../src/plc/specification/conditions.py) derives these predicates for known input bindings. Contradictory or missing levels remain unresolved; register-value semantics are ordinary parameters, not address selection.

## Artifact path

[ConfirmedGenerationContext](../../src/application/confirmed_generation_context.py) carries current labels; compact expansion reads the I/O table and canonical IR supplies SVG and GX comment CSV. These views use the same purpose text.

[test_spec_choice_metadata.py](../../tests/test_spec_choice_metadata.py) owns identity, label, alias and polarity matrices. [test_confirmed_reconfirmation.py](../../tests/test_confirmed_reconfirmation.py) covers the HTTP-to-saved-artifact path with recorded provider responses.
