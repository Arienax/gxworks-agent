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
            model_factory=lambda: (provider, {'model': model}))
        self.app = create_app(self.service.store.base_dir, state_dir=self.service.state_dir,
            service=self.service, operator_token=self.token, origin=self.origin, static_dir=web_dist)
        self.preview_attempts = 0
        self.api_errors = []
        self.executions = []
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
            response = await call_next(request)
            if path.startswith('/api/') and response.status_code >= 500:
                self.api_errors.append((request.method, path, response.status_code))
            return response
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


async def exercise(args, root, live, results=None):
    # Real HTTP calls include persistence and polling; mocked UI timing does not
    # apply. All state, bytes, permissions and pixel assertions remain required.
    expect.set_options(timeout=15000)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
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
            return cases
        finally:
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--web-dist', type=Path, default=ROOT / 'web/dist')
    parser.add_argument('--report', type=Path, default=Path('generation-delivery-e2e.json'))
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
