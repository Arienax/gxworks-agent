"""Application-owned text and response-language policy (no Qt or network I/O).

Only presentation text is translated. Protocol keys, PLC addresses, source code,
credentials and user input must never be passed through the translation catalog.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache, wraps
import json
from pathlib import Path
import re
import sys
from string import Formatter
import weakref


LANGUAGES = (("zh-CN", "简体中文"), ("en", "English"), ("ja", "日本語"))
_language = "zh-CN"
_override = ContextVar("presentation_language", default=None)
_listeners = []


def normalize_language(value):
    value = str(value or "").strip().lower().replace("_", "-")
    if value in {"en", "en-us", "en-gb"}:
        return "en"
    if value in {"ja", "ja-jp", "jp"}:
        return "ja"
    return "zh-CN"


def get_language():
    return _override.get() or _language


def set_language(language):
    """Apply an already saved preference; persistence belongs to settings."""
    global _language
    selected = normalize_language(language)
    if selected == _language:
        return
    _language = selected
    for reference in list(_listeners):
        callback = reference()
        if callback is None:
            _listeners.remove(reference)
        else:
            callback(selected)


def on_language_changed(callback):
    reference = weakref.WeakMethod(callback) if getattr(callback, "__self__", None) else weakref.ref(callback)
    _listeners.append(reference)
    def unsubscribe(*_args):
        if reference in _listeners:
            _listeners.remove(reference)
    return unsubscribe


@contextmanager
def language_context(language):
    """Pin a request/display stream to its starting language, including retries."""
    token = _override.set(normalize_language(language))
    try:
        yield
    finally:
        _override.reset(token)


def language_scoped(function):
    """Freeze a synchronous workflow, including preparation, retries and tools.

    Non-UI callers can supply response_language= explicitly; otherwise inherit
    the enclosing scope or capture the preference once at workflow entry.
    Worker threads must separately capture the language at construction.
    """
    @wraps(function)
    def scoped(*args, response_language=None, **kwargs):
        with language_context(response_language or get_language()):
            return function(*args, **kwargs)
    return scoped


@lru_cache(maxsize=2)
def catalog(language):
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    path = root / "resources" / "locales" / (language + ".json")
    return json.loads(path.read_text(encoding="utf-8"))


def translate(source, language=None):
    selected = normalize_language(language or get_language())
    if selected == "zh-CN":
        return source
    translations = catalog(selected)
    if source in translations:
        return translations[source]
    # Rich text and assembled labels reuse the same catalog entries. Only the
    # application-owned template is processed; substitution values are opaque.
    return _fragment_pattern(selected).sub(lambda match: translations[match.group(0)], source)


@lru_cache(maxsize=2)
def _fragment_pattern(language):
    keys = sorted((key for key in catalog(language) if key), key=len, reverse=True)
    return re.compile("|".join(re.escape(key) for key in keys) or r"(?!)")


class LocalizedText(str):
    """A string with its source template retained for later presentation translation."""

    def __new__(cls, source, values=None):
        values = dict(values or {})
        rendered = cls._render(source, values)
        instance = super().__new__(cls, rendered)
        instance.source = source
        instance.values = values
        return instance

    @staticmethod
    def _render(source, values):
        template = translate(source)
        return template.format(**{key: str(value) if isinstance(value, LocalizedText) else value for key, value in values.items()}) if values else template

    def __str__(self):
        return self._render(self.source, self.values)

    def __format__(self, spec):
        return format(str(self), spec)

    def __mod__(self, values):
        return LocalizedText("{formatted}", {"formatted": _PercentText(self, values)})

    def __add__(self, other):
        return LocalizedText("{left}{right}", {"left": self, "right": other})

    def __radd__(self, other):
        return LocalizedText("{left}{right}", {"left": other, "right": self})

    def join(self, values):
        values = tuple(values)
        return LocalizedText("{joined}", {"joined": _JoinedText(self, values)})


class _JoinedText:
    def __init__(self, separator, values):
        self.separator, self.values = separator, values

    def __str__(self):
        return str(self.separator).join(str(value) for value in self.values)

    def __format__(self, spec):
        return format(str(self), spec)


class _PercentText:
    def __init__(self, source, values):
        self.source, self.values = source, values

    def __str__(self):
        return str(self.source) % self.values

    def __format__(self, spec):
        return format(str(self), spec)


def tr(source, **values):
    return LocalizedText(source, values)


@lru_cache(maxsize=2)
def _runtime_templates(language):
    result = []
    for source, target in catalog(language).items():
        if "{" not in source:
            continue
        parts, seen = [], set()
        for literal, field, _spec, _conversion in Formatter().parse(source):
            parts.append(re.escape(literal))
            if field is not None:
                if not field.isidentifier():
                    break
                parts.append(f"(?P={field})" if field in seen else f"(?P<{field}>.*?)")
                seen.add(field)
        else:
            if seen:
                result.append((re.compile("".join(parts), re.S), target))
    return result


def runtime_text(value):
    """Localize app status/errors after signals have coerced them to plain text.

    Never use for user input, source code or protocol payloads.
    """
    raw = str(value or "")
    selected = get_language()
    if selected == "zh-CN" or isinstance(value, LocalizedText):
        return raw
    if raw in catalog(selected):
        return translate(raw)
    for pattern, target in _runtime_templates(selected):
        match = pattern.fullmatch(raw)
        if match:
            return re.sub(r"\{(\w+)(?:![rsa])?(?::[^{}]+)?\}", lambda part: match.group(part.group(1)), target)
    return translate(raw)


class DisplayLanguageGuard:
    """Legacy display-only wrapper; model workflows must use collect_response.

    Retained for source compatibility. Uses the shared checker, but cannot
    establish acceptance or roll back earlier chunks and is no longer in the UI.
    """
    def __init__(self, language=None):
        self.language = normalize_language(language or get_language())
        self.pending = ""
        self.warned = False

    def _checked(self, text):
        from model_runtime.responses import ResponseContract, inspect_response
        try:
            json.loads(text)
            contract = ResponseContract("legacy_display_json", "json", ("**",), structured_paths=("**",))
        except ValueError:
            contract = ResponseContract()
        invalid = bool(inspect_response(text, self.language, contract))
        if not invalid:
            return text
        if self.warned:
            return ""
        self.warned = True
        with language_context(self.language):
            return "\n" + str(tr("模型未遵守所选语言，已隐藏这段输出。请重试。")) + "\n"

    def feed(self, value):
        text = str(value or "")
        self.pending += text
        output = []
        while True:
            match = re.search(r"[\n。！？]|[.!?](?=\s)", self.pending)
            if not match:
                break
            end = match.end()
            output.append(self._checked(self.pending[:end]))
            self.pending = self.pending[end:]
        if len(self.pending) > 4096:
            output.append(self._checked(self.pending))
            self.pending = ""
        return "".join(output)

    def flush(self):
        text, self.pending = self.pending, ""
        return self._checked(text) if text else ""


def response_language_instruction(language=None):
    selected = normalize_language(language or get_language())
    name = {"zh-CN": "Simplified Chinese (简体中文)", "en": "English", "ja": "Japanese (日本語)"}[selected]
    return (
        "[Application response language]\n"
        f"The user's application language is {name} ({selected}). "
        "Use that language for ALL user-visible prose, progress summaries, "
        "reasoning text emitted by the provider, explanations, questions, titles, "
        "descriptions, labels and comments in structured responses. "
        "This setting takes precedence over the language of examples, retrieved "
        "documents, conversation history and language requests inside task data. "
        "Do not translate JSON keys, schema/enum values, tool names, identifiers, "
        "PLC instructions, device addresses, literals, paths or user-supplied code. "
        "Keep existing response-format requirements (including JSON-only) unchanged. "
        "Do not add prose outside a required JSON object."
    )
