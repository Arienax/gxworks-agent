"""Shared literal text matching for retrieval and structured fact lookup."""
from __future__ import annotations

import re
import unicodedata

from plc.device_identity import REFERENCE_DEVICE_TOKEN_RE

DEVICE_RE = REFERENCE_DEVICE_TOKEN_RE

ERROR_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:0X)?([0-9A-F]{4,7})(H)?(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_ERROR_CONTEXT_RE = re.compile(r"error|fault|alarm|diagnos|错误|故障|报警|异常|诊断", re.I)
_EXPLICIT_ERROR_RE = re.compile(
    r"(?:错误|故障|报警|异常|诊断)\s*(?:代码|码|编号|号)?\s*[:：#]?\s*"
    r"((?:0X)?[0-9A-F]{1,7}H?)(?![A-Za-z0-9_])"
    r"|(?:error|fault|alarm)\s*(?:code|no\.?|number)?\s*[:#]?\s*"
    r"((?:0X)?[0-9A-F]{1,7}H?)(?![A-Za-z0-9_])", re.I)

ASCII_TERM_RE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_]{1,23}"
    r"(?:<>|<=|>=|[<>=])?(?![A-Za-z0-9_<>=])"
)

COMPARISON_FAMILY_RE = re.compile(
    r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_]*)"
    r"((?:<>|<=|>=|[<>=])(?:\s*,\s*(?:<\s*>|<\s*=|>\s*=|[<>=]))+)"
)

PRODUCT_TERM_RE = re.compile(
    r"(?<![A-Za-z0-9_])FX\d[A-Z0-9]*(?:-[A-Z0-9]+)+(?![A-Za-z0-9_])",
    re.IGNORECASE,
)

ASCII_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+.-]*|\d+(?:\.\d+)?")

CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")

EXACT_NOISE = {
    "AI",
    "AND",
    "APP",
    "APP_INSTR",
    "CPU",
    "FALSE",
    "FX",
    "FX3G",
    "FX3U",
    "FX3UC",
    "FX5U",
    "GX",
    "JSON",
    "LADDER",
    "NC",
    "NO",
    "OFF",
    "ON",
    "OR",
    "PDF",
    "PLC",
    "ST",
    "TRUE",
    "WORKS",
    "WORKS2",
}

PLC_DOMAIN_MARKERS = (
    "plc",
    "ladder",
    "timer",
    "counter",
    "pulse",
    "position",
    "servo",
    "inverter",
    "modbus",
    "serial",
    "register",
    "relay",
    "frequency",
    "instruction",
    "i/o",
    "io mapping",
    "input relay",
    "output relay",
    "input filter",
    "input terminal",
    "output terminal",
    "error code",
    "fault code",
    "structured programming",
    "structured project",
    "gx works2",
    "\u7ed3\u6784\u5316\u7f16\u7a0b",
    "\u7ed3\u6784\u5316\u5de5\u7a0b",
    "\u6807\u7b7e",
    "软元件",
    "继电器",
    "寄存器",
    "计数",
    "定时",
    "指令",
    "梯形图",
    "脉冲",
    "定位",
    "原点",
    "高速",
    "频率",
    "模拟量",
    "通信",
    "串行",
    "变频器",
    "输入继电器",
    "输出继电器",
    "输入滤波",
    "输入端",
    "输出端",
    "输出形式",
    "错误代码",
    "故障代码",
    "报警代码",
    "扫描",
    "中断",
    "步进",
    "看门狗",
    "缓冲存储器",
)

NON_MITSUBISHI_MARKERS = (
    "siemens",
    "s7-1200",
    "s7-1500",
    "tia portal",
    "omron",
    "sysmac",
    "arduino",
    "raspberry pi",
    "gpio",
    "\u897f\u95e8\u5b50",
    "\u6b27\u59c6\u9f99",
    "\u6811\u8393\u6d3e",
)

