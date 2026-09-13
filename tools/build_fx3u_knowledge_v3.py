#!/usr/bin/env python3
"""Build the section-aware, fidelity-preserving FX3U knowledge database.

The build is deliberately offline.  Runtime retrieval needs only SQLite/FTS5;
PDF parsing dependencies are used by this tool only.  Each source page is kept
in ``page_artifacts`` as plain text, layout text, detected tables, word geometry
and visual metadata.  Retrieval chunks are then assembled across contiguous
manual sections or instruction sections instead of blindly slicing each page.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
import unicodedata
from typing import Any, Iterable, Iterator

try:
    import pypdf
    from pypdf import PdfReader
except ImportError as error:  # pragma: no cover
    raise SystemExit("pypdf is required to rebuild the knowledge database") from error

try:
    import pdfplumber
except ImportError as error:  # pragma: no cover
    raise SystemExit("pdfplumber is required to preserve tables and word geometry") from error


BUILDER_VERSION = "3.0.2"
SCHEMA_VERSION = 3
TASK_TYPES = "*"
DEFAULT_TARGET_CHARS = 4800
DEFAULT_MAX_CHARS = 7600

CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
FNC_RE = re.compile(r"(?<![A-Z0-9])FNC\s*0*(\d{1,3})(?!\d)", re.IGNORECASE)
FNC_HEADING_RE = re.compile(
    r"(?:^|\n)\s*(?:\d+(?:\.\d+)*)?\s*FNC\s*0*(\d{1,3})\s*"
    r"[\-\u2013\u2014]\s*([A-Z][A-Z0-9_]*(?:\s*/\s*[A-Z][A-Z0-9_]*)?)"
    r"(?:\s*/\s*([^\n]+))?",
    re.IGNORECASE,
)
FNC_OPCODE_RE = re.compile(
    r"FNC\s*0*\d{1,3}\s*[\-\u2013\u2014]\s*([A-Z][A-Z0-9_]*)",
    re.IGNORECASE,
)

DEVICE_PREFIX = r"(?:ER|SM|SD|TS|TC|CS|CC|[XYMSTCDRVZPIN])"
DEVICE_RE = re.compile(
    rf"(?<![A-Z0-9_])({DEVICE_PREFIX})\s*(\d+)(?:\.(\d+))?(?![A-Z0-9_])",
    re.IGNORECASE,
)
DEVICE_RANGE_RE = re.compile(
    rf"(?<![A-Z0-9_])({DEVICE_PREFIX})\s*(\d+)\s*"
    rf"(?:~|\-|\u2013|\u2014|\uff5e)\s*(?:({DEVICE_PREFIX})\s*)?(\d+)(?![A-Z0-9_])",
    re.IGNORECASE,
)
OPERAND_TOKEN_RE = re.compile(r"(?<![A-Z0-9_])(S[1-9]\d*|D)(?![A-Z0-9_])", re.IGNORECASE)
ERROR_CODE_RE = re.compile(
    r"(?<![A-Z0-9])(?:0X)?([0-9A-F]{4,5})(H)?(?![A-Z0-9])",
    re.IGNORECASE,
)
PRIVATE_USE_RE = re.compile(r"[\ue000-\uf8ff]")

HIGH_FIDELITY_RE = re.compile(
    r"Operand\s+Type|Set\s+data|Applicable\s+devices|Instruction\s+format|"
    r"Error\s+(?:code|list|message)|Corrective\s+action|Cause|"
    r"Program\s+example|Device\s+Name|State\s+Relay|Special\s+Device",
    re.IGNORECASE,
)
ERROR_CONTEXT_RE = re.compile(
    r"error\s+(?:code|list|message|detection|check)|fault\s+(?:code|list)|"
    r"diagnos|troubleshoot|corrective\s+action|故障(?:码|诊断|处理)|错误码|报警码",
    re.IGNORECASE,
)

BASE_INSTRUCTIONS = {
    "LD", "LDI", "LDP", "LDF", "AND", "ANI", "ANDP", "ANDF",
    "OR", "ORI", "ORP", "ORF", "ANB", "ORB", "MPS", "MRD", "MPP",
    "OUT", "SET", "RST", "PLS", "PLF", "MC", "MCR", "INV", "NOP",
    "END", "FEND", "CJ", "CALL", "SRET", "IRET", "EI", "DI", "FOR",
    "NEXT", "MOV", "DMOV", "BMOV", "FMOV", "ZRST", "CMP", "ZCP",
    "ADD", "SUB", "MUL", "DIV", "INC", "DEC", "SFTL", "SFTLP",
    "SFR", "SFL", "WSFR", "WSFL", "RS", "RS2", "FROM", "TO", "PID",
    "PLSY", "DPLSY", "PLSR", "DPLSR", "PLSV", "DRVI", "DDRVI",
    "DRVA", "DDRVA", "DVIT", "ZRN", "DSZR", "HSCS", "HSCR", "HSZ",
    "SPD", "ALT", "ALTP", "DECO", "ENCO", "BON", "SUM", "MEAN",
    "ANS", "ANR", "SQR", "FLT", "INT", "WAND", "WOR", "WXOR",
    "ROL", "ROR", "RCL", "RCR", "SFTR", "SFTL", "XCH", "BCD",
    "BIN", "TRD", "TWR", "HKY", "DSW", "SEGD", "SEGL", "ARWS",
    "ASC", "PR", "VRRD", "VRSC", "PWM", "PLSR", "ABS", "SER",
    "SORT", "TKY", "DSZR", "TBL", "DUTY", "RAMP", "ROTC", "EXTR",
}

DEVICE_FAMILIES = {
    "X": "Input relay",
    "Y": "Output relay",
    "M": "Auxiliary relay",
    "S": "State relay",
    "T": "Timer",
    "C": "Counter",
    "D": "Data register",
    "R": "Extension register or file register",
    "ER": "Extension file register",
    "V": "Index register",
    "Z": "Index register",
    "P": "Branch pointer",
    "I": "Interrupt pointer",
}

INSTRUCTION_ZH_ALIASES = {
    "PLSY": ["脉冲输出", "定频脉冲", "脉冲数量输出"],
    "DPLSY": ["32位脉冲输出", "双字脉冲输出"],
    "PLSR": ["带加减速脉冲输出", "加减速脉冲"],
    "PLSV": ["可变速脉冲输出", "变速脉冲"],
    "DRVI": ["相对定位", "相对位置控制"],
    "DRVA": ["绝对定位", "绝对位置控制"],
    "ZRN": ["原点回归", "回零"],
    "DSZR": ["带DOG搜索回零", "原点搜索"],
    "DVIT": ["中断定位", "中断定长"],
    "MOV": ["数据传送", "字传送"],
    "DMOV": ["32位数据传送", "双字传送"],
    "BMOV": ["批量传送", "块传送"],
    "FMOV": ["多点同值传送", "填充传送"],
    "ZRST": ["区间复位", "批量清零"],
    "CMP": ["比较指令", "三路比较"],
    "ZCP": ["区间比较"],
    "INC": ["加一", "递增"],
    "DEC": ["减一", "递减"],
    "SET": ["置位", "保持置位"],
    "RST": ["复位", "解除保持"],
    "RS": ["无协议串行通信", "串行发送接收"],
    "RS2": ["扩展无协议串行通信"],
    "FROM": ["读取缓冲存储器", "读BFM"],
    "TO": ["写入缓冲存储器", "写BFM"],
    "PID": ["PID控制", "比例积分微分"],
    "HSCS": ["高速计数比较置位"],
    "HSCR": ["高速计数比较复位"],
    "HSZ": ["高速计数区间比较"],
    "SPD": ["脉冲密度检测", "转速检测"],
    "PWM": ["脉宽调制", "PWM输出"],
    "FLT": ["浮点转换"],
    "BCD": ["BCD转换"],
    "BIN": ["BIN转换", "二进制转换"],
}


@dataclass(frozen=True)
class ManualSpec:
    id: str
    path: Path
    manual_number: str
    revision: str
    published: str
    title: str
    language: str
    manual_type: str
    plc_models: str
    priority: int
    official_url: str
    expected_sha256: str


@dataclass
class PageArtifact:
    manual: ManualSpec
    pdf_page: int
    printed_page: str
    chapter: str
    section: str
    outline_path: str
    section_key: str
    chunk_type: str
    instruction_opcode: str
    fnc_number: str
    instruction_title: str
    plain_text: str
    clean_text: str
    layout_text: str
    compact_layout: str
    tables: list[dict[str, Any]] = field(default_factory=list)
    words: list[dict[str, Any]] = field(default_factory=list)
    visual: dict[str, Any] = field(default_factory=dict)
    fidelity_flags: set[str] = field(default_factory=set)


@dataclass
class ChunkDraft:
    manual: ManualSpec
    section_key: str
    chunk_type: str
    instruction_opcode: str
    fnc_number: str
    chapter: str
    section: str
    outline_path: str
    pages: list[int]
    printed_pages: list[str]
    text: str
    fidelity_flags: set[str]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value or "")
    value = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\u00a0", " ")
    return "\n".join(line.rstrip() for line in value.split("\n")).strip()


def normalize_line(value: str) -> str:
    return re.sub(r"\s+", " ", normalize_text(value)).strip()


def safe_glyphs(value: str) -> str:
    return PRIVATE_USE_RE.sub(lambda match: f"[GLYPH-{ord(match.group(0)):04X}]", value)


def cjk_bigrams(text: str) -> tuple[str, int]:
    terms: list[str] = []
    for match in CJK_RUN_RE.finditer(text):
        run = match.group(0)
        terms.extend(run[index : index + 2] for index in range(len(run) - 1))
    return " ".join(terms), len(terms)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Build a multi-manual, section-aware FX3U SQLite knowledge index"
    )
    parser.add_argument(
        "--sources-config",
        type=Path,
        default=repo_root / "resources" / "knowledge" / "sources.json",
    )
    parser.add_argument(
        "--debug-cases",
        type=Path,
        default=repo_root / "resources" / "knowledge" / "debug_cases.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root / "resources" / "knowledge" / "fx3u_knowledge.sqlite",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=repo_root / "resources" / "knowledge" / "manifest.json",
    )
    parser.add_argument("--target-chars", type=int, default=DEFAULT_TARGET_CHARS)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    parser.add_argument("--progress-every", type=int, default=50)
    parser.add_argument("--manual-id", action="append", default=[])
    parser.add_argument("--skip-tables", action="store_true")
    args = parser.parse_args(argv)
    if args.target_chars < 512:
        parser.error("--target-chars must be at least 512")
    if args.max_chars < args.target_chars:
        parser.error("--max-chars must be >= --target-chars")
    return args


def load_manual_specs(config_path: Path, selected_ids: Iterable[str]) -> list[ManualSpec]:
    config_path = config_path.expanduser().resolve()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    selected = {str(item).strip() for item in selected_ids if str(item).strip()}
    specs: list[ManualSpec] = []
    for item in payload.get("manuals", []):
        manual_id = str(item["id"])
        if selected and manual_id not in selected:
            continue
        source_path = (config_path.parent / str(item["file"])).resolve()
        spec = ManualSpec(
            id=manual_id,
            path=source_path,
            manual_number=str(item["manual_number"]),
            revision=str(item["revision"]),
            published=str(item.get("published", "")),
            title=str(item["title"]),
            language=str(item.get("language", "en")),
            manual_type=str(item.get("manual_type", "manual")),
            plc_models=",".join(str(value) for value in item.get("plc_models", [])),
            priority=int(item.get("priority", 50)),
            official_url=str(item.get("official_url", "")),
            expected_sha256=str(item.get("sha256", "")).lower(),
        )
        if not spec.path.is_file():
            raise FileNotFoundError(f"manual source not found: {spec.path}")
        actual_hash = sha256_file(spec.path)
        if spec.expected_sha256 and actual_hash != spec.expected_sha256:
            raise RuntimeError(
                f"source hash mismatch for {spec.id}: {actual_hash} != {spec.expected_sha256}"
            )
        specs.append(spec)
    if not specs:
        raise ValueError("no manuals selected")
    return sorted(specs, key=lambda item: (-item.priority, item.id))


def flatten_outline(reader: PdfReader) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    sequence = 0
    page_by_reference: dict[tuple[int, int], int] = {}
    for page_number, page in enumerate(reader.pages, start=1):
        reference = getattr(page, "indirect_reference", None)
        if reference is not None and hasattr(reference, "idnum"):
            page_by_reference[(int(reference.idnum), int(reference.generation))] = page_number

    def walk(items: list[Any], depth: int) -> None:
        nonlocal sequence
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            destination_page = getattr(item, "page", None)
            reference_key: tuple[int, int] | None = None
            if destination_page is not None and hasattr(destination_page, "idnum"):
                reference_key = (
                    int(destination_page.idnum),
                    int(destination_page.generation),
                )
            pdf_page = page_by_reference.get(reference_key) if reference_key else None
            if pdf_page is None:
                try:
                    pdf_page = reader.get_destination_page_number(item) + 1
                except Exception:
                    continue
            title = normalize_line(str(getattr(item, "title", item)))
            if title:
                entries.append(
                    {
                        "pdf_page": pdf_page,
                        "depth": depth,
                        "title": title,
                        "sequence": sequence,
                    }
                )
                sequence += 1

    outline = reader.outline
    if isinstance(outline, list):
        walk(outline, 0)
    entries.sort(key=lambda item: (item["pdf_page"], item["sequence"]))
    return entries


def outline_page_map(
    page_count: int, entries: list[dict[str, Any]]
) -> dict[int, tuple[str, str, str]]:
    result: dict[int, tuple[str, str, str]] = {}
    stack: list[str] = []
    cursor = 0
    for page_number in range(1, page_count + 1):
        while cursor < len(entries) and int(entries[cursor]["pdf_page"]) <= page_number:
            entry = entries[cursor]
            depth = int(entry["depth"])
            stack = stack[:depth]
            while len(stack) < depth:
                stack.append("")
            stack.append(str(entry["title"]))
            cursor += 1
        nonempty = [item for item in stack if item]
        chapter = nonempty[0] if nonempty else "Front matter"
        section = nonempty[-1] if nonempty else chapter
        result[page_number] = (chapter, section, " > ".join(nonempty))
    return result


def instruction_vocabulary(entries: list[dict[str, Any]]) -> set[str]:
    vocabulary = set(BASE_INSTRUCTIONS)
    for entry in entries:
        title = str(entry["title"]).upper()
        vocabulary.update(match.group(1).upper() for match in FNC_OPCODE_RE.finditer(title))
    return {
        value
        for value in vocabulary
        if re.fullmatch(r"[A-Z][A-Z0-9_]{1,15}", value)
        and value not in {"CPU", "PLC", "RUN", "STOP", "ON", "OFF", "FNC", "ERROR"}
    }


def compile_instruction_re(vocabulary: set[str]) -> re.Pattern[str]:
    alternatives = "|".join(
        re.escape(token) for token in sorted(vocabulary, key=lambda item: (-len(item), item))
    )
    return re.compile(rf"(?<![A-Z0-9_])(?:{alternatives})(?![A-Z0-9_])", re.IGNORECASE)


def extract_printed_page(text: str, manual_type: str = "") -> str:
    lines = [normalize_line(line) for line in text.splitlines() if normalize_line(line)]
    edge_lines = lines[:20] + lines[-20:]
    for candidate in reversed(edge_lines):
        if re.fullmatch(r"(?:[A-Z0-9]+\s*-\s*)+\d{1,4}", candidate, flags=re.I):
            return re.sub(r"\s+", "", candidate)
    numeric = [candidate for candidate in edge_lines if re.fullmatch(r"\d{1,4}", candidate)]
    if numeric:
        candidate = numeric[-1]
        if manual_type == "gxworks2_structured" and int(candidate) <= 10:
            return ""
        return candidate
    return ""


def common_page_lines(page_texts: list[str]) -> set[str]:
    counts: Counter[str] = Counter()
    for text in page_texts:
        seen = {
            normalize_line(line).casefold()
            for line in text.splitlines()
            if 1 < len(normalize_line(line)) <= 180
        }
        counts.update(seen)
    threshold = max(8, int(len(page_texts) * 0.04))
    common = set()
    for line, count in counts.items():
        if count < threshold:
            continue
        if "fnc " in line or "error" in line or "device" in line:
            continue
        common.add(line)
    return common


def clean_retrieval_text(text: str, common_lines: set[str]) -> str:
    kept: list[str] = []
    blank = False
    for raw_line in text.splitlines():
        line = normalize_line(raw_line)
        if line and line.casefold() in common_lines:
            continue
        if re.fullmatch(r"\d{1,4}", line):
            continue
        if not line:
            if kept and not blank:
                kept.append("")
            blank = True
            continue
        kept.append(safe_glyphs(line))
        blank = False
    return "\n".join(kept).strip()


def compact_layout_text(text: str, common_lines: set[str]) -> str:
    lines: list[str] = []
    blank_count = 0
    for raw_line in text.splitlines():
        stripped = raw_line.rstrip()
        canonical = normalize_line(stripped)
        if canonical and canonical.casefold() in common_lines:
            continue
        if re.fullmatch(r"\d{1,4}", canonical):
            continue
        if not canonical:
            blank_count += 1
            if lines and blank_count <= 1:
                lines.append("")
            continue
        blank_count = 0
        compact = re.sub(r" {3,}", " | ", stripped.strip())
        lines.append(safe_glyphs(compact))
    return "\n".join(lines).strip()


def detect_instruction(
    text: str,
    outline_path: str,
    manual_type: str,
    instruction_re: re.Pattern[str],
) -> tuple[str, str, str]:
    search_text = f"{outline_path}\n{text[:5000]}"
    if re.search(r"table of contents|contents >|index$", outline_path, flags=re.IGNORECASE):
        return "", "", ""
    matches = list(FNC_HEADING_RE.finditer(search_text))
    if matches:
        match = matches[-1]
        opcode = match.group(2).split("/")[0].strip().upper()
        title = normalize_line(match.group(3) or "")
        return opcode, str(int(match.group(1))), title

    if manual_type == "positioning":
        # The positioning manual uses outline headings such as
        # ``8.2 Drive to Increment - DRVI Instruction`` and
        # ``6.2 ... (DSZR Instruction)`` instead of the programming manual's
        # ``FNCxxx - OPCODE`` heading.  Match only one opcode (plus its D-prefixed
        # double-word variant) at the end of a heading.  This deliberately does
        # not collapse overview headings such as ``DSZR/ZRN Instruction`` or
        # parameter tables listing several different instructions.
        heading_re = re.compile(
            r"(?:[-(]\s*|for\s+)([A-Z][A-Z0-9_]*)(?:/D\1)?\s+Instructions?\)?$",
            re.IGNORECASE,
        )
        for component in reversed(outline_path.split(" > ")):
            heading_match = heading_re.search(component.strip())
            if not heading_match:
                continue
            opcode = heading_match.group(1).upper()
            if opcode not in BASE_INSTRUCTIONS:
                continue
            fnc_match = re.search(
                rf"FNC\s*0*(\d{{1,3}})\s+(?:D?{re.escape(opcode)})(?![A-Z0-9_])",
                text[:5000],
                flags=re.IGNORECASE,
            )
            fnc_number = str(int(fnc_match.group(1))) if fnc_match else ""
            return opcode, fnc_number, normalize_line(component)

    if manual_type in {"structured_instruction", "structured_function"}:
        outline_tail = outline_path.split(" > ")[-1]
        heading_body = re.sub(
            r"^\s*\d+(?:\.\d+)*\s+",
            "",
            outline_tail,
            count=1,
        )
        heading_match = instruction_re.match(heading_body)
        if heading_match:
            suffix = heading_body[heading_match.end() :]
            if re.match(r"^(?:\(_?E\)|_E)?\s*(?:/|[-–—]|instruction\b|$)", suffix, re.I):
                opcode = heading_match.group(0).upper()
                title = normalize_line(suffix)
                return opcode, "", title.strip(" -/()")
    return "", "", ""


def classify_section(
    manual: ManualSpec,
    page_number: int,
    outline_path: str,
    clean_text: str,
    instruction_re: re.Pattern[str],
) -> tuple[str, str, str, str, str]:
    opcode, fnc_number, title = detect_instruction(
        clean_text, outline_path, manual.manual_type, instruction_re
    )
    if opcode:
        return f"{manual.id}:instruction:{opcode}", "instruction", opcode, fnc_number, title
    context = f"{outline_path}\n{clean_text[:1200]}"
    if ERROR_CONTEXT_RE.search(context):
        normalized = hashlib.sha1(outline_path.encode("utf-8")).hexdigest()[:12]
        return f"{manual.id}:error:{normalized}", "error", "", "", ""
    device_match = re.search(
        r"(?:Input/Output Relays?|Auxiliary relay|State Relay|Timer|Counter|"
        r"Data Register|Index Register|Pointer|Special Device)",
        context,
        flags=re.IGNORECASE,
    )
    if device_match:
        normalized = hashlib.sha1(outline_path.encode("utf-8")).hexdigest()[:12]
        return f"{manual.id}:device:{normalized}", "device", "", "", ""
    normalized = hashlib.sha1(outline_path.encode("utf-8")).hexdigest()[:12]
    return f"{manual.id}:section:{normalized}", "section", "", "", ""


def normalize_table_cell(value: Any) -> str:
    if value is None:
        return ""
    return safe_glyphs(normalize_line(str(value)))


def serialize_table(rows: list[list[Any]]) -> tuple[list[list[str]], str]:
    normalized = [[normalize_table_cell(cell) for cell in row] for row in rows]
    normalized = [row for row in normalized if any(row)]
    if not normalized:
        return [], ""
    width = max(len(row) for row in normalized)
    padded = [row + [""] * (width - len(row)) for row in normalized]
    rendered = [" | ".join(cell or "<blank>" for cell in row) for row in padded]
    return padded, "\n".join(rendered)


def extract_tables_and_geometry(
    plumber_page: Any,
    *,
    include_tables: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    if include_tables:
        try:
            found_tables = plumber_page.find_tables()
        except Exception:
            found_tables = []
        for index, found in enumerate(found_tables, start=1):
            try:
                rows, text = serialize_table(found.extract())
            except Exception:
                continue
            if not rows or not text:
                continue
            tables.append(
                {
                    "index": index,
                    "bbox": [round(float(value), 2) for value in found.bbox],
                    "rows": rows,
                    "text": text,
                }
            )
    try:
        raw_words = plumber_page.extract_words(
            x_tolerance=2,
            y_tolerance=3,
            keep_blank_chars=False,
            use_text_flow=True,
        )
    except Exception:
        raw_words = []
    words = [
        {
            "text": safe_glyphs(str(word.get("text", ""))),
            "x0": round(float(word.get("x0", 0.0)), 2),
            "x1": round(float(word.get("x1", 0.0)), 2),
            "top": round(float(word.get("top", 0.0)), 2),
            "bottom": round(float(word.get("bottom", 0.0)), 2),
        }
        for word in raw_words[:6000]
        if str(word.get("text", "")).strip()
    ]
    visual = {
        "width": round(float(plumber_page.width), 2),
        "height": round(float(plumber_page.height), 2),
        "images": len(plumber_page.images),
        "lines": len(plumber_page.lines),
        "rects": len(plumber_page.rects),
        "curves": len(plumber_page.curves),
        "word_count": len(words),
        "table_count": len(tables),
    }
    return tables, words, visual


def layout_text_from_words(words: list[dict[str, Any]]) -> str:
    """Reconstruct readable rows from coordinates without a second PDF parse."""
    if not words:
        return ""
    rows: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        if not rows:
            rows.append([word])
            continue
        row_top = sum(float(item["top"]) for item in rows[-1]) / len(rows[-1])
        if abs(float(word["top"]) - row_top) <= 3.0:
            rows[-1].append(word)
        else:
            rows.append([word])
    lines: list[str] = []
    previous_bottom: float | None = None
    for row in rows:
        row.sort(key=lambda item: float(item["x0"]))
        top = min(float(item["top"]) for item in row)
        bottom = max(float(item["bottom"]) for item in row)
        if previous_bottom is not None and top - previous_bottom > 14.0:
            lines.append("")
        fragments: list[str] = []
        previous_x1: float | None = None
        for word in row:
            x0 = float(word["x0"])
            if previous_x1 is not None:
                gap = x0 - previous_x1
                fragments.append(" | " if gap > 18.0 else " ")
            fragments.append(str(word["text"]))
            previous_x1 = float(word["x1"])
        lines.append("".join(fragments).strip())
        previous_bottom = bottom
    return "\n".join(lines).strip()


def extract_diagram_text(layout_text: str, instruction_opcode: str) -> str:
    if not layout_text or not instruction_opcode:
        return ""
    lines = layout_text.splitlines()
    selected: list[str] = []
    pattern = re.compile(
        rf"(?<![A-Z0-9_])(?:D?{re.escape(instruction_opcode)})(?![A-Z0-9_])",
        re.IGNORECASE,
    )
    indexes = [index for index, line in enumerate(lines) if pattern.search(line)]
    seen: set[int] = set()
    for index in indexes:
        for line_index in range(max(0, index - 2), min(len(lines), index + 4)):
            if line_index in seen:
                continue
            line = normalize_line(lines[line_index])
            if line:
                selected.append(safe_glyphs(re.sub(r" {3,}", " | ", lines[line_index].strip())))
                seen.add(line_index)
    return "\n".join(selected[:80])


def extract_manual_pages(
    manual: ManualSpec,
    *,
    skip_tables: bool,
    progress_every: int,
    started: float,
) -> tuple[list[PageArtifact], set[str], re.Pattern[str], list[dict[str, Any]]]:
    reader = PdfReader(str(manual.path))
    page_count = len(reader.pages)
    outline_entries = flatten_outline(reader)
    page_outline = outline_page_map(page_count, outline_entries)
    vocabulary = instruction_vocabulary(outline_entries)
    instruction_re = compile_instruction_re(vocabulary)

    plain_pages: list[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        plain = normalize_text(page.extract_text() or "")
        plain_pages.append(plain)
        if page_number % progress_every == 0 or page_number == page_count:
            print(
                f"[{manual.id}] plain text {page_number:04d}/{page_count:04d} "
                f"elapsed={time.perf_counter() - started:.1f}s",
                flush=True,
            )

    common_lines = common_page_lines(plain_pages)
    artifacts: list[PageArtifact] = []
    with pdfplumber.open(str(manual.path)) as plumber_pdf:
        for page_number, plain in enumerate(plain_pages, start=1):
            chapter, section, outline_path = page_outline[page_number]
            clean = clean_retrieval_text(plain, common_lines)
            section_key, chunk_type, opcode, fnc_number, instruction_title = classify_section(
                manual,
                page_number,
                outline_path,
                clean,
                instruction_re,
            )
            context = f"{outline_path}\n{clean[:5000]}"
            fidelity_match = bool(HIGH_FIDELITY_RE.search(context))
            high_fidelity = bool(opcode or fidelity_match)
            table_candidate = bool(
                not skip_tables
                and (fidelity_match or chunk_type in {"error", "device"})
            )
            tables: list[dict[str, Any]] = []
            words: list[dict[str, Any]] = []
            visual: dict[str, Any]
            fidelity_flags = {"plain_text", "layout_text"}
            if high_fidelity:
                tables, words, visual = extract_tables_and_geometry(
                    plumber_pdf.pages[page_number - 1],
                    include_tables=table_candidate,
                )
                layout = layout_text_from_words(words) or plain
                visual["geometry_extracted"] = True
                fidelity_flags.add("word_geometry")
                if tables:
                    fidelity_flags.add("tables")
                if visual.get("lines") or visual.get("rects") or visual.get("curves"):
                    fidelity_flags.add("drawing_geometry")
            else:
                layout = plain
                box = reader.pages[page_number - 1].mediabox
                visual = {
                    "width": round(float(box.width), 2),
                    "height": round(float(box.height), 2),
                    "images": 0,
                    "lines": 0,
                    "rects": 0,
                    "curves": 0,
                    "word_count": 0,
                    "table_count": 0,
                    "geometry_extracted": False,
                }
            compact_layout = compact_layout_text(layout, common_lines)
            diagram_text = extract_diagram_text(compact_layout, opcode)
            if diagram_text:
                visual["diagram_text"] = diagram_text
                fidelity_flags.add("diagram_text")
            artifacts.append(
                PageArtifact(
                    manual=manual,
                    pdf_page=page_number,
                    printed_page=extract_printed_page(plain, manual.manual_type),
                    chapter=chapter,
                    section=section,
                    outline_path=outline_path,
                    section_key=section_key,
                    chunk_type=chunk_type,
                    instruction_opcode=opcode,
                    fnc_number=fnc_number,
                    instruction_title=instruction_title,
                    plain_text=safe_glyphs(plain),
                    clean_text=clean,
                    layout_text=safe_glyphs(layout),
                    compact_layout=compact_layout,
                    tables=tables,
                    words=words,
                    visual=visual,
                    fidelity_flags=fidelity_flags,
                )
            )
            if page_number % progress_every == 0 or page_number == page_count:
                print(
                    f"[{manual.id}] fidelity {page_number:04d}/{page_count:04d} "
                    f"tables={sum(len(item.tables) for item in artifacts)} "
                    f"elapsed={time.perf_counter() - started:.1f}s",
                    flush=True,
                )
    return artifacts, vocabulary, instruction_re, outline_entries


def split_lines_complete(text: str, maximum: int) -> list[str]:
    if len(text) <= maximum:
        return [text] if text else []
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines(keepends=True):
        if current and size + len(line) > maximum:
            parts.append("".join(current).rstrip())
            current = []
            size = 0
        if len(line) > maximum:
            if current:
                parts.append("".join(current).rstrip())
                current = []
                size = 0
            for start in range(0, len(line), maximum):
                parts.append(line[start : start + maximum].rstrip())
            continue
        current.append(line)
        size += len(line)
    if current:
        parts.append("".join(current).rstrip())
    return [part for part in parts if part]


def table_chunk_parts(page: PageArtifact, maximum: int) -> list[str]:
    parts: list[str] = []
    for table in page.tables:
        rows = list(table.get("rows", []))
        if not rows:
            continue
        bbox = table.get("bbox", [])
        header = (
            f"[TABLE page={page.pdf_page} index={table.get('index')} "
            f"bbox={json.dumps(bbox, separators=(',', ':'))}]"
        )
        row_lines = [" | ".join(cell or "<blank>" for cell in row) for row in rows]
        current = header
        for row_line in row_lines:
            candidate = current + "\n" + row_line
            if len(candidate) > maximum and current != header:
                parts.append(current)
                current = header + "\n" + row_line
            else:
                current = candidate
        if current != header:
            parts.append(current)
    return parts


def page_retrieval_parts(page: PageArtifact, maximum: int) -> list[tuple[int, str, str]]:
    parts: list[tuple[int, str, str]] = []
    if page.clean_text:
        for block in split_lines_complete(page.clean_text, maximum):
            parts.append((page.pdf_page, "prose", f"[PAGE {page.pdf_page} PROSE]\n{block}"))
    if page.chunk_type in {"instruction", "error", "device"} and page.compact_layout:
        for block in split_lines_complete(page.compact_layout, maximum):
            parts.append((page.pdf_page, "layout", f"[PAGE {page.pdf_page} LAYOUT]\n{block}"))
    for block in table_chunk_parts(page, maximum):
        parts.append((page.pdf_page, "table", block))
    diagram = str(page.visual.get("diagram_text", ""))
    if diagram:
        parts.append((page.pdf_page, "diagram", f"[PAGE {page.pdf_page} LADDER/DIAGRAM]\n{diagram}"))
    if not parts:
        parts.append((page.pdf_page, "empty", f"[PAGE {page.pdf_page} EMPTY/TEXT-UNAVAILABLE]"))
    return parts


def contiguous_groups(pages: list[PageArtifact]) -> Iterator[list[PageArtifact]]:
    current: list[PageArtifact] = []
    for page in pages:
        if current and page.section_key != current[-1].section_key:
            yield current
            current = []
        current.append(page)
    if current:
        yield current


def make_section_chunks(
    pages: list[PageArtifact],
    *,
    target_chars: int,
    max_chars: int,
) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    part_maximum = max(1200, max_chars - 1200)
    for group in contiguous_groups(pages):
        first = group[0]
        packed: list[tuple[int, str, str]] = []
        packed_size = 0

        def flush() -> None:
            nonlocal packed, packed_size
            if not packed:
                return
            source_pages = sorted({item[0] for item in packed})
            page_lookup = {page.pdf_page: page for page in group}
            printed = [page_lookup[number].printed_page for number in source_pages if page_lookup[number].printed_page]
            flags = {
                kind for _page, kind, _text in packed if kind not in {"prose", "empty"}
            }
            flags.update(*(page_lookup[number].fidelity_flags for number in source_pages))
            prefix = [
                f"SOURCE: {first.manual.title}",
                f"MANUAL: {first.manual.manual_number} Rev.{first.manual.revision}",
                f"SECTION: {first.outline_path or first.section}",
                f"SECTION_KEY: {first.section_key}",
                f"PAGES: {','.join(str(value) for value in source_pages)}",
                f"CHUNK_TYPE: {first.chunk_type}",
            ]
            if first.instruction_opcode:
                prefix.append(
                    f"INSTRUCTION: {first.instruction_opcode}"
                    + (f" (FNC {first.fnc_number})" if first.fnc_number else "")
                )
            text = "\n".join(prefix) + "\n\n" + "\n\n".join(item[2] for item in packed)
            drafts.append(
                ChunkDraft(
                    manual=first.manual,
                    section_key=first.section_key,
                    chunk_type=first.chunk_type,
                    instruction_opcode=first.instruction_opcode,
                    fnc_number=first.fnc_number,
                    chapter=first.chapter,
                    section=first.section,
                    outline_path=first.outline_path,
                    pages=source_pages,
                    printed_pages=printed,
                    text=text,
                    fidelity_flags=flags,
                )
            )
            packed = []
            packed_size = 0

        for page in group:
            for part in page_retrieval_parts(page, part_maximum):
                addition = len(part[2]) + (2 if packed else 0)
                if packed and packed_size + addition > max_chars:
                    flush()
                packed.append(part)
                packed_size += addition
                if packed_size >= target_chars:
                    flush()
        flush()
    return drafts


def _explicit_state_relay_context(text: str, token: str) -> bool:
    escaped = re.escape(token)
    return bool(
        re.search(rf"state\s+relay[^\n]{0,80}\b{escaped}\b", text, flags=re.I)
        or re.search(rf"\b{escaped}\b[^\n]{0,80}state\s+relay", text, flags=re.I)
        or re.search(
            rf"\b(?:SET|RST|LD|LDI|OUT)\s+(?:\[[^\]]+\]\s*)?{escaped}\b",
            text,
            flags=re.I,
        )
    )


def _operand_placeholder_context(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 120) : min(len(text), end + 160)]
    return bool(
        re.search(
            r"operand|source\s+data|set\s+data|instruction\s+format|"
            r"source\s+operand|destination\s+operand|position|placeholder|"
            r"操作数|源数据|源操作数|形参|占位符",
            window,
            flags=re.I,
        )
    )


def extract_entities(
    text: str,
    instruction_re: re.Pattern[str],
    *,
    chunk_type: str,
    explicit_entities: Iterable[str] = (),
) -> Counter[tuple[str, str]]:
    entities: Counter[tuple[str, str]] = Counter()
    for match in DEVICE_RANGE_RE.finditer(text):
        first_prefix = match.group(1).upper()
        second_prefix = (match.group(3) or first_prefix).upper()
        first_token = f"{first_prefix}{match.group(2)}"
        second_token = f"{second_prefix}{match.group(4)}"
        if (
            first_prefix == second_prefix == "S"
            and chunk_type == "instruction"
            and not _explicit_state_relay_context(text, first_token)
            and not _explicit_state_relay_context(text, second_token)
        ):
            entities[(first_token, "operand_placeholder")] += 1
            entities[(second_token, "operand_placeholder")] += 1
            continue
        entity = f"{first_prefix}{match.group(2)}-{second_prefix}{match.group(4)}"
        entities[(entity, "device_range")] += 1

    for match in DEVICE_RE.finditer(text):
        prefix = match.group(1).upper()
        entity = f"{prefix}{match.group(2)}"
        if match.group(3):
            entity += f".{match.group(3)}"
        if prefix == "S" and int(match.group(2)) > 0:
            is_operand_context = chunk_type == "instruction" or _operand_placeholder_context(
                text, match.start(), match.end()
            )
            if is_operand_context and not _explicit_state_relay_context(text, entity):
                entities[(entity, "operand_placeholder")] += 1
                continue
        entities[(entity, "device")] += 1

    for match in instruction_re.finditer(text):
        entities[(match.group(0).upper(), "instruction")] += 1
    for match in FNC_RE.finditer(text):
        entities[(f"FNC {int(match.group(1)):02d}", "fnc")] += 1
    if chunk_type == "instruction":
        for match in OPERAND_TOKEN_RE.finditer(text):
            token = match.group(1).upper()
            if token.startswith("S") and _explicit_state_relay_context(text, token):
                continue
            entities[(token, "operand_placeholder")] += 1
    if chunk_type == "error":
        for match in plausible_error_matches(sanitize_error_text(text)):
            code = match.group(1).upper() + ("H" if match.group(2) else "")
            entities[(code, "error_code")] += 1
    for raw in explicit_entities:
        entity = normalize_line(str(raw)).upper()
        if not entity:
            continue
        if instruction_re.fullmatch(entity):
            kind = "instruction"
        elif re.fullmatch(r"S[1-9]\d*", entity) and (
            chunk_type == "instruction"
            or _operand_placeholder_context(text, 0, len(text))
        ) and not _explicit_state_relay_context(text, entity):
            kind = "operand_placeholder"
        elif DEVICE_RE.fullmatch(entity):
            kind = "device"
        elif ERROR_CODE_RE.fullmatch(entity):
            kind = "error_code"
        else:
            kind = "topic"
        entities[(entity, kind)] += 1
    return entities


def _operand_name(value: str) -> str:
    """Normalize Mitsubishi instruction operand placeholders.

    Data operands in the source manuals use S/D/N/M/P families (optionally
    numbered). Device headers such as X/Y/K are not operand placeholders.
    """
    token = normalize_line(str(value or "")).strip()
    if not token or token.upper() in {"EN", "ENO"}:
        return ""
    if not re.fullmatch(r"[SDNMPsdnmp](?:\d{0,3})?", token):
        return ""
    return token.upper()


def _clean_table_cell(value: str) -> str:
    token = normalize_line(str(value or "")).strip()
    if token.casefold() in {"", "<blank>", "blank", "-", "—"}:
        return ""
    return token


def _device_column_name(value: str) -> str:
    """Normalize one column label from an Applicable devices matrix."""
    token = normalize_line(str(value or ""))
    token = re.sub(r"\[GLYPH-[0-9A-F]+\]|\(cid:\d+\)", "", token, flags=re.I)
    token = re.sub(r"\s+", "", token)
    if not token or token.casefold() in {"<blank>", "blank"}:
        return ""
    upper = token.upper()
    if upper in {"X", "Y", "M", "T", "C", "S", "D", "R", "V", "Z", "K", "H", "E", "P"}:
        return upper
    if re.fullmatch(r"KN[XYMS]", upper):
        return "Kn" + upper[2:]
    if upper in {"D.B", "DB"}:
        return "D.b"
    if upper.startswith("U") and "\\G" in upper:
        return "U\\G"
    if upper.startswith("MODIF"):
        return "Modifier"
    if token.startswith('"'):
        return "String"
    return ""


def _signature_operand_order(pages: list[PageArtifact], opcode: str) -> list[str]:
    if not opcode:
        return []
    pattern = re.compile(rf"(?<![A-Z0-9_]){re.escape(opcode)}\s*\(([^)]{{1,180}})\)", re.I)
    for page in pages[:4]:
        for text in (page.compact_layout, page.layout_text, page.clean_text):
            for match in pattern.finditer(text or ""):
                raw_args = [normalize_line(value) for value in match.group(1).split(",")]
                names: list[str] = []
                valid = True
                for raw in raw_args:
                    if raw.upper() in {"EN", "ENO"}:
                        continue
                    name = _operand_name(raw)
                    if not name:
                        valid = False
                        break
                    names.append(name)
                if valid and names:
                    return list(dict.fromkeys(names))
    return []


def _compact_operand_rows(page: PageArtifact) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    active = False
    type_pattern = re.compile(
        r"\b(?:ANY(?:16|32|_SIMPLE)|BIN\s*\d+(?:/\d+)?-?bit|bit|binary|word|double\s*word|integer|real|bool|string)\b",
        flags=re.I,
    )
    for raw_line in page.compact_layout.splitlines():
        line = normalize_line(raw_line.replace("|", " | "))
        if re.search(r"\bSet data\b|\bOperand\s+Type\b|^Variable\s*\|", line, flags=re.I):
            active = True
            continue
        if active and re.search(
            r"Applicable devices|Explanation of function|Function and operation explanation",
            line,
            flags=re.I,
        ):
            break
        if not active:
            continue
        cells = [_clean_table_cell(value) for value in raw_line.split("|")]
        if not cells:
            continue
        name = next((_operand_name(value) for value in cells[:2] if _operand_name(value)), "")
        if not name:
            continue
        meaningful = [value for value in cells[1:] if value]
        description = meaningful[0] if meaningful else ""
        data_type = next((value for value in meaningful[1:] if type_pattern.search(value)), "")
        if description:
            rows.append((name, description, data_type))
    return rows


def _definition_table_rows(
    page: PageArtifact,
    expected_order: list[str],
) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    type_pattern = re.compile(
        r"\b(?:ANY(?:16|32|_SIMPLE)|BIN\s*\d+(?:/\d+)?-?bit|bit|binary|word|double\s*word|integer|real|bool|string)\b",
        flags=re.I,
    )
    for table in page.tables:
        rows = table.get("rows") or []
        table_text = normalize_line(str(table.get("text", "")))
        if not rows or not re.search(r"\bDescription\b", table_text, flags=re.I):
            continue
        if re.search(r"Bit\s+Devices", table_text, flags=re.I) and re.search(r"Word\s+Devices", table_text, flags=re.I):
            continue

        header_index = -1
        desc_col = -1
        for row_index, row in enumerate(rows[:6]):
            for column, raw in enumerate(row):
                if re.search(r"\bDescription\b", _clean_table_cell(raw), flags=re.I):
                    header_index, desc_col = row_index, column
                    break
            if header_index >= 0:
                break
        if header_index < 0 or desc_col < 0:
            continue

        expected_cursor = 0
        consumed: set[str] = set()
        for row in rows[header_index + 1:]:
            cells = [_clean_table_cell(value) for value in row]
            if desc_col >= len(cells):
                continue
            name_area = cells[:desc_col]
            if any(str(value).upper() in {"EN", "ENO"} for value in name_area if value):
                continue
            explicit = next((_operand_name(value) for value in name_area if _operand_name(value)), "")
            description = cells[desc_col]
            if not description:
                continue
            data_type = next((value for value in cells[desc_col + 1:] if value and type_pattern.search(value)), "")
            name = explicit
            if not name:
                while expected_cursor < len(expected_order) and expected_order[expected_cursor] in consumed:
                    expected_cursor += 1
                if expected_cursor < len(expected_order):
                    name = expected_order[expected_cursor]
                    expected_cursor += 1
            if not name:
                continue
            consumed.add(name)
            result.append((name, description, data_type))
    return result


def _applicable_devices_by_operand(
    page: PageArtifact,
    expected_order: list[str],
) -> dict[str, set[str]]:
    """Decode official Applicable-devices matrices without opcode rules."""
    found: dict[str, set[str]] = defaultdict(set)
    for table in page.tables:
        rows = table.get("rows") or []
        table_text = normalize_line(str(table.get("text", "")))
        if not rows:
            continue
        if not (
            re.search(r"Bit\s+Devices", table_text, flags=re.I)
            and re.search(r"Word\s+Devices", table_text, flags=re.I)
        ):
            continue

        best_index = -1
        best_headers: dict[int, str] = {}
        for row_index, row in enumerate(rows[:8]):
            headers = {
                index: name
                for index, cell in enumerate(row)
                if (name := _device_column_name(cell))
            }
            if len(headers) > len(best_headers):
                best_index, best_headers = row_index, headers
        if len(best_headers) < 4:
            continue

        first_device_col = min(best_headers)
        expected_cursor = 0
        consumed: set[str] = set()
        for row in rows[best_index + 1:]:
            cells = [_clean_table_cell(value) for value in row]
            marked = [
                column
                for column in best_headers
                if column < len(cells) and cells[column]
            ]
            if not marked:
                continue
            explicit = next(
                (_operand_name(value) for value in cells[:first_device_col] if _operand_name(value)),
                "",
            )
            name = explicit
            if not name:
                while expected_cursor < len(expected_order) and expected_order[expected_cursor] in consumed:
                    expected_cursor += 1
                if expected_cursor < len(expected_order):
                    name = expected_order[expected_cursor]
                    expected_cursor += 1
            if not name:
                continue
            consumed.add(name)
            for column in marked:
                found[name].add(best_headers[column])
    return found


def parse_operand_schema(
    pages: list[PageArtifact],
    opcode: str = "",
) -> list[dict[str, Any]]:
    """Extract operand semantics and device applicability from manual tables.

    The parser distinguishes operand-definition tables from Applicable-devices
    matrices and uses instruction signatures only to align rows whose operand
    glyph was lost by PDF table extraction.
    """
    operands: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}

    def remember(name: str, description: str = "", data_type: str = "") -> None:
        if not name:
            return
        description = normalize_line(description)
        data_type = normalize_line(data_type)
        type_only = re.compile(
            r"^(?:ANY(?:16|32|_SIMPLE)|BIN\s*\d+(?:/\d+)?-?bit|\d+-bit\s+binary|\d+-\s*or\s*\d+-bit\s+binary|bit|binary|word|double\s*word|integer|real|bool|string)(?:\s+binary)?$",
            flags=re.I,
        )
        if description and not data_type and type_only.fullmatch(description):
            data_type, description = description, ""
        if description and re.fullmatch(
            r"(?:(?:\[GLYPH-[0-9A-F]+\]|\(cid:\d+\))\d*\s*)+", description, flags=re.I
        ):
            description = ""
        if description and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", description) and len(description) <= 3:
            description = ""
        if description and len(description) < 24:
            words = re.findall(r"[A-Za-z]+", description)
            if words and not any(len(word) >= 4 for word in words) and not re.search(r"\d{2,}", description):
                description = ""
        current = by_name.get(name)
        if current is None:
            current = {"position": name, "description": description, "data_type": data_type}
            operands.append(current)
            by_name[name] = current
        else:
            if description and not current.get("description"):
                current["description"] = description
            if data_type and not current.get("data_type"):
                current["data_type"] = data_type

    compact_rows: list[tuple[str, str, str]] = []
    for page in pages[:4]:
        compact_rows.extend(_compact_operand_rows(page))
    signature_order = _signature_operand_order(pages, opcode)
    if signature_order:
        compact_rows = [row for row in compact_rows if row[0] in signature_order]
    compact_order = list(dict.fromkeys(name for name, _description, _data_type in compact_rows))
    expected_order = signature_order or compact_order

    for name, description, data_type in compact_rows:
        remember(name, description, data_type)
    for page in pages[:4]:
        for name, description, data_type in _definition_table_rows(page, expected_order):
            remember(name, description, data_type)

    applicability: dict[str, set[str]] = defaultdict(set)
    for page in pages[:4]:
        for name, devices in _applicable_devices_by_operand(page, expected_order).items():
            applicability[name].update(devices)
    for name, devices in applicability.items():
        remember(name)
        if devices:
            by_name[name]["applicable_devices"] = sorted(devices)

    # Do not publish a placeholder that was seen only as an unlabeled/empty row.
    return [
        item for item in operands
        if item.get("description") or item.get("data_type") or item.get("applicable_devices")
    ]


def instruction_summary(pages: list[PageArtifact]) -> str:
    text = "\n".join(page.clean_text for page in pages[:2])
    match = re.search(
        r"\bOutline\b\s*(.+?)(?=\n\s*(?:1\.|Instruction format|Set data|Applicable devices))",
        text,
        flags=re.I | re.S,
    )
    if match:
        return normalize_line(match.group(1))[:1000]
    lines = [normalize_line(line) for line in text.splitlines() if len(normalize_line(line)) > 20]
    return " ".join(lines[:3])[:1000]


def instruction_restrictions(pages: list[PageArtifact]) -> list[str]:
    restrictions: list[str] = []
    for page in pages:
        for line in page.clean_text.splitlines():
            normalized = normalize_line(line)
            if re.search(
                r"allowable setting range|supported only|cannot be used|caution|must be|specify",
                normalized,
                flags=re.I,
            ) and normalized not in restrictions:
                restrictions.append(normalized)
            if len(restrictions) >= 24:
                return restrictions
    return restrictions


def instruction_completion_flags(pages: list[PageArtifact]) -> list[str]:
    flags: list[str] = []
    for page in pages:
        text = page.clean_text
        for match in re.finditer(r"\bM8\d{3}\b", text, flags=re.I):
            window = text[max(0, match.start() - 100) : match.end() + 140]
            if re.search(r"complete|completion|flag|finished", window, flags=re.I):
                value = match.group(0).upper()
                if value not in flags:
                    flags.append(value)
    return flags


def instruction_variants(pages: list[PageArtifact], opcode: str, instruction_re: re.Pattern[str]) -> list[str]:
    variants = {opcode.upper()}
    for page in pages[:3]:
        for match in instruction_re.finditer(page.clean_text):
            candidate = match.group(0).upper()
            if candidate == opcode.upper() or candidate.lstrip("D") == opcode.upper().lstrip("D"):
                variants.add(candidate)
    return sorted(variants, key=lambda value: (len(value), value))


def sanitize_error_text(text: str) -> str:
    value = re.sub(r"\[GLYPH-[0-9A-F]{4}\]", " ", text, flags=re.I)
    value = re.sub(
        rf"\[[A-Z]{{1,2}}\]\s*\d+(?:\.\d+)?",
        " ",
        value,
        flags=re.I,
    )
    value = DEVICE_RANGE_RE.sub(" ", value)
    value = DEVICE_RE.sub(" ", value)
    return value


def plausible_error_matches(text: str) -> list[re.Match[str]]:
    matches: list[re.Match[str]] = []
    for match in ERROR_CODE_RE.finditer(text):
        digits = match.group(1).upper()
        if len(digits) != 4:
            continue
        is_hex = bool(re.search(r"[A-F]", digits))
        if digits == "0000" or is_hex or digits[:1] in set("3456789"):
            matches.append(match)
    return matches


def parse_error_records(page: PageArtifact) -> list[dict[str, Any]]:
    if page.chunk_type != "error":
        return []
    records: list[dict[str, Any]] = []
    strict_error_list = bool(
        re.search(
            r"Error\s+Code\s+List\s+and\s+Action|Error\s+List\s+and\s+Action",
            page.outline_path,
            flags=re.I,
        )
    )
    if strict_error_list:
        text = sanitize_error_text(page.clean_text)
        matches = plausible_error_matches(text)
        for match_index, match in enumerate(matches):
            code = match.group(1).upper() + ("H" if match.group(2) else "")
            before = text[max(0, match.start() - 90) : match.start()]
            if code != "0000" and re.search(
                r"example.{0,70}$|\bwhen\s*$",
                before,
                flags=re.I,
            ):
                continue
            block_end = (
                matches[match_index + 1].start()
                if match_index + 1 < len(matches)
                else min(len(text), match.end() + 1800)
            )
            raw_block = text[match.end() : block_end].strip()
            raw_lines = [
                normalize_line(line)
                for line in raw_block.splitlines()
                if normalize_line(line)
            ]
            if not raw_lines:
                continue
            message = raw_lines[0][:500]
            action_lines = [
                line
                for line in raw_lines
                if re.search(
                    r"\b(?:check|verify|review|modify|correct|ensure|confirm|"
                    r"turn\s+off|set|consult|replace|connect)\b",
                    line,
                    flags=re.I,
                )
            ]
            corrective_action = " ".join(dict.fromkeys(action_lines))[:1400]
            records.append(
                {
                    "code": code,
                    "message": message,
                    "cause": " ".join(raw_lines[:5])[:1400],
                    "corrective_action": corrective_action,
                    "raw_text": normalize_text(raw_block)[:4000],
                    "table_index": 0,
                    "row_index": match_index + 1,
                }
            )
        return records

    for table in page.tables:
        rows = table.get("rows", [])
        if not rows:
            continue
        header = [normalize_line(value).casefold() for value in rows[0]]
        table_context = " ".join(header)
        if not re.search(r"error\s+code|fault\s+code", table_context, flags=re.I):
            continue
        for row_index, row in enumerate(rows[1:], start=2):
            cells = [normalize_line(value) for value in row]
            joined = " | ".join(value for value in cells if value)
            sanitized = sanitize_error_text(joined)
            code_matches = plausible_error_matches(sanitized)
            if not code_matches:
                continue
            code_match = code_matches[0]
            code = code_match.group(1).upper() + ("H" if code_match.group(2) else "")
            nonempty = [value for value in cells if value]
            remainder = [value for value in nonempty if code not in value.upper()]
            records.append(
                {
                    "code": code,
                    "message": remainder[0] if remainder else "",
                    "cause": remainder[1] if len(remainder) > 1 else "",
                    "corrective_action": remainder[-1] if len(remainder) > 2 else "",
                    "raw_text": joined,
                    "table_index": table.get("index", 0),
                    "row_index": row_index,
                }
            )
    return records


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        f"""
        PRAGMA foreign_keys=ON;
        PRAGMA user_version={SCHEMA_VERSION};

        CREATE TABLE meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE manuals (
            manual_id TEXT PRIMARY KEY,
            manual_number TEXT NOT NULL,
            revision TEXT NOT NULL,
            published TEXT NOT NULL,
            title TEXT NOT NULL,
            language TEXT NOT NULL,
            manual_type TEXT NOT NULL,
            plc_models TEXT NOT NULL,
            task_types TEXT NOT NULL,
            priority INTEGER NOT NULL,
            source_file TEXT NOT NULL,
            source_bytes INTEGER NOT NULL,
            source_sha256 TEXT NOT NULL,
            official_url TEXT NOT NULL,
            pdf_pages INTEGER NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE page_artifacts (
            id INTEGER PRIMARY KEY,
            manual_id TEXT NOT NULL REFERENCES manuals(manual_id) ON DELETE CASCADE,
            pdf_page INTEGER NOT NULL,
            printed_page TEXT NOT NULL,
            chapter TEXT NOT NULL,
            section TEXT NOT NULL,
            outline_path TEXT NOT NULL,
            section_key TEXT NOT NULL,
            chunk_type TEXT NOT NULL,
            instruction_opcode TEXT NOT NULL,
            fnc_number TEXT NOT NULL,
            plain_text TEXT NOT NULL,
            clean_text TEXT NOT NULL,
            layout_text TEXT NOT NULL,
            compact_layout TEXT NOT NULL,
            word_geometry_json TEXT NOT NULL,
            visual_json TEXT NOT NULL,
            fidelity_flags TEXT NOT NULL,
            plain_text_sha256 TEXT NOT NULL,
            UNIQUE(manual_id, pdf_page)
        );

        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY,
            chunk_uid TEXT NOT NULL UNIQUE,
            manual_id TEXT NOT NULL REFERENCES manuals(manual_id) ON DELETE CASCADE,
            manual_number TEXT NOT NULL,
            revision TEXT NOT NULL,
            manual_title TEXT NOT NULL,
            manual_type TEXT NOT NULL,
            manual_priority INTEGER NOT NULL,
            language TEXT NOT NULL,
            plc_models TEXT NOT NULL,
            task_types TEXT NOT NULL,
            source_file TEXT NOT NULL,
            pdf_page INTEGER NOT NULL,
            pdf_page_end INTEGER NOT NULL,
            printed_page TEXT NOT NULL,
            printed_page_end TEXT NOT NULL,
            chapter TEXT NOT NULL,
            section TEXT NOT NULL,
            outline_path TEXT NOT NULL,
            section_key TEXT NOT NULL,
            chunk_type TEXT NOT NULL,
            instruction_opcode TEXT NOT NULL,
            fnc_number TEXT NOT NULL,
            block_index INTEGER NOT NULL,
            block_count INTEGER NOT NULL,
            source_pages_json TEXT NOT NULL,
            page_char_count INTEGER NOT NULL,
            page_text_sha256 TEXT NOT NULL,
            text TEXT NOT NULL,
            char_count INTEGER NOT NULL,
            text_sha256 TEXT NOT NULL,
            entities TEXT NOT NULL,
            entities_json TEXT NOT NULL,
            cjk_bigrams TEXT NOT NULL,
            fidelity_flags TEXT NOT NULL,
            UNIQUE(manual_id, section_key, block_index)
        );

        CREATE TABLE entity_index (
            entity_norm TEXT NOT NULL,
            entity TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            plc_models TEXT NOT NULL,
            task_types TEXT NOT NULL,
            manual_id TEXT NOT NULL REFERENCES manuals(manual_id) ON DELETE CASCADE,
            chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
            occurrences INTEGER NOT NULL CHECK(occurrences > 0),
            PRIMARY KEY(entity_norm, entity_type, chunk_id)
        ) WITHOUT ROWID;

        CREATE TABLE tables (
            id INTEGER PRIMARY KEY,
            manual_id TEXT NOT NULL REFERENCES manuals(manual_id) ON DELETE CASCADE,
            pdf_page INTEGER NOT NULL,
            table_index INTEGER NOT NULL,
            section_key TEXT NOT NULL,
            chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
            bbox_json TEXT NOT NULL,
            rows_json TEXT NOT NULL,
            table_text TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            column_count INTEGER NOT NULL,
            table_sha256 TEXT NOT NULL,
            UNIQUE(manual_id, pdf_page, table_index)
        );

        CREATE TABLE instructions (
            id INTEGER PRIMARY KEY,
            opcode TEXT NOT NULL,
            opcode_norm TEXT NOT NULL,
            fnc_number TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            variants_json TEXT NOT NULL,
            operands_json TEXT NOT NULL,
            completion_flags_json TEXT NOT NULL,
            restrictions_json TEXT NOT NULL,
            manual_id TEXT NOT NULL REFERENCES manuals(manual_id) ON DELETE CASCADE,
            manual_number TEXT NOT NULL,
            revision TEXT NOT NULL,
            page_start INTEGER NOT NULL,
            page_end INTEGER NOT NULL,
            source_pages_json TEXT NOT NULL,
            chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
            UNIQUE(opcode_norm, manual_id)
        );

        CREATE TABLE instruction_aliases (
            alias_norm TEXT NOT NULL,
            alias TEXT NOT NULL,
            alias_type TEXT NOT NULL,
            instruction_id INTEGER NOT NULL REFERENCES instructions(id) ON DELETE CASCADE,
            chunk_id INTEGER REFERENCES chunks(id) ON DELETE CASCADE,
            PRIMARY KEY(alias_norm, instruction_id)
        ) WITHOUT ROWID;

        CREATE TABLE device_records (
            id INTEGER PRIMARY KEY,
            device_norm TEXT NOT NULL,
            device TEXT NOT NULL,
            prefix TEXT NOT NULL,
            record_type TEXT NOT NULL,
            description TEXT NOT NULL,
            occurrences INTEGER NOT NULL,
            plc_models TEXT NOT NULL,
            source_manuals_json TEXT NOT NULL,
            chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
            UNIQUE(device_norm, record_type)
        );

        CREATE TABLE error_records (
            id INTEGER PRIMARY KEY,
            error_code TEXT NOT NULL,
            error_code_norm TEXT NOT NULL,
            message TEXT NOT NULL,
            cause TEXT NOT NULL,
            corrective_action TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            manual_id TEXT NOT NULL REFERENCES manuals(manual_id) ON DELETE CASCADE,
            manual_number TEXT NOT NULL,
            revision TEXT NOT NULL,
            pdf_page INTEGER NOT NULL,
            table_index INTEGER NOT NULL,
            row_index INTEGER NOT NULL,
            chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
            UNIQUE(error_code_norm, manual_id, pdf_page, table_index, row_index)
        );

        CREATE TABLE debug_cases (
            case_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            symptom TEXT NOT NULL,
            root_cause TEXT NOT NULL,
            checks_json TEXT NOT NULL,
            fix TEXT NOT NULL,
            entities_json TEXT NOT NULL,
            task_types TEXT NOT NULL,
            source_refs_json TEXT NOT NULL,
            plc_models TEXT NOT NULL,
            chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE
        ) WITHOUT ROWID;

        CREATE TABLE vector_embeddings (
            chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
            model TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL,
            vector_norm REAL NOT NULL,
            content_sha256 TEXT NOT NULL,
            PRIMARY KEY(chunk_id, model)
        ) WITHOUT ROWID;

        CREATE INDEX idx_pages_manual_section ON page_artifacts(manual_id, section_key, pdf_page);
        CREATE INDEX idx_chunks_manual_page ON chunks(manual_id, pdf_page, pdf_page_end);
        CREATE INDEX idx_chunks_section ON chunks(section_key, block_index);
        CREATE INDEX idx_chunks_opcode ON chunks(instruction_opcode, manual_priority DESC);
        CREATE INDEX idx_chunks_type ON chunks(chunk_type, manual_priority DESC);
        CREATE INDEX idx_entity_chunk ON entity_index(chunk_id);
        CREATE INDEX idx_entity_type_norm ON entity_index(entity_type, entity_norm);
        CREATE INDEX idx_entity_manual ON entity_index(manual_id, entity_norm);
        CREATE INDEX idx_tables_page ON tables(manual_id, pdf_page);
        CREATE INDEX idx_instruction_opcode ON instructions(opcode_norm, page_start);
        CREATE INDEX idx_alias_norm ON instruction_aliases(alias_norm);
        CREATE INDEX idx_device_prefix ON device_records(prefix, device_norm);
        CREATE INDEX idx_error_code ON error_records(error_code_norm);

        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            text,
            cjk_bigrams,
            entities,
            chapter,
            section,
            chunk_type,
            instruction_opcode,
            manual_title,
            content='chunks',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 0'
        );
        """
    )


def put_meta(connection: sqlite3.Connection, values: dict[str, Any]) -> None:
    connection.executemany(
        "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
        [
            (
                key,
                json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if isinstance(value, (dict, list, bool))
                else str(value),
            )
            for key, value in sorted(values.items())
        ],
    )


def insert_manual(
    connection: sqlite3.Connection,
    manual: ManualSpec,
    page_count: int,
) -> None:
    connection.execute(
        """
        INSERT INTO manuals(
            manual_id,manual_number,revision,published,title,language,manual_type,
            plc_models,task_types,priority,source_file,source_bytes,source_sha256,
            official_url,pdf_pages
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            manual.id,
            manual.manual_number,
            manual.revision,
            manual.published,
            manual.title,
            manual.language,
            manual.manual_type,
            manual.plc_models,
            TASK_TYPES,
            manual.priority,
            manual.path.name,
            manual.path.stat().st_size,
            sha256_file(manual.path),
            manual.official_url,
            page_count,
        ),
    )


