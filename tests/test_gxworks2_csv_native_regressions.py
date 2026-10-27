import copy
import csv
import io
import json
import xml.etree.ElementTree as ET

import pytest
from hypothesis import example, given, settings, strategies as st

from rendering.ladder_svg import AdvancedSVGLadder
from gxworks2.csv_export import generate_gx_works2_csv
from gxworks2.native_export import GXNativeLoweringError, lower_large_parallel_blocks_for_gxworks2


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


@pytest.mark.parametrize('model,relay', [('FX3U','M8000'),('FX5U','SM400')])
@pytest.mark.parametrize('same_rung', [False, True])
def test_unconditional_output_has_an_independent_cpu_scoped_enable(tmp_path, model, relay, same_rung):
    from gxworks2.csv_importer import parse_gxworks2_csv
    from simulator.bounded import ScanMachine
    conditional = {'inputs':[{'type':'NO','address':'X0'}],
                   'outputs':[{'type':'APP_INSTR','opcode':'MOV','operands':['K3','D0']}]}
    unconditional = {'inputs':[], 'outputs':[{'type':'APP_INSTR','opcode':'MOV','operands':['K9','D1']}]}
    ladder = {'device_comments':{}, 'rungs': [
        {'rung_id':1,'header_element':None,'shared_inputs':[], 'branches':[conditional,unconditional]}
        ] if same_rung else [
        {'rung_id':1,'header_element':None,'shared_inputs':[], 'branches':[conditional]},
        {'rung_id':2,'header_element':None,'shared_inputs':[], 'branches':[unconditional]}]}
    before = copy.deepcopy(ladder)
    program,comments = tmp_path/'program.csv',tmp_path/'comments.csv'
    assert generate_gx_works2_csv(ladder,program,comments,plc_model=model)
    rows = _instruction_rows(program)
    assert [(row[2],row[3]) for row in rows][2:4] == [('LD',relay),('MOV','K9 D1')]
    parsed = parse_gxworks2_csv(program,comments)
    for candidate in (ladder,parsed.ladder):
        machine = ScanMachine(candidate,plc_model=model,initial={'D0':0,'D1':0},
            execution_context={'program_type':'scan','initial_execution_program':False})
        assert machine.scan({'inputs':{'X0':False}})['after']['D1'] == 9
        assert machine.scan({'inputs':{'X0':True}})['after']['D0'] == 3
        assert not machine.gaps
    assert ladder == before


def test_unknown_cpu_cannot_inherit_an_unconditional_fx3u_enable(tmp_path):
    ladder = {'device_comments':{},'rungs':[{'header_element':None,'shared_inputs':[],
        'branches':[{'inputs':[],'outputs':[{'type':'APP_INSTR','opcode':'MOV','operands':['K9','D1']}]}]}]}
    assert not generate_gx_works2_csv(ladder,tmp_path/'program.csv',tmp_path/'comments.csv',plc_model='UNKNOWN')
    assert not (tmp_path/'program.csv').exists()


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


def _shared_parallel_outputs(widths):
    return {"device_comments": {}, "rungs": [{
        "rung_id": 1, "header_element": None,
        "shared_inputs": [{"type": "NO", "address": "M0"}],
        "branches": [{
            "branch_id": index + 1, "y_offset_level": index,
            "inputs": [{"type": "parallel_block", "branches": [
                [{"type": "NO", "address": f"M{100 + index * 24 + offset}"}]
                for offset in range(width)]}],
            "outputs": [{"type": "COIL", "address": f"M{600 + index}"}],
        } for index, width in enumerate(widths)],
    }]}


@pytest.mark.parametrize("widths", [[12, 12], [12, 13], [1, 1, 1, 1, 1, 10, 10, 10]])
def test_export_checks_whole_shared_output_network_height(tmp_path, widths):
    source = _shared_parallel_outputs(widths)
    before = copy.deepcopy(source)
    program, comments = tmp_path / "program.csv", tmp_path / "comments.csv"
    if sum(widths) <= 24:
        assert generate_gx_works2_csv(source, program, comments)
    else:
        with pytest.raises(GXNativeLoweringError, match="超过24行"):
            lower_large_parallel_blocks_for_gxworks2(source)
        assert not generate_gx_works2_csv(source, program, comments)
        assert not program.exists() and not comments.exists()
    assert source == before


