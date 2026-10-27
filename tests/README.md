# Test ownership and file-boundary rules

This registry assigns each test file a stable responsibility. Add regression cases to the owner of the affected contract.

Original-manual parser and historical recall tests explicitly opt into the
`private_manual_evidence` fixture and its frozen private definition archive.
They do not exercise the product's default corpus. Authored-corpus migration,
scope, dependency, numeric and publication-boundary checks live in
`test_structured_knowledge_quality.py`; shared generation and protocol tests
continue to use the product default.

The registry below lists the current owners. Data fixtures under `tests/fixtures/` belong to the test files that exercise them. Check registry coverage with `python -m pytest -q tests/test_source_layout.py`.

## Rules for adding tests

1. **Append to an existing owner by default.** A bug fix normally adds a case to the file that already owns the invariant. A new bug, issue or PR is not a new architectural boundary.
2. **Use parametrization for variants of the same invariant.** Different keywords, devices, polarities, malformed shapes or provider responses should normally be rows in one table-driven test rather than separate files or near-identical functions.
3. **Create a new test file only for a new stable boundary.** Valid reasons are a new subsystem/public contract, a materially different execution environment/platform, or dependency isolation that cannot live safely in an existing owner.
4. **Do not name new test files after dates, issue numbers, PR numbers or temporary migration phases.** Name the stable behavior, not the incident that discovered it. Existing `*_regressions.py` files are compatibility owners, not a pattern to multiply.
5. **Keep one detailed owner for an invariant.** Cross-layer coverage may keep one representative end-to-end sentinel, but low-level permutations belong to the lowest deterministic owner. Do not reproduce the same matrix in API, MCP, Web and full-workflow files.
6. **Test observable contracts, not private implementation layout.** Implementation-detail assertions are acceptable only in explicit architecture/source-layout tests.
7. **PLC semantics live in Python Core tests.** TypeScript tests cover presentation/state wiring; vendor/native tests cover adapters and transport. They must not become duplicate PLC-rule implementations.
8. **A small file is justified only by a distinct boundary.** One- or two-test files should normally be merged into an existing owner unless they intentionally isolate a platform, optional dependency, packaging boundary or critical smoke contract.
9. **Fixtures are not owners.** New data-only samples go under `tests/fixtures/` and are exercised by an existing owner file.
10. **Update this registry whenever a test file is added, renamed, merged or removed.** `tests/test_source_layout.py` checks that every Python and Web test file has exactly one registry entry.

## Native acceptance orchestration

Native CPU/model acceptance must avoid repeating setup for each input or
diagnostic category:

1. Plan before touching the desktop: list the source-bound evidence already
   available, the missing assertions, and the reason for each requested rerun.
   A new script name or test category is not an invalidation reason.
2. Group cases with the same CPU, tool version, source, declarations, tasks and
   compiler options into one explicitly bound engineering/simulator session.
   Prefer attaching to that exact existing project; ambiguous targets require
   explicit selection. Do not choose the first visible window.
3. Compile, save and cold-reopen once per changed engineering context. Reuse
   completed evidence only while its actual source/context still matches;
   directory existence, an old progress flag or a filename is not proof.
   Runtime-only input changes do not invalidate compile/save/reopen evidence.
4. Run normal inputs continuously through the program's request/acknowledgement
   protocol. Stage every input before publishing a fresh request; verify that
   request's acknowledgement so stale output cannot satisfy the next case.
5. Reset the CPU only for initialisation, recovery from an observed fault/STOP,
   or a case that explicitly requires an independent initial state. Clearing
   fixture-owned input/acknowledgement memory does not require a CPU reset.
   Tests of first-scan, retained state and reset semantics must retain their
   required isolation. Do not silently change the product Test DSL's default
   case-isolation contract to optimise a native acceptance script.
6. Run normal cases before intentional fault cases where dependencies allow.
   A terminal expected fault needs no recovery reset merely to stop the
   simulator. Do not reorder stateful cases with declared dependencies.
7. Stop on unexpected errors, uncertain completion or a failed assertion.
   Diagnose before retrying; resume only the affected assertions rather than
   replaying the entire model matrix. Warning review and native/source binding
   remain mandatory.
8. Record setup/compile/reopen/start/stop/reset counts and each reset reason in
   the acceptance receipt. Check orchestration with fake native adapters and
   existing frozen results first; do not repeat desktop runs just to test the
   test runner.

