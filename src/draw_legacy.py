# ========================================================
# 文件名：draw.py
# ========================================================
import json
import csv

from gxworks2.csv_manager import CSVManager
from ladder_display import build_rung_display_map
from plc_ir import ir_to_ladder, is_plc_ir

def generate_gx_works2_csv(
    json_data,
    output_program_csv="MAIN.csv",
    output_comment_csv="COMMENT.csv",
    *,
    infer_device_comments=True,
):
    import csv
    import re

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
    
    current_step = 0

    def get_operands(elem):
        t, expr = elem.get("type", ""), elem.get("expression", "").strip()
        if t == "COMPARE" or re.search(r'[<=>]', expr):
            parts = expr.split()
            if not parts: return [""]
            if parts[0] in ["=", ">", "<", "<=", ">=", "<>"]: return parts[1:]
            if len(parts) >= 3: return [parts[0], parts[2]]
            return parts
        return [elem.get("address", "")]

    def get_step_size(inst, operands):
        inst_up = inst.upper()
        if inst_up in ["LDP", "LDF", "ANDP", "ANDF", "ORP", "ORF"]: return 2
        if inst_up in ["LD", "LDI", "AND", "ANI", "OR", "ORI", "SET", "MPS", "MRD", "MPP", "ANB", "ORB"]: return 1
        if inst_up == "RST": return 2 if operands and any(operands[0].upper().startswith(x) for x in ["T", "C", "D", "V", "Z"]) else 1
        if inst_up == "OUT": return 3 if operands and operands[0].upper().startswith(("T", "C")) else 1
        if any(inst_up.startswith(x) for x in ["LD=", "AND=", "OR=", "LD>", "AND>", "OR>", "LD<", "AND<"]): return 5
        if inst_up in ["MOV", "ZRST"]: return 5
        if inst_up == "PID": return 9
        return 1

    def add_instruction(inst, operands, comment=""):
        nonlocal current_step
        step_size = get_step_size(inst, operands)
        
        formatted_ops = [format_device(op) for op in operands if op]
        
        inst_up = inst.upper()
        is_contact = inst_up.startswith(("LD", "AN", "OR")) and inst_up not in ["ORB", "ANB"]
        is_sys_block = inst_up in ["MPS", "MRD", "MPP", "ORB", "ANB", "END"]
        
        valid_note = comment if comment != "null" else ""
        if is_contact or is_sys_block:
            valid_note = ""
            
        combined_ops = " ".join(formatted_ops)
        
        # 当前指令行，注解列强制留空
        rows.append([str(current_step), "", inst, combined_ops, "", "", ""])
        
        current_step += step_size
        
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
                str(current_step),
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

    try:
        with open(output_program_csv, mode="w", newline="", encoding="utf-16") as f_prog:
            writer = csv.writer(f_prog, delimiter="\t", quoting=csv.QUOTE_ALL, lineterminator="\r\n")
            writer.writerows(rows)
        return True
    except Exception as e:
        print(f"主程序CSV生成失败: {e}")
        return False
import json
import re


def normalize_svg_for_preview(svg_text, theme="dark"):
    """Render legacy or current ladder SVG colors for the selected UI theme."""
    text = str(svg_text or "")
    is_light = str(getattr(theme, "value", theme)).lower() == "light"
    background = "#ffffff" if is_light else "#181818"
    stroke = "#1e1e1e" if is_light else "#cccccc"
    annotation = "#0066b8" if is_light else "#9cdcfe"
    text = re.sub(
        r'(stroke\s*=\s*["\'])(?:black|#000(?:000)?|#1e1e1e|#ccc(?:ccc)?)(["\'])',
        rf'\g<1>{stroke}\2',
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r'(fill\s*=\s*["\'])(?:black|#000(?:000)?|#1e1e1e|#ccc(?:ccc)?)(["\'])',
        rf'\g<1>{stroke}\2',
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r'(<rect\b[^>]*\bfill\s*=\s*["\'])(?:white|#fff(?:fff)?|#181818)(["\'])',
        rf'\g<1>{background}\2',
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r'(fill\s*=\s*["\'])(?:#9cdcfe|#0066b8)(["\'])',
        rf'\g<1>{annotation}\2',
        text,
        flags=re.IGNORECASE,
    )
    return text


