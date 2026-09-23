"""Low-latency broad retrieval for the bundled PLC knowledge index.

Explicit PLC facts are resolved by :mod:`knowledge.structured_facts`. Broad
retrieval applies metadata scope before candidate limits, then combines exact
entity, lexical and dense ranks with reciprocal-rank fusion. No PLC topic gets
its own ranking boost.

The module does not touch SQLite or import the optional dense runtime until the
first retrieval call. A connection and its schema snapshot are kept per calling
thread so concurrent workers never share SQLite objects.

Expected index tables are ``meta``, ``chunks``, ``entity_index`` and
``chunks_fts``. Column names are discovered at runtime to keep the reader
compatible with small schema revisions of the prebuilt index.
"""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import re
import sqlite3
import threading
import unicodedata
from urllib.parse import quote

from shared.paths import resource_path
from knowledge.gxworks2_concepts import query_skill_concepts
from plc.device_identity import DEVICE_TOKEN_RE


_INDEX_RESOURCE = "knowledge/fx3u_knowledge.sqlite"
_CACHE_SIZE = 256
_MAX_TOP_K = 50
_MAX_CANDIDATES = 200
_MAX_ENTITY_ROWS_PER_TERM = 64
_RRF_K = 60.0

_thread_state = threading.local()

_DEVICE_RE = DEVICE_TOKEN_RE
_ERROR_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:0X)?([0-9A-F]{4,5})(H)?(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_ASCII_TERM_RE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_]{1,23}"
    r"(?:<>|<=|>=|[<>=])?(?![A-Za-z0-9_<>=])"
)
# Official manuals abbreviate comparison families in headings, for example
# "AND=, >, <, < >, <=, >=". Expand the heading, never the source text.
_COMPARISON_FAMILY_RE = re.compile(
    r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_]*)"
    r"((?:<>|<=|>=|[<>=])(?:\s*,\s*(?:<\s*>|<\s*=|>\s*=|[<>=]))+)"
)
_OFFICIAL_INSTRUCTION_MANUAL_TYPES = frozenset(
    {"programming", "positioning", "structured_instruction", "structured_function"}
)
_PRODUCT_TERM_RE = re.compile(
    r"(?<![A-Za-z0-9_])FX\d[A-Z0-9]*(?:-[A-Z0-9]+)+(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_ASCII_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+.-]*|\d+(?:\.\d+)?")
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_EXACT_NOISE = {
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
_PLC_DOMAIN_MARKERS = (
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

_NON_MITSUBISHI_MARKERS = (
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
_MITSUBISHI_SCOPE_MARKERS = (
    "fx3s",
    "fx3g",
    "fx3gc",
    "fx3u",
    "fx3uc",
    "mitsubishi",
    "gx works2",
    "\u4e09\u83f1",
)

_CHUNK_ID_COLUMNS = ("chunk_id", "chunk_key", "id")
_TEXT_COLUMNS = ("text", "content", "content_text", "chunk_text", "body")
_SOURCE_COLUMNS = (
    "source",
    "source_name",
    "manual_title",
    "manual_id",
    "document",
    "document_title",
    "document_id",
    "title",
)
_PAGE_COLUMNS = (
    "printed_page",
    "page",
    "page_number",
    "manual_page",
    "page_start",
    "start_page",
    "source_page",
)
_PAGE_END_COLUMNS = ("page_end", "end_page")
_PDF_PAGE_COLUMNS = ("pdf_page", "pdf_page_number", "page_pdf")
_SECTION_COLUMNS = (
    "outline_path",
    "section_path",
    "section",
    "section_title",
    "heading",
    "chapter",
)
_MODEL_COLUMNS = (
    "plc_model",
    "plc_models",
    "model",
    "plc_family",
    "applies_to",
)
_TASK_COLUMNS = (
    "task_type",
    "task_types",
    "tasks",
    "workflow",
    "applies_to_task",
)
_ENTITY_TERM_COLUMNS = (
    "entity_norm",
    "normalized_entity",
    "normalized_alias",
    "normalized",
    "entity",
    "entity_value",
    "canonical",
    "canonical_key",
    "term",
    "alias",
    "alias_norm",
    "opcode",
    "device",
    "name",
    "key",
)
_ENTITY_CHUNK_COLUMNS = ("chunk_id", "target_chunk_id", "content_id")
_ENTITY_OCCURRENCE_COLUMNS = (
    "occurrences",
    "occurrence_count",
    "frequency",
    "count",
    "weight",
)


def _normalize_text(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.strip().split())


def _query_is_out_of_scope(query, plc_model):
    model = _normalize_text(plc_model).upper()
    if model and model not in {"FX3S", "FX3G", "FX3GC", "FX3U", "FX3UC"}:
        return True
    normalized = _normalize_text(query).casefold()
    foreign = any(marker.casefold() in normalized for marker in _NON_MITSUBISHI_MARKERS)
    in_scope = any(marker.casefold() in normalized for marker in _MITSUBISHI_SCOPE_MARKERS)
    return foreign and not in_scope


def _quote_identifier(value):
    return '"' + str(value).replace('"', '""') + '"'


def _first_column(columns, candidates):
    lookup = {str(column).casefold(): str(column) for column in columns}
    for candidate in candidates:
        matched = lookup.get(candidate.casefold())
        if matched:
            return matched
    return None


def _matching_columns(columns, candidates):
    lookup = {str(column).casefold(): str(column) for column in columns}
    return [lookup[item.casefold()] for item in candidates if item.casefold() in lookup]


def _row_value(row, columns, default=None):
    keys = {str(key).casefold(): key for key in row.keys()}
    for column in columns:
        key = keys.get(column.casefold())
        if key is None:
            continue
        value = row[key]
        if value is not None and str(value).strip():
            return value
    return default


def _index_path():
    try:
        path = Path(resource_path(_INDEX_RESOURCE))
    except (OSError, TypeError, ValueError):
        return None
    return path if path.is_file() else None


def _index_identity(path):
    if path is None:
        return ("missing", 0, 0)
    try:
        stat = path.stat()
    except OSError:
        return ("missing", 0, 0)
    return (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))


def _close_thread_connection():
    connection = getattr(_thread_state, "connection", None)
    if connection is not None:
        try:
            connection.close()
        except sqlite3.Error:
            pass
    _thread_state.connection = None
    _thread_state.identity = None
    _thread_state.schema = None
    _thread_state.dense_verification = None
    _thread_state.instruction_sections = None


def _connection(path, identity):
    if (
        getattr(_thread_state, "connection", None) is not None
        and getattr(_thread_state, "identity", None) == identity
    ):
        return _thread_state.connection

    _close_thread_connection()
    encoded_path = quote(path.resolve().as_posix(), safe="/:")
    connection = sqlite3.connect(
        "file:{}?mode=ro&immutable=1".format(encoded_path),
        uri=True,
        timeout=0.2,
        check_same_thread=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA temp_store=MEMORY")
    try:
        connection.execute("PRAGMA mmap_size=268435456")
    except sqlite3.Error:
        pass

    _thread_state.connection = connection
    _thread_state.identity = identity
    _thread_state.schema = None
    _thread_state.dense_verification = None
    return connection


def _table_columns(connection, table_name):
    rows = connection.execute(
        "PRAGMA table_info({})".format(_quote_identifier(table_name))
    ).fetchall()
    return tuple(str(row[1]) for row in rows)


def _schema(connection):
    cached = getattr(_thread_state, "schema", None)
    if cached is not None:
        return cached

    table_rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
    ).fetchall()
    table_lookup = {str(row[0]).casefold(): str(row[0]) for row in table_rows}
    schema = {}
    for expected in (
        "meta",
        "manuals",
        "chunks",
        "entity_index",
        "chunks_fts",
        "instructions",
        "instruction_aliases",
        "device_records",
        "error_records",
        "debug_cases",
        "vector_embeddings",
    ):
        actual = table_lookup.get(expected.casefold())
        if actual:
            schema[expected] = {
                "name": actual,
                "columns": _table_columns(connection, actual),
            }
    _thread_state.schema = schema
    return schema


def _load_meta(connection, schema):
    table = schema.get("meta")
    if not table:
        return {}
    columns = table["columns"]
    if not columns:
        return {}
    rows = connection.execute(
        "SELECT * FROM {} LIMIT 128".format(_quote_identifier(table["name"]))
    ).fetchall()
    if not rows:
        return {}

    key_column = _first_column(columns, ("key", "name", "meta_key"))
    value_column = _first_column(columns, ("value", "meta_value", "content"))
    if key_column and value_column:
        return {
            str(row[key_column]).strip().casefold(): row[value_column]
            for row in rows
            if row[key_column] is not None
        }
    return {str(key).casefold(): rows[0][key] for key in rows[0].keys()}


def _scope_values(value):
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip().upper() for item in value if str(item).strip()}
    text = str(value).strip()
    if not text:
        return set()
    if text[:1] in "[{":
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, (list, tuple, set)):
            return {str(item).strip().upper() for item in parsed if str(item).strip()}
    return {
        item.upper()
        for item in re.split(r"[,;|/\s]+", text)
        if item.strip()
    }