## Local incremental checks

Use `python tests/run_checks.py` while editing. The default `quick` level selects
the changed contracts and representative Web/MCP integration cases.
`python tests/run_checks.py --level domain` runs the complete affected owners
when a module is finished. Only explicit `--level full` selects the full level.
Shared source, runtime changes, unknown inputs and missing input history preserve
the requested level. The runner records their impact separately from execution.
Receipts retain `selection.requested_level` and `selection.full_reasons` so a
pending verification notice cannot be mistaken for a request to run everything.
Public and private skill Markdown files
follow the documentation rule; executable files under those directories still
require an owner. A quick/domain plan that actually covers every Python test is
identified separately as full execution scope and still requires permission.
Selection is not permission to execute a full regression. Every full run requires
explicit user permission for that invocation, supplied as
`--full-approval "<the user's explicit authorization or a verifiable reference>"`.
Agents must obtain this permission from the user and must not invent the statement.
The runner refuses an unapproved full plan before starting Python or Web checks
or writing a result/baseline, including a quick/domain selection whose actual
coverage happens to include every Python test.
The execution function enforces the same check independently of the CLI.

Direct pytest invocations covering the complete suite (the default test scope,
the repository/test directory, or complete file/function lists) require the same
explicit command-line option. Permission in `PYTEST_ADDOPTS`, pytest `addopts`, CI
environment flags or an earlier receipt never authorizes a later invocation.
The user authorization is recorded in the runner receipt and the pytest summary.
Use targeted file/function selectors for local checks; do not split a full run
into batches to evade permission. `--dry-run` and pytest `--collect-only` only
inspect scope and do not require permission. No full regression is implied by
an earlier plan, an ordinary request to continue, or module completion.

Inspect the scope first with `--dry-run`. `--base <ref>` overrides the default
local merge-base of `HEAD` and `origin/main`; the runner does not fetch.
`--paths <path> [...]` adds explicit source-relative paths, `private/...` paths,
or absolute paths within those two repositories. It supplements detected changes.
Staged, unstaged, untracked, committed-since-base, deleted and renamed inputs are
included. Changed test functions select all their parameter variants. Fixture or
module changes select their declared owner and statically known import users.
An unowned test can select itself while its missing ownership remains unresolved;
other unmapped inputs stay explicitly pending until their association is known.
Root temporary files are neither silently ignored nor reasons to run every test.

Shared Python paths use reverse imports, including literal dynamic imports and
deleted module names. Resource associations combine `shared_impacts` reader
declarations in `check_suites.json` with literal resource references. These are
conservative static associations, not proof that a test executes every code path.
When ordinary changes already define a scope, shared inputs are rechecked only
inside that scope and their other consumers stay owed. An explicit shared path,
or a run with only shared changes, selects related quick contracts; domain uses
the known consumer files without filling unrelated parts of their owners.
An association that cannot be resolved remains pending; static import analysis
does not establish coverage of computed dynamic imports.

Python tests/configuration ignored by Git and tracked local overlays hidden by
`skip-worktree`/`assume-unchanged` use a separate local input snapshot.
The first run, a missing/corrupt snapshot, or missing Git history records unverified
inputs without automatically selecting the whole suite. Explicit paths still
select their related scope; establishing an observation proves no test passed.

Dependency declarations (`requirements.txt`, `requirements/**`) are inputs, not
executions: a constraints list only caps versions, so its pins are not treated as
enabled distributions. A dependency change maps to owners through the graph that
actually runs — direct imports in `src`/`tests`, transitive dependencies enabled by
markers and extras, and enabled pytest plugins (entry-point autoload honoured,
`-p no:`/`PYTEST_DISABLE_PLUGIN_AUTOLOAD`/`PYTEST_PLUGINS`/`conftest` respected) —
including reverse edges, so a changed or removed transitive package still names the
top-level package and its tests. Imports match by module prefix, distribution names
use PEP 503 normalization, and packages this repository no longer imports are
ignored rather than reported as unknown.

