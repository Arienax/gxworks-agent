#!/usr/bin/env python3
"""Reusable semantic stage of the existing knowledge build pipeline."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, default=ROOT / 'resources/knowledge/fx3u_knowledge.sqlite')
    parser.add_argument('--sources', type=Path, default=ROOT / 'resources/knowledge/sources.json')
    parser.add_argument('--source-dir', type=Path, help='Local complete manual directory; source files are read only')
    parser.add_argument('--output', type=Path, default=ROOT / 'resources/instructions/mitsubishi/instruction_definitions.json.gz')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--authored-facts', type=Path, default=ROOT / 'resources/instructions/mitsubishi/instruction_behavior_facts.json')
    args = parser.parse_args(argv)
    from knowledge.instruction_compiler import compile_instruction_definitions
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    authored = json.loads(args.authored_facts.read_text(encoding='utf-8')) if args.authored_facts.is_file() else None
    payload, report = compile_instruction_definitions(args.index, args.sources, DEFAULT_INSTRUCTION_REGISTRY,
                                                      source_dir=args.source_dir, authored_facts=authored)
    from plc.instruction_definition_storage import compact_definitions
    base_forms = {opcode: form.base_mnemonic for opcode in DEFAULT_INSTRUCTION_REGISTRY.known_mnemonics()
                  if (form := DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode)) is not None}
    # Source-declared modifier families share one definition plus form diffs;
    # this includes literal variants absent from the old compatibility catalogue.
    base_forms.update(payload.get('form_families', {}))
    payload = compact_definitions(payload, base_forms=base_forms)
    report.update(storage_schema=2, stored_families=len(payload['definitions']),
                  model_diff_count=sum(len(d['model_diffs']) for d in payload['definitions']),
                  verification_inheritance=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    content = (json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    args.output.write_bytes(gzip.compress(content, mtime=0) if args.output.suffix == '.gz' else content)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
