# Test ownership and file-boundary rules

This registry assigns each test file a stable responsibility. Add regression cases to the owner of the affected contract.

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

## CI placement

A test file should have one primary targeted workflow. It may also run in the full suite. Listing the same file in multiple targeted workflows is justified only when the workflows exercise meaningfully different environments or gates (for example Windows/native versus Linux/provider compatibility). Historical baseline-vs-candidate full-suite comparison is a migration/regression mechanism, not a reason to duplicate every file in every fast gate.

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
| `tests/test_context_refresh_runtime.py` | focused coverage for context refresh runtime |
| `tests/test_fx3u_rag.py` | FX3U factual retrieval quality and routing |
| `tests/test_gxw2_skill_import.py` | focused coverage for gxw2 skill import |
| `tests/test_gxw2_skill_ranking.py` | focused coverage for gxw2 skill ranking |
| `tests/test_gxw2_skill_reranker.py` | focused coverage for gxw2 skill reranker |
| `tests/test_instruction_fact_context.py` | task-directed instruction evidence assembly, source integrity and final-budget receipts; no behavioral certification |
| `tests/test_instruction_applicability_rag.py` | focused coverage for instruction applicability rag |
| `tests/test_prompt_context_budget.py` | focused coverage for prompt context budget |
| `tests/test_prompt_context_policy.py` | focused coverage for prompt context policy |
| `tests/test_prompt_slim_generation.py` | focused coverage for prompt slim generation |
| `tests/test_rag_instruction_recall.py` | focused coverage for rag instruction recall |
| `tests/test_rag_natural_language_recall.py` | focused coverage for rag natural language recall |
| `tests/test_shared_generation_context.py` | focused coverage for shared generation context |
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
| `tests/test_generation_delivery.py` | focused coverage for generation delivery |
| `tests/test_generation_fast_path.py` | focused coverage for generation fast path |
| `tests/test_generation_path_parity.py` | generation behavior parity across API/worker/MCP entry paths |
| `tests/test_generation_rejected_json_repair.py` | focused coverage for generation rejected json repair |
| `tests/test_generation_repair_assembly.py` | focused coverage for generation repair assembly |
| `tests/test_generation_repair_workflow.py` | focused coverage for generation repair workflow |
| `tests/test_generation_scope_audit.py` | focused coverage for generation scope audit |
| `tests/test_generation_workflow.py` | focused coverage for generation workflow |
| `tests/test_hybrid_compact_recovery.py` | focused coverage for hybrid compact recovery |
| `tests/test_intent_evidence_handoff.py` | ConfirmedSpec v4 / DecisionReceipt v1 lifecycle, migration atomicity, immutable version audit, and API/MCP context isolation |
| `tests/test_native_repair_scope_schema.py` | focused coverage for native repair scope schema |
| `tests/test_partial_repair_semantic_freeze.py` | focused coverage for partial repair semantic freeze |
| `tests/test_rejected_generation_delivery.py` | focused coverage for rejected generation delivery |
| `tests/test_repair_render_delivery.py` | focused coverage for repair render delivery |
| `tests/test_repair_responsibility_simplification.py` | focused coverage for repair responsibility simplification |
| `tests/test_review_noise_regressions.py` | focused coverage for review noise regressions |
| `tests/test_user_confirmed_generation_repair.py` | focused coverage for user confirmed generation repair |

### Deterministic PLC Core and instruction semantics

Owns deterministic PLC IR, semantic checks, instruction/device registry, timing and static analysis. These tests must not depend on provider calls, Web presentation, Qt state, or vendor-native automation unless the boundary itself is under test.

| Test file | File-level focus |
| --- | --- |
| `tests/test_analog_instruction_regressions.py` | focused coverage for analog instruction regressions |
| `tests/test_device_entity_cleanup.py` | focused coverage for device entity cleanup |
| `tests/test_device_identity_delivery.py` | focused coverage for device identity delivery |
| `tests/test_index_register_operands.py` | focused coverage for index register operands |
| `tests/test_instruction_contract_alignment.py` | instruction registry/manual evidence/validator/schema/prompt alignment |
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
| `tests/test_zrst_registry_alignment.py` | focused coverage for zrst registry alignment |

### GXW reverse engineering and round-trip

