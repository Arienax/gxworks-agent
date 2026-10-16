"""Implementation details excluded from both public application transports.

Core retains native observations for binding bytes, source and compiler phases.
Clients receive the resulting engineering diagnostics and completion states.
"""
from __future__ import annotations


_NATIVE_INTERNAL_FIELDS = frozenset({
    'native_workspace', 'lexical_reader', 'graph_profile',
    'native_code_lexical_reads', 'source_references',
    'module', 'module_base', 'module_version', 'rva', 'vtable',
    'native_cpu', 'native_versions', 'compiled_interval',
})


def public_result_field(key):
    name = str(key)
    return not name.startswith('_') and name not in _NATIVE_INTERNAL_FIELDS
