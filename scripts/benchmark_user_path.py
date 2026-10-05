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
import statistics
import itertools
import subprocess
import zipfile
from datetime import datetime, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.benchmark_agent_b import (
    EVIDENCE_ARMS, NO_RAG_ARM, ObservedProvider, PreviewProvider, no_rag_context, no_rag_scope,
    run_case, evidence_experiment_context, curated_evidence_context, assess_first_candidate,
    summarize,
)
from shared.tracing import sanitize

EXAMPLE_BLOCK = re.compile(r"\n# Routed construction examples \([^\n]*\)\n.*?\n# End routed construction examples\n", re.S)
KNOWLEDGE_MARKERS = ("# Retrieved PLC", "[KNOWLEDGE ", "[INSTRUCTION FACTS ",
                     "OPERAND_SEMANTICS:", "# Routed construction examples",
                     "# Confirmed operation effects", "# Compacted historical context")
CONSTRUCTION_ARMS = ('construction_description', 'core_instantiation')


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(sanitize(value), stream, ensure_ascii=False, indent=2, allow_nan=False)


def load_cases(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    cases = copy.deepcopy(data["cases"])
    for include in data.get("include_cases", []):
        inherited = load_cases(Path(path).parent / include)
        cases.extend({**case, "category": "complex"} for case in inherited)
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


def evidence_preflight(case, spec, provider):
    """Only the manual lane may differ; Core binding remains in every arm."""
    from knowledge.core import _format_result_block
    curated_evidence_context(case)
    records = [run_case({**case, "confirmed_spec": spec}, arm,
               provider=PreviewProvider(provider), model=provider.profile["model"])
               for arm in EVIDENCE_ARMS]
    common, requests = [], {}
    for arm, record in zip(EVIDENCE_ARMS, records):
        if len(record["actual_requests"]) != 1 or len(record["evidence"]) != 1:
            raise ValueError("Evidence preflight requires exactly one final request and context per arm")
        request, evidence = record["actual_requests"][0], record["evidence"][0]
        if record.get("handoff", {}).get("construction_examples", {}).get("enabled"):
            raise ValueError("Construction examples must be off in all evidence arms")
        content = "\n".join(str(m.get("content", "")) for m in request["messages"])
        if "# Routed construction examples" in content or "# Compacted historical context" in content:
            raise ValueError("Examples or historical context leaked into an evidence arm")
        if arm == "no_manual" and (evidence["text"] or evidence["manifest"].get("records")
                or any(marker in content for marker in KNOWLEDGE_MARKERS[:4])):
            raise ValueError("Manual evidence leaked into the no-manual arm")
        common.append(request_without_rag(request, evidence["text"]))
        requests[arm] = request
    if any(value != common[0] for value in common[1:]):
        raise ValueError("Specifications, protocol, Core bindings or tuning differ between evidence arms")
    for row in case["curated_evidence"]["records"]:
        content = requests["curated_evidence"]["messages"][0]["content"]
        if content.count(_format_result_block(row)) != 1:
            raise ValueError("A required curated source block was lost or changed after budgeting")
    return {"case_id": case["case_id"], "network_calls": 0, "passed": True,
        "non_evidence_content_identical": True, "Core_binding_policy_identical": True,
        "examples_enabled": False, "required_curated_blocks_delivered": True,
        "requests": requests, "records": records}


class RequestCheckedProvider:
    """Reject a drifted generation request before transport, without tuning it."""
    def __init__(self, provider, expected):
        self.provider, self.expected = provider, expected

    def __getattr__(self, name):
        return getattr(self.provider, name)

    def stream(self, request):
        if getattr(request.response_contract, "name", None) == "compact_ladder":
            if self.provider._request_params(request) != self.expected():
                raise ValueError("Generation request differs from its frozen offline preflight")
        yield from self.provider.stream(request)


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
    def __init__(self, directory, provider, *, cases=None, expected_requests=None):
        from application.workbench import WorkbenchService
        self.directory, self.provider = directory, provider
        self.current = None
        self.cases = {case["case_id"]: case for case in cases or []}
        self.expected_requests = expected_requests
        checked = RequestCheckedProvider(provider, lambda: self.expected_requests[
            self.current["case_id"]][self.current["arm"]]) if expected_requests is not None else provider
        self.observed = ObservedProvider(checked)
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
            diagnostic = self.current is not None and self.current.get("phase") == "generation" and self.current.get("arm") in EVIDENCE_ARMS
            if self.current is not None and self.current.get('arm') in CONSTRUCTION_ARMS:
                from knowledge.evidence import KnowledgeContext
                frozen = self.cases[self.current['case_id']]['frozen_evidence']
                context = KnowledgeContext(frozen['text'], copy.deepcopy(frozen['manifest']))
            elif diagnostic:
                context = evidence_experiment_context(self.cases[self.current["case_id"]],
                    self.current["arm"], original, query, **kwargs)
            else:
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
        import shared.diagnostics as diagnostics
        original_emit = diagnostics.emit
        def observe_result(event, **fields):
            if event == "provider_result" and self.current is not None:
                self.current.setdefault("provider_results", []).append({key: fields.get(key) for key in
                    ("model", "stream", "finish_reason", "finish_seen")})
            return original_emit(event, **fields)
        self.stack.enter_context(patch.object(diagnostics, "emit", observe_result))
        self.service.start()
        return self

    def __exit__(self, *args):
        self.service.close()
        self.stack.close()

    def job(self, command, *, case_id, arm=None, repeat=None, experiment_seed=None):
        disabled = command["kind"] == "generation" and arm == NO_RAG_ARM
        diagnostic = command["kind"] == "generation" and arm in (*EVIDENCE_ARMS, *CONSTRUCTION_ARMS)
        if disabled or diagnostic:
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
                    record["generation_handoff"] = self.service.projects.raw_version(
                        command["project_id"], version_id).get("generation_handoff")
                    if arm in CONSTRUCTION_ARMS:
                        metadata = self.service.projects.raw_version(command['project_id'], version_id)
                        record['final_behavior_check'] = copy.deepcopy(metadata.get('behavior_check'))
        finally:
            record["wall_ms"] = (time.perf_counter() - start) * 1000
            self.current = None
        if diagnostic:
            case = self.cases[case_id]
            spec = self.service.projects.raw_project(command["project_id"])["confirmed_spec"]
            if arm in CONSTRUCTION_ARMS:
                record['confirmed_spec_after'] = copy.deepcopy(spec)
            candidate_record = {"attempts": record["attempts"],
                "generation_status": "completed" if record["job"]["status"] == "completed" else "failed",
                "behavior": {"status": "not_covered"}}
            record["first_candidate"] = assess_first_candidate({**case, "confirmed_spec": spec,
                **({'construction_delivery': 'instantiate' if arm == 'core_instantiation' else 'description'} if arm in CONSTRUCTION_ARMS else {})}, candidate_record)
            if arm in CONSTRUCTION_ARMS and record.get('program') and case.get('bounded_time_traces'):
                from simulator.bounded import check_bounded_traces
                record['final_trace_check'] = check_bounded_traces(record['program'],spec,[],
                    frozen_time_traces=case['bounded_time_traces'],version_binding={'version_id':record.get('version_id')})
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


def diagnostic_specification(case):
    """Public synthetic requirement -> the existing confirmed-spec normalizer."""
    from plc.specification.confirmed import canonicalize_confirmed_spec
    rows = [{"kind": re.match(r"[A-Z]+", address)[0], "address": address, "label": label}
            for address, label in case["io"].items()]
    return canonicalize_confirmed_spec({"schema_version": 4, "plc_model": case["plc_model"],
        "summary": case["name"], "io_table": rows, "parameters": [],
        "selected_approach": {"approach_id": "confirmed_control", "name": "已确认控制需求",
            "description": case["confirmation"], "implementation_semantics": []},
        "io_bindings": [{"binding_id": "input." + row["address"], **row,
            "source": "user_request", "active_level": 1, "inactive_level": 0}
            for row in rows if row["kind"] == "X"],
        "intent_context": {"schema_version": 1, "requests": [{"id": case["case_id"],
            "text": case["confirmation"], "source": "user_request"}]},
        "execution_semantics": [], "operation_intents": []})


def assert_unfilled_spec(spec):
    if isinstance(spec, dict):
        if spec.get("instruction_instances"):
            raise ValueError("Evidence diagnostics cannot prefill complete instruction calls")
        for value in spec.values():
            assert_unfilled_spec(value)
    elif isinstance(spec, list):
        for value in spec:
            assert_unfilled_spec(value)


def freeze_code(directory):
    """Private byte snapshot; no new hash ledger or repository evidence archive."""
    sources = [p for group in ("application", "plc", "simulator", "model_runtime", "knowledge", "storage", "shared")
               for p in (ROOT / "src" / group).rglob("*.py")]
    sources.extend((ROOT / "resources" / "instructions").rglob("*.json"))
    sources.extend((ROOT / "resources" / "instructions").rglob("*.gz"))
    sources.extend(ROOT / name for name in ("resources/plc_models.json",
        "resources/pattern_library.json", "scripts/benchmark_user_path.py", "scripts/benchmark_agent_b.py"))
    sources.extend([ROOT/'scripts/benchmark_construction.py', ROOT/'benchmarks/user_path_construction_cases.json'])
    with zipfile.ZipFile(directory / "code_snapshot.zip", "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(sources)):
            archive.write(path, path.relative_to(ROOT).as_posix())


def assert_frozen_code(directory):
    with zipfile.ZipFile(directory / "code_snapshot.zip") as archive:
        for name in archive.namelist():
            if (ROOT / name).read_bytes() != archive.read(name):
                raise ValueError("Measured code changed after freeze: " + name)


def prepare_evidence_experiment(directory, cases, provider, packets, *, repeats, seed, specs_directory=None):
    """Freeze all materials and all final requests before the first live call."""
    if (directory / "experiment.json").exists():
        raise ValueError("An existing frozen experiment cannot be prepared again")
    directory.mkdir(parents=True, exist_ok=True)
    prepared, flights, receipts = [], {}, {}
    with Journey(directory / "preparation", PreviewProvider(provider)) as journey:
        for case in cases:
            packet = packets[case["case_id"]]
            source_path = specs_directory / (case["case_id"] + ".json") if specs_directory else None
            if source_path and source_path.exists():
                spec = json.loads(source_path.read_text(encoding="utf-8-sig"))
            elif case.get("category") == "complex":
                raise ValueError("The four complex cases require their shared confirmed-spec snapshots")
            else:
                spec = diagnostic_specification(case)
            assert_unfilled_spec(spec)
            project = journey.service.create_project(name=case["name"], plc_model=case["plc_model"], target_mode="ladder")
            receipt = journey.service.set_spec(project["id"], spec, None)
            if not receipt.get("valid"):
                raise ValueError(case["case_id"] + ": confirmed specification rejected: " + str(receipt.get("issues")))
            spec = receipt["spec"]
            enriched = {**case, "confirmed_spec": spec, "curated_evidence": packet}
            flight = evidence_preflight(enriched, spec, provider)
            prepared.append(enriched)
            flights[case["case_id"]] = flight["requests"]
            receipts[case["case_id"]] = flight
    # Nothing is frozen until every case passes. A failed preflight can be fixed
    # and rerun without deleting evidence or duplicating any live transport.
    for case in prepared:
        write_new(directory / "confirmed" / (case["case_id"] + ".json"), case["confirmed_spec"])
        write_new(directory / "preflight" / (case["case_id"] + ".json"), receipts[case["case_id"]])
    from scripts.benchmark_agent_b import schedule
    tasks = schedule(prepared, list(EVIDENCE_ARMS), repeats, seed, blocked=True)
    rows = [{"case_id": c["case_id"], "arm": arm, "repeat": repeat, "block": i // 3,
             "run_id": f"{c['case_id']}.{repeat}.{arm}", "blind_id": "candidate_" + uuid.uuid4().hex[:16]}
             for i, (c, arm, repeat) in enumerate(tasks)]
    code = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    identity = {"profile_id": provider.profile.get("id"), "profile_model": provider.profile.get("model"),
        "endpoint": provider.profile.get("baseUrl"), "user_model_settings": provider.profile.get("userModelSettings"),
        "seed": seed, "repeats": repeats, "git_commit": code, "git_status": dirty,
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version, "platform": sys.platform,
        "conclusion_scope": "This saved ModelScope DeepSeek V4.1 configuration and these confirmed tasks only",
        "curation": "Codex source review; no independent human signoff", "native_execution": "not_measured"}
    freeze_code(directory)
    write_new(directory / "experiment.json", {"identity": identity, "cases": prepared,
        "tasks": rows, "expected_requests": flights})
    with zipfile.ZipFile(directory / "materials_snapshot.zip", "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(directory / "experiment.json", "experiment.json")
    return {"passed": True, "network_calls": 0, "cases": len(cases), "scheduled_jobs": len(rows), **identity}


def run_evidence_experiment(directory, provider, *, worker_index=0, workers=1):
    with zipfile.ZipFile(directory / "materials_snapshot.zip") as archive:
        if (directory / "experiment.json").read_bytes() != archive.read("experiment.json"):
            raise ValueError("Frozen specifications, evidence or schedule changed")
    frozen = json.loads((directory / "experiment.json").read_text(encoding="utf-8"))
    identity = frozen["identity"]
    actual = {"profile_id": provider.profile.get("id"), "profile_model": provider.profile.get("model"),
        "endpoint": provider.profile.get("baseUrl"), "user_model_settings": provider.profile.get("userModelSettings")}
    if any(actual[key] != identity[key] for key in actual):
        raise ValueError("Saved ModelScope configuration changed after freeze")
    assert_frozen_code(directory)
    if not 0 <= worker_index < workers:
        raise ValueError("Invalid worker partition")
    partition_path = directory / "partition.json"
    if partition_path.exists():
        if json.loads(partition_path.read_text(encoding="utf-8"))["workers"] != workers:
            raise ValueError("Worker partition changed after jobs started")
    else:
        try:
            write_new(partition_path, {"workers": workers})
        except FileExistsError:
            if json.loads(partition_path.read_text(encoding="utf-8"))["workers"] != workers:
                raise ValueError("Conflicting worker partitions")
    cases = {c["case_id"]: c for c in frozen["cases"]}
    with Journey(directory / "workers" / str(worker_index), provider, cases=list(cases.values()),
                 expected_requests=frozen["expected_requests"]) as journey:
        projects = {}
        for task in frozen["tasks"]:
            if task["block"] % workers != worker_index:
                continue
            output = directory / "runs" / (task["run_id"] + ".json")
            claim = directory / "runs" / (task["run_id"] + ".started.json")
            if output.exists():
                blind_path = directory / "blind" / (task["blind_id"] + ".json")
                if not blind_path.exists():
                    export_blind_candidate(directory, task, cases[task["case_id"]],
                        json.loads(output.read_text(encoding="utf-8")))
                continue
            if claim.exists():
                # A started transport is never silently retried after a crash.
                continue
            case = cases[task["case_id"]]
            if case["case_id"] not in projects:
                project = journey.service.create_project(name=case["name"], plc_model=case["plc_model"], target_mode="ladder")
                receipt = journey.service.set_spec(project["id"], case["confirmed_spec"], None)
                if not receipt.get("valid") or receipt["spec"] != case["confirmed_spec"]:
                    raise ValueError("Confirmed specification drifted on worker startup")
                projects[case["case_id"]] = project["id"]
            write_new(claim, {**task, "started_at_utc": datetime.now(timezone.utc).isoformat(), "worker": worker_index})
            record = journey.job({"kind": "generation", "project_id": projects[case["case_id"]],
                "text": "根据当前已确认规格生成完整程序。", "construction_examples": False,
                "fresh_confirmed_generation": True}, case_id=case["case_id"], arm=task["arm"],
                repeat=task["repeat"], experiment_seed=identity["seed"])
            record.update(task, category=case["category"], plc_model=case["plc_model"], worker=worker_index)
            write_new(output, record)
            export_blind_candidate(directory, task, case, record)


def final_delivery_status(record):
    """Saving a rejected diagnostic is a completed job, not formal delivery."""
    if not record.get("ladder"):
        return "not_delivered"
    output = record.get("output") or {}
    generation = output.get("generation") or {}
    validation = generation.get("validation") or {}
    if (output.get("status") in {"saved_invalid", "saved_behavior_draft"} or output.get('activation_blocked') is True or generation.get("diagnostic_only") is True
            or generation.get("validation_profile") == "rejected_diagnostic"
            or validation.get("profile") == "rejected_diagnostic"
            or validation.get("status") == "invalid_candidate"):
        return "diagnostic_only"
    status = (record.get("job") or {}).get("status")
    if status is None:
        return "unverified"
    if status != "completed":
        return "not_delivered"
    return "delivered" if output.get("status") == "saved" else "unverified"


def export_blind_candidate(directory, task, case, record):
    """The review packet never exposes the arm, retrieval or job filename."""
    first = record.get("first_candidate") or {}
    delivered = record.get("ladder")
    delivery = final_delivery_status(record)
    packet = {"blind_id": task["blind_id"], "plc_model": case["plc_model"],
        "confirmed_spec": case["confirmed_spec"], "acceptance": case["acceptance"],
        "first_candidate": {"ladder": first.get("ladder"), "structural_valid": first.get("structural_valid", False),
            "error": first.get("error"), "contract_status": first.get("contract_status")},
        "final_candidate": {"ladder": delivered, "delivered": delivery == "delivered", "delivery_status": delivery},
        "raw_first_response": next((a["raw_content"] for a in record["attempts"]
            if a.get("response_contract") == "compact_ladder"), None),
        "scope": "Codex static engineering review; no independent human signoff; native execution not measured"}
    write_new(directory / "blind" / (task["blind_id"] + ".json"), packet)


def review_outcome(candidate, criteria):
    """Unknown requirements cannot turn into a whole-program success."""
    if not candidate.get("ladder"):
        return "not_delivered"
    if not criteria:
        return "unverified"
    statuses = [row["status"] for row in criteria]
    if "failed" in statuses:
        return "failed"
    return "passed" if all(s == "passed" for s in statuses) else "unverified"


def paired_outcome(baseline, treatment):
    if "unverified" in (baseline, treatment) or "missing" in (baseline, treatment):
        return "undetermined"
    if baseline == "passed":
        return "both_pass" if treatment == "passed" else "harm"
    return "rescue" if treatment == "passed" else "both_fail"


def case_cluster_test(grading, cases, repeats, arm, stage, *, baseline_arm=None):
    """Exploratory exact sign-flip test; a case, not a repetition, is the unit."""
    differences, excluded = [], []
    for case in cases:
        pairs = [(grading.get((case["case_id"], repeat, baseline_arm or EVIDENCE_ARMS[0])),
                  grading.get((case["case_id"], repeat, arm))) for repeat in range(repeats)]
        if any(a is None or b is None or a[stage] in {"unverified", "missing"} or b[stage] in {"unverified", "missing"}
               for a, b in pairs):
            excluded.append(case["case_id"])
            continue
        differences.append(sum((b[stage] == "passed") - (a[stage] == "passed") for a, b in pairs) / repeats)
    if not differences:
        return {"unit": "case", "eligible_cases": 0, "excluded_cases": excluded,
                "mean_difference": None, "two_sided_p": None, "interpretation": "exploratory"}
    observed = abs(sum(differences))
    tail = sum(abs(sum(s * d for s, d in zip(signs, differences))) >= observed - 1e-12
               for signs in itertools.product((-1, 1), repeat=len(differences)))
    return {"unit": "case", "eligible_cases": len(differences), "excluded_cases": excluded,
            "mean_difference": statistics.mean(differences), "two_sided_p": tail / 2 ** len(differences),
            "method": "exact paired sign-flip of case-level pass-rate differences",
            "interpretation": "exploratory; no population-frequency weighting or multiplicity correction"}


def request_exposure(expected_requests, case_id, arm):
    requests = expected_requests.get(case_id) or {}
    if EVIDENCE_ARMS[0] not in requests or arm not in requests:
        return "unverified"
    return "same_request" if requests[arm] == requests[EVIDENCE_ARMS[0]] else "manual_evidence_added"


def _latency_summary(values):
    values = sorted(values)
    return {"known_runs": len(values), "median": statistics.median(values) if values else None,
            "p95": values[max(0, (95 * len(values) + 99) // 100 - 1)] if values else None}


def summarize_evidence_experiment(directory):
    frozen = json.loads((directory / "experiment.json").read_text(encoding="utf-8"))
    if any((directory / "runs" / (t["run_id"] + ".json")).exists()
           and not (directory / "reviews" / (t["blind_id"] + ".json")).exists() for t in frozen["tasks"]):
        raise ValueError("Complete anonymous reviews for all recorded jobs before unblinding")
    rows, grading = [], {}
    for task in frozen["tasks"]:
        path = directory / "runs" / (task["run_id"] + ".json")
        if not path.exists():
            continue
        row = json.loads(path.read_text(encoding="utf-8"))
        case = next(c for c in frozen["cases"] if c["case_id"] == row["case_id"])
        review_path = directory / "reviews" / (task["blind_id"] + ".json")
        review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
        if (review.get("performed_by") != "Codex" or review.get("human_signoff") is not False
                or review.get("native_execution") != "not_measured" or not review.get("completed_at")
                or not review.get("method") or review.get("blind_id") != task["blind_id"]):
            raise ValueError("Review requires honest blinded static-review provenance")
        expected_ids = {c["id"] for c in case["acceptance"]}
        if not expected_ids or len(expected_ids) != len(case["acceptance"]):
            raise ValueError("Frozen requirements must have nonempty unique criterion IDs")
        outcomes, static = {}, {}
        delivery = final_delivery_status(row)
        for stage in ("first", "final"):
            checks = review.get(stage)
            if (not isinstance(checks, list) or len(checks) != len(expected_ids)
                    or any(not isinstance(c, dict) for c in checks)
                    or {c.get("id") for c in checks} != expected_ids
                    or any(c.get("status") not in {"passed", "failed", "unverified"} or not c.get("evidence") for c in checks)):
                raise ValueError("Review must cover each frozen requirement exactly once with evidence")
            candidate = row.get("first_candidate", {}) if stage == "first" else {"ladder": row.get("ladder")}
            outcome = review_outcome(candidate, checks)
            static[stage] = outcome
            if stage == "first" and outcome == "passed" and not candidate.get("structural_valid"):
                outcome = "failed"
            if stage == "final":
                if delivery in {"diagnostic_only", "not_delivered"}:
                    outcome = "not_delivered"
                elif delivery == "unverified" and outcome == "passed":
                    outcome = "unverified"
            outcomes[stage] = outcome
        grading[(row["case_id"], row["repeat"], row["arm"])] = {**outcomes,
            "wall_ms": row["wall_ms"], "calls": len(row["attempts"]), "category": case["category"],
            "review_ms": review.get("review_ms"), "delivery": delivery,
            **{"static_" + stage: static[stage] for stage in ("first", "final")}}
        row.update(end_to_end_ms=row["wall_ms"], model_calls=len(row["attempts"]),
            generation_status="completed" if row["job"]["status"] == "completed" else "failed",
            behavior={"status": "verified" if outcomes["final"] == "passed" else "failed"},
            first_candidate={**row.get("first_candidate", {}), "usable": outcomes["first"] == "passed",
                "semantic": {"status": "verified" if static["first"] == "passed" else "not_covered"}})
        rows.append(row)
    summaries = {}
    for category in ("all", "ordinary", "boundary", "complex"):
        selected = rows if category == "all" else [r for r in rows if r["category"] == category]
        group = summarize(selected)["groups"] if selected else {}
        for arm, value in group.items():
            grades = [g for key, g in grading.items() if key[2] == arm and (category == "all" or g["category"] == category)]
            # Reviews grade the decoded/common-Core-bound first candidate.
            # An ungraded raw response must not look like zero correct outputs.
            value["raw_model_semantic_correct"] = None
            value["raw_model_semantic_reviewed_runs"] = 0
            value["whole_program"] = {stage: {status: sum(g[stage] == status for g in grades)
                for status in ("passed", "failed", "unverified", "not_delivered")} for stage in ("first", "final")}
            value["whole_program_static"] = {stage: {status: sum(g["static_" + stage] == status for g in grades)
                for status in ("passed", "failed", "unverified", "not_delivered")} for stage in ("first", "final")}
            value["job_completed"] = value["completed"]
            value["formal_delivery"] = {status: sum(g["delivery"] == status for g in grades)
                for status in ("delivered", "diagnostic_only", "not_delivered", "unverified")}
            arm_rows = [r for r in selected if r["arm"] == arm]
            repairs = [(r.get("output") or {}).get("generation", {}).get("repair_attempts") for r in arm_rows]
            known_repairs = [v for v in repairs if type(v) is int and v >= 0]
            value["repair_attempts"] = {"known_runs": len(known_repairs),
                "unreported_runs": len(repairs) - len(known_repairs),
                "total": sum(known_repairs) if known_repairs else None}
            value["generation_followup_model_calls"] = sum(max(0, g["calls"] - 1) for g in grades)
            value["transport_failed_runs"] = sum(bool(r.get("transport_errors")) for r in arm_rows)
            value["no_program_runs"] = sum(not r.get("ladder") for r in arm_rows)
            value["latency_ms"] = {"all_jobs": _latency_summary([g["wall_ms"] for g in grades]),
                "first_usable": _latency_summary([g["wall_ms"] for g in grades if g["first"] == "passed"]),
                "final_passed": _latency_summary([g["wall_ms"] for g in grades if g["final"] == "passed"])}
            known = [g["review_ms"] for g in grades if type(g["review_ms"]) in (int, float)]
            value["codex_review_ms"] = {"known_runs": len(known), "median": statistics.median(known) if known else None}
            value["human_review_ms"] = None
            scheduled = sum(t["arm"] == arm and (category == "all" or
                next(c["category"] for c in frozen["cases"] if c["case_id"] == t["case_id"]) == category)
                for t in frozen["tasks"])
            value["scheduled_runs"] = scheduled
            value["missing_runs"] = scheduled - len(grades)
            value["first_usable_rate"] = sum(g["first"] == "passed" for g in grades) / scheduled
            value["final_full_pass_rate"] = sum(g["final"] == "passed" for g in grades) / scheduled
        summaries[category] = group
    contrasts = []
    for case in frozen["cases"]:
        for repeat in range(frozen["identity"]["repeats"]):
            baseline = grading.get((case["case_id"], repeat, EVIDENCE_ARMS[0]))
            for arm in EVIDENCE_ARMS[1:]:
                treatment = grading.get((case["case_id"], repeat, arm))
                contrasts.append({"case_id": case["case_id"], "category": case["category"], "repeat": repeat,
                    "treatment": arm, "request_exposure": request_exposure(frozen.get("expected_requests", {}), case["case_id"], arm),
                    **{stage: paired_outcome((baseline or {}).get(stage, "missing"),
                        (treatment or {}).get(stage, "missing")) for stage in ("first", "final")},
                    "treatment_minus_baseline_ms": treatment["wall_ms"] - baseline["wall_ms"] if baseline and treatment else None})
    counts = {category: {arm: {stage: {outcome: sum(c["treatment"] == arm and c[stage] == outcome
        and (category == "all" or c["category"] == category) for c in contrasts)
        for outcome in ("rescue", "harm", "both_pass", "both_fail", "undetermined")} for stage in ("first", "final")}
        for arm in EVIDENCE_ARMS[1:]} for category in ("all", "ordinary", "boundary", "complex")}
    exposure_counts = {arm: {exposure: {stage: {outcome: sum(c["treatment"] == arm
        and c["request_exposure"] == exposure and c[stage] == outcome for c in contrasts)
        for outcome in ("rescue", "harm", "both_pass", "both_fail", "undetermined")} for stage in ("first", "final")}
        for exposure in ("same_request", "manual_evidence_added", "unverified")} for arm in EVIDENCE_ARMS[1:]}
    case_results = [{"case_id": case["case_id"], "category": case["category"],
        "groups": {arm: {"first_passed": sum(g["first"] == "passed" for key, g in grading.items()
            if key[0] == case["case_id"] and key[2] == arm),
            "final_passed": sum(g["final"] == "passed" for key, g in grading.items()
            if key[0] == case["case_id"] and key[2] == arm),
            "static_final_passed": sum(g["static_final"] == "passed" for key, g in grading.items()
            if key[0] == case["case_id"] and key[2] == arm)} for arm in EVIDENCE_ARMS},
        "contrasts": [c for c in contrasts if c["case_id"] == case["case_id"]]} for case in frozen["cases"]]
    cluster_tests = {category: {arm: {stage: case_cluster_test(grading,
        [c for c in frozen["cases"] if category == "all" or c["category"] == category],
        frozen["identity"]["repeats"], arm, stage) for stage in ("first", "final")}
        for arm in EVIDENCE_ARMS[1:]} for category in ("all", "ordinary", "boundary", "complex")}
    interrupted = [t["run_id"] for t in frozen["tasks"] if
        (directory / "runs" / (t["run_id"] + ".started.json")).exists() and
        not (directory / "runs" / (t["run_id"] + ".json")).exists()]
    return {"identity": frozen["identity"], "scheduled_jobs": len(frozen["tasks"]), "recorded_jobs": len(rows),
        "started_without_result": interrupted, "case_cluster_tests": cluster_tests,
        "response_models": sorted({r["model"] for row in rows for r in row.get("provider_results", []) if r.get("model")}),
        "response_model_known_jobs": sum(any(r.get("model") for r in row.get("provider_results", [])) for row in rows),
        "groups": summaries, "contrasts": contrasts, "contrast_counts": counts,
        "contrast_counts_by_exposure": exposure_counts, "case_results": case_results,
        "frequency_weighting": "not_available; strata are not a workload-weighted population",
        "independent_cases": len(frozen["cases"]), "native_execution": "not_measured",
        "performance_acceptance": "not_established", "curation": "Codex; not independent human signoff"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--phase", choices=("analysis", "clarification", "confirm", "preflight", "generation", "review", "prepare", "summary"), required=True)
    parser.add_argument("--case-id")
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--seed", type=int, default=41004)
    parser.add_argument("--review-depth", choices=("basic", "deep"), default="basic",
                        help="Basic runs the existing local reviewer; deep additionally calls the saved model")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--include-no-rag", action="store_true",
                        help="Add an unassisted generation arm; disable all retrieval and construction examples")
    parser.add_argument("--experiment", choices=("examples", "evidence-value", "construction-value"), default="examples",
                        help="Evidence-only diagnostic, preserving common Core bindings")
    parser.add_argument("--curated-evidence", type=Path, help="Private source-reviewed excerpt packet indexed by case_id")
    parser.add_argument("--confirmed-specs", type=Path, help="Directory of the shared confirmed complex-case snapshots")
    parser.add_argument("--supplement-from", type=Path,
                        help="Explicit official completion of a frozen construction experiment; preserve all source records")
    parser.add_argument("--workers", type=int, default=1, help="Independent process partitions; each block stays serial")
    parser.add_argument("--worker-index", type=int, default=0)
    args = parser.parse_args(argv)
    diagnostic = args.experiment == "evidence-value"
    args.repeats = args.repeats if args.repeats is not None else (3 if diagnostic or args.experiment == 'construction-value' else 2)
    args.cases = args.cases or ROOT / ('benchmarks/user_path_construction_cases.json' if args.experiment == 'construction-value' else "benchmarks/user_path_evidence_cases.json" if diagnostic else "benchmarks/user_path_complex_cases.json")
    if args.repeats < 1:
        parser.error("repeats must be positive")
    directory = private_directory(args.output)
    cases = [case for case in load_cases(args.cases) if not args.case_id or case["case_id"] == args.case_id]
    if not cases:
        parser.error("No selected case")
    if args.supplement_from and args.experiment != 'construction-value':
        parser.error("Supplementing belongs only to the construction experiment")
    if args.supplement_from:
        args.supplement_from = private_directory(args.supplement_from)
    if args.experiment == 'construction-value':
        from scripts.benchmark_construction import construction_main
        return construction_main(args, cases, directory)
    if diagnostic and args.include_no_rag:
        parser.error("The legacy no-RAG ablation is separate from the evidence-only experiment")
    if diagnostic and args.phase == "summary":
        result = summarize_evidence_experiment(directory)
        path = directory / ("summary-" + uuid.uuid4().hex[:8] + ".json")
        write_new(path, result)
        print(json.dumps({"output": str(path), "scheduled_jobs": result["scheduled_jobs"],
                          "recorded_jobs": result["recorded_jobs"], "contrast_counts": result["contrast_counts"]}, ensure_ascii=False))
        return 0
    if args.phase in {"analysis", "clarification", "generation", "review"} and not args.live:
        print(json.dumps({"live": False, "phase": args.phase, "cases": [case["case_id"] for case in cases],
                          "profile_id": args.profile_id, "generation_repeats": args.repeats,
                          "generation_arms": list(EVIDENCE_ARMS) if diagnostic else [arm for arm, _ in generation_arms(args.include_no_rag)],
                          **({"scheduled_jobs": len(cases) * args.repeats * 3,
                              "conclusion_scope": "This ModelScope DeepSeek V4.1 configuration only"} if diagnostic else {}),
                          "network_calls": 0}, ensure_ascii=False))
        return 0
    from storage.config import load_full_config, get_model_profile
    from model_runtime.provider import get_active_provider
    config = load_full_config()
    profile = get_model_profile(config, args.profile_id)
    if str(profile.get("baseUrl", "")).rstrip("/") != "https://api-inference.modelscope.cn/v1":
        parser.error("This journey requires the saved ModelScope endpoint")
    if diagnostic and profile.get("model") != "deepseek-ai/DeepSeek-V4.1-Flash":
        parser.error("This evidence diagnostic is limited to the saved ModelScope DeepSeek V4.1 model")
    provider = get_active_provider({**config, "activeModelProfileId": args.profile_id})
    directory.mkdir(parents=True, exist_ok=True)
    if diagnostic:
        if args.phase in {"prepare", "preflight"}:
            if not args.curated_evidence or not args.confirmed_specs:
                parser.error("Prepare requires private curated evidence and shared confirmed-spec snapshots")
            packets = json.loads(args.curated_evidence.read_text(encoding="utf-8-sig"))
            result = prepare_evidence_experiment(directory, cases, provider, packets,
                repeats=args.repeats, seed=args.seed, specs_directory=args.confirmed_specs)
            print(json.dumps(result, ensure_ascii=False))
        elif args.phase == "generation":
            run_evidence_experiment(directory, provider, worker_index=args.worker_index, workers=args.workers)
        else:
            parser.error("Evidence diagnostics use prepare, generation and summary phases")
        return 0
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
