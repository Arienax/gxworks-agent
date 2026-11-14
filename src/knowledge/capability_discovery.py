# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Functional queries over the existing retriever and source-owned catalogue."""
from __future__ import annotations

import re
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from knowledge.evidence import evidence_record


# A process can need to retain several pending events without specifying how
# they are stored or advanced. Recall both region history and ordered records.
_TRACKED_EVENTS = re.compile(
    r"(?:跟踪|追踪)(?:工件|物料|任务|事件)|连续发料|"
    r"(?:同时|连续|多件|多个|多笔).{0,12}(?:物料|工件|任务|事件|请求)|"
    r"(?:物料|工件|任务|事件|请求).{0,12}(?:跟踪|追踪|在途|并发)|"
    r"\bmultiple\s+(?:items|workpieces|objects|events|requests)\b|\bin.flight\s+(?:items|events|requests)\b", re.I)
_RANGE_SHIFT_REQUEST = (r"移位寄存器|移位链|(?:位|字|软元件|寄存器)(?:区|范围|块).{0,6}移位|"
                        r"shift\s+register|(?:bit|word|device)\s+(?:range|block|region)\s+shift")
_WATCHDOG_REFRESH = (r"(?:刷新|复位|重置|清零).{0,8}看门狗|看门狗.{0,8}(?:刷新|复位|重置|清零)|"
                     r"(?:refresh|reset|kick).{0,20}watchdog|watchdog.{0,12}(?:refresh|reset)")
# Search the broader indexed concept, then check the narrower effect in the
# source packet. Index labels are recall hints, never classification evidence.
_RECALL_FUNCTION = {'range_shift': 'shift'}

# Concepts expand vocabulary, never map a requirement to a mandated opcode.
FUNCTION_TERMS = (
    ("shift", r"移位|位移|bit\s+shift|word\s+shift", "bit shift left right word shift"),
    ("range_shift", _RANGE_SHIFT_REQUEST, "bit word block shift register left right source insertion"),
    ("queue", r"队列|先进先出|先入先出|\bqueue\b|\bfifo\b|first.in.first.out", "FIFO queue enqueue dequeue read write"),
    ("bulk_reset", r"批量.{0,12}(?:清|复位)|(?:清空|清零|清除|复位|初始化).{0,20}(?:范围|连续|全部|所有|数组|状态)|(?:全部|所有|范围|连续|数组).{0,16}(?:清空|清零|清除|复位|初始化)|zone reset|(?:bulk|all|range|array).{0,10}(?:reset|clear|initialize)|(?:clear|reset|initialize).{0,10}(?:bulk|all|range|array)", "zone reset clear range fill move"),
    ("block_move", r"块传送|块复制|批量.{0,6}(?:传送|复制)|连续.{0,8}(?:复制|传送)|数组|block move|array|copy", "block move fill move array"),
    ("timer", r"延时|延迟|计时|倒计时|定时|timer|delay|timing|\d+(?:\.\d+)?\s*(?:毫秒|秒|ms\b|s\b)", "timer on delay off delay timing"),
    ("off_delay", r"断电延时|断开延时|断开延迟|(?:消失|断开|撤销|关闭|停止|释放).{0,18}(?:延时|延迟)|(?:延时|延迟).{0,18}(?:关闭|关断|断开)|off[ -]?delay", "timer off delay falling input delayed output off"),
    ("counter", r"计数|计件|累计次数|counter|counting", "counter count up down"),
    ("latch", r"锁存|置位|复位优先|置位优先|自保持|latch|flip.flop", "latch set reset bistable"),
    ("logic", r"逻辑|与或|与门|或门|异或|取反|logic|boolean|\band\b|\bxor\b", "boolean logic and or xor not"),
    ("compare", r"比较|区间|大小判断|\d+\s*[～~至]\s*\d+\s*(?:表示|对应|判为|分为)|compare|comparison", "compare zone compare equal greater less"),
    ("loop", r"循环|遍历|迭代|\bloop\b|\biterat\w*\b|\btravers\w*\b", "program flow iteration loop repeat traverse array"),
    ("sequence", r"多阶段|交通灯|顺序控制|状态机|multi.?stage|state machine|sequence control", "state sequence step transition sequential control"),
    ("edge", r"上升沿|下降沿|边沿|每次|一次事件|rising|falling|edge", "rising falling edge pulse trigger"),
    ("rtc", r"实时时钟|RTC|校时|分钟校准|real[ -]?time\s+clock|clock\s+(?:data|setting|write)", "real time clock set RTC data minute correction four digit year"),
    ("scan_clock", r"用户.{0,8}(?:扫描)?时钟|通\s*\d+\s*个?扫描|(?:扫描数|扫描次数).{0,8}时钟|user\s+timing\s+clock|timing\s+pulse|scan\s+clock", "user timing clock timing pulse generation number of scans ON OFF"),
    ("watchdog_refresh", _WATCHDOG_REFRESH, "watchdog timer refresh reset"),
    ("socket_receive", r"接收|读取.{0,12}以太网|socket.{0,24}(?:receiv|read)|receiv.{0,24}(?:socket|connection|data)", "socket communication reading out received data during instruction execution"),
    ("counter_read", r"(?:读取|刷新).{0,16}高速计数|高速计数.{0,24}(?:读取|最新|当前值|即时刷新)|计数器.{0,12}(?:最新|当前值)|即时刷新|(?:read|refresh).{0,24}high[ -]speed\s+counter|counter.{0,16}current\s+value", "high speed counter current value read dedicated instruction immediate refresh"),
    ("counter_range_write", r"(?:写入|设置|修改).{0,20}(?:环形计数|计数范围)|(?:环形计数|ring\s+counter).{0,30}(?:上下限|范围|limits?|range)|(?:write|set).{0,24}(?:counter\s+range|lower.{0,8}upper)", "ring counter lower limit upper limit value write"),
)

