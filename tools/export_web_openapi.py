"""Export the Web app's OpenAPI document into web/openapi.json.

The document is produced by the FastAPI app itself and must never be edited by
hand: web/src/api/generated.ts is derived from it, so both files come out of one
export. After changing any request/response model, run

    python tools/export_web_openapi.py
    npm --prefix web run types

and commit web/openapi.json together with web/src/api/generated.ts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "web" / "openapi.json"

if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def render(document) -> str:
    """Serialize a schema exactly like the committed document.

    Sorted, two-space indented, UTF-8 and newline-terminated, so the CI drift
    check stays a plain ``git diff --exit-code``.
    """

    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def export(destination: Path) -> int:
    from integrations.web.app import create_app

    with tempfile.TemporaryDirectory(prefix="openapi-export-") as workspace:
        app = create_app(workspace, operator_token="schema-export", agent_token="schema-agent")
        document = app.openapi()
    text = render(document)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8", newline="")
    print(f"OpenAPI document written to {destination} ({len(text)} characters)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="destination file (defaults to web/openapi.json)",
    )
    return export(parser.parse_args().out)


if __name__ == "__main__":
    raise SystemExit(main())
