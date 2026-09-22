"""Stage public documentation and inert source references for a release directory.

This module copies documentation only. It does not build, start or configure the
application. Original evidence and license bytes are retained; source excerpts
are copied as .txt so they cannot become importable application modules.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

LINK = re.compile(r"(?<!!)\[([^\]\n]*)\]\(([^\s)]+)\)")
PUBLIC_READMES = (
    "README.md", "README.zh-CN.md", "AGENTS.md", "requirements/README.md",
    "resources/knowledge/README.md", "resources/knowledge/THIRD_PARTY_NOTICES.md",
    "simulator_gateway/README.md", "hardware_reader/README.md", "research/README.md",
    "tests/README.md", "evals/context-replay/README.md",
)


def stage_documentation(root: Path, destination: Path) -> dict:
    """Copy the source version's documents, references and SHA-256 manifest."""
    root, destination = root.resolve(), destination.resolve()
    if root == destination or root.is_relative_to(destination):
        raise ValueError("Use a separate package destination, not the source root or its parent")
    destination.mkdir(parents=True, exist_ok=True)
    docs = {root / name for name in PUBLIC_READMES if (root / name).is_file()}
    docs.update((root / "docs").rglob("*.md"))
    outputs = {p: Path(p.relative_to(root)) for p in docs}
    records = {}
    queued = []

    def source_path(path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"Documentation link leaves the source tree: {path}")
        if not resolved.exists():
            raise FileNotFoundError(f"Documentation source is missing: {path}")
        return resolved

    def record(source: Path, out: Path, data: bytes, kind: str) -> None:
        target = destination / out
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        original = source.read_bytes() if source.is_file() else None
        records[out.as_posix()] = {
            "source": source.relative_to(root).as_posix(), "kind": kind,
            "source_sha256": hashlib.sha256(original).hexdigest() if original is not None else None,
            "output_sha256": hashlib.sha256(data).hexdigest(),
        }

    def reference(source: Path) -> Path:
        if source in outputs:
            return outputs[source]
        rel = source.relative_to(root)
        if source.is_dir():
            out = Path("docs/source") / rel / "index.md"
            listing = "# Source directory\n\n`" + rel.as_posix() + "`\n\n"
            listing += "\n".join("- `" + p.name + ("/" if p.is_dir() else "") + "`"
                                 for p in sorted(source.iterdir()) if not p.name.startswith(".")) + "\n"
            record(source, out, listing.encode("utf-8"), "directory-index")
        else:
            if source.suffix.lower() in {".ttf", ".otf", ".woff", ".woff2"}:
                raise ValueError("Font distribution is not part of the documentation stage")
            data = source.read_bytes()
            try:
                data.decode("utf-8")
                out = Path("docs/source") / (rel.as_posix() + ".txt")
            except UnicodeDecodeError:
                out = Path("docs/source") / rel
            record(source, out, data, "source-reference")
        outputs[source] = out
        return out

    def render(source: Path, out: Path) -> bytes:
        text = source.read_text(encoding="utf-8")
        # License and third-party attribution are verbatim distribution sources.
        if "THIRD_PARTY" in source.name or "LICENSE" in source.name:
            return source.read_bytes()
        def replace(match):
            label, url = match.groups()
            parsed = urlsplit(url)
            if parsed.scheme or parsed.netloc or not parsed.path:
                return match.group(0)
            linked = source_path(source.parent / unquote(parsed.path))
            target = reference(linked)
            relative = Path(os.path.relpath(target, out.parent)).as_posix()
            if parsed.fragment:
                relative += "#" + parsed.fragment
            return "[" + label + "](" + relative + ")"
        # Fenced examples remain literal. Rewrite only Markdown links outside them.
        parts = re.split(r"(```.*?```|~~~.*?~~~)", text, flags=re.S)
        return "".join(part if i % 2 else LINK.sub(replace, part)
                       for i, part in enumerate(parts)).encode("utf-8")

    for source in sorted(docs):
        out = outputs[source]
        record(source, out, render(source, out), "document")
    # Existing package location remains usable, with links relative to its location.
    source = root / "simulator_gateway/README.md"
    if source in docs:
        alias = Path("docs/simulator_gateway/README.md")
        record(source, alias, render(source, alias), "distribution-alias")
    license_file = root / "LICENSE"
    if license_file.is_file():
        record(license_file, Path("LICENSE"), license_file.read_bytes(), "license")
    manifest = {"schema_version": 1, "files": dict(sorted(records.items()))}
    (destination / "documentation-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = stage_documentation(args.root, args.destination)
    print(f"Documentation manifest: {args.destination / 'documentation-manifest.json'}")
    print(f"Files: {len(manifest['files'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