_PURPOSE_TERMS = {
    "shift": r"shift|移位", "range_shift": _RANGE_SHIFT_REQUEST + r"|block shift|range shift",
    "queue": r"\bfifo\b|\bqueue\b|队列|先进先出|先入先出|first.in.first.out",
    "bulk_reset": r"reset|fill|clear|复位", "block_move": r"block|fill|array|块",
    "timer": r"timer|delay|timing|定时", "compare": r"compar|比较",
    "off_delay": r"off[ -]?delay|断电延时|断开延时|断开延迟",
    "counter": r"counter|counting|计数", "latch": r"latch|bistable|set.reset|锁存",
    "logic": r"boolean|logic|bitwise|逻辑",
    "loop": r"\bloop\b|iterat|travers|循环体|遍历|迭代|程序循环|循环程序",
    "sequence": r"sequence|sequential|state transition|step control|顺序|状态转移",
    "edge": r"edge|trigger|\bPLS\b|\bPLF\b|\bLDP\b|\bLDF\b|边沿",
    "rtc": r"RTC|real[ -]?time\s+clock|clock\s+data",
    "scan_clock": r"timing\s+pulse|user\s+timing\s+clock",
    "watchdog_refresh": _WATCHDOG_REFRESH,
    "socket_receive": r"receiv|SOCRCV",
    "counter_read": r"current\s+value\s+read|ICCNTRD",
    "counter_range_write": r"(?:lower|upper).{0,20}limit|ICRNGWR",
    "scaling": r"\bscal(?:e|ing)\b|量程换算|缩放",
    "conversion": r"\bconver(?:t|sion)\b|encod|decod|转换|编码|解码",
    "positioning": r"positioning|定位",
    "task_execution": r"first[ _-]scan|task|interrupt|首扫|任务|中断",
}

# The PLC scan and a closed control loop are execution/process facts, not a
# request for program iteration. Cyclic redundancy is another distinct use of
# "cyclic". Match source meaning rather than excluding individual opcodes.
_NON_ITERATION = re.compile(
    r"循环扫描|扫描循环|闭环|(?:control|closed[ -]?loop)\s+loop|closed[ -]?loop"
    r"|cyclic\s+redundancy|(?:cyclic\s+)?scan(?:ning)?\s+(?:cycle|loop)"
    r"|(?:cycle|loop|cyclic)\s+scan(?:ning)?", re.I)


def _conflicts_with_purpose(function, text):
    text = str(text or "")
    if function == "loop" and _NON_ITERATION.search(text):
        return True
    if function == "queue" and (re.search(r"\b(?:FILO|LIFO)\b|先进后出|后进先出", text, re.I)
            and not re.search(r"\bFIFO\b|先进先出|先入先出", text, re.I)):
        return True
    return False


