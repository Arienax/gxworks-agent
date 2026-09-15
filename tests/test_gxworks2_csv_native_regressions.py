import csv
import json
import xml.etree.ElementTree as ET

from draw import AdvancedSVGLadder, generate_gx_works2_csv


def _read_program_rows(path):
    with path.open("r", encoding="utf-16", newline="") as handle:
        return list(csv.reader(handle, delimiter="\t"))


def _instruction_rows(path):
    return [
        row
        for row in _read_program_rows(path)[3:]
        if len(row) >= 4 and str(row[2] or "").strip()
    ]


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


def _range_network(output, low, high, pulse_low, pulse_high):
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
        "rung_id": 1,
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


def test_16_way_orb_range_network_is_preserved_for_m30_m31_m32(tmp_path):
    cases = [
        ("M30", "K1", "K3", "D101", "D102"),
        ("M31", "K4", "K6", "D103", "D104"),
        ("M32", "K7", "K9", "D105", "D106"),
    ]
    for case_index, case in enumerate(cases):
        program = tmp_path / f"program-{case_index}.csv"
        comments = tmp_path / f"comments-{case_index}.csv"
        rung = _range_network(*case)
        assert generate_gx_works2_csv(
            {"device_comments": {}, "rungs": [rung]},
            program,
            comments,
        )
        instructions = _instruction_rows(program)
        ops = [row[2] for row in instructions]

        assert instructions[0][2:4] == ["LD", "M100"]
        assert ops.count("ORB") == 15
        assert instructions[-2][2:4] == ["OUT", case[0]]
        assert instructions[-2][0] == "351"
