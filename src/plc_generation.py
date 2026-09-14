"""Model-free generation acceptance shared by the API and engineering tools.

The API fast path is authoritative: representation checks are blocking;
engineering findings belong to review and do not trigger model retries.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

from application.generation_repair import check_candidate_containers, materialize_partial
from contract_repair import patch_device_addresses
from i18n import tr
from ladder_repair import normalize_app_instr_out_outputs, normalize_legacy_counter_outputs
from plc_ir import build_plc_ir, ir_to_ladder, validate_plc_ir
from plc_json_validator import (
    PLCJsonValidationError, validate_ladder_candidate_structure,
    validate_ladder_partial_structure,
)


GENERATION_VALIDATION_PROFILE = "generation_structural"


def _normalize_legacy_blocks(ladder):
    """Let legacy textual outputs reach the same typed instruction checks.

    Without this conversion BLOCK_OUTPUT could bypass the instruction catalogue
    and write-target checks simply by putting an opcode in ``expression``.
    """
    for rung in ladder.get("rungs", []):
        for branch in rung.get("branches", []):
            for output in branch.get("outputs", []):
                if output.get("type") != "BLOCK_OUTPUT":
                    continue
                expression = output.get("expression")
                if not isinstance(expression, str) or not expression.strip():
                    raise PLCJsonValidationError("$.rungs: BLOCK_OUTPUT requires an instruction expression")
                opcode, *operands = expression.strip().split()
                replacement = {"type": "APP_INSTR", "opcode": opcode.upper(), "operands": operands}
                if opcode.upper() in {"PLS", "PLF"} and len(operands) == 1:
                    replacement = {"type": opcode.upper(), "address": operands[0]}
                if "label" in output:
                    replacement["label"] = output["label"]
                output.clear()
                output.update(replacement)


def _common_semantic_signature(element):
    if not isinstance(element, dict):
        return None
    return json.dumps(
        {
            key: copy.deepcopy(element[key])
            for key in ("type", "address", "expression", "opcode", "operands", "value")
            if key in element
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _leaf_semantic_signatures(element):
    """Flatten representation-only parallel containers into semantic leaf tokens."""
    if not isinstance(element, dict):
        return []
    if str(element.get("type") or "").casefold() == "parallel_block":
        result = []
        for branch in element.get("branches", []) or []:
            if isinstance(branch, list):
                for child in branch:
                    result.extend(_leaf_semantic_signatures(child))
        return result
    signature = _common_semantic_signature(element)
    return [signature] if signature is not None else []


def _rung_semantic_tokens(rung):
    """Return a multiset of engineering tokens, independent of container layout."""
    tokens = []
    if not isinstance(rung, dict):
        return Counter()
    header = rung.get("header_element")
    if isinstance(header, dict):
        tokens.extend(_leaf_semantic_signatures(header))
    for element in rung.get("shared_inputs", []) or []:
        tokens.extend(_leaf_semantic_signatures(element))
    for branch in rung.get("branches", []) or []:
        if not isinstance(branch, dict):
            continue
        for element in branch.get("inputs", []) or []:
            tokens.extend(_leaf_semantic_signatures(element))
        for element in branch.get("outputs", []) or []:
            tokens.extend(_leaf_semantic_signatures(element))
    return Counter(tokens)


def _enforce_structural_repair_semantics(previous_ladder, submitted_partial):
    """Whole-rung fallback may rearrange structure, never engineering meaning."""
    if not isinstance(previous_ladder, dict) or not isinstance(submitted_partial, dict):
        return
    baseline = {
        rung.get("rung_id"): rung
        for rung in previous_ladder.get("rungs", []) or []
        if isinstance(rung, dict) and isinstance(rung.get("rung_id"), int)
        and not isinstance(rung.get("rung_id"), bool)
    }
    for rung in submitted_partial.get("rungs", []) or []:
        if not isinstance(rung, dict):
            continue
        rung_id = rung.get("rung_id")
        original = baseline.get(rung_id)
        if original is None:
            continue
        if _rung_semantic_tokens(original) != _rung_semantic_tokens(rung):
            raise PLCJsonValidationError(
                f"$.rungs: contract repair changed out-of-scope semantic tokens in rung {rung_id}"
            )


def normalization_summary(report):
    """A small user-facing summary, without copied program bodies or paths."""
    operations = {
        "remove_duplicate_condition": "已删除重复触点或比较条件",
        "extract_common_prefix": "已提取分支公共条件",
        "merge_adjacent_coils": "已合并公共条件相同的相邻输出网络",
    }
    reasons = {
        "non_pure_condition": "含边沿或复杂条件，保留原求值位置",
        "stateful_or_unknown_output": "含状态指令或无法确认的副作用，保留原结构",
        "read_after_write": "条件可能在写入后变化，保留再次读取",
        "duplicate_coil_target": "保留同一线圈多次写入的执行顺序",
        "distinct_network_notes": "网络说明不同，保留独立网络",
        "different_branch_conditions": "分支条件不同，保留独立网络",
        "outside_scope": "位于本次修改范围之外，保持不变",
        "unsupported_container": "保留现有兼容结构",
    }
    result = {}
    for key in ("changes", "skipped"):
        items = []
        for item in report.get(key, []):
            code = item.get("operation" if key == "changes" else "reason", "")
            value = {"message": (operations if key == "changes" else reasons).get(code, code),
                     "network_ids": [f"N{identifier:04d}" for identifier in item.get("rung_ids", [])]}
            if value not in items:
                items.append(value)
        result[key] = items
    return result


def prepare_ladder_candidate(
    candidate, *, plc_model="FX3U", program_name="MAIN", revision=1,
    confirmed_spec=None, previous_ladder=None, repair_mode=False,
    allowed_rung_ids=None, allowed_addresses=None, task_type=None,
    on_progress=None,
):
    """Normalize one response, materialize its edit, and build a consistent IR.

    Caller snapshots are never mutated. Only submitted/changed rungs are
    eligible for condition factoring when a baseline exists. Explicit repair
    envelopes retain the API's existing evidence and address scope checks.
    """
    parsed = copy.deepcopy(candidate)
    check_candidate_containers(parsed)
    submitted = copy.deepcopy(parsed)
    progress = on_progress or (lambda _message: None)
    messages = []
    progress(tr('正在解析模型输出：规范化梯形图协议'))
    _normalize_legacy_blocks(parsed)
    parsed, counters = normalize_legacy_counter_outputs(parsed)
    if counters:
        messages.append(tr('已将旧版 TIMER+C 计数器结构转换为 COUNTER：') + ", ".join(counters))
    parsed, outs = normalize_app_instr_out_outputs(parsed)
    if outs:
        messages.append(tr('已将误放入 APP_INSTR 的 OUT 转换为标准输出结构：') + "；".join(outs))

    allowed_ids = set(allowed_rung_ids or ())
    allowed_devices = {str(item).strip().upper() for item in (allowed_addresses or ())}
    if repair_mode:
        if parsed.get("mode") != "partial":
            raise PLCJsonValidationError('$.mode: repair must return "partial"')
        changed_ids = {int(rung["rung_id"]) for rung in parsed.get("rungs", []) if rung.get("rung_id") is not None}
        changed_ids.update(int(item) for item in parsed.get("delete_rung_ids", []))
        outside = changed_ids - allowed_ids
        if outside:
            raise PLCJsonValidationError("$.rungs: repair changed evidence-external rung ids " + ", ".join(map(str, sorted(outside))))
        outside_comments = {str(item).strip().upper() for item in parsed.get("device_comments", {})} - allowed_devices
        if outside_comments:
            raise PLCJsonValidationError("$.device_comments: repair changed evidence-external addresses " + ", ".join(sorted(outside_comments)))
        if task_type == "contract_repair":
            if parsed.get("delete_rung_ids"):
                raise PLCJsonValidationError("$.delete_rung_ids: contract repair may not delete existing rungs")
            if allowed_devices:
                outside_devices = patch_device_addresses(parsed) - allowed_devices
                if outside_devices:
                    raise PLCJsonValidationError("$.rungs: contract repair introduced out-of-scope devices " + ", ".join(sorted(outside_devices)))
            _enforce_structural_repair_semantics(previous_ladder, submitted)

    normalization_scope = None
    if parsed.get("mode") == "partial":
        if previous_ladder is None:
            raise PLCJsonValidationError('$.mode: received "partial" without a previous ladder')
        validate_ladder_partial_structure(parsed, plc_model=plc_model)
        normalization_scope = {rung["rung_id"] for rung in parsed.get("rungs", [])}
        parsed = materialize_partial(previous_ladder, parsed)
    elif previous_ladder is not None:
        baseline = {rung["rung_id"]: rung for rung in previous_ladder.get("rungs", [])}
        normalization_scope = {rung.get("rung_id") for rung in parsed.get("rungs", [])
                               if baseline.get(rung.get("rung_id")) != rung}
    if repair_mode:
        normalization_scope = allowed_ids if normalization_scope is None else normalization_scope & allowed_ids

    if not parsed.get("rungs"):
        raise PLCJsonValidationError("$.rungs: generated program must not be empty")
    progress(tr('正在解析模型输出：检查结构与地址'))
    validate_ladder_candidate_structure(parsed, plc_model=plc_model, require_catalogued_instructions=True)
    from plc_condition_normalizer import normalize_shared_conditions
    parsed, normalization = normalize_shared_conditions(parsed, allowed_rung_ids=normalization_scope)
    validate_ladder_candidate_structure(parsed, plc_model=plc_model, require_catalogued_instructions=True)

    semantics = []
    if isinstance(confirmed_spec, dict) and isinstance(confirmed_spec.get("execution_semantics"), list):
        from plc_semantics import normalize_semantic_requirements
        semantics = normalize_semantic_requirements(confirmed_spec["execution_semantics"])
    progress(tr('正在解析模型输出：构建 PLC IR'))
    program = build_plc_ir(parsed, plc_model=plc_model, program_name=program_name,
                           revision=revision, confirmed_spec=confirmed_spec,
                           semantic_requirements=semantics)
    validate_plc_ir(program, validate_ladder=False)
    return {"ladder": parsed, "program_ir": program, "validation_messages": messages,
            "normalization": normalization_summary(normalization), "validation_profile": GENERATION_VALIDATION_PROFILE}


def render_generation_artifacts(program, output_dir):
    """Render exactly the API's artifact set, without semantic revalidation."""
    from draw import AdvancedSVGLadder, generate_gx_works2_csv
    from plc_st_renderer import render_plc_ir_to_st, validate_st_traceability

    validate_plc_ir(program, validate_ladder=False)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    ladder_text = json.dumps(ir_to_ladder(program), ensure_ascii=False, indent=2)
    (directory / "ladder.json").write_text(ladder_text, encoding="utf-8")
    st_text = render_plc_ir_to_st(program)
    validate_st_traceability(program, st_text)
    (directory / "program_from_ir.st").write_text(st_text, encoding="utf-8")
    (directory / "program.ir.json").write_text(json.dumps(program, ensure_ascii=False, indent=2), encoding="utf-8")
    drawer = AdvancedSVGLadder()
    (directory / "ladder.svg").write_text(drawer.generate_ladder(ladder_text), encoding="utf-8")
    artifacts = {"json": "ladder.json", "ir": "program.ir.json", "svg": "ladder.svg", "st_from_ir": "program_from_ir.st"}
    if str(program.get("plc", {}).get("cpu") or "FX3U").upper() == "FX3U":
        generate_gx_works2_csv(program, str(directory / "program.csv"), str(directory / "comments.csv"))
        artifacts.update(program_csv="program.csv", comment_csv="comments.csv")
    return {"artifacts": artifacts, "width": int(drawer.width), "height": int(drawer.height),
            "st_from_ir_sha256": hashlib.sha256(st_text.encode("utf-8")).hexdigest()}
