"""Canonical GX Works2 CSV export, native step widths, and export-only lowering."""
import csv
import logging
from gxworks2.csv_manager import CSVManager
from gxworks2.native_export import GXNativeLoweringError, lower_large_parallel_blocks_for_gxworks2
from plc.ir import ir_to_ladder, is_plc_ir
from plc.instruction_steps import StepCursor, instruction_step_width

def _step_width_model(payload, override):
    """Keep an explicit CPU through IR-to-ladder projection; default legacy FX."""
    if override is not None:
        return str(override)
    if isinstance(payload, dict):
        plc = payload.get("plc")
        if isinstance(plc, dict) and plc.get("cpu"):
            return str(plc["cpu"])
        if payload.get("plc_model"):
            return str(payload["plc_model"])
    return "FX3U"


def _write_program_csv(
    json_data,
    output_program_csv="MAIN.csv",
    output_comment_csv="COMMENT.csv",
    *,
    infer_device_comments=True,
    plc_model=None,
    step_diagnostics=None,
):
    import csv
    import re

    plc_model = _step_width_model(json_data, plc_model)
    if is_plc_ir(json_data):
        json_data = ir_to_ladder(json_data)

    declared_comments = {}
    rungs = []

    if isinstance(json_data, dict) and "rungs" in json_data:
        rungs = json_data.get("rungs", [])
        declared_comments = json_data.get("device_comments", {})
    elif isinstance(json_data, list):
        rungs = json_data

    def format_device(addr):
        if not addr: return ""
        addr = addr.strip().upper()
        match = re.match(r'^([XY])([0-7]+)$', addr)
        if match: return f"{match.group(1)}{match.group(2).zfill(3)}"
        return addr

    device_comments = {}
    comment_devices = set()

    def add_missing_comment(addr, comment):
        if not addr:
            return
        identity = addr.strip().upper()
        match = re.fullmatch(r"([A-Z]+)(\d+)", identity)
        if match:
            identity = match.group(1) + (match.group(2).lstrip("0") or "0")
        if identity not in comment_devices:
            comment_devices.add(identity)
            device_comments[addr] = comment

    # Project declarations are authoritative, including explicit empty values.
    # Work on a separate collection and identify aliases before inferring any
    # labels, so X1/x001 cannot overwrite or duplicate the same device comment.
    for addr, comment in declared_comments.items():
        add_missing_comment(addr, comment)

    if infer_device_comments:
        for rung in rungs:
            header = rung.get("header_element")
            if header and header.get("label"):
                if header.get("address"):
                    add_missing_comment(header["address"], header["label"])
                else:
                    parts = header.get("expression", "").strip().split()
                    if len(parts) >= 2:
                        addr = parts[1] if parts[0] in ["=", ">", "<", "<=", ">=", "<>"] else parts[0]
                        if re.fullmatch(r'[A-Za-z]+\d+', addr):
                            add_missing_comment(addr, header["label"])

            for branch in rung.get("branches", []):
                for elem in branch.get("inputs", []):
                    if elem.get("type") == "parallel_block":
                        for sub_b in elem.get("branches", []):
                            for sub_elem in sub_b:
                                if sub_elem.get("address") and sub_elem.get("label"):
                                    add_missing_comment(sub_elem["address"], sub_elem["label"])
                    else:
                        if elem.get("address") and elem.get("label"):
                            add_missing_comment(elem["address"], elem["label"])
                for out in branch.get("outputs", []):
                    if out.get("address") and out.get("label"):
                        add_missing_comment(out["address"], out["label"])
            for elem in rung.get("shared_inputs", []):
                if elem.get("address") and elem.get("label"):
                    add_missing_comment(elem["address"], elem["label"])

    # 2. 生成注释
    comment_rows = [["COMMENT - 副本"], ["软元件名", "注释"]]
    for addr, comment in device_comments.items():
        if addr and comment and comment != "null":
            comment_rows.append([format_device(addr), comment])

    try:
        with open(output_comment_csv, mode="w", newline="", encoding="utf-16") as f_comment:
            writer = csv.writer(f_comment, delimiter="\t", quoting=csv.QUOTE_ALL, lineterminator="\r\n")
            writer.writerows(comment_rows)
    except Exception as e:
        print(f"注释CSV生成失败: {e}")

    # 3. 编译器核心逻辑
    rows = [
        ["MAIN - 副本"],
        ["PLC信息:", "三菱 GX Works2 兼容"],
        ["步号", "行间声明", "指令", "I/O(软元件)", "空白栏", "PI声明", "注解"]
    ]

    cursor = StepCursor()
    width_findings = []

    def get_operands(elem):
        t, expr = elem.get("type", ""), elem.get("expression", "").strip()
        if t == "COMPARE" or re.search(r'[<=>]', expr):
            parts = expr.split()
            if not parts: return [""]
            if parts[0] in ["=", ">", "<", "<=", ">=", "<>"]: return parts[1:]
            if len(parts) >= 3: return [parts[0], parts[2]]
            return parts
        return [elem.get("address", "")]

    def add_instruction(inst, operands, comment=""):
        width = instruction_step_width(inst, operands, plc_model=plc_model)
        if not width.known:
            width_findings.append({"opcode": inst, "operands": list(operands),
                                   "step": cursor.step, "reason": width.reason})

        formatted_ops = [format_device(op) for op in operands if op]

        inst_up = inst.upper()
        is_contact = inst_up.startswith(("LD", "AN", "OR")) and inst_up not in ["ORB", "ANB"]
        is_sys_block = inst_up in ["MPS", "MRD", "MPP", "ORB", "ANB", "END"]

        valid_note = comment if comment != "null" else ""
        if is_contact or is_sys_block:
            valid_note = ""

        combined_ops = " ".join(formatted_ops)

        # 当前指令行，注解列强制留空
        rows.append([cursor.label, "", inst, combined_ops, "", "", ""])

        cursor.advance(width)

        if valid_note:
            rows.append(["", "", "", "", "", "", valid_note])

    def get_input_inst(elem, is_first, is_sub=False):
        t, expr = elem.get("type", ""), elem.get("expression", "").strip()
        if t == "COMPARE" or re.search(r'[<=>]', expr):
            sym = re.search(r'([<=>]+)', expr)
            sym = sym.group(1) if sym else "="
            return f"LD{sym}" if is_first else (f"OR{sym}" if is_sub else f"AND{sym}")

        if is_first:
            return "LDI" if t == "NC" else "LDP" if t in ["P", "RISING"] else "LDF" if t in ["F", "FALLING"] else "LD"
        elif is_sub:
            return "ORI" if t == "NC" else "ORP" if t in ["P", "RISING"] else "ORF" if t in ["F", "FALLING"] else "OR"
        else:
            return "ANI" if t == "NC" else "ANDP" if t in ["P", "RISING"] else "ANDF" if t in ["F", "FALLING"] else "AND"

    def parse_input_list(inputs, is_first_input):
        for i, elem in enumerate(inputs):
            is_first = (is_first_input and i == 0)

            if elem.get("type") == "parallel_block":
                valid_branches = [b for b in elem.get("branches", []) if b]
                if not valid_branches: continue

                if len(valid_branches) == 1:
                    for k, sub_elem in enumerate(valid_branches[0]):
                        inst = get_input_inst(sub_elem, is_first=(is_first and k==0))
                        add_instruction(inst, get_operands(sub_elem), sub_elem.get("label", ""))
                else:
                    for j, sub_b in enumerate(valid_branches):
                        if len(sub_b) == 1:
                            sub_elem = sub_b[0]
                            inst = get_input_inst(sub_elem, is_first=(j==0), is_sub=(j>0))
                            add_instruction(inst, get_operands(sub_elem), sub_elem.get("label", ""))
                        else:
                            for k, sub_elem in enumerate(sub_b):
                                inst = get_input_inst(sub_elem, is_first=(k==0))
                                add_instruction(inst, get_operands(sub_elem), sub_elem.get("label", ""))
                            if j > 0: add_instruction("ORB", [])
                    if not is_first: add_instruction("ANB", [])
            else:
                inst = get_input_inst(elem, is_first=is_first)
                add_instruction(inst, get_operands(elem), elem.get("label", ""))

    def parse_outputs(outputs):
        for out in outputs:
            t, addr, label = out.get("type"), out.get("address", ""), out.get("label", "")
            if t == "COIL": add_instruction("OUT", [addr], label)
            elif t in ["PLS", "PLF"]: add_instruction(t, [addr], label)
            elif t in ["TIMER", "COUNTER"]: add_instruction("OUT", [addr, out.get("value", "K0")], label)
            elif t == "APP_INSTR": add_instruction(out.get("opcode", ""), out.get("operands", []), label)
            elif t == "BLOCK_OUTPUT":
                parts = out.get("expression", "").strip().split()
                if len(parts) >= 2: add_instruction(parts[0], parts[1:], label)

    for rung in rungs:
        line_statement = rung.get("debug_note", "")
        if line_statement and line_statement != "null":
            rows.append([
                cursor.label,
                CSVManager.truncate_statement(line_statement),
                "", "", "", "", "",
            ])

        header = rung.get("header_element")
        shared_inputs = rung.get("shared_inputs", [])
        branches = rung.get("branches", [])
        has_prefix = False

        if header:
            inst = get_input_inst(header, is_first=True)
            add_instruction(inst, get_operands(header), header.get("label", ""))
            has_prefix = True

        if shared_inputs:
            parse_input_list(shared_inputs, is_first_input=not has_prefix)
            has_prefix = True

        if not branches: continue

        if len(branches) == 1:
            parse_input_list(branches[0].get("inputs", []), is_first_input=not has_prefix)
            parse_outputs(branches[0].get("outputs", []))
        else:
            if has_prefix:
                add_instruction("MPS", [])
                for b_idx, branch in enumerate(branches):
                    if 0 < b_idx < len(branches) - 1:
                        add_instruction("MRD", [])
                    elif b_idx == len(branches) - 1:
                        add_instruction("MPP", [])

                    parse_input_list(branch.get("inputs", []), is_first_input=False)
                    parse_outputs(branch.get("outputs", []))
            else:
                for branch in branches:
                    parse_input_list(branch.get("inputs", []), is_first_input=True)
                    parse_outputs(branch.get("outputs", []))

    add_instruction("END", [])

    if step_diagnostics is not None:
        step_diagnostics.extend(width_findings)
    if width_findings:
        logging.getLogger(__name__).warning(
            "GX Works2 CSV: %d unresolved instruction widths; subsequent step labels "
            "are blank, instructions are preserved. First: %s",
            len(width_findings), width_findings[0],
        )

    try:
        with open(output_program_csv, mode="w", newline="", encoding="utf-16") as f_prog:
            writer = csv.writer(f_prog, delimiter="\t", quoting=csv.QUOTE_ALL, lineterminator="\r\n")
            writer.writerows(rows)
        return True
    except Exception as e:
        print(f"主程序CSV生成失败: {e}")
        return False