A dependency, Python, pytest or plugin change never raises the level: earlier Python
results of that interpreter/venv become owed and are re-run only inside the already
selected file/function scope, while other results stay listed as still owing
verification. A quick function selection does not become its entire file just
because that file has an outstanding obligation. Impact that maps to no test becomes an
explicit pending confirmation instead of a complete run. A change of the
import/reader mapping can resolve that confirmation into the affected files;
those files stay owed until actually selected and verified. A change of the
interpreter, the platform, pytest, an active plugin set or anything they require
unverifies every earlier Python result of that environment: `pluggy` or a plugin's
own dependency changes how every test executes, including tests that never import
pytest. Node/npm changes owe the Web tests and build but
do not start them for an unrelated PLC scope; that frontend check stays owed until a
frontend or explicit integration scope is selected.

Observations are stored separately from results. They are keyed by source root plus
the absolute interpreter/venv paths — versions are recorded data, not identity — so
a second venv or checkout never overwrites another's records, and the same
environment difference is recorded once. `local/test-checks/checkouts/<source path>`
holds one checkout's state; a shared `inputs.json` from the earlier layout is still
read when it belongs to the same checkout.
`observed_inputs` records the actual source bytes already observed, including
reversions and deletions, independently of the Git merge base. Repeated identical
content creates no new change event. `source_pending` retains the unverified
file/function selectors per interpreter/venv; new changes invalidate all known
environments and a passing run clears only its own executed cases. Unresolved
input associations remain in `unresolved_inputs` and are reconsidered when a
mapping becomes available. Failed and unexecuted results are preserved.
Historical failures supplement only the selected files; with no new scope and
no explicit paths the runner can retry pending failures. Domain acceptance debt
does not enlarge an explicitly selected different scope; `--level domain` with
no other scope can close the known pending owners.
`--align-baseline "<user instruction>"` records the current inputs and environment as the
working baseline without running anything: it writes an alignment record under
`alignments/`, keeps known failures, owed verification and module debt pending, and
is not a regression pass. Use it only when the user asked for the alignment; it
cannot be combined with `--dry-run`. Timestamped logs/JUnit/selection receipts live
beside the snapshot under `GXWORKS_AGENT_PRIVATE_ROOT/local/test-checks`.
`--dry-run` writes neither snapshots nor receipts. Skipped cases remain pending for
subsequent runs; failure, missing environment and incomplete results return nonzero
exit codes and still record input observations with their outstanding verification.
Input changes during execution prevent successful-result and observation updates;
failures and uncompleted obligations from that run are still retained.

Receipts describe only their selected scope. A module-level receipt can be reused
within the same task while its source, tests, dependencies, evidence and execution
environment still match. Recheck affected assertions after any of those inputs
change. A successful quick check does not substitute for module completion.
Quick runs retain their module acceptance obligations in the input snapshot;
`--level domain` executes those owners even after the ignored-file snapshot has
advanced or the source changes have been committed. Skipped cases remain pending
independently of that module completion list, and a module obligation is cleared
only by the complete owner scope that actually ran and passed.

The runner uses the existing pytest/Hypothesis and Node tools. Frozen ZIP members
and source ASTs are process-local test input caches; mutable observations are
recreated for each caller. Tested diagnostics, parsers and save verifiers execute
normally. Genuine live GX cases are deselected by the runner's explicit offline
plugin and recorded separately; run them through the bound native acceptance
workflow above. Register new genuine live cases in `check_suites.json` or with
`native_live`. Offline replay tests with “live” in their names remain included.

## CI placement

The public `Source boundary and Web checks` workflow checks the exported boundary,
Python compilation, clean-source startup and Web tests. Source Layout Validation
runs source ownership, public Owner Store/PWM and MCP paging/schema regressions.
The generation and model workflows run their explicitly listed public contract
tests; adding a test file to a PR does not automatically select the entire suite.
These workflows do not build executable packages or the production Web bundle.

Captured-response replay in `test_analysis_format_repair.py` and
`test_intent_evidence_handoff.py`, private definition coverage in
`test_instruction_fact_context.py`, and original-corpus/ledger alignment in
`test_instruction_contract_alignment.py` require the separate materials repository.
They remain maintained tests and are not part of the public CI commands. The
private generation workflow commands are retained with those materials. Individual
acceptance cases report a skip when their explicitly required private input is
absent; this is not a passing native or private-data result. Source CI, private
offline acceptance, live native acceptance and package validation remain separate.

A test file should have one primary targeted workflow. It may also run in the full suite. Listing the same file in multiple targeted workflows is justified only when the workflows exercise materially different contracts; platform/package checks belong to full validation rather than every PR.

## When to merge an existing test file

