"""Opt-in, auditable policies for automatically injected PLC context.

No model calls, file I/O, retrieval imports, or PLC semantics live here.
``legacy`` is the release default. Other policies are experiments, not evidence
that less context produces a better program. Explicit Agent/MCP tools are not
blocked by these policies; this is not a document permission mechanism.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import os
import re
import sys
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Tuple

POLICY_ENV = "GXWORKS_CONTEXT_POLICY"
POLICY_VERSION = 1
POLICY_NAMES = ("legacy", "minimal", "manual", "examples", "combined", "adaptive")


class ContextPolicyError(ValueError):
    """An invalid policy or an unexpected upstream prompt layout."""


@dataclass(frozen=True)
class ContextPolicy:
    name: str = "legacy"

    def __post_init__(self) -> None:
        if self.name not in POLICY_NAMES:
            raise ContextPolicyError("Unknown context policy; expected: " + ", ".join(POLICY_NAMES))

    @property
    def legacy(self) -> bool:
        return self.name == "legacy"

    @property
    def examples(self) -> bool:
        return self.name in ("legacy", "examples", "combined")

    @property
    def manuals(self) -> str:
        if self.name in ("legacy", "manual", "combined"):
            return "automatic"
        return "adaptive" if self.name == "adaptive" else "off"

    def snapshot(self) -> Dict[str, Any]:
        return {"name": self.name, "version": POLICY_VERSION}


def resolve_context_policy(value: Any = None) -> ContextPolicy:
    if value is None:
        active = _policy.get()
        if active is not None:
            return active
        value = os.environ.get(POLICY_ENV, "legacy")
    if isinstance(value, ContextPolicy):
        return value
    if isinstance(value, Mapping):
        if set(value) != {"name", "version"} or type(value["version"]) is not int or value["version"] != POLICY_VERSION:
            raise ContextPolicyError("Unsupported context policy snapshot")
        value = value["name"]
    if not isinstance(value, str):
        raise ContextPolicyError("Context policy must be a name or a versioned snapshot")
    return ContextPolicy(value.strip().lower())


_policy: ContextVar[Optional[ContextPolicy]] = ContextVar("plc_prompt_context_policy", default=None)
_audit: ContextVar[Optional["ContextAudit"]] = ContextVar("plc_prompt_context_audit", default=None)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ContextAudit:
    """Metadata only: never store prompts, document text, paths, or credentials.

    A collector belongs to one workflow. Context variables isolate collectors
    between threads/tasks when each workflow enters its own policy scope.
    Character counts are NOT tokenizer counts and section sizes are NOT additive
    (a final system-prompt measurement can contain previously measured sections).
    """
    def __init__(self, on_request: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        self._sections: List[Dict[str, Any]] = []
        self._requests: List[Dict[str, Any]] = []
        self._on_request = on_request
        self._dropped = 0

    def add(self, section: str, text: str, *, status: str, reason: str, source: str) -> None:
        # Labels are application-owned identifiers, not arbitrary exception text.
        for identifier in (section, status, reason, source):
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", identifier):
                raise ContextPolicyError("Audit labels must be fixed, bounded identifiers")
        if len(self._sections) >= 256:
            self._dropped += 1
            return
        self._sections.append({"section": section, "source": source, "status": status,
                               "reason": reason, "chars": len(text), "sha256": _digest(text)})

    def request(self, messages: Any) -> Dict[str, Any]:
        summary: List[Dict[str, Any]] = []
        for message in messages:
            if isinstance(message, Mapping):
                role, content = message.get("role"), message.get("content")
                images = ()
            else:
                role, content = getattr(message, "role", None), getattr(message, "content", None)
                images = getattr(message, "images", ()) or ()
            role = role if role in ("system", "developer", "user", "assistant", "tool") else "unknown"
            text_parts: List[str] = []
            image_count = len(images) if isinstance(images, (list, tuple)) else 0
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, (list, tuple)):
                for part in content:
                    if isinstance(part, Mapping):
                        if part.get("type") in ("text", "input_text") and isinstance(part.get("text"), str):
                            text_parts.append(part["text"])
                        elif part.get("type") in ("image_url", "input_image"):
                            image_count += 1
            text = "".join(text_parts)
            summary.append({"role": role, "text_chars": len(text), "images": image_count,
                            "sha256": _digest(text)})
        report = {"schema_version": 1, "policy": resolve_context_policy().snapshot(),
                  "request_index": len(self._requests) + 1,
                  "messages": summary, "message_text_chars": sum(x["text_chars"] for x in summary),
                  "sections": [dict(x) for x in self._sections], "dropped_sections": self._dropped,
                  "measurement": "characters_not_tokens", "section_counts_are_additive": False}
        self._sections.clear()
        self._dropped = 0
        self._requests.append(report)
        if self._on_request:
            try:
                # The sink cannot mutate the collector's retained data.
                import copy
                self._on_request(copy.deepcopy(report))
            except Exception:
                # Optional presentation failure must not change model execution.
                # Deliberately do not log arbitrary exception messages.
                print("Context audit sink unavailable", file=sys.stderr)
        return self.snapshot()["requests"][-1]

    def snapshot(self) -> Dict[str, Any]:
        import copy
        return copy.deepcopy({"requests": self._requests, "pending_sections": self._sections,
                              "dropped_sections": self._dropped})


@contextmanager
def context_policy_scope(value: Any = None, *, audit: Optional[ContextAudit] = None) -> Iterator[ContextPolicy]:
    """Freeze once; nested API repair/fallback scopes inherit the same policy."""
    selected = resolve_context_policy(value)
    previous = _policy.get()
    inherited_audit = _audit.get() if previous is None or previous == selected else None
    policy_token = _policy.set(selected)
    audit_token = _audit.set(audit if audit is not None else inherited_audit)
    try:
        yield selected
    finally:
        _audit.reset(audit_token)
        _policy.reset(policy_token)


def audit_section(section: str, text: str = "", *, status: str = "included",
                  reason: str = "required", source: str = "application") -> None:
    collector = _audit.get()
    if collector is not None:
        collector.add(section, text, status=status, reason=reason, source=source)


def audit_retrieval_fragment(result: Mapping[str, Any], block: str, *, included: bool = True) -> None:
    """Record corpus identifiers and hashes, never the chunk's prose or URL."""
    if _audit.get() is None:
        return

    def identifier(value: Any, fallback: str) -> str:
        text = str(value) if isinstance(value, (str, int)) else ""
        if re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", text):
            return text
        return fallback + ("_" + _digest(text)[:16] if text else "")

    source = identifier(result.get("manual_id"), "unknown_source")
    chunk = identifier(result.get("id", result.get("chunk_id")), "unknown_chunk")
    audit_section("manual_chunk:" + chunk, block,
                  status="included" if included else "excluded",
                  reason="retrieved_chunk" if included else "context_budget",
                  source=source)


