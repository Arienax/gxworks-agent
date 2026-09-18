"""Small callback/language boundary for synchronous application workflows."""
import copy
import json

from shared.i18n import get_language, language_context


class WorkflowError(RuntimeError):
    pass


def _structural_repair_payload(value):
    """Recognize the one whole-rung fallback that is currently justified.

    Semantic contract repair also uses mode=partial, so mode alone must never
    redirect those calls into the structure-only prompt.  A parallel block in
    shared_inputs is explicit representation evidence and does not require
    guessing PLC intent.
    """
    if not isinstance(value, dict):
        return False
    baseline = value.get("baseline_subset")
    if not isinstance(baseline, dict):
        return False
    for rung in baseline.get("rungs", []) or []:
        if not isinstance(rung, dict):
            continue
        for element in rung.get("shared_inputs", []) or []:
            if (
                isinstance(element, dict)
                and str(element.get("type") or "").casefold() == "parallel_block"
            ):
                return True
    return False


def _repair_short_circuit(function, args, kwargs):
    """Keep deterministic fields local and semantic-free structural repair narrow."""
    mode = kwargs.get("mode")
    api_repair = (
        getattr(function, "__module__", "") == "api"
        and getattr(function, "__name__", "") == "repair_ladder_response"
    )
    if mode == "field_patch" and args and isinstance(args[0], dict):
        from application.field_repair import deterministic_response
        response = deterministic_response(args[0])
        if response is not None:
            return "", json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    if mode == "format" and args and isinstance(args[0], dict) and api_repair:
        # The legacy API format mode asks the model to rewrite the complete
        # ladder. Production workflows must never take that path. Deterministic
        # syntax recovery runs first; any remaining ambiguity is a bounded text
        # patch whose output is applied locally to the immutable raw candidate.
        from application.format_patch_repair import format_repair_response
        options = dict(kwargs)
        options.pop("mode", None)
        return format_repair_response(*args, **options)
    if (
        mode == "partial"
        and args
        and _structural_repair_payload(args[0])
        and api_repair
    ):
        from application.repair_policy import structural_repair_response
        return structural_repair_response(*args, **kwargs)
    return None


def model_call(function, *args, **kwargs):
    """Guard model invocations while deterministic repair stays model-free."""
    from model_runtime.provider import ResponseRejectedError, public_model_error
    from application.compact_protocol import CompactProtocolError
    from plc.validation import PLCJsonValidationError
    try:
        local = _repair_short_circuit(function, args, kwargs)
        if local is not None:
            return local
        return function(*args, **kwargs)
    except (ResponseRejectedError, CompactProtocolError, PLCJsonValidationError):
        raise
    except Exception as error:
        safe_error = public_model_error(error)
        raise WorkflowError(str(safe_error)) from safe_error


class Workflow:
    def __init__(self, *, on_event=None, response_language=None, provider=None, model_name=None):
        self.on_event = on_event
        self.response_language = response_language or get_language()
        self.provider = provider
        self.model_name = model_name

    def _emit(self, event_type, payload):
        if self.on_event:
            if not isinstance(payload, dict):
                key = "message" if event_type == "progress" else "text"
                payload = {key: str(payload)}
            self.on_event(event_type, copy.deepcopy(payload))

    def run(self):
        import application.model_api as api

        with language_context(self.response_language), api.provider_scope(self.provider, model_name=self.model_name):
            return self._run()
