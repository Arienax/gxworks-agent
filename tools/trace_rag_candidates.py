#!/usr/bin/env python3
"""Read-only tracing of entity/FTS/dense recall and supporting reranking."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import knowledge.retriever as facade
import knowledge.core as core
from knowledge.supporting_reranker import rerank
from knowledge.gxworks2_concepts import query_skill_concepts
from tools.evaluate_rag_benchmark import result_matches


def brief(result):
    fields = (
        "id", "chunk_type", "manual_id", "section", "matched_entity",
        "retrieval_signals", "score", "gxw2_supporting_boost", "gxw2_supporting_slot",
    )
    return {key: result.get(key, 0 if key == "gxw2_supporting_boost" else None)
            for key in fields}


def trace_case(case, database, candidate_budget=sys.maxsize):
    facade._index_path = lambda: database
    facade._sync_core_hooks()
    core._retrieve_cached.cache_clear()
    core._close_thread_connection()
    captured = {}

    def capture(name, original):
        def wrapped(*args, **kwargs):
            result = original(*args, **kwargs)
            captured[name] = result
            return result
        return wrapped

    select = core._select_with_budget

    def capture_candidates(candidates, top_k, char_budget):
        captured["scored"] = [dict(item) for item in candidates]
        return select(candidates, top_k, char_budget)

    with ExitStack() as stack:
        for name in ("_entity_references", "_fts_references", "_dense_references"):
            stack.enter_context(patch.object(core, name, capture(name, getattr(core, name))))
        stack.enter_context(patch.object(core, "_select_with_budget", capture_candidates))
        before = core.retrieve_knowledge(
            case["query"], plc_model=case.get("plc_model", "FX3U"),
            task_type=case.get("task_type", "analysis"), top_k=40, char_budget=candidate_budget,
        )
    after = rerank(before, case.get("task_type", "analysis"))
    actual = facade.retrieve_knowledge(
        case["query"], plc_model=case.get("plc_model", "FX3U"),
        task_type=case.get("task_type", "analysis"), top_k=10, char_budget=50000,
    )
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        chunks = {str(row["id"]): dict(row) for row in connection.execute("SELECT * FROM chunks")}

    entity_refs = captured.get("_entity_references", [])
    fts_refs = captured.get("_fts_references", [])
    dense_refs = captured.get("_dense_references", [])
    entity_ids = {str(row[1]) for row in entity_refs}
    fts_ids = {str(row[1]) for row in fts_refs}
    dense_ids = {str(row[0]) for row in dense_refs}
    ranks = [{str(item["id"]): i for i, item in enumerate(items, 1)}
             for items in (captured.get("scored", []), before, after, actual)]
    expected = []
    for chunk_id, chunk in chunks.items():
        if not result_matches(case, chunk):
            continue
        quality, _ = core._fts_match_quality(case["query"], chunk)
        expected.append({
            **brief(chunk), "text": chunk["text"],
            "entity_recalled": chunk_id in entity_ids,
            "fts_recalled": chunk_id in fts_ids, "fts_quality": quality,
            "dense_recalled": chunk_id in dense_ids,
            "in_scope": core._row_in_scope(chunk, case.get("plc_model", "FX3U"),
                                            case.get("task_type", "analysis")),
            "scored_rank": ranks[0].get(chunk_id),
            "rank_before": ranks[1].get(chunk_id), "rank_after": ranks[2].get(chunk_id),
            "actual_rank": ranks[3].get(chunk_id),
        })

    def describe_refs(refs, dense=False):
        return [{"reference": list(row), **brief(chunks.get(str(row[0 if dense else 1]), {}))}
                for row in refs]

    return {
        **case, "candidate_budget": candidate_budget,
        "expands_supporting_pool": facade._query_has_gxw2_skill_concept(case["query"], case.get("task_type", "analysis")),
        "exact_terms": core._exact_terms(case["query"]),
        "scoped_concepts": query_skill_concepts(case["query"], case.get("task_type", "analysis")),
        "fts_expression": core._fts_expression(case["query"]),
        "entity_refs": describe_refs(entity_refs), "fts_refs": describe_refs(fts_refs),
        "dense_refs": describe_refs(dense_refs, dense=True),
        "scored_candidates": [brief(item) for item in captured.get("scored", [])],
        "core_top40": [brief(item) for item in before],
        "phase2c_reranked": [brief(item) for item in after],
        "top10_actual": [brief(item) for item in actual],
        "expected_chunks": expected,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "resources/knowledge/fx3u_knowledge.sqlite")
    parser.add_argument("--benchmark", type=Path, default=ROOT / "benchmarks/gxw2_skill_rag_benchmark.jsonl")
    parser.add_argument("--failure-report", type=Path)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--candidate-budget", type=int, default=sys.maxsize)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selected = set(args.case_id)
    if args.failure_report:
        selected.update(row["id"] for row in json.loads(args.failure_report.read_text(encoding="utf-8"))["failures"])
    cases = [json.loads(line) for line in args.benchmark.read_text(encoding="utf-8").splitlines() if line.strip()]
    traces = [trace_case(case, args.database.resolve(), args.candidate_budget)
              for case in cases if not selected or case["id"] in selected]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"cases": traces}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for trace in traces:
        correct = [item for item in trace["expected_chunks"] if item["rank_before"]]
        print(json.dumps({"id": trace["id"], "correct_in_top40": [
            {key: item[key] for key in ("id", "section", "rank_before", "rank_after")}
            for item in correct]}, ensure_ascii=False))
    core._close_thread_connection()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
