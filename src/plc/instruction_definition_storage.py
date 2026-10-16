"""Lossless common definitions plus literal form/CPU field differences.

Storage factoring does not infer applicability or inherit verification. Every
expanded owner has exactly the facts/statuses it had before factoring. Runtime
ownership stays in InstructionSpec and the existing Registry.
"""
from __future__ import annotations

import copy
import json
from collections import Counter, defaultdict
from collections.abc import Mapping

_REMOVED = {"$remove": True}


def _diff(base, changed):
    if isinstance(base, Mapping) and isinstance(changed, Mapping):
        result = {key: copy.deepcopy(_REMOVED) for key in base if key not in changed}
        for key, value in changed.items():
            if key not in base:
                result[key] = copy.deepcopy(value)
            elif base[key] != value:
                result[key] = _diff(base[key], value)
        return result
    return copy.deepcopy(changed)


def _apply(base, changes):
    if not isinstance(base, Mapping) or not isinstance(changes, Mapping):
        return copy.deepcopy(changes)
    result = copy.deepcopy(dict(base))
    for key, value in changes.items():
        if value == _REMOVED:
            result.pop(key, None)
        elif key in result:
            result[key] = _apply(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _owner_tokens(value, model, opcode, *, restore=False):
    if isinstance(value, list):
        return [_owner_tokens(item, model, opcode, restore=restore) for item in value]
    if not isinstance(value, Mapping):
        return value
    # Review is tied to a literal CPU/form, including its source receipt. Adding
    # an owner to an applicability profile must never retarget that review.
    if value.get("status") == "source_verified" and "dimension" in value:
        return copy.deepcopy(dict(value))
    result = {}
    for key, item in value.items():
        if key == "target_model" and item == ("$target_model" if restore else model):
            result[key] = model if restore else "$target_model"
        elif key == "opcode" and item == ("$form" if restore else opcode):
            result[key] = opcode if restore else "$form"
        elif key == "scope" and isinstance(item, Mapping):
            result[key] = copy.deepcopy(dict(item))
            if item.get("models") == (["$target_model"] if restore else [model]):
                result[key]["models"] = [model] if restore else ["$target_model"]
            if item.get("forms") == (["$form"] if restore else [opcode]):
                result[key]["forms"] = [opcode] if restore else ["$form"]
        else:
            result[key] = _owner_tokens(item, model, opcode, restore=restore)
    return result


def compact_definitions(payload, *, base_forms=None):
    """Factor exact equality only; verification changes remain explicit diffs."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("entries"), list):
        raise ValueError("Definition entries are required")
    families, seen = defaultdict(list), set()
    for entry in payload["entries"]:
        if not isinstance(entry, Mapping) or any(not isinstance(entry.get(key), str) or not entry[key]
                                                 for key in ("opcode", "target_model")):
            raise ValueError("Definition owner needs a literal form and model")
        family = (entry.get("vendor", "mitsubishi"), (base_forms or {}).get(entry["opcode"], entry["opcode"]))
        marker = (family[0], entry["opcode"], entry["target_model"])
        if marker in seen:
            raise ValueError("Duplicate definition owner")
        seen.add(marker)
        value = {k: copy.deepcopy(v) for k, v in entry.items() if k not in {"vendor", "opcode", "target_model"}}
        value["facts"] = {fact["id"]: copy.deepcopy(fact) for fact in value.get("facts", [])}
        if len(value["facts"]) != len(entry.get("facts", [])):
            raise ValueError("Duplicate fact ID cannot be factored")
        families[family].append((entry["opcode"], entry["target_model"], _owner_tokens(value, entry["target_model"], entry["opcode"])))
    model_sets = sorted({tuple(sorted({m for f, m, _ in rows if f == opcode})) for rows in families.values()
                         for opcode in {f for f, _, _ in rows}})
    profiles = {models: "models_" + str(i + 1) for i, models in enumerate(model_sets)}
    definitions = []
    for (vendor, base), rows in sorted(families.items()):
        by_form = defaultdict(list)
        for opcode, model, value in rows:
            by_form[opcode].append((model, value))
        defaults = {}
        for opcode, values in by_form.items():
            counts = Counter(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for _, value in values)
            defaults[opcode] = json.loads(counts.most_common(1)[0][0])
        common = defaults[base] if base in defaults else defaults[sorted(defaults)[0]]
        owners = defaultdict(list)
        for opcode, values in sorted(by_form.items()):
            owners[profiles[tuple(sorted(model for model, _ in values))]].append(opcode)
        overrides = defaultdict(lambda: {"forms": []})
        for opcode, values in sorted(by_form.items()):
            for model, value in values:
                diff = _diff(defaults[opcode], value)
                if diff:
                    # Group only equal patches for the same model. Applying a
                    # union of two independent selector sets would invent owners.
                    marker = (model, json.dumps(diff, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                    overrides[marker]["forms"].append(opcode)
        definitions.append({"vendor": vendor, "base_opcode": base, "common": common,
                            "owners": [{"forms": forms, "model_profile": profile} for profile, forms in sorted(owners.items())],
                            "form_diffs": {f: _diff(common, value) for f, value in sorted(defaults.items()) if value != common},
                            "model_diffs": [{"forms": row["forms"], "model": model, "changes": json.loads(changes)}
                                            for (model, changes), row in sorted(overrides.items())]})
    result = {k: copy.deepcopy(v) for k, v in payload.items() if k not in {"entries", "schema_version"}}
    result.update(schema_version=2, model_profiles={p: list(models) for models, p in profiles.items()},
                  definitions=definitions, serialization="common+form_diff+model_diff-v1",
                  verification_inheritance=False)
    return result


def expand_definitions(payload):
    """Resolve the stored diffs at the existing Registry loading boundary."""
    if not isinstance(payload, Mapping):
        raise ValueError("Definition document must be an object")
    if payload.get("schema_version") == 1:
        return copy.deepcopy(payload)
    if (payload.get("schema_version") != 2 or payload.get("serialization") != "common+form_diff+model_diff-v1"
            or payload.get("verification_inheritance") is not False):
        raise ValueError("Unsupported definition diff format")
    entries, seen = [], set()
    profiles = payload.get("model_profiles", {})
    if not isinstance(profiles, Mapping) or not isinstance(payload.get("definitions"), list):
        raise ValueError("Invalid definition profiles or families")
    for definition in payload.get("definitions", []):
        owned = set()
        for owner in definition["owners"]:
            models = profiles.get(owner["model_profile"])
            if (not isinstance(models, list) or not models or len(set(models)) != len(models)
                    or any(not isinstance(m, str) or not m for m in models)):
                raise ValueError("Invalid model applicability profile")
            forms = owner.get("forms")
            if (not isinstance(forms, list) or not forms or len(set(forms)) != len(forms)
                    or any(not isinstance(f, str) or not f for f in forms)):
                raise ValueError("Invalid form applicability selector")
            owned.update((f, m) for f in forms for m in models)
        overrides_seen = set()
        for override in definition.get("model_diffs", []):
            for form in override["forms"]:
                marker = (form, override["model"])
                if marker not in owned or marker in overrides_seen:
                    raise ValueError("Overlapping or unowned model difference")
                overrides_seen.add(marker)
        if set(definition.get("form_diffs", {})) - {f for f, _ in owned}:
            raise ValueError("Unowned form difference")
        for owner in definition["owners"]:
            models = profiles.get(owner["model_profile"])
            for opcode in owner["forms"]:
                value = _apply(definition["common"], definition.get("form_diffs", {}).get(opcode, {}))
                for model in models:
                    marker = (definition["vendor"], opcode, model)
                    if marker in seen:
                        raise ValueError("Duplicate definition owner")
                    seen.add(marker)
                    resolved = copy.deepcopy(value)
                    for override in definition.get("model_diffs", []):
                        if model == override["model"] and opcode in override["forms"]:
                            resolved = _apply(resolved, override["changes"])
                    resolved = _owner_tokens(resolved, model, opcode, restore=True)
                    resolved["facts"] = list(resolved.get("facts", {}).values())
                    entries.append({"vendor": definition["vendor"], "opcode": opcode, "target_model": model, **resolved})
    return {**{k: copy.deepcopy(v) for k, v in payload.items() if k not in {
        "schema_version", "model_profiles", "definitions", "serialization", "verification_inheritance",
    }}, "schema_version": 1, "entries": sorted(entries, key=lambda e: (e["target_model"], e["opcode"]))}
