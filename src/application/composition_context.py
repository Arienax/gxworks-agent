"""Request-local, opt-in experiment selection shared by Web/MCP generation.

No persistent model settings or engineering specification fields are introduced.
Arms are incremental: A existing, B grouping, C relations, D composition.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class CompositionExperiment:
    arm: str = "A"
    relation_limit: int = 12
    input_version: tuple = ()

    def __post_init__(self):
        if self.arm not in {"A", "B", "C", "D"}:
            raise ValueError("composition arm must be A, B, C or D")
        if type(self.relation_limit) is not int or not 0 <= self.relation_limit <= 64:
            raise ValueError("invalid relation expansion limit")


_EXPERIMENT = ContextVar("composition_experiment", default=CompositionExperiment())


def composition_experiment():
    return _EXPERIMENT.get()


@contextmanager
def composition_scope(options):
    if not isinstance(options, CompositionExperiment):
        raise TypeError("composition scope requires CompositionExperiment")
    token = _EXPERIMENT.set(options)
    try:
        yield options
    finally:
        _EXPERIMENT.reset(token)
