"""Exact PLC fact lookup from structured index tables.

This module is deliberately separate from the broad hybrid retriever. Once a
PLC entity is explicit (instruction, device, error code), resolution is a
deterministic SQLite lookup. BM25/dense ranking may still answer residual prose,
but it never decides which record represents the explicit fact.
"""
from __future__ import annotations

from knowledge import index_access
from knowledge.fact_queries import (
    positioning_targets,
    query_is_timer_preset,
    timer_range_evidence,
)
from knowledge.index_access import (
    TEXT_COLUMNS,
)
from knowledge.record_projection import (
    augment_structured_instruction,
    chunk_result,
)
from knowledge.scope import (
    row_in_scope,
    scope_matches,
)
from knowledge.text_matching import (
    alias_occurs,
    error_terms,
    explicit_error_terms,
    canonical_error_code,
    error_code_aliases,
    diagnostic_reference_text,
    instruction_heading_terms,
)

import copy
import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping

_VERSION = "structured-facts-v3-process-needs"
from knowledge.scope import OFFICIAL_INSTRUCTION_TYPES as _OFFICIAL_INSTRUCTION_TYPES


def structured_fact_targets(query, confirmed_spec=None, *, plc_model=None, language=None):
    """Return exact fact identities without retrieving any manual prose."""
    from knowledge.analysis_router import route_analysis_request
    from knowledge.instruction_facts import instruction_fact_targets
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.semantics import generation_process_fact_needs
    from knowledge.device_evidence import family_targets

    spec = confirmed_spec if isinstance(confirmed_spec, Mapping) else {}
    # Device/error targets come only from the current retrieval query. The
    # confirmed specification contains settled I/O bindings and required
    # devices that are generation inputs, not automatic manual fact requests.
    # Selected opcodes are different: instruction_fact_targets deliberately
    # carries required_opcodes from the confirmed generation contract.
    # Tool callers commonly prefix a question with "CPU:". L02 is also a
    # syntactically valid latch-device spelling; the explicit scope prefix is
    # metadata, not an address request that should consume retrieval slots.
    scoped_query = query
    if plc_model and re.match(r'^\s*' + re.escape(str(plc_model)) + r'\s*[:：]', str(query), re.I):
        scoped_query = re.sub(r'^\s*' + re.escape(str(plc_model)) + r'\s*[:：]\s*', '', str(query), count=1, flags=re.I)
    route = route_analysis_request(
        diagnostic_reference_text(scoped_query),
        confirmed_context=None,
        resolve_opcode=DEFAULT_INSTRUCTION_REGISTRY.resolve_fact_form,
    )
    entity_query = diagnostic_reference_text(query)
    if hasattr(query, 'metadata'):
        from knowledge.evidence import KnowledgeQuery
        entity_query = KnowledgeQuery(entity_query, precompiled=getattr(query, 'precompiled', False),
                                      metadata=query.metadata, truncated=getattr(query, 'truncated', False))
    instructions = instruction_fact_targets(entity_query, spec)
    identified = {str(row.get("opcode") or "").upper() for row in instructions}
    try:
        alias_targets = _declared_instruction_alias_targets(route.query_text, plc_model=plc_model)
    except (OSError, sqlite3.Error):
        # Alias enrichment is optional during query planning. A missing,
        # corrupt, or unhydrated index must not discard explicit identities or
        # block generation. Evidence retrieval reports storage failures at its
        # existing diagnostic boundary; it is not made successful here.
        index_access.close_thread_connection()
        alias_targets = []
    for target in alias_targets:
        if target["opcode"] not in identified:
            instructions.append(target)
            identified.add(target["opcode"])
    language_features = []
    feature_scores = {}
    query_metadata = getattr(query, 'metadata', {})
    target_language = language or (str(query_metadata.get('target_mode', '')).casefold()
                                  if isinstance(query_metadata, Mapping) else '')
    requested_languages = {target_language} if target_language in {'ladder', 'st', 'fbd'} else set()
    if re.search(r'\bFBD\b|结构化梯形图', str(query), re.I):
        requested_languages.add('fbd')
    if re.search(r'\bST\b|结构化文本', str(query), re.I):
        requested_languages.add('st')
    st_context = 'st' in requested_languages
    for feature in getattr(DEFAULT_INSTRUCTION_REGISTRY, 'language_facts', ()):
        language = feature.get('language', feature['target'].partition('.')[0].lower())
        if requested_languages and language not in requested_languages:
            continue
        for alias in feature.get('aliases', []):
            if alias == '**':
                matched = '**' in str(query) and (st_context or str(query).strip() == '**'
                    or bool(re.search(r'\bST\b|幂|运算|\d\s*\*\*\s*\d', str(query), re.I)))
            else:
                matched = alias_occurs(str(query), alias)
            if matched:
                language_features.append(feature['target'])
                feature_scores[feature['target']] = (feature['target'].count('.'), len(alias))
                break
    language_features.sort(key=lambda target: feature_scores[target], reverse=True)
    remaining_by_language, selected_features = {}, []
    feature_by_target = {feature['target']: feature for feature in getattr(DEFAULT_INSTRUCTION_REGISTRY, 'language_facts', ())}
    for target in language_features:
        feature = feature_by_target[target]
        language = feature.get('language', target.partition('.')[0].lower())
        remaining = remaining_by_language.get(language, str(query))
        aliases = feature['aliases']
        if not any(alias in remaining if alias == '**' else alias_occurs(remaining, alias) for alias in aliases):
            continue
        selected_features.append(target)
        for alias in sorted(aliases, key=len, reverse=True):
            remaining = re.sub(re.escape(alias), ' ', remaining, flags=re.I)
        remaining_by_language[language] = remaining
    language_features = selected_features
    # Cxxxx is also a counter spelling. A compiler diagnostic request carries
    # its own namespace; it must not retrieve a counter or incidental examples.
    compiler_query = bool(re.search(r'编译|警告|compiler|compil(?:e|ation)|warning|GX\s*Works\s*2', str(query), re.I)
                          and re.search(r'\bC[0-9A-F]{4,7}\b', str(query), re.I))
    errors = error_terms(str(query or ""), plc_model=plc_model)
    explicit_errors = set(explicit_error_terms(query))
    errors = [code for code in errors if code not in identified or code in explicit_errors]
    devices = list(dict.fromkeys(value.upper() for value in route.devices))
    if compiler_query:
        diagnostics = re.findall(r'\bC[0-9A-F]{4,7}\b', str(query).upper())
        devices = [device for device in devices if device not in diagnostics]
        errors = list(dict.fromkeys(diagnostics))
    return {
        "version": _VERSION,
        "instructions": copy.deepcopy(instructions),
        "devices": devices,
        "device_families": family_targets(scoped_query),
        "errors": errors,
        "language_features": language_features,
        "diagnostic_namespace": 'compiler' if compiler_query else None,
        "process": generation_process_fact_needs(spec, plc_model=plc_model, requirement=route.query_text),
        "positioning": positioning_targets(query, plc_model=plc_model, confirmed_spec=spec),
    }


def _runtime():

    path = index_access.index_path()
    identity = index_access.index_identity(path)
    if identity[0] == "missing":
        return index_access, None, None, None, {}
    connection = index_access.connection(path, identity)
    schema = index_access.schema(connection)
    meta = index_access.load_meta(connection, schema)
    return index_access, path, connection, schema, meta


def resolve_language_records(targets, *, plc_model, task_type='analysis'):
    from knowledge.fact_corpus import resolve
    return resolve('language', targets, plc_model=plc_model, task_type=task_type) or []


def resolve_positioning_records(targets, *, plc_model, task_type="analysis"):
    """Resolve an explicit wiring question without adding a broad ranking signal.

    Runtime facts use their ordinary exact owner. The chapter lookup is solely
    an offline compatibility path for indexes predating structured wiring facts;
    its complete source retains the distinction between CPU and adapter outputs.
    """
    from knowledge.fact_corpus import resolve

    requested = [target for target in targets or () if isinstance(target, Mapping)
                 and target.get("target") == "DIRECTION_OUTPUT_ASSIGNMENT"
                 and str(target.get("plc_model") or "").upper() == str(plc_model).upper()]
    if not requested:
        return []
    _index, _path, connection, schema, _meta = _runtime()
    if connection is None or schema is None:
        return []
    authored = resolve("positioning", ["DIRECTION_OUTPUT_ASSIGNMENT"],
                       plc_model=plc_model, task_type=task_type, connection=connection)
    if authored is not None:
        results = []
        for target in requested:
            for record in authored:
                scoped = copy.deepcopy(record)
                bound_context = record.get("positioning_context")
                contextual = bool(target.get("products") or target.get("hardware_context"))
                established = (not contextual or bound_context == dict(target)
                               and record.get("positioning_hardware_status") == "source_scoped")
                scoped.update(positioning_context=copy.deepcopy(dict(target)),
                              positioning_hardware_status="source_scoped" if established else "unverified")
                results.append(scoped)
        return results
    chunks = schema.get("chunks")
    if not chunks or not {"id", "section", "manual_type", "text"}.issubset(chunks["columns"]):
        return []
    rows = connection.execute(
        f"SELECT * FROM {index_access.quote_identifier(chunks['name'])} "
        "WHERE manual_type='positioning' AND section LIKE '%Assignment of Output Numbers%'"
    ).fetchall()
    results = []
    for target in requested:
        for row in rows:
            if not row_in_scope(row, plc_model, task_type):
                continue
            body = str(row["text"] or "")
            # This legacy chapter can witness literal products, but cannot
            # validate arbitrary structured hardware conditions supplied by a
            # caller. Those remain unresolved until a typed fact owns them.
            hardware_established = not target.get("hardware_context") and all(
                alias_occurs(body, product) for product in target.get("products") or ())
            for record in _chunk_results([row["id"]], plc_model=plc_model, task_type=task_type,
                                         fact_kind="positioning", fact_target=target["target"]):
                record["fact_dimensions"] = ["definition", "applicability", "hardware_distinction"]
                record["positioning_context"] = copy.deepcopy(dict(target))
                record["positioning_hardware_status"] = "source_scoped" if hardware_established else "unverified"
                record["positioning_lookup_basis"] = "legacy_official_property_section"
                results.append(record)
    return results


def authored_packet(record):
    """Read only this record's typed envelope, never interpret manual prose."""
    for line in str(record.get('text', '')).splitlines():
        if line.startswith('{'):
            try:
                packet = json.loads(line)
            except ValueError:
                continue
            if packet.get('content_origin') == 'authored_fact':
                return packet
    return {}


def _contradictory_fact_values(left, right):
    """A missing detail is not a contrary claim in another source.

    Complementary records remain separate, preserving each witness's scope and
    verification. Only fields both sources specify can establish a conflict.
    """
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return any(_contradictory_fact_values(left[key], right[key])
                   for key in left.keys() & right.keys())
    unknown = (None, 'unknown', 'unresolved', 'not_established')
    if any(value == marker for value in (left, right) for marker in unknown):
        return False
    return left != right


_MERGE_COMPARISON_IGNORED = frozenset({'id', 'sources', 'status', 'verification'})


def _merge_comparable_fact(fact):
    return {key: value for key, value in fact.items()
            if key not in _MERGE_COMPARISON_IGNORED}


class _FactMergeIndex:
    """Per-accumulator lookup; store references, not copies of the facts."""

    __slots__ = ('by_id', 'by_family', 'next_suffix', 'transient_ids')

    def __init__(self, facts):
        # Match the legacy dict's last-value/first-key-order behavior if the
        # incoming accumulator happens to contain duplicate IDs.
        self.by_id = {fact['id']: fact for fact in facts if fact.get('id')}
        self.by_family = {}
        for identity, fact in self.by_id.items():
            if isinstance(identity, str):
                family = identity.split(':variant:')[0]
                self.by_family.setdefault(family, []).append(fact)
        self.next_suffix = {}
        self.transient_ids = set()

    def begin_batch(self):
        # The legacy routine rebuilt by_id from the accumulator and excluded
        # falsy IDs. Keep such IDs visible within a batch, but not across calls.
        if not self.transient_ids:
            return
        for identity in self.transient_ids:
            self.by_id.pop(identity, None)
        if '' in self.transient_ids and '' in self.by_family:
            self.by_family[''] = [fact for fact in self.by_family['']
                                  if fact.get('id')]
        self.transient_ids.clear()

    def add(self, fact):
        identity = fact.get('id')
        self.by_id[identity] = fact
        if not identity:
            self.transient_ids.add(identity)
        if isinstance(identity, str):
            family = identity.split(':variant:')[0]
            self.by_family.setdefault(family, []).append(fact)

    def allocate_variant(self, family):
        # Preserve the smallest available suffix >= 2. Since IDs are only
        # added, the next search can resume where the previous one stopped.
        suffix = self.next_suffix.get(family, 2)
        while family + ':variant:' + str(suffix) in self.by_id:
            suffix += 1
        self.next_suffix[family] = suffix + 1
        return family + ':variant:' + str(suffix)


def _merge_fact_rows(left, incoming, *, _index=None):
    """Merge source variants without repeated owner-wide ID scans or copies.

    The optional index is owned by the accumulator. Callers combining several
    batches must reuse it until the accumulator's facts list is replaced.
    """
    index = _index if _index is not None else _FactMergeIndex(left)
    index.begin_batch()
    remap, added = {}, []
    for original in incoming:
        identity = original.get('id')
        existing = index.by_id.get(identity)
        if existing is not None:
            family = identity.split(':variant:')[0]
            siblings = index.by_family.get(family, ())
            comparable = _merge_comparable_fact(original)
            equal = next((row for row in siblings
                          if _merge_comparable_fact(row) == comparable), None)
            if equal is not None:
                if identity != equal['id']:
                    remap[identity] = equal['id']
                sources = equal.setdefault('sources', [])
                for source in original.get('sources', ()):
                    if source not in sources:
                        sources.append(copy.deepcopy(source))
                statuses = {equal.get('status'), original.get('status')}
                if 'conflict' in statuses:
                    equal['status'] = 'conflict'
                    equal['verification'] = 'not_performed'
                elif 'not_applicable' not in statuses:
                    # An equal, unreviewed witness does not invalidate an
                    # independently source-checked claim.
                    strength = ('source_verified', 'candidate_evidence', 'declared_unverified', 'unknown')
                    chosen = next((status for status in strength if status in statuses), equal.get('status'))
                    equal['status'] = chosen
                    equal['verification'] = 'source_checked' if chosen == 'source_verified' else 'not_performed'
                continue

            # Only new or genuinely distinct variants need an owned deep copy.
            fact = copy.deepcopy(original)
            fact['id'] = index.allocate_variant(family)
            remap[identity] = fact['id']
            for prior in siblings:
                same_condition = all(prior.get(k) == fact.get(k)
                                     for k in ('dimension', 'scope', 'conditions'))
                if same_condition and _contradictory_fact_values(prior.get('value'), fact.get('value')):
                    prior['status'] = fact['status'] = 'conflict'
        else:
            fact = copy.deepcopy(original)
        index.add(fact)
        added.append(fact)
    for fact in added:
        if fact.get('depends_on'):
            fact['depends_on'] = [remap.get(key, key) for key in fact['depends_on']]
    left.extend(added)