def _scope_matches(value, requested):
    values = _scope_values(value)
    if not values or values.intersection({"*", "ALL", "ANY", "COMMON"}):
        return True
    requested = str(requested or "").strip().upper()
    return not requested or requested in values


def _row_in_scope(row, plc_model, task_type):
    model = _row_value(row, _MODEL_COLUMNS)
    task = _row_value(row, _TASK_COLUMNS)
    return _scope_matches(model, plc_model) and _scope_matches(task, task_type)


def _literal_instruction_word(query, word):
    """Disambiguate the English preposition from an explicitly named FOR opcode."""
    if str(word).upper() != "FOR":
        return True
    normalized = _normalize_text(query)
    if normalized.casefold() == "for":
        return True
    if re.search(r"(?<![A-Za-z0-9_])FOR(?![A-Za-z0-9_])", normalized):
        return True
    return bool(re.search(
        r"[`\"']for[`\"']|\bfor\s*(?:/\s*next\b|instruction\b|指令|循环)|"
        r"\bfor\s+(?:K[+-]?\d+|D\d+)(?![A-Za-z0-9_])",
        normalized, re.IGNORECASE,
    ))


def _exact_terms(query):
    ordered = []
    seen = set()

    def add(value):
        term = _normalize_text(value).upper()
        if not term or term in _EXACT_NOISE or term in seen or not _literal_instruction_word(query, term):
            return
        seen.add(term)
        ordered.append(term)

    matches = [
        (match.start(), -len(match.group(0)), match.group(0))
        for match in _DEVICE_RE.finditer(query)
    ]
    matches.extend(
        (match.start(), -len(match.group(0)), match.group(0))
        for match in _PRODUCT_TERM_RE.finditer(query)
    )
    matches.extend(
        (match.start(), -len(match.group(0)), match.group(0))
        for match in _ASCII_TERM_RE.finditer(query)
    )
    for _position, _negative_length, value in sorted(matches):
        add(value)
    return ordered[:32]


def _entity_term_variants(term):
    """Return display-equivalent X/Y addresses used across Mitsubishi manuals."""

    normalized = _normalize_text(term).upper()
    matched = re.fullmatch(r"([XY])0*(\d+)", normalized)
    if not matched:
        return [normalized]
    prefix, digits = matched.groups()
    number = int(digits or "0")
    variants = [normalized, f"{prefix}{number}", f"{prefix}{number:02d}", f"{prefix}{number:03d}"]
    return list(dict.fromkeys(variants))


