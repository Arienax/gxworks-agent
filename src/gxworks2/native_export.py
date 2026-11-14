# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""GX Works2-specific export lowering.

GX Works2 simple-project ladder blocks are limited to 24 diagram rows. The
canonical ladder/IR remains untouched. Legacy top-level OR lowering uses a
conservative list-row count and collision-free internal M relays. Oversized
output groups with stable conditions can use several consecutive networks.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Mapping, MutableSequence, Sequence

from plc.ir import ir_to_ladder, is_plc_ir
from plc.ladder_limits import GXWORKS2_MAX_LADDER_BLOCK_ROWS, parallel_ladder_rows

FX3U_GENERAL_M_MAX = 7679

_M_DEVICE_RE = re.compile(r"(?<![A-Z0-9_])M(\d+)(?![A-Z0-9_])", re.IGNORECASE)


class GXNativeLoweringError(ValueError):
    """Raised when a ladder network cannot be safely lowered for GX Works2."""

    def __init__(self, message, *, rung_id=None, details=None):
        super().__init__(message)
        self.rung_id = rung_id
        self.details = dict(details or {})


def _collect_used_general_m_devices(payload: Any) -> set[int]:
    used: set[int] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                visit(str(key))
                visit(child)
            return
        if isinstance(value, (list, tuple)):
            for child in value:
                visit(child)
            return
        if not isinstance(value, str):
            return
        for match in _M_DEVICE_RE.finditer(value.upper()):
            number = int(match.group(1))
            if 0 <= number <= FX3U_GENERAL_M_MAX:
                used.add(number)

    visit(payload)
    return used


class _RelayAllocator:
    def __init__(self, payload: Any) -> None:
        self.used = _collect_used_general_m_devices(payload)
        self.cursor = FX3U_GENERAL_M_MAX

    def allocate(self) -> str:
        while self.cursor >= 0 and self.cursor in self.used:
            self.cursor -= 1
        if self.cursor < 0:
            raise GXNativeLoweringError(
                "GX Works2超长并联网络需要辅助M继电器，但M0-M7679已全部占用"
            )
        number = self.cursor
        self.used.add(number)
        self.cursor -= 1
        return f"M{number}"


def _parallel_block_rows(
    branches: Sequence[Sequence[Mapping[str, Any]]],
    *,
    output_rows: int,
) -> int:
    """Return legacy exporter list-row count for one parallel block."""
    if not branches:
        return output_rows
    if all(len(branch) == 1 for branch in branches):
        return len(branches) + output_rows
    return (
        sum(len(branch) for branch in branches)
        + max(0, len(branches) - 1)
        + output_rows
    )


def _partition_parallel_branches(
    branches: Sequence[Sequence[Mapping[str, Any]]],
    *,
    max_rows: int,
) -> List[List[List[Dict[str, Any]]]]:
    groups: List[List[List[Dict[str, Any]]]] = []
    current: List[List[Dict[str, Any]]] = []

    for raw_branch in branches:
        branch = [copy.deepcopy(dict(item)) for item in raw_branch]
        if not branch:
            raise GXNativeLoweringError("GX Works2并联网络包含空分支，无法安全拆分")
        if any(str(item.get("type", "")) == "parallel_block" for item in branch):
            raise GXNativeLoweringError(
                "GX Works2超长并联网络包含嵌套并联块，无法安全拆分"
            )

        candidate = current + [branch]
        if current and _parallel_block_rows(candidate, output_rows=1) > max_rows:
            groups.append(current)
            current = [branch]
        else:
            current = candidate

        if _parallel_block_rows(current, output_rows=1) > max_rows:
            raise GXNativeLoweringError(
                "GX Works2单个并联支路本身已超过24行，不能仅通过分组保持逻辑"
            )

    if current:
        groups.append(current)
    return groups


def _contact(address: str) -> Dict[str, Any]:
    return {"type": "NO", "address": address, "label": ""}


def _coil(address: str) -> Dict[str, Any]:
    return {"type": "COIL", "address": address, "label": ""}


def _parallel_input_from_relays(relays: Sequence[str]) -> List[Dict[str, Any]]:
    if not relays:
        raise GXNativeLoweringError("GX Works2辅助并联网络没有输入")
    if len(relays) == 1:
        return [_contact(relays[0])]
    return [
        {
            "type": "parallel_block",
            "branches": [[_contact(address)] for address in relays],
        }
    ]


