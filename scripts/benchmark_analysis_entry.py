"""Interleaved Direct entry measurements over isolated loopback HTTP services.

The baseline runs the actual 0592933 source in a separate process. Both sources
use the same official preset and credential target; the active config is never
loaded through a migrating reader. No frozen confirmed specification is used.
"""
from __future__ import annotations

import argparse
import copy
import json
import itertools
import os
from pathlib import Path
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "0592933"
ARMS = ("baseline", "repaired")


def write_record(path, value):
    from shared.tracing import sanitize
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitize(value), ensure_ascii=False, indent=2), encoding="utf-8")


def isolated_profile(profile_id, model):
    from storage.config import DEFAULT_MODEL_PROFILES, get_config_path, get_model_profile, _normalize_profile
    config = json.loads(Path(get_config_path()).read_text(encoding="utf-8-sig"))
    credential = get_model_profile(config, profile_id)
    if credential["baseUrl"].rstrip("/") != "https://api.deepseek.com":
        raise ValueError("Credential profile must use the official DeepSeek endpoint")
    preset = copy.deepcopy(next(p for p in DEFAULT_MODEL_PROFILES if p["id"] == "deepseek-v4-flash"))
    preset.update(model=model, credentialTarget=credential["credentialTarget"])
    profile = _normalize_profile(preset)
    return {"language": "zh-CN", "activeModelProfileId": profile["id"], "modelProfiles": [profile]}


