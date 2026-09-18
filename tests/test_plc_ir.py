import copy
import csv
import hashlib
import json
from pathlib import Path

import pytest

from rendering.ladder_svg import AdvancedSVGLadder
from gxworks2.csv_export import generate_gx_works2_csv
from plc.ir import (
    PLCIRValidationError,
    apply_ladder_partial_to_ir,
    apply_network_patch,
    build_plc_ir,
    canonical_sha256,
    ir_to_ladder,
    validate_plc_ir,
)
from plc.st_renderer import (
    STTranslationError,
    render_plc_ir_to_st,
    validate_st_traceability,
)


def input_element(kind, address, label=""):
    return {"type": kind, "address": address, "label": label}


def instruction(opcode, operands, label=""):
    return {
        "type": "APP_INSTR",
        "opcode": opcode,
        "operands": operands,
        "label": label,
    }


def rung(rung_id, *, inputs=None, outputs=None, note=""):
    return {
        "rung_id": rung_id,
        "debug_note": note,
        "header_element": None,
        "shared_inputs": [],
        "branches": [
            {
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": inputs or [],
                "outputs": outputs or [],
            }
        ],
    }


def sample_ladder():
    return {
        "device_comments": {
            "X0": "启动",
            "X1": "停止",
            "M0": "运行保持",
            "D100": "HMI 累计值",
        },
        "rungs": [
            rung(
                10,
                inputs=[input_element("P", "X0", "启动沿")],
                outputs=[instruction("SET", ["M0"], "置位运行")],
                note="启动保持",
            ),
            rung(
                20,
                inputs=[input_element("NO", "M0"), input_element("NC", "X1")],
                outputs=[instruction("INC", ["D100"], "供 HMI 显示")],
                note="运行累计",
            ),
        ],
    }


def read_tab_rows(path):
    with open(path, encoding="utf-16", newline="") as handle:
        return list(csv.reader(handle, delimiter="\t"))


def test_ladder_builds_project_ir_with_deterministic_access_and_timing():
    ladder = sample_ladder()
    confirmed = {
        "io_table": [
            {"kind": "X", "address": "X0", "label": "启动按钮", "source": "user"},
            {"kind": "Y", "address": "Y0", "label": "电机", "source": "user"},
        ]
    }

    program = build_plc_ir(
        ladder,
        plc_model="FX3U",
        program_name="MAIN",
        revision=18,
        confirmed_spec=confirmed,
    )

    assert program["kind"] == "plc_program_ir"
    assert program["plc"] == {"series": "FX", "cpu": "FX3U"}
    assert program["program_name"] == "MAIN"
    assert program["revision"] == 18
    assert [item["id"] for item in program["networks"]] == ["N0010", "N0020"]
    assert program["networks"][0]["reads"] == ["X0"]
    assert program["networks"][0]["writes"] == ["M0"]
    assert program["networks"][1]["reads"] == ["X1", "M0", "D100"]
    assert program["networks"][1]["writes"] == ["D100"]
    assert program["timing"]["edge_triggers"] == [
        {"network": "N0010", "device": "X0", "edge": "rising"}
    ]
    assert program["devices"]["D100"]["access"] == "read_write"
    assert program["devices"]["D100"]["read_by"] == ["N0020"]
    assert program["devices"]["D100"]["written_by"] == ["N0020"]
    assert program["io_map"]["Y0"]["label"] == "电机"
    assert ir_to_ladder(program) == ladder
    assert validate_plc_ir(program) is program