Merge a file into another owner when all of the following are true: its assertions protect the same stable contract, it does not require a distinct environment/dependency boundary, and moving it does not make the receiving file cross subsystem ownership. Preserve the test cases; consolidation is about ownership, not deleting regression coverage.

Earlier consolidations are recorded in [test ownership history](../docs/process/test-ownership.md).

## Current file-boundary registry

The file-level focus in the table is read together with its section boundary. The section is normative: if a proposed test would make a file cross that boundary, choose the narrower existing owner or justify a new stable boundary.

<!-- TEST-BOUNDARY-REGISTRY-START -->
### Analysis, planning and confirmation

Owns Agent A analysis mode, candidate/choice handling, confirmation/re-confirmation and user intent capture. It must not own Agent B provider transport, deterministic PLC validation, or artifact delivery.

| Test file | File-level focus |
| --- | --- |
| `tests/test_analysis_design_rag.py` | Haystack task/source routing, pre-top-k filtering and Design evidence scope |
| `tests/test_analysis_format_repair.py` | focused coverage for analysis format repair |
| `tests/test_analysis_mode_jobs.py` | focused coverage for analysis mode jobs |
| `tests/test_analysis_prompt_integration.py` | focused coverage for analysis prompt integration |
| `tests/test_analysis_prompt_routing.py` | focused coverage for analysis prompt routing |
| `tests/test_application_planning.py` | focused coverage for application planning |
| `tests/test_confirmed_input_protocol.py` | confirmed physical input levels across legacy projection, generation and validation |
| `tests/test_confirmed_reconfirmation.py` | focused coverage for confirmed reconfirmation |
| `tests/test_hardware_intent_boundary.py` | focused coverage for hardware intent boundary |
| `tests/test_optional_hardware_parameters.py` | optional hardware questions plus Pydantic parameter identity/review-to-generation views |
| `tests/test_program_exploration.py` | focused coverage for program exploration |
| `tests/test_spec_choice_metadata.py` | focused coverage for spec choice metadata |
| `tests/test_workbench_planning.py` | focused coverage for workbench planning |

### Context, prompt and retrieval

Owns prompt assembly, token/context budgeting, RAG query construction, retrieval quality and shared generation context projection. It must not decide PLC semantic correctness or UI behavior.

| Test file | File-level focus |
| --- | --- |
| `tests/test_context_compiler.py` | model-aware budgets, intent/evidence projection and unconditional historical-audit exclusion |
| `tests/test_context_compactor.py` | over-budget historical checkpoint selection, recent-tail preservation, model compaction fallback and wire shrink accounting |
| `tests/test_context_refresh_runtime.py` | focused coverage for context refresh runtime |
| `tests/test_fx3u_rag.py` | FX3U factual retrieval quality and routing |
| `tests/test_gxw2_skill_import.py` | focused coverage for gxw2 skill import |
| `tests/test_gxw2_skill_ranking.py` | GX Works2 skill routing, candidate expansion and unified RRF boundary |
| `tests/test_instruction_fact_context.py` | task-directed instruction evidence assembly, source integrity and final-budget receipts; no behavioral certification |
| Private `tests/review_tools/` | manual excerpt matchers and frozen source-review assertions, run from the private repository with `GXWORKS_AGENT_SOURCE_ROOT`; public owners retain synthetic and runtime tests |
| `tests/test_instruction_applicability_rag.py` | focused coverage for instruction applicability rag |
| `tests/test_prompt_context_budget.py` | focused coverage for prompt context budget |
| `tests/test_prompt_slim_generation.py` | focused coverage for prompt slim generation |
| `tests/test_rag_instruction_recall.py` | focused coverage for rag instruction recall |
| `tests/test_rag_natural_language_recall.py` | focused coverage for rag natural language recall |
| `tests/test_shared_generation_context.py` | focused coverage for shared generation context |
| `tests/test_structured_fact_lookup.py` | exact instruction/device/error resolution outside broad RAG, residual-query separation and agent manual-search boundary |
| `tests/test_structured_knowledge_quality.py` | focused coverage for structured knowledge quality |

### Generation, contracts, repair and delivery

Owns confirmed-spec to Agent B generation, selected-approach contracts, candidate validation/repair scope, save/delivery and generation-path parity. Domain facts belong to PLC Core; model transport behavior belongs to model/runtime tests.

