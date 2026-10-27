#!/usr/bin/env python3
# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Import the pinned gxw2-skill Markdown corpus into the schema-v3 RAG database.

The importer is intentionally separate from the PDF builder. Run the PDF build
first, then this importer, then rebuild dense embeddings. The source is treated
as a lower-priority third-party supporting corpus; official Mitsubishi manuals
remain the authoritative structured evidence.
"""

from __future__ import annotations

import sys as _private_sys
from pathlib import Path as _PrivatePath
_private_sys.path.insert(0, str(_PrivatePath(__file__).resolve().parents[1]))
from tools.offline_inputs import private_path

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Iterable, Iterator
import unicodedata
import urllib.request
import zipfile


SCHEMA_VERSION = 3
TASK_TYPES = "*"
DEFAULT_TARGET_CHARS = 4800
DEFAULT_MAX_CHARS = 7600

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
DEVICE_RE = re.compile(r"(?<![A-Z0-9_])(ER|SM|SD|TS|TC|CS|CC|[XYMSTCDRVZPIN])\s*(\d+)(?:\.(\d+))?(?![A-Z0-9_])", re.I)
CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")


@dataclass(frozen=True)
class SourceSpec:
    id: str
    repository: str
    commit: str
    version: str
    title: str
    language: str
    plc_models: str
    priority: int
    license: str
    homepage: str
    archive_url: str
    root: str
    published: str


@dataclass(frozen=True)
class SourceDocument:
    relative_path: str
    text: str
    sha256: str
    size: int
    chunk_type: str
    instruction_opcode: str = ""


@dataclass(frozen=True)
class ChunkDraft:
    source_file: str
    chapter: str
    section: str
    outline_path: str
    section_key: str
    chunk_type: str
    instruction_opcode: str
    text: str
    block_index: int
    block_count: int
    source_sha256: str
    source_size: int


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=private_path('resources/knowledge/fx3u_knowledge.sqlite'),
    )
    parser.add_argument(
        "--source-config",
        type=Path,
        default=private_path('resources/knowledge/external_sources.json'),
    )
    parser.add_argument("--source-id", default="gxw2_skill_1_6_1")
    parser.add_argument(
        "--source-dir",
        type=Path,
        help="Existing gxw2-skill checkout. If omitted, download the pinned archive.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=private_path('resources/knowledge/manifest.json'),
    )
    parser.add_argument("--target-chars", type=int, default=DEFAULT_TARGET_CHARS)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    args = parser.parse_args(argv)
    if args.target_chars < 512:
        parser.error("--target-chars must be at least 512")
    if args.max_chars < args.target_chars:
        parser.error("--max-chars must be >= --target-chars")
    return args


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value or "")
    value = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\u00a0", " ")
    return "\n".join(line.rstrip() for line in value.split("\n")).strip()


def decode_source_text(raw: bytes) -> str:
    """Decode gxw2-skill text files while preserving GX Works2 CSV compatibility."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def cjk_bigrams(text: str) -> str:
    terms: list[str] = []
    for match in CJK_RUN_RE.finditer(text):
        run = match.group(0)
        terms.extend(run[index : index + 2] for index in range(len(run) - 1))
    return " ".join(terms)


def load_source_spec(config_path: Path, source_id: str) -> SourceSpec:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    for item in payload.get("sources", []):
        if str(item.get("id")) != source_id:
            continue
        return SourceSpec(
            id=str(item["id"]),
            repository=str(item["repository"]),
            commit=str(item["commit"]),
            version=str(item["version"]),
            title=str(item["title"]),
            language=str(item.get("language", "en")),
            plc_models=",".join(str(value) for value in item.get("plc_models", [])),
            priority=int(item.get("priority", 50)),
            license=str(item.get("license", "")),
            homepage=str(item.get("homepage", "")),
            archive_url=str(item["archive_url"]),
            root=str(item.get("root", "")),
            published=str(item.get("published", "")),
        )
    raise KeyError(f"source id not found in {config_path}: {source_id}")


