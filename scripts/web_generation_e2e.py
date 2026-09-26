"""Real HTTP + browser generation delivery acceptance in disposable workspaces.

No HTTP API or candidate result is mocked. The default replaces ONLY the model
transport with fixed replies. --live uses DeepSeek's official endpoint and a
key read from DEEPSEEK_API_KEY or a hidden prompt; nothing saves that key. The
live mode performs one simple analysis/generation/preview/acceptance exercise,
with at most six provider requests and no GX/simulator/real PLC operations.

Requires requirements-web.txt and playwright; build web/dist first.
"""
from __future__ import annotations
import argparse
import asyncio
import copy
from collections import Counter
from dataclasses import replace
import getpass
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get('GX_TEST_BACKEND_SRC', str(ROOT / 'src')))
import uvicorn
from playwright.async_api import async_playwright, expect
from application.workbench import WorkbenchService
from integrations.web.app import create_app
from model_runtime.provider import TextDelta, Usage, ModelProviderError, OpenAICompatibleProvider


LADER = {'device_comments': {'X0': '输入', 'Y0': '输出'}, 'rungs': [
    {'rung_id': 1, 'header_element': None, 'shared_inputs': [], 'branches': [
        {'branch_id': 1, 'y_offset_level': 0, 'inputs': [{'type': 'NO', 'address': 'X0', 'label': '输入'}],
         'outputs': [{'type': 'COIL', 'address': 'Y0', 'label': '输出'}]}]}]}
ANALYSIS = {'summary': 'X0 接通时 Y0 接通，X0 断开时 Y0 断开。', 'approaches': [],
    'missing_info': [], 'suggested_io': {'X': {'X0': '输入'}, 'Y': {'Y0': '输出'}}, 'assumptions': []}
REQUIREMENT = 'FX3U，普通梯形图。只用常开输入 X0 直接控制输出 Y0；X0=1 时 Y0=1，X0=0 时 Y0=0。不需要自锁、定时、计数或其他功能。'


class Provider:
    def __init__(self, live=None):
        self.live, self.calls, self.usage = live, 0, []
        self.profile = copy.deepcopy(live.profile) if live else {
            "adapter": "openai_compatible", "baseUrl": "https://offline.invalid/v1",
            "model": "deterministic-delivery-fixture",
        }
        self.api_key = live.api_key if live else None
    def stream(self, request):
        self.calls += 1
        if self.calls > 6:
            raise ModelProviderError('Test request budget reached', code='protocol')
        if self.live:
            for event in self.live.stream(replace(request, timeout=120, max_retries=0)):
                if isinstance(event, Usage):
                    self.usage.append({'input_tokens': event.input_tokens, 'output_tokens': event.output_tokens})
                yield event
        else:
            raw = json.dumps(ANALYSIS if request.response_contract.name == 'analysis' else LADER, ensure_ascii=False)
            # Exercise actual streamed text acceptance and persistent job events.
            for start in range(0, len(raw), 70):
                yield TextDelta(raw[start:start + 70])


class IsolatedSettings:
    def __init__(self, model):
        self.model = model
    def public_settings(self):
        return {'language': 'zh-CN', 'active_profile_id': 'test-only', 'profiles': [
            {'id': 'test-only', 'name': 'Isolated test provider', 'model': self.model, 'configured': True}]}


