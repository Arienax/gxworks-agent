"""Template access for source builds; replaced with embedded data during compilation."""
from pathlib import Path


def template_bytes(name: str) -> bytes:
    if name not in {"fx3u.gxw", "nodes.json", "provenance.json", "q02-token-grammar.json", "q03udv-token-grammar.json", "blank-fbd-projects.zip.xz"}:
        raise ValueError("Unknown built-in GXW template")
    return (Path(__file__).with_name("templates") / name).read_bytes()
