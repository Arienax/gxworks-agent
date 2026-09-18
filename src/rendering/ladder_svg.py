"""Headless SVG rendering shared by Web, desktop, and deterministic exports."""
import json
import re
from plc.ir import ir_to_ladder, is_plc_ir
from rendering.numbering import build_rung_display_map

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


class _BaseLadderRenderer:
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


class AdvancedSVGLadder(_BaseLadderRenderer):
    """GX Works2-like SVG renderer with native 11-contact serial wrapping."""

    GXWORKS2_CONTACTS_PER_ROW = 11
    WRAP_ROW_HEIGHT = 85
    WRAPPED_CONTACT_STEP_W = 75

    _SIMPLE_CONTACT_TYPES = {
        "NO",
        "NC",
        "P",
        "RISING",
        "F",
        "FALLING",
    }

    @classmethod
    def _serial_wrap_count(cls, inputs):
        items = list(inputs or [])
        if (
            len(items) <= cls.GXWORKS2_CONTACTS_PER_ROW
            or not all(
                str(item.get("type", "") or "").upper()
                in cls._SIMPLE_CONTACT_TYPES
                for item in items
            )
        ):
            return 1
        return (
            len(items) + cls.GXWORKS2_CONTACTS_PER_ROW - 1
        ) // cls.GXWORKS2_CONTACTS_PER_ROW

    def _draw_wrap_source(self, x, y, right_bus, serial):
        """Draw the right-side K<n> continuation source used by GX Works2."""
        label_x = right_bus - 58
        if x < label_x - 24:
            self.draw_line(x, y, label_x - 24, y, width=1.5)
        self.draw_text(label_x, y + 4, f"K{serial}", size=12)
        self.draw_text(right_bus - 18, y + 5, ">", size=18)

    def _draw_wrap_destination(self, left_bus, y, serial):
        """Draw the matching left-side K<n> continuation destination."""
        self.draw_text(left_bus + 22, y + 4, f"K{serial}", size=12)
        self.draw_text(left_bus + 65, y + 5, ">", size=18)
        start_x = left_bus + 90
        self.draw_line(left_bus + 78, y, start_x, y, width=1.5)
        return start_x

    def _requires_native_wrap_renderer(self, parsed_data):
        if is_plc_ir(parsed_data):
            parsed_data = ir_to_ladder(parsed_data)
        if isinstance(parsed_data, list):
            rungs = parsed_data
        else:
            rungs = (parsed_data or {}).get("rungs", [])
        for rung in rungs:
            for branch in rung.get("branches", []) or []:
                if self._serial_wrap_count(branch.get("inputs", [])) > 1:
                    return True
        return False

    def generate_ladder(self, json_str, highlight_rung_ids=None):
        parsed_probe = json.loads(json_str)
        if not self._requires_native_wrap_renderer(parsed_probe):
            return super().generate_ladder(json_str, highlight_rung_ids)

        self.shapes = []
        self.rung_bounds = {}
        self.rung_bounds_by_display = {}
        highlight_rung_ids = {
            int(item)
            for item in (highlight_rung_ids or [])
            if str(item).lstrip("-").isdigit()
        }
        parsed_data = parsed_probe
        if is_plc_ir(parsed_data):
            parsed_data = ir_to_ladder(parsed_data)
        if isinstance(parsed_data, list):
            rungs = parsed_data
            self.device_comments = {}
        else:
            rungs = parsed_data.get("rungs", [])
            self.device_comments = parsed_data.get("device_comments", {})

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

        if rungs and not (
            rungs[-1].get("branches")
            and rungs[-1]["branches"][-1].get("outputs")
            and rungs[-1]["branches"][-1]["outputs"][-1].get("expression")
            == "END"
        ):
            last_raw_id = rungs[-1].get("rung_id")
            synthetic_raw_id = (
                last_raw_id + 1 if isinstance(last_raw_id, int) else None
            )
            rungs.append(
                {
                    "rung_id": synthetic_raw_id,
                    "_display_only_end": True,
                    "header_element": None,
                    "branches": [
                        {
                            "branch_id": 1,
                            "y_offset_level": 0,
                            "inputs": [],
                            "outputs": [
                                {
                                    "type": "BLOCK_OUTPUT",
                                    "expression": "END",
                                    "label": "",
                                }
                            ],
                        }
                    ],
                }
            )

        total_rows = 0
        for rung in rungs:
            rung_rows = 0
            for branch in rung.get("branches", []):
                inputs = branch.get("inputs", [])
                serial_rows = self._serial_wrap_count(inputs)
                if serial_rows > 1:
                    rung_rows += (serial_rows - 1) + max(
                        1, len(branch.get("outputs", []))
                    )
                    continue
                max_in_rows = 1
                for elem in inputs:
                    if elem["type"] == "parallel_block":
                        max_in_rows = max(
                            max_in_rows, len(elem.get("branches", []))
                        )
                rung_rows += max(
                    max_in_rows, len(branch.get("outputs", []))
                )
            total_rows += rung_rows + 1

        estimated_height = total_rows * 85 + 100
        self.height = estimated_height

        self.draw_line(
            left_bus, 20, left_bus, estimated_height - 20, width=3.5
        )
        self.draw_line(
            right_bus, 20, right_bus, estimated_height - 20, width=3.5
        )

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
                self.draw_line(
                    left_bus,
                    base_y,
                    header_center_x - 25,
                    base_y,
                    width=1.5,
                )
                self.draw_element_symbol(
                    header_center_x, base_y, rung["header_element"]
                )
                self.draw_line(
                    header_center_x + 25,
                    base_y,
                    left_bus + step_w,
                    base_y,
                    width=1.5,
                )
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

                inputs = branch.get("inputs", [])
                serial_rows = self._serial_wrap_count(inputs)
                max_in_rows = 1
                output_base_y = by

                if serial_rows > 1:
                    chunks = [
                        inputs[
                            index : index
                            + self.GXWORKS2_CONTACTS_PER_ROW
                        ]
                        for index in range(
                            0,
                            len(inputs),
                            self.GXWORKS2_CONTACTS_PER_ROW,
                        )
                    ]
                    bx = cx_after_header + 15
                    for wrap_index, chunk in enumerate(chunks):
                        row_y = by + wrap_index * self.WRAP_ROW_HEIGHT
                        if wrap_index == 0:
                            row_bx = cx_after_header + 15
                            self.draw_line(
                                cx_after_header,
                                row_y,
                                row_bx,
                                row_y,
                                width=1.5,
                            )
                        else:
                            row_bx = self._draw_wrap_destination(
                                left_bus, row_y, wrap_index - 1
                            )

                        for elem in chunk:
                            center_x = (
                                row_bx
                                + self.WRAPPED_CONTACT_STEP_W / 2
                            )
                            self.draw_line(
                                row_bx,
                                row_y,
                                center_x - 25,
                                row_y,
                                width=1.5,
                            )
                            self.draw_element_symbol(
                                center_x, row_y, elem
                            )
                            self.draw_line(
                                center_x + 25,
                                row_y,
                                row_bx + self.WRAPPED_CONTACT_STEP_W,
                                row_y,
                                width=1.5,
                            )
                            row_bx += self.WRAPPED_CONTACT_STEP_W

                        if wrap_index < len(chunks) - 1:
                            self._draw_wrap_source(
                                row_bx,
                                row_y,
                                right_bus,
                                wrap_index,
                            )
                        else:
                            bx = row_bx
                            output_base_y = row_y
                    max_in_rows = len(chunks)
                else:
                    self.draw_line(
                        cx_after_header,
                        by,
                        cx_after_header + 15,
                        by,
                        width=1.5,
                    )
                    bx = cx_after_header + 15

                    for elem in inputs:
                        if elem["type"] == "parallel_block":
                            sub_branches = elem.get("branches", [])
                            sub_rows = len(sub_branches)
                            max_in_rows = max(max_in_rows, sub_rows)

                            max_sub_len = (
                                max(len(sb) for sb in sub_branches)
                                if sub_branches
                                else 1
                            )
                            block_w = max_sub_len * step_w

                            left_join_x = bx
                            right_join_x = left_join_x + block_w

                            for sb_idx, sub_b in enumerate(sub_branches):
                                sby = by + sb_idx * 85
                                sbx = left_join_x

                                for sb_elem in sub_b:
                                    center_x = sbx + step_w / 2
                                    self.draw_line(
                                        sbx,
                                        sby,
                                        center_x - 25,
                                        sby,
                                        width=1.5,
                                    )
                                    self.draw_element_symbol(
                                        center_x, sby, sb_elem
                                    )
                                    self.draw_line(
                                        center_x + 25,
                                        sby,
                                        sbx + step_w,
                                        sby,
                                        width=1.5,
                                    )
                                    sbx += step_w

                                if sbx < right_join_x:
                                    self.draw_line(
                                        sbx,
                                        sby,
                                        right_join_x,
                                        sby,
                                        width=1.5,
                                    )

                            if sub_rows > 1:
                                sby_max = by + (sub_rows - 1) * 85
                                self.draw_line(
                                    left_join_x,
                                    by,
                                    left_join_x,
                                    sby_max,
                                    width=1.5,
                                )
                                self.draw_line(
                                    right_join_x,
                                    by,
                                    right_join_x,
                                    sby_max,
                                    width=1.5,
                                )

                            bx = right_join_x
                        else:
                            center_x = bx + step_w / 2

                            expr = elem.get("expression", "")
                            is_block = elem.get("type") in [
                                "BLOCK_INPUT",
                                "COMPARE",
                            ] or any(
                                sym in expr for sym in ["=", ">", "<"]
                            )
                            gap_r = 50 if is_block else 25

                            self.draw_line(
                                bx,
                                by,
                                center_x - gap_r,
                                by,
                                width=1.5,
                            )
                            self.draw_element_symbol(
                                center_x, by, elem
                            )
                            self.draw_line(
                                center_x + gap_r,
                                by,
                                bx + step_w,
                                by,
                                width=1.5,
                            )
                            bx += step_w

                outputs = branch.get("outputs", [])
                if outputs:
                    out_x = 940
                    split_out_x = out_x - 70
                    self.draw_line(
                        bx,
                        output_base_y,
                        split_out_x,
                        output_base_y,
                        width=1.5,
                    )

                    if len(outputs) > 1:
                        self.draw_line(
                            split_out_x,
                            output_base_y,
                            split_out_x,
                            output_base_y + (len(outputs) - 1) * 85,
                            width=1.5,
                        )

                    for o_idx, output in enumerate(outputs):
                        out_y = output_base_y + o_idx * 85
                        if output["type"] in [
                            "BLOCK_OUTPUT",
                            "PLS",
                            "PLF",
                            "APP_INSTR",
                        ]:
                            left_p, right_p = out_x - 70, out_x + 70
                        else:
                            left_p, right_p = out_x - 35, out_x + 35

                        self.draw_line(
                            split_out_x,
                            out_y,
                            left_p,
                            out_y,
                            width=1.5,
                        )
                        self.draw_element_symbol(out_x, out_y, output)
                        self.draw_line(
                            right_p,
                            out_y,
                            right_bus,
                            out_y,
                            width=1.5,
                        )
                else:
                    if bx < right_bus:
                        self.draw_line(
                            bx,
                            output_base_y,
                            right_bus,
                            output_base_y,
                            width=1.5,
                        )

                if serial_rows > 1:
                    current_rung_row += (serial_rows - 1) + max(
                        1, len(outputs)
                    )
                else:
                    current_rung_row += max(
                        max_in_rows, len(outputs)
                    )

            if len(branch_y_positions) > 1:
                self.draw_line(
                    cx_after_header,
                    branch_y_positions[0],
                    cx_after_header,
                    branch_y_positions[-1],
                    width=1.5,
                )

            rung_height = max(60, current_rung_row * 85 + 60)
            bounds = {
                "top": rung_top,
                "height": rung_height,
                "display_number": display_number,
                "raw_rung_id": raw_rung_id,
                "json_path": (
                    ""
                    if rung.get("_display_only_end")
                    else "$.rungs[%d]" % rung_index
                ),
            }
            if display_number is not None:
                self.rung_bounds_by_display[display_number] = bounds
            if (
                raw_rung_id not in (None, "")
                and not display_only_end
            ):
                self.rung_bounds[raw_rung_id] = bounds
            if (
                raw_rung_id in highlight_rung_ids
                and not display_only_end
            ):
                self.shapes.insert(
                    rung_shape_start,
                    (
                        f'<rect x="64" y="{rung_top}" width="972" '
                        f'height="{rung_height}" rx="6" '
                        'fill="#cca700" fill-opacity="0.14" '
                        'stroke="#cca700" stroke-width="2" />'
                    ),
                )
            base_y += rung_height

        svg_header = (
            f'<svg width="{self.width}" height="{self.height}" '
            'xmlns="http://www.w3.org/2000/svg">'
            f'<rect x="0" y="0" width="{self.width}" '
            f'height="{self.height}" '
            f'fill="{self.palette["background"]}"/>'
        )
        return svg_header + "\n".join(self.shapes) + "\n</svg>"

