"""Headless SVG rendering shared by Web, desktop, and deterministic exports."""
import json
import re
import math
import unicodedata
from html import escape
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


class AdvancedSVGLadder:
    """One measured layout for previews, exports and navigation anchors.

    Storage steps are not drawing columns. Boxes use their complete text width;
    series paths wrap at the available wire span or eleven contact cells. A
    continuation is a display-only connection, never an extra PLC instruction.
    """

    DEFAULT_PALETTE = {
        "background": "#181818", "stroke": "#cccccc",
        "text": "#cccccc", "annotation": "#9cdcfe",
    }
    GXWORKS2_CONTACTS_PER_ROW = 11
    WRAP_ROW_HEIGHT = 85
    WRAPPED_CONTACT_STEP_W = 75
    _SIMPLE_CONTACT_TYPES = {"NO", "NC", "P", "RISING", "F", "FALLING"}

    def __init__(self, width=1100, height=600, palette=None):
        self._minimum_width = width
        self.width, self.height = width, height
        self.palette = dict(self.DEFAULT_PALETTE, **(palette or {}))
        self.shapes = []
        self.device_comments = {}
        self.rung_bounds = {}
        self.rung_bounds_by_display = {}
        self.rung_display_map = build_rung_display_map({})
        self.rung_display_to_raw = {}
        self.rung_raw_to_display = {}
        self.rung_display_to_path = {}
        self._continuation = 0
        self._label_id = 0

    @staticmethod
    def text_width(text, size=13):
        """Font-independent advance also used by navigation's text anchors."""
        return sum(0 if unicodedata.combining(ch) else
                   size if unicodedata.east_asian_width(ch) in {"W", "F"} else
                   size * .85 if ch in "MW@%" else size * .62
                   for ch in str(text))

    @classmethod
    def _wrap_text(cls, text, width, size=11):
        lines = []
        for paragraph in str(text).replace("\t", "    ").split("\n"):
            line, used = "", 0
            for char in paragraph:
                advance = cls.text_width(char, size)
                if line and used + advance > width:
                    lines.append(line)
                    line, used = "", 0
                line += char
                used += advance
            lines.append(line)
        return lines

    def draw_line(self, x1, y1, x2, y2, width=2, color=None):
        self.shapes.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{color or self.palette["stroke"]}" stroke-width="{width}" />')

    def draw_text(self, x, y, text, color=None, size=13, anchor="middle", weight="normal"):
        for index, line in enumerate(str(text).split("\n")):
            # Fix the planned advance across browser font fallbacks. This is
            # layout, not truncation; labels have already been line-wrapped.
            advance = self.text_width(line, size)
            length = f' textLength="{advance}" lengthAdjust="spacingAndGlyphs"' if advance else ""
            self.shapes.append(
                f'<text x="{x}" y="{y + index * 15}" font-family="Arial, sans-serif" '
                f'font-weight="{weight}" font-size="{size}" fill="{color or self.palette["text"]}" '
                f'text-anchor="{anchor}"{length}>{escape(line)}</text>')

    def _label(self, element):
        return str(element.get("label") or self.device_comments.get(element.get("address", ""), "") or "")

    @staticmethod
    def _box_text(element):
        kind = element.get("type", "")
        if kind == "APP_INSTR":
            return " ".join([str(element.get("opcode", "")), *map(str, element.get("operands", []))]).strip()
        if kind in {"PLS", "PLF"}:
            return f'{kind} {element.get("address", "")}'.strip()
        return str(element.get("expression") or element.get("address") or "")

    def _symbol_width(self, element):
        kind = element.get("type", "")
        if kind in self._SIMPLE_CONTACT_TYPES:
            return max(50, self.text_width(element.get("address", "")) + 10)
        if kind in {"COIL", "TIMER", "COUNTER"}:
            return max(70, self.text_width(element.get("address", "")) + 20,
                       (self.text_width(element.get("value", ""), 12) + 30) * 2)
        minimum = 95 if kind in {"COMPARE", "BLOCK_INPUT"} else 115
        return max(minimum, self.text_width(self._box_text(element), 11) + 24)

    def _slot_width(self, element):
        if element.get("type") == "parallel_block":
            return max((sum(self._slot_width(item) for item in branch)
                        for branch in element.get("branches", [])), default=75)
        return max(self.WRAPPED_CONTACT_STEP_W, self._symbol_width(element) + 20)

    def draw_element_symbol(self, x, y, element):
        kind = element.get("type", "")
        address = str(element.get("address", ""))
        label = self._label(element)
        self.shapes.append(f'<g class="ladder-element" data-type="{escape(kind, quote=True)}">')
        self.shapes.append(f'<title>{escape((address or self._box_text(element)) + (": " + label if label else ""))}</title>')
        half = self._symbol_width(element) / 2
        if kind in self._SIMPLE_CONTACT_TYPES:
            self.draw_line(x - half, y, x - 12, y, width=1.5)
            self.draw_line(x + 12, y, x + half, y, width=1.5)
            self.draw_line(x - 12, y - 15, x - 12, y + 15, width=2.5)
            self.draw_line(x + 12, y - 15, x + 12, y + 15, width=2.5)
            if kind == "NC":
                self.draw_line(x - 16, y + 13, x + 16, y - 13, width=2.5)
            elif kind in {"P", "RISING", "F", "FALLING"}:
                self.draw_text(x, y + 4, "↑" if kind in {"P", "RISING"} else "↓", size=14, weight="bold")
            self.draw_text(x, y - 20, address)
        elif kind in {"COIL", "TIMER", "COUNTER"}:
            for sign in (-1, 1):
                self.shapes.append(f'<path d="M {x + sign * 16} {y-15} Q {x + sign * 26} {y} {x + sign * 16} {y+15}" '
                                   f'stroke="{self.palette["stroke"]}" stroke-width="2.5" fill="none" />')
            self.draw_line(x - half, y, x - 21, y, width=1.5)
            self.draw_line(x + 21, y, x + half, y, width=1.5)
            self.draw_text(x, y + 4, address)
            if kind in {"TIMER", "COUNTER"}:
                self.draw_text(x + 22, y - 10, element.get("value", ""), anchor="start", size=12, weight="bold")
        else:
            for sign in (-1, 1):
                side = x + sign * half
                self.draw_line(side, y - 14, side, y + 14, width=2.5)
                self.draw_line(side, y - 14, side - sign * 6, y - 14, width=2.5)
                self.draw_line(side, y + 14, side - sign * 6, y + 14, width=2.5)
            self.draw_text(x, y + 4, self._box_text(element), size=11, weight="bold")
        bottom = y + 45
        if label:
            width = self._slot_width(element) - 12
            lines = self._wrap_text(label, width)
            label_top, label_height = y + 14, len(lines) * 15 + 4
            self._label_id += 1
            clip_id = f"annotation-{self._label_id}"
            self.shapes.append(f'<defs><clipPath id="{clip_id}"><rect x="{x-width/2}" y="{label_top}" '
                               f'width="{width}" height="{label_height}" /></clipPath></defs>')
            self.shapes.append(f'<g class="device-annotation" clip-path="url(#{clip_id})">')
            self.draw_text(x, y + 25, "\n".join(lines), color=self.palette["annotation"], size=11)
            self.shapes.append('</g>')
            bottom = max(bottom, label_top + label_height)
        self.shapes.append('</g>')
        return bottom

    def _wrap(self, x, y, bottom, left, edge):
        serial = self._continuation
        self._continuation += 1
        source = edge - 48
        self.draw_line(x, y, max(x, source - 25), y, width=1.5)
        self.draw_text(source, y + 4, f"K{serial}", size=12)
        self.draw_text(edge - 14, y + 5, ">", size=18)
        next_y = max(y + self.WRAP_ROW_HEIGHT, bottom + 40)
        self.draw_text(left + 22, next_y + 4, f"K{serial}", size=12)
        self.draw_text(left + 65, next_y + 5, ">", size=18)
        self.draw_line(left + 78, next_y, left + 90, next_y, width=1.5)
        return left + 90, next_y, next_y + 45, 0

    def _series(self, elements, state, *, left, edge):
        x, y, bottom, count = state
        for element in elements:
            width = self._slot_width(element)
            parallel = element.get("type") == "parallel_block"
            limit = edge - 90  # reserve the continuation label, not wire cells
            if x + width > limit or count >= self.GXWORKS2_CONTACTS_PER_ROW:
                # A wide parallel group can itself contain wrapped branches.
                if x > left + 90 or count or not parallel:
                    x, y, bottom, count = self._wrap(x, y, bottom, left, edge)
            if parallel:
                x, y, bottom = self._parallel(element, x, y, bottom, edge=edge)
                count += max(1, math.ceil(width / self.WRAPPED_CONTACT_STEP_W))
                continue
            center, half = x + width / 2, self._symbol_width(element) / 2
            self.draw_line(x, y, center - half, y, width=1.5)
            bottom = max(bottom, self.draw_element_symbol(center, y, element))
            self.draw_line(center + half, y, x + width, y, width=1.5)
            x += width
            count += 1
        return x, y, bottom, count

    def _parallel(self, element, x, y, bottom, *, edge):
        branches = element.get("branches", []) or [[]]
        natural_width = self._slot_width(element)
        wrapped = x + natural_width > edge - 90 or any(len(branch) > 11 for branch in branches)
        join = edge - 110 if wrapped else x + natural_width
        starts, ends = [], []
        row_y = y
        for branch in branches:
            starts.append(row_y)
            # Wrapped continuations stay inside both vertical join gutters.
            branch_edge = join - 10 if wrapped else join + 90
            bx, by, bb, _ = self._series(branch, (x, row_y, row_y + 45, 0), left=x, edge=branch_edge)
            self.draw_line(bx, by, join, by, width=1.5)
            ends.append(by)
            bottom = max(bottom, bb)
            row_y = bottom + 40
        if len(branches) > 1:
            self.draw_line(x, starts[0], x, starts[-1], width=1.5)
            self.draw_line(join, ends[0], join, ends[-1], width=1.5)
        return join, ends[0], bottom

    def _outputs(self, outputs, state, *, left, edge):
        x, y, bottom, count = state
        if not outputs:
            self.draw_line(x, y, edge, y, width=1.5)
            return bottom
        widths = [self._slot_width(item) for item in outputs]
        split = edge - 20 - max(widths)
        if x > split:
            x, y, bottom, count = self._wrap(x, y, bottom, left, edge)
        self.draw_line(x, y, split, y, width=1.5)
        first_y = last_y = y
        for index, (output, width) in enumerate(zip(outputs, widths)):
            out_y = y if index == 0 else bottom + 40
            center = edge - 20 - width / 2
            half = self._symbol_width(output) / 2
            self.draw_line(split, out_y, center - half, out_y, width=1.5)
            bottom = max(bottom, self.draw_element_symbol(center, out_y, output))
            self.draw_line(center + half, out_y, edge, out_y, width=1.5)
            last_y = out_y
        if len(outputs) > 1:
            self.draw_line(split, first_y, split, last_y, width=1.5)
        return bottom

    def generate_ladder(self, json_str, highlight_rung_ids=None):
        parsed = json.loads(json_str)
        if is_plc_ir(parsed):
            parsed = ir_to_ladder(parsed)
        rungs = parsed if isinstance(parsed, list) else parsed.get("rungs", [])
        self.device_comments = {} if isinstance(parsed, list) else parsed.get("device_comments", {})
        self.shapes, self.rung_bounds, self.rung_bounds_by_display = [], {}, {}
        self._continuation = self._label_id = 0
        self.rung_display_map = build_rung_display_map(parsed)
        self.rung_display_to_raw = dict(self.rung_display_map["display_to_raw"])
        self.rung_raw_to_display = dict(self.rung_display_map["raw_to_display"])
        self.rung_display_to_path = dict(self.rung_display_map["display_to_path"])
        highlighted = {str(item) for item in (highlight_rung_ids or [])}

        def atoms(value):
            if isinstance(value, dict):
                if "type" in value and value["type"] != "parallel_block":
                    yield value
                for child in value.values():
                    yield from atoms(child)
            elif isinstance(value, list):
                for child in value:
                    yield from atoms(child)

        # Unusually long individual instructions remain whole and readable.
        # Only such indivisible atoms may grow the canvas; a long series wraps.
        largest = max((self._slot_width(item) for item in atoms(rungs)), default=75)
        self.width = max(self._minimum_width, largest + 540)
        left, edge = 60, self.width - 60
        visible_rungs = list(rungs)
        last_outputs = (rungs[-1].get("branches") or [{}])[-1].get("outputs", []) if rungs else []
        if rungs and not (last_outputs and self._box_text(last_outputs[-1]).strip().upper() == "END"):
            visible_rungs.append({"_display_only_end": True, "branches": [{"inputs": [], "outputs": [
                {"type": "BLOCK_OUTPUT", "expression": "END"}]}]})
        base_y = 80
        for index, rung in enumerate(visible_rungs):
            start_shape, top = len(self.shapes), base_y - 42
            raw_id = rung.get("rung_id")
            display = None if rung.get("_display_only_end") else index + 1
            if display is not None:
                self.draw_text(left - 20, base_y + 5, display, size=14, weight="bold")
            prefix = ([rung["header_element"]] if rung.get("header_element") else []) + rung.get("shared_inputs", [])
            state = self._series(prefix, (left, base_y, base_y + 45, 0), left=left, edge=edge)
            branches = rung.get("branches", [])
            if len(branches) > 1 and state[0] > left + 150:
                state = self._wrap(*state[:3], left, edge)
            fork_x, fork_y, bottom, count = state
            branch_starts = []
            for branch_index, branch in enumerate(branches):
                by = fork_y if branch_index == 0 else bottom + 40
                branch_starts.append(by)
                local = self._series(branch.get("inputs", []), (fork_x, by, max(bottom, by + 45), count),
                                     left=fork_x if len(branches) > 1 else left, edge=edge)
                bottom = self._outputs(branch.get("outputs", []), local,
                                       left=fork_x if len(branches) > 1 else left, edge=edge)
            if len(branch_starts) > 1:
                self.draw_line(fork_x, branch_starts[0], fork_x, branch_starts[-1], width=1.5)
            height = bottom - top + 30
            bounds = {"top": top, "height": height, "display_number": display,
                      "raw_rung_id": raw_id, "json_path": f"$.rungs[{index}]" if display else ""}
            if display is not None:
                self.rung_bounds_by_display[display] = bounds
                if raw_id is not None:
                    self.rung_bounds[raw_id] = bounds
                if str(raw_id) in highlighted:
                    self.shapes.insert(start_shape, f'<rect x="{left+4}" y="{top}" width="{edge-left-8}" '
                                       f'height="{height}" rx="6" fill="#cca700" fill-opacity="0.14" stroke="#cca700" stroke-width="2" />')
            base_y = bottom + 75
        self.height = max(120, base_y + 10)
        drawing = self.shapes
        self.shapes = []
        self.draw_line(left, 20, left, self.height - 20, width=3.5)
        self.draw_line(edge, 20, edge, self.height - 20, width=3.5)
        self.shapes.extend(drawing)
        return (f'<svg width="{self.width}" height="{self.height}" xmlns="http://www.w3.org/2000/svg">'
                f'<rect x="0" y="0" width="{self.width}" height="{self.height}" fill="{self.palette["background"]}"/>'
                + "\n".join(self.shapes) + "\n</svg>")