class Server:
    def __init__(self, root, web_dist, provider, *, fault=None):
        self.provider, self.fault = provider, fault
        self.socket = socket.socket()
        self.socket.bind(('127.0.0.1', 0))
        self.origin = 'http://127.0.0.1:' + str(self.socket.getsockname()[1])
        self.token = secrets.token_urlsafe(32)
        model = 'deepseek-v4-flash' if provider.live else 'deterministic-delivery-fixture'
        self.service = WorkbenchService(root / 'workspace', root / 'state', settings=IsolatedSettings(model),
            model_factory=lambda: (provider, provider.profile))
        self.app = create_app(self.service.store.base_dir, state_dir=self.service.state_dir,
            service=self.service, operator_token=self.token, origin=self.origin, static_dir=web_dist)
        self.preview_attempts = 0
        self.api_errors = []
        self.executions = []
        self.read_counts, self.inflight, self.max_inflight = Counter(), Counter(), Counter()
        self.delays = {}
        self.hold_next_jobs = False
        self.jobs_snapshot_ready = threading.Event()
        self.release_jobs_snapshot = threading.Event()
        @self.app.middleware('http')
        async def observe(request, call_next):
            # Fault injection delays genuine backend requests or fails one read;
            # it never fabricates a job, output, proposal or preview response.
            path = request.url.path
            if path.startswith('/api/projects/') and '/versions/' in path and path.endswith('/preview'):
                self.preview_attempts += 1
                if fault == 'preview-once' and self.preview_attempts == 1:
                    from fastapi.responses import JSONResponse
                    return JSONResponse({'error': {'message': 'Transient preview read failure'}}, status_code=503)
            if fault == 'delayed-reads' and request.method == 'GET' and (
                path == '/api/proposals' or (path.startswith('/api/projects/') and path.count('/') == 3)):
                await asyncio.sleep(0.45)
            if fault == 'no-sse' and path.endswith('/events'):
                from fastapi.responses import Response
                return Response(status_code=204)
            if request.method == 'POST' and path == '/api/proposals':
                self.executions.append(path)
            counted = request.method == 'GET' and path.startswith('/api/') and not path.endswith('/events')
            if counted:
                self.read_counts[path] += 1
                self.inflight[path] += 1
                self.max_inflight[path] = max(self.max_inflight[path], self.inflight[path])
            hold = path == '/api/jobs' and self.hold_next_jobs
            if hold: self.hold_next_jobs = False
            try:
                response = await call_next(request)
                if hold:
                    # Freeze genuine old bytes, not a fabricated API response.
                    from fastapi.responses import Response
                    body = b''.join([part async for part in response.body_iterator])
                    self.jobs_snapshot_ready.set()
                    for _ in range(400):
                        if self.release_jobs_snapshot.is_set(): break
                        await asyncio.sleep(0.05)
                    response = Response(body, status_code=response.status_code, headers=dict(response.headers))
                if path in self.delays: await asyncio.sleep(self.delays[path])
                if path.startswith('/api/') and response.status_code >= 500:
                    self.api_errors.append((request.method, path, response.status_code))
                return response
            finally:
                if counted: self.inflight[path] -= 1
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level='error', access_log=False, timeout_graceful_shutdown=5))
        self.thread = threading.Thread(target=lambda: self.server.run(sockets=[self.socket]), daemon=True)
    def __enter__(self):
        self.thread.start()
        for _ in range(200):
            if self.server.started: return self
            if not self.thread.is_alive(): raise RuntimeError('HTTP backend did not start')
            time.sleep(0.05)
        raise RuntimeError('HTTP startup timed out')
    def __exit__(self, exc_type, exc_value, exc_tb):
        # Bound orphaned HTTP connections in the disposable fault-injection server.
        # Service shutdown still joins workers and releases its real workspace lock.
        self.release_jobs_snapshot.set()
        self.server.should_exit = True
        self.thread.join(timeout=30)
        self.socket.close()
        if self.thread.is_alive():
            self.server.force_exit = True
            if exc_value is not None:
                exc_value.add_note('Disposable HTTP server also failed to stop')
                return False
            raise RuntimeError('Test backend failed to stop')
    def project(self, name, *, legacy_contract=False):
        project = self.service.create_project(name=name, plc_model='FX3U', target_mode='ladder')
        if legacy_contract:
            # A legacy confirmed approach may carry an old hard-looking contract,
            # but confirmed specification is generation context, not a second
            # post-hoc semantic gate after the model returns a valid PLC program.
            spec = {'summary': REQUIREMENT, 'io_table': [], 'parameters': [],
                'selected_approach': {'name': 'MOV approach', 'generation_contract': {'required_opcodes': ['MOV'], 'enforce': True}}}
            self.service.store.set_confirmed_spec(project['id'], spec)
        return project['id']


async def open_page(browser, server, pid):
    context = await browser.new_context(viewport={'width': 1600, 'height': 1050})
    await context.add_init_script("localStorage.setItem('gx.locale','zh-CN');")
    page = await context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    expected_name = server.service.projects.project(pid)['name']
    try:
        async with page.expect_response(lambda r: r.request.method == 'GET' and
                r.url == server.origin + '/api/projects/' + pid, timeout=30000) as loaded:
            await page.goto(server.origin + '/?project=' + pid + '#token=' + server.token)
        response = await loaded.value
        assert response.status == 200, 'Project HTTP load failed: ' + str(response.status)
        assert (await response.json())['id'] == pid
        await expect(page.locator('.project-title h1')).to_have_text(expected_name, timeout=30000)
        return context, page, errors
    except Exception:
        evidence = Path(os.environ['GX_DELIVERY_EVIDENCE_DIR'])
        evidence.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(evidence / 'generation-delivery-failure.png'), full_page=True)
        await context.close()
        raise


async def visible_svg(page):
    # Keep this locator live while React mounts the current Program Explorer;
    # do not take an instantaneous count and freeze onto an obsolete fallback.
    image = page.locator('.program-explorer .explorer-drawing img, .canvas-shell img, img.ladder-svg').first
    await expect(image).to_be_visible(timeout=20000)
    await page.wait_for_function("() => { const i=document.querySelector('.program-explorer .explorer-drawing img, .canvas-shell img, img.ladder-svg'); return i && i.complete && i.naturalWidth>0 && i.naturalHeight>0; }", timeout=20000)


async def wait_job(server, pid):
    for _ in range(3000):
        rows = server.service.jobs.list(pid)
        if rows and rows[0]['kind'] == 'generation' and rows[0]['status'] not in ('queued','running','cancelling'):
            assert rows[0]['status'] == 'completed', rows[0].get('error_code')
            return rows[0]
        await asyncio.sleep(0.1)
    raise RuntimeError('Generation did not complete in test budget')


