"""Serializable models and normalization helpers for ladder inspections.

The inspection boundary deliberately accepts dictionaries because reports are
stored as JSON and may also originate from an AI response.  Every public helper
returns plain JSON-compatible dictionaries so Web, the session store and older
callers do not need to know about the dataclasses used internally.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from shared.i18n import tr


REPORT_SCHEMA_VERSION = 1

REPORT_TYPES = {"program_review", "fault_debug"}
TRIGGERS = {"automatic", "manual", "ai_retry", "legacy"}
DEPTHS = {"basic", "deep"}
REPORT_STATUSES = {
    "complete", "local_only", "partial", "failed", "unsupported",
}
FINDING_SOURCES = {"local", "ai", "legacy"}
SEVERITIES = {"error", "warning", "info"}
CONFIDENCES = {"low", "medium", "high"}
RESOLUTION_STATUSES = {
    "open", "resolved", "still_present", "needs_review", "not_applicable",
}

_REPORT_TYPE_ALIASES = {
    "review": "program_review",
    "version_review": "program_review",
    "program_review": "program_review",
    "debug": "fault_debug",
    "fault_debug": "fault_debug",
}
_TRIGGER_ALIASES = {
    "auto": "automatic",
    "automatic": "automatic",
    "post_generation": "automatic",
    "manual": "manual",
    "retry": "ai_retry",
    "ai_retry": "ai_retry",
    "legacy": "legacy",
}
_STATUS_ALIASES = {
    "completed": "complete",
    "complete": "complete",
    "local": "local_only",
    "local_only": "local_only",
    "partial_complete": "partial",
    "partially_complete": "partial",
    "partial": "partial",
    "failed": "failed",
    "unsupported": "unsupported",
}
_SEVERITY_ALIASES = {
    "critical": "error",
    "fatal": "error",
    # ``high`` belongs to the confidence field in the inspection schema.  Some
    # models still place it in severity; treating that slip as a hard error
    # turns ordinary risk advice into a red program failure.
    "high": "warning",
    "error": "error",
    "warn": "warning",
    "warning": "warning",
    "medium": "warning",
    "notice": "info",
    "information": "info",
    "info": "info",
    "low": "info",
}
_CONFIDENCE_ALIASES = {
    "high": "high",
    "medium": "medium",
    "med": "medium",
    "low": "low",
}
_RESOLUTION_ALIASES = {
    "open": "open",
    "unresolved": "open",
    "resolved": "resolved",
    "fixed": "resolved",
    "still_present": "still_present",
    "present": "still_present",
    "needs_review": "needs_review",
    "pending_review": "needs_review",
    "not_applicable": "not_applicable",
}

_JSON_PATH_TOKEN_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")
_DEVICE_TOKEN_RE = re.compile(
    r"(?<![A-Z0-9_])(?:X|Y|M|SM|D|SD|T|C|S)\d+(?![A-Z0-9_])",
    re.IGNORECASE,
)
_SAFETY_RE = re.compile(
    r"急停|安全门|限位|安全回路|安全继电器|emergency\s*stop|e[- ]?stop|"
    r"safety\s*(?:door|gate|relay|circuit)|limit\s*switch",
    re.IGNORECASE,
)
_AI_EXTERNAL_USE_ASSUMPTION_RE = re.compile(
    r"未(?:找到|发现)?.{0,12}(?:读取|读出|引用|使用)|"
    r"没有.{0,12}(?:读取|读出|复位)|未(?:找到|发现)?.{0,12}(?:RST|复位)|"
    r"not\s+(?:read|used|referenced)|no\s+(?:read|reader|reset)|"
    r"without\s+(?:a\s+)?reset|unused\s+(?:result|value)",
    re.IGNORECASE,
)
_AI_SET_RST_CONFLICT_RE = re.compile(
    r"(?:SET.{0,30}RST|RST.{0,30}SET).{0,40}(?:重复|多处|多重|冲突|"
    r"duplicate|multiple|conflict|writer)|"
    r"(?:重复|多处|多重|冲突|duplicate|multiple|conflict|writer).{0,40}"
    r"(?:SET.{0,30}RST|RST.{0,30}SET)",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _clean_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip() or default


def _string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    result = []
    seen = set()
    for item in value:
        text = _clean_text(item)
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _int_list(value: Any) -> List[int]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    result = []
    for item in value:
        # bool is an int subclass, but never a valid rung id.
        if isinstance(item, bool):
            continue
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number not in result:
            result.append(number)
    return result


def _strict_bool(value: Any) -> bool:
    """Only accept a JSON boolean.

    Treating strings such as ``"yes"`` as true made malformed model output
    accidentally authorize fixes.  At this trust boundary an invalid boolean
    is therefore always false.
    """

    return value if isinstance(value, bool) else False


def _enum(value: Any, aliases: Mapping[str, str], default: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    return aliases.get(normalized, default)


def normalize_plc_model(value: Any) -> str:
    model = str(value or "FX3U").strip().upper()
    return model if model in {"FX3U", "FX5U"} else "FX3U"


def hash_ladder_json(data: Any) -> str:
    """Return a SHA-256 hash of canonical ladder JSON.

    Whitespace and object key order never change the result.  A string is
    interpreted as JSON rather than being hashed verbatim.
    """

    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as error:
            raise ValueError("ladder JSON string is invalid") from error
    canonical = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class Finding:
    finding_id: str
    source: str = "local"
    severity: str = "info"
    category: str = "review"
    title: str = ""
    message: str = ""
    evidence: List[str] = field(default_factory=list)
    rung_ids: List[int] = field(default_factory=list)
    json_paths: List[str] = field(default_factory=list)
    addresses: List[str] = field(default_factory=list)
    suggestion: str = ""
    fixable: bool = False
    fix_instruction: str = ""
    confidence: str = "medium"
    resolution_status: str = "open"
    safety_related: bool = False
    code: str = ""
    network_refs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InspectionReport:
    report_id: str
    report_type: str = "program_review"
    trigger: str = "manual"
    depth: str = "basic"
    base_version_id: Optional[str] = None
    base_json_hash: str = ""
    plc_model: str = "FX3U"
    status: str = "local_only"
    summary: str = ""
    findings: List[Dict[str, Any]] = field(default_factory=list)
    online_checks: List[Dict[str, Any]] = field(default_factory=list)
    fix_history: List[Dict[str, Any]] = field(default_factory=list)
    request: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)
    counts: Dict[str, int] = field(default_factory=dict)
    # ``base`` and ``execution`` mirror the flat binding/status fields for the
    # report card and older in-progress integrations.  The flat fields remain
    # canonical and are what the report index uses.
    base: Dict[str, Any] = field(default_factory=dict)
    execution: Dict[str, Any] = field(default_factory=dict)
    schema_version: int = REPORT_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _base_context(base_json: Any) -> Tuple[set, set]:
    rung_ids = set()
    addresses = set()
    if not isinstance(base_json, dict):
        return rung_ids, addresses
    for rung in base_json.get("rungs", []) or []:
        if isinstance(rung, dict) and isinstance(rung.get("rung_id"), int):
            rung_ids.add(rung["rung_id"])
    for address in (base_json.get("device_comments") or {}):
        if isinstance(address, str):
            addresses.add(address.upper())
    text = json.dumps(base_json, ensure_ascii=False)
    addresses.update(match.group(0).upper() for match in _DEVICE_TOKEN_RE.finditer(text))
    return rung_ids, addresses


def _writer_kinds(base_json: Any, address: str) -> set:
    """Return the explicit COIL/SET/RST writer kinds for one Y/M address."""

    target = str(address or "").strip().upper()
    kinds = set()
    if not target or not isinstance(base_json, Mapping):
        return kinds
    for rung in base_json.get("rungs", []) or []:
        if not isinstance(rung, Mapping):
            continue
        for branch in rung.get("branches", []) or []:
            if not isinstance(branch, Mapping):
                continue
            for output in branch.get("outputs", []) or []:
                if not isinstance(output, Mapping):
                    continue
                output_type = str(output.get("type", "")).upper()
                if output_type == "COIL" and str(output.get("address", "")).upper() == target:
                    kinds.add("COIL")
                    continue
                if output_type not in {"APP_INSTR", "BLOCK_OUTPUT"}:
                    continue
                opcode = str(output.get("opcode", "")).upper()
                operands = output.get("operands", []) or []
                if opcode in {"SET", "RST"} and operands:
                    if str(operands[0]).strip().upper() == target:
                        kinds.add(opcode)
    return kinds


def _json_path_exists(data: Any, path: str) -> bool:
    if path == "$":
        return True
    if not isinstance(path, str) or not path.startswith("$"):
        return False
    cursor = data
    position = 1
    for match in _JSON_PATH_TOKEN_RE.finditer(path, position):
        if match.start() != position:
            return False
        key, raw_index = match.groups()
        if key is not None:
            if not isinstance(cursor, dict) or key not in cursor:
                return False
            cursor = cursor[key]
        else:
            index = int(raw_index)
            if not isinstance(cursor, list) or index >= len(cursor):
                return False
            cursor = cursor[index]
        position = match.end()
    return position == len(path)


def _stable_finding_id(payload: Mapping[str, Any]) -> str:
    semantic = {
        "category": payload.get("category", "review"),
        "message": re.sub(r"\s+", " ", payload.get("message", "")).strip().lower(),
        "rung_ids": payload.get("rung_ids", []),
        "json_paths": payload.get("json_paths", []),
        "addresses": payload.get("addresses", []),
    }
    digest = hashlib.sha256(
        json.dumps(
            semantic,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    return "finding_" + digest


def normalize_finding(
    payload: Any,
    base_json: Any = None,
    default_source: str = "local",
) -> Dict[str, Any]:
    """Normalize an untrusted local, legacy or AI finding."""

    if hasattr(payload, "to_dict"):
        payload = payload.to_dict()
    elif hasattr(payload, "__dataclass_fields__"):
        payload = asdict(payload)
    if not isinstance(payload, Mapping):
        payload = {"message": _clean_text(payload)}

    ai_boundary = default_source == "ai"
    source = str(payload.get("source") or default_source).strip().lower()
    if ai_boundary:
        # The candidate is untrusted AI output even if it claims to be local.
        source = "ai"
    if source not in FINDING_SOURCES:
        source = default_source if default_source in FINDING_SOURCES else "local"
    severity = _enum(payload.get("severity"), _SEVERITY_ALIASES, "info")
    if ai_boundary and severity == "error":
        # Red errors are reserved for deterministic local hard validation.
        severity = "warning"
    category = _clean_text(payload.get("category"), "review").lower().replace(" ", "_")
    title = _clean_text(payload.get("title"))
    message = _clean_text(
        payload.get("message") or payload.get("description") or payload.get("summary")
    )
    recommendation_value = (
        payload.get("suggestion")
        or payload.get("recommendation")
        or payload.get("recommended_change")
        or payload.get("recommended_changes")
    )
    if isinstance(recommendation_value, (list, tuple, set)):
        suggestion = "；".join(_string_list(recommendation_value))
    else:
        suggestion = _clean_text(recommendation_value)

    raw_evidence = payload.get("evidence")
    evidence = _string_list(raw_evidence)
    if not evidence and payload.get("reason"):
        evidence = _string_list(payload.get("reason"))

    raw_rungs = payload.get("rung_ids")
    if raw_rungs is None:
        raw_rungs = payload.get("related_rungs", payload.get("rung_id"))
    rung_ids = _int_list(raw_rungs)
    raw_paths = payload.get("json_paths")
    if raw_paths is None:
        raw_paths = payload.get("json_path")
    json_paths = _string_list(raw_paths)
    raw_addresses = payload.get("addresses")
    if raw_addresses is None:
        raw_addresses = payload.get("address")
    addresses = [item.upper() for item in _string_list(raw_addresses)]
    code = _clean_text(payload.get("code")).upper()
    network_refs = _string_list(payload.get("network_refs"))

    # The deep-inspection schema carries structured evidence locations.  Merge
    # them into the same canonical location lists used by local findings while
    # retaining a readable JSON rendering in ``evidence``.
    evidence_items = raw_evidence if isinstance(raw_evidence, list) else [raw_evidence]
    for item in evidence_items:
        if not isinstance(item, Mapping):
            continue
        evidence_rungs = item.get("rung_ids", item.get("rung_id"))
        for rung_id in _int_list(evidence_rungs):
            if rung_id not in rung_ids:
                rung_ids.append(rung_id)
        evidence_paths = item.get("json_paths", item.get("json_path"))
        for json_path in _string_list(evidence_paths):
            if json_path not in json_paths:
                json_paths.append(json_path)
        evidence_addresses = item.get("addresses", item.get("address"))
        for address in _string_list(evidence_addresses):
            address = address.upper()
            if address not in addresses:
                addresses.append(address)

    valid_rungs, valid_addresses = _base_context(base_json)
    if isinstance(base_json, dict):
        rung_ids = [item for item in rung_ids if item in valid_rungs]
        json_paths = [item for item in json_paths if _json_path_exists(base_json, item)]
        addresses = [item for item in addresses if item in valid_addresses]

    confidence = _enum(payload.get("confidence"), _CONFIDENCE_ALIASES, "medium")
    resolution = _enum(
        payload.get("resolution_status"), _RESOLUTION_ALIASES, "open"
    )
    fix_instruction = _clean_text(
        payload.get("fix_instruction") or payload.get("repair_instruction")
    )

    safety_text = " ".join(
        [category, title, message, suggestion, fix_instruction] + evidence
    )
    safety_related = _strict_bool(payload.get("safety_related")) or bool(
        _SAFETY_RE.search(safety_text)
    )
    pure_set_rst_claim = False
    if ai_boundary and _AI_SET_RST_CONFLICT_RE.search(safety_text) and addresses:
        writer_sets = [_writer_kinds(base_json, address) for address in addresses]
        pure_set_rst_claim = bool(writer_sets) and all(
            kinds and kinds <= {"SET", "RST"} for kinds in writer_sets
        )
    unsupported_design_assumption = bool(
        ai_boundary
        and (
            _AI_EXTERNAL_USE_ASSUMPTION_RE.search(safety_text)
            or pure_set_rst_claim
        )
    )
    has_location = bool(rung_ids or json_paths or addresses)
    if ai_boundary and isinstance(base_json, dict) and not has_location:
        # A code diagnosis that cannot be anchored to the inspected version is
        # a low-confidence note, not a selectable or high-severity defect.
        severity = "info"
        confidence = "low"
    if unsupported_design_assumption:
        # Absence of an in-ladder reader/reset and pure SET/RST distribution
        # both have common external/HMI explanations.  Without a confirmed
        # program-owned contract these are notes, never actionable defects.
        severity = "info"
        confidence = "low"
    evidence_complete = bool(evidence and has_location)
    fixable = (
        _strict_bool(
            payload.get(
                "fixable", payload.get("auto_fixable", payload.get("needs_fix"))
            )
        )
        and evidence_complete
        and bool(fix_instruction)
        and not safety_related
        and not unsupported_design_assumption
    )

    normalized = Finding(
        finding_id="",
        source=source,
        severity=severity,
        category=category,
        title=title,
        message=message,
        evidence=evidence,
        rung_ids=rung_ids,
        json_paths=json_paths,
        addresses=addresses,
        suggestion=suggestion,
        fixable=fixable,
        fix_instruction=fix_instruction if fixable else "",
        confidence=confidence,
        resolution_status=resolution,
        safety_related=safety_related,
        code=code,
        network_refs=network_refs,
    ).to_dict()
    supplied_id = _clean_text(payload.get("finding_id") or payload.get("id"))
    normalized["finding_id"] = supplied_id or _stable_finding_id(normalized)
    return normalized


def _normalize_online_checks(value: Any, base_json: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    _, valid_addresses = _base_context(base_json)
    result = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            item = {"instruction": item}
        if not isinstance(item, Mapping):
            continue
        address = _clean_text(item.get("address")).upper()
        if isinstance(base_json, dict) and address and address not in valid_addresses:
            address = ""
        instruction = _clean_text(
            item.get("instruction") or item.get("check") or item.get("description")
        )
        if not instruction:
            condition = _clean_text(item.get("condition"))
            reason = _clean_text(item.get("reason"))
            if condition and reason:
                instruction = str(tr("在{condition}时观察；{reason}", condition=condition, reason=reason))
            else:
                instruction = condition or reason
        if not address and not instruction:
            continue
        status = str(item.get("status") or "pending").strip().lower()
        if status not in {"pending", "confirmed", "mismatch", "not_observed"}:
            status = "pending"
        result.append(
            {
                "check_id": _clean_text(item.get("check_id")) or "check_%04d" % (index + 1),
                "address": address,
                "instruction": instruction,
                "expected": _clean_text(item.get("expected")),
                "observed": _clean_text(item.get("observed")),
                "status": status,
            }
        )
    return result


def _normalize_history(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        result.append({str(key): val for key, val in item.items()})
    return result


def _finding_counts(findings: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    counts = {"error": 0, "warning": 0, "info": 0}
    for finding in findings:
        severity = finding.get("severity")
        if severity in counts:
            counts[severity] += 1
    return counts


def _normalize_execution(value: Any, status: str, depth: str) -> Dict[str, Any]:
    execution = dict(value) if isinstance(value, Mapping) else {}
    local_value = execution.get("local")
    local = dict(local_value) if isinstance(local_value, Mapping) else {}
    ai_value = execution.get("ai")
    ai = dict(ai_value) if isinstance(ai_value, Mapping) else {}
    local_status = str(local.get("status") or "complete").strip().lower()
    if local_status not in {"complete", "failed"}:
        local_status = "complete"
    ai_default = "not_requested" if depth == "basic" else "pending"
    ai_status = str(ai.get("status") or ai_default).strip().lower()
    return {
        "status": str(execution.get("status") or status).strip().lower(),
        "local": {
            "status": local_status,
            "error": _clean_text(local.get("error")),
        },
        "ai": {
            "status": ai_status,
            "error": _clean_text(ai.get("error")),
        },
    }


def normalize_inspection_report(
    payload: Any,
    base_json: Any = None,
    defaults: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a strict InspectionReport v1 dictionary.

    ``defaults`` supplies trusted task metadata (selected version, model,
    trigger) and takes precedence over untrusted payload values.  Conflicting
    version/hash/model claims are rejected instead of being silently rebound.
    """

    if hasattr(payload, "to_dict"):
        payload = payload.to_dict()
    elif hasattr(payload, "__dataclass_fields__"):
        payload = asdict(payload)
    if not isinstance(payload, Mapping):
        payload = {}
    defaults = dict(defaults or {})

    def selected(name: str, fallback: Any = None) -> Any:
        trusted_names = {
            "report_type", "trigger", "depth", "base_version_id",
            "base_json_hash", "plc_model", "request",
        }
        if name in trusted_names and name in defaults and defaults[name] is not None:
            return defaults[name]
        value = payload.get(name)
        return fallback if value is None else value

    base_value = payload.get("base")
    base_value = dict(base_value) if isinstance(base_value, Mapping) else {}
    claimed_version = _clean_text(
        payload.get("base_version_id") or base_value.get("version_id")
    )
    trusted_version = _clean_text(defaults.get("base_version_id"))
    if claimed_version and trusted_version and claimed_version != trusted_version:
        raise ValueError("inspection report refers to a different base version")
    claimed_model = payload.get("plc_model") or base_value.get("plc_model")
    if claimed_model and defaults.get("plc_model"):
        if normalize_plc_model(claimed_model) != normalize_plc_model(defaults["plc_model"]):
            raise ValueError("inspection report refers to a different PLC model")

    report_type = _enum(
        selected("report_type", "program_review"),
        _REPORT_TYPE_ALIASES,
        "program_review",
    )
    trigger = _enum(selected("trigger", "manual"), _TRIGGER_ALIASES, "manual")
    depth = str(selected("depth", "basic")).strip().lower()
    if depth not in DEPTHS:
        depth = "basic"
    default_status = "complete" if depth == "deep" else "local_only"
    status = _enum(
        selected("status", default_status), _STATUS_ALIASES, default_status
    )
    plc_model = normalize_plc_model(selected("plc_model", "FX3U"))
    claimed_hash = _clean_text(
        payload.get("base_json_hash")
        or base_value.get("json_sha256")
        or base_value.get("json_hash")
    )
    actual_hash = hash_ladder_json(base_json) if base_json is not None else ""
    trusted_hash = _clean_text(defaults.get("base_json_hash"))
    if claimed_hash and actual_hash and claimed_hash != actual_hash:
        raise ValueError("inspection reports refer to different ladder JSON hashes")
    if claimed_hash and trusted_hash and claimed_hash != trusted_hash:
        raise ValueError("inspection reports refer to different ladder JSON hashes")
    base_hash = actual_hash or trusted_hash or claimed_hash
    base_version_id = _clean_text(
        selected("base_version_id") or base_value.get("version_id")
    ) or None

    # ``deep`` reports can contain both deterministic local findings and AI
    # findings after merging.  Use the trusted caller marker to identify the
    # untrusted AI boundary instead of treating every deep finding as AI.
    default_source = "ai" if defaults.get("origin") == "ai" else "local"
    findings_value = payload.get("findings")
    if findings_value is None:
        findings_value = payload.get("local_findings", [])
    if not isinstance(findings_value, list):
        findings_value = [findings_value] if findings_value else []
    findings = [
        normalize_finding(item, base_json=base_json, default_source=default_source)
        for item in findings_value
    ]

    # Legacy debug reports are converted into one conservative, non-fixable
    # finding instead of silently granting the report-wide needs_fix flag.
    if not findings and any(
        key in payload
        for key in ("possible_causes", "recommended_changes", "related_rungs", "needs_fix")
    ):
        possible_causes = _string_list(payload.get("possible_causes"))
        changes = _string_list(payload.get("recommended_changes"))
        legacy_finding = {
            "source": "legacy",
            "severity": "warning" if possible_causes else "info",
            "category": "legacy_debug",
            "message": "; ".join(possible_causes) or _clean_text(payload.get("summary")),
            "evidence": possible_causes,
            "related_rungs": payload.get("related_rungs", []),
            "suggestion": "; ".join(changes),
            "fixable": False,
            "fix_instruction": "",
            "confidence": "low",
        }
        findings.append(
            normalize_finding(legacy_finding, base_json=base_json, default_source="legacy")
        )

    report = InspectionReport(
        report_id=_clean_text(selected("report_id") or selected("id"))
        or "report_" + uuid.uuid4().hex,
        report_type=report_type,
        trigger=trigger,
        depth=depth,
        base_version_id=base_version_id,
        base_json_hash=base_hash,
        plc_model=plc_model,
        status=status,
        summary=_clean_text(payload.get("summary")),
        findings=findings,
        online_checks=_normalize_online_checks(
            payload.get("online_checks", payload.get("verification_steps")),
            base_json,
        ),
        fix_history=_normalize_history(payload.get("fix_history", payload.get("fix_records"))),
        request=(
            dict(selected("request", {}))
            if isinstance(selected("request", {}), Mapping)
            else {"text": _clean_text(selected("request"))}
        ),
        created_at=_clean_text(selected("created_at")) or _utc_now(),
        counts=_finding_counts(findings),
        base={
            "version_id": base_version_id,
            "json_sha256": base_hash,
            "plc_model": plc_model,
        },
        execution=_normalize_execution(payload.get("execution"), status, depth),
    )
    return report.to_dict()