def merge_exact_records(records, *, plc_model):
    """Union applicable fact packets before ranking; catalogue rows add no slots.

    Fact scope, conditions and dependencies survive the union. Conflicting
    claims remain separate facts. A catalogue entry never downgrades an admitted
    call, and a source-language signature never grants runtime execution.
    """
    from plc.target_identity import target_members
    members = set(target_members(plc_model))
    merged, groups, fact_indexes = [], {}, {}
    for original in records:
        packet = authored_packet(original)
        if packet.get('kind') != 'instruction':
            merged.append(copy.deepcopy(original))
            continue
        scope = copy.deepcopy(packet.get('scope') or {})
        scope['models'] = sorted(members.intersection(scope.get('models', ())))
        key = (packet.get('target'), json.dumps(scope, sort_keys=True))
        if key not in groups:
            row, packet = copy.deepcopy(original), copy.deepcopy(packet)
            packet['scope'] = scope
            groups[key] = (row, packet)
            merged.append(row)
        else:
            row, accumulated = groups[key]
            left, right = accumulated.setdefault('value', {}), packet.get('value') or {}
            # Prefer a complete definition to its directory placeholder.
            if left.get('catalogue_only') and not right.get('catalogue_only'):
                accumulated['value'] = copy.deepcopy(right)
                accumulated['gaps'] = copy.deepcopy(packet.get('gaps', []))
                left = accumulated['value']
                # A directory placeholder was replaced with a new list.
                fact_indexes.pop(key, None)
            facts = left.setdefault('facts', [])
            index = fact_indexes.get(key)
            if index is None:
                index = fact_indexes[key] = _FactMergeIndex(facts)
            _merge_fact_rows(facts, right.get('facts', ()), _index=index)
            for field, value in right.items():
                if field not in left:
                    left[field] = copy.deepcopy(value)
            if right.get('call_available') is True:
                left['call_available'] = True
                if right.get('registry_contract'):
                    left['registry_contract'] = copy.deepcopy(right['registry_contract'])
            if not right.get('catalogue_only'):
                left.pop('catalogue_only', None)
            # Keep unresolved claims from substantive definitions, not obsolete
            # directory placeholders superseded by this exact definition.
            if not right.get('catalogue_only'):
                accumulated['gaps'] = [json.loads(value) for value in dict.fromkeys(
                    json.dumps(gap, sort_keys=True, ensure_ascii=False) for gap in
                    [*accumulated.get('gaps', ()), *packet.get('gaps', ())])]
        row, packet = groups[key]
        row['text'] = json.dumps(packet, ensure_ascii=False, separators=(',', ':'))
    return merged


def fact_record_coverage(record, *, limits=False):
    if record.get("fact_kind") == "positioning" and record.get("positioning_hardware_status") == "unverified":
        return False
    if record.get('fact_kind') == 'device' and record.get('device_definition_status') in {
            'unknown', 'unverified', 'conditions_missing', 'conflict'}:
        return False
    packet = authored_packet(record)
    if not packet:
        return True  # Original evidence fixtures keep their existing boundary.
    if packet.get('fact_selection', {}).get('gaps'):
        return False
    value = packet.get('value', {})
    facts = value.get('facts', [])
    if packet.get('kind') not in {'instruction', 'language'}:
        return True
    if not facts:
        return False
    required = [f for f in facts if f.get('dimension', '').startswith('constraints.')] if limits else facts
    for related in value.get('related_instructions', []):
        required.extend(f for f in related.get('facts', []) if f.get('dimension', '').startswith('constraints.'))
    if not required:
        return False if limits and packet.get('kind') == 'instruction' else True
    by_id = {f['id']: f for f in facts}
    for related in value.get('related_instructions', []):
        by_id.update({f['id']: f for f in related.get('facts', [])})
    visiting = set()
    def complete(fact):
        if fact['id'] in visiting or fact.get('status') != 'source_verified':
            return False
        if fact.get('dimension') == 'constraints.source_qualifications':
            return False
        visiting.add(fact['id'])
        okay = all(dependency in by_id and complete(by_id[dependency]) for dependency in fact.get('depends_on', []))
        visiting.remove(fact['id'])
        return okay
    return all(complete(fact) for fact in required)


_ASPECT_PATTERNS = (
    ('arity', r'参数个数|几个参数|参数数量|操作数个数|操作数量|arity|operand count|number of operands|how many'),
    ('order', r'参数顺序|操作数顺序|顺序|符号|签名|operand order|parameter order|signature'),
    ('roles', r'读写|输入输出|角色|\brole|direction'),
    ('purpose', r'含义|用途|意义|meaning|purpose|semantic name'),
    ('unit', r'单位|\bunit'),
    ('encoding', r'编码|encoding'),
    ('devices', r'允许.*软元件|哪些软元件|设备类型|operand device|device type|allowed device'),
    ('types', r'数据类型|位宽|字长|数据宽度|16\s*位|32\s*位|\btype|\bwidth|bit length'),
    ('effects', r'效果|结果|写入|读出|行为|保持|状态|effect|behaviour|behavior|state|retain|latch'),
    ('execution', r'执行条件|触发|边沿|脉冲|使能|时序|扫描|跳转|首次|禁用|trigger|execution|edge|timing|pulse|scan|first execution|disable|jump'),
    ('timing_state', r'上一(?:次)?扫描|前一扫描|上次执行|状态归属|previous\s+(?:scan|execution)|state\s+owner'),
    ('same_scan', r'同一?扫描|同一扫描周期|same[ -]scan'),
    ('first_execution', r'首次执行|首次扫描|first\s+(?:execution|scan)|startup'),
    ('disabled', r'禁用|条件撤销|disable|not\s+executed|not\s+driven'),
    ('jump', r'跳转|\bjump'),
    ('constraints', r'限制|范围|约束|注意|limit|restrict|constraint|domain|range'),
    ('st_call', r'\bST\b|结构化文本|structured text'),
)


def requested_aspects(query, *, language=None, aspects=None):
    text = str(query or '')
    explicit = [aspects] if isinstance(aspects, str) else aspects
    aspects = list(dict.fromkeys(explicit)) if explicit is not None else [
        name for name, pattern in _ASPECT_PATTERNS if re.search(pattern, text, re.I)]
    if explicit is None and language == 'st' and 'st_call' not in aspects:
        aspects.append('st_call')
    if explicit is None and language == 'fbd' and 'fbd_call' not in aspects:
        aspects.append('fbd_call')
    parameter_question = bool(re.search(r'操作数|参数|形参|operand|parameter', text, re.I))
    if parameter_question and not set(aspects) - {'st_call'}:
        aspects = [*aspects, 'arity', 'order', 'roles', 'types', 'purpose']
    if re.search(r'操作数|参数|形参|operand|parameter', text, re.I):
        if re.search(r'范围|\brange', text, re.I):
            aspects.append('operand_range')
        if re.search(r'条件|\bcondition', text, re.I):
            aspects.append('operand_condition')
    return aspects


def generation_aspects(query, *, language):
    """Generation always needs operands and behaviour as well as a call form."""
    return list(dict.fromkeys(['arity', 'order', 'roles', 'types', 'effects', 'execution',
        'constraints', *requested_aspects(query, language=language)]))


def instruction_aspect_gap_reasons(packet, aspect, *, state, model, language):
    """Keep reviewed source limits apart from unfinished extraction.

    This describes an already-incomplete answer; it never turns a source gap
    into support or treats a missing executor as missing manual knowledge.
    """
    if state in {'answered', 'not_applicable'}:
        return []
    facts = [fact for fact in (packet.get('value') or {}).get('facts', ())
             if (not fact.get('scope', {}).get('models') or model in fact['scope']['models'])
             and (not language or not fact.get('scope', {}).get('languages')
                  or language in fact['scope']['languages'])]
    family = ('parameters.' if aspect in {'arity', 'order', 'roles', 'types', 'devices', 'st_call', 'fbd_call',
                                          'purpose', 'unit', 'encoding', 'operand_range', 'operand_condition'}
              else 'execution.' if aspect in {'execution', 'timing_state', 'same_scan', 'first_execution',
                                               'disabled', 'jump'} else aspect + '.')
    by_id = {fact['id']: fact for fact in facts}
    pending = [fact['id'] for fact in facts if str(fact.get('dimension', '')).startswith(family)]
    visited, reasons = set(), set()
    reviewed_absence = {'not_stated', 'not_stated_in_this_manual', 'section_states_repeat_contexts_only'}
    allowed_kinds = {'source_not_stated', 'source_conflict', 'native_validation_pending',
                     'source_present_not_extracted', 'source_review_pending', 'applicability_unresolved'}
    while pending:
        identity = pending.pop()
        if identity in visited:
            continue
        visited.add(identity)
        fact = by_id.get(identity)
        if fact is None:
            reasons.add('dependency_unresolved')
            continue
        pending.extend(fact.get('depends_on', ()))
        value = fact.get('value') or {}
        status = fact.get('status')
        if status == 'source_verified':
            continue
        explicit = value.get('gap_kind') or value.get('source_disposition')
        if isinstance(explicit, str) and explicit in allowed_kinds:
            reasons.add(explicit)
        elif status == 'conflict' or value.get('conflict'):
            reasons.add('source_conflict')
        elif (isinstance(value.get('basis'), str) and value['basis'] in reviewed_absence
              or str(fact.get('dimension', '')).endswith('.unresolved_source')):
            reasons.add('source_not_stated')
        elif value.get('unresolved_items') or value.get('full_source_relation_complete') is False:
            reasons.add('source_present_not_extracted')
        else:
            reasons.add('source_review_pending')
    if not reasons:
        reasons.add('source_present_not_extracted' if state == 'missing' else 'dependency_or_scope_unresolved')
    return sorted(reasons)


