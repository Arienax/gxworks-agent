import copy
import json

import pytest

from model_runtime.provider import ToolCall
from agent_runtime.plc_tools import build_default_tool_registry, build_tool_context
from plc.core import PLCCore, accept_candidate_patch
from plc.ir import build_plc_ir, canonical_sha256, ir_to_ladder, validate_plc_ir
from storage.session import SessionStore
from agent_runtime.runtime import InProcessToolRuntime


def _rung(rung_id, input_address, output_address, note):
    return {
        "rung_id": rung_id,
        "debug_note": note,
        "header_element": None,
        "shared_inputs": [],
        "branches": [
            {
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [
                    {"type": "NO", "address": input_address, "label": ""}
                ],
                "outputs": [
                    {"type": "COIL", "address": output_address, "label": ""}
                ],
            }
        ],
    }


def _program(two_networks=False):
    rungs = [_rung(1, "X0", "Y0", "启动输出")]
    comments = {"X0": "启动", "Y0": "运行"}
    if two_networks:
        rungs.append(_rung(2, "X1", "Y1", "辅助输出"))
        comments.update({"X1": "辅助输入", "Y1": "辅助输出"})
    return build_plc_ir(
        {"device_comments": comments, "rungs": rungs},
        plc_model="FX3U",
        program_name="MAIN",
        revision=1,
    )


def _modify_patch(program):
    replacement = copy.deepcopy(program["networks"][0]["ladder"])
    replacement["branches"][0]["inputs"].append(
        {"type": "NC", "address": "X2", "label": ""}
    )
    return {
        "base_revision": program["revision"],
        "base_ir_sha256": canonical_sha256(program),
        "target_revision": program["revision"] + 1,
        "operations": [
            {
                "operation": "modify_network",
                "network": "N0001",
                "ladder": replacement,
            }
        ],
        "device_comments": {"X2": "停止"},
    }


def _persist_base(store, project_id, program):
    core = PLCCore()
    version_id, output_dir = store.prepare_version(project_id)
    compiled = core.compile_project(program, output_dir)
    metadata = store._ir_metadata(program)
    metadata.update(
        {
            "target_mode": "ladder",
            "plc_model": "FX3U",
            "program_name": "MAIN",
            "artifacts": dict(compiled["artifacts"]),
            "confirmed_spec_snapshot": None,
            "confirmed_spec_hash": None,
        }
    )
    return store.complete_version(project_id, version_id, metadata)


def test_plc_core_reads_validates_and_compiles_through_existing_pipeline(tmp_path):
    program = _program()
    output_dir = tmp_path / "compiled"
    core = PLCCore()

    network = core.read_network(program, "N0001")
    diagnostics = core.get_diagnostics(program)
    validation = core.validate_project(program)
    compiled = core.compile_project(program, output_dir)

    assert network["writes"] == ["Y0"]
    assert diagnostics["counts"]["error"] == 0
    assert validation["valid"] is True
    assert set(compiled["artifacts"]) == {
        "json",
        "ir",
        "svg",
        "st_from_ir",
        "program_csv",
        "comment_csv",
    }
    assert all((output_dir / name).is_file() for name in compiled["artifacts"].values())
    assert len(compiled["hashes"]) == 6


def test_network_patch_supports_add_modify_and_delete_with_structured_diff():
    core = PLCCore()
    base = _program(two_networks=True)
    replacement = copy.deepcopy(base["networks"][0]["ladder"])
    replacement["branches"][0]["inputs"].append(
        {"type": "NC", "address": "X2", "label": ""}
    )
    patch = {
        "base_revision": 1,
        "base_ir_sha256": canonical_sha256(base),
        "target_revision": 2,
        "operations": [
            {
                "operation": "modify_network",
                "network": "N0001",
                "ladder": replacement,
            },
            {"operation": "delete_network", "network": "N0002"},
            {
                "operation": "add_network",
                "network": "N0003",
                "after": "N0001",
                "ladder": _rung(3, "X3", "M0", "新增状态"),
            },
        ],
        "device_comments": {"X2": "停止", "X3": "条件", "M0": "状态"},
    }

    candidate = core.patch_program(base, patch)

    assert candidate["target_revision"] == 2
    assert candidate["diff"]["added"] == ["N0003"]
    assert candidate["diff"]["deleted"] == ["N0002"]
    assert candidate["diff"]["modified"] == ["N0001"]
    assert candidate["diff"]["device_comments_changed"] is True
    assert [item["marker"] for item in candidate["diff"]["changes"]] == [
        "+",
        "-",
        "~",
    ]
    assert candidate["diagnostics"]["valid"] is True