def _locate_skill_root(base: Path, relative_root: str) -> Path:
    base = base.expanduser().resolve()
    direct = base / relative_root
    if relative_root and direct.is_dir():
        return direct
    if (base / "references").is_dir() and (base / "examples").is_dir():
        return base
    candidates = list(base.glob(f"*/{relative_root}")) if relative_root else []
    if len(candidates) == 1 and candidates[0].is_dir():
        return candidates[0]
    raise FileNotFoundError(
        f"cannot locate gxw2 skill root under {base}; expected {relative_root!r}"
    )


@contextmanager
def source_root(spec: SourceSpec, source_dir: Path | None) -> Iterator[Path]:
    if source_dir is not None:
        yield _locate_skill_root(source_dir, spec.root)
        return

    with urllib.request.urlopen(spec.archive_url, timeout=60) as response:
        archive = response.read()
    if not archive:
        raise RuntimeError(f"empty archive downloaded from {spec.archive_url}")

    with tempfile.TemporaryDirectory(prefix="gxw2-skill-") as temp_name:
        temp_dir = Path(temp_name)
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = bundle.namelist()
            if not members:
                raise RuntimeError("gxw2-skill archive is empty")
            base = temp_dir.resolve()
            for member in bundle.infolist():
                destination = (base / member.filename).resolve()
                if base != destination and base not in destination.parents:
                    raise RuntimeError(
                        f"unsafe path in gxw2-skill archive: {member.filename}"
                    )
            bundle.extractall(temp_dir)
        root = _locate_skill_root(temp_dir, spec.root)
        yield root


def classify_path(relative_path: str) -> tuple[str, str]:
    path = Path(relative_path)
    name = path.name.casefold()
    if relative_path.startswith("references/DB/"):
        stem = path.stem
        if stem == "00_Instruction_List":
            return "instruction_index", ""
        if re.match(r"^\d+_", stem):
            return "instruction_group", ""
        return "skill_instruction", stem.upper()
    if name == "devices.md" or name == "system-devices.md":
        return "device", ""
    if name == "compatibility.md":
        return "compatibility", ""
    if name == "csv-variables.md":
        return "label_csv", ""
    if name == "data-types.md":
        return "data_type", ""
    if name == "common-rules.md":
        return "st_rule", ""
    if path.suffix.casefold() == ".iecst":
        return "example_st", ""
    if path.suffix.casefold() == ".csv":
        return "example_csv", ""
    return "reference", ""


def discover_documents(root: Path) -> list[SourceDocument]:
    paths: list[Path] = []
    references = root / "references"
    examples = root / "examples"
    if references.is_dir():
        paths.extend(path for path in references.rglob("*.md") if path.is_file())
    if examples.is_dir():
        paths.extend(
            path
            for path in examples.iterdir()
            if path.suffix.casefold() in {".iecst", ".csv"}
        )
    documents: list[SourceDocument] = []
    for path in sorted(set(paths), key=lambda item: item.as_posix().casefold()):
        relative = path.relative_to(root).as_posix()
        chunk_type, opcode = classify_path(relative)
        raw = path.read_bytes()
        text = normalize_text(decode_source_text(raw))
        documents.append(
            SourceDocument(
                relative_path=relative,
                text=text,
                sha256=sha256_bytes(raw),
                size=len(raw),
                chunk_type=chunk_type,
                instruction_opcode=opcode,
            )
        )
    return documents


def _markdown_units(text: str, fallback_title: str) -> list[tuple[str, str, str]]:
    """Return (chapter, outline_path, text) units split at Markdown headings."""
    lines = normalize_text(text).splitlines()
    stack: list[str] = []
    units: list[tuple[str, str, str]] = []
    current_lines: list[str] = []
    current_outline = fallback_title

    def flush() -> None:
        nonlocal current_lines
        body = "\n".join(current_lines).strip()
        if body:
            chapter = stack[0] if stack else fallback_title
            units.append((chapter, current_outline, body))
        current_lines = []

    for line in lines:
        match = HEADING_RE.match(line)
        if match:
            flush()
            depth = len(match.group(1))
            title = match.group(2).strip()
            stack[:] = stack[: depth - 1]
            while len(stack) < depth - 1:
                stack.append("")
            stack.append(title)
            current_outline = " > ".join(item for item in stack if item) or fallback_title
            current_lines.append(line)
        else:
            current_lines.append(line)
    flush()
    return units or [(fallback_title, fallback_title, normalize_text(text))]


