#!/usr/bin/env python3
"""Canonical evidence + instruction-definition build pipeline.

This wraps build_fx3u_knowledge_v3 without duplicating its implementation.
Document ownership and operand bindings are source-derived; device extraction
also excludes PDF/layout artifacts. Raw evidence can be reused independently.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from device_entity_cleanup import sanitize_device_like_entities
base = None
_original_extract_entities = None


def _pdf_builder():
    global base, _original_extract_entities
    if base is None:
        import build_fx3u_knowledge_v3 as implementation
        base = implementation
        _original_extract_entities = base.extract_entities
    return base


def extract_entities_clean(
    text: str,
    instruction_re,
    *,
    chunk_type: str,
    explicit_entities=(),
):
    _pdf_builder()
    extracted = _original_extract_entities(
        text,
        instruction_re,
        chunk_type=chunk_type,
        explicit_entities=explicit_entities,
    )
    return sanitize_device_like_entities(text, chunk_type, extracted)


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--compile-definitions-only', action='store_true')
    parser.add_argument('--refresh-instruction-index-only', action='store_true')
    parser.add_argument('--index', type=Path)
    parser.add_argument('--index-report', type=Path)
    parser.add_argument('--definitions-output', type=Path, default=root / 'resources/instructions/mitsubishi/instruction_definitions.json.gz')
    parser.add_argument('--definitions-report', type=Path)
    parser.add_argument('--authored-facts', type=Path, default=root / 'resources/instructions/mitsubishi/instruction_behavior_facts.json')
    stage, remaining = parser.parse_known_args(argv)
    if stage.refresh_instruction_index_only:
        import json
        outputs = argparse.ArgumentParser()
        outputs.add_argument('--output', type=Path, required=True)
        settings, _ = outputs.parse_known_args(remaining)
        report = _pdf_builder().refresh_instruction_index_copy(
            stage.index or root / 'resources/knowledge/fx3u_knowledge.sqlite', settings.output)
        if stage.index_report:
            stage.index_report.parent.mkdir(parents=True, exist_ok=True)
            stage.index_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f"Instruction index: {report['before']} -> {report['after']}; source evidence unchanged")
        return 0
    if any(flag in remaining for flag in ('--help', '-h')):
        parser.print_help()
        _pdf_builder().parse_args(['--help'])
        return 0
    if not stage.compile_definitions_only:
        builder = _pdf_builder()
        builder.extract_entities = extract_entities_clean
        if builder.main(remaining):
            return 1
    paths = argparse.ArgumentParser(add_help=False)
    paths.add_argument('--output', type=Path, default=root / 'resources/knowledge/fx3u_knowledge.sqlite')
    paths.add_argument('--sources-config', type=Path, default=root / 'resources/knowledge/sources.json')
    paths.add_argument('--source-dir', type=Path)
    settings, _ = paths.parse_known_args(remaining)
    from build_instruction_definitions import main as compile_definitions
    arguments = ['--index', str(stage.index or settings.output), '--sources', str(settings.sources_config),
                 '--output', str(stage.definitions_output), '--authored-facts', str(stage.authored_facts)]
    if settings.source_dir:
        arguments.extend(['--source-dir', str(settings.source_dir)])
    if stage.definitions_report:
        arguments.extend(['--report', str(stage.definitions_report)])
    return compile_definitions(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