def test_oversized_shared_network_is_not_reported_as_compiled_artifacts(tmp_path):
    from plc.candidate_service import CandidateService
    from plc.ir import build_plc_ir
    source = _shared_parallel_outputs([1, 1, 1, 1, 1, 10, 10, 10])
    program = build_plc_ir(source, plc_model="FX3U")
    with pytest.raises(ValueError, match="CSV 渲染失败"):
        CandidateService().compile(program, tmp_path)
    assert not (tmp_path / "program.csv").exists()


def test_repartitioned_sorting_shape_exports_two_independent_shared_networks(tmp_path):
    from plc.candidate_service import CandidateService
    source = _shared_parallel_outputs([1, 1, 1, 1, 1, 10, 10, 10])
    service = CandidateService()
    prepared = service.prepare(source, plc_model="FX3U")
    rendered = service.compile(prepared["program_ir"], tmp_path)
    rows = _instruction_rows(tmp_path / rendered["artifacts"]["program_csv"])
    assert [(r[2], r[3]) for r in rows if r[2] == "OUT"] == [
        ("OUT", f"M{600 + i}") for i in range(8)]
    assert sum(r[2] == "LD" and r[3] == "M0" for r in rows) == 2
    assert sum(r[2] == "MPS" for r in rows) == 2
    assert sum(r[2] == "MPP" for r in rows) == 2


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
    assert instruction_step_width("MOV", output["operands"], plc_model="FX3U").steps == 5
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


def _native_label_table(header, rows, *, title="Global1"):
    """Encode a caller-owned table; expected merge values are asserted below."""
    text = io.StringIO(newline="")
    writer = csv.writer(text, delimiter="\t", quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerows([[title], header, *rows])
    return text.getvalue().encode("utf-16")


def _decoded_native_label_table(raw):
    # Use the standard CSV reader independently of the driver's own parser.
    rows = list(csv.reader(io.StringIO(raw.decode("utf-16"), newline=""), delimiter="\t", strict=True))
    return rows[0], rows[1], [dict(zip(rows[1], row)) for row in rows[2:]]


@pytest.mark.parametrize("column", ["新版本字段", "Initial Value", "unknown_vendor_metadata"])
def test_visible_label_csv_rejects_unknown_columns_without_dropping_them(column):
    from gxworks2.engineering_driver import NativeEngineeringDriverError, _merge_label_csv

    header = ["类", "标签名", "数据类型", "常量", "注释", column]
    source = _native_label_table(header, [["VAR_GLOBAL", "Value", "INT", "", "before", "retain"]])
    with pytest.raises(NativeEngineeringDriverError, match="列尚未验证"):
        _merge_label_csv(source, [{"name": "Value", "class_code": 8, "data_type": "INT",
            "initial_value": "0", "comment": "after"}])


_LABEL_TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
                      max_size=100)
_LABEL_HEADER = ("类", "标签名", "数据类型", "常量", "软元件", "地址", "注释", "备注",
                 "系统标签的关联", "系统标签名", "属性")


@settings(max_examples=80, deadline=None)
@given(comment=_LABEL_TEXT, metadata=_LABEL_TEXT, header=st.permutations(_LABEL_HEADER))
@example(comment='中文\t"quoted"\r\n日本語\n😀', metadata='保留\t"原文"\r\n备注', header=_LABEL_HEADER)
def test_visible_label_csv_roundtrips_text_and_preserves_unedited_native_metadata(comment, metadata, header):
    from gxworks2.engineering_driver import _merge_label_csv

    first = {"类": "VAR_GLOBAL", "标签名": "Value", "数据类型": "INT", "常量": "",
             "软元件": "D100", "地址": "%MW0.100", "注释": "before", "备注": metadata,
             "系统标签的关联": "关联", "系统标签名": "SystemValue", "属性": "ReadWrite"}
    untouched = {"类": "VAR_GLOBAL", "标签名": "Unchanged", "数据类型": "BOOL", "常量": "",
                 "软元件": "M100", "地址": "%MX0.100", "注释": "unchanged comment",
                 "备注": "independent metadata", "系统标签的关联": "", "系统标签名": "", "属性": "Keep"}
    source = _native_label_table(header, [[row[column] for column in header] for row in (first, untouched)])
    plan = [{"name": "vALUE", "class_code": 8, "data_type": "DINT", "initial_value": "0",
             "device": "D102", "iec_address": "%MW0.102", "comment": comment},
            {"name": "Unchanged", "class_code": 8, "data_type": "BOOL", "initial_value": "FALSE",
             "device": "M100", "iec_address": "%MX0.100", "comment": "unchanged comment"}]
    before_plan = copy.deepcopy(plan)
    title, columns, rows = _decoded_native_label_table(_merge_label_csv(source, plan))
    assert title == ["Global1"] and columns == list(header)
    assert len(rows) == 2
    assert rows[0] == {**first, "标签名": "vALUE", "数据类型": "DINT", "软元件": "D102",
                       "地址": "%MW0.102", "注释": comment}
    assert rows[1] == untouched
    assert plan == before_plan


