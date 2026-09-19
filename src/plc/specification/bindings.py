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
_ROLE_LABELS = {
    "start": {"启动", "启动按钮", "启动信号", "起动", "起动按钮", "start", "startbutton", "startsignal", "起動", "起動ボタン"},
    "stop": {"停止", "停止按钮", "停止信号", "stop", "stopbutton", "stopsignal", "停止ボタン"},
    "output": {"输出", "控制输出", "被控负载", "被控负载输出", "output", "load", "motor", "モーター", "出力"},
}
_ORDER = {k: i for i, k in enumerate(("X", "Y", "M", "T", "C", "D", "S", "V", "Z", "SM", "SD"))}


def label_key(value):
    return re.sub(r"[\W_]+", "", str(value), flags=re.UNICODE).casefold()


def binding_hint(parameter):
    """Return bounded typed metadata; ordinary parameter prose is not metadata."""
    raw = parameter.get("io_binding")
    if isinstance(raw, dict):
        binding_id = str(raw.get("binding_id") or "").strip()
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
            return result
    identifier = str(parameter.get("id") or "").strip().casefold()
    if identifier in _ALIASES:
        role, kind = _ALIASES[identifier]
        return {"binding_id": "legacy_" + role, "role": role, "kind": kind}
    if identifier in _QUESTION_ALIASES:
        role, kind = _QUESTION_ALIASES[identifier]
        return {"binding_id": "question_" + identifier, "role": role, "kind": kind}
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
    for match in re.finditer(r"(?<!未)(?<!没)(?<!不)(?:按下|动作|有信号|押下)(?:时|時)?\s*(?:为|為|是|=|:|：)?\s*(on|off|1|0|接通|断开)(?![a-z0-9])", text):
        explicit.add(1 if match[1] in {"on", "1", "接通"} else 0)
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


def bind_answers(rows, parameters, bindings=(), *, protected_ids=()):
    """Bind confirmed answers on a copy; return rows, remaining params, provenance.

    Rows that already exist are matched by explicit identity, then a unique
    exact legacy role label, then an already allocated exact address. Newly appended rows cannot become
    another answer's replacement target. This removes parameter-order dependence.
    """
    original_alias_rows = copy.deepcopy(list(rows or []))
    rows = canonical_io_rows(original_alias_rows)
    previous = {str(item.get("binding_id")): copy.deepcopy(item)
                for item in bindings or () if isinstance(item, dict) and item.get("binding_id")}
    owners = {r.get("address"): r.get("binding_id") for r in rows if isinstance(r, dict)}
    old_owners = {r.get("binding_id"): canonical_device(r.get("address"))
                  for r in original_alias_rows if isinstance(r, dict) and r.get("binding_id")}
    for identity, binding in previous.items():
        binding["address"] = canonical_device(binding.get("address"))
        old_owner = binding.get("row_binding_id") or identity
        owner_address = old_owners.get(old_owner)
        if owner_address in owners and owners[owner_address]:
            binding["row_binding_id"] = owners[owner_address]
    original_count = len(rows)
    original_rows = copy.deepcopy(rows)
    pending, remaining, applied = [], [], {}
    for parameter in parameters or ():
        item = copy.deepcopy(parameter)
        if not isinstance(item, dict):
            remaining.append(item)
            continue
        name = str(item.get("name") or "").strip()
        identifier = str(item.get("id") or "").strip()
        hint = binding_hint(item)
        if identifier in protected_ids or not name or (hint is None and not _question_is_address(name)):
            remaining.append(item)
            continue
        address = single_address(item.get("value", ""), hint.get("kind") if hint else None)
        if address is None:
            remaining.append(item)
            continue
        if hint is None:
            identity = identifier or hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
            hint = {"binding_id": "question_" + identity, "kind": re.match(r"[A-Z]+", address).group()}
        # Normalize only a confirmed address answer, not arbitrary prose. Keep
        # physical-contact wording while removing the address's padding alias.
        item["value"] = _DEVICE.sub(lambda _m: address, str(item["value"]), count=1)
        prior = previous.get(hint["binding_id"])
        if prior:
            prior_address = single_address(prior.get("value", ""), hint["kind"])
            linked_row = _bound_row(original_rows, hint["binding_id"], prior)
            if address == prior_address and linked_row is not None:
                # A combined answer such as "X1，常闭" remains editable for its
                # polarity. If only its historical address was retained, an
                # explicit I/O-table edit wins; preserve all the contact text.
                current_address = str(linked_row.get("address") or "").strip().upper()
                if current_address and current_address != address:
                    item["value"] = _DEVICE.sub(lambda _m: current_address, str(item["value"]), count=1)
                    address = current_address
            elif address == prior_address and linked_row is None:
                # Explicitly removing a bound row must not re-create it from an
                # unchanged retained answer. Its original value remains in the
                # operator audit, not in the active generation specification.
                continue
        pending.append((hint, item, address))
        if re.search(r"常[开闭閉]|normally\s+(?:open|closed)|\b(?:NO|NC)\b|上升沿|下降沿|rising|falling|edge", str(item.get("value")), re.I):
            remaining.append(item)

    claimed = set()
    for hint, item, address in sorted(pending, key=lambda x: (x[0]["binding_id"], x[2], str(x[1].get("id") or ""), str(x[1].get("name") or ""))):
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
        if isinstance(item.get("io_binding"), dict):
            item["io_binding"]["label"] = str(row.get("label") or "").strip()
        claimed.add(index)
        previous[identity] = {**hint, "address": address,
                              "source_parameter_id": str(item.get("id") or ""),
                              "name": name, "label": str(row.get("label") or "").strip(),
                              "value": str(item.get("value") or ""),
                              "source": item.get("source") or "user",
                              "row_binding_id": row.get("binding_id") or identity}
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
        if not single_address(value) or not single_address(address):
            continue
        question["value"] = _DEVICE.sub(lambda _m: address, value, count=1)
        question["source"] = binding.get("source") or "previous"
        if isinstance(question.get("io_binding"), dict):
            question["io_binding"]["label"] = str(row.get("label") or "").strip()
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
    rows, remaining, bindings, _ = bind_answers(
        result["io_table"], parameters if isinstance(parameters, list) else [],
        bindings if isinstance(bindings, list) else [], protected_ids=protected_ids,
    )
    result["io_table"] = rows
    if isinstance(parameters, list):
        result["parameters"] = remaining
    if bindings or "io_bindings" in result:
        result["io_bindings"] = bindings
    return result
