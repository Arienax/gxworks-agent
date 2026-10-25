"""Resolve the saved GXW file an open GX Works2 window is working on.

The MDI caption is a display string, not a path.  GX Works2 abbreviates a long
project path with a leading ``...`` and shows a placeholder such as
``(工程未设置)`` until the project is first saved, so a caption can neither be
used directly as a path nor be turned back into one.  It is used here to
*corroborate* a path that came from somewhere else, never to invent one.

What is being resolved is not "which file does the user mean" but "which file
will this import modify".  The target is always the project open in the window;
the path only decides which file gets backed up first.  Backing up a file the
window does not hold would leave the real target unprotected, so anything
unproven is refused instead of adopted.

An abbreviated caption only carries a suffix, so it cannot distinguish
``D:\\work\\plc\\line.gxw`` from ``D:\\backup\\plc\\line.gxw``.  Callers that can
prove ownership - GX Works2 holds the file open - pass ``owns``; without that
proof a truncated or bare caption is refused.  A caption that already names the
file completely needs no proof, because nothing is left to disambiguate.

Trust order, strongest first:

1. ``explicit``  - the caller named the file, the caption agrees, and the
   window's process holds it - except when the caption is already a complete
   path, which needs no further proof.
2. ``caption``   - the caption is itself a complete absolute ``.gxw`` path.
3. ``candidates``- exactly one known ``.gxw`` matches the caption.

Anything else resolves to ``None`` with a reason.
"""

from __future__ import annotations

import ntpath
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple

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

BINDING_OWNER = "file-owner"
BINDING_CAPTION = "caption"
BINDING_NONE = "none"

MATCH = "match"
CONFLICT = "conflict"
SILENT = "silent"


@dataclass(frozen=True)
class ProjectPathResolution:
    """Outcome of a path lookup; ``path`` is None when nothing was corroborated."""

    path: Optional[Path] = None
    source: str = UNKNOWN
    reason: str = ""
    candidates: Tuple[Path, ...] = ()
    binding: str = BINDING_NONE

    def __bool__(self) -> bool:
        return self.path is not None


def caption_matches_path(caption, path) -> bool:
    """True when an abbreviated caption can only describe ``path``.

    This is a guard, never a source of information: it answers "could this
    window be showing that file", not "which file is that".  A suffix match does
    not distinguish two paths that share the visible tail.
    """

    text = str(caption or "").strip()
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


def caption_agreement(caption, path) -> str:
    """How the caption relates to ``path``: ``MATCH``, ``CONFLICT`` or ``SILENT``.

    ``SILENT`` means the caption asserts nothing (empty or unsaved).  It is a
    third state on purpose: treating "no assertion" as agreement is how a wrong
    path gets adopted.
    """

    text = str(caption or "").strip()
    if not text or text.casefold() in UNSAVED_PROJECT_NAMES:
        return SILENT
    target = str(path)
    if text.startswith(ABBREVIATION_PREFIX):
        return MATCH if caption_matches_path(text, target) else CONFLICT
    if ntpath.isabs(text):
        return MATCH if ntpath.normcase(text) == ntpath.normcase(target) else CONFLICT
    return MATCH if text.casefold() == ntpath.basename(target).casefold() else CONFLICT


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
    if isinstance(roots, (str, Path)):
        roots = (roots,)
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


def _remedy(owns) -> str:
    """What the operator can actually do, given whether proof is obtainable.

    When the session can prove ownership, naming the file is a real way out; when
    it cannot, no explicit path would ever be accepted, so only the short-path
    advice is honest.
    """

    if owns is None:
        return "请把工程另存到较短路径后重试。"
    return (
        "请显式指定与窗口标题一致的工程路径（该文件必须正被 GX Works2 持有），"
        "或把工程另存到较短路径后重试。"
    )