async def run_case(browser, root, web_dist, name, *, legacy_contract=False, fault=None, live=None):
    provider = Provider(live)
    with Server(root, web_dist, provider, fault=fault) as server:
        pid = server.project(name, legacy_contract=legacy_contract)
        context, page, page_errors = await open_page(browser, server, pid)
        try:
            if not legacy_contract:
                await page.locator('.composer textarea').fill(REQUIREMENT)
                await page.get_by_role('button', name='发送', exact=True).click()
                await expect(page.locator('.spec-editor')).to_be_visible(timeout=180000 if live else 30000)
                await page.get_by_role('button', name='确认规格', exact=True).click()
                await expect(page.locator('.composer')).to_be_visible()
                await expect(page.get_by_role('button', name='按已确认规格生成程序', exact=True)).to_be_disabled()
            else:
                await page.get_by_role('button', name='按已确认规格生成程序', exact=True).click()
            job = await wait_job(server, pid)
            output = server.service.output(job['id'])
            project_state = server.service.projects.project(pid)
            assert project_state['version_count'] == 1
            assert output.get('status') == 'saved'
            assert output.get('proposal_id'), 'Generation finished without a proposal'
            assert output.get('version_id'), 'Generation finished without a saved version'
            assert output.get('status') != 'contract_mismatch'
            if legacy_contract:
                contract = project_state['confirmed_spec']['selected_approach']['generation_contract']
                assert contract['required_opcodes'] == ['MOV'] and contract['enforce'] is True
            proposal = server.service.proposals.get(output['proposal_id'])
            assert proposal['status'] == 'accepted' and output['version_id'] == proposal['result']['version_id']
            if fault == 'preview-once':
                await expect(page.get_by_text('Transient preview read failure', exact=True)).to_be_visible(timeout=20000)
                await page.get_by_role('button', name='查看程序', exact=True).click()
            await visible_svg(page)
            calls_before = provider.calls
            await page.get_by_role('button', name='刷新结果 / 重绘梯形图', exact=True).click()
            await expect(page.get_by_text('预览已刷新，未调用模型或修改程序。', exact=True)).to_be_visible()
            await visible_svg(page)
            assert provider.calls == calls_before
            assert server.service.projects.project(pid)['version_count'] == 1
            # Refresh formerly cleared the preview and permanently marked it shown.
            await page.get_by_role('button', name='刷新结果 / 重绘梯形图', exact=True).click()
            await expect(page.get_by_text('正在读取工程', exact=True)).to_have_count(0, timeout=20000)
            await visible_svg(page)
            await page.reload()
            await visible_svg(page)
            assert server.service.projects.project(pid)['version_count'] == 1
            assert await page.get_by_role('button', name='接受为本地版本', exact=True).count() == 0
            assert server.service.projects.project(pid)['version_count'] == 1
            await expect(page.locator('.preview-banner')).to_have_count(0)
            await visible_svg(page)
            await page.reload()
            await visible_svg(page)
            project = server.service.projects.project(pid)
            vid = project['active_version_id']
            # Exercise the visible export menu with real file bytes.
            await page.locator('.export-menu summary').click()
            async with page.expect_download() as download_info:
                await page.locator('.export-menu').get_by_role('link').filter(has=page.get_by_text('程序 CSV', exact=True)).click()
            download = await download_info.value
            assert download.suggested_filename.endswith('.csv')
            path = await download.path()
            assert Path(path).read_bytes() == server.service.projects.artifact(pid, vid, 'program_csv').read_bytes()
            for width in (1920, 1366, 1024):
                await page.set_viewport_size({'width': width, 'height': 950})
                await expect(page.locator('.project-toolbar')).to_be_visible()
                assert await page.locator('.project-toolbar').evaluate('el => el.scrollWidth <= el.clientWidth + 1')
                assert await page.locator('.project-toolbar').get_by_role('button', name='刷新结果 / 重绘梯形图').count() == 1
            if fault is None and not legacy_contract:
                evidence = Path(os.environ['GX_DELIVERY_EVIDENCE_DIR'])
                await page.set_viewport_size({'width': 1600, 'height': 1000})
                await page.screenshot(path=str(evidence / 'autosaved-toolbar.png'), full_page=True)
                await page.locator('.approval-mode-indicator').click()
                await expect(page.get_by_role('radio', name='替我审批', exact=False)).to_be_visible()
                await page.get_by_role('radio', name='替我审批', exact=False).check()
                await page.get_by_role('button', name='保存审批模式', exact=True).click()
                await expect(page.get_by_text('审批模式已保存，仅影响后续请求。', exact=True)).to_be_visible()
                assert server.service.approval.read()['mode'] == 'auto'
                await page.get_by_role('radio', name='完全访问', exact=False).check()
                await expect(page.get_by_role('button', name='保存审批模式', exact=True)).to_be_disabled()
                await page.get_by_role('checkbox', name='我允许工作台自动执行已支持的 GX、仿真和调试操作。', exact=True).check()
                await page.get_by_role('button', name='保存审批模式', exact=True).click()
                await expect(page.locator('.approval-mode-indicator')).to_have_text('完全访问')
                assert server.service.approval.read()['mode'] == 'full'
                await page.screenshot(path=str(evidence / 'approval-settings.png'), full_page=True)
                await page.get_by_role('radio', name='逐项审批', exact=False).check()
                await page.get_by_role('button', name='保存审批模式', exact=True).click()
                await expect(page.locator('.approval-mode-indicator')).to_have_text('逐项审批')
                await page.keyboard.press('Escape')
            artifacts = project['versions'][0]['artifacts']
            assert {'ir','json','svg','program_csv','st_from_ir'} <= {a['id'] for a in artifacts if a['available']}
            assert server.service.proposals.get(output['proposal_id'])['status'] == 'accepted'
            # Delete just the derived SVG in the disposable fixture. The new
            # button must recover pixels from canonical IR, without changing
            # the saved version or invoking a second generation.
            saved_svg = server.service.projects.artifact(pid, vid, 'svg')
            saved_svg.unlink()
            await page.reload()
            await expect(page.locator('.preview-banner')).to_have_count(0)
            await expect(page.get_by_role('button', name='刷新结果 / 重绘梯形图', exact=True)).to_be_enabled()
            before = {str(p): p.read_bytes() for p in server.service.store.base_dir.rglob('*') if p.is_file()}
            await page.get_by_role('button', name='刷新结果 / 重绘梯形图', exact=True).click()
            await expect(page.get_by_text('预览已刷新，未调用模型或修改程序。', exact=True)).to_be_visible()
            await visible_svg(page)
            assert before == {str(p): p.read_bytes() for p in server.service.store.base_dir.rglob('*') if p.is_file()}
            assert not saved_svg.exists() and server.service.projects.project(pid)['version_count'] == 1
            expected_calls = 1 if legacy_contract else 2
            assert provider.calls == expected_calls or live
            assert not page_errors, page_errors
            assert not server.executions, 'Test attempted a native GX operation'
            assert not server.api_errors, server.api_errors
            return {'name': name, 'passed': True, 'model_requests': provider.calls,
                    'usage': provider.usage, 'legacy_contract': legacy_contract, 'real_http': True,
                    'svg_decoded': True, 'manual_redraw_without_model_call': True,
                    'model_mocked': live is None}
        except Exception:
            # Browser does not display the API key. Never dump private backend state.
            evidence = Path(os.environ.get('GX_DELIVERY_EVIDENCE_DIR', str(root.parent)))
            evidence.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(evidence / 'generation-delivery-failure.png'), full_page=True)
            raise
        finally:
            await context.close()


