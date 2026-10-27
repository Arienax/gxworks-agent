# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Real HTTP/browser acceptance of catalog v3; only a synthetic model transport.

Default: use the production Vite web/dist. --component-fixture is an explicitly
labelled offline harness for the same Settings component, not a production build.
No real credentials, engineering prompts, GX operations or paid APIs are used.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / 'src', ROOT / 'tests'):
    sys.path.insert(0, str(folder))

from playwright.async_api import async_playwright, expect
from scripts.web_demo import isolated_demo_settings
from scripts.web_generation_e2e import Server, Provider, open_page
from model_runtime.provider import ModelRequest, OpenAICompatibleProvider, UserMessage, test_model_profile
from test_model_verification import WireEndpoint


async def run(web_dist, evidence, *, executable=None, component_fixture=False):
    evidence.mkdir(parents=True, exist_ok=True)
    os.environ['GX_DELIVERY_EVIDENCE_DIR'] = str(evidence)
    endpoints = []
    def factory(profile, key):
        endpoint = WireEndpoint(ignore=True, metadata=[{'id':'tenant-alias','parameters':{
            'enable_thinking':{'type':'boolean'},
            'thinking_budget':{'type':'integer','minimum':0,'maximum':32768,'multipleOf':1024,
                'wire_location':'extra_body','wire_path':['thinking','budget_tokens'],
                'requires':{'enable_thinking':[True]}},
            'verbosity':{'enum':['quiet','normal','verbose']}},
            'capabilities':{'vision':True,'audio':False},'context_window':262144}])
        endpoints.append(endpoint)
        return OpenAICompatibleProvider(profile,key,client=endpoint)
    requests = lambda: sum(len(e.calls) for e in endpoints)
    with tempfile.TemporaryDirectory(prefix='gx-catalog-v3-') as directory:
        root = Path(directory)
        with isolated_demo_settings(root,factory) as settings, patch('model_runtime.provider.test_model_profile',test_model_profile):
            settings.delete_profile('offline')
            server = Server(root,web_dist,Provider())
            server.service.settings = settings
            with server:
                pid=server.project('Catalog v3 acceptance')
                async with async_playwright() as playwright:
                    browser=await playwright.chromium.launch(**({'executable_path':executable} if executable else {}))
                    if component_fixture:
                        context=await browser.new_context(viewport={'width':1400,'height':1200})
                        page=await context.new_page();errors=[]
                        page.on('pageerror',lambda e:errors.append(str(e)))
                        # Chromium's managed network policy may forbid local URL
                        # navigation in an offline runner. This opt-in unit harness
                        # uses in-process ASGI, never disables that browser policy.
                        import httpx
                        bridge = httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url=server.origin)
                        async def fixture_fetch(url, options):
                            if not (url == '/api/session' or url.startswith('/api/settings')):
                                raise ValueError('Fixture accepts settings/session requests only')
                            headers = {'Origin':server.origin, **{k:v for k,v in options.get('headers',{}).items()
                                if k.lower() in {'content-type','x-csrf-token'}}}
                            response = await bridge.request(options.get('method','GET'),url,headers=headers,content=options.get('body'))
                            return {'status':response.status_code,'body':response.text}
                        await page.expose_function('__ASGIFixtureFetch',fixture_fetch)
                        async def mount_fixture():
                            await page.evaluate('() => window.__FixtureRoot?.unmount()')
                            await page.set_content('<html lang="zh-CN"><head></head><body><div id="root"></div></body></html>')
                            await page.add_style_tag(content=(web_dist/'style.css').read_text())
                            await page.add_script_tag(content=(web_dist/'vendor.js').read_text(),type='module')
                            await page.wait_for_function('() => !!window.__FixtureReact')
                            await page.evaluate("""() => {window.fetch=async(url,options={})=>{const r=await window.__ASGIFixtureFetch(url,options);return new Response(r.body,{status:r.status,headers:{'Content-Type':'application/json'}})}}""")
                            await page.add_script_tag(content=(web_dist/'component-bundle.js').read_text())
                            await page.evaluate('(token) => window.__MountSettingsFixture(token)',server.token)
                        await mount_fixture()
                    else:
                        context,page,errors=await open_page(browser,server,pid)
                    async def open_settings():
                        if not component_fixture:
                            await page.get_by_role('button',name='设置',exact=True).click()
                            await page.get_by_role('button',name='模型',exact=True).click()
                    async def open_editor(name=None):
                        # Saved providers render as collapsed rows and only one
                        # editor card is open at a time. An empty profile list
                        # already opens the create card.
                        field=page.get_by_label('模型',exact=True)
                        # Wait for the lazy panel before deciding whether its
                        # empty-profile create card is already open.
                        await expect(field.or_(page.get_by_role('button',name='添加模型',exact=True))).to_be_visible()
                        if await field.count() and await field.is_visible(): return
                        if name is None:
                            await page.get_by_role('button',name='添加模型',exact=True).click()
                        else:
                            await page.locator('.provider-row',has_text=name).get_by_role('button',name='编辑',exact=True).click()
                        await expect(field).to_be_visible()
                    async def open_more():
                        details=page.locator('.more-settings')
                        if await details.get_attribute('open') is None:
                            await details.locator(':scope > summary').click()
                    async def open_debug():
                        details=page.locator('.model-debug')
                        if await details.get_attribute('open') is None:
                            await details.locator(':scope > summary').click()
                    try:
                        if not component_fixture:
                            await expect(page.get_by_role('heading',name='创建第一个程序',exact=True)).to_be_visible()
                            await expect(page.locator('.onboarding p, .onboarding ol')).to_have_count(0)
                            await expect(page.locator('.composer-hint, .agent-intro, .acceptance-note')).to_have_count(0)
                            await page.screenshot(path=str(evidence/'first-program.png'),full_page=True)
                        await open_settings()
                        await open_editor()
                        name_field=page.get_by_label('配置名称',exact=True)
                        service_field=page.get_by_role('combobox',name='服务',exact=True)
                        preset_source=page.get_by_role('button',name='预设服务',exact=True)
                        custom_source=page.get_by_role('button',name='自定义 API',exact=True)
                        await expect(page.get_by_role('group',name='模型服务来源',exact=True)).to_be_visible()
                        await expect(preset_source).to_be_visible()
                        await expect(custom_source).to_be_visible()
                        await expect(preset_source).to_have_attribute('aria-pressed','true')
                        await expect(custom_source).to_have_attribute('aria-pressed','false')
                        await expect(service_field.locator('option[value="custom"]')).to_have_count(0)
                        await expect(name_field).not_to_be_visible()
                        await expect(page.get_by_label('API URL',exact=True)).not_to_be_visible()
                        await expect(page.locator('.more-settings')).not_to_have_attribute('open','')
                        await expect(page.locator('.model-debug')).not_to_have_attribute('open','')
                        await expect(page.locator('.model-debug > summary')).to_have_text('debug')
                        await expect(page.locator('.settings-form > p, .feature-preferences, .mode-switch')).to_have_count(0)
                        await open_more()
                        await name_field.fill(' ')
                        await service_field.select_option('https://api.deepseek.com')
                        await expect(name_field).to_have_value('DeepSeek')
                        await page.get_by_label('模型',exact=True).fill('deepseek-chat')
                        await expect(page.get_by_role('button',name='保存',exact=True)).to_be_enabled()
                        await page.locator('.more-settings > summary').click()
                        await expect(page.locator('.model-parameters')).not_to_be_visible()
                        await expect(page.locator('.settings-form > p')).to_have_count(0)
                        assert requests()==0
                        await page.screenshot(path=str(evidence/'model-api-default.png'),full_page=True)
                        await open_more()
                        await page.get_by_label('模型',exact=True).fill('')
                        await name_field.fill('主力模型')
                        await service_field.select_option('https://api.openai.com/v1')
                        await expect(name_field).to_have_value('主力模型')
                        await page.locator('.more-settings > summary').click()
                        await expect(page.get_by_label('API URL',exact=True)).not_to_be_visible()
                        await open_more()
                        await page.get_by_label('API URL',exact=True).fill('https://gateway.invalid/custom/v2/')
                        await page.locator('.settings-form input[type="password"]').fill('synthetic-only-key')
                        await page.get_by_role('button',name='获取列表',exact=True).click()
                        await expect(page.get_by_label('模型',exact=True)).to_have_value('')
                        assert requests()==0
                        await page.get_by_label('模型',exact=True).fill('tenant-alias')
                        await expect(page.locator('.advanced-parameters')).to_be_visible()
                        await expect(page.get_by_role('button',name='加载能力配置（零生成请求）')).to_have_count(0)
                        await expect(page.locator('.model-debug')).not_to_have_attribute('open','')
                        await open_more()
                        temp_control=page.locator('.model-parameters [data-parameter="temperature"]')
                        flag_control=page.locator('.model-parameters [data-parameter="enable_thinking"]')
                        await expect(temp_control.get_by_role('slider')).to_have_count(0)
                        await expect(flag_control.get_by_role('switch')).to_have_count(0)
                        await flag_control.get_by_role('combobox').select_option('inherit')
                        await expect(flag_control.get_by_role('combobox')).to_have_value('inherit')
                        await expect(flag_control.get_by_role('switch')).to_have_count(0)
                        await temp_control.get_by_role('combobox').select_option('value')
                        await page.get_by_label('temperature value',exact=True).fill('0.733')
                        await expect(temp_control.get_by_role('slider')).to_have_count(0)
                        effort_control=page.locator('.model-parameters [data-parameter="reasoning_effort"]')
                        await effort_control.get_by_role('combobox',name='reasoning_effort mode').select_option('value')
                        await page.get_by_label('reasoning_effort value',exact=True).fill('high')
                        await flag_control.get_by_role('combobox').select_option('value')
                        await flag_control.get_by_role('switch').check()
                        await page.get_by_role('combobox',name='thinking_budget mode').select_option('value')
                        await page.get_by_label('thinking_budget value',exact=True).fill('8192')
                        await page.get_by_role('combobox',name='verbosity mode').select_option('value')
                        await page.get_by_role('combobox',name='verbosity choice').select_option(label='verbose')
                        await page.locator('.more-settings > summary').click()
                        await expect(page.locator('.more-settings > summary .badge')).to_have_text('5')
                        await custom_source.click()
                        await expect(custom_source).to_have_attribute('aria-pressed','true')
                        await expect(service_field).to_have_count(0)
                        await expect(name_field).to_have_value('主力模型')
                        await expect(page.get_by_label('API URL',exact=True)).to_have_value('https://gateway.invalid/custom/v2/')
                        await expect(page.get_by_label('模型',exact=True)).to_have_value('tenant-alias')
                        await expect(page.locator('.settings-form input[type="password"]')).to_have_value('synthetic-only-key')
                        await preset_source.click()
                        await expect(preset_source).to_have_attribute('aria-pressed','true')
                        await expect(service_field).to_have_value('https://api.openai.com/v1')
                        await expect(page.get_by_label('API URL',exact=True)).not_to_be_visible()
                        await expect(page.get_by_label('模型',exact=True)).to_have_value('tenant-alias')
                        await expect(page.locator('.settings-form input[type="password"]')).to_have_value('synthetic-only-key')
                        await expect(page.locator('.more-settings > summary .badge')).to_have_text('5')
                        assert requests()==0 and not settings.public_settings()['profiles']
                        await open_more()
                        await expect(page.get_by_label('temperature value',exact=True)).to_have_value('0.733')
                        await open_debug()
                        await page.locator('.request-preview > summary').click()
                        await page.get_by_role('button',name='生成预览（无模型请求）').click()
                        await expect(page.locator('.request-summary')).to_contain_text('0.733')
                        await expect(page.locator('.request-summary')).to_contain_text('本次请求未提供工具')
                        assert requests()==0
                        await page.get_by_role('button',name='保存',exact=True).click()
                        await expect(page.get_by_text('首个模型已保存并设为当前使用',exact=True)).to_be_visible()
                        saved=settings.public_settings()['profiles'][0]
                        first_id=saved['id']
                        assert settings.public_settings()['active_profile_id']==first_id
                        assert saved['user_settings']['parameters']['temperature']['value']==.733
                        assert saved['user_settings']['parameters']['reasoning_effort']['value']=='high'
                        assert 'synthetic-only-key' not in json.dumps(settings.public_settings())
                        model,_=settings.model_snapshot()
                        wire=model._request_params(ModelRequest((UserMessage('synthetic validation'),),stream=False))
                        assert wire['temperature']==.733 and wire['reasoning_effort']=='high'
                        assert wire['extra_body']['thinking']['budget_tokens']==8192
                        assert wire['enable_thinking'] is True and wire['verbosity']=='verbose'
                        await expect(page.locator('.provider-row')).to_contain_text('tenant-alias')
                        await expect(page.locator('.provider-row')).to_contain_text('当前使用')
                        await expect(page.locator('.provider-row')).not_to_contain_text('尚未验证生成')
                        await expect(page.locator('.provider-row')).not_to_contain_text('工具调用')
                        await page.get_by_role('button',name='添加模型',exact=True).click()
                        await custom_source.click()
                        await expect(service_field).to_have_count(0)
                        await page.get_by_label('配置名称',exact=True).fill('备用模型')
                        await page.get_by_label('API URL',exact=True).fill('https://other.invalid/v1')
                        await page.get_by_label('模型',exact=True).fill('unknown-model-exact')
                        await preset_source.click()
                        await expect(service_field).to_be_visible()
                        await expect(service_field).to_have_value('')
                        await custom_source.click()
                        await expect(custom_source).to_have_attribute('aria-pressed','true')
                        await expect(name_field).to_have_value('备用模型')
                        await expect(page.get_by_label('API URL',exact=True)).to_have_value('https://other.invalid/v1')
                        await expect(page.get_by_label('模型',exact=True)).to_have_value('unknown-model-exact')
                        assert requests()==0 and len(settings.public_settings()['profiles'])==1
                        await expect(page.get_by_role('button',name='保存',exact=True)).to_be_enabled()
                        await expect(page.locator('.model-parameters')).not_to_be_visible()
                        await expect(page.get_by_role('button',name='测试连接',exact=True)).not_to_be_visible()
                        await page.screenshot(path=str(evidence/'model-api-custom.png'),full_page=True)
                        await page.get_by_role('button',name='保存',exact=True).click()
                        await expect(page.get_by_text('设置已保存',exact=True)).to_be_visible()
                        backup=next(p for p in settings.public_settings()['profiles'] if p['name']=='备用模型')
                        assert settings.public_settings()['active_profile_id']==first_id
                        await open_editor('备用模型')
                        await name_field.fill('备用模型已调整')
                        await page.get_by_role('button',name='保存',exact=True).click()
                        await expect(page.get_by_text('设置已保存',exact=True)).to_be_visible()
                        assert settings.public_settings()['active_profile_id']==first_id
                        await page.locator('.provider-row',has_text='备用模型已调整').get_by_role('button',name='使用此模型').click()
                        await expect(page.locator('.provider-row',has_text='备用模型已调整')).to_have_attribute('data-active','true')
                        assert settings.public_settings()['active_profile_id']==backup['id']
                        assert requests()==0
                        await page.screenshot(path=str(evidence/'models-and-connections.png'),full_page=True)
                        await open_editor('主力模型')
                        await expect(page.get_by_role('button',name='测试连接',exact=True)).not_to_be_visible()
                        await open_debug()
                        await page.get_by_role('button',name='测试连接',exact=True).click()
                        await expect(page.locator('.model-debug p[role="status"]')).to_contain_text('没有发送生成请求')
                        assert requests()==0
                        await open_more()
                        await expect(page.get_by_label('temperature value',exact=True)).to_have_value('0.733')
                        verify=page.locator('.model-diagnostics [data-parameter="temperature"]').get_by_role('button',name='验证当前值（1 次请求）',exact=True)
                        page.once('dialog',lambda d:d.dismiss())
                        await verify.click();assert requests()==0
                        page.once('dialog',lambda d:d.accept())
                        await verify.click()
                        await expect(page.locator('.model-debug p[role="status"]')).to_contain_text('单次验证已完成')
                        assert requests()==1
                        await expect(page.locator('.model-diagnostics [data-parameter="temperature"]')).to_contain_text('accepted')
                        # Tightening manual domains must preserve and expose stale selections.
                        manual={'parameters':{'temperature':{'type':'number','status':'supported','domain':{'minimum':0,'maximum':.5}},
                            'custom_flag':{'type':'boolean','status':'supported'}}}
                        await page.get_by_label('手动能力覆盖 JSON',exact=True).fill(json.dumps(manual))
                        await expect(temp_control).to_have_attribute('data-invalid','true')
                        await page.get_by_role('button',name='保存',exact=True).click()
                        await expect(page.locator('.settings-form > p[role="alert"]')).to_contain_text('请检查更多设置中的参数')
                        await page.get_by_label('temperature value',exact=True).fill('0.37')
                        await page.get_by_role('combobox',name='custom_flag mode').select_option('value')
                        await page.get_by_role('switch',name='custom_flag').check()
                        await page.get_by_role('switch',name='custom_flag').uncheck()
                        await page.get_by_role('button',name='保存并使用',exact=True).click()
                        await expect(page.get_by_text('设置已保存，已切换当前模型',exact=True)).to_be_visible()
                        assert settings.public_settings()['active_profile_id']==first_id
                        params=settings.public_settings()['profiles'][0]['user_settings']['parameters']
                        assert params['custom_flag']['value'] is False and params['temperature']['value']==.37
                        for stale_status in ('unsupported','fixed'):
                            await open_editor('主力模型')
                            await open_debug()
                            manual['parameters']['temperature']={'type':'number','status':stale_status,
                                **({'domain':{'values':[.25]}} if stale_status=='fixed' else {})}
                            await page.get_by_label('手动能力覆盖 JSON',exact=True).fill(json.dumps(manual))
                            await expect(temp_control).to_have_attribute('data-invalid','true')
                            await open_more()
                            await expect(temp_control.get_by_role('combobox')).to_have_count(0)
                            await temp_control.get_by_role('button',name='恢复服务默认值',exact=True).click()
                            await expect(temp_control).to_have_count(0)
                            await page.get_by_role('button',name='保存',exact=True).click()
                            await expect(page.get_by_text('设置已保存',exact=True)).to_be_visible()
                            persisted=settings.public_settings()['profiles'][0]['user_settings']['parameters']
                            assert persisted['temperature']=={'mode':'omit'} and persisted['custom_flag']['value'] is False
                            await open_editor('主力模型')
                            await open_debug()
                            manual['parameters']['temperature']={'type':'number','status':'supported','domain':{'minimum':0,'maximum':.5}}
                            await page.get_by_label('手动能力覆盖 JSON',exact=True).fill(json.dumps(manual))
                            await open_more()
                            await expect(page.get_by_role('combobox',name='temperature mode')).to_be_enabled()
                            await page.get_by_role('combobox',name='temperature mode').select_option('value')
                            await page.get_by_label('temperature value',exact=True).fill('0.37')
                            await page.get_by_role('button',name='保存',exact=True).click()
                            await expect(page.get_by_text('设置已保存',exact=True)).to_be_visible()
                        # Active deletion describes the same ordered fallback as the backend.
                        row=page.locator('.provider-row',has_text='主力模型')
                        await row.locator('summary').click()
                        await row.get_by_role('button',name='删除',exact=True).click()
                        await expect(page.get_by_role('dialog').last).to_contain_text('删除后将使用: 备用模型已调整 · unknown-model-exact')
                        await page.get_by_role('dialog').last.get_by_role('button',name='取消',exact=True).click()
                        await open_editor('备用模型已调整')
                        await page.get_by_label('模型',exact=True).fill('superseded-model')
                        await page.get_by_label('模型',exact=True).fill('never-seen-model')
                        await expect(page.get_by_role('button',name='保存',exact=True)).to_be_enabled()
                        await open_more()
                        await expect(page.locator('.advanced-parameters')).to_be_visible()
                        await page.screenshot(path=str(evidence/'model-preferences.png'),full_page=True)
                        await page.get_by_role('button',name='取消',exact=True).click()
                        await page.get_by_role('button',name='关闭',exact=True).click()
                        # Conversation stays free of per-request model details.
                        if not component_fixture:
                            import threading
                            from model_runtime.usage import usage_scope, tool_executed
                            bound, frozen=settings.model_snapshot()
                            release=threading.Event()
                            def worker(ctx):
                                with usage_scope(ctx.emit):
                                    list(bound.stream(ModelRequest((UserMessage('synthetic task'),),stream=False)))
                                    tool_executed('search_plc_manual',True)
                                    if not release.wait(20): raise RuntimeError('Browser did not release the synthetic task')
                                return {'status':'complete'}
                            job=server.service.jobs.submit('agent',{'project_id':pid,'model':frozen},worker,request_id='usage-ui')
                            try:
                                await expect(page.locator('.job-conversation')).to_be_visible()
                                await expect(page.locator('.job-model-usage')).to_have_count(0)
                                await expect(page.locator('.agent-sidebar .request-summary')).to_have_count(0)
                                await expect(page.locator('.agent-sidebar')).not_to_contain_text('tenant-alias')
                                await page.get_by_role('combobox',name='当前模型（用于新任务）').select_option(backup['id'])
                                await expect(page.get_by_role('combobox',name='当前模型（用于新任务）')).to_have_value(backup['id'])
                                await expect(page.locator('.model-switcher')).to_contain_text('运行中: 主力模型')
                                await expect(page.locator('.agent-sidebar')).not_to_contain_text('unknown-model-exact')
                                header=await page.locator('.app-header').bounding_box()
                                control=await page.locator('.model-switcher').bounding_box()
                                assert control['y']>=header['y'] and control['y']+control['height']<=header['y']+header['height']
                                await page.screenshot(path=str(evidence/'conversation.png'),full_page=True)
                            finally:
                                release.set()
                            server.service.jobs._futures[job['id']].result(timeout=5)
                        assert not errors and not server.executions and not server.api_errors, (errors,server.api_errors)
                        assert requests()==(1 if component_fixture else 2)
                        report={'status':'passed','contract_version':3,
                            'browser_mode':'component-fixture-in-process-ASGI' if component_fixture else 'production-web-dist',
                            'synthetic_model_requests':requests(),'configuration_generation_requests':0,
                            'checks':['automatic-local-resolution','precise-model-id','default-has-no-false-toggle-or-slider',
                                'save-backup-preserves-active','create-backup-preserves-active','direct-list-switch',
                                'explicit-save-and-use','active-delete-fallback-described','unknown-model-editable',
                                'preview-without-generation','single-consented-verification','manual-conflict-preserved',
                                'boolean-false-preserved','rapid-model-change','connection-check-is-not-generation','stale-fixed-and-unsupported-cleared',
                                'running-task-model-snapshot','conversation-without-model-request-details','concise-first-program-screen',
                                'minimal-default-api-form','custom-api-fields-visible','debug-is-opt-in',
                                'folded-parameters-preserve-values-and-count','parallel-api-source-entry',
                                'source-switch-preserves-draft']}
                        (evidence/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
                        print(json.dumps(report),flush=True)
                    except BaseException:
                        await page.screenshot(path=str(evidence/'failure.png'),full_page=True)
                        raise
                    finally:
                        await context.close();await browser.close()
                        if component_fixture:
                            await bridge.aclose()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--web-dist',type=Path,default=ROOT/'web'/'dist')
    parser.add_argument('--evidence',type=Path,default=ROOT/'model-api-evidence')
    parser.add_argument('--browser-executable',default=None)
    parser.add_argument('--component-fixture',action='store_true')
    args=parser.parse_args()
    asyncio.run(run(args.web_dist.resolve(),args.evidence.resolve(),executable=args.browser_executable,component_fixture=args.component_fixture))