MITSUBISHI_SCOPE_MARKERS = (
    "fx3s",
    "fx3g",
    "fx3gc",
    "fx3u",
    "fx3uc",
    "mitsubishi",
    "gx works2",
    "\u4e09\u83f1",
)


def normalize_text(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.strip().split())


def literal_instruction_word(query, word):
    """Disambiguate the English preposition from an explicitly named FOR opcode."""
    if str(word).upper() != "FOR":
        return True
    normalized = normalize_text(query)
    if normalized.casefold() == "for":
        return True
    if re.search(r"(?<![A-Za-z0-9_])FOR(?![A-Za-z0-9_])", normalized):
        return True
    return bool(re.search(
        r"[`\"']for[`\"']|\bfor\s*(?:/\s*next\b|instruction\b|指令|循环)|"
        r"\bfor\s+(?:K[+-]?\d+|D\d+)(?![A-Za-z0-9_])",
        normalized, re.IGNORECASE,
    ))


def exact_terms(query):
    ordered = []
    seen = set()

    def add(value):
        term = normalize_text(value).upper()
        if not term or term in EXACT_NOISE or term in seen or not literal_instruction_word(query, term):
            return
        seen.add(term)
        ordered.append(term)

    matches = [
        (match.start(), -len(match.group(0)), match.group(0))
        for match in DEVICE_RE.finditer(query)
    ]
    matches.extend(
        (match.start(), -len(match.group(0)), match.group(0))
        for match in PRODUCT_TERM_RE.finditer(query)
    )
    matches.extend(
        (match.start(), -len(match.group(0)), match.group(0))
        for match in ASCII_TERM_RE.finditer(query)
    )
    for _position, _negative_length, value in sorted(matches):
        add(value)
    return ordered[:32]


def entity_term_variants(term):
    """Return display-equivalent X/Y addresses used across Mitsubishi manuals."""

    normalized = normalize_text(term).upper()
    matched = re.fullmatch(r"([XY])0*(\d+)", normalized)
    if not matched:
        return [normalized]
    prefix, digits = matched.groups()
    number = int(digits or "0")
    variants = [normalized, f"{prefix}{number}", f"{prefix}{number:02d}", f"{prefix}{number:03d}"]
    return list(dict.fromkeys(variants))


def canonical_error_code(value):
    return str(value or "").strip().upper().removeprefix("0X").removesuffix("H")


def error_code_aliases(value):
    code = canonical_error_code(value)
    return (code, code + "H", "0X" + code) if code else ()


def explicit_error_terms(query):
    return tuple(dict.fromkeys(canonical_error_code(match[1] or match[2])
                              for match in _EXPLICIT_ERROR_RE.finditer(normalize_text(query))))


def diagnostic_reference_text(query):
    """Mask only code occurrences explicitly bound to the error namespace."""
    text = normalize_text(query)
    spans = [match.span(1 if match[1] else 2) for match in _EXPLICIT_ERROR_RE.finditer(text)]
    for start, end in reversed(spans):
        text = text[:start] + ' ' * (end - start) + text[end:]
    return text


def error_terms(query, *, plc_model=None):
    """Diagnostic identities, with lookup aliases kept out of coverage targets.

    Device recognition is lexical reference recognition, independent of Core's
    execution support for that CPU. An explicit error namespace takes priority.
    """
    normalized = normalize_text(query)
    has_error_context = bool(_ERROR_CONTEXT_RE.search(normalized))
    values = list(explicit_error_terms(normalized))
    for match in ERROR_CODE_RE.finditer(normalized):
        code = canonical_error_code(match[0])
        if REFERENCE_DEVICE_TOKEN_RE.fullmatch(match[0]) and code not in values:
            continue
        has_hex_letter = bool(re.search(r"[A-F]", code))
        if not (has_error_context or match.group(2) or has_hex_letter):
            continue
        values.append(code)
    return list(dict.fromkeys(values))[:12]


