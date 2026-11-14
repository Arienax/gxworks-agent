"""Offline, version-scoped inverse data for runtime research projection.

Only compilation imports this module. Runtime readers never open the archive.
An uninstalled candidate cannot replace the research data of the active version.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import zlib


class RuntimeResearchArchive:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS versions(version TEXT PRIMARY KEY, parent TEXT);
            CREATE TABLE IF NOT EXISTS owner_research(
                version TEXT, vendor TEXT, opcode TEXT, model TEXT, payload BLOB NOT NULL,
                PRIMARY KEY(version,vendor,opcode,model));
        ''')
        self.version = None
        self._parents = {}

    def begin(self, version, parent):
        if version == parent:
            raise ValueError('Candidate research version must differ from its input')
        if self.db.execute('SELECT 1 FROM versions WHERE version=?', (version,)).fetchone():
            raise ValueError('Research version already exists')
        self.db.execute('INSERT INTO versions VALUES (?,?)', (version, parent))
        self.version = version

    def put(self, entry, sidecar, *, admission_input=None):
        if self.version is None:
            raise ValueError('Begin a candidate version before writing research')
        identity = (entry.get('vendor', 'mitsubishi'), entry['opcode'], entry['target_model'])
        if identity != (sidecar.get('vendor', 'mitsubishi'), sidecar['opcode'], sidecar['target_model']):
            raise ValueError('Research owner differs from projected owner')
        value = {'sidecar': sidecar, 'admission_input': admission_input or {}}
        encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        self.db.execute('INSERT INTO owner_research VALUES (?,?,?,?,?)',
                        (self.version, *identity, zlib.compress(encoded)))

    def get(self, version, *, vendor='mitsubishi', opcode, model):
        while version:
            row = self.db.execute('SELECT payload FROM owner_research '
                'WHERE version=? AND vendor=? AND opcode=? AND model=?',
                (version, vendor, opcode, model)).fetchone()
            if row:
                return json.loads(zlib.decompress(row[0]))
            if version not in self._parents:
                parent = self.db.execute('SELECT parent FROM versions WHERE version=?', (version,)).fetchone()
                self._parents[version] = parent[0] if parent else None
            version = self._parents[version]
        return None

    def restore(self, version, entry):
        saved = self.get(version, vendor=entry.get('vendor', 'mitsubishi'),
                         opcode=entry['opcode'], model=entry['target_model'])
        if saved is None:
            return entry
        from knowledge.runtime_fact_projection import restore_runtime_research
        restored = restore_runtime_research(entry, saved['sidecar'])
        for field, previous in saved['admission_input'].items():
            if previous['present']:
                restored[field] = previous['value']
            else:
                restored.pop(field, None)
        return restored

    def commit(self):
        self.db.commit()

    def close(self):
        # An incomplete compile rolls its version back; previously committed
        # versions remain readable and need no copy of the runtime database.
        self.db.close()