def _paragraph_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    fence = ""
    for line in normalize_text(text).splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence = marker
            elif marker == fence:
                in_fence = False
            current.append(line)
            continue
        if not in_fence and not stripped:
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def _pack_text(text: str, target_chars: int, max_chars: int) -> list[str]:
    blocks = _paragraph_blocks(text)
    if not blocks:
        return []
    packed: list[str] = []
    current: list[str] = []
    current_size = 0

    def flush() -> None:
        nonlocal current, current_size
        if current:
            packed.append("\n\n".join(current).strip())
            current = []
            current_size = 0

    for block in blocks:
        if len(block) > max_chars:
            flush()
            for start in range(0, len(block), max_chars):
                packed.append(block[start : start + max_chars])
            continue
        addition = len(block) + (2 if current else 0)
        if current and current_size + addition > max_chars:
            flush()
        current.append(block)
        current_size += addition
        if current_size >= target_chars:
            flush()
    flush()
    return packed


def make_chunks(
    documents: list[SourceDocument],
    *,
    target_chars: int,
    max_chars: int,
) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    for document in documents:
        if document.chunk_type == "instruction_index":
            # The index duplicates the individual instruction pages and would
            # otherwise crowd out higher-fidelity instruction chunks.
            continue
        fallback = Path(document.relative_path).stem
        if Path(document.relative_path).suffix.casefold() == ".md":
            units = _markdown_units(document.text, fallback)
        else:
            units = [(fallback, fallback, document.text)]
        per_document: list[tuple[str, str, str]] = []
        for chapter, outline, unit_text in units:
            for part in _pack_text(unit_text, target_chars, max_chars):
                per_document.append((chapter, outline, part))
        count = max(1, len(per_document))
        for index, (chapter, outline, part) in enumerate(per_document, start=1):
            section = outline.split(" > ")[-1]
            section_key = f"{document.relative_path}:{outline}"
            prefix = [
                "SOURCE: gxw2-skill",
                f"FILE: {document.relative_path}",
                f"SECTION: {outline}",
                f"CHUNK_TYPE: {document.chunk_type}",
            ]
            if document.instruction_opcode:
                prefix.append(f"INSTRUCTION: {document.instruction_opcode}")
            text = "\n".join(prefix) + "\n\n" + part
            drafts.append(
                ChunkDraft(
                    source_file=document.relative_path,
                    chapter=chapter,
                    section=section,
                    outline_path=outline,
                    section_key=section_key,
                    chunk_type=document.chunk_type,
                    instruction_opcode=document.instruction_opcode,
                    text=text,
                    block_index=index,
                    block_count=count,
                    source_sha256=document.sha256,
                    source_size=document.size,
                )
            )
    return drafts


def known_opcodes(documents: Iterable[SourceDocument]) -> set[str]:
    return {
        document.instruction_opcode
        for document in documents
        if document.chunk_type == "skill_instruction" and document.instruction_opcode
    }


def extract_entities(
    text: str,
    opcodes: set[str],
    explicit_opcode: str = "",
    *,
    chunk_type: str = "",
) -> Counter[tuple[str, str]]:
    entities: Counter[tuple[str, str]] = Counter()
    for match in DEVICE_RE.finditer(text):
        prefix = match.group(1).upper()
        token = f"{prefix}{match.group(2)}"
        if match.group(3):
            token += f".{match.group(3)}"
        kind = (
            "operand_placeholder"
            if chunk_type in {"instruction", "skill_instruction"} and prefix == "S"
            else "device"
        )
        entities[(token, kind)] += 1
    upper = text.upper()
    for opcode in opcodes:
        count = len(
            re.findall(
                rf"(?<![A-Z0-9_]){re.escape(opcode)}(?![A-Z0-9_])", upper
            )
        )
        if count:
            entities[(opcode, "instruction")] += count
    if explicit_opcode:
        entities[(explicit_opcode, "instruction")] += 1
    return entities


