import copy
import csv
import json
import xml.etree.ElementTree as ET

import pytest

from rendering.ladder_svg import AdvancedSVGLadder
from gxworks2.csv_export import generate_gx_works2_csv
from gxworks2.native_export import lower_large_parallel_blocks_for_gxworks2


def _read_program_rows(path):
    with path.open("r", encoding="utf-16", newline="") as handle:
        return list(csv.reader(handle, delimiter="\t"))


def _instruction_rows(path):
    return [
        row
        for row in _read_program_rows(path)[3:]
        if len(row) >= 4 and str(row[2] or "").strip()
    ]


def _out_terminated_blocks(instructions):
    blocks = []
    current = []
    for row in instructions:
        op = str(row[2] or "").strip().upper()
        if op == "END":
            break
        current.append(row)
        if op == "OUT":
            blocks.append(current)
            current = []
    assert not current
    return blocks


def test_fx3u_step_labels_account_for_pls_and_inc(tmp_path):
    program = tmp_path / "program.csv"
    comments = tmp_path / "comments.csv"
    ladder = {
        "device_comments": {},
        "rungs": [
            {
                "rung_id": 0,
                "debug_note": "",
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "NO", "address": "X0", "label": ""}],
                        "outputs": [{"type": "PLS", "address": "M11", "label": ""}],
                    }
                ],
            },
            {
                "rung_id": 1,
                "debug_note": "",
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "NO", "address": "X1", "label": ""}],
                        "outputs": [
                            {
                                "type": "APP_INSTR",
                                "opcode": "INC",
                                "operands": ["D220"],
                                "label": "",
                            }
                        ],
                    }
                ],
            },
            {
                "rung_id": 2,
                "debug_note": "",
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "NO", "address": "X2", "label": ""}],
                        "outputs": [{"type": "COIL", "address": "Y0", "label": ""}],
                    }
                ],
            },
        ],
    }

    assert generate_gx_works2_csv(ladder, program, comments)
    instructions = [
        (row[0], row[2], row[3]) for row in _instruction_rows(program)
    ]

    assert instructions[:6] == [
        ("0", "LD", "X000"),
        ("1", "PLS", "M11"),
        ("3", "LD", "X001"),
        ("4", "INC", "D220"),
        ("7", "LD", "X002"),
        ("8", "OUT", "Y000"),
    ]


def test_svg_wraps_m100_m115_after_m110_with_k0_pair():
    ladder = {
        "device_comments": {},
        "rungs": [
            {
                "rung_id": 78,
                "debug_note": "",
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [
                            {"type": "NO", "address": f"M{index}", "label": ""}
                            for index in range(100, 116)
                        ],
                        "outputs": [
                            {"type": "COIL", "address": "M13", "label": ""},
                            {
                                "type": "BLOCK_OUTPUT",
                                "expression": "RST M12",
                                "label": "",
                            },
                        ],
                    }
                ],
            }
        ],
    }

    svg = AdvancedSVGLadder().generate_ladder(
        json.dumps(ladder, ensure_ascii=False)
    )
    root = ET.fromstring(svg)
    texts = [
        (
            str(element.text or ""),
            float(element.attrib["x"]),
            float(element.attrib["y"]),
        )
        for element in root.iter()
        if element.tag.endswith("text")
    ]

    by_text = {}
    for text, x, y in texts:
        by_text.setdefault(text, []).append((x, y))

    assert len(by_text["K0"]) == 2
    assert by_text["M110"][0][1] + 85 == by_text["M111"][0][1]
    assert by_text["M111"][0][0] < by_text["M110"][0][0]
    assert by_text["M13"][0][1] + 85 == by_text["RST M12"][0][1]


