"""Explicit two-stage discovery; regular requests never probe or scan models.

The registry contains bounded strategies, not a capability/model allowlist.
Quick mode checks one scalar sample per parameter; deep mode extends those
samples without repeating already observed capability tests. Metadata wins.
"""
from __future__ import annotations

import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace

from model_contract import (
    MISSING, CapabilityContract, CapabilityDescriptor, ConstraintDescriptor,
    ParameterDescriptor, contract_scope, metadata_contract_parts, identifier,
    scoped_contract, validate_parameter_paths, member,
)
from model_request_policy import parameter_value, merge
from model_probes import PROBE_REGISTRY, ProbeContext
from model_provider import ModelProviderError

# These bound scheduling, not the remote server's latency. SDK I/O timeouts are
# phase/inactivity timeouts, not a guaranteed wall-clock completion deadline.
QUICK_BUDGET_SECONDS = 20.0
DEEP_BUDGET_SECONDS = 90.0
QUICK_REQUEST_SECONDS = 5.0
DEEP_REQUEST_SECONDS = 15.0


class _Budget:
    def __init__(self, seconds, request_seconds):
        self.started = time.monotonic()
        self.deadline = self.started + seconds
        self.request_seconds = request_seconds
        self.stopped = threading.Event()

    def remaining(self):
        if self.stopped.is_set():
            return 0.0
        return max(0.0, min(self.request_seconds, self.deadline - time.monotonic()))


def _configured_values(profile, parameters):
    options = merge(profile.get("generationDefaults") or {}, profile.get("requestOverrides") or {})
    selections = (profile.get("userModelSettings") or {}).get("parameters", {})
    result = {}
    for name, descriptor in parameters.items():
        value = parameter_value(options, name, descriptor)
        selection = selections.get(name, {})
        if selection.get("mode") == "value":
            value = selection.get("value")
        elif selection.get("mode") == "omit":
            value = None
        if value is not MISSING and value is not None:
            try:
                validator = replace(descriptor, values=None) if descriptor.scan == "partial" else descriptor
                validator.validate(value)
                result[name] = value
            except ValueError:
                pass
    return result