def _matches_purpose(function, text):
    text = str(text or "")
    if _conflicts_with_purpose(function, text):
        return False
    from knowledge.query_vocabulary import purpose_terms
    pattern = _PURPOSE_TERMS.get(function)
    if pattern is None:
        terms = purpose_terms(function)
        pattern = '|'.join(re.escape(term) for term in terms) if terms else r'(?!)'
    return bool(re.search(pattern, text, re.I))


# Some primary effects use words also found in a different purpose (e.g. a
# clock comparison is not a comparison of arbitrary PLC values).
_EFFECT_PURPOSES = {
    "shift": {"shift"},
    "block_shift_with_source_insertion": {"shift", "range_shift"},
    "range_copy": {"block_move"},
    "scan_count_pulse": {"scan_clock"},
    "generate_scan_timing_pulse": {"scan_clock"},
    "watchdog_refresh": {"watchdog_refresh"},
    "reset_watchdog_timer": {"watchdog_refresh"},
    "time_comparison": {"rtc"},
    "time_zone_comparison": {"rtc"},
    "high_speed_zone_compare": set(),
    "increment": {"counter"},
    "decrement": {"counter"},
    "repeat_execution": {"loop"},
    "begin_bounded_repeat": {"loop"},
    "end_bounded_repeat": {"loop"},
}


def _instruction_purposes(packet):
    """Read operation claims, excluding incidental terms in tables and examples.

    None means the record lacks a typed purpose; an empty set means it describes
    a different operation. The latter must not be overridden by alias labels.
    """
    value = packet.get('value')
    if not isinstance(value, Mapping):
        return None
    purposes, processing = set(), set()
    described = False
    for fact in value.get('facts', ()):
        if (fact.get('status') not in {'source_verified', 'declared_unverified'}
                or not isinstance(claim := fact.get('value'), Mapping)):
            continue
        dimension = fact.get('dimension')
        operation = claim.get('operation')
        if (dimension in {'effects.documented_operation', 'effects.operation', 'effects.documented_processing'}
                and isinstance(operation, str)):
            if operation.startswith('queue_') and claim.get('order') in {'filo', 'lifo'}:
                described = True
                continue
            if operation in _EFFECT_PURPOSES:
                described = True
                purposes.update(_EFFECT_PURPOSES[operation])
            else:
                label = operation.replace('_', ' ')
                matches = {function for function in _PURPOSE_TERMS if _matches_purpose(function, label)}
                if dimension == 'effects.documented_processing':
                    processing.update(matches)
                else:
                    purposes.update(matches)
                    described |= dimension == 'effects.documented_operation' or bool(matches)
        elif dimension == 'effects.range_shift':
            purposes.update(('shift', 'range_shift'))
        elif dimension == 'effects.pulse_write' and claim.get('edge') in {'rising', 'falling'}:
            purposes.add('edge')
        elif dimension in {'effects.retention', 'effects.reset_bit'}:
            purposes.add('latch')
        elif dimension == 'parameters.device_form' and claim.get('timer_counter_parameters') == 'separate_source_facts':
            # Some native calls have several device-dependent behaviors.
            # The authored form declares which timing/counting operands exist.
            purposes.update({'T': 'timer', 'C': 'counter'}[family]
                            for family in claim.get('device_families', ()) if family in {'T', 'C'})
    # A processing detail may contain a transfer, comparison or shift used
    # internally. Only use its loose labels if the operation has no own label.
    if not described:
        purposes.update(processing)
    return purposes if described or purposes else None


def _support_matches(function, record, packet):
    kind = record.get('fact_kind')
    value = packet.get('value')
    if not isinstance(value, Mapping) or packet.get('technical_status') == 'candidate_evidence':
        return False
    if kind == 'device_family':
        family = value.get('family') or record.get('fact_target')
        return ((family == 'T' and function in {'timer', 'off_delay'})
                or (family == 'C' and function in {'counter', 'counter_read', 'counter_range_write'}))
    if kind == 'device':
        meaning = value.get('semantic_facts') or {}
        if meaning.get('function') == 'clock_pulse' and isinstance(meaning.get('period_ms'), (int, float)):
            return function in {'timer', 'off_delay', 'scan_clock'}
        return function in (record.get('semantic_functions') or ()) or meaning.get('function') == function
    return kind == 'process' and _matches_purpose(function, record.get('section'))


