"""Operator cancellation must truncate model streaming, not become a model error."""

import pytest

from application.jobs import JobCancelled
from model_provider import (
    ModelRequest,
    TextDelta,
    UserMessage,
    collect_response,
    response_policy_scope,
)


class StreamingProvider:
    def __init__(self):
        self.closed = False
        self.reached_second_chunk = False

    def stream(self, request):
        try:
            yield TextDelta('{"partial":"first')
            self.reached_second_chunk = True
            yield TextDelta(' second"}')
        finally:
            self.closed = True


def test_operator_cancel_escapes_collector_and_truncates_stream():
    provider = StreamingProvider()
    request = ModelRequest((UserMessage("generate"),), stream=True)

    def cancel_on_first_content(progress):
        if progress.phase == "receiving":
            raise JobCancelled()

    with response_policy_scope(on_progress=cancel_on_first_content):
        with pytest.raises(JobCancelled):
            collect_response(provider, request)

    assert provider.closed is True
    assert provider.reached_second_chunk is False


def test_job_cancel_signal_is_control_flow_not_an_ordinary_model_exception():
    # Provider/collector layers intentionally catch Exception.  Cancellation has
    # to cross those boundaries untouched so JobManager can record "cancelled"
    # rather than a model_provider_error/job_failed result.
    assert issubclass(JobCancelled, BaseException)
    assert not issubclass(JobCancelled, Exception)
