"""Paired Agent-B experiments through the real application path.

Without --live this prints a plan and never loads credentials or calls a model.
No experiment changes effort, provider settings, retry policy or token ceilings.
Oracle evidence must be supplied and reviewed by the operator, not another LLM.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import nullcontext
import hashlib
import importlib
import json
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
ARMS = ("legacy_retrieval", "automatic", "oracle")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def load_cases(path):
    cases = [json.loads(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    ids = set()
    for case in cases:
        identity = case.get("case_id")
        if not isinstance(identity, str) or not identity or identity in ids or not isinstance(case.get("confirmed_spec"), dict):
            raise ValueError("Each case needs a unique case_id and a confirmed_spec object")
        ids.add(identity)
    if not cases:
        raise ValueError("The case file is empty")
    return cases


def schedule(cases, arms, repeats, seed):
    if repeats < 1 or not arms or any(arm not in ARMS for arm in arms):
        raise ValueError("Invalid repeat count or experiment arm")
    tasks = [(case, arm, repeat) for repeat in range(repeats) for case in cases for arm in dict.fromkeys(arms)]
    random.Random(seed).shuffle(tasks)
    return tasks


class ObservedProvider:
    """Observe before JSON framing, forwarding every event and request unchanged."""
    def __init__(self, provider):
        self.provider = provider
        self.attempts = []

    def __getattr__(self, name):
        return getattr(self.provider, name)

    def stream(self, request):
        from application.generation_agent import _FirstJSONObjectStream
        from model_runtime.provider import TextDelta, ReasoningDelta, Usage
        attempt = {"raw_content": "", "reasoning": "", "usage": None,
                   "first_content_ms": None, "json_complete_ms": None, "transport_ms": None,
                   "transport_completed": False}
        self.attempts.append(attempt)
        frame, started = _FirstJSONObjectStream(), time.perf_counter()
        try:
            for event in self.provider.stream(request):
                elapsed = (time.perf_counter() - started) * 1000
                if isinstance(event, TextDelta):
                    attempt["raw_content"] += event.text
                    if event.text and attempt["first_content_ms"] is None:
                        attempt["first_content_ms"] = elapsed
                    if attempt["json_complete_ms"] is None and frame.feed(event.text)[1]:
                        attempt["json_complete_ms"] = elapsed
                elif isinstance(event, ReasoningDelta):
                    attempt["reasoning"] += event.text
                elif isinstance(event, Usage):
                    attempt["usage"] = {name: copy.deepcopy(getattr(event, name, None)) for name in
                                        ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens", "raw_usage")}
                yield event
            attempt["transport_completed"] = True
        finally:
            attempt["transport_ms"] = (time.perf_counter() - started) * 1000


def run_case(case, arm, *, provider, model=None, effort=None, evaluator=None):
    from application import generation_agent as agent
    from application.model_api import provider_scope
    from application.confirmed_generation_context import project_confirmed_specification
    from knowledge.evidence import KnowledgeContext, KnowledgeQuery, context_manifest, evidence_record
    from knowledge.core import _format_result_block
    from plc.ir import build_plc_ir, validate_plc_ir
    from plc.validation import validate_ladder_candidate_structure
    from plc.specification.checks import check_direct_self_hold

    original_builder = agent._build_knowledge_context
    observed = ObservedProvider(provider)
    record = {"case_id": case["case_id"], "arm": arm, "spec_sha256": digest(case["confirmed_spec"]),
              "requested_model": model, "requested_effort": effort, "actual_requests": [],
              "evidence": [], "generation_status": "not_started", "structural_valid": None,
              "behavior": {"status": "not_covered"}, "attempts": observed.attempts}
    previous_sink = getattr(provider, "observation_sink", None)

    def capture_request(params, events, error=None):
        record["actual_requests"].append(copy.deepcopy(params))
        if previous_sink is not None:
            previous_sink(params, events, error)

    def builder(query, **kwargs):
        started = time.perf_counter()
        if arm == "oracle":
            rows = case.get("oracle_evidence")
            if not case.get("oracle_reviewed") or not isinstance(rows, list) or not rows:
                raise ValueError("Oracle arm requires operator-reviewed oracle_evidence")
            for row in rows:
                if not all(isinstance(row.get(key), str) and row[key] for key in ("id", "source", "text")):
                    raise ValueError("Oracle records require id, source and original text")
            text = "\n\n".join(_format_result_block(row) for row in rows)
            context = KnowledgeContext(text, {"status": "operator_supplied_oracle",
                                               "records": [evidence_record(row) for row in rows]})
        else:
            if arm == "legacy_retrieval":
                metadata = copy.deepcopy(getattr(query, "metadata", {}))
                metadata.pop("instruction_fact_mode", None)
                metadata.pop("instruction_fact_targets", None)
                query = KnowledgeQuery(str(query), precompiled=getattr(query, "precompiled", False), metadata=metadata)
            context = original_builder(query, **kwargs)
        record["evidence"].append({"query": str(query), "text": str(context),
                                   "manifest": context_manifest(context),
                                   "elapsed_ms": (time.perf_counter()-started)*1000})
        return context

    # The CLI is serial. Both patches are restored even on transport/validation
    # failure; nothing persists in the user's model profile or application state.
    sink_scope = patch.object(provider, "observation_sink", capture_request) if hasattr(provider, "observation_sink") else nullcontext()
    started = time.perf_counter()
    try:
        with sink_scope, patch.object(agent, "_build_knowledge_context", builder), provider_scope(observed, model_name=model):
            result = agent.generate_confirmed_ladder(case["confirmed_spec"], case.get("plc_model", "FX3U"),
                model_name=model, effort=effort, on_context=lambda handoff: record.update(handoff=handoff))
        record["generation_status"] = "completed"
        record["ladder"] = result["ladder"]
        plc_model = case.get("plc_model", "FX3U")
        validate_ladder_candidate_structure(result["ladder"], plc_model=plc_model)
        validate_plc_ir(build_plc_ir(result["ladder"], plc_model=plc_model), validate_ladder=False)
        record["structural_valid"] = True
        record["behavior"] = (evaluator(case, result) if evaluator else
            check_direct_self_hold(result["ladder"], project_confirmed_specification(case["confirmed_spec"])))
        if not isinstance(record["behavior"], dict) or record["behavior"].get("status") not in {"verified", "failed", "not_covered"}:
            raise ValueError("Evaluator must report verified, failed or not_covered")
    except Exception as error:
        record["generation_status"] = "failed"
        record["error"] = {"type": type(error).__name__, "code": getattr(error, "code", None)}
        # No raw SDK exception: it may contain request credentials.
    finally:
        record["end_to_end_ms"] = (time.perf_counter()-started)*1000
        record["model_calls"] = len(observed.attempts)
    return record


def summarize(records):
    """Describe all runs including failures, never infer a correctness win."""
    groups = {}
    for record in records:
        key = record["arm"]
        groups.setdefault(key, []).append(record)
    result = {}
    for arm, rows in groups.items():
        durations = sorted(row["end_to_end_ms"] for row in rows)
        reasoning = []
        for row in rows:
            usages = [attempt.get("usage") or {} for attempt in row["attempts"]]
            values = [usage.get("reasoning_tokens") for usage in usages]
            if values and all(type(value) is int for value in values):
                reasoning.append(sum(values))
        result[arm] = {"runs": len(rows), "completed": sum(r["generation_status"] == "completed" for r in rows),
                       "behavior_verified": sum(r["behavior"].get("status") == "verified" for r in rows),
                       "median_end_to_end_ms": statistics.median(durations),
                       "p95_end_to_end_ms": durations[max(0, (95*len(durations)+99)//100-1)],
                       "reasoning_usage_known_runs": len(reasoning),
                       "median_reasoning_tokens": statistics.median(reasoning) if reasoning else None}
    return {"groups": result, "performance_acceptance": "not_established",
            "note": "Inspect matched case/repeat pairs, actual settings and behavioral coverage before drawing conclusions."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="JSONL: case_id, confirmed_spec, optional plc_model/oracle_evidence")
    parser.add_argument("--arms", default="legacy_retrieval,automatic")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", default=None, help="Omit to preserve the saved model settings")
    parser.add_argument("--evaluator", help="Optional module:function(case, result) behavioral evaluator")
    parser.add_argument("--output", type=Path, help="Private JSONL result path; required with --live")
    parser.add_argument("--live", action="store_true", help="Authorize paid calls using the saved active profile")
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.cases)
        tasks = schedule(cases, args.arms.split(","), args.repeat, args.seed)
        if "oracle" in args.arms.split(",") and any(not c.get("oracle_reviewed") or not c.get("oracle_evidence") for c in cases):
            raise ValueError("Supply reviewed oracle_evidence for every oracle case")
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if not args.live:
        print(json.dumps({"live": False, "scheduled_runs": len(tasks), "case_ids": [c["case_id"] for c in cases],
                          "arms": args.arms.split(","), "effort": args.effort or "saved_profile_unchanged"}, ensure_ascii=False, indent=2))
        return 0
    if args.output is None or args.output.exists():
        parser.error("--live requires a new --output path; existing results are never overwritten")
    from model_runtime.provider import get_active_provider
    from shared.tracing import sanitize
    provider = get_active_provider()
    evaluator = None
    if args.evaluator:
        module, name = args.evaluator.split(":", 1)
        evaluator = getattr(importlib.import_module(module), name)
    def git(*command):
        completed = subprocess.run(["git", "-C", str(ROOT), *command], capture_output=True, text=True, check=False)
        return completed.stdout.strip() if completed.returncode == 0 else None
    url = urlsplit(str(provider.profile.get("baseUrl") or ""))
    endpoint = urlunsplit((url.scheme, url.netloc.rsplit("@", 1)[-1], url.path, "", ""))
    identity = {"git_commit": git("rev-parse", "HEAD"), "git_status": git("status", "--porcelain"),
                "endpoint": endpoint, "profile_model": provider.profile.get("model"), "seed": args.seed}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    records = []
    # Private output permissions where the platform supports them.
    import os
    descriptor = os.open(args.output, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        for case, arm, repeat in tasks:
            record = run_case(case, arm, provider=provider, model=args.model, effort=args.effort, evaluator=evaluator)
            record.update(identity, repeat=repeat)
            stream.write(json.dumps(sanitize(record), ensure_ascii=False, allow_nan=False)+"\n")
            stream.flush()
            records.append(record)
            print(f"{case['case_id']} / {arm} / {repeat}: {record['generation_status']}", file=sys.stderr)
    print(json.dumps(summarize(records), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
