"""Paired Agent-B experiments through the real application path.

Without --live/--preflight this prints a plan without loading credentials.
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
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
ARMS = ("manual_text", "usage_bound", "oracle", "legacy_retrieval", "automatic")
PAIRED_ARMS = ("manual_text", "usage_bound")
FACTORIAL_ARMS = ("manual_text_no_relations", "usage_bound_no_relations", *PAIRED_ARMS)
ARMS = (*ARMS, *FACTORIAL_ARMS[:2])
RELATION_DIMENSIONS = ("operation.result_mapping", "execution.disabled_retention")


def _without_candidate_slots(slots):
    from plc.instruction_semantics import operand_usage_status
    slots = copy.deepcopy(slots)
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        slot["usage_facts"] = [fact for fact in slot.get("usage_facts", [])
                               if fact.get("status") != "candidate_evidence"]
        slot.pop("usage_conflicts", None)
        slot["purpose_status"] = operand_usage_status(slot["usage_facts"], "purpose")
        if not slot["usage_facts"]:
            slot.pop("usage_facts")
    return slots


def without_candidate_usage(text):
    """Experiment-only ablation; leave every manual byte and other lane intact."""
    def replace(match):
        value = json.loads(match[1])
        value["slots"] = _without_candidate_slots(value["slots"])
        return "OPERAND_SEMANTICS: " + json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return re.sub(r"(?m)^OPERAND_SEMANTICS: (\{[^\r\n]+\})", replace, str(text))


def manual_text_context(context):
    """Ablate bindings after common retrieval, including its detached receipt."""
    from knowledge.evidence import KnowledgeContext, context_manifest, text_sha256
    text = without_candidate_usage(context)
    manifest = context_manifest(context)
    removed = {}
    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if "operand_slots" in value:
                value["operand_slots"] = _without_candidate_slots(value["operand_slots"])
            bindings = value.get("operand_evidence_bindings", [])
            dimensions = {f"operand:{b['position']}:{b['facet']}" for b in bindings if b.get("fact")}
            if dimensions:
                removed.setdefault(str(value.get("id")), set()).update(dimensions)
                value["operand_evidence_bindings"] = [b for b in bindings if not b.get("fact")]
                value["fact_dimensions"] = [d for d in value.get("fact_dimensions", []) if d not in dimensions]
            value.pop("operand_candidate_conflicts", None)
            for item in list(value.values()):
                visit(item)
    visit(manifest)
    # Keep the existing complete-block delivery accounting honest after changing
    # only a structured prefix. These are existing receipt fields, not new IDs.
    blocks = {json.loads(m[1])["id"]: m[2] for m in re.finditer(
        r"(?ms)^\[KNOWLEDGE (\{[^\n]+\})\]\n(.*?)\n\[/KNOWLEDGE\]", text)}
    def receipts(value):
        if isinstance(value, list):
            for item in value:
                receipts(item)
        elif isinstance(value, dict):
            identity = str(value.get("id"))
            if identity in blocks and "content_sha256" in value:
                value["content_sha256"] = text_sha256(blocks[identity])
            if identity in removed and "dimensions" in value:
                value["dimensions"] = [d for d in value["dimensions"] if d not in removed[identity]]
            if "candidate_source_ids" in value:
                value["candidate_source_ids"] = [identity for identity in value["candidate_source_ids"]
                    if value.get("dimension") not in removed.get(identity, ())]
            for item in value.values():
                receipts(item)
    receipts(manifest)
    manifest["context_sha256"] = text_sha256(text)
    manifest["benchmark_ablation"] = "candidate_usage_only"
    return KnowledgeContext(text, manifest)


def _wire_without_usage(params):
    value = copy.deepcopy(params)
    for message in value.get("messages", []):
        if isinstance(message.get("content"), str):
            message["content"] = without_candidate_usage(message["content"])
    return value


def without_relation_evidence(text):
    """Experiment-only removal of the entire atomic manual relation group."""
    return re.sub(r'(?ms)(?:\n\n)?^\[RELATION EVIDENCE \{[^\n]+\}\]\n.*?^\[/RELATION EVIDENCE\]', '', str(text))


def without_relation_context(context):
    from knowledge.evidence import KnowledgeContext, context_manifest, text_sha256
    from knowledge.fact_coverage import included_evidence_ids, reconcile_fact_coverage
    text = without_relation_evidence(context)
    manifest = context_manifest(context)
    blocks = {json.loads(m[1])["id"]: m[2] for m in re.finditer(
        r"(?ms)^\[KNOWLEDGE (\{[^\n]+\})\]\n(.*?)\n\[/KNOWLEDGE\]", text)}
    def visit(value):
        if isinstance(value, list):
            for item in value: visit(item)
        elif isinstance(value, dict):
            if "relation_evidence_groups" in value: value["relation_evidence_groups"] = []
            if "fact_group_members" in value:
                value["fact_group_members"] = {d:m for d,m in value["fact_group_members"].items() if d not in RELATION_DIMENSIONS}
            for key in ("dimensions", "fact_dimensions"):
                if key in value: value[key] = [d for d in value[key] if d not in RELATION_DIMENSIONS]
            if value.get("dimension", value.get("question")) in RELATION_DIMENSIONS:
                value.update(candidate_source_ids=[], source_ids=[], status="unresolved")
            if value.get("id") in blocks and "content_sha256" in value:
                value["content_sha256"] = text_sha256(blocks[value["id"]])
            for item in list(value.values()): visit(item)
    visit(manifest)
    if "fact_coverage" in manifest:
        manifest["fact_coverage"] = reconcile_fact_coverage(
            manifest["fact_coverage"], included_evidence_ids(text, manifest["fact_coverage"].get("records", [])))
    manifest.update(context_sha256=text_sha256(text), benchmark_relation_ablation=True)
    return KnowledgeContext(text, manifest)


def _wire_without_factors(params):
    value = _wire_without_usage(params)
    for message in value.get("messages", []):
        if isinstance(message.get("content"), str): message["content"] = without_relation_evidence(message["content"])
    return value


def preflight_factorial(cases, *, provider, model=None, evidence_cache=None):
    blocks = []
    for case in cases:
        records = [run_case(case, arm, provider=PreviewProvider(provider), model=model,
                            evidence_cache=evidence_cache) for arm in FACTORIAL_ARMS]
        if any(len(row["actual_requests"]) != 1 for row in records):
            raise ValueError(f"{case['case_id']}: factorial preflight requires one final request")
        requests = [row["actual_requests"][0] for row in records]
        normalized = [_wire_without_factors(r) for r in requests]
        if any(r != normalized[0] for r in normalized):
            raise ValueError(f"{case['case_id']}: content outside the two factors differs")
        counts = [_candidate_count(r) for r in requests]
        relation_counts = [sum(str(m.get('content','')).count('[RELATION EVIDENCE ') for m in r['messages']) for r in requests]
        if counts != [0,3,0,3] or relation_counts != [0,0,1,1]:
            raise ValueError(f"{case['case_id']}: invalid factor delivery {counts} / {relation_counts}")
        examples = [row['handoff']['construction_examples'] for row in records]
        if any(e != examples[0] for e in examples): raise ValueError('Construction examples differ')
        blocks.append({"case_id":case['case_id'], "other_request_content_identical":True,
                       "candidate_counts":dict(zip(FACTORIAL_ARMS,counts)),
                       "relation_counts":dict(zip(FACTORIAL_ARMS,relation_counts)), "records":records})
    return {"network_calls":0, "passed":True, "factorial_blocks":blocks}


def _candidate_count(params):
    count = 0
    for message in params.get("messages", []):
        for match in re.finditer(r"(?m)^OPERAND_SEMANTICS: (\{[^\r\n]+\})", str(message.get("content", ""))):
            count += sum(fact.get("status") == "candidate_evidence"
                         for slot in json.loads(match[1])["slots"] for fact in slot.get("usage_facts", []))
    return count


class PreviewProvider:
    """Use the saved provider's exact request resolver, with no transport call."""
    def __init__(self, provider):
        self.provider = provider
        self.profile, self.api_key = provider.profile, provider.api_key
        self.observation_sink = None

    def stream(self, request):
        from model_runtime.provider import TextDelta
        params = self.provider._request_params(request)
        if self.observation_sink:
            self.observation_sink(params, [], None)
        yield TextDelta('{"r":[]}')


