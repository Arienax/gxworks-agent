"""Pure I/O answer binding. Identity, not fuzzy prose, owns an address.

This is a legacy-input adapter, not a PLC intent inference engine. Explicit
bindings work for arbitrary machines/languages. The small aliases below migrate
old single-role questions; unmatched answers are preserved for the generator.
"""
from __future__ import annotations

import copy
import hashlib
import re
from plc.device_identity import canonical_device, canonical_io_rows
from plc.comments import device_purpose_label

_DEVICE = re.compile(r"(?<![A-Za-z0-9_])(?:SM|SD|[XYMTCSDVZ])\d+(?![A-Za-z0-9_])", re.I)
_ALIASES = {"start_input": ("start", "X"), "stop_input": ("stop", "X"),
            "output_coil": ("output", "Y"), "output_address": ("output", "Y")}
# Exact historical question IDs only. Keep the fallback identities previously
# persisted for these questions; do not merge arbitrary machine-specific roles.
_QUESTION_ALIASES = {
    "start_signal": ("start", "X"), "start_address": ("start", "X"),
    "stop_signal": ("stop", "X"), "stop_address": ("stop", "X"),
    "output_device": ("output", "Y"),
}
_IO_ATTRIBUTE_RE = re.compile(
    r"常[开闭閉]|normally\s+(?:open|closed)|\b(?:NO|NC)\b|上升沿|下降沿|rising|falling|edge",
    re.IGNORECASE,
)

_DECLARED_IO_LINE_RE = re.compile(
    r"^\s*(?:[-*•]\s*|\d+[.)、]\s*)?((?:SM|SD|[XYMTCSDVZ])\s*\d+)"
    r"\s*(?P<delimiter>[：:=]|为|是|is\s+)\s*(?P<purpose>.+?)\s*$",
    re.IGNORECASE,
)
_INPUT_QUALIFIER_RE = re.compile(
    r"[,，(（]\s*(?:按下|未按下|松开|释放|动作|未动作|常开|常闭|常開|常閉|normally\b|active\b)",
    re.IGNORECASE,
)
_DECLARATION_SEPARATOR_RE = re.compile(
    r"[,，、](?=\s*(?:SM|SD|[XYMTCSDVZ])\s*\d+(?![A-Za-z0-9_]))",
    re.IGNORECASE,
)
_STATE_NOT_PURPOSE_RE = re.compile(
    r"^(?:(?:ON|OFF|TRUE|FALSE)(?=$|[^A-Za-z0-9_])|"
    r"[+-]?\d+(?:[.,]\d+)?(?=$|[\s~～<>=+\-]|时|時))",
    re.IGNORECASE,
)
_DECLARATION_HEADING_RE = re.compile(r"^\s*(?:确认(?:如下现场规格|现场规格|如下)?|现场确认|I/O(?:\s*约定)?)\s*[：:]\s*", re.I)
_NAMED_INPUT_LEVEL_RE = re.compile(
    r"^\s*(X\s*\d+)\s*([^,，、;；：:=?？]{1,32}?)\s*=\s*([01])\s*$", re.I
)
_LEVEL_PURPOSE_RE = re.compile(r"^([01])\s*(?:为|表示)?\s*([^\d\s,，、;；~～<>=+\-].*)$")
_GROUPED_INPUT_LEVEL_RE = re.compile(
    r"^\s*(?P<addresses>X\s*\d+(?:\s*[/／]\s*X\s*\d+)+)\s*=\s*"
    r"(?P<level>[01])\s*分别\s*(?P<purpose>.+?)\s*$", re.I,
)
_ADJACENT_INPUT_DECLARATION_RE = re.compile(
    r"(?<![A-Za-z0-9_/／])(?=X\s*\d+\s*(?:[：:=]|为|是|is\b))", re.I,
)


def _declared_io_value(statement):
    match = _DECLARED_IO_LINE_RE.fullmatch(statement)
    if match is not None:
        address, raw_value = match.group(1), match.group("purpose").strip()
        # Equals may bind a bit level to a named purpose. This never interprets
        # register-value classifications or a bare input condition as wiring.
        if match.group("delimiter") == "=" and re.match(r"(?:SM|[XYMS])", address, re.I):
            level_purpose = _LEVEL_PURPOSE_RE.fullmatch(raw_value)
            if level_purpose is not None:
                purpose = level_purpose.group(2)
                if re.match(r"时|時|且|则|則|and\b|or\b", purpose, re.I) or _DEVICE.search(purpose):
                    return None
                return address, purpose, int(level_purpose.group(1))
        if match.group("delimiter") == "=" and re.match(r"[+-]?\d", raw_value):
            return None
        return address, raw_value, None
    match = _NAMED_INPUT_LEVEL_RE.fullmatch(statement)
    if match is not None:
        purpose = re.sub(r"(?:按下|动作|激活)\s*$", "", match.group(2)).strip()
        if purpose and not _DEVICE.search(purpose) and not re.search(r"时|则|如果|上升沿|下降沿", purpose):
            return match.group(1), purpose, int(match.group(3))
    return None


