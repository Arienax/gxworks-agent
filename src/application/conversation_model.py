# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Append accepted model turns within a workbench conversation."""
from __future__ import annotations

import base64
import copy
import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace

from application.generation_wire import CONTEXT_REFERENCE_HELP, expand_context_document, structured_context_text
from application.workspace import atomic_json, canonical_hash, contained, read_json, record_id
from knowledge.evidence import estimate_tokens
from model_runtime.context_budget import model_budget
from model_runtime.provider import AssistantMessage, SystemMessage, ToolResult, UserMessage, coerce_message
from shared.model_context import model_knowledge_value


_DELTA_POLICY = """Continuation updates use JSON Pointer paths. Apply `remove` first,
then `set` in order. Set adds or replaces an object member, replaces an array
index, or appends at the array length. Unchanged values carry forward.
Expand contextual references into independent values before applying changes;
changing one value does not change another copy that previously referenced it.
`rule_changes` updates the current task instructions, keyed by their `# `
section titles, without the heading marker. Retired sections no longer apply.
An untitled section is `preamble`. A section containing only structured JSON
can be updated by its JSON paths. Common reference and language rules still apply.
`context_changes` updates the last structured project context. `context_base`
of `empty` starts a new context. With `analysis_base`, first copy only the
listed paths from the preceding analysis reply into the indicated context
paths, then apply the changes. A `{"$analysis_request":true}` value refers to
the user request immediately preceding that reply. No other analysis proposals
or assumptions become confirmed facts. `knowledge` adds applicable technical
references. Use the current confirmed spec, selected program and user changes
to implement the current task and output protocol."""
_SESSION_POLICY = """# GXWorks workbench conversation
The rules below this policy govern the first task. In a later turn, an
application user message headed "# GXWorks continuation" sets the current task.
""" + _DELTA_POLICY + "\n\n"
_KNOWLEDGE = re.compile(r"(?ms)^\[PLC KNOWLEDGE\]\n(.*?)^\[/PLC KNOWLEDGE\]")
_RULE_SECTION = re.compile(r"(?m)(?=^# )")
_RULE_DATA = {"Confirmed project specification", "Project planning context",
              "Relevant PLC model facts", "Settled input predicates (not a new requirement)",
              "Project acceptance cases", "Confirmed decision references"}
_EVIDENCE_IDENTITY_VERSION = 2
_active_turn = ContextVar("workbench_model_turn", default=None)
_log = logging.getLogger(__name__)


@contextmanager
def model_turn_scope(turn):
    token = _active_turn.set(turn)
    try:
        yield
    finally:
        _active_turn.reset(token)


def current_model_turn():
    return _active_turn.get()


def _message_record(message):
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": message.content}
    if isinstance(message, ToolResult):
        return {"role": "tool", "tool_call_id": message.call_id, "name": message.name,
                "content": message.content, "is_error": message.is_error}
    if isinstance(message, AssistantMessage):
        record = {"role": "assistant", "content": message.content}
        if message.tool_calls:
            record["tool_calls"] = [{"id": call.id, "type": "function",
                                     "function": {"name": call.name, "arguments": copy.deepcopy(call.arguments)}}
                                    for call in message.tool_calls]
        state = message._provider_fields
        origin = state.get("origin")
        if origin and (state.get("responses_items") or state.get("message") or state.get("tools")):
            record["_provider_fields"] = {
                "origin": list(origin),
                **{name: copy.deepcopy(state[name])
                   for name in ("responses_items", "effort_update", "message", "tools", "reasoning_tokens")
                   if name in state},
            }
        return record
    record = {"role": "user", "content": message.content}
    if message.images:
        record["images"] = [
            {"filename": image.filename, "media_type": image.media_type,
             "data": base64.b64encode(image.data).decode("ascii")}
            for image in message.images
        ]
    return record