def insert_page_artifacts(connection: sqlite3.Connection, pages: list[PageArtifact]) -> None:
    for page in pages:
        connection.execute(
            """
            INSERT INTO page_artifacts(
                manual_id,pdf_page,printed_page,chapter,section,outline_path,
                section_key,chunk_type,instruction_opcode,fnc_number,plain_text,
                clean_text,layout_text,compact_layout,word_geometry_json,
                visual_json,fidelity_flags,plain_text_sha256
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                page.manual.id,
                page.pdf_page,
                page.printed_page,
                page.chapter,
                page.section,
                page.outline_path,
                page.section_key,
                page.chunk_type,
                page.instruction_opcode,
                page.fnc_number,
                page.plain_text,
                page.clean_text,
                page.layout_text,
                page.compact_layout,
                json.dumps(page.words, ensure_ascii=False, separators=(",", ":")),
                json.dumps(page.visual, ensure_ascii=False, separators=(",", ":")),
                ",".join(sorted(page.fidelity_flags)),
                hashlib.sha256(page.plain_text.encode("utf-8")).hexdigest(),
            ),
        )
        for table in page.tables:
            rows = table.get("rows", [])
            width = max((len(row) for row in rows), default=0)
            table_text = str(table.get("text", ""))
            connection.execute(
                """
                INSERT INTO tables(
                    manual_id,pdf_page,table_index,section_key,chunk_id,bbox_json,
                    rows_json,table_text,row_count,column_count,table_sha256
                ) VALUES(?,?,?,?,NULL,?,?,?,?,?,?)
                """,
                (
                    page.manual.id,
                    page.pdf_page,
                    int(table.get("index", 0)),
                    page.section_key,
                    json.dumps(table.get("bbox", []), separators=(",", ":")),
                    json.dumps(rows, ensure_ascii=False, separators=(",", ":")),
                    table_text,
                    len(rows),
                    width,
                    hashlib.sha256(table_text.encode("utf-8")).hexdigest(),
                ),
            )


def upsert_entity_rows(
    connection: sqlite3.Connection,
    *,
    manual_id: str,
    plc_models: str,
    chunk_id: int,
    entities: Counter[tuple[str, str]],
) -> None:
    connection.executemany(
        """
        INSERT INTO entity_index(
            entity_norm,entity,entity_type,plc_models,task_types,manual_id,
            chunk_id,occurrences
        ) VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(entity_norm,entity_type,chunk_id)
        DO UPDATE SET occurrences=entity_index.occurrences+excluded.occurrences
        """,
        [
            (
                entity.casefold(),
                entity,
                kind,
                plc_models,
                TASK_TYPES,
                manual_id,
                chunk_id,
                count,
            )
            for (entity, kind), count in sorted(entities.items())
        ],
    )


def insert_chunks(
    connection: sqlite3.Connection,
    pages: list[PageArtifact],
    drafts: list[ChunkDraft],
    instruction_re: re.Pattern[str],
) -> tuple[dict[tuple[str, int], int], dict[tuple[str, str], int], Counter[str]]:
    counts_by_section = Counter((draft.manual.id, draft.section_key) for draft in drafts)
    indexes_by_section: Counter[tuple[str, str]] = Counter()
    page_first_chunk: dict[tuple[str, int], int] = {}
    instruction_first_chunk: dict[tuple[str, str], int] = {}
    stats: Counter[str] = Counter()
    page_lookup = {(page.manual.id, page.pdf_page): page for page in pages}
    for draft in drafts:
        section_identity = (draft.manual.id, draft.section_key)
        indexes_by_section[section_identity] += 1
        block_index = indexes_by_section[section_identity]
        block_count = counts_by_section[section_identity]
        entities = extract_entities(
            draft.text,
            instruction_re,
            chunk_type=draft.chunk_type,
        )
        entity_tokens = sorted({entity for entity, _kind in entities})
        entities_json = [
            {"entity": entity, "type": kind, "occurrences": count}
            for (entity, kind), count in sorted(entities.items())
        ]
        bigrams, bigram_count = cjk_bigrams(draft.text)
        page_plain = "".join(
            page_lookup[(draft.manual.id, page_number)].plain_text
            for page_number in draft.pages
        )
        chunk_uid = (
            f"{draft.manual.id}:{hashlib.sha1(draft.section_key.encode('utf-8')).hexdigest()[:10]}:"
            f"b{block_index:03d}"
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
                draft.manual.id,
                draft.manual.manual_number,
                draft.manual.revision,
                draft.manual.title,
                draft.manual.manual_type,
                draft.manual.priority,
                draft.manual.language,
                draft.manual.plc_models,
                TASK_TYPES,
                draft.manual.path.name,
                min(draft.pages),
                max(draft.pages),
                draft.printed_pages[0] if draft.printed_pages else "",
                draft.printed_pages[-1] if draft.printed_pages else "",
                draft.chapter,
                draft.section,
                draft.outline_path,
                draft.section_key,
                draft.chunk_type,
                draft.instruction_opcode,
                draft.fnc_number,
                block_index,
                block_count,
                json.dumps(draft.pages, separators=(",", ":")),
                len(page_plain),
                hashlib.sha256(page_plain.encode("utf-8")).hexdigest(),
                draft.text,
                len(draft.text),
                hashlib.sha256(draft.text.encode("utf-8")).hexdigest(),
                " ".join(entity_tokens),
                json.dumps(entities_json, ensure_ascii=False, separators=(",", ":")),
                bigrams,
                ",".join(sorted(draft.fidelity_flags)),
            ),
        )
        chunk_id = int(cursor.lastrowid)
        upsert_entity_rows(
            connection,
            manual_id=draft.manual.id,
            plc_models=draft.manual.plc_models,
            chunk_id=chunk_id,
            entities=entities,
        )
        for page_number in draft.pages:
            page_first_chunk.setdefault((draft.manual.id, page_number), chunk_id)
        if draft.instruction_opcode:
            instruction_first_chunk.setdefault(
                (draft.manual.id, draft.instruction_opcode), chunk_id
            )
        stats["chunks"] += 1
        stats["characters"] += len(draft.text)
        stats["entity_rows"] += len(entities)
        stats["entity_occurrences"] += sum(entities.values())
        stats["cjk_bigrams"] += bigram_count

    for (manual_id, page_number), chunk_id in page_first_chunk.items():
        connection.execute(
            "UPDATE tables SET chunk_id=? WHERE manual_id=? AND pdf_page=?",
            (chunk_id, manual_id, page_number),
        )
    return page_first_chunk, instruction_first_chunk, stats


def insert_instruction_records(
    connection: sqlite3.Connection,
    pages: list[PageArtifact],
    instruction_re_by_manual: dict[str, re.Pattern[str]],
    instruction_first_chunk: dict[tuple[str, str], int],
) -> int:
    grouped: dict[tuple[str, str], list[PageArtifact]] = defaultdict(list)
    for page in pages:
        if page.instruction_opcode:
            grouped[(page.manual.id, page.instruction_opcode)].append(page)
    inserted = 0
    for (manual_id, opcode), instruction_pages in sorted(grouped.items()):
        instruction_pages.sort(key=lambda item: item.pdf_page)
        manual = instruction_pages[0].manual
        instruction_re = instruction_re_by_manual[manual_id]
        variants = instruction_variants(instruction_pages, opcode, instruction_re)
        operands = parse_operand_schema(instruction_pages, opcode)
        completion_flags = instruction_completion_flags(instruction_pages)
        restrictions = instruction_restrictions(instruction_pages)
        title = next(
            (page.instruction_title for page in instruction_pages if page.instruction_title),
            instruction_pages[0].section,
        )
        fnc_number = next(
            (page.fnc_number for page in instruction_pages if page.fnc_number), ""
        )
        source_pages = sorted({page.pdf_page for page in instruction_pages})
        chunk_id = instruction_first_chunk.get((manual_id, opcode))
        cursor = connection.execute(
            """
            INSERT INTO instructions(
                opcode,opcode_norm,fnc_number,title,summary,variants_json,
                operands_json,completion_flags_json,restrictions_json,manual_id,
                manual_number,revision,page_start,page_end,source_pages_json,chunk_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                opcode,
                opcode.casefold(),
                fnc_number,
                title,
                instruction_summary(instruction_pages),
                json.dumps(variants, ensure_ascii=False, separators=(",", ":")),
                json.dumps(operands, ensure_ascii=False, separators=(",", ":")),
                json.dumps(completion_flags, ensure_ascii=False, separators=(",", ":")),
                json.dumps(restrictions, ensure_ascii=False, separators=(",", ":")),
                manual_id,
                manual.manual_number,
                manual.revision,
                min(source_pages),
                max(source_pages),
                json.dumps(source_pages, separators=(",", ":")),
                chunk_id,
            ),
        )
        instruction_id = int(cursor.lastrowid)
        aliases: dict[str, str] = {opcode: "opcode"}
        aliases.update({variant: "variant" for variant in variants})
        if fnc_number:
            aliases[f"FNC {int(fnc_number):02d}"] = "fnc"
            aliases[f"FNC {int(fnc_number)}"] = "fnc"
        if title:
            aliases[title] = "title"
        for alias in INSTRUCTION_ZH_ALIASES.get(opcode, []):
            aliases[alias] = "zh_alias"
        connection.executemany(
            """
            INSERT OR IGNORE INTO instruction_aliases(
                alias_norm,alias,alias_type,instruction_id,chunk_id
            ) VALUES(?,?,?,?,?)
            """,
            [
                (
                    normalize_line(alias).casefold(),
                    normalize_line(alias),
                    alias_type,
                    instruction_id,
                    chunk_id,
                )
                for alias, alias_type in aliases.items()
                if normalize_line(alias)
            ],
        )
        if chunk_id is not None:
            operand_summary = "; ".join(
                f"{item.get('position', '')}: {item.get('description', '')}"
                + (f" [{item.get('data_type')}]" if item.get("data_type") else "")
                + (" applicable=" + ",".join(item.get("applicable_devices") or [])
                   if item.get("applicable_devices") else "")
                for item in operands
            )[:900]
            restriction_summary = " | ".join(restrictions[:2])[:260]
            structured_lines = ["[STRUCTURED INSTRUCTION RECORD]"]
            if operand_summary:
                structured_lines.append(f"OPERANDS: {operand_summary}")
            if completion_flags:
                structured_lines.append(
                    f"COMPLETION_FLAGS: {', '.join(completion_flags)}"
                )
            if restriction_summary:
                structured_lines.append(f"KEY_RESTRICTIONS: {restriction_summary}")
            old_row = connection.execute(
                "SELECT text,fidelity_flags FROM chunks WHERE id=?",
                (chunk_id,),
            ).fetchone()
            if old_row and len(structured_lines) > 1:
                enhanced_text = "\n".join(structured_lines) + "\n\n" + str(old_row[0])
                enhanced_entities = extract_entities(
                    enhanced_text,
                    instruction_re,
                    chunk_type="instruction",
                )
                entity_tokens = sorted(
                    {entity for entity, _kind in enhanced_entities}
                )
                entity_json = [
                    {"entity": entity, "type": kind, "occurrences": count}
                    for (entity, kind), count in sorted(enhanced_entities.items())
                ]
                bigrams, _bigram_count = cjk_bigrams(enhanced_text)
                connection.execute(
                    "DELETE FROM entity_index WHERE chunk_id=?",
                    (chunk_id,),
                )
                upsert_entity_rows(
                    connection,
                    manual_id=manual_id,
                    plc_models=manual.plc_models,
                    chunk_id=chunk_id,
                    entities=enhanced_entities,
                )
                connection.execute(
                    """
                    UPDATE chunks
                    SET text=?,char_count=?,text_sha256=?,entities=?,entities_json=?,
                        cjk_bigrams=?,fidelity_flags=?
                    WHERE id=?
                    """,
                    (
                        enhanced_text,
                        len(enhanced_text),
                        hashlib.sha256(enhanced_text.encode("utf-8")).hexdigest(),
                        " ".join(entity_tokens),
                        json.dumps(
                            entity_json,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        bigrams,
                        ",".join(
                            sorted(
                                set(str(old_row[1] or "").split(","))
                                | {"structured_instruction_record"}
                            )
                        ).strip(","),
                        chunk_id,
                    ),
                )
        inserted += 1
    return inserted


def insert_device_records(connection: sqlite3.Connection) -> int:
    rows = connection.execute(
        """
        SELECT e.entity_norm, MIN(e.entity), e.entity_type, SUM(e.occurrences),
               MIN(e.chunk_id), GROUP_CONCAT(DISTINCT e.manual_id),
               MIN(c.section)
        FROM entity_index e
        JOIN chunks c ON c.id=e.chunk_id
        WHERE e.entity_type IN ('device','device_range')
        GROUP BY e.entity_norm,e.entity_type
        ORDER BY e.entity_norm
        """
    ).fetchall()
    inserted = 0
    for entity_norm, entity, entity_type, occurrences, chunk_id, manuals, section in rows:
        prefix_match = re.match(r"(?:ER|SM|SD|TS|TC|CS|CC|[A-Z]+)", str(entity), re.I)
        prefix = prefix_match.group(0).upper() if prefix_match else ""
        description = normalize_line(str(section or DEVICE_FAMILIES.get(prefix, "")))
        source_manuals = sorted(set(str(manuals or "").split(",")))
        connection.execute(
            """
            INSERT INTO device_records(
                device_norm,device,prefix,record_type,description,occurrences,
                plc_models,source_manuals_json,chunk_id
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                entity_norm,
                entity,
                prefix,
                entity_type,
                description,
                int(occurrences),
                "FX3S,FX3G,FX3GC,FX3U,FX3UC",
                json.dumps(source_manuals, separators=(",", ":")),
                chunk_id,
            ),
        )
        inserted += 1
    for prefix, description in DEVICE_FAMILIES.items():
        connection.execute(
            """
            INSERT OR IGNORE INTO device_records(
                device_norm,device,prefix,record_type,description,occurrences,
                plc_models,source_manuals_json,chunk_id
            ) VALUES(?,?,?,?,?,0,?,?,NULL)
            """,
            (
                f"family:{prefix.casefold()}",
                prefix,
                prefix,
                "device_family",
                description,
                "FX3S,FX3G,FX3GC,FX3U,FX3UC",
                "[]",
            ),
        )
        inserted += 1
    return inserted


def insert_error_records(
    connection: sqlite3.Connection,
    pages: list[PageArtifact],
    page_first_chunk: dict[tuple[str, int], int],
) -> int:
    inserted = 0
    for page in pages:
        chunk_id = page_first_chunk.get((page.manual.id, page.pdf_page))
        for record in parse_error_records(page):
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO error_records(
                    error_code,error_code_norm,message,cause,corrective_action,
                    raw_text,manual_id,manual_number,revision,pdf_page,table_index,
                    row_index,chunk_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record["code"],
                    record["code"].casefold(),
                    record["message"],
                    record["cause"],
                    record["corrective_action"],
                    record["raw_text"],
                    page.manual.id,
                    page.manual.manual_number,
                    page.manual.revision,
                    page.pdf_page,
                    int(record["table_index"]),
                    int(record["row_index"]),
                    chunk_id,
                ),
            )
            if cursor.rowcount and chunk_id is not None:
                upsert_entity_rows(
                    connection,
                    manual_id=page.manual.id,
                    plc_models=page.manual.plc_models,
                    chunk_id=chunk_id,
                    entities=Counter({(record["code"], "error_code"): 1}),
                )
                inserted += 1
    return inserted


def insert_debug_cases(
    connection: sqlite3.Connection,
    debug_path: Path,
    instruction_re: re.Pattern[str],
) -> tuple[int, Counter[str]]:
    payload = json.loads(debug_path.read_text(encoding="utf-8"))
    cases = list(payload.get("cases", []))
    manual_id = "curated_debug_cases"
    models = "FX3G,FX3U,FX3UC"
    source_hash = sha256_file(debug_path)
    connection.execute(
        """
        INSERT INTO manuals(
            manual_id,manual_number,revision,published,title,language,manual_type,
            plc_models,task_types,priority,source_file,source_bytes,source_sha256,
            official_url,pdf_pages
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            manual_id,
            "CURATED-DEBUG",
            "1",
            datetime.now(timezone.utc).date().isoformat(),
            "FX3U Curated Debugging Cases",
            "zh",
            "debug_cases",
            models,
            "debug,program_review,analysis",
            96,
            debug_path.name,
            debug_path.stat().st_size,
            source_hash,
            "",
            0,
        ),
    )
    stats: Counter[str] = Counter()
    for index, case in enumerate(cases, start=1):
        checks = [str(value) for value in case.get("checks", [])]
        entities_list = [str(value) for value in case.get("entities", [])]
        source_refs = [str(value) for value in case.get("source_refs", [])]
        tasks = [str(value) for value in case.get("tasks", ["debug"])]
        text = "\n".join(
            [
                f"DEBUG CASE: {case['title']}",
                f"CASE_ID: {case['id']}",
                f"SYMPTOM: {case['symptom']}",
                f"ROOT_CAUSE: {case['root_cause']}",
                "CHECKS:",
                *(f"- {value}" for value in checks),
                f"FIX: {case['fix']}",
                f"ENTITIES: {', '.join(entities_list)}",
                f"SOURCE_REFS: {', '.join(source_refs)}",
            ]
        )
        entity_counts = extract_entities(
            text,
            instruction_re,
            chunk_type="debug_case",
            explicit_entities=entities_list,
        )
        entity_tokens = sorted({entity for entity, _kind in entity_counts})
        entity_json = [
            {"entity": entity, "type": kind, "occurrences": count}
            for (entity, kind), count in sorted(entity_counts.items())
        ]
        bigrams, bigram_count = cjk_bigrams(text)
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
                f"debug:{case['id']}",
                manual_id,
                "CURATED-DEBUG",
                "1",
                "FX3U Curated Debugging Cases",
                "debug_cases",
                96,
                "zh",
                models,
                ",".join(tasks),
                debug_path.name,
                0,
                0,
                "",
                "",
                "Debugging Cases",
                str(case["title"]),
                f"Debugging Cases > {case['title']}",
                f"debug:{case['id']}",
                "debug_case",
                next((value.upper() for value in entities_list if instruction_re.fullmatch(value)), ""),
                "",
                1,
                1,
                "[]",
                len(text),
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                text,
                len(text),
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                " ".join(entity_tokens),
                json.dumps(entity_json, ensure_ascii=False, separators=(",", ":")),
                bigrams,
                "curated,structured",
            ),
        )
        chunk_id = int(cursor.lastrowid)
        upsert_entity_rows(
            connection,
            manual_id=manual_id,
            plc_models=models,
            chunk_id=chunk_id,
            entities=entity_counts,
        )
        connection.execute(
            """
            INSERT INTO debug_cases(
                case_id,title,symptom,root_cause,checks_json,fix,entities_json,
                task_types,source_refs_json,plc_models,chunk_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                case["id"],
                case["title"],
                case["symptom"],
                case["root_cause"],
                json.dumps(checks, ensure_ascii=False, separators=(",", ":")),
                case["fix"],
                json.dumps(entities_list, ensure_ascii=False, separators=(",", ":")),
                ",".join(tasks),
                json.dumps(source_refs, ensure_ascii=False, separators=(",", ":")),
                models,
                chunk_id,
            ),
        )
        stats["chunks"] += 1
        stats["characters"] += len(text)
        stats["entity_rows"] += len(entity_counts)
        stats["entity_occurrences"] += sum(entity_counts.values())
        stats["cjk_bigrams"] += bigram_count
    return len(cases), stats


def _scalar(connection: sqlite3.Connection, sql: str, parameters: tuple[Any, ...] = ()) -> Any:
    row = connection.execute(sql, parameters).fetchone()
    return row[0] if row else None


def verify_database(
    connection: sqlite3.Connection,
    *,
    specs: list[ManualSpec],
    expected_pages: int,
    expected_debug_cases: int,
) -> dict[str, Any]:
    checks: dict[str, Any] = {}

    integrity = str(_scalar(connection, "PRAGMA integrity_check") or "")
    checks["integrity_check"] = integrity
    if integrity.lower() != "ok":
        raise RuntimeError(f"SQLite integrity check failed: {integrity}")

    foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    checks["foreign_key_violations"] = len(foreign_key_violations)
    if foreign_key_violations:
        raise RuntimeError(f"foreign key violations: {foreign_key_violations[:5]}")

    counts = {
        table: int(_scalar(connection, f"SELECT COUNT(*) FROM {table}") or 0)
        for table in (
            "manuals",
            "page_artifacts",
            "chunks",
            "entity_index",
            "tables",
            "instructions",
            "instruction_aliases",
            "device_records",
            "error_records",
            "debug_cases",
            "vector_embeddings",
            "chunks_fts",
        )
    }
    checks["counts"] = counts
    expected_manuals = len(specs) + (1 if expected_debug_cases else 0)
    if counts["manuals"] != expected_manuals:
        raise RuntimeError(
            f"manual count mismatch: {counts['manuals']} != {expected_manuals}"
        )
    if counts["page_artifacts"] != expected_pages:
        raise RuntimeError(
            f"page artifact mismatch: {counts['page_artifacts']} != {expected_pages}"
        )
    if counts["chunks"] == 0 or counts["chunks_fts"] != counts["chunks"]:
        raise RuntimeError(
            f"FTS row mismatch: chunks={counts['chunks']} fts={counts['chunks_fts']}"
        )
    if counts["debug_cases"] != expected_debug_cases:
        raise RuntimeError(
            f"debug case mismatch: {counts['debug_cases']} != {expected_debug_cases}"
        )

    manual_types = {spec.manual_type for spec in specs}
    if manual_types.intersection({"programming", "structured_instruction", "structured_function"}):
        if counts["instructions"] == 0 or counts["instruction_aliases"] == 0:
            raise RuntimeError("instruction structure was not populated")
    if counts["device_records"] == 0:
        raise RuntimeError("device structure was not populated")

    selected_ids = {spec.id for spec in specs}
    if "fx3_programming_r" in selected_ids:
        plsy_row = connection.execute(
            """
            SELECT operands_json,page_start,page_end,chunk_id
            FROM instructions
            WHERE opcode_norm='plsy' AND manual_id='fx3_programming_r'
            """
        ).fetchone()
        if not plsy_row:
            raise RuntimeError("PLSY structured instruction record is missing")
        operand_positions = {
            str(item.get("position", "")).upper()
            for item in json.loads(str(plsy_row[0]) or "[]")
        }
        checks["plsy_operand_positions"] = sorted(operand_positions)
        if not {"S1", "S2", "D"}.issubset(operand_positions):
            raise RuntimeError(
                f"PLSY operand extraction lost fields: {sorted(operand_positions)}"
            )
        wrong_s_entities = int(
            _scalar(
                connection,
                """
                SELECT COUNT(*)
                FROM entity_index e
                JOIN chunks c ON c.id=e.chunk_id
                WHERE c.manual_id='fx3_programming_r'
                  AND c.instruction_opcode='PLSY'
                  AND e.entity_norm IN ('s1','s2')
                  AND e.entity_type='device'
                """,
            )
            or 0
        )
        placeholder_entities = int(
            _scalar(
                connection,
                """
                SELECT COUNT(*)
                FROM entity_index e
                JOIN chunks c ON c.id=e.chunk_id
                WHERE c.manual_id='fx3_programming_r'
                  AND c.instruction_opcode='PLSY'
                  AND e.entity_norm IN ('s1','s2')
                  AND e.entity_type='operand_placeholder'
                """,
            )
            or 0
        )
        checks["plsy_s1_s2_wrong_device_rows"] = wrong_s_entities
        checks["plsy_s1_s2_placeholder_rows"] = placeholder_entities
        if wrong_s_entities or placeholder_entities < 2:
            raise RuntimeError(
                "S1/S2 entity typing failed for PLSY: "
                f"device={wrong_s_entities}, placeholder={placeholder_entities}"
            )
        first_chunk = connection.execute(
            "SELECT text,char_count FROM chunks WHERE id=?",
            (int(plsy_row[3]),),
        ).fetchone()
        checks["plsy_first_chunk_characters"] = int(first_chunk[1]) if first_chunk else 0
        checks["plsy_first_chunk_has_completion_flag"] = bool(
            first_chunk and "M8029" in str(first_chunk[0])
        )
        if not first_chunk or "M8029" not in str(first_chunk[0]):
            raise RuntimeError("PLSY first retrieval chunk lost completion flag M8029")
        if int(first_chunk[1]) > 5900:
            raise RuntimeError(
                f"PLSY first retrieval chunk exceeds prompt budget target: {first_chunk[1]}"
            )
        plsy_tables = int(
            _scalar(
                connection,
                """
                SELECT COUNT(*) FROM tables
                WHERE manual_id='fx3_programming_r' AND pdf_page BETWEEN ? AND ?
                """,
                (int(plsy_row[1]), int(plsy_row[2])),
            )
            or 0
        )
        checks["plsy_table_records"] = plsy_tables
        if plsy_tables == 0:
            raise RuntimeError("PLSY section has no preserved table records")

    if "fx3_positioning_k" in selected_ids:
        positioning_opcodes = {
            str(row[0]).upper()
            for row in connection.execute(
                "SELECT opcode FROM instructions "
                "WHERE manual_id='fx3_positioning_k'"
            ).fetchall()
        }
        required_opcodes = {"DRVI", "DRVA", "ZRN", "DSZR", "DVIT"}
        checks["positioning_instruction_opcodes"] = sorted(positioning_opcodes)
        missing_opcodes = required_opcodes - positioning_opcodes
        if missing_opcodes:
            raise RuntimeError(
                "positioning instruction extraction lost opcodes: "
                f"{sorted(missing_opcodes)}"
            )

        required_devices = {"d8342", "d8343", "d8345", "d8348", "d8349", "m8336"}
        indexed_devices = {
            str(row[0]).casefold()
            for row in connection.execute(
                """
                SELECT DISTINCT e.entity_norm
                FROM entity_index e
                JOIN chunks c ON c.id=e.chunk_id
                WHERE c.manual_id='fx3_positioning_k'
                  AND e.entity_norm IN ('d8342','d8343','d8345','d8348','d8349','m8336')
                """
            ).fetchall()
        }
        checks["positioning_indexed_devices"] = sorted(indexed_devices)
        missing_devices = required_devices - indexed_devices
        if missing_devices:
            raise RuntimeError(
                "positioning device extraction lost entities: "
                f"{sorted(missing_devices)}"
            )

        wrong_positioning_operands = int(
            _scalar(
                connection,
                """
                SELECT COUNT(*)
                FROM entity_index e
                JOIN chunks c ON c.id=e.chunk_id
                WHERE c.manual_id='fx3_positioning_k'
                  AND c.chunk_type='instruction'
                  AND e.entity_norm IN ('s1','s2','s3')
                  AND e.entity_type='device'
                """,
            )
            or 0
        )
        checks["positioning_operand_wrong_device_rows"] = wrong_positioning_operands
        if wrong_positioning_operands:
            raise RuntimeError(
                "positioning S1/S2/S3 operands were indexed as state relays"
            )

    checks["vector_status"] = "schema_ready_embeddings_deferred"
    return checks


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    os.close(descriptor)
    temp_path = Path(temp_name)
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def build(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    config_path = args.sources_config.expanduser().resolve()
    debug_path = args.debug_cases.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    manifest_path = args.manifest.expanduser().resolve()
    specs = load_manual_specs(config_path, args.manual_id)
    if not debug_path.is_file():
        raise FileNotFoundError(f"debug case source not found: {debug_path}")
    debug_payload = json.loads(debug_path.read_text(encoding="utf-8"))
    expected_debug_cases = len(debug_payload.get("cases", []))

    all_pages: list[PageArtifact] = []
    all_drafts: list[ChunkDraft] = []
    vocabulary_by_manual: dict[str, set[str]] = {}
    instruction_re_by_manual: dict[str, re.Pattern[str]] = {}
    manual_stats: dict[str, dict[str, Any]] = {}
    for manual in specs:
        pages, vocabulary, instruction_re, outline_entries = extract_manual_pages(
            manual,
            skip_tables=bool(args.skip_tables),
            progress_every=max(1, int(args.progress_every)),
            started=started,
        )
        drafts = make_section_chunks(
            pages,
            target_chars=int(args.target_chars),
            max_chars=int(args.max_chars),
        )
        all_pages.extend(pages)
        all_drafts.extend(drafts)
        vocabulary_by_manual[manual.id] = vocabulary
        instruction_re_by_manual[manual.id] = instruction_re
        manual_stats[manual.id] = {
            "pdf_pages": len(pages),
            "outline_entries": len(outline_entries),
            "chunks": len(drafts),
            "instruction_pages": sum(bool(page.instruction_opcode) for page in pages),
            "high_fidelity_pages": sum(
                bool(page.words or page.tables or page.visual.get("geometry_extracted"))
                for page in pages
            ),
            "table_records": sum(len(page.tables) for page in pages),
            "word_geometry_records": sum(len(page.words) for page in pages),
        }
        print(
            f"[{manual.id}] section chunks={len(drafts)} "
            f"tables={manual_stats[manual.id]['table_records']}",
            flush=True,
        )

    global_vocabulary: set[str] = set(BASE_INSTRUCTIONS)
    for vocabulary in vocabulary_by_manual.values():
        global_vocabulary.update(vocabulary)
    global_instruction_re = compile_instruction_re(global_vocabulary)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=str(output_path.parent)
    )
    os.close(descriptor)
    temp_path = Path(temp_name)
    connection: sqlite3.Connection | None = None
    verification: dict[str, Any]
    actual_stats: dict[str, int]
    try:
        connection = sqlite3.connect(str(temp_path))
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=MEMORY")
        connection.execute("PRAGMA synchronous=OFF")
        create_schema(connection)
        page_count_by_manual = Counter(page.manual.id for page in all_pages)
        for manual in specs:
            insert_manual(connection, manual, int(page_count_by_manual[manual.id]))
        insert_page_artifacts(connection, all_pages)
        page_first_chunk, instruction_first_chunk, chunk_stats = insert_chunks(
            connection,
            all_pages,
            all_drafts,
            global_instruction_re,
        )
        instruction_count = insert_instruction_records(
            connection,
            all_pages,
            instruction_re_by_manual,
            instruction_first_chunk,
        )
        error_count = insert_error_records(connection, all_pages, page_first_chunk)
        device_count = insert_device_records(connection)
        debug_count, debug_stats = insert_debug_cases(
            connection,
            debug_path,
            global_instruction_re,
        )
        connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        source_manifest = [
            {
                "id": manual.id,
                "manual_number": manual.manual_number,
                "revision": manual.revision,
                "published": manual.published,
                "title": manual.title,
                "manual_type": manual.manual_type,
                "plc_models": manual.plc_models.split(","),
                "priority": manual.priority,
                "source_file": manual.path.name,
                "source_bytes": manual.path.stat().st_size,
                "source_sha256": sha256_file(manual.path),
                "official_url": manual.official_url,
                **manual_stats[manual.id],
            }
            for manual in specs
        ]
        put_meta(
            connection,
            {
                "schema_version": SCHEMA_VERSION,
                "builder_version": BUILDER_VERSION,
                "built_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_config_sha256": sha256_file(config_path),
                "debug_cases_sha256": sha256_file(debug_path),
                "source_manuals": source_manifest,
                "chunk_strategy": "section_and_instruction_aware",
                "fidelity_strategy": {
                    "plain_text": True,
                    "layout_text": True,
                    "private_glyph_placeholders": True,
                    "table_rows_and_bboxes": not bool(args.skip_tables),
                    "word_geometry_on_high_value_pages": True,
                    "ladder_diagram_text_windows": True,
                },
                "structured_tables": [
                    "instructions",
                    "instruction_aliases",
                    "device_records",
                    "error_records",
                    "debug_cases",
                ],
                "vector_status": "schema_ready_embeddings_deferred_until_benchmark",
                "vector_model": "",
                "vector_dimensions": 0,
            },
        )
        connection.commit()
        verification = verify_database(
            connection,
            specs=specs,
            expected_pages=len(all_pages),
            expected_debug_cases=expected_debug_cases,
        )
        actual_stats = {
            "chunks": int(_scalar(connection, "SELECT COUNT(*) FROM chunks") or 0),
            "characters": int(
                _scalar(connection, "SELECT COALESCE(SUM(char_count),0) FROM chunks") or 0
            ),
            "entities": int(_scalar(connection, "SELECT COUNT(*) FROM entity_index") or 0),
            "tables": int(_scalar(connection, "SELECT COUNT(*) FROM tables") or 0),
        }
        connection.commit()
        connection.execute("ANALYZE")
        connection.commit()
        connection.execute("VACUUM")
        connection.close()
        connection = None
        os.replace(temp_path, output_path)
    finally:
        if connection is not None:
            connection.close()
        if temp_path.exists():
            temp_path.unlink()

    elapsed = time.perf_counter() - started
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": output_path.name,
        "database_bytes": output_path.stat().st_size,
        "database_sha256": sha256_file(output_path),
        "primary_manual": next(
            (item for item in source_manifest if item["id"] == "fx3_programming_r"),
            source_manifest[0],
        ),
        "manuals": source_manifest,
        "chunk_strategy": {
            "name": "section_and_instruction_aware",
            "target_chars": int(args.target_chars),
            "max_chars": int(args.max_chars),
            "cross_page_within_section": True,
        },
        "fidelity": {
            "plain_and_layout_text": True,
            "table_rows_and_bboxes": not bool(args.skip_tables),
            "word_geometry_high_value_pages": True,
            "ladder_diagram_text_windows": True,
            "lossy_private_glyphs_replaced_with_codepoint_tokens": True,
        },
        "structured": {
            "instructions": instruction_count,
            "devices": device_count,
            "errors": error_count,
            "debug_cases": debug_count,
        },
        "retrieval": {
            "entity_index": True,
            "bm25_fts5": True,
            "vector_schema": True,
            "dense_embeddings": False,
            "reranker": "runtime_heuristic",
            "vector_status": "deferred_until_foundation_benchmark",
        },
        "stats": {
            "pages": len(all_pages),
            "manual_chunks": int(actual_stats["chunks"] - debug_stats["chunks"]),
            "debug_chunks": int(debug_stats["chunks"]),
            "characters": actual_stats["characters"],
            "entities": actual_stats["entities"],
            "tables": actual_stats["tables"],
        },
        "verification": verification,
        "build_seconds": round(elapsed, 3),
    }
    atomic_write_json(manifest_path, manifest)
    print(
        f"Built {output_path} pages={len(all_pages)} chunks={verification['counts']['chunks']} "
        f"instructions={instruction_count} debug_cases={debug_count} elapsed={elapsed:.1f}s",
        flush=True,
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        build(args)
    except Exception as error:
        print(f"build failed: {error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