def _declared_io_segments(user_text):
    """Split declared rows, without treating a bare device list as declarations."""
    for statement in re.split(r"[\n;；。]+", str(user_text or "")):
        statement = _DECLARATION_HEADING_RE.sub("", statement.strip())
        if not statement:
            continue
        parts = [part.strip() for part in _DECLARATION_SEPARATOR_RE.split(statement)]
        if (
            len(parts) > 1
            and (_declared_io_value(parts[0]) is not None
                 or _GROUPED_INPUT_LEVEL_RE.fullmatch(parts[0]) is not None)
        ):
            segments = parts
        else:
            segments = [statement]
        adjacent = []
        for segment in segments:
            # Split only within an established declaration row. An output
            # assignment is never used as a boundary: that would turn
            # "X0=1启动Y0=1" (control logic) into two wiring declarations.
            boundaries = [match.start() for match in _ADJACENT_INPUT_DECLARATION_RE.finditer(segment)
                          if match.start() and not segment[:match.start()].rstrip().endswith(("/", "／"))]
            limits = [0, *boundaries, len(segment)]
            separated = [segment[start:end].strip(" \t\r\n,，、")
                         for start, end in zip(limits, limits[1:])]
            if boundaries and _declared_io_value(separated[0]) is not None:
                adjacent.extend(separated)
            else:
                adjacent.append(segment)
        for segment in adjacent:
            group = _GROUPED_INPUT_LEVEL_RE.fullmatch(segment)
            if group is None:
                yield segment
                continue
            # "分别" explicitly associates the same level with each named
            # input. Retain the shared purpose instead of guessing a missing
            # prefix in labels such as "手动点动A/B".
            for address in re.findall(r"X\s*\d+", group["addresses"], re.I):
                yield f'{address}={group["level"]}{group["purpose"]}'




def _is_io_attribute_answer(value):
    return bool(_IO_ATTRIBUTE_RE.search(str(value or "")) or confirmed_input_levels(value))


def _is_plain_address_answer(value, kind=None):
    """Accept a bare address/default label, not arbitrary device-related prose."""
    text = str(value or "").strip()
    address = single_address(text, kind)
    if address is None:
        return False
    remainder = _DEVICE.sub("", text, count=1)
    remainder = re.sub(
        r"[\s，,。；;：:（）()［］\[\]【】<>《》_-]+|建议|推薦|recommended|default",
        "", remainder, flags=re.IGNORECASE,
    )
    return not remainder


def parameter_uses_bound_address(parameter):
    """Whether this answer is actually selecting or qualifying one device address.

    io_binding identifies which device a question is about; it does not mean
    every answer must itself be an address. Register semantics such as
    "D0=0 means no material" remain ordinary confirmed parameters.
    """
    hint = binding_hint(parameter)
    if hint is None:
        return False
    name = str(parameter.get("name") or parameter.get("question") or "")
    value = parameter.get("value", "")
    return (
        _question_is_address(name)
        or (hint["kind"] in {"X", "M", "S", "SM"} and _is_io_attribute_answer(value))
        or _is_plain_address_answer(value, hint["kind"])
    )


_ROLE_LABELS = {
    "start": {"启动", "启动按钮", "启动信号", "起动", "起动按钮", "start", "startbutton", "startsignal", "起動", "起動ボタン"},
    "stop": {"停止", "停止按钮", "停止信号", "stop", "stopbutton", "stopsignal", "停止ボタン"},
    "output": {"输出", "控制输出", "被控负载", "被控负载输出", "output", "load", "motor", "モーター", "出力"},
}
_ORDER = {k: i for i, k in enumerate(("X", "Y", "M", "T", "C", "D", "S", "V", "Z", "SM", "SD"))}


def label_key(value):
    return re.sub(r"[\W_]+", "", str(value), flags=re.UNICODE).casefold()


def canonical_signal_role(label):
    """Return one exact canonical signal role; never fuzzy-match prose."""
    key = label_key(label)
    if not key:
        return ""
    matches = [
        role for role, aliases in _ROLE_LABELS.items()
        if key in {label_key(alias) for alias in aliases}
    ]
    return matches[0] if len(matches) == 1 else ""


def extract_declared_bindings(user_text, plc_model=None):
    """Extract explicit device-purpose declarations into generic binding identities.

    Every explicit declaration is retained as an identity/address/purpose fact.
    Optional role and active-level metadata are added only when deterministically
    known; their absence never causes the binding itself to be discarded.
    """
    model = str(plc_model or "").strip().upper()
    result = []
    seen = {}
    for statement in _declared_io_segments(user_text):
        declared = _declared_io_value(statement)
        if declared is None:
            continue
        address_text, raw_value, level = declared
        address = canonical_device(re.sub(r"\s+", "", address_text).upper())
        kind_match = re.match(r"[A-Z]+", address)
        if kind_match is None:
            continue
        kind = kind_match.group()
        digits = address[len(kind):]
        if model == "FX3U" and kind in {"X", "Y"} and any(char not in "01234567" for char in digits):
            continue
        if "?" in raw_value or "？" in raw_value or _STATE_NOT_PURPOSE_RE.match(raw_value):
            continue
        purpose = _INPUT_QUALIFIER_RE.split(raw_value, maxsplit=1)[0].strip()
        label = device_purpose_label(purpose or raw_value)
        if not label:
            continue
        role = canonical_signal_role(label)
        identity = f"declared.{role or kind.casefold()}.{address}"
        item = {
            "binding_id": identity,
            "kind": kind,
            "address": address,
            "label": label,
            "name": label,
            "source": "user_request",
        }
        if role:
            item["role"] = role
        if kind == "X":
            item.update(confirmed_input_levels(raw_value))
            if level is not None:
                item.update(active_level=level, inactive_level=1 - level)
        prior = seen.get(identity)
        if prior is not None:
            if "active_level" in prior and "active_level" in item:
                if prior["active_level"] != item["active_level"]:
                    # Conflicting declarations in one request do not establish
                    # a polarity. A later request is handled as a revision by
                    # the confirmation adapter, rather than by list order here.
                    prior.update(active_level=None, inactive_level=None)
            elif prior["label"] == item["label"] and "active_level" in item:
                prior.update(active_level=item["active_level"], inactive_level=item["inactive_level"])
            continue
        seen[identity] = item
        result.append(item)
    return result