def inspect_openai_compatible(provider, model, configured_capabilities=None, *,
                              probes=None, mode="quick", refresh=False):
    if mode not in {"list", "quick", "deep"} or not isinstance(refresh, bool):
        raise ValueError("Invalid discovery mode")
    original = copy.deepcopy(getattr(provider, "profile", {}) or {})
    key = getattr(provider, "api_key", "")
    selected = str(model or "").strip()
    registry = PROBE_REGISTRY if probes is None else probes
    seconds = DEEP_BUDGET_SECONDS if mode == "deep" else QUICK_BUDGET_SECONDS
    budget = _Budget(seconds, DEEP_REQUEST_SECONDS if mode == "deep" else QUICK_REQUEST_SECONDS)
    remaining = budget.remaining

    listing_available, metadata = True, []
    try:
        if callable(getattr(provider, "list_model_metadata", None)):
            metadata = list(provider.list_model_metadata(timeout=remaining()))
            models = sorted({str(item["id"]) for item in metadata if item.get("id")})
        else:
            models = sorted(set(provider.list_models(timeout=remaining())))
    except ModelProviderError as error:
        if error.code in {"authentication", "rate_limit"}:
            raise
        listing_available, models = False, []
    except Exception:
        listing_available, models = False, []

    # An endpoint's model listing is never a ranking or model recommendation.
    if mode == "list" or not selected or selected == "__discover__":
        if not models:
            raise ModelProviderError("模型列表不可用，请手动填写模型 ID。", code="invalid_request")
        return {
            "models": models, "recommended_model": None,
            "selected_model_available": selected in models,
            "model_listing_available": listing_available,
            "mode": "list", "elapsed_ms": round((time.monotonic() - budget.started) * 1000),
            "note": f"已获取 {len(models)} 个模型。请选择模型后点击快速能力检测；不会自动选择或调用任何模型。",
        }

    entry = next((item for item in metadata if item.get("id") == selected), {})
    parameters, capabilities, constraints = metadata_contract_parts(entry)
    metadata_parameters = set(parameters)
    try:
        cached = scoped_contract(original, selected, api_key=key)
    except ValueError:
        cached = None
    # Cached contracts are usable only with the exact endpoint/model/context/key.
    # A refresh ignores parameter samples; deep scans retain capability evidence
    # because deep scanning does not run capability tests at all.
    if cached:
        for name, descriptor in cached.parameters.items():
            if name not in parameters and (not refresh or name not in registry):
                try:
                    validate_parameter_paths({**parameters, name: descriptor})
                    parameters[name] = descriptor
                except ValueError:
                    pass
        if not refresh or mode == "deep":
            for name, descriptor in cached.capabilities.items():
                capabilities.setdefault(name, descriptor)
        for name, condition in cached.constraints.items():
            if name not in metadata_parameters and (name in parameters or name in capabilities):
                constraints.setdefault(name, condition)

    parameter_strategies = [s for s in registry.values() if s.kind == "parameter"]
    # Neutral descriptors allow reading an explicit draft value before it has
    # been sampled. This is a probe registry, never a whitelist for metadata.
    for strategy in parameter_strategies:
        parameters.setdefault(strategy.name, ParameterDescriptor.from_dict(strategy.name,
            {"type": strategy.type, "status": "unknown", "source": "probe", "scan": "partial"}))
    validate_parameter_paths(parameters)
    configured = _configured_values(original, parameters)
    pending, reused = [], []
    for strategy in parameter_strategies:
        desc = parameters[strategy.name]
        expected = strategy.requirements(ProbeContext(provider, selected, remaining, configured, parameters))
        # Sampling parameters are not in the scope fingerprint, so their
        # dependent evidence needs a separate condition comparison.
        same_condition = desc.constraints.to_dict().get("requires", {}) == expected
        if desc.source == "probe" and not same_condition:
            desc = ParameterDescriptor.from_dict(strategy.name,
                {"type": strategy.type, "status": "unknown", "source": "probe", "scan": "partial"})
            parameters[strategy.name] = desc
        declared = strategy.name in metadata_parameters or desc.source in {"metadata", "manual"}
        # Scope excludes tuning values. Reusing a partial domain is safe only
        # if it already covers the explicit draft sample; otherwise quick mode
        # must validate that sample rather than silently retaining an old one.
        requested = configured.get(strategy.name, MISSING)
        new_sample = (mode == "quick" and desc.scan == "partial" and
            requested is not MISSING and not member(requested, desc.values or ()))
        reusable = same_condition and not new_sample and (desc.scan == "complete" or
            mode == "quick" and desc.status in {"supported", "accepted", "unsupported", "fixed", "conditional"})
        if declared or reusable:
            reused.append(strategy.name)
        else:
            pending.append(strategy)
    capability_strategies = [s for s in registry.values() if s.kind == "capability"]
    cap_pending = []
    for strategy in capability_strategies:
        desc = capabilities.get(strategy.name)
        if mode == "quick" and (desc is None or desc.status == "unknown" and desc.source == "probe"):
            cap_pending.append(strategy)
        elif desc is None:
            capabilities[strategy.name] = CapabilityDescriptor("unknown", "probe")

    if callable(getattr(provider, "for_detection", None)):
        provider = provider.for_detection(parameters=parameters)
    # Negotiate the output-limit key once before parallel work, avoiding races
    # on a legacy max_tokens transport. A failed preflight stops this attempt.
    preflight_failed = False
    if (pending or cap_pending) and remaining() > 0 and callable(getattr(provider, "probe_parameter", None)):
        try:
            if provider.probe_parameter(selected, timeout=remaining()) != "accepted":
                preflight_failed = True
                budget.stopped.set()
        except ModelProviderError as error:
            if error.code in {"authentication", "rate_limit"}:
                raise
            preflight_failed = True
            budget.stopped.set()

    def parameter_lane():
        local = dict(parameters)
        previous = dict(parameters)
        for strategy in pending:
            context = ProbeContext(provider, selected, remaining,
                _configured_values(original, local), local, mode, previous)
            # A freshly sampled upstream setting can invalidate an older
            # dependent; never carry its validated values to a different mode.
            if previous[strategy.name].constraints.to_dict().get("requires", {}) != strategy.requirements(context):
                context.previous = {k: v for k, v in previous.items() if k != strategy.name}
            local[strategy.name] = strategy.run(context)
        return {s.name: local[s.name] for s in pending}

    # Only independent capabilities and the ordered parameter lane overlap.
    # Temperature stays behind reasoning; adding a registry entry needs no
    # provider/model-name dispatch. Join in-flight work before returning so no
    # worker silently keeps spending tokens after the result is delivered.
    tasks = {}
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="model-discovery") as pool:
        if pending:
            tasks[pool.submit(parameter_lane)] = None
        for strategy in cap_pending:
            context = ProbeContext(provider, selected, remaining, configured, dict(parameters), mode)
            tasks[pool.submit(strategy.run, context)] = strategy.name
        try:
            for future in as_completed(tasks):
                name = tasks[future]
                result = future.result()
                if name is None:
                    parameters.update(result)
                else:
                    capabilities[name] = result
        except BaseException:
            budget.stopped.set()
            for future in tasks:
                future.cancel()
            raise

    for name, value in (configured_capabilities or {}).items():
        try:
            identifier(name)
            if name not in capabilities and isinstance(value, bool):
                capabilities[name] = CapabilityDescriptor("supported" if value else "unsupported", "legacy")
        except ValueError:
            continue
    refs = set(parameters) | set(capabilities) | {"stream", "tools", "response_format"}
    for group in (parameters, capabilities):
        for name, descriptor in list(group.items()):
            condition = descriptor.constraints
            if (set(condition.requires) | set(condition.conflicts_with)) - refs:
                group[name] = replace(descriptor, status="unknown", constraints=ConstraintDescriptor())
    constraints = {name: c for name, c in constraints.items()
        if not (set(c.requires) | set(c.conflicts_with)) - refs}
    contract = CapabilityContract(contract_scope(original, parameters, selected, key), capabilities, parameters, constraints)
    contract = CapabilityContract.from_dict(contract.to_dict())
    incomplete = [name for name, d in parameters.items() if d.source == "probe" and d.scan != "complete"]
    incomplete_caps = [name for name, d in capabilities.items() if d.status == "unknown"]
    partial = bool(incomplete or mode == "quick" and incomplete_caps)
    return {
        "models": models, "recommended_model": selected,
        "selected_model_available": selected in models,
        "model_listing_available": listing_available, "contract": contract.to_dict(),
        "mode": mode, "elapsed_ms": round((time.monotonic() - budget.started) * 1000),
        "budget_seconds": seconds, "budget_exhausted": time.monotonic() >= budget.deadline,
        "preflight_failed": preflight_failed,
        "partial": partial, "parameters_pending": incomplete, "parameters_reused": reused,
        "note": ("基础聊天验证未完成，本次未继续发送能力探测请求。" if preflight_failed else
                 "快速检测完成；仅验证少量参数取值，深度参数扫描可补充档位。" if mode == "quick" else
                 "深度参数扫描已结束；保留已验证取值，未完成项仍标记为部分扫描。" if partial else
                 "深度参数扫描完成；已检查注册策略的候选档位，不代表穷尽服务的全部取值。") +
                "未知不等于不支持；超时不会判为不支持。正常调用不自动探测。",
    }
