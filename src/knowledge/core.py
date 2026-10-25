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

from knowledge.gxworks2_concepts import query_skill_concepts

from knowledge.index_access import (
    CHUNK_ID_COLUMNS as _CHUNK_ID_COLUMNS,
    TEXT_COLUMNS as _TEXT_COLUMNS,
    SOURCE_COLUMNS as _SOURCE_COLUMNS,
    PAGE_COLUMNS as _PAGE_COLUMNS,
    PAGE_END_COLUMNS as _PAGE_END_COLUMNS,
    PDF_PAGE_COLUMNS as _PDF_PAGE_COLUMNS,
    SECTION_COLUMNS as _SECTION_COLUMNS,
    MODEL_COLUMNS as _MODEL_COLUMNS,
    TASK_COLUMNS as _TASK_COLUMNS,
    ENTITY_TERM_COLUMNS as _ENTITY_TERM_COLUMNS,
    ENTITY_CHUNK_COLUMNS as _ENTITY_CHUNK_COLUMNS,
    ENTITY_OCCURRENCE_COLUMNS as _ENTITY_OCCURRENCE_COLUMNS,
    quote_identifier as _quote_identifier,
    first_column as _first_column,
    matching_columns as _matching_columns,
    row_value as _row_value,
    index_path as _index_path,
    index_identity as _index_identity,
    close_thread_connection as _close_thread_connection,
    connection as _connection,
    table_columns as _table_columns,
    schema as _schema,
    load_meta as _load_meta,
    fetch_chunks as _fetch_chunks,
)
from knowledge.text_matching import (
    DEVICE_RE as _DEVICE_RE,
    ERROR_CODE_RE as _ERROR_CODE_RE,
    ASCII_TERM_RE as _ASCII_TERM_RE,
    COMPARISON_FAMILY_RE as _COMPARISON_FAMILY_RE,
    PRODUCT_TERM_RE as _PRODUCT_TERM_RE,
    ASCII_WORD_RE as _ASCII_WORD_RE,
    CJK_RUN_RE as _CJK_RUN_RE,
    EXACT_NOISE as _EXACT_NOISE,
    PLC_DOMAIN_MARKERS as _PLC_DOMAIN_MARKERS,
    NON_MITSUBISHI_MARKERS as _NON_MITSUBISHI_MARKERS,
    MITSUBISHI_SCOPE_MARKERS as _MITSUBISHI_SCOPE_MARKERS,
    normalize_text as _normalize_text,
    literal_instruction_word as _literal_instruction_word,
    exact_terms as _exact_terms,
    entity_term_variants as _entity_term_variants,
    error_terms as _error_terms,
    fts_tokens as _fts_tokens,
    cjk_bigram_set as _cjk_bigram_set,
    fts_expression as _fts_expression,
    fts_match_quality as _fts_match_quality,
    alias_occurs as _alias_occurs,
    instruction_heading_terms as _instruction_heading_terms,
)
from knowledge.scope import (
    query_is_out_of_scope as _query_is_out_of_scope,
    scope_values as _scope_values,
    scope_matches as _scope_matches,
    row_in_scope as _row_in_scope,
)
from knowledge.record_projection import (
    default_source as _default_source,
    chunk_result as _chunk_result,
    source_applicability as _source_applicability,
    structured_instruction_record as _structured_instruction_record,
    augment_structured_instruction as _augment_structured_instruction,
    format_result_block as _format_result_block,
    select_ranked as _select_ranked,
)
from knowledge.record_projection import MAX_TOP_K as _MAX_TOP_K
from knowledge import index_access


_CACHE_SIZE = 256
_MAX_CANDIDATES = 200
_MAX_ENTITY_ROWS_PER_TERM = 64
_RRF_K = 60.0


# Official manuals abbreviate comparison families in headings, for example
# "AND=, >, <, < >, <=, >=". Expand the heading, never the source text.
from knowledge.scope import OFFICIAL_INSTRUCTION_TYPES as _OFFICIAL_INSTRUCTION_MANUAL_TYPES