def merge_declared_bindings(rows, existing=(), declared=()):
    """Merge Core-extracted declaration bindings against active I/O rows."""
    active = {
        canonical_device(str(row.get("address") or "").strip().upper())
        for row in (rows or ())
        if isinstance(row, dict) and row.get("address")
    }
    result = {}
    for source in (existing or ()):
        if not isinstance(source, dict) or not source.get("binding_id"):
            continue
        item = copy.deepcopy(source)
        item["address"] = canonical_device(str(item.get("address") or "").strip().upper())
        if item["address"] in active:
            result[str(item["binding_id"])] = item

    for source in (declared or ()):
        if not isinstance(source, dict) or not source.get("binding_id"):
            continue
        item = copy.deepcopy(source)
        item["address"] = canonical_device(str(item.get("address") or "").strip().upper())
        if item["address"] not in active:
            continue
        same_role = [
            key for key, value in result.items()
            if item.get("role")
            and value.get("role") == item.get("role")
            and value.get("address") == item["address"]
        ]
        if len(same_role) == 1:
            key = same_role[0]
            merged = result[key]
            for field in ("active_level", "inactive_level", "label", "name", "source"):
                if field in item:
                    merged[field] = copy.deepcopy(item[field])
            result[key] = merged
            continue
        result[str(item["binding_id"])] = item
    return [result[key] for key in sorted(result)]


def recover_declared_bindings(spec, rows, bindings=()):
    """Recover missing legacy identities without replaying edits from prose.

    Current bindings own their addresses, including deliberately unknown levels.
    Requests can supply a missing binding only for one still-present I/O row.
    Once recovered, its row identity preserves later moves and deletions through
    bind_answers. This adapter does not allocate I/O or replace current facts.
    """
    from plc.generation_contract import intent_context

    rows, bindings = copy.deepcopy(rows), copy.deepcopy(list(bindings or ()))
    occupied = {canonical_device(item.get("address")) for item in bindings
                if isinstance(item, dict)}
    declarations = {}
    model = str(spec.get("plc_model") or "FX3U").strip().upper()
    for request in intent_context(spec).get("requests", []):
        if not isinstance(request, dict):
            continue
        for item in extract_declared_bindings(request.get("text", ""), model):
            # A later explicit declaration is a user revision. Existing
            # structured bindings still take precedence over every request.
            declarations[item["address"]] = item
    identities = {item.get("binding_id") for item in bindings if isinstance(item, dict)}
    for address, source in declarations.items():
        if address in occupied:
            continue
        matches = [row for row in rows if isinstance(row, dict)
                   and canonical_device(row.get("address")) == address]
        if len(matches) != 1:
            continue
        row = matches[0]
        identity = row.get("binding_id") or source["binding_id"]
        if identity in identities:
            continue
        item = copy.deepcopy(source)
        item["binding_id"] = identity
        item["row_binding_id"] = identity
        row["binding_id"] = identity
        # The editable row owns its displayed purpose. Recovery must not undo
        # a label edit, including an intentionally empty label.
        if isinstance(row.get("label"), str):
            item["label"] = row["label"].strip()
        elif "label" in row:
            item.pop("label", None)
        bindings.append(item)
        identities.add(identity)
        occupied.add(address)
    return rows, sorted(bindings, key=lambda item: str(item.get("binding_id") or ""))


def binding_reference(parameter):
    """Keep an explicit logical identity even before its kind is available."""
    raw = parameter.get("io_binding")
    if not isinstance(raw, dict) and parameter.get("binding_id"):
        raw = parameter  # A response may place explicit metadata on the question.
    if not isinstance(raw, dict):
        return None
    identity = str(raw.get("binding_id") or "").strip()
    if not identity or len(identity) > 128:
        return None
    result = {"binding_id": identity}
    for key in ("kind", "role", "row_id", "label"):
        value = raw.get(key)
        if isinstance(value, str) and (key == "label" or len(value) <= 128):
            result[key] = value.upper() if key == "kind" else value.strip()
    return result


def binding_hint(parameter, related=()):
    """Return bounded typed metadata; ordinary parameter prose is not metadata."""
    raw = binding_reference(parameter)
    if isinstance(raw, dict):
        raw = dict(raw)
        binding_id = str(raw.get("binding_id") or "").strip()
        # An attribute question may reference an explicitly declared sibling
        # identity. Complete absent metadata only, never repair contradictory
        # declarations or infer identity/kind from natural-language labels.
        if binding_id and related:
            siblings = [binding_reference(p) for p in related if isinstance(p, dict)]
            siblings = [p for p in siblings if p and p["binding_id"] == binding_id]
            for key in ("kind", "role", "row_id", "label"):
                values = {p[key] for p in siblings if isinstance(p.get(key), str)}
                if key not in raw and len(values) == 1:
                    raw[key] = values.pop()
            if "kind" not in raw:
                addresses = [single_address(p.get("value", "")) for p in related if isinstance(p, dict)
                             and (binding_reference(p) or {}).get("binding_id") == binding_id]
                kinds = {re.match(r"[A-Z]+", a).group() for a in addresses if a}
                if len(kinds) == 1:
                    raw["kind"] = kinds.pop()
        if "kind" not in raw:
            address = single_address(parameter.get("value", ""))
            if address:
                raw["kind"] = re.match(r"[A-Z]+", address).group()
        kind = str(raw.get("kind") or "").upper()
        if binding_id and len(binding_id) <= 128 and kind in _ORDER:
            result = {"binding_id": binding_id, "kind": kind}
            # Purpose is independent of the question/answer text. Missing or
            # malformed optional labels do not create a confirmation gate.
            label = raw.get("label")
            if isinstance(label, str):
                result["label"] = label.strip()
            for key in ("role", "row_id"):
                value = raw.get(key)
                if isinstance(value, str) and 0 < len(value) <= 128:
                    result[key] = value
            if "role" not in result:
                identifier = str(parameter.get("id") or "").strip().casefold()
                legacy = _ALIASES.get(identifier) or _QUESTION_ALIASES.get(identifier)
                if legacy and legacy[1] == kind:
                    result["role"] = legacy[0]
            return result
    identifier = str(parameter.get("id") or "").strip().casefold()
    if identifier in _ALIASES:
        role, kind = _ALIASES[identifier]
        return {"binding_id": "legacy_" + role, "role": role, "kind": kind}
    if identifier in _QUESTION_ALIASES:
        role, kind = _QUESTION_ALIASES[identifier]
        return {"binding_id": "question_" + identifier, "role": role, "kind": kind}
    # Read old polarity-only questions that predate typed bindings. A single
    # explicit input reference identifies the point the user is qualifying;
    # no purpose or input polarity is guessed from the question's wording.
    name = str(parameter.get("name") or parameter.get("question") or "")
    if re.search(r"极性|polarity|常[开開闭閉]|normally[ _-]+(?:open|closed)", name, re.I):
        address = single_address(name, "X")
        if address is not None:
            identity = identifier or hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
            if len(identity) > 110:
                identity = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
            return {"binding_id": "question_" + identity, "kind": "X"}
    return None


