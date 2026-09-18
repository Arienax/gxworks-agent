"""Independent review of partial generation, saved reports and explicit strict tools."""
import copy
import json

import pytest

from application.workbench import WorkbenchService
from plc.change_scope import ChangeScopeError
from plc.core import PLCCore
from plc.ir import build_plc_ir, ir_to_ladder
from storage.session import SessionStore


def _rung(identifier, address, output):
    return {"rung_id": identifier, "header_element": None, "shared_inputs": [],
            "debug_note": "Independent network " + str(identifier),
            "branches": [{"branch_id": 1, "y_offset_level": 0,
                          "inputs": [{"type": "NO", "address": address},
                                     {"type": "NO", "address": address}],
                          "outputs": [{"type": "COIL", "address": output}]}]}


@pytest.fixture
def saved_baseline(tmp_path):
    def no_model():
        pytest.fail("Generated candidates must not call the internal provider")

    store = SessionStore(tmp_path / "workspace")
    project = store.create_project("Generation scope audit")
    source = {"device_comments": {"X0": "Start", "X1": "Auxiliary"},
              "rungs": [_rung(1, "X0", "Y0"), _rung(2, "X1", "Y1")]}
    source["rungs"][1]["legacy_annotation"] = {
        "private_path": "C:/private/audit.json", "token": "private-audit-marker",
    }
    program = build_plc_ir(source, plc_model="FX3U", revision=1)
    version_id, output = store.prepare_version(project["id"])
    metadata = store._ir_metadata(program)
    metadata.update(target_mode="ladder", plc_model="FX3U",
                    artifacts=PLCCore().compile_project(program, output)["artifacts"])
    store.complete_version(project["id"], version_id, metadata, activate=True)
    service = WorkbenchService(store.base_dir, tmp_path / "state", model_factory=no_model)
    service.start()
    try:
        yield service, project["id"], version_id, source
    finally:
        service.close()


def _workspace_files(store):
    return {str(path.relative_to(store.base_dir)): path.read_bytes()
            for path in store.base_dir.rglob("*") if path.is_file()}


def test_partial_generation_keeps_untouched_source_and_persists_public_normalization(saved_baseline):
    service, project_id, base_id, original = saved_baseline
    changed = _rung(1, "X2", "Y0")
    # Unknown rung metadata remains source data; the normalization receipt must
    # never include a copied rung body or serialize this opaque private marker.
    command = {"project_id": project_id, "version_id": base_id, "name": "create_program_candidate",
               "call_id": "partial-audit", "change_scope": {"network_ids": ["N0001"]},
               "arguments": {"ladder": {"mode": "partial", "rungs": [changed]}}}
    before = _workspace_files(service.store)
    response = service.agent_call(command)
    assert response["is_error"] is False
    assert _workspace_files(service.store) == before
    proposal = service.proposals.get(response["proposal_id"])
    assert proposal["status"] == "pending"
    normalization = response["data"]["data"]["normalization"]
    assert normalization["changes"]
    assert proposal["summary"]["normalization"] == normalization
    assert set(normalization) == {"changes", "skipped"}
    for records in normalization.values():
        for record in records:
            assert set(record) == {"message", "network_ids"}
    assert "private-audit-marker" not in json.dumps(normalization)
    assert "C:/private" not in json.dumps(normalization)
    accepted = service.proposals.accept(proposal["id"])
    saved_id = accepted["result"]["version_id"]
    version = service.store.get_version(project_id, saved_id)
    assert version["validation_profile"] == "generation_structural"
    assert version["normalization"] == normalization
    reloaded = SessionStore(service.store.base_dir, create=False).load_program_ir(project_id, saved_id, persist_legacy=False)
    source = ir_to_ladder(reloaded)
    assert source["rungs"][1] == original["rungs"][1]
    assert len(source["rungs"][0]["branches"][0]["inputs"]) == 1
    assert ir_to_ladder(service.store.load_program_ir(project_id, base_id, persist_legacy=False)) == original
    after_save = _workspace_files(service.store)
    assert service.version_preview(project_id, saved_id)["read_only"] is True
    assert _workspace_files(service.store) == after_save


def test_partial_scope_escape_cannot_create_a_proposal_or_change_saved_program(saved_baseline):
    service, project_id, base_id, original = saved_baseline
    before = _workspace_files(service.store)
    command = {"project_id": project_id, "version_id": base_id, "name": "create_program_candidate",
               "call_id": "outside-audit", "change_scope": {"network_ids": ["N0001"]},
               "arguments": {"ladder": {"mode": "partial", "rungs": [_rung(2, "X2", "Y1")]}}}
    with pytest.raises(ChangeScopeError, match="N0002"):
        service.agent_call(command)
    assert service.proposals.list(project_id) == []
    assert _workspace_files(service.store) == before
    assert command["arguments"]["ladder"]["rungs"][0]["branches"][0]["inputs"] == [
        {"type": "NO", "address": "X2"}, {"type": "NO", "address": "X2"}]


def test_generated_structural_profile_does_not_relax_explicit_patch_or_strict_compile():
    source = {"device_comments": {}, "rungs": [_rung(1, "X0", "Y0"), _rung(2, "X1", "Y0")]}
    untouched = copy.deepcopy(source)
    core = PLCCore()
    generated = core.create_program_candidate(source, plc_model="FX3U")
    assert generated["validation_profile"] == "generation_structural"
    core.compile_project(generated["candidate_ir"], validation_profile=generated["validation_profile"])
    with pytest.raises(ValueError):
        core.compile_project(generated["candidate_ir"])
    # Explicit patch validation retains its historical semantic checks even
    # when its selected base was accepted as a generated structural candidate.
    with pytest.raises(ValueError):
        core.patch_program(generated["candidate_ir"], {"operations": [{
            "operation": "modify_network", "network": "N0001",
            "ladder": _rung(1, "X2", "Y0"),
        }]})
    assert source == untouched