@pytest.mark.parametrize("class_code,class_name,data_type,initial,constant,device,iec", [
    (8, "VAR_GLOBAL", "BOOL", "FALSE", "", "M100", "%MX0.100"),
    (8, "VAR_GLOBAL", "INT", "0", "", "D100", "%MW0.100"),
    (2, "VAR_CONSTANT", "INT", "2", "2", "", ""),
    (9, "VAR_GLOBAL_CONSTANT", "STRING[8]", '"中文"', '"中文"', "", ""),
    (1, "VAR", "ScaleValue", "", "", "", ""),
])
def test_visible_label_csv_transports_core_classes_types_constants_and_declared_addresses(
        class_code, class_name, data_type, initial, constant, device, iec):
    from gxworks2.engineering_driver import _merge_label_csv

    header = ["类", "标签名", "数据类型", "常量", "注释", "软元件", "地址"]
    source = _native_label_table(header, [])
    row = {"name": "Value", "class_code": class_code, "data_type": data_type,
           "initial_value": initial, "csv_constant": constant, "device": device,
           "iec_address": iec, "comment": "transported by Core plan"}
    _, _, actual = _decoded_native_label_table(_merge_label_csv(source, [row]))
    assert actual == [{"类": class_name, "标签名": "Value", "数据类型": data_type,
        "常量": constant, "注释": "transported by Core plan", "软元件": device, "地址": iec}]


@pytest.mark.parametrize("change", ["device_without_column", "iec_without_column", "member_devices"])
def test_visible_label_csv_rejects_unrepresented_address_fields(change):
    from gxworks2.engineering_driver import NativeEngineeringDriverError, _merge_label_csv

    source = _native_label_table(["类", "标签名", "数据类型", "常量", "注释"], [])
    row = {"name": "Value", "class_code": 8, "data_type": "INT", "initial_value": "0", "comment": ""}
    row.update({"device": "D100"} if change == "device_without_column" else
               {"iec_address": "%MW0.100"} if change == "iec_without_column" else
               {"devices": [["D100", "%MW0.100"]]})
    with pytest.raises(NativeEngineeringDriverError, match="显式地址"):
        _merge_label_csv(source, [row])


@pytest.mark.parametrize("relative", ["grid-empty.png", "acceptance-02-run3/fbd-unbound.png"])
def test_visible_fbd_grid_matches_independently_observed_native_capture(relative):
    from PIL import Image
    from gxworks2.engineering_driver import _grid_calibration
    from tools.offline_inputs import private_path

    path = private_path("research/experiments/visible-session-20261017/" + relative)
    if not path.is_file():
        pytest.skip("private native FBD grid capture is unavailable")
    with Image.open(path) as capture:
        actual = _grid_calibration(capture)
        # Coordinates were measured in the native capture, not generated by
        # the calibration algorithm or its mouse-coordinate projection.
        assert (actual["pitch"], actual["x"], actual["y"]) == (24, 123, 4)
        assert (actual["width"], actual["height"]) == capture.size


@pytest.mark.parametrize("shape", ["blank", "horizontal_line", "horizontal_dots"])
def test_visible_fbd_grid_rejects_images_without_both_native_grid_axes(shape):
    from PIL import Image, ImageDraw
    from gxworks2.engineering_driver import NativeEngineeringDriverError, _grid_calibration

    pixels = Image.new("RGB", (800, 180), "white")
    draw = ImageDraw.Draw(pixels)
    if shape == "horizontal_line":
        draw.line((40, 28, 760, 28), fill="black", width=1)
    elif shape == "horizontal_dots":
        for x in range(147, 760, 24):
            draw.point((x, 28), fill="black")
    with pytest.raises(NativeEngineeringDriverError, match="网格"):
        _grid_calibration(pixels)