def single_address(value, kind=None):
    """Extract one address only, allowing legacy option labels such as X0（建议）."""
    matches = list(_DEVICE.finditer(str(value)))
    if len(matches) != 1:
        return None
    address = canonical_device(matches[0].group())
    prefix = re.match(r"[A-Z]+", address).group()
    return address if kind is None or prefix == kind else None


def confirmed_input_levels(value):
    """Decode a confirmed physical input answer, never options or model notes.

    Active/inactive describe the input bit when the signal acts. They are not
    ladder NO/NC contacts. Conflicting statements remain unresolved; this helper
    neither fills an unanswered question nor changes generated logic.
    """
    text = str(value or "").casefold()
    explicit = set()
    # Parse event-level statements for any input, not just a start/stop button.
    # Negative states contribute the inverse level; contradictory statements
    # stay unresolved rather than falling back to the physical-contact label.
    state = (r"按下|动作|有信号|押下|到达[^，,。；;:：=\n]{0,20}?|"
             r"检测到[^，,。；;:：=\n]{0,20}?|触发|报警|故障|信号有效|输入有效|复位")
    pattern = (r"(?P<negative>没有|未曾|未|没|不|无)?(?P<state>" + state + r")"
               r"(?:时|時)?\s*(?:信号|输入)?\s*(?:为|為|是|=|:|：)?\s*"
               r"(?P<level>on|off|1|0|接通|断开)(?![a-z0-9])")
    inactive = re.compile(r"(?:松开|松開|释放|解除报警|报警解除|无信号)(?:时|時)?\s*(?:为|為|是|=|:|：)?\s*(on|off|1|0|接通|断开)(?![a-z0-9])")
    for match in inactive.finditer(text):
        explicit.add(0 if match[1] in {"on", "1", "接通"} else 1)
    # Don't parse the "报警" substring of "解除报警" a second time.
    event_text = inactive.sub(" ", text)
    for match in re.finditer(pattern, event_text):
        level = 1 if match['level'] in {"on", "1", "接通"} else 0
        explicit.add(1 - level if match['negative'] else level)
    for match in re.finditer(r"\b(active|inactive|triggered|released)\s*(?:is|=|:)?\s*(on|off|1|0)\b", text):
        level = 1 if match[2] in {"on", "1"} else 0
        explicit.add(1 - level if match[1] in {"inactive", "released"} else level)
    for match in re.finditer(r"(?<![a-z])(?:active|level)[_ -](high|low)(?![a-z])", text):
        explicit.add(1 if match[1] == "high" else 0)
    if explicit:
        levels = explicit
    else:
        levels = set()
        if re.search(r"常开|常開|normally[ _-]+open|(?<![a-z])no(?![a-z])", text):
            levels.add(1)
        if re.search(r"常闭|常閉|normally[ _-]+closed|(?<![a-z])nc(?![a-z])", text):
            levels.add(0)
    if len(levels) != 1:
        return {}
    active = levels.pop()
    return {"active_level": active, "inactive_level": 1 - active}


def _question_is_address(name):
    """Narrow legacy compatibility for questions without typed metadata."""
    text = str(name).casefold()
    return any(s in text for s in ("哪个输入", "哪个输出", "哪个x", "哪个y", "哪个轴", "输入点", "输出点", "输入地址", "输出地址", "x输入", "y输出", "接什么输入", "接什么输出", "接哪", "which input", "which output", "input address", "output address"))


def _purpose_label(hint):
    """Explicit purpose first; old typed roles may use a neutral short name."""
    if "label" in hint:
        return hint["label"]
    return {"start": "启动输入", "stop": "停止输入", "output": "控制输出"}.get(hint.get("role"), "")


def _answer_label(name):
    """Match historical unbound rows only; never create a new device comment."""
    text = str(name)
    for phrase in ("使用哪个轴", "接哪个输入点", "接哪个输出点", "分别接什么输入", "接什么输入", "接什么输出", "是否有", "分别", "哪个", "？", "?"):
        text = text.replace(phrase, "")
    return text.strip(" ：:，,。") or "用户确认地址"


