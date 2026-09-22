"""Analysis results."""
import copy
import json
import re
from shared.i18n import tr
from plc.specification.approach import normalize_approach
from plc.validation import PLCJsonValidationError, parse_device_address
from plc.device_identity import canonical_device
from plc.hardware_profiles import ensure_hardware_questions

_ANALYSIS_IO_KINDS = {"X", "Y", "M", "D", "T", "C", "S", "V", "Z", "SM", "SD"}
_FLAT_IO_ADDRESS_RE = re.compile(r"(SM|SD|[XYMDTCSVZ])\d+", re.IGNORECASE)


_ASSUMPTION_MARKERS = ("假设", "暂定", "待确认", "需确认", "unknown", "assume")
_DECLARED_IO_LINE_RE = re.compile(
    r"^\s*(?:[-*•]\s*|\d+[.)、]\s*)?((?:SM|SD|[XYMTCSDVZ])\s*\d+)"
    r"\s*(?:[：:]|为|是|is\s+)\s*(.+?)\s*$",
    re.IGNORECASE,
)
_INPUT_QUALIFIER_RE = re.compile(
    r"[,，(（]\s*(?:按下|未按下|松开|释放|动作|未动作|常开|常闭|常開|常閉|normally\b|active\b)",
    re.IGNORECASE,
)
_STATE_NOT_PURPOSE_RE = re.compile(
    r"^(?:(?:ON|OFF|TRUE|FALSE)(?=$|[^A-Za-z0-9_])|[+-]?\d+(?:[.,]\d+)?(?=$|[\s~～<>=+\-]|时|時))",
    re.IGNORECASE,
)


def _extract_user_declared_io(user_text, plc_model):
    """Preserve explicit device-purpose declarations, including inline clauses.

    This bounded grammar is not intent inference: device: purpose, device 为/是
    purpose, or device is purpose at a statement boundary. It never assigns a
    purpose from an address number or parses instruction operands/conditions.
    Electrical qualifiers stay in original intent, separate from the short label.
    """
    declared = {}
    for statement in re.split(r"[\n;；。]+", str(user_text or "")):
        match = _DECLARED_IO_LINE_RE.fullmatch(statement)
        if match is None:
            continue
        address = canonical_device(re.sub(r"\s+", "", match.group(1)).upper())
        raw_label = match.group(2).strip()
        # A question or state comparison is not an explicit purpose declaration.
        if "?" in raw_label or "？" in raw_label or _STATE_NOT_PURPOSE_RE.match(raw_label):
            continue
        label = _INPUT_QUALIFIER_RE.split(raw_label, maxsplit=1)[0].strip()
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
    from plc.specification.provenance import intent_context
    context = intent_context(confirmed_spec)
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



def _selected_low_level_constraints(selected):
    """Read confirmed low-level constraints without reconstructing them from prose."""
    from plc.specification.explicit_constraints import normalize_explicit_user_constraints
    if not isinstance(selected, dict):
        return normalize_explicit_user_constraints({})
    if isinstance(selected.get("explicit_user_constraints"), dict):
        return normalize_explicit_user_constraints(selected["explicit_user_constraints"])
    contract = selected.get("generation_contract")
    if not isinstance(contract, dict):
        return normalize_explicit_user_constraints({})
    return normalize_explicit_user_constraints({
        "required_opcodes": contract.get("required_opcodes", []),
        "forbidden_opcodes": contract.get("forbidden_opcodes", []),
        "required_devices": contract.get("required_devices", []),
        "forbidden_devices": contract.get("forbidden_devices", []),
        "instruction_instances": contract.get("instruction_instances", []),
    })


