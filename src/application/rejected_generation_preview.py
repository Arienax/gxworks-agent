"""Best-effort rendering for rejected ladder generations.

This module is intentionally outside the acceptance path except for one narrowly
scoped syntax recovery: when a compact Agent-B token is missing only its closing
quote immediately before a JSON delimiter, the missing byte is deterministic and
may be restored. Every other salvage path is diagnostic-only and can never turn
a rejected candidate into a validated program.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from plc.ir import canonical_sha256
from typing import Any, Mapping


_DIAGNOSTIC_ARTIFACTS = {
    "json": "diagnostic_ladder.json",
    "ir": "diagnostic_program.ir.json",
    "svg": "diagnostic_ladder.svg",
    "program_csv": "diagnostic_program.csv",
    "comment_csv": "diagnostic_comments.csv",
}

# Compact Agent-B values are token-like and never need literal JSON delimiters.
# If a provider drops only the closing quote before a list/object delimiter,
# restoring that quote is deterministic and does not invent PLC semantics.
_COMPACT_UNCLOSED_VALUE = re.compile(
    r'("(?:NO|NC|P|F|RISING|FALLING|COIL|PLS|PLF|TIMER|COUNTER|'
    r'[<>]=?|==|<>|[A-Z][A-Z0-9_.$@+\-]*) [^"\[\]\{\},]+)'
    r'(?=(?:\]\]|\]\}|\],|,))'
)


def diagnostic_artifact_names() -> dict[str, str]:
    return dict(_DIAGNOSTIC_ARTIFACTS)


def _clean_json_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.endswith("```"):
        text = text.rsplit("\n", 1)[0]
    return text.strip()


def _repair_compact_json(text: str) -> tuple[str, int]:
    """Apply syntax-only repairs whose intended bytes are unambiguous."""
    candidate = _clean_json_text(text)
    try:
        json.loads(candidate)
        return candidate, 0
    except (TypeError, ValueError):
        pass
    repaired, count = _COMPACT_UNCLOSED_VALUE.subn(r'\1"', candidate)
    return repaired, count


def _projected_spec(confirmed_spec: Any) -> dict[str, Any]:
    try:
        from application.generation_agent import _strict_generation_projection
        return _strict_generation_projection(confirmed_spec) if isinstance(confirmed_spec, Mapping) else {}
    except Exception:
        return dict(confirmed_spec) if isinstance(confirmed_spec, Mapping) else {}


def _expand_compact(compact: Mapping[str, Any], confirmed_spec: Any) -> dict[str, Any]:
    from application.generation_agent import _expand_compact_ladder
    return _expand_compact_ladder(dict(compact), _projected_spec(confirmed_spec))


def recover_compact_for_validation(raw_text: str, *, confirmed_spec=None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Recover only a complete compact response using deterministic syntax repair.

    No rung may be dropped or altered here. Success returns the ordinary full
    ladder_v1 candidate, which still has to pass the normal validators before it
    can be considered valid.
    """
    original = _clean_json_text(raw_text)
    repaired, count = _repair_compact_json(original)
    parsed = json.loads(repaired)
    if not isinstance(parsed, Mapping) or set(parsed) != {"r"}:
        raise ValueError("response is not one complete compact ladder")
    ladder = _expand_compact(parsed, confirmed_spec)
    return ladder, {
        "source_format": "compact_ladder",
        "syntax_quote_repairs": count,
        "original_json_valid": repaired == original,
    }


def _compact_rows_from_fragments(text: str) -> list[dict[str, Any]]:
    """Recover individually parseable compact rungs from a broken top-level array.

    This is display-only salvage. Rungs that cannot be parsed are omitted and
    the preview is explicitly marked partial; they are never accepted as valid.
    """
    marker = re.search(r'"r"\s*:\s*\[', text)
    if not marker:
        return []
    body = text[marker.end():]
    starts = [m.start() for m in re.finditer(r'\{\s*"(?:h|s|b)"\s*:', body)]
    rows: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    for start in starts:
        try:
            value, _end = decoder.raw_decode(body[start:])
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict) and isinstance(value.get("b"), list):
            rows.append(value)
    return rows


