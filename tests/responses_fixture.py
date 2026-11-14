"""OpenAI-compatible payloads for the local SDK transport tests."""


def chat_call(call_id, name, arguments):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def chat_body(text="", *, reasoning="", calls=(), usage=None):
    message = {"role": "assistant", "content": text or None}
    if reasoning:
        message["reasoning_content"] = reasoning
    if calls:
        message["tool_calls"] = list(calls)
    return {"object": "chat.completion", "id": "chat_fixture", "created": 1, "model": "fixture",
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": usage}


def chat_chunk(delta=None, *, finish_reason=None, usage=None):
    return {"object": "chat.completion.chunk", "id": "chat_fixture", "created": 1, "model": "fixture",
            "choices": [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            "usage": usage}


def chat_stream(body, *deltas):
    choice = body["choices"][0]
    chunks = [chat_chunk(delta) for delta in deltas or (choice["message"],)]
    chunks.append(chat_chunk({}, finish_reason=choice["finish_reason"]))
    if body["usage"] is not None:
        chunks.append(chat_chunk(usage=body["usage"]))
    return iter(chunks)


def function_item(call_id, name, arguments):
    return {"type": "function_call", "id": f"fc_{call_id}", "call_id": call_id,
            "name": name, "arguments": arguments, "status": "completed"}


def response_body(text="", *, summary="", encrypted=None, calls=(), usage=None, status="completed"):
    output = []
    if summary or encrypted:
        item = {"type": "reasoning", "id": "rs_fixture", "summary": (
            [{"type": "summary_text", "text": summary}] if summary else [])}
        if encrypted:
            item["encrypted_content"] = encrypted
        output.append(item)
    if text:
        output.append({"type": "message", "role": "assistant", "status": "completed", "id": "msg_fixture",
                       "content": [{"type": "output_text", "text": text, "annotations": []}]})
    output.extend(calls)
    return {"object": "response", "id": "resp_fixture", "created_at": 1,
            "model": "fixture", "status": status, "output": output, "usage": usage}


def stream_body(response, *events):
    return iter([*events, {"type": "response." + response["status"], "response": response}])
