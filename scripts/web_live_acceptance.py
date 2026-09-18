"""Real DeepSeek browser acceptance against a fresh temporary workspace.

The key arrives through stdin, stays in memory, and is never added to config,
arguments, environment or logs. No synthetic model response or execution result
is installed. GX, physical PLC and host MCP registration remain disabled.
"""
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import replace
import argparse
import copy
import getpass
import json
import logging
from pathlib import Path
import re
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import httpx

DEEPSEEK_URL = "https://api.deepseek.com"
MAX_REQUESTS = 12
REQUEST_TIMEOUT = 120.0
_request_kind = ContextVar("live_acceptance_request_kind", default="models")


class AcceptanceLimitError(ValueError):
    pass


class RequestBudget:
    def __init__(self):
        self.count = 0
        self.lock = threading.Lock()

    def reserve(self, kind):
        with self.lock:
            if self.count >= MAX_REQUESTS:
                raise AcceptanceLimitError("真实 API 验收已达到 12 次请求上限，请结束本次验收。")
            self.count += 1
            print(f"LIVE_HTTP_CALL {self.count} {kind}", file=sys.stderr, flush=True)


class BoundedDeepSeekTransport(httpx.BaseTransport):
    """Count actual HTTP attempts, including application-level fallbacks."""
    def __init__(self, budget, *, transport=None):
        self.budget = budget
        self.transport = transport or httpx.HTTPTransport(retries=0, trust_env=False)

    def handle_request(self, request):
        url = request.url
        if (url.scheme != "https" or url.host != "api.deepseek.com" or url.port not in (None, 443)
                or url.path not in ("/models", "/chat/completions") or url.query):
            raise AcceptanceLimitError("真实验收只允许访问固定 DeepSeek API。")
        kind = "models" if url.path == "/models" else _request_kind.get()
        self.budget.reserve(kind)
        # SDK defaults and caller overrides cannot raise this per-operation bound.
        request.extensions["timeout"] = {name: REQUEST_TIMEOUT for name in ("connect", "read", "write", "pool")}
        return self.transport.handle_request(request)

    def close(self):
        self.transport.close()


@contextmanager
def live_settings(directory, api_key, *, model="deepseek-v4-flash"):
    import storage.config as config_manager
    import storage.credentials as credential_store
    import model_runtime.provider as model_provider
    from application.settings import SettingsService
    from openai import OpenAI

    # The SDK may initialize its logger on first import, after main() ran.
    for name in ("httpx", "httpcore", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)
    if not api_key or any(character in api_key for character in "\r\n"):
        raise ValueError("需要通过 stdin 输入一行 API Key。")
    config_path = Path(directory) / "live-config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    target = credential_store.credential_target_for_profile("live-acceptance")
    profile = next((copy.deepcopy(item) for item in config_manager._default_profiles() if item["model"] == model), {
        "adapter": "openai_compatible", "capabilities": {"tools": True}, "generationDefaults": {}, "requestOverrides": {}})
    profile.update(id="live-acceptance", name="DeepSeek 真实 API 验收", baseUrl=DEEPSEEK_URL,
                   model=model, credentialTarget=target)
    config_path.write_text(json.dumps({"language": "zh-CN", "activeModelProfileId": profile["id"],
                                      "modelProfiles": [profile]}, ensure_ascii=False), encoding="utf-8")
    keys = {target: api_key}
    budget, clients = RequestBudget(), []

    def read_key(target=credential_store.CREDENTIAL_TARGET):
        return keys.get(target, "")

    def write_key(value, target=credential_store.CREDENTIAL_TARGET):
        value = str(value or "").strip()
        if not value:
            raise ValueError("API Key 不能为空。")
        keys[target] = value

    def delete_key(target=credential_store.CREDENTIAL_TARGET):
        keys.pop(target, None)

    def create_client(provider):
        if str(provider.profile.get("baseUrl", "")).rstrip("/") != DEEPSEEK_URL:
            raise AcceptanceLimitError("真实验收只允许使用 https://api.deepseek.com。")
        client = OpenAI(api_key=provider.api_key, base_url=DEEPSEEK_URL, timeout=REQUEST_TIMEOUT, max_retries=0,
                        http_client=httpx.Client(transport=BoundedDeepSeekTransport(budget),
                                                 timeout=REQUEST_TIMEOUT, follow_redirects=False, trust_env=False))
        clients.append(client)
        return client

    original_stream = model_provider.OpenAICompatibleProvider.stream

    def real_stream(provider, request):
        kind = str(request.response_contract.name)
        kind = kind if re.fullmatch(r"[a-z_]{1,40}", kind) else "model"
        token = _request_kind.set(kind)
        try:
            for event in original_stream(provider, replace(request, timeout=REQUEST_TIMEOUT, max_retries=0)):
                if isinstance(event, model_provider.Usage):
                    print(f"LIVE_USAGE {kind} input={event.input_tokens} output={event.output_tokens} total={event.total_tokens}",
                          file=sys.stderr, flush=True)
                yield event
        finally:
            _request_kind.reset(token)

    with ExitStack() as stack:
        stack.enter_context(patch.object(config_manager, "get_config_path", lambda: config_path))
        for module in (credential_store, config_manager):
            stack.enter_context(patch.object(module, "read_api_key", read_key))
            stack.enter_context(patch.object(module, "write_api_key", write_key))
        stack.enter_context(patch.object(credential_store, "delete_api_key", delete_key))
        stack.enter_context(patch.object(model_provider.OpenAICompatibleProvider, "_create_client", create_client))
        stack.enter_context(patch.object(model_provider.OpenAICompatibleProvider, "stream", real_stream))
        try:
            # create_provider and test_model_profile remain the real application functions.
            yield SettingsService(), budget
        finally:
            for client in clients:
                client.close()
            keys.clear()