def _row_matches(row, hint, name):
    if row.get("binding_id") == hint["binding_id"]:
        return True
    if hint.get("row_id"):
        return row.get("binding_id") == hint["row_id"] or row.get("row_id") == hint["row_id"]
    if row.get("binding_id"):
        return row["binding_id"] == hint["binding_id"]
    key = label_key(str(row.get("label") or "").strip())
    if key and key == label_key(hint.get("label", "")):
        return True  # Exact purpose match; ambiguous matches are not rebound.
    if key and key == label_key(_answer_label(name)):
        return True
    # Only exact, unqualified legacy labels. "Motor 2 start" must never be
    # treated as the same binding as "Motor 1 start".
    return key in _ROLE_LABELS.get(hint.get("role"), set())


def _bound_row(rows, identity, binding):
    """Resolve a persisted answer by row identity, not by its historical value."""
    owner = binding.get("row_binding_id") or identity
    matches = [row for row in rows if isinstance(row, dict)
               and row.get("binding_id") == owner]
    if len(matches) == 1:
        return matches[0]
    if binding.get("row_binding_id"):
        return None  # Explicit ownership was removed, not rebound by spelling.
    # Older shared-address provenance has no owning row identity. An exact,
    # unique existing address is its only safe read-only association.
    matches = [row for row in rows if isinstance(row, dict)
               and str(row.get("address") or "").strip().upper() == binding.get("address")]
    return matches[0] if len(matches) == 1 else None


def _saved_parameter_binding(parameter, bindings):
    """An explicit binding identity wins over a legacy question-id fallback."""
    hint = binding_hint(parameter)
    saved = [item for item in (bindings or ()) if isinstance(item, dict)]
    # Public generation parameters intentionally omit io_binding. Their stable
    # source_parameter_id must still recover the existing owner, not produce a
    # fresh question_<id> binding on every projection.
    matches = [item for item in saved if hint and item.get("binding_id") == hint["binding_id"]]
    if not matches:
        identifier = str(parameter.get("id") or "").strip()
        matches = [item for item in saved if identifier and item.get("source_parameter_id") == identifier]
    return matches[0] if len(matches) == 1 else None


def bound_parameter_is_removed(parameter, rows, bindings=()):
    """Ignore retained answers for an explicitly removed, previously owned row.

    A new explicit address is an edit, not a stale answer. This is shared by
    validation and binding so deleting a row cannot either resurrect it or
    leave a polarity-only answer blocking confirmation.
    """
    hint = binding_hint(parameter)
    prior = _saved_parameter_binding(parameter, bindings)
    if hint is None or prior is None or _bound_row(rows, hint["binding_id"], prior) is not None:
        return False
    value = str(parameter.get("value") or "")
    if not _DEVICE.search(value):
        return _is_io_attribute_answer(value)
    address = single_address(value, hint["kind"])
    old_address = single_address(prior.get("value", ""), hint["kind"]) or single_address(prior.get("address", ""), hint["kind"])
    return address is not None and address == old_address


def resolve_parameter_address(parameter, rows, bindings=()):
    """Resolve an answer against its existing owner, not a model's default.

    Address+polarity answers may allocate/edit an address. Polarity-only
    answers reuse their current row or the one point named in the question.
    Ambiguous/wrong-kind explicit addresses
    cannot silently fall back to question wording or another row's label.
    """
    hint = binding_hint(parameter)
    if hint is None or not parameter_uses_bound_address(parameter):
        return None
    value = str(parameter.get("value") or "")
    if _DEVICE.search(value):
        return single_address(value, hint["kind"])
    if not _is_io_attribute_answer(value):
        return None
    rows = [row for row in (rows or ()) if isinstance(row, dict)]
    saved = _saved_parameter_binding(parameter, bindings)
    if saved is not None:
        row = _bound_row(rows, hint["binding_id"], saved)
        return single_address(canonical_device(row.get("address")), hint["kind"]) if row else None

    owner = hint.get("row_id") or hint["binding_id"]
    owned = [row for row in rows if row.get("binding_id") == owner or row.get("row_id") == owner]
    if owned or hint.get("row_id"):
        return single_address(canonical_device(owned[0].get("address")), hint["kind"]) if len(owned) == 1 else None

    name = str(parameter.get("name") or parameter.get("question") or "")
    if _DEVICE.search(name):
        address = single_address(name, hint["kind"])
        # This is a displayed, single-point confirmation question, not a
        # suggestion from options/defaults. A saved owner above always wins.
        return address
    else:
        matches = [row for row in rows
                   if str(row.get("kind") or re.sub(r"\d+$", "", str(row.get("address") or ""))).upper() in {hint["kind"], "特殊"}
                   and _row_matches(row, hint, name)]
    if len(matches) != 1:
        return None
    return single_address(canonical_device(matches[0].get("address")), hint["kind"])



