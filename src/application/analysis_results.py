"""Analysis results."""
import copy
import json
import re
from shared.i18n import tr
from plc.specification.approach import (
    IMPLEMENTATION_SEMANTIC_STATUSES,
    SUPPORTED_STRUCTURES,
    normalize_approach,
    normalize_implementation_semantics,
)
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


_CURRENT_APPROACH_FORBIDDEN_FIELDS = frozenset({
    "generation_contract",
    "explicit_user_constraints",
    "implementation_preferences",
})
_CURRENT_SEMANTIC_FORBIDDEN_FIELDS = frozenset({
    "opcode",
    "operands",
    "device",
    "instruction_instance",
})
_CURRENT_ROOT_FORBIDDEN_FIELDS = frozenset({"execution_semantics"})


class AnalysisProtocolError(ValueError):
    """Fresh Agent-A JSON does not satisfy the current analysis wire protocol."""

    def __init__(self, violations, *, details=()):
        self.violations = tuple(str(item) for item in violations if str(item).strip())
        self.details = tuple(copy.deepcopy(item) for item in details)
        super().__init__("; ".join(self.violations) or "analysis protocol violation")


def prepare_analysis_payload(result, *, contract_stage="bound", user_text="", confirmed_spec=None):
    """Separate optional unbound proposals from the active wire contract.

    A missing device/evidence binding stays an inactive proposal. Other shape
    failures and fabricated evidence retain the normal bounded protocol repair.
    """
    if not isinstance(result, dict):
        return result
    prepared = copy.deepcopy(result)
    prepared.pop("_deferred_execution_claims", None)
    claims = prepared.get("execution_intent_claims")
    if not isinstance(claims, list):
        return prepared
    from plc.execution_intent import compile_execution_intent_claims, execution_intent_claim_violations
    retained, pending = [], []
    text = " ".join(str(user_text or "").split())
    for index, claim in enumerate(claims):
        violations = execution_intent_claim_violations([claim])
        evidence = claim.get("evidence", []) if isinstance(claim, dict) else []
        if (contract_stage == "requirements"
                and violations == ["$.execution_intent_claims[0].trigger.source_devices: this trigger kind requires a source device"]
                and text and all(" ".join(span.split()) in text for span in evidence)):
            pending.append({"index": index, "status": "pending_binding", "reason": "source_binding_missing", "candidate": claim})
            continue
        if not violations:
            receipt = compile_execution_intent_claims([claim], user_text, confirmed_spec=confirmed_spec)
            if [item["reason"] for item in receipt["rejected"]] == ["claimed_device_not_in_evidence"]:
                # Core still rejects the binding. Keep the claim for review,
                # without synthesizing evidence or turning it into semantics.
                pending.append({"index": index, "status": "pending_binding",
                                "reason": "claimed_device_not_in_evidence", "candidate": claim})
                continue
        retained.append(claim)
    prepared["execution_intent_claims"] = retained
    if pending:
        prepared["_deferred_execution_claims"] = pending
    return prepared


