"""Web stream projection preserves content without per-token job-file reads."""
from types import SimpleNamespace
from threading import RLock

from application.dsh_jobs import ToolBridge
from application.model_progress import ModelJobContext, ModelProgressReporter


def bridge_context():
    events, checks = [], []
    context = SimpleNamespace(checkpoint=lambda: checks.append(True),
                              emit=lambda kind, data: events.append((kind, data)))
    bridge = object.__new__(ToolBridge)
    bridge.ctx = ModelJobContext(context)
    bridge.snapshot = {'model': {'profile_id': 'local', 'profile_name': '123', 'model': 'local-model'}}
    bridge.progress = ModelProgressReporter(bridge.ctx, clock=lambda: 0)
    bridge._events_lock = RLock()
    bridge._usage_seen = set()
    bridge.request_count = 0
    return bridge, events, checks


def test_stream_batch_preserves_text_metering_and_boundaries_with_bounded_job_reads():
    bridge, events, checks = bridge_context()
    deltas = [
        {'type': 'chunk', 'attemptId': 'first', 'chunk': {'type': kind, 'delta': str(index) + ','}}
        for kind in ('reasoning-delta', 'text-delta') for index in range(1000)
    ]
    original = [dict(frame['chunk']) for frame in deltas]
    usage = {'type': 'chunk', 'attemptId': 'first', 'chunk': {'type': 'usage',
             'usage': {'inputTokens': 13, 'outputTokens': 5, 'cacheReadTokens': 0}}}
    bridge.events([{'type': 'start'}, *deltas, usage, {'type': 'end'},
                   {'type': 'start'}, {'type': 'chunk', 'attemptId': 'second',
                    'chunk': {'type': 'text-delta', 'text': 'second response'}}, {'type': 'end'}])
    expected = ''.join(str(index) + ',' for index in range(1000))
    assert [data['text'] for kind, data in events if kind == 'reasoning'] == [expected]
    assert [data['text'] for kind, data in events if kind == 'content'] == [expected, 'second response']
    assert [data for kind, data in events if kind == 'model_token_usage'] == [
        {'input_tokens': 13, 'output_tokens': 5, 'cached_input_tokens': 0}]
    assert [data['request_number'] for kind, data in events if kind == 'model_progress'] == [1, 2]
    assert len(checks) < 30  # 2,000 transport chunks must not mean 4,000 full job reads.
    assert [frame['chunk'] for frame in deltas] == original


def test_request_receipt_reports_only_observed_safe_features():
    bridge, events, _ = bridge_context()
    bridge.events([{'type': 'request', 'model': 'local-model', 'tools': True,
                   'streaming': True, 'structured_output': False, 'images': False,
                   'reasoning_effort': 'high', 'headers': {'Authorization': 'secret'},
                   'messages': ['private prompt']}])
    kind, receipt = events[0]
    assert kind == 'model_request'
    assert receipt['model'] == 'local-model'
    assert receipt['features'] == {'tools': True, 'streaming': True, 'structured_output': False, 'images': False}
    assert receipt['parameters'][0]['value'] == 'high'
    assert 'secret' not in str(receipt) and 'private prompt' not in str(receipt)