def _query_is_positioning(query):
    """Recognize motion queries without confusing a step state machine with a stepper."""

    normalized = _normalize_text(query)
    return bool(
        re.search(
            r"(?<![A-Za-z0-9_])(?:DRVI|DRVA|ZRN|DSZR|DVIT|PLSY|DPLSY|PLSV)"
            r"(?![A-Za-z0-9_])|"
            r"(?<![A-Za-z0-9_])(?:M8029|M8336|D834[0-9]|D835[0-9])"
            r"(?![A-Za-z0-9_])|"
            r"FX\d[A-Z0-9]*(?:-[A-Z0-9]+)+|"
            r"position(?:ing)?|pulse output|zero return|home return|servo|stepper|"
            r"\u5b9a\u4f4d|\u8109\u51b2|\u539f\u70b9|\u56de\u96f6|"
            r"\u4f3a\u670d|\u6b65\u8fdb\u7535\u673a|\u6b65\u8fdb\u9a71\u52a8\u5668",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def _query_is_direction_output_assignment(query):
    """Recognize pulse/direction wiring assignment questions in either language."""

    normalized = _normalize_text(query)
    direction = re.search(
        r"方向(?:输出|信号)|direction\s+(?:signal|output)", normalized, re.IGNORECASE,
    )
    assignment = re.search(
        r"固定|配对|映射|任意|指定|分配|可以用|assign|arbitrary|fixed|pair|any\s+output|must",
        normalized, re.IGNORECASE,
    )
    motion = _query_is_positioning(normalized) or re.search(
        r"(?<![A-Za-z0-9_])Y\d+(?![A-Za-z0-9_])", normalized, re.IGNORECASE,
    )
    return bool(direction and assignment and motion)


def _query_is_clock_semantics(query):
    normalized = _normalize_text(query)
    return bool(
        re.search(
            r"(?<![A-Za-z0-9_])M801[1-4](?![A-Za-z0-9_])|"
            r"时钟|闪烁|振荡|方波|\bclock\b|\bblink\w*\b|\bflash\w*\b|"
            r"\boscillat\w*\b|square\s*wave",
            normalized,
            re.IGNORECASE,
        )
    )


def _query_is_timer_preset(query):
    """Scope device-range evidence to timer settings, units, and time-base questions."""
    normalized = _normalize_text(query)
    timer = re.search(r"\btimers?\b|\bT\d+\b|定时器|计时器|时基", normalized, re.IGNORECASE)
    setting = re.search(
        r"\bK\d*\b|\bpreset\b|\btime\s*base\b|\bresolution\b|\bunits?\b|"
        r"\btimer\s+(?:numbers?|ranges?)\b|时基|时间基准|预置值|设定值|定时器编号",
        normalized, re.IGNORECASE,
    )
    return bool(timer and setting and not _query_is_clock_semantics(normalized))


def _timer_range_evidence(text, plc_model):
    """Require model, timer range and time units in the original evidence body."""
    body = re.split(r"\n\[(?:PAGE|TABLE)\b", str(text or ""), maxsplit=1)[-1]
    model = _normalize_text(plc_model)
    if not model:
        return False
    model_pattern = r"\s*".join(re.escape(character) for character in model)
    return bool(
        re.search(r"(?<![A-Za-z0-9])" + model_pattern + r"(?![A-Za-z0-9])", body, re.IGNORECASE)
        and re.search(r"\bT\d+\s*(?:to|[-–～]|至)\s*T\d+\b", body, re.IGNORECASE)
        and re.search(r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds?)\b", body, re.IGNORECASE)
    )


def _error_terms(query):
    normalized = _normalize_text(query)
    has_error_context = bool(
        re.search(
            r"error|fault|alarm|diagnos|错误|故障|报警|异常|诊断",
            normalized,
            flags=re.IGNORECASE,
        )
    )
    values = []
    seen = set()
    for match in _ERROR_CODE_RE.finditer(normalized):
        code = match.group(1).upper() + ("H" if match.group(2) else "")
        has_hex_letter = bool(re.search(r"[A-F]", match.group(1), flags=re.I))
        if not (has_error_context or match.group(2) or has_hex_letter):
            continue
        for value in (code, code[:-1] if code.endswith("H") else code + "H"):
            if value.casefold() not in seen:
                seen.add(value.casefold())
                values.append(value)
    return values[:12]


def _fts_tokens(query):
    tokens = []
    seen = set()

    def add(value):
        token = _normalize_text(value).casefold().replace('"', "")
        if not token or token in seen:
            return
        seen.add(token)
        tokens.append(token)

    for term in _exact_terms(query):
        add(term)
    for match in _ASCII_WORD_RE.finditer(query):
        value = match.group(0)
        if value.upper() not in _EXACT_NOISE and _literal_instruction_word(query, value):
            add(value)
    for match in _CJK_RUN_RE.finditer(query):
        run = match.group(0)
        if len(run) <= 12:
            add(run)
        if len(run) == 1:
            add(run)
        else:
            for index in range(len(run) - 1):
                add(run[index : index + 2])
    return tokens[:48]


def _cjk_bigram_set(value):
    terms = set()
    for match in _CJK_RUN_RE.finditer(_normalize_text(value)):
        run = match.group(0)
        terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return terms


def _fts_expression(query):
    tokens = _fts_tokens(query)
    if not tokens:
        return ""
    return " OR ".join('"{}"'.format(token.replace('"', '""')) for token in tokens)


def _fts_match_quality(query, result, bm25_score=0.0):
    """Return lexical coverage for a candidate, or zero when it is too weak.

    CJK bigram OR queries deliberately favor recall, but without a coverage
    gate generic requests such as "modify this program" match arbitrary pages
    containing only one common word.  Exact entity hits bypass this function;
    FTS-only evidence must cover a meaningful share of the actual query.
    """

    tokens = _fts_tokens(query)
    if not tokens:
        return 0.0, []
    haystack = _normalize_text(
        " ".join(
            str(result.get(key, "") or "")
            for key in ("section", "text")
        )
    ).casefold()
    matched = [token for token in tokens if token.casefold() in haystack]
    coverage = len(matched) / len(tokens)
    product_hits = [
        term
        for term in _exact_terms(query)
        if _PRODUCT_TERM_RE.fullmatch(term) and term.casefold() in haystack
    ]
    # A full Mitsubishi module identifier is already an unambiguous lexical
    # match.  Do not reject its English manual page merely because the rest of
    # the user query is Chinese and therefore cannot overlap that page.
    if product_hits:
        return max(coverage, 0.55), matched
    normalized_query = _normalize_text(query).casefold()
    has_domain_marker = any(
        marker.casefold() in normalized_query for marker in _PLC_DOMAIN_MARKERS
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


def _entity_references(connection, schema, terms, plc_model, task_type, source_lanes=None, exclude_chunk_types=()):
    table = schema.get("entity_index")
    if not table or not terms:
        return []
    columns = table["columns"]
    term_columns = _matching_columns(columns, _ENTITY_TERM_COLUMNS)
    chunk_column = _first_column(columns, _ENTITY_CHUNK_COLUMNS)
    if not term_columns or not chunk_column:
        return []

    select_columns = "e.*"
    join_clause = ""
    chunks_table = schema.get("chunks")
    if chunks_table:
        chunks_id_column = _first_column(chunks_table["columns"], _CHUNK_ID_COLUMNS)
        section_column = _first_column(chunks_table["columns"], _SECTION_COLUMNS)
        if chunks_id_column and section_column:
            select_columns += ", c.{} AS _entity_section".format(
                _quote_identifier(section_column)
            )
            join_clause = " LEFT JOIN {} AS c ON c.{} = e.{}".format(
                _quote_identifier(chunks_table["name"]),
                _quote_identifier(chunks_id_column),
                _quote_identifier(chunk_column),
            )
    occurrence_column = _first_column(columns, _ENTITY_OCCURRENCE_COLUMNS)
    term_clause = " OR ".join(
        "e.{} COLLATE NOCASE = ?".format(_quote_identifier(column))
        for column in term_columns
    )
    order_clause = (
        " ORDER BY e.{} DESC".format(_quote_identifier(occurrence_column))
        if occurrence_column
        else ""
    )
    sql = "SELECT {} FROM {} AS e{} WHERE ({}){} LIMIT {}".format(
        select_columns,
        _quote_identifier(table["name"]),
        join_clause,
        term_clause,
        order_clause,
        _MAX_ENTITY_ROWS_PER_TERM,
    )
    scope_values = []
    if source_lanes is not None and chunks_table:
        from knowledge.scope import source_subquery
        subquery, scope_values = source_subquery(
            connection, schema, source_lanes, exclude_chunk_types=exclude_chunk_types,
        )
        scope_clause = f" AND e.{_quote_identifier(chunk_column)} IN ({subquery})"
        sql = sql.replace(" WHERE (" + term_clause + ")", " WHERE (" + term_clause + ")" + scope_clause)

    grouped = {}
    for matched_order, requested_term in enumerate(terms):
        rows = []
        seen_chunk_ids = set()
        for query_term in _entity_term_variants(requested_term):
            variant_rows = connection.execute(
                sql,
                tuple(query_term for _column in term_columns) + tuple(scope_values),
            ).fetchall()
            for row in variant_rows:
                chunk_id = row[chunk_column]
                if chunk_id is None or str(chunk_id) in seen_chunk_ids:
                    continue
                seen_chunk_ids.add(str(chunk_id))
                rows.append(row)
        for row in rows:
            if not _row_in_scope(row, plc_model, task_type):
                continue
            chunk_id = row[chunk_column]
            if chunk_id is None:
                continue
            occurrence_value = _row_value(row, _ENTITY_OCCURRENCE_COLUMNS, 1)
            try:
                occurrences = max(1.0, float(occurrence_value))
            except (TypeError, ValueError):
                occurrences = 1.0
            section = (
                _normalize_text(row["_entity_section"])
                if "_entity_section" in row.keys()
                else ""
            )
            matched = str(requested_term)
            normalized_entity = _normalize_text(requested_term)
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", normalized_entity):
                title_match = bool(
                    re.search(
                        rf"(?<![A-Za-z0-9_]){re.escape(normalized_entity)}"
                        rf"(?![A-Za-z0-9_])",
                        section,
                        flags=re.IGNORECASE,
                    )
                )
            else:
                title_match = bool(
                    normalized_entity
                    and normalized_entity.casefold() in section.casefold()
                )
            grouped.setdefault(matched_order, []).append(
                ("id", chunk_id, matched, title_match, occurrences)
            )

    # A manual usually mentions an opcode in its table of contents, overview,
    # execution-time appendix and detailed instruction pages.  Rank repeated
    # occurrences first, then interleave different query entities.  This puts
    # the detailed instruction page ahead of the table of contents and prevents
    # one common device (for example Y000) from consuming the whole top-k list.
    for group in grouped.values():
        group.sort(key=lambda item: (-int(item[3]), -item[4], str(item[1])))
    references = []
    ordered_groups = [grouped[key] for key in sorted(grouped)]
    depth = 0
    while any(depth < len(group) for group in ordered_groups):
        for group in ordered_groups:
            if depth < len(group):
                kind, chunk_id, matched, _title_match, _occurrences = group[depth]
                references.append((kind, chunk_id, matched, len(references)))
        depth += 1
    return references[:_MAX_CANDIDATES]


def _alias_occurs(query, alias):
    normalized_query = _normalize_text(query).casefold()
    normalized_alias = _normalize_text(alias).casefold()
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


def _instruction_heading_terms(section):
    terms = set(_exact_terms(section))
    for match in _COMPARISON_FAMILY_RE.finditer(section):
        prefix, operators = match.groups()
        terms.update(
            prefix.upper() + re.sub(r"\s+", "", operator)
            for operator in operators.split(",")
        )
    return terms


def _manual_instruction_references(connection, schema, terms, plc_model, task_type, structured_refs):
    """Recall catalogued opcodes missing from the prebuilt instruction tables.

    Cache only official chapter metadata in memory for this read-only database
    connection. A chapter title must name the opcode (including abbreviated
    comparison families), and the original body is checked after fetching it.
    This narrow path does not relax the generic FTS/dense relevance gates.
    """

    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    known = DEFAULT_INSTRUCTION_REGISTRY.known_mnemonics()
    structured_terms = {
        _normalize_text(item["matched"]).upper()
        for item in structured_refs
        if item["match_type"] == "structured_instruction"
    }
    missing = [term for term in terms if term in known and term not in structured_terms]
    if not missing:
        return []
    chunks = schema.get("chunks")
    columns = chunks["columns"] if chunks else ()
    id_column = _first_column(columns, _CHUNK_ID_COLUMNS)
    section_column = _first_column(columns, _SECTION_COLUMNS)
    if not id_column or not section_column or "manual_type" not in columns:
        return []
    index = getattr(_thread_state, "instruction_sections", None)
    if index is None:
        selected_columns = list(dict.fromkeys(
            [id_column, section_column, "manual_type"]
            + _matching_columns(columns, _MODEL_COLUMNS + _TASK_COLUMNS)
        ))
        rows = connection.execute(
            "SELECT {} FROM {} WHERE manual_type IN ({})".format(
                ",".join(_quote_identifier(column) for column in selected_columns),
                _quote_identifier(chunks["name"]),
                ",".join("?" for _ in _OFFICIAL_INSTRUCTION_MANUAL_TYPES),
            ),
            tuple(_OFFICIAL_INSTRUCTION_MANUAL_TYPES),
        ).fetchall()
        index = {}
        for row in rows:
            section = _normalize_text(row[section_column])
            for term in _instruction_heading_terms(section).intersection(known):
                index.setdefault(term, []).append(row)
        _thread_state.instruction_sections = index
    references = []
    # Round-robin keeps one common opcode from exhausting the candidate limit.
    groups = [
        [(term, row) for row in index.get(term, []) if _row_in_scope(row, plc_model, task_type)]
        for term in missing
    ]
    for depth in range(min(_MAX_ENTITY_ROWS_PER_TERM, max(map(len, groups), default=0))):
        for group in groups:
            if depth >= len(group):
                continue
            term, row = group[depth]
            references.append({
                "kind": "id", "value": row[id_column], "matched": term,
                "match_type": "manual_instruction", "rank": len(references),
            })
            if len(references) >= _MAX_CANDIDATES:
                return references
    return references


def _structured_references(connection, schema, query, terms, plc_model, task_type, source_lanes=None):
    """Return exact/metadata candidates; final ordering is owned by RRF."""

    references = []
    seen = set()
    ranks = {}

    def add(chunk_id, matched, match_type):
        if chunk_id is None:
            return
        key = (str(chunk_id), str(match_type), _normalize_text(matched).casefold())
        if key in seen:
            return
        seen.add(key)
        rank = ranks.get(str(match_type), 0)
        ranks[str(match_type)] = rank + 1
        references.append(
            {
                "kind": "id",
                "value": chunk_id,
                "matched": str(matched or ""),
                "match_type": str(match_type),
                "rank": rank,
            }
        )

    manuals = schema.get("manuals")
    chunks = schema.get("chunks")
    if manuals and chunks and {
        "manual_id",
        "manual_number",
        "title",
    }.issubset(set(manuals["columns"])) and {
        "id",
        "manual_id",
        "section",
        "chunk_type",
    }.issubset(set(chunks["columns"])):
        normalized_query = _normalize_text(query).casefold()
        manual_rows = connection.execute(
            "SELECT manual_id,manual_number,title FROM {}".format(
                _quote_identifier(manuals["name"])
            )
        ).fetchall()
        matched_manual_ids = []
        for row in manual_rows:
            manual_number = _normalize_text(row["manual_number"]).casefold()
            title = _normalize_text(row["title"]).casefold()
            exact_number = manual_number and manual_number in normalized_query
            gx_title = "gx works2" in normalized_query and "gx works2" in title
            if exact_number or gx_title:
                matched_manual_ids.append(str(row["manual_id"]))
        query_words = {
            match.group(0).casefold()
            for match in _ASCII_WORD_RE.finditer(normalized_query)
            if match.group(0).upper() not in _EXACT_NOISE
        }
        query_words.update(_cjk_bigram_set(normalized_query))
        for manual_id in matched_manual_ids:
            rows = connection.execute(
                "SELECT id,section,chunk_type FROM {} WHERE manual_id=?".format(
                    _quote_identifier(chunks["name"])
                ),
                (manual_id,),
            ).fetchall()
            matched_rows = []
            for row in rows:
                section = _normalize_text(row["section"])
                section_terms = {
                    match.group(0).casefold()
                    for match in _ASCII_WORD_RE.finditer(section)
                    if match.group(0).upper() not in _EXACT_NOISE
                }
                section_terms.update(_cjk_bigram_set(section))
                coverage = (
                    len(section_terms.intersection(query_words)) / len(section_terms)
                    if section_terms
                    else 0.0
                )
                if coverage > 0:
                    matched_rows.append((coverage, str(row["id"]), row["id"], section))
            matched_rows.sort(key=lambda item: (-item[0], item[1]))
            for _coverage, _stable_id, chunk_id, section in matched_rows[:16]:
                add(chunk_id, section, "manual_section")

    # Precise metadata routes remain candidate selectors, not scoring boosts.
    if (
        chunks
        and {"id", "manual_type", "section"}.issubset(chunks["columns"])
        and _query_is_direction_output_assignment(query)
    ):
        rows = connection.execute(
            "SELECT id,section FROM {} WHERE manual_type='positioning' "
            "AND section LIKE '%Assignment of Output Numbers%'".format(
                _quote_identifier(chunks["name"]),
            )
        ).fetchall()
        for row in rows:
            add(row["id"], row["section"], "manual_section")

    if (
        chunks and {"id", "manual_type", "section", "text"}.issubset(chunks["columns"])
        and _query_is_timer_preset(query)
    ):
        rows = connection.execute(
            "SELECT * FROM {} WHERE manual_type IN ('programming','structured_device') "
            "AND section LIKE '%Numbers of timers%'".format(_quote_identifier(chunks["name"])),
        ).fetchall()
        for row in rows:
            if _row_in_scope(row, plc_model, task_type) and _timer_range_evidence(row["text"], plc_model):
                add(row["id"], row["section"], "manual_section")

    aliases = schema.get("instruction_aliases")
    if aliases and {
        "alias_norm",
        "alias",
        "alias_type",
        "chunk_id",
    }.issubset(set(aliases["columns"])):
        rows = connection.execute(
            "SELECT alias_norm,alias,alias_type,chunk_id "
            "FROM {} WHERE chunk_id IS NOT NULL".format(
                _quote_identifier(aliases["name"])
            )
        ).fetchall()
        for row in rows:
            alias = str(row["alias"] or "")
            if _literal_instruction_word(query, alias) and _alias_occurs(query, alias):
                add(row["chunk_id"], alias, "structured_instruction")

    errors = schema.get("error_records")
    error_terms = _error_terms(query)
    if errors and error_terms and {
        "error_code_norm",
        "error_code",
        "chunk_id",
    }.issubset(set(errors["columns"])):
        placeholders = ",".join("?" for _value in error_terms)
        rows = connection.execute(
            "SELECT error_code,chunk_id FROM {} "
            "WHERE error_code_norm IN ({}) AND chunk_id IS NOT NULL".format(
                _quote_identifier(errors["name"]), placeholders
            ),
            tuple(value.casefold() for value in error_terms),
        ).fetchall()
        for row in rows:
            add(row["chunk_id"], row["error_code"], "structured_error")

    devices = schema.get("device_records")
    if devices and terms and {
        "device_norm",
        "device",
        "chunk_id",
    }.issubset(set(devices["columns"])):
        device_terms = [term for term in terms if _DEVICE_RE.fullmatch(term)]
        for term in device_terms:
            rows = connection.execute(
                "SELECT device,chunk_id FROM {} "
                "WHERE device_norm=? AND chunk_id IS NOT NULL".format(
                    _quote_identifier(devices["name"])
                ),
                (term.casefold(),),
            ).fetchall()
            for row in rows:
                add(row["chunk_id"], row["device"], "structured_device")

    cases = schema.get("debug_cases") if source_lanes is None or "debug" in source_lanes else None
    if cases and {
        "chunk_id",
        "title",
        "symptom",
        "root_cause",
        "entities_json",
        "task_types",
        "plc_models",
    }.issubset(set(cases["columns"])):
        query_tokens = _fts_tokens(query)
        query_bigrams = _cjk_bigram_set(query)
        query_terms = {str(term).casefold() for term in terms}
        matched_cases = []
        rows = connection.execute(
            "SELECT * FROM {}".format(_quote_identifier(cases["name"]))
        ).fetchall()
        for row in rows:
            if not _row_in_scope(row, plc_model, task_type):
                continue
            try:
                entities = {
                    str(value).casefold()
                    for value in json.loads(str(row["entities_json"] or "[]"))
                }
            except (TypeError, ValueError):
                entities = set()
            entity_hits = query_terms.intersection(entities)
            case_text = _normalize_text(
                " ".join(
                    str(row[key] or "")
                    for key in ("title", "symptom", "root_cause")
                )
            ).casefold()
            lexical_hits = {
                token.casefold()
                for token in query_tokens
                if token.casefold() in case_text
            }
            case_bigrams = _cjk_bigram_set(case_text)
            bigram_coverage = (
                len(query_bigrams.intersection(case_bigrams)) / len(query_bigrams)
                if query_bigrams
                else 0.0
            )
            if not entity_hits and len(lexical_hits) < 2 and bigram_coverage < 0.12:
                continue
            matched_cases.append(
                (
                    bool(entity_hits),
                    len(lexical_hits),
                    bigram_coverage,
                    str(row["chunk_id"]),
                    row,
                )
            )
        matched_cases.sort(
            key=lambda item: (-int(item[0]), -item[1], -item[2], item[3])
        )
        for _entity, _lexical, _coverage, _stable_id, row in matched_cases:
            add(row["chunk_id"], row["title"], "debug_case")

    return references[:_MAX_CANDIDATES]

def _fts_references(connection, schema, expression, candidate_limit, source_lanes=None, exclude_chunk_types=()):
    table = schema.get("chunks_fts")
    if not table or not expression:
        return []
    table_name = _quote_identifier(table["name"])
    sql = (
        "SELECT rowid AS _fts_rowid, *, bm25({name}) AS _bm25 "
        "FROM {name} WHERE {name} MATCH ? ORDER BY _bm25 LIMIT ?"
    ).format(name=table_name)
    params = [expression]
    if source_lanes is not None and schema.get("chunks"):
        from knowledge.scope import source_subquery
        fts_id = _first_column(table["columns"], _CHUNK_ID_COLUMNS)
        subquery, values = source_subquery(
            connection, schema, source_lanes, rowid=not bool(fts_id),
            exclude_chunk_types=exclude_chunk_types,
        )
        identifier = _quote_identifier(fts_id) if fts_id else "rowid"
        sql = sql.replace(" ORDER BY _bm25", f" AND {identifier} IN ({subquery}) ORDER BY _bm25")
        params.extend(values)
    params.append(int(candidate_limit))
    rows = connection.execute(sql, params).fetchall()
    id_column = _first_column(table["columns"], _CHUNK_ID_COLUMNS)
    references = []
    for rank, row in enumerate(rows):
        if id_column and row[id_column] is not None:
            reference = ("id", row[id_column])
        else:
            reference = ("rowid", row["_fts_rowid"])
        try:
            bm25_score = float(row["_bm25"])
        except (TypeError, ValueError):
            bm25_score = 0.0
        references.append((reference[0], reference[1], rank, bm25_score))
    return references


def _query_has_dense_scope(query, structured_refs):
    normalized = _normalize_text(query).casefold()
    if any(marker.casefold() in normalized for marker in _PLC_DOMAIN_MARKERS):
        return True
    if _DEVICE_RE.search(query) or _PRODUCT_TERM_RE.search(query) or _error_terms(query):
        return True
    return any(
        str(item.get("match_type") or "").startswith("structured_")
        or item.get("match_type") in {"debug_case", "manual_section"}
        for item in structured_refs
    )


def _dense_index_ready(connection, schema):
    """Verify sidecar/database identity once per read-only thread connection."""

    cached = getattr(_thread_state, "dense_verification", None)
    if cached is not None:
        return cached
    result = {"ready": False, "model": "", "reason": "unavailable"}
    vector_table = schema.get("vector_embeddings")
    chunks_table = schema.get("chunks")
    if not vector_table or not chunks_table:
        _thread_state.dense_verification = result
        return result
    try:
        from knowledge.dense import dense_model_info

        model_info = dense_model_info()
        meta = _load_meta(connection, schema)
        model_name = str(meta.get("vector_model") or "")
        corpus_sha256 = str(meta.get("vector_corpus_sha256") or "")
        if (
            not model_info
            or str(meta.get("vector_status") or "") != "ready"
            or model_info.get("model") != model_name
            or model_info.get("corpus_sha256") != corpus_sha256
        ):
            result["reason"] = "model_identity_mismatch"
        else:
            vector_name = _quote_identifier(vector_table["name"])
            chunks_name = _quote_identifier(chunks_table["name"])
            row = connection.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN v.content_sha256=c.text_sha256 THEN 1 ELSE 0 END) AS valid "
                f"FROM {vector_name} AS v JOIN {chunks_name} AS c ON c.id=v.chunk_id "
                "WHERE v.model=?",
                (model_name,),
            ).fetchone()
            expected = int(model_info.get("chunks") or 0)
            total = int(row["total"] or 0)
            valid = int(row["valid"] or 0)
            if expected and total == expected and valid == expected:
                result = {"ready": True, "model": model_name, "reason": ""}
            else:
                result["reason"] = "stale_chunk_vectors"
    except (ImportError, OSError, sqlite3.Error, TypeError, ValueError, KeyError):
        result["reason"] = "verification_failed"
    _thread_state.dense_verification = result
    return result


def _dense_references(connection, schema, query, candidate_limit, structured_refs, source_lanes=None, exclude_chunk_types=()):
    if not _query_has_dense_scope(query, structured_refs):
        return []
    state = _dense_index_ready(connection, schema)
    if not state.get("ready"):
        return []
    try:
        from knowledge.dense import dense_search

        options = {}
        if source_lanes is not None:
            from knowledge.scope import source_subquery
            sql, values = source_subquery(
                connection, schema, source_lanes, exclude_chunk_types=exclude_chunk_types,
            )
            options["allowed_ids"] = {str(row[0]) for row in connection.execute(sql, values)}
        return dense_search(
            query, top_k=int(candidate_limit), minimum_score=0.06, **options,
        )
    except (ImportError, OSError, TypeError, ValueError):
        return []


def _fetch_chunks(connection, schema, references):
    table = schema.get("chunks")
    if not table or not references:
        return {}
    columns = table["columns"]
    id_column = _first_column(columns, _CHUNK_ID_COLUMNS)
    by_id = []
    by_rowid = []
    for kind, value in references:
        if kind == "id" and id_column:
            by_id.append(value)
        else:
            by_rowid.append(value)

    found = {}
    table_name = _quote_identifier(table["name"])
    if by_id and id_column:
        placeholders = ",".join("?" for _item in by_id)
        sql = "SELECT rowid AS _chunk_rowid, * FROM {} WHERE {} IN ({})".format(
            table_name,
            _quote_identifier(id_column),
            placeholders,
        )
        for row in connection.execute(sql, by_id).fetchall():
            found[("id", str(row[id_column]))] = row
    if by_rowid:
        placeholders = ",".join("?" for _item in by_rowid)
        sql = "SELECT rowid AS _chunk_rowid, * FROM {} WHERE rowid IN ({})".format(
            table_name,
            placeholders,
        )
        for row in connection.execute(sql, by_rowid).fetchall():
            found[("rowid", str(row["_chunk_rowid"]))] = row
    return found


def _default_source(meta, path):
    for key in (
        "source",
        "manual_title",
        "title",
        "manual_number",
        "document",
    ):
        value = meta.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return path.stem


def _chunk_result(row, meta, path, plc_model, task_type):
    if row is None or not _row_in_scope(row, plc_model, task_type):
        return None
    text = _row_value(row, _TEXT_COLUMNS)
    if text is None or not str(text).strip():
        return None
    chunk_id = _row_value(row, _CHUNK_ID_COLUMNS, row["_chunk_rowid"])
    source = _row_value(row, _SOURCE_COLUMNS, _default_source(meta, path))
    page = _row_value(row, _PAGE_COLUMNS, "")
    pdf_page = _row_value(row, _PDF_PAGE_COLUMNS, "")
    if page in (None, ""):
        page = pdf_page
    page_end = _row_value(
        row,
        ("printed_page_end", "page_end", "end_page", "pdf_page_end"),
        "",
    )
    section = _row_value(row, _SECTION_COLUMNS, "")
    return {
        "id": str(chunk_id),
        "source": str(source),
        "page": page,
        "page_end": page_end,
        "pdf_page": pdf_page,
        "section": str(section or ""),
        "text": str(text).strip(),
        "manual_id": str(_row_value(row, ("manual_id",), "") or ""),
        "manual_number": str(_row_value(row, ("manual_number",), "") or ""),
        "revision": str(_row_value(row, ("revision",), "") or ""),
        "manual_type": str(_row_value(row, ("manual_type",), "") or ""),
        "chunk_type": str(_row_value(row, ("chunk_type",), "") or ""),
        "instruction_opcode": str(
            _row_value(row, ("instruction_opcode", "opcode"), "") or ""
        ),
        "manual_priority": int(
            _row_value(row, ("manual_priority", "priority"), 0) or 0
        ),
    }


def _structured_instruction_record(connection, schema, chunk_id):
    """Render authoritative instruction fields from SQLite at retrieval time."""
    table = schema.get("instructions")
    if not table:
        return ""
    required = {"chunk_id", "opcode", "operands_json", "restrictions_json"}
    if not required.issubset(set(table["columns"])):
        return ""
    selected = ["opcode", "operands_json", "restrictions_json"]
    if "completion_flags_json" in table["columns"]:
        selected.append("completion_flags_json")
    row = connection.execute(
        "SELECT {} FROM {} WHERE chunk_id=? LIMIT 1".format(
            ",".join(_quote_identifier(column) for column in selected),
            _quote_identifier(table["name"]),
        ),
        (chunk_id,),
    ).fetchone()
    if row is None:
        return ""
    try:
        operands = json.loads(str(row["operands_json"] or "[]"))
    except (TypeError, ValueError):
        operands = []
    try:
        restrictions = json.loads(str(row["restrictions_json"] or "[]"))
    except (TypeError, ValueError):
        restrictions = []
    flags = []
    if "completion_flags_json" in selected:
        try:
            flags = json.loads(str(row["completion_flags_json"] or "[]"))
        except (TypeError, ValueError):
            flags = []
    lines = ["[STRUCTURED INSTRUCTION RECORD]", f"INSTRUCTION: {row['opcode']}"]
    rendered = []
    for item in operands if isinstance(operands, list) else []:
        if not isinstance(item, dict):
            continue
        part = f"{item.get('position', '')}: {item.get('description', '')}".strip()
        if item.get("data_type"):
            part += f" [{item['data_type']}]"
        devices = item.get("applicable_devices") or []
        if isinstance(devices, list) and devices:
            part += " applicable=" + ",".join(str(value) for value in devices)
        if part:
            rendered.append(part)
    if rendered:
        lines.append("OPERANDS: " + "; ".join(rendered))
    if flags:
        lines.append("COMPLETION_FLAGS: " + ", ".join(str(value) for value in flags))
    if restrictions:
        lines.append("KEY_RESTRICTIONS: " + " | ".join(str(value) for value in restrictions[:3]))
    return "\n".join(lines) if len(lines) > 2 else ""


def _augment_structured_instruction(connection, schema, result):
    if result is None:
        return None
    prefix = _structured_instruction_record(connection, schema, result.get("id"))
    if not prefix:
        return result
    enriched = dict(result)
    body = str(enriched.get("text") or "")
    if body.startswith("[STRUCTURED INSTRUCTION RECORD]"):
        _old, separator, remainder = body.partition("\n\n")
        body = remainder if separator else ""
    enriched["text"] = prefix + ("\n\n" + body if body else "")
    return enriched

def _format_result_block(result):
    citation = {
        "id": result.get("id", ""),
        "source": result.get("source", ""),
        "page": result.get("page", ""),
    }
    if result.get("pdf_page") not in (None, ""):
        citation["pdf_page"] = result["pdf_page"]
    if result.get("page_end") not in (None, ""):
        citation["page_end"] = result["page_end"]
    if result.get("section"):
        citation["section"] = result["section"]
    return "[KNOWLEDGE {}]\n{}\n[/KNOWLEDGE]".format(
        json.dumps(citation, ensure_ascii=False, separators=(",", ":")),
        result.get("text", ""),
    )


def _select_with_budget(candidates, top_k, char_budget):
    selected = []
    used = 0
    seen = set()
    for candidate in candidates:
        chunk_id = str(candidate.get("id", ""))
        if not chunk_id or chunk_id in seen:
            continue
        cost = len(_format_result_block(candidate)) + (2 if selected else 0)
        if cost > char_budget - used:
            continue
        selected.append(candidate)
        seen.add(chunk_id)
        used += cost
        if len(selected) >= top_k:
            break
    return selected


def _retrieve_uncached(path, identity, query, plc_model, task_type, top_k, char_budget, source_lanes=None, exclude_chunk_types=()):
    connection = _connection(path, identity)
    schema = _schema(connection)
    if "chunks" not in schema:
        return []
    meta = _load_meta(connection, schema)
    exclude_chunk_types = tuple(sorted({
        str(value).strip().casefold()
        for value in exclude_chunk_types or ()
        if str(value).strip()
    }))
    allowed_prefilter_ids = None
    if exclude_chunk_types:
        from knowledge.scope import source_subquery
        sql, values = source_subquery(
            connection, schema, source_lanes,
            exclude_chunk_types=exclude_chunk_types,
        )
        allowed_prefilter_ids = {
            str(row[0]) for row in connection.execute(sql, values).fetchall()
        }

    exact_terms = _exact_terms(query)
    structured_refs = _structured_references(
        connection,
        schema,
        query,
        exact_terms,
        plc_model,
        task_type,
        **({"source_lanes": source_lanes} if source_lanes is not None else {}),
    )
    if allowed_prefilter_ids is not None:
        structured_refs = [
            reference for reference in structured_refs
            if reference.get("kind") != "id"
            or str(reference.get("value")) in allowed_prefilter_ids
        ]
    if "instruction" not in exclude_chunk_types:
        structured_refs.extend(_manual_instruction_references(
            connection, schema, exact_terms, plc_model, task_type, structured_refs
        ))
    # Qualified routes enter the same entity pipeline as native entities. They
    # do not change the original query, official structured lookup, or dense
    # text, and cannot be triggered by bare generic software words.
    routed_terms = query_skill_concepts(query, task_type)
    entity_terms = exact_terms + [term for term in routed_terms if term not in exact_terms]
    exact_refs = _entity_references(
        connection, schema, entity_terms, plc_model, task_type,
        **({"source_lanes": source_lanes} if source_lanes is not None else {}),
        exclude_chunk_types=exclude_chunk_types,
    )
    fts_limit = min(_MAX_CANDIDATES, max(60, top_k * 12))
    fts_refs = _fts_references(
        connection, schema, _fts_expression(query), fts_limit,
        **({"source_lanes": source_lanes} if source_lanes is not None else {}),
        exclude_chunk_types=exclude_chunk_types,
    )
    dense_limit = min(_MAX_CANDIDATES, max(80, top_k * 16))
    dense_refs = _dense_references(
        connection,
        schema,
        query,
        dense_limit,
        structured_refs,
        **({"source_lanes": source_lanes} if source_lanes is not None else {}),
        exclude_chunk_types=exclude_chunk_types,
    )

    all_refs = [
        (reference["kind"], reference["value"])
        for reference in structured_refs
    ]
    all_refs.extend((kind, value) for kind, value, _entity, _rank in exact_refs)
    all_refs.extend((kind, value) for kind, value, _rank, _score in fts_refs)
    all_refs.extend(("id", chunk_id) for chunk_id, _score, _rank in dense_refs)
    rows = _fetch_chunks(connection, schema, all_refs)

    candidates_by_id = {}

    def merge_candidate(result, match_type, matched, rank, details=None):
        if result is None:
            return
        chunk_id = str(result.get("id", ""))
        if not chunk_id:
            return
        candidate = candidates_by_id.get(chunk_id)
        if candidate is None:
            candidate = dict(result)
            candidate["_signals"] = []
            candidates_by_id[chunk_id] = candidate
        signal = {
            "type": str(match_type),
            "matched": str(matched or ""),
            "rank": max(0, int(rank)),
        }
        if details:
            signal.update(details)
        candidate["_signals"].append(signal)

    for fallback_rank, reference in enumerate(structured_refs):
        row = rows.get((reference["kind"], str(reference["value"])))
        result = _chunk_result(row, meta, path, plc_model, task_type)
        if reference["match_type"] == "structured_instruction":
            result = _augment_structured_instruction(connection, schema, result)
        if reference["match_type"] == "manual_instruction" and (
            result is None or not _alias_occurs(result["text"], reference["matched"])
        ):
            continue
        merge_candidate(
            result,
            reference["match_type"],
            reference["matched"],
            reference.get("rank", fallback_rank),
        )

    for kind, value, entity, entity_rank in exact_refs:
        row = rows.get((kind, str(value)))
        result = _chunk_result(row, meta, path, plc_model, task_type)
        if result is None:
            continue
        merge_candidate(result, "entity", entity, entity_rank)

    for kind, value, rank, bm25_score in fts_refs:
        row = rows.get((kind, str(value)))
        result = _chunk_result(row, meta, path, plc_model, task_type)
        if result is None:
            continue
        coverage, matched_terms = _fts_match_quality(query, result, bm25_score)
        if coverage <= 0:
            continue
        merge_candidate(
            result,
            "bm25",
            "",
            rank,
            {
                "bm25": bm25_score,
                "query_coverage": round(coverage, 4),
                "matched_terms": matched_terms,
            },
        )

    for chunk_id, cosine, rank in dense_refs:
        row = rows.get(("id", str(chunk_id)))
        result = _chunk_result(row, meta, path, plc_model, task_type)
        if result is None:
            continue
        merge_candidate(
            result,
            "vector",
            "",
            rank,
            {
                "vector_cosine": round(float(cosine), 6),
                "vector_rank": int(rank + 1),
            },
        )

    candidates = []
    scoped_candidates = list(candidates_by_id.values())
    if source_lanes is not None:
        from knowledge.scope import filter_records
        scoped_candidates = filter_records(scoped_candidates, source_lanes)
    for candidate in scoped_candidates:
        signals = candidate.pop("_signals", [])
        best_by_channel = {}
        for signal in signals:
            channel = (
                signal["type"]
                if signal["type"] in {"entity", "bm25", "vector"}
                else "structured"
            )
            current = best_by_channel.get(channel)
            if current is None or signal["rank"] < current["rank"]:
                best_by_channel[channel] = signal
        fused = sum(
            1.0 / (_RRF_K + signal["rank"] + 1.0)
            for signal in best_by_channel.values()
        )
        ordered_signals = sorted(
            best_by_channel.values(),
            key=lambda signal: signal["rank"],
        )
        candidate["retrieval_signals"] = [
            signal["type"] for signal in ordered_signals
        ]
        candidate["score"] = round(fused, 8)
        if ordered_signals:
            best_signal = ordered_signals[0]
            candidate["match_type"] = best_signal["type"]
            candidate["matched_entity"] = best_signal.get("matched", "")
        bm25_signal = best_by_channel.get("bm25")
        if bm25_signal:
            for key in ("bm25", "query_coverage", "matched_terms"):
                if key in bm25_signal:
                    candidate[key] = bm25_signal[key]
        vector_signal = best_by_channel.get("vector")
        if vector_signal:
            for key in ("vector_cosine", "vector_rank"):
                if key in vector_signal:
                    candidate[key] = vector_signal[key]
        candidates.append(candidate)

    candidates.sort(
        key=lambda item: (
            -float(item.get("score", 0.0)),
            -int(item.get("manual_priority", 0) or 0),
            int(item.get("pdf_page", 0) or 0),
            str(item.get("id", "")),
        )
    )

    query_term_set = {term.casefold() for term in exact_terms}
    from knowledge.source_authority import authoritative_instruction_manual

    source_authority = {
        opcode: authoritative_instruction_manual(opcode, plc_model)
        for opcode in query_term_set
    }
    source_authority = {
        opcode: manual_id
        for opcode, manual_id in source_authority.items()
        if manual_id
    }
    if source_authority:
        preferred = {}
        for candidate in candidates:
            opcode = _normalize_text(candidate.get("instruction_opcode", "")).casefold()
            chunk_type = _normalize_text(candidate.get("chunk_type", "")).casefold()
            if chunk_type != "instruction" or opcode not in query_term_set:
                continue
            expected_manual = source_authority.get(opcode)
            if expected_manual and candidate.get("manual_id") == expected_manual and opcode not in preferred:
                preferred[opcode] = candidate

        if preferred:
            deduped = []
            emitted = set()
            for candidate in candidates:
                opcode = _normalize_text(candidate.get("instruction_opcode", "")).casefold()
                chunk_type = _normalize_text(candidate.get("chunk_type", "")).casefold()
                if opcode in preferred and chunk_type == "instruction":
                    if opcode in emitted:
                        continue
                    deduped.append(preferred[opcode])
                    emitted.add(opcode)
                    continue
                deduped.append(candidate)
            candidates = deduped

    return _select_with_budget(candidates, top_k, char_budget)


def _retrieve_design_uncached(path, identity, query, plc_model, task_type, top_k, char_budget):
    """Rank analysis-scoped curated design chunks separately from hard facts.

    This lane is intentionally source-type based. It contains no architecture
    catalog in code; the design vocabulary, applicability and trade-offs live in
    SQLite rows with ``manual_type=curated_design``.
    """
    if str(task_type or "").casefold() != "analysis":
        return []
    connection = _connection(path, identity)
    schema = _schema(connection)
    table = schema.get("chunks")
    if not table:
        return []
    required = {"manual_type", "text"}
    if not required.issubset(set(table["columns"])):
        return []

    table_name = _quote_identifier(table["name"])
    rows = connection.execute(
        f"SELECT rowid AS _chunk_rowid, * FROM {table_name} "
        "WHERE manual_type='curated_design' ORDER BY manual_priority DESC, id LIMIT 512"
    ).fetchall()
    if not rows:
        return []

    # The appended generic words are retrieval metadata within an already
    # source-scoped lane; they do not describe or privilege any architecture.
    scoring_query = _normalize_text(query) + " 控制架构 方案设计"
    query_tokens = _fts_tokens(scoring_query)
    query_bigrams = _cjk_bigram_set(scoring_query)
    candidates = []
    for row in rows:
        result = _chunk_result(row, {}, path, plc_model, task_type)
        if result is None:
            continue
        haystack = _normalize_text(
            str(result.get("section") or "") + " " + str(result.get("text") or "")
        ).casefold()
        matched = [token for token in query_tokens if token.casefold() in haystack]
        candidate_bigrams = _cjk_bigram_set(haystack)
        overlap = query_bigrams.intersection(candidate_bigrams)
        bigram_coverage = len(overlap) / len(query_bigrams) if query_bigrams else 0.0
        if len(matched) < 2 and bigram_coverage < 0.06:
            continue
        result["match_type"] = "curated_design"
        result["matched_entity"] = ""
        result["retrieval_signals"] = ["curated_design"]
        result["query_coverage"] = round(bigram_coverage, 4)
        result["_design_rank_key"] = (
            -len(matched),
            -bigram_coverage,
            -int(result.get("manual_priority", 0) or 0),
            str(result.get("id", "")),
        )
        candidates.append(result)

    candidates.sort(key=lambda item: item["_design_rank_key"])
    for rank, candidate in enumerate(candidates):
        candidate.pop("_design_rank_key", None)
        candidate["score"] = round(1.0 / (_RRF_K + rank + 1.0), 8)
    return _select_with_budget(candidates, top_k, char_budget)


@lru_cache(maxsize=_CACHE_SIZE)
def _retrieve_design_cached(identity, query, plc_model, task_type, top_k, char_budget):
    if identity[0] == "missing":
        return "[]"
    path = Path(identity[0])
    return _freeze_results(
        _retrieve_design_uncached(
            path, identity, query, plc_model, task_type, top_k, char_budget
        )
    )


def _retrieve_design_knowledge(
    query,
    plc_model="FX3U",
    task_type="analysis",
    top_k=2,
    char_budget=2400,
):
    """Return curated design evidence only for requirement analysis."""
    normalized_query = _normalize_text(query)
    normalized_task = _normalize_text(task_type).casefold() or "analysis"
    if not normalized_query or normalized_task != "analysis":
        return []
    try:
        normalized_top_k = max(0, min(_MAX_TOP_K, int(top_k)))
        normalized_budget = max(0, int(char_budget))
    except (TypeError, ValueError):
        return []
    if normalized_top_k == 0 or normalized_budget == 0:
        return []
    normalized_model = _normalize_text(plc_model).upper() or "FX3U"
    if _query_is_out_of_scope(normalized_query, normalized_model):
        return []
    path = _index_path()
    identity = _index_identity(path)
    try:
        frozen = _retrieve_design_cached(
            identity,
            normalized_query,
            normalized_model,
            normalized_task,
            normalized_top_k,
            normalized_budget,
        )
        return json.loads(frozen)
    except (OSError, sqlite3.Error, TypeError, ValueError, KeyError, IndexError):
        _close_thread_connection()
        return []


def _freeze_results(results):
    return json.dumps(
        results,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


@lru_cache(maxsize=_CACHE_SIZE)
def _retrieve_cached(identity, query, plc_model, task_type, top_k, char_budget, source_lanes=None, exclude_chunk_types=()):
    if identity[0] == "missing":
        return "[]"
    path = Path(identity[0])
    results = _retrieve_uncached(
        path,
        identity,
        query,
        plc_model,
        task_type,
        top_k,
        char_budget,
        **({"source_lanes": source_lanes} if source_lanes is not None else {}),
        exclude_chunk_types=exclude_chunk_types,
    )
    return _freeze_results(results)


def _retrieve_knowledge(
    query,
    plc_model="FX3U",
    task_type="generate",
    top_k=5,
    char_budget=6000,
    source_lanes=None,
    exclude_chunk_types=(),
):
    """Return ranked knowledge blocks without ever opening the index eagerly.

    Each result contains at least ``id``, ``source``, ``page`` and ``text``.
    Exact opcode/device matches precede FTS5/BM25 matches.  Individual chunks
    are skipped rather than truncated when they do not fit ``char_budget``.
    Missing or incompatible indexes quietly return an empty list.
    """

    normalized_query = _normalize_text(query)
    if not normalized_query:
        return []
    try:
        normalized_top_k = max(0, min(_MAX_TOP_K, int(top_k)))
        normalized_budget = max(0, int(char_budget))
    except (TypeError, ValueError):
        return []
    if normalized_top_k == 0 or normalized_budget == 0:
        return []

    normalized_model = _normalize_text(plc_model).upper() or "FX3U"
    normalized_task = _normalize_text(task_type).casefold() or "generate"
    normalized_excluded = tuple(sorted({
        str(value).strip().casefold()
        for value in exclude_chunk_types or ()
        if str(value).strip()
    }))
    if _query_is_out_of_scope(normalized_query, normalized_model):
        return []
    path = _index_path()
    identity = _index_identity(path)
    try:
        frozen = _retrieve_cached(
            identity,
            normalized_query,
            normalized_model,
            normalized_task,
            normalized_top_k,
            normalized_budget,
            **({"source_lanes": tuple(sorted(source_lanes))} if source_lanes is not None else {}),
            exclude_chunk_types=normalized_excluded,
        )
    except (OSError, sqlite3.Error, TypeError, ValueError, KeyError, IndexError):
        # Exceptions are intentionally handled outside the cached function so
        # a transient open/read failure is retried on the next user action.
        _close_thread_connection()
        return []
    try:
        return json.loads(frozen)
    except (TypeError, ValueError):
        return []


# Deprecated names forward through the scoped facade, never around its policy.
def retrieve_knowledge(*args, **kwargs):
    from knowledge.retriever import retrieve_knowledge as scoped
    return scoped(*args, **kwargs)


def retrieve_design_knowledge(*args, **kwargs):
    from knowledge.retriever import retrieve_design_knowledge as scoped
    return scoped(*args, **kwargs)


def build_knowledge_context(*args, **kwargs):
    from knowledge.retriever import build_knowledge_context as scoped
    return scoped(*args, **kwargs)


__all__ = []  # internal backend; public API lives in knowledge.retriever
