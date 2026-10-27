# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Shared ladder facts; validators and reviews choose their own reporting policy."""
import re


TIMER_CYCLIC_INTENT_RE = re.compile(
    r"闪烁|闪灯|周期|循环闪|振荡|时钟|方波|脉冲发生|"
    r"blink|flash|oscillat|clock|square\s*wave|toggle",
    re.IGNORECASE,
)


def flatten_inputs(elements):
    for element in elements or []:
        if not isinstance(element, dict):
            continue
        if element.get("type") == "parallel_block":
            for branch in element.get("branches", []) or []:
                yield from flatten_inputs(branch)
        else:
            yield element


def timer_descriptive_text(data, timer_address, *, branch_scoped=False):
    """Collect timer descriptions with the caller's existing evidence scope.

    Validation considers the whole rung; advisory review considers only branches
    mentioning this timer, including comments on their input contacts.
    """
    comments = data.get("device_comments") or {}
    parts = [str(comments.get(timer_address, ""))]
    for rung in data.get("rungs", []) or []:
        note = str(rung.get("debug_note", "") or "")
        rung_parts, rung_mentions_timer = [note], False
        shared = list(rung.get("shared_inputs", []) or [])
        for branch in rung.get("branches", []) or []:
            inputs = list(flatten_inputs(shared + list(branch.get("inputs", []) or [])))
            outputs = list(branch.get("outputs", []) or [])
            mentions = any(str(item.get("address", "")).strip().upper() == timer_address
                           for item in inputs + outputs)
            branch_parts = []
            for item in inputs:
                branch_parts.append(str(item.get("label", "") or ""))
                if branch_scoped:
                    branch_parts.append(str(comments.get(str(item.get("address", "")).upper(), "")))
            for item in outputs:
                branch_parts.append(str(item.get("label", "") or ""))
                address = str(item.get("address", "")).strip().upper()
                if address:
                    branch_parts.append(str(comments.get(address, "")))
            if branch_scoped and mentions:
                parts.extend([note, *branch_parts])
            rung_parts.extend(branch_parts)
            rung_mentions_timer |= mentions
        if not branch_scoped and rung_mentions_timer:
            parts.extend(rung_parts)
    return " ".join(parts)


def same_scan_toggle_findings(rungs):
    """Describe complementary SET/RST branches that can execute on one edge."""

    for rung_idx, rung in enumerate(rungs or []):
        shared_inputs = list(flatten_inputs(rung.get("shared_inputs", []) or []))
        has_shared_edge = any(
            str(item.get("type", "")).upper() in {"P", "RISING", "F", "FALLING"}
            for item in shared_inputs
        )
        toggle_text = str(rung.get("debug_note", "") or "")
        guarded_sets = {}
        guarded_resets = {}
        for branch_idx, branch in enumerate(rung.get("branches", []) or []):
            branch_inputs = list(flatten_inputs(branch.get("inputs", []) or []))
            toggle_text += " " + " ".join(
                str(item.get("label", "") or "") for item in branch_inputs
            )
            guards = {
                (
                    str(item.get("type", "")).upper(),
                    str(item.get("address", "")).strip().upper(),
                )
                for item in branch_inputs
            }
            for output_idx, output in enumerate(branch.get("outputs", []) or []):
                toggle_text += " " + str(output.get("label", "") or "")
                if str(output.get("type", "")).upper() != "APP_INSTR":
                    continue
                opcode = str(output.get("opcode", "")).upper()
                operands = output.get("operands", []) or []
                if opcode not in {"SET", "RST"} or not operands:
                    continue
                address = str(operands[0]).strip().upper()
                entry = (branch_idx, output_idx)
                if opcode == "SET" and ("NC", address) in guards:
                    guarded_sets[address] = entry
                if opcode == "RST" and ("NO", address) in guards:
                    guarded_resets[address] = entry
        toggle_intent = bool(
            re.search(
                r"翻转|交替|切换|toggle|alternate|\bALT\b",
                toggle_text,
                re.IGNORECASE,
            )
        )
        for address in sorted(set(guarded_sets).intersection(guarded_resets)):
            if not (has_shared_edge or toggle_intent):
                continue
            branch_idx, output_idx = guarded_sets[address]
            reset_branch, reset_output = guarded_resets[address]
            yield {
                "address": address, "rung_id": rung.get("rung_id"),
                "path": f"$.rungs[{rung_idx}].branches[{branch_idx}].outputs[{output_idx}]",
                "reset_path": f"$.rungs[{rung_idx}].branches[{reset_branch}].outputs[{reset_output}]",
            }


def edge_only_inputs(inputs):
    return bool(inputs) and all(str(item.get("type") or "").upper() in {"P", "RISING", "F", "FALLING"} for item in inputs)


def always_on_inputs(inputs, devices):
    return (len(inputs) == 1 and str(inputs[0].get("type") or "").upper() == "NO"
            and str(inputs[0].get("address") or "").strip().upper() in devices)