def _range_network(rung_id, output, low, high, pulse_low, pulse_high):
    branches = []
    for index in range(16):
        branches.append(
            [
                {"type": "NO", "address": f"M{100 + index}", "label": ""},
                {
                    "type": "BLOCK_INPUT",
                    "expression": f">= D{200 + index} {low}",
                    "label": "",
                },
                {
                    "type": "BLOCK_INPUT",
                    "expression": f"<= D{200 + index} {high}",
                    "label": "",
                },
                {
                    "type": "BLOCK_INPUT",
                    "expression": f">= D{220 + index} {pulse_low}",
                    "label": "",
                },
                {
                    "type": "BLOCK_INPUT",
                    "expression": f"<= D{220 + index} {pulse_high}",
                    "label": "",
                },
            ]
        )
    return {
        "rung_id": rung_id,
        "debug_note": "",
        "header_element": None,
        "shared_inputs": [],
        "branches": [
            {
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [{"type": "parallel_block", "branches": branches}],
                "outputs": [{"type": "COIL", "address": output, "label": ""}],
            }
        ],
    }


def test_large_orb_networks_are_split_into_gxworks2_convertible_blocks(tmp_path):
    program = tmp_path / "program.csv"
    comments = tmp_path / "comments.csv"
    ladder = {
        "device_comments": {},
        "rungs": [
            _range_network(492, "M30", "K1", "K3", "D101", "D102"),
            _range_network(844, "M31", "K4", "K6", "D103", "D104"),
            _range_network(1196, "M32", "K7", "K9", "D105", "D106"),
        ],
    }

    assert generate_gx_works2_csv(ladder, program, comments)
    instructions = _instruction_rows(program)
    blocks = _out_terminated_blocks(instructions)

    assert len(blocks) == 15
    assert [len(block) for block in blocks] == [24, 24, 24, 24, 5] * 3
    assert max(map(len, blocks)) <= 24

    target_blocks = [
        block for block in blocks if block[-1][3] in {"M30", "M31", "M32"}
    ]
    assert [block[-1][3] for block in target_blocks] == ["M30", "M31", "M32"]
    for block in target_blocks:
        assert [row[2] for row in block] == ["LD", "OR", "OR", "OR", "OUT"]

    helper_outputs = [
        block[-1][3]
        for block in blocks
        if block[-1][3] not in {"M30", "M31", "M32"}
    ]
    assert len(helper_outputs) == 12
    assert len(set(helper_outputs)) == 12
    assert all(address.startswith("M") for address in helper_outputs)
    assert all(0 <= int(address[1:]) <= 7679 for address in helper_outputs)

    operands = [row[3] for row in instructions]
    for address in (f"M{index}" for index in range(100, 116)):
        assert sum(address in value.split() for value in operands) == 3


def test_export_lowering_is_copy_only_and_skips_used_high_m_relay():
    ladder = {
        "device_comments": {"M7679": "reserved by user"},
        "rungs": [
            _range_network(492, "M30", "K1", "K3", "D101", "D102"),
        ],
    }
    original = copy.deepcopy(ladder)

    lowered = lower_large_parallel_blocks_for_gxworks2(ladder)

    assert ladder == original
    assert len(lowered["rungs"]) == 5

    helper_outputs = [
        rung["branches"][0]["outputs"][0]["address"]
        for rung in lowered["rungs"][:-1]
    ]
    assert helper_outputs == ["M7678", "M7677", "M7676", "M7675"]

    final_inputs = lowered["rungs"][-1]["branches"][0]["inputs"][0]
    assert final_inputs["type"] == "parallel_block"
    assert [
        branch[0]["address"] for branch in final_inputs["branches"]
    ] == helper_outputs

    first_devices = []
    for helper_rung in lowered["rungs"][:-1]:
        helper_block = helper_rung["branches"][0]["inputs"][0]
        assert len(helper_block["branches"]) == 4
        first_devices.extend(
            branch[0]["address"] for branch in helper_block["branches"]
        )
    assert first_devices == [f"M{index}" for index in range(100, 116)]


SVG = "{http://www.w3.org/2000/svg}"