def _apply_explicit_user_constraints(result, user_text, plc_model, confirmed_spec=None):
    """Merge caller-fixed low-level choices independently of Agent-A output."""
    from plc.specification.explicit_constraints import (
        extract_explicit_user_constraints,
        merge_explicit_user_constraints,
    )

    update = extract_explicit_user_constraints(user_text, plc_model)
    previous_selected = (
        confirmed_spec.get("selected_approach")
        if isinstance(confirmed_spec, dict)
        else None
    )
    previous_id = str((previous_selected or {}).get("approach_id") or "").strip()
    previous_constraints = _selected_low_level_constraints(previous_selected)

    approaches = []
    for raw in result.get("approaches", []) or []:
        if not isinstance(raw, dict):
            continue
        approach = dict(raw)
        same_plan = (
            previous_id
            and str(approach.get("approach_id") or "").strip() == previous_id
        )
        base = previous_constraints if same_plan else {}
        merged = merge_explicit_user_constraints(
            base,
            update["constraints"],
            clear_fields=update["clear_fields"],
        )

        if "implementation_semantics" in approach:
            approach["explicit_user_constraints"] = merged
            approach = normalize_approach(approach)
        else:
            # Compatibility for old Agent-A response fixtures/saved protocol.
            approach = normalize_approach(approach)
            contract = dict(approach.get("generation_contract") or {})
            for key in (
                "required_opcodes", "forbidden_opcodes",
                "required_devices", "forbidden_devices",
                "instruction_instances",
            ):
                contract[key] = copy.deepcopy(merged[key])
            approach["generation_contract"] = contract
        approaches.append(approach)

    result["approaches"] = approaches
    return result


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
    normalized.pop("intent_context", None)
    normalized.pop("decision_receipt", None)
    # Legacy UI/classification fields are not part of the current model contract.
    # Do not replay them into later model requests or migrate saved revisions.
    normalized.pop("control_type", None)
    normalized.pop("flowchart_steps", None)
    normalized["approaches"] = [
        normalize_approach({
            key: value
            for key, value in item.items()
            if key not in {"implementation_preferences", "explicit_user_constraints"}
            and not (key == "generation_contract" and "implementation_semantics" in item)
        })
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

    # Only the normalizer may author format diagnostics, never the model.
    diagnostics = []
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
        flat_address = _FLAT_IO_ADDRESS_RE.fullmatch(category_upper) if isinstance(values, str) else None
        is_device_category = category_upper in _ANALYSIS_IO_KINDS or flat_address is not None

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

        # Both {"X": {"X0": "name"}} and {"X0": "name"} carry an exact
        # device-purpose pair. A full device key is not a hardware category.
        # Do not recursively flatten arbitrary module/channel objects.
        if flat_address is not None:
            entries = [(category_text, values)]
        elif isinstance(values, dict):
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
            address = canonical_device(str(raw_address).strip().upper())
            path = ("suggested_io.%s" % category_text if flat_address is not None
                    else "suggested_io.%s.%s" % (category_text, address or "<empty>"))
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
                allowed_kinds = {flat_address.group(1) if flat_address is not None else category_upper}

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
    normalized = _apply_explicit_user_constraints(
        normalized, user_text, plc_model, confirmed_spec
    )
    from plc.specification.provenance import analysis_context
    from application.generation_support import public_generation_value
    normalized.update(analysis_context(
        public_generation_value(user_text), normalized.get("approaches", []), confirmed_spec,
    ))
    return normalized



def attach_analysis_evidence(result, analysis_evidence, *, plc_model="FX3U", knowledge_builder=None):
    """Audit evidence actually supplied to A. No post-candidate retrieval.

    The optional builder remains an API-compatible argument, not an operation.
    Evidence discovered after a candidate was produced is not its reasoning basis.
    """
    import copy
    from knowledge.evidence import context_manifest
    from plc.specification.provenance import evidence_snapshot
    from application.generation_support import public_generation_value
    result = copy.deepcopy(result)
    receipt = result.setdefault("decision_receipt", {"schema_version": 1, "kind": "analysis"})
    receipt["analysis_evidence"] = public_generation_value(evidence_snapshot(
        context_manifest(analysis_evidence, stage="analysis")))
    return result