def test_tool_runtime_hides_candidate_ir_from_model_but_keeps_it_for_ui():
    program = _program()
    version = {
        "id": "v0001",
        "target_mode": "ladder",
        "plc_model": "FX3U",
        "program_name": "MAIN",
        "revision": 1,
        "confirmed_spec_snapshot": None,
    }
    context = build_tool_context(
        {
            "id": "project1",
            "name": "测试项目",
            "plc_model": "FX3U",
            "active_version_id": "v0001",
            "versions": [version],
        },
        version=version,
        program_ir=program,
    )
    runtime = InProcessToolRuntime(build_default_tool_registry())

    result = runtime.invoke(
        ToolCall("call_patch", "patch_program", {"patch": _modify_patch(program)}),
        context,
    )

    public = json.loads(result.content)
    assert result.is_error is False
    assert public["status"] == "confirmation_required"
    assert "_candidate_ir" not in result.content
    assert "_confirmed_spec" not in result.content
    pending = result.data["data"]["pending_action"]
    assert pending["_candidate_ir"]["revision"] == 2


def test_invalid_candidate_never_creates_a_version(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project("候选测试", plc_model="FX3U")
    base = _program()
    version = _persist_base(store, project["id"], base)
    context = build_tool_context(
        store.get_project(project["id"]),
        version=version,
        program_ir=base,
    )
    invalid = _modify_patch(base)
    invalid["operations"][0]["ladder"]["rung_id"] = 99

    result = build_default_tool_registry().call(
        "patch_program", {"patch": invalid}, context
    )

    assert result["ok"] is False
    assert len(store.get_project(project["id"])["versions"]) == 1
    assert sorted(path.name for path in store.version_dir(project["id"], "v0001").parent.iterdir()) == [
        "v0001"
    ]


def test_candidate_cancel_changes_neither_current_version_nor_gx_state(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project("候选测试", plc_model="FX3U")
    base = _program()
    version = _persist_base(store, project["id"], base)
    original_project = copy.deepcopy(store.get_project(project["id"]))
    original_ir = copy.deepcopy(store.load_program_ir(project["id"], version["id"]))
    context = build_tool_context(original_project, version=version, program_ir=base)

    result = build_default_tool_registry().call(
        "patch_program", {"patch": _modify_patch(base)}, context
    )
    assert result["status"] == "confirmation_required"
    # Cancellation is deliberately represented by not invoking the UI-only accept action.

    assert store.get_project(project["id"])["active_version_id"] == version["id"]
    assert len(store.get_project(project["id"])["versions"]) == 1
    assert store.load_program_ir(project["id"], version["id"]) == original_ir
    assert not (store.project_dir(project["id"]) / "GX Works2 Backups").exists()


def test_accept_candidate_creates_only_one_local_child_version(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project("候选测试", plc_model="FX3U")
    base = _program()
    version = _persist_base(store, project["id"], base)
    base_ir_path = store.version_dir(project["id"], version["id"]) / "program.ir.json"
    base_bytes = base_ir_path.read_bytes()
    context = build_tool_context(
        store.get_project(project["id"]), version=version, program_ir=base
    )
    tool_result = build_default_tool_registry().call(
        "patch_program", {"patch": _modify_patch(base)}, context
    )
    action = tool_result["data"]["pending_action"]

    accepted = accept_candidate_patch(store, action)

    current = store.get_project(project["id"])
    assert accepted["id"] == "v0002"
    assert accepted["parent_version_id"] == "v0001"
    assert accepted["source_candidate_id"] == action["candidate_id"]
    assert accepted["lifecycle_status"] == "accepted"
    assert current["active_version_id"] == "v0002"
    assert len(current["versions"]) == 2
    assert base_ir_path.read_bytes() == base_bytes
    assert store.load_program_ir(project["id"], "v0002")["revision"] == 2
    assert all(
        (store.version_dir(project["id"], "v0002") / name).is_file()
        for name in accepted["artifacts"].values()
    )
    assert not (store.project_dir(project["id"]) / "GX Works2 Backups").exists()


def test_tampered_candidate_is_rejected_without_creating_a_version(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project("候选测试", plc_model="FX3U")
    base = _program()
    version = _persist_base(store, project["id"], base)
    context = build_tool_context(
        store.get_project(project["id"]), version=version, program_ir=base
    )
    result = build_default_tool_registry().call(
        "patch_program", {"patch": _modify_patch(base)}, context
    )
    action = copy.deepcopy(result["data"]["pending_action"])
    action["_candidate_ir"]["revision"] = 100

    with pytest.raises(ValueError):
        accept_candidate_patch(store, action)

    assert len(store.get_project(project["id"])["versions"]) == 1


@pytest.mark.parametrize("plc_model", ["FX3U", "FX5U"])
def test_create_program_candidate_builds_valid_initial_ir(plc_model):
    ladder = ir_to_ladder(_program())
    ladder["device_comments"] = {" x0 ": "启动", "y0": "运行"}
    original = copy.deepcopy(ladder)
    spec = {"io_table": [{"address": "Y0", "kind": "Y", "label": "电机"}]}
    core = PLCCore()

    candidate = core.create_program_candidate(ladder, plc_model=plc_model, confirmed_spec=spec)
    program = candidate["candidate_ir"]

    assert candidate["revision"] == program["revision"] == 1
    assert program["plc"]["cpu"] == plc_model
    assert program["program_name"] == "MAIN"
    assert validate_plc_ir(program, confirmed_spec=spec) == program
    assert ir_to_ladder(program) == {
        **ladder, "device_comments": {"X0": "启动", "Y0": "运行"},
    }
    assert candidate["candidate_ir_sha256"] == canonical_sha256(program)
    assert candidate["ladder_sha256"] == canonical_sha256(ir_to_ladder(program))
    assert program["io_map"]["Y0"]["label"] == "电机"
    assert program["networks"][0]["reads"] == ["X0"]
    assert program["networks"][0]["writes"] == ["Y0"]
    assert candidate["diagnostics"]["valid"] is True
    assert candidate["summary"] == {
        "network_count": 1, "network_ids": ["N0001"], "instruction_count": 2,
        "device_count": 2, "devices_by_kind": {"X": 1, "Y": 1},
    }
    repeated = core.create_program_candidate(ladder, plc_model=plc_model, confirmed_spec=spec)
    assert candidate["candidate_id"] != repeated["candidate_id"]
    assert candidate["candidate_ir_sha256"] == repeated["candidate_ir_sha256"]
    assert ladder == original


@pytest.mark.parametrize("ladder", [None, [], "text", {}, {"device_comments": {}, "rungs": []}])
def test_create_program_candidate_rejects_invalid_ladder(ladder):
    with pytest.raises((TypeError, ValueError)):
        PLCCore().create_program_candidate(ladder, plc_model="FX3U")


@pytest.mark.parametrize("opcode,operands,error", [
    ("UNVERIFIED", [], "unsupported APP_INSTR"),
    ("turn on motor", [], "invalid APP_INSTR opcode"),
    ("RD3A", ["D0"], "exactly 3 operands"),
    ("DRVTBL", [], "not supported by FX3U"),
])
def test_create_program_candidate_requires_real_catalogued_application_instructions(opcode, operands, error):
    ladder = ir_to_ladder(_program())
    ladder["rungs"][0]["branches"][0]["outputs"] = [
        {"type": "APP_INSTR", "opcode": opcode, "operands": operands},
    ]
    with pytest.raises(ValueError, match=error):
        PLCCore().create_program_candidate(ladder, plc_model="FX3U")


@pytest.mark.parametrize("kind", ["derived_top", "block_output", "nested_parallel", "long_label"])
def test_generation_cannot_smuggle_derived_fields_or_import_only_syntax(kind):
    ladder = ir_to_ladder(_program())
    rung = ladder["rungs"][0]
    branch = rung["branches"][0]
    if kind == "derived_top":
        ladder["analysis"] = {"counts": {"error": 0}}
    elif kind == "derived_rung":
        rung["instructions"] = [{"op": "OUT", "args": ["Y7"]}]
    elif kind == "block_output":
        branch["outputs"] = [{"type": "BLOCK_OUTPUT", "expression": "UNVERIFIED D0"}]
    elif kind == "nested_parallel":
        branch["inputs"] = [{"type": "parallel_block", "branches": [[
            {"type": "parallel_block", "branches": [[{"type": "NO", "address": "X0"}]]},
        ]]}]
    else:
        branch["outputs"][0]["label"] = "长" * 65
    with pytest.raises(ValueError):
        PLCCore().create_program_candidate(ladder, plc_model="FX3U")


def test_create_program_candidate_defers_confirmed_approach_conflict_to_review():
    spec = {"selected_approach": {
        "name": "必须自锁", "generation_contract": {"required_structures": ["self_hold"]},
    }}
    core = PLCCore()
    candidate = core.create_program_candidate(ir_to_ladder(_program()), plc_model="FX3U", confirmed_spec=spec)
    assert candidate["validation_profile"] == "generation_structural"
    assert not core.validate_project(candidate["candidate_ir"], spec)["valid"]


def test_create_program_candidate_defers_hardware_heuristics_to_review():
    ladder = ir_to_ladder(_program())
    ladder["rungs"][0]["branches"][0]["outputs"] = [{
        "type": "APP_INSTR", "opcode": "PLSY", "operands": ["K100", "K1000", "Y0"],
    }]
    spec = {"hardware_profile": {"plc_family": "FX3U", "output_type": "relay"}}
    core = PLCCore()
    candidate = core.create_program_candidate(ladder, plc_model="FX3U", confirmed_spec=spec)
    assert candidate["diagnostics"]["valid"]
    assert not core.validate_project(candidate["candidate_ir"], spec)["valid"]


def test_create_program_candidate_retains_nonblocking_static_diagnostics(monkeypatch):
    core = PLCCore()

    def diagnostics(program):
        validate_plc_ir(program)
        return {"counts": {"error": 1}, "findings": [{"code": "STATIC_ERROR", "message": "test diagnostic"}]}

    monkeypatch.setattr(core, "get_diagnostics", diagnostics)
    candidate = core.create_program_candidate(ir_to_ladder(_program()), plc_model="FX3U")
    assert candidate["diagnostics"]["valid"] is True
    assert candidate["diagnostics"]["findings"][0]["code"] == "STATIC_ERROR"


@pytest.mark.parametrize("existing_version", [False, True])
@pytest.mark.parametrize("compile_failure", [False, True])
def test_generated_candidate_uses_only_temporary_artifacts_and_never_persists(tmp_path, monkeypatch, existing_version, compile_failure):
    import plc.generation as plc_generation

    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project("新程序候选", plc_model="FX3U")
    version = _persist_base(store, project["id"], _program()) if existing_version else None
    project = store.get_project(project["id"])
    before = {path.relative_to(store.base_dir): path.read_bytes() for path in store.base_dir.rglob("*") if path.is_file()}
    renderer = plc_generation.render_generation_artifacts
    directories = []

    def render(program, directory):
        directories.append(directory)
        assert not directory.is_relative_to(store.base_dir)
        if compile_failure:
            (directory / "partial.txt").write_text("partial")
            raise ValueError("test compile failure")
        rendered = renderer(program, directory)
        assert len(rendered["artifacts"]) == 6
        assert all((directory / name).exists() for name in rendered["artifacts"].values())
        return rendered

    def forbid_persistence(*args, **kwargs):
        pytest.fail("Candidate creation must not persist a version")

    monkeypatch.setattr(plc_generation, "render_generation_artifacts", render)
    monkeypatch.setattr(SessionStore, "prepare_version", forbid_persistence)
    monkeypatch.setattr(SessionStore, "complete_version", forbid_persistence)
    result = build_default_tool_registry().call(
        "create_program_candidate", {"ladder": ir_to_ladder(_program()), "program_name": "MOTOR"},
        build_tool_context(project, version=version),
    )

    if compile_failure:
        assert result["ok"] is False
        assert "status" not in result
        assert "test compile failure" in result["error"]["message"]
    else:
        assert result["status"] == "confirmation_required"
        assert result["data"]["verification"]["behavior_verified"] is False
        assert result["data"]["verification"]["native_verified"] is False
        pending = result["data"]["pending_action"]
        assert pending["type"] == "accept_generated_program"
        assert pending["_candidate_ir"]["program_name"] == "MOTOR"
        assert pending["_candidate_ir"]["revision"] == pending["revision"] == 1
        assert len(pending["artifact_hashes"]) == 6
    assert len(directories) == 1
    assert all(not directory.exists() for directory in directories)
    assert store.get_project(project["id"]) == project
    assert store.get_project(project["id"])["active_version_id"] == (version["id"] if version else None)
    assert {path.relative_to(store.base_dir): path.read_bytes() for path in store.base_dir.rglob("*") if path.is_file()} == before


def test_generation_rejects_non_ladder_target_before_core_invocation(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("The core must not be called for an ST project")

    monkeypatch.setattr(PLCCore, "create_program_candidate", forbidden)
    result = build_default_tool_registry().call(
        "create_program_candidate", {"ladder": ir_to_ladder(_program())},
        build_tool_context({"id": "st-project", "target_mode": "st"}),
    )
    assert result["ok"] is False
    assert "ladder" in result["error"]["message"]
