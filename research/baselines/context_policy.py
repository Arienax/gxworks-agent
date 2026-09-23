"""Frozen context-policy behavior used only by the historical prompt baseline.

This module is not imported by production code. It exists so research comparisons
can reconstruct the pre-unified prompt/RAG behavior without keeping that control
surface in src/.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class _LegacyPolicy:
    legacy: bool = True
    examples: bool = True
    manuals: str = "automatic"


def resolve_context_policy(_value=None):
    return _LegacyPolicy()


def manual_lookup_decision(query: str):
    return (bool(str(query or "").strip()), "legacy_automatic_policy" if str(query or "").strip() else "empty_query")


def select_base_prompt(original: str, _target_mode: str) -> str:
    return original


def audit_section(*_args, **_kwargs) -> None:
    return None
