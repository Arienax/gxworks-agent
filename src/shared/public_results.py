# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Implementation details excluded from both public application transports.

Core retains native observations for binding bytes, source and compiler phases.
Clients receive the resulting engineering diagnostics and completion states.
"""
from __future__ import annotations

from .privacy import private_field


_NATIVE_INTERNAL_FIELDS = frozenset({
    'native_workspace', 'lexical_reader', 'graph_profile',
    'native_code_lexical_reads', 'source_references',
    'module', 'module_base', 'module_version', 'rva', 'vtable',
    'native_cpu', 'native_versions', 'compiled_interval',
})


def public_result_field(key):
    name = str(key)
    return not private_field(name) and name not in _NATIVE_INTERNAL_FIELDS