def _native_structure_csv_witness():
    from tools.offline_inputs import private_path

    directory = private_path('research/experiments/visible-session-20261017/structure-defaults/labels-001')
    if not (directory / 'before.csv').is_file() or not (directory / 'change.csv').is_file():
        pytest.skip('private native structure CSV capture is unavailable')
    return (directory / 'before.csv').read_bytes(), (directory / 'change.csv').read_bytes()


def test_visible_structure_csv_matches_native_four_column_default_member_table():
    from gxworks2.engineering_driver import NativeEngineeringDriverError, _merge_label_csv

    before, observed = _native_structure_csv_witness()
    planned = [dict(name='Factor', class_code=1, data_type='INT', initial_value='0', comment='Multiplier'),
               dict(name='Ready', class_code=1, data_type='BOOL', initial_value='FALSE', comment='Readiness')]
    actual = _merge_label_csv(before, planned, structure=True)
    title, header, members = _decoded_native_label_table(actual)
    assert title == ['Live'] and header == ['标签名', '数据类型', '常量', '注释']
    assert members == [dict(标签名='Factor', 数据类型='INT', 常量='', 注释='Multiplier'),
                       dict(标签名='Ready', 数据类型='BOOL', 常量='', 注释='Readiness')]
    assert _decoded_native_label_table(observed) == (title, header, members)
    with pytest.raises(NativeEngineeringDriverError, match='列尚未验证'):
        _merge_label_csv(before, planned)


@pytest.mark.parametrize('extra', ['类', '软元件', '备注'])
def test_visible_structure_csv_does_not_admit_unverified_extra_columns(extra):
    from gxworks2.engineering_driver import NativeEngineeringDriverError, _merge_label_csv

    source = _native_label_table(['标签名', '数据类型', '常量', '注释', extra], [], title='Structure')
    with pytest.raises(NativeEngineeringDriverError, match='列尚未验证'):
        _merge_label_csv(source, [], structure=True)


@pytest.mark.parametrize('constant', ['3', 'TRUE', '"text"'])
def test_visible_structure_csv_never_transmits_a_member_default_via_the_constant_column(constant):
    from gxworks2.engineering_driver import _merge_label_csv

    source = _native_label_table(['标签名', '数据类型', '常量', '注释'], [], title='Structure')
    # Core rejects unsupported nondefault structure initializers before the
    # driver. Even an unexpected csv_constant must not populate this column.
    row = dict(name='Factor', class_code=1, data_type='INT', initial_value='0',
               csv_constant=constant, comment='Default value stays zero')
    _, header, members = _decoded_native_label_table(_merge_label_csv(source, [row], structure=True))
    assert '类' not in header
    assert members == [dict(标签名='Factor', 数据类型='INT', 常量='', 注释='Default value stays zero')]


def _active_label_identity():
    return dict(process_id=42, window_handle=100, process_started='1234567', executable='C:/GX/GD2.exe',
                software_version='1.635.0.1', project_path='C:/Lab/Current.gxw', cpu_menu='FX3U/FX3UC')


def _read_active_csv(raw, *, owner='Global1', scope='global', type_sources=None):
    from gxw.active_context import read_active_labels_csv
    title = {'global': f'全局标签设置 {owner}', 'program': f'局部标签设置 {owner} [PRG]',
             'fb': f'函数/FB标签设置 {owner} [FB]', 'structure': f'结构体设置 {owner}'}[scope]
    return read_active_labels_csv(raw, owner=owner, scope=scope, identity=_active_label_identity(),
        document_handle=200, document_title=title, type_sources=type_sources)


