# Model capability contracts

## Local resolution and explicit verification

Capability configuration resolves from the local catalog, scoped declarations, cached metadata and manual overrides. Loading or saving this configuration does not generate model output. Listing models and testing list-endpoint access are separate from verifying a model's generation capability.

The current interface offers bounded, explicitly confirmed single-capability verification. The former `quick` action resolves locally; the former `deep` batch scan returns a migration error before I/O. Verification limits and fixtures are owned by [model_runtime.verification](../../src/model_runtime/verification.py) and [model_runtime.probes](../../src/model_runtime/probes.py). Cancellation before confirmation sends no verification request.

## Descriptor roles

[CapabilityContract](../../src/model_runtime/contract.py) and its descriptors define the schema. `domain` carries declared type/range/enum constraints; `ui_hint` describes the control; `evidence` records observations; saved settings select a value, omission or inheritance. A slider increment is not a wire-level numeric grid. Accepted samples do not define a continuous range or exhaustive enum.

Unknown controls remain editable and may offer suggestions plus exact input. Declared unsupported or fixed entries remain visible in advanced settings. Contradictory explicit choices are shown for correction rather than silently deleted.

[resolve_request](../../src/model_runtime/request_policy.py) maps the active profile to provider parameters. Explicit omission remains omission. Project/workflow tuning is retired as described in [runtime ownership](../architecture/runtime-ownership.md).

## Catalog identity and overrides

The catalog lives in [resources/model_catalog](../../resources/model_catalog/). Each entry declares exact endpoint/model matches, source references and review date. Catalog inheritance and descriptor validation belong to [model_runtime.catalog](../../src/model_runtime/catalog.py); similar model names and third-party gateways do not inherit an official endpoint's contract automatically.

Private aliases can use manual capability override JSON in model settings. Declare only the fields supported by that endpoint; the example below illustrates a declaration, not detected provider behavior:

```json
{"parameters":{"custom_flag":{"type":"boolean","status":"unknown"}}}
```

Protected protocol and credential paths are enforced by the contract/request-policy implementation. Changing model or endpoint requires a contract in the new scope. Catalog updates retain the user's selection and separate historical evidence.

## Observation and storage

[SettingsService.model_snapshot](../../src/application/settings.py) can attach an observer to an ordinary request. [model_runtime.observations](../../src/model_runtime/observations.py) stores bounded, scoped request evidence without issuing another generation request or rewriting declared domains. Completed acceptance, transport failure and unsupported-parameter evidence remain separate.

Observation TTL, row limits and accepted value types are code-owned in `model_runtime.observations`. Settings and database paths are described in [model settings](../guides/model-settings.md#storage). Records exclude prompts, raw model responses, tool arguments and credentials.

## Compatibility and verification

Legacy profile fields are interpreted only by `model_runtime.legacy_migration` and projected into the current contract/settings view before runtime use. Old probe samples remain evidence; explicit values and omissions become canonical selections. `parameterSupport`, `generationDefaults`, `requestOverrides`, and the old boolean capability bag are no longer public settings/runtime APIs. Reading an old configuration does not rewrite it; changing endpoint/model drops its hidden legacy runtime state so it cannot leak to a new identity.

[test_model_catalog_v3.py](../../tests/test_model_catalog_v3.py), [test_model_contract.py](../../tests/test_model_contract.py) and [test_model_verification.py](../../tests/test_model_verification.py) cover resolution, mapping and bounded verification. Earlier catalog/probe design is retained in the [process archive](../process/README.md).