def _aspect_state(packet, aspect, *, model, language, query):
    """Require every requested slot and the shared atomic dependency closure."""
    from plc.instruction_definition import DefinitionError, select_fact_dependencies
    if packet.get('fact_selection', {}).get('gaps'):
        return 'unresolved'
    value = packet.get('value') or {}
    # Owners can merge several source packets.  A different CPU's unresolved
    # signature must not replace the requested CPU's known declaration.
    value = {**value, 'facts': [fact for fact in value.get('facts', ())
                               if not fact.get('scope', {}).get('models')
                               or model in fact['scope']['models']]}
    def known_value(value):
        if isinstance(value, Mapping):
            return bool(value) and all(known_value(part) for part in value.values())
        if isinstance(value, list):
            return all(known_value(part) for part in value)
        return value is not None and (not isinstance(value, str) or value.casefold() not in
                                     {'', 'unknown', 'unresolved', 'not_established'})
    language = 'st' if aspect == 'st_call' else 'fbd' if aspect == 'fbd_call' else language
    cpu_availability = []
    for fact in value.get('facts', ()):
        if fact.get('dimension') not in {'constraints.cpu_availability', 'identity.applicability'}:
            continue
        scope = fact.get('scope', {})
        if (fact.get('conditions') or scope.get('models') and model not in scope['models']
                or scope.get('languages') and language not in scope['languages']):
            continue
        claim = fact.get('value', {})
        state = claim.get('model_states', {}).get(model)
        available = claim.get('available') if state is None else {'supported': True, 'unsupported': False}.get(state)
        if type(available) is bool and fact.get('status') == 'source_verified':
            cpu_availability.append((fact, available))
    if cpu_availability and any(not available for _, available in cpu_availability):
        if any(available for _, available in cpu_availability):
            return 'unresolved'
        try:
            closure = select_fact_dependencies(value['facts'], [f['id'] for f, _ in cpu_availability],
                opcode=packet.get('target') or value.get('opcode'), model=model, language=language)
        except (DefinitionError, KeyError, TypeError, ValueError):
            return 'unresolved'
        return 'not_applicable' if closure['source_verification_complete'] else 'unresolved'
    if language:
        availability = [f for f in value.get('facts', ())
                        if f.get('dimension') == 'identity.language_availability'
                        and f.get('value', {}).get('language') == language
                        and not f.get('conditions')]
        if any(f.get('value', {}).get('available') is False for f in availability):
            if not all(f.get('value', {}).get('available') is False for f in availability):
                return 'unresolved'
            try:
                closure = select_fact_dependencies(value['facts'], [f['id'] for f in availability],
                    opcode=packet.get('target') or value.get('opcode'), model=model, language=language)
            except (DefinitionError, KeyError, TypeError, ValueError):
                return 'unresolved'
            return 'not_applicable' if closure['source_verification_complete'] else 'unresolved'
        # A manual can explicitly decline a function/block signature without
        # providing a separate identity fact. Its call parameters then do not
        # exist in this language. Literal ST operators are different: they
        # retain expression operands even though there is no function call.
        parameter_aspects = {'arity', 'order', 'roles', 'types', 'devices', 'st_call', 'fbd_call',
                             'purpose', 'unit', 'encoding', 'operand_range', 'operand_condition'}
        signatures = [f for f in value.get('facts', ())
                      if f.get('dimension') == 'parameters.' + language + '_signature'
                      and (not f.get('scope', {}).get('languages') or language in f['scope']['languages'])]
        expressions = [f for f in value.get('facts', ())
                       if f.get('dimension') == 'parameters.language_expression'
                       and f.get('value', {}).get('available') is not False
                       and (not f.get('scope', {}).get('languages') or language in f['scope']['languages'])]
        if aspect in parameter_aspects and signatures and any(f.get('value', {}).get('available') is False for f in signatures):
            if any(f.get('status') == 'conflict' for f in signatures):
                return 'unresolved'
            if not all(f.get('value', {}).get('available') is False for f in signatures):
                return 'unresolved'
            if all(f.get('value', {}).get('available') is False for f in signatures) and (
                    aspect in {'st_call', 'fbd_call'} or not expressions):
                if any(f.get('conditions') for f in signatures):
                    return 'unresolved'
                try:
                    closure = select_fact_dependencies(value['facts'], [f['id'] for f in signatures],
                        opcode=packet.get('target') or value.get('opcode'), model=model, language=language)
                except (DefinitionError, KeyError, TypeError, ValueError):
                    return 'unresolved'
                return 'not_applicable' if closure['source_verification_complete'] and not closure['gaps'] else 'unresolved'
    documented_call = [f for f in value.get('facts', ())
                       if f.get('dimension') == 'parameters.st_signature'
                       and (not f.get('scope', {}).get('languages') or 'st' in f['scope']['languages'])]
    if aspect == 'st_call' and not documented_call:
        contract = value.get('registry_contract') or {}
        call = contract.get('st_call') or {}
        if value.get('call_available') is False:
            return 'unresolved'
        # enable describes an EN argument, including a valid signature without EN.
        if not call.get('name') or type(call.get('enable')) is not bool:
            return 'missing'
        return ('answered' if value.get('call_available') is True
                and contract.get('verification', {}).get('st_call') == 'source_verified' else 'unverified')
    if aspect == 'st_call' and documented_call:
        if any(fact.get('status') == 'conflict' for fact in documented_call):
            return 'unresolved'
        unavailable = [fact for fact in documented_call if fact.get('value', {}).get('available') is False]
        if unavailable and len(unavailable) == len(documented_call):
            try:
                closure = select_fact_dependencies(value['facts'], [fact['id'] for fact in unavailable],
                    opcode=packet.get('target') or value.get('opcode'), model=model, language='st')
            except (DefinitionError, KeyError, TypeError, ValueError):
                return 'unresolved'
            return 'not_applicable' if closure['source_verification_complete'] and not closure['gaps'] else 'unresolved'
        def complete_signature(fact):
            signature = fact.get('value') or {}
            parameters = signature.get('parameters')
            return (isinstance(parameters, list) and known_value(signature.get('returns'))
                    and type(signature.get('enable')) is bool
                    and all(parameter.get('name') and known_value(parameter.get('type'))
                            and parameter.get('direction') in {'input', 'output', 'in_out'}
                            for parameter in parameters))
        if not all(complete_signature(fact) for fact in documented_call):
            return 'missing'
    facts = [f for f in value.get('facts') or () if not language
             or not f.get('scope', {}).get('languages') or language in f['scope']['languages']]
    by_dimension = {}
    for fact in facts:
        by_dimension.setdefault(fact.get('dimension'), []).append(fact)
    prefixes = {'effects': ('effects.',), 'execution': ('execution.',),
                'constraints': ('constraints.',),
                'same_scan': ('execution.same_scan', 'effects.same_scan'),
                'first_execution': ('execution.first_execution', 'execution.startup', 'execution.mode_change'),
                'jump': ('execution.jump',)}
    aliases = {'arity': ('parameters.arity', 'parameters.typed_operands'),
               'order': ('parameters.order', 'parameters.st_signature', 'parameters.source_order', 'parameters.typed_operands'),
               'roles': ('parameters.roles', 'parameters.typed_operands'),
               'types': ('parameters.types', 'parameters.value_type', 'parameters.typed_operands'),
               'devices': ('parameters.devices', 'constraints.operand_devices'),
               'st_call': ('parameters.st_signature',),
               'fbd_call': ('parameters.fbd_signature', 'parameters.fbd_pins', 'parameters.language_expression')}
    roots, alternatives = [], []
    if aspect in {'purpose', 'unit', 'encoding', 'operand_range', 'operand_condition'}:
        arities = by_dimension.get('parameters.arity') or ()
        count = next((f.get('value', {}).get('maximum') for f in arities
                      if f.get('value', {}).get('maximum') == f.get('value', {}).get('minimum')), None)
        if type(count) is not int:
            return 'missing'
        order = (value.get('registry_contract') or {}).get('native_operand_order') or next(
            (f.get('value', {}).get('symbols') for f in by_dimension.get('parameters.order', ())), ())
        positions = [index for index, symbol in enumerate(order or (), start=1)
                     if re.search(r'(?<![A-Za-z0-9_])' + re.escape(symbol) + r'(?![A-Za-z0-9_])', str(query), re.I)]
        if positions:
            ordering = by_dimension.get('parameters.order') or by_dimension.get('parameters.source_order')
            if not ordering:
                return 'missing'
            roots.extend(f['id'] for f in ordering)
        facet = {'operand_range': 'range', 'operand_condition': 'condition'}.get(aspect, aspect)
        for position in positions or range(1, count + 1):
            slot = by_dimension.get(f'parameters.{position}.{facet}')
            if not slot:
                return 'missing'
            roots.extend(f['id'] for f in slot)
        roots.extend(f['id'] for f in arities)
    elif aspect in aliases:
        groups = [by_dimension[key] for key in aliases[aspect] if by_dimension.get(key)]
        if any(f.get('status') == 'conflict' for group in groups for f in group):
            return 'unresolved'
        for selected in groups:
            # A complete authored table can answer a slot even when an older
            # registry array is incomplete. Keep both facts in the delivery;
            # only this completeness calculation chooses a representation.
            complete = True
            for fact in selected:
                if fact.get('dimension') != 'parameters.typed_operands':
                    continue
                operands = fact.get('value', {}).get('operands')
                required = {'order': ('name', 'symbol'), 'roles': ('role', 'direction'),
                            'types': ('data_type', 'type')}.get(aspect, ())
                if (not isinstance(operands, list) or required and any(
                        not any(known_value(operand.get(key)) for key in required) for operand in operands)):
                    complete = False
            option = [f['id'] for f in selected]
            facet = {'roles': 'roles', 'types': 'types', 'devices': 'prefixes'}.get(aspect)
            if facet and any(facet in f.get('value', {}) for f in selected):
                arities = by_dimension.get('parameters.arity') or ()
                count = next((f.get('value', {}).get('maximum') for f in arities), None)
                if (type(count) is not int or any(len(f['value'][facet]) != count
                        or not known_value(f['value'][facet]) for f in selected if facet in f.get('value', {}))):
                    complete = False
                option.extend(f['id'] for f in arities)
            if complete:
                alternatives.append(option)
    elif aspect in {'timing_state', 'disabled'}:
        # A generic edge/enable fact does not answer state ownership or disabled
        # execution. Require the requested semantic slots in a verified fact.
        slots = ({'comparison_basis', 'state_owner', 'history_owner', 'update_timing', 'previous_value'}
                 if aspect == 'timing_state' else
                 {'when_disabled', 'disabled_effect', 'en_false', 'when_not_executed', 'disabled_state'})
        def has_slot(value):
            return (isinstance(value, Mapping) and (bool(slots.intersection(value))
                    or any(has_slot(child) for child in value.values()))
                    or isinstance(value, list) and any(has_slot(child) for child in value))
        selected = [fact for fact in facts if has_slot(fact.get('value'))
                    or aspect == 'disabled' and 'disabled' in str(fact.get('dimension'))]
        roots.extend(fact['id'] for fact in selected)
    else:
        selected = [f for f in facts if any(str(f.get('dimension', '')).startswith(prefix)
                                           for prefix in prefixes.get(aspect, ()))
                    and not (aspect == 'effects' and f.get('dimension') in
                             {'effects.source_read', 'effects.destination_write'})]
        roots.extend(f['id'] for f in selected)
        if not roots:
            return 'missing'
    if not roots and not alternatives:
        return 'missing'
    def closure_state(requested):
        # An unqualified manual question may refer to a language-scoped source
        # (for example EXPT's ST constraints). Use its declared common scope,
        # rather than silently imposing Ladder on every reference question.
        by_id = {fact['id']: fact for fact in facts}
        pending, visited, scopes = list(requested), set(), []
        while pending:
            identity = pending.pop()
            if identity in visited or identity not in by_id:
                continue
            visited.add(identity)
            fact = by_id[identity]
            if fact.get('scope', {}).get('languages'):
                scopes.append(set(fact['scope']['languages']))
            pending.extend(fact.get('depends_on', ()))
        languages = [language] if language else sorted(set.intersection(*scopes)) if scopes else [None]
        if not languages:
            return 'unresolved'
        closures = [select_fact_dependencies(facts, requested, opcode=packet.get('target') or value.get('opcode'),
                                             model=model, language=lang) for lang in languages]
        if any(closure['gaps'] for closure in closures):
            return 'unresolved'
        return 'answered' if all(closure['source_verification_complete'] for closure in closures) else 'unverified'
    states = []
    for option in alternatives or [roots]:
        try:
            states.append(closure_state(option))
        except (DefinitionError, KeyError, TypeError, ValueError):
            states.append('unresolved')
    return next(state for state in ('answered', 'unverified', 'unresolved') if state in states)


def _instruction_aspects(records, aspects, *, plc_model, language, query):
    from plc.target_identity import target_members
    packets = [authored_packet(row) for row in records]
    rows = []
    for aspect in aspects:
        states, reasons = [], set()
        for member in target_members(plc_model):
            applicable = [packet for packet in packets if member in packet.get('scope', {}).get('models', ())]
            # Packets with different model sets may overlap on this concrete
            # CPU. Resolve their claims together so a complete packet cannot
            # conceal a conflicting claim in another packet.
            combined = {'target': next((packet.get('target') for packet in applicable), ''), 'value': {'facts': []}}
            index = _FactMergeIndex(combined['value']['facts'])
            for packet in applicable:
                if packet.get('fact_selection', {}).get('gaps'):
                    combined.setdefault('fact_selection', {}).setdefault('gaps', []).extend(packet['fact_selection']['gaps'])
                value = packet.get('value') or {}
                _merge_fact_rows(combined['value']['facts'], value.get('facts', ()), _index=index)
                if value.get('call_available') is True or 'call_available' not in combined['value']:
                    combined['value']['call_available'] = value.get('call_available')
                    combined['value']['registry_contract'] = copy.deepcopy(value.get('registry_contract') or {})
            candidates = [_aspect_state(combined, aspect, model=member, language=language, query=query)] if applicable else []
            if candidates:
                reasons.update(instruction_aspect_gap_reasons(combined, aspect, state=candidates[0],
                    model=member, language='st' if aspect == 'st_call' else 'fbd' if aspect == 'fbd_call' else language))
            states.append('answered' if 'answered' in candidates else 'not_applicable' if candidates and all(s == 'not_applicable' for s in candidates) else 'unresolved' if 'unresolved' in candidates
                          else 'unverified' if 'unverified' in candidates else 'missing')
        state = ('not_applicable' if states and all(s == 'not_applicable' for s in states) else
                 'answered' if states and all(s in {'answered', 'not_applicable'} for s in states) else
                 'unresolved' if 'unresolved' in states else 'unverified' if 'unverified' in states else 'missing')
        rows.append({'aspect': aspect, 'status': state,
                     **({'gap_reasons': sorted(reasons)} if reasons else {})})
    return rows


def _device_range_coverage(query, records, address, *, plc_model):
    from plc.device_identity import canonical_reference_device
    from plc.target_identity import target_members
    rows = [row for row in records if isinstance(row.get('device_range'), Mapping)
            and canonical_reference_device(row['device_range'].get('address')) == address]
    if not rows:
        return None
    members = set(target_members(plc_model))
    covered = {member for row in rows for member in authored_packet(row).get('scope', {}).get('models', ())}
    aspects = []
    for aspect, pattern in (('time_base', r'时基|time\s*base|分辨率'),
                            ('range', r'编号|地址范围|number\s*range|address\s*range'),
                            ('timer_class', r'定时器类[型别]|timer\s*(?:class|type)')):
        if re.search(pattern, str(query), re.I):
            aspects.append({'aspect': aspect, 'status': 'answered' if members <= covered else 'missing'})
    definition_needed = not aspects or bool(re.search(
        r'掉电|断电|复位|使能|撤销|行为|运行|执行|Core|Simulator|reset|power.loss|enable|execution', str(query), re.I))
    if definition_needed:
        aspects.append({'aspect': 'device_definition', 'status': 'missing'})
    complete = bool(aspects) and all(item['status'] == 'answered' for item in aspects)
    return {'identity_found': members <= covered, 'range_fact': copy.deepcopy(rows[0]['device_range']),
            'aspects': aspects, 'status': 'complete' if complete else 'incomplete',
            'reason': '' if complete else 'device_definition_not_established',
            'runtime_execution': 'not_established'}


def _device_family_behavior_coverage(query, records, address, *, plc_model):
    from plc.target_identity import target_members
    rows = [row for row in records if row.get('device_family_behavior', {}).get('address') == address]
    if not rows:
        return None
    covered = {model for row in rows if authored_packet(row).get('status') == 'source_verified'
               and row['device_family_behavior'].get('selected_ranges')
               for model in authored_packet(row).get('scope', {}).get('models', ())}
    complete = set(target_members(plc_model)) <= covered
    aspects = [{'aspect': 'state_behavior', 'status': 'answered' if complete else 'unverified'},
               {'aspect': 'device_class', 'status': 'answered' if complete else 'unverified'}]
    if re.search(r'Core|Simulator|模拟器|执行器', str(query), re.I):
        aspects.append({'aspect': 'runtime_execution', 'status': 'missing'})
        complete = False
    return {'identity_found': True, 'aspects': aspects,
            'status': 'complete' if complete else 'incomplete',
            'reason': '' if complete else 'requested_facts_not_established',
            'runtime_execution': 'not_established'}


def _device_numbering_coverage(query, records, address, *, plc_model):
    """Address facts answer numbering questions, never special-device meaning."""
    numbering_question = bool(re.search(r'编号|进制|地址|越界|上限|范围|numbering|radix|address|range|limit', str(query), re.I))
    rows = [row for row in records if (row.get('device_address_rule') or {}).get('address') == address]
    if not rows:
        return None
    from plc.target_identity import target_members
    covered = {member for row in rows for member in authored_packet(row).get('scope', {}).get('models', ())}
    complete = set(target_members(plc_model)) <= covered
    meaning = not numbering_question or bool(re.search(r'含义|作用|读写权限|掉电|复位|执行|状态|meaning|purpose|permission|execution|state', str(query), re.I))
    return dict(identity_found=complete, numbering_fact=copy.deepcopy(rows[0]['device_address_rule']),
                aspects=[{'aspect': 'numbering', 'status': 'answered' if complete else 'missing'},
                         *([{'aspect': 'device_definition', 'status': 'missing'}] if meaning else [])],
                status='complete' if complete and not meaning else 'incomplete',
                reason='device_definition_not_established' if meaning else '' if complete else 'model_scope_incomplete',
                runtime_execution='not_established')