def freeze_sources(directory, *, baseline=BASELINE):
    """Freeze old code literally; materialize unchanged LFS resources locally."""
    archive = directory / "baseline-source.zip"
    subprocess.run(["git", "archive", baseline, "src", "resources", "--format=zip", "--output", str(archive)],
                   cwd=ROOT, check=True)
    old = directory / "sources" / "baseline"
    old.mkdir(parents=True)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (old / member.filename).resolve()
            if old.resolve() not in target.parents:
                raise ValueError("Unexpected source archive member")
        bundle.extractall(old)
    for path in (old / "resources").rglob("*"):
        if path.is_file() and path.read_bytes()[:42].startswith(b"version https://git-lfs.github.com/spec/v1"):
            shutil.copyfile(ROOT / path.relative_to(old), path)
    new = directory / "sources" / "repaired"
    shutil.copytree(old, new)
    shutil.copytree(ROOT / "src", new / "src", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # The observer is shared instrumentation, imported against each frozen src.
    for source in (old, new):
        (source / "scripts").mkdir()
        shutil.copyfile(ROOT / "scripts" / "benchmark_agent_b.py", source / "scripts" / "benchmark_agent_b.py")
    return {"baseline": old, "repaired": new}


def describe_metrics(record):
    attempts = record.get("attempts", [])
    transports = [a.get("transport_ms") for a in attempts]
    total = next((e["elapsed_ms"] for e in reversed(record.get("diagnostics", []))
                  if e.get("event") == "job_finished" and e.get("elapsed_ms") is not None), record["wall_ms"])
    usage = [a.get("usage") or {} for a in attempts]
    return {"first_call_ms": transports[0] if transports else None,
            "protocol_repair_ms": sum(x for x in transports[1:] if x is not None),
            "local_and_gaps_ms": total - sum(x for x in transports if x is not None),
            "total_ms": total, "http_wall_ms": record["wall_ms"],
            "request_count": len(record.get("actual_requests", [])),
            "models": [e.get("model") for e in record.get("diagnostics", [])
                       if e.get("event") == "provider_result" and e.get("model")],
            "reasoning_tokens": [u.get("reasoning_tokens") for u in usage],
            "input_tokens": [u.get("input_tokens") for u in usage],
            "output_tokens": [u.get("output_tokens") for u in usage],
            "prompt_characters": [sum(len(m.get("content", "")) for m in r.get("messages", [])
                                       if m.get("role") == "system") for r in record.get("actual_requests", [])],
            "transport_observations_complete": len(attempts) == len(record.get("actual_requests", []))}


def fill_entry_answers(draft, analysis=None):
    """Answer actual questions using Core identities; never modify an approach."""
    from plc.specification.bindings import binding_hint, _question_is_address, confirmed_input_levels
    spec = copy.deepcopy(draft)
    addresses = {"start": "x1", "stop": "x0", "output": "y0"}
    # Operator answers can use the explicit roles in the received questions,
    # even if the old draft projection dropped malformed/partial metadata.
    roles = {q.get("id"): (q.get("io_binding") or {}).get("role", q.get("role"))
             for q in (analysis or {}).get("missing_info", []) if isinstance(q, dict)}
    def role(parameter):
        return (parameter.get("io_binding") or {}).get("role") or roles.get(parameter.get("id"))
    for p in spec.get("parameters", []):
        hint = binding_hint(p) or {}
        purpose = hint.get("role") or role(p)
        address = addresses.get(purpose)
        if not address:
            raise ValueError("unsupported_confirmation_question:" + str(p.get("id")))
        options = p.get("options") or []
        level_choice = next((o for o in options if confirmed_input_levels(o).get("active_level") == 1), None)
        asks_address = _question_is_address(p.get("name")) or purpose == "output" or not level_choice
        if level_choice and not asks_address:
            value = level_choice
        elif asks_address:
            # A combined address/polarity question receives both facts. Split
            # questions receive separate answers, without the old workaround.
            split_level = any(q is not p and role(q) == purpose and any(confirmed_input_levels(o)
                              for o in q.get("options", [])) for q in spec.get("parameters", []))
            value = address + "，常开" if purpose != "output" and (level_choice or not split_level) else address
        else:
            raise ValueError("unsupported_confirmation_question:" + str(p.get("id")))
        p.update(value=value, source="user")
    return spec


def check_entry_program(program):
    """Independent latch oracle over all four-scan input sequences and states."""
    from plc.ir import ir_to_ladder
    from simulator.bounded import ScanMachine
    ladder = ir_to_ladder(program)
    failures, count = [], 0
    for previous in (False, True):
        for frames in itertools.product(itertools.product((False, True), repeat=2), repeat=4):
            machine = ScanMachine(ladder, plc_model="FX3U", initial={"X1": False, "X0": False, "Y0": previous},
                                  execution_context={"program_type": "scan"})
            expected = previous
            for tick, (start, stop) in enumerate(frames):
                expected = (start or expected) and not stop
                machine.scan({"inputs": {"X1": start, "X0": stop}, "timestamp_ms": tick*10}, first_scan=tick == 0)
                if machine.memory.get("Y0") != expected or machine.gaps:
                    failures.append({"previous": previous, "frames": frames, "tick": tick, "gaps": machine.gaps})
                    break
            count += 1
    return {"traces": count, "scans_per_trace": 4, "passed": not failures, "failures": failures,
            "oracle": "next_Y0=(X1 or previous_Y0) and not X0", "native_execution": False}


def run_http_path(directory, provider, *, analysis_content=None, generation_required=True):
    import httpx
    import uvicorn
    from unittest.mock import patch
    from scripts.benchmark_agent_b import ObservedProvider
    from application.workbench import WorkbenchService
    from integrations.web.app import create_app
    import shared.diagnostics as diagnostics

    class ReplayAnalysis:
        def __getattr__(self, name):
            return getattr(provider, name)

        def stream(self, request):
            if request.response_contract.name == "analysis":
                from model_runtime.provider import TextDelta
                yield TextDelta(analysis_content)
            else:
                yield from provider.stream(request)

    observed = ObservedProvider(ReplayAnalysis() if analysis_content is not None else provider)
    service = WorkbenchService(directory / "workspace", directory / "state",
                               model_factory=lambda: (observed, provider.profile))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    origin = "http://127.0.0.1:" + str(listener.getsockname()[1])
    operator = uuid.uuid4().hex
    app = create_app(service.store.base_dir, state_dir=service.state_dir, service=service,
                     origin=origin, operator_token=operator)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    current = None
    emit = diagnostics.emit

    def observe(event, **fields):
        if current is not None:
            current["diagnostics"].append({"event": event, **copy.deepcopy(fields)})
        return emit(event, **fields)

    def capture(params, events, error=None):
        if current is not None:
            current["actual_requests"].append(copy.deepcopy(params))
            if error:
                current.setdefault("transport_errors", []).append({"type": type(error).__name__})

    provider.observation_sink = capture
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    result = {"started_at": datetime.now(timezone.utc).isoformat(), "confirmation_success": False,
              "analysis_mode": "direct", "plc_model": "FX3U", "request": "起保停",
              "analysis_replayed": analysis_content is not None}
    with patch.object(diagnostics, "emit", observe), httpx.Client(base_url=origin, timeout=30, trust_env=False) as client:
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if time.monotonic() >= deadline:
                    raise TimeoutError("isolated_http_startup")
                time.sleep(0.05)
            session = client.post("/api/session", headers={"Origin": origin}, json={"token": operator})
            session.raise_for_status()
            headers = {"Origin": origin, "X-CSRF-Token": session.json()["csrf"]}
            created = client.post("/api/projects", headers=headers,
                                  json={"name": "analysis entry benchmark", "plc_model": "FX3U"})
            created.raise_for_status()
            pid = created.json()["id"]

            def job(kind, text):
                nonlocal current
                row = {"actual_requests": [], "attempts": [], "diagnostics": []}
                observed.attempts = row["attempts"]
                current = row
                started = time.perf_counter()
                try:
                    response = client.post("/api/jobs", headers=headers, json={"kind": kind, "project_id": pid,
                        "request_id": "entry_" + uuid.uuid4().hex, "text": text, "response_language": "zh-CN",
                        **({"analysis_mode": "direct"} if kind == "analysis" else {})})
                    response.raise_for_status()
                    jid = response.json()["id"]
                    timeout = time.monotonic() + 600
                    while True:
                        row["job"] = client.get("/api/jobs/" + jid).json()
                        if row["job"]["status"] not in {"queued", "running", "cancelling"}:
                            break
                        if time.monotonic() >= timeout:
                            raise TimeoutError("isolated_http_job")
                        time.sleep(0.1)
                    if row["job"]["status"] == "completed":
                        output = client.get("/api/jobs/" + jid + "/output")
                        output.raise_for_status()
                        row["output"] = output.json()
                    row["events"] = service.jobs.events(jid)
                finally:
                    row["wall_ms"] = (time.perf_counter() - started) * 1000
                    current = None
                row["metrics"] = describe_metrics(row)
                row["observation_kind"] = "cached_response_replay" if kind == "analysis" and analysis_content is not None else "live_http_job"
                write_record(directory / (kind + ".json"), row)
                print(json.dumps({"phase": kind, "status": row["job"]["status"], **row["metrics"]}, ensure_ascii=False), flush=True)
                return row

            analysis = job("analysis", "起保停")
            result["analysis"] = analysis["metrics"]
            result["analysis_status"] = analysis["job"]["status"]
            if "output" not in analysis:
                result["failure"] = "analysis_failed"
                return result
            try:
                answered = fill_entry_answers(analysis["output"]["spec_draft"], analysis["output"].get("analysis"))
            except ValueError as error:
                result["failure"] = str(error)
                return result
            confirmed = client.put(f"/api/projects/{pid}/spec", headers=headers,
                                   json={"spec": answered, "expected_hash": None})
            confirmed.raise_for_status()
            confirmation = confirmed.json()
            write_record(directory / "confirmation.json", {"submitted": answered, "response": confirmation})
            result["confirmation_success"] = bool(confirmation.get("valid"))
            result["confirmation_errors"] = confirmation.get("issues", {}).get("errors", [])
            if not result["confirmation_success"]:
                result["failure"] = "confirmation_rejected"
                return result
            saved = client.get(f"/api/projects/{pid}").json()
            write_record(directory / "saved.json", saved)
            result["save_readback_matches"] = saved.get("confirmed_spec") == confirmation["spec"]
            from plc.specification.conditions import generation_input_conditions
            facts = generation_input_conditions(confirmation["spec"].get("io_bindings"))
            result["input_predicates"] = facts
            stop = next((p for p in facts["level_predicates"] if p.get("role") == "stop"), {})
            result["stop_predicates_correct"] = stop.get("active_when") == "NO X0" and stop.get("run_permit_when") == "NC X0"
            if not generation_required:
                return result
            generation = job("generation", "按确认规格生成")
            result["generation"] = generation["metrics"]
            result["generation_status"] = generation.get("output", {}).get("status", generation["job"]["status"])
            vid = generation.get("output", {}).get("version_id")
            if vid:
                program = service.projects.program(pid, vid)
                write_record(directory / "program.json", program)
                result["program_check"] = check_entry_program(program)
                for name in generation["output"].get("generation", {}).get("artifacts", {}):
                    path = service.projects.artifact(pid, vid, name)
                    destination = directory / "artifacts" / path.name
                    destination.parent.mkdir(exist_ok=True)
                    shutil.copyfile(path, destination)
            return result
        finally:
            server.should_exit = True
            thread.join(timeout=30)
            listener.close()


def worker(source, directory, config_path, *, replay=None, skip_generation=False):
    # No product modules are imported before selecting the immutable source.
    sys.path[:0] = [str(source / "src"), str(source)]
    os.environ["PLC_AI_CONFIG_PATH"] = str(config_path)
    from storage.config import get_model_profile, get_api_key
    from model_runtime.provider import OpenAICompatibleProvider
    config = json.loads(config_path.read_text(encoding="utf-8"))
    profile = get_model_profile(config)
    key = get_api_key(config)
    if not key:
        raise ValueError("official_credential_unavailable")
    provider = OpenAICompatibleProvider(profile, key)
    content = json.loads(replay.read_text(encoding="utf-8"))["attempts"][-1]["raw_content"] if replay else None
    result = run_http_path(directory, provider, analysis_content=content, generation_required=not skip_generation)
    write_record(directory / "result.json", result)


def summary(directory):
    groups = {}
    for arm in ARMS:
        rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((directory / "runs").glob(arm + "-*/result.json"))]
        metrics = {}
        for name in ("first_call_ms", "protocol_repair_ms", "local_and_gaps_ms", "total_ms", "request_count"):
            values = [r["analysis"][name] for r in rows if r.get("analysis", {}).get(name) is not None]
            metrics[name] = {"count": len(values), "median": statistics.median(values) if values else None,
                             "min": min(values) if values else None, "max": max(values) if values else None}
        groups[arm] = {"runs": rows, "metrics": metrics,
                       "confirmation_successes": sum(r.get("confirmation_success", False) for r in rows),
                       "generation_saved": sum(r.get("generation_status") == "saved" for r in rows)}
        followups = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((directory / "confirmation-replays").glob(arm + "-*/result.json"))]
        groups[arm]["confirmation_replays"] = followups
        groups[arm]["replayed_confirmation_successes"] = sum(r.get("confirmation_success", False) for r in followups)
    return {"baseline": BASELINE, "groups": groups, "scope": "Direct FX3U entry over loopback HTTP; native execution not measured"}