def _sanitize_ladder(ladder: Any) -> dict[str, Any]:
    """Keep renderable ladder_v1 containers without claiming validity."""
    if not isinstance(ladder, Mapping):
        raise ValueError("diagnostic candidate is not a ladder object")
    raw_rungs = ladder.get("rungs")
    if not isinstance(raw_rungs, list) or not raw_rungs:
        raise ValueError("diagnostic candidate has no recoverable rungs")

    rungs = []
    for index, raw_rung in enumerate(raw_rungs, start=1):
        if not isinstance(raw_rung, Mapping):
            continue
        raw_branches = raw_rung.get("branches")
        if not isinstance(raw_branches, list) or not raw_branches:
            continue
        branches = []
        for branch_index, raw_branch in enumerate(raw_branches, start=1):
            if not isinstance(raw_branch, Mapping):
                continue
            inputs = [copy.deepcopy(item) for item in (raw_branch.get("inputs") or []) if isinstance(item, Mapping)]
            outputs = [copy.deepcopy(item) for item in (raw_branch.get("outputs") or []) if isinstance(item, Mapping)]
            if not outputs:
                continue
            branches.append({
                "branch_id": raw_branch.get("branch_id") if isinstance(raw_branch.get("branch_id"), int) else branch_index,
                "y_offset_level": raw_branch.get("y_offset_level") if isinstance(raw_branch.get("y_offset_level"), int) else branch_index - 1,
                "inputs": inputs,
                "outputs": outputs,
            })
        if not branches:
            continue
        header = raw_rung.get("header_element")
        if not isinstance(header, Mapping):
            header = None
        shared = [copy.deepcopy(item) for item in (raw_rung.get("shared_inputs") or []) if isinstance(item, Mapping)]
        rung_id = raw_rung.get("rung_id")
        if isinstance(rung_id, bool) or not isinstance(rung_id, int):
            rung_id = index
        rungs.append({
            "rung_id": rung_id,
            "header_element": copy.deepcopy(header),
            "shared_inputs": shared,
            "branches": branches,
        })
    if not rungs:
        raise ValueError("diagnostic candidate has no renderable rungs")
    comments = ladder.get("device_comments")
    return {
        "device_comments": copy.deepcopy(comments) if isinstance(comments, Mapping) else {},
        "rungs": rungs,
    }