def preflight_pairs(cases, *, provider, model=None, evidence_cache=None):
    rows = []
    for case in cases:
        pair = [run_case(case, arm, provider=PreviewProvider(provider), model=model,
                         evidence_cache=evidence_cache) for arm in PAIRED_ARMS]
        if any(len(record["actual_requests"]) != 1 for record in pair):
            raise ValueError(f"{case['case_id']}: preflight needs exactly one final request per arm")
        raw, bound = (record["actual_requests"][0] for record in pair)
        same = _wire_without_usage(raw) == _wire_without_usage(bound)
        counts = [_candidate_count(request) for request in (raw, bound)]
        control = case.get("evaluation", {}).get("kind") == "control"
        if not same or counts[0] or (not control and not counts[1]):
            raise ValueError(f"{case['case_id']}: invalid usage-only contrast (same_other_content={same}, counts={counts})")
        if pair[0].get("handoff", {}).get("construction_examples") != pair[1].get("handoff", {}).get("construction_examples"):
            raise ValueError(f"{case['case_id']}: construction examples differ")
        rows.append({"case_id": case["case_id"], "other_request_content_identical": same,
                     "candidate_counts": dict(zip(PAIRED_ARMS, counts)), "records": pair})
    return {"network_calls": 0, "passed": True, "pairs": rows}


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
        if case.get("evaluation", {}).get("kind") == "operand_mapping":
            def unfilled(value):
                if isinstance(value, dict):
                    if value.get("instruction_instances") or isinstance(value.get("operands"), list):
                        raise ValueError(f"{identity}: mapping cases must not prefill instruction operands")
                    for item in value.values():
                        unfilled(item)
                elif isinstance(value, list):
                    for item in value:
                        unfilled(item)
            unfilled(case["confirmed_spec"])
    if not cases:
        raise ValueError("The case file is empty")
    return cases


