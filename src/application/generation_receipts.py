# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Local observations for explicitly bound generation handoffs.

A receipt says which context this runtime returned, not which text an external
model actually read or whether its candidate implements the specification.
Callers validate explicit references against the current project binding; expired
or outdated references require a fresh context rather than a different receipt.
"""
from __future__ import annotations

import copy
import hashlib
import re
import threading
import uuid
from collections import OrderedDict
from pathlib import Path


class ToolReceiptStore:
    """Store completed tool receipts under the workspace's application state."""

    def __init__(self, directory):
        self.directory = Path(directory)

    def _path(self, kind, identity):
        if isinstance(identity, str) and re.fullmatch(r"(?:read-|generation-context-)[a-f0-9]{32}", identity):
            return self.directory / kind / (identity + ".json")
        return None

    def write(self, kind, identity, value):
        from application.workspace import atomic_json
        path = self._path(kind, identity)
        if path is not None:
            atomic_json(path, value)

    def read(self, kind, identity):
        from application.workspace import read_json
        path = self._path(kind, identity)
        if path is None or not path.is_file():
            return None
        return read_json(path)

    def remove(self, kind, identity):
        path = self._path(kind, identity)
        if path is not None:
            path.unlink(missing_ok=True)

    def remember_read_binding(self, binding, document_id, receipt):
        if isinstance(binding, str) and re.fullmatch(r'[a-f0-9]{64}', binding):
            self.write(Path('read_bindings') / binding, document_id, {'receipt': receipt})

    def generation_reads(self, binding):
        if not isinstance(binding, str) or not re.fullmatch(r'[a-f0-9]{64}', binding):
            return {}
        kind = Path('read_bindings') / binding
        return {path.stem: record['receipt'] for path in sorted((self.directory / kind).glob('read-*.json'))
                if (record := self.read(kind, path.stem)) is not None}

    def remember_fact_read(self, scope, document_id, identities):
        kind = self._fact_directory(scope)
        self.write(kind, document_id, {'fact_ids': sorted(identities)})

    def fact_reads(self, scope, identities):
        kind = self._fact_directory(scope)
        for path in sorted((self.directory / kind).glob('read-*.json')):
            record = self.read(kind, path.stem)
            if record and identities.intersection(record['fact_ids']):
                yield path.stem

    @staticmethod
    def _fact_directory(scope):
        return Path('fact_reads') / hashlib.sha256(repr(scope).encode('utf-8')).hexdigest()


class GenerationReceiptCache:
    def __init__(self, capacity=32, *, store=None):
        self.capacity = max(1, int(capacity))
        self._items = OrderedDict()
        self._lock = threading.Lock()
        self._store = store

    def remember(self, binding, handoff):
        receipt_id = "generation-context-" + uuid.uuid4().hex
        with self._lock:
            self._items[receipt_id] = (binding, copy.deepcopy(handoff))
            if self._store is not None:
                self._store.write("generation", receipt_id, {"binding": binding, "handoff": handoff})
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)
        return receipt_id

    def resolve(self, receipt_id, binding):
        with self._lock:
            record = self._items.get(receipt_id)
            if record is None and self._store is not None:
                saved = self._store.read("generation", receipt_id)
                if saved is not None:
                    record = saved["binding"], saved["handoff"]
            if record is None or record[0] != binding:
                return None
            if receipt_id in self._items:
                self._items.move_to_end(receipt_id)
            return copy.deepcopy(record[1])
