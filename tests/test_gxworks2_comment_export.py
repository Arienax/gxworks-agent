"""CSV comments preserve project declarations independently of contact labels."""
import copy
import csv

import pytest

from rendering.ladder import generate_gx_works2_csv
from plc.ir import build_plc_ir


def _ladder():
    return {
        "device_comments": {"M0": "系统运行"},
        "rungs": [{
            "rung_id": 1,
            "header_element": None,
            "shared_inputs": [],
            "branches": [{
                "branch_id": 1, "y_offset_level": 0,
                "inputs": [{"type": "NC", "address": "M0", "label": "未运行"}],
                "outputs": [{"type": "COIL", "address": "Y2", "label": "连续运行"}],
            }],
        }],
    }


def _export(source, tmp_path, *, infer=True):
    program, comments = tmp_path / "MAIN.csv", tmp_path / "COMMENT.csv"
    assert generate_gx_works2_csv(source, program, comments, infer_device_comments=infer)
    with comments.open(encoding="utf-16", newline="") as stream:
        rows = list(csv.reader(stream, delimiter="\t"))
    assert rows[:2] == [["COMMENT - 副本"], ["软元件名", "注释"]]
    return program.read_bytes(), rows[2:]


@pytest.mark.parametrize("as_ir", [False, True])
def test_explicit_comment_wins_without_mutating_source_or_program_csv(tmp_path, as_ir):
    source = build_plc_ir(_ladder()) if as_ir else _ladder()
    before = copy.deepcopy(source)
    program, comments = _export(source, tmp_path)
    assert comments == [["M0", "系统运行"], ["Y002", "连续运行"]]
    assert source == before
    # The instruction file is the pre-fix byte contract, including its output
    # annotation. Comment inference must never rewrite ladder labels or logic.
    expected = (
        '"MAIN - 副本"\r\n'
        '"PLC信息:"\t"三菱 GX Works2 兼容"\r\n'
        '"步号"\t"行间声明"\t"指令"\t"I/O(软元件)"\t"空白栏"\t"PI声明"\t"注解"\r\n'
        '"0"\t""\t"LDI"\t"M0"\t""\t""\t""\r\n'
        '"1"\t""\t"OUT"\t"Y002"\t""\t""\t""\r\n'
        '""\t""\t""\t""\t""\t""\t"连续运行"\r\n'
        '"2"\t""\t"END"\t""\t""\t""\t""\r\n'
    ).encode("utf-16")
    assert program == expected


@pytest.mark.parametrize("location", ["header", "comparison_header", "shared", "input", "parallel", "output"])
def test_labels_fill_only_missing_comments_in_each_supported_location(tmp_path, location):
    source = _ladder()
    source["device_comments"] = {}
    rung = source["rungs"][0]
    branch = rung["branches"][0]
    branch["inputs"] = []
    branch["outputs"][0]["label"] = ""
    element = {"type": "NO", "address": "M1", "label": "补充用途"}
    address = "M1"
    if location == "header":
        rung["header_element"] = element
    elif location == "comparison_header":
        rung["header_element"] = {"type": "COMPARE", "expression": "= D0 K1", "label": "补充用途"}
        address = "D0"
    elif location == "shared":
        rung["shared_inputs"] = [element]
    elif location == "input":
        branch["inputs"] = [element]
    elif location == "parallel":
        branch["inputs"] = [{"type": "parallel_block", "branches": [[element]]}]
    else:
        branch["outputs"] = [{**element, "type": "COIL"}]
    before = copy.deepcopy(source)
    _, comments = _export(source, tmp_path)
    assert comments == [[address, "补充用途"]]
    assert source == before


@pytest.mark.parametrize("as_ir", [False, True])
def test_inference_can_be_disabled_without_mutating_source(tmp_path, as_ir):
    source = build_plc_ir(_ladder()) if as_ir else _ladder()
    before = copy.deepcopy(source)
    _, comments = _export(source, tmp_path, infer=False)
    assert comments == [["M0", "系统运行"]]
    assert source == before


@pytest.mark.parametrize("infer", [False, True])
def test_explicit_aliases_identify_one_device_and_keep_first_declaration(tmp_path, infer):
    source = _ladder()
    source["device_comments"] = {" x001 ": "启动按钮", "X1": "别名重复声明"}
    branch = source["rungs"][0]["branches"][0]
    branch["inputs"] = [{"type": "NO", "address": "x1", "label": "局部启动条件"}]
    branch["outputs"][0]["label"] = ""
    before = copy.deepcopy(source)
    _, comments = _export(source, tmp_path, infer=infer)
    assert comments == [["X001", "启动按钮"]]
    assert source == before


def test_list_ladder_inferred_aliases_do_not_overwrite_first_label(tmp_path):
    source = _ladder()["rungs"]
    branch = source[0]["branches"][0]
    branch["inputs"] = [
        {"type": "NO", "address": "x1", "label": "启动按钮"},
        {"type": "NC", "address": "X001", "label": "未启动"},
    ]
    branch["outputs"][0]["label"] = ""
    before = copy.deepcopy(source)
    _, comments = _export(source, tmp_path)
    assert comments == [["X001", "启动按钮"]]
    assert source == before


@pytest.mark.parametrize("explicit", ["", "null"])
def test_explicit_empty_comment_is_not_replaced_by_a_label(tmp_path, explicit):
    source = _ladder()
    source["device_comments"] = {"m000": explicit}
    source["rungs"][0]["branches"][0]["outputs"][0]["label"] = ""
    before = copy.deepcopy(source)
    _, comments = _export(source, tmp_path)
    assert comments == []
    assert source == before
