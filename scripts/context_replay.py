"""Offline replay of the real analysis/confirmation/generation boundaries.

Providers below return recorded/synthetic JSON. They never load a saved model
profile or send a model request. Haystack/SQLite and the application assemblers
are real. Passing demonstrates boundary behavior, not PLC behavioral correctness
or lower live-model latency. This CLI is serial and restores all test patches.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import ExitStack, contextmanager, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import traceback
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
FIXTURES = ROOT / "evals" / "context-replay" / "cases.json"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_cases():
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def archive_case(path):
    """Read bounded known members, not executable code or a model configuration."""
    wanted = {"job.json", "transcript.jsonl", "decision_receipt.json"}
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 64 or len({m.filename for m in members}) != len(members):
            raise ValueError("Invalid or ambiguous diagnostic archive")
        chosen = {m.filename: m for m in members if m.filename in wanted}
        if not {"job.json", "transcript.jsonl"} <= chosen.keys():
            raise ValueError("Archive lacks job/transcript")
        if any(m.file_size > 16 * 1024 * 1024 for m in chosen.values()):
            raise ValueError("Diagnostic member exceeds offline replay limit")
        job = json.loads(archive.read("job.json"))
        transcript = [json.loads(line) for line in archive.read("transcript.jsonl").decode("utf-8-sig").splitlines() if line.strip()]
    snapshot = job.get("snapshot", {})
    project = snapshot.get("project", {})
    spec = project.get("confirmed_spec")
    if not isinstance(spec, dict):
        raise ValueError("Archive has no frozen confirmed specification")
    outputs = [row.get("content") for row in transcript if row.get("event") == "model_response" and isinstance(row.get("content"), str)]
    if not outputs:
        raise ValueError("Archive has no recorded model response")
    return {"case_id": "operator_archive", "confirmed_spec": spec,
            "plc_model": project.get("plc_model", "FX3U"), "completion": outputs[-1],
            "capture_scope": "generation_only_no_analysis_reconstruction"}


@contextmanager
def offline_environment():
    """Fail closed on networking or implicit provider/credential resolution."""
    attempted = []
    def denied(*args, **kwargs):
        attempted.append("blocked_network_or_provider_access")
        raise RuntimeError("Offline replay forbids network/provider resolution")
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"HAYSTACK_TELEMETRY_ENABLED": "false"}))
        stack.enter_context(patch.object(socket, "create_connection", denied))
        stack.enter_context(patch.object(socket.socket, "connect", denied))
        stack.enter_context(patch.object(socket.socket, "connect_ex", denied))
        import application.model_api as api
        import model_runtime.provider as providers
        stack.enter_context(patch.object(api, "get_active_provider", denied))
        stack.enter_context(patch.object(providers, "get_active_provider", denied))
        stack.enter_context(patch.object(api, "load_full_config", lambda: {}))
        yield attempted


class RecordedProvider:
    """Transport fixture only. Responses pass the real application acceptance."""
    profile = {"id": "offline-replay", "adapter": "openai_compatible", "model": "offline-replay",
               "capabilities": {"structured_output": True}}

    def __init__(self, completion):
        self.completion = completion if isinstance(completion, str) else json.dumps(completion, ensure_ascii=False)
        self.requests = []

    def stream(self, request):
        from model_runtime.provider import TextDelta
        self.requests.append(request)
        if len(self.requests) != 1:
            raise AssertionError("A replay stage must consume exactly one recorded completion")
        yield TextDelta(self.completion)


def _request_spec(request):
    prompt = "\n".join(str(getattr(message, "content", "")) for message in request.messages)
    marker = "# Confirmed project specification\n"
    if marker not in prompt:
        raise ValueError("Real generation request has no specification section")
    return json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])[0], prompt


def run_case(case, *, include_content=False):
    """Run fixture or generation-only archive through the same production paths."""
    report = {"case_id": str(case.get("case_id", "fixture")), "checks": {},
              "real_model_calls": 0, "provider_fixture_calls": 0,
              "behavioral_verification": "not_performed", "live_latency_improvement": "not_measured",
              "capture_scope": case.get("capture_scope", "synthetic_analysis_and_generation")}
    before = digest(case)
    checks = report["checks"]
    attempts = []
    captured = {}
    try:
        # Suppress application's ordinary progress prints; optional content is
        # returned only in the sanitized report, never leaked via CLI stdout.
        with offline_environment() as attempts, redirect_stdout(io.StringIO()):
            from knowledge.scope import retrieval_plan
            from knowledge.retriever import build_knowledge_context
            from application import model_api as api, generation_agent as agent
            from plc.specification.confirmed import build_review_draft
            from plc.specification.provenance import seal_confirmation
            from plc.hardware_profiles import build_hardware_profile
            from plc.validation import validate_ladder_candidate_structure
            from knowledge.evidence import context_manifest
            # Resolve the real dependency explicitly. A swallowed RAG import
            # error is not evidence of passing a retrieval-scope test.
            plan = retrieval_plan(case.get("request", "FX3U"), "analysis", case.get("mode") == "design")
            checks["real_haystack_router"] = plan["engine"] == "haystack.MetadataRouter"
            analysis_provider = None
            if "analysis" in case:
                analysis_provider = RecordedProvider(case["analysis"])
                with api.provider_scope(analysis_provider, model_name="offline-replay"):
                    analysis = api.analyze_requirement(case["request"], analysis_mode=case.get("mode", "direct"))
                if not isinstance(analysis, dict):
                    raise ValueError("Recorded analysis was not accepted")
                draft = build_review_draft(analysis)
                selected_id = case.get("selected_approach_id")
                if selected_id:
                    draft["selected_approach"] = next(a for a in draft["approaches"] if a["approach_id"] == selected_id)
                for parameter in draft["parameters"]:
                    if parameter["id"] in case.get("answers", {}):
                        parameter.update(value=case["answers"][parameter["id"]], source="user")
                    if parameter["id"] in case.get("user_notes", {}):
                        parameter["note"] = case["user_notes"][parameter["id"]]
                if "user_summary" in case:
                    draft["summary"] = case["user_summary"]
                spec, receipt = seal_confirmation(draft)
                analysis_manifest = analysis.get("decision_receipt", {}).get("analysis_evidence", {})
                captured["analysis_evidence"] = analysis_manifest
                checks["analysis_no_debug_sources"] = all(r.get("manual_type") != "debug_cases" for r in analysis_manifest.get("records", []))
                checks["one_recorded_analysis"] = len(analysis_provider.requests) == 1
                checks["question_identity_preserved"] = set(case.get("answers", {})) <= {p["id"] for p in draft["parameters"]}
            else:
                # Keep unknown legacy text. Do not invent A's missing output,
                # or reinterpret already-corrupt historical question identities.
                spec = copy.deepcopy(case["confirmed_spec"])
                receipt = {}
            evidence = []
            original = agent._build_knowledge_context
            def observe(query, **kwargs):
                value = original(query, **kwargs)
                evidence.append(context_manifest(value))
                return value
            provider = RecordedProvider(case["completion"])
            with patch.object(agent, "_build_knowledge_context", observe), api.provider_scope(provider, model_name="offline-replay"):
                result = agent.generate_confirmed_ladder(spec, case.get("plc_model", "FX3U"), model_name="offline-replay", effort="high")
            validate_ladder_candidate_structure(result["ladder"], plc_model=case.get("plc_model", "FX3U"))
            projected, prompt = _request_spec(provider.requests[0])
            checks["one_recorded_generation"] = len(provider.requests) == 1
            checks["effort_preserved"] = provider.requests[0].options.get("reasoning_effort") == "high"
            checks["no_audit_envelope"] = not {"approaches", "engineering_context", "decision_receipt"} & projected.keys()
            checks["no_form_choices"] = all(not {"options", "suggested_default", "note_provenance"} & p.keys() for p in projected.get("parameters", []))
            checks["generation_no_debug_sources"] = all(r.get("manual_type") != "debug_cases" for m in evidence for r in m.get("records", []))
            if "analysis" in case:
                checks["selected_only"] = projected.get("selected_approach", {}).get("approach_id") == case["selected_approach_id"]
                checks["summary_view"] = projected.get("summary") == case.get("user_summary")
                checks["parameter_notes_view"] = all(p.get("note") == case.get("user_notes", {}).get(p["id"]) for p in projected.get("parameters", []))
            if case.get("no_drive_model"):
                checks["no_misbound_drive_model"] = not build_hardware_profile(spec, "FX3U").get("motion_drive_model")
            expected_labels = case.get("expected_labels")
            if expected_labels:
                checks["device_names_retained"] = all(result["ladder"]["device_comments"].get(k) == v for k, v in expected_labels.items())
            if case.get("requires_facts"):
                checks["current_facts_present"] = any(m.get("records") for m in evidence)
            # Exercise debug routing too; its cases must remain available for
            # actual troubleshooting, not be deleted from the knowledge base.
            if case.get("check_debug_lane"):
                debug = build_knowledge_context("FX3U M8336 zero return flag not positioning completion", task_type="debug", top_k=8, char_budget=20000)
                checks["debug_lane_available"] = "debug" in debug.manifest["retrieval_scope"]["source_lanes"]
            report["provider_fixture_calls"] = len(provider.requests) + (len(analysis_provider.requests) if analysis_provider else 0)
            report["generation_evidence_count"] = sum(len(m.get("records", [])) for m in evidence)
            report["generation_prompt_chars"] = len(prompt)
            captured.update(confirmed_spec=spec, generation_spec=projected, generation_evidence=evidence,
                            generation_prompt=prompt, decision_receipt=receipt)
    except Exception as error:
        checks["completed_replay"] = False
        report["error_type"] = type(error).__name__
        report["error_frames"] = [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                                  for frame in traceback.extract_tb(error.__traceback__)[-5:]]
    else:
        checks["completed_replay"] = True
    checks["no_network_attempts"] = not attempts
    checks["input_unchanged"] = digest(case) == before
    report["passed"] = all(checks.values())
    if include_content:
        from shared.tracing import sanitize
        report["content"] = sanitize(captured)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", help="One bundled case_id (otherwise run all)")
    parser.add_argument("--archive", type=Path, help="Private diagnostic ZIP; generation-only replay")
    parser.add_argument("--output", type=Path, help="New local report file; never overwrite")
    parser.add_argument("--include-content", action="store_true", help="Include sanitized private inputs/prompts; review before sharing")
    args = parser.parse_args(argv)
    if args.case and args.archive:
        parser.error("Choose --case or --archive, not both")
    try:
        cases = [archive_case(args.archive)] if args.archive else load_cases()
        if args.case:
            cases = [case for case in cases if case["case_id"] == args.case]
        if not cases:
            raise ValueError("Unknown/empty replay selection")
        rows = [run_case(case, include_content=args.include_content) for case in cases]
        data = json.dumps({"mode": "offline", "results": rows, "passed": all(r["passed"] for r in rows)}, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(data + "\n")
        else:
            print(data)
        return 0 if all(r["passed"] for r in rows) else 1
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        parser.error(type(error).__name__ + ": replay input/output unavailable")


if __name__ == "__main__":
    raise SystemExit(main())