def audit_request(messages: Any) -> None:
    collector = _audit.get()
    if collector is not None:
        collector.request(messages)


# Conservative, explicit signals. No model-reported confidence is consulted.
# This gate does NOT validate an instruction, authorize equipment actions, or
# claim all vendor-specific facts can be detected with regular expressions.
_SPECIAL = re.compile(r"(?<![A-Za-z0-9_])(?:[MD]8\d{3}|S[MD]\d+)(?![A-Za-z0-9_])", re.I)
_MODULE = re.compile(r"(?<![A-Za-z0-9_])(?:FX[0-9A-Z]+|Q[0-9A-Z]+|L[0-9A-Z]+)-[0-9A-Z-]+", re.I)
_OPCODE = re.compile(r"(?<![A-Za-z0-9_])(?:D?PLSY|PLSV|PLSR|D?DRVI|D?DRVA|ZRN|DSZR|DVIT|PID|RS2|RD3A|WR3A)(?![A-Za-z0-9_])", re.I)
_BUFFER_OP = re.compile(r"(?<![A-Za-z0-9_])(?:TO|FROM|RS)(?![A-Za-z0-9_])(?=\s*(?:指令|instruction|[KHMDXY]\d))", re.I)
_ERROR = re.compile(r"(?<![A-Za-z0-9_])C\d{4}(?![A-Za-z0-9_])|错误码|编译错误|buffer memory|缓冲存储器|VAR_IN_OUT|\bABI\b", re.I)
_EXPLICIT = re.compile(r"手册|查证|查阅|查询指令|\bmanual(?!\s+(?:mode|control|operation)\b)|\bdatasheet\b|\bdocumentation\b|verify instruction", re.I)


def manual_lookup_decision(query: str) -> Tuple[bool, str]:
    policy = resolve_context_policy()
    if policy.manuals == "off":
        return False, "disabled_by_policy"
    if not query.strip():
        return False, "empty_query"
    if policy.manuals == "automatic":
        return True, "automatic_policy"
    for pattern, reason in ((_EXPLICIT, "explicit_lookup"), (_MODULE, "module_reference"),
                            (_SPECIAL, "special_device"), (_OPCODE, "specialized_instruction"),
                            (_BUFFER_OP, "module_instruction"),
                            (_ERROR, "diagnostic_or_interface")):
        if pattern.search(query):
            return True, reason
    return False, "no_lookup_signal"


def _remove_between(text: str, start: str, end: str, section: str, replacement: str = "") -> str:
    # Do not silently fall back to a large prompt if an upstream edit moves a marker.
    if text.count(start) != 1 or text.count(end) != 1:
        raise ContextPolicyError("Upstream prompt layout changed for " + section)
    first, last = text.index(start), text.index(end)
    if first >= last:
        raise ContextPolicyError("Reversed prompt markers for " + section)
    audit_section(section, text[first:last], status="excluded", reason="controlled_baseline", source="base_prompt")
    return text[:first] + replacement + text[last:]