def _restore_message(record):
    message = coerce_message(record)
    if isinstance(message, ToolResult):
        try:
            data = json.loads(message.content)
        except ValueError:
            return message
        return replace(message, data=data) if isinstance(data, dict) else message
    replay = record.get("_provider_fields")
    if (isinstance(message, AssistantMessage) and isinstance(replay, dict)
            and (replay.get("responses_items") or replay.get("message") or replay.get("tools"))):
        return replace(message, _provider_fields={
            **copy.deepcopy(replay), "origin": tuple(replay["origin"]),
        })
    return message


def _system_units(text):
    units = []

    def extract(match):
        try:
            value = json.loads(match[1])
        except ValueError:
            return match[0]
        if not isinstance(value, dict):
            return match[0]
        units.append(value)
        return ""

    return _KNOWLEDGE.sub(extract, text).strip(), units


def _rule_sections(rules):
    sections = {}
    text = rules.replace(CONTEXT_REFERENCE_HELP.strip(), "").strip()
    for block in _RULE_SECTION.split(text):
        if not block.strip():
            continue
        heading, _, body = block.partition("\n") if block.startswith("# ") else ("", "", block)
        title = heading[2:] or "preamble"
        key, suffix = title, 1
        while key in sections:
            suffix += 1
            key = f"{title} ({suffix})"
        body = body.strip()
        if title in _RULE_DATA:
            try:
                body = expand_context_document(json.loads(body))
            except ValueError:
                pass
        sections[key] = body
    return sections


def _pointer(path, key):
    return path + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _changes(previous, current):
    removed, updated = [], {}

    def walk(old, new, path):
        if isinstance(old, dict) and isinstance(new, dict):
            removed.extend(_pointer(path, key) for key in old if key not in new)
            for key, value in new.items():
                child = _pointer(path, key)
                if key in old:
                    walk(old[key], value, child)
                else:
                    updated[child] = value
        elif isinstance(old, list) and isinstance(new, list):
            removed.extend(_pointer(path, index) for index in range(len(old) - 1, len(new) - 1, -1))
            for index, value in enumerate(new):
                child = _pointer(path, index)
                if index < len(old):
                    walk(old[index], value, child)
                else:
                    updated[child] = value
        elif type(old) is not type(new) or old != new:
            updated[path] = new

    walk(previous, current, "")
    return {key: value for key, value in (("remove", removed), ("set", updated)) if value}


def _analysis_base(context, analysis):
    """Reference proposals only where the authoritative context can amend them."""
    spec = context.get("confirmed_spec")
    if not isinstance(spec, dict) or not analysis:
        return {}, {}
    base, references = {}, {}
    for key in ("requirements", "implementation_items", "open_questions"):
        if key in spec and isinstance(analysis.get(key), list) and analysis[key]:
            base[key] = analysis[key]
            references[_pointer("/confirmed_spec", key)] = _pointer("", key)
    selected = spec.get("selected_approach")
    if isinstance(selected, dict) and (selected_id := selected.get("approach_id")):
        for index, candidate in enumerate(analysis.get("approaches") or []):
            if isinstance(candidate, dict) and candidate.get("approach_id") == selected_id:
                base["selected_approach"] = candidate
                references["/confirmed_spec/selected_approach"] = f"/approaches/{index}"
                break
    return ({"confirmed_spec": base}, references) if references else ({}, {})


def _reference_analysis_request(value, request):
    if isinstance(value, dict):
        return {key: _reference_analysis_request(item, request) for key, item in value.items()}
    if isinstance(value, list):
        return [_reference_analysis_request(item, request) for item in value]
    return {"$analysis_request": True} if value == request else value


def _knowledge_identity(value):
    if isinstance(value, dict):
        if "citation_id" in value:
            value = {k: v for k, v in value.items() if k != "citation_id"}
        if value.get("kind") == "instruction" and isinstance(value.get("context"), dict):
            # The lookup's requested aspects can differ with identical facts.
            # Keep the applicability and the complete technical body.
            value = {**value, "context": {k: v for k, v in value["context"].items() if k != "aspects"}}
        elif value.get("kind") == "instruction_capability":
            # A previously discovered instruction may now be a selected lookup.
            value = {k: v for k, v in value.items() if k != "functions"}
    return canonical_hash(value)


