"""Handoff artifacts use verified stored evidence and retain provenance and limits."""
import base64
from copy import deepcopy
import hashlib
import json

import pytest

from application.delivery import delivery_summary
from application.native_validation import NativeValidationService
from application.workbench import WorkbenchService
from tests.test_simulator_verification import version, _execute
from tests.test_web_fbd import service, generate, accept
from tests.test_native_validation import command


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_handoff_keeps_memory_backend_and_execution_binding_without_any_read_migration(version, tmp_path):
    store, pid, vid, _, folder = version
    execution = _execute(version)
    wb = WorkbenchService(store.base_dir, tmp_path / "read-state", read_only=True)
    before = snapshot(store.base_dir)
    result = delivery_summary(wb, pid, vid)
    assert snapshot(store.base_dir) == before
    assert not (tmp_path / "read-state").exists()
    run, = result["simulation_runs"]
    assert run["status"] == "passed"
    assert run["backend_kinds"] == ["test_memory_not_plc_simulator"]
    assert run["binding"]["run_id"] == execution["record"]["run_id"]
    assert run["binding"]["result_sha256"] in result["markdown"]
    assert "test\\_memory\\_not\\_plc\\_simulator" in result["markdown"]
    assert "内存后端的观测范围为软件流程" in result["markdown"]
    assert "需求覆盖按关联测试的实际执行结果逐条核对" in result["markdown"]
    for artifact in result["artifacts"]:
        assert artifact["sha256"] == hashlib.sha256((folder / artifact["filename"]).read_bytes()).hexdigest()


def test_corrupted_trace_is_exported_as_unavailable_not_a_pass(version, tmp_path):
    store, pid, vid, _, folder = version
    execution = _execute(version)
    path = folder / execution["record"]["trace_artifact"]
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["result"]["results"][0]["trace"].clear()
    store._write_json(path, raw)
    wb = WorkbenchService(store.base_dir, tmp_path / "read-state", read_only=True)
    result = delivery_summary(wb, pid, vid)
    assert result["simulation_runs"] == [{"run_id": execution["record"]["run_id"], "status": "evidence_unavailable"}]
    assert "evidence\\_unavailable" in result["markdown"]


@pytest.mark.parametrize('status,label,blocked',[
    ('violated','找到违反',True),('no_violation_found_in_tested_scope','受测范围内未发现违反',False),
    ('unverified','未验证',False)])
def test_delivery_keeps_bounded_behavior_status_and_activation_distinct(version,tmp_path,status,label,blocked):
    store,pid,vid,_,_ = version
    report={'status':status,'coverage':{'traces_tested':8,'maximum_scans':6},
        'assumptions':['declared inputs only'],'violations':[],'activation_blocked':blocked}
    store.update_version_metadata(pid,vid,{'behavior_check':report,'activation_blocked':blocked})
    result=delivery_summary(WorkbenchService(store.base_dir,tmp_path/'read-state',read_only=True),pid,vid)
    assert result['behavior_check'] == report and result['activation_blocked'] is blocked
    assert label in result['markdown'] and '短轨迹执行证据' in result['markdown']


def test_delivery_redacts_private_metadata_and_treats_report_text_as_literal(version, tmp_path):
    store, pid, vid, _, _ = version
    store.update_version_metadata(pid, vid, {
        "summary": "![tracking](https://example.invalid/secret)\n# forged-heading",
        "confirmed_spec_snapshot": {"text": "保留工程说明", "_candidate_ir": {"secret": "hidden-secret"},
                                    "private_path": "C:\\Users\\operator\\private-file.txt"},
        "validation": {"status": "validated", "messages": ["仅静态检查"]},
    })
    wb = WorkbenchService(store.base_dir, tmp_path / "read-state", read_only=True)
    result = delivery_summary(wb, pid, vid)
    serialized = json.dumps(result, ensure_ascii=False)
    assert "hidden-secret" not in serialized and "private-file" not in serialized
    assert result["confirmed_spec"] == {"text": "保留工程说明"}
    assert "![tracking](" not in result["markdown"] and "\n# forged-heading" not in result["markdown"]
    assert "保留工程说明" in result["markdown"] and "仅静态检查" in result["markdown"]


def test_delivery_exposes_actual_bounded_witness_and_unknown_scope(version,tmp_path):
    store,pid,vid,_,_ = version
    report = {'status':'violated','activation_blocked':True,
        'coverage':{'traces_tested':12,'maximum_scans':8},
        'violations':[{'requirement_id':'boot','construct_group':'init_group','scan':0,
            'device':'D100','expected':7,'actual':0,
            'writes':[{'location':{'rung_id':5,'instruction_index':3}}]}],
        'checks':[{'requirement_id':'event','status':'unverified','reasons':['source_edge_history_unverified']}],
        'unsupported_or_missing':[{'reason':'unknown_instruction_write_scope'}],
        'construction_violations':[{'instance_id':'source_event','reason':'missing_reference','position':2}],
        'construction_gaps':[{'id':'other_group','reason':'missing_cpu_control_facts'}]}
    store.update_version_metadata(pid,vid,{'behavior_check':report,'activation_blocked':True})
    result = delivery_summary(WorkbenchService(store.base_dir,tmp_path/'read-state',read_only=True),pid,vid)
    markdown = result['markdown']
    for expected in ('12 条轨迹','8 扫描','D100 预期 7，实际 0','扫描 0',
            'init\\_group','梯级 5，指令 3','要求 event','source\\_edge\\_history\\_unverified',
            'unknown\\_instruction\\_write\\_scope','source\\_event','missing\\_reference','输出位置 2',
            'other\\_group','missing\\_cpu\\_control\\_facts'):
        assert expected in markdown
    assert result['behavior_check'] == report


