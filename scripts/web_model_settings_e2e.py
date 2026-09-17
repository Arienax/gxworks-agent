"""Real HTTP/browser model-settings acceptance using a synthetic model transport.

Requires a built web/dist, requirements/web.txt, pytest and Playwright Chromium.
Only disposable settings/credentials/workspaces are used. No external API calls.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / "src", ROOT / "tests"):
    sys.path.insert(0, str(folder))

from playwright.async_api import async_playwright, expect
from scripts.web_demo import isolated_demo_settings
from scripts.web_generation_e2e import Server, Provider, open_page
from model_provider import ModelRequest, OpenAICompatibleProvider, UserMessage
from test_model_capabilities import Endpoint


async def run(web_dist, evidence):
    evidence.mkdir(parents=True, exist_ok=True)
    os.environ["GX_DELIVERY_EVIDENCE_DIR"] = str(evidence)
    endpoints = []

    def factory(profile, key):
        endpoint = Endpoint(metadata=[{"id": "tenant-alias", "parameters": {
            "enable_thinking": {"type": "boolean"},
            "thinking_budget": {"type": "integer", "minimum": 0, "maximum": 32768, "multipleOf": 1024,
                "wire_location": "extra_body", "wire_path": ["thinking", "budget_tokens"],
                "requires": {"enable_thinking": [True]}},
            "verbosity": {"enum": ["quiet", "normal", "verbose"]},
        }, "capabilities": {"vision": True, "audio": False}, "context_window": 262144}])
        endpoints.append(endpoint)
        return OpenAICompatibleProvider(profile, key, client=endpoint)

    with tempfile.TemporaryDirectory(prefix="gx-model-settings-") as folder:
        root = Path(folder)
        with isolated_demo_settings(root, factory) as settings:
            settings.delete_profile("offline")  # exercise the first unsaved profile
            server = Server(root, web_dist, Provider())
            server.service.settings = settings
            with server:
                pid = server.project("模型 API 验收")
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch()
                    context, page, errors = await open_page(browser, server, pid)
                    try:
                        await page.get_by_role("button", name="设置", exact=True).click()
                        await page.get_by_role("button", name="模型", exact=True).click()
                        await page.get_by_label("配置名称", exact=True).fill("多 API 验收")
                        await page.get_by_label("API URL", exact=True).fill("https://gateway.invalid/custom/v2/")
                        await page.locator('.settings-form input[type="password"]').fill("synthetic-only-key")
                        listing = page.get_by_role("button", name="获取模型列表", exact=True)
                        detect = page.get_by_role("button", name="快速能力检测", exact=True)
                        deep = page.get_by_role("button", name="深度参数扫描", exact=True)
                        await expect(detect).to_be_disabled()
                        await expect(deep).to_be_disabled()
                        await listing.click()
                        await expect(page.locator('.settings-form p[role="status"]')).to_contain_text("已获取")
                        await expect(page.get_by_label("模型", exact=True)).to_have_value("")
                        assert sum(len(endpoint.calls) for endpoint in endpoints) == 0
                        await page.get_by_label("模型", exact=True).fill("tenant-alias")
                        await detect.click()
                        await expect(deep).to_be_enabled()
                        await expect(page.get_by_role("slider", name="reasoning_effort", exact=True)).to_have_attribute("max", "1")
                        await page.screenshot(path=str(evidence / "quick-discovery.png"), full_page=True)
                        await deep.click()
                        effort = page.get_by_role("slider", name="reasoning_effort", exact=True)
                        await expect(effort).to_be_enabled()
                        await effort.focus()
                        await effort.press("End")
                        await effort.press("ArrowLeft")
                        await expect(effort).to_have_attribute("aria-valuetext", "high")
                        # Temperature from the old reasoning mode must not survive.
                        await expect(page.get_by_role("slider", name="temperature", exact=True)).to_have_count(0)
                        await deep.click()
                        temperature = page.get_by_role("slider", name="temperature", exact=True)
                        await expect(temperature).to_be_enabled()
                        await temperature.focus()
                        await temperature.press("Home")
                        await temperature.press("ArrowRight")
                        await temperature.press("ArrowRight")
                        await expect(temperature).to_have_attribute("aria-valuetext", "0.5")
                        await page.get_by_role("switch", name="enable_thinking", exact=True).check()
                        budget = page.get_by_role("slider", name="thinking_budget", exact=True)
                        await expect(budget).to_be_enabled()
                        await budget.focus()
                        await budget.press("Home")
                        for _ in range(9):
                            await budget.press("ArrowRight")
                        await expect(budget).to_have_attribute("aria-valuetext", "8192")
                        verbosity = page.get_by_role("slider", name="verbosity", exact=True)
                        await verbosity.focus()
                        await verbosity.press("End")
                        await expect(verbosity).to_have_attribute("aria-valuetext", "verbose")
                        await page.get_by_role("button", name="创建配置", exact=True).click()
                        await expect(page.get_by_text("设置已保存", exact=True)).to_be_visible()
                        public = settings.public_settings()
                        saved = public["profiles"][0]
                        assert saved["user_settings"]["parameters"]["reasoning_effort"] == {"mode": "value", "value": "high"}
                        assert saved["user_settings"]["parameters"]["temperature"] == {"mode": "value", "value": .5}
                        assert saved["user_settings"]["parameters"]["thinking_budget"]["value"] == 8192
                        assert not saved["generation_defaults"]
                        assert saved["contract"]["schema_version"] == 2
                        assert "synthetic-only-key" not in json.dumps(public)
                        model, _ = settings.model_snapshot()
                        wire = model._request_params(ModelRequest((UserMessage("验收"),), stream=False))
                        assert wire["reasoning_effort"] == "high" and wire["temperature"] == .5
                        assert wire["extra_body"]["thinking"]["budget_tokens"] == 8192
                        assert wire["enable_thinking"] is True and wire["verbosity"] == "verbose"
                        assert "thinking_budget" not in wire
                        await page.reload()
                        await page.get_by_role("button", name="设置", exact=True).click()
                        await page.get_by_role("button", name="模型", exact=True).click()
                        await expect(page.get_by_role("slider", name="reasoning_effort")).to_have_attribute("aria-valuetext", "high")
                        await expect(page.get_by_role("slider", name="temperature")).to_have_attribute("aria-valuetext", "0.5")
                        await page.get_by_role("slider", name="thinking_budget", exact=True).scroll_into_view_if_needed()
                        await page.screenshot(path=str(evidence / "model-parameters.png"), full_page=True)
                        await page.locator(".model-parameters").screenshot(path=str(evidence / "parameter-controls.png"))
                        await detect.click()
                        await expect(page.get_by_role("slider", name="reasoning_effort")).to_have_attribute("aria-valuetext", "high")
                        await expect(page.get_by_role("slider", name="reasoning_effort")).to_have_attribute("max", "3")
                        await listing.click()
                        await expect(page.get_by_label("模型", exact=True)).to_have_value("tenant-alias")
                        await expect(page.get_by_role("slider", name="reasoning_effort")).to_have_attribute("aria-valuetext", "high")
                        calls_before_test = sum(len(endpoint.calls) for endpoint in endpoints)
                        await page.get_by_role("button", name="测试连接", exact=True).click()
                        await expect(page.locator('.settings-form p[role="status"]')).to_contain_text("连接测试通过")
                        assert sum(len(endpoint.calls) for endpoint in endpoints) == calls_before_test
                        # Explicit omission stays omitted even when a production
                        # workflow supplies an effort hint after persistence.
                        await page.get_by_role("combobox", name="reasoning_effort mode").select_option("omit")
                        await page.get_by_role("button", name="保存并使用", exact=True).click()
                        await expect(page.get_by_text("设置已保存", exact=True)).to_be_visible()
                        model, _ = settings.model_snapshot()
                        omitted = model._request_params(ModelRequest((UserMessage("验收"),), stream=False,
                            options={"reasoning_effort": "high"}))
                        assert "reasoning_effort" not in omitted and "temperature" not in omitted
                        await page.get_by_label("模型", exact=True).fill("different-model")
                        await expect(page.get_by_role("slider")).to_have_count(0)
                        advanced = page.get_by_text("高级设置", exact=True)
                        await advanced.click()
                        draft_defaults = json.loads(await page.get_by_label("生成默认参数", exact=True).input_value())
                        assert "reasoning_effort" not in draft_defaults and "temperature" not in draft_defaults
                        assert not errors, errors
                        report = {"status": "passed", "checks": ["first-unsaved-profile-detection", "reasoning-slider",
                            "temperature-mode-invalidation", "temperature-slider", "persist-and-reload", "actual-request-options",
                            "model-switch-invalidation", "credential-redaction"],
                            "contract_version": 2,
                            "additional_checks": ["metadata-driven-unknown-controls", "boolean-switch", "nested-budget-wire-path",
                                "observation-selection-separation", "explicit-omit-beats-workflow",
                                "list-does-not-select-or-generate", "quick-partial-domain", "explicit-deep-expansion",
                                "cached-quick-keeps-deep-domain", "list-keeps-selected-contract", "connection-no-discovery"],
                            "synthetic_requests": sum(len(item.calls) for item in endpoints)}
                        (evidence / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                        print(json.dumps(report))
                    except BaseException:
                        await page.screenshot(path=str(evidence / "failure.png"), full_page=True)
                        raise
                    finally:
                        await context.close()
                        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-dist", type=Path, default=ROOT / "web" / "dist")
    parser.add_argument("--evidence", type=Path, default=ROOT / "model-api-evidence")
    args = parser.parse_args()
    asyncio.run(run(args.web_dist.resolve(), args.evidence.resolve()))
