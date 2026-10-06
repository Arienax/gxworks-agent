"""Alarm-only, serial source-frozen API/MCP contrast; no repair or native calls.

Context preparation loads the pre-change snapshot in a separate process. Both
live arms use the current transport, Core, candidate/persistence and exporters.
Raw records stay in the user-selected measurement directory, outside Git.
"""
from __future__ import annotations

import argparse
import base64
import copy
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "custom_e9bb932c31124eba9a2fcdd6dc769624"
LANGUAGES = {"motor_latch": ("ladder", "ladder"), "segd_countdown": ("ladder", "ladder"),
             "multi_zone_alarm": ("ladder", "ladder"), "copy_initialize": ("st", "ladder"),
             "delayed_start": ("fbd", "fbd"), "traffic_cycle": ("fbd", "fbd")}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def boot(source=ROOT):
    sys.path[:0] = [str(ROOT), str(Path(source) / "src")]


def specification(case, text):
    from application.confirmed_generation_context import project_direct_user_facts
    facts = project_direct_user_facts(text, "FX3U")
    return {**facts, "schema_version": 4, "summary": text, "io_table": copy.deepcopy(case["io"]),
            "selected_approach": {"approach_id": "frozen_user_facts", "name": "实现冻结用户需求"},
            "intent_context": {"schema_version": 1, "requests": [
                {"id": case["case_id"], "source": "user_request", "text": text}]}}


def context_packet(source, case, interface, *, answered=False, profile=None):
    boot(source)
    import application.model_api as api
    import application.generation_context as gc
    import application.generation_agent as ga
    import knowledge.retriever as retriever
    from knowledge.evidence import context_manifest
    from agent_runtime.plc_tools import build_default_tool_registry, build_tool_context
    target = LANGUAGES[case["case_id"]][0 if interface == "api" else 1]
    text = case["initial_request"] + ("\n用户续答：" + case["clarification_answer"] if answered else "")
    spec = specification(case, text) if target != "ladder" and (case["case_id"] != "delayed_start" or answered) else None
    retrieval, handoff = [], {}
    original, search = gc._build_knowledge_context, retriever.retrieve_knowledge

    def knowledge(query, **kwargs):
        start = time.perf_counter()
        value = original(query, **kwargs)
        retrieval.append({"kind": "context", "query": str(query), "text": str(value),
            "manifest": context_manifest(value), "elapsed_ms": (time.perf_counter()-start)*1000})
        return value

    def searched(query, **kwargs):
        start = time.perf_counter()
        value = search(query, **kwargs)
        retrieval.append({"kind": "search", "query": str(query), "arguments": kwargs,
            "records": value, "elapsed_ms": (time.perf_counter()-start)*1000})
        return value

    start = time.perf_counter()
    with ExitStack() as stack:
        from application.construction_examples import prepare_construction_examples
        stack.enter_context(patch.object(ga, "prepare_construction_examples",
            lambda cpu, *a, **k: prepare_construction_examples(cpu, False)))
        for module in (gc, api, ga):
            stack.enter_context(patch.object(module, "_build_knowledge_context", knowledge))
        stack.enter_context(patch.object(retriever, "retrieve_knowledge", searched))
        packet = {"case_id": case["case_id"], "interface": interface, "target_mode": target,
                  "requirement": text, "specification": spec, "retrieval": retrieval}
        if interface == "mcp":
            project = {"id": "measurement-project", "name": case["name"], "plc_model": "FX3U",
                       "target_mode": target, "confirmed_spec": spec}
            result = build_default_tool_registry().call("get_generation_context", {"user_requirement": text},
                                                       build_tool_context(project))
            if not result["ok"]:
                raise ValueError(result)
            packet["context"] = result["data"]
            packet["project"] = project
            from integrations.mcp.server import SERVER_INSTRUCTIONS
            from agent_runtime.runtime import build_default_tool_runtime
            packet["mcp_server_instructions"] = SERVER_INSTRUCTIONS
            packet["mcp_tools"] = build_default_tool_runtime().list_tools()
        elif target == "ladder":
            from application.confirmed_generation_context import build_direct_generation_context
            from application.construction_examples import prepare_construction_examples
            value = build_direct_generation_context(text, "FX3U", knowledge_builder=knowledge,
                model_profile=profile or {}, wire_renderer=ga._compact_wire_renderer("FX3U", direct=True,
                    example_block=prepare_construction_examples("FX3U", False)))
            packet["direct_context"] = value.to_dict()
        elif target == "st":
            packet["system"] = str(gc.build_generation_instructions(text, plc_model="FX3U", target_mode="st",
                confirmed_context=spec, model_profile=profile or {}, knowledge_builder=knowledge,
                on_context=handoff.update))
            packet["generation_handoff"] = handoff
        else:
            from application import fbd
            from gxw.object_model import default_baseline
            catalog = fbd.catalog_snapshot(default_baseline())
            if hasattr(fbd, "build_fbd_generation_context"):
                packet["fbd_context"] = fbd.build_fbd_generation_context(text, plc_model="FX3U",
                    confirmed_spec=spec, catalog=catalog, model_profile=profile or {})
            else:
                from gxw.generation_contract import FBD_GENERATION_PROMPT
                from application.generation_support import public_generation_specification
                packet["fbd_context"] = {"generation_instructions": FBD_GENERATION_PROMPT,
                    "generation_input": {"schema_version": 2, "cpu": catalog["cpu"], "program": catalog["program"],
                        "catalog": catalog["nodes"], "declaration_tables": catalog["declaration_tables"],
                        "confirmed_spec": public_generation_specification(spec), "previous": None, "request": text},
                    "generation_handoff": {"stage": "generate", "verification": "not_performed"}}
            if case["case_id"] == "delayed_start" and not answered:
                value = api.assemble_analysis_prompt(text, plc_model="FX3U", confirmed_context=None,
                    analysis_mode="direct", model_loader=gc._load_plc_models, knowledge_builder=knowledge,
                    resolve_opcode=api.DEFAULT_INSTRUCTION_REGISTRY.resolve_form, audit=lambda *a, **k: None)
                packet["analysis_context"] = {"system_prompt": value.system_prompt,
                    "knowledge_context": str(value.knowledge_context),
                    "knowledge_manifest": context_manifest(value.knowledge_context),
                    "contract_stage": value.contract_stage, "include_design": value.route.include_design}
    packet["context_ms"] = (time.perf_counter()-start)*1000
    return packet


def samples():
    return [read(ROOT / "benchmarks/capability_alarm_case.json")]


def selected_profile():
    from storage.config import get_config_path
    from model_runtime.provider import get_active_provider
    config = read(get_config_path())
    config["activeModelProfileId"] = PROFILE
    provider = get_active_provider(config)
    if provider.profile["model"] != "deepseek-flash" or provider.profile["baseUrl"].rstrip("/") != "https://api.deepseek.com":
        raise ValueError("The saved official DeepSeek preset differs from the frozen experiment")
    if not provider.api_key:
        raise ValueError("Saved DeepSeek credential is unavailable")
    return config, provider


