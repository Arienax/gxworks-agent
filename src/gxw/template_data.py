# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Access the same template files in source and executable distributions."""


def template_bytes(name: str) -> bytes:
    if name not in {"fx3u.gxw", "nodes.json", "provenance.json", "q02-token-grammar.json", "q03udv-token-grammar.json", "fx-token-encoder-forms.json", "q-token-encoder-forms.json", "blank-fbd-projects.zip.xz",
                                "blank-ladder-projects.zip.xz"}:
        raise ValueError("Unknown built-in GXW template")
    # One reader for these files: Core's storage tables resolve the same directory, so
    # the encoder and the calculator cannot end up reading two copies of one table.
    from plc.storage.tables import resource_bytes
    return resource_bytes(name)
