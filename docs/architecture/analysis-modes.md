# Analysis modes

`analysis_mode` is selected by the caller. Direct is the default; Design is enabled only by an explicit selection. Request complexity, keywords, missing parameters and a selected approach do not select Design.

## Direct and Design

Direct asks Agent A for one concrete implementation and uses targeted factual retrieval. With an existing selected approach, the internal pinned/extract path preserves the implementation and processes the amendment. Approach-specific guidance is a delta, not a second copy of the specification.

Design adds design evidence and asks for distinct candidate approaches. Existing confirmed I/O and parameters remain available during exploration. Candidate selection and required-parameter review precede generation.

The mode is analysis input, not a generation constraint or a model tuning setting. Agent B receives the confirmed implementation under the [generation execution policy](generation-execution-policy.md).

## Ownership

[JobCreate](../../src/integrations/web/schemas.py) defines the request field and default. [WorkbenchService](../../src/application/workbench.py) resolves and freezes it in the job before execution. [application.analysis_context](../../src/application/analysis_context.py) assembles the mode-specific prompt; [knowledge.scope](../../src/knowledge/scope.py) selects evidence lanes. A model-authored mode cannot replace the application choice.

Agent A's output fields belong to [application.analysis_results](../../src/application/analysis_results.py) and its prompt contract. Normalization diagnostics and execution semantics are produced by Core, not delegated to a model-authored metadata field. Legacy display metadata remains readable in old snapshots but is excluded from new analysis context.

## Review and retries

The sole Direct approach can be preselected. Suggested parameter defaults are not confirmed answers; real missing required parameters remain unresolved. Mode changes affect the next submitted job, while retries retain the original mode and request identity.

I/O question identity, polarity and labels are covered in [I/O purpose labels](io-binding-labels.md). Regression owners are [test_analysis_prompt_routing.py](../../tests/test_analysis_prompt_routing.py), [test_analysis_prompt_integration.py](../../tests/test_analysis_prompt_integration.py) and [test_analysis_mode_jobs.py](../../tests/test_analysis_mode_jobs.py).
