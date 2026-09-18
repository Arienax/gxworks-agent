"""Provider-neutral acceptance rules for *declared* model-authored prose.

This is a conservative script check, not a universal language classifier. It
rejects observable mismatches (and ambiguous Han-only Japanese), never rewrites
content, and deliberately leaves protocol fields and source evidence alone.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Tuple

from shared.i18n import normalize_language


@dataclass(frozen=True)
class ResponseContract:
    name: str = "text"
    format: str = "text"
    human_paths: Tuple[str, ...] = ()
    st_paths: Tuple[str, ...] = ()
    # Only annotations explicitly copied from a known input may retain their
    # original language. A summary is never exempt merely because it was seen.
    annotation_paths: Tuple[str, ...] = ()
    # Legacy unions such as online_checks accept either a string or an object.
    # Object fields must have their own selectors; arbitrary objects in a prose
    # slot cannot silently pass and later be stringified by a domain normalizer.
    structured_paths: Tuple[str, ...] = ()

    def __post_init__(self):
        if self.format not in {"text", "json"}:
            raise ValueError("Unsupported response contract format")
        for name in ("human_paths", "st_paths", "annotation_paths", "structured_paths"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


TEXT_RESPONSE = ResponseContract()


@dataclass(frozen=True)
class LanguageViolation:
    path: str
    reason: str


_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]")
_KANA = re.compile(r"[\u3041-\u3096\u309d-\u309f\u30a1-\u30fa\u30fd-\u30ff\uff66-\uff6f\uff71-\uff9d]")
_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
# Tokens are removed only from the inspection view; the returned bytes never
# change. Punctuation such as ':' or '{' is not an exemption for a whole string.
_TOKEN = re.compile(
    r"https?://[^\s<>]+|(?:[A-Za-z]:[\\/]|\\\\)[^\s\"<>]+"
    r"|(?<![A-Za-z0-9_])(?:[A-Za-z0-9]+[_./][A-Za-z0-9_./:-]+"
    r"|[A-Za-z]+[0-9][A-Za-z0-9]*|[A-Z][A-Z0-9_$]*)(?![A-Za-z0-9_])"
)
_QUOTED = re.compile(r"`([^`\n]+)`|\"([^\"\n]+)\"|“([^”\n]+)”|「([^」\n]+)」")
_FENCE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)


def st_comments(code: str) -> Iterator[str]:
    """Read IEC ST comments without treating quoted literals as comments."""
    i = 0
    while i < len(code):
        if code[i] in "\"'":
            quote = code[i]
            i += 1
            while i < len(code):
                if code[i] == "$":  # IEC escaped quote / control character
                    i += 2
                elif code[i] == quote:
                    if i + 1 < len(code) and code[i + 1] == quote:
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    i += 1
        elif code.startswith("//", i):
            end = code.find("\n", i)
            end = len(code) if end < 0 else end
            yield code[i + 2:end]
            i = end
        elif code.startswith("(*", i):
            i += 2
            start, depth = i, 1
            while i < len(code) and depth:
                if code.startswith("(*", i):
                    depth += 1
                    i += 2
                elif code.startswith("*)", i):
                    depth -= 1
                    if not depth:
                        yield code[start:i]
                    i += 2
                else:
                    i += 1
            if depth:
                yield code[start:]  # Syntax validation is still the compiler's job.
        else:
            i += 1


def selected_fields(value: Any, selector: str):
    """Select values, never keys. '*' matches one level; '**' descends."""
    def children(item):
        if isinstance(item, Mapping):
            return item.items()
        if isinstance(item, (list, tuple)):
            return enumerate(item)
        return ()

    def visit(item, parts, path):
        if not parts:
            yield path, item
        elif parts[0] == "**":
            yield from visit(item, parts[1:], path)
            for key, child in children(item):
                yield from visit(child, parts, f"{path}.{key}")
        else:
            for key, child in children(item):
                if parts[0] in {"*", str(key)}:
                    yield from visit(child, parts[1:], f"{path}.{key}")

    yield from visit(value, selector.split(".") if selector else (), "$")


def preserved_annotations(*sources: Any) -> Tuple[str, ...]:
    """Snapshot existing annotations, not arbitrary source strings or prose."""
    result = set()

    def visit(value):
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key in {"label", "debug_note", "comment"} and isinstance(item, str):
                    result.add(item)
                elif key == "device_comments" and isinstance(item, Mapping):
                    result.update(v for v in item.values() if isinstance(v, str))
                elif key == "st_code" and isinstance(item, str):
                    result.update(comment.strip() for comment in st_comments(item))
                visit(item)
        elif isinstance(value, (tuple, list)):
            for item in value:
                visit(item)

    for source in sources:
        visit(source)
    return tuple(sorted(result))


def prose_violation(text: str, language: str, source_texts=()) -> str | None:
    """Reject definite script mismatches; report ambiguity instead of guessing.

    Latin-script languages cannot be distinguished by this check. Technical
    acronyms, short mixed prose and Chinese/Japanese shared Han are limitations,
    explicitly documented rather than advertised as semantic guarantees.
    """
    def quotation(match):
        quoted = next(group for group in match.groups() if group is not None)
        return " " if any(quoted in source for source in source_texts) else quoted

    def fence(match):
        dialect, body = match.groups()
        if any(body.strip() in source for source in source_texts):
            return " "
        if dialect.strip().lower() in {"st", "iecst", "structured-text"}:
            return "\n".join(st_comments(body))
        return body  # Arbitrary code fences do not hide wrong-language prose.

    text = _QUOTED.sub(quotation, _FENCE.sub(fence, text))
    text = _TOKEN.sub(" ", text)
    language = normalize_language(language)
    for segment in re.split(r"[\n.!?。！？;；]+", text):
        han, kana = bool(_HAN.search(segment)), bool(_KANA.search(segment))
        words = _WORD.findall(segment)
        foreign_script = any(
            char.isalpha() and not ("a" <= char.lower() <= "z")
            and not _HAN.fullmatch(char) and not _KANA.fullmatch(char)
            and char not in "ーｰ"
            for char in segment
        )
        if foreign_script:
            return "unsupported_script"
        if language == "en" and (han or kana):
            return "non_english_script"
        if language == "zh-CN":
            if kana:
                return "japanese_script"
            if words and (not han or len(words) >= 3):
                return "latin_prose"
        if language == "ja":
            if han and not kana:
                return "ambiguous_han_only"
            if words and (not (han or kana) or len(words) >= 3):
                return "latin_prose"
    return None


def inspect_response(
    content: str,
    language: str,
    contract: ResponseContract = TEXT_RESPONSE,
    *,
    source_texts=(),
    annotations=(),
    path_prefix="content",
) -> Tuple[LanguageViolation, ...]:
    violations = []
    structured_paths = set()

    def check(path, value, may_preserve=False):
        if isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                check(f"{path}.{index}", item, may_preserve)
            return
        if value is None:
            return
        if isinstance(value, (int, float, bool)):
            return  # Numbers/booleans remain protocol values even in legacy slots.
        if not isinstance(value, str):
            if path not in structured_paths:
                violations.append(LanguageViolation(f"{path_prefix}{path}", "invalid_prose_field"))
            return
        if may_preserve and value.strip() in annotations:
            return
        reason = prose_violation(value, language, source_texts)
        if reason:
            violations.append(LanguageViolation(f"{path_prefix}{path}", reason))

    if contract.format == "text":
        check("", content)
    else:
        raw = content.strip()
        if raw.startswith("```") and raw.endswith("```") and "\n" in raw:
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object")
        except (ValueError, TypeError, RecursionError):
            return (LanguageViolation(path_prefix, "invalid_json_object"),)
        annotation_paths = {
            path for selector in contract.annotation_paths
            for path, _ in selected_fields(payload, selector)
        }
        structured_paths = {
            path for selector in contract.structured_paths
            for path, _ in selected_fields(payload, selector)
        }
        seen = set()
        for selector in contract.human_paths:
            for path, value in selected_fields(payload, selector):
                if path not in seen:
                    check(path, value, path in annotation_paths)
                    seen.add(path)
        for selector in contract.st_paths:
            for path, value in selected_fields(payload, selector):
                if isinstance(value, str):
                    for index, comment in enumerate(st_comments(value)):
                        check(f"{path}.comment[{index}]", comment, True)
                else:
                    violations.append(LanguageViolation(f"{path_prefix}{path}", "invalid_code_field"))
    return tuple(violations)