def retrieval_coverage(query, records, *, plc_model, targets=None, language=None, aspects=None,
                       delivered_records=None):
    from plc.target_identity import reference_target_identity
    plc_model = reference_target_identity(plc_model) or plc_model
    targets = targets or structured_fact_targets(query, plc_model=plc_model, language=language)
    limits = bool(re.search(r'限制|条件|范围|约束|\blimit|restrict|constraint|domain|range', str(query), re.I))
    requirements = []
    language = language or ('fbd' if re.search(r'\bFBD\b', str(query), re.I) else
                           'st' if re.search(r'\bST\b|结构化文本', str(query), re.I) else
                           'ladder' if re.search(r'梯形图|\bladder\b', str(query), re.I) else None)
    aspects = requested_aspects(query, language=language, aspects=aspects) or ['arity', 'order', 'roles', 'types', 'purpose',
                                          'effects', 'execution', 'constraints']
    def identity(kind, value):
        value = str(value or '').upper()
        if kind == 'device':
            from plc.device_identity import canonical_reference_device
            return canonical_reference_device(value)
        return canonical_error_code(value) if kind == 'error' else value
    for kind, key in (('instruction', 'instructions'), ('language', 'language_features'),
                      ('device', 'devices'), ('error', 'errors'), ('positioning', 'positioning')):
        seen = set()
        for raw in targets.get(key, []):
            target = (raw.get('opcode') or raw.get('target')) if isinstance(raw, Mapping) else raw
            if kind == 'error':
                target = canonical_error_code(target)
                if target in seen:
                    continue
                seen.add(target)
            selected = [row for row in records if row.get('fact_kind') == kind and
                        identity(kind, row.get('fact_target')) == identity(kind, target)]
            complete = any(fact_record_coverage(row, limits=limits) for row in selected)
            entry = {'kind': kind, 'target': target, 'identity_found': bool(selected),
                                 'status': 'complete' if complete else 'incomplete' if selected else 'unresolved',
                                 'reason': '' if complete else 'requested_facts_not_established' if selected else 'exact_fact_not_found'}
            if kind == 'instruction' and selected and aspects and any(authored_packet(row) for row in selected):
                aspect_rows = _instruction_aspects(selected, aspects, plc_model=plc_model,
                    language=language,
                    query=query)
                answered = all(row['status'] in {'answered', 'not_applicable'} for row in aspect_rows)
                entry.update(aspects=aspect_rows, status='complete' if answered else 'incomplete',
                             reason='' if answered else 'requested_aspects_not_established')
                value = authored_packet(selected[0]).get('value') or {}
                call = (value.get('registry_contract') or {}).get('st_call') or {}
                available = value.get('call_available')
                documented_st = _instruction_aspects(selected, ['st_call'], plc_model=plc_model,
                                                      language='st', query=query)[0]['status']
                entry.update(call_available=available, core_call_available=available,
                             runtime_execution='not_established_by_retrieval',
                             st_call_available=False if available is False else True if available is True and call.get('name') else None,
                             documented_st_call_available=(True if documented_st == 'answered' else
                                                           False if documented_st == 'not_applicable' else None),
                             st_has_enable_parameter=call.get('enable'))
            if kind == 'device':
                family_coverage = _device_family_behavior_coverage(query, records, identity(kind, target), plc_model=plc_model)
                range_coverage = family_coverage or _device_range_coverage(query, records, identity(kind, target), plc_model=plc_model)
                if range_coverage:
                    entry.update(range_coverage)
                numbering = _device_numbering_coverage(query, records, identity(kind, target), plc_model=plc_model)
                if numbering and not range_coverage and not selected:
                    entry.update(numbering)
                    selected = [row for row in records if row.get('structured_fact_requested_target') == target]
            delivered = records if delivered_records is None else delivered_records
            entry['corpus'] = 'present' if entry['identity_found'] else 'absent'
            entry['knowledge'] = entry['status']
            entry['delivery'] = ('delivered' if selected and all(any(
                row.get('id') == candidate.get('id') for candidate in delivered) for row in selected)
                else 'pending' if selected else 'not_available')
            requirements.append(entry)
    statuses = {row['status'] for row in requirements}
    return {'status': 'unresolved' if not requirements or statuses == {'unresolved'} else
            'complete' if statuses == {'complete'} else 'incomplete', 'targets': requirements,
            'omitted_evidence_is_not_unsupported': True}


def _declared_instruction_alias_targets(query, *, plc_model=None):
    """Resolve declared natural-language names, not inferred implementation intent.

    The importer already owns these aliases. Preserve their lookup when exact
    facts move out of broad RRF instead of rebuilding a prompt-specific rule.
    Incidental English prose is not an opcode or a free-form title match.
    """
    from knowledge.analysis_router import instruction_reference_text
    query = instruction_reference_text(query)
    _index, _path, connection, schema, _meta = _runtime()
    if connection is not None and _meta.get('corpus_kind') == 'authored_plc_facts_v1':
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        targets = []
        for row in connection.execute('SELECT DISTINCT alias,opcode,alias_type,plc_models FROM fact_aliases ORDER BY alias,opcode'):
            if plc_model and not scope_matches(row['plc_models'], plc_model):
                continue
            if row['alias_type'] == 'opcode' and DEFAULT_INSTRUCTION_REGISTRY.resolve_fact_form(row['alias']) is not None:
                continue
            if alias_occurs(query, row['alias']):
                targets.append({'opcode': row['opcode'], 'base_opcode': row['opcode'],
                    'target_resolution': 'declared_manual_alias', 'requested_alias': row['alias']})
        return targets
    aliases = schema.get("instruction_aliases") if schema else None
    instructions = schema.get("instructions") if schema else None
    if not connection or not aliases or not instructions:
        return []
    if not {"alias", "alias_type", "instruction_id"}.issubset(aliases["columns"]) or not {"id", "opcode"}.issubset(instructions["columns"]):
        return []
    rows = connection.execute(
        f"SELECT DISTINCT a.alias,i.opcode,a.alias_type FROM {index_access.quote_identifier(aliases['name'])} a "
        f"JOIN {index_access.quote_identifier(instructions['name'])} i ON i.id=a.instruction_id "
        "WHERE a.alias_type IN ('zh_alias','opcode') ORDER BY a.alias,i.opcode"
    )
    targets = []
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    for row in rows:
        alias, opcode = str(row["alias"] or ""), str(row["opcode"] or "").upper()
        if row["alias_type"] == "opcode":
            # Known forms already passed the context-aware query router. Only
            # enrich missing source symbols, such as an ST-specific spelling.
            if DEFAULT_INSTRUCTION_REGISTRY.resolve_form(alias) is not None:
                continue
            if not re.search(r"(?<![A-Za-z0-9_.$])" + re.escape(alias) + r"(?![A-Za-z0-9_.$])", query):
                continue
        if len(alias) < 2 or not alias_occurs(query, alias):
            continue
        targets.append({"opcode": opcode, "base_opcode": opcode,
                        "target_resolution": "declared_manual_alias", "requested_alias": alias})
    return targets


def _chunk_results(chunk_ids, *, plc_model, task_type, fact_kind, fact_target, augment_instruction=False):
    _index, path, connection, schema, meta = _runtime()
    if connection is None or schema is None or not chunk_ids:
        return []
    ordered_ids = list(dict.fromkeys(str(value) for value in chunk_ids if value is not None))
    rows = index_access.fetch_chunks(connection, schema, [("id", value) for value in ordered_ids])
    results = []
    for chunk_id in ordered_ids:
        row = rows.get(("id", str(chunk_id)))
        result = chunk_result(row, meta, path, plc_model, task_type)
        if result is None:
            continue
        original_text = str(index_access.row_value(row, TEXT_COLUMNS, result.get("text") or ""))
        if augment_instruction:
            result = augment_structured_instruction(connection, schema, result)
            if result is None or result.get("manual_type") not in _OFFICIAL_INSTRUCTION_TYPES:
                continue
        value = dict(result)
        if augment_instruction:
            value["manual_text"] = original_text
        value["structured_fact_kind"] = fact_kind
        value["structured_fact_target"] = fact_target
        value["fact_kind"] = fact_kind
        value["fact_target"] = fact_target
        value["fact_dimensions"] = ["definition"]
        value["structured_lookup"] = True
        value["match_type"] = "structured_direct"
        results.append(value)
    return results


def _instruction_completeness(row):
    score = 0
    for key in ("summary", "operands_json", "completion_flags_json", "restrictions_json"):
        raw = row[key] if key in row.keys() else None
        text = str(raw or "").strip()
        if text and text not in {"[]", "{}", '""'}:
            score += 1
    return score

def resolve_instruction_contract(target, *, plc_model="FX3U"):
    """Return the registry-owned instruction contract for one exact target.

    This is a deterministic catalogue lookup, not retrieval ranking. Verified
    and unverified fields are both retained so consumers can distinguish what
    the registry proves from what remains unknown.
    """
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    if isinstance(target, Mapping):
        opcode = str(target.get("opcode") or target.get("base_opcode") or "").strip().upper()
        operands = target.get("operands")
        instance_source = target.get("instance_source")
    else:
        opcode = str(target or "").strip().upper()
        operands = None
        instance_source = None

    if not opcode:
        return {
            "opcode": "",
            "contract_level": "unknown",
            "verified_fields": [],
            "unverified_fields": [],
        }

    contract = copy.deepcopy(
        DEFAULT_INSTRUCTION_REGISTRY.describe_contract(opcode, cpu=plc_model)
    )
    if isinstance(operands, (list, tuple)):
        contract["confirmed_operands"] = [str(value) for value in operands]
        if instance_source:
            contract["instance_source"] = str(instance_source)
    return contract


