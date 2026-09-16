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
        endpoint = Endpoint()
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
                        await page.get_by_label("模型", exact=True).fill("tenant-alias")
                        detect = page.get_by_role("button", name="自动获取模型 + 检测能力", exact=True)
                        await detect.click()
                        effort = page.get_by_role("slider", name="reasoning_effort", exact=True)
                        await expect(effort).to_be_enabled()
                        await effort.focus()
                        await effort.press("End")
                        await effort.press("ArrowLeft")
                        await expect(effort).to_have_attribute("aria-valuetext", "high")
                        # Temperature from the old reasoning mode must not survive.
                        await expect(page.get_by_role("slider", name="temperature", exact=True)).to_have_count(0)
                        await detect.click()
                        temperature = page.get_by_role("slider", name="temperature", exact=True)
                        await expect(temperature).to_be_enabled()
                        await temperature.focus()
                        await temperature.press("Home")
                        await temperature.press("ArrowRight")
                        await temperature.press("ArrowRight")
                        await expect(temperature).to_have_attribute("aria-valuetext", "0.5")
                        await page.get_by_role("button", name="创建配置", exact=True).click()
                        await expect(page.get_by_text("设置已保存", exact=True)).to_be_visible()
                        public = settings.public_settings()
                        saved = public["profiles"][0]
                        assert saved["generation_defaults"]["reasoning_effort"] == "high"
                        assert saved["generation_defaults"]["temperature"] == .5
                        assert "synthetic-only-key" not in json.dumps(public)
                        model, _ = settings.model_snapshot()
                        wire = model._request_params(ModelRequest((UserMessage("验收"),), stream=False))
                        assert wire["reasoning_effort"] == "high" and wire["temperature"] == .5
                        await page.reload()
                        await page.get_by_role("button", name="设置", exact=True).click()
                        await page.get_by_role("button", name="模型", exact=True).click()
                        await expect(page.get_by_role("slider", name="reasoning_effort")).to_have_attribute("aria-valuetext", "high")
                        await expect(page.get_by_role("slider", name="temperature")).to_have_attribute("aria-valuetext", "0.5")
                        # The settings modal scrolls independently from the page.
                        # Capture the controls, not just the top of the dialog.
                        await page.locator(".model-parameters").scroll_into_view_if_needed()
                        await page.screenshot(path=str(evidence / "model-parameters.png"), full_page=True)
                        await page.locator(".model-parameters").screenshot(path=str(evidence / "parameter-controls.png"))
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