def _take_new(units, known):
    result = []
    for unit in units:
        value = model_knowledge_value(unit) or unit
        identity = _knowledge_identity(value)
        if identity not in known:
            known.add(identity)
            result.append(unit)
    return result


def _knowledge_payloads(value):
    if isinstance(value, dict):
        if isinstance(value.get("knowledge"), list):
            yield value
        for key in ("context", "generation_input"):
            yield from _knowledge_payloads(value.get(key))


def _legacy_evidence_aliases(record):
    """Read earlier content hashes without changing the saved cache prefix."""
    if record.get("evidence_identity_version") == _EVIDENCE_IDENTITY_VERSION or not record["evidence"]:
        return
    recorded = set(record["evidence"])
    for message in record["messages"]:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        if message["role"] == "system":
            _, units = _system_units(content)
        elif message["role"] == "user":
            text = content.removeprefix("# GXWorks continuation\n").lstrip()
            if not text.startswith("{"):
                continue
            try:
                payload = expand_context_document(json.loads(text))
            except (ValueError, TypeError, KeyError, IndexError):
                continue
            units = (unit for section in _knowledge_payloads(payload) for unit in section["knowledge"])
        else:
            continue
        for unit in units:
            value = model_knowledge_value(unit) or unit
            if canonical_hash(value) in recorded:
                yield _knowledge_identity(value)


def _context_payload(message):
    if not isinstance(message, UserMessage) or not isinstance(message.content, str):
        return None
    if not message.content.lstrip().startswith("{"):
        return None
    try:
        payload = json.loads(message.content)
        if not isinstance(payload, dict):
            return None
        if not (payload.get("context_schema_version") == 3 or "confirmed_spec" in payload
                or "generation_input" in payload or "tool_schema" in payload):
            return None
        # Repack the current payload after filtering so its local references
        # never point at an evidence item that was removed.
        return expand_context_document(payload)
    except (ValueError, TypeError, KeyError, IndexError):
        return None


def _user_context(message, known, *, previous=None, incremental=False, analysis=None, analysis_request=None,
                  repaired_analysis=False):
    payload = _context_payload(message)
    if payload is None:
        return message, None
    changed = False
    added_knowledge = []
    for section in _knowledge_payloads(payload):
        units = section["knowledge"]
        delta = _take_new(units, known)
        added_knowledge.extend(delta)
        section["knowledge"] = delta
        changed |= len(delta) != len(units)
    # Knowledge is cumulative reference material, independent of mutable context.
    state = copy.deepcopy(payload)
    for section in _knowledge_payloads(state):
        section["knowledge"] = []
    if not incremental:
        return (replace(message, content=structured_context_text(payload)) if changed else message), state

    base, references = (previous, {}) if previous is not None else _analysis_base(state, analysis)
    delta = {"context_changes": _changes(base, state)}
    if previous is None:
        delta["context_base"] = "empty"
    if references:
        delta["analysis_base"] = references
        if repaired_analysis:
            delta["analysis_base_instruction"] = (
                "Use the corrected analysis reply. If the final analysis reply is a patch, apply its repairs by "
                "JSON Pointer to the original analysis reply before following these references. "
                "$analysis_request refers to the original requirement, before the repair turn."
            )
    if isinstance(analysis_request, str) and len(analysis_request) >= 128:
        delta["context_changes"] = _reference_analysis_request(delta["context_changes"], analysis_request)
    if added_knowledge:
        delta["knowledge"] = added_knowledge
    return replace(message, content=structured_context_text(delta)), state