def resolve_answer_bindings(rows, parameters, bindings=(), *, protected_ids=()):
    """Resolve a complete submission against current ownership, before allocation.

    Sibling answers join only by explicit binding identity. Retained answers
    follow table edits and removals; defaults and new rows are never evidence.
    """
    from plc.specification.parameters import parameter_is_applicable

    aliases = copy.deepcopy(list(rows or []))
    rows = canonical_io_rows(aliases)
    previous = {str(b["binding_id"]): copy.deepcopy(b) for b in bindings or ()
                if isinstance(b, dict) and b.get("binding_id")}
    owners = {r.get("address"): r.get("binding_id") for r in rows if isinstance(r, dict)}
    old_owners = {r.get("binding_id"): canonical_device(r.get("address"))
                  for r in aliases if isinstance(r, dict) and r.get("binding_id")}
    for identity, binding in previous.items():
        binding["address"] = canonical_device(binding.get("address"))
        address = old_owners.get(binding.get("row_binding_id") or identity)
        if address in owners and owners[address]:
            binding["row_binding_id"] = owners[address]

    answers, groups, issues = {}, {}, []
    related = [*parameters, *({"io_binding": b} for b in previous.values())]
    for index, parameter in enumerate(parameters or ()):
        if not isinstance(parameter, dict) or not parameter_is_applicable(parameter, parameters):
            continue
        item = copy.deepcopy(parameter)
        if not isinstance(item.get("io_binding"), dict):
            saved = _saved_parameter_binding(item, previous.values())
            hint = binding_hint({"io_binding": saved}) if saved else None
            if hint is not None:
                item["io_binding"] = hint
        hint = binding_hint(item, related)
        if hint is not None:
            item["io_binding"] = hint
        name = str(item.get("name") or "").strip()
        if item.get("id") in protected_ids or not name:
            continue
        if hint is None:
            reference = binding_reference(item)
            if reference and (_is_io_attribute_answer(item.get("value")) or single_address(item.get("value"))):
                hint = {"kind": "", **reference}
                entry = {"index": index, "item": item, "hint": hint, "address": None,
                         "removed": False, "explicit_address": bool(_DEVICE.search(str(item.get("value") or "")))}
                answers[index] = entry
                groups.setdefault(hint["binding_id"], []).append(entry)
                continue
            address = single_address(item.get("value", "")) if _question_is_address(name) else None
            if address is None:
                continue
            identifier = str(item.get("id") or "") or hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
            hint = {"binding_id": "question_" + identifier, "kind": re.match(r"[A-Z]+", address).group()}
        elif not parameter_uses_bound_address(item):
            continue
        value = str(item.get("value") or "")
        address = resolve_parameter_address(item, rows, previous.values()) if binding_hint(item) else single_address(value)
        prior = previous.get(hint["binding_id"])
        if address and prior:
            old_address = single_address(prior.get("value", ""), hint["kind"]) or single_address(prior.get("address", ""), hint["kind"])
            owner = _bound_row(rows, hint["binding_id"], prior)
            if address == old_address and owner is not None:
                address = single_address(owner.get("address"), hint["kind"])
        entry = {"index": index, "item": item, "hint": hint, "address": address,
                 "removed": bound_parameter_is_removed(item, rows, previous.values()),
                 "explicit_address": bool(_DEVICE.search(value))}
        answers[index] = entry
        if value.strip():
            groups.setdefault(hint["binding_id"], []).append(entry)

    def issue(code, message, entry, **details):
        issues.append({"code": code, "message": message,
                       "path": f"$.parameters[{entry['index']}].value", "row": entry["index"], **details})

    merged = {}
    for identity, entries in groups.items():
        explicit = {e["address"] for e in entries if e["explicit_address"] and e["address"] and not e["removed"]}
        for entry in entries:
            # Invalid explicit addresses never borrow a sibling's valid answer.
            if not entry["explicit_address"] and _is_io_attribute_answer(entry["item"].get("value")) and len(explicit) == 1:
                entry.update(address=next(iter(explicit)), removed=False)
        entries = [e for e in entries if not e["removed"]]
        groups[identity] = entries
        if not entries:
            merged[identity] = {"removed": True, "address": None, "attributes": {}}
            continue
        addresses = {e["address"] for e in entries if e["address"]}
        levels = {confirmed_input_levels(e["item"].get("value")).get("active_level") for e in entries}
        levels.discard(None)
        metadata_conflict = any(len({e["hint"][key] for e in entries if e["hint"].get(key)}) > 1
                                for key in ("kind", "role", "row_id"))
        if len(addresses) > 1 or metadata_conflict:
            issue("conflicting_io_binding", "同一输入/输出绑定选择了不同地址或身份，请统一选择", entries[-1])
        if len(levels) > 1:
            issue("conflicting_io_attributes", "同一输入/输出绑定的有效电平或触点极性相互矛盾，请统一选择", entries[-1])
        merged[identity] = {"removed": False, "address": next(iter(addresses)) if len(addresses) == 1 else None,
                            "attributes": {"active_level": next(iter(levels)), "inactive_level": 1-next(iter(levels))} if len(levels) == 1 else {}}
        for entry in entries:
            if entry["address"] is None:
                issue("invalid_io_answer", "请为该输入/输出选择一个明确的软元件地址", entry)

    # Current owned rows also occupy addresses. Apply the whole batch of
    # declared moves before detecting collisions, so a legitimate swap works.
    effective_owners = {str(row.get("row_id") or row["binding_id"]):
                        (row.get("address"), row.get("kind"), None)
                        for row in rows if row.get("binding_id")}
    for entry in answers.values():
        if entry["removed"] or not entry["address"]:
            continue
        hint, address = entry["hint"], entry["address"]
        owner = hint.get("row_id") or hint["binding_id"]
        previous_owner = (previous.get(hint["binding_id"]) or {}).get("row_binding_id")
        matches = [row for row in rows if row.get("binding_id") == hint["binding_id"]
                   or previous_owner and row.get("binding_id") == previous_owner
                   or hint.get("row_id") and hint["row_id"] in {row.get("row_id"), row.get("binding_id")}]
        if len(matches) == 1:
            owner = matches[0].get("row_id") or matches[0].get("binding_id")
        effective_owners[owner] = (address, hint["kind"], entry)
    address_owners = {}
    for owner, (address, kind, entry) in effective_owners.items():
        prior = address_owners.get(address)
        if prior and prior[0] != owner and kind in {"X", "Y"} and (entry or prior[1]):
            issue("conflicting_io_owners", f"{address} 被不同输入/输出用途重复选择；有意共用时应显式关联同一 I/O 行", entry or prior[1],
                  address=address, first_row=prior[1]["index"] if prior[1] else None)
        else:
            address_owners[address] = (owner, entry)
    return {"rows": rows, "bindings": previous, "answers": answers, "groups": groups,
            "merged": merged, "issues": issues}