def functional_queries(requirement):
    from knowledge.analysis_router import positive_requirement_text
    text = positive_requirement_text(requirement)
    # A periodic scan clock is not program iteration or a time-based timer.
    if re.search(_PURPOSE_TERMS["scan_clock"], text, re.I) or re.search(r"扫描.{0,12}(?:时钟|循环)|循环时钟", text):
        text = _NON_ITERATION.sub("", text)
        text = re.sub(r"循环时钟", "时钟", text)
    intent_text = {'loop': _NON_ITERATION.sub('', text),
                   'shift': re.sub(_RANGE_SHIFT_REQUEST, '', text, flags=re.I)}
    queries = {name: 'PLC ' + expansion for name, pattern, expansion in FUNCTION_TERMS
               if re.search(pattern, intent_text.get(name, text), re.I)}
    if _TRACKED_EVENTS.search(text) and not queries.keys() & {'range_shift', 'shift', 'queue'}:
        for name, _, expansion in FUNCTION_TERMS:
            if name in {'range_shift', 'queue'}:
                queries[name] = 'PLC ' + expansion
    from knowledge.query_vocabulary import matching_entries
    for entry in matching_entries(text):
        name, terms = entry['function'], entry['terms']
        queries[name] = queries.get(name, 'PLC') + ' ' + terms
    return [{'function': name, 'query': query} for name, query in queries.items()]


def process_behavior_preferences(requirement):
    """Explicit process behavior, used to rank source-bound FBD interfaces.

    Missing detail keeps the broad category; it never invents an input edge,
    counter direction, latch priority or comparison for an underspecified task.
    """
    from knowledge.analysis_router import positive_requirement_text
    text = positive_requirement_text(requirement)
    patterns = {
        'timer:on_delay': r'接通延时|通电延时|on[ -]?delay|持续.{0,16}(?:秒|ms|s\b).{0,10}(?:接通|开启|打开|有效)|(?:接通|有效|导通).{0,8}(?:延时|延迟).{0,16}(?:接通|开启|输出)',
        'timer:off_delay': r'断电延时|断开延时|off[ -]?delay|(?:消失|断开|撤销|释放).{0,22}(?:延时|延迟|保持)|(?:延时|延迟).{0,18}(?:关闭|关断|断开)',
        'timer:pulse': r'单稳|单次脉冲|脉冲定时|pulse\s+timer|定宽脉冲',
        'edge:rising': r'上升沿|rising\s+edge',
        'edge:falling': r'下降沿|falling\s+edge',
        'counter:up_down': r'加减计数|增减计数|双向计数|up[ /-]*down\s+count',
        'counter:down': r'减计数|向下计数|递减|down\s+count',
        'counter:up': r'加计数|向上计数|递增|up\s+count',
        'latch:set_dominant': r'置位优先|set[ -]dominant',
        'latch:reset_dominant': r'复位优先|reset[ -]dominant',
        'compare:GE': r'大于等于|不小于|>=', 'compare:LE': r'小于等于|不大于|<=',
        'compare:NE': r'不等于|!=|<>',
        'compare:GT': r'(?<!不)大于(?!等于)|(?<![<>=])>(?!=)',
        'compare:LT': r'(?<!不)小于(?!等于)|(?<![<>=])<(?!=|>)',
        'compare:EQ': r'(?<!大于)(?<!小于)(?<!不)等于|(?<![<>=!])=(?!=)',
        'logic:AND': r'与门|逻辑与|全部.{0,8}(?:满足|有效)|\bAND\b',
        'logic:OR': r'或门|逻辑或|任一.{0,8}(?:满足|有效)|\bOR\b',
        'logic:XOR': r'异或|\bXOR\b', 'logic:NOT': r'取反|逻辑非|\bNOT\b',
    }
    result = {}
    for behavior, pattern in patterns.items():
        if re.search(pattern, text, re.I):
            category = behavior.split(':', 1)[0]
            result.setdefault(category, set()).add(behavior)
    if 'counter:up_down' in result.get('counter', ()):
        result['counter'] = {'counter:up_down'}
    return result


