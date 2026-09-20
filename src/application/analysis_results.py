"""Analysis results."""
import json
import re
from shared.i18n import tr
from plc.specification.approach import normalize_approach
from plc.validation import PLCJsonValidationError, parse_device_address
from plc.device_identity import canonical_device
from plc.hardware_profiles import ensure_hardware_questions

_ANALYSIS_IO_KINDS = {"X", "Y", "M", "D", "T", "C", "S", "SM", "SD"}


_ASSUMPTION_MARKERS = ("假设", "暂定", "待确认", "需确认", "unknown", "assume")
_DECLARED_IO_LINE_RE = re.compile(
    r"^\s*(?:[-*•]\s*|\d+[.)、]\s*)?((?:SM|SD|[XYMTCSDVZ])\s*\d+)\s*[：:]\s*(.+?)\s*$",
    re.IGNORECASE,
)


def _extract_user_declared_io(user_text, plc_model):
    """Recover explicit address-to-purpose declarations from the user request.

    This is a non-blocking preservation path, not a validator. Only standalone
    device: purpose lines are accepted so comparisons such as D0 = 1~3 and
    instruction operands cannot accidentally become I/O allocations.
    """
    declared = {}
    for raw_line in str(user_text or "").splitlines():
        match = _DECLARED_IO_LINE_RE.match(raw_line)
        if match is None:
            continue
        address = canonical_device(re.sub(r"\s+", "", match.group(1)).upper())
        label = str(match.group(2) or "").strip()
        if not label:
            continue
        try:
            parsed = parse_device_address(address, plc_model)
        except (PLCJsonValidationError, ValueError, TypeError):
            continue
        if parsed is None:
            continue
        actual_kind, _number = parsed
        declared.setdefault(actual_kind, {})[address] = label
    return declared


def _historical_declared_io(confirmed_spec, plc_model):
    """Return explicit I/O declarations already seen before this analysis turn."""
    historical = {}
    context = (confirmed_spec or {}).get("engineering_context") if isinstance(confirmed_spec, dict) else None
    requests = context.get("requests", []) if isinstance(context, dict) else []
    for item in requests or []:
        text = item.get("text", "") if isinstance(item, dict) else ""
        for category, values in _extract_user_declared_io(text, plc_model).items():
            target = historical.setdefault(category, {})
            for address, label in values.items():
                target[(canonical_device(address), str(label).strip().casefold())] = True
    return historical


def _confirmed_io_addresses(confirmed_spec):
    addresses = set()
    rows = (confirmed_spec or {}).get("io_table", []) if isinstance(confirmed_spec, dict) else []
    for row in rows or []:
        if isinstance(row, dict) and row.get("address"):
            addresses.add(canonical_device(str(row["address"]).strip().upper()))
    return addresses


def _removed_confirmed_io_addresses(confirmed_spec):
    overrides = (confirmed_spec or {}).get("io_user_overrides") if isinstance(confirmed_spec, dict) else None
    if not isinstance(overrides, dict):
        return set()
    return {
        canonical_device(str(address).strip().upper())
        for address in overrides.get("removed_addresses", []) or []
        if str(address).strip()
    }


def _iter_analysis_text(value):
    if isinstance(value, dict):
        for nested in value.values():
            for item in _iter_analysis_text(nested):
                yield item
    elif isinstance(value, (list, tuple)):
        for nested in value:
            for item in _iter_analysis_text(nested):
                yield item
    elif value is not None:
        yield str(value)


