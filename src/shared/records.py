"""Identifier and resolved-path checks shared by durable record boundaries."""
from pathlib import Path
import re

_RECORD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def valid_record_id(value) -> bool:
    return isinstance(value, str) and _RECORD_ID.fullmatch(value) is not None


def record_id(value, *, message="Invalid record ID", error_type=ValueError) -> str:
    if not valid_record_id(value):
        raise error_type(message)
    return value


def contained(path, root, *, allow_root=True, message="Artifact escapes its managed directory", error_type=ValueError) -> Path:
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root) or (not allow_root and path == root):
        raise error_type(message)
    return path