def _layout_program(inputs, *, header=None, shared=None, outputs=None):
    return {"device_comments": {}, "rungs": [{"rung_id": 42, "header_element": header,
            "shared_inputs": shared or [], "branches": [{"branch_id": 1, "y_offset_level": 0,
            "inputs": inputs, "outputs": outputs or [{"type": "COIL", "address": "Y0"}]}]}]}


def _assert_svg_contained(renderer, root):
    for line in root.iter(SVG + "line"):
        x1, x2, y1, y2 = (float(line.attrib[k]) for k in ("x1", "x2", "y1", "y2"))
        assert 0 <= x1 <= renderer.width and 0 <= x2 <= renderer.width
        assert 0 <= y1 <= renderer.height and 0 <= y2 <= renderer.height
        if line.get("stroke-width") == "1.5":
            assert x1 <= x2, "wire must not backtrack through a contact or instruction"
    for bounds in renderer.rung_bounds.values():
        assert bounds["top"] + bounds["height"] <= renderer.height


@pytest.mark.parametrize("placement", ["inputs", "shared", "header_and_shared", "mixed", "parallel"])
@pytest.mark.parametrize("length", [8, 11, 12, 24])
def test_all_condition_containers_wrap_by_available_width_without_moving_logic(placement, length):
    contacts = [{"type": "NO", "address": f"M{100+i}"} for i in range(length)]
    if placement == "inputs":
        source = _layout_program(contacts)
    elif placement == "shared":
        source = _layout_program([], shared=contacts)
    elif placement == "header_and_shared":
        source = _layout_program(contacts[-2:], header=contacts[0], shared=contacts[1:-2])
    elif placement == "mixed":
        contacts[length // 2] = {"type": "COMPARE", "expression": ">= D32760 K32767"}
        source = _layout_program(contacts)
    else:
        source = _layout_program([{"type": "parallel_block", "branches": [contacts, [{"type": "NC", "address": "X0"}]]},
                                 {"type": "NO", "address": "X1"}])
    original = copy.deepcopy(source)
    renderer = AdvancedSVGLadder()
    root = ET.fromstring(renderer.generate_ladder(json.dumps(source)))
    _assert_svg_contained(renderer, root)
    text = [e.text for e in root.iter(SVG + "text")]
    for element in contacts:
        assert text.count(element.get("address") or element["expression"]) == 1
    assert source == original
    continuation = [word for word in text if word and word.startswith("K") and word[1:].isdigit()]
    if length > 11:
        assert continuation
    assert all(continuation.count(word) == 2 for word in set(continuation))
    assert renderer.rung_bounds[42]["display_number"] == 1


@pytest.mark.parametrize("output", [
    {"type": "APP_INSTR", "opcode": "MOV", "operands": ["K32767", "D7999"]},
    {"type": "APP_INSTR", "opcode": "ZRST", "operands": ["D7990", "D7999"]},
    {"type": "APP_INSTR", "opcode": "SFTL", "operands": ["M100", "M1000", "K128", "K1"]},
    {"type": "BLOCK_OUTPUT", "expression": "MOV K32767 D7999"},
])
def test_instruction_boxes_reserve_complete_text_span_and_do_not_overlap_contacts(output):
    source = _layout_program([{"type": "NO", "address": f"M{i}"} for i in range(11)], outputs=[output])
    renderer = AdvancedSVGLadder()
    root = ET.fromstring(renderer.generate_ladder(json.dumps(source)))
    _assert_svg_contained(renderer, root)
    group = next(e for e in root.iter(SVG + "g") if e.attrib.get("data-type") == output["type"])
    text = next(group.iter(SVG + "text"))
    expected = output.get("expression") or " ".join([output["opcode"], *output["operands"]])
    assert text.text == expected
    rails = [e for e in group.iter(SVG + "line") if e.attrib["x1"] == e.attrib["x2"]]
    left, right = sorted(float(e.attrib["x1"]) for e in rails)
    center, advance = float(text.attrib["x"]), float(text.attrib["textLength"])
    assert left + 8 <= center - advance / 2 < center + advance / 2 <= right - 8


def test_five_step_instruction_listing_and_svg_layout_are_independent(tmp_path):
    from plc.instruction_steps import instruction_step_width
    output = {"type": "APP_INSTR", "opcode": "MOV", "operands": ["K32767", "D7999"]}
    source = _layout_program([{"type": "NO", "address": "M0"}], outputs=[output])
    assert instruction_step_width("MOV", output["operands"]).steps == 5
    program, comments = tmp_path / "program.csv", tmp_path / "comments.csv"
    assert generate_gx_works2_csv(source, program, comments)
    assert [(r[0], r[2]) for r in _instruction_rows(program)] == [("0", "LD"), ("1", "MOV"), ("6", "END")]
    assert "MOV K32767 D7999" in AdvancedSVGLadder().generate_ladder(json.dumps(source))


@pytest.mark.parametrize("comment", ["相邻触点的中文说明不能相互遮挡" * 5, "VeryLongUnbrokenDeviceAnnotation_" * 8,
                                     "第一行<&>\n第二行 mixed 日本語テスト" * 4])
def test_annotation_wraps_completely_with_cell_clipping_and_vertical_space(comment):
    source = _layout_program([{"type": "NO", "address": "X0", "label": comment},
                              {"type": "NC", "address": "X1", "label": comment}],
                             outputs=[{"type": "APP_INSTR", "opcode": "MOV", "operands": ["K1", "D10"], "label": comment}])
    source["rungs"].append(_layout_program([{"type": "NO", "address": "X2"}])["rungs"][0] | {"rung_id": 43})
    renderer = AdvancedSVGLadder()
    root = ET.fromstring(renderer.generate_ladder(json.dumps(source)))
    _assert_svg_contained(renderer, root)
    groups = [e for e in root.iter(SVG + "g") if e.attrib.get("class") == "device-annotation"]
    assert len(groups) == 3
    clips = {e.attrib["id"]: next(e.iter(SVG + "rect")) for e in root.iter(SVG + "clipPath")}
    for group in groups:
        lines = list(group.iter(SVG + "text"))
        assert "".join(e.text or "" for e in lines) == comment.replace("\n", "")
        clip = clips[group.attrib["clip-path"][5:-1]]
        width = float(clip.attrib["width"])
        assert all(float(e.attrib.get("textLength", "0")) <= width for e in lines)
        assert max(float(e.attrib["y"]) for e in lines) + 8 < renderer.rung_bounds[43]["top"]
    assert renderer.rung_bounds[43]["top"] >= renderer.rung_bounds[42]["top"] + renderer.rung_bounds[42]["height"]


# Interpret only SVG wires, switch gaps and continuation endpoints. This oracle
# is independent of the renderer's layout coordinates and PLC instruction lowering:
# an accidental join/short introduced by wrapping changes the observed outputs.
def _svg_energized_outputs(rung, bits):
    from collections import defaultdict

    ns = {"s": "http://www.w3.org/2000/svg"}
    root = ET.fromstring(AdvancedSVGLadder().generate_ladder(json.dumps({"rungs": [rung]})))
    segments, switches, jumps = [], [], []
    targets, continuations = {}, {}
    for line in root.findall(".//s:line", ns):
        # Wires and the supply rail, never switch plates or instruction brackets.
        if line.get("stroke-width") not in {"1.5", "3.5"}:
            continue
        x1, y1, x2, y2 = [round(float(line.get(key)), 6) for key in ("x1", "y1", "x2", "y2")]
        if x1 == x2 == float(root.get("width")) - 60:
            continue  # The right bus is not an alternate power source.
        segments.append(((x1, y1), (x2, y2)))
    for group in root.findall('.//s:g[@class="ladder-element"]', ns):
        kind = group.get("data-type")
        if kind not in {"NO", "NC", "COIL"}:
            continue
        text = group.find("s:text", ns)
        x = float(text.get("x"))
        y = float(text.get("y")) + (-4 if kind == "COIL" else 20)
        if kind == "COIL":
            targets[text.text] = (x - 21, y)
        elif bool(bits.get(text.text)) != (kind == "NC"):
            switches.append(((x - 12, y), (x + 12, y)))
    for text in root.findall("s:text", ns):
        if text.text and text.text.startswith("K") and text.text[1:].isdigit():
            continuations.setdefault(text.text, []).append((float(text.get("x")), float(text.get("y")) - 4))
    for source, destination in continuations.values():
        jumps.append(((source[0] - 25, source[1]), (destination[0] + 56, destination[1])))

    points = {p for segment in segments + switches + jumps for p in segment} | set(targets.values())
    for a, b in segments:
        for c, d in segments:
            if (a[1] == b[1] and c[0] == d[0]
                    and min(a[0], b[0]) <= c[0] <= max(a[0], b[0])
                    and min(c[1], d[1]) <= a[1] <= max(c[1], d[1])):
                points.add((c[0], a[1]))
    graph = defaultdict(set)
    for a, b in segments + switches:
        on_segment = sorted(p for p in points if
            (a[1] == b[1] == p[1] and min(a[0], b[0]) <= p[0] <= max(a[0], b[0])) or
            (a[0] == b[0] == p[0] and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])))
        for u, v in zip(on_segment, on_segment[1:]):
            graph[u].add(v)
            graph[v].add(u)
    for u, v in jumps:
        graph[u].add(v)
        graph[v].add(u)
    reached, pending = set(), [(60., 20.)]
    while pending:
        point = pending.pop()
        if point not in reached:
            reached.add(point)
            pending.extend(graph[point] - reached)
    return {address: point in reached for address, point in targets.items()}


