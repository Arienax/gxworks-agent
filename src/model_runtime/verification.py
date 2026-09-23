"""One explicitly authorized synthetic request. No scans, retries or fallback."""
from __future__ import annotations

import copy
import json
import math
from dataclasses import replace

from model_runtime.contract import CapabilityContract, UserModelSettings, path_remove
from model_runtime.runtime_profile import materialize_runtime_profile
from model_runtime.provider import (
    ModelRequest, UserMessage, TextDelta, ReasoningDelta, ToolCallEnd, Usage,
)
from model_runtime.observations import request_observer
from model_runtime.probes import VERIFIERS


_LIMIT_NAMES = {"max_tokens", "max_completion_tokens"}
_PROBE_STRIP_NAMES = _LIMIT_NAMES | {"timeout", "max_retries", "n"}


def _strip_probe_controls(options, contract):
    """Remove persisted fields that could widen or shadow the bounded probe."""
    result = copy.deepcopy(dict(options or {}))
    for name in _PROBE_STRIP_NAMES:
        path_remove(result, (name,))
        path_remove(result, ("extra_body", name))
    for name, descriptor in contract.parameters.items():
        if name in _PROBE_STRIP_NAMES or descriptor.wire_path[-1] in _PROBE_STRIP_NAMES:
            descriptor.remove(result, name)
    return result


def _probe_output_limit(contract, target, kind, value):
    if kind == "parameter" and (
        target in _LIMIT_NAMES
        or contract.parameters[target].wire_path[-1] in _LIMIT_NAMES
    ):
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not 1 <= value <= 64
        ):
            raise ValueError(
                "Single verification permits only an output budget from 1 to 64; "
                "use higher limits in normal requests"
            )
        return int(value)

    limit = contract.parameters.get("max_completion_tokens")
    output_limit = 64
    if limit is None:
        return output_limit
    if limit.values:
        allowed = [
            item
            for item in limit.values
            if not isinstance(item, bool)
            and isinstance(item, (int, float))
            and 1 <= item <= 64
        ]
        if not allowed:
            raise ValueError(
                "Declared output domain cannot fit a 64-token verification budget"
            )
        output_limit = max(allowed)
    else:
        if limit.maximum is not None:
            output_limit = min(output_limit, math.floor(limit.maximum))
        if limit.exclusive_maximum is not None:
            output_limit = min(
                output_limit, math.ceil(limit.exclusive_maximum) - 1
            )
        if limit.step:
            anchor = 0 if limit.zero_anchored else limit.minimum or 0
            output_limit = (
                anchor
                + math.floor((output_limit - anchor) / limit.step) * limit.step
            )
    limit.validate(output_limit)
    if not 1 <= output_limit <= 64:
        raise ValueError(
            "Declared output domain cannot fit a 64-token verification budget"
        )
    return int(output_limit)


def _probe_runtime(provider, contract, target, kind, value):
    """Build one temporary RuntimeModelProfile; never clear the contract."""
    profile = copy.deepcopy(provider.profile)
    profile["capabilityContract"] = contract.to_dict()

    previous = provider.profile.get("userModelSettings") or {}
    if previous.get("scope") == contract.scope:
        profile["userModelSettings"] = copy.deepcopy(previous)
    else:
        profile["userModelSettings"] = {
            "scope": dict(contract.scope),
            "parameters": {},
        }

    runtime = materialize_runtime_profile(
        profile,
        api_key=provider.api_key,
        model=profile["model"],
    )
    selections = copy.deepcopy(dict(runtime.settings.parameters))

    if kind == "parameter":
        if target not in contract.parameters:
            raise ValueError("Unknown parameter")
        contract.parameters[target].validate(value)
        selections[target] = {"mode": "value", "value": copy.deepcopy(value)}

    if (
        kind == "parameter"
        and (target == "n" or contract.parameters[target].wire_path[-1] == "n")
        and value != 1
    ):
        raise ValueError("Single verification cannot generate multiple choices")

    output_limit = _probe_output_limit(contract, target, kind, value)
    limit = contract.parameters.get("max_completion_tokens")
    request_options = {}
    if limit is not None:
        if limit.status in {"unsupported", "fixed"}:
            raise ValueError(
                "Declared output-limit control cannot provide a bounded verification request"
            )
        selections["max_completion_tokens"] = {
            "mode": "value",
            "value": output_limit,
        }
    else:
        request_options["max_completion_tokens"] = output_limit

    settings = UserModelSettings.from_dict(
        {"scope": dict(contract.scope), "parameters": selections},
        contract,
    )
    runtime = replace(
        runtime,
        contract=contract,
        settings=settings,
        defaults=_strip_probe_controls(runtime.defaults, contract),
        overrides=_strip_probe_controls(runtime.overrides, contract),
    )
    return profile, runtime, request_options