def _dedupe_key(finding: Mapping[str, Any]) -> Tuple[Any, ...]:
    category = finding.get("category", "review")
    locations = (
        tuple(sorted(set(finding.get("addresses") or []))),
        tuple(sorted(set(finding.get("rung_ids") or []))),
        tuple(sorted(set(finding.get("json_paths") or []))),
    )
    if any(locations):
        return (category,) + locations
    message = re.sub(r"\s+", " ", finding.get("message", "")).strip().lower()
    return category, message


def _merge_finding(left: Mapping[str, Any], right: Mapping[str, Any]) -> Dict[str, Any]:
    severity_rank = {"info": 0, "warning": 1, "error": 2}
    preferred = right if severity_rank.get(right.get("severity"), 0) >= severity_rank.get(
        left.get("severity"), 0
    ) else left
    merged = dict(left)
    merged["severity"] = preferred.get("severity", "info")
    for name in ("evidence", "rung_ids", "json_paths", "addresses"):
        values = []
        for item in list(left.get(name) or []) + list(right.get(name) or []):
            if item not in values:
                values.append(item)
        merged[name] = values
    for name in ("title", "message", "suggestion"):
        if right.get(name):
            merged[name] = right[name]
    merged["confidence"] = right.get("confidence") or left.get("confidence", "medium")
    merged["safety_related"] = bool(
        left.get("safety_related") or right.get("safety_related")
    )
    # A merged fix remains selectable only when the deep result explicitly
    # authorizes it and the conservative normalization already accepted it.
    merged["fixable"] = bool(right.get("fixable")) and not merged["safety_related"]
    merged["fix_instruction"] = right.get("fix_instruction", "") if merged["fixable"] else ""
    return merged