def test_network_patch_is_revision_and_hash_guarded_and_does_not_touch_neighbors():
    program = build_plc_ir(sample_ladder(), revision=18)
    untouched = copy.deepcopy(program["networks"][1])
    replacement = copy.deepcopy(program["networks"][0]["ladder"])
    replacement["branches"][0]["inputs"].append(
        input_element("NC", "X3", "急停")
    )

    updated = apply_network_patch(
        program,
        {
            "base_revision": 18,
            "base_ir_sha256": canonical_sha256(program),
            "operations": [
                {
                    "operation": "modify_network",
                    "network": "N0010",
                    "ladder": replacement,
                }
            ],
        },
    )

    assert updated["revision"] == 19
    assert updated["networks"][0]["reads"] == ["X0", "X3"]
    assert updated["networks"][1]["ladder"] == untouched["ladder"]
    assert updated["networks"][1]["instructions"] == untouched["instructions"]
    assert validate_plc_ir(updated) is updated

    with pytest.raises(PLCIRValidationError, match="stale patch revision"):
        apply_network_patch(
            program,
            {
                "base_revision": 17,
                "operations": [
                    {
                        "operation": "modify_network",
                        "network": "N0010",
                        "ladder": replacement,
                    }
                ],
            },
        )

    with pytest.raises(PLCIRValidationError, match="base_ir_sha256"):
        apply_network_patch(
            program,
            {
                "base_ir_sha256": "0" * 64,
                "operations": [
                    {
                        "operation": "delete_network",
                        "network": "N0010",
                    }
                ],
            },
        )


def test_existing_partial_ladder_response_can_patch_ir_without_full_regeneration():
    program = build_plc_ir(sample_ladder(), revision=5)
    replacement = copy.deepcopy(program["networks"][1]["ladder"])
    replacement["branches"][0]["outputs"] = [
        instruction("DINC", ["D100"], "双字累计")
    ]

    updated = apply_ladder_partial_to_ir(
        program,
        {
            "mode": "partial",
            "device_comments": {"D100": "HMI 双字累计值"},
            "rungs": [replacement],
            "delete_rung_ids": [],
        },
    )

    assert updated["revision"] == 6
    assert updated["networks"][0]["ladder"] == program["networks"][0]["ladder"]
    assert updated["networks"][1]["instructions"][-1]["op"] == "DINC"
    assert updated["devices"]["D100"]["comment"] == "HMI 双字累计值"


def test_ir_rejects_stale_derived_access_sets():
    program = build_plc_ir(sample_ladder())
    tampered = copy.deepcopy(program)
    tampered["networks"][0]["reads"] = []

    with pytest.raises(PLCIRValidationError, match="reads are stale"):
        validate_plc_ir(tampered, validate_ladder=False)


def test_ir_is_a_compatible_source_for_csv_and_svg(tmp_path):
    ladder = sample_ladder()
    program = build_plc_ir(ladder)
    legacy_program = tmp_path / "legacy_program.csv"
    legacy_comments = tmp_path / "legacy_comments.csv"
    ir_program = tmp_path / "ir_program.csv"
    ir_comments = tmp_path / "ir_comments.csv"

    assert generate_gx_works2_csv(ladder, legacy_program, legacy_comments)
    assert generate_gx_works2_csv(program, ir_program, ir_comments)
    assert read_tab_rows(ir_program) == read_tab_rows(legacy_program)
    assert read_tab_rows(ir_comments) == read_tab_rows(legacy_comments)

    drawer = AdvancedSVGLadder()
    legacy_svg = drawer.generate_ladder(json.dumps(ladder, ensure_ascii=False))
    ir_svg = AdvancedSVGLadder().generate_ladder(
        json.dumps(program, ensure_ascii=False)
    )
    assert ir_svg == legacy_svg


def test_gxworks2_interline_statements_stay_within_64_legacy_bytes(tmp_path):
    ladder = sample_ladder()
    ladder["rungs"][0]["debug_note"] = (
        "停止状态且未满料时启动进入运行；NC X1为停止常闭触点，未按下时导通"
    )
    program_path = tmp_path / "program.csv"
    comments_path = tmp_path / "comments.csv"

    assert generate_gx_works2_csv(ladder, program_path, comments_path)
    rows = read_tab_rows(program_path)
    statements = [row[1] for row in rows[3:] if len(row) > 1 and row[1]]

    assert statements
    assert all(len(value.encode("gb18030")) <= 64 for value in statements)


def test_ir_renders_traceable_gxworks2_st_with_scan_conditions():
    program = build_plc_ir(sample_ladder(), revision=18)

    st_text = render_plc_ir_to_st(program)

    assert "(* NETWORK N0010 - 启动保持 *)" in st_text
    assert "SET(LDP(TRUE, X0), M0);" in st_text
    assert "(* NETWORK N0020 - 运行累计 *)" in st_text
    assert "INC((M0) AND (NOT X1), D100);" in st_text
    assert validate_st_traceability(program, st_text)


