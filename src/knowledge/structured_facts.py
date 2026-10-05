"""Exact PLC fact lookup from structured index tables.

This module is deliberately separate from the broad hybrid retriever. Once a
PLC entity is explicit (instruction, device, error code), resolution is a
deterministic SQLite lookup. BM25/dense ranking may still answer residual prose,
but it never decides which record represents the explicit fact.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping

_VERSION = "structured-facts-v3-process-needs"
_OFFICIAL_INSTRUCTION_TYPES = frozenset(
    {"programming", "positioning", "structured_instruction", "structured_function"}
)


def structured_fact_targets(query, confirmed_spec=None, *, plc_model=None):
    """Return exact fact identities without retrieving any manual prose."""
    from knowledge import core
    from knowledge.analysis_router import route_analysis_request
    from knowledge.instruction_facts import instruction_fact_targets
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.semantics import generation_process_fact_needs

    spec = confirmed_spec if isinstance(confirmed_spec, Mapping) else {}
    # Device/error targets come only from the current retrieval query. The
    # confirmed specification contains settled I/O bindings and required
    # devices that are generation inputs, not automatic manual fact requests.
    # Selected opcodes are different: instruction_fact_targets deliberately
    # carries required_opcodes from the confirmed generation contract.
    route = route_analysis_request(
        query,
        confirmed_context=None,
        resolve_opcode=DEFAULT_INSTRUCTION_REGISTRY.resolve_form,
    )
    instructions = instruction_fact_targets(query, spec)
    identified = {str(row.get("opcode") or "").upper() for row in instructions}
    try:
        alias_targets = _declared_instruction_alias_targets(route.query_text)
    except (OSError, sqlite3.Error):
        # Alias enrichment is optional during query planning. A missing,
        # corrupt, or unhydrated index must not discard explicit identities or
        # block generation. Evidence retrieval reports storage failures at its
        # existing diagnostic boundary; it is not made successful here.
        core._close_thread_connection()
        alias_targets = []
    for target in alias_targets:
        if target["opcode"] not in identified:
            instructions.append(target)
            identified.add(target["opcode"])
    return {
        "version": _VERSION,
        "instructions": copy.deepcopy(instructions),
        "devices": list(dict.fromkeys(value.upper() for value in route.devices)),
        "errors": list(dict.fromkeys(core._error_terms(str(query or "")))),
        "process": generation_process_fact_needs(spec, plc_model=plc_model),
    }


def _runtime():
    from knowledge import core

    path = core._index_path()
    identity = core._index_identity(path)
    if identity[0] == "missing":
        return core, None, None, None, {}
    connection = core._connection(path, identity)
    schema = core._schema(connection)
    meta = core._load_meta(connection, schema)
    return core, path, connection, schema, meta


def _declared_instruction_alias_targets(query):
    """Resolve declared natural-language names, not inferred implementation intent.

    The importer already owns these aliases. Preserve their lookup when exact
    facts move out of broad RRF instead of rebuilding a prompt-specific rule.
    Incidental English prose is not an opcode or a free-form title match.
    """
    core, _path, connection, schema, _meta = _runtime()
    aliases = schema.get("instruction_aliases") if schema else None
    instructions = schema.get("instructions") if schema else None
    if not connection or not aliases or not instructions:
        return []
    if not {"alias", "alias_type", "instruction_id"}.issubset(aliases["columns"]) or not {"id", "opcode"}.issubset(instructions["columns"]):
        return []
    rows = connection.execute(
        f"SELECT DISTINCT a.alias,i.opcode FROM {core._quote_identifier(aliases['name'])} a "
        f"JOIN {core._quote_identifier(instructions['name'])} i ON i.id=a.instruction_id "
        "WHERE a.alias_type='zh_alias' ORDER BY a.alias,i.opcode"
    )
    targets = []
    for row in rows:
        alias, opcode = str(row["alias"] or ""), str(row["opcode"] or "").upper()
        if len(alias) < 2 or not core._alias_occurs(query, alias):
            continue
        targets.append({"opcode": opcode, "base_opcode": opcode,
                        "target_resolution": "declared_manual_alias", "requested_alias": alias})
    return targets


def _chunk_results(chunk_ids, *, plc_model, task_type, fact_kind, fact_target, augment_instruction=False):
    core, path, connection, schema, meta = _runtime()
    if connection is None or schema is None or not chunk_ids:
        return []
    ordered_ids = list(dict.fromkeys(str(value) for value in chunk_ids if value is not None))
    rows = core._fetch_chunks(connection, schema, [("id", value) for value in ordered_ids])
    results = []
    for chunk_id in ordered_ids:
        row = rows.get(("id", str(chunk_id)))
        result = core._chunk_result(row, meta, path, plc_model, task_type)
        if result is None:
            continue
        original_text = str(core._row_value(row, core._TEXT_COLUMNS, result.get("text") or ""))
        if augment_instruction:
            result = core._augment_structured_instruction(connection, schema, result)
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


def _instruction_lane_prompt_lines(lanes):
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
    details = "\n".join(_instruction_lane_prompt_lines(lanes))
    if not details:
        return value
    if body.startswith("[STRUCTURED INSTRUCTION RECORD]"):
        first, separator, rest = body.partition("\n")
        value["text"] = first + "\n" + details + (separator + rest if separator else "")
    else:
        value["text"] = details + ("\n\n" + body if body else "")
    return value


def resolve_instruction_step_width(target, *, plc_model="FX3U"):
    """Resolve one instruction expected GX program width from the shared owner.

    Exact opcode+operands use instruction_step_width. Opcode-only targets may
    expose a width only when the catalogue proves the mnemonic has one fixed
    observed width/arity. Operand-dependent and unsupported forms remain
    explicitly unresolved; this layer never guesses operands or a one-step
    fallback.
    """
    from plc.instruction_steps import default_step_width_catalog, instruction_step_width

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
            "resolution": "unresolved",
            "source": "unknown",
            "reason": "Instruction opcode is missing",
            "evidence": [],
            "operands": operands,
        }

    if has_operands:
        width = instruction_step_width(opcode, operands, plc_model=plc_model)
        return {
            "known": width.known,
            "steps": width.steps,
            "resolution": "instruction_instance" if width.known else "unresolved_instance",
            "source": width.source,
            "reason": width.reason,
            "evidence": list(width.evidence),
            "operands": operands,
        }

    try:
        catalogue = default_step_width_catalog()
        model = str(plc_model or "FX3U").strip().upper()
        fixed = catalogue.fixed_forms().get(opcode) if model in catalogue.models else None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        fixed = None
        unavailable_reason = "Step-width metadata unavailable: " + str(exc)
    else:
        unavailable_reason = ""

    if fixed is not None:
        steps, arity = fixed
        return {
            "known": True,
            "steps": int(steps),
            "resolution": "fixed_mnemonic",
            "source": "native_observation",
            "reason": "",
            "evidence": [],
            "operands": [],
            "operand_arity": int(arity),
        }

    return {
        "known": False,
        "steps": None,
        "resolution": "requires_operands",
        "source": "unknown",
        "reason": unavailable_reason or (
            "Opcode-only target is operand-dependent, variable-width, "
            "unsupported for this CPU, or lacks one fixed observed width"
        ),
        "evidence": [],
        "operands": [],
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
        *_instruction_lane_prompt_lines(lanes),
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
    core, _path, connection, schema, _meta = _runtime()
    chunks = schema.get("chunks") if schema is not None else None
    if not connection or not chunks or not {"id", "section", "text", "manual_type"}.issubset(chunks["columns"]):
        return []
    indexed = getattr(core._thread_state, "instruction_sections", None)
    if indexed is None:
        indexed = []
        placeholders = ",".join("?" for _ in _OFFICIAL_INSTRUCTION_TYPES)
        for row in connection.execute(
            f"SELECT * FROM {core._quote_identifier(chunks['name'])} WHERE manual_type IN ({placeholders})",
            tuple(sorted(_OFFICIAL_INSTRUCTION_TYPES)),
        ):
            leaf = str(row["section"] or "").split(" > ")[-1]
            indexed.append((row, core._instruction_heading_terms(leaf)))
        core._thread_state.instruction_sections = indexed
    records = []
    for row, names in indexed:
        if opcode not in names or not core._row_in_scope(row, plc_model, task_type):
            continue
        if not core._alias_occurs(str(row["text"] or ""), opcode):
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
    core, _path, connection, schema, _meta = _runtime()
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
            f"SELECT id FROM {core._quote_identifier(chunks['name'])} "
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


def resolve_instruction_records(targets, *, plc_model="FX3U", task_type="generate"):
    """Resolve canonical/variant opcodes directly through ``instructions``.

    No call to ``retrieve_knowledge`` is permitted here.
    """
    from knowledge.source_authority import instruction_source_authority

    core, _path, connection, schema, _meta = _runtime()
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
        # Prefer the compiled literal chapter over a historical index row whose
        # first opcode mention can be a reading-guide example. Verify its source
        # identity against this index before using the reproducible artifact.
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode, cpu=plc_model)
        materials = list(form.spec.source_materials) if form else []
        if not materials:
            entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry(opcode, cpu=plc_model) or {}
            materials = entry.get('source_materials', [])
        compiled = []
        for material in materials:
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
                    core._quote_identifier(table["name"]), placeholders
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
                f"SELECT * FROM {core._quote_identifier(table['name'])} WHERE chunk_id IN ({placeholders})",
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
    from plc.device_identity import canonical_device
    core, _path, _connection, _schema, _meta = _runtime()
    chunks = schema.get("chunks")
    if not chunks or not {"id", "section", "manual_type", "text"}.issubset(chunks["columns"]):
        return []
    parsed = re.fullmatch(r"([A-Z]+)([0-9A-F]+)", device)
    if not parsed:
        return []
    prefix, number = parsed.groups()
    ids = []
    for row in connection.execute(
        f"SELECT * FROM {core._quote_identifier(chunks['name'])} "
        "WHERE section LIKE '%[%]%' AND manual_type IN ('programming','positioning','structured_device')"
    ):
        if not core._row_in_scope(row, plc_model, task_type):
            continue
        leaf = str(row["section"] or "").split(" > ")[-1]
        for label in re.findall(r"\[([^\]]+)\]", leaf):
            match = re.fullmatch(r"\s*([A-Z]+)([0-9A-F]+)\s*(?:to|[-–～]|至)\s*([A-Z]+)([0-9A-F]+)\s*", label, re.I)
            matches = canonical_device(label.strip().upper()) == device
            if match:
                low_prefix, low, high_prefix, high = match.groups()
                matches = low_prefix.upper() == high_prefix.upper() == prefix and int(low, 16) <= int(number, 16) <= int(high, 16)
            body = re.split(r"\n\[(?:PAGE|TABLE)\b", str(row["text"] or ""), maxsplit=1)[-1]
            # Continuation chunks can repeat a heading while containing only an
            # adjacent topic. The cited body must still contain this definition.
            body_mentions = core._alias_occurs(body, device) or label.casefold() in body.casefold()
            if matches and body_mentions:
                ids.append(row["id"])
                break
    return ids


def resolve_device_records(devices, *, plc_model="FX3U", task_type="analysis", query=None):
    """Resolve device definitions and explicit property questions, before RRF.

    An original official range table owns time-base questions. This retains the
    established metadata selector without allowing incidental T-device mentions
    in program examples to displace that evidence in the broad rank fusion.
    """
    from plc.device_identity import canonical_device
    core, _path, connection, schema, _meta = _runtime()
    if connection is None or schema is None:
        return []
    table = schema.get("device_records")
    table_available = bool(table and {"device_norm", "device", "chunk_id"}.issubset(table["columns"]))
    requested_devices = list(dict.fromkeys(str(item or "").strip().upper()
                             for item in devices or () if str(item or "").strip()))
    results, seen = [], set()
    chunks = schema.get("chunks")
    if query and core._query_is_timer_preset(str(query)) and chunks and {"id", "manual_type", "section", "text"}.issubset(chunks["columns"]):
        targets = [canonical_device(item) for item in requested_devices if re.fullmatch(r"T[0-9]+", item)] or ["T"]
        for row in connection.execute(
            f"SELECT * FROM {core._quote_identifier(chunks['name'])} "
            "WHERE manual_type IN ('programming','structured_device') AND section LIKE '%Numbers of timers%'"
        ):
            if not core._row_in_scope(row, plc_model, task_type) or not core._timer_range_evidence(row["text"], plc_model):
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
    for requested_device in requested_devices:
        device = canonical_device(requested_device)
        if not isinstance(device, str) or not device:
            continue
        definition_ids = _device_definition_ids(connection, schema, device, plc_model=plc_model, task_type=task_type)
        ids = definition_ids
        if not ids and table_available:
            rows = connection.execute(
                f"SELECT * FROM {core._quote_identifier(table['name'])} WHERE device_norm=? AND chunk_id IS NOT NULL ORDER BY occurrences DESC,id",
                (device.casefold(),),
            ).fetchall()
            ids = [row["chunk_id"] for row in rows if "plc_models" not in row.keys() or core._scope_matches(row["plc_models"], plc_model)]
        candidates = _chunk_results(ids, plc_model=plc_model, task_type=task_type, fact_kind="device", fact_target=device)
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
            results.append(record)
    return results


def resolve_process_records(needs, *, plc_model="FX3U", task_type="generate"):
    """Use original official device chapters for explicit process fact needs.

    No occurrence-index fallback: a timer or counter mentioned in an unrelated
    program is not its operating definition. Preserve original source blocks.
    """
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
    core, _path, connection, schema, _meta = _runtime()
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
                f"SELECT * FROM {core._quote_identifier(chunks['name'])} "
                "WHERE manual_type IN ('programming','structured_device') "
                f"AND text LIKE ? ORDER BY {ordering}",
                (f"%{family}%",),
            )
            for row in rows:
                if not core._row_in_scope(row, plc_model, task_type):
                    continue
                text = str(row["text"] or "")
                # The source section must include the actual operation, not just
                # a cross-reference or a device-number list.
                if "Functions and operation examples" not in text:
                    continue
                if target == "TIMER" and not (core._timer_range_evidence(text, plc_model)
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
    core, _path, connection, schema, _meta = _runtime()
    if connection is None or schema is None:
        return []
    table = schema.get("error_records")
    if not table or not {"error_code_norm", "error_code", "chunk_id"}.issubset(set(table["columns"])):
        return []

    results = []
    seen = set()
    for requested in codes or ():
        code = str(requested or "").strip().upper()
        if not code:
            continue
        variants = list(dict.fromkeys((code, code[:-1] if code.endswith("H") else code + "H")))
        placeholders = ",".join("?" for _ in variants)
        rows = connection.execute(
            "SELECT * FROM {} WHERE error_code_norm IN ({}) AND chunk_id IS NOT NULL "
            "ORDER BY pdf_page,table_index,row_index".format(
                core._quote_identifier(table["name"]), placeholders
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
    "resolve_error_records",
    "resolve_instruction_contract",
    "resolve_instruction_records",
    "resolve_instruction_step_width",
    "compact_structured_fact_record",
    "resolve_structured_records",
    "structured_fact_targets",
    "without_structured_targets",
]
