# Analysis modes

The Web composer separates task purpose from workflow and analysis strategy: **创建程序**, **修改程序**, and **工程问答**. [conversationRoute](../../web/src/features/conversationRouting.ts) maps presentation state to the existing application jobs. Each task retains its own input draft; selecting a task never changes a submitted job.

New Ladder creation defaults to **直接生成**: `kind="direct_generation"` sends the complete original requirement to one generation call, then uses the shared CandidateService, IR, local version save and CSV export. It invokes neither Agent A nor a mandatory specification review. Internal model choices are not persisted as `confirmed_spec`. ST and FBD use their existing analysis and format-specific generation services.

The optional **先确认规格** flow uses Agent A, a specification review, and generation. Its **单方案** strategy maps to `analysis_mode="direct"`; **比较方案** explicitly selects `design`. The confirmed stage exposes an action button rather than another top-level mode. Historical analysis and generation retries preserve their original identity and mode. Request complexity and missing facts never select Design.

Only an analysis mode selected by the caller can enable Design.

## Modification and questions

`JobCreate.generation_action` distinguishes `edit` from `regenerate` inside the existing generation job. [WorkbenchService._submit_job](../../src/application/workbench.py) freezes the action and selected baseline. Editing requires a selected version and a nonempty amendment, retains the baseline, and can apply the existing change scope. Complete regeneration requires a Ladder version and a confirmed specification, removes old-program content from the generation context, and keeps the version binding for diff and save checks. A missing specification first enters analysis and confirmation. Regeneration cannot also request a local change scope or repair. An omitted action preserves the earlier API generation contract.

Web `kind="agent"` jobs are engineering questions. [ReadOnlyToolRuntime](../../src/agent_runtime/runtime.py) filters discovery and invocation with [READ_ONLY_TOOL_NAMES](../../src/agent_runtime/plc_tools.py), including rejection of unadvertised calls and proposed engineering actions returned by a custom runtime. The workbench does not create proposals from these jobs. External engineering tools retain the shared registry and existing approval policy; analysis strategy and model tuning do not grant operation permissions.

## One-call generation and clarification

[GenerationRequest.direct_generation](../../src/application/generation.py) selects the raw generation branch. [build_direct_generation_context](../../src/application/confirmed_generation_context.py) reuses Core declaration extraction, existing user facts, CPU information, targeted retrieval and model budgets. The full request reaches the model; Direct performs no model context compaction or automatic repair. If the known context budget cannot fit the facts it fails locally rather than truncating them.

An adequate response is the existing compact ladder object. The same call may instead return `{"status":"needs_input","missing_info":[...]}` using the existing question structure. This response cannot contain a program, creates no version or CSV, and remains a completed job awaiting user input. A continuation uses `kind="direct_generation"`, a new `request_id` and `clarification_job_id` bound to that job. The application retains the original request and answers, verifies project/CPU/spec/version state, and freezes attachments and the provider for each submitted job. Core declaration extraction uses only human input; model questions remain context and cannot create user I/O facts. Repeating the same request ID returns the original job.

Local structure/address/instruction and IR checks do not certify process timing, restart or phase exclusion. Direct records these coverage gaps in its generation handoff and displays a review note with the saved result. MCP obtains the same raw context and Core through `get_generation_context` and `create_program_candidate`; its existing engineering confirmation rules still apply, and the server makes no nested model call.

## Analysis contracts inside the detailed flow

Inside the detailed flow, `analysis_mode="direct"` asks Agent A for one concrete implementation and uses targeted factual retrieval. With an existing selected approach, the internal pinned/extract path preserves the implementation and processes the amendment. Approach-specific guidance is a delta, not a second copy of the specification.

Design adds design evidence and asks for distinct candidate approaches. Existing confirmed I/O and parameters remain available during exploration. Candidate selection and required-parameter review precede generation.

The mode is analysis input, not a generation constraint or a model tuning setting. Agent B receives the confirmed implementation under the [generation execution policy](generation-execution-policy.md).

## Ownership

[JobCreate](../../src/integrations/web/schemas.py) defines the request field and default. [WorkbenchService](../../src/application/workbench.py) resolves and freezes it in the job before execution. [application.analysis_context](../../src/application/analysis_context.py) assembles the mode-specific prompt; [knowledge.scope](../../src/knowledge/scope.py) selects evidence lanes. A model-authored mode cannot replace the application choice.

Agent A's output fields belong to [application.analysis_results](../../src/application/analysis_results.py) and its prompt contract. Normalization diagnostics and execution semantics are produced by Core, not delegated to a model-authored metadata field. Legacy display metadata remains readable in old snapshots but is excluded from new analysis context.

## Review and retries

The assembler selects an internal information stage independently of Direct/Design. With unresolved required I/O questions or no current device facts it uses the requirements contract: control intent, one implementation in Direct, logical identities and necessary questions. It retains explicit timing, recovery and low-level restrictions but omits address-based effects, behavior and construction protocol instructions. Current device facts or current I/O rows allow the bound contract; historical/deleted bindings alone do not.

In either stage, a structurally valid optional execution claim whose copied evidence does not ground its claimed device is retained in the execution-intent receipt as `pending_binding`. Core still rejects that binding; the application retains the candidate and reason without adding evidence, inferring aliases or sending a model repair. It contributes no executable constraint, and filling addresses or confirming the review draft never promotes it. Existing uniquely confirmed aliases still ground valid claims.

The requirements stage also defers an otherwise valid claim whose source device is absent. Fabricated evidence, illegal device syntax, other shape/state failures and ungrounded explicit user constraints remain blocking and retain the existing bounded repair policy. The stage does not change model parameters or select Design.

The sole Direct approach can be preselected. Suggested parameter defaults are not confirmed answers; real missing required parameters remain unresolved. Mode changes affect the next submitted job, while retries retain the original mode and request identity.

I/O question identity, polarity and labels are covered in [I/O purpose labels](io-binding-labels.md). Regression owners are [test_analysis_prompt_routing.py](../../tests/test_analysis_prompt_routing.py), [test_analysis_prompt_integration.py](../../tests/test_analysis_prompt_integration.py) and [test_analysis_mode_jobs.py](../../tests/test_analysis_mode_jobs.py).
