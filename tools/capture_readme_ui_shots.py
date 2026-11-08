# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Capture the real Web workbench UI for the README banner and Web section.

The screenshots are taken from the actual workbench: a project is imported through
the same service path the Web API uses, and the page is rendered by Chromium at 2x
scale in the light theme. No interface is drawn by hand here.

    python tools/capture_readme_ui_shots.py [--lang zh en ja]
    python tools/capture_readme_ui_shots.py --gxw path/to/PROJECT.gxw

Requires the Web runtime (``requirements/web.txt``), a built ``web/dist`` and
Playwright with an available Chromium channel. Per language, the full workbench is
written to ``resources/assets`` as ``web-workbench.png`` (and the ``-en`` /
``-ja`` variants); the README shows it in the Web section and the banner embeds it
as its window.
"""
import argparse
import base64
import secrets
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))
ASSETS = ROOT / 'resources' / 'assets'
TEMPLATE = ROOT / 'src' / 'gxw' / 'templates' / 'fx3u.gxw'
LANGS = ('zh', 'en', 'ja')
LOCALES = {'zh': 'zh-CN', 'en': 'en', 'ja': 'ja'}
SUFFIX = {'zh': '', 'en': '-en', 'ja': '-ja'}
VIEWPORT = {'width': 1600, 'height': 1000}
SCALE = 2


class OfflineSettings:
    """Settings stand-in: the capture never calls a model."""

    def __init__(self, model):
        self.model = model

    def public_settings(self):
        return {'language': 'zh-CN', 'active_profile_id': 'local', 'profiles': [
            {'id': 'local', 'name': 'Local model', 'model': 'local', 'configured': True}]}


class Workbench:
    """Serve the built workbench on a free port with a disposable workspace."""

    def __init__(self, workspace_root, web_dist):
        import uvicorn

        from application.workbench import WorkbenchService
        from integrations.web.app import create_app

        self.socket = socket.socket()
        self.socket.bind(('127.0.0.1', 0))
        self.origin = 'http://127.0.0.1:' + str(self.socket.getsockname()[1])
        self.token = secrets.token_urlsafe(32)
        self.service = WorkbenchService(workspace_root / 'workspace', workspace_root / 'state',
                                        settings=OfflineSettings('readme-capture'),
                                        model_factory=lambda: (None, None))
        self.app = create_app(self.service.store.base_dir, state_dir=self.service.state_dir,
                             service=self.service, operator_token=self.token, origin=self.origin,
                             static_dir=web_dist)
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level='error', access_log=False,
                                                   timeout_graceful_shutdown=5))
        self.thread = threading.Thread(target=lambda: self.server.run(sockets=[self.socket]), daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                return self
            if not self.thread.is_alive():
                raise SystemExit('The workbench backend did not start.')
            time.sleep(0.05)
        raise SystemExit('The workbench backend did not start in time.')

    def __exit__(self, exc_type, exc_value, traceback):
        self.server.should_exit = True
        self.thread.join(timeout=30)
        self.socket.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--lang', nargs='*', choices=LANGS, default=list(LANGS))
    parser.add_argument('--gxw', type=Path, default=TEMPLATE,
                        help='project imported before the capture (default: the bundled FX3U template)')
    parser.add_argument('--web-dist', type=Path, default=ROOT / 'web' / 'dist')
    parser.add_argument('--channel', default='msedge', help='Playwright Chromium channel (default: msedge)')
    args = parser.parse_args(argv)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit('Playwright is required: python -m pip install -r requirements/web.txt')
    if not (args.web_dist / 'index.html').is_file():
        raise SystemExit(f'Build the Web frontend first: {args.web_dist / "index.html"} is missing.')

    ASSETS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='readme-ui-') as directory:
        with Workbench(Path(directory), args.web_dist) as workbench:
            imported = workbench.service.gxw_files.import_project(
                data_base64=base64.b64encode(args.gxw.read_bytes()).decode('ascii'),
                filename=args.gxw.name, request_id='readme-ui-capture')
            project_id = imported['project_id']
            print(f'imported {args.gxw.name} as project {project_id}')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel=args.channel)
                try:
                    for lang in args.lang:
                        context = browser.new_context(viewport=VIEWPORT, device_scale_factor=SCALE)
                        context.add_init_script(
                            f"localStorage.setItem('gx.locale','{LOCALES[lang]}');"
                            "localStorage.setItem('gx.theme','light');")
                        page = context.new_page()
                        page.goto(f'{workbench.origin}/?project={project_id}#token={workbench.token}')
                        page.wait_for_selector('.fbd-panel, .canvas-shell, .program-explorer',
                                               timeout=60000)
                        page.wait_for_function(
                            "() => { const image = document.querySelector("
                            "'.canvas-shell img, .program-explorer img, img.ladder-svg, .fbd-panel img');"
                            " if (image) return image.complete && image.naturalWidth > 0;"
                            " return Boolean(document.querySelector("
                            "'.canvas-shell svg, .program-explorer svg, .fbd-panel svg')); }",
                            timeout=60000)
                        page.wait_for_timeout(1500)
                        suffix = SUFFIX[lang]
                        whole = ASSETS / f'web-workbench{suffix}.png'
                        page.screenshot(path=str(whole))
                        print('wrote', whole.relative_to(ROOT), whole.stat().st_size)
                        context.close()
                finally:
                    browser.close()


if __name__ == '__main__':
    sys.exit(main())