@contextmanager
def live_app(directory, api_key, *, model="deepseek-v4-flash", port=8877):
    from application.hardware import HardwareService
    from application.workbench import WorkbenchService
    from fastapi.responses import JSONResponse
    from integrations.web.app import create_app
    from scripts.web_demo import DemoHardwareReader, DemoIntegrationDisabled, isolated_demo_execution

    root = Path(directory)
    with live_settings(root, api_key, model=model) as (settings, budget), isolated_demo_execution():
        service = WorkbenchService(root / "workspace", root / "state", settings=settings)
        service.hardware = HardwareService(service, reader=DemoHardwareReader())
        origin = f"http://127.0.0.1:{port}"
        app = create_app(service.store.base_dir, state_dir=service.state_dir, service=service, origin=origin,
                         operator_token="live-browser-acceptance", agent_token="live-acceptance-agent")

        @app.exception_handler(DemoIntegrationDisabled)
        async def disabled_host_integration(_request, _error):
            return JSONResponse({"error": {"code": "isolated_live_acceptance", "message":
                "当前只验收真实模型 API；不注册 MCP、不操作 GX 或真实 PLC。"}}, status_code=400)

        try:
            yield app, budget
        finally:
            service.close()


def read_session_key():
    print("LIVE_KEY_STDIN_READY", file=sys.stderr, flush=True)
    if sys.stdin.isatty():
        return getpass.getpass("API Key: ").strip()
    return sys.stdin.readline().strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument("--model", default="deepseek-v4-flash")
    args = parser.parse_args()
    # Avoid SDK debug logging inherited from the host; no request/response bodies.
    for name in ("httpx", "httpcore", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)
    api_key = read_session_key()
    import uvicorn
    with tempfile.TemporaryDirectory(prefix="gx-live-api-qa-") as directory, \
            live_app(directory, api_key, model=args.model, port=args.port) as (app, budget):
        api_key = ""
        print(f"LIVE http://127.0.0.1:{args.port}/#token=live-browser-acceptance", flush=True)
        print(f"LIVE_TARGET {DEEPSEEK_URL} model={args.model} limit={MAX_REQUESTS}", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
        print(f"LIVE_TOTAL_REQUESTS {budget.count}", flush=True)


if __name__ == "__main__":
    main()
