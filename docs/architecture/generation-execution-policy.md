# Generation execution policy

Agent B materializes the confirmed implementation. It retains settled choices and resolves technical questions needed to produce that implementation. A new explicit amendment, new technical evidence or a concrete correctness conflict can require reconsideration.

## Settled input facts

[generation_input_conditions](../../src/plc/specification/conditions.py) supplies action and run-permit predicates from current canonical input bindings. For an active-low stop, `active_when` uses `NC`, while `inactive_when` and `run_permit_when` use `NO` on that input. These predicates describe the input level; the generator still constructs the program topology and edge behavior.

Only known input-typed bindings participate. Outputs, word registers and arbitrary prose do not become Boolean input facts. Unknown or contradictory levels remain unresolved. Edit requests treat these values as baseline facts subordinate to the explicit amendment.

## Evidence and choices

The generator uses the selected PLC's facts for special devices and instruction behavior. Explicit I/O, hardware and instruction constraints remain in force. Missing retrieval evidence is recorded as missing, rather than converted into a new instruction ban.

The shared execution prompt is owned by [application.confirmed_generation_context](../../src/application/confirmed_generation_context.py). Compact and full-wire generation append the same policy. Factual evidence construction belongs to [instruction-fact delivery](instruction-fact-delivery.md); transport and request counts belong to [call contracts](generation-call-contracts.md).

## Verification

[test_confirmed_input_protocol.py](../../tests/test_confirmed_input_protocol.py) covers role/level combinations, changed and deleted inputs, amendment precedence and shared prompt wiring. Real-model latency and token usage require matched measurements under the [diagnostic procedure](../guides/diagnostics.md); deterministic tests establish the data and call contract.
