"""Small callback/language boundary for synchronous application workflows."""
import copy
import json

from i18n import get_language, language_context


class WorkflowError(RuntimeError):
    pass


def _repair_short_circuit(function, args, kwargs):
    """Keep deterministic fields local and semantic-free structural repair narrow."""
    mode = kwargs.get("mode")
    if mode == "field_patch" and args and isinstance(args[0], dict):
        from application.field_repair import deterministic_response
        response = deterministic_response(args[0])
        if response is not None:
            return "", json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    if (
        mode == "partial"
        and getattr(function, "__module__", "") == "api"
        and getattr(function, "__name__", "") == "repair_ladder_response"
    ):
        from application.repair_policy import structural_repair_response
        return structural_repair_response(*args, **kwargs)
    return None


def model_call(function, *args, **kwargs):
    """Guard model invocations while deterministic repair stays model-free."""
    local = _repair_short_circuit(function, args, kwargs)
    if local is not None:
        return local
    from model_provider import ResponseRejectedError, public_model_error
    try:
        return function(*args, **kwargs)
    except ResponseRejectedError:
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
        import api

        with language_context(self.response_language), api.provider_scope(self.provider, model_name=self.model_name):
            return self._run()
