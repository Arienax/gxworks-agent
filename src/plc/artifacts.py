"""Deterministic artifacts shared by PLC Core and debug workflows.

No models, application orchestration, UI, or external execution is loaded here.
"""
from pathlib import Path
from typing import Any, Dict, Mapping
import json

from plc.errors import DebugLoopError
from plc.ir import validate_plc_ir, ir_to_ladder
from plc.st_renderer import render_plc_ir_to_st, validate_st_traceability
from rendering.ladder_svg import AdvancedSVGLadder
from gxworks2.csv_export import generate_gx_works2_csv

def render_candidate_artifacts(
    program: Mapping[str, Any],
    output_dir: Path,
    *,
    validate_ladder: bool = True,
) -> Dict[str, str]:
    """Render user-facing/backend artifacts from a consistent IR.

    Direct confirmed-spec generation can pass ``validate_ladder=False`` because
    semantic/style acceptance is deliberately deferred to Review/simulation/GX.
    Debug and patch workflows retain the historical strict default.
    """

    validate_plc_ir(program, validate_ladder=validate_ladder)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ladder = ir_to_ladder(program)
    ladder_text = json.dumps(ladder, ensure_ascii=False, indent=2)
    ir_path = output_dir / "program.ir.json"
    ladder_path = output_dir / "ladder.json"
    st_path = output_dir / "program_from_ir.st"
    svg_path = output_dir / "ladder.svg"
    program_csv = output_dir / "program.csv"
    comment_csv = output_dir / "comments.csv"
    ladder_path.write_text(ladder_text, encoding="utf-8")
    ir_path.write_text(json.dumps(program, ensure_ascii=False, indent=2), encoding="utf-8")
    st_text = render_plc_ir_to_st(program)
    validate_st_traceability(program, st_text)
    st_path.write_text(st_text, encoding="utf-8")
    drawer = AdvancedSVGLadder()
    svg_path.write_text(drawer.generate_ladder(ladder_text), encoding="utf-8")
    if not generate_gx_works2_csv(program, str(program_csv), str(comment_csv)):
        raise DebugLoopError("GX Works2 CSV 渲染失败。")
    if not program_csv.is_file() or not comment_csv.is_file():
        raise DebugLoopError("GX Works2 程序或注释 CSV 没有完整生成。")
    return {
        "json": ladder_path.name,
        "ir": ir_path.name,
        "svg": svg_path.name,
        "st_from_ir": st_path.name,
        "program_csv": program_csv.name,
        "comment_csv": comment_csv.name,
    }