def _validate_database(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version != SCHEMA_VERSION:
        raise RuntimeError(
            f"knowledge database schema v{SCHEMA_VERSION} required; found v{version}"
        )
    required = {
        "manuals",
        "chunks",
        "entity_index",
        "instructions",
        "instruction_aliases",
        "chunks_fts",
        "vector_embeddings",
        "meta",
    }
    present = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    missing = sorted(required - present)
    if missing:
        raise RuntimeError(
            f"knowledge database missing required objects: {', '.join(missing)}"
        )


def _combined_source_sha(documents: Iterable[SourceDocument]) -> str:
    digest = hashlib.sha256()
    for document in sorted(documents, key=lambda item: item.relative_path.casefold()):
        digest.update(document.relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(document.sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _summary_from_instruction(text: str) -> tuple[str, str]:
    clean = normalize_text(text)
    title = ""
    for line in clean.splitlines():
        match = HEADING_RE.match(line)
        if match:
            title = match.group(2).strip()
            break
    blocks = _paragraph_blocks(clean)
    summary = ""
    for block in blocks:
        if HEADING_RE.match(block.splitlines()[0]) and len(block.splitlines()) == 1:
            continue
        candidate = re.sub(r"^#{1,6}\s+.+?\n", "", block, count=1).strip()
        if candidate and not candidate.startswith("|"):
            summary = re.sub(r"\s+", " ", candidate)[:1000]
            break
    return title, summary


def import_source(
    database: Path,
    spec: SourceSpec,
    documents: list[SourceDocument],
    *,
    target_chars: int = DEFAULT_TARGET_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, int | str]:
    drafts = make_chunks(
        documents, target_chars=target_chars, max_chars=max_chars
    )
    opcodes = known_opcodes(documents)
    total_bytes = sum(document.size for document in documents)
    source_sha = _combined_source_sha(documents)

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        _validate_database(connection)
        connection.execute("BEGIN")
        try:
            # Deleting the manual cascades prior third-party chunks, entities,
            # instructions, and aliases. This also upgrades databases imported
            # by v1 of this adapter without leaving authoritative skill rows.
            connection.execute("DELETE FROM manuals WHERE manual_id=?", (spec.id,))
            connection.execute(
                """
                INSERT INTO manuals(
                    manual_id,manual_number,revision,published,title,language,manual_type,
                    plc_models,task_types,priority,source_file,source_bytes,source_sha256,
                    official_url,pdf_pages
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    spec.id,
                    "gxw2-skill",
                    spec.version,
                    spec.published,
                    spec.title,
                    spec.language,
                    "third_party_skill",
                    spec.plc_models,
                    TASK_TYPES,
                    spec.priority,
                    f"{spec.repository}@{spec.commit}",
                    total_bytes,
                    source_sha,
                    spec.homepage,
                    0,
                ),
            )

            chunk_count = 0
            entity_rows = 0
            for draft in drafts:
                entities = extract_entities(
                    draft.text,
                    opcodes,
                    explicit_opcode=draft.instruction_opcode,
                    chunk_type=draft.chunk_type,
                )
                entity_tokens = sorted({entity for entity, _kind in entities})
                entities_json = [
                    {"entity": entity, "type": kind, "occurrences": count}
                    for (entity, kind), count in sorted(entities.items())
                ]
                chunk_uid = (
                    f"{spec.id}:"
                    f"{hashlib.sha1(draft.section_key.encode('utf-8')).hexdigest()[:10]}:"
                    f"b{draft.block_index:03d}"
                )
                cursor = connection.execute(
                    """
                    INSERT INTO chunks(
                        chunk_uid,manual_id,manual_number,revision,manual_title,manual_type,
                        manual_priority,language,plc_models,task_types,source_file,pdf_page,
                        pdf_page_end,printed_page,printed_page_end,chapter,section,outline_path,
                        section_key,chunk_type,instruction_opcode,fnc_number,block_index,
                        block_count,source_pages_json,page_char_count,page_text_sha256,text,
                        char_count,text_sha256,entities,entities_json,cjk_bigrams,fidelity_flags
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        chunk_uid,
                        spec.id,
                        "gxw2-skill",
                        spec.version,
                        spec.title,
                        "third_party_skill",
                        spec.priority,
                        spec.language,
                        spec.plc_models,
                        TASK_TYPES,
                        draft.source_file,
                        0,
                        0,
                        "",
                        "",
                        draft.chapter,
                        draft.section,
                        draft.outline_path,
                        draft.section_key,
                        draft.chunk_type,
                        draft.instruction_opcode,
                        "",
                        draft.block_index,
                        draft.block_count,
                        "[]",
                        draft.source_size,
                        draft.source_sha256,
                        draft.text,
                        len(draft.text),
                        sha256_bytes(draft.text.encode("utf-8")),
                        " ".join(entity_tokens),
                        json.dumps(
                            entities_json,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        cjk_bigrams(draft.text),
                        "third_party,supporting,markdown",
                    ),
                )
                chunk_id = int(cursor.lastrowid)
                chunk_count += 1
                for (entity, kind), count in entities.items():
                    connection.execute(
                        """
                        INSERT INTO entity_index(
                            entity_norm,entity,entity_type,plc_models,task_types,
                            manual_id,chunk_id,occurrences
                        ) VALUES(?,?,?,?,?,?,?,?)
                        """,
                        (
                            entity.casefold(),
                            entity,
                            kind,
                            spec.plc_models,
                            TASK_TYPES,
                            spec.id,
                            chunk_id,
                            count,
                        ),
                    )
                    entity_rows += 1

            # Third-party instruction pages remain retrievable through chunks,
            # entities, FTS5, and dense search. They intentionally do not enter
            # the authoritative instructions/instruction_aliases stores.
            instruction_count = 0

            # FTS5 external-content indexes do not update themselves in this
            # schema, so rebuild after replacing the source.
            connection.execute(
                "INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')"
            )

            # Existing dense artifacts no longer match the corpus. The next
            # build_dense_embeddings.py run will repopulate these records.
            connection.execute("DELETE FROM vector_embeddings")
            for key, value in {
                "vector_status": "stale",
                "external_source_gxw2_skill_commit": spec.commit,
                "external_source_gxw2_skill_version": spec.version,
                "external_source_gxw2_skill_sha256": source_sha,
                "external_source_gxw2_skill_license": spec.license,
                "external_source_gxw2_skill_role": "supporting",
            }.items():
                connection.execute(
                    "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                    (key, value),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"database integrity check failed: {integrity}")

    return {
        "documents": len(documents),
        "chunks": chunk_count,
        "instructions": instruction_count,
        "entity_rows": entity_rows,
        "source_sha256": source_sha,
    }


def update_manifest(
    path: Path, spec: SourceSpec, stats: dict[str, int | str]
) -> None:
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    external = payload.setdefault("external_sources", [])
    replacement = {
        "id": spec.id,
        "type": "third_party_skill",
        "role": "supporting",
        "repository": spec.repository,
        "commit": spec.commit,
        "version": spec.version,
        "license": spec.license,
        "priority": spec.priority,
        "plc_models": spec.plc_models.split(",") if spec.plc_models else [],
        "source_sha256": stats["source_sha256"],
        "documents": stats["documents"],
        "chunks": stats["chunks"],
        "structured_instructions": stats["instructions"],
        "imported_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    external[:] = [item for item in external if item.get("id") != spec.id]
    external.append(replacement)
    payload.setdefault("retrieval", {})["vector_status"] = "stale"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    database = args.database.expanduser().resolve()
    config = args.source_config.expanduser().resolve()
    manifest = args.manifest.expanduser().resolve()
    if not database.is_file():
        raise SystemExit(
            f"knowledge database not found: {database}\n"
            "Run tools/build_fx3u_knowledge_v3.py before importing gxw2-skill."
        )
    spec = load_source_spec(config, args.source_id)
    with source_root(spec, args.source_dir) as root:
        documents = discover_documents(root)
        if not documents:
            raise SystemExit(f"no gxw2-skill documents found under {root}")
        stats = import_source(
            database,
            spec,
            documents,
            target_chars=args.target_chars,
            max_chars=args.max_chars,
        )
    update_manifest(manifest, spec, stats)
    print(
        "gxw2-skill imported: "
        f"documents={stats['documents']} chunks={stats['chunks']} "
        f"structured_instructions={stats['instructions']} entities={stats['entity_rows']}"
    )
    print("Dense embeddings are stale; run tools/build_dense_embeddings.py next.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
