# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Compatibility entry for the Q/L storage plan that now lives in Core.

The rules moved to :mod:`plc.storage.plan`, which Core calls directly. This module
stays so existing GXW callers and scripts keep one import; it adds no rule of its own.
"""
from __future__ import annotations

from plc.storage.plan import (allocation_dependent_operands, context_cpu_mismatch,
                             cpu_storage_mode, resolve_stored_steps)

__all__ = ["allocation_dependent_operands", "context_cpu_mismatch", "cpu_storage_mode",
           "resolve_stored_steps"]
