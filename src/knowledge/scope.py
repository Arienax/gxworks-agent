"""Haystack routing at the existing SQLite boundary, not a new agent stack.

Routes are application-owned. Document text is never a Jinja template or a rule.
The same MetadataRouter rules select SQL source partitions before candidate
limits and check returned records before prompt packing. No LLM/reranker calls.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

# Local workbench: do not enable a new telemetry channel merely by importing a
# retrieval component. Explicit deployment overrides remain the owner's choice.
os.environ.setdefault("HAYSTACK_TELEMETRY_ENABLED", "false")


def _document(**kwargs):
    from haystack.dataclasses import Document
    return Document(**kwargs)


def _router(rules):
    from haystack.components.routers.metadata_router import MetadataRouter
    return MetadataRouter(rules=rules)


@lru_cache(maxsize=1)
def _request_router():
    return _router({name: {"field": "meta." + name, "operator": "==", "value": True}
                    for name in ("design", "technical", "debug")})


_FACT_QUESTION = re.compile(
    r"手册|查证|查阅|查询|边界|范围|单位|掉电|断电|溢出|执行条件|触发条件|"
    r"时间基准|扫描周期|保持性|缓冲存储器|"
    r"\b(?:manual|datasheet|range|overflow|retentive|operand|execution|VAR_IN_OUT|ABI)\b|"
    r"\b(?:FX[0-9A-Z]+|Q[0-9A-Z]+|L[0-9A-Z]+)-[0-9A-Z-]+", re.I)


def has_fact_question(query):
    from knowledge.analysis_router import route_analysis_request
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    route = route_analysis_request(query, resolve_opcode=DEFAULT_INSTRUCTION_REGISTRY.resolve_form)
    metadata = getattr(query, "metadata", {})
    # Explicit fact questions support natural-language evidence needs even when
    # no opcode appears. Family mentions alone do not authorize debug examples.
    return bool(route.opcodes or route.devices or _FACT_QUESTION.search(str(query))
                or (isinstance(metadata, dict) and metadata.get("fact_questions")))


def retrieval_plan(query, task_type, include_design=False):
    task = str(task_type or "generate").casefold()
    analysis = task == "analysis"
    debugging = task in {"debug", "review", "program_review", "diagnosis"}
    disabled = task in {"contract_repair", "format_repair"}
    fact_requested = has_fact_question(query) if analysis and include_design else True
    doc = _document(content=str(query), meta={
        "design": analysis and bool(include_design),
        "technical": not disabled and fact_requested,
        "debug": debugging and not disabled,
    })
    outputs = _request_router().run(documents=[doc])
    return {
        "engine": "haystack.MetadataRouter", "policy": "task-source-scope-v1",
        "design": bool(outputs["design"]), "facts": bool(outputs["technical"]),
        "fact_question_present": fact_requested,
        "source_lanes": ["fact", "support", "unclassified"] + (["debug"] if outputs["debug"] else []),
    }


def source_kind(record):
    manual = str(record.get("manual_type") or "").casefold()
    chunk = str(record.get("chunk_type") or "").casefold()
    if manual in {"debug_cases", "debugging_case"} or chunk in {"debug_case", "debugging_case"}:
        return "debug"
    if manual == "curated_design" or chunk == "design_pattern" or record.get("manual_id") == "curated_control_design":
        return "design"
    if manual == "third_party_skill":
        return "support"
    return "fact" if manual else "unclassified"


@lru_cache(maxsize=16)
def _source_router(lanes):
    return _router({"eligible": {"field": "meta.source_kind", "operator": "in", "value": list(lanes)}})


def filter_records(records, lanes):
    """Keep ranking order and original metadata/text; don't replace provenance."""
    records = list(records)
    if not records:
        return []
    docs = [_document(id=str(i), meta={"source_kind": source_kind(row)}) for i, row in enumerate(records)]
    kept = _source_router(tuple(sorted(lanes))).run(documents=docs)["eligible"]
    return [records[int(doc.id)] for doc in kept]


