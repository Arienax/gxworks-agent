import copy
import csv
import json
import xml.etree.ElementTree as ET

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
