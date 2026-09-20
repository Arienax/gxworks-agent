# Instruction step widths: one source, two distinct meanings

## Ownership and consumers

`resources/instructions/mitsubishi/fx3u_step_widths.json` is the only runtime
step-width metadata source. `plc.instruction_steps` is its Python Core API.
The file retains all 499 original native encoding observations, their provenance,
and the historical exact lexical overrides. It adds explicit operand-width rules
and the separately identified supplemental observations. The old
`src/gxw/templates/token_opcodes_native.json` is removed, not maintained as a copy.

`gxw.token_pou` loads native encodings and lexical spellings through that API.
`gxworks2.csv_export` asks `instruction_step_width(opcode, operands, plc_model=...)`
for every emitted instruction, including contacts, comparisons, coils and applied
instructions. One `StepCursor` assigns their cumulative step labels. There is no
legacy `get_step_size`, default-one fallback or post-export delta repair.
Existing PyInstaller specifications already package the complete Mitsubishi
instruction-resource directory; no runtime research-directory dependency is added.

Instruction support, semantic operand contracts and the generation whitelist
remain in `plc.instructions`. Knowing a native byte spelling or storage width
must not promote an opcode to supported PLC execution or model generation.

## Stored width is not expected width

A GXW reader must preserve the width actually stored in its source. It must NOT
replace a positive stored width with the expected width from the catalogue.
For example, a WSFL header storing 7 remains observable as 7 even though the
ordinary four-operand form has expected width 9. The original byte stream and
round-trip behavior are unchanged. Binary arity is a different property again:
the WSFL native-table value 4 is its operand count, not its width.

The original 499 encodings continue to define exactly the existing decoder's
native-family inference scope. Supplemental exact spellings are not silently
added to that family inference. Existing exact lexical overrides are relocated
without changing their identities.

Source evidence includes `research/results/token-20260919/step-width-manifest.json`:
GetStepSize merely sums stored headers. A preset-less indexed OUT control also
has contradictory native results. Such observations do not establish execution
validity and are not used to manufacture an expected width.

## Resolution

The profile is explicitly scoped to FX3U/FX3UC. An explicit CPU survives
IR-to-ladder conversion; a legacy payload without a CPU defaults to FX3U.
Another CPU yields an unknown result, not an FX3U width silently relabeled.

Exact native spellings supply ordinary applied-instruction widths. D/P forms
are looked up as themselves; there is no universal prefix/suffix stripping or
operand-count formula. Conflicting observations stay unknown. Basic OUT/RST,
special devices and indexed/bit-selected forms use explicit operand rules with
evidence. String moves and unobserved addressing forms remain unknown.

`StepWidth` reports `steps`, `source`, `reason` and `evidence`. Unknown means
`steps=None`, never 1. Coverage is deliberately not a claim that every operand
form of every generation opcode has been verified. In particular, 32-bit-counter
OUT, arbitrary indexed applied instructions and string-width rules still need
appropriate native/manual evidence.

## Non-blocking unknown handling

The CSV exporter preserves all instructions and their operands. After an unknown
width it leaves subsequent absolute step labels blank rather than inventing an
offset; the start of the unknown instruction is retained when still known.
Interline statement labels follow the same cursor. The exporter logs a warning
and optionally appends structured records to the supplied `step_diagnostics` list.
It does not add a model retry, alter the user's confirmed specification, or add a
new rejection to generation. Existing structure and authorization checks remain.

Blank steps are accepted by the application's existing CSV format validator.
That is NOT a GX Works2 compile/import guarantee for an unresolved listing.
Actual native acceptance of those cases still needs Windows/GX Works2 verification.
A successful file write is not a claim that its PLC program is executable.

## Maintenance and regression ownership

Extend this one JSON resource with source-bound observations or explicit rules;
do not add mnemonic exceptions in the serializer or a second width table.
Preserve provenance and distinguish native conversion evidence from complete
project/CSV acceptance. Keep unknowns explicit until the applicable form is known.

The existing owner `tests/test_gxworks2_sftl_native_steps.py` now covers the whole
step-width boundary, while retaining its historical path for the test registry.
It includes independent ordinary-width expectations, every fixed native form,
the real CSV serializer across generation forms, D/P variants, OR comparisons,
operand-dependent rules, cumulative steps, unknown preservation, CPU scoping and
existing native CSV fixtures. Dummy operands in broad serializer tests establish
storage consistency only, not valid instruction semantics.

Run from the repository root:

```text
python -m pytest -q tests/test_gxworks2_sftl_native_steps.py tests/test_gxw_lossless.py tests/test_instruction_registry.py tests/test_instruction_contract_alignment.py tests/test_source_layout.py
```

No new workflow, permission rule, validation gate, model call or PLC operation is
introduced by this change.