@lru_cache(maxsize=32)
def _eligible_partitions(partitions, lanes):
    records = [{"manual_type": manual, "chunk_type": chunk, "manual_id": identity} for manual, chunk, identity in partitions]
    return tuple((r["manual_type"], r["chunk_type"], r["manual_id"]) for r in filter_records(records, lanes))


def source_sql(connection, schema, lanes=None, *, exclude_chunk_types=()):
    """Push metadata scope into SQLite before candidate limits and ranking.

    MetadataRouter still owns source-lane selection. exclude_chunk_types is a
    runtime pre-filter over the same indexed metadata when an exact structured
    lookup already owns that candidate type.
    """
    from knowledge.core import _quote_identifier
    table = schema["chunks"]
    columns = table["columns"]
    expressions = [_quote_identifier(k) if k in columns else "''" for k in ("manual_type", "chunk_type", "manual_id")]
    expressions = [f"COALESCE({x}, '')" for x in expressions]
    partitions = tuple(tuple(row) for row in connection.execute(
        f"SELECT DISTINCT {', '.join(expressions)} FROM {_quote_identifier(table['name'])}").fetchall())
    accepted = (partitions if lanes is None
                else _eligible_partitions(partitions, tuple(sorted(lanes))))
    excluded = {str(value).strip().casefold() for value in exclude_chunk_types or () if str(value).strip()}
    if excluded:
        accepted = tuple(pair for pair in accepted if str(pair[1]).casefold() not in excluded)
    if not accepted:
        return "0", []
    predicate = " OR ".join("(" + " AND ".join(f"{expression}=?" for expression in expressions) + ")" for _ in accepted)
    return "(" + predicate + ")", [value for pair in accepted for value in pair]


def source_subquery(
    connection, schema, lanes=None, *, rowid=False,
    exclude_chunk_types=(), exclude_structured_kinds=(),
):
    from knowledge.core import _first_column, _CHUNK_ID_COLUMNS, _quote_identifier
    table = schema["chunks"]
    chunk_id_column = _first_column(table["columns"], _CHUNK_ID_COLUMNS)
    identifier = None if rowid else chunk_id_column
    identifier = _quote_identifier(identifier) if identifier else "rowid"
    owner_identifier = (
        _quote_identifier(chunk_id_column) if chunk_id_column else "rowid"
    )
    predicate, values = source_sql(
        connection, schema, lanes, exclude_chunk_types=exclude_chunk_types,
    )
    owner_tables = {
        "device": "device_records",
        "error": "error_records",
    }
    structured_clauses = []
    for raw_kind in exclude_structured_kinds or ():
        kind = str(raw_kind or "").strip().casefold()
        owner_name = owner_tables.get(kind)
        owner = schema.get(owner_name) if owner_name else None
        if not owner or "chunk_id" not in owner["columns"]:
            continue
        structured_clauses.append(
            f"{owner_identifier} NOT IN ("
            f"SELECT {_quote_identifier('chunk_id')} "
            f"FROM {_quote_identifier(owner['name'])} "
            f"WHERE {_quote_identifier('chunk_id')} IS NOT NULL)"
        )
    if structured_clauses:
        predicate = "(" + predicate + ") AND " + " AND ".join(structured_clauses)
    return (
        f"SELECT {identifier} FROM {_quote_identifier(table['name'])} WHERE {predicate}",
        values,
    )

def runtime_status():
    """Probe the installed router and bundled index in THIS interpreter, offline."""
    from importlib.metadata import version
    from knowledge.evidence import retrieval_failure
    try:
        from knowledge.core import _index_path
        import sqlite3
        plan = retrieval_plan("", "format_repair")
        path = _index_path()
        if not path.is_file():
            return {"status": "unavailable", "failure": {"code": "index_missing", "error_type": "FileNotFoundError"}}
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)
        try:
            connection.execute("SELECT 1 FROM chunks LIMIT 1").fetchone()
        finally:
            connection.close()
        return {"status": "available", "engine": plan["engine"], "haystack_version": version("haystack-ai")}
    except Exception as error:
        return {"status": "unavailable", "failure": retrieval_failure(error)}
