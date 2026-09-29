"""Ground Agent-A execution-intent claims before they become PLC requirements.

Free-form language understanding belongs to Agent A.  Python Core does not
classify arbitrary prose with an ever-growing keyword list.  It verifies that
each claim is structurally valid and grounded in exact user evidence, then
projects the claim into the small execution-semantic vocabulary used by PLC IR.

A narrow deterministic fast path remains for explicit notation such as
"X0 上升沿", "0 -> 1", "首扫", and an explicitly dimensioned cycle/pulse.
That grammar is intentionally not a general natural-language classifier.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence

from plc.device_identity import DEVICE_TOKEN_RE, canonical_device


TRIGGER_KINDS = frozenset({"level", "transition", "first_scan", "cyclic", "interrupt", "clear"})
EFFECT_KINDS = frozenset({"while_true", "one_shot", "event", "state_change", "unspecified"})
REARM_VALUES = frozenset({"required", "not_required", "unspecified"})

_CLAUSE_END = re.compile(r"[。！？!?；;\n]+")
_RISING_WORD = re.compile(r"上升沿|正沿|rising\s*edge|0\s*(?:→|->)\s*1", re.I)
_FALLING_WORD = re.compile(r"下降沿|负沿|falling\s*edge|1\s*(?:→|->)\s*0", re.I)
_RISING_TRANSITION = re.compile(
    r"(?:(?:从|由)\s*)?(?:0|OFF|FALSE|断开)"
    r"\s*(?:变(?:成|为)|切换(?:到|为)|转(?:成|为)|到|至|→|->)\s*"
    r"(?:1|ON|TRUE|接通)",
    re.I,
)
_FALLING_TRANSITION = re.compile(
    r"(?:(?:从|由)\s*)?(?:1|ON|TRUE|接通)"
    r"\s*(?:变(?:成|为)|切换(?:到|为)|转(?:成|为)|到|至|→|->)\s*"
    r"(?:0|OFF|FALSE|断开)",
    re.I,
)
_FIRST_SCAN = re.compile(r"首(?:次)?扫描|首扫|first\s*scan", re.I)
_INTERRUPT = re.compile(r"中断(?:程序|任务|输入|处理|事件)?|interrupt", re.I)
_LEVEL = re.compile(r"(?:高|低)?电平|\blevel\b", re.I)
_PERIOD = re.compile(
    r"(?:每隔|周期(?:为|是)?|间隔(?:为|是)?)\s*"
    r"(\d+(?:\.\d+)?)\s*(ms|毫秒|秒|s|分钟|min)",
    re.I,
)
_CYCLIC = re.compile(r"周期执行|固定周期|cyclic|periodic", re.I)
_PULSE_WIDTH = re.compile(
    r"(?:输入\s*)?(?:脉冲宽度|脉冲宽|脉宽|pulse\s*width)"
    r"\s*(?:为|是|=|约|大约|最短|最小|至少)?\s*"
    r"(\d+(?:\.\d+)?)\s*(us|μs|µs|微秒|ms|毫秒|s|秒)",
    re.I,
)


def _fold_ws(value):
    return " ".join(str(value or "").split())


def _canonical_devices(values):
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    result = []
    for value in values:
        token = canonical_device(str(value or "").strip().upper())
        if isinstance(token, str) and DEVICE_TOKEN_RE.fullmatch(token) and token not in result:
            result.append(token)
    return result


def _evidence_devices(evidence):
    found = []
    for text in evidence:
        for match in DEVICE_TOKEN_RE.finditer(str(text or "")):
            token = canonical_device(match.group(0).upper())
            if token not in found:
                found.append(token)
    return found


def _alias_key(value):
    return re.sub(r"[\W_]+", "", str(value or ""), flags=re.UNICODE).casefold()


def _confirmed_device_aliases(confirmed_spec):
    """Return only unique confirmed human labels -> canonical device identities.

    This is evidence grounding, not fuzzy intent inference.  A label/name must
    already be part of the confirmed specification and must identify exactly one
    device.  Generic roles and model-authored summaries are deliberately ignored.
    """
    if not isinstance(confirmed_spec, Mapping):
        return {}
    owners = {}
    for row in confirmed_spec.get("io_table", []) or []:
        if not isinstance(row, Mapping):
            continue
        device = canonical_device(str(row.get("address") or "").strip().upper())
        if not isinstance(device, str) or not DEVICE_TOKEN_RE.fullmatch(device):
            continue
        for field in ("label", "description"):
            alias = str(row.get(field) or "").strip()
            key = _alias_key(alias)
            if len(key) >= 2:
                owners.setdefault(key, set()).add(device)
    for row in confirmed_spec.get("io_bindings", []) or []:
        if not isinstance(row, Mapping):
            continue
        device = canonical_device(str(row.get("address") or "").strip().upper())
        if not isinstance(device, str) or not DEVICE_TOKEN_RE.fullmatch(device):
            continue
        for field in ("label", "name"):
            alias = str(row.get(field) or "").strip()
            key = _alias_key(alias)
            if len(key) >= 2:
                owners.setdefault(key, set()).add(device)
    return {
        alias: next(iter(devices))
        for alias, devices in owners.items()
        if len(devices) == 1
    }


def _device_grounded(device, evidence, aliases):
    if device in set(_evidence_devices(evidence)):
        return True
    joined = [_alias_key(item) for item in evidence]
    return any(
        owner == device and alias and any(alias in text for text in joined)
        for alias, owner in aliases.items()
    )


def _state(value):
    token = str(value or "").strip().casefold()
    if token in {"0", "off", "false", "low", "断开", "低电平"}:
        return 0
    if token in {"1", "on", "true", "high", "接通", "高电平"}:
        return 1
    return None


def execution_intent_claim_violations(value, path="$.execution_intent_claims"):
    """Validate only the Agent-A claim protocol shape, never sentence meaning."""
    if value is None:
        return []
    if not isinstance(value, list):
        return [path + ": expected an array"]
    violations = []
    for index, claim in enumerate(value):
        root = f"{path}[{index}]"
        if not isinstance(claim, Mapping):
            violations.append(root + ": claim must be an object")
            continue
        if "semantic" in claim or "execution_semantics" in claim:
            violations.append(root + ": Agent A must emit a trigger frame, not final execution_semantics")
        trigger = claim.get("trigger")
        if not isinstance(trigger, Mapping):
            violations.append(root + ".trigger: object is required")
            continue
        kind = str(trigger.get("kind") or "").strip().casefold()
        if kind not in TRIGGER_KINDS:
            violations.append(root + ".trigger.kind: unsupported trigger kind")
            continue
        source_devices = trigger.get("source_devices", [])
        if not isinstance(source_devices, list) or any(not isinstance(item, str) for item in source_devices):
            violations.append(root + ".trigger.source_devices: expected a string array")
        if kind in {"level", "transition", "interrupt", "clear"} and not source_devices:
            violations.append(root + ".trigger.source_devices: this trigger kind requires a source device")
        if kind == "transition":
            if _state(trigger.get("from")) is None or _state(trigger.get("to")) is None:
                violations.append(root + ".trigger: transition requires binary from/to states")
        if kind == "level" and _state(trigger.get("value")) is None:
            violations.append(root + ".trigger.value: level requires a binary state")
        if kind == "cyclic" and trigger.get("period_ms") is not None:
            try:
                if float(trigger.get("period_ms")) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                violations.append(root + ".trigger.period_ms: expected a positive number")
        effect = claim.get("effect")
        if effect is not None:
            if not isinstance(effect, Mapping):
                violations.append(root + ".effect: expected an object")
            else:
                effect_kind = str(effect.get("kind") or "unspecified").strip().casefold()
                if effect_kind not in EFFECT_KINDS:
                    violations.append(root + ".effect.kind: unsupported effect kind")
                devices = effect.get("devices", [])
                if not isinstance(devices, list) or any(not isinstance(item, str) for item in devices):
                    violations.append(root + ".effect.devices: expected a string array")
        rearm = str(claim.get("rearm") or "unspecified").strip().casefold()
        if rearm not in REARM_VALUES:
            violations.append(root + ".rearm: expected required, not_required, or unspecified")
        evidence = claim.get("evidence")
        if (
            not isinstance(evidence, list)
            or not evidence
            or any(not isinstance(item, str) or not item.strip() for item in evidence)
        ):
            violations.append(root + ".evidence: one or more exact user-text spans are required")
    return violations


def _claim_id(frame):
    payload = json.dumps(frame, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "EC-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def compile_execution_intent_claims(
    value, user_text, *, source="agent_a_claim", confirmed_spec=None
):
    """Ground model claims against the current user request and compile safe frames.

    A claim is accepted only when its evidence is copied from the user request
    (whitespace-normalized) and every claimed device occurs in that evidence.
    The semantic relation itself remains a reviewed Agent-A interpretation; it
    becomes authoritative only if the resulting specification is confirmed.
    """
    shape = execution_intent_claim_violations(value)
    if shape:
        return {"requirements": [], "accepted": [], "rejected": [
            {"reason": "invalid_claim_shape", "detail": item} for item in shape
        ], "touched_devices": []}

    source_text = _fold_ws(user_text)
    confirmed_aliases = _confirmed_device_aliases(confirmed_spec)
    accepted, rejected, requirements, touched = [], [], [], []
    for index, raw in enumerate(value or []):
        claim = copy.deepcopy(dict(raw))
        evidence = [_fold_ws(item) for item in claim.get("evidence") or []]
        if any(not quote or quote not in source_text for quote in evidence):
            rejected.append({"index": index, "reason": "evidence_not_in_current_request"})
            continue

        trigger = dict(claim.get("trigger") or {})
        trigger_kind = str(trigger.get("kind") or "").strip().casefold()
        source_devices = _canonical_devices(trigger.get("source_devices") or [])
        effect = dict(claim.get("effect") or {})
        effect_devices = _canonical_devices(effect.get("devices") or [])
        if trigger_kind in {"level", "transition", "interrupt", "clear"} and not source_devices:
            rejected.append({"index": index, "reason": "invalid_source_device"})
            continue
        if any(
            not _device_grounded(device, evidence, confirmed_aliases)
            for device in [*source_devices, *effect_devices]
        ):
            rejected.append({"index": index, "reason": "claimed_device_not_in_evidence"})
            continue

        rearm = str(claim.get("rearm") or "unspecified").strip().casefold()
        if trigger_kind == "level" and rearm == "required":
            rejected.append({"index": index, "reason": "level_cannot_require_rearm"})
            continue

        semantic = None
        if trigger_kind == "transition":
            before, after = _state(trigger.get("from")), _state(trigger.get("to"))
            if (before, after) == (0, 1):
                semantic = "RISING_EDGE"
            elif (before, after) == (1, 0):
                semantic = "FALLING_EDGE"
            else:
                rejected.append({"index": index, "reason": "non_edge_transition"})
                continue
        elif trigger_kind == "level":
            semantic = "LEVEL"
        elif trigger_kind == "first_scan":
            semantic = "FIRST_SCAN"
        elif trigger_kind == "cyclic":
            semantic = "CYCLIC"
        elif trigger_kind == "interrupt":
            semantic = "INTERRUPT"
        elif trigger_kind == "clear":
            touched.extend(device for device in source_devices if device not in touched)
            accepted.append({
                "claim_id": _claim_id(claim),
                "trigger": {**trigger, "source_devices": source_devices},
                "effect": {**effect, "devices": effect_devices} if effect else {},
                "rearm": rearm,
                "evidence": evidence,
                "action": "clear",
            })
            continue

        frame = {
            "trigger": {**trigger, "kind": trigger_kind, "source_devices": source_devices},
            "effect": {
                **effect,
                "kind": str(effect.get("kind") or "unspecified").strip().casefold(),
                "devices": effect_devices,
            } if effect else {"kind": "unspecified", "devices": []},
            "rearm": rearm,
            "evidence": evidence,
        }
        claim_id = _claim_id(frame)
        accepted.append({"claim_id": claim_id, **frame})
        touched.extend(device for device in source_devices if device not in touched)
        requirement = {
            "semantic": semantic,
            "devices": source_devices,
            "effect_devices": effect_devices,
            "effect_kind": frame["effect"]["kind"],
            "rearm": rearm,
            "evidence": " | ".join(evidence)[:500],
            "source": source,
            "intent_id": claim_id,
            "strict": semantic in {"RISING_EDGE", "FALLING_EDGE", "FIRST_SCAN"},
        }
        period = trigger.get("period_ms")
        if semantic == "CYCLIC" and period is not None:
            requirement["period_ms"] = float(period)
        pulse = trigger.get("pulse_width_ms")
        if pulse is not None:
            try:
                pulse = float(pulse)
            except (TypeError, ValueError):
                pulse = None
            if pulse is not None and pulse > 0:
                requirement["pulse_width_ms"] = pulse
        requirements.append(requirement)

    return {
        "requirements": requirements,
        "accepted": accepted,
        "rejected": rejected,
        "touched_devices": touched,
    }


def _fragments(text):
    return [part.strip() for part in _CLAUSE_END.split(str(text or "")) if part.strip()]


def _nearest_device(fragment, marker):
    matches = list(DEVICE_TOKEN_RE.finditer(fragment))
    if not matches or marker is None:
        return []
    def distance(match):
        return max(marker.start() - match.end(), match.start() - marker.end(), 0)
    nearest = min(distance(match) for match in matches)
    if nearest > 24:
        return []
    return list(dict.fromkeys(
        canonical_device(match.group(0).upper())
        for match in matches
        if distance(match) == nearest
    ))


def _period_ms(fragment):
    match = _PERIOD.search(fragment)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit in {"秒", "s"}:
        value *= 1000.0
    elif unit in {"分钟", "min"}:
        value *= 60000.0
    return value


def _pulse_width_ms(fragment):
    match = _PULSE_WIDTH.search(fragment)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit in {"us", "μs", "µs", "微秒"}:
        value /= 1000.0
    elif unit in {"s", "秒"}:
        value *= 1000.0
    return value if value > 0 else None


def extract_explicit_execution_semantics(text, *, source="explicit_syntax"):
    """Extract only formal/unambiguous notation; never classify arbitrary prose."""
    result = []
    for fragment in _fragments(text):
        candidates = []
        rising = _RISING_TRANSITION.search(fragment) or _RISING_WORD.search(fragment)
        falling = _FALLING_TRANSITION.search(fragment) or _FALLING_WORD.search(fragment)
        first = _FIRST_SCAN.search(fragment)
        interrupt = _INTERRUPT.search(fragment)
        level = _LEVEL.search(fragment)
        period = _period_ms(fragment)
        cyclic = _CYCLIC.search(fragment)
        pulse = _pulse_width_ms(fragment)

        if rising:
            candidates.append(("RISING_EDGE", rising))
        if falling:
            candidates.append(("FALLING_EDGE", falling))
        if first:
            candidates.append(("FIRST_SCAN", first))
        if period is not None or cyclic:
            candidates.append(("CYCLIC", _PERIOD.search(fragment) or cyclic))
        if interrupt:
            candidates.append(("INTERRUPT", interrupt))
        if not candidates and level:
            candidates.append(("LEVEL", level))
        # A dimensioned physical input pulse is formal sampling evidence, but
        # only use it as an edge requirement when no polarity was already stated.
        if pulse is not None and not any(item[0] in {"RISING_EDGE", "FALLING_EDGE"} for item in candidates):
            x_devices = [
                canonical_device(match.group(0).upper())
                for match in DEVICE_TOKEN_RE.finditer(fragment)
                if canonical_device(match.group(0).upper()).startswith("X")
            ]
            if x_devices:
                candidates.append(("RISING_EDGE", _PULSE_WIDTH.search(fragment)))

        for semantic, marker in candidates:
            devices = [] if semantic in {"FIRST_SCAN", "CYCLIC"} else _nearest_device(fragment, marker)
            record = {
                "semantic": semantic,
                "devices": devices,
                "evidence": fragment[:500],
                "source": source,
                "strict": semantic in {"RISING_EDGE", "FALLING_EDGE", "FIRST_SCAN"},
            }
            if semantic == "CYCLIC" and period is not None:
                record["period_ms"] = period
            if pulse is not None and semantic in {"RISING_EDGE", "FALLING_EDGE", "INTERRUPT"}:
                record["pulse_width_ms"] = pulse
            result.append(record)
    return result


__all__ = [
    "EFFECT_KINDS",
    "REARM_VALUES",
    "TRIGGER_KINDS",
    "compile_execution_intent_claims",
    "execution_intent_claim_violations",
    "extract_explicit_execution_semantics",
]