def export_evidence(directory, destination):
    """Export observed material and source witnesses, excluding runtime config."""
    from shared.tracing import sanitize
    sources = ("application/analysis_context.py", "application/analysis_results.py", "application/model_api.py",
               "application/prompts.py", "plc/specification/bindings.py", "plc/specification/confirmed.py")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in ("preflight.json", "run_profile.json", "summary.json", "report_summary.json", "program_checks.json"):
            path = directory / name
            if path.exists():
                bundle.writestr(name, json.dumps(sanitize(json.loads(path.read_text(encoding="utf-8"))), ensure_ascii=False, indent=2))
        for phase in ("runs", "confirmation-replays"):
            for run in sorted((directory / phase).glob("*")):
                for path in sorted(run.glob("*.json")):
                    if path.name == "isolated-config.json":
                        continue
                    value = sanitize(json.loads(path.read_text(encoding="utf-8")))
                    bundle.writestr(path.relative_to(directory).as_posix(), json.dumps(value, ensure_ascii=False, indent=2))
                for path in sorted((run / "artifacts").glob("*")):
                    if path.is_file():
                        bundle.write(path, path.relative_to(directory).as_posix())
        for stage in ("baseline", "repaired", "repaired-confirmation"):
            for name in sources:
                path = directory / "sources" / stage / "src" / name
                if path.exists():
                    bundle.write(path, f"sources/{stage}/{name}")
        for name in sources:
            bundle.write(ROOT / "src" / name, "sources/final/" + name)
        bundle.write(Path(__file__).resolve(), "replay/benchmark_analysis_entry.py")
        bundle.write(ROOT / "scripts" / "benchmark_user_path.py", "replay/benchmark_user_path.py")
        bundle.writestr("README.txt", "Direct FX3U analysis-entry evidence.\nBaseline: 0592933.\n"
                        "runs/: original live observations, including harness failures.\n"
                        "confirmation-replays/: literal accepted analysis replay followed by HTTP confirmation and live generation.\n"
                        "Replay analysis timings are offline and excluded from live comparisons.\n"
                        "No API keys, runtime configs, operator sessions, native writes or fresh analysis calls in replay.\n")


