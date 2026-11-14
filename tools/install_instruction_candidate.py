"""Install a checked local owner-store candidate without copying its database."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def install(candidate, formal, *, expected_version, database_name, reports, receipt):
    candidate, formal, receipt = map(Path, (candidate, formal, receipt))
    current, fresh = read(formal), read(candidate)
    if current['fact_bundle_version'] != expected_version:
        raise ValueError('Installed version differs from the expected input')
    version = fresh['fact_bundle_version']
    if not reports:
        raise ValueError('Candidate verification reports required')
    for path in reports:
        result = read(path)
        if result.get('version') != version or not (
                result.get('passed') is True or result.get('status') == 'passed'):
            raise ValueError('Candidate verification did not pass for this version: ' + str(path))
    if (Path(database_name).name != database_name or '/' in database_name
            or '\\' in database_name or not database_name.endswith('.sqlite')):
        raise ValueError('Database name must be a sibling SQLite filename')
    source = candidate.parent / fresh['database']['filename']
    target = formal.parent / database_name
    if (source.is_symlink() or source.resolve().parent != candidate.parent.resolve()
            or not source.is_file() or source.stat().st_size != fresh['database']['bytes']):
        raise ValueError('Candidate database is not the compiled sibling')
    if target.exists() or receipt.exists():
        raise ValueError('Installation output already exists')
    receipt.parent.mkdir(parents=True, exist_ok=True)
    previous = receipt.with_name('previous-manifest.json')
    if previous.exists():
        raise ValueError('Previous manifest record already exists')
    save(previous, current)
    source.rename(target)
    fresh['database']['filename'] = database_name
    temporary = formal.with_name(formal.name + '.installing')
    save(temporary, fresh)
    os.replace(temporary, formal)
    candidate.unlink()
    result = {'version': version, 'installed': True,
              'candidate_verified': True, 'fresh_process_default_load_verified': False,
              'installed_manifest': str(formal), 'installed_database': str(target),
              'previous_database': str(formal.parent / current['database']['filename']),
              'verification_reports': [str(p) for p in reports],
              'database_transferred_without_duplicate': True, 'ledger_written': False,
              'cleanup_status': 'awaiting installed-default verification'}
    save(receipt, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--formal', required=True, type=Path)
    parser.add_argument('--expected-version', required=True)
    parser.add_argument('--database-name', required=True)
    parser.add_argument('--report', action='append', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(install(args.candidate, args.formal, expected_version=args.expected_version,
        database_name=args.database_name, reports=args.report, receipt=args.receipt)))


if __name__ == '__main__':
    main()
