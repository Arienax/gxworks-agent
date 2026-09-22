# Instruction step widths

## Ownership

[fx3u_step_widths.json](../../resources/instructions/mitsubishi/fx3u_step_widths.json) is the runtime step-width resource. [plc.instruction_steps](../../src/plc/instruction_steps.py) exposes `instruction_step_width`, `StepWidth` and `StepCursor`. GXW native decoding and CSV export consume this API; the serializer does not maintain a second mnemonic table.

Instruction admission and operand contracts belong to [plc.instructions](../../src/plc/instructions.py), as described in [contract coverage](instruction-contract-coverage.md).

## Stored and expected widths

A reader preserves the positive width stored in its input, including a width that differs from the catalog expectation. The source bytes remain observable for round-trip analysis. Operand count is a separate property.

Expected widths resolve by CPU, literal native form and supported operand rules. Exact observed forms take precedence over broader operand classes. D/P forms are resolved by their own identities; unobserved indexed, string or special-device forms remain unknown. An explicit non-FX3U CPU is not silently assigned an FX3U width.

Evidence and form rules are retained in the resource; original native observations are linked through [research results](../../research/README.md). Conflicting observations remain unresolved until reviewed.

## Unknown handling

An unknown `StepWidth` has `steps=None`, with a source and reason. CSV export preserves the instruction and operands. Once a width is unknown, later absolute labels remain blank rather than using invented offsets; structured step diagnostics record the issue.

The application's CSV format validator accepts blank labels. GX Works2 acceptance of an unresolved listing still requires a native import/compile result for that listing. Follow the [GX operations guide](../guides/gxworks2.md).

## Maintenance

Add reviewed observations or explicit rules to the single resource, retaining their provenance. The existing [test_gxworks2_sftl_native_steps.py](../../tests/test_gxworks2_sftl_native_steps.py) owns the full width boundary despite its historical filename; it covers cumulative labels, operand-dependent forms, CPU scope, unknown preservation and native fixtures. Decoder preservation tests are in [test_gxw_lossless.py](../../tests/test_gxw_lossless.py).
