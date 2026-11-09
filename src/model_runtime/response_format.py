"""Choose an available wire mode before generating; never probe or retry a model.

The same local compact contract applies in all modes. Provider names are not
used: a materialized v3 declaration owns structured-output and streaming.
"""
from __future__ import annotations

import copy

from model_runtime.request_policy import resolve_request
from model_runtime.runtime_profile import (
    RuntimeModelProfile,
    materialize_runtime_profile,
)


def response_plan(profile, schema, *, model=None, api_key=None, hints=None):
    runtime = (
        profile
        if isinstance(profile, RuntimeModelProfile)
        else materialize_runtime_profile(profile, api_key=api_key, model=model)
    )
    descriptor = runtime.contract.capabilities.get("structured_output")
    modes = []
    if descriptor and descriptor.status in {"supported", "conditional"}:
        declared = list(descriptor.modes or ("json_object",))
        if (
            "json_schema" in declared
            and descriptor.source
            in {"metadata", "catalog", "manual", "legacy"}
        ):
            modes.append("json_schema")
        if "json_object" in declared:
            modes.append("json_object")
    # JSON content remains required locally even without server-side format
    # enforcement. This prevents known unsupported response_format errors.
    modes.append(None)

    streaming = runtime.contract.capabilities.get("streaming")
    streams = (
        [False]
        if streaming and streaming.status == "unsupported"
        else [True, False]
    )

    last_error = None
    for mode in modes:
        value = None if mode is None else {"type": mode}
        if mode == "json_schema":
            value["json_schema"] = {
                "name": "confirmed_spec_compact_ladder",
                "strict": True,
                "schema": copy.deepcopy(schema),
            }
        for stream in streams:
            try:
                resolve_request(
                    runtime,
                    hints,
                    protocol={"stream": stream, "response_format": value},
                    model=runtime.model,
                    api_key=api_key,
                )
            except ValueError as error:
                last_error = error
                continue
            return {"response_format": value}, stream
    raise last_error or ValueError("No compatible response mode")
