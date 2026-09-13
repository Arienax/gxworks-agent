#!/usr/bin/env python3
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "src" / "knowledge_retriever_core.py"
text = path.read_text(encoding="utf-8")
replacements = {
    'return "\n".join(lines) if len(lines) > 2 else ""': 'return "\\n".join(lines) if len(lines) > 2 else ""',
    'body.partition("\n\n")': 'body.partition("\\n\\n")',
    'prefix + ("\n\n" + body if body else "")': 'prefix + ("\\n\\n" + body if body else "")',
}
for old, new in replacements.items():
    if old not in text:
        raise SystemExit(f"generated retriever escape target missing: {old!r}")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
