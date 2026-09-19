"""Bounded in-process observations for tool-based generation handoffs.

A receipt says which context this runtime returned, not which text an external
model actually read or whether its candidate implements the specification. Missing
or expired receipts are trace gaps, never another generation acceptance gate.
"""
from __future__ import annotations

import copy
import threading
import uuid
from collections import OrderedDict


class GenerationReceiptCache:
    def __init__(self, capacity=32):
        self.capacity = max(1, int(capacity))
        self._items = OrderedDict()
        self._lock = threading.Lock()

    def remember(self, binding, handoff):
        receipt_id = "generation-context-" + uuid.uuid4().hex
        with self._lock:
            self._items[receipt_id] = (binding, copy.deepcopy(handoff))
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)
        return receipt_id

    def resolve(self, receipt_id, binding):
        with self._lock:
            record = self._items.get(receipt_id)
            if record is None or record[0] != binding:
                return None
            self._items.move_to_end(receipt_id)
            return copy.deepcopy(record[1])
