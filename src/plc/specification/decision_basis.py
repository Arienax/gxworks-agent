# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Source checks for model-authored implementation decisions, separate from requirements."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

_QUOTE_LIMIT = 240
_TEXT = {"type": "string", "pattern": r"\S"}
_REFERENCE_FIELDS = {
    "source": {"enum": ["request", "answer", "confirmed_spec", "model_fact"]},
    "ref": {"type": "string"},
    "quote": {**_TEXT, "maxLength": _QUOTE_LIMIT},
}
_FACT_REF = r"^fact-[a-f0-9]{24}$"
_REFERENCE_SCHEMA = {"type": "object", "properties": _REFERENCE_FIELDS,
    "required": ["source", "ref"], "additionalProperties": False,
    "anyOf": [{"required": ["quote"]},
              {"properties": {"source": {"const": "model_fact"}, "ref": {"pattern": _FACT_REF}}}]}
_ROW_FIELDS = {
    "variant": _TEXT,
    "premise": _TEXT,
    "status": {"enum": ["known", "inferred", "unknown"]},
    "conclusion": {"enum": ["applicable", "conditional", "excluded"]},
    "references": {"type": "array", "items": _REFERENCE_SCHEMA},
}
DECISION_BASIS_ROW_SCHEMA = {
    "type": "object", "properties": {**_ROW_FIELDS, "applicability_conditions": {
        "type": "array", "items": {"type": "object",
            "properties": {key: _ROW_FIELDS[key] for key in ("premise", "status", "references")},
            "required": ["premise", "status", "references"], "additionalProperties": False}}},
    "required": list(_ROW_FIELDS), "additionalProperties": False,
}
DECISION_BASIS_SCHEMA = {"type": "array", "items": DECISION_BASIS_ROW_SCHEMA}
DECISION_BASIS_FIELDS = [{**dict.fromkeys(_ROW_FIELDS), "references": [dict.fromkeys(_REFERENCE_FIELDS)],
                         "applicability_conditions": [{"premise": None, "status": None,
                                                       "references": [dict.fromkeys(_REFERENCE_FIELDS)]}]}]

# Only settled spec data can substantiate a premise. Candidate prose, questions,
# suggested defaults and audit records cannot be cited as confirmed inputs.
_CONFIRMED_SECTIONS = frozenset({
    "io_table", "io_bindings", "hardware_profile", "hardware_context", "hardware_requirements",
    "timing", "user_notes", "scan_budget_ms", "scan_warning_ms",
})


def _span(value):
    return " ".join(value.split()) if isinstance(value, str) else ""


def _confirmed_value(spec, pointer):
    if pointer.startswith("/confirmed_spec/"):
        pointer = pointer[len("/confirmed_spec"):]
    if not pointer.startswith("/"):
        return None
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    if parts[0] not in _CONFIRMED_SECTIONS:
        return None
    value = spec
    for part in parts:
        if isinstance(value, Mapping):
            value = value.get(part)
        elif isinstance(value, list) and part.isascii() and part.isdecimal() and len(part) <= 10:
            index = int(part)
            value = value[index] if index < len(value) else None
        else:
            return None
    return value


def _answer_value(spec, ref):
    for parameter in spec.get("parameters") or []:
        if isinstance(parameter, Mapping) and parameter.get("id") == ref:
            return parameter.get("value")
    answers = spec.get("missing_answers")
    return answers.get(ref) if isinstance(answers, Mapping) else None


