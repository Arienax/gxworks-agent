"""Core-owned project modes and client presentation metadata."""
from __future__ import annotations

DEFAULT_PLC_MODEL = "FX3U"
DEFAULT_TARGET_MODE = "ladder"
STARTER_REQUIREMENT = (
    "离线示例：FX3U 电机接触器启停。I/O：X0 启动按钮（常开），X1 停止按钮（常闭），Y0 接触器线圈。"
    "要求：启动自保持；X1 断开时停机并复位保持，停止优先；上电不自启，停止恢复后须重新按下 X0 才能启动。"
    "若还缺少影响程序的条件，请先提问。"
)


_MODES = {"ladder": "LADDER / 梯形图", "st": "ST", "fbd": "FBD / 结构化梯形图"}

# Observed New-dialog selections, GX Works2 1.635.0.1. Enabling labels
# changes these Simple targets to FX3U/FX3UC; FX1 is also absent from the
# Structured CPU menu. Never accept the resulting automatic CPU substitution.
_SIMPLE_WITHOUT_LABELS = {"FX0S/FX0", "FX0N", "FX1", "FXU/FX2C"}


def project_creation_options():
    """Native series/type, then project type and its available languages.

    SH-080779ENG-AT PDF p151 defines the New-dialog fields. Structured
    language restrictions are SH-080781ENG-AG PDF p28: FX has ST and
    Structured Ladder/FBD, but no traditional LD in a structured project.
    Menu identities come from the existing frozen native CPU catalogue.
    """
    from plc.target_support import native_target_catalogue
    targets = native_target_catalogue()
    for target in targets:
        target["default_plc_model"] = (DEFAULT_PLC_MODEL if DEFAULT_PLC_MODEL in target["members"]
                                       else target["members"][0])
        if not target["program_supported"]:
            types = [("parameter", "参数工程", [])]
        elif target["menu_cpu"] == "FX1":
            types = [("simple", "简单工程", ["ladder"])]
        elif target["family"] == "FX":
            types = [("simple", "简单工程", ["ladder"]),
                     ("structured", "结构化工程", ["st", "fbd"])]
        else:
            types = [("simple", "简单工程", ["ladder", "st"]),
                     ("structured", "结构化工程", ["ladder", "st", "fbd"])]
        target["project_types"] = [{"value": value, "label": label,
                                    "target_modes": [{"value": mode, "label": _MODES[mode],
                                                      "labels_required": value == "structured" or mode == "st"}
                                                     for mode in modes],
                                    "labels_required": value == "structured",
                                    "labels_supported": value != "parameter" and not (
                                        value == "simple" and target["menu_cpu"] in _SIMPLE_WITHOUT_LABELS)}
                                   for value, label, modes in types]
    return {"plc_model": DEFAULT_PLC_MODEL, "default_target_mode": DEFAULT_TARGET_MODE,
            "starter_requirement": STARTER_REQUIREMENT, "native_targets": targets,
            "target_modes": [{"value": mode, "label": label} for mode, label in _MODES.items()]}


def normalize_project_selection(plc_model, target_mode="ladder", native_project_type=None, use_labels=None):
    """Validate the selected native combination without granting execution support."""
    from gxworks2.project_profiles import native_project_profile
    profile = native_project_profile(plc_model)
    if profile is None:
        raise ValueError("CPU is not a GX Works2 native New-dialog target")
    target = next(row for row in project_creation_options()["native_targets"] if row["menu_cpu"] == profile.cpu)
    project_type = native_project_type or ("parameter" if not profile.program_supported else
                                          "simple" if target_mode == "ladder" else "structured")
    option = next((row for row in target["project_types"] if row["value"] == project_type), None)
    if option is None:
        raise ValueError("Project type is unavailable for the selected CPU")
    if profile.program_supported and target_mode not in {row["value"] for row in option["target_modes"]}:
        raise ValueError("Language is unavailable for the selected CPU and project type")
    if not profile.program_supported and (target_mode != "ladder" or use_labels is True):
        raise ValueError("Parameter-only targets have no program language or labels")
    labels_required = option["labels_required"] or target_mode == "st"
    if use_labels is True and not option["labels_supported"]:
        raise ValueError("Labels are unavailable for the selected CPU and project type")
    if use_labels is False and labels_required:
        raise ValueError("This project type/language requires labels")
    selected_cpu = profile.cpu if str(plc_model).strip().upper() == profile.cpu.upper() else str(plc_model).strip().upper()
    return {"plc_model": selected_cpu, "target_mode": target_mode,
            "native_project_type": project_type,
            "use_labels": labels_required if use_labels is None else bool(use_labels)}

def project_capabilities(version=None):
    from plc.target_support import DEFAULT_TARGET_SUPPORT
    mode = str((version or {}).get("target_mode") or "ladder").lower()
    ladder = mode == "ladder"
    fbd = mode == "fbd"
    sfc = mode == "sfc"
    target = DEFAULT_TARGET_SUPPORT.target((version or {}).get("plc_model") or DEFAULT_PLC_MODEL)
    result = {"project_type": target.get("family", "unknown").lower(), "body_form": mode,
            "representations": ["ladder_svg", "st", "ir"] if ladder else ["fbd", "svg", "gxw"] if fbd else ["sfc", "gxw"] if sfc else ["st"],
            "operations": {"view": True, "generate": mode in ("ladder", "st", "fbd") or sfc and bool((version or {}).get('artifacts')),
                           "diagnose": ladder, "patch": ladder, "gx_import": ladder or fbd,
                           "simulation": ladder, "gx_compile": False, "fbd_edit": fbd, "gx_inspect": not fbd,
                           "fbd_convert": ladder, "sfc_edit": sfc},
            "sfc": "imported_child_source" if sfc else "requirement_input",
            "structured_semantics": "verified_native_templates" if fbd else "source_records_only" if sfc else "experimental_read_only"}

    result["creation"] = project_creation_options()
    if ((version or {}).get('validation') or {}).get('source_format') in {'instruction_sequence_v1', 'instruction_sequence_v2'}:
        result['representations'] = ['ordered_instructions', 'ladder_svg', 'ir', 'gxw']
    result["target_support"] = target
    if target.get("program_supported") is False:
        for operation in ("generate", "diagnose", "patch", "gx_import", "simulation", "fbd_edit", "fbd_convert", "sfc_edit"):
            result["operations"][operation] = False
    return result