def prepare(directory):
    boot()
    directory = directory.resolve()
    if directory == ROOT or ROOT in directory.parents:
        raise ValueError("Raw measurements must be outside the repository")
    if (directory / "experiment.json").exists():
        raise ValueError("An existing experiment is immutable; resume it instead")
    cases = samples()
    config, provider = selected_profile()
    # Only producer fields enter context subprocesses. Oracles stay separate.
    for case in cases:
        write(directory / "oracles" / (case["case_id"] + ".json"), case)
        write(directory / "inputs" / (case["case_id"] + ".json"),
              {k: v for k, v in case.items() if k not in {"evaluation_reference", "acceptance", "supplements"}})
        if capability_traces(case['case_id']):
            write(directory / 'expected-traces' / (case['case_id']+'.json'), capability_traces(case['case_id']))
    write(directory / "profile.json", provider.profile)
    pairs = [(case['case_id'], interface, repeat) for repeat in range(1, 4)
             for case in cases for interface in ("api", "mcp")]
    random.Random(20261006).shuffle(pairs)
    schedule = []
    for index, (key, interface, repeat) in enumerate(pairs):
        for arm in (("baseline", "unified") if index % 2 == 0 else ("unified", "baseline")):
            schedule.append({"journey_id": f"{len(schedule)+1:02d}_{interface}_{key}_{arm}_{repeat}",
                             "case_id": key, "interface": interface, "arm": arm, "repeat": repeat})
    for case in cases:
        for interface in ("api", "mcp"):
            for arm in ("baseline", "unified"):
                for answered in ([False, True] if case["case_id"] == "delayed_start" else [False]):
                    output = directory / "packets" / f"{interface}_{case['case_id']}_{arm}{'_answered' if answered else ''}.json"
                    source = directory / "baseline" if arm == "baseline" else ROOT
                    command = [sys.executable, str(Path(__file__).resolve()), "context", "--source", str(source),
                        "--case", str(directory / "inputs" / (case["case_id"] + ".json")),
                        "--interface", interface, "--profile", str(directory / "profile.json"), "--output", str(output)]
                    if answered:
                        command.append("--answered")
                    result = subprocess.run(command, capture_output=True, timeout=120)
                    if result.returncode:
                        raise ValueError(result.stderr.decode("utf-8", errors="replace"))
    import tomllib
    external = tomllib.loads((Path(os.environ.get("USERPROFILE", "")) / ".codex/config.toml").read_text(encoding="utf-8"))
    write(directory / "experiment.json", {"started_at_utc": datetime.now(timezone.utc).isoformat(),
        "seed": 20261006, "schedule": schedule, "api_profile_id": PROFILE,
        "api_model": provider.profile["model"], "api_endpoint": provider.profile["baseUrl"],
        "external_configured_model": external.get("model"), "external_reasoning_effort": external.get("model_reasoning_effort"),
        "external_servers_to_disable": list(external.get("mcp_servers", {})),
        "retry": False, "repair": False, "native": False, "hardware": False,
        "examples": False, "global_profile_changed": False,
        "preconditions": "Only the complete three-zone alarm raw requirement enters Ladder Direct; no analysis or specification confirmation. External MCP creates one candidate, then the isolated lab compiles it through the shared Core and saves the artifact.",
        "benchmark_adapter": "Frozen context substitution only; both arms retain current application jobs, candidate Core and artifact persistence."})
    print(json.dumps({"prepared": len(schedule), "directory": str(directory)}, ensure_ascii=False), flush=True)


class NativeRequired(RuntimeError):
    pass


def no_native(model, baseline=None):
    from gxw.object_model import prepare_object_project
    from gxw.project_writer import write_prepared_project
    value = prepare_object_project(model, baseline=baseline)
    if value.requires_native_save:
        raise NativeRequired("Candidate requires native save; this contrast does not run GX Works2")
    return write_prepared_project(value)


class OneTransport:
    def __init__(self, provider):
        self.provider, self.calls = provider, 0

    def __getattr__(self, name):
        return getattr(self.provider, name)

    @property
    def observation_sink(self):
        return self.provider.observation_sink

    @observation_sink.setter
    def observation_sink(self, value):
        self.provider.observation_sink = value

    def stream(self, request):
        if self.calls:
            raise RuntimeError("Experiment prohibits another model call in the same job")
        self.calls += 1
        yield from self.provider.stream(replace(request, max_retries=0))


def freeze_unified(directory):
    """Save the reviewed source once; retain all previous preparation records."""
    directory = directory.resolve()
    experiment = read(directory / "experiment.json")
    if any((directory / task["journey_id"] / "journey.json").exists()
           for task in experiment["schedule"]):
        raise ValueError("Cannot change a contrast after live journeys have started")
    accepted = read(directory / "reference-accepted/reference-acceptance.json")
    if accepted.get("status") != "pass" or accepted.get("csv_acceptance", {}).get("status") != "pass":
        raise ValueError("The selected reference and its CSV must pass independent acceptance")
    destination = directory / "unified"
    if destination.exists():
        raise ValueError("A unified snapshot already exists; it must not be overwritten")
    for source in (directory / "baseline").iterdir():
        current = ROOT / source.name
        if source.is_dir():
            shutil.copytree(current, destination / source.name,
                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".git", "node_modules"))
        else:
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(current, destination / source.name)
    write(directory / "preparation-before-final-freeze.json", experiment)
    experiment.update(source_snapshots={"baseline": str(directory / "baseline"), "unified": str(destination)},
        primary_reference_case="multi_zone_alarm", final_reference_selected_at_utc=datetime.now(timezone.utc).isoformat(),
        context_preparation="Rebuilt for each actual journey/clarification from the frozen arm source; included in end-to-end machine time.",
        mcp_core="Current shared transport/Core/persistence; frozen arm context; isolated lab candidate acceptance after external generation.",
        examples=False)
    write(directory / "experiment.json", experiment)
    print(json.dumps({"source_frozen": str(destination), "live_calls": 0}), flush=True)


def refresh_packet(directory, task, *, answered=False):
    experiment = read(directory / "experiment.json")
    source = experiment["source_snapshots"][task["arm"]]
    path = directory / task["journey_id"] / "packets" / ("answered.json" if answered else "initial.json")
    command = [sys.executable, str(Path(__file__).resolve()), "context", "--source", source,
        "--case", str(directory / "inputs" / (task["case_id"] + ".json")), "--interface", task["interface"],
        "--profile", str(directory / "profile.json"), "--output", str(path)]
    if answered:
        command.append("--answered")
    start = time.perf_counter()
    result = subprocess.run(command, capture_output=True, timeout=180)
    elapsed = (time.perf_counter()-start)*1000
    write(path.with_suffix(".preparation.json"), {"command": command, "elapsed_ms": elapsed,
        "return_code": result.returncode, "stdout": result.stdout.decode("utf-8", errors="replace"),
        "stderr": result.stderr.decode("utf-8", errors="replace")})
    if result.returncode:
        raise ValueError("Frozen context preparation failed; inspect " + str(path.with_suffix(".preparation.json")))
    return path, {"answered": answered, "elapsed_ms": elapsed, "builder_ms": read(path)["context_ms"]}


def artifact_delivery(store, project_id, version_id, destination):
    """Copy the existing public artifact files into the measurement directory."""
    metadata = store.raw_version(project_id, version_id)
    destination.mkdir(exist_ok=True)
    for key in metadata.get("artifacts", {}):
        artifact = store.artifact(project_id, version_id, key)
        shutil.copyfile(artifact, destination / artifact.name)
    return metadata


def frozen_context_scope(stack, packet):
    import application.model_api as api
    import application.confirmed_generation_context as cg
    import application.fbd as fbd
    if "direct_context" in packet:
        context = cg.ConfirmedGenerationContext(**packet["direct_context"])
        stack.enter_context(patch.object(cg, "build_direct_generation_context", lambda *a, **k: context))
    if "system" in packet:
        def instructions(*a, **kwargs):
            if kwargs.get("on_context"):
                kwargs["on_context"](copy.deepcopy(packet.get("generation_handoff", {})))
            return packet["system"]
        stack.enter_context(patch.object(api, "build_generation_instructions", instructions))
    if "fbd_context" in packet:
        stack.enter_context(patch.object(fbd, "build_fbd_generation_context", lambda *a, **k: copy.deepcopy(packet["fbd_context"])))
    if "analysis_context" in packet:
        from knowledge.evidence import KnowledgeContext
        assembly = packet["analysis_context"]
        stack.enter_context(patch.object(api, "assemble_analysis_prompt", lambda *a, **k: SimpleNamespace(
            system_prompt=assembly["system_prompt"], contract_stage=assembly["contract_stage"],
            knowledge_context=KnowledgeContext(assembly["knowledge_context"], assembly["knowledge_manifest"]),
            route=SimpleNamespace(include_design=assembly["include_design"]))))
    stack.enter_context(patch.object(fbd, "_write_candidate", no_native))


