from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from integrations.dsh_runtime import DshRuntime, find_dsh_bin
from model_runtime.usage import add_token_counts


@pytest.mark.parametrize('report_zero', [True, False])
def test_provider_usage_survives_a_dsh_tool_turn(report_zero, tmp_path):
    if not find_dsh_bin():
        pytest.skip('DeepSeek Harness is unavailable')

    frames = []
    model_calls = []
    tool_name = 'read_usage_fixture'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send_body(self, data, content_type, status=200):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, value):
            self.send_body(json.dumps(value).encode(), 'application/json')

        def do_GET(self):
            assert self.path == '/tools'
            self.send_json({'tools': [{'type': 'function', 'function': {'name': tool_name,
                'description': 'Read a local fixture', 'parameters': {'type': 'object', 'properties': {}}}}]})

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path == '/event':
                frames.extend(data['frames'] if 'frames' in data else [data['frame']])
                self.send_json({'ok': True})
                return
            if self.path == '/invoke':
                assert data['name'] == tool_name
                self.send_json({'value': 42})
                return
            assert self.path == '/v1/chat/completions'
            model_calls.append(data)
            if report_zero and len(model_calls) == 2:
                self.send_body(b'{"error":{"message":"Temporary fixture error"}}', 'application/json', 503)
                return
            tool_finished = any(message['role'] == 'tool' for message in data['messages'])
            if tool_finished:
                delta = {'content': 'Fixture value is 42.'}
                finish = 'stop'
                usage = {'prompt_tokens': 30, 'completion_tokens': 4, 'total_tokens': 34,
                         'prompt_cache_hit_tokens': 12, 'prompt_cache_miss_tokens': 18}
            else:
                delta = {'tool_calls': [{'index': 0, 'id': 'call_usage_fixture', 'type': 'function',
                    'function': {'name': tool_name, 'arguments': '{}'}}]}
                finish = 'tool_calls'
                usage = {'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25}
                if report_zero:
                    usage['prompt_tokens_details'] = {'cached_tokens': 0}
            common = {'id': 'chatcmpl-usage', 'object': 'chat.completion.chunk', 'created': 1, 'model': data['model']}
            chunks = [
                {**common, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]},
                {**common, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                {**common, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]},
                {**common, 'choices': [], 'usage': usage},
            ]
            body = ''.join(f'data: {json.dumps(chunk)}\r\n\r\n' for chunk in chunks) + 'data: [DONE]\r\n\r\n'
            self.send_body(body.encode(), 'text/event-stream')

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        runtime = DshRuntime({'base_url': url + '/v1', 'model': 'usage-fixture', 'api_key': 'fixture-secret',
                              'reasoning_effort': 'high'}, url, 'bridge-secret', tmp_path / 'home', tmp_path)
        try:
            result = runtime.run('Read the fixture value.', session_id='gxworks-usage-fixture')
        finally:
            runtime.close()
            server.shutdown()
            thread.join()

    assert result.final_response == 'Fixture value is 42.'
    assert len(model_calls) == (3 if report_zero else 2)
    requests = [frame for frame in frames if frame['type'] == 'request']
    usage_frames = [frame for frame in frames if frame['type'] == 'chunk' and frame['chunk']['type'] == 'usage']
    assert len(requests) == len(model_calls)
    assert [frame['attemptId'] for frame in (requests[0], requests[-1])] == [frame['attemptId'] for frame in usage_frames]
    for frame in requests:
        assert {key: value for key, value in frame.items() if key != 'attemptId'} == {
            'type': 'request', 'model': 'usage-fixture', 'api': 'openai-completions', 'tools': True,
            'streaming': True, 'structured_output': False, 'images': False, 'reasoning_effort': 'high',
        }

    first, second = [frame['chunk']['usage'] for frame in usage_frames]
    assert first['inputTokens'] == 20 and first['outputTokens'] == 5
    assert ('cacheReadTokens' in first) is report_zero
    if report_zero:
        assert first['cacheReadTokens'] == 0
    assert second == {'inputTokens': 18, 'outputTokens': 4, 'cacheReadTokens': 12}

    total = {}
    for usage in (first, second):
        cache = {'cached_input_tokens': usage['cacheReadTokens']} if 'cacheReadTokens' in usage else {}
        total = add_token_counts(total, {'input_tokens': usage['inputTokens'] + cache.get('cached_input_tokens', 0),
                                         'output_tokens': usage['outputTokens'], **cache})
    assert total == {'input_tokens': 50, 'output_tokens': 9,
                     **({'cached_input_tokens': 12} if report_zero else {})}