def normalize_svg_for_dark_preview(svg_text):
    """Backward-compatible dark preview helper."""
    return normalize_svg_for_preview(svg_text, "dark")

class AdvancedSVGLadder:
    DEFAULT_PALETTE = {
        "background": "#181818",
        "stroke": "#cccccc",
        "text": "#cccccc",
        "annotation": "#9cdcfe",
    }

    def __init__(self, width=1100, height=600, palette=None):
        self.width = width
        self.height = height
        self.shapes = []
        self.device_comments = {}
        self.rung_bounds = {}
        self.rung_bounds_by_display = {}
        self.rung_display_map = build_rung_display_map({})
        self.rung_display_to_raw = {}
        self.rung_raw_to_display = {}
        self.rung_display_to_path = {}
        self.palette = dict(self.DEFAULT_PALETTE)
        if palette:
            self.palette.update(palette)
        
    def draw_line(self, x1, y1, x2, y2, width=2, color=None):
        color = color or self.palette["stroke"]
        self.shapes.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}" />')
        
    def draw_text(self, x, y, text, color=None, size=13, anchor="middle", weight="normal"):
        color = color or self.palette["text"]
        text = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        
        lines = text.split('\n')
        for i, line in enumerate(lines):
            self.shapes.append(f'<text x="{x}" y="{y + i*15}" font-family="Arial, Courier New" font-weight="{weight}" font-size="{size}" fill="{color}" text-anchor="{anchor}">{line}</text>')

    def draw_element_symbol(self, x, y, elem):
        if not elem: return
        t = elem.get("type", "")
        W, H = 12, 15
        
        
        addr = elem.get("address", "")
        
        
        label = elem.get("label")
        if not label and addr:
            label = self.device_comments.get(addr, "")
            
        if label:
            self.draw_text(x, y + 25, label, color=self.palette["annotation"], size=11)
            
        if t == "NO":
            self.draw_line(x - W, y - H, x - W, y + H, width=2.5)
            self.draw_line(x + W, y - H, x + W, y + H, width=2.5)
            self.draw_line(x - 25, y, x - W, y, width=1.5)
            self.draw_line(x + W, y, x + 25, y, width=1.5)
            self.draw_text(x, y - 20, addr)
            
        elif t in ["P", "RISING"]:
            self.draw_line(x - W, y - H, x - W, y + H, width=2.5)
            self.draw_line(x + W, y - H, x + W, y + H, width=2.5)
            self.draw_line(x - 25, y, x - W, y, width=1.5)
            self.draw_line(x + W, y, x + 25, y, width=1.5)
            self.draw_text(x, y + 4, "↑", size=14, weight="bold")
            self.draw_text(x, y - 20, addr)
            
        elif t in ["F", "FALLING"]:
            self.draw_line(x - W, y - H, x - W, y + H, width=2.5)
            self.draw_line(x + W, y - H, x + W, y + H, width=2.5)
            self.draw_line(x - 25, y, x - W, y, width=1.5)
            self.draw_line(x + W, y, x + 25, y, width=1.5)
            self.draw_text(x, y + 4, "↓", size=14, weight="bold")
            self.draw_text(x, y - 20, addr)
            
        elif t == "NC":
            self.draw_line(x - W, y - H, x - W, y + H, width=2.5)
            self.draw_line(x + W, y - H, x + W, y + H, width=2.5)
            self.draw_line(x - W - 4, y + H - 2, x + W + 4, y - H + 2, width=2.5) 
            self.draw_line(x - 25, y, x - W, y, width=1.5)
            self.draw_line(x + W, y, x + 25, y, width=1.5)
            self.draw_text(x, y - 20, addr)
            
        elif t == "COIL":
            stroke = self.palette["stroke"]
            self.shapes.append(f'<path d="M {x-16} {y-H} Q {x-26} {y} {x-16} {y+H}" stroke="{stroke}" stroke-width="2.5" fill="none" />')
            self.shapes.append(f'<path d="M {x+16} {y-H} Q {x+26} {y} {x+16} {y+H}" stroke="{stroke}" stroke-width="2.5" fill="none" />')
            self.draw_line(x - 35, y, x - 30, y, width=1.5)
            self.draw_line(x + 30, y, x + 35, y, width=1.5)
            self.draw_text(x, y + 4, addr)
            
        elif t in ["PLS", "PLF"]:
            bw, bh = 115, 28  
            self.draw_line(x - bw/2, y - bh/2, x - bw/2, y + bh/2, width=2.5)
            self.draw_line(x - bw/2, y - bh/2, x - bw/2 + 6, y - bh/2, width=2.5)
            self.draw_line(x - bw/2, y + bh/2, x - bw/2 + 6, y + bh/2, width=2.5)
            self.draw_line(x + bw/2, y - bh/2, x + bw/2, y + bh/2, width=2.5)
            self.draw_line(x + bw/2, y - bh/2, x + bw/2 - 6, y - bh/2, width=2.5)
            self.draw_line(x + bw/2, y + bh/2, x + bw/2 - 6, y + bh/2, width=2.5)
            self.draw_line(x - 70, y, x - bw/2, y, width=1.5)
            self.draw_line(x + bw/2, y, x + 70, y, width=1.5)
            self.draw_text(x, y + 4, f"{t} {addr}", weight="bold", size=11)
            
        elif t in ["TIMER", "COUNTER"]:
            stroke = self.palette["stroke"]
            self.shapes.append(f'<path d="M {x-16} {y-H} Q {x-26} {y} {x-16} {y+H}" stroke="{stroke}" stroke-width="2.5" fill="none" />')
            self.shapes.append(f'<path d="M {x+16} {y-H} Q {x+26} {y} {x+16} {y+H}" stroke="{stroke}" stroke-width="2.5" fill="none" />')
            self.draw_line(x - 35, y, x - 30, y, width=1.5)
            self.draw_line(x + 30, y, x + 35, y, width=1.5)
            self.draw_text(x, y + 4, addr)
            self.draw_text(x + 22, y - 10, elem.get("value", ""), anchor="start", size=12, weight="bold")
            
        elif t == "APP_INSTR":
            bw, bh = 115, 28  
            self.draw_line(x - bw/2, y - bh/2, x - bw/2, y + bh/2, width=2.5)
            self.draw_line(x - bw/2, y - bh/2, x - bw/2 + 6, y - bh/2, width=2.5)
            self.draw_line(x - bw/2, y + bh/2, x - bw/2 + 6, y + bh/2, width=2.5)
            self.draw_line(x + bw/2, y - bh/2, x + bw/2, y + bh/2, width=2.5)
            self.draw_line(x + bw/2, y - bh/2, x + bw/2 - 6, y - bh/2, width=2.5)
            self.draw_line(x + bw/2, y + bh/2, x + bw/2 - 6, y + bh/2, width=2.5)
            self.draw_line(x - 70, y, x - bw/2, y, width=1.5)
            self.draw_line(x + bw/2, y, x + 70, y, width=1.5)
            opcode = elem.get("opcode", "")
            operands = " ".join(elem.get("operands", []))
            display_text = f"{opcode} {operands}".strip()
            self.draw_text(x, y + 4, display_text, weight="bold", size=11)
            
        elif any(sym in elem.get("expression", "") for sym in ["=", ">", "<"]) or t == "COMPARE":
            bw, bh = 95, 28  
            self.draw_line(x - bw/2, y - bh/2, x - bw/2, y + bh/2, width=2.5)
            self.draw_line(x - bw/2, y - bh/2, x - bw/2 + 6, y - bh/2, width=2.5)
            self.draw_line(x - bw/2, y + bh/2, x - bw/2 + 6, y + bh/2, width=2.5)
            self.draw_line(x + bw/2, y - bh/2, x + bw/2, y + bh/2, width=2.5)
            self.draw_line(x + bw/2, y - bh/2, x + bw/2 - 6, y - bh/2, width=2.5)
            self.draw_line(x + bw/2, y + bh/2, x + bw/2 - 6, y + bh/2, width=2.5)
            self.draw_line(x - 60, y, x - bw/2, y, width=1.5)
            self.draw_line(x + bw/2, y, x + 60, y, width=1.5)
            display_text = elem.get("expression", "") if elem.get("expression") else addr
            self.draw_text(x, y + 4, display_text, weight="bold", size=11)
            
        elif t == "BLOCK_INPUT":
            bw, bh = 90, 26
            self.draw_line(x - bw/2, y - bh/2, x - bw/2, y + bh/2, width=2)
            self.draw_line(x - bw/2, y - bh/2, x - bw/2 + 5, y - bh/2, width=2)
            self.draw_line(x - bw/2, y + bh/2, x - bw/2 + 5, y + bh/2, width=2)
            self.draw_line(x + bw/2, y - bh/2, x + bw/2, y + bh/2, width=2)
            self.draw_line(x + bw/2, y - bh/2, x + bw/2 - 5, y - bh/2, width=2)
            self.draw_line(x + bw/2, y + bh/2, x + bw/2 - 5, y + bh/2, width=2)
            self.draw_line(x - 60, y, x - bw/2, y, width=1.5)
            self.draw_line(x + bw/2, y, x + 60, y, width=1.5)
            self.draw_text(x, y + 4, elem.get("expression", ""), weight="bold", size=11)
            
        elif t == "BLOCK_OUTPUT":
            bw, bh = 115, 28  
            self.draw_line(x - bw/2, y - bh/2, x - bw/2, y + bh/2, width=2.5)
            self.draw_line(x - bw/2, y - bh/2, x - bw/2 + 6, y - bh/2, width=2.5)
            self.draw_line(x - bw/2, y + bh/2, x - bw/2 + 6, y + bh/2, width=2.5)
            self.draw_line(x + bw/2, y - bh/2, x + bw/2, y + bh/2, width=2.5)
            self.draw_line(x + bw/2, y - bh/2, x + bw/2 - 6, y - bh/2, width=2.5)
            self.draw_line(x + bw/2, y + bh/2, x + bw/2 - 6, y + bh/2, width=2.5)
            self.draw_line(x - 70, y, x - bw/2, y, width=1.5)
            self.draw_line(x + bw/2, y, x + 70, y, width=1.5)
            display_text = elem.get("expression", "")
            self.draw_text(x, y + 4, display_text, weight="bold", size=11)

    def generate_ladder(self, json_str, highlight_rung_ids=None):
        self.shapes = []
        self.rung_bounds = {}
        self.rung_bounds_by_display = {}
        highlight_rung_ids = {
            int(item) for item in (highlight_rung_ids or [])
            if str(item).lstrip("-").isdigit()
        }
        parsed_data = json.loads(json_str)
        if is_plc_ir(parsed_data):
            parsed_data = ir_to_ladder(parsed_data)
        if isinstance(parsed_data, list):
            rungs = parsed_data
            self.device_comments = {}
        else:
            rungs = parsed_data.get("rungs", [])
            self.device_comments = parsed_data.get("device_comments", {})

        # Visible rung numbers are version-local positions.  Raw rung_id stays
        # untouched because reports, repairs and partial merges use it as an
        # anchor.  Expose both directions for UI/report/export consumers.
        self.rung_display_map = build_rung_display_map(parsed_data)
        self.rung_display_to_raw = dict(
            self.rung_display_map.get("display_to_raw") or {}
        )
        self.rung_raw_to_display = dict(
            self.rung_display_map.get("raw_to_display") or {}
        )
        self.rung_display_to_path = dict(
            self.rung_display_map.get("display_to_path") or {}
        )
            
        left_bus, right_bus = 60, 1040
        
        if rungs and not (rungs[-1].get("branches") and rungs[-1]["branches"][-1].get("outputs") and rungs[-1]["branches"][-1]["outputs"][-1].get("expression") == "END"):
            last_raw_id = rungs[-1].get("rung_id")
            synthetic_raw_id = (
                last_raw_id + 1 if isinstance(last_raw_id, int) else None
            )
            rungs.append({
                "rung_id": synthetic_raw_id,
                "_display_only_end": True,
                "header_element": None,
                "branches": [{"branch_id": 1, "y_offset_level": 0, "inputs": [], "outputs": [{"type": "BLOCK_OUTPUT", "expression": "END", "label": ""}]}]
            })
            
        total_rows = 0
        for rung in rungs:
            rung_rows = 0
            for branch in rung.get("branches", []):
                max_in_rows = 1
                for elem in branch.get("inputs", []):
                    if elem["type"] == "parallel_block":
                        max_in_rows = max(max_in_rows, len(elem.get("branches", [])))
                rung_rows += max(max_in_rows, len(branch.get("outputs", [])))
            total_rows += rung_rows + 1
            
        estimated_height = total_rows * 85 + 100
        self.height = estimated_height
        
        self.draw_line(left_bus, 20, left_bus, estimated_height - 20, width=3.5)
        self.draw_line(right_bus, 20, right_bus, estimated_height - 20, width=3.5)
        
        base_y = 80
        step_w = 120  
        
        for rung_index, rung in enumerate(rungs):
            rung_shape_start = len(self.shapes)
            rung_top = max(24, base_y - 42)
            display_only_end = bool(rung.get("_display_only_end"))
            display_number = None if display_only_end else rung_index + 1
            raw_rung_id = rung.get("rung_id")
            if display_number is not None:
                self.draw_text(
                    left_bus - 20,
                    base_y + 5,
                    str(display_number),
                    size=14,
                    weight="bold",
                )
            cx_after_header = left_bus
            
            if rung.get("header_element"):
                header_center_x = left_bus + step_w / 2
                self.draw_line(left_bus, base_y, header_center_x - 25, base_y, width=1.5)
                self.draw_element_symbol(header_center_x, base_y, rung["header_element"])
                self.draw_line(header_center_x + 25, base_y, left_bus + step_w, base_y, width=1.5)
                cx_after_header = left_bus + step_w

            for shared_elem in rung.get("shared_inputs", []):
                center_x = cx_after_header + step_w / 2
                self.draw_line(
                    cx_after_header,
                    base_y,
                    center_x - 25,
                    base_y,
                    width=1.5,
                )
                self.draw_element_symbol(center_x, base_y, shared_elem)
                self.draw_line(
                    center_x + 25,
                    base_y,
                    cx_after_header + step_w,
                    base_y,
                    width=1.5,
                )
                cx_after_header += step_w
            
            branches = rung.get("branches", [])
            current_rung_row = 0
            branch_y_positions = []
            
            for branch in branches:
                branch_start_row = current_rung_row
                by = base_y + branch_start_row * 85
                branch_y_positions.append(by)
                
                self.draw_line(cx_after_header, by, cx_after_header + 15, by, width=1.5)
                bx = cx_after_header + 15
                
                max_in_rows = 1
                for elem in branch.get("inputs", []):
                    if elem["type"] == "parallel_block":
                        sub_branches = elem.get("branches", [])
                        sub_rows = len(sub_branches)
                        max_in_rows = max(max_in_rows, sub_rows)
                        
                        max_sub_len = max(len(sb) for sb in sub_branches) if sub_branches else 1
                        block_w = max_sub_len * step_w
                        
                        left_join_x = bx 
                        right_join_x = left_join_x + block_w

                        for sb_idx, sub_b in enumerate(sub_branches):
                            sby = by + sb_idx * 85
                            sbx = left_join_x
                            
                            for sb_elem in sub_b:
                                center_x = sbx + step_w / 2
                                self.draw_line(sbx, sby, center_x - 25, sby, width=1.5)
                                self.draw_element_symbol(center_x, sby, sb_elem)
                                self.draw_line(center_x + 25, sby, sbx + step_w, sby, width=1.5)
                                sbx += step_w
                                
                            if sbx < right_join_x:
                                self.draw_line(sbx, sby, right_join_x, sby, width=1.5)
                                
                        if sub_rows > 1:
                            sby_max = by + (sub_rows - 1) * 85
                            self.draw_line(left_join_x, by, left_join_x, sby_max, width=1.5)
                            self.draw_line(right_join_x, by, right_join_x, sby_max, width=1.5)
                            
                        bx = right_join_x
                    else:
                        center_x = bx + step_w / 2
                        
                        expr = elem.get("expression", "")
                        is_block = elem.get("type") in ["BLOCK_INPUT", "COMPARE"] or any(sym in expr for sym in ["=", ">", "<"])
                        gap_r = 50 if is_block else 25  
                        
                        self.draw_line(bx, by, center_x - gap_r, by, width=1.5)
                        self.draw_element_symbol(center_x, by, elem)
                        self.draw_line(center_x + gap_r, by, bx + step_w, by, width=1.5)
                        bx += step_w
                    
                outputs = branch.get("outputs", [])
                if outputs:
                    out_x = 940
                    split_out_x = out_x - 70
                    self.draw_line(bx, by, split_out_x, by, width=1.5)
                    
                    if len(outputs) > 1:
                        self.draw_line(split_out_x, by, split_out_x, by + (len(outputs) - 1) * 85, width=1.5)
                        
                    for o_idx, output in enumerate(outputs):
                        out_y = by + o_idx * 85
                        if output["type"] in ["BLOCK_OUTPUT", "PLS", "PLF", "APP_INSTR"]:
                         left_p, right_p = out_x - 70, out_x + 70
                        else: 
                         left_p, right_p = out_x - 35, out_x + 35
                        
                        self.draw_line(split_out_x, out_y, left_p, out_y, width=1.5)
                        self.draw_element_symbol(out_x, out_y, output)
                        self.draw_line(right_p, out_y, right_bus, out_y, width=1.5)
                else:
                    if bx < right_bus: self.draw_line(bx, by, right_bus, by, width=1.5)
                    
                current_rung_row += max(max_in_rows, len(outputs))
                
            if len(branch_y_positions) > 1:
                self.draw_line(cx_after_header, branch_y_positions[0], cx_after_header, branch_y_positions[-1], width=1.5)
                
            rung_height = max(60, current_rung_row * 85 + 60)
            bounds = {
                "top": rung_top,
                "height": rung_height,
                "display_number": display_number,
                "raw_rung_id": raw_rung_id,
                "json_path": (
                    "" if rung.get("_display_only_end") else "$.rungs[%d]" % rung_index
                ),
            }
            if display_number is not None:
                self.rung_bounds_by_display[display_number] = bounds
            if raw_rung_id not in (None, "") and not display_only_end:
                self.rung_bounds[raw_rung_id] = bounds
            if raw_rung_id in highlight_rung_ids and not display_only_end:
                self.shapes.insert(
                    rung_shape_start,
                    (
                        f'<rect x="64" y="{rung_top}" width="972" '
                        f'height="{rung_height}" rx="6" fill="#cca700" '
                        'fill-opacity="0.14" stroke="#cca700" '
                        'stroke-width="2" />'
                    ),
                )
            base_y += rung_height

        svg_header = (
            f'<svg width="{self.width}" height="{self.height}" '
            'xmlns="http://www.w3.org/2000/svg">'
            f'<rect x="0" y="0" width="{self.width}" height="{self.height}" '
            f'fill="{self.palette["background"]}"/>'
        )
        return svg_header + "\n".join(self.shapes) + "\n</svg>"