def _entity_references(connection, schema, terms, plc_model, task_type, source_lanes=None, exclude_chunk_types=(), exclude_structured_kinds=()):
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
    if chunks_table:
        from knowledge.scope import source_subquery
        subquery, scope_values = source_subquery(
            connection, schema, source_lanes,
            exclude_chunk_types=exclude_chunk_types,
            exclude_structured_kinds=exclude_structured_kinds,
            plc_model=plc_model, task_type=task_type,
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


def _broad_metadata_references(connection, schema, query, terms, plc_model, task_type, source_lanes=None):
    """Return broad metadata-routed candidates; exact PLC facts live elsewhere."""

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

def _fts_references(connection, schema, expression, candidate_limit, source_lanes=None, exclude_chunk_types=(), exclude_structured_kinds=(), *, plc_model=None, task_type=None):
    table = schema.get("chunks_fts")
    if not table or not expression:
        return []
    table_name = _quote_identifier(table["name"])
    sql = (
        "SELECT rowid AS _fts_rowid, *, bm25({name}) AS _bm25 "
        "FROM {name} WHERE {name} MATCH ? ORDER BY _bm25 LIMIT ?"
    ).format(name=table_name)
    params = [expression]
    if schema.get("chunks"):
        from knowledge.scope import source_subquery
        fts_id = _first_column(table["columns"], _CHUNK_ID_COLUMNS)
        subquery, values = source_subquery(
            connection, schema, source_lanes, rowid=not bool(fts_id),
            exclude_chunk_types=exclude_chunk_types,
            exclude_structured_kinds=exclude_structured_kinds,
            plc_model=plc_model, task_type=task_type,
        )
        # With MATCH plus a rowid-IN scope, FTS5 may repeat MATCH for every
        # permitted row (virtual plan =M). Unary + on the integer rowid keeps
        # the same pre-limit filter, but runs MATCH once (plan M). Do not apply
        # this to declared columns, where removing affinity could change IDs.
        identifier = _quote_identifier(fts_id) if fts_id else "+rowid"
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

    cached = index_access.cached_value(connection, "dense_verification")
    if cached is not None:
        return cached
    result = {"ready": False, "model": "", "reason": "unavailable"}
    vector_table = schema.get("vector_embeddings")
    chunks_table = schema.get("chunks")
    if not vector_table or not chunks_table:
        index_access.cache_value(connection, "dense_verification", result)
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
    index_access.cache_value(connection, "dense_verification", result)
    return result


def _dense_references(connection, schema, query, candidate_limit, structured_refs, source_lanes=None, exclude_chunk_types=(), exclude_structured_kinds=(), *, plc_model=None, task_type=None):
    if not _query_has_dense_scope(query, structured_refs):
        return []
    state = _dense_index_ready(connection, schema)
    if not state.get("ready"):
        return []
    try:
        from knowledge.dense import dense_search

        options = {}
        if source_lanes is not None or exclude_chunk_types or exclude_structured_kinds or plc_model is not None:
            from knowledge.scope import source_subquery
            sql, values = source_subquery(
                connection, schema, source_lanes,
                exclude_chunk_types=exclude_chunk_types,
                exclude_structured_kinds=exclude_structured_kinds,
                plc_model=plc_model, task_type=task_type,
            )
            options["allowed_ids"] = {str(row[0]) for row in connection.execute(sql, values)}
        return dense_search(
            query, top_k=int(candidate_limit), minimum_score=0.06, **options,
        )
    except (ImportError, OSError, TypeError, ValueError):
        return []


def _retrieve_uncached(path, identity, query, plc_model, task_type, top_k, char_budget, source_lanes=None, exclude_chunk_types=(), exclude_structured_kinds=()):
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
    exclude_structured_kinds = tuple(sorted({
        str(value).strip().casefold()
        for value in exclude_structured_kinds or ()
        if str(value).strip()
    }))
    allowed_prefilter_ids = None
    if exclude_chunk_types or exclude_structured_kinds or plc_model:
        from knowledge.scope import source_subquery
        sql, values = source_subquery(
            connection, schema, source_lanes,
            exclude_chunk_types=exclude_chunk_types,
            exclude_structured_kinds=exclude_structured_kinds,
            plc_model=plc_model, task_type=task_type,
        )
        allowed_prefilter_ids = {
            str(row[0]) for row in connection.execute(sql, values).fetchall()
        }

    exact_terms = _exact_terms(query)
    metadata_refs = _broad_metadata_references(
        connection,
        schema,
        query,
        exact_terms,
        plc_model,
        task_type,
        **({"source_lanes": source_lanes} if source_lanes is not None else {}),
    )
    if allowed_prefilter_ids is not None:
        metadata_refs = [
            reference for reference in metadata_refs
            if reference.get("kind") != "id"
            or str(reference.get("value")) in allowed_prefilter_ids
        ]
    # Qualified routes enter the same entity pipeline as native entities. They
    # do not change the original query, official structured lookup, or dense
    # text, and cannot be triggered by bare generic software words.
    routed_terms = query_skill_concepts(query, task_type)
    entity_terms = exact_terms + [term for term in routed_terms if term not in exact_terms]
    exact_refs = _entity_references(
        connection, schema, entity_terms, plc_model, task_type,
        **({"source_lanes": source_lanes} if source_lanes is not None else {}),
        exclude_chunk_types=exclude_chunk_types,
        exclude_structured_kinds=exclude_structured_kinds,
    )
    fts_limit = min(_MAX_CANDIDATES, max(60, top_k * 12))
    fts_refs = _fts_references(
        connection, schema, _fts_expression(query), fts_limit,
        **({"source_lanes": source_lanes} if source_lanes is not None else {}),
        exclude_chunk_types=exclude_chunk_types,
        exclude_structured_kinds=exclude_structured_kinds,
        plc_model=plc_model, task_type=task_type,
    )
    dense_limit = min(_MAX_CANDIDATES, max(80, top_k * 16))
    dense_refs = _dense_references(
        connection,
        schema,
        query,
        dense_limit,
        metadata_refs,
        **({"source_lanes": source_lanes} if source_lanes is not None else {}),
        exclude_chunk_types=exclude_chunk_types,
        exclude_structured_kinds=exclude_structured_kinds,
        plc_model=plc_model, task_type=task_type,
    )

    all_refs = [
        (reference["kind"], reference["value"])
        for reference in metadata_refs
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

    for fallback_rank, reference in enumerate(metadata_refs):
        row = rows.get((reference["kind"], str(reference["value"])))
        result = _chunk_result(row, meta, path, plc_model, task_type, connection=connection)
        merge_candidate(
            result,
            reference["match_type"],
            reference["matched"],
            reference.get("rank", fallback_rank),
        )

    for kind, value, entity, entity_rank in exact_refs:
        row = rows.get((kind, str(value)))
        result = _chunk_result(row, meta, path, plc_model, task_type, connection=connection)
        if result is None:
            continue
        merge_candidate(result, "entity", entity, entity_rank)

    for kind, value, rank, bm25_score in fts_refs:
        row = rows.get((kind, str(value)))
        result = _chunk_result(row, meta, path, plc_model, task_type, connection=connection)
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
        result = _chunk_result(row, meta, path, plc_model, task_type, connection=connection)
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
                else "metadata"
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


    return _select_ranked(candidates, top_k)


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
    return _select_ranked(candidates, top_k)


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


def retrieve_design_records(
    query,
    plc_model="FX3U",
    task_type="analysis",
    top_k=2,
    char_budget=None,
):
    """Return curated design evidence only for requirement analysis."""
    normalized_query = _normalize_text(query)
    normalized_task = _normalize_text(task_type).casefold() or "analysis"
    if not normalized_query or normalized_task != "analysis":
        return []
    try:
        normalized_top_k = max(0, min(_MAX_TOP_K, int(top_k)))
    except (TypeError, ValueError):
        return []
    if normalized_top_k == 0:
        return []
    normalized_model = _normalize_text(plc_model).upper() or "FX3U"
    if _query_is_out_of_scope(normalized_query, normalized_model):
        return []
    path = index_access.index_path()
    identity = _index_identity(path)
    try:
        frozen = _retrieve_design_cached(
            identity,
            normalized_query,
            normalized_model,
            normalized_task,
            normalized_top_k,
            None,
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
def _retrieve_cached(identity, query, plc_model, task_type, top_k, char_budget, source_lanes=None, exclude_chunk_types=(), exclude_structured_kinds=()):
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
        exclude_structured_kinds=exclude_structured_kinds,
    )
    return _freeze_results(results)


def retrieve_broad_knowledge(
    query,
    plc_model="FX3U",
    task_type="generate",
    top_k=5,
    char_budget=None,
    source_lanes=None,
    exclude_chunk_types=(),
    exclude_structured_kinds=(),
):
    """Return ranked knowledge blocks without ever opening the index eagerly.

    Each result contains at least ``id``, ``source``, ``page`` and ``text``.
    Exact opcode/device matches precede FTS5/BM25 matches. Complete records are
    selected by relevance; the legacy ``char_budget`` argument is ignored.
    Missing or incompatible indexes quietly return an empty list.
    """

    normalized_query = _normalize_text(query)
    if not normalized_query:
        return []
    try:
        normalized_top_k = max(0, min(_MAX_TOP_K, int(top_k)))
    except (TypeError, ValueError):
        return []
    if normalized_top_k == 0:
        return []

    normalized_model = _normalize_text(plc_model).upper() or "FX3U"
    normalized_task = _normalize_text(task_type).casefold() or "generate"
    normalized_excluded = tuple(sorted({
        str(value).strip().casefold()
        for value in exclude_chunk_types or ()
        if str(value).strip()
    }))
    normalized_structured_excluded = tuple(sorted({
        str(value).strip().casefold()
        for value in exclude_structured_kinds or ()
        if str(value).strip()
    }))
    if _query_is_out_of_scope(normalized_query, normalized_model):
        return []
    path = index_access.index_path()
    identity = _index_identity(path)
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    owner_path = DEFAULT_INSTRUCTION_REGISTRY.selected_owner_database_path()
    if owner_path is not None and _index_identity(owner_path) != identity:
        # The existing retrieval pipeline ranks each corpus independently. Fuse
        # domain ranks, never raw BM25 scores from unrelated corpus statistics.
        # A candidate failure aborts this selection rather than exposing stale
        # instruction/language roots from the installed mixed corpus.
        owner_identity = _index_identity(owner_path)
        try:
            owner_connection = _connection(owner_path, owner_identity)
            other_kinds = {str(row[0]).casefold() for row in owner_connection.execute(
                "SELECT DISTINCT kind FROM facts WHERE kind NOT IN ('instruction','language')")}
            lanes = []
            for lane_identity, exclusions, domain in (
                (owner_identity, set(normalized_structured_excluded) | other_kinds, 'selected_owner'),
                (identity, set(normalized_structured_excluded) | {'instruction', 'language'}, 'installed_other'),
            ):
                encoded = _retrieve_cached(lane_identity, normalized_query, normalized_model,
                    normalized_task, normalized_top_k, None,
                    **({"source_lanes": tuple(sorted(source_lanes))} if source_lanes is not None else {}),
                    exclude_chunk_types=normalized_excluded,
                    exclude_structured_kinds=tuple(sorted(exclusions)))
                lane = json.loads(encoded)
                for rank, result in enumerate(lane):
                    result['domain_rank'] = rank
                    result['retrieval_domain'] = domain
                    result['score'] = round(1.0 / (_RRF_K + rank + 1.0), 8)
                lanes.append(lane)
            ordered = sorted([item for lane in lanes for item in lane],
                key=lambda item: (item['domain_rank'],
                    0 if item['retrieval_domain'] == 'selected_owner' else 1, str(item.get('id',''))))
            return _select_ranked(ordered, normalized_top_k)
        except (OSError, sqlite3.Error, TypeError, ValueError, KeyError, IndexError):
            _close_thread_connection()
            return []
    try:
        frozen = _retrieve_cached(
            identity,
            normalized_query,
            normalized_model,
            normalized_task,
            normalized_top_k,
            None,
            **({"source_lanes": tuple(sorted(source_lanes))} if source_lanes is not None else {}),
            exclude_chunk_types=normalized_excluded,
            exclude_structured_kinds=normalized_structured_excluded,
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

# Internal compatibility for diagnostics which explicitly import the backend.
_retrieve_knowledge = retrieve_broad_knowledge
_retrieve_design_knowledge = retrieve_design_records


def clear_query_cache():
    """Invalidate frozen broad/design results after an explicit index override."""
    _retrieve_cached.cache_clear()
    _retrieve_design_cached.cache_clear()