def schedule(cases, arms, repeats, seed, *, blocked=False):
    if repeats < 1 or not arms or any(arm not in ARMS for arm in arms):
        raise ValueError("Invalid repeat count or experiment arm")
    rng = random.Random(seed)
    if blocked:
        blocks = [(case, repeat) for repeat in range(repeats) for case in cases]
        rng.shuffle(blocks)
        tasks = []
        for case,repeat in blocks:
            order = list(dict.fromkeys(arms)); rng.shuffle(order)
            tasks.extend((case,arm,repeat) for arm in order)
    else:
        tasks = [(case, arm, repeat) for repeat in range(repeats) for case in cases for arm in dict.fromkeys(arms)]
        rng.shuffle(tasks)
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
                   "transport_completed": False,
                   "response_contract": getattr(request.response_contract, "name", None),
                   "max_retries": request.max_retries, "timeout": request.timeout}
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


def run_case(case, arm, *, provider, model=None, effort=None, evaluator=None, evidence_cache=None):
    from application import generation_agent as agent
    from application.model_api import provider_scope
    from application.confirmed_generation_context import project_confirmed_specification
    from knowledge.evidence import KnowledgeContext, KnowledgeQuery, context_manifest, evidence_record
    from knowledge.core import _format_result_block
    from plc.ir import build_plc_ir, validate_plc_ir
    from plc.validation import validate_ladder_candidate_structure
    from plc.specification.semantic_validation import validate_confirmed_semantics

    original_builder = agent._build_knowledge_context
    observed = ObservedProvider(provider)
    record = {"case_id": case["case_id"], "arm": arm, "spec_sha256": digest(case["confirmed_spec"]),
              "confirmed_spec": copy.deepcopy(case["confirmed_spec"]), "evaluation": copy.deepcopy(case.get("evaluation")),
              "requested_model": model, "retired_effort_argument": effort, "tuning_source": "model_profile", "actual_requests": [],
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
            key = json.dumps([str(query), getattr(query, "metadata", {}), kwargs], ensure_ascii=False, sort_keys=True)
            if evidence_cache is not None and key in evidence_cache:
                context = evidence_cache[key]
            else:
                context = original_builder(query, **kwargs)
                if evidence_cache is not None:
                    evidence_cache[key] = context
            if arm in {"manual_text", "manual_text_no_relations"}:
                context = manual_text_context(context)
            if arm in FACTORIAL_ARMS[:2]:
                context = without_relation_context(context)
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
                model_name=model, effort=effort, construction_examples=case.get("construction_examples"),
                on_context=lambda handoff: record.update(handoff=handoff))
        record["generation_status"] = "completed"
        record["ladder"] = result["ladder"]
        plc_model = case.get("plc_model", "FX3U")
        validate_ladder_candidate_structure(result["ladder"], plc_model=plc_model)
        validate_plc_ir(build_plc_ir(result["ladder"], plc_model=plc_model), validate_ladder=False)
        record["structural_valid"] = True
        record["semantic_validation"] = validate_confirmed_semantics(
            result["ladder"], project_confirmed_specification(case["confirmed_spec"]), plc_model,
        )
        evaluate = evaluator or (evaluate_synthetic_case if case.get("evaluation") else None)
        record["behavior"] = evaluate(case, result) if evaluate else {
            "status": "not_covered", "reason": "no_behavior_evaluator",
        }
        if not isinstance(record["behavior"], dict) or record["behavior"].get("status") not in {"verified", "failed", "not_covered"}:
            raise ValueError("Evaluator must report verified, failed or not_covered")
    except Exception as error:
        record["generation_status"] = "failed"
        record["error"] = {"type": type(error).__name__, "code": getattr(error, "code", None)}
        # No raw SDK exception: it may contain request credentials.
    finally:
        record["end_to_end_ms"] = (time.perf_counter()-started)*1000
        record["model_calls"] = len(observed.attempts)
        record["first_candidate"] = assess_first_candidate(case, record, evaluator=evaluator)
    return record


