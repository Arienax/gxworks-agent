# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Native engineering errors helpers."""
from __future__ import annotations


class NativeEngineeringDriverError(RuntimeError):
    def __init__(self, message, *, observation=None):
        super().__init__(message)
        self.observation = observation or {}
