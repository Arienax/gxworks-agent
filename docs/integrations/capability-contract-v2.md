# Capability Contract v2

## What is implemented

One endpoint/model observation now owns a versioned `CapabilityContract`, with typed `CapabilityDescriptor`, `ParameterDescriptor` and `ConstraintDescriptor` entries. No model-name rules determine controls. The old two-name constant is retained as `LEGACY_PARAMETER_NAMES` only for v1 migration; it is not the v2 schema or discovery allowlist.

Three layers are separate:

- Observation: `capabilityContract` in a stored profile; `contract` in the HTTP API.
- Selection: `userModelSettings`; `user_settings` in the API. A choice is `{mode: "value", value: ...}`, `{mode: "omit"}` or `{mode: "inherit"}`.
- Effective request: `model_request_policy.resolve_request` returns resolved options, per-parameter sources and values, without mutating either observation or selection.

`POST /api/settings/detect` returns one `discovery.contract`, not three independent capability/support/probe-result documents. `GET /api/settings` returns the cached scoped contract and selections; it does not call the model. Normal provider requests do not run discovery. Old `parameter_support` is kept in the profile DTO for legacy readers, not as the v2 runtime's authority.

## Scope and evidence

Scope contains `endpoint`, `model`, `context`, and `binding` (a one-way credential fingerprint, never the key). Endpoint, model, credential or non-parameter request context changes invalidate the observation. The context excludes dynamically declared parameter wire paths, but includes legacy protocol flags. A key rotation clears the cached contract and selections; normal requests reject stale nonempty selections rather than leaking them into a different model.

Changing a parameter does not rewrite observation. `requires` binds a conditional result to tested settings. For example, the built-in temperature probe records the reasoning mode under which those values were tested. A new mode disables the old temperature control and clears its selection; explicit re-detection can supply new evidence. No automatic per-request probes or combinatorial exploration are performed.

Capability states: supported / unsupported / unknown / conditional. Parameter states additionally include accepted (an invalid negative control was also accepted; the parameter may be ignored) and fixed. A timeout or malformed probe result is unknown, not unsupported. A successful request is not proof of causal effect on the underlying model.

## Open descriptor space, bounded probe registry

`model_contract.metadata_contract_parts` accepts arbitrary declared scalar parameters from `parameters` or `parameter_schema`, including JSON Schema `properties`, `enum`, `const`, `minimum`, `maximum`, `multipleOf`, and boolean/string/integer/number types. Capability maps and context-window metadata are preserved separately. Unrecognized JSON Schema constructs are skipped, not widened into unconstrained controls. This is a deliberately bounded scalar subset, not a general JSON Schema engine.

`model_probes.PROBE_REGISTRY` is only the list of safe, bounded active strategies. It initially contains reasoning effort, temperature, function tools and JSON-object output. New metadata parameters need no registry entry; adding an active strategy does not require provider or UI branches. Tools are validated by an actual synthetic function call and arguments, not executed against PLC tooling. The output probe establishes JSON-object mode only, not JSON-schema strictness, every modality, parallel execution, or context-window size.

There is no universal OpenAI-compatible capability-description endpoint. Endpoints that only return model IDs cannot provide all these facts automatically. Undeclared/unprobed capabilities remain unknown. An operator can explicitly submit validated `source: manual` descriptors through the settings API. Vendor presets contain addresses only; the model-name examples in design discussions are not evidence or built-in defaults.

## Parameter and wire example

This fragment is illustrative metadata, not a claim about any named model:

```json
{
  "parameters": {
    "enable_thinking": {"type": "boolean"},
    "thinking_budget": {
      "type": "integer",
      "minimum": 0,
      "maximum": 32768,
      "multipleOf": 1024,
      "requires": {"enable_thinking": [true]},
      "wire_location": "extra_body",
      "wire_path": ["thinking", "budget_tokens"]
    },
    "verbosity": {"enum": ["quiet", "normal", "verbose"]}
  },
  "capabilities": {"vision": true, "audio": false},
  "context_window": 262144
}
```

The generated UI contains a switch, an integer slider and an enum slider. Selecting budget 8192 writes `extra_body.thinking.budget_tokens = 8192`, not a top-level logical `thinking_budget`. Unrelated extension siblings are retained. A large numeric domain, or one without a declared step/range, uses a numeric input rather than thousands of slider positions. Fixed values are read-only; unsupported controls are hidden; unknown/accepted observations appear in the advanced section and can be configured through explicit advanced settings.

The UI reads `contract.parameters` and renders by type. It does not know supplier/model names. Parameter labels are plain escaped text. There are no dynamic script, template, URL, header or executable mappings.

## Policy, constraints and protected ownership

Priority is canonical protocol/validation > explicit user selection > advanced user overrides > workflow hint > profile default > server omission. Advanced overrides are existing explicitly entered user settings, not a second workflow-hint source. For migrated v1 sliders, previous chosen values become explicit selections when adopted. Legacy profiles not adopting v2 retain their previous merge behavior.

An absent selection inherits. Explicit `omit` removes every declared wire/logical alias and prevents a workflow from resurrecting that parameter. Explicit boolean false is a value, not omission. Even an entire `extra_body: null` deletion does not resurrect nested profile defaults. The service validates selection types/ranges and scope on save; the request policy validates final values and conditions again before sending.

`requires` is a conjunction of membership conditions, and `conflicts_with` forbids the simultaneous presence of another selected parameter. Null in a requirement means the parameter is omitted. Both descriptor-level and top-level constraints apply. Conditions on `tools`, `stream` and `response_format` are evaluated against the actual request, not just the model's advertised abilities. Editor checks cannot replace runtime checks. Known incompatible tool or structured-output requests fail visibly; they are not silently degraded into prose or stripped of schemas.