def test_active_label_csv_reads_frozen_native_types_explicit_addresses_and_unassigned_result():
    import zipfile
    from tools.offline_inputs import private_path
    path = private_path('research/' + 'evidence/gxworks2-visible-engineering-session-20261017.zip')
    if not path.is_file():
        pytest.skip('frozen native label export is unavailable')
    with zipfile.ZipFile(path) as archive:
        raw = archive.read('experiments/application-05/native-run/evidence/labels-002/before.csv')
    observed = _read_active_csv(raw)
    rows = {row['name']: row for row in observed['declarations']}
    assert observed['status'] == 'available' and observed['raw_bytes'] == raw
    assert observed['csv_title'] == 'Acceptance06'  # Export title is the project, not table owner.
    assert (rows['Enable']['data_type'], rows['Enable']['device'], rows['Enable']['iec_address']) == (
        'BOOL', 'M100', '%MX0.100')
    assert (rows['InputValue']['data_type'], rows['InputValue']['device'], rows['InputValue']['iec_address']) == (
        'INT', 'D100', '%MW0.100')
    assert rows['Result']['device'] == rows['Result']['iec_address'] == ''
    assert all(row['initial_value'] is None and row['initial_value_status'] == 'unavailable_in_csv'
               for row in rows.values())
    assert not observed['save_performed'] and observed['compilation'] == 'not_run'


@pytest.mark.parametrize('scope,class_name', [('global', 'VAR_GLOBAL'), ('program', 'VAR'), ('fb', 'VAR_INPUT')])
def test_active_label_csv_binds_global_and_program_fb_local_owners(scope, class_name):
    source = _native_label_table(['类', '标签名', '数据类型', '常量', '注释'],
                                 [[class_name, 'Value', 'BOOL', '', 'current']])
    observed = _read_active_csv(source, owner='CurrentOwner', scope=scope)
    assert observed['owner'] == 'CurrentOwner' and observed['owner_kind'] == scope
    assert observed['scope'] == ('global' if scope == 'global' else 'local')
    assert observed['declarations'][0]['type_status'] == 'resolved'
    assert observed['declarations'][0]['device'] is None  # Missing column stays distinct from empty cell.
    assert observed['source_binding']['identity'] == _active_label_identity()


def test_active_label_csv_reuses_named_interface_directions_without_inventing_port_numbers():
    header = ['类', '标签名', '数据类型', '常量', '注释']
    source = _native_label_table(header, [
        ['VAR_INPUT', 'Enabled', 'BOOL', '', ''], ['VAR_INPUT', 'Source', 'UnboundType', '', ''],
        ['VAR_OUTPUT', 'Result', 'INT', '', ''], ['VAR_IN_OUT', 'State', 'WORD', '', ''],
        ['VAR CONSTANT', 'Limit', 'INT', '20', '']])
    observed = _read_active_csv(source, owner='Scale', scope='fb')
    assert observed['status'] == observed['interface_status'] == 'partial'
    named = observed['interface']
    assert named['graphic_port_order'] == 'unverified'
    assert [(row['name'], row['direction']) for row in named['inputs']] == [
        ('Enabled', 'input'), ('Source', 'input'), ('State', 'in_out')]
    assert [(row['name'], row['direction']) for row in named['outputs']] == [('Result', 'output'), ('State', 'in_out')]
    assert named['inputs'][1]['type_code'] is None and named['inputs'][1]['type_status'] == 'unresolved'
    assert all('port_index' not in row and row['source_offset'] is None for row in named['inputs'] + named['outputs'])
    constant = next(row for row in observed['declarations'] if row['name'] == 'Limit')
    assert (constant['class_name'], constant['initial_value'], constant['initial_value_status']) == (
        'VAR_CONSTANT', '20', 'observed_constant')


@pytest.mark.parametrize('data_type', ['Bit', 'MissingType', 'ARRAY [3..1] OF INT', 'STRING[256]', ''])
def test_active_label_csv_retains_unknown_or_unsupported_types_as_partial(data_type):
    source = _native_label_table(['类', '标签名', '数据类型', '常量', '注释', '厂商扩展'],
                                 [['VAR_GLOBAL', 'Value', data_type, '', 'retained', 'opaque']])
    observed = _read_active_csv(source)
    row, = observed['declarations']
    assert observed['status'] == 'partial' and row['type_status'] == 'unresolved'
    assert row['data_type'] == data_type and row['raw_fields']['厂商扩展'] == 'opaque'
    assert {issue['code'] for issue in observed['issues']} == {'unresolved_type', 'unknown_column'}
    assert observed['raw_bytes'] == source


