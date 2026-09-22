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
from collections.abc import Mapping

_VERSION = "structured-facts-v2-contract-merged"
_OFFICIAL_INSTRUCTION_TYPES = frozenset(
    {"programming", "positioning", "structured_instruction", "structured_function"}
)


def structured_fact_targets(query, confirmed_spec=None):
    """Return exact fact identities without retrieving any manual prose."""
    from knowledge import core
    from knowledge.analysis_router import route_analysis_request
    from knowledge.instruction_facts import instruction_fact_targets
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

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
    return {
        "version": _VERSION,
        "instructions": copy.deepcopy(instructions),
        "devices": list(dict.fromkeys(value.upper() for value in route.devices)),
        "errors": list(dict.fromkeys(core._error_terms(str(query or "")))),
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
        if augment_instruction:
            result = core._augment_structured_instruction(connection, schema, result)
            if result is None or result.get("manual_type") not in _OFFICIAL_INSTRUCTION_TYPES:
                continue
        value = dict(result)
        value["structured_fact_kind"] = fact_kind
        value["structured_fact_target"] = fact_target
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


def _attach_instruction_contract(record, target, *, plc_model):
    contract = resolve_instruction_contract(target, plc_model=plc_model)
    value = dict(record)
    value["instruction_contract"] = copy.deepcopy(contract)

    body = str(value.get("text") or "")
    detail = "INSTRUCTION_CONTRACT: " + json.dumps(
        contract, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )
    if body.startswith("[STRUCTURED INSTRUCTION RECORD]"):
        first, separator, rest = body.partition("\n")
        value["text"] = first + "\n" + detail + (separator + rest if separator else "")
    else:
        value["text"] = detail + ("\n\n" + body if body else "")
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
        value["id"] = original_id + "#instance-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
        value["instruction_instance"] = {
            "opcode": str(target.get("opcode") or "").upper(),
            "operands": [str(item) for item in operands],
        }

    body = str(value.get("text") or "")
    if fact["known"]:
        detail = f"STEP_WIDTH: {fact['steps']} program step(s)"
        if fact["resolution"] == "fixed_mnemonic":
            detail += f" [fixed mnemonic; arity={fact.get('operand_arity', '?')}; source={fact['source']}]"
        else:
            detail += f" [instruction instance; source={fact['source']}]"
    else:
        detail = "STEP_WIDTH: unresolved"
        if fact["resolution"] == "requires_operands":
            detail += " [opcode alone is insufficient; supply operands]"
        elif fact.get("reason"):
            detail += f" [{fact['reason']}]"

    if body.startswith("[STRUCTURED INSTRUCTION RECORD]"):
        first, separator, rest = body.partition("\n")
        value["text"] = first + "\n" + detail + (separator + rest if separator else "")
    else:
        value["text"] = detail + ("\n\n" + body if body else "")
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
    lines = [
        "[STRUCTURED LOCAL INSTRUCTION RECORD]",
        f"INSTRUCTION: {opcode}",
        "INSTRUCTION_CONTRACT: " + json.dumps(
            contract, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ),
    ]
    if isinstance(operands, (list, tuple)):
        lines.append("OPERANDS: " + " ".join(str(value) for value in operands))
    if step_width["known"]:
        lines.append(f"STEP_WIDTH: {step_width['steps']} program step(s)")
        lines.append(f"STEP_WIDTH_SOURCE: {step_width['source']}")
    else:
        lines.append("STEP_WIDTH: unresolved")
        lines.append("STEP_WIDTH_REASON: " + str(step_width.get("reason") or "unknown"))

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
        "structured_lookup": True,
        "match_type": "structured_direct",
        "instruction_step_width": copy.deepcopy(step_width),
        "instruction_contract": copy.deepcopy(contract),
        "task_type": task_type,
        "plc_model": plc_model,
    }
    if isinstance(operands, (list, tuple)):
        value["instruction_instance"] = {
            "opcode": opcode,
            "operands": [str(item) for item in operands],
        }
    return value


def resolve_instruction_records(targets, *, plc_model="FX3U", task_type="generate"):
    """Resolve canonical/variant opcodes directly through ``instructions``.

    No call to ``retrieve_knowledge`` is permitted here.
    """
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
        rows = []
        if table_available:
            placeholders = ",".join("?" for _ in names)
            rows = connection.execute(
                "SELECT * FROM {} WHERE opcode_norm IN ({}) AND chunk_id IS NOT NULL".format(
                    core._quote_identifier(table["name"]), placeholders
                ),
                tuple(value.casefold() for value in names),
            ).fetchall()
        # Prefer the exact selected form and the most complete structured record.
        # Manual priority only resolves otherwise-equivalent official sources;
        # there is no query-dependent score or opcode-specific boost here.
        candidates = []
        for row in rows:
            row_opcode = str(row["opcode"] if "opcode" in row.keys() else row["opcode_norm"]).upper()
            exact = 1 if row_opcode == opcode else 0
            chunk = _chunk_results(
                [row["chunk_id"]],
                plc_model=plc_model,
                task_type=task_type,
                fact_kind="instruction",
                fact_target=opcode or base,
                augment_instruction=True,
            )
            if not chunk:
                continue
            candidate = _attach_instruction_step_width(
                chunk[0], step_target, plc_model=plc_model,
            )
            candidate = _attach_instruction_contract(
                candidate, step_target, plc_model=plc_model,
            )
            candidates.append(
                (
                    -exact,
                    -_instruction_completeness(row),
                    -int(candidate.get("manual_priority") or 0),
                    int(candidate.get("pdf_page") or 0),
                    str(candidate.get("id") or ""),
                    candidate,
                )
            )
        if not candidates:
            fallback = _local_instruction_fact_record(
                step_target, plc_model=plc_model, task_type=task_type,
            )
            if fallback is not None:
                candidates.append(
                    (
                        0,
                        0,
                        -int(fallback.get("manual_priority") or 0),
                        0,
                        str(fallback.get("id") or ""),
                        fallback,
                    )
                )

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


def resolve_device_records(devices, *, plc_model="FX3U", task_type="analysis"):
    """Resolve explicit device identities through ``device_records`` only."""
    core, _path, connection, schema, _meta = _runtime()
    if connection is None or schema is None:
        return []
    table = schema.get("device_records")
    if not table or not {"device_norm", "device", "chunk_id"}.issubset(set(table["columns"])):
        return []

    results = []
    seen = set()
    for requested in devices or ():
        device = str(requested or "").strip().upper()
        if not device:
            continue
        rows = connection.execute(
            "SELECT * FROM {} WHERE device_norm=? AND chunk_id IS NOT NULL "
            "ORDER BY occurrences DESC,id".format(core._quote_identifier(table["name"])),
            (device.casefold(),),
        ).fetchall()
        for row in rows:
            if "plc_models" in row.keys() and not core._scope_matches(row["plc_models"], plc_model):
                continue
            chunks = _chunk_results(
                [row["chunk_id"]],
                plc_model=plc_model,
                task_type=task_type,
                fact_kind="device",
                fact_target=device,
            )
            for candidate in chunks:
                marker = candidate.get("id")
                if marker in seen:
                    continue
                seen.add(marker)
                results.append(candidate)
    results.sort(
        key=lambda item: (
            -int(item.get("manual_priority") or 0),
            int(item.get("pdf_page") or 0),
            str(item.get("id") or ""),
        )
    )
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
    "resolve_structured_records",
    "structured_fact_targets",
    "without_structured_targets",
]