Wire mappings support `body` / `extra_body` and bounded nested paths. Canonical messages/model/tools/tool choice/stream/response format, credential/header/client fields and prototype-related paths cannot be owned by a parameter. Overlapping parent/child or alias paths are rejected. The existing provider still serializes canonical messages and private tool replay state. PLC registry, safety checks, approvals and GX/Simulator operations are unchanged.

## Compatibility and limits

The transport remains Chat Completions-compatible. Declaring audio/video/context size is descriptive data, not implementation of new upload protocols or native provider SDKs. Vision consumes its scoped contract and final conditions; native audio/video inputs are not added. Legacy Qt UI is unchanged. JSON-schema and parallel-tool capability facts need endpoint metadata/manual evidence until dedicated strategies are registered.

Probes use fixed synthetic prompts with the existing 90-second scheduling budget and per-request 15-second maximum, no retries, and bounded output. Discovery may cost API tokens. It never sends project files or conversation history. No real provider credentials are included in tests.

## Validation

```sh
python -m pytest -q tests/test_model_contract.py tests/test_model_capabilities.py tests/test_model_detection.py tests/test_model_provider.py tests/test_application_settings.py tests/test_web_contract.py
node --experimental-strip-types --test web/tests/model-parameters.test.mjs
python scripts/export_web_schema.py
npm run types --prefix web
npm run build --prefix web
python scripts/web_model_settings_e2e.py --web-dist web/dist --evidence model-api-evidence
```

The real-browser test substitutes only the model transport, not settings HTTP/auth/storage. It covers unknown metadata controls, boolean false/true, nested budget mapping, dependencies, observation/selection separation, persistence/reload, explicit omission versus workflow hints and model-scope invalidation. Live provider-account behavior and Windows/PLC integration still require operator acceptance.

## Two-stage discovery

`POST /api/settings/detect` accepts `mode: "list" | "quick" | "deep"`
(default `quick`) and `refresh: boolean` (default `false`). These are command
options, not profile settings or provider request parameters. The Web settings
panel exposes **获取模型列表**, **快速能力检测**, and **深度参数扫描** separately.
The existing **测试连接** route remains independent and never runs discovery.

- `list`: fetch availability only, even when a model is already selected. Never
  chooses the first model, sends a completion, or replaces the current contract.
  An empty model on the quick route also returns the list without probing.
- `quick`: metadata first; absent declarations use a negative control and **one**
  positive sample per registered parameter (the explicit draft value, otherwise
  the strategy's quick sample). Tools and JSON-object checks run independently
  beside the ordered parameter lane, with at most three workers. Temperature
  still follows reasoning. A normal cold run with no metadata makes one model
  listing plus at most seven completion calls; the legacy output-limit fallback
  can add one rejected request. Rejected/ignored controls or cached evidence
  reduce that count. Quick mode does not enumerate all effort/temperature levels.
- `deep`: manually extends registered parameter candidate sets, skipping known
  positive samples and preserving the scoped quick capability evidence. It does
  **not** repeat tool or structured-output generations. The UI enables it after
  a scoped quick result. A direct deep request without one leaves missing
  capabilities unknown rather than guessing them.

Quick scheduling uses a 20-second total budget and up to 5 seconds per SDK
request; deep uses 90 seconds and up to 15 seconds. All probes disable SDK
retries, apart from the existing named output-limit compatibility fallback.
These are **scheduling and network I/O limits**, not a guaranteed wall-clock
latency SLA: SDK read/connect timeouts are phase/inactivity based, and in-flight
requests are joined before returning. No worker keeps launching scans after the
result is delivered. An authentication/rate-limit error stops scheduling; a
failed preflight prevents the remaining generations. Expired budget, transient
failure, and an untested value are not evidence of unsupported functionality.

A probe parameter may carry `scan: "partial" | "complete"`. This describes
coverage of its registered **finite candidate set**, never the entirety of a
provider's possible domain. Existing v2 contracts without `scan` remain valid.
A quick positive sample remains `status: "supported", scan: "partial"`, not
`fixed`; discrete samples do not imply a continuous range. The editor labels
partial evidence, and only verified/declaratively valid values become controls.
A deep scan interrupted by a timeout retains earlier validated positive values
and remains partial. Metadata remains authoritative; adding a metadata-defined
scalar control does not require adding a probe or a model-name branch.

Reusable results must match endpoint, model, non-parameter context, and credential
fingerprint. Dependent parameter evidence also has to match current `requires`
values (e.g. the selected reasoning effort), because tuning values themselves
are excluded from the context fingerprint. Deep results are not narrowed by a
later quick check. **重新验证已有结果** in Advanced settings explicitly discards
reusable registered samples; a deep refresh still only rechecks parameters.
Saving/reloading retains observations and user selections separately. Listing,
testing the connection, normal requests, or switching models never silently
starts a deep scan.

Discovery responses include `mode`, actual `elapsed_ms`, `partial`,
`parameters_pending`, `parameters_reused`, `budget_seconds`, `budget_exhausted`,
and `preflight_failed`. These are diagnostic summaries, not parameters sent to
the model. The UI shows an elapsed timer while waiting and actual timing on
completion, never a fabricated percentage. No secret or raw provider error body
is included.

Regression coverage: `tests/test_model_detection_stages.py`, updated HTTP tests,
`web/tests/model-parameters.test.mjs`, and `scripts/web_model_settings_e2e.py`.
All use synthetic transports and disposable settings; they are not live provider
or PLC acceptance tests.