async def until(predicate, message, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(): return
        await asyncio.sleep(0.05)
    raise AssertionError(message)


async def lifecycle_cases(browser, root, web_dist, results):
    """Production UI and real persistence. No GX/PLC calls or model spend."""
    from application.simulation_workbench import SimulationWorkbenchService
    provider = Provider()
    with Server(root, web_dist, provider, fault='no-sse') as server:
        pid = server.project('Lifecycle regression', legacy_contract=True)
        server.service.submit({'kind': 'generation', 'project_id': pid, 'version_id': None,
            'request_id': secrets.token_hex(16), 'text': REQUIREMENT,
            'response_language': 'zh-CN', 'attachment_ids': []})
        saved = await wait_job(server, pid)
        vid = server.service.output(saved['id'])['version_id']
        version_path = f'/api/projects/{pid}/versions/{vid}'
        server.service.create_project(name='Deferred FBD scope', plc_model='FX3U', target_mode='fbd')
        simulation = SimulationWorkbenchService(server.service)
        simulation.save(pid, vid, suite={'name': 'Lifecycle plan', 'plc_model': 'FX3U', 'tests': [{
            'name': 'input follows output', 'plc_model': 'FX3U', 'initial': {'X0': 0},
            'steps': [{'id': 'on', 'at_ms': 0, 'set': {'X0': 1}},
                      {'id': 'assert', 'at_ms': 10, 'expect': {'Y0': 1}}],
            'sample_ms': 5, 'timeout_ms': 100}]}, requirement_links={}, issue_ids=[],
            expected_ir_sha256=simulation.read(pid, vid)['ir_sha256'])

        async def tab(page, name):
            await page.locator('.editor-tabs').get_by_role('button', name=name, exact=True).click()

        async def preserve_tabs(page):
            explorer = page.locator('.program-explorer')
            await explorer.get_by_label('搜索地址或注释', exact=True).fill('X0')
            await explorer.get_by_role('button', name='+', exact=True).click()
            await explorer.evaluate('el => window.__retainedExplorer=el')
            before = server.read_counts[version_path + '/explorer']
            before_program = server.read_counts[version_path + '/program']
            for _ in range(20):
                await tab(page, 'ST')
                await tab(page, '梯形图')
            await expect(explorer.get_by_label('搜索地址或注释', exact=True)).to_have_value('X0')
            await expect(explorer.get_by_role('button', name='125%', exact=True)).to_be_visible()
            assert await explorer.evaluate('el => el===window.__retainedExplorer'), 'Tab switching remounted explorer'
            assert server.read_counts[version_path + '/explorer'] == before, 'Tab clicks refetched unchanged drawings'
            assert server.read_counts[version_path + '/program'] == before_program, 'Tab clicks refetched immutable IR'
            await tab(page, '仿真记录')
            await page.get_by_label('方案名称', exact=True).fill('Unsaved lifecycle draft')
            await tab(page, '梯形图'); await tab(page, '仿真记录')
            await expect(page.get_by_label('方案名称', exact=True)).to_have_value('Unsaved lifecycle draft')
            return {'tab_clicks': 42, 'extra_explorer_requests': server.read_counts[version_path + '/explorer'] - before}

        async def redraw_retains_dom(page):
            explorer = page.locator('.program-explorer')
            await explorer.get_by_label('搜索地址或注释', exact=True).fill('Y0')
            await explorer.evaluate('el => {window.__retainedExplorer=el;window.__retainedImage=el.querySelector("img");window.__detached=false;window.__watch=new MutationObserver(()=>{if(!window.__retainedImage.isConnected)window.__detached=true});window.__watch.observe(document.body,{subtree:true,childList:true})}')
            server.delays[version_path + '/explorer'] = 0.45
            before = server.read_counts[version_path + '/explorer']
            await page.get_by_role('button', name='刷新结果 / 重绘梯形图', exact=True).click()
            await until(lambda: server.read_counts[version_path + '/explorer'] > before and not server.inflight[version_path + '/explorer'], 'Redraw did not complete')
            await visible_svg(page)
            assert await explorer.evaluate('el => el===window.__retainedExplorer && el.querySelector("img")===window.__retainedImage && !window.__detached'), 'Refreshing detached the rendered drawing'
            await expect(explorer.get_by_label('搜索地址或注释', exact=True)).to_have_value('Y0')
            await page.evaluate('window.__watch.disconnect()')
            return {'drawing_detached': False}

        async def save_plan_retains_editor(page):
            await tab(page, '仿真记录')
            field = page.get_by_label('方案名称', exact=True)
            await field.fill('Saved lifecycle plan')
            await field.evaluate('el => {window.__planField=el;window.__planDetached=false;window.__watch=new MutationObserver(()=>{if(!el.isConnected)window.__planDetached=true});window.__watch.observe(document.body,{subtree:true,childList:true})}')
            before = {path: server.read_counts[path] for path in ['/api/settings', '/api/environment', '/api/projects', version_path + '/program']}
            count = len(simulation.read(pid, vid)['plans'])
            button = page.get_by_role('button', name='保存为新方案', exact=True)
            # Same-turn clicks exercise the synchronous lock, not only disabled styling.
            await button.evaluate('el => {for(let i=0;i<20;i++)el.click()}')
            await until(lambda: len(simulation.read(pid, vid)['plans']) == count + 1, 'Plan was not saved once')
            await expect(page.locator('.sim-notice')).to_contain_text('已保存新方案')
            await asyncio.sleep(0.7)
            assert len(simulation.read(pid, vid)['plans']) == count + 1, 'Rapid save duplicated plans'
            assert await field.evaluate('el => el===window.__planField && !window.__planDetached'), 'Save remounted the plan editor'
            await expect(field).to_have_value('Saved lifecycle plan')
            assert all(server.read_counts[path] == total for path, total in before.items()), 'Plan save refetched unrelated global resources'
            await page.evaluate('window.__watch.disconnect()')
            return {'save_clicks': 20, 'plans_created': 1, 'unrelated_requests': 0}

        async def completion_without_sse(page):
            await tab(page, '诊断')
            issues_path = version_path + '/issues'
            await until(lambda: server.read_counts[issues_path] > 0 and not server.inflight[issues_path], 'Initial issues missing')
            before = server.read_counts[issues_path]
            jobs_before = {j['id'] for j in server.service.jobs.list(pid)}
            await page.get_by_role('button', name='本地检查', exact=True).click()
            await until(lambda: any(j['id'] not in jobs_before and j['kind'] == 'review' and j['status'] == 'completed' for j in server.service.jobs.list(pid)), 'Review did not complete')
            await until(lambda: server.read_counts[issues_path] > before, 'Polling completion did not refresh mounted diagnosis')
            assert await page.locator('.editor-tabs .active').inner_text() == '诊断'
            return {'sse_disabled': True, 'diagnosis_refreshed_without_click': True}

        async def stale_poll_and_slow_reads(page):
            await tab(page, '诊断')
            server.jobs_snapshot_ready.clear(); server.release_jobs_snapshot.clear()
            server.hold_next_jobs = True
            await until(server.jobs_snapshot_ready.is_set, 'No poll was available to hold')
            # This exceeds the old 2.5 s interval: a second overlapping request is a defect.
            current = server.read_counts['/api/jobs']
            await asyncio.sleep(2.8)
            assert server.read_counts['/api/jobs'] == current, 'A slow job poll overlapped the previous request'
            async with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/jobs')) as response:
                await page.get_by_role('button', name='本地检查', exact=True).click()
            job = await (await response.value).json()
            selector = page.get_by_label('任务记录', exact=True)
            await expect(selector).to_have_value(job['id'])
            await selector.evaluate('''(el) => {window.__regressed=false;const expected=el.value;window.__watch=new MutationObserver(()=>{if(el.value!==expected)window.__regressed=true});window.__watch.observe(el,{subtree:true,childList:true,attributes:true})}''')
            server.release_jobs_snapshot.set()
            await asyncio.sleep(1)
            await expect(selector).to_have_value(job['id'])
            assert not await page.evaluate('window.__regressed'), 'An old snapshot erased the newly submitted task'
            await page.evaluate('window.__watch.disconnect()')
            return {'slow_poll_overlap': False, 'stale_snapshot_applied': False}

        def feature_asset(feature):
            assets = list((web_dist / 'assets').glob(f'{feature}-*.js'))
            assert len(assets) == 1, f'Expected one deferred {feature} entry, found {assets}'
            return assets[0].name

        async def loaded_assets(page):
            return await page.evaluate("performance.getEntriesByType('resource').map(e => new URL(e.name).pathname.split('/').pop())")

        async def optional_panels_load_on_demand(page):
            features = ['Settings', 'SimulationWorkbench', 'FBDPanel', 'HardwarePanel']
            assets = {name: feature_asset(name) for name in features}
            initial = await loaded_assets(page)
            assert not set(assets.values()) & set(initial), 'Optional code was fetched on the initial view'
            await page.get_by_role('button', name='设置', exact=True).click()
            assert assets['Settings'] not in await loaded_assets(page), 'General settings loaded the model editor'
            await page.get_by_role('button', name='模型', exact=True).click()
            await page.get_by_role('button', name='新增配置', exact=True).click()
            field = page.get_by_label('配置名称', exact=True)
            await field.fill('Unsaved deferred settings')
            await field.evaluate('el => window.__settingsField=el')
            for _ in range(5):
                await page.get_by_role('button', name='通用与审批', exact=True).click()
                await page.get_by_role('button', name='模型', exact=True).click()
            await expect(field).to_have_value('Unsaved deferred settings')
            assert await field.evaluate('el => el===window.__settingsField'), 'Settings tabs remounted the draft'
            await page.get_by_role('dialog').get_by_role('button', name='关闭', exact=True).click()
            await tab(page, '工程交付摘要')
            assert assets['HardwarePanel'] not in await loaded_assets(page), 'Collapsed hardware loaded code'
            await page.get_by_text('高级维护：真实 PLC 只读接入', exact=True).click()
            hardware = page.locator('.hardware-panel')
            await expect(hardware).to_be_visible()
            hardware_path = version_path + '/hardware'
            await until(lambda: server.read_counts[hardware_path] and not server.inflight[hardware_path], 'Hardware status missing')
            before = server.read_counts[hardware_path]
            await hardware.evaluate('el => window.__hardwarePanel=el')
            await tab(page, '梯形图'); await tab(page, '工程交付摘要')
            assert await hardware.evaluate('el => el===window.__hardwarePanel'), 'Hardware main-tab switch remounted the panel'
            assert server.read_counts[hardware_path] == before, 'Hardware main-tab switch refetched unchanged status'
            # The import dialog shares FBD code but must not preload it.
            assert assets['FBDPanel'] not in await loaded_assets(page)
            await page.locator('.toolbar-menu').filter(has_text='导入 GXW').locator('summary').click()
            await page.get_by_role('button', name='导入 GXW', exact=True).click()
            await expect(page.get_by_label('选择 GXW 工程', exact=True)).to_be_visible()
            await page.get_by_role('dialog').get_by_role('button', name='关闭', exact=True).click()
            fetched = await loaded_assets(page)
            assert all(fetched.count(assets[name]) == 1 for name in ['Settings', 'HardwarePanel', 'FBDPanel'])
            assert assets['SimulationWorkbench'] not in fetched, 'Unvisited simulation fetched code'
            return {'initial_optional_js_requests': 0, 'settings_draft_retained': True,
                    'hardware_dom_retained': True, 'fbd_import_loaded_on_demand': True}

        async def delayed_chunk_keeps_visibility_and_draft(page):
            asset = feature_asset('SimulationWorkbench')
            started, release = asyncio.Event(), asyncio.Event()
            async def delay(route):
                started.set()
                await release.wait()
                await route.continue_()
            await page.route('**/assets/' + asset, delay)
            explorer = page.locator('.program-explorer')
            await explorer.get_by_label('搜索地址或注释', exact=True).fill('X0')
            await explorer.get_by_role('button', name='+', exact=True).click()
            await explorer.evaluate('el => window.__coldExplorer=el')
            path = version_path + '/simulation-workbench'
            before = server.read_counts[path]
            try:
                await tab(page, '仿真记录')
                await asyncio.wait_for(started.wait(), 10)
                await expect(page.locator('.editor-content').get_by_role('status')).to_have_text('正在读取…')
                # A suspended view cannot blank or disable the rest of the shell.
                await tab(page, '梯形图')
                await expect(explorer.get_by_role('button', name='125%', exact=True)).to_be_visible()
                assert await explorer.evaluate('el => el===window.__coldExplorer')
                release.set()
                workbench = page.locator('.simulation-workbench')
                await workbench.wait_for(state='attached')
                assert not await workbench.is_visible()
                await asyncio.sleep(0.3)
                assert server.read_counts[path] == before, 'A chunk resolving while hidden started a data read'
                await tab(page, '仿真记录')
                field = page.get_by_label('方案名称', exact=True)
                await field.fill('Unsaved late-loaded draft')
                await field.evaluate('el => window.__lateDraft=el')
                await until(lambda: not server.inflight[path], 'Simulation read did not finish')
                before = server.read_counts[path]
                await tab(page, '梯形图')
                await page.get_by_role('button', name='刷新结果 / 重绘梯形图', exact=True).click()
                await expect(page.get_by_role('button', name='刷新结果 / 重绘梯形图', exact=True)).to_be_enabled()
                await asyncio.sleep(0.3)
                assert server.read_counts[path] == before, 'Hidden workbench fetched after result invalidation'
                await tab(page, '仿真记录')
                await until(lambda: server.read_counts[path] > before and not server.inflight[path], 'Visible workbench did not revalidate')
                await expect(field).to_have_value('Unsaved late-loaded draft')
                assert await field.evaluate('el => el===window.__lateDraft'), 'Revalidation remounted the draft'
                assert (await loaded_assets(page)).count(asset) == 1, 'Repeated tab visits downloaded code again'
                return {'late_hidden_reads': 0, 'hidden_refresh_reads': 0, 'draft_dom_retained': True}
            finally:
                release.set()
                await page.unroute('**/assets/' + asset, delay)

        async def fbd_draft_and_document_scope(page):
            await page.locator('.project-item').filter(has_text='Deferred FBD scope').click()
            panel = page.locator('.fbd-panel')
            await expect(panel.get_by_role('button', name='+', exact=True)).to_be_visible()
            await panel.get_by_role('button', name='+', exact=True).click()
            await panel.locator('.fbd-sections').get_by_role('button', name='对象', exact=True).click()
            await expect(panel.get_by_role('button', name='添加对象', exact=True)).to_be_enabled()
            await panel.get_by_role('button', name='添加对象', exact=True).click()
            await expect(panel.get_by_role('button', name='撤销草稿', exact=True)).to_be_visible()
            await expect(panel.locator('.fbd-sheet tbody tr')).to_have_count(1)
            await panel.evaluate('el => window.__fbdPanel=el')
            before = server.read_counts['/api/fbd/catalog']
            await tab(page, 'ST'); await tab(page, 'FBD')
            assert await panel.evaluate('el => el===window.__fbdPanel'), 'FBD tab switch remounted the editor'
            await expect(panel.locator('.fbd-sheet tbody tr')).to_have_count(1)
            await panel.locator('.fbd-sections').get_by_role('button', name='图形', exact=True).click()
            await expect(panel.get_by_role('button', name='125%', exact=True)).to_be_visible()
            assert server.read_counts['/api/fbd/catalog'] == before
            # Retention is document-scoped, never a cross-project singleton.
            await page.locator('.project-item').filter(has_text='Lifecycle regression').click()
            await tab(page, '梯形图')
            await visible_svg(page)
            assert not await page.evaluate('window.__fbdPanel.isConnected')
            await page.locator('.project-item').filter(has_text='Deferred FBD scope').click()
            await expect(panel.get_by_role('button', name='100%', exact=True)).to_be_visible()
            assert await panel.evaluate('el => el!==window.__fbdPanel')
            await expect(panel.get_by_role('button', name='撤销草稿', exact=True)).to_have_count(0)
            return {'fbd_draft_retained': True, 'fbd_zoom_retained': True, 'old_document_disposed': True}

        async def hidden_replay_pauses(page):
            from simulator import InMemoryTestBackend, SimulatorRegressionService
            # Persist observed test-backend reads through the real service. This
            # is explicitly not a PLC emulator or a native simulation execution.
            backend = InMemoryTestBackend(on_write=lambda state, _values: state.values.update(Y0=state.values.get('X0', 0)))
            run = SimulatorRegressionService(server.service.store, backend=backend).run_version_suite(pid, vid, {
                'name': 'Deferred replay fixture', 'plc_model': 'FX3U', 'tests': [{
                    'name': 'recorded input', 'plc_model': 'FX3U', 'initial': {'X0': 0},
                    'steps': [{'at_ms': 0, 'set': {'X0': 1}}, {'at_ms': 200, 'expect': {'Y0': 1}}],
                    'sample_ms': 5, 'timeout_ms': 500}]})
            await tab(page, '仿真记录')
            await page.get_by_role('tab', name='波形回放', exact=True).click()
            await page.get_by_role('combobox', name='本版本运行记录', exact=True).select_option(run['record']['run_id'])
            cursor = page.get_by_label('回放采样位置', exact=True)
            await expect(cursor).to_be_visible()
            await cursor.evaluate('el => window.__replayCursor=el')
            await page.locator('.sim-replay').get_by_role('button', name='播放', exact=True).click()
            await expect(cursor).not_to_have_value('0')
            await tab(page, '梯形图')
            await asyncio.sleep(0.1)
            paused = await cursor.input_value()
            await asyncio.sleep(0.65)
            assert await cursor.input_value() == paused, 'Hidden replay advanced its playback timer'
            assert await cursor.evaluate('el => el===window.__replayCursor && el.isConnected')
            await tab(page, '仿真记录')
            await expect(cursor).not_to_have_value(paused)
            assert int(await cursor.input_value()) > int(paused), 'Resuming replay reset its cursor'
            await page.locator('.sim-replay').get_by_role('button', name='暂停', exact=True).click()
            return {'hidden_playback_paused': True, 'cursor_retained': True,
                    'backend_kind': 'test_memory_not_plc_simulator'}

        checks = [('tab-state-and-request-budget', preserve_tabs), ('redraw-keeps-dom', redraw_retains_dom),
                  ('plan-save-is-local-and-single-flight', save_plan_retains_editor),
                  ('diagnosis-autorefresh-without-SSE', completion_without_sse),
                  ('slow-poll-and-stale-response', stale_poll_and_slow_reads),
                  ('optional-panels-load-on-demand', optional_panels_load_on_demand),
                  ('late-chunk-visibility-and-draft', delayed_chunk_keeps_visibility_and_draft),
                  ('fbd-draft-and-document-scope', fbd_draft_and_document_scope),
                  ('hidden-replay-pauses', hidden_replay_pauses)]
        for name, check in checks:
            print('START lifecycle:', name, flush=True)
            context, page, errors = await open_page(browser, server, pid)
            try:
                await visible_svg(page)
                await expect(page.get_by_role('button', name='刷新结果 / 重绘梯形图', exact=True)).to_be_enabled()
                await asyncio.sleep(0.2)
                metrics = await check(page)
                assert not errors, errors
                results.append({'name': name, 'passed': True, 'real_http': True, **metrics})
                print('PASS lifecycle:', name, flush=True)
            except Exception:
                results.append({'name': name, 'passed': False, 'error': traceback.format_exc()})
                await page.screenshot(path=str(Path(os.environ['GX_DELIVERY_EVIDENCE_DIR']) / f'{name}-failure.png'), full_page=True)
                print('FAIL lifecycle:', name, flush=True)
            finally:
                server.release_jobs_snapshot.set(); server.delays.clear()
                await context.close()
        assert not server.executions, 'Lifecycle acceptance must not execute native operations'
        assert provider.calls == 1, 'UI interactions must not generate extra model requests'
        assert all(result['passed'] for result in results), 'Lifecycle acceptance failed; see per-case evidence'
    return results


async def exercise(args, root, live, results=None):
    # Real HTTP calls include persistence and polling; mocked UI timing does not
    # apply. All state, bytes, permissions and pixel assertions remain required.
    expect.set_options(timeout=15000)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=args.browser_executable)
        try:
            if args.lifecycle_only:
                return await lifecycle_cases(browser, root / 'lifecycle', args.web_dist, results if results is not None else [])
            if live:
                return [await run_case(browser, root / 'live', args.web_dist, 'DeepSeek simple control', live=live)]
            cases = results if results is not None else []
            for name, legacy_contract, fault in [
                ('analysis-confirm-generation-autosave-export-reload', False, None),
                ('legacy-confirmed-approach-is-context-not-gate', True, None),
                ('delayed-project-and-proposal-reads', False, 'delayed-reads'),
                ('transient-preview-failure-can-retry', False, 'preview-once'),
                ('polling-completion-without-SSE', False, 'no-sse'),
            ]:
                print('START:', name, flush=True)
                result = await run_case(browser, root / name, args.web_dist, name,
                                        legacy_contract=legacy_contract, fault=fault)
                cases.append(result)
                print('PASS:', name, flush=True)
            await lifecycle_cases(browser, root / 'lifecycle', args.web_dist, cases)
            return cases
        finally:
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--web-dist', type=Path, default=ROOT / 'web/dist')
    parser.add_argument('--report', type=Path, default=Path('generation-delivery-e2e.json'))
    parser.add_argument('--lifecycle-only', action='store_true', help='Run lifecycle and request-budget acceptance only')
    parser.add_argument('--browser-executable', default=None, help='Optional installed Chromium executable')
    parser.add_argument('--live', action='store_true', help='Use a real DeepSeek API key; incurs API usage')
    args = parser.parse_args()
    args.web_dist = args.web_dist.resolve()
    os.environ['GX_DELIVERY_EVIDENCE_DIR'] = str(args.report.resolve().parent)
    if not (args.web_dist / 'index.html').is_file():
        parser.error('Build web/dist first')
    live = None
    if args.live:
        # No credential in CLI arguments, config files, GitHub files or reports.
        key = os.environ.get('DEEPSEEK_API_KEY') or getpass.getpass('DeepSeek API key (hidden): ')
        live = OpenAICompatibleProvider({'adapter': 'openai_compatible', 'model': 'deepseek-v4-flash',
            'baseUrl': 'https://api.deepseek.com', 'capabilities': {},
            'requestOverrides': {'max_tokens': 8192, 'extra_body': {'thinking': {'type': 'disabled'}}}}, key)
        del key
    report = {'ok': False, 'api_mocked': False, 'live_model_called': args.live,
              'native_gx_tested': False, 'temporary_workspace_only': True, 'cases': []}
    try:
        with tempfile.TemporaryDirectory(prefix='gx-delivery-e2e-') as scratch:
            report['cases'] = asyncio.run(exercise(args, Path(scratch), live, report['cases']))
        report['ok'] = True
    except Exception as error:
        # SDK exception strings may contain sensitive request data; never log
        # them in live mode. A failed run is not reported as a successful test.
        report['error_type'] = type(error).__name__
        if not args.live: report['error'] = traceback.format_exc()
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