def merge_inspection_reports(local: Any, ai: Any) -> Dict[str, Any]:
    """Merge local and deep reports while preserving version/hash binding."""

    local_report = normalize_inspection_report(local)
    ai_report = normalize_inspection_report(
        ai,
        defaults={
            "report_type": local_report["report_type"],
            "trigger": local_report["trigger"],
            "depth": "deep",
            "base_version_id": local_report["base_version_id"],
            "base_json_hash": local_report["base_json_hash"],
            "plc_model": local_report["plc_model"],
            "origin": "ai",
        },
    )
    left_hash = local_report.get("base_json_hash")
    right_hash = ai_report.get("base_json_hash")
    if left_hash and right_hash and left_hash != right_hash:
        raise ValueError("inspection reports refer to different ladder JSON hashes")
    left_version = local_report.get("base_version_id")
    right_version = ai_report.get("base_version_id")
    if left_version and right_version and left_version != right_version:
        raise ValueError("inspection reports refer to different base versions")

    merged_findings = []
    positions = {}
    id_positions = {}
    for finding in list(local_report["findings"]) + list(ai_report["findings"]):
        finding_id = finding.get("finding_id") or finding.get("id")
        key = _dedupe_key(finding)
        index = id_positions.get(finding_id) if finding_id else None
        if index is None:
            index = positions.get(key)
        if index is not None:
            merged_findings[index] = _merge_finding(merged_findings[index], finding)
        else:
            index = len(merged_findings)
            positions[key] = index
            merged_findings.append(dict(finding))
        if finding_id:
            id_positions[finding_id] = index

    ai_status = ai_report.get("status")
    if ai_status == "complete":
        status = "complete"
    elif ai_status in {"failed", "partial", "local_only"}:
        status = "partial"
    else:
        status = ai_status or "partial"
    summary = ai_report.get("summary") or local_report.get("summary")
    online_checks = []
    seen_checks = set()
    for check in list(local_report["online_checks"]) + list(ai_report["online_checks"]):
        key = (check.get("address"), check.get("instruction"))
        if key not in seen_checks:
            online_checks.append(check)
            seen_checks.add(key)

    return normalize_inspection_report(
        {
            "report_id": local_report["report_id"],
            "report_type": local_report["report_type"],
            "trigger": local_report["trigger"],
            "depth": "deep",
            "base_version_id": left_version or right_version,
            "base_json_hash": left_hash or right_hash,
            "plc_model": local_report["plc_model"],
            "status": status,
            "summary": summary,
            "findings": merged_findings,
            "online_checks": online_checks,
            "fix_history": list(local_report["fix_history"]) + list(ai_report["fix_history"]),
            "request": ai_report["request"] or local_report["request"],
            "created_at": local_report["created_at"],
            "execution": {
                "status": status,
                "local": {"status": "complete", "error": ""},
                "ai": {
                    "status": "complete" if status == "complete" else "failed",
                    "error": ai_report.get("execution", {}).get("ai", {}).get("error", ""),
                },
            },
        }
    )


__all__ = [
    "Finding",
    "InspectionReport",
    "REPORT_SCHEMA_VERSION",
    "hash_ladder_json",
    "merge_inspection_reports",
    "normalize_finding",
    "normalize_inspection_report",
    "normalize_plc_model",
]
