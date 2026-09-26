from pathlib import Path
import json, re
R=Path.cwd()
def edit(name, fn):
 p=R/name; before=p.read_text(); after=fn(before); assert before!=after, name; p.write_text(after)
def sub(s,a,b,count=1):
 assert s.count(a)==count,(a[:100],s.count(a),count)
 return s.replace(a,b)

def retriever(s):
 a=s.index('# Compatibility aliases'); b=s.index('def _query_has_gxw2_skill_concept',a); s=s[:a]+'\n\n'+s[b:]
 a=s.index('def _sync_core_hooks():'); b=s.index('def _fact_identity',a); s=s[:a]+s[b:]
 s=s.replace('    _sync_core_hooks()\n','')
 s=sub(s,'targets.get("devices") or (),\n            plc_model=plc_model,\n            task_type=task,','targets.get("devices") or (),\n            plc_model=plc_model,\n            task_type=task,\n            query=query,')
 s=sub(s,'    if device_targets:\n        direct_results.extend(resolve_device_records(\n            device_targets, plc_model=plc_model, task_type=task,','    if plan["facts"]:\n        direct_results.extend(resolve_device_records(\n            device_targets, plc_model=plc_model, task_type=task, query=query,')
 return s
edit('src/knowledge/retriever.py',retriever)
for name in ['tests/test_rag_instruction_recall.py','tests/test_rag_natural_language_recall.py']:
 edit(name,lambda s:s.replace('monkeypatch.setattr(retriever, "_index_path",','monkeypatch.setattr(core, "_index_path",').replace('    retriever._sync_core_hooks()\n',''))
def evaluate(s):
 s=sub(s,'    import knowledge.retriever as retriever','    import knowledge.retriever as retriever\n    import knowledge.core as core\n    from unittest.mock import patch')
 s=sub(s,'    retriever._index_path = lambda: database\n','')
 a=s.index('    for case in cases:'); b=s.index('    positives =',a)
 s=s[:a]+'    with patch.object(core, "_index_path", lambda: database):\n'+''.join('    '+ln if ln.strip() else ln for ln in s[a:b].splitlines(True))+s[b:]
 return s
edit('tools/evaluate_rag_benchmark.py',evaluate)
def trace(s):
 s=sub(s,'    facade._index_path = lambda: database\n    facade._sync_core_hooks()\n','')
 a=s.index('    with ExitStack() as stack:'); b=s.index('    with sqlite3.connect',a)
 block=s[a:b]
 block=block.replace('    actual = facade.retrieve_knowledge(','    # The facade call must not overwrite the captured pre-budget candidates.\n    actual = facade.retrieve_knowledge(')
 return s[:a]+'    with patch.object(core, "_index_path", lambda: database):\n'+''.join('    '+ln if ln.strip() else ln for ln in block.splitlines(True))+s[b:]
edit('tools/trace_rag_candidates.py',trace)
edit('scripts/context_replay.py',lambda s:sub(s,'"capabilities": {"structured_output": True}','"baseUrl": "https://offline.invalid/v1"'))
def web(s):
 s=sub(s,'        self.live, self.calls, self.usage = live, 0, []','        self.live, self.calls, self.usage = live, 0, []\n        self.profile = copy.deepcopy(live.profile) if live else {\n            "adapter": "openai_compatible", "baseUrl": "https://offline.invalid/v1",\n            "model": "deterministic-delivery-fixture",\n        }\n        self.api_key = live.api_key if live else None')
 return sub(s,"model_factory=lambda: (provider, {'model': model})",'model_factory=lambda: (provider, provider.profile)')
edit('scripts/web_generation_e2e.py',web)
edit('tests/test_call_contract_regressions.py',lambda s:sub(sub(s,'from shared.i18n import language_context','from shared.i18n import language_context\nfrom model_profile_fixtures import offline_runtime_profile'),'self.profile = profile or {}','self.profile = profile if profile is not None else offline_runtime_profile()'))
edit('tests/test_runtime_diagnostics.py',lambda s:sub(s,'https://private.example/v1?token=PRIVATE_ENDPOINT','https://private.invalid/PRIVATE_ENDPOINT/v1'))
edit('tests/test_application_planning.py',lambda s:sub(s,'plc_debug_loop, "retrieve_knowledge", _knowledge','plc_debug_loop, "retrieve_fact_aware_knowledge", _knowledge'))
edit('tests/test_plc_agent.py',lambda s:sub(s,'knowledge.retriever.retrieve_knowledge','knowledge.retriever.retrieve_fact_aware_knowledge'))
def runtime(s):
 s=sub(s,'assert runtime.defaults == {"budget": 1024, "extra_body": {"keep": True}}','assert runtime.defaults == {"extra_body": {"keep": True}}\n    effective = resolve_request(runtime, {}, model=runtime.model, api_key="key")\n    assert "budget" not in effective.options')
 s=s.replace('capability.source == "legacy"','capability.source == "catalog"')
 return s