def bind_known_question_rows(rows, parameters):
    """Anchor a displayed one-point polarity question before the first answer.

    This records identity only: no value/default is accepted, no I/O is added,
    and no label or address is changed. A subsequent edit of the review table
    can therefore keep its owner even before the first specification save.
    """
    rows, parameters = copy.deepcopy(rows), copy.deepcopy(parameters)
    for parameter in parameters:
        hint = binding_hint(parameter)
        if hint is None or hint["kind"] != "X" or hint.get("row_id"):
            continue
        name = str(parameter.get("name") or "")
        if not re.search(r"极性|polarity|常[开開闭閉]|normally[ _-]+(?:open|closed)", name, re.I):
            continue
        address = single_address(name, hint["kind"])
        matches = [row for row in rows if isinstance(row, dict)
                   and address is not None and canonical_device(row.get("address")) == address]
        if len(matches) != 1:
            continue
        row = matches[0]
        owner = row.get("binding_id") or hint["binding_id"]
        row.setdefault("binding_id", owner)
        if owner != hint["binding_id"]:
            hint["row_id"] = owner
        parameter["io_binding"] = hint
    return rows, parameters


def bind_answers(rows, parameters, bindings=(), *, protected_ids=()):
    """Bind confirmed answers on a copy; return rows, remaining params, provenance.

    Rows that already exist are matched by explicit identity, then a unique
    exact legacy role label, then an already allocated exact address. Newly appended rows cannot become
    another answer's replacement target. This removes parameter-order dependence.
    """
    resolution = resolve_answer_bindings(rows, parameters, bindings, protected_ids=protected_ids)
    rows, previous = resolution["rows"], resolution["bindings"]
    original_count = len(rows)
    original_rows = copy.deepcopy(rows)
    pending, remaining, applied = {}, [], {}
    invalid = {resolution["answers"][issue["row"]]["hint"]["binding_id"]
               for issue in resolution["issues"] if issue["code"] in
               {"conflicting_io_binding", "conflicting_io_attributes", "invalid_io_answer"}}
    for index, parameter in enumerate(parameters or ()):
        entry = resolution["answers"].get(index)
        if entry is not None and entry["removed"]:
            continue
        if entry is None or not entry["address"] or entry["hint"]["binding_id"] in invalid:
            remaining.append(copy.deepcopy(parameter))
            continue
        item, hint, address = entry["item"], entry["hint"], entry["address"]
        item["value"] = _DEVICE.sub(lambda _m: address, str(item["value"]), count=1)
        pending.setdefault(hint["binding_id"], []).append((hint, item, address))
        applied[str(item["name"])] = str(item.get("value") or "")
        if _is_io_attribute_answer(item.get("value")) or isinstance(item.get("required_when"), dict):
            item.setdefault("io_binding", copy.deepcopy(hint))
            remaining.append(item)

    merged = []
    for identity, answers in pending.items():
        # Attribute provenance survives when the consumed address question is
        # absent on the next save. Pick by stable identifiers, never input order.
        answers.sort(key=lambda a: (not bool(confirmed_input_levels(a[1].get("value"))),
                                   not _is_io_attribute_answer(a[1].get("value")),
                                   str(a[1].get("id") or ""), str(a[1].get("name") or "")))
        hint, item, address = answers[0]
        hint = copy.deepcopy(hint)
        for other, _item, _address in answers:
            for key, value in other.items():
                hint.setdefault(key, value)
        merged.append((hint, item, address))

    claimed = set()
    for hint, item, address in sorted(merged, key=lambda x: x[0]["binding_id"]):
        identity = hint["binding_id"]
        name = str(item["name"])
        matches = [i for i, row in enumerate(original_rows) if isinstance(row, dict)
                   and str(row.get("kind") or re.sub(r"\d+$", "", str(row.get("address") or ""))).upper() in {hint["kind"], "特殊"}
                   and _row_matches(row, hint, name) and i not in claimed]
        # Explicit identity wins over a new address so editing X0 -> X2 updates
        # the same row. Exact role matches can also perform an address swap.
        exact_identity = [i for i in matches if original_rows[i].get("binding_id") == identity
                          or hint.get("row_id") and original_rows[i].get("row_id") == hint["row_id"]]
        addresses = [i for i, row in enumerate(rows) if isinstance(row, dict)
                     and str(row.get("address") or "").strip().upper() == address]
        if len(exact_identity) == 1:
            index = exact_identity[0]
        elif len(matches) == 1:
            index = matches[0]
        elif len(addresses) == 1:
            index = addresses[0]
        else:
            index = len(rows)
            rows.append({"kind": hint["kind"], "address": address,
                         "label": _purpose_label(hint), "source": item.get("source") or "user"})
        row = rows[index]
        # Never consume an existing different binding merely to attach a role.
        # Shared addresses can have several provenance records, but only one
        # physical I/O row. They are not silently reassigned to another address.
        explicit_row = hint.get("row_id") and (row.get("row_id") == hint["row_id"] or row.get("binding_id") == hint["row_id"])
        if row.get("binding_id") in (None, "", identity) or explicit_row:
            row["binding_id"] = identity
            row["source_parameter_id"] = str(item.get("id") or "")
            row["address"] = address
            row["source"] = item.get("source") or "user"
            # Existing labels (including an intentionally empty one) belong to
            # the I/O table. Reconfirmation must not restore a model's label.
            row.setdefault("label", _purpose_label(hint))
        owns_purpose = row.get("binding_id") == identity or bool(explicit_row)
        purpose = (str(row.get("label") or "").strip() if owns_purpose else
                   str(hint.get("label", (previous.get(identity) or {}).get("label", ""))).strip())
        if isinstance(item.get("io_binding"), dict):
            item["io_binding"]["label"] = purpose
        claimed.add(index)
        previous[identity] = {**hint, "address": address,
                              "source_parameter_id": str(item.get("id") or ""),
                              "name": name, "label": purpose,
                              "value": str(item.get("value") or ""),
                              "source": item.get("source") or "user",
                              "row_binding_id": row.get("binding_id") or identity}
        previous[identity].update(resolution["merged"][identity]["attributes"])
        applied[name] = str(item.get("value") or "")

    # Stable order for additions, without changing the order of existing rows.
    def order(row):
        address = str(row.get("address") or "")
        parts = re.fullmatch(r"([A-Z]+)(\d+)", address)
        return (_ORDER.get(parts[1], 99), int(parts[2])) if parts else (99, 0)
    rows[original_count:] = sorted(rows[original_count:], key=order)
    # A user may edit the I/O table directly after address questions were
    # consumed. Reflect that edit in provenance; never replay old answers.
    active = {}
    for identity, binding in previous.items():
        row = _bound_row(rows, identity, binding)
        if row is not None:
            binding["address"] = str(row.get("address") or "").strip().upper()
            binding["row_binding_id"] = row.get("binding_id") or identity
            # Several semantic owners can reference one physical point. Only
            # the actual owner or an explicitly linked row inherits its label.
            # An accidental address collision must not rename another signal.
            if row.get("binding_id") == identity or binding.get("row_id"):
                binding["label"] = str(row.get("label") or "").strip()
            hint = binding_hint({"id": binding.get("source_parameter_id")})
            if not binding.get("role") and hint and hint["kind"] == binding.get("kind"):
                binding["role"] = hint["role"]
            if binding.get("kind") == "X" and "value" in binding:
                # Recompute, so a polarity edit cannot leave stale derived facts.
                binding.pop("active_level", None)
                binding.pop("inactive_level", None)
                binding.update(confirmed_input_levels(binding["value"]))
            active[identity] = binding
    # A deleted row has no active binding. Do not feed its stale address to
    # Agent B merely because it still occurs in historical provenance.
    return rows, remaining, [active[k] for k in sorted(active)], applied


