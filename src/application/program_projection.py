"""Deterministic navigation projections from the canonical program and renderer."""
from __future__ import annotations

import hashlib
import json
import re

from plc.ir import build_plc_ir, canonical_sha256, ir_to_ladder, validate_plc_ir
from plc.device_identity import canonical_device_map, canonical_ladder_devices


def explore_program(program, *, theme="dark"):
    from rendering.ladder_svg import AdvancedSVGLadder, normalize_svg_for_preview

    validate_plc_ir(program, validate_ladder=False)
    saved_digest = canonical_sha256(program)
    ladder = ir_to_ladder(program)
    canonical = canonical_ladder_devices(ladder)
    canonical_io = canonical_device_map(program.get("io_map", {}))
    if canonical != ladder or canonical_io != program.get("io_map", {}):
        # Navigation is a read-only projection. Coalesce legacy aliases without
        # rewriting saved evidence or changing the version's integrity hash.
        program = build_plc_ir(canonical, plc_model=program["plc"]["cpu"],
                               program_name=program["program_name"], revision=program["revision"],
                               io_map=canonical_io,
                               semantic_requirements=(program.get("logic") or {}).get("requirements", []),
                               analysis_config=(program.get("analysis") or {}).get("config", {}))
    class NavigationDrawer(AdvancedSVGLadder):
        def __init__(self):
            super().__init__()
            self.address_targets = []

        def draw_text(self, x, y, text, *args, **kwargs):
            size = kwargs.get("size", args[1] if len(args) > 1 else 13)
            anchor = kwargs.get("anchor", args[2] if len(args) > 2 else "middle")
            for line_index, line in enumerate(str(text).split("\n")):
                left = x if anchor == "start" else x - self.text_width(line, size) / (1 if anchor == "end" else 2)
                for match in re.finditer(r"(?<![A-Za-z0-9_])(?:[A-Za-z]+\d+)(?![A-Za-z0-9_])", line):
                    address = match.group().upper()
                    if address in program.get("devices", {}):
                        self.address_targets.append({"address": address, "x": left + self.text_width(line[:match.start()], size) - 3,
                            "y": y + line_index * 15 - size, "width": self.text_width(address, size) + 6, "height": size + 5})
            return super().draw_text(x, y, text, *args, **kwargs)

    drawer = NavigationDrawer()
    svg = drawer.generate_ladder(json.dumps(ir_to_ladder(program), ensure_ascii=False))
    networks = []
    for network in program["networks"]:
        bounds = drawer.rung_bounds.get(network["rung_id"], {})
        networks.append({key: network.get(key) for key in
                         ("id", "order", "rung_id", "comment", "reads", "writes", "instructions")}
                        | {"bounds": bounds})
    return {"ir_sha256": saved_digest, "networks": networks,
            "devices": program.get("devices", {}), "width": drawer.width, "height": drawer.height,
            "address_targets": drawer.address_targets, "svg": normalize_svg_for_preview(svg, theme)}


def issue_cards(program, findings, *, source="static", report_id=None):
    """Only link known anchors; preserve unresolved references for inspection."""
    networks = program.get("networks", []) if program else []
    known = {n["id"] for n in networks}
    rungs = {str(n["rung_id"]): n["id"] for n in networks}
    cards = []
    for finding in findings:
        refs = list(finding.get("network_refs") or [])
        refs.extend(rungs[str(r)] for r in finding.get("rung_ids", []) if str(r) in rungs)
        identity = str(finding.get("finding_id") or hashlib.sha256(json.dumps(
            finding, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20])
        cards.append({"id": f"{report_id or source}:{identity}", "source": finding.get("source", source),
                      "report_id": report_id, "severity": finding.get("severity", "info"),
                      "title": finding.get("title") or finding.get("code") or "程序问题",
                      "message": finding.get("message", ""), "suggestion": finding.get("suggestion", ""),
                      "evidence": finding.get("evidence") or [], "addresses": finding.get("addresses") or [],
                      "networks": sorted(set(refs) & known),
                      "unresolved_networks": sorted(set(refs) - known),
                      "confidence": finding.get("confidence", "unknown")})
    return cards
