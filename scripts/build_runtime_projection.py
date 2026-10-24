"""Reproducible standalone semantic publication from compiled definitions.

Private evidence is written separately; no files are uploaded. This is not a
replacement for an unavailable private full-corpus build pipeline. The SQLite
output contains ONLY the instruction owners present in the input.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
try:
    import resource
except ImportError:  # Windows has no resource module.
    resource = None
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from knowledge.fact_corpus import project_definition
from knowledge.runtime_fact_projection import project_runtime_definitions, intern_runtime_sources
from knowledge.runtime_fact_publication import write_runtime_sqlite, project_language_features, write_owner_manifest
from plc.instruction_definition_storage import expand_definitions


def dump_gzip(path, value):
    # Deterministic output, exclusive creation, incremental encoding.
    total = 0
    pending, size = [], 0
    with Path(path).open('xb') as output, gzip.GzipFile(fileobj=output, mode='wb', mtime=0, filename='') as stream:
        for part in json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).iterencode(value):
            data = part.encode(); total += len(data)
            pending.append(data); size += len(data)
            if size >= 1048576:
                stream.write(b"".join(pending)); pending, size = [], 0
        if pending:
            stream.write(b"".join(pending))
    return {'raw_bytes': total, 'gzip_bytes': Path(path).stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--private-evidence', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.private_evidence.resolve().is_relative_to(args.output_dir.resolve()):
        parser.error('--private-evidence must be outside the runtime output directory')
    outputs = [args.output_dir / name for name in ('instruction_definitions.json.gz', 'plc_facts.sqlite', 'instruction_definitions.manifest.json', 'receipt.json')]
    outputs += [args.private_evidence, args.private_evidence.with_name(args.private_evidence.name + '.projection-map.gz'),
                args.private_evidence.with_name(args.private_evidence.name + '.strict-withheld.gz')]
    if any(path.exists() for path in outputs):
        parser.error('Output already exists; select new paths rather than replacing evidence or runtime data')
    args.private_evidence.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if args.input.suffix == '.gz' else open
    with opener(args.input, 'rt', encoding='utf-8') as stream:
        raw = json.load(stream)
    # Preserve the original document before even lossless transport expansion.
    evidence_metrics = dump_gzip(args.private_evidence, raw)
    if raw.get('schema_version') != 1 or raw.get('source_serialization'):
        raw = expand_definitions(raw)
    allowed_root = {"schema_version", "method", "entries", "documents", "corpus_kind",
                    "fact_bundle_version", "language_facts"}
    if set(raw) - allowed_root:
        raise ValueError("Unreviewed top-level fields require an explicit publication policy")
    publication = {key: value for key, value in raw.items() if key not in {'entries', 'documents'}}
    strict = [project_definition(entry) for entry in raw['entries']]
    publication['entries'] = [entry for entry, withheld in strict]
    strict_withheld = sum(len(withheld) for entry, withheld in strict)
    language_features, language_withheld = project_language_features(raw.get('language_facts', []))
    if 'language_facts' in raw:
        publication['language_facts'] = language_features
    language_withheld_count = sum(len(feature['withheld']) for feature in language_withheld)
    language_input_facts = sum(len(feature.get('facts', [])) for feature in raw.get('language_facts', []))
    language_output_facts = sum(len(feature['facts']) for feature in language_features)
    dump_gzip(args.private_evidence.with_name(args.private_evidence.name + '.strict-withheld.gz'),
              [{'vendor': entry.get('vendor'), 'opcode': entry.get('opcode'),
                'target_model': entry.get('target_model'), 'withheld': withheld}
               for entry, withheld in strict if withheld] + language_withheld)
    del strict
    publication['corpus_kind'] = 'authored_plc_facts_v1'
    digest = hashlib.sha256()
    with args.input.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
    from knowledge import fact_corpus, runtime_fact_projection, runtime_fact_publication
    from plc import instruction_owner_store
    for module in (fact_corpus, runtime_fact_projection, runtime_fact_publication, instruction_owner_store):
        digest.update(module.__name__.encode())
        digest.update(Path(module.__file__).read_bytes())
    digest.update(Path(__file__).read_bytes())
    publication['fact_bundle_version'] = 'semantic-runtime-v1:' + digest.hexdigest()
    raw_facts = sum(len(entry.get('facts', [])) for entry in raw['entries'])
    del raw
    projected, mapping, receipt = project_runtime_definitions(publication)
    del publication
    dump_gzip(args.private_evidence.with_name(args.private_evidence.name + '.projection-map.gz'), mapping)
    del mapping
    pooled = intern_runtime_sources(projected, intern_values=True)
    del projected
    metrics = dump_gzip(args.output_dir / 'instruction_definitions.json.gz', pooled)
    sqlite_metrics = write_runtime_sqlite(pooled, args.output_dir / 'plc_facts.sqlite',field_pooling=True)
    owner_manifest = write_owner_manifest(pooled, args.output_dir / 'plc_facts.sqlite', args.output_dir / 'instruction_definitions.manifest.json')
    receipt.update(raw_input_facts=raw_facts, strict_withheld=strict_withheld, language_withheld=language_withheld_count, language_input_facts=language_input_facts,
                   language_output_facts=language_output_facts, runtime_storage=metrics, sqlite=sqlite_metrics, owner_store=owner_manifest,
                   private_evidence=evidence_metrics, peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if resource else None,
                   scope='provided_instruction_definitions_only', publication_accepted=False)
    (args.output_dir / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(receipt, ensure_ascii=False))

if __name__ == '__main__':
    main()
