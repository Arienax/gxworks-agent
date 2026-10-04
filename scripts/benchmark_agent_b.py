"""Paired Agent-B experiments through the real application path.

Without --live/--preflight this prints a plan without loading credentials.
No experiment changes effort, provider settings, retry policy or token ceilings.
Oracle evidence must be supplied and reviewed by the operator, not another LLM.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager, nullcontext
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
NO_RAG_ARM = "no_rag"
ARMS = ("manual_text", "usage_bound", "oracle", "legacy_retrieval", "automatic", NO_RAG_ARM)
PAIRED_ARMS = ("manual_text", "usage_bound")
FACTORIAL_ARMS = ("manual_text_no_relations", "usage_bound_no_relations", *PAIRED_ARMS)
ARMS = (*ARMS, *FACTORIAL_ARMS[:2])
BINDING_ARMS = ('native_parameters', 'core_binding')
ARMS = (*ARMS, *BINDING_ARMS)
RELATION_DIMENSIONS = ("operation.result_mapping", "execution.disabled_retention")


def no_rag_context():
    """An explicit experiment receipt; never enter the knowledge builder."""
    from knowledge.evidence import KnowledgeContext
    return KnowledgeContext("", {"status": "experiment_disabled", "experiment_arm": NO_RAG_ARM,
                                 "retrieval_enabled": False, "records": []})


@contextmanager
def no_rag_scope():
    # Query planning otherwise opens SQLite for alias enrichment. Keep this
    # ablation in the runner, without adding a production configuration flag.
    targets = {"version": "experiment_no_rag", "instructions": [], "devices": [],
               "errors": [], "process": []}
    with patch("knowledge.structured_facts.structured_fact_targets", return_value=targets):
        yield


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
    value = re.sub(r"(?m)^OPERAND_SEMANTICS: (\{[^\r\n]+\})", replace, str(text))
    def compiled(match):
        payload = json.loads(match[2])
        payload['groups'] = [g for g in payload['groups'] if not (
            g.get('status') == 'candidate_evidence' and g.get('value', {}).get('facet'))]
        return match[1] + '\n' + json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n[/INSTRUCTION FACTS]' if payload['groups'] else ''
    return re.sub(r'(?m)^(\[INSTRUCTION FACTS [^\n]+\])\n(\{[^\n]+\})\n\[/INSTRUCTION FACTS\]', compiled, value)


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
    _reconcile_compiled_ablation(manifest, text)
    manifest["context_sha256"] = text_sha256(text)
    manifest["benchmark_ablation"] = "candidate_usage_only"
    return KnowledgeContext(text, manifest)


def _wire_without_usage(params):
    value = copy.deepcopy(params)
    for message in value.get("messages", []):
        if isinstance(message.get("content"), str):
            message["content"] = without_candidate_usage(message["content"])
    return value


def _reconcile_compiled_ablation(manifest, text):
    """Reflect actual compiled facts, separately from the unchanged raw units."""
    from knowledge.evidence import text_sha256
    blocks = {json.loads(m[1])['id']: m[2] for m in re.finditer(
        r'(?ms)^\[KNOWLEDGE (\{[^\n]+\})\]\n(.*?)\n\[/KNOWLEDGE\]', text)}
    available = {}
    for identity, body in blocks.items():
        available[identity] = {g['id'] for m in re.finditer(r'(?m)^\[INSTRUCTION FACTS [^\n]+\]\n(\{[^\n]+\})', body)
                               for g in json.loads(m[1])['groups']}
    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            identity = value.get('id')
            if identity in available:
                for key in ('dimensions', 'fact_dimensions'):
                    if key in value:
                        value[key] = [d for d in value[key] if not d.startswith('definition.') or d[11:] in available[identity]]
                if 'definition_fact_groups' in value:
                    value['definition_fact_groups'] = [g for g in value['definition_fact_groups'] if g['id'] in available[identity]]
                if 'content_sha256' in value:
                    value['content_sha256'] = text_sha256(blocks[identity])
            if value.get('dimension', '').startswith('definition.') and 'candidate_source_ids' in value:
                value['candidate_source_ids'] = [i for i in value['candidate_source_ids'] if value['dimension'][11:] in available.get(i, ())]
            for item in list(value.values()):
                visit(item)
    visit(manifest)


def without_relation_evidence(text):
    """Experiment-only removal of the entire atomic manual relation group."""
    value = re.sub(r'(?ms)(?:\n\n)?^\[RELATION EVIDENCE \{[^\n]+\}\]\n.*?^\[/RELATION EVIDENCE\]', '', str(text))
    def compiled(match):
        groups = json.loads(match[2])['groups']
        if any(g['value'].get('behavior') == 'conditional_results' or g['dimension'] == 'execution.disabled_retention'
               or g['value'].get('disabled') == 'retain' for g in groups):
            return ''
        return match[0]
    return re.sub(r'(?m)^(\[INSTRUCTION FACTS [^\n]+\])\n(\{[^\n]+\})\n\[/INSTRUCTION FACTS\]', compiled, value)


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
    _reconcile_compiled_ablation(manifest, text)
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
        if (counts != [0,3,0,3] or relation_counts[:2] != [0,0]
                or relation_counts[2] <= 0 or relation_counts[2] != relation_counts[3]):
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
        for match in re.finditer(r'(?m)^\[INSTRUCTION FACTS [^\n]+\]\n(\{[^\n]+\})', str(message.get('content', ''))):
            count += sum(g.get('status') == 'candidate_evidence' and bool(g.get('value', {}).get('facet'))
                         for g in json.loads(match[1])['groups'])
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


def preflight_binding(cases, *, provider, model=None, evidence_cache=None):
    pairs = []
    def common(request):
        result = copy.deepcopy(request)
        for message in result['messages']:
            message['content'] = re.sub(r'\n# Confirmed operation effects\n[^\n]*\n', '', message['content'])
        return result
    for case in cases:
        rows = [run_case(case, arm, provider=PreviewProvider(provider), model=model, evidence_cache=evidence_cache)
                for arm in BINDING_ARMS]
        if any(len(r['actual_requests']) != 1 for r in rows):
            raise ValueError(case['case_id'] + ': binding preflight needs one final request')
        requests = [r['actual_requests'][0] for r in rows]
        control = not case['confirmed_spec'].get('operation_intents')
        if common(requests[0]) != common(requests[1]) or (requests[0] == requests[1]) != control:
            raise ValueError(case['case_id'] + ': binding contrast is not isolated')
        pairs.append({'case_id': case['case_id'], 'other_request_content_identical': True,
                      'control_request_unchanged': control, 'records': rows})
    return {'network_calls': 0, 'passed': True, 'pairs': pairs}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def reviewed_effect_case_coverage(cases):
    """Require an independent expectation for every currently reviewed effect form."""
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.instruction_definition import select_fact_dependencies
    models = {case.get('plc_model', 'FX3U') for case in cases}
    required = set()
    for model in models:
        for opcode in registry.known_mnemonics():
            form = registry.resolve_form(opcode, cpu=model)
            if form is None or not form.spec.supports_cpu(model):
                continue
            for group in form.spec.definition_facts:
                if group.dimension.startswith('effects.') and group.value.get('behavior'):
                    closure = select_fact_dependencies(form.spec.definition_facts, [group.id], opcode=opcode, model=model)
                    if closure['source_verification_complete'] and not closure['gaps']:
                        required.add((model, opcode))
    represented = {(case.get('plc_model', 'FX3U'), case['evaluation']['opcode']) for case in cases
                   if case.get('evaluation', {}).get('kind') == 'operand_mapping'
                   and case['evaluation'].get('reference', {}).get('expected_source')
                   and case['evaluation'].get('effect_traces')}
    return {'required_reviewed_forms': len(required), 'represented_reviewed_forms': len(required & represented),
            'missing': [{'target_model': model, 'opcode': opcode} for model, opcode in sorted(required - represented)],
            'scope': 'reviewed_effect_subset; unformalized_forms_and_hardware_are_separate_gaps'}


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
        if arm == NO_RAG_ARM:
            context = no_rag_context()
        elif arm == "oracle":
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
    original_binding_prompt = agent.operation_intent_prompt
    binding_prompt_scope = patch.object(agent, 'operation_intent_prompt', lambda *a, **k:
        ('\n# Confirmed operation effects\n按 operation_intents 中已确认的效果输出原生指令调用，逐项推导操作数；不要输出 OP 引用。\n'
         if original_binding_prompt(*a, **k) else '')) if arm == 'native_parameters' else nullcontext()
    retrieval_scope = no_rag_scope() if arm == NO_RAG_ARM else nullcontext()
    try:
        with sink_scope, binding_prompt_scope, retrieval_scope, patch.object(agent, "_build_knowledge_context", builder), provider_scope(observed, model_name=model):
            result = agent.generate_confirmed_ladder(case["confirmed_spec"], case.get("plc_model", "FX3U"),
                model_name=model, effort=effort,
                construction_examples=False if arm == NO_RAG_ARM else case.get("construction_examples"),
                on_context=lambda handoff: record.update(handoff=handoff))
        record["generation_status"] = "completed"
        record["ladder"] = result["ladder"]
        record['operation_binding'] = result.get('operation_binding')
        record['confirmed_predicate_binding'] = result.get('confirmed_predicate_binding')
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
    valid = {wanted, *(tuple(map(identity, values)) for values in expected.get('equivalent_operands', []))}
    outputs = [output for rung in result["ladder"].get("rungs", [])
               for branch in rung.get("branches", []) for output in branch.get("outputs", [])]
    calls = [output for output in outputs if output.get("type") == "APP_INSTR"
             and str(output.get("opcode", "")).strip().upper() == expected["opcode"]]
    # Preserve occurrences: the Core instance-presence helper is a set, which
    # cannot distinguish one shift/reset/communication call from two equal ones.
    actual = [output.get("operands", []) for output in calls]
    checks = {"exactly_one_designated_call": len(calls) == 1,
              "operand_mapping": bool(actual) and all(tuple(map(identity, operands)) in valid for operands in actual),
              "no_additional_outputs": len(outputs) == len(calls)}
    gate = expected.get("gate")
    if gate is not None:
        gates = [instruction for rung in result["ladder"].get("rungs", [])
                 for instruction in lower_rung_instructions(rung) if ".outputs[" not in instruction.get("path", "")]
        assumed = expected.get('assumed_bits', {})
        # Expectations come from the independently reviewed synthetic case,
        # never from production extraction or a model-generated predicate.
        redundant = lambda g: (g['op'] in {'AND', 'ANI'} and len(g['args']) == 1
            and type(assumed.get(canonical_operand(g['args'][0]))) is bool
            and (g['op'] == 'AND') == assumed[canonical_operand(g['args'][0])])
        effective_gates = [g for g in gates if not redundant(g)]
        if gates and gates[0]['op'] in {'LD', 'LDI'} and all(
                g['op'] in {'AND', 'ANI'} for g in gates[1:]):
            # A confirmed true contact can precede the variable gate as well
            # as follow it. This is an independent conjunction oracle, not a
            # relaxation for arbitrary unknown ladder predicates.
            def known_true(g):
                return (len(g['args']) == 1 and type(assumed.get(canonical_operand(g['args'][0]))) is bool
                        and (g['op'] in {'LD', 'AND'}) == assumed[canonical_operand(g['args'][0])])
            effective_gates = [dict(g) for g in gates if not known_true(g)]
            if effective_gates and effective_gates[0]['op'] in {'AND', 'ANI'}:
                effective_gates[0]['op'] = 'LD' if effective_gates[0]['op'] == 'AND' else 'LDI'
        allowed_gates = [gate, *expected.get('equivalent_gates', [])]
        checks["specified_direct_gate"] = (len(effective_gates) == 1 and any(
            effective_gates[0]["op"] == option["op"] and
            tuple(map(identity, effective_gates[0]["args"])) == tuple(map(identity, option["args"]))
            for option in allowed_gates))
    behavior = None
    if expected.get("cmp_behavior"):
        behavior = evaluate_cmp_behavior(expected["cmp_behavior"], result["ladder"])
        checks["cmp_behavior"] = behavior["status"] == "verified"
    traces = None
    if expected.get('effect_traces'):
        traces = evaluate_effect_traces(expected['effect_traces'], calls, case.get('plc_model', 'FX3U'))
        checks['effect_traces'] = traces['status'] == 'verified'
    return {"status": "verified" if all(checks.values()) else "failed",
            "scope": "operand_mapping_single_call_and_no_extra_operations",
            "checks": checks, "call_count": len(calls), "output_count": len(outputs),
            "expected": expected["operands"], "actual": actual,
            "native_execution": "not_measured",
            **({"cmp_behavior": behavior} if behavior is not None else {}),
            **({'effect_traces': traces} if traces is not None else {})}


def evaluate_effect_traces(expected, calls, model):
    """Execute the shared reference primitives against independent fixed traces.

    The expected memory and writes are authored in the case, never generated
    from the production definition. This checks its formalized reference scope,
    not native execution or physical communication.
    """
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.instruction_effects import execute_behavior
    from plc.instruction_definition import DefinitionError, select_fact_dependencies
    from plc.device_identity import canonical_operand, decimal_region_address
    traces = []
    if len(calls) != 1:
        return {'status': 'failed', 'reason': 'single_call_required', 'traces': traces}
    call = calls[0]
    form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(call['opcode'], cpu=model)
    if form is None or len(call['operands']) != len(form.spec.native_operand_order):
        return {'status': 'failed', 'reason': 'missing_parameter_contract', 'traces': traces}
    groups = [g for g in form.spec.definition_facts if g.dimension.startswith('effects.')
              and g.status == 'source_verified' and g.value.get('behavior')]
    if len(groups) != 1:
        return {'status': 'failed', 'reason': 'unique_checked_effect_required', 'traces': traces}
    closure = select_fact_dependencies(form.spec.definition_facts, [groups[0].id], opcode=call['opcode'], model=model)
    if not closure['source_verification_complete']:
        return {'status': 'failed', 'reason': 'unchecked_effect_dependencies', 'traces': traces}
    parameters = {}
    for name, operand in zip(form.spec.native_operand_order, call['operands']):
        token = canonical_operand(operand)
        parameters[name] = int(token[1:], 16 if token.startswith('H') else 10) if re.fullmatch(r'K[+-]?\d+|H[0-9A-F]+', token) else token
    lifecycle = next((g.value for g in form.spec.definition_facts
        if g.id in closure['bundles'][0]['fact_ids'] and g.dimension.startswith('execution.')), {})
    scalar_names = set()
    def scalar_parameters(value):
        if isinstance(value, dict):
            if value.get('op') == 'parameter' and value.get('type', {}).get('kind') in {'int', 'bool'}:
                scalar_names.add(value['name'])
            for item in value.values():
                scalar_parameters(item)
        elif isinstance(value, list):
            for item in value:
                scalar_parameters(item)
    scalar_parameters(groups[0].value)
    sequences = {}
    try:
        for row in expected:
            sequence = row.get('sequence')
            if sequence is not None:
                if not isinstance(sequence, str) or not sequence:
                    raise ValueError('Trace sequence needs a nonempty identity')
                frame = sequences.setdefault(sequence, {'memory': {}, 'previous_enabled': False})
                memory = frame['memory']
                memory.update(row.get('memory', {}))
                previous_enabled = frame['previous_enabled']
            else:
                memory = row.get('memory', {})
                previous_enabled = row.get('previous_enabled', False)
            values = {name: memory[operand] if name in scalar_names and isinstance(operand, str) else operand
                      for name, operand in parameters.items()}
            result = execute_behavior(groups[0].value, values, memory=memory,
                state=row.get('state', {}), enabled=row['enabled'],
                previous_enabled=previous_enabled,
                trigger=lifecycle.get('trigger', 'level'), disabled=lifecycle.get('disabled', 'unknown'))
            writes = {decimal_region_address(base, offset): value for (base, offset), value in result['writes'].items()}
            checks = {'writes': writes == row['writes'], 'executed': result['executed'] == row['executed']}
            if sequence is not None:
                memory.update(writes)
                frame['previous_enabled'] = row['enabled']
            if 'memory_after' in row:
                final = {**memory, **writes}
                checks['retained_sequence_memory'] = all(final.get(address) == value
                    for address, value in row['memory_after'].items())
            if 'external_resource' in row:
                checks['external_resource'] = result.get('external_contract', {}).get('resource') == row['external_resource']
            traces.append({'id': row['id'], 'actual_writes': writes, 'checks': checks, 'passed': all(checks.values())})
    except (DefinitionError, KeyError, ValueError, TypeError) as error:
        return {'status': 'failed', 'reason': type(error).__name__, 'traces': traces}
    return {'status': 'verified' if traces and all(t['passed'] for t in traces) else 'failed',
            'traces': traces, 'scope': 'source_checked_typed_reference_against_independent_fixed_traces',
            'native_execution': 'not_measured', 'hardware_effect': 'not_tested'}


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
        # Preserve the model's own semantic outcome separately from the
        # explicit Core stage. Rebinding must not erase a model direction error.
        raw_candidate = {'semantic': {'status': 'not_covered'}}
        outputs = [out for rung in compact.get('r', compact.get('rungs', []))
                   for branch in rung.get('b', rung.get('branches', []))
                   for out in branch.get('o', branch.get('outputs', []))]
        if any(isinstance(out, str) and out.startswith('OP ') for out in outputs):
            raw_candidate['semantic']['reason'] = 'operation_reference_requires_Core_binding'
            raw_candidate['status'] = 'operation_reference'
        else:
            try:
                raw_ladder, _ = agent._decode_generated_ladder(compact, spec, model)
                if case.get('evaluation'):
                    raw_candidate['semantic'] = evaluate_synthetic_case(case, {'ladder': raw_ladder})
                raw_candidate['contract_status'] = validate_confirmed_semantics(raw_ladder, spec, model)['status']
            except Exception as error:
                raw_candidate['status'] = 'not_native_or_invalid'
                raw_candidate['error'] = {'type': type(error).__name__, 'code': getattr(error, 'code', None)}
        outcome['raw_model_candidate'] = raw_candidate
        compact, binding = agent.materialize_operation_references(compact, spec, target_model=model)
        outcome['Core_binding'] = binding
        outcome['raw_model_candidate_retained'] = True
        ladder, _ = agent._decode_generated_ladder(compact, spec, model)
        from plc.specification.semantic_validation import bind_confirmed_predicates
        ladder, predicate_binding = bind_confirmed_predicates(ladder, spec)
        outcome['Core_predicate_binding'] = predicate_binding
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


def prompt_cache_meter(usage):
    """Read provider-reported cache tokens; missing/invalid metering stays unknown."""
    raw = usage.get('raw_usage') or {}
    if not isinstance(raw, dict):
        return None
    hit = raw.get('prompt_cache_hit_tokens')
    if hit is None:
        for name in ('prompt_tokens_details', 'input_tokens_details'):
            details = raw.get(name)
            if isinstance(details, dict) and details.get('cached_tokens') is not None:
                hit = details['cached_tokens']
                break
    if hit is None:
        hit = raw.get('cache_read_input_tokens')
    inputs = usage.get('input_tokens')
    if type(hit) is not int or hit < 0 or type(inputs) is not int or inputs <= 0 or hit > inputs:
        return None
    miss = raw.get('prompt_cache_miss_tokens')
    if miss is not None and (type(miss) is not int or miss < 0 or hit + miss != inputs):
        return None
    return {'hit_tokens': hit, 'miss_tokens': miss, 'input_tokens': inputs}


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
        cache_calls = []
        observed_calls = 0
        metering = {name: [] for name in ("input_tokens", "output_tokens", "total_tokens")}
        for row in rows:
            usages = [attempt.get("usage") or {} for attempt in row["attempts"]]
            observed_calls += len(usages)
            cache_calls.extend(meter for usage in usages if (meter := prompt_cache_meter(usage)) is not None)
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
                       "raw_model_semantic_correct": sum(r.get('first_candidate', {}).get('raw_model_candidate', {}).get('semantic', {}).get('status') == 'verified' for r in rows),
                       "raw_model_native_evaluated_runs": sum(r.get('first_candidate', {}).get('raw_model_candidate', {}).get('semantic', {}).get('status') in {'verified', 'failed'} for r in rows),
                       "native_parameter_rebindings": sum(sum(p.get('binding_mode') == 'native_read_parameter_rebinding'
                            for p in r.get('first_candidate', {}).get('Core_binding', {}).get('receipts', [])) for r in rows),
                       "model_calls": sum(r.get("model_calls", len(r["attempts"])) for r in rows),
                       "median_end_to_end_ms": statistics.median(durations),
                       "p95_end_to_end_ms": durations[max(0, (95*len(durations)+99)//100-1)],
                       "reasoning_usage_known_runs": len(reasoning),
                       "median_reasoning_tokens": statistics.median(reasoning) if reasoning else None,
                       'prompt_cache': {
                           'known_calls': len(cache_calls), 'unreported_or_invalid_calls': observed_calls - len(cache_calls),
                           'hit_tokens': sum(m['hit_tokens'] for m in cache_calls) if cache_calls else None,
                           'reported_miss_tokens': (sum(m['miss_tokens'] for m in cache_calls)
                               if cache_calls and all(m['miss_tokens'] is not None for m in cache_calls) else None),
                           'measured_input_tokens': sum(m['input_tokens'] for m in cache_calls) if cache_calls else None,
                           'input_token_hit_rate': (sum(m['hit_tokens'] for m in cache_calls)
                               / sum(m['input_tokens'] for m in cache_calls) if cache_calls else None),
                       },
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
    binding_pairs = {}
    for row in records:
        if row['arm'] in BINDING_ARMS:
            binding_pairs.setdefault((row['case_id'], row.get('repeat', 0)), {})[row['arm']] = row
    binding_contrasts = []
    for (case_id, repeat), pair in sorted(binding_pairs.items()):
        if not all(arm in pair for arm in BINDING_ARMS):
            continue
        baseline, treatment = (pair[arm] for arm in BINDING_ARMS)
        binding_contrasts.append({'case_id': case_id, 'repeat': repeat,
            'baseline_usable': baseline.get('first_candidate', {}).get('usable', False),
            'treatment_usable': treatment.get('first_candidate', {}).get('usable', False),
            'treatment_minus_baseline_ms': treatment['end_to_end_ms'] - baseline['end_to_end_ms'],
            'binding_reference_used': any(p.get('binding_mode') == 'operation_reference'
                for p in (treatment.get('operation_binding') or {}).get('receipts', [])),
            'native_parameters_rebound': any(p.get('binding_mode') == 'native_read_parameter_rebinding'
                for p in (baseline.get('operation_binding') or {}).get('receipts', []))})
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
            "paired_results": matched, 'binding_contrasts': binding_contrasts,
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
    parser.add_argument('--require-reviewed-effects', action='store_true',
                        help='Refuse to measure a partial selection of the reviewed effect forms')
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.cases)
        if args.cmp_relations:
            cases = [c for c in cases if c.get('evaluation',{}).get('opcode')=='CMP']
            if not cases: raise ValueError('CMP factorial requires operand-mapping CMP cases')
            if any(c.get('plc_model','FX3U')!='FX3U' or not c['evaluation'].get('cmp_behavior') for c in cases):
                raise ValueError('CMP factorial requires FX3U cases with independent behavior truth traces')
            args.arms=','.join(FACTORIAL_ARMS)
        tasks = schedule(cases, args.arms.split(","), args.repeat, args.seed,
                         blocked=args.cmp_relations or set(BINDING_ARMS).issubset(args.arms.split(',')))
        case_coverage = reviewed_effect_case_coverage(cases) if args.require_reviewed_effects else None
        if case_coverage and case_coverage['missing']:
            raise ValueError('Independent cases are missing for reviewed forms: ' +
                             ', '.join(row['target_model'] + ':' + row['opcode'] for row in case_coverage['missing']))
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
    if args.preflight or set(PAIRED_ARMS).issubset(args.arms.split(",")) or set(BINDING_ARMS).issubset(args.arms.split(',')):
        preview = preflight_factorial if args.cmp_relations else preflight_pairs
        if set(BINDING_ARMS).issubset(args.arms.split(',')):
            preview = preflight_binding
        preflight = preview(cases, provider=provider, model=args.model, evidence_cache=evidence_cache)
        if case_coverage is not None:
            preflight['definition_case_coverage'] = case_coverage
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
    if case_coverage is not None:
        summary['definition_case_coverage'] = case_coverage
    descriptor = os.open(args.output.with_suffix(".summary.json"), os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(sanitize(summary), stream, ensure_ascii=False, allow_nan=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
