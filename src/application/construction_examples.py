"""Opt-in, fixed construction examples built with the canonical PLC IR.

This is a prompt-only experiment. Example devices never enter the project spec,
retrieval query, instruction requirements or device allocator. Derived IR fields
are owned by plc.ir, not hand-maintained in a second schema.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os

ENV_NAME = "GXWORKS_CONSTRUCTION_EXAMPLES"
PACK_VERSION = "routed_construction/2"
# Applicability is explicit; do not relabel an FX example as another CPU's facts.
SUPPORTED_MODELS = ("FX3U",)


def resolve_construction_examples(value: bool | None = None) -> bool:
    """Per-call bool wins over the process opt-in; an absent setting is off."""
    if value is not None:
        if not isinstance(value, bool):
            raise TypeError("construction_examples must be bool or None")
        return value
    raw = os.environ.get(ENV_NAME, "").strip().casefold()
    if raw in {"", "0", "false", "off", "no"}:
        return False
    if raw in {"1", "true", "on", "yes"}:
        return True
    raise ValueError(f"{ENV_NAME} must be 0/1, false/true, off/on or no/yes")


@dataclass(frozen=True)
class ConstructionExampleBlock:
    requested: bool
    plc_model: str
    text: str = ""
    example_ids: tuple[str, ...] = ()
    ir_hashes: tuple[str, ...] = ()
    reason: str = "disabled"
    route: dict | None = None

    def manifest(self) -> dict:
        """Metadata only, safe to attach to the existing generation handoff."""
        return {
            "requested": self.requested,
            "enabled": bool(self.text),
            "status": "included" if self.text else "excluded",
            "reason": self.reason,
            "pack_version": PACK_VERSION,
            "plc_model": self.plc_model,
            "example_ids": list(self.example_ids),
            "ir_sha256": list(self.ir_hashes),
            "chars": len(self.text),
            "sha256": hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
            "route": copy.deepcopy(self.route) if isinstance(self.route, dict) else None,
        }


def _contact(kind, address):
    return {"type": kind, "address": address}


def _app(opcode, *operands):
    return {"type": "APP_INSTR", "opcode": opcode, "operands": list(operands)}


def _rung(number, *branches, shared=()):
    return {
        "rung_id": number,
        "header_element": None,
        "shared_inputs": list(shared),
        "branches": [
            {"branch_id": i, "y_offset_level": i - 1, "inputs": inputs, "outputs": outputs}
            for i, (inputs, outputs) in enumerate(branches, start=1)
        ],
    }


def _definitions():
    """Source networks for small, independent examples, not scenario patches."""
    no = lambda address: _contact("NO", address)
    nc = lambda address: _contact("NC", address)
    coil = lambda address: _contact("COIL", address)
    return (
        (
            "compound_condition",
            "输入位均按位值判断。Y0=(X0 AND X1) OR (X2 AND NOT X3)，无保持状态。",
            {"X0": "条件A", "X1": "条件B", "X2": "条件C", "X3": "禁止条件", "Y0": "组合输出"},
            [_rung(1, ([{"type": "parallel_block", "branches": [
                [no("X0"), no("X1")], [no("X2"), nc("X3")],
            ]}], [coil("Y0")]))],
        ),
        (
            "reset_dominant_hold",
            "X0=1请求启动，X1=1请求停止，停止优先。M0初始为0；启动后保持，停止清除。Y0跟随M0。输入和初始条件均已明确。",
            {"X0": "启动请求", "X1": "停止请求", "M0": "保持状态", "Y0": "运行输出"},
            [
                _rung(1, ([{"type": "parallel_block", "branches": [
                    [no("X0")], [no("M0")],
                ]}], [coil("M0")]), shared=[nc("X1")]),
                _rung(2, ([no("M0")], [coil("Y0")])),
            ],
        ),
        (
            "shared_permit_fanout",
            "X0是公共使能。使能且X1=1时，Y0和M0同时ON；使能且X2=1、X3=0时Y1为ON。其余情况对应输出OFF，不保持。",
            {"X0": "公共使能", "X1": "分支A", "X2": "分支B", "X3": "分支B禁止", "Y0": "输出A", "M0": "同步标志", "Y1": "输出B"},
            [_rung(1,
                ([no("X1")], [coil("Y0"), coil("M0")]),
                ([no("X2"), nc("X3")], [coil("Y1")]),
                shared=[no("X0")],
            )],
        ),
        (
            "edge_and_level",
            "X0从0变1时M0仅ON一个扫描周期；Y0持续跟随X0，Y1跟随该单扫描脉冲。观察从X0=0开始的扫描序列。",
            {"X0": "输入信号", "M0": "上升沿脉冲", "Y0": "电平输出", "Y1": "事件输出"},
            [
                _rung(1, ([_contact("P", "X0")], [coil("M0")])),
                _rung(2, ([no("X0")], [coil("Y0")])),
                _rung(3, ([no("M0")], [coil("Y1")])),
            ],
        ),
        (
            "calculate_then_compare",
            "已选16位ADD，D100为0到100、D202为0到200。X0=1时先算D200=D100+2，再令Y0表示D200>D202；X0=0时Y0为OFF，D200不更新。",
            {"X0": "计算使能", "D100": "基础值", "D200": "计算结果", "D202": "比较阈值", "Y0": "比较输出"},
            [
                _rung(1, ([no("X0")], [_app("ADD", "D100", "K2", "D200")])),
                _rung(2, ([no("X0"), {"type": "COMPARE", "expression": "> D200 D202"}], [coil("Y0")])),
            ],
        ),
        (
            "counter_reset_priority",
            "已选C0普通计数器，初始为0。X0的每个上升沿计一次，累计3次后Y0为ON；X1=1时复位C0并禁止计数，Y0同扫描变OFF。复位优先。",
            {"X0": "计数输入", "X1": "复位请求", "C0": "累计计数", "Y0": "达到数量"},
            [
                _rung(1, ([no("X1")], [_app("RST", "C0")])),
                _rung(2, ([nc("X1"), _contact("P", "X0")], [{"type": "COUNTER", "address": "C0", "value": "K3"}])),
                _rung(3, ([no("C0")], [coil("Y0")])),
            ],
        ),
    )


_ROUTING_METADATA = {
    # Only high-confidence mappings are active in this first router pass.
    # Generic direct_logic examples intentionally have no primary key.
    "compound_condition": {},
    "reset_dominant_hold": {"primary_structures": ["self_hold"]},
    "shared_permit_fanout": {},
    "edge_and_level": {
        "primary_structures": ["edge_trigger"],
        "primary_execution_semantics": ["RISING_EDGE", "FALLING_EDGE"],
    },
    "calculate_then_compare": {},
    "counter_reset_priority": {"primary_structures": ["hardware_counter"]},
}


def _route_candidates():
    return [
        {"id": identifier, **copy.deepcopy(_ROUTING_METADATA.get(identifier, {}))}
        for identifier, _requirement, _comments, _rungs in _definitions()
    ]


@lru_cache(maxsize=1)
def _ir_examples() -> tuple[dict, ...]:
    # Lazy: disabled generation does not import/build/validate any example IR.
    from plc.ir import build_plc_ir, validate_plc_ir

    examples = []
    for identifier, requirement, comments, rungs in _definitions():
        program = build_plc_ir(
            {"device_comments": comments, "rungs": rungs},
            plc_model="FX3U", program_name="MAIN",
        )
        validate_plc_ir(program)
        examples.append({"id": identifier, "requirement": requirement, "program_ir": program})
    return tuple(examples)


def construction_examples_ir() -> list[dict]:
    """Detached canonical v3 programs for inspection, export and regression tests."""
    return copy.deepcopy(list(_ir_examples()))


def _compact_from_ir(program: dict) -> dict:
    """Project authoritative network topology to B's existing wire format."""
    from plc.ir import ir_to_ladder
    from application.compact_protocol import validate_compact_structure

    def simple(element):
        kind = element["type"]
        if kind in {"COMPARE", "BLOCK_INPUT"}:
            return element["expression"]
        if kind in {"NO", "NC", "P", "F", "RISING", "FALLING"}:
            return f"{kind} {element['address']}"
        raise ValueError(f"Unsupported example input type: {kind}")

    def input_value(element):
        if element["type"] == "parallel_block":
            return {"or": [[simple(child) for child in branch] for branch in element["branches"]]}
        return simple(element)

    def output_value(element):
        kind = element["type"]
        if kind == "APP_INSTR":
            return " ".join([element["opcode"], *element["operands"]])
        if kind in {"TIMER", "COUNTER"}:
            return f"{kind} {element['address']} {element['value']}"
        if kind in {"COIL", "PLS", "PLF"}:
            return f"{kind} {element['address']}"
        raise ValueError(f"Unsupported example output type: {kind}")

    result = {"r": [
        {
            "h": simple(rung["header_element"]) if rung.get("header_element") else None,
            "s": [simple(element) for element in rung.get("shared_inputs", [])],
            "b": [
                {"i": [input_value(element) for element in branch["inputs"]],
                 "o": [output_value(element) for element in branch["outputs"]]}
                for branch in rung["branches"]
            ],
        }
        for rung in ir_to_ladder(program)["rungs"]
    ]}
    validate_compact_structure(result)
    return result


