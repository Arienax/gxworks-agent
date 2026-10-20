"""Ordered native events with reusable operation and stage indexes."""
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Sequence
from heapq import merge
from itertools import islice


class EventIndex(Sequence):
    def __init__(self, events):
        self.events = tuple(events)
        positions, stages, operation_stages = defaultdict(list), defaultdict(list), defaultdict(list)
        for index, row in enumerate(self.events):
            positions[row.get('operation')].append(index)
            stage = row.get('stage')
            if isinstance(stage, str):
                stages[stage].append(index)
                operation_stages[row.get('operation'), stage].append(index)
        self.positions, self.stages = dict(positions), dict(stages)
        self.operation_stages = dict(operation_stages)

    @classmethod
    def of(cls, events):
        return events if isinstance(events, cls) else cls(events)

    def __len__(self):
        return len(self.events)

    def __getitem__(self, index):
        return self.events[index]

    def has(self, *operations):
        return any(operation in self.positions for operation in operations)

    def count(self, operation):
        return len(self.positions.get(operation, ()))

    def matching(self, prefix):
        return tuple(operation for operation in self.positions
                     if isinstance(operation, str) and operation.startswith(prefix))

    def numbered(self, *operations, stage=None):
        positions = merge(*(self.positions.get(operation, ()) if stage is None else
                            self.operation_stages.get((operation, stage), ())
                            for operation in set(operations)))
        return ((index, self.events[index]) for index in positions)

    def rows(self, *operations, stage=None):
        return (row for _, row in self.numbered(*operations, stage=stage))

    def grouped(self, *operations, key):
        """Group ordered candidates without discarding duplicate observations.

        Callers choose keys matching their typed identities. Unhashable keys
        cannot equal those valid identities and are left out of the groups.
        """
        groups = defaultdict(list)
        for index, row in self.numbered(*operations):
            value = key(row)
            try:
                groups[value].append((index, row))
            except TypeError:
                continue
        return dict(groups)

    def before(self, operation, end):
        positions = self.positions.get(operation, ())
        return (self.events[index] for index in islice(positions, bisect_left(positions, end)))

    def at_stage(self, stage):
        return (self.events[index] for index in self.stages.get(stage, ()))
