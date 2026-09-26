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
                        field=page.get_by_label('配置名称',exact=True)
                        # Wait for the lazy panel before deciding whether its
                        # empty-profile create card is already open.
                        await expect(field.or_(page.get_by_role('button',name='新增配置',exact=True))).to_be_visible()
                        if await field.count() and await field.is_visible(): return
                        if name is None:
                            await page.get_by_role('button',name='新增配置',exact=True).click()
                        else:
                            await page.locator('.provider-row',has_text=name).get_by_role('button',name='编辑',exact=True).click()
                        await expect(field).to_be_visible()
                    try:
                        await open_settings()
                        await open_editor()
                        name_field=page.get_by_label('配置名称',exact=True)
                        service_field=page.get_by_role('combobox',name='服务',exact=True)
                        await name_field.fill(' ')
                        await service_field.select_option('https://api.deepseek.com')
                        await expect(name_field).to_have_value('DeepSeek')
                        await name_field.fill('Catalog v3 审查')
                        await service_field.select_option('https://api.openai.com/v1')
                        await expect(name_field).to_have_value('Catalog v3 审查')
                        await page.get_by_label('API URL',exact=True).fill('https://gateway.invalid/custom/v2/')
                        await page.locator('.settings-form input[type="password"]').fill('synthetic-only-key')
                        listing=page.get_by_role('button',name='获取模型列表',exact=True)
                        resolve=page.get_by_role('button',name='加载能力配置（零生成请求）',exact=True)
                        await expect(resolve).to_be_disabled()
                        await listing.click()
                        await expect(page.locator('.settings-form p[role="status"]')).to_contain_text('已获取')
                        await expect(page.get_by_label('模型',exact=True)).to_have_value('')
                        assert requests()==0
                        await page.get_by_label('模型',exact=True).fill('tenant-alias')
                        await resolve.click()
                        temp=page.get_by_label('temperature value',exact=True)
                        await expect(temp).to_be_enabled()
                        await expect(page.get_by_role('slider',name='temperature slider',exact=True)).to_have_attribute('step','0.01')
                        await temp.fill('0.733')
                        effort_control=page.locator('[data-parameter="reasoning_effort"]')
                        await page.locator('.model-parameters > details > summary').click()
                        await page.get_by_label('reasoning_effort value',exact=True).fill('high')
                        await page.get_by_role('switch',name='enable_thinking',exact=True).check()
                        await page.get_by_label('thinking_budget value',exact=True).fill('8192')
                        await page.get_by_role('combobox',name='verbosity choice',exact=True).select_option('2')
                        assert requests()==0
                        await page.get_by_role('button',name='创建配置',exact=True).click()
                        await expect(page.get_by_text('设置已保存',exact=True)).to_be_visible()
                        saved=settings.public_settings()['profiles'][0]
                        assert saved['contract']['schema_version']==3
                        assert saved['user_settings']['parameters']['temperature']['value']==.733
                        assert saved['user_settings']['parameters']['reasoning_effort']['value']=='high'
                        assert 'generation_defaults' not in saved
                        assert 'synthetic-only-key' not in json.dumps(settings.public_settings())
                        model,_=settings.model_snapshot()
                        wire=model._request_params(ModelRequest((UserMessage('synthetic validation'),),stream=False))
                        assert wire['temperature']==.733 and wire['reasoning_effort']=='high'
                        assert wire['extra_body']['thinking']['budget_tokens']==8192
                        assert wire['enable_thinking'] is True and wire['verbosity']=='verbose'
                        await (mount_fixture() if component_fixture else page.reload());await open_settings()
                        await open_editor('Catalog v3 审查')
                        await expect(page.get_by_label('temperature value',exact=True)).to_have_value('0.733')
                        await page.get_by_role('button',name='测试连接',exact=True).click()
                        await expect(page.locator('.settings-form p[role="status"]')).to_contain_text('没有发送生成请求')
                        assert requests()==0
                        await resolve.click()
                        await expect(page.get_by_label('temperature value',exact=True)).to_have_value('0.733')
                        verify=page.locator('[data-parameter="temperature"]').get_by_role('button',name='验证当前值（1 次请求）',exact=True)
                        page.once('dialog',lambda d:d.dismiss())
                        await verify.click();assert requests()==0
                        page.once('dialog',lambda d:d.accept())
                        await verify.click()
                        await expect(page.locator('.settings-form p[role="status"]')).to_contain_text('单次验证已完成')
                        assert requests()==1
                        await expect(page.locator('[data-parameter="temperature"]')).to_contain_text('accepted')
                        await page.locator('.model-parameters').screenshot(path=str(evidence/'parameter-controls-v3.png'))
                        # Explicit omission must not discard independent temperature.
                        await page.locator('.model-parameters > details > summary').click()
                        await effort_control.get_by_role('combobox',name='reasoning_effort mode',exact=True).select_option('omit')
                        await page.get_by_role('button',name='保存并使用',exact=True).click()
                        await expect(page.get_by_text('设置已保存',exact=True)).to_be_visible()
                        model,_=settings.model_snapshot()
                        options=model._request_params(ModelRequest((UserMessage('synthetic validation'),),stream=False,options={'reasoning_effort':'high'}))
                        assert 'reasoning_effort' not in options and options['temperature']==.733
                        # Manual domains can tighten a limit without erasing a draft.
                        await page.get_by_text('高级设置',exact=True).click()
                        manual={'parameters':{'temperature':{'type':'number','status':'supported','domain':{'minimum':0,'maximum':.5},'ui_hint':{'step':.01}},
                            'custom_flag':{'type':'boolean','status':'supported'}}}
                        await page.get_by_label('手动能力覆盖 JSON',exact=True).fill(json.dumps(manual))
                        await resolve.click()
                        await expect(page.get_by_label('temperature value',exact=True)).to_have_value('0.733')
                        await expect(page.locator('[data-parameter="temperature"]')).to_have_attribute('data-invalid','true')
                        await page.get_by_label('temperature value',exact=True).fill('0.37')
                        await page.get_by_role('switch',name='custom_flag',exact=True).check()
                        await page.get_by_role('switch',name='custom_flag',exact=True).uncheck()
                        await page.get_by_role('button',name='保存并使用',exact=True).click()
                        await expect(page.get_by_text('设置已保存',exact=True)).to_be_visible()
                        saved=settings.public_settings()['profiles'][0]
                        assert saved['user_settings']['parameters']['custom_flag']['value'] is False
                        assert saved['user_settings']['parameters']['temperature']['value']==.37
                        assert requests()==1
                        # A refreshed non-editable descriptor preserves the stale
                        # explicit choice, but must still let the operator clear it.
                        save=page.get_by_role('button',name='保存并使用',exact=True)
                        for status in ('unsupported','fixed'):
                            manual['parameters']['temperature']={
                                'type':'number','status':status,
                                **({'domain':{'values':[.25]}} if status=='fixed' else {}),
                            }
                            await page.get_by_label('手动能力覆盖 JSON',exact=True).fill(json.dumps(manual))
                            await resolve.click()
                            control=page.locator('[data-parameter="temperature"]')
                            await expect(control).to_have_attribute('data-invalid','true')
                            if not await control.is_visible():
                                await page.locator('.model-parameters > details > summary').click()
                            await expect(control.get_by_role('combobox',name='temperature mode',exact=True)).to_have_count(0)
                            await save.click()
                            await expect(page.locator('.settings-form > p[role="alert"]')).to_contain_text('请先修正参数类型、范围或关联条件冲突。')
                            persisted=settings.public_settings()['profiles'][0]['user_settings']['parameters']
                            assert persisted['temperature']=={'mode':'value','value':.37}
                            await control.get_by_role('button',name='恢复服务默认值',exact=True).click()
                            await expect(control).not_to_have_attribute('data-invalid','true')
                            await save.click()
                            await expect(page.get_by_text('设置已保存',exact=True)).to_be_visible()
                            persisted=settings.public_settings()['profiles'][0]['user_settings']['parameters']
                            assert persisted['temperature']=={'mode':'omit'}
                            assert persisted['custom_flag']=={'mode':'value','value':False}
                            model,_=settings.model_snapshot()
                            wire=model._request_params(ModelRequest((UserMessage('synthetic validation'),),stream=False))
                            assert 'temperature' not in wire and requests()==1
                            # Restore an editable explicit value for the next case.
                            manual['parameters']['temperature']={
                                'type':'number','status':'supported',
                                'domain':{'minimum':0,'maximum':.5},'ui_hint':{'step':.01},
                            }
                            await page.get_by_label('手动能力覆盖 JSON',exact=True).fill(json.dumps(manual))
                            await resolve.click()
                            await page.get_by_label('temperature value',exact=True).fill('0.37')
                            await save.click()
                            await expect(page.get_by_text('设置已保存',exact=True)).to_be_visible()
                        await page.screenshot(path=str(evidence/'settings-v3.png'),full_page=True)
                        await page.get_by_label('模型',exact=True).fill('never-seen-model')
                        await expect(page.locator('.model-parameters')).to_have_count(0)
                        assert json.loads(await page.get_by_label('手动能力覆盖 JSON',exact=True).input_value())=={}
                        assert not errors and not server.executions and not server.api_errors, (errors,server.api_errors)
                        report={'status':'passed','contract_version':3,
                            'browser_mode':'component-fixture-in-process-ASGI' if component_fixture else 'production-web-dist',
                            'synthetic_model_requests':requests(),'configuration_generation_requests':0,
                            'checks':['preset-empty-name-filled','preset-keeps-draft-name',
                                'list-no-auto-selection','local-resolve-zero-generation','unknown-parameter-editable',
                                'temperature-0.733-wire-precision','metadata-bool-and-nested-budget','persist-reload',
                                'connection-no-generations','verification-consent-cancel','explicit-single-verification',
                                'observation-is-evidence','user-omit-beats-workflow','manual-JSON-override',
                                'invalid-explicit-choice-preserved','boolean-false-saved',
                                'unsupported-explicit-choice-cleared','fixed-explicit-choice-cleared',
                                'model-scope-invalidation','credential-redaction']}
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