def restore_bound_choices(questions, rows, bindings):
    """Carry an existing confirmed answer into the same stable review question.

    This is not adoption of a newly suggested model default. Unidentified/new
    questions stay unanswered; direct table edits replace only the old address.
    """
    result = copy.deepcopy(questions)
    saved = [item for item in (bindings or []) if isinstance(item, dict)]
    for question in result:
        if not isinstance(question, dict) or str(question.get("value") or "").strip():
            continue
        identifier = str(question.get("id") or "").strip()
        hint = binding_hint(question)
        matches = [item for item in saved if
                   (hint and item.get("binding_id") == hint["binding_id"]) or
                   (identifier and item.get("source_parameter_id") == identifier)]
        if len(matches) != 1:
            continue
        binding = matches[0]
        row = _bound_row(rows, binding["binding_id"], binding)
        if row is None:
            continue
        value = str(binding.get("value") or "")
        address = str(row.get("address") or "").strip().upper()
        if not single_address(address):
            continue
        name = str(question.get("name") or question.get("question") or "")
        if _question_is_address(name) and not _is_io_attribute_answer(name):
            question["value"] = address
        elif single_address(value):
            question["value"] = _DEVICE.sub(lambda _m: address, value, count=1)
        elif _is_io_attribute_answer(value):
            question["value"] = value
        else:
            continue
        question["source"] = binding.get("source") or "previous"
        if isinstance(question.get("io_binding"), dict):
            owns_row = row.get("binding_id") == binding["binding_id"] or binding.get("row_id")
            question["io_binding"]["label"] = str((row if owns_row else binding).get("label") or "").strip()
    return result


def generation_io_snapshot(spec, *, protected_ids=()):
    """Repair old confirmed answer bindings on a copy for all generation paths.

    Preserve row identity/deletions via bind_answers, not a fresh text-to-I/O
    allocation. An already projected binding may lack private value/row IDs;
    its explicit derived levels survive repeated projection unchanged.
    """
    result = copy.deepcopy(spec)
    if not isinstance(result, dict) or not isinstance(result.get("io_table"), list):
        return result
    parameters = result.get("parameters")
    bindings = result.get("io_bindings")
    if isinstance(parameters, list):
        from plc.specification.parameters import parameter_is_applicable
        disabled = {p.get("id") for p in parameters if isinstance(p, dict)
                    and p.get("id") and not parameter_is_applicable(p, parameters)}
        parameters = [p for p in parameters if not isinstance(p, dict) or p.get("id") not in disabled]
        if isinstance(bindings, list):
            bindings = [b for b in bindings if not isinstance(b, dict) or b.get("source_parameter_id") not in disabled]
        referenced = {b.get("address") for b in (bindings or []) if isinstance(b, dict)}
        result["io_table"] = [r for r in result["io_table"] if not isinstance(r, dict)
                              or r.get("source_parameter_id") not in disabled or r.get("address") in referenced]
    rows, remaining, bindings, _ = bind_answers(
        result["io_table"], parameters if isinstance(parameters, list) else [],
        bindings if isinstance(bindings, list) else [], protected_ids=protected_ids,
    )
    rows, bindings = recover_declared_bindings(result, rows, bindings)
    result["io_table"] = rows
    if isinstance(parameters, list):
        result["parameters"] = remaining
    if bindings or "io_bindings" in result:
        result["io_bindings"] = bindings
    return result