def evaluate_synthetic_case(case, result):
    """Independent fixed answers, never derived from the usage extractor.

    Core supplies syntax/identity and its existing structural checks. This is
    operand mapping coverage, not native communication or PLC execution proof.
    """
    from plc.device_identity import canonical_operand
    from plc.ir import lower_rung_instructions
    from plc.specification.semantic_validation import validate_confirmed_semantics
    expected = case.get("evaluation") or {}
    if expected.get("kind") == "control":
        receipt = validate_confirmed_semantics(result["ladder"], case["confirmed_spec"], case.get("plc_model", "FX3U"))
        return {"status": "verified" if receipt["status"] == "verified" else "failed",
                "scope": "core_control_structure_and_binding_predicates", "receipt": receipt}
    if expected.get("kind") != "operand_mapping":
        return {"status": "not_covered", "reason": "no_independent_operand_expectation"}
    # Explicit aliases belong to the independently reviewed case. In particular,
    # a signed CMP constant may have a documented 16-bit hexadecimal spelling;
    # do not apply a guessed width or wrap unrelated length/channel operands.
    aliases = {alias.upper(): original.upper()
               for original, values in expected.get("constant_aliases", {}).items() for alias in values}
    def identity(value):
        value = str(value).strip().upper()
        if re.fullmatch(r"H[0-9A-F]+", value):
            value = "H" + format(int(value[1:], 16), "X")
        value = aliases.get(value, value)
        if re.fullmatch(r"K[+-]?\d+", value):
            return ("integer", int(value[1:]))
        if re.fullmatch(r"H[0-9A-F]+", value):
            return ("integer", int(value[1:], 16))
        return ("device", canonical_operand(value))
    wanted = tuple(map(identity, expected["operands"]))
    outputs = [output for rung in result["ladder"].get("rungs", [])
               for branch in rung.get("branches", []) for output in branch.get("outputs", [])]
    calls = [output for output in outputs if output.get("type") == "APP_INSTR"
             and str(output.get("opcode", "")).strip().upper() == expected["opcode"]]
    # Preserve occurrences: the Core instance-presence helper is a set, which
    # cannot distinguish one shift/reset/communication call from two equal ones.
    actual = [output.get("operands", []) for output in calls]
    checks = {"exactly_one_designated_call": len(calls) == 1,
              "operand_mapping": bool(actual) and all(tuple(map(identity, operands)) == wanted for operands in actual),
              "no_additional_outputs": len(outputs) == len(calls)}
    gate = expected.get("gate")
    if gate is not None:
        gates = [instruction for rung in result["ladder"].get("rungs", [])
                 for instruction in lower_rung_instructions(rung) if ".outputs[" not in instruction.get("path", "")]
        checks["specified_direct_gate"] = (len(gates) == 1 and gates[0]["op"] == gate["op"]
            and tuple(map(identity, gates[0]["args"])) == tuple(map(identity, gate["args"])))
    behavior = None
    if expected.get("cmp_behavior"):
        behavior = evaluate_cmp_behavior(expected["cmp_behavior"], result["ladder"])
        checks["cmp_behavior"] = behavior["status"] == "verified"
    return {"status": "verified" if all(checks.values()) else "failed",
            "scope": "operand_mapping_single_call_and_no_extra_operations",
            "checks": checks, "call_count": len(calls), "output_count": len(outputs),
            "expected": expected["operands"], "actual": actual,
            "native_execution": "not_measured",
            **({"cmp_behavior": behavior} if behavior is not None else {})}


