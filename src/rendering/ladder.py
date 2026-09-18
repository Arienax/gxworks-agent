# ========================================================
# 文件名：draw.py
# GX Works2 compatibility facade.
# ========================================================

# Keep the SVG wrapping and native step-width fixes from the first reproduction
# commit isolated from the export-only 24-row ladder-block lowering below.
from rendering.native_layout import *  # noqa: F401,F403
from rendering.native_layout import (
    AdvancedSVGLadder,
    _NATIVE_STEP_DELTA_AFTER,
    generate_gx_works2_csv as _generate_native_wrapped_csv,
)
from gxworks2.native_export import (
    GXNativeLoweringError,
    lower_large_parallel_blocks_for_gxworks2,
)

# The legacy exporter advances application instructions by one list-program
# step. FX3U SFTL/SFTLP are 9-step instructions, so the native-label repair
# needs eight additional steps after either opcode.
_NATIVE_STEP_DELTA_AFTER.update({"SFTL": 8, "SFTLP": 8})


def generate_gx_works2_csv(
    json_data,
    output_program_csv="MAIN.csv",
    output_comment_csv="COMMENT.csv",
    *,
    infer_device_comments=True,
):
    """Generate GX Works2 CSV with native step widths and <=24-row OR blocks.

    The canonical ladder/IR is not changed. Oversized top-level parallel
    networks are lowered only for the GX Works2 CSV artifact.
    """
    from plc.ir import ir_to_ladder, is_plc_ir
    from plc.device_identity import canonical_ladder_devices
    if is_plc_ir(json_data):
        json_data = ir_to_ladder(json_data)
    if isinstance(json_data, dict):
        json_data = canonical_ladder_devices(json_data)
    try:
        export_ladder = lower_large_parallel_blocks_for_gxworks2(json_data)
    except GXNativeLoweringError as exc:
        print(f"GX Works2并联网络导出失败: {exc}")
        return False

    return _generate_native_wrapped_csv(
        export_ladder,
        output_program_csv,
        output_comment_csv,
        infer_device_comments=infer_device_comments,
    )
