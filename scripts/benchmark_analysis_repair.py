"""Frozen-first-response repair pairs and fresh Direct analyses, no PLC writes.

Workers import the literal baseline or frozen current source before importing
product modules. The saved profile is copied without changing its tuning or
active selection. Replay transport and usage are excluded from live metrics.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "26b57a6"
if __name__ == "__main__":
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]


def write_record(path, value):
    from shared.tracing import sanitize
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitize(value), ensure_ascii=False, indent=2), encoding="utf-8")


def validate_measurement_profile(profile, provider):
    """Only an explicitly selected saved endpoint/model may run the test."""
    endpoint = str(profile.get("baseUrl") or "").rstrip("/")
    if provider == "official":
        valid = endpoint in {"https://api.deepseek.com", "https://api.deepseek.com/v1"} and profile.get("model") == "deepseek-flash"
    else:
        valid = (profile.get("name") == "intern-ai" and profile.get("model") == "deepseek-v4-flash-0731"
                 and endpoint == "https://discovery-api.intern-ai.org.cn/v1")
    if not valid:
        raise ValueError("saved_profile_does_not_match_selected_provider")


def frozen_witness(attachment):
    with zipfile.ZipFile(attachment) as bundle:
        rows = [json.loads(line) for line in bundle.read("transcript.jsonl").decode("utf-8-sig").splitlines()]
    first_request = next(r for r in rows if r["event"] == "model_request")
    first_response = next(r for r in rows if r["event"] == "model_response")
    literal = json.loads((ROOT / "tests/fixtures/analysis_sftl_repair.json").read_text(encoding="utf-8"))
    if first_response["content"] != literal["first_response"]:
        raise ValueError("attachment_first_response_differs_from_witness")
    return {**literal, "initial_messages": first_request["messages"],
            "transport": {k: first_request[k] for k in ("stream", "timeout", "max_retries", "options")}}


def repair_quality(raw, normalized, witness):
    original = json.loads(witness["first_response"])
    expected = [
        {"opcode": "SFTL", "operands": ["M10", "M100", "K128", "K1"]},
        {"opcode": "SFTL", "operands": ["M11", "M300", "K128", "K1"]},
        {"opcode": "SFTL", "operands": ["M12", "M500", "K128", "K1"]},
    ]
    contracts = [a.get("generation_contract", {}) for a in normalized.get("approaches", [])]
    frozen_fields = {k: raw.get(k) == v for k, v in original.items()
                     if k not in {"explicit_constraint_claims", "execution_intent_claims"}}
    rejected = {k: normalized.get(k, {}).get("rejected", []) for k in
                ("explicit_constraint_receipt", "execution_intent_receipt")}
    return {"frozen_fields": frozen_fields, "frozen_fields_passed": all(frozen_fields.values()),
            "exact_instances_preserved": bool(contracts) and all(c.get("instruction_instances") == expected for c in contracts),
            "concrete_device_bans_preserved": bool(contracts) and all(
                {"M8011", "M8012"} <= set(c.get("forbidden_devices", [])) for c in contracts),
            "output_device_aliases_bound": bool(contracts) and all(
                {"Y0", "Y2", "Y4", "Y6", "Y10"} <= set(c.get("required_devices", [])) for c in contracts),
            "rejected_claims": rejected, "all_claims_bound": not any(rejected.values()),
            "contracts": contracts,
            "scope": "protocol, grounding and frozen fields; legacy scheme semantics are not accepted as engineering-correct"}


def live_metrics(row):
    attempts, requests = row.get("attempts", []), row.get("actual_requests", [])
    return {"network_request_count": len(requests), "live_transport_ms": sum(a["transport_ms"] for a in attempts),
            "replay_transport_ms": row.get("replay_transport_ms"),
            "requested_models": [r.get("model") for r in requests],
            "returned_models": [d.get("model") for d in row.get("diagnostics", []) if d.get("event") == "provider_result"],
            "input_tokens": [(a.get("usage") or {}).get("input_tokens") for a in attempts],
            "reasoning_tokens": [(a.get("usage") or {}).get("reasoning_tokens") for a in attempts],
            "output_tokens": [(a.get("usage") or {}).get("output_tokens") for a in attempts],
            "body_characters": [len(a.get("raw_content", "")) for a in attempts],
            "system_characters": [sum(len(m.get("content", "")) for m in r.get("messages", []) if m.get("role") == "system") for r in requests],
            "observations_complete": len(attempts) == len(requests)}


def worker(source, directory, config_path, witness_path, phase, *, first_only=False):
    sys.path[:0] = [str(source / "src"), str(source)]
    os.environ["PLC_AI_CONFIG_PATH"] = str(config_path)
    from unittest.mock import patch
    from application import model_api as api
    from model_runtime.provider import OpenAICompatibleProvider, TextDelta, response_policy_scope
    from scripts.benchmark_agent_b import ObservedProvider
    from storage.config import get_api_key, get_model_profile
    import shared.diagnostics as diagnostics

    config = json.loads(config_path.read_text(encoding="utf-8"))
    profile = get_model_profile(config)
    key = get_api_key(config)
    if not key:
        write_record(directory / "result.json", {"status": "failed", "error": "saved_credential_unavailable"})
        return
    provider = OpenAICompatibleProvider(profile, key)
    observed = ObservedProvider(provider)
    witness = json.loads(witness_path.read_text(encoding="utf-8"))
    row = {"phase": phase, "first_only": first_only, "started_at": datetime.now(timezone.utc).isoformat(), "actual_requests": [],
           "diagnostics": [], "attempts": observed.attempts, "replayed_requests": 0}

    class ReplayFirst:
        def __getattr__(self, name):
            return getattr(observed, name)

        def stream(self, request):
            if not row["replayed_requests"]:
                started = time.perf_counter()
                row["replayed_requests"] += 1
                yield TextDelta(witness["first_response"])
                row["replay_transport_ms"] = (time.perf_counter() - started) * 1000
            else:
                yield from observed.stream(request)

    original_emit = diagnostics.emit
    def emit(event, **fields):
        row["diagnostics"].append({"event": event, **copy.deepcopy(fields)})
        return original_emit(event, **fields)

    def capture(params, events, error=None):
        row["actual_requests"].append(copy.deepcopy(params))
        if error:
            row.setdefault("transport_errors", []).append(type(error).__name__)

    provider.observation_sink = capture
    started = time.perf_counter()
    last_progress = ("", 0.0)
    def progress(event):
        nonlocal last_progress
        elapsed = time.perf_counter() - started
        if event.phase != last_progress[0] or elapsed - last_progress[1] >= 30:
            write_record(directory / "running.json", {"phase": event.phase, "elapsed_ms": elapsed * 1000,
                         "network_attempt": len(observed.attempts), "received_characters": event.received_characters})
            last_progress = (event.phase, elapsed)
    with patch.object(diagnostics, "emit", emit), response_policy_scope(enforce_language=False, on_progress=progress), \
            api.provider_scope(ReplayFirst() if phase == "repair" else observed):
        try:
            if phase == "repair":
                transport = witness["transport"]
                response = api._request_analysis_response(witness["initial_messages"], contract_stage="bound",
                    user_text=witness["request"], stream=transport["stream"], request_timeout=transport["timeout"],
                    max_retries=transport["max_retries"], options=transport["options"])
                raw = json.loads(response.message.content)
                normalized = api._normalize_analysis_result(raw, witness["plc_model"], witness["request"])
                row.update(raw_result=raw, normalized_result=normalized,
                           repair_receipt=getattr(response, "repair_receipt", {}),
                           quality=repair_quality(raw, normalized, witness))
            else:
                def needs_repair():
                    row["first_requires_repair"] = True
                    if first_only:
                        raise ValueError("measurement_stopped_before_repair")
                row["normalized_result"] = api.analyze_requirement_streaming(
                    witness["request"], analysis_mode="direct", on_format_repair=needs_repair)
                row["semantic_acceptance"] = "Review first analysis structures and necessary process questions independently of frozen repair acceptance"
            row["status"] = "completed" if row.get("normalized_result") else "failed"
        except Exception as error:
            row.update(status="failed", error_type=type(error).__name__)
            if hasattr(error, "violations"):
                row["violations"] = [str(v) for v in error.violations]
            if hasattr(error, "details"):
                row["error_details"] = error.details
        finally:
            row["wall_ms"] = (time.perf_counter() - started) * 1000
            row["metrics"] = live_metrics(row)
            write_record(directory / "result.json", row)
    print(json.dumps({"run": directory.name, "status": row["status"], **row["metrics"]}, ensure_ascii=False), flush=True)


def summarize(directory):
    groups = {}
    for name in ("baseline", "repaired", "fresh"):
        rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((directory / "runs").glob(name + "-*/result.json"))]
        groups[name] = {"count": len(rows), "completed": sum(r.get("status") == "completed" for r in rows),
            "runs": [{"run": p.parent.name, "status": r.get("status"), "metrics": r.get("metrics"),
                      "quality": r.get("quality"), "error_type": r.get("error_type"), "violations": r.get("violations")}
                     for p, r in zip(sorted((directory / "runs").glob(name + "-*/result.json")), rows)]}
        for key in ("live_transport_ms", "input_tokens", "reasoning_tokens", "body_characters"):
            values = []
            for row in rows:
                metric = row.get("metrics", {}).get(key)
                if isinstance(metric, list):
                    metric = sum(metric) if metric and all(v is not None for v in metric) else None
                if metric is not None:
                    values.append(metric)
            groups[name][key] = {"count": len(values), "median": statistics.median(values) if values else None,
                                 "min": min(values) if values else None, "max": max(values) if values else None}
    return {"baseline": BASELINE, "groups": groups,
            "measurement": "Repair pairs use a frozen first response; its replay time and usage are excluded. Fresh first-analysis runs are separate."}


def replay_repairs(directory):
    """Review measured patches with final Core, without altering live records."""
    from application.analysis_repair import apply_analysis_repair, plan_analysis_repair
    from application import model_api as api
    witness = json.loads((directory / "witness.json").read_text(encoding="utf-8"))
    reviews = []
    for path in sorted((directory / "runs").glob("repaired-*/result.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        row = {"run": path.parent.name, "original_status": record.get("status")}
        try:
            patch = next(a["raw_content"] for a in record.get("attempts", [])
                         if a.get("response_contract") == "analysis_repair")
            plan = plan_analysis_repair(json.loads(witness["first_response"]), witness["request"],
                                        plc_model=witness["plc_model"])
            raw, receipt = apply_analysis_repair(plan, json.loads(patch), witness["request"],
                                                 plc_model=witness["plc_model"])
            normalized = api._parse_analysis_response(json.dumps(raw, ensure_ascii=False),
                                                       witness["plc_model"], witness["request"])
            quality = repair_quality(raw, normalized, witness)
            row.update(quality=quality, repair_receipt=receipt, passed=all(quality[key] for key in (
                "frozen_fields_passed", "exact_instances_preserved", "concrete_device_bans_preserved",
                "output_device_aliases_bound", "all_claims_bound")))
        except Exception as error:
            row.update(passed=False, error_type=type(error).__name__)
        reviews.append(row)
    return {"network_calls": 0, "reviews": reviews,
            "scope": "Final-Core offline replay. Original live observations are unchanged; frozen scheme semantics are not certified."}


def export_evidence(directory, destination):
    from shared.tracing import sanitize
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        def include_record(path):
            bundle.writestr(path.relative_to(directory).as_posix(), json.dumps(
                sanitize(json.loads(path.read_text(encoding="utf-8"))), ensure_ascii=False, indent=2))
        for name in ("witness.json", "run_profile.json", "run_complete.json", "summary.json", "final_review.json", "interruption.json", "semantic_review.json", "verification.json"):
            if (directory / name).exists():
                include_record(directory / name)
        for path in sorted((directory / "runs").glob("*/result.json")):
            include_record(path)
        for path in sorted(directory.glob("*tests*.xml")):
            bundle.write(path, "verification/" + path.name)
        changed = ["application/analysis_context.py", "application/analysis_results.py", "application/analysis_repair.py", "application/model_api.py",
                   "application/prompts.py", "application/response_contracts.py", "model_runtime/provider.py",
                   "plc/execution_intent.py", "plc/validation.py", "plc/specification/explicit_constraint_claims.py", "plc/specification/explicit_constraints.py"]
        for arm in ("baseline", "repaired"):
            for name in changed:
                path = directory / "sources" / arm / "src" / name
                if path.exists():
                    bundle.write(path, "sources/" + arm + "/" + name)
        for name in changed:
            bundle.write(ROOT / "src" / name, "sources/final/" + name)
        bundle.write(Path(__file__).resolve(), "replay/benchmark_analysis_repair.py")
        bundle.write(ROOT / "scripts/benchmark_analysis_entry.py", "replay/benchmark_analysis_entry.py")
        bundle.writestr("README.txt", "FX3U frozen-first-response repair comparison and fresh Direct analyses.\n"
                        "Baseline 26b57a6; selected saved profile in run_profile.json, tuning unchanged.\n"
                        "Repair replay time is offline, excluded from live transport metrics.\n"
                        "Original failure observations are retained. No private configuration, credentials or native writes.\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile-id")
    parser.add_argument("--provider", choices=("diagnostic", "official"), default="diagnostic")
    parser.add_argument("--attachment", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--first-only", action="store_true", help="Fresh runs stop before any required repair; first request is unchanged")
    parser.add_argument("--phase", choices=("all", "repair", "fresh", "replay", "summary"), default="all")
    parser.add_argument("--export", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--witness", type=Path)
    args = parser.parse_args(argv)
    directory = args.output.resolve()
    if args.worker:
        worker(args.source, directory, args.config, args.witness, args.phase, first_only=args.first_only)
        return 0
    if args.phase in {"replay", "summary"}:
        if args.phase == "replay":
            write_record(directory / "final_review.json", replay_repairs(directory))
        write_record(directory / "summary.json", summarize(directory))
        if args.export:
            export_evidence(directory, args.export.resolve())
        return 0
    order = [(arm, i+1) for i in range(3) for arm in (("baseline", "repaired") if i % 2 == 0 else ("repaired", "baseline"))]
    jobs = order if args.phase in {"all", "repair"} else []
    if args.phase in {"all", "fresh"}:
        jobs = [*jobs, *(("fresh", i+1) for i in range(3))]
    if not args.live:
        print(json.dumps({"baseline": BASELINE, "network_calls": 0, "repair_order": order,
                          "fresh_runs": 3, "first_only": args.first_only, "provider": args.provider,
                          "scheduled_jobs": jobs, "saved_model_settings": "unchanged"}))
        return 0
    if not args.profile_id or not args.attachment:
        parser.error("--live requires the exact saved --profile-id and --attachment")
    from storage.config import get_config_path, get_model_profile
    from scripts.benchmark_analysis_entry import freeze_sources
    config_path = Path(get_config_path())
    before = config_path.read_bytes()
    saved = json.loads(before.decode("utf-8-sig"))
    profile = get_model_profile(saved, args.profile_id)
    validate_measurement_profile(profile, args.provider)
    directory.mkdir(parents=True, exist_ok=False)
    private = directory / "isolated-config.json"
    private.write_text(json.dumps({"language": "zh-CN", "activeModelProfileId": profile["id"],
                                  "modelProfiles": [profile]}, ensure_ascii=False), encoding="utf-8")
    write_record(directory / "witness.json", frozen_witness(args.attachment))
    sources = freeze_sources(directory, baseline=BASELINE)
    write_record(directory / "run_profile.json", {"baseline": BASELINE,
        "provider_selection": args.provider, "scheduled_jobs": jobs,
        "saved_profile": {k: v for k, v in profile.items() if k != "credentialTarget"},
        "repair_order": order, "fresh_runs": 3, "first_only": args.first_only, "tuning_owner": "saved profile unchanged",
        "entry": "shared _request_analysis_response for repair; analyze_requirement_streaming/direct for fresh"})
    try:
        for arm, repeat in jobs:
            run = directory / "runs" / f"{arm}-{repeat}"
            run.mkdir(parents=True)
            print(json.dumps({"starting": run.name, "model": profile["model"]}), flush=True)
            command = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--worker",
                "--phase", "fresh" if arm == "fresh" else "repair", "--output", str(run),
                "--source", str(sources["repaired" if arm == "fresh" else arm]),
                "--config", str(private), "--witness", str(directory / "witness.json")]
            if args.first_only and arm == "fresh":
                command.append("--first-only")
            process = subprocess.run(command, cwd=run)
            if process.returncode:
                write_record(run / "result.json", {"status": "failed", "error_type": "worker_failed", "exit_code": process.returncode})
            write_record(directory / "summary.json", summarize(directory))
    finally:
        if config_path.read_bytes() != before:
            raise ValueError("active_config_changed_during_measurement")
    write_record(directory / "run_complete.json", {"active_config_unchanged": True,
                 "scheduled_jobs": jobs, "completed_at": datetime.now(timezone.utc).isoformat()})
    if args.export:
        export_evidence(directory, args.export.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
