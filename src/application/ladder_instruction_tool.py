# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Read exact operand and behavior facts within one generation's CPU scope."""
from __future__ import annotations

import json
import re

from agent_runtime.messages import ToolResult
from model_runtime.request_policy import resolve_request
from model_runtime.runtime_profile import materialize_runtime_profile


INSTRUCTION_LOOKUP_TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_plc_instructions",
        "description": (
            "批量查证当前 CPU 的梯形图指令，返回原生写法、参数、适用条件和行为。"
            "查询拟使用但缺少写法的指令，连同其配套操作一次提交。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "check_cpu_version": {"type": "boolean", "description": "用户明确要求核对 CPU 固件版本时才启用。"},
                "opcodes": {"type": "array", "minItems": 1, "maxItems": 12, "uniqueItems": True,
                            "items": {"type": "string", "minLength": 1, "maxLength": 32}},
            },
            "required": ["opcodes"],
            "additionalProperties": False,
        },
    },
}

INSTRUCTION_LOOKUP_PROMPT = """
# Instruction lookup
拟用指令缺少适用于本型号的原生写法时，先用 lookup_plc_instructions 一次查询相关助记符及配套指令。查证轮只提交工具调用。资料齐备后按原程序协议生成，已提供的事实直接沿用。缺项保留为未知，选择有资料支持的可行写法。
"""

_ASPECTS = ("arity", "order", "roles", "types", "devices", "operand_range",
            "operand_condition", "execution", "effects", "constraints")
_OPCODE = re.compile(r"[A-Za-z0-9_.$+*/<>!=\-]{1,32}")


def instruction_tool_for(provider, *, model_name, plc_model, options, stream, on_stage=None, tools=None):
    """Use the same capability contract as the ordinary model request."""
    try:
        api_key = getattr(provider, "api_key", None)
        profile = materialize_runtime_profile(getattr(provider, "profile", {}), api_key=api_key, model=model_name)
        capability = profile.contract.capabilities.get("tools")
        if capability is None or capability.status not in {"supported", "conditional"}:
            return None
        resolve_request(profile, None, model=profile.model, api_key=api_key,
                        protocol={**options, "tools": list(tools or (INSTRUCTION_LOOKUP_TOOL,)),
                                  "tool_choice": "auto", "stream": stream})
    except (TypeError, ValueError):
        return None
    return LadderInstructionLookup(plc_model, on_stage=on_stage)


class LadderInstructionLookup:
    def __init__(self, plc_model, *, on_stage=None):
        self.plc_model = plc_model
        self.on_stage = on_stage
        self.receipts = []
        self.knowledge = []
        self.citations = {}
        self._seen = set()

    def remember(self, units):
        from application.conversation_model import _take_new
        _take_new(units, self._seen)

    def remember_identities(self, identities):
        self._seen.update(identities)

    def __call__(self, call):
        arguments = call.arguments
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = None
        names = arguments.get("opcodes") if isinstance(arguments, dict) and not set(arguments) - {"opcodes", "check_cpu_version"} else None
        if (call.name != INSTRUCTION_LOOKUP_TOOL["function"]["name"]
                or not isinstance(names, list) or not 1 <= len(names) <= 12
                or any(not isinstance(name, str) or not _OPCODE.fullmatch(name) for name in names)
                or ('check_cpu_version' in arguments and type(arguments['check_cpu_version']) is not bool)):
            data = {"error": "invalid_instruction_lookup"}
        else:
            if self.on_stage:
                self.on_stage("instruction_lookup", "正在查证当前型号的指令写法")
            try:
                names = list(dict.fromkeys(name.upper() for name in names))
                data = self._lookup(names, check_cpu_version=True) if arguments.get('check_cpu_version') else self._lookup(names)
            except (LookupError, OSError, ValueError):
                data = {"error": "instruction_lookup_unavailable"}
        from model_runtime.usage import tool_executed
        tool_executed(INSTRUCTION_LOOKUP_TOOL["function"]["name"], "error" not in data)
        self.receipts.append({key: value for key, value in data.items() if key != "knowledge"})
        return ToolResult(call.id, call.name, json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                          data, "error" in data)

    def _lookup(self, names, *, check_cpu_version=False):
        from application.conversation_model import _take_new
        from knowledge.instruction_facts import retrieve_instruction_facts
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        from shared.model_context import technical_knowledge_results

        targets = []
        for name in names:
            form = DEFAULT_INSTRUCTION_REGISTRY.resolve_catalog_form(name, cpu=self.plc_model)
            targets.append({"opcode": name, "base_opcode": form.base_mnemonic if form else name})
        rows, _ = retrieve_instruction_facts(
            "", plc_model=self.plc_model, task_type="generate", targets=targets,
            language="ladder", aspects=list(_ASPECTS), compact_sources=True,
        )
        origins = {}
        units = [unit for result in technical_knowledge_results(rows, origins=origins) for unit in result["knowledge"]]
        missing = set(names)
        for unit in units:
            value = unit.get("value", {})
            if unit.get("kind") == "instruction" and value.get("parameter_tables"):
                missing.difference_update({unit.get("target"), *(form["opcode"] for form in value.get("forms", []))})
        from application.compact_bindings import instruction_form_units
        forms = instruction_form_units(self.plc_model, units, targets=names)
        units.extend(forms)
        from shared.cpu_version_projection import project_cpu_version_knowledge
        units = project_cpu_version_knowledge(units, requested=check_cpu_version)
        new = _take_new(units, self._seen)
        from plc.specification.fact_citations import cite_units, fact_version
        new, citations = cite_units(new, model=self.plc_model, language="ladder", version=fact_version(),
                                  path="/lookup_plc_instructions/knowledge", origins=origins)
        self.citations.update(citations)
        self.knowledge.extend(new)
        return {"plc_model": self.plc_model, "language": "ladder", "requested": names,
                "missing_operand_tables": sorted(missing),
                "unresolved_forms": sorted(set(names) - {form["opcode"] for form in forms}),
                "knowledge": new}