def resolve_project_path(
    caption,
    *,
    explicit=None,
    candidates: Iterable = (),
    owns: Optional[Callable[[Path], bool]] = None,
) -> ProjectPathResolution:
    """Return the project file for ``caption`` using the trust order above.

    ``owns`` proves that the window's process holds a candidate file open.  It is
    the only check that survives a shared caption suffix, so it is required
    whenever the caption carries less than a complete path: a truncated tail or a
    bare file name cannot identify the file on its own.  Without it those cases
    are refused rather than reported as caption-bound.
    """

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
        if caption_agreement(text, named) == CONFLICT:
            return ProjectPathResolution(
                source=EXPLICIT,
                reason=(
                    "指定的工程文件与当前 GX Works2 窗口不是同一个工程；未继续写入。"
                    f"（窗口：{text}，指定：{named.name}）"
                ),
            )
        if ntpath.isabs(text):
            # The caption already names the file completely, so ownership adds no
            # information.  Demanding it here would refuse an unambiguous path on
            # shares where the query is unavailable.
            return ProjectPathResolution(
                path=named, source=EXPLICIT, binding=BINDING_CAPTION
            )

        if owns is None:
            # A truncated tail or a bare name cannot identify the file, and no
            # owner proof is available to settle it.  Refusing is the only safe
            # answer: the backup would protect a file nothing writes to.
            return ProjectPathResolution(
                source=EXPLICIT,
                reason=(
                    "无法取得当前 GX Works2 进程对该文件的占用证明；"
                    "仅凭被截断的窗口标题不能确定将被写入的工程。"
                    + _remedy(None)
                ),
            )
        if not owns(named):
            return ProjectPathResolution(
                source=EXPLICIT,
                reason=(
                    "无法确认指定的工程文件正被当前 GX Works2 进程占用；"
                    "同一标题尾部可能对应多个同名工程。"
                    + _remedy(owns)
                ),
            )
        return ProjectPathResolution(path=named, source=EXPLICIT, binding=BINDING_OWNER)

    caption_path = _as_project_file(text)
    if caption_path is not None:
        if not caption_path.is_file():
            return ProjectPathResolution(
                source=CAPTION, reason=f"窗口标题指向的工程文件不存在：{caption_path}"
            )
        return ProjectPathResolution(path=caption_path, source=CAPTION, binding=BINDING_CAPTION)

    label = caption_label(text)
    if not label:
        return ProjectPathResolution(reason="当前窗口没有已保存的工程。")

    abbreviated = text.startswith(ABBREVIATION_PREFIX)
    candidates = tuple(candidates or ())
    if candidates and owns is None:
        # Candidates are only ever matched by a caption suffix, which is exactly
        # what ownership exists to disambiguate.
        return ProjectPathResolution(
            reason=(
                "无法取得当前 GX Works2 进程对候选工程的占用证明；"
                "不能仅凭窗口标题选择工程。" + _remedy(owns)
            )
        )
    matched, mismatched, unowned = [], [], []
    for item in candidates:
        candidate = _as_project_file(item)
        if candidate is None or candidate.name.casefold() != label.casefold():
            continue
        if not candidate.is_file():
            continue
        if abbreviated and not caption_matches_path(text, candidate):
            mismatched.append(candidate)
            continue
        if not owns(candidate):
            unowned.append(candidate)
            continue
        matched.append(candidate)
    matched = _unique(matched)
    if len(matched) == 1:
        return ProjectPathResolution(path=matched[0], source=CANDIDATE, binding=BINDING_OWNER)
    if len(matched) > 1:
        return ProjectPathResolution(
            source=CANDIDATE,
            reason=(
                "找到多个同名工程，无法确定当前窗口对应哪一个。"
                + _remedy(owns)
            ),
            candidates=matched,
        )
    if unowned:
        return ProjectPathResolution(
            source=CANDIDATE,
            reason=(
                "找到同名工程，但没有一个被当前 GX Works2 进程占用。"
                + _remedy(owns)
            ),
            candidates=_unique(unowned),
        )
    if mismatched:
        return ProjectPathResolution(
            source=CANDIDATE,
            reason=(
                "找到同名工程，但其路径与窗口标题显示的目录不一致。"
                + _remedy(owns)
            ),
            candidates=_unique(mismatched),
        )
    if abbreviated:
        return ProjectPathResolution(
            reason=(
                "窗口标题只给出被截断的工程名，未包含完整路径。"
                + _remedy(owns)
            )
        )
    return ProjectPathResolution(
        reason=(
            "窗口标题只给出文件名、没有目录，无法确定是哪一个同名工程。"
            + _remedy(owns)
        )
    )