| Test file | File-level focus |
| --- | --- |
| `tests/test_approach_contracts.py` | selected approach → normalized generation contract → deterministic enforcement |
| `tests/test_approval_modes.py` | focused coverage for approval modes |
| `tests/test_call_contract_regressions.py` | frozen model-call contract, retry bounds and single-completion behavior |
| `tests/test_candidate_diff.py` | focused coverage for candidate diff |
| `tests/test_change_scope.py` | focused coverage for change scope |
| `tests/test_confirmed_compatibility.py` | focused coverage for confirmed compatibility |
| `tests/test_confirmed_generation_regressions.py` | focused coverage for confirmed generation regressions |
| `tests/test_contract_conflict_resolution.py` | focused coverage for contract conflict resolution |
| `tests/test_contract_repair_planner.py` | contract-repair violation structure, repair scope and repairability policy |
| `tests/test_delivery_summary.py` | focused coverage for delivery summary |
| `tests/test_field_patch_repair_protocol.py` | focused coverage for field patch repair protocol |
| `tests/test_format_patch_repair.py` | focused coverage for format patch repair |
| `tests/test_generation_agent_boundary.py` | focused coverage for generation agent boundary |
| `tests/test_generation_delivery.py` | generation preview/artifact delivery, including deterministic repair re-entry |
| `tests/test_generation_fast_path.py` | focused coverage for generation fast path |
| `tests/test_generation_path_parity.py` | generation behavior parity across API/worker/MCP entry paths |
| `tests/test_generation_rejected_json_repair.py` | focused coverage for rejected generation json repair |
| `tests/test_generation_repair_assembly.py` | focused coverage for generation repair assembly |
| `tests/test_generation_repair_workflow.py` | focused coverage for generation repair workflow |
| `tests/test_generation_scope_audit.py` | focused coverage for generation scope audit |
| `tests/test_generation_workflow.py` | focused coverage for generation workflow |
| `tests/test_hybrid_compact_recovery.py` | focused coverage for hybrid compact recovery |
| `tests/test_intent_evidence_handoff.py` | ConfirmedSpec v4 / DecisionReceipt v1 lifecycle, migration atomicity, immutable version audit, and API/MCP context isolation |
| `tests/test_native_repair_scope_schema.py` | focused coverage for native repair scope schema |
| `tests/test_partial_repair_semantic_freeze.py` | focused coverage for partial repair semantic freeze |
| `tests/test_rejected_generation_delivery.py` | focused coverage for rejected generation delivery |
| `tests/test_repair_responsibility_simplification.py` | focused coverage for repair responsibility simplification |
| `tests/test_review_noise_regressions.py` | focused coverage for review noise regressions |
| `tests/test_semantic_contract_architecture.py` | provenance, structured repair scope, legacy migration separation, and instruction capability ownership |
| `tests/test_user_confirmed_generation_repair.py` | focused coverage for user confirmed generation repair |

### Deterministic PLC Core and instruction semantics

Owns deterministic PLC IR, semantic checks, instruction/device registry, timing and static analysis. These tests must not depend on provider calls, Web presentation, Qt state, or vendor-native automation unless the boundary itself is under test.

| Test file | File-level focus |
| --- | --- |
| `tests/test_device_entity_cleanup.py` | focused coverage for device entity cleanup |
| `tests/test_device_identity_delivery.py` | focused coverage for device identity delivery |
| `tests/test_index_register_operands.py` | focused coverage for index register operands |
| `tests/test_instruction_contract_alignment.py` | instruction registry/manual evidence/validator/schema/prompt alignment, including model-specific analog and zone-reset contracts |
| `tests/test_instruction_registry.py` | focused coverage for instruction registry |
| `tests/test_motion_control_regressions.py` | focused coverage for motion control regressions |
| `tests/test_native_validation.py` | focused coverage for native validation |
| `tests/test_pattern_routing_boundaries.py` | focused coverage for pattern routing boundaries |
| `tests/test_plc_agent.py` | focused coverage for plc agent |
| `tests/test_plc_condition_normalizer.py` | focused coverage for plc condition normalizer |
| `tests/test_plc_core_boundary.py` | Python-Core ownership of PLC semantics versus TS presentation/C# native adapters |
| `tests/test_plc_debug_loop.py` | focused coverage for plc debug loop |
| `tests/test_plc_ir.py` | IR/artifact integrity, headless generation and edits, historical workspace identity |
| `tests/test_plc_multi_agent.py` | focused coverage for plc multi agent |
| `tests/test_plc_semantics.py` | PLC semantic interpretation and saved SFC document projection without GUI |
| `tests/test_plc_static_analysis.py` | focused coverage for plc static analysis |
| `tests/test_plc_timing.py` | focused coverage for plc timing |
| `tests/test_timer_semantics_regressions.py` | focused coverage for timer semantics regressions |

