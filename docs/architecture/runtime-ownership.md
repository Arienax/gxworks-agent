# Runtime ownership

## Model tuning

The saved model profile owns tuning. Project/workflow `effort` is a retired compatibility field; accepted legacy arguments do not set request parameters. Direct and Design select analysis behavior only.

[without_workflow_effort](../../src/model_runtime/request_policy.py) removes workflow reasoning hints before provider resolution. [resolve_request](../../src/model_runtime/request_policy.py) applies the profile's saved value, omit and inherit state. Legacy flat-config migration in [storage.config](../../src/storage/config.py) retains explicit saved values and discards retired placeholders rather than restoring a preset maximum. Explicit capability probes use their trial profiles.

User-facing configuration and storage instructions are in [model settings](../guides/model-settings.md). Capability schema ownership is in [capability contracts](../integrations/capability-contract-v3.md).

## Retrieval

Application, agent runtime and integrations enter through [knowledge.retriever](../../src/knowledge/retriever.py). Task/source routing in [knowledge.scope](../../src/knowledge/scope.py) runs before backend candidate limits. Caller source lanes can narrow the task policy. Legacy public `knowledge.core` names forward to the same facade.

[Knowledge maintenance](../../resources/knowledge/README.md) owns index and source procedures; [instruction-fact delivery](instruction-fact-delivery.md) owns prompt evidence selection. The existing [architecture tests](../../tests/test_architecture_boundaries.py) cover cross-layer imports.

## Persistent settings implementation

[storage.user_data](../../src/storage/user_data.py) owns path resolution, the inter-process lock and crash recovery. [storage.config.migrate_user_settings](../../src/storage/config.py) owns legacy location discovery and profile loading. Path lookup itself performs no migration.

Migration stages files in the destination directory, checks the SQLite backup, publishes a recoverable journal, then replaces each target atomically. The config/database pair is recoverable across interruption; it is not a multi-file atomic rename. Existing targets remain authoritative and legacy sources remain available. Conflict recovery preserves staged data and reports the conflicting target.

The source/frozen diagnostics in [scripts.web_entry](../../scripts/web_entry.py) expose `--settings-path-info` and the disposable `--self-test-settings`. Historical test results are recorded in the [version reports](../reports/README.md).
