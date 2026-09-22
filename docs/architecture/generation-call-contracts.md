# Generation call contracts

## Shared context, distinct wire formats

Built-in confirmed generation uses [generate_confirmed_ladder](../../src/application/generation_agent.py) and the compact protocol. External candidate generation uses the full `ladder_v1` instructions from [build_generation_instructions](../../src/application/generation_context.py). Both consume [ConfirmedGenerationContext](../../src/application/confirmed_generation_context.py).

The selected PLC model reaches [generation_output_contract](../../src/plc/generation_contract.py), so the output schema uses the CPU-scoped instruction registry. Protocol fields are owned by [application.compact_protocol](../../src/application/compact_protocol.py) and [plc.generation_contract](../../src/plc/generation_contract.py), not duplicated in client code.

## Requests and transport

The built-in confirmed-generation entry issues one model generation request. [application.model_api](../../src/application/model_api.py) and [model_runtime.provider](../../src/model_runtime/provider.py) own request construction, streaming and completion collection. Saved model tuning follows [runtime ownership](runtime-ownership.md).

A supported streaming downgrade is narrow: it requires an explicit structured transport rejection before content, reasoning or tool deltas have been observed. Authentication, timeout, rate-limit and post-delta failures retain their failure classification. The provider's retry configuration and exact downgrade predicate are defined in `model_runtime.provider`.

The candidate is the first complete JSON object. Transport collection retains available trailing usage and finish data; a later transport error remains observable. Timing and usage measurements are described in [diagnostics](../guides/diagnostics.md).

## Candidate acceptance

The shared candidate path performs compatibility normalization, permitted partial materialization, validation and artifact construction. It preserves the chosen strict or `generation_structural` validation profile. Model output is not saved merely because it parsed as JSON.

Candidate amendments and repair requests follow [repair boundaries](generation-repair.md). Saved artifact identity and diagnostic previews follow [generation delivery](generation-delivery.md). Native execution uses the separate [approval policy](approval-modes.md).

The regression owner is [test_call_contract_regressions.py](../../tests/test_call_contract_regressions.py), with external-path coverage in [test_mcp.py](../../tests/test_mcp.py).