def current_analysis_protocol_violations(result):
    """Validate only the current Agent-A wire shape, never PLC engineering behavior."""
    if not isinstance(result, dict):
        return ["$: analysis response must be a JSON object"]

    approaches = result.get("approaches")
    violations = []
    if not isinstance(approaches, list):
        violations.append("$.approaches: current protocol requires an array")
        approaches = []
    forbidden_root = sorted(_CURRENT_ROOT_FORBIDDEN_FIELDS.intersection(result))
    if forbidden_root:
        violations.append(
            "$: model must not emit " + ", ".join(forbidden_root)
            + "; use execution_intent_claims instead"
        )
    from plc.execution_intent import execution_intent_claim_violations
    from plc.specification.explicit_constraint_claims import (
        explicit_constraint_claim_violations,
    )
    violations.extend(
        execution_intent_claim_violations(result.get("execution_intent_claims"))
    )
    violations.extend(
        explicit_constraint_claim_violations(result.get("explicit_constraint_claims"))
    )
    if 'operation_intents' in result:
        violations.append('$.operation_intents: model must use operation_intent_claims; confirmation belongs to the user')
    if 'behavior_constraints' in result:
        violations.append('$.behavior_constraints: model must use behavior_claims; confirmation belongs to the user')
    if 'operation_intent_claims' in result:
        from plc.instruction_binding import normalize_operation_intents
        from plc.instruction_definition import DefinitionError
        try:
            normalize_operation_intents(result['operation_intent_claims'], candidate=True)
        except DefinitionError as error:
            violations.append('$.operation_intent_claims: ' + str(error))
    for index, approach in enumerate(approaches):
        path = f"$.approaches[{index}]"
        if not isinstance(approach, dict):
            violations.append(path + ": approach must be an object")
            continue

        forbidden = sorted(_CURRENT_APPROACH_FORBIDDEN_FIELDS.intersection(approach))
        if forbidden:
            violations.append(
                path + ": model must not emit " + ", ".join(forbidden)
            )

        if "implementation_semantics" not in approach:
            violations.append(
                path + ".implementation_semantics: required current-protocol field is missing"
            )
            continue
        semantics = approach.get("implementation_semantics")
        if not isinstance(semantics, list):
            violations.append(
                path + ".implementation_semantics: must be an array (empty is allowed)"
            )
            continue

        for semantic_index, item in enumerate(semantics):
            semantic_path = (
                path + f".implementation_semantics[{semantic_index}]"
            )
            if not isinstance(item, dict):
                violations.append(semantic_path + ": semantic must be an object")
                continue

            forbidden_semantic = sorted(
                _CURRENT_SEMANTIC_FORBIDDEN_FIELDS.intersection(item)
            )
            if forbidden_semantic:
                violations.append(
                    semantic_path + ": low-level fields are not Agent-A semantics: "
                    + ", ".join(forbidden_semantic)
                )

            kind = str(item.get("kind") or "").strip().casefold()
            status = str(item.get("status") or "").strip().casefold()
            if kind != "structure":
                violations.append(
                    semantic_path + ".kind: only 'structure' is allowed"
                )
                continue
            if status not in IMPLEMENTATION_SEMANTIC_STATUSES:
                violations.append(
                    semantic_path + ".status: expected required, forbidden, or any_of"
                )
                continue

            if status == "any_of":
                values = item.get("values")
                if (
                    not isinstance(values, list)
                    or not values
                    or any(not isinstance(value, str) or not value.strip() for value in values)
                ):
                    violations.append(
                        semantic_path + ".values: any_of requires a non-empty string array"
                    )
                    continue
                if any(value.strip().casefold() not in SUPPORTED_STRUCTURES for value in values):
                    violations.append(
                        semantic_path + ".values: use canonical Core structure tokens only"
                    )
                    continue
            else:
                value = item.get("value")
                if not isinstance(value, str) or not value.strip():
                    violations.append(
                        semantic_path + ".value: required/forbidden requires a structure name"
                    )
                    continue
                if value.strip().casefold() not in SUPPORTED_STRUCTURES:
                    violations.append(
                        semantic_path + ".value: use a canonical Core structure token"
                    )
                    continue

            if not normalize_implementation_semantics([item]):
                violations.append(
                    semantic_path + ": implementation semantic could not be normalized"
                )

    return violations


def current_analysis_protocol_details(result):
    """Keep legacy messages and expose actual values plus Core-owned contracts."""
    from plc.execution_intent import execution_intent_claim_details
    from plc.specification.explicit_constraint_claims import explicit_constraint_claim_details
    claim_details = []
    if isinstance(result, dict):
        claim_details = [*execution_intent_claim_details(result.get("execution_intent_claims")),
                         *explicit_constraint_claim_details(result.get("explicit_constraint_claims"))]
    by_path = {item["path"]: item for item in claim_details}
    details = []
    for violation in current_analysis_protocol_violations(result):
        path, message = violation.split(": ", 1)
        if path in by_path:
            details.append(copy.deepcopy(by_path[path]))
            continue
        actual = result
        for index, field in re.findall(r"\[(\d+)\]|\.([A-Za-z_][A-Za-z0-9_]*)", path):
            try:
                actual = actual[int(index) if index else field]
            except (IndexError, KeyError, TypeError):
                actual = None
                break
        details.append({"path": path, "code": "invalid_analysis_shape", "message": message,
                        "actual": copy.deepcopy(actual)})
    return details