_SPEC_AUTHORITY = ("\n# Confirmed specification controls behavior\n"
                   "Use the confirmed approach, canonical I/O and declared initialization/stop behavior. "
                   "Do not invent addresses, outputs, alarms, reset behavior or hardware. "
                   "Examples are optional references, never permission to replace the specification. "
                   "Retain output-schema, validator and approval boundaries.\n\n")


def select_base_prompt(original: str, target_mode: str) -> str:
    """Keep legacy byte-for-byte; controlled arms share the same contract base.

    Inline schema/operand examples remain: they specify the private protocol.
    The large unconditional control-program demonstrations do not remain.
    ST/GXW semantics and validators are deliberately not reimplemented here.
    """
    if resolve_context_policy().legacy:
        audit_section("base_prompt", original, reason="legacy", source="base_prompt")
        return original
    if target_mode == "ladder":
        result = _remove_between(original, "### 二、 经典多模范例（Few-Shot Skill）",
                                 "# ✅ 输出前自检清单", "base_control_examples")
        result = _remove_between(result, "### FX3U 的 M8029 正例：同一 rung 双 branch",
                                 "### 一、 JSON Schema 协议架构规范", "base_motion_demonstration",
                                 "M8029 的公共使能条件只放 shared_inputs；定位指令分支 inputs 为空；"
                                 "定位指令是该分支最后一个 output；M8029 是下一 branch 的第一个触点。\n\n")
        # An unconfirmed implementation preference is not a hard PLC constraint.
        result = "\n".join(line for line in result.split("\n")
                           if not line.startswith("3. **自动步进化**："))
    elif target_mode == "st":
        result = _remove_between(original, "# 分析流程（内部）", "# 【严格遵守的语法与类型规范】",
                                 "st_automatic_completion")
        result = _remove_between(result, "# 【工业常识模式库】", "# 【输出前自检清单】", "st_pattern_examples")
        result = _remove_between(result, "# 范例\n", "# 【最终输出约束】", "st_full_example")
        result = "\n".join(line for line in result.split("\n")
                           if not line.startswith("5. □ 步进流程从初始化"))
    else:
        # Native FBD uses its own object contract, not these text prompts.
        audit_section("base_prompt", original, reason="separate_backend", source="base_prompt")
        return original
    result += _SPEC_AUTHORITY
    audit_section("base_prompt", result, reason="controlled_baseline", source="base_prompt")
    return result


def controlled_dynamic_prompt(classification: Mapping[str, Any], library: Mapping[str, Any],
                              target_mode: str, plc_model: Optional[str], *,
                              char_budget: int = 6500, max_examples: int = 2) -> Optional[str]:
    """None means use the untouched legacy assembler; an empty string is valid.

    Controlled arms differ only by retrieved manuals / eligible examples.
    Auto-completion rules, design patterns and duplicate output instructions are
    not silently reintroduced. The confirmed-approach contract remains upstream.
    Untagged legacy examples are FX3U Ladder examples, never cross-vendor ST.
    """
    policy = resolve_context_policy()
    if policy.legacy:
        return None
    audit_section("dynamic_advice", status="excluded", reason="controlled_baseline", source="pattern_library")
    if not policy.examples:
        audit_section("dynamic_examples", status="excluded", reason="disabled_by_policy", source="pattern_library")
        return ""
    if char_budget < 0 or max_examples < 0:
        raise ContextPolicyError("Example limits cannot be negative")
    model = str(plc_model or "").upper()
    matched = set(classification.get("matched_ids") or ())
    candidates = [entry for entry in library.get("examples", ())
                  if entry.get("id") in matched
                  and entry.get("target_mode", "ladder") == target_mode
                  and model in entry.get("plc_models", ["FX3U"])]
    candidates.sort(key=lambda item: (item.get("priority", 999), str(item.get("id", ""))))
    parts = []
    header = "\n# Relevant control examples (adapt to the confirmed specification)\n"
    for entry in candidates:
        block = str(entry.get("header", "")) + "\n" + str(entry.get("content", ""))
        candidate = header + "\n\n".join(parts + [block])
        entry_id = str(entry.get("id", "example"))
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", entry_id):
            entry_id = "example"
        if len(parts) >= max_examples or len(candidate) > char_budget:
            audit_section(entry_id, block, status="excluded", reason="example_budget", source="pattern_library")
            continue
        parts.append(block)
        audit_section(entry_id, block, reason="matched_example", source="pattern_library")
    result = header + "\n\n".join(parts) if parts else ""
    audit_section("dynamic_examples", result, status="included" if result else "empty",
                  reason="matched_examples" if result else "no_eligible_example", source="pattern_library")
    return result