def test_ir_st_renderer_handles_parallel_compare_timer_counter_and_coil():
    ladder = {
        "device_comments": {},
        "rungs": [
            {
                "rung_id": 1,
                "debug_note": "复合逻辑",
                "header_element": {
                    "type": "BLOCK_INPUT",
                    "expression": "= D0 K10",
                    "label": "状态十",
                },
                "shared_inputs": [
                    {
                        "type": "parallel_block",
                        "branches": [
                            [input_element("NO", "X0")],
                            [input_element("NC", "X1")],
                        ],
                    }
                ],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [],
                        "outputs": [
                            {"type": "TIMER", "address": "T0", "value": "K50"},
                            {"type": "COUNTER", "address": "C200", "value": "K3"},
                            {"type": "COIL", "address": "Y0"},
                        ],
                    }
                ],
            }
        ],
    }
    program = build_plc_ir(ladder)

    st_text = render_plc_ir_to_st(program)

    condition = "((D0 = 10)) AND ((X0) OR (NOT X1))"
    assert f"OUT_T({condition}, TC0, 50);" in st_text
    assert f"OUT_C_32({condition}, CC200, 3);" in st_text
    assert f"OUT({condition}, Y0);" in st_text


def test_ir_st_renderer_fails_closed_on_unrepresentable_free_form_operand():
    ladder = sample_ladder()
    ladder["rungs"][1]["branches"][0]["outputs"] = [
        instruction("MOV", ["D0 + K1", "D100"], "invalid expression")
    ]
    # Skip the public ladder validator here so the test reaches the renderer's
    # own fail-closed boundary for corrupted/imported legacy IR.
    program = build_plc_ir(ladder)

    with pytest.raises(STTranslationError, match="unsupported ST operand"):
        render_plc_ir_to_st(program)


def test_compiler_thread_persists_ir_and_all_legacy_artifacts(monkeypatch, tmp_path):
    import application.model_api as api
    import ui.desktop.main_window as main_module
    from ui.desktop.workers import CompilerThread

    ladder = sample_ladder()
    monkeypatch.setattr(
        api,
        "stream_model_response",
        lambda *args, **kwargs: (
            "",
            json.dumps(ladder, ensure_ascii=False),
        ),
    )
    succeeded = []
    failed = []
    progress = []
    worker = CompilerThread(
        "task-ir",
        "生成测试程序",
        "high",
        "ladder",
        tmp_path,
        plc_model="FX3U",
        program_name="MAIN",
        revision=23,
    )
    worker.success.connect(lambda task_id, result: succeeded.append((task_id, result)))
    worker.failure.connect(lambda task_id, error: failed.append((task_id, error)))
    worker.progress_updated.connect(
        lambda task_id, payload: progress.append((task_id, payload))
    )

    worker.run()

    assert failed == []
    assert len(succeeded) == 1
    task_id, result = succeeded[0]
    assert task_id == "task-ir"
    assert result["revision"] == 23
    assert result["program_name"] == "MAIN"
    assert set(result["artifacts"]) == {
        "json",
        "ir",
        "svg",
        "program_csv",
        "comment_csv",
        "st_from_ir",
    }
    for name in result["artifacts"].values():
        assert (tmp_path / name).is_file()

    program = json.loads((tmp_path / result["artifacts"]["ir"]).read_text(encoding="utf-8"))
    persisted_ladder = json.loads(
        (tmp_path / result["artifacts"]["json"]).read_text(encoding="utf-8")
    )
    assert validate_plc_ir(program) is program
    assert ir_to_ladder(program) == persisted_ladder == ladder
    assert (tmp_path / result["artifacts"]["svg"]).read_text(encoding="utf-8").lstrip().startswith("<svg")
    assert result["ir_sha256"] == canonical_sha256(program)
    assert result["ladder_sha256"] == canonical_sha256(ladder)
    parsing_messages = [
        payload.get("message", "")
        for task_id, payload in progress
        if task_id == "task-ir" and payload.get("stage") in {"parsing", "parsed"}
    ]
    assert any("读取 JSON 结构" in message for message in parsing_messages)
    assert any("规范化梯形图协议" in message for message in parsing_messages)
    assert any("检查结构与地址" in message for message in parsing_messages)
    assert any("构建 PLC IR" in message for message in parsing_messages)
    assert "模型输出已解析为候选程序" in parsing_messages
    st_text = (tmp_path / result["artifacts"]["st_from_ir"]).read_text(
        encoding="utf-8"
    )
    assert validate_st_traceability(program, st_text)
    assert result["st_from_ir_sha256"] == hashlib.sha256(
        st_text.encode("utf-8")
    ).hexdigest()
    assert result["semantic_schema_version"] == 1
    assert result["semantic_summary"]["state_machine_count"] == 0
    assert program["logic"]["execution_model"] == "plc_scan_cycle"