def _structured_st_instruction_card(opcode, plc_model, record, registry, *, call=None):
    """Project only an explicitly admitted, source-bound owner ST contract."""
    call = call if call is not None else registry.describe_call(opcode, cpu=plc_model)
    interfaces = call.get("interfaces", ())
    if not isinstance(interfaces, (list, tuple)):
        return None
    verified = call.get("st_contract_verified") is True
    legacy_st_only = list(interfaces) == ["st"]
    if (not (verified or legacy_st_only)
            or "st" not in interfaces
            or call.get("opcode", "").upper() != str(opcode).upper()):
        return None
    parameters = call.get("st_parameters")
    if not isinstance(parameters, list) or not parameters or not isinstance(call.get("st_signature"), str):
        return None
    # A mixed native/ST owner may expose both contracts. Keep this card's
    # target_call and source list restricted to the independently verified ST
    # contract so discovery cannot route its native operands into ST.
    st_sources = call.get("st_contract_sources")
    if st_sources is None:
        st_sources = call.get("contract_sources", ()) if legacy_st_only else ()
    st_call = call.get("st_call")
    if not isinstance(st_call, Mapping):
        return None
    st_target_call = {
        key: call[key] for key in ("target_model", "opcode", "st_signature", "st_parameters",
            "st_call", "applicability_cases", "conditions_evaluated", "source_contract_fact_ids")
        if key in call
    }
    st_target_call.update(interfaces=["st"], st_contract_verified=True,
                          contract_sources=[dict(source) for source in st_sources
                                            if isinstance(source, Mapping)])
    return {"id": "instruction:" + call["opcode"], "kind": "instruction",
            "name": call["opcode"], "family": call["opcode"],
            "purpose": record.get("section") or call["opcode"],
            "interface": [dict(parameter) for parameter in parameters],
            "execution": None, "st_call": dict(st_call),
            "target_call": st_target_call,
            "unknown": ["execution_semantics", "applicability_conditions_not_evaluated"],
            "sources": [dict(source) for source in st_sources if isinstance(source, Mapping)],
            "functions": []}


def _authored_instruction_view(registry, opcode, plc_model, target_mode):
    """Project compiled family facts with the shared instruction presentation."""
    entry = next((item for item in getattr(registry, "_family_knowledge", ())
                  if str(item.get("base_opcode") or "").upper() == opcode.upper()), None)
    if entry is None:
        return None
    from knowledge.instruction_presentation import instruction_card, model_instruction_view
    from shared.model_context import technical_value
    packet = {"kind": "instruction", "target": opcode, "status": "source_verified",
              "value": {"facts": entry.get("facts") or [],
                        "shared_values": entry.get("shared_values") or {}}}
    view = model_instruction_view(instruction_card(packet, technical_value))
    value = view.get("value") if isinstance(view, dict) else None
    if not isinstance(value, dict):
        return None
    language = "st" if target_mode == "st" else "ladder"
    tables = [table for table in value.get("parameter_tables") or ()
              if plc_model in ((table.get("scope") or {}).get("models") or [plc_model])
              and ((table.get("scope") or {}).get("languages") or [language]) == [language]]
    native = [table for table in tables
              if table.get("representation") == "native_ladder_instruction"] or tables
    if native:
        value["parameter_tables"] = native[:1]
    return value


