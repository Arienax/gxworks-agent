"""Explicit runtime knowledge allowlist, shared by all package collectors."""
from pathlib import Path
from contextlib import closing
import json
import sqlite3
import gzip
import re
from urllib.parse import unquote, urlsplit

FILES = (
    ("resources/knowledge/runtime/plc_facts.sqlite", "knowledge/runtime"),
    ("resources/knowledge/runtime/plc_facts_lsa.npz", "knowledge/runtime"),
    ("resources/knowledge/runtime/manifest.json", "knowledge/runtime"),
    ("resources/knowledge/runtime/original_cases.json", "knowledge/runtime"),
    ("resources/knowledge/runtime/diagnostic_facts.json", "knowledge/runtime"),
    ("resources/knowledge/THIRD_PARTY_NOTICES.md", "knowledge"),
    ("resources/knowledge/function_relations.json", "knowledge"),
)
INSTRUCTION_FILES = (
    'common.json', 'device_definitions.json', 'fx3u_contract_promotions.json', 'fx3u_instruction_quarantine.json',
    'fx3u_operand_semantic_promotions.json', 'fx3u_verified_opcodes.json', 'fx3u.json', 'fx5u.json',
    'instruction_behavior_facts.json', 'instruction_capabilities.json', 'instruction_definitions.json.gz',
    'instruction_source_authority.json', 'modifier_rules.json', 'operand_semantic_coverage_summary.json',
    'fx3u_step_widths.json',
)


def runtime_viewer_data(root):
    """Collect only the existing simulator viewer's local asset graph."""
    directory = (Path(root) / 'web/dist').resolve()
    pending, visited = [directory / 'simulator.html'], set()
    pattern = re.compile(r'''["']([^"']+\.(?:js|css|woff2?|svg|png|ico|wasm)(?:\?[^"']*)?)["']''')
    while pending:
        path = pending.pop().resolve()
        if path in visited:
            continue
        if not path.is_relative_to(directory) or path.is_symlink() or not path.is_file():
            raise ValueError('Invalid simulator viewer asset: ' + str(path))
        visited.add(path)
        if path.suffix not in {'.html', '.js', '.css'}:
            continue
        content = path.read_text(encoding='utf-8')
        references = pattern.findall(content) + re.findall(r'url\(([^)]+)\)', content)
        for reference in references:
            parts = urlsplit(reference.strip(' "\''))
            if parts.scheme or parts.netloc:
                continue
            relative = unquote(parts.path)
            candidate = (directory / relative.lstrip('/') if relative.startswith(('/assets/', 'assets/')) else path.parent / relative).resolve()
            if candidate.is_relative_to(directory) and candidate.is_file():
                pending.append(candidate)
    if directory / 'index.html' in visited:
        raise ValueError('Simulator viewer must not require the Web workbench entry')
    return [(str(path), ('web/dist/' + path.relative_to(directory).parent.as_posix()).rstrip('/.')) for path in sorted(visited)]


def runtime_instruction_data(root):
    root = Path(root)
    result = [(str(root / 'resources/instructions/mitsubishi' / name), 'resources/instructions/mitsubishi')
              for name in INSTRUCTION_FILES]
    for source, _ in result:
        if not Path(source).is_file():
            raise FileNotFoundError(source)
    payload = json.loads(gzip.decompress((root / 'resources/instructions/mitsubishi/instruction_definitions.json.gz').read_bytes()))
    if payload.get('corpus_kind') != 'authored_plc_facts_v1' or payload.get('documents'):
        raise ValueError('Unreviewed compiled instruction definitions')
    def inspect(value):
        if isinstance(value, dict):
            if set(value).intersection({'bbox', 'raw_text', 'manual_text', 'layout_text', 'geometry_reference', 'source_span'}):
                raise ValueError('Original evidence fields in compiled definitions')
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)
    inspect(payload)
    return result


def runtime_knowledge_data(root):
    root = Path(root)
    for relative, _ in FILES:
        if not (root / relative).is_file():
            raise FileNotFoundError(relative)
    assert_fact_database(root / FILES[0][0])
    runtime_instruction_data(root)
    return [(str(root / relative), destination) for relative, destination in FILES]


def assert_fact_database(path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        kind = db.execute("SELECT value FROM meta WHERE key='corpus_kind'").fetchone()
        if not kind or kind[0] != "authored_plc_facts_v1":
            raise ValueError("Historical evidence databases cannot be packaged")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if {"page_artifacts", "tables", "device_sections"} & tables:
            raise ValueError("Original document tables in runtime corpus")
        invalid = db.execute("SELECT count(*) FROM chunks WHERE content_origin NOT IN ('authored_fact','original_case') "
            "OR distribution_status!='fact_representation_reviewed'").fetchone()[0]
        if invalid:
            raise ValueError("Unreviewed runtime content")
        for row in db.execute("SELECT metadata_json,text FROM chunks"):
            meta = json.loads(row[0])
            if meta.get("distribution_status") != "fact_representation_reviewed":
                raise ValueError("Unreviewed result metadata")
            if any(marker in row[1] for marker in ("[PAGE ", "[TABLE page=", "[STRUCTURED INSTRUCTION RECORD]")):
                raise ValueError("Original document wrappers in runtime corpus")


def audit_packaged_knowledge(package):
    package = Path(package)
    allowed = {destination + "/" + Path(relative).name for relative, destination in FILES}
    actual = {p.relative_to(package).as_posix() for p in (package / "knowledge").rglob("*") if p.is_file()}
    if actual != allowed:
        raise ValueError("Knowledge package differs from allowlist: " + repr(sorted(actual ^ allowed)))
    assert_fact_database(package / "knowledge/runtime/plc_facts.sqlite")
