# Generation execution policy

Agent A owns requirement analysis and the explicit Direct/Design choice. Once a
specification reaches Agent B, generation is materialization, not another design
competition. Both the compact adapter (`generation_agent`) and the ladder_v1
adapter (`generation_context`, also used by MCP) append the same
`generation_execution_prompt` from `application.confirmed_generation_context`.
Repair, review and analysis prompts do not receive this generation policy.

## Settled facts versus open technical questions

`plc.specification.conditions.generation_input_conditions` derives level tests
from the current canonical input bindings. For an active-low stop at X3, it
supplies `active_when=NC X3`, `inactive_when=NO X3` and
`run_permit_when=NO X3`. These are predicates, not a generated latch, a replacement
for edge detection or proof of a whole program's correctness.

Only X inputs and explicitly input-typed M/S bindings participate. Word
registers, outputs and arbitrary prose are not interpreted as boolean inputs.
Missing/contradictory levels remain unresolved; the helper raises no user-facing
validation error and never allocates devices, invents missing levels or rewrites
a specification. It runs on each new snapshot, so user edits and deletions do
not reuse stale predicates. During editing the predicates are explicitly marked
as baseline facts, subordinate to the current explicit amendment.

The shared policy tells the generator to retain settled decisions, check only
necessary unresolved technical facts, avoid comparing equivalent implementations
or guessing evaluator preferences, and revisit a decision only for a new
amendment, technical evidence or a concrete correctness conflict. It does not
instruct the model to ignore discovered errors. Current structured parameters and
bindings take precedence over stale proposal prose; unverified constraints remain
reference semantics, not newly invented hard constraints. Original narrative
content and provenance are preserved, not reconciled by fragile keyword rules.

## Special devices and evidence

The compact protocol no longer bans every unlisted internal special device while
simultaneously asking the model to implement a confirmed clock requirement.
Internal special devices may be chosen for the confirmed semantics using the
current PLC model's documentation/evidence. Explicit bans and external I/O,
hardware and module-register boundaries remain in force. No particular clock,
shift instruction, timer or allocation is hard-coded as a recipe.

The context records only whether retrieved text is present. Presence is not
semantic coverage: a chunk mentioning an instruction does not prove its operand
order, shift direction or trigger semantics. Missing evidence is not a ban, and
repeated recollection is not a replacement for evidence. This change does not add
an instruction manual, a new retrieval pass, or tools that Agent B does not have.
Existing task-shaped fact retrieval remains unchanged.

## Runtime boundaries and validation

This is a prompt/data change, not reasoning-stream concealment. It does not alter
`reasoning_effort`, token ceilings, model capability handling, retry count,
stream framing, output schema, acceptance checks, user confirmation or native
PLC permissions. The compact generation entry still makes its existing single
model call. The source handoff records policy version `settled-facts-v1`.

The existing `tests/test_confirmed_input_protocol.py` owner covers level/role
combinations, unknowns, changed/deleted inputs, unchanged source data, shared
prompt wiring, evidence preservation, edit precedence, task isolation and the
actual one-request HTTP generation path. Run it together with
`tests/test_call_contract_regressions.py` and `tests/test_intent_evidence_handoff.py`.

A passing deterministic test does not establish that a particular model will
use fewer reasoning tokens. Measure the same confirmed specification, model,
endpoint and reasoning settings before/after, recording time to first useful
output, reasoning/output usage and generated-program correctness. No fixed
100-150-token target, percentage reduction or successful hardware execution is
claimed by this policy.