def verify_one(
    provider,
    contract,
    target,
    *,
    kind="parameter",
    value=None,
    consent=False,
    store=None,
):
    if consent is not True:
        raise ValueError("Verification requires explicit paid-request consent")
    if kind not in {"parameter", "capability", "chat"} or (
        kind == "chat" and target != "chat"
    ):
        raise ValueError("Unknown verification kind")

    contract = CapabilityContract.from_dict(contract)
    if kind == "parameter" and target not in contract.parameters:
        raise ValueError("Unknown parameter")
    if kind == "capability" and target not in VERIFIERS:
        raise ValueError("No single-request verifier for this capability")

    tools = ()
    prompt = "Reply with OK."
    request_options = {}
    if kind == "capability":
        spec = VERIFIERS[target]
        if target == "structured_output" and value not in {"json_object", None}:
            raise ValueError(
                "Only json_object verification is implemented; "
                "no JSON-schema guarantee"
            )
        prompt = spec.prompt
        tools = copy.deepcopy(spec.tools)
        if spec.response_format is not None:
            request_options["response_format"] = copy.deepcopy(
                spec.response_format
            )

    profile, runtime, budget_options = _probe_runtime(
        provider, contract, target, kind, value
    )
    request_options.update(budget_options)

    # One synthetic request now follows the same runtime contract path as every
    # normal completion. There is no pre-resolve and no cleared-contract pass.
    clone = copy.copy(provider)
    clone.profile = profile
    clone._runtime_profiles = {runtime.model: runtime}

    owner = copy.copy(provider)
    owner.profile = profile
    owner._runtime_profiles = {runtime.model: runtime}
    clone.observation_sink = (
        request_observer(owner, store, source="probe") if store else None
    )

    request = ModelRequest(
        (UserMessage(prompt),),
        model=runtime.model,
        tools=tools,
        options=request_options,
        stream=True,
        timeout=8.0,
        max_retries=0,
        enforce_response_language=False,
        _synthetic_probe=True,
    )
    events = list(clone.stream(request))

    ok = any(
        isinstance(event, (TextDelta, ReasoningDelta, ToolCallEnd))
        for event in events
    )
    if kind == "capability" and target == "tools":
        calls = [
            event.tool_call
            for event in events
            if isinstance(event, ToolCallEnd)
        ]
        try:
            ok = (
                len(calls) == 1
                and calls[0].name == "capability_probe"
                and (
                    json.loads(calls[0].arguments)
                    if isinstance(calls[0].arguments, str)
                    else calls[0].arguments
                )
                == {"value": "ok"}
            )
        except (ValueError, TypeError):
            ok = False
    elif kind == "capability":
        try:
            ok = json.loads(
                "".join(
                    event.text
                    for event in events
                    if isinstance(event, TextDelta)
                )
            ) == {"probe": True}
        except (ValueError, TypeError):
            ok = False

    usage = next(
        (event for event in reversed(events) if isinstance(event, Usage)),
        None,
    )
    return {
        "outcome": (
            "observed"
            if ok and kind != "parameter"
            else "accepted"
            if ok
            else "inconclusive"
        ),
        "generation_requests": 1,
        "input_tokens": usage.input_tokens if usage else None,
        "output_tokens": usage.output_tokens if usage else None,
        "note": (
            "单次验证已完成；接口接受不代表参数一定生效，"
            "JSON 样例不代表严格 schema 保证。"
        ),
    }