@lru_cache(maxsize=16)
def _routed_block(example_ids: tuple[str, ...], route_json: str) -> ConstructionExampleBlock:
    from application.compact_protocol import PROTOCOL_VERSION
    from plc.ir import canonical_sha256

    selected = [
        example for example in _ir_examples()
        if example["id"] in set(example_ids)
    ]
    route = json.loads(route_json)
    if not selected:
        return ConstructionExampleBlock(
            requested=True,
            plc_model="FX3U",
            reason=str(route.get("reason") or "no_primary_match"),
            route=route,
        )

    parts = [
        f"\n# Routed construction examples ({PACK_VERSION})\n"
        "以下是按已确认结构需求选择的独立构造范例，不是本项目需求、地址分配或必须采用的方案。"
        "仅学习条件、状态、扫描顺序与分支组织；当前确认规格及型号事实优先。\n"
    ]
    for example in selected:
        parts.append(
            f"\n## {example['id']}\n需求：{example['requirement']}\n"
            f"输出（{PROTOCOL_VERSION}）：\n"
            + json.dumps(_compact_from_ir(example["program_ir"]), ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )
    parts.append("\n# End routed construction examples\n")
    return ConstructionExampleBlock(
        requested=True,
        plc_model="FX3U",
        text="".join(parts),
        example_ids=tuple(example["id"] for example in selected),
        ir_hashes=tuple(canonical_sha256(example["program_ir"]) for example in selected),
        reason="routed_primary_match",
        route=route,
    )

def prepare_construction_examples(
    plc_model: str, enabled: bool | None = None, confirmed_spec=None,
) -> ConstructionExampleBlock:
    """Route once from the confirmed structured spec before token counting."""
    from application.construction_routing import (
        build_construction_need_profile,
        route_construction_examples,
    )

    requested = resolve_construction_examples(enabled)
    model = str(plc_model or "FX3U").strip().upper() or "FX3U"
    if not requested:
        return ConstructionExampleBlock(False, model)
    if model not in SUPPORTED_MODELS:
        return ConstructionExampleBlock(True, model, reason="unsupported_model")
    profile = build_construction_need_profile(confirmed_spec)
    route = route_construction_examples(profile, _route_candidates(), max_examples=2)
    selected = tuple(route.get("selected_ids") or ())
    return _routed_block(
        selected,
        json.dumps(route, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


if __name__ == "__main__":
    # Inspect the complete current IR without a model call or a project mutation.
    print(json.dumps({"pack_version": PACK_VERSION, "examples": construction_examples_ir()},
                     ensure_ascii=False, indent=2))