def entry_main(args, directory):
    repeats = args.repeats if args.repeats is not None else 5
    if repeats < 1 or args.phase not in {"analysis", "preflight", "summary", "confirm"}:
        raise ValueError("Analysis entry uses analysis, preflight, confirm or summary and positive repeats")
    if args.phase == "summary":
        write_record(directory / "summary.json", summary(directory))
        if args.entry_export:
            export_evidence(directory, args.entry_export.resolve())
        return 0
    if not args.live:
        print(json.dumps({"experiment": "analysis-entry", "analysis_mode": "direct", "baseline": BASELINE,
            "requested_model": args.entry_model, "repeats_per_arm": repeats, "network_calls": 0}, ensure_ascii=False))
        return 0
    if args.phase == "confirm":
        # Preserve the first online run, including unsupported harness questions.
        # Reuse its literal accepted response; no new analysis request is sent.
        source = directory / "sources" / "repaired-confirmation"
        shutil.copytree(directory / "sources" / "repaired", source)
        shutil.copytree(ROOT / "src", source / "src", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for i in range(repeats):
            for arm in ARMS:
                original = directory / "runs" / f"{arm}-{i+1}"
                followup = directory / "confirmation-replays" / original.name
                followup.mkdir(parents=True)
                original_result = json.loads((original / "result.json").read_text(encoding="utf-8"))
                skip = arm == "baseline" or original_result.get("generation_status") == "saved"
                cmd = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--worker",
                       "--source", str(source if arm == "repaired" else directory / "sources" / "baseline"),
                       "--directory", str(followup), "--config", str(original / "isolated-config.json"),
                       "--replay-response", str(original / "analysis.json")]
                if skip:
                    cmd.append("--skip-generation")
                completed = subprocess.run(cmd, cwd=followup)
                if completed.returncode:
                    write_record(followup / "result.json", {"failure": "worker_failed", "exit_code": completed.returncode})
                elif skip and original_result.get("generation_status") == "saved":
                    row = json.loads((followup / "result.json").read_text(encoding="utf-8"))
                    from application.confirmed_generation_context import project_confirmed_specification
                    before = json.loads((original / "confirmation.json").read_text(encoding="utf-8"))["response"]["spec"]
                    after = json.loads((followup / "confirmation.json").read_text(encoding="utf-8"))["response"]["spec"]
                    row["generation_context_unchanged"] = project_confirmed_specification(before) == project_confirmed_specification(after)
                    row["program_check"] = check_entry_program(json.loads((original / "program.json").read_text(encoding="utf-8")))
                    row.update(generation=original_result["generation"], generation_status="saved",
                               generation_source="original_online_run")
                    write_record(followup / "result.json", row)
        write_record(directory / "summary.json", summary(directory))
        return 0
    directory.mkdir(parents=True, exist_ok=True)
    config = isolated_profile(args.profile_id, args.entry_model)
    from storage.config import get_api_key
    from model_runtime.provider import OpenAICompatibleProvider
    profile = config["modelProfiles"][0]
    key = get_api_key(config)
    if not key:
        write_record(directory / "preflight.json", {"passed": False, "reason": "official_credential_unavailable"})
        return 2
    provider = OpenAICompatibleProvider(profile, key)
    try:
        models = list(provider.list_models(timeout=20))
        flight = {"passed": args.entry_model in models, "requested_model": args.entry_model, "available_models": models}
    except Exception as error:
        flight = {"passed": False, "reason": type(error).__name__, "requested_model": args.entry_model}
    write_record(directory / "preflight.json", flight)
    print(json.dumps(flight, ensure_ascii=False), flush=True)
    if not flight["passed"]:
        return 2
    if args.phase == "preflight":
        return 0
    sources = freeze_sources(directory)
    write_record(directory / "run_profile.json", {"baseline": BASELINE, "preset": "deepseek-v4-flash",
        "requested_model": args.entry_model, "endpoint": profile["baseUrl"],
        "profile": {k: v for k, v in profile.items() if k != "credentialTarget"},
        "parameters_owner": "unchanged official preset", "repeats_per_arm": repeats,
        "order": [f"{a}-{i+1}" for i in range(repeats) for a in (ARMS if i % 2 == 0 else ARMS[::-1])]})
    for i in range(repeats):
        for arm in ARMS if i % 2 == 0 else ARMS[::-1]:
            run = directory / "runs" / f"{arm}-{i+1}"
            run.mkdir(parents=True)
            private = run / "isolated-config.json"
            private.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"starting": run.name, "requested_model": args.entry_model}, ensure_ascii=False), flush=True)
            completed = subprocess.run([sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--worker",
                "--source", str(sources[arm]), "--directory", str(run), "--config", str(private)], cwd=run)
            if completed.returncode:
                write_record(run / "result.json", {"failure": "worker_failed", "exit_code": completed.returncode})
            write_record(directory / "summary.json", summary(directory))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--replay-response", type=Path)
    parser.add_argument("--skip-generation", action="store_true")
    opts = parser.parse_args()
    worker(opts.source, opts.directory, opts.config, replay=opts.replay_response, skip_generation=opts.skip_generation)