@pytest.mark.parametrize("layout", ["series", "shared", "parallel", "successive_parallel"])
def test_wrapped_svg_connectivity_preserves_each_branch_condition(layout):
    import random
    def contact(i):
        return {"type": "NO", "address": f"M{i}"}
    def output(i):
        return {"type": "COIL", "address": f"Y{i}"}
    def branch(inputs, outputs):
        return {"inputs": inputs, "outputs": outputs}
    def parallel(left, right):
        return {"type": "parallel_block", "branches": [left, right]}
    shared = []
    if layout == "series":
        branches = [branch([contact(i) for i in range(24)], [output(0)])]
    elif layout == "shared":
        shared = [contact(i) for i in range(16)]
        branches = [branch([contact(i) for i in range(16, 38)], [output(0)]),
                    branch([contact(40)], [output(1)])]
    elif layout == "parallel":
        shared = [contact(1)]
        branches = [branch([parallel([contact(i) for i in range(2, 20)],
                                    [contact(i) for i in range(20, 30)]), contact(31)],
                           [output(0), output(1)])]
    else:
        shared = [contact(1)]
        branches = [branch([parallel([contact(i) for i in range(2, 20)], [contact(21)]),
                            parallel([contact(i) for i in range(22, 38)], [contact(40)])],
                           [output(0)])]
    rung = {"rung_id": 42, "shared_inputs": shared, "branches": branches}
    def enabled(items, bits):
        return all(any(enabled(path, bits) for path in item["branches"])
                   if item["type"] == "parallel_block" else bits[item["address"]]
                   for item in items)
    rng = random.Random(902)
    for iteration in range(50):
        bits = {f"M{i}": True for i in range(50)}
        for i in rng.sample(range(50), iteration % 5):
            bits[f"M{i}"] = False
        expected = {out["address"]: enabled(shared + b["inputs"], bits)
                    for b in branches for out in b["outputs"]}
        assert _svg_energized_outputs(rung, bits) == expected