class ModelTurn:
    """Record the actual provider conversation for one job, including tool replies."""

    def __init__(self, state_dir, previous_jobs, model, profile, language, *, session_id=None):
        self.state_dir = state_dir
        endpoint = profile.get("baseUrl") or profile.get("base_url")
        self.route = canonical_hash([
            model.get("profile_id"), model.get("model"), profile.get("adapter"),
            endpoint, language,
        ])
        self.cache_key = (canonical_hash([self.route, session_id])
                          if session_id and (endpoint or "").rstrip("/") == "https://api.openai.com/v1" else None)
        self.input_limit = model_budget(profile)["usable_input_tokens"]
        self.history = ()
        self.parent = None
        self.last_rules = None
        self.last_context = None
        self.last_task = None
        self.last_analysis = None
        self.last_analysis_request = None
        self.known = set()
        self.started = False
        self.pending = None
        self._restore(previous_jobs)

    def _path(self, job_id):
        # Staging already has the lifetime and cleanup of its owning job.
        return contained(self.state_dir / "staging" / record_id(job_id, "job") / ".model-turn.json", self.state_dir)

    def _restore(self, previous_jobs):
        records = []
        remaining = set(previous_jobs)
        try:
            for job in previous_jobs:
                path = self._path(job)
                if path.is_file():
                    break
            else:
                return
            while job in remaining:
                remaining.remove(job)
                record = read_json(self._path(job))
                if record["route"] != self.route:
                    return
                payload = (record.get("context") or {}).get("context") or {}
                if (record.get("task") == "gxw_existing_edit"
                        and (payload.get("selected_source") or {}).get("language") == "ladder"):
                    # These requests included the preview and a second source
                    # representation. Keep the exact prefix before that edit.
                    records.clear()
                else:
                    records.append((job, record))
                job = record["parent"]
                if job is None:
                    break
            if job is not None or not records:
                return
            history = tuple(_restore_message(row) for _, record in reversed(records) for row in record["messages"])
            if not isinstance(history[0], SystemMessage) or not isinstance(history[-1], (AssistantMessage, ToolResult)):
                return
            known = {value for _, record in records for value in record["evidence"]}
            known.update(identity for _, record in records for identity in _legacy_evidence_aliases(record))
            head, latest = records[0]
            self.history, self.parent, self.known = history, head, known
            self.last_rules = latest.get("rules")
            self.last_context = latest.get("context")
            self.last_task = latest.get("task")
            self.last_analysis = latest.get("analysis_result")
            self.last_analysis_request = latest.get("analysis_request")
        except (OSError, ValueError, TypeError, KeyError, IndexError):
            # Legacy or incomplete history still allows a standalone request.
            return

    def prepare(self, request, *, instruction_tools=False):
        if self.started:
            self.pending = None
            return request
        self.started = True
        messages = request.messages
        if ((request.tools and not instruction_tools) or len(messages) < 2 or not isinstance(messages[0], SystemMessage)
                or not isinstance(messages[-1], UserMessage)
                or not all(isinstance(item, (UserMessage, AssistantMessage)) for item in messages[1:])):
            return request

        if self.cache_key and "prompt_cache_key" not in request.options:
            request = replace(request, options={**request.options, "prompt_cache_key": self.cache_key})
        task = request.response_contract.name

        def current_inputs(known, incremental=False):
            prior = self.last_context if incremental else None
            if task == "analysis":
                return messages[1:], prior
            analysis, user = self._preceding_analysis() if incremental and prior is None else (None, None)
            message, state = _user_context(messages[1], known, previous=prior, incremental=incremental,
                                           analysis=analysis, analysis_request=user,
                                           repaired_analysis=self.last_analysis is not None)
            return (message, *messages[2:]), state if state is not None else prior

        def remember(parent, tail, known, state):
            self.pending = {"route": self.route, "parent": parent, "task": task, "rules": sections,
                            "context": state, "evidence": sorted(known),
                            "evidence_identity_version": _EVIDENCE_IDENTITY_VERSION,
                            "messages": [_message_record(message) for message in tail]}

        rules, units = _system_units(messages[0].content)
        sections = _rule_sections(rules)
        known = self.known.copy()
        if self.history:
            envelope = {"task": task}
            if isinstance(self.last_rules, dict):
                if changes := _changes(self.last_rules, sections):
                    envelope["rule_changes"] = changes
            else:
                # Older records retain their exact cache prefix and get the
                # update protocol once with the new task rules.
                envelope["instructions"] = _DELTA_POLICY + "\n\n" + rules
            if delta := _take_new(units, known):
                envelope["knowledge"] = delta
            control = UserMessage("# GXWorks continuation\n" + json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))
            inputs, state = current_inputs(known, incremental=True)
            tail = (control, *inputs)
            combined = self.history + tail
            tool_tokens = estimate_tokens(json.dumps(request.tools, ensure_ascii=False, separators=(",", ":"))) if request.tools else 0
            if self.input_limit is None or self._estimate(combined) + tool_tokens <= self.input_limit:
                remember(self.parent, tail, known - self.known, state)
                return replace(request, messages=combined)

        known = set()
        _take_new(units, known)
        inputs, state = current_inputs(known)
        tail = (SystemMessage(_SESSION_POLICY + messages[0].content), *inputs)
        remember(None, tail, known, state)
        return replace(request, messages=tail)

    def _preceding_analysis(self):
        if self.last_task == "analysis" and self.last_analysis is not None:
            return self.last_analysis, self.last_analysis_request
        if self.last_task not in (None, "analysis"):
            return None, None
        request = self.last_analysis_request
        if request is None and isinstance(self.history[-2], UserMessage):
            request = self.history[-2].content
        if request is None:
            return None, None
        from application.analysis_response import _analysis_json_payload

        try:
            value = _analysis_json_payload(self.history[-1].content)
        except ValueError:
            return None, None
        return (value, request) if isinstance(value, dict) else (None, None)

    @staticmethod
    def _estimate(messages):
        total = 0
        for message in messages:
            total += estimate_tokens(str(message.content)) + 4 + 2048 * len(getattr(message, "images", ()))
            if isinstance(message, AssistantMessage):
                total += message._provider_fields.get("reasoning_tokens") or 0
                total += sum(estimate_tokens(str(call.arguments)) for call in message.tool_calls)
        return total

    def record_tool_exchange(self, assistant, replies):
        if self.pending is None:
            return
        self.record_followup(assistant, replies)
        known = set(self.pending["evidence"])
        for reply in replies:
            _take_new(reply.data.get("knowledge", ()), known)
        self.pending["evidence"] = sorted(known)

    def record_followup(self, assistant, messages):
        if self.pending is not None:
            self.pending["messages"].extend(_message_record(message) for message in (assistant, *messages))

    def accept(self, message, task, *, analysis_result=None, analysis_request=None):
        if self.pending is None:
            return
        if not message.content or message.tool_calls:
            self.pending = None
            return
        if task == "analysis":
            from application.analysis_response import _analysis_json_payload
            from application.analysis_results import model_analysis_fields

            if analysis_request is not None:
                self.pending["analysis_request"] = analysis_request
            if isinstance(analysis_result, dict):
                self.pending["analysis_result"] = model_analysis_fields(analysis_result)
                self.pending["messages"].append(_message_record(message))
                return
            try:
                payload = _analysis_json_payload(message.content)
            except ValueError:
                self.pending = None
                return
            if not isinstance(payload, dict):
                self.pending = None
                return
            projected = model_analysis_fields(payload)
            if projected != payload:
                message = replace(message, content=json.dumps(projected, ensure_ascii=False, separators=(",", ":")),
                                  _provider_fields={})
        self.pending["messages"].append(_message_record(message))

    def save(self, job_id):
        if self.pending and self.pending["messages"][-1]["role"] in {"assistant", "tool"}:
            try:
                atomic_json(self._path(job_id), self.pending)
            except OSError:
                _log.warning("Unable to save model continuation for job %s", job_id, exc_info=True)