def cmp_reference_state(left, right, previous, enabled):
    """Benchmark-only signed-16 CMP reference, independent of retrieval data."""
    if not (-32768 <= left <= 32767 and -32768 <= right <= 32767):
        raise ValueError("Reference inputs must be signed 16-bit words")
    if not enabled: return tuple(previous)
    return (left > right, left == right, left < right)


def evaluate_cmp_behavior(expected, ladder):
    """Compare a closed single-CMP program against frozen task truth traces.

    The trace oracle is authored separately from the source extractor; this
    evaluator never imports or reads relationship evidence or usage facts.
    Only M-result cells, direct LD/LDI gates and word/K/H inputs are in scope.
    """
    from plc.ir import lower_rung_instructions
    if not expected.get('scenarios') or len(expected.get('result_devices',[]))!=3:
        return {'status':'failed','reason':'no_independent_truth_traces','traces':[]}
    instructions = [i for rung in ladder.get('rungs',[]) for i in lower_rung_instructions(rung)]
    calls = [i for i in instructions if i['op']=='CMP']
    gates = [i for i in instructions if '.outputs[' not in i.get('path','')]
    if len(calls)!=1 or len(gates)!=1 or gates[0]['op'] not in {'LD','LDI'}:
        return {'status':'failed','reason':'outside_single_cmp_reference_scope','traces':[]}
    args = calls[0]['args']; traces=[]
    def word(operand, memory):
        token=str(operand).upper()
        if token.startswith('K'): return int(token[1:])
        if token.startswith('H'):
            value=int(token[1:],16)
            if not 0<=value<=65535: raise ValueError('Outside 16-bit constant')
            return value-65536 if value>=32768 else value
        return int(memory[token])
    try:
        if len(args)!=3 or not re.fullmatch(r'M\d+',args[2]): raise ValueError('Unsupported result address')
        written=[f'M{int(args[2][1:])+offset}' for offset in range(3)]
        for row in expected['scenarios']:
            memory={**row['inputs'],**dict(zip(expected['result_devices'],row['before']))}
            active=bool(memory[gates[0]['args'][0]])
            if gates[0]['op']=='LDI': active=not active
            if active:
                values=cmp_reference_state(word(args[0],memory),word(args[1],memory),(False,False,False),True)
                memory.update(zip(written,values))
            actual=[bool(memory[d]) for d in expected['result_devices']]
            traces.append({'id':row['id'],'actual':actual,'expected':row['expected'],'passed':actual==row['expected']})
    except (KeyError,ValueError,IndexError):
        return {'status':'failed','reason':'unsupported_or_missing_reference_input','traces':traces}
    return {'status':'verified' if all(t['passed'] for t in traces) else 'failed','traces':traces,
            'scope':'benchmark_signed16_cmp_and_disabled_retention','native_execution':'not_measured'}