Owns GXW container/object/ABI/declaration/network parsing, preservation and round-trip behavior. It must not absorb GX Works2 UI automation or generic PLC-generation contracts.

| Test file | File-level focus |
| --- | --- |
| `tests/test_gxw_cfb_allocator.py` | focused coverage for gxw cfb allocator |
| `tests/test_gxw_connectivity.py` | focused coverage for gxw connectivity |
| `tests/test_gxw_compiler.py` | stored compiler artifacts, native component/table observations and source-to-cache bindings |
| `tests/test_gxw_compiler_call_tree.py` | native CallTree framing, reference maps and replay/preservation boundaries |
| `tests/test_gxw_container_appended_growth.py` | focused coverage for gxw container appended growth |
| `tests/test_gxw_container_writer.py` | focused coverage for gxw container writer |
| `tests/test_gxw_declarations.py` | focused coverage for gxw declarations |
| `tests/test_gxw_function_abi.py` | focused coverage for gxw function abi |
| `tests/test_gxw_function_abi_67_71.py` | focused coverage for gxw function abi 67 71 |
| `tests/test_gxw_function_blocks.py` | focused coverage for gxw function blocks |
| `tests/test_gxw_generation_roundtrip.py` | focused coverage for gxw generation roundtrip |
| `tests/test_gxw_lossless.py` | native source/compiled token corpora, operand groups and labels, opaque preservation, bounded patches and failure replay |
| `tests/test_gxw_network_blocks.py` | focused coverage for gxw network blocks |
| `tests/test_gxw_object_model.py` | focused coverage for gxw object model |
| `tests/test_gxw_project_import.py` | focused coverage for gxw project import |
| `tests/test_gxw_project_roundtrip.py` | focused coverage for gxw project roundtrip |
| `tests/test_gxw_project_writer.py` | focused coverage for gxw project writer |
| `tests/test_gxw_semantic_model.py` | focused coverage for gxw semantic model |
| `tests/test_gxw_structured_reader.py` | Structured GXW parsing, unknown records and read-only POU CLI selection |
| `tests/test_gxw_structured_writer.py` | focused coverage for gxw structured writer |

### GX Works2 native, CSV and synchronization

Owns GX Works2 import/export, native bridge, comment CSV, live/read-only boundaries, send/sync and vendor-specific workflows. Generic ladder semantics remain in PLC Core.

| Test file | File-level focus |
| --- | --- |
| `tests/test_fresh_gxworks2_csv_export.py` | focused coverage for fresh gxworks2 csv export |
| `tests/test_fresh_gxworks2_csv_http.py` | focused coverage for fresh gxworks2 csv http |
| `tests/test_gx_execution_ui_and_project_delete.py` | focused coverage for gx execution ui and project delete |
| `tests/test_gx_fast_send.py` | focused coverage for gx fast send |
| `tests/test_gxworks2_comment_export.py` | focused coverage for gxworks2 comment export |
| `tests/test_gxworks2_csv_native_regressions.py` | focused coverage for gxworks2 csv native regressions |
| `tests/test_gxworks2_import.py` | GX Works2 import semantics and compatibility edge cases |
| `tests/test_gxworks2_live_boundaries.py` | focused coverage for gxworks2 live boundaries |
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
| `tests/test_web_fbd.py` | focused coverage for web fbd |
| `tests/test_web_frontend_state_flow.py` | focused coverage for web frontend state flow |
| `tests/test_web_live_acceptance.py` | focused coverage for web live acceptance |
| `tests/test_web_source_entrypoints.py` | focused coverage for web source entrypoints |
| `tests/test_web_startup.py` | focused coverage for web startup |
| `tests/test_workbench_service.py` | focused coverage for workbench service |
| `web/tests/gx-send.test.mjs` | browser-side GX send interaction and presentation wiring |
| `web/tests/model-parameters.test.mjs` | browser model-parameter UI behavior |
| `web/tests/module-resolution.test.mjs` | frontend module resolution and packaging-sensitive imports |

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
| `tests/test_issue_test_links.py` | focused coverage for issue test links |
| `tests/test_source_layout.py` | source/import ownership plus completeness of this test-boundary registry |
| `tests/test_source_layout_regression_gate.py` | focused coverage for source layout regression gate |
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
