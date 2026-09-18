"""Choose an available wire mode before generating; never probe or retry a model.

The same local compact contract applies in all modes. Provider names are not
used: a scoped declaration owns structured-output and streaming capabilities.
"""
from __future__ import annotations

import copy
from model_contract import scoped_contract
from model_request_policy import resolve_request


def response_plan(profile, schema, *, model=None, api_key=None, hints=None):
    profile = profile if isinstance(profile, dict) else {}
    legacy = profile.get("capabilities") or {}
    contract = scoped_contract(profile, model, api_key)
    if contract is None and not profile.get("capabilityContract"):
        modes = (["json_schema"] if legacy.get("json_schema_response_format") else
                 [None] if legacy.get("structured_output") is False else ["json_object"])
        streams = [legacy.get("streaming") is not False]
    elif contract is None:
        # A changed endpoint/model must not inherit the previous service's schema.
        modes, streams = [None], [True, False]
    else:
        descriptor = contract.capabilities.get("structured_output")
        modes = []
        if descriptor and descriptor.status in {"supported", "conditional"}:
            declared = list(descriptor.modes or ("json_object",))
            if "json_schema" in declared and descriptor.source in {"metadata", "catalog", "manual", "legacy"}:
                modes.append("json_schema")
            if "json_object" in declared:
                modes.append("json_object")
        # JSON content remains required locally even without server-side format
        # enforcement. This prevents known unsupported response_format errors.
        modes.append(None)
        streams = [True, False]
    last_error = None
    for mode in modes:
        value = None if mode is None else {"type": mode}
        if mode == "json_schema":
            value["json_schema"] = {"name": "confirmed_spec_compact_ladder", "strict": True,
                                    "schema": copy.deepcopy(schema)}
        for streaming in streams:
            try:
                resolve_request(profile, hints, protocol={"stream": streaming, "response_format": value},
                                model=model, api_key=api_key)
            except ValueError as error:
                # This is pure local capability selection, not a paid fallback.
                # Invalid user parameter values still fail every candidate and
                # are never cleared or altered to make a request succeed.
                last_error = error
                continue
            return {"response_format": value}, streaming
    raise last_error or ValueError("No compatible response mode")