def _generate_native_wrapped_csv(
    json_data,
    output_program_csv="MAIN.csv",
    output_comment_csv="COMMENT.csv",
    *,
    infer_device_comments=True,
    plc_model=None,
    step_diagnostics=None,
):
    """Compatibility wrapper; steps are assigned once from the Core catalogue."""
    return _write_program_csv(
        json_data,
        output_program_csv,
        output_comment_csv,
        infer_device_comments=infer_device_comments,
        plc_model=plc_model,
        step_diagnostics=step_diagnostics,
    )


def generate_gx_works2_csv(
    json_data,
    output_program_csv="MAIN.csv",
    output_comment_csv="COMMENT.csv",
    *,
    infer_device_comments=True,
    plc_model=None,
    step_diagnostics=None,
):
    """Generate GX Works2 CSV with native step widths and <=24-row OR blocks.

    The canonical ladder/IR is not changed. Oversized top-level parallel
    networks are lowered only for the GX Works2 CSV artifact. Unknown widths
    leave later step labels blank and produce diagnostics, never a model retry
    or a new rejection of the user's program. Native import of such an unresolved
    listing still needs verification; a successful export is not a compile claim.
    """
    from plc.ir import ir_to_ladder, is_plc_ir
    from plc.device_identity import canonical_ladder_devices
    plc_model = _step_width_model(json_data, plc_model)
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
        plc_model=plc_model,
        step_diagnostics=step_diagnostics,
    )

