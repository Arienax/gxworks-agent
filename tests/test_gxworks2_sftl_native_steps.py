import csv

from draw import generate_gx_works2_csv


def _instruction_rows(path):
    with path.open("r", encoding="utf-16", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    return [
        row
        for row in rows[3:]
        if len(row) >= 4 and str(row[2] or "").strip()
    ]


def test_fx3u_sftl_uses_nine_native_program_steps(tmp_path):
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
                        "inputs": [
                            {"type": "NO", "address": "X0", "label": ""}
                        ],
                        "outputs": [
                            {
                                "type": "APP_INSTR",
                                "opcode": "SFTL",
                                "operands": ["M10", "M100", "K128", "K1"],
                                "label": "",
                            }
                        ],
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
                        "inputs": [
                            {"type": "NO", "address": "X1", "label": ""}
                        ],
                        "outputs": [
                            {"type": "COIL", "address": "Y0", "label": ""}
                        ],
                    }
                ],
            },
        ],
    }

    assert generate_gx_works2_csv(ladder, program, comments)
    instructions = [
        (row[0], row[2], row[3]) for row in _instruction_rows(program)
    ]

    assert instructions[:4] == [
        ("0", "LD", "X000"),
        ("1", "SFTL", "M10 M100 K128 K1"),
        ("10", "LD", "X001"),
        ("11", "OUT", "Y000"),
    ]