### GXW reverse engineering and round-trip

Owns GXW container/object/ABI/declaration/network parsing, preservation and round-trip behavior. It must not absorb GX Works2 UI automation or generic PLC-generation contracts.

| Test file | File-level focus |
| --- | --- |
| `tests/test_gxw_cfb_allocator.py` | focused coverage for gxw cfb allocator |
| `tests/test_gxw_connectivity.py` | focused coverage for gxw connectivity |
| `tests/test_gxw_active_graph.py` | current native object identity, semantic port projection and prepared graph action verification |
| `tests/test_gxw_compiler.py` | stored compiler artifacts, offline native validation transport, phase completion and source-to-code bindings |
| `tests/test_gxw_compiler_call_tree.py` | native CallTree framing, reference maps and replay/preservation boundaries |
| `tests/test_gxw_container_appended_growth.py` | focused coverage for gxw container appended growth |
| `tests/test_gxw_container_writer.py` | focused coverage for gxw container writer |
| `tests/test_gxw_declarations.py` | focused coverage for gxw declarations |
| `tests/test_gxw_function_abi.py` | native function ABI observations across the structured 65-71 sample series |
| `tests/test_gxw_function_blocks.py` | focused coverage for gxw function blocks |
| `tests/test_gxw_generation_roundtrip.py` | focused coverage for gxw generation roundtrip |
| `tests/test_gxw_lossless.py` | native source/compiled token corpora, operand groups and labels, opaque preservation, bounded patches and failure replay |
| `tests/test_gxw_network_blocks.py` | focused coverage for gxw network blocks |
| `tests/test_gxw_object_model.py` | source-bound v2 editing, native save plans/readback, opaque preservation and generated edits against frozen interfaces |
| `tests/test_gxw_live_multitarget.py` | exact-menu allocation intervals, saved/live configuration profiles, language and codepage identity binding, and project-encoded compiler-name readback |
| `tests/test_gxw_project_import.py` | focused coverage for gxw project import |
| `tests/test_gxw_project_roundtrip.py` | focused coverage for gxw project roundtrip |
| `tests/test_gxw_project_writer.py` | focused coverage for gxw project writer |
| `tests/test_gxw_integrity.py` | observed GXW save-integrity envelope, opaque suffixes and nested stream allocation |
| `tests/test_gxw_token_encoder.py` | CPU-bound offline instruction encoding against independent frozen native records |
| `tests/test_gxw_device_comments.py` | lossless QCD address identity, row edits, Unicode and independent native table controls |
| `tests/test_gxw_offline_authoring.py` | existing ST bodies and declaration tables in one offline transaction and cross-source binding checks |
| `tests/test_gxw_sfc_offline.py` | existing SFC graph encoding, source-bound edits and unknown-region preservation |
| `tests/test_gxw_offline_project.py` | whole-project offline instruction/comment transactions, ordered warehouse source and refusal boundaries |
| `tests/test_gxw_offline_application.py` | offline GXW import, inspection and version/proposal parity across Web services and MCP |
| `web/tests/gxw-import.test.mjs` | GXW composer upload/drop, source selection, import request identity and project/version adoption |
| `tests/test_gxw_semantic_model.py` | focused coverage for gxw semantic model |
| `tests/test_gxw_structured_reader.py` | Structured GXW parsing, unknown records and read-only POU CLI selection |
| `tests/test_gxw_structured_writer.py` | focused coverage for gxw structured writer |

### GX Works2 native, CSV and synchronization

Owns GX Works2 import/export, native bridge, comment CSV, live/read-only boundaries, send/sync and vendor-specific workflows. Generic ladder semantics remain in PLC Core.

