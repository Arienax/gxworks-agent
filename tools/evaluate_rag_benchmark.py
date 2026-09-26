#!/usr/bin/env python3
"""Evaluate the runtime FX3U hybrid retriever against the JSONL benchmark."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=root / "resources" / "knowledge" / "fx3u_knowledge.sqlite",
    )
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=root / "benchmarks" / "fx3u_rag_benchmark.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "benchmarks" / "fx3u_rag_benchmark_report.json",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=root / "resources" / "knowledge" / "manifest.json",
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--char-budget", type=int, default=50000)
    parser.add_argument("--fail-under-recall-10", type=float, default=0.75)
    return parser.parse_args()


def normalize(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def result_matches(case: dict[str, Any], result: dict[str, Any]) -> bool:
    haystack = normalize(
        " ".join(
            str(result.get(key, "") or "")
            for key in (
                "text",
                "section",
                "matched_entity",
                "instruction_opcode",
                "manual_id",
                "manual_number",
            )
        )
    )
    entities = [normalize(value) for value in case.get("expected_entities", []) if normalize(value)]
    chunk_types = {
        normalize(value) for value in case.get("expected_chunk_types", []) if normalize(value)
    }
    manuals = {
        normalize(value) for value in case.get("expected_manual_ids", []) if normalize(value)
    }
    case_ids = [normalize(value) for value in case.get("expected_case_ids", []) if normalize(value)]
    if entities and not any(entity in haystack for entity in entities):
        return False
    if chunk_types and normalize(result.get("chunk_type")) not in chunk_types:
        return False
    if manuals and normalize(result.get("manual_id")) not in manuals:
        return False
    if case_ids and not any(f"case_id: {case_id}" in haystack for case_id in case_ids):
        return False
    return True


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return float(ordered[index])


def _is_release_benchmark(benchmark: Path, root: Path) -> bool:
    """Only the canonical benchmark is allowed to update release metadata."""

    canonical = (root / "benchmarks" / "fx3u_rag_benchmark.jsonl").resolve()
    return benchmark.expanduser().resolve() == canonical


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    database = args.database.expanduser().resolve()
    benchmark = args.benchmark.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not database.is_file() or not benchmark.is_file():
        raise SystemExit("database and benchmark must exist")
    sys.path.insert(0, str(root / "src"))
    import knowledge.retriever as retriever
    import knowledge.core as core
    from unittest.mock import patch

    retriever._retrieve_cached.cache_clear()
    retriever._close_thread_connection()

    cases = [
        json.loads(line)
        for line in benchmark.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    positive_ranks: list[int | None] = []
    negative_outcomes: list[bool] = []
    latencies_ms: list[float] = []
    details = []
    category_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with patch.object(core, "_index_path", lambda: database):
        for case in cases:
            started = time.perf_counter()
            results = retriever.retrieve_knowledge(
                case["query"],
                plc_model=case.get("plc_model", "FX3U"),
                task_type=case.get("task_type", "analysis"),
                top_k=int(args.top_k),
                char_budget=int(args.char_budget),
            )
            latency_ms = (time.perf_counter() - started) * 1000.0
            latencies_ms.append(latency_ms)
            if case.get("negative"):
                passed = len(results) == 0
                rank = None
                negative_outcomes.append(passed)
            else:
                rank = next(
                    (
                        index
                        for index, result in enumerate(results, start=1)
                        if result_matches(case, result)
                    ),
                    None,
                )
                passed = rank is not None
                positive_ranks.append(rank)
            row = {
                "id": case["id"],
                "category": case["category"],
                "negative": bool(case.get("negative")),
                "passed": passed,
                "rank": rank,
                "latency_ms": round(latency_ms, 3),
                "result_count": len(results),
                "top_results": [
                    {
                        "id": result.get("id"),
                        "manual_id": result.get("manual_id"),
                        "chunk_type": result.get("chunk_type"),
                        "instruction_opcode": result.get("instruction_opcode"),
                        "match_type": result.get("match_type"),
                        "matched_entity": result.get("matched_entity"),
                        "score": result.get("score"),
                    }
                    for result in results[:3]
                ],
            }
            details.append(row)
            category_rows[str(case["category"])].append(row)

    positives = len(positive_ranks)
    recalls = {
        f"recall_at_{cutoff}": round(
            sum(rank is not None and rank <= cutoff for rank in positive_ranks) / positives,
            4,
        )
        if positives
        else 0.0
        for cutoff in (1, 5, 10)
    }
    mrr = (
        sum(1.0 / rank for rank in positive_ranks if rank is not None) / positives
        if positives
        else 0.0
    )
    negative_accuracy = (
        sum(negative_outcomes) / len(negative_outcomes) if negative_outcomes else 0.0
    )
    categories = {}
    for category, rows in sorted(category_rows.items()):
        category_positive = [row for row in rows if not row["negative"]]
        category_negative = [row for row in rows if row["negative"]]
        categories[category] = {
            "cases": len(rows),
            "passed": sum(row["passed"] for row in rows),
            "pass_rate": round(sum(row["passed"] for row in rows) / len(rows), 4),
            "recall_at_10": round(
                sum(row["rank"] is not None and row["rank"] <= 10 for row in category_positive)
                / len(category_positive),
                4,
            )
            if category_positive
            else None,
            "negative_accuracy": round(
                sum(row["passed"] for row in category_negative) / len(category_negative),
                4,
            )
            if category_negative
            else None,
        }

    report = {
        "schema_version": 1,
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": str(database),
        "benchmark": str(benchmark),
        "cases": len(cases),
        "positive_cases": positives,
        "negative_cases": len(negative_outcomes),
        **recalls,
        "mrr": round(mrr, 4),
        "negative_accuracy": round(negative_accuracy, 4),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies_ms), 3) if latencies_ms else 0.0,
            "p50": round(percentile(latencies_ms, 0.50), 3),
            "p95": round(percentile(latencies_ms, 0.95), 3),
            "max": round(max(latencies_ms), 3) if latencies_ms else 0.0,
        },
        "categories": categories,
        "failures": [row for row in details if not row["passed"]],
        "passed_threshold": recalls["recall_at_10"] >= float(args.fail_under_recall_10),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # A/B and feature-specific benchmark runs are diagnostic artifacts. They
    # must not overwrite the canonical release benchmark stored in manifest.
    manifest_path = args.manifest.expanduser().resolve()
    if _is_release_benchmark(benchmark, root) and manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.setdefault("retrieval", {})["benchmark"] = {
            "cases": report["cases"],
            "positive_cases": report["positive_cases"],
            "negative_cases": report["negative_cases"],
            "recall_at_1": report["recall_at_1"],
            "recall_at_5": report["recall_at_5"],
            "recall_at_10": report["recall_at_10"],
            "mrr": report["mrr"],
            "negative_accuracy": report["negative_accuracy"],
            "latency_ms": report["latency_ms"],
            "evaluated_at_utc": report["evaluated_at_utc"],
            "report": output.name,
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "cases",
                    "recall_at_1",
                    "recall_at_5",
                    "recall_at_10",
                    "mrr",
                    "negative_accuracy",
                    "latency_ms",
                    "passed_threshold",
                )
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["passed_threshold"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
