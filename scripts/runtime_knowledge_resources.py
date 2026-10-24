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


def _inspect_public_value(value):
    if isinstance(value, dict):
        if set(value).intersection({'bbox', 'raw_text', 'manual_text', 'layout_text', 'geometry_reference', 'source_span'}):
            raise ValueError('Original evidence fields in compiled definitions')
        for child in value.values():
            _inspect_public_value(child)
    elif isinstance(value, list):
        for child in value:
            _inspect_public_value(child)


def runtime_instruction_data(root):
    root = Path(root)
    directory = root / 'resources/instructions/mitsubishi'
    manifest = root / 'resources/knowledge/runtime/instruction_definitions.manifest.json'
    local_manifest = directory / 'instruction_definitions.manifest.json'
    if local_manifest.exists():
        manifest = local_manifest
    use_store = manifest.exists()
    names = [name for name in INSTRUCTION_FILES if not (use_store and name == 'instruction_definitions.json.gz')]
    result = [(str(directory / name), 'resources/instructions/mitsubishi') for name in names]
    for source, _ in result:
        if not Path(source).is_file():
            raise FileNotFoundError(source)
    if use_store:
        from plc.instruction_owner_store import OwnerDefinitionStore
        store = OwnerDefinitionStore(manifest)
        try:
            metadata = store.metadata
            if metadata.get('corpus_kind') != 'authored_plc_facts_v1' or metadata.get('documents'):
                raise ValueError('Unreviewed owner store metadata')
            _inspect_public_value(metadata)
            # Build-time validation is bounded to one owner, never an eager
            # runtime load of the complete inventory. No producer is inferred.
            for header in store.iter_owner_headers():
                _inspect_public_value(store.get_entry(header['opcode'],cpu=header['target_model'],vendor=header['vendor']))
            assert_fact_database(store.database_path)
        finally:
            store.close()
        if manifest == local_manifest:
            result.extend([(str(manifest), 'resources/instructions/mitsubishi'),
                           (str(store.database_path), 'resources/instructions/mitsubishi')])
        return result
    with gzip.open(directory / 'instruction_definitions.json.gz', 'rt', encoding='utf-8') as stream:
        payload = json.load(stream)
    if payload.get('corpus_kind') != 'authored_plc_facts_v1' or payload.get('documents'):
        raise ValueError('Unreviewed compiled instruction definitions')
    _inspect_public_value(payload)
    return result


def runtime_knowledge_data(root):
    root = Path(root)
    for relative, _ in FILES:
        if not (root / relative).is_file():
            raise FileNotFoundError(relative)
    assert_fact_database(root / FILES[0][0])
    runtime_instruction_data(root)
    result = [(str(root / relative), destination) for relative, destination in FILES]
    manifest = root / 'resources/knowledge/runtime/instruction_definitions.manifest.json'
    if manifest.exists():
        result.append((str(manifest), 'knowledge/runtime'))
    return result


def assert_fact_database(path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        kind = db.execute("SELECT value FROM meta WHERE key='corpus_kind'").fetchone()
        if not kind or kind[0] != "authored_plc_facts_v1":
            raise ValueError("Historical evidence databases cannot be packaged")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if {"page_artifacts", "tables", "device_sections"} & tables:
            raise ValueError("Original document tables in runtime corpus")
        for table in ('runtime_sources', 'runtime_values', 'runtime_definitions', 'runtime_language_features', 'runtime_fact_fields'):
            if table in tables:
                for row in db.execute('SELECT payload_json FROM '+table):
                    _inspect_public_value(json.loads(row[0]))
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
    if (package / 'knowledge/runtime/instruction_definitions.manifest.json').exists():
        allowed.add('knowledge/runtime/instruction_definitions.manifest.json')
        from plc.instruction_owner_store import OwnerDefinitionStore
        store = OwnerDefinitionStore(package / 'knowledge/runtime/instruction_definitions.manifest.json')
        store.close()
    actual = {p.relative_to(package).as_posix() for p in (package / "knowledge").rglob("*") if p.is_file()}
    if actual != allowed:
        raise ValueError("Knowledge package differs from allowlist: " + repr(sorted(actual ^ allowed)))
    assert_fact_database(package / "knowledge/runtime/plc_facts.sqlite")
