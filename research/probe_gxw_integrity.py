"""Inspect native metadata digests and bound source-only patch experiments.

The pinned ProjectOperation DLL's CreateProjectHash/CheckProjectHash use
SHA-256 -> default 128-bit RC4 key derivation -> HMAC-SHA256(file + 0x10).
The embedded seed is an application-format constant, not a user's credential.
Nine lowercase hex digests occupy the last 576 bytes of _hdb. Project.gd2
contains the digest of _hdb including that table. This is a format-integrity
check, not authentication, PLC safety, or permission to edit protected data.

Static RVA 0x36210 and a read-only native CryptoAPI trace independently match
all ten digests. Other versions/providers/layouts remain unsupported. Empty
Project.gd2 is reported as absent, never silently treated as verified.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from pathlib import Path
import re

from gxw.container_writer import validate_cfb_streams, replace_stream_within_allocation, replace_project_stream
from gxw.container import CompoundFile, FREESECT
from gxw.project_metadata import logical_mapping, synchronize_history

NATIVE_DLL_SHA256 = '74e4cd0fd98215eb36a7c5df7103fdb3cc917d636e036f7f8b774a89b01dcfdb'
FORMAT_SEED = bytes.fromhex('20210805776f726477151505a12e71b4c0c1aa518f26fa1c9a300d0de5ba0c77')
FILES = ('dataprotection.xml', 'history.xml', 'label.xml', 'labellink.xml',
         'projectdatalist.xml', 'securitylevel.xml', 'storedhistory.xml', 'user.xml', '_hdb')
TABLE_BYTES = 64 * len(FILES)


def observed_digest(raw: bytes) -> bytes:
    # The installed PROV_RSA_AES default RC4 key length is 128 bits. This
    # exact derivation matches native output; no provider-independent claim.
    key = hashlib.sha256(FORMAT_SEED).digest()[:16]
    return hmac.new(key, raw + b'\x10', hashlib.sha256).hexdigest().encode('ascii')


def _validate_hdb_body(body: bytes) -> tuple[dict[str, bytes], dict]:
    """Validate complete sectors, retaining any unallocated partial suffix.

    Native repeated saves can retain previous digest bytes before the active
    table. They are opaque; neither delete them nor treat them as new sectors.
    Same-size allocation writes leave their bytes and offsets unchanged.
    """
    cfb = CompoundFile(body)
    end = len(body) // cfb.sector_size * cfb.sector_size
    if end != len(body):
        partial_sector = end // cfb.sector_size - 1
        if partial_sector >= len(cfb._fat) or cfb._fat[partial_sector] != FREESECT:
            raise ValueError('partial _hdb suffix intersects an allocated sector')
    payloads = validate_cfb_streams(body[:end])
    suffix = body[end:]
    return payloads, dict(offset=end, bytes=len(suffix), sha256=hashlib.sha256(suffix).hexdigest(),
                          handling='opaque-preserved')


def inspect(source: bytes) -> dict:
    outer = validate_cfb_streams(source)
    result = dict(source_sha256=hashlib.sha256(source).hexdigest(),
                  algorithm='observed-native-metadata-hmac-v1', native_dll_sha256=NATIVE_DLL_SHA256,
                  authentication_claim=False, members=[])
    project = outer.get('Project.gd2')
    if project in (None, b''):
        return dict(result, handling='absent', valid=None)
    if not re.fullmatch(b'[0-9a-f]{64}', project):
        return dict(result, handling='unsupported', valid=None,
                    diagnostic='Project.gd2 is not the observed 64-byte lowercase hex digest')
    hdb = outer.get('_hdb', b'')
    if len(hdb) < 512 + TABLE_BYTES or not re.fullmatch(b'[0-9a-f]{576}', hdb[-TABLE_BYTES:]):
        return dict(result, handling='unsupported', valid=None,
                    diagnostic='missing observed nine-entry _hdb digest trailer')
    missing = [name for name in FILES if name not in outer]
    if missing:
        return dict(result, handling='unsupported', valid=None, diagnostic='missing digest inputs: ' + ', '.join(missing))
    table_offset = len(hdb) - TABLE_BYTES
    for index, name in enumerate(FILES):
        raw = hdb[:table_offset] if name == '_hdb' else outer[name]
        start = table_offset + index * 64
        actual, expected = hdb[start:start + 64], observed_digest(raw)
        result['members'].append(dict(name=name, bytes=len(raw), input_sha256=hashlib.sha256(raw).hexdigest(),
            table_offset=start, stored=actual.decode(), calculated=expected.decode(), valid=actual == expected))
    project_expected = observed_digest(hdb)
    result['project_digest'] = dict(stored=project.decode(), calculated=project_expected.decode(),
                                    valid=project == project_expected)
    result['valid'] = project == project_expected and all(item['valid'] for item in result['members'])
    result['handling'] = 'verified' if result['valid'] else 'differs'
    return result


def stage_same_size_source_patch(source: bytes, logical: str, old: bytes, new: bytes) -> tuple[bytes, dict]:
    """Stage an explicit program patch, retaining the original digest table.

    The generic CFB writer correctly rejects the 576-byte non-CFB suffix.
    Remove it only after verifying the original table, edit the aligned CFB
    body, then restore the exact suffix for a separate integrity refresh.
    """
    if inspect(source)['valid'] is not True or not logical.endswith('.Program.pou') or len(old) != len(new):
        raise ValueError('requires a verified original and explicit same-size program patch')
    outer = validate_cfb_streams(source)
    body, table = outer['_hdb'][:-TABLE_BYTES], outer['_hdb'][-TABLE_BYTES:]
    nested, suffix = _validate_hdb_body(body)
    physical = logical_mapping(outer['projectdatalist.xml'])[logical]
    if nested[physical] != old:
        raise ValueError('source program binding differs')
    history, changes, preserved = synchronize_history(outer['history.xml'], {logical: (physical, old, new)})
    if len(history) != len(outer['history.xml']):
        raise ValueError('history synchronization changed size')
    changed_body = replace_stream_within_allocation(body, physical, new)
    candidate = replace_stream_within_allocation(source, '_hdb', changed_body + table)
    candidate = replace_stream_within_allocation(candidate, 'history.xml', history)
    changed_nested, changed_suffix = _validate_hdb_body(changed_body)
    if changed_nested != dict(nested, **{physical: new}) or changed_suffix != suffix:
        raise ValueError('source edit changed an unrelated nested payload')
    if validate_cfb_streams(candidate) != dict(outer, _hdb=changed_body + table, **{'history.xml': history}):
        raise ValueError('source edit changed an unrelated outer payload')
    return candidate, dict(metadata_changes=changes, preserved_metadata=preserved, opaque_suffix=suffix,
                           unrelated_payloads='byte-identical', integrity='stale; refresh required')


def refresh_after_same_size_patch(source: bytes, candidate: bytes) -> tuple[bytes, dict]:
    """Experimental refresh for a verified source-only patch, never a repair.

    Require a valid original, identical stream inventories and an untouched
    original digest table. Only _hdb payload/history changes are accepted.
    Protection, identity, label-link and security metadata cannot be changed
    through this function. Growth/relocation of the trailer is unsupported.
    This does not make the changed source or PLC program valid.
    """
    before = inspect(source)
    if before['valid'] is not True:
        raise ValueError('original native integrity table is not verified')
    original, proposed = validate_cfb_streams(source), validate_cfb_streams(candidate)
    if original.keys() != proposed.keys() or any(
            original[name] != proposed[name] for name in original if name not in ('_hdb', 'history.xml')):
        raise ValueError('patch changed metadata outside the bounded source/history scope')
    old_hdb, new_hdb = original['_hdb'], proposed['_hdb']
    if len(old_hdb) != len(new_hdb) or old_hdb[-TABLE_BYTES:] != new_hdb[-TABLE_BYTES:]:
        raise ValueError('digest table moved or changed before refresh')
    body = new_hdb[:-TABLE_BYTES]
    original_nested, original_suffix = _validate_hdb_body(old_hdb[:-TABLE_BYTES])
    nested_before, suffix_before = _validate_hdb_body(body)
    if original_suffix != suffix_before:
        raise ValueError('patch changed the opaque partial suffix')
    if original_nested.keys() != nested_before.keys():
        raise ValueError('patch changed nested stream inventory')
    mapping = logical_mapping(original['projectdatalist.xml'])
    allowed = {physical: logical for logical, physical in mapping.items() if logical.endswith('.Program.pou')}
    changed = {physical for physical in original_nested if original_nested[physical] != nested_before[physical]}
    if not changed or changed - allowed.keys():
        raise ValueError('patch changed a nested payload outside program source')
    if any(len(original_nested[name]) != len(nested_before[name]) for name in changed):
        raise ValueError('source payload changed size')
    expected_history, _, _ = synchronize_history(original['history.xml'],
        {allowed[name]: (name, original_nested[name], nested_before[name]) for name in changed})
    if proposed['history.xml'] != expected_history:
        raise ValueError('history differs from the exact source patch synchronization')
    table = b''.join(observed_digest(body if name == '_hdb' else proposed[name]) for name in FILES)
    refreshed_hdb = body + table
    result = replace_stream_within_allocation(candidate, '_hdb', refreshed_hdb)
    result = replace_stream_within_allocation(result, 'Project.gd2', observed_digest(refreshed_hdb))
    expected = dict(proposed, _hdb=refreshed_hdb, **{'Project.gd2': observed_digest(refreshed_hdb)})
    if validate_cfb_streams(result) != expected or _validate_hdb_body(refreshed_hdb[:-TABLE_BYTES]) != (nested_before, suffix_before):
        raise ValueError('digest refresh changed unrelated stream payloads')
    after = inspect(result)
    if after['valid'] is not True:
        raise ValueError('refreshed digest table does not verify')
    return result, dict(original_sha256=before['source_sha256'], candidate_sha256=hashlib.sha256(candidate).hexdigest(),
        result_sha256=after['source_sha256'], table_offset=len(body),
        changed_entries=[name for index, name in enumerate(FILES)
                         if old_hdb[-TABLE_BYTES + index * 64:][:64] != table[index * 64:index * 64 + 64]],
        nested_payloads='identical to candidate; only outer integrity bytes refreshed',
        scope='experimental same-size source/history patch; no native acceptance claim')


def patch_verified_payloads(source: bytes, replacements: dict[str, tuple[bytes, bytes]]) -> tuple[bytes, dict]:
    """Apply explicit, source-bound program/declaration edits under a valid table.

    Payload semantics remain the caller's responsibility. Unknown/protection
    metadata and unrelated payloads are preserved. Aligned bodies may grow
    through the existing CFB allocator; bodies with an opaque partial suffix
    currently allow only same-size edits, retaining that suffix in place.
    """
    before = inspect(source)
    if before['valid'] is not True:
        raise ValueError('original native integrity table is not verified')
    if not 1 <= len(replacements) <= 64 or any(
            not name.endswith(('.Program.pou', '.Labels.lh', '.gh', '.lnb', '.lbo',
                               '.lnl', '.llv', '.lng', '.lgv', '.lns', '.lst', '.tsk', '.res')) for name in replacements):
        raise ValueError('outside the bounded program/declaration patch scope')
    outer = validate_cfb_streams(source)
    body = outer['_hdb'][:-TABLE_BYTES]
    nested, suffix = _validate_hdb_body(body)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    bound, expected_nested = {}, dict(nested)
    for logical, (old, new) in replacements.items():
        physical = mapping[logical]
        if physical not in nested or nested[physical] != old or physical in [item[0] for item in bound.values()]:
            raise ValueError('source binding is missing, different or ambiguous')
        if suffix['bytes'] and len(old) != len(new):
            raise ValueError('growth with an opaque partial _hdb suffix is not validated')
        bound[logical] = (physical, old, new)
        expected_nested[physical] = new
    history, changes, preserved = synchronize_history(outer['history.xml'], bound)
    methods = []
    for logical, (physical, old, new) in bound.items():
        if suffix['bytes']:
            body = replace_stream_within_allocation(body, physical, new)
            method = 'existing_allocation_with_opaque_suffix'
        else:
            body, method = replace_project_stream(body, physical, new)
        methods.append(dict(object=logical, method=method, before_bytes=len(old), after_bytes=len(new)))
    payloads, remaining_suffix = _validate_hdb_body(body)
    if payloads != expected_nested or suffix['bytes'] and remaining_suffix != suffix:
        raise ValueError('patch changed unrelated nested payloads or opaque suffix')
    updated = dict(outer, **{'history.xml': history})
    table = b''.join(observed_digest(body if name == '_hdb' else updated[name]) for name in FILES)
    hdb = body + table
    result, outer_method = replace_project_stream(source, '_hdb', hdb)
    result, _ = replace_project_stream(result, 'history.xml', history)
    result, _ = replace_project_stream(result, 'Project.gd2', observed_digest(hdb))
    expected_outer = dict(updated, _hdb=hdb, **{'Project.gd2': observed_digest(hdb)})
    if validate_cfb_streams(result) != expected_outer or inspect(result)['valid'] is not True:
        raise ValueError('patch did not preserve its outer payload/digest contract')
    return result, dict(metadata_changes=changes, preserved_metadata=preserved,
        input_sha256=before['source_sha256'], output_sha256=hashlib.sha256(result).hexdigest(),
        methods=methods, outer_hdb_method=outer_method, opaque_suffix=suffix,
        integrity='verified observed metadata table; not native compilation or authorization',
        unrelated_payloads='byte-identical')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('-o', '--output', type=Path)
    args = parser.parse_args()
    result = inspect(args.source.read_bytes())
    if args.output:
        with args.output.open('x', encoding='utf-8') as output:
            json.dump(result, output, ensure_ascii=False, indent=2)
            output.write('\n')
    print(json.dumps(result, ensure_ascii=True))
