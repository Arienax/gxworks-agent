# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Generate the README banner and feature figures as PNG files.

The figures use a flat engineering-software style (light panels, thin borders,
square corners) and are drawn as SVG in three languages (zh-CN, en, ja), then
rasterised with a headless Chromium-based browser (Microsoft Edge or Chrome).
The banner is composed from two real assets instead of drawn interface parts:
``resources/assets/logo.svg`` supplies the product mark, and the workbench
window is the screenshot captured by ``tools/capture_readme_ui_shots.py``.

    python tools/capture_readme_ui_shots.py [--gxw PROJECT.gxw]
    python tools/generate_readme_assets.py [--scale 2] [--keep-svg DIR]

Outputs go to ``resources/assets``. Simplified Chinese uses the bare file name
(``banner.png``); the other languages use the ``-en`` / ``-ja`` suffix.
"""
import argparse
import base64
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'resources' / 'assets'
LANGS = ('zh', 'en', 'ja')
SUFFIX = {'zh': '', 'en': '-en', 'ja': '-ja'}

INK, MUTED, SOFT = '#1f2328', '#59636e', '#8a939c'
LINE, LIGHT = '#8f99a3', '#d3d8dd'
PANEL, HEAD, TITLE = '#f3f4f6', '#e3e6ea', '#2e3a46'
BLUE, TEAL, GREEN, ORANGE = '#0063b1', '#00808a', '#2f8a3b', '#c76f00'
WIRE = '#4b5560'
SANS = {
    'zh': "'Microsoft YaHei UI','Microsoft YaHei','PingFang SC','Noto Sans CJK SC','Segoe UI',sans-serif",
    'en': "'Segoe UI','Helvetica Neue',Arial,'Microsoft YaHei UI',sans-serif",
    'ja': "'Yu Gothic UI','Meiryo UI','Hiragino Sans','Noto Sans CJK JP','Segoe UI',sans-serif",
}
MONO = "Consolas,'Cascadia Mono',Menlo,monospace"
WARNINGS = []


def est_width(value, size, mono=False):
    return sum(size if ord(ch) > 0x2E80 else size * (0.55 if mono else 0.54) for ch in value)


def wrap(value, size, maxw, mono=False):
    """Break a label into lines that fit maxw: Latin words stay whole, CJK breaks per character."""
    tokens, word = [], ''
    for char in value:
        if ord(char) > 0x2E80:
            if word:
                tokens.append(word)
                word = ''
            tokens.append(char)
        elif char == ' ':
            tokens.append(word + ' ')
            word = ''
        else:
            word += char
    if word:
        tokens.append(word)
    lines, line = [], ''
    for token in tokens:
        if line.strip() and est_width((line + token).rstrip(), size, mono) > maxw:
            lines.append(line.strip())
            line = token.lstrip()
        else:
            line += token
    lines.append(line.strip())
    return lines


def pick(table, lang):
    return table[LANGS.index(lang)]


class Canvas:
    def __init__(self, lang, width, height, name):
        self.lang, self.w, self.h, self.name = lang, width, height, name
        self.parts = []

    def add(self, markup):
        self.parts.append(markup)

    def text(self, x, y, value, size=13, fill=INK, weight=400, anchor='start', mono=False, maxw=None):
        if maxw and est_width(value, size, mono) > maxw:
            WARNINGS.append(f'{self.name}/{self.lang}: "{value}" ~{est_width(value, size, mono):.0f}px > {maxw}px')
        family = MONO if mono else SANS[self.lang]
        self.add(f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}" '
                 f'fill="{fill}" text-anchor="{anchor}" xml:space="preserve">{escape(value)}</text>')

    def code(self, x, y, segments, size=12.5):
        spans = ''.join(f'<tspan fill="{color}">{escape(part)}</tspan>' for part, color in segments)
        self.add(f'<text x="{x}" y="{y}" font-family="{MONO}" font-size="{size}" xml:space="preserve">{spans}</text>')

    def rect(self, x, y, w, h, fill='#ffffff', stroke=LINE, sw=1, r=0, dash=None, opacity=None):
        stroke_attr = f'stroke="{stroke}" stroke-width="{sw}"' if stroke else 'stroke="none"'
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ''
        alpha = f' opacity="{opacity}"' if opacity is not None else ''
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" {stroke_attr}{dash_attr}{alpha}/>')

    def line(self, x1, y1, x2, y2, stroke=WIRE, sw=1.2, dash=None):
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ''
        self.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" stroke-width="{sw}"{dash_attr}/>')

    def path(self, d, stroke=WIRE, sw=1.4, fill='none'):
        self.add(f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" '
                 f'stroke-linecap="round" stroke-linejoin="round"/>')

    def wire(self, points, stroke=WIRE, sw=1.4, head=True):
        """Orthogonal connector with an optional arrow head at the last point."""
        pts = [list(p) for p in points]
        if head:
            (x1, y1), (x2, y2) = pts[-2], pts[-1]
            dx, dy = (x2 > x1) - (x2 < x1), (y2 > y1) - (y2 < y1)
            pts[-1] = [x2 - dx * 7, y2 - dy * 7]
            px, py = -dy, dx
            self.add(f'<path d="M{x2} {y2} L{x2 - dx * 8 + px * 4} {y2 - dy * 8 + py * 4} '
                     f'L{x2 - dx * 8 - px * 4} {y2 - dy * 8 - py * 4} Z" fill="{stroke}"/>')
        d = 'M' + ' L'.join(f'{x} {y}' for x, y in pts)
        self.add(f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{sw}" stroke-linejoin="miter"/>')

    def check(self, x, y, color=GREEN):
        self.path(f'M{x} {y - 4} l3.5 3.5 l7 -8', color, 1.8)

    def tag(self, cx, cy, label, color=BLUE, size=12, mono=False):
        w = est_width(label, size, mono) + 18
        self.rect(cx - w / 2, cy - 10, w, 20, '#ffffff', color, 1)
        self.text(cx, cy + 4.3, label, size, color, 600, 'middle', mono)

    def render(self):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}"><rect width="{self.w}" height="{self.h}" fill="#ffffff"/>'
                + ''.join(self.parts)
                + f'<rect x="0.5" y="0.5" width="{self.w - 1}" height="{self.h - 1}" fill="none" stroke="#b7bec6"/></svg>')


def frame(c, title, subtitle):
    """Dialog-like figure header: dark title bar plus a subtitle strip."""
    c.rect(0, 0, c.w, 40, TITLE, None)
    c.text(20, 26, title, 16, '#ffffff', 600, maxw=c.w - 200)
    c.text(c.w - 20, 25, 'GXWorks Agent', 12, '#aeb8c2', 400, 'end')
    c.rect(0, 40, c.w, 30, PANEL, None)
    c.line(0, 70, c.w, 70, LIGHT, 1)
    c.text(20, 60, subtitle, 13, MUTED, maxw=c.w - 40)


def block(c, x, y, w, h, title, mark=BLUE, right=None):
    c.rect(x, y, w, h, '#ffffff', LINE)
    c.rect(x, y, w, 24, HEAD, LINE)
    c.rect(x + 9, y + 8, 8, 8, mark, None)
    c.text(x + 24, y + 16.5, title, 13, INK, 600, maxw=w - 34)
    if right:
        c.text(x + w - 10, y + 16.5, right, 11.5, MUTED, 400, 'end', True)


def lines(c, x, y, items, size=12.5, gap=20, fill=MUTED, maxw=None, mono_index=()):
    for i, item in enumerate(items):
        c.text(x, y + i * gap, item, size, BLUE if i in mono_index else fill, 400, mono=i in mono_index, maxw=maxw)


# --------------------------------------------------------------- ladder art
def contact(c, cx, ry, label, nc=False):
    c.line(cx, ry - 8, cx, ry + 8, INK, 1.6)
    c.line(cx + 12, ry - 8, cx + 12, ry + 8, INK, 1.6)
    if nc:
        c.line(cx - 2, ry + 8, cx + 14, ry - 8, INK, 1.4)
    c.text(cx + 6, ry - 13, label, 11, INK, 400, 'middle', True)


def coil(c, cx, ry, label):
    c.path(f'M{cx} {ry - 9} Q{cx - 7} {ry} {cx} {ry + 9}', INK, 1.5)
    c.path(f'M{cx + 20} {ry - 9} Q{cx + 27} {ry} {cx + 20} {ry + 9}', INK, 1.5)
    c.text(cx + 10, ry - 13, label, 11, INK, 400, 'middle', True)


def ladder(c, x, y, w, h):
    """GX Works2-like ladder: step column, rails, three rungs and END."""
    c.rect(x, y, 34, h, '#f0f2f4', None)
    c.line(x + 34, y, x + 34, y + h, LIGHT, 1)
    left, right = x + 46, x + w - 10
    c.line(left, y + 10, left, y + h - 10, INK, 1.6)
    c.line(right, y + 10, right, y + h - 10, INK, 1.6)
    r0, r1, r2 = y + 34, y + h * 0.62, y + h - 24
    for step, ry in (('0', r0), ('4', r1), ('10', r2)):
        c.text(x + 28, ry + 4, step, 11, SOFT, 400, 'end', True)
    # rung 0: self-holding output
    c.line(left, r0, left + 20, r0)
    contact(c, left + 20, r0, 'X0')
    c.line(left + 32, r0, left + 84, r0)
    contact(c, left + 84, r0, 'X1', True)
    c.rect(left + 76, r0 - 26, 28, 38, 'none', BLUE, 1.2)
    c.line(left + 96, r0, right - 44, r0)
    coil(c, right - 44, r0, 'Y0')
    c.line(right - 24, r0, right, r0)
    br = r0 + 30
    c.line(left + 8, r0, left + 8, br)
    c.line(left + 8, br, left + 20, br)
    contact(c, left + 20, br, 'Y0')
    c.line(left + 32, br, left + 58, br)
    c.line(left + 58, br, left + 58, r0)
    # rung 4: MOV instruction
    c.line(left, r1, left + 20, r1)
    contact(c, left + 20, r1, 'M0')
    box_w = min(150, w * 0.42)
    c.line(left + 32, r1, right - box_w - 6, r1)
    c.rect(right - box_w - 6, r1 - 10, box_w, 20, '#ffffff', INK, 1.3)
    c.text(right - box_w / 2 - 6, r1 + 4, 'MOV   K10   D0', 11.5, INK, 400, 'middle', True)
    c.line(right - 6, r1, right, r1)
    # END
    c.line(left, r2, right - 66, r2)
    c.rect(right - 66, r2 - 10, 60, 20, '#ffffff', INK, 1.3)
    c.text(right - 36, r2 + 4, 'END', 11.5, INK, 600, 'middle', True)
    c.line(right - 6, r2, right, r2)


# -------------------------------------------------------------------- banner
def png_size(path):
    """Read the pixel size from a PNG header without an imaging dependency."""
    header = path.read_bytes()[:24]
    if header[:8] != b'\x89PNG\r\n\x1a\n':
        raise SystemExit(f'{path} is not a PNG file.')
    return struct.unpack('>II', header[16:24])


def ui_shot(c, x, y, w, h):
    """Place the captured workbench window in the banner, scaled to the slot."""
    path = ASSETS / ('web-workbench' + SUFFIX[c.lang] + '.png')
    if not path.is_file():
        raise SystemExit(f'Missing {path.name}; run tools/capture_readme_ui_shots.py first.')
    width, height = png_size(path)
    data = base64.b64encode(path.read_bytes()).decode('ascii')
    c.add(f'<svg x="{x}" y="{y}" width="{w}" height="{h}" viewBox="0 0 {width} {height}">'
          f'<image x="0" y="0" width="{width}" height="{height}" '
          f'href="data:image/png;base64,{data}"/></svg>')


LOGO = ASSETS / 'logo.svg'


def brand_mark(c, x, y, size):
    """Place the product logo; the markup itself lives in resources/assets/logo.svg."""
    if not LOGO.is_file():
        raise SystemExit(f'Missing {LOGO.name}: the banner needs the product logo.')
    markup = LOGO.read_text(encoding='utf-8')
    inner = markup[markup.index('>', markup.index('<svg')) + 1:markup.rindex('</svg>')]
    inner = re.sub(r'<title[^>]*>.*?</title>', '', inner, flags=re.S)
    c.add(f'<svg x="{x}" y="{y}" width="{size}" height="{size}" viewBox="0 0 256 256">{inner}</svg>')


def banner(lang):
    c = Canvas(lang, 1280, 440, 'banner')
    c.rect(0, 0, 1280, 440, PANEL, None)
    c.rect(0, 0, 1280, 4, BLUE, None)
    brand_mark(c, 64, 60, 52)
    c.text(132, 83, pick(('三菱 PLC 工程工具链', 'Mitsubishi PLC engineering toolchain', '三菱 PLC エンジニアリングツールチェーン'), lang), 14, MUTED, 600, maxw=460)
    c.text(132, 105, 'MCP Server  ·  Web Workbench', 13, SOFT, 400, mono=True)
    c.text(62, 196, 'GXWorks Agent', 46, INK, 600)
    c.text(64, 234, pick(('面向三菱 PLC 工程的 MCP 工具服务与 AI 工作台',
                          'MCP engineering tools and an AI workbench',
                          '三菱 PLC 向け MCP ツールサービスと AI ワークベンチ'), lang), 19, INK, 400, maxw=520)
    c.text(64, 264, pick(('读取工程上下文 · 生成与审阅程序 · 驱动 GX Works2 原生工程与诊断',
                          'Project context · program build & review · native GX Works2',
                          'プロジェクト文脈 · 生成とレビュー · GX Works2 ネイティブ操作と診断'), lang), 14, MUTED, maxw=520)
    x = 64
    for chip in ('Ladder / ST / FBD / SFC', 'GX Works2 1.635.0.1', 'FX / Q / L CPU'):
        w = est_width(chip, 12.5) + 22
        c.rect(x, 296, w, 26, '#ffffff', LINE, 1, 2)
        c.text(x + w / 2, 313.5, chip, 12.5, INK, 400, 'middle')
        x += w + 8
    c.text(64, 380, 'Apache-2.0  ·  Windows 10/11 x64  ·  github.com/Arienax/gxworks-agent', 12, SOFT, mono=True)

    # the real workbench window, captured by tools/capture_readme_ui_shots.py
    wx, wy, ww, wh = 632, 38, 584, 365
    c.rect(wx + 4, wy + 5, ww, wh, '#000000', None, opacity=0.07)
    ui_shot(c, wx, wy, ww, wh)
    c.rect(wx, wy, ww, wh, 'none', '#9aa3ac')
    return c


# -------------------------------------------------------------- architecture
def feature_architecture(lang):
    c = Canvas(lang, 1200, 530, 'architecture')
    frame(c, *pick((('系统架构', '外部客户端负责模型推理与规划；GXWorks Agent 提供工程上下文、确定性工具、版本持久化与执行回执'),
                    ('System architecture', 'Clients handle model reasoning and planning; GXWorks Agent supplies context, deterministic tools, versioning and receipts'),
                    ('システム構成', '推論と計画はクライアント側。GXWorks Agent は文脈・確定的ツール・版管理・実行レシートを提供')), lang))
    x0, width = 40, 1120
    clients = (('Codex', pick(('MCP 客户端 · stdio', 'MCP client · stdio', 'MCP クライアント · stdio'), lang)),
               ('Claude Desktop', pick(('MCP 客户端 · stdio', 'MCP client · stdio', 'MCP クライアント · stdio'), lang)),
               ('DeepSeek Harness', pick(('插件 · 免额外安装', 'Plugin · no extra setup', 'プラグイン · 追加設定不要'), lang)),
               (pick(('其他 MCP 客户端', 'Other MCP clients', 'その他の MCP クライアント'), lang), pick(('--local 独立运行', 'Standalone --local', '--local で単独実行'), lang)),
               (pick(('Web 工作台', 'Web workbench', 'Web ワークベンチ'), lang), pick(('Direct 生成 · 图形审阅', 'Direct generation · review', 'Direct 生成 · 図形レビュー'), lang)))
    bus1, bus2 = 178, 428
    for i, (name, sub) in enumerate(clients):
        bx = x0 + i * 228
        block(c, bx, 94, 208, 58, name, TEAL if i == 4 else BLUE)
        c.text(bx + 12, 140, sub, 12.5, MUTED, maxw=188)
        c.line(bx + 104, 152, bx + 104, bus1, BLUE, 1.4)
        c.rect(bx + 101, bus1 - 3, 6, 6, BLUE, None)
    c.line(x0, bus1, x0 + width, bus1, BLUE, 3)
    c.tag(486, bus1, pick(('MCP · stdio  /  Web API', 'MCP · stdio  /  Web API', 'MCP · stdio  /  Web API'), lang), BLUE)

    # agent
    ay = 204
    c.line(600, bus1, 600, ay, BLUE, 1.4)
    block(c, x0, ay, width, 196, pick(('GXWorks Agent · Python Core', 'GXWorks Agent · Python Core', 'GXWorks Agent · Python Core'), lang),
          INK, 'gxworks-agent-mcp.exe  ·  start-web.cmd')
    modules = pick((
        (('工程与版本管理', ('工程 · 版本 · 规格', '候选审阅与版本差异', '现场只读快照')),
         ('CPU 作用域 RAG', ('实体 · BM25 · Dense/LSA', '域内局部 RRF 融合', '指令事实库')),
         ('确定性 Core 校验', ('PLC IR 构建', '操作数与时序检查', 'FBD 拓扑与声明核对')),
         ('GXW 离线读写', ('Ladder / ST / FBD / SFC', '事务式联合编辑', '未改记录保持原字节')),
         ('产物生成', ('program.gxw', 'Ladder JSON · PLC IR · SVG', 'CSV · TXT · 写入报告'))),
        (('Projects & versions', ('Projects · versions · specs', 'Candidate review & diff', 'Read-only live snapshots')),
         ('CPU-scoped RAG', ('Entity · BM25 · Dense/LSA', 'Domain-local RRF fusion', 'Instruction fact store')),
         ('Deterministic checks', ('PLC IR construction', 'Operand & timing checks', 'FBD topology & labels')),
         ('Offline GXW I/O', ('Ladder / ST / FBD / SFC', 'Transactional joint edits', 'Untouched bytes preserved')),
         ('Artifacts', ('program.gxw', 'Ladder JSON · PLC IR · SVG', 'CSV · TXT · reports'))),
        (('プロジェクトと版管理', ('プロジェクト · 版 · 仕様', '候補レビューと版差分', '読み取り専用スナップショット')),
         ('CPU 単位の RAG', ('実体照合 · BM25 · Dense/LSA', 'ドメイン内 RRF 融合', '命令事実ストア')),
         ('確定的 Core 検証', ('PLC IR の構築', 'オペランド・タイミング検査', 'FBD トポロジと宣言')),
         ('GXW オフライン読み書き', ('Ladder / ST / FBD / SFC', 'トランザクション編集', '未変更バイトを保持')),
         ('成果物の生成', ('program.gxw', 'Ladder JSON · PLC IR · SVG', 'CSV · TXT · レポート')))), lang)
    marks = (BLUE, TEAL, GREEN, BLUE, ORANGE)
    for i, (title, items) in enumerate(modules):
        mx = x0 + 12 + i * 222
        block(c, mx, ay + 36, 208, 100, title, marks[i])
        lines(c, mx + 12, ay + 82, items, 12.5, 20, MUTED, 188)
    strips = pick((('工作区：工程 · 版本 · 规格 · 产物 · 执行回执', '知识资源：指令事实库 · SQLite FTS5 · 工程模板'),
                   ('Workspace: projects · versions · specs · artifacts · receipts', 'Knowledge: instruction facts · SQLite FTS5 · templates'),
                   ('ワークスペース: プロジェクト · 版 · 仕様 · 成果物 · レシート', 'ナレッジ: 命令事実 · SQLite FTS5 · テンプレート')), lang)
    for i, label in enumerate(strips):
        sx = x0 + 12 + i * 554
        c.rect(sx, ay + 148, 542, 34, PANEL, LINE, 1)
        c.rect(sx, ay + 148, 4, 34, [SOFT, TEAL][i], None)
        c.text(sx + 16, ay + 169.5, label, 12.5, INK, maxw=515)

    # targets
    c.line(600, ay + 196, 600, bus2, TEAL, 1.4)
    c.line(x0, bus2, x0 + width, bus2, TEAL, 3)
    targets = pick((
        (('GX Works2 1.635.0.1', '编译 · 保存 · 冷重开 · 可见会话'), ('GX Simulator2', '类型化信号只读采样与回放'),
         ('PLC · MX Component', '操作员授权的只读监测'), (pick(('工程文件', '', ''), lang), '.gxw · CSV · TXT 交付')),
        (('GX Works2 1.635.0.1', 'Compile · save · reopen · live'), ('GX Simulator2', 'Read-only typed-signal traces'),
         ('PLC · MX Component', 'Operator-approved read-only'), ('Project files', '.gxw · CSV · TXT delivery')),
        (('GX Works2 1.635.0.1', 'コンパイル · 保存 · 再オープン'), ('GX Simulator2', '型付き信号の読み取り専用採取'),
         ('PLC · MX Component', '許可制の読み取り専用監視'), ('プロジェクトファイル', '.gxw · CSV · TXT 納品'))), lang)
    for i, (name, sub) in enumerate(targets):
        tx = x0 + i * 285
        c.rect(tx + 129.5, bus2 - 3, 6, 6, TEAL, None)
        c.line(tx + 132.5, bus2, tx + 132.5, 454, TEAL, 1.4)
        block(c, tx, 454, 265, 58, name, ORANGE if i < 3 else SOFT)
        c.text(tx + 12, 500, sub, 12.5, MUTED, maxw=245)
    c.tag(315, bus2, pick(('原生适配器 · 文件交付', 'Native adapters · file delivery', 'ネイティブアダプタ · ファイル納品'), lang), TEAL)
    return c


# ------------------------------------------------------------ four languages
def editor_st(c, x, y, w, h, lang):
    c.rect(x, y, 30, h, '#f0f2f4', None)
    comment = pick(('(* 输送线启停 *)', '(* Conveyor start/stop *)', '(* コンベア起動/停止 *)'), lang)
    code = (((comment, GREEN),),
            (('IF ', BLUE), ('bStart ', INK), ('AND NOT ', BLUE), ('bStop ', INK), ('THEN', BLUE)),
            (('    bRun := ', INK), ('TRUE', BLUE), (';', INK)),
            (('ELSIF ', BLUE), ('bStop ', INK), ('THEN', BLUE)),
            (('    bRun := ', INK), ('FALSE', BLUE), (';', INK)),
            (('END_IF', BLUE), (';', INK)),
            (('tonDelay(IN := bRun, PT := ', INK), ('T#500MS', ORANGE), (');', INK)))
    for i, segments in enumerate(code):
        ly = y + 24 + i * 23
        c.text(x + 22, ly, str(i + 1), 11, SOFT, 400, 'end', True)
        c.code(x + 40, ly, segments, 12)


def editor_fbd(c, x, y, w, h, lang):
    def var(vx, vy, label, out=False):
        c.rect(vx, vy - 10, 70, 20, '#ffffff', INK, 1.2)
        c.text(vx + 35, vy + 4, label, 11, INK, 400, 'middle', True)

    var(x + 14, y + 66, 'bRun')
    var(x + 14, y + 106, 'T#500MS')
    bx, by = x + 128, y + 40
    c.text(bx + 40, by - 8, 'tonDelay', 11, INK, 400, 'middle', True)
    c.rect(bx, by, 80, 92, '#ffffff', INK, 1.4)
    c.rect(bx, by, 80, 20, '#eef1f4', INK, 1.4)
    c.text(bx + 40, by + 14.5, 'TON', 12, INK, 700, 'middle', True)
    for label, py, side in (('IN', 66, 'l'), ('PT', 106, 'l'), ('Q', 66, 'r'), ('ET', 106, 'r')):
        if side == 'l':
            c.text(bx + 6, y + py + 4, label, 11, INK, 400, mono=True)
        else:
            c.text(bx + 74, y + py + 4, label, 11, INK, 400, 'end', True)
    c.line(x + 84, y + 66, bx, y + 66, INK, 1.2)
    c.line(x + 84, y + 106, bx, y + 106, INK, 1.2)
    c.line(bx + 80, y + 66, bx + 116, y + 66, INK, 1.2)
    var(bx + 116, y + 66, 'bEnable')
    c.line(bx + 80, y + 106, bx + 100, y + 106, INK, 1.2)
    c.rect(bx + 112, y + 54, 74, 24, 'none', BLUE, 1.2)
    c.text(x + 14, y + h - 14, pick(('网络 1 / 3', 'Network 1 / 3', 'ネットワーク 1 / 3'), lang), 11, SOFT)


def editor_sfc(c, x, y, w, h, lang):
    cx = x + 90

    def step(sy, label, action, initial=False):
        c.rect(cx - 24, sy - 12, 48, 24, '#ffffff', INK, 1.4)
        if initial:
            c.rect(cx - 20, sy - 8, 40, 16, 'none', INK, 1)
        c.text(cx, sy + 4, label, 11.5, INK, 600, 'middle', True)
        c.line(cx + 24, sy, cx + 44, sy, INK, 1.2)
        c.rect(cx + 44, sy - 11, 96, 22, '#ffffff', INK, 1.2)
        c.text(cx + 92, sy + 4, action, 11, INK, 400, 'middle', True)

    def transition(ty, label):
        c.line(cx - 10, ty, cx + 10, ty, INK, 2.4)
        c.text(cx - 16, ty + 4, label, 11, MUTED, 400, 'end', True)

    s0, s1, s2 = y + 30, y + 96, y + 162
    c.line(cx, s0 + 12, cx, s1 - 12, INK, 1.2)
    c.line(cx, s1 + 12, cx, s2 - 12, INK, 1.2)
    step(s0, '0', 'Init', True)
    transition((s0 + s1) / 2, 'TR0')
    step(s1, '10', 'Feed')
    c.rect(cx + 34, s1 - 21, 116, 42, 'none', BLUE, 1.2)
    transition((s1 + s2) / 2, 'TR1')
    step(s2, '20', 'Stop')
    c.line(cx, s2 + 12, cx, s2 + 24, INK, 1.2)
    c.path(f'M{cx - 5} {s2 + 20} L{cx} {s2 + 28} L{cx + 5} {s2 + 20}', INK, 1.2)
    c.text(cx + 10, s2 + 30, '0', 11, INK, 400, mono=True)


def editor_ladder(c, x, y, w, h, lang):
    ladder(c, x, y, w, h)


def feature_languages(lang):
    c = Canvas(lang, 1200, 590, 'languages')
    frame(c, *pick((('Ladder · ST · FBD · SFC 编辑能力', '四种语言共用离线事务：读取 → 编辑 → 预览差异 → 保存版本；可在发现 GX Works2 后自动原生编译'),
                    ('Ladder · ST · FBD · SFC editing', 'One offline transaction for all four languages: read → edit → preview diff → save; native compile when GX Works2 is found'),
                    ('Ladder · ST · FBD · SFC の編集機能', '4 言語共通のオフライン処理: 読み取り → 編集 → 差分確認 → 版保存。GX Works2 があれば自動コンパイル')), lang))
    panes = (
        ('MAIN [PRG]', pick(('梯形图', 'Ladder Diagram', 'ラダー図'), lang), editor_ladder,
         pick((('指令事实与手册依据', '结构与操作数检查', '按网络局部补丁', 'SVG · MAIN / 注释 CSV'),
               ('Instruction facts & sources', 'Structure & operand checks', 'Per-network patches', 'SVG · MAIN / comment CSV'),
               ('命令の事実と根拠', '構造・オペランド検査', 'ネットワーク単位の修正', 'SVG · MAIN / コメント CSV')), lang)),
        ('Conveyor [PRG]', pick(('结构化文本', 'Structured Text', '構造化テキスト'), lang), editor_st,
         pick((('Program · FB · FUNCTION', '正文与声明联合编辑', '接口与返回类型调整', '本地语法与语义检查'),
               ('Program · FB · FUNCTION', 'Body & declaration edits', 'Interfaces & return types', 'Local syntax checks'),
               ('Program · FB · FUNCTION', '本文と宣言の同時編集', 'インタフェース・戻り値型', 'ローカル構文・意味検査')), lang)),
        ('Axis1 [FB]', pick(('功能块图', 'Function Block Diagram', 'ファンクションブロック図'), lang), editor_fbd,
         pick((('参数级 calls / arguments', '节点、端子与正交连线', 'ST 功能块模块化组装', '库导入与任务注册'),
               ('Parameter-level calls', 'Nodes, pins & wiring', 'Modular ST function blocks', 'Library import & tasks'),
               ('パラメータ単位の calls', 'ノード・端子・配線', 'ST FB によるモジュール化', 'ライブラリ取込・タスク登録')), lang)),
        ('Sequence [SFC]', pick(('顺序功能图', 'Sequential Function Chart', 'シーケンシャルファンクションチャート'), lang), editor_sfc,
         pick((('步骤、转换与动作', '分支、汇合与跳转', '子程序与内部注册', '声明与注释联合编辑'),
               ('Steps, transitions, actions', 'Branches, joins & jumps', 'Subroutines & registration', 'Declarations & comments'),
               ('ステップ・遷移・アクション', '分岐・合流・ジャンプ', 'サブルーチンと内部登録', '宣言とコメントの編集')), lang)),
    )
    heading = pick(('支持的操作', 'Supported operations', '対応する操作'), lang)
    for i, (tab, kind, draw, items) in enumerate(panes):
        x = 30 + (i % 2) * 580
        y = 88 + (i // 2) * 248
        w, h = 560, 232
        c.rect(x, y, w, h, '#ffffff', LINE)
        c.rect(x, y, w, 26, HEAD, LINE)
        tab_w = est_width(tab, 11.5, True) + 22
        c.rect(x + 6, y + 4, tab_w, 22, '#ffffff', LIGHT)
        c.line(x + 7, y + 26, x + 5 + tab_w, y + 26, '#ffffff', 1.5)
        c.text(x + 17, y + 19, tab, 11.5, INK, 600, mono=True)
        c.text(x + w - 10, y + 18, kind, 12, MUTED, 600, 'end', maxw=w - tab_w - 30)
        ew = 320
        draw(c, x + 1, y + 27, ew, h - 28, lang)
        c.rect(x + ew + 1, y + 27, w - ew - 2, h - 28, PANEL, None)
        c.line(x + ew + 1, y + 27, x + ew + 1, y + h, LIGHT)
        c.text(x + ew + 18, y + 54, heading, 12, MUTED, 600, maxw=200)
        for j, item in enumerate(items):
            iy = y + 88 + j * 32
            c.check(x + ew + 18, iy - 3)
            c.text(x + ew + 36, iy, item, 13, INK, maxw=w - ew - 46)
    return c


# ------------------------------------------------------------------ workflow
def feature_workflow(lang):
    c = Canvas(lang, 1200, 500, 'workflow')
    frame(c, *pick((('工程流程：离线编辑到原生交付', '读取、编辑、审阅与保存无需 GX Works2；编译、保存与冷重开在隔离后台工作区执行，不写入 PLC'),
                    ('Workflow: offline editing to native delivery', 'Read, edit, review and save without GX Works2; compile, save and cold-reopen in an isolated workspace with no PLC writes'),
                    ('ワークフロー: オフライン編集からネイティブ納品まで', '読み取り・編集・レビュー・保存は GX Works2 不要。コンパイル・保存・再オープンは隔離環境で実行し、PLC には書き込みません')), lang))
    groups = ((30, 740, BLUE, pick(('离线 · 无需安装或启动 GX Works2', 'Offline · no GX Works2 required', 'オフライン · GX Works2 不要'), lang)),
              (790, 380, TEAL, pick(('原生 · 隔离后台工作区', 'Native · isolated background', 'ネイティブ · 隔離バックグラウンド'), lang)))
    for gx, gw, color, label in groups:
        c.rect(gx, 90, gw, 172, '#fbfcfd', color, 1, 0, '5 4')
        c.rect(gx, 90, est_width(label, 12) + 20, 22, color, None)
        c.text(gx + 10, 105.5, label, 12, '#ffffff', 600)
    steps = pick((
        (('检查', 'inspect_gxw_project', 'CPU、程序目录与格式能力'), ('导入', 'import_gxw_project', '进入工程与版本流程'),
         ('编辑', 'edit_gxw_project', '源码、声明与注释联合提交'), ('保存版本', 'save_policy', 'draft / checked 预览差异'),
         ('原生编译', 'compile', '编译器检查当前副本'), ('保存与重开', 'save · reopen', '冷重开核对后交付')),
        (('Inspect', 'inspect_gxw_project', 'CPU, programs, capability'), ('Import', 'import_gxw_project', 'Enter project versioning'),
         ('Edit', 'edit_gxw_project', 'Source, labels, comments'), ('Save version', 'save_policy', 'draft / checked + diff'),
         ('Native compile', 'compile', 'Compiler checks the copy'), ('Save & reopen', 'save · reopen', 'Cold reopen, then deliver')),
        (('検査', 'inspect_gxw_project', 'CPU・プログラム・対応'), ('インポート', 'import_gxw_project', 'プロジェクトと版管理へ'),
         ('編集', 'edit_gxw_project', 'ソース・宣言・コメント'), ('版の保存', 'save_policy', 'draft / checked と差分'),
         ('コンパイル', 'compile', 'コピーをコンパイラで検査'), ('保存と再オープン', 'save · reopen', '再オープン確認後に納品'))), lang)
    xs = [40, 222, 404, 586, 806, 988]
    widths = [168, 168, 168, 168, 172, 172]
    for i, (title, tool, desc) in enumerate(steps):
        x, w = xs[i], widths[i]
        color = TEAL if i >= 4 else BLUE
        block(c, x, 128, w, 108, f'{i + 1}  {title}', color)
        c.text(x + 12, 176, tool, 12, color, 600, mono=True, maxw=w - 20)
        for k, text in enumerate(wrap(desc, 12, w - 24)[:2]):
            c.text(x + 12, 204 + k * 16, text, 12, MUTED, maxw=w - 20)
        if i < 5:
            c.wire([(x + w, 182), (xs[i + 1], 182)], WIRE, 1.4)
    c.text(806, 250, 'execute_native_engineering  plan.mode="background"', 11, TEAL, 400, mono=True, maxw=352)

    # output window
    oy = 282
    c.rect(30, oy, 1140, 190, '#ffffff', LINE)
    c.rect(30, oy, 1140, 26, HEAD, LINE)
    c.rect(36, oy + 4, 92, 22, '#ffffff', LIGHT)
    c.line(37, oy + 26, 127, oy + 26, '#ffffff', 1.5)
    c.text(82, oy + 19, pick(('输出', 'Output', '出力'), lang), 12, INK, 600, 'middle')
    c.text(1160, oy + 18, pick(('示例输出', 'Example output', '出力例'), lang), 11.5, SOFT, 400, 'end')
    log = (
        (('[1] ', SOFT), ('inspect_gxw_project        ', BLUE), ('cpu=FX3U  programs=[MAIN]  language=Ladder', INK)),
        (('[2] ', SOFT), ('import_gxw_project         ', BLUE), ('version_id=v1', INK)),
        (('[3] ', SOFT), ('edit_gxw_project           ', BLUE), ('save_policy=draft  diff: MAIN +3 / -1', INK)),
        (('[4] ', SOFT), ('save                       ', BLUE), ('version_id=v2  core_diagnostics=0', INK)),
        (('[5] ', SOFT), ('execute_native_engineering ', TEAL), ('compile  ', INK), ('compilation.acceptance=accepted', GREEN)),
        (('[6] ', SOFT), ('                           ', TEAL), ('save -> reopen  ', INK), ('delivered version_id=v3', GREEN)),
    )
    for i, segments in enumerate(log):
        c.code(48, oy + 52 + i * 21, segments, 12.5)
    c.text(48, oy + 180, pick(('编译、代码检查、保存与 PLC 运行是各自独立的结果，均绑定 version_id 返回',
                               'Compilation, code checks, saving and PLC runs are separate results, each bound to a version_id',
                               'コンパイル・コード検査・保存・PLC 実行はそれぞれ独立した結果として version_id に紐付けて返却'), lang),
           12, MUTED, maxw=1100)
    return c


# ----------------------------------------------------------------------- RAG
def feature_rag(lang):
    c = Canvas(lang, 1200, 500, 'rag')
    frame(c, *pick((('CPU 作用域工程 RAG', '多路检索按 CPU 隔离并局部融合；检索结果先投影为紧凑上下文，生成的程序再经 Core 规则校验'),
                    ('CPU-scoped engineering RAG', 'Multi-route retrieval isolated per CPU and fused per domain; compact projections feed the model, Core rules check the output'),
                    ('CPU 単位のエンジニアリング RAG', 'CPU ごとに分離した多経路検索とドメイン内融合。コンパクトな投影をモデルへ渡し、出力は Core 規則で検証')), lang))
    block(c, 30, 96, 170, 196, pick(('查询', 'Query', 'クエリ'), lang), ORANGE)
    lines(c, 44, 146, pick((('工程问题', '所选 CPU', 'FX / Q / L', '程序类型'), ('Engineering question', 'Selected CPU', 'FX / Q / L', 'Program type'),
                            ('エンジニアリング質問', '選択した CPU', 'FX / Q / L', 'プログラム種別')), lang), 13, 30, INK, 145)
    routes = pick(((('实体路由', '指令 / 软元件精确匹配'), ('BM25 全文检索', 'SQLite FTS5'), ('Dense / LSA 向量', '分词覆盖门控')),
                   (('Entity routing', 'Exact instruction / device'), ('BM25 full-text', 'SQLite FTS5'), ('Dense / LSA vectors', 'Token-coverage gating')),
                   (('実体ルーティング', '命令・デバイスの完全一致'), ('BM25 全文検索', 'SQLite FTS5'), ('Dense / LSA ベクトル', 'トークン被覆ゲート'))), lang)
    for i, (title, sub) in enumerate(routes):
        ry = 96 + i * 70
        block(c, 250, ry, 230, 56, title, TEAL)
        c.text(262, ry + 44, sub, 12.5, MUTED, mono=(i == 1), maxw=210)
        c.wire([(200, 194), (225, 194), (225, ry + 28), (250, ry + 28)], WIRE, 1.3)
        c.wire([(480, ry + 28), (530, ry + 28)], WIRE, 1.3)
    block(c, 530, 96, 190, 196, pick(('域内局部 RRF', 'Domain-local RRF', 'ドメイン内 RRF'), lang), BLUE)
    c.text(544, 150, 'k = 60.0', 16, INK, 600, mono=True)
    lines(c, 544, 182, pick((('域命名空间隔离', '实体精确匹配优先', '域内相对得分', '确定性 ID 仲裁'),
                             ('Domain namespaces', 'Exact entity first', 'Domain-relative score', 'Deterministic tie-break'),
                             ('ドメイン名前空間', '完全一致を優先', 'ドメイン内の相対スコア', '決定的な ID 調停')), lang), 12.5, 26, MUTED, 170)
    block(c, 770, 96, 180, 84, pick(('技术投影', 'Technical projection', '技術プロジェクション'), lang), BLUE)
    c.text(782, 140, 'model_context', 12, BLUE, 600, mono=True)
    c.text(782, 164, pick(('引脚规格 · 时序约束', 'Pins · timing limits', 'ピン仕様 · タイミング'), lang), 12.5, MUTED, maxw=160)
    block(c, 990, 96, 180, 84, pick(('客户端模型', 'Client model', 'クライアントモデル'), lang), SOFT)
    c.text(1002, 140, pick(('规划与生成候选', 'Plans and drafts', '計画と候補生成'), lang), 12.5, MUTED, maxw=160)
    c.text(1002, 164, pick(('MCP / Direct', 'MCP / Direct', 'MCP / Direct'), lang), 12, MUTED, mono=True)
    block(c, 770, 208, 180, 84, pick(('Core 规则校验', 'Core validation', 'Core 検証'), lang), GREEN)
    c.text(782, 252, 'instruction_constraints', 11.5, GREEN, 600, mono=True, maxw=160)
    c.text(782, 276, pick(('CPU 白名单 · 位宽 · 互斥', 'CPU list · bit width', 'CPU 可否 · ビット幅'), lang), 12.5, MUTED, maxw=160)
    block(c, 990, 208, 180, 84, pick(('版本化产物', 'Versioned artifacts', '版付き成果物'), lang), ORANGE)
    c.text(1002, 252, pick(('诊断 · 候选 · 版本', 'Diagnostics · versions', '診断 · 候補 · 版'), lang), 12.5, MUTED, maxw=160)
    c.text(1002, 276, 'program.gxw', 12, MUTED, mono=True)
    c.wire([(720, 138), (770, 138)], WIRE, 1.3)
    c.wire([(950, 138), (990, 138)], WIRE, 1.3)
    c.wire([(1080, 180), (1080, 194), (860, 194), (860, 208)], WIRE, 1.3)
    c.wire([(950, 250), (990, 250)], WIRE, 1.3)

    sy = 330
    block(c, 30, sy, 1140, 146, pick(('知识存储（发布包不含厂商手册 PDF 原文及原始索引）', 'Knowledge storage (no vendor manual PDFs or raw indexes are shipped)',
                                      'ナレッジストア（ベンダーマニュアル PDF と元インデックスは同梱しません）'), lang), SOFT)
    stores = pick((
        (('文本池', 'exact_search_storage', ('SHA-256 去重池化，按型号保留独立行', 'FTS5 视图挂接，BM25 统计与未压缩一致')),
         ('事实库', 'OwnerDefinitionStore', ('owner-field-row-v3 · fact_field_codec', '启动只读身份头，命中后 hydrate_owner')),
         ('检索器', 'knowledge.retriever', ('实体 · BM25 · Dense/LSA 统一入口', '跨库检索使用域命名空间'))),
        (('Text pool', 'exact_search_storage', ('SHA-256 dedup, per-model binding rows', 'FTS5 views keep BM25 statistics identical')),
         ('Fact store', 'OwnerDefinitionStore', ('owner-field-row-v3 · fact_field_codec', 'Identity headers first, hydrate on hit')),
         ('Retriever', 'knowledge.retriever', ('One entry: entity · BM25 · Dense/LSA', 'Domain namespaces across stores'))),
        (('テキストプール', 'exact_search_storage', ('SHA-256 重複排除、型式別の行を保持', 'FTS5 ビューで BM25 統計を維持')),
         ('事実ストア', 'OwnerDefinitionStore', ('owner-field-row-v3 · fact_field_codec', '起動時は識別ヘッダのみ、命中時に展開')),
         ('リトリーバ', 'knowledge.retriever', ('実体 · BM25 · Dense/LSA の統一入口', 'ストア横断はドメイン名前空間で分離')))), lang)
    for i, (title, module, items) in enumerate(stores):
        bx = 46 + i * 374
        c.rect(bx, sy + 38, 358, 94, PANEL, LIGHT)
        c.text(bx + 12, sy + 60, title, 13, INK, 600)
        c.text(bx + 346, sy + 60, module, 11.5, BLUE, 400, 'end', True)
        lines(c, bx + 12, sy + 86, items, 12.5, 22, MUTED, 334)
    c.wire([(365, sy), (365, 292)], WIRE, 1.3)
    return c


# ------------------------------------------------------------ function table
def feature_functions(lang):
    rows = pick((
        (('工程与版本管理', '按 CPU 与程序类型建工程；候选审阅、版本差异与产物导出', ('list_projects',)),
         ('离线 GXW 编辑', '无需 GX Works2 读取、联合编辑并保存已有 .gxw', ('inspect_gxw_project · import_gxw_project', 'edit_gxw_project')),
         ('生成上下文与检索', '按工程 CPU 检索指令事实、手册依据与原创工程范例', ('get_generation_context · search_plc_manual',)),
         ('运行中 GX 只读快照', '读取本机已打开 GX Works2 的程序快照并按网络检查', ('list_gxworks2_sessions · read_gxworks2_snapshot',)),
         ('后台原生编译', '隔离工作区编译、保存、冷重开并交付同语言新版本', ('execute_native_engineering', 'plan.mode="background"')),
         ('持续原生会话', '同一 GX Works2 进程内多步编辑、编译与分配地址查询', ('execute_native_engineering', 'mode="live"')),
         ('源码级诊断', '诊断与步号定位到 ST 行、声明项、FBD 网络或调用实例', ('locate_native_code_steps', 'read_native_device_assignments')),
         ('仿真与只读监测', 'GX Simulator2 只读采样与波形回放；MX Component 授权只读监测', ('connect_simulator · start_simulator_trace',))),
        (('Projects & versions', 'Create by CPU and program type; review candidates, diff and export', ('list_projects',)),
         ('Offline GXW editing', 'Read, jointly edit and save existing .gxw files without GX Works2', ('inspect_gxw_project · import_gxw_project', 'edit_gxw_project')),
         ('Context & retrieval', 'CPU-scoped instruction facts, manual evidence and original examples', ('get_generation_context · search_plc_manual',)),
         ('Live GX snapshot', 'Read programs from open GX Works2 windows and check per network', ('list_gxworks2_sessions · read_gxworks2_snapshot',)),
         ('Background native build', 'Compile, save and cold-reopen in an isolated workspace', ('execute_native_engineering', 'plan.mode="background"')),
         ('Live native session', 'Multi-step edits, compile and address lookup in one GX Works2', ('execute_native_engineering', 'mode="live"')),
         ('Source-level diagnostics', 'Map diagnostics and steps to ST lines, labels and FBD networks', ('locate_native_code_steps', 'read_native_device_assignments')),
         ('Simulation & monitoring', 'GX Simulator2 traces with replay; approved MX Component reads', ('connect_simulator · start_simulator_trace',))),
        (('プロジェクトと版管理', 'CPU とプログラム種別で作成、候補レビュー・版差分・成果物出力', ('list_projects',)),
         ('オフライン GXW 編集', 'GX Works2 なしで既存 .gxw を読み取り・一括編集・保存', ('inspect_gxw_project · import_gxw_project', 'edit_gxw_project')),
         ('生成コンテキストと検索', 'CPU に応じた命令事実・マニュアル根拠・オリジナル例を検索', ('get_generation_context · search_plc_manual',)),
         ('起動中 GX のスナップショット', '開いている GX Works2 のプログラムを読み取り、ネットワーク単位で検査', ('list_gxworks2_sessions · read_gxworks2_snapshot',)),
         ('バックグラウンド処理', '隔離ワークスペースでコンパイル・保存・再オープンし新しい版を納品', ('execute_native_engineering', 'plan.mode="background"')),
         ('継続ネイティブセッション', '同一 GX Works2 プロセスで多段編集・コンパイル・アドレス照会', ('execute_native_engineering', 'mode="live"')),
         ('ソースレベル診断', '診断とステップ番号を ST 行・宣言・FBD ネットワークへ対応付け', ('locate_native_code_steps', 'read_native_device_assignments')),
         ('シミュレーションと監視', 'GX Simulator2 の採取と波形再生、MX Component の許可制監視', ('connect_simulator · start_simulator_trace',)))), lang)
    row_h, top = 44, 120
    c = Canvas(lang, 1200, top + row_h * len(rows) + 26, 'functions')
    frame(c, *pick((('功能一览', '主要功能模块及其对应的 MCP 工具；GX 导入与原生操作遵循操作员配置的执行许可'),
                    ('Function overview', 'Main function modules and their MCP tools; GX import and native operations follow operator-configured approval'),
                    ('機能一覧', '主な機能モジュールと対応する MCP ツール。GX 取込とネイティブ操作はオペレータ設定の実行許可に従います')), lang))
    cols = ((30, 230), (260, 520), (780, 390))
    headers = pick((('功能模块', '说明', 'MCP 工具'), ('Module', 'Description', 'MCP tools'), ('機能モジュール', '説明', 'MCP ツール')), lang)
    c.rect(30, 88, 1140, 32, HEAD, LINE)
    for (cx, cw), label in zip(cols, headers):
        c.text(cx + 12, 109, label, 13, INK, 600)
        if cx > 30:
            c.line(cx, 88, cx, top + row_h * len(rows), LIGHT)
    for i, (name, desc, tools) in enumerate(rows):
        ry = top + i * row_h
        c.rect(30, ry, 1140, row_h, '#f8f9fa' if i % 2 else '#ffffff', None)
        c.line(30, ry + row_h, 1170, ry + row_h, LIGHT)
        c.rect(30, ry, 3, row_h, (BLUE, BLUE, TEAL, TEAL, ORANGE, ORANGE, GREEN, GREEN)[i], None)
        c.text(42, ry + 27, name, 13, INK, 600, maxw=210)
        c.text(272, ry + 27, desc, 13, MUTED, maxw=500)
        for j, tool in enumerate(tools):
            ty = ry + (27 if len(tools) == 1 else 19 + j * 17)
            c.text(792, ty, tool, 12, BLUE, 400, mono=True, maxw=370)
    for (cx, cw) in cols[1:]:
        c.line(cx, 88, cx, top + row_h * len(rows), LIGHT)
    c.rect(30, 88, 1140, 32 + row_h * len(rows), 'none', LINE)
    return c


FIGURES = {
    'banner': banner,
    'feature-architecture': feature_architecture,
    'feature-languages': feature_languages,
    'feature-workflow': feature_workflow,
    'feature-rag': feature_rag,
    'feature-functions': feature_functions,
}


def find_browser():
    for env_name in ('GXWORKS_README_BROWSER', 'BROWSER'):
        path = os.environ.get(env_name)
        if path and Path(path).is_file():
            return path
    for name in ('msedge', 'chrome', 'google-chrome', 'chromium'):
        found = shutil.which(name)
        if found:
            return found
    for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'), os.environ.get('LOCALAPPDATA')):
        if not base:
            continue
        for relative in ('Microsoft/Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe'):
            candidate = Path(base) / relative
            if candidate.is_file():
                return str(candidate)
    raise SystemExit('No Chromium-based browser found; set GXWORKS_README_BROWSER to its executable.')


def rasterise(browser, svg_path, png_path, width, height, scale):
    profile = tempfile.mkdtemp(prefix='readme-assets-')
    try:
        subprocess.run([browser, '--headless=new', '--disable-gpu', '--hide-scrollbars',
                        f'--user-data-dir={profile}', f'--force-device-scale-factor={scale}',
                        f'--window-size={width},{height}', f'--screenshot={png_path}', svg_path.as_uri()],
                       check=True, capture_output=True, timeout=120)
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def shrink(png_path):
    """Reduce the PNG to an adaptive palette when Pillow is available."""
    try:
        from PIL import Image
    except ImportError:
        return
    with Image.open(png_path) as source:
        image = source.convert('RGB')
    image.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(png_path, optimize=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--scale', type=float, default=2.0, help='device scale factor (default: 2)')
    parser.add_argument('--no-shrink', action='store_true', help='skip palette reduction of the PNG files')
    parser.add_argument('--keep-svg', type=Path, help='also write the SVG sources into this folder')
    parser.add_argument('--only', nargs='*', choices=sorted(FIGURES), help='render only these figures')
    parser.add_argument('--lang', nargs='*', choices=LANGS, default=list(LANGS))
    args = parser.parse_args(argv)
    browser = find_browser()
    ASSETS.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='readme-svg-'))
    try:
        for name in (args.only or FIGURES):
            for lang in args.lang:
                canvas = FIGURES[name](lang)
                stem = name + SUFFIX[lang]
                svg_path = work / (stem + '.svg')
                svg_path.write_text(canvas.render(), encoding='utf-8')
                if args.keep_svg:
                    args.keep_svg.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(svg_path, args.keep_svg / svg_path.name)
                png_path = ASSETS / (stem + '.png')
                rasterise(browser, svg_path, png_path, canvas.w, canvas.h, args.scale)
                if not args.no_shrink:
                    shrink(png_path)
                print('wrote', png_path.relative_to(ROOT))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for warning in WARNINGS:
        print('WARNING:', warning, file=sys.stderr)


if __name__ == '__main__':
    main()
