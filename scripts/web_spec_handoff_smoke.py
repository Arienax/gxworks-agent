"""Browser regression for the built Web UI, with a mocked HTTP API.

No model, credentials, GX software, simulator, or PLC is used. The API fixtures
exercise client navigation and submission boundaries, not model correctness.
Install the test-only dependency: pip install playwright==1.55.0
Then: python -m playwright install chromium
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import functools
import json
import threading
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import async_playwright, expect

BROWSER_CHANNEL = None


SPEC = {"summary": "X0 starts Y0; X1 stops Y0", "io_table": [
    {"address": "X0", "kind": "X", "label": "Start"},
    {"address": "X1", "kind": "X", "label": "Stop"},
    {"address": "Y0", "kind": "Y", "label": "Motor"}], "parameters": []}
DEFAULT_TEXT = "请严格按照已确认规格生成候选程序。"
CTA = "按规格生成"
STALE_NOTICE = "规格已变化，请重新分析后再应用草稿。"


def job(job_id, kind, status, result=None):
    return {"id": job_id, "kind": kind, "status": status,
            "project_id": "testproject", "version_id": None,
            "created_at": "2026-09-10T00:00:00Z", "result": result or {}}


class MockAPI:
    def __init__(self, *, confirmed=False, history=True, read_only=False, reject=None):
        self.project = {"id": "testproject", "name": "Handoff test", "plc_model": "FX3U",
                        "target_mode": "ladder", "versions": [], "active_version_id": None,
                        "confirmed_spec": copy.deepcopy(SPEC) if confirmed else None,
                        "confirmed_spec_hash": "saved-spec-hash" if confirmed else None}
        self.other = {**self.project, "id": "otherproject", "name": "Other project",
                      "confirmed_spec": None, "confirmed_spec_hash": None}
        self.jobs = [job("analysis-job", "analysis", "completed")] if history else []
        self.proposals = []
        self.posts = []
        self.saves = []
        self.decisions = []
        self.preview_reads = []
        self.read_only = read_only
        self.reject = reject
        self.errors = []

    async def route(self, route):
        request = route.request
        url = urlsplit(request.url)
        path, method = url.path.removeprefix("/api"), request.method
        body = request.post_data_json if request.post_data else None
        status, data = 200, {}
        if path == "/session":
            data = {"authenticated": True, "read_only": self.read_only,
                    "role": "operator", "csrf": "mock-csrf"}
        elif path == "/settings/approval":
            data = {"mode": "ask", "revision": 0, "local_autosave": True, "read_only": self.read_only}
        elif path == "/settings":
            data = {"active_profile_id": "mock", "profiles": [
                {"id": "mock", "name": "Mock model", "model": "mock", "configured": True}]}
        elif path == "/environment":
            data = {"status": "unverified"}
        elif path == "/capabilities":
            data = {"creation": {"plc_model": "FX3U", "default_target_mode": "ladder", "starter_requirement": "FX3U X0启动Y0输出。",
                                 "target_modes": [{"value": "ladder", "label": "Ladder"}]}, "operations": {}}
        elif path == "/projects":
            data = {"projects": [self.project, self.other]}
        elif path == "/projects/testproject":
            data = self.project
        elif path == "/projects/otherproject":
            data = self.other
        elif path == "/projects/testproject/spec" and method == "PUT":
            self.saves.append(body)
            if self.reject == "conflict":
                status, data = 409, {"error": {"message": "确认规格已变化，请重新加载。"}}
            elif self.reject == "invalid":
                data = {"valid": False, "issues": {"errors": [
                    {"path": "$.summary", "message": "测试：尚有未确认项"}]}}
            else:
                assert body["expected_hash"] == self.project["confirmed_spec_hash"]
                saved = {**body["spec"], "confirmed": True}
                self.project.update(confirmed_spec=saved, confirmed_spec_hash="saved-spec-hash")
                data = {"valid": True, "spec": saved, "hash": "saved-spec-hash"}
        elif path == "/jobs" and method == "POST":
            self.posts.append(body)
            assert not self.read_only
            assert body["kind"] == "generation" and self.project["confirmed_spec"]
            assert body["project_id"] == "testproject" and body["version_id"] is None
            assert body["text"].strip() and body["request_id"]
            data = job("generation-job", "generation", "running")
            self.jobs.insert(0, data)
        elif path == "/jobs":
            pid = parse_qs(url.query).get("project_id", [None])[0]
            data = {"jobs": [j for j in self.jobs if pid in (None, j["project_id"])]}
        elif path.startswith("/jobs/") and path.endswith("/events"):
            current = next(j for j in self.jobs if j["id"] == path.split("/")[2])
            event = {"sequence": 1, "job_id": current["id"], "project_id": current["project_id"],
                     "event_type": current["status"], "payload": {"result": current["result"]}}
            await route.fulfill(status=200, content_type="text/event-stream",
                                body="data: " + json.dumps(event) + "\n\n")
            return
        elif path == "/jobs/analysis-job/output":
            data = {"spec_draft": copy.deepcopy(SPEC), "spec_base_hash": None, "base_version_id": None}
        elif path == "/jobs/generation-job/output":
            data = {"proposal_id": "candidate-1"}
        elif path == "/proposals":
            pid = parse_qs(url.query).get("project_id", [None])[0]
            data = {"proposals": [p for p in self.proposals if pid in (None, p["project_id"])]}
        elif path == "/proposals/candidate-1":
            data = next(p for p in self.proposals if p["id"] == "candidate-1")
        elif path == "/proposals/candidate-1/preview":
            self.preview_reads.append(path)
            data = {"target_mode": "ladder", "program": {"networks": []}, "diff": {},
                    "svg": '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40"><text x="5" y="20">Mock candidate</text></svg>'}
        elif path == "/proposals/candidate-1/explorer":
            data = {"width": 120, "height": 40,
                    "svg": '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40"><text x="5" y="20">Mock candidate</text></svg>',
                    "networks": [], "devices": {}, "address_targets": []}
        elif path.endswith("/decision"):
            self.decisions.append(body)
            status, data = 500, {"error": {"message": "Approval must never be automatic"}}
        else:
            self.errors.append(f"{method} {path}")
            status, data = 404, {"error": {"message": "Unexpected mocked API route: " + path}}
        await route.fulfill(status=status, content_type="application/json", body=json.dumps(data))

    def finish(self):
        self.jobs[0] = job("generation-job", "generation", "completed", {"proposal_id": "candidate-1"})
        self.proposals = [{"id": "candidate-1", "project_id": "testproject", "action": "accept_local",
                           "status": "pending", "base_version_id": None,
                           "summary": {"summary": "Mock candidate pending operator review"}}]


class DirectAPI(MockAPI):
    def __init__(self, clarification=False):
        super().__init__(history=False)
        self.clarification = clarification

    async def route(self, route):
        request = route.request
        path = urlsplit(request.url).path.removeprefix("/api")
        if path == "/jobs" and request.method == "POST":
            body = request.post_data_json
            self.posts.append(body)
            assert body["kind"] == "direct_generation" and not self.project["confirmed_spec"]
            assert body["text"].strip() and body["request_id"]
            identifier = "direct-question" if self.clarification and len(self.posts) == 1 else "direct-success"
            data = job(identifier, "direct_generation", "completed", {"status": "needs_input"} if identifier == "direct-question" else {"version_id": "v0001", "status": "saved"})
            self.jobs.insert(0, data)
        elif path == "/jobs/direct-question/output":
            data = {"status": "needs_input", "clarification_job_id": "direct-question",
                    "missing_info": [{"id": "delay", "question": "延时几秒？", "required": True}],
                    "generation": {"status": "needs_input", "artifacts": {}}}
        elif path == "/jobs/direct-success/output":
            data = {"status": "saved", "version_id": "v0001", "generation": {"target_mode": "ladder"}}
        else:
            return await super().route(route)
        await route.fulfill(status=200, content_type="application/json", body=json.dumps(data))


async def direct_generation(page, model):
    await page.get_by_role("button", name="Agent", exact=True).click()
    await expect(page.get_by_label("任务目的", exact=True)).to_have_value("create")
    await expect(page.get_by_label("创建流程", exact=True)).to_have_value("direct")
    assert await page.get_by_label("任务目的", exact=True).locator("option").all_text_contents() == ["创建程序", "修改程序", "工程问答"]
    await page.locator(".composer textarea").fill("FX3U X0启动，Y0输出；输入电平和行为已明确。")
    await page.locator(".composer-actions button").last.click()
    if model.clarification:
        await expect(page.get_by_text("延时几秒？", exact=True)).to_be_visible()
        await expect(page.get_by_role("button", name="查看程序", exact=True)).to_have_count(0)
        await page.locator(".composer textarea").fill("5秒")
        await page.get_by_role("button", name="补充并继续", exact=True).click()
        assert model.posts[1]["clarification_job_id"] == "direct-question"
        assert model.posts[1]["request_id"] != model.posts[0]["request_id"]
    await expect(page.get_by_role("button", name="查看程序", exact=True)).to_be_visible()
    await expect(page.get_by_role("button", name="重新导出 GX Works2 CSV", exact=True)).to_be_visible()
    assert not model.saves
    await expect(page.locator(".spec-editor")).to_have_count(0)


async def exercise(browser, origin, name, model, action, locale="zh-CN"):
    context = await browser.new_context(viewport={"width": 1600, "height": 1000})
    await context.add_init_script("localStorage.setItem('gx.locale', " + json.dumps(locale) + ");")
    page = await context.new_page()
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    await page.route("**/api/**", model.route)
    try:
        await page.goto(origin + "/?project=testproject")
        await expect(page.locator(".project-title h1")).to_have_text("Handoff test")
        await action(page, model)
        assert not model.errors, model.errors
        assert not page_errors, page_errors
        assert not model.decisions, "A candidate was automatically accepted or executed"
        return {"name": name, "passed": True, "generation_requests": len(model.posts)}
    except Exception:
        await page.screenshot(path="web-spec-handoff-failure.png", full_page=True)
        raise
    finally:
        await context.close()


async def confirm(page):
    await expect(page.locator(".spec-editor")).to_be_visible()
    await page.get_by_role("button", name="确认规格", exact=True).click()


async def baseline(page, model):
    await confirm(page)
    await page.wait_for_timeout(500)
    assert len(model.saves) == 1 and model.project["confirmed_spec"]
    await expect(page.locator(".spec-editor")).to_be_visible()
    await page.get_by_role("button", name="Agent", exact=True).click()
    await expect(page.get_by_label("任务目的", exact=True)).to_have_value("create")
    await expect(page.locator(".composer-actions button").last).to_be_disabled()
    await expect(page.get_by_text(STALE_NOTICE, exact=True)).to_be_visible()
    assert not model.posts


async def handoff(page, model):
    await confirm(page)
    await expect(page.locator(".composer")).to_be_visible()
    await expect(page.get_by_label("任务目的", exact=True)).to_have_value("create")
    await expect(page.locator(".composer textarea")).to_have_count(0)
    await expect(page.get_by_text(STALE_NOTICE, exact=True)).to_have_count(0)
    button = page.get_by_role("button", name=CTA, exact=True)
    await expect(button).to_be_disabled()
    assert len(model.posts) == 1 and model.posts[0]["text"] == DEFAULT_TEXT
    model.finish()
    await expect(page.get_by_text("Mock candidate pending operator review", exact=True)).to_be_visible(timeout=15000)
    assert model.preview_reads and not model.decisions
    assert len(model.posts) == 1


async def keyboard(page, model):
    await expect(page.locator(".composer-actions button").last).to_be_enabled()
    await page.locator(".composer-actions button").last.press("Control+Enter")
    await expect(page.locator(".composer-actions button").last).to_be_disabled()
    assert len(model.posts) == 1 and model.posts[0]["text"] == DEFAULT_TEXT


async def blocked(page, model):
    await expect(page.locator(".composer-actions button").last).to_be_disabled()
    await page.locator(".composer select").first.press("Control+Enter")
    assert not model.posts


async def rejected(page, model):
    await confirm(page)
    message = "确认规格已变化，请重新加载。" if model.reject == "conflict" else "测试：尚有未确认项"
    await expect(page.get_by_text(message, exact=True)).to_be_visible()
    await expect(page.locator(".spec-editor")).to_be_visible()
    assert not model.posts and not model.project["confirmed_spec"]


async def dirty(page, model):
    await page.get_by_role("button", name="规格", exact=True).click()
    await page.locator(".spec-editor textarea").first.fill("Changed and not confirmed")
    await page.get_by_role("button", name="Agent", exact=True).click()
    await expect(page.get_by_role("button", name=CTA, exact=True)).to_be_disabled()
    await expect(page.get_by_text("规格有未确认修改，请先确认后再生成。", exact=True)).to_be_visible()
    assert not model.posts


async def project_switch(page, model):
    await expect(page.get_by_role("button", name=CTA, exact=True)).to_be_enabled()
    await page.locator(".project-item").filter(has_text="Other project").click()
    await expect(page.locator(".project-title h1")).to_have_text("Other project")
    await expect(page.get_by_role("button", name=CTA, exact=True)).to_have_count(0)
    await blocked(page, model)


async def historical(page, model):
    await expect(page.get_by_role("button", name=CTA, exact=True)).to_be_enabled()
    await expect(page.get_by_text("该分析草稿早于当前确认规格，可直接使用当前规格生成。", exact=True)).to_be_visible()
    await expect(page.get_by_text(STALE_NOTICE, exact=True)).to_have_count(0)
    await page.get_by_role("button", name=CTA, exact=True).click()
    await expect(page.get_by_role("button", name=CTA, exact=True)).to_be_disabled()
    assert len(model.posts) == 1


async def localized(page, model):
    button = page.locator(".composer-actions button").last
    await expect(button).to_be_enabled()
    await button.click()
    await expect(button).to_be_disabled()
    assert len(model.posts) == 1
    assert model.posts[0]["response_language"] in ("en", "ja")
    assert model.posts[0]["text"] != DEFAULT_TEXT


async def generation_failure(page, model):
    await page.get_by_role("button", name=CTA, exact=True).click()
    model.jobs[0].update(status="failed", error_code="generation_validation_failed", error_details={
        "stage": "generation_validation", "attempt_count": 3, "max_attempts": 3,
        "response_language": "zh-CN", "contract_name": "ladder", "diagnostic_id": "a" * 16,
        "violation_count": 1, "truncated": False, "stop_reason": "attempt_limit",
        "violations": [{"path": "content$.rungs.36.shared_inputs.3.type", "reason": "invalid_shared_input"}]})
    await expect(page.get_by_text("梯形图候选结构不符合协议，未接受任何程序。", exact=True)).to_be_visible()
    await expect(page.get_by_text("content$.rungs.36.shared_inputs.3.type", exact=True)).to_be_visible()
    await expect(page.get_by_text("公共串联输入中不能包含并联块；请在分支输入中表达并联逻辑。", exact=True)).to_be_visible()
    assert len(model.posts) == 1 and not model.proposals and model.project["confirmed_spec"]


async def generation_timeout(page, model):
    await page.get_by_role("button", name=CTA, exact=True).click()
    model.jobs[0].update(status="failed", error_code="model_timeout", error_details=None)
    await expect(page.get_by_text("模型服务请求超时，请稍后重试。", exact=True)).to_be_visible()
    assert len(model.posts) == 1 and not model.proposals


async def run(origin, baseline_only):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel=BROWSER_CHANNEL)
        try:
            if baseline_only:
                return [await exercise(browser, origin, "baseline: stuck panel, empty-input block, stale draft warning", MockAPI(), baseline)]
            cases = [
                ("Direct default: one submit, program and CSV, no specification", DirectAPI(), direct_generation),
                ("Direct clarification: bound answer with a new request ID", DirectAPI(clarification=True), direct_generation),
                ("generation validation failure keeps exact safe location and confirmed spec", MockAPI(confirmed=True, history=False), generation_failure),
                ("model timeout displays actionable classification", MockAPI(confirmed=True, history=False), generation_timeout),
                ("confirm -> automatic generation -> preview; no duplicate or automatic approval", MockAPI(), handoff),
                ("restored confirmed project: empty-input keyboard generation", MockAPI(confirmed=True, history=False), keyboard),
                ("unconfirmed project cannot generate", MockAPI(history=False), blocked),
                ("read-only project cannot generate", MockAPI(confirmed=True, history=False, read_only=True), blocked),
                ("invalid specification stays editable", MockAPI(reject="invalid"), rejected),
                ("real hash conflict is not bypassed", MockAPI(reject="conflict"), rejected),
                ("unconfirmed edits block generation", MockAPI(confirmed=True, history=False), dirty),
                ("switching projects does not reuse confirmation", MockAPI(confirmed=True, history=False), project_switch),
                ("historical draft does not block confirmed specification", MockAPI(confirmed=True), historical),
            ]
            results = []
            for name, model, action in cases:
                results.append(await exercise(browser, origin, name, model, action))
                print("PASS:", name, flush=True)
            for locale in ("en", "ja"):
                results.append(await exercise(browser, origin, "localized generation: " + locale,
                    MockAPI(confirmed=True, history=False), localized, locale))
            return results
        finally:
            await browser.close()


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    global BROWSER_CHANNEL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-dist", type=Path, default=Path("web/dist"))
    parser.add_argument("--report", type=Path, default=Path("web-spec-handoff.json"))
    parser.add_argument("--baseline", action="store_true", help="Verify the original preview.1 regression before applying the fix")
    parser.add_argument("--browser-channel", default=None, help="Use an installed browser, e.g. msedge")
    args = parser.parse_args()
    BROWSER_CHANNEL = args.browser_channel
    assert (args.web_dist / "index.html").is_file(), "Build the frontend before this test"
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(args.web_dist.resolve())))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = {"ok": False, "api_mocked": True, "model_called": False, "native_gx_tested": False}
    try:
        report["cases"] = asyncio.run(run(f"http://127.0.0.1:{server.server_port}", args.baseline))
        report["ok"] = True
        report["baseline_regression_reproduced"] = args.baseline
    except Exception:
        report["error"] = traceback.format_exc()
        raise
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
