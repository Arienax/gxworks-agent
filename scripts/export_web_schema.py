# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Export the actual local HTTP contract without starting a backend or workspace."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "web" / "openapi.json"


def build_schema():
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    from integrations.web.app import create_app
    from integrations.web.responses import document_sse_events

    # These are explicit placeholder paths, never the operator's configured
    # workspace. Constructing a read-only app performs no mkdir or migration.
    app = create_app(
        ROOT / ".schema-only-workspace", state_dir=ROOT / ".schema-only-state",
        read_only=True, operator_token="schema-export-never-started",
    )
    return document_sse_events(app.openapi())


def export_schema(output=DEFAULT_OUTPUT):
    schema = build_schema()
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8", newline="\n")
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args(argv)
    print(export_schema(arguments.output))


if __name__ == "__main__":
    main()
