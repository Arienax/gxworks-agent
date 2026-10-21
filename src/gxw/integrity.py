"""Preserve and refresh the observed GXW native save envelope offline.

Some native files append nine metadata integrity records to the nested CFB and
store its final integrity record in ``Project.gd2``.  These are existing file
format fields, not source freshness, authentication, or compiler evidence.
The layout and derivation were matched against native CreateProjectHash output;
unknown layouts are retained as unsupported instead of being stripped.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import re
from typing import Mapping

from .container import CompoundFile, FREESECT
from .container_writer import (replace_project_stream, replace_stream_within_allocation,
                               validate_cfb_streams)
from .models import GXWFormatError


INTEGRITY_MEMBERS = ('dataprotection.xml', 'history.xml', 'label.xml', 'labellink.xml',
                     'projectdatalist.xml', 'securitylevel.xml', 'storedhistory.xml',
                     'user.xml', '_hdb')
INTEGRITY_TABLE_BYTES = 64 * len(INTEGRITY_MEMBERS)
# Application format constant recovered from the native hash derivation. It is
# neither a user credential nor permission to edit a protected project.
_FORMAT_SEED = bytes.fromhex('20210805776f726477151505a12e71b4c0c1aa518f26fa1c9a300d0de5ba0c77')
_FORMAT_KEY = hashlib.sha256(_FORMAT_SEED).digest()[:16]


class GXWIntegrityError(GXWFormatError):
    """A recognized corrupt envelope or an unrecognized native save layout."""

    def __init__(self, message: str, *, status: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class HdbEnvelope:
    """Validated CFB body and the native fields that surround it.

    ``data`` includes an observed unallocated partial suffix, if present, but
    excludes the active integrity table. The suffix cannot move during edits.
    """

    data: bytes
    payloads: dict[str, bytes]
    opaque_suffix: bytes = b''
    table: bytes = b''
    status: str = 'absent'

    @property
    def active(self) -> bool:
        return self.status == 'verified'


def _native_integrity_record(raw: bytes) -> bytes:
    # Native CryptoAPI uses SHA-256 -> a default 128-bit RC4-derived HMAC key,
    # and hashes one final 0x10 byte after the file data.
    return hmac.new(_FORMAT_KEY, bytes(raw) + b'\x10', hashlib.sha256).hexdigest().encode('ascii')


def _read_hdb_body(body: bytes, *, native_table: bool = False) -> tuple[dict[str, bytes], bytes]:
    cfb = CompoundFile(body)
    end = len(body) // cfb.sector_size * cfb.sector_size
    suffix = body[end:]
    if suffix:
        sector = end // cfb.sector_size - 1
        if sector < 0 or sector >= len(cfb._fat) or cfb._fat[sector] != FREESECT:
            if native_table:
                # Native SaveProject can end _hdb exactly at the final regular
                # stream's declared byte count, then append its integrity table.
                # These are live data bytes, not an opaque save-record suffix.
                # Validate every allocation before accepting that exact extent;
                # extra bytes within an allocated sector remain unrecognized.
                payloads = validate_cfb_streams(body)
                for entry in cfb.iter_streams():
                    if entry.stream_size < cfb.mini_stream_cutoff:
                        continue
                    chain = cfb._walk_chain(entry.start_sector, cfb._fat)
                    if (chain and chain[-1] == sector
                            and entry.stream_size - (len(chain) - 1) * cfb.sector_size == len(suffix)):
                        return payloads, b''
                # A native resave can retain the previous nine-record table.
                # At this observed FAT-capacity boundary its first sector is
                # explicitly free, but its last record has no FAT entry. Keep
                # the complete old table opaque; never infer free allocation
                # from an arbitrary out-of-range suffix or consume live data.
                old_table_start = len(body) - INTEGRITY_TABLE_BYTES
                if (sector >= len(cfb._fat)
                        and old_table_start >= cfb.sector_size
                        and old_table_start % cfb.sector_size == 0
                        and old_table_start // cfb.sector_size == len(cfb._fat)
                        and cfb._fat[-1] == FREESECT
                        and re.fullmatch(b'[0-9a-f]{576}', body[old_table_start:])):
                    try:
                        prefix_payloads = validate_cfb_streams(body[:old_table_start])
                    except GXWFormatError:
                        pass
                    else:
                        if prefix_payloads == payloads:
                            return payloads, body[old_table_start:]
                raise GXWIntegrityError('partial _hdb suffix intersects an allocated sector', status='unsupported')
            # Ordinary CFBs may omit unused padding from the last allocated
            # sector. The existing writer validates all stored payload bytes
            # before adding that padding; these bytes are not an opaque tail.
            return validate_cfb_streams(body), b''
    return validate_cfb_streams(body[:end]), suffix


def read_project_hdb(outer: Mapping[str, bytes]) -> HdbEnvelope:
    """Read nested streams without treating a native integrity table as CFB.

    Missing/empty Project.gd2 uses the ordinary CFB format. A nonempty native
    entry is accepted only with its matching observed nine-entry table. No
    installation or original native component is loaded.
    """
    if '_hdb' not in outer:
        raise GXWFormatError('required GXW stream is missing: _hdb')
    raw = bytes(outer['_hdb'])
    project = outer.get('Project.gd2')
    if project in (None, b''):
        payloads, suffix = _read_hdb_body(raw)
        return HdbEnvelope(raw, payloads, suffix)
    if not re.fullmatch(b'[0-9a-f]{64}', project):
        raise GXWIntegrityError('unsupported nonempty Project.gd2 save envelope', status='unsupported')
    if (len(raw) < 512 + INTEGRITY_TABLE_BYTES
            or not re.fullmatch(b'[0-9a-f]{576}', raw[-INTEGRITY_TABLE_BYTES:])):
        raise GXWIntegrityError('unsupported native _hdb integrity trailer', status='unsupported')
    missing = [name for name in INTEGRITY_MEMBERS if name not in outer]
    if missing:
        raise GXWIntegrityError('native integrity inputs are missing: ' + ', '.join(missing), status='corrupt')
    body, table = raw[:-INTEGRITY_TABLE_BYTES], raw[-INTEGRITY_TABLE_BYTES:]
    invalid = [name for index, name in enumerate(INTEGRITY_MEMBERS)
               if table[index * 64:(index + 1) * 64]
               != _native_integrity_record(body if name == '_hdb' else outer[name])]
    if project != _native_integrity_record(raw):
        invalid.append('Project.gd2')
    if invalid:
        raise GXWIntegrityError('native integrity records differ: ' + ', '.join(invalid), status='corrupt')
    payloads, suffix = _read_hdb_body(body, native_table=True)
    return HdbEnvelope(body, payloads, suffix, table, 'verified')


def inspect_project_integrity(outer: Mapping[str, bytes]) -> dict:
    """Return capability diagnostics for absent, verified, unsupported or corrupt."""
    try:
        envelope = read_project_hdb(outer)
    except GXWIntegrityError as error:
        return {'status': error.status, 'supported': False, 'message': str(error)}
    except GXWFormatError as error:
        return {'status': 'corrupt', 'supported': False, 'message': str(error)}
    return {'status': envelope.status, 'supported': True,
            'opaque_suffix_bytes': len(envelope.opaque_suffix),
            'size_changes_supported': not envelope.opaque_suffix}


def replace_hdb_streams(envelope: HdbEnvelope,
                        replacements: Mapping[str, bytes]) -> tuple[bytes, list[dict]]:
    """Replace existing physical streams, preserving all other payloads.

    An unallocated partial suffix may contain older native save records; only
    equal-length replacements are established for that layout. CFBs without an
    opaque suffix reuse the allocator, which validates live data before padding
    an incomplete final sector for stream growth and shrinkage.
    """
    data, allocations = envelope.data, []
    expected = dict(envelope.payloads)
    for stream, supplied in replacements.items():
        if stream not in expected:
            raise GXWFormatError('offline write cannot add a nested stream: ' + stream)
        if not isinstance(supplied, (bytes, bytearray, memoryview)):
            raise GXWFormatError('nested stream replacement must be bytes: ' + stream)
        replacement = bytes(supplied)
        original = expected[stream]
        if replacement == original:
            continue
        if envelope.opaque_suffix:
            if len(original) != len(replacement):
                raise GXWIntegrityError('size change with an opaque partial _hdb suffix is unsupported',
                                        status='unsupported')
            data = replace_stream_within_allocation(data, stream, replacement)
            mode = 'existing_allocation_with_opaque_suffix'
        else:
            data, mode = replace_project_stream(data, stream, replacement)
        expected[stream] = replacement
        allocations.append({'layer': 'nested', 'stream': stream, 'mode': mode})
    actual, suffix = _read_hdb_body(data, native_table=envelope.active)
    if actual != expected or envelope.opaque_suffix != suffix:
        raise GXWFormatError('offline nested stream or opaque suffix preservation check failed')
    return data, allocations


def rebuild_project_integrity(envelope: HdbEnvelope, outer: Mapping[str, bytes],
                              hdb_body: bytes) -> dict[str, bytes]:
    """Refresh only existing save fields against the final in-memory payloads.

    Call after history and source replacements are prepared. The returned
    mapping is suitable for the existing outer CFB transaction; this function
    neither writes a file nor creates a native table in an unsigned project.
    """
    body = bytes(hdb_body)
    payloads, suffix = _read_hdb_body(body, native_table=envelope.active)
    if payloads.keys() != envelope.payloads.keys() or suffix != envelope.opaque_suffix:
        raise GXWFormatError('offline write changed nested inventory or opaque suffix')
    updated = dict(outer)
    if not envelope.active:
        if updated.get('Project.gd2') not in (None, b''):
            raise GXWIntegrityError('cannot create a native save envelope offline', status='unsupported')
        updated['_hdb'] = body
        return updated
    missing = [name for name in INTEGRITY_MEMBERS if name not in updated]
    if missing:
        raise GXWIntegrityError('native integrity inputs are missing: ' + ', '.join(missing), status='corrupt')
    table = b''.join(_native_integrity_record(body if name == '_hdb' else updated[name])
                     for name in INTEGRITY_MEMBERS)
    updated['_hdb'] = body + table
    updated['Project.gd2'] = _native_integrity_record(updated['_hdb'])
    return updated