def measured_job(journey, command, task):
    """Use the real job manager, retaining non-Ladder language artifacts too."""
    record = {"case_id": task["case_id"], "phase": command["kind"], "arm": task["arm"],
        "repeat": task["repeat"], "actual_requests": [], "retrieval": [], "attempts": [],
        "transport_errors": [], "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "submitted_command": copy.deepcopy(command)}
    journey.observed.attempts = record["attempts"]
    journey.current = record
    start = time.perf_counter()
    try:
        job = journey.service.submit(command)
        record["submission_ms"] = (time.perf_counter()-start)*1000
        while journey.service.jobs.get(job["id"])["status"] in {"queued", "running", "cancelling"}:
            if time.perf_counter()-start > 900:
                journey.service.jobs.cancel(job["id"])
                record["timeout"] = True
                break
            time.sleep(0.25)
        record["job"] = journey.service.jobs.get(job["id"])
        record["events"] = journey.service.jobs.events(job["id"])
        try:
            record["output"] = journey.service.output(job["id"])
        except KeyError:
            record["output"] = None
        version_id = (record["output"] or {}).get("version_id") or (record["job"].get("result") or {}).get("version_id")
        if version_id:
            record["version_id"] = version_id
            version = journey.service.projects.raw_version(command["project_id"], version_id)
            record["generation_handoff"] = version.get("generation_handoff")
            if version.get("target_mode", "ladder") == "ladder":
                record["program"] = journey.service.projects.program(command["project_id"], version_id)
    except Exception as error:
        record["runner_error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        record["wall_ms"] = (time.perf_counter()-start)*1000
        for attempt in record["attempts"]:
            attempt["effective_max_retries"] = 0
        journey.current = None
    job_id = (record.get("job") or {}).get("id", command["request_id"])
    write(journey.directory / "jobs" / (job_id + ".json"), record)
    return record


def capability_traces(case_id):
    """Independent, frozen user-visible expectations, without candidate state."""
    if case_id == 'multi_zone_alarm':
        frames, armed, memory, arming_at, alarm_at = [], False, [False]*3, None, None
        for t in range(0, 165101, 10):
            arm = 100 <= t < 10000 or 11000 <= t < 74000 or 75000 <= t < 101000 or 102000 <= t < 164000
            # Cancel a first arming attempt, retain transient and later faults,
            # disarm during entry delay, then complete another alarm cycle.
            zones = [not (4000 <= t < 4200 or 31100 <= t < 31200 or 95000 <= t < 95200 or t >= 123000),
                     not (50000 <= t < 50200), not (72000 <= t < 73000)]
            if not arm:
                armed, memory, arming_at, alarm_at = False, [False]*3, None, None
            else:
                if arming_at is None:
                    arming_at = t
                if t-arming_at >= 20000:
                    armed = True
                if armed:
                    for i, normal in enumerate(zones):
                        memory[i] = memory[i] or not normal
                    if any(memory) and alarm_at is None:
                        alarm_at = t
            age = t-alarm_at if alarm_at is not None else -1
            expected = {'Y0': armed, 'Y1': 10000 <= age < 40000, 'Y2': age >= 10000,
                        **{f'Y{i+3}': memory[i] for i in range(3)}}
            frames.append({'timestamp_ms': t, 'inputs': {'X1': arm, **{f'X{i+2}': zones[i] for i in range(3)}},
                           'expected': expected})
        return [{'id': 'alarm_cancel_memory_and_repeated_cycles', 'frames': frames}]
    if case_id == 'copy_initialize':
        frames, previous, src, dst = [], [False, False], list(range(-8, 8)), list(range(100, 116))
        for step, buttons in enumerate([(0,0), (1,0), (1,0), (0,0), (0,1), (0,1),
                                        (0,0), (1,1), (1,1), (0,0), (1,0), (0,0)]):
            if step in (2, 8):
                src = [v+1000 for v in src]  # external source change while held
            edges = [bool(buttons[i] and not previous[i]) for i in range(2)]
            if edges[1]:
                dst = [0]*16
            elif edges[0]:
                dst = src.copy()
            inputs = {'X0': buttons[0], 'X1': buttons[1], **{f'D{i+100}': src[i] for i in range(16)}}
            frames.append({'timestamp_ms': step*10, 'inputs': inputs,
                           'expected': {**{f'D{i+100}': src[i] for i in range(16)},
                                        **{f'D{i+200}': dst[i] for i in range(16)}}})
            previous = list(buttons)
        return [{'id': 'array_copy_edges_reset_priority_source_preservation', 'frames': frames,
                 'initial': {**{f'D{i+100}': i-8 for i in range(16)},
                             **{f'D{i+200}': i+100 for i in range(16)}}}]
    return []


def evaluate_capability_program(case, program, traces, native_instructions=None):
    """Reuse Core's bounded scanner; unknown execution stays unverified."""
    from plc.ir import ir_to_ladder, lower_rung_instructions, analyze_instruction_access
    from simulator.bounded import ScanMachine
    ladder = ir_to_ladder(program)
    lowered = native_instructions or [lower_rung_instructions(rung) for rung in ladder['rungs']]
    instructions = [[(ins, analyze_instruction_access(ins['op'], ins['args'], plc_model='FX3U'))
                     for ins in rung] for rung in lowered]
    unsupported = sorted({ins['op'] for rung in lowered for ins in rung}.intersection({'STL', 'RET', 'MC', 'MCR', 'CJ', 'JMP'}))
    results, checks = [], []
    output_names = {f'Y{i}' for i in range(6)}
    destination_names = {f'D{i+200}' for i in range(16)}
    source_names = {f'D{i+100}' for i in range(16)}

    def selected_observations(index, frame):
        t = frame['timestamp_ms']
        if case['case_id'] == 'copy_initialize':
            selectors = [destination_names if t in (10,100) else set(),
                         destination_names if t in (20,50,80) else set(),
                         destination_names if t in (40,70) else set(), source_names,
                         destination_names if t == 0 else set()]
        else:
            selectors = [{'Y3','Y4','Y5'}, {'Y0'},
                         {'Y1','Y2'} if 31100 <= t <= 41110 or 95000 <= t < 102000 else set(),
                         {'Y1'} if t >= 41100 else set(),
                         {'Y2','Y3','Y4','Y5'} if t >= 41100 else set(),
                         output_names if not frame['inputs']['X1'] or t >= 75000 else set()]
        return selectors[index]
    for trace in traces:
        initial = {name: False if name.startswith(('X', 'Y', 'M', 'S')) else 0 for name in program['devices']}
        initial.update({name+'.contact': False for name in program['devices'] if name.startswith(('T','C'))})
        initial.update({'X1': False, 'X2': True, 'X3': True, 'X4': True} if case['case_id'] == 'multi_zone_alarm' else {})
        initial.update(trace.get('initial', {}))
        machine = ScanMachine(ladder, plc_model='FX3U', initial=initial, previous_inputs=copy.deepcopy(initial),
                              instructions=instructions, execution_context={'program_type': 'scan'})
        failures, unknown, tolerated, previous, previous_inputs = [], [], 0, None, None
        criteria = [{'check': c, 'observations': 0, 'mismatch_count': 0, 'failures': []} for c in case['acceptance']]
        for i, frame in enumerate(trace['frames']):
            try:
                after = machine.scan({k:v for k,v in frame.items() if k != 'expected'}, first_scan=i == 0)['after']
                actual = {key: after.get(key) for key in frame['expected']}
                mismatch = {key: {'expected': value, 'actual': actual[key]} for key,value in frame['expected'].items()
                            if actual[key] is not None and actual[key] != value}
                # One fixed scan at an output transition, never a shifted oracle.
                if (mismatch and case['case_id'] == 'multi_zone_alarm' and previous is not None
                        and actual == previous and frame['expected'] != previous
                        and frame['inputs']['X1'] and frame['inputs'] == previous_inputs
                        and not set(mismatch).intersection({'Y3','Y4','Y5'})):
                    mismatch = {}
                    tolerated += 1
                if mismatch and len(failures) < 12:
                    failures.append({'timestamp_ms': frame['timestamp_ms'], 'mismatches': mismatch})
                for index, criterion in enumerate(criteria):
                    selected = selected_observations(index, frame)
                    criterion['observations'] += len(selected)
                    relevant = {key: value for key,value in mismatch.items() if key in selected}
                    if relevant:
                        criterion['mismatch_count'] += len(relevant)
                        if len(criterion['failures']) < 4:
                            criterion['failures'].append({'timestamp_ms': frame['timestamp_ms'], 'mismatches': relevant})
                if any(v is None for v in actual.values()):
                    unknown.append('unknown_observation')
                previous = frame['expected']
                previous_inputs = frame['inputs']
            except Exception as error:
                unknown.append(type(error).__name__ + ':' + str(error))
                break
        unknown.extend(str(gap.get('reason')) for gap in machine.gaps)
        status = 'unverified' if unknown or unsupported else 'fail' if failures else 'pass'
        results.append({'trace_id': trace['id'], 'status': status, 'scans': machine.scan_index,
                        'failures': failures, 'unknown_reasons': sorted(set(unknown)),
                        'unsupported_control': unsupported, 'boundary_tolerance_scans': tolerated})
        for criterion in criteria:
            criterion['status'] = ('unverified' if unknown or unsupported or not criterion['observations']
                                   else 'fail' if criterion['mismatch_count'] else 'pass')
        checks.extend(criteria)
    field_io = {row['address'] for row in case['io']}
    extra = sorted({name for name in program['devices'] if re.fullmatch(r'[XY]\d+', name)}-field_io)
    status = 'fail' if extra or any(r['status'] == 'fail' for r in results) else 'unverified' if any(r['status'] == 'unverified' for r in results) else 'pass'
    return {'status': status, 'checks': checks,
            'traces': results, 'extra_io': extra, 'method': 'existing Core scanner; independent frozen output traces',
            'native_execution': False, 'hardware_execution': False}


def api_journey(directory, task, provider):
    from scripts.benchmark_direct_generation import MeasuredJourney
    case = read(directory / "oracles" / (task["case_id"] + ".json"))
    path = directory / task["journey_id"]
    start = time.perf_counter()
    packet_path, preparation = refresh_packet(directory, task)
    packet = read(packet_path)
    record = {**task, "target_mode": packet["target_mode"], "jobs": [], "questions": [],
              "packet_files": [str(packet_path)], "context_preparation": [preparation]}
    bounded = OneTransport(provider)
    with MeasuredJourney(path, bounded) as journey, ExitStack() as stack:
        import application.model_api as api
        from application.workspace import read_json
        from storage.config import get_config_path
        config = read(get_config_path())
        config["activeModelProfileId"] = PROFILE
        stack.enter_context(patch.object(api, "load_full_config", lambda: copy.deepcopy(config)))
        project = journey.service.create_project(name=task["journey_id"], plc_model="FX3U", target_mode=packet["target_mode"])
        if packet["specification"]:
            reviewed = journey.service.set_spec(project["id"], packet["specification"], None)
            if not reviewed.get("valid"):
                raise ValueError(reviewed)
        for phase in range(2):
            bounded.calls = 0
            with ExitStack() as context_stack:
                frozen_context_scope(context_stack, packet)
                kind = "analysis" if phase == 0 and case["case_id"] == "delayed_start" else "direct_generation" if packet["target_mode"] == "ladder" else "generation"
                command = {"kind": kind, "project_id": project["id"], "request_id": f"{task['journey_id']}-phase-{phase}",
                    "text": packet["requirement"], "response_language": "zh-CN", "construction_examples": False,
                    "allow_analysis_repair": False}
                job = measured_job(journey, command, task)
            record["jobs"].append(job)
            output = job.get("output") or {}
            questions = output.get("missing_info") or (output.get("analysis") or {}).get("missing_info") or []
            record["questions"].extend(questions)
            write(path / "journey-progress.json", record)
            if phase == 0 and questions and case.get("clarification_answer"):
                packet_path, preparation = refresh_packet(directory, task, answered=True)
                packet = read(packet_path)
                record["packet_files"].append(str(packet_path))
                record["context_preparation"].append(preparation)
                review_start = time.perf_counter()
                confirmed = journey.service.set_spec(project["id"], packet["specification"], None)
                record["confirmation_check_ms"] = (time.perf_counter()-review_start)*1000
                record["confirmation"] = {"valid": confirmed.get("valid"), "basis": "independent frozen user facts; no implementation assumptions confirmed", "human_time_measured": False}
                if not confirmed.get("valid"):
                    break
                continue
            version_id = output.get("version_id")
            if version_id:
                version = artifact_delivery(journey.service.projects, project["id"], version_id, path / "delivery")
                record["metadata"] = version
                if version["target_mode"] == "ladder":
                    record["program"] = journey.service.projects.program(project["id"], version_id)
            break
    record["machine_total_ms"] = (time.perf_counter()-start)*1000
    record["model_calls"] = sum(len(j.get("actual_requests", [])) for j in record["jobs"])
    record["local_timings"] = [row for j in record["jobs"] for row in j.get("local_timings", [])]
    return record


def mcp_server(packet_path, directory):
    boot()
    import anyio
    from mcp.server.stdio import stdio_server
    from integrations.mcp.server import create_server
    from integrations.mcp.context_provider import StaticToolContextProvider
    from agent_runtime.plc_tools import build_tool_context, _create_program_candidate, _create_fbd_candidate
    from agent_runtime.runtime import build_default_tool_runtime, _public_value
    from agent_runtime.messages import ToolResult
    from application.fbd import unpack_candidate
    packet = read(packet_path)
    context = build_tool_context(packet["project"])
    runtime = build_default_tool_runtime()
    allowed = {"get_current_project", "get_generation_context", "search_plc_manual", "get_fbd_catalog",
               "read_fbd_project", "create_program_candidate", "create_fbd_candidate"}

    class Recorded:
        def __init__(self):
            self.observed = False
            self.submitted = False
            self.sequence = 0

        def list_tools(self, *a):
            return [t for t in packet.get("mcp_tools", runtime.list_tools(*a))
                    if t["function"]["name"] in allowed]

        def invoke(self, call, ctx):
            start = time.perf_counter()
            self.sequence += 1
            try:
                if call.name == "get_generation_context":
                    self.observed = True
                    data = copy.deepcopy(packet["context"])
                    data["generation_context_id"] = "frozen-measurement-context"
                    envelope = {"ok": True, "tool": call.name, "data": data}
                elif call.name in {"create_program_candidate", "create_fbd_candidate"}:
                    if self.submitted:
                        raise ValueError("Experiment forbids repair/repeated candidate submission")
                    self.submitted = True
                    handoff = copy.deepcopy(packet["context"].get("generation_handoff")) if self.observed else None
                    with patch("application.fbd._write_candidate", no_native):
                        data = (_create_program_candidate(ctx, call.arguments, generation_handoff=handoff)
                                if call.name == "create_program_candidate" else
                                _create_fbd_candidate(ctx, call.arguments, generation_handoff=handoff))
                    envelope = {"ok": True, "tool": call.name, "status": "confirmation_required", "data": data}
                    action = data["pending_action"]
                    write(directory / "candidate-action.json", action)
                    if "_fbd_candidate" in action:
                        unpack_candidate(action["_fbd_candidate"], directory / "delivery")
                else:
                    result = runtime.invoke(call, ctx)
                    envelope = result.data
            except Exception as error:
                envelope = {"ok": False, "tool": call.name, "error": {"code": type(error).__name__, "message": str(error)}}
            row = {"sequence": self.sequence, "tool": call.name, "arguments": call.arguments,
                   "result": envelope, "elapsed_ms": (time.perf_counter()-start)*1000}
            with (directory / "tools.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps(row, ensure_ascii=False) + "\n")
            return ToolResult(call.id, call.name, json.dumps(_public_value(envelope), ensure_ascii=False),
                              envelope, is_error=not envelope.get("ok"))

    async def serve():
        async with stdio_server() as (input_stream, output_stream):
            from integrations.mcp.server import SERVER_INSTRUCTIONS
            with patch("integrations.mcp.server.SERVER_INSTRUCTIONS",
                       packet.get("mcp_server_instructions", SERVER_INSTRUCTIONS)):
                server = create_server(StaticToolContextProvider(context), Recorded())
            await server.run(input_stream, output_stream, server.create_initialization_options())
    anyio.run(serve)


def mcp_journey(directory, task):
    from knowledge.evidence import estimate_tokens
    from storage.session import SessionStore
    from plc.core import PLCCore
    from plc.ir import ir_to_ladder
    path = directory / task["journey_id"]
    path.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    packet_path, preparation = refresh_packet(directory, task)
    packet = read(packet_path)
    case = read(directory / "oracles" / (task["case_id"] + ".json"))
    experiment = read(directory / "experiment.json")
    record = {**task, "target_mode": packet["target_mode"], "external_model": experiment["external_configured_model"],
              "model_calls": None, "questions": [], "turns": [], "packet_files": [str(packet_path)],
              "context_preparation": [preparation], "local_timings": []}
    for phase in range(2):
        config = {"command": sys.executable, "args": [str(Path(__file__).resolve()), "mcp-server",
                  "--packet", str(packet_path), "--directory", str(path)], "startup_timeout_sec": 60.0}
        overrides = [f'mcp_servers.{name}.enabled=false' for name in experiment["external_servers_to_disable"]]
        overrides += ["mcp_servers.gx_measure=" + json.dumps(config, ensure_ascii=False), "notify=[]"]
        # JSON objects are not TOML inline tables: serialize the explicit server
        # fields as TOML assignments; all strings pass through CLI args, no shell.
        overrides[-2] = "mcp_servers.gx_measure={command=" + json.dumps(config["command"]) + ",args=" + json.dumps(config["args"]) + ",startup_timeout_sec=60}"
        overrides.extend(["model=" + json.dumps(experiment["external_configured_model"]),
                          "model_reasoning_effort=" + json.dumps(experiment["external_reasoning_effort"]),
                          'model_provider="measurement-openai"',
                          'model_providers.measurement-openai={name="OpenAI",requires_openai_auth=true,'
                          'wire_api="responses",supports_websockets=true,supports_standalone_web_search=true,'
                          'request_max_retries=0,stream_max_retries=0}'])
        if experiment.get("isolated_mcp_tool_permission_authorized"):
            for tool in ("get_current_project", "get_generation_context", "search_plc_manual",
                         "get_fbd_catalog", "read_fbd_project", "create_program_candidate", "create_fbd_candidate"):
                overrides.append(f'mcp_servers.gx_measure.tools.{tool}.approval_mode="approve"')
        command = [shutil.which("codex") or "codex", "exec", "--json", "--ephemeral", "--skip-git-repo-check",
                   "--sandbox", "read-only", "-C", str(path), "-o", str(path / f"final-{phase}.txt")]
        for override in overrides:
            command.extend(["-c", override])
        prompt = ("只使用 gx_measure MCP 工具完成当前冻结PLC需求。先读取get_current_project和get_generation_context，按返回的语言、协议、事实及工程目录实现。"
                  "不使用文件、终端、网页、其他MCP或独立规划/审阅Agent。信息充分时提交一次create_program_candidate或create_fbd_candidate。"
                  "若真正缺工艺事实，直接简短提出问题，禁止提交程序。候选失败后立即报告实际错误，不修复、不重新提交。"
                  "FBD不支持的接口须报告unsupported，禁止近似实现。不得运行任何原生软件。最后简短报告实际工具结果。\n\n" + packet["requirement"])
        (path / f"prompt-{phase}.txt").write_text(prompt, encoding="utf-8")
        write(path / f"command-{phase}.json", command)
        turn_start = time.perf_counter()
        with (path / f"events-{phase}.jsonl").open("wb") as output, (path / f"stderr-{phase}.txt").open("wb") as error:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output, stderr=error)
            try:
                process.communicate(prompt.encode("utf-8"), timeout=900)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                record["timeout"] = True
        final_path = path / f"final-{phase}.txt"
        final = final_path.read_text(encoding="utf-8") if final_path.exists() else ""
        events = []
        for line in (path / f"events-{phase}.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        usage = [e.get("usage") for e in events if e.get("type") == "turn.completed"]
        record["turns"].append({"elapsed_ms": (time.perf_counter()-turn_start)*1000,
            "return_code": process.returncode, "final": final, "usage": usage or None,
            "prompt_estimate": estimate_tokens(prompt)})
        requested_fact = not (path / "candidate-action.json").exists() and bool(re.search(
            r"[？?]|(?:请|需要).{0,15}(?:提供|确认|说明)|多久|几秒|多少秒", final))
        necessary = case.get("category") == "missing_fact" and bool(re.search(r"延时|时长|几秒|多久|时间", final))
        if requested_fact:
            record["questions"].append({"question": final, "necessary": necessary,
                                        "classification": "local_text_screen_pending_review"})
        write(path / "journey-progress.json", record)
        if requested_fact and necessary and case.get("clarification_answer") and phase == 0:
            packet_path, preparation = refresh_packet(directory, task, answered=True)
            packet = read(packet_path)
            record["packet_files"].append(str(packet_path))
            record["context_preparation"].append(preparation)
            continue
        break
    if (path / "tools.jsonl").exists():
        record["tools"] = [json.loads(line) for line in (path / "tools.jsonl").read_text(encoding="utf-8").splitlines()]
    write(path / "journey-progress.json", record)
    action_path = path / "candidate-action.json"
    if action_path.exists():
        action = read(action_path)
        if "_candidate_ir" in action:
            store = SessionStore(base_dir=path / "workspace")
            project = store.create_project(task["journey_id"], plc_model="FX3U")
            version_id, out_dir = store.prepare_version(project["id"])
            local_start = time.perf_counter()
            result = PLCCore().compile_project(action["_candidate_ir"], out_dir,
                validation_profile=action.get("_validation_profile", "generation_structural"))
            record["local_timings"].append({"phase": "Core_IR_and_CSV_compile", "elapsed_ms": (time.perf_counter()-local_start)*1000})
            metadata = {**store._ir_metadata(action["_candidate_ir"]), "target_mode": "ladder", "plc_model": "FX3U",
                        "artifacts": result["artifacts"], "generation_handoff": action.get("_generation_handoff"),
                        "maintainability_review": action.get("maintainability_review")}
            store.complete_version(project["id"], version_id, metadata)
            destination = path / "delivery"
            destination.mkdir(exist_ok=True)
            for key, filename in result["artifacts"].items():
                shutil.copyfile(out_dir / filename, destination / Path(filename).name)
            record["program"] = action["_candidate_ir"]
            record["metadata"] = metadata
        elif "_fbd_candidate" in action:
            record["metadata"] = action["_fbd_candidate"]["metadata"]
    record["machine_total_ms"] = (time.perf_counter()-start)*1000
    record["tool_calls"] = len(record.get("tools", []))
    record["tool_rounds"] = None
    return record


def evaluate(directory, record):
    from scripts.benchmark_direct_generation import evaluate_program, evaluate_csv
    from gxworks2.csv_importer import parse_gxworks2_csv
    path = directory / record["journey_id"]
    case = read(directory / "oracles" / (record["case_id"] + ".json"))
    start = time.perf_counter()
    acceptance = {"status": "no_candidate", "checks": []}
    if record.get("program"):
        if case["case_id"] in {"motor_latch", "segd_countdown"}:
            acceptance = evaluate_program(case, record["program"])
        elif case['case_id'] in {'multi_zone_alarm', 'copy_initialize'}:
            traces = read(directory / 'expected-traces' / (case['case_id'] + '.json'))
            acceptance = evaluate_capability_program(case, record['program'], traces)
        else:
            acceptance = {"status": "unverified", "checks": [{"check": c, "status": "unverified"} for c in case["acceptance"]]}
    elif record.get("metadata"):
        acceptance = {"status": "unverified", "checks": [{"check": c, "status": "unverified"} for c in case["acceptance"]]}
    csv_path, comment_path = path / "delivery/program.csv", path / "delivery/comments.csv"
    readable = False
    if csv_path.exists():
        parsed = parse_gxworks2_csv(csv_path, comment_path if comment_path.exists() else None)
        readable = bool(parsed.ladder.get("rungs"))
        if case["case_id"] in {"motor_latch", "segd_countdown"}:
            acceptance["csv_acceptance"] = evaluate_csv(case, csv_path, comment_path)
        elif case['case_id'] in {'multi_zone_alarm', 'copy_initialize'}:
            from plc.ir import build_plc_ir
            acceptance['csv_acceptance'] = evaluate_capability_program(case, build_plc_ir(parsed.ladder, plc_model='FX3U'),
                read(directory / 'expected-traces' / (case['case_id'] + '.json')), parsed.network_instructions)
    record["acceptance"] = acceptance
    record["csv_readable"] = readable if record["target_mode"] == "ladder" else None
    record["correct_delivery"] = acceptance["status"] == "pass" and readable and acceptance.get("csv_acceptance", {}).get("status") == "pass"
    record["independent_acceptance_ms"] = (time.perf_counter()-start)*1000
    record["time_to_correct_delivery_ms"] = record["machine_total_ms"] + record["independent_acceptance_ms"] if record["correct_delivery"] else None
    record["failure_class"] = None if record["correct_delivery"] else "behavior_failure" if acceptance["status"] == "fail" else "behavior_unverified" if acceptance["status"] == "unverified" else "no_candidate"
    if acceptance['status'] == 'no_candidate':
        errors = [j.get('job', {}).get('error') or j.get('runner_error') for j in record.get('jobs', [])]
        errors += [t['result'].get('error') for t in record.get('tools', []) if not t['result'].get('ok')]
        record['candidate_errors'] = [e for e in errors if e]
        if any('NativeRequired' in str(e) for e in record['candidate_errors']):
            record['failure_class'] = 'native_save_required_not_performed'
        elif record['candidate_errors']:
            record['failure_class'] = 'candidate_or_job_rejected'
        elif record.get('questions'):
            record['failure_class'] = 'unresolved_input'
    record["actual_calls_after_first_failure"] = 0
    record['actual_wait_after_first_failure_ms'] = 0
    return record


def annotate(directory, record):
    from knowledge.evidence import estimate_tokens
    case = read(directory / 'oracles' / (record['case_id'] + '.json'))
    record.setdefault('target_mode', LANGUAGES[record['case_id']][0 if record['interface'] == 'api' else 1])
    questions = []
    for question in record.get('questions', []):
        text = str(question.get('question', question)) if isinstance(question, dict) else str(question)
        necessary = case.get('category') == 'missing_fact' and bool(re.search(r'延时|延迟|时长|几秒|多久|时间', text))
        questions.append({'question': text, 'necessary': necessary, 'basis': 'frozen task has only a missing delay duration' if necessary else 'frozen complete user facts'})
    record['question_review'] = questions
    record['assumption_review'] = {'status': 'partial_static_and_behavior_trace_coverage',
        'unexpected_io': record.get('acceptance', {}).get('extra_io'),
        'process_assumptions': 'unverified outside the recorded independent checks; no implementation choice was user-confirmed'}
    record['context_preparation_ms'] = sum(t['elapsed_ms'] for t in record.get('context_preparation', []))
    metrics = {'basis': 'local deterministic heuristic, separate from supplier usage',
        'candidate_briefs': 0, 'new_facts': 0, 'selection_policy': 0,
        'API_serialized_input': None, 'raw_program_output': None, 'visible_reasoning_text': None,
        'MCP_observed_tool_text': None, 'MCP_generation_context_text': None,
        'MCP_internal_context_replay': None}
    record['discovery'] = []
    for filename in record.get('packet_files', []):
        packet = read(filename)
        handoff = (packet.get('direct_context', {}).get('handoff') or packet.get('generation_handoff')
                   or packet.get('fbd_context', {}).get('generation_handoff')
                   or packet.get('context', {}).get('generation_handoff') or {})
        discovery = handoff.get('capability_discovery')
        if discovery:
            record['discovery'].append(discovery)
            for key in ('candidate_briefs', 'new_facts', 'selection_policy'):
                metrics[key] += discovery.get('tokens', {}).get(key, 0)
    attempts = [a for job in record.get('jobs', []) for a in job.get('attempts', [])]
    if record['interface'] == 'api':
        requests = [r for job in record.get('jobs', []) for r in job.get('actual_requests', [])]
        record.setdefault('model_calls', len(requests))
        metrics['API_serialized_input'] = sum(estimate_tokens(json.dumps(r.get('messages', []), ensure_ascii=False)) for r in requests)
        metrics['raw_program_output'] = sum(estimate_tokens(a['raw_content']) for a in attempts)
        metrics['visible_reasoning_text'] = sum(estimate_tokens(a['reasoning']) for a in attempts) if any(a['reasoning'] for a in attempts) else None
        record['supplier_usage'] = [a.get('usage') for a in attempts]
        record['model_wait_ms'] = sum(a['transport_ms'] for a in attempts if a.get('transport_ms') is not None) if attempts else None
    else:
        record['supplier_usage'] = [u for turn in record.get('turns', []) for u in (turn.get('usage') or [])]
        record['supplier_usage_scope'] = ('Codex CLI aggregate per external turn; preserve reported reasoning/cache fields. '
                                          'Individual model calls and unreported fields remain unavailable.')
        emitted = [t['arguments'].get('ladder') or t['arguments'].get('model') for t in record.get('tools', [])
                   if t['tool'] in ('create_program_candidate', 'create_fbd_candidate')]
        metrics['raw_program_output'] = sum(estimate_tokens(json.dumps(v, ensure_ascii=False)) for v in emitted) if emitted else None
        observed, context_text = [], []
        for filename in sorted((directory / record['journey_id']).glob('events-*.jsonl')):
            for line in filename.read_text(encoding='utf-8').splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                item = event.get('item') or {}
                if event.get('type') != 'item.completed' or item.get('type') != 'mcp_tool_call':
                    continue
                for content in (item.get('result') or {}).get('content', []):
                    if content.get('type') == 'text':
                        observed.append(estimate_tokens(content.get('text') or ''))
                        if item.get('tool') == 'get_generation_context':
                            context_text.append(observed[-1])
        metrics['MCP_observed_tool_text'] = sum(observed) if observed else None
        metrics['MCP_generation_context_text'] = sum(context_text) if context_text else None
    record['local_token_estimates'] = metrics
    review = record.get('metadata', {}).get('maintainability_review') or {}
    if not review:
        for job in record.get('jobs', []):
            generation = (job.get('output') or {}).get('generation') or {}
            if generation.get('maintainability_review'):
                review = generation['maintainability_review']
                record['review_source'] = 'original_job_output; saved-version forwarding gap recorded separately'
                break
    record['maintainability'] = {'used_capabilities': review.get('used_capabilities'),
        'findings': review.get('findings'), 'coverage': review.get('coverage'), 'model_calls': review.get('model_calls')}
    record.setdefault('actual_wait_after_first_failure_ms', 0)
    record['timing_scope'] = 'End-to-end machine time includes source-context subprocess, model/tool wait, candidate checks and persistence; independent acceptance is separate. Local sub-spans may overlap.'
    return record


def run(directory, limit=None):
    boot()
    if any(task['case_id'] != 'multi_zone_alarm' for task in read(directory / 'experiment.json')['schedule']):
        raise ValueError('本轮在线对照只允许三回路报警系统；其他案例须从运行清单移除并保留历史记录。')
    _, provider = selected_profile()
    if provider.profile != read(directory / "profile.json"):
        raise ValueError("The saved provider parameters changed after the experiment was frozen")
    if "source_snapshots" not in read(directory / "experiment.json"):
        raise ValueError("Freeze the final reviewed source before starting live journeys")
    done = 0
    for task in read(directory / "experiment.json")["schedule"]:
        path = directory / task["journey_id"] / "journey.json"
        if path.exists():
            continue
        start = time.perf_counter()
        record = {**task}
        try:
            record.update(api_journey(directory, task, provider) if task["interface"] == "api" else mcp_journey(directory, task))
            record = evaluate(directory, record)
        except Exception as error:
            draft = directory / task["journey_id"] / "journey-progress.json"
            if draft.exists():
                record.update(read(draft))
            record.update({"failure_class": type(error).__name__, "error": str(error),
                      "machine_total_ms": (time.perf_counter()-start)*1000, "correct_delivery": False,
                      "actual_calls_after_first_failure": 0})
        record = annotate(directory, record)
        write(path, record)
        print(json.dumps({"journey": task["journey_id"], "outcome": record.get("failure_class"),
                          "correct": record.get("correct_delivery"), "elapsed_s": round(record["machine_total_ms"]/1000, 2)}, ensure_ascii=False), flush=True)
        done += 1
        if limit and done >= limit:
            break


def preflight(directory):
    """Check the real stdio framing and source context without any model call."""
    boot()
    import anyio
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.client.session import ClientSession
    task = {"journey_id": "offline-preflight", "case_id": "multi_zone_alarm", "interface": "mcp", "arm": "unified"}
    packet, timing = refresh_packet(directory, task)
    path = directory / task["journey_id"]

    async def exercise():
        params = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).resolve()), "mcp-server",
            "--packet", str(packet), "--directory", str(path)])
        async with stdio_client(params) as (incoming, outgoing):
            async with ClientSession(incoming, outgoing) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                project = await session.call_tool("get_current_project", {})
                context = await session.call_tool("get_generation_context", {"user_requirement": read(packet)["requirement"]})
                if context.is_error or project.is_error:
                    raise ValueError("MCP preflight tool failed")
                result = json.loads(context.content[0].text)
                if result["data"]["generation_request"] != read(packet)["requirement"]:
                    raise ValueError("MCP changed the frozen raw requirement")
                return {"server": initialized.server_info.name, "tools": [t.name for t in tools.tools],
                    "project_ok": not project.is_error, "context_ok": not context.is_error,
                    "discovery_model_calls": result["data"]["generation_handoff"]["capability_discovery"]["model_calls"],
                    "context_preparation": timing, "live_model_calls": 0}
    result = anyio.run(exercise)
    write(directory / "offline-preflight.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


def report_record(directory, original, bootstrap_records):
    """Derived inspection only; first journey records and programs stay intact."""
    record = annotate(directory, copy.deepcopy(original))
    path = directory / record['journey_id']
    errors = []
    for job in record.get('jobs', []):
        state = job.get('job') or {}
        if state.get('error_code'):
            errors.append({'code': state['error_code'], 'details': state.get('error_details')})
    diagnostics = []
    for filename in (path / 'state').rglob('*.jsonl'):
        if 'diagnostics' not in filename.parts:
            continue
        for line in filename.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if row.get('event') == 'workflow_exception':
                diagnostics.extend(row.get('exceptions', []))
    record['recorded_job_errors'] = errors
    record['recorded_exception_types'] = sorted({row['error_type'] for row in diagnostics})
    record['artifact_saved'] = bool(record.get('metadata'))
    from plc.maintainability import review_maintainability
    source = record.get('program')
    if record['target_mode'] == 'st' and (path / 'delivery/program.st').exists():
        source = {'st_code': (path / 'delivery/program.st').read_text(encoding='utf-8')}
    elif record['target_mode'] == 'fbd' and (path / 'delivery/fbd.json').exists():
        source = read(path / 'delivery/fbd.json')
    if source is not None:
        review = review_maintainability(source, target_mode=record['target_mode'])
        record['maintainability'] = {k: review[k] for k in ('used_capabilities', 'findings', 'coverage', 'model_calls')}
        record['comparison_review_scope'] = 'Same deterministic post-hoc local review for both arms; original version metadata unchanged.'
    # A blocked client turn does not contain a generated PLC candidate. Keep
    # that distinction, but do not discard its actual extra time or usage.
    lineage = [row for row in bootstrap_records
               if (row['case_id'], row['interface'], row['arm'], row['repeat']) ==
               (record['case_id'], record['interface'], record['arm'], record['repeat'])]
    record['client_bootstrap_records'] = [row['journey_id'] for row in lineage]
    record['extra_client_turns'] = sum(len(row.get('turns', [])) for row in lineage)
    record['extra_client_machine_ms'] = sum(row['machine_total_ms'] for row in lineage)
    record['active_machine_with_client_bootstrap_ms'] = record['machine_total_ms'] + record['extra_client_machine_ms']
    record['correct_delivery_with_client_bootstrap_ms'] = (
        record['time_to_correct_delivery_ms'] + record['extra_client_machine_ms']
        if record.get('time_to_correct_delivery_ms') is not None else None)
    return record


def report(directory):
    boot()
    experiment = read(directory / 'experiment.json')
    bootstrap_records = [read(row['path']) for row in experiment.get('client_blocked_bootstrap_attempts', [])]
    records = [report_record(directory, read(directory / task['journey_id'] / 'journey.json'), bootstrap_records)
               for task in experiment['schedule']
               if (directory / task['journey_id'] / 'journey.json').exists()]
    excluded_records = []
    for filename in sorted(directory.glob('*/journey.json')):
        row = read(filename)
        if row.get('case_id') == 'multi_zone_alarm':
            continue
        excluded_records.append({'journey_id': row['journey_id'], 'case_id': row['case_id'],
            'interface': row['interface'], 'arm': row['arm'], 'repeat': row['repeat'],
            'failure_class': row.get('failure_class'), 'machine_total_ms': row.get('machine_total_ms'),
            'correct_delivery': row.get('correct_delivery'), 'model_calls': row.get('model_calls'),
            'tool_calls': row.get('tool_calls'),
            'supplier_usage': row.get('supplier_usage') or [u for turn in row.get('turns', []) for u in (turn.get('usage') or [])] or None,
            'record': str(filename), 'excluded_reason': '用户收窄为仅三回路报警系统；保留原始记录。'})
    interruptions = [read(filename) for filename in sorted(directory.glob('*/interruption.json'))]
    write(directory / 'analysis-projection.json', records)
    groups = []
    def span(values):
        known = [v for v in values if isinstance(v, (int,float))]
        return {'known': len(known), 'min': min(known), 'median': statistics.median(known), 'max': max(known)} if known else None
    def supplier_tokens(row, field):
        values = []
        for usage in row.get('supplier_usage') or []:
            if not isinstance(usage, dict):
                return None
            if field == 'reasoning_tokens':
                value = usage.get(field, usage.get('reasoning_output_tokens'))
            elif field == 'cached_input_tokens':
                raw = usage.get('raw_usage') or {}
                value = usage.get(field, raw.get('prompt_cache_hit_tokens'))
            else:
                value = usage.get(field)
            if not isinstance(value, (int, float)):
                return None
            values.append(value)
        return sum(values) if values else None
    for case_id in dict.fromkeys(task['case_id'] for task in experiment['schedule']):
        for interface in ('api','mcp'):
            for arm in ('baseline','unified'):
                rows = [r for r in records if (r['case_id'],r['interface'],r['arm']) == (case_id,interface,arm)]
                groups.append({'case_id': case_id, 'interface': interface, 'arm': arm, 'journeys': len(rows),
                    'language': LANGUAGES[case_id][0 if interface == 'api' else 1],
                    'correct_deliveries': sum(r.get('correct_delivery',False) for r in rows),
                    'saved_artifacts': sum(r['artifact_saved'] for r in rows),
                    'behavior_pass': sum(r.get('acceptance',{}).get('status') == 'pass' for r in rows),
                    'behavior_fail': sum(r.get('acceptance',{}).get('status') == 'fail' for r in rows),
                    'behavior_unverified': sum(r.get('acceptance',{}).get('status') == 'unverified' for r in rows),
                    'outcomes': [r.get('failure_class') or 'verified_delivery' for r in rows],
                    'machine_total_ms': span([r.get('machine_total_ms') for r in rows]),
                    'machine_with_client_bootstrap_ms': span([r['active_machine_with_client_bootstrap_ms'] for r in rows]),
                    'correct_delivery_ms': span([r.get('time_to_correct_delivery_ms') for r in rows]),
                    'necessary_questions': sum(q['necessary'] for r in rows for q in r.get('question_review',[])),
                    'unnecessary_questions': sum(not q['necessary'] for r in rows for q in r.get('question_review',[])),
                    'repetition_findings': sum(len(r.get('maintainability',{}).get('findings') or []) for r in rows),
                    'model_calls': [r.get('model_calls') for r in rows],
                    'candidate_brief_estimates': span([r.get('local_token_estimates',{}).get('candidate_briefs') for r in rows]),
                    'new_fact_estimates': span([r.get('local_token_estimates',{}).get('new_facts') for r in rows]),
                    'serialized_API_input_estimates': span([r.get('local_token_estimates',{}).get('API_serialized_input') for r in rows]),
                    'program_output_estimates': span([r.get('local_token_estimates',{}).get('raw_program_output') for r in rows]),
                    'visible_reasoning_estimates': span([r.get('local_token_estimates',{}).get('visible_reasoning_text') for r in rows]),
                    'selection_policy_estimates': span([r.get('local_token_estimates',{}).get('selection_policy') for r in rows]),
                    'MCP_observed_tool_text_estimates': span([r.get('local_token_estimates',{}).get('MCP_observed_tool_text') for r in rows]),
                    'MCP_generation_context_text_estimates': span([r.get('local_token_estimates',{}).get('MCP_generation_context_text') for r in rows]),
                    'failures_followed_by_calls': sum(r.get('actual_calls_after_first_failure', 0) for r in rows),
                    'actual_wait_after_first_failure_ms': sum(r.get('actual_wait_after_first_failure_ms', 0) for r in rows),
                    'supplier_token_statistics': {field: span([supplier_tokens(r, field) for r in rows])
                        for field in ('input_tokens', 'output_tokens', 'reasoning_tokens', 'cached_input_tokens')},
                    'supplier_usage': [r.get('supplier_usage') for r in rows]})
    result = {'expected_journeys': len(experiment['schedule']), 'recorded_journeys': len(records),
        'complete': len(records) == len(experiment['schedule']), 'groups': groups,
        'primary_reference': [g for g in groups if g['case_id'] == 'multi_zone_alarm'],
        'client_blocked_bootstrap_attempts': experiment.get('client_blocked_bootstrap_attempts', []),
        'scope_correction': experiment.get('scope_correction'),
        'excluded_case_records': excluded_records,
        'interrupted_excluded_records': interruptions,
        'excluded_pilot_records': experiment.get('pilot_records'),
        'order_deviation': experiment.get('order_deviation'),
        'integration_review_persistence_correction': experiment.get('integration_review_persistence_correction'),
        'local_ST_review_extension': experiment.get('local_ST_review_extension'),
        'local_FBD_review_extension': experiment.get('local_FBD_review_extension'),
        'limitations': ['未执行原生软件或真实PLC设备。',
            '在线对照仅FX3U Ladder报警系统；ST/FBD共享入口完成离线回归，不据此宣称在线生成效果。',
            '未测人工阅读、审批等待或操作时间。',
            'API与MCP的模型和工具流程不同，分别报告，不能直接配对比较性能。',
            '供应商未返回的数据保持缺失；本地估算不是计费token。',
            'MCP逐次模型消息、模型调用数和准确的内部上下文重放不可见；保留CLI提示、事件、工具输入输出及供应商返回的总推理和缓存用量。',
            '其他案例、审批引导和中断记录单独归档，不构成报警行为证据。',
            '首次失败后不重试、不修复；充分事实任务没有补充续答。',
            '假设审阅覆盖记录的接口与行为观测，其他工艺假设保持未验证。',
            '未控制供应商历史缓存，三次样本不建立统计显著性，也不推广至其他型号、任务或设备运行。']}
    write(directory / 'summary.json',result)
    lines = ['# 三回路报警系统：统一能力发现、指令选择与可维护性审阅对照', '',
        f"已记录 {len(records)}/{len(experiment['schedule'])} 个旅程。本轮在线样本仅三菱三回路报警系统。",
        'API、MCP 各比较基线与统一上下文，每组重复三次，共12次；沿用固定种子20261006原清单中的报警子序列，串行执行并交替组别。首次失败保持原样。',
        '基线冻结当前工作树的已有改动；两组共用当前Core、IR、compact协议和CSV交付。发现与本地审阅不调用模型。参考程序仅交给评估侧。', '',
        '|案例|接口|语言|组别|行为通过/失败/未验证|保存产物/正确交付|机器耗时中位数（秒）|简表/新增事实估算中位数|',
        '|---|---|---|---|---|---|---|---|']
    for g in groups:
        def median(key):
            value=g[key]
            return f"{value['median']:.1f}" if value else '缺失'
        seconds=f"{g['machine_total_ms']['median']/1000:.2f}" if g['machine_total_ms'] else '缺失'
        lines.append(f"|{g['case_id']}|{g['interface']}|{g['language']}|{g['arm']}|{g['behavior_pass']}/{g['behavior_fail']}/{g['behavior_unverified']}|{g['saved_artifacts']}/{g['correct_deliveries']}（{g['journeys']}次）|{seconds}|{median('candidate_brief_estimates')}/{median('new_fact_estimates')}|")
    lines += ['', '结构/协议拒绝没有行为判定，不并入行为通过数。正确交付要求IR行为与实际CSV行为共同通过。', '',
              result.get('order_deviation') or '',
              'summary.json 保留原始分项耗时及三次最小值、中位数、最大值；只有正确交付计入取得正确CSV耗时。人工阅读、审批等待和操作时间未测。']
    lines += ['', '## 全部报警旅程', '', '|旅程|首次结果|机器耗时（秒）|独立验收（秒）|必要/多余提问|模型调用/工具调用|记录/CSV|', '|---|---|---|---|---|---|---|']
    for r in records:
        q=r.get('question_review',[])
        model_calls = r.get('model_calls') if r.get('model_calls') is not None else '缺失'
        tool_calls = r.get('tool_calls') if r.get('tool_calls') is not None else '不适用'
        evaluation = f"{r['independent_acceptance_ms']/1000:.2f}" if r.get('independent_acceptance_ms') is not None else '缺失'
        links = f"[原始记录]({r['journey_id']}/journey.json)"
        if (directory / r['journey_id'] / 'delivery/program.csv').exists():
            links += f" / [CSV]({r['journey_id']}/delivery/program.csv)"
        lines.append(f"|{r['journey_id']}|{r.get('failure_class') or '行为与CSV通过'}|{r['machine_total_ms']/1000:.2f}|{evaluation}|{sum(x['necessary'] for x in q)}/{sum(not x['necessary'] for x in q)}|{model_calls}/{tool_calls}|{links}|")
    lines += ['', '## 计量与审阅', '',
              '候选简表、新增事实、选择要求、API完整输入、程序输出和可见推理文本使用本地估算；供应商input/output/reasoning/cache用量原样另列，未返回的数据保持缺失。MCP内部上下文重放和逐次模型请求不可见，不用工具次数代替模型次数。',
              'MCP另估算CLI实际收到的工具文本和生成上下文文本，不把这些可见文本的和当作模型内部重放或供应商计费输入。',
              '供应商缓存状态原样记录，未控制供应商历史缓存；三次结果只报告观测波动，不作统计显著性或其他任务效果推断。',
              '两组都以同一确定性本地审阅检查实际使用能力和重复位置，保留原版本元数据。发现候选不构成必须使用的指令清单；重复建议不证明可以等价替换，也不改变行为验收结果。',
              '每份acceptance保存六项要求的观测数、状态、首个反例、未知行为和CSV复核。仅稳定输入下的计时输出边界允许最多一扫描偏差，撤防和回路记忆要求同一扫描。未授权工艺假设仅覆盖记录的I/O和行为断言，其他假设保持未验证。',
              '本轮首次失败后不重试、不修复、不重新生成；实际追加调用和等待为0，不用后续成功覆盖首次结果。']
    lines += ['', '|接口|组别|供应商输入/输出/推理/缓存输入中位数|API完整输入/程序输出估算中位数|重复位置建议总数|',
              '|---|---|---|---|---|']
    for g in groups:
        values = g['supplier_token_statistics']
        supplier = '/'.join(str(values[k]['median']) if values[k] else '缺失'
                            for k in ('input_tokens', 'output_tokens', 'reasoning_tokens', 'cached_input_tokens'))
        estimated = '/'.join(str(g[k]['median']) if g[k] else '缺失'
                             for k in ('serialized_API_input_estimates', 'program_output_estimates'))
        lines.append(f"|{g['interface']}|{g['arm']}|{supplier}|{estimated}|{g['repetition_findings']}|")
    lines += ['', '## 范围偏离归档', '',
              f"停止前的其他案例保留 {len(excluded_records)} 个旅程记录，其中 {len(result['client_blocked_bootstrap_attempts'])} 个是工具审批引导；另保留 {len(interruptions)} 个中断记录。全部排除报警效果统计。前期pilot与原72项清单也保留。中断没有完整用量与耗时，保持缺失。",
              '原清单见sequence-before-alarm-only-correction.json，排除记录路径与分类见summary.json；这些记录不能证明三回路报警系统的效果。']
    lines += ['', '## 限制', '', *['- '+v for v in result['limitations']], '',
              '详细分项验收、实际请求/响应、检索、产物、供应商用量和估算保存在每个旅程目录；三次耗时范围及全部用量保存在 summary.json。']
    (directory / 'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'recorded':len(records),'expected':len(experiment['schedule']),'report':str(directory/'report.md')},ensure_ascii=False),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "freeze-unified", "preflight", "run", "report"):
        sub = commands.add_parser(name)
        sub.add_argument("--directory", type=Path, required=True)
        if name == "run":
            sub.add_argument("--limit", type=int)
    context = commands.add_parser("context")
    context.add_argument("--source", type=Path, required=True)
    context.add_argument("--case", type=Path, required=True)
    context.add_argument("--interface", choices=("api", "mcp"), required=True)
    context.add_argument("--profile", type=Path, required=True)
    context.add_argument("--output", type=Path, required=True)
    context.add_argument("--answered", action="store_true")
    server = commands.add_parser("mcp-server")
    server.add_argument("--packet", type=Path, required=True)
    server.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "context":
        write(args.output, context_packet(args.source, read(args.case), args.interface,
                                         answered=args.answered, profile=read(args.profile)))
    elif args.command == "prepare":
        prepare(args.directory)
    elif args.command == "freeze-unified":
        freeze_unified(args.directory)
    elif args.command == "preflight":
        preflight(args.directory)
    elif args.command == "report":
        report(args.directory)
    elif args.command == "mcp-server":
        mcp_server(args.packet, args.directory)
    else:
        run(args.directory, args.limit)


if __name__ == "__main__":
    main()
