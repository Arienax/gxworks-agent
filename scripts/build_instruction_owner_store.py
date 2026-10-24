"""Stream a reviewed schema 1/2 bundle into a non-release owner store and audit it.

No full-document json.load or corpus-wide Python catalog. Exact pools use SQLite.
No status is promoted and no CPU support is inferred. Explicit raw projection
records every withheld fact privately rather than silently dropping evidence.
"""
from __future__ import annotations
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import re
import sys
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
from knowledge.runtime_fact_publication import OwnerStoreWriter, project_language_features
from knowledge.fact_corpus import project_definition
from plc.instruction_owner_store import MANIFEST_NAME
from tools.audit_runtime_fact_package import audit_owner_store
from tools.runtime_definition_stream import inspect_definition_stream, iter_definition_owners


def _identity(path):
    value = path.stat()
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _write_json(path, value):
    with path.open('x', encoding='utf-8') as output:
        json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write('\n')


def _object_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _ledger_line(stream, value):
    json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    stream.write('\n')


def _build_owner_store(input_path, output_dir, *, max_unit_bytes=64 * 1024 * 1024,
                       project_raw=False, private_audit_dir=None, progress=None):
    started = time.perf_counter()
    progress = progress if progress is not None else {}
    progress['phase'] = 'input_inspection'
    input_path, output_dir = Path(input_path).resolve(), Path(output_dir).resolve()
    before = _identity(input_path)
    digest = hashlib.sha256()
    with input_path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    if _identity(input_path) != before:
        raise ValueError('Input changed while hashing')
    inspected = inspect_definition_stream(input_path, max_unit_bytes=max_unit_bytes)
    if _identity(input_path) != before:
        raise ValueError('Input changed while inspecting')
    metadata = {key: value for key, value in inspected.items() if key not in {
        'model_profiles', 'serialization', 'verification_inheritance'}}
    metadata['schema_version'] = 1
    if bool(project_raw) != bool(private_audit_dir):
        raise ValueError('--project-raw requires a separate --private-audit-dir, and vice versa')
    if metadata.get('method') != 'instruction-material-compiler-v1':
        raise ValueError('Input must explicitly identify the instruction material compiler')
    if not project_raw and (metadata.get('corpus_kind') != 'authored_plc_facts_v1' or metadata.get('documents')):
        raise ValueError('Input needs explicit reviewed compiler metadata and no private documents; migration cannot infer review or silently project raw evidence')
    allowed = {'schema_version', 'method', 'corpus_kind', 'fact_bundle_version', 'documents', 'language_facts'}
    if not project_raw and set(metadata) - allowed:
        raise ValueError('Unknown root metadata needs a reviewed preservation policy')
    features = metadata.pop('language_facts', [])
    progress.update(phase='language_preflight', input_sha256=digest.hexdigest())
    if not isinstance(features, list):
        raise ValueError('language_facts must be a bounded list')
    progress['input_language_features'] = len(features)
    # Root metadata is already bounded. Validate/project it before opening a
    # database or owner ledger, and retain the result rather than projecting a
    # second time after expensive owner traversal.
    published_languages, language_rejections = project_language_features(features)
    if not project_raw and (language_rejections or _object_digest(published_languages) != _object_digest(features)):
        raise ValueError('Language features need prior strict review; use explicit raw projection with private evidence')
    language_records = []
    if project_raw:
        for index, feature in enumerate(features):
            target = feature.get('target')
            language_records.append({'kind': 'language_projection', 'feature_index': index,
                'original_feature_sha256': _object_digest(feature),
                'projected_feature_sha256': [_object_digest(value) for value in published_languages if value.get('target') == target],
                'withheld': [value for value in language_rejections if value.get('target') == target]})
    progress['phase'] = 'output_preparation'
    generated_version = not bool(metadata.get('fact_bundle_version'))
    if generated_version:
        metadata['fact_bundle_version'] = 'owner-store-migration:' + digest.hexdigest()
    ledger_path = private_summary = None
    if project_raw:
        private_dir = Path(private_audit_dir).resolve()
        if private_dir.is_relative_to(output_dir) or output_dir.is_relative_to(private_dir):
            raise ValueError('Private evidence and runtime output directories must be separate')
        private_dir.mkdir(parents=True, exist_ok=True)
        ledger_path = private_dir / 'projection-receipts.jsonl.gz'
        private_summary = private_dir / 'projection-summary.json'
        if ledger_path.exists() or private_summary.exists() or (private_dir / 'projection-failure.json').exists():
            raise ValueError('Private evidence already exists; choose new audit paths')
        import knowledge.fact_corpus as projector
        projector_sha = hashlib.sha256(Path(projector.__file__).read_bytes()).hexdigest()
        import knowledge.runtime_fact_publication as language_projector
        language_projector_sha = hashlib.sha256(Path(language_projector.__file__).read_bytes()).hexdigest()
        language_projection_sha = _object_digest(published_languages)
        version = hashlib.sha256((digest.hexdigest() + projector_sha + language_projector_sha
                                  + language_projection_sha).encode()).hexdigest()
        metadata = {'schema_version': 1, 'method': 'instruction-material-compiler-v1',
            'corpus_kind': 'authored_plc_facts_v1', 'fact_bundle_version': 'strict-owner-projection:' + version}
        generated_version = True
    output_dir.mkdir(parents=True, exist_ok=True)
    database = output_dir / 'plc_facts.sqlite'
    manifest = output_dir / MANIFEST_NAME
    pending_manifest = output_dir / 'instruction_definitions.manifest.pending.json'
    audit_path = output_dir / 'owner-store-audit.json'
    receipt_path = output_dir / 'owner-store-build-receipt.json'
    if any(path.exists() for path in (database, manifest, pending_manifest, audit_path, receipt_path)):
        raise ValueError('Use a new output directory; existing resources are never overwritten')
    writer = OwnerStoreWriter(database, metadata, field_pooling=True)
    ledger = None
    withheld_reasons = Counter()
    raw_facts = projected_facts = withheld_facts = changed_owners = published_features = 0
    language_withheld = sum(bool(item.get('withheld')) for item in language_rejections)
    progress['owners_processed'] = 0
    try:
        if project_raw:
            ledger = gzip.open(ledger_path, 'xt', encoding='utf-8')
            _ledger_line(ledger, {'kind': 'input_reference', 'input_path': str(input_path),
                'input_sha256': digest.hexdigest(), 'must_retain_original_input': True,
                'projector_sha256': projector_sha,
                'language_projector_sha256': language_projector_sha,
                'language_projection_sha256': language_projection_sha,
                'original_root_metadata': dict(inspected)})
            for record in language_records:
                _ledger_line(ledger, record)
        progress['phase'] = 'owner_projection'
        for entry in iter_definition_owners(input_path, inspected, max_unit_bytes=max_unit_bytes):
            raw_facts += len(entry.get('facts', []))
            if project_raw:
                original = entry
                entry, withheld = project_definition(original)
                before_hash, after_hash = _object_digest(original), _object_digest(entry)
                changed_owners += before_hash != after_hash
                withheld_facts += len(withheld)
                for item in withheld:
                    reason = item.get('reason')
                    withheld_reasons[reason if isinstance(reason, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', reason) else '<other>'] += 1
                _ledger_line(ledger, {'kind': 'owner_projection',
                    'owner': {key: original.get(key) for key in ('vendor', 'opcode', 'target_model')},
                    'original_owner_sha256': before_hash, 'projected_owner_sha256': after_hash,
                    'original_fact_count': len(original.get('facts', [])),
                    'projected_fact_count': len(entry.get('facts', [])), 'withheld': withheld})
            writer.add_entry(entry)
            projected_facts += len(entry.get('facts', []))
            progress['owners_processed'] += 1
        progress['phase'] = 'language_write'
        for feature in published_languages:
            writer.add_language_feature(feature)
            published_features += 1
        progress['phase'] = 'finalize'
        transport = writer.finalize(pending_manifest)
    finally:
        writer.close()
        if ledger is not None:
            ledger.close()
    progress['phase'] = 'post_build_audit'
    audit = audit_owner_store(pending_manifest, max_unit_bytes=max_unit_bytes, owner_breakdown=True)
    if audit['counts']['owners'] != transport['sqlite']['owners']:
        raise ValueError('Post-build owner audit count differs')
    _write_json(audit_path, {'audit_schema': 1, 'owner_store': audit})
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == 'darwin':
            peak //= 1024
    except ImportError:
        peak = None
    receipt = {'method': 'streaming-strict-publication-owner-store-v1' if project_raw else 'streaming-reviewed-definition-owner-store-migration-v1',
        'input_sha256': digest.hexdigest(), 'input_bytes': before[2],
        'input_schema_version': inspected['schema_version'],
        'maximum_stored_json_unit_bytes': inspected.maximum_unit_bytes,
        'metadata_bytes': inspected.metadata_bytes, 'max_unit_limit_bytes': max_unit_bytes,
        'generated_transport_version': generated_version,
        'owners_checked': audit['counts']['owners'], 'input_language_features': len(features),
        'published_language_features': published_features,
        **transport, 'audit_file': audit_path.name, 'peak_rss_kib': peak,
        'elapsed_seconds': time.perf_counter() - started,
        'private_full_source_producer_integrated': False,
        'strict_projection_performed': bool(project_raw), 'original_objects_unchanged': not project_raw,
        'original_input_file_modified': False,
        'raw_fact_instances': raw_facts, 'projected_fact_instances': projected_facts,
        'withheld_fact_instances': withheld_facts, 'withheld_reason_counts': dict(withheld_reasons),
        'owners_with_projection_changes': changed_owners, 'language_withheld_records': language_withheld,
        'fact_status_promotion_performed': False,
        'limitations': ['NON_RELEASE candidate; no deployed resources were replaced.',
            'original_objects_unchanged is a mode guarantee, not the measured owner difference count; see owners_with_projection_changes.',
            'Exact reversible storage of admitted projection; original raw objects may differ and withheld evidence stays private.' if project_raw else 'Exact reversible storage only; compiler admission and hardware support are not inferred.',
            'One stored family, bounded metadata and one expanded owner are retained; Python object overhead is not a hard byte cap.',
            'The post-build audit scans every owner offline; it is not a lazy-startup benchmark.',
            'Physical storage expansion and full-corpus publication acceptance need separate validation.']}
    progress['phase'] = 'publication_receipts'
    _write_json(receipt_path, receipt)
    if project_raw:
        _write_json(private_summary, {'input_path': str(input_path), 'input_sha256': digest.hexdigest(),
            'must_retain_original_input': True, 'projection_ledger': ledger_path.name,
            'projector_sha256': projector_sha, 'language_projector_sha256': language_projector_sha,
            'language_projection_sha256': language_projection_sha,
            'withheld_fact_instances': withheld_facts,
            'language_withheld_records': language_withheld,
            'note': 'Original objects remain in the referenced input; only admitted formal projection is equivalent to this store.'})
    # Publish the conventional activation name only after all owners, audit and
    # receipts succeeded. Failed partial outputs are retained, never enabled.
    progress['phase'] = 'publish_manifest'
    with manifest.open('xb') as target:
        target.write(pending_manifest.read_bytes())
    pending_manifest.unlink()
    return receipt


def build_owner_store(input_path, output_dir, *, max_unit_bytes=64 * 1024 * 1024,
                      project_raw=False, private_audit_dir=None):
    """Build a candidate; preserve failures privately without overwriting files."""
    progress = {'phase': 'input_inspection', 'owners_processed': 0}
    try:
        return _build_owner_store(input_path, output_dir, max_unit_bytes=max_unit_bytes,
            project_raw=project_raw, private_audit_dir=private_audit_dir, progress=progress)
    except Exception as error:
        # This is an optional private diagnostic, not a publication receipt.
        # Never delete failed artifacts, overwrite an earlier failure, or write
        # private error details into the runtime directory.
        if project_raw and private_audit_dir:
            private_dir, output = Path(private_audit_dir).resolve(), Path(output_dir).resolve()
            if not (private_dir.is_relative_to(output) or output.is_relative_to(private_dir)):
                try:
                    private_dir.mkdir(parents=True, exist_ok=True)
                    _write_json(private_dir / 'projection-failure.json', {
                        'audit_schema': 1, 'status': 'failed', 'release_accepted': False,
                        **progress, 'error_type': type(error).__name__, 'error': str(error)[:1000],
                        'conventional_manifest_present': (output / MANIFEST_NAME).exists(),
                        'partial_artifacts_retained': True,
                        'note': 'This attempt failed. Do not enable partial outputs; retain evidence and retry into new output/audit directories.'})
                except OSError:
                    pass  # The original failure must not be masked by diagnostics.
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--max-unit-mib', type=int, default=64)
    parser.add_argument('--project-raw', action='store_true', help='Explicitly apply existing strict publication projection; preserve private withheld evidence separately')
    parser.add_argument('--private-audit-dir', type=Path)
    args = parser.parse_args()
    if args.max_unit_mib <= 0:
        parser.error('--max-unit-mib must be positive')
    try:
        receipt = build_owner_store(args.input, args.output_dir, max_unit_bytes=args.max_unit_mib * 1024 * 1024,
            project_raw=args.project_raw, private_audit_dir=args.private_audit_dir)
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as error:
        parser.exit(2, 'Migration failed without publishing a release: ' + str(error) + '\n')
    print(json.dumps({'owners_checked': receipt['owners_checked'], 'release_accepted': False,
        'receipt': str(args.output_dir / 'owner-store-build-receipt.json'),
        'audit': str(args.output_dir / 'owner-store-audit.json')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
