# Generation lane ownership

Generation facts are split by the decision they answer. They must not be merged
back into one prompt-side capability blob.

## 1. Construction routing

`application.construction_routing` selects zero to two construction examples
from the confirmed structured specification. It does not inspect free user prose,
manual RAG text or instruction operand details. The first implementation is
deliberately conservative: multiple independent primary needs without a composite
motif receive no example rather than several weak analogies.

## 2. Common instruction operand semantics

`plc.instruction_semantics` owns the vendor-level meaning of operand positions:
position, semantic name, read/write role and data type. For example, ADD keeps
three positions with read/read/write roles independent of the selected CPU.

A catalogue entry that is already CPU-scoped is marked `catalog_scoped`; this
layer does not claim that such a definition has been proven common to other
families.

## 3. Target applicability overlay

`plc.target_capabilities` resolves whether the selected target may use an
instruction and carries target-specific arity/order evidence, device-class
constraints, numeric/memory boundaries and literal-form properties. It does not
own completion relays.

The materialized `operand_slots` zip common position meanings, target-native
symbols when known, and exact confirmed operands. This removes a repeated
position-mapping task from Agent B.

## 4. Device/runtime semantics

`plc.runtime_semantics` owns instruction completion devices and pulse/hardware
runtime semantics for the selected target. Special-device meaning therefore does
not leak into the common operand contract.

## Model-facing delivery

Structured instruction records retain the historical `instruction_contract`
only as compatibility/diagnostic metadata. Agent-facing evidence is rendered as
three explicit lanes:

- `OPERAND_SEMANTICS`
- `TARGET_APPLICABILITY`
- `RUNTIME_SEMANTICS`

Construction examples are injected independently by the application router.

This split is intentionally compatible with the existing instruction registry.
It does not yet claim complete cross-family source verification; missing common
or target facts remain visible through their existing coverage status.