def _instruction_card(opcode, plc_model, record, *, target_mode=None):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    registry = DEFAULT_INSTRUCTION_REGISTRY
    if target_mode == "st":
        st_call = registry.describe_call(opcode, cpu=plc_model)
        if (st_call.get("st_contract_verified") is True
                or st_call.get("interfaces") == ["st"]):
            # Resolve the independent source contract before looking at any
            # same-name Native form. A malformed or stale verified projection
            # stays unavailable instead of falling through to a different ABI.
            return _structured_st_instruction_card(opcode, plc_model, record, registry,
                                                   call=st_call)
    form = registry.resolve_form(opcode, cpu=plc_model)
    if form is None and target_mode != "ladder":
        form = registry.resolve_st_call(opcode, cpu=plc_model)
    if form is None or not form.spec.supports_cpu(plc_model):
        return None
    opcode = form.opcode
    spec = form.spec
    from plc.instructions import GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES
    if spec.category in GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES:
        return None
    coverage = spec.contract_coverage()
    materials = spec.source_materials
    # A source document ID locates evidence; its page suffix is not a purpose.
    title = record.get('section') or opcode
    card = {"id": "instruction:" + opcode, "kind": "instruction", "name": opcode,
            "family": form.base_mnemonic, "purpose": title,
            "interface": [{"name": o.name, "role": o.role, "type": o.data_type,
                           **({"allowed_devices": list(o.allowed_devices)} if o.allowed_devices else {}),
                           **({"word_count": o.word_count} if o.word_count != 1 else {})}
                          for o in spec.operands],
            "execution": spec.execution_form,
            **({"st_call": dict(spec.st_call)} if spec.st_call else {}),
            **({"target_call": DEFAULT_INSTRUCTION_REGISTRY.describe_call(opcode, cpu=plc_model)}
               if spec.st_call and spec.contract_sources else {}),
            "unknown": [k for k, status in coverage.items() if status != "source_verified"],
            "sources": [evidence_record(record)] if record.get("id") else [
                {"manual_id": m["manual_id"], "revision": m["revision"], "pdf_pages": m["pdf_pages"]}
                for m in materials[:1]] or [
                {k: s[k] for k in ("manual_id", "revision", "pdf_page", "target_model") if k in s}
                for s in spec.contract_sources[:1]], "functions": []}
    # Complete facts are resolved after selection through the scoped retriever.
    # Loading an unscoped family view for every search hit duplicates that work.
    return card


