"""Core-owned project modes and client presentation metadata."""
from __future__ import annotations

DEFAULT_PLC_MODEL = "FX3U"
DEFAULT_TARGET_MODE = "ladder"
STARTER_REQUIREMENT = "FX3U 电机启停练习：X0 是瞬时启动按钮，X1 是停止信号（ON 时停止）。Y0 表示电机运行。按启动后保持运行，停止优先；上电不自动启动。请先整理 I/O、保持和复位规则，再让我确认规格。这是离线学习示例。"

def project_capabilities(version=None):
    mode = str((version or {}).get("target_mode") or "ladder").lower()
    ladder = mode == "ladder"
    fbd = mode == "fbd"
    result = {"project_type": "fx", "body_form": mode,
            "representations": ["ladder_svg", "st", "ir"] if ladder else ["fbd", "svg", "gxw"] if fbd else ["st"],
            "operations": {"view": True, "generate": mode in ("ladder", "st", "fbd"),
                           "diagnose": ladder, "patch": ladder, "gx_import": ladder or fbd,
                           "simulation": ladder, "gx_compile": False, "fbd_edit": fbd, "gx_inspect": not fbd,
                           "fbd_convert": ladder},
            "sfc": "requirement_input", "structured_semantics": "verified_native_templates" if fbd else "experimental_read_only"}

    result["creation"] = {
        "plc_model": DEFAULT_PLC_MODEL,
        "default_target_mode": DEFAULT_TARGET_MODE,
        "starter_requirement": STARTER_REQUIREMENT,
        "target_modes": [
            {"value": "ladder", "label": "梯形图"},
            {"value": "st", "label": "ST"},
            {"value": "fbd", "label": "FBD / 结构化梯形图"},
        ],
    }
    return result
