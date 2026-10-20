"""Native engineering errors helpers."""
from __future__ import annotations


class NativeEngineeringDriverError(RuntimeError):
    def __init__(self, message, *, observation=None):
        super().__init__(message)
        self.observation = observation or {}
