"""Synthetic user journeys through WorkbenchService, with private observations.

Reuse the Agent-B request observer and offline provider. This runner does not
implement generation, retrieval, approval or PLC semantics. Saved profile tuning
and the application validation/repair behavior are unchanged.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import ExitStack, nullcontext
import json
from pathlib import Path
import random
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.benchmark_agent_b import (
    NO_RAG_ARM, ObservedProvider, PreviewProvider, no_rag_context, no_rag_scope, run_case,
)
from shared.tracing import sanitize

EXAMPLE_BLOCK = re.compile(r"\n# Routed construction examples \([^\n]*\)\n.*?\n# End routed construction examples\n", re.S)
KNOWLEDGE_MARKERS = ("# Retrieved PLC", "[KNOWLEDGE ", "[INSTRUCTION FACTS ",
                     "OPERAND_SEMANTICS:", "# Routed construction examples",
                     "# Confirmed operation effects", "# Compacted historical context")


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(sanitize(value), stream, ensure_ascii=False, indent=2, allow_nan=False)


def load_cases(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    cases = data["cases"]
    identifiers = [case["case_id"] for case in cases]
    if len(set(identifiers)) != len(identifiers) or not cases:
        raise ValueError("Cases require unique identifiers")
    for case in cases:
        if not re.fullmatch(r"[a-z0-9_]+", case["case_id"]):
            raise ValueError("Invalid case identifier")
        for key in ("initial_request", "confirmation", "acceptance", "rag_needs"):
            if not case.get(key):
                raise ValueError(f"Missing {key}")
    return cases


def private_directory(value):
    directory = Path(value).resolve()
    if directory == ROOT or ROOT in directory.parents:
        raise ValueError("Raw results and user specifications must be outside the repository")
    return directory


def request_without_examples(request):
    result = copy.deepcopy(request)
    for message in result.get("messages", []):
        if isinstance(message.get("content"), str):
            message["content"] = EXAMPLE_BLOCK.sub("", message["content"])
    return result


def request_without_rag(request, evidence_text):
    """Remove only the observed evidence and its availability flag for comparison."""
    result = copy.deepcopy(request)
    system = result["messages"][0]
    content = system["content"]
    if evidence_text:
        if content.count(evidence_text) != 1:
            raise ValueError("Retrieved evidence must be present once in the final request")
        content = content.replace(evidence_text, "", 1)
    marker = "# Settled input predicates (not a new requirement)\n"
    if marker in content:
        prefix, predicates = content.rsplit(marker, 1)
        start = len(predicates) - len(predicates.lstrip())
        facts, end = json.JSONDecoder().raw_decode(predicates, start)
        facts["retrieved_text_present"] = False
        content = (prefix + marker + predicates[:start]
                   + json.dumps(facts, ensure_ascii=False, separators=(",", ":")) + predicates[end:])
    system["content"] = content
    return result


def assert_no_rag_request(request, contexts):
    """Check the model-visible boundary, including indirect facts and examples."""
    if not contexts or any(row.get("text") or row.get("manifest", {}).get("records")
                           or row.get("manifest", {}).get("retrieval_enabled") is not False
                           for row in contexts):
        raise ValueError("No-RAG requires explicitly disabled retrieval with empty evidence")
    content = "\n".join(str(message.get("content", "")) for message in request.get("messages", []))
    if any(marker in content for marker in KNOWLEDGE_MARKERS):
        raise ValueError("Knowledge, examples or automatic operation binding leaked into the no-RAG request")
    if '"retrieved_text_present":true' in content:
        raise ValueError("No-RAG request claims retrieved evidence")


def generation_arms(include_no_rag=False):
    arms = [("examples_off", False), ("examples_on", True)]
    return [*arms, (NO_RAG_ARM, False)] if include_no_rag else arms


def preflight(case, spec, provider, *, include_no_rag=False):
    from application.confirmed_generation_context import project_confirmed_specification
    from plc.specification.conditions import generation_input_conditions

    records = [run_case({**case, "confirmed_spec": spec, "construction_examples": enabled},
                        "automatic", provider=PreviewProvider(provider), model=provider.profile["model"])
               for enabled in (False, True)]
    if any(len(record["actual_requests"]) != 1 for record in records):
        raise ValueError("A pair must have exactly one final request per arm before live transport")
    requests = [record["actual_requests"][0] for record in records]
    same = request_without_examples(requests[0]) == request_without_examples(requests[1])
    if not same:
        raise ValueError("Non-example request content differs; inspect budget/delivery before a paired run")
    enabled = bool(records[1].get("handoff", {}).get("construction_examples", {}).get("enabled"))
    snapshot = project_confirmed_specification(spec)
    predicates = generation_input_conditions(
        snapshot.get("io_bindings"), plc_model=spec.get("plc_model") or case.get("plc_model") or "FX3U"
    )
    inputs = sorted({row["address"] for row in snapshot.get("io_table", [])
                     if row.get("kind") == "X" and row.get("address")})
    settled = {row["address"] for row in predicates["level_predicates"]}
    no_rag_check = None
    if include_no_rag:
        baseline = run_case({**case, "confirmed_spec": spec, "construction_examples": False},
                            NO_RAG_ARM, provider=PreviewProvider(provider), model=provider.profile["model"])
        if len(baseline["actual_requests"]) != 1:
            raise ValueError("No-RAG must have exactly one final request before live transport")
        request = baseline["actual_requests"][0]
        assert_no_rag_request(request, baseline["evidence"])
        evidence = records[0]["evidence"]
        if len(evidence) != 1:
            raise ValueError("A single common knowledge context is required for the RAG contrast")
        if request_without_rag(requests[0], evidence[0]["text"]) != request:
            raise ValueError("Non-RAG content differs; specifications, protocol and tuning must match")
        no_rag_check = {"passed": True, "non_rag_content_identical": True,
                        "retrieval_enabled": False, "evidence_records": 0, "examples_enabled": False,
                        "scope": "generation_from_the_same_confirmed_specification"}
        records.append(baseline)
    return {"case_id": case["case_id"], "network_calls": 0, "non_example_content_identical": same,
            "example_contrast_delivered": enabled, "input_level_delivery": {
                "physical_input_count": len(inputs), "settled_predicate_count": len(settled),
                "missing_level_addresses": [address for address in inputs if address not in settled],
                "unresolved_binding_ids": predicates["unresolved_input_bindings"]},
            "no_rag_check": no_rag_check, "records": records}


class Journey:
    def __init__(self, directory, provider):
        from application.workbench import WorkbenchService
        self.directory, self.provider = directory, provider
        self.current = None
        self.observed = ObservedProvider(provider)
        self.provider.observation_sink = self.capture_request
        self.service = WorkbenchService(directory / "workspace", directory / "state",
            model_factory=lambda: (self.observed, {"profile_id": provider.profile["id"],
                "model": provider.profile["model"], "response_language": "zh-CN"}))

    def capture_request(self, params, events, error=None):
        if self.current is not None:
            self.current["actual_requests"].append(copy.deepcopy(params))
            if error is not None:
                self.current["transport_errors"].append({"type": type(error).__name__, "message": str(error)})

    def __enter__(self):
        import application.model_api as api
        import application.generation_agent as agent
        self.stack = ExitStack()
        original = api._build_knowledge_context

        def observe(query, **kwargs):
            from knowledge.evidence import context_manifest
            start = time.perf_counter()
            disabled = self.current is not None and self.current.get("phase") == "generation" and self.current.get("arm") == NO_RAG_ARM
            context = no_rag_context() if disabled else original(query, **kwargs)
            if self.current is not None:
                self.current["retrieval"].append({"query": str(query),
                    "query_metadata": copy.deepcopy(getattr(query, "metadata", {})),
                    "arguments": copy.deepcopy(kwargs), "text": str(context),
                    "manifest": context_manifest(context),
                    "elapsed_ms": (time.perf_counter() - start) * 1000})
            return context

        self.stack.enter_context(patch.object(api, "_build_knowledge_context", observe))
        self.stack.enter_context(patch.object(agent, "_build_knowledge_context", observe))
        self.service.start()
        return self

    def __exit__(self, *args):
        self.service.close()
        self.stack.close()

    def job(self, command, *, case_id, arm=None, repeat=None, experiment_seed=None):
        disabled = command["kind"] == "generation" and arm == NO_RAG_ARM
        if disabled:
            command = {**command, "construction_examples": False}
        command = {"response_language": "zh-CN", "attachment_ids": [],
                   "request_id": "request_" + uuid.uuid4().hex, **command}
        record = {"case_id": case_id, "phase": command["kind"], "arm": arm,
                  "repeat": repeat, "experiment_seed": experiment_seed, "actual_requests": [], "retrieval": [],
                  "attempts": [], "transport_errors": [], "started_at_utc": datetime.now(timezone.utc).isoformat()}
        self.observed.attempts = record["attempts"]
        self.current = record
        start = time.perf_counter()
        try:
            with no_rag_scope() if disabled else nullcontext():
                job = self.service.submit(command)
                job_id = job["id"]
                # JobManager owns cancellation, retries and worker exceptions.
                while self.service.jobs.get(job_id)["status"] in {"queued", "running", "cancelling"}:
                    time.sleep(0.25)
                record["job"] = self.service.jobs.get(job_id)
                record["events"] = self.service.jobs.events(job_id)
                try:
                    record["output"] = self.service.output(job_id)
                except KeyError:
                    record["output"] = None
                version_id = (record["output"] or {}).get("version_id") or (record["job"].get("result") or {}).get("version_id")
                if version_id:
                    from plc.ir import ir_to_ladder
                    record["version_id"] = version_id
                    record["program"] = self.service.projects.program(command["project_id"], version_id)
                    if record["program"]:
                        record["ladder"] = ir_to_ladder(record["program"])
        finally:
            record["wall_ms"] = (time.perf_counter() - start) * 1000
            self.current = None
        write_new(self.directory / "jobs" / f"{job_id}.json", record)
        if disabled:
            if len(record["actual_requests"]) != 1:
                raise ValueError("No-RAG must retain one unassisted generation request")
            assert_no_rag_request(record["actual_requests"][0], record["retrieval"])
        print(json.dumps({"case_id": case_id, "phase": command["kind"], "arm": arm,
                          "repeat": repeat, "status": record["job"]["status"],
                          "calls": len(record["attempts"]), "job_id": job_id,
                          "wall_ms": round(record["wall_ms"])}, ensure_ascii=False), flush=True)
        return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "benchmarks/user_path_complex_cases.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--phase", choices=("analysis", "clarification", "confirm", "preflight", "generation", "review"), required=True)
    parser.add_argument("--case-id")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--seed", type=int, default=41004)
    parser.add_argument("--review-depth", choices=("basic", "deep"), default="basic",
                        help="Basic runs the existing local reviewer; deep additionally calls the saved model")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--include-no-rag", action="store_true",
                        help="Add an unassisted generation arm; disable all retrieval and construction examples")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("repeats must be positive")
    directory = private_directory(args.output)
    cases = [case for case in load_cases(args.cases) if not args.case_id or case["case_id"] == args.case_id]
    if not cases:
        parser.error("No selected case")
    if args.phase in {"analysis", "clarification", "generation", "review"} and not args.live:
        print(json.dumps({"live": False, "phase": args.phase, "cases": [case["case_id"] for case in cases],
                          "profile_id": args.profile_id, "generation_repeats": args.repeats,
                          "generation_arms": [arm for arm, _ in generation_arms(args.include_no_rag)],
                          "network_calls": 0}, ensure_ascii=False))
        return 0
    from storage.config import load_full_config, get_model_profile
    from model_runtime.provider import get_active_provider
    config = load_full_config()
    profile = get_model_profile(config, args.profile_id)
    if str(profile.get("baseUrl", "")).rstrip("/") != "https://api-inference.modelscope.cn/v1":
        parser.error("This journey requires the saved ModelScope endpoint")
    provider = get_active_provider({**config, "activeModelProfileId": args.profile_id})
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / "run_profile.json").exists():
        write_new(directory / "run_profile.json", {
            "profile_id": profile["id"], "profile_model": profile["model"],
            "endpoint": profile["baseUrl"], "user_model_settings": profile.get("userModelSettings"),
            "global_active_profile_id": config.get("activeModelProfileId"),
            "started_at_utc": datetime.now(timezone.utc).isoformat(), "seed": args.seed,
            "fixed_generation_repeats": args.repeats,
            "generation_arms": [arm for arm, _ in generation_arms(args.include_no_rag)],
        })
        write_new(directory / "synthetic_cases.json", {"cases": load_cases(args.cases)})
    projects_path = directory / "projects.json"
    projects = json.loads(projects_path.read_text(encoding="utf-8")) if projects_path.exists() else {}
    if args.phase == "preflight":
        for case in cases:
            spec = json.loads((directory / "confirmed" / f"{case['case_id']}.json").read_text(encoding="utf-8"))
            result = preflight(case, spec, provider, include_no_rag=args.include_no_rag)
            write_new(directory / "preflight" / f"{case['case_id']}.json", result)
            print(json.dumps({key: result[key] for key in result if key != "records"}, ensure_ascii=False), flush=True)
        return 0
    with Journey(directory, provider) as journey:
        if args.phase == "analysis":
            for case in cases:
                project = journey.service.create_project(name=case["name"], plc_model=case["plc_model"], target_mode="ladder")
                projects[case["case_id"]] = project["id"]
                record = journey.job({"kind": "analysis", "project_id": project["id"], "analysis_mode": "design",
                                      "text": case["initial_request"]}, case_id=case["case_id"])
                if record.get("output"):
                    write_new(directory / "drafts" / f"{case['case_id']}.json", record["output"])
                # Keep every completed journey recoverable even if a later case fails.
                from application.workspace import atomic_json
                atomic_json(projects_path, projects)
        elif args.phase == "clarification":
            for case in cases:
                project_id = projects[case["case_id"]]
                draft = json.loads((directory / "drafts" / f"{case['case_id']}.json").read_text(encoding="utf-8"))
                if not journey.service.projects.raw_project(project_id).get("messages"):
                    journey.service.store.add_message(project_id, "user", case["initial_request"], kind="analysis")
                journey.service.store.add_message(project_id, "assistant", json.dumps(draft["analysis"], ensure_ascii=False), kind="analysis")
                record = journey.job({"kind": "analysis", "project_id": project_id, "analysis_mode": "design",
                                      "text": "补齐并修正规格如下，请根据这些确认事实更新方案，不生成程序：\n" + case.get("clarification_note", "") + "\n" + case["confirmation"]},
                                     case_id=case["case_id"])
                if record.get("output"):
                    write_new(directory / "clarified_drafts" / f"{case['case_id']}.json", record["output"])
        elif args.phase == "confirm":
            for case in cases:
                path = directory / "reviewed_drafts" / f"{case['case_id']}.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                project_id = projects[case["case_id"]]
                journey.service.store.add_message(project_id, "user", case["confirmation"], kind="spec_confirmation")
                result = journey.service.set_spec(project_id, payload["spec"], payload.get("expected_hash"))
                write_new(directory / "confirmations" / f"{case['case_id']}.json", {**result, "review": payload.get("review")})
                if not result.get("valid"):
                    raise ValueError(f"{case['case_id']}: reviewed specification rejected: {result.get('issues')}")
                write_new(directory / "confirmed" / f"{case['case_id']}.json", result["spec"])
                print(json.dumps({"case_id": case["case_id"], "confirmed": True,
                                  "warnings": len(result.get("issues", {}).get("warnings", []))}, ensure_ascii=False), flush=True)
        elif args.phase == "generation":
            rng = random.Random(args.seed)
            blocks = [(case, repeat) for repeat in range(args.repeats) for case in cases]
            rng.shuffle(blocks)
            for case, repeat in blocks:
                arms = generation_arms(args.include_no_rag)
                rng.shuffle(arms)
                spec = json.loads((directory / "confirmed" / f"{case['case_id']}.json").read_text(encoding="utf-8"))
                flight = json.loads((directory / "preflight" / f"{case['case_id']}.json").read_text(encoding="utf-8"))
                if not flight["non_example_content_identical"]:
                    raise ValueError("A successful final-request preflight is required")
                if args.include_no_rag and not (flight.get("no_rag_check") or {}).get("passed"):
                    raise ValueError("A successful no-RAG final-request preflight is required")
                if journey.service.projects.raw_project(projects[case["case_id"]])["confirmed_spec"] != spec:
                    raise ValueError("Specification changed after preflight")
                for arm, enabled in arms:
                    journey.job({"kind": "generation", "project_id": projects[case["case_id"]],
                                 "text": "根据当前已确认规格生成完整程序。", "construction_examples": enabled,
                                 "fresh_confirmed_generation": True}, case_id=case["case_id"], arm=arm,
                                repeat=repeat, experiment_seed=args.seed)
        else:
            generated = []
            for path in sorted((directory / "jobs").glob("*.json")):
                row = json.loads(path.read_text(encoding="utf-8"))
                if row["phase"] == "generation" and row.get("version_id"):
                    generated.append(row)
            for row in generated:
                case = next((case for case in cases if case["case_id"] == row["case_id"]), None)
                if case is None:
                    continue
                journey.job({"kind": "review", "project_id": projects[case["case_id"]],
                             "version_id": row["version_id"], "deep": args.review_depth == "deep",
                             "text": "逐项核查以下独立验收要求。指出具体梯级、触发条件和同扫描顺序问题，不以JSON合法代替功能正确：\n"
                                + json.dumps(case["acceptance"], ensure_ascii=False)},
                            case_id=case["case_id"], arm=row["arm"], repeat=row["repeat"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