def test_compiler_does_not_repair_semantic_mismatch_after_confirmed_generation(
    monkeypatch, tmp_path
):
    import application.model_api as api
    import ui.desktop.main_window as main
    from ui.desktop.workers import CompilerThread

    level = {
        "device_comments": {"X0": "按钮", "D0": "计数"},
        "rungs": [
            rung(
                1,
                inputs=[input_element("NO", "X0")],
                outputs=[instruction("INC", ["D0"])],
            )
        ],
    }
    edge = copy.deepcopy(level)
    edge["rungs"][0]["branches"][0]["inputs"][0]["type"] = "P"
    monkeypatch.setattr(
        api,
        "stream_model_response",
        lambda *args, **kwargs: ("", json.dumps(level, ensure_ascii=False)),
    )
    repair_requests = []

    def fake_repair(user_input, *args, **kwargs):
        repair_requests.append(user_input)
        return json.dumps(edge, ensure_ascii=False)

    from ui.desktop import workers
    monkeypatch.setattr(workers, "generate_model_json", fake_repair)
    succeeded = []
    failed = []
    worker = CompilerThread(
        "task-semantic-repair",
        "每次按下 X0 一次，INC D0",
        "high",
        "ladder",
        tmp_path,
        plc_model="FX3U",
        requirement_text="每次按下 X0 一次，INC D0",
    )
    worker.success.connect(lambda task_id, result: succeeded.append((task_id, result)))
    worker.failure.connect(lambda task_id, error: failed.append((task_id, error)))

    worker.run()

    assert failed == []
    assert len(succeeded) == 1
    # The requirement text is model context, not a second local semantic judge.
    # If the model returns LEVEL logic, direct generation publishes that
    # candidate instead of spending another model call to rewrite it.
    assert repair_requests == []
    program = json.loads((tmp_path / "program.ir.json").read_text(encoding="utf-8"))
    assert program["networks"][0]["execution"]["semantics"] == ["LEVEL"]
    assert program["logic"]["requirements"] == []
    assert program["timing"]["coverage"] == []


def test_compiler_partial_edit_uses_ir_revision_and_preserves_other_networks(
    monkeypatch, tmp_path
):
    import application.model_api as api
    from ui.desktop.workers import CompilerThread

    ladder = sample_ladder()
    base_ir = build_plc_ir(ladder, revision=23)
    replacement = copy.deepcopy(ladder["rungs"][0])
    replacement["branches"][0]["inputs"].append(
        input_element("NC", "X3", "急停")
    )
    partial = {
        "mode": "partial",
        "device_comments": {"X3": "急停"},
        "rungs": [replacement],
        "delete_rung_ids": [],
    }
    monkeypatch.setattr(
        api,
        "stream_model_response",
        lambda *args, **kwargs: ("", json.dumps(partial, ensure_ascii=False)),
    )
    succeeded = []
    failed = []
    worker = CompilerThread(
        "task-patch",
        "把 X3 急停加进去",
        "high",
        "ladder",
        tmp_path,
        previous_json=ladder,
        previous_ir=base_ir,
        current_version_json=ladder,
        plc_model="FX3U",
        program_name="MAIN",
        revision=24,
    )
    worker.success.connect(lambda task_id, result: succeeded.append((task_id, result)))
    worker.failure.connect(lambda task_id, error: failed.append((task_id, error)))

    worker.run()

    assert failed == []
    assert len(succeeded) == 1
    program = json.loads((tmp_path / "program.ir.json").read_text(encoding="utf-8"))
    assert program["revision"] == 24
    assert program["networks"][0]["reads"] == ["X0", "X3"]
    assert program["networks"][1]["ladder"] == base_ir["networks"][1]["ladder"]
    assert ir_to_ladder(program)["device_comments"]["X3"] == "急停"


