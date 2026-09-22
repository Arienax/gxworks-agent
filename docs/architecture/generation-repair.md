# Generation repair boundaries

The saved version, an unaccepted candidate and a repair response are separate objects. A partial validation repair applies to the current unaccepted full candidate, including during first generation. It must not use the last saved version as an unrelated repair base.

## Validation profiles

[GenerationWorkflow](../../src/application/generation.py) owns the generation pipeline and its validation profile. The confirmed-generation `generation_structural` path does not inherit the strict workflow's semantic retry loop. Retry counts, time budgets and repair transitions are implementation parameters in that class, rather than a universal promise for every entry point.

Shape parsing precedes compatibility normalization. Local normalizers may accept equivalent representations, but they do not change contact polarity, scan order or the selected control method. Failed candidates may be retained for inspection with their original validation status.

## Explicit repair

[format_repair_response](../../src/application/format_patch_repair.py) applies the format-patch contract. [structural_repair_response](../../src/application/repair_policy.py) restricts structural repair against the baseline's semantics. [application.field_repair](../../src/application/field_repair.py) handles field-addressed repair requests.

Repairs preserve unrelated rungs, order, comments, original amendment scope and frozen instruction semantics. The complete materialized candidate is checked again. An incomplete full response is not accepted as the entire program. Cancellation and provider failures retain their own outcomes instead of being treated as requests to redesign the program.

## Outcomes and evidence

A failed repair records its reason, affected location and available candidate; it does not activate a program or perform native execution. A successful candidate proceeds through [artifact delivery](generation-delivery.md) and the existing save transaction. GX operations still require the [approval policy](approval-modes.md).

[test_contract_repair_planner.py](../../tests/test_contract_repair_planner.py), [test_format_patch_repair.py](../../tests/test_format_patch_repair.py) and [test_partial_repair_semantic_freeze.py](../../tests/test_partial_repair_semantic_freeze.py) own the corresponding invariants. Historical repair experiments are in the [process index](../process/README.md).