def _normalize_analysis_result(result, plc_model="FX3U", user_text="", confirmed_spec=None):
    """Normalize phase-one AI JSON before the specification editor sees it.

    Only actual PLC device addresses remain in ``suggested_io``.  Hardware
    metadata is preserved separately so strict specification validation never
    mistakes CHANNEL/ADDRESS/NOTE fields for C/D/I/O devices.
    """
    if not isinstance(result, dict):
        raise ValueError("Analysis response must be a JSON object")

    normalized = dict(result)
    # Only the application can attach user-derived hardware evidence. A model
    # cannot authenticate its own questions by emitting this metadata field.
    normalized.pop("hardware_intent", None)
    normalized.pop("engineering_context", None)
    normalized["approaches"] = [
        normalize_approach({key: value for key, value in item.items() if key != "implementation_preferences"})
        for item in (normalized.get("approaches") or [])
        if isinstance(item, dict)
    ]
    raw_io = normalized.get("suggested_io", {})
    if not isinstance(raw_io, dict):
        raw_io = {}

    existing_hardware = normalized.get("hardware_config")
    if isinstance(existing_hardware, dict):
        hardware = dict(existing_hardware)
    elif existing_hardware in (None, "", []):
        hardware = {}
    else:
        hardware = {"reported_value": existing_hardware}

    existing_assumptions = normalized.get("assumptions", [])
    if isinstance(existing_assumptions, list):
        assumptions = [str(item) for item in existing_assumptions if str(item).strip()]
    elif existing_assumptions:
        assumptions = [str(existing_assumptions)]
    else:
        assumptions = []

    existing_diagnostics = normalized.get("format_diagnostics", [])
    diagnostics = list(existing_diagnostics) if isinstance(existing_diagnostics, list) else []
    clean_io = {}
    unmapped = []
    metadata = {}

    def add_diagnostic(code, path, message, value=None):
        item = {"code": code, "path": path, "message": message}
        if value is not None:
            item["value"] = value
        diagnostics.append(item)

    def capture_assumptions(path, value):
        for text in _iter_analysis_text(value):
            lower = text.lower()
            if any(marker in lower for marker in _ASSUMPTION_MARKERS):
                note = "%s: %s" % (path, text)
                if note not in assumptions:
                    assumptions.append(note)

    for raw_category, values in raw_io.items():
        category_text = str(raw_category).strip()
        category_lower = category_text.lower()
        category_upper = category_text.upper()
        special_relays = category_lower == "special_relays"
        special_registers = category_lower == "special_registers"
        is_device_category = category_upper in _ANALYSIS_IO_KINDS

        if not (is_device_category or special_relays or special_registers):
            key = re.sub(r"[^a-z0-9_]+", "_", category_lower).strip("_") or "metadata"
            if key in hardware:
                metadata[category_text] = values
            else:
                hardware[key] = values
            capture_assumptions("suggested_io.%s" % category_text, values)
            add_diagnostic(
                "non_io_metadata_moved",
                "suggested_io.%s" % category_text,
                str(tr("非软元件类别已移入 hardware_config，未作为 I/O 使用。")),
                values,
            )
            continue

        if isinstance(values, dict):
            entries = list(values.items())
        elif isinstance(values, list):
            if is_device_category:
                add_diagnostic(
                    "io_labels_required",
                    "suggested_io.%s" % category_text,
                    str(tr("普通 I/O 类别必须使用地址到非空用途说明的字典；仅地址列表已忽略。")),
                    values,
                )
                continue
            entries = [(value, "") for value in values]
        else:
            metadata[category_text] = values
            add_diagnostic(
                "invalid_io_container",
                "suggested_io.%s" % category_text,
                str(tr("I/O 类别必须是地址字典；特殊软元件类别也可使用地址列表，原值已移入 hardware_config。")),
                values,
            )
            continue

        for raw_address, raw_label in entries:
            address = str(raw_address).strip().upper()
            path = "suggested_io.%s.%s" % (category_text, address or "<empty>")
            try:
                parsed_address = parse_device_address(address, plc_model)
                if parsed_address is None:
                    raise ValueError(
                        str(tr("{address} 不是 {model} 的合法软元件地址", address=address or "<empty>", model=plc_model))
                    )
                actual_kind, _number = parsed_address
            except (PLCJsonValidationError, ValueError, TypeError) as exc:
                item = {
                    "category": category_text,
                    "address": address,
                    "label": raw_label,
                }
                unmapped.append(item)
                capture_assumptions(path, raw_label)
                add_diagnostic("invalid_io_address", path, str(exc), item)
                continue

            allowed_kinds = None
            if special_relays:
                allowed_kinds = {"M", "SM"}
            elif special_registers:
                allowed_kinds = {"D", "SD"}
            elif is_device_category:
                allowed_kinds = {category_upper}

            if actual_kind not in allowed_kinds:
                add_diagnostic(
                    "io_category_corrected",
                    path,
                    str(tr("地址前缀与类别不一致，已按真实前缀归类为 {kind}。", kind=actual_kind)),
                    {"from": category_text, "to": actual_kind},
                )

            label = raw_label
            if isinstance(label, (dict, list)):
                add_diagnostic(
                    "io_label_normalized",
                    path,
                    str(tr("结构化标签不能作为 I/O 说明，已转为简短 JSON 文本。")),
                )
                label = json.dumps(label, ensure_ascii=False, separators=(",", ":"))
            else:
                label = str(label or "").strip()
            if is_device_category and not label:
                add_diagnostic(
                    "missing_io_label",
                    path,
                    str(tr("普通 I/O 地址缺少用途说明，已忽略该项。")),
                )
                continue
            if actual_kind == "SM" or (special_relays and actual_kind == "M"):
                target_category = "special_relays"
            elif actual_kind == "SD" or (special_registers and actual_kind == "D"):
                target_category = "special_registers"
            else:
                target_category = actual_kind
            clean_io.setdefault(target_category, {})[address] = label

    if metadata:
        current_metadata = hardware.get("analysis_metadata")
        if not isinstance(current_metadata, dict):
            if current_metadata not in (None, "", []):
                metadata = {"reported_value": current_metadata, **metadata}
            hardware["analysis_metadata"] = {}
        hardware["analysis_metadata"].update(metadata)
    if unmapped:
        current_unmapped = hardware.get("unmapped_suggested_io")
        if not isinstance(current_unmapped, list):
            current_unmapped = [] if current_unmapped in (None, "", {}) else [current_unmapped]
            hardware["unmapped_suggested_io"] = current_unmapped
        current_unmapped.extend(unmapped)

    # User-declared wiring seeds the first draft deterministically, but once a
    # specification has been confirmed its edited I/O table is authoritative.
    # Old declarations from earlier requests must not resurrect a row that the
    # operator changed or deleted in the specification editor.
    declared_io = _extract_user_declared_io(user_text, plc_model)
    historical = _historical_declared_io(confirmed_spec, plc_model)
    confirmed_addresses = _confirmed_io_addresses(confirmed_spec)
    removed_addresses = _removed_confirmed_io_addresses(confirmed_spec)
    historical_addresses = {
        address for values in historical.values() for address, _label in values
    }
    if confirmed_spec:
        for category, values in list(clean_io.items()):
            if not isinstance(values, dict):
                continue
            for address in list(values):
                identity = canonical_device(address)
                if identity in removed_addresses or (
                    identity in historical_addresses and identity not in confirmed_addresses
                ):
                    values.pop(address, None)
            if not values:
                clean_io.pop(category, None)

    for category, values in declared_io.items():
        target = clean_io.setdefault(category, {})
        seen = historical.get(category, {})
        for address, label in values.items():
            identity = canonical_device(address)
            # On a later analysis turn, only a genuinely new explicit
            # declaration may seed a new suggestion. Replaying the original
            # request cannot undo direct edits made in the confirmed spec.
            if confirmed_spec and (
                identity in removed_addresses
                or (identity, str(label).strip().casefold()) in seen
            ):
                continue
            for existing in list(target):
                if canonical_device(existing) == identity:
                    target.pop(existing, None)
            target[identity] = label

    normalized["suggested_io"] = clean_io
    if hardware:
        normalized["hardware_config"] = hardware
    else:
        normalized.pop("hardware_config", None)
    normalized["assumptions"] = assumptions
    normalized["format_diagnostics"] = diagnostics
    normalized["plc_model"] = plc_model
    from plc.semantics import (
        infer_semantic_requirements,
        normalize_semantic_requirements,
    )

    # Execution semantics are validation constraints, so hidden model output
    # must not be allowed to invent them.  Only deterministic evidence from the
    # user's own request is authoritative here.  A previously confirmed value
    # is preserved later by ``build_review_draft`` when this list is empty.
    inferred_semantics = infer_semantic_requirements(
        user_text,
        source="current_request",
    )
    normalized["execution_semantics"] = normalize_semantic_requirements(
        inferred_semantics
    )
    normalized = ensure_hardware_questions(normalized, plc_model, user_text, confirmed_spec)
    from plc.specification.provenance import analysis_context
    from application.generation_support import public_generation_value
    normalized["engineering_context"] = analysis_context(
        public_generation_value(user_text), normalized.get("approaches", []), confirmed_spec,
    )
    return normalized