def discover(requirement, *, plc_model, target_mode, explicit_targets=(), catalog=(), retrieve, lookup=None,
             relation_records=(), relation_limit=0):
    from knowledge.structured_facts import authored_packet
    queries = functional_queries(requirement)
    candidates, seen, gaps = [], {}, []
    supporting_evidence = {}
    source_records = list(relation_records)

    def add(card, function):
        if not card:
            return
        key = card["id"]
        if key not in seen:
            seen[key] = card
            candidates.append(card)
        if function not in seen[key]["functions"]:
            seen[key]["functions"].append(function)

    for target in explicit_targets or ():
        op = str(target.get("opcode") or "")
        card = _instruction_card(op, plc_model, {}, target_mode=target_mode)
        if target_mode != "fbd":
            add(card, "explicit")
        if card is None:
            gaps.append({"function": "explicit", "target": op, "reason": "not_available_in_registry_scope"})
    def recall(query):
        from knowledge.evidence import KnowledgeQuery
        function = query['function']
        recall_function = _RECALL_FUNCTION.get(function, function)
        scoped_query = KnowledgeQuery(query['query'], metadata={'semantic_function': recall_function})
        return retrieve(scoped_query, plc_model=plc_model, task_type="generate",
                        top_k=64, char_budget=200000, source_lanes=("fact", "example"))

    if len(queries) > 1:
        with ThreadPoolExecutor(max_workers=min(4, len(queries)), thread_name_prefix="plc-discovery") as executor:
            pending = [executor.submit(copy_context().run, recall, query) for query in queries]
            query_hits = [result.result() for result in pending]
    else:
        query_hits = [recall(query) for query in queries]

    ranked = []
    purposes = {}
    aliases = set()
    for query, hits in zip(queries, query_hits):
        function = query['function']
        recall_function = _RECALL_FUNCTION.get(function, function)
        ranked.append((function, hits))
        for record in hits:
            opcode = str(record.get('instruction_opcode') or '').upper()
            if not opcode or target_mode == 'fbd':
                continue
            matched = _instruction_purposes(authored_packet(record))
            if matched is not None:
                purposes.setdefault(opcode, set()).update(matched)
            elif (recall_function in (record.get('semantic_functions') or ())
                  or _matches_purpose(recall_function, record.get('section'))):
                aliases.add(opcode)
    if lookup and (unclassified := sorted(aliases - purposes.keys())):
        # An alias can rank ahead of the exact owner. Consult its source claims
        # before rendering a contract, without loading every recalled owner.
        for record in lookup(unclassified):
            matched = _instruction_purposes(authored_packet(record))
            if matched is not None:
                purposes.setdefault(record['instruction_opcode'], set()).update(matched)

    for function, hits in ranked:
        before = set(seen)
        for record in hits:
            op = str(record.get("instruction_opcode") or "").upper()
            if record.get('fact_kind') in {'device', 'device_family', 'process'}:
                if (record.get('technical_status') != 'candidate_evidence'
                        and _support_matches(function, record, authored_packet(record))):
                    supporting_evidence.setdefault(str(record['id']), record)
                continue
            if _conflicts_with_purpose(function, record.get('section')):
                continue
            if op in purposes:
                matched = function in purposes[op]
            else:
                matched = (function in (record.get('semantic_functions') or ())
                           or _matches_purpose(function, record.get('section')))
            if not matched:
                continue
            source_records.append(record)
            if op and target_mode != "fbd":
                if 'instruction:' + op in seen:
                    add(seen['instruction:' + op], function)
                    continue
                card = _instruction_card(op, plc_model, record, target_mode=target_mode)
                if card and target_mode == "st" and not card.get("st_call"):
                    card["unknown"].append("language_specific_call_signature")
                add(card, function)
            elif record.get("chunk_type") in {"example_st", "example_csv", "design_pattern"}:
                language = "st" if record["chunk_type"] == "example_st" else "ladder"
                if language == target_mode:
                    add({"id": "pattern:" + str(record["id"]), "kind": "pattern",
                         "name": record.get("section"), "purpose": record.get("section"),
                         "interface": [], "execution": None, "unknown": ["process_applicability"],
                         "sources": [evidence_record(record)], "functions": []}, function)
        if set(seen) == before and not any(function in c["functions"] for c in candidates):
            gaps.append({"function": function, "reason": "no_scoped_manual_match"})
    entries = catalog.get("nodes", catalog.get("templates", catalog.get("catalog", []))) if isinstance(catalog, Mapping) else catalog
    if isinstance(entries, Mapping):
        entries = list(entries.values())
    for index, entry in enumerate(entries or ()):
        kind = str(entry.get("kind") or "")
        if kind not in {"function", "function_block", "FUNCTION", "FUNCTION_BLOCK"}:
            continue
        name = entry.get("type_name") or entry.get("symbol") or entry.get("name")
        identity = entry.get("template") or entry.get("qualified_name") or name
        # Distinct prototypes and colliding names must not collapse to one owner.
        key = "project:" + str(identity) + ":" + str(entry.get("prototype_offset", index))
        add({"id": key, "kind": "project_callable", "name": name,
             "purpose": entry.get("description") or "当前工程目录中的可调用对象",
             "interface": [{k: p[k] for k in ("name", "role", "side", "data_type", "type") if k in p}
                           for p in entry.get("ports", [])],
             "execution": None, "unknown": ["body_behavior_unverified"],
             "sources": [{"kind": "current_project_catalog", "identity": identity},
                         *([dict(entry['origin'])] if entry.get('origin') else [])],
             "origin": dict(entry.get('origin') or {'kind': 'unknown'}),
             "template": identity,
             "functions": []}, "project_catalog")
        card = seen[key]
        if entry.get("description_source"):
            card["sources"].append(dict(entry["description_source"]))
        if name and re.search(r"(?<![A-Za-z0-9_])" + re.escape(str(name)) + r"(?![A-Za-z0-9_])", str(requirement), re.I):
            add(card, "explicit")
        for query in queries:
            # A name alone is not proof of a user FB's behavior. Match the
            # supplied description only; exact calls preserve all owners.
            if (query['function'] in entry.get('semantic_functions', ())
                    or entry.get('description_source') and _matches_purpose(query["function"], entry.get("description"))):
                add(card, query["function"])
    if target_mode == "fbd":
        names = {str(c["name"]).upper() for c in candidates if c["kind"] == "project_callable"}
        for target in explicit_targets or ():
            if str(target.get("opcode") or "").upper() not in names:
                gaps.append({"function": "explicit", "target": target.get("opcode"),
                             "reason": "not_in_current_language_catalog"})
    result = {"version": "functional-discovery-v1", "target_mode": target_mode,
            "functions": [q["function"] for q in queries], "queries": queries,
            "candidates": candidates, "gaps": gaps, "model_calls": 0,
            "supporting_evidence": list(supporting_evidence.values())}
    if relation_limit:
        from knowledge.function_relations import annotate_source_records, expand_relations
        expanded = expand_relations(candidates, annotate_source_records(source_records), plc_model=plc_model,
            target_mode=target_mode, retrieve=retrieve,
            instruction_card=lambda opcode, cpu, record: _instruction_card(
                opcode, cpu, record, target_mode=target_mode), limit=relation_limit)
        candidates.extend(expanded.pop("additions"))
        result.update(expanded)
    return result
