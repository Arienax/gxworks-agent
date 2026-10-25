"""The OpenAPI document is generated, so the generator itself needs a guard.

web/openapi.json drives web/src/api/generated.ts.  If the export stops matching
what is committed, the contract silently drifts; CI checks the same thing with
`git diff --exit-code`, and these cases make the failure reproducible locally.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

COMMITTED = ROOT / "web" / "openapi.json"


def test_the_render_is_sorted_indented_and_newline_terminated():
    from tools.export_web_openapi import render

    text = render({"b": 1, "a": {"d": 2, "c": "中文"}})

    assert text.endswith("\n") and not text.endswith("\n\n")
    assert text.index('"a"') < text.index('"b"'), "键必须排序，否则 diff 会漂"
    assert '\n  "a"' in text, "缩进必须是两个空格"
    assert "中文" in text and "\\u4e2d" not in text, "不能转义非 ASCII"


def test_the_committed_document_matches_the_app():
    import pytest

    pytest.importorskip("fastapi", reason="导出需要 Web 依赖，缺失时应显式跳过")
    from tools.export_web_openapi import export

    with tempfile.TemporaryDirectory() as directory:
        produced = Path(directory) / "openapi.json"
        assert export(produced) == 0
        assert produced.read_bytes() == COMMITTED.read_bytes(), (
            "web/openapi.json 与后端不一致：请运行 python tools/export_web_openapi.py "
            "和 npm --prefix web run types 后提交两个文件"
        )
