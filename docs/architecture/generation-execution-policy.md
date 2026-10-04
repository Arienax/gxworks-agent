# Generation execution policy

Agent B materializes the confirmed implementation. It retains settled choices and resolves technical questions needed to produce that implementation. A new explicit amendment, new technical evidence or a concrete correctness conflict can require reconsideration.

## Settled input facts

[generation_input_conditions](../../src/plc/specification/conditions.py) supplies the signal label, explicit active/inactive bit values and their action and run-permit predicates from current canonical input bindings. For a stop button confirmed to read 0 when pressed, `active_level` is 0 and `active_when` uses `NC`; the released state reads 1, so `inactive_when` and `run_permit_when` use `NO`. These predicates describe the input level; the generator still constructs the program topology and edge behavior.

Confirmation and generation share the legacy declaration recovery in
[recover_declared_bindings](../../src/plc/specification/bindings.py). It recovers
only missing identities for existing I/O rows from explicit user declarations.
Current bindings take precedence, including unknown levels; row labels, moved
addresses and deletions remain authoritative. Generation operates on a copy and
does not rewrite a saved snapshot.

Only known input-typed bindings participate. Outputs, word registers and arbitrary prose do not become Boolean input facts. Unknown or contradictory levels remain unresolved. Edit requests treat these values as baseline facts subordinate to the explicit amendment.

The shared prompt states the bit tests briefly: `NO` conducts at
bit 1 and `NC` conducts at bit 0, independently of a physical button's wiring.
It asks the generator to translate the required condition into a bit value before
choosing the contact, including internal fault flags and permission signals, and
to check that serial conditions are reachable together. This is generation
guidance; it does not certify a program's polarity, startup or scan priorities.
Keep this policy concise: deliver confirmed facts and necessary constraints,
without button tutorials or repeated step-by-step instructions.

## Evidence and choices

The generator uses the selected PLC's facts for special devices and instruction behavior. Explicit I/O, hardware and instruction constraints remain in force. Missing retrieval evidence is recorded as missing, rather than converted into a new instruction ban.

The shared execution prompt is owned by [application.confirmed_generation_context](../../src/application/confirmed_generation_context.py). Compact and full-wire generation append the same policy. Factual evidence construction belongs to [instruction-fact delivery](instruction-fact-delivery.md); transport and request counts belong to [call contracts](generation-call-contracts.md).

[generation_process_fact_needs](../../src/plc/semantics.py) requests source
evidence for confirmed first-scan semantics, declared timer references and the
selected hardware-counter structure. The existing structured lookup uses
official device-operation sections in SQLite. A compiled first-generation
request uses this fact plan rather than broad recall over flattened process
prose; edits retain the current user delta. Missing or budget-omitted sources
remain explicit in the final fact-coverage receipt.

The advisory review context retains each network's canonical ladder topology
and the same confirmed input predicates. Local syntax and static checks do not
establish process correctness; timing and process findings still require
evidence tied to the reviewed version.

Local review passes the confirmed structured execution requirements to the same
Core semantic analyzer used during generation. Missing source-edge coverage
therefore remains visible in review; a pulse of a gated composite condition
does not establish an edge of the physical input. Review does not reconstruct
these requirements from free-form notes.

## Verification

[test_confirmed_input_protocol.py](../../tests/test_confirmed_input_protocol.py) covers role/level combinations, changed and deleted inputs, amendment precedence and shared prompt wiring. Real-model latency and token usage require matched measurements under the [diagnostic procedure](../guides/diagnostics.md); deterministic tests establish the data and call contract.