| Test file | File-level focus |
| --- | --- |
| `tests/test_fresh_gxworks2_csv_export.py` | deterministic saved-IR GX Works2 CSV export plus one model-free/read-only HTTP sentinel |
| `tests/test_gx_execution_ui_and_project_delete.py` | focused coverage for gx execution ui and project delete |
| `tests/test_gx_fast_send.py` | focused coverage for gx fast send |
| `tests/test_gxworks2_comment_export.py` | focused coverage for gxworks2 comment export |
| `tests/test_gxworks2_csv_native_regressions.py` | focused coverage for gxworks2 csv native regressions |
| `tests/test_gxworks2_import.py` | GX Works2 import semantics and compatibility edge cases |
| `tests/test_gxworks2_active_document.py` | process-bound active graph transport, verified selection, edit-mode waiting and pending native child ownership |
| `tests/test_gxworks2_live_boundaries.py` | focused coverage for gxworks2 live boundaries |
| `tests/test_live_engineering_session.py` | persistent native source revisions, paused steps, interrupted-operation reconciliation and stage-save delivery |
| `tests/test_gxworks2_sftl_native_steps.py` | focused coverage for gxworks2 sftl native steps |
| `tests/test_gxworks2_simulation.py` | focused coverage for gxworks2 simulation |
| `tests/test_gxworks2_sync.py` | GX Works2 synchronization state, scope and preservation |
| `tests/test_io_protocol_and_gx_fastpath.py` | focused coverage for io protocol and gx fastpath |

### Simulation and verification

Owns simulator/workbench execution, verification, reporting and GX Works2 simulation integration. It must consume confirmed PLC semantics rather than redefine them.

| Test file | File-level focus |
| --- | --- |
| `tests/test_simulation_workbench.py` | focused coverage for simulation workbench |
| `tests/test_simulator.py` | focused coverage for simulator |
| `tests/test_simulator_reporting.py` | Version-bound simulation report data and evidence, not dialog layout |
| `tests/test_simulator_verification.py` | focused coverage for simulator verification |
| `tests/test_simulator_workflow.py` | focused coverage for simulator workflow |

### Model runtime and provider compatibility

Owns model catalog/profile/capabilities, provider requests, detection/verification, streaming cancellation and API compatibility. It must not encode PLC domain rules.

| Test file | File-level focus |
| --- | --- |
| `tests/test_model_capabilities.py` | focused coverage for model capabilities |
| `tests/test_model_catalog_v3.py` | focused coverage for model catalog v3 |
| `tests/test_model_contract.py` | focused coverage for model contract |
| `tests/test_model_detection.py` | focused coverage for model detection |
| `tests/test_model_observations.py` | focused coverage for model observations |
| `tests/test_model_presentation.py` | focused coverage for model presentation |
| `tests/test_model_profile_config.py` | focused coverage for model profile config |
| `tests/test_model_profile_deletion.py` | focused coverage for model profile deletion |
| `tests/test_model_provider.py` | focused coverage for model provider |
| `tests/test_model_runtime_profile.py` | persisted-profile → RuntimeModelProfile materialization, legacy capability promotion and direct resolver input |
| `tests/test_model_stream_cancellation.py` | focused coverage for model stream cancellation |
| `tests/test_model_verification.py` | focused coverage for model verification |

### MCP integration

Owns MCP tool schemas, service/onboarding/client/product-launcher behavior and MCP entry-path parity. Core PLC meaning remains in Python Core.

| Test file | File-level focus |
| --- | --- |
| `tests/test_mcp.py` | public MCP tool contracts and end-to-end MCP data paths |
| `tests/test_mcp_client_activity.py` | focused coverage for mcp client activity |
| `tests/test_mcp_local_onboarding.py` | focused coverage for mcp local onboarding |
| `tests/test_mcp_onboarding_routes.py` | focused coverage for mcp onboarding routes |
| `tests/test_mcp_product_launcher.py` | focused coverage for mcp product launcher |
| `tests/test_mcp_service.py` | focused coverage for mcp service |

### Web, desktop presentation and workbench application

Owns HTTP/Web contracts, presentation state, startup, FBD UI, workbench/application persistence and desktop compatibility smoke. Presentation tests must not become a second PLC semantic implementation.

