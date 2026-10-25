"""Resolve the saved GXW file an open GX Works2 window is working on.

The MDI caption is a display string, not a path.  GX Works2 abbreviates a long
project path with a leading ``...`` and shows a placeholder such as
``(工程未设置)`` until the project is first saved, so a caption can neither be
used directly as a path nor be turned back into one.  It is used here to
*corroborate* a path that came from somewhere else, never to invent one.

What is being resolved is not "which file does the user mean" but "which file
will this import modify".  The target is always the project open in the window;
the path only decides which file gets backed up first.  A path the window does
not corroborate would protect the wrong file while the open project is written
to, so it is refused instead of adopted.

Trust order, strongest first:

1. ``explicit``  - the caller named the file and the caption agrees with it.
2. ``caption``   - the caption is itself a complete absolute ``.gxw`` path.
3. ``candidates``- exactly one known ``.gxw`` matches the caption.

Anything else resolves to ``None`` with a reason.  Guessing is deliberately not
an option: writing into the wrong project is worse than refusing to write.
"""

from __future__ import annotations

import ntpath
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple

ABBREVIATION_PREFIX = "..."
UNSAVED_PROJECT_NAMES = frozenset(
    {
        "(工程未设置)",
        "工程未设置",
        "(project not set)",
        "project not set",
    }
)
SKIP_DIRECTORY_NAMES = frozenset(
    {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv"}
)

EXPLICIT = "explicit"
CAPTION = "caption"
CANDIDATE = "candidate"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProjectPathResolution:
    """Outcome of a path lookup; ``path`` is None when nothing was corroborated."""

    path: Optional[Path] = None
    source: str = UNKNOWN
    reason: str = ""
    candidates: Tuple[Path, ...] = ()

    def __bool__(self) -> bool:
        return self.path is not None


def caption_matches_path(caption, path) -> bool:
    """True when an abbreviated caption can only describe ``path``.

    This is a guard, never a source of information: it answers "could this
    window be showing that file", not "which file is that".
    """

    text = caption if isinstance(caption, str) else ""
    text = text.strip()
    if not text.startswith(ABBREVIATION_PREFIX):
        return False
    tail = text[len(ABBREVIATION_PREFIX) :]
    if not tail:
        return False
    target = str(path)
    return (
        ntpath.normcase(target).endswith(ntpath.normcase(tail))
        and text.casefold().endswith(ntpath.basename(target).casefold())
    )


def caption_label(caption) -> str:
    """Return the file name a caption refers to, or "" when it names none."""

    text = str(caption or "").strip()
    if not text or text.casefold() in UNSAVED_PROJECT_NAMES:
        return ""
    label = ntpath.basename(text)
    return "" if label.casefold() in UNSAVED_PROJECT_NAMES else label


def caption_describes(caption, path) -> bool:
    """True when the caption is consistent with ``path`` being active.

    Accepts a complete path, an abbreviated caption, or a bare project name.
    An empty or unsaved caption asserts nothing; callers that need proof must
    treat that as unknown rather than as agreement.
    """

    text = str(caption or "").strip()
    if not text or text.casefold() in UNSAVED_PROJECT_NAMES:
        return True
    target = str(path)
    if text.startswith(ABBREVIATION_PREFIX):
        return caption_matches_path(text, target)
    if ntpath.isabs(text):
        return ntpath.normcase(text) == ntpath.normcase(target)
    return text.casefold() == ntpath.basename(target).casefold()


def _as_project_file(value) -> Optional[Path]:
    text = str(value or "").strip()
    if not text or text.casefold() in UNSAVED_PROJECT_NAMES:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute() or candidate.suffix.casefold() != ".gxw":
        return None
    return candidate


def _unique(paths: Iterable[Path]) -> Tuple[Path, ...]:
    seen = {}
    for path in paths:
        seen.setdefault(ntpath.normcase(str(path)), path)
    return tuple(seen.values())


def discover_candidates(label, roots, *, max_depth: int = 4, limit: int = 256) -> Tuple[Path, ...]:
    """Find saved projects named ``label`` under ``roots``, bounded on purpose.

    The search stays shallow, skips dependency directories and never looks
    outside the roots the caller named, because the result may be written into.
    """

    wanted = str(label or "").strip().casefold()
    if not wanted:
        return ()
    found = []
    for root in roots or ():
        base = Path(str(root)).expanduser()
        if not base.is_dir():
            continue
        base_depth = len(base.parts)
        for current, dirnames, _filenames in os.walk(base):
            depth = len(Path(current).parts) - base_depth
            if depth >= max_depth:
                dirnames[:] = []
            else:
                dirnames[:] = [
                    name for name in dirnames
                    if name.casefold() not in SKIP_DIRECTORY_NAMES
                ]
            for name in _filenames:
                if name.casefold() != wanted:
                    continue
                path = Path(current) / name
                if path.is_file():
                    found.append(path)
                    if len(found) >= limit:
                        return _unique(found)
    return _unique(found)


def resolve_project_path(caption, *, explicit=None, candidates: Iterable = ()) -> ProjectPathResolution:
    """Return the project file for ``caption`` using the trust order above."""

    text = str(caption or "").strip()
    silent = not text or text.casefold() in UNSAVED_PROJECT_NAMES

    if explicit is not None and str(explicit).strip():
        named = _as_project_file(explicit)
        if named is None:
            return ProjectPathResolution(
                source=EXPLICIT, reason="指定的工程路径必须是 .gxw 文件的绝对路径。"
            )
        if not named.is_file():
            return ProjectPathResolution(
                source=EXPLICIT, reason=f"指定的工程文件不存在：{named}"
            )
        if silent:
            # Nothing to compare against: the window has no saved project, so the
            # named file cannot be the one that is about to be modified.
            return ProjectPathResolution(
                source=EXPLICIT,
                reason=(
                    "当前窗口还没有已保存的工程，无法确认指定文件就是将被写入的工程；"
                    "未继续写入。"
                ),
            )
        if not caption_describes(text, named):
            return ProjectPathResolution(
                source=EXPLICIT,
                reason=(
                    "指定的工程文件与当前 GX Works2 窗口不是同一个工程；未继续写入。"
                    f"（窗口：{text}，指定：{named.name}）"
                ),
            )
        return ProjectPathResolution(path=named, source=EXPLICIT)

    caption_path = _as_project_file(text)
    if caption_path is not None:
        if not caption_path.is_file():
            return ProjectPathResolution(
                source=CAPTION, reason=f"窗口标题指向的工程文件不存在：{caption_path}"
            )
        return ProjectPathResolution(path=caption_path, source=CAPTION)

    label = caption_label(text)
    if not label:
        return ProjectPathResolution(reason="当前窗口没有已保存的工程。")

    abbreviated = text.startswith(ABBREVIATION_PREFIX)
    matched, mismatched = [], []
    for item in candidates:
        candidate = _as_project_file(item)
        if candidate is None or candidate.name.casefold() != label.casefold():
            continue
        if not candidate.is_file():
            continue
        if abbreviated and not caption_matches_path(text, candidate):
            mismatched.append(candidate)
            continue
        matched.append(candidate)
    matched = _unique(matched)
    if len(matched) == 1:
        return ProjectPathResolution(path=matched[0], source=CANDIDATE)
    if len(matched) > 1:
        return ProjectPathResolution(
            source=CANDIDATE,
            reason="找到多个同名工程，无法确定当前窗口对应哪一个；请显式指定工程路径。",
            candidates=matched,
        )
    if mismatched:
        return ProjectPathResolution(
            source=CANDIDATE,
            reason=(
                "找到同名工程，但其路径与窗口标题显示的目录不一致；"
                "请显式指定工程路径，或把工程另存到较短的路径后重试。"
            ),
            candidates=_unique(mismatched),
        )
    return ProjectPathResolution(
        reason=(
            "窗口标题只给出被截断的工程名，未包含完整路径；"
            "请显式指定工程路径，或把工程另存到较短的路径后重试。"
        )
    )