def analysis_grounding_details(result, user_text, *, plc_model="FX3U", confirmed_spec=None, contract_stage="bound"):
    """Inspect each claim independently so one malformed frame cannot mask others."""
    from plc.execution_intent import compile_execution_intent_claims, execution_intent_claim_violations
    from plc.specification.explicit_constraint_claims import compile_explicit_constraint_claims, explicit_constraint_claim_violations
    if not isinstance(result, dict) or not str(user_text or "").strip():
        return []
    details = []
    for name, check, compile_claim in (
        ("explicit_constraint_claims", explicit_constraint_claim_violations,
         lambda claims: compile_explicit_constraint_claims(claims, user_text, plc_model)),
        ("execution_intent_claims", execution_intent_claim_violations,
         lambda claims: compile_execution_intent_claims(claims, user_text, confirmed_spec=confirmed_spec)),
    ):
        claims = result.get(name)
        if not isinstance(claims, list):
            continue
        for index, claim in enumerate(claims):
            if check([claim]):
                continue
            for rejected in compile_claim([claim]).get("rejected", []):
                rows = rejected.get("details") or [{"path": f"$.{name}[0]", "code": rejected["reason"],
                    "message": "claim must bind to exact current-request evidence and its reported devices",
                    "actual": copy.deepcopy(claim)}]
                for item in rows:
                    item = copy.deepcopy(item)
                    item["path"] = item["path"].replace(f"$.{name}[0]", f"$.{name}[{index}]", 1)
                    details.append(item)
    return details


def validate_current_analysis_protocol(result):
    """Raise an analysis-protocol error before normalization or PLC validation."""
    violations = current_analysis_protocol_violations(result)
    if violations:
        raise AnalysisProtocolError(violations, details=current_analysis_protocol_details(result))
    return result


def _extract_user_declared_io(user_text, plc_model):
    """Project Core-owned explicit declaration bindings back to suggested_io."""
    from plc.specification.bindings import extract_declared_bindings

    declared = {}
    for item in extract_declared_bindings(user_text, plc_model):
        address = canonical_device(str(item.get("address") or "").strip().upper())
        label = str(item.get("label") or "").strip()
        if not address or not label:
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