@pytest.mark.parametrize('data_type,base,bounds', [
    (' INT ', 'INT', []), ('STRING [ 8 ]', 'STRING', []),
    ('ARRAY [ -2 .. 3 ] OF BOOL', 'BOOL', [(-2, 3)]),
    ('ARRAY[1..2, 3..4] OF INT', 'INT', [(1, 2), (3, 4)]),
])
def test_active_label_csv_reuses_core_type_parts_without_changing_native_type_text(data_type, base, bounds):
    source = _native_label_table(['类', '标签名', '数据类型', '常量', '注释'],
                                 [['VAR_GLOBAL', 'Value', data_type, '', '']])
    observed = _read_active_csv(source)
    row, = observed['declarations']
    assert observed['status'] == 'available' and row['data_type'] == data_type
    assert row['type']['base_type'] == base and row['type']['bounds'] == bounds


@pytest.mark.parametrize('class_name,scope', [('UNKNOWN_CLASS', 'global'), ('VAR_INPUT', 'program'), ('VAR_GLOBAL', 'fb')])
def test_active_label_csv_retains_unknown_and_foreign_scope_classes(class_name, scope):
    source = _native_label_table(['类', '标签名', '数据类型', '常量', '注释'],
                                 [[class_name, 'Value', 'INT', '', '']])
    observed = _read_active_csv(source, scope=scope)
    row, = observed['declarations']
    assert observed['status'] == 'partial' and row['class_status'] == 'unresolved'
    assert row['raw_fields']['类'] == class_name


@pytest.mark.parametrize('owner_kind,type_name', [('fb', 'ScaleValue'), ('structure', 'ScaleConfig')])
def test_active_label_csv_requires_same_current_source_for_compound_types(owner_kind, type_name):
    from gxw.models import GXWFormatError
    header = ['标签名', '数据类型', '常量', '注释']
    definition = _read_active_csv(_native_label_table(header if owner_kind == 'structure' else ['类', *header], []),
                                  owner=type_name, scope=owner_kind)
    raw = _native_label_table(['类', *header], [['VAR', 'Value', type_name, '', '']])
    missing = _read_active_csv(raw, owner='Main', scope='program')
    assert missing['declarations'][0]['type_status'] == 'unresolved'
    bound = _read_active_csv(raw, owner='Main', scope='program', type_sources=[definition])
    row, = bound['declarations']
    assert bound['status'] == 'available' and row['type_status'] == 'source_bound'
    assert row['kind'] == ('function_block' if owner_kind == 'fb' else 'structure')
    assert row['type']['source_binding'] == definition['source_binding']
    foreign = copy.deepcopy(definition)
    foreign['source_binding']['identity']['project_path'] = 'C:/Lab/Another.gxw'
    with pytest.raises(GXWFormatError, match='同一当前原生工程'):
        _read_active_csv(raw, owner='Main', scope='program', type_sources=[foreign])
    assert _read_active_csv(raw, owner='Main', scope='program', type_sources=[definition, definition])['status'] == 'partial'


@pytest.mark.parametrize('damage', ['bom', 'unicode', 'quote', 'header', 'empty_header', 'columns', 'missing_column', 'name', 'duplicate'])
def test_active_label_csv_rejects_incomplete_or_ambiguous_native_tables(damage):
    from gxw.models import GXWFormatError
    header, rows = ['类', '标签名', '数据类型', '常量', '注释'], [['VAR_GLOBAL', 'Value', 'BOOL', '', '']]
    if damage == 'header': header[-1] = '标签名'
    elif damage == 'empty_header': header[-1] = ''
    elif damage == 'columns': rows[0].append('extra')
    elif damage == 'missing_column': header[-1] = 'UnknownComment'
    elif damage == 'name': rows[0][1] = ''
    elif damage == 'duplicate': rows.append(['VAR_GLOBAL', 'vALUE', 'INT', '', ''])
    raw = _native_label_table(header, rows)
    if damage == 'bom': raw = raw[2:]
    elif damage == 'unicode': raw = b'\xff\xfe\x00'
    elif damage == 'quote': raw = ('"Project"\n"类"\t"unterminated').encode('utf-16')
    with pytest.raises(GXWFormatError):
        _read_active_csv(raw)


