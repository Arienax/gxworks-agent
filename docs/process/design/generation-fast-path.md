# Generation fast path: confirmed specification is the semantic authority

> 历史设计记录，冻结来源版本 `f2f1781`。有效规则见[generation-fast-path](../../architecture/generation-fast-path.md)；下文保留原实验、选择和失败。

## Decision

The existing Web/application API generation flow is the acceptance authority for both
internal generation and external MCP candidates. Once the operator has confirmed
the specification, the application does **not** reinterpret the request with a
second set of heuristic semantic gates. MCP does not add stricter generation
rules or cause those rules to flow back into the API.

The direct generation critical path is:

1. confirmed specification and current project context,
2. one model generation (stream transport may fall back once to non-stream transport),
3. JSON/ST parsing, compatibility normalization and partial-edit assembly,
4. structural/processability validation and conservative condition normalization,
5. deterministic PLC IR construction and IR consistency validation,
6. artifact rendering and local version save.

There is no automatic semantic re-generation loop after step 2.

An external client supplies its own model response instead of step 2. It calls
`create_program_candidate` for first creation or ordinary full/partial edits;
the service does not call an internal model to generate a second answer. MCP is
the engineering interface and has no client-skill installation dependency.
Optional client guidance does not replace ToolRuntime, the project context,
validation, persistence or approval.

## Shared implementation

- `plc_generation_context.py` owns the existing API prompts, output discipline,
  routing, selected-model profile, confirmed-context assembly and local RAG.
  `get_generation_context` accepts optional `user_requirement` and returns
  `generation_instructions` and `generation_request` from this same assembly.
  Empty arguments remain valid. Engineering fields are projected before being
  returned externally; credentials, private paths and conversation state stay
  local. Retrieval retains the API policy, budget and full-profile fallback.
- `plc_generation.py::prepare_ladder_candidate` performs compatibility
  conversion, validates/materializes partial edits, checks candidate structure,
  normalizes eligible conditions and constructs a consistent IR. Both
  `GenerationWorkflow` and `PLCCore.create_program_candidate` call it.
- `render_generation_artifacts` provides the same JSON, IR, SVG and ST outputs,
  plus FX3U CSV artifacts, for the API and generated MCP candidates. It checks
  IR consistency without invoking the historical full semantic validator.

The published response schema describes the recommended shape. The shared API
parser is the acceptance authority; a separate MCP schema walker must not reject
an API-compatible encoding before normalization. Project, CPU, specification,
profile, revision, hashes and derived IR remain server-owned.

## Compatibility and condition normalization

Unambiguous `APP_INSTR OUT` forms become dedicated coil/timer/counter outputs;
legacy `TIMER` with a C address becomes `COUNTER`. `BLOCK_OUTPUT` expressions
become typed outputs/application instructions before the same catalogue, CPU,
operand and writable-target checks. Invalid instructions cannot bypass those
checks through a textual encoding. Additional rung source fields accepted by
the API remain source data and cannot overwrite computed IR.

Condition cleanup is conservative: it can remove repeated ordinary conditions,
extract a common branch prefix and combine adjacent ordinary outputs with the
same conditions. It does not move conditions across stateful/unknown operations
or intervening writes to their operands. Edges, complex conditions, special
devices, unknown layouts and repeated writes to the same coil are not treated
as freely interchangeable Boolean expressions. It does not turn duplicate-coil
writes into a single OR expression.

When a baseline exists, normalization is limited to submitted partial rungs or
rungs changed by a full response. Untouched source bodies remain unchanged;
partial additions and deletions use the existing API ordering rules. Explicit
repair scope and the workbench's requested network/address scope remain binding.

`normalization.changes` and `normalization.skipped` contain concise messages and
network IDs. Proposals and saved versions retain these summaries. They explain
what was changed or preserved; they are not executed simulation or native
compiler evidence.

## What can block direct generation

`generation_structural` validation is deliberately narrow. It can reject a candidate when the application cannot safely represent or process it, including:

- malformed JSON or wrong top-level response shape;
- unsupported ladder element encoding;
- invalid device/address syntax or out-of-range address;
- an instruction absent from the verified instruction registry, unsupported by the selected CPU, or encoded with invalid arity;
- writes to CPU-owned read-only targets;
- duplicate/invalid rung identifiers;
- an invalid partial-edit envelope or an explicit repair scope escape;
- internally stale/inconsistent PLC IR.

These are representation/tooling facts, not a second interpretation of user intent.

## What no longer blocks direct generation

The following remain useful for Review, diagnostics, simulator planning and GX/runtime evidence, but they do not reject a direct candidate after specification confirmation:

- `selected_approach.generation_contract` conformance;
- prose/regex-derived execution semantics;
- inferred edge/first-scan/cyclic intent;
- duplicate-coil style/ownership findings;
- timer-oscillation style rules;
- same-scan SET/RST toggle findings;
- M8029 topology preferences;
- confirmed hardware-family heuristics beyond the model-facing instruction/address contract.

The application still computes IR analysis metadata. It does not turn those findings into a hidden model retry.

CSV export preserves explicit project device comments. Per-contact/output labels
only supply missing comments, and address aliases identify one device. Export
does not mutate the input ladder or IR; comment precedence does not alter the
instruction CSV or introduce another model call.

## No hidden repair loop

A structurally invalid model response fails once with diagnostics. Neither the
API nor MCP starts an automatic semantic repair or repeated-submission loop.

A streaming transport failure may make one ordinary non-streaming request for the same candidate. This is a transport fallback, not a code/semantic repair.

Explicit Debug/patch/contract-repair tools keep their evidence and scope
boundaries. They are separate workflows and do not silently run after normal
generation. Ordinary edits use `create_program_candidate`; an explicitly
requested scoped Debug patch uses the existing `read_network → patch_program`
path and its strict checks.

## Validation profiles

- `generation_structural`: internal generation and external full/partial
  generated candidates. The server carries it through temporary compilation,
  proposal checks, first/child-version saving, reload and preview. It is not a
  client-selectable bypass. Persisted versions retain this profile so reading
  them does not retroactively apply semantic gates.
- `strict`: existing Agent patch, Debug, review/execution-oriented validation unless that workflow explicitly chooses otherwise.

This prevents a version that was intentionally accepted through the direct generation fast path from becoming unreadable merely because a later load path invokes the historical strict validator.

Approval is independent of the validation profile. Direct workbench generation
retains its local autosave behavior; connected Agent clients follow the current
workspace approval policy. Standalone MCP returns `confirmation_required`
without saving a version or granting approval. Project/version/specification
bindings, hashes, scope checks and authentication remain in force.

## Product responsibility boundary

- Requirement meaning: model + operator during specification confirmation.
- Candidate implementation: model using the confirmed specification.
- Representation integrity: deterministic parser/schema/IR code.
- Engineering quality findings: optional Review/static analysis.
- Behavior correctness: simulator/Factory I/O/regression evidence.
- Native legality: GX/compiler/runtime evidence.

A review warning is not promoted to a generation failure merely because it can be expressed as a deterministic rule.