def _extract_user_declared_bindings(user_text, plc_model):
    """Application wrapper around Core-owned explicit I/O declaration parsing."""
    from plc.specification.bindings import extract_declared_bindings
    return extract_declared_bindings(user_text, plc_model)


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
    """Ground Agent-A user-constraint claims, then apply structured edits."""
    from plc.specification.explicit_constraint_claims import compile_explicit_constraint_claims
    from plc.specification.explicit_constraints import apply_explicit_constraint_operations

    receipt = compile_explicit_constraint_claims(
        result.get("explicit_constraint_claims") or [], user_text, plc_model
    )
    result["explicit_constraint_receipt"] = {
        "accepted": copy.deepcopy(receipt.get("accepted") or []),
        "rejected": copy.deepcopy(receipt.get("rejected") or []),
    }
    previous_selected = confirmed_spec.get("selected_approach") if isinstance(confirmed_spec, dict) else None
    previous_id = str((previous_selected or {}).get("approach_id") or "").strip()
    previous_constraints = _selected_low_level_constraints(previous_selected)

    approaches = []
    for raw in result.get("approaches", []) or []:
        if not isinstance(raw, dict):
            continue
        approach = dict(raw)
        same_plan = previous_id and str(approach.get("approach_id") or "").strip() == previous_id
        base = previous_constraints if same_plan else {}
        merged = apply_explicit_constraint_operations(base, receipt["operations"])
        if "implementation_semantics" in approach:
            approach["explicit_user_constraints"] = merged
            approach = normalize_approach(approach)
        else:
            approach = normalize_approach(approach)
            contract = dict(approach.get("generation_contract") or {})
            for key in ("required_opcodes", "forbidden_opcodes", "required_devices", "forbidden_devices", "instruction_instances"):
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

    fresh_semantics_protocol = any(
        isinstance(item, dict) and "implementation_semantics" in item
        for item in (result.get("approaches") or [])
    )
    normalized = dict(result)
    # Only the application can attach user-derived hardware evidence. A model
    # cannot authenticate its own questions by emitting this metadata field.
    normalized.pop("hardware_intent", None)
    normalized.pop("engineering_context", None)
    normalized.pop("intent_context", None)
    normalized.pop("decision_receipt", None)
    normalized.pop("explicit_constraint_receipt", None)
    normalized.pop("analysis_repair_receipt", None)
    normalized.pop("declared_io_bindings", None)
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
    if fresh_semantics_protocol:
        allowed_addresses = set(confirmed_addresses)
        allowed_addresses.update(
            canonical_device(address)
            for values in declared_io.values()
            for address in values
        )
        removed_model_allocations = []
        for category, values in list(clean_io.items()):
            if not isinstance(values, dict):
                continue
            for address in list(values):
                identity = canonical_device(address)
                if identity not in allowed_addresses:
                    removed_model_allocations.append(identity)
                    values.pop(address, None)
            if not values:
                clean_io.pop(category, None)
        if removed_model_allocations:
            add_diagnostic(
                "model_internal_io_allocation_removed",
                "suggested_io",
                str(tr("模型分配的未声明内部软元件已移除；内部地址由生成阶段决定。")),
                sorted(set(removed_model_allocations)),
            )
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
    normalized["declared_io_bindings"] = _extract_user_declared_bindings(
        user_text, plc_model
    )
    if hardware:
        normalized["hardware_config"] = hardware
    else:
        normalized.pop("hardware_config", None)
    normalized["assumptions"] = assumptions
    normalized["format_diagnostics"] = diagnostics
    normalized["plc_model"] = plc_model
    from plc.semantics import normalize_semantic_requirements
    from plc.execution_intent import (
        compile_execution_intent_claims,
        extract_explicit_execution_semantics,
    )

    # Agent A may understand arbitrary wording, but it cannot author the final
    # validation enum directly.  Core grounds each frame in current-request
    # evidence and independently retains only formal explicit syntax as a fast
    # path.  On pinned reanalysis, current claims replace only the source
    # devices they touch; unrelated confirmed semantics remain stable.
    normalized.pop("execution_semantics", None)
    claim_receipt = compile_execution_intent_claims(
        normalized.get("execution_intent_claims") or [],
        user_text,
        source="agent_a_claim",
        confirmed_spec=confirmed_spec,
    )
    explicit_semantics = normalize_semantic_requirements(
        extract_explicit_execution_semantics(
            user_text,
            source="current_request_explicit",
        )
    )
    # A grounded Agent-A frame owns the evidence spans it interpreted.  The
    # explicit-syntax fast path only fills uncovered formal notation; it must
    # not independently reinterpret another device in the same claimed clause.
    claimed_evidence = {
        " ".join(str(item or "").split()).casefold()
        for claim in claim_receipt.get("accepted") or []
        for item in claim.get("evidence") or []
        if str(item or "").strip()
    }
    if claimed_evidence:
        explicit_semantics = [
            item for item in explicit_semantics
            if not (
                (evidence := " ".join(
                    str(item.get("evidence") or "").split()
                ).casefold())
                and any(
                    evidence in claimed or claimed in evidence
                    for claimed in claimed_evidence
                )
            )
        ]
    current_semantics = normalize_semantic_requirements(
        [*claim_receipt["requirements"], *explicit_semantics]
    )
    touched_devices = set(claim_receipt.get("touched_devices") or [])
    touched_devices.update(
        device
        for item in explicit_semantics
        for device in item.get("devices") or []
    )
    previous_semantics = normalize_semantic_requirements(
        (confirmed_spec or {}).get("execution_semantics") or []
    ) if isinstance(confirmed_spec, dict) else []
    if touched_devices:
        previous_semantics = [
            item for item in previous_semantics
            if not touched_devices.intersection(item.get("devices") or [])
        ]
    normalized["execution_semantics"] = normalize_semantic_requirements(
        [*previous_semantics, *current_semantics]
    )
    normalized["execution_intent_receipt"] = {
        "accepted": copy.deepcopy(claim_receipt.get("accepted") or []),
        "rejected": copy.deepcopy(claim_receipt.get("rejected") or []),
        "pending": normalized.pop("_deferred_execution_claims", []),
    }
    from plc.specification.behavior import normalize_behavior_constraints
    normalized['behavior_constraints'] = []
    normalized['behavior_claim_diagnostics'] = []
    for claim in normalized.get('behavior_claims') or []:
        try:
            normalized['behavior_constraints'].extend(normalize_behavior_constraints(
                [claim], candidate=True, evidence_text=user_text))
        except ValueError as error:
            normalized['behavior_claim_diagnostics'].append({'status': 'unverified', 'reason': str(error)})
    normalized = ensure_hardware_questions(normalized, plc_model, user_text, confirmed_spec)
    normalized = _apply_explicit_user_constraints(
        normalized, user_text, plc_model, confirmed_spec
    )
    from plc.specification.provenance import analysis_context
    from application.generation_support import public_generation_value
    normalized.update(analysis_context(
        public_generation_value(user_text), normalized.get("approaches", []), confirmed_spec,
    ))
    if normalized.get("operation_intent_claims"):
        from plc.instruction_binding import normalize_operation_intents
        normalized["operation_intents"] = normalize_operation_intents(
            normalized["operation_intent_claims"], candidate=True, evidence_text=user_text,
        )
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
