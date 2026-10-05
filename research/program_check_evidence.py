"""Classify saved native check observations without running GX Works2.

Compilation, actual resource reads, backend completion, public diagnostics and
save/reopen are independent facts. Publication alone is not read evidence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from gxw.native_diagnostics import (analyze_native_check as analyze_check, bind_native_check_input, bind_native_source_chain, bind_native_source_snapshot,
                                    project_native_source_identity, compare_public_source_diagnostic,
                                    project_native_source_diagnostic as _project_source_diagnostic,
                                    bind_native_instance_interval as _native_instance_interval, correlate_st_diagnostic, correlate_fbd_bool_output)


def _id(value):
    return tuple(value) if isinstance(value, list) and len(value) == 12 else None


def has_current_native_check_code(evidence):
    """Accept owned task bytes without hiding conflicting observed reads."""
    traced = evidence.get('checker_resources', {})
    owned = evidence.get('owned_check_inputs', {}).get('correspondence', 'not_observed')
    if owned == 'not_observed':
        return traced.get('correspondence') == 'current'
    return (owned == 'current' and not traced.get('observation_errors')
            and traced.get('correspondence') in ('current', 'not_observed')
            and evidence.get('raw_backend_check', {}).get('completed') is True)


def project_native_source_diagnostic(query, diagnostic, *, project_id, native_version, references=None):
    return _project_source_diagnostic(query, diagnostic, project_id=project_id,
                                     native_version=native_version, references=references)


def analyze_directory(directory):
    directory = Path(directory)
    events = [json.loads(line) for line in (directory/'native-events.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    observed = next((directory/name for name in ('observation-events.jsonl', 'events.jsonl') if (directory/name).exists()), None)
    observations = [json.loads(line) for line in observed.read_text(encoding='utf-8').splitlines()] if observed else []
    names = {_id(row['id']): row['name'] for row in events if row.get('operation') == 'NativeObject' and row.get('name')}
    resources = {row['name']: tuple((directory/f"pcode-{row['index']}-{i}.bin").read_bytes() for i in range(3))
                 for row in events if row.get('operation') == 'Resource'}
    generated = {identity: resources[name] for identity, name in names.items() if name in resources}
    published = {}
    owned_snapshots = {}
    for row in events:
        if row.get('operation') == 'OwnedCheckInput':
            for item in row.get('rows', []):
                path = directory / item['file']
                if path.resolve().is_relative_to(directory.resolve()) and path.is_file():
                    owned_snapshots[item['file']] = path.read_bytes()
        if row.get('operation') != 'PublishedResourceCodeCorrespondence':
            continue
        channels = row.get('channels', [])
        if len(channels) != 3 or [channel.get('channel') for channel in channels] != [0, 1, 2]:
            continue
        paths = [directory/channel['published_snapshot'] for channel in channels]
        if all(path.resolve().is_relative_to(directory.resolve()) and path.is_file() for path in paths):
            published[_id(row.get('target'))] = tuple(path.read_bytes() for path in paths)
    source_audits = []
    audit_path = directory/'public-source-diagnostic-audit.json'
    if audit_path.exists():
        projects = [row['words'] for row in events if row.get('operation') == 'ProjectID']
        modules = [row for row in events if row.get('operation') == 'OwnedBackendModule']
        required = {'DZDataABS_CompilerAdapter.dll', 'DZDataABS_Compiler_IEC.dll'}
        allowed = required | {'DZDataABS_Workspace.dll'}
        version = ('1.635.0.1' if modules and all(row.get('version') == '1.635.0.1' for row in modules)
            and required <= {row.get('name') for row in modules} <= allowed else None)
        for row in json.loads(audit_path.read_text(encoding='utf-8')):
            public = row.get('audit', {}).get('original_public_diagnostic')
            raw = row.get('raw_observation')
            if public is None or raw is None:
                source_audits.append({'status': 'unresolved', 'reason': 'original correspondence missing'})
                continue
            original = dict(raw, library={'text': bytes.fromhex(raw['library_hex']).decode('cp936')},
                name={'text': bytes.fromhex(raw['name_hex']).decode('cp936')})
            source_audits.append(compare_public_source_diagnostic(public, row['source_query'], original,
                project_id=projects[0] if len(projects) == 1 else None, native_version=version))
    return analyze_check(events, observations, generated, source_audits=source_audits,
                         published=published, owned_snapshots=owned_snapshots)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze_directory(args.directory), ensure_ascii=False, indent=2))