def attach_analysis_evidence(result, analysis_evidence, *, plc_model="FX3U", knowledge_builder=None):
    """Bind engine-produced evidence to candidate identities before user review.

    Candidate lookup is evidence collection, not a claim that the plan was verified.
    No extra model request, hidden analysis text or inferred hard constraint is used.
    """
    from knowledge.evidence import context_manifest
    from plc.specification.provenance import evidence_snapshot
    from application.generation_support import public_generation_value
    if knowledge_builder is None:
        from application.generation_context import _build_knowledge_context
        knowledge_builder = _build_knowledge_context
    context = result["engineering_context"]
    context["analysis_evidence"] = public_generation_value(evidence_snapshot(
        context_manifest(analysis_evidence, stage="analysis")))
    by_id = {row.get("approach_id"): row for row in result.get("approaches", [])}
    for index, record in enumerate(context.get("proposals", [])):
        if index >= 3:
            record["evidence"] = {"stage": "candidate", "status": "excluded",
                                  "reason": "candidate_budget", "records": []}
            continue
        approach = by_id.get(record.get("approach_id"))
        if not approach:
            continue
        try:
            evidence = knowledge_builder("", plc_model=plc_model, task_type="generate",
                                         confirmed_context={"selected_approach": approach})
            manifest = context_manifest(evidence, stage="candidate")
            manifest["stage"] = "candidate"
        except Exception:
            manifest = {"stage": "candidate", "status": "unavailable", "records": []}
        record["evidence"] = public_generation_value(evidence_snapshot(manifest))
    return result