@dataclass(frozen=True)
class DecisionSources:
    request: str = ""
    confirmed_spec: Mapping | None = None
    model_facts: Mapping = field(default_factory=dict)
    answers: Mapping | None = None
    citations: Mapping = field(default_factory=dict)

    def resolve(self, reference):
        source, ref = reference["source"], reference["ref"]
        if source == "model_fact" and ref in self.citations:
            text = self.citations[ref]["text"]
            quote = _span(reference.get("quote")) or _span(text)[:_QUOTE_LIMIT].rstrip()
            if quote and quote in _span(text):
                return {"source": source, "ref": ref, "quote": quote}
            return None
        return reference if self.matches(reference) else None

    def used_citations(self, candidates):
        ids = set()
        for candidate in candidates:
            for decision in candidate.get("decision_basis", []):
                for row in [decision, *decision.get("applicability_conditions", [])]:
                    ids.update(ref["ref"] for ref in row["references"] if ref["source"] == "model_fact")
        return {identity: self.citations[identity] for identity in sorted(ids & self.citations.keys())}

    def reference_entries(self):
        """Offer copyable references into the exact displayed specification."""
        spec = self.confirmed_spec if isinstance(self.confirmed_spec, Mapping) else {}
        answer_ids = dict.fromkeys(
            row["id"] for row in spec.get("parameters") or []
            if isinstance(row, Mapping) and isinstance(row.get("id"), str))
        legacy_answers = spec.get("missing_answers")
        if isinstance(legacy_answers, Mapping):
            answer_ids.update(dict.fromkeys(legacy_answers))
        answers = self.answers or {}
        answer_ids.update(dict.fromkeys(answers))
        entries = []

        def add(source, ref, value):
            if isinstance(value, str):
                quote = _span(value)[:_QUOTE_LIMIT].rstrip()
            elif type(value) in (bool, int, float):
                quote = json.dumps(value)
            else:
                return
            entry = {"source": source, "ref": ref, "quote": quote}
            if quote and self.matches(entry):
                entries.append(entry)

        add("confirmed_spec", "/user_notes", spec.get("user_notes"))
        for ref in answer_ids:
            add("answer", ref, answers[ref] if ref in answers else _answer_value(spec, ref))
        for index, binding in enumerate(spec.get("io_bindings") or []):
            if isinstance(binding, Mapping):
                for key in ("address", "active_level", "inactive_level"):
                    add("confirmed_spec", f"/io_bindings/{index}/{key}", binding.get(key))
        for ref in ("/timing/scan_budget_ms", "/timing/scan_warning_ms", "/scan_budget_ms", "/scan_warning_ms"):
            add("confirmed_spec", ref, _confirmed_value(spec, ref))
        return entries[:48]

    def reference_prompt(self):
        entries = self.reference_entries()
        return ("\n\n# Confirmed decision references\n" + json.dumps(
            entries, ensure_ascii=False, separators=(",", ":"))) if entries else ""

    def matches(self, reference):
        source, ref = reference["source"], reference["ref"]
        quote = _span(reference.get("quote"))
        if source == "model_fact" and ref in self.citations:
            return not quote or quote in _span(self.citations[ref]["text"])
        if not quote:
            return False
        spec = self.confirmed_spec if isinstance(self.confirmed_spec, Mapping) else {}
        if source == "request":
            if not ref and quote in _span(self.request):
                return True
            from plc.generation_contract import intent_context
            return any(isinstance(row, Mapping) and not row.get("superseded_by")
                       and (not ref or row.get("id") == ref) and quote in _span(row.get("text"))
                       for row in intent_context(spec).get("requests", []))
        if source == "model_fact":
            if ref in {"profile", "knowledge"} and quote in _span(self.model_facts.get(ref)):
                return True
            # Previously verified references can be carried in the selected
            # plan without requiring another copy of their knowledge block.
            selected = spec.get("selected_approach") or {}
            if not isinstance(selected, Mapping):
                return False
            return any(isinstance(row, Mapping) and row.get("status") == "known"
                       and reference in (row.get("references") or [])
                       for row in selected.get("decision_basis") or [])
        if not ref:
            return False
        if source == "answer":
            answers = self.answers or {}
            value = answers[ref] if ref in answers else _answer_value(spec, ref)
        else:
            value = _confirmed_value(spec, ref)
        if isinstance(value, str):
            text = _span(value)
            return quote == text or ((ref in {"/user_notes", "/confirmed_spec/user_notes"} or len(text) > _QUOTE_LIMIT) and quote in text)
        return type(value) in (bool, int, float) and quote == json.dumps(value)


def review_decision_basis(value, *, path, reference_matches=None, resolve_reference=None):
    """Return applicable metadata and errors. A matching citation does not prove entailment."""
    issues = []

    def issue(location, code, message):
        issues.append({"path": location, "code": code, "message": message})

    if not isinstance(value, list):
        issue(path, "invalid_decision_basis", "decision_basis must be an array")
        return [], issues

    from jsonschema import Draft202012Validator
    validator = Draft202012Validator(DECISION_BASIS_ROW_SCHEMA)
    def resolve(reference):
        if resolve_reference is not None:
            return resolve_reference(reference)
        return reference if reference_matches is None or reference_matches(reference) else None
    reviewed = []
    for index, row in enumerate(value):
        location = f"{path}[{index}]"
        if not validator.is_valid(row):
            issue(location, "invalid_decision_basis", "expected variant, premise, status, conclusion and references")
            continue
        references = [resolved for ref in row["references"] if (resolved := resolve(ref)) is not None]
        status, conclusion = row["status"], row["conclusion"]
        if len(references) != len(row["references"]) or (status == "known" and not references):
            issue(location, "ungrounded_decision_basis", "cite an available source or mark the premise unknown or inferred")
            if status == "known":
                status = "unknown"
        conditions = []
        for condition_index, condition in enumerate(row.get("applicability_conditions", [])):
            condition_refs = [resolved for ref in condition["references"] if (resolved := resolve(ref)) is not None]
            condition_status = condition["status"]
            if (len(condition_refs) != len(condition["references"])
                    or condition_status == "known" and not condition_refs):
                issue(f"{location}.applicability_conditions[{condition_index}]",
                      "ungrounded_decision_basis", "cite the condition's own source; a rule does not confirm its preconditions")
                if condition_status == "known":
                    condition_status = "unknown"
            conditions.append({**condition, "status": condition_status, "references": condition_refs})
        if any(condition["status"] != "known" for condition in conditions):
            if conclusion != "conditional":
                issue(location, "unconfirmed_applicability_condition",
                      "unconfirmed applicability conditions cannot establish applicability or exclude a variant")
            conclusion = "conditional"
        if status != "known" and conclusion != "conditional":
            issue(location, "unconfirmed_decision_basis", "unknown or inferred premises can only give a conditional conclusion")
            conclusion = "conditional"
        reviewed.append({**row, "status": status, "conclusion": conclusion, "references": references,
                         **({"applicability_conditions": conditions} if "applicability_conditions" in row else {})})
    return reviewed, issues
