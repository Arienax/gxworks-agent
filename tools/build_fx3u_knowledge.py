#!/usr/bin/env python3
"""Compatibility-named entrypoint for the catalogue-driven knowledge pipeline.

Model scopes come from sources and Registry; the historical filename is not a
restriction on future CPU families. Use --compile-definitions-only to reuse an
existing evidence index without reparsing already-preserved PDFs.
"""
from __future__ import annotations

from build_fx3u_knowledge_clean import main


if __name__ == "__main__":
    raise SystemExit(main())