@settings(max_examples=35, deadline=None)
@given(comment=_LABEL_TEXT, metadata=_LABEL_TEXT, header=st.permutations(_LABEL_HEADER))
def test_active_label_csv_preserves_every_native_field_through_column_order_and_unicode(comment, metadata, header):
    original = {'类': 'VAR_GLOBAL_CONSTANT', '标签名': 'Value', '数据类型': 'STRING[8]', '常量': '"文"',
                '软元件': 'D100', '地址': '%MW0.100', '注释': comment, '备注': metadata,
                '系统标签的关联': '关联', '系统标签名': 'SystemValue', '属性': 'ReadWrite'}
    raw = _native_label_table(header, [[original[column] for column in header]])
    observed = _read_active_csv(raw)
    row, = observed['declarations']
    assert observed['csv_columns'] == list(header) and observed['raw_bytes'] == raw
    assert row['raw_fields'] == original and row['comment'] == comment
    assert row['initial_value'] == original['常量'] and row['type_status'] == 'resolved'


_SINGLE_IMPORT_HEADERS = {
    'zh-CN': list(_LABEL_HEADER),
    'en': ['Class', 'Label Name', 'Data Type', 'Constant', 'Device', 'Address', 'Comment', 'Remark',
           'Relation with System Label', 'System Label Name', 'Attribute'],
    'ja': ['クラス', 'ラベル名', 'データ型', '定数値', 'デバイス', 'アドレス', 'コメント', '備考',
           'システムラベルの関連', 'システムラベル名', '属性'],
}