def assess_first_candidate(case, record, *, evaluator=None):
    from application import generation_agent as agent
    from application.compact_protocol import normalize_compact
    from application.confirmed_generation_context import project_confirmed_specification
    from plc.ir import build_plc_ir, validate_plc_ir
    from plc.validation import validate_ladder_candidate_structure
    from plc.specification.semantic_validation import validate_confirmed_semantics
    generators = [attempt for attempt in record["attempts"] if attempt.get("response_contract") == "compact_ladder"]
    outcome = {"status": "not_produced", "usable": False, "generator_calls": len(generators),
               "retries": max(0, len(generators)-1), "semantic": {"status": "not_covered"}}
    if not generators:
        return outcome
    attempt = generators[0]
    outcome["json_complete_ms"] = attempt.get("json_complete_ms")
    try:
        frame = agent._FirstJSONObjectStream()
        text, _ = frame.feed(attempt["raw_content"])
        compact, _ = normalize_compact(agent._json_object(text))
        model = case.get("plc_model", "FX3U")
        spec = project_confirmed_specification(case["confirmed_spec"])
        ladder, _ = agent._decode_generated_ladder(compact, spec, model)
        outcome.update(status="decoded", ladder=ladder)
        if evaluator and record["generation_status"] == "completed" and len(generators) == 1:
            outcome["semantic"] = copy.deepcopy(record["behavior"])
        elif case.get("evaluation"):
            outcome["semantic"] = evaluate_synthetic_case(case, {"ladder": ladder})
        validate_ladder_candidate_structure(ladder, plc_model=model)
        validate_plc_ir(build_plc_ir(ladder, plc_model=model), validate_ladder=False)
        outcome["structural_valid"] = True
        receipt = validate_confirmed_semantics(ladder, spec, model)
        outcome["contract_status"] = receipt["status"]
        outcome["usable"] = (record["generation_status"] == "completed"
                             and outcome["semantic"].get("status") == "verified"
                             and receipt["status"] in {"verified", "not_applicable"})
    except Exception as error:
        outcome["error"] = {"type": type(error).__name__, "code": getattr(error, "code", None)}
    return outcome


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
        metering = {name: [] for name in ("input_tokens", "output_tokens", "total_tokens")}
        for row in rows:
            usages = [attempt.get("usage") or {} for attempt in row["attempts"]]
            values = [usage.get("reasoning_tokens") for usage in usages]
            if values and all(type(value) is int for value in values):
                reasoning.append(sum(values))
            for name in metering:
                values = [usage.get(name) for usage in usages]
                if values and all(type(value) is int for value in values):
                    metering[name].append(sum(values))
        result[arm] = {"runs": len(rows), "completed": sum(r["generation_status"] == "completed" for r in rows),
                       "behavior_verified": sum(r["behavior"].get("status") == "verified" for r in rows),
                       "first_pass_semantic_correct": sum(r.get("first_candidate", {}).get("semantic", {}).get("status") == "verified" for r in rows),
                       "first_pass_usable": sum(bool(r.get("first_candidate", {}).get("usable")) for r in rows),
                       "model_calls": sum(r.get("model_calls", len(r["attempts"])) for r in rows),
                       "median_end_to_end_ms": statistics.median(durations),
                       "p95_end_to_end_ms": durations[max(0, (95*len(durations)+99)//100-1)],
                       "reasoning_usage_known_runs": len(reasoning),
                       "median_reasoning_tokens": statistics.median(reasoning) if reasoning else None,
                       **{name: {"known_runs": len(values), "median": statistics.median(values) if values else None}
                          for name, values in metering.items()}}
    pairs = {}
    for row in records:
        if row["arm"] in PAIRED_ARMS:
            pairs.setdefault((row["case_id"], row.get("repeat", 0)), {})[row["arm"]] = row
    matched = []
    for (case_id, repeat), pair in sorted(pairs.items()):
        if all(arm in pair for arm in PAIRED_ARMS):
            raw, bound = (pair[arm] for arm in PAIRED_ARMS)
            matched.append({"case_id": case_id, "repeat": repeat,
                "usable": {arm: pair[arm].get("first_candidate", {}).get("usable", False) for arm in PAIRED_ARMS},
                "bound_minus_manual_ms": bound["end_to_end_ms"]-raw["end_to_end_ms"]})
    factorial = []
    factor_blocks = {}
    for row in records:
        if row['arm'] in FACTORIAL_ARMS: factor_blocks.setdefault((row['case_id'],row.get('repeat',0)),{})[row['arm']]=row
    for (case_id,repeat), block in sorted(factor_blocks.items()):
        if set(block) != set(FACTORIAL_ARMS): continue
        for name, baseline, treatment in (
            ('relations_without_usage',FACTORIAL_ARMS[0],FACTORIAL_ARMS[2]),
            ('relations_with_usage',FACTORIAL_ARMS[1],FACTORIAL_ARMS[3]),
            ('usage_without_relations',FACTORIAL_ARMS[0],FACTORIAL_ARMS[1]),
            ('usage_with_relations',FACTORIAL_ARMS[2],FACTORIAL_ARMS[3]),
        ):
            factorial.append({'case_id':case_id,'repeat':repeat,'contrast':name,
                              'baseline_usable':block[baseline]['first_candidate']['usable'],
                              'treatment_usable':block[treatment]['first_candidate']['usable'],
                              'treatment_minus_baseline_ms':block[treatment]['end_to_end_ms']-block[baseline]['end_to_end_ms']})
    return {"groups": result, "performance_acceptance": "not_established", "factorial_contrasts":factorial,
            "paired_results": matched,
            "note": "Inspect matched case/repeat pairs, actual settings and behavioral coverage before drawing conclusions."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="JSONL: case_id, confirmed_spec, optional plc_model/oracle_evidence")
    parser.add_argument("--arms", default=",".join(PAIRED_ARMS))
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", default=None, help="Deprecated, ignored. Tune effort in the model profile.")
    parser.add_argument("--evaluator", help="Optional module:function(case, result) behavioral evaluator")
    parser.add_argument("--output", type=Path, help="Private JSONL result path; required with --live")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Authorize paid calls using the saved profile")
    mode.add_argument("--preflight", action="store_true", help="Compile final requests with the saved profile; no transport")
    parser.add_argument("--profile-id", help="Select an existing saved profile without changing the active profile")
    parser.add_argument("--require-endpoint", help="Abort before any transport if the saved endpoint differs")
    parser.add_argument("--cmp-relations", action="store_true", help="Six existing CMP cases, four factors, randomized adjacent blocks")
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.cases)
        if args.cmp_relations:
            cases = [c for c in cases if c.get('evaluation',{}).get('opcode')=='CMP']
            if not cases: raise ValueError('CMP factorial requires operand-mapping CMP cases')
            if any(c.get('plc_model','FX3U')!='FX3U' or not c['evaluation'].get('cmp_behavior') for c in cases):
                raise ValueError('CMP factorial requires FX3U cases with independent behavior truth traces')
            args.arms=','.join(FACTORIAL_ARMS)
        tasks = schedule(cases, args.arms.split(","), args.repeat, args.seed, blocked=args.cmp_relations)
        if "oracle" in args.arms.split(",") and any(not c.get("oracle_reviewed") or not c.get("oracle_evidence") for c in cases):
            raise ValueError("Supply reviewed oracle_evidence for every oracle case")
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if not args.live and not args.preflight:
        print(json.dumps({"live": False, "scheduled_runs": len(tasks), "case_ids": [c["case_id"] for c in cases],
                          "arms": args.arms.split(","), "effort": "saved_profile_unchanged", "retired_effort_argument": args.effort}, ensure_ascii=False, indent=2))
        return 0
    if args.output is None or args.output.exists():
        parser.error("--live/--preflight requires a new --output path; existing results are never overwritten")
    if args.output.resolve().is_relative_to(ROOT):
        parser.error("Raw requests/results must be stored in a private directory outside the repository")
    from model_runtime.provider import get_active_provider
    from shared.tracing import sanitize
    if args.profile_id:
        from storage.config import load_full_config, get_model_profile
        config = load_full_config()
        get_model_profile(config, args.profile_id)
        config = {**config, "activeModelProfileId": args.profile_id}
        provider = get_active_provider(config)
    else:
        provider = get_active_provider()
    saved_endpoint = str(provider.profile.get("baseUrl") or "").rstrip("/")
    if args.require_endpoint and saved_endpoint != args.require_endpoint.rstrip("/"):
        parser.error("Saved profile endpoint differs from --require-endpoint")
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
                "endpoint": endpoint, "profile_model": provider.profile.get("model"), "seed": args.seed,
                "profile_id": provider.profile.get("id"), "user_model_settings": copy.deepcopy(provider.profile.get("userModelSettings")),
                "started_at_utc": datetime.now(timezone.utc).isoformat()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    records = []
    # Private output permissions where the platform supports them.
    import os
    evidence_cache = {}
    if args.preflight or set(PAIRED_ARMS).issubset(args.arms.split(",")):
        preview = preflight_factorial if args.cmp_relations else preflight_pairs
        preflight = preview(cases, provider=provider, model=args.model, evidence_cache=evidence_cache)
        preflight.update(identity)
        path = args.output if args.preflight else args.output.with_suffix(".preflight.json")
        descriptor = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(sanitize(preflight), stream, ensure_ascii=False, allow_nan=False, indent=2)
        if args.preflight:
            print(json.dumps({"preflight_passed": True, "network_calls": 0, "cases": len(cases), "endpoint": endpoint,
                              "profile_model": provider.profile.get("model"), "output": str(path)}, ensure_ascii=False))
            return 0
        print(f"Offline final-request preflight passed for {len(cases)} cases; transport starts now.", file=sys.stderr)
    descriptor = os.open(args.output, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        for case, arm, repeat in tasks:
            record = run_case(case, arm, provider=provider, model=args.model, effort=args.effort,
                              evaluator=evaluator, evidence_cache=evidence_cache)
            record.update(identity, repeat=repeat)
            stream.write(json.dumps(sanitize(record), ensure_ascii=False, allow_nan=False)+"\n")
            stream.flush()
            records.append(record)
            print(f"{case['case_id']} / {arm} / {repeat}: {record['generation_status']}", file=sys.stderr)
    summary = summarize(records)
    descriptor = os.open(args.output.with_suffix(".summary.json"), os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(sanitize(summary), stream, ensure_ascii=False, allow_nan=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
