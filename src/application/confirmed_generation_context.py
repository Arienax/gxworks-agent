"""Shared engineering facts for compact and ladder_v1 generation adapters.

No provider, credentials, SDK or wire-schema selection belongs here. All callers
receive detached snapshots; private analysis prose is not an engineering fact.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, fields

from application.generation_support import (
    public_generation_ladder, public_generation_specification, public_generation_value,
)
from plc.device_identity import canonical_device, canonical_io_rows


CONFIRMED_GENERATION_REQUEST = (
    "根据已经由用户确认的规格生成完整梯形图。"
    "不得重新分析需求、提出问题或改变已确认 I/O、触点极性、参数和所选方案。"
    "输入条件的 OR 必须在同一个输出分支中表示；"
    "不得把 (A OR B) -> 同一输出 拆成多个 output branch/多个 branches 来表达。"
    "只返回一份最终 JSON；顶层对象闭合后立即结束回复，"
    "不得在同一次 completion 中自检后再重写或追加第二份完整 JSON。"
)



def project_confirmed_specification(confirmed_spec):
    """Expose the same allowlisted, canonical confirmed facts to every adapter."""
    source = dict(confirmed_spec) if isinstance(confirmed_spec, Mapping) else None
    projected = public_generation_specification(source) or {}
    bindings = source.get("io_bindings") if source is not None else None
    if isinstance(bindings, list):
        projected["io_bindings"] = [
            {key: public_generation_value(copy.deepcopy(row[key]))
             for key in ("binding_id", "role", "kind", "address", "source_parameter_id", "name")
             if key in row}
            for row in bindings if isinstance(row, Mapping)
        ]
    if isinstance(projected.get("io_table"), list):
        projected["io_table"] = canonical_io_rows(projected["io_table"])
    for row in projected.get("io_bindings", []):
        if "address" in row:
            row["address"] = canonical_device(row["address"])
    selected = projected.get("selected_approach")
    if isinstance(selected, dict):
        for key in ("name", "description", "generation_guide"):
            selected.pop(key, None)
    return projected


@dataclass(frozen=True)
class ConfirmedGenerationContext:
    """A detached engineering snapshot, independent of compact/full wire format."""
    plc_model: str
    confirmed_spec: dict
    io_bindings: list
    generation_contract: dict
    knowledge_context: str
    current_program: dict | None
    generation_request: str

    def __post_init__(self):
        for item in fields(self):
            object.__setattr__(self, item.name, copy.deepcopy(getattr(self, item.name)))

    def to_dict(self):
        return {item.name: copy.deepcopy(getattr(self, item.name)) for item in fields(self)}


def build_confirmed_generation_context(
    confirmed_spec, plc_model, *, user_requirement="", current_program=None,
    task_type="generate", evidence=None, knowledge_builder=None,
):
    """Project before routing/retrieval; first generation cannot replay Agent A.

    Edits retain the explicit delta and the existing immutable merge baseline.
    Repair/format-only callers must use their own bounded repair contexts.
    """
    if task_type not in {"generate", "edit"}:
        raise ValueError("confirmed generation context is only for generation/editing")
    projected = project_confirmed_specification(confirmed_spec)
    if not projected:
        raise ValueError("confirmed generation specification is empty")
    model = str(plc_model or "FX3U").strip().upper() or "FX3U"
    current = public_generation_ladder(current_program)
    request = (public_generation_value(str(user_requirement or ""))
               if task_type == "edit" else CONFIRMED_GENERATION_REQUEST)
    if knowledge_builder is None:
        from application.generation_context import _build_knowledge_context
        knowledge_builder = _build_knowledge_context
    knowledge = knowledge_builder(
        request, plc_model=model, task_type=task_type,
        confirmed_context=projected, evidence=public_generation_value(evidence),
    )
    selected = projected.get("selected_approach") or {}
    return ConfirmedGenerationContext(
        plc_model=model, confirmed_spec=projected,
        io_bindings=projected.get("io_bindings") or [],
        generation_contract=selected.get("generation_contract") or {},
        knowledge_context=public_generation_value(knowledge or ""),
        current_program=current, generation_request=request,
    )