def _single_label_import_driver(tmp_path, monkeypatch, language, source, *, evidence=True):
    from gxworks2 import engineering_labels as owner
    from itertools import count
    from types import SimpleNamespace
    from gxworks2 import engineering_driver as native

    class ImportOnlyUI:
        READ_CSV_COMMAND = 102

        @property
        def WRITE_CSV_COMMAND(self):
            pytest.fail('A label import must not request the export command')

    events = []
    driver = object.__new__(native.NativeEngineeringDriver)
    driver.native_ui_language = language
    driver.ui = ImportOnlyUI()
    driver._transfers = 0
    driver.evidence_directory = tmp_path / 'evidence' if evidence else None
    transfer_directory = tmp_path / ('evidence/labels-001' if evidence else 'temporary-import')
    if not evidence:
        from contextlib import contextmanager
        @contextmanager
        def temporary_directory(*, prefix):
            events.append(('temporary_directory', prefix))
            try:
                yield str(transfer_directory)
            finally:
                (transfer_directory / 'change.csv').unlink(missing_ok=True)
                transfer_directory.rmdir()
        monkeypatch.setattr(owner.tempfile, 'TemporaryDirectory', temporary_directory)
    observed = dict(raw_bytes=source, origin='active_native_labels', read_transport='native_label_table')
    def read_labels(name, scope):
        events.append(('read_labels', name, scope))
        assert not transfer_directory.exists()
        return copy.deepcopy(observed)
    driver.read_labels = read_labels
    driver._export_label_csv = lambda *args, **kwargs: pytest.fail('Label import must not export the current table')
    driver.snapshot = lambda *args, **kwargs: pytest.fail('Label import must not save a project')
    def command(value):
        assert value == 102
        events.append(('command', value))
    driver._command = command
    driver._dialog = lambda title: title
    def file_name(dialog, path):
        assert path.is_file()
        observed['imported_bytes'] = path.read_bytes()
        events.append(('file_name', dialog, str(path)))
    driver._file_name = file_name
    driver._confirm = lambda message, button: events.append(('confirm', message, button))
    driver._dialogs = lambda: []
    driver._window = lambda: SimpleNamespace(is_enabled=lambda: True)
    driver._labels_page = lambda name, scope: events.append(('labels_page', name, scope))
    driver._visible = lambda page, **details: dict(completed=True, page=page, **details)
    ticks = count()
    monkeypatch.setattr(owner, 'time', SimpleNamespace(monotonic=lambda: next(ticks) / 4))
    def wait(predicate, message):
        events.append(('wait',))
        for _ in range(8):
            result = predicate()
            if result:
                return result
        pytest.fail(message)
    driver._wait = wait
    return driver, events, observed, transfer_directory


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
@pytest.mark.parametrize('scope,owner,class_code,class_name', [
    ('global', 'Globals', 8, 'VAR_GLOBAL'), ('program', 'Main', 1, 'VAR'),
    ('fb', 'Logic', 1, 'VAR'), ('structure', 'Config', 1, None),
])
@pytest.mark.parametrize('evidence', [False, True])
def test_native_label_import_reads_current_table_once_and_only_transfers_one_import(
        tmp_path, monkeypatch, language, scope, owner, class_code, class_name, evidence):
    columns = _SINGLE_IMPORT_HEADERS[language]
    indices = [1, 2, 3, 6] if scope == 'structure' else list(range(11))
    columns = [columns[index] for index in indices]
    first = [class_name, 'Factor', 'INT', '', 'D100', '%MW0.100', 'Before comment',
             'Retained remark\t"quoted"\r\nnext line', 'Current system relation', 'SystemFactor', 'ReadWrite']
    second = [class_name, 'Ready', 'BOOL', '', 'M100', '%MX0.100', 'Unchanged comment',
              'Independent remark', '', '', 'Keep']
    records = [[row[index] for index in indices] for row in (first, second)]
    source = _native_label_table(columns, records, title='Controlled current project')
    driver, events, observed, transfer_directory = _single_label_import_driver(
        tmp_path, monkeypatch, language, source, evidence=evidence)
    rows = [dict(name='Factor', class_code=class_code, data_type='DINT', initial_value='0', csv_constant='',
                 device='' if scope == 'structure' else 'D100', iec_address='' if scope == 'structure' else '%MW0.100',
                 comment='Updated comment\t"quoted"\r\nnext line'),
            dict(name='Ready', class_code=class_code, data_type='BOOL', initial_value='FALSE', csv_constant='',
                 device='' if scope == 'structure' else 'M100', iec_address='' if scope == 'structure' else '%MX0.100',
                 comment='Unchanged comment')]
    original_rows = copy.deepcopy(rows)
    result = driver._csv_labels(owner, scope, rows)
    change = transfer_directory / 'change.csv'
    assert list(tmp_path.rglob('*.csv')) == ([change] if evidence else [])
    assert not list(tmp_path.rglob('before.csv'))
    assert driver._transfers == 1 and rows == original_rows and observed['raw_bytes'] == source
    assert [event for event in events if event[0] == 'read_labels'] == [('read_labels', owner, scope)]
    assert [event for event in events if event[0] == 'command'] == [('command', 102)]
    assert [event for event in events if event[0] == 'file_name'] == [('file_name', '从CSV文件读取', str(change))]
    assert [event for event in events if event[0] == 'confirm'] == [('confirm', '读取指定的文件内容。', 6)]
    assert [event for event in events if event[0] == 'wait'] == [('wait',)]
    assert events[0] == ('read_labels', owner, scope) and events[-1] == ('labels_page', owner, scope)
    assert len([event for event in events if event[0] == 'temporary_directory']) == (0 if evidence else 1)
    page = '全局标签表' if scope == 'global' else owner + (' 结构体成员表' if scope == 'structure' else ' 局部标签表')
    assert result == dict(completed=True, page=page, transfer_file=str(change))
    title, actual_columns, actual = _decoded_native_label_table(observed['imported_bytes'])
    assert title == ['Controlled current project'] and actual_columns == columns
    expected_first = [class_name, 'Factor', 'DINT', '', 'D100', '%MW0.100',
                      'Updated comment\t"quoted"\r\nnext line',
                      'Retained remark\t"quoted"\r\nnext line', 'Current system relation', 'SystemFactor', 'ReadWrite']
    expected_second = [class_name, 'Ready', 'BOOL', '', 'M100', '%MX0.100', 'Unchanged comment',
                       'Independent remark', '', '', 'Keep']
    assert actual == [dict(zip(columns, [row[index] for index in indices]))
                      for row in (expected_first, expected_second)]


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
@pytest.mark.parametrize('scope', ['global', 'program', 'fb', 'structure'])
def test_native_label_import_rejects_unknown_observed_columns_before_creating_or_dispatching_import(
        tmp_path, monkeypatch, language, scope):
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    indices = [1, 2, 3, 6] if scope == 'structure' else list(range(11))
    columns = [_SINGLE_IMPORT_HEADERS[language][index] for index in indices] + ['VendorExtension']
    source = _native_label_table(columns, [], title='Controlled current project')
    driver, events, observed, transfer_directory = _single_label_import_driver(tmp_path, monkeypatch, language, source)
    with pytest.raises(NativeEngineeringDriverError, match='列尚未验证'):
        driver._csv_labels('Owner', scope, [])
    assert events == [('read_labels', 'Owner', scope)]
    assert observed['raw_bytes'] == source and driver._transfers == 0
    assert not transfer_directory.exists() and list(tmp_path.iterdir()) == []
