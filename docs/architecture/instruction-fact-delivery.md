# Agent B instruction facts and behavioral measurement

## Runtime change

`application.confirmed_generation_context` builds the same detached confirmed
specification for compact and ladder_v1 adapters. It adds instruction-fact targets
to the existing `KnowledgeQuery`, not a second analysis agent. Targets come from
the selected contract, positive selected-plan text and the existing catalogue.
Instruction families without a named opcode can use official instruction anchors
from the existing ranked retrieval. Those anchors are retrieval candidates, never
new required opcodes or automatic Direct/Design decisions.

`knowledge.instruction_facts` asks about operands, operation, execution and limits.
Exact instruction definitions outrank generic manual prose; a body mention is
not a definition and `MOV` is not `$MOV`. The index still owns CPU applicability.
Same-section/explicit-parent recovery stays inside a manual and revision; no
neighboring page is assumed to belong to an instruction. The first ranked manual
revision is preferred rather than mixing different programming syntaxes to fill
space. No database migration, model call or vector-store replacement is needed.

Definition prose, operand tables and cautions are packed together before top-k
selection. Tables stay whole; duplicate layout/diagram renditions do not displace
prose/table evidence. Source identifiers, revision, original-text hash and exact
source offsets accompany excerpts. The existing character and token allowances
remain in force. This can still omit relevant facts under pressure; it does not
prove that parsed manuals are complete or unambiguous.

The receipt distinguishes `candidate_evidence`, `budget_omitted` and `unresolved`.
Keyword categories are packing hints, not semantic verification. Final delivery
is reconciled after context compilation using complete block identities and body
hashes. The application-owned `handoff.instruction_facts` survives the older
generic provenance allowlist without modifying the stored PLC specification.
Missing facts do not reject generation, forbid an instruction or invent an I/O.

## Canonical representation

`compact_protocol` owns the transport schema and renders its required-field list
into the prompt. The standard representation uses `h:null`, `s:[]` and `i:[]`
when those slots are empty, matching the strict schema. Existing local aliases
remain accepted. Device polarity, edge behavior and scan order are not format
normalizations. Selected opcode capability descriptions come from the current
Python catalogue's generation support and arity contracts, not a second manual
or a claim that the local simulator/hardware has verified the program.

The existing settled input predicates remain authoritative: signal action and
run permission are distinct. No new runtime validation gate, retry loop, effort
override, token ceiling or hidden-thinking UI behavior is introduced.

## Complete metering

The first complete JSON is still the only generated candidate. Framing no longer
closes the provider iterator at that point: trailing usage and finish diagnostics
are consumed, and reasoning events remain observable. End-to-end duration must
therefore include any provider continuation after the JSON. This is measurement
integrity, not a claimed latency optimization. Existing cancellation/timeout
behavior remains in the transport; a later transport failure is not swallowed.

`Usage` retains optional `reasoning_tokens` and provider `raw_usage`. Empty-choice
usage tail packets are processed before skipping choices. Missing reasoning
metering stays `None`; reasoning characters are not converted into token counts.
Successful and failed operator transcripts retain these metering fields.

## Reproducible experiments

Use `scripts/benchmark_agent_b.py`. The default only prints a plan and does not
load credentials. An explicit `--live` authorizes API calls using the saved active
profile. Omitting `--effort` preserves the saved setting. Keep endpoint, exact
model version and effective parameters identical across comparison groups.

The JSONL case format is one object per line:

```json
{"case_id":"your-existing-case","plc_model":"FX3U","confirmed_spec":{"summary":"Replace with the complete confirmed project specification"}}
```

Use the actual exported confirmed specification, not a new prompt which gives
away the expected program. This runner isolates Agent B; end-to-end Agent A+B
acceptance also needs original pure-requirement cases. The bundled smoke cases
only exercise input-polarity/self-hold coverage, not shift-generation quality.

```powershell
python scripts/benchmark_agent_b.py benchmarks/agent_b_smoke_cases.jsonl --repeat 1
python scripts/benchmark_agent_b.py YOUR_CASES.jsonl --repeat 5 --live --output D:\private-evals\agent-b-run.jsonl
```

Groups:

- `legacy_retrieval`: original retrieval without the new instruction-fact stage,
  but the **same current** protocol and metering. It is not the complete previous
  software version.
- `automatic`: actual new production context assembly and generation path.
- `oracle`: operator-supplied reviewed source records. Each case must include
  `oracle_reviewed:true` and an `oracle_evidence` array with `id`, `source`, `text`
  and preferably manual/revision/page metadata. The runner never generates an
  oracle with another model.

To include the oracle group, pass
`--arms legacy_retrieval,automatic,oracle`. To isolate protocol changes, repeat
with the same runner/cases/settings on the previous checkout and the current
checkout. Do not label a current-protocol retrieval ablation as that historical
baseline. Interleaved randomized case/group/repeat order reduces ordering effects;
there is no model-response cache in the runner.

Records contain actual resolved requests (via the provider observation hook),
retrieval text/manifests, handoff, raw provider-exposed content/reasoning, optional
usage, first-content/JSON-complete/transport/end-to-end timings, generation output,
errors and structural/behavioral check results. Output is credential-redacted and
created with private permissions where supported. Existing redaction size limits
can truncate unusually large strings; do not treat truncated exports as complete
prompt evidence. Do not commit private experiment outputs or API credentials.

The standard structural/IR checks are not vendor compilation. The default
behavioral evaluator uses the existing narrow self-hold checker; other programs
remain `not_covered`. Supply `--evaluator your_module:evaluate`, returning
`{"status":"verified"|"failed"|"not_covered",...}`, for independent simulator or
reviewed behavior checks. Include stop during operation, simultaneous start/stop,
active-high/low variants, edge/continuous execution and relevant range boundaries.
No PLC/native writes occur in the runner itself.

Compare matched case/repeat rows, actual effective effort, missing-usage rates,
all attempts and failures, behavior coverage, median and tail latency. Review
provider-exposed reasoning for repeated implementation changes only when that
channel exists; hidden internal reasoning cannot be counted from output alone.
The summary deliberately reports `performance_acceptance:not_established` rather
than declaring a win from a few faster samples or green CI.

Primary automated owners: `test_instruction_fact_context.py` for evidence
assembly and `test_agent_b_measurement.py` for experiment/metering contracts.
Existing confirmed-input tests continue to protect polarity and local expansion.
The existing Confirmed Generation Compatibility workflow runs these tests.

## Not implemented or claimed

This change does not add an in-generation lookup tool loop, a second research
model, a new reranker service, exhaustive fact certification or full PLC simulation.
Those should be considered only after the reviewed-evidence comparison identifies
remaining gaps. No live-model latency/token reduction follows from static tests.
