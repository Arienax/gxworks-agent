#!/usr/bin/env python3
"""Canonical FX3U knowledge builder with semantic device-entity cleanup.

This wraps build_fx3u_knowledge_v3 without duplicating its large implementation.
The generic extractor remains unchanged; only its result is post-processed so
PDF/layout artifacts cannot become device_records.
"""
from __future__ import annotations

import build_fx3u_knowledge_v3 as base
from device_entity_cleanup import sanitize_device_like_entities

_original_extract_entities = base.extract_entities


def extract_entities_clean(
    text: str,
    instruction_re,
    *,
    chunk_type: str,
    explicit_entities=(),
):
    extracted = _original_extract_entities(
        text,
        instruction_re,
        chunk_type=chunk_type,
        explicit_entities=explicit_entities,
    )
    return sanitize_device_like_entities(text, chunk_type, extracted)


def main(argv=None):
    base.extract_entities = extract_entities_clean
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
