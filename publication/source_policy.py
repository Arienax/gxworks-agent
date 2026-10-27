"""Keep research materials and local development output out of source exports."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath


CACHE_DIRECTORIES = frozenset({
    '.git', '.venv', '.venv-mcp', '__pycache__', '.pytest_cache', '.mypy_cache',
    '.ruff_cache', '.hypothesis', 'node_modules',
})
PRIVATE_DIRECTORIES = frozenset({
    'research', 'evidence', 'experiments', 'acceptance', 'reports', 'tmp', 'temp',
    'local', 'logs', 'test-results', 'playwright-report', 'coverage', '.tmp',
    '.codex', '.agents', '.workbuddy', '.runtime', 'build', 'dist', 'out',
})
PRIVATE_PREFIXES = (
    'docs/reports/', 'docs/process/', 'docs/internal/', 'docs/research/',
    'resources/knowledge/', 'resources/gxw/', 'tests/fixtures/',
    'publication/public/',
    'web/dist/', 'tests/tmp/', 'checks/tmp/',
    'resources/instructions/mitsubishi/instruction_definitions',
)
PRIVATE_FILES = frozenset({
    'config.json', 'src/config.json', 'publication/public-files.json',
    'tools/download_mitsubishi_manuals.py',

    'examples/comment.csv', 'examples/plc_import_comment.csv',
    'examples/plc_import_program.csv', 'examples/real_import_program.csv',
    'tools/private_materials.py', 'tools/download_mitsubishi_manuals.py',
    'resources/instructions/mitsubishi/device_definitions.json',
    'resources/instructions/mitsubishi/fx3u_contract_promotions.json',
    'resources/instructions/mitsubishi/fx3u_instruction_quarantine.json',
    'resources/instructions/mitsubishi/fx3u_operand_semantic_promotions.json',
    'resources/instructions/mitsubishi/instruction_behavior_facts.json',
    'resources/instructions/mitsubishi/instruction_source_authority.json',
    'resources/instructions/mitsubishi/operand_semantic_coverage_summary.json',
})
MATERIAL_SUFFIXES = frozenset({
    '.pdf', '.chm', '.djvu', '.gxw', '.gx3', '.gxp', '.gpj', '.gpp',
    '.zip', '.7z', '.rar', '.xz', '.gz', '.bz2',
    '.sqlite', '.sqlite3', '.db', '.npz',
})
GENERATED_SUFFIXES = frozenset({
    '.pyc', '.pyo', '.log', '.tsbuildinfo', '.exe', '.dll', '.pdb',
    '.sqlite-shm', '.sqlite-wal',
})


def exclusion_reason(name: str) -> str | None:
    """Classify material/output paths; ordinary source needs no registration."""
    path = PurePosixPath(name.replace('\\', '/'))
    parts = tuple(part.casefold() for part in path.parts)
    value = path.as_posix().casefold()
    if any(part in CACHE_DIRECTORIES for part in parts):
        return 'local cache or build output'
    if parts and parts[0] in PRIVATE_DIRECTORIES:
        return 'research material or development output'
    if parts and parts[0].startswith('tmp_'):
        return 'temporary development output'
    if parts and parts[0] == 'web' and any(part.startswith('.sfc-check-') for part in parts[1:]):
        return 'temporary development output'
    if value.startswith(PRIVATE_PREFIXES):
        return 'private research material or process record'
    if path.name.casefold() in {'agents.md', 'claude.md', '.coverage', '.ds_store'}:
        return 'local collaboration or test output'
    if value.startswith('tools/fact_') or value in {
            'tools/fbd_fact_review.py', 'tools/build_fact_knowledge.py', 'tools/validate_fact_knowledge.py'}:
        return 'private manual review implementation'
    if value in PRIVATE_FILES:
        return 'local configuration or private research input/tool'
    if path.name.casefold().startswith('.env') and path.name.casefold() != '.env.example':
        return 'local credentials or configuration'
    if path.suffix.lower() in GENERATED_SUFFIXES:
        return 'generated development output'
    native_template = value.startswith('src/gxw/templates/') and value.endswith(('.gxw', '.zip.xz'))
    if path.suffix.lower() in MATERIAL_SUFFIXES and not native_template:
        return 'manual, research sample, database or archive'
    return None


def source_files(root: Path):
    """Select source by shared rules, independently of Git and local ignores."""
    for directory, folders, names in os.walk(root):
        base = Path(directory)
        folders[:] = sorted(folder for folder in folders
            if exclusion_reason((base / folder).relative_to(root).as_posix() + '/_') is None)
        for name in sorted(names):
            path = base / name
            if exclusion_reason(path.relative_to(root).as_posix()) is None:
                yield path