def test_session_store_reads_ir_first_and_persistently_upgrades_legacy(tmp_path):
    from storage.session import SessionStore

    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project(name="IR project", plc_model="FX3U")

    legacy_id, legacy_dir = store.prepare_version(project["id"])
    legacy = sample_ladder()
    store._write_json(legacy_dir / "ladder.json", legacy)
    original_ladder_bytes = (legacy_dir / "ladder.json").read_bytes()
    store.complete_version(
        project["id"],
        legacy_id,
        {
            "target_mode": "ladder",
            "plc_model": "FX3U",
            "artifacts": {"json": "ladder.json"},
        },
    )
    assert store.load_ladder(project["id"], legacy_id) == legacy
    upgraded = store.load_program_ir(project["id"], legacy_id)
    assert upgraded["kind"] == "plc_program_ir"
    assert ir_to_ladder(upgraded) == legacy
    assert (legacy_dir / "program.ir.json").exists()
    assert (legacy_dir / "ladder.json").read_bytes() == original_ladder_bytes
    migrated_version = store.get_version(project["id"], legacy_id)
    assert migrated_version["id"] == legacy_id
    assert migrated_version["revision"] == 1
    assert migrated_version["artifacts"]["ir"] == "program.ir.json"
    assert migrated_version["ir_sha256"] == canonical_sha256(upgraded)
    assert migrated_version["static_analysis_summary"]["rules_checked"]
    assert migrated_version["timing_summary"]["profile"]
    assert store.load_program_ir(project["id"], legacy_id) == upgraded

    current_id, current_dir = store.prepare_version(project["id"])
    current_ir = build_plc_ir(legacy, revision=2)
    store._write_json(current_dir / "ladder.json", {"device_comments": {}, "rungs": []})
    store._write_json(current_dir / "program.ir.json", current_ir)
    store.complete_version(
        project["id"],
        current_id,
        {
            "target_mode": "ladder",
            "plc_model": "FX3U",
            "revision": 2,
            "artifacts": {"json": "ladder.json", "ir": "program.ir.json"},
        },
    )
    assert store.load_ladder(project["id"], current_id) == legacy
    assert store.load_program_ir(project["id"], current_id) == current_ir


def test_session_store_never_overwrites_an_existing_future_ir(tmp_path):
    from storage.session import SessionStore

    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project(name="Future IR", plc_model="FX3U")
    version_id, version_dir = store.prepare_version(project["id"])
    legacy = sample_ladder()
    future_ir = {"kind": "plc_program_ir", "schema_version": 999, "future": True}
    store._write_json(version_dir / "ladder.json", legacy)
    store._write_json(version_dir / "future.ir.json", future_ir)
    original_future_bytes = (version_dir / "future.ir.json").read_bytes()
    store.complete_version(
        project["id"],
        version_id,
        {
            "target_mode": "ladder",
            "plc_model": "FX3U",
            "artifacts": {"json": "ladder.json", "ir": "future.ir.json"},
        },
    )

    compatibility_ir = store.load_program_ir(project["id"], version_id)
    assert ir_to_ladder(compatibility_ir) == legacy
    assert store.load_ladder(project["id"], version_id) == legacy
    assert (version_dir / "future.ir.json").read_bytes() == original_future_bytes
    assert not (version_dir / "program.ir.json").exists()
    assert store.get_version(project["id"], version_id)["artifacts"]["ir"] == "future.ir.json"