def instruction_lane_prompt_lines(lanes):
    """Render only current Agent-B decisions; full lane objects stay metadata."""
    lanes = lanes if isinstance(lanes, Mapping) else {}
    common = lanes.get("operand_semantics") or {}
    slots = lanes.get("operand_slots") or []

    compact_slots = []
    usage_sources, source_indexes = [], {}
    for item in slots:
        if not isinstance(item, Mapping):
            continue
        slot = {
            key: copy.deepcopy(item[key])
            for key in (
                "position", "symbol", "name", "role", "data_type", "value",
                "role_status", "data_type_status", "symbol_status", "device_class_status",
                "purpose_status", "usage_facts",
            )
            if key in item
            and item[key] not in (None, "", [], {})
            and not (key == "data_type" and item[key] == "any")
        }
        if slot:
            if item.get("usage_conflicts"):
                slot["usage_conflicts"] = [{
                    "facet": conflict["facet"], "status": "unresolved",
                    "reason": conflict["reason"], "candidate_count": len(conflict["candidates"]),
                } for conflict in item["usage_conflicts"]]
            if slot.get("usage_facts"):
                for fact in slot["usage_facts"]:
                    if fact.get("sources"):
                        refs = []
                        for source in fact.pop("sources"):
                            source_view = {key: copy.deepcopy(source[key]) for key in (
                                "id", "manual_id", "manual", "revision", "pdf_page", "reference",
                                "context_span", "target_model", "opcode",
                                "offset_basis", "path",
                            ) if source.get(key) not in (None, "", [], {})}
                            marker = json.dumps(source_view, sort_keys=True, ensure_ascii=False)
                            if marker not in source_indexes:
                                source_indexes[marker] = len(usage_sources)
                                usage_sources.append(source_view)
                            ref = {"source": source_indexes[marker]}
                            if source.get("row_span"):
                                ref["row_span"] = copy.deepcopy(source["row_span"])
                            if source.get("value_spans"):
                                ref["value_spans"] = copy.deepcopy(source["value_spans"])
                            refs.append(ref)
                        fact["source_refs"] = refs
            compact_slots.append(slot)

    lines = []
    if compact_slots:
        lines.append(
            "OPERAND_SEMANTICS: "
            + json.dumps(
                {"opcode": common.get("opcode"), "slots": compact_slots,
                 **({"sources": usage_sources} if usage_sources else {})},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )

    target = lanes.get("target_applicability") or {}
    if target.get("st_call"):
        lines.append("TARGET_CALL: " + json.dumps(target.get("call_contract") or {key: target[key] for key in
                     ("target_model", "opcode", "st_call", "source_notes", "contract_sources")
                     if key in target}, ensure_ascii=False, separators=(",", ":")))
    target_view = {}
    if target.get("available") is False:
        for key in ("target_model", "opcode", "available", "replacement_opcode"):
            if target.get(key) not in (None, "", [], {}):
                target_view[key] = copy.deepcopy(target[key])
    else:
        # Shared catalogue applicability is the default and adds no decision.
        if target.get("support_status") not in (None, "", "shared_catalog"):
            target_view["target_model"] = target.get("target_model")
            target_view["support_status"] = target.get("support_status")
        if target.get("replacement_opcode"):
            target_view["replacement_opcode"] = target.get("replacement_opcode")
        for key in (
            "operand_constraints",
            "numeric_operand_boundaries",
            "disjoint_bit_ranges",
        ):
            if target.get(key):
                target_view[key] = copy.deepcopy(target[key])
        if target.get("execution_form"):
            target_view["execution_form"] = target["execution_form"]
            target_view["execution_form_status"] = target.get("execution_form_status", "unresolved")
        if target.get("numeric_operand_boundaries") or target.get("disjoint_bit_ranges"):
            target_view["boundary_status"] = target.get("boundary_status", "unresolved")
    if target_view:
        lines.append(
            "TARGET_APPLICABILITY: "
            + json.dumps(
                target_view,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )

    runtime = lanes.get("runtime_semantics") or {}
    runtime_view = {
        key: copy.deepcopy(runtime[key])
        for key in ("completion", "pulse_output")
        if runtime.get(key)
    }
    if runtime_view:
        lines.append(
            "RUNTIME_SEMANTICS: "
            + json.dumps(
                runtime_view,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    return lines


def _attach_instruction_contract(record, target, *, plc_model):
    # The legacy mixed contract remains metadata for compatibility/validators.
    # Agent-facing text is split into independently owned lanes.
    from plc.instruction_resolution import resolve_instruction_lanes

    contract = resolve_instruction_contract(target, plc_model=plc_model)
    lanes = resolve_instruction_lanes(target, plc_model=plc_model)
    value = dict(record)
    value["instruction_contract"] = copy.deepcopy(contract)
    value.update(copy.deepcopy(lanes))

    body = str(value.get("text") or "")
    details = "\n".join(instruction_lane_prompt_lines(lanes))
    if not details:
        return value
    if body.startswith("[STRUCTURED INSTRUCTION RECORD]"):
        first, separator, rest = body.partition("\n")
        value["text"] = first + "\n" + details + (separator + rest if separator else "")
    else:
        value["text"] = details + ("\n\n" + body if body else "")
    return value


def _step_width_resolution(status):
    """Name one unresolved state without merging coverage gaps into one bucket."""
    return {
        "requires_operands": "requires_operands",
        "requires_context": "requires_context",
        "no_profile": "uncovered",
        "conflict": "conflict",
        "metadata_unavailable": "metadata_unavailable",
        "unknown_cpu": "unknown_cpu",
        "not_applicable": "unresolved",
    }.get(status, "unresolved")


def resolve_instruction_step_width(target, *, plc_model=None, context=None,
                                  text_encoding=None, assume_default_allocation=False):
    """Resolve one instruction expected GX program width from the shared owner.

    Exact opcode+operands use instruction_step_width for the real CPU; an
    opcode-only target uses mnemonic_step_width, which asks the same Core rules
    whether the mnemonic has a width of its own, needs its operands, or is
    uncovered. Both questions keep the whole target identity, so a merged menu
    option is never split into members here and its context still matches. Missing
    operands, missing project context, a missing code page, an uncovered CPU and
    conflicting member answers stay separate states: this layer never guesses
    operands, a CPU or a one-step fallback, and a missing target is reported
    instead of assumed.
    """
    from plc.instruction_steps import instruction_step_width

    model = str(plc_model or "").strip().upper()
    if isinstance(target, Mapping):
        opcode = str(target.get("opcode") or target.get("base_opcode") or "").strip().upper()
        raw_operands = target.get("operands")
        has_operands = isinstance(raw_operands, (list, tuple))
        operands = [str(value).strip() for value in raw_operands] if has_operands else []
    else:
        opcode = str(target or "").strip().upper()
        has_operands = False
        operands = []

    if not opcode:
        return {
            "known": False,
            "steps": None,
            "status": "not_applicable",
            "resolution": "unresolved",
            "source": "unknown",
            "reason": "Instruction opcode is missing",
            "evidence": [],
            "requirements": [],
            "operands": operands,
            "plc_model": model or None,
        }

    if has_operands:
        width = instruction_step_width(opcode, operands, plc_model=model or None,
                                       context=context, text_encoding=text_encoding,
                                       assume_default_allocation=assume_default_allocation)
        return {
            "known": width.known,
            "steps": width.steps,
            "status": width.status,
            "resolution": "instruction_instance" if width.known else _step_width_resolution(width.status),
            "source": width.source,
            "reason": width.reason,
            "evidence": list(width.evidence),
            "requirements": list(width.requirements),
            "operands": operands,
            "plc_model": model or None,
            "members": [{"cpu": cpu, **member.as_dict()} for cpu, member in width.members],
        }

    # Opcode-only asks Core's second question: does this mnemonic have a width of
    # its own, does it need its operands, or is it uncovered? Core keeps the full
    # identity (a merged menu option is not split here), so this layer only
    # converts the answer instead of re-deciding fixed widths or members itself.
    from plc.instruction_steps import mnemonic_step_width

    try:
        width = mnemonic_step_width(opcode, plc_model=model or None, context=context,
                                    assume_default_allocation=assume_default_allocation,
                                    text_encoding=text_encoding)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {
            "known": False,
            "steps": None,
            "status": "metadata_unavailable",
            "resolution": "metadata_unavailable",
            "source": "unknown",
            "reason": "Step-width metadata unavailable: " + str(exc),
            "evidence": [],
            "requirements": [],
            "operands": [],
            "plc_model": model,
            "members": [],
        }
    return {
        "known": width.known,
        "steps": width.steps,
        "status": width.status,
        "resolution": ("fixed_mnemonic" if width.known
                       else _step_width_resolution(width.status)),
        "source": width.source,
        "reason": width.reason,
        "evidence": list(width.evidence),
        "requirements": list(width.requirements),
        "operands": [],
        "plc_model": model or None,
        "members": [{"cpu": cpu, **member.as_dict()} for cpu, member in width.members],
    }


def _attach_instruction_step_width(record, target, *, plc_model):
    """Attach runtime/export width metadata without exposing it to Agent B."""
    fact = resolve_instruction_step_width(target, plc_model=plc_model)
    value = dict(record)
    value["instruction_step_width"] = copy.deepcopy(fact)
    operands = target.get("operands") if isinstance(target, Mapping) else None
    if isinstance(operands, (list, tuple)):
        identity = json.dumps(
            {"opcode": str(target.get("opcode") or "").upper(), "operands": list(operands)},
            ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        )
        original_id = str(value.get("id") or "")
        value["original_id"] = original_id
        value["id"] = original_id + "#instance-" + hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()[:12]
        value["instruction_instance"] = {
            "opcode": str(target.get("opcode") or "").upper(),
            "operands": [str(item) for item in operands],
        }
    return value


def _local_instruction_fact_record(target, *, plc_model, task_type):
    """Return one local structured record when no manual instruction row exists.

    Registry contract and step-width metadata keep separate provenance inside
    their respective sub-objects. The wrapper is only a transport record; it
    does not claim either owner as the source of the other fact family.
    """
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    if isinstance(target, Mapping):
        opcode = str(target.get("opcode") or target.get("base_opcode") or "").strip().upper()
        operands = target.get("operands")
    else:
        opcode = str(target or "").strip().upper()
        operands = None
    if not opcode:
        return None

    form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode, cpu=plc_model)
    step_width = resolve_instruction_step_width(target, plc_model=plc_model)
    contract = resolve_instruction_contract(target, plc_model=plc_model)
    if form is None and not step_width["known"] and contract.get("contract_level") == "unknown":
        return None

    identity = json.dumps(
        {"plc_model": str(plc_model).upper(), "opcode": opcode, "operands": operands},
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    from plc.instruction_resolution import resolve_instruction_lanes
    lanes = resolve_instruction_lanes(target, plc_model=plc_model)
    lines = [
        "[STRUCTURED LOCAL INSTRUCTION RECORD]",
        f"INSTRUCTION: {opcode}",
        *instruction_lane_prompt_lines(lanes),
    ]
    if isinstance(operands, (list, tuple)):
        lines.append("OPERANDS: " + " ".join(str(value) for value in operands))

    value = {
        "id": f"structured-instruction:{digest}",
        "source": "local_structured_instruction_owners",
        "manual_id": "structured_instruction_registry",
        "manual_number": "LOCAL-INSTRUCTION-FACT",
        "revision": "runtime",
        "manual_type": "structured_instruction",
        "manual_priority": 100,
        "chunk_type": "instruction",
        "instruction_opcode": opcode,
        "section": "Structured instruction facts",
        "page": "",
        "pdf_page": 0,
        "text": "\n".join(lines),
        "structured_fact_kind": "instruction",
        "structured_fact_target": opcode,
        "fact_kind": "instruction",
        "fact_target": opcode,
        "fact_dimensions": ["definition"],
        "structured_lookup": True,
        "match_type": "structured_direct",
        "instruction_step_width": copy.deepcopy(step_width),
        "instruction_contract": copy.deepcopy(contract),
        "operand_semantics": copy.deepcopy(lanes["operand_semantics"]),
        "target_applicability": copy.deepcopy(lanes["target_applicability"]),
        "runtime_semantics": copy.deepcopy(lanes["runtime_semantics"]),
        "operand_slots": copy.deepcopy(lanes["operand_slots"]),
        "task_type": task_type,
        "plc_model": plc_model,
    }
    if isinstance(operands, (list, tuple)):
        value["instruction_instance"] = {
            "opcode": opcode,
            "operands": [str(item) for item in operands],
        }
    return value


def _instruction_section_records(opcode, *, plc_model, task_type):
    """Recover official basic-instruction sections absent from the row catalogue.

    Exact leaf headings and the original body must both identify the mnemonic.
    This is metadata lookup, never broad ranking or an alias substring match.
    The section remains verbatim; registry facts retain their own provenance.
    """
    _index, _path, connection, schema, _meta = _runtime()
    chunks = schema.get("chunks") if schema is not None else None
    if not connection or not chunks or not {"id", "section", "text", "manual_type"}.issubset(chunks["columns"]):
        return []
    indexed = index_access.cached_value(connection, "instruction_sections")
    if indexed is None:
        indexed = []
        placeholders = ",".join("?" for _ in _OFFICIAL_INSTRUCTION_TYPES)
        for row in connection.execute(
            f"SELECT * FROM {index_access.quote_identifier(chunks['name'])} WHERE manual_type IN ({placeholders})",
            tuple(sorted(_OFFICIAL_INSTRUCTION_TYPES)),
        ):
            leaf = str(row["section"] or "").split(" > ")[-1]
            indexed.append((row, instruction_heading_terms(leaf)))
        index_access.cache_value(connection, "instruction_sections", indexed)
    records = []
    for row, names in indexed:
        if opcode not in names or not row_in_scope(row, plc_model, task_type):
            continue
        if not alias_occurs(str(row["text"] or ""), opcode):
            continue
        for record in _chunk_results([row["id"]], plc_model=plc_model, task_type=task_type,
                                     fact_kind="instruction", fact_target=opcode):
            record["instruction_opcode"] = opcode
            record["instruction_lookup_basis"] = "official_section_heading"
            records.append(record)
    return records


def _instruction_contract_source_records(target, *, plc_model, task_type):
    """Follow exact definition pages already owned by the selected Core form.

    Shared D/P definitions can be absent from instructions.opcode_norm. Their
    independently verified order already cites a manual/revision/page; reading
    that page adds candidate prose without certifying its operand purposes.
    """
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    _index, _path, connection, schema, _meta = _runtime()
    form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(target.get("opcode"), cpu=plc_model)
    chunks = schema.get("chunks") if schema is not None else None
    if (form is None or form.spec.contract_coverage().get("operand_order") != "source_verified"
            or not connection or not chunks
            or not {"id", "manual_id", "revision", "pdf_page", "pdf_page_end"}.issubset(chunks["columns"])):
        return []
    records, seen = [], set()
    for source in form.spec.contract_sources:
        if not source.get("manual_id") or not source.get("revision") or not source.get("pdf_page"):
            continue
        ids = connection.execute(
            f"SELECT id FROM {index_access.quote_identifier(chunks['name'])} "
            "WHERE manual_id=? AND revision=? AND pdf_page<=? AND pdf_page_end>=? ORDER BY id",
            (source["manual_id"], source["revision"], source["pdf_page"], source["pdf_page"]),
        ).fetchall()
        for record in _chunk_results(
            [row["id"] for row in ids], plc_model=plc_model, task_type=task_type,
            fact_kind="instruction", fact_target=target["opcode"], augment_instruction=True,
        ):
            if record["id"] in seen or (source.get("manual") and record.get("manual_number") != source["manual"]):
                continue
            seen.add(record["id"])
            record["instruction_lookup_basis"] = "verified_contract_source_page"
            record["instruction_contract_source"] = copy.deepcopy(dict(source))
            record["manual_instruction_opcode"] = record.get("instruction_opcode")
            record["instruction_opcode"] = target["opcode"]
            records.append(record)
    return records


def _adjudicated_rows(rows, targets, plc_model):
    """Put the audited material first for its own target and leave the rest alone.

    The runtime does not re-decide which material owns an instruction: each target that
    has an audited precedence is handled on its own, its rows keep the decision that
    was already reached, and rows belonging to another target or another material are
    returned unchanged. Pages only rank the reference list; they never decide which
    facts may enter the answer.
    """
    from knowledge.source_authority import adjudicate_fact_sources, instruction_source_authority
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    if not isinstance(rows, list) or not rows:
        return rows, False
    entries, decided = [], []
    for target in targets or ():
        if isinstance(target, Mapping):
            opcode = str(target.get("opcode") or "").strip().upper()
            base = str(target.get("base_opcode") or opcode).strip().upper()
        else:
            opcode = base = str(target or "").strip().upper()
        if not opcode:
            continue
        if instruction_source_authority(opcode, plc_model) is None and base != opcode:
            opcode = base
        rule = instruction_source_authority(opcode, plc_model)
        if rule is None:
            continue
        decided.append(opcode)
        definition = DEFAULT_INSTRUCTION_REGISTRY.definition_entry(opcode, cpu=plc_model) or {}
        materials = [material for material in definition.get("source_materials") or ()
                     if isinstance(material, Mapping)]
        candidates = [{"manual_id": material.get("manual_id"),
                       "revision": material.get("revision"),
                       "fact_id": material.get("document_id")} for material in materials]
        decision = adjudicate_fact_sources(opcode=opcode, plc_model=plc_model,
                                           fact_type="instruction_semantics",
                                           candidates=candidates)
        selected = decision.get("selected") or {}
        owner = (str(selected.get("manual_id") or ""), str(selected.get("revision") or ""))
        if not owner[0]:
            continue
        pages = set()
        for material in materials:
            if (str(material.get("manual_id") or ""), str(material.get("revision") or "")) == owner:
                pages.update(int(page) for page in material.get("pdf_pages") or ())
        entries.append((opcode, owner, pages, {
            "policy": decision["policy"],
            "schema_version": decision["schema_version"],
            "status": decision["status"],
            "fact_id": None,
            "dimension": decision["fact_type"],
            "opcode": opcode,
            "plc_model": decision["plc_model"],
            "form": opcode,
            "selected_manual_id": owner[0],
            "selected_revision": owner[1],
            "reason": decision.get("reason"),
            "rule": decision.get("rule"),
            "candidates": [
                {"manual_id": item.get("manual_id"), "revision": item.get("revision"),
                 "selected": bool(item.get("selected")),
                 "excluded_because": item.get("excluded_because")}
                for item in decision["candidates"]
            ],
        }))
    if not entries:
        return rows, bool(decided)
    ordered, ranked = [], []
    for index, row in enumerate(rows):
        entry = dict(row)
        for opcode, owner, pages, record in entries:
            row_opcode = str(entry.get("instruction_opcode") or entry.get("opcode") or "").upper()
            if row_opcode and row_opcode != opcode:
                # Another target's row: it keeps its own state untouched.
                continue
            if (str(entry.get("manual_id") or ""), str(entry.get("revision") or "")) != owner:
                continue
            page = entry.get("pdf_page")
            within = not pages or page is None or int(page) in pages
            entry["instruction_lookup_basis"] = "authored_fact_material_match"
            entry["authority_decision"] = copy.deepcopy(record)
            ranked.append((0 if within else 1, index, entry))
            break
        else:
            ordered.append((2, index, entry))
    merged = sorted(ranked + ordered, key=lambda item: (item[0], item[1]))
    return [item[2] for item in merged], True


def resolve_instruction_records(targets, *, plc_model="FX3U", task_type="generate", language=None,
                                aspects=None, query=None, project_mode=None, instance_context=None, version=None):
    """Resolve canonical/variant opcodes directly through ``instructions``.

    No call to ``retrieve_knowledge`` is permitted here.
    """
    from knowledge.fact_corpus import resolve
    from knowledge.source_authority import instruction_source_authority

    _index, _path, connection, schema, _meta = _runtime()
    authored = resolve("instruction", targets, plc_model=plc_model, task_type=task_type, connection=connection)
    if authored is not None:
        admitted, decided = _adjudicated_rows(authored, targets, plc_model)
        from knowledge.fact_selection import project_instruction_records
        selected_aspects = (generation_aspects(query, language=language) if task_type in {'generate', 'edit'} and aspects is None
                            else requested_aspects(query, language=language, aspects=aspects))
        return project_instruction_records(admitted if decided else authored, plc_model=plc_model,
            language=language, aspects=selected_aspects, project_mode=project_mode,
            instance_context=instance_context, version=version, query=query)
    table = schema.get("instructions") if schema is not None else None
    table_available = bool(
        connection is not None
        and table
        and {"opcode_norm", "chunk_id"}.issubset(set(table["columns"]))
    )

    results = []
    seen = set()
    for target in targets or ():
        if isinstance(target, Mapping):
            opcode = str(target.get("opcode") or "").strip().upper()
            base = str(target.get("base_opcode") or opcode).strip().upper()
            step_target = copy.deepcopy(dict(target))
        else:
            opcode = str(target or "").strip().upper()
            base = opcode
            step_target = {"opcode": opcode, "base_opcode": base}
        names = list(dict.fromkeys(value for value in (opcode, base) if value))
        if not names:
            continue
        authority = (
            instruction_source_authority(opcode, plc_model)
            or instruction_source_authority(base, plc_model)
        )
        # Research path, kept as-is and no longer extended: it re-derives a compiled
        # chapter from this index and is reached when no authored corpus answers.
        # Published retrieval does not depend on it, and no new verification is added
        # here. Prefer the compiled literal chapter over a historical index row whose
        # first opcode mention can be a reading-guide example.
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode, cpu=plc_model)
        materials = list(form.spec.source_materials) if form else []
        if not materials:
            entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry(opcode, cpu=plc_model) or {}
            materials = entry.get('source_materials', [])
        # Build references are only meaningful inside the index that produced them,
        # so replaying them here requires the same verified index. Otherwise the
        # compiled chapter cannot be re-materialised and the answer says so instead
        # of looking the numbers up in an index where they mean something else.
        from knowledge.source_identity import (REPLAY_AVAILABLE, runtime_replay_status)
        definition_entry = (DEFAULT_INSTRUCTION_REGISTRY.definition_entry(
            opcode, cpu=plc_model) or entry or {})
        replay_status = runtime_replay_status(definition_entry, db=connection)
        # A legacy artifact carries no build ids, so nothing cross-index is at risk and
        # the section-scoped materials keep working. Once build ids exist, they must be
        # verified and replayable in this runtime before the numbers are used.
        replayable = (not definition_entry.get("build_source_ids")
                      or replay_status == REPLAY_AVAILABLE)
        compiled = []
        for material in (materials if replayable else []):
            hits = _chunk_results(material['source_ids'], plc_model=plc_model, task_type=task_type,
                                  fact_kind='instruction', fact_target=opcode, augment_instruction=True)
            for hit in hits:
                if (hit.get('manual_id') != material['manual_id'] or hit.get('revision') != material['revision']
                        or material['document_id'].split(':', 2)[-1] != str(hit.get('section') or '').split(' > ')[-1]):
                    continue
                candidate = _attach_instruction_contract(_attach_instruction_step_width(hit, step_target, plc_model=plc_model),
                                                         step_target, plc_model=plc_model)
                candidate['compiled_source_chapter'] = True
                candidate['instruction_lookup_basis'] = 'compiled_definition_section'
                candidate['manual_instruction_opcode'] = hit.get('instruction_opcode')
                candidate['instruction_opcode'] = opcode
                if form and form.spec.contract_coverage().get('operand_order') == 'source_verified':
                    source = next((source for source in form.spec.contract_sources
                                   if source.get('manual_id') == hit.get('manual_id')
                                   and source.get('revision') == hit.get('revision')
                                   and source.get('pdf_page') == hit.get('pdf_page')), None)
                    if source:
                        candidate['instruction_contract_source'] = copy.deepcopy(dict(source))
                if authority:
                    candidate['instruction_source_authority'] = copy.deepcopy(authority)
                    candidate['instruction_source_authority_status'] = (
                        'authoritative' if hit.get('manual_id') == authority['manual_id'] else 'non_authoritative')
                compiled.append((not material.get('primary_semantic_source'), int(hit.get('pdf_page') or 0), candidate))
        if compiled:
            if authority:
                authoritative = [row for row in compiled if row[-1].get('manual_id') == authority['manual_id']]
                if authoritative:
                    compiled = authoritative
            for _, _, candidate in sorted(compiled, key=lambda row: (row[0], row[1])):
                marker = (candidate['id'], opcode,
                          tuple(step_target.get('operands') or ()) if isinstance(step_target, Mapping) else ())
                if marker not in seen:
                    seen.add(marker)
                    results.append(candidate)
            continue
        rows = []
        if table_available:
            placeholders = ",".join("?" for _ in names)
            rows = connection.execute(
                "SELECT * FROM {} WHERE opcode_norm IN ({}) AND chunk_id IS NOT NULL".format(
                    index_access.quote_identifier(table["name"]), placeholders
                ),
                tuple(value.casefold() for value in names),
            ).fetchall()
        source_records = _instruction_contract_source_records(
            step_target, plc_model=plc_model, task_type=task_type,
        )
        source_by_id = {record["id"]: record for record in source_records}
        source_scopes = {(record["manual_id"], record["revision"]) for record in source_records}
        if table_available and source_by_id:
            placeholders = ",".join("?" for _ in source_by_id)
            rows.extend(connection.execute(
                f"SELECT * FROM {index_access.quote_identifier(table['name'])} WHERE chunk_id IN ({placeholders})",
                tuple(source_by_id),
            ).fetchall())
        inputs, input_seen = [], set()
        for row in rows:
            chunk_id = str(row["chunk_id"])
            if chunk_id in input_seen:
                continue
            input_seen.add(chunk_id)
            chunk = ([source_by_id[chunk_id]] if chunk_id in source_by_id else _chunk_results(
                [row["chunk_id"]], plc_model=plc_model, task_type=task_type,
                fact_kind="instruction", fact_target=opcode or base, augment_instruction=True,
            ))
            if chunk and ((chunk[0].get("manual_id"), chunk[0].get("revision")) not in source_scopes
                          or chunk_id in source_by_id):
                inputs.append((row, chunk[0]))
        inputs.extend(({}, record) for record in source_records if record["id"] not in input_seen)
        # Prefer the exact selected form and the most complete structured record.
        # Manual priority only resolves otherwise-equivalent official sources;
        # there is no query-dependent score or opcode-specific boost here.
        candidates = []
        for row, chunk in inputs:
            row_opcode = str(row["opcode"] if "opcode" in row.keys() else
                             row["opcode_norm"] if "opcode_norm" in row.keys() else "").upper()
            exact = 2 if chunk.get("instruction_contract_source") else 1 if row_opcode == opcode else 0
            candidate = _attach_instruction_step_width(
                chunk, step_target, plc_model=plc_model,
            )
            candidate = _attach_instruction_contract(
                candidate, step_target, plc_model=plc_model,
            )
            candidate['manual_instruction_opcode'] = candidate.get('instruction_opcode')
            candidate['instruction_opcode'] = opcode
            if "operands_json" in row.keys():
                try:
                    operand_rows = json.loads(row["operands_json"] or "[]")
                except (TypeError, ValueError):
                    operand_rows = []
                if isinstance(operand_rows, list):
                    # Hints only: binding still requires the description in an
                    # original source row and never uses physical row order.
                    candidate["manual_operand_rows"] = operand_rows
            if authority:
                candidate["instruction_source_authority"] = copy.deepcopy(authority)
                candidate["instruction_source_authority_status"] = (
                    "authoritative"
                    if candidate.get("manual_id") == authority["manual_id"]
                    else "non_authoritative"
                )
            candidates.append(
                (
                    0 if not authority or candidate.get("manual_id") == authority["manual_id"] else 1,
                    -exact,
                    -_instruction_completeness(row) if row else 0,
                    -int(candidate.get("manual_priority") or 0),
                    int(candidate.get("pdf_page") or 0),
                    str(candidate.get("id") or ""),
                    candidate,
                )
            )
        if not candidates:
            for section in _instruction_section_records(opcode, plc_model=plc_model, task_type=task_type):
                original_text = section["text"]
                candidate = _attach_instruction_contract(
                    _attach_instruction_step_width(section, step_target, plc_model=plc_model),
                    step_target, plc_model=plc_model,
                )
                candidate["manual_text"] = original_text
                if authority:
                    candidate["instruction_source_authority"] = copy.deepcopy(authority)
                    candidate["instruction_source_authority_status"] = (
                        "authoritative" if candidate.get("manual_id") == authority["manual_id"] else "non_authoritative"
                    )
                candidates.append((
                    0 if not authority or candidate.get("manual_id") == authority["manual_id"] else 1,
                    -1, 0, -int(candidate.get("manual_priority") or 0),
                    int(candidate.get("pdf_page") or 0), str(candidate.get("id") or ""), candidate,
                ))
        if not candidates:
            fallback = _local_instruction_fact_record(
                step_target, plc_model=plc_model, task_type=task_type,
            )
            if fallback is not None:
                if authority:
                    fallback["instruction_source_authority"] = copy.deepcopy(authority)
                    fallback["instruction_source_authority_status"] = "unavailable"
                candidates.append(
                    (
                        1 if authority else 0,
                        0,
                        0,
                        -int(fallback.get("manual_priority") or 0),
                        0,
                        str(fallback.get("id") or ""),
                        fallback,
                    )
                )

        if authority:
            authoritative = [
                item for item in candidates
                if item[-1].get("manual_id") == authority["manual_id"]
            ]
            if authoritative:
                candidates = authoritative

        for *_keys, candidate in sorted(candidates, key=lambda item: item[:-1]):
            marker = (
                candidate.get("id"),
                opcode or base,
                tuple(step_target.get("operands") or ()) if isinstance(step_target, Mapping) else (),
            )
            if marker in seen:
                continue
            seen.add(marker)
            results.append(candidate)
    return results


def _device_definition_ids(connection, schema, device, *, plc_model, task_type):
    """A named device/range in an official leaf heading owns its definition.

    The occurrence index can point at an instruction example. Do not promote
    such a mention over a declaration; use the original section unchanged.
    """
    from plc.device_identity import canonical_reference_device as canonical_device, reference_device_parts
    _index, _path, _connection, _schema, _meta = _runtime()
    chunks = schema.get("chunks")
    if not chunks or not {"id", "section", "manual_type", "text"}.issubset(chunks["columns"]):
        return []
    parsed = reference_device_parts(device)
    if not parsed:
        return []
    prefix, number = parsed
    ids = []
    for row in connection.execute(
        f"SELECT * FROM {index_access.quote_identifier(chunks['name'])} "
        "WHERE manual_type IN ('programming','positioning','structured_device','cpu_function','cpu_hardware')"
    ):
        if not row_in_scope(row, plc_model, task_type):
            continue
        if 'device_table_binding_status' in row.keys() and row['device_table_binding_status']=='cpu_columns_require_projection':
            continue
        leaf = str(row["section"] or "").split(" > ")[-1]
        # CPU manuals use names such as 'Special relay (SM)' rather than an
        # explicit bracketed address. The chapter defines that device class;
        # it does not prove that the caller's individual address is legal.
        # A class heading alone never owns a particular address. Its source
        # occurrences remain available below, explicitly as unverified mentions.
        for label in re.findall(r"\[([^\]]+)\]", leaf):
            match = re.fullmatch(r"\s*([A-Z]+)([0-9A-F]+)\s*(?:to|[-–～]|至)\s*([A-Z]+)([0-9A-F]+)\s*", label, re.I)
            matches = canonical_device(label.strip().upper()) == device
            if match:
                low_prefix, low, high_prefix, high = match.groups()
                radix = 16 if prefix in {'X', 'Y', 'B', 'W', 'SB', 'SW', 'DX', 'DY'} else 10
                matches = low_prefix.upper() == high_prefix.upper() == prefix and int(low, radix) <= int(number, radix) <= int(high, radix)
            body = re.split(r"\n\[(?:PAGE|TABLE)\b", str(row["text"] or ""), maxsplit=1)[-1]
            # Continuation chunks can repeat a heading while containing only an
            # adjacent topic. The cited body must still contain this definition.
            body_mentions = alias_occurs(body, device) or label.casefold() in body.casefold()
            if matches and body_mentions:
                ids.append(row["id"])
                break
    return ids


def _device_range_records(devices, *, plc_model, task_type, connection):
    """Bind an address to a typed range without promoting it to a definition."""
    from knowledge.fact_corpus import resolve
    from plc.device_identity import reference_device_parts, canonical_reference_device
    requests = []
    for device in devices:
        parts = reference_device_parts(device)
        if parts and parts[0] == 'T' and parts[1].isdigit():
            requests.append((canonical_reference_device(device), parts[0], int(parts[1])))
    families = list(dict.fromkeys(family for _, family, _ in requests))
    rows = resolve('device_family', families, plc_model=plc_model, task_type=task_type, connection=connection) or []
    results = []
    for address, family, number in requests:
        for row in rows:
            packet = authored_packet(row)
            value = packet.get('value') or {}
            if (value.get('family') != family or value.get('range_basis') != 'model_reference_device_number_ranges'
                    or packet.get('technical_status') != 'source_verified'):
                continue
            matches = [item for item in value.get('ranges', ())
                       if type(item.get('first')) is int and type(item.get('last')) is int
                       and item['first'] <= number <= item['last']]
            if len(matches) != 1:
                continue
            selected = copy.deepcopy(row)
            packet = copy.deepcopy(packet)
            packet['value'] = {**packet['value'], 'address': address, 'ranges': copy.deepcopy(matches)}
            selected.update(id=f"{row['id']}:address:{address}", text=json.dumps(packet, ensure_ascii=False),
                structured_fact_requested_target=address,
                device_lookup_basis='reviewed_model_number_range',
                device_range={**copy.deepcopy(matches[0]), 'family': family, 'address': address,
                              'basis': value['range_basis']},
                fact_dimensions=['range', 'time_base', 'timer_class'])
            results.append(selected)
    return results


def _device_address_records(devices, *, plc_model, task_type, connection):
    """Deliver exact address-numbering facts, including a rejected boundary."""
    from knowledge.fact_corpus import resolve
    from plc.device_identity import canonical_reference_device
    from plc.validation import DEVICE_ADDRESS_RE, device_address_status
    from plc.target_support import normalize_target_cpu
    cpu = normalize_target_cpu(plc_model)
    requests = []
    for device in devices:
        text = str(device).strip().upper()
        match = DEVICE_ADDRESS_RE.fullmatch(text)
        if match:
            requests.append((canonical_reference_device(text), match.groups()))
        elif text in {'V', 'Z'} and device_address_status(text, cpu) == 'valid':
            requests.append((text, (text, '0', None)))
    # Resolve each explicit family independently: the broad candidate cap must
    # not discard a later address in a request containing many device classes.
    rows = [row for family in dict.fromkeys(parts[0] for _, parts in requests)
            for row in (resolve('device_family', [family], plc_model=cpu,
                        task_type=task_type, connection=connection) or [])]
    results = []
    for address, (prefix, digits, bit) in requests:
        for row in rows:
            packet = authored_packet(row)
            value = packet.get('value') or {}
            if value.get('family') == prefix and value.get('step_sequence_behavior') and not bit:
                selected = [item for item in value.get('ranges', ())
                            if item.get('first', -1) <= int(digits) <= item.get('last', -1)]
                family = {**copy.deepcopy(value), 'address': address, 'selected_ranges': selected}
                packet = copy.deepcopy(packet)
                packet['value'] = family
                results.append({**copy.deepcopy(row), 'id': f"{row['id']}:address:{address}",
                    'fact_kind': 'device', 'fact_target': address,
                    'text': json.dumps(packet, ensure_ascii=False), 'structured_fact_requested_target': address,
                    'device_family_behavior': family, 'device_lookup_basis': 'reviewed_family_behavior',
                    'fact_dimensions': ['device_class', 'state_behavior']})
            if (value.get('range_basis') != 'model_address_numbering' or value.get('cpu') != cpu
                    or value.get('family') != prefix):
                continue
            packet = copy.deepcopy(packet)
            packet['value'] = {**value, 'address': address,
                               'numbering_status': device_address_status(address, cpu)}
            results.append({**copy.deepcopy(row), 'id': f"{row['id']}:address:{address}",
                'text': json.dumps(packet, ensure_ascii=False), 'structured_fact_requested_target': address,
                'device_lookup_basis': 'reviewed_address_numbering',
                'device_address_rule': packet['value'], 'fact_dimensions': ['numbering', 'range', 'allocation']})
    return results


def resolve_device_records(devices, *, plc_model="FX3U", task_type="analysis", query=None, hardware_context=None):
    """Resolve device definitions and explicit property questions, before RRF.

    An original official range table owns time-base questions. This retains the
    established metadata selector without allowing incidental T-device mentions
    in program examples to displace that evidence in the broad rank fusion.
    """
    from knowledge.fact_corpus import resolve
    from plc.device_identity import canonical_reference_device as canonical_device
    from plc.device_definitions import device_definition_targets, resolve_device_definition
    _index, _path, connection, schema, _meta = _runtime()
    authored_targets = list(dict.fromkeys([*(devices or ()), *device_definition_targets(query, plc_model)]))
    authored = resolve("device", authored_targets, plc_model=plc_model, task_type=task_type, connection=connection)
    if authored is not None:
        for row in authored:
            resolved = resolve_device_definition(row['fact_target'], plc_model, context=hardware_context)
            clean = copy.deepcopy(resolved)
            for definition in clean.get('definitions', []):
                definition['description'] = json.dumps(definition.get('semantic_facts', {}), ensure_ascii=False)
                definition['notes'] = []
                for condition in definition.get('conditions', []):
                    condition['note'] = str(condition['field']) + ':' + str(condition['operator']) + ':' + str(condition['value'])
            row.update(device_definition=clean, device_definition_status=resolved['status'],
                       fact_dimensions=['definition', 'applicability', 'access', 'conditions'])
            lines = ['[SOURCE-REVIEWED DEVICE DEFINITION]',
                f"CPU: {resolved['cpu']}; address: {row['fact_target']}; applicability: {resolved['status']}.",
                row['text'], json.dumps(clean, ensure_ascii=False)]
            for definition in clean.get('definitions', []):
                for source in definition.get('sources', []):
                    lines.append(f"Source: {source['manual_number']}, revision {source['revision']}, PDF pages {source['pdf_pages']}.")
                connection_number = definition.get('semantic_facts', {}).get('bit_zero_connection')
                if connection_number is not None:
                    lines.append(f'bit 0 -> connection {connection_number}')
            row['text'] = '\n'.join(lines)
        return [*_device_range_records(authored_targets, plc_model=plc_model,
                    task_type=task_type, connection=connection),
                *_device_address_records(authored_targets, plc_model=plc_model,
                    task_type=task_type, connection=connection), *authored]
    if connection is None or schema is None:
        return []
    table = schema.get("device_records")
    table_available = bool(table and {"device_norm", "device", "chunk_id"}.issubset(table["columns"]))
    requested_devices = list(dict.fromkeys(str(item or "").strip().upper()
                             for item in devices or () if str(item or "").strip()))
    requested_devices = list(dict.fromkeys([
        *requested_devices, *device_definition_targets(query, plc_model),
    ]))
    results, seen = [], set()
    chunks = schema.get("chunks")
    if not schema.get('device_sections') and query and query_is_timer_preset(str(query)) and chunks and {"id", "manual_type", "section", "text"}.issubset(chunks["columns"]):
        targets = [canonical_device(item) for item in requested_devices if re.fullmatch(r"T[0-9]+", item)] or ["T"]
        for row in connection.execute(
            f"SELECT * FROM {index_access.quote_identifier(chunks['name'])} "
            "WHERE manual_type IN ('programming','structured_device') AND section LIKE '%Numbers of timers%'"
        ):
            if not row_in_scope(row, plc_model, task_type) or not timer_range_evidence(row["text"], plc_model):
                continue
            for target in targets:
                if target != "T":
                    ranges = re.findall(r"\bT(\d+)\s*(?:to|[-–～]|至)\s*T(\d+)\b", str(row["text"] or ""), re.I)
                    if not any(int(low) <= int(target[1:]) <= int(high) for low, high in ranges):
                        continue
                for record in _chunk_results([row["id"]], plc_model=plc_model, task_type=task_type,
                                             fact_kind="device", fact_target=target):
                    record["fact_dimensions"] = ["range", "time_base"]
                    record["device_lookup_basis"] = "official_property_section"
                    record["structured_fact_requested_target"] = target
                    seen.add((record["id"], target))
                    results.append(record)
    if schema.get('device_sections') and query and query_is_timer_preset(str(query)) and not any(re.fullmatch(r'T[0-9]+',item) for item in requested_devices):
        from knowledge.device_evidence import resolve_section_records
        properties=resolve_section_records(connection,schema,['T'],plc_model=plc_model)
        properties.sort(key=lambda row:row.get('table_binding_status')!='cpu_row_with_notes')
        for record in properties:
            record['device_lookup_basis']='official_property_section'
            record['fact_dimensions']=['definition','range','time_base']
            results.append(record)
    for requested_device in requested_devices:
        device = canonical_device(requested_device)
        if not isinstance(device, str) or not device:
            continue
        resolved = resolve_device_definition(device, plc_model, context=hardware_context)
        if resolved['definitions']:
            # The reviewed definition is a summary bound to original pages, not
            # an arbitrary mention promoted to a definition by rank or heading.
            rows = resolved['definitions']
            sources = [source for row in rows for source in row['sources']]
            primary = sources[0]
            text = ['[SOURCE-REVIEWED DEVICE DEFINITION]',
                    f"CPU: {resolved['cpu']}; address: {device}; applicability: {resolved['status']}."]
            for row in rows:
                text.extend([row['description'], f"Control: {row['access']}; CPU scope: {', '.join(row['models'])}."])
                text.extend('Condition: ' + q['note'] for q in row['conditions'])
                text.extend('Note: ' + note for note in row['notes'])
                text.extend(f"Source: {s['manual_number']}, revision {s['revision']}, PDF pages {s['pdf_pages']}."
                            for s in row['sources'])
            results.append({
                'id': f"device-definition:{resolved['cpu']}:{device}",
                'source': primary['official_url'], 'pdf_page': primary['pdf_pages'][0],
                'page': primary['pdf_pages'][0], 'section': 'Source-reviewed device definition',
                'text': '\n'.join(text), 'manual_type': 'structured_device',
                'plc_models': [resolved['cpu']], 'source_kind': 'official_manual',
                'structured_lookup': True, 'match_type': 'structured_direct',
                'structured_fact_kind': 'device', 'fact_kind': 'device',
                'structured_fact_target': device, 'fact_target': device,
                'structured_fact_requested_target': requested_device,
                'fact_dimensions': ['definition', 'applicability', 'access', 'conditions'],
                'device_lookup_basis': 'reviewed_source_definition',
                'device_definition_status': resolved['status'], 'device_definition': resolved,
                'source_evidence': sources,
                'manual_id': primary['manual_id'], 'manual_number': primary['manual_number'],
                'revision': primary['revision'], 'content_status': 'complete',
            })
            # A confirmed or conditional definition owns this address's meaning;
            # broad recall remains available for unresolved conditions below.
            continue
        definition_ids = _device_definition_ids(connection, schema, device, plc_model=plc_model, task_type=task_type)
        family_question = not query or query_is_timer_preset(str(query)) or bool(re.search(
            r'软元件|定义|作用|用途|保持|锁存|时间基准|时基|分辨率|地址范围|分区|'
            r'\b(?:definition|purpose|retentive|latch|EEPROM|time\s*base|resolution)\b',str(query),re.I))
        if not definition_ids and family_question:
            from knowledge.device_evidence import resolve_section_records
            sections = resolve_section_records(connection, schema, [device], plc_model=plc_model)
            if sections:
                # A family chapter locates original evidence for this address;
                # it never certifies an individual definition or permission.
                for record in sections:
                    marker=(record['id'],device)
                    if marker in seen:continue
                    seen.add(marker)
                    record['structured_fact_requested_target']=requested_device
                    results.append(record)
                continue
        ids = definition_ids
        if not ids and table_available:
            rows = connection.execute(
                f"SELECT * FROM {index_access.quote_identifier(table['name'])} WHERE device_norm=? AND chunk_id IS NOT NULL ORDER BY occurrences DESC,id",
                (device.casefold(),),
            ).fetchall()
            ids = [row["chunk_id"] for row in rows if "plc_models" not in row.keys() or scope_matches(row["plc_models"], plc_model)]
        candidates = _chunk_results(ids, plc_model=plc_model, task_type=task_type, fact_kind="device", fact_target=device)
        candidates = project_device_source_records(candidates,plc_model=plc_model)
        candidates.sort(key=lambda item: (-int(item.get("manual_priority") or 0), int(item.get("pdf_page") or 0), str(item.get("id") or "")))
        # Preserve requested-target order; source priority compares sources for
        # one target, never starves a later target in a multi-device query.
        for record in candidates:
            marker = (record["id"], device)
            if marker in seen:
                continue
            seen.add(marker)
            record["structured_fact_requested_target"] = requested_device
            record["device_lookup_basis"] = "official_definition_heading" if definition_ids else "device_record"
            if not definition_ids:
                # Legacy and current occurrence indexes can locate useful source
                # material, but neither establishes the address's definition.
                record.update(structured_fact_kind="device_occurrence", fact_kind="device_occurrence",
                              fact_dimensions=["occurrence"], device_definition_status="unverified")
            results.append(record)
    # An explicit definition establishes meaning; incidental occurrences only
    # locate follow-up reading. A common M0 mention must not consume every slot
    # before a source-reviewed hardware definition requested by the same query.
    # Interleave sources for distinct targets within each evidence class so
    # repeated mentions of the first device cannot exhaust the shared budget.
    ranks = {}
    ranked = []
    for row in results:
        occurrence = row.get('fact_kind') == 'device_occurrence'
        key = (occurrence, row.get('structured_fact_target'))
        rank = ranks.get(key, 0)
        ranks[key] = rank + 1
        reviewed = row.get('device_lookup_basis') == 'reviewed_source_definition'
        ranked.append((not reviewed, occurrence, rank, row))
    return [row for _, _, _, row in sorted(ranked, key=lambda item: item[:3])]


def resolve_device_family_records(families, *, plc_model="FX3U", task_type="analysis"):
    from knowledge.fact_corpus import resolve
    from knowledge.device_evidence import resolve_section_records
    _index, _, connection, schema, _ = _runtime()
    authored = resolve("device_family", families, plc_model=plc_model, task_type=task_type, connection=connection)
    if authored is not None:
        from plc.target_support import normalize_target_cpu
        cpu = normalize_target_cpu(plc_model)
        # A merged menu and one of its members can share source pages. Deliver
        # only this identity's numbering profile; keep other kinds of facts.
        rows = [row for row in authored if (authored_packet(row).get('value') or {}).get('range_basis')
                != 'model_address_numbering' or (authored_packet(row).get('value') or {}).get('cpu') == cpu]
        return sorted(rows, key=lambda row: (authored_packet(row).get('value') or {}).get('range_basis')
                      != 'model_address_numbering')
    if connection is None:
        return []
    return resolve_section_records(connection, schema, families, plc_model=plc_model)


def project_device_source_records(records, *, plc_model):
    """Project source-owned CPU columns without changing broad candidate rank."""
    from knowledge.fact_corpus import is_authored_record
    if all(is_authored_record(row) for row in records):
        return records
    from knowledge.device_evidence import project_source_record
    _,_,connection,schema,_=_runtime()
    if connection is None:return [r for r in records if r.get('table_binding_status')!='cpu_columns_require_projection']
    return [projected for row in records
            if (projected:=project_source_record(connection,schema,row,plc_model=plc_model)) is not None]


def _process_definition_records(target, *, plc_model, task_type):
    """Find original device definitions by purpose, with exact CPU scoping.

    Addresses come from the eligible manual page, never from a family default
    or an experiment oracle. Return whole source blocks, including caveats.
    """
    _index, _path, connection, schema, _meta = _runtime()
    chunks = schema.get('chunks') if schema else None
    if not connection or not chunks or not {'id', 'section', 'text', 'manual_type'}.issubset(chunks['columns']):
        return []
    purpose = {
        'FIRST_SCAN': r'initial pulse|single operation pulse|after RUN,?\s*ON for 1 scan|first scan',
        'CLOCK': r'internal clock|clock devices|\d+(?:\.\d+)?\s*second clock',
    }.get(target)
    if purpose is None:
        return []
    rows = connection.execute(
        f"SELECT * FROM {index_access.quote_identifier(chunks['name'])} "
        "WHERE manual_type IN ('programming','structured_device','cpu_function','cpu_hardware') "
        "AND (section LIKE '%relay%' OR section LIKE '%clock%' OR section LIKE '%initial pulse%')"
    )
    ids = []
    for row in rows:
        if not row_in_scope(row, plc_model, task_type):
            continue
        section = str(row['section'] or '')
        if not re.search(r'special.{0,24}relay|initial pulse|single operation pulse|internal clock|clock devices', section, re.I):
            continue
        if re.search(purpose, str(row['text'] or ''), re.I):
            ids.append(row['id'])
    records = _chunk_results(ids, plc_model=plc_model, task_type=task_type,
                             fact_kind='process', fact_target=target)
    records.sort(key=lambda r: (not bool(re.search(purpose, str(r.get('section') or ''), re.I)),
                               -int(r.get('manual_priority') or 0), int(r.get('pdf_page') or 0), str(r.get('id'))))
    return records[:1]


def resolve_process_records(needs, *, plc_model="FX3U", task_type="generate"):
    """Use original official device chapters for explicit process fact needs.

    No occurrence-index fallback: a timer or counter mentioned in an unrelated
    program is not its operating definition. Preserve original source blocks.
    """
    from knowledge.fact_corpus import resolve
    __index, _, authored_connection, _, authored_meta = _runtime()
    if authored_meta.get('corpus_kind') == 'authored_plc_facts_v1':
        results = []
        for need in needs or ():
            target = str(need.get('target', '')).upper()
            kind, key = ('device_family', 'T') if target == 'TIMER' else ('device_family', 'C') if target == 'COUNTER' else ('process', target)
            rows = resolve(kind, [key], plc_model=plc_model, task_type=task_type, connection=authored_connection) or []
            # Family catalogue presence is insufficient for process behavior.
            rows = [row for row in rows if row.get('runtime_dimension')]
            for row in rows:
                row.update(fact_kind='process', structured_fact_kind='process', fact_target=target,
                    structured_fact_target=target, process_fact_need=copy.deepcopy(need),
                    fact_dimensions=[need.get('dimension', 'definition')])
            results.extend(rows[:1])
        return results
    from plc.runtime_semantics import control_runtime_facts, timer_runtime_fact
    facts = control_runtime_facts(plc_model)
    runtime_records = []
    for need in needs or ():
        target = need.get('target') if isinstance(need, Mapping) else None
        if not isinstance(need, Mapping) or plc_model != 'FX5U' and need.get('basis') != 'confirmed_behavior_constraints':
            continue
        fact = facts.get('first_scan') if target == 'FIRST_SCAN' else facts.get('timers') if target == 'TIMER' else None
        if not fact:
            continue
        source = fact.get('source', {})
        content = ({'devices': fact['devices'], 'execution_type': fact['execution_type'], 'conditions': fact['conditions']}
                   if target == 'FIRST_SCAN' else {'forms': {k: v for k,v in fact.items() if k != 'source'},
                       'declared_devices': [timer_runtime_fact(plc_model, d) for d in need.get('devices', [])],
                       'condition': 'ordinary timer; disabled resets; no timebase inference from scan count'})
        runtime_records.append({'id': 'control_runtime:'+plc_model+':'+target, 'text': json.dumps(content, ensure_ascii=False),
            'source': source.get('manual_number'), 'manual_number': source.get('manual_number'), 'revision': source.get('revision'),
            'pdf_page': source.get('pdf_page') or (source.get('pdf_pages') or [None])[0], 'plc_models': [plc_model],
            'manual_type': 'programming', 'chunk_type': 'source_checked_runtime_fact', 'section': target,
            'fact_kind': 'process', 'structured_fact_kind': 'process', 'fact_target': target,
            'structured_fact_target': target, 'source_record': copy.deepcopy(source), 'process_fact_need': copy.deepcopy(need),
            'process_lookup_basis': 'shared_source_checked_runtime', 'fact_dimensions': [need.get('dimension', 'definition')]})
    _index, _path, connection, schema, _meta = _runtime()
    if connection is None or schema is None:
        return runtime_records
    chunks = schema.get("chunks")
    if not chunks or not {"id", "manual_type", "text"}.issubset(chunks["columns"]):
        return runtime_records
    results = list(runtime_records)
    for need in needs or ():
        if not isinstance(need, Mapping):
            continue
        target = str(need.get("target") or "").upper()
        if any(r['fact_target'] == target for r in runtime_records):
            continue
        if target == "FIRST_SCAN":
            candidates = resolve_device_records(need.get("devices"), plc_model=plc_model, task_type=task_type)
            candidates = [row for row in candidates
                          if row.get("device_lookup_basis") == "official_definition_heading"][:1]
            if not candidates:
                candidates = _process_definition_records(target, plc_model=plc_model, task_type=task_type)
        elif target == "CLOCK":
            candidates = _process_definition_records(target, plc_model=plc_model, task_type=task_type)
        elif target in {"TIMER", "COUNTER"}:
            if target == "TIMER" and plc_model == "FX3U" and any(
                re.fullmatch(r"T\d+", str(device)) and 246 <= int(str(device)[1:]) <= 255
                for device in need.get("devices") or []
            ):
                # The selected general-type page does not establish retentive
                # enable/reset behavior. Leave this family unresolved until its
                # own complete operation evidence is selected.
                continue
            if target == "COUNTER" and (not need.get("devices") or any(
                not re.fullmatch(r"C\d+", str(device)) or int(str(device)[1:]) > 199
                for device in need["devices"]
            )):
                # This source selector establishes ordinary 16-bit operation;
                # high-speed/32-bit families need their own operating chapter.
                continue
            family = "Timer [T]" if target == "TIMER" else "Counter [C]"
            candidates = []
            ordering = ",".join(key for key in ("manual_priority DESC", "pdf_page", "id")
                                if key.split()[0] in chunks["columns"])
            rows = connection.execute(
                f"SELECT * FROM {index_access.quote_identifier(chunks['name'])} "
                "WHERE manual_type IN ('programming','structured_device') "
                f"AND text LIKE ? ORDER BY {ordering}",
                (f"%{family}%",),
            )
            for row in rows:
                if not row_in_scope(row, plc_model, task_type):
                    continue
                text = str(row["text"] or "")
                # The source section must include the actual operation, not just
                # a cross-reference or a device-number list.
                if "Functions and operation examples" not in text:
                    continue
                if target == "TIMER" and not (timer_range_evidence(text, plc_model)
                                                and "General type" in text and "reset" in text):
                    continue
                if target == "COUNTER" and not ("16-bit up counter" in text and "RST" in text):
                    continue
                candidates = _chunk_results([row["id"]], plc_model=plc_model, task_type=task_type,
                                             fact_kind="process", fact_target=target)
                if candidates:
                    break
        else:
            continue
        for raw in candidates:
            record = copy.deepcopy(raw)
            record.update(fact_kind="process", structured_fact_kind="process", fact_target=target,
                          structured_fact_target=target, fact_dimensions=[str(need.get("dimension") or "definition")],
                          process_lookup_basis="official_device_operation", process_fact_need=copy.deepcopy(dict(need)))
            record.pop("structured_fact_requested_target", None)
            results.append(record)
    return results


def resolve_error_records(codes, *, plc_model="FX3U", task_type="debug"):
    """Resolve explicit error codes through ``error_records`` only."""
    from knowledge.fact_corpus import resolve
    _index, _path, connection, schema, _meta = _runtime()
    authored = resolve("error", codes, plc_model=plc_model, task_type=task_type, connection=connection)
    if authored is not None:
        return authored
    if connection is None or schema is None:
        return []
    table = schema.get("error_records")
    if not table or not {"error_code_norm", "error_code", "chunk_id"}.issubset(set(table["columns"])):
        return []

    results = []
    seen = set()
    for requested in codes or ():
        code = canonical_error_code(requested)
        if not code:
            continue
        variants = error_code_aliases(code)
        placeholders = ",".join("?" for _ in variants)
        rows = connection.execute(
            "SELECT * FROM {} WHERE error_code_norm IN ({}) AND chunk_id IS NOT NULL "
            "ORDER BY pdf_page,table_index,row_index".format(
                index_access.quote_identifier(table["name"]), placeholders
            ),
            tuple(value.casefold() for value in variants),
        ).fetchall()
        for row in rows:
            chunks = _chunk_results(
                [row["chunk_id"]],
                plc_model=plc_model,
                task_type=task_type,
                fact_kind="error",
                fact_target=code,
            )
            for candidate in chunks:
                marker = candidate.get("id")
                if marker in seen:
                    continue
                seen.add(marker)
                results.append(candidate)
    return results


def compact_structured_fact_record(record):
    """Return a compact row view without discarding structured metadata.

    Manual-backed instruction rows may carry an entire page body after the
    structured header. Row-oriented exact-fact APIs need the deterministic
    header only; generation's instruction-fact packer still receives the full
    record and can select the original manual prose/tables separately.
    """
    if not isinstance(record, Mapping):
        return record
    value = copy.deepcopy(dict(record))
    if value.get("structured_fact_kind") != "instruction":
        return value
    if (value.get("instruction_lookup_basis") == "official_section_heading" or
            value.get('compiled_source_chapter') and
            not str(value.get('text') or '').startswith('[STRUCTURED INSTRUCTION RECORD]')):
        value["text"] = value.pop("manual_text", value.get("text", ""))
        return value
    text = str(value.get("text") or "")
    if text.startswith("[STRUCTURED INSTRUCTION RECORD]"):
        head, separator, _rest = text.partition("\n\n")
        if separator:
            value["text"] = head.rstrip()
            value["structured_text_compacted"] = True
    return value


def resolve_structured_records(targets, *, plc_model="FX3U", task_type="analysis"):
    """Resolve all supplied exact targets without invoking the broad retriever."""
    targets = targets if isinstance(targets, Mapping) else {}
    return [
        *resolve_positioning_records(targets.get("positioning"), plc_model=plc_model, task_type=task_type),
        *resolve_instruction_records(
            targets.get("instructions") or (),
            plc_model=plc_model,
            task_type=task_type,
        ),
        *resolve_device_records(
            targets.get("devices") or (),
            plc_model=plc_model,
            task_type=task_type,
        ),
        *resolve_error_records(
            targets.get("errors") or (),
            plc_model=plc_model,
            task_type=task_type,
        ),
    ]


def without_structured_targets(query, targets):
    """Mask explicit identities before optional residual prose retrieval."""
    text = str(query or "")
    targets = targets if isinstance(targets, Mapping) else {}
    values = []
    for target in targets.get("instructions") or ():
        if isinstance(target, Mapping):
            values.extend((target.get("opcode"), target.get("base_opcode")))
        else:
            values.append(target)
    values.extend(targets.get("devices") or ())
    values.extend(targets.get("errors") or ())
    from knowledge.device_evidence import FAMILY_ALIASES
    for family in targets.get('device_families') or ():
        values.append(family)
        values.extend(FAMILY_ALIASES.get(family, ()))
    for raw in sorted({str(value).strip() for value in values if str(value or "").strip()},
                      key=len, reverse=True):
        text = re.sub(
            r"(?<![A-Za-z0-9_.$@+<>!=\-])" + re.escape(raw) +
            r"(?![A-Za-z0-9_.$@+<>!=\-])",
            " ",
            text,
            flags=re.IGNORECASE,
        )
    return " ".join(text.split())





def exclude_structured_target_hits(records, targets):
    """Keep broad retrieval from re-resolving identities owned by this module."""
    targets = targets if isinstance(targets, Mapping) else {}
    opcodes = {
        str(value).upper()
        for target in targets.get("instructions") or ()
        for value in (
            (target.get("opcode"), target.get("base_opcode"))
            if isinstance(target, Mapping) else (target,)
        )
        if value
    }
    devices = {str(value).upper() for value in targets.get("devices") or ()}
    errors = {str(value).upper().rstrip("H") for value in targets.get("errors") or ()}
    kept = []
    for record in records or ():
        opcode = str(record.get("instruction_opcode") or "").upper()
        matched = str(record.get("matched_entity") or "").upper()
        if opcode and opcode in opcodes:
            continue
        if matched and matched in devices:
            continue
        normalized_error = matched.rstrip("H")
        if normalized_error and normalized_error in errors and record.get("chunk_type") == "error":
            continue
        kept.append(record)
    return kept


__all__ = [
    "exclude_structured_target_hits",
    "resolve_device_records",
    "resolve_device_family_records",
    "resolve_error_records",
    "resolve_instruction_contract",
    "resolve_instruction_records",
    "resolve_instruction_step_width",
    "resolve_positioning_records",
    "compact_structured_fact_record",
    "resolve_structured_records",
    "structured_fact_targets",
    "without_structured_targets",
]
