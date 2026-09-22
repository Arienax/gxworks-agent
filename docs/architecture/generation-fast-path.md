# Generation path navigation

The generation path is documented by responsibility:

- [Call contracts](generation-call-contracts.md): request construction, transport and candidate acceptance.
- [Execution policy](generation-execution-policy.md): settled choices and unresolved technical facts.
- [Instruction facts](instruction-fact-delivery.md): evidence selection and budget receipts.
- [Repair boundaries](generation-repair.md): local normalization and explicit repairs.
- [Delivery](generation-delivery.md): version-bound artifacts and reports.

Historical fast-path investigations are preserved in the [process index](../process/README.md). For measured latency and request counts, use the [diagnostic procedure](../guides/diagnostics.md) and record the exact version and model settings.


## Confirmed Agent B acceptance path

Fresh confirmed generation has one canonical acceptance path:

```
compact_ladder/1.1
    -> deterministic compact expansion
    -> CandidateService.prepare(candidate_origin="compact_agent")
    -> structural validation
    -> confirmed semantic validation
    -> PLC IR
    -> artifact rendering
```

The prepared candidate is returned directly to the generation workflow. It is
not serialized back to ladder JSON, parsed again, and prepared a second time.
Legacy BLOCK_OUTPUT/counter/OUT compatibility normalizers remain available for
legacy, external, import, and repair inputs, but the fresh compact Agent B path
does not enter them.

[plc.specification.semantic_validation](../../src/plc/specification/semantic_validation.py)
owns generation-time checks against machine-readable confirmed facts. It is
deliberately narrower than `validate_ladder_full`: generic engineering style
checks remain review concerns and are not promoted back into hard generation
gates.
