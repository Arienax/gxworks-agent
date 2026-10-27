# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Pure framing rules for localized native observations, without UI calls."""
from __future__ import annotations

from .models import GXWFormatError
from .native_locale_text import LANGUAGES, TEXT


def native_language(identity=None):
    # Old recorded observations predate locale metadata and are Chinese only.
    if identity is None or ('native_ui_language' not in identity and 'project_codepage' not in identity):
        return 'zh-CN'
    language = identity.get('native_ui_language')
    codepage = identity.get('project_codepage')
    if language not in LANGUAGES or type(codepage) is not int or codepage not in (1252, 932, 936, 949):
        raise GXWFormatError('Native UI language and project codepage are incomplete or unsupported')
    return language


def ui_text(key, language='zh-CN'):
    if language not in LANGUAGES or key not in TEXT:
        raise GXWFormatError('Unknown native UI language or operational text identity')
    return TEXT[key][LANGUAGES.index(language)]


def native_diagnostic_severity(value, *, language='zh-CN'):
    """Map exact native result resources to the existing Core severity names."""
    canonical = ('Error', 'Warning', 'CheckWarning', 'Information')
    lookup = {ui_text('diagnostic_' + name.lower(), language): name for name in canonical}
    if not isinstance(value, str) or value not in lookup or len(lookup) != len(canonical):
        raise GXWFormatError('Native diagnostic severity is unknown for this UI language')
    return lookup[value]


def project_codepage_status(texts, *, language):
    """Preserve all codepages represented by the actual project-language pane.

    GX Works2 uses the same status resource for CP936 and GB18030 (54936).
    A status observation cannot choose between them or replace a saved value.
    """
    choices = {
        'japanese': (932,), 'english': (1252,),
        'simplified_chinese': (936, 54936), 'korean': (949,), 'traditional_chinese': (950,),
    }
    lookup = {ui_text('project_codepage_status_' + key, language): candidates
              for key, candidates in choices.items()}
    if len(lookup) != len(choices):
        raise GXWFormatError('Native project-language resources have ambiguous identities')
    if (not isinstance(texts, (list, tuple)) or not texts
            or any(not isinstance(text, str) or '\0' in text for text in texts)):
        raise GXWFormatError('Native project-language status text is incomplete')
    known = {ui_text('project_codepage_status_' + key, candidate)
             for key in choices for candidate in LANGUAGES}
    observed = {}
    for text in texts:
        value = text.strip()
        if value in known and value not in lookup:
            raise GXWFormatError('Native project-language status belongs to another UI language')
        if value in lookup:
            observed.setdefault(value, text)
    if len(observed) != 1:
        raise GXWFormatError('Native project-language status is missing or ambiguous')
    value, raw_text = next(iter(observed.items()))
    return dict(status='observed', codepage_candidates=list(lookup[value]), status_text=raw_text)


def rebuild_completion_markers(language=None):
    languages = LANGUAGES if language is None else (language,)
    return tuple(ui_text('compile_completed_format', value) % ui_text('compile_rebuild', value)
                 for value in languages)


def cross_reference_summary_count(summary, search_text, *, language='zh-CN'):
    """Match the native language's exact count and exact qualified search."""
    import re
    if not isinstance(summary, str) or not isinstance(search_text, str):
        return None
    parts = ui_text('xref_summary_format', language).split('%s')
    if len(parts) != 3:
        raise GXWFormatError('Native cross-reference summary format is unverified')
    pattern = (re.escape(parts[0]) + '([0-9]+)' + re.escape(parts[1])
               + re.escape(search_text) + re.escape(parts[2]))
    matched = re.fullmatch(pattern, summary.strip())
    return int(matched[1]) if matched else None


def program_title(owner, kind='program', implementation='fbd', *, language='zh-CN'):
    if kind not in ('program', 'fb') or implementation not in ('st', 'fbd'):
        raise GXWFormatError('Unsupported native document kind')
    return (owner + (' [PRG]' if kind == 'program' else ' [FB]') + ' ' + ui_text('nav_body', language)
            + ' [' + ('ST' if implementation == 'st' else ui_text('language_fbd', language)) + ']')


def label_title(owner, kind, *, language='zh-CN'):
    if kind not in ('global', 'program', 'fb', 'structure'):
        raise GXWFormatError('Unsupported native label document kind')
    suffix = ' [PRG]' if kind == 'program' else ' [FB]' if kind == 'fb' else ''
    return ui_text('title_' + kind, language) + ' ' + owner + suffix


def canonical_label_columns(columns, *, language='zh-CN'):
    keys = ('class', 'name', 'type', 'constant', 'comment', 'device', 'address', 'remark',
            'relation', 'system_name', 'attribute')
    lookup = {ui_text('column_' + key, language): ui_text('column_' + key) for key in keys}
    known = {ui_text('column_' + key, candidate) for key in keys for candidate in LANGUAGES}
    if any(column in known and column not in lookup for column in columns):
        raise GXWFormatError('Native label columns belong to another UI language')
    result = [lookup.get(column, column) for column in columns]
    if len(set(result)) != len(result):
        raise GXWFormatError('Localized native label columns have duplicate semantic identities')
    return result


def strict_project_text(text, codepage):
    if not isinstance(text, str) or type(codepage) is not int or codepage <= 0:
        raise GXWFormatError('Native project text requires an explicit codepage')
    try:
        codec = 'cp' + str(codepage)
        encoded = text.encode(codec, errors='strict')
        if encoded.decode(codec, errors='strict') != text:
            raise GXWFormatError('Text changes after the selected native project codepage roundtrip')
    except (LookupError, UnicodeError) as error:
        raise GXWFormatError('Text is not representable in the selected native project codepage') from error
    return text