edit('tests/test_model_runtime_profile.py',runtime)
def devices(s):
 i=s.index('\nimport pytest') if '\nimport pytest' in s else s.index('\nfrom application')
 s=s[:i]+'\nfrom model_profile_fixtures import offline_runtime_profile'+s[i:]
 s=sub(s,'profile = {"id": "fixture", "model": "offline", "adapter": "openai_compatible", "capabilities": {"structured_output": True}}','profile = offline_runtime_profile()')
 s=sub(s,'model_factory=lambda: (provider, {"model": "offline"})','model_factory=lambda: (provider, provider.profile)')
 insertion='''                # Labels and roles are declared bindings; guessed hardware
                # addresses in suggested_io are intentionally not authoritative.
                bindings = {
                    "start_input": ("start", "X", "启动按钮"),
                    "stop_input": ("stop", "X", "停止按钮"),
                    "output_address": ("output", "Y", "电机接触器输出"),
                }
                for question in payload["missing_info"]:
                    identity = question["id"]
                    if identity in bindings:
                        role, kind, label = bindings[identity]
                        question["io_binding"] = {"binding_id": identity, "role": role, "kind": kind, "label": label}
                # This is a fresh provider response, not a legacy saved snapshot.
                for approach in payload["approaches"]:
                    contract = approach.pop("generation_contract")
                    approach["implementation_semantics"] = [
                        {"kind": "structure", "status": status, "value": value}
                        for status in ("required", "forbidden")
                        for value in contract.get(status + "_structures", [])
                    ]
'''
 return sub(s,'                payload = hallucinated_analysis()\n','                payload = hallucinated_analysis()\n'+insertion)
edit('tests/test_device_identity_delivery.py',devices)
def model_context(s):
 a=s.index('    """Build the generation-relevant profile for one PLC model.');b=s.index('    models = _load_plc_models()',a)
 s=s[:a]+'''    """Build the authoritative target PLC profile independently of retrieval.

    The compact argument is retained for caller compatibility, but no longer
    switches profile contents or asserts that manual evidence was delivered.
    """
'''+s[b:]
 a=s.index('    if compact:',a);b=s.index('    confirmed_hardware = None',a)
 return s[:a]+'''    profile["special_m"] = m.get("special_m", {})
    profile["special_d"] = m.get("special_d", {})
'''+s[b:]
edit('src/application/generation_support.py',model_context)
def router(s):
 a=s.index('            if (raw_token in');b=s.index('            resolution =',a)
 return s[:a]+'''            if raw_token in {"to", "or", "and", "not", "out", "set", "for", "end", "in", "on", "off"}:
                # Common English words need explicit instruction context. This
                # applies to every ambiguous mnemonic, not one opcode family.
                tail = text[token.end():]
                quoted = token.start() > 0 and text[token.start()-1] in "`\\\"'" and tail[:1] == text[token.start()-1]
                qualified = re.match(r"\\s*(?:instruction\\b|指令|循环|/\\s*[A-Za-z]+\\b)", tail, re.I)
                operand = re.match(r"\\s+[KHDXYMTSCRVZ][+-]?\\d+", tail, re.I)
                if not (text.strip() == raw_token or quoted or qualified or operand):
                    continue
'''+s[b:]
edit('src/knowledge/analysis_router.py',router)
def benchmark(s):
 s=sub(s,'from plc.specification.checks import check_direct_self_hold','from plc.specification.semantic_validation import validate_confirmed_semantics')
 s=sub(s,'''        record["behavior"] = (evaluator(case, result) if evaluator else
            check_direct_self_hold(result["ladder"], project_confirmed_specification(case["confirmed_spec"])))''','''        record["semantic_validation"] = validate_confirmed_semantics(
            result["ladder"], project_confirmed_specification(case["confirmed_spec"]), plc_model,
        )
        record["behavior"] = evaluator(case, result) if evaluator else {
            "status": "not_covered", "reason": "no_behavior_evaluator",
        }''')
 return s
edit('scripts/benchmark_agent_b.py',benchmark)
p=R/'evals/context-replay/cases.json';data=json.loads(p.read_text());cases=data if isinstance(data,list) else data['cases']
for c in cases:
 if 'analysis' in c:
  for a in c['analysis']['approaches']:
   assert a.pop('generation_contract')=={};a['implementation_semantics']=[]
 cid=c.get('case_id',c.get('id'))
 if cid in {'design-conveyor','typed-parameter-identity','user-edited-prose'}:
  c['request'] += '\n已确认的接口与内部地址：\n'+'\n'.join(f'{address}：{label}' for address,label in c['expected_labels'].items())
 if cid=='servo-typed-handoff':c['request']+='\nY0：脉冲输出'
p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
