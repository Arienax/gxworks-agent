# OpenAI-compatible transport

[OpenAICompatibleProvider](../../src/model_runtime/provider.py) maps canonical model requests to the compatible chat-completions transport. It converts provider content, reasoning, tool calls, finish data and usage into the shared runtime events.

## Configuration

Endpoint and model identity are explicit profile inputs. Supported controls, wire paths, omission and declared constraints come from the [capability contract](capability-contract-v3.md). Unknown gateways remain configurable without inheriting another endpoint's verified claims.

`_request_params` in [provider.py](../../src/model_runtime/provider.py) owns SDK request construction. [resolve_request](../../src/model_runtime/request_policy.py) owns parameter resolution, and [model_runtime.transport_policy](../../src/model_runtime/transport_policy.py) owns transport policy. Do not copy provider-specific parameter lists into workflow prompts or TypeScript.

## Responses

Application-owned model calls pass through `collect_response` in [provider.py](../../src/model_runtime/provider.py). Public response fields and preserved provider-private reasoning have different owners. Language and response acceptance are described in [response language](../architecture/response-language.md).

Streaming fallback and ordinary generation retries follow [generation call contracts](../architecture/generation-call-contracts.md). An authentication error, timeout or missing models endpoint is not evidence that an arbitrary parameter is unsupported.

## Tests and history

[test_model_provider.py](../../tests/test_model_provider.py) exercises actual adapter request construction against controlled SDK responses. [test_model_contract.py](../../tests/test_model_contract.py) checks parameter mapping. Live provider results require the exact endpoint/model and test date in a versioned report.

The earlier batch-probe investigation is preserved in the [process archive](../process/README.md); it is not the current settings workflow.