def test_fbd_handoff_contains_source_and_returned_file_fingerprints_and_operator_origin(service):
    project, _, proposal = generate(service)
    vid = accept(service, proposal)
    native = NativeValidationService(service)
    source = service.projects.artifact(project["id"], vid, "gxw").read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    evidence = native.record(project["id"], vid, command(digest, native_gxw={
        "filename": "native.gxw", "data_base64": base64.b64encode(source).decode()}))
    result = delivery_summary(service, project["id"], vid)
    assert result["native_validation"]["records"] == [evidence]
    assert digest in result["markdown"] and "原生附件" in result["markdown"]
    assert "自动原生验证：未提供通过证明" in result["markdown"] and "操作员记录" in result["markdown"]
    assert "TIMER\\_A" in result["markdown"] and result["fbd_declarations"]["1.Labels.lh"]
    assert result["static_validation"]["gx_compile"] == "not_run"


def test_structural_generation_handoff_keeps_profile_and_parent_changes(version, tmp_path):
    from plc.ir import build_plc_ir, canonical_sha256, ir_to_ladder, validate_plc_ir

    store, pid, base_id, base, _ = version
    ladder = ir_to_ladder(base)
    duplicate = deepcopy(ladder["rungs"][0])
    duplicate["rung_id"] = 2
    ladder["rungs"].append(duplicate)
    program = build_plc_ir(ladder)
    # This is deliberately acceptable to the generation structural path only.
    with pytest.raises(ValueError):
        validate_plc_ir(program, validate_ladder=True)
    vid, folder = store.prepare_version(pid)
    store._write_json(folder / "ladder.json", ladder)
    store._write_json(folder / "program.ir.json", program)
    store.complete_version(pid, vid, {
        "target_mode": "ladder", "plc_model": "FX3U", "parent_version_id": base_id,
        "revision": program["revision"], "ir_sha256": canonical_sha256(program),
        "validation_profile": "generation_structural", "validation": {"status": "validated"},
        "artifacts": {"json": "ladder.json", "ir": "program.ir.json"},
    })
    wb = WorkbenchService(store.base_dir, tmp_path / "read-state", read_only=True)
    before = snapshot(store.base_dir)
    result = delivery_summary(wb, pid, vid)
    assert result["validation_profile"] == "generation_structural"
    assert result["changes"]["added"] == ["N0002"]
    assert "生成结构校验" in result["markdown"]
    assert snapshot(store.base_dir) == before
    assert not (tmp_path / "read-state").exists()


def test_spec_handoff_is_readable_without_dumping_generation_contracts(version, tmp_path):
    store, pid, vid, _, _ = version
    spec = {
        "summary": "启动电机，停止后保持断电。",
        "selected_approach": {"name": "独立启停", "description": "停止输入优先。",
                              "generation_contract": {"required_opcodes": ["OUT"]},
                              "planning_details": "INTERNAL_ONLY_SENTINEL"},
        "io_table": [{"address": "X0", "label": "启动", "kind": "X"},
                     {"address": "Y0", "label": "电机", "kind": "Y"}],
        "parameters": [{"id": "delay", "name": "启动延时", "value": "2 秒"},
                       {"id": "stop_contact", "name": "停止接点", "value": None}],
        "user_notes": "现场复核接线。",
        "hardware_profile": {"internal_configuration": ["MACHINE_ONLY_SENTINEL"] * 100},
    }
    store.update_version_metadata(pid, vid, {"confirmed_spec_snapshot": spec, "confirmed_spec_hash": "a" * 64})
    wb = WorkbenchService(store.base_dir, tmp_path / "read-state", read_only=True)
    result = delivery_summary(wb, pid, vid)
    markdown = result["markdown"]
    assert result["confirmed_spec"] == spec
    assert "a" * 64 in markdown
    for text in ("启动电机", "独立启停", "停止输入优先", "| X0 | 启动 | X |", "| 启动延时 | 2 秒 |",
                 "| 停止接点 | 待确认 |", "现场复核接线", "自动原生验证：未提供通过证明", "交付产物指纹"):
        assert text in markdown
    assert "INTERNAL_ONLY_SENTINEL" not in markdown and "MACHINE_ONLY_SENTINEL" not in markdown
    assert "generation_contract" not in markdown and '"summary"' not in markdown
