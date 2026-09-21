# Generation call contracts

Follow-up to source-layout head `77971d74698b8f71608352ed2c49e60d84132913`.
This change fixes call contracts; it does not change PLC acceptance rules,
artifact formats, workspace approval, stored snapshots or credential handling.

## Engineering semantics versus wire format

`application.confirmed_generation_context` owns a detached
`ConfirmedGenerationContext` snapshot. Both the built-in compact adapter and the
MCP/full-ladder adapter apply its projection before retrieval or prompt routing.
The snapshot carries selected CPU, canonical confirmed specification, confirmed
I/O bindings, structured generation contract, sanitized evidence, current program
and generation request. The server-bound CPU also selects the opcode schema.

First confirmed generation uses the same fixed translation instruction. It does
not turn fresh unconfirmed prose into new engineering requirements. Editing keeps
the user's explicit delta and the current baseline. Repair modes keep their own
bounded contexts and never enter ordinary generation through this builder.

Compact `{"r": [...]}` and full/partial `ladder_v1` remain distinct wire protocols.
Agent B still makes one completion and stops at the first complete JSON object.
MCP's output schema now receives the selected CPU; every APP_INSTR enum equals
`generation_app_instr_mnemonics(plc_model)`, including partial-edit branches.
The semantic snapshot is not redundantly serialized into another MCP payload.

## Model calls and transport downgrade

`application.model_api.current_provider()` and `request_model()` are the narrow
public gateway used by Agent B. The provider remains frozen for the workflow.
The established private hooks remain internal implementation/injection details.

`GenerationWorkflow` never starts `generate_model_json()` because another attempt
failed or returned empty content. Its legacy `generate_json` injection field is
retained for construction compatibility, not permission for a second completion.
Ordinary generation sends `max_retries=0`; transport choice and the only allowed
downgrade live in `model_runtime`, not in application exception handling.

The runtime may select non-streaming immediately when a correctly scoped contract
already declares streaming unsupported. A caller-opted-in downgrade after a
failure is allowed only for a structured `stream_not_supported` rejection (or a
structured server `unsupported_parameter` for `stream`), at HTTP 400/405/422 or
an explicitly typed adapter rejection, and only before any content, reasoning,
tool, usage or opaque provider-state event. The frozen request is retried at most
once with only `stream` changed. A failed non-stream attempt is not retried here.

401/403, ordinary 400, 429, 5xx, connection failures, timeouts, generic protocol
errors, `stream_protocol_error`, unknown exceptions and response acceptance errors
never authorize a stream downgrade. In particular a broken stream may already
have consumed paid computation. Error prose is not parsed as retry authorization.
Explicit user values are not erased to make a request succeed.

The tool agent no longer sends a hard-coded `reasoning_effort="high"`. Profile
settings, explicit selection, omission and the capability domain stay authoritative;
there is no invented mapping from high to a provider-specific enum.

## Deterministic candidate service

Both `GenerationWorkflow` and `PLCCore` enter `plc.candidate_service.CandidateService`
for preparation and compilation. Existing normalization, partial materialization,
IR validation, strict/debug versus generation-structural profiles and renderers
remain the implementation. The service does not save versions, accept proposals,
invoke models, access credentials, synchronize GX or execute a PLC.

## Regression evidence

`tests/test_call_contract_regressions.py` checks CPU-specific schemas, shared
semantic/evidence projection, snapshot isolation, exact request counts, restricted
stream downgrade, nonstandard effort vocabularies, public gateway use, and entry
through the same candidate service for Web and an actual MCP client. Existing
byte-level artifact parity and approval/storage tests remain in the full suite.

Historical tests expecting an authentication/timeout replay were corrected to
assert the new fail-closed behavior. Positive fallback tests now simulate an
explicit stream rejection before output. The two renamed parameterized test
families have explicit mappings in `scripts/source_layout_regression.py`; missing,
skipped or newly failing cases still fail the comparison. Baseline failures remain
reported separately; comparison success does not mean every old test is green.

## Profile-owned tuning

Production workflow effort hints are neutral, including legacy API and project
inputs. Saved model settings remain authoritative in capability-contract and
contractless profiles. See [runtime ownership](runtime-ownership.md).