def _helper_rung(
    *,
    rung_id: Any,
    branches: Sequence[Sequence[Mapping[str, Any]]],
    relay: str,
    debug_note: str = "",
) -> Dict[str, Any]:
    return {
        "rung_id": rung_id,
        "debug_note": debug_note,
        "header_element": None,
        "shared_inputs": [],
        "branches": [
            {
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [
                    {
                        "type": "parallel_block",
                        "branches": [
                            [copy.deepcopy(dict(item)) for item in branch]
                            for branch in branches
                        ],
                    }
                ],
                "outputs": [_coil(relay)],
            }
        ],
    }


def _merge_helper_rung(
    *,
    rung_id: Any,
    source_relays: Sequence[str],
    target_relay: str,
) -> Dict[str, Any]:
    return {
        "rung_id": rung_id,
        "debug_note": "",
        "header_element": None,
        "shared_inputs": [],
        "branches": [
            {
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": _parallel_input_from_relays(source_relays),
                "outputs": [_coil(target_relay)],
            }
        ],
    }


def _candidate_parallel_block(
    rung: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    if rung.get("header_element") or (rung.get("shared_inputs") or []):
        return None
    branches = rung.get("branches") or []
    if len(branches) != 1 or not isinstance(branches[0], Mapping):
        return None
    branch = branches[0]
    inputs = branch.get("inputs") or []
    outputs = branch.get("outputs") or []
    if len(inputs) != 1 or not outputs:
        return None
    block = inputs[0]
    if not isinstance(block, Mapping) or str(block.get("type", "")) != "parallel_block":
        return None
    return branch, block


def _lower_one_rung(
    rung: Mapping[str, Any],
    allocator: _RelayAllocator,
    *,
    max_rows: int,
    synthetic_id: MutableSequence[int],
) -> List[Dict[str, Any]] | None:
    candidate = _candidate_parallel_block(rung)
    if candidate is None:
        return None

    original_branch, block = candidate
    source_branches = block.get("branches") or []
    outputs = original_branch.get("outputs") or []
    if len(source_branches) < 2 or len(outputs) >= max_rows:
        return None

    source_rows = _parallel_block_rows(
        source_branches,
        output_rows=len(outputs),
    )
    if source_rows <= max_rows:
        return None

    groups = _partition_parallel_branches(
        source_branches,
        max_rows=max_rows,
    )
    if len(groups) < 2:
        return None

    def next_id() -> int:
        value = synthetic_id[0]
        synthetic_id[0] -= 1
        return value

    replacement: List[Dict[str, Any]] = []
    relays: List[str] = []
    original_note = str(rung.get("debug_note", "") or "")

    for group_index, group in enumerate(groups):
        relay = allocator.allocate()
        relays.append(relay)
        replacement.append(
            _helper_rung(
                rung_id=next_id(),
                branches=group,
                relay=relay,
                debug_note=original_note if group_index == 0 else "",
            )
        )

    while len(relays) + len(outputs) > max_rows:
        fan_in = max_rows - 1
        if fan_in < 2:
            raise GXNativeLoweringError(
                "GX Works2输出数量过多，无法在24行限制内建立辅助合并网络"
            )
        reduced: List[str] = []
        for start in range(0, len(relays), fan_in):
            chunk = relays[start : start + fan_in]
            if len(chunk) == 1:
                reduced.append(chunk[0])
                continue
            relay = allocator.allocate()
            replacement.append(
                _merge_helper_rung(
                    rung_id=next_id(),
                    source_relays=chunk,
                    target_relay=relay,
                )
            )
            reduced.append(relay)
        relays = reduced

    final_rung = copy.deepcopy(dict(rung))
    final_rung["debug_note"] = ""
    final_branch = (final_rung.get("branches") or [])[0]
    final_branch["inputs"] = _parallel_input_from_relays(relays)
    replacement.append(final_rung)
    return replacement


def _split_output_group(rung, plc_model, max_rows, synthetic_id, execution_context):
    branches = rung.get("branches") or []
    if len(branches) != 1 or not isinstance(branches[0], Mapping):
        return None
    branch = branches[0]
    outputs = branch.get("outputs") or []
    capacity = max_rows - parallel_ladder_rows(rung) + len(outputs)
    if capacity < 1 or len(outputs) <= capacity:
        return None
    from plc.condition_analysis import ConditionAnalysis

    analysis = ConditionAnalysis(plc_model, execution_context=execution_context)
    conditions = ([rung["header_element"]] if rung.get("header_element") else [])
    conditions += (rung.get("shared_inputs") or []) + (branch.get("inputs") or [])
    writes = analysis.writes(branch)
    if writes is None or analysis.repeat_condition_reason(conditions, writes):
        return None

    replacement = []
    for offset in range(0, len(outputs), capacity):
        part = copy.deepcopy({**rung, "branches": [{**branch, "outputs": outputs[offset:offset + capacity]}]})
        if offset:
            part.update(rung_id=synthetic_id[0], debug_note="")
            synthetic_id[0] -= 1
        replacement.append(part)
    return replacement


def lower_large_parallel_blocks_for_gxworks2(
    payload: Any,
    *,
    max_rows: int = GXWORKS2_MAX_LADDER_BLOCK_ROWS,
    plc_model: str | None = None,
    execution_context=None,
) -> Any:
    """Return an export-only ladder with oversized GX Works2 blocks split."""
    if max_rows < 2:
        raise GXNativeLoweringError("GX Works2 ladder block行数限制必须至少为2")
    if plc_model is None and isinstance(payload, Mapping):
        plc = payload.get("plc")
        plc_model = (plc.get("cpu") if isinstance(plc, Mapping) else None) or payload.get("plc_model")

    source_was_list = isinstance(payload, list)
    if is_plc_ir(payload):
        lowered: Any = ir_to_ladder(payload)
    else:
        lowered = copy.deepcopy(payload)

    if source_was_list:
        rungs = lowered
    elif isinstance(lowered, Mapping) and isinstance(lowered.get("rungs"), list):
        rungs = lowered["rungs"]
    else:
        return lowered

    allocator = _RelayAllocator(lowered)
    existing_negative_ids = [
        int(rung.get("rung_id"))
        for rung in rungs
        if isinstance(rung, Mapping)
        and isinstance(rung.get("rung_id"), int)
        and int(rung.get("rung_id")) < 0
    ]
    synthetic_id = [min(existing_negative_ids, default=0) - 1]

    result: List[Dict[str, Any]] = []
    for raw_rung in rungs:
        if not isinstance(raw_rung, Mapping):
            result.append(copy.deepcopy(raw_rung))
            continue
        replacement = _split_output_group(raw_rung, plc_model or "FX3U", max_rows, synthetic_id,
                                          execution_context if plc_model is not None else None)
        if replacement is None:
            replacement = _lower_one_rung(
                raw_rung,
                allocator,
                max_rows=max_rows,
                synthetic_id=synthetic_id,
            )
        if replacement is None:
            result.append(copy.deepcopy(dict(raw_rung)))
        else:
            result.extend(replacement)

    for rung in result:
        if not isinstance(rung, Mapping):
            continue
        # With no prefix the CSV writer starts a separate LD network for each
        # output branch; only a shared prefix connects them through MPS/MPP.
        networks = ([rung] if rung.get("header_element") or rung.get("shared_inputs") else
                    [{**rung, "branches": [branch]} for branch in rung.get("branches", [])])
        oversized = next((network for network in networks if parallel_ladder_rows(network) > max_rows), None)
        if oversized is not None:
            height = parallel_ladder_rows(oversized)
            raise GXNativeLoweringError(
                f"GX Works2梯级{rung.get('rung_id', '?')}的并联结构为{height}行，超过{max_rows}行；"
                "共享条件连接的所有分支合计高度，增加同一网络内的输出分支不能降低总高度。"
                "调整分组时须保持条件求值次数、读写顺序和触发行为",
                rung_id=rung.get("rung_id"),
                details={"parallel_rows": height, "max_parallel_rows": max_rows,
                         "branch_rows": [{"branch_id": branch.get("branch_id"),
                                          "parallel_rows": parallel_ladder_rows({"branches": [branch]})}
                                         for branch in oversized.get("branches", [])]},
            )

    if source_was_list:
        return result
    lowered["rungs"] = result
    return lowered


__all__ = [
    "FX3U_GENERAL_M_MAX",
    "GXNativeLoweringError",
    "GXWORKS2_MAX_LADDER_BLOCK_ROWS",
    "lower_large_parallel_blocks_for_gxworks2",
]