| Test file | File-level focus |
| --- | --- |
| `tests/test_application_job_errors.py` | focused coverage for application job errors |
| `tests/test_application_persistence.py` | focused coverage for application persistence |
| `tests/test_application_settings.py` | focused coverage for application settings |
| `tests/test_display_names.py` | Stable model/device identifiers and their presentation names |
| `tests/test_i18n.py` | Headless locale keys, placeholders, request language and raw payload preservation |
| `tests/test_language_workflows.py` | Request-scoped language, generation fallback, parsing and persistence without Qt |
| `tests/test_web_api.py` | HTTP auth, projects, jobs, confirmation and generation API behavior |
| `tests/test_web_architecture.py` | focused coverage for web architecture |
| `tests/test_web_contract.py` | focused coverage for web contract |
| `tests/test_web_demo_isolation.py` | focused coverage for web demo isolation |
| `tests/test_web_fbd.py` | shared FBD context, preview/candidate persistence, offline native-save routing, version preservation and resource conflicts |
| `tests/test_web_live_acceptance.py` | focused coverage for web live acceptance |
| `tests/test_web_source_entrypoints.py` | source launch/build entrypoints and inert documentation packaging, including link preservation and source-tree limits |
| `tests/test_web_startup.py` | focused coverage for web startup |
| `tests/test_workbench_service.py` | focused coverage for workbench service |
| `web/tests/requests.test.mjs` | read cancellation, serial polling, invalidation, visibility and structural sharing |
| `web/tests/conversation.test.mjs` | task purpose, creation/review stages, explicit modification/regeneration and clarification presentation |
| `web/tests/generation-review.test.mjs` | delivered capability options, budget omissions and conservative local review presentation |
| `web/tests/simulator-timeline.test.mjs` | read-only simulator timeline presentation and observation controls |
| `web/tests/gx-send.test.mjs` | browser-side GX send interaction and presentation wiring |
| `web/tests/model-parameters.test.mjs` | browser model-parameter UI behavior |
| `web/tests/module-resolution.test.mjs` | frontend module resolution, packaging-sensitive imports and production chunk boundaries |

### Runtime diagnostics, privacy and streaming

Owns diagnostics capture/export, error privacy, response-language/presentation transport, streaming workflows and multimodal request handling. Observation must not change acceptance, retries or PLC semantics.

| Test file | File-level focus |
| --- | --- |
| `tests/test_agent_b_measurement.py` | paired real-generation experiment harness and complete metering/transport observation; offline providers only |
| `tests/test_diagnostics_workflow_regressions.py` | focused coverage for diagnostics workflow regressions |
| `tests/test_multimodal_inputs.py` | Image payload/media handling and persisted attachments, not widget state |
| `tests/test_provider_error_privacy.py` | focused coverage for provider error privacy |
| `tests/test_response_language.py` | focused coverage for response language |
| `tests/test_runtime_diagnostics.py` | bounded diagnostics observation/privacy without changing runtime behavior |
| `tests/test_runtime_diagnostics_web.py` | focused coverage for runtime diagnostics web |
| `tests/test_streaming_workflows.py` | Headless generation/planning streams and original model payload preservation |

### Architecture, repository and platform boundaries

Owns source/import ownership, architecture constraints, issue-to-test linkage, Windows credential handling and small safety guards that protect repository structure rather than PLC behavior.

| Test file | File-level focus |
| --- | --- |
| `tests/test_architecture_boundaries.py` | focused coverage for architecture boundaries |
| `tests/test_architecture_boundary_cleanup.py` | retired runtime control surfaces stay retired: no context-policy import, no workflow reasoning-effort defaults, no dedicated self-hold checker module |
| `tests/test_issue_test_links.py` | focused coverage for issue test links |
| `tests/test_repository_storage.py` | Git-object size/LFS pointer policy and intermediate-commit coverage in isolated repositories; standard-library-only Linux/Windows storage checks |
| `tests/test_source_layout.py` | source/import ownership plus completeness of this test-boundary registry |
| `tests/test_windows_credentials.py` | focused coverage for windows credentials |

### Cross-cutting application and execution

Owns cross-cutting orchestration that does not fit a narrower stable owner. New tests should prefer a narrower group; this section is not a default dumping ground.

| Test file | File-level focus |
| --- | --- |
| `tests/test_execution_coordinator.py` | focused coverage for execution coordinator |
| `tests/test_execution_read.py` | focused coverage for execution read |
| `tests/test_hardware_read_only.py` | focused coverage for hardware read only |
<!-- TEST-BOUNDARY-REGISTRY-END -->

## Review checklist

Before accepting a new test file, reviewers should be able to answer: what stable contract does this file uniquely own; why no existing owner fits; why parametrization is insufficient; whether an end-to-end case duplicates a lower-level matrix; and which targeted CI workflow, if any, should own it. If those answers are weak, add the tests to an existing file instead.