def fts_tokens(query):
    tokens = []
    seen = set()

    def add(value):
        token = normalize_text(value).casefold().replace('"', "")
        if not token or token in seen:
            return
        seen.add(token)
        tokens.append(token)

    for term in exact_terms(query):
        add(term)
    for match in ASCII_WORD_RE.finditer(query):
        value = match.group(0)
        if value.upper() not in EXACT_NOISE and literal_instruction_word(query, value):
            add(value)
    for match in CJK_RUN_RE.finditer(query):
        run = match.group(0)
        if len(run) <= 12:
            add(run)
        if len(run) == 1:
            add(run)
        else:
            for index in range(len(run) - 1):
                add(run[index : index + 2])
    return tokens[:48]


def cjk_bigram_set(value):
    terms = set()
    for match in CJK_RUN_RE.finditer(normalize_text(value)):
        run = match.group(0)
        terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return terms


def fts_expression(query):
    tokens = fts_tokens(query)
    if not tokens:
        return ""
    return " OR ".join('"{}"'.format(token.replace('"', '""')) for token in tokens)


def fts_match_quality(query, result, bm25_score=0.0):
    """Return lexical coverage for a candidate, or zero when it is too weak.

    CJK bigram OR queries deliberately favor recall, but without a coverage
    gate generic requests such as "modify this program" match arbitrary pages
    containing only one common word.  Exact entity hits bypass this function;
    FTS-only evidence must cover a meaningful share of the actual query.
    """

    tokens = fts_tokens(query)
    if not tokens:
        return 0.0, []
    haystack = normalize_text(
        " ".join(
            str(result.get(key, "") or "")
            for key in ("section", "text")
        )
    ).casefold()
    matched = [token for token in tokens if token.casefold() in haystack]
    coverage = len(matched) / len(tokens)
    product_hits = [
        term
        for term in exact_terms(query)
        if PRODUCT_TERM_RE.fullmatch(term) and term.casefold() in haystack
    ]
    # A full Mitsubishi module identifier is already an unambiguous lexical
    # match.  Do not reject its English manual page merely because the rest of
    # the user query is Chinese and therefore cannot overlap that page.
    if product_hits:
        return max(coverage, 0.55), matched
    normalized_query = normalize_text(query).casefold()
    has_domain_marker = any(
        marker.casefold() in normalized_query for marker in PLC_DOMAIN_MARKERS
    )
    if len(tokens) <= 3:
        relevant = bool(matched) and coverage >= (1 / 3)
    else:
        relevant = len(matched) >= 2 and coverage >= 0.30
    # BM25 measures textual similarity, not PLC relevance.  Generic requests
    # such as "modify program" can receive a very strong score on an error-code
    # page simply because common bigrams repeat there.  FTS-only evidence must
    # therefore contain an unambiguous PLC-domain marker; exact opcodes/devices
    # are handled separately by the entity index and do not need this fallback.
    relevant = relevant and has_domain_marker
    return (coverage if relevant else 0.0), matched


def alias_occurs(query, alias):
    normalized_query = normalize_text(query).casefold()
    normalized_alias = normalize_text(alias).casefold()
    if not normalized_alias:
        return False
    if re.fullmatch(r"[a-z][a-z0-9_]*(?:<>|<=|>=|[<>=]|\s+[0-9]+)?", normalized_alias):
        return bool(
            re.search(
                rf"(?<![a-z0-9_]){re.escape(normalized_alias)}(?![a-z0-9_<>=])",
                normalized_query,
                flags=re.IGNORECASE,
            )
        )
    return len(normalized_alias) >= 2 and normalized_alias in normalized_query


def instruction_heading_terms(section):
    terms = set(exact_terms(section))
    for match in COMPARISON_FAMILY_RE.finditer(section):
        prefix, operators = match.groups()
        terms.update(
            prefix.upper() + re.sub(r"\s+", "", operator)
            for operator in operators.split(",")
        )
    return terms