def recover_rejected_ladder(raw_text: str, *, confirmed_spec=None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Recover a renderable ladder without changing acceptance state."""
    text = _clean_json_text(raw_text)
    repaired, quote_repairs = _repair_compact_json(text)
    parsed = None
    parse_error = None
    for candidate in (text, repaired) if repaired != text else (text,):
        try:
            parsed = json.loads(candidate)
            parse_error = None
            break
        except json.JSONDecodeError as error:
            parse_error = error

    partial = False
    omitted_rungs = 0
    if isinstance(parsed, Mapping) and "rungs" in parsed:
        alias = None
        try:
            from application.compact_alias import expand_hybrid_compact_ladder
            alias = expand_hybrid_compact_ladder(parsed, confirmed_spec)
        except Exception:
            alias = None
        if alias is not None:
            ladder = alias
            source_format = "compact_alias_ladder"
        else:
            ladder = dict(parsed)
            source_format = "ladder_v1"
    elif isinstance(parsed, Mapping) and "r" in parsed:
        source_format = "compact_ladder"
        try:
            ladder = _expand_compact(parsed, confirmed_spec)
        except Exception:
            rows = []
            for row in parsed.get("r", []) if isinstance(parsed.get("r"), list) else []:
                if not isinstance(row, Mapping):
                    continue
                try:
                    one = _expand_compact({"r": [dict(row)]}, confirmed_spec)
                except Exception:
                    omitted_rungs += 1
                    continue
                rows.extend(one.get("rungs", []))
            if not rows:
                raise
            ladder = {"device_comments": {}, "rungs": rows}
            partial = omitted_rungs > 0
    else:
        source_format = "compact_ladder"
        rows = _compact_rows_from_fragments(repaired)
        if not rows:
            if parse_error is not None:
                raise parse_error
            raise ValueError("rejected response has no recoverable ladder structure")
        ladder = _expand_compact({"r": rows}, confirmed_spec)
        partial = True

    ladder = _sanitize_ladder(ladder)
    info = {
        "source_format": source_format,
        "syntax_quote_repairs": quote_repairs,
        "partial": bool(partial),
        "recovered_rung_count": len(ladder["rungs"]),
        "omitted_rung_count": omitted_rungs,
    }
    if parse_error is not None:
        info["original_json_error"] = str(parse_error)
    return ladder, info


def _diagnostic_program(ladder: Mapping[str, Any], *, plc_model="FX3U", program_name="MAIN", revision=1,
                        confirmed_spec=None) -> dict[str, Any]:
    """Build internally consistent IR without re-running blocking ladder checks."""
    from plc.ir import build_plc_ir, validate_plc_ir

    program = build_plc_ir(
        ladder,
        plc_model=str(plc_model or "FX3U").strip().upper() or "FX3U",
        program_name=str(program_name or "MAIN").strip() or "MAIN",
        revision=revision,
        confirmed_spec=confirmed_spec if isinstance(confirmed_spec, Mapping) else None,
    )
    # Validate only IR self-consistency. The ladder is intentionally already known
    # to be rejected by the ordinary generation validator.
    validate_plc_ir(program, validate_ladder=False)
    return program


def render_diagnostic_program(program: Mapping[str, Any], output_dir) -> dict[str, str]:
    """Render a rejected diagnostic IR without applying blocking ladder validation."""
    from plc.ir import ir_to_ladder, validate_plc_ir
    from rendering.ladder_svg import AdvancedSVGLadder
    from gxworks2.csv_export import generate_gx_works2_csv

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    validate_plc_ir(program, validate_ladder=False)
    ladder = ir_to_ladder(program)
    names = diagnostic_artifact_names()
    ladder_text = json.dumps(ladder, ensure_ascii=False, indent=2)
    (directory / names["json"]).write_text(ladder_text, encoding="utf-8")
    (directory / names["ir"]).write_text(json.dumps(program, ensure_ascii=False, indent=2), encoding="utf-8")
    drawer = AdvancedSVGLadder()
    (directory / names["svg"]).write_text(drawer.generate_ladder(ladder_text), encoding="utf-8")
    csv_ok = bool(generate_gx_works2_csv(
        ladder,
        str(directory / names["program_csv"]),
        str(directory / names["comment_csv"]),
    ))
    artifacts = {"json": names["json"], "ir": names["ir"], "svg": names["svg"]}
    if csv_ok:
        artifacts.update(program_csv=names["program_csv"], comment_csv=names["comment_csv"])
    return artifacts


def materialize_rejected_preview(raw_text: str, output_dir, *, confirmed_spec=None, plc_model="FX3U",
                                 program_name="MAIN", revision=1) -> dict[str, Any]:
    """Render SVG/CSV/IR/JSON for a rejected candidate, never mark it valid."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    ladder, recovery = recover_rejected_ladder(raw_text, confirmed_spec=confirmed_spec)
    program = _diagnostic_program(
        ladder,
        plc_model=plc_model,
        program_name=program_name,
        revision=revision,
        confirmed_spec=confirmed_spec,
    )
    artifacts = render_diagnostic_program(program, directory)
    if not all(key in artifacts for key in ("json", "ir", "svg", "program_csv", "comment_csv")):
        raise ValueError("diagnostic candidate did not produce the complete GX artifact set")
    return {
        "target_mode": "ladder",
        "diagnostic_only": True,
        "validation_profile": "generation_structural",
        "program_name": str(program_name or "MAIN"),
        "revision": int(program.get("revision", 1)),
        "ir_sha256": canonical_sha256(program),
        "ladder_sha256": program["source"]["ladder_sha256"],
        "validation": {
            "status": "invalid_candidate",
            "profile": "rejected_diagnostic",
            "messages": [
                "候选未通过正式校验；梯形图和 CSV 已保留，可继续发送到 GX Works2 检查黄色错误。"
            ],
        },
        "artifacts": artifacts,
        "width": 0,
        "height": 0,
        "recovery": recovery,
    }
